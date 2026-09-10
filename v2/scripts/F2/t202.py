#!/usr/bin/env python
"""Inspecciona T-202 (F2) — decisión binaria "¿es comprobante?" del gate qween.

Para cada archivo/carpeta pasada, prepara la **vista rápida** (T-201),
**reduce la resolución/peso de la imagen** (preprocesamiento de la decisión
barata E-QWE-1: lado mayor 512 px, JPEG q80, piso de lado menor 256 px — ver
constantes ``LADO_MAXIMO_VISTA_PX``/``CALIDAD_JPEG_VISTA``), arma el **prompt
corto** de 3 salidas (``prompt_qween.py``) y llama a
``decidir_es_comprobante`` (T-202) contra el **Ollama local** para decidir si
el documento es comprobante. Muestra por archivo:

  - ``tipo_entrada`` detectado por F1.
  - vista usada (``rapida``), calidad (``baja``) y si decide sobre imagen o
    sobre markdown (texto nativo).
  - el **veredicto** del gate (comprobante | no_comprobante | indeterminado),
    la ``confianza_fuente`` (alta/media/baja) y la fuente de evidencia
    (``vlm`` si la vista es imagen, ``llm`` si es texto).
  - con ``--detalle``: la nota del gate, el modelo usado, la versión de prompt
    (``qween-gate@2``) y la evidencia (``SourceEvidence`` serializada).

Al final imprime un **reporte del procesamiento**: distribución de veredictos
(con %), ahorro de la reducción de imagen (peso original vs. reducido, total y
factor), tiempos y errores por archivo.

Sin dependencias nuevas:
  - **Imágenes**: NO corre Docling (vista directa; igual que ``t201.py``).
  - **PDF / office / texto**: corre ``procesar_documento`` (F1) para obtener el
    ``ProcessedDocument`` real (el routing decide si el PDF es escaneado →
    imagen, o apto → markdown).
  - El gate **sí** llama a Ollama real (a diferencia de la suite default, que
    usa dobles): esto es una herramienta de uso manual, como los scripts F1 que
    corren Docling real. Requiere Ollama local (default ``http://localhost:11434``,
    modelo VLM ``qwen2.5vl:3b`` — ver ``Settings.modelos.vlm``).
  - La reducción de imagen usa **Pillow** (disponible en ``py313_env``); si no
    está, se envía la imagen original (aviso). ``--no-reducir`` desactiva la
    reducción.

Uso:
    python scripts/F2/t202.py <archivo|carpeta>...
    python scripts/F2/t202.py tests/fixtures/golden/2991f57d-*.jpg
    python scripts/F2/t202.py tests/fixtures/golden --detalle
    python scripts/F2/t202.py mi.pdf --modelo qwen2.5vl:3b --detalle
    python scripts/F2/t202.py img.jpg --no-reducir   # envía la imagen original

Ejemplos:
    python scripts/F2/t202.py tests/fixtures/golden/2991f57d-c143-4b23-9f87-4dfb1214ef53.jpg --detalle
    python scripts/F2/t202.py tests/fixtures/pdf_escaneados/*.pdf
    python scripts/F2/t202.py ../files/2025-08 --detalle
"""

from __future__ import annotations

import argparse
import sys
import tempfile
from dataclasses import replace
from pathlib import Path

# El paquete usa layout src/; si se corre sin instalar, se agrega src/ al path.
_SRC = Path(__file__).resolve().parents[2] / "src"
if str(_SRC) not in sys.path:
    sys.path.insert(0, str(_SRC))

from voucherflow.models.docling import ProcessedDocument  # noqa: E402
from voucherflow.models.ollama import OllamaClient  # noqa: E402
from voucherflow.processing.type_detector import EXTENSIONES_IMAGEN  # noqa: E402
from voucherflow.settings.config import cargar_settings  # noqa: E402
from voucherflow.validation import (  # noqa: E402
    RESOLUCION_VISTA_RAPIDA_PX,
    VERSION_PROMPT_QWEEN,
    construir_messages_gate,
    decidir_es_comprobante,
    preparar_vista_rapida,
)

#: Extensiones de imagen: la vista se deriva sin correr Docling.
_IMAGEN = EXTENSIONES_IMAGEN

#: Lado mayor objetivo de la vista rápida (px) — misma constante que T-201
#: (``RESOLUCION_VISTA_RAPIDA_PX``). Bajar a este lado mayor controla el peso
#: del payload al VLM (decisión barata E-QWE-1).
LADO_MAXIMO_VISTA_PX = RESOLUCION_VISTA_RAPIDA_PX

#: Piso del lado **menor** de la imagen reducida (px). Si bajar el lado mayor a
#: ``LADO_MAXIMO_VISTA_PX`` dejara el lado menor por debajo de este valor, se
#: reduce menos (se garantiza un mínimo de detalle para que el VLM aún pueda
#: decidir "¿es comprobante?").
LADO_MENOR_MINIMO_PX = 256

#: Calidad JPEG del thumbnail (0-100). Piso recomendado para no degradar la
#: legibilidad del texto/QR/sellos: valores < 75 pueden volver ilegible detalle
#: fino. 80 es un buen balance peso/legibilidad para la decisión barata.
CALIDAD_JPEG_VISTA = 80

#: Peso máximo orientativo del payload tras reducir (bytes). Informativo: con
#: lado mayor 512px y JPEG q80 un comprobante suele quedar muy por debajo; se
#: muestra en ``--detalle`` para verificar el ahorro (E-QWE-1).
PESO_MAX_ORIENTATIVO_BYTES = 200_000


def _tipo_por_extension(archivo: Path) -> str:
    """Estima el ``tipo_entrada`` de F1 por extensión (solo informativo)."""
    ext = archivo.suffix.lower()
    if ext in _IMAGEN:
        return "imagen"
    if ext == ".pdf":
        return "pdf_texto"  # solo informativo; al convertir el routing refina
    if ext in {".docx", ".pptx", ".xlsx"}:
        return "office"
    if ext in {".txt", ".md", ".html", ".csv", ".log"}:
        return "texto"
    return "no_soportado"


def _expandir(args: list[str]) -> list[Path]:
    """Expande archivos/carpetas de la CLI a una lista de archivos."""
    rutas: list[Path] = []
    for raw in args:
        p = Path(raw)
        if p.is_dir():
            rutas.extend(q for q in sorted(p.rglob("*")) if q.is_file())
        elif p.is_file():
            rutas.append(p)
        else:
            print(f"⚠  Se ignora (no existe): {p}", file=sys.stderr)
    return sorted(set(rutas))


def _obtener_documento(archivo: Path) -> "ProcessedDocument":
    """Obtiene el ``ProcessedDocument`` de F1 sobre el cual preparar la vista.

    - Imagen: ``ProcessedDocument`` mínimo sin correr Docling (la vista de
      T-201 solo necesita tipo + ruta; el VLM decide sobre la imagen original).
    - PDF / office / texto: ``procesar_documento`` (F1/T-105/ORQ, Docling real
      o pdftotext --layout) para el documento real. Import diferido para no
      cargar orquestación cuando solo se inspeccionan imágenes.
    """
    if archivo.suffix.lower() in _IMAGEN:
        return ProcessedDocument(
            tipo_entrada="imagen", ruta=str(archivo), markdown=""
        )
    from voucherflow.processing.orquestacion import procesar_documento  # noqa: E402

    return procesar_documento(archivo)


def _reducir_imagen_para_vista(ruta_imagen: str) -> tuple[str | None, dict]:
    """Reduce la resolución/peso de una imagen para la decisión barata (E-QWE-1).

    Objetivo: bajar el **lado mayor** a :data:`LADO_MAXIMO_VISTA_PX` (512 px,
    misma constante que T-201) manteniendo aspecto, y guardar como JPEG de
    calidad :data:`CALIDAD_JPEG_VISTA` — controla el peso del payload al VLM
    sin perder la capacidad de decidir \"¿es comprobante?\".

    Pisos (\"resolución/peso mínimo adecuado\"):
      - **Lado menor mínimo**: si reducir a 512 px dejaría el lado menor por
        debajo de :data:`LADO_MENOR_MINIMO_PX` (256 px), se reduce solo hasta
        ese piso (detalle suficiente para no perder texto/QR/sellos).
      - **No se agranda**: si la imagen original ya es más chica que el
        objetivo, se usa tal cual (sin upscale).
      - **Peso**: con lado mayor 512 px y JPEG q80 el resultado queda muy por
        debajo de :data:`PESO_MAX_ORIENTATIVO_BYTES` (se informa en ``--detalle``).

    Usa Pillow (disponible en el entorno ``py313_env``); si no está, devuelve
    la imagen original sin reducir (comportamiento previo) con una nota.

    Devuelve ``(ruta_reducida, info)``:
      - ``ruta_reducida``: ruta del thumbnail temporal (JPEG) o ``None`` si no
        se redujo (sin Pillow o la imagen no se pudo abrir).
      - ``info``: dict con dimensiones originales/finales, reducción aplicada y
        pesos, para trazabilidad.
    """
    try:
        from PIL import Image, ImageOps
    except ImportError:
        return None, {"nota": "Pillow no disponible; se usa la imagen original."}

    try:
        img = Image.open(ruta_imagen)
        img = ImageOps.exif_transpose(img)  # respeta orientación EXIF
        ancho_orig, alto_orig = img.size
        img = img.convert("RGB")
    except Exception as exc:
        return None, {
            "nota": f"No se pudo abrir la imagen para reducir: {exc}; se usa la original."
        }

    lado_mayor_orig = max(ancho_orig, alto_orig)
    lado_menor_orig = min(ancho_orig, alto_orig)
    orig_bytes = Path(ruta_imagen).stat().st_size

    # Escala para llevar el lado mayor al objetivo, pero sin que el lado menor
    # baje del piso (no agrandar si la original ya es menor que el objetivo).
    escala = min(1.0, LADO_MAXIMO_VISTA_PX / lado_mayor_orig)
    nuevo_mayor = max(1, round(lado_mayor_orig * escala))
    nuevo_menor = max(1, round(lado_menor_orig * escala))
    if nuevo_menor < LADO_MENOR_MINIMO_PX and lado_menor_orig > LADO_MENOR_MINIMO_PX:
        # Piso: reducir menos para conservar detalle del lado menor.
        escala = LADO_MENOR_MINIMO_PX / lado_menor_orig
        nuevo_mayor = max(1, round(lado_mayor_orig * escala))
        nuevo_menor = LADO_MENOR_MINIMO_PX

    if escala >= 1.0:
        # No se agranda; la imagen ya es chica (o igual) — no hace falta reducir.
        return None, {
            "nota": f"Imagen {ancho_orig}x{alto_orig}px ya es <= objetivo "
                    f"{LADO_MAXIMO_VISTA_PX}px; sin reducir.",
            "ancho_orig": ancho_orig, "alto_orig": alto_orig,
            "peso_orig_bytes": orig_bytes,
        }

    # Remuestreo LANCZOS (buena calidad al bajar) y guardado JPEG q80.
    nuevo = (nuevo_mayor if ancho_orig >= alto_orig else nuevo_menor,
             nuevo_menor if ancho_orig >= alto_orig else nuevo_mayor)
    img = img.resize(nuevo, Image.Resampling.LANCZOS)

    tmp = tempfile.NamedTemporaryFile(suffix=".jpg", delete=False)
    tmp.close()
    img.save(tmp.name, "JPEG", quality=CALIDAD_JPEG_VISTA, optimize=True)
    peso_reducido = Path(tmp.name).stat().st_size

    return tmp.name, {
        "nota": (f"Reducida {ancho_orig}x{alto_orig}px ({orig_bytes} B) -> "
                 f"{nuevo[0]}x{nuevo[1]}px JPEG q{CALIDAD_JPEG_VISTA} "
                 f"({peso_reducido} B)"),
        "ancho_orig": ancho_orig, "alto_orig": alto_orig,
        "ancho_final": nuevo[0], "alto_final": nuevo[1],
        "peso_orig_bytes": orig_bytes, "peso_final_bytes": peso_reducido,
        "reduccion_x": round(orig_bytes / max(1, peso_reducido), 1),
        "bajo_peso_max": peso_reducido <= PESO_MAX_ORIENTATIVO_BYTES,
    }


def _mostrar_messages(messages: list[dict]) -> None:
    """Imprime (para debug) la forma del mensaje enviado al modelo.

    No vuelca el contenido de la imagen (base64, muy largo): muestra el
    **tamaño** del payload de ``images`` para verificar el ahorro de la
    decisión barata (E-QWE-1).
    """
    for m in messages:
        if m.get("images"):
            n = len(m["images"])
            chars = sum(len(i) for i in m["images"])
            print(f"      [{m['role']}] content={m['content'][:60]!r} "
                  f"images={n} (~{chars // 1024} KB base64)")
        else:
            print(f"      [{m['role']}] content={m['content'][:80]!r}{'…' if len(m['content']) > 80 else ''}")


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Inspecciona la decisión binaria '¿es comprobante?' de T-202 (F2 / gate qween E-QWE-1)."
    )
    parser.add_argument("rutas", nargs="+", help="Archivos o carpetas.")
    parser.add_argument(
        "--modelo",
        help="Modelo a usar (default: Settings.modelos.vlm, p. ej. qwen2.5vl:3b).",
    )
    parser.add_argument(
        "--url",
        help="URL del servidor Ollama (default: Settings.ollama.url).",
    )
    parser.add_argument(
        "--timeout",
        type=float,
        default=120.0,
        help="Timeout por llamada en segundos (default: 120).",
    )
    parser.add_argument(
        "--no-reducir",
        action="store_true",
        help="No reducir la resolución/peso de la imagen (envía la original). "
        "Default: reduce a lado mayor 512px / JPEG q80 con piso de lado menor "
        "256px (vista barata E-QWE-1).",
    )
    parser.add_argument(
        "--detalle",
        action="store_true",
        help="Además del resumen, imprime nota, modelo, versión de prompt, "
        "evidencia, reducción aplicada y los messages enviados.",
    )
    args = parser.parse_args()

    archivos = _expandir(args.rutas)
    if not archivos:
        print("No se encontraron archivos.", file=sys.stderr)
        sys.exit(2)

    settings = cargar_settings()
    url = args.url or settings.url_ollama
    modelo = args.modelo or settings.modelo_vlm
    if not modelo:
        print("No hay modelo VLM configurado en Settings.modelos.vlm. "
              "Pasá --modelo.", file=sys.stderr)
        sys.exit(2)

    cliente = OllamaClient(url=url, timeout_s=args.timeout)
    print(f"Archivos a decidir: {len(archivos)}  [Ollama={url} modelo={modelo} "
          f"prompt={VERSION_PROMPT_QWEEN}]\n")

    cabecera = ("archivo", "tipo", "vista", "calidad", "veredicto", "confianza", "fuente")
    fmt = "{:<34} {:<12} {:<7} {:<8} {:<16} {:<9} {:<6}"
    print(fmt.format(*cabecera))
    print(fmt.format(*("─" * 34, "─" * 12, "─" * 7, "─" * 8, "─" * 16, "─" * 9, "─" * 6)))

    import time as _time

    # Acumuladores para el reporte final.
    conteo_veredictos: dict[str, int] = {}
    errores: list[tuple[str, str]] = []
    t0_total = _time.time()
    n_reducidas = 0
    peso_orig_total = 0
    peso_final_total = 0
    sin_reducir = 0  # imágenes que no se redujeron (ya chicas / sin Pillow)
    latencias: list[float] = []

    for archivo in archivos:
        ruta_reducida = None  # limpiar por archivo
        t0_archivo = _time.time()
        try:
            doc = _obtener_documento(archivo)
            tipo = doc.tipo_entrada or _tipo_por_extension(archivo)
            vista = preparar_vista_rapida(doc, origen=archivo)

            # Preprocesamiento: bajar resolución/peso de la imagen para la
            # decisión barata (E-QWE-1) antes de llamar al VLM. Solo aplica a
            # vistas de imagen (texto nativo no tiene píxeles que reducir).
            info_reduccion = None
            if (vista.ruta_imagen_original and not args.no_reducir):
                ruta_reducida, info_reduccion = _reducir_imagen_para_vista(
                    vista.ruta_imagen_original
                )
                if ruta_reducida:
                    # La vista decide sobre el thumbnail (misma calidad baja).
                    vista = replace(
                        vista,
                        representacion=ruta_reducida,
                        ruta_imagen_original=ruta_reducida,
                        factor_escala=round(
                            max(1, Path(vista.origen).stat().st_size
                                / max(1, Path(ruta_reducida).stat().st_size)), 4
                        ),
                        nota=(vista.nota + f" | {info_reduccion['nota']}"),
                    )
                    n_reducidas += 1
                    peso_orig_total += info_reduccion.get("peso_orig_bytes", 0)
                    peso_final_total += info_reduccion.get("peso_final_bytes", 0)
                else:
                    sin_reducir += 1
                    print(f"    ℹ  {archivo.name}: {info_reduccion['nota']}")

            # T-202: decisión de una pasada sobre la vista (Ollama real).
            resultado = decidir_es_comprobante(
                vista, cliente, modelo=modelo, settings=settings
            )
            veredicto = resultado.veredicto.value
            ev = resultado.detalle.get("evidencia", {})
            fuente = (ev.get("fuente") or "-")[:6]

            print(fmt.format(
                archivo.name[:34],
                (tipo or "-")[:12],
                resultado.vista_usada[:7],
                vista.calidad[:8],
                veredicto[:16],
                resultado.confianza_fuente[:9],
                fuente,
            ))

            if args.detalle:
                print(f"    decide_sobre: "
                      f"{'imagen ' + vista.ruta_imagen_original if vista.ruta_imagen_original else 'markdown (texto nativo)'}")
                if info_reduccion and ruta_reducida:
                    print(f"    reducción: {info_reduccion['nota']}  "
                          f"(x{info_reduccion.get('reduccion_x', 1)} peso, "
                          f"bajo_peso_max={info_reduccion.get('bajo_peso_max')})")
                print(f"    nota: {resultado.detalle.get('nota', '')}")
                print(f"    modelo: {resultado.detalle.get('modelo')}  "
                      f"version_prompt: {resultado.detalle.get('version_prompt')}")
                frag = ev.get("campos", {}).get("es_comprobante", {}).get("fragmento_sustento", "")
                print(f"    fragmento_sustento: {frag[:200]}{'…' if len(frag) > 200 else ''}")
                _mostrar_messages(construir_messages_gate(vista))

            # Acumular métricas del archivo.
            conteo_veredictos[veredicto] = conteo_veredictos.get(veredicto, 0) + 1
            latencias.append(_time.time() - t0_archivo)
        except Exception as exc:
            errores.append((archivo.name, f"{type(exc).__name__}: {exc}"))
            print(f"❌ {archivo.name}: error {type(exc).__name__}: {exc}")
        finally:
            # Limpiar el thumbnail temporal creado para este archivo.
            if ruta_reducida:
                try:
                    Path(ruta_reducida).unlink(missing_ok=True)
                except OSError:
                    pass

    # ------------------------------------------------------------------
    # Reporte final del procesamiento
    # ------------------------------------------------------------------
    t_total = _time.time() - t0_total
    n_ok = len(archivos) - len(errores)

    print("\n" + "═" * 62)
    print("REPORTE DE PROCESAMIENTO")
    print("═" * 62)

    # 1. Veredictos del gate.
    print(f"\nVeredictos del gate ({n_ok} procesados, {len(errores)} con error):")
    for v in ("comprobante", "no_comprobante", "indeterminado"):
        n = conteo_veredictos.get(v, 0)
        pct = (100.0 * n / n_ok) if n_ok else 0.0
        print(f"  • {v:<16} {n:>4}  ({pct:5.1f}%)")
    if errores:
        print(f"  • {'error':<16} {len(errores):>4}")

    # 2. Ahorro por reducción (solo si se redujo alguna imagen).
    if not args.no_reducir and n_reducidas:
        ahorro_b = max(0, peso_orig_total - peso_final_total)
        ahorro_x = (peso_orig_total / max(1, peso_final_total))
        print(f"\nReducción de imagen (decisión barata E-QWE-1, "
              f"lado≤{LADO_MAXIMO_VISTA_PX}px q{CALIDAD_JPEG_VISTA}):")
        print(f"  • imágenes reducidas : {n_reducidas}  "
              f"(sin reducir: {sin_reducir})")
        print(f"  • peso original total: {peso_orig_total/1024:9.1f} KB")
        print(f"  • peso final total   : {peso_final_total/1024:9.1f} KB")
        print(f"  • ahorro             : {ahorro_b/1024:9.1f} KB "
              f"(x{ahorro_x:.1f} menos)")
    elif args.no_reducir:
        print("\nReducción de imagen: desactivada (--no-reducir).")

    # 3. Tiempos.
    print(f"\nTiempo total: {t_total:.1f}s  "
          f"({t_total/max(1, n_ok):.2f}s por archivo procesado).")

    # 4. Errores (si los hay).
    if errores:
        print("\nErrores:")
        for nombre, detalle in errores:
            print(f"  ❌ {nombre}: {detalle}")

    # 5. Nota de ahorro de costo (semántica del gate qween).
    n_no_comp = conteo_veredictos.get("no_comprobante", 0)
    if n_no_comp:
        print(f"\nℹ  {n_no_comp} documento(s) 'no_comprobante' no deberían llegar "
              f"a extracción (ahorro de costo E-QWE — validar en T-204).")
    print("═" * 62)


if __name__ == "__main__":
    main()

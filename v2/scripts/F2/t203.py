#!/usr/bin/env python
"""Inspecciona T-203 (F2) — orquestación del doble paso qween y vista fiel.

Para cada archivo/carpeta pasada, corre la **orquestación completa del doble
paso** (`validation.validar_y_procesar`, T-203) contra el **Ollama local**:

  1. Procesa el documento con F1 (``procesar_documento``) o usa un
     ``ProcessedDocument`` mínimo para imágenes (vista directa, sin Docling).
  2. 1ª pasada con la **vista rápida** (T-201/T-202, calidad baja):
     ``comprobante`` / ``no_comprobante`` / ``indeterminado``.
  3. Si es ``indeterminado`` → 2ª pasada con la **vista de revisión**
     (T-203, calidad media) — y si sigue indeterminado se rechaza (política
     E-QWE-1, conservando la trazabilidad de ambas pasadas).
  4. Si el veredicto final es ``comprobante`` → prepara la **vista fiel**
     (T-203, calidad alta, NO reutiliza la rápida: E-QWE-2) que alimenta F4.

Muestra por archivo:
  - ``tipo_entrada`` y el **veredicto final** (comprobante / no_comprobante).
  - **pasadas**: cuántas (1 = solo rápida; 2 = rápida + revisión) y con qué
    vista decidió cada una.
  - **vista fiel**: tipo/calidad/resolución objetivo cuando se preparó (o ``-``
    si el documento se rechazó: no llega a extracción → ahorro E-QWE).
  - con ``--detalle``: veredicto/confianza de cada pasada, la traza
    ``resolucion_doble_paso`` y la **reducción de imagen** aplicada en cada
    vista (la librería la hace sola: ``imagen_envio_base64`` alinea al
    preprocesador de Qwen2.5-VL con el lado mayor de cada tipo de vista).

Al final imprime un **reporte del procesamiento**: distribución de veredictos
finales (con %), cuántos necesitaron 2ª pasada, cuántos prepararon vista fiel,
tiempos y errores por archivo.

Sin dependencias nuevas:
  - **Imágenes**: NO corre Docling (vista directa, igual que ``t201.py``).
  - **PDF / office / texto**: ``procesar_documento`` (F1) para el documento
    real (el routing decide si el PDF es escaneado → imagen, o apto → markdown).
  - El gate **sí** llama a Ollama real (la suite default usa dobles): es una
    herramienta de uso manual. Requiere Ollama local (default
    ``http://localhost:11434``, modelo VLM ``qwen2.5vl:3b``).
  - La reducción de imagen la hace la **librería** (``validation``); este script
    solo la reporta.

Uso:
    python scripts/F2/t203.py <archivo|carpeta>...
    python scripts/F2/t203.py tests/fixtures/golden --detalle
    python scripts/F2/t203.py tests/fixtures/golden/2991f57d-*.jpg
    python scripts/F2/t203.py mi.pdf --modelo qwen2.5vl:3b --detalle

Ejemplos:
    python scripts/F2/t203.py tests/fixtures/golden/2991f57d-c143-4b23-9f87-4dfb1214ef53.jpg --detalle
    python scripts/F2/t203.py tests/fixtures/pdf_escaneados/*.pdf
    python scripts/F2/t203.py ../files/2025-08 --detalle
"""

from __future__ import annotations

import argparse
import sys
import time
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
    VERSION_PROMPT_QWEEN,
    imagen_envio_base64,
    preparar_vista_rapida,
    preparar_vista_revision,
    validar_y_procesar,
)

#: Extensiones de imagen: la vista se deriva sin correr Docling.
_IMAGEN = EXTENSIONES_IMAGEN


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
    """Obtiene el ``ProcessedDocument`` de F1 sobre el cual orquestar.

    - Imagen: ``ProcessedDocument`` mínimo sin correr Docling (la orquestación
      prepara las vistas y el VLM decide; no hace falta el OCR de F1).
    - PDF / office / texto: ``procesar_documento`` (F1/T-105/ORQ, Docling real
      o pdftotext --layout) para el documento real. Import diferido para no
      cargar la orquestación cuando solo se inspeccionan imágenes.
    """
    if archivo.suffix.lower() in _IMAGEN:
        return ProcessedDocument(
            tipo_entrada="imagen", ruta=str(archivo), markdown=""
        )
    from voucherflow.processing.orquestacion import procesar_documento  # noqa: E402

    return procesar_documento(archivo)


def _info_reduccion_vista(vista) -> str:
    """Resumen legible de la reducción de imagen aplicada a una vista.

    La reducción la hace la librería (``imagen_envio_base64``, T-202/T-203):
    alinea al preprocesador de Qwen2.5-VL con el lado mayor de la vista. Para
    vistas textuales no aplica ("texto"). Devuelve una línea corta.
    """
    if not vista.ruta_imagen_original:
        return "texto (sin imagen)"
    try:
        _b64, info = imagen_envio_base64(vista)
    except Exception as exc:  # pragma: no cover - informativo
        return f"(no se pudo reducir: {type(exc).__name__})"
    if info.get("reducida"):
        return (
            f"{info['ancho_original']}x{info['alto_original']} -> "
            f"{info['ancho_envio']}x{info['alto_envio']}px "
            f"({info['peso_original_bytes']//1024}->{info['peso_envio_bytes']//1024} KB)"
        )
    return f"sin reducir ({info.get('motivo', '')})"


def _payload_imagen_kb(vista) -> int:
    """KB del payload base64 que se enviaría al VLM para la vista (0 si texto)."""
    if not vista.ruta_imagen_original:
        return 0
    try:
        b64, _ = imagen_envio_base64(vista)
        return len(b64) // 1024
    except Exception:  # pragma: no cover - informativo
        return 0


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Inspecciona la orquestación del doble paso qween (F2 / T-203, E-QWE-1/E-QWE-2)."
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
        "--detalle",
        action="store_true",
        help="Además del resumen, imprime cada pasada, la traza del doble paso, "
        "la vista fiel y la reducción de imagen por vista.",
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
    print(f"Archivos a validar: {len(archivos)}  [Ollama={url} modelo={modelo} "
          f"prompt={VERSION_PROMPT_QWEEN}]\n")

    cabecera = ("archivo", "tipo", "pasadas", "vistas_usadas", "veredicto_final", "vista_fiel")
    fmt = "{:<34} {:<12} {:<7} {:<18} {:<16} {:<10}"
    print(fmt.format(*cabecera))
    print(fmt.format(*("─" * 34, "─" * 12, "─" * 7, "─" * 18, "─" * 16, "─" * 10)))

    # Acumuladores para el reporte final.
    conteo_final: dict[str, int] = {}
    n_dos_pasadas = 0
    n_vista_fiel = 0
    errores: list[tuple[str, str]] = []
    t0_total = time.time()

    for archivo in archivos:
        t0_archivo = time.time()
        try:
            doc = _obtener_documento(archivo)
            tipo = doc.tipo_entrada or _tipo_por_extension(archivo)

            # T-203: orquestación completa del doble paso sobre el documento.
            rv = validar_y_procesar(
                doc, cliente, modelo=modelo, settings=settings
            )
            veredicto_final = rv.veredicto_final.value
            vistas_usadas = ",".join(p.vista_usada for p in rv.pasadas) or "-"
            n_pasadas = len(rv.pasadas)
            fiel = rv.vista_fiel.tipo_vista if rv.vista_fiel else "-"

            print(fmt.format(
                archivo.name[:34],
                (tipo or "-")[:12],
                str(n_pasadas),
                vistas_usadas[:18],
                veredicto_final[:16],
                fiel[:10],
            ))

            if args.detalle:
                for i, p in enumerate(rv.pasadas, start=1):
                    print(f"    pasada {i}: veredicto={p.veredicto.value:<15} "
                          f"vista={p.vista_usada:<8} confianza={p.confianza_fuente}")
                traza = rv.resultado.detalle.get("resolucion_doble_paso")
                if traza:
                    print(f"    resolución doble paso: {traza}")
                if rv.vista_fiel:
                    vf = rv.vista_fiel
                    print(f"    vista fiel: tipo={vf.tipo_vista} calidad={vf.calidad} "
                          f"nivel={vf.nivel_vista} resolucion_objetivo={vf.resolucion_objetivo}px "
                          f"factor={vf.factor_escala}")
                    print(f"    vista fiel NO reutiliza la rápida: "
                          f"reutiliza={vf.metadatos.get('reutiliza_vista_rapida')}")
                else:
                    print("    vista fiel: NO preparada (documento rechazado → "
                          "no llega a extracción, ahorro E-QWE)")
                # Reducción de imagen por vista (la hace la librería al enviar).
                # Las vistas son deterministas sobre el mismo documento, así que
                # se reconstruyen solo para reportar su reducción al VLM.
                v_rapida = preparar_vista_rapida(doc, origen=archivo)
                print(f"    reducción 1ª pasada (rapida): {_info_reduccion_vista(v_rapida)}")
                print(f"    payload al VLM: 1ª={_payload_imagen_kb(v_rapida)} KB")
                if len(rv.pasadas) > 1:
                    v_rev = preparar_vista_revision(doc, origen=archivo)
                    print(f"    reducción 2ª pasada (revision): {_info_reduccion_vista(v_rev)}")
                    print(f"    payload al VLM: 2ª={_payload_imagen_kb(v_rev)} KB")

            # Métricas.
            conteo_final[veredicto_final] = conteo_final.get(veredicto_final, 0) + 1
            if n_pasadas > 1:
                n_dos_pasadas += 1
            if rv.vista_fiel:
                n_vista_fiel += 1
        except Exception as exc:
            errores.append((archivo.name, f"{type(exc).__name__}: {exc}"))
            print(f"❌ {archivo.name}: error {type(exc).__name__}: {exc}")

    # ------------------------------------------------------------------
    # Reporte final del procesamiento
    # ------------------------------------------------------------------
    t_total = time.time() - t0_total
    n_ok = len(archivos) - len(errores)

    print("\n" + "═" * 62)
    print("REPORTE DE PROCESAMIENTO (doble paso qween — T-203)")
    print("═" * 62)

    # 1. Veredictos finales.
    print(f"\nVeredictos finales ({n_ok} procesados, {len(errores)} con error):")
    for v in ("comprobante", "no_comprobante"):
        n = conteo_final.get(v, 0)
        pct = (100.0 * n / n_ok) if n_ok else 0.0
        print(f"  • {v:<16} {n:>4}  ({pct:5.1f}%)")

    # 2. Doble paso (E-QWE-1).
    print("\nDoble paso (E-QWE-1):")
    print(f"  • 1 sola pasada (rápida)      : {n_ok - n_dos_pasadas:>4}")
    print(f"  • 2 pasadas (rápida+revisión) : {n_dos_pasadas:>4}")

    # 3. Vista fiel (E-QWE-2).
    print("\nVista fiel para extracción (E-QWE-2):")
    print(f"  • preparada (comprobante)     : {n_vista_fiel:>4}")
    print(f"  • no preparada (rechazado)    : {n_ok - n_vista_fiel:>4}")

    # 4. Tiempos.
    print(f"\nTiempo total: {t_total:.1f}s  "
          f"({t_total/max(1, n_ok):.2f}s por archivo procesado).")

    # 5. Errores.
    if errores:
        print("\nErrores:")
        for nombre, detalle in errores:
            print(f"  ❌ {nombre}: {detalle}")

    # 6. Nota de ahorro (los no_comprobante no llegan a extracción).
    n_no_comp = conteo_final.get("no_comprobante", 0)
    if n_no_comp:
        print(f"\nℹ  {n_no_comp} documento(s) rechazado(s): no llegan a extracción "
              f"(ahorro de costo E-QWE — validar en T-204).")
    print("═" * 62)


if __name__ == "__main__":
    main()

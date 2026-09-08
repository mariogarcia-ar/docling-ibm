#!/usr/bin/env python
"""Inspecciona T-202 (F2) — decisión binaria "¿es comprobante?" del gate qween.

Para cada archivo/carpeta pasada, prepara la **vista rápida** (T-201), arma el
**prompt corto** de 3 salidas (``prompt_qween.py``) y llama a
``decidir_es_comprobante`` (T-202) contra el **Ollama local** para decidir si
el documento es comprobante. Muestra por archivo:

  - ``tipo_entrada`` detectado por F1.
  - vista usada (``rapida``), calidad (``baja``) y si decide sobre imagen o
    sobre markdown (texto nativo).
  - el **veredicto** del gate (comprobante | no_comprobante | indeterminado),
    la ``confianza_fuente`` (alta/media/baja) y la fuente de evidencia
    (``vlm`` si la vista es imagen, ``llm`` si es texto).
  - con ``--detalle``: la nota del gate, el modelo usado, la versión de prompt
    (``qween-gate@1``) y la evidencia (``SourceEvidence`` serializada).

Sin dependencias nuevas:
  - **Imágenes**: NO corre Docling (vista directa; igual que ``t201.py``).
  - **PDF / office / texto**: corre ``procesar_documento`` (F1) para obtener el
    ``ProcessedDocument`` real (el routing decide si el PDF es escaneado →
    imagen, o apto → markdown).
  - El gate **sí** llama a Ollama real (a diferencia de la suite default, que
    usa dobles): esto es una herramienta de uso manual, como los scripts F1 que
    corren Docling real. Requiere Ollama local (default ``http://localhost:11434``,
    modelo VLM ``qwen2.5vl:3b`` — ver ``Settings.modelos.vlm``).

Uso:
    python scripts/F2/t202.py <archivo|carpeta>...
    python scripts/F2/t202.py tests/fixtures/golden/2991f57d-*.jpg
    python scripts/F2/t202.py tests/fixtures/golden --detalle
    python scripts/F2/t202.py mi.pdf --modelo qwen2.5vl:3b --detalle

Ejemplos:
    python scripts/F2/t202.py tests/fixtures/golden/2991f57d-c143-4b23-9f87-4dfb1214ef53.jpg --detalle
    python scripts/F2/t202.py tests/fixtures/pdf_escaneados/*.pdf
    python scripts/F2/t202.py ../files/2025-08 --detalle
"""

from __future__ import annotations

import argparse
import sys
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
    construir_messages_gate,
    decidir_es_comprobante,
    preparar_vista_rapida,
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


def _mostrar_messages(messages: list[dict]) -> None:
    """Imprime (para debug) la forma del mensaje enviado al modelo."""
    for m in messages:
        if m.get("images"):
            print(f"      [{m['role']}] content={m['content'][:60]!r} images={m['images']}")
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
        "--detalle",
        action="store_true",
        help="Además del resumen, imprime nota, modelo, versión de prompt, "
        "evidencia y los messages enviados.",
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

    n_no_comprobante = 0
    n_indeterminado = 0
    for archivo in archivos:
        try:
            doc = _obtener_documento(archivo)
            tipo = doc.tipo_entrada or _tipo_por_extension(archivo)
            vista = preparar_vista_rapida(doc, origen=archivo)

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
                print(f"    nota: {resultado.detalle.get('nota', '')}")
                print(f"    modelo: {resultado.detalle.get('modelo')}  "
                      f"version_prompt: {resultado.detalle.get('version_prompt')}")
                frag = ev.get("campos", {}).get("es_comprobante", {}).get("fragmento_sustento", "")
                print(f"    fragmento_sustento: {frag[:200]}{'…' if len(frag) > 200 else ''}")
                _mostrar_messages(construir_messages_gate(vista))

            if veredicto == "no_comprobante":
                n_no_comprobante += 1
            elif veredicto == "indeterminado":
                n_indeterminado += 1
        except Exception as exc:
            print(f"❌ {archivo.name}: error {type(exc).__name__}: {exc}")

    print(f"\nℹ  Resumen: {n_no_comprobante} no_comprobante(s), "
          f"{n_indeterminado} indeterminado(s) — el resto, comprobante(s).")


if __name__ == "__main__":
    main()

#!/usr/bin/env python
"""Inspecciona T-201 (F2) — preparación de la vista rápida del doble paso qween.

Para cada archivo/carpeta pasada, deriva la **vista rápida** (thumbnail /
calidad baja) de la Fase F2 (gate "¿es comprobante?", E-QWE-1) usando
``voucherflow.validation.preparar_vista_rapida`` y muestra:

  - ``tipo_entrada`` detectado por F1 (imagen/pdf_texto/pdf_escaneado/office/
    texto) — vía ``procesar_documento`` cuando se convierte, o por extensión
    cuando no.
  - tipo de vista (``rapida``), calidad (``baja``), nivel (1) y, si aplica,
    resolución objetivo / factor de escala anotados (stub anotativo: sin
    remuestrear píxeles, sin Pillow/OpenCV — F2-subplan §3.1).
  - si la vista decide sobre **imagen** (``ruta_imagen_original``, insumo de
    T-202 para el VLM) o sobre **markdown** de F1 (texto nativo: no aplica
    thumbnail, F2-subplan §2.4).

Sin dependencias nuevas:
  - **Imágenes** (jpg/png/jpeg/tiff/bmp): NO corre Docling. Se construye un
    ``ProcessedDocument`` mínimo (tipo ``imagen`` + ruta + markdown vacío) y la
    vista rápida anota la resolución objetivo sobre la imagen original
    (``leer_caracteristicas``, stdlib). Rápido.
  - **PDF / office / texto**: corre ``procesar_documento`` (F1/T-105/ORQ —
    Docling real o pdftotext --layout) para obtener el ``ProcessedDocument``
    real: el routing decide si el PDF es **escaneado** (→ imagen renderizada,
    la vista decide sobre imagen) o **apto/texto nativo** (→ markdown, la
    vista decide sobre texto; F2-subplan §2.4). Puede ser lento (Docling).

Uso:
    python scripts/F2/t201.py <archivo|carpeta>...
    python scripts/F2/t201.py tests/fixtures/golden/2991f57d-*.jpg
    python scripts/F2/t201.py tests/fixtures/golden
    python scripts/F2/t201.py ../files/2025-08
    python scripts/F2/t201.py mi.pdf otro.png docs/ --detalle

Ejemplos:
    python scripts/F2/t201.py tests/fixtures/golden/2991f57d-c143-4b23-9f87-4dfb1214ef53.jpg
    python scripts/F2/t201.py tests/fixtures/pdf_escaneados/*.pdf
    python scripts/F2/t201.py tests/fixtures/golden --detalle
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
from voucherflow.processing.type_detector import EXTENSIONES_IMAGEN  # noqa: E402
from voucherflow.validation import preparar_vista_rapida  # noqa: E402

#: Extensiones de imagen: la vista rápida se deriva sin correr Docling.
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

    - Imagen: ``ProcessedDocument`` mínimo (tipo ``imagen`` + ruta + markdown
      vacío) **sin correr Docling**: la vista rápida de T-201 solo necesita
      tipo_entrada y ruta (anota resolución sobre la imagen original; el
      markdown no se usa para la decisión de imagen). Rápido y sin deps.
    - PDF / office / texto: ``procesar_documento`` (F1/T-105/ORQ, Docling real
      o pdftotext --layout) para obtener el documento real: el routing decide
      si un PDF es escaneado (imagen) o apto (texto nativo), y office/texto
      producen markdown. Lento (Docling), como en los scripts de F1.
      Import diferido: evita cargar el módulo de orquestación (y sus imports
      transitivos) cuando solo se inspeccionan imágenes.
    """
    if archivo.suffix.lower() in _IMAGEN:
        return ProcessedDocument(
            tipo_entrada="imagen", ruta=str(archivo), markdown=""
        )
    from voucherflow.processing.orquestacion import procesar_documento  # noqa: E402

    return procesar_documento(archivo)


def _resumen(vista) -> dict:
    """Devuelve un dict resumen de la vista preparada para imprimir."""
    es_imagen = vista.ruta_imagen_original is not None
    return {
        "tipo_vista": vista.tipo_vista,
        "calidad": vista.calidad,
        "nivel": vista.nivel_vista,
        "resolucion_objetivo": vista.resolucion_objetivo,
        "factor_escala": vista.factor_escala,
        "usa_imagen": es_imagen,
        "representacion": (
            vista.ruta_imagen_original
            if es_imagen
            else f"markdown ({len(vista.representacion)} chars)"
        ),
    }


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Inspecciona la vista rápida de T-201 (F2 / gate qween E-QWE-1)."
    )
    parser.add_argument("rutas", nargs="+", help="Archivos o carpetas.")
    parser.add_argument(
        "--detalle",
        action="store_true",
        help="Además del resumen, imprime la nota de trazabilidad completa.",
    )
    args = parser.parse_args()

    archivos = _expandir(args.rutas)
    if not archivos:
        print("No se encontraron archivos.", file=sys.stderr)
        sys.exit(2)

    print(f"Archivos a inspeccionar: {len(archivos)}\n")

    cabecera = ("archivo", "tipo", "vista", "calidad", "nivel", "res_objetivo", "factor", "decide_sobre")
    fmt = "{:<34} {:<12} {:<7} {:<8} {:>5} {:>11} {:>7}  {:<22}"
    print(fmt.format(*cabecera))
    print(fmt.format(*("─" * 34, "─" * 12, "─" * 7, "─" * 8, "─" * 5, "─" * 11, "─" * 7, "─" * 22)))

    n_directas = 0
    for archivo in archivos:
        try:
            doc = _obtener_documento(archivo)
            if archivo.suffix.lower() in _IMAGEN:
                n_directas += 1
            tipo = doc.tipo_entrada or _tipo_por_extension(archivo)

            vista = preparar_vista_rapida(doc, origen=archivo)
            r = _resumen(vista)
            decide = (
                f"imagen:{Path(r['representacion']).name}"
                if r["usa_imagen"]
                else "markdown (texto nativo)"
            )
            print(fmt.format(
                archivo.name[:34],
                (tipo or "-")[:12],
                r["tipo_vista"][:7],
                r["calidad"][:8],
                r["nivel"],
                f"{r['resolucion_objetivo']}px",
                f"{r['factor_escala']:.2f}",
                decide[:22],
            ))
            if args.detalle:
                print(f"    nota: {vista.nota}")
                print(f"    metadatos: {vista.metadatos}")
        except Exception as exc:
            print(f"❌ {archivo.name}: error {type(exc).__name__}: {exc}")

    if n_directas:
        print(f"\nℹ  {n_directas} imagen(es) procesada(s) sin Docling (vista directa); "
              f"los PDF/office/texto usan el pipeline F1 (procesar_documento).")


if __name__ == "__main__":
    main()

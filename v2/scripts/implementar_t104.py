#!/usr/bin/env python
"""Implementa/prueba T-104: procesa un archivo con la cadena F1 y guarda el .md.

Aplica a un archivo real (imagen/pdf/office/txt) la cadena de F1 para validar
el exportador por posición de T-104:

  1. T-101 ``detectar()``: tipo de entrada (imagen/pdf_texto/pdf_escaneado/...).
  2. Conversión con Docling (F0/T-006, OCR full-page para imágenes).
  3. T-103 ``detectar_orientacion()``: orientación dominante por boxes.
  4. T-104 ``exportar_documento()``: Markdown final (tablas conservadas del
     crudo de Docling + texto ordenado por posición).
  5. Guarda el resultado en un ``.md`` (junto al archivo o en --outdir).

Para PDFs escaneados el flujo correcto es render→imagen→OCR (ver PROC.md §5);
este script usa el veredicto de ``routing`` para decidir y renderiza la página
a JPG antes de pasar por Docling cuando corresponde.

Uso:
    python scripts/implementar_t104.py <archivo> [--outdir DIR] [--print]

Ejemplos:
    python scripts/implementar_t104.py tests/fixtures/golden/2991f57d-*.jpg
    python scripts/implementar_t104.py tests/fixtures/pdf_escaneados/*.pdf --print
    python scripts/implementar_t104.py tests/fixtures/pdf_aptos_layout/*.pdf --outdir /tmp/md

Nota: convierte con Docling real (descarga modelos la 1ra vez, lento). Para la
suite esto es ``@pytest.mark.integration``; acá es una herramienta de uso manual.
"""

from __future__ import annotations

import argparse
import sys
import tempfile
from pathlib import Path

# El paquete usa layout src/; si se corre sin instalar, se agrega src/ al path.
_SRC = Path(__file__).resolve().parents[1] / "src"
if str(_SRC) not in sys.path:
    sys.path.insert(0, str(_SRC))

from voucherflow.models.docling import DoclingConverter  # noqa: E402
from voucherflow.processing.markdown_exporter import (  # noqa: E402
    exportar_documento,
    exportar_por_posicion,
)
from voucherflow.processing.orientation import detectar_orientacion  # noqa: E402
from voucherflow.processing.routing import VeredictoPdf, analizar_pdf  # noqa: E402
from voucherflow.processing.type_detector import detectar  # noqa: E402


def _render_pdf_a_jpg(pdf: Path, dpi: int = 300) -> Path:
    """Renderiza la primera página de un PDF escaneado a JPG (RGB, sin alfa)."""
    import fitz  # PyMuPDF

    doc = fitz.open(str(pdf))
    try:
        pagina = doc[0]
        mat = fitz.Matrix(dpi / 72, dpi / 72)
        pix = pagina.get_pixmap(matrix=mat, colorspace=fitz.csRGB)
    finally:
        doc.close()

    tmp = Path(tempfile.mkdtemp(prefix="vf_render_")) / "pagina.jpg"
    pix.save(str(tmp), jpg_quality=95)
    return tmp


def implementar_t104(archivo: Path) -> tuple[str, str, str]:
    """Procesa un archivo con la cadena F1; devuelve (tipo, orientacion, markdown)."""
    converter = DoclingConverter()
    tipo = detectar(archivo).tipo

    # Ruta para PDF escaneado: render a imagen -> Docling OCR.
    if tipo == "pdf_escaneado":
        analisis = analizar_pdf(archivo)
        if analisis.veredicto == VeredictoPdf.requiere_ocr or analisis.requiere_ocr_en_alguna:
            img = _render_pdf_a_jpg(archivo)
            try:
                doc = converter.convert(img)
            finally:
                img.unlink(missing_ok=True)
        else:
            doc = converter.convert(archivo)
    else:
        # pdf_texto / imagen / office / texto: Docling directo.
        doc = converter.convert(archivo)

    orientacion = detectar_orientacion(doc.boxes)
    # Política combinada: conserva tablas del markdown crudo de Docling y
    # ordena por posición el texto cuando no hay tabla (ver markdown_exporter).
    markdown = exportar_documento(doc)
    return tipo, orientacion, markdown


#: Extensiones que se procesan con Docling.
_EXTENSIONES_PROCESABLES = frozenset(
    {".pdf", ".jpg", ".jpeg", ".png", ".tif", ".tiff", ".bmp", ".docx", ".xlsx", ".pptx", ".txt", ".md", ".html"}
)


def _expandir_rutas(args: list[str]) -> list[Path]:
    """Expande archivos/carpetas de la CLI a una lista de archivos a procesar."""
    rutas: list[Path] = []
    for raw in args:
        p = Path(raw)
        if p.is_dir():
            rutas.extend(
                q for q in sorted(p.rglob("*"))
                if q.is_file() and q.suffix.lower() in _EXTENSIONES_PROCESABLES
            )
        elif p.is_file() and p.suffix.lower() in _EXTENSIONES_PROCESABLES:
            rutas.append(p)
        else:
            print(f"⚠  Se ignora (no procesable o no existe): {p}", file=sys.stderr)
    return sorted(set(rutas))


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Procesa archivos con la cadena F1 (T-104) y guarda el .md ordenado."
    )
    parser.add_argument("archivos", nargs="+", help="Archivos o carpetas a procesar (imagen/pdf/office/txt).")
    parser.add_argument("--outdir", help="Carpeta de salida para los .md (default: junto a cada archivo).")
    parser.add_argument("--print", action="store_true", help="Además de guardar, imprime el markdown.")
    args = parser.parse_args()

    archivos = _expandir_rutas(args.archivos)
    if not archivos:
        print("No se encontraron archivos procesables.", file=sys.stderr)
        sys.exit(2)

    print(f"Archivos a procesar: {len(archivos)}\n")
    for archivo in archivos:
        try:
            tipo, orientacion, markdown = implementar_t104(archivo)
        except Exception as exc:
            print(f"❌ {archivo.name}: error {type(exc).__name__}: {exc}")
            continue

        print(f"  {archivo.name[:30]:32} tipo={tipo:<14} orient={orientacion:<10} chars={len(markdown)}")

        # Guardar .md
        if args.outdir:
            outdir = Path(args.outdir)
            outdir.mkdir(parents=True, exist_ok=True)
            salida = outdir / f"{archivo.stem}.md"
        else:
            salida = archivo.with_suffix(".md")
        salida.write_text(markdown, encoding="utf-8")
        print(f"    guardado: {salida}")

        if args.print:
            print("\n" + "=" * 60)
            print(markdown)
            print("=" * 60)


if __name__ == "__main__":
    main()

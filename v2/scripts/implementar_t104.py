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
import shutil
import subprocess
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
    """Renderiza un PDF escaneado a JPG (RGB, sin alfa) para OCR.

    Estrategia: si la página tiene imagen(es), renderiza el **área de la imagen
    más grande** (clip) con zoom — evita el caso de un ticket/recibo chico
    centrado en una hoja A4 escaneada, que a página completa queda diminuto y
    Docling no lo lee (ej. fixture 3ac5a2ec). Si no hay imágenes, renderiza la
    página completa.
    """
    import fitz  # PyMuPDF

    doc = fitz.open(str(pdf))
    try:
        pagina = doc[0]
        infos = pagina.get_image_info()
        if infos:
            # Tomar la imagen de mayor área.
            mayor = max(infos, key=lambda i: (i["bbox"][2] - i["bbox"][0]) * (i["bbox"][3] - i["bbox"][1]))
            bbox = fitz.Rect(mayor["bbox"])
            # Zoom para que el lado mayor de la imagen quede ~2000 px (bueno para OCR).
            lado_px = max(bbox.width, bbox.height)
            zoom = max(2000 / lado_px, dpi / 72) if lado_px else dpi / 72
            mat = fitz.Matrix(zoom, zoom)
            pix = pagina.get_pixmap(matrix=mat, clip=bbox, colorspace=fitz.csRGB)
        else:
            mat = fitz.Matrix(dpi / 72, dpi / 72)
            pix = pagina.get_pixmap(matrix=mat, colorspace=fitz.csRGB)
    finally:
        doc.close()

    tmp = Path(tempfile.mkdtemp(prefix="vf_render_")) / "pagina.jpg"
    pix.save(str(tmp), jpg_quality=95)
    return tmp


def _pdftotext_layout(pdf: Path) -> str:
    """Extrae texto con ``pdftotext --layout`` (mejor para PDFs aptos: columnas).

    Requiere poppler (``pdftotext``) en el sistema. Si no está disponible o
    falla, devuelve cadena vacía (el llamador decide el fallback).
    """
    if shutil.which("pdftotext") is None:
        return ""
    with tempfile.TemporaryDirectory(prefix="vf_pdftotext_") as d:
        out = Path(d) / "salida.txt"
        try:
            subprocess.run(
                ["pdftotext", "-layout", str(pdf), str(out)],
                check=True,
                capture_output=True,
                timeout=60,
            )
        except Exception:
            return ""
        return out.read_text(encoding="utf-8") if out.exists() else ""


def implementar_t104(archivo: Path) -> tuple[str, str, str]:
    """Procesa un archivo con la cadena F1; devuelve (tipo, orientacion, markdown).

    Ruteo por tipo (PROC.md §5):
      - PDF ``apto`` (routing: texto nativo, imágenes no dominan) → se usa
        ``pdftotext --layout`` que recupera mejor columnas/campos alineados.
      - PDF ``requiere_ocr`` / ``parcial`` → render a imagen + Docling OCR.
      - ``pdf_texto`` no-apto, imagen, office, texto → Docling directo.
    """
    tipo = detectar(archivo).tipo

    # --- PDF: decidir por routing (apto -> layout; resto -> Docling) ---
    if tipo in ("pdf_texto", "pdf_escaneado") and archivo.suffix.lower() == ".pdf":
        analisis = analizar_pdf(archivo)

        # PDF apto (todas las páginas con texto nativo) -> pdftotext --layout.
        if analisis.veredicto == VeredictoPdf.apto:
            md = _pdftotext_layout(archivo)
            if md.strip():
                return tipo, "horizontal", md + "\n"

        # PDF que requiere OCR o es parcial: render -> imagen -> Docling OCR.
        converter = DoclingConverter()
        if analisis.veredicto == VeredictoPdf.requiere_ocr or analisis.requiere_ocr_en_alguna:
            img = _render_pdf_a_jpg(archivo)
            try:
                doc = converter.convert(img)
            finally:
                img.unlink(missing_ok=True)
        else:
            doc = converter.convert(archivo)

        orientacion = detectar_orientacion(doc.boxes)
        markdown = exportar_documento(doc)
        return tipo, orientacion, markdown

    # --- Imagen / office / texto / pdf sin routing claro: Docling directo ---
    converter = DoclingConverter()
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

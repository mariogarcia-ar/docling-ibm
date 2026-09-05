from __future__ import annotations

"""
cd /Users/mgarcia/Desktop/ibm-docling
python process_images.py files/2025-08/2D2C9343/2991f57d-c143-4b23-9f87-4dfb1214ef53.jpg
python process_images.py files/2025-08/2E1F7D6C/deddaca4-049e-4a9e-be19-9da7ddde3ec6.jpg

"""

import argparse
from pathlib import Path

from docling.datamodel.base_models import InputFormat
from docling.datamodel.pipeline_options import PdfPipelineOptions
from docling.document_converter import DocumentConverter, ImageFormatOption


def build_converter():
    """Crea un convertidor Docling para imágenes con OCR y estructura de tabla habilitada."""
    pipeline_options = PdfPipelineOptions()
    pipeline_options.ocr_options.force_full_page_ocr = True
    pipeline_options.do_table_structure = True

    return DocumentConverter(
        format_options={
            InputFormat.IMAGE: ImageFormatOption(pipeline_options=pipeline_options),
        }
    )


def process_image(image_path: str | Path):
    image_path = Path(image_path)
    if not image_path.exists():
        raise FileNotFoundError(f"No existe la imagen: {image_path}")

    converter = build_converter()
    result = converter.convert(str(image_path))
    doc = result.document

    # Guardar markdown en la misma carpeta que la imagen original
    output_md = image_path.with_suffix('.md')
    output_md.write_text(doc.export_to_markdown(), encoding='utf-8')
    print(f"Markdown guardado en: {output_md}")

    print(f"\n--- Áreas semánticas detectadas en '{doc.name}' ---\n")
    for item, level in doc.iterate_items():
        tipo_area = item.label.value if hasattr(item, 'label') else type(item).__name__
        texto = item.text.strip() if hasattr(item, 'text') else ""
        texto_corto = (texto[:80] + '...') if len(texto) > 80 else texto

        print(f"[{tipo_area.upper()}]")
        print(f"  Texto: {texto_corto or '(sin texto)'}")

        if getattr(item, 'prov', None):
            for prov in item.prov:
                page_num = prov.page_no
                bbox = prov.bbox
                print(
                    f"  Ubicación: Página {page_num} | "
                    f"Coordenadas: [{bbox.l:.1f}, {bbox.t:.1f}, {bbox.r:.1f}, {bbox.b:.1f}]"
                )

        print('-' * 50)

    return output_md


def iter_images(directory: str | Path):
    root = Path(directory)
    for ext in ('.jpg', '.jpeg', '.png', '.tif', '.tiff', '.bmp', '.pdf'):
        yield from root.rglob(f'*{ext}')
        yield from root.rglob(f'*{ext.upper()}')


def main():
    parser = argparse.ArgumentParser(
        description='Procesa imágenes con Docling y guarda el markdown junto al archivo original.'
    )
    parser.add_argument(
        'source',
        nargs='?',
        default='files',
        help='Archivo o carpeta a procesar. Por defecto: files'
    )
    args = parser.parse_args()

    source = Path(args.source)

    if source.is_file():
        process_image(source)
        return

    files = sorted({p for p in iter_images(source) if p.is_file()})
    if not files:
        print(f'No se encontraron imágenes o PDFs en: {source}')
        return

    for image_path in files:
        print(f"\n=== Procesando: {image_path} ===")
        try:
            process_image(image_path)
        except Exception as exc:
            print(f"ERROR al procesar {image_path}: {exc}")


if __name__ == '__main__':
    main()



import argparse
from pathlib import Path

from docling.document_converter import DocumentConverter, ImageFormatOption
from docling.datamodel.base_models import InputFormat
from docling.datamodel.pipeline_options import PdfPipelineOptions


def get_orientation_for_item(item):
    if not getattr(item, 'prov', None):
        return None

    for prov in item.prov:
        bbox = prov.bbox
        width = abs(bbox.r - bbox.l)
        height = abs(bbox.t - bbox.b)
        return 'horizontal' if width >= height else 'vertical'

    return None


def get_dominant_orientation(doc):
    items_with_orientation = []
    for item, _ in doc.iterate_items():
        orientation = get_orientation_for_item(item)
        if orientation:
            items_with_orientation.append(orientation)

    if not items_with_orientation:
        return 'horizontal'

    horizontal_count = sum(1 for orientation in items_with_orientation if orientation == 'horizontal')
    vertical_count = sum(1 for orientation in items_with_orientation if orientation == 'vertical')
    return 'horizontal' if horizontal_count >= vertical_count else 'vertical'


def export_orientation_text(doc, selected_orientation):
    selected_lines = []

    for item, _ in doc.iterate_items():
        if not hasattr(item, 'text'):
            continue

        text = str(item.text).strip()
        if not text:
            continue

        item_orientation = get_orientation_for_item(item)
        if item_orientation == selected_orientation:
            selected_lines.append(text)

    if not selected_lines:
        return f"No se encontraron textos en orientación {selected_orientation}.\n"

    return '\n'.join(selected_lines) + '\n'


def main():
    parser = argparse.ArgumentParser(description='Extrae texto de una imagen y filtra por orientación horizontal o vertical.')
    parser.add_argument('--image', default='files/2025-08/2E1F7D6C/deddaca4-049e-4a9e-be19-9da7ddde3ec6.jpg', help='Ruta de la imagen a procesar.')
    parser.add_argument(
        '--orientation',
        choices=['auto', 'horizontal', 'vertical'],
        default='auto',
        help='Orientación a mostrar: auto usa la dominante, horizontal o vertical muestran solo ese tipo de texto.'
    )
    args = parser.parse_args()

    pipeline_options = PdfPipelineOptions()
    pipeline_options.ocr_options.force_full_page_ocr = True
    pipeline_options.do_table_structure = True

    converter = DocumentConverter(
        format_options={
            InputFormat.IMAGE: ImageFormatOption(pipeline_options=pipeline_options),
        }
    )

    result = converter.convert(args.image)
    doc = result.document

    dominant_orientation = get_dominant_orientation(doc)
    selected_orientation = dominant_orientation if args.orientation == 'auto' else args.orientation

    output_path = Path(args.image).with_suffix('.md')
    output_text = export_orientation_text(doc, selected_orientation)
    output_path.write_text(output_text, encoding='utf-8')

    print(f"Orientación seleccionada: {selected_orientation}")
    print(f"Orientación dominante en el documento: {dominant_orientation}")
    print(f"Archivo guardado en: {output_path}")


if __name__ == '__main__':
    main()

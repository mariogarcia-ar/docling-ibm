import argparse
from pathlib import Path

from lib.converter import setup_converter


def main():
    parser = argparse.ArgumentParser(
        description='Convierte una imagen o PDF a Markdown crudo de Docling.'
    )
    parser.add_argument(
        '--input',
        default='files/2025-08/2E1F7D6C/deddaca4-049e-4a9e-be19-9da7ddde3ec6.jpg',
        help='Ruta del archivo a procesar.',
    )
    parser.add_argument(
        '--output',
        help='Ruta del Markdown de salida (por defecto, junto al archivo de entrada con .raw.md).',
    )
    args = parser.parse_args()

    input_path = Path(args.input)
    output_path = (
        Path(args.output)
        if args.output
        else input_path.with_name(f'{input_path.stem}.raw.md')
    )

    document = setup_converter().convert(str(input_path)).document
    output_path.write_text(document.export_to_markdown(), encoding='utf-8')

    print(f'Markdown crudo guardado en: {output_path}')


if __name__ == '__main__':
    main()
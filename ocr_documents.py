import argparse

from lib.processor import process_directory_recursive


DESCRIPTION = """
Procesa recursivamente imágenes y PDFs con Docling y guarda el texto extraído
ordenado por boxes en archivos Markdown.
"""


def build_parser():
    parser = argparse.ArgumentParser(
        description=DESCRIPTION,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument(
        'directory',
        nargs='?',
        default='files',
        help='Directorio raíz a procesar (por defecto: files)',
    )
    parser.add_argument(
        '-o', '--output',
        dest='output_dir',
        help='Directorio de salida (por defecto: junto al archivo original)',
    )
    parser.add_argument(
        '-f', '--force',
        action='store_true',
        help='Sobrescribe los archivos Markdown existentes',
    )
    parser.add_argument(
        '-w', '--workers',
        type=int,
        default=1,
        help='Cantidad de workers paralelos (por defecto: 1)',
    )
    parser.add_argument(
        '--orientation',
        choices=['auto', 'horizontal', 'vertical'],
        default='auto',
        help='Orientación a extraer (por defecto: auto)',
    )
    return parser


def main():
    args = build_parser().parse_args()
    print(f"Procesando directorio: {args.directory}")
    print(f"Orientación seleccionada: {args.orientation}")
    if args.output_dir:
        print(f"Guardando resultados en: {args.output_dir}")
    if args.force:
        print("Modo: Forzar reprocesamiento (--force)")
    if args.workers > 1:
        print(f"Workers: {args.workers} (paralelo)")

    process_directory_recursive(
        args.directory,
        output_dir=args.output_dir,
        skip_existing=not args.force,
        workers=args.workers,
        orientation=args.orientation,
    )


if __name__ == '__main__':
    main()

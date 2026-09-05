import os
import argparse
import re
from pathlib import Path
from concurrent.futures import ProcessPoolExecutor, as_completed
from docling.document_converter import DocumentConverter, ImageFormatOption
from docling.datamodel.base_models import InputFormat
from docling.datamodel.pipeline_options import PdfPipelineOptions

# Variable global para el convertidor en cada proceso worker
_converter = None


def get_orientation_for_item(item):
    """Devuelve 'horizontal' o 'vertical' según el tamaño del bounding box."""
    if not getattr(item, 'prov', None):
        return None

    for prov in item.prov:
        bbox = prov.bbox
        width = abs(bbox.r - bbox.l)
        height = abs(bbox.t - bbox.b)
        return 'horizontal' if width >= height else 'vertical'

    return None


def get_dominant_orientation(doc):
    """Calcula la orientación predominante del documento."""
    orientations = []
    for item, _ in doc.iterate_items():
        orientation = get_orientation_for_item(item)
        if orientation:
            orientations.append(orientation)

    if not orientations:
        return 'horizontal'

    horizontal_count = sum(1 for orientation in orientations if orientation == 'horizontal')
    vertical_count = sum(1 for orientation in orientations if orientation == 'vertical')
    return 'horizontal' if horizontal_count >= vertical_count else 'vertical'


def export_orientation_text(doc, selected_orientation):
    """Exporta los textos elegidos en filas de dos columnas según su posición."""
    selected_items = []

    for item, _ in doc.iterate_items():
        if not hasattr(item, 'text'):
            continue

        text = str(item.text).strip()
        if not text:
            continue

        item_orientation = get_orientation_for_item(item)
        if item_orientation != selected_orientation or not getattr(item, 'prov', None):
            continue

        bbox = item.prov[0].bbox
        selected_items.append({
            'text': text,
            'left': min(bbox.l, bbox.r),
            'right': max(bbox.l, bbox.r),
            'center_y': (bbox.t + bbox.b) / 2,
        })

    if not selected_items:
        return f"No se encontraron textos en orientación {selected_orientation}.\n"

    def is_numeric(item):
        return re.fullmatch(r'-?[0-9][0-9.,]*', item['text']) is not None

    def is_quantity(item):
        text = item['text'].lower()
        return ('×' in text or ' x ' in text) and '(' in text

    numeric_items = [
        item for item in selected_items
        if is_numeric(item)
    ]

    # The largest horizontal gap separates the left labels from right values.
    x_centers = sorted((item['left'] + item['right']) / 2 for item in numeric_items)
    if len(x_centers) > 1:
        gaps = [
            (x_centers[index + 1] - x_centers[index], index)
            for index in range(len(x_centers) - 1)
        ]
        _, split_index = max(gaps)
        split_x = (x_centers[split_index] + x_centers[split_index + 1]) / 2
    else:
        split_x = float('inf')

    right_items = {
        id(item): item for item in numeric_items
        if (item['left'] + item['right']) / 2 > split_x
    }
    left_items = [item for item in selected_items if id(item) not in right_items]
    pairs = {}
    paired_right_ids = set()

    ean_item = next((item for item in left_items if item['text'] == 'EAN'), None)
    subtotal_item = next((item for item in left_items if item['text'].startswith('SUBTOT')), None)
    if ean_item and subtotal_item:
        product_labels = [
            item for item in left_items
            if subtotal_item['center_y'] < item['center_y'] < ean_item['center_y']
            and not is_numeric(item)
            and not is_quantity(item)
            and not item['text'].startswith('Cant.')
        ]
        product_values = [
            item for item in numeric_items
            if subtotal_item['center_y'] < item['center_y'] < ean_item['center_y']
            and (item['left'] + item['right']) / 2 > split_x
        ]
        product_labels.sort(key=lambda item: -item['center_y'])
        product_values.sort(key=lambda item: -item['center_y'])
        for label, value in zip(product_labels, product_values):
            pairs[id(label)] = value['text']
            paired_right_ids.add(id(value))

    for right_item in right_items.values():
        if id(right_item) in paired_right_ids:
            continue
        candidates = [
            item for item in left_items
            if id(item) not in pairs
            and not is_numeric(item)
            and not is_quantity(item)
        ]
        if not candidates:
            continue

        left_item = min(
            candidates,
            key=lambda item: abs(item['center_y'] - right_item['center_y']),
        )
        pairs[id(left_item)] = right_item['text']
        paired_right_ids.add(id(right_item))

    rows = []
    for item in sorted(left_items, key=lambda value: -value['center_y']):
        value = pairs.get(id(item), '')
        rows.append(f"{item['text']} | {value}".rstrip())

    for item in sorted(
        (value for key, value in right_items.items() if key not in paired_right_ids),
        key=lambda value: -value['center_y'],
    ):
        rows.append(f" | {item['text']}")

    return '\n'.join(rows) + '\n'

def init_worker():
    """Inicializa el convertidor una sola vez por proceso worker"""
    global _converter
    if _converter is None:
        _converter = setup_converter()
        print(f"Worker {os.getpid()}: Convertidor inicializado")

def get_converter():
    """Obtiene el convertidor del worker actual"""
    global _converter
    return _converter

def setup_converter():
    """Inicializa el convertidor de Docling con OCR habilitado"""
    pipeline_options = PdfPipelineOptions()
    pipeline_options.ocr_options.force_full_page_ocr = True
    
    converter = DocumentConverter(
        format_options={
            InputFormat.IMAGE: ImageFormatOption(pipeline_options=pipeline_options),
        }
    )
    return converter

def resolve_output_file(file_path, output_dir=None):
    """Resuelve la ruta del archivo generado.
    Por defecto, se guarda junto a la imagen original.
    """
    file_path = Path(file_path)
    if output_dir is None:
        return file_path.with_suffix('.md')

    output_dir = Path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    return output_dir / file_path.name.replace(file_path.suffix, '.md')


def process_file_wrapper(args):
    """
    Wrapper para procesar archivos en paralelo.
    Usa el convertidor ya inicializado del worker (no crea uno nuevo).
    """
    file_path, output_dir, skip_existing, orientation = args

    try:
        output_file = resolve_output_file(file_path, output_dir)

        if skip_existing and output_file.exists():
            return ('skipped', file_path, None)

        converter = get_converter()
        result = converter.convert(str(file_path))
        doc = result.document

        dominant_orientation = get_dominant_orientation(doc)
        selected_orientation = dominant_orientation if orientation == 'auto' else orientation
        markdown_content = export_orientation_text(doc, selected_orientation)

        with open(output_file, 'w', encoding='utf-8') as f:
            f.write(markdown_content)

        return ('success', file_path, output_file)

    except Exception as e:
        return ('error', file_path, str(e))


def process_file(file_path, converter, output_dir=None, skip_existing=True, orientation='auto'):
    """
    Procesa un archivo individual y guarda el resultado.

    Args:
        file_path: Ruta del archivo a procesar
        converter: Instancia de DocumentConverter
        output_dir: Directorio donde guardar los resultados (opcional)
        skip_existing: Si True, salta archivos que ya tienen .md generado
        orientation: 'auto', 'horizontal' o 'vertical'

    Returns:
        True si se procesó exitosamente, False si hubo error, None si se saltó
    """
    try:
        output_file = resolve_output_file(file_path, output_dir)

        if skip_existing and output_file.exists():
            print(f"⊘ Saltando (ya existe): {file_path}")
            return None

        print(f"Procesando: {file_path}")
        result = converter.convert(str(file_path))
        doc = result.document

        dominant_orientation = get_dominant_orientation(doc)
        selected_orientation = dominant_orientation if orientation == 'auto' else orientation
        markdown_content = export_orientation_text(doc, selected_orientation)

        with open(output_file, 'w', encoding='utf-8') as f:
            f.write(markdown_content)

        print(f"✓ Guardado: {output_file} | orientación: {selected_orientation}")
        return True

    except Exception as e:
        print(f"✗ Error procesando {file_path}: {str(e)}")
        return False


def process_directory_recursive(directory_path, output_dir=None, extensions=None, skip_existing=True, workers=1, orientation='auto'):
    """
    Recorre recursivamente un directorio y procesa todos los archivos compatibles
    
    Args:
        directory_path: Directorio raíz a procesar
        output_dir: Directorio donde guardar los resultados (opcional)
        extensions: Lista de extensiones a procesar (por defecto: imágenes y PDFs)
        skip_existing: Si True, salta archivos que ya tienen .md generado
        workers: Número de workers paralelos (1 = secuencial, >1 = paralelo)
    """
    if extensions is None:
        extensions = {'.jpg', '.jpeg', '.png', '.pdf', '.tiff', '.tif', '.bmp'}
    
    directory = Path(directory_path)
    
    if not directory.exists():
        print(f"Error: El directorio {directory_path} no existe")
        return
    
    # Buscar todos los archivos recursivamente
    all_files = []
    for ext in extensions:
        all_files.extend(directory.rglob(f"*{ext}"))
        all_files.extend(directory.rglob(f"*{ext.upper()}"))
    
    # Eliminar duplicados y ordenar
    all_files = sorted(list(set(all_files)))
    
    if not all_files:
        print(f"No se encontraron archivos con extensiones: {extensions}")
        return
    
    print(f"\nEncontrados {len(all_files)} archivo(s) para procesar")
    print(f"Modo: {'Paralelo (' + str(workers) + ' workers)' if workers > 1 else 'Secuencial'}\n")
    
    # Procesar archivos
    successful = 0
    failed = 0
    skipped = 0
    
    if workers > 1:
        print(f"Inicializando {workers} workers paralelos...")
        file_args = [(f, output_dir, skip_existing, orientation) for f in all_files]

        with ProcessPoolExecutor(max_workers=workers, initializer=init_worker) as executor:
            futures = {executor.submit(process_file_wrapper, arg): arg[0] for arg in file_args}

            for future in as_completed(futures):
                status, file_path, result = future.result()

                if status == 'success':
                    print(f"✓ Guardado: {result}")
                    successful += 1
                elif status == 'skipped':
                    print(f"⊘ Saltando (ya existe): {file_path}")
                    skipped += 1
                else:
                    print(f"✗ Error procesando {file_path}: {result}")
                    failed += 1
    else:
        print("Inicializando convertidor Docling...")
        converter = setup_converter()

        for file_path in all_files:
            result = process_file(file_path, converter, output_dir, skip_existing, orientation)
            if result is True:
                successful += 1
            elif result is False:
                failed += 1
            else:
                skipped += 1
    
    # Resumen
    print(f"\n{'='*60}")
    print(f"Procesamiento completado:")
    print(f"  ✓ Exitosos: {successful}")
    if skipped > 0:
        print(f"  ⊘ Saltados: {skipped}")
    print(f"  ✗ Fallidos: {failed}")
    print(f"  Total: {len(all_files)}")
    print(f"{'='*60}")

if __name__ == "__main__":
    # Configurar argumentos de línea de comandos
    parser = argparse.ArgumentParser(
        description='''
Procesa recursivamente imágenes y PDFs con Docling y guarda el texto extraído en archivos .md.

La herramienta puede filtrar el texto por orientación:
- auto: usa la orientación dominante del documento
- horizontal: guarda sólo texto horizontal
- vertical: guarda sólo texto vertical

Útil para documentos escaneados, comprobantes o formularios donde se mezclan textos con distinta orientación.
''',
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog='''
Ejemplos:
  python process_recursive.py                                        # Procesa la carpeta 'files'
  python process_recursive.py files/2025-08                          # Procesa una carpeta específica
  python process_recursive.py -o output files                      # Guarda resultados en 'output'
  python process_recursive.py --force files                         # Reprocesa archivos aunque ya existan .md
  python process_recursive.py -w 4 files/2025-08                   # Procesa con 4 workers en paralelo
  python process_recursive.py --orientation horizontal files        # Guarda solo texto horizontal
  python process_recursive.py --orientation vertical files          # Guarda solo texto vertical
  python process_recursive.py --orientation auto files              # Usa la orientación predominante

Ejemplo real con esta carpeta:
  python process_recursive.py --orientation vertical files/2025-08/2E1F7D6C
  python process_recursive.py --orientation horizontal files/2025-08/2E1F7D6C
        '''
    )
    
    parser.add_argument(
        'directory',
        nargs='?',
        default='files',
        help='Directorio a procesar recursivamente (por defecto: files)'
    )
    
    parser.add_argument(
        '-o', '--output',
        dest='output_dir',
        default=None,
        help='Directorio donde guardar los archivos .md (por defecto: misma carpeta que los originales)'
    )
    
    parser.add_argument(
        '-f', '--force',
        action='store_true',
        help='Forzar reprocesamiento de archivos aunque ya exista el .md'
    )
    
    parser.add_argument(
        '-w', '--workers',
        type=int,
        default=1,
        help='Número de workers paralelos (por defecto: 1 = secuencial, >1 = paralelo)'
    )

    parser.add_argument(
        '--orientation',
        choices=['auto', 'horizontal', 'vertical'],
        default='auto',
        help='Orientación de texto a guardar: auto usa la dominante, horizontal o vertical filtran solo ese tipo.'
    )

    args = parser.parse_args()

    print(f"Procesando directorio: {args.directory}")
    if args.output_dir:
        print(f"Guardando resultados en: {args.output_dir}")
    if args.force:
        print(f"Modo: Forzar reprocesamiento (--force)")
    if args.workers > 1:
        print(f"Workers: {args.workers} (paralelo)")
    print(f"Orientación seleccionada: {args.orientation}")

    process_directory_recursive(
        args.directory,
        output_dir=args.output_dir,
        skip_existing=not args.force,
        workers=args.workers,
        orientation=args.orientation
    )

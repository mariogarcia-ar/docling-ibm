import os
import argparse
from pathlib import Path
from concurrent.futures import ProcessPoolExecutor, as_completed
from functools import partial
from docling.document_converter import DocumentConverter, ImageFormatOption
from docling.datamodel.base_models import InputFormat
from docling.datamodel.pipeline_options import PdfPipelineOptions

# Variable global para el convertidor en cada proceso worker
_converter = None

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

def process_file_wrapper(args):
    """
    Wrapper para procesar archivos en paralelo
    Usa el convertidor ya inicializado del worker (no crea uno nuevo)
    """
    file_path, output_dir, skip_existing = args
    
    try:
        # Crear nombre del archivo de salida
        if output_dir:
            relative_path = file_path.relative_to(Path(file_path).parts[0])
            output_file = Path(output_dir) / relative_path.with_suffix('.md')
            output_file.parent.mkdir(parents=True, exist_ok=True)
        else:
            output_file = file_path.with_suffix('.md')
        
        # Verificar si ya existe el archivo de salida
        if skip_existing and output_file.exists():
            return ('skipped', file_path, None)
        
        # Usar el convertidor ya inicializado del worker
        converter = get_converter()
        result = converter.convert(str(file_path))
        markdown_content = result.document.export_to_markdown()
        
        # Guardar el markdown
        with open(output_file, 'w', encoding='utf-8') as f:
            f.write(markdown_content)
        
        return ('success', file_path, output_file)
        
    except Exception as e:
        return ('error', file_path, str(e))


def process_file(file_path, converter, output_dir=None, skip_existing=True):
    """
    Procesa un archivo individual y guarda el resultado
    
    Args:
        file_path: Ruta del archivo a procesar
        converter: Instancia de DocumentConverter
        output_dir: Directorio donde guardar los resultados (opcional)
        skip_existing: Si True, salta archivos que ya tienen .md generado
    
    Returns:
        True si se procesó exitosamente, False si hubo error, None si se saltó
    """
    try:
        # Crear nombre del archivo de salida
        if output_dir:
            # Mantener estructura de carpetas relativa
            relative_path = file_path.relative_to(Path(file_path).parts[0])
            output_file = Path(output_dir) / relative_path.with_suffix('.md')
            output_file.parent.mkdir(parents=True, exist_ok=True)
        else:
            # Guardar en la misma carpeta que el archivo original
            output_file = file_path.with_suffix('.md')
        
        # Verificar si ya existe el archivo de salida
        if skip_existing and output_file.exists():
            print(f"⊘ Saltando (ya existe): {file_path}")
            return None
        
        print(f"Procesando: {file_path}")
        result = converter.convert(str(file_path))
        markdown_content = result.document.export_to_markdown()
        
        # Guardar el markdown
        with open(output_file, 'w', encoding='utf-8') as f:
            f.write(markdown_content)
        
        print(f"✓ Guardado: {output_file}")
        return True
        
    except Exception as e:
        print(f"✗ Error procesando {file_path}: {str(e)}")
        return False

def process_directory_recursive(directory_path, output_dir=None, extensions=None, skip_existing=True, workers=1):
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
        # Procesamiento en paralelo con ProcessPoolExecutor
        # Cada proceso carga los weights una sola vez al inicio
        print(f"Inicializando {workers} workers paralelos...")
        file_args = [(f, output_dir, skip_existing) for f in all_files]
        
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
                else:  # error
                    print(f"✗ Error procesando {file_path}: {result}")
                    failed += 1
    else:
        # Procesamiento secuencial (original)
        print("Inicializando convertidor Docling...")
        converter = setup_converter()
        
        for file_path in all_files:
            result = process_file(file_path, converter, output_dir, skip_existing)
            if result is True:
                successful += 1
            elif result is False:
                failed += 1
            else:  # None = skipped
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
        description='Procesa recursivamente archivos de imágenes y PDFs usando Docling OCR',
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog='''
Ejemplos:
  python process_recursive.py                        # Procesa 'files' (por defecto)
  python process_recursive.py files/2025-08         # Procesa carpeta específica
  python process_recursive.py -o output files       # Guarda resultados en carpeta 'output'
  python process_recursive.py --force files         # Reprocesa todo, incluso archivos ya procesados
  python process_recursive.py -w 4 files/2025-08   # Procesa con 4 workers en paralelo
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
    
    args = parser.parse_args()
    
    print(f"Procesando directorio: {args.directory}")
    if args.output_dir:
        print(f"Guardando resultados en: {args.output_dir}")
    if args.force:
        print(f"Modo: Forzar reprocesamiento (--force)")
    if args.workers > 1:
        print(f"Workers: {args.workers} (paralelo)")
    
    process_directory_recursive(
        args.directory, 
        output_dir=args.output_dir, 
        skip_existing=not args.force,
        workers=args.workers
    )

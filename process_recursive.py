import os
import argparse
from pathlib import Path
from docling.document_converter import DocumentConverter, ImageFormatOption
from docling.datamodel.base_models import InputFormat
from docling.datamodel.pipeline_options import PdfPipelineOptions

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

def process_file(file_path, converter, output_dir=None):
    """
    Procesa un archivo individual y guarda el resultado
    
    Args:
        file_path: Ruta del archivo a procesar
        converter: Instancia de DocumentConverter
        output_dir: Directorio donde guardar los resultados (opcional)
    """
    try:
        print(f"Procesando: {file_path}")
        result = converter.convert(str(file_path))
        markdown_content = result.document.export_to_markdown()
        
        # Crear nombre del archivo de salida
        if output_dir:
            # Mantener estructura de carpetas relativa
            relative_path = file_path.relative_to(Path(file_path).parts[0])
            output_file = Path(output_dir) / relative_path.with_suffix('.md')
            output_file.parent.mkdir(parents=True, exist_ok=True)
        else:
            # Guardar en la misma carpeta que el archivo original
            output_file = file_path.with_suffix('.md')
        
        # Guardar el markdown
        with open(output_file, 'w', encoding='utf-8') as f:
            f.write(markdown_content)
        
        print(f"✓ Guardado: {output_file}")
        return True
        
    except Exception as e:
        print(f"✗ Error procesando {file_path}: {str(e)}")
        return False

def process_directory_recursive(directory_path, output_dir=None, extensions=None):
    """
    Recorre recursivamente un directorio y procesa todos los archivos compatibles
    
    Args:
        directory_path: Directorio raíz a procesar
        output_dir: Directorio donde guardar los resultados (opcional)
        extensions: Lista de extensiones a procesar (por defecto: imágenes y PDFs)
    """
    if extensions is None:
        extensions = {'.jpg', '.jpeg', '.png', '.pdf', '.tiff', '.tif', '.bmp'}
    
    directory = Path(directory_path)
    
    if not directory.exists():
        print(f"Error: El directorio {directory_path} no existe")
        return
    
    # Inicializar el convertidor una sola vez
    print("Inicializando convertidor Docling...")
    converter = setup_converter()
    
    # Buscar todos los archivos recursivamente
    all_files = []
    for ext in extensions:
        all_files.extend(directory.rglob(f"*{ext}"))
        all_files.extend(directory.rglob(f"*{ext.upper()}"))
    
    # Eliminar duplicados
    all_files = list(set(all_files))
    
    if not all_files:
        print(f"No se encontraron archivos con extensiones: {extensions}")
        return
    
    print(f"\nEncontrados {len(all_files)} archivo(s) para procesar\n")
    
    # Procesar cada archivo
    successful = 0
    failed = 0
    
    for file_path in sorted(all_files):
        if process_file(file_path, converter, output_dir):
            successful += 1
        else:
            failed += 1
    
    # Resumen
    print(f"\n{'='*60}")
    print(f"Procesamiento completado:")
    print(f"  ✓ Exitosos: {successful}")
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
    
    args = parser.parse_args()
    
    print(f"Procesando directorio: {args.directory}")
    if args.output_dir:
        print(f"Guardando resultados en: {args.output_dir}")
    
    process_directory_recursive(args.directory, output_dir=args.output_dir)

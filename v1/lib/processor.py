import os
from concurrent.futures import ProcessPoolExecutor, as_completed
from pathlib import Path

from lib.converter import setup_converter
from lib.orientation import export_orientation_text, get_dominant_orientation


_converter = None
SUPPORTED_EXTENSIONS = {'.jpg', '.jpeg', '.png', '.pdf', '.tiff', '.tif', '.bmp'}


def init_worker():
    """Inicializa un convertidor por worker."""
    global _converter
    if _converter is None:
        _converter = setup_converter()
        print(f"Worker {os.getpid()}: Convertidor inicializado")


def get_converter():
    """Devuelve el convertidor del worker actual."""
    return _converter


def resolve_output_file(file_path, output_dir=None):
    """Resuelve la ruta del Markdown generado."""
    file_path = Path(file_path)
    if output_dir is None:
        return file_path.with_suffix('.md')

    output_dir = Path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    return output_dir / f'{file_path.stem}.md'


def _convert_file(file_path, converter, output_dir=None):
    output_file = resolve_output_file(file_path, output_dir)
    result = converter.convert(str(file_path))
    document = result.document
    orientation = get_dominant_orientation(document)
    content = export_orientation_text(document, orientation)
    output_file.write_text(content, encoding='utf-8')
    return output_file, orientation


def process_file_wrapper(args):
    """Procesa un archivo dentro de un worker."""
    file_path, output_dir, skip_existing, orientation = args
    output_file = resolve_output_file(file_path, output_dir)

    try:
        if skip_existing and output_file.exists():
            return 'skipped', file_path, None

        converter = get_converter()
        result = converter.convert(str(file_path))
        document = result.document
        selected_orientation = (
            get_dominant_orientation(document)
            if orientation == 'auto'
            else orientation
        )
        content = export_orientation_text(document, selected_orientation)
        output_file.write_text(content, encoding='utf-8')
        return 'success', file_path, output_file
    except Exception as error:
        return 'error', file_path, str(error)


def process_file(file_path, converter, output_dir=None, skip_existing=True, orientation='auto'):
    """Procesa un archivo individual."""
    output_file = resolve_output_file(file_path, output_dir)
    if skip_existing and output_file.exists():
        print(f"⊘ Saltando (ya existe): {file_path}")
        return None

    try:
        document = converter.convert(str(file_path)).document
        selected_orientation = (
            get_dominant_orientation(document)
            if orientation == 'auto'
            else orientation
        )
        output_file.write_text(
            export_orientation_text(document, selected_orientation),
            encoding='utf-8',
        )
        print(f"✓ Guardado: {output_file} | orientación: {selected_orientation}")
        return True
    except Exception as error:
        print(f"✗ Error procesando {file_path}: {error}")
        return False


def _find_files(directory_path, extensions=None):
    extensions = extensions or SUPPORTED_EXTENSIONS
    directory = Path(directory_path)
    return sorted({
        file_path
        for extension in extensions
        for pattern in (f'*{extension}', f'*{extension.upper()}')
        for file_path in directory.rglob(pattern)
    })


def process_directory_recursive(
    directory_path,
    output_dir=None,
    extensions=None,
    skip_existing=True,
    workers=1,
    orientation='auto',
):
    """Procesa recursivamente los archivos compatibles de una carpeta."""
    directory = Path(directory_path)
    if not directory.exists():
        print(f"Error: el directorio {directory_path} no existe")
        return

    files = _find_files(directory, extensions)
    if not files:
        print("No se encontraron archivos compatibles")
        return

    print(f"\nEncontrados {len(files)} archivo(s) para procesar")
    mode = f"Paralelo ({workers} workers)" if workers > 1 else "Secuencial"
    print(f"Modo: {mode}\n")

    successful = failed = skipped = 0
    if workers > 1:
        arguments = [
            (file_path, output_dir, skip_existing, orientation)
            for file_path in files
        ]
        with ProcessPoolExecutor(
            max_workers=workers,
            initializer=init_worker,
        ) as executor:
            futures = {
                executor.submit(process_file_wrapper, args): args[0]
                for args in arguments
            }
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
        converter = setup_converter()
        for file_path in files:
            result = process_file(
                file_path,
                converter,
                output_dir,
                skip_existing,
                orientation,
            )
            if result is True:
                successful += 1
            elif result is False:
                failed += 1
            else:
                skipped += 1

    print("\n" + "=" * 60)
    print("Procesamiento completado:")
    print(f"  ✓ Exitosos: {successful}")
    if skipped:
        print(f"  ⊘ Saltados: {skipped}")
    print(f"  ✗ Fallidos: {failed}")
    print(f"  Total: {len(files)}")
    print("=" * 60)

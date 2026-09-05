#!/usr/bin/env python3
"""Ejecuta OCR, extracción 10/11 y clasificación 01/02/03 sobre imágenes."""

import argparse
import json
import sys
from pathlib import Path

from classification_pipeline import PROMPT_FILES, primary_center_cost, primary_macro_category
from extraction_pipeline import PROMPTS
from extraction_invoice.ask import DEFAULT_MODEL, ask_ollama, load_document_text
from lib.converter import setup_converter
from lib.orientation import export_orientation_text, get_dominant_orientation
from lib.pipeline import execute_prompt
from lib.pipeline import write_results
from lib.processor import SUPPORTED_EXTENSIONS


def find_source_files(source: Path):
    if source.is_file():
        return [source]
    return sorted({
        file_path
        for extension in SUPPORTED_EXTENSIONS
        for pattern in (f"*{extension}", f"*{extension.upper()}")
        for file_path in source.rglob(pattern)
    })


def markdown_output(image_path: Path, output_dir: Path | None) -> Path:
    if output_dir is None:
        return image_path.with_suffix(".md")
    output_dir.mkdir(parents=True, exist_ok=True)
    return output_dir / f"{image_path.stem}.md"


def create_markdown(image_path: Path, converter, output_path: Path, orientation: str):
    document = converter.convert(str(image_path)).document
    selected_orientation = (
        get_dominant_orientation(document)
        if orientation == "auto"
        else orientation
    )
    output_path.write_text(
        export_orientation_text(document, selected_orientation),
        encoding="utf-8",
    )
    return selected_orientation


def checkpoint_output(image_path: Path) -> Path:
    return image_path.with_name(f"{image_path.stem}_pipeline.json")


def save_checkpoint(output_path: Path, result: dict):
    output_path.write_text(
        json.dumps(result, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )


def run_extraction_step(prompt_path: Path, document: str, model: str):
    return execute_prompt(prompt_path, {"documento": document}, model)


def run_classification_step(prompt_path: Path, values: dict[str, str], model: str):
    return execute_prompt(prompt_path, values, model)


def process_image(
    image_path: Path,
    converter,
    model: str,
    tax_condition: str,
    orientation: str,
    force: bool,
):
    checkpoint_path = checkpoint_output(image_path)
    if checkpoint_path.exists() and not force:
        result = json.loads(checkpoint_path.read_text(encoding="utf-8"))
        print(f"  Reanudando desde {checkpoint_path}", file=sys.stderr)
    else:
        result = {
            "archivo": str(image_path),
            "extracciones": {},
            "clasificacion": {},
            "errores": {},
        }
    result.setdefault("extracciones", {})
    result.setdefault("clasificacion", {})
    result.setdefault("errores", {})

    markdown_path = image_path.with_suffix(".md")
    if force or not markdown_path.exists():
        selected_orientation = create_markdown(
            image_path,
            converter,
            markdown_path,
            orientation,
        )
        result["markdown"] = str(markdown_path)
        result["orientacion"] = selected_orientation
        save_checkpoint(checkpoint_path, result)
    else:
        result["markdown"] = str(markdown_path)
    save_checkpoint(checkpoint_path, result)

    document = load_document_text(markdown_path)

    for step_name, prompt_path in PROMPTS.items():
        if step_name in result["extracciones"]:
            print(f"  Reutilizando {step_name}", file=sys.stderr)
            continue
        try:
            result["extracciones"][step_name] = run_extraction_step(
                prompt_path,
                document,
                model,
            )
            result["errores"].pop(step_name, None)
        except Exception as error:
            result["errores"][step_name] = str(error)
        save_checkpoint(checkpoint_path, result)
        print(f"  Checkpoint guardado después de {step_name}", file=sys.stderr)

    if "01_centro_costo" not in result["clasificacion"]:
        try:
            result["clasificacion"]["01_centro_costo"] = run_classification_step(
                PROMPT_FILES["01"],
                {
                    "proveedor": "no informado",
                    "descripcion": document,
                    "monto": "no informado",
                },
                model,
            )
            result["errores"].pop("01_centro_costo", None)
        except Exception as error:
            result["errores"]["01_centro_costo"] = str(error)
        save_checkpoint(checkpoint_path, result)
        print("  Checkpoint guardado después de 01_centro_costo", file=sys.stderr)
    else:
        print("  Reutilizando 01_centro_costo", file=sys.stderr)

    if "01_centro_costo" in result["clasificacion"] and "02_macro_categoria" not in result["clasificacion"]:
        try:
            center_cost = primary_center_cost(result["clasificacion"]["01_centro_costo"])
            result["clasificacion"]["02_macro_categoria"] = run_classification_step(
                PROMPT_FILES["02"],
                {
                    "centro_costo": center_cost,
                    "proveedor": "no informado",
                    "descripcion": document,
                    "monto": "no informado",
                },
                model,
            )
            result["errores"].pop("02_macro_categoria", None)
        except Exception as error:
            result["errores"]["02_macro_categoria"] = str(error)
        save_checkpoint(checkpoint_path, result)
        print("  Checkpoint guardado después de 02_macro_categoria", file=sys.stderr)
    elif "02_macro_categoria" in result["clasificacion"]:
        print("  Reutilizando 02_macro_categoria", file=sys.stderr)

    if "02_macro_categoria" in result["clasificacion"] and "03_concepto_codigo_final" not in result["clasificacion"]:
        try:
            macro_category = primary_macro_category(result["clasificacion"]["02_macro_categoria"])
            result["clasificacion"]["03_concepto_codigo_final"] = run_classification_step(
                PROMPT_FILES["03"],
                {
                    "macro_categoria": macro_category,
                    "proveedor": "no informado",
                    "descripcion": document,
                    "monto": "no informado",
                    "condicion_impositiva": tax_condition,
                },
                model,
            )
            result["errores"].pop("03_concepto_codigo_final", None)
        except Exception as error:
            result["errores"]["03_concepto_codigo_final"] = str(error)
        save_checkpoint(checkpoint_path, result)
        print("  Checkpoint guardado después de 03_concepto_codigo_final", file=sys.stderr)
    elif "03_concepto_codigo_final" in result["clasificacion"]:
        print("  Reutilizando 03_concepto_codigo_final", file=sys.stderr)

    if not result["errores"]:
        result.pop("errores", None)
    save_checkpoint(checkpoint_path, result)
    return result


def main():
    parser = argparse.ArgumentParser(
        description="""
Ejecuta el pipeline completo por imagen:
  1. OCR de la imagen y generación de Markdown.
  2. Extracciones independientes con prompts 10 y 11.
  3. Clasificación encadenada con prompts 01, 02 y 03.

La clasificación lee el Markdown generado. No recibe resultados de 10/11.
""",
        epilog="""
Ejemplos:
  python full_pipeline.py imagen.jpg
  python full_pipeline.py files/2025-08
  python full_pipeline.py files/2025-08 --orientation horizontal
  python full_pipeline.py imagen.jpg --condicion-impositiva 10_5
    python full_pipeline.py imagen.jpg --force
  python full_pipeline.py files/2025-08 -o resultados_completos.json
""",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument(
        "source",
        type=Path,
        help="Imagen/PDF o directorio a recorrer recursivamente",
    )
    parser.add_argument(
        "-m",
        "--model",
        default=DEFAULT_MODEL,
        help=f"Modelo de Ollama (por defecto: {DEFAULT_MODEL})",
    )
    parser.add_argument(
        "--condicion-impositiva",
        default="21",
        help="Condición para el paso 03: 21, 10_5, 27, 2_5 o exento_no_gravado",
    )
    parser.add_argument(
        "--orientation",
        choices=["auto", "horizontal", "vertical"],
        default="auto",
    )
    parser.add_argument(
        "-o",
        "--output",
        type=Path,
        help="JSON agregado; sin esta opción crea un JSON junto a cada imagen",
    )
    parser.add_argument(
        "-f",
        "--force",
        action="store_true",
        help="Reprocesa OCR y todos los pasos, ignorando checkpoints existentes",
    )
    args = parser.parse_args()

    if not args.source.exists():
        print(f"Error: no existe '{args.source}'", file=sys.stderr)
        sys.exit(1)

    files = find_source_files(args.source)
    if not files:
        print(f"No se encontraron imágenes o PDFs en '{args.source}'", file=sys.stderr)
        sys.exit(1)

    converter = setup_converter()
    results = []
    for index, image_path in enumerate(files, start=1):
        print(f"[{index}/{len(files)}] Procesando {image_path}", file=sys.stderr)
        try:
            results.append(process_image(
                image_path,
                converter,
                args.model,
                args.condicion_impositiva,
                args.orientation,
                args.force,
            ))
        except Exception as error:
            print(f"Error en '{image_path}': {error}", file=sys.stderr)
            results.append({
                "archivo": str(image_path),
                "extracciones": {},
                "clasificacion": {},
                "errores": {"pipeline": str(error)},
            })

    if args.output:
        write_results(args.output, results)
    else:
        for result in results:
            output_path = Path(result["archivo"]).with_name(
                f"{Path(result['archivo']).stem}_pipeline.json"
            )
            output_path.write_text(
                json.dumps(result, ensure_ascii=False, indent=2),
                encoding="utf-8",
            )
            print(f"Guardado: {output_path}", file=sys.stderr)


if __name__ == "__main__":
    main()

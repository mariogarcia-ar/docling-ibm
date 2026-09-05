#!/usr/bin/env python3
"""Ejecuta secuencialmente la clasificación contable 01, 02 y 03."""

import argparse
import json
import sys
from pathlib import Path

from extraction_invoice.ask import DEFAULT_MODEL, ask_ollama, load_document_text
from document_extraction import extract_json, load_prompt

ROOT = Path(__file__).resolve().parent
PROMPTS_DIR = ROOT / "prompts"
PROMPT_FILES = {
    "01": PROMPTS_DIR / "01-clasificacion_centro_costo_prompt.yaml",
    "02": PROMPTS_DIR / "02-clasificacion_macro_categoria_prompt.yaml",
    "03": PROMPTS_DIR / "03-clasificacion_concepto_codigo_final_prompt.yaml",
}


class ClassificationError(Exception):
    """Error de un paso con resultados parciales para evaluación."""

    def __init__(self, message, steps):
        super().__init__(message)
        self.steps = steps


def find_markdown_files(source: Path):
    if source.is_file():
        return [source]
    return sorted(source.rglob("*.md"))


def ask_prompt(prompt_path: Path, values: dict[str, str], model: str):
    prompt = load_prompt(prompt_path)
    user_prompt = prompt["user"]
    replacements = {
        "proveedor": "no informado",
        "descripcion": values["descripcion"],
        "monto": "no informado",
        **values,
    }
    for key, value in replacements.items():
        user_prompt = user_prompt.replace(f"{{{{{key}}}}}", value)

    messages = [
        {"role": "system", "content": prompt["system"]},
        {"role": "user", "content": user_prompt},
    ]
    try:
        return extract_json(ask_ollama(messages, model))
    except (ValueError, json.JSONDecodeError) as error:
        raise ValueError(f"{prompt_path.name}: {error}") from error


def primary_center_cost(step_01: dict) -> str:
    options = step_01.get("centros_costos", [])
    if not options:
        raise ValueError("El paso 01 no devolvió centros_costos")
    return options[0]["codigo_centro_costo"]


def primary_macro_category(step_02: dict) -> str:
    options = step_02.get("macro_categorias", [])
    if not options:
        raise ValueError("El paso 02 no devolvió macro_categorias")
    return options[0]["macro_categoria"]


def classify_document(document_path: Path, model: str, tax_condition: str):
    description = load_document_text(document_path)
    base_values = {"descripcion": description}
    steps = {}

    try:
        step_01 = ask_prompt(PROMPT_FILES["01"], base_values, model)
    except ValueError as error:
        raise ClassificationError(str(error), steps) from error
    steps["01_centro_costo"] = step_01
    center_cost = primary_center_cost(step_01)

    try:
        step_02 = ask_prompt(
            PROMPT_FILES["02"],
            {**base_values, "centro_costo": center_cost},
            model,
        )
    except ValueError as error:
        raise ClassificationError(str(error), steps) from error
    steps["02_macro_categoria"] = step_02
    macro_category = primary_macro_category(step_02)

    try:
        step_03 = ask_prompt(
            PROMPT_FILES["03"],
            {
                **base_values,
                "macro_categoria": macro_category,
                "condicion_impositiva": tax_condition,
            },
            model,
        )
    except ValueError as error:
        raise ClassificationError(str(error), steps) from error
    steps["03_concepto_codigo_final"] = step_03

    return {
        "archivo": str(document_path),
        "pasos": steps,
    }


def main():
    parser = argparse.ArgumentParser(
        description="Clasifica Markdown secuencialmente con los prompts 01, 02 y 03."
    )
    parser.add_argument("source", type=Path, help="Markdown o carpeta raíz a recorrer")
    parser.add_argument("-m", "--model", default=DEFAULT_MODEL)
    parser.add_argument(
        "--condicion-impositiva",
        default="21",
        help="Condición para el paso 03: 21, 10_5, 27, 2_5 o exento_no_gravado",
    )
    parser.add_argument(
        "-o", "--output", type=Path, default=Path("classification_results.json")
    )
    args = parser.parse_args()

    if not args.source.exists():
        print(f"Error: no existe '{args.source}'", file=sys.stderr)
        sys.exit(1)

    files = find_markdown_files(args.source)
    if not files:
        print(f"No se encontraron archivos Markdown en '{args.source}'", file=sys.stderr)
        sys.exit(1)

    results = []
    for index, document_path in enumerate(files, start=1):
        print(f"[{index}/{len(files)}] Clasificando {document_path}", file=sys.stderr)
        try:
            results.append(classify_document(document_path, args.model, args.condicion_impositiva))
        except ClassificationError as error:
            print(f"Error en '{document_path}': {error}", file=sys.stderr)
            results.append({
                "archivo": str(document_path),
                "pasos": error.steps,
                "error": str(error),
            })
        except (ValueError, json.JSONDecodeError, KeyError) as error:
            print(f"Error en '{document_path}': {error}", file=sys.stderr)
            results.append({"archivo": str(document_path), "pasos": {}, "error": str(error)})

    args.output.write_text(
        json.dumps(results, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    print(f"Guardado: {args.output}", file=sys.stderr)


if __name__ == "__main__":
    main()

#!/usr/bin/env python3
"""Ejecuta de forma independiente las extracciones 10 y 11 sobre Markdown OCR."""

import argparse
import json
import sys
from pathlib import Path

from document_extraction import extract_json, load_prompt
from extraction_invoice.ask import DEFAULT_MODEL, ask_ollama, load_document_text

ROOT = Path(__file__).resolve().parent
PROMPTS = {
    "10_extraccion_generica": ROOT / "prompts/10-extraction_key_value_generic_prompt.yaml",
    "11_extraccion_factura": ROOT / "prompts/11-extraction_key_value_invoice_prompt.yaml",
}


def find_markdown_files(source: Path):
    if source.is_file():
        return [source]
    return sorted(source.rglob("*.md"))


def run_prompt(prompt_path: Path, document: str, model: str) -> dict:
    prompt = load_prompt(prompt_path)
    user_prompt = prompt["user"].replace("{{documento}}", document)
    messages = [
        {"role": "system", "content": prompt["system"]},
        {"role": "user", "content": user_prompt},
    ]
    try:
        return extract_json(ask_ollama(messages, model))
    except (ValueError, json.JSONDecodeError) as error:
        raise ValueError(f"{prompt_path.name}: {error}") from error


def extract_document(document_path: Path, model: str) -> dict:
    document = load_document_text(document_path)
    steps = {}
    errors = {}

    for step_name, prompt_path in PROMPTS.items():
        try:
            steps[step_name] = run_prompt(prompt_path, document, model)
        except (ValueError, json.JSONDecodeError) as error:
            errors[step_name] = str(error)

    result = {
        "archivo": str(document_path),
        "extracciones": steps,
    }
    if errors:
        result["errores"] = errors
    return result


def main():
    parser = argparse.ArgumentParser(
        description="""
Extrae datos de uno o varios Markdown mediante dos prompts independientes:
  10  extracción genérica key-value
  11  extracción de comprobante/factura

Ambos prompts reciben exactamente el mismo documento. El paso 10 no alimenta
al paso 11 y el paso 11 no alimenta al paso 10.
""",
        epilog="""
Ejemplos:
  python extraction_pipeline.py documento.md
  python extraction_pipeline.py documento.md -o extracciones.json
  python extraction_pipeline.py files/2025-08 -o extracciones_2025_08.json
  python extraction_pipeline.py documento.md -m qwen2.5vl:3b
""",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument(
        "source",
        type=Path,
        help="Ruta de un Markdown o carpeta raíz a recorrer recursivamente",
    )
    parser.add_argument(
        "-m",
        "--model",
        default=DEFAULT_MODEL,
        help=f"Modelo de Ollama (por defecto: {DEFAULT_MODEL})",
    )
    parser.add_argument(
        "-o",
        "--output",
        type=Path,
        default=Path("extraction_results.json"),
        help="JSON de salida (por defecto: extraction_results.json)",
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
        print(f"[{index}/{len(files)}] Extrayendo {document_path}", file=sys.stderr)
        try:
            results.append(extract_document(document_path, args.model))
        except Exception as error:
            results.append({
                "archivo": str(document_path),
                "extracciones": {},
                "errores": {"carga_documento": str(error)},
            })
            print(f"Error en '{document_path}': {error}", file=sys.stderr)

    args.output.write_text(
        json.dumps(results, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    print(f"Guardado: {args.output}", file=sys.stderr)


if __name__ == "__main__":
    main()

#!/usr/bin/env python3
"""Ejecuta de forma independiente las extracciones 10 y 11 sobre Markdown OCR."""

import argparse
import json
import sys
from pathlib import Path

from extraction_invoice.ask import DEFAULT_MODEL, load_document_text
from lib.pipeline import execute_prompt, require_markdown_files, write_checkpoint, write_results

ROOT = Path(__file__).resolve().parent
PROMPTS = {
    "10_extraccion_generica": ROOT / "prompts/10-extraction_key_value_generic_prompt.yaml",
    "11_extraccion_factura": ROOT / "prompts/11-extraction_key_value_invoice_prompt.yaml",
}


def extract_document(document_path: Path, model: str, checkpoint_path: Path | None = None) -> dict:
    document = load_document_text(document_path)
    result = {
        "archivo": str(document_path),
        "extracciones": {},
    }
    if checkpoint_path and checkpoint_path.exists():
        result.update(json.loads(checkpoint_path.read_text(encoding="utf-8")))
    steps = result.setdefault("extracciones", {})
    errors = result.setdefault("errores", {})

    for step_name, prompt_path in PROMPTS.items():
        if step_name in steps:
            continue
        try:
            steps[step_name] = execute_prompt(
                prompt_path,
                {"documento": document},
                model,
            )
            errors.pop(step_name, None)
        except (ValueError, json.JSONDecodeError) as error:
            errors[step_name] = str(error)
        if checkpoint_path:
            if not errors:
                result.pop("errores", None)
            write_checkpoint(checkpoint_path, result)
            result.setdefault("errores", errors)
    if not errors:
        result.pop("errores", None)
    return result


def sidecar_output(document_path: Path) -> Path:
    """Devuelve la ruta del JSON junto al Markdown procesado."""
    return document_path.with_name(f"{document_path.stem}_extraction.json")


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
        help="JSON agregado; sin esta opción crea un JSON junto a cada Markdown",
    )
    args = parser.parse_args()

    files = require_markdown_files(args.source)

    results = []
    for index, document_path in enumerate(files, start=1):
        print(f"[{index}/{len(files)}] Extrayendo {document_path}", file=sys.stderr)
        try:
            results.append(extract_document(
                document_path,
                args.model,
                sidecar_output(document_path),
            ))
        except Exception as error:
            results.append({
                "archivo": str(document_path),
                "extracciones": {},
                "errores": {"carga_documento": str(error)},
            })
            print(f"Error en '{document_path}': {error}", file=sys.stderr)

        if args.output:
            write_results(args.output, results)
    else:
        for result in results:
            output_path = sidecar_output(Path(result["archivo"]))
            write_checkpoint(output_path, result)


if __name__ == "__main__":
    main()

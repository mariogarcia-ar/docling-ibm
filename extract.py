#!/usr/bin/env python3
"""
Aplica un prompt "single-shot" (system/user con placeholder {{documento}}) a
un documento y devuelve el JSON de la respuesta del modelo.

Modos disponibles (atajo -M/--mode) y su template en prompts/:
    kvi   -> extraction_key_value_invoice_prompt.yaml - auditoría de comprobantes (JSON plano)
    kvg   -> extraction_key_value_generic_prompt.yaml - cualquier tipo de documento (JSON plano)

Uso:
    python extract.py archivo.md
    python extract.py archivo.md -M kvi
    python extract.py archivo.pdf -M kvg -m qwen2.5vl:3b -o resultado.json
    python extract.py archivo.md -p prompts/otro_template.yaml   # template custom, sin usar -M

    python extract.py 'files/2025-08/2D2C9343/2991f57d-c143-4b23-9f87-4dfb1214ef53.md'
"""
import argparse
import json
import re
import sys
from pathlib import Path

import yaml

from extraction_invoice.ask import DEFAULT_MODEL, ask_ollama, load_document_text

PROMPTS_DIR = Path(__file__).resolve().parent / "prompts"
DEFAULT_MODE = "kvi"

MODE_PROMPTS = {
    "kvi": "extraction_key_value_invoice_prompt.yaml",
    "kvg": "extraction_key_value_generic_prompt.yaml",
}

DEFAULT_PROMPT = PROMPTS_DIR / MODE_PROMPTS[DEFAULT_MODE]


def load_prompt(prompt_path: Path) -> dict:
    with open(prompt_path, encoding="utf-8") as f:
        return yaml.safe_load(f)


def extract_json(text: str) -> dict:
    """Extrae el primer bloque JSON de la respuesta del modelo."""
    match = re.search(r"\{.*\}", text, re.DOTALL)
    if not match:
        raise ValueError(f"El modelo no devolvió un JSON reconocible:\n{text}")
    return json.loads(match.group(0))


def run_extraction(file_path: Path, prompt_path: Path, model: str) -> dict:
    document_text = load_document_text(file_path)
    prompt = load_prompt(prompt_path)
    user_prompt = prompt["user"].replace("{{documento}}", document_text)
    messages = [
        {"role": "system", "content": prompt["system"]},
        {"role": "user", "content": user_prompt},
    ]
    response = ask_ollama(messages, model)
    return extract_json(response)


def main():
    parser = argparse.ArgumentParser(
        description="Extrae información de un documento aplicando un prompt system/user"
    )
    parser.add_argument("file", type=Path, help="Ruta del archivo a procesar")
    parser.add_argument(
        "-M",
        "--mode",
        choices=sorted(MODE_PROMPTS),
        help=f"Template en prompts/ (default: {DEFAULT_MODE})",
    )
    parser.add_argument(
        "-p",
        "--prompt",
        type=Path,
        help=f"YAML personalizado (default: {DEFAULT_PROMPT.relative_to(Path(__file__).parent)})",
    )
    parser.add_argument(
        "-m",
        "--model",
        default=DEFAULT_MODEL,
        help=f"Modelo de Ollama (default: {DEFAULT_MODEL})",
    )
    parser.add_argument("-o", "--output", type=Path, help="Archivo JSON de salida")
    args = parser.parse_args()

    if args.mode:
        args.prompt = PROMPTS_DIR / MODE_PROMPTS[args.mode]
    elif not args.prompt:
        args.prompt = DEFAULT_PROMPT

    if not args.file.exists():
        print(f"Error: no existe el archivo '{args.file}'", file=sys.stderr)
        sys.exit(1)
    if not args.prompt.exists():
        print(f"Error: no existe el prompt '{args.prompt}'", file=sys.stderr)
        sys.exit(1)

    print(f"Cargando '{args.file}'...", file=sys.stderr)
    print(f"Consultando '{args.model}' con prompt '{args.prompt}'...", file=sys.stderr)
    try:
        result = run_extraction(args.file, args.prompt, args.model)
    except (ValueError, json.JSONDecodeError) as exc:
        print(f"Error al parsear la respuesta del modelo: {exc}", file=sys.stderr)
        sys.exit(1)

    output_json = json.dumps(result, ensure_ascii=False, indent=2)
    if args.output:
        args.output.write_text(output_json, encoding="utf-8")
        print(f"Guardado en '{args.output}'", file=sys.stderr)
    else:
        print(output_json)


if __name__ == "__main__":
    main()

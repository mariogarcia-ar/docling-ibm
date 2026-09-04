#!/usr/bin/env python3
"""
Aplica extraction_key_value_prompt.yaml (system/user) a un documento y
devuelve un JSON plano (key-value, sin objetos anidados) del comprobante.

Uso:
    python extract_key_value_invoice.py archivo.md
    python extract_key_value_invoice.py archivo.pdf -m qwen2.5vl:3b -o resultado.json
"""
import argparse
import json
import re
import sys
from pathlib import Path

import yaml

from ask import DEFAULT_MODEL, ask_ollama, load_document_text

DEFAULT_PROMPT = Path(__file__).parent / "prompts/extraction_key_value_prompt.yaml"


def load_prompt(prompt_path: Path) -> dict:
    with open(prompt_path, encoding="utf-8") as f:
        return yaml.safe_load(f)


def extract_json(text: str) -> dict:
    """Extrae el primer bloque JSON de la respuesta del modelo, tolerando
    fences de markdown (```json ... ```) o texto extra alrededor."""
    match = re.search(r"\{.*\}", text, re.DOTALL)
    if not match:
        raise ValueError(f"El modelo no devolvió un JSON reconocible:\n{text}")
    return json.loads(match.group(0))


def main():
    parser = argparse.ArgumentParser(description="Extrae datos de un comprobante en formato key-value usando extraction_key_value_prompt.yaml")
    parser.add_argument("file", type=Path, help="Ruta del archivo (documento con OCR) a procesar")
    parser.add_argument("-p", "--prompt", type=Path, default=DEFAULT_PROMPT, help=f"YAML con system/user (default: {DEFAULT_PROMPT.name})")
    parser.add_argument("-m", "--model", default=DEFAULT_MODEL, help=f"Modelo de Ollama a usar (default: {DEFAULT_MODEL})")
    parser.add_argument("-o", "--output", type=Path, help="Archivo donde guardar el JSON (default: stdout)")
    args = parser.parse_args()

    if not args.file.exists():
        print(f"Error: no existe el archivo '{args.file}'", file=sys.stderr)
        sys.exit(1)
    if not args.prompt.exists():
        print(f"Error: no existe el prompt '{args.prompt}'", file=sys.stderr)
        sys.exit(1)

    print(f"Cargando '{args.file}'...", file=sys.stderr)
    document_text = load_document_text(args.file)

    prompt = load_prompt(args.prompt)
    system_prompt = prompt["system"]
    user_prompt = prompt["user"].replace("{{documento}}", document_text)

    messages = [
        {"role": "system", "content": system_prompt},
        {"role": "user", "content": user_prompt},
    ]

    print(f"Consultando '{args.model}'...", file=sys.stderr)
    respuesta = ask_ollama(messages, args.model)

    try:
        resultado = extract_json(respuesta)
    except (ValueError, json.JSONDecodeError) as exc:
        print(f"Error al parsear la respuesta del modelo: {exc}", file=sys.stderr)
        sys.exit(1)

    output_json = json.dumps(resultado, ensure_ascii=False, indent=2)
    if args.output:
        args.output.write_text(output_json, encoding="utf-8")
        print(f"Guardado en '{args.output}'", file=sys.stderr)
    else:
        print(output_json)


if __name__ == "__main__":
    main()

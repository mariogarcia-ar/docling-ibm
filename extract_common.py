#!/usr/bin/env python3
"""
Lógica compartida por los scripts extract_*.py de extracción "single-shot"
(un solo prompt system/user con placeholder {{documento}}, la respuesta debe
ser un JSON). Cada script solo define su template default y la descripción.
"""
import argparse
import json
import re
import sys
from pathlib import Path

import yaml

from ask import DEFAULT_MODEL, ask_ollama, load_document_text


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


def run_extraction(file_path: Path, prompt_path: Path, model: str) -> dict:
    document_text = load_document_text(file_path)
    prompt = load_prompt(prompt_path)
    user_prompt = prompt["user"].replace("{{documento}}", document_text)
    messages = [
        {"role": "system", "content": prompt["system"]},
        {"role": "user", "content": user_prompt},
    ]
    respuesta = ask_ollama(messages, model)
    return extract_json(respuesta)


def main(default_prompt: Path, description: str):
    parser = argparse.ArgumentParser(description=description)
    parser.add_argument("file", type=Path, help="Ruta del archivo (documento con OCR) a procesar")
    parser.add_argument("-p", "--prompt", type=Path, default=default_prompt, help=f"YAML con system/user (default: {default_prompt.name})")
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
    print(f"Consultando '{args.model}'...", file=sys.stderr)
    try:
        resultado = run_extraction(args.file, args.prompt, args.model)
    except (ValueError, json.JSONDecodeError) as exc:
        print(f"Error al parsear la respuesta del modelo: {exc}", file=sys.stderr)
        sys.exit(1)

    output_json = json.dumps(resultado, ensure_ascii=False, indent=2)
    if args.output:
        args.output.write_text(output_json, encoding="utf-8")
        print(f"Guardado en '{args.output}'", file=sys.stderr)
    else:
        print(output_json)

#!/usr/bin/env python3
"""
Aplica las preguntas de questions.yaml a un archivo y devuelve el resultado en JSON.

Es un atajo sobre ask.py con --fields, pensado para uso en pipelines: por
default no imprime nada más que el JSON final (los logs van a stderr).

Uso:
    python extract_template.py archivo.md
    python extract_template.py archivo.pdf -o resultado.json
    python extract_template.py archivo.md -t otro_template.yaml -m qwen2.5vl:3b
"""
import argparse
import json
import sys
from pathlib import Path

from ask import DEFAULT_MODEL, load_document_text, load_fields, run_fields_extraction

DEFAULT_TEMPLATE = Path(__file__).parent / "questions.yaml"


def main():
    parser = argparse.ArgumentParser(description="Extrae campos de un archivo según un template YAML, usando Ollama")
    parser.add_argument("file", type=Path, help="Ruta del archivo a procesar")
    parser.add_argument("-t", "--template", type=Path, default=DEFAULT_TEMPLATE, help=f"YAML con los campos a extraer (default: {DEFAULT_TEMPLATE.name})")
    parser.add_argument("-m", "--model", default=DEFAULT_MODEL, help=f"Modelo de Ollama a usar (default: {DEFAULT_MODEL})")
    parser.add_argument("-o", "--output", type=Path, help="Archivo donde guardar el JSON (default: stdout)")
    args = parser.parse_args()

    if not args.file.exists():
        print(f"Error: no existe el archivo '{args.file}'", file=sys.stderr)
        sys.exit(1)
    if not args.template.exists():
        print(f"Error: no existe el template '{args.template}'", file=sys.stderr)
        sys.exit(1)

    print(f"Cargando '{args.file}' con template '{args.template}'...", file=sys.stderr)
    document_text = load_document_text(args.file)
    fields = load_fields(args.template)

    system_prompt = (
        "Sos un asistente que responde preguntas basándote ÚNICAMENTE en el "
        "contenido del siguiente documento. Si la respuesta no está en el "
        "documento, decilo claramente.\n\n"
        f"--- DOCUMENTO ---\n{document_text}\n--- FIN DEL DOCUMENTO ---"
    )
    messages = [{"role": "system", "content": system_prompt}]

    # los logs por campo de run_fields_extraction van a stdout; los redirigimos a stderr
    real_stdout = sys.stdout
    sys.stdout = sys.stderr
    try:
        results = run_fields_extraction(messages, args.model, fields)
    finally:
        sys.stdout = real_stdout

    output_json = json.dumps(results, ensure_ascii=False, indent=2)
    if args.output:
        args.output.write_text(output_json, encoding="utf-8")
        print(f"Guardado en '{args.output}'", file=sys.stderr)
    else:
        print(output_json)


if __name__ == "__main__":
    main()

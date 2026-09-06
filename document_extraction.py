#!/usr/bin/env python3
"""
Aplica un prompt "single-shot" (system/user con placeholder {{documento}}) a
un documento y devuelve el JSON de la respuesta del modelo.

Modos disponibles (atajo -M/--mode) y su template en prompts/:
    kvi   -> extraction_key_value_invoice_prompt.yaml - auditoría de comprobantes (JSON plano)
    11.1  -> facturacion/11.1-deteccion_tipo_factura.yaml - tipo y letra del comprobante
    kvg   -> extraction_key_value_generic_prompt.yaml - cualquier tipo de documento (JSON plano)
    ccc   -> 01-clasificacion_centro_costo_prompt.yaml - hasta tres centros de costo
    mcc   -> 02-clasificacion_macro_categoria_prompt.yaml - macro categoría
    cfc   -> 03-clasificacion_concepto_codigo_final_prompt.yaml - concepto y código final

Uso:
    python document_extraction.py archivo.md
    python document_extraction.py archivo.md -M kvi
    python document_extraction.py archivo.pdf -M kvg -m qwen2.5vl:3b -o resultado.json
    python document_extraction.py archivo.md -p prompts/otro_template.yaml   # template custom, sin usar -M

    python document_extraction.py 'files/2025-08/2D2C9343/2991f57d-c143-4b23-9f87-4dfb1214ef53.md'
"""
import argparse
import base64
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
    "11.1": "facturacion/11.1-deteccion_tipo_factura.yaml",
    "kvg": "extraction_key_value_generic_prompt.yaml",
    "ccc": "01-clasificacion_centro_costo_prompt.yaml",
    "mcc": "02-clasificacion_macro_categoria_prompt.yaml",
    "cfc": "03-clasificacion_concepto_codigo_final_prompt.yaml",
    "01": "01-clasificacion_centro_costo_prompt.yaml",
    "02": "02-clasificacion_macro_categoria_prompt.yaml",
    "03": "03-clasificacion_concepto_codigo_final_prompt.yaml",
}

DEFAULT_PROMPT = PROMPTS_DIR / MODE_PROMPTS[DEFAULT_MODE]
IMAGE_EXTENSIONS = {".jpg", ".jpeg", ".png", ".webp", ".tif", ".tiff", ".bmp"}


def load_prompt(prompt_path: Path) -> dict:
    with open(prompt_path, encoding="utf-8") as f:
        return yaml.safe_load(f)


def is_image_file(file_path: Path) -> bool:
    return file_path.suffix.lower() in IMAGE_EXTENSIONS


def extract_json(text: str) -> dict:
    """Extrae el primer bloque JSON de la respuesta del modelo."""
    match = re.search(r"\{.*\}", text, re.DOTALL)
    if not match:
        raise ValueError(
            "El modelo no devolvió un JSON completo. Respuesta recibida:\n"
            f"{text}"
        )
    json_text = match.group(0)
    # Algunos modelos insertan un token aislado antes de cerrar el último campo.
    json_text = re.sub(r'"\s*v"\s*(?=\n\s*})', '"', json_text)
    return json.loads(json_text)


def run_extraction(file_path: Path, prompt_path: Path, model: str) -> dict:
    prompt = load_prompt(prompt_path)
    use_vlm = is_image_file(file_path) and "system_vlm" in prompt and "user_vlm" in prompt
    system_key = "system_vlm" if use_vlm else "system_llm"
    user_key = "user_vlm" if use_vlm else "user"
    document_text = "" if use_vlm else load_document_text(file_path)
    user_prompt = prompt[user_key].replace("{{documento}}", document_text)
    system_prompt = f'{prompt["system"]}\n\n{prompt[system_key]}'
    messages = [{"role": "system", "content": system_prompt}]
    user_message = {"role": "user", "content": user_prompt}
    if use_vlm:
        user_message["images"] = [
            base64.b64encode(file_path.read_bytes()).decode("ascii")
        ]
    messages.append(user_message)
    response = ask_ollama(
        messages,
        model,
        json_format=True,
        options={"num_ctx": 8192} if use_vlm else None,
    )
    return extract_json(response)


def run_classification(
    file_path: Path,
    prompt_path: Path,
    model: str,
    values: dict[str, str],
) -> dict:
    document_text = load_document_text(file_path)
    prompt = load_prompt(prompt_path)
    user_prompt = prompt["user"]
    replacements = {
        "proveedor": "no informado",
        "descripcion": document_text,
        "monto": "no informado",
        **values,
    }
    for key, value in replacements.items():
        user_prompt = user_prompt.replace(f"{{{{{key}}}}}", value)
    messages = [
        {"role": "system", "content": prompt["system"]},
        {"role": "user", "content": user_prompt},
    ]
    return extract_json(ask_ollama(messages, model))


def iter_markdown_files(path: Path):
    if path.is_file():
        yield path
        return
    yield from sorted(path.rglob("*.md"))


def main():
    parser = argparse.ArgumentParser(
        description="Extrae información de un documento aplicando un prompt system/user"
    )
    parser.add_argument("file", type=Path, help="Ruta de un Markdown o carpeta con Markdowns")
    parser.add_argument(
        "-M",
        "--mode",
        choices=sorted(MODE_PROMPTS),
        help=f"Template en prompts/ (default: {DEFAULT_MODE})",
    )
    parser.add_argument(
        "--centro-costo",
        help="Código de centro de costo ya asignado para los modos 02/mcc y 03/cfc",
    )
    parser.add_argument(
        "--macro-categoria",
        help="Código de macro categoría ya asignado para el modo 03/cfc",
    )
    parser.add_argument(
        "--condicion-impositiva",
        default="no informada",
        help="Condición impositiva para el modo 03/cfc",
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

    print(f"Consultando '{args.model}' con prompt '{args.prompt}'...", file=sys.stderr)
    classification_modes = {"ccc", "mcc", "cfc", "01", "02", "03"}
    is_classification = args.mode in classification_modes
    files = list(iter_markdown_files(args.file)) if is_classification else [args.file]
    if not files:
        print(f"No se encontraron archivos Markdown en '{args.file}'", file=sys.stderr)
        sys.exit(1)

    results = {}
    for document_file in files:
        print(f"Cargando '{document_file}'...", file=sys.stderr)
        try:
            if is_classification:
                if args.mode in {"mcc", "02"} and not args.centro_costo:
                    raise ValueError("el modo 02/mcc requiere --centro-costo")
                if args.mode in {"cfc", "03"} and not args.macro_categoria:
                    raise ValueError("el modo 03/cfc requiere --macro-categoria")
                values = {
                    "centro_costo": args.centro_costo or "no informado",
                    "macro_categoria": args.macro_categoria or "no informada",
                    "condicion_impositiva": args.condicion_impositiva,
                }
                result = run_classification(
                    document_file,
                    args.prompt,
                    args.model,
                    values,
                )
            else:
                result = run_extraction(document_file, args.prompt, args.model)
        except (ValueError, json.JSONDecodeError) as exc:
            print(f"Error al parsear '{document_file}': {exc}", file=sys.stderr)
            sys.exit(1)
        results[str(document_file)] = result

    output = results if len(results) > 1 else next(iter(results.values()))
    output_json = json.dumps(output, ensure_ascii=False, indent=2)
    if args.output:
        args.output.write_text(output_json, encoding="utf-8")
        print(f"Guardado en '{args.output}'", file=sys.stderr)
    else:
        print(output_json)


if __name__ == "__main__":
    main()

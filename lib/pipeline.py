import json
import sys
from pathlib import Path
from tempfile import NamedTemporaryFile

from document_extraction import extract_json, load_prompt
from extraction_invoice.ask import ask_ollama


def find_markdown_files(source: Path):
    if source.is_file():
        return [source]
    return sorted(source.rglob("*.md"))


def require_markdown_files(source: Path):
    if not source.exists():
        print(f"Error: no existe '{source}'", file=sys.stderr)
        raise SystemExit(1)

    files = find_markdown_files(source)
    if not files:
        print(f"No se encontraron archivos Markdown en '{source}'", file=sys.stderr)
        raise SystemExit(1)
    return files


def write_results(output: Path, results):
    output.parent.mkdir(parents=True, exist_ok=True)
    with NamedTemporaryFile(
        mode="w",
        encoding="utf-8",
        dir=output.parent,
        prefix=f".{output.name}.",
        delete=False,
    ) as temporary:
        temporary.write(json.dumps(results, ensure_ascii=False, indent=2))
        temporary_path = Path(temporary.name)
    temporary_path.replace(output)
    print(f"Guardado: {output}", file=sys.stderr)


def write_checkpoint(output: Path, result: dict):
    """Guarda un resultado parcial de forma atómica."""
    write_results(output, result)


def execute_prompt(prompt_path: Path, values: dict[str, str], model: str) -> dict:
    """Construye y ejecuta un prompt YAML con placeholders dinámicos."""
    prompt = load_prompt(prompt_path)
    user_prompt = prompt["user"]
    for key, value in values.items():
        user_prompt = user_prompt.replace(f"{{{{{key}}}}}", value)

    messages = [
        {"role": "system", "content": prompt["system"]},
        {"role": "user", "content": user_prompt},
    ]
    try:
        return extract_json(ask_ollama(messages, model))
    except (ValueError, json.JSONDecodeError) as error:
        raise ValueError(f"{prompt_path.name}: {error}") from error

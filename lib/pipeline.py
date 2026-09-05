import json
import sys
from pathlib import Path


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
    output.write_text(
        json.dumps(results, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    print(f"Guardado: {output}", file=sys.stderr)

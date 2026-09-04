#!/usr/bin/env python3
"""
Hace preguntas sobre un archivo usando un modelo local de Ollama.

Uso:
    python ask.py archivo.md
    python ask.py archivo.pdf --model qwen2.5vl:3b
    python ask.py archivo.md -q "¿De qué trata el documento?"
"""
import argparse
import json
import sys
import urllib.error
import urllib.request
from pathlib import Path

OLLAMA_URL = "http://localhost:11434/api/chat"
DEFAULT_MODEL = "qwen2.5vl:3b"

CONVERTIBLE_EXTENSIONS = {".pdf", ".jpg", ".jpeg", ".png", ".tif", ".tiff", ".bmp", ".docx", ".pptx", ".xlsx"}


def load_document_text(file_path: Path) -> str:
    """Devuelve el contenido del archivo como texto/markdown.

    Si el archivo ya es texto (.md, .txt) se lee directamente.
    Si es un formato convertible (pdf, imagen, etc.) se usa Docling
    para extraer el markdown, tal como en process_recursive.py.
    """
    suffix = file_path.suffix.lower()

    if suffix in {".md", ".txt"}:
        return file_path.read_text(encoding="utf-8")

    if suffix in CONVERTIBLE_EXTENSIONS:
        from docling.document_converter import DocumentConverter, ImageFormatOption
        from docling.datamodel.base_models import InputFormat
        from docling.datamodel.pipeline_options import PdfPipelineOptions

        pipeline_options = PdfPipelineOptions()
        pipeline_options.ocr_options.force_full_page_ocr = True
        converter = DocumentConverter(
            format_options={
                InputFormat.IMAGE: ImageFormatOption(pipeline_options=pipeline_options),
            }
        )
        result = converter.convert(str(file_path))
        return result.document.export_to_markdown()

    raise ValueError(f"Extensión no soportada: {suffix}")


def ask_ollama(messages: list[dict], model: str) -> str:
    payload = json.dumps({"model": model, "messages": messages, "stream": False}).encode("utf-8")
    request = urllib.request.Request(
        OLLAMA_URL, data=payload, headers={"Content-Type": "application/json"}
    )
    try:
        with urllib.request.urlopen(request) as response:
            body = json.loads(response.read().decode("utf-8"))
    except urllib.error.URLError as exc:
        raise RuntimeError(
            "No se pudo conectar con Ollama en http://localhost:11434. "
            "¿Está corriendo el servicio? ('ollama serve')"
        ) from exc
    return body["message"]["content"]


def main():
    parser = argparse.ArgumentParser(description="Responde preguntas sobre un archivo usando Ollama")
    parser.add_argument("file", type=Path, help="Ruta del archivo a consultar")
    parser.add_argument("-m", "--model", default=DEFAULT_MODEL, help=f"Modelo de Ollama a usar (default: {DEFAULT_MODEL})")
    parser.add_argument("-q", "--question", help="Pregunta puntual. Si se omite, entra en modo interactivo")
    args = parser.parse_args()

    if not args.file.exists():
        print(f"Error: no existe el archivo '{args.file}'", file=sys.stderr)
        sys.exit(1)

    print(f"Cargando '{args.file}'...")
    try:
        document_text = load_document_text(args.file)
    except Exception as exc:
        print(f"Error al cargar el archivo: {exc}", file=sys.stderr)
        sys.exit(1)

    system_prompt = (
        "Sos un asistente que responde preguntas basándote ÚNICAMENTE en el "
        "contenido del siguiente documento. Si la respuesta no está en el "
        "documento, decilo claramente.\n\n"
        f"--- DOCUMENTO ---\n{document_text}\n--- FIN DEL DOCUMENTO ---"
    )
    messages = [{"role": "system", "content": system_prompt}]

    if args.question:
        messages.append({"role": "user", "content": args.question})
        print(ask_ollama(messages, args.model))
        return

    print(f"Modo interactivo con '{args.model}'. Escribí 'salir' para terminar.\n")
    while True:
        try:
            question = input("Pregunta> ").strip()
        except (EOFError, KeyboardInterrupt):
            print()
            break
        if not question or question.lower() in {"salir", "exit", "quit"}:
            break

        messages.append({"role": "user", "content": question})
        try:
            answer = ask_ollama(messages, args.model)
        except RuntimeError as exc:
            print(f"Error: {exc}", file=sys.stderr)
            break
        print(f"\n{answer}\n")
        messages.append({"role": "assistant", "content": answer})


if __name__ == "__main__":
    main()

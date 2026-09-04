#!/usr/bin/env python3
"""
Aplica prompts/extraction_prompt.yaml (system/user) a un documento y devuelve
el JSON de auditoría de comprobantes definido en el prompt.

Uso:
    python extract_invoice.py archivo.md
    python extract_invoice.py archivo.pdf -m qwen2.5vl:3b -o resultado.json
"""
from pathlib import Path

from extract_common import main

DEFAULT_PROMPT = Path(__file__).parent / "prompts" / "extraction_prompt.yaml"

if __name__ == "__main__":
    main(DEFAULT_PROMPT, "Extrae datos de un comprobante usando extraction_prompt.yaml")

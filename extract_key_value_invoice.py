#!/usr/bin/env python3
"""
Aplica prompts/extraction_key_value_prompt.yaml (system/user) a un documento
y devuelve un JSON plano (key-value, sin objetos anidados) del comprobante.

Uso:
    python extract_key_value_invoice.py archivo.md
    python extract_key_value_invoice.py archivo.pdf -m qwen2.5vl:3b -o resultado.json
"""
from pathlib import Path

from extract_common import main

DEFAULT_PROMPT = Path(__file__).parent / "prompts" / "extraction_key_value_prompt.yaml"

if __name__ == "__main__":
    main(DEFAULT_PROMPT, "Extrae datos de un comprobante en formato key-value usando extraction_key_value_prompt.yaml")

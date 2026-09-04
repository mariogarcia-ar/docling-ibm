#!/usr/bin/env python3
"""
Aplica prompts/extraction_key_value_generic_prompt.yaml a un documento de
tipo desconocido y devuelve un JSON plano con la información disponible, sin
asumir un schema fijo de factura/comprobante.

Uso:
    python extract_key_value_generic.py archivo.md
    python extract_key_value_generic.py archivo.pdf -m qwen2.5vl:3b -o resultado.json
"""
from pathlib import Path

from extract_common import main

DEFAULT_PROMPT = Path(__file__).parent / "prompts" / "extraction_key_value_generic_prompt.yaml"

if __name__ == "__main__":
    main(DEFAULT_PROMPT, "Extrae información key-value de un documento de cualquier tipo")

from pathlib import Path

import pymupdf4llm

# Raíz de v2/ (derivada del propio script → no depende del CWD).
_RAIZ = Path(__file__).resolve().parents[1]

archivos = [
    _RAIZ / "tests/fixtures/golden/9dfc597f-34c5-41ec-99ae-cf35544c7af8.pdf",
    _RAIZ / "tests/fixtures/golden/66e6e0ea-e910-41f4-9037-13f0309812c1.jpg",
]

for file in archivos:
    print(f"\n{'=' * 60}\nARCHIVO: {file.name}\n{'=' * 60}")
    # Convierte el PDF/imagen a Markdown respetando columnas y maquetación.
    md_text = pymupdf4llm.to_markdown(str(file))
    print(md_text)
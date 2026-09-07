from pathlib import Path

import pymupdf4llm

# Raíz de v2/ (derivada del propio script → no depende del CWD).
_RAIZ = Path(__file__).resolve().parents[1]

#: Extensiones de imagen: nunca traen capa de texto → OCR forzado (si no,
#: pymupdf4llm no ejecuta el OCR sobre imagen directa y devuelve Markdown vacío).
_EXT_IMAGEN = frozenset({".jpg", ".jpeg", ".png", ".tif", ".tiff", ".bmp"})

archivos = [
    _RAIZ / "tests/fixtures/golden/9dfc597f-34c5-41ec-99ae-cf35544c7af8.pdf",
    _RAIZ / "tests/fixtures/golden/66e6e0ea-e910-41f4-9037-13f0309812c1.jpg",
]

for file in archivos:
    print(f"\n{'=' * 60}\nARCHIVO: {file.name}\n{'=' * 60}")
    # Convierte el PDF/imagen a Markdown respetando columnas y maquetación.
    # Las imágenes directas requieren force_ocr (pymupdf4llm no decide OCR solo).
    kwargs = {"force_ocr": True} if file.suffix.lower() in _EXT_IMAGEN else {}
    md_text = pymupdf4llm.to_markdown(str(file), **kwargs)
    print(md_text)
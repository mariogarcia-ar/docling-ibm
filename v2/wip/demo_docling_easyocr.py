"""Demo de conversión a Markdown con Docling usando EasyOCR como motor OCR.

Igual que ``demo_docling.py`` (misma config de ``v1/lib/converter.py``), pero
cambiando el motor OCR por defecto (RapidOCR) por **EasyOCR** — recomendado en
PROC.md §5.4 para facturas en español/inglés cuando RapidOCR falla en local.

Se mapea tanto PDF como imagen al pipeline estándar con ``EasyOcrOptions``
(``force_full_page_ocr=True``), para que EasyOCR aplique también sobre el PDF.

Notas:
    - Docling descarga sus modelos la primera corrida (lento la 1ra vez).
    - EasyOCR descarga a su vez los modelos de detección/reconocimiento de
      texto (``~64 MB`` en ``~/.EasyOCR``) en la primera corrida.
    - Requiere tener instalado ``easyocr`` en el entorno (puramente Python).
    - EasyOCR usa códigos ISO de idioma: ``es`` (español) y ``en`` (inglés) —
      NO los códigos de Tesseract ``spa``/``eng`` del ejemplo de PROC.md.

Uso:
    python wip/demo_docling_easyocr.py
"""

from __future__ import annotations

from pathlib import Path

from docling.datamodel.base_models import InputFormat
from docling.datamodel.pipeline_options import (
    EasyOcrOptions,
    PdfPipelineOptions,
)
from docling.document_converter import (
    DocumentConverter,
    ImageFormatOption,
    PdfFormatOption,
)

# Raíz de v2/ (derivada del propio script → no depende del CWD).
_RAIZ = Path(__file__).resolve().parents[1]

archivos = [
    _RAIZ / "tests/fixtures/golden/9dfc597f-34c5-41ec-99ae-cf35544c7af8.pdf",
    _RAIZ / "tests/fixtures/golden/66e6e0ea-e910-41f4-9037-13f0309812c1.jpg",
]


def setup_converter_easyocr() -> DocumentConverter:
    """Inicializa Docling con OCR de página completa usando el motor EasyOCR.

    Configura idiomas español/inglés (los de las facturas) y fuerza el OCR
    completo de página para no saltarse el procesamiento por texto basura.

    Nota: los códigos de idioma son los de EasyOCR (``es``/``en``), no los de
    Tesseract (``spa``/``eng``).
    """
    pipeline_options = PdfPipelineOptions()
    pipeline_options.do_table_structure = True
    pipeline_options.ocr_options = EasyOcrOptions(
        lang=["es", "en"],            # idiomas de las facturas (EasyOCR)
        force_full_page_ocr=True,     # evita saltarse el OCR por texto basura
    )

    # Mismo pipeline estándar para PDF (backend docling-parse) e imagen.
    return DocumentConverter(
        format_options={
            InputFormat.PDF: PdfFormatOption(
                pipeline_options=pipeline_options,
            ),
            InputFormat.IMAGE: ImageFormatOption(
                pipeline_options=pipeline_options,
            ),
        }
    )


converter = setup_converter_easyocr()

for archivo in archivos:
    print(f"\n{'=' * 60}\nARCHIVO: {archivo.name}\n{'=' * 60}")
    resultado = converter.convert(str(archivo))
    markdown = resultado.document.export_to_markdown()
    print(markdown)

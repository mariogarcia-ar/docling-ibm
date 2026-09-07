"""Demo de conversión a Markdown con Docling usando RapidOCR como motor OCR.

Igual que ``demo_docling.py`` (misma config de ``v1/lib/converter.py``), pero
fijando explícitamente el motor OCR **RapidOCR** (el que Docling usa por
defecto) con ``RapidOcrOptions``, en vez de dejarlo en ``OcrAutoOptions``.

Se mapea tanto PDF como imagen al pipeline estándar con ``RapidOcrOptions``
(``force_full_page_ocr=True``), para que RapidOCR aplique también sobre el PDF.

Notas:
    - Docling descarga sus modelos la primera corrida (lento la 1ra vez).
    - RapidOCR descarga a su vez sus modelos (detección/rec) desde ModelScope
      en la primera corrida.
    - RapidOCR no trae un modelo dedicado por idioma: Docling mapea los códigos
      a un set de modelos — ``es``/``en`` usan el set **latin** (cubre español
      e inglés). El set por defecto es ``chinese``, que en texto latino pierde
      los espacios entre palabras (issues #2887/#1635/#2927 de docling).
    - ``rapidocr`` debe estar instalado en el entorno (puramente Python).

Uso:
    python wip/demo_docling_rapidocr.py
"""

from __future__ import annotations

from pathlib import Path

from docling.datamodel.base_models import InputFormat
from docling.datamodel.pipeline_options import (
    PdfPipelineOptions,
    RapidOcrOptions,
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


def setup_converter_rapidocr() -> DocumentConverter:
    """Inicializa Docling con OCR de página completa usando el motor RapidOCR.

    Configura idiomas español/inglés (los de las facturas) y fuerza el OCR
    completo de página para no saltarse el procesamiento por texto basura.

    Nota: ``lang=["es", "en"]`` resuelve al set de modelos ``latin`` de
    RapidOCR (cubre español e inglés sin perder espacios entre palabras).
    """
    pipeline_options = PdfPipelineOptions()
    pipeline_options.do_table_structure = True
    pipeline_options.ocr_options = RapidOcrOptions(
        lang=["es", "en"],            # → set latin de RapidOCR (es/en)
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


converter = setup_converter_rapidocr()

for archivo in archivos:
    print(f"\n{'=' * 60}\nARCHIVO: {archivo.name}\n{'=' * 60}")
    resultado = converter.convert(str(archivo))
    markdown = resultado.document.export_to_markdown()
    print(markdown)

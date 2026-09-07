"""Demo de conversión a Markdown con Docling puro (sin el adaptador del proyecto).

Usa directamente ``docling.document_converter.DocumentConverter`` con OCR de
página completa (misma config que ``v1/lib/converter.py``), sobre archivos
locales del golden set (PDF e imagen), y muestra el Markdown exportado — igual
que ``demo_pymupdf4llm.py`` y ``demo_xberg.py`` para poder comparar librerías.

Nota: Docling descarga modelos en la primera corrida (lento la 1ra vez).

Uso:
    python wip/demo_docling.py
"""

from __future__ import annotations

from pathlib import Path

from docling.datamodel.base_models import InputFormat
from docling.datamodel.pipeline_options import PdfPipelineOptions
from docling.document_converter import DocumentConverter, ImageFormatOption

# Raíz de v2/ (derivada del propio script → no depende del CWD).
_RAIZ = Path(__file__).resolve().parents[1]

archivos = [
    _RAIZ / "tests/fixtures/golden/9dfc597f-34c5-41ec-99ae-cf35544c7af8.pdf",
    _RAIZ / "tests/fixtures/golden/66e6e0ea-e910-41f4-9037-13f0309812c1.jpg",
]


def setup_converter() -> DocumentConverter:
    """Inicializa Docling con OCR de página completa (config de v1)."""
    pipeline_options = PdfPipelineOptions()
    pipeline_options.ocr_options.force_full_page_ocr = True
    pipeline_options.do_table_structure = True
    return DocumentConverter(
        format_options={
            InputFormat.IMAGE: ImageFormatOption(pipeline_options=pipeline_options),
        }
    )


converter = setup_converter()

for archivo in archivos:
    print(f"\n{'=' * 60}\nARCHIVO: {archivo.name}\n{'=' * 60}")
    resultado = converter.convert(str(archivo))
    markdown = resultado.document.export_to_markdown()
    print(markdown)

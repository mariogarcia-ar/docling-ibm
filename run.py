

from pathlib import Path

from docling.document_converter import DocumentConverter, ImageFormatOption
from docling.datamodel.base_models import InputFormat
from docling.datamodel.pipeline_options import PdfPipelineOptions

# 1. Initialize the correct pipeline options
pipeline_options = PdfPipelineOptions()

# 2. Correct parameter: Force OCR across all pages (even programmatic ones)
pipeline_options.ocr_options.force_full_page_ocr = True 
pipeline_options.do_table_structure = True


# 3. Initialize converter with custom options using format_options
converter = DocumentConverter(
    format_options={
        InputFormat.IMAGE: ImageFormatOption(pipeline_options=pipeline_options),
    }
)

# 4. Convert your document
img = 'files/2025-08/2D2C9343/2991f57d-c143-4b23-9f87-4dfb1214ef53.jpg'
img = 'files/2025-08/2E1F7D6C/deddaca4-049e-4a9e-be19-9da7ddde3ec6.jpg'
result = converter.convert(img)
markdown_content = result.document.export_to_markdown()

# 5. Guardar el resultado junto a la imagen original
output_path = Path(img).with_suffix('.md')
output_path.write_text(markdown_content, encoding='utf-8')
print(f"Archivo guardado en: {output_path}")
print(markdown_content)



from docling.document_converter import DocumentConverter, ImageFormatOption
from docling.datamodel.base_models import InputFormat
from docling.datamodel.pipeline_options import PdfPipelineOptions

# 1. Initialize the correct pipeline options
pipeline_options = PdfPipelineOptions()

# 2. Correct parameter: Force OCR across all pages (even programmatic ones)
pipeline_options.ocr_options.force_full_page_ocr = True 

# 3. Initialize converter with custom options using format_options
converter = DocumentConverter(
    format_options={
        InputFormat.IMAGE: ImageFormatOption(pipeline_options=pipeline_options),
    }
)

# 4. Convert your document
# result = converter.convert("id.jpeg")
result = converter.convert("ticket.jpg")

# 5. Export to structured markdown format
print(result.document.export_to_markdown())

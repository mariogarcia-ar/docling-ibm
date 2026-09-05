

from pathlib import Path

from docling.document_converter import DocumentConverter, ImageFormatOption
from docling.datamodel.base_models import InputFormat
from docling.datamodel.pipeline_options import PdfPipelineOptions


pipeline_options = PdfPipelineOptions()
pipeline_options.ocr_options.force_full_page_ocr = True
pipeline_options.do_table_structure = True

converter = DocumentConverter(
    format_options={
        InputFormat.IMAGE: ImageFormatOption(pipeline_options=pipeline_options),
    }
)

img = 'files/2025-08/2E1F7D6C/deddaca4-049e-4a9e-be19-9da7ddde3ec6.jpg'
result = converter.convert(img)
doc = result.document

# Filtro por orientación dominante para evitar mezclar comprobantes
items_with_orientation = []
for item, _ in doc.iterate_items():
    if not getattr(item, 'prov', None):
        continue
    for prov in item.prov:
        bbox = prov.bbox
        width = abs(bbox.r - bbox.l)
        height = abs(bbox.t - bbox.b)
        orientation = 'horizontal' if width >= height else 'vertical'
        items_with_orientation.append((item, orientation, prov))

if items_with_orientation:
    horizontal_count = sum(1 for _, orientation, _ in items_with_orientation if orientation == 'horizontal')
    vertical_count = sum(1 for _, orientation, _ in items_with_orientation if orientation == 'vertical')
    dominant_orientation = 'horizontal' if horizontal_count >= vertical_count else 'vertical'
else:
    dominant_orientation = 'horizontal'

filtered_items = []
for item, orientation, prov in items_with_orientation:
    if orientation == dominant_orientation:
        filtered_items.append((item, orientation, prov))

# Se conserva el comportamiento original del script: guardar markdown junto a la imagen
output_path = Path(img).with_suffix('.md')
output_path.write_text(doc.export_to_markdown(), encoding='utf-8')

print(f"Orientación dominante: {dominant_orientation}")
print(f"Se muestran solo los bloques con esa orientación.")
print(f"Archivo guardado en: {output_path}")

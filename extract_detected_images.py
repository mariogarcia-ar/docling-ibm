from pathlib import Path

from PIL import Image

from docling.document_converter import DocumentConverter, ImageFormatOption
from docling.datamodel.base_models import InputFormat
from docling.datamodel.pipeline_options import PdfPipelineOptions


def extract_detected_images(image_path: str | Path, output_dir: str | Path | None = None):
    """Guarda las imágenes detectadas por Docling usando los bounding boxes del documento.

    Nota: en este documento concreto, `picture.image` aparece en `None`, así que la
    extracción real se hace recortando la imagen original con las coordenadas `bbox`
    que Docling guarda en `prov`.
    """
    image_path = Path(image_path)
    if output_dir is None:
        output_dir = image_path.parent
    output_dir = Path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)

    pipeline_options = PdfPipelineOptions()
    pipeline_options.ocr_options.force_full_page_ocr = True
    pipeline_options.do_table_structure = True

    converter = DocumentConverter(
        format_options={
            InputFormat.IMAGE: ImageFormatOption(pipeline_options=pipeline_options),
        }
    )

    result = converter.convert(str(image_path))
    document = result.document

    if not hasattr(document, 'pictures'):
        print(f'No se detectaron imágenes en {image_path}')
        return []

    original = Image.open(image_path)
    width, height = original.size

    extracted = []
    for idx, picture in enumerate(document.pictures):
        if not picture.prov:
            continue

        bbox = picture.prov[0].bbox
        left = max(0, int(bbox.l))
        top = max(0, int(height - bbox.t))
        right = min(width, int(bbox.r))
        bottom = min(height, int(height - bbox.b))

        if right <= left or bottom <= top:
            continue

        crop = original.crop((left, top, right, bottom))
        out_path = output_dir / f"{image_path.stem}_detected_{idx}.png"
        crop.save(out_path)
        extracted.append(out_path)
        print(f"Imagen extraída: {out_path}")

    return extracted


if __name__ == '__main__':
    image_path = Path('files/2025-08/2D2C9343/2991f57d-c143-4b23-9f87-4dfb1214ef53.jpg')
    image_path = Path('files/2025-08/2E1F7D6C/deddaca4-049e-4a9e-be19-9da7ddde3ec6.jpg')
    extracted = extract_detected_images(image_path)
    print(f"Total extraídas: {len(extracted)}")

"""
Ejemplo: detección de firmas en documentos (PDF/imagen) usando Docling.

Docling incluye un modelo de clasificación de imágenes/figuras
(DocumentFigureClassifier-v2.5) que, entre sus 26 categorías, incluye
la clase "signature". Este ejemplo:

  1) Convierte el documento activando la clasificación de figuras.
  2) Recorre las PictureItem del documento resultante.
  3) Filtra las que fueron clasificadas como firma, por encima de un
     umbral de confianza.
  4) Exporta cada recorte de firma detectada como PNG.

Requisitos:
    pip install docling
    (la primera ejecución descarga el modelo desde Hugging Face)
"""

from pathlib import Path

from docling.datamodel.base_models import InputFormat
from docling.datamodel.pipeline_options import PdfPipelineOptions
from docling.document_converter import DocumentConverter, PdfFormatOption
from docling_core.types.doc import PictureItem
from docling_core.types.doc.document import PictureClassificationData

# --- Configuración ---------------------------------------------------

INPUT_PATH = "factura_firmada.pdf"          # PDF, imagen escaneada, etc.
OUTPUT_DIR = Path("firmas_detectadas")
CONFIDENCE_THRESHOLD = 0.6                  # ajustar según falsos positivos
SIGNATURE_CLASS = "signature"


def build_converter() -> DocumentConverter:
    """Arma el DocumentConverter con la clasificación de figuras activada."""
    pipeline_options = PdfPipelineOptions()
    pipeline_options.generate_picture_images = True   # necesario para recortar
    pipeline_options.images_scale = 2                  # más resolución = mejor recorte
    pipeline_options.do_picture_classification = True  # activa DocumentFigureClassifier

    return DocumentConverter(
        format_options={
            InputFormat.PDF: PdfFormatOption(pipeline_options=pipeline_options)
        }
    )


def find_signatures(doc, threshold: float = CONFIDENCE_THRESHOLD):
    """
    Recorre las figuras del documento y devuelve una lista de tuplas
    (picture_item, confianza) para las que el modelo clasificó como firma.
    """
    signatures = []

    for item, _level in doc.iterate_items():
        if not isinstance(item, PictureItem):
            continue

        for annotation in item.annotations:
            if not isinstance(annotation, PictureClassificationData):
                continue

            # predicted_classes viene ordenado por score descendente
            top_class = annotation.predicted_classes[0]
            if top_class.class_name == SIGNATURE_CLASS and top_class.confidence >= threshold:
                signatures.append((item, top_class.confidence))

    return signatures


def export_signatures(doc, signatures, output_dir: Path):
    output_dir.mkdir(parents=True, exist_ok=True)

    for idx, (picture, confidence) in enumerate(signatures, start=1):
        image = picture.get_image(doc)  # PIL.Image recortada de la figura
        if image is None:
            continue

        out_path = output_dir / f"firma_{idx:02d}_conf{confidence:.2f}.png"
        image.save(out_path)
        print(f"Firma detectada -> {out_path}  (confianza: {confidence:.2%})")


def main():
    converter = build_converter()
    result = converter.convert(INPUT_PATH)
    doc = result.document

    signatures = find_signatures(doc)

    if not signatures:
        print("No se detectaron firmas por encima del umbral configurado.")
        return

    export_signatures(doc, signatures, OUTPUT_DIR)
    print(f"\nTotal de firmas detectadas: {len(signatures)}")


if __name__ == "__main__":
    main()

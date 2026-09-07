# """Demo de conversión de PDF a Markdown con Docling + VLM Granite Docling.

# Usa el pipeline VLM de Docling con el modelo ``ibm-granite/granite-docling-258M``
# (Hugging Face) para entender la página como un VLM (layout + lectura de texto),
# en lugar del pipeline clásico (layout heurístico + OCR).

# NOTA sobre la API: la forma ``pipeline_options.vlm_options.provider =
# "granite_docling"`` corresponde a una versión antigua de Docling. En la versión
# instalada (2.126.0) se configura con ``VlmPipelineOptions`` +
# ``vlm_model_specs.GRANITEDOCLING_TRANSFORMERS`` y el pipeline ``VlmPipeline``.

# Aviso: la primera corrida descarga el modelo (~1 GB) desde Hugging Face.

# Uso:
#     python wip/demo_docling_granite.py
# """

# from __future__ import annotations

# from pathlib import Path

# from docling.datamodel.base_models import InputFormat
# from docling.datamodel.pipeline_options import (
#     VlmPipelineOptions,
#     vlm_model_specs,
# )
# from docling.document_converter import DocumentConverter, PdfFormatOption
# from docling.models.utils.generation_utils import DocTagsRepetitionStopper
# from docling.pipeline.vlm_pipeline import VlmPipeline

# # Raíz de v2/ (derivada del propio script → no depende del CWD).
# _RAIZ = Path(__file__).resolve().parents[1]

# archivos = [
#     _RAIZ / "tests/fixtures/golden/9dfc597f-34c5-41ec-99ae-cf35544c7af8.pdf",
#     _RAIZ / "tests/fixtures/golden/66e6e0ea-e910-41f4-9037-13f0309812c1.jpg",
# ]


# def configurar_granite_docling() -> DocumentConverter:
#     """Configura Docling con el VLM Granite Docling (258M) para PDF/imagen."""
#     # 1. Spec del modelo Granite Docling (descarga desde Hugging Face).
#     custom_vlm_options = vlm_model_specs.GRANITEDOCLING_TRANSFORMERS.model_copy()

#     # 2. Anti-loop de repetición: el modelo 258M entra en loops con textos
#     #    repetitivos (ej. condiciones de boletos). Corta la generación cuando
#     #    detecta patrones DocTags repetidos (ejemplo oficial de Docling).
#     custom_vlm_options.custom_stopping_criteria = [
#         DocTagsRepetitionStopper(N=32)
#     ]

#     # 3. Pipeline VLM con esas opciones.
#     pipeline_options = VlmPipelineOptions(
#         vlm_options=custom_vlm_options,
#     )

#     # 4. Convertidor mapeando PDF e imagen al pipeline VLM.
#     return DocumentConverter(
#         format_options={
#             InputFormat.PDF: PdfFormatOption(
#                 pipeline_cls=VlmPipeline,
#                 pipeline_options=pipeline_options,
#             ),
#             InputFormat.IMAGE: PdfFormatOption(
#                 pipeline_cls=VlmPipeline,
#                 pipeline_options=pipeline_options,
#             ),
#         }
#     )


# # Inicializamos nuestro extractor optimizado con Granite Docling.
# conversor = configurar_granite_docling()

# for archivo in archivos:
#     print(f"\n{'=' * 60}\nARCHIVO: {archivo.name}\n{'=' * 60}")
#     resultado = conversor.convert(str(archivo))
#     markdown = resultado.document.export_to_markdown()
#     print(markdown)

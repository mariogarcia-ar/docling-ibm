# """Demo de conversión a Markdown con Docling + VLM Granite Docling vía Ollama.

# Usa el pipeline VLM de Docling contra el modelo ``ibm/granite-docling:258m``
# servido por **Ollama local** (endpoint compatible OpenAI), en vez de descargar
# el modelo (~1 GB) desde Hugging Face. Ideal para probar el golden set sin
# instalar dependencias de transformers.

# Prerrequisitos:
#     - Ollama corriendo en http://localhost:11434
#     - Modelo descargado:  ollama pull ibm/granite-docling:258m

# NOTA sobre la API (docling >= 2.10): la forma ``ApiVlmOptions(api_url=...,
# api_key=..., model_id=...)`` corresponde a una versión antigua de Docling. En la
# versión instalada se usa ``VlmConvertOptions.from_preset("granite_docling",
# engine_options=ApiVlmEngineOptions(engine_type=VlmEngineType.API_OLLAMA))``:
# el preset ya trae el prompt ``Convert this page to docling.``, el
# ``response_format`` DOCTAGS y el ``model_id`` ``ibm/granite-docling:258m``.
# Como es una API remota (local), hay que habilitar ``enable_remote_services``.

# Uso:
#     python wip/demo_docling_granite_ollama.py
# """

# from __future__ import annotations

# from pathlib import Path

# from docling.datamodel.base_models import InputFormat
# from docling.datamodel.pipeline_options import (
#     VlmConvertOptions,
#     VlmPipelineOptions,
# )
# from docling.datamodel.vlm_engine_options import ApiVlmEngineOptions, VlmEngineType
# from docling.document_converter import DocumentConverter, PdfFormatOption
# from docling.pipeline.vlm_pipeline import VlmPipeline

# # Raíz de v2/ (derivada del propio script → no depende del CWD).
# _RAIZ = Path(__file__).resolve().parents[1]

# archivos = [
#     _RAIZ / "tests/fixtures/golden/9dfc597f-34c5-41ec-99ae-cf35544c7af8.pdf",
#     _RAIZ / "tests/fixtures/golden/66e6e0ea-e910-41f4-9037-13f0309812c1.jpg",
# ]


# def configurar_granite_docling_ollama() -> DocumentConverter:
#     """Configura Docling con el VLM Granite Docling (258M) servido por Ollama."""
#     # 1. Opciones del VLM: preset Granite Docling + motor API Ollama local.
#     #    El preset define modelo/prompt/formato (DOCTAGS); el motor apunta a
#     #    http://localhost:11434/v1/chat/completions (default de API_OLLAMA).
#     vlm_options = VlmConvertOptions.from_preset(
#         "granite_docling",
#         engine_options=ApiVlmEngineOptions(
#             engine_type=VlmEngineType.API_OLLAMA,
#         ),
#     )

#     # 2. Pipeline VLM con esas opciones.
#     pipeline_options = VlmPipelineOptions(vlm_options=vlm_options)
#     # Las llamadas a Ollama son una API remota → hay que habilitarlas explícitamente.
#     pipeline_options.enable_remote_services = True

#     # 3. Convertidor mapeando PDF e imagen al pipeline VLM (ambos backends son paginados).
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


# # Inicializamos el conversor con Granite Docling vía Ollama.
# conversor = configurar_granite_docling_ollama()

# for archivo in archivos:
#     print(f"\n{'=' * 60}\nARCHIVO: {archivo.name}\n{'=' * 60}")
#     resultado = conversor.convert(str(archivo))
#     markdown = resultado.document.export_to_markdown()
#     print(markdown)

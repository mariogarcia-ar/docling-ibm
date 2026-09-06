"""Módulo ``processing`` (esqueleto — F1) — procesamiento Docling multi-formato.

**Fase**: F1 (refactor docling). En F0 solo se deja el **esqueleto** con la
firma pública de los submódulos y sus contratos de entrada/salida, sin lógica
de negocio ni acoplamiento a scripts de ``v1/``.

Responsabilidades (doc 03 §4.1): decidir tipo de entrada, elegir ruta,
normalizar y, en imágenes, aplicar gate de procesabilidad → clase de imagen →
preprocesamiento → orientación → motor OCR/VLM → salida ordenada. El contrato
de salida común es ``ProcessedDocument`` (definido en ``models/docling.py``).
"""

from __future__ import annotations

from ..models.docling import ProcessedDocument  # contrato de salida (F1)

__all__ = ["ProcessedDocument"]

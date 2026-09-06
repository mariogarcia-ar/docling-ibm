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

# Detector de tipo de entrada (F1 / T-101, E-DOC-1): contrato ``TipoEntrada``
# y función ``detectar`` expuestos por el módulo ``processing``.
from .type_detector import TipoEntrada, detectar

# Clasificador de imagen + gate de procesabilidad (F1 / T-102, E-DOC-2).
from .image_classifier import (
    ClaseImagen,
    ClasificacionImagen,
    VeredictoGate,
    clasificar,
    leer_caracteristicas,
    sospechar_manuscrito,
    verificar_procesabilidad,
)

# Preprocesamiento + orientación (F1 / T-103, E-DOC-2).
from .orientation import (
    ORIENTACION_HORIZONTAL,
    ORIENTACION_VERTICAL,
    ORIENTACIONES_VALIDAS,
    detectar_orientacion,
    orientacion_de,
    orientacion_por_box,
    requiere_rotacion,
)
from .preprocessing import QualityReport, evaluar_calidad, preprocesar

__all__ = [
    "ProcessedDocument",
    "TipoEntrada",
    "detectar",
    "ClaseImagen",
    "ClasificacionImagen",
    "VeredictoGate",
    "clasificar",
    "leer_caracteristicas",
    "sospechar_manuscrito",
    "verificar_procesabilidad",
    "ORIENTACION_HORIZONTAL",
    "ORIENTACION_VERTICAL",
    "ORIENTACIONES_VALIDAS",
    "detectar_orientacion",
    "orientacion_de",
    "orientacion_por_box",
    "requiere_rotacion",
    "QualityReport",
    "evaluar_calidad",
    "preprocesar",
]

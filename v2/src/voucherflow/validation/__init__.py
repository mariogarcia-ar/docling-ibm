"""Módulo ``validation`` (F2) — gate qween doble-paso.

**Fase**: F2 (refactor qween). En F0 se deja el esqueleto con el contrato de
salida ``ValidationResult`` y el enumerado ``VeredictoGate``. En F2/T-201 se
agrega la **preparación de vistas** (``vistas.py``: ``VistaPreparada`` y
``preparar_vista_rapida``, vista barata para la decisión de E-QWE-1). En
**T-202** se agrega la **decisión binaria de una pasada**
(``decidir_es_comprobante``) con el prompt corto versionado
(``prompt_qween.py``) sobre la vista de decisión y el ``OllamaClient``. En
**T-203** se agrega la **orquestación del doble paso**
(``validar_y_procesar`` + ``ResultadoValidacion``), las vistas de revisión/fiel
(``preparar_vista_revision`` / ``preparar_vista_fiel``) y ``validar_comprobante``
deja de lanzar ``NotImplementedError`` (delega en la orquestación).
"""

from __future__ import annotations

from .qween import (
    CAMPO_GATE,
    MAX_FRAGMENTO_TEXTO_CHARS,
    ResultadoValidacion,
    ValidationResult,
    VeredictoGate,
    decidir_es_comprobante,
    validar_comprobante,
    validar_y_procesar,
)
from .prompt_qween import (
    CALIDAD_JPEG_ENVIO,
    FACTOR_PATCH_QWEN2VL,
    LADO_MAYOR_OBJETIVO_POR_VISTA,
    LADO_MENOR_MINIMO_ENVIO_PX,
    SYSTEM_PROMPT_QWEEN,
    USER_SOLO_IMAGEN,
    USER_TEXTO,
    VERSION_PROMPT_QWEEN,
    construir_messages_gate,
    dimensiones_objetivo_vlm,
    imagen_envio_base64,
)
from .vistas import (
    CALIDAD_POR_TIPO_VISTA,
    GRADO_CALIDAD_POR_NIVEL,
    HOOK_DEGRADACION,
    RESOLUCION_VISTA_FIEL_PX,
    RESOLUCION_VISTA_RAPIDA_PX,
    RESOLUCION_VISTA_REVISION_PX,
    TIPOS_TEXTO_SIN_THUMBNAIL,
    TIPOS_VISTA,
    VistaPreparada,
    preparar_vista_fiel,
    preparar_vista_rapida,
    preparar_vista_revision,
)

__all__ = [
    # Contratos congelados de F0 (no romper; F2-subplan §4 reglas duras).
    "ValidationResult",
    "VeredictoGate",
    "validar_comprobante",
    # Preparación de vistas (T-201 / T-203; E-QWE-1 y E-QWE-2).
    "VistaPreparada",
    "preparar_vista_rapida",
    "preparar_vista_revision",
    "preparar_vista_fiel",
    "TIPOS_VISTA",
    "CALIDAD_POR_TIPO_VISTA",
    "GRADO_CALIDAD_POR_NIVEL",
    "HOOK_DEGRADACION",
    "RESOLUCION_VISTA_RAPIDA_PX",
    "RESOLUCION_VISTA_REVISION_PX",
    "RESOLUCION_VISTA_FIEL_PX",
    "TIPOS_TEXTO_SIN_THUMBNAIL",
    # Decisión binaria de una pasada (T-202 / E-QWE-1) y prompt corto.
    "decidir_es_comprobante",
    "CAMPO_GATE",
    "MAX_FRAGMENTO_TEXTO_CHARS",
    "construir_messages_gate",
    "imagen_envio_base64",
    "LADO_MAYOR_OBJETIVO_POR_VISTA",
    "CALIDAD_JPEG_ENVIO",
    "LADO_MENOR_MINIMO_ENVIO_PX",
    "FACTOR_PATCH_QWEN2VL",
    "dimensiones_objetivo_vlm",
    "VERSION_PROMPT_QWEEN",
    "SYSTEM_PROMPT_QWEEN",
    "USER_SOLO_IMAGEN",
    "USER_TEXTO",
    # Orquestación del doble paso (T-203 / E-QWE-1 y E-QWE-2).
    "ResultadoValidacion",
    "validar_y_procesar",
]


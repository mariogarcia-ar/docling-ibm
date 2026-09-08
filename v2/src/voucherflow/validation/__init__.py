"""Módulo ``validation`` (F2) — gate qween doble-paso.

**Fase**: F2 (refactor qween). En F0 se deja el esqueleto con el contrato de
salida ``ValidationResult`` y el enumerado ``VeredictoGate``. En F2/T-201 se
agrega la **preparación de vistas** (``vistas.py``: ``VistaPreparada`` y
``preparar_vista_rapida``, vista barata para la decisión de E-QWE-1). En
**T-202** se agrega la **decisión binaria de una pasada**
(``decidir_es_comprobante``) con el prompt corto versionado
(``prompt_qween.py``) sobre la vista de decisión y el ``OllamaClient``. La
orquestación del doble paso (``validar_y_procesar``) es T-203 (F2-subplan
§3.2/§3.3): por eso el esqueleto ``validar_comprobante`` sigue lanzando
``NotImplementedError`` hasta entonces.
"""

from __future__ import annotations

from .qween import (
    CAMPO_GATE,
    MAX_FRAGMENTO_TEXTO_CHARS,
    ValidationResult,
    VeredictoGate,
    decidir_es_comprobante,
    validar_comprobante,
)
from .prompt_qween import (
    SYSTEM_PROMPT_QWEEN,
    USER_SOLO_IMAGEN,
    USER_TEXTO,
    VERSION_PROMPT_QWEEN,
    construir_messages_gate,
)
from .vistas import (
    CALIDAD_POR_TIPO_VISTA,
    GRADO_CALIDAD_POR_NIVEL,
    HOOK_DEGRADACION,
    RESOLUCION_VISTA_RAPIDA_PX,
    TIPOS_TEXTO_SIN_THUMBNAIL,
    TIPOS_VISTA,
    VistaPreparada,
    preparar_vista_rapida,
)

__all__ = [
    # Contratos congelados de F0 (no romper; F2-subplan §4 reglas duras).
    "ValidationResult",
    "VeredictoGate",
    "validar_comprobante",
    # Preparación de vistas (T-201 / E-QWE-1).
    "VistaPreparada",
    "preparar_vista_rapida",
    "TIPOS_VISTA",
    "CALIDAD_POR_TIPO_VISTA",
    "GRADO_CALIDAD_POR_NIVEL",
    "HOOK_DEGRADACION",
    "RESOLUCION_VISTA_RAPIDA_PX",
    "TIPOS_TEXTO_SIN_THUMBNAIL",
    # Decisión binaria de una pasada (T-202 / E-QWE-1) y prompt corto.
    "decidir_es_comprobante",
    "CAMPO_GATE",
    "MAX_FRAGMENTO_TEXTO_CHARS",
    "construir_messages_gate",
    "VERSION_PROMPT_QWEEN",
    "SYSTEM_PROMPT_QWEEN",
    "USER_SOLO_IMAGEN",
    "USER_TEXTO",
]


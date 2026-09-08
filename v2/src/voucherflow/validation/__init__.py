"""Módulo ``validation`` (F2) — gate qween doble-paso.

**Fase**: F2 (refactor qween). En F0 se deja el esqueleto con el contrato de
salida ``ValidationResult`` y el enumerado ``VeredictoGate``. En F2/T-201 se
agrega la **preparación de vistas** (``vistas.py``: ``VistaPreparada`` y
``preparar_vista_rapida``, vista barata para la decisión de E-QWE-1). La
decisión en sí (``decidir_es_comprobante``) y la orquestación del doble paso
(``validar_y_procesar``) son T-202/T-203 (F2-subplan §3.2/§3.3); el esqueleto
``validar_comprobante`` sigue lanzando ``NotImplementedError`` hasta entonces.
"""

from __future__ import annotations

from .qween import ValidationResult, VeredictoGate, validar_comprobante
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
]


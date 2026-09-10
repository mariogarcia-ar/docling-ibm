"""Módulo ``classification`` — tipo/letra (F3/T-301/T-302) + contable (F3/T-304).

**Fase**: F3 (refactor clasificación). F0 dejó el esqueleto con los contratos
``TipoComprobanteResult`` y ``ClasificacionContableResult``. **T-301** implementa
``clasificar_tipo_comprobante()`` (motor de reglas R1-R7 en código, ADR-006);
**T-302** implementa la **evidencia de lectura** del tipo/letra
(``prompt_tipo_comprobante.py`` + ``evidencia.py``): el prompt `11.1` deja de
devolver la decisión y pasa a devolver evidencia trazable (recuadro del VLM +
texto del LLM) que alimenta ese motor; ``clasificar_contable()`` se implementa
en **T-304**.

Los flujos VLM/LLM reales son de F4/T-401: T-302 usa un **lector inyectable**
(``evidencia.Lector``) y en la suite default se le pasa un doble
(F3-subplan §2.6).
"""

from __future__ import annotations

from .evidencia import (
    CAMPO_EXPLICACION,
    CAMPOS_TRAZABLES,
    EVIDENCIA_CAMPO_LETRA,
    NOTA_LLM_SIN_PATRON_R5,
    ErrorEvidencia,
    EvidenciaLectura,
    LecturaTipoComprobante,
    Lector,
    campo_declarado_de_evidencia,
    construir_source_evidence,
    contexto_desde_evidencia,
    leer_evidencia,
    parsear_evidencia_lectura,
    veredicto_raw_de_evidencia,
)
from .prompt_tipo_comprobante import (
    CAMPOS_EVIDENCIA,
    CAMPOS_FUERA_DEL_CONTRATO,
    FUENTES_LECTURA,
    SYSTEM_PROMPT_TIPO_COMPROBANTE,
    SYSTEM_PROMPT_TIPO_COMPROBANTE_LLM,
    SYSTEM_PROMPT_TIPO_COMPROBANTE_VLM,
    USER_IMAGEN_TIPO_COMPROBANTE,
    USER_TEXTO_TIPO_COMPROBANTE,
    VERSION_PROMPT_TIPO_COMPROBANTE,
    construir_messages_tipo_comprobante,
)
from .tipo_comprobante import (
    ClasificacionContableResult,
    TipoComprobanteResult,
    clasificar_contable,
    clasificar_tipo_comprobante,
)

__all__ = [
    # Contratos congelados de F0 (no romper; F3-subplan §4 reglas duras).
    "TipoComprobanteResult",
    "ClasificacionContableResult",
    # T-301 · Motor de reglas R1-R7 (ADR-006).
    "clasificar_tipo_comprobante",
    "clasificar_contable",
    # T-302 · Prompt de evidencia versionado (ADR-005) y contrato del prompt.
    "VERSION_PROMPT_TIPO_COMPROBANTE",
    "CAMPOS_EVIDENCIA",
    "CAMPOS_FUERA_DEL_CONTRATO",
    "FUENTES_LECTURA",
    "SYSTEM_PROMPT_TIPO_COMPROBANTE",
    "SYSTEM_PROMPT_TIPO_COMPROBANTE_VLM",
    "SYSTEM_PROMPT_TIPO_COMPROBANTE_LLM",
    "USER_TEXTO_TIPO_COMPROBANTE",
    "USER_IMAGEN_TIPO_COMPROBANTE",
    "construir_messages_tipo_comprobante",
    # T-302 · Evidencia de lectura (ADR-001) y lector inyectable.
    "EVIDENCIA_CAMPO_LETRA",
    "CAMPO_EXPLICACION",
    "CAMPOS_TRAZABLES",
    "NOTA_LLM_SIN_PATRON_R5",
    "ErrorEvidencia",
    "Lector",
    "EvidenciaLectura",
    "LecturaTipoComprobante",
    "parsear_evidencia_lectura",
    "construir_source_evidence",
    "contexto_desde_evidencia",
    "leer_evidencia",
    # T-303 · Pasada 1 de reglas raw por fuente (califican la evidencia).
    "campo_declarado_de_evidencia",
    "veredicto_raw_de_evidencia",
]

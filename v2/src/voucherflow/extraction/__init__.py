"""Módulo ``extraction`` (F4) — extracción VLM + LLM con contrato de evidencia.

**Fase**: F4 (extracción) · **Tarea**: T-401.

Expone la superficie pública de la extracción —el contrato congelado de F0 más
los elementos de T-401— y reexporta el contrato de evidencia de los módulos
internos:

* ``flujo_vlm`` / ``flujo_llm`` — cada flujo devuelve ``SourceEvidence`` con el
  contrato de F0 (ADR-001).
* ``extraer`` — corre **los dos flujos en paralelo** sobre el mismo comprobante
  (E-EXT-1) y conserva ambas evidencias sin colapsar.
* ``construir_messages_extraccion`` / ``VERSION_PROMPT_EXTRACCION`` — el prompt
  de evidencia versionado (ADR-005).
* ``parsear_evidencia_extraccion`` / ``construir_source_evidence`` — el paso de
  respuesta del modelo a contrato de F0.
* ``combinar_evidencia`` — sigue siendo el esqueleto de **T-404** (precedencia
  ADR-002) y lanza ``NotImplementedError`` a propósito.
"""

from __future__ import annotations

from .evidencia import (
    CAMPO_FUENTE_LECTURA,
    CAMPOS_CON_VOCABULARIO,
    CAMPOS_SOSTEN_NO_EVALUADO,
    CLAVE_CAMPOS,
    CLAVES_POR_CAMPO,
    VOCABULARIO_MONEDA,
    VOCABULARIO_TIPO_COMPROBANTE,
    CampoLectura,
    ErrorEvidencia,
    ErrorExtraccion,
    EvidenciaExtraccion,
    ExtraccionEvidencia,
    Lector,
    ResultadoFlujo,
    campo_declarado_de_campo,
    construir_source_evidence,
    ejecutar_flujo,
    extraer_evidencia,
    parsear_evidencia_extraccion,
    veredicto_raw_de_evidencia,
)
from .flows import combinar_evidencia, extraer, flujo_llm, flujo_vlm
from .prompt_extraccion import (
    CAMPOS_EXTRACCION,
    CAMPOS_FUERA_DEL_CONTRATO,
    FUENTES_EXTRACCION,
    SYSTEM_PROMPT_EXTRACCION,
    SYSTEM_PROMPT_EXTRACCION_LLM,
    SYSTEM_PROMPT_EXTRACCION_VLM,
    SYSTEM_PROMPT_POR_FUENTE,
    USER_IMAGEN_EXTRACCION,
    USER_TEXTO_EXTRACCION,
    VERSION_PROMPT_EXTRACCION,
    construir_messages_extraccion,
)

__all__ = [
    # flujos (contrato de F0)
    "flujo_vlm",
    "flujo_llm",
    "extraer",
    "combinar_evidencia",
    # orquestación y evidencia (T-401)
    "Lector",
    "CampoLectura",
    "EvidenciaExtraccion",
    "ResultadoFlujo",
    "ExtraccionEvidencia",
    "parsear_evidencia_extraccion",
    "campo_declarado_de_campo",
    "veredicto_raw_de_evidencia",
    "construir_source_evidence",
    "ejecutar_flujo",
    "extraer_evidencia",
    # errores
    "ErrorEvidencia",
    "ErrorExtraccion",
    # prompt versionado (ADR-005)
    "VERSION_PROMPT_EXTRACCION",
    "CAMPOS_EXTRACCION",
    "CAMPOS_FUERA_DEL_CONTRATO",
    "FUENTES_EXTRACCION",
    "SYSTEM_PROMPT_EXTRACCION",
    "SYSTEM_PROMPT_EXTRACCION_VLM",
    "SYSTEM_PROMPT_EXTRACCION_LLM",
    "SYSTEM_PROMPT_POR_FUENTE",
    "USER_TEXTO_EXTRACCION",
    "USER_IMAGEN_EXTRACCION",
    "construir_messages_extraccion",
    # constantes del contrato
    "CLAVE_CAMPOS",
    "CLAVES_POR_CAMPO",
    "CAMPO_FUENTE_LECTURA",
    "CAMPOS_CON_VOCABULARIO",
    "CAMPOS_SOSTEN_NO_EVALUADO",
    "VOCABULARIO_TIPO_COMPROBANTE",
    "VOCABULARIO_MONEDA",
]

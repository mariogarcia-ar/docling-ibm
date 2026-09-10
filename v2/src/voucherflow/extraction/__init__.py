"""Módulo ``extraction`` (F4) — extracción VLM + LLM con contrato de evidencia.

**Fase**: F4 (extracción) · **Tareas**: T-401, T-402.

Expone la superficie pública de la extracción —el contrato congelado de F0 más
los elementos de T-401/T-402— y reexporta el contrato de evidencia de los módulos
internos:

* ``flujo_vlm`` / ``flujo_llm`` — cada flujo devuelve ``SourceEvidence`` con el
  contrato de F0 (ADR-001).
* ``extraer`` — corre **los dos flujos en paralelo** sobre el mismo comprobante
  (E-EXT-1) y conserva ambas evidencias sin colapsar.
* ``construir_messages_extraccion`` / ``VERSION_PROMPT_EXTRACCION`` — el prompt
  de evidencia versionado (ADR-005).
* ``parsear_evidencia_extraccion`` / ``construir_source_evidence`` — el paso de
  respuesta del modelo a contrato de F0.
* ``normalizar_campo`` / ``normalizar_evidencia`` / ``VERSION_NORMALIZACION`` —
  la normalización key-value de **T-402** (E-EXT-3): CUIT cortado a dígitos y
  guiones propios, fechas ``YYYY-MM-DD``, montos numéricos, textos colapsados y
  ``punto_venta``/``numero_comprobante`` derivados del número impreso. El valor
  crudo de cada campo queda en ``CampoLectura.valor_crudo``.
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
from .key_value import (
    ALIAS_FECHA,
    ALIAS_MONTO,
    CAMPOS_DERIVADOS_COMPROBANTE,
    CAMPOS_GENERICOS_CON_REGLA,
    NORM_COMPROBANTE,
    NORM_CUIT,
    NORM_FECHA,
    NORM_ITEMS,
    NORM_MONEDA,
    NORM_MONTO,
    NORM_TEXTO,
    NORM_VOCABULARIO,
    REGLA_POR_CAMPO,
    VERSION_NORMALIZACION,
    AvisoNormalizacion,
    CampoNormalizado,
    ErrorNormalizacion,
    InformeNormalizacion,
    ItemExtraido,
    NormalizacionEvidencia,
    cuit_completo,
    monto_ambiguo,
    normalizar_campo,
    normalizar_cuit,
    normalizar_descripcion,
    normalizar_evidencia,
    normalizar_evidencia_extraccion,
    normalizar_fecha,
    normalizar_moneda,
    normalizar_monto,
    normalizar_texto,
    normalizar_vocabulario,
    parsear_items,
    regla_de_campo,
    separar_comprobante,
    valores_normalizados,
)
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
    # normalización key-value (T-402 / E-EXT-3)
    "VERSION_NORMALIZACION",
    "NORM_CUIT",
    "NORM_FECHA",
    "NORM_MONTO",
    "NORM_COMPROBANTE",
    "NORM_VOCABULARIO",
    "NORM_MONEDA",
    "NORM_TEXTO",
    "NORM_ITEMS",
    "REGLA_POR_CAMPO",
    "ALIAS_MONTO",
    "ALIAS_FECHA",
    "CAMPOS_DERIVADOS_COMPROBANTE",
    "CAMPOS_GENERICOS_CON_REGLA",
    "ErrorNormalizacion",
    "AvisoNormalizacion",
    "ItemExtraido",
    "CampoNormalizado",
    "InformeNormalizacion",
    "NormalizacionEvidencia",
    "normalizar_texto",
    "normalizar_descripcion",
    "normalizar_vocabulario",
    "normalizar_cuit",
    "cuit_completo",
    "normalizar_fecha",
    "normalizar_monto",
    "monto_ambiguo",
    "normalizar_moneda",
    "separar_comprobante",
    "parsear_items",
    "regla_de_campo",
    "normalizar_campo",
    "normalizar_evidencia",
    "normalizar_evidencia_extraccion",
    "valores_normalizados",
]

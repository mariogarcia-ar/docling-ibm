"""Módulo ``classification`` — tipo/letra (F3/T-301/T-302/T-303) + contable (F3/T-304).

**Fase**: F3 (refactor clasificación). F0 dejó el esqueleto con los contratos
``TipoComprobanteResult`` y ``ClasificacionContableResult``. **T-301** implementa
``clasificar_tipo_comprobante()`` (motor de reglas R1-R7 en código, ADR-006);
**T-302** la **evidencia de lectura** del tipo/letra (``prompt_tipo_comprobante.py``
+ ``evidencia.py``): el prompt `11.1` deja de devolver la decisión y pasa a
devolver evidencia trazable que alimenta ese motor; **T-303** la **pasada 1 de
reglas raw** por fuente (``rules/raw.py``); **T-304** la **cadena contable
01→02→03** (``prompts_contable.py`` + ``contable.py``), que reemplaza a
``v1/classification_pipeline.py`` con contratos tipados entre pasos y checkpoints.

Los flujos VLM/LLM reales de lectura son de F4/T-401: T-302 usa un **lector
inyectable** (``evidencia.Lector``) y en la suite default se le pasa un doble
(F3-subplan §2.6). La cadena contable, en cambio, **sí** corre el modelo en F3
(``contable.ejecutar_cadena``), con doble en la suite.
"""

from __future__ import annotations

from .contable import (
    REGLAS_CADENA_CONTABLE,
    ErrorCadenaContable,
    OpcionCentroCosto,
    OpcionMacroCategoria,
    PasoConceptoCodigo,
    ResultadoCadenaContable,
    RespuestaContableInvalida,
    base_values,
    clasificar_pasos_contables,
    ejecutar_cadena,
    ejecutar_paso,
    ejecutar_paso_01,
    ejecutar_paso_02,
    ejecutar_paso_03,
    escribir_checkpoint,
    leer_checkpoint,
    opciones_centro_costo,
    opciones_macro_categoria,
    paso_concepto_codigo,
    primary_centro_costo,
    primary_macro_categoria,
    ruta_checkpoint,
)
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
from .prompts_contable import (
    CONDICIONES_IMPOSITIVAS,
    CONDICION_IMPOSITIVA_DEFAULT,
    PASOS_CONTABLES,
    VALOR_NO_INFORMADO,
    VERSION_PROMPT_CONTABLE_01,
    VERSION_PROMPT_CONTABLE_02,
    VERSION_PROMPT_CONTABLE_03,
    PlaceholderFaltante,
    condicion_impositiva_no_valida,
    construir_messages_contable,
    renderizar_user,
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
    # T-304 · Cadena contable 01→02→03 (contratos entre pasos + checkpoints).
    "VERSION_PROMPT_CONTABLE_01",
    "VERSION_PROMPT_CONTABLE_02",
    "VERSION_PROMPT_CONTABLE_03",
    "CONDICIONES_IMPOSITIVAS",
    "CONDICION_IMPOSITIVA_DEFAULT",
    "VALOR_NO_INFORMADO",
    "PASOS_CONTABLES",
    "PlaceholderFaltante",
    "renderizar_user",
    "construir_messages_contable",
    "condicion_impositiva_no_valida",
    "ErrorCadenaContable",
    "RespuestaContableInvalida",
    "OpcionCentroCosto",
    "OpcionMacroCategoria",
    "PasoConceptoCodigo",
    "ResultadoCadenaContable",
    "REGLAS_CADENA_CONTABLE",
    "primary_centro_costo",
    "primary_macro_categoria",
    "opciones_centro_costo",
    "opciones_macro_categoria",
    "paso_concepto_codigo",
    "clasificar_pasos_contables",
    "ruta_checkpoint",
    "escribir_checkpoint",
    "leer_checkpoint",
    "ejecutar_paso",
    "ejecutar_paso_01",
    "ejecutar_paso_02",
    "ejecutar_paso_03",
    "ejecutar_cadena",
    "base_values",
]

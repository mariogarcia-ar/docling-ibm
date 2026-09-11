"""Módulo ``rules`` — motor de reglas declarativo (F0) + reglas R1-R7 (F3).

**Fase**: F0 deja el motor declarativo (``Rule`` + ``Registry``) como base del
ADR-006 (reglas en código). **F3/T-301** migra las reglas R1-R7 del prompt WIP
(``prompts/wip/deteccion_tipo_factura.yaml``) a un motor de reglas en código,
organizadas en tres registros:

- :data:`REGISTRO_NEGOCIO` (R1/R2A/R2B/R3) → ``tipo_esperado_por_negocio``.
- :data:`REGISTRO_LECTURA` (R4/R5/R6) → ``tipo_detectado_por_documento``.
- :data:`REGISTRO_CONFLICTO` (R7) → alertas de auditoría.

**F4/T-403** completa la pasada 1 por fuente en :mod:`~voucherflow.rules.raw`
(sostén por forma canónica y coherencia interna) y **F4/T-404** agrega la tabla
de precedencia por campo en :mod:`~voucherflow.rules.precedencia` (ADR-002).

``Rule`` y ``Registry`` se siguen exportando sin cambios (contrato congelado de
F0). Las reglas cruzadas de F5 y la detección de gaps se agregan en su fase.
"""

from __future__ import annotations

from .contexto import ContextoTipoComprobante, normalizar_letra
from .precedencia import (
    ORDEN_CANONICO_FUENTES,
    FUENTES_LECTURA,
    FUENTES_NO_LECTURA,
    PREC_DATO_COMPUTADO,
    PREC_LECTURA_TEXTO,
    PREC_LECTURA_VISUAL,
    PREC_REGLA_DE_ORO,
    TABLA_PRECEDENCIA,
    CombinacionEvidencia,
    PrecedenciaCampo,
    ResolucionCampo,
    combinar,
    resolver_campo,
    resumen_combinacion,
    valor_de,
)
from .raw import (
    GRAVEDAD_POR_REGLA,
    REGISTRO_RAW,
    CampoDeclarado,
    Gravedad,
    VeredictoRaw,
    coincidencias_en_sustento,
    construir_registro_raw,
    evaluar_raw,
)
from .registry import Registry, Rule
from .contexto_conclusion import (
    CAMPOS_CRITICOS,
    CAMPO_IVA,
    CRUZ_1_NEGOCIO,
    CRUZ_2_LETRA_SIN_SOSTEN,
    CRUZ_3_COHERENCIA_LETRA,
    CRUZ_4_DATOS_FALTANTES,
    CRUZ_5_CONFLICTO_CREDITO,
    FAMILIA_CONFLICTO,
    FAMILIA_FAST_FAIL,
    FAMILIA_NEGOCIO,
    ContextoConclusion,
    es_monto_cero,
)
from .cruzadas import (
    ESTADO_APROBADO,
    ESTADO_RECHAZADO,
    ESTADO_REVISION,
    REGISTRO_CRUZADAS,
    VERSION_CRUZADAS,
    ConclusionResult,
    VeredictoCruzadas,
    construir_conclusion,
    construir_decision,
    construir_registro_cruzadas,
    evaluar_cruzadas,
    resumen_cruzadas,
)
from .gaps import (
    CATALOGO_GAPS,
    CRITICIDAD_BLOQUEANTE,
    CRITICIDAD_INFORMATIVA,
    GAP_POR_DEFECTO,
    INTENTO_CUBIERTO,
    INTENTO_NO_BUSCABLE,
    INTENTO_NO_DISPONIBLE,
    INTENTO_PRESUPUESTO_AGOTADO,
    INTENTO_SIN_DATO,
    VERSION_GAPS,
    BuscadorEvidencia,
    DeteccionGaps,
    Gap,
    IntentoBusqueda,
    PresupuestoBusqueda,
    ResultadoBusqueda,
    ResultadoBusquedaAdicional,
    buscar_evidencia_adicional,
    detectar_gaps,
)
from .tipo_comprobante_rules import (
    MENSAJE_R7,
    REGEX_LETRA_ENCABEZADO,
    REGISTRO_CONFLICTO,
    REGISTRO_LECTURA,
    REGISTRO_NEGOCIO,
    TABLA_INFERENCIA_R6,
    construir_alerta,
    construir_registros,
    evaluar_conflicto,
    evaluar_lectura,
    evaluar_negocio,
    extraer_letra_encabezado,
    letra_de_campos_totales,
    letra_de_encabezado,
    letra_de_recuadro,
    regla_lectura_resolutoria,
    regla_negocio_resolutoria,
    reglas_lectura_disparadas,
    reglas_negocio_disparadas,
)

__all__ = [
    # Motor declarativo (F0, contrato congelado).
    "Rule",
    "Registry",
    # Contexto tipado (F3/T-301).
    "ContextoTipoComprobante",
    "normalizar_letra",
    # Reglas R1-R7 y registros (F3/T-301).
    "REGISTRO_NEGOCIO",
    "REGISTRO_LECTURA",
    "REGISTRO_CONFLICTO",
    "REGEX_LETRA_ENCABEZADO",
    "MENSAJE_R7",
    "TABLA_INFERENCIA_R6",
    "construir_registros",
    "evaluar_negocio",
    "evaluar_lectura",
    "evaluar_conflicto",
    "regla_negocio_resolutoria",
    "regla_lectura_resolutoria",
    "reglas_negocio_disparadas",
    "reglas_lectura_disparadas",
    "construir_alerta",
    "extraer_letra_encabezado",
    "letra_de_recuadro",
    "letra_de_encabezado",
    "letra_de_campos_totales",
    # Reglas raw por fuente / pasada 1 (F3/T-303; las reutiliza F4/T-403).
    "Gravedad",
    "CampoDeclarado",
    "VeredictoRaw",
    "REGISTRO_RAW",
    "GRAVEDAD_POR_REGLA",
    "construir_registro_raw",
    "coincidencias_en_sustento",
    "evaluar_raw",
    # Precedencia por campo / combinación (F4/T-404, ADR-002).
    "ORDEN_CANONICO_FUENTES",
    "FUENTES_LECTURA",
    "FUENTES_NO_LECTURA",
    "PREC_REGLA_DE_ORO",
    "PREC_LECTURA_VISUAL",
    "PREC_LECTURA_TEXTO",
    "PREC_DATO_COMPUTADO",
    "TABLA_PRECEDENCIA",
    "PrecedenciaCampo",
    "ResolucionCampo",
    "CombinacionEvidencia",
    "resolver_campo",
    "valor_de",
    "combinar",
    "resumen_combinacion",
    # Reglas cruzadas / pasada 2 de conclusión (F5/T-501, E-CONC-1).
    "ContextoConclusion",
    "CAMPOS_CRITICOS",
    "CAMPO_IVA",
    "es_monto_cero",
    "CRUZ_1_NEGOCIO",
    "CRUZ_2_LETRA_SIN_SOSTEN",
    "CRUZ_3_COHERENCIA_LETRA",
    "CRUZ_4_DATOS_FALTANTES",
    "CRUZ_5_CONFLICTO_CREDITO",
    "FAMILIA_NEGOCIO",
    "FAMILIA_FAST_FAIL",
    "FAMILIA_CONFLICTO",
    "VERSION_CRUZADAS",
    "VeredictoCruzadas",
    "ConclusionResult",
    "REGISTRO_CRUZADAS",
    "construir_registro_cruzadas",
    "evaluar_cruzadas",
    "construir_decision",
    "construir_conclusion",
    "resumen_cruzadas",
    "ESTADO_APROBADO",
    "ESTADO_RECHAZADO",
    "ESTADO_REVISION",
    # Gaps y búsqueda de evidencia adicional (F5/T-502, E-CONC-2 / ADR-003).
    "VERSION_GAPS",
    "CRITICIDAD_BLOQUEANTE",
    "CRITICIDAD_INFORMATIVA",
    "INTENTO_CUBIERTO",
    "INTENTO_NO_DISPONIBLE",
    "INTENTO_SIN_DATO",
    "INTENTO_PRESUPUESTO_AGOTADO",
    "INTENTO_NO_BUSCABLE",
    "CATALOGO_GAPS",
    "GAP_POR_DEFECTO",
    "Gap",
    "DeteccionGaps",
    "PresupuestoBusqueda",
    "BuscadorEvidencia",
    "ResultadoBusqueda",
    "IntentoBusqueda",
    "ResultadoBusquedaAdicional",
    "detectar_gaps",
    "buscar_evidencia_adicional",
]


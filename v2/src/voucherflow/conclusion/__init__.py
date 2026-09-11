"""Módulo ``conclusion`` — reglas → agente → HITL (F5).

**Fase**: F5. En F0 se dejó el esqueleto con las firmas públicas
``concluir`` / ``escalar_a_agente`` / ``encolar_hitl``. **T-501** implementa
``concluir`` (la pasada 2 de reglas cruzadas sobre la evidencia combinada, que
produce el ``Decision`` del caso); **T-504** implementa ``escalar_a_agente``
(escalada al agente con blindaje de la elección); **T-505** implementa
``encolar_hitl`` (cola de revisión humana + muestreo de auditoría). Queda
pendiente la persistencia del ``CaseRecord`` (T-506).
"""

from __future__ import annotations

from .agent import (
    AGENTE_ELECCION_INVALIDA,
    AGENTE_FALLO,
    AGENTE_NO_ESCALADO,
    AGENTE_SE_ABSTUVO,
    AGENTE_ELIGIO,
    VERSION_AGENTE,
    Agente,
    AgenteOllama,
    DecisionAgente,
    EleccionAgente,
    parsear_eleccion_agente,
)
from .agent import escalar_a_agente as escalar_veredicto
from .consolidacion import (
    Consolidacion,
    consolidar,
    es_certeza_alta_por_programa,
)
from .hitl import (
    ESTADO_PENDIENTE,
    ESTADO_REVISADO,
    MOTIVO_CERTEZA_BAJA,
    MOTIVO_MUESTREO_AUDITORIA,
    PRIORIDAD_ALTA,
    PRIORIDAD_BAJA,
    VERSION_HITL,
    ColaHitl,
    Correccion,
    Encolado,
    EntradaHitl,
    decidir_encolado,
    encolar_hitl,
    encolar_lote,
    seleccionado_para_auditoria,
)
from .engine import (
    ConclusionConAgente,
    ConclusionConBusqueda,
    concluir,
    concluir_caso,
    concluir_con_agente,
    concluir_con_busqueda,
    consolidar_caso,
    escalar_a_agente,
)

__all__ = [
    "concluir",
    "concluir_caso",
    "concluir_con_busqueda",
    "concluir_con_agente",
    "consolidar_caso",
    "ConclusionConBusqueda",
    "ConclusionConAgente",
    "Consolidacion",
    "consolidar",
    "es_certeza_alta_por_programa",
    # `escalar_a_agente` (API pública, firma del esqueleto de F0): recibe la
    # evidencia combinada y corre la pasada 2 por dentro. `escalar_veredicto` es
    # la variante de bajo nivel, que recibe un `ConclusionResult` ya calculado.
    "escalar_a_agente",
    "escalar_veredicto",
    # Cola HITL y muestreo de auditoría (F5/T-505, E-CONC-4 / ADR-004).
    "encolar_hitl",
    "encolar_lote",
    "decidir_encolado",
    "seleccionado_para_auditoria",
    "ColaHitl",
    "EntradaHitl",
    "Correccion",
    "Encolado",
    "VERSION_HITL",
    "PRIORIDAD_ALTA",
    "PRIORIDAD_BAJA",
    "MOTIVO_CERTEZA_BAJA",
    "MOTIVO_MUESTREO_AUDITORIA",
    "ESTADO_PENDIENTE",
    "ESTADO_REVISADO",
    "Agente",
    "AgenteOllama",
    "DecisionAgente",
    "EleccionAgente",
    "parsear_eleccion_agente",
    "VERSION_AGENTE",
    "AGENTE_ELIGIO",
    "AGENTE_SE_ABSTUVO",
    "AGENTE_ELECCION_INVALIDA",
    "AGENTE_FALLO",
    "AGENTE_NO_ESCALADO",
]

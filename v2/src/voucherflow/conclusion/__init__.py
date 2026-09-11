"""Módulo ``conclusion`` — reglas → agente → HITL (F5).

**Fase**: F5. En F0 se dejó el esqueleto con las firmas públicas
``concluir`` / ``escalar_a_agente`` / ``encolar_hitl``. **T-501** implementa
``concluir`` (la pasada 2 de reglas cruzadas sobre la evidencia combinada, que
produce el ``Decision`` del caso); ``escalar_a_agente`` (T-504) y
``encolar_hitl`` (T-505) siguen siendo esqueleto y se completan en sus tareas,
junto con la persistencia de ``CaseRecord`` (T-506).
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
from .engine import (
    ConclusionConAgente,
    ConclusionConBusqueda,
    concluir,
    concluir_caso,
    concluir_con_agente,
    concluir_con_busqueda,
    consolidar_caso,
    encolar_hitl,
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
    "encolar_hitl",
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

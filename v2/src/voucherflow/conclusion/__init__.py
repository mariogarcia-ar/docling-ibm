"""Módulo ``conclusion`` — reglas → agente → HITL (F5).

**Fase**: F5. En F0 se dejó el esqueleto con las firmas públicas
``concluir`` / ``escalar_a_agente`` / ``encolar_hitl``. **T-501** implementa
``concluir`` (la pasada 2 de reglas cruzadas sobre la evidencia combinada, que
produce el ``Decision`` del caso); ``escalar_a_agente`` (T-504) y
``encolar_hitl`` (T-505) siguen siendo esqueleto y se completan en sus tareas,
junto con la persistencia de ``CaseRecord`` (T-506).
"""

from __future__ import annotations

from .consolidacion import (
    Consolidacion,
    consolidar,
    es_certeza_alta_por_programa,
)
from .engine import (
    ConclusionConBusqueda,
    concluir,
    concluir_caso,
    concluir_con_busqueda,
    consolidar_caso,
    encolar_hitl,
    escalar_a_agente,
)

__all__ = [
    "concluir",
    "concluir_caso",
    "concluir_con_busqueda",
    "consolidar_caso",
    "ConclusionConBusqueda",
    "Consolidacion",
    "consolidar",
    "es_certeza_alta_por_programa",
    "escalar_a_agente",
    "encolar_hitl",
]

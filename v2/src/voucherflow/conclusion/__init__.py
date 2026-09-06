"""Módulo ``conclusion`` (esqueleto — F5) — reglas → agente → HITL.

**Fase**: F5. En F0 se deja el esqueleto con las firmas públicas
``concluir`` / ``escalar_a_agente`` / ``encolar_hitl``. La lógica se completa
en F5 (engine, agent, hitl) sobre los contratos de ``schemas/`` (F0).
"""

from __future__ import annotations

from .engine import concluir, encolar_hitl, escalar_a_agente

__all__ = ["concluir", "escalar_a_agente", "encolar_hitl"]

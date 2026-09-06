"""Módulo ``conclusion`` (esqueleto — F5) — reglas → agente → HITL.

**Fase**: F5 (refactor conclusión). En F0 solo se deja el **esqueleto** con las
firmas públicas y referencias a los contratos de ``schemas/``, sin lógica de
negocio ni acoplamiento a scripts de ``v1/``.

Responsabilidades (doc 03 §4.5, `CONC.md`): reglas cruzadas sobre la evidencia
combinada (negocio + fast-fail + conflicto R7), detección de gaps con límite
(hook ARCA, ADR-003), consolidación con certeza **por etapa** (programa → alta;
agente IA → baja + HITL), cola HITL con muestreo de auditoría (ADR-004) y
persistencia de ``CaseRecord`` (ADR-005). El agente solo elige entre candidatos
no descartados (ADR-008).
"""

from __future__ import annotations

from ..schemas.evidence import CombinedEvidence
from ..schemas.result import VoucherResult


def concluir(evidencia: CombinedEvidence) -> VoucherResult:
    """Consolida la evidencia combinada en un ``VoucherResult`` (F5).

    Esqueleto F0 — se implementa en F5 (módulo ``conclusion.engine``).
    """
    raise NotImplementedError("concluir(): se implementa en F5 (T-501..T-504).")


def escalar_a_agente(evidencia: CombinedEvidence, candidatos: list[str]) -> VoucherResult:
    """Escala a agente IA solo con los candidatos restantes (F5, ADR-008).

    Esqueleto F0 — se implementa en F5 (módulo ``conclusion.agent``).
    """
    raise NotImplementedError("escalar_a_agente(): se implementa en F5 (T-504).")


def encolar_hitl(resultado: VoucherResult) -> VoucherResult:
    """Encola a revisión humana los casos de certeza baja + muestreo (F5).

    Esqueleto F0 — se implementa en F5 (módulo ``conclusion.hitl``).
    """
    raise NotImplementedError("encolar_hitl(): se implementa en F5 (T-505).")


__all__ = ["concluir", "escalar_a_agente", "encolar_hitl"]

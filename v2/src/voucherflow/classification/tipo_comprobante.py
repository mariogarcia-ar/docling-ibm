"""Módulo ``classification`` (esqueleto — F3) — tipo/letra + clasificación contable.

**Fase**: F3 (refactor clasificación). En F0 solo se deja el **esqueleto** con
los contratos y firmas públicas, sin lógica de negocio ni acoplamiento a
scripts de ``v1/`` (``classification_pipeline.py``, prompts 01/02/03, ``-M
11.1``).

Responsabilidades (doc 03 §4.3, `CLAS.md`): decidir el tipo/letra de
comprobante (A/B/C/M/E, 090/099) mediante el **motor de reglas R1-R7 en código**
(ADR-006) sobre la evidencia de los flujos VLM/LLM, y resolver la cadena
contable 01 → 02 → 03 (centro de costo → macro categoría → concepto/código).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass
class TipoComprobanteResult:
    """Salida de la clasificación de tipo/letra (E-CLAS-1).

    ``letra``: A/B/C/M/E/090/099 (o ``None`` si no se concluye).
    ``certeza`` y ``origen``: regla de oro (programa → alta).
    ``reglas_aplicadas``: ids R1..R7 disparadas (trazabilidad E-CONC-5).
    """

    letra: str | None = None
    certeza: str | None = None  # alta | baja
    origen: str | None = None  # programa | agente_ia | hitl
    candidatos_descartados: list[str] = field(default_factory=list)
    candidatos_restantes: list[str] = field(default_factory=list)
    reglas_aplicadas: list[str] = field(default_factory=list)
    alertas: list[dict[str, Any]] = field(default_factory=list)


@dataclass
class ClasificacionContableResult:
    """Salida de la cadena contable 01 → 02 → 03 (E-CLAS-2)."""

    centro_costo: str | None = None
    macro_categoria: str | None = None
    concepto: str | None = None
    codigo: str | None = None
    reglas_aplicadas: list[str] = field(default_factory=list)


def clasificar_tipo_comprobante(evidencia: Any) -> TipoComprobanteResult:
    """Decide tipo/letra por reglas R1-R7 sobre evidencia combinada (F3).

    Esqueleto F0 — se implementa en F3 (módulo ``classification`` +
    ``rules/tipo_comprobante_rules.py``).
    """
    raise NotImplementedError("clasificar_tipo_comprobante(): se implementa en F3 (T-301..T-303).")


def clasificar_contable(
    markdown: str, condicion_impositiva: str | None = None
) -> ClasificacionContableResult:
    """Resuelve la cadena contable 01 → 02 → 03 (F3).

    Esqueleto F0 — se implementa en F3 (T-304).
    """
    raise NotImplementedError("clasificar_contable(): se implementa en F3 (T-304).")


__all__ = ["TipoComprobanteResult", "ClasificacionContableResult", "clasificar_tipo_comprobante", "clasificar_contable"]

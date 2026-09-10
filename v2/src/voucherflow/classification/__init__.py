"""Módulo ``classification`` — tipo/letra (F3/T-301) + cadena contable (F3/T-304).

**Fase**: F3 (refactor clasificación). F0 dejó el esqueleto con los contratos
``TipoComprobanteResult`` y ``ClasificacionContableResult``. **T-301** implementa
``clasificar_tipo_comprobante()`` (motor de reglas R1-R7 en código, ADR-006);
``clasificar_contable()`` se implementa en **T-304**.
"""

from __future__ import annotations

from .tipo_comprobante import (
    ClasificacionContableResult,
    TipoComprobanteResult,
    clasificar_contable,
    clasificar_tipo_comprobante,
)

__all__ = [
    "TipoComprobanteResult",
    "ClasificacionContableResult",
    "clasificar_tipo_comprobante",
    "clasificar_contable",
]

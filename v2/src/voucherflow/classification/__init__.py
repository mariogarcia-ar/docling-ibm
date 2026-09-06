"""Módulo ``classification`` (esqueleto — F3) — tipo/letra + cadena contable.

**Fase**: F3 (refactor clasificación). En F0 se deja el esqueleto con los
contratos ``TipoComprobanteResult`` y ``ClasificacionContableResult``. La
lógica de reglas R1-R7 y la cadena 01→02→03 se implementa en F3.
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

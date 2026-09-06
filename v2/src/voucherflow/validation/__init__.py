"""Módulo ``validation`` (esqueleto — F2) — gate qween doble-paso.

**Fase**: F2 (refactor qween). En F0 se deja el esqueleto con el contrato de
salida ``ValidationResult`` y el enumerado ``VeredictoGate``. La lógica de
vistas (rápida/revisión/fiel) se implementa en F2.
"""

from __future__ import annotations

from .qween import ValidationResult, VeredictoGate, validar_comprobante

__all__ = ["ValidationResult", "VeredictoGate", "validar_comprobante"]

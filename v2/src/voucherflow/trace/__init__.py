"""Módulo ``trace`` (esqueleto — F0/F5) — trazabilidad ``CaseRecord``.

**Fase**: F0 deja el contrato ``CaseRecord`` congelado (``schemas/result.py``)
y el esqueleto del registrador. La persistencia completa se implementa en F5
(T-506).
"""

from __future__ import annotations

from .recorder import CaseRecorder, sidecar_para

__all__ = ["CaseRecorder", "sidecar_para"]

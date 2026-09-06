"""Módulo ``rules`` (esqueleto — F0/F3/F5) — motor de reglas declarativo.

**Fase**: F0 deja el motor declarativo (``Rule`` + ``Registry``) como base del
ADR-006 (reglas en código). Las reglas concretas (R1-R7) se migran en F3 y las
cruzadas en F5.
"""

from __future__ import annotations

from .registry import Registry, Rule

__all__ = ["Rule", "Registry"]

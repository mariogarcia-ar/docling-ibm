"""Módulo ``extraction`` (esqueleto — F4) — flujos VLM + LLM.

**Fase**: F4. En F0 se deja el esqueleto con las firmas públicas
``flujo_vlm`` / ``flujo_llm`` / ``combinar_evidencia``. La implementación se
completa en F4 usando ``OllamaClient`` (F0) y los schemas de evidencia (F0).
"""

from __future__ import annotations

from .flows import combinar_evidencia, flujo_llm, flujo_vlm

__all__ = ["flujo_vlm", "flujo_llm", "combinar_evidencia"]

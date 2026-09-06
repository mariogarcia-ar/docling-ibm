"""Módulo ``extraction`` (esqueleto — F4) — flujos VLM + LLM con evidencia.

**Fase**: F4 (refactor extracción). En F0 solo se deja el **esqueleto** con las
firmas públicas y los contratos (basados en ``schemas/evidence.py``), sin
lógica de negocio ni acoplamiento a scripts de ``v1/``
(``document_extraction.py`` kvi/kvg, ``extraction_pipeline.py`` 10/11).

Responsabilidades (doc 03 §4.4, `EXT.md`): correr **en paralelo** el flujo VLM
(lee la imagen) y el flujo LLM (lee el OCR/Markdown), cada uno devolviendo
``SourceEvidence`` con el contrato de F0, normalizar campos clave
(CUIT/fechas/montos/ítems) y combinar la evidencia con resolución por campo
(ADR-002).
"""

from __future__ import annotations

from ..schemas.evidence import CombinedEvidence, SourceEvidence


def flujo_vlm(origen: str, **kwargs) -> SourceEvidence:
    """Flujo VLM: lee la imagen directo y devuelve ``SourceEvidence`` (F4).

    Esqueleto F0 — se implementa en F4 (T-401) con ``OllamaClient``.
    """
    raise NotImplementedError("flujo_vlm(): se implementa en F4 (T-401).")


def flujo_llm(markdown: str, **kwargs) -> SourceEvidence:
    """Flujo LLM: lee el OCR/Markdown y devuelve ``SourceEvidence`` (F4).

    Esqueleto F0 — se implementa en F4 (T-401) con ``OllamaClient``.
    """
    raise NotImplementedError("flujo_llm(): se implementa en F4 (T-401).")


def combinar_evidencia(documento_id: str, fuentes: list[SourceEvidence]) -> CombinedEvidence:
    """Combina la evidencia de las fuentes con resolución por campo (F4).

    Esqueleto F0 — se implementa en F4 (T-404) aplicando la precedencia
    declarativa de ADR-002.
    """
    raise NotImplementedError("combinar_evidencia(): se implementa en F4 (T-404).")


__all__ = ["flujo_vlm", "flujo_llm", "combinar_evidencia"]

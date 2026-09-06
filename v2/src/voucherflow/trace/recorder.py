"""Módulo ``trace`` (esqueleto — F0/F5) — trazabilidad y persistencia.

**Fase**: F0 congela el contrato ``CaseRecord`` (en ``schemas/result.py``,
ADR-005) y deja aquí el esqueleto del registrador/persistencia. La
implementación completa se hace en F5 (T-506): persistencia como **JSON
sidecar** por documento (patrón de v1, ampliado con ``trazabilidad``) y, en una
fase posterior, índice/store SQLite para consultas agregadas de auditoría.
"""

from __future__ import annotations

from pathlib import Path

from ..schemas.result import CaseRecord


class CaseRecorder:
    """Registra y persiste la trazabilidad de un caso (``CaseRecord``).

    Esqueleto F0 — se implementa en F5/T-506 (escritura atómica de sidecar
    ``<documento>.case.json``). El contrato ``CaseRecord`` ya está congelado en
    ``schemas/result.py``.
    """

    def __init__(self, dir_salida: str | Path = ".") -> None:
        self.dir_salida = Path(dir_salida)

    def registrar(self, caso: CaseRecord) -> Path:
        """Persiste un ``CaseRecord`` como JSON sidecar (F5).

        Esqueleto F0 — se implementa en F5/T-506.
        """
        raise NotImplementedError("CaseRecorder.registrar(): se implementa en F5 (T-506).")


def sidecar_para(documento_id: str) -> str:
    """Nombre de archivo sidecar canónico para un documento (helper).

    Patrón heredado de v1 (``_pipeline.json`` / ``.case.json``), útil para
    checkpoints y reanudación (F6/T-602).
    """
    return f"{documento_id}.case.json"


__all__ = ["CaseRecorder", "sidecar_para"]

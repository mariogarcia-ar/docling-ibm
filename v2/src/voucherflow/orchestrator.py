"""Orquestador del pipeline ``voucherflow`` — esqueleto (F0).

**Fase**: F0 deja el esqueleto del orquestador (doc 03 §3/§4 y `ORCH-CLI.md`).
La secuencia completa (processing → validation → classification → extraction →
conclusion) y el encadenamiento de módulos se implementa en F1–F5. Este módulo
no debe acoplarse a scripts de ``v1/``.
"""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class PipelineResult:
    """Resultado de una corrida del pipeline (resumen para el cliente).

    F0 esboza el contrato de salida; F5/F6 lo pueblan con el ``VoucherResult``
    y la trazabilidad persistida (``CaseRecord`` sidecar).
    """

    documento_id: str | None = None
    ok: bool = False
    etapas_completadas: list[str] = field(default_factory=list)
    resumen: dict = field(default_factory=dict)
    error: str | None = None


class PipelineOrchestrator:
    """Orquesta las etapas del pipeline para un documento o lote.

    Esqueleto F0 — la implementación de las etapas se agrega fase a fase
    (F1 processing, F2 validation, F3 classification, F4 extraction,
    F5 conclusion + HITL). El orquestador **no** debe conocer la lógica de
    cada módulo: los invoca por su interfaz pública.
    """

    def __init__(self) -> None:
        self._etapas: list[str] = []

    # ------------------------------------------------------------------
    def registrar_etapa(self, nombre: str) -> None:
        """Registra una etapa en el orden de ejecución (usado en F1–F5)."""
        if nombre not in self._etapas:
            self._etapas.append(nombre)

    # ------------------------------------------------------------------
    def ejecutar(self, origen: str) -> PipelineResult:
        """Ejecuta el pipeline completo sobre un documento (F5/F6).

        Esqueleto F0 — lanza ``NotImplementedError`` hasta que existan las
        etapas (F1–F5).
        """
        raise NotImplementedError(
            "PipelineOrchestrator.ejecutar(): se implementa en F5/F6 a medida que "
            "las etapas (F1–F5) queden listas. Etapas registradas: "
            f"{self._etapas or '(ninguna)'}."
        )


__all__ = ["PipelineResult", "PipelineOrchestrator"]

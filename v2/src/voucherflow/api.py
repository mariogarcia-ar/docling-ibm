"""API de alto nivel (facade) de ``voucherflow`` — esqueleto (F0).

**Fase**: F0 deja el esqueleto de la fachada pública de la librería. La
implementación de cada operación se completa cuando su módulo de capacidad
exista (F1–F5): ``process`` (F1), ``validate`` (F2), ``classify`` (F3),
``extract`` (F4) y ``run``/``concluir`` (F5).

El objetivo de exponer esta fachada desde F0 es **fijar la API pública** de la
librería (E-LIB-1: "librería primero, cliente después") para que el cliente
CLI/API (F6) y los notebooks consuman una superficie estable, sin conocer los
módulos internos.

Contratos que expone (doc 03 §9): ``ProcessedDocument`` (processing),
``ValidationResult`` (validation), ``VoucherResult`` / ``CombinedEvidence``
(conclusion), ``EvidenceField`` / ``SourceEvidence`` (extraction/classification).
"""

from __future__ import annotations

from .schemas.result import VoucherResult

# ---------------------------------------------------------------------------
# Errores públicos de dominio (E-LIB-1)
# ---------------------------------------------------------------------------


class VoucherflowError(RuntimeError):
    """Error base de la librería, con mensaje claro en español."""


class DocumentoNoProcesableError(VoucherflowError):
    """El documento no superó el gate de procesabilidad (F1/F2)."""


class ContratoError(VoucherflowError):
    """Una respuesta de modelo no cumple el contrato de evidencia (E-LIB-2).

    Se usa en F3/F4 cuando el JSON del VLM/LLM no valida contra
    ``schemas/evidence.py``; el mensaje incluye el detalle de pydantic.
    """


# ---------------------------------------------------------------------------
# Fachada (esqueletos de F1–F5; firmas públicas estables)
# ---------------------------------------------------------------------------


def process(origen: str) -> "ProcessedDocument":
    """Procesa un documento a representación Markdown+boxes (F1).

    Esqueleto F0 — se implementa en F1 (módulo ``processing``).
    """
    raise NotImplementedError("process(): se implementa en F1 (módulo processing).")


def validate(origen: str, quick: bool = True) -> "ValidationResult":
    """Gate "¿es comprobante?" con doble paso qween (F2).

    Esqueleto F0 — se implementa en F2 (módulo ``validation``).
    """
    raise NotImplementedError("validate(): se implementa en F2 (módulo validation).")


def classify(markdown: str, condicion_impositiva: str | None = None) -> VoucherResult:
    """Clasifica tipo/letra + contable (F3).

    Esqueleto F0 — se implementa en F3 (módulo ``classification``).
    """
    raise NotImplementedError("classify(): se implementa en F3 (módulo classification).")


def extract(origen: str, mode: str = "kvi") -> "CombinedEvidence":
    """Extrae evidencia VLM+LLM de un documento (F4).

    Esqueleto F0 — se implementa en F4 (módulo ``extraction``).
    """
    raise NotImplementedError("extract(): se implementa en F4 (módulo extraction).")


def run(origen: str) -> VoucherResult:
    """Pipeline completo document → VoucherResult (F5).

    Esqueleto F0 — se implementa en F5 (módulo ``conclusion`` + orquestador).
    """
    raise NotImplementedError("run(): se implementa en F5 (módulo conclusion/orquestador).")


__all__ = [
    "VoucherflowError",
    "DocumentoNoProcesableError",
    "ContratoError",
    "process",
    "validate",
    "classify",
    "extract",
    "run",
]

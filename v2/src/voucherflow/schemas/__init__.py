"""Schemas de contrato de la librería ``voucherflow``.

Expone los modelos pydantic del contrato de evidencia (``evidence.py``) y de
resultados/trazabilidad (``result.py``), congelados en F0 (T-001). Consumidos
por F3/F4/F5.
"""

from __future__ import annotations

from .evidence import (
    CRITERIO_CAMBIO,
    SCHEMA_VERSION as EVIDENCE_SCHEMA_VERSION,
    CampoCombinado,
    Certeza,
    CombinedEvidence,
    Decision,
    EstadoResultado,
    EvidenceField,
    FieldResolution,
    Fuente,
    Origen,
    SourceEvidence,
    TipoComprobante,
    nueva_meta,
)
from .result import (
    SCHEMA_VERSION as RESULT_SCHEMA_VERSION,
    CaseRecord,
    CampoExtraido,
    ClasificacionContable,
    HitlDecision,
    RegistroEtapa,
    VoucherResult,
)

#: Versión consolidada del contrato (evidencia + resultado).
SCHEMA_VERSION = "1.0.0"

__all__ = [
    "SCHEMA_VERSION",
    "EVIDENCE_SCHEMA_VERSION",
    "RESULT_SCHEMA_VERSION",
    "CRITERIO_CAMBIO",
    # evidence
    "Fuente",
    "Certeza",
    "Origen",
    "EstadoResultado",
    "TipoComprobante",
    "EvidenceField",
    "SourceEvidence",
    "FieldResolution",
    "CampoCombinado",
    "Decision",
    "CombinedEvidence",
    "nueva_meta",
    # result
    "HitlDecision",
    "CampoExtraido",
    "ClasificacionContable",
    "VoucherResult",
    "RegistroEtapa",
    "CaseRecord",
]

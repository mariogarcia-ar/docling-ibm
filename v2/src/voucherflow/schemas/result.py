"""Resultados consolidados y trazabilidad (v2) — congelado en F0 (T-001).

Define el resultado final que devuelve la librería (``VoucherResult``, glosario
§2.4) y el registro de trazabilidad completa por caso (``CaseRecord``, requisito
de auditoría E-CONC-5 / **ADR-005**).

``CaseRecord`` es el contrato que el módulo ``trace/`` persiste en F5 (T-506)
como JSON sidecar + store posterior; aquí se congela su *shape* (schema) para
que F3/F4/F5 produzcan ya los datos que la auditoría exige: versión de prompt,
modelo, evidencia por fuente, reglas disparadas y quién decidió.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, field_validator

from .evidence import (
    Certeza,
    CombinedEvidence,
    EstadoResultado,
    Fuente,
    Origen,
    SourceEvidence,
    nueva_meta,
)

#: Versión del contrato de resultados (igual al de evidencia por ahora).
SCHEMA_VERSION = "1.0.0"


class HitlDecision(BaseModel):
    """Decisión/estado de la revisión humana (HITL) para un caso.

    ``requerido`` es ``True`` cuando la certeza es baja (origen agente_ia) o el
    caso cae en el muestreo de auditoría de certeza alta (ADR-004). ``prioridad``
    es "alta" (revisión obligatoria) o "baja" (muestreo de auditoría).
    """

    model_config = ConfigDict(extra="forbid")

    requerido: bool = False
    prioridad: str = Field(default="baja", description="alta (revisión obligatoria) | baja (muestreo auditoría).")
    estado: str = Field(default="no_aplica", description="no_aplica | pendiente | revisado.")
    correccion: dict[str, Any] = Field(default_factory=dict, description="Corrección humana registrada (feedback).")

    @field_validator("prioridad")
    @classmethod
    def _prioridad_valida(cls, v: str) -> str:
        if v not in {"alta", "baja"}:
            raise ValueError("prioridad debe ser 'alta' o 'baja'")
        return v

    @field_validator("estado")
    @classmethod
    def _estado_valido(cls, v: str) -> str:
        if v not in {"no_aplica", "pendiente", "revisado"}:
            raise ValueError("estado debe ser 'no_aplica', 'pendiente' o 'revisado'")
        return v


class CampoExtraido(BaseModel):
    """Campo extraído normalizado para el resultado plano (glosario §2.4)."""

    model_config = ConfigDict(extra="forbid")

    campo: str
    valor: str | float | int | bool | None = None
    normalizado: bool = Field(default=True, description="Indica si pasó por normalización (CUIT/fechas/montos).")


class ClasificacionContable(BaseModel):
    """Clasificación contable de referencia (cadena 01→02→03, E-CLAS-2)."""

    model_config = ConfigDict(extra="forbid")

    centro_costo: str | None = None
    macro_categoria: str | None = None
    concepto: str | None = None
    codigo: str | None = None
    condicion_impositiva: str | None = Field(default=None, description="21 | 10_5 | 27 | 2_5 | exento_no_gravado.")


class VoucherResult(BaseModel):
    """Resultado consolidado final del pipeline (glosario §2.4, contrato §9).

    Es el objeto tipado que devuelve la API/CLI (E-LIB-1). Contiene el estado
    (aprobado/rechazado/revision), el tipo de comprobante, la certeza y origen
    (por etapa), los campos extraídos, la clasificación contable, la evidencia
    combinada y el estado HITL.
    """

    model_config = ConfigDict(extra="forbid")

    documento_id: str = Field(..., min_length=1)
    estado: EstadoResultado
    tipo_comprobante: str | None = Field(default=None, description="Letra/tipo final: A/B/C/M/E/090/099.")
    certeza: Certeza | None = None
    origen: Origen | None = None
    campos_extraidos: dict[str, Any] = Field(default_factory=dict, description="Campos planos normalizados por campo.")
    clasificacion_contable: ClasificacionContable | None = None
    evidencia: CombinedEvidence | None = None
    hitl: HitlDecision = Field(default_factory=HitlDecision)
    trazabilidad: dict[str, Any] = Field(default_factory=dict, description="Ref. a CaseRecord / resumen de trazabilidad.")

    @field_validator("documento_id")
    @classmethod
    def _doc_id_no_vacio(cls, v: str) -> str:
        if not v.strip():
            raise ValueError("documento_id es obligatorio y no puede ser vacío")
        return v.strip()

    @field_validator("tipo_comprobante")
    @classmethod
    def _tipo_acepta_none(cls, v: str | None) -> str | None:
        if v is None:
            return None
        val = v.strip()
        if not val:
            raise ValueError("tipo_comprobante no puede ser vacío (usar null si no aplica)")
        return val


class RegistroEtapa(BaseModel):
    """Registro de una etapa del pipeline dentro de ``CaseRecord``.

    Guarda qué se ejecutó (etapa), qué modelo/prompt se usó (con versión), la
    evidencia que produjo y el resultado de la etapa.
    """

    model_config = ConfigDict(extra="forbid")

    etapa: str = Field(..., description="p. ej. 'processing', 'validation', 'classification', 'extraction', 'conclusion'.")
    modelo: str | None = Field(default=None, description="Modelo usado (p. ej. 'qwen2.5vl:3b', 'docling').")
    version_prompt: str | None = Field(default=None, description="Versión/hash del prompt (p. ej. '11.1@sha:abc123').")
    evidencia_por_fuente: dict[str, SourceEvidence] = Field(default_factory=dict)
    reglas_disparadas: list[str] = Field(default_factory=list)
    detalle: dict[str, Any] = Field(default_factory=dict)


class CaseRecord(BaseModel):
    """Trazabilidad completa por caso (ADR-005 / E-CONC-5).

    Registro auditable de todo lo que ocurrió con un documento: identidad,
    versiones (contrato + prompts), modelos por etapa, evidencia por fuente,
    reglas disparadas, quién decidió (origen) y el resultado consolidado.
    En el MVP se persiste como JSON sidecar (módulo ``trace``, F5/T-506); el
    store SQLite para consultas agregadas se agrega en una fase posterior.

    Alcance (decisión ADR-005): un ``CaseRecord`` **por documento procesado**,
    versionado con ``schema_version`` para poder auditar casos producidos con
    versiones previas del contrato.
    """

    model_config = ConfigDict(extra="forbid")

    documento_id: str = Field(..., min_length=1)
    archivo: str | None = Field(default=None, description="Ruta del archivo de origen (referencial).")
    schema_version: str = Field(default=SCHEMA_VERSION, description="Versión del contrato con que se produjo el caso.")
    timestamp: str = Field(default_factory=lambda: datetime.now(timezone.utc).isoformat())
    version_prompt: dict[str, str] = Field(default_factory=dict, description="Prompt → versión/hash usados.")
    modelo_por_etapa: dict[str, str] = Field(default_factory=dict, description="Etapa → modelo usado.")
    evidencia_por_fuente: dict[str, SourceEvidence] = Field(default_factory=dict, description="Fuente → evidencia completa.")
    reglas_disparadas: list[str] = Field(default_factory=list)
    quien_decidio: Origen | None = Field(default=None, description="programa | agente_ia | hitl (quién decidió el caso).")
    etapas: list[RegistroEtapa] = Field(default_factory=list, description="Registro pormenorizado por etapa.")
    resultado: VoucherResult | None = None

    @field_validator("documento_id")
    @classmethod
    def _doc_id_no_vacio(cls, v: str) -> str:
        if not v.strip():
            raise ValueError("documento_id es obligatorio y no puede ser vacío")
        return v.strip()


__all__ = [
    "SCHEMA_VERSION",
    "HitlDecision",
    "CampoExtraido",
    "ClasificacionContable",
    "VoucherResult",
    "RegistroEtapa",
    "CaseRecord",
    "Fuente",
    "nueva_meta",
]

"""Contrato de evidencia del sistema documental (v2) — congelado en F0 (T-001).

Este módulo es la **base del contrato central** definido en
``v2/docs/plan/00-glosario.md`` §2 y fijado como borrador pydantic en
``v2/docs/plan/03-arquitectura-solucion.md`` §6 (decisión **ADR-001**: schema
estricto pydantic por campo). Es el esquema que **todos** los flujos
(VLM / LLM / reglas de programa / ARCA / HITL) deben producir y consumir para
poder comparar evidencia programáticamente.

El presente archivo es el **contrato congelado** que consumen las fases
F3 (clasificación), F4 (extracción) y F5 (conclusión). Cualquier modificación
posterior debe respetar el criterio de cambio versionado definido en
:data:`SCHEMA_VERSION` y :data:`CRITERIO_CAMBIO`.

Modelado
--------
La combinación por campo de :class:`CombinedEvidence` se modela con un modelo
auxiliar :class:`CampoCombinado` (en lugar del ``dict`` anidado del borrador)
porque:

1. **Validación temprana (E-LIB-2)**: pydantic valida de forma declarativa cada
   ``vlm`` / ``llm`` / ``resolucion`` presente y rechaza con un error de
   contrato claro si un valor está mal formado (no un ``dict`` opaco).
2. **Compatibilidad conceptual con el glosario**: la serialización conserva el
   shape ``{ campo: { "vlm": …, "llm": …, "resolucion": … } }`` (mismo
   contrato JSON del glosario §2.3).
3. **Rigidez del ``dict[str, …]`` complejo**: con pydantic un tipo
   ``dict[Fuente, EvidenceField]`` no es expresable directamente como clave de
   diccionario (los enums como clave exigen configuración adicional) y complica
   el mensaje de error; un modelo dedicado es más limpio y testeable.
"""

from __future__ import annotations

from datetime import datetime, timezone
from enum import Enum
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

# ---------------------------------------------------------------------------
# Criterio de cambio versionado del contrato
# ---------------------------------------------------------------------------

#: Versión semántica del contrato. Se incrementa **mayor** ante cambios
#: incompatibles (renombrar/quitar un campo obligatorio, cambiar tipos o
#: valores de un enum) y **menor** ante adiciones compatibles (campos nuevos
#: con default). Congelada en F0; la consumen F3/F4/F5 y la trazabilidad
#: (``CaseRecord``) para saber con qué versión de contrato se produjo un caso.
SCHEMA_VERSION = "1.0.0"

#: Criterio de cambio documentado (auditable). Cualquier PR que toque este
#: módulo debe: (1) justificar el cambio contra el plan, (2) actualizar
#: ``SCHEMA_VERSION`` según semver, y (3) actualizar los fixtures de
#: ``tests/golden`` y los tests de contrato. Si el cambio afecta a los prompts,
#: actualizar también el hash/versión de prompt registrado en ``CaseRecord``.
CRITERIO_CAMBIO = (
    "Contrato de evidencia congelado en F0 (ADR-001/002). Cambios incompatibles "
    "requieren bump mayor de SCHEMA_VERSION + revisión de F3/F4/F5 + nota en "
    "04-decisiones-abiertas-adr.md. Adiciones compatibles: bump menor."
)


# ---------------------------------------------------------------------------
# Enumerados del dominio
# ---------------------------------------------------------------------------


class Fuente(str, Enum):
    """Origen de una evidencia. Valores exactos del glosario §2.1."""

    vlm = "vlm"
    llm = "llm"
    programa = "programa"
    arca = "arca"
    hitl = "hitl"


class Certeza(str, Enum):
    """Nivel de certeza de una decisión. Solo dos valores (glosario §2.3)."""

    alta = "alta"
    baja = "baja"


class Origen(str, Enum):
    """Etapa que resolvió el caso. La certeza se deriva de esta etapa."""

    programa = "programa"
    agente_ia = "agente_ia"
    hitl = "hitl"


class EstadoResultado(str, Enum):
    """Estado consolidado de un ``VoucherResult`` (glosario §2.4)."""

    aprobado = "aprobado"
    rechazado = "rechazado"
    revision = "revision"


class TipoComprobante(str, Enum):
    """Letras/tipos de comprobante que el sistema clasifica (E-CLAS-1)."""

    factura_a = "A"
    factura_b = "B"
    factura_c = "C"
    factura_m = "M"
    factura_e = "E"
    tique_090 = "090"
    tique_099 = "099"


# ---------------------------------------------------------------------------
# Unidad de evidencia
# ---------------------------------------------------------------------------


class EvidenceField(BaseModel):
    """Unidad mínima de evidencia: un valor extraído para un campo.

    Es el átomo que VLM/LLM/programa/ARCA/HITL producen con el **mismo
    esquema** (ADR-001) para que las reglas puedan compararlos
    programáticamente. El ``fragmento_sustento`` da trazabilidad de *de dónde
    salió* el valor (auditoría E-CONC-5).
    """

    model_config = ConfigDict(extra="forbid")

    campo: str = Field(..., min_length=1, description="Nombre del campo (p. ej. 'tipo_comprobante').")
    valor: str | float | int | bool | None = Field(..., description="Valor extraído (puede ser nulo si no se encontró).")
    fuente: Fuente = Field(..., description="Origen de la evidencia.")
    fragmento_sustento: str = Field(..., min_length=1, description="Fragmento textual/visual que sustenta el valor.")
    confianza_fuente: str = Field(default="media", description="Autoevaluación del flujo. NO es la certeza final (glosario).")
    meta: dict[str, Any] = Field(default_factory=dict, description="modelo, version_prompt, timestamp, etc.")

    @field_validator("campo")
    @classmethod
    def _campo_no_vacio(cls, v: str) -> str:
        # E-LIB-2: error de contrato claro, no assert silencioso.
        if not v.strip():
            raise ValueError("campo no puede ser vacío ni solo espacios")
        return v.strip()

    @field_validator("fragmento_sustento")
    @classmethod
    def _fragmento_no_vacio(cls, v: str) -> str:
        if not v.strip():
            raise ValueError("fragmento_sustento es obligatorio y no puede ser vacío (E-LIB-2)")
        return v.strip()

    @field_validator("confianza_fuente")
    @classmethod
    def _confianza_normalizada(cls, v: str) -> str:
        val = v.strip().lower()
        if not val:
            raise ValueError("confianza_fuente no puede ser vacío")
        return val


class SourceEvidence(BaseModel):
    """Evidencia emitida por **una sola** fuente (pasada de reglas raw).

    ``valida`` es el resultado de aplicar las reglas raw (pasada 1) sobre esta
    fuente en particular; ``reglas_aplicadas`` y ``debilidades`` registran qué
    se evaluó y qué flaquea (ADR-001/006).
    """

    model_config = ConfigDict(extra="forbid")

    fuente: Fuente
    campos: dict[str, EvidenceField] = Field(default_factory=dict)
    valida: bool = Field(default=True, description="Resultado de la pasada 1 (reglas raw) sobre esta fuente.")
    reglas_aplicadas: list[str] = Field(default_factory=list)
    debilidades: list[str] = Field(default_factory=list)


class FieldResolution(BaseModel):
    """Resolución de un desacuerdo entre fuentes para un campo (ADR-002).

    ``ganador`` es la fuente que prevalece; ``regla`` identifica la regla de
    precedencia (p. ej. ``PREC_1``) y ``motivo`` el porqué, para auditoría.
    """

    model_config = ConfigDict(extra="forbid")

    ganador: Fuente | None = None
    regla: str | None = Field(default=None, description="p. ej. 'PREC_1' (tabla de precedencia ADR-002).")
    motivo: str | None = Field(default=None, description="Justificación legible de la resolución.")


class CampoCombinado(BaseModel):
    """Un campo con sus lecturas por fuente y la resolución del desacuerdo.

    Modelo auxiliar de :class:`CombinedEvidence.campos`. Mantiene el shape
    conceptual del glosario ``{ campo: { vlm, llm, resolucion } }`` pero con
    validación declarativa por cada lado (y soporte para programa/arca/hitl).
    """

    model_config = ConfigDict(extra="forbid")

    vlm: EvidenceField | None = None
    llm: EvidenceField | None = None
    programa: EvidenceField | None = Field(default=None, description="Evidencia computada por reglas de programa (si aplica).")
    arca: EvidenceField | None = Field(default=None, description="Evidencia de padrón ARCA/WSCDC (si se consultó).")
    hitl: EvidenceField | None = Field(default=None, description="Corrección/confirmación humana (si aplica).")
    resolucion: FieldResolution | None = None

    def fuentes_presentes(self) -> list[Fuente]:
        """Fuentes con evidencia cargada en este campo (para reglas)."""
        out: list[Fuente] = []
        for src, ev in (
            (Fuente.vlm, self.vlm),
            (Fuente.llm, self.llm),
            (Fuente.programa, self.programa),
            (Fuente.arca, self.arca),
            (Fuente.hitl, self.hitl),
        ):
            if ev is not None:
                out.append(src)
        return out


class Decision(BaseModel):
    """Decisión de conclusión del caso (E-CONC-1).

    Registra si el código concluyó (o escaló a agente), la certeza y origen
    resultantes (regla de oro: por etapa), los candidatos descartados/restantes
    y las reglas aplicadas.
    """

    model_config = ConfigDict(extra="forbid")

    concluye: bool
    certeza: Certeza = Field(..., description="alta (programa) | baja (agente).")
    origen: Origen = Field(..., description="programa | agente_ia | hitl.")
    candidatos_descartados: list[str] = Field(default_factory=list)
    candidatos_restantes: list[str] = Field(default_factory=list)
    reglas_aplicadas: list[str] = Field(default_factory=list)
    alertas: list[dict[str, Any]] = Field(default_factory=list)


class CombinedEvidence(BaseModel):
    """Evidencia combinada del caso: por campo, decisión y trazabilidad.

    Contrato principal que consumen la conclusión (F5) y el motor de reglas
    cruzadas. ``campos`` modela cada campo con :class:`CampoCombinado` (ver
    nota de modelado arriba y glosario §2.3).
    """

    model_config = ConfigDict(extra="forbid")

    documento_id: str = Field(..., min_length=1, description="Id del documento (hash sha256 del archivo).")
    campos: dict[str, CampoCombinado] = Field(default_factory=dict)
    decision: Decision
    trazabilidad: dict[str, Any] = Field(default_factory=dict)

    @field_validator("documento_id")
    @classmethod
    def _doc_id_no_vacio(cls, v: str) -> str:
        if not v.strip():
            raise ValueError("documento_id es obligatorio y no puede ser vacío")
        return v.strip()

    @model_validator(mode="after")
    def _coherencia_decision(self) -> "CombinedEvidence":
        # Regla de oro del glosario: la certeza se deriva de la etapa que
        # decidió. programa → certeza alta; agente_ia → certeza baja (+ HITL).
        if self.decision.origen == Origen.programa and self.decision.certeza != Certeza.alta:
            raise ValueError(
                "Contrato inválido: origen=programa exige certeza=alta "
                "(la certeza se deriva de la etapa, glosario §2)."
            )
        if self.decision.origen == Origen.agente_ia and self.decision.certeza != Certeza.baja:
            raise ValueError(
                "Contrato inválido: origen=agente_ia exige certeza=baja "
                "(el agente siempre escala a revisión HITL, glosario §2)."
            )
        # El agente nunca puede elegir un candidato descartado (blindaje,
        # ADR-008). No debe haber intersección entre descartados y restantes.
        descartados = set(self.decision.candidatos_descartados)
        restantes = set(self.decision.candidatos_restantes)
        cruce = descartados & restantes
        if cruce:
            raise ValueError(
                f"Contrato inválido: candidatos {sorted(cruce)} figuran a la vez "
                "como descartados y restantes (blindaje ADR-008)."
            )
        return self


# ---------------------------------------------------------------------------
# Helpers de construcción de meta
# ---------------------------------------------------------------------------


def _utc_now_iso() -> str:
    """Timestamp ISO-8601 en UTC (para meta/evidencia)."""
    return datetime.now(timezone.utc).isoformat()


def nueva_meta(modelo: str | None = None, version_prompt: str | None = None) -> dict[str, Any]:
    """Construye el dict ``meta`` estándar de una ``EvidenceField``.

    ``version_prompt`` suele ser del estilo ``"11.1@sha:abc123"`` (hash/versión
    de prompt, requisito de trazabilidad E-CONC-5 / ADR-005).
    """
    meta: dict[str, Any] = {"timestamp": _utc_now_iso()}
    if modelo:
        meta["modelo"] = modelo
    if version_prompt:
        meta["version_prompt"] = version_prompt
    return meta


__all__ = [
    "SCHEMA_VERSION",
    "CRITERIO_CAMBIO",
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
]

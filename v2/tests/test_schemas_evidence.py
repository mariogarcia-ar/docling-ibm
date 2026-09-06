"""Tests del contrato de evidencia (F0/T-001) — schemas/evidence.py.

Validan la **unidad mínima del contrato** (ADR-001, E-LIB-2): los modelos
pydantic aceptan JSON/modelos válidos y rechazan con **error de contrato claro**
los que faltan campo/valor/fuente/fragmento, con enums restringidos y la
coherencia de la regla de oro (certeza por etapa).
"""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from voucherflow.schemas.evidence import (
    SCHEMA_VERSION,
    CampoCombinado,
    Certeza,
    CombinedEvidence,
    Decision,
    EvidenceField,
    FieldResolution,
    Fuente,
    Origen,
    SourceEvidence,
    nueva_meta,
)


# ---------------------------------------------------------------------------
# EvidenceField
# ---------------------------------------------------------------------------


class TestEvidenceField:
    def test_evidencia_valida_minima(self):
        ev = EvidenceField(
            campo="tipo_comprobante",
            valor="A",
            fuente=Fuente.vlm,
            fragmento_sustento="Recuadro encabezado: letra grande 'A', COD. 01",
        )
        assert ev.campo == "tipo_comprobante"
        assert ev.valor == "A"
        assert ev.fuente == Fuente.vlm
        assert ev.confianza_fuente == "media"  # default
        assert ev.meta == {}

    def test_acepta_meta_y_confianza(self):
        ev = EvidenceField(
            campo="cuit",
            valor=20123456789,
            fuente=Fuente.llm,
            fragmento_sustento="CUIT 20-12345678-9",
            confianza_fuente="ALTA",  # se normaliza a minúscula
            meta={"modelo": "qwen2.5vl:3b", "version_prompt": "11.1@sha:abc123"},
        )
        assert ev.confianza_fuente == "alta"
        assert ev.meta["modelo"] == "qwen2.5vl:3b"

    @pytest.mark.parametrize(
        "campo,valor,fuente,fragmento",
        [
            ("", "A", Fuente.vlm, "frag"),  # campo vacío
            ("   ", "A", Fuente.vlm, "frag"),  # campo solo espacios
            ("tipo", "A", Fuente.vlm, ""),  # fragmento vacío
            ("tipo", "A", Fuente.vlm, "   "),  # fragmento solo espacios
        ],
    )
    def test_rechaza_contrato_invalido(self, campo, valor, fuente, fragmento):
        # E-LIB-2: falta campo/valor/fuente/fragmento → error de contrato claro.
        with pytest.raises(ValidationError) as exc:
            EvidenceField(campo=campo, valor=valor, fuente=fuente, fragmento_sustento=fragmento)
        msg = str(exc.value)
        assert "campo" in msg or "fragmento" in msg

    def test_rechaza_fuente_invalida(self):
        with pytest.raises(ValidationError):
            EvidenceField(campo="tipo", valor="A", fuente="no_existe", fragmento_sustento="frag")

    def test_rechaza_campo_extra(self):
        # extra="forbid": evita campos no contemplados en el contrato (típico
        # de respuestas de modelo "inventadas").
        with pytest.raises(ValidationError):
            EvidenceField(
                campo="tipo",
                valor="A",
                fuente=Fuente.vlm,
                fragmento_sustento="frag",
                campo_inesperado="x",
            )

    def test_fuente_enum_valores(self):
        assert {f.value for f in Fuente} == {"vlm", "llm", "programa", "arca", "hitl"}


# ---------------------------------------------------------------------------
# SourceEvidence
# ---------------------------------------------------------------------------


class TestSourceEvidence:
    def test_valida_por_defecto(self):
        src = SourceEvidence(fuente=Fuente.vlm)
        assert src.valida is True
        assert src.campos == {}
        assert src.reglas_aplicadas == []
        assert src.debilidades == []

    def test_con_campos(self):
        src = SourceEvidence(
            fuente=Fuente.llm,
            campos={
                "cuit": EvidenceField(
                    campo="cuit",
                    valor="20-12345678-9",
                    fuente=Fuente.llm,
                    fragmento_sustento="CUIT ...",
                )
            },
            reglas_aplicadas=["R4"],
        )
        assert src.campos["cuit"].valor == "20-12345678-9"


# ---------------------------------------------------------------------------
# CombinedEvidence + Decision (regla de oro: certeza por etapa)
# ---------------------------------------------------------------------------


class TestCombinedEvidence:
    def _decision(self, origen: Origen) -> Decision:
        return Decision(
            concluye=True,
            certeza=Certeza.alta if origen == Origen.programa else Certeza.baja,
            origen=origen,
        )

    def test_campo_combinado_con_vlm_llm_y_resolucion(self):
        campo = CampoCombinado(
            vlm=EvidenceField(campo="tipo", valor="A", fuente=Fuente.vlm, fragmento_sustento="recuadro A"),
            llm=EvidenceField(campo="tipo", valor="B", fuente=Fuente.llm, fragmento_sustento="texto B"),
            resolucion=FieldResolution(ganador=Fuente.vlm, regla="PREC_1", motivo="visual gana en recuadro"),
        )
        assert campo.fuentes_presentes() == [Fuente.vlm, Fuente.llm]
        assert campo.resolucion.ganador == Fuente.vlm

    def test_combined_valido_programa_alta(self):
        comb = CombinedEvidence(
            documento_id="sha256:abc",
            campos={},
            decision=self._decision(Origen.programa),
        )
        assert comb.decision.certeza == Certeza.alta

    def test_combined_valido_agente_baja(self):
        comb = CombinedEvidence(
            documento_id="sha256:abc",
            campos={},
            decision=self._decision(Origen.agente_ia),
        )
        assert comb.decision.certeza == Certeza.baja

    def test_rechaza_programa_con_certeza_baja(self):
        # Regla de oro: origen=programa exige certeza=alta.
        with pytest.raises(ValidationError) as exc:
            CombinedEvidence(
                documento_id="sha256:abc",
                campos={},
                decision=Decision(concluye=True, certeza=Certeza.baja, origen=Origen.programa),
            )
        assert "programa" in str(exc.value)

    def test_rechaza_agente_con_certeza_alta(self):
        # El agente siempre escala a revisión → certeza baja.
        with pytest.raises(ValidationError):
            CombinedEvidence(
                documento_id="sha256:abc",
                campos={},
                decision=Decision(concluye=True, certeza=Certeza.alta, origen=Origen.agente_ia),
            )

    def test_rechaza_candidato_descartado_y_restante(self):
        # Blindaje ADR-008: el agente no puede elegir un descartado.
        with pytest.raises(ValidationError) as exc:
            CombinedEvidence(
                documento_id="sha256:abc",
                campos={},
                decision=Decision(
                    concluye=True,
                    certeza=Certeza.alta,
                    origen=Origen.programa,
                    candidatos_descartados=["C"],
                    candidatos_restantes=["C"],
                ),
            )
        assert "descartados" in str(exc.value)


# ---------------------------------------------------------------------------
# Helpers / versión
# ---------------------------------------------------------------------------


class TestMeta:
    def test_nueva_meta(self):
        meta = nueva_meta(modelo="qwen2.5vl:3b", version_prompt="11.1@sha:abc123")
        assert meta["modelo"] == "qwen2.5vl:3b"
        assert meta["version_prompt"] == "11.1@sha:abc123"
        assert "timestamp" in meta

    def test_schema_version_congelada(self):
        assert SCHEMA_VERSION == "1.0.0"

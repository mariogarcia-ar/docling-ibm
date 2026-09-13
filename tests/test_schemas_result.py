"""Tests del contrato de resultados/trazabilidad (F0/T-001) — schemas/result.py.

Validan ``VoucherResult`` (glosario §2.4) y ``CaseRecord`` (ADR-005, requisito
de auditoría: versión de prompt, modelo, evidencia por fuente, reglas, quién
decidió).
"""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from voucherflow.schemas.evidence import (
    Certeza,
    CombinedEvidence,
    Decision,
    Fuente,
    Origen,
    SourceEvidence,
)
from voucherflow.schemas.result import (
    CaseRecord,
    ClasificacionContable,
    HitlDecision,
    RegistroEtapa,
    VoucherResult,
)


class TestVoucherResult:
    def test_resultado_valido_minimo(self):
        res = VoucherResult(documento_id="sha256:abc", estado="aprobado")
        assert res.documento_id == "sha256:abc"
        assert res.estado.value == "aprobado"
        assert res.hitl.requerido is False
        assert res.campos_extraidos == {}
        assert res.clasificacion_contable is None

    def test_rechaza_estado_invalido(self):
        with pytest.raises(ValidationError):
            VoucherResult(documento_id="sha256:abc", estado="invalido")

    def test_rechaza_documento_id_vacio(self):
        with pytest.raises(ValidationError):
            VoucherResult(documento_id="", estado="aprobado")

    def test_con_clasificacion_contable(self):
        res = VoucherResult(
            documento_id="sha256:abc",
            estado="aprobado",
            tipo_comprobante="A",
            certeza=Certeza.alta,
            origen=Origen.programa,
            clasificacion_contable=ClasificacionContable(
                centro_costo="CC0006", macro_categoria="MC07", concepto="...", codigo="..."
            ),
        )
        assert res.clasificacion_contable.centro_costo == "CC0006"

    def test_con_evidencia(self):
        res = VoucherResult(
            documento_id="sha256:abc",
            estado="aprobado",
            evidencia=CombinedEvidence(
                documento_id="sha256:abc",
                campos={},
                decision=Decision(concluye=True, certeza=Certeza.alta, origen=Origen.programa),
            ),
        )
        assert res.evidencia.decision.concluye is True


class TestHitlDecision:
    def test_default(self):
        h = HitlDecision()
        assert h.requerido is False
        assert h.prioridad == "baja"
        assert h.estado == "no_aplica"

    def test_rechaza_prioridad_invalida(self):
        with pytest.raises(ValidationError):
            HitlDecision(requerido=True, prioridad="media")

    def test_rechaza_estado_invalido(self):
        with pytest.raises(ValidationError):
            HitlDecision(estado="raro")


class TestCaseRecord:
    def test_case_record_minimo(self):
        caso = CaseRecord(documento_id="sha256:abc")
        assert caso.schema_version == "1.0.0"
        assert caso.quien_decidio is None
        assert caso.etapas == []
        assert caso.timestamp  # autogenerado

    def test_case_record_con_trazabilidad_completa(self):
        # ADR-005: un CaseRecord auditable tiene versiones de prompt, modelos,
        # evidencia por fuente, reglas y quién decidió.
        caso = CaseRecord(
            documento_id="sha256:abc",
            archivo="v2/tests/fixtures/golden/ejemplo.jpg",
            version_prompt={"11.1": "11.1@sha:abc123"},
            modelo_por_etapa={"vlm": "qwen2.5vl:3b", "llm": "qwen2.5:7b"},
            evidencia_por_fuente={
                "vlm": SourceEvidence(fuente=Fuente.vlm),
            },
            reglas_disparadas=["R1", "R4"],
            quien_decidio=Origen.programa,
            etapas=[
                RegistroEtapa(
                    etapa="classification",
                    modelo="qwen2.5vl:3b",
                    version_prompt="11.1@sha:abc123",
                )
            ],
        )
        assert caso.quien_decidio == Origen.programa
        assert caso.etapas[0].etapa == "classification"

    def test_rechaza_documento_id_vacio(self):
        with pytest.raises(ValidationError):
            CaseRecord(documento_id="  ")


class TestImportPublico:
    def test_exports_schemas_result(self):
        # La superficie pública de schemas.result debe exponer los contratos.
        from voucherflow.schemas.result import SCHEMA_VERSION

        assert SCHEMA_VERSION == "1.0.0"

    def test_paquete_voucherflow_importa(self):
        import voucherflow

        assert voucherflow.SCHEMA_VERSION == "1.0.0"

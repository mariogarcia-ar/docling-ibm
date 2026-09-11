"""Tests de la cola HITL, el muestreo de auditoría y el feedback (F5/T-505).

**DoD de T-505** (F5.md §3, E-CONC-4 / ADR-004 / ADR-009): "Los casos de certeza
baja entran a revisión humana con prioridad alta y un muestreo configurable de
certeza alta con prioridad baja. Las correcciones del revisor quedan como señal
de feedback para ajustar reglas y prompts (mitiga R-03 y R-09)".

Los tres escenarios del Gherkin:

    Dado un caso de certeza baja (resuelto por agente IA)
    Cuando se consolida
    Entonces llega a revisión humana (HITL) con prioridad alta

    Dado un caso de certeza alta (resuelto por programa)
    Cuando se consolida
    Entonces entra en un muestreo de auditoría periódico con prioridad baja

    Regla: feedback
      Dado que el HITL corrige una decisión
      Cuando se registra la corrección
      Entonces la corrección queda disponible como señal para ajustar reglas y
      prompts

Qué se verifica, en el orden del entregable:

1. **La decisión de encolar** (`decidir_encolado`): pura, para los dos caminos
   del Gherkin y para el caso que no entra.
2. **El muestreo de auditoría** (`seleccionado_para_auditoria`): determinista,
   reproducible y con la tasa pedida — el ADR-004 pide un muestreo *auditable*,
   no un `random` sin semilla.
3. **La cola** (`ColaHitl`): alta, consultas (pendientes priorizadas,
   obligatorios, muestreados) y no-duplicación del mismo caso.
4. **La corrección del revisor** (`registrar_correccion`): queda registrada
   estructurada (campo/antes/después/quién) y el caso pasa a revisado.
5. **La confirmación sin corrección** (`confirmar`): distingue "la regla acertó"
   de "no se revisó", que es justo lo que el muestreo de auditoría necesita.
6. **El feedback** (`feedback`): separa las correcciones de certeza baja (error
   del agente, R-09) de las de muestreo (regla que acierta por accidente, R-03).
7. **La integración** (`encolar_hitl`): muta la `HitlDecision` y deja el bloque
   de traza, sin tocar el resto del resultado.
8. **Fronteras**: sin red, determinístico, y los errores explícitos (documento
   fuera de la cola, campo vacío).

Reglas duras: suite default **sin** Ollama, **sin** Docling y **sin** red.
"""

from __future__ import annotations

from typing import Any

import pytest

from voucherflow.conclusion import (
    ESTADO_PENDIENTE,
    ESTADO_REVISADO,
    MOTIVO_CERTEZA_BAJA,
    MOTIVO_MUESTREO_AUDITORIA,
    PRIORIDAD_ALTA,
    PRIORIDAD_BAJA,
    VERSION_HITL,
    ColaHitl,
    Correccion,
    decidir_encolado,
    encolar_hitl,
    encolar_lote,
    seleccionado_para_auditoria,
)
from voucherflow.conclusion.hitl import (
    MOTIVO_NO_APLICA,
    MOTIVO_REVISION_DESACTIVADA,
)
from voucherflow.extraction.flows import combinar_evidencia
from voucherflow.rules.contexto import ContextoTipoComprobante
from voucherflow.schemas.evidence import (
    Certeza,
    EvidenceField,
    Fuente,
    Origen,
    SourceEvidence,
)
from voucherflow.schemas.result import EstadoResultado, HitlDecision, VoucherResult
from voucherflow.settings.config import HitlSettings, Settings

# ---------------------------------------------------------------------------
# Utilidades
# ---------------------------------------------------------------------------

CTX_RI_RI = ContextoTipoComprobante(
    emisor_condicion_fiscal="Responsable Inscripto",
    receptor_condicion_fiscal="Responsable Inscripto",
)

BASE: dict[str, Any] = {
    "fecha_emision": "2025-08-14",
    "nro_comprobante": "0001-00000001",
    "importe_total_facturado": "121.00",
}

#: Factura A coherente: el código concluye con certeza alta.
A_COMPLETA: dict[str, Any] = {
    **BASE,
    "tipo_comprobante": "A",
    "cuit_emisor": "30-12345678-9",
    "cuit_receptor": "27-12345678-4",
    "subtotal": "100.00",
    "iva": "21.00",
}


def _source(fuente: Fuente, campos: dict[str, Any]) -> SourceEvidence:
    return SourceEvidence(
        fuente=fuente,
        campos={
            campo: EvidenceField(
                campo=campo, valor=valor, fuente=fuente, fragmento_sustento="frag"
            )
            for campo, valor in campos.items()
        },
    )


def _evidencia(campos: dict[str, Any], *, documento_id: str = "doc-1"):
    return combinar_evidencia(
        documento_id,
        [_source(Fuente.vlm, campos), _source(Fuente.llm, campos)],
    )


def _resultado(
    *,
    documento_id: str = "doc-1",
    certeza: Certeza | None,
    origen: Origen | None,
    tipo_comprobante: str | None = None,
    estado: EstadoResultado = EstadoResultado.revision,
) -> VoucherResult:
    """``VoucherResult`` mínimo para ejercitar la política sin pasar por F3/F4."""
    return VoucherResult(
        documento_id=documento_id,
        estado=estado,
        tipo_comprobante=tipo_comprobante,
        certeza=certeza,
        origen=origen,
    )


def _alta_por_programa(**kwargs: Any) -> VoucherResult:
    return _resultado(
        certeza=Certeza.alta,
        origen=Origen.programa,
        tipo_comprobante="A",
        estado=EstadoResultado.aprobado,
        **kwargs,
    )


def _baja_por_agente(**kwargs: Any) -> VoucherResult:
    return _resultado(certeza=Certeza.baja, origen=Origen.agente_ia, **kwargs)


# ---------------------------------------------------------------------------
# 1. La decisión de encolar
# ---------------------------------------------------------------------------


class TestDecidirEncolado:
    """El Gherkin, en sus dos ramas, y el caso que no entra."""

    def test_certeza_baja_va_a_revision_con_prioridad_alta(self):
        # Gherkin: "Dado un caso de certeza baja (resuelto por agente IA) ...
        # Entonces llega a revisión humana (HITL) con prioridad alta".
        decision = decidir_encolado(_baja_por_agente())

        assert decision.requerido is True
        assert decision.prioridad == PRIORIDAD_ALTA
        assert decision.motivo == MOTIVO_CERTEZA_BAJA

    def test_certeza_baja_sin_origen_tambien_va_a_revision(self):
        # Un caso que el código no pudo concluir y el agente no tocó sigue sin
        # veredicto firme: también es revisión obligatoria.
        decision = decidir_encolado(_resultado(certeza=None, origen=None))

        assert decision.requerido is True
        assert decision.prioridad == PRIORIDAD_ALTA

    def test_certeza_alta_muestreada_entra_con_prioridad_baja(self):
        # Gherkin: "Dado un caso de certeza alta (resuelto por programa) ...
        # Entonces entra en un muestreo de auditoría periódico con prioridad baja".
        decision = decidir_encolado(_alta_por_programa(), hitl=HitlSettings(muestreo_tasa=1.0))

        assert decision.requerido is True
        assert decision.prioridad == PRIORIDAD_BAJA
        assert decision.motivo == MOTIVO_MUESTREO_AUDITORIA

    def test_certeza_alta_no_muestreada_no_entra(self):
        decision = decidir_encolado(_alta_por_programa(), hitl=HitlSettings(muestreo_tasa=0.0))

        assert decision.requerido is False
        assert decision.motivo == MOTIVO_NO_APLICA

    def test_muestreo_apagado_no_encola_ningun_caso_alto(self):
        # `muestreo_activo=False` desactiva la auditoría sin perder la tasa.
        politica = HitlSettings(muestreo_tasa=1.0, muestreo_activo=False)

        assert decidir_encolado(_alta_por_programa(), hitl=politica).requerido is False

    def test_la_revision_obligatoria_se_puede_desactivar_explicitamente(self):
        # El flag existe para documentar la política; desactivarlo se declara,
        # no se silencia.
        politica = HitlSettings(revision_obligatoria_certeza_baja=False)
        decision = decidir_encolado(_baja_por_agente(), hitl=politica)

        assert decision.requerido is False
        assert decision.motivo == MOTIVO_REVISION_DESACTIVADA

    def test_la_decision_es_pura_y_deterministica(self):
        resultado = _baja_por_agente()

        a = decidir_encolado(resultado)
        b = decidir_encolado(resultado)

        assert a == b
        assert resultado.hitl.requerido is False  # no mutó el resultado

    def test_explica_por_que(self):
        # La explicación viaja a la traza: el revisor tiene que poder leerla.
        assert decidir_encolado(_baja_por_agente()).explicacion
        assert decidir_encolado(
            _alta_por_programa(), hitl=HitlSettings(muestreo_tasa=1.0)
        ).explicacion

    def test_como_dict_es_serializable(self):
        decision = decidir_encolado(_baja_por_agente())
        d = decision.como_dict()

        assert d["requerido"] is True
        assert d["prioridad"] == PRIORIDAD_ALTA
        assert d["motivo"] == MOTIVO_CERTEZA_BAJA


# ---------------------------------------------------------------------------
# 2. El muestreo de auditoría
# ---------------------------------------------------------------------------


class TestMuestreoAuditoria:
    """ADR-004: muestreo configurable y —sobre todo— **auditable**."""

    def test_es_determinista(self):
        # El mismo documento con la misma semilla cae siempre igual: si no,
        # re-procesar cambiaría la suerte del caso.
        for doc in (f"doc-{i}" for i in range(50)):
            assert seleccionado_para_auditoria(doc, tasa=0.10, semilla=0) == (
                seleccionado_para_auditoria(doc, tasa=0.10, semilla=0)
            )

    def test_la_muestra_es_estable_entre_llamadas(self):
        # Y la muestra completa también: es lo que permite decir "este caso se
        # auditó porque le tocó, y le va a volver a tocar".
        docs = [f"doc-{i:04d}" for i in range(500)]
        primera = {d for d in docs if seleccionado_para_auditoria(d, tasa=0.10, semilla=0)}
        segunda = {d for d in docs if seleccionado_para_auditoria(d, tasa=0.10, semilla=0)}

        assert primera == segunda

    def test_la_tasa_se_respeta_en_el_conjunto(self):
        docs = [f"doc-{i:04d}" for i in range(5000)]
        proporcion = sum(
            seleccionado_para_auditoria(d, tasa=0.10, semilla=0) for d in docs
        ) / len(docs)

        assert 0.08 < proporcion < 0.12  # ~10% con margen de hash

    def test_tasa_cero_no_selecciona_nada(self):
        assert not any(
            seleccionado_para_auditoria(f"doc-{i}", tasa=0.0) for i in range(200)
        )

    def test_tasa_uno_selecciona_todo(self):
        assert all(
            seleccionado_para_auditoria(f"doc-{i}", tasa=1.0) for i in range(200)
        )

    def test_la_semilla_cambia_la_muestra(self):
        # La semilla es el "estrato" configurable: permite rotar qué se audita
        # sin cambiar la tasa.
        docs = [f"doc-{i:04d}" for i in range(1000)]
        a = {d for d in docs if seleccionado_para_auditoria(d, tasa=0.10, semilla=0)}
        b = {d for d in docs if seleccionado_para_auditoria(d, tasa=0.10, semilla=7)}

        assert a != b
        assert a and b  # ambas no vacías

    def test_es_estable_caso_por_caso_entre_lotes(self):
        # Aplicar la política a un lote o de a uno da lo mismo (no hay estado
        # compartido que acumule).
        docs = [f"doc-{i:04d}" for i in range(200)]

        uno_por_uno = {
            d for d in docs if seleccionado_para_auditoria(d, tasa=0.10, semilla=3)
        }
        en_lote = {
            seleccionado_para_auditoria(d, tasa=0.10, semilla=3) and d for d in docs
        }
        en_lote.discard(False)

        assert uno_por_uno == en_lote


# ---------------------------------------------------------------------------
# 3. La cola
# ---------------------------------------------------------------------------


class TestColaHitl:
    """Alta, consultas y no-duplicación."""

    def test_encola_los_casos_que_corresponden(self):
        cola = ColaHitl(hitl=HitlSettings(muestreo_tasa=0.0))

        assert cola.encolar(_baja_por_agente(documento_id="bajo")) is not None
        assert cola.encolar(_alta_por_programa(documento_id="alto")) is None
        assert len(cola) == 1

    def test_pendientes_prioriza_lo_obligatorio(self):
        # R-09: el agente escala de más y carga el HITL. La mitigación es una
        # cola **priorizada**: lo de certeza baja se mira primero.
        cola = ColaHitl(hitl=HitlSettings(muestreo_tasa=1.0))
        cola.encolar(_alta_por_programa(documento_id="muestreado"))
        cola.encolar(_baja_por_agente(documento_id="obligatorio"))

        orden = [e.documento_id for e in cola.pendientes()]

        assert orden == ["obligatorio", "muestreado"]
        assert cola.pendientes()[0].prioridad == PRIORIDAD_ALTA

    def test_obligatorios_y_muestreados_estan_separados(self):
        cola = ColaHitl(hitl=HitlSettings(muestreo_tasa=1.0))
        cola.encolar(_baja_por_agente(documento_id="bajo"))
        cola.encolar(_alta_por_programa(documento_id="alto"))

        assert [e.documento_id for e in cola.obligatorios()] == ["bajo"]
        assert [e.documento_id for e in cola.muestreados()] == ["alto"]

    def test_el_mismo_caso_no_se_duplica(self):
        cola = ColaHitl(hitl=HitlSettings(muestreo_tasa=0.0))
        resultado = _baja_por_agente()

        cola.encolar(resultado)
        cola.encolar(resultado)

        assert len(cola) == 1
        assert len(cola.pendientes()) == 1

    def test_reencontrar_un_caso_revisado_no_borra_el_trabajo_humano(self):
        # Re-encolar un caso ya revisado no debe perder la corrección: la cola
        # es por caso, no por corrida.
        cola = ColaHitl(hitl=HitlSettings(muestreo_tasa=0.0))
        cola.encolar(_baja_por_agente())
        cola.registrar_correccion("doc-1", campo="tipo_comprobante", valor_nuevo="A")

        cola.encolar(_baja_por_agente())

        entrada = cola.entrada("doc-1")
        assert entrada is not None
        assert entrada.revisado is True
        assert entrada.corregido is True

    def test_revisados_salen_de_pendientes(self):
        cola = ColaHitl(hitl=HitlSettings(muestreo_tasa=0.0))
        cola.encolar(_baja_por_agente())
        cola.confirmar("doc-1")

        assert cola.pendientes() == []

    def test_contiene_y_responde_por_documento(self):
        cola = ColaHitl(hitl=HitlSettings(muestreo_tasa=0.0))
        cola.encolar(_baja_por_agente(documento_id="doc-7"))

        assert "doc-7" in cola
        assert cola.entrada("doc-7") is not None
        assert cola.entrada("nope") is None

    def test_registra_el_contexto_del_caso_para_el_revisor(self):
        # El revisor necesita saber qué dijo el sistema para poder corregirlo.
        cola = ColaHitl(hitl=HitlSettings(muestreo_tasa=0.0))
        cola.encolar(_baja_por_agente())

        entrada = cola.entrada("doc-1")
        assert entrada is not None
        assert entrada.certeza == "baja"
        assert entrada.origen == "agente_ia"

    def test_resumen_es_serializable(self):
        cola = ColaHitl(hitl=HitlSettings(muestreo_tasa=1.0))
        cola.encolar(_baja_por_agente(documento_id="bajo"))
        cola.encolar(_alta_por_programa(documento_id="alto"))

        resumen = cola.resumen()

        assert resumen["version"] == VERSION_HITL
        assert resumen["total"] == 2
        assert resumen["obligatorios"] == 1
        assert resumen["muestreados"] == 1
        assert len(resumen["entradas"]) == 2

    def test_la_cola_usa_la_politica_por_defecto_de_la_configuracion(self):
        cola = ColaHitl()  # sin política: HitlSettings() con la tasa del ADR-004
        cola.encolar(_baja_por_agente())

        assert cola.hitl.muestreo_tasa == 0.10


# ---------------------------------------------------------------------------
# 4. La corrección del revisor
# ---------------------------------------------------------------------------


class TestCorreccion:
    """El Gherkin de feedback: la corrección es señal, estructurada."""

    def test_la_correccion_queda_registrada_estructurada(self):
        cola = ColaHitl(hitl=HitlSettings(muestreo_tasa=0.0))
        cola.encolar(_baja_por_agente())

        entrada = cola.registrar_correccion(
            "doc-1",
            campo="tipo_comprobante",
            valor_anterior="B",
            valor_nuevo="A",
            motivo="el negocio esperaba A",
            revisor="contador",
        )

        assert entrada.corregido is True
        assert entrada.revisado is True
        correccion = entrada.correcciones[0]
        assert correccion.campo == "tipo_comprobante"
        assert correccion.valor_anterior == "B"
        assert correccion.valor_nuevo == "A"
        assert correccion.revisor == "contador"

    def test_la_correccion_marca_el_caso_revisado(self):
        cola = ColaHitl(hitl=HitlSettings(muestreo_tasa=0.0))
        cola.encolar(_baja_por_agente())

        cola.registrar_correccion("doc-1", campo="tipo_comprobante", valor_nuevo="A")

        assert cola.entrada("doc-1").estado == ESTADO_REVISADO
        assert cola.entrada("doc-1").timestamp_revision is not None

    def test_se_acumulan_varias_correcciones(self):
        cola = ColaHitl(hitl=HitlSettings(muestreo_tasa=0.0))
        cola.encolar(_baja_por_agente())

        cola.registrar_correccion("doc-1", campo="tipo_comprobante", valor_nuevo="A")
        entrada = cola.registrar_correccion(
            "doc-1", campo="importe_total_facturado", valor_nuevo="121.00"
        )

        assert len(entrada.correcciones) == 2

    def test_un_caso_de_un_documento_fuera_de_la_cola_es_un_error(self):
        # Una corrección sin caso es un feedback huérfano: mejor fallar ruidoso.
        cola = ColaHitl()

        with pytest.raises(KeyError):
            cola.registrar_correccion("nope", campo="tipo_comprobante", valor_nuevo="A")

    def test_un_campo_vacio_no_es_senal(self):
        cola = ColaHitl(hitl=HitlSettings(muestreo_tasa=0.0))
        cola.encolar(_baja_por_agente())

        with pytest.raises(ValueError):
            cola.registrar_correccion("doc-1", campo="", valor_nuevo="A")

    def test_correccion_como_dict(self):
        correccion = Correccion(
            campo="tipo_comprobante", valor_anterior="B", valor_nuevo="A"
        )
        d = correccion.como_dict()

        assert set(d) == {
            "campo",
            "valor_anterior",
            "valor_nuevo",
            "motivo",
            "revisor",
            "timestamp",
        }


# ---------------------------------------------------------------------------
# 5. La confirmación
# ---------------------------------------------------------------------------


class TestConfirmacion:
    """"La regla acertó" es información distinta de "no se revisó"."""

    def test_confirmar_marca_revisado_sin_correccion(self):
        cola = ColaHitl(hitl=HitlSettings(muestreo_tasa=1.0))
        cola.encolar(_alta_por_programa())

        entrada = cola.confirmar("doc-1")

        assert entrada.revisado is True
        assert entrada.corregido is False

    def test_un_caso_fuera_de_la_cola_no_se_puede_confirmar(self):
        with pytest.raises(KeyError):
            ColaHitl().confirmar("nope")

    def test_confirmados_son_revisados_sin_correccion(self):
        cola = ColaHitl(hitl=HitlSettings(muestreo_tasa=1.0))
        cola.encolar(_alta_por_programa(documento_id="ok"))
        cola.encolar(_baja_por_agente(documento_id="malo"))
        cola.confirmar("ok")
        cola.registrar_correccion("malo", campo="tipo_comprobante", valor_nuevo="A")

        feedback = cola.feedback()

        assert feedback["confirmados"] == 1
        assert feedback["corregidos"] == 1


# ---------------------------------------------------------------------------
# 6. El feedback
# ---------------------------------------------------------------------------


class TestFeedback:
    """La señal para ajustar reglas y prompts, separada por motivo."""

    def test_las_correcciones_quedan_disponibles(self):
        # Gherkin: "Cuando se registra la corrección, Entonces la corrección
        # queda disponible como señal para ajustar reglas y prompts".
        cola = ColaHitl(hitl=HitlSettings(muestreo_tasa=0.0))
        cola.encolar(_baja_por_agente())
        cola.registrar_correccion("doc-1", campo="tipo_comprobante", valor_nuevo="A")

        feedback = cola.feedback()

        assert feedback["correcciones_por_campo"] == {"tipo_comprobante": 1}
        assert feedback["correcciones_de_certeza_baja"]

    def test_separa_las_dos_razones_de_auditar(self):
        # R-09: el agente se equivocó (certeza baja). R-03: una regla acierta
        # por accidente (muestreo). Son señales distintas y no se mezclan.
        cola = ColaHitl(hitl=HitlSettings(muestreo_tasa=1.0))
        cola.encolar(_baja_por_agente(documento_id="agente"))
        cola.encolar(_alta_por_programa(documento_id="regla"))

        cola.registrar_correccion("agente", campo="tipo_comprobante", valor_nuevo="A")
        cola.registrar_correccion("regla", campo="importe_total_facturado", valor_nuevo="1")

        feedback = cola.feedback()

        assert len(feedback["correcciones_de_certeza_baja"]) == 1
        assert len(feedback["correcciones_de_muestreo"]) == 1
        assert feedback["correcciones_de_certeza_baja"][0]["campo"] == "tipo_comprobante"
        assert feedback["correcciones_de_muestreo"][0]["campo"] == "importe_total_facturado"

    def test_agrega_que_campo_se_corrige_mas(self):
        # El agregado más accionable: si la letra se corrige siempre, el
        # problema está en la regla de la letra, no en el mapeo de gastos.
        cola = ColaHitl(hitl=HitlSettings(muestreo_tasa=0.0))
        for i in range(3):
            cola.encolar(_baja_por_agente(documento_id=f"doc-{i}"))
        cola.registrar_correccion("doc-0", campo="tipo_comprobante", valor_nuevo="A")
        cola.registrar_correccion("doc-1", campo="tipo_comprobante", valor_nuevo="A")
        cola.registrar_correccion("doc-2", campo="categoria_gasto", valor_nuevo="X")

        campos = cola.feedback()["correcciones_por_campo"]

        assert campos["tipo_comprobante"] == 2
        assert campos["categoria_gasto"] == 1

    def test_una_cola_sin_revisiones_tiene_feedback_vacio(self):
        cola = ColaHitl(hitl=HitlSettings(muestreo_tasa=0.0))
        cola.encolar(_baja_por_agente())

        feedback = cola.feedback()

        assert feedback["corregidos"] == 0
        assert feedback["correcciones_por_campo"] == {}
        assert feedback["pendientes"] == 1


# ---------------------------------------------------------------------------
# 7. La integración (`encolar_hitl`)
# ---------------------------------------------------------------------------


class TestEncolarHitl:
    """La firma del esqueleto de F0, ahora con comportamiento real."""

    def test_muta_la_decision_hitl_del_resultado(self):
        resultado = _baja_por_agente()
        assert resultado.hitl.requerido is False  # la expectativa de F5/T-501

        encolar_hitl(resultado)

        assert resultado.hitl.requerido is True
        assert resultado.hitl.prioridad == PRIORIDAD_ALTA
        assert resultado.hitl.estado == ESTADO_PENDIENTE

    def test_deja_el_bloque_de_traza(self):
        resultado = _baja_por_agente()

        encolar_hitl(resultado)

        bloque = resultado.trazabilidad["hitl"]
        assert bloque["version"] == VERSION_HITL
        assert bloque["motivo"] == MOTIVO_CERTEZA_BAJA
        assert bloque["requerido"] is True

    def test_registra_la_entrada_en_la_cola(self):
        cola = ColaHitl(hitl=HitlSettings(muestreo_tasa=0.0))
        resultado = _baja_por_agente()

        encolar_hitl(resultado, cola=cola)

        assert "doc-1" in cola
        assert cola.entrada("doc-1").estado == ESTADO_PENDIENTE

    def test_un_caso_alto_no_muestreado_no_entra(self):
        cola = ColaHitl(hitl=HitlSettings(muestreo_tasa=0.0))
        resultado = _alta_por_programa()

        encolar_hitl(resultado, cola=cola)

        assert resultado.hitl.requerido is False
        assert resultado.hitl.estado == "no_aplica"
        assert len(cola) == 0

    def test_un_caso_alto_muestreado_entra_con_prioridad_baja(self):
        cola = ColaHitl(hitl=HitlSettings(muestreo_tasa=1.0))
        resultado = _alta_por_programa()

        encolar_hitl(resultado, cola=cola)

        assert resultado.hitl.requerido is True
        assert resultado.hitl.prioridad == PRIORIDAD_BAJA
        assert cola.entrada("doc-1").motivo == MOTIVO_MUESTREO_AUDITORIA

    def test_la_politica_puede_venir_de_settings(self):
        resultado = _alta_por_programa()
        settings = Settings(hitl=HitlSettings(muestreo_tasa=1.0))

        encolar_hitl(resultado, settings=settings)

        assert resultado.hitl.requerido is True
        assert resultado.hitl.prioridad == PRIORIDAD_BAJA

    def test_sin_cola_la_decision_se_calcula_sin_efectos(self):
        resultado = _baja_por_agente()

        encolar_hitl(resultado, cola=None)

        assert resultado.hitl.requerido is True  # la decisión se publica
        assert resultado.trazabilidad["hitl"]["motivo"] == MOTIVO_CERTEZA_BAJA

    def test_devuelve_el_mismo_objeto(self):
        # La firma de F0 devuelve `VoucherResult`: es el mismo caso, con la
        # decisión HITL aplicada (no una copia que el llamador podría perder).
        resultado = _baja_por_agente()

        assert encolar_hitl(resultado) is resultado

    def test_no_toca_el_resto_del_resultado(self):
        resultado = _baja_por_agente(tipo_comprobante="B")
        antes = (resultado.estado, resultado.certeza, resultado.origen, resultado.tipo_comprobante)

        encolar_hitl(resultado)

        assert (
            resultado.estado,
            resultado.certeza,
            resultado.origen,
            resultado.tipo_comprobante,
        ) == antes

    def test_es_deterministico(self):
        def correr() -> dict[str, Any]:
            resultado = _baja_por_agente()
            encolar_hitl(resultado)
            return resultado.trazabilidad["hitl"]

        assert correr() == correr()

    def test_la_salida_es_un_voucher_result_valido(self):
        # El resultado sigue cumpliendo el contrato de F0 después del encolado.
        resultado = _baja_por_agente()

        encolar_hitl(resultado)

        assert isinstance(resultado, VoucherResult)
        assert isinstance(resultado.hitl, HitlDecision)
        assert resultado.model_dump()["hitl"]["requerido"] is True


class TestEncolarLote:
    """Aplicar la política a una corrida completa."""

    def test_encola_el_lote_y_devuelve_la_cola(self):
        resultados = [
            _baja_por_agente(documento_id="bajo-1"),
            _baja_por_agente(documento_id="bajo-2"),
            _alta_por_programa(documento_id="alto"),
        ]

        cola = encolar_lote(resultados, settings=Settings(hitl=HitlSettings(muestreo_tasa=0.0)))

        assert len(cola) == 2
        assert all(r.hitl.requerido for r in resultados[:2])
        assert resultados[2].hitl.requerido is False

    def test_reutiliza_la_cola_que_se_le_pasa(self):
        cola = ColaHitl(hitl=HitlSettings(muestreo_tasa=0.0))
        cola.encolar(_baja_por_agente(documento_id="previo"))

        resultado = encolar_lote([_baja_por_agente(documento_id="nuevo")], cola=cola)

        assert resultado is cola
        assert len(cola) == 2

    def test_el_lote_es_estable_ante_el_orden(self):
        # El muestreo es por documento, no por posición: reordenar el lote no
        # cambia quién se audita.
        uno = _alta_por_programa(documento_id="doc-0001")
        dos = _alta_por_programa(documento_id="doc-0002")
        politica = Settings(hitl=HitlSettings(muestreo_tasa=0.5))

        cola_a = encolar_lote([uno, dos], settings=politica)
        cola_b = encolar_lote([dos, uno], settings=politica)

        assert {e.documento_id for e in cola_a.muestreados()} == {
            e.documento_id for e in cola_b.muestreados()
        }


# ---------------------------------------------------------------------------
# 8. Fronteras
# ---------------------------------------------------------------------------


class TestFronteras:
    """Lo que el HITL no hace y los errores que declara."""

    def test_no_hay_red(self):
        # La cola no habla con ningún modelo: es una estructura de datos.
        import inspect

        from voucherflow.conclusion import hitl

        fuente = inspect.getsource(hitl)

        assert "requests" not in fuente
        assert "urllib" not in fuente
        assert "http" not in fuente

    def test_la_entrada_es_serializable(self):
        cola = ColaHitl(hitl=HitlSettings(muestreo_tasa=0.0))
        cola.encolar(_baja_por_agente())
        cola.registrar_correccion("doc-1", campo="tipo_comprobante", valor_nuevo="A")

        entrada = cola.entrada("doc-1")
        d = entrada.como_dict()

        assert d["documento_id"] == "doc-1"
        assert d["estado"] == ESTADO_REVISADO
        assert d["correcciones"][0]["campo"] == "tipo_comprobante"

    def test_la_entrada_proyecta_al_contrato_de_f0(self):
        # `HitlDecision` tiene un solo `correccion`: se publica la última.
        cola = ColaHitl(hitl=HitlSettings(muestreo_tasa=0.0))
        cola.encolar(_baja_por_agente())
        cola.registrar_correccion("doc-1", campo="tipo_comprobante", valor_nuevo="A")
        cola.registrar_correccion("doc-1", campo="moneda", valor_nuevo="ARS")

        decision = cola.entrada("doc-1").como_hitl_decision()

        assert isinstance(decision, HitlDecision)
        assert decision.requerido is True
        assert decision.estado == ESTADO_REVISADO
        assert decision.correccion["campo"] == "moneda"

    def test_una_entrada_sin_correcciones_proyecta_vacio(self):
        cola = ColaHitl(hitl=HitlSettings(muestreo_tasa=0.0))
        cola.encolar(_baja_por_agente())

        assert cola.entrada("doc-1").como_hitl_decision().correccion == {}

    def test_el_encolado_no_decide_por_el_agente(self):
        # La cola **no** re-evalúa el caso ni cambia su certeza: solo lo deriva
        # a revisión. La decisión del agente (T-504) ya está tomada.
        resultado = _baja_por_agente(tipo_comprobante="B")

        encolar_hitl(resultado)

        assert resultado.certeza == Certeza.baja
        assert resultado.origen == Origen.agente_ia
        assert resultado.tipo_comprobante == "B"

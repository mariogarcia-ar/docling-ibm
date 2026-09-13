"""Tests de las métricas del pipeline (F5/T-507, E-LIB-5).

**DoD de T-507** (F5.md §3, E-LIB-5 / `06-estrategia-calidad.md` §5): "Las
métricas de la fase se calculan y reportan: % certeza alta, % agente, % rechazado
y acuerdo VLM/LLM".

El Gherkin de E-LIB-5 que se implementa acá::

    Dado el procesamiento de un lote
    Cuando termina
    Entonces se producen métricas: documentos procesados, % certeza alta,
    % agente, % rechazados, latencias

(Las *latencias* y el diagnóstico del cliente ante latencia/status inesperado son
la otra mitad de E-LIB-5 y viven en `models/ollama.py` desde F0/T-005: la suite
verifica que esa mitad exista y no se duplique acá.)

Qué se verifica, en el orden del entregable:

1. **Las cuatro métricas del DoD** (% certeza alta, % agente, % rechazado, acuerdo
   VLM/LLM), cada una con su `n` y su definición.
2. **La honestidad del cálculo**: cada métrica usa su **propio denominador**; un
   caso sin resultado consolidado no entra como "no rechazado" ni como "no agente",
   y una métrica sin denominador sale **"no calculable"** con su motivo en vez de
   un 0% que miente.
3. **El acuerdo VLM/LLM cuenta sobre lo leído por ambas**: un campo que solo leyó
   una fuente es cobertura, no desacuerdo; los desacuerdos se listan con valores.
4. **La cobertura HITL separa obligatorios de muestreo** (objetivos distintos: 100%
   vs. la tasa configurada) y distingue *encolado* de *revisado*.
5. **La tasa de alertas** cuenta casos con al menos una alerta.
6. **El reporte se versiona** (librería + contrato + formato de traza) y **avisa si
   el lote es chico** — una tendencia no se lee sobre dos casos.
7. **Fronteras**: sin red, determinístico, no muta el histórico, y el diagnóstico
   de E-LIB-5 no se duplica.

Reglas duras: suite default **sin** Ollama, **sin** Docling y **sin** red.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from voucherflow import __version__
from voucherflow.conclusion import (
    ColaHitl,
    concluir_con_agente,
    consolidar_caso,
    encolar_hitl,
)
from voucherflow.extraction.flows import combinar_evidencia
from voucherflow.rules.contexto import ContextoTipoComprobante
from voucherflow.schemas.evidence import (
    EvidenceField,
    Fuente,
    SourceEvidence,
    nueva_meta,
)
from voucherflow.schemas.result import SCHEMA_VERSION
from voucherflow.settings.config import HitlSettings
from voucherflow.trace import (
    MINIMO_LOTE_CONFIABLE,
    VERSION_METRICAS,
    VERSION_TRAZA,
    CaseRecorder,
    acuerdo_vlm_llm,
    casos_del_historico,
    cobertura_hitl,
    construir_case_record,
    metricas_certidumbre,
    metricas_de,
    metricas_del_recorder,
    metricas_rechazo,
    resumen_legible,
    tasa_alertas,
)

# ---------------------------------------------------------------------------
# Utilidades
# ---------------------------------------------------------------------------

CTX_RI_RI = ContextoTipoComprobante(
    emisor_condicion_fiscal="Responsable Inscripto",
    receptor_condicion_fiscal="Responsable Inscripto",
)

#: Emisor RI + receptor Consumidor Final: **sin** R7.
CTX_RI_CF = ContextoTipoComprobante(
    emisor_condicion_fiscal="Responsable Inscripto",
    receptor_condicion_fiscal="Consumidor Final",
)

BASE: dict[str, Any] = {
    "fecha_emision": "2025-08-14",
    "nro_comprobante": "0001-00000001",
    "importe_total_facturado": "121.00",
}

#: Factura A coherente: concluye con certeza alta por programa.
A_COMPLETA: dict[str, Any] = {
    **BASE,
    "tipo_comprobante": "A",
    "cuit_emisor": "30-12345678-9",
    "cuit_receptor": "27-12345678-4",
    "subtotal": "100.00",
    "iva": "21.00",
}

#: Factura B con emisor y receptor RI: queda en revisión (R7).
AMBIGUO: dict[str, Any] = {
    **BASE,
    "tipo_comprobante": "B",
    "cuit_emisor": "30-12345678-9",
    "iva": "0.00",
}

#: Factura B **discriminando IVA**: contradice su propia letra (CRUZ_3 → rechazo).
#: Con emisor RI + receptor CF **no** hay R7, así que el rechazo queda de certeza
#: alta: "esto no es válido" es una conclusión, no una duda (T-501/T-503).
RECHAZO_CERTEZA_ALTA: dict[str, Any] = {
    **BASE,
    "tipo_comprobante": "B",
    "cuit_emisor": "30-12345678-9",
    "cuit_receptor": "27-12345678-4",
    "subtotal": "100.00",
    "iva": "21.00",
}

#: Evidencia incompleta: falta casi todo.
INCOMPLETO: dict[str, Any] = {"razon_social_emisor": "ACME SA"}

MODELO_VLM = "qwen2.5vl:3b"
MODELO_LLM = "qwen2.5:7b"


def _source(fuente: Fuente, campos: dict[str, Any], **kwargs: Any) -> SourceEvidence:
    return SourceEvidence(
        fuente=fuente,
        **kwargs,
        campos={
            campo: EvidenceField(
                campo=campo,
                valor=valor,
                fuente=fuente,
                fragmento_sustento=f"soporte de {campo}",
                meta=nueva_meta(
                    MODELO_VLM if fuente is Fuente.vlm else MODELO_LLM, "extraccion@1"
                ),
            )
            for campo, valor in campos.items()
        },
    )


class AgenteDoble:
    """Agente de prueba: elige dentro del universo (sin red)."""

    def __init__(self, candidato: str | None) -> None:
        self.candidato = candidato

    def decidir(self, messages, modelo, *, num_ctx=None):
        class Respuesta:
            contenido = json.dumps({"candidato": self.candidato, "justificacion": "x"})

        return Respuesta()


def _caso(
    campos: dict[str, Any] | None = None,
    *,
    documento_id: str = "doc-1",
    campos_llm: dict[str, Any] | None = None,
    contexto: ContextoTipoComprobante | None = CTX_RI_RI,
    con_resultado: bool = True,
    hitl: bool = False,
    agente: bool = False,
):
    campos = A_COMPLETA if campos is None else campos
    evidencia = combinar_evidencia(
        documento_id,
        [
            _source(Fuente.vlm, campos, reglas_aplicadas=["R1"]),
            _source(Fuente.llm, campos_llm if campos_llm is not None else campos),
        ],
    )
    if not con_resultado:
        return construir_case_record(evidencia)

    if agente:
        corrida = concluir_con_agente(
            evidencia, contexto_tipo=contexto, agente=AgenteDoble("B"), modelo="llama3.1:8b"
        )
        return construir_case_record(evidencia, resultado=corrida.resultado)

    resultado = consolidar_caso(evidencia, contexto_tipo=contexto).valor
    if hitl:
        encolar_hitl(resultado, cola=ColaHitl(hitl=HitlSettings(muestreo_tasa=0.0)))
    return construir_case_record(evidencia, resultado=resultado)


# ---------------------------------------------------------------------------
# 1. Las cuatro métricas del DoD
# ---------------------------------------------------------------------------


class TestMetricasDelDoD:
    """% certeza alta, % agente, % rechazado y acuerdo VLM/LLM."""

    def test_porcentaje_de_certeza_alta(self):
        casos = [_caso(documento_id=f"doc-{i}") for i in range(3)]

        metricas = metricas_certidumbre(casos)

        assert metricas["certeza_alta_programa"]["valor_pct"] == 100.0
        assert metricas["certeza_alta_programa"]["numerador"] == 3

    def test_porcentaje_de_agente(self):
        casos = [_caso(AMBIGUO, documento_id="a", agente=True)]

        metricas = metricas_certidumbre(casos)

        assert metricas["agente_ia"]["valor_pct"] == 100.0
        assert metricas["por_origen"] == {"agente_ia": 1}

    def test_porcentaje_de_rechazo(self):
        # Una factura B que discrimina IVA contradice su letra: el código la
        # rechaza (CRUZ_3) con **certeza alta** (RI + CF: sin R7 de por medio).
        casos = [_caso(RECHAZO_CERTEZA_ALTA, documento_id="malo", contexto=CTX_RI_CF)]

        metricas = metricas_rechazo(casos)

        assert metricas["por_estado"].get("rechazado") == 1
        assert metricas["rechazado"]["valor_pct"] == 100.0

    def test_acuerdo_vlm_llm(self):
        casos = [
            _caso(documento_id="ok"),
            _caso(
                documento_id="desacuerdo",
                campos_llm={**A_COMPLETA, "iva": "22.00"},
            ),
        ]

        metrica = acuerdo_vlm_llm(casos)

        # 2 casos × 8 campos leídos por ambas, con 1 desacuerdo.
        assert metrica["denominador"] == 2 * len(A_COMPLETA)
        assert metrica["numerador"] == 2 * len(A_COMPLETA) - 1
        assert metrica["desacuerdos"][0]["campo"] == "iva"

    def test_el_reporte_completo_trae_las_cuatro(self):
        reporte = metricas_de([_caso()])

        assert reporte["certidumbre"]["certeza_alta_programa"]["valor_pct"] == 100.0
        assert "agente_ia" in reporte["certidumbre"]
        assert "rechazado" in reporte["rechazo"]
        assert "valor_pct" in reporte["acuerdo_vlm_llm"]

    def test_cuenta_los_documentos_procesados(self):
        # El Gherkin pide "documentos procesados".
        casos = [_caso(documento_id=f"doc-{i}") for i in range(4)]

        assert metricas_de(casos)["documentos_procesados"] == 4

    def test_cada_metrica_lleva_su_definicion_y_objetivo(self):
        reporte = metricas_de([_caso()])

        for bloque in (
            reporte["certidumbre"]["certeza_alta_programa"],
            reporte["rechazo"]["rechazado"],
            reporte["acuerdo_vlm_llm"],
        ):
            assert bloque["definicion"]
            assert bloque["objetivo"]


# ---------------------------------------------------------------------------
# 2. La honestidad del cálculo
# ---------------------------------------------------------------------------


class TestHonestidadDelCalculo:
    """Cada métrica con su denominador, y "no calculable" en vez de 0%."""

    def test_un_caso_sin_resultado_no_entra_en_el_denominador(self):
        # No se sabe si un caso sin consolidar es rechazado: contarlo como "no
        # rechazado" inflaría el denominador con casos que no se midieron.
        casos = [_caso(documento_id="ok"), _caso(documento_id="sin-resultado", con_resultado=False)]

        metricas = metricas_rechazo(casos)
        certidumbre = metricas_certidumbre(casos)

        assert metricas["rechazado"]["denominador"] == 1
        assert certidumbre["casos_totales"] == 2
        assert certidumbre["casos_con_resultado"] == 1

    def test_sin_resultados_la_metrica_es_no_calculable_con_motivo(self):
        casos = [_caso(con_resultado=False)]

        metricas = metricas_certidumbre(casos)

        assert metricas["certeza_alta_programa"]["calculable"] is False
        assert metricas["certeza_alta_programa"]["valor_pct"] is None
        assert "resultado consolidado" in metricas["certeza_alta_programa"]["motivo"]

    def test_no_calculable_no_es_cero_por_ciento(self):
        # La diferencia importa: 0% afirmaría que se midió y dio cero.
        metrica = metricas_certidumbre([])["certeza_alta_programa"]

        assert metrica["valor_pct"] is None
        assert metrica["denominador"] == 0

    def test_un_cero_real_si_es_cero(self):
        # Si hay casos y ninguno salió por programa, el 0% es un dato, no un vacío.
        casos = [_caso(AMBIGUO, documento_id="a")]

        metrica = metricas_certidumbre(casos)["certeza_alta_programa"]

        assert metrica["valor_pct"] == 0.0
        assert metrica["calculable"] is True

    def test_certeza_alta_exige_certeza_y_origen(self):
        # La definición de `06` §5 dice "resueltos por reglas (origen=programa)";
        # el contrato implica certeza=alta, pero la métrica verifica las dos en vez
        # de asumir la implicación.
        caso = _caso()

        # Se fuerza un resultado inconsistente a mano para verificar la defensa.
        caso.resultado.certeza = None
        metrica = metricas_certidumbre([caso])["certeza_alta_programa"]

        assert metrica["numerador"] == 0

    def test_el_rechazo_dice_si_fue_con_certeza_alta(self):
        # La tesis del proyecto: un rechazo firme es certeza alta.
        casos = [_caso(RECHAZO_CERTEZA_ALTA, documento_id="malo", contexto=CTX_RI_CF)]

        metricas = metricas_rechazo(casos)

        assert metricas["rechazado_con_certeza_alta"]["casos"] == 1
        assert metricas["rechazado_con_certeza_alta"]["de"] == 1

    def test_un_rechazo_junto_a_una_alerta_abierta_es_certeza_baja(self):
        # El caso más sutil: la contradicción (CRUZ_3) **y** el conflicto de R7
        # (CRUZ_5) en el mismo documento. La contradicción manda el rechazo, pero
        # la alerta sin resolver baja la certeza (T-503): el rechazo es firme y la
        # duda está en el crédito fiscal. La métrica lo deja ver.
        casos = [_caso(RECHAZO_CERTEZA_ALTA, documento_id="doble", contexto=CTX_RI_RI)]

        metricas = metricas_rechazo(casos)

        assert metricas["rechazado"]["valor_pct"] == 100.0
        assert metricas["rechazado_con_certeza_alta"]["casos"] == 0
        assert "no está concluyendo" in metricas["rechazado_con_certeza_alta"]["nota"]

    def test_un_aviso_de_lote_chico(self):
        # Un % agente de 1/2 = 50% no significa lo mismo que 500/1000.
        reporte = metricas_de([_caso()])

        assert reporte["lote_chico"] is True
        assert "anecdóticos" in reporte["aviso_lote_chico"]

    def test_un_lote_grande_no_lleva_aviso(self):
        casos = [_caso(documento_id=f"doc-{i}") for i in range(MINIMO_LOTE_CONFIABLE)]

        reporte = metricas_de(casos)

        assert reporte["lote_chico"] is False
        assert "aviso_lote_chico" not in reporte


# ---------------------------------------------------------------------------
# 3. El acuerdo VLM/LLM
# ---------------------------------------------------------------------------


class TestAcuerdoVlmLlm:
    """Se compara lo leído por **ambas** fuentes, no el contrato completo."""

    def test_un_campo_que_solo_leyo_una_fuente_no_cuenta(self):
        # Dividir por el total del contrato haría parecer mal acuerdo lo que en
        # realidad es cobertura.
        solo_vlm = {**A_COMPLETA, "observaciones": "texto solo del vlm"}
        casos = [
            combinar_evidencia(
                "doc-1",
                [
                    _source(Fuente.vlm, solo_vlm),
                    _source(Fuente.llm, A_COMPLETA),
                ],
            )
        ]
        casos = [construir_case_record(ev) for ev in casos]

        metrica = acuerdo_vlm_llm(casos)

        assert metrica["denominador"] == len(A_COMPLETA)  # sin `observaciones`
        assert metrica["valor_pct"] == 100.0

    def test_el_valor_igual_cuenta_como_acuerdo(self):
        casos = [_caso(documento_id="ok")]

        assert acuerdo_vlm_llm(casos)["valor_pct"] == 100.0

    def test_el_valor_distinto_cuenta_como_desacuerdo(self):
        casos = [_caso(documento_id="x", campos_llm={**A_COMPLETA, "iva": "22.00"})]

        metrica = acuerdo_vlm_llm(casos)

        assert metrica["valor_pct"] < 100.0
        assert metrica["desacuerdos"][0]["vlm"] == "21.00"
        assert metrica["desacuerdos"][0]["llm"] == "22.00"

    def test_los_desacuerdos_incluyen_el_documento(self):
        # Los desacuerdos son la señal de "formato nuevo": hay que poder ir al caso.
        casos = [_caso(documento_id="nuevo-formato", campos_llm={**A_COMPLETA, "iva": "22.00"})]

        desacuerdo = acuerdo_vlm_llm(casos)["desacuerdos"][0]

        assert desacuerdo["documento_id"] == "nuevo-formato"

    def test_sin_las_dos_fuentes_no_hay_que_comparar(self):
        # Un caso donde solo corrió una fuente no aporta al acuerdo.
        evidencia = combinar_evidencia("doc-1", [_source(Fuente.vlm, A_COMPLETA)])
        casos = [construir_case_record(evidencia)]

        metrica = acuerdo_vlm_llm(casos)

        assert metrica["calculable"] is False
        assert metrica["denominador"] == 0
        assert "las dos fuentes" in metrica["motivo"]

    def test_limita_los_ejemplos_de_desacuerdo(self):
        # El reporte no puede crecer sin límite: lista algunos y cuenta todos.
        casos = [
            _caso(documento_id=f"d-{i}", campos_llm={**A_COMPLETA, "iva": f"{i}.00"})
            for i in range(10)
        ]

        metrica = acuerdo_vlm_llm(casos, max_ejemplos=3)

        assert len(metrica["desacuerdos"]) == 3
        assert metrica["denominador"] == 10 * len(A_COMPLETA)


# ---------------------------------------------------------------------------
# 4. La cobertura HITL
# ---------------------------------------------------------------------------


class TestCoberturaHitl:
    """Obligatorios (objetivo 100%) y muestreo (objetivo: la tasa) por separado."""

    def test_un_caso_obligatorio_pendiente_no_esta_cubierto(self):
        # Encolado no es revisado: el caso está en la cola pero nadie lo miró.
        casos = [_caso(AMBIGUO, documento_id="a", hitl=True)]

        cobertura = cobertura_hitl(casos)

        assert cobertura["obligatorios"]["denominador"] == 1
        assert cobertura["obligatorios"]["numerador"] == 0
        assert cobertura["obligatorios"]["valor_pct"] == 0.0
        assert cobertura["pendientes"] == 1

    def test_un_caso_obligatorio_revisado_esta_cubierto(self):
        evidencia = combinar_evidencia(
            "doc-1", [_source(Fuente.vlm, AMBIGUO), _source(Fuente.llm, AMBIGUO)]
        )
        resultado = consolidar_caso(evidencia, contexto_tipo=CTX_RI_RI).valor
        cola = ColaHitl(hitl=HitlSettings(muestreo_tasa=0.0))
        encolar_hitl(resultado, cola=cola)
        cola.confirmar("doc-1")
        resultado.hitl = cola.entrada("doc-1").como_hitl_decision()

        cobertura = cobertura_hitl([construir_case_record(evidencia, resultado=resultado)])

        assert cobertura["obligatorios"]["valor_pct"] == 100.0
        assert cobertura["pendientes"] == 0

    def test_el_muestreo_se_mide_aparte_de_lo_obligatorio(self):
        # Objetivos distintos (100% vs. la tasa configurada): mezclarlos daría un
        # número comparable con ninguno de los dos.
        evidencia = combinar_evidencia(
            "doc-muestra", [_source(Fuente.vlm, A_COMPLETA), _source(Fuente.llm, A_COMPLETA)]
        )
        resultado = consolidar_caso(evidencia, contexto_tipo=CTX_RI_RI).valor
        encolar_hitl(resultado, cola=ColaHitl(hitl=HitlSettings(muestreo_tasa=1.0)))

        cobertura = cobertura_hitl([construir_case_record(evidencia, resultado=resultado)])

        assert cobertura["muestreados"]["denominador"] == 1
        assert cobertura["obligatorios"]["denominador"] == 0

    def test_sin_casos_en_cola_es_no_calculable(self):
        cobertura = cobertura_hitl([_caso()])

        assert cobertura["obligatorios"]["calculable"] is False
        assert "revisión obligatoria" in cobertura["obligatorios"]["motivo"]

    def test_un_caso_sin_hitl_requerido_no_cuenta(self):
        cobertura = cobertura_hitl([_caso(documento_id="ok")])

        assert cobertura["obligatorios"]["denominador"] == 0
        assert cobertura["muestreados"]["denominador"] == 0


# ---------------------------------------------------------------------------
# 5. La tasa de alertas
# ---------------------------------------------------------------------------


class TestTasaAlertas:
    """Casos con al menos una alerta de conflicto (R7)."""

    def test_cuenta_casos_con_alerta(self):
        casos = [_caso(AMBIGUO, documento_id="con-alerta"), _caso(documento_id="ok")]

        metrica = tasa_alertas(casos)

        assert metrica["numerador"] == 1
        assert metrica["denominador"] == 2
        assert metrica["valor_pct"] == 50.0

    def test_identifica_la_regla_de_la_alerta(self):
        casos = [_caso(AMBIGUO, documento_id="con-alerta")]

        assert tasa_alertas(casos)["por_regla"]

    def test_sin_alertas_es_cero_y_calculable(self):
        metrica = tasa_alertas([_caso(documento_id="ok", contexto=CTX_RI_RI)])

        assert metrica["calculable"] is True
        assert metrica["valor_pct"] == 0.0


# ---------------------------------------------------------------------------
# 6. El reporte versionado
# ---------------------------------------------------------------------------


class TestReporteVersionado:
    """Una métrica sin la versión con que se produjo no es comparable."""

    def test_lleva_las_tres_versiones(self):
        reporte = metricas_de([_caso()])

        assert reporte["version_libreria"] == __version__
        assert reporte["schema_version"] == SCHEMA_VERSION
        assert reporte["version_traza"] == VERSION_TRAZA
        assert reporte["version"] == VERSION_METRICAS

    def test_es_serializable_a_json(self):
        reporte = metricas_de([_caso()])

        texto = json.dumps(reporte, ensure_ascii=False)

        assert json.loads(texto)["documentos_procesados"] == 1

    def test_registra_de_donde_salio_el_historico(self, tmp_path: Path):
        recorder = CaseRecorder(tmp_path)
        recorder.registrar(_caso())

        reporte = metricas_del_recorder(recorder)

        assert reporte["origen_historico"] == str(tmp_path)

    def test_el_resumen_legible_es_una_vista_sin_recalculo(self):
        reporte = metricas_de([_caso()])

        filas = dict(resumen_legible(reporte))

        assert filas["documentos procesados"] == "1"
        assert "% certeza alta (programa)" in filas

    def test_el_resumen_muestra_el_motivo_cuando_no_es_calculable(self):
        filas = dict(resumen_legible(metricas_de([])))

        assert filas["% certeza alta (programa)"].startswith("n/d")


# ---------------------------------------------------------------------------
# 7. Integración con el histórico (T-506)
# ---------------------------------------------------------------------------


class TestIntegracionConElHistorico:
    """Las métricas se calculan sobre lo que el registrador persistió."""

    def test_metricas_del_recorder(self, tmp_path: Path):
        recorder = CaseRecorder(tmp_path)
        recorder.registrar(_caso(documento_id="ok-1"))
        recorder.registrar(_caso(documento_id="ok-2"))
        recorder.registrar(_caso(AMBIGUO, documento_id="revisar"))

        reporte = metricas_del_recorder(recorder)

        assert reporte["documentos_procesados"] == 3
        assert reporte["certidumbre"]["certeza_alta_programa"]["valor_pct"] == 66.67

    def test_las_metricas_leen_los_sidecars_no_el_indice(self, tmp_path: Path):
        # El acuerdo VLM/LLM necesita la evidencia por fuente, que el índice
        # (derivado y mínimo) no lleva.
        recorder = CaseRecorder(tmp_path)
        recorder.registrar(_caso(documento_id="ok"))
        recorder.indice.unlink()

        assert metricas_del_recorder(recorder)["acuerdo_vlm_llm"]["valor_pct"] == 100.0

    def test_casos_del_historico_devuelve_los_registros(self, tmp_path: Path):
        recorder = CaseRecorder(tmp_path)
        recorder.registrar(_caso(documento_id="ok"))

        casos = casos_del_historico(recorder)

        assert [c.documento_id for c in casos] == ["ok"]

    def test_un_historico_vacio_no_rompe(self, tmp_path: Path):
        reporte = metricas_del_recorder(CaseRecorder(tmp_path))

        assert reporte["documentos_procesados"] == 0
        assert reporte["certidumbre"]["certeza_alta_programa"]["calculable"] is False

    def test_el_desacuerdo_vlm_llm_se_ve_desde_el_historico(self, tmp_path: Path):
        recorder = CaseRecorder(tmp_path)
        recorder.registrar(
            _caso(documento_id="d", campos_llm={**A_COMPLETA, "iva": "22.00"})
        )

        reporte = metricas_del_recorder(recorder)

        assert reporte["acuerdo_vlm_llm"]["desacuerdos"][0]["campo"] == "iva"


# ---------------------------------------------------------------------------
# 8. Fronteras
# ---------------------------------------------------------------------------


class TestFronteras:
    """Lo que las métricas no hacen."""

    def test_no_hay_red(self):
        import inspect

        from voucherflow.trace import metricas

        fuente = inspect.getsource(metricas)

        assert "requests" not in fuente
        assert "urllib" not in fuente
        assert "http" not in fuente

    def test_no_recalcula_el_pipeline(self):
        # Las métricas agregan el histórico: no corren reglas ni modelos.
        import inspect

        from voucherflow.trace import metricas

        fuente = inspect.getsource(metricas)

        assert ".ask(" not in fuente
        assert "Ollama" not in fuente
        assert "concluir" not in fuente

    def test_no_muta_los_casos(self, tmp_path: Path):
        recorder = CaseRecorder(tmp_path)
        recorder.registrar(_caso())
        casos = casos_del_historico(recorder)
        antes = casos[0].model_dump()

        metricas_de(casos)

        assert casos[0].model_dump() == antes

    def test_es_deterministico(self):
        casos = [_caso(documento_id=f"doc-{i}") for i in range(3)]

        assert metricas_de(casos) == metricas_de(casos)

    def test_no_duplica_el_diagnostico_del_cliente(self):
        # La otra mitad de E-LIB-5 (diagnóstico ante latencia/status inesperado)
        # vive en el cliente de modelos desde F0/T-005: acá no se reimplementa.
        from voucherflow.models.ollama import UMBRAL_LATENCIA_DIAGNOSTICO_S, OllamaClient

        assert UMBRAL_LATENCIA_DIAGNOSTICO_S > 0
        assert hasattr(OllamaClient, "_diagnostico")

    def test_las_metricas_no_dependen_del_orden(self, tmp_path: Path):
        uno = _caso(documento_id="a")
        dos = _caso(AMBIGUO, documento_id="b")

        assert metricas_de([uno, dos])["certidumbre"]["por_origen"] == metricas_de(
            [dos, uno]
        )["certidumbre"]["por_origen"]

    def test_no_hay_dependencias_nuevas(self):
        import inspect

        from voucherflow.trace import metricas

        fuente = inspect.getsource(metricas)

        assert "import numpy" not in fuente
        assert "import pandas" not in fuente

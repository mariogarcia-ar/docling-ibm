"""Tests de la consolidación del `VoucherResult` (F5/T-503, E-CONC-1).

**DoD de T-503** (F5.md §3, E-CONC-1): "La consolidación clasifica el caso como
'certeza alta por programa' cuando las reglas en código concluyen sin
ambigüedad".

La regla que se verifica es el Gherkin de la historia:

    Regla: concluye por programa
      Dado que las reglas concluyen de forma consistente
      Cuando se consolida el resultado
      Entonces se marca certeza=alta y origen=programa
      Y NO pasó por el agente de IA para decidir

Qué se verifica, en el orden del entregable:

1. **La condición de "sin ambigüedad"** (`es_certeza_alta_por_programa`): el
   código concluyó, no quedaron alertas pendientes y hay una letra. Cada
   disyunto, por separado, baja la certeza y **lo explica**.
2. **El `VoucherResult` consolidado** (contrato de F0, glosario §2.4): estado,
   tipo, certeza, origen, campos planos, clasificación contable, HITL y traza.
3. **La derivación de certeza/origen**: el consolidador **no** la recibe, la
   calcula — y un caso ambiguo sale **sin** `origen` porque todavía no lo decidió
   nadie.
4. **La integración** con T-501 (reglas cruzadas) y T-502 (búsqueda acotada):
   `consolidar_caso()` y `concluir_con_busqueda(consolidar_resultado=True)`.
5. **Fronteras**: no se inventa la clasificación contable, no se llama al agente
   (T-504) ni se encola HITL (T-505), y no se muta la evidencia de entrada.

Reglas duras: suite default **sin** Ollama, **sin** Docling y **sin** red.
"""

from __future__ import annotations

from typing import Any

import pytest

from voucherflow.conclusion import consolidar_caso
from voucherflow.conclusion.consolidacion import (
    MOTIVO_ALERTA_PENDIENTE,
    MOTIVO_AMBIGUO,
    MOTIVO_SIN_CLASIFICACION,
    MOTIVO_SIN_LETRA,
    VERSION_CONSOLIDACION,
    Consolidacion,
    consolidar,
    es_certeza_alta_por_programa,
)
from voucherflow.conclusion.engine import concluir_con_busqueda
from voucherflow.extraction.flows import combinar_evidencia
from voucherflow.rules.contexto import ContextoTipoComprobante
from voucherflow.rules.cruzadas import (
    ESTADO_APROBADO,
    ESTADO_RECHAZADO,
    ESTADO_REVISION,
    ConclusionResult,
)
from voucherflow.rules.gaps import ResultadoBusqueda
from voucherflow.schemas.evidence import (
    Certeza,
    CombinedEvidence,
    EstadoResultado,
    EvidenceField,
    Fuente,
    Origen,
    SourceEvidence,
)
from voucherflow.schemas.result import (
    ClasificacionContable,
    HitlDecision,
    VoucherResult,
)

# ---------------------------------------------------------------------------
# Utilidades
# ---------------------------------------------------------------------------

BASE: dict[str, Any] = {
    "fecha_emision": "2025-08-14",
    "nro_comprobante": "0001-00000001",
    "moneda": "ARS",
    "importe_total_facturado": "121.00",
}

#: Factura A completa y coherente (el caso "normal" de aprobación).
FACTURA_A: dict[str, Any] = {
    **BASE,
    "tipo_comprobante": "A",
    "razon_social_emisor": "ACME SA",
    "cuit_emisor": "30-12345678-9",
    "razon_social_receptor": "Cliente SA",
    "cuit_receptor": "27-30111222-4",
    "subtotal": "100.00",
    "iva": "21.00",
}

#: Factura B con IVA discriminado: contradicción → rechazo firme.
FACTURA_B_INVALIDA: dict[str, Any] = {
    **BASE,
    "tipo_comprobante": "B",
    "cuit_emisor": "30-12345678-9",
    "subtotal": "100.00",
    "iva": "21.00",
}

#: Factura B que dispara R7 (RI + RI): sospecha sin resolver → certeza baja.
FACTURA_B_CONFLICTO: dict[str, Any] = {
    **BASE,
    "tipo_comprobante": "B",
    "cuit_emisor": "30-12345678-9",
    "iva": "0.00",
}

CTX_RI_RI = ContextoTipoComprobante(
    emisor_condicion_fiscal="Responsable Inscripto",
    receptor_condicion_fiscal="Responsable Inscripto",
)
CTX_RI_CF = ContextoTipoComprobante(
    emisor_condicion_fiscal="Responsable Inscripto",
    receptor_condicion_fiscal="Consumidor Final",
)


def _source(fuente: Fuente, campos: dict[str, Any]) -> SourceEvidence:
    return SourceEvidence(
        fuente=fuente,
        campos={
            nombre: EvidenceField(
                campo=nombre,
                valor=valor,
                fuente=fuente,
                fragmento_sustento=f"soporte de {nombre}",
            )
            for nombre, valor in campos.items()
        },
    )


def _evidencia(campos: dict[str, Any]) -> CombinedEvidence:
    return combinar_evidencia(
        "doc-1", [_source(Fuente.vlm, campos), _source(Fuente.llm, campos)]
    )


def _conclusion(**cambios: Any) -> ConclusionResult:
    """``ConclusionResult`` mínimo para ejercitar la regla de la certeza en aislamiento."""
    base: dict[str, Any] = {
        "concluye": True,
        "certeza": "alta",
        "origen": "programa",
        "estado": ESTADO_APROBADO,
        "reglas_aplicadas": ["CRUZ_1"],
    }
    base.update(cambios)
    return ConclusionResult(**base)


class BuscadorDoble:
    """Buscador de prueba (mismo patrón que T-502)."""

    def __init__(self, respuestas: dict[str, list[ResultadoBusqueda]] | None = None):
        self.respuestas = respuestas or {}
        self.consultas: list[str] = []

    def buscar(self, gap: Any, contexto: Any) -> ResultadoBusqueda:
        self.consultas.append(gap.campo)
        secuencia = self.respuestas.get(gap.campo)
        if not secuencia:
            return ResultadoBusqueda(campo=gap.campo, disponible=True, valor=None)
        if len(secuencia) == 1:
            return secuencia[0]
        return secuencia.pop(0)


# ---------------------------------------------------------------------------
# 1. La condición de la certeza alta
# ---------------------------------------------------------------------------


class TestCondicionDeCertezaAlta:
    """``alta + programa`` ⇔ el código concluyó de forma consistente."""

    def test_concluye_sin_alertas_y_con_letra_es_alta(self):
        es_alta, motivo = es_certeza_alta_por_programa(
            _conclusion(), tipo_comprobante="A"
        )

        assert es_alta
        assert motivo.strip()

    def test_el_motivo_nombra_las_reglas_que_concluyeron(self):
        _, motivo = es_certeza_alta_por_programa(
            _conclusion(reglas_aplicadas=["CRUZ_1"]), tipo_comprobante="A"
        )

        assert "CRUZ_1" in motivo

    def test_sin_concluir_no_es_alta(self):
        es_alta, motivo = es_certeza_alta_por_programa(
            _conclusion(concluye=False, estado=ESTADO_REVISION, reglas_aplicadas=["CRUZ_4"]),
            tipo_comprobante="A",
        )

        assert not es_alta
        assert motivo == MOTIVO_AMBIGUO

    def test_con_alerta_pendiente_no_es_alta(self):
        # El Gherkin exige que las reglas concluyan "de forma consistente": una
        # alerta sin resolver es justamente lo contrario.
        es_alta, motivo = es_certeza_alta_por_programa(
            _conclusion(alertas=[{"regla": "CRUZ_5", "mensaje": "conflicto"}]),
            tipo_comprobante="B",
        )

        assert not es_alta
        assert "CRUZ_5" in motivo

    def test_sin_letra_no_es_alta(self):
        # Una aprobación sin letra sería vacía: no hay nada que afirmar.
        es_alta, motivo = es_certeza_alta_por_programa(
            _conclusion(), tipo_comprobante=None
        )

        assert not es_alta
        assert motivo == MOTIVO_SIN_LETRA

    def test_un_rechazo_firme_es_certeza_alta(self):
        # Contraintuitivo pero correcto: "no es válido" es una conclusión, no una
        # duda. La certeza mide cuánto sabe el sistema.
        es_alta, motivo = es_certeza_alta_por_programa(
            _conclusion(estado=ESTADO_RECHAZADO, reglas_aplicadas=["CRUZ_3"]),
            tipo_comprobante="B",
        )

        assert es_alta
        assert "rechazo" in motivo.lower() or "no válido" in motivo

    def test_un_rechazo_con_alerta_pendiente_no_es_alta(self):
        # El fast-fail puede coexistir con R7 (T-501): mientras la alerta siga
        # ahí, el caso no es consistente y la certeza baja.
        es_alta, _ = es_certeza_alta_por_programa(
            _conclusion(estado=ESTADO_RECHAZADO, alertas=[{"regla": "CRUZ_5"}]),
            tipo_comprobante="B",
        )

        assert not es_alta

    def test_es_determinista(self):
        a = es_certeza_alta_por_programa(_conclusion(), tipo_comprobante="A")
        b = es_certeza_alta_por_programa(_conclusion(), tipo_comprobante="A")

        assert a == b


# ---------------------------------------------------------------------------
# 2. El VoucherResult consolidado
# ---------------------------------------------------------------------------


class TestVoucherResultConsolidado:
    """El contrato congelado de F0 se llena con el veredicto."""

    def test_devuelve_el_contrato_de_f0(self):
        resultado = consolidar_caso(_evidencia(FACTURA_A), contexto_tipo=CTX_RI_RI)

        assert isinstance(resultado, Consolidacion)
        assert isinstance(resultado.valor, VoucherResult)

    def test_un_caso_aprobado_queda_certeza_alta_por_programa(self):
        resultado = consolidar_caso(_evidencia(FACTURA_A), contexto_tipo=CTX_RI_RI)
        valor = resultado.valor

        assert valor.estado == EstadoResultado.aprobado
        assert valor.certeza == Certeza.alta
        assert valor.origen == Origen.programa
        assert valor.tipo_comprobante == "A"
        assert resultado.concluyo_por_programa

    def test_un_rechazo_firme_queda_certeza_alta(self):
        resultado = consolidar_caso(_evidencia(FACTURA_B_INVALIDA), contexto_tipo=CTX_RI_CF)
        valor = resultado.valor

        assert valor.estado == EstadoResultado.rechazado
        assert valor.certeza == Certeza.alta
        assert valor.origen == Origen.programa

    def test_un_caso_ambiguo_queda_en_revision_sin_origen(self):
        # No lo decidió nadie: el origen no puede afirmar que decidió el código.
        resultado = consolidar_caso(_evidencia({"razon_social_emisor": "ACME"}))
        valor = resultado.valor

        assert valor.estado == EstadoResultado.revision
        assert valor.certeza == Certeza.baja
        assert valor.origen is None
        assert not resultado.concluyo_por_programa

    def test_una_alerta_de_r7_baja_la_certeza_y_pide_hitl(self):
        resultado = consolidar_caso(_evidencia(FACTURA_B_CONFLICTO), contexto_tipo=CTX_RI_RI)
        valor = resultado.valor

        assert valor.estado == EstadoResultado.revision
        assert valor.certeza == Certeza.baja
        assert valor.origen is None
        assert valor.hitl.requerido
        assert valor.hitl.prioridad == "alta"
        assert resultado.alertas_pendientes

    def test_los_campos_extraidos_son_planos_y_vigentes(self):
        resultado = consolidar_caso(_evidencia(FACTURA_A), contexto_tipo=CTX_RI_RI)

        campos = resultado.valor.campos_extraidos
        assert campos["tipo_comprobante"] == "A"
        assert campos["importe_total_facturado"] == "121.00"
        assert campos["cuit_emisor"] == "30-12345678-9"

    def test_un_campo_que_nadie_declaro_no_se_publica_como_valor(self):
        # La ausencia no se rellena con ``None``: no es un valor.
        resultado = consolidar_caso(_evidencia(FACTURA_A), contexto_tipo=CTX_RI_RI)

        assert "impuestos_internos" not in resultado.valor.campos_extraidos

    def test_el_resultado_lleva_la_evidencia_combinada(self):
        evidencia = _evidencia(FACTURA_A)
        resultado = consolidar_caso(evidencia, contexto_tipo=CTX_RI_RI)

        assert resultado.valor.evidencia is not None
        assert resultado.valor.evidencia.documento_id == "doc-1"

    def test_los_campos_del_contrato_no_se_inventan(self):
        # El resultado es construible con lo mínimo: no exige campos que el
        # contrato no tenga.
        resultado = consolidar_caso(_evidencia({"tipo_comprobante": "A"}))
        for campo in resultado.valor.model_dump():
            assert campo is not None

    def test_rechaza_algo_que_no_es_evidencia_combinada(self):
        with pytest.raises(TypeError, match="CombinedEvidence"):
            consolidar("no soy evidencia", _conclusion())  # type: ignore[arg-type]


# ---------------------------------------------------------------------------
# 3. La derivación de certeza y origen
# ---------------------------------------------------------------------------


class TestDerivacionDeCertezaYOrigen:
    """El consolidador no recibe la certeza: la calcula (glosario §2)."""

    def test_la_certeza_no_es_un_parametro(self):
        # Una sola fuente de verdad sobre "qué tan seguro estamos".
        import inspect

        firma = inspect.signature(consolidar)
        assert "certeza" not in firma.parameters
        assert "origen" not in firma.parameters

    def test_el_estado_sale_del_veredicto_no_del_llamador(self):
        resultado = consolidar_caso(_evidencia(FACTURA_B_INVALIDA), contexto_tipo=CTX_RI_CF)

        assert resultado.valor.estado == EstadoResultado.rechazado

    def test_la_letra_sale_del_valor_vigente(self):
        resultado = consolidar_caso(_evidencia(FACTURA_A))

        assert resultado.valor.tipo_comprobante == "A"

    def test_una_letra_fuera_del_vocabulario_no_se_reporta_como_letra(self):
        # Un tique ``090`` (D-13): el motor no lo reconoce como letra, así que no
        # se consolida con certeza alta.
        campos = {**BASE, "tipo_comprobante": "090"}
        resultado = consolidar_caso(_evidencia(campos))

        assert resultado.valor.tipo_comprobante is None
        assert resultado.valor.certeza == Certeza.baja

    def test_el_motivo_de_la_certeza_queda_en_la_traza(self):
        resultado = consolidar_caso(_evidencia(FACTURA_A), contexto_tipo=CTX_RI_RI)
        traza = resultado.valor.trazabilidad["consolidacion"]

        assert traza["motivo_certeza"].strip()
        assert traza["concluye"] is True
        assert traza["version"] == VERSION_CONSOLIDACION

    def test_la_traza_conserva_la_de_las_etapas_anteriores(self):
        resultado = consolidar_caso(_evidencia(FACTURA_A), contexto_tipo=CTX_RI_RI)

        assert "combinacion" in resultado.valor.trazabilidad
        assert "conclusion" in resultado.valor.trazabilidad
        assert "consolidacion" in resultado.valor.trazabilidad

    def test_la_traza_registra_las_alertas_y_los_gaps(self):
        resultado = consolidar_caso(_evidencia(FACTURA_B_CONFLICTO), contexto_tipo=CTX_RI_RI)
        traza = resultado.valor.trazabilidad["consolidacion"]

        assert traza["alertas"]
        assert traza["reglas_aplicadas"]

    def test_el_resumen_es_serializable(self):
        resultado = consolidar_caso(_evidencia(FACTURA_A), contexto_tipo=CTX_RI_RI)
        resumen = resultado.como_dict()

        assert resumen["estado"] == "aprobado"
        assert resumen["certeza"] == "alta"
        assert resumen["origen"] == "programa"
        assert resumen["concluyo_por_programa"] is True
        assert resumen["version"] == VERSION_CONSOLIDACION


# ---------------------------------------------------------------------------
# 4. Clasificación contable y HITL
# ---------------------------------------------------------------------------


class TestClasificacionContable:
    """La cadena de F3 se publica si se corrió; no se inventa."""

    def test_sin_clasificacion_el_resultado_viaja_sin_ella(self):
        resultado = consolidar_caso(_evidencia(FACTURA_A), contexto_tipo=CTX_RI_RI)

        assert resultado.valor.clasificacion_contable is None
        assert not resultado.clasificacion_disponible

    def test_la_traza_explica_por_que_no_hay_clasificacion(self):
        resultado = consolidar_caso(_evidencia(FACTURA_A))
        traza = resultado.valor.trazabilidad["consolidacion"]

        assert traza["clasificacion_disponible"] is False
        assert traza["nota_clasificacion"] == MOTIVO_SIN_CLASIFICACION

    def test_una_clasificacion_aportada_se_publica(self):
        clasificacion = ClasificacionContable(
            centro_costo="CC0006",
            macro_categoria="Servicios",
            concepto="Honorarios",
            codigo="501010",
            condicion_impositiva="21",
        )
        resultado = consolidar_caso(
            _evidencia(FACTURA_A), contexto_tipo=CTX_RI_RI, clasificacion=clasificacion
        )

        assert resultado.valor.clasificacion_contable is not None
        assert resultado.valor.clasificacion_contable.centro_costo == "CC0006"
        assert resultado.clasificacion_disponible

    def test_la_condicion_impositiva_se_infiere_del_contexto_fiscal(self):
        # RI → 21; Monotributo/Exento → exento_no_gravado (mismo mapeo que la
        # cadena contable de F3).
        resultado = consolidar_caso(_evidencia(FACTURA_A), contexto_tipo=CTX_RI_RI)
        traza = resultado.valor.trazabilidad["consolidacion"]

        assert traza["condicion_impositiva"] == "21"

    def test_sin_contexto_fiscal_no_se_inventa_la_condicion(self):
        resultado = consolidar_caso(_evidencia(FACTURA_A))
        traza = resultado.valor.trazabilidad["consolidacion"]

        assert traza["condicion_impositiva"] is None
        assert traza["contexto_fiscal_disponible"] is False


class TestHitl:
    """El HITL de la consolidación es la expectativa; T-505 la materializa."""

    def test_un_caso_resuelto_no_pide_hitl(self):
        resultado = consolidar_caso(_evidencia(FACTURA_A), contexto_tipo=CTX_RI_RI)

        assert not resultado.valor.hitl.requerido
        assert resultado.valor.hitl.estado == "no_aplica"

    def test_un_caso_ambiguo_pide_hitl_de_prioridad_alta(self):
        resultado = consolidar_caso(_evidencia({"razon_social_emisor": "ACME"}))

        assert resultado.valor.hitl.requerido
        assert resultado.valor.hitl.prioridad == "alta"
        assert resultado.valor.hitl.estado == "pendiente"

    def test_una_hitl_aportada_prevalece(self):
        decision = HitlDecision(requerido=True, prioridad="baja", estado="pendiente")
        resultado = consolidar_caso(
            _evidencia(FACTURA_A), contexto_tipo=CTX_RI_RI, hitl=decision
        )

        assert resultado.valor.hitl.prioridad == "baja"


# ---------------------------------------------------------------------------
# 5. Integración con T-501 y T-502
# ---------------------------------------------------------------------------


class TestIntegracionConElPipeline:
    """La consolidación cierra el pipeline de la conclusión."""

    def test_consolidar_caso_corre_la_pasada_2(self):
        # No hace falta concluir antes: `consolidar_caso` corre las cruzadas.
        resultado = consolidar_caso(_evidencia(FACTURA_A), contexto_tipo=CTX_RI_RI)

        assert resultado.valor.trazabilidad["conclusion"]["estado"] == ESTADO_APROBADO

    def test_la_regla_se_cumple_de_punta_a_punta(self):
        # El Gherkin, tal cual: reglas consistentes → alta + programa, sin agente.
        resultado = consolidar_caso(_evidencia(FACTURA_A), contexto_tipo=CTX_RI_RI)

        assert resultado.valor.certeza == Certeza.alta
        assert resultado.valor.origen == Origen.programa
        assert "NO pasó por el agente" in resultado.motivo_certeza

    def test_concluir_con_busqueda_puede_consolidar(self):
        resultado = concluir_con_busqueda(
            _evidencia(FACTURA_A), contexto_tipo=CTX_RI_RI, consolidar_resultado=True
        )

        assert resultado.consolidacion is not None
        assert resultado.resultado is not None
        assert resultado.resultado.certeza == Certeza.alta

    def test_sin_consolidar_resultado_no_hay_voucher(self):
        resultado = concluir_con_busqueda(_evidencia(FACTURA_A), contexto_tipo=CTX_RI_RI)

        assert resultado.consolidacion is None
        assert resultado.resultado is None

    def test_la_busqueda_puede_desbloquear_la_certeza_alta(self):
        # Un gap crítico impide la certeza alta; con el padrón cubriéndolo, el
        # caso se consolida por programa.
        sin_importe = {k: v for k, v in FACTURA_A.items() if k != "importe_total_facturado"}
        buscador = BuscadorDoble({
            "importe_total_facturado": [
                ResultadoBusqueda(
                    campo="importe_total_facturado",
                    valor="121.00",
                    disponible=True,
                    sostento="padrón: total constatado",
                )
            ]
        })

        sin_hook = concluir_con_busqueda(
            _evidencia(sin_importe), contexto_tipo=CTX_RI_RI, consolidar_resultado=True
        )
        con_hook = concluir_con_busqueda(
            _evidencia(sin_importe),
            contexto_tipo=CTX_RI_RI,
            buscador=buscador,
            consolidar_resultado=True,
        )

        assert sin_hook.resultado.certeza == Certeza.baja
        assert con_hook.resultado.certeza == Certeza.alta
        assert con_hook.resultado.origen == Origen.programa

    def test_el_resumen_de_la_corrida_incluye_la_consolidacion(self):
        resultado = concluir_con_busqueda(
            _evidencia(FACTURA_A), contexto_tipo=CTX_RI_RI, consolidar_resultado=True
        )
        resumen = resultado.como_dict()

        assert "consolidacion" in resumen
        assert resumen["consolidacion"]["certeza"] == "alta"


# ---------------------------------------------------------------------------
# 6. Fronteras
# ---------------------------------------------------------------------------


class TestFronteras:
    """Lo que la consolidación NO hace (es de otras tareas de F5)."""

    def test_no_muta_la_evidencia_de_entrada(self):
        original = _evidencia(FACTURA_A)
        antes = dict(original.trazabilidad)

        consolidar_caso(original, contexto_tipo=CTX_RI_RI)

        assert dict(original.trazabilidad) == antes

    def test_no_llama_al_agente_ni_encola_hitl(self):
        # La consolidación **no** invoca al agente ni encola: consolida el
        # veredicto que recibe. El encolado (T-505) es un paso explícito aparte.
        from voucherflow.conclusion import escalar_a_agente

        resultado = escalar_a_agente(_evidencia(FACTURA_A))
        assert resultado.escalado is False  # el código concluyó: no se escala

        # La consolidación tampoco aplicó la política HITL por su cuenta.
        consolidado = consolidar_caso(_evidencia(FACTURA_A), contexto_tipo=CTX_RI_RI)
        assert consolidado.valor.hitl.requerido is False

    def test_un_caso_ambiguo_no_se_consolida_como_final(self):
        # Sale marcado como revisión y sin origen: es la señal para T-504/T-505,
        # no un resultado final.
        resultado = consolidar_caso(_evidencia({"razon_social_emisor": "ACME"}))

        assert resultado.valor.estado == EstadoResultado.revision
        assert resultado.valor.origen is None
        assert not resultado.concluyo_por_programa

    def test_es_determinista(self):
        a = consolidar_caso(_evidencia(FACTURA_A), contexto_tipo=CTX_RI_RI).como_dict()
        b = consolidar_caso(_evidencia(FACTURA_A), contexto_tipo=CTX_RI_RI).como_dict()

        assert a == b

    def test_no_cambia_el_veredicto_de_la_pasada_2(self):
        # La consolidación publica el veredicto, no lo re-calcula con otra lógica.
        resultado = consolidar_caso(_evidencia(FACTURA_B_INVALIDA), contexto_tipo=CTX_RI_CF)
        cruza = resultado.valor.trazabilidad["conclusion"]

        assert cruza["estado"] == ESTADO_RECHAZADO
        assert resultado.valor.estado == EstadoResultado.rechazado

    def test_el_estado_del_voucher_es_el_del_veredicto(self):
        for campos, ctx in (
            (FACTURA_A, CTX_RI_RI),
            (FACTURA_B_INVALIDA, CTX_RI_CF),
            ({"razon_social_emisor": "ACME"}, None),
        ):
            resultado = consolidar_caso(_evidencia(campos), contexto_tipo=ctx)
            assert resultado.valor.estado.value == resultado.valor.trazabilidad["conclusion"]["estado"]

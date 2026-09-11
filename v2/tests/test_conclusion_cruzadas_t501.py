"""Tests de las reglas cruzadas de conclusión (F5/T-501, E-CONC-1).

**DoD de T-501** (F5.md §3, E-CONC-1): "Las reglas cruzadas (negocio, fast-fail y
conflicto R7) corren sobre la evidencia combinada de F4 y determinan la
certeza/origen correctos".

Qué se verifica, en el orden del entregable:

1. **El contexto de la pasada 2** (``ContextoConclusion.desde_evidencia``): lee la
   resolución por campo de F4 (valor vigente + fuente responsable), reporta los
   campos ausentes y los gaps **sin inventarlos**, y evalúa la coherencia de la
   letra **sobre el caso combinado** (reutilizando ``COHERENCIA_POR_CAMPO`` de
   T-403).
2. **Las tres familias de reglas cruzadas** (``rules/cruzadas.py``): negocio
   (R1-R7 re-aplicadas sobre los valores vigentes), fast-fail (contradicción de la
   letra; falta de sostén) y conflicto R7 (sospecha → revisión, no rechazo).
3. **La derivación de certeza/origen** (glosario §2): la certeza sale de la etapa
   que decidió; ``origen=programa`` implica ``certeza=alta`` y **el contrato de F0
   lo hace cumplir** (un caso ambiguo no puede llevar un ``Decision`` de
   programa con certeza baja — se verificó al implementar).
4. **El ``ConclusionResult``** (doc 03 §4.5): el estado completo del caso,
   incluido el ambiguo, con sus gaps, conflictos, candidatos y la expectativa de
   HITL.
5. **Fronteras**: la pasada 2 **no** busca evidencia (T-502), **no** llama al
   agente (T-504) ni encola HITL (T-505); no muta la evidencia de entrada; los
   candidatos son un conjunto cerrado (ADR-008); la ausencia no se castiga como
   si fuera un valor.

Reglas duras: suite default **sin** Ollama real ni Docling real.
"""

from __future__ import annotations

from typing import Any

import pytest

from voucherflow.conclusion import concluir, concluir_caso
from voucherflow.conclusion.agent import AGENTE_NO_ESCALADO
from voucherflow.conclusion.engine import encolar_hitl
from voucherflow.extraction.flows import combinar_evidencia
from voucherflow.rules.contexto import ContextoTipoComprobante
from voucherflow.rules.contexto_conclusion import (
    CAMPOS_CRITICOS,
    ContextoConclusion,
    es_monto_cero,
)
from voucherflow.rules.cruzadas import (
    CRUZ_1_NEGOCIO,
    CRUZ_2_LETRA_SIN_SOSTEN,
    CRUZ_3_COHERENCIA_LETRA,
    CRUZ_4_DATOS_FALTANTES,
    CRUZ_5_CONFLICTO_CREDITO,
    ESTADO_APROBADO,
    ESTADO_RECHAZADO,
    ESTADO_REVISION,
    FAMILIA_CONFLICTO,
    FAMILIA_FAST_FAIL,
    FAMILIA_NEGOCIO,
    FAMILIA_POR_REGLA,
    REGISTRO_CRUZADAS,
    VERSION_CRUZADAS,
    ConclusionResult,
    construir_decision,
    construir_registro_cruzadas,
    evaluar_cruzadas,
)
from voucherflow.schemas.evidence import (
    Certeza,
    CombinedEvidence,
    EvidenceField,
    Fuente,
    Origen,
    SourceEvidence,
)

# ---------------------------------------------------------------------------
# Utilidades (mismo patrón que los tests de F4)
# ---------------------------------------------------------------------------

#: Campos mínimos para que un caso sea "completo" (sin gaps) salvo que el test
#: pida lo contrario.
BASE: dict[str, Any] = {
    "fecha_emision": "2025-08-14",
    "nro_comprobante": "0001-00000001",
    "moneda": "ARS",
    "importe_total_facturado": "121.00",
}

#: Contexto fiscal del caso "normal": emisor y receptor Responsable Inscripto.
CTX_RI_RI = ContextoTipoComprobante(
    emisor_condicion_fiscal="Responsable Inscripto",
    receptor_condicion_fiscal="Responsable Inscripto",
)

#: Contexto fiscal "B típica": emisor RI, receptor Consumidor Final.
CTX_RI_CF = ContextoTipoComprobante(
    emisor_condicion_fiscal="Responsable Inscripto",
    receptor_condicion_fiscal="Consumidor Final",
)


def _source(fuente: Fuente, campos: dict[str, Any], **extra: Any) -> SourceEvidence:
    """``SourceEvidence`` con los campos indicados (los valores ya canónicos)."""
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
        **extra,
    )


def _evidencia(campos: dict[str, Any], *, con_llm: bool = True) -> CombinedEvidence:
    """Evidencia combinada (T-404) a partir de los campos del caso."""
    fuentes = [_source(Fuente.vlm, campos)]
    if con_llm:
        fuentes.append(_source(Fuente.llm, campos))
    return combinar_evidencia("doc-1", fuentes)


def _cruzadas(campos: dict[str, Any], *, contexto_tipo: ContextoTipoComprobante | None = None):
    """Corre la pasada 2 sobre un caso y devuelve ``(contexto, veredicto)``."""
    evidencia = _evidencia(campos)
    contexto = ContextoConclusion.desde_evidencia(evidencia, contexto_tipo=contexto_tipo)
    return contexto, evaluar_cruzadas(contexto)


# ---------------------------------------------------------------------------
# 1. El contexto de la pasada 2
# ---------------------------------------------------------------------------


class TestContextoConclusion:
    """``ContextoConclusion`` proyecta la evidencia combinada de F4."""

    def test_lee_el_valor_vigente_y_su_fuente(self):
        # El valor vigente es el que resolvió la precedencia (T-404); la fuente
        # responsable queda registrada para auditoría.
        campos = {**BASE, "tipo_comprobante": "A", "cuit_emisor": "30-12345678-9"}
        ctx = ContextoConclusion.desde_evidencia(_evidencia(campos))

        assert ctx.letra == "A"
        assert ctx.valor("cuit_emisor") == "30-12345678-9"
        assert ctx.fuente_letra is not None

    def test_un_campo_que_nadie_declaro_se_reporta_no_se_inventa(self):
        # Un campo del contrato que ninguna fuente declaró no aparece con un
        # valor inventado: queda en ``campos_ausentes`` (ADR-001). El universo
        # de ``campos_ausentes`` es el **contrato** de extracción (incluye los
        # campos que la combinación ni menciona) más los extra del modo genérico
        # que hayan quedado sin ganador.
        campos = {"tipo_comprobante": "A", "cuit_emisor": "30-12345678-9"}
        ctx = ContextoConclusion.desde_evidencia(_evidencia(campos))

        assert not ctx.tiene("importe_total_facturado")
        assert "importe_total_facturado" in ctx.campos_ausentes
        assert ctx.valor("importe_total_facturado") is None

    def test_los_campos_ausentes_cubren_todo_el_contrato(self):
        # Un campo del contrato que nadie declaró tiene que estar en
        # ``campos_ausentes`` aunque la evidencia combinada no lo mencione.
        from voucherflow.extraction import CAMPOS_EXTRACCION

        ctx = ContextoConclusion.desde_evidencia(_evidencia({"razon_social_emisor": "ACME"}))

        for campo in CAMPOS_EXTRACCION:
            if not ctx.tiene(campo):
                assert campo in ctx.campos_ausentes, campo

    def test_los_gaps_son_los_campos_criticos_faltantes(self):
        # La señal del gap (insumo de T-502) es el subconjunto crítico ausente.
        ctx = ContextoConclusion.desde_evidencia(_evidencia({"razon_social_emisor": "ACME"}))

        assert ctx.hay_gap
        for campo in CAMPOS_CRITICOS:
            assert campo in ctx.campos_criticos_ausentes

    def test_sin_gap_cuando_estan_los_criticos(self):
        campos = {**BASE, "tipo_comprobante": "A"}
        ctx = ContextoConclusion.desde_evidencia(_evidencia(campos))

        assert not ctx.hay_gap
        assert ctx.campos_criticos_ausentes == []

    def test_un_caso_completo_puede_no_tener_gap_y_ser_incoherente(self):
        # Las dos cosas son independientes: tener todos los críticos no implica
        # coherencia (una B con IVA los tiene todos y se contradice).
        campos = {
            **BASE,
            "tipo_comprobante": "B",
            "cuit_emisor": "30-12345678-9",
            "subtotal": "100.00",
            "iva": "21.00",
        }
        ctx = ContextoConclusion.desde_evidencia(_evidencia(campos))

        assert not ctx.hay_gap
        assert not ctx.coherente

    def test_la_letra_fuera_del_vocabulario_no_se_trata_como_letra(self):
        # ``"090"`` está en el enum de F0 pero no en el vocabulario del motor
        # (D-13): no se asume una letra conocida.
        campos = {**BASE, "tipo_comprobante": "090"}
        ctx = ContextoConclusion.desde_evidencia(_evidencia(campos))

        assert ctx.letra is None or ctx.letra in {"A", "B", "C", "M", "E"}

    def test_rechaza_algo_que_no_es_evidencia_combinada(self):
        with pytest.raises(TypeError, match="CombinedEvidence"):
            ContextoConclusion.desde_evidencia({"tipo_comprobante": "A"})  # type: ignore[arg-type]

    def test_el_contexto_es_inmutable(self):
        ctx = ContextoConclusion.desde_evidencia(_evidencia({**BASE, "tipo_comprobante": "A"}))
        with pytest.raises(Exception):
            ctx.letra = "B"  # type: ignore[misc]


class TestCoherenciaDelCaso:
    """La coherencia de la letra se evalúa sobre el **caso combinado** (T-403)."""

    def test_factura_a_completa_es_coherente(self):
        campos = {
            **BASE,
            "tipo_comprobante": "A",
            "cuit_emisor": "30-12345678-9",
            "cuit_receptor": "27-30111222-4",
            "subtotal": "100.00",
            "iva": "21.00",
        }
        ctx = ContextoConclusion.desde_evidencia(_evidencia(campos))

        assert ctx.coherente
        assert ctx.incoherencias == []

    def test_factura_a_sin_cuit_receptor_es_incoherente(self):
        # El caso textual de E-EXT-2, ahora sobre el valor vigente del caso.
        campos = {
            **BASE,
            "tipo_comprobante": "A",
            "cuit_emisor": "30-12345678-9",
            "subtotal": "100.00",
            "iva": "21.00",
        }
        ctx = ContextoConclusion.desde_evidencia(_evidencia(campos))

        assert not ctx.coherente
        assert any("cuit_receptor" in motivo for motivo in ctx.incoherencias)

    def test_factura_b_con_iva_discriminado_es_incoherente(self):
        campos = {
            **BASE,
            "tipo_comprobante": "B",
            "cuit_emisor": "30-12345678-9",
            "subtotal": "100.00",
            "iva": "21.00",
        }
        ctx = ContextoConclusion.desde_evidencia(_evidencia(campos))

        assert not ctx.coherente
        assert any("iva" in motivo.lower() for motivo in ctx.incoherencias)

    def test_factura_b_con_iva_en_cero_es_coherente(self):
        # El cero admite varias formas (``0``, ``"0"``, ``"0,00"``): la
        # comparación es tolerante, igual que en T-403.
        for forma in (0, 0.0, "0", "0,00", "0.00"):
            campos = {
                **BASE,
                "tipo_comprobante": "B",
                "cuit_emisor": "30-12345678-9",
                "iva": forma,
            }
            ctx = ContextoConclusion.desde_evidencia(_evidencia(campos))
            assert ctx.coherente, f"forma {forma!r} debería ser coherente"

    def test_un_iva_ausente_no_se_juzga(self):
        # "No se leyó el IVA" no es "IVA discriminado": la ausencia no se castiga
        # como si fuera un valor (ADR-001).
        campos = {**BASE, "tipo_comprobante": "B", "cuit_emisor": "30-12345678-9"}
        ctx = ContextoConclusion.desde_evidencia(_evidencia(campos))

        assert ctx.coherente

    def test_sin_letra_no_hay_nada_que_evaluar(self):
        ctx = ContextoConclusion.desde_evidencia(_evidencia(BASE))

        assert ctx.letra is None
        assert ctx.coherente


class TestSenalesDeR6:
    """El desglose de importes señala si el documento discrimina IVA (R6)."""

    def test_iva_discriminado_cuando_neto_mas_iva_explican_el_total(self):
        campos = {
            **BASE,
            "tipo_comprobante": "A",
            "subtotal": "100.00",
            "iva": "21.00",
            "importe_total_facturado": "121.00",
        }
        ctx = ContextoConclusion.desde_evidencia(_evidencia(campos))

        assert ctx.contexto_tipo.desglose_iva_discriminado is True

    def test_sin_desglose_cuando_el_iva_es_cero(self):
        campos = {**BASE, "tipo_comprobante": "B", "subtotal": "121.00", "iva": "0.00"}
        ctx = ContextoConclusion.desde_evidencia(_evidencia(campos))

        assert ctx.contexto_tipo.desglose_iva_discriminado is False

    def test_desconocido_cuando_no_hay_evidencia_suficiente(self):
        # Sin IVA declarado no se puede observar el desglose: ``None``, y R6 no
        # infiere letra (no se asume).
        ctx = ContextoConclusion.desde_evidencia(_evidencia(BASE))

        assert ctx.contexto_tipo.desglose_iva_discriminado is None


class TestImportes:
    """``es_monto_cero`` / ``iva_es_cero`` distinguen cero de "no se sabe"."""

    @pytest.mark.parametrize("valor", [0, 0.0, "0", "0,00", "0.00", -0.0])
    def test_cero_en_cualquier_forma(self, valor: Any):
        assert es_monto_cero(valor)

    def test_el_formato_contable_con_coma_tambien_se_interpreta(self):
        # El documento imprime ``1.234,56``; la normalización de T-402 ya lo
        # vuelve ``1234.56``, pero un valor crudo que llegue sin normalizar no
        # debe leerse como un monto distinto (ni como cero).
        from voucherflow.rules.contexto_conclusion import ContextoConclusion

        assert es_monto_cero("0,00")
        assert not es_monto_cero("1.234,56")
        ctx = ContextoConclusion.desde_evidencia(_evidencia({**BASE, "iva": "1.234,56"}))
        assert ctx.iva_es_cero is False

    @pytest.mark.parametrize("valor", [21, 0.01, "21.00", "1.234,56", None, "ilegible"])
    def test_no_cero_o_no_interpretable(self, valor: Any):
        assert not es_monto_cero(valor)

    def test_iva_es_cero_es_none_si_no_se_leyo(self):
        ctx = ContextoConclusion.desde_evidencia(_evidencia(BASE))
        assert ctx.iva_es_cero is None

    def test_iva_es_cero_es_true_si_se_leyo_cero(self):
        ctx = ContextoConclusion.desde_evidencia(_evidencia({**BASE, "iva": "0.00"}))
        assert ctx.iva_es_cero is True


# ---------------------------------------------------------------------------
# 2. El registro de reglas cruzadas
# ---------------------------------------------------------------------------


class TestRegistroCruzadas:
    """Las reglas cruzadas se declaran como ``Rule`` del motor de F0."""

    def test_el_registro_tiene_las_cinco_reglas(self):
        ids = [regla.id for regla in REGISTRO_CRUZADAS.reglas]
        assert ids == [
            CRUZ_3_COHERENCIA_LETRA,
            CRUZ_2_LETRA_SIN_SOSTEN,
            CRUZ_4_DATOS_FALTANTES,
            CRUZ_5_CONFLICTO_CREDITO,
            CRUZ_1_NEGOCIO,
        ]

    def test_todas_son_de_tipo_cruzada_y_tienen_detalle(self):
        for regla in REGISTRO_CRUZADAS.reglas:
            assert regla.tipo == "cruzada", regla.id
            assert regla.detalle.strip(), regla.id

    def test_cada_regla_tiene_familia_declarada(self):
        # La familia es lo que el reporte usa para decir *qué clase* de regla
        # resolvió el caso (negocio / fast-fail / conflicto).
        for regla in REGISTRO_CRUZADAS.reglas:
            assert regla.id in FAMILIA_POR_REGLA, regla.id
            assert FAMILIA_POR_REGLA[regla.id] in {
                FAMILIA_NEGOCIO,
                FAMILIA_FAST_FAIL,
                FAMILIA_CONFLICTO,
            }

    def test_las_prioridades_estan_ordenadas_y_son_unicas(self):
        prioridades = [regla.prioridad for regla in REGISTRO_CRUZADAS.reglas]
        assert prioridades == sorted(prioridades)
        assert len(prioridades) == len(set(prioridades))

    def test_construir_devuelve_instancias_nuevas(self):
        # Los tests (y T-502/T-504) pueden mutar una copia sin tocar el motor.
        registro = construir_registro_cruzadas()
        assert registro is not REGISTRO_CRUZADAS
        assert [r.id for r in registro.reglas] == [r.id for r in REGISTRO_CRUZADAS.reglas]

    def test_el_registro_no_reescribe_el_motor_de_f0(self):
        # ``Registry`` sigue siendo el de F0 (contrato congelado).
        from voucherflow.rules import Registry

        assert isinstance(REGISTRO_CRUZADAS, Registry)


# ---------------------------------------------------------------------------
# 3. Las familias de reglas
# ---------------------------------------------------------------------------


class TestFamiliaFastFail:
    """La contradicción cierra el caso: el código concluyó que no."""

    def test_b_con_iva_discriminado_se_rechaza(self):
        campos = {
            **BASE,
            "tipo_comprobante": "B",
            "cuit_emisor": "30-12345678-9",
            "subtotal": "100.00",
            "iva": "21.00",
        }
        _, veredicto = _cruzadas(campos)

        assert veredicto.fast_fail
        assert veredicto.concluye
        assert veredicto.estado == ESTADO_RECHAZADO
        assert CRUZ_3_COHERENCIA_LETRA in veredicto.disparadas

    def test_a_sin_los_dos_cuit_se_rechaza(self):
        campos = {**BASE, "tipo_comprobante": "A", "cuit_emisor": "30-12345678-9"}
        _, veredicto = _cruzadas(campos)

        assert veredicto.estado == ESTADO_RECHAZADO
        assert veredicto.concluye
        assert CRUZ_3_COHERENCIA_LETRA in veredicto.disparadas

    def test_el_fast_fail_gana_a_la_ambiguedad(self):
        # Faltan críticos Y la letra se contradice: lo que importa es la
        # contradicción (el resto de los datos no cambiaría el veredicto).
        campos = {"tipo_comprobante": "A", "cuit_emisor": "30-12345678-9"}
        _, veredicto = _cruzadas(campos)

        assert veredicto.estado == ESTADO_RECHAZADO
        assert veredicto.fast_fail

    def test_sin_letra_ni_candidatos_no_se_afirma_nada(self):
        # Un caso con todos los críticos pero sin letra: no hay veredicto, y el
        # sistema no lo inventa.
        campos = {k: v for k, v in BASE.items()}
        _, veredicto = _cruzadas(campos)

        assert veredicto.estado == ESTADO_REVISION
        assert not veredicto.concluye

    def test_la_contradiccion_cierra_con_certeza_alta(self):
        # El fast-fail es una conclusión (el código resolvió que no): certeza
        # alta y origen programa.
        campos = {
            **BASE,
            "tipo_comprobante": "B",
            "cuit_emisor": "30-12345678-9",
            "subtotal": "100.00",
            "iva": "21.00",
        }
        resultado = concluir_caso(_evidencia(campos))

        assert resultado.certeza == "alta"
        assert resultado.origen == "programa"


class TestFamiliaGaps:
    """Los campos críticos ausentes son el gap (insumo de T-502)."""

    def test_el_gap_degrada_a_revision(self):
        _, veredicto = _cruzadas({"razon_social_emisor": "ACME"})

        assert veredicto.estado == ESTADO_REVISION
        assert not veredicto.concluye
        assert CRUZ_4_DATOS_FALTANTES in veredicto.disparadas

    def test_el_gap_reporta_que_falta(self):
        _, veredicto = _cruzadas({"razon_social_emisor": "ACME"})

        assert len(veredicto.faltan_datos) == len(CAMPOS_CRITICOS)
        assert "importe_total_facturado" in veredicto.faltan_datos

    def test_un_gap_parcial_reporta_solo_lo_que_falta(self):
        campos = {**BASE, "tipo_comprobante": "A", "cuit_emisor": "30-12345678-9"}
        _, veredicto = _cruzadas(campos)

        assert veredicto.faltan_datos == []


class TestFamiliaConflicto:
    """R7 sospecha: no rechaza, exige revisión."""

    def test_r7_degrada_a_revision_con_alerta(self):
        campos = {**BASE, "tipo_comprobante": "B", "cuit_emisor": "30-12345678-9", "iva": "0.00"}
        _, veredicto = _cruzadas(campos, contexto_tipo=CTX_RI_RI)

        assert veredicto.estado == ESTADO_REVISION
        assert not veredicto.concluye
        assert CRUZ_5_CONFLICTO_CREDITO in veredicto.disparadas
        assert len(veredicto.alertas) == 1

    def test_la_alerta_de_r7_trae_su_motivo_y_su_letra(self):
        campos = {**BASE, "tipo_comprobante": "B", "cuit_emisor": "30-12345678-9", "iva": "0.00"}
        _, veredicto = _cruzadas(campos, contexto_tipo=CTX_RI_RI)

        alerta = veredicto.alertas[0]
        assert alerta["regla"] == CRUZ_5_CONFLICTO_CREDITO
        assert alerta["letra"] == "B"
        assert alerta["mensaje"].strip()

    def test_sin_conflicto_cuando_el_receptor_no_es_ri(self):
        # El caso normal de una B: emisor RI, receptor Consumidor Final.
        campos = {**BASE, "tipo_comprobante": "B", "cuit_emisor": "30-12345678-9", "iva": "0.00"}
        _, veredicto = _cruzadas(campos, contexto_tipo=CTX_RI_CF)

        assert CRUZ_5_CONFLICTO_CREDITO not in veredicto.disparadas

    def test_r7_no_es_fast_fail(self):
        # Diferencia deliberada: R7 es sospecha (revisión), no contradicción.
        campos = {**BASE, "tipo_comprobante": "B", "cuit_emisor": "30-12345678-9", "iva": "0.00"}
        _, veredicto = _cruzadas(campos, contexto_tipo=CTX_RI_RI)

        assert not veredicto.fast_fail

    def test_el_negocio_que_espera_otra_letra_es_conflicto(self):
        # Cruce negocio vs. documento (D-14): un emisor Monotributo no emite
        # Factura A. El documento manda (default de F3), pero con certeza baja:
        # es discrepancia, no contradicción del documento consigo mismo.
        ctx_tipo = ContextoTipoComprobante(
            emisor_condicion_fiscal="Monotributo",
            receptor_condicion_fiscal="Consumidor Final",
        )
        campos = {
            **BASE,
            "tipo_comprobante": "A",
            "cuit_emisor": "20-12345678-9",
            "cuit_receptor": "27-30111222-4",
            "subtotal": "100.00",
            "iva": "21.00",
        }
        _, veredicto = _cruzadas(campos, contexto_tipo=ctx_tipo)

        assert CRUZ_5_CONFLICTO_CREDITO in veredicto.disparadas
        assert veredicto.estado == ESTADO_REVISION
        assert not veredicto.fast_fail

    def test_sin_contexto_fiscal_no_hay_cruce_de_negocio(self):
        # Sin condiciones fiscales no se inventa un "esperado": el negocio no
        # contradice nada.
        campos = {
            **BASE,
            "tipo_comprobante": "A",
            "cuit_emisor": "30-12345678-9",
            "cuit_receptor": "27-30111222-4",
            "subtotal": "100.00",
            "iva": "21.00",
        }
        _, veredicto = _cruzadas(campos)

        assert CRUZ_5_CONFLICTO_CREDITO not in veredicto.disparadas
        assert veredicto.estado == ESTADO_APROBADO


class TestFamiliaNegocio:
    """El negocio concluye: certeza alta por programa, sin agente."""

    def test_factura_a_completa_se_aprueba(self):
        campos = {
            **BASE,
            "tipo_comprobante": "A",
            "cuit_emisor": "30-12345678-9",
            "cuit_receptor": "27-30111222-4",
            "subtotal": "100.00",
            "iva": "21.00",
        }
        _, veredicto = _cruzadas(campos, contexto_tipo=CTX_RI_RI)

        assert veredicto.concluye
        assert veredicto.estado == ESTADO_APROBADO
        assert CRUZ_1_NEGOCIO in veredicto.disparadas

    def test_la_exportacion_concluye_por_negocio(self):
        ctx_tipo = ContextoTipoComprobante(
            emisor_condicion_fiscal="Responsable Inscripto",
            receptor_condicion_fiscal="Responsable Inscripto",
            receptor_pais="Uruguay",
        )
        campos = {**BASE, "tipo_comprobante": "E", "cuit_emisor": "30-12345678-9", "iva": "0.00"}
        _, veredicto = _cruzadas(campos, contexto_tipo=ctx_tipo)

        assert veredicto.estado == ESTADO_APROBADO
        assert veredicto.concluye

    def test_la_factura_c_de_monotributo_concluye(self):
        ctx_tipo = ContextoTipoComprobante(
            emisor_condicion_fiscal="Monotributo",
            receptor_condicion_fiscal="Consumidor Final",
        )
        campos = {**BASE, "tipo_comprobante": "C", "cuit_emisor": "20-12345678-9", "iva": "0.00"}
        _, veredicto = _cruzadas(campos, contexto_tipo=ctx_tipo)

        assert veredicto.estado == ESTADO_APROBADO

    def test_un_tique_fuera_del_vocabulario_no_se_aprueba_por_descarte(self):
        # Un tique ``090`` (D-13): el valor está declarado, pero el motor no lo
        # reconoce como letra. El caso NO se aprueba por falta de evidencia en
        # contra: queda en revisión (el patrón prohíbe aprobar por descarte).
        campos = {**BASE, "tipo_comprobante": "090"}
        _, veredicto = _cruzadas(campos)

        assert not veredicto.concluye
        assert veredicto.estado == ESTADO_REVISION
        assert CRUZ_2_LETRA_SIN_SOSTEN in veredicto.disparadas

    def test_un_caso_ambiguo_reporta_por_que(self):
        campos = {**BASE, "tipo_comprobante": "090"}
        _, veredicto = _cruzadas(campos)

        assert veredicto.motivo.strip()
        assert "letra" in veredicto.motivo.lower()

    def test_no_se_aprueba_con_una_letra_fuera_del_vocabulario(self):
        # Refuerzo del mismo principio en el resultado consolidado.
        resultado = concluir_caso(_evidencia({**BASE, "tipo_comprobante": "090"}))

        assert not resultado.concluye
        assert resultado.estado == ESTADO_REVISION
        assert resultado.certeza is None


# ---------------------------------------------------------------------------
# 4. Certeza, origen y el contrato de F0
# ---------------------------------------------------------------------------


class TestDecisionYContrato:
    """``Decision`` es el veredicto **final**: solo cuando alguien decidió."""

    def test_el_contrato_exige_que_programa_implique_certeza_alta(self):
        # Es la regla de oro del glosario §2 y el validador de F0 la hace
        # cumplir: un ``Decision`` con ``origen=programa`` y certeza baja es
        # inválido. Por eso la pasada 2 no puede emitir un ``Decision`` cuando
        # el caso quedó ambiguo.
        from voucherflow.schemas.evidence import Decision

        with pytest.raises(Exception, match="programa"):
            CombinedEvidence(
                documento_id="doc-1",
                campos={},
                decision=Decision(concluye=False, certeza="baja", origen="programa"),
            )

    def test_un_caso_que_concluye_lleva_decision_de_programa_alta(self):
        campos = {
            **BASE,
            "tipo_comprobante": "A",
            "cuit_emisor": "30-12345678-9",
            "cuit_receptor": "27-30111222-4",
            "subtotal": "100.00",
            "iva": "21.00",
        }
        evidencia = concluir(_evidencia(campos))

        assert evidencia.decision is not None
        assert evidencia.decision.origen == Origen.programa
        assert evidencia.decision.certeza == Certeza.alta
        assert evidencia.decision.concluye

    def test_un_caso_ambiguo_no_lleva_decision(self):
        # No lo decidió nadie todavía: el ``Decision`` (que afirma *quién*
        # decidió) espera al agente (T-504) o al HITL (T-505).
        evidencia = concluir(_evidencia({"razon_social_emisor": "ACME"}))

        assert evidencia.decision is None

    def test_el_estado_completo_vive_en_el_conclusion_result(self):
        # El caso ambiguo no se pierde: su estado, gaps y expectativa de HITL
        # están en el ``ConclusionResult`` (doc 03 §4.5).
        resultado = concluir_caso(_evidencia({"razon_social_emisor": "ACME"}))

        assert isinstance(resultado, ConclusionResult)
        assert resultado.estado == ESTADO_REVISION
        assert not resultado.concluye
        assert resultado.certeza is None
        assert resultado.origen is None
        assert resultado.faltan_datos
        assert resultado.hitl is not None
        assert resultado.hitl.requerido
        assert resultado.hitl.prioridad == "alta"

    def test_un_caso_concluido_no_pide_hitl_obligatorio(self):
        campos = {
            **BASE,
            "tipo_comprobante": "A",
            "cuit_emisor": "30-12345678-9",
            "cuit_receptor": "27-30111222-4",
            "subtotal": "100.00",
            "iva": "21.00",
        }
        resultado = concluir_caso(_evidencia(campos))

        assert resultado.concluye
        assert resultado.certeza == "alta"
        assert resultado.origen == "programa"
        assert not resultado.hitl.requerido
        assert resultado.hitl.estado == "no_aplica"

    def test_construir_decision_devuelve_none_si_no_concluyo(self):
        contexto, veredicto = _cruzadas({"razon_social_emisor": "ACME"})
        assert construir_decision(contexto, veredicto) is None

    def test_la_certeza_sale_de_la_etapa_no_de_la_regla(self):
        # Ninguna ``Rule`` declara certeza: el ``Decision`` la deriva del hecho
        # de que el código concluyó.
        for regla in REGISTRO_CRUZADAS.reglas:
            assert "certeza" not in str(regla.resultado)


class TestConclusionResult:
    """El contrato del diseño (doc 03 §4.5) se cumple."""

    def test_tiene_los_campos_del_diseno(self):
        resultado = concluir_caso(_evidencia({**BASE, "tipo_comprobante": "A"}))
        for campo in (
            "concluye",
            "certeza",
            "origen",
            "candidatos_descartados",
            "candidatos_restantes",
            "reglas_aplicadas",
            "alertas",
            "hitl",
        ):
            assert hasattr(resultado, campo), campo

    def test_es_serializable_para_la_trazabilidad(self):
        resultado = concluir_caso(_evidencia({"razon_social_emisor": "ACME"}))
        resumen = resultado.como_dict()

        assert resumen["version"] == VERSION_CRUZADAS
        assert resumen["estado"] == ESTADO_REVISION
        assert isinstance(resumen["reglas_por_familia"], dict)

    def test_rechaza_algo_que_no_es_evidencia_combinada(self):
        with pytest.raises(TypeError, match="CombinedEvidence"):
            concluir_caso("no soy evidencia")  # type: ignore[arg-type]

    def test_rechaza_algo_que_no_es_evidencia_en_concluir(self):
        with pytest.raises(TypeError, match="CombinedEvidence"):
            concluir(None)  # type: ignore[arg-type]


# ---------------------------------------------------------------------------
# 5. Candidatos (ADR-008: conjunto cerrado)
# ---------------------------------------------------------------------------


class TestCandidatos:
    """Los candidatos no pueden estar descartados y restantes a la vez."""

    def test_los_candidatos_se_curan_al_construir_el_contexto(self):
        # El blindaje de F3 (un valor en las dos listas no es candidato) se
        # conserva en la conclusión.
        ctx = ContextoConclusion.desde_evidencia(_evidencia({**BASE, "tipo_comprobante": "A"}))
        curado = ctx.con_candidatos(descartados=["A", "B"], restantes=["A"])

        assert "A" in curado.candidatos_restantes
        assert "A" not in curado.candidatos_descartados

    def test_la_decision_no_repite_un_candidato_en_las_dos_listas(self):
        # El contrato de F0 valida esto; el armado del ``Decision`` no puede
        # violarlo.
        campos = {
            **BASE,
            "tipo_comprobante": "A",
            "cuit_emisor": "30-12345678-9",
            "cuit_receptor": "27-30111222-4",
            "subtotal": "100.00",
            "iva": "21.00",
        }
        evidencia = concluir(_evidencia(campos))
        decision = evidencia.decision

        assert decision is not None
        assert not set(decision.candidatos_descartados) & set(decision.candidatos_restantes)

    def test_con_candidatos_no_muta_el_original(self):
        ctx = ContextoConclusion.desde_evidencia(_evidencia({**BASE, "tipo_comprobante": "A"}))
        antes = list(ctx.candidatos_descartados)
        ctx.con_candidatos(descartados=["B"], restantes=["A"])

        assert list(ctx.candidatos_descartados) == antes


# ---------------------------------------------------------------------------
# 6. Trazabilidad y fronteras
# ---------------------------------------------------------------------------


class TestTrazabilidad:
    """La pasada 2 deja su traza para el ``CaseRecord`` (E-CONC-5)."""

    def test_la_trazabilidad_conserva_la_de_f4_y_agrega_la_conclusion(self):
        evidencia = concluir(_evidencia({**BASE, "tipo_comprobante": "A"}))

        assert "combinacion" in evidencia.trazabilidad
        assert "conclusion" in evidencia.trazabilidad
        assert evidencia.trazabilidad["etapa"] == VERSION_CRUZADAS

    def test_la_traza_publica_reglas_por_familia_y_el_motivo(self):
        campos = {
            **BASE,
            "tipo_comprobante": "A",
            "cuit_emisor": "30-12345678-9",
            "cuit_receptor": "27-30111222-4",
            "subtotal": "100.00",
            "iva": "21.00",
        }
        evidencia = concluir(_evidencia(campos))
        traza = evidencia.trazabilidad["conclusion"]

        assert traza["estado"] == ESTADO_APROBADO
        assert traza["motivo"].strip()
        assert isinstance(traza["reglas_por_familia"], dict)
        assert traza["letra_vigente"] == "A"
        assert traza["reglas_por_familia"][FAMILIA_NEGOCIO] == [CRUZ_1_NEGOCIO]

    def test_la_traza_registra_los_gaps_y_los_campos_ausentes(self):
        evidencia = concluir(_evidencia({"razon_social_emisor": "ACME"}))
        traza = evidencia.trazabilidad["conclusion"]

        assert traza["campos_criticos_ausentes"]
        assert traza["campos_ausentes"]

    def test_la_traza_deja_la_nota_de_alcance(self):
        # La nota explica por qué el ``Decision`` puede ir en ``None`` y qué
        # tareas de F5 siguen pendientes: el caso ambiguo no es un bug.
        evidencia = concluir(_evidencia({"razon_social_emisor": "ACME"}))

        assert "nota" in evidencia.trazabilidad["conclusion"]
        assert "T-504" in evidencia.trazabilidad["conclusion"]["nota"]


class TestFronteras:
    """Lo que la pasada 2 NO hace (es de otras tareas de F5)."""

    def test_no_muta_la_evidencia_de_entrada(self):
        original = _evidencia({"razon_social_emisor": "ACME"})
        antes_decision = original.decision
        antes_traza = dict(original.trazabilidad)

        concluir(original)

        assert original.decision is antes_decision
        assert dict(original.trazabilidad) == antes_traza

    def test_conserva_los_campos_de_la_evidencia_combinada(self):
        campos = {"tipo_comprobante": "A", "cuit_emisor": "30-12345678-9"}
        original = _evidencia(campos)
        salida = concluir(original)

        assert set(salida.campos) == set(original.campos)

    def test_no_busca_evidencia_adicional(self):
        # T-502: la pasada 2 **detecta** el gap y lo reporta, no lo cubre (no
        # hay red ni reintentos acá).
        evidencia = concluir(_evidencia({"razon_social_emisor": "ACME"}))

        assert evidencia.trazabilidad["conclusion"]["faltan_datos"]

    def test_no_llama_al_agente(self):
        # T-504: el agente ya está implementado, pero la pasada 2 (T-501) **no**
        # lo invoca: concluir y escalar son etapas distintas. El escalado vive en
        # `concluir_con_agente()` / `escalar_a_agente()`.
        from voucherflow.conclusion import escalar_a_agente

        resultado = escalar_a_agente(_evidencia({**BASE, "tipo_comprobante": "A"}))
        assert resultado.escalado is False
        assert resultado.desenlace == AGENTE_NO_ESCALADO

    def test_no_encola_hitl(self):
        # T-505: sigue siendo esqueleto.
        with pytest.raises(NotImplementedError):
            encolar_hitl(None)  # type: ignore[arg-type]
    def test_es_determinista(self):
        campos = {
            **BASE,
            "tipo_comprobante": "B",
            "cuit_emisor": "30-12345678-9",
            "subtotal": "100.00",
            "iva": "21.00",
        }
        primera = concluir_caso(_evidencia(campos))
        segunda = concluir_caso(_evidencia(campos))

        assert primera.como_dict() == segunda.como_dict()

    def test_no_depende_del_orden_de_las_fuentes(self):
        campos = {
            **BASE,
            "tipo_comprobante": "A",
            "cuit_emisor": "30-12345678-9",
            "cuit_receptor": "27-30111222-4",
            "subtotal": "100.00",
            "iva": "21.00",
        }
        directo = concluir_caso(
            combinar_evidencia("doc-1", [_source(Fuente.vlm, campos), _source(Fuente.llm, campos)])
        )
        invertido = concluir_caso(
            combinar_evidencia("doc-1", [_source(Fuente.llm, campos), _source(Fuente.vlm, campos)])
        )

        assert directo.como_dict() == invertido.como_dict()

    def test_una_sola_fuente_alcanza_para_concluir(self):
        # La conclusión corre sobre la evidencia **combinada**: con una sola
        # fuente que declaró todo, el caso se resuelve igual.
        campos = {
            **BASE,
            "tipo_comprobante": "A",
            "cuit_emisor": "30-12345678-9",
            "cuit_receptor": "27-30111222-4",
            "subtotal": "100.00",
            "iva": "21.00",
        }
        resultado = concluir_caso(_evidencia(campos, con_llm=False))

        assert resultado.estado == ESTADO_APROBADO

    def test_el_documento_id_se_propaga(self):
        evidencia = concluir(_evidencia({**BASE, "tipo_comprobante": "A"}))
        assert evidencia.documento_id == "doc-1"


class TestContextoFiscalInyectable:
    """El contexto fiscal entra por la API (no viaja en la evidencia, T-501).

    Las condiciones fiscales las aporta la clasificación de F3 o el padrón; **no**
    son campos del contrato de extracción, así que no pueden viajar dentro de la
    ``CombinedEvidence``. La conclusión los recibe como parámetro **opcional**.
    """

    def test_el_contexto_fiscal_habilita_el_conflicto_r7(self):
        campos = {
            **BASE,
            "tipo_comprobante": "B",
            "cuit_emisor": "30-12345678-9",
            "iva": "0.00",
        }
        sin_contexto = concluir_caso(_evidencia(campos))
        con_contexto = concluir_caso(_evidencia(campos), CTX_RI_RI)

        assert sin_contexto.estado == ESTADO_APROBADO
        assert con_contexto.estado == ESTADO_REVISION

    def test_el_contexto_fiscal_habilita_el_cruce_negocio_documento(self):
        ctx_mono = ContextoTipoComprobante(
            emisor_condicion_fiscal="Monotributo",
            receptor_condicion_fiscal="Consumidor Final",
        )
        campos = {
            **BASE,
            "tipo_comprobante": "A",
            "cuit_emisor": "20-12345678-9",
            "cuit_receptor": "27-30111222-4",
            "subtotal": "100.00",
            "iva": "21.00",
        }
        resultado = concluir_caso(_evidencia(campos), ctx_mono)

        assert resultado.estado == ESTADO_REVISION
        assert CRUZ_5_CONFLICTO_CREDITO in resultado.reglas_aplicadas

    def test_sin_contexto_fiscal_igual_concluye(self):
        # El contexto fiscal **refina**; no es un requisito para concluir.
        campos = {
            **BASE,
            "tipo_comprobante": "A",
            "cuit_emisor": "30-12345678-9",
            "cuit_receptor": "27-30111222-4",
            "subtotal": "100.00",
            "iva": "21.00",
        }
        resultado = concluir_caso(_evidencia(campos))

        assert resultado.estado == ESTADO_APROBADO

    def test_un_contexto_coherente_no_cambia_el_veredicto(self):
        campos = {
            **BASE,
            "tipo_comprobante": "A",
            "cuit_emisor": "30-12345678-9",
            "cuit_receptor": "27-30111222-4",
            "subtotal": "100.00",
            "iva": "21.00",
        }
        # RI + RI es el negocio que espera una A: coincide con el documento.
        resultado = concluir_caso(_evidencia(campos), CTX_RI_RI)

        assert resultado.estado == ESTADO_APROBADO
        assert CRUZ_5_CONFLICTO_CREDITO not in resultado.reglas_aplicadas

    def test_la_traza_registra_la_fuente_de_la_letra(self):
        # La procedencia de la letra (qué flujo la sostuvo) queda en la traza:
        # es lo que permite auditar la decisión (E-CONC-5).
        evidencia = concluir(
            _evidencia({**BASE, "tipo_comprobante": "A", "cuit_emisor": "30-12345678-9"})
        )
        traza = evidencia.trazabilidad["conclusion"]

        assert traza["fuente_letra"] in {"vlm", "llm"}


class TestContextoTipadoDelMotor:
    """El motor de F3 no se reescribe: se reutiliza sobre los valores vigentes."""

    def test_la_reutilizacion_del_motor_no_modifica_el_de_f3(self):
        # ``evaluar_negocio`` es el motor de F3 tal cual: la conclusión lo llama,
        # no lo reimplementa ni lo parchea.
        from voucherflow.rules.contexto import ContextoTipoComprobante as Ctx
        from voucherflow.rules.tipo_comprobante_rules import evaluar_negocio

        assert evaluar_negocio(Ctx(emisor_condicion_fiscal="Responsable Inscripto", receptor_condicion_fiscal="Responsable Inscripto")) == "A"
        assert evaluar_negocio(Ctx()) is None

    def test_las_reglas_cruzadas_son_rule_del_motor_de_f0(self):
        from voucherflow.rules import Rule

        for regla in REGISTRO_CRUZADAS.reglas:
            assert isinstance(regla, Rule)


class TestResumenCruzadas:
    """El resumen de la pasada 2 es serializable y completo."""

    def test_incluye_las_reglas_por_familia_y_el_estado(self):
        from voucherflow.rules.cruzadas import resumen_cruzadas

        _, veredicto = _cruzadas(
            {**BASE, "tipo_comprobante": "B", "cuit_emisor": "30-12345678-9", "iva": "0.00"},
            contexto_tipo=CTX_RI_RI,
        )
        resumen = resumen_cruzadas(veredicto)

        assert resumen["estado"] == ESTADO_REVISION
        assert resumen["reglas_por_familia"][FAMILIA_CONFLICTO] == [CRUZ_5_CONFLICTO_CREDITO]
        assert resumen["concluye"] is False

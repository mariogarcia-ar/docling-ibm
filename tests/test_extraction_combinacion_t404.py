"""Tests de la combinación de evidencia con resolución por campo (F4/T-404).

**DoD de T-404** (F4.md §3, E-EXT-1 / ADR-002): "La evidencia de ambos flujos se
combina resolviendo campo a campo según la precedencia definida en ADR-002".

Qué se verifica, en el orden del entregable:

1. **La tabla de precedencia** (``rules/precedencia.py``): cada campo del contrato
   tiene precedencia declarada, con su regla (``PREC_n``) y su motivo; los campos
   del modo genérico caen en la regla de oro (``PREC_0``); las fuentes que no son
   lectura (``programa``/``arca``/``hitl``) no pierden contra una lectura.
2. **La resolución de un campo** (``resolver_campo``): acuerdo (mismo valor
   canónico), desacuerdo (gana la precedencia), campo que solo declaró una fuente,
   campo que ninguna declaró (no se inventa), y el caso en que la ganadora por
   precedencia está **invalidada** por la pasada 1 (T-403): no puede ganar un
   campo que otra fuente sí resolvió, y si **ninguna** es utilizable la resolución
   lo declara no confiable.
3. **La combinación completa** (``combinar``/``combinar_evidencia``): conserva
   **todas** las lecturas (combinar no es descartar), resuelve todos los campos
   declarados, y arma el contrato de F0 (``CombinedEvidence`` con
   ``CampoCombinado``/``FieldResolution`` + el atajo ``valor``/``fuente``).
4. **Determinismo**: el resultado no depende del orden de las fuentes ni del orden
   de las claves (los flujos corren en paralelo, T-401).
5. **Fronteras**: la combinación **no** decide el caso (``decision`` en ``None``:
   concluir es F5/T-501), **no** re-calcula la pasada 1 ni la normalización, y
   **no** descarta lecturas.

Reglas duras: suite default **sin** Ollama real ni Docling real.
"""

from __future__ import annotations

import json
import tempfile
from typing import Any

import pytest

from voucherflow.extraction import (
    CAMPO_FUENTE_LECTURA,
    CAMPOS_EXTRACCION,
    CLAVE_CAMPOS,
    VERSION_COMBINACION,
    campo_declarado_de_campo,
    combinar_evidencia,
    construir_source_evidence,
    parsear_evidencia_extraccion,
    veredicto_raw_de_evidencia,
)
from voucherflow.extraction.key_value import normalizar_evidencia
from voucherflow.rules.precedencia import (
    FUENTES_LECTURA,
    FUENTES_NO_LECTURA,
    ORDEN_CANONICO_FUENTES,
    PREC_DATO_COMPUTADO,
    PREC_LECTURA_TEXTO,
    PREC_LECTURA_VISUAL,
    PREC_REGLA_DE_ORO,
    TABLA_PRECEDENCIA,
    combinar,
    resolver_campo,
    resumen_combinacion,
    valor_de,
)
from voucherflow.schemas.evidence import (
    CampoCombinado,
    CombinedEvidence,
    EvidenceField,
    Fuente,
    SourceEvidence,
)

# ---------------------------------------------------------------------------
# Utilidades
# ---------------------------------------------------------------------------


def _campo(valor: Any, fuente: Fuente = Fuente.vlm, sustento: str = "frag") -> EvidenceField:
    """``EvidenceField`` mínimo (el contrato de F0 exige fragmento no vacío)."""
    return EvidenceField(
        campo="campo", valor=valor, fuente=fuente, fragmento_sustento=sustento
    )


def _source(fuente: Fuente, *, valida: bool = True, **campos: Any) -> SourceEvidence:
    """``SourceEvidence`` con los campos indicados (valores ya canónicos)."""
    return SourceEvidence(
        fuente=fuente,
        campos={
            nombre: _campo(valor, fuente) for nombre, valor in campos.items()
        },
        valida=valida,
    )


def _evidencia(campos: dict[str, Any], fuente: str = "llm") -> SourceEvidence:
    """``SourceEvidence`` desde el JSON del contrato, normalizado (T-402) y

    calificado por la pasada 1 (T-403): el pipeline real de la extracción.
    """
    crudo = json.dumps(
        {CAMPO_FUENTE_LECTURA: fuente, CLAVE_CAMPOS: campos}, ensure_ascii=False
    )
    lectura = parsear_evidencia_extraccion(crudo, fuente=fuente)
    normalizada = normalizar_evidencia(lectura).evidencia
    veredicto = veredicto_raw_de_evidencia(normalizada)
    return construir_source_evidence(normalizada, veredicto=veredicto)


# ---------------------------------------------------------------------------
# 1. La tabla de precedencia
# ---------------------------------------------------------------------------


class TestTablaPrecedencia:
    """La tabla declara la precedencia de cada campo, con regla y motivo."""

    def test_todos_los_campos_del_contrato_tienen_precedencia(self):
        # Un campo del contrato sin entrada usaría la regla de oro en silencio;
        # se exige que esté declarado explícitamente.
        faltan = [c for c in CAMPOS_EXTRACCION if c not in TABLA_PRECEDENCIA]
        assert faltan == [], f"campos del contrato sin precedencia declarada: {faltan}"

    def test_cada_entrada_declara_regla_y_motivo(self):
        for campo, precedencia in TABLA_PRECEDENCIA.items():
            assert precedencia.campo == campo
            assert precedencia.regla.startswith("PREC_")
            assert precedencia.motivo.strip(), campo
            assert precedencia.orden, campo

    def test_la_letra_y_el_numero_ganan_por_lectura_visual(self):
        # ADR-002 lo dice explícitamente: "visual gana en la letra del encabezado
        # si el recuadro se detectó con claridad".
        for campo in ("tipo_comprobante", "nro_comprobante"):
            precedencia = TABLA_PRECEDENCIA[campo]
            assert precedencia.regla == PREC_LECTURA_VISUAL
            assert precedencia.orden_lecturas()[0] is Fuente.vlm

    def test_la_fecha_los_importes_y_la_moneda_ganan_por_lectura_textual(self):
        for campo in ("fecha_emision", "moneda", "importe_total_facturado", "subtotal", "iva"):
            precedencia = TABLA_PRECEDENCIA[campo]
            assert precedencia.regla == PREC_LECTURA_TEXTO, campo
            assert precedencia.orden_lecturas()[0] is Fuente.llm

    def test_los_campos_derivados_son_del_programa(self):
        for campo in ("punto_venta", "numero_comprobante"):
            precedencia = TABLA_PRECEDENCIA[campo]
            assert precedencia.regla == PREC_DATO_COMPUTADO
            assert precedencia.orden_completo()[0] is Fuente.hitl  # no-lectura
            # ``programa`` es la fuente que los aporta y está por delante de
            # cualquier lectura.
            orden = precedencia.orden_completo()
            assert orden.index(Fuente.programa) < orden.index(Fuente.vlm)

    def test_el_orden_declarado_se_completa_con_todas_las_fuentes(self):
        # Un campo que declara solo "gana el visual" igual tiene orden total.
        for precedencia in TABLA_PRECEDENCIA.values():
            assert set(precedencia.orden_completo()) == set(ORDEN_CANONICO_FUENTES)

    def test_un_campo_desconocido_usa_la_regla_de_oro(self):
        # Los campos del modo genérico (kvg) no están en la tabla: se aplica la
        # regla de oro de ADR-002 en vez de inventar una precedencia específica.
        resolucion = resolver_campo(
            "alicuota_21",
            {Fuente.llm: _source(Fuente.llm, alicuota_21="21")},
        )
        assert resolucion.regla == PREC_REGLA_DE_ORO
        assert "regla de oro" in resolucion.motivo

    def test_las_fuentes_no_lectura_estan_por_encima_de_las_lecturas(self):
        # La regla de oro: lo comprobado o corregido no se discute con una lectura.
        orden = TABLA_PRECEDENCIA["tipo_comprobante"].orden_completo()
        for fuente in FUENTES_NO_LECTURA:
            assert orden.index(fuente) < orden.index(Fuente.vlm)
        assert Fuente.hitl in orden  # la corrección humana también participa

    def test_las_fuentes_de_lectura_son_las_dos_de_eext1(self):
        assert set(FUENTES_LECTURA) == {Fuente.vlm, Fuente.llm}
        assert set(ORDEN_CANONICO_FUENTES) == {
            Fuente.vlm,
            Fuente.llm,
            Fuente.programa,
            Fuente.arca,
            Fuente.hitl,
        }


# ---------------------------------------------------------------------------
# 2. Resolución de un campo
# ---------------------------------------------------------------------------


class TestResolverCampo:
    """La resolución es determinista y auditable (regla + motivo + descartadas)."""

    def test_campo_que_ninguna_fuente_declaro(self):
        resolucion = resolver_campo("cuit_receptor", {Fuente.vlm: _source(Fuente.vlm)})
        assert resolucion.ganador is None
        assert resolucion.regla is None
        assert "no se inventa" in resolucion.motivo

    def test_campo_de_una_sola_fuente(self):
        resolucion = resolver_campo(
            "tipo_comprobante", {Fuente.llm: _source(Fuente.llm, tipo_comprobante="A")}
        )
        assert resolucion.ganador is Fuente.llm
        assert resolucion.acuerdo is True  # no hubo desacuerdo que resolver
        assert resolucion.descartadas == ()

    def test_las_fuentes_coinciden(self):
        resolucion = resolver_campo(
            "tipo_comprobante",
            {
                Fuente.vlm: _source(Fuente.vlm, tipo_comprobante="A"),
                Fuente.llm: _source(Fuente.llm, tipo_comprobante="A"),
            },
        )
        assert resolucion.acuerdo is True
        assert resolucion.ganador is Fuente.vlm  # la de mayor precedencia
        assert "coinciden" in resolucion.motivo

    def test_discrepan_y_gana_la_precedencia_del_campo(self):
        # La letra prioriza visual: gana el VLM.
        letra = resolver_campo(
            "tipo_comprobante",
            {
                Fuente.vlm: _source(Fuente.vlm, tipo_comprobante="A"),
                Fuente.llm: _source(Fuente.llm, tipo_comprobante="B"),
            },
        )
        assert letra.ganador is Fuente.vlm
        assert letra.regla == PREC_LECTURA_VISUAL
        assert letra.acuerdo is False
        assert letra.descartadas == ((Fuente.llm, "B"),)

        # El importe prioriza texto: gana el LLM.
        importe = resolver_campo(
            "importe_total_facturado",
            {
                Fuente.vlm: _source(Fuente.vlm, importe_total_facturado=111.0),
                Fuente.llm: _source(Fuente.llm, importe_total_facturado=222.0),
            },
        )
        assert importe.ganador is Fuente.llm
        assert importe.regla == PREC_LECTURA_TEXTO
        assert importe.descartadas == ((Fuente.vlm, 111.0),)

    def test_la_resolucion_registra_que_gana_y_por_que(self):
        resolucion = resolver_campo(
            "tipo_comprobante",
            {
                Fuente.vlm: _source(Fuente.vlm, tipo_comprobante="A"),
                Fuente.llm: _source(Fuente.llm, tipo_comprobante="B"),
            },
        )
        assert "vlm='A'" in resolucion.motivo and "llm='B'" in resolucion.motivo
        assert resolucion.motivo.strip()
        contrato = resolucion.como_field_resolution()
        assert contrato.ganador is Fuente.vlm
        assert contrato.regla == PREC_LECTURA_VISUAL
        assert contrato.motivo

    def test_una_fuente_invalida_no_gana_un_campo_que_la_otra_resolvio(self):
        # La pasada 1 (T-403) puede invalidar una fuente; esa fuente no puede
        # imponer su lectura aunque tenga la precedencia del campo.
        resolucion = resolver_campo(
            "tipo_comprobante",
            {
                Fuente.vlm: _source(Fuente.vlm, valida=False, tipo_comprobante="X"),
                Fuente.llm: _source(Fuente.llm, tipo_comprobante="A"),
            },
        )
        assert resolucion.ganador is Fuente.llm
        assert resolucion.invalidas == (Fuente.vlm,)
        assert "inválidas: vlm" in resolucion.motivo

    def test_si_ninguna_es_utilizable_la_resolucion_lo_declara(self):
        resolucion = resolver_campo(
            "tipo_comprobante",
            {
                Fuente.vlm: _source(Fuente.vlm, valida=False, tipo_comprobante="X"),
                Fuente.llm: _source(Fuente.llm, valida=False, tipo_comprobante="Y"),
            },
        )
        assert resolucion.ganador is Fuente.vlm  # no se inventa: gana la precedencia
        assert resolucion.confiable is False
        assert "indicio, no como dato" in resolucion.motivo

    def test_el_ganador_es_independiente_del_orden_del_mapping(self):
        fuentes = {
            Fuente.vlm: _source(Fuente.vlm, tipo_comprobante="A"),
            Fuente.llm: _source(Fuente.llm, tipo_comprobante="B"),
        }
        invertido = dict(reversed(list(fuentes.items())))
        assert resolver_campo("tipo_comprobante", fuentes) == resolver_campo(
            "tipo_comprobante", invertido
        )

    def test_las_no_lectura_ganan_sobre_las_lecturas(self):
        # ``programa`` (T-402 deriva punto_venta/numero) manda sobre lo leído.
        resolucion = resolver_campo(
            "punto_venta",
            {
                Fuente.programa: _source(Fuente.programa, punto_venta="00005"),
                Fuente.vlm: _source(Fuente.vlm, punto_venta="00006"),
            },
        )
        assert resolucion.ganador is Fuente.programa

    def test_valor_de_es_el_comparador(self):
        source = _source(Fuente.vlm, tipo_comprobante="A")
        assert valor_de(source, "tipo_comprobante") == "A"
        assert valor_de(source, "cuit_receptor") is None


# ---------------------------------------------------------------------------
# 3. Combinación completa
# ---------------------------------------------------------------------------


class TestCombinar:
    """``combinar`` conserva todas las lecturas y resuelve campo a campo."""

    def test_conserva_las_lecturas_de_todas_las_fuentes(self):
        # Combinar **no** es descartar: el CampoCombinado lleva las dos lecturas.
        combinacion = combinar(
            {
                Fuente.vlm: _source(Fuente.vlm, tipo_comprobante="A", cuit_emisor="30-1"),
                Fuente.llm: _source(Fuente.llm, tipo_comprobante="B"),
            }
        )
        campo = combinacion.campos["tipo_comprobante"]
        assert campo.vlm is not None and campo.vlm.valor == "A"
        assert campo.llm is not None and campo.llm.valor == "B"
        # Y un campo que solo declaró una fuente queda con esa sola lectura.
        assert combinacion.campos["cuit_emisor"].llm is None

    def test_resuelve_todos_los_campos_declarados(self):
        combinacion = combinar(
            {
                Fuente.vlm: _source(Fuente.vlm, tipo_comprobante="A"),
                Fuente.llm: _source(Fuente.llm, nro_comprobante="00005-00007344"),
            }
        )
        assert set(combinacion.campos) == {"tipo_comprobante", "nro_comprobante"}
        assert combinacion.campos_resueltos == {
            "tipo_comprobante": "A",
            "nro_comprobante": "00005-00007344",
        }
        assert combinacion.fuentes_por_campo["nro_comprobante"] is Fuente.llm

    def test_el_universo_respeta_el_orden_del_contrato(self):
        # El orden de los campos es el del prompt (determinista), no el del JSON.
        combinacion = combinar(
            {
                Fuente.llm: _source(
                    Fuente.llm,
                    importe_total_facturado=1.0,
                    tipo_comprobante="A",
                    cuit_emisor="30-1",
                )
            }
        )
        orden = list(combinacion.campos)
        assert orden == ["tipo_comprobante", "cuit_emisor", "importe_total_facturado"]

    def test_los_campos_extra_van_al_final_en_orden_alfabetico(self):
        combinacion = combinar(
            {
                Fuente.llm: _source(
                    Fuente.llm,
                    productos="[]",
                    alicuota_21="21",
                    tipo_comprobante="A",
                )
            }
        )
        assert list(combinacion.campos)[0] == "tipo_comprobante"
        assert list(combinacion.campos)[1:] == ["alicuota_21", "productos"]

    def test_el_desacuerdo_y_el_acuerdo_quedan_separados(self):
        combinacion = combinar(
            {
                Fuente.vlm: _source(
                    Fuente.vlm, tipo_comprobante="A", cuit_emisor="30-12345678-9"
                ),
                Fuente.llm: _source(
                    Fuente.llm, tipo_comprobante="B", cuit_emisor="30-12345678-9"
                ),
            }
        )
        assert set(combinacion.desacuerdos) == {"tipo_comprobante"}
        assert combinacion.no_confiables == {}
        resumen = resumen_combinacion(combinacion)
        assert resumen["acuerdos"] == 1
        assert resumen["desacuerdos"] == ["tipo_comprobante"]
        assert resumen["reglas_aplicadas"] == {PREC_LECTURA_VISUAL: 2}

    def test_acepta_un_iterable_o_un_mapping(self):
        vlm = _source(Fuente.vlm, tipo_comprobante="A")
        llm = _source(Fuente.llm, tipo_comprobante="A")
        por_mapping = combinar({Fuente.vlm: vlm, Fuente.llm: llm})
        por_iterable = combinar([vlm, llm])
        assert por_mapping.campos_resueltos == por_iterable.campos_resueltos

    def test_una_fuente_que_no_es_source_evidence_lanza_type_error(self):
        with pytest.raises(TypeError, match="SourceEvidence"):
            combinar({"vlm": "no soy evidencia"})  # type: ignore[dict-item]
        with pytest.raises(TypeError, match="SourceEvidence"):
            combinar(["no soy evidencia"])  # type: ignore[list-item]

    def test_sin_fuentes_no_hay_campos(self):
        combinacion = combinar({})
        assert combinacion.campos == {}
        assert combinacion.fuentes == ()

    def test_el_resultado_es_serializable(self):
        combinacion = combinar(
            {Fuente.llm: _source(Fuente.llm, tipo_comprobante="A")}
        )
        for campo in combinacion.campos.values():
            assert isinstance(campo, CampoCombinado)
            assert json.dumps(campo.model_dump(), default=str)


# ---------------------------------------------------------------------------
# 4. El contrato de F0 (combinar_evidencia)
# ---------------------------------------------------------------------------


class TestCombinarEvidenciaContrato:
    """``combinar_evidencia`` implementa el contrato congelado de F0."""

    def test_devuelve_combined_evidence(self):
        combinada = combinar_evidencia(
            "doc-1", [_source(Fuente.vlm, tipo_comprobante="A")]
        )
        assert isinstance(combinada, CombinedEvidence)
        assert combinada.documento_id == "doc-1"
        assert isinstance(combinada.campos["tipo_comprobante"], CampoCombinado)

    def test_el_campo_lleva_valor_y_fuente_ganadora(self):
        # El atajo operativo: sin él cada consumidor re-derivaría el ganador.
        combinada = combinar_evidencia(
            "doc-1",
            [
                _source(Fuente.vlm, tipo_comprobante="A"),
                _source(Fuente.llm, tipo_comprobante="B"),
            ],
        )
        campo = combinada.campos["tipo_comprobante"]
        assert campo.valor == "A"
        assert campo.fuente is Fuente.vlm
        assert campo.resolucion is not None
        assert campo.resolucion.ganador is Fuente.vlm

    def test_la_decision_la_produce_f5_no_la_combinacion(self):
        # Combinar (T-404) y concluir (F5/T-501) son etapas distintas: el contrato
        # exige que la certeza se derive de la etapa que decidió, y acá no hubo
        # decisión. Inventarla sería el error que el glosario prohíbe.
        combinada = combinar_evidencia(
            "doc-1", [_source(Fuente.vlm, tipo_comprobante="A")]
        )
        assert combinada.decision is None

    def test_la_trazabilidad_registra_la_combinacion(self):
        combinada = combinar_evidencia(
            "doc-1",
            [
                _source(Fuente.vlm, tipo_comprobante="A"),
                _source(Fuente.llm, tipo_comprobante="B"),
            ],
        )
        traza = combinada.trazabilidad
        assert traza["version_combinacion"] == VERSION_COMBINACION
        info = traza["combinacion"]
        assert info["fuentes"] == ["vlm", "llm"]
        assert info["desacuerdos"] == ["tipo_comprobante"]
        assert info["resoluciones"]["tipo_comprobante"]["regla"] == PREC_LECTURA_VISUAL

    def test_documento_id_vacio_lanza_contrato(self):
        with pytest.raises(Exception, match="documento_id"):
            combinar_evidencia("", [_source(Fuente.vlm, tipo_comprobante="A")])

    def test_acepta_una_sola_fuente(self):
        # La firma de F0 recibe una lista: con una sola fuente también resuelve.
        combinada = combinar_evidencia(
            "doc-1", [_source(Fuente.llm, fecha_emision="2025-08-14")]
        )
        assert combinada.campos["fecha_emision"].valor == "2025-08-14"
        assert combinada.campos["fecha_emision"].fuente is Fuente.llm


# ---------------------------------------------------------------------------
# 5. Integración con el pipeline real de F4 (T-401 → T-402 → T-403 → T-404)
# ---------------------------------------------------------------------------


class TestIntegracionPipeline:
    """Desde la lectura cruda del modelo hasta la evidencia combinada."""

    def test_dos_fuentes_que_discrepan_en_la_letra(self):
        vlm = _evidencia(
            {
                "tipo_comprobante": {
                    "valor": "A",
                    "fragmento_sustento": "Recuadro 'A' COD. 01",
                }
            },
            fuente="vlm",
        )
        llm = _evidencia(
            {
                "tipo_comprobante": {
                    "valor": "B",
                    "fragmento_sustento": "FACTURA B",
                }
            },
            fuente="llm",
        )
        combinada = combinar_evidencia("doc-1", [vlm, llm])
        campo = combinada.campos["tipo_comprobante"]
        # Gana el visual (ADR-002) y la otra lectura se conserva y se registra.
        assert campo.valor == "A"
        assert campo.fuente is Fuente.vlm
        assert campo.llm is not None and campo.llm.valor == "B"
        assert combinada.trazabilidad["combinacion"]["desacuerdos"] == [
            "tipo_comprobante"
        ]

    def test_la_normalizacion_hace_comparables_los_valores(self):
        # T-402 deja los valores canónicos: "14/08/2025" y "2025-08-14" son el
        # MISMO valor, así que no hay un "desacuerdo" espurio por formato.
        vlm = _evidencia(
            {"fecha_emision": {"valor": "2025-08-14", "fragmento_sustento": "Fecha"}},
            fuente="vlm",
        )
        llm = _evidencia(
            {"fecha_emision": {"valor": "14/08/2025", "fragmento_sustento": "Fecha"}},
            fuente="llm",
        )
        assert vlm.campos["fecha_emision"].valor == "2025-08-14"
        assert llm.campos["fecha_emision"].valor == "2025-08-14"
        resolucion = resolver_campo(
            "fecha_emision", {Fuente.vlm: vlm, Fuente.llm: llm}
        )
        assert resolucion.acuerdo is True

    def test_la_pasada_1_decide_quien_puede_ganar(self):
        # Una fuente invalidada por T-403 (letra fuera de vocabulario) no gana,
        # aunque el campo priorice su lectura.
        invalida = _evidencia(
            {
                "tipo_comprobante": {
                    "valor": "X",
                    "fragmento_sustento": "Recuadro 'X'",
                }
            },
            fuente="vlm",
        )
        assert invalida.valida is False
        valida = _evidencia(
            {
                "tipo_comprobante": {
                    "valor": "A",
                    "fragmento_sustento": "Recuadro 'A' COD. 01",
                }
            },
            fuente="llm",
        )
        combinada = combinar_evidencia("doc-1", [invalida, valida])
        campo = combinada.campos["tipo_comprobante"]
        assert campo.fuente is Fuente.llm
        assert campo.valor == "A"
        assert "vlm" in campo.resolucion.motivo  # queda registrado el descarte

    def test_los_campos_derivados_ganan_por_programa(self):
        # ``punto_venta``/``numero_comprobante`` los deriva T-402: si el programa
        # los aporta, mandan sobre cualquier lectura del modelo.
        vlm = _evidencia(
            {
                "nro_comprobante": {
                    "valor": "00005-00007344",
                    "fragmento_sustento": "Nro: 00005-00007344",
                }
            },
            fuente="vlm",
        )
        llm = _evidencia(
            {
                "nro_comprobante": {
                    "valor": "00005-00007344",
                    "fragmento_sustento": "Nro: 00005-00007344",
                }
            },
            fuente="llm",
        )
        combinada = combinar_evidencia("doc-1", [vlm, llm])
        # Las dos fuentes derivan el punto de venta (el valor es el mismo).
        assert combinada.campos["punto_venta"].valor == "00005"
        assert combinada.campos["punto_venta"].resolucion.regla == PREC_DATO_COMPUTADO

    def test_un_campo_solo_declarado_por_una_fuente_igual_se_resuelve(self):
        vlm = _evidencia(
            {"cuit_receptor": {"valor": "27-30111222-4", "fragmento_sustento": "CUIT"}},
            fuente="vlm",
        )
        llm = _evidencia(
            {"cuit_emisor": {"valor": "30-12345678-9", "fragmento_sustento": "CUIT"}},
            fuente="llm",
        )
        combinada = combinar_evidencia("doc-1", [vlm, llm])
        assert combinada.campos["cuit_receptor"].fuente is Fuente.vlm
        assert combinada.campos["cuit_emisor"].fuente is Fuente.llm
        # Sola una lectura cada uno: no hay desacuerdo que reportar.
        assert combinada.trazabilidad["combinacion"]["desacuerdos"] == []

    def test_la_evidencia_combinada_es_serializable(self):
        vlm = _evidencia(
            {"tipo_comprobante": {"valor": "A", "fragmento_sustento": "'A'"}},
            fuente="vlm",
        )
        combinada = combinar_evidencia("doc-1", [vlm])
        crudo = combinada.model_dump_json()
        assert CombinedEvidence.model_validate_json(crudo) == combinada


# ---------------------------------------------------------------------------
# 6. Fronteras de la tarea
# ---------------------------------------------------------------------------


class TestFronteras:
    """Lo que T-404 NO hace: decidir el caso, re-calcular o descartar."""

    def test_no_decide_el_caso(self):
        combinada = combinar_evidencia(
            "doc-1", [_source(Fuente.vlm, tipo_comprobante="A")]
        )
        assert combinada.decision is None
        assert "F5" in combinada.trazabilidad["nota"]

    def test_no_descarta_lecturas(self):
        # Aunque un valor pierda la resolución, la lectura se conserva entera.
        combinada = combinar_evidencia(
            "doc-1",
            [
                _source(Fuente.vlm, tipo_comprobante="A"),
                _source(Fuente.llm, tipo_comprobante="B"),
            ],
        )
        campo = combinada.campos["tipo_comprobante"]
        assert campo.llm is not None
        assert campo.llm.valor == "B"
        assert campo.llm.fragmento_sustento  # con su sostén, para auditar

    def test_no_recalcula_la_normalizacion_ni_la_pasada_raw(self):
        # T-404 recibe la evidencia ya calificada y canónica: los valores que
        # publica son los que le llegaron, sin re-normalizar.
        source = _source(Fuente.llm, cuit_emisor="30-12345678-9")
        combinada = combinar_evidencia("doc-1", [source])
        assert combinada.campos["cuit_emisor"].valor == "30-12345678-9"

    def test_la_letra_del_comprobante_no_se_decide_aca(self):
        # El motor R1-R7 (F3/T-301) resuelve la letra con su propia precedencia
        # (D-14); acá solo se combinan lecturas por campo.
        from voucherflow.classification.tipo_comprobante import (
            clasificar_tipo_comprobante,
        )

        # La función de clasificación existe aparte y no es la que combina.
        assert callable(clasificar_tipo_comprobante)

    def test_el_registro_raw_y_el_motor_de_f0_siguen_intactos(self):
        from voucherflow.rules.raw import REGISTRO_RAW
        from voucherflow.rules.registry import Registry

        assert [r.id for r in REGISTRO_RAW.reglas] == [
            "RAW_CAMPO",
            "RAW_VOCABULARIO",
            "RAW_SUSTENTO",
            "RAW_CONTRADICCION",
        ]
        assert Registry().ids_disparados({}) == []

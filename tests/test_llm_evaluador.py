"""Evaluación determinística de la extracción (las 15 reglas del prompt).

Fijan el **comportamiento del evaluador**, que es la referencia contra la que se
mide un prompt o un modelo. La regla que lo gobierna: lo que se puede calcular
se calcula en código, no se le pregunta al LLM.

Cada test corresponde a una regla de negocio del prompt. Si alguno falla, o el
port cambió una decisión de negocio, o la regla cambió a propósito — en los dos
casos hay que mirarlo: un evaluador que se mueve solo hace incomparables las
mediciones entre modelos.

⚠️ Los nombres de campo de la **extracción** (lo que el modelo lee del
documento) y los de los **datos cargados** (la carga de Mendel) no siempre
coinciden — `importe_total` vs `importe_total_facturado`, `nro_factura` vs
`nro_factura`, los impuestos anidados bajo `impuestos`—. Confundirlos es el
error más fácil de cometer acá, así que los tests usan el contrato real.
"""

from __future__ import annotations

import pytest

from voucherflow.llm.evaluador import (
    CATEGORIAS_CON_COMENSALES,
    CATEGORIAS_CON_LITROS,
    COMPONENTES_DEL_TOTAL,
    TOLERANCIA_MONTO,
    _norm,
    _norm_razon_social,
    a_numero,
    diff_deterministico,
    verificar_aritmetica,
)
from voucherflow.extraction.key_value import normalizar_monto


def _diff(extraccion, datos):
    return diff_deterministico(extraccion, datos)


# ---------------------------------------------------------------------------
# Utilidades de normalización y números
# ---------------------------------------------------------------------------


class TestNormalizacion:
    def test_norm_quita_tildes_y_pasa_a_mayusculas(self):
        assert _norm("Combustible") == "COMBUSTIBLE"
        assert _norm("  café  ") == "CAFE"
        assert _norm(None) == ""

    def test_norm_razon_social_tolera_puntuacion(self):
        # La regla 2 tolera «S.A. vs SA»: un `coincide=False` ahí sería un
        # falso positivo de negocio.
        assert _norm_razon_social("Estación de Servicio S.A.") == _norm_razon_social(
            "ESTACION DE SERVICIO SA"
        )

    def test_los_conjuntos_de_categorias_estan_en_la_forma_de_norm(self):
        # ⚠️ `_norm` devuelve MAYÚSCULAS: un conjunto escrito en minúsculas
        # nunca coincidiría y la regla se apagaría en silencio.
        for categoria in CATEGORIAS_CON_COMENSALES | CATEGORIAS_CON_LITROS:
            assert categoria == categoria.upper()
            assert categoria.strip() == categoria


class TestNumero:
    @pytest.mark.parametrize(
        "crudo,esperado",
        [
            ("12345.67", 12345.67),
            (12345.67, 12345.67),
            ("12.345,67", 12345.67),  # convención argentina con decimales
            ("-500,50", -500.50),
            # Convención argentina: el punto separa miles.
            ("1.000", 1000.0),
            ("1.234.567", 1234567.0),
            ("$ 1.000", 1000.0),
            ("1.234,56-", -1234.56),  # ajuste negativo impreso
            ("(1.234,56)", -1234.56),  # ajuste negativo entre paréntesis
            # Y la convención inversa sigue funcionando (comprobantes en USD).
            ("1,234,567.89", 1234567.89),
        ],
    )
    def test_parsea_los_formatos_reales(self, crudo, esperado):
        assert a_numero(crudo) == pytest.approx(esperado)

    def test_un_valor_ilegible_no_es_cero(self):
        # Devolver 0 haría que un dato ausente sume como si valiera cero.
        assert a_numero("ilegible") is None
        assert a_numero(None) is None
        assert a_numero("") is None

    def test_el_cero_es_un_valor(self):
        assert a_numero("0") == 0.0
        assert a_numero(0) == 0.0

    def test_un_booleano_no_es_un_monto(self):
        assert a_numero(True) is None

    @pytest.mark.parametrize(
        "crudo",
        [
            "1.000",
            "1.234.567",
            "12.345,67",
            "1,234,567.89",
            "$ 1.000",
            "-500,50",
            "(1.234,56)",
            "ilegible",
            0,
            None,
            "",
        ],
    )
    def test_el_evaluador_y_el_pipeline_leen_el_monto_igual(self, crudo):
        """El evaluador y el pipeline comparten la lectura de montos.

        Son las dos mitades de la misma comparación —uno lee el comprobante, el
        otro la carga—, así que una regla duplicada los desincroniza y produce
        discrepancias falsas. Antes de unificar, el evaluador leía ``"1.000"``
        como ``1`` y ``"1.234.567"`` como **ilegible**, mientras el pipeline
        devolvía ``1000`` y ``1234567``: los importes grandes —los que más
        importa controlar— quedaban sin comparar.

        El test fija la equivalencia contra ``normalizar_monto``, que es la
        implementación única. Si alguien vuelve a escribir un parser propio acá,
        esto lo detecta.
        """
        esperado = normalizar_monto(crudo)
        obtenido = a_numero(crudo)
        if esperado is None:
            assert obtenido is None
        else:
            assert obtenido == pytest.approx(float(esperado))


# ---------------------------------------------------------------------------
# Las reglas del diff
# ---------------------------------------------------------------------------


class TestReglaTipoComprobante:
    def test_090_y_099_son_indistintos(self):
        # Regla 1: los tiques usan 090/099 para lo mismo.
        r = _diff({"tipo_comprobante": "090"}, {"tipo_comprobante": "099"})
        assert r["campos"]["tipo_comprobante"]["coincide"] is True

    def test_tipos_distintos_discrepan_y_son_criticos(self):
        r = _diff({"tipo_comprobante": "A"}, {"tipo_comprobante": "B"})
        assert r["campos"]["tipo_comprobante"]["coincide"] is False
        assert "tipo_comprobante" in r["discrepancias_criticas"]

    def test_sin_datos_es_no_verificable(self):
        r = _diff({"tipo_comprobante": "A"}, {})
        assert r["campos"]["tipo_comprobante"]["coincide"] == "no_verificable"


class TestReglaRazonSocial:
    def test_tolera_formato(self):
        r = _diff(
            {"razon_social_emisor": "Estación de Servicio S.A."},
            {"razon_social_emisor": "ESTACION DE SERVICIO SA"},
        )
        assert r["campos"]["razon_social_emisor"]["coincide"] is True

    def test_razones_distintas_discrepan_pero_no_son_criticas(self):
        r = _diff(
            {"razon_social_emisor": "Farmacia Silva"},
            {"razon_social_emisor": "Azul Combustibles"},
        )
        assert r["campos"]["razon_social_emisor"]["coincide"] is False
        assert "razon_social_emisor" not in r["discrepancias_criticas"]


class TestReglaCuit:
    def test_compara_digito_a_digito(self):
        # Regla 3: el CUIT se compara por sus 11 dígitos, sin guiones.
        r = _diff({"cuit_emisor": "30-12345678-9"}, {"cuit_emisor": "30123456789"})
        assert r["campos"]["cuit_emisor"]["coincide"] is True

    def test_un_digito_distinto_discrepa_y_es_critico(self):
        r = _diff({"cuit_emisor": "30-12345678-9"}, {"cuit_emisor": "30-12345678-8"})
        assert r["campos"]["cuit_emisor"]["coincide"] is False
        assert "cuit_emisor" in r["discrepancias_criticas"]


class TestReglaFecha:
    def test_compara_como_fecha_no_como_texto(self):
        # Regla 4: «14/08/2025» y «2025-08-14» son la misma fecha.
        r = _diff({"fecha_emision": "14/08/2025"}, {"fecha_emision": "2025-08-14"})
        assert r["campos"]["fecha_emision"]["coincide"] is True

    def test_fechas_distintas_discrepan_y_son_criticas(self):
        r = _diff({"fecha_emision": "14/08/2025"}, {"fecha_emision": "15/08/2025"})
        assert r["campos"]["fecha_emision"]["coincide"] is False
        assert "fecha_emision" in r["discrepancias_criticas"]

    def test_una_fecha_ilegible_no_se_compara(self):
        r = _diff({"fecha_emision": "ilegible"}, {"fecha_emision": "2025-08-14"})
        assert r["campos"]["fecha_emision"]["coincide"] == "no_verificable"


class TestReglaNumeroFactura:
    def test_ignora_el_separador_punto_de_venta(self):
        # Regla 5: «0002-00000123» y «000200000123» son el mismo número.
        r = _diff({"nro_factura": "0002-00000123"}, {"nro_factura": "000200000123"})
        assert r["campos"]["nro_factura"]["coincide"] is True

    def test_numeros_distintos_discrepan_pero_no_son_criticos(self):
        r = _diff({"nro_factura": "0002-00000123"}, {"nro_factura": "0002-00000999"})
        assert r["campos"]["nro_factura"]["coincide"] is False
        assert "nro_factura" not in r["discrepancias_criticas"]


class TestReglaMoneda:
    def test_la_moneda_coincide(self):
        r = _diff({"moneda": "ARS"}, {"moneda": "ARS"})
        assert r["campos"]["moneda"]["coincide"] is True

    def test_monedas_distintas_discrepan(self):
        r = _diff({"moneda": "ARS"}, {"moneda": "USD"})
        assert r["campos"]["moneda"]["coincide"] is False


class TestReglaSubtotal:
    def test_si_discrimina_se_compara_el_neto(self):
        r = _diff(
            {"subtotal": 100.0, "discrimina_impuestos": True},
            {"subtotal": 100.0},
        )
        assert r["campos"]["subtotal"]["coincide"] is True

    def test_si_no_discrimina_se_compara_contra_el_total(self):
        # Regla 7 (factura B/C): sin IVA discriminado el neto no existe, así que
        # el subtotal cargado se contrasta contra el total del comprobante.
        r = _diff(
            {"importe_total": 121.0, "discrimina_impuestos": False},
            {"subtotal": 121.0},
        )
        assert r["campos"]["subtotal"]["coincide"] is True

    def test_un_subtotal_distinto_discrepa_y_es_critico(self):
        r = _diff({"subtotal": 100.0}, {"subtotal": 999.0})
        assert r["campos"]["subtotal"]["coincide"] is False
        assert "subtotal" in r["discrepancias_criticas"]


class TestReglaImpuestos:
    def test_compara_por_concepto(self):
        r = _diff(
            {"iva": 21.0, "impuestos_internos": 5.0},
            {"impuestos": {"iva": 21.0, "impuestos_internos": 5.0}},
        )
        impuestos = r["campos"]["impuestos"]
        assert impuestos["iva"]["coincide"] is True
        assert impuestos["impuestos_internos"]["coincide"] is True
        # Los conceptos que ningún lado declaró no se juzgan: "no_verificable"
        # no es "coincide" (nunca se comparó) ni "False" (no hay discrepancia).
        assert impuestos["percepciones_iibb"]["coincide"] == "no_verificable"
        assert impuestos["otros"]["coincide"] == "no_verificable"

    def test_un_impuesto_distinto_discrepa_y_es_critico(self):
        r = _diff({"iva": 21.0}, {"impuestos": {"iva": 10.5}})
        assert any(c["coincide"] is False for c in r["campos"]["impuestos"].values())
        assert "impuestos" in r["discrepancias_criticas"]

    def test_si_no_discrimina_y_hay_impuesto_cargado_discrepa(self):
        # Cargar un IVA que el comprobante no discrimina es un error de carga.
        r = _diff({"discrimina_impuestos": False}, {"impuestos": {"iva": 21.0}})
        assert r["campos"]["impuestos"]["iva"]["coincide"] is False

    def test_sin_impuesto_en_ningun_lado_es_no_verificable(self):
        r = _diff({}, {"impuestos": {}})
        assert r["campos"]["impuestos"]["iva"]["coincide"] == "no_verificable"


class TestReglaMontoNoGravadoYTotal:
    def test_la_diferencia_puede_estar_explicada_por_el_no_gravado(self):
        # Reglas 9 y 10: la diferencia entre el total del comprobante y el
        # cargado queda explicada si hay un monto no gravado igual a esa
        # diferencia.
        r = _diff(
            {"importe_total": 100.0},
            {"importe_total_facturado": 110.0, "monto_no_gravado": 10.0},
        )
        campo = r["campos"]["importe_total_facturado"]
        assert campo["coincide"] is True
        assert campo["diferencia_explicada_por_monto_no_gravado"] is True

    def test_una_diferencia_sin_explicacion_discrepa(self):
        r = _diff({"importe_total": 100.0}, {"importe_total_facturado": 110.0})
        campo = r["campos"]["importe_total_facturado"]
        assert campo["coincide"] is False
        assert campo["diferencia"] == pytest.approx(10.0)
        assert "importe_total_facturado" in r["discrepancias_criticas"]

    def test_totales_iguales_coinciden(self):
        r = _diff({"importe_total": 100.0}, {"importe_total_facturado": 100.0})
        assert r["campos"]["importe_total_facturado"]["coincide"] is True


class TestReglasCondicionales:
    def test_los_comensales_no_entran_en_las_discrepancias_criticas(self):
        # Regla 13: el campo es informativo, no bloquea la aprobación.
        r = _diff(
            {"categoria_gasto_sugerida": "Restaurante"},
            {"categoria_gasto": "Restaurante"},
        )
        assert "cantidad_comensales_personas" not in r["discrepancias_criticas"]
        assert (
            r["campos"]["cantidad_comensales_personas"]["requerido_por_categoria"] is True
        )

    def test_una_categoria_sin_comensales_no_los_requiere(self):
        r = _diff(
            {"categoria_gasto_sugerida": "Peaje"},
            {"categoria_gasto": "Peaje"},
        )
        assert (
            r["campos"]["cantidad_comensales_personas"]["requerido_por_categoria"] is False
        )

    def test_los_litros_se_comparan_en_su_categoria(self):
        # Regla 14: en combustible los litros suelen estar impresos.
        r = _diff(
            {"categoria_gasto_sugerida": "Combustible", "cantidad_litros": 40.0},
            {"categoria_gasto": "Combustible", "cantidad_litros": 40.0},
        )
        assert r["campos"]["cantidad_litros"]["coincide"] is True

    def test_los_litros_no_impresos_son_no_verificable(self):
        # Si el comprobante no los imprime, no se puede afirmar que falten.
        r = _diff(
            {"categoria_gasto_sugerida": "Combustible"},
            {"categoria_gasto": "Combustible", "cantidad_litros": 40.0},
        )
        assert r["campos"]["cantidad_litros"]["coincide"] == "no_verificable"


class TestEstadoGlobal:
    def test_sin_discrepancias_el_estado_es_ok(self):
        r = _diff(
            {"tipo_comprobante": "A", "cuit_emisor": "30123456789"},
            {"tipo_comprobante": "A", "cuit_emisor": "30123456789"},
        )
        assert r["discrepancias_criticas"] == []
        assert r["estado_global"] == "OK"

    def test_con_una_discrepancia_critica_el_estado_es_revisar(self):
        r = _diff({"cuit_emisor": "30123456789"}, {"cuit_emisor": "30123456788"})
        assert r["estado_global"] == "REVISAR"

    def test_una_imagen_ilegible_da_incompleto(self):
        # Una imagen mala no es una discrepancia: no se puede verificar.
        r = _diff({"legibilidad": "MALA"}, {})
        assert r["estado_global"] == "INCOMPLETO"

    def test_devuelve_la_misma_forma_que_el_modo_validar(self):
        # El consumo aguas abajo es uniforme: el diff no inventa un shape.
        r = _diff({"tipo_comprobante": "A"}, {"tipo_comprobante": "A"})
        for clave in (
            "estado_global",
            "resumen",
            "campos",
            "discrepancias_criticas",
            "campos_no_legibles",
        ):
            assert clave in r, clave


# ---------------------------------------------------------------------------
# Aritmética (lo que el modelo no debe calcular)
# ---------------------------------------------------------------------------


class TestAritmetica:
    def test_los_componentes_suman_el_total(self):
        # El caso real de la farmacia: 52069,85 + 10118,12 + 10934,67.
        r = verificar_aritmetica(
            {
                "subtotal": 52069.85,
                "exento": 10118.12,
                "iva": 10934.67,
                "importe_total": 73122.64,
            }
        )
        assert r["calculable"] is True
        assert r["cierra"] is True

    def test_detecta_una_diferencia_de_diez_pesos(self):
        # El caso que motivó hacer la suma en Python: el modelo declaró que
        # cerraba con $10 de diferencia.
        r = verificar_aritmetica({"subtotal": 90.0, "importe_total": 100.0})
        assert r["cierra"] is False
        assert r["diferencia"] == pytest.approx(10.0)

    def test_la_tolerancia_del_cierre_es_de_centavos(self):
        r = verificar_aritmetica({"subtotal": 100.0, "importe_total": 100.01})
        assert r["cierra"] is True
        assert TOLERANCIA_MONTO < 0.05

    def test_sin_total_no_es_calculable(self):
        # Sin datos no se puede afirmar que cierre: se declara, no se inventa.
        r = verificar_aritmetica({"subtotal": 100.0})
        assert r["calculable"] is False
        assert r["cierra"] is None

    def test_sin_componentes_no_es_calculable(self):
        r = verificar_aritmetica({"importe_total": 100.0})
        assert r["calculable"] is False

    def test_cuando_no_cierra_lista_los_faltantes(self):
        # El candidato más probable es un importe que el modelo no transcribió.
        r = verificar_aritmetica({"subtotal": 90.0, "iva": None, "importe_total": 100.0})
        assert r["cierra"] is False
        assert "iva" in r["faltantes"]

    def test_el_exento_entra_en_la_suma(self):
        # El caso de la farmacia: el exento se le escapaba a gpt-4o.
        componentes = set(COMPONENTES_DEL_TOTAL)
        assert "exento" in componentes
        assert "no_gravado" in componentes
        assert "iva" in componentes

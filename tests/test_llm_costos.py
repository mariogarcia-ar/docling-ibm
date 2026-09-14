"""Costos: precios, tokens y cálculo del gasto.

Lo que estos tests protegen es la **honestidad del número**. Un costo mal
calculado no rompe nada —nadie deja de procesar por eso— pero hace que una
decisión se tome con datos falsos: elegir un modelo «más barato» que no lo es,
o creer que una corrida salió gratis cuando en realidad no se supo calcular.

Por eso los casos que importan no son los felices, sino los límites: un modelo
sin precio, un precio a medias, una entrada cacheada.
"""

from __future__ import annotations

import pytest

from voucherflow.llm.costos import (
    CONFIANZA_FORMULA,
    CONFIANZA_HISTORICO,
    PRECIOS_CACHE,
    PRECIOS_REFERENCIA,
    clave_modelo,
    costo_de_tokens,
    costo_legible,
    estimar_costo,
    estimar_tokens_de_texto,
    formatear_precio,
    parsear_precios,
    precio_cache_de,
    precio_de,
)


class TestClaveModelo:
    def test_el_match_exacto_gana(self):
        tabla = {"gpt-4o": 1, "gpt-4o-mini": 2}
        assert clave_modelo("gpt-4o", tabla) == "gpt-4o"

    def test_tolera_el_sufijo_de_fecha(self):
        # Los modelos versionados usan el precio de su familia.
        tabla = {"gpt-4o": 1}
        assert clave_modelo("gpt-4o-2026-08-06", tabla) == "gpt-4o"

    def test_el_prefijo_mas_largo_gana(self):
        # `gpt-4o-mini-2026` tiene que matchear `gpt-4o-mini`, no `gpt-4o`.
        tabla = {"gpt-4o": 1, "gpt-4o-mini": 2}
        assert clave_modelo("gpt-4o-mini-2026", tabla) == "gpt-4o-mini"

    def test_el_comodin_se_usa_si_no_hay_otro(self):
        assert clave_modelo("modelo-raro", {"*": (1, 2)}) == "*"

    def test_sin_match_ni_comodin_devuelve_none(self):
        assert clave_modelo("modelo-raro", {"gpt-4o": 1}) is None


class TestParsearPrecios:
    def test_parsea_modelos_separados_por_coma(self):
        tabla = parsear_precios("gpt-4o=2.5/10, gemini-2.5-flash=0.3/2.5")
        assert tabla["gpt-4o"] == (2.5, 10.0)
        assert tabla["gemini-2.5-flash"] == (0.3, 2.5)

    def test_el_comodin_es_un_modelo_mas(self):
        assert parsear_precios("*=1/3")["*"] == (1.0, 3.0)

    def test_un_precio_sin_definir_queda_en_none(self):
        # `-` significa "no sé este precio", que NO es lo mismo que cero.
        assert parsear_precios("modelo=-/3")["modelo"] == (None, 3.0)
        assert parsear_precios("modelo=1/-")["modelo"] == (1.0, None)

    def test_rechaza_una_forma_invalida(self):
        with pytest.raises(ValueError, match="modelo=entrada/salida"):
            parsear_precios("gpt-4o=2.5")

    def test_rechaza_un_precio_negativo(self):
        with pytest.raises(ValueError, match="negativo"):
            parsear_precios("gpt-4o=-1/10")

    def test_rechaza_vacio(self):
        with pytest.raises(ValueError, match="vacío"):
            parsear_precios("   ,  ")


class TestPrecioDe:
    def test_usa_la_tabla_del_llamador(self):
        assert precio_de("m", {"m": (1.0, 2.0)}) == (1.0, 2.0)

    def test_cae_a_la_tabla_de_referencia(self):
        # Un modelo de la tabla de referencia, sin overrides.
        assert precio_de("gpt-4o", {}) == PRECIOS_REFERENCIA["gpt-4o"]

    def test_un_modelo_desconocido_no_tiene_precio(self):
        # ⚠️ No devuelve (0, 0): eso haría que el costo saliera 0 y pareciera
        # gratis. Devuelve None para que el reporte lo declare.
        assert precio_de("modelo-que-no-existe", {}) == (None, None)

    def test_se_puede_desactivar_la_tabla_de_referencia(self):
        assert precio_de("gpt-4o", {}, por_defecto=False) == (None, None)


class TestCostoDeTokens:
    def test_calcula_el_costo_de_entrada_y_salida(self):
        # 1M de entrada a 2.5 y 1M de salida a 10.
        assert costo_de_tokens(1_000_000, 1_000_000, 2.5, 10.0) == pytest.approx(12.5)

    def test_sin_ningun_precio_no_hay_costo(self):
        assert costo_de_tokens(1000, 1000, None, None) is None

    def test_un_precio_a_medias_da_un_piso(self):
        # Solo se conoce la entrada: se cobra esa parte (el número es un piso).
        assert costo_de_tokens(1_000_000, 1_000_000, 2.5, None) == pytest.approx(2.5)

    def test_la_entrada_cacheada_se_cobra_mas_barata(self):
        # 1M de entrada de los cuales 900k vinieron del caché a 0.006.
        costo = costo_de_tokens(
            1_000_000,
            0,
            0.30,
            None,
            cache_hit_tokens=900_000,
            precio_cache=0.006,
        )
        assert costo == pytest.approx((100_000 * 0.30 + 900_000 * 0.006) / 1e6)

    def test_sin_precio_de_cache_no_se_descuenta_nada(self):
        # Si el proveedor no expone el precio de caché, no se puede asumir el
        # descuento: se cobra la entrada completa.
        costo = costo_de_tokens(
            1_000_000, 0, 0.30, None, cache_hit_tokens=900_000, precio_cache=None
        )
        assert costo == pytest.approx(0.30)

    def test_el_costo_se_redondea_a_seis_decimales(self):
        costo = costo_de_tokens(1, 0, 2.5, None)
        assert costo == round(costo, 6)


class TestPrecioCacheDe:
    def test_devuelve_el_precio_de_cache_del_modelo(self):
        assert precio_cache_de("deepseek-flash") == PRECIOS_CACHE["deepseek-flash"]

    def test_un_modelo_sin_cache_devuelve_none(self):
        # OpenAI no expone un precio de caché distinto en esta tabla.
        assert precio_cache_de("gpt-4o") is None


class TestFormato:
    def test_formatear_precio_marca_el_desconocido(self):
        assert formatear_precio(None) == "—"
        assert formatear_precio(2.5) == "2.5"

    def test_costo_legible_no_miente_con_cero(self):
        # ⚠️ `None` es "no sé", y no puede imprimirse como "US$ 0.0000", que se
        # lee como "salió gratis".
        assert costo_legible(None) == "—"
        assert "0.0" not in costo_legible(None)
        assert costo_legible(0.0) == "US$ 0.0000"


class TestEstimarTokens:
    def test_usa_la_proporcion_medida(self):
        # 3920 chars / 3.92 = 1000 tokens.
        assert estimar_tokens_de_texto(3920) == 1000

    def test_un_texto_corto_no_da_cero_tokens(self):
        assert estimar_tokens_de_texto(1) == 1
        assert estimar_tokens_de_texto(0) == 1


class TestEstimarCosto:
    def _estimar(self, **over):
        base = dict(
            caracteres_prompt=3920,
            imagenes=1,
            tokens_por_imagen=1024,
            precio_entrada=0.30,
            precio_salida=1.20,
        )
        base.update(over)
        return estimar_costo(**base)

    def test_suma_texto_e_imagen(self):
        r = self._estimar()
        assert r["tokens_texto_estimados"] == 1000
        assert r["tokens_imagen_estimados"] == 1024
        assert r["tokens_entrada_estimados"] == 2024
        assert r["calculable"] is True

    def test_sin_precio_la_estimacion_es_no_calculable(self):
        # Se declara, no se inventa un cero.
        r = self._estimar(precio_entrada=None, precio_salida=None)
        assert r["costo_estimado_usd"] is None
        assert r["calculable"] is False

    def test_declara_la_confianza(self):
        # Una estimación nunca se presenta como una factura.
        r = self._estimar()
        assert r["confianza"] == CONFIANZA_FORMULA
        r = self._estimar(confianza=CONFIANZA_HISTORICO)
        assert r["confianza"] == CONFIANZA_HISTORICO

    def test_el_costo_crece_con_las_imagenes(self):
        una = self._estimar(imagenes=1)["costo_estimado_usd"]
        diez = self._estimar(imagenes=10)["costo_estimado_usd"]
        assert diez > una

    def test_guarda_los_precios_usados(self):
        # El reporte tiene que poder explicar con qué precios se estimó.
        r = self._estimar()
        assert r["precio_entrada"] == 0.30
        assert r["precio_salida"] == 1.20

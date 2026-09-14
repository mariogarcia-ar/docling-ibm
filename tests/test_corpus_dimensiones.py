"""Dimensión objetivo y tokens de visión (``voucherflow.corpus.dimensiones``).

Lo que se protege acá son los **tres defectos del loop de shell** que este módulo
corrige (ver el docstring de ``corpus.imagen``):

1. Nunca agrandar (``scale=1024:-1`` agrandaba las chicas).
2. Reducir por **lado mayor**, no por ancho (una vertical salía ~45% más pesada).
3. Alinear a múltiplos de 28 para que Qwen2.5-VL no re-escale.
"""

from __future__ import annotations

import pytest

from voucherflow.corpus.dimensiones import (
    FACTOR_PATCH_QWEN2VL,
    LADO_MAYOR_PX,
    _dimensiones_local,
    dimensiones_objetivo,
    tokens_estimados_vlm,
)

#: Casos reales del corpus (vertical grande, chica, justo en el límite, alargada).
CASOS = [
    (3000, 4000, "vertical grande"),
    (600, 800, "chica"),
    (1024, 1024, "justo en el objetivo"),
    (4000, 300, "muy alargada"),
    (2000, 2000, "cuadrada"),
]

#: Los que SÍ se reducen (los otros se devuelven tal cual, sin alinear).
CASOS_REDUCIDOS = [c for c in CASOS if max(c[:2]) > 1024]


class TestNuncaAgranda:
    """Defecto 1: ``scale=1024:-1`` agrandaba lo que ya entraba."""

    @pytest.mark.parametrize("ancho,alto,caso", CASOS)
    def test_no_agranda_ningun_caso(self, ancho, alto, caso):
        n_ancho, n_alto = dimensiones_objetivo(ancho, alto, LADO_MAYOR_PX)
        assert n_ancho <= ancho, f"{caso}: el ancho creció"
        assert n_alto <= alto, f"{caso}: el alto creció"

    def test_una_imagen_que_ya_entra_queda_igual(self):
        """La chica sale idéntica: no se toca (el loop base la agrandaba a 1024)."""
        assert dimensiones_objetivo(600, 800, LADO_MAYOR_PX) == (600, 800)

    def test_justo_en_el_limite_no_se_toca(self):
        """El límite es inclusivo: 1024 con objetivo 1024 entra."""
        assert dimensiones_objetivo(1024, 1024, LADO_MAYOR_PX) == (1024, 1024)

    def test_lado_mayor_cero_o_negativo_devuelve_original(self):
        """Sin objetivo no hay reducción (evita una división sin sentido)."""
        assert dimensiones_objetivo(3000, 4000, 0) == (3000, 4000)
        assert dimensiones_objetivo(3000, 4000, -10) == (3000, 4000)


class TestReducePorLadoMayor:
    """Defecto 2: el objetivo es el LADO MAYOR, no el ancho."""

    def test_la_vertical_no_se_reduce_por_el_ancho(self):
        """Con ``scale=1024:-1`` la vertical salía 1024x1365 (>1 Mpx).

        Acá el resultado tiene el MISMO lado mayor que una cuadrada de 2000 px:
        la reducción se mide sobre el lado mayor, no sobre el ancho.
        """
        vertical = dimensiones_objetivo(3000, 4000, LADO_MAYOR_PX)
        cuadrada = dimensiones_objetivo(2000, 2000, LADO_MAYOR_PX)
        assert max(vertical) == max(cuadrada)
        # Y el ancho SÍ es menor que el objetivo (no se fijó el ancho).
        assert vertical[0] < LADO_MAYOR_PX

    def test_el_objetivo_no_se_excede_mas_de_lo_que_alinea(self):
        """⚠️ El objetivo es *aproximado*: la alineación a 28 puede pasarlo.

        Es la contracara de alinear: redondear hacia arriba a un múltiplo de 28
        deja el lado mayor hasta 27 px por encima del objetivo (1024 → 1036).
        No es un bug: es el precio de que Qwen2.5-VL no re-escale. Se fija acá
        para que nadie lo "arregle" rompiendo la alineación.
        """
        n_ancho, n_alto = dimensiones_objetivo(3000, 4000, LADO_MAYOR_PX)
        exceso = max(n_ancho, n_alto) - LADO_MAYOR_PX
        assert 0 < exceso < FACTOR_PATCH_QWEN2VL
        assert max(n_ancho, n_alto) % FACTOR_PATCH_QWEN2VL == 0

    def test_la_reduccion_de_tokens_de_una_vertical_es_grande(self):
        """El caso del docstring: menos tokens que fijando el ancho.

        1024x1365 (lo que hacía ``scale=1024:-1``) vs el resultado real. Se
        compara contra el recálculo del gridding, no contra un número a mano.
        """
        alineado = dimensiones_objetivo(3000, 4000, LADO_MAYOR_PX)
        solo_ancho = (LADO_MAYOR_PX, 1365)  # lo que hacía `scale=1024:-1`
        assert tokens_estimados_vlm(*alineado) < tokens_estimados_vlm(*solo_ancho)


class TestAlineacionQwen:
    """Defecto 3 (el punto fino): sin alinear, Qwen2.5-VL re-escalea y el conteo miente."""

    @pytest.mark.parametrize("ancho,alto,caso", CASOS_REDUCIDOS)
    def test_alineado_es_multiplo_de_28(self, ancho, alto, caso):
        n_ancho, n_alto = dimensiones_objetivo(ancho, alto, LADO_MAYOR_PX)
        assert n_ancho % FACTOR_PATCH_QWEN2VL == 0, f"{caso}: ancho no alineado"
        assert n_alto % FACTOR_PATCH_QWEN2VL == 0, f"{caso}: alto no alineado"

    @pytest.mark.parametrize("ancho,alto", [(600, 800), (1024, 1024), (1000, 1024)])
    def test_una_imagen_que_no_se_reduce_no_se_realinea(self, ancho, alto):
        """Si ya entra en el objetivo se devuelve TAL CUAL, sin alinear.

        Alinear una imagen que no se reduce sería *modificarla* (y agrandarla,
        si el redondeo sube), que es exactamente el defecto 1.
        """
        assert dimensiones_objetivo(ancho, alto, LADO_MAYOR_PX) == (ancho, alto)

    def test_la_alineacion_no_incumple_el_no_agrandar(self):
        """Alinear redondea hacia arriba: no debe pasarse del original."""
        n_ancho, n_alto = _dimensiones_local(2060, 2060, 1030, 256, alinear=True)
        assert n_ancho <= 2060 and n_alto <= 2060

    def test_el_piso_del_lado_menor_protege_las_alargadas(self):
        """Una imagen muy alargada puede quedar con el lado mayor > objetivo.

        Es deliberado (``LADO_MENOR_MINIMO_PX``): sin el piso, 4000x300 se
        reduciría a 280x21 y el lado corto sería ilegible. El precio es que el
        objetivo del lado mayor no se respeta.
        """
        n_ancho, n_alto = dimensiones_objetivo(4000, 300, LADO_MAYOR_PX)
        assert n_alto >= 256
        assert n_ancho > LADO_MAYOR_PX

    def test_sin_alinear_puede_no_ser_multiplo(self):
        """``--sin-alinear`` es para cuando el destino NO es Qwen2.5-VL."""
        n_ancho, n_alto = _dimensiones_local(2000, 2000, 1000, 256, alinear=False)
        assert (n_ancho % FACTOR_PATCH_QWEN2VL) != 0 or (n_alto % 28) != 0


class TestTokens:
    """El gridding de Qwen2.5-VL: cada token cubre 28x28 px."""

    def test_un_token_por_bloque_de_28(self):
        assert tokens_estimados_vlm(28, 28) == 1
        assert tokens_estimados_vlm(56, 56) == 4

    def test_redondea_hacia_arriba(self):
        """Un píxel de más abre un bloque nuevo (no se trunca)."""
        assert tokens_estimados_vlm(29, 28) == 2

    def test_el_valor_conocido_de_una_vertical_grande(self):
        """3000x4000 = 108 x 143 bloques (verificado contra el cálculo real)."""
        assert tokens_estimados_vlm(3000, 4000) == 108 * 143

    def test_una_imagen_chica_cuesta_poco(self):
        assert tokens_estimados_vlm(600, 800) < tokens_estimados_vlm(3000, 4000)

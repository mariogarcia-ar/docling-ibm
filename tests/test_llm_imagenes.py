"""Costo de la imagen: cada proveedor cobra distinto, y la estimación lo respeta.

⚠️ **El bug que estos tests fijan.** El módulo tenía una sola fórmula —la de
DeepSeek, tope fijo— y el núcleo la aplicaba a **todos** los proveedores. Con
``-p openai`` la estimación del ``--dry-run`` erraba entre 0,9x y 12x:

* sobreestimaba 1,3x en fotos (1.024 vs 765),
* sobreestimaba 4x en miniaturas (1.024 vs 255),
* **subestimaba** 0,9x en un A4 a 300 dpi (1.024 vs 1.105),
* y con ``--detalle low`` sobreestimaba **12x** (1.024 vs 85).

La costura no tenía test: `test_llm_costos` probaba la aritmética y
`test_llm_proveedores` las capacidades declaradas, pero nadie verificaba que la
estimación **consultara** al proveedor.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from PIL import Image

from voucherflow.llm.corrida import Opciones, estimar_costo_corrida
from voucherflow.llm.imagenes import (
    MOSAICO_TOKENS_BASE,
    MOSAICO_TOKENS_DETALLE_BAJO,
    tokens_imagen,
)
from voucherflow.llm.proveedores import (
    PROVEEDORES,
    AdaptadorDeepSeek,
    AdaptadorGemini,
    AdaptadorOpenAI,
)


def _cap(clase):
    return clase().capacidades


class TestEstrategiaDeclarada:
    def test_cada_proveedor_declara_una_estrategia_valida(self):
        """Un valor desconocido tiene que reventar, no caer al default."""
        from voucherflow.llm.protocolo import ESTRATEGIAS_IMAGEN

        for nombre, clase in PROVEEDORES.items():
            assert clase().capacidades.estrategia_imagen in ESTRATEGIAS_IMAGEN, nombre

    def test_una_estrategia_desconocida_es_un_error_de_configuracion(self):
        from voucherflow.llm.protocolo import Capacidades

        with pytest.raises(ValueError, match="estrategia_imagen desconocida"):
            Capacidades(nombre="raro", estrategia_imagen="inventada")

    def test_openai_cobra_por_mosaicos_y_deepseek_a_tope_fijo(self):
        """La diferencia es el punto: si los dos declaran lo mismo, nada cambió."""
        from voucherflow.llm.protocolo import ESTRATEGIA_MOSAICOS, ESTRATEGIA_TOPE_FIJO

        assert _cap(AdaptadorOpenAI).estrategia_imagen == ESTRATEGIA_MOSAICOS
        assert _cap(AdaptadorDeepSeek).estrategia_imagen == ESTRATEGIA_TOPE_FIJO
        assert _cap(AdaptadorGemini).estrategia_imagen == ESTRATEGIA_TOPE_FIJO


class TestTopeFijoDeepSeek:
    """DeepSeek redimensiona a ~1300×1300 y agranda las chicas: resolución da igual."""

    @pytest.mark.parametrize(
        "ancho,alto",
        [(4000, 3000), (1500, 2000), (2480, 3508), (1024, 1024), (120, 90)],
    )
    def test_la_resolucion_no_cambia_el_costo(self, ancho, alto):
        cap = _cap(AdaptadorDeepSeek)
        assert tokens_imagen(ancho, alto, "high", cap) == 1024

    def test_el_detalle_no_cambia_el_costo(self):
        """`detail='low'` no ahorra en DeepSeek: el redimensionado posterior la
        vuelve a llevar al objetivo."""
        cap = _cap(AdaptadorDeepSeek)
        assert tokens_imagen(4000, 3000, "low", cap) == tokens_imagen(4000, 3000, "high", cap)


class TestMosaicosOpenAI:
    """OpenAI: `85 + 170 × mosaicos`, con `detail=low` plano en 85."""

    def test_una_foto_grande_se_recorta_y_da_765(self):
        """4000×3000: el lado mayor baja a 2048 y el menor a 768 → 765 tokens.

        Los dos recortes que aplica el proveedor antes de contar mosaicos.
        """
        cap = _cap(AdaptadorOpenAI)
        assert tokens_imagen(4000, 3000, "high", cap) == 765

    def test_una_tipica_de_1500x2000_da_lo_mismo_que_la_de_4000x3000(self):
        """Las dos se recortan a los mismos límites: el peso no importa, la
        resolución sí (pero estas dos terminan en el mismo lugar)."""
        cap = _cap(AdaptadorOpenAI)
        assert tokens_imagen(1500, 2000, "high", cap) == tokens_imagen(
            4000, 3000, "high", cap
        )

    def test_un_a4_a_300dpi_cuesta_mas_que_una_foto(self):
        """El caso que el lab **subestimaba**: 1.024 estimados vs 1.105 reales."""
        cap = _cap(AdaptadorOpenAI)
        a4 = tokens_imagen(2480, 3508, "high", cap)
        assert a4 == 1105
        assert a4 > tokens_imagen(4000, 3000, "high", cap)

    def test_una_miniatura_cuesta_255_y_no_85(self):
        """Por debajo del piso queda un mosaico: no se agranda hasta 768."""
        cap = _cap(AdaptadorOpenAI)
        assert tokens_imagen(120, 90, "high", cap) == 255

    def test_el_detalle_bajo_es_plano_e_igual_para_cualquier_tamano(self):
        """El caso que el lab sobreestimaba 12x (1.024 vs 85)."""
        cap = _cap(AdaptadorOpenAI)
        for ancho, alto in ((4000, 3000), (2480, 3508), (120, 90)):
            assert tokens_imagen(ancho, alto, "low", cap) == MOSAICO_TOKENS_DETALLE_BAJO

    def test_los_recortes_nunca_agrandan(self):
        """Una imagen chica no se escala hacia arriba (a diferencia de DeepSeek)."""
        cap = _cap(AdaptadorOpenAI)
        assert tokens_imagen(100, 100, "high", cap) == MOSAICO_TOKENS_BASE + 170

    def test_una_imagen_degenerada_no_rompe(self):
        """Dimensiones ilegibles (0) devuelven el piso, no un ZeroDivisionError."""
        cap = _cap(AdaptadorOpenAI)
        assert tokens_imagen(0, 0, "high", cap) == MOSAICO_TOKENS_BASE


class TestSinCapacidades:
    def test_sin_capacidades_se_asume_el_default_historico(self):
        """El parámetro es opcional: sin él se mantiene el comportamiento previo."""
        assert tokens_imagen(4000, 3000, "high") == 1024


class TestCosturaConLaEstimacion:
    """⚠️ El test que faltaba: que la estimación **consulte** al proveedor.

    `test_llm_costos` probaba la aritmética y `test_llm_proveedores` las
    capacidades declaradas; nadie verificaba la junta. Por eso `tokens_por_imagen`
    quedó declarado sin que nadie lo leyera.
    """

    def _imagenes(self, tmp_path) -> list[Path]:
        rutas = []
        for nombre, (ancho, alto) in (
            ("grande.jpg", (4000, 3000)),
            ("a4.jpg", (2480, 3508)),
            ("mini.jpg", (120, 90)),
        ):
            ruta = tmp_path / nombre
            Image.new("RGB", (ancho, alto), "white").save(ruta, quality=90)
            rutas.append(ruta)
        return rutas

    def _estimar(self, tmp_path, proveedor: str, detalle: str = "high"):
        return estimar_costo_corrida(
            self._imagenes(tmp_path),
            "SISTEMA",
            "USER [IMAGEN] {}",
            Opciones(
                modo="extraer",
                modelo="deepseek-flash",
                detalle=detalle,
                temperatura=None,
                max_tokens=None,
                esfuerzo=None,
                salida=tmp_path / "out",
                forzar=False,
                workers=1,
                dry_run=True,
                incluir_ejemplo=False,
                proveedor=proveedor,
            ),
        )

    def test_cambiar_de_proveedor_cambia_la_estimacion(self, tmp_path):
        """Si los dos dan lo mismo, la estimación no está consultando al proveedor."""
        ds = self._estimar(tmp_path, "deepseek")
        oa = self._estimar(tmp_path, "openai")

        assert ds["opciones"]["estrategia_imagen"] == "tope_fijo"
        assert oa["opciones"]["estrategia_imagen"] == "mosaicos"
        assert ds["tokens_entrada_totales"] != oa["tokens_entrada_totales"]

    def test_la_estimacion_de_openai_usa_los_mosaicos(self, tmp_path):
        """El caso A4: el lab subestimaba (1.024) cuando el real es 1.105."""
        oa = self._estimar(tmp_path, "openai")
        por_imagen = [d["tokens_imagen"] for d in oa["detalle"]]
        assert 1105 in por_imagen  # el A4
        assert 255 in por_imagen  # la miniatura
        assert 1024 not in por_imagen  # el tope de DeepSeek no aplica

    def test_la_estimacion_de_deepseek_es_constante(self, tmp_path):
        ds = self._estimar(tmp_path, "deepseek")
        assert {d["tokens_imagen"] for d in ds["detalle"]} == {1024}

    def test_detalle_bajo_abarece_en_openai(self, tmp_path):
        alto = self._estimar(tmp_path, "openai", "high")
        bajo = self._estimar(tmp_path, "openai", "low")
        assert bajo["tokens_entrada_totales"] < alto["tokens_entrada_totales"]
        assert {d["tokens_imagen"] for d in bajo["detalle"]} == {85}

    def test_detalle_bajo_no_abarece_en_deepseek(self, tmp_path):
        """`detail` no cambia el costo ahí: no se puede prometer un ahorro que no existe."""
        alto = self._estimar(tmp_path, "deepseek", "high")
        bajo = self._estimar(tmp_path, "deepseek", "low")
        assert bajo["tokens_entrada_totales"] == alto["tokens_entrada_totales"]

    def test_el_resumen_declara_el_proveedor_y_su_estrategia(self, tmp_path):
        """El reporte nombraba «tope fijo de DeepSeek» aunque se corriera OpenAI."""
        oa = self._estimar(tmp_path, "openai")
        assert oa["opciones"]["proveedor"] == "openai"
        assert oa["tokens_imagen"] == oa["detalle"][0]["tokens_imagen"]

"""Reporte y orquestación de una corrida (``corpus.reporte`` y ``corpus.corrida``).

El reporte tiene una regla de honestidad heredada del resto del repo: **lo que no
se midió se declara, no se estima como si fuera un dato**. En el modo de solo
medición no hay peso destino, así que comparar contra 0 daría un «-100%»
inventado; el reporte devuelve ``None`` y la presentación dice «no medido».
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from voucherflow.corpus.corrida import (
    ErrorCorpus,
    contar_fallos,
    escribir_reporte,
    normalizar_extensiones,
    planificar,
    validar,
)
from voucherflow.corpus.modelo import ESTADO_FALLO, ESTADO_OMITIDO, ESTADO_REDUCIDO, Opciones, Resultado
from voucherflow.corpus.reporte import comparar, resumen


def _opciones(tmp_path: Path, **cambios) -> Opciones:
    base = dict(
        salida=tmp_path / "out",
        lado_mayor=1024,
        calidad=80,
        piso_lado_menor=256,
        alinear=True,
        backend="pillow",
        formato="mismo",
        forzar=False,
        escribir=True,
        copiar_no_reducidas=False,
        workers=1,
        detalle=False,
    )
    base.update(cambios)
    return Opciones(**base)  # type: ignore[arg-type]


def _resultado(
    estado: str,
    *,
    dims_origen=(3000, 4000),
    dims_destino=(756, 1036),
    peso_origen=200_000,
    peso_destino=5_000,
    nombre="x.jpg",
) -> Resultado:
    return Resultado(
        Path(nombre),
        Path("out") / nombre,
        estado,
        dims_origen=dims_origen,
        dims_destino=dims_destino,
        peso_origen=peso_origen,
        peso_destino=peso_destino,
    )


class TestComparar:
    """Solo se compara donde hay medición de destino."""

    def test_calcula_la_reduccion(self):
        antes, despues, pct = comparar([_resultado(ESTADO_REDUCIDO)], "peso")
        assert (antes, despues) == (200_000, 5_000)
        assert pct == 97.5

    def test_sin_medicion_de_destino_devuelve_none(self):
        """El caso de solo-medición: comparar contra 0 sería un «-100%» falso."""
        solo_origen = _resultado(ESTADO_REDUCIDO, peso_destino=None)
        antes, despues, pct = comparar([solo_origen], "peso")
        assert pct is None
        assert (antes, despues) == (0, 0)

    def test_ignora_los_que_no_tienen_par(self):
        """Un fallo sin dimensiones no debe ensuciar el promedio."""
        mixto = [
            _resultado(ESTADO_REDUCIDO),
            _resultado(ESTADO_FALLO, dims_origen=None, dims_destino=None, peso_origen=None, peso_destino=None),
        ]
        antes, _, pct = comparar(mixto, "peso")
        assert antes == 200_000  # solo el reducido
        assert pct == 97.5

    def test_sin_datos_no_hay_porcentaje(self):
        assert comparar([], "peso") == (0, 0, None)

    def test_compara_tokens_con_dimensiones(self):
        antes, despues, pct = comparar([_resultado(ESTADO_REDUCIDO)], "tokens")
        assert antes > despues
        assert pct is not None and pct > 0


class TestResumen:
    """El resumen cuenta por estado y agrega antes/después."""

    def test_cuenta_por_estado(self, tmp_path):
        rep = resumen(
            [
                _resultado(ESTADO_REDUCIDO),
                _resultado(ESTADO_REDUCIDO, nombre="b.jpg"),
                _resultado(ESTADO_OMITIDO, nombre="c.jpg"),
                _resultado(ESTADO_FALLO, nombre="d.jpg"),
            ],
            _opciones(tmp_path),
        )
        assert rep["archivos"] == 4
        assert rep["reducidos"] == 2
        assert rep["omitidos"] == 1
        assert rep["fallos"] == 1

    def test_el_total_de_origen_incluye_todo(self, tmp_path):
        """``peso_origen_bytes`` es todo lo recorrido, no solo lo reducido."""
        rep = resumen(
            [
                _resultado(ESTADO_REDUCIDO, peso_origen=100, peso_destino=10),
                _resultado(ESTADO_OMITIDO, nombre="b.jpg", peso_origen=50, peso_destino=None),
            ],
            _opciones(tmp_path),
        )
        assert rep["peso_origen_bytes"] == 150

    def test_en_simulacion_no_hay_porcentaje_de_peso(self, tmp_path):
        rep = resumen(
            [_resultado(ESTADO_REDUCIDO, peso_destino=None)],
            _opciones(tmp_path, escribir=False),
        )
        assert rep["reduccion_peso_pct"] is None

    def test_declara_de_donde_salieron_los_defaults(self, tmp_path):
        rep = resumen([], _opciones(tmp_path))
        assert rep["defaults_desde"]

    def test_las_opciones_quedan_registradas(self, tmp_path):
        rep = resumen([], _opciones(tmp_path, calidad=55))
        assert rep["opciones"]["calidad"] == 55
        assert rep["opciones"]["solo_medir"] is False

    def test_es_serializable(self, tmp_path):
        rep = resumen([_resultado(ESTADO_REDUCIDO)], _opciones(tmp_path))
        assert json.loads(json.dumps(rep))["archivos"] == 1


class TestValidacion:
    """Los argumentos incoherentes se rechazan ANTES de recorrer el corpus."""

    @pytest.mark.parametrize(
        "cambio,mensaje",
        [
            ({"lado_mayor": 0}, "lado-mayor"),
            ({"calidad": 0}, "calidad"),
            ({"calidad": 101}, "calidad"),
            ({"workers": 0}, "workers"),
        ],
    )
    def test_rechaza_opciones_invalidas(self, tmp_path, cambio, mensaje):
        with pytest.raises(ErrorCorpus, match=mensaje):
            validar(_opciones(tmp_path, **cambio))

    def test_acepta_los_limites(self, tmp_path):
        validar(_opciones(tmp_path, calidad=1, workers=1, lado_mayor=1))

    def test_un_backend_ffmpeg_inservible_falla_con_motivo(self, tmp_path, monkeypatch):
        """El preflight es una vez, no un error replicado por archivo."""
        from voucherflow.corpus import imagen

        monkeypatch.setattr(imagen, "_FFMPEG_CACHE", {"motivo": "ffmpeg roto"})
        with pytest.raises(ErrorCorpus, match="ffmpeg roto"):
            validar(_opciones(tmp_path, backend="ffmpeg"))


class TestExtensiones:
    """Parseo de ``--extensiones``."""

    def test_normaliza_con_punto_y_minuscula(self):
        assert normalizar_extensiones("JPG, .PNG") == frozenset({".jpg", ".png"})

    def test_ignora_espacios_y_vacios(self):
        assert normalizar_extensiones(" jpg , , png ") == frozenset({".jpg", ".png"})

    def test_vacio_es_error(self):
        with pytest.raises(ErrorCorpus, match="vacío"):
            normalizar_extensiones(" , , ")


class TestPlanificar:
    """El plan es el mismo para simular y para escribir (si divergiera, mentiría)."""

    @pytest.fixture
    def corpus(self, tmp_path: Path) -> Path:
        from PIL import Image

        raiz = tmp_path / "files"
        for mes, lote, nombre in (
            ("2025-08", "2D2C9343", "a.jpg"),
            ("2025-08", "2D2C9343", "b.jpg"),
            ("2025-09", "AABBCCDD", "c.jpg"),
        ):
            carpeta = raiz / mes / lote
            carpeta.mkdir(parents=True, exist_ok=True)
            Image.new("RGB", (100, 100)).save(carpeta / nombre, "JPEG")
        return raiz

    def test_espeja_la_estructura(self, corpus, tmp_path):
        raiz, _, tareas = planificar([corpus], _opciones(tmp_path))
        assert raiz == corpus
        destinos = {str(d.relative_to(tmp_path / "out")) for _, d in tareas}
        assert destinos == {
            "2025-08/2D2C9343/a.jpg",
            "2025-08/2D2C9343/b.jpg",
            "2025-09/AABBCCDD/c.jpg",
        }

    def test_la_misma_imagen_planea_el_mismo_destino(self, corpus, tmp_path):
        """La propiedad que evita pagar dos veces, a nivel de plan."""
        op = _opciones(tmp_path)
        _, _, desde_raiz = planificar([corpus], op)
        _, _, desde_mes = planificar([corpus / "2025-08"], op)
        destino_a = dict(desde_raiz)[_ruta(desde_raiz, "a.jpg")]
        destino_b = dict(desde_mes)[_ruta(desde_mes, "a.jpg")]
        assert destino_a == destino_b

    def test_el_limite_recorta(self, corpus, tmp_path):
        _, _, tareas = planificar([corpus], _opciones(tmp_path), limite=2)
        assert len(tareas) == 2

    def test_no_incluye_lo_que_esta_en_la_salida(self, corpus, tmp_path):
        """Reprocesar la propia salida sería un bucle."""
        salida = corpus / "2025-08"
        op = _opciones(tmp_path, salida=salida)
        _, _, tareas = planificar([corpus], op)
        assert all(not str(o).startswith(str(salida)) for o, _ in tareas)

    def test_respeta_la_extension_pedida(self, corpus, tmp_path):
        _, _, tareas = planificar(
            [corpus], _opciones(tmp_path, formato="jpg"), extensiones=frozenset({".jpg"})
        )
        assert all(d.suffix == ".jpg" for _, d in tareas)


def _ruta(tareas, nombre: str) -> Path:
    """La imagen (clave del plan) cuyo nombre coincide."""
    return next(o for o, _ in tareas if o.name == nombre)


class TestReporteArchivo:
    """El reporte en disco lleva versión y detalle por archivo."""

    def test_escribe_version_y_detalle(self, tmp_path):
        destino = tmp_path / "rep.json"
        resultados = [_resultado(ESTADO_REDUCIDO)]
        escribir_reporte(destino, resumen(resultados, _opciones(tmp_path)), resultados)
        datos = json.loads(destino.read_text(encoding="utf-8"))
        assert datos["version"].startswith("voucherflow-corpus@")
        assert len(datos["archivos_detalle"]) == 1
        assert datos["archivos_detalle"][0]["estado"] == ESTADO_REDUCIDO

    def test_crea_las_carpetas_del_reporte(self, tmp_path):
        destino = tmp_path / "a" / "b" / "rep.json"
        escribir_reporte(destino, resumen([], _opciones(tmp_path)), [])
        assert destino.exists()


class TestContarFallos:
    """El código de salida del CLI depende de esto."""

    def test_cuenta_solo_los_fallos(self):
        assert contar_fallos([_resultado(ESTADO_REDUCIDO), _resultado(ESTADO_FALLO)]) == 1

    def test_una_omision_no_es_fallo(self):
        assert contar_fallos([_resultado(ESTADO_OMITIDO)]) == 0

    def test_sin_resultados_no_hay_fallos(self):
        assert contar_fallos([]) == 0

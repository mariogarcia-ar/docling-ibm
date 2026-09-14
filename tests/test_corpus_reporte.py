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
    describir_colisiones,
    detectar_colisiones,
    escribir_reporte,
    normalizar_extensiones,
    planificar,
    validar,
)
from voucherflow.corpus.modelo import (
    CATEGORIA_COPIA,
    CATEGORIA_LECTURA,
    CATEGORIA_REANUDADO,
    ESTADO_FALLO,
    ESTADO_OMITIDO,
    ESTADO_REDUCIDO,
    Opciones,
    Resultado,
)
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
    motivo="",
    categoria="",
) -> Resultado:
    return Resultado(
        Path(nombre),
        Path("out") / nombre,
        estado,
        motivo=motivo,
        dims_origen=dims_origen,
        dims_destino=dims_destino,
        peso_origen=peso_origen,
        peso_destino=peso_destino,
        categoria=categoria,
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


class TestColisionesDeDestino:
    """⚠️ Regresión: dos originales escribiendo el MISMO archivo.

    Con ``--formato jpg`` (o con ``foto.JPG`` y ``foto.jpg``, que colisionan ya
    con el default) el plan calculaba un solo destino para dos originales. La
    corrida terminaba con un archivo y el reporte daba por reducidos a los DOS:
    pérdida silenciosa de datos que además el reporte afirmaba como éxito.
    """

    def _tarea(self, tmp_path: Path, origen: str, destino: str):
        return (tmp_path / "files" / origen, tmp_path / "out" / destino)

    def test_detecta_dos_originales_al_mismo_destino(self, tmp_path):
        tareas = [
            self._tarea(tmp_path, "factura.jpg", "factura.jpg"),
            self._tarea(tmp_path, "factura.png", "factura.jpg"),
        ]
        colisiones = detectar_colisiones(tareas)
        assert len(colisiones) == 1
        assert len(next(iter(colisiones.values()))) == 2

    def test_no_marca_destinos_distintos(self, tmp_path):
        """Control: sin esto, la guarda podría rechazar todo y pasar igual."""
        tareas = [
            self._tarea(tmp_path, "a.jpg", "a.jpg"),
            self._tarea(tmp_path, "b.jpg", "b.jpg"),
        ]
        assert detectar_colisiones(tareas) == {}

    def test_el_mismo_original_dos_veces_no_es_colision(self, tmp_path):
        """Pasar la carpeta y una imagen de adentro ya lo resuelve ``expandir``."""
        tarea = self._tarea(tmp_path, "a.jpg", "a.jpg")
        assert detectar_colisiones([tarea, tarea]) == {}

    def test_detecta_la_colision_por_ruta_no_normalizada(self, tmp_path):
        """El corpus se recorre con rutas que pueden traer ``..`` o symlinks."""
        tareas = [
            self._tarea(tmp_path, "a.jpg", "sub/a.jpg"),
            self._tarea(tmp_path, "b.jpg", "sub/../sub/a.jpg"),
        ]
        assert len(detectar_colisiones(tareas)) == 1

    def test_el_texto_nombra_los_dos_originales(self, tmp_path):
        tareas = [
            self._tarea(tmp_path, "factura.jpg", "factura.jpg"),
            self._tarea(tmp_path, "factura.png", "factura.jpg"),
        ]
        texto = describir_colisiones(detectar_colisiones(tareas))
        assert "factura.jpg" in texto and "factura.png" in texto
        assert "--formato mismo" in texto  # la salida sugerida

    def test_planificar_con_formato_jpg_produce_la_colision(self, tmp_path):
        """El caso real que la motivó, a nivel de plan (dos nombres base iguales)."""
        from PIL import Image

        corpus = tmp_path / "files"
        corpus.mkdir()
        Image.new("RGB", (3000, 4000)).save(corpus / "factura.jpg", "JPEG")
        Image.new("RGB", (3000, 4000)).save(corpus / "factura.png", "PNG")
        _, _, tareas = planificar([corpus], _opciones(tmp_path, formato="jpg"))
        assert len(tareas) == 2
        assert len(detectar_colisiones(tareas)) == 1

    def test_formato_mismo_igualmente_colisiona_con_mayusculas(self, tmp_path):
        """``foto.JPG`` y ``foto.jpg`` son el mismo archivo en macOS/Windows."""
        tareas = [
            self._tarea(tmp_path, "foto.JPG", "foto.jpg"),
            self._tarea(tmp_path, "foto.jpg", "foto.jpg"),
        ]
        assert len(detectar_colisiones(tareas)) == 1


class TestContadoresDelReporte:
    """Lo que el reporte agrega para no mezclar causas distintas."""

    def test_cuenta_las_copiadas(self, tmp_path):
        rep = resumen(
            [
                _resultado(ESTADO_OMITIDO, categoria=CATEGORIA_COPIA),
                _resultado(ESTADO_OMITIDO, nombre="b.jpg", categoria=CATEGORIA_COPIA),
                _resultado(ESTADO_REDUCIDO, nombre="c.jpg"),
            ],
            _opciones(tmp_path),
        )
        assert rep["copiadas"] == 2

    def test_una_omision_no_copiada_no_cuenta(self, tmp_path):
        """«ya entra en el objetivo» sin copiar no dejó archivo en la salida."""
        rep = resumen(
            [
                _resultado(
                    ESTADO_OMITIDO, motivo="3000x4000px ya entra en el objetivo"
                )
            ],
            _opciones(tmp_path),
        )
        assert rep["copiadas"] == 0

    def test_distingue_el_fallo_de_lectura(self, tmp_path):
        """Una imagen ilegible no es lo mismo que un disco lleno."""
        rep = resumen(
            [
                _resultado(
                    ESTADO_FALLO,
                    nombre="roto.jpg",
                    motivo="no se pudo leer: [Errno 13]",
                    categoria=CATEGORIA_LECTURA,
                ),
                _resultado(
                    ESTADO_FALLO, nombre="pisa.jpg", motivo="PermissionError: denied"
                ),
            ],
            _opciones(tmp_path),
        )
        assert rep["fallos"] == 2
        assert rep["fallos_lectura"] == 1

    def test_cuenta_los_que_ya_estaban(self, tmp_path):
        rep = resumen(
            [_resultado(ESTADO_OMITIDO, categoria=CATEGORIA_REANUDADO)],
            _opciones(tmp_path),
        )
        assert rep["ya_estaba"] == 1

    def test_el_motivo_no_cambia_los_numeros(self, tmp_path):
        """⚠️ Antes los contadores buscaban TEXTO dentro de ``motivo``.

        Retocar un mensaje cambiaba los números del reporte en silencio. Ahora
        manda el dato: un ``reanudado`` con prosa de copia sigue siendo
        reanudado, y cuenta donde corresponde.
        """
        rep = resumen(
            [
                _resultado(
                    ESTADO_OMITIDO,
                    motivo="texto que antes contaba como copia: copiada sin reducir",
                    categoria=CATEGORIA_REANUDADO,
                )
            ],
            _opciones(tmp_path),
        )
        assert rep["copiadas"] == 0
        assert rep["ya_estaba"] == 1

    def test_cuenta_los_que_engordaron(self, tmp_path):
        """Un archivo que creció no es un fallo, pero el reporte lo declara."""
        rep = resumen(
            [
                _resultado(ESTADO_REDUCIDO, peso_origen=1_000, peso_destino=3_000),
                _resultado(ESTADO_REDUCIDO, nombre="b.jpg"),
            ],
            _opciones(tmp_path),
        )
        assert rep["engordaron"] == 1

    def test_sin_engordar_no_cuenta(self, tmp_path):
        rep = resumen([_resultado(ESTADO_REDUCIDO)], _opciones(tmp_path))
        assert rep["engordaron"] == 0

    def test_un_fallo_no_entra_en_ningun_contador_de_omitidos(self, tmp_path):
        rep = resumen(
            [
                _resultado(
                    ESTADO_FALLO,
                    motivo="no se pudo escribir: PermissionError",
                )
            ],
            _opciones(tmp_path),
        )
        assert (rep["copiadas"], rep["ya_estaba"], rep["fallos_lectura"]) == (0, 0, 0)

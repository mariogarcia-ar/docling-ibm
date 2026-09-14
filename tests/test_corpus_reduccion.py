"""Reducción de una imagen (``voucherflow.corpus.reduccion``).

Los casos que importan son las **decisiones**: reducir, omitir, copiar sin
reducir, reanudar, simular y no sobrescribir la entrada. Cada una tiene un motivo
distinto y el reporte los cuenta por separado, así que confundirlas cambiaría lo
que el operador cree que pasó con su corpus.
"""

from __future__ import annotations

from dataclasses import replace
from pathlib import Path

import pytest

from voucherflow.corpus.modelo import (
    ESTADO_FALLO,
    ESTADO_OMITIDO,
    ESTADO_REDUCIDO,
    Opciones,
    Resultado,
)
from voucherflow.corpus.reduccion import procesar

LADO_MAYOR = 1024


@pytest.fixture
def opciones(tmp_path: Path) -> Opciones:
    """Opciones por defecto, apuntando la salida a un temporal."""
    return Opciones(
        salida=tmp_path / "out",
        lado_mayor=LADO_MAYOR,
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


@pytest.fixture
def imagenes(tmp_path: Path) -> dict[str, Path]:
    """Una imagen grande (se reduce) y una chica (ya entra en el objetivo)."""
    from PIL import Image

    creadas: dict[str, Path] = {}
    for nombre, (ancho, alto) in {
        "grande": (3000, 4000),
        "chica": (600, 800),
        "cuadrada": (2000, 2000),
    }.items():
        ruta = tmp_path / f"{nombre}.jpg"
        Image.new("RGB", (ancho, alto), (200, 150, 100)).save(ruta, "JPEG")
        creadas[nombre] = ruta
    return creadas


class TestReduccion:
    """El caso normal: una imagen grande se reduce y baja de tokens."""

    def test_reduce_la_grande(self, imagenes, opciones):
        r = procesar(imagenes["grande"], opciones.salida / "g.jpg", opciones)
        assert r.estado == ESTADO_REDUCIDO
        assert r.ok is True
        assert r.motivo == ""

    def test_el_destino_se_escribe(self, imagenes, opciones):
        destino = opciones.salida / "g.jpg"
        procesar(imagenes["grande"], destino, opciones)
        assert destino.exists()

    def test_baja_los_tokens_de_vision(self, imagenes, opciones):
        """El motivo de existir de la capacidad: menos tokens, no solo menos bytes."""
        r = procesar(imagenes["grande"], opciones.salida / "g.jpg", opciones)
        assert r.tokens_destino is not None and r.tokens_origen is not None
        assert r.tokens_destino < r.tokens_origen

    def test_baja_el_peso(self, imagenes, opciones):
        r = procesar(imagenes["grande"], opciones.salida / "g.jpg", opciones)
        assert r.peso_destino is not None and r.peso_origen is not None
        assert r.peso_destino < r.peso_origen

    def test_crea_las_carpetas_intermedias(self, imagenes, opciones):
        destino = opciones.salida / "2025-08" / "2D2C9343" / "g.jpg"
        procesar(imagenes["grande"], destino, opciones)
        assert destino.exists()


class TestNoSeTocaLoQueYaEntra:
    """Defecto 1 del loop base, verificado de punta a punta."""

    def test_omite_la_chica(self, imagenes, opciones):
        r = procesar(imagenes["chica"], opciones.salida / "c.jpg", opciones)
        assert r.estado == ESTADO_OMITIDO
        assert "ya entra en el objetivo" in r.motivo

    def test_no_escribe_nada_para_la_chica(self, imagenes, opciones):
        """Omitir es NO escribir: si escribiera, la salida tendría copias."""
        procesar(imagenes["chica"], opciones.salida / "c.jpg", opciones)
        assert not (opciones.salida / "c.jpg").exists()

    def test_una_omision_no_es_un_fallo(self, imagenes, opciones):
        """``ok`` refleja el estado, no el código de salida del CLI."""
        r = procesar(imagenes["chica"], opciones.salida / "c.jpg", opciones)
        assert r.ok is True


class TestCopiarNoReducidas:
    """``--copiar-no-reducidas``: la salida queda completa, sin reducir."""

    def test_copia_la_chica(self, imagenes, opciones):
        destino = opciones.salida / "c.jpg"
        r = procesar(imagenes["chica"], destino, replace(opciones, copiar_no_reducidas=True))
        assert r.estado == ESTADO_OMITIDO
        assert destino.exists()
        assert "copiada sin reducir" in r.motivo

    def test_la_copia_conserva_los_bytes(self, imagenes, opciones):
        destino = opciones.salida / "c.jpg"
        procesar(imagenes["chica"], destino, replace(opciones, copiar_no_reducidas=True))
        assert destino.read_bytes() == imagenes["chica"].read_bytes()

    def test_sin_escribir_no_copia_y_lo_declara(self, imagenes, opciones):
        r = procesar(
            imagenes["chica"],
            opciones.salida / "c.jpg",
            replace(opciones, copiar_no_reducidas=True, escribir=False),
        )
        assert r.estado == ESTADO_OMITIDO
        assert "se copiaría" in r.motivo

    def test_la_copia_existente_no_se_reescribe(self, imagenes, opciones):
        """Segundo pase: la copia ya está, así que se declara (no se recopia)."""
        destino = opciones.salida / "c.jpg"
        copiar = replace(opciones, copiar_no_reducidas=True)
        procesar(imagenes["chica"], destino, copiar)
        r = procesar(imagenes["chica"], destino, copiar)
        assert "copia ya existente" in r.motivo


class TestReanudacion:
    """Lo que permite correr el corpus completo en varias pasadas."""

    def test_saltea_el_destino_existente(self, imagenes, opciones):
        destino = opciones.salida / "g.jpg"
        procesar(imagenes["grande"], destino, opciones)
        r = procesar(imagenes["grande"], destino, opciones)
        assert r.estado == ESTADO_OMITIDO
        assert "ya existe" in r.motivo

    def test_mide_el_destino_real_no_el_calculado(self, imagenes, opciones):
        """⚠️ No se asume que el destino tenga las dimensiones calculadas.

        Pudo generarse con otros parámetros; el reporte mide el archivo real en
        vez de inventar el resultado.
        """
        destino = opciones.salida / "g.jpg"
        procesar(imagenes["grande"], destino, opciones)
        r = procesar(imagenes["grande"], destino, opciones)
        assert r.dims_destino is not None
        from voucherflow.corpus.imagen import medir

        assert r.dims_destino == medir(destino)

    def test_forzar_reescribe(self, imagenes, opciones):
        destino = opciones.salida / "g.jpg"
        procesar(imagenes["grande"], destino, opciones)
        r = procesar(imagenes["grande"], destino, replace(opciones, forzar=True))
        assert r.estado == ESTADO_REDUCIDO


class TestSoloMedir:
    """El modo con el que conviene empezar siempre: no escribe nada."""

    def test_no_escribe_y_lo_declara(self, imagenes, opciones):
        destino = opciones.salida / "g.jpg"
        r = procesar(imagenes["grande"], destino, replace(opciones, escribir=False))
        assert r.estado == ESTADO_REDUCIDO
        assert "simulación" in r.motivo

    def test_no_crea_ni_la_carpeta_de_salida(self, imagenes, opciones):
        procesar(imagenes["grande"], opciones.salida / "g.jpg", replace(opciones, escribir=False))
        assert not opciones.salida.exists()

    def test_declara_las_dimensiones_que_tendria(self, imagenes, opciones):
        """Sirve para decidir ANTES de tocar el corpus: las dims sí se calculan."""
        r = procesar(imagenes["grande"], opciones.salida / "g.jpg", replace(opciones, escribir=False))
        assert r.dims_destino is not None
        assert max(r.dims_destino) < max(r.dims_origen or (0, 0))

    def test_no_inventa_un_peso_destino(self, imagenes, opciones):
        """No se escribió: el peso destino es ``None``, no un 0 que parece dato."""
        r = procesar(imagenes["grande"], opciones.salida / "g.jpg", replace(opciones, escribir=False))
        assert r.peso_destino is None


class TestSeguridad:
    """Nunca escribir sobre la entrada (lección de T-604)."""

    def test_se_omite_si_el_destino_es_el_origen(self, imagenes, opciones):
        r = procesar(imagenes["grande"], imagenes["grande"], opciones)
        assert r.estado == ESTADO_OMITIDO
        assert "coincide con el origen" in r.motivo

    def test_la_entrada_queda_intacta(self, imagenes, opciones):
        antes = imagenes["grande"].read_bytes()
        procesar(imagenes["grande"], imagenes["grande"], opciones)
        assert imagenes["grande"].read_bytes() == antes

    def test_detecta_la_colision_por_ruta_no_normalizada(self, imagenes, opciones):
        """La guarda compara rutas RESUELTAS: ``a/../a/x.jpg`` es el mismo archivo.

        Importa porque el corpus real se recorre con rutas que pueden venir con
        ``..`` o con symlinks, y escribir ahí destruiría la entrada sin que el
        nombre lo delate.
        """
        origen = imagenes["grande"]
        confusa = origen.parent / ".." / origen.parent.name / origen.name
        assert confusa.resolve() == origen.resolve()

        r = procesar(origen, confusa, opciones)
        assert r.estado == ESTADO_OMITIDO
        assert "coincide con el origen" in r.motivo

    def test_no_se_omite_si_es_otro_archivo(self, imagenes, opciones, tmp_path):
        """Control del anterior: un destino distinto SÍ se reduce.

        Sin este caso, la guarda podría estar rechazando todo y el test de
        colisión pasaría igual.
        """
        r = procesar(imagenes["grande"], tmp_path / "otro.jpg", opciones)
        assert r.estado == ESTADO_REDUCIDO


class TestFallos:
    """Un archivo malo devuelve un ``Resultado``, no una excepción."""

    def test_una_imagen_ilegible_es_un_fallo(self, tmp_path, opciones):
        malo = tmp_path / "malo.jpg"
        malo.write_bytes(b"no soy una imagen")
        r = procesar(malo, opciones.salida / "m.jpg", opciones)
        assert r.estado == ESTADO_FALLO
        assert r.ok is False
        assert "dimensiones" in r.motivo

    def test_un_archivo_inexistente_es_un_fallo(self, tmp_path, opciones):
        r = procesar(tmp_path / "no-existe.jpg", opciones.salida / "x.jpg", opciones)
        assert r.estado == ESTADO_FALLO
        assert "no se pudo leer" in r.motivo

    def test_el_fallo_no_deja_el_destino(self, tmp_path, opciones):
        malo = tmp_path / "malo.jpg"
        malo.write_bytes(b"no soy una imagen")
        procesar(malo, opciones.salida / "m.jpg", opciones)
        assert not (opciones.salida / "m.jpg").exists()


class TestFormato:
    """Defecto 3: escribir JPEG con extensión ``.png`` desalineaba bytes y nombre."""

    def test_png_se_guarda_como_png(self, tmp_path, opciones):
        from PIL import Image

        origen = tmp_path / "grande.png"
        Image.new("RGB", (3000, 4000), (10, 20, 30)).save(origen, "PNG")
        destino = opciones.salida / "g.png"
        procesar(origen, destino, opciones)
        with Image.open(destino) as img:
            assert img.format == "PNG"

    def test_jpg_se_guarda_como_jpeg(self, imagenes, opciones):
        from PIL import Image

        destino = opciones.salida / "g.jpg"
        procesar(imagenes["grande"], destino, opciones)
        with Image.open(destino) as img:
            assert img.format == "JPEG"


class TestResultado:
    """El contrato de datos del resultado (alimenta el reporte)."""

    @pytest.mark.parametrize(
        "estado", [ESTADO_REDUCIDO, ESTADO_OMITIDO, ESTADO_FALLO]
    )
    def test_acepta_los_estados_validos(self, estado):
        assert Resultado(Path("a.jpg"), None, estado).estado == estado

    def test_rechaza_un_estado_inventado(self):
        with pytest.raises(ValueError, match="estado desconocido"):
            Resultado(Path("a.jpg"), None, "inventado")

    def test_ida_y_vuelta_por_dict(self, imagenes, opciones):
        r = procesar(imagenes["grande"], opciones.salida / "g.jpg", opciones)
        vuelta = Resultado.desde_dict(r.a_dict())
        assert vuelta.origen == r.origen
        assert vuelta.estado == r.estado
        assert vuelta.dims_origen == r.dims_origen
        assert vuelta.dims_destino == r.dims_destino
        assert vuelta.peso_destino == r.peso_destino

    def test_el_dict_es_serializable(self, imagenes, opciones):
        import json

        r = procesar(imagenes["grande"], opciones.salida / "g.jpg", opciones)
        assert json.loads(json.dumps(r.a_dict()))["estado"] == ESTADO_REDUCIDO

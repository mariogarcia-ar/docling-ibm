"""Subcomando ``voucherflow corpus`` (``voucherflow.corpus.cli``).

⚠️ **Este archivo también fija el contrato de subcomandos del CLI.** Hasta acá
eran 11 (E-CLI-1 + los de auditoría) y estaban congelados por
``test_cli_t601.py``. Sumar ``corpus`` fue una **decisión explícita**: la
capacidad se necesitaba en el pipeline, no como script suelto. Los tests que
fijaban 11 se actualizaron a 12 a propósito; si alguien suma un 13.º comando sin
querer, estos dos archivos fallan.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from voucherflow.cli.main import COMANDOS, DESPACHO, construir_parser, main
from voucherflow.corpus.cli import EntornoCorpus
from voucherflow.corpus.cli import main as main_corpus

#: Lo que el contrato debe seguir diciendo: los 11 de siempre + ``corpus``.
COMANDOS_ESPERADOS = {
    "process",
    "validate",
    "classify",
    "extract",
    "extract-detect",
    "run",
    "batch",
    "ask",
    "arca",
    "case",
    "hitl",
    "corpus",
}


@pytest.fixture
def corpus(tmp_path: Path) -> Path:
    """Un corpus con la forma real, con una imagen grande y una chica."""
    from PIL import Image

    raiz = tmp_path / "files"
    for mes, lote, nombre, tam in (
        ("2025-08", "2D2C9343", "grande.jpg", (3000, 4000)),
        ("2025-08", "2D2C9343", "chica.jpg", (600, 800)),
    ):
        carpeta = raiz / mes / lote
        carpeta.mkdir(parents=True, exist_ok=True)
        Image.new("RGB", tam, (200, 150, 100)).save(carpeta / nombre, "JPEG")
    return raiz


@pytest.fixture
def entorno() -> EntornoCorpus:
    from io import StringIO

    return EntornoCorpus(stdout=StringIO(), stderr=StringIO())


def _correr(argv: list[str], entorno: EntornoCorpus | None = None) -> tuple[int, str, str]:
    """Corre el subcomando y devuelve ``(codigo, stdout, stderr)``."""
    from io import StringIO

    entorno = entorno or EntornoCorpus(stdout=StringIO(), stderr=StringIO())
    args = _parsear(argv)
    codigo = main_corpus(args, entorno=entorno)
    return codigo, entorno.stdout.getvalue(), entorno.stderr.getvalue()


def _parsear(argv: list[str]):
    """Parsea ``corpus <argv>`` con el parser REAL del CLI (no uno de prueba)."""
    return construir_parser().parse_args(["corpus", *argv])


class TestContrato:
    """El contrato de subcomandos, con el 12.º declarado a propósito."""

    def test_son_doce_y_estan_todos(self):
        assert set(COMANDOS) == COMANDOS_ESPERADOS
        assert len(COMANDOS) == 12

    def test_el_despacho_cubre_los_comandos(self):
        assert set(DESPACHO) == COMANDOS_ESPERADOS

    def test_corpus_esta_en_el_parser(self):
        assert _parsear(["x.jpg"]).comando == "corpus"

    def test_no_se_agrego_ningun_otro_comando(self):
        """Control del contrato: si aparece uno nuevo, esta lista lo delata."""
        assert set(COMANDOS) - COMANDOS_ESPERADOS == set()

    def test_el_entrypoint_del_paquete_apunta_al_cli_nuevo(self):
        """``voucherflow corpus`` despacha a ``voucherflow.corpus.cli``.

        ⚠️ Se importa por ``importlib`` porque ``voucherflow.cli.__init__``
        reexporta ``main``, y eso ensombrece el submódulo al hacer
        ``import voucherflow.cli.main``.
        """
        from importlib import import_module

        modulo_cli = import_module("voucherflow.cli.main")

        assert modulo_cli.DESPACHO["corpus"] is modulo_cli._cmd_corpus


class TestSoloMedir:
    """El modo con el que conviene empezar: no escribe nada."""

    def test_no_escribe_y_lo_declara(self, corpus, tmp_path, entorno):
        codigo, salida, _ = _correr(
            [str(corpus), "-o", str(tmp_path / "out"), "--solo-medir"], entorno
        )
        assert codigo == 0
        assert "solo-medir" in salida
        assert not (tmp_path / "out").exists()

    def test_informa_el_plan_antes_de_trabajar(self, corpus, tmp_path, entorno):
        _, _, error = _correr(
            [str(corpus), "-o", str(tmp_path / "out"), "--solo-medir"], entorno
        )
        assert "raíz de espejado" in error
        assert "imágenes         : 2" in error

    def test_no_inventa_el_porcentaje_de_peso(self, corpus, tmp_path, entorno):
        """Sin escritura no hay peso destino: se declara, no se estima."""
        _, salida, _ = _correr(
            [str(corpus), "-o", str(tmp_path / "out"), "--solo-medir"], entorno
        )
        assert "no medido" in salida


class TestCorridaReal:
    """Escribe de verdad, con la estructura espejada."""

    def test_reduce_y_espeja_el_arbol(self, corpus, tmp_path, entorno):
        out = tmp_path / "out"
        codigo, _, _ = _correr([str(corpus), "-o", str(out), "--workers", "1"], entorno)
        assert codigo == 0
        assert (out / "2025-08" / "2D2C9343" / "grande.jpg").exists()

    def test_omite_la_que_ya_entra(self, corpus, tmp_path, entorno):
        out = tmp_path / "out"
        _correr([str(corpus), "-o", str(out), "--workers", "1"], entorno)
        assert not (out / "2025-08" / "2D2C9343" / "chica.jpg").exists()

    def test_cuenta_los_estados(self, corpus, tmp_path, entorno):
        _, salida, _ = _correr([str(corpus), "-o", str(tmp_path / "out"), "--workers", "1"], entorno)
        assert "reducidos               : 1" in salida
        assert "omitidos                : 1" in salida
        assert "fallos                  : 0" in salida

    def test_informa_la_reduccion_de_tokens(self, corpus, tmp_path, entorno):
        _, salida, _ = _correr([str(corpus), "-o", str(tmp_path / "out"), "--workers", "1"], entorno)
        assert "tokens de visión (estim.)" in salida

    def test_es_idempotente_por_reanudacion(self, corpus, tmp_path, entorno):
        """Correr dos veces no reescribe: la segunda todo es "omitido"."""
        out = tmp_path / "out"
        _correr([str(corpus), "-o", str(out), "--workers", "1"], entorno)
        _, salida, _ = _correr([str(corpus), "-o", str(out), "--workers", "1"], entorno)
        assert "reducidos               : 0" in salida
        assert "omitidos                : 2" in salida

    def test_forzar_rehace(self, corpus, tmp_path, entorno):
        out = tmp_path / "out"
        _correr([str(corpus), "-o", str(out), "--workers", "1"], entorno)
        _, salida, _ = _correr(
            [str(corpus), "-o", str(out), "--forzar", "--workers", "1"], entorno
        )
        assert "reducidos               : 1" in salida

    def test_con_workers_paralelos(self, corpus, tmp_path, entorno):
        """El camino con pool de hilos debe dar el mismo resultado."""
        out = tmp_path / "out"
        codigo, salida, _ = _correr([str(corpus), "-o", str(out), "--workers", "4"], entorno)
        assert codigo == 0
        assert "reducidos               : 1" in salida
        assert (out / "2025-08" / "2D2C9343" / "grande.jpg").exists()


class TestDetalle:
    """``--detalle``: una línea por archivo, a stderr."""

    def test_imprime_una_linea_por_archivo(self, corpus, tmp_path, entorno):
        _, _, error = _correr(
            [str(corpus), "-o", str(tmp_path / "out"), "--detalle", "--workers", "1"],
            entorno,
        )
        assert error.count("[reducido]") == 1
        assert error.count("[omitido]") == 1

    def test_muestra_dimensiones_y_peso(self, corpus, tmp_path, entorno):
        _, _, error = _correr(
            [str(corpus), "-o", str(tmp_path / "out"), "--detalle", "--workers", "1"],
            entorno,
        )
        assert "3000x4000→" in error


class TestReporte:
    """``--reporte`` escribe el JSON completo."""

    def test_escribe_el_reporte(self, corpus, tmp_path, entorno):
        reporte = tmp_path / "rep.json"
        _correr(
            [str(corpus), "-o", str(tmp_path / "out"), "--reporte", str(reporte), "--workers", "1"],
            entorno,
        )
        datos = json.loads(reporte.read_text(encoding="utf-8"))
        assert datos["archivos"] == 2
        assert len(datos["archivos_detalle"]) == 2

    def test_lo_avisa_por_stdout(self, corpus, tmp_path, entorno):
        _, salida, _ = _correr(
            [str(corpus), "-o", str(tmp_path / "out"), "--reporte", str(tmp_path / "r.json"), "--workers", "1"],
            entorno,
        )
        assert "reporte escrito" in salida

    def test_el_reporte_distingue_los_estados(self, corpus, tmp_path, entorno):
        reporte = tmp_path / "rep.json"
        _correr(
            [str(corpus), "-o", str(tmp_path / "out"), "--reporte", str(reporte), "--workers", "1"],
            entorno,
        )
        estados = {d["estado"] for d in json.loads(reporte.read_text(encoding="utf-8"))["archivos_detalle"]}
        assert estados == {"reducido", "omitido"}


class TestCodigosDeSalida:
    """Los códigos siguen la convención del repo: 0 ok, 1 fallos, 2 uso."""

    def test_uso_incorrecto_da_2(self, corpus, entorno):
        codigo, _, error = _correr([str(corpus), "--calidad", "999"], entorno)
        assert codigo == 2
        assert "error:" in error

    def test_workers_invalido_da_2(self, corpus, entorno):
        assert _correr([str(corpus), "--workers", "0"], entorno)[0] == 2

    def test_extensiones_vacias_dan_2(self, corpus, entorno):
        assert _correr([str(corpus), "--extensiones", ","], entorno)[0] == 2

    def test_sin_imagenes_da_0(self, tmp_path, entorno):
        vacia = tmp_path / "vacia"
        vacia.mkdir()
        codigo, _, error = _correr([str(vacia)], entorno)
        assert codigo == 0
        assert "No hay imágenes" in error

    def test_un_fallo_da_1(self, tmp_path, entorno):
        """Un archivo con extensión de imagen pero ilegible reporta exit 1."""
        raiz = tmp_path / "files"
        raiz.mkdir()
        (raiz / "roto.jpg").write_bytes(b"no soy una imagen")
        codigo, salida, _ = _correr([str(raiz), "-o", str(tmp_path / "out"), "--workers", "1"], entorno)
        assert codigo == 1
        assert "fallos                  : 1" in salida

    def test_los_fallos_se_listan_en_stderr(self, tmp_path, entorno):
        """El detalle del fallo va a stderr, para no ensuciar el dato."""
        raiz = tmp_path / "files"
        raiz.mkdir()
        (raiz / "roto.jpg").write_bytes(b"no soy una imagen")
        _, _, error = _correr([str(raiz), "-o", str(tmp_path / "out"), "--workers", "1"], entorno)
        assert "--- Fallos (1) ---" in error


class TestSalidaPorDefecto:
    """El default sale de la configuración, no de un literal."""

    def test_sale_de_paths_processed(self):
        from voucherflow.corpus.cli import carpeta_por_defecto
        from voucherflow.settings.config import cargar_settings

        assert carpeta_por_defecto() == str(cargar_settings().paths.resolver("processed"))

    def test_la_salida_pedida_gana(self, corpus, tmp_path, entorno):
        _, _, error = _correr(
            [str(corpus), "-o", str(tmp_path / "elegida"), "--solo-medir"], entorno
        )
        assert str(tmp_path / "elegida") in error


class TestViaCliPrincipal:
    """El subcomando funciona a través del ``main`` del CLI (integración)."""

    def test_main_despacha_corpus(self, corpus, tmp_path, capsys):
        codigo = main(["corpus", str(corpus), "-o", str(tmp_path / "out"), "--workers", "1"])
        assert codigo == 0
        assert (tmp_path / "out" / "2025-08" / "2D2C9343" / "grande.jpg").exists()

    def test_main_reporta_el_resumen(self, corpus, tmp_path, capsys):
        main(["corpus", str(corpus), "-o", str(tmp_path / "out"), "--workers", "1"])
        assert "=== Resumen ===" in capsys.readouterr().out

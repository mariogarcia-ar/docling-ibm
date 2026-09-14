"""Credenciales y ``.env`` (``voucherflow.llm.entorno``).

⚠️ **Este archivo existe por un bug real.** ``resolver_api_key`` tenía
``DEEPSEEK_API_KEY`` **cableado** — el proveedor que se usó primero — y eso
rompía a los otros dos de dos maneras:

1. Con la clave correcta en el entorno (``GEMINI_API_KEY``) igual decía *«falta
   la credencial: definí GEMINI_API_KEY»*.
2. Peor: con la de DeepSeek presente le mandaba **esa** clave al endpoint de
   otro proveedor (un ``401`` confuso en vez de un error claro).

Y no tenía **ni un test**, que es por lo que sobrevivió. La regla que se fija
acá: la variable de la credencial la decide el **proveedor elegido**, no un
literal.
"""

from __future__ import annotations

import os
from pathlib import Path

import pytest

from voucherflow.llm.entorno import cargar_env, resolver_api_key

#: Variables que puede tocar este módulo (se aíslan por test).
VARIABLES = ("OPENAI_API_KEY", "DEEPSEEK_API_KEY", "GEMINI_API_KEY")


@pytest.fixture(autouse=True)
def entorno_limpio(monkeypatch):
    """Ninguna variable de API filtra entre tests (el entorno se contamina fácil)."""
    for variable in VARIABLES:
        monkeypatch.delenv(variable, raising=False)


class TestVariablePorProveedor:
    """La variable sale del adaptador del proveedor, no de un literal."""

    @pytest.mark.parametrize(
        "proveedor,variable",
        [
            ("openai", "OPENAI_API_KEY"),
            ("deepseek", "DEEPSEEK_API_KEY"),
            ("gemini", "GEMINI_API_KEY"),
        ],
    )
    def test_lee_la_variable_del_proveedor(self, proveedor, variable, monkeypatch):
        monkeypatch.setenv(variable, "la-clave")
        assert resolver_api_key(None, proveedor) == "la-clave"

    @pytest.mark.parametrize(
        "proveedor,variable",
        [
            ("openai", "OPENAI_API_KEY"),
            ("deepseek", "DEEPSEEK_API_KEY"),
            ("gemini", "GEMINI_API_KEY"),
        ],
    )
    def test_la_clave_de_otro_proveedor_no_sirve(
        self, proveedor, variable, monkeypatch
    ):
        """La contracara: cada proveedor lee **su** variable, no cualquiera."""
        for otra in VARIABLES:
            if otra != variable:
                monkeypatch.setenv(otra, "clave-ajena")
        assert resolver_api_key(None, proveedor) is None

    def test_gemini_no_usa_la_clave_de_deepseek(self, monkeypatch):
        """El caso concreto que fallaba: es el bug, en una línea."""
        monkeypatch.setenv("DEEPSEEK_API_KEY", "sk-deepseek")
        monkeypatch.setenv("GEMINI_API_KEY", "AIza-gemini")
        assert resolver_api_key(None, "gemini") == "AIza-gemini"

    def test_no_le_pasa_la_clave_de_otro_al_endpoint(self, monkeypatch):
        """Sin su clave, devuelve ``None`` en vez de una clave ajena.

        Devolver la de DeepSeek haría que el SDK mandara una credencial
        equivocada y el error fuera un 401 del servidor, no un mensaje claro.
        """
        monkeypatch.setenv("DEEPSEEK_API_KEY", "sk-deepseek")
        assert resolver_api_key(None, "gemini") is None

    def test_sin_proveedor_usa_el_default(self, monkeypatch):
        """Espeja el default del CLI (una sola fuente de verdad en ``proveedores``)."""
        from voucherflow.llm.proveedores import PROVEEDOR_POR_DEFECTO

        variable = {
            "openai": "OPENAI_API_KEY",
            "deepseek": "DEEPSEEK_API_KEY",
            "gemini": "GEMINI_API_KEY",
        }[PROVEEDOR_POR_DEFECTO]
        monkeypatch.setenv(variable, "la-del-default")
        assert resolver_api_key(None) == "la-del-default"

    def test_el_default_del_cli_es_el_de_proveedores(self):
        """El CLI no repite el literal del proveedor por defecto."""
        from voucherflow.llm.cli import construir_parser
        from voucherflow.llm.proveedores import PROVEEDOR_POR_DEFECTO

        assert construir_parser().parse_args(["x.jpg"]).proveedor == PROVEEDOR_POR_DEFECTO


class TestPrecedencia:
    """La bandera gana sobre el entorno (misma prioridad que el resto del repo)."""

    def test_la_explicita_gana(self, monkeypatch):
        monkeypatch.setenv("GEMINI_API_KEY", "la-del-entorno")
        assert resolver_api_key("la-de-la-bandera", "gemini") == "la-de-la-bandera"

    def test_la_explicita_gana_tambien_sin_variable(self):
        assert resolver_api_key("la-de-la-bandera", "gemini") == "la-de-la-bandera"

    def test_una_explicita_vacia_cae_al_entorno(self, monkeypatch):
        """``""`` es «no la pasé», no «pasé vacío»."""
        monkeypatch.setenv("GEMINI_API_KEY", "la-del-entorno")
        assert resolver_api_key("", "gemini") == "la-del-entorno"

    def test_sin_nada_devuelve_none(self):
        assert resolver_api_key(None, "gemini") is None


class TestProveedorDesconocido:
    """Un nombre inválido se rechaza nombrando los válidos."""

    def test_lanza_value_error(self):
        with pytest.raises(ValueError, match="proveedor desconocido"):
            resolver_api_key(None, "inventado")

    def test_lista_los_validos(self):
        with pytest.raises(ValueError, match="gemini"):
            resolver_api_key(None, "inventado")


class TestCargarEnv:
    """Parser mínimo de ``.env`` (el repo no suma ``python-dotenv``)."""

    def test_carga_pares_clave_valor(self, tmp_path, monkeypatch):
        archivo = tmp_path / ".env"
        archivo.write_text("GEMINI_API_KEY=AIza-123\n", encoding="utf-8")
        cargar_env(archivo)
        assert os.environ["GEMINI_API_KEY"] == "AIza-123"

    def test_quita_las_comillas(self, tmp_path):
        archivo = tmp_path / ".env"
        archivo.write_text('DEEPSEEK_API_KEY="sk-con-comillas"\n', encoding="utf-8")
        cargar_env(archivo)
        assert os.environ["DEEPSEEK_API_KEY"] == "sk-con-comillas"

    def test_ignora_comentarios_y_lineas_vacias(self, tmp_path):
        archivo = tmp_path / ".env"
        archivo.write_text(
            "# un comentario\n\n   \nGEMINI_API_KEY=ok\n", encoding="utf-8"
        )
        cargar_env(archivo)
        assert os.environ["GEMINI_API_KEY"] == "ok"

    def test_ignora_lineas_sin_igual(self, tmp_path):
        archivo = tmp_path / ".env"
        archivo.write_text("SIN_IGUAL\nGEMINI_API_KEY=ok\n", encoding="utf-8")
        cargar_env(archivo)
        assert os.environ["GEMINI_API_KEY"] == "ok"
        assert "SIN_IGUAL" not in os.environ

    def test_no_pisa_el_entorno_existente(self, tmp_path, monkeypatch):
        """Lo que ya está exportado gana: cargar un .env no debe sorprender."""
        monkeypatch.setenv("GEMINI_API_KEY", "la-de-la-terminal")
        archivo = tmp_path / ".env"
        archivo.write_text("GEMINI_API_KEY=la-del-archivo\n", encoding="utf-8")
        cargar_env(archivo)
        assert os.environ["GEMINI_API_KEY"] == "la-de-la-terminal"

    def test_una_ruta_none_no_rompe(self):
        cargar_env(None)

    def test_una_ruta_inexistente_no_rompe(self, tmp_path):
        cargar_env(tmp_path / "no-existe.env")

    def test_no_imprime_la_clave(self, tmp_path, capsys):
        """La credencial nunca se imprime ni se registra."""
        archivo = tmp_path / ".env"
        archivo.write_text("GEMINI_API_KEY=AIza-secreta\n", encoding="utf-8")
        cargar_env(archivo)
        capturado = capsys.readouterr()
        assert "AIza-secreta" not in capturado.out
        assert "AIza-secreta" not in capturado.err

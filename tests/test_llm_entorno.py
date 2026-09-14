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


class TestElCliCargaElEnvSolo:
    """⚠️ Regresión del port: el ``./.env`` se carga **sin** pedir ``--env``.

    Los dos scripts originales (``validar-deepseek.py`` / ``validar-openai.py``,
    recuperables con ``git show 31aa6dd^:<ruta>``) declaraban
    ``--env`` con ``default=Path(".env")``. Al unificar en ``voucherflow-lab``
    la bandera quedó sin default, así que un ``.env`` en disco dejó de tener
    efecto: el operador recibía «falta la credencial» con la clave ahí al lado
    (y la doc lo explicaba en vez de arreglarlo, que es lo que lo dejó pasar).

    Se ejercita el CLI real con ``ejecutar`` doble, no ``cargar_env`` sola: el
    bug estaba en el **cableado**, no en el parser de ``.env``.
    """

    @pytest.fixture(autouse=True)
    def _ejecutar_doble(self, monkeypatch):
        """Captura lo que el CLI le pasa a ``corrida.ejecutar`` sin tocar la red."""
        from voucherflow.llm import cli, corrida

        self.recibido: dict = {}

        def falso_ejecutar(**kw):
            self.recibido = kw
            return 0

        monkeypatch.setattr(corrida, "ejecutar", falso_ejecutar)
        monkeypatch.setattr(cli.corrida, "ejecutar", falso_ejecutar)
        # La carpeta de salida sale de la configuración: no es lo que se prueba.
        monkeypatch.setattr(
            "voucherflow.settings.config.cargar_settings",
            lambda: type("A", (), {"paths": type("P", (), {
                "resolver": staticmethod(lambda k: Path("var/validations"))
            })()})(),
        )

    def _correr(self, argv, cwd, monkeypatch):
        """Corre el CLI desde ``cwd`` (el ``./.env`` depende del directorio)."""
        from voucherflow.llm import cli

        monkeypatch.chdir(cwd)
        return cli.main(argv)

    def test_un_env_en_el_cwd_se_carga_sin_la_bandera(self, tmp_path, monkeypatch):
        """El caso del bug: el comando pelado tiene que resolver la credencial."""
        (tmp_path / ".env").write_text(
            "DEEPSEEK_API_KEY=sk-del-archivo\n", encoding="utf-8"
        )
        codigo = self._correr(["x.jpg", "-p", "deepseek"], tmp_path, monkeypatch)
        assert codigo == 0
        assert self.recibido["api_key"] == "sk-del-archivo"

    def test_sin_env_no_es_un_error(self, tmp_path, monkeypatch):
        """No tener ``.env`` no rompe: la credencial puede venir del entorno."""
        codigo = self._correr(["x.jpg", "-p", "deepseek"], tmp_path, monkeypatch)
        assert codigo == 0
        assert self.recibido["api_key"] is None

    def test_el_entorno_no_se_pisa(self, tmp_path, monkeypatch):
        """Lo exportado gana: es lo que permite probar otra clave sin editar el archivo."""
        monkeypatch.setenv("DEEPSEEK_API_KEY", "sk-de-la-terminal")
        (tmp_path / ".env").write_text(
            "DEEPSEEK_API_KEY=sk-del-archivo\n", encoding="utf-8"
        )
        self._correr(["x.jpg", "-p", "deepseek"], tmp_path, monkeypatch)
        assert self.recibido["api_key"] == "sk-de-la-terminal"

    def test_env_explicito_apunta_a_otro_archivo(self, tmp_path, monkeypatch):
        """``--env`` sigue sirviendo para un ``.env`` fuera del cwd."""
        otro = tmp_path / "otro.env"
        otro.write_text("DEEPSEEK_API_KEY=sk-de-otro\n", encoding="utf-8")
        casa = tmp_path / "casa"
        casa.mkdir()
        (casa / ".env").write_text("DEEPSEEK_API_KEY=sk-del-cwd\n", encoding="utf-8")
        self._correr(
            ["x.jpg", "-p", "deepseek", "--env", str(otro)], casa, monkeypatch
        )
        assert self.recibido["api_key"] == "sk-de-otro"

    def test_un_env_explicito_que_no_existe_si_es_un_error(self, tmp_path, monkeypatch):
        """⚠️ Ahí el operador **afirmó** que estaba: el default sí es opcional."""
        codigo = self._correr(
            ["x.jpg", "-p", "deepseek", "--env", "no-existe.env"],
            tmp_path,
            monkeypatch,
        )
        assert codigo == 2
        assert not self.recibido

    def test_declara_que_env_cargo(self, tmp_path, monkeypatch, capsys):
        """Un `.env` en el lugar equivocado no debe leerse como clave inválida."""
        (tmp_path / ".env").write_text(
            "DEEPSEEK_API_KEY=sk-del-archivo\n", encoding="utf-8"
        )
        self._correr(["x.jpg", "-p", "deepseek"], tmp_path, monkeypatch)
        assert "credencial: .env" in capsys.readouterr().out

    def test_declara_cuando_no_hay_env(self, tmp_path, monkeypatch, capsys):
        self._correr(["x.jpg", "-p", "deepseek"], tmp_path, monkeypatch)
        assert "no hay .env" in capsys.readouterr().out

    def test_la_clave_no_aparece_en_la_declaracion(self, tmp_path, monkeypatch, capsys):
        """La línea nueva nombra el archivo, nunca el valor."""
        (tmp_path / ".env").write_text(
            "DEEPSEEK_API_KEY=sk-secreta\n", encoding="utf-8"
        )
        self._correr(["x.jpg", "-p", "deepseek"], tmp_path, monkeypatch)
        capturado = capsys.readouterr()
        assert "sk-secreta" not in capturado.out
        assert "sk-secreta" not in capturado.err

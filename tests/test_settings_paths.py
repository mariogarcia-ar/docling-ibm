"""Los paths de datos: `var/` como único directorio de datos.

Antes cada herramienta inventaba su carpeta (`../files`, `procesadas/`,
`validaciones/`) y las rutas quedaban repartidas entre scripts. Ahora viven en
**un solo lugar** (`settings.paths`), y eso tiene tres consecuencias que estos
tests protegen:

1. **Un solo directorio que ignorar, respaldar o borrar**: todo cuelga de `var/`.
2. **Un default no puede ser un literal suelto**: si alguien escribe
   `Path("validaciones")` en un script, el cambio de layout vuelve a repartirse.
3. **La ruta se puede pisar sin tocar código**: por YAML o por variable de
   entorno, que es lo que hace falta para una corrida reproducible.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

from voucherflow.settings.config import (
    ENV_PREFIX,
    PathsSettings,
    Settings,
    _DEFAULTS,
    cargar_settings,
)

RAIZ_REPO = Path(__file__).resolve().parents[1]


class TestPathsPorDefecto:
    def test_todo_cuelga_de_var(self):
        p = PathsSettings()
        for cual in ("files", "processed", "validations"):
            assert getattr(p, cual).startswith("var/"), cual

    def test_las_tres_carpetas_son_distintas(self):
        # Mezclarlas haría que una corrida pisara la entrada, o que borrar la
        # salida se llevara el corpus.
        p = PathsSettings()
        rutas = {p.files, p.processed, p.validations}
        assert len(rutas) == 3, rutas

    def test_estan_declaradas_en_los_defaults(self):
        # Es lo que hace que `voucherflow.yaml` pueda pisarlas.
        assert set(_DEFAULTS["paths"]) == {"var", "files", "processed", "validations"}


class TestResolucion:
    def test_devuelve_la_ruta_relativa_sin_base(self):
        assert PathsSettings().resolver("files") == Path("var/files")

    def test_resuelve_contra_una_base(self):
        # Para una corrida desde otro directorio: la ruta se ancla explícita.
        resuelta = PathsSettings().resolver("validations", Path("/tmp/otro"))
        assert resuelta == Path("/tmp/otro/var/validations")

    def test_una_ruta_absoluta_ignora_la_base(self):
        # Si la configuración la fija absoluta, no se re-ancla: es lo que pide
        # una instalación real.
        p = PathsSettings(files="/datos/corpus")
        assert p.resolver("files", Path("/tmp")) == Path("/datos/corpus")

    def test_una_carpeta_desconocida_falla_con_las_validas(self):
        with pytest.raises(ValueError) as exc:
            PathsSettings().resolver("inventada")
        assert "inventada" in str(exc.value)
        assert "validations" in str(exc.value)

    def test_el_atajo_de_settings(self):
        assert Settings().carpeta("processed") == Path("var/processed")


class TestConfigurables:
    def test_se_pueden_pisar_desde_un_dict(self):
        s = cargar_settings()
        assert s.paths.files.startswith("var/")

    def test_se_pueden_pisar_por_variable_de_entorno(self, monkeypatch):
        """Una corrida reproducible necesita fijar las rutas sin editar código."""
        monkeypatch.setenv(f"{ENV_PREFIX}__PATHS__FILES", "/datos/corpus")
        assert cargar_settings().paths.files == "/datos/corpus"

    def test_la_de_entorno_gana_sobre_el_default(self, monkeypatch):
        monkeypatch.setenv(f"{ENV_PREFIX}__PATHS__VALIDATIONS", "/tmp/v")
        assert cargar_settings().paths.validations == "/tmp/v"


class TestEstructuraEnDisco:
    def test_var_esta_en_el_gitignore(self):
        # Es la razón de tener un solo directorio: que git lo ignore de una.
        ignorado = (RAIZ_REPO / ".gitignore").read_text(encoding="utf-8")
        assert re.search(r"^var/$", ignorado, re.M), "falta `var/` en .gitignore"

    def test_no_quedaron_los_paths_viejos_en_el_gitignore(self):
        # Si siguieran, alguien los usaría de nuevo por costumbre.
        ignorado = (RAIZ_REPO / ".gitignore").read_text(encoding="utf-8")
        for viejo in ("/procesadas/", "/procesados/", "/validaciones/"):
            assert viejo not in ignorado, f"{viejo} sigue en .gitignore"


class TestNadieInventaSuRuta:
    """Un default literal reparte las rutas otra vez."""

    #: Literales que ya no deberían usarse como **ruta**.
    #:
    #: ⚠️ `"procesados"` queda afuera a propósito: en `batch.py` es la **clave de
    #: una métrica** («cuántos documentos se procesaron»), no una carpeta.
    #: Confundir las dos cosas es fácil y el test lo distinguiría mal.
    VIEJOS = ('"procesadas"', "'procesadas'", '"procesados"', "'procesados'",
              '"validaciones"', "'validaciones'")

    #: Fragmentos que indican que el literal **no** es una ruta.
    NO_ES_RUTA = ("len(", "count", '": ', "traza", "self.procesados")

    def _archivos(self):
        for raiz in (RAIZ_REPO / "src", RAIZ_REPO / "scripts"):
            for archivo in raiz.rglob("*.py"):
                if "__pycache__" in str(archivo):
                    continue
                yield archivo

    def test_ningun_modulo_usa_un_literal_de_carpeta(self):
        culpables: list[str] = []
        for archivo in self._archivos():
            texto = archivo.read_text(encoding="utf-8")
            for linea in texto.splitlines():
                # Se permite nombrar el path viejo en un comentario o docstring
                # (documentar la migración), pero no usarlo como valor.
                codigo = linea.split("#")[0]
                if any(v in codigo for v in self.VIEJOS) and not any(
                    marca in codigo for marca in self.NO_ES_RUTA
                ):
                    culpables.append(f"{archivo.relative_to(RAIZ_REPO)}: {linea.strip()[:70]}")
        assert not culpables, (
            "hay rutas de datos hardcodeadas; deberían salir de `settings.paths`:\n"
            + "\n".join(culpables)
        )

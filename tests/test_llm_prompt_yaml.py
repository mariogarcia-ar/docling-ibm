"""El prompt efectivo en YAML y el documento que lo explica.

La separación entre el **prompt** (`.yaml`, lo que se manda) y la **explicación**
(`.md`, lo que se lee) tiene un riesgo obvio: que los dos se desincronicen y el
documento describa un prompt que ya no es el que corre.

Estos tests fijan lo que hace que la separación sea segura:

1. El YAML es la **fuente** y produce exactamente el mismo prompt que antes.
2. El `.md` **referencia** al YAML (no lo duplica): si alguien copia las reglas
   ahí, quedan dos versiones y una miente.
3. `cargar_prompt` acepta los dos formatos: el prompt se puede mover sin tocar el
   código.
4. El `ejemplo_salida` se vuelve a unir al `user` con la separación original, así
   el texto que llega al modelo no cambia por haber separado el archivo.
"""

from __future__ import annotations

from pathlib import Path

import pytest
import yaml

from voucherflow.llm.prompts import (
    _RE_BLOQUE_MD,
    _separar_template,
    armar_prompt_efectivo,
    cargar_prompt,
    hash_prompt,
)

DIR = Path(__file__).resolve().parents[1] / "src" / "voucherflow" / "llm" / "prompts"
YAML_RUTA = DIR / "validacion-mendel.yaml"
MD_RUTA = DIR / "validacion-mendel.md"


@pytest.fixture(scope="module")
def datos_yaml() -> dict:
    return yaml.safe_load(YAML_RUTA.read_text(encoding="utf-8"))


# ---------------------------------------------------------------------------
# La fuente: el YAML
# ---------------------------------------------------------------------------


class TestElYamlEsLaFuente:
    def test_tiene_las_claves_del_prompt(self, datos_yaml):
        assert set(datos_yaml) >= {"system", "user"}

    def test_usa_las_mismas_claves_que_los_demas_prompts(self, datos_yaml):
        # Los prompts de `prompts/` usan `system` y `user`: seguir la convención
        # evita que cada prompt se lea distinto.
        otros = Path(__file__).resolve().parents[1] / "prompts"
        if (otros / "01-clasificacion_centro_costo_prompt.yaml").is_file():
            ejemplo = yaml.safe_load(
                (otros / "01-clasificacion_centro_costo_prompt.yaml").read_text(encoding="utf-8")
            )
            assert set(ejemplo) <= set(datos_yaml) | {"ejemplo_salida"}

    def test_el_system_tiene_las_15_reglas(self, datos_yaml):
        # Las reglas se numeran: si alguien borra una, la enumeración lo delata.
        sistema = datos_yaml["system"]
        for n in range(1, 16):
            assert f"{n}." in sistema, f"falta la regla {n}"

    def test_el_system_declara_el_estado_global(self, datos_yaml):
        for estado in ("OK", "REVISAR", "INCOMPLETO"):
            assert estado in datos_yaml["system"]

    def test_el_user_trae_el_template_de_entrada(self, datos_yaml):
        # El template es lo que se sustituye: importan los NOMBRES de las claves.
        user = datos_yaml["user"]
        assert "[IMAGEN]" in user
        assert "tipo_comprobante" in user
        assert "importe_total_facturado" in user

    def test_el_ejemplo_de_salida_tiene_las_claves_de_la_respuesta(self, datos_yaml):
        ejemplo = datos_yaml["ejemplo_salida"]
        for clave in ("estado_global", "campos", "discrepancias_criticas", "campos_no_legibles"):
            assert clave in ejemplo, clave

    def test_el_yaml_explica_en_que_zonas_no_tocar(self, datos_yaml):
        # Los comentarios del encabezado son la guía de quien lo edita.
        crudo = YAML_RUTA.read_text(encoding="utf-8")
        assert "REEMPLAZA en runtime" in crudo
        assert "validacion-mendel.md" in crudo


# ---------------------------------------------------------------------------
# El documento: el md
# ---------------------------------------------------------------------------


class TestElDocumentoExplica:
    def test_existe_y_referencia_al_yaml(self):
        assert MD_RUTA.is_file()
        assert "validacion-mendel.yaml" in MD_RUTA.read_text(encoding="utf-8")

    def test_no_duplica_el_prompt(self):
        """⚠️ Si el `.md` copiara las reglas, habría dos versiones y una mentiría.

        El documento puede **nombrar** las reglas (una tabla que las resume) pero
        no puede contener el prompt: el único lugar donde vive es el YAML.
        """
        texto = MD_RUTA.read_text(encoding="utf-8")
        bloques = _RE_BLOQUE_MD.findall(texto)
        # La única cerca de código permitida son los ejemplos de invocación (shell).
        for bloque in bloques:
            assert "Sos un auditor experto" not in bloque, "el md copió el SYSTEM"
            assert '"estado_global"' not in bloque, "el md copió el ejemplo de salida"

    def test_explica_que_el_template_de_entrada_no_se_edita(self):
        # Es la trampa principal: editarlo no cambia nada.
        texto = MD_RUTA.read_text(encoding="utf-8")
        assert "template" in texto.lower()
        assert "no cambia nada" in texto

    def test_documenta_cuando_se_usa_el_ejemplo_de_salida(self):
        texto = MD_RUTA.read_text(encoding="utf-8")
        assert "strict" in texto  # el proveedor que impone la forma
        assert "extraer" in texto  # donde se reemplaza por el generado

    def test_resume_las_reglas_sin_copiarlas_enteras(self):
        texto = MD_RUTA.read_text(encoding="utf-8")
        assert "15 reglas" in texto
        # Y nombra las decisiones que más confunden.
        assert "090" in texto and "monto no gravado" in texto.lower()

    def test_menciona_el_evaluador_deterministico(self):
        # El documento explica que el diff se hace en código, que es lo que el
        # propio prompt recomienda.
        assert "evaluador" in MD_RUTA.read_text(encoding="utf-8")


# ---------------------------------------------------------------------------
# Los dos formatos se cargan igual
# ---------------------------------------------------------------------------


class TestCargarLosDosFormatos:
    def test_el_yaml_se_carga(self):
        sistema, user = cargar_prompt(YAML_RUTA)
        assert sistema and user
        assert "auditor experto" in sistema

    def test_el_md_con_bloques_sigue_funcionando(self, tmp_path):
        """El formato `.md` no se retiró: un prompt se puede escribir así si hace
        falta (es el formato en el que se redacta y se revisa)."""
        md = tmp_path / "prompt.md"
        md.write_text(
            "# Doc\n\n## SYSTEM PROMPT\n\n```\nSISTEMA\n```\n\n"
            "## USER PROMPT\n\n```\nUSUARIO\n```\n",
            encoding="utf-8",
        )
        assert cargar_prompt(md) == ("SISTEMA", "USUARIO")

    def test_un_md_sin_bloques_falla_con_un_mensaje_util(self, tmp_path):
        md = tmp_path / "vacio.md"
        md.write_text("# Solo prosa\n", encoding="utf-8")
        with pytest.raises(ValueError, match="bloques"):
            cargar_prompt(md)

    def test_un_yaml_sin_las_claves_falla(self, tmp_path):
        y = tmp_path / "malo.yaml"
        y.write_text("solo_system: |\n  x\n", encoding="utf-8")
        with pytest.raises(ValueError, match="system"):
            cargar_prompt(y)

    def test_un_archivo_inexistente_falla(self, tmp_path):
        with pytest.raises(FileNotFoundError):
            cargar_prompt(tmp_path / "no-existe.yaml")


class TestElYamlReconstruyeElTemplate:
    def test_el_ejemplo_se_reune_al_user(self, datos_yaml):
        """Separarlos es una decisión de diseño (el código los trata distinto),
        pero al leerlos vuelven a ser un solo template."""
        _, user = cargar_prompt(YAML_RUTA)
        assert datos_yaml["user"].rstrip("\n") in user
        assert datos_yaml["ejemplo_salida"].rstrip("\n") in user

    def test_la_separacion_es_la_misma_del_original(self, datos_yaml):
        # Dos saltos: el template y el ejemplo se separan con una línea en blanco.
        _, user = cargar_prompt(YAML_RUTA)
        cabecera, ejemplo = _separar_template(user)
        assert cabecera == datos_yaml["user"].rstrip("\n")
        assert ejemplo == datos_yaml["ejemplo_salida"].rstrip("\n")

    def test_sin_ejemplo_el_user_queda_solo(self, tmp_path):
        # Un prompt sin ejemplo de salida es válido (OpenAI lo impone).
        y = tmp_path / "sin_ejemplo.yaml"
        y.write_text('system: |\n  S\nuser: |\n  U\n', encoding="utf-8")
        assert cargar_prompt(y) == ("S", "U")

    def test_el_prompt_efectivo_no_cambia_con_el_formato(self):
        """El hash del prompt efectivo es el registro de auditoría.

        Separar el archivo no puede cambiarlo: si cambiara, las corridas
        anteriores quedarían registradas con un prompt que ya no existe.
        """
        sistema, user = cargar_prompt(YAML_RUTA)
        para_validar = armar_prompt_efectivo(
            "validar", sistema, user, {"tipo_comprobante": "A"}, incluir_ejemplo=False
        )
        para_extraer = armar_prompt_efectivo(
            "extraer", sistema, user, None, incluir_ejemplo=False
        )
        # Los hashes quedan fijados: si el prompt cambia, hay que **querer**
        # cambiarlos (y re-medir las corridas anteriores).
        #
        # ⚠️ Actualizados al agregar "INTERNACIONAL" a la regla 1 (2026-09-15):
        # los comprobantes de proveedores de otro país no tenían cómo declararse
        # y el modelo improvisaba (`null`, texto libre, o "E"). Las corridas
        # anteriores al cambio quedan registradas con el hash viejo — es lo que
        # el registro de auditoría debe poder distinguir.
        assert hash_prompt(*para_validar) == "sha256:60b92df8beb84472"
        assert hash_prompt(*para_extraer) == "sha256:0d7ffc3dc25de2fe"

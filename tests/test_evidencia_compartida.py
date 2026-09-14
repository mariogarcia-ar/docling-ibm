"""Contrato de evidencia compartido (``voucherflow.evidencia``).

⚠️ **Este archivo existe porque seis piezas estaban duplicadas** entre
``classification.evidencia`` y ``extraction.evidencia`` (F2/F3 y F4, construidas
por separado). La duplicación era casi perfecta: lo único que las distinguía era
una etiqueta de tarea en los mensajes de error (``T-302`` vs ``T-401``) y un
sustantivo (``lectura``/``extracción``).

El comportamiento de cada pieza ya se ejercita **a través de los flujos**
(``test_classification_prompt_tipo.py``, ``test_extraction_flujos.py``). Lo que se
fija acá es la **propiedad del refactor**: que las dos fases usen la misma
implementación y no vuelvan a divergir. Una corrección aplicada en una sola copia
—un caso de parseo, una validación de fuente— dejaba a la otra fase con el
comportamiento viejo sin que nada fallara; `test_las_dos_fases_usan_lo_mismo` es
lo que impide que eso vuelva a pasar en silencio.
"""

from __future__ import annotations

import pytest

from voucherflow.evidencia import (
    CAMPO_FUENTE_DECLARADA,
    MOTIVO_FUENTE_DECLARADA_DISTINTA,
    ErrorEvidencia,
    fuente_declarada_del_modelo,
    fuentes_con_insumo,
    parsear_json_de_respuesta,
    resolver_modelo,
)

FUENTES = ("vlm", "llm")


class TestParseoDeRespuesta:
    """Tolerancia con las formas reales que devuelven los modelos locales."""

    def test_json_puro(self):
        assert parsear_json_de_respuesta('{"a": 1}') == {"a": 1}

    def test_json_en_cerca_markdown(self):
        assert parsear_json_de_respuesta('```json\n{"a": 1}\n```') == {"a": 1}

    def test_cerca_sin_la_palabra_json(self):
        assert parsear_json_de_respuesta('```\n{"a": 1}\n```') == {"a": 1}

    def test_json_con_prosa_alrededor(self):
        """El último recurso: ``raw_decode`` sobre el primer ``{``."""
        texto = 'Claro, acá va el resultado:\n{"a": 1}\ny eso es todo.'
        assert parsear_json_de_respuesta(texto) == {"a": 1}

    def test_elige_el_primer_objeto_completo(self):
        """Con dos objetos, gana el primero (no se concatena nada)."""
        assert parsear_json_de_respuesta('{"a": 1} {"b": 2}') == {"a": 1}

    def test_una_respuesta_vacia_es_error(self):
        with pytest.raises(ErrorEvidencia, match="respuesta vacía"):
            parsear_json_de_respuesta("")

    def test_solo_espacios_es_error(self):
        with pytest.raises(ErrorEvidencia, match="respuesta vacía"):
            parsear_json_de_respuesta("   \n  ")

    def test_texto_sin_json_es_error(self):
        with pytest.raises(ErrorEvidencia):
            parsear_json_de_respuesta("no hay json acá")

    def test_incluye_el_inicio_de_la_respuesta_para_diagnosticar(self):
        """El diagnóstico necesita ver qué devolvió el modelo."""
        with pytest.raises(ErrorEvidencia, match="primeros 200 caracteres"):
            parsear_json_de_respuesta("basura " * 10)

    def test_el_mensaje_nombra_lo_que_se_esperaba(self):
        """``que`` permite reusar el parseo en más de una fase sin mentir."""
        with pytest.raises(ErrorEvidencia, match="la evidencia de extracción"):
            parsear_json_de_respuesta("", que="la evidencia de extracción")

    def test_un_json_no_objeto_se_devuelve_igual(self):
        """El parseo no valida la forma: eso es del contrato de cada fase."""
        assert parsear_json_de_respuesta("[1, 2, 3]") == [1, 2, 3]

    def test_un_json_roto_con_llave_inicial_es_error(self):
        with pytest.raises(ErrorEvidencia):
            parsear_json_de_respuesta('{\n "campos": {"x": "20-1"\n "v"\n}')


class TestFuentesConInsumo:
    """Separa las fuentes que tienen material de las que no."""

    class _Vista:
        def __init__(self, ruta=None):
            self.ruta_imagen_original = ruta

    def test_llm_necesita_markdown(self):
        con, sin = fuentes_con_insumo(
            FUENTES, markdown="# factura", vista=self._Vista("foto.jpg"), validas=FUENTES
        )
        assert (con, sin) == (["vlm", "llm"], [])

    def test_vlm_necesita_la_ruta_de_la_imagen(self):
        con, sin = fuentes_con_insumo(
            FUENTES, markdown="# factura", vista=self._Vista(), validas=FUENTES
        )
        assert (con, sin) == (["llm"], ["vlm"])

    def test_markdown_vacio_no_cuenta_como_insumo(self):
        con, sin = fuentes_con_insumo(
            FUENTES, markdown="   ", vista=self._Vista("foto.jpg"), validas=FUENTES
        )
        assert (con, sin) == (["vlm"], ["llm"])

    def test_sin_insumo_ninguno_no_es_un_error(self):
        """Un documento sin material es un caso válido, no una falla."""
        con, sin = fuentes_con_insumo(
            FUENTES, markdown=None, vista=self._Vista(), validas=FUENTES
        )
        assert (con, sin) == ([], ["vlm", "llm"])

    def test_una_fuente_invalida_es_un_error_de_programacion(self):
        with pytest.raises(ValueError, match="fuente inválida"):
            fuentes_con_insumo(("ocr",), markdown="x", vista=self._Vista(), validas=FUENTES)

    def test_el_mensaje_lista_las_validas(self):
        with pytest.raises(ValueError, match="vlm"):
            fuentes_con_insumo(("ocr",), markdown="x", vista=self._Vista(), validas=FUENTES)

    def test_respeta_las_validas_que_le_pasa_la_fase(self):
        """Cada fase trae su constante: el módulo no impone una lista propia."""
        with pytest.raises(ValueError, match="fuente inválida"):
            fuentes_con_insumo(("vlm",), markdown="x", vista=self._Vista(), validas=("ocr",))


class TestResolverModelo:
    """El rol de configuración depende de la fuente."""

    class _Rol:
        def __init__(self, modelo, num_ctx):
            self.modelo = modelo
            self.num_ctx = num_ctx

    class _Settings:
        def __init__(self, roles):
            self._roles = roles

        def modelo_para(self, fuente):
            return self._roles.get(fuente)

    def test_usa_el_rol_de_la_fuente(self):
        s = self._Settings({"vlm": self._Rol("qwen2.5vl:3b", 4096)})
        assert resolver_modelo("vlm", None, s) == ("qwen2.5vl:3b", 4096)

    def test_un_modelo_explicito_gana_y_no_trae_num_ctx(self):
        """Lo fija el llamador, que es responsable de su ventana de contexto."""
        s = self._Settings({"vlm": self._Rol("configurado", 4096)})
        assert resolver_modelo("vlm", "elegido-a-mano", s) == ("elegido-a-mano", None)

    def test_sin_rol_configurado_es_un_error_claro(self):
        s = self._Settings({})
        with pytest.raises(ValueError, match="No hay modelo configurado"):
            resolver_modelo("vlm", None, s)

    def test_un_rol_sin_modelo_tambien_es_error(self):
        s = self._Settings({"vlm": self._Rol("", 4096)})
        with pytest.raises(ValueError, match="No hay modelo configurado"):
            resolver_modelo("vlm", None, s)


class TestFuenteDeclarada:
    """El modelo puede declarar su fuente, pero no decide la trazabilidad."""

    def test_sin_declaracion_no_hay_problema(self):
        assert fuente_declarada_del_modelo({}, fuente="vlm") == (None, None)

    def test_una_declaracion_coincidente_no_es_problema(self):
        datos = {CAMPO_FUENTE_DECLARADA: "vlm"}
        assert fuente_declarada_del_modelo(datos, fuente="vlm") == ("vlm", None)

    def test_se_normaliza_a_minusculas(self):
        datos = {CAMPO_FUENTE_DECLARADA: "  VLM  "}
        assert fuente_declarada_del_modelo(datos, fuente="vlm") == ("vlm", None)

    def test_una_declaracion_distinta_es_un_problema(self):
        """Dato menor, pero auditable: se conserva la fuente REAL."""
        datos = {CAMPO_FUENTE_DECLARADA: "llm"}
        declarada, problema = fuente_declarada_del_modelo(datos, fuente="vlm")
        assert declarada == "llm"
        assert problema is not None
        assert "'llm'" in problema and "'vlm'" in problema

    def test_un_valor_no_textual_se_ignora(self):
        datos = {CAMPO_FUENTE_DECLARADA: 42}
        assert fuente_declarada_del_modelo(datos, fuente="vlm") == (None, None)

    def test_el_motivo_es_el_declarado_como_constante(self):
        datos = {CAMPO_FUENTE_DECLARADA: "llm"}
        _, problema = fuente_declarada_del_modelo(datos, fuente="vlm")
        assert problema == MOTIVO_FUENTE_DECLARADA_DISTINTA.format(
            declarada="llm", real="vlm"
        )


class TestLasDosFasesUsanLoMismo:
    """⚠️ La propiedad del refactor: una sola implementación, no dos copias.

    Si alguien vuelve a copiar una de estas piezas en una fase, este test lo
    delata antes de que las dos versiones diverjan.
    """

    def test_las_dos_fases_importan_las_piezas_compartidas(self):
        import voucherflow.classification.evidencia as clasif
        import voucherflow.evidencia as compartido
        import voucherflow.extraction.evidencia as extraccion

        for modulo in (clasif, extraccion):
            assert modulo.ErrorEvidencia is compartido.ErrorEvidencia
            assert modulo.Lector is compartido.Lector
            assert modulo._parsear_json is compartido.parsear_json_de_respuesta
            assert modulo._fuentes_con_insumo is compartido.fuentes_con_insumo
            assert modulo._resolver_modelo is compartido.resolver_modelo
            assert (
                modulo.fuente_declarada_del_modelo
                is compartido.fuente_declarada_del_modelo
            )

    def test_los_motivos_compartidos_son_el_mismo_objeto(self):
        import voucherflow.classification.evidencia as clasif
        import voucherflow.evidencia as compartido
        import voucherflow.extraction.evidencia as extraccion

        for modulo in (clasif, extraccion):
            assert (
                modulo.MOTIVO_FUENTE_DECLARADA_DISTINTA
                is compartido.MOTIVO_FUENTE_DECLARADA_DISTINTA
            )

    def test_ninguna_fase_redefine_las_piezas(self):
        """Ninguna fase tiene su propia copia del parseo ni del protocolo."""
        from pathlib import Path

        raiz = Path(__file__).resolve().parents[1] / "src" / "voucherflow"
        redefinidas = []
        for fase in ("classification/evidencia.py", "extraction/evidencia.py"):
            texto = (raiz / fase).read_text(encoding="utf-8")
            for marca in (
                "def _parsear_json(",
                "def _fuentes_con_insumo(",
                "def _resolver_modelo(",
                "class ErrorEvidencia(",
                "class Lector(",
            ):
                if marca in texto:
                    redefinidas.append(f"{fase}: {marca}")
        assert not redefinidas, (
            f"estas piezas volvieron a definirse por fase: {redefinidas} "
            "(usar `voucherflow.evidencia`)"
        )

    def test_los_mensajes_ya_no_llevan_etiqueta_de_tarea(self):
        """La etiqueta T-302/T-401 describía *cuándo* se escribió, no *qué* falló.

        Se mira el **código** de la función, no el docstring del módulo: ahí la
        etiqueta se nombra a propósito, para explicar qué duplicación se resolvió.
        """
        import ast
        import inspect
        import textwrap

        import voucherflow.evidencia as compartido

        for nombre in ("parsear_json_de_respuesta", "fuentes_con_insumo", "resolver_modelo"):
            funcion = getattr(compartido, nombre)
            arbol = ast.parse(textwrap.dedent(inspect.getsource(funcion)))
            cuerpo = "\n".join(
                ast.unparse(n) for n in arbol.body[0].body
                if not (
                    isinstance(n, ast.Expr)
                    and isinstance(n.value, ast.Constant)
                    and isinstance(n.value.value, str)
                )
            )
            assert "T-302" not in cuerpo, f"{nombre} conserva la etiqueta T-302"
            assert "T-401" not in cuerpo, f"{nombre} conserva la etiqueta T-401"

"""Paridad de la superficie del CLI v1 → v2 (F6 / T-604).

**DoD de T-604** (F6.md §3, E-CLI): "Los comandos de v1 tienen equivalente en v2
con resultados comparables (o mejores) sobre una muestra acordada de `files/`
(DoD de F6); aquí se declara el corte de v1."

Qué verifica, y por qué en tres niveles
---------------------------------------
La paridad de F6 **no** se puede medir como la de F3/F4 (dos salidas del mismo
modelo): ahí se comparaba el mismo modelo leyendo el mismo documento, acá v1 y v2
tienen **arquitecturas distintas a propósito** (ADR-001/ADR-006: v1 le pedía al
modelo el dato normalizado y decidido; v2 le pide la lectura y decide en código).
Comparar el veredicto documento a documento mezclaría una mejora de diseño con una
regresión, así que la paridad se mide en lo que sí tiene sustento objetivo:

1. **La procedencia** (``TestProcedencia``): cada comando del mapa cita el script
   de v1 que reemplaza, y ese script **existe**. Un mapa que cita archivos
   inexistentes declara procedencias falsas.
2. **La superficie de invocación** (``TestSuperficie``): cada comando de v1 tiene
   su equivalente en v2, y **cada bandera de v1 está declarada**: o bien con su
   bandera equivalente en v2 (que se verifica contra el parser real) o bien como
   *sin equivalente* con su motivo. Es lo que hace verificable la frase "los
   comandos de v1 tienen equivalente en v2".
3. **Los artefactos** (``TestArtefactos``): los nombres de archivo que coinciden se
   verifican como coincidentes, y los que cambian **tienen motivo**. Se verifica
   además la coherencia del propio mapa (no se puede declarar coincidencia donde no
   la hay).

Fronteras (``TestFronteras``): el mapa declara lo que queda **fuera de paridad** y
dónde se mide, y el corte de v1 es explícito. La corrida real con modelos no corre
acá (requiere Ollama y `files/`, que no está versionada): vive en
``scripts/F6/paridad_cli.py --real`` y se declara como informativa.

Reglas duras: la suite default corre **sin** Ollama, **sin** Docling y **sin** red;
el módulo de paridad se carga por ``importlib`` sin ejecutar su ``main`` (misma
convención que F3/T-305 y F4/T-405).
"""

from __future__ import annotations

import importlib.util
import json
from pathlib import Path
from typing import Any

import pytest

#: Raíz de v2 (``v2/tests/test_paridad_t604.py`` → ``parents[1]``).
RAIZ_V2 = Path(__file__).resolve().parents[1]
RAIZ_REPO = RAIZ_V2.parent

SUBCONJUNTO = RAIZ_V2 / "tests" / "golden" / "F6" / "subconjunto.json"
SCRIPT_PARIDAD = RAIZ_V2 / "scripts" / "F6" / "paridad_cli.py"


@pytest.fixture(scope="module")
def subconjunto() -> dict[str, Any]:
    with SUBCONJUNTO.open(encoding="utf-8") as archivo:
        return json.load(archivo)


@pytest.fixture(scope="module")
def paridad() -> Any:
    """El módulo de paridad, cargado **sin** ejecutar su ``main``."""
    especificacion = importlib.util.spec_from_file_location("paridad_cli", SCRIPT_PARIDAD)
    assert especificacion is not None and especificacion.loader is not None
    modulo = importlib.util.module_from_spec(especificacion)
    especificacion.loader.exec_module(modulo)
    return modulo


@pytest.fixture(scope="module")
def mapa(paridad: Any) -> dict[str, Any]:
    return paridad.mapa_de_paridad()


# ---------------------------------------------------------------------------
# 1. Procedencia
# ---------------------------------------------------------------------------


class TestProcedencia:
    def test_el_subconjunto_declara_su_alcance(self, subconjunto: dict[str, Any]):
        # Sin la decisión de alcance, el mapa parece una comparación de salidas.
        assert subconjunto["decision_alcance"]
        assert "ADR-001" in subconjunto["decision_alcance"]

    def test_la_muestra_esta_declarada_y_existe(self, subconjunto: dict[str, Any]):
        muestra = subconjunto["muestra"]["carpeta"]
        # La muestra acordada es la copia versionada del golden: `files/` no está
        # en git, así que una medición que dependa de ella no es reproducible.
        assert (RAIZ_REPO / muestra).is_dir()
        assert "files/" not in muestra

    def test_todos_los_scripts_de_v1_citados_existen(self, mapa: dict[str, Any]):
        assert mapa["referencias_v1"]["ok"], mapa["referencias_v1"]["faltantes"]

    def test_cada_comando_cita_su_script_de_v1(self, subconjunto: dict[str, Any]):
        for comando in subconjunto["comandos"]:
            ruta = RAIZ_REPO / comando["v1"]
            assert ruta.is_file(), f"{comando['id']}: no existe {comando['v1']}"


# ---------------------------------------------------------------------------
# 2. Superficie de invocación
# ---------------------------------------------------------------------------


class TestSuperficie:
    def test_todos_los_comandos_tienen_equivalente(self, mapa: dict[str, Any]):
        resumen = mapa["resumen"]
        assert resumen["comandos_con_equivalente"] == resumen["comandos_declarados"]
        assert resumen["comandos_declarados"] >= 8

    def test_todas_las_banderas_equivalentes_existen_en_v2(self, mapa: dict[str, Any]):
        # Es el chequeo que importa: una bandera declarada equivalente y ausente en
        # v2 es una brecha real (el operador no puede hacer lo mismo).
        for comando in mapa["comandos"]:
            assert not comando["flags_faltantes_en_v2"], (
                f"{comando['id']}: banderas declaradas equivalentes que v2 no acepta: "
                f"{comando['flags_faltantes_en_v2']}"
            )

    def test_toda_bandera_de_v1_esta_declarada(self, mapa: dict[str, Any]):
        # Ni mapeada ni declarada sin equivalente = el mapa está incompleto, y una
        # bandera olvidada se lee como "no existía".
        for comando in mapa["comandos"]:
            assert not comando["flags_no_declaradas"], (
                f"{comando['id']}: banderas sin declarar: {comando['flags_no_declaradas']}"
            )

    def test_lo_que_no_tiene_equivalente_tiene_motivo(self, subconjunto: dict[str, Any]):
        for comando in subconjunto["comandos"]:
            if comando["flags_v1_sin_equivalente"]:
                assert comando.get("nota"), (
                    f"{comando['id']} declara banderas sin equivalente y no explica por qué"
                )

    def test_el_estado_global_es_ok(self, mapa: dict[str, Any]):
        assert mapa["ok"], "el mapa de paridad no está en verde"

    def test_la_inspeccion_es_del_parser_real(self, paridad: Any):
        # No es una lista escrita a mano: se lee el parser del CLI. Si alguien
        # renombra un subcomando, esto lo ve.
        subcomandos = paridad.subcomandos_v2()
        assert "process" in subcomandos
        assert "batch" in subcomandos
        assert "case" in subcomandos

    def test_detecta_un_comando_inexistente(self, paridad: Any):
        # Control negativo: el chequeo tiene que poder fallar.
        resultado = paridad.verificar_comando(
            {"id": "x", "v1": "v1/x.py", "v2": "comando-que-no-existe", "flags_v1": []}
        )
        assert resultado["ok"] is False
        assert resultado["nombres_v2_inexistentes"] == ["comando-que-no-existe"]

    def test_detecta_una_bandera_equivalente_ausente(self, paridad: Any):
        # Control negativo del chequeo de superficie.
        resultado = paridad.verificar_comando(
            {
                "id": "x",
                "v1": "v1/x.py",
                "v2": "process",
                "mapa_flags_v1_v2": {"--inventada": "--flag-que-no-existe"},
                "flags_v1_sin_equivalente": [],
                "flags_v1": ["--inventada"],
                "nota": "porque sí",
            }
        )
        assert resultado["ok"] is False
        assert resultado["flags_faltantes_en_v2"] == ["--inventada→--flag-que-no-existe"]


# ---------------------------------------------------------------------------
# 3. Artefactos
# ---------------------------------------------------------------------------


class TestArtefactos:
    def test_todos_los_artefactos_son_coherentes(self, mapa: dict[str, Any]):
        for artefacto in mapa["artefactos"]:
            assert artefacto["ok"], f"{artefacto['id']}: {artefacto['problemas']}"

    def test_hay_coincidencias_reales_con_v1(self, mapa: dict[str, Any]):
        # Un mapa donde TODO cambia no mide paridad: mide una reescritura.
        assert mapa["resumen"]["artefactos_coincidentes_con_v1"] >= 3

    def test_las_diferencias_estan_declaradas(self, subconjunto: dict[str, Any]):
        for artefacto in subconjunto["artefactos"]:
            if not artefacto["coincide"]:
                assert artefacto.get("nota"), (
                    f"{artefacto['id']}: diferencia sin motivo declarado"
                )

    def test_un_artefacto_sin_contraparte_se_declara_mejora(self, subconjunto: dict[str, Any]):
        for artefacto in subconjunto["artefactos"]:
            if str(artefacto["patron_v1"]).startswith("("):
                assert artefacto.get("mejor_que_v1"), (
                    f"{artefacto['id']}: sin contraparte en v1 y no se declara mejora"
                )

    def test_detecta_una_coincidencia_falsa(self, paridad: Any):
        # Control negativo: declarar "coincide" con patrones distintos es el peor
        # error posible en este mapa (afirmaría una paridad que no existe).
        resultado = paridad.verificar_artefacto(
            {
                "id": "x",
                "coincide": True,
                "patron_v1": "<doc>.uno",
                "patron_v2": "<doc>.otro",
            }
        )
        assert resultado["ok"] is False
        assert any("patrones difieren" in p for p in resultado["problemas"])

    def test_detecta_una_diferencia_sin_motivo(self, paridad: Any):
        resultado = paridad.verificar_artefacto(
            {"id": "x", "coincide": False, "patron_v1": "a", "patron_v2": "b"}
        )
        assert resultado["ok"] is False
        assert any("sin motivo" in p for p in resultado["problemas"])


# ---------------------------------------------------------------------------
# 4. Artefactos verificables de verdad (el nombre, no la declaración)
# ---------------------------------------------------------------------------


class TestArtefactosReales:
    """Los artefactos que el mapa declara **coincidentes** se comprueban en vivo.

    Hasta acá se verificó la coherencia del mapa; estos tests comprueban que el
    nombre declarado sea el que el pipeline realmente escribe. Sin esto, el mapa
    podría declarar ``<doc>.raw.md`` y el código escribir otra cosa.
    """

    def test_el_checkpoint_contable_coincide_con_v1(self):
        # v1: f"{document_path.stem}_classification.json". Se verifica contra el
        # nombre real que produce la F3 (no contra el mapa).
        from voucherflow.classification.contable import ruta_checkpoint

        assert ruta_checkpoint("/x/factura.pdf").name == "factura_classification.json"

    def test_el_markdown_por_posicion_coincide_con_v1(self, tmp_path: Path):
        from voucherflow.cli.main import _destino_markdown, EntornoCLI
        import io

        entorno = EntornoCLI(stdout=io.StringIO(), stderr=io.StringIO(), cwd=tmp_path)
        destino = _destino_markdown(tmp_path / "factura.pdf", None, entorno)
        assert destino.name == "factura.md"

    def test_el_crudo_usa_la_convencion_raw_md_de_v1(self, tmp_path: Path):
        # v1/run_raw.py: input_path.with_name(f"{stem}.raw.md"). El crudo y el
        # markdown ordenado son dos artefactos distintos: con el mismo nombre, la
        # segunda corrida pisaría a la primera (y al crudo sobre el .md).
        import io

        from voucherflow.cli.main import _destino_markdown, EntornoCLI

        entorno = EntornoCLI(stdout=io.StringIO(), stderr=io.StringIO(), cwd=tmp_path)
        crudo = _destino_markdown(tmp_path / "factura.pdf", None, entorno, raw=True)
        assert crudo.name == "factura.raw.md"
        ordenado = _destino_markdown(tmp_path / "factura.pdf", None, entorno)
        assert crudo != ordenado

    def test_process_raw_no_pisa_el_documento_de_entrada(self, tmp_path: Path):
        """El peor desenlace posible: que ``--raw`` sobrescriba el origen.

        Se ejercita el comando de verdad (con dobles) y se verifica que la entrada
        siga intacta y que el crudo quede en su propio archivo.
        """
        import io

        from voucherflow.cli.main import EntornoCLI, main

        documento = tmp_path / "factura.md"
        original = "FACTURA A\ncontenido original\n"
        documento.write_text(original, encoding="utf-8")

        from test_cli_t601 import _orquestador  # reutiliza los dobles de T-601

        entorno = EntornoCLI(
            orquestador=_orquestador(), stdout=io.StringIO(), stderr=io.StringIO(), cwd=tmp_path
        )
        assert main(["process", str(documento), "--raw"], entorno=entorno) == 0

        assert documento.read_text(encoding="utf-8") == original, "pisó la entrada"
        assert (tmp_path / "factura.raw.md").is_file(), "no escribió el crudo"

    def test_process_no_pisa_una_entrada_de_texto_plano(self, tmp_path: Path):
        """Riesgo que introdujo v2: un ``.md`` de entrada tiene el mismo sufijo que la salida.

        v1 no corría ``process`` sobre texto plano (su lista de extensiones era solo
        imágenes y PDF), así que la colisión de nombre es nueva. Si ``process``
        escribiera en ``<doc>.md`` con un ``.md`` de entrada, **destruiría el
        documento original** — silenciosamente y en la primera corrida. El destino se
        tiene que apartar (``<doc>.processed.md``).
        """
        import io

        from voucherflow.cli.main import EntornoCLI, main

        # (entrada, salida esperada). Solo el `.md` COLISIONA (mismo sufijo que la
        # salida); el `.txt` produce `nota.md`, que es un nombre distinto y no hace
        # falta apartarlo. El riesgo a cubrir es la colisión, no el sufijo en sí.
        for nombre, salida in (("nota.md", "nota.processed.md"), ("nota.txt", "nota.md")):
            carpeta = tmp_path / nombre.replace(".", "_")
            carpeta.mkdir(parents=True, exist_ok=True)
            documento = carpeta / nombre
            original = "contenido que no se puede perder\n"
            documento.write_text(original, encoding="utf-8")

            from test_cli_t601 import _orquestador

            entorno = EntornoCLI(
                orquestador=_orquestador(),
                stdout=io.StringIO(),
                stderr=io.StringIO(),
                cwd=carpeta,
            )
            codigo = main(["process", str(documento)], entorno=entorno)

            assert codigo == 0
            assert documento.read_text(encoding="utf-8") == original, (
                f"{nombre}: la corrida pisó el documento de entrada"
            )
            assert (carpeta / salida).is_file(), (
                f"{nombre}: no escribió la salida esperada ({salida})"
            )


# ---------------------------------------------------------------------------
# 5. Fronteras
# ---------------------------------------------------------------------------


class TestFronteras:
    def test_lo_que_queda_fuera_esta_declarado(self, subconjunto: dict[str, Any]):
        fuera = subconjunto["fuera_de_paridad"]
        assert len(fuera) >= 4
        for item in fuera:
            # Sin motivo ni destino, "fuera de paridad" se lee como "no comparable".
            assert item["motivo"].strip()
            assert item["donde_se_mide"].strip()

    def test_la_comparacion_de_veredicto_esta_excluida_y_derivada(self, subconjunto: dict[str, Any]):
        # La exclusión más importante: comparar el veredicto mezcla diseño con
        # regresión. Se excluye y se deriva a donde SÍ se midió.
        textos = " ".join(item["que"] for item in subconjunto["fuera_de_paridad"])
        assert "veredicto" in textos
        derivacion = " ".join(item["donde_se_mide"] for item in subconjunto["fuera_de_paridad"])
        assert "F3" in derivacion and "F4" in derivacion

    def test_las_capacidades_mejoradas_se_declaran_como_tales(self, subconjunto: dict[str, Any]):
        capacidades = subconjunto["equivalencias_de_capacidad"]
        assert capacidades
        for capacidad in capacidades:
            # Nunca "paridad" donde no la hay: o mejora o es diferencia por diseño.
            assert capacidad["estado"] in {"mejor", "diferente_por_diseno"}
            assert capacidad["nota"].strip()

    def test_no_se_declara_paridad_donde_hay_diferencia_de_diseno(self, subconjunto: dict[str, Any]):
        decisiones = [
            c for c in subconjunto["equivalencias_de_capacidad"]
            if c["estado"] == "diferente_por_diseno"
        ]
        # La decisión de la letra es la diferencia central de ADR-006: tiene que
        # estar declarada como tal, no como paridad.
        assert any("letra" in c["capacidad"] or "tipo" in c["capacidad"] for c in decisiones)

    def test_el_corte_de_v1_es_explicito(self, subconjunto: dict[str, Any]):
        """El DoD dice "aquí se declara el corte de v1": el mapa tiene que decirlo."""
        assert subconjunto.get("corte_de_v1"), (
            "el subconjunto no declara el corte de v1 (lo pide el DoD de T-604)"
        )

    def test_la_corrida_real_queda_fuera_de_la_suite(self, subconjunto: dict[str, Any]):
        # Requiere Ollama con los modelos de cada rol y `files/` (no versionada):
        # se declara como informativa, no como criterio.
        informativas = [
            item for item in subconjunto["fuera_de_paridad"]
            if "real" in item["que"] or "real" in item["donde_se_mide"]
        ]
        assert informativas
        assert any("informativa" in item["donde_se_mide"] or "fuera de la suite" in item["donde_se_mide"]
                   for item in informativas)

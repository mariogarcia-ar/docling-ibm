"""Tests de **paridad** del subconjunto F4 (T-405, épica E-EXT).

**DoD de F4** (``05-plan-ejecucion.md``): "paridad de extracción con v1 en campos
normalizados sobre el golden set" (mitiga R-02/R-08). Este módulo cubre el tramo
**determinista** (sin Ollama, sin Docling y sin v1), que es el que puede correr en
la suite default; la corrida real la hacen
``scripts/F4/paridad_extraccion.py`` y ``scripts/F4/t405.py --origen``.

Qué se verifica:

1. **Procedencia de las reglas de normalización** — cada regla del subconjunto
   cita de qué regla de v1 viene (``regla_v1`` + ``prompt_v1`` + el ``texto_v1``
   **literal**) y ese texto debe seguir existiendo en el YAML de v1. Es lo que
   hace verificable "se reutilizan las reglas de los prompts 10/11/kvi/kvg": si
   alguien cambia el YAML, la regla v2 quedaría citando una procedencia falsa y el
   test falla.
2. **Paridad de cada regla de normalización** sobre los casos
   ``(crudo → esperado)`` derivados del texto de v1: coincidencia exacta.
3. **La regla dura compartida ("no inventar")**: un valor no normalizable conserva
   el crudo en los dos lados (es la regla que v1 pedía en el prompt y que v2
   garantiza en código).
4. **Integridad del subconjunto**: rutas reales existentes, campos de paridad
   dentro del contrato, campos fuera de paridad declarados con su motivo (ADR-001),
   ids únicos.
5. **Paridad estructural de la extracción**: para cada caso del subconjunto se
   corre el **pipeline completo de v2** sobre la lectura declarada
   (interpretar → normalizar → pasada 1 → combinar), se **proyecta** la evidencia
   combinada al shape plano de v1 y se compara contra el ``v1_esperado``. Se exige
   coincidencia en los campos normalizados de lectura.
6. **La lógica de comparación del script** (``campos_desde_v2`` /
   ``notas_de_proyeccion`` / ``comparar`` / ``_equivalentes``): los estados
   ``coincide``/``difiere``/``no_comparable``, la tolerancia número↔texto, el
   plegado de mayúsculas de la letra y las **notas de diferencias esperadas** por
   diseño (no se reportan como regresión).

Reglas duras (F4-subplan §4): la suite default **no** corre Ollama ni Docling ni
v1; este módulo tampoco **importa** v1 ni ejecuta el ``main`` de ``scripts/`` (la
lógica pura del script se carga con ``importlib`` acotado, como en T-305).
"""

from __future__ import annotations

import importlib.util
import json
from pathlib import Path
from typing import Any

import pytest

from voucherflow.extraction import (
    CAMPOS_EXTRACCION,
    CAMPO_FUENTE_LECTURA,
    CLAVE_CAMPOS,
    combinar_evidencia,
    construir_source_evidence,
    normalizar_campo,
    parsear_evidencia_extraccion,
    veredicto_raw_de_evidencia,
)
from voucherflow.extraction.key_value import normalizar_evidencia
from voucherflow.schemas.evidence import Fuente, SourceEvidence

# ---------------------------------------------------------------------------
# Rutas y utilidades
# ---------------------------------------------------------------------------

#: Raíz de v2 (``v2/tests/test_extraction_paridad.py`` → ``parents[1]``).
RAIZ_V2 = Path(__file__).resolve().parents[1]

#: Raíz del repo (contiene ``v1/`` y ``prompts/``).
RAIZ_REPO = RAIZ_V2.parent

#: Subconjunto de paridad de F4.
F4_DIR = RAIZ_V2 / "tests" / "golden" / "F4"

#: Manifiesto del subconjunto.
SUBCONJUNTO = F4_DIR / "subconjunto.json"

#: Prompts YAML de la raíz del repo (los que usaba v1).
PROMPTS_RAIZ = RAIZ_REPO / "prompts"

#: Script de paridad real (su lógica pura se prueba acá, sin ejecutar `main`).
PARIDAD_EXTRAECCION = RAIZ_V2 / "scripts" / "F4" / "paridad_extraccion.py"


def _cargar_json(ruta: Path) -> dict:
    return json.loads(ruta.read_text(encoding="utf-8"))


def _cargar_modulo_script(ruta: Path):
    """Importa un script de ``scripts/F4/`` sin ejecutar su ``main`` (patrón T-305).

    Se usa ``importlib`` con nombre propio para no colisionar con otros módulos y
    para dejar claro que **no** se ejecuta el script (que requeriría Ollama); solo
    se prueban sus funciones puras.
    """
    especificacion = importlib.util.spec_from_file_location(f"_{ruta.stem}_test", ruta)
    modulo = importlib.util.module_from_spec(especificacion)
    assert especificacion.loader is not None
    especificacion.loader.exec_module(modulo)
    return modulo


@pytest.fixture(scope="module")
def subconjunto() -> dict:
    """Manifiesto del subconjunto de paridad de F4."""
    return _cargar_json(SUBCONJUNTO)


@pytest.fixture(scope="module")
def script():
    """El script de paridad real, cargado sin ejecutar ``main``."""
    return _cargar_modulo_script(PARIDAD_EXTRAECCION)


def _evidencia_combinada(lectura: dict[str, Any], fuente: str) -> SourceEvidence:
    """Corre el pipeline de v2 sobre una lectura y devuelve la evidencia combinada.

    Es el tramo determinista completo de F4: interpretar la respuesta (T-401),
    normalizar (T-402), calificar la fuente (T-403) y combinar (T-404).
    """
    crudo = json.dumps(
        {CAMPO_FUENTE_LECTURA: fuente, CLAVE_CAMPOS: lectura}, ensure_ascii=False
    )
    interpretada = parsear_evidencia_extraccion(crudo, fuente=fuente)
    normalizada = normalizar_evidencia(interpretada).evidencia
    veredicto = veredicto_raw_de_evidencia(normalizada)
    source = construir_source_evidence(normalizada, veredicto=veredicto)
    combinada = combinar_evidencia("paridad", [source])
    return combinada


# ---------------------------------------------------------------------------
# 1. Procedencia de las reglas (la afirmación de la tarea, hecha verificable)
# ---------------------------------------------------------------------------


class TestProcedenciaDeLasReglas:
    """Cada regla de v2 cita su regla de v1 y el texto sigue en el prompt."""

    def test_toda_regla_cita_origen(self, subconjunto):
        for regla in subconjunto["normalizacion"]:
            assert regla["regla_v1"], regla["id"]
            assert regla["prompt_v1"], regla["id"]
            assert regla["texto_v1"], regla["id"]
            assert regla["regla_v2"].startswith("NORM_"), regla["id"]

    def test_el_texto_citado_existe_en_el_prompt_de_v1(self, subconjunto):
        # Es la verificación central: la regla de v2 dice "esto viene de acá" y
        # acá tiene que estar. Si el YAML cambia, el test avisa.
        for regla in subconjunto["normalizacion"]:
            prompt = PROMPTS_RAIZ / Path(regla["prompt_v1"]).relative_to("prompts")
            assert prompt.exists(), f"no existe el prompt {regla['prompt_v1']}"
            contenido = prompt.read_text(encoding="utf-8")
            assert regla["texto_v1"] in contenido, (
                f"la regla {regla['id']} cita {regla['texto_v1']!r} de "
                f"{regla['prompt_v1']} y ese texto ya no está"
            )

    def test_las_reglas_de_v2_cubren_todos_los_campos_del_contrato(self, subconjunto):
        # No se exige que cada campo tenga una regla propia (los montos comparten
        # NORM_MONTO), pero sí que las reglas declaradas sean reglas reales.
        from voucherflow.extraction.key_value import REGLA_POR_CAMPO

        declaradas = {regla["regla_v2"] for regla in subconjunto["normalizacion"]}
        assert declaradas <= set(REGLA_POR_CAMPO.values())

    def test_los_prompts_de_v1_son_los_de_la_extraccion(self, subconjunto):
        # El subconjunto no puede citar un prompt que no sea de extracción: la
        # paridad es de F4, no de clasificación.
        for regla in subconjunto["normalizacion"]:
            assert "extraction_key_value" in regla["prompt_v1"], regla["id"]


# ---------------------------------------------------------------------------
# 2. Paridad de cada regla de normalización
# ---------------------------------------------------------------------------


class TestParidadNormalizacion:
    """Los casos derivados del texto de v1 dan el valor canónico exacto."""

    @pytest.mark.parametrize(
        "regla_id",
        [
            regla["id"]
            for regla in json.loads(
                (Path(__file__).resolve().parents[1] / "tests" / "golden" / "F4"
                 / "subconjunto.json").read_text(encoding="utf-8")
            )["normalizacion"]
        ],
    )
    def test_casos_de_la_regla(self, subconjunto, regla_id):
        regla = next(r for r in subconjunto["normalizacion"] if r["id"] == regla_id)
        for caso in regla["casos"]:
            resultado = normalizar_campo(caso["campo"], caso["crudo"])
            if caso["esperado"] == "estructurado":
                # Los ítems se publican como JSON canónico: se compara la forma.
                assert resultado.valor, caso
                assert resultado.items, caso
                continue
            assert resultado.valor == caso["esperado"], (
                f"{regla_id}: {caso['campo']} {caso['crudo']!r} → "
                f"{resultado.valor!r} (esperado {caso['esperado']!r})"
            )

    def test_los_derivados_del_comprobante_se_generan(self, subconjunto):
        regla = next(
            r for r in subconjunto["normalizacion"] if r["id"] == "comprobante_ppppp"
        )
        resultado = normalizar_campo(
            regla["casos"][0]["campo"], regla["casos"][0]["crudo"]
        )
        assert resultado.derivados == regla["derivados_esperados"]

    def test_los_items_estructurados_coinciden(self, subconjunto):
        regla = next(r for r in subconjunto["normalizacion"] if r["id"] == "productos")
        resultado = normalizar_campo(
            regla["casos"][0]["campo"], regla["casos"][0]["crudo"]
        )
        assert [item.descripcion for item in resultado.items] == ["café", "pan"]
        assert resultado.items[1].cantidad is None
        assert resultado.items[1].precio_unitario == 250


# ---------------------------------------------------------------------------
# 3. La regla dura compartida: no inventar
# ---------------------------------------------------------------------------


class TestNoInventar:
    """Los dos lados conservan el crudo cuando no se puede normalizar."""

    def test_el_subconjunto_declara_la_regla_y_sus_casos(self, subconjunto):
        bloque = subconjunto["no_inventar"]
        # La regla y sus dos lados (v1 en el prompt, v2 en codigo) se declaran
        # explicitamente; el texto de v1 se verifica contra el YAML mas abajo.
        assert bloque["regla_v1"].strip()
        assert bloque["regla_v2"].strip()
        assert bloque["casos"], "la regla compartida necesita casos"

    def test_un_valor_no_normalizable_conserva_el_crudo(self, subconjunto):
        for caso in subconjunto["no_inventar"]["casos"]:
            resultado = normalizar_campo(caso["campo"], caso["crudo"])
            assert resultado.valor == caso["esperado"], caso
            # Y lo declara: un valor conservado sin aviso sería un silencio.
            assert resultado.avisos, caso

    def test_la_regla_esta_en_los_prompts_de_v1(self, subconjunto):
        # El texto que la regla compartida cita debe estar en el YAML de v1:
        # es la misma verificacion de procedencia que las reglas de normalizacion.
        bloque = subconjunto["no_inventar"]
        contenido = (PROMPTS_RAIZ / "11-extraction_key_value_invoice_prompt.yaml").read_text(
            encoding="utf-8"
        )
        assert bloque["texto_v1"] in contenido

    def test_el_modo_generico_tambien_declara_no_inventar(self):
        contenido = (
            PROMPTS_RAIZ / "10-extraction_key_value_generic_prompt.yaml"
        ).read_text(encoding="utf-8")
        assert "Omití" in contenido or "No lo inventes" in contenido


# ---------------------------------------------------------------------------
# 4. Integridad del subconjunto
# ---------------------------------------------------------------------------


class TestIntegridadSubconjunto:
    """El subconjunto es honesto: rutas reales, campos válidos y alcance explícito."""

    def test_los_documentos_reales_existen(self, subconjunto):
        for caso in subconjunto["real"]:
            assert (RAIZ_REPO / caso["ruta"]).exists(), caso["id"]

    def test_los_campos_de_paridad_son_del_contrato(self, subconjunto):
        for campo in subconjunto["campos_paridad"]:
            assert campo in CAMPOS_EXTRACCION, campo

    def test_los_campos_del_modo_generico_estan_fuera_del_contrato(self, subconjunto):
        # El modo genérico (`kvg`) agrega claves del documento que el contrato
        # fiscal no pide: se declaran aparte para que la paridad los cubra sin
        # fingir que son del contrato.
        genericos = subconjunto["campos_paridad_generico"]
        assert genericos
        assert any(c not in CAMPOS_EXTRACCION for c in genericos)

    def test_los_campos_fuera_de_paridad_estan_justificados(self, subconjunto):
        # ADR-001: los campos de decisión no se leen en v2. Declararlos con su
        # motivo es lo que hace que su ausencia no se lea como un faltante.
        fuera = subconjunto["campos_fuera_de_paridad"]
        assert fuera
        for entrada in fuera:
            assert entrada["campo"], entrada
            assert entrada["por_que"].strip(), entrada
            assert entrada["campo"] not in subconjunto["campos_paridad"]

    def test_todo_campo_del_contrato_esta_declarado(self, subconjunto):
        # Un campo del contrato no puede simplemente desaparecer de la paridad:
        # o se compara, o se declara por qué no. Sin esto, `razon_social_receptor`
        # (que v1 nunca pidió) quedaba en tierra de nadie y la paridad parecía
        # cubrir los 16 campos cuando cubría 15.
        declarados = (
            set(subconjunto["campos_paridad"])
            | {e["campo"] for e in subconjunto["campos_fuera_de_paridad"]}
            | {e["campo"] for e in subconjunto["campos_sin_contraparte_v1"]}
        )
        sin_declarar = set(CAMPOS_EXTRACCION) - declarados
        assert not sin_declarar, f"campos del contrato sin declarar: {sorted(sin_declarar)}"

    def test_los_campos_sin_contraparte_v1_estan_fundados(self, subconjunto):
        # Son campos que v2 lee pero v1 no: no hay nada contra lo que comparar.
        # Declararlos evita confundir "no comparable" con "no implementado".
        sin_contraparte = subconjunto["campos_sin_contraparte_v1"]
        assert sin_contraparte
        for entrada in sin_contraparte:
            assert entrada["campo"] in CAMPOS_EXTRACCION, entrada
            assert entrada["por_que"].strip(), entrada
            assert entrada["v1_lo_pide"] is False, entrada
            assert entrada["v2_lo_lee"] is True, entrada
            assert entrada["campo"] not in subconjunto["campos_paridad"]

    def test_los_ids_son_unicos(self, subconjunto):
        ids = [r["id"] for r in subconjunto["normalizacion"]]
        ids += [c["id"] for c in subconjunto["extraccion"]]
        ids += [c["id"] for c in subconjunto["real"]]
        assert len(ids) == len(set(ids))

    def test_las_lecturas_de_los_casos_son_campos_conocidos(self, subconjunto):
        # Un caso puede declarar campos del contrato **o** del modo genérico
        # (``kvg``); lo que no puede es inventar un campo nuevo sin declararlo.
        permitidos = set(CAMPOS_EXTRACCION) | set(subconjunto["campos_paridad_generico"])
        for caso in subconjunto["extraccion"]:
            for campo in caso["lectura"]:
                assert campo in permitidos, (caso["id"], campo)
            for campo in caso.get("campos", []):
                assert campo in permitidos, (caso["id"], campo)

    def test_el_manifiesto_declara_su_decision_de_alcance(self, subconjunto):
        assert subconjunto["golden_version"].startswith("0.1-f4")
        assert "ADR-001" in subconjunto["decision_alcance"]


# ---------------------------------------------------------------------------
# 5. Paridad estructural de la extracción (proyección sobre el JSON plano)
# ---------------------------------------------------------------------------


class TestParidadExtraccion:
    """La evidencia de v2 proyectada al shape de v1 coincide campo a campo."""

    def test_los_casos_declarados_corren_y_coinciden(self, subconjunto, script):
        for caso in subconjunto["extraccion"]:
            combinada = _evidencia_combinada(caso["lectura"], caso["fuente"])
            campos = caso.get("campos") or subconjunto["campos_paridad"]
            v2 = script.campos_desde_v2(combinada, campos=campos)
            esperados = caso.get("v1_esperado") or caso.get("v1_esperado_generico") or {}
            comparacion = script.comparar(esperados, v2)
            for campo, esperado in esperados.items():
                info = comparacion[campo]
                assert info["estado"] == "coincide", (
                    f"{caso['id']}: {campo} → {info['v2']!r} "
                    f"(v1 esperaba {esperado!r})"
                )

    def test_el_caso_generico_estructura_los_items(self, subconjunto, script):
        caso = next(c for c in subconjunto["extraccion"] if c["id"] == "modo_generico_kvg")
        combinada = _evidencia_combinada(caso["lectura"], caso["fuente"])
        v2 = script.campos_desde_v2(combinada, campos=["productos"])
        # El JSON canónico de ítems debe contener los ítems declarados.
        items = json.loads(v2["productos"])
        assert [i["descripcion"] for i in items] == [
            i["descripcion"] for i in caso["v1_esperado_items"]
        ]
        assert [i["cantidad"] for i in items] == [
            i["cantidad"] for i in caso["v1_esperado_items"]
        ]
        assert [i["precio_unitario"] for i in items] == [
            i["precio_unitario"] for i in caso["v1_esperado_items"]
        ]

    def test_los_valores_no_normalizables_sobreviven_al_pipeline(self, subconjunto, script):
        caso = next(
            c for c in subconjunto["extraccion"] if c["id"] == "valores_no_normalizables"
        )
        combinada = _evidencia_combinada(caso["lectura"], caso["fuente"])
        v2 = script.campos_desde_v2(combinada)
        for campo, esperado in caso["v1_esperado"].items():
            assert v2[campo] == esperado, campo

    def test_la_proyeccion_no_inventa_campos_ausentes(self, subconjunto, script):
        # Un campo que ninguna fuente declaró NO entra en la proyección: la
        # ausencia es información, no un None que después se confunda con
        # "no comparable".
        caso = next(c for c in subconjunto["extraccion"] if c["id"] == "cuit_truncado_por_ocr")
        combinada = _evidencia_combinada(caso["lectura"], caso["fuente"])
        v2 = script.campos_desde_v2(combinada)
        assert "fecha_emision" not in v2
        assert "importe_total_facturado" not in v2

    def test_la_evidencia_completa_sigue_disponible(self, subconjunto, script):
        # La proyección es una vista: la evidencia por fuente con su resolución
        # sigue entera para la auditoría (combinar no descarta).
        caso = subconjunto["extraccion"][0]
        combinada = _evidencia_combinada(caso["lectura"], caso["fuente"])
        campo = combinada.campos["cuit_emisor"]
        assert campo.fuente is not None
        assert campo.resolucion is not None
        assert campo.llm is not None or campo.vlm is not None

    def test_la_letra_se_compara_plegando_mayusculas(self, subconjunto, script):
        # v2 publica el vocabulario en mayúsculas (T-402); v1 también, pero la
        # comparación no debe depender de eso.
        comparacion = script.comparar({"tipo_comprobante": "A"}, {"tipo_comprobante": "a"})
        assert comparacion["tipo_comprobante"]["estado"] == "coincide"


# ---------------------------------------------------------------------------
# 6. La lógica de comparación del script
# ---------------------------------------------------------------------------


class TestLogicaDeComparacion:
    """Los estados, la tolerancia numérica y las notas de diferencias esperadas."""

    def test_coincide_difiere_y_no_comparable(self, script):
        comparacion = script.comparar(
            {"a": "1", "b": "2", "c": "3"},
            {"a": "1", "b": "9"},
        )
        assert comparacion["a"]["estado"] == "coincide"
        assert comparacion["b"]["estado"] == "difiere"
        assert comparacion["c"]["estado"] == "no_comparable"

    def test_la_nota_de_una_diferencia_esperada_no_habla_de_regresion(self, script):
        comparacion = script.comparar({"moneda": "ARS"}, {"moneda": None})
        assert comparacion["moneda"]["estado"] == "no_comparable"
        assert "default" in comparacion["moneda"]["nota"]

    def test_una_diferencia_sin_causa_declarada_pide_revision(self, script):
        comparacion = script.comparar({"cuit_emisor": "30-1"}, {"cuit_emisor": "30-2"})
        assert comparacion["cuit_emisor"]["estado"] == "difiere"
        assert "paridad **o mejora**" in comparacion["cuit_emisor"]["nota"]

    def test_numero_contra_texto_es_el_mismo_dato(self, script):
        comparacion = script.comparar(
            {"importe_total_facturado": "12345.67"},
            {"importe_total_facturado": 12345.67},
        )
        assert comparacion["importe_total_facturado"]["estado"] == "coincide"

    def test_no_se_interpreta_el_texto_como_numero(self, script):
        # "12.345,67" NO equivale a 12.345: interpretarlo sería re-hacer la
        # normalización de T-402 (que tiene sus propios tests).
        comparacion = script.comparar(
            {"importe_total_facturado": "12.345,67"},
            {"importe_total_facturado": 12.345},
        )
        assert comparacion["importe_total_facturado"]["estado"] == "difiere"

    def test_las_notas_de_proyeccion_marcan_los_campos_estructurados(self, script, subconjunto):
        caso = subconjunto["extraccion"][0]
        combinada = _evidencia_combinada(caso["lectura"], caso["fuente"])
        notas = script.notas_de_proyeccion(combinada)
        assert notas["fecha_emision"] == "formato_volatil"
        assert notas["importe_total_facturado"] == "formato_volatil"
        assert "cuit_emisor" not in notas

    def test_el_script_no_importa_v1_al_cargarse(self, script):
        # La suite default no puede depender de v1 ni de Ollama: el script se
        # carga (importlib) y su lógica pura corre sin tocar la red.
        assert not hasattr(script, "OllamaClient")

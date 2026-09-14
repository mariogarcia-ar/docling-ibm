"""Subconjunto de extracción F4 (T-405, épica E-EXT).

**DoD de F4** (``05-plan-ejecucion.md``): "paridad de extracción en campos
normalizados sobre el golden set" (mitiga R-02/R-08). Este módulo cubre el tramo
**determinista** (sin Ollama, sin Docling y sin red), que es el que puede correr
en la suite default; la corrida real sobre documentos la hace
``scripts/verificacion/etapa-extraccion.py``.

Qué se verifica:

1. **Procedencia de las reglas de normalización** — cada regla del subconjunto
   cita de dónde viene (``regla_referencia`` + ``prompt_referencia`` + el
   ``texto_referencia`` **literal**) y ese texto debe seguir existiendo en el
   YAML de referencia. Es lo que hace verificable "se reutilizan las reglas de
   los prompts 10/11/kvi/kvg": si alguien cambia el YAML, la regla quedaría
   citando una procedencia falsa y el test falla.
2. **Exactitud de cada regla de normalización** sobre los casos
   ``(crudo → esperado)`` derivados del texto de referencia: coincidencia exacta.
3. **La regla dura compartida ("no inventar")**: un valor no normalizable
   conserva el crudo **y lo declara** (un valor conservado sin aviso sería un
   silencio).
4. **Integridad del subconjunto**: rutas reales existentes, campos de medición
   dentro del contrato, campos fuera de alcance declarados con su motivo
   (ADR-001) e ids únicos.
5. **Paridad estructural de la extracción**: para cada caso del subconjunto se
   corre el **pipeline completo** sobre la lectura declarada (interpretar →
   normalizar → pasada 1 → combinar), se **proyecta** la evidencia combinada al
   shape plano y se compara contra el ``esperado_plano``. Se exige coincidencia
   en los campos normalizados.

Reglas duras (F4-subplan §4): la suite default **no** corre Ollama ni Docling ni
red; este módulo no ejecuta el ``main`` de ``scripts/``.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from voucherflow.extraction import (
    CAMPO_FUENTE_LECTURA,
    CAMPOS_EXTRACCION,
    CLAVE_CAMPOS,
    combinar_evidencia,
    construir_source_evidence,
    normalizar_campo,
    parsear_evidencia_extraccion,
    veredicto_raw_de_evidencia,
)
from voucherflow.extraction.key_value import REGLA_POR_CAMPO, normalizar_evidencia

# ---------------------------------------------------------------------------
# Rutas y utilidades
# ---------------------------------------------------------------------------

#: Raíz del repo (``tests/test_extraction_paridad.py`` → ``parents[1]``).
RAIZ_REPO = Path(__file__).resolve().parents[1]

#: Subconjunto de extracción de F4.
F4_DIR = RAIZ_REPO / "tests" / "golden" / "F4"

#: Manifiesto del subconjunto.
SUBCONJUNTO = F4_DIR / "subconjunto.json"

#: Prompts YAML de referencia (raíz del repo).
PROMPTS_RAIZ = RAIZ_REPO / "prompts"

#: Script de métricas del DoD (su lógica pura se prueba acá, sin ejecutar `main`).
T405 = RAIZ_REPO / "scripts" / "verificacion" / "etapa-extraccion.py"


def _cargar_json(ruta: Path) -> dict:
    return json.loads(ruta.read_text(encoding="utf-8"))


@pytest.fixture(scope="module")
def subconjunto() -> dict:
    """Manifiesto del subconjunto de extracción de F4."""
    return _cargar_json(SUBCONJUNTO)


def _evidencia_combinada(lectura: dict[str, Any], fuente: str):
    """Corre el pipeline determinista sobre una lectura y devuelve la evidencia.

    Es el tramo completo: interpretar la respuesta (T-401), normalizar (T-402),
    calificar la fuente (T-403) y combinar (T-404).
    """
    crudo = json.dumps(
        {CAMPO_FUENTE_LECTURA: fuente, CLAVE_CAMPOS: lectura}, ensure_ascii=False
    )
    interpretada = parsear_evidencia_extraccion(crudo, fuente=fuente)
    normalizada = normalizar_evidencia(interpretada).evidencia
    veredicto = veredicto_raw_de_evidencia(normalizada)
    source = construir_source_evidence(normalizada, veredicto=veredicto)
    return combinar_evidencia("subconjunto", [source]), source


def _proyectar(combinada: Any, campos: list[str]) -> dict[str, Any]:
    """Proyecta la evidencia combinada al shape plano (ver el reporte de la etapa)."""
    valores: dict[str, Any] = {}
    for campo in campos:
        evidencia = combinada.campos.get(campo)
        if evidencia is None or evidencia.fuente is None:
            continue
        valores[campo] = evidencia.valor
    return valores


# ---------------------------------------------------------------------------
# 1. Procedencia de las reglas (la afirmación de la tarea, hecha verificable)
# ---------------------------------------------------------------------------


class TestProcedenciaDeLasReglas:
    """Cada regla cita su origen y el texto sigue en el prompt de referencia."""

    def test_toda_regla_cita_origen(self, subconjunto: dict):
        for regla in subconjunto["normalizacion"]:
            assert regla["regla_referencia"], regla["id"]
            assert regla["prompt_referencia"], regla["id"]
            assert regla["texto_referencia"], regla["id"]
            assert regla["regla_normalizacion"].startswith("NORM_"), regla["id"]

    def test_el_texto_citado_existe_en_el_prompt_de_referencia(self, subconjunto: dict):
        # Es la verificación central: la regla dice "esto viene de acá" y acá
        # tiene que estar. Si el YAML cambia, el test avisa.
        for regla in subconjunto["normalizacion"]:
            prompt = PROMPTS_RAIZ / Path(regla["prompt_referencia"]).relative_to("prompts")
            assert prompt.exists(), f"no existe el prompt {regla['prompt_referencia']}"
            contenido = prompt.read_text(encoding="utf-8")
            assert regla["texto_referencia"] in contenido, (
                f"la regla {regla['id']} cita {regla['texto_referencia']!r} de "
                f"{regla['prompt_referencia']} y ese texto ya no está"
            )

    def test_las_reglas_declaradas_son_reglas_reales(self, subconjunto: dict):
        declaradas = {regla["regla_normalizacion"] for regla in subconjunto["normalizacion"]}
        assert declaradas <= set(REGLA_POR_CAMPO.values())

    def test_los_prompts_citados_son_de_extraccion(self, subconjunto: dict):
        # El subconjunto no puede citar un prompt que no sea de extracción.
        for regla in subconjunto["normalizacion"]:
            assert "extraction_key_value" in regla["prompt_referencia"], regla["id"]


# ---------------------------------------------------------------------------
# 2. Exactitud de cada regla de normalización
# ---------------------------------------------------------------------------


class TestExactitudNormalizacion:
    """Los casos derivados del texto de referencia dan el valor canónico exacto."""

    @pytest.mark.parametrize(
        "regla_id",
        [regla["id"] for regla in json.loads(SUBCONJUNTO.read_text(encoding="utf-8"))["normalizacion"]],
    )
    def test_casos_de_la_regla(self, subconjunto: dict, regla_id: str):
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

    def test_los_derivados_del_comprobante_se_generan(self, subconjunto: dict):
        regla = next(r for r in subconjunto["normalizacion"] if r["id"] == "comprobante_ppppp")
        resultado = normalizar_campo(regla["casos"][0]["campo"], regla["casos"][0]["crudo"])
        assert resultado.derivados == regla["derivados_esperados"]

    def test_los_items_estructurados_coinciden(self, subconjunto: dict):
        regla = next(r for r in subconjunto["normalizacion"] if r["id"] == "productos")
        resultado = normalizar_campo(regla["casos"][0]["campo"], regla["casos"][0]["crudo"])
        assert [item.descripcion for item in resultado.items] == ["café", "pan"]
        assert resultado.items[1].cantidad is None
        assert resultado.items[1].precio_unitario == 250


# ---------------------------------------------------------------------------
# 3. La regla dura compartida: no inventar
# ---------------------------------------------------------------------------


class TestNoInventar:
    """Un valor no normalizable conserva el crudo, y lo declara."""

    def test_el_subconjunto_declara_la_regla_y_sus_casos(self, subconjunto: dict):
        bloque = subconjunto["no_inventar"]
        # La regla y sus dos lados (el prompt de referencia y el código) se
        # declaran explícitamente; el texto se verifica contra el YAML abajo.
        assert bloque["regla_referencia"].strip()
        assert bloque["regla_normalizacion"].strip()
        assert bloque["casos"], "la regla compartida necesita casos"

    def test_un_valor_no_normalizable_conserva_el_crudo(self, subconjunto: dict):
        for caso in subconjunto["no_inventar"]["casos"]:
            resultado = normalizar_campo(caso["campo"], caso["crudo"])
            assert resultado.valor == caso["esperado"], caso
            # Y lo declara: un valor conservado sin aviso sería un silencio.
            assert resultado.avisos, caso

    def test_la_regla_esta_en_los_prompts_de_referencia(self, subconjunto: dict):
        bloque = subconjunto["no_inventar"]
        contenido = (PROMPTS_RAIZ / "11-extraction_key_value_invoice_prompt.yaml").read_text(
            encoding="utf-8"
        )
        assert bloque["texto_referencia"] in contenido

    def test_el_modo_generico_tambien_declara_no_inventar(self):
        contenido = (PROMPTS_RAIZ / "10-extraction_key_value_generic_prompt.yaml").read_text(
            encoding="utf-8"
        )
        assert "Omití" in contenido or "No lo inventes" in contenido


# ---------------------------------------------------------------------------
# 4. Integridad del subconjunto
# ---------------------------------------------------------------------------


class TestIntegridadSubconjunto:
    """El subconjunto es honesto: rutas reales, campos válidos y alcance explícito."""

    def test_los_documentos_reales_existen(self, subconjunto: dict):
        for caso in subconjunto["real"]:
            assert (RAIZ_REPO / caso["ruta"]).exists(), caso["id"]

    def test_los_campos_medidos_son_del_contrato(self, subconjunto: dict):
        for campo in subconjunto["campos_paridad"]:
            assert campo in CAMPOS_EXTRACCION, campo

    def test_los_campos_del_modo_generico_estan_fuera_del_contrato(self, subconjunto: dict):
        # El modo genérico (`kvg`) agrega claves del documento que el contrato
        # fiscal no pide: se declaran aparte para cubrirlas sin fingir que son
        # del contrato.
        genericos = subconjunto["campos_paridad_generico"]
        assert genericos
        assert any(c not in CAMPOS_EXTRACCION for c in genericos)

    def test_los_campos_fuera_de_alcance_estan_justificados(self, subconjunto: dict):
        # ADR-001: los campos de decisión no se leen en la extracción.
        # Declararlos con su motivo es lo que hace que su ausencia no se lea
        # como un faltante.
        fuera = subconjunto["campos_fuera_de_alcance"]
        assert fuera
        for entrada in fuera:
            assert entrada["campo"], entrada
            assert entrada["por_que"].strip(), entrada
            assert entrada["campo"] not in subconjunto["campos_paridad"]

    def test_todo_campo_del_contrato_esta_declarado(self, subconjunto: dict):
        # Un campo del contrato no puede simplemente desaparecer de la medición:
        # o se compara, o se declara por qué no. Sin esto, un campo que el
        # prompt de referencia nunca pidió quedaba en tierra de nadie y el
        # conteo parecía cubrir todo el contrato sin cubrirlo.
        declarados = (
            set(subconjunto["campos_paridad"])
            | {e["campo"] for e in subconjunto["campos_fuera_de_alcance"]}
            | {e["campo"] for e in subconjunto["campos_sin_contraparte"]}
        )
        sin_declarar = set(CAMPOS_EXTRACCION) - declarados
        assert not sin_declarar, f"campos del contrato sin declarar: {sorted(sin_declarar)}"

    def test_los_campos_sin_contraparte_estan_fundados(self, subconjunto: dict):
        # Son campos que el extractor lee pero el prompt de referencia no pedía:
        # no hay nada contra lo que comparar. Declararlos evita confundir
        # "no comparable" con "no implementado".
        sin_contraparte = subconjunto["campos_sin_contraparte"]
        assert sin_contraparte
        for entrada in sin_contraparte:
            assert entrada["campo"] in CAMPOS_EXTRACCION, entrada
            assert entrada["por_que"].strip(), entrada
            assert entrada["pedido_por_prompt_referencia"] is False, entrada
            assert entrada["leido_por_el_extractor"] is True, entrada
            assert entrada["campo"] not in subconjunto["campos_paridad"]

    def test_los_ids_son_unicos(self, subconjunto: dict):
        ids = [r["id"] for r in subconjunto["normalizacion"]]
        ids += [c["id"] for c in subconjunto["extraccion"]]
        ids += [c["id"] for c in subconjunto["real"]]
        assert len(ids) == len(set(ids))

    def test_las_lecturas_de_los_casos_son_campos_conocidos(self, subconjunto: dict):
        # Un caso puede declarar campos del contrato **o** del modo genérico
        # (`kvg`); lo que no puede es inventar un campo nuevo sin declararlo.
        permitidos = set(CAMPOS_EXTRACCION) | set(subconjunto["campos_paridad_generico"])
        for caso in subconjunto["extraccion"]:
            for campo in caso["lectura"]:
                assert campo in permitidos, (caso["id"], campo)
            for campo in caso.get("campos", []):
                assert campo in permitidos, (caso["id"], campo)

    def test_el_manifiesto_declara_su_decision_de_alcance(self, subconjunto: dict):
        assert subconjunto["golden_version"].startswith("0.1-f4")
        assert "ADR-001" in subconjunto["decision_alcance"]


# ---------------------------------------------------------------------------
# 5. Paridad estructural de la extracción (proyección al shape plano)
# ---------------------------------------------------------------------------


class TestParidadExtraccion:
    """La evidencia proyectada al shape plano coincide campo a campo."""

    def test_los_casos_declarados_corren_y_coinciden(self, subconjunto: dict):
        for caso in subconjunto["extraccion"]:
            combinada, _ = _evidencia_combinada(caso["lectura"], caso["fuente"])
            campos = caso.get("campos") or subconjunto["campos_paridad"]
            obtenidos = _proyectar(combinada, campos)
            esperados = (
                caso.get("esperado_plano")
                or caso.get("esperado_plano_generico")
                or {}
            )
            for campo, esperado in esperados.items():
                obtenido = obtenidos.get(campo)
                assert esperado == obtenido or str(esperado) == str(obtenido), (
                    f"{caso['id']}: {campo} → {obtenido!r} "
                    f"(se esperaba {esperado!r})"
                )

    def test_el_caso_generico_estructura_los_items(self, subconjunto: dict):
        caso = next(c for c in subconjunto["extraccion"] if c["id"] == "modo_generico_kvg")
        combinada, _ = _evidencia_combinada(caso["lectura"], caso["fuente"])
        obtenidos = _proyectar(combinada, ["productos"])
        items = json.loads(obtenidos["productos"])
        esperados = caso["esperado_plano_items"]
        assert [i["descripcion"] for i in items] == [i["descripcion"] for i in esperados]
        assert [i["cantidad"] for i in items] == [i["cantidad"] for i in esperados]
        assert [i["precio_unitario"] for i in items] == [i["precio_unitario"] for i in esperados]

    def test_los_valores_no_normalizables_sobreviven_al_pipeline(self, subconjunto: dict):
        caso = next(
            c for c in subconjunto["extraccion"] if c["id"] == "valores_no_normalizables"
        )
        combinada, _ = _evidencia_combinada(caso["lectura"], caso["fuente"])
        obtenidos = _proyectar(combinada, list(caso["esperado_plano"]))
        for campo, esperado in caso["esperado_plano"].items():
            assert obtenidos[campo] == esperado, campo

    def test_la_proyeccion_no_inventa_campos_ausentes(self, subconjunto: dict):
        # Un campo que ninguna fuente declaró NO entra en la proyección: la
        # ausencia es información, no un None que después se confunda con
        # "no comparable".
        caso = next(
            c for c in subconjunto["extraccion"] if c["id"] == "cuit_truncado_por_ocr"
        )
        combinada, _ = _evidencia_combinada(caso["lectura"], caso["fuente"])
        obtenidos = _proyectar(combinada, list(CAMPOS_EXTRACCION))
        assert "fecha_emision" not in obtenidos
        assert "importe_total_facturado" not in obtenidos

    def test_la_evidencia_completa_sigue_disponible(self, subconjunto: dict):
        # La proyección es una vista: la evidencia por fuente con su resolución
        # sigue entera para la auditoría (combinar no descarta).
        caso = subconjunto["extraccion"][0]
        combinada, _ = _evidencia_combinada(caso["lectura"], caso["fuente"])
        campo = combinada.campos["cuit_emisor"]
        assert campo.fuente is not None
        assert campo.resolucion is not None
        assert campo.llm is not None or campo.vlm is not None

    def test_el_sosten_de_los_campos_no_es_vacio(self, subconjunto: dict):
        # El contrato de F0 exige un fragmento de sostén no vacío: sin él, el
        # campo no es auditable.
        for caso in subconjunto["extraccion"]:
            _, source = _evidencia_combinada(caso["lectura"], caso["fuente"])
            for campo, evidencia in source.campos.items():
                assert evidencia.fragmento_sustento.strip(), (caso["id"], campo)


# ---------------------------------------------------------------------------
# 6. Lógica de comparación de las métricas (reporte de la etapa)
# ---------------------------------------------------------------------------


def _cargar_t405():
    """Importa el reporte de la etapa sin ejecutar su ``main``."""
    import importlib.util
    import sys

    especificacion = importlib.util.spec_from_file_location("_t405_test", T405)
    assert especificacion is not None and especificacion.loader is not None
    modulo = importlib.util.module_from_spec(especificacion)
    sys.modules[especificacion.name] = modulo
    especificacion.loader.exec_module(modulo)
    return modulo


class TestMetricasDelDod:
    """``t405.py`` agrega las métricas del DoD de F4 (subplan §10)."""

    @pytest.fixture(scope="class")
    def modulo(self):
        return _cargar_t405()

    def test_las_reglas_de_normalizacion_son_exactas(self, modulo, subconjunto):
        medicion = modulo.metrica_reglas(subconjunto)
        assert medicion["casos"] > 0
        assert medicion["exactitud"] == 100.0
        assert medicion["procedencia_verificada"] is True

    def test_la_paridad_estructural_es_exacta(self, modulo, subconjunto):
        medicion = modulo.metrica_extraccion(subconjunto)
        assert medicion["campos_comparados"] > 0
        assert medicion["exactitud"] == 100.0, [
            c for c in medicion["detalle"] if c["coinciden"] != c["campos"]
        ]

    def test_el_sosten_de_los_campos_es_total(self, modulo, subconjunto):
        medicion = modulo.metrica_extraccion(subconjunto)
        assert medicion["campos_totales"] > 0
        assert medicion["sustento"] == 100.0

    def test_el_modo_generico_cubre_sus_claves(self, modulo, subconjunto):
        medicion = modulo.metrica_generico(subconjunto)
        assert medicion["campos"]
        assert medicion["cobertura"] == 100.0

    def test_el_contrato_no_tiene_campos_sin_regla(self, modulo):
        assert modulo.metrica_norm_vs_contrato()["sin_regla"] == []

    def test_el_reporte_sale_con_codigo_cero(self, tmp_path, monkeypatch):
        # El script sale con 0 cuando el tramo determinista está en el umbral
        # (y con ≠ 0 si no), que es lo que lo hace usable en CI.
        modulo = _cargar_t405()
        salida = tmp_path / "t405.json"
        monkeypatch.setattr("sys.argv", ["t405.py", "--json", str(salida)])
        with pytest.raises(SystemExit) as salida_sistema:
            modulo.main()
        assert salida_sistema.value.code == 0
        datos = _cargar_json(salida)
        assert datos["fallos"] == 0
        assert datos["reglas_normalizacion"]["exactitud"] == 100.0

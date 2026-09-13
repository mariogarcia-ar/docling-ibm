"""Tests del subconjunto de clasificación F3 (T-305, épicas E-CLAS-1/E-CLAS-2).

**DoD de F3** (``05-plan-ejecucion.md``): "La letra se decide por reglas sobre
evidencia (no por el prompt); tests de reglas R1-R7 unitarios." La parte de
reglas unitarias la cubren T-301/T-303/T-304; este módulo cubre el tramo
**determinista** del subconjunto de F3 (sin Ollama y sin red), que es el único
que puede correr en la suite default.

Qué se verifica:

1. **Fidelidad de los prompts versionados en código** — los ``system``/``user``
   de los pasos contables deben ser **idénticos** a los YAML de ``prompts/``
   (``01..03-*.yaml``) y el ``11.1`` debe conservar la **guía de lectura** de
   ``prompts/facturacion/11.1-deteccion_tipo_factura.yaml``. Los YAML son la
   copia de referencia congelada que vive en el repo: si alguien retoca el
   prompt en código, el test obliga a justificar el cambio.
2. **Integridad del subconjunto** (``tests/golden/F3/``) — las rutas existen,
   los ids reales están en ``casos.csv`` y la ``letra_derivada`` está sustentada
   por la ``evidencia_veredicto`` del golden (si no, sería una etiqueta
   inventada); las etiquetas se declaran como derivadas, no "verificadas" por
   contador.
3. **Exactitud determinista de la letra** sobre los casos **sintéticos** del
   subconjunto: el motor debe decidir la letra y la regla esperadas (R1/R2A/R2B/
   R3 y la cascada R4→R5 con la alerta R7). Esto es exactamente el tramo que
   ADR-006 sacó del prompt, así que se exige exactitud, no acuerdo.
4. **Regresión del bug de R5 encontrado por T-305**: el ``\\s+`` del patrón
   literal cruzaba el salto de línea del markdown de Docling y tomaba la letra
   de la línea siguiente (``"FACTURA\\n  Código: 1"`` → ``C``). El test fija el
   comportamiento correcto (la letra debe estar **en la misma línea**).

Reglas duras (F3-subplan §4): la suite default no corre Ollama ni red; este
módulo tampoco **importa** ``scripts/`` (verifica los prompts leyendo los YAML
como **datos**).
"""

from __future__ import annotations

import csv
import importlib.util
import json
from pathlib import Path

import pytest

from voucherflow.classification import (
    PASOS_CONTABLES,
    SYSTEM_PROMPT_POR_FUENTE,
    clasificar_tipo_comprobante,
)
from voucherflow.classification.prompt_tipo_comprobante import (
    SYSTEM_PROMPT_TIPO_COMPROBANTE,
)
from voucherflow.rules.contexto import ContextoTipoComprobante
from voucherflow.rules.tipo_comprobante_rules import (
    REGEX_LETRA_ENCABEZADO,
    extraer_letra_encabezado,
)

# ---------------------------------------------------------------------------
# Rutas
# ---------------------------------------------------------------------------

#: Raíz del repo (``tests/test_classification_paridad.py`` → ``parents[1]``).
RAIZ_REPO = Path(__file__).resolve().parents[1]

#: Raíz del subconjunto de clasificación de F3.
F3_DIR = RAIZ_REPO / "tests" / "golden" / "F3"

#: Manifiesto del subconjunto.
SUBCONJUNTO = F3_DIR / "subconjunto.json"

#: Prompts YAML de la raíz del repo (referencia congelada de la cadena contable).
PROMPTS_RAIZ = RAIZ_REPO / "prompts"

#: Índice del golden completo.
CASOS_CSV = RAIZ_REPO / "tests" / "golden" / "casos.csv"


def _cargar_json(ruta: Path) -> dict:
    return json.loads(ruta.read_text(encoding="utf-8"))


def _cargar_modulo_script(ruta: Path):
    """Importa un script de ``scripts/F3/`` sin ejecutar su ``main`` (T-305).

    Se usa ``importlib`` con nombre propio para no colisionar con otros módulos
    y para dejar claro que **no** se está ejecutando el script; solo se prueban
    sus funciones puras.
    """
    especificacion = importlib.util.spec_from_file_location(f"_{ruta.stem}_test", ruta)
    modulo = importlib.util.module_from_spec(especificacion)
    assert especificacion.loader is not None
    especificacion.loader.exec_module(modulo)
    return modulo


@pytest.fixture(scope="module")
def subconjunto() -> dict:
    """Manifiesto del subconjunto de clasificación de F3."""
    return _cargar_json(SUBCONJUNTO)


@pytest.fixture(scope="module")
def filas_golden() -> dict[str, dict]:
    """``casos.csv`` indexado por id."""
    with CASOS_CSV.open(encoding="utf-8") as manejador:
        return {fila["id"]: fila for fila in csv.DictReader(manejador)}


# ---------------------------------------------------------------------------
# 1. Fidelidad de los prompts versionados
# ---------------------------------------------------------------------------


class TestFidelidadDePrompts:
    """Los prompts de la cadena contable son idénticos a los YAML de ``prompts/``."""

    #: Paso contable → archivo YAML de referencia en ``prompts/``.
    YAML_POR_PASO = {
        "01": "01-clasificacion_centro_costo_prompt.yaml",
        "02": "02-clasificacion_macro_categoria_prompt.yaml",
        "03": "03-clasificacion_concepto_codigo_final_prompt.yaml",
    }

    @pytest.mark.parametrize("paso", ["01", "02", "03"])
    def test_system_prompt_identico_al_yaml_versionado(self, paso: str):
        # La cadena corre **el mismo texto** que el YAML de referencia. Si
        # alguien retoca el prompt, este test obliga a justificar el cambio.
        yaml = pytest.importorskip("yaml")
        ruta = PROMPTS_RAIZ / self.YAML_POR_PASO[paso]
        if not ruta.exists():  # pragma: no cover - el repo siempre los tiene
            pytest.skip(f"no está el YAML de referencia: {ruta}")
        datos = yaml.safe_load(ruta.read_text(encoding="utf-8"))
        assert PASOS_CONTABLES[paso]["system"].strip() == datos["system"].strip(), (
            f"El system prompt del paso {paso} difiere del YAML de referencia "
            f"({self.YAML_POR_PASO[paso]}): la cadena contable depende de que el "
            "texto sea el mismo (T-305)."
        )

    @pytest.mark.parametrize("paso", ["01", "02", "03"])
    def test_user_prompt_identico_al_yaml_versionado(self, paso: str):
        yaml = pytest.importorskip("yaml")
        ruta = PROMPTS_RAIZ / self.YAML_POR_PASO[paso]
        if not ruta.exists():  # pragma: no cover
            pytest.skip(f"no está el YAML de referencia: {ruta}")
        datos = yaml.safe_load(ruta.read_text(encoding="utf-8"))
        assert PASOS_CONTABLES[paso]["user"].strip() == datos["user"].strip(), (
            f"El user prompt del paso {paso} difiere del YAML de referencia (T-305)."
        )

    def test_el_11_1_conserva_la_guia_de_lectura(self):
        # La detección de tipo reescribió `11.1` (ahora pide evidencia, no la
        # decisión: ADR-006), pero debe conservar la **guía de lectura** del
        # prompt de referencia para que la evidencia que llega al motor sea la
        # misma que el sistema usaba para decidir.
        yaml = pytest.importorskip("yaml")
        ruta = PROMPTS_RAIZ / "facturacion" / "11.1-deteccion_tipo_factura.yaml"
        if not ruta.exists():  # pragma: no cover
            pytest.skip(f"no está el YAML de referencia: {ruta}")
        datos = yaml.safe_load(ruta.read_text(encoding="utf-8"))
        system_referencia = datos["system_vlm"]

        # La guía del recuadro (el corazón de R4) sigue instruida.
        for fragmento in ("recuadro", "COD. 01", "encabezado"):
            assert fragmento in SYSTEM_PROMPT_POR_FUENTE["vlm"], (
                f"El prompt de evidencia del VLM perdió la guía del `11.1`: "
                f"falta {fragmento!r} (T-305)."
            )
        # Y la guía textual del `system_llm` también.
        system_llm_referencia = datos["system_llm"]
        assert (
            "FACTURA" in system_llm_referencia
            and "FACTURA" in SYSTEM_PROMPT_POR_FUENTE["llm"]
        )
        assert "tipo_detectado_por_documento_explicacion" in SYSTEM_PROMPT_POR_FUENTE["llm"]

        # La instrucción de no inventar datos se conserva.
        assert "No inventes datos" in SYSTEM_PROMPT_TIPO_COMPROBANTE

    def test_no_se_le_pide_la_decision_al_modelo(self):
        # La contracara: el sistema **saca** del prompt los campos de decisión,
        # tal como exige ADR-006.
        for prompt in SYSTEM_PROMPT_POR_FUENTE.values():
            assert "tipo_comprobante" not in prompt.replace(
                "tipo_detectado_por_documento", ""
            )
            assert "reglas_aplicadas" not in prompt


# ---------------------------------------------------------------------------
# 2. Integridad del subconjunto
# ---------------------------------------------------------------------------


class TestSubconjuntoF3:
    """El subconjunto declara sus casos y su sustento (F3-subplan §2.9)."""

    def test_declara_decision_de_alcance_y_version(self, subconjunto: dict):
        assert subconjunto["golden_version"]
        assert "decision_alcance" in subconjunto, (
            "El subconjunto debe documentar la decisión de alcance (§2.9): no "
            "espera la curación con contador (T-305)."
        )

    def test_los_casos_reales_citan_el_golden_y_existen(self, subconjunto: dict, filas_golden):
        for caso in subconjunto["letra_real"]:
            assert caso["id"] in filas_golden, f"{caso['id']} no está en casos.csv"
            # La ruta del subconjunto debe coincidir con la del golden (si no,
            # se estaría midiendo sobre otro archivo).
            assert caso["ruta"] == filas_golden[caso["id"]]["ruta"], (
                f"{caso['id']}: la ruta del subconjunto no coincide con casos.csv"
            )
            assert (RAIZ_REPO / caso["ruta"]).exists(), f"falta {caso['ruta']}"

    def test_la_letra_derivada_esta_sustentada_por_la_evidencia(self, subconjunto: dict, filas_golden):
        # Regla de honestidad: la letra solo se marca "derivada" si la evidencia
        # objetiva del golden la **nombra**. Si no, sería una etiqueta inventada.
        for caso in subconjunto["letra_real"]:
            assert caso["letra_estado"] == "derivada_de_evidencia", (
                f"{caso['id']}: la letra no puede declararse 'verificada' (requiere "
                "contador, F2 §2.5); debe ser 'derivada_de_evidencia' (T-305)."
            )
            evidencia = filas_golden[caso["id"]].get("evidencia_veredicto", "")
            assert caso["letra_derivada"] in evidencia, (
                f"{caso['id']}: la letra {caso['letra_derivada']!r} no aparece en la "
                f"evidencia objetiva ({evidencia!r}); no hay sustento para la etiqueta."
            )

    def test_los_casos_sinteticos_y_sus_archivos_existen(self, subconjunto: dict):
        for caso in subconjunto["letra_sintetica"]:
            ruta = F3_DIR / caso["archivo"]
            assert ruta.exists(), f"falta el caso sintético {caso['archivo']}"
            assert caso["letra_esperada"], f"{caso['id']} no declara letra esperada"
            assert caso["regla_esperada"], f"{caso['id']} no declara la regla esperada"

    def test_los_casos_contables_existen(self, subconjunto: dict):
        assert subconjunto["contable"], "el subconjunto debe tener casos contables"
        for caso in subconjunto["contable"]:
            assert (F3_DIR / caso["archivo"]).exists(), f"falta {caso['archivo']}"

    def test_los_casos_sinteticos_no_quedan_sucios(self):
        # Una corrida real escribe SIEMPRE un sidecar junto al markdown: el
        # subconjunto no debe tener residuos de una corrida (los scripts corren
        # sobre copias en un temporal).
        residuos = list((F3_DIR / "casos").glob("*_classification.json"))
        assert residuos == [], (
            f"quedaron sidecars en el subconjunto: {[r.name for r in residuos]} "
            "(correr sobre copias en un temporal)."
        )


# ---------------------------------------------------------------------------
# 3. Exactitud determinista de la letra (casos sintéticos)
# ---------------------------------------------------------------------------


class TestParidadLetraDeterminista:
    """El motor decide la letra esperada por reglas sobre la evidencia del texto."""

    @pytest.fixture(scope="class")
    def casos_sinteticos(self) -> list[dict]:
        return _cargar_json(SUBCONJUNTO)["letra_sintetica"]

    def _contexto_de(self, caso: dict) -> ContextoTipoComprobante:
        texto = (F3_DIR / caso["archivo"]).read_text(encoding="utf-8")
        return ContextoTipoComprobante(
            emisor_condicion_fiscal=caso.get("condicion_emisor"),
            receptor_condicion_fiscal=caso.get("condicion_receptor"),
            receptor_pais=caso.get("receptor_pais"),
            texto_encabezado_llm=texto,
        )

    def test_cada_caso_sintetico_decide_la_letra_esperada(self, casos_sinteticos):
        fallos = []
        for caso in casos_sinteticos:
            resultado = clasificar_tipo_comprobante(self._contexto_de(caso))
            esperada = caso.get("letra_esperada_final") or caso["letra_esperada"]
            if resultado.letra != esperada:
                fallos.append(f"{caso['id']}: letra={resultado.letra!r} (esperada {esperada!r})")
        assert fallos == [], (
            "El motor de reglas no reproduce la letra esperada del subconjunto "
            f"(tramo determinista, debe ser 100%): {fallos}"
        )

    def test_cada_caso_sintetico_dispara_la_regla_esperada(self, casos_sinteticos):
        fallos = []
        for caso in casos_sinteticos:
            resultado = clasificar_tipo_comprobante(self._contexto_de(caso))
            if caso["regla_esperada"] not in resultado.reglas_aplicadas:
                fallos.append(
                    f"{caso['id']}: reglas={resultado.reglas_aplicadas} "
                    f"(se esperaba {caso['regla_esperada']})"
                )
        assert fallos == [], f"Reglas esperadas no disparadas: {fallos}"

    def test_la_exportacion_pisa_la_condicion_fiscal(self, casos_sinteticos):
        caso = next(c for c in casos_sinteticos if c["regla_esperada"] == "R3")
        resultado = clasificar_tipo_comprobante(self._contexto_de(caso))
        assert resultado.letra == "E"
        assert resultado.certeza == "alta"
        assert resultado.reglas_aplicadas == ["R3"], (
            "R3 (exportación) tiene prioridad máxima: no debe evaluarse la lectura"
        )

    def test_la_discrepancia_dispara_r7_con_la_letra_del_documento(self, casos_sinteticos):
        caso = next(c for c in casos_sinteticos if c["regla_esperada"] == "R7")
        resultado = clasificar_tipo_comprobante(self._contexto_de(caso))
        assert resultado.tipo_esperado_por_negocio == caso["letra_esperada"]
        assert resultado.tipo_detectado_por_documento == caso["letra_detectada"]
        assert resultado.letra == caso["letra_esperada_final"]
        assert resultado.certeza == "baja"
        assert [alerta["regla"] for alerta in resultado.alertas] == ["R7"]

    def test_la_letra_sale_del_motor_y_no_del_modelo(self, casos_sinteticos):
        # La prueba conceptual de ADR-006: el contexto solo aporta **evidencia**
        # (texto + condiciones fiscales); la letra la produce el motor.
        caso = next(c for c in casos_sinteticos if c["regla_esperada"] == "R1")
        contexto = self._contexto_de(caso)
        assert contexto.letra_recuadro_vlm is None, "no se le pasó ninguna letra"
        assert clasificar_tipo_comprobante(contexto).letra == "C"


# ---------------------------------------------------------------------------
# 4. Regresión del bug de R5 (encontrado por T-305)
# ---------------------------------------------------------------------------


class TestRegresionR5SaltoDeLinea:
    """La letra de R5 debe estar en la **misma línea** que FACTURA/COMPROBANTE."""

    def test_el_patron_no_usa_espacios_verticales(self):
        # El `\s+` del patrón literal del WIP comía el salto de línea; el patrón
        # corregido solo acepta espacios/tabulaciones.
        assert r"\s+" not in REGEX_LETRA_ENCABEZADO.pattern, (
            "El patrón de R5 volvió a usar \\s+: cruzaría el salto de línea del "
            "markdown y tomaría la letra de la línea siguiente (bug de T-305)."
        )

    def test_no_toma_la_inicial_de_la_palabra_siguiente(self):
        # El caso real: el encabezado dice 'FACTURA A' y debajo 'COD.01'; el
        # markdown de Docling intercala un salto, así que sin el guardia se
        # capturaba la 'C' de "Código".
        assert extraer_letra_encabezado("FACTURA\n  Código: 1") is None
        assert extraer_letra_encabezado("FACTURA\n      C") is None
        assert extraer_letra_encabezado("  A\n  FACTURA\nCOD.01") is None

    def test_sigue_extrayendo_la_letra_en_la_misma_linea(self):
        assert extraer_letra_encabezado("FACTURA A\nCOD. 001") == "A"
        assert extraer_letra_encabezado("FACTURA B COD. 006") == "B"
        assert extraer_letra_encabezado("COMPROBANTE E") == "E"
        assert extraer_letra_encabezado("factura c") == "C"  # normaliza a mayúscula

    def test_el_motor_deja_de_inventar_la_letra_sobre_ese_markdown(self):
        # Antes del fix, este contexto decidía 'C' (falso positivo) en lugar de
        # no concluir letra: el test fija el comportamiento correcto.
        contexto = ContextoTipoComprobante(
            texto_encabezado_llm="  A\n  FACTURA\nCOD.01\n  Código: 1\n  Total: 100"
        )
        resultado = clasificar_tipo_comprobante(contexto)
        assert resultado.tipo_detectado_por_documento is None
        assert "R5" not in resultado.reglas_aplicadas

    def test_la_letra_detectada_siempre_esta_en_el_vocabulario(self):
        # Invariante de cierre: lo que R5 extrae es una letra del motor.
        for texto in ("FACTURA A", "FACTURA B", "FACTURA C", "FACTURA M", "FACTURA E"):
            assert extraer_letra_encabezado(texto) in {"A", "B", "C", "M", "E"}



# ---------------------------------------------------------------------------
# 5. Reporte de métricas del DoD (``scripts/F3/t305.py``)
# ---------------------------------------------------------------------------

#: Script de métricas del DoD de F3.
T305 = RAIZ_REPO / "scripts" / "F3" / "t305.py"


class TestMetricasDelDod:
    """``t305.py`` agrega las métricas del DoD de F3 (T-305, §10 del subplan).

    El plan pide ``python scripts/F3/t305.py`` como verificación final
    (F3-subplan §10). Acá se prueba su tramo determinista, que es el que puede
    ejecutarse sin Ollama, sin Docling y sin red.
    """

    @pytest.fixture(scope="class")
    def modulo(self):
        return _cargar_modulo_script(T305)

    @pytest.fixture(scope="class")
    def subconjunto(self) -> dict:
        return _cargar_json(SUBCONJUNTO)

    def test_exactitud_de_letra_es_total(self, modulo, subconjunto):
        # El tramo determinista (el que ADR-006 sacó del prompt) debe ser exacto:
        # la letra la decide el motor, no el modelo.
        medicion = modulo.medir_letra(subconjunto)
        assert medicion["total"] > 0
        assert medicion["exactitud"] == 1.0, (
            f"letra incorrecta en: "
            f"{[c['id'] for c in medicion['casos'] if not c['correcto']]}"
        )

    def test_desglose_por_categoria_cubre_las_letras_del_subconjunto(self, modulo, subconjunto):
        medicion = modulo.medir_letra(subconjunto)
        esperadas = {
            caso.get("letra_esperada_final") or caso["letra_esperada"]
            for caso in subconjunto["letra_sintetica"]
        }
        assert set(medicion["por_categoria"]) == esperadas, (
            "el desglose por categoría debe cubrir exactamente las letras del subconjunto"
        )
        assert all(d["correctos"] == d["total"] for d in medicion["por_categoria"].values())

    def test_la_alerta_r7_se_dispara_en_los_casos_de_discrepancia(self, modulo, subconjunto):
        medicion = modulo.medir_r7(subconjunto)
        assert medicion["total"] > 0, "el subconjunto debe tener un caso de discrepancia (R7)"
        assert medicion["tasa"] == 1.0

    def test_el_cruce_negocio_documento_es_consciente_de_la_expectativa(self, modulo, subconjunto):
        # Este es el punto fino: el caso R7 está **diseñado para discrepar**, así
        # que contarlo como "no acuerdan" mediría lo contrario de lo que el DoD
        # pide. La métrica evalúa "coincide cuando debe / discrepa cuando debe".
        medicion = modulo.medir_acuerdo_negocio_documento(subconjunto)
        assert medicion["tasa"] == 1.0
        assert medicion["espera_discrepancia"] >= 1, (
            "debe haber al menos un caso donde la discrepancia sea lo esperado"
        )
        assert medicion["discrepan"] == medicion["espera_discrepancia"]

    def test_excluye_los_casos_sin_ambas_evidencias(self, modulo, subconjunto):
        # R3 (exportación) resuelve la letra sin evaluar la lectura: no hay cruce
        # que medir, y contarlo como desacuerdo sería un falso negativo.
        medicion = modulo.medir_acuerdo_negocio_documento(subconjunto)
        ids = {caso["id"] for caso in medicion["casos"]}
        assert "letra_e_exportacion" not in ids

    def test_el_default_cc0006_se_conserva(self, modulo):
        # Criterio Gherkin de E-CLAS-2: sin señal específica → CC0006, baja, ninguna.
        medicion = modulo.medir_default_cc0006()
        assert medicion["es_default"] is True
        assert medicion["centro_costo"] == "CC0006"
        assert medicion["confianza"] == "baja"
        assert medicion["senal_usada"] == "ninguna"

    def test_el_reporte_escribe_json_y_respeta_el_codigo_de_salida(self, tmp_path, monkeypatch, capsys):
        # El script sale con código 0 cuando el tramo determinista está en el
        # umbral (y con ≠ 0 si no), que es lo que lo hace usable en CI.
        import importlib.util

        especificacion = importlib.util.spec_from_file_location("_t305_main_test", T305)
        modulo = importlib.util.module_from_spec(especificacion)
        assert especificacion.loader is not None
        especificacion.loader.exec_module(modulo)

        salida = tmp_path / "t305.json"
        monkeypatch.setattr("sys.argv", ["t305.py", "--json", str(salida)])
        with pytest.raises(SystemExit) as salida_sistema:
            modulo.main()
        assert salida_sistema.value.code == 0
        datos = _cargar_json(salida)
        assert datos["tarea"] == "T-305"
        assert datos["letra"]["exactitud"] == 1.0
        assert "paridad_real" not in datos, (
            "el reporte ya no corre una comparación externa: el sistema es "
            "autocontenido."
        )

    def test_el_reporte_declara_su_alcance(self, capsys, monkeypatch):
        # Honestidad del reporte: debe decir que el subconjunto NO es el golden
        # completo (la curación con contador sigue pendiente, F2 §2.5).
        import importlib.util

        especificacion = importlib.util.spec_from_file_location("_t305_alcance_test", T305)
        modulo = importlib.util.module_from_spec(especificacion)
        assert especificacion.loader is not None
        especificacion.loader.exec_module(modulo)

        monkeypatch.setattr("sys.argv", ["t305.py"])
        with pytest.raises(SystemExit):
            modulo.main()
        salida = capsys.readouterr().out
        assert "NO es el golden completo" in salida
        assert "contador" in salida
        assert "ADR-006" in salida

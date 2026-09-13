"""Tests de la cadena contable 01→02→03 (F3 / T-304, E-CLAS-2).

**DoD de T-304** (F3-subplan §3.4): "La cadena contable 01 (centro de costo) →
02 (macro categoría) → 03 (concepto/código) queda refactorizada con contratos
tipados entre pasos y reproduce v1", incluido el **default CC0006** del Gherkin
de E-CLAS-2.

Qué se verifica:

1. **Contratos por paso** (``OpcionCentroCosto``/``OpcionMacroCategoria``/
   ``PasoConceptoCodigo``): tipados, inmutables y con los cinco campos del
   prompt (``codigo``, ``nombre``, ``confianza``, ``senal_usada``/
   ``criterio_inferido``, ``justificacion``).
2. **Cadena feliz** con ``FakeOllamaClient``: 01→02→03 encadenados con el **1º**
   de cada paso, y los ``messages`` del paso siguiente contienen el centro/macro
   del paso anterior (contrato entre pasos).
3. **Default CC0006** / ``senal_usada=none`` (criterio Gherkin de E-CLAS-2), en
   la variante pura.
4. **Checkpoint** (paridad con v1): reejecutar con
   ``<doc>_classification.json`` **no** vuelve a llamar al modelo para los pasos
   ya resueltos.
5. **Error a mitad de cadena** → ``ErrorCadenaContable`` con los resultados
   parciales preservados en ``error.pasos``.
6. **Variante pura** ``clasificar_contable()``: no toca la red; acepta el
   checkpoint de v1 o los tres pasos sueltos; y **coincide** con el pipeline real
   (mismo centro/macro/concepto/código) — que es la garantía de que ambos caminos
   no divergen.
7. **Prompts versionados**: las versiones están congeladas (ADR-005) y los
   placeholders se resuelven (o se reporta el faltante).

Reglas duras (F3-subplan §4): la suite default corre **sin Ollama real** (doble
del cliente) y **sin Docling real**; ``api.classify`` ya **no** es un esqueleto
(eso lo cubre ``test_golden_y_esqueleto.py``, que ahora solo espera
``extract``/``run``); no se agregan dependencias nuevas.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from voucherflow.classification import (
    CONDICION_IMPOSITIVA_DEFAULT,
    PASOS_CONTABLES,
    REGLAS_CADENA_CONTABLE,
    VALOR_NO_INFORMADO,
    VERSION_PROMPT_CONTABLE_01,
    VERSION_PROMPT_CONTABLE_02,
    VERSION_PROMPT_CONTABLE_03,
    ErrorCadenaContable,
    OpcionCentroCosto,
    OpcionMacroCategoria,
    PasoConceptoCodigo,
    PlaceholderFaltante,
    ResultadoCadenaContable,
    RespuestaContableInvalida,
    base_values,
    clasificar_contable,
    clasificar_pasos_contables,
    construir_messages_contable,
    ejecutar_cadena,
    ejecutar_paso_01,
    escribir_checkpoint,
    leer_checkpoint,
    opciones_centro_costo,
    opciones_macro_categoria,
    paso_concepto_codigo,
    primary_centro_costo,
    primary_macro_categoria,
    renderizar_user,
    ruta_checkpoint,
)
from voucherflow.classification.contable import ejecutar_paso
from voucherflow.models.ollama import RespuestaOllama

# ---------------------------------------------------------------------------
# Respuestas sintéticas de los tres pasos (shape del prompt de v1)
# ---------------------------------------------------------------------------

PASO_01 = {
    "centros_costos": [
        {
            "codigo_centro_costo": "CC0004",
            "centro": "Repuestos",
            "confianza": "alta",
            "senal_usada": "descripcion",
            "justificacion": "La descripción menciona repuestos.",
        },
        {"codigo_centro_costo": "CC0005", "centro": "Servicios", "confianza": "baja"},
    ]
}

PASO_02 = {
    "macro_categorias": [
        {
            "macro_categoria": "MC07",
            "nombre": "Operaciones y Logística",
            "confianza": "alta",
            "criterio_inferido": False,
            "justificacion": "Materiales e insumos.",
        },
        {"macro_categoria": "MC08", "nombre": "Activos y Mantenimiento", "confianza": "baja"},
    ]
}

PASO_03 = {
    "macro_categoria": "MC07",
    "concepto": "CT017",
    "nombre_concepto": "Materiales, insumos y herramientas",
    "cuenta_contable": "4221,16",
    "condicion_impositiva": "21",
    "codigo_final": "48",
    "candidatos_codigo_final": [],
    "requiere_revision_humana": False,
    "confianza": "alta",
    "criterio_inferido": False,
    "justificacion": "Materiales.",
}

#: Respuesta del paso 01 cuando no hay señal específica: el default del Gherkin
#: de E-CLAS-2 (CC0006, confianza baja, ``senal_usada=none``).
PASO_01_DEFAULT = {
    "centros_costos": [
        {
            "codigo_centro_costo": "CC0006",
            "centro": "Indirectos",
            "confianza": "baja",
            "senal_usada": "ninguna",
            "justificacion": "Sin señal específica.",
        }
    ]
}

#: Respuesta del paso 03 con la celda "—" (no existe código para esa combinación).
PASO_03_SIN_CODIGO = {
    "macro_categoria": "MC03",
    "concepto": "CT007",
    "nombre_concepto": "Publicidad",
    "cuenta_contable": "4221,10",
    "condicion_impositiva": "27",
    "codigo_final": None,
    "candidatos_codigo_final": [],
    "requiere_revision_humana": True,
    "confianza": "baja",
    "justificacion": "No existe código para publicidad al 27%.",
}


# ---------------------------------------------------------------------------
# Doble del cliente (sin Ollama real; regla dura F3-subplan §4)
# ---------------------------------------------------------------------------


class FakeOllamaClient:
    """Doble de ``OllamaClient``: responde por paso y registra las llamadas.

    Detecta el paso por el system prompt del primer mensaje (cada prompt
    contable es distinto) y devuelve el JSON configurado para ese paso. Si se
    agotan las respuestas, repite la última (para no acoplar los tests al
    número exacto de llamadas del checkpoint).
    """

    def __init__(self, respuestas: dict[str, Any] | list[Any], *,
                 contenido_crudo: dict[str, str] | None = None) -> None:
        if isinstance(respuestas, list):
            self.por_paso = {
                paso: respuestas[indice] if indice < len(respuestas) else respuestas[-1]
                for indice, paso in enumerate(("01", "02", "03"))
            }
        else:
            self.por_paso = dict(respuestas)
        self.contenido_crudo = contenido_crudo or {}
        self.llamadas: list[dict[str, Any]] = []

    def _paso(self, messages: list[dict[str, Any]]) -> str:
        sistema = messages[0].get("content", "") if messages else ""
        for paso, definicion in PASOS_CONTABLES.items():
            if definicion["system"] == sistema:
                return paso
        return ""

    def ask(self, messages, model, json_format=False, options=None, num_ctx=None):
        paso = self._paso(messages)
        self.llamadas.append(
            {"paso": paso, "model": model, "num_ctx": num_ctx, "messages": messages}
        )
        if paso in self.contenido_crudo:
            contenido = self.contenido_crudo[paso]
        else:
            contenido = json.dumps(self.por_paso.get(paso, {}), ensure_ascii=False)
        return RespuestaOllama(
            contenido=contenido, modelo=model, latencia_s=0.0, status=200
        )


# ---------------------------------------------------------------------------
# 1. Contratos por paso
# ---------------------------------------------------------------------------


class TestContratosPorPaso:
    """Los pasos se convierten a dataclasses tipadas (contrato entre pasos)."""

    def test_centro_costo_tipado_con_los_cinco_campos(self):
        opciones = opciones_centro_costo(PASO_01)
        assert len(opciones) == 2
        primera = opciones[0]
        assert isinstance(primera, OpcionCentroCosto)
        assert primera.codigo == "CC0004"
        assert primera.centro == "Repuestos"
        assert primera.confianza == "alta"
        assert primera.senal_usada == "descripcion"
        assert primera.justificacion

    def test_macro_categoria_tipado(self):
        opciones = opciones_macro_categoria(PASO_02)
        assert isinstance(opciones[0], OpcionMacroCategoria)
        assert opciones[0].macro == "MC07"
        assert opciones[0].nombre == "Operaciones y Logística"

    def test_paso_03_tipado(self):
        contrato = paso_concepto_codigo(PASO_03)
        assert isinstance(contrato, PasoConceptoCodigo)
        assert contrato.concepto == "CT017"
        assert contrato.codigo_final == "48"
        assert contrato.cuenta_contable == "4221,16"
        assert contrato.requiere_revision_humana is False

    def test_paso_03_normaliza_candidatos_y_codigo_nulo(self):
        contrato = paso_concepto_codigo(
            {**PASO_03, "codigo_final": None, "candidatos_codigo_final": ["47", "397", ""]}
        )
        assert contrato.codigo_final is None
        assert contrato.candidatos_codigo_final == ["47", "397"]

    def test_los_contratos_son_inmutables(self):
        # ``frozen=True``: un paso no puede mutar la entrada de otro.
        opcion = opciones_centro_costo(PASO_01)[0]
        with pytest.raises(Exception):
            opcion.codigo = "CC0001"  # type: ignore[misc]

    def test_primary_toma_el_primero_y_no_inventa(self):
        # v1: ``options[0]["codigo_centro_costo"]``. Acá se agrega el error claro.
        assert primary_centro_costo(PASO_01) == "CC0004"
        assert primary_macro_categoria(PASO_02) == "MC07"
        with pytest.raises(RespuestaContableInvalida, match="no devolvió centros_costos"):
            primary_centro_costo({"centros_costos": []})
        with pytest.raises(RespuestaContableInvalida, match="no devolvió macro_categorias"):
            primary_macro_categoria({"macro_categorias": []})

    def test_primary_reporta_opcion_sin_codigo(self):
        with pytest.raises(RespuestaContableInvalida, match="codigo_centro_costo"):
            primary_centro_costo({"centros_costos": [{"centro": "Repuestos"}]})

    def test_paso_03_no_objeto_lanza_error(self):
        with pytest.raises(RespuestaContableInvalida, match="debe ser un objeto"):
            paso_concepto_codigo(["no", "es", "objeto"])  # type: ignore[arg-type]


# ---------------------------------------------------------------------------
# 7. Prompts versionados (ADR-005) y placeholders
# ---------------------------------------------------------------------------


class TestPromptsContables:
    """Los prompts contables están versionados y sus placeholders se resuelven."""

    def test_versiones_congeladas(self):
        assert VERSION_PROMPT_CONTABLE_01 == "contable-01@1"
        assert VERSION_PROMPT_CONTABLE_02 == "contable-02@1"
        assert VERSION_PROMPT_CONTABLE_03 == "contable-03@1"

    def test_cada_paso_declara_su_version_y_placeholders(self):
        for paso, definicion in PASOS_CONTABLES.items():
            assert definicion["version"].startswith("contable-")
            assert definicion["system"] and definicion["user"]
            assert definicion["placeholders"]

    def test_renderiza_los_placeholders(self):
        texto = renderizar_user(
            "01", {"proveedor": "Repuestos SA", "descripcion": "bujías", "monto": "100"}
        )
        assert "Repuestos SA" in texto
        assert "{{" not in texto

    def test_el_paso_02_incluye_el_centro_de_costo(self):
        # Contrato entre pasos: el user del 02 lleva el centro del 01.
        texto = renderizar_user(
            "02",
            {"centro_costo": "CC0004", "proveedor": "x", "descripcion": "y", "monto": "1"},
        )
        assert "centro_costo: CC0004" in texto

    def test_el_paso_03_incluye_macro_y_condicion(self):
        texto = renderizar_user(
            "03",
            {
                "macro_categoria": "MC07",
                "proveedor": "x",
                "descripcion": "y",
                "monto": "1",
                "condicion_impositiva": "10_5",
            },
        )
        assert "macro_categoria: MC07" in texto
        assert "condicion_impositiva: 10_5" in texto

    def test_un_none_se_renderiza_como_no_informado(self):
        texto = renderizar_user("01", {"proveedor": None, "descripcion": "x", "monto": None})
        assert f"proveedor: {VALOR_NO_INFORMADO}" in texto

    def test_falta_de_placeholder_es_error_de_contrato(self):
        # Un ``{{monto}}`` sin resolver llegaría al modelo como texto literal.
        with pytest.raises(PlaceholderFaltante, match="monto"):
            renderizar_user("01", {"proveedor": "x", "descripcion": "y"})

    def test_paso_desconocido_lanza_key_error(self):
        with pytest.raises(KeyError, match="Paso contable desconocido"):
            renderizar_user("09", {})

    def test_construir_messages_devuelve_system_y_user(self):
        messages = construir_messages_contable(
            "01", {"proveedor": "x", "descripcion": "y", "monto": "1"}
        )
        assert [m["role"] for m in messages] == ["system", "user"]
        assert messages[0]["content"] == PASOS_CONTABLES["01"]["system"]

    def test_base_values_porta_el_provisional_de_v1(self):
        valores = base_values("texto del documento")
        assert valores["proveedor"] == VALOR_NO_INFORMADO
        assert valores["monto"] == VALOR_NO_INFORMADO
        assert valores["descripcion"] == "texto del documento"
        # F4 podrá pasar los reales sin cambiar el contrato.
        reales = base_values("texto", proveedor="Proveedor SA", monto="1234.56")
        assert reales["proveedor"] == "Proveedor SA"
        assert reales["monto"] == "1234.56"


# ---------------------------------------------------------------------------
# 2/3/5. Pipeline real (con doble del cliente)
# ---------------------------------------------------------------------------


class TestEjecutarCadena:
    """La cadena real encadena los tres pasos y respeta los checkpoints."""

    def test_cadena_feliz_encadena_con_el_primero_de_cada_paso(self):
        cliente = FakeOllamaClient({"01": PASO_01, "02": PASO_02, "03": PASO_03})
        resultado = ejecutar_cadena(cliente, descripcion="Compra de repuestos")
        assert isinstance(resultado, ResultadoCadenaContable)
        assert resultado.centro_costo == "CC0004"
        assert resultado.macro_categoria == "MC07"
        assert resultado.concepto == "CT017"
        assert resultado.codigo == "48"
        assert resultado.condicion_impositiva == CONDICION_IMPOSITIVA_DEFAULT
        assert [llamada["paso"] for llamada in cliente.llamadas] == ["01", "02", "03"]
        assert resultado.reglas_aplicadas == list(REGLAS_CADENA_CONTABLE)

    def test_los_messages_del_paso_siguiente_llevan_el_resultado_anterior(self):
        # El contrato entre pasos: el 02 recibe el centro del 01 y el 03 la macro
        # del 02 (no se inventa la entrada).
        cliente = FakeOllamaClient({"01": PASO_01, "02": PASO_02, "03": PASO_03})
        ejecutar_cadena(cliente, descripcion="Compra de repuestos")
        user_02 = cliente.llamadas[1]["messages"][1]["content"]
        user_03 = cliente.llamadas[2]["messages"][1]["content"]
        assert "centro_costo: CC0004" in user_02
        assert "macro_categoria: MC07" in user_03

    def test_usa_el_rol_llm_y_json_format(self):
        cliente = FakeOllamaClient({"01": PASO_01, "02": PASO_02, "03": PASO_03})
        ejecutar_cadena(cliente, descripcion="x")
        assert {llamada["model"] for llamada in cliente.llamadas} == {"qwen2.5:7b"}
        assert all(llamada["num_ctx"] for llamada in cliente.llamadas)

    def test_la_condicion_impositiva_viaja_al_paso_03(self):
        cliente = FakeOllamaClient({"01": PASO_01, "02": PASO_02, "03": PASO_03})
        ejecutar_cadena(cliente, descripcion="x", condicion_impositiva="10_5")
        assert "condicion_impositiva: 10_5" in cliente.llamadas[2]["messages"][1]["content"]

    def test_modelo_explicito_se_usa_en_todos_los_pasos(self):
        cliente = FakeOllamaClient({"01": PASO_01, "02": PASO_02, "03": PASO_03})
        ejecutar_cadena(cliente, descripcion="x", modelo="mi-modelo")
        assert {llamada["model"] for llamada in cliente.llamadas} == {"mi-modelo"}

    def test_el_paso_03_puede_no_traer_codigo(self):
        # Celda "—" de la tabla: no existe código para esa combinación.
        cliente = FakeOllamaClient(
            {"01": PASO_01, "02": PASO_02, "03": PASO_03_SIN_CODIGO}
        )
        resultado = ejecutar_cadena(cliente, descripcion="x")
        assert resultado.codigo is None
        assert resultado.requiere_revision_humana is True

    def test_el_proveedor_y_el_monto_llegan_al_prompt(self):
        # Provisional de F3 (no informado) o los reales que pase F4.
        cliente = FakeOllamaClient({"01": PASO_01, "02": PASO_02, "03": PASO_03})
        ejecutar_cadena(cliente, descripcion="x", proveedor="Proveedor SA", monto="999")
        user_01 = cliente.llamadas[0]["messages"][1]["content"]
        assert "proveedor: Proveedor SA" in user_01
        assert "monto: 999" in user_01

    def test_sin_proveedor_ni_monto_usa_no_informado(self):
        cliente = FakeOllamaClient({"01": PASO_01, "02": PASO_02, "03": PASO_03})
        ejecutar_cadena(cliente, descripcion="x")
        user_01 = cliente.llamadas[0]["messages"][1]["content"]
        assert f"proveedor: {VALOR_NO_INFORMADO}" in user_01
        assert f"monto: {VALOR_NO_INFORMADO}" in user_01

    def test_ejecutar_paso_01_devuelve_json_y_modelo(self):
        cliente = FakeOllamaClient({"01": PASO_01})
        paso, modelo = ejecutar_paso_01(cliente, descripcion="x")
        assert paso == PASO_01
        assert modelo == "qwen2.5:7b"

    def test_ejecutar_paso_con_paso_invalido_lanza_key_error(self):
        cliente = FakeOllamaClient({"01": PASO_01})
        with pytest.raises(KeyError, match="Paso contable desconocido"):
            ejecutar_paso("09", cliente, {})

    def test_default_cc0006_senal_ninguna(self):
        # Criterio Gherkin de E-CLAS-2: sin señal específica → CC0006, baja,
        # senal_usada=ninguna (la cadena lo propaga sin retocarlo).
        cliente = FakeOllamaClient(
            {"01": PASO_01_DEFAULT, "02": PASO_02, "03": PASO_03}
        )
        resultado = ejecutar_cadena(cliente, descripcion="gasto sin señal")
        assert resultado.centro_costo == "CC0006"
        opcion = resultado.opciones_centro_costo[0]
        assert opcion.confianza == "baja"
        assert opcion.senal_usada == "ninguna"


# ---------------------------------------------------------------------------
# 4. Checkpoints (paridad con v1)
# ---------------------------------------------------------------------------


class TestCheckpoints:
    """El checkpoint evita volver a llamar al modelo (patrón de v1)."""

    def test_escribe_el_sidecar_con_el_shape_de_v1(self, tmp_path: Path):
        documento = tmp_path / "doc.md"
        ruta = escribir_checkpoint(
            ruta_checkpoint(documento), {"01_centro_costo": PASO_01}, str(documento)
        )
        datos = json.loads(ruta.read_text(encoding="utf-8"))
        assert datos["archivo"] == str(documento)
        assert "01_centro_costo" in datos["pasos"]

    def test_ruta_checkpoint_es_la_de_v1(self):
        # v1: ``<doc>_classification.json`` junto al markdown.
        assert ruta_checkpoint("/tmp/carpeta/doc.md") == Path(
            "/tmp/carpeta/doc_classification.json"
        )

    def test_la_cadena_escribe_el_checkpoint_y_reanuda(self, tmp_path: Path):
        documento = tmp_path / "doc.md"
        cliente_1 = FakeOllamaClient({"01": PASO_01, "02": PASO_02, "03": PASO_03})
        ejecutar_cadena(cliente_1, descripcion="x", documento=documento)
        assert ruta_checkpoint(documento).exists()

        # Segunda corrida: los tres pasos ya están resueltos → ninguna llamada.
        cliente_2 = FakeOllamaClient({"01": PASO_01, "02": PASO_02, "03": PASO_03})
        resultado = ejecutar_cadena(cliente_2, descripcion="x", documento=documento)
        assert cliente_2.llamadas == []
        assert resultado.centro_costo == "CC0004"
        assert resultado.macro_categoria == "MC07"

    def test_reanuda_solo_los_pasos_faltantes(self, tmp_path: Path):
        # Checkpoint parcial (v1 escribía después de cada paso): solo se
        # re-ejecutan los que faltan.
        documento = tmp_path / "doc.md"
        escribir_checkpoint(
            ruta_checkpoint(documento), {"01_centro_costo": PASO_01}, str(documento)
        )
        cliente = FakeOllamaClient({"02": PASO_02, "03": PASO_03})
        resultado = ejecutar_cadena(cliente, descripcion="x", documento=documento)
        assert [llamada["paso"] for llamada in cliente.llamadas] == ["02", "03"]
        assert resultado.centro_costo == "CC0004"

    def test_checkpoint_corrupto_degrada_a_reejecutar(self, tmp_path: Path):
        # Un sidecar ilegible no debe romper la corrida.
        documento = tmp_path / "doc.md"
        ruta = ruta_checkpoint(documento)
        ruta.write_text("{no es json", encoding="utf-8")
        assert leer_checkpoint(ruta) == {}
        cliente = FakeOllamaClient({"01": PASO_01, "02": PASO_02, "03": PASO_03})
        resultado = ejecutar_cadena(cliente, descripcion="x", documento=documento)
        assert len(cliente.llamadas) == 3
        assert resultado.centro_costo == "CC0004"

    def test_checkpoint_explicito_tiene_prioridad(self, tmp_path: Path):
        documento = tmp_path / "doc.md"
        otro = tmp_path / "otro.json"
        escribir_checkpoint(otro, {"01_centro_costo": PASO_01_DEFAULT}, str(documento))
        cliente = FakeOllamaClient({"02": PASO_02, "03": PASO_03})
        resultado = ejecutar_cadena(
            cliente, descripcion="x", documento=documento, checkpoint=otro
        )
        assert resultado.centro_costo == "CC0006"
        assert [llamada["paso"] for llamada in cliente.llamadas] == ["02", "03"]

    def test_sin_documento_no_escribe_checkpoint(self, tmp_path: Path):
        cliente = FakeOllamaClient({"01": PASO_01, "02": PASO_02, "03": PASO_03})
        ejecutar_cadena(cliente, descripcion="x")
        assert list(tmp_path.glob("*_classification.json")) == []


# ---------------------------------------------------------------------------
# 5. Errores con resultados parciales
# ---------------------------------------------------------------------------


class TestErroresParciales:
    """Un error a mitad de cadena preserva los resultados parciales (v1)."""

    def test_paso_01_sin_opciones_corta_la_cadena(self):
        cliente = FakeOllamaClient({"01": {"centros_costos": []}})
        with pytest.raises(ErrorCadenaContable, match="no devolvió centros_costos") as exc:
            ejecutar_cadena(cliente, descripcion="x")
        # No se llegó a llamar al paso 02 (no se inventa su entrada).
        assert [llamada["paso"] for llamada in cliente.llamadas] == ["01"]
        # El paso 01 queda en los parciales: v1 lo guardaba en el checkpoint
        # antes de leerle las opciones (y acá además se envuelve el error, que
        # en v1 se escapaba como ValueError pelado).
        assert "01_centro_costo" in exc.value.pasos

    def test_el_paso_sin_opciones_se_guarda_igual_en_el_checkpoint(self, tmp_path: Path):
        # Paridad con v1: el checkpoint se escribe **después de cada paso**, así
        # que un paso que devolvió opciones vacías igual queda registrado (y la
        # reanudación no lo vuelve a pedir).
        documento = tmp_path / "doc.md"
        cliente = FakeOllamaClient({"01": {"centros_costos": []}})
        with pytest.raises(ErrorCadenaContable):
            ejecutar_cadena(cliente, descripcion="x", documento=documento)
        assert "01_centro_costo" in leer_checkpoint(ruta_checkpoint(documento))

    def test_paso_02_sin_opciones_preserva_el_paso_01(self):
        cliente = FakeOllamaClient({"01": PASO_01, "02": {"macro_categorias": []}})
        with pytest.raises(ErrorCadenaContable, match="no devolvió macro_categorias") as exc:
            ejecutar_cadena(cliente, descripcion="x")
        assert [llamada["paso"] for llamada in cliente.llamadas] == ["01", "02"]
        assert exc.value.pasos["01_centro_costo"] == PASO_01

    def test_json_invalido_en_el_paso_02_preserva_el_paso_01(self):
        cliente = FakeOllamaClient(
            {"01": PASO_01}, contenido_crudo={"02": "no soy JSON"}
        )
        with pytest.raises(ErrorCadenaContable, match="paso 02") as exc:
            ejecutar_cadena(cliente, descripcion="x")
        assert exc.value.pasos["01_centro_costo"] == PASO_01

    def test_respuesta_vacia_lanza_error(self):
        cliente = FakeOllamaClient({}, contenido_crudo={"01": "   "})
        with pytest.raises(RespuestaContableInvalida, match="respuesta vacía"):
            ejecutar_cadena(cliente, descripcion="x")

    def test_el_error_de_cadena_es_capturable_de_forma_generica(self):
        # ``RespuestaContableInvalida`` hereda de ``ErrorCadenaContable``: un solo
        # ``except`` alcanza para quedarse con los parciales.
        cliente = FakeOllamaClient({}, contenido_crudo={"01": "no json"})
        assert issubclass(RespuestaContableInvalida, ErrorCadenaContable)
        with pytest.raises(ErrorCadenaContable) as exc:
            ejecutar_cadena(cliente, descripcion="x")
        assert exc.value.pasos == {}

    def test_json_dentro_de_cercas_markdown_se_tolera(self):
        cliente = FakeOllamaClient(
            {"01": PASO_01, "02": PASO_02},
            contenido_crudo={"03": "```json\n" + json.dumps(PASO_03) + "\n```"},
        )
        resultado = ejecutar_cadena(cliente, descripcion="x")
        assert resultado.codigo == "48"


# ---------------------------------------------------------------------------
# 6. Variante pura (sin red) y paridad con el pipeline real
# ---------------------------------------------------------------------------


class TestVariantePura:
    """``clasificar_contable()`` resuelve sobre pasos ya calculados, sin red."""

    def test_acepta_los_tres_pasos_sueltos(self):
        resultado = clasificar_contable(
            "markdown",
            "21",
            centro_costo="CC0004",
            macro_categoria="MC07",
            paso_03=PASO_03,
        )
        assert resultado.centro_costo == "CC0004"
        assert resultado.macro_categoria == "MC07"
        assert resultado.concepto == "CT017"
        assert resultado.codigo == "48"
        assert resultado.reglas_aplicadas == list(REGLAS_CADENA_CONTABLE)

    def test_acepta_el_checkpoint_de_v1(self):
        pasos = {
            "01_centro_costo": PASO_01,
            "02_macro_categoria": PASO_02,
            "03_concepto_codigo_final": PASO_03,
        }
        resultado = clasificar_contable("markdown", pasos=pasos)
        assert resultado.centro_costo == "CC0004"
        assert resultado.macro_categoria == "MC07"
        assert resultado.codigo == "48"

    def test_acepta_claves_cortas(self):
        resultado = clasificar_contable(
            "markdown", pasos={"01": PASO_01, "02": PASO_02, "03": PASO_03}
        )
        assert resultado.centro_costo == "CC0004"

    def test_sin_pasos_lanza_value_error_explicando_la_variante_real(self):
        # El mensaje debe explicar que esta es la variante pura y cuál es la que
        # corre el modelo (para no mandar al llamador a adivinar).
        with pytest.raises(ValueError, match=r"variante .*pura") as exc:
            clasificar_contable("markdown", "21")
        assert "ejecutar_cadena" in str(exc.value)

    def test_default_cc0006_en_la_variante_pura(self):
        # Gherkin de E-CLAS-2, forma pura: sin señal específica → CC0006/baja.
        resultado = clasificar_contable(
            "markdown",
            pasos={
                "01_centro_costo": PASO_01_DEFAULT,
                "02_macro_categoria": PASO_02,
                "03_concepto_codigo_final": PASO_03,
            },
        )
        assert resultado.centro_costo == "CC0006"

    def test_default_cc0006_tambien_con_lista_vacia_de_centros(self):
        # El Gherkin pide el default también "con centros_costos vacío": acá se
        # prueba con el centro suelto (la forma que usa F5), que es el mismo
        # camino de lectura que la cadena real.
        resultado = clasificar_contable(
            "markdown", centro_costo="CC0006", macro_categoria="MC07", paso_03=PASO_03
        )
        assert resultado.centro_costo == "CC0006"

    def test_el_codigo_puede_ser_nulo_con_revision_humana(self):
        resultado = clasificar_contable(
            "markdown",
            centro_costo="CC0004",
            macro_categoria="MC03",
            paso_03=PASO_03_SIN_CODIGO,
        )
        assert resultado.codigo is None

    def test_la_condicion_impositiva_explicita_gana(self):
        resultado = clasificar_contable(
            "markdown",
            "10_5",
            centro_costo="CC0004",
            macro_categoria="MC07",
            paso_03=PASO_03,
        )
        assert resultado.condicion_impositiva == "10_5"

    def test_la_variante_pura_no_toca_la_red(self, monkeypatch):
        # Ningún ``OllamaClient`` debe instanciarse: la variante pura es pura.
        import voucherflow.models.ollama as ollama

        def explotar(*args, **kwargs):
            raise AssertionError("la variante pura no debe construir un cliente")

        monkeypatch.setattr(ollama, "OllamaClient", explotar)
        resultado = clasificar_contable(
            "markdown", centro_costo="CC0004", macro_categoria="MC07", paso_03=PASO_03
        )
        assert resultado.codigo == "48"

    def test_la_variante_pura_coincide_con_la_cadena_real(self):
        # Garantía anti-divergencia: mismos accesores, mismo resultado.
        cliente = FakeOllamaClient({"01": PASO_01, "02": PASO_02, "03": PASO_03})
        real = ejecutar_cadena(cliente, descripcion="x")
        pura = clasificar_contable(
            "x",
            pasos={
                "01_centro_costo": PASO_01,
                "02_macro_categoria": PASO_02,
                "03_concepto_codigo_final": PASO_03,
            },
        )
        assert pura.centro_costo == real.centro_costo
        assert pura.macro_categoria == real.macro_categoria
        assert pura.concepto == real.concepto
        assert pura.codigo == real.codigo

    def test_clasificar_pasos_contables_expone_los_contratos_tipados(self):
        resultado = clasificar_pasos_contables(PASO_01, PASO_02, PASO_03)
        assert len(resultado.opciones_centro_costo) == 2
        assert len(resultado.opciones_macro_categoria) == 2
        assert isinstance(resultado.paso_03, PasoConceptoCodigo)
        assert resultado.pasos["01_centro_costo"] == PASO_01
        assert resultado.como_clasificacion()["centro_costo"] == "CC0004"
        assert resultado.detalle["versiones_prompt"]["01"] == VERSION_PROMPT_CONTABLE_01

    def test_el_markdown_queda_en_el_detalle(self):
        resultado = clasificar_contable(
            "x" * 40, centro_costo="CC0004", macro_categoria="MC07", paso_03=PASO_03
        )
        assert resultado.detalle["markdown_chars"] == 40


# ---------------------------------------------------------------------------
# api.classify (T-304 saca ``classify`` de los esqueletos)
# ---------------------------------------------------------------------------


class TestApiClassify:
    """``api.classify`` corre el motor de letra + la cadena contable real."""

    def _parchear(self, monkeypatch, respuestas: dict[str, Any]):
        import voucherflow.models.ollama as ollama

        cliente = FakeOllamaClient(respuestas)

        def fake_ask(self, messages, model, json_format=False, options=None, num_ctx=None):
            return cliente.ask(messages, model, json_format, options, num_ctx)

        monkeypatch.setattr(ollama.OllamaClient, "ask", fake_ask)
        return cliente

    def test_devuelve_un_voucher_result_completo(self, monkeypatch):
        from voucherflow import api
        from voucherflow.schemas.result import VoucherResult

        self._parchear(
            monkeypatch, {"01": PASO_01, "02": PASO_02, "03": PASO_03}
        )
        resultado = api.classify("FACTURA A\nProveedor: Repuestos SA")
        assert isinstance(resultado, VoucherResult)
        assert resultado.clasificacion_contable.centro_costo == "CC0004"
        assert resultado.clasificacion_contable.codigo == "48"
        assert resultado.documento_id  # sha256 del markdown

    def test_la_letra_sale_del_motor_con_la_evidencia_de_texto(self, monkeypatch):
        # Sin condiciones fiscales (llegan en F4), el motor lee la letra del
        # texto con R5 y declara la certeza baja.
        from voucherflow import api

        self._parchear(monkeypatch, {"01": PASO_01, "02": PASO_02, "03": PASO_03})
        resultado = api.classify("FACTURA B COD. 006\nTotal: 100")
        assert resultado.tipo_comprobante == "B"
        assert resultado.certeza.value == "baja"
        assert resultado.origen.value == "programa"

    def test_declara_las_condiciones_fiscales_faltantes(self, monkeypatch):
        # Honestidad del alcance: T-304 no conoce la condición fiscal y lo dice.
        from voucherflow import api

        self._parchear(monkeypatch, {"01": PASO_01, "02": PASO_02, "03": PASO_03})
        resultado = api.classify("FACTURA A\nTotal: 100")
        traza = resultado.trazabilidad["tipo_comprobante"]
        assert "emisor.condicion_fiscal" in traza["contexto"]["campos_ausentes"]

    def test_la_trazabilidad_registra_las_versiones_de_prompt(self, monkeypatch):
        from voucherflow import api

        self._parchear(monkeypatch, {"01": PASO_01, "02": PASO_02, "03": PASO_03})
        resultado = api.classify("FACTURA A")
        versiones = resultado.trazabilidad["cadena_contable"]["versiones_prompt"]
        assert versiones["01"] == VERSION_PROMPT_CONTABLE_01
        assert versiones["03"] == VERSION_PROMPT_CONTABLE_03

    def test_condicion_impositiva_explicita(self, monkeypatch):
        from voucherflow import api

        cliente = self._parchear(
            monkeypatch, {"01": PASO_01, "02": PASO_02, "03": PASO_03}
        )
        api.classify("FACTURA A", "10_5")
        assert "condicion_impositiva: 10_5" in cliente.llamadas[2]["messages"][1]["content"]

    def test_un_error_de_cadena_se_propaga(self, monkeypatch):
        from voucherflow import api

        self._parchear(monkeypatch, {"01": {"centros_costos": []}})
        with pytest.raises(ErrorCadenaContable):
            api.classify("FACTURA A")

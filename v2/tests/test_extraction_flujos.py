"""Tests de la extracción VLM+LLM en paralelo con contrato de evidencia (F4/T-401).

**DoD de T-401** (F4.md §3, E-EXT-1): "Ambos flujos (VLM sobre imagen, LLM sobre
OCR/Markdown) corren **en paralelo** y devuelven `SourceEvidence` con el contrato
de F0."

Qué se verifica, en el orden del entregable:

1. **El prompt de evidencia** (``prompt_extraccion.py``): versión congelada
   (``extraccion-key-value@1``, ADR-005), contrato por campo
   (``campos`` con ``valor`` + ``fragmento_sustento``) y la decisión
   explícitamente **fuera** del contrato (``comprobante_valido``,
   ``categoria_gasto``, ``centro_de_costo``…: ADR-001/ADR-006). Los ``messages``
   se construyen por fuente (imagen en base64 / markdown en ``content``).
2. **El intérprete** (``parsear_evidencia_extraccion``): no normaliza los valores
   (T-402), no inventa campos ausentes, tolera el JSON plano de v1 (``kvi``/
   ``kvg``), registra el campo sin sustento como debilidad y conserva el valor
   fuera del vocabulario **crudo** para auditoría.
3. **El contrato de F0** (``construir_source_evidence``): un ``EvidenceField``
   por campo con ``fragmento_sustento`` y ``meta.version_prompt``, y la pasada
   raw de T-303 publicando ``valida``/``debilidades``/``reglas_aplicadas``.
4. **Los dos flujos en paralelo** (``extraer_evidencia``/``extraer``): corren
   siempre los dos, se conservan **ambas** evidencias sin colapsar (ADR-001), el
   orden del resultado es determinista (no depende de qué hilo termina antes) y
   el paralelismo es **real** (dos llamadas lentas tardan ≈ el máximo, no la
   suma).
5. **Tolerancia a fallos**: una fuente que falla no tumba a la otra (el error
   queda en ``detalle['fallos']``); si fallan todas, ``ErrorExtraccion``.
6. **Los flujos públicos** (``flujo_vlm``/``flujo_llm``) devuelven
   ``SourceEvidence`` válidos, y ``combinar_evidencia`` sigue siendo el esqueleto
   de T-404.

Reglas duras (F4 / criterio de F1 §4 y F3 §4): la suite default corre **sin**
Ollama real (doble del lector) y **sin** Docling real (se inyecta una
``VistaPreparada`` de F2); no se rompen los contratos congelados de F0
(``flujo_vlm``/``flujo_llm``/``combinar_evidencia`` siguen existiendo con sus
firmas) ni se agregan dependencias nuevas.
"""

from __future__ import annotations

import json
import tempfile
import time
from typing import Any

import pytest

from voucherflow.extraction import (
    CAMPO_FUENTE_LECTURA,
    CAMPOS_CON_VOCABULARIO,
    CAMPOS_EXTRACCION,
    CAMPOS_FUERA_DEL_CONTRATO,
    CAMPOS_SOSTEN_NO_EVALUADO,
    CLAVE_CAMPOS,
    CLAVES_POR_CAMPO,
    FUENTES_EXTRACCION,
    VOCABULARIO_MONEDA,
    VOCABULARIO_TIPO_COMPROBANTE,
    VERSION_PROMPT_EXTRACCION,
    CampoLectura,
    ErrorEvidencia,
    ErrorExtraccion,
    EvidenciaExtraccion,
    ExtraccionEvidencia,
    campo_declarado_de_campo,
    construir_messages_extraccion,
    construir_source_evidence,
    ejecutar_flujo,
    extraer,
    extraer_evidencia,
    flujo_llm,
    flujo_vlm,
    parsear_evidencia_extraccion,
    veredicto_raw_de_evidencia,
)
from voucherflow.extraction.prompt_extraccion import (
    SYSTEM_PROMPT_EXTRACCION,
    SYSTEM_PROMPT_EXTRACCION_LLM,
    SYSTEM_PROMPT_EXTRACCION_VLM,
    SYSTEM_PROMPT_POR_FUENTE,
    USER_IMAGEN_EXTRACCION,
    USER_TEXTO_EXTRACCION,
)
from voucherflow.schemas.evidence import Fuente, SourceEvidence
from voucherflow.settings.config import Settings, cargar_desde_dict
from voucherflow.validation.vistas import VistaPreparada

# ---------------------------------------------------------------------------
# Dobles del lector (sin Ollama real; misma política que F2/T-202 y F3/T-302)
# ---------------------------------------------------------------------------


class FakeRespuesta:
    """Respuesta mínima de un lector (solo se usa ``contenido``)."""

    def __init__(self, contenido: str) -> None:
        self.contenido = contenido


class FakeLector:
    """Doble del ``OllamaClient``: responde por fuente y registra las llamadas.

    Expone la misma superficie que usan los flujos (``ask`` con ``messages``/
    ``model``/``json_format``/``num_ctx``). Detecta la fuente por el system prompt
    del primer mensaje (que es distinto para VLM y LLM) y devuelve el contenido
    configurado para esa fuente, o ``contenido`` como fallback. No toca la red.
    """

    def __init__(
        self,
        contenido: str = "{}",
        por_fuente: dict[str, str] | None = None,
        demora_s: float = 0.0,
    ) -> None:
        self.contenido = contenido
        self.por_fuente = por_fuente or {}
        self.demora_s = demora_s
        self.llamadas: list[dict[str, Any]] = []

    def _fuente(self, messages: list[dict[str, Any]]) -> str:
        sistema = messages[0].get("content", "") if messages else ""
        for fuente, prompt in SYSTEM_PROMPT_POR_FUENTE.items():
            if prompt == sistema:
                return fuente
        return ""

    def ask(
        self,
        messages,
        model,
        json_format=False,
        options=None,
        num_ctx=None,
    ):
        if self.demora_s:
            time.sleep(self.demora_s)
        fuente = self._fuente(messages)
        self.llamadas.append(
            {
                "fuente": fuente,
                "messages": messages,
                "model": model,
                "num_ctx": num_ctx,
                "json_format": json_format,
            }
        )
        return FakeRespuesta(self.por_fuente.get(fuente, self.contenido))


class LectorQueFalla:
    """Doble que falla siempre (para probar el aislamiento de fallos)."""

    def __init__(self, error: Exception | None = None) -> None:
        self.error = error or RuntimeError("Ollama no responde")

    def ask(self, *args, **kwargs):
        raise self.error


class LectorPorFuenteQueFalla(LectorQueFalla):
    """Falla solo para una fuente (por el system prompt) y delega en la otra."""

    def __init__(self, fuente_que_falla: str, contenido: str = "{}") -> None:
        super().__init__()
        self.fuente_que_falla = fuente_que_falla
        self.delegado = FakeLector(contenido)

    def ask(self, messages, *args, **kwargs):
        fuente = self.delegado._fuente(messages)
        if fuente == self.fuente_que_falla:
            raise self.error
        return self.delegado.ask(messages, *args, **kwargs)


# ---------------------------------------------------------------------------
# Vistas de F2 y respuestas sintéticas
# ---------------------------------------------------------------------------

#: PNG mínimo 1x1 válido: ``construir_messages_extraccion`` lee el archivo para
#: codificarlo en base64, así que la ruta tiene que existir de verdad.
_PNG_1PX = (
    b"\x89PNG\r\n\x1a\n"
    b"\x00\x00\x00\rIHDR\x00\x00\x00\x01\x00\x00\x00\x01\x08\x02\x00\x00\x00"
    b"\x90wS\xde\x00\x00\x00\x0cIDATx\x9cc\xf8\xcf\xc0\x00\x00\x00\x03\x00\x01"
    b"\x86\xa0\xb5\x9d\x00\x00\x00\x00IEND\xaeB`\x82"
)


def _archivo_imagen() -> str:
    """Escribe un PNG 1x1 temporal (válido) y devuelve su ruta."""
    tmp = tempfile.NamedTemporaryFile(suffix=".png", delete=False)
    tmp.write(_PNG_1PX)
    tmp.close()
    return tmp.name


def _vista_fiel() -> VistaPreparada:
    """Vista **fiel** de F2 con imagen (la entrada de la extracción, E-QWE-2)."""
    ruta = _archivo_imagen()
    return VistaPreparada(
        tipo_vista="fiel",
        calidad="alta",
        representacion=ruta,
        resolucion_objetivo=2048,
        origen=ruta,
        ruta_imagen_original=ruta,
        factor_escala=1.0,
        nota="Vista fiel (T-203/E-QWE-2)",
    )


def _vista_texto(markdown: str = "FACTURA A\nTotal: $12.345,67") -> VistaPreparada:
    """Vista de texto nativo (``ruta_imagen_original`` None)."""
    return VistaPreparada(
        tipo_vista="fiel",
        calidad="alta",
        representacion=markdown,
        resolucion_objetivo=0,
        origen="doc.pdf",
        ruta_imagen_original=None,
    )


def _campo(valor: Any = "A", sustento: str = "Recuadro 'A' y COD. 01") -> dict[str, Any]:
    """Campo con el shape del contrato (``valor`` + ``fragmento_sustento``)."""
    return {"valor": valor, "fragmento_sustento": sustento}


def _json_extraccion(**cambios: Any) -> str:
    """JSON de extracción con los defaults del contrato, ajustable por kwargs."""
    campos: dict[str, Any] = {
        "tipo_comprobante": _campo("A", "Recuadro grande con 'A' y COD. 01"),
        "cuit_emisor": _campo("20-12345678-9", "C.U.I.T. 20-12345678-9"),
        "razon_social_emisor": _campo("ACME S.A.", "ACME S.A."),
        "importe_total_facturado": _campo(12345.67, "Importe Total: $12.345,67"),
        "fecha_emision": _campo("2025-08-14", "Fecha: 14/08/2025"),
    }
    if "campos" in cambios:
        campos = cambios.pop("campos")
    datos: dict[str, Any] = {
        CAMPO_FUENTE_LECTURA: "vlm",
        CLAVE_CAMPOS: campos,
    }
    datos.update(cambios)
    return json.dumps(datos, ensure_ascii=False)


def _settings_dobles() -> Any:
    """``Settings`` de prueba con los roles ``vlm``/``llm`` (sin Ollama real)."""
    return cargar_desde_dict(
        {
            "modelos": {
                "vlm": {"rol": "vlm", "modelo": "vlm-doble", "num_ctx": 4096},
                "llm": {"rol": "llm", "modelo": "llm-doble", "num_ctx": 8192},
            }
        }
    )


# ---------------------------------------------------------------------------
# 1. El prompt de evidencia (contrato y versión)
# ---------------------------------------------------------------------------


class TestPromptExtraccion:
    """El prompt reporta evidencia por campo; la decisión queda fuera (ADR-001)."""

    def test_version_prompt_congelada(self):
        assert VERSION_PROMPT_EXTRACCION == "extraccion-key-value@1"

    def test_el_contrato_es_por_campo_con_valor_y_sustento(self):
        # El shape del contrato es el mismo de F3/T-302 (ADR-001): un objeto por
        # campo con el valor y su fragmento de sustento.
        assert CLAVE_CAMPOS == "campos"
        assert set(CLAVES_POR_CAMPO) == {"valor", "fragmento_sustento"}
        for prompt in (SYSTEM_PROMPT_EXTRACCION, SYSTEM_PROMPT_EXTRACCION_VLM, SYSTEM_PROMPT_EXTRACCION_LLM):
            assert "fragmento_sustento" in prompt
        assert CLAVE_CAMPOS in SYSTEM_PROMPT_EXTRACCION_VLM
        assert CLAVE_CAMPOS in SYSTEM_PROMPT_EXTRACCION_LLM

    def test_la_lista_de_campos_viaja_en_las_dos_guias(self):
        # El modelo necesita saber qué buscar; el vocabulario es el mismo en las
        # dos fuentes para que las evidencias sean comparables (ADR-002).
        for prompt in (SYSTEM_PROMPT_EXTRACCION_VLM, SYSTEM_PROMPT_EXTRACCION_LLM):
            for campo in ("cuit_emisor", "importe_total_facturado", "fecha_emision"):
                assert f"- {campo}" in prompt

    def test_el_ejemplo_declara_la_fuente_de_su_guia(self):
        # Un ejemplo que dijera "llm" dentro de la guía del VLM sería una
        # inconsistencia que el modelo podría copiar.
        assert '"fuente_lectura": "vlm"' in SYSTEM_PROMPT_EXTRACCION_VLM
        assert '"fuente_lectura": "llm"' in SYSTEM_PROMPT_EXTRACCION_LLM
        # La guía del VLM no debe declarar la fuente del LLM y viceversa.
        assert '"fuente_lectura": "llm"' not in SYSTEM_PROMPT_EXTRACCION_VLM

    def test_la_decision_queda_fuera_del_contrato(self):
        # ADR-001/ADR-006: los campos que el programa (o el negocio) resuelven no
        # se le piden al modelo de extracción.
        assert set(CAMPOS_EXTRACCION).isdisjoint(CAMPOS_FUERA_DEL_CONTRATO)
        for campo in (
            "comprobante_valido",
            "motivo_rechazo",
            "categoria_gasto",
            "centro_de_costo",
        ):
            assert campo in CAMPOS_FUERA_DEL_CONTRATO

    def test_el_prompt_declara_que_no_normaliza_ni_decide(self):
        # El prompt debe ser explícito: otra etapa normaliza (T-402) y decide.
        for prompt in SYSTEM_PROMPT_POR_FUENTE.values():
            assert "NO normalices" in prompt
            assert "Si devolvés esos campos, se ignoran" in prompt
            assert "No inventes datos" in prompt

    def test_conserva_las_correcciones_de_ocr_de_v1(self):
        # Lo único que v1 corregía y se conserva: O/0 dentro de palabras libres.
        assert "ROSARI0" in SYSTEM_PROMPT_EXTRACCION
        assert "No toques números" in SYSTEM_PROMPT_EXTRACCION

    def test_hay_un_system_prompt_por_fuente(self):
        assert set(SYSTEM_PROMPT_POR_FUENTE) == {"vlm", "llm"}
        # Las dos guías comparten la base: la única diferencia es el insumo.
        assert SYSTEM_PROMPT_EXTRACCION_VLM.startswith(SYSTEM_PROMPT_EXTRACCION)
        assert SYSTEM_PROMPT_EXTRACCION_LLM.startswith(SYSTEM_PROMPT_EXTRACCION)
        # El VLM describe lo que ve; el LLM copia el texto OCR literal.
        assert "descripción visual" in SYSTEM_PROMPT_EXTRACCION_VLM
        assert "texto OCR literal" in SYSTEM_PROMPT_EXTRACCION_LLM

    def test_los_campos_del_contrato_se_mencionan_en_el_prompt(self):
        # El prompt debe indicar los campos fiscales/comerciales a leer (E-EXT-3).
        for campo in ("cuit_emisor", "importe_total_facturado", "fecha_emision"):
            assert f"- {campo}" in SYSTEM_PROMPT_POR_FUENTE["llm"]
            assert f"- {campo}" in SYSTEM_PROMPT_POR_FUENTE["vlm"]


class TestConstruirMessages:
    """Los ``messages`` se construyen por fuente, sin tocar la red (T-401)."""

    def test_fuente_invalida_lanza_value_error(self):
        with pytest.raises(ValueError) as exc:
            construir_messages_extraccion(fuente="ocr", markdown="x")
        assert "fuente inválida" in str(exc.value)

    def test_vlm_lleva_la_imagen_en_base64(self):
        messages = construir_messages_extraccion(fuente="vlm", vista=_vista_fiel())
        assert messages[0]["role"] == "system"
        assert messages[0]["content"] == SYSTEM_PROMPT_EXTRACCION_VLM
        assert messages[1]["role"] == "user"
        assert messages[1]["content"] == USER_IMAGEN_EXTRACCION
        assert isinstance(messages[1]["images"], list)
        assert messages[1]["images"][0]  # base64 no vacío

    def test_vlm_sin_vista_o_sin_imagen_lanza_value_error(self):
        with pytest.raises(ValueError) as exc:
            construir_messages_extraccion(fuente="vlm", vista=None)
        assert "ruta_imagen_original" in str(exc.value)

        with pytest.raises(ValueError):
            construir_messages_extraccion(fuente="vlm", vista=_vista_texto())

    def test_llm_lleva_el_markdown_en_content(self):
        markdown = "FACTURA A\nCUIT: 20-12345678-9"
        messages = construir_messages_extraccion(fuente="llm", markdown=markdown)
        assert messages[0]["content"] == SYSTEM_PROMPT_EXTRACCION_LLM
        assert messages[1]["content"] == USER_TEXTO_EXTRACCION.replace(
            "{{documento}}", markdown
        )
        assert "images" not in messages[1]

    def test_llm_sin_markdown_lanza_value_error(self):
        with pytest.raises(ValueError) as exc:
            construir_messages_extraccion(fuente="llm", markdown="   ")
        assert "markdown" in str(exc.value)


# ---------------------------------------------------------------------------
# 2. El intérprete de la respuesta (sin normalizar valores, sin inventar)
# ---------------------------------------------------------------------------


class TestParsearEvidencia:
    """``parsear_evidencia_extraccion`` normaliza el envelope, no los valores."""

    def test_evidencia_completa_por_campo(self):
        ev = parsear_evidencia_extraccion(_json_extraccion(), fuente="vlm")
        assert ev.fuente == "vlm"
        assert set(ev.campos) == {
            "tipo_comprobante",
            "cuit_emisor",
            "razon_social_emisor",
            "importe_total_facturado",
            "fecha_emision",
        }
        assert ev.campos["cuit_emisor"].valor == "20-12345678-9"
        assert ev.campos["cuit_emisor"].fragmento == "C.U.I.T. 20-12345678-9"
        assert ev.campos["importe_total_facturado"].valor == 12345.67
        assert ev.problemas == []
        assert ev.valida is True

    def test_no_normaliza_los_valores(self):
        # T-402 es quien normaliza: acá el CUIT conserva los guiones y la fecha
        # su formato impreso (el valor crudo no se toca).
        ev = parsear_evidencia_extraccion(
            _json_extraccion(
                campos={
                    "cuit_emisor": _campo("20-1 Ing, Brutas: 201641", "CUIT 20-1"),
                    "fecha_emision": _campo("14/08/2025", "Fecha: 14/08/2025"),
                    "importe_total_facturado": _campo(
                        "12.345,67", "Importe Total: $12.345,67"
                    ),
                }
            ),
            fuente="llm",
        )
        assert ev.campos["cuit_emisor"].valor == "20-1 Ing, Brutas: 201641"
        assert ev.campos["fecha_emision"].valor == "14/08/2025"
        assert ev.campos["importe_total_facturado"].valor == "12.345,67"

    def test_valor_crudo_se_conserva(self):
        ev = parsear_evidencia_extraccion(
            _json_extraccion(campos={"moneda": _campo("ars", "Moneda: ARS")}),
            fuente="llm",
        )
        assert ev.campos["moneda"].valor_crudo == "ars"
        assert ev.campos["moneda"].valor == "ars"  # T-401 no normaliza

    def test_campo_ausente_no_se_inventa_y_se_lista(self):
        ev = parsear_evidencia_extraccion(
            _json_extraccion(
                fuente_lectura="llm",
                campos={"tipo_comprobante": _campo("A", "Recuadro 'A'")},
            ),
            fuente="llm",
        )
        assert set(ev.campos) == {"tipo_comprobante"}
        assert "cuit_emisor" in ev.campos_ausentes
        assert set(ev.campos_ausentes) == set(CAMPOS_EXTRACCION) - {"tipo_comprobante"}
        # La ausencia no es una debilidad: el prompt pide no inventar.
        assert ev.campos_sin_sustento == []
        assert ev.problemas == []
        assert ev.motivos_ausencia  # ...pero queda explicada para F5.

    def test_valor_nulo_o_vacio_es_ausente(self):
        ev = parsear_evidencia_extraccion(
            _json_extraccion(
                campos={
                    "tipo_comprobante": _campo("A", "Recuadro 'A'"),
                    "cuit_emisor": _campo(None, "CUIT ilegible"),
                    "razon_social_emisor": _campo("   ", "  "),
                }
            ),
            fuente="llm",
        )
        assert set(ev.campos) == {"tipo_comprobante"}
        assert "cuit_emisor" in ev.campos_ausentes
        assert "razon_social_emisor" in ev.campos_ausentes

    def test_campo_sin_sustento_es_debilidad(self):
        ev = parsear_evidencia_extraccion(
            _json_extraccion(
                campos={
                    "cuit_emisor": {"valor": "20-12345678-9"},
                }
            ),
            fuente="llm",
        )
        assert ev.campos["cuit_emisor"].fragmento == ""
        assert ev.campos["cuit_emisor"].con_sustento is False
        assert ev.campos_sin_sustento == ["cuit_emisor"]
        assert ev.valida is False

    def test_el_campo_sin_sustento_se_reporta_una_sola_vez(self):
        # El registro raw (T-303) ya lo dice con RAW_CAMPO para los campos que
        # evalúa: T-401 no debe duplicar la misma debilidad.
        ev = parsear_evidencia_extraccion(
            _json_extraccion(campos={"cuit_emisor": {"valor": "20-12345678-9"}}),
            fuente="llm",
        )
        source = construir_source_evidence(ev)
        assert "RAW_CAMPO" in source.reglas_aplicadas
        menciones = [d for d in source.debilidades if "sustento" in d]
        assert len(menciones) == 1, source.debilidades

    def test_valor_fuera_del_vocabulario_se_conserva_crudo(self):
        ev = parsear_evidencia_extraccion(
            _json_extraccion(campos={"tipo_comprobante": _campo("X", "Recuadro 'X'")}),
            fuente="vlm",
        )
        # El intérprete no valida el vocabulario ni inventa un valor válido:
        # conserva el crudo y la regla raw (T-303) es la que lo califica.
        assert ev.campos["tipo_comprobante"].valor == "X"
        assert ev.campos["tipo_comprobante"].valor_crudo == "X"
        source = construir_source_evidence(ev)
        assert source.valida is False
        assert "RAW_VOCABULARIO" in source.reglas_aplicadas

    def test_el_vocabulario_cerrado_ignora_capitalizacion_al_comparar(self):
        ev = parsear_evidencia_extraccion(
            _json_extraccion(campos={"moneda": _campo("ars", "Moneda: ARS")}),
            fuente="llm",
        )
        # "ars" pertenece al vocabulario (comparación case-insensitive)... aunque
        # el valor publicado sigue siendo el crudo "ars" (T-402 normaliza).
        assert ev.campos["moneda"].valor == "ars"
        assert not any("vocabulario" in p for p in ev.problemas)

    def test_tolera_el_shape_plano_de_v1(self):
        # Compatibilidad con los modos kvi/kvg de v1: JSON plano sin envoltorio.
        crudo = json.dumps(
            {
                "tipo_comprobante": "A",
                "cuit_emisor": "20-12345678-9",
                "importe_total_facturado": 12345.67,
            }
        )
        ev = parsear_evidencia_extraccion(crudo, fuente="llm")
        assert ev.campos["tipo_comprobante"].valor == "A"
        assert ev.campos["cuit_emisor"].valor == "20-12345678-9"
        # Sin sostento en el shape plano: v1 no lo pedía → todos débiles.
        assert all(not c.con_sustento for c in ev.campos.values())
        assert ev.valida is False

    def test_campos_extra_del_modo_generico_se_conservan(self):
        ev = parsear_evidencia_extraccion(
            _json_extraccion(
                campos={
                    "proveedor": _campo("ACME S.A.", "ACME S.A."),
                    "alicuota_21": _campo("21", "Alicuota 21%"),
                }
            ),
            fuente="llm",
        )
        assert set(ev.campos_extra) == {"proveedor", "alicuota_21"}
        assert "proveedor" not in CAMPOS_EXTRACCION  # es del modo genérico

    def test_campo_sin_nombre_se_ignora_con_problema(self):
        ev = parsear_evidencia_extraccion(
            _json_extraccion(campos={"": _campo("x"), "tipo_comprobante": _campo("A")}),
            fuente="llm",
        )
        assert "" not in ev.campos
        assert any("sin nombre" in p for p in ev.problemas)

    def test_claves_fuera_del_contrato_del_campo_se_ignoran_y_reportan(self):
        ev = parsear_evidencia_extraccion(
            _json_extraccion(
                campos={
                    "cuit_emisor": {
                        "valor": "20-12345678-9",
                        "fragmento_sustento": "C.U.I.T. 20-12345678-9",
                        "confianza": "alta",  # clave fuera del contrato
                    }
                }
            ),
            fuente="llm",
        )
        assert ev.campos["cuit_emisor"].valor == "20-12345678-9"
        assert any("fuera del contrato" in p for p in ev.problemas)

    def test_fuente_declarada_distinta_queda_anotada(self):
        ev = parsear_evidencia_extraccion(
            _json_extraccion(fuente_lectura="llm"), fuente="vlm"
        )
        assert ev.fuente_declarada == "llm"
        assert ev.fuente == "vlm"  # la fuente autoritativa la fija el orquestador
        assert any("fuente_lectura" in p for p in ev.problemas)

    def test_tolera_json_dentro_de_cercas_markdown(self):
        contenido = "```json\n" + _json_extraccion() + "\n```"
        ev = parsear_evidencia_extraccion(contenido, fuente="vlm")
        assert ev.campos["tipo_comprobante"].valor == "A"

    def test_tolera_prosa_alrededor_del_json(self):
        contenido = "Claro, acá está la extracción:\n" + _json_extraccion() + "\nSaludos."
        ev = parsear_evidencia_extraccion(contenido, fuente="vlm")
        assert ev.campos["tipo_comprobante"].valor == "A"

    def test_respuesta_malformada_conserva_el_crudo_para_diagnostico(self):
        # Nota de honestidad portada de v1: el token aislado que algunos modelos
        # emiten antes de la última llave no tiene una reparación fiable (se
        # comprobó al reproducirlo), así que se reporta el error y la respuesta
        # cruda queda disponible para el diagnóstico.
        contenido = '{\n "campos": {"cuit_emisor": {"valor": "20-1"\n "v"\n}'
        with pytest.raises(ErrorEvidencia) as exc:
            parsear_evidencia_extraccion(contenido, fuente="llm")
        assert "primeros 200 caracteres" in str(exc.value)

    def test_respuesta_vacia_o_json_invalido_lanza_errorevidencia(self):
        with pytest.raises(ErrorEvidencia):
            parsear_evidencia_extraccion("", fuente="vlm")
        with pytest.raises(ErrorEvidencia):
            parsear_evidencia_extraccion("no hay json acá", fuente="vlm")

    def test_json_no_objeto_lanza_errorevidencia(self):
        with pytest.raises(ErrorEvidencia):
            parsear_evidencia_extraccion("[1, 2, 3]", fuente="vlm")

    def test_fuente_invalida_lanza_value_error(self):
        with pytest.raises(ValueError):
            parsear_evidencia_extraccion(_json_extraccion(), fuente="ocr")

    def test_la_respuesta_cruda_se_conserva(self):
        contenido = _json_extraccion()
        ev = parsear_evidencia_extraccion(contenido, fuente="vlm")
        assert ev.crudo == contenido


# ---------------------------------------------------------------------------
# 3. El contrato de F0 (SourceEvidence + pasada raw de T-303)
# ---------------------------------------------------------------------------


class TestSourceEvidence:
    """``construir_source_evidence`` cumple ADR-001 campo a campo."""

    def test_un_evidence_field_por_campo_con_sustento(self):
        ev = parsear_evidencia_extraccion(_json_extraccion(), fuente="vlm")
        source = construir_source_evidence(ev, modelo="qwen2.5vl:3b")
        assert source.fuente is Fuente.vlm
        assert set(source.campos) == set(ev.campos)
        campo = source.campos["cuit_emisor"]
        assert campo.valor == "20-12345678-9"
        assert campo.fuente is Fuente.vlm
        assert campo.fragmento_sustento == "C.U.I.T. 20-12345678-9"
        assert campo.meta["version_prompt"] == VERSION_PROMPT_EXTRACCION
        assert campo.meta["modelo"] == "qwen2.5vl:3b"
        assert campo.meta["fuente_lectura"] == "vlm"

    def test_llm_mapea_a_fuente_llm_del_schema(self):
        ev = parsear_evidencia_extraccion(_json_extraccion(), fuente="llm")
        source = construir_source_evidence(ev)
        assert source.fuente is Fuente.llm
        assert all(c.fuente is Fuente.llm for c in source.campos.values())

    def test_la_evidencia_es_serializable_y_reconstruible(self):
        ev = parsear_evidencia_extraccion(_json_extraccion(), fuente="vlm")
        source = construir_source_evidence(ev)
        crudo = source.model_dump_json()
        assert SourceEvidence.model_validate_json(crudo) == source

    def test_sin_sustento_el_fragmento_declara_la_ausencia(self):
        # El contrato de F0 exige fragmento no vacío: no se puede silenciar la
        # ausencia, así que se escribe explícitamente (como hace F3/T-302).
        ev = parsear_evidencia_extraccion(
            _json_extraccion(campos={"cuit_emisor": {"valor": "20-12345678-9"}}),
            fuente="llm",
        )
        source = construir_source_evidence(ev)
        fragmento = source.campos["cuit_emisor"].fragmento_sustento
        assert fragmento.strip()
        assert "Sin fragmento de sustento" in fragmento
        assert source.campos["cuit_emisor"].confianza_fuente == "baja"
        assert source.valida is True  # dudosa (indicio), no inválida

    def test_la_ausencia_de_campo_no_genera_debilidad(self):
        ev = parsear_evidencia_extraccion(
            _json_extraccion(campos={"tipo_comprobante": _campo("A", "'A' y COD. 01")}),
            fuente="vlm",
        )
        source = construir_source_evidence(ev)
        assert len(source.campos) == 1
        assert source.debilidades == []
        assert source.valida is True

    def test_valor_fuera_del_vocabulario_invalida_la_fuente(self):
        # RAW_VOCABULARIO de T-303: el valor no pertenece al vocabulario → la
        # evidencia no es utilizable (``valida=False``).
        ev = parsear_evidencia_extraccion(
            _json_extraccion(campos={"tipo_comprobante": _campo("X", "Recuadro 'X'")}),
            fuente="vlm",
        )
        source = construir_source_evidence(ev)
        assert source.valida is False
        assert "RAW_VOCABULARIO" in source.reglas_aplicadas
        assert any("vocabulario" in d for d in source.debilidades)

    def test_la_pasada_raw_reutiliza_el_registro_de_t303(self):
        ev = parsear_evidencia_extraccion(_json_extraccion(), fuente="vlm")
        veredicto = veredicto_raw_de_evidencia(ev)
        assert veredicto.fuente == "vlm"
        assert veredicto.valida is True
        # El CUIT es texto libre: su sostén literal se evalúa y se cumple.
        assert veredicto.debilidades == []

    def test_cuit_que_no_esta_en_el_fragmento_queda_como_indicio(self):
        ev = parsear_evidencia_extraccion(
            _json_extraccion(
                campos={"cuit_emisor": _campo("20-99999999-9", "C.U.I.T. 20-12345678-9")}
            ),
            fuente="llm",
        )
        source = construir_source_evidence(ev)
        assert "RAW_SUSTENTO" in source.reglas_aplicadas
        assert source.valida is True  # dudosa: indicio, no prueba
        assert any("no contiene ese valor" in d for d in source.debilidades)

    def test_los_campos_de_formato_volatil_no_se_evaluan_por_sosten(self):
        # Un monto con separadores no puede compararse literalmente: se registra
        # como no evaluado (T-402/T-403 lo cubren) sin inventar una debilidad.
        ev = parsear_evidencia_extraccion(_json_extraccion(), fuente="llm")
        source = construir_source_evidence(ev)
        meta = source.campos["importe_total_facturado"].meta
        assert meta["raw_evaluado"] is False
        assert meta["valor_coercionado_a_texto"] is False
        assert not any(
            "importe_total_facturado" in d and "no contiene" in d
            for d in source.debilidades
        )

    def test_veredicto_de_otra_fuente_lanza_value_error(self):
        ev = parsear_evidencia_extraccion(_json_extraccion(), fuente="vlm")
        veredicto_llm = veredicto_raw_de_evidencia(
            parsear_evidencia_extraccion(_json_extraccion(), fuente="llm")
        )
        with pytest.raises(ValueError) as exc:
            construir_source_evidence(ev, veredicto=veredicto_llm)
        assert "deben coincidir" in str(exc.value)

    def test_valor_no_escalar_se_publica_como_texto(self):
        # Una lista (ítems del modo genérico) no entra en el contrato de F0: se
        # publica como texto y se deja anotado (T-402 la estructura).
        ev = parsear_evidencia_extraccion(
            _json_extraccion(campos={"productos": _campo(["café x1 - 1000"], "Ítems")}),
            fuente="llm",
        )
        source = construir_source_evidence(ev)
        assert source.campos["productos"].valor == '["café x1 - 1000"]'
        assert source.campos["productos"].meta["valor_coercionado_a_texto"] is True


class TestCampoDeclarado:
    """``campo_declarado_de_campo`` traduce al ``CampoDeclarado`` de T-303."""

    def test_campo_de_texto_sin_vocabulario(self):
        declarado = campo_declarado_de_campo(
            CampoLectura(campo="cuit_emisor", valor="20-1", fragmento="CUIT 20-1")
        )
        assert declarado is not None
        assert declarado.campo == "cuit_emisor"
        assert declarado.vocabulario is None
        assert declarado.exigir_sustento is True

    def test_campo_con_vocabulario_cerrado(self):
        declarado = campo_declarado_de_campo(
            CampoLectura(campo="tipo_comprobante", valor="A", fragmento="Recuadro 'A'")
        )
        assert declarado is not None
        assert declarado.vocabulario == VOCABULARIO_TIPO_COMPROBANTE

    def test_campo_de_formato_volatil_no_se_evalua(self):
        assert (
            campo_declarado_de_campo(
                CampoLectura(campo="subtotal", valor="12.345,67", fragmento="Subtotal: 12.345,67")
            )
            is None
        )
        assert (
            campo_declarado_de_campo(
                CampoLectura(campo="iva", valor=2100.5, fragmento="IVA 21%: 2.100,50")
            )
            is None
        )


# ---------------------------------------------------------------------------
# 4. Los dos flujos en paralelo (E-EXT-1: "ambos siempre")
# ---------------------------------------------------------------------------


class TestExtraccionParalela:
    """Los dos flujos corren juntos y sus evidencias no se colapsan."""

    def test_corren_las_dos_fuentes(self):
        lector = FakeLector(
            por_fuente={"vlm": _json_extraccion(), "llm": _json_extraccion()}
        )
        resultado = extraer_evidencia(
            lector,
            markdown="FACTURA A",
            vista=_vista_fiel(),
            documento_id="doc-1",
            settings=_settings_dobles(),
        )
        assert {ll["fuente"] for ll in lector.llamadas} == {"vlm", "llm"}
        assert set(resultado.evidencias_por_fuente()) == {"vlm", "llm"}
        assert resultado.documento_id == "doc-1"
        # No se elige un flujo por documento: se piden los dos.
        assert resultado.detalle["fuentes_pedidas"] == list(FUENTES_EXTRACCION)
        assert resultado.detalle["fuentes_corridas"] == ["vlm", "llm"]

    def test_conserva_ambas_evidencias_sin_colapsar(self):
        # ADR-001/ADR-002: la evidencia de cada fuente queda entera, con sus
        # campos; la resolución por campo es T-404 y NO se hace acá.
        lector = FakeLector(
            por_fuente={
                "vlm": _json_extraccion(campos={"tipo_comprobante": _campo("A", "'A'")}),
                "llm": _json_extraccion(campos={"tipo_comprobante": _campo("B", "'B'")}),
            }
        )
        resultado = extraer_evidencia(
            lector,
            markdown="FACTURA B",
            vista=_vista_fiel(),
            settings=_settings_dobles(),
        )
        assert len(resultado.evidencias) == 2
        assert resultado.por_fuente("vlm").campos["tipo_comprobante"].valor == "A"
        assert resultado.por_fuente("llm").campos["tipo_comprobante"].valor == "B"

    def test_el_orden_del_resultado_no_depende_de_los_hilos(self):
        # La fuente "llm" termina mucho antes (0 s) que la "vlm" (30 ms): el
        # resultado igual debe venir en el orden pedido (vlm, llm) para que sea
        # determinista (el paralelismo no puede cambiar el resultado).
        lector = FakeLector(por_fuente={"vlm": _json_extraccion(), "llm": _json_extraccion()})
        resultado = extraer_evidencia(
            lector,
            markdown="FACTURA A",
            vista=_vista_fiel(),
            settings=_settings_dobles(),
        )
        assert [e.fuente.value for e in resultado.evidencias] == ["vlm", "llm"]

    def test_el_paralelismo_es_real(self):
        # Dos llamadas de 0.15 s en paralelo tardan ≈ 0.15 s, no 0.30 s. El
        # margen es amplio (0.28 s) para no ser flaky en CI.
        lector = FakeLector(
            por_fuente={"vlm": _json_extraccion(), "llm": _json_extraccion()},
            demora_s=0.15,
        )
        inicio = time.monotonic()
        extraer_evidencia(
            lector,
            markdown="FACTURA A",
            vista=_vista_fiel(),
            settings=_settings_dobles(),
        )
        transcurrido = time.monotonic() - inicio
        assert transcurrido < 0.28, (
            "los flujos no corrieron en paralelo: tardaron "
            f"{transcurrido:.3f}s (dos llamadas de 0.15s en serie = 0.30s+)"
        )
        assert {ll["fuente"] for ll in lector.llamadas} == {"vlm", "llm"}

    def test_max_workers_uno_serializa(self):
        lector = FakeLector(
            por_fuente={"vlm": _json_extraccion(), "llm": _json_extraccion()},
            demora_s=0.12,
        )
        inicio = time.monotonic()
        resultado = extraer_evidencia(
            lector,
            markdown="FACTURA A",
            vista=_vista_fiel(),
            settings=_settings_dobles(),
            max_workers=1,
        )
        transcurrido = time.monotonic() - inicio
        assert transcurrido >= 0.22  # en serie: dos llamadas de 0.12 s
        assert resultado.detalle["paralelo"] is False
        assert resultado.detalle["max_workers"] == 1

    def test_una_fuente_sin_insumo_se_reporta_y_la_otra_corre(self):
        lector = FakeLector(contenido=_json_extraccion())
        resultado = extraer_evidencia(
            lector,
            markdown="FACTURA A",  # sin vista de imagen
            settings=_settings_dobles(),
        )
        assert resultado.detalle["fuentes_sin_insumo"] == ["vlm"]
        assert resultado.detalle["fuentes_corridas"] == ["llm"]
        assert [ll["fuente"] for ll in lector.llamadas] == ["llm"]

    def test_sin_insumo_no_es_error(self):
        lector = FakeLector(contenido=_json_extraccion())
        resultado = extraer_evidencia(lector, settings=_settings_dobles())
        assert resultado.evidencias == []
        assert resultado.detalle["fuentes_corridas"] == []
        assert lector.llamadas == []

    def test_una_fuente_que_falla_no_tumba_a_la_otra(self):
        lector = LectorPorFuenteQueFalla("vlm", contenido=_json_extraccion())
        resultado = extraer_evidencia(
            lector,
            markdown="FACTURA A",
            vista=_vista_fiel(),
            settings=_settings_dobles(),
        )
        assert resultado.detalle["fuentes_corridas"] == ["llm"]
        assert "vlm" in resultado.fallos
        assert "Ollama no responde" in resultado.fallos["vlm"]
        assert resultado.por_fuente("llm") is not None

    def test_si_todas_fallan_lanza_errorextraccion(self):
        lector = LectorQueFalla()
        with pytest.raises(ErrorExtraccion) as exc:
            extraer_evidencia(
                lector,
                markdown="FACTURA A",
                vista=_vista_fiel(),
                settings=_settings_dobles(),
            )
        assert set(exc.value.fallos) == {"vlm", "llm"}

    def test_respuesta_invalida_es_fallo_de_esa_fuente(self):
        lector = FakeLector(
            por_fuente={"vlm": "no es json", "llm": _json_extraccion()}
        )
        resultado = extraer_evidencia(
            lector,
            markdown="FACTURA A",
            vista=_vista_fiel(),
            settings=_settings_dobles(),
        )
        assert resultado.detalle["fuentes_corridas"] == ["llm"]
        assert "ErrorEvidencia" in resultado.fallos["vlm"]

    def test_modelos_y_duraciones_quedan_registrados(self):
        lector = FakeLector(contenido=_json_extraccion(), demora_s=0.01)
        resultado = extraer_evidencia(
            lector,
            markdown="FACTURA A",
            vista=_vista_fiel(),
            settings=_settings_dobles(),
        )
        assert resultado.detalle["modelos"]["vlm"]["modelo"] == "vlm-doble"
        assert resultado.detalle["modelos"]["vlm"]["num_ctx"] == 4096
        assert resultado.detalle["modelos"]["llm"]["modelo"] == "llm-doble"
        assert resultado.detalle["modelos"]["vlm"]["duracion_s"] > 0
        assert resultado.detalle["version_prompt"] == VERSION_PROMPT_EXTRACCION

    def test_sosten_no_evaluado_queda_listado_en_la_traza(self):
        lector = FakeLector(contenido=_json_extraccion())
        resultado = extraer_evidencia(
            lector,
            markdown="FACTURA A",
            settings=_settings_dobles(),
        )
        no_evaluados = resultado.detalle["modelos"]["llm"]["sosten_no_evaluado"]
        assert "importe_total_facturado" in no_evaluados
        assert "fecha_emision" in no_evaluados
        # ...y los campos de texto sí se evalúan.
        assert "cuit_emisor" not in no_evaluados

    def test_las_debilidades_agregan_la_fuente(self):
        lector = FakeLector(
            por_fuente={
                "llm": _json_extraccion(
                    campos={"cuit_emisor": {"valor": "20-1"}}
                )
            }
        )
        resultado = extraer_evidencia(
            lector,
            markdown="FACTURA A",
            settings=_settings_dobles(),
        )
        assert resultado.debilidades
        assert all(d.startswith("[llm]") for d in resultado.debilidades)

    def test_fuente_invalida_lanza_value_error(self):
        with pytest.raises(ValueError):
            extraer_evidencia(
                FakeLector(),
                markdown="x",
                fuentes=["ocr"],
                settings=_settings_dobles(),
            )

    def test_sin_modelo_configurado_lanza_value_error_antes_de_paralelizar(self):
        lector = FakeLector(contenido=_json_extraccion())
        # ``cargar_settings`` hace deep-merge con los defaults (los roles vlm/llm
        # siempre existen), así que para probar la ausencia de configuración se
        # construye ``Settings`` directo con el mapa de modelos vacío.
        with pytest.raises(ValueError) as exc:
            extraer_evidencia(
                lector,
                markdown="FACTURA A",
                vista=_vista_fiel(),
                settings=Settings(modelos={}),
            )
        assert "No hay modelo configurado" in str(exc.value)
        assert lector.llamadas == []  # falla rápido, sin llamar al modelo


# ---------------------------------------------------------------------------
# 5. Los flujos públicos y el esqueleto de T-404
# ---------------------------------------------------------------------------


class TestFlujosPublicos:
    """``flujo_vlm``/``flujo_llm`` devuelven ``SourceEvidence`` (contrato de F0)."""

    def test_flujo_vlm_devuelve_source_evidence(self):
        lector = FakeLector(contenido=_json_extraccion(fuente_lectura="vlm"))
        source = flujo_vlm(_vista_fiel(), lector=lector, settings=_settings_dobles())
        assert isinstance(source, SourceEvidence)
        assert source.fuente is Fuente.vlm
        assert source.campos["tipo_comprobante"].valor == "A"
        assert lector.llamadas[0]["fuente"] == "vlm"
        assert lector.llamadas[0]["json_format"] is True

    def test_flujo_llm_devuelve_source_evidence(self):
        lector = FakeLector(contenido=_json_extraccion(fuente_lectura="llm"))
        source = flujo_llm("FACTURA A", lector=lector, settings=_settings_dobles())
        assert isinstance(source, SourceEvidence)
        assert source.fuente is Fuente.llm
        assert lector.llamadas[0]["fuente"] == "llm"

    def test_flujo_exige_el_lector(self):
        # El lector pasó a ser obligatorio (protocolo inyectable, F3 §2.6):
        # sin él no hay con qué llamar al modelo.
        import inspect

        for fn in (flujo_vlm, flujo_llm):
            assert "lector" in inspect.signature(fn).parameters
            assert (
                inspect.signature(fn).parameters["lector"].default
                is inspect.Parameter.empty
            )

    def test_ejecutar_flujo_expone_modelo_y_duracion(self):
        lector = FakeLector(contenido=_json_extraccion())
        resultado = ejecutar_flujo(
            "llm", lector, markdown="FACTURA A", settings=_settings_dobles()
        )
        assert resultado.fuente == "llm"
        assert resultado.modelo == "llm-doble"
        assert resultado.duracion_s >= 0
        assert isinstance(resultado.evidencia, EvidenciaExtraccion)

    def test_extraer_es_el_punto_de_entrada_paralelo(self):
        lector = FakeLector(contenido=_json_extraccion())
        resultado = extraer(
            lector,
            markdown="FACTURA A",
            vista=_vista_fiel(),
            settings=_settings_dobles(),
        )
        assert isinstance(resultado, ExtraccionEvidencia)
        assert set(resultado.evidencias_por_fuente()) == {"vlm", "llm"}

    def test_combinar_evidencia_sigue_siendo_esqueleto_de_t404(self):
        # El entregable de T-401 no incluye la combinación por campo:
        # la precedencia ADR-002 es de T-404.
        from voucherflow.extraction import combinar_evidencia

        with pytest.raises(NotImplementedError) as exc:
            combinar_evidencia("doc-1", [])
        assert "T-404" in str(exc.value)


# ---------------------------------------------------------------------------
# 6. Contrato de vocabularios y consistencia del módulo
# ---------------------------------------------------------------------------


class TestVocabularios:
    """El vocabulario cerrado de T-401 es explícito y está alineado con v1."""

    def test_tipo_comprobante_incluye_los_tiques(self):
        # El prompt de v1 (regla 4) admite "090"/"099" para boletos: acá se
        # evalúa qué se leyó, no qué letra decide el negocio (D-13 es de F3).
        assert set(VOCABULARIO_TIPO_COMPROBANTE) == {"A", "B", "C", "M", "E", "090", "099"}

    def test_moneda(self):
        assert set(VOCABULARIO_MONEDA) == {"ARS", "USD"}

    def test_solo_los_campos_declarados_tienen_vocabulario(self):
        assert set(CAMPOS_CON_VOCABULARIO) == {"tipo_comprobante", "moneda"}

    def test_los_campos_de_formato_volatil_son_del_contrato(self):
        assert CAMPOS_SOSTEN_NO_EVALUADO <= set(CAMPOS_EXTRACCION)
        # Los campos de texto clave NO son de formato volátil.
        for campo in (
            "cuit_emisor",
            "cuit_receptor",
            "razon_social_emisor",
            "nro_comprobante",
        ):
            assert campo not in CAMPOS_SOSTEN_NO_EVALUADO

    def test_las_fuentes_son_las_dos_de_eext1(self):
        assert FUENTES_EXTRACCION == ("vlm", "llm")

"""Tests del prompt de evidencia y del lector de tipo/letra (F3 / T-302, E-CLAS-1).

**DoD de T-302** (F3-subplan §3.2): "El prompt `11.1` devuelve evidencia
trazable (recuadro del VLM + texto del LLM) en lugar de solo la letra; la
decisión se basa en esa evidencia."

Qué se verifica, en el orden del subplan:

1. **El prompt no decide** y su contrato de evidencia es el que se documenta
   (``CAMPOS_EVIDENCIA``); los campos de decisión del `11.1` de v1 quedan
   explícitamente **fuera** del contrato (``CAMPOS_FUERA_DEL_CONTRATO``, ADR-006)
   y la versión del prompt está congelada (``tipo-comprobante@1``, ADR-005).
2. **Los ``messages``** se construyen por fuente: la de imagen agrega ``images``
   con la imagen en **base64** (formato que exige Ollama ``/api/chat``, el mismo
   que valida F2/T-202) y la de texto lleva el markdown en ``content``.
3. **Un doble de lector** devuelve evidencia VLM (recuadro) y LLM (texto) → se
   construyen ``SourceEvidence`` válidas (ADR-001) con el fragmento de sustento,
   el ``SourceEvidence.valida`` y las ``debilidades``.
4. **Salida sin letra** → ``tipo_detectado_por_documento = None`` con
   ``campos_desconocidos`` (no se inventa una letra; D-13 para ``090``/``099``).
5. **JSON inválido** → :class:`~voucherflow.classification.evidencia.ErrorEvidencia`.
6. La evidencia poblada en el contexto del motor **produce la decisión de T-301**
   (R4 del recuadro; R5 del texto literal del encabezado; R7 en la discrepancia):
   el puente T-302 → T-301 queda cubierto de punta a punta.

Reglas duras (F3-subplan §4): la suite default corre **sin Ollama real** (doble
del lector) y **sin Docling real** (se inyecta una ``VistaPreparada`` de F2, no
un archivo procesado); no se rompe el contrato congelado de F0 (``TipoComprobanteResult``
y ``ClasificacionContableResult`` siguen construibles igual, ``classify``/
``extract``/``run`` siguen siendo esqueletos — cubierto por
``test_golden_y_esqueleto.py``); no se agregan dependencias nuevas.
"""

from __future__ import annotations

import json
import tempfile
from typing import Any

import pytest

from voucherflow.classification import (
    CAMPO_EXPLICACION,
    CAMPOS_EVIDENCIA,
    CAMPOS_FUERA_DEL_CONTRATO,
    EVIDENCIA_CAMPO_LETRA,
    FUENTES_LECTURA,
    VERSION_PROMPT_TIPO_COMPROBANTE,
    ErrorEvidencia,
    EvidenciaLectura,
    construir_messages_tipo_comprobante,
    construir_source_evidence,
    contexto_desde_evidencia,
    leer_evidencia,
    parsear_evidencia_lectura,
)
from voucherflow.classification.prompt_tipo_comprobante import (
    SYSTEM_PROMPT_POR_FUENTE,
    SYSTEM_PROMPT_TIPO_COMPROBANTE,
    USER_IMAGEN_TIPO_COMPROBANTE,
    USER_TEXTO_TIPO_COMPROBANTE,
)
from voucherflow.classification.tipo_comprobante import clasificar_tipo_comprobante
from voucherflow.rules.contexto import (
    CONDICION_CONSUMIDOR_FINAL,
    CONDICION_RI,
    ContextoTipoComprobante,
)
from voucherflow.schemas.evidence import EvidenceField, Fuente, SourceEvidence
from voucherflow.validation.vistas import VistaPreparada

# ---------------------------------------------------------------------------
# Dobles del lector (sin Ollama real; regla dura F3-subplan §4)
# ---------------------------------------------------------------------------


class FakeRespuesta:
    """Respuesta mínima de un lector (solo se usa ``contenido``)."""

    def __init__(self, contenido: str) -> None:
        self.contenido = contenido


class FakeLector:
    """Doble del ``OllamaClient``: responde por fuente y registra las llamadas.

    Expone la misma superficie que usa :func:`leer_evidencia` (``ask`` con
    ``messages``/``model``/``json_format``/``num_ctx``). Devuelve el contenido
    configurado para la fuente que se está leyendo (se detecta por el system
    prompt del primer mensaje) o ``contenido`` como fallback. No toca la red.
    """

    def __init__(self, contenido: str = "{}", por_fuente: dict[str, str] | None = None) -> None:
        self.contenido = contenido
        self.por_fuente = por_fuente or {}
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


# ---------------------------------------------------------------------------
# Vistas de F2 y respuestas sintéticas
# ---------------------------------------------------------------------------

#: PNG mínimo 1x1 válido: ``construir_messages_tipo_comprobante`` lee el archivo
#: para codificarlo en base64 (mismo requisito que F2/T-202), así que la ruta
#: tiene que existir de verdad.
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


def _vista_imagen() -> VistaPreparada:
    """Vista de revisión con imagen (modalidad VLM; lee el recuadro)."""
    ruta = _archivo_imagen()
    return VistaPreparada(
        tipo_vista="revision",
        calidad="media",
        representacion=ruta,
        resolucion_objetivo=1024,
        origen=ruta,
        ruta_imagen_original=ruta,
        factor_escala=1.0,
        nota="Vista de revisión (T-203/E-QWE-1)",
    )


def _vista_texto(markdown: str = "FACTURA B COD. 006\nTotal: $100,00") -> VistaPreparada:
    """Vista de texto nativo (``ruta_imagen_original`` None; modalidad LLM)."""
    return VistaPreparada(
        tipo_vista="rapida",
        calidad="baja",
        representacion=markdown,
        resolucion_objetivo=0,
        origen="doc.pdf",
        ruta_imagen_original=None,
    )


def _json_evidencia(**cambios: Any) -> str:
    """JSON de evidencia con los defaults del contrato, ajustable por kwargs."""
    datos: dict[str, Any] = {
        "tipo_detectado_por_documento": "A",
        "tipo_detectado_por_documento_explicacion": (
            "Recuadro grande con 'A' junto a 'COD. 01'"
        ),
        "candidatos_descartados": [],
        "candidatos_restantes": [],
        "campos_desconocidos": [],
        "fuente_lectura": "vlm",
    }
    datos.update(cambios)
    return json.dumps(datos, ensure_ascii=False)


# ---------------------------------------------------------------------------
# 1. El prompt de evidencia (contrato y versión)
# ---------------------------------------------------------------------------


class TestPromptEvidencia:
    """El prompt de T-302 reporta evidencia; la decisión es del motor (ADR-006)."""

    def test_version_prompt_congelada(self):
        # ADR-005: la versión del prompt se registra en la evidencia para poder
        # auditar con qué prompt se produjo cada lectura (E-CONC-5).
        assert VERSION_PROMPT_TIPO_COMPROBANTE == "tipo-comprobante@1"

    def test_contrato_de_evidencia_es_el_documentado(self):
        # F3-subplan §6: exactamente estos seis campos constituyen la evidencia.
        assert CAMPOS_EVIDENCIA == (
            "tipo_detectado_por_documento",
            "tipo_detectado_por_documento_explicacion",
            "candidatos_descartados",
            "candidatos_restantes",
            "campos_desconocidos",
            "fuente_lectura",
        )

    def test_la_decision_queda_fuera_del_contrato(self):
        # ADR-006: los campos que el `11.1` de v1 pedía al modelo (letra final,
        # confianza, reglas, alerta, cruce negocio-vs-documento) NO se piden.
        assert set(CAMPOS_EVIDENCIA).isdisjoint(CAMPOS_FUERA_DEL_CONTRATO)
        for campo in (
            "tipo_comprobante",
            "tipo_esperado_por_negocio",
            "reglas_aplicadas",
            "coincide_negocio_vs_documento",
            "confianza",
            "alerta",
        ):
            assert campo in CAMPOS_FUERA_DEL_CONTRATO

    def test_los_campos_de_decision_no_aparecen_en_los_prompts(self):
        # El texto del prompt no debe pedir la decisión: si se la pide, el
        # modelo la devuelve y se ignora (además de contaminar su atención).
        for nombre, prompt in SYSTEM_PROMPT_POR_FUENTE.items():
            assert "tipo_esperado_por_negocio" not in prompt, nombre
            assert "reglas_aplicadas" not in prompt, nombre
            assert '"alerta"' not in prompt, nombre

    def test_el_prompt_declara_que_no_decide(self):
        # La instrucción de alcance es explícita ("NO decidas...") y pide el
        # fragmento de sustento (ADR-001).
        assert "NO decidas" in SYSTEM_PROMPT_TIPO_COMPROBANTE
        assert "No inventes datos" in SYSTEM_PROMPT_TIPO_COMPROBANTE

    def test_hay_un_system_prompt_por_fuente(self):
        # F3-subplan §3.2: dos fuentes con guía de lectura distinta.
        assert set(SYSTEM_PROMPT_POR_FUENTE) == set(FUENTES_LECTURA) == {"vlm", "llm"}

    def test_los_system_prompts_explican_donde_mirar(self):
        # VLM: recuadro del encabezado + el caso COD. 01 del `11.1` (el código
        # AFIP acompaña al recuadro pero no reemplaza la letra).
        vlm = SYSTEM_PROMPT_POR_FUENTE["vlm"]
        assert "recuadro" in vlm
        assert "COD. 01" in vlm
        # LLM: expresiones textuales del encabezado.
        llm = SYSTEM_PROMPT_POR_FUENTE["llm"]
        assert "FACTURA A" in llm
        assert "texto OCR" in llm


# ---------------------------------------------------------------------------
# 2. Construcción de los messages por fuente
# ---------------------------------------------------------------------------


class TestConstruirMessages:
    """Los ``messages`` se arman por fuente (mismo formato que valida F2)."""

    def test_fuente_invalida_lanza_value_error(self):
        with pytest.raises(ValueError, match="fuente inválida"):
            construir_messages_tipo_comprobante(fuente="ocr", markdown="x")

    def test_vlm_lleva_la_imagen_en_base64(self):
        # Ollama /api/chat espera cada ítem de ``images`` en base64 (validado
        # empíricamente en F2/T-202: una ruta devuelve HTTP 400).
        vista = _vista_imagen()
        messages = construir_messages_tipo_comprobante(fuente="vlm", vista=vista)
        assert [m["role"] for m in messages] == ["system", "user"]
        assert messages[1]["content"] == USER_IMAGEN_TIPO_COMPROBANTE
        assert len(messages[1]["images"]) == 1
        # base64 válido y decodificable (no una ruta).
        import base64

        assert base64.b64decode(messages[1]["images"][0])[:8] == b"\x89PNG\r\n\x1a\n"

    def test_vlm_sin_vista_o_sin_imagen_lanza_value_error(self):
        with pytest.raises(ValueError, match="ruta_imagen_original"):
            construir_messages_tipo_comprobante(fuente="vlm", markdown="texto")
        with pytest.raises(ValueError, match="ruta_imagen_original"):
            construir_messages_tipo_comprobante(fuente="vlm", vista=_vista_texto())

    def test_llm_lleva_el_markdown_en_content(self):
        messages = construir_messages_tipo_comprobante(
            fuente="llm", markdown="FACTURA A\nProveedor: Ejemplo S.A."
        )
        assert messages[0]["content"] == SYSTEM_PROMPT_POR_FUENTE["llm"]
        assert "FACTURA A" in messages[1]["content"]
        assert "images" not in messages[1]
        assert USER_TEXTO_TIPO_COMPROBANTE.split("{documento}")[0].strip() in messages[1]["content"]

    def test_llm_sin_markdown_lanza_value_error(self):
        with pytest.raises(ValueError, match="markdown"):
            construir_messages_tipo_comprobante(fuente="llm", markdown="   ")


# ---------------------------------------------------------------------------
# 3/4/5. Interpretación de la respuesta del modelo
# ---------------------------------------------------------------------------


class TestParsearEvidencia:
    """La respuesta del modelo se normaliza; nunca se inventa una letra."""

    def test_evidencia_vlm_completa(self):
        evidencia = parsear_evidencia_lectura(
            _json_evidencia(
                tipo_detectado_por_documento="A",
                candidatos_descartados=["B", "C"],
                candidatos_restantes=["A"],
                fuente_lectura="vlm",
            ),
            fuente="vlm",
        )
        assert evidencia.letra == "A"
        assert evidencia.fragmento.startswith("Recuadro grande")
        assert evidencia.candidatos_descartados == ["B", "C"]
        assert evidencia.candidatos_restantes == ["A"]
        assert evidencia.campos_desconocidos == []
        assert evidencia.problemas == []
        assert evidencia.valida is True

    def test_evidencia_llm_con_texto_literal(self):
        # El fragmento del LLM es el texto OCR literal: es el insumo de R5.
        evidencia = parsear_evidencia_lectura(
            _json_evidencia(
                tipo_detectado_por_documento="B",
                tipo_detectado_por_documento_explicacion="FACTURA B COD. 006",
                fuente_lectura="llm",
            ),
            fuente="llm",
        )
        assert evidencia.letra == "B"
        assert evidencia.fragmento == "FACTURA B COD. 006"
        assert evidencia.problemas == []

    def test_salida_sin_letra_queda_en_none_con_campos_desconocidos(self):
        # F3-subplan §3.2: sin letra, el campo queda en None y se listan los
        # campos que faltaron (no se completa con un valor por defecto).
        evidencia = parsear_evidencia_lectura(
            _json_evidencia(
                tipo_detectado_por_documento=None,
                tipo_detectado_por_documento_explicacion=None,
            ),
            fuente="vlm",
        )
        assert evidencia.letra is None
        assert EVIDENCIA_CAMPO_LETRA in evidencia.campos_desconocidos
        assert CAMPO_EXPLICACION in evidencia.campos_desconocidos
        assert evidencia.valida is False

    def test_letra_fuera_del_vocabulario_no_se_inventa(self):
        # D-13: 090/099 (tiques) y cualquier otra letra no son vocabulario del
        # motor; el valor se descarta y se deja constancia del crudo.
        for crudo in ("Z", "090", "099", "factura A", "AB"):
            evidencia = parsear_evidencia_lectura(
                _json_evidencia(tipo_detectado_por_documento=crudo), fuente="vlm"
            )
            assert evidencia.letra is None, crudo
            assert EVIDENCIA_CAMPO_LETRA in evidencia.campos_desconocidos
            assert any(crudo in problema for problema in evidencia.problemas)

    def test_letra_no_textual_se_descarta_sin_castear(self):
        # Un entero no es una letra: no se castea a texto (090 numérico ≠ "090").
        evidencia = parsear_evidencia_lectura(
            _json_evidencia(tipo_detectado_por_documento=90), fuente="vlm"
        )
        assert evidencia.letra is None
        assert any("int" in problema for problema in evidencia.problemas)

    def test_la_letra_se_normaliza_a_mayuscula(self):
        evidencia = parsear_evidencia_lectura(
            _json_evidencia(tipo_detectado_por_documento="a"), fuente="vlm"
        )
        assert evidencia.letra == "A"

    def test_candidatos_se_normalizan_y_deduplican(self):
        evidencia = parsear_evidencia_lectura(
            _json_evidencia(
                tipo_detectado_por_documento="A",
                candidatos_descartados=["b", "B", "Z", "A", None, 7],
                candidatos_restantes=["a", "C", "C"],
            ),
            fuente="vlm",
        )
        # Solo letras del vocabulario, sin duplicados. La letra leída se quita
        # de descartados (el documento la muestra) pero se conserva en
        # restantes (sigue siendo una alternativa viva).
        assert evidencia.candidatos_descartados == ["B"]
        assert evidencia.candidatos_restantes == ["A", "C"]

    def test_descartados_y_restantes_nunca_se_solapan(self):
        # Blindaje ADR-008: una letra no puede figurar en ambas listas.
        evidencia = parsear_evidencia_lectura(
            _json_evidencia(
                tipo_detectado_por_documento="A",
                candidatos_descartados=["B", "C"],
                candidatos_restantes=["B"],
            ),
            fuente="vlm",
        )
        assert set(evidencia.candidatos_descartados) & set(evidencia.candidatos_restantes) == set()
        # El sesgo es conservador: prevalece como restante y queda anotado.
        assert "B" in evidencia.candidatos_restantes
        assert any("ADR-008" in problema for problema in evidencia.problemas)

    def test_lectura_de_texto_sin_patron_r5_queda_anotada(self):
        # El motor lee la fuente de texto con la regex de R5, no con la letra
        # suelta: si el fragmento no la contiene, se avisa (no se silencia).
        evidencia = parsear_evidencia_lectura(
            _json_evidencia(
                tipo_detectado_por_documento="A",
                tipo_detectado_por_documento_explicacion="letra grande arriba",
                fuente_lectura="llm",
            ),
            fuente="llm",
        )
        assert evidencia.letra == "A"
        assert any("R5" in problema for problema in evidencia.problemas)

    def test_fuente_declarada_distinta_queda_anotada(self):
        # La fuente autoritativa la fija el orquestador, no el modelo.
        evidencia = parsear_evidencia_lectura(
            _json_evidencia(fuente_lectura="llm"), fuente="vlm"
        )
        assert evidencia.fuente == "vlm"
        assert evidencia.fuente_declarada == "llm"
        assert any("fuente_lectura" in problema for problema in evidencia.problemas)

    def test_tolera_json_dentro_de_cercas_markdown(self):
        contenido = "```json\n" + _json_evidencia() + "\n```"
        assert parsear_evidencia_lectura(contenido, fuente="vlm").letra == "A"

    def test_tolera_prosa_alrededor_del_json(self):
        contenido = "Claro, acá va el resultado:\n" + _json_evidencia() + "\nSaludos."
        assert parsear_evidencia_lectura(contenido, fuente="vlm").letra == "A"

    def test_json_invalido_lanza_errorevidencia(self):
        # F3-subplan §3.2: JSON inválido → error de contrato (E-LIB-2).
        with pytest.raises(ErrorEvidencia, match="objeto JSON válido"):
            parsear_evidencia_lectura("no soy JSON", fuente="vlm")
        with pytest.raises(ErrorEvidencia, match="respuesta vacía"):
            parsear_evidencia_lectura("   ", fuente="vlm")
        with pytest.raises(ErrorEvidencia, match="no una lista"):
            parsear_evidencia_lectura('["A"]', fuente="vlm")

    def test_fuente_invalida_lanza_value_error(self):
        with pytest.raises(ValueError, match="fuente inválida"):
            parsear_evidencia_lectura(_json_evidencia(), fuente="ocr")

    def test_la_respuesta_cruda_se_conserva(self):
        # Auditoría: la respuesta textual del modelo no se pierde.
        contenido = _json_evidencia()
        assert parsear_evidencia_lectura(contenido, fuente="vlm").crudo == contenido


# ---------------------------------------------------------------------------
# 3. Conversión a SourceEvidence (ADR-001)
# ---------------------------------------------------------------------------


class TestSourceEvidence:
    """La lectura se convierte en ``SourceEvidence`` (contrato congelado F0)."""

    def _evidencia(self, **cambios: Any) -> EvidenciaLectura:
        return parsear_evidencia_lectura(_json_evidencia(**cambios), fuente="vlm")

    def test_source_evidence_valida_con_fragmento_y_meta(self):
        evidencia = self._evidencia(candidatos_restantes=["A"], candidatos_descartados=["B"])
        fuente_ev = construir_source_evidence(evidencia, modelo="qwen2.5vl:3b")

        assert isinstance(fuente_ev, SourceEvidence)
        assert fuente_ev.fuente is Fuente.vlm
        campo = fuente_ev.campos[EVIDENCIA_CAMPO_LETRA]
        assert isinstance(campo, EvidenceField)
        assert campo.campo == EVIDENCIA_CAMPO_LETRA
        assert campo.valor == "A"
        assert campo.fuente is Fuente.vlm
        assert campo.fragmento_sustento.strip()
        # ADR-005: la versión del prompt viaja en meta (trazabilidad E-CONC-5).
        assert campo.meta["version_prompt"] == VERSION_PROMPT_TIPO_COMPROBANTE
        assert campo.meta["modelo"] == "qwen2.5vl:3b"
        # Los candidatos y lo faltante acompañan la evidencia.
        assert campo.meta["candidatos_descartados"] == ["B"]
        assert campo.meta["candidatos_restantes"] == ["A"]
        assert "timestamp" in campo.meta

    def test_la_evidencia_es_serializable_y_reconstruible(self):
        # El detalle de la corrida guarda el SourceEvidence serializado y debe
        # poder reconstruirse (mismo contrato que F2/T-202).
        fuente_ev = construir_source_evidence(self._evidencia())
        reconstruida = SourceEvidence.model_validate(fuente_ev.model_dump(mode="json"))
        assert reconstruida == fuente_ev

    def test_fuente_llm_mapea_a_fuente_llm_del_schema(self):
        evidencia = parsear_evidencia_lectura(
            _json_evidencia(fuente_lectura="llm"), fuente="llm"
        )
        assert construir_source_evidence(evidencia).fuente is Fuente.llm

    def test_sin_sustento_la_evidencia_no_es_valida_y_lo_declara(self):
        # ADR-001 exige fragmento no vacío; si el modelo no lo dio, la evidencia
        # no es válida y el problema se declara en debilidades.
        evidencia = self._evidencia(tipo_detectado_por_documento_explicacion=None)
        fuente_ev = construir_source_evidence(evidencia)
        assert fuente_ev.valida is False
        assert any("sustento" in debilidad for debilidad in fuente_ev.debilidades)
        # El contrato pydantic se respeta igual: se explica la ausencia.
        assert fuente_ev.campos[EVIDENCIA_CAMPO_LETRA].fragmento_sustento.strip()

    def test_reglas_raw_quedan_vacias_hasta_t303(self):
        # La pasada de reglas raw por fuente es T-303 (F3-subplan §3.3): T-302
        # deja el campo listo pero no anticipa su veredicto.
        fuente_ev = construir_source_evidence(self._evidencia())
        assert fuente_ev.reglas_aplicadas == []

    def test_confianza_fuente_es_la_autoevaluacion_no_la_certeza(self):
        # Glosario §2.3: ``confianza_fuente`` es la autoevaluación de la fuente.
        alta = construir_source_evidence(self._evidencia())
        assert alta.campos[EVIDENCIA_CAMPO_LETRA].confianza_fuente == "alta"
        media = construir_source_evidence(
            self._evidencia(campos_desconocidos=["total"])
        )
        assert media.campos[EVIDENCIA_CAMPO_LETRA].confianza_fuente == "media"
        baja = construir_source_evidence(
            self._evidencia(tipo_detectado_por_documento=None)
        )
        assert baja.campos[EVIDENCIA_CAMPO_LETRA].confianza_fuente == "baja"


# ---------------------------------------------------------------------------
# Puente con el motor de T-301
# ---------------------------------------------------------------------------


class TestContextoDesdeEvidencia:
    """La evidencia puebla el contexto tipado del motor (T-302 → T-301)."""

    def test_vlm_puebla_la_letra_del_recuadro_r4(self):
        evidencia = parsear_evidencia_lectura(_json_evidencia(), fuente="vlm")
        ctx = contexto_desde_evidencia(evidencia)
        assert ctx.letra_recuadro_vlm == "A"
        assert ctx.campos_ausentes == []

    def test_llm_puebla_el_texto_del_encabezado_r5_con_el_fragmento(self):
        # R5 aplica su regex sobre ``texto_encabezado_llm``: el campo lleva el
        # fragmento literal, no la letra suelta.
        evidencia = parsear_evidencia_lectura(
            _json_evidencia(
                tipo_detectado_por_documento="B",
                tipo_detectado_por_documento_explicacion="FACTURA B COD. 006",
                fuente_lectura="llm",
            ),
            fuente="llm",
        )
        ctx = contexto_desde_evidencia(evidencia)
        assert ctx.texto_encabezado_llm == "FACTURA B COD. 006"
        assert ctx.letra_recuadro_vlm is None

    def test_lectura_sin_letra_deja_el_campo_ausente(self):
        evidencia = parsear_evidencia_lectura(
            _json_evidencia(tipo_detectado_por_documento=None), fuente="vlm"
        )
        ctx = contexto_desde_evidencia(evidencia)
        assert ctx.letra_recuadro_vlm is None
        assert "letra_recuadro_vlm" in ctx.campos_ausentes

    def test_no_muta_el_contexto_base(self):
        # El contexto es ``frozen``: la evidencia se aplica sobre una copia.
        base = ContextoTipoComprobante(
            emisor_condicion_fiscal=CONDICION_RI,
            receptor_condicion_fiscal=CONDICION_RI,
        )
        evidencia = parsear_evidencia_lectura(_json_evidencia(), fuente="vlm")
        nuevo = contexto_desde_evidencia(evidencia, base)
        assert base.letra_recuadro_vlm is None  # intacto
        assert nuevo.letra_recuadro_vlm == "A"
        assert nuevo.emisor_condicion_fiscal == CONDICION_RI  # base preservada

    def test_la_evidencia_del_recuadro_produce_la_decision_del_motor(self):
        # End-to-end T-302 → T-301: R2A (RI+RI) espera A y el recuadro dice A.
        base = ContextoTipoComprobante(
            emisor_condicion_fiscal=CONDICION_RI,
            receptor_condicion_fiscal=CONDICION_RI,
        )
        evidencia = parsear_evidencia_lectura(_json_evidencia(), fuente="vlm")
        resultado = clasificar_tipo_comprobante(contexto_desde_evidencia(evidencia, base))
        assert resultado.letra == "A"
        assert resultado.certeza == "alta"
        assert resultado.reglas_aplicadas == ["R2A", "R4"]
        assert resultado.alertas == []

    def test_la_evidencia_del_texto_produce_la_decision_del_motor(self):
        # Misma decisión por la vía del texto (R5 en cascada tras R4 ausente).
        base = ContextoTipoComprobante(
            emisor_condicion_fiscal=CONDICION_RI,
            receptor_condicion_fiscal=CONDICION_RI,
        )
        evidencia = parsear_evidencia_lectura(
            _json_evidencia(
                tipo_detectado_por_documento="A",
                tipo_detectado_por_documento_explicacion="FACTURA A COD. 001",
                fuente_lectura="llm",
            ),
            fuente="llm",
        )
        resultado = clasificar_tipo_comprobante(contexto_desde_evidencia(evidencia, base))
        assert resultado.letra == "A"
        assert resultado.certeza == "alta"
        assert resultado.reglas_aplicadas == ["R2A", "R5"]

    def test_discrepancia_entre_negocio_y_lectura_dispara_r7(self):
        # El caso del prompt WIP: emisor RI + receptor RI (espera A) y el
        # recuadro dice B → la letra la decide el motor y R7 alerta.
        base = ContextoTipoComprobante(
            emisor_condicion_fiscal=CONDICION_RI,
            receptor_condicion_fiscal=CONDICION_RI,
        )
        evidencia = parsear_evidencia_lectura(
            _json_evidencia(
                tipo_detectado_por_documento="B",
                tipo_detectado_por_documento_explicacion="Recuadro con 'B'",
            ),
            fuente="vlm",
        )
        resultado = clasificar_tipo_comprobante(contexto_desde_evidencia(evidencia, base))
        assert resultado.tipo_esperado_por_negocio == "A"
        assert resultado.tipo_detectado_por_documento == "B"
        assert resultado.letra == "B"  # preferencia_letra="documento" (default)
        assert resultado.certeza == "baja"
        assert resultado.reglas_aplicadas == ["R2A", "R4", "R7"]
        assert [alerta["regla"] for alerta in resultado.alertas] == ["R7"]

    def test_una_letra_fuera_del_vocabulario_no_altera_la_decision(self):
        # El modelo leyó "Z": la evidencia queda vacía y el motor decide solo
        # con el negocio (no se inventa una letra, D-13).
        base = ContextoTipoComprobante(
            emisor_condicion_fiscal=CONDICION_RI,
            receptor_condicion_fiscal=CONDICION_RI,
        )
        evidencia = parsear_evidencia_lectura(
            _json_evidencia(tipo_detectado_por_documento="Z"), fuente="vlm"
        )
        resultado = clasificar_tipo_comprobante(contexto_desde_evidencia(evidencia, base))
        assert resultado.tipo_detectado_por_documento is None
        assert resultado.letra == "A"
        assert resultado.certeza == "baja"


# ---------------------------------------------------------------------------
# 3. Orquestación de las fuentes (leer_evidencia)
# ---------------------------------------------------------------------------


class TestLeerEvidencia:
    """``leer_evidencia`` corre las fuentes disponibles con un lector inyectado."""

    def test_corre_las_dos_fuentes_y_conserva_ambas_evidencias(self):
        # ADR-002: las dos lecturas se conservan (no se colapsan).
        lector = FakeLector(
            por_fuente={
                "vlm": _json_evidencia(
                    tipo_detectado_por_documento="A",
                    fuente_lectura="vlm",
                ),
                "llm": _json_evidencia(
                    tipo_detectado_por_documento="B",
                    tipo_detectado_por_documento_explicacion="FACTURA B COD. 006",
                    fuente_lectura="llm",
                ),
            }
        )
        lectura = leer_evidencia(
            lector, markdown="FACTURA B", vista=_vista_imagen(), documento_id="doc-1"
        )
        assert [lectura_ev.fuente for lectura_ev in lectura.lecturas] == ["vlm", "llm"]
        assert lectura.letra_por_recuadro == "A"
        assert lectura.letra_por_texto == "B"
        assert [ev.fuente for ev in lectura.evidencias] == [Fuente.vlm, Fuente.llm]
        assert lectura.documento_id == "doc-1"
        assert [llamada["fuente"] for llamada in lector.llamadas] == ["vlm", "llm"]
        assert all(llamada["json_format"] is True for llamada in lector.llamadas)

    def test_el_contexto_queda_listo_para_el_motor(self):
        lector = FakeLector(
            por_fuente={
                "vlm": _json_evidencia(tipo_detectado_por_documento="A"),
                "llm": _json_evidencia(
                    tipo_detectado_por_documento="B",
                    tipo_detectado_por_documento_explicacion="FACTURA B COD. 006",
                    fuente_lectura="llm",
                ),
            }
        )
        base = ContextoTipoComprobante(
            emisor_condicion_fiscal=CONDICION_RI,
            receptor_condicion_fiscal=CONDICION_CONSUMIDOR_FINAL,
        )
        lectura = leer_evidencia(
            lector, markdown="FACTURA B", vista=_vista_imagen(), contexto_base=base
        )
        # R4 (recuadro A) gana en la cascada sobre R5 (texto B).
        resultado = clasificar_tipo_comprobante(lectura.contexto)
        assert resultado.letra == "A"
        assert resultado.reglas_aplicadas == ["R2B", "R4"]

    def test_omite_las_fuentes_sin_insumo_y_lo_reporta(self):
        # Sin vista no se puede leer la imagen; la fuente se saltea y se informa
        # (no se llama al modelo sin material).
        lector = FakeLector(por_fuente={"llm": _json_evidencia(fuente_lectura="llm")})
        lectura = leer_evidencia(lector, markdown="FACTURA A")
        assert lectura.detalle["fuentes_corridas"] == ["llm"]
        assert lectura.detalle["fuentes_sin_insumo"] == ["vlm"]
        assert [llamada["fuente"] for llamada in lector.llamadas] == ["llm"]

    def test_fuente_invalida_lanza_value_error(self):
        with pytest.raises(ValueError, match="fuente inválida"):
            leer_evidencia(FakeLector(), markdown="x", fuentes=["ocr"])

    def test_resuelve_modelo_y_num_ctx_por_rol(self):
        # E-LIB-3: la lectura visual usa el rol ``vlm`` y la textual el ``llm``.
        lector = FakeLector(
            por_fuente={
                "vlm": _json_evidencia(),
                "llm": _json_evidencia(fuente_lectura="llm"),
            }
        )
        leer_evidencia(lector, markdown="FACTURA A", vista=_vista_imagen())
        modelos = {llamada["fuente"]: llamada["model"] for llamada in lector.llamadas}
        assert modelos["vlm"] == "qwen2.5vl:3b"
        assert modelos["llm"] == "qwen2.5:7b"
        assert all(llamada["num_ctx"] for llamada in lector.llamadas)

    def test_modelo_explicito_se_usa_para_todas_las_fuentes(self):
        lector = FakeLector(
            por_fuente={
                "vlm": _json_evidencia(),
                "llm": _json_evidencia(fuente_lectura="llm"),
            }
        )
        leer_evidencia(
            lector, markdown="FACTURA A", vista=_vista_imagen(), modelo="mi-modelo"
        )
        assert {llamada["model"] for llamada in lector.llamadas} == {"mi-modelo"}

    def test_detalle_declara_que_el_modelo_no_decide(self):
        lector = FakeLector(por_fuente={"llm": _json_evidencia(fuente_lectura="llm")})
        lectura = leer_evidencia(lector, markdown="FACTURA A")
        assert lectura.detalle["version_prompt"] == VERSION_PROMPT_TIPO_COMPROBANTE
        assert "no decide la letra" in lectura.detalle["nota"]
        assert lectura.detalle["modelos"]["llm"]["modelo"] == "qwen2.5:7b"

    def test_sin_insumo_no_llama_al_modelo_ni_falla(self):
        # Un documento sin markdown ni vista es un caso válido (no un error).
        lector = FakeLector()
        lectura = leer_evidencia(lector)
        assert lectura.lecturas == []
        assert lectura.contexto is None
        assert lector.llamadas == []

    def test_una_respuesta_invalida_propaga_errorevidencia(self):
        # Una falla de contrato no se silencia como "sin letra".
        lector = FakeLector(por_fuente={"llm": "no soy JSON"})
        with pytest.raises(ErrorEvidencia):
            leer_evidencia(lector, markdown="FACTURA A")

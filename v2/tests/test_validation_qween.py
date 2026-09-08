"""Tests de la decisión binaria del gate qween (F2 / T-202, épica E-QWE-1).

Validan (F2-subplan §3.2 y reglas duras §4):

1. El prompt corto versionado (``prompt_qween.py``) se construye correctamente
   según la vista: con imagen agrega ``images`` con la imagen en **base64**
   (formato que exige Ollama ``/api/chat`` — validado empíricamente) al
   mensaje ``user``; textual incluye el markdown en ``content``. Se verifica
   ``VERSION_PROMPT_QWEEN == "qween-gate@1"`` (ADR-005).
2. ``decidir_es_comprobante`` con un doble del cliente cubre las 3 salidas
   (``comprobante`` / ``no_comprobante`` / ``indeterminado``) + respuestas
   vacías/ruido/no reconocibles → ``indeterminado`` con nota en ``detalle``,
   sin lanzar excepción.
3. El resultado respeta el contrato congelado de F0 (``ValidationResult``),
   reporta ``vista_usada`` desde la vista y deja ``SourceEvidence``
   (ADR-001) serializado en ``detalle["evidencia"]`` con ``campo=
   "es_comprobante"``, fuente coherente con la modalidad de la vista
   (imagen→``vlm``, texto→``llm``) y fragmento no vacío.
4. El modelo se puede inyectar; si no se pasa se resuelve de ``Settings``
   (rol ``vlm``, default ``qwen2.5vl:3b`` con su ``num_ctx``) — se verifica
   con el doble qué ``model`` recibe ``ask``.

Reglas duras: la suite default corre **sin Ollama real** (doble del cliente);
no se rompe el esqueleto F0 (``validar_comprobante`` sigue lanzando
``NotImplementedError`` y ``api.validate`` también — cubierto por
``test_golden_y_esqueleto.py``).
"""

from __future__ import annotations

from typing import Any

import pytest

from voucherflow.schemas.evidence import EvidenceField, Fuente, SourceEvidence
from voucherflow.settings.config import cargar_settings
from voucherflow.validation import (
    CAMPO_GATE,
    ValidationResult,
    VeredictoGate,
    VistaPreparada,
    construir_messages_gate,
    decidir_es_comprobante,
    validar_comprobante,
)
from voucherflow.validation.prompt_qween import (
    SYSTEM_PROMPT_QWEEN,
    VERSION_PROMPT_QWEEN,
)


# ---------------------------------------------------------------------------
# Dobles del cliente (sin Ollama real; regla dura F2-subplan §4)
# ---------------------------------------------------------------------------


class FakeRespuesta:
    """Respuesta mínima de ``OllamaClient.ask`` (solo se usa ``contenido``)."""

    def __init__(self, contenido: str) -> None:
        self.contenido = contenido


class FakeOllamaClient:
    """Doble del ``OllamaClient``: responde un contenido fijo y registra la llamada.

    Expone la misma superficie que usa ``decidir_es_comprobante`` (``ask`` con
    ``messages``/``model``/``num_ctx``) y devuelve :class:`FakeRespuesta`. No
    toca la red: la suite default corre sin Ollama real.
    """

    def __init__(self, contenido: str = "comprobante") -> None:
        self.contenido = contenido
        self.llamadas: list[dict[str, Any]] = []

    def ask(self, messages, model, json_format=False, options=None, num_ctx=None):
        self.llamadas.append(
            {"messages": messages, "model": model, "num_ctx": num_ctx}
        )
        return FakeRespuesta(self.contenido)


# ---------------------------------------------------------------------------
# Vistas mínimas (contrato T-201): imagen y texto nativo
# ---------------------------------------------------------------------------


#: PNG mínimo 1x1 (válido) para los tests que construyen messages con imagen:
#: ``construir_messages_gate`` lee el archivo para codificarlo en base64
#: (formato que exige Ollama /api/chat; T-202), así que la ruta debe existir.
_PNG_1PX = (
    b"\x89PNG\r\n\x1a\n"  # firma
    b"\x00\x00\x00\rIHDR\x00\x00\x00\x01\x00\x00\x00\x01\x08\x02\x00\x00\x00"
    b"\x90wS\xde\x00\x00\x00\x0cIDATx\x9cc\xf8\xcf\xc0\x00\x00\x00\x03\x00\x01"
    b"\x86\xa0\xb5\x9d\x00\x00\x00\x00IEND\xaeB`\x82"
)


def _vista_imagen(
    ruta: str | None = None,
    nota: str = "Vista rápida (T-201/E-QWE-1): imagen 'factura.png' 1200x800px -> objetivo 512px (factor 2.3438); calidad baja.",
) -> VistaPreparada:
    """Vista rápida de imagen (``ruta_imagen_original`` = ruta, modalidad VLM).

    Si ``ruta`` no se pasa, crea un PNG mínimo 1x1 en un archivo temporal
    (válido) para que ``construir_messages_gate``/``decidir_es_comprobante``
    puedan leerlo y codificarlo en base64 (T-202). No depende de fixtures.
    """
    if ruta is None:
        import tempfile

        tmp = tempfile.NamedTemporaryFile(suffix=".png", delete=False)
        tmp.write(_PNG_1PX)
        tmp.close()
        ruta = tmp.name
    return VistaPreparada(
        tipo_vista="rapida",
        calidad="baja",
        representacion=ruta,
        resolucion_objetivo=512,
        origen=ruta,
        ruta_imagen_original=ruta,
        factor_escala=2.3438,
        nota=nota,
    )


def _vista_texto(
    markdown: str = "FACTURA B\nProveedor: Ejemplo S.A.\nTotal: $100,00",
) -> VistaPreparada:
    """Vista rápida de texto nativo (``ruta_imagen_original`` None, modalidad LLM)."""
    return VistaPreparada(
        tipo_vista="rapida",
        calidad="baja",
        representacion=markdown,
        resolucion_objetivo=0,
        origen="doc.pdf",
        ruta_imagen_original=None,
    )


# ---------------------------------------------------------------------------
# 1. Construcción del prompt corto versionado (prompt_qween.py)
# ---------------------------------------------------------------------------


class TestPromptQween:
    def test_version_prompt_congelada(self):
        # ADR-005: versión del prompt corto para trazabilidad de la evidencia.
        assert VERSION_PROMPT_QWEEN == "qween-gate@1"

    def test_system_prompt_pide_las_tres_salidas_sin_explicar(self):
        # qween.md §1: prompt corto, decisión orientada, tres salidas.
        assert "comprobante" in SYSTEM_PROMPT_QWEEN
        assert "no_comprobante" in SYSTEM_PROMPT_QWEEN
        assert "indeterminado" in SYSTEM_PROMPT_QWEEN
        assert "explica" not in SYSTEM_PROMPT_QWEEN.lower().replace("no expliques", "")

    def test_mensajes_con_imagen_incluyen_la_imagen(self):
        # Vista con imagen: el mensaje user agrega ``images`` con la imagen en
        # base64 (formato que exige Ollama /api/chat para qwen2.5vl:3b,
        # validado empíricamente — T-202; mismo patrón que v1).
        import base64

        vista = _vista_imagen()
        messages = construir_messages_gate(vista)

        assert len(messages) == 2
        assert messages[0]["role"] == "system"
        assert messages[1]["role"] == "user"
        # images[0] es la imagen codificada en base64 (decodificable = PNG 1px).
        assert len(messages[1]["images"]) == 1
        decodificado = base64.b64decode(messages[1]["images"][0])
        assert decodificado.startswith(b"\x89PNG\r\n\x1a\n")
        assert messages[1]["content"]  # texto corto de acompañamiento
        # El markdown NO va en content cuando la imagen va en images.
        assert "FACTURA" not in messages[1]["content"].upper()

    def test_mensajes_textuales_incluyen_el_markdown(self):
        # Vista textual: el markdown/representación de F1 va en content
        # (F2-subplan §2.4, no aplica thumbnail) y NO hay clave images.
        vista = _vista_texto()
        messages = construir_messages_gate(vista)

        assert len(messages) == 2
        assert messages[0]["role"] == "system"
        assert messages[1]["role"] == "user"
        assert "images" not in messages[1]
        assert "FACTURA B" in messages[1]["content"]

    def test_vista_textual_sin_markdown_lanza_valueerror(self):
        vista = _vista_texto(markdown="   ")
        with pytest.raises(ValueError, match="T-202"):
            construir_messages_gate(vista)


# ---------------------------------------------------------------------------
# 2. decidir_es_comprobante: 3 salidas + parseo tolerante
# ---------------------------------------------------------------------------


class TestDecidirEsComprobante:
    def test_respuesta_comprobante(self):
        # Happy path: la decisión sobre la vista rápida es "comprobante".
        cliente = FakeOllamaClient("comprobante")
        resultado = decidir_es_comprobante(_vista_texto(), cliente)

        assert isinstance(resultado, ValidationResult)
        assert resultado.veredicto == VeredictoGate.comprobante
        assert resultado.vista_usada == "rapida"  # vista de T-201
        assert resultado.confianza_fuente == "alta"

    def test_respuesta_no_comprobante(self):
        # "no_comprobante" corta el flujo: el veredicto lo refleja (T-203 decide
        # el rechazo; aquí solo la decisión).
        cliente = FakeOllamaClient("no_comprobante")
        resultado = decidir_es_comprobante(_vista_texto(), cliente)
        assert resultado.veredicto == VeredictoGate.no_comprobante
        assert resultado.confianza_fuente == "alta"

    def test_respuesta_indeterminado(self):
        # "indeterminado" explícito: queda para la 2ª pasada de revisión (T-203).
        cliente = FakeOllamaClient("indeterminado")
        resultado = decidir_es_comprobante(_vista_texto(), cliente)
        assert resultado.veredicto == VeredictoGate.indeterminado
        assert resultado.confianza_fuente == "media"

    @pytest.mark.parametrize(
        "cruda",
        [
            "",  # vacía
            "   \n\t  ",  # solo espacios
            "no entiendo nada de esto",  # ruido sin ninguna de las tres salidas
            "Tal vez sea una carta formal sin datos fiscales",  # no reconocible
            "xxxx yyyy zzzz 123",  # tokens ajenos a las tres etiquetas
        ],
    )
    def test_respuesta_vacia_o_ruido_indeterminado_sin_excepcion(self, cruda):
        # Respuesta vacía/no reconocible → indeterminado con nota en detalle,
        # sin lanzar excepción (criterio T-202 documentado).
        cliente = FakeOllamaClient(cruda)
        resultado = decidir_es_comprobante(_vista_texto(), cliente)

        assert resultado.veredicto == VeredictoGate.indeterminado
        assert resultado.confianza_fuente == "baja"
        assert resultado.detalle["parseo"]["reconocido"] is False
        assert "no reconocida" in resultado.detalle["nota"]

    @pytest.mark.parametrize(
        "cruda,esperado",
        [
            ("NO_COMPROBANTE", VeredictoGate.no_comprobante),  # mayúsculas
            ("no comprobante", VeredictoGate.no_comprobante),  # espacio en vez de _
            ("No es un comprobante.", VeredictoGate.no_comprobante),  # frase negativa
            (" No es una comprobante ", VeredictoGate.no_comprobante),  # artículo femenino + espacios
            ("Comprobante.", VeredictoGate.comprobante),  # mayúscula + puntuación
            ("  indeterminado ", VeredictoGate.indeterminado),  # espacios
            ("la respuesta es: indeterminado.", VeredictoGate.indeterminado),  # prefijo
        ],
    )
    def test_parseo_tolera_variaciones(self, cruda, esperado):
        # Criterio T-202: tolera mayúsculas/espacios/puntuación/variaciones de
        # formato de las tres etiquetas del prompt corto.
        cliente = FakeOllamaClient(cruda)
        resultado = decidir_es_comprobante(_vista_texto(), cliente)
        assert resultado.veredicto == esperado

    def test_vista_usada_es_la_de_la_vista(self):
        # ``vista_usada`` se toma de la vista (T-201) — coherente con el
        # contrato (rapida | revision). T-203 podrá llamar con la de revisión.
        cliente = FakeOllamaClient("comprobante")
        resultado = decidir_es_comprobante(_vista_texto(), cliente)
        assert resultado.vista_usada == "rapida"


# ---------------------------------------------------------------------------
# 3. Evidencia (ADR-001): SourceEvidence en detalle["evidencia"]
# ---------------------------------------------------------------------------


class TestEvidenciaDelGate:
    def _evidencia(self, resultado: ValidationResult) -> SourceEvidence:
        # detalle["evidencia"] es el SourceEvidence serializado (model_dump
        # mode="json"); reconstruible con model_validate (contrato ADR-001).
        return SourceEvidence.model_validate(resultado.detalle["evidencia"])

    def test_reporte_sourceevidence_campo_es_comprobante(self):
        cliente = FakeOllamaClient("comprobante")
        resultado = decidir_es_comprobante(_vista_texto(), cliente)
        ev = self._evidencia(resultado)

        assert CAMPO_GATE == "es_comprobante"
        assert CAMPO_GATE in ev.campos
        campo = ev.campos[CAMPO_GATE]
        assert isinstance(campo, EvidenceField)
        assert campo.valor == "comprobante"
        assert campo.fragmento_sustento.strip(), "fragmento_sustento no puede ser vacío (ADR-001)"
        assert campo.meta["version_prompt"] == VERSION_PROMPT_QWEEN

    def test_fuente_coherente_imagen_vlm_texto_llm(self):
        # La fuente de la evidencia refleja la modalidad de la vista:
        # imagen → vlm; texto nativo → llm (F2-subplan §2.3 / glosario §2.1).
        cliente = FakeOllamaClient("comprobante")
        ev_imagen = self._evidencia(decidir_es_comprobante(_vista_imagen(), cliente))
        assert ev_imagen.fuente == Fuente.vlm
        assert ev_imagen.campos[CAMPO_GATE].fuente == Fuente.vlm

        cliente2 = FakeOllamaClient("comprobante")
        ev_texto = self._evidencia(decidir_es_comprobante(_vista_texto(), cliente2))
        assert ev_texto.fuente == Fuente.llm
        assert ev_texto.campos[CAMPO_GATE].fuente == Fuente.llm

    def test_fragmento_no_vacio_en_imagen_y_texto(self):
        # Imagen: la nota de la vista (trazabilidad T-201) como sustento.
        cliente = FakeOllamaClient("comprobante")
        ev_img = self._evidencia(decidir_es_comprobante(_vista_imagen(), cliente))
        assert "T-201" in ev_img.campos[CAMPO_GATE].fragmento_sustento

        # Texto: recorte del markdown como sustento.
        cliente2 = FakeOllamaClient("comprobante")
        ev_txt = self._evidencia(decidir_es_comprobante(_vista_texto(), cliente2))
        assert "FACTURA B" in ev_txt.campos[CAMPO_GATE].fragmento_sustento

    def test_detalle_lleva_modelo_version_prompt_y_respuesta_bruta(self):
        # Trazabilidad (ADR-005): el detalle expone modelo/versión/respuesta.
        cliente = FakeOllamaClient("comprobante")
        resultado = decidir_es_comprobante(_vista_texto(), cliente, modelo="mi-vlm:1")
        assert resultado.detalle["modelo"] == "mi-vlm:1"
        assert resultado.detalle["version_prompt"] == VERSION_PROMPT_QWEEN
        assert resultado.detalle["respuesta_bruta"] == "comprobante"


# ---------------------------------------------------------------------------
# 4. Modelo: inyección y resolución por Settings (default qwen2.5vl:3b)
# ---------------------------------------------------------------------------


class TestModeloResolucion:
    def test_se_puede_inyectar_modelo(self):
        # El modelo explícito se usa tal cual (sin num_ctx; el llamador elige).
        cliente = FakeOllamaClient("comprobante")
        decidir_es_comprobante(_vista_texto(), cliente, modelo="qwen2.5vl:7b")
        llamada = cliente.llamadas[-1]
        assert llamada["model"] == "qwen2.5vl:7b"

    def test_sin_modelo_usa_settings_default_vlm(self):
        # Sin ``modelo`` ni ``settings``: se resuelve de ``Settings`` (default
        # ``qwen2.5vl:3b`` con num_ctx 4096; F0/T-005 y test_settings_config).
        cliente = FakeOllamaClient("comprobante")
        decidir_es_comprobante(_vista_texto(), cliente)
        llamada = cliente.llamadas[-1]
        assert llamada["model"] == "qwen2.5vl:3b"
        assert llamada["num_ctx"] == 4096
        # El detalle del resultado también expone el modelo resuelto.
        # (se cubre con una llamada adicional para inspeccionar el resultado)

    def test_sin_modelo_resuelve_con_cargar_settings_default(self):
        # El camino por defecto usa ``cargar_settings()`` (sin archivo ni env en
        # el entorno de test → defaults). Verificamos con el doble el model.
        s = cargar_settings(buscar_default=False)
        assert s.modelo_vlm == "qwen2.5vl:3b"

        cliente = FakeOllamaClient("comprobante")
        resultado = decidir_es_comprobante(_vista_texto(), cliente)
        assert resultado.detalle["modelo"] == "qwen2.5vl:3b"
        assert cliente.llamadas[-1]["model"] == "qwen2.5vl:3b"

    def test_vista_con_imagen_usa_modelo_vlm_por_defecto(self):
        # La vista con imagen también usa el modelo VLM por defecto y el
        # mensaje enviado incluye la imagen (en base64; se captura en el doble).
        import base64

        cliente = FakeOllamaClient("comprobante")
        decidir_es_comprobante(_vista_imagen(), cliente)
        llamada = cliente.llamadas[-1]
        assert llamada["model"] == "qwen2.5vl:3b"
        user = llamada["messages"][1]
        # images[0] es base64 válido de un PNG (no una ruta).
        assert len(user["images"]) == 1
        decodificado = base64.b64decode(user["images"][0])
        assert decodificado.startswith(b"\x89PNG\r\n\x1a\n")


# ---------------------------------------------------------------------------
# 5. F0 intacto: validar_comprobante sigue siendo esqueleto (T-203)
# ---------------------------------------------------------------------------


class TestNoRompeF0:
    def test_validar_comprobante_sigue_siendo_esqueleto(self):
        # T-202 implementa la decisión de una pasada; la orquestación del doble
        # paso (validar_comprobante / api.validate) es T-203 y sigue lanzando
        # NotImplementedError (regla dura F2-subplan §4).
        with pytest.raises(NotImplementedError):
            validar_comprobante("origen.jpg")

    def test_validationresult_sigue_construible_como_f0(self):
        # El contrato congelado no cambió: se construye igual que en F0.
        r = ValidationResult(veredicto=VeredictoGate.no_comprobante)
        assert r.veredicto == VeredictoGate.no_comprobante
        assert r.confianza_fuente == "media"
        assert r.vista_usada == "rapida"
        assert r.detalle == {}

    def test_contratos_congelados_siguen_exportandose(self):
        # F2-subplan §4: la ampliación de __all__ no quita los contratos F0.
        from voucherflow import validation

        assert hasattr(validation, "ValidationResult")
        assert hasattr(validation, "VeredictoGate")
        assert hasattr(validation, "validar_comprobante")

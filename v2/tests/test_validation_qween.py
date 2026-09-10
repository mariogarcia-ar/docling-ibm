"""Tests de la decisión binaria del gate qween (F2 / T-202, épica E-QWE-1).

Validan (F2-subplan §3.2 y reglas duras §4):

1. El prompt corto versionado (``prompt_qween.py``) se construye correctamente
   según la vista: con imagen agrega ``images`` con la imagen en **base64**
   (formato que exige Ollama ``/api/chat`` — validado empíricamente) al
   mensaje ``user``; textual incluye el markdown en ``content``. Se verifica
   ``VERSION_PROMPT_QWEEN == "qween-gate@2"`` (ADR-005).
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

5. (T-203) La orquestación del doble paso ``validar_y_procesar``: 1ª pasada
   rápida → rechazo si ``no_comprobante``; 2ª pasada de revisión si
   ``indeterminado``; vista fiel solo si ``comprobante``; la política
   "indeterminado tras revisión" se resuelve como rechazo conservando la
   trazabilidad. ``validar_comprobante`` y ``api.validate`` dejan de lanzar
   ``NotImplementedError``.

Reglas duras: la suite default corre **sin Ollama real** (doble del cliente) y
**sin Docling real** (se inyecta un ``ProcessedDocument`` ya procesado, no un
archivo); no se rompe el contrato congelado de F0 (``ValidationResult``
construible igual, ``classify``/``extract``/``run`` siguen siendo esqueletos —
cubierto por ``test_golden_y_esqueleto.py``).
"""

from __future__ import annotations

import base64
from pathlib import Path
from typing import Any

import pytest

from voucherflow.models.docling import ProcessedDocument
from voucherflow.schemas.evidence import EvidenceField, Fuente, SourceEvidence
from voucherflow.settings.config import cargar_settings
from voucherflow.validation import (
    CAMPO_GATE,
    ResultadoValidacion,
    ValidationResult,
    VeredictoGate,
    VistaPreparada,
    construir_messages_gate,
    decidir_es_comprobante,
    preparar_vista_fiel,
    preparar_vista_rapida,
    preparar_vista_revision,
    validar_comprobante,
    validar_y_procesar,
)
from voucherflow.validation.prompt_qween import (
    LADO_MAYOR_OBJETIVO_POR_VISTA,
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


def _imagen_sintetica(ancho: int = 1200, alto: int = 800, sufijo: str = ".jpg") -> str:
    """Crea una imagen sintética temporal (para tests de reducción).

    Escribe una imagen válida de ``ancho`` x ``alto`` en un archivo temporal y
    devuelve su ruta. No depende de fixtures ni de Pillow para crear el caso
    base mínima: si Pillow está disponible (lo está en ``py313_env``) la usa
    para generar una imagen con contenido; si no, escribe un PNG/JPEG mínimo.
    """
    import struct
    import tempfile
    import zlib

    def _png_blanco(w: int, h: int) -> bytes:
        fila = b"\x00" + b"\xff\xff\xff" * w
        raw = fila * h
        def chunk(tipo: bytes, datos: bytes) -> bytes:
            return (
                struct.pack(">I", len(datos))
                + tipo
                + datos
                + struct.pack(">I", zlib.crc32(tipo + datos) & 0xFFFFFFFF)
            )
        ihdr = struct.pack(">IIBBBBB", w, h, 8, 2, 0, 0, 0)
        return (
            b"\x89PNG\r\n\x1a\n"
            + chunk(b"IHDR", ihdr)
            + chunk(b"IDAT", zlib.compress(raw))
            + chunk(b"IEND", b"")
        )

    tmp = tempfile.NamedTemporaryFile(suffix=".png", delete=False)
    tmp.write(_png_blanco(ancho, alto))
    tmp.close()
    return tmp.name


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
        # v2: fix de falsos positivos (caso 2926bed9) — define qué es/no es
        # comprobante.
        assert VERSION_PROMPT_QWEEN == "qween-gate@2"

    def test_system_prompt_pide_las_tres_salidas_y_define_limites(self):
        # qween.md §1 + v2: prompt corto, decisión orientada, tres salidas.
        # v2 agrega la definición de qué ES / qué NO ES comprobante (fix de
        # falsos positivos: capturas de sistema mostrando movimientos).
        assert "comprobante" in SYSTEM_PROMPT_QWEEN
        assert "no_comprobante" in SYSTEM_PROMPT_QWEEN
        assert "indeterminado" in SYSTEM_PROMPT_QWEEN
        # v2: define los límites — capturas de apps/sistemas que muestran un
        # movimiento NO son comprobante (caso 2926bed9).
        assert "Capturas de pantalla" in SYSTEM_PROMPT_QWEEN
        assert "movimiento" in SYSTEM_PROMPT_QWEEN.lower()
        assert "Extractos bancarios" in SYSTEM_PROMPT_QWEEN

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

    # ------------------------------------------------------------------
    # Reducción real de la imagen antes del envío (E-QWE-1 + contexto VLM)
    # ------------------------------------------------------------------

    def test_lado_mayor_objetivo_por_vista(self):
        # Gradiente del doble paso: la rápida reduce más, la revisión menos y
        # la fiel no reduce (máxima fidelidad, E-QWE-2).
        assert LADO_MAYOR_OBJETIVO_POR_VISTA == {
            "rapida": 512,
            "revision": 1024,
            "fiel": None,
        }

    def test_imagen_envio_base64_reduce_imagen_grande(self):
        # Una imagen grande se reduce al lado mayor objetivo de la vista (512
        # para la rápida): evita exceder el num_ctx del VLM y hace la decisión
        # barata (E-QWE-1). Se usa una imagen sintética grande (no fixture).
        from voucherflow.validation.prompt_qween import imagen_envio_base64

        vista = _vista_imagen(ruta=_imagen_sintetica(ancho=2200, alto=2700))
        b64, info = imagen_envio_base64(vista)

        assert info["reducida"] is True, "la imagen grande debe reducirse"
        assert max(info["ancho_envio"], info["alto_envio"]) <= 512, (
            "el lado mayor de la vista rápida no debe superar los 512 px"
        )
        assert info["peso_envio_bytes"] < info["peso_original_bytes"]
        # El base64 decodifica a un JPEG válido (se re-codifica al reducir).
        assert base64.b64decode(b64)[:2] == b"\xff\xd8"

    def test_imagen_envio_base64_no_agranda_imagen_chica(self):
        # Imágenes más chicas que el objetivo no se agrandan (se envían tal cual).
        from voucherflow.validation.prompt_qween import imagen_envio_base64

        ruta = _imagen_sintetica(ancho=200, alto=150)
        vista = _vista_imagen(ruta=ruta)
        b64, info = imagen_envio_base64(vista)

        assert info["reducida"] is False
        assert "ya es <= objetivo" in info["motivo"]
        assert base64.b64decode(b64) == Path(ruta).read_bytes()

    def test_imagen_envio_fiel_no_reduce(self):
        # La vista fiel NO se degrada (máxima fidelidad para F4, E-QWE-2).
        from voucherflow.validation.prompt_qween import imagen_envio_base64

        ruta = _imagen_sintetica(ancho=2200, alto=2700)
        vista = VistaPreparada(
            tipo_vista="fiel",
            calidad="alta",
            representacion=ruta,
            origen=ruta,
            ruta_imagen_original=ruta,
        )
        b64, info = imagen_envio_base64(vista)

        assert info["reducida"] is False
        assert "sin reducción" in info["motivo"]
        assert base64.b64decode(b64) == Path(ruta).read_bytes()

    def test_imagen_envio_base64_vista_textual_lanza_valueerror(self):
        # Una vista textual no tiene imagen que enviar (contrato T-202/T-203).
        from voucherflow.validation.prompt_qween import imagen_envio_base64

        with pytest.raises(ValueError, match="vista textual"):
            imagen_envio_base64(_vista_texto())

    def test_messages_imagen_reducida_no_excede_payload(self):
        # End-to-end del montaje: la imagen grande en ``images`` va reducida
        # (payload chico), no la original (que excedía el num_ctx del VLM).
        vista = _vista_imagen(ruta=_imagen_sintetica(ancho=2200, alto=2700))
        messages = construir_messages_gate(vista)
        payload = messages[1]["images"][0]

        # 512 px JPEG q80 en base64 queda muy por debajo de la imagen original.
        assert len(payload) < 200_000, "el payload de la vista rápida debe ser chico"


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
# 5. T-203 · Orquestación del doble paso (validar_y_procesar)
# ---------------------------------------------------------------------------


class FakeOllamaSecuencia:
    """Doble del ``OllamaClient`` que responde una **secuencia** de contenidos.

    La orquestación del doble paso puede llamar hasta dos veces (rápida y
    revisión). Este doble devuelve la respuesta i-ésima en orden de llamada
    (la última se repite si hay más llamadas) y registra cada llamada, para
    verificar qué vista se usó en cada pasada.
    """

    def __init__(self, contenidos: list[str]) -> None:
        self.contenidos = list(contenidos)
        self.llamadas: list[dict[str, Any]] = []

    def ask(self, messages, model, json_format=False, options=None, num_ctx=None):
        idx = min(len(self.llamadas), len(self.contenidos) - 1)
        self.llamadas.append(
            {"messages": messages, "model": model, "num_ctx": num_ctx}
        )
        return FakeRespuesta(self.contenidos[idx])


def _doc_procesado_texto(
    markdown: str = "FACTURA A\nProveedor: Ejemplo S.A.\nTotal: $100,00",
) -> ProcessedDocument:
    """``ProcessedDocument`` de texto nativo ya procesado por F1 (sin Docling).

    Decisión de testeo (F2-subplan §4): ``validar_y_procesar`` acepta un
    ``ProcessedDocument`` ya procesado, así los tests del doble paso **no**
    corren Docling real (lento). El documento es mínimo (3 posicionales).
    """
    return ProcessedDocument("pdf_texto", "doc.pdf", markdown)


class TestValidarYProcesar:
    def test_comprobante_en_primera_pasada_prepara_vista_fiel(self):
        # Happy path: la 1ª pasada (vista rápida) decide comprobante → se
        # prepara la vista fiel y NO hay 2ª pasada.
        cliente = FakeOllamaClient("comprobante")
        res = validar_y_procesar(_doc_procesado_texto(), cliente)

        assert isinstance(res, ResultadoValidacion)
        assert res.veredicto_final == VeredictoGate.comprobante
        assert res.vista_fiel is not None, "comprobante → debe haber vista fiel"
        assert res.vista_fiel.tipo_vista == "fiel"
        assert res.vista_fiel.calidad == "alta"
        assert len(res.pasadas) == 1, "comprobante en 1ª pasada: una sola pasada"
        assert len(cliente.llamadas) == 1
        assert res.resultado.veredicto == VeredictoGate.comprobante
        assert res.resultado.vista_usada == "rapida"
        assert res.pasadas[0].vista_usada == "rapida"

    def test_no_comprobante_en_primera_pasada_no_prepara_vista_fiel(self):
        # Rechazo: no se prepara la vista fiel (no llega a extracción; ahorro
        # E-QWE) y no hay 2ª pasada.
        cliente = FakeOllamaClient("no_comprobante")
        res = validar_y_procesar(_doc_procesado_texto(), cliente)

        assert res.veredicto_final == VeredictoGate.no_comprobante
        assert res.vista_fiel is None, "no_comprobante → sin vista fiel (ahorro)"
        assert len(res.pasadas) == 1
        assert len(cliente.llamadas) == 1
        assert res.resultado.vista_usada == "rapida"

    def test_indeterminado_luego_comprobante_usa_vista_revision(self):
        # 1ª pasada indeterminado → 2ª pasada con la vista de revisión que
        # confirma comprobante → vista fiel presente y 2 pasadas trazadas.
        cliente = FakeOllamaSecuencia(["indeterminado", "comprobante"])
        res = validar_y_procesar(_doc_procesado_texto(), cliente)

        assert res.veredicto_final == VeredictoGate.comprobante
        assert res.vista_fiel is not None
        assert res.vista_fiel.tipo_vista == "fiel"
        assert len(res.pasadas) == 2, "indeterminado → 2ª pasada de revisión"
        assert len(cliente.llamadas) == 2
        assert res.pasadas[0].vista_usada == "rapida"
        assert res.pasadas[1].vista_usada == "revision", (
            "la 2ª pasada debe decidir sobre la vista de revisión (T-203)"
        )
        assert res.resultado.vista_usada == "revision"
        assert res.resultado is res.pasadas[-1]

    def test_indeterminado_luego_no_comprobante_rechaza(self):
        # 1ª indeterminado + 2ª no_comprobante → rechazo, sin vista fiel.
        cliente = FakeOllamaSecuencia(["indeterminado", "no_comprobante"])
        res = validar_y_procesar(_doc_procesado_texto(), cliente)

        assert res.veredicto_final == VeredictoGate.no_comprobante
        assert res.vista_fiel is None
        assert len(res.pasadas) == 2
        assert res.pasadas[1].veredicto == VeredictoGate.no_comprobante
        assert res.pasadas[1].vista_usada == "revision"

    def test_indeterminado_tras_revision_rechaza_con_trazabilidad(self):
        # Política E-QWE-1 ("si sigue sin ser comprobante, se rechaza"): un
        # indeterminado tras la revisión se resuelve como no_comprobante, pero
        # se conserva la trazabilidad de ambas pasadas.
        cliente = FakeOllamaSecuencia(["indeterminado", "indeterminado"])
        res = validar_y_procesar(_doc_procesado_texto(), cliente)

        assert res.veredicto_final == VeredictoGate.no_comprobante, (
            "indeterminado tras revisión se rechaza (E-QWE-1)"
        )
        assert res.vista_fiel is None
        assert len(res.pasadas) == 2
        # La 2ª pasada conserva su veredicto original (indeterminado) para no
        # perder trazabilidad; la política se aplica en veredicto_final.
        assert res.pasadas[1].veredicto == VeredictoGate.indeterminado
        resolucion = res.resultado.detalle["resolucion_doble_paso"]
        assert resolucion["veredictos_por_pasada"] == ["indeterminado", "indeterminado"]
        assert resolucion["decision"] == "no_comprobante"
        assert "E-QWE-1" in resolucion["motivo"]

    def test_documento_procesado_se_reutiliza_para_las_tres_vistas(self):
        # El ProcessedDocument de F1 se usa tal cual (no se re-procesa Docling):
        # se expone en el resultado para trazabilidad/consumo aguas abajo.
        doc = _doc_procesado_texto()
        cliente = FakeOllamaClient("comprobante")
        res = validar_y_procesar(doc, cliente)

        assert res.documento is doc
        # La vista fiel apunta a la representación de máxima fidelidad
        # (markdown completo del documento), no a la vista rápida degradada.
        rapida = preparar_vista_rapida(doc)
        fiel = preparar_vista_fiel(doc)
        assert res.vista_fiel.representacion == fiel.representacion
        assert res.vista_fiel.tipo_vista != rapida.tipo_vista

    def test_modelo_se_propaga_a_todas_las_pasadas(self):
        # El modelo inyectado se usa en ambas pasadas del doble paso.
        cliente = FakeOllamaSecuencia(["indeterminado", "comprobante"])
        validar_y_procesar(_doc_procesado_texto(), cliente, modelo="mi-gate:1")

        assert len(cliente.llamadas) == 2
        assert all(c["model"] == "mi-gate:1" for c in cliente.llamadas)

    def test_error_de_ollama_se_propaga(self):
        # Una falla de comunicación NO se silencia como no_comprobante (T-203):
        # se propaga al llamador.
        from voucherflow.models.ollama import OllamaError

        class ClienteQueFalla:
            def ask(self, *a, **k):  # noqa: ARG002
                raise OllamaError("sin conexión con Ollama (test)")

        with pytest.raises(OllamaError):
            validar_y_procesar(_doc_procesado_texto(), ClienteQueFalla())


class TestValidarComprobanteImplementado:
    def test_validar_comprobante_delega_y_devuelve_validationresult(self, monkeypatch):
        # T-203: ``validar_comprobante`` ya no lanza NotImplementedError; delega
        # en la orquestación y devuelve el ValidationResult de la última pasada
        # (misma firma/tipo que el contrato F0).
        import voucherflow.validation.qween as qween_mod

        def _fake_validar_y_procesar(origen, cliente=None, **kwargs):  # noqa: ARG001
            return ResultadoValidacion(
                resultado=ValidationResult(
                    veredicto=VeredictoGate.comprobante, vista_usada="rapida"
                ),
                veredicto_final=VeredictoGate.comprobante,
                vista_fiel=None,
                pasadas=[],
                documento=None,
            )

        monkeypatch.setattr(qween_mod, "validar_y_procesar", _fake_validar_y_procesar)
        resultado = validar_comprobante("cualquier.pdf")

        assert isinstance(resultado, ValidationResult)
        assert resultado.veredicto == VeredictoGate.comprobante

    def test_validar_comprobante_con_documento_procesado(self, monkeypatch):
        # Integración real de ``validar_comprobante`` → ``validar_y_procesar``:
        # se monkeypatcha la resolución del documento de F1 (Docling lento)
        # devolviendo un ProcessedDocument mínimo, y el cliente Ollama se
        # reemplaza por un doble.
        import voucherflow.validation.qween as qween_mod

        doc = _doc_procesado_texto()
        monkeypatch.setattr(qween_mod, "_resolver_documento", lambda *a, **k: doc)
        monkeypatch.setattr(qween_mod, "OllamaClient", lambda *a, **k: FakeOllamaClient("comprobante"))
        resultado = validar_comprobante("doc.pdf")
        assert isinstance(resultado, ValidationResult)
        assert resultado.veredicto == VeredictoGate.comprobante

    def test_api_validate_ya_no_lanza_notimplemented(self, monkeypatch):
        # ``api.validate`` deja de lanzar NotImplementedError (T-203): delega en
        # ``validation.validar_comprobante``.
        import voucherflow.api as api_mod

        def _fake_validar_comprobante(origen, quick=True):  # noqa: ARG001
            return ValidationResult(veredicto=VeredictoGate.comprobante)

        monkeypatch.setattr(
            "voucherflow.validation.validar_comprobante", _fake_validar_comprobante
        )
        resultado = api_mod.validate("doc.pdf")
        assert isinstance(resultado, ValidationResult)
        assert resultado.veredicto == VeredictoGate.comprobante


# ---------------------------------------------------------------------------
# 6. F0 intacto: contratos congelados y esqueletos de otras fases
# ---------------------------------------------------------------------------


class TestNoRompeF0:
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

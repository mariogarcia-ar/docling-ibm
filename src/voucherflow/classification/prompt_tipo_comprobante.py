"""Prompt de **evidencia** de tipo/letra, versionado (F3 / T-302, E-CLAS-1).

**Qué cambia respecto de `11.1` de v1**: el prompt **deja de decidir**. Antes
pedía ``tipo_comprobante``, ``confianza``, ``tipo_esperado_por_negocio``,
``reglas_aplicadas``, ``alerta`` y ``coincide_negocio_vs_documento`` — es decir,
le pedía al modelo el resultado de aplicar las reglas R1-R7. Eso es exactamente
lo que **ADR-006** prohíbe: la letra la decide el motor determinístico en código
(T-301, ``rules/tipo_comprobante_rules.py``) y el modelo solo aporta **evidencia
de lectura** (ADR-001: qué se leyó y con qué fragmento de sustento).

Este módulo congela el **texto del prompt** (versionado — ADR-005) y la
construcción de los ``messages`` de ``OllamaClient.ask``, con el mismo
desdoblamiento que F2 hace en ``validation/prompt_qween.py``: el *qué se le
pide* (este módulo) queda separado del *cómo se interpreta la respuesta*
(``evidencia.py``).

Contrato de salida (F3-subplan §6) — el modelo reporta **solo** esto:

===============================================  ==========================
Campo                                            Qué es
===============================================  ==========================
``tipo_detectado_por_documento``                 letra que se ve/lee (A/B/C/M/E)
``tipo_detectado_por_documento_explicacion``     el **fragmento de sustento**:
                                                 el recuadro visto o el texto OCR
``candidatos_descartados``                       letras que el documento descarta
``candidatos_restantes``                         letras que siguen posibles
``campos_desconocidos``                          qué faltó para poder concluir
``fuente_lectura``                               ``vlm`` (recuadro) | ``llm`` (texto)
===============================================  ==========================

**Fuera del contrato a propósito** (el motor T-301 los calcula, por eso no se
le piden al modelo): ``tipo_comprobante`` (la letra final),
``tipo_esperado_por_negocio`` (R1/R2A/R2B/R3), ``reglas_aplicadas``,
``coincide_negocio_vs_documento``, ``confianza`` y ``alerta`` (R7). Si el modelo
los devuelve igual, se **ignoran** al interpretar la respuesta (``evidencia.py``
no los lee): la evidencia no decide.

**Dos fuentes, dos system prompts** (F3-subplan §3.2): ``system_vlm`` mira la
imagen y busca el **recuadro** de la letra en el encabezado; ``system_llm`` mira
el OCR/Markdown y aplica la búsqueda textual. El orquestador corre las dos
cuando tiene ambos insumos y **conserva ambas evidencias** sin colapsarlas
(ADR-002 las resolverá por campo en F4).

Por qué no se copió el ``system`` de ``11.1``: ese bloque contiene las reglas
R1-R3 y la validación de conflictos R7. Portarlo sería volver a poner la
decisión en el prompt. Lo que sí se conserva de ``11.1`` es la **guía de
lectura** (dónde mirar, qué priorizar, cómo explicar el sustento) y la
instrucción de no inventar datos, más el caso ``COD. 01`` (el código AFIP
acompaña al recuadro pero **no** reemplaza a la letra).

Formato de la imagen en ``messages``: idéntico al validado empíricamente en F2
(``validation/prompt_qween.py``) — ``images`` con la imagen en **base64** y
**reducida** antes de codificar (Ollama ``/api/chat`` interpreta cada ítem de
``images`` como base64 y una imagen sin reducir excede el ``num_ctx`` del modelo
local: HTTP 400). La reducción la hace ``imagen_envio_base64`` de F2, que la
deriva del ``tipo_vista`` de la :class:`~voucherflow.validation.vistas.VistaPreparada`
recibida: para leer la letra del recuadro alcanza la calidad ``revision``
(1024 px); la ``fiel`` (sin reducción) es para el detalle de F4.
"""

from __future__ import annotations

from typing import Any

# ---------------------------------------------------------------------------
# Versión del prompt (ADR-005: trazabilidad de prompt por versión)
# ---------------------------------------------------------------------------

#: Identificador versionado del prompt de evidencia. Se registra en
#: ``EvidenceField.meta.version_prompt`` vía :func:`~voucherflow.schemas.evidence.nueva_meta`
#: para poder auditar con qué prompt se produjo cada evidencia (E-CONC-5).
#
# Historial de versiones:
#   - ``tipo-comprobante@1``: primer prompt de **evidencia** (T-302). Porta la
#     guía de lectura de ``prompts/facturacion/11.1-deteccion_tipo_factura.yaml``
#     (``system``/``system_llm``/``system_vlm``) pero **quita** la decisión:
#     no pide letra final, confianza, reglas aplicadas, alerta ni el cruce
#     negocio-vs-documento (los calcula el motor de T-301, ADR-006). Agrega
#     ``fuente_lectura`` y exige el fragmento de sustento (ADR-001).
VERSION_PROMPT_TIPO_COMPROBANTE = "tipo-comprobante@1"

# ---------------------------------------------------------------------------
# Contrato de campos de la evidencia de lectura (F3-subplan §6)
# ---------------------------------------------------------------------------

#: Nombres de los campos que el modelo **sí** debe devolver. Es la lista que
#: valida ``evidencia.py`` al interpretar la respuesta y la que se documenta en
#: el prompt. Cualquier otra clave de la respuesta se ignora (el modelo no
#: decide).
CAMPOS_EVIDENCIA: tuple[str, ...] = (
    "tipo_detectado_por_documento",
    "tipo_detectado_por_documento_explicacion",
    "candidatos_descartados",
    "candidatos_restantes",
    "campos_desconocidos",
    "fuente_lectura",
)

#: Claves que el motor determina y que por eso **no** se le piden al modelo
#: (se listan para dejarlo explícito en el prompt y en la documentación).
CAMPOS_FUERA_DEL_CONTRATO: tuple[str, ...] = (
    "tipo_comprobante",
    "tipo_esperado_por_negocio",
    "reglas_aplicadas",
    "coincide_negocio_vs_documento",
    "confianza",
    "alerta",
)

#: Valores admitidos de ``fuente_lectura`` (declaración del propio modelo; la
#: fuente **autoritativa** la fija el orquestador según qué system prompt usó).
FUENTES_LECTURA: tuple[str, ...] = ("vlm", "llm")

#: Plantilla JSON del contrato de evidencia. Es el **ejemplo** que se incrusta
#: en el prompt (mismo criterio que ``11.1``): valores concretos y ``null`` para
#: lo ausente, ``[]`` para listas vacías.
ESQUEMA_JSON_EVIDENCIA: dict[str, Any] = {
    "tipo_detectado_por_documento": "A",
    "tipo_detectado_por_documento_explicacion": (
        "Recuadro grande con 'A' en el encabezado, junto a 'COD. 01'"
    ),
    "candidatos_descartados": [],
    "candidatos_restantes": [],
    "campos_desconocidos": [],
    "fuente_lectura": "vlm",
}


def _json_ejemplo() -> str:
    """Renderiza :data:`ESQUEMA_JSON_EVIDENCIA` como JSON indentado para el prompt.

    Se usa ``json.dumps`` (y no un literal a mano) para que el ejemplo impreso y
    el contrato validado no puedan divergir: si se agrega un campo al esquema,
    aparece solo en el prompt. ``ensure_ascii=False`` conserva los acentos.
    """
    import json

    return json.dumps(ESQUEMA_JSON_EVIDENCIA, ensure_ascii=False, indent=2)


# ---------------------------------------------------------------------------
# System prompts: base común + guía de lectura por fuente
# ---------------------------------------------------------------------------

#: Base común del system prompt (tarea = evidencia, no decisión). Portado de la
#: introducción de ``11.1`` y del ``system_prompt`` del WIP
#: ``prompts/wip/deteccion_tipo_factura.yaml``, con la decisión **removida**.
SYSTEM_PROMPT_TIPO_COMPROBANTE = (
    "Sos un lector de comprobantes argentinos (AFIP/ARCA).\n"
    "Tu ÚNICA tarea es reportar la EVIDENCIA DE LECTURA: qué letra de "
    "comprobante (A, B, C, M o E) se ve o se lee en el documento, dónde se ve "
    "y con qué texto de sustento.\n"
    "\n"
    "NO decidas el tipo final del comprobante, NO apliques reglas de negocio, "
    "NO emitas alertas y NO calcules ningún nivel de confianza: otra etapa "
    "aplica las reglas sobre tu lectura. Si devolvés esos campos, se ignoran.\n"
    "\n"
    "No inventes datos. Si un dato no está disponible, usá null y listá el "
    "campo faltante en 'campos_desconocidos' en vez de asumir un valor.\n"
    "Devolvé únicamente JSON válido, sin markdown ni texto fuera del JSON.\n"
)

#: Guía de lectura por fuente. Cada entrada es el system prompt completo de esa
#: fuente: ``SYSTEM_PROMPT_TIPO_COMPROBANTE`` + la guía específica.
SYSTEM_PROMPT_TIPO_COMPROBANTE_VLM = (
    SYSTEM_PROMPT_TIPO_COMPROBANTE
    + "\n"
    + "Analizá únicamente la imagen adjunta.\n"
    + "- Localizá el recuadro cercano al encabezado del comprobante.\n"
    + "- Leé la letra grande dentro del recuadro: A, B, C, M o E.\n"
    + "- Priorizá siempre la letra del recuadro sobre cualquier inferencia.\n"
    + "- Si el recuadro muestra 'A' junto a 'COD. 01', la letra es A: el "
    + "código AFIP acompaña al recuadro pero NO reemplaza a la letra.\n"
    + "- Guardá esa letra en 'tipo_detectado_por_documento' y explicá en "
    + "'tipo_detectado_por_documento_explicacion' QUÉ letra viste, DÓNDE la "
    + "viste y qué código acompañaba al recuadro.\n"
    + "- Reportá 'fuente_lectura': \"vlm\".\n"
    + "- Usá únicamente datos visibles en la imagen.\n"
    + "\n"
    + "Esquema JSON obligatorio:\n"
    + _json_ejemplo()
)

SYSTEM_PROMPT_TIPO_COMPROBANTE_LLM = (
    SYSTEM_PROMPT_TIPO_COMPROBANTE
    + "\n"
    + "Analizá únicamente el texto OCR/Markdown recibido.\n"
    + "- Buscá A, B, C, M o E junto a FACTURA, COMPROBANTE, Nota de Crédito o "
    + "Nota de Débito.\n"
    + "- Priorizá expresiones del tipo FACTURA A, FACTURA B, FACTURA C, "
    + "FACTURA M y FACTURA E.\n"
    + "- Guardá la letra encontrada en 'tipo_detectado_por_documento'.\n"
    + "- Explicá en 'tipo_detectado_por_documento_explicacion' el texto OCR "
    + "literal que prueban la letra. Si no hay evidencia textual, usá null.\n"
    + "- Reportá 'fuente_lectura': \"llm\".\n"
    + "- Usá únicamente datos presentes en el texto.\n"
    + "\n"
    + "Esquema JSON obligatorio:\n"
    + _json_ejemplo()
)

#: System prompt por fuente (vocabulario = ``fuente_lectura`` del contrato).
SYSTEM_PROMPT_POR_FUENTE: dict[str, str] = {
    "vlm": SYSTEM_PROMPT_TIPO_COMPROBANTE_VLM,
    "llm": SYSTEM_PROMPT_TIPO_COMPROBANTE_LLM,
}

# ---------------------------------------------------------------------------
# Mensajes de usuario
# ---------------------------------------------------------------------------

#: Mensaje ``user`` para la fuente LLM: el markdown/OCR de F1 va en ``content``.
USER_TEXTO_TIPO_COMPROBANTE = (
    "Analizá el siguiente comprobante (texto OCR/Markdown) y reportá la "
    "evidencia de lectura.\n"
    "Devolvé únicamente el JSON del esquema indicado.\n"
    "\n"
    "--- COMPROBANTE ---\n"
    "{documento}\n"
    "--- FIN DEL COMPROBANTE ---"
)

#: Mensaje ``user`` para la fuente VLM: la imagen viaja en ``images`` (base64) y
#: este texto corto la acompaña.
USER_IMAGEN_TIPO_COMPROBANTE = (
    "Analizá la imagen adjunta y reportá la evidencia de lectura del "
    "comprobante. Prestá atención al recuadro con la letra grande y su código. "
    "Devolvé únicamente el JSON del esquema indicado."
)


def construir_messages_tipo_comprobante(
    *,
    fuente: str,
    markdown: str | None = None,
    vista: Any = None,
) -> list[dict[str, Any]]:
    """Construye los ``messages`` de ``OllamaClient.ask`` para una fuente (T-302).

    Desdobla el rol del orquestador (``evidencia.py``) y no toca la red, con el
    mismo patrón que ``construir_messages_gate`` de F2.

    Argumentos:
        fuente: ``"vlm"`` (imagen) o ``"llm"`` (texto). Selecciona el system
            prompt de :data:`SYSTEM_PROMPT_POR_FUENTE`.
        markdown: markdown/OCR de F1. **Obligatorio** para ``fuente="llm"``.
        vista: :class:`~voucherflow.validation.vistas.VistaPreparada` de F2 con
            ``ruta_imagen_original``. **Obligatoria** para ``fuente="vlm"``: la
            imagen se reduce y codifica con ``imagen_envio_base64`` (misma
            reducción que valida F2 para no exceder el ``num_ctx``).

    Devuelve:
        ``[system, user]``. Para ``vlm`` el ``user`` lleva ``images`` con el
        base64 de la imagen reducida (formato que exige Ollama ``/api/chat``).

    Lanza:
        ``ValueError`` con mensaje explicativo si la fuente no está en
        :data:`FUENTES_LECTURA`, o si falta el insumo de esa fuente (markdown
        para ``llm``, vista con imagen para ``vlm``).
    """
    if fuente not in SYSTEM_PROMPT_POR_FUENTE:
        raise ValueError(
            f"fuente inválida: {fuente!r}. Válidas: {FUENTES_LECTURA} "
            "(T-302, sistema de dos fuentes VLM/LLM)."
        )

    system: dict[str, Any] = {
        "role": "system",
        "content": SYSTEM_PROMPT_POR_FUENTE[fuente],
    }

    if fuente == "vlm":
        ruta = getattr(vista, "ruta_imagen_original", None)
        if not ruta:
            raise ValueError(
                "construir_messages_tipo_comprobante(fuente='vlm'): hace falta "
                "una vista de F2 con 'ruta_imagen_original' (la vista textual "
                "va por la fuente 'llm'; T-302)."
            )
        # Import diferido: mantiene liviano el import de ``classification`` y
        # reutiliza la reducción + base64 ya validada en F2 (una sola
        # implementación del formato que exige Ollama /api/chat).
        from ..validation.prompt_qween import imagen_envio_base64

        imagen_b64, _info = imagen_envio_base64(vista)
        user: dict[str, Any] = {
            "role": "user",
            "content": USER_IMAGEN_TIPO_COMPROBANTE,
            "images": [imagen_b64],
        }
        return [system, user]

    texto = (markdown or "").strip()
    if not texto:
        raise ValueError(
            "construir_messages_tipo_comprobante(fuente='llm'): el markdown "
            "está vacío; no hay documento sobre el cual leer la letra (T-302)."
        )
    user = {
        "role": "user",
        "content": USER_TEXTO_TIPO_COMPROBANTE.format(documento=texto),
    }
    return [system, user]


__all__ = [
    "VERSION_PROMPT_TIPO_COMPROBANTE",
    "CAMPOS_EVIDENCIA",
    "CAMPOS_FUERA_DEL_CONTRATO",
    "FUENTES_LECTURA",
    "ESQUEMA_JSON_EVIDENCIA",
    "SYSTEM_PROMPT_TIPO_COMPROBANTE",
    "SYSTEM_PROMPT_TIPO_COMPROBANTE_VLM",
    "SYSTEM_PROMPT_TIPO_COMPROBANTE_LLM",
    "SYSTEM_PROMPT_POR_FUENTE",
    "USER_TEXTO_TIPO_COMPROBANTE",
    "USER_IMAGEN_TIPO_COMPROBANTE",
    "construir_messages_tipo_comprobante",
]

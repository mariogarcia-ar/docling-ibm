"""Prompt de **evidencia** de extracción key-value, versionado (F4 / T-401).

**Fase**: F4 (extracción) · **Tarea**: T-401 · **Épica**: E-EXT-1.

Qué cambia respecto de los prompts de v1
---------------------------------------
Los prompts de extracción de v1 (`10-extraction_key_value_generic_prompt.yaml`,
`11-extraction_key_value_invoice_prompt.yaml`, modos ``kvg``/``kvi`` de
``v1/document_extraction.py``) pedían un **JSON plano normalizado y decidido**:
el modelo devolvía ``cuit_emisor`` ya "cortado" en el CUIT, ``fecha_emision`` ya
en ``YYYY-MM-DD``, ``comprobante_valido`` ya evaluado y ``categoria_gasto`` ya
inferida. Eso es exactamente lo que **ADR-001** prohíbe: el contrato de
evidencia exige que cada flujo devuelva, por campo, **el valor tal como se leyó
+ el fragmento de sustento**, y que los datos derivados (normalizaciones,
validaciones, agregaciones) los calcule el programa.

Este módulo congela el **texto del prompt** (versionado — ADR-005) y la
construcción de los ``messages`` de ``OllamaClient.ask``, con el mismo
desdoblamiento que F2/T-202 (``validation/prompt_qween.py``) y F3/T-302
(``classification/prompt_tipo_comprobante.py``): el *qué se le pide* queda
separado del *cómo se interpreta la respuesta* (``evidencia.py``).

Diferencias concretas con v1 (todas deliberadas):

============================================  ==================================
v1 (kvi/kvg)                                   v2 (``extraccion-key-value@1``)
============================================  ==================================
pide ``cuit_emisor`` ya normalizado            pide ``valor_crudo`` del CUIT tal como
                                               se lee (T-402 normaliza)
pide fechas ``AAAA-MM-DD``                     pide la fecha como está impresa
pide ``subtotal``/``iva``/``total`` decididos  pide cada lectura con su sustento
pide ``comprobante_valido``                    **fuera del contrato**: el estado lo
                                               resuelve el programa (F5)
pide ``categoria_gasto`` inferida              **fuera del contrato**: es una
                                               decisión de negocio, no una lectura
pide "devolvé JSON plano"                      pide un **objeto por campo** con
                                               ``valor`` + ``fragmento_sustento``
============================================  ==================================

Contrato de salida (shape del campo de F3-T-302, doc 03 §6)
----------------------------------------------------------
El prompt pide ``"campos": { "<nombre>": {"valor": …, "fragmento_sustento": …} }``:
es el **mismo shape** del contrato de evidencia de F3/T-302 (unidad mínima =
``EvidenceField``), de modo que el intérprete de ``evidencia.py`` pueda mapearlo
a ``SourceEvidence`` sin traducciones ad-hoc (ADR-001: todos los flujos hablan el
mismo esquema).

Dos fuentes, dos system prompts (E-EXT-1)
-----------------------------------------
``system_vlm`` mira la **imagen** (vista fiel de F2) y exige que el sustento sea
una descripción de lo que se ve; ``system_llm`` mira el **OCR/Markdown** de F1 y
exige que el sustento sea el **texto literal** del documento. Los **campos a
extraer** son los mismos en ambas (la evidencia de los dos flujos debe poder
compararse campo a campo: ADR-002 resuelve el desacuerdo en T-404).

Formato de la imagen en ``messages``: idéntico al validado empíricamente en
F2/T-202 y reutilizado en F3/T-302 — ``images`` con la imagen en **base64**
reducida por ``imagen_envio_base64`` (Ollama ``/api/chat`` interpreta cada ítem
de ``images`` como base64 y una imagen sin reducir excede el ``num_ctx`` del
modelo local). La vista fiel **no** se reduce (``LADO_MAYOR_OBJETIVO_POR_VISTA``
es ``None`` para ``fiel``): es la vista de máxima fidelidad de la extracción
(E-QWE-2), y ``imagen_envio_base64`` respeta esa decisión.
"""

from __future__ import annotations

from typing import Any

# ---------------------------------------------------------------------------
# Versión del prompt (ADR-005: trazabilidad de prompt por versión)
# ---------------------------------------------------------------------------

#: Identificador versionado del prompt de extracción key-value. Se registra en
#: ``EvidenceField.meta.version_prompt`` de cada campo (ADR-005 / E-CONC-5).
#
# Historial de versiones:
#   - ``extraccion-key-value@1``: primer prompt de **evidencia** de extracción
#     (T-401). Porta de v1 (``10``/``11``) la **lista de campos**, las
#     correcciones de OCR (O/0 dentro de palabras de texto libre) y la
#     instrucción de no inventar, pero **quita toda decisión y normalización**
#     (el valor se reporta tal como se lee, con su fragmento de sustento) y
#     agrega el contrato de evidencia por campo (ADR-001). Los campos derivados
#     de v1 (``comprobante_valido``, ``motivo_rechazo``, ``categoria_gasto``,
#     ``centro_de_costo``, ``alicuotas_detectadas``, ``monto_no_gravado``) quedan
#     **fuera** del contrato: los calcula el programa o los resuelve el negocio.
VERSION_PROMPT_EXTRACCION = "extraccion-key-value@1"

# ---------------------------------------------------------------------------
# Campos a extraer (mismo vocabulario en las dos fuentes)
# ---------------------------------------------------------------------------

#: Campos **fiscales/comerciales** que el negocio necesita para clasificar y
#: conciliar (E-EXT-3), en el orden en que se le piden al modelo. El vocabulario
#: es **compartido por las dos fuentes** (VLM y LLM): la evidencia de ambas debe
#: ser comparable campo a campo (ADR-002, T-404).
#:
#: Los nombres siguen el vocabulario de v1 (``11-extraction_key_value_invoice``)
#: para que la paridad de T-405 sea verificable campo a campo.
CAMPOS_EXTRACCION: tuple[str, ...] = (
    "tipo_comprobante",
    "razon_social_emisor",
    "cuit_emisor",
    "razon_social_receptor",
    "cuit_receptor",
    "fecha_emision",
    "nro_comprobante",
    "moneda",
    "subtotal",
    "iva",
    "impuestos_internos",
    "percepcion_iibb",
    "otros_impuestos",
    "monto_no_gravado",
    "importe_total_facturado",
    "descripcion",
)

#: Campos que el modelo **no** debe devolver y que los prompts de v1 sí pedían.
#: Se listan para dejarlo explícito en el prompt y en los tests (ADR-001/ADR-006):
#:
#:  * ``comprobante_valido`` / ``motivo_rechazo``: los resuelve el programa
#:    (F5, reglas cruzadas) — el modelo no decide si el comprobante sirve.
#:  * ``categoria_gasto``: inferencia de negocio, no lectura del documento.
#:  * ``centro_de_costo``: lo completa la cadena contable (F3/T-304), no la
#:    extracción.
#:  * ``alicuotas_detectadas`` / ``condicion_impositiva_dominante``: datos
#:    **derivados** (se calculan sobre las alícuotas leídas).
#:  * ``cantidad_comensales_personas`` / ``cantidad_litros``: campos
#:    condicionales que dependen de ``categoria_gasto`` (que ya no se decide acá).
CAMPOS_FUERA_DEL_CONTRATO: tuple[str, ...] = (
    "comprobante_valido",
    "motivo_rechazo",
    "categoria_gasto",
    "centro_de_costo",
    "alicuotas_detectadas",
    "condicion_impositiva_dominante",
    "cantidad_comensales_personas",
    "cantidad_litros",
)

#: Fuentes de extracción (mismo vocabulario que ``schemas.evidence.Fuente`` y que
#: ``classification.prompt_tipo_comprobante.FUENTES_LECTURA``). Los dos flujos
#: corren **siempre** (E-EXT-1): no se elige uno por documento.
FUENTES_EXTRACCION: tuple[str, ...] = ("vlm", "llm")

#: Placeholder del documento de texto en el mensaje ``user`` del LLM.
PLACEHOLDER_DOCUMENTO = "{{documento}}"

# ---------------------------------------------------------------------------
# Esquema JSON del contrato (ejemplo incrustado en el prompt)
# ---------------------------------------------------------------------------


def _json_ejemplo(fuente: str = "llm") -> str:
    """Renderiza el esquema JSON del contrato como ejemplo para el prompt.

    Es el **mismo shape** que valida ``evidencia.py`` (``campos`` con ``valor`` +
    ``fragmento_sustento``) y se genera con ``json.dumps`` (no un literal a mano)
    para que el ejemplo impreso y el contrato validado no puedan divergir — el
    mismo criterio que ``prompt_tipo_comprobante._json_ejemplo`` de F3/T-302.

    ``fuente`` hace que el ejemplo de ``fuente_lectura`` coincida con la guía que
    lo contiene (el VLM no debería ver un ejemplo que declare ``llm``).

    El ejemplo usa **pocos campos** (los primeros de :data:`CAMPOS_EXTRACCION`)
    para no inflar el prompt: la lista completa de campos se imprime aparte con
    :func:`_lista_campos`, y el ``system`` común explica la forma del contrato.
    """
    import json

    ejemplo: dict[str, Any] = {
        "fuente_lectura": fuente,
        "campos": {
            nombre: {
                "valor": "…",
                "fragmento_sustento": "…",
            }
            for nombre in CAMPOS_EXTRACCION[:4]
        },
    }
    return json.dumps(ejemplo, ensure_ascii=False, indent=2)


def _lista_campos() -> str:
    """Renderiza la lista de campos esperados para el prompt (uno por línea).

    El modelo necesita saber **qué** buscar; la lista viaja en las dos guías por
    fuente (es el mismo vocabulario, para que las dos evidencias sean comparables
    campo a campo — ADR-002). Es una lista de *posibles* campos: no declarar uno
    es válido (el prompt pide no inventar).
    """
    return "\n".join(f"- {campo}" for campo in CAMPOS_EXTRACCION)


# ---------------------------------------------------------------------------
# System prompts: base común + guía por fuente
# ---------------------------------------------------------------------------

#: Base común del system prompt (tarea = evidencia, no decisión). Porta de los
#: prompts 10/11 de v1 lo que **no** es una decisión: la corrección de O/0 en
#: palabras de texto libre y la prohibición de inventar datos.
SYSTEM_PROMPT_EXTRACCION = (
    "Sos un extractor de datos de comprobantes argentinos (AFIP/ARCA).\n"
    "Tu ÚNICA tarea es reportar la EVIDENCIA DE LECTURA: para cada campo, el "
    "valor tal como figura en el documento y el fragmento de texto o la "
    "descripción visual que lo sustenta.\n"
    "\n"
    "NO normalices los datos: NO conviertas fechas de formato, NO quites "
    "separadores de miles, NO completes ni corrijas CUIT, NO decidas si el "
    "comprobante es válido, NO infieras la categoría del gasto y NO calcules "
    "impuestos. Otra etapa normaliza y decide sobre tu lectura. Si devolvés "
    "esos campos, se ignoran.\n"
    "\n"
    "No inventes datos. Si un campo no está en el documento o no se puede "
    "leer, NO lo incluyas (o usá null): nunca lo asumas.\n"
    "\n"
    "Devolvé un objeto por cada campo que puedas leer, con esta forma exacta:\n"
    "  'valor': el dato tal como figura en el documento\n"
    "  'fragmento_sustento': el texto o la descripción que prueba ese valor\n"
    "\n"
    "Lo único que SÍ debés corregir es la confusión O/0 del OCR dentro de "
    "palabras de texto libre (por ejemplo 'ROSARI0' -> 'ROSARIO', 'GRAYADO' -> "
    "'GRAVADO'). No toques números, CUIT, fechas ni montos.\n"
    "\n"
    "Devolvé únicamente JSON válido, sin markdown ni texto fuera del JSON.\n"
)

#: Guía del flujo **VLM** (lee la imagen de la vista fiel).
SYSTEM_PROMPT_EXTRACCION_VLM = (
    SYSTEM_PROMPT_EXTRACCION
    + "\n"
    + "Analizá únicamente la imagen adjunta del comprobante.\n"
    + "- Recorré el encabezado (emisor/receptor, tipo, número y fecha) y el "
    + "detalle de importes (subtotal, IVA, percepciones, total).\n"
    + "- Para cada campo, reportá en 'fragmento_sustento' QUÉ se ve y DÓNDE "
    + "(por ejemplo: \"Recuadro superior izquierdo con 'FACTURA A' y COD. 01\" "
    + "o \"línea 'Importe Total: $ 12.345,67' al pie\"). El sustento del VLM es "
    + "una **descripción visual**, no el texto OCR.\n"
    + "- Si un valor no se ve con claridad, no lo reportes.\n"
    + "- Reportá la fuente: \"vlm\".\n"
    + "\n"
    + "Campos a leer (reportá solo los que puedas ver):\n"
    + _lista_campos()
    + "\n\n"
    + "Esquema JSON obligatorio:\n"
    + _json_ejemplo("vlm")
)

#: Guía del flujo **LLM** (lee el OCR/Markdown de F1).
SYSTEM_PROMPT_EXTRACCION_LLM = (
    SYSTEM_PROMPT_EXTRACCION
    + "\n"
    + "Analizá únicamente el texto OCR/Markdown recibido.\n"
    + "- Buscá cada campo junto a su etiqueta impresa (por ejemplo 'CUIT', "
    + "'Fecha de Emisión', 'Importe Total', 'Subtotal', 'IVA 21%').\n"
    + "- Para cada campo, copiá en 'fragmento_sustento' el **texto OCR "
    + "literal** de la línea donde aparece el valor (sin corregirlo).\n"
    + "- El emisor es quien encabeza el documento (membrete); el receptor "
    + "aparece después. Usá los sufijos '_emisor' y '_receptor'.\n"
    + "- Si un campo no aparece en el texto, no lo reportes.\n"
    + "- Reportá la fuente: \"llm\".\n"
    + "\n"
    + "Campos a leer (reportá solo los que encuentres):\n"
    + _lista_campos()
    + "\n\n"
    + "Esquema JSON obligatorio:\n"
    + _json_ejemplo("llm")
)

#: System prompt por fuente (vocabulario = ``fuente_lectura`` del contrato).
SYSTEM_PROMPT_POR_FUENTE: dict[str, str] = {
    "vlm": SYSTEM_PROMPT_EXTRACCION_VLM,
    "llm": SYSTEM_PROMPT_EXTRACCION_LLM,
}

# ---------------------------------------------------------------------------
# Mensajes de usuario
# ---------------------------------------------------------------------------

#: Mensaje ``user`` para la fuente LLM: el markdown/OCR de F1 va en ``content``.
USER_TEXTO_EXTRACCION = (
    "Extraé la evidencia de los campos del siguiente comprobante "
    "(texto OCR/Markdown).\n"
    "Devolvé únicamente el JSON del esquema indicado.\n"
    "\n"
    "--- COMPROBANTE ---\n"
    "{{documento}}\n"
    "--- FIN DEL COMPROBANTE ---"
)

#: Mensaje ``user`` para la fuente VLM: la imagen viaja en ``images`` (base64) y
#: este texto corto la acompaña.
USER_IMAGEN_EXTRACCION = (
    "Analizá la imagen adjunta del comprobante y extraé la evidencia de los "
    "campos indicados. Prestá atención al encabezado y al detalle de importes. "
    "Devolvé únicamente el JSON del esquema indicado."
)


def construir_messages_extraccion(
    *,
    fuente: str,
    markdown: str | None = None,
    vista: Any = None,
) -> list[dict[str, Any]]:
    """Construye los ``messages`` de ``OllamaClient.ask`` para una fuente (T-401).

    Desdobla el rol del orquestador (``evidencia.py``) y no toca la red, con el
    mismo patrón que ``construir_messages_tipo_comprobante`` de F3/T-302 y
    ``construir_messages_gate`` de F2/T-202.

    Argumentos:
        fuente: ``"vlm"`` (imagen) o ``"llm"`` (texto). Selecciona el system
            prompt de :data:`SYSTEM_PROMPT_POR_FUENTE`.
        markdown: markdown/OCR de F1. **Obligatorio** para ``fuente="llm"``.
        vista: :class:`~voucherflow.validation.vistas.VistaPreparada` de F2 con
            ``ruta_imagen_original``. **Obligatoria** para ``fuente="vlm"``: la
            imagen se reduce y codifica con ``imagen_envio_base64`` (misma
            reducción que valida F2, para no exceder el ``num_ctx``). La vista
            ``fiel`` no se reduce (``LADO_MAYOR_OBJETIVO_POR_VISTA["fiel"]`` es
            ``None``): es la vista de máxima fidelidad de la extracción.

    Devuelve:
        ``[system, user]``. Para ``vlm`` el ``user`` lleva ``images`` con el
        base64 de la imagen (formato que exige Ollama ``/api/chat``).

    Lanza:
        ``ValueError`` con mensaje explicativo si la fuente no está en
        :data:`FUENTES_EXTRACCION`, o si falta el insumo de esa fuente (markdown
        para ``llm``, vista con imagen para ``vlm``).
    """
    if fuente not in SYSTEM_PROMPT_POR_FUENTE:
        raise ValueError(
            f"fuente inválida: {fuente!r}. Válidas: {FUENTES_EXTRACCION} "
            "(T-401, sistema de dos fuentes VLM/LLM en paralelo)."
        )

    system: dict[str, Any] = {
        "role": "system",
        "content": SYSTEM_PROMPT_POR_FUENTE[fuente],
    }

    if fuente == "vlm":
        ruta = getattr(vista, "ruta_imagen_original", None)
        if not ruta:
            raise ValueError(
                "construir_messages_extraccion(fuente='vlm'): hace falta una "
                "vista de F2 con 'ruta_imagen_original' (el texto va por la "
                "fuente 'llm'; T-401)."
            )
        # Import diferido: mantiene liviano el import de ``extraction`` y
        # reutiliza la reducción + base64 ya validada en F2 (una sola
        # implementación del formato que exige Ollama /api/chat).
        from ..validation.prompt_qween import imagen_envio_base64

        imagen_b64, _info = imagen_envio_base64(vista)
        user: dict[str, Any] = {
            "role": "user",
            "content": USER_IMAGEN_EXTRACCION,
            "images": [imagen_b64],
        }
        return [system, user]

    texto = (markdown or "").strip()
    if not texto:
        raise ValueError(
            "construir_messages_extraccion(fuente='llm'): el markdown está "
            "vacío; no hay documento sobre el cual extraer (T-401)."
        )
    user = {
        "role": "user",
        "content": USER_TEXTO_EXTRACCION.replace(PLACEHOLDER_DOCUMENTO, texto),
    }
    return [system, user]


__all__ = [
    "VERSION_PROMPT_EXTRACCION",
    "CAMPOS_EXTRACCION",
    "CAMPOS_FUERA_DEL_CONTRATO",
    "FUENTES_EXTRACCION",
    "PLACEHOLDER_DOCUMENTO",
    "SYSTEM_PROMPT_EXTRACCION",
    "SYSTEM_PROMPT_EXTRACCION_VLM",
    "SYSTEM_PROMPT_EXTRACCION_LLM",
    "SYSTEM_PROMPT_POR_FUENTE",
    "USER_TEXTO_EXTRACCION",
    "USER_IMAGEN_EXTRACCION",
    "construir_messages_extraccion",
]

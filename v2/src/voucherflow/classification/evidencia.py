"""Evidencia de lectura de tipo/letra + protocolo de lector (F3 / T-302).

**Fase**: F3 (clasificación) · **Tarea**: T-302 · **Épica**: E-CLAS-1.

Este módulo cierra el desdoblamiento que exige **ADR-006**: el modelo **lee y
reporta evidencia**; el motor determinístico de T-301 **decide la letra**. Aquí
viven las tres piezas que hacen ese puente:

1. :class:`EvidenciaLectura` — la evidencia de lectura de **una** fuente,
   normalizada y trazable (fuente, letra, fragmento de sustento, candidatos,
   campos que faltaron, problemas detectados).
2. :class:`Lector` — el **protocolo de lector** (F3-subplan §2.6): un callable
   inyectable que recibe los ``messages`` del prompt de evidencia y devuelve la
   respuesta del modelo. Los flujos VLM/LLM reales de ``extraction/flows.py``
   son de **F4/T-401** y todavía lanzan ``NotImplementedError``; F3 **no** los
   implementa ni los llama — los tests inyectan un doble y F4 conecta el real
   sin refactor.
3. :func:`leer_evidencia` — el orquestador que corre las fuentes disponibles
   (``vlm`` con el recuadro, ``llm`` con el texto), conserva **ambas**
   evidencias sin colapsarlas (ADR-001/ADR-002) y arma el
   :class:`~voucherflow.rules.contexto.ContextoTipoComprobante` de lectura para
   que T-301 decida.

Por qué la letra se re-valida acá
---------------------------------
El modelo puede devolver ``"Z"``, ``"090"``, ``"factura a"`` o ``null``. El
contrato de vocabulario lo fija el enum ``TipoComprobante`` de
``schemas/evidence.py`` (sin los códigos de tique ``090``/``099``: decisión
abierta **D-13**, ver F3-subplan §2.8). :func:`parsear_evidencia_lectura` usa
:func:`~voucherflow.rules.contexto.normalizar_letra` — el **mismo normalizador**
que R4/R5 — de modo que la evidencia que llega al motor jamás aporta una letra
que el motor no pueda comparar. Lo que no valida **no se inventa**: se
descarta la letra, se conserva el crudo en el fragmento de sustento (auditoría)
y se anota el motivo en ``campos_desconocidos`` y en ``debilidades``.

Nota de honestidad (alcance T-302): las reglas **raw** por fuente (validez del
sustento, contradicciones entre texto y letra declarada, veredicto por fuente)
son de **T-303** (``rules/raw.py``); ``SourceEvidence.valida`` queda en ``True``
con ``reglas_aplicadas`` vacío hasta que esa tarea las implemente. La
**curaduría fina** de ``candidatos_descartados``/``candidatos_restantes`` con
esas reglas también es de T-303: acá se aplica solo una normalización de higiene
(documentada en :func:`parsear_evidencia_lectura`), no una decisión.

El ``SourceEvidence`` que se construye (ADR-001) lleva un ``EvidenceField`` por
lectura con ``campo`` = :data:`EVIDENCIA_CAMPO_LETRA`, el ``fragmento_sustento``
que exige la auditoría E-CONC-5 y ``meta.version_prompt`` =
``tipo-comprobante@1`` (ADR-005). Los candidatos y campos faltantes viajan en
``meta`` (no como campos nuevos): el vocabulario de campos per-campo lo define
F4/T-404 con su tabla de precedencia (ADR-002), y T-302 no debe anticiparlo.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any, Iterable, Mapping, Protocol, runtime_checkable

from ..rules.contexto import (
    LETRAS_COMPROBANTE,
    ContextoTipoComprobante,
    letra_en_vocabulario,
    normalizar_letra,
)
from ..rules.tipo_comprobante_rules import extraer_letra_encabezado
from ..schemas.evidence import EvidenceField, Fuente, SourceEvidence, nueva_meta
from ..settings.config import Settings, cargar_settings
from .prompt_tipo_comprobante import (
    FUENTES_LECTURA,
    VERSION_PROMPT_TIPO_COMPROBANTE,
    construir_messages_tipo_comprobante,
)

# ---------------------------------------------------------------------------
# Constantes del contrato de evidencia
# ---------------------------------------------------------------------------

#: Nombre del campo de evidencia de la lectura (ADR-001). Coincide con el
#: nombre del campo del contrato del prompt (F3-subplan §6) para que la
#: evidencia y el contexto hablen del mismo campo.
EVIDENCIA_CAMPO_LETRA = "tipo_detectado_por_documento"

#: Campo que se reporta como faltante cuando la lectura no aportó explicación
#: (fragmento de sustento). El prompt lo exige; si no viene, la evidencia es
#: débil y debe verse (ADR-001: sin sustento no hay evidencia auditable).
CAMPO_EXPLICACION = "tipo_detectado_por_documento_explicacion"

#: Campos que se reportan como faltantes cuando el modelo los omite. Se listan
#: para dejar explícito qué se trazó (el detalle de la telemetría de la
#: lectura); la ausencia de la letra y del sustento ya se registra en los pasos
#: 1 y 2 de :func:`parsear_evidencia_lectura`.
CAMPOS_TRAZABLES: tuple[str, ...] = (
    "tipo_detectado_por_documento",
    CAMPO_EXPLICACION,
    "candidatos_restantes",
)

#: Fuente de ``schemas.evidence`` que corresponde a cada fuente de lectura. El
#: vocabulario de ``fuente_lectura`` del prompt (``vlm``/``llm``) coincide con
#: el enum ``Fuente``: la evidencia conserva de dónde salió la lectura.
_FUENTE_SCHEMA: dict[str, Fuente] = {"vlm": Fuente.vlm, "llm": Fuente.llm}

#: Motivo con el que se marca una lectura cuando el ``fuente_lectura`` que
#: declaró el modelo no coincide con la fuente del system prompt usado (dato
#: menor: la fuente autoritativa la fija el orquestador, no el modelo).
MOTIVO_FUENTE_DECLARADA_DISTINTA = (
    "El modelo declaró fuente_lectura='{declarada}' pero la lectura se pidió "
    "con el system prompt de '{real}'; se conserva la fuente real (el modelo "
    "no decide la trazabilidad)."
)

#: Motivo con el que se marca una letra fuera del vocabulario (D-13).
MOTIVO_LETRA_FUERA_VOCABULARIO = (
    "El modelo devolvió la letra {crudo!r}, fuera del vocabulario del motor "
    "{vocabulario} (el enum TipoComprobante sin los tiques 090/099, decisión "
    "D-13); no se inventa una letra válida."
)

MOTIVO_LETRA_NO_TEXTO = (
    "El modelo devolvió 'tipo_detectado_por_documento' de tipo {tipo} en lugar "
    "de una letra; se descarta el valor y se registra el campo como faltante."
)

#: Motivo con el que se marca una lectura de texto que reporta letra pero cuyo
#: fragmento no contiene una expresión que R5 sepa extraer
#: (``FACTURA X``/``COMPROBANTE X``). Sin esta nota, la letra leída se perdería
#: en silencio al pasar por la regex de R5 (el motor solo lee **texto**, no la
#: letra suelta). No es un veredicto de contradicción — eso es T-303.
MOTIVO_FRAGMENTO_SIN_PATRON_R5 = (
    "La fuente de texto reportó la letra {letra!r}, pero su fragmento de "
    "sustento no contiene una expresión 'FACTURA <letra>' ni 'COMPROBANTE "
    "<letra>' que R5 pueda extraer; el motor no confirmará la letra con esta "
    "evidencia (el texto crudo del encabezado es el insumo de R5)."
)

#: Motivo con el que se marca una lectura sin fragmento de sustento. El prompt
#: lo exige (ADR-001); si falta, la evidencia **no es auditable** y por eso se
#: registra como debilidad de la fuente, no solo como campo faltante.
MOTIVO_SIN_SUSTENTO = (
    "La fuente no reportó el fragmento de sustento "
    "('tipo_detectado_por_documento_explicacion'): la lectura no es auditable "
    "según ADR-001 (no se puede verificar de dónde salió la letra)."
)


class ErrorEvidencia(ValueError):
    """La respuesta del modelo no cumple el contrato de evidencia (E-LIB-2).

    Se lanza cuando el contenido **no es JSON** o no es un objeto JSON. Un JSON
    válido con campos faltantes o valores fuera del vocabulario **no** es un
    error: es evidencia débil y se reporta por
    :attr:`EvidenciaLectura.campos_desconocidos` / ``debilidades`` (el modelo no
    decide; una lectura pobre no debe romper el flujo, T-302).

    Hereda de ``ValueError`` a propósito (no de
    :class:`~voucherflow.api.ContratoError`, que es un ``RuntimeError``):
    importar ``api`` desde ``classification`` cerraría un ciclo de import
    (``api.classify`` importa este módulo de forma diferida en T-304). El punto
    de contacto con la fachada (T-304) traduce este error al error público.
    """


# ---------------------------------------------------------------------------
# Lector inyectable (protocolo F3-subplan §2.6)
# ---------------------------------------------------------------------------


@runtime_checkable
class Lector(Protocol):
    """Protocolo del lector de evidencia (F3-subplan §2.6).

    Es el **mismo** contrato que ya usa F2 para el gate: un objeto con ``ask``.
    Por eso :class:`~voucherflow.models.ollama.OllamaClient` lo satisface
    estructuralmente y, en la suite default, se inyecta un doble
    (``FakeOllamaClient``) — sin Ollama real.

    Argumentos de ``ask`` (subconjunto que usa T-302):

    - ``messages``: ``[system, user]`` de
      :func:`~voucherflow.classification.prompt_tipo_comprobante.construir_messages_tipo_comprobante`.
    - ``model``: modelo a usar (se resuelve del rol ``vlm``/``llm`` de
      ``Settings`` si el llamador no lo fija).
    - ``num_ctx``: ventana de contexto del modelo (viene de ``Settings``).
    - ``json_format``: ``True`` — el prompt pide JSON y el formato se fuerza en
      la API (``format="json"``), reduciendo el riesgo de prosa alrededor.

    Devuelve un objeto con ``.contenido`` (str). Es lo único que T-302 consume.
    """

    def ask(
        self,
        messages: list[dict[str, Any]],
        model: str,
        json_format: bool = False,
        options: dict[str, Any] | None = None,
        num_ctx: int | None = None,
    ) -> Any:  # pragma: no cover - contrato estructural
        ...


# ---------------------------------------------------------------------------
# Evidencia de lectura de una fuente
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class EvidenciaLectura:
    """Evidencia de lectura de **una** fuente (T-302).

    Es la lectura ya normalizada: lo que el modelo reportó, pasado por el
    vocabulario del motor y con la trazabilidad de qué faltó. No decide nada —
    T-301 compara :attr:`letra` con la letra esperada por negocio.

    Campos:
        fuente: ``"vlm"`` (recuadro de la imagen) o ``"llm"`` (texto OCR). Es la
            fuente **real** de la corrida (no lo que declaró el modelo).
        letra: letra del vocabulario del motor (``A``/``B``/``C``/``M``/``E``) o
            ``None`` si la lectura no aportó una letra válida.
        fragmento: ``fragmento_sustento`` (ADR-001) — el recuadro visto o el
            texto OCR literal que sustenta la letra. String vacío si el modelo
            no explicó nada.
        candidatos_descartados: letras que el documento descarta (normalizadas).
        candidatos_restantes: letras que siguen posibles (normalizadas).
        campos_desconocidos: qué faltó para poder leer con más certeza.
        problemas: motivos por los que la lectura quedó débil (letra fuera de
            vocabulario, sin sustento, etc.). Insumo directo de
            ``SourceEvidence.debilidades`` (que T-303/T-403 enriquecerán con las
            reglas raw).
        fuente_declarada: ``fuente_lectura`` que declaró el modelo (puede ser
            ``None`` si no lo declaró, o distinta de :attr:`fuente`).
        crudo: la respuesta textual del modelo (auditoría / diagnóstico).
    """

    fuente: str
    letra: str | None = None
    fragmento: str = ""
    candidatos_descartados: list[str] = field(default_factory=list)
    candidatos_restantes: list[str] = field(default_factory=list)
    campos_desconocidos: list[str] = field(default_factory=list)
    problemas: list[str] = field(default_factory=list)
    fuente_declarada: str | None = None
    crudo: str = ""

    @property
    def valida(self) -> bool:
        """True si la lectura es utilizable **sin reservas** (T-302).

        Exige letra del vocabulario y fragmento de sustento no vacío (ADR-001:
        sin sustento no hay evidencia auditable). No es el veredicto de las
        reglas raw — eso es T-303 — sino la higiene mínima que T-302 garantiza.
        """
        return self.letra is not None and bool(self.fragmento.strip())


# ---------------------------------------------------------------------------
# Interpretación de la respuesta del modelo
# ---------------------------------------------------------------------------


def _parsear_json(contenido: str) -> Any:
    """Extrae el objeto JSON de la respuesta del modelo (T-302).

    Tolerante con las formas reales que devuelven los modelos locales (mismo
    problema que resolvía ``extract_json`` de v1): JSON puro, JSON dentro de un
    bloque markdown ```` ```json ````, o JSON con prosa alrededor. Se intenta,
    en orden: ``json.loads`` directo → sin cercas de código → primer objeto JSON
    completo con ``raw_decode``.

    Lanza:
        :class:`ErrorEvidencia` si no hay ningún objeto JSON parseable. El
        mensaje incluye el inicio de la respuesta para poder diagnosticar.
    """
    texto = contenido.strip()
    if not texto:
        raise ErrorEvidencia(
            "El modelo devolvió una respuesta vacía donde se esperaba el JSON de "
            "evidencia (T-302)."
        )

    intentos: list[str] = [texto]

    # Quitar cercas de código markdown (```json ... ``` o ``` ... ```).
    if "```" in texto:
        partes = texto.split("```")
        for indice, parte in enumerate(partes):
            if indice % 2 == 1:  # el contenido entre cercas
                intentos.append(parte.removeprefix("json").strip())

    for candidato in intentos:
        try:
            return json.loads(candidato)
        except (ValueError, TypeError):
            continue

    # Último recurso: el primer objeto JSON completo dentro del texto.
    inicio = texto.find("{")
    if inicio >= 0:
        try:
            objeto, _fin = json.JSONDecoder().raw_decode(texto[inicio:])
            return objeto
        except ValueError:
            pass

    raise ErrorEvidencia(
        "La respuesta del modelo no contiene un objeto JSON válido de evidencia "
        f"(T-302). Respuesta recibida (primeros 200 caracteres): {texto[:200]!r}"
    )


def _normalizar_letra_cruda(valor: Any) -> str | None:
    """Normaliza una letra del modelo al vocabulario del motor (T-302).

    Delega en :func:`~voucherflow.rules.contexto.normalizar_letra` — el mismo
    normalizador de R4/R5 — para que la evidencia y el motor usen un único
    criterio. Acepta solo ``str``: un entero u otro tipo se considera ausencia
    (no se castea a texto: ``090`` numérico no es una letra).
    """
    if not isinstance(valor, str):
        return None
    return normalizar_letra(valor)


def _normalizar_lista_letras(valor: Any, *, excluir: str | None = None) -> list[str]:
    """Normaliza una lista de letras del modelo (candidatos) (T-302).

    Reglas de higiene (documentadas; la curaduría fina es T-303):

    - Se aceptan solo letras del vocabulario del motor (el resto se descarta en
      silencio: no se puede razonar sobre una letra que el motor no conoce).
    - Se **deduplica** conservando el orden de aparición.
    - ``excluir`` permite sacar una letra puntual: se usa con la letra leída en
      **descartados** (una letra que el documento muestra no puede figurar como
      descartada). No se usa en **restantes**: la letra leída es, por
      definición, una alternativa que sigue viva — quitarla vaciaría la lista.
    """
    if not isinstance(valor, (list, tuple)):
        return []
    normalizadas: list[str] = []
    for item in valor:
        letra = _normalizar_letra_cruda(item)
        if letra is None or letra == excluir or letra in normalizadas:
            continue
        normalizadas.append(letra)
    return normalizadas


def _normalizar_campos(valor: Any) -> list[str]:
    """Normaliza ``campos_desconocidos`` a lista de strings no vacíos (T-302)."""
    if isinstance(valor, str):
        return [valor.strip()] if valor.strip() else []
    if not isinstance(valor, (list, tuple)):
        return []
    salida: list[str] = []
    for item in valor:
        texto = str(item).strip()
        if texto and texto not in salida:
            salida.append(texto)
    return salida


def parsear_evidencia_lectura(
    contenido: str,
    *,
    fuente: str,
) -> EvidenciaLectura:
    """Interpreta la respuesta del modelo y devuelve la evidencia (T-302).

    Es el paso que separa *qué se le pidió* (``prompt_tipo_comprobante.py``) de
    *qué se entendió*. Nunca decide la letra final: solo normaliza lo que la
    fuente reportó y registra lo que faltó.

    Normalizaciones aplicadas (todas documentadas, ninguna inventa datos):

    1. Letra fuera del vocabulario (``"Z"``, ``"090"``, ``"factura A"``) o de
       tipo no textual → se descarta la letra (queda ``None``), se conserva el
       crudo y se agrega ``tipo_detectado_por_documento`` a
       ``campos_desconocidos`` (D-13 / ADR-001).
    2. Explicación (fragmento de sustento) vacía o ausente → se agrega
       :data:`CAMPO_EXPLICACION` a ``campos_desconocidos`` (sin sustento la
       evidencia no es auditable).
    3. Valores ``null``/ausentes de los campos trazables → se agregan a
       ``campos_desconocidos``.
    4. Candidatos: solo letras del vocabulario, deduplicadas, sin la letra
       propia (higiene; **la curaduría es T-303**).
    5. Intersección descartados ∩ restantes → se conserva en ``restantes`` y se
       quita de ``descartados``. Motivo: ``candidatos_restantes`` es la lista
       que consume la decisión y **ADR-008** prohíbe que una letra figure a la
       vez como descartada y restante (blindaje del contrato); el sesgo
       conservador evita tachar una alternativa que el modelo también considera
       viable. Queda anotado en ``problemas`` para que T-303 lo revise.

    Argumentos:
        contenido: la respuesta textual del modelo.
        fuente: ``"vlm"`` o ``"llm"`` — la fuente **real** de la corrida.

    Lanza:
        ``ValueError`` si ``fuente`` no es una de :data:`FUENTES_LECTURA`.
        :class:`ErrorEvidencia` si la respuesta no es JSON de objeto.

    Devuelve:
        :class:`EvidenciaLectura` con la lectura normalizada y su trazabilidad.
    """
    if fuente not in FUENTES_LECTURA:
        raise ValueError(
            f"fuente inválida: {fuente!r}. Válidas: {FUENTES_LECTURA} (T-302)."
        )

    datos = _parsear_json(contenido)
    if not isinstance(datos, Mapping):
        raise ErrorEvidencia(
            "El JSON de evidencia debe ser un objeto (no una lista ni un "
            f"escalar); recibido: {type(datos).__name__} (T-302)."
        )

    problemas: list[str] = []
    faltantes: list[str] = []

    # --- 1. Letra de la lectura ------------------------------------------
    crudo_letra = datos.get(EVIDENCIA_CAMPO_LETRA)
    letra = _normalizar_letra_cruda(crudo_letra)
    if crudo_letra is None:
        faltantes.append(EVIDENCIA_CAMPO_LETRA)
    elif letra is None:
        problemas.append(
            MOTIVO_LETRA_NO_TEXTO.format(tipo=type(crudo_letra).__name__)
            if not isinstance(crudo_letra, str)
            else MOTIVO_LETRA_FUERA_VOCABULARIO.format(
                crudo=crudo_letra, vocabulario=sorted(LETRAS_COMPROBANTE)
            )
        )
        faltantes.append(EVIDENCIA_CAMPO_LETRA)

    # --- 2. Fragmento de sustento (ADR-001) -------------------------------
    crudo_fragmento = datos.get(CAMPO_EXPLICACION)
    fragmento = crudo_fragmento.strip() if isinstance(crudo_fragmento, str) else ""
    if not fragmento:
        faltantes.append(CAMPO_EXPLICACION)
        problemas.append(MOTIVO_SIN_SUSTENTO)

    # --- 2.b. Coherencia lectura↔sustento en la fuente de texto -----------
    # El motor lee la fuente de texto con la **regex de R5** sobre el texto
    # crudo del encabezado (``texto_encabezado_llm``), no con la letra suelta.
    # Si el fragmento no trae una expresión extraíble, la letra se perdería más
    # adelante sin explicación: se deja anotado acá (visibilidad, no decisión).
    if fuente == "llm" and letra is not None:
        letra_del_fragmento = letra_en_vocabulario(extraer_letra_encabezado(fragmento))
        if letra_del_fragmento is None:
            problemas.append(MOTIVO_FRAGMENTO_SIN_PATRON_R5.format(letra=letra))

    # --- 3. Candidatos ----------------------------------------------------
    # En descartados sí se excluye la letra leída (el documento la muestra, no
    # puede estar descartada); en restantes se conserva (sigue siendo viable).
    descartados = _normalizar_lista_letras(
        datos.get("candidatos_descartados"), excluir=letra
    )
    restantes = _normalizar_lista_letras(datos.get("candidatos_restantes"))
    if "candidatos_restantes" not in datos:
        faltantes.append("candidatos_restantes")

    # --- 5. Blindaje ADR-008: sin intersección descartados/restantes -------
    repetidos = [letra_c for letra_c in descartados if letra_c in restantes]
    if repetidos:
        descartados = [letra_c for letra_c in descartados if letra_c not in repetidos]
        problemas.append(
            f"El modelo listó {repetidos} a la vez como candidatos descartados y "
            "restantes; se conservan como restantes (conservador) y se quitan de "
            "descartados para no violar el blindaje ADR-008. La curaduría con "
            "reglas raw por fuente es T-303."
        )

    # --- 3/4. Campos desconocidos: lo que declaró el modelo + lo trazable ---
    # ``campos_desconocidos`` que reportó el modelo (p. ej. por un total
    # ilegible) + los campos del contrato que faltaron (letra, sustento y
    # candidatos restantes).
    declarados = _normalizar_campos(datos.get("campos_desconocidos"))

    campos_desconocidos: list[str] = []
    for campo in [*declarados, *faltantes]:
        if campo and campo not in campos_desconocidos:
            campos_desconocidos.append(campo)

    # --- Fuente declarada por el modelo (informativa) ---------------------
    fuente_declarada = datos.get("fuente_lectura")
    fuente_declarada = (
        fuente_declarada.strip().lower() if isinstance(fuente_declarada, str) else None
    )
    if fuente_declarada and fuente_declarada != fuente:
        problemas.append(
            MOTIVO_FUENTE_DECLARADA_DISTINTA.format(
                declarada=fuente_declarada, real=fuente
            )
        )

    return EvidenciaLectura(
        fuente=fuente,
        letra=letra,
        fragmento=fragmento,
        candidatos_descartados=descartados,
        candidatos_restantes=restantes,
        campos_desconocidos=campos_desconocidos,
        problemas=problemas,
        fuente_declarada=fuente_declarada,
        crudo=contenido,
    )


# ---------------------------------------------------------------------------
# Conversión a los contratos de F0 (ADR-001)
# ---------------------------------------------------------------------------


def construir_source_evidence(
    evidencia: EvidenciaLectura,
    *,
    modelo: str | None = None,
) -> SourceEvidence:
    """Convierte una :class:`EvidenciaLectura` en ``SourceEvidence`` (ADR-001).

    Emite un ``EvidenceField`` con ``campo`` = :data:`EVIDENCIA_CAMPO_LETRA`,
    ``valor`` = la letra leída (o ``None``), ``fuente`` = la fuente de la
    lectura, ``fragmento_sustento`` = el recuadro/texto que sustenta la letra y
    ``meta`` = :func:`~voucherflow.schemas.evidence.nueva_meta` con el modelo y
    ``version_prompt`` = ``tipo-comprobante@1`` (ADR-005).

    Los candidatos y los campos faltantes viajan en ``meta`` — no como campos
    nuevos de ``EvidenceField`` — porque el vocabulario per-campo lo fija F4
    con la tabla de precedencia (T-404/ADR-002); T-302 no debe anticiparlo.
    ``reglas_aplicadas`` queda vacío y ``valida`` toma
    :attr:`EvidenciaLectura.valida`: el veredicto de las reglas **raw** es de
    **T-303** (``rules/raw.py``) y se implementa una sola vez, para que F4/T-403
    lo reutilice (F3-subplan §3.3).

    ``confianza_fuente`` es la autoevaluación de la fuente (glosario §2.3) y se
    deriva de la higiene de la lectura: ``alta`` solo con letra **y** sustento y
    sin campos faltantes; ``baja`` sin letra; ``media`` en el resto. **No** es
    la certeza final de la decisión (esa la fija el motor de T-301).

    Lanza:
        ``ValueError`` si el fragmento de sustento queda vacío — ``ADR-001``
        exige sustento no vacío en ``EvidenceField``. Se usa un texto explícito
        que declara la ausencia en lugar de silenciarla.
    """
    fragmento = evidencia.fragmento.strip() or (
        f"Sin fragmento de sustento reportado por la fuente '{evidencia.fuente}' "
        "(T-302); la lectura no es auditable según ADR-001."
    )

    if evidencia.letra is None:
        confianza = "baja"
    elif evidencia.campos_desconocidos:
        confianza = "media"
    else:
        confianza = "alta"

    meta = nueva_meta(modelo=modelo, version_prompt=VERSION_PROMPT_TIPO_COMPROBANTE)
    meta.update(
        {
            "fuente_lectura": evidencia.fuente,
            "fuente_declarada": evidencia.fuente_declarada,
            "candidatos_descartados": list(evidencia.candidatos_descartados),
            "candidatos_restantes": list(evidencia.candidatos_restantes),
            "campos_desconocidos": list(evidencia.campos_desconocidos),
        }
    )

    campo = EvidenceField(
        campo=EVIDENCIA_CAMPO_LETRA,
        valor=evidencia.letra,
        fuente=_FUENTE_SCHEMA[evidencia.fuente],
        fragmento_sustento=fragmento,
        confianza_fuente=confianza,
        meta=meta,
    )
    return SourceEvidence(
        fuente=_FUENTE_SCHEMA[evidencia.fuente],
        campos={EVIDENCIA_CAMPO_LETRA: campo},
        valida=evidencia.valida,
        reglas_aplicadas=[],  # T-303 (rules/raw.py) las agrega una sola vez.
        debilidades=list(evidencia.problemas),
    )


def contexto_desde_evidencia(
    evidencia: EvidenciaLectura,
    base: ContextoTipoComprobante | None = None,
) -> ContextoTipoComprobante:
    """Puebla el contexto del motor con la lectura de **una** fuente (T-302).

    Cierra el puente con T-301: ``ContextoTipoComprobante.letra_recuadro_vlm``
    (R4) se llena con una lectura de fuente ``vlm``;
    ``texto_encabezado_llm`` (R5) con una de fuente ``llm``. Las señales de
    desglose de IVA y campos totales (R6) **no** las aporta esta tarea: son
    datos estructurados del documento, no una lectura de letra (los llenará el
    productor correspondiente, p. ej. la extracción de F4).

    Cuando la lectura no aportó letra, el campo del contexto queda en ``None``
    (o vacío para el texto) y el campo se registra en ``campos_ausentes``: el
    motor trata lo ausente como desconocido y **no dispara** la regla (prompt
    WIP: "no inventes datos").

    Nota sobre la fuente de texto: R5 no lee una letra suelta sino que aplica la
    **regex de R5** sobre ``texto_encabezado_llm``. Por eso este campo se puebla
    con el **fragmento de sustento literal** (el texto OCR que el modelo reportó
    como prueba de la letra) y no con la letra: así el motor ve exactamente el
    texto que la fuente citó. Si el fragmento no contiene una expresión
    ``FACTURA <letra>``/``COMPROBANTE <letra>``, R5 no confirmará la letra — la
    situación queda anotada en ``EvidenciaLectura.problemas``
    (:data:`MOTIVO_FRAGMENTO_SIN_PATRON_R5`), no silenciada.

    Argumentos:
        evidencia: la lectura normalizada.
        base: contexto a completar (p. ej. el que ya trae las condiciones
            fiscales del negocio). Si es ``None`` se parte de un contexto vacío.
            Se usa :meth:`ContextoTipoComprobante.reemplazar`, que **no** muta el
            original (el contexto es ``frozen``).

    Devuelve:
        Un :class:`ContextoTipoComprobante` nuevo con la lectura aplicada.
    """
    contexto = base or ContextoTipoComprobante()
    ausentes = list(contexto.campos_ausentes)

    cambios: dict[str, Any] = {}
    if evidencia.fuente == "vlm":
        cambios["letra_recuadro_vlm"] = evidencia.letra
        if evidencia.letra is None:
            ausentes.append("letra_recuadro_vlm")
    else:
        cambios["texto_encabezado_llm"] = evidencia.fragmento
        if evidencia.letra is None:
            ausentes.append("texto_encabezado_llm")

    if ausentes != list(contexto.campos_ausentes):
        cambios["campos_ausentes"] = ausentes

    return contexto.reemplazar(**cambios)


# ---------------------------------------------------------------------------
# Orquestación de las fuentes (T-302)
# ---------------------------------------------------------------------------


@dataclass
class LecturaTipoComprobante:
    """Resultado de leer la evidencia de tipo/letra (T-302).

    Agrupa las evidencias de **todas** las fuentes corridas (sin colapsarlas:
    ADR-002 resuelve el desacuerdo por campo en F4) y el contexto de lectura ya
    listo para que T-301 decida.

    Campos:
        lecturas: las :class:`EvidenciaLectura` normalizadas, en orden de
            corrida (``vlm`` antes que ``llm``: el recuadro tiene prioridad,
            R4 → R5 en cascada).
        evidencias: los ``SourceEvidence`` (ADR-001) correspondientes, para
            ``CombinedEvidence``/auditoría.
        contexto: :class:`ContextoTipoComprobante` con la lectura aplicada, listo
            para :func:`~voucherflow.classification.clasificar_tipo_comprobante`.
        documento_id: id del documento (hash sha256 en producción; parámetro acá
            porque T-302 no lee el archivo — el lector es inyectado).
        detalle: trazabilidad de la corrida (fuentes pedidas/saltadas, modelo y
            ``num_ctx`` usados por fuente, versión del prompt).
    """

    lecturas: list[EvidenciaLectura] = field(default_factory=list)
    evidencias: list[SourceEvidence] = field(default_factory=list)
    contexto: ContextoTipoComprobante | None = None
    documento_id: str = ""
    detalle: dict[str, Any] = field(default_factory=dict)

    @property
    def letra_por_recuadro(self) -> str | None:
        """Letra leída de la imagen (R4), si la fuente ``vlm`` la aportó."""
        for lectura in self.lecturas:
            if lectura.fuente == "vlm" and lectura.letra is not None:
                return lectura.letra
        return None

    @property
    def letra_por_texto(self) -> str | None:
        """Letra leída del texto OCR (R5), si la fuente ``llm`` la aportó."""
        for lectura in self.lecturas:
            if lectura.fuente == "llm" and lectura.letra is not None:
                return lectura.letra
        return None


def _fuentes_con_insumo(
    fuentes: Iterable[str], *, markdown: str | None, vista: Any
) -> tuple[list[str], list[str]]:
    """Separa las fuentes pedidas entre las que tienen insumo y las que no (T-302).

    Evita llamar al modelo sin material: la fuente ``llm`` necesita markdown y
    la ``vlm`` una vista con ``ruta_imagen_original``. Las que no tienen insumo
    se informan en el ``detalle`` (no se silencian: la ausencia de una fuente es
    una debilidad de la lectura, no un detalle cosmético).
    """
    con_insumo: list[str] = []
    sin_insumo: list[str] = []
    for fuente in fuentes:
        if fuente not in FUENTES_LECTURA:
            raise ValueError(
                f"fuente inválida: {fuente!r}. Válidas: {FUENTES_LECTURA} (T-302)."
            )
        tiene = (
            (markdown or "").strip()
            if fuente == "llm"
            else getattr(vista, "ruta_imagen_original", None)
        )
        (con_insumo if tiene else sin_insumo).append(fuente)
    return con_insumo, sin_insumo


def _resolver_modelo(
    fuente: str, modelo: str | None, settings: Settings
) -> tuple[str, int | None]:
    """Resuelve modelo y ``num_ctx`` de la fuente desde ``Settings`` (T-302).

    La lectura visual usa el rol ``vlm`` (``qwen2.5vl:3b``) y la textual el rol
    ``llm`` (``qwen2.5:7b``), con el ``num_ctx`` declarado para cada rol
    (E-LIB-3). Si el llamador fija ``modelo``, se usa tal cual (sin ``num_ctx``:
    el llamador es responsable), igual que en F2/T-202.
    """
    if modelo:
        return modelo, None
    rol = settings.modelo_para(fuente)
    if rol is None or not rol.modelo:
        raise ValueError(
            f"No hay modelo configurado para la fuente {fuente!r} "
            "(rol 'vlm'/'llm' de Settings, E-LIB-3); T-302 necesita un modelo "
            "para construir la llamada."
        )
    return rol.modelo, rol.num_ctx


def leer_evidencia(
    lector: Lector,
    *,
    markdown: str | None = None,
    vista: Any = None,
    documento_id: str = "documento",
    fuentes: Iterable[str] = FUENTES_LECTURA,
    modelo: str | None = None,
    settings: Settings | None = None,
    contexto_base: ContextoTipoComprobante | None = None,
) -> LecturaTipoComprobante:
    """Lee la evidencia de tipo/letra de las fuentes disponibles (T-302).

    Corre el prompt de evidencia (``tipo-comprobante@1``) sobre cada fuente con
    insumo y devuelve las lecturas normalizadas + el contexto listo para el
    motor de T-301. **No decide ninguna letra**: la decisión es de T-301.

    Contrato de inyección (F3-subplan §2.6): los flujos VLM/LLM reales
    (``extraction/flows.py``) son de F4/T-401, así que el ``lector`` es un
    parámetro. En la suite default se pasa un doble
    (``FakeOllamaClient``, como en F2) y en F4 se pasa el
    ``OllamaClient`` real — o un adaptador que envuelva los flujos — sin
    cambiar esta función.

    Argumentos:
        lector: objeto con ``ask`` (protocolo :class:`Lector`).
        markdown: markdown/OCR de F1 para la fuente ``llm``.
        vista: :class:`~voucherflow.validation.vistas.VistaPreparada` de F2 para
            la fuente ``vlm`` (se usa su ``ruta_imagen_original``).
        documento_id: id del documento (hash sha256 en producción; T-302 no lee
            el archivo, así que se recibe).
        fuentes: fuentes a correr, en orden. Default las dos (``vlm``, ``llm``):
            correr ambas y conservar las dos evidencias es lo que pide ADR-002.
        modelo: modelo explícito para **todas** las fuentes (sin ``num_ctx``).
        settings: ``Settings`` para resolver modelo/``num_ctx`` por rol
            (default: ``cargar_settings()``).
        contexto_base: contexto con la parte de negocio (condiciones fiscales)
            que deba viajar junto a la lectura. La lectura se **aplica encima**
            (no se muta: el contexto es ``frozen``).

    Devuelve:
        :class:`LecturaTipoComprobante` con las lecturas, los ``SourceEvidence``
        (ADR-001) y el ``contexto`` poblado. Si ninguna fuente tiene insumo, el
        resultado trae las listas vacías y el ``detalle`` explica por qué — no
        es un error (un documento sin material de lectura es un caso válido).

    Lanza:
        ``ValueError`` si una fuente pedida no es válida o si no hay modelo
        configurable para una fuente con insumo.
        ``ErrorEvidencia`` si la respuesta de una fuente no es JSON de objeto.
        ``OllamaError`` (del cliente real) **se propaga**: una falla de
        comunicación no debe confundirse con "no se pudo leer la letra".
    """
    settings_usado = settings or cargar_settings()
    pedidas = list(fuentes)
    con_insumo, sin_insumo = _fuentes_con_insumo(pedidas, markdown=markdown, vista=vista)

    lecturas: list[EvidenciaLectura] = []
    evidencias: list[SourceEvidence] = []
    modelos: dict[str, dict[str, Any]] = {}

    for fuente in con_insumo:
        modelo_usado, num_ctx = _resolver_modelo(fuente, modelo, settings_usado)
        messages = construir_messages_tipo_comprobante(
            fuente=fuente, markdown=markdown, vista=vista
        )
        respuesta = lector.ask(
            messages,
            model=modelo_usado,
            json_format=True,
            num_ctx=num_ctx,
        )
        evidencia = parsear_evidencia_lectura(respuesta.contenido, fuente=fuente)
        lecturas.append(evidencia)
        evidencias.append(construir_source_evidence(evidencia, modelo=modelo_usado))
        modelos[fuente] = {"modelo": modelo_usado, "num_ctx": num_ctx}

    contexto = contexto_base
    for evidencia in lecturas:
        contexto = contexto_desde_evidencia(evidencia, contexto)

    detalle: dict[str, Any] = {
        "version_prompt": VERSION_PROMPT_TIPO_COMPROBANTE,
        "fuentes_pedidas": pedidas,
        "fuentes_corridas": con_insumo,
        "fuentes_sin_insumo": sin_insumo,
        "modelos": modelos,
        "nota": (
            "Lectura de evidencia (T-302): el modelo no decide la letra; la "
            "decide el motor de reglas R1-R7 (T-301)."
        ),
    }
    return LecturaTipoComprobante(
        lecturas=lecturas,
        evidencias=evidencias,
        contexto=contexto,
        documento_id=documento_id,
        detalle=detalle,
    )


__all__ = [
    "EVIDENCIA_CAMPO_LETRA",
    "CAMPO_EXPLICACION",
    "CAMPOS_TRAZABLES",
    "MOTIVO_FUENTE_DECLARADA_DISTINTA",
    "MOTIVO_LETRA_FUERA_VOCABULARIO",
    "MOTIVO_LETRA_NO_TEXTO",
    "MOTIVO_FRAGMENTO_SIN_PATRON_R5",
    "MOTIVO_SIN_SUSTENTO",
    "ErrorEvidencia",
    "Lector",
    "EvidenciaLectura",
    "LecturaTipoComprobante",
    "parsear_evidencia_lectura",
    "construir_source_evidence",
    "contexto_desde_evidencia",
    "leer_evidencia",
]

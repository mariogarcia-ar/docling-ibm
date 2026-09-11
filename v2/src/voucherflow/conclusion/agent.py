"""Agente de conclusión — escalado y blindaje post-agente (F5 / T-504, E-CONC-3).

**Fase**: F5 — Conclusión. **Tarea**: T-504 · **Épica**: E-CONC-3 · **ADR-008**.

El paso del pseudocódigo de `algoritmo.md`:

    si resultado.concluye: consolidar(certeza="alta", origen="programa") …
    decision_agente = agente_ia_decide(
        evidencia=evidencia,
        candidatos_restantes=resultado.candidatos_restantes,   # nunca los ya descartados
        reglas_que_fallaron=resultado.conflictos,
    )
    consolidar(decision_agente, certeza="baja", origen="agente_ia")

Es decir: **solo cuando el código no concluyó**, y **solo entre los candidatos
que sobrevivieron al descarte**.

El blindaje, en capas
---------------------
La regla dura de E-CONC-3 es que el agente **no puede resucitar un candidato
descartado**. Confiar en el prompt sería confiar en que un modelo obedezca: acá
se **verifica**. Tres capas, de la más débil a la más fuerte:

1. **El prompt** (:mod:`voucherflow.conclusion.prompt_agente`) declara el
   universo cerrado y **no** incluye los descartados: lo que el agente no ve, no
   puede elegir.
2. **El orquestador** (este módulo) valida la salida contra
   ``candidatos_restantes``. Una elección fuera del universo **se descarta** (no
   se "corrige" a un valor parecido: se rechaza y queda registrada la anomalía).
3. **El contrato** (``schemas/evidence.py``) rechaza que un valor figure a la vez
   como descartado y restante.

Decisiones de diseño (T-504)
----------------------------
1. **El agente decide, pero no declara la certeza.** La certeza se deriva de la
   etapa (glosario §2): que decida un agente implica ``certeza=baja`` y origen
   ``agente_ia``, con revisión humana obligatoria. El agente que devolviera
   "certeza: alta" no cambia nada — el sistema no lo escucha.
2. **Abstenerse es una salida válida.** ``candidato: null`` ("no puedo elegir con
   fundamento") es preferible a una elección forzada: el caso va a revisión igual,
   pero sin una decisión inventada que auditar.
3. **Una elección inválida no se "arregla", se rechaza.** Si el agente elige un
   descartado, el resultado es **abstención con anomalía registrada** — no un
   fallback al primer candidato restante. Corregir en silencio escondería que el
   agente desobedeció, que es exactamente lo que hay que poder auditar.
4. **El agente es inyectable** (:class:`Agente`), como el ``Lector`` de F4 y el
   ``BuscadorEvidencia`` de T-502: la suite default corre **sin Ollama** y la
   integración real es el mismo protocolo con :class:`~voucherflow.models.ollama.OllamaClient`.
5. **No decide si el código ya concluyó.** Escalar un caso resuelto gastaría una
   llamada al modelo y podría introducir una decisión peor que la del código
   (ADR-008: el agente es para lo que el código **no** pudo).
6. **``% agente`` se puede medir** (mitiga R-09): el resultado publica si el caso
   se escaló, así que T-507 puede reportar la tasa sin recalcular nada.

Referencias: ADR-008, Gherkin E-CONC-3, doc 03 §4.5, `CONC.md` §1/§3,
`algoritmo.md` §7, F5-subplan §3.4.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from typing import Any, Mapping, Protocol, Sequence, runtime_checkable

from ..rules.cruzadas import ConclusionResult
from ..schemas.result import Certeza, HitlDecision, Origen
from ..settings.config import Settings, cargar_settings
from .prompt_agente import (
    CLAVE_CANDIDATO,
    CLAVE_JUSTIFICACION,
    CLAVE_SUSTENTO,
    ROL_AGENTE,
    VERSION_PROMPT_AGENTE,
    construir_messages_agente,
)

#: Versión de la orquestación del agente (se registra en la trazabilidad).
VERSION_AGENTE = "conclusion-agente-orq@1"

#: Cómo terminó el escalado.
AGENTE_ELIGIO = "eligio"
AGENTE_SE_ABSTUVO = "se_abstuvo"
AGENTE_ELECCION_INVALIDA = "eleccion_invalida"
AGENTE_FALLO = "fallo"
AGENTE_NO_ESCALADO = "no_escalado"

#: Mensajes canónicos (van a la traza; en español y citando el Gherkin).
MOTIVO_NO_ESCALADO = (
    "El código concluyó el caso: no se escala al agente (ADR-008 / Gherkin "
    "E-CONC-3: el escalado es solo cuando el código NO concluye)."
)
MOTIVO_SIN_CANDIDATOS = (
    "No hay candidatos restantes que ofrecerle al agente: sin universo cerrado "
    "no hay decisión que validar, así que no se escala."
)
MOTIVO_ELECCION_INVALIDA = (
    "El agente eligió «{candidato}», que NO está entre los candidatos restantes "
    "({universo}) y sí figura como descartado. Se rechaza la elección (no se "
    "corrige a un valor parecido): el agente no puede resucitar un descartado "
    "(blindaje post-agente, ADR-008). El caso queda en revisión humana."
)
MOTIVO_ABSTENCION = (
    "El agente se abstuvo: con la evidencia disponible no puede elegir con "
    "fundamento. Es una salida válida (mejor que una decisión inventada): el "
    "caso queda en revisión humana."
)
MOTIVO_ELIGIO = (
    "El agente eligió «{candidato}» dentro del universo de candidatos restantes. "
    "La certeza es baja y el origen agente_ia (glosario §2: la certeza se deriva "
    "de la etapa que decidió), así que el caso va a revisión humana (Gherkin "
    "E-CONC-3)."
)
MOTIVO_FALLO = (
    "El agente no pudo producir una decisión utilizable ({error}). El caso no se "
    "queda sin salida: queda en revisión humana con la anomalía registrada."
)


# ---------------------------------------------------------------------------
# El protocolo del agente
# ---------------------------------------------------------------------------


@runtime_checkable
class Agente(Protocol):
    """Protocolo del agente de decisión: un objeto con ``decidir``.

    Mismo criterio que el ``Lector`` de F4 y el ``BuscadorEvidencia`` de T-502:
    inyectable, para que la suite default corra **sin Ollama** y la integración
    real sea un adaptador con el mismo contrato. Recibe los ``messages`` ya
    construidos (versión del prompt incluida) y el modelo a usar, y devuelve algo
    con ``.contenido`` (str) — es lo único que se consume.
    """

    def decidir(
        self,
        messages: list[dict[str, str]],
        modelo: str,
        *,
        num_ctx: int | None = None,
    ) -> Any:  # pragma: no cover - contrato estructural
        ...


# ---------------------------------------------------------------------------
# El resultado del escalado
# ---------------------------------------------------------------------------


@dataclass
class EleccionAgente:
    """Lo que el agente devolvió, **antes** del blindaje (T-504).

    Campos:
        candidato: el valor elegido (``None`` si se abstuvo o si la salida no se
            pudo interpretar).
        justificacion: el porqué declarado.
        sustento: el fragmento en el que se apoyó (ADR-001).
        crudo: la respuesta textual del agente (auditoría/diagnóstico).
        valida: si la salida se pudo interpretar como el contrato del prompt.
    """

    candidato: str | None = None
    justificacion: str = ""
    sustento: str = ""
    crudo: str = ""
    valida: bool = False

    def como_dict(self) -> dict[str, Any]:
        return {
            "candidato": self.candidato,
            "justificacion": self.justificacion,
            "sustento": self.sustento,
            "valida": self.valida,
        }


@dataclass
class DecisionAgente:
    """Resultado del escalado al agente, con su blindaje (T-504 / E-CONC-3).

    Campos:
        escalado: ``True`` si se llamó al agente (el código no concluyó y había
            universo). Es la señal para medir ``% agente`` (R-09).
        desenlace: uno de :data:`AGENTE_ELIGIO`, :data:`AGENTE_SE_ABSTUVO`,
            :data:`AGENTE_ELECCION_INVALIDA`, :data:`AGENTE_FALLO` o
            :data:`AGENTE_NO_ESCALADO`.
        candidato: el candidato **aceptado** (``None`` si no hubo elección
            válida). Es el único valor que el resto del sistema debe consumir.
        eleccion: la salida cruda del agente (para auditar qué declaró).
        certeza / origen: derivados de la etapa (glosario §2). ``certeza`` es
            **siempre** ``baja`` cuando se escaló (un caso que necesitó al agente
            no es de certeza alta, decida o no). ``origen`` es ``agente_ia``
            **solo si la elección sobrevivió al blindaje**: si el agente se
            abstuvo, falló o propuso un descartado, no decidió nadie y el origen
            queda en ``None`` (lo resolverá la revisión humana, T-505).
        hitl: la expectativa de revisión (siempre prioridad ``alta``: el Gherkin
            pide que un caso resuelto por agente vaya a revisión humana).
        bloques: los candidatos descartados que el agente intentó elegir (la
            anomalía del blindaje; vacío si respetó el universo).
        motivo: explicación legible del desenlace.
        version_prompt / modelo: trazabilidad de la llamada (ADR-005).
    """

    escalado: bool
    desenlace: str
    candidato: str | None = None
    eleccion: EleccionAgente | None = None
    certeza: Certeza | None = None
    origen: Origen | None = None
    hitl: HitlDecision | None = None
    bloques: list[str] = field(default_factory=list)
    motivo: str = ""
    version_prompt: str = VERSION_PROMPT_AGENTE
    modelo: str | None = None

    @property
    def hubo_blindaje(self) -> bool:
        """True si el blindaje tuvo que rechazar la elección del agente."""
        return bool(self.bloques) or self.desenlace == AGENTE_ELECCION_INVALIDA

    @property
    def eligio(self) -> bool:
        """True si el agente eligió y su elección sobrevivió al blindaje."""
        return self.candidato is not None and self.desenlace == AGENTE_ELIGIO

    def como_dict(self) -> dict[str, Any]:
        return {
            "version": VERSION_AGENTE,
            "escalado": self.escalado,
            "desenlace": self.desenlace,
            "candidato": self.candidato,
            "certeza": self.certeza.value if self.certeza else None,
            "origen": self.origen.value if self.origen else None,
            "bloques": list(self.bloques),
            "hubo_blindaje": self.hubo_blindaje,
            "motivo": self.motivo,
            "version_prompt": self.version_prompt,
            "modelo": self.modelo,
            "eleccion": self.eleccion.como_dict() if self.eleccion else None,
            "hitl": (
                {
                    "requerido": self.hitl.requerido,
                    "prioridad": self.hitl.prioridad,
                    "estado": self.hitl.estado,
                }
                if self.hitl
                else None
            ),
        }


# ---------------------------------------------------------------------------
# El agente real (Ollama, ADR-008 alternativa (a))
# ---------------------------------------------------------------------------


class AgenteOllama:
    """Agente = llamada a Ollama con prompt estructurado (ADR-008, sin framework).

    Envuelve al ``OllamaClient`` en el contrato del :class:`Agente`: arma la
    llamada (``json_format=True`` para forzar el JSON del contrato, la ventana
    ``num_ctx`` del rol ``agente``) y devuelve la respuesta tal cual — la
    interpretación y el **blindaje** son de :func:`escalar_a_agente`, no de acá.
    """

    def __init__(self, cliente: Any) -> None:
        self.cliente = cliente

    def decidir(
        self,
        messages: list[dict[str, str]],
        modelo: str,
        *,
        num_ctx: int | None = None,
    ) -> Any:
        """Delega en ``cliente.ask`` con el contrato de decisión del agente."""
        return self.cliente.ask(
            messages,
            model=modelo,
            json_format=True,
            num_ctx=num_ctx,
        )


# ---------------------------------------------------------------------------
# El escalado con blindaje
# ---------------------------------------------------------------------------


def escalar_a_agente(
    conclusion: ConclusionResult,
    *,
    evidencia: Mapping[str, Any] | None = None,
    agente: Agente | None = None,
    modelo: str | None = None,
    settings: Settings | None = None,
    descripcion: str | None = None,
) -> DecisionAgente:
    """Escala el caso al agente **solo si el código no concluyó** (F5/T-504).

    Secuencia (Gherkin E-CONC-3):

    1. **¿Corresponde escalar?** Si el código concluyó, **no** se escala
       (``no_escalado``): ADR-008 y el propio Gherkin dicen que el agente es para
       lo que el código no pudo. Tampoco se escala si no hay candidatos
       restantes: sin universo cerrado no hay nada que elegir ni que validar.
    2. **Llamada**: se arman los ``messages`` (evidencia + reglas que fallaron +
       candidatos restantes, **sin** los descartados) y se le pide al agente.
    3. **Interpretación**: se parsea el JSON del contrato. Una salida ilegible es
       ``fallo`` (no una decisión inventada).
    4. **Blindaje**: si eligió un valor que **no** está en ``candidatos_restantes``
       la elección se **rechaza** (``eleccion_invalida``) y queda registrada la
       anomalía. Si eligió ``null``, fue abstención. Si eligió dentro del
       universo, se acepta.

    La **certeza y el origen se derivan de la etapa** (glosario §2): apenas se
    escala, el caso es ``agente_ia`` + ``baja`` con revisión humana de prioridad
    ``alta`` — incluso si el agente se abstuvo o falló, porque en todos los casos
    lo resolvió un agente (o quedó para que lo resuelva un humano).

    Determinística y **sin red** con un `agente` doble o ``None``.

    Argumentos:
        conclusion: el veredicto de la pasada 2 (T-501). De ahí salen los
            candidatos, las reglas que fallaron, las alertas y los gaps.
        evidencia: ``campo -> valor`` vigente (la resolución de T-404). Default:
            vacío (el prompt lo declara).
        agente: el agente inyectable. ``None`` (hook desactivado) se trata como
            no disponible: el caso queda en revisión igual.
        modelo / settings: de dónde sale el modelo del rol ``agente``.
        descripcion: la descripción del documento, como contexto opcional.

    Devuelve:
        :class:`DecisionAgente` con el desenlace, el candidato aceptado (si lo
        hubo) y la traza del blindaje.
    """
    # --- 1. ¿Corresponde escalar? ---
    if conclusion.concluye:
        return DecisionAgente(
            escalado=False,
            desenlace=AGENTE_NO_ESCALADO,
            motivo=MOTIVO_NO_ESCALADO,
        )

    candidatos = list(conclusion.candidatos_restantes)
    if not candidatos:
        return DecisionAgente(
            escalado=False,
            desenlace=AGENTE_NO_ESCALADO,
            motivo=MOTIVO_SIN_CANDIDATOS,
        )

    # --- 2. La llamada ---
    messages = construir_messages_agente(
        evidencia=evidencia,
        candidatos_restantes=candidatos,
        reglas_que_fallaron=conclusion.reglas_aplicadas,
        alertas=conclusion.alertas,
        campos_faltantes=conclusion.faltan_datos,
        descripcion=descripcion,
    )
    modelo_usado, num_ctx = _resolver_modelo(modelo, settings)

    if agente is None:
        return _sin_agente(candidatos, modelo_usado)

    try:
        respuesta = agente.decidir(messages, modelo_usado, num_ctx=num_ctx)
        contenido = str(getattr(respuesta, "contenido", respuesta))
    except Exception as exc:  # el agente es opcional: no tumba el pipeline
        return _fallo(candidatos, modelo_usado, f"el agente no respondió: {exc}")

    # --- 3. Interpretación ---
    eleccion = parsear_eleccion_agente(contenido)
    if not eleccion.valida:
        return _fallo(candidatos, modelo_usado, "la salida no es el JSON del contrato", eleccion=eleccion)

    # --- 4. Blindaje post-agente ---
    return _blindar(eleccion, candidatos, conclusion, modelo_usado)


def parsear_eleccion_agente(contenido: str) -> EleccionAgente:
    """Interpreta la salida del agente contra el contrato del prompt (T-504).

    Acepta el JSON del contrato, tolerando el ruido habitual de un modelo (cercas
    de código, prosa alrededor): se busca el **primer objeto JSON** con la clave
    del candidato. Una salida sin JSON, o con el JSON mal formado, devuelve
    ``valida=False`` — el llamador la trata como fallo, **nunca** como una
    decisión inventada.

    ``candidato: null`` es **válido** (abstención explícita) y se distingue de
    "no pude interpretar" por :attr:`EleccionAgente.valida`.
    """
    crudo = contenido or ""
    objeto = _primer_json(crudo)
    if objeto is None:
        return EleccionAgente(crudo=crudo, valida=False)

    if CLAVE_CANDIDATO not in objeto:
        return EleccionAgente(crudo=crudo, valida=False)

    valor = objeto.get(CLAVE_CANDIDATO)
    candidato = None
    if valor is not None:
        texto = str(valor).strip()
        # Un modelo puede devolver la lista entera o un valor vacío; nada de eso
        # es una elección.
        candidato = texto or None

    return EleccionAgente(
        candidato=candidato,
        justificacion=str(objeto.get(CLAVE_JUSTIFICACION, "") or ""),
        sustento=str(objeto.get(CLAVE_SUSTENTO, "") or ""),
        crudo=crudo,
        valida=True,
    )


# ---------------------------------------------------------------------------
# Desenlaces
# ---------------------------------------------------------------------------


def _blindar(
    eleccion: EleccionAgente,
    candidatos: list[str],
    conclusion: ConclusionResult,
    modelo: str | None,
) -> DecisionAgente:
    """Valida la elección del agente contra el universo cerrado (E-CONC-3).

    Es el corazón del blindaje post-agente: la elección **no** se acepta por
    venir de un modelo. Si el valor elegido no está entre los candidatos
    restantes, se rechaza — y **no** se sustituye por otro (corregir en silencio
    escondería la desobediencia, que es justo lo que hay que poder auditar).
    """
    if eleccion.candidato is None:
        return DecisionAgente(
            escalado=True,
            desenlace=AGENTE_SE_ABSTUVO,
            eleccion=eleccion,
            certeza=Certeza.baja,
            # Se abstuvo: no decidió nadie. El origen queda en ``None`` para que
            # ningún consumidor lea "lo decidió el agente" cuando el agente
            # justamente dijo que no podía.
            origen=None,
            hitl=_hitl_revision(),
            motivo=MOTIVO_ABSTENCION,
            modelo=modelo,
        )

    if eleccion.candidato not in candidatos:
        descartado = eleccion.candidato in conclusion.candidatos_descartados
        universo = ", ".join(candidatos)
        return DecisionAgente(
            escalado=True,
            desenlace=AGENTE_ELECCION_INVALIDA,
            candidato=None,
            eleccion=eleccion,
            certeza=Certeza.baja,
            origen=None,
            hitl=_hitl_revision(),
            bloques=[eleccion.candidato],
            motivo=MOTIVO_ELECCION_INVALIDA.format(
                candidato=eleccion.candidato, universo=universo
            )
            + (
                ""
                if descartado
                else " (El valor tampoco estaba entre los restantes: el agente "
                "propuso algo fuera del caso.)"
            ),
            modelo=modelo,
        )

    return DecisionAgente(
        escalado=True,
        desenlace=AGENTE_ELIGIO,
        candidato=eleccion.candidato,
        eleccion=eleccion,
        certeza=Certeza.baja,
        origen=Origen.agente_ia,
        hitl=_hitl_revision(),
        motivo=MOTIVO_ELIGIO.format(candidato=eleccion.candidato),
        modelo=modelo,
    )


def _sin_agente(candidatos: list[str], modelo: str | None) -> DecisionAgente:
    """El hook del agente está desactivado (ADR-008: no bloquea el MVP).

    No hay decisión automática: el caso queda para la revisión humana, con el
    origen sin asignar (no lo decidió nadie).
    """
    return DecisionAgente(
        escalado=True,
        desenlace=AGENTE_FALLO,
        certeza=Certeza.baja,
        origen=None,
        hitl=_hitl_revision(),
        motivo=MOTIVO_FALLO.format(
            error=(
                "no hay agente configurado; el caso queda en revisión humana sin "
                "decisión automática"
            )
        ),
        modelo=modelo,
    )


def _fallo(
    candidatos: list[str],
    modelo: str | None,
    error: str,
    *,
    eleccion: EleccionAgente | None = None,
) -> DecisionAgente:
    """El agente no produjo una decisión utilizable: el caso va a revisión igual."""
    return DecisionAgente(
        escalado=True,
        desenlace=AGENTE_FALLO,
        eleccion=eleccion,
        certeza=Certeza.baja,
        origen=None,
        hitl=_hitl_revision(),
        motivo=MOTIVO_FALLO.format(error=error),
        modelo=modelo,
    )


def _hitl_revision() -> HitlDecision:
    """Expectativa de revisión humana: un caso del agente va con prioridad alta."""
    return HitlDecision(requerido=True, prioridad="alta", estado="pendiente")


def _resolver_modelo(
    modelo: str | None, settings: Settings | None
) -> tuple[str, int | None]:
    """Resuelve modelo y ``num_ctx`` del rol ``agente`` desde ``Settings``.

    Mismo criterio que T-302 (``_resolver_modelo`` de ``classification.evidencia``):
    si el llamador fija el modelo, se usa tal cual; si no, se busca el rol
    ``agente`` de ``Settings`` (E-LIB-3).
    """
    if modelo:
        return modelo, None

    config = settings if settings is not None else cargar_settings()
    rol = config.modelo_para(ROL_AGENTE)
    if rol is not None and rol.modelo:
        return rol.modelo, rol.num_ctx

    # Sin rol configurado se usa el del LLM: el agente es una tarea de texto y no
    # se puede inventar un modelo (el llamador debería pasar uno).
    rol_llm = config.modelo_para("llm")
    if rol_llm is not None and rol_llm.modelo:
        return rol_llm.modelo, rol_llm.num_ctx

    raise ValueError(
        "No hay modelo configurado para el rol 'agente' ni 'llm' en Settings "
        "(E-LIB-3): T-504 necesita un modelo para escalar el caso."
    )


def _primer_json(texto: str) -> dict[str, Any] | None:
    """Extrae el primer objeto JSON de la respuesta del agente.

    Tolerante al ruido del modelo (cercas ```` ```json ````, prosa alrededor):
    recorre los ``{`` y devuelve el primer objeto que cierre y parsee. Devuelve
    ``None`` si no hay ninguno (la salida no es utilizable).
    """
    limpio = texto.strip()
    if limpio.startswith("```"):
        # Quitar las cercas de código conservando el contenido.
        limpio = re.sub(r"^```[a-zA-Z]*\s*", "", limpio)
        limpio = re.sub(r"\s*```$", "", limpio)

    for inicio in (i for i, c in enumerate(limpio) if c == "{"):
        profundidad = 0
        for fin in range(inicio, len(limpio)):
            if limpio[fin] == "{":
                profundidad += 1
            elif limpio[fin] == "}":
                profundidad -= 1
                if profundidad == 0:
                    candidato = limpio[inicio : fin + 1]
                    try:
                        objeto = json.loads(candidato)
                    except json.JSONDecodeError:
                        break
                    return objeto if isinstance(objeto, dict) else None
    return None


__all__ = [
    "VERSION_AGENTE",
    "AGENTE_ELIGIO",
    "AGENTE_SE_ABSTUVO",
    "AGENTE_ELECCION_INVALIDA",
    "AGENTE_FALLO",
    "AGENTE_NO_ESCALADO",
    "MOTIVO_NO_ESCALADO",
    "MOTIVO_SIN_CANDIDATOS",
    "MOTIVO_ELECCION_INVALIDA",
    "MOTIVO_ABSTENCION",
    "MOTIVO_ELIGIO",
    "MOTIVO_FALLO",
    "Agente",
    "AgenteOllama",
    "EleccionAgente",
    "DecisionAgente",
    "escalar_a_agente",
    "parsear_eleccion_agente",
]

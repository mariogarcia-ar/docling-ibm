"""Prompt del **agente de conclusión**, versionado (F5 / T-504, ADR-008).

**Fase**: F5 (conclusión) · **Tarea**: T-504 · **Épica**: E-CONC-3.

Qué es este agente (y qué no)
-----------------------------
ADR-008 adopta la alternativa **(a)**: el agente es una **llamada a Ollama con
prompt de decisión estructurado**, sin framework. No es un agente autónomo: es
una etapa *orquestada* que recibe la evidencia consolidada y un **universo de
candidatos ya filtrado** por el código, elige uno (o se abstiene) y justifica.
Su contrato de entrada/salida es estable para que, en el futuro, se pueda
reemplazar la implementación por un agente más sofisticado sin tocar el resto.

La regla dura (E-CONC-3)
------------------------
El agente **no puede resucitar un candidato descartado**. Los descartes los hizo
el código con certeza (la coherencia de la letra, el conflicto R7, la pasada 1 por
fuente) y el agente no los revisa: elige **entre los que sobrevivieron**. Por eso
este prompt recibe ``candidatos_restantes`` y **no** los descartados — lo que el
agente no ve, no puede elegir.

Tres capas de blindaje (defensa en profundidad)
-----------------------------------------------
El prompt pide respetar el universo, pero **pedir no es garantizar** (un modelo
puede devolver otra cosa). El blindaje está en tres lugares:

1. **El prompt** (acá): el universo se declara explícitamente y se pide la
   elección dentro de él, con la abstención como salida válida.
2. **El orquestador** (:mod:`voucherflow.conclusion.agent`): valida la elección
   contra ``candidatos_restantes`` y la rechaza si salió del universo.
3. **El contrato** (``schemas/evidence.py``): ``Decision`` rechaza que un valor
   figure a la vez como descartado y restante.

Qué se le pide y qué no (ADR-001)
---------------------------------
El agente **decide** (es su trabajo: es la etapa de decisión), pero **no
normaliza** ni inventa evidencia: devuelve el candidato elegido, una
**justificación** y el **fragmento** en el que se apoyó. La certeza **no** la
declara el agente: se deriva de la etapa (glosario §2 — el agente siempre
implica ``certeza=baja`` y origen ``agente_ia``, con revisión humana).

Contrato de salida
------------------
El prompt pide un objeto JSON::

    {"candidato": "<uno de los candidatos_restantes>" | null,
     "justificacion": "<por qué>",
     "fragmento_sustento": "<el texto/dato en que se apoya>"}

``candidato: null`` es una salida **válida y explícita**: "con la evidencia
disponible no puedo elegir". Es preferible a una elección forzada — el caso va a
revisión humana igual, pero sin una decisión inventada que auditar.

Referencias: ADR-008, Gherkin E-CONC-3, doc 03 §4.5, `CONC.md` §1, F5-subplan
§3.4, `algorithms.md` §7 ("solo puede elegir entre los candidatos que
sobrevivieron a las reglas de descarte").
"""

from __future__ import annotations

from typing import Any, Mapping, Sequence

#: Versión del prompt del agente (ADR-005: la trazabilidad guarda la versión).
VERSION_PROMPT_AGENTE = "conclusion-agente@1"

#: Clave del candidato elegido en el JSON de salida.
CLAVE_CANDIDATO = "candidato"
#: Clave de la justificación.
CLAVE_JUSTIFICACION = "justificacion"
#: Clave del fragmento de sustento (ADR-001).
CLAVE_SUSTENTO = "fragmento_sustento"

#: Rol del modelo en ``Settings`` que usa el agente.
ROL_AGENTE = "agente"

SYSTEM_PROMPT_AGENTE = """\
Sos la etapa de DECISIÓN de un sistema de auditoría de comprobantes fiscales \
argentinos. El código ya aplicó reglas determinísticas y NO pudo concluir el \
caso: tu trabajo es elegir, entre los candidatos que sobrevivieron al descarte, \
el que mejor explique la evidencia.

REGLA ABSOLUTA — el universo está cerrado:
- Solo podés elegir un valor que esté en `candidatos_restantes`.
- NO podés proponer ni "resucitar" un valor descartado: si un valor no está en \
la lista, para vos no existe, aunque te parezca correcto.
- Si con la evidencia disponible no podés elegir con fundamento, devolvé \
`"candidato": null`. Abstenerse es una respuesta válida y preferible a inventar \
una decisión.

QUÉ NO HACER:
- No normalices valores ni corrijas la evidencia: no es tu tarea y el programa \
ya lo hizo.
- No inventes datos que no estén en la evidencia.
- No devuelvas la certeza ni el estado del caso: el sistema los deriva de que \
la decisión la tomó un agente (siempre es una decisión de certeza baja que va a \
revisión humana).

CÓMO FUNDAMENTAR:
- `justificacion`: por qué ese candidato y no los otros. Sé concreto y breve.
- `fragmento_sustento`: el dato, texto o campo de la evidencia en el que te \
apoyás. Sin sostén, tu decisión no es auditable.

FORMATO DE SALIDA (JSON, sin texto alrededor):
{"candidato": "<uno de candidatos_restantes o null>", \
"justificacion": "<por qué>", "fragmento_sustento": "<en qué te apoyás>"}
"""


def _lista(valores: Sequence[str] | None) -> str:
    """Lista de valores como texto legible (``A``, ``B`` o ``(ninguno)``)."""
    limpios = [str(valor) for valor in (valores or []) if str(valor).strip()]
    return ", ".join(limpios) if limpios else "(ninguno)"


def _evidencia_legible(evidencia: Mapping[str, Any] | None) -> str:
    """La evidencia vigente por campo, como líneas ``campo: valor``.

    Es la misma superficie que ve el consumidor de la librería (los valores
    **vigentes** de la resolución de T-404): el agente decide sobre lo que el
    sistema ya resolvió, no sobre las lecturas crudas de cada fuente. Un campo
    ausente no se imprime (no se sugiere que su valor sea ``None``).
    """
    if not evidencia:
        return "(sin evidencia)"
    lineas = [
        f"- {campo}: {valor}"
        for campo, valor in sorted(evidencia.items())
        if valor is not None
    ]
    return "\n".join(lineas) if lineas else "(sin evidencia)"


def construir_messages_agente(
    *,
    evidencia: Mapping[str, Any] | None,
    candidatos_restantes: Sequence[str] | None,
    reglas_que_fallaron: Sequence[str] | None = None,
    alertas: Sequence[Mapping[str, Any]] | None = None,
    campos_faltantes: Sequence[str] | None = None,
    descripcion: str | None = None,
) -> list[dict[str, str]]:
    """Construye los ``messages`` del agente para ``OllamaClient.ask`` (ADR-008).

    El agente recibe **exactamente** lo que el Gherkin de E-CONC-3 pide que
    reciba: la evidencia, las **reglas que fallaron** (por qué el código no pudo
    concluir) y los **candidatos restantes**. Además se le pasan las alertas y los
    campos que faltaron porque son parte de "por qué el código no concluyó" — sin
    eso el agente decide a ciegas sobre el motivo de la ambigüedad.

    Los **candidatos descartados no se pasan**: es la primera capa del blindaje
    (lo que el agente no ve, no puede elegir). El orquestador valida igual su
    salida, porque pedir no es garantizar.

    Argumentos:
        evidencia: ``campo -> valor`` vigente (la resolución de T-404).
        candidatos_restantes: el universo cerrado de la elección.
        reglas_que_fallaron: ids de las reglas que llevaron a la ambigüedad.
        alertas: las alertas pendientes (p. ej. el conflicto de R7).
        campos_faltantes: los gaps que quedaron sin cubrir (T-502).
        descripcion: la descripción del documento, si se tiene, como contexto.

    Devuelve:
        La lista de mensajes (``system`` + ``user``) lista para ``ask``.
    """
    partes = [
        "CASO A DECIDIR",
        "",
        "Evidencia vigente (valores ya resueltos y normalizados):",
        _evidencia_legible(evidencia),
        "",
        f"Candidatos restantes (TU UNIVERSO — elegí uno de estos): "
        f"{_lista(candidatos_restantes)}",
        "",
        f"Reglas que el código aplicó sin poder concluir: "
        f"{_lista(reglas_que_fallaron)}",
    ]

    if alertas:
        partes.append("")
        partes.append("Alertas pendientes:")
        for alerta in alertas:
            regla = alerta.get("regla", "?")
            mensaje = alerta.get("mensaje", "")
            partes.append(f"- {regla}: {mensaje}")

    if campos_faltantes:
        partes.append("")
        partes.append(
            "Campos que faltaron y no se pudieron cubrir: "
            f"{_lista(campos_faltantes)}"
        )

    if descripcion:
        partes.extend(["", "Descripción del documento:", descripcion[:1500]])

    partes.extend(
        [
            "",
            "Devolvé SOLO el JSON del contrato de salida: "
            '{"candidato": …, "justificacion": …, "fragmento_sustento": …}. '
            "Recordá: solo un valor de la lista de candidatos restantes, o null "
            "si no podés elegir con fundamento.",
        ]
    )

    return [
        {"role": "system", "content": SYSTEM_PROMPT_AGENTE},
        {"role": "user", "content": "\n".join(partes)},
    ]


__all__ = [
    "VERSION_PROMPT_AGENTE",
    "ROL_AGENTE",
    "CLAVE_CANDIDATO",
    "CLAVE_JUSTIFICACION",
    "CLAVE_SUSTENTO",
    "SYSTEM_PROMPT_AGENTE",
    "construir_messages_agente",
]

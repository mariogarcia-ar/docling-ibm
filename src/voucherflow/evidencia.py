"""Evidencia que se pide a un modelo: el contrato con el lector y el parseo.

Lo que vive acá lo comparten **dos fases del pipeline que se construyeron por
separado**: la evidencia de la lectura de tipo (F2/F3, ``classification``) y la de
la extracción (F4, ``extraction``). Las dos le piden a un modelo un objeto JSON y
las dos tienen que parsearlo con la misma tolerancia, resolver el mismo modelo por
rol y tratar una fuente caída de la misma manera.

⚠️ **Estas seis piezas estaban duplicadas**, y la duplicación era casi perfecta:
lo único que las distinguía era una etiqueta de tarea en los mensajes de error
(``T-302`` vs ``T-401``) y un sustantivo (``lectura``/``extracción``). Eso es lo
que lo hacía peligroso: una corrección aplicada en una sola de las dos copias
—un caso de parseo, una validación de fuente— dejaba a la otra fase con el
comportamiento viejo, **sin que nada fallara**.

**Un caso concreto que ya divergió**: `_fuentes_con_insumo` calculaba lo mismo de
dos maneras (``(markdown or "").strip()`` vs ``bool((markdown or "").strip())``).
Hoy son equivalentes, pero es exactamente el tipo de diferencia que se convierte
en bug cuando alguien agrega un caso.

La etiqueta de tarea dejó de ir en el mensaje: es información de *cuándo* se
escribió el código, no de *qué* salió mal. El mensaje describe el problema y el
llamador sabe en qué fase está.
"""

from __future__ import annotations

import json
from typing import Any, Protocol, runtime_checkable

#: Cómo se llama el campo donde el modelo declara la fuente de su respuesta.
#: El nombre viene de la lectura de tipo (ADR-001) y la extracción usa el mismo
#: para que las dos fases hablen del mismo campo.
CAMPO_FUENTE_DECLARADA = "fuente_lectura"

#: Motivo con el que se marca una lectura cuando el ``fuente_lectura`` que
#: declaró el modelo no coincide con la fuente del system prompt usado (dato
#: menor: la fuente autoritativa la fija el orquestador, no el modelo).
MOTIVO_FUENTE_DECLARADA_DISTINTA = (
    "El modelo declaró fuente_lectura='{declarada}' pero la lectura se pidió "
    "con el system prompt de '{real}'; se conserva la fuente real (el modelo "
    "no decide la trazabilidad)."
)


class ErrorEvidencia(ValueError):
    """La respuesta del modelo no sirve como evidencia.

    Es un error de **protocolo**, no de negocio: el modelo no devolvió algo
    parseable. La respuesta cruda viaja en el mensaje (y en la evidencia) para
    poder diagnosticar.
    """


@runtime_checkable
class Lector(Protocol):
    """Lo mínimo que necesita el pipeline para pedirle una lectura al modelo.

    Es estructural (no hereda de nada): en producción lo satisface
    ``OllamaClient`` y en los tests un doble con el mismo método. Lo que hace
    testeable el flujo sin red ni Ollama.
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


def parsear_json_de_respuesta(
    contenido: str, *, que: str = "la evidencia"
) -> Any:
    """Extrae el objeto JSON de la respuesta de un modelo.

    Tolerante con las formas reales que devuelven los modelos locales: JSON puro,
    JSON dentro de un bloque markdown ```` ```json ````, o JSON con prosa
    alrededor. Se intenta, en orden: ``json.loads`` directo → sin cercas de código
    → primer objeto JSON completo con ``raw_decode``.

    ``que`` nombra lo que se esperaba (``"la evidencia de extracción"``), para que
    el mensaje sirva aunque el mismo parseo lo use más de una fase.

    Lanza:
        :class:`ErrorEvidencia` si no hay ningún objeto JSON parseable. El mensaje
        incluye el inicio de la respuesta para poder diagnosticar.
    """
    texto = contenido.strip()
    if not texto:
        raise ErrorEvidencia(
            f"El modelo devolvió una respuesta vacía donde se esperaba "
            f"el JSON de {que}."
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
        f"La respuesta del modelo no contiene un objeto JSON válido de {que}. "
        f"Respuesta recibida (primeros 200 caracteres): {texto[:200]!r}"
    )


def fuentes_con_insumo(
    fuentes: tuple[str, ...] | list[str],
    *,
    markdown: str | None,
    vista: Any,
    validas: tuple[str, ...],
) -> tuple[list[str], list[str]]:
    """Separa las fuentes pedidas entre las que tienen insumo y las que no.

    ``validas`` es la constante de fuentes de la fase que llama
    (``FUENTES_LECTURA``, ``FUENTES_EXTRACCION``): este módulo **no** impone una
    lista propia, para no agregar una tercera copia de la misma tupla.

    Evita llamar al modelo sin material: la fuente ``llm`` necesita markdown y la
    ``vlm`` una vista con ``ruta_imagen_original``. Las que no tienen insumo se
    **informan** (el llamador las anota en su ``detalle``): la ausencia de una
    fuente es una debilidad de la evidencia, no un detalle cosmético. ADR-002
    pide correr las dos y decir si una no pudo.

    Una fuente desconocida es un error de programación, no un dato del documento:
    se rechaza con ``ValueError`` (no se ignora en silencio).
    """
    con_insumo: list[str] = []
    sin_insumo: list[str] = []
    for fuente in fuentes:
        if fuente not in validas:
            raise ValueError(
                f"fuente inválida: {fuente!r}. Válidas: {validas}."
            )
        tiene = (
            bool((markdown or "").strip())
            if fuente == "llm"
            else getattr(vista, "ruta_imagen_original", None)
        )
        (con_insumo if tiene else sin_insumo).append(fuente)
    return con_insumo, sin_insumo


def resolver_modelo(
    fuente: str, modelo: str | None, settings: Any
) -> tuple[str, int | None]:
    """Resuelve modelo y ``num_ctx`` de la fuente desde la configuración.

    La lectura visual usa el rol ``vlm`` (``qwen2.5vl:3b``) y la textual el rol
    ``llm`` (``qwen2.5:7b``), con el ``num_ctx`` declarado para cada rol
    (E-LIB-3). Si el llamador fija ``modelo``, se usa tal cual y **sin**
    ``num_ctx``: el llamador es responsable, igual que en F2/T-202.

    ``settings`` es ``Any`` a propósito: acá solo se usa ``modelo_para()``, así
    que el contrato es ese método y no la clase entera.
    """
    if modelo:
        return modelo, None
    rol = settings.modelo_para(fuente)
    if rol is None or not rol.modelo:
        raise ValueError(
            f"No hay modelo configurado para la fuente {fuente!r} "
            "(rol 'vlm'/'llm' de Settings, E-LIB-3); hace falta un modelo "
            "para construir la llamada."
        )
    return rol.modelo, rol.num_ctx


def fuente_declarada_del_modelo(
    datos: Any, *, fuente: str
) -> tuple[str | None, str | None]:
    """Lee ``fuente_lectura`` de la respuesta y avisa si no coincide.

    El modelo puede declarar de qué fuente es su lectura, pero **no decide la
    trazabilidad**: la fuente autoritativa la fija el orquestador. Una
    declaración distinta se conserva como problema (es un dato menor, pero
    auditable) y la fuente real se mantiene.

    Devuelve ``(fuente_declarada, problema)``; ``problema`` es ``None`` si el
    modelo no declaró nada o si coincidió.
    """
    declarada = datos.get(CAMPO_FUENTE_DECLARADA) if hasattr(datos, "get") else None
    declarada = declarada.strip().lower() if isinstance(declarada, str) else None
    if declarada and declarada != fuente:
        return declarada, MOTIVO_FUENTE_DECLARADA_DISTINTA.format(
            declarada=declarada, real=fuente
        )
    return declarada, None

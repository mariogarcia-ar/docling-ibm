"""Prompt corto versionado del gate qween (F2 / T-202, épica E-QWE-1).

La decisión binaria "¿es comprobante?" (3 salidas) se hace con un **prompt
corto** sobre la vista de decisión (F2-subplan §3.2). Este módulo congela el
**texto del prompt** (versionado — ADR-005) y la construcción de los
``messages`` de ``OllamaClient.ask`` a partir de una :class:`VistaPreparada`
(T-201), separando el *qué se le pide* (este módulo) del *cómo se decide*
(parseo a :class:`VeredictoGate` en ``qween.decidir_es_comprobante``, T-202).

El texto del prompt se porta de la idea fuente ``docs/ideas/qween.md`` §1
("Fase de validación rápida: ¿es comprobante?") y pide una respuesta breve
orientada a decisión con **tres salidas** (``comprobante | no_comprobante |
indeterminado``). El prompt es **determinista en la salida**: pide solo la
etiqueta, sin explicar; la normalización/tolerancia de la respuesta la hace el
paso siguiente (T-202).

Formato de la imagen en ``messages`` (T-202):
  - Vista con ``ruta_imagen_original`` (imagen): el mensaje ``user`` agrega
    ``images`` con la imagen en **base64**
    (``{"role": "user", "content": ..., "images": [<base64>]}``). Es el formato
    que la API ``/api/chat`` de Ollama espera para el modelo local
    ``qwen2.5vl:3b`` de este entorno: **validado empíricamente** — enviar la
    ruta (absoluta o relativa) en ``images`` devuelve HTTP 400 ``illegal
    base64 data`` porque Ollama interpreta cada ítem de ``images`` como base64.
    Precedente del repo: ``v1/document_extraction.py`` (modalidad VLM) usa el
    mismo patrón (``base64.b64encode(ruta.read_bytes()).decode("ascii")``).
    Nota: algunas versiones de Ollama aceptan rutas en ``images``; acá se
    usa base64 por compatibilidad con el servidor local del entorno y con v1.
    La decisión barata (E-QWE-1) dejará de transportar la imagen completa
    cuando T-201 implemente el hook de thumbnail (``degradar_a_thumbnail``).
  - Vista textual (``ruta_imagen_original is None``): el markdown /
    representación de F1 va en ``content`` (F2-subplan §2.4: no aplica
    thumbnail).
"""

from __future__ import annotations

import base64
from pathlib import Path
from typing import Any

from .vistas import VistaPreparada

#: Versión del prompt corto del gate (ADR-005: trazabilidad de prompt por
#: versión/hash). Se registra en ``EvidenceField.meta.version_prompt`` vía
#: ``nueva_meta`` en ``qween._construir_evidencia_gate`` para poder auditar qué
#: prompt produjo cada decisión (E-CONC-5 / ADR-005).
VERSION_PROMPT_QWEEN = "qween-gate@1"

#: System prompt del gate "¿es comprobante?" (E-QWE-1). Portado de la idea
#: fuente ``docs/ideas/qween.md`` §1. Pide una sola etiqueta de tres opciones
#: (``comprobante | no_comprobante | indeterminado``), sin explicación.
SYSTEM_PROMPT_QWEEN = (
    "Analiza esta imagen/documento de forma rápida.\n"
    "Tu tarea es decidir si corresponde a un comprobante fiscal o comercial.\n"
    "Responder solo con una de estas opciones:\n"
    "- comprobante\n"
    "- no_comprobante\n"
    "- indeterminado\n"
    "\n"
    "No expliques demasiado. Solo decide."
)

#: Contenido del mensaje ``user`` cuando la vista es una imagen: el artefacto
#: va en ``images`` (base64) y este texto corto acompaña a la imagen para el VLM.
USER_SOLO_IMAGEN = (
    "Analizá la imagen adjunta y decidí si es un comprobante fiscal o "
    "comercial. Respondé solo con una de las tres opciones indicadas."
)

#: Plantilla del mensaje ``user`` cuando la vista es textual (markdown de F1).
USER_TEXTO = (
    "Analizá el siguiente documento (markdown) y decidí si es un comprobante "
    "fiscal o comercial. Respondé solo con una de las tres opciones "
    "indicadas.\n\n--- DOCUMENTO ---\n{documento}\n--- FIN DOCUMENTO ---"
)


def construir_messages_gate(vista: VistaPreparada) -> list[dict[str, Any]]:
    """Construye los ``messages`` de ``OllamaClient.ask`` para la vista (T-202).

    Devuelve ``[system, user]`` con el system prompt del gate
    (:data:`SYSTEM_PROMPT_QWEEN`) y, según la vista:

      - Imagen (``ruta_imagen_original``): el mensaje ``user`` incluye
        ``images`` con la imagen en **base64** (formato que exige la API
        ``/api/chat`` de Ollama para el modelo local — validado
        empíricamente) y un texto corto de acompañamiento
        (:data:`USER_SOLO_IMAGEN`).
      - Textual (``ruta_imagen_original is None``): el mensaje ``user`` incluye
        el markdown/representación de la vista (:data:`USER_TEXTO`).

    Lanza:
        ``ValueError`` si la vista es textual y no trae contenido (no hay
        representación sobre la cual decidir).
    """
    system: dict[str, Any] = {"role": "system", "content": SYSTEM_PROMPT_QWEEN}
    if vista.ruta_imagen_original:
        # Ollama /api/chat (modelo local qwen2.5vl:3b) espera la imagen en
        # base64: validado empíricamente — la ruta (absoluta o relativa)
        # devuelve HTTP 400 "illegal base64 data". Mismo patrón que
        # v1/document_extraction.py (modalidad VLM de F4).
        try:
            imagen_b64 = base64.b64encode(
                Path(vista.ruta_imagen_original).read_bytes()
            ).decode("ascii")
        except OSError as exc:
            raise ValueError(
                "construir_messages_gate(): no se pudo leer la imagen de la "
                f"vista '{vista.ruta_imagen_original}' para codificar en "
                f"base64 (T-202): {exc}"
            ) from exc
        user: dict[str, Any] = {
            "role": "user",
            "content": USER_SOLO_IMAGEN,
            "images": [imagen_b64],
        }
    else:
        markdown = (vista.representacion or "").strip()
        if not markdown:
            raise ValueError(
                "construir_messages_gate(): la vista textual no tiene "
                "markdown/representación para construir el mensaje (T-202)."
            )
        user = {
            "role": "user",
            "content": USER_TEXTO.format(documento=markdown),
        }
    return [system, user]


__all__ = [
    "VERSION_PROMPT_QWEEN",
    "SYSTEM_PROMPT_QWEEN",
    "USER_SOLO_IMAGEN",
    "USER_TEXTO",
    "construir_messages_gate",
]

"""Módulo ``validation`` — gate "¿es comprobante?" doble-paso qween (F2).

**Fase**: F2 (refactor qween). F0 dejó el esqueleto con la firma pública y los
contratos congelados (``VeredictoGate``, ``ValidationResult`` y
``validar_comprobante`` lanzando ``NotImplementedError``). T-201 agregó la
preparación de la vista rápida (``vistas.py``) y **T-202** implementa la
**decisión binaria de una pasada** :func:`decidir_es_comprobante` sobre la
vista de decisión con el ``OllamaClient`` (F0/T-005) y el prompt corto
versionado de 3 salidas (``prompt_qween.py``, portado de ``ideas/qween.md`` §1).
La orquestación del doble paso (vistas de revisión/fiel, rechazo y
``validar_y_procesar``/``api.validate``) es T-203: por eso ``validar_comprobante``
**sigue** lanzando ``NotImplementedError`` (regla dura F2-subplan §4; no romper
``test_golden_y_esqueleto.py`` ni ``api.py``).

Responsabilidades implementadas aquí (doc 03 §4.2 y `VAL.md`): decidir de forma
barata (una pasada, prompt corto) si la vista corresponde a un comprobante
(3 salidas: comprobante / no / indeterminado) y reportar la decisión como
:class:`ValidationResult` con ``SourceEvidence`` (ADR-001) en
``detalle["evidencia"]``. El principio de doble calidad (E-QWE-2) y la segunda
pasada de indeterminados son T-203.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from enum import Enum
from typing import Any

from ..models.ollama import OllamaClient
from ..schemas.evidence import EvidenceField, Fuente, SourceEvidence, nueva_meta
from ..settings.config import Settings, cargar_settings
from .prompt_qween import VERSION_PROMPT_QWEEN, construir_messages_gate
from .vistas import VistaPreparada


class VeredictoGate(str, Enum):
    """Salida de la decisión binaria barata "¿es comprobante?" (E-QWE-1)."""

    comprobante = "comprobante"
    no_comprobante = "no_comprobante"
    indeterminado = "indeterminado"


@dataclass
class ValidationResult:
    """Contrato de salida del gate qween (doc 03 §9 ``ValidationResult``).

    ``veredicto``: comprobante / no_comprobante / indeterminado.
    ``vista_usada``: rápida | revisión (nunca se reutiliza la rápida para extraer).
    """

    veredicto: VeredictoGate
    confianza_fuente: str = "media"
    vista_usada: str = "rapida"
    detalle: dict = field(default_factory=dict)


def validar_comprobante(origen: str, quick: bool = True) -> ValidationResult:
    """Gate doble-paso: decide si ``origen`` es un comprobante (F2).

    Esqueleto F0 — se implementa en F2/T-203 (orquestación del doble paso que
    orquesta :func:`decidir_es_comprobante` sobre la vista rápida y, en
    indeterminados, sobre la vista de revisión). T-202 implementa solo la
    decisión de una pasada; esta función sigue lanzando ``NotImplementedError``
    (regla dura F2-subplan §4).
    """
    raise NotImplementedError("validar_comprobante(): se implementa en F2 (T-203, orquestación del doble paso).")


# ---------------------------------------------------------------------------
# T-202 · Decisión binaria "¿es comprobante?" con prompt corto (3 salidas)
# ---------------------------------------------------------------------------

#: Nombre del campo de evidencia del gate (ADR-001 / VAL.md §3): la decisión
#: reporta un ``EvidenceField`` con ``campo="es_comprobante"`` y el veredicto
#: como valor.
CAMPO_GATE = "es_comprobante"

#: Límite de caracteres del fragmento de sustento cuando la vista es textual
#: (recorte del markdown para no inflar la evidencia; T-202). Para imagen el
#: fragmento es la nota de la vista (F2-subplan §2.3: "una nota de la vista
#: usada + confianza" cuando el VLM devuelve solo la etiqueta).
MAX_FRAGMENTO_TEXTO_CHARS = 400


def _fuente_para_vista(vista: VistaPreparada) -> Fuente:
    """Fuente de evidencia del gate según la modalidad de la vista (T-202).

    Decisión documentada: la ``Fuente`` describe **cómo se presentó la vista**
    al modelo (no el checkpoint): si la vista trae imagen
    (``ruta_imagen_original``) la evidencia es **VLM** (modalidad visual,
    análoga a ``flujo_vlm`` de F4); si la vista es textual (markdown,
    ``ruta_imagen_original is None``) la evidencia es **LLM** (modalidad de
    texto). El modelo por defecto del gate es el VLM de ``Settings``
    (``qwen2.5vl:3b``), que acepta ambas modalidades, pero la fuente la define
    la vista (F2-subplan §2.3 y glosario §2.1).
    """
    return Fuente.vlm if vista.ruta_imagen_original else Fuente.llm


def _resolver_modelo_y_ctx(
    modelo: str | None,
    settings: Settings | None,
) -> tuple[str, int | None]:
    """Resuelve el modelo (y ``num_ctx``) para la llamada al gate (T-202).

    Si ``modelo`` es explícito se usa tal cual (sin ``num_ctx``: el llamador
    es responsable); si no, se resuelve del rol ``vlm`` de ``Settings``
    (``settings`` inyectado o ``cargar_settings()`` por defecto — mismo
    criterio que F0/T-005). Lanza ``ValueError`` claro si no hay modelo
    configurable para decidir.
    """
    if modelo:
        return modelo, None
    s = settings if settings is not None else cargar_settings()
    rol = s.modelo_para("vlm")
    if rol is None or not rol.modelo:
        raise ValueError(
            "decidir_es_comprobante(): no hay modelo VLM configurado en "
            "Settings.modelos['vlm'] para decidir (T-202)."
        )
    return rol.modelo, rol.num_ctx


def _normalizar_respuesta(contenido: str) -> str:
    """Normaliza la respuesta del modelo para el parseo de la etiqueta (T-202).

    Minúsculas y todo lo no alfanumérico (puntuación, guiones, guiones bajos,
    comillas) a espacios, colapsando blancos. Así ``no_comprobante``,
    ``no-comprobante`` y ``no comprobante`` colapsan al mismo token stream
    ``no comprobante`` (tolerancia de mayúsculas/espacios/variaciones de
    formato del criterio T-202).
    """
    t = (contenido or "").lower()
    t = re.sub(r"[^a-z0-9]+", " ", t)
    return re.sub(r"\s+", " ", t).strip()


def _parsear_veredicto(
    contenido: str,
) -> tuple[VeredictoGate, str, bool]:
    """Parsea la respuesta cruda del modelo a :class:`VeredictoGate` (T-202).

    Devuelve ``(veredicto, confianza_fuente, reconocido)``. Criterio:

      - ``comprobante`` / ``no_comprobante`` reconocidos (incl. frases
        negativas simples "no [es (un)] comprobante") → **alta** (respuesta
        limpia y firme).
      - ``indeterminado`` explícito → **media** (el modelo respondió una de
        las tres salidas pero no concluye; queda para la 2ª pasada T-203).
      - Respuesta vacía / ruido / no reconocible → **indeterminado** con
        **baja** (fallback; sin excepción) — nota en ``detalle``.

    El parser es determinista sobre el formato de las tres etiquetas del
    prompt corto (qween.md §1): tolera mayúsculas/espacios/puntuación y frases
    negativas simples; no es un parser de lenguaje natural. Cualquier otra
    respuesta cae a ``indeterminado``/baja.
    """
    if not (contenido or "").strip():
        return VeredictoGate.indeterminado, "baja", False
    tok = _normalizar_respuesta(contenido)
    # Orden importante: las frases negativas de "no comprobante" se evalúan
    # antes que "comprobante" (si no, "no comprobante" matchearía como
    # comprobante por la subcadena positiva).
    if re.search(r"\bno\s+(?:es\s+(?:un\s+|una\s+)?|un\s+|una\s+)?comprobante\b", tok):
        return VeredictoGate.no_comprobante, "alta", True
    if re.search(r"\bindeterminado\b", tok):
        return VeredictoGate.indeterminado, "media", True
    if re.search(r"\bcomprobante\b", tok):
        return VeredictoGate.comprobante, "alta", True
    return VeredictoGate.indeterminado, "baja", False


def _fragmento_sustento(vista: VistaPreparada) -> str:
    """Fragmento de sustento del gate (obligatorio en ``EvidenceField``, ADR-001).

    - Vista con imagen: la nota de la vista (``vista.nota``, trazabilidad de
      T-201 con dimensiones/factor) — F2-subplan §2.3 permite "una nota de la
      vista usada + confianza" cuando el VLM devuelve solo la etiqueta.
    - Vista textual: recorte del markdown/representación (acotado a
      :data:`MAX_FRAGMENTO_TEXTO_CHARS` en un corte por palabra).

    Nunca devuelve vacío (contrato ``EvidenceField.fragmento_sustento``).
    """
    if vista.ruta_imagen_original:
        frag = (vista.nota or "").strip() or (
            f"Vista '{vista.tipo_vista}' sobre imagen {vista.ruta_imagen_original}"
        )
    else:
        frag = (vista.representacion or "").strip()
        if len(frag) > MAX_FRAGMENTO_TEXTO_CHARS:
            frag = frag[:MAX_FRAGMENTO_TEXTO_CHARS].rsplit(" ", 1)[0] + " …"
    frag = frag.strip()
    return frag or f"Vista '{vista.tipo_vista}' (sin nota ni fragmento textual)"


def _construir_evidencia_gate(
    vista: VistaPreparada,
    veredicto: VeredictoGate,
    confianza: str,
    modelo: str,
) -> SourceEvidence:
    """Construye el ``SourceEvidence`` de la decisión (T-202, ADR-001).

    Un ``EvidenceField`` con ``campo=CAMPO_GATE`` (``es_comprobante``),
    ``valor`` = el veredicto, ``fragmento_sustento`` = :func:`_fragmento_sustento`
    y ``meta`` = ``nueva_meta(modelo, version_prompt=VERSION_PROMPT_QWEEN)``
    (ADR-005). No se agregan campos fuera del contrato (``extra="forbid"``).
    """
    fuente = _fuente_para_vista(vista)
    campo = EvidenceField(
        campo=CAMPO_GATE,
        valor=veredicto.value,
        fuente=fuente,
        fragmento_sustento=_fragmento_sustento(vista),
        confianza_fuente=confianza,
        meta=nueva_meta(modelo=modelo, version_prompt=VERSION_PROMPT_QWEEN),
    )
    return SourceEvidence(fuente=fuente, campos={CAMPO_GATE: campo})


def decidir_es_comprobante(
    vista: VistaPreparada,
    cliente: OllamaClient,
    *,
    modelo: str | None = None,
    settings: Settings | None = None,
) -> ValidationResult:
    """Decisión de una pasada "¿es comprobante?" sobre una vista (T-202/E-QWE-1).

    F2-subplan §3.2: llama al ``OllamaClient`` (F0/T-005) con el **prompt
    corto versionado** de 3 salidas (``prompt_qween.py``, portado de
    ``ideas/qween.md`` §1) construido a partir de la vista (T-201) y devuelve
    la decisión como :class:`ValidationResult` respetando el contrato congelado
    de F0. Es la **pieza que T-203 orquestará** (1ª pasada sobre la vista
    rápida y 2ª pasada sobre la vista de revisión en indeterminados): por eso
    la vista es un parámetro genérico y ``vista_usada`` se toma de
    ``vista.tipo_vista``.

    Argumentos:
        vista: :class:`VistaPreparada` (T-201) con la que decidir — para
            T-202 la rápida; T-203 la usará también con la de revisión.
        cliente: ``OllamaClient`` inyectable (en la suite default un doble /
            ``FakeSession``; el Ollama real queda en ``@pytest.mark.integration``).
        modelo: modelo a usar. Si es ``None`` se resuelve de ``Settings``
            (rol ``vlm``, default ``qwen2.5vl:3b`` con su ``num_ctx``).
        settings: ``Settings`` opcional para resolver el modelo (si ``None``
            se usa ``cargar_settings()``).

    Devuelve:
        ``ValidationResult`` con ``veredicto`` en ``VeredictoGate``,
        ``confianza_fuente`` (alta/media/baja según el parseo, ver
        :func:`_parsear_veredicto`), ``vista_usada`` = ``vista.tipo_vista`` y
        ``detalle`` con: ``nota`` (explica el veredicto), ``modelo``,
        ``version_prompt`` (``qween-gate@1``, ADR-005), ``respuesta_bruta``,
        ``parseo`` (``reconocido`` + ``normalizado``) y ``evidencia``: el
        :class:`SourceEvidence` serializado con ``model_dump(mode="json")``
        (campo ``es_comprobante``, fuente coherente con la vista, fragmento no
        vacío). ``detalle["evidencia"]`` es reconstruible con
        ``SourceEvidence.model_validate(...)``.

    Lanza:
        ``ValueError`` si la vista textual no trae contenido o no hay modelo
        configurable. Los errores de comunicación de Ollama (``OllamaError``)
        se **propagan** al llamador: el gate no debe silenciar una falla de
        conexión como si fuera "no comprobante"; T-203 decide cómo enrutarlos
        (reintento/HITL) en la orquestación.
    """
    modelo_usado, num_ctx = _resolver_modelo_y_ctx(modelo, settings)
    messages = construir_messages_gate(vista)
    respuesta = cliente.ask(messages, model=modelo_usado, num_ctx=num_ctx)
    contenido = respuesta.contenido

    veredicto, confianza, reconocido = _parsear_veredicto(contenido)
    evidencia = _construir_evidencia_gate(vista, veredicto, confianza, modelo_usado)

    if not reconocido:
        nota = (
            "Respuesta del modelo no reconocida como una de las tres salidas "
            "del prompt corto; se devuelve 'indeterminado' con confianza baja "
            "(T-202)."
        )
    elif veredicto is VeredictoGate.comprobante:
        nota = "El modelo respondió 'comprobante' sobre la vista usada (T-202)."
    elif veredicto is VeredictoGate.no_comprobante:
        nota = (
            "El modelo respondió 'no_comprobante' sobre la vista usada; el "
            "documento no debería llegar a extracción (T-202, ahorro E-QWE)."
        )
    else:
        nota = (
            "El modelo respondió 'indeterminado' sobre la vista usada; queda "
            "para la 2ª pasada de revisión (T-203)."
        )

    detalle: dict[str, Any] = {
        "nota": nota,
        "modelo": modelo_usado,
        "version_prompt": VERSION_PROMPT_QWEEN,
        "respuesta_bruta": contenido,
        "parseo": {
            "reconocido": reconocido,
            "normalizado": _normalizar_respuesta(contenido),
        },
        # SourceEvidence serializable (ADR-001); reconstruible con
        # ``SourceEvidence.model_validate(detalle["evidencia"])``.
        "evidencia": evidencia.model_dump(mode="json"),
    }
    return ValidationResult(
        veredicto=veredicto,
        confianza_fuente=confianza,
        vista_usada=vista.tipo_vista,
        detalle=detalle,
    )


__all__ = [
    "VeredictoGate",
    "ValidationResult",
    "validar_comprobante",
    "CAMPO_GATE",
    "MAX_FRAGMENTO_TEXTO_CHARS",
    "decidir_es_comprobante",
]

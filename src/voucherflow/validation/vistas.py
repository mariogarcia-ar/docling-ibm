"""Preparación de vistas (F2 / T-201, épica E-QWE-1) — vista rápida de doble paso.

Fase F2 (refactor qween): el gate de validación decide con una **vista barata**
si el documento es un comprobante (3 salidas: comprobante / no_comprobante /
indeterminado) y, solo cuando corresponde, sube de calidad para la extracción
(principio de doble calidad, E-QWE-2). Este módulo prepara las **vistas** sobre
la representación procesada de F1 (``ProcessedDocument``, doc 03 §4.1) **sin
volver a correr Docling ni Ollama** (decisión F2-subplan §2.2).

T-201 implementa únicamente ``preparar_vista_rapida`` (thumbnail / calidad
baja-moderada para la decisión barata E-QWE-1). Las vistas de revisión y fiel
son T-203 y **no** se implementan aquí; el contrato de salida
(:class:`VistaPreparada`) ya contempla ``tipo_vista`` ``rapida``/``revision``/
``fiel`` y un gradiente de calidad comparable para que T-203 las distinga sin
cambiar el contrato (y para que T-204 pueda verificar la "calidad distinta").

Decisión de alcance (F2-subplan §3.1, misma política "sin dependencias nuevas"
de F1 §4): **no hay Pillow/OpenCV declarados**, por lo que la vista rápida es un
**stub funcional anotativo**: no remuestrea píxeles con librería externa. En su
lugar:

  - Si la entrada es una **imagen** (o PDF escaneado con render de F1): la vista
    rápida anota la **resolución reducida objetivo** y el **factor de escala**
    sobre las dimensiones reales leídas con ``processing.leer_caracteristicas``
    (stdlib, sin decodificar píxeles) y conserva la ruta de la imagen original
    como ``ruta_imagen_original``. La degradación real de píxeles queda
    documentada como hook ``degradar_a_thumbnail`` (ver :data:`HOOK_DEGRADACION`
    y el docstring de :func:`preparar_vista_rapida`) para cuando exista una
    librería de imagen declarada.
  - Si la entrada es texto nativo (``pdf_texto``/``office``/``texto``): la
    "vista rápida" es el propio ``markdown`` de F1 con metadatos de calidad baja
    (no aplica thumbnail; decisión F2-subplan §2.4).

Coherencia con el contrato congelado F0: el valor de ``tipo_vista`` es el mismo
vocabulario que ``ValidationResult.vista_usada`` (``rapida`` | ``revision``) y
``detalle`` de la vista es lo que T-202 reportará como nota de la vista usada.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

# Reutilización de F1: lectura de dimensiones con stdlib (sin Pillow/OpenCV).
# ``leer_caracteristicas`` devuelve ancho/alto/formato/ratio sin decodificar
# píxeles (F1 / T-102, E-DOC-2); ``EXTENSIONES_IMAGEN`` (de type_detector,
# E-DOC-1) lista las extensiones de imagen del pipeline de F1.
from ..processing import leer_caracteristicas
from ..processing.type_detector import EXTENSIONES_IMAGEN

# ---------------------------------------------------------------------------
# Calidades de vista y tipos de vista (vocabulario del doble paso, E-QWE)
# ---------------------------------------------------------------------------

#: Tipos de vista del doble paso (mismo vocabulario que
#: ``ValidationResult.vista_usada``: ``rapida`` | ``revision``; ``fiel`` es la
#: vista de extracción de T-203/E-QWE-2, que **no reutiliza** la rápida).
TIPOS_VISTA = ("rapida", "revision", "fiel")

#: Calidades por tipo de vista (gradiente E-QWE: la rápida es barata/baja, la
#: de revisión sube a media y la fiel es alta — F2-subplan §2.2 y qween.md §3).
#: La clave de la distinción "doble calidad" es que la rápida sea **menor** que
#: las demás: por eso la fiel (T-203) nunca podrá reutilizarla (regla dura §4).
CALIDAD_POR_TIPO_VISTA: dict[str, str] = {
    "rapida": "baja",
    "revision": "media",
    "fiel": "alta",
}

#: Gradiente numérico de calidad (para comparar/exponer "baja < media < alta"
#: de forma programática en tests y orquestación; T-203/T-204 lo usan).
GRADO_CALIDAD_POR_NIVEL: dict[str, int] = {"baja": 1, "media": 2, "alta": 3}

#: Nombre del hook documentado para la degradación real de píxeles (thumbnail)
#: cuando exista una librería de imagen declarada en ``pyproject.toml``
#: (T-203/F4+). Hoy la vista rápida es anotativa (sin remuestreo).
HOOK_DEGRADACION = "degradar_a_thumbnail"

#: Longitud objetivo (px, lado mayor) de la vista rápida sobre una imagen
#: original. 512 px es una base barata para la decisión "¿es comprobante?"
#: (E-QWE-1: vista reducida; la lectura de detalle no es necesaria). Anotativo.
RESOLUCION_VISTA_RAPIDA_PX = 512

#: Longitud objetivo (px, lado mayor) de la **vista de revisión** (T-203). Es
#: intermedia entre la rápida (512) y la fiel (2048): la 2ª pasada de los
#: indeterminados necesita más detalle que la decisión barata pero no la
#: fidelidad completa de la extracción (E-QWE-1: "vista de revisión de mayor
#: calidad"; qween.md §1). Anotativo (sin remuestreo de píxeles en F2).
RESOLUCION_VISTA_REVISION_PX = 1024

#: Tope alto de referencia (px, lado mayor) de la **vista fiel** (T-203). La
#: vista fiel es la de **máxima fidelidad** disponible y **nunca** reutiliza la
#: vista rápida (regla dura F2-subplan §4, E-QWE-2): su ``representacion`` es la
#: imagen original (o el render de F1) sin reducir, con ``factor_escala=1.0``.
#: El tope (2048 px) solo acota el valor anotado de ``resolucion_objetivo``
#: para no arrastrar cifras desmesuradas a la trazabilidad; no implica
#: reducción de la representación. Anotativo (sin remuestreo de píxeles en F2).
RESOLUCION_VISTA_FIEL_PX = 2048

# Etiquetas de trazabilidad de la nota de cada vista (una por tipo, E-QWE).
_ETIQUETA_RAPIDA = "Vista rápida (T-201/E-QWE-1)"
_ETIQUETA_REVISION = "Vista de revisión (T-203/E-QWE-1)"
_ETIQUETA_FIEL = "Vista fiel (T-203/E-QWE-2)"
# Motivos de calidad por vista (documentan por qué la calidad es baja/media/alta).
_MOTIVO_RAPIDA_IMAGEN = (
    "calidad baja para decidir barato, sin remuestrear píxeles "
    "(sin librería de imagen declarada)."
)
_MOTIVO_RAPIDA_TEXTO = (
    "el gate decide sobre el markdown de F1; no aplica thumbnail "
    "(F2-subplan §2.4)."
)

#: ``tipo_entrada`` de F1 cuya "vista" para el gate es la representación de
#: texto (markdown), sin thumbnail (F2-subplan §2.4: PDF con texto nativo y
#: office/texto no aplican thumbnail).
TIPOS_TEXTO_SIN_THUMBNAIL = frozenset({"pdf_texto", "office", "texto"})


@dataclass(frozen=True)
class VistaPreparada:
    """Vista preparada del doble paso (T-201 / E-QWE-1; se amplía en T-203).

    Representa una "vista" derivada de la representación procesada de F1 para
    decidir (rápida/revisión) o extraer (fiel). Al ser el contrato de salida de
    la preparación, permite **distinguir calidades**: una vista de decisión
    barata (``rapida``) tiene calidad ``baja`` y grado ``1``, menor que la de
    revisión (``media``/``2``) y que la fiel de extracción (``alta``/``3``,
    T-203). La vista fiel **no reutiliza** la rápida (regla dura F2-subplan §4,
    E-QWE-2); la no-reutilización se verifica comparando ``tipo_vista``/
    ``calidad`` y que la fiel apunte a otra derivación (T-203).

    Campos:
        tipo_vista: ``rapida`` | ``revision`` | ``fiel`` (coherente con
            ``ValidationResult.vista_usada``).
        calidad: ``baja`` | ``media`` | ``alta`` según ``tipo_vista``.
        representacion: artefacto a consumir por el gate/extracción — ruta de
            la imagen derivada (thumbnail) o el ``markdown`` cuando no aplica
            thumbnail (texto nativo).
        resolucion_objetivo: px del lado mayor objetivo (0 cuando la vista es
            textual y no tiene resolución de píxeles).
        origen: ruta del documento original (trazabilidad).
        ruta_imagen_original: ruta de la imagen original (o render de F1 para
            PDF escaneado) cuando la vista podría derivarse de una imagen; None
            si la vista es textual. Es el insumo del hook de degradación y de
            T-202 (el VLM decide sobre la vista).
        factor_escala: factor de reducción anotado (original/objetivo; 1.0 si
            no aplica thumbnail). Anotativo en F2 (no remuestrea píxeles).
        nota: nota de trazabilidad en español (documenta el origen y la
            decisión de diseño).
        metadatos: metadatos adicionales (dimensiones originales, hook de
            degradación, etc.).
        nivel_vista: grado numérico de calidad (1 baja / 2 media / 3 alta)
            para comparar vistas de forma programática. Se **deriva** de
            ``calidad`` (no es un insumo del constructor; T-203/T-204 lo usan
            para verificar que la rápida < revisión < fiel).
    """

    tipo_vista: str
    calidad: str
    representacion: str
    resolucion_objetivo: int = 0
    origen: str = ""
    ruta_imagen_original: str | None = None
    factor_escala: float = 1.0
    nota: str = ""
    metadatos: dict[str, Any] = field(default_factory=dict)
    #: Grado numérico de calidad (1 baja / 2 media / 3 alta). No es un insumo:
    #: se **deriva** de ``calidad`` en ``__post_init__`` para comparar vistas
    #: de forma programática sin riesgo de inconsistencia.
    nivel_vista: int = field(init=False, default=1)

    # ------------------------------------------------------------------
    def __post_init__(self) -> None:
        """Valida el contrato de la vista (T-201, doc 03 §4.2 y F2-subplan §2.2).

        Errores con mensaje explicativo (convención del repo) si el tipo de
        vista o la calidad no pertenecen al vocabulario del doble paso, o si la
        calidad no se corresponde con el tipo (p. ej. una vista ``rapida`` no
        puede declarar calidad ``alta``). Deriva ``nivel_vista`` desde
        ``calidad`` (la vista es ``frozen``: se fija con ``object.__setattr__``).
        """
        if self.tipo_vista not in TIPOS_VISTA:
            raise ValueError(
                f"tipo_vista inválido: '{self.tipo_vista}'. "
                f"Válidos: {TIPOS_VISTA} (E-QWE / F2-subplan §2.2)."
            )
        if self.calidad not in GRADO_CALIDAD_POR_NIVEL:
            raise ValueError(
                f"calidad inválida: '{self.calidad}'. "
                f"Válidas: {tuple(GRADO_CALIDAD_POR_NIVEL)} (baja < media < alta)."
            )
        esperada = CALIDAD_POR_TIPO_VISTA[self.tipo_vista]
        if self.calidad != esperada:
            raise ValueError(
                f"calidad '{self.calidad}' incoherente con tipo_vista "
                f"'{self.tipo_vista}' (debe ser '{esperada}'; F2-subplan §2.2)."
            )
        object.__setattr__(self, "nivel_vista", GRADO_CALIDAD_POR_NIVEL[self.calidad])


# ---------------------------------------------------------------------------
# Preparación de la vista rápida (T-201, E-QWE-1)
# ---------------------------------------------------------------------------

def _es_imagen(tipo_entrada: str, ruta: str | Path) -> bool:
    """True si la representación procesada proviene de una imagen (T-201).

    Una entrada es "imagen" cuando F1 la procesó por el pipeline de imagen
    (``imagen``/``pdf_escaneado`` — E-DOC-1) o cuando la extensión del archivo
    es de imagen (los ``ProcessedDocument`` de tests pueden traer un
    ``tipo_entrada`` genérico). Usa la misma fuente de verdad que F1
    (``EXTENSIONES_IMAGEN``) para no duplicar criterio.
    """
    if tipo_entrada in ("imagen", "pdf_escaneado"):
        return True
    return Path(ruta).suffix.lower() in EXTENSIONES_IMAGEN


def _resolucion_objetivo(carac) -> tuple[int, float]:
    """Calcula la resolución objetivo y el factor de escala de la vista rápida.

    Anotativo (T-201, sin remuestreo): dado el lado mayor real de la imagen,
    devuelve (lado_mayor_objetivo, factor_escala = real / objetivo). Si no se
    pudieron leer dimensiones (archivo inexistente/corrupto) se anota objetivo
    0 y factor 1.0 (sin degradación declarable; el gate de T-102 ya lo maneja).
    """
    lado_mayor = max(carac.ancho, carac.alto)
    if not lado_mayor:
        return 0, 1.0
    objetivo = min(lado_mayor, RESOLUCION_VISTA_RAPIDA_PX)
    factor = round(lado_mayor / objetivo, 4) if objetivo else 1.0
    return objetivo, factor


def preparar_vista_rapida(
    documento: "ProcessedDocument",
    *,
    origen: str | Path | None = None,
) -> VistaPreparada:
    """Prepara la vista rápida (thumbnail/calidad baja) para el gate (T-201).

    F2-subplan §3.1 / E-QWE-1: deriva de la representación procesada de F1
    (``ProcessedDocument``) una vista **barata** de calidad baja/moderada para
    la decisión "¿es comprobante?" (3 salidas en T-202). La vista rápida es
    para **decidir barato, no para extraer** (qween.md §1/§6): su calidad es
    ``baja`` (nivel 1), distinguible de la vista de revisión (``media``) y de
    la fiel (``alta``, T-203).

    Decisiones de diseño (documentadas, F2-subplan §3.1 y qween.md §3):

      - **Imagen** (``imagen``/``pdf_escaneado`` o ruta con extensión de
        imagen): la vista rápida anota la **resolución reducida objetivo**
        (lado mayor <= :data:`RESOLUCION_VISTA_RAPIDA_PX`) y el **factor de
        escala**, leídos con ``processing.leer_caracteristicas`` (stdlib, sin
        decodificar píxeles). ``representacion`` apunta a la **imagen original
        de F1** (o al render de PDF escaneado de F1) con metadatos de calidad
        baja y el hook de degradación documentado; **no** se remuestrean
        píxeles (sin Pillow/OpenCV declarados). La degradación real queda en
        ``metadatos["hook_degradacion"]`` = ``degradar_a_thumbnail`` para
        cuando exista librería de imagen declarada (T-203/F4+).
      - **Texto nativo** (``pdf_texto``/``office``/``texto``): la "vista
        rápida" es el propio ``markdown`` de F1 con metadatos de calidad baja
        (decisión F2-subplan §2.4: no aplica thumbnail; el gate decide sobre la
        representación procesada).

    Stub funcional anotativo (análogo a ``processing.preprocesar`` de F1, que
    es idempotente sin CV): T-201 **no transforma píxeles**; anota calidad y
    resolución objetivo. El mecanismo de degradación real (remuestreo) es un
    hook que se documenta aquí y se implementa cuando el paquete declare una
    librería de imagen.

    Argumentos:
        documento: :class:`~voucherflow.models.docling.ProcessedDocument` de F1
            (markdown + boxes + metadatos; construible con 3 posicionales).
        origen: ruta del documento original. Opcional: si se omite se usa
            ``documento.ruta``.

    Devuelve:
        :class:`VistaPreparada` con ``tipo_vista="rapida"``, ``calidad="baja"``
        y la trazabilidad de la derivación.

    Lanza:
        ``ValueError`` si el documento no trae markdown ni ruta (no hay
        representación de la cual derivar la vista).
    """
    doc = documento
    ruta_origen = str(origen) if origen is not None else (doc.ruta or "")
    tipo_entrada = doc.tipo_entrada or ""

    if not ruta_origen and not (doc.markdown or "").strip():
        raise ValueError(
            "preparar_vista_rapida(): el ProcessedDocument no tiene ruta ni "
            "markdown para derivar la vista rápida (T-201)."
        )

    es_imagen = _es_imagen(tipo_entrada, ruta_origen)

    if es_imagen and ruta_origen:
        # Imagen (o PDF escaneado renderizado por F1): leer dimensiones con
        # stdlib (T-102) y anotar la resolución reducida objetivo (sin
        # remuestrear píxeles; stub anotativo, F2-subplan §3.1).
        carac = leer_caracteristicas(ruta_origen)
        objetivo, factor = _resolucion_objetivo(carac)
        nota = (
            f"{_ETIQUETA_RAPIDA}: imagen '{Path(ruta_origen).name}' "
            f"{carac.ancho}x{carac.alto}px -> objetivo {objetivo}px "
            f"(factor {factor}); {_MOTIVO_RAPIDA_IMAGEN}"
        )
        metadatos: dict[str, Any] = {
            "dimensiones_originales": (carac.ancho, carac.alto),
            "formato": carac.formato,
            "ratio": carac.ratio,
            "hook_degradacion": HOOK_DEGRADACION,
            "tipo_vista_futura_fiel_no_reutiliza": True,  # E-QWE-2 (T-203)
        }
        return VistaPreparada(
            tipo_vista="rapida",
            calidad=CALIDAD_POR_TIPO_VISTA["rapida"],
            representacion=ruta_origen,
            resolucion_objetivo=objetivo,
            origen=ruta_origen,
            ruta_imagen_original=ruta_origen,
            factor_escala=factor,
            nota=nota,
            metadatos=metadatos,
        )

    # Texto nativo (pdf_texto/office/texto) o ruta inexistente: la vista rápida
    # es el markdown de F1 (decisión F2-subplan §2.4; no aplica thumbnail).
    markdown = (doc.markdown or "").strip() or "(sin markdown)"
    nota = (
        f"{_ETIQUETA_RAPIDA}: representación de texto "
        f"(tipo_entrada='{tipo_entrada or 'desconocido'}') — {_MOTIVO_RAPIDA_TEXTO}"
    )
    metadatos = {
        "tipo_entrada": tipo_entrada,
        "hook_degradacion": None,  # texto: no hay píxeles que degradar
        "tipo_vista_futura_fiel_no_reutiliza": True,
    }
    return VistaPreparada(
        tipo_vista="rapida",
        calidad=CALIDAD_POR_TIPO_VISTA["rapida"],
        representacion=markdown,
        resolucion_objetivo=0,
        origen=ruta_origen,
        ruta_imagen_original=None,
        factor_escala=1.0,
        nota=nota,
        metadatos=metadatos,
    )


# ---------------------------------------------------------------------------
# Vistas de revisión y fiel (T-203, E-QWE-1 / E-QWE-2)
# ---------------------------------------------------------------------------

def _comun_vista(
    documento: "ProcessedDocument",
    origen: str | Path | None,
) -> tuple[str, str, bool]:
    """Valida el documento y resuelve ``(ruta_origen, tipo_entrada, es_imagen)``.

    Helper compartido por las vistas de revisión/fiel (T-203) para no duplicar
    la lógica de T-201: exige que el ``ProcessedDocument`` de F1 traiga ruta o
    markdown (si no, no hay representación de la cual derivar la vista) y
    decide si la derivación es de imagen o de texto nativo con el mismo
    criterio (:func:`_es_imagen` + ``processing.type_detector``).

    Lanza ``ValueError`` con mensaje en español si no hay representación.
    """
    doc = documento
    ruta_origen = str(origen) if origen is not None else (doc.ruta or "")
    tipo_entrada = doc.tipo_entrada or ""
    if not ruta_origen and not (doc.markdown or "").strip():
        raise ValueError(
            "preparar_vista(): el ProcessedDocument no tiene ruta ni markdown "
            "para derivar la vista (T-203)."
        )
    return ruta_origen, tipo_entrada, _es_imagen(tipo_entrada, ruta_origen)


def preparar_vista_revision(
    documento: "ProcessedDocument",
    *,
    origen: str | Path | None = None,
) -> VistaPreparada:
    """Prepara la vista de revisión (calidad media) para la 2ª pasada (T-203).

    F2-subplan §3.3 / E-QWE-1: cuando la 1ª pasada sobre la vista rápida
    devuelve ``indeterminado``, se re-decide con una vista de **mayor calidad**
    (``revision``/``media``, nivel 2). Se prepara **sobre la misma
    representación procesada de F1** (``ProcessedDocument``), sin volver a
    correr Docling ni Ollama (decisión F2-subplan §2.2).

    Decisiones de diseño (alineadas a T-201, stub funcional anotativo):

      - **Imagen** (``imagen``/``pdf_escaneado`` o ruta con extensión de
        imagen): anota la resolución objetivo intermedia
        (:data:`RESOLUCION_VISTA_REVISION_PX` = 1024 px, entre la rápida de 512
        y la fiel de 2048) y el factor de escala, leídos con
        ``processing.leer_caracteristicas`` (stdlib, sin decodificar píxeles).
        ``representacion`` apunta a la **imagen original de F1** con metadatos
        de calidad media; **no** se remuestrean píxeles (sin Pillow/OpenCV
        declarados): la degradación real es el hook documentado
        (:data:`HOOK_DEGRADACION`).
      - **Texto nativo** (``pdf_texto``/``office``/``texto``): la vista es el
        propio ``markdown`` de F1 con calidad media (no aplica thumbnail;
        F2-subplan §2.4).

    La vista de revisión es **distinta** de la rápida (calidad ``media`` vs.
    ``baja``; ``tipo_vista`` ``revision`` vs. ``rapida``) y de la fiel
    (``media`` < ``alta``): T-204 verifica ese gradiente (E-QWE-2).

    Argumentos:
        documento: :class:`~voucherflow.models.docling.ProcessedDocument` de F1.
        origen: ruta del documento original (si se omite se usa
            ``documento.ruta``).

    Devuelve:
        :class:`VistaPreparada` con ``tipo_vista="revision"`` y
        ``calidad="media"`` (``nivel_vista=2``).

    Lanza:
        ``ValueError`` si el documento no trae markdown ni ruta.
    """
    ruta_origen, tipo_entrada, es_imagen = _comun_vista(documento, origen)

    if es_imagen and ruta_origen:
        carac = leer_caracteristicas(ruta_origen)
        lado_mayor = max(carac.ancho, carac.alto)
        objetivo = min(lado_mayor, RESOLUCION_VISTA_REVISION_PX) if lado_mayor else 0
        factor = round(lado_mayor / objetivo, 4) if objetivo else 1.0
        nota = (
            f"{_ETIQUETA_REVISION}: imagen '{Path(ruta_origen).name}' "
            f"{carac.ancho}x{carac.alto}px -> objetivo {objetivo}px "
            f"(factor {factor}); calidad media para la 2ª pasada de "
            "indeterminados, sin remuestrear píxeles (sin librería de imagen "
            "declarada)."
        )
        metadatos: dict[str, Any] = {
            "dimensiones_originales": (carac.ancho, carac.alto),
            "formato": carac.formato,
            "ratio": carac.ratio,
            "hook_degradacion": HOOK_DEGRADACION,
            "pasada": 2,
        }
        return VistaPreparada(
            tipo_vista="revision",
            calidad=CALIDAD_POR_TIPO_VISTA["revision"],
            representacion=ruta_origen,
            resolucion_objetivo=objetivo,
            origen=ruta_origen,
            ruta_imagen_original=ruta_origen,
            factor_escala=factor,
            nota=nota,
            metadatos=metadatos,
        )

    markdown = (documento.markdown or "").strip() or "(sin markdown)"
    nota = (
        f"{_ETIQUETA_REVISION}: representación de texto "
        f"(tipo_entrada='{tipo_entrada or 'desconocido'}') — calidad media para "
        "la 2ª pasada de indeterminados; no aplica thumbnail "
        "(F2-subplan §2.4)."
    )
    return VistaPreparada(
        tipo_vista="revision",
        calidad=CALIDAD_POR_TIPO_VISTA["revision"],
        representacion=markdown,
        resolucion_objetivo=0,
        origen=ruta_origen,
        ruta_imagen_original=None,
        factor_escala=1.0,
        nota=nota,
        metadatos={
            "tipo_entrada": tipo_entrada,
            "hook_degradacion": None,
            "pasada": 2,
        },
    )


def preparar_vista_fiel(
    documento: "ProcessedDocument",
    *,
    origen: str | Path | None = None,
) -> VistaPreparada:
    """Prepara la vista fiel (calidad alta) que alimenta la extracción (T-203).

    F2-subplan §3.3 / E-QWE-2: para un comprobante confirmado se prepara la
    vista de **máxima fidelidad** disponible, que es la entrada del flujo de
    extracción (F4). **Regla dura (F2-subplan §4, E-QWE-2): la vista fiel NUNCA
    reutiliza la vista rápida**; por eso ``representacion`` apunta a la **imagen
    original de F1** (o al render de F1 sin reducir) con ``factor_escala=1.0``,
    o al ``markdown`` completo para texto nativo (no a la vista degradada).

    Decisiones de diseño:

      - **Imagen**: ``resolucion_objetivo`` = lado mayor real de la imagen
        (máxima fidelidad: no se reduce), acotado solo informativamente al tope
        alto :data:`RESOLUCION_VISTA_FIEL_PX` (2048 px) para la trazabilidad;
        ``factor_escala=1.0`` (no hay reducción). La vista conserva, cuando
        existan, tabla/sello/firma/QR/texto pequeño porque es la imagen
        original, no la derivada degradada (E-QWE-2). La orientación
        corregida/limpieza leve real son hooks de F4 (F2 es anotativo, sin
        Pillow/OpenCV declarados).
      - **Texto nativo**: el ``markdown`` completo de F1 con calidad alta (la
        vista de máxima fidelidad para el flujo textual).

    Argumentos:
        documento: :class:`~voucherflow.models.docling.ProcessedDocument` de F1.
        origen: ruta del documento original (si se omite se usa
            ``documento.ruta``).

    Devuelve:
        :class:`VistaPreparada` con ``tipo_vista="fiel"`` y ``calidad="alta"``
        (``nivel_vista=3``), distinta de la rápida y de la de revisión.

    Lanza:
        ``ValueError`` si el documento no trae markdown ni ruta.
    """
    ruta_origen, tipo_entrada, es_imagen = _comun_vista(documento, origen)

    if es_imagen and ruta_origen:
        carac = leer_caracteristicas(ruta_origen)
        lado_mayor = max(carac.ancho, carac.alto)
        # Máxima fidelidad: la representación es la imagen original sin reducir
        # (factor 1.0). ``resolucion_objetivo`` anota el lado mayor real,
        # acotado al tope alto solo para la trazabilidad (no reduce la imagen).
        objetivo = min(lado_mayor, RESOLUCION_VISTA_FIEL_PX) if lado_mayor else 0
        nota = (
            f"{_ETIQUETA_FIEL}: imagen '{Path(ruta_origen).name}' "
            f"{carac.ancho}x{carac.alto}px -> representación original sin "
            f"reducir (objetivo {objetivo}px, tope {RESOLUCION_VISTA_FIEL_PX}); "
            "NO reutiliza la vista rápida (E-QWE-2); orientación/limpieza son "
            "hooks de F4."
        )
        metadatos: dict[str, Any] = {
            "dimensiones_originales": (carac.ancho, carac.alto),
            "formato": carac.formato,
            "ratio": carac.ratio,
            "reutiliza_vista_rapida": False,
            "resolucion_completa": True,
            "hook_degradacion": None,  # la fiel no se degrada
            "hook_limpieza": "preprocesar_fiel",  # F4 (orientación/limpieza)
        }
        return VistaPreparada(
            tipo_vista="fiel",
            calidad=CALIDAD_POR_TIPO_VISTA["fiel"],
            representacion=ruta_origen,
            resolucion_objetivo=objetivo,
            origen=ruta_origen,
            ruta_imagen_original=ruta_origen,
            factor_escala=1.0,
            nota=nota,
            metadatos=metadatos,
        )

    markdown = (documento.markdown or "").strip() or "(sin markdown)"
    nota = (
        f"{_ETIQUETA_FIEL}: representación de texto completa "
        f"(tipo_entrada='{tipo_entrada or 'desconocido'}') — calidad alta para "
        "la extracción (F4); NO reutiliza la vista rápida (E-QWE-2)."
    )
    return VistaPreparada(
        tipo_vista="fiel",
        calidad=CALIDAD_POR_TIPO_VISTA["fiel"],
        representacion=markdown,
        resolucion_objetivo=0,
        origen=ruta_origen,
        ruta_imagen_original=None,
        factor_escala=1.0,
        nota=nota,
        metadatos={
            "tipo_entrada": tipo_entrada,
            "reutiliza_vista_rapida": False,
            "hook_degradacion": None,
            "hook_limpieza": None,
        },
    )


__all__ = [
    "TIPOS_VISTA",
    "CALIDAD_POR_TIPO_VISTA",
    "GRADO_CALIDAD_POR_NIVEL",
    "HOOK_DEGRADACION",
    "RESOLUCION_VISTA_RAPIDA_PX",
    "RESOLUCION_VISTA_REVISION_PX",
    "RESOLUCION_VISTA_FIEL_PX",
    "TIPOS_TEXTO_SIN_THUMBNAIL",
    "VistaPreparada",
    "preparar_vista_rapida",
    "preparar_vista_revision",
    "preparar_vista_fiel",
]

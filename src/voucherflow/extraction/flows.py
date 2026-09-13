"""Módulo ``extraction`` (F4) — flujos VLM + LLM en paralelo con evidencia.

**Fase**: F4 (extracción) · **Tareas**: T-401, T-402, T-404 · **Épicas**:
E-EXT-1, E-EXT-3.

Este módulo expone la **superficie pública** de la extracción (las firmas que F0
dejó como esqueleto) y delega la lógica en
:mod:`voucherflow.extraction.evidencia` (lectura y contrato),
:mod:`voucherflow.extraction.key_value` (normalización) y
:mod:`voucherflow.rules.precedencia` (combinación por campo):

* :func:`flujo_vlm` — corre el flujo **VLM** (lee la imagen de la vista fiel de
  F2) y devuelve ``SourceEvidence`` (contrato de F0/ADR-001).
* :func:`flujo_llm` — corre el flujo **LLM** (lee el OCR/Markdown de F1) y
  devuelve ``SourceEvidence`` con el **mismo** contrato: eso es lo que permite
  comparar las dos lecturas campo a campo (E-EXT-1).
* :func:`extraer` — corre **las dos fuentes en paralelo** sobre el mismo
  comprobante (no se elige una por documento: es la regla dura de E-EXT-1) y
  conserva **ambas** evidencias sin colapsarlas.
* :func:`combinar_evidencia` — **implementada (T-404)**: aplica la tabla de
  precedencia de ADR-002 y devuelve el ``CombinedEvidence`` con la resolución por
  campo, conservando todas las lecturas.

Los valores que devuelven los flujos están **normalizados** (T-402/E-EXT-3):
CUIT cortado a dígitos y guiones propios, fechas ``YYYY-MM-DD``, montos
numéricos, ``punto_venta``/``numero_comprobante`` derivados del número impreso.
El valor **crudo** de cada campo se conserva en ``meta['valor_crudo']``, y
``normalizar=False`` devuelve la lectura tal como la reportó el modelo. Cada
fuente llega además **calificada por sí sola** (T-403/E-EXT-2).

Nota sobre la firma de F0: ``flujo_vlm(origen, **kwargs)`` /
``flujo_llm(markdown, **kwargs)`` se conservan tal cual (eran el contrato
congelado del esqueleto). ``origen`` se interpreta como **la vista preparada de
F2** (idealmente la vista fiel de E-QWE-2): el camino de ``origen`` a vista es de
F2, no de la extracción, y aceptar una ``VistaPreparada`` evita que este módulo
dependa de ``processing``. El flujo real (con ``OllamaClient``) corre con el rol
``vlm``/``llm`` de ``Settings`` (E-LIB-3).
"""

from __future__ import annotations

from typing import Any, Iterable

from ..rules.precedencia import (
    combinar as rules_combinar,
    resumen_combinacion,
)
from ..schemas.evidence import (
    CombinedEvidence,
    SourceEvidence,
)
from .evidencia import (
    ErrorEvidencia,
    ErrorExtraccion,
    ExtraccionEvidencia,
    Lector,
    ejecutar_flujo,
    extraer_evidencia,
)
from .prompt_extraccion import (
    CAMPOS_EXTRACCION,
    FUENTES_EXTRACCION,
    VERSION_PROMPT_EXTRACCION,
    construir_messages_extraccion,
)

#: Versión de la **combinación** (T-404). Se registra en la trazabilidad del
#: ``CombinedEvidence`` para poder reproducir un caso: si cambia la tabla de
#: precedencia o el algoritmo de resolución, sube la versión y la auditoría
#: distingue los casos (mismo criterio que ``VERSION_PROMPT_EXTRACCION``).
VERSION_COMBINACION = "combinacion-precedencia@1"

#: Versión de la **combinación** (T-404). Se registra en la trazabilidad del
#: ``CombinedEvidence`` para poder reproducir un caso: si cambia la tabla de
#: precedencia o el algoritmo de resolución, sube la versión y la auditoría
#: distingue los casos (mismo criterio que ``VERSION_PROMPT_EXTRACCION``).
VERSION_COMBINACION = "combinacion-precedencia@1"

__all__ = [
    "flujo_vlm",
    "flujo_llm",
    "extraer",
    "combinar_evidencia",
    # re-exportes de conveniencia (contrato y errores del módulo)
    "ErrorEvidencia",
    "ErrorExtraccion",
    "ExtraccionEvidencia",
    "Lector",
    "CAMPOS_EXTRACCION",
    "FUENTES_EXTRACCION",
    "VERSION_PROMPT_EXTRACCION",
    "construir_messages_extraccion",
]


def flujo_vlm(
    origen: Any,
    *,
    lector: Lector,
    modelo: str | None = None,
    settings: Any = None,
    normalizar: bool = True,
    **kwargs: Any,
) -> SourceEvidence:
    """Flujo VLM: lee la imagen y devuelve ``SourceEvidence`` (F4/T-401).

    Implementación de T-401 sobre el ``OllamaClient`` de F0: el flujo construye
    los ``messages`` del prompt de evidencia versionado
    (``extraccion-key-value@1``) con la **imagen** de la vista (base64 reducida
    como en F2/T-202, salvo en la vista ``fiel``, que no se reduce) y convierte
    la respuesta al contrato de evidencia de F0 (ADR-001).

    Argumentos:
        origen: la **vista preparada de F2** (``VistaPreparada``, idealmente la
            vista fiel de E-QWE-2) que trae la imagen en
            ``ruta_imagen_original``. El camino documento→vista es de F2.
        lector: objeto con ``ask`` (protocolo :class:`Lector`); en producción,
            :class:`~voucherflow.models.ollama.OllamaClient`.
        modelo: modelo explícito (default: rol ``vlm`` de ``Settings``, que ya
            declara su ``num_ctx``).
        settings: ``Settings`` para resolver el rol del modelo (default:
            ``cargar_settings()``).
        normalizar: si ``True`` (default), el ``SourceEvidence`` publica los
            valores en su forma canónica (T-402: CUIT cortado, fecha ISO, montos
            numéricos); el crudo queda en ``meta['valor_crudo']``.

    Devuelve:
        ``SourceEvidence`` de la fuente ``vlm`` (un ``EvidenceField`` por campo
        declarado, con su fragmento de sustento y la pasada raw de T-303).

    Lanza:
        ``ValueError`` si la vista no trae imagen o no hay modelo configurable.
        :class:`ErrorEvidencia` si la respuesta no es JSON de objeto.
        ``OllamaError`` si falla la comunicación con el modelo (se propaga).
    """
    return ejecutar_flujo(
        "vlm",
        lector,
        vista=origen,
        modelo=modelo,
        settings=settings,
        normalizar=normalizar,
    ).source_evidence


def flujo_llm(
    markdown: str,
    *,
    lector: Lector,
    modelo: str | None = None,
    settings: Any = None,
    normalizar: bool = True,
    **kwargs: Any,
) -> SourceEvidence:
    """Flujo LLM: lee el OCR/Markdown y devuelve ``SourceEvidence`` (F4/T-401).

    Implementación de T-401 sobre el ``OllamaClient`` de F0: el flujo construye
    los ``messages`` del prompt de evidencia (el markdown va en ``content``) y
    convierte la respuesta al contrato de evidencia de F0 (ADR-001). Los valores
    se publican normalizados (T-402); el crudo queda en ``meta['valor_crudo']``.

    Argumentos:
        markdown: markdown/OCR procesado de F1
            (``api.process(...).markdown``).
        lector: objeto con ``ask`` (protocolo :class:`Lector`).
        modelo: modelo explícito (default: rol ``llm`` de ``Settings``).
        settings: ``Settings`` para resolver el rol del modelo.
        normalizar: si ``True`` (default), normaliza los valores (T-402).

    Devuelve:
        ``SourceEvidence`` de la fuente ``llm``.

    Lanza:
        ``ValueError`` si el markdown está vacío o no hay modelo configurable.
        :class:`ErrorEvidencia` si la respuesta no es JSON de objeto.
        ``OllamaError`` si falla la comunicación con el modelo (se propaga).
    """
    return ejecutar_flujo(
        "llm",
        lector,
        markdown=markdown,
        modelo=modelo,
        settings=settings,
        normalizar=normalizar,
    ).source_evidence


def extraer(
    lector: Lector,
    *,
    markdown: str | None = None,
    vista: Any = None,
    documento_id: str = "documento",
    fuentes: Iterable[str] = FUENTES_EXTRACCION,
    modelo: str | None = None,
    settings: Any = None,
    max_workers: int | None = None,
    normalizar: bool = True,
) -> ExtraccionEvidencia:
    """Corre los dos flujos **en paralelo** y devuelve la evidencia por fuente (T-401).

    Es el punto de entrada de la extracción (E-EXT-1): los flujos VLM y LLM
    corren **siempre juntos** sobre el comprobante —no se elige uno por
    documento—, cada uno devuelve ``SourceEvidence`` con el mismo contrato
    (ADR-001) y las dos evidencias se **conservan sin colapsar** (la resolución
    por campo con la precedencia de ADR-002 es T-404).

    Argumentos:
        lector: objeto con ``ask`` (protocolo :class:`Lector`).
        markdown: markdown/OCR de F1 para la fuente ``llm``.
        vista: ``VistaPreparada`` de F2 (idealmente la **vista fiel**) para la
            fuente ``vlm``.
        documento_id: id del documento (hash sha256 en producción).
        fuentes: fuentes a correr, en orden (default: las dos).
        modelo: modelo explícito para todas las fuentes.
        settings: ``Settings`` para resolver modelo/``num_ctx`` por rol.
        max_workers: tope de hilos (default: una tarea por fuente con insumo;
            ``max_workers=1`` fuerza la serialización).
        normalizar: si ``True`` (default), publica los valores normalizados
            (T-402); ``False`` devuelve la lectura cruda de T-401.

    Devuelve:
        :class:`~voucherflow.extraction.evidencia.ExtraccionEvidencia` con las
        evidencias por fuente, los veredictos raw y la trazabilidad de la corrida
        (fuentes sin insumo, fallos por fuente, paralelismo, duraciones).

    Lanza:
        :class:`ErrorExtraccion` si **todas** las fuentes con insumo fallaron.
    """
    return extraer_evidencia(
        lector,
        markdown=markdown,
        vista=vista,
        documento_id=documento_id,
        fuentes=fuentes,
        modelo=modelo,
        settings=settings,
        max_workers=max_workers,
        normalizar=normalizar,
    )


def combinar_evidencia(
    documento_id: str, fuentes: list[SourceEvidence]
) -> CombinedEvidence:
    """Combina la evidencia de las fuentes con resolución por campo (F4/T-404).

    Implementación del contrato congelado de F0 (misma firma que dejó el
    esqueleto): aplica la **tabla de precedencia** de ADR-002
    (:mod:`voucherflow.rules.precedencia`) y devuelve el ``CombinedEvidence`` que
    consume la conclusión de F5.

    Qué conserva y qué resuelve:

    * **Conserva todas las lecturas**: cada campo del ``CombinedEvidence`` lleva
      la evidencia de cada fuente presente (``vlm``, ``llm``, y las no-lectura si
      las hubiera) — combinar **no** es descartar (ADR-001/ADR-008).
    * **Resuelve el desacuerdo**: agrega la ``FieldResolution`` (ganador + regla
      ``PREC_n`` + motivo) y el atajo operativo ``valor``/``fuente`` con el valor
      vigente del campo. La resolución es independiente del orden de llegada de
      los flujos (corren en paralelo, T-401) y de la fuente que esté debilitada:
      una lectura que la pasada 1 marcó como **inválida** (T-403) no puede ganar
      un campo que otra fuente sí resolvió, y la resolución lo deja por escrito.
    * **Deja los campos ausentes como tales**: un campo que ninguna fuente
      declaró no aparece en ``campos`` con un valor inventado; su resolución
      registra que no se leyó (insumo del gate de F5).

    Argumentos:
        documento_id: id del documento (``sha256`` en producción, T-405/F6).
        fuentes: las ``SourceEvidence`` de la corrida (típicamente las dos de
            ``extraer()``; también acepta las de ``classification`` cuando la
            extracción quiere la letra).

    Devuelve:
        ``CombinedEvidence`` con ``campos`` (lecturas + resolución), una
        ``Decision`` **provisional** —la conclusión real es F5/T-501, así que va
        con ``concluye=False``— y la ``trazabilidad`` de la combinación (fuentes,
        acuerdos, desacuerdos, reglas aplicadas y lecturas descartadas).

    Lanza:
        ``TypeError`` si alguna fuente no es una ``SourceEvidence``.
        ``ValueError`` si ``documento_id`` es vacío (contrato de F0).
    """
    combinacion = rules_combinar(fuentes, documento_id=documento_id)
    resumen = resumen_combinacion(combinacion)
    return CombinedEvidence(
        documento_id=documento_id,
        campos=combinacion.campos,
        # ``decision`` queda en ``None`` a propósito: combinar la evidencia
        # (T-404) y concluir el caso (F5/T-501) son etapas distintas, y el
        # contrato exige que la certeza se derive de la etapa que decidió — que
        # todavía no ocurrió. Inventar acá un veredicto sería exactamente lo que
        # el glosario prohíbe.
        trazabilidad={
            "etapa": VERSION_COMBINACION,
            "version_combinacion": VERSION_COMBINACION,
            "documento_id": documento_id,
            "combinacion": resumen,
            "nota": (
                "Combinación T-404 (ADR-002): las lecturas de todas las fuentes "
                "se conservan y el desacuerdo se resuelve por campo con la tabla "
                "de precedencia. La decisión del caso la produce F5/T-501, así "
                "que `decision` viaja en None a propósito (combinar no es "
                "decidir)."
            ),
        },
    )

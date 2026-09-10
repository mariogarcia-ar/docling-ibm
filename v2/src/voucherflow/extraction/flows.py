"""Módulo ``extraction`` (F4) — flujos VLM + LLM en paralelo con evidencia.

**Fase**: F4 (extracción) · **Tareas**: T-401, T-402 · **Épicas**: E-EXT-1, E-EXT-3.

Este módulo expone la **superficie pública** de la extracción (las firmas que F0
dejó como esqueleto) y delega la lógica en
:mod:`voucherflow.extraction.evidencia` (lectura y contrato) y
:mod:`voucherflow.extraction.key_value` (normalización):

* :func:`flujo_vlm` — corre el flujo **VLM** (lee la imagen de la vista fiel de
  F2) y devuelve ``SourceEvidence`` (contrato de F0/ADR-001).
* :func:`flujo_llm` — corre el flujo **LLM** (lee el OCR/Markdown de F1) y
  devuelve ``SourceEvidence`` con el **mismo** contrato: eso es lo que permite
  comparar las dos lecturas campo a campo (E-EXT-1).
* :func:`extraer` — corre **las dos fuentes en paralelo** sobre el mismo
  comprobante (no se elige una por documento: es la regla dura de E-EXT-1) y
  conserva **ambas** evidencias sin colapsarlas.
* :func:`combinar_evidencia` — **esqueleto de T-404**: combinar por campo con la
  precedencia de ADR-002 es de esa tarea; acá sigue lanzando
  ``NotImplementedError`` a propósito.

Los valores que devuelven los flujos están **normalizados** (T-402/E-EXT-3):
CUIT cortado a dígitos y guiones propios, fechas ``YYYY-MM-DD``, montos
numéricos, ``punto_venta``/``numero_comprobante`` derivados del número impreso.
El valor **crudo** de cada campo se conserva en ``meta['valor_crudo']``, y
``normalizar=False`` devuelve la lectura tal como la reportó el modelo.

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

from ..schemas.evidence import CombinedEvidence, SourceEvidence
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
    """Combina la evidencia de las fuentes con resolución por campo (F4).

    Esqueleto F0 — se implementa en F4/**T-404** aplicando la precedencia
    declarativa de ADR-002. Ni T-401 (flujos en paralelo) ni T-402
    (normalización) la implementan a propósito: sus entregables dejan las dos
    ``SourceEvidence`` **completas y comparables** (mismos campos, mismos
    valores canónicos) para que la resolución por campo tenga con qué trabajar.
    """
    raise NotImplementedError(
        "combinar_evidencia(): se implementa en F4 (T-404, precedencia ADR-002). "
        "T-401 entrega las dos SourceEvidence sin colapsar; usá "
        "extraction.extraer() para obtenerlas."
    )

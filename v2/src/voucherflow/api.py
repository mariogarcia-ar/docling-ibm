"""API de alto nivel (facade) de ``voucherflow`` — F1.

**Fase**: F0 deja el esqueleto de la fachada pública de la librería. La
implementación de cada operación se completa cuando su módulo de capacidad
exista (F1–F5): ``process`` (F1, implementado — T-105/ORQ), ``validate`` (F2),
``classify`` (F3), ``extract`` (F4) y ``run``/``concluir`` (F5).

El objetivo de exponer esta fachada desde F0 es **fijar la API pública** de la
librería (E-LIB-1: "librería primero, cliente después") para que el cliente
CLI/API (F6) y los notebooks consuman una superficie estable, sin conocer los
módulos internos.

Contratos que expone (doc 03 §9): ``ProcessedDocument`` (processing),
``ValidationResult`` (validation), ``VoucherResult`` / ``CombinedEvidence``
(conclusion), ``EvidenceField`` / ``SourceEvidence`` (extraction/classification).
"""

from __future__ import annotations

from .schemas.result import VoucherResult

# ---------------------------------------------------------------------------
# Errores públicos de dominio (E-LIB-1)
# ---------------------------------------------------------------------------


class VoucherflowError(RuntimeError):
    """Error base de la librería, con mensaje claro en español."""


class DocumentoNoProcesableError(VoucherflowError):
    """El documento no superó el gate de procesabilidad (F1/F2)."""


class ContratoError(VoucherflowError):
    """Una respuesta de modelo no cumple el contrato de evidencia (E-LIB-2).

    Se usa en F3/F4 cuando el JSON del VLM/LLM no valida contra
    ``schemas/evidence.py``; el mensaje incluye el detalle de pydantic.
    """


# ---------------------------------------------------------------------------
# Fachada (esqueletos de F1–F5; firmas públicas estables)
# ---------------------------------------------------------------------------


def process(origen: str, *, docling_raw: bool = False) -> "ProcessedDocument":
    """Procesa un documento a representación Markdown+boxes (F1).

    Implementación de F1 (T-105/ORQ): delega en la orquestación del módulo
    ``processing`` (``procesar_documento``), que decide la ruta por tipo de
    entrada (doc 03 §4.1 y ``docs/ideas/docling.md``):

      - PDF escaneado / imagen → gate T-102 → Docling OCR sobre la imagen
        (render→imagen para PDF escaneado, PROC.md §5).
      - PDF apto / office / texto → Docling directo (texto nativo).
      - Formato no soportado → ``DocumentoNoProcesableError`` (rechazo).

    El import de ``processing`` es **diferido** (dentro de la función) para no
    crear un ciclo de import en el arranque del paquete: ``processing`` no
    importa ``api`` a nivel de módulo, pero el import local es la opción más
    segura y explícita (el módulo ``processing`` recién se necesita al
    procesar, no al importar la fachada).

    ``docling_raw`` (Opción A, decisión de alcance subplan F1 §2.5): cuando es
    ``True``, ``ProcessedDocument.markdown`` contiene el **crudo de Docling**
    (el markdown de ``export_to_markdown()`` del adaptador, sin el reordenado
    por posición del exportador E-DOC-3); equivale a ``v1/run_raw.py``. El
    default ``False`` preserva el comportamiento actual (política combinada
    E-DOC-3, contrato F2/F3/F4). Aplica a **documento completo** (imagen / PDF
    apto / office / texto y PDF escaneado vía imagen renderizada); en un PDF
    **mixto/parcial** el crudo pleno no existe (las páginas aptas usan
    PyMuPDF, no Docling por página) y se anota en ``calidad``
    (``docling_raw: "parcial_no_aplica"``), no aplica pleno.

    Argumentos:
        origen: ruta al documento (pdf/imagen/office/txt/...).
        docling_raw: si True, devuelve en ``markdown`` el crudo de Docling (sin
            reordenar por posición); default False = comportamiento actual.

    Devuelve:
        :class:`ProcessedDocument` con ``markdown`` + ``boxes`` + metadatos.

    Lanza:
        ``FileNotFoundError`` si la ruta no existe.
        ``DocumentoNoProcesableError`` si el formato no es soportado, el PDF no
        pudo analizarse o la imagen no superó el gate de procesabilidad (F1).
    """
    # Import diferido: evita el ciclo api -> processing -> (api) en el arranque.
    from .processing.orquestacion import procesar_documento

    return procesar_documento(origen, docling_raw=docling_raw)


def validate(origen: str, quick: bool = True) -> "ValidationResult":
    """Gate "¿es comprobante?" con doble paso qween (F2).

    Esqueleto F0 — se implementa en F2 (módulo ``validation``).
    """
    raise NotImplementedError("validate(): se implementa en F2 (módulo validation).")


def classify(markdown: str, condicion_impositiva: str | None = None) -> VoucherResult:
    """Clasifica tipo/letra + contable (F3).

    Esqueleto F0 — se implementa en F3 (módulo ``classification``).
    """
    raise NotImplementedError("classify(): se implementa en F3 (módulo classification).")


def extract(origen: str, mode: str = "kvi") -> "CombinedEvidence":
    """Extrae evidencia VLM+LLM de un documento (F4).

    Esqueleto F0 — se implementa en F4 (módulo ``extraction``).
    """
    raise NotImplementedError("extract(): se implementa en F4 (módulo extraction).")


def run(origen: str) -> VoucherResult:
    """Pipeline completo document → VoucherResult (F5).

    Esqueleto F0 — se implementa en F5 (módulo ``conclusion`` + orquestador).
    """
    raise NotImplementedError("run(): se implementa en F5 (módulo conclusion/orquestador).")


__all__ = [
    "VoucherflowError",
    "DocumentoNoProcesableError",
    "ContratoError",
    "process",
    "validate",
    "classify",
    "extract",
    "run",
]

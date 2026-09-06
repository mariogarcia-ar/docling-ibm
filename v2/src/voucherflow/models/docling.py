"""Adaptador Docling encapsulado (F0, T-006) — conversión multi-formato.

Encapsula la inicialización y conversión con Docling tal como se hace en v1
(``v1/lib/converter.py`` y ``v1/run.py``: ``DocumentConverter``,
``ImageFormatOption``, ``PdfPipelineOptions`` con OCR de página completa) y
expone un **contrato de salida estructurado** :class:`ProcessedDocument`
(markdown + boxes + metadatos), que es lo que consume F1 (módulo
``processing``).

El adaptador NO implementa la lógica de F1 (detector de tipo de entrada, gate,
clasificador de imagen, orientación, orden por posición): eso vive en
``voucherflow/processing`` en la Fase 1. Aquí solo se encapsula Docling.

Nota de rendimiento (E-DOC): Docling es lento y descarga modelos en la primera
corrida. Por eso los tests **no** convierten archivos reales (se marcan
``@pytest.mark.integration``); el test unitario verifica la construcción del
convertidor con un fixture mínimo.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

logger = logging.getLogger("voucherflow.models.docling")

#: Extensiones que Docling soporta nativamente vía el convertidor multi-formato.
EXTENSIONES_SOPORTADAS = {
    ".pdf",
    ".jpg",
    ".jpeg",
    ".png",
    ".tif",
    ".tiff",
    ".bmp",
    ".docx",
    ".pptx",
    ".xlsx",
    ".html",
    ".md",
    ".txt",
}


@dataclass
class Box:
    """Caja de texto detectada (para debug/reordenamiento en F1).

    Atributos que F1 usa para ordenar por posición (center_y / center_x según
    orientación). ``bbox`` es ``(l, t, r, b)``.

    Campos de tabla (F1/T-104, E-DOC-3): cuando el ítem Docling original es una
    tabla detectada, ``es_tabla=True`` y ``markdown_tabla`` guarda su Markdown
    exportado; el exportador por posición lo trata como ítem único con
    orientación forzada horizontal (paridad con ``v1/lib/orientation.py``).
    """

    texto: str
    center_x: float = 0.0
    center_y: float = 0.0
    bbox: tuple[float, float, float, float] | None = None  # (l, t, r, b)
    es_tabla: bool = False
    markdown_tabla: str | None = None


@dataclass
class ProcessedDocument:
    """Contrato de salida del procesamiento Docling (doc 03 §4.1 y §9).

    Es el objeto que consume F1 (``processing``) y, aguas abajo, los flujos
    LLM/VLM (F3/F4). ``tipo_entrada`` se completa en F1 (p. ej.
    ``pdf_texto``/``pdf_escaneado``/``imagen``/``office``/``txt``); aquí el
    adaptador deja un valor básico según la extensión.
    """

    tipo_entrada: str
    ruta: str
    markdown: str = ""
    boxes: list[Box] = field(default_factory=list)
    orientacion: str = "horizontal"  # se refina en F1
    motor: str | None = "docling"
    calidad: dict[str, Any] | None = None  # QualityReport se define en F1
    n_items: int = 0


def _tipo_entrada_basico(extension: str) -> str:
    """Clasificación básica por extensión (el detector fino es de F1)."""
    ext = extension.lower()
    if ext == ".pdf":
        return "pdf"  # F1 distingue pdf_texto / pdf_escaneado
    if ext in {".jpg", ".jpeg", ".png", ".tif", ".tiff", ".bmp"}:
        return "imagen"
    if ext in {".docx", ".pptx", ".xlsx"}:
        return "office"
    if ext in {".txt", ".md", ".html", ".csv", ".log"}:
        return "texto"
    return "desconocido"


class DoclingConverter:
    """Adaptador sobre Docling para conversión multi-formato a Markdown+boxes.

    Construcción:
        force_full_page_ocr: aplica OCR de página completa (default True, igual
            que v1) sobre imágenes/PDFs escaneados.
        do_table_structure: detecta estructura de tablas (default True, v1).
        formato_documento: formato de exportación (default "markdown").
        converter: instancia Docling ``DocumentConverter`` inyectable (tests).

    Método principal:
        convert(origen) -> ProcessedDocument  (contrato que consume F1).
    """

    def __init__(
        self,
        force_full_page_ocr: bool = True,
        do_table_structure: bool = True,
        formato_documento: str = "markdown",
        converter: Any = None,
    ) -> None:
        self.force_full_page_ocr = force_full_page_ocr
        self.do_table_structure = do_table_structure
        self.formato_documento = formato_documento
        # Se construye lazy para no importar/validar Docling si no se usa
        # (permite tests de construcción sin modelos descargados).
        self._converter = converter
        self._converter_propio = converter is None

    # ------------------------------------------------------------------
    def _obtener_converter(self) -> Any:
        """Construye (una vez) el ``DocumentConverter`` de Docling con OCR full."""
        if self._converter is None:
            from docling.datamodel.base_models import InputFormat
            from docling.datamodel.pipeline_options import PdfPipelineOptions
            from docling.document_converter import DocumentConverter, ImageFormatOption

            pipeline_options = PdfPipelineOptions()
            pipeline_options.ocr_options.force_full_page_ocr = self.force_full_page_ocr
            pipeline_options.do_table_structure = self.do_table_structure

            self._converter = DocumentConverter(
                format_options={
                    InputFormat.IMAGE: ImageFormatOption(pipeline_options=pipeline_options),
                }
            )
            logger.debug("DoclingConverter: convertidor inicializado (OCR full=%s, tablas=%s).", self.force_full_page_ocr, self.do_table_structure)
        return self._converter

    # ------------------------------------------------------------------
    def convert(self, origen: str | Path) -> ProcessedDocument:
        """Convierte un archivo a :class:`ProcessedDocument` (markdown + boxes).

        Argumentos:
            origen: ruta al archivo (pdf/imagen/office/txt...).

        Devuelve:
            :class:`ProcessedDocument` con el markdown exportado y, si está
            disponible, los boxes (ítems con texto y posición).

        Lanza:
            ``FileNotFoundError`` si el archivo no existe.
            ``ValueError`` si la extensión no está soportada.
            Excepciones de Docling si la conversión falla (se propagan con un
            mensaje contextual en español).
        """
        ruta = Path(origen)
        if not ruta.exists():
            raise FileNotFoundError(f"No existe el archivo a convertir: {ruta}")

        ext = ruta.suffix.lower()
        if ext not in EXTENSIONES_SOPORTADAS:
            raise ValueError(
                f"Extensión no soportada por DoclingConverter: '{ext}'. "
                f"Soportadas: {sorted(EXTENSIONES_SOPORTADAS)}"
            )

        converter = self._obtener_converter()
        try:
            resultado = converter.convert(str(ruta))
        except Exception as exc:  # Docling lanza varias excepciones según formato
            raise RuntimeError(f"Docling no pudo convertir '{ruta}': {exc}") from exc

        documento = resultado.document
        markdown = documento.export_to_markdown()

        boxes: list[Box] = []
        n_items = 0
        for item, _nivel in documento.iterate_items():
            n_items += 1
            texto = getattr(item, "text", None)
            prov = getattr(item, "prov", None)

            # Detectar tabla (paridad con v1/lib/orientation.py: label=='table').
            label = getattr(item, "label", None)
            label_value = getattr(label, "value", label)
            es_tabla = label_value == "table"

            # Para una tabla, el texto relevante es su markdown exportado.
            markdown_tabla = None
            if es_tabla:
                try:
                    markdown_tabla = item.export_to_markdown(doc=documento).strip()
                except Exception:
                    markdown_tabla = None
                texto = texto or markdown_tabla

            if not texto and not markdown_tabla:
                continue

            box = Box(
                texto=str(texto),
                es_tabla=es_tabla,
                markdown_tabla=markdown_tabla,
            )
            if prov:
                bbox0 = prov[0].bbox if hasattr(prov[0], "bbox") else None
                if bbox0 is not None:
                    box.bbox = (bbox0.l, bbox0.t, bbox0.r, bbox0.b)
                    ancho = abs(bbox0.r - bbox0.l)
                    alto = abs(bbox0.b - bbox0.t)
                    box.center_x = (bbox0.l + bbox0.r) / 2.0
                    box.center_y = (bbox0.t + bbox0.b) / 2.0
                    if ancho or alto:
                        pass  # la orientación dominante se calcula en F1
            boxes.append(box)

        return ProcessedDocument(
            tipo_entrada=_tipo_entrada_basico(ext),
            ruta=str(ruta),
            markdown=markdown,
            boxes=boxes,
            motor="docling",
            n_items=n_items,
        )

    # ------------------------------------------------------------------
    def exportar_markdown(self, origen: str | Path) -> str:
        """Atajo: devuelve solo el markdown (equivalente a v1)."""
        return self.convert(origen).markdown


__all__ = [
    "EXTENSIONES_SOPORTADAS",
    "Box",
    "ProcessedDocument",
    "DoclingConverter",
    "_tipo_entrada_basico",
]

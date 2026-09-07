"""Enrutado de PDF por página (F1 / apoyo a orquestación, E-DOC-1).

Decide, para un PDF, **qué ruta de procesamiento** conviene por página:
texto nativo directo o convertir a imagen + OCR. Complementa al detector de
tipo de entrada (``type_detector``), que clasifica el PDF en ``pdf_texto``/
``pdf_escaneado`` de forma binaria, con un análisis **por página** que detecta:

  - ``apta_layout``  → texto nativo real suficiente y las imágenes NO dominan:
                      se puede leer directo (pdftotext --layout / Docling).
  - ``escaneada``    → sin texto real (o residual) y con imagen(es): hay que
                      aplicar OCR sobre la página renderizada.
  - ``corrupta``     → texto con >10% de caracteres de control raros (fuentes
                      mal codificadas / ToUnicode roto): tratarla como escaneada.
  - ``vacia``        → página en blanco (se ignora).

Veredicto por PDF (lo que usa la orquestación para elegir ruta):

  - ``apto``          → todas las páginas legibles como texto nativo.
  - ``requiere_ocr``  → todas las páginas son escaneadas/corruptas/vacías.
  - ``parcial``       → mezcla: la orquestación puede procesar cada página con
                      su ruta (texto nativo las aptas, OCR las escaneadas).

Umbrales calibrados (2026-09-06) contra ``v2/tests/fixtures`` (ver el script
de QA ``scripts/detectar_aptos_pdftotext_layout.py``, del que se portó la
lógica). Requiere PyMuPDF (dependencia del paquete desde F1/T-101).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path

# ---------------------------------------------------------------------------
# Umbrales (calibrados; mismos valores que el script de QA)
# ---------------------------------------------------------------------------

#: Máxima proporción de caracteres de control "raros" para considerar texto legible.
RATIO_RAROS_MAX = 0.10
#: Debajo de esta cantidad de chars una página "con texto" se trata como escaneada.
TEXTO_MINIMO_CHARS = 40
#: Si la cobertura de imágenes supera esto y el texto por área es bajo → escaneada.
COBERTURA_IMAGEN_MAX = 0.60
#: Texto por unidad de área (chars/punto²) bajo el cual la imagen domina.
TEXTO_AREA_MIN = 0.15


class ClasePagina(str, Enum):
    """Clasificación de una página de PDF (routing)."""

    apta_layout = "apta_layout"
    escaneada = "escaneada"
    corrupta = "corrupta"
    vacia = "vacia"


class VeredictoPdf(str, Enum):
    """Ruta recomendada para un PDF completo (routing)."""

    apto = "apto"                  # texto nativo en todas las páginas.
    requiere_ocr = "requiere_ocr"  # OCR en todas las páginas (render→imagen).
    parcial = "parcial"            # mezcla: ruta por página.
    error = "error"                # no se pudo abrir/analizar.


@dataclass(frozen=True)
class MetricasPagina:
    """Métricas de bajo costo de una página (para debug/QA)."""

    chars: int = 0
    imagenes: int = 0
    raros: int = 0
    cobertura_img: float = 0.0
    texto_por_area: float = 0.0


@dataclass(frozen=True)
class AnalisisPagina:
    """Resultado del análisis de una página."""

    numero: int  # 1-indexado
    clase: ClasePagina
    metricas: MetricasPagina = field(default_factory=MetricasPagina)


@dataclass(frozen=True)
class AnalisisPdf:
    """Resultado del enrutado de un PDF."""

    veredicto: VeredictoPdf
    paginas: list[AnalisisPagina] = field(default_factory=list)
    resumen: str = ""

    @property
    def requiere_ocr_en_alguna(self) -> bool:
        """True si alguna página necesita OCR (escaneada/corrupta)."""
        return any(
            p.clase in (ClasePagina.escaneada, ClasePagina.corrupta)
            for p in self.paginas
        )

    @property
    def paginas_aptas(self) -> list[int]:
        """Números de página (1-indexados) legibles como texto nativo."""
        return [p.numero for p in self.paginas if p.clase == ClasePagina.apta_layout]

    @property
    def paginas_ocr(self) -> list[int]:
        """Números de página que requieren OCR (escaneada/corrupta)."""
        return [
            p.numero
            for p in self.paginas
            if p.clase in (ClasePagina.escaneada, ClasePagina.corrupta)
        ]


# ---------------------------------------------------------------------------
# Análisis por página
# ---------------------------------------------------------------------------

def _caracteres_raros(texto: str) -> int:
    """Cuenta caracteres de control que no son salto/línea/tab (fuentes rotas)."""
    return sum(1 for c in texto if ord(c) < 32 and c not in "\n\r\t ")


def _cobertura_imagenes(pagina: "fitz.Page") -> float:
    """Fracción (0–1) del área de la página cubierta por imágenes dibujadas."""
    try:
        import pymupdf as fitz
    except ImportError:  # pragma: no cover - pymupdf es dependencia del paquete
        return 0.0
    area_img = 0.0
    for info in pagina.get_image_info():
        r = fitz.Rect(info["bbox"])
        area_img += r.width * r.height
    area_pag = pagina.rect.width * pagina.rect.height
    return (area_img / area_pag) if area_pag else 0.0


def clasificar_pagina(numero: int, pagina: "fitz.Page") -> AnalisisPagina:
    """Clasifica una página de PDF (E-DOC-1 / routing).

    Devuelve :class:`AnalisisPagina` con la clase y sus métricas.
    """
    texto = pagina.get_text()
    n_chars = len(texto.strip())
    n_imagenes = len(pagina.get_images(full=True))
    n_raros = _caracteres_raros(texto)
    cobertura = _cobertura_imagenes(pagina)
    area_pag = pagina.rect.width * pagina.rect.height
    texto_por_area = (n_chars / area_pag) if area_pag else 0.0

    metricas = MetricasPagina(
        chars=n_chars,
        imagenes=n_imagenes,
        raros=n_raros,
        cobertura_img=cobertura,
        texto_por_area=texto_por_area,
    )

    # 1. Sin texto y sin imagen → página en blanco.
    if n_chars == 0 and n_imagenes == 0:
        return AnalisisPagina(numero, ClasePagina.vacia, metricas)

    # 2. Texto con demasiados caracteres corruptos → tratarla como escaneada.
    if n_chars > 0 and (n_raros / n_chars) > RATIO_RAROS_MAX:
        return AnalisisPagina(numero, ClasePagina.corrupta, metricas)

    # 3. Texto despreciable o la imagen domina el área → escaneada.
    if n_chars < TEXTO_MINIMO_CHARS:
        return AnalisisPagina(numero, ClasePagina.escaneada, metricas)
    if cobertura > COBERTURA_IMAGEN_MAX and texto_por_area < TEXTO_AREA_MIN:
        return AnalisisPagina(numero, ClasePagina.escaneada, metricas)

    # 4. Texto real suficiente y las imágenes no dominan → apta (texto nativo).
    return AnalisisPagina(numero, ClasePagina.apta_layout, metricas)


# ---------------------------------------------------------------------------
# Análisis de PDF completo
# ---------------------------------------------------------------------------

def analizar_pdf(origen: str | Path) -> AnalisisPdf:
    """Analiza un PDF y devuelve la ruta recomendada (E-DOC-1 / routing).

    Argumentos:
        origen: ruta al PDF.

    Devuelve:
        :class:`AnalisisPdf` con el veredicto, el análisis por página y un
        resumen legible. Si no se puede abrir, ``veredicto=error``.

    La orquestación usa ``veredicto`` y ``paginas_ocr``/``paginas_aptas`` para
    decidir si procesa el PDF directo (texto nativo), por OCR (render→imagen) o
    página a página (parcial).
    """
    ruta = Path(origen)
    try:
        import pymupdf as fitz
    except ImportError as exc:  # pragma: no cover
        return AnalisisPdf(
            VeredictoPdf.error,
            resumen=f"PyMuPDF no disponible: {exc}",
        )

    try:
        doc = fitz.open(str(ruta))
    except Exception as exc:
        return AnalisisPdf(
            VeredictoPdf.error,
            resumen=f"No se pudo abrir el PDF: {type(exc).__name__}",
        )

    try:
        paginas = [
            clasificar_pagina(numero + 1, pagina)
            for numero, pagina in enumerate(doc)
        ]
    finally:
        doc.close()

    clases = {p.clase for p in paginas}

    if not clases or clases <= {ClasePagina.vacia}:
        return AnalisisPdf(
            VeredictoPdf.requiere_ocr,
            paginas,
            "PDF sin contenido aprovechable (todas las páginas vacías): aplicar OCR.",
        )
    if clases <= {ClasePagina.apta_layout, ClasePagina.vacia}:
        return AnalisisPdf(
            VeredictoPdf.apto,
            paginas,
            "Todo el texto es nativo y las imágenes no dominan: leer directo.",
        )
    if clases <= {ClasePagina.escaneada, ClasePagina.corrupta, ClasePagina.vacia}:
        return AnalisisPdf(
            VeredictoPdf.requiere_ocr,
            paginas,
            "Sin texto nativo aprovechable: aplicar OCR (render→imagen).",
        )
    return AnalisisPdf(
        VeredictoPdf.parcial,
        paginas,
        "Mezcla de páginas aptas y escaneadas/corruptas: procesar por página.",
    )


__all__ = [
    "RATIO_RAROS_MAX",
    "TEXTO_MINIMO_CHARS",
    "COBERTURA_IMAGEN_MAX",
    "TEXTO_AREA_MIN",
    "ClasePagina",
    "VeredictoPdf",
    "MetricasPagina",
    "AnalisisPagina",
    "AnalisisPdf",
    "clasificar_pagina",
    "analizar_pdf",
]

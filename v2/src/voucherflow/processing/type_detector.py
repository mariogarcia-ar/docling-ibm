"""Detector de tipo de entrada (F1 / T-101, épica E-DOC-1).

Decide el tipo de entrada de un archivo y la ruta de procesamiento correcta
(ver doc 03 §4.1): texto nativo para ``pdf_texto``/``texto``/``office``,
conversión a imagen + OCR para ``pdf_escaneado``/``imagen``, y rechazo /
reencolado para ``no_soportado`` (los formatos no soportados **no** fuerzan
OCR — doc 02 E-DOC-1 / doc 03 §4.1).

La distinción ``pdf_texto``/``pdf_escaneado`` (doc 03 §4.1; regla "PDF
escaneado" de E-DOC-1) se resuelve con **PyMuPDF** (``fitz``): se abre el PDF y
se consulta si sus páginas tienen capa de texto real (``page.get_text()``). Un
PDF con texto extraíble en todas sus páginas es ``pdf_texto`` (texto nativo,
sin OCR); uno sin texto en ninguna es ``pdf_escaneado`` (convertir a imagen +
OCR); mixto → se decide por la página dominante.

Por qué PyMuPDF y no una heurística de bytes: PDFs con fuentes ``Type0`` /
codificación ``Identity-H`` y texto comprimido en streams **no** exponen
``/Font`` en los bytes crudos, y un logo junto a texto real hacía que la
heurística los marcara erróneamente como escaneados (11 falsos positivos en
``files/``, validado con datos reales 2026-09-06). PyMuPDF analiza el
contenido real por página.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

# ---------------------------------------------------------------------------
# Tipos soportados y su clasificación por extensión (doc 03 §4.1)
# ---------------------------------------------------------------------------

#: Tipos de entrada que devuelve el detector.
#: "pdf_texto" | "pdf_escaneado" | "imagen" | "office" | "texto" | "no_soportado"
TIPOS_VALIDOS = frozenset(
    {"pdf_texto", "pdf_escaneado", "imagen", "office", "texto", "no_soportado"}
)

#: Extensiones de imagen (entran al gate de procesabilidad / OCR de imagen).
EXTENSIONES_IMAGEN = frozenset({".jpg", ".jpeg", ".png", ".tif", ".tiff", ".bmp"})

#: Extensiones de office (extractor específico docx/xlsx/pptx).
EXTENSIONES_OFFICE = frozenset({".docx", ".xlsx", ".pptx"})

#: Extensiones de texto plano (txt/csv/log/html/md).
EXTENSIONES_TEXTO = frozenset({".txt", ".csv", ".log", ".html", ".htm", ".md"})

#: Extensiones que el detector reconoce como ``no_soportado`` explícito
#: (rechazo/reencolado sin forzar OCR).
EXTENSIONES_NO_SOPORTADAS = frozenset({".exe", ".zip", ".rar", ".7z", ".bin", ".dat"})


@dataclass(frozen=True)
class TipoEntrada:
    """Clasificación del tipo de entrada (doc 03 §4.1)."""

    tipo: str  # "pdf_texto" | "pdf_escaneado" | "imagen" | "office" | "texto" | "no_soportado"
    ruta_ocr: bool  # True si hay que convertir a imagen/OCR
    motivo: str = ""


# ---------------------------------------------------------------------------
# Clasificación pdf_texto / pdf_escaneado con PyMuPDF (E-DOC-1)
# ---------------------------------------------------------------------------

def _clasificar_pdf(ruta: Path) -> TipoEntrada:
    """Clasifica un PDF como ``pdf_texto`` o ``pdf_escaneado`` (E-DOC-1).

    Usa PyMuPDF (``fitz``) para leer el contenido real de cada página:

      - ``paginas_con_texto == total`` → ``pdf_texto`` (capa de texto nativa).
      - ``paginas_con_texto == 0`` → ``pdf_escaneado`` (sin capa de texto).
      - Mixto → se decide por la mayoría (un escaneo con una portada con texto
        no debe forzar OCR en todo).

    Si PyMuPDF no puede abrir el archivo (corrupto) o no está disponible, se
    devuelve ``pdf_texto`` conservador (Docling validará la capa real al
    convertir) — nunca se fuerza OCR sin necesidad (E-DOC-1).
    """
    try:
        import fitz  # PyMuPDF

        doc = fitz.open(str(ruta))
    except Exception:
        # No disponible o PDF corrupto: no forzar OCR, se asume texto.
        return TipoEntrada(
            tipo="pdf_texto",
            ruta_ocr=False,
            motivo="No se pudo inspeccionar el PDF con PyMuPDF; se asume capa de texto (E-DOC-1).",
        )

    try:
        total = doc.page_count
        pag_con_texto = 0
        for pagina in doc:
            if pagina.get_text().strip():
                pag_con_texto += 1
    except Exception:
        return TipoEntrada(
            tipo="pdf_texto",
            ruta_ocr=False,
            motivo="Error leyendo páginas del PDF con PyMuPDF; se asume capa de texto (E-DOC-1).",
        )
    finally:
        doc.close()

    if total == 0:
        return TipoEntrada(
            tipo="pdf_escaneado",
            ruta_ocr=True,
            motivo="PDF sin páginas: no procesable como texto (E-DOC-1).",
        )

    if pag_con_texto == 0:
        return TipoEntrada(
            tipo="pdf_escaneado",
            ruta_ocr=True,
            motivo=f"PDF escaneado: 0 de {total} página(s) con capa de texto (PyMuPDF, E-DOC-1).",
        )

    if pag_con_texto < total:
        return TipoEntrada(
            tipo="pdf_texto",
            ruta_ocr=False,
            motivo=(
                f"PDF mixto: {pag_con_texto} de {total} página(s) con texto; "
                "se prioriza capa de texto (E-DOC-1)."
            ),
        )

    return TipoEntrada(
        tipo="pdf_texto",
        ruta_ocr=False,
        motivo=f"PDF con capa de texto en {total} página(s) (PyMuPDF, E-DOC-1).",
    )


# ---------------------------------------------------------------------------
# Detección principal
# ---------------------------------------------------------------------------

def detectar(origen: str | Path) -> TipoEntrada:
    """Detecta el tipo de entrada de un archivo (F1 / T-101, doc 03 §4.1).

    Argumentos:
        origen: ruta al archivo (pdf/imagen/office/txt/csv/log/html/md...).

    Devuelve:
        :class:`TipoEntrada` con el tipo, si requiere ruta de OCR
        (``ruta_ocr=True`` para ``pdf_escaneado``/``imagen``) y el motivo
        de la decisión.

    Lanza:
        ``FileNotFoundError`` si la ruta no existe.
        ``ValueError`` si la ruta es un directorio.
    """
    ruta = Path(origen)
    if not ruta.exists():
        raise FileNotFoundError(f"No existe el archivo a detectar: {ruta}")
    if ruta.is_dir():
        raise ValueError(f"Se esperaba un archivo, no un directorio: {ruta}")

    ext = ruta.suffix.lower()

    if ext == ".pdf":
        return _clasificar_pdf(ruta)
    if ext in EXTENSIONES_IMAGEN:
        return TipoEntrada(
            tipo="imagen",
            ruta_ocr=True,
            motivo=f"Imagen ({ext}): convertir a texto vía OCR/VLM (E-DOC-1).",
        )
    if ext in EXTENSIONES_OFFICE:
        return TipoEntrada(
            tipo="office",
            ruta_ocr=False,
            motivo=f"Documento office ({ext}): extractor específico, sin OCR (E-DOC-1).",
        )
    if ext in EXTENSIONES_TEXTO:
        return TipoEntrada(
            tipo="texto",
            ruta_ocr=False,
            motivo=f"Texto plano ({ext}): lectura directa, sin OCR (E-DOC-1).",
        )
    if ext in EXTENSIONES_NO_SOPORTADAS:
        return TipoEntrada(
            tipo="no_soportado",
            ruta_ocr=False,
            motivo=f"Formato '{ext}' no soportado: rechazo/reencolado sin forzar OCR (E-DOC-1).",
        )

    return TipoEntrada(
        tipo="no_soportado",
        ruta_ocr=False,
        motivo=f"Extensión '{ext or '(sin extensión)'}' no soportada: rechazo/reencolado (E-DOC-1).",
    )


__all__ = [
    "TIPOS_VALIDOS",
    "EXTENSIONES_IMAGEN",
    "EXTENSIONES_OFFICE",
    "EXTENSIONES_TEXTO",
    "EXTENSIONES_NO_SOPORTADAS",
    "TipoEntrada",
    "detectar",
    "_clasificar_pdf",
]

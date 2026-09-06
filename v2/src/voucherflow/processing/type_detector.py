"""Detector de tipo de entrada (F1 / T-101, épica E-DOC-1).

Decide el tipo de entrada de un archivo y la ruta de procesamiento correcta
(ver doc 03 §4.1): texto nativo para ``pdf_texto``/``texto``/``office``,
conversión a imagen + OCR para ``pdf_escaneado``/``imagen``, y rechazo /
reencolado para ``no_soportado`` (los formatos no soportados **no** fuerzan
OCR — doc 02 E-DOC-1 / doc 03 §4.1).

La distinción ``pdf_texto``/``pdf_escaneado`` (doc 03 §4.1; regla "PDF
escaneado" de E-DOC-1) se resuelve con una **heurística barata** que inspecciona
los bytes del PDF buscando fuentes declaradas (``/Font``) e imágenes de página
(``/Subtype /Image``): un PDF con capa de texto declara tipografías; uno
escaneado se compone sobre todo de imágenes. No se agregan dependencias (sin
pypdf/PyMuPDF en F0) ni se corre Docling para decidir.
"""

from __future__ import annotations

import re
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
# Heurística barata pdf_texto / pdf_escaneado (E-DOC-1)
# ---------------------------------------------------------------------------

#: Marcas de capa de texto: una fuente declarada implica texto vectorial.
_RE_FUENTE = re.compile(rb"/Font\b|/Type\s*/Font\b", re.IGNORECASE)
#: Marcas de imagen incrustada (página escaneada). ``/Subtype /Image`` es la
#: forma canónica; se acepta también ``/Image`` como respaldo.
_RE_IMAGEN = re.compile(rb"/Subtype\s*/Image\b", re.IGNORECASE)
#: Tamaño de ventana (bytes) para decidir si el PDF es escaneado sin recorrer
#: todo el archivo (heurística barata).
_LECTURA_PDF_BYTES = 2 * 1024 * 1024  # 2 MiB de prefijo


def _muestrear_pdf(ruta: Path) -> tuple[int, int]:
    """Cuenta fuentes e imágenes en el prefijo del PDF (heurística barata).

    Devuelve ``(n_fuentes, n_imagenes)``. Lee solo los primeros
    ``_LECTURA_PDF_BYTES`` bytes (los objetos de página suelen estar al
    inicio); suficiente para separar un PDF con capa de texto de uno escaneado.
    """
    with ruta.open("rb") as fh:
        cabeza = fh.read(_LECTURA_PDF_BYTES)
    n_fuentes = len(_RE_FUENTE.findall(cabeza))
    n_imagenes = len(_RE_IMAGEN.findall(cabeza))
    return n_fuentes, n_imagenes


def _clasificar_pdf(ruta: Path) -> TipoEntrada:
    """Clasifica un PDF como ``pdf_texto`` o ``pdf_escaneado`` (E-DOC-1).

    Regla heurística:
      - Si declara fuentes (``/Font``) → hay capa de texto extraíble
        (``pdf_texto``, ruta de texto nativo, sin OCR).
      - Si solo hay imágenes de página y casi ninguna fuente → escaneado
        (``pdf_escaneado``, hay que convertir a imagen y aplicar OCR).
      - Documentos mixtos (texto + imágenes): se decide por la señal
        dominante usando un umbral conservador.

    Sin fuentes declaradas y sin imágenes no debería ocurrir en un PDF válido;
    se resuelve por la cantidad relativa de cada marca.
    """
    n_fuentes, n_imagenes = _muestrear_pdf(ruta)

    if n_fuentes == 0 and n_imagenes == 0:
        # PDF sin señales claras en el prefijo: no forzar OCR, se asume texto
        # (Docling validará la capa real al convertir).
        return TipoEntrada(
            tipo="pdf_texto",
            ruta_ocr=False,
            motivo="PDF sin señales claras de escaneo en el prefijo; se asume capa de texto (E-DOC-1).",
        )

    if n_fuentes == 0:
        # Solo imágenes → PDF escaneado (convertir a imagen + OCR).
        return TipoEntrada(
            tipo="pdf_escaneado",
            ruta_ocr=True,
            motivo=f"PDF escaneado: {n_imagenes} imagen(es) de página y 0 fuentes declaradas (E-DOC-1).",
        )

    if n_imagenes == 0:
        # Solo fuentes → PDF con texto extraíble.
        return TipoEntrada(
            tipo="pdf_texto",
            ruta_ocr=False,
            motivo=f"PDF con capa de texto: {n_fuentes} fuente(s) declaradas (E-DOC-1).",
        )

    # Hay ambas: decidir por la señal dominante (documentos mixtos).
    if n_imagenes >= n_fuentes * 4:
        return TipoEntrada(
            tipo="pdf_escaneado",
            ruta_ocr=True,
            motivo=(
                f"PDF mayormente escaneado: {n_imagenes} imagen(es) vs. "
                f"{n_fuentes} fuente(s) declaradas (E-DOC-1)."
            ),
        )
    return TipoEntrada(
        tipo="pdf_texto",
        ruta_ocr=False,
        motivo=(
            f"PDF con capa de texto predominante: {n_fuentes} fuente(s) vs. "
            f"{n_imagenes} imagen(es) (E-DOC-1)."
        ),
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

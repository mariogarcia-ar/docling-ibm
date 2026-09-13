"""Clasificador de imagen + gate de procesabilidad (F1 / T-102, épica E-DOC-2).

Clasifica una imagen en una de las clases del dominio (doc 03 §4.1 y glosario
doc 00): ``foto``, ``escaneo_plano``, ``screenshot`` o ``manuscrito`` (sello /
firma / texto manuscrito), y decide si la imagen **supera el gate de
procesabilidad** (doc 00: "chequeo previo —formato, resolución mínima,
legibilidad— antes de gastar OCR o llamadas a modelo"; doc 02 E-DOC-2, regla
"clasificación de imagen").

Decisión de alcance F1 (subplan §2.1): clasificador y gate **heurísticos
livianos, sin OpenCV/Pillow** (no se agregan dependencias). Las dimensiones se
leen de las cabeceras de formato con stdlib (``struct``) y la clase se infiere
del **ratio de aspecto**, la resolución y el tipo de archivo:

  - ``escaneo_plano``: ratio de página documento (~1.20–1.60) y alta
    resolución (escaneo limpio de un comprobante en hoja A4/carta).
  - ``foto``: foto de documento real (perspectiva, cámara) — se distingue del
    escaneo por EXIF de cámara/rotación o por resolución/ratio de cámara
    típicos (3:2/4:3).
  - ``screenshot``: captura de pantalla (ratio 16:9/4:3 de display, PNG/RGBA,
    ancho de pantalla típico).
  - ``manuscrito``: señal de texto manuscrito/sello/firma. En F1 (sin OCR real
    en el default) se expone como clase con heurística de metadatos (p. ej.
    archivos con perfil "documento escaneado a color con sellos" no son
    distinguibles pre-OCR); el refinado por contenido llega cuando el motor
    (T-104/F4) reporte legibilidad. Se provee un hook ``sospechar_manuscrito``
    para que T-104 pueda decidir motor VLM.

El gate rechaza (devuelve ``procesable=False``) solo cuando hay **certeza
barata** de que la imagen no puede leerse: formato corrupto/desconocido,
resolución por debajo del mínimo, o ratio extremo (imagen casi lineal /
degradada). El resto pasa para no rechazar en falso documentos reales.
"""

from __future__ import annotations

import struct
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import Any

# ---------------------------------------------------------------------------
# Umbrales del gate y del clasificador (heurística liviana, calibrada con
# fixtures reales del golden set).
# ---------------------------------------------------------------------------

#: Resolución mínima (px) en el lado menor para considerar una imagen legible
#: por OCR (doc 00: "resolución mínima"). 600 px ≈ base aceptable para un
#: comprobante.
RESOLUCION_MINIMA_PX = 600

#: Resolución por debajo de la cual se rechaza por ilegible (muy baja).
RESOLUCION_CRITICA_PX = 200

#: Ratios de aspecto (mayor/menor) que delimitan las clases (calibrados sobre
#: los fixtures: escaneos ~1.23–1.33, fotos de cámara 3:2=1.5 y 4:3=1.33,
#: pantallas 1.78–2.4).
RATIO_DOCUMENTO_MIN = 1.15
RATIO_DOCUMENTO_MAX = 1.45      # documento A4/carta: 1.29–1.41
RATIO_FOTO_MIN = 1.45           # foto de cámara (3:2=1.5)
RATIO_FOTO_MAX = 1.60
RATIO_SCREENSHOT_MIN = 1.60     # 16:10 = 1.6, 16:9 = 1.78

#: Ratio extremo: una imagen más alargada que esto se considera no procesable
#: (banner/recorte inútil) — evita gastar OCR en basura.
RATIO_MAXIMO_PROCESABLE = 4.0

#: Ancho mínimo típico de un screenshot de pantalla (px).
ANCHO_SCREENSHOT_MIN = 640

#: Formato (mime aproximado) permitidos para el gate.
FORMATOS_IMAGEN_VALIDOS = frozenset({".jpg", ".jpeg", ".png", ".tif", ".tiff", ".bmp"})


class ClaseImagen(str, Enum):
    """Clases de imagen del dominio (doc 00 glosario; doc 03 §4.1)."""

    foto = "foto"
    escaneo_plano = "escaneo_plano"
    screenshot = "screenshot"
    manuscrito = "manuscrito"
    #: Valor de seguridad cuando la imagen no pudo clasificarse (no debería
    #: llegar al pipeline si el gate la rechazó).
    desconocida = "desconocida"


@dataclass(frozen=True)
class CaracteristicasImagen:
    """Características de bajo costo de una imagen (sin decodificar píxeles)."""

    ancho: int = 0
    alto: int = 0
    formato: str = ""        # extensión normalizada (".jpg", ".png", ...)
    ratio: float = 0.0       # mayor/menor (>= 1)
    exif_orientacion: int | None = None  # tag EXIF 274 si existe
    es_rgba: bool = False    # PNG con canal alpha (típico de screenshot)
    tamano_bytes: int = 0


@dataclass(frozen=True)
class ClasificacionImagen:
    """Resultado del clasificador (T-102, doc 03 §4.1)."""

    clase: ClaseImagen
    caracteristicas: CaracteristicasImagen = field(default_factory=CaracteristicasImagen)
    motivo: str = ""
    #: Sugerencia de motor (afín a T-104): "ocr" para impreso estándar, "vlm"
    #: cuando se sospecha manuscrito/sello/firma.
    motor_sugerido: str = "ocr"


@dataclass(frozen=True)
class VeredictoGate:
    """Resultado del gate de procesabilidad (T-102, doc 00)."""

    procesable: bool
    motivo: str
    caracteristicas: CaracteristicasImagen = field(default_factory=CaracteristicasImagen)
    #: Razón de rechazo tipificada (para métricas de tasa de rechazo, doc 06).
    razon_rechazo: str | None = None


# ---------------------------------------------------------------------------
# Lectura de dimensiones con stdlib (sin Pillow/OpenCV)
# ---------------------------------------------------------------------------

def _leer_dimensiones_jpeg(data: bytes) -> tuple[int, int] | None:
    """Lee (ancho, alto) de un JPEG recorriendo los marcadores SOF (stdlib)."""
    if not data.startswith(b"\xff\xd8"):
        return None
    i = 2
    n = len(data)
    while i + 4 <= n:
        if data[i] != 0xFF:
            i += 1
            continue
        marker = data[i + 1]
        if marker in (0xD8, 0x01) or 0xD0 <= marker <= 0xD7:
            i += 2
            continue
        if marker == 0xDA:  # SOS: comienza la imagen, no hay más metadata
            break
        if i + 4 > n:
            break
        seg_len = struct.unpack(">H", data[i + 2:i + 4])[0]
        if marker in (0xC0, 0xC1, 0xC2, 0xC3, 0xC5, 0xC6, 0xC7, 0xC9, 0xCA, 0xCB, 0xCD, 0xCE, 0xCF):
            if i + 9 <= n:
                h, w = struct.unpack(">HH", data[i + 5:i + 9])
                return w, h
        i += 2 + seg_len
    return None


def _leer_dimensiones_png(data: bytes) -> tuple[int, int] | None:
    """Lee (ancho, alto) de un PNG desde su cabecera IHDR (stdlib)."""
    if len(data) >= 24 and data[:8] == b"\x89PNG\r\n\x1a\n":
        w, h = struct.unpack(">II", data[16:24])
        return w, h
    return None


def _leer_dimensiones_bmp(data: bytes) -> tuple[int, int] | None:
    """Lee (ancho, alto) de un BMP desde su cabecera DIB (stdlib)."""
    if len(data) >= 26 and data[:2] == b"BM":
        # offset 18: ancho (int32 LE), 22: alto (int32 LE, puede ser negativo)
        w = struct.unpack("<i", data[18:22])[0]
        h = struct.unpack("<i", data[22:26])[0]
        if w > 0 and h != 0:
            return abs(w), abs(h)
    return None


def _leer_dimensiones_tiff(data: bytes) -> tuple[int, int] | None:
    """Lee (ancho, alto) de un TIFF desde el IFD0 (stdlib, little/big endian)."""
    if len(data) < 8:
        return None
    orden = data[:2]
    if orden == b"II":
        endian = "<"
    elif orden == b"MM":
        endian = ">"
    else:
        return None
    magic = struct.unpack(f"{endian}H", data[2:4])[0]
    if magic != 42:
        return None
    offset_ifd = struct.unpack(f"{endian}I", data[4:8])[0]
    if offset_ifd + 2 > len(data):
        return None
    n_entradas = struct.unpack(f"{endian}H", data[offset_ifd:offset_ifd + 2])[0]
    ancho = alto = 0
    for k in range(n_entradas):
        pos = offset_ifd + 2 + k * 12
        if pos + 12 > len(data):
            break
        tag = struct.unpack(f"{endian}H", data[pos:pos + 2])[0]
        tipo = struct.unpack(f"{endian}H", data[pos + 2:pos + 4])[0]
        # type 3 = SHORT (2 bytes), type 4 = LONG (4 bytes)
        if tipo == 3:
            val = struct.unpack(f"{endian}H", data[pos + 8:pos + 10])[0]
        elif tipo == 4:
            val = struct.unpack(f"{endian}I", data[pos + 8:pos + 12])[0]
        else:
            val = 0
        if tag == 256:
            ancho = val
        elif tag == 257:
            alto = val
    if ancho and alto:
        return ancho, alto
    return None


def _exif_orientacion(data: bytes) -> int | None:
    """Lee el tag EXIF 274 (orientación) de un JPEG si está presente (stdlib)."""
    try:
        if not data.startswith(b"\xff\xd8"):
            return None
        # APP1 = 0xFFE1 contiene EXIF
        i = 2
        n = len(data)
        while i + 4 <= n:
            if data[i] != 0xFF:
                i += 1
                continue
            marker = data[i + 1]
            if marker == 0xDA:
                break
            if i + 4 > n:
                break
            seg_len = struct.unpack(">H", data[i + 2:i + 4])[0]
            if marker == 0xE1 and data[i + 4:i + 10] == b"Exif\x00\x00":
                # data[i+4:i+10] es "Exif\0\0" (6 bytes); el TIFF embebido
                # empieza en i+10 y ocupa hasta el final del segmento APP1.
                exif = data[i + 10:i + 2 + seg_len]
                return _exif_orientacion_desde_bloque(exif)
            i += 2 + seg_len
    except Exception:
        return None
    return None


def _exif_orientacion_desde_bloque(exif: bytes) -> int | None:
    """Extrae el tag 274 dentro del bloque EXIF (TIFF embebido).

    El tag ``Orientation`` suele vivir en el **subIFD EXIF** (apuntado por el
    tag 34665 del IFD0), no en el IFD0. Se busca en ambos para cubrir las
    variantes de cámaras.
    """
    try:
        if len(exif) < 8:
            return None
        # El bloque llega sin el prefijo "Exif\0\0" -> empieza con el header TIFF.
        orden = exif[:2]
        endian = "<" if orden == b"II" else (">" if orden == b"MM" else None)
        if endian is None:
            return None

        def _leer_ifd(offset_ifd: int) -> tuple[dict[int, int], int | None]:
            """Lee un IFD; devuelve (tags con valor corto, offset subIFD EXIF)."""
            if offset_ifd + 2 > len(exif):
                return {}, None
            n_entradas = struct.unpack(f"{endian}H", exif[offset_ifd:offset_ifd + 2])[0]
            tags: dict[int, int] = {}
            offset_exif: int | None = None
            for k in range(n_entradas):
                pos = offset_ifd + 2 + k * 12
                if pos + 12 > len(exif):
                    break
                tag = struct.unpack(f"{endian}H", exif[pos:pos + 2])[0]
                tipo = struct.unpack(f"{endian}H", exif[pos + 2:pos + 4])[0]
                if tag == 34665 and tipo == 4:  # ExifIFDPointer (LONG)
                    offset_exif = struct.unpack(f"{endian}I", exif[pos + 8:pos + 12])[0]
                    continue
                # type 3 = SHORT (2 bytes); type 4 = LONG (4 bytes)
                if tipo == 3:
                    tags[tag] = struct.unpack(f"{endian}H", exif[pos + 8:pos + 10])[0]
                elif tipo == 4:
                    tags[tag] = struct.unpack(f"{endian}I", exif[pos + 8:pos + 12])[0]
            return tags, offset_exif

        # IFD0 + subIFD EXIF (tag 34665).
        offset_ifd0 = struct.unpack(f"{endian}I", exif[4:8])[0]
        tags0, offset_exif = _leer_ifd(offset_ifd0)
        if 274 in tags0:
            return tags0[274]
        if offset_exif is not None:
            tags_exif, _ = _leer_ifd(offset_exif)
            if 274 in tags_exif:
                return tags_exif[274]
    except Exception:
        return None
    return None


def leer_caracteristicas(ruta: str | Path) -> CaracteristicasImagen:
    """Lee características de bajo costo de una imagen (sin decodificar píxeles).

    Usa solo stdlib (``struct``) sobre las cabeceras de JPEG/PNG/BMP/TIFF. Si el
    formato no es legible o el archivo está corrupto devuelve características
    con dimensiones 0 (el gate las rechazará).
    """
    p = Path(ruta)
    ext = p.suffix.lower()
    tamano = 0
    try:
        tamano = p.stat().st_size
    except OSError:
        pass
    cabecera = b""
    try:
        with p.open("rb") as fh:
            cabecera = fh.read(4096)
    except OSError:
        pass

    dims: tuple[int, int] | None = None
    es_rgba = False
    if ext == ".png" or cabecera.startswith(b"\x89PNG\r\n\x1a\n"):
        dims = _leer_dimensiones_png(cabecera)
        es_rgba = cabecera[25] == 6 if len(cabecera) > 25 else False  # color type RGBA=6
    elif ext in (".jpg", ".jpeg") or cabecera.startswith(b"\xff\xd8"):
        dims = _leer_dimensiones_jpeg(cabecera)
    elif ext in (".bmp",) or cabecera.startswith(b"BM"):
        dims = _leer_dimensiones_bmp(cabecera)
    elif ext in (".tif", ".tiff"):
        dims = _leer_dimensiones_tiff(cabecera)

    ancho, alto = dims if dims else (0, 0)
    ratio = (max(ancho, alto) / min(ancho, alto)) if (ancho and alto) else 0.0
    orientacion = _exif_orientacion(cabecera) if ext in (".jpg", ".jpeg") else None

    return CaracteristicasImagen(
        ancho=ancho,
        alto=alto,
        formato=ext,
        ratio=round(ratio, 4),
        exif_orientacion=orientacion,
        es_rgba=es_rgba,
        tamano_bytes=tamano,
    )


# ---------------------------------------------------------------------------
# Clasificador heurístico
# ---------------------------------------------------------------------------

def clasificar(origen: str | Path) -> ClasificacionImagen:
    """Clasifica una imagen (foto/escaneo_plano/screenshot/manuscrito).

    Doc 03 §4.1 (T-102): la clase determina preprocesamiento (T-103) y motor
    (T-104). La heurística usa ratio/resolución/EXIF y **no** decodifica
    píxeles (sin OpenCV/Pillow, subplan §2.1).

    La detección de ``manuscrito``/sello/firma por contenido requiere leer el
    documento (OCR/VLM); en F1 se expone ``motor_sugerido`` y el hook
    ``sospechar_manuscrito`` para que T-104 decida motor sin llamar a Ollama.
    """
    carac = leer_caracteristicas(origen)
    ruta = Path(origen)

    # --- Señales fuertes de screenshot (independientes del ratio por si el
    #     display es vertical). PNG con alpha + dimensiones de pantalla.
    if carac.formato == ".png" and carac.es_rgba and carac.ancho >= ANCHO_SCREENSHOT_MIN:
        return ClasificacionImagen(
            clase=ClaseImagen.screenshot,
            caracteristicas=carac,
            motivo="Screenshot: PNG con canal alpha y ancho de pantalla (E-DOC-2).",
            motor_sugerido="ocr",
        )

    # --- EXIF de rotación/cámara => foto (no escaneo plano).
    if carac.exif_orientacion not in (None, 1):
        return ClasificacionImagen(
            clase=ClaseImagen.foto,
            caracteristicas=carac,
            motivo=f"Foto: EXIF orientación {carac.exif_orientacion} indica captura de cámara (E-DOC-2).",
            motor_sugerido="ocr",
        )

    # --- Clasificación por ratio de aspecto.
    if carac.ancho and carac.alto and carac.ratio >= 1.0:
        # Screenshot: ratio de pantalla Y ancho típico de display. Un recorte
        # panorámico pequeño (logo/banner) no es un screenshot de pantalla.
        if (
            RATIO_SCREENSHOT_MIN <= carac.ratio <= RATIO_MAXIMO_PROCESABLE
            and carac.ancho >= ANCHO_SCREENSHOT_MIN
        ):
            return ClasificacionImagen(
                clase=ClaseImagen.screenshot,
                caracteristicas=carac,
                motivo=f"Screenshot: ratio de pantalla {carac.ratio:.2f} y ancho {carac.ancho}px (E-DOC-2).",
                motor_sugerido="ocr",
            )
        if RATIO_FOTO_MIN <= carac.ratio < RATIO_FOTO_MAX:
            return ClasificacionImagen(
                clase=ClaseImagen.foto,
                caracteristicas=carac,
                motivo=f"Foto: ratio de cámara {carac.ratio:.2f} (E-DOC-2).",
                motor_sugerido="ocr",
            )
        if RATIO_DOCUMENTO_MIN <= carac.ratio < RATIO_DOCUMENTO_MAX:
            return ClasificacionImagen(
                clase=ClaseImagen.escaneo_plano,
                caracteristicas=carac,
                motivo=f"Escaneo plano: ratio de documento {carac.ratio:.2f} (E-DOC-2).",
                motor_sugerido="ocr",
            )
        # Ratio panorámico con ancho pequeño: fragmento/recorte, no screenshot
        # de pantalla. Se clasifica como foto (el gate lo acepta si es legible).
        if carac.ratio >= RATIO_SCREENSHOT_MIN:
            return ClasificacionImagen(
                clase=ClaseImagen.foto,
                caracteristicas=carac,
                motivo=f"Recorte panorámico pequeño {carac.ancho}x{carac.alto}: foto/fragmento (E-DOC-2).",
                motor_sugerido="ocr",
            )
        if carac.ratio < RATIO_DOCUMENTO_MIN:
            # Cuadrado o casi cuadrado: puede ser foto de documento o escaneo.
            if carac.alto >= RESOLUCION_MINIMA_PX:
                return ClasificacionImagen(
                    clase=ClaseImagen.escaneo_plano,
                    caracteristicas=carac,
                    motivo=f"Imagen casi cuadrada {carac.ancho}x{carac.alto} con resolución suficiente (E-DOC-2).",
                    motor_sugerido="ocr",
                )
            return ClasificacionImagen(
                clase=ClaseImagen.foto,
                caracteristicas=carac,
                motivo=f"Imagen casi cuadrada de baja resolución {carac.ancho}x{carac.alto}: foto (E-DOC-2).",
                motor_sugerido="ocr",
            )

    return ClasificacionImagen(
        clase=ClaseImagen.desconocida,
        caracteristicas=carac,
        motivo="Imagen no clasificable por metadatos (sin dimensiones legibles).",
        motor_sugerido="ocr",
    )


# ---------------------------------------------------------------------------
# Gate de procesabilidad
# ---------------------------------------------------------------------------

def _razon_rechazo(carac: CaracteristicasImagen) -> tuple[bool, str | None, str]:
    """Evalúa las condiciones baratas de rechazo (resolución/formato/ratio).

    Devuelve ``(procesable, razon_rechazo, motivo)``. Solo rechaza con certeza
    barata (doc 00 gate): no gastar OCR en imágenes que no pueden leerse.
    """
    # Formato desconocido o archivo vacío.
    if carac.formato not in FORMATOS_IMAGEN_VALIDOS:
        return False, "formato_no_soportado", (
            f"Formato '{carac.formato or '(desconocido)'}' no es una imagen soportada (E-DOC-2)."
        )
    if carac.tamano_bytes == 0:
        return False, "archivo_vacio", "El archivo está vacío o no se pudo leer (E-DOC-2)."

    # Dimensiones no legibles => corrupto o formato no parseable.
    if carac.ancho <= 0 or carac.alto <= 0:
        return False, "dimensiones_ilegibles", (
            "No se pudieron leer las dimensiones (archivo corrupto o formato no soportado) (E-DOC-2)."
        )

    # Ratio extremo (banner/recorte): no es un documento — señal más fuerte que
    # la resolución, se chequea primero.
    if carac.ratio > RATIO_MAXIMO_PROCESABLE:
        return False, "ratio_extremo", (
            f"Ratio de aspecto extremo ({carac.ratio:.2f}): no parece un documento (E-DOC-2)."
        )

    # Resolución crítica (muy baja para OCR).
    lado_menor = min(carac.ancho, carac.alto)
    if lado_menor < RESOLUCION_CRITICA_PX:
        return False, "resolucion_muy_baja", (
            f"Resolución demasiado baja para OCR: {lado_menor}px < {RESOLUCION_CRITICA_PX}px (E-DOC-2)."
        )

    # Baja resolución (entre crítica y mínima): procesable con advertencia.
    if lado_menor < RESOLUCION_MINIMA_PX:
        return True, "resolucion_baja", (
            f"Resolución baja ({lado_menor}px) pero procesable; puede requerir preprocesamiento (T-103)."
        )

    return True, None, "Imagen procesable: formato, resolución y ratio válidos (E-DOC-2)."


def verificar_procesabilidad(origen: str | Path) -> VeredictoGate:
    """Decide si una imagen supera el gate de procesabilidad (T-102).

    Doc 00: "chequeo previo (formato, resolución mínima, legibilidad) antes de
    gastar OCR o llamadas a modelo". Si ``procesable=False`` el documento se
    rechaza/reencola (doc 03 §4.1: GATE -->|no pasa| REJ) sin invertir OCR.
    """
    carac = leer_caracteristicas(origen)
    procesable, razon, motivo = _razon_rechazo(carac)
    return VeredictoGate(
        procesable=procesable,
        motivo=motivo,
        caracteristicas=carac,
        razon_rechazo=razon,
    )


# ---------------------------------------------------------------------------
# Hook para T-104: sospecha de manuscrito/sello/firma (motor VLM)
# ---------------------------------------------------------------------------

def sospechar_manuscrito(clasificacion: ClasificacionImagen, senal: dict[str, Any] | None = None) -> bool:
    """Indica si conviene priorizar motor VLM por posible manuscrito/sello/firma.

    Doc 02 E-DOC-2: "Dado una imagen con texto manuscrito, firma o sello →
    se prioriza un modelo visual (VLM)". La señal fina de manuscrito proviene
    de leer el contenido (OCR de baja confianza, boxes irregulares); en F1
    ese análisis llega en T-104. Este hook consume la señal cuando exista y,
    por ahora, devuelve ``False`` para no forzar VLM en el default (no se
    llama a Ollama en F1, subplan §2.2).
    """
    if senal is None:
        return False
    # Señales baratas que puede reportar un OCR previo o el propio clasificador.
    return bool(
        senal.get("contiene_sello")
        or senal.get("contiene_firma")
        or senal.get("manuscrito_detectado")
        or clasificacion.motor_sugerido == "vlm"
    )


__all__ = [
    "RESOLUCION_MINIMA_PX",
    "RESOLUCION_CRITICA_PX",
    "ClaseImagen",
    "CaracteristicasImagen",
    "ClasificacionImagen",
    "VeredictoGate",
    "leer_caracteristicas",
    "clasificar",
    "verificar_procesabilidad",
    "sospechar_manuscrito",
]

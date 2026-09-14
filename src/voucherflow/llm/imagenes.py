"""Imagen: codificación a data URL y estimación de sus tokens.

El token de la imagen no es gratis y no se estima igual en todos los
proveedores: unos cobran por fórmula de mosaicos según el tamaño, otros
redimensionan a un tope fijo y **agrandan** las chicas. Por eso el adaptador de
cada proveedor declara ``tokens_por_imagen`` y acá vive el cálculo del caso
general, junto con la codificación.

El límite de peso se valida **antes** de mandar: una imagen que la API rechaza
gasta una llamada y devuelve un error que habla del payload, no del archivo.
"""

from __future__ import annotations

import base64
import math
from pathlib import Path
from typing import Any

from .config import LIMITE_BYTES_IMAGEN, MIME_POR_EXTENSION, TOKENS_MAX_IMAGEN

# --------------------------------------------------------------------------- #
# Imagen: codificación y estimación de tokens
# --------------------------------------------------------------------------- #


def _dimensiones(ruta: Path) -> tuple[int, int] | None:
    """Dimensiones de la imagen vía Pillow (best-effort)."""
    try:
        from PIL import Image, ImageOps
    except ImportError:
        return None
    try:
        with Image.open(ruta) as img:
            img = ImageOps.exif_transpose(img)
            return int(img.size[0]), int(img.size[1])
    except Exception:  # noqa: BLE001 - imagen ilegible
        return None


def tokens_imagen(ancho: int, alto: int, detalle: str) -> int:
    """Estimación de tokens de imagen con la fórmula de **DeepSeek**.

    DeepSeek no cobra por mosaicos como OpenAI: redimensiona siempre a un total
    de ~1300×1300 px (agrandando las chicas hasta un piso de ~544×544) y **tope
    duro de 1.024 tokens por imagen**. Es decir: para DeepSeek la resolución no
    cambia el costo de la imagen — una foto de 800×600 y una de 5000×5000 pagan
    lo mismo.

    ``detalle='low'`` la baja a 512×512 antes de inferir, pero **no** ahorra
    tokens (el redimensionado posterior la vuelve a llevar al objetivo): se
    devuelve el mismo valor y el llamador lo declara.

    Es una **estimación**; el número que manda es el ``usage`` de la API.
    """
    del ancho, alto, detalle  # el costo no depende de las dimensiones
    return TOKENS_MAX_IMAGEN


def info_imagen(ruta: Path, detalle: str) -> dict[str, Any]:
    """Metadatos de la imagen (peso, mime, dimensiones y tokens estimados).

    No lee el contenido: con ``stat`` alcanza para el peso y Pillow sólo mira el
    encabezado. Es lo que usa ``--dry-run``, que no necesita el base64.
    """
    info: dict[str, Any] = {
        "bytes": ruta.stat().st_size,
        "mime": MIME_POR_EXTENSION.get(ruta.suffix.lower(), "image/jpeg"),
        "detalle": detalle,
    }
    dims = _dimensiones(ruta)
    if dims:
        info["dimensiones"] = list(dims)
        info["tokens_estimados"] = tokens_imagen(dims[0], dims[1], detalle)
    return info


def codificar_imagen(ruta: Path, detalle: str) -> tuple[str, dict[str, Any]]:
    """Data URL base64 de la imagen + metadatos para el registro.

    Devuelve ``(data_url, info)``. El peso se informa aunque supere el límite de
    la API, para que el llamador pueda avisar en vez de mandar una petición
    condenada a fallar por una imagen demasiado grande.
    """
    info = info_imagen(ruta, detalle)
    b64 = base64.b64encode(ruta.read_bytes()).decode("ascii")
    return f"data:{info['mime']};base64,{b64}", info



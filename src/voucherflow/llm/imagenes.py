"""Imagen: codificación a data URL y estimación de sus tokens.

El token de la imagen no es gratis y **no se estima igual en todos los
proveedores**: unos cobran por fórmula de mosaicos según el tamaño, otros
redimensionan a un tope fijo y **agrandan** las chicas. Por eso cada proveedor
declara su ``estrategia_imagen`` en
:class:`~voucherflow.llm.protocolo.Capacidades` y acá vive el cálculo de cada
una, junto con la codificación.

El límite de peso se valida **antes** de mandar: una imagen que la API rechaza
gasta una llamada y devuelve un error que habla del payload, no del archivo.
"""

from __future__ import annotations

import base64
import math
from pathlib import Path
from typing import Any

from .config import MIME_POR_EXTENSION, TOKENS_MAX_IMAGEN
from .protocolo import ESTRATEGIA_MOSAICOS, Capacidades

# --------------------------------------------------------------------------- #
# Costo de la imagen: dos modelos de facturación distintos
# --------------------------------------------------------------------------- #

#: Lado de un mosaico (OpenAI cobra por bloques de 512×512).
PATCH_MOSAICO = 512

#: Techo que OpenAI aplica al lado mayor antes de contar mosaicos (2048×2048).
MOSAICO_LADO_MAXIMO = 2048

#: Objetivo del lado menor cuando la imagen es más grande que el techo (768).
MOSAICO_LADO_MINIMO = 768

#: Costo base de una imagen en OpenAI: el primero de los mosaicos.
MOSAICO_TOKENS_BASE = 85

#: Costo de cada mosaico adicional.
MOSAICO_TOKENS_POR_BLOQUE = 170

#: Costo plano de ``detail='low'`` (OpenAI no cuenta mosaicos ahí).
MOSAICO_TOKENS_DETALLE_BAJO = 85


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


def tokens_imagen(
    ancho: int, alto: int, detalle: str, capacidades: Capacidades | None = None
) -> int:
    """Tokens que consume una imagen, **según cómo la cobre el proveedor**.

    ⚠️ Los proveedores no lo calculan igual, y la diferencia es grande. Este
    módulo tenía una sola fórmula (la de DeepSeek) y el núcleo la aplicaba a
    todos: estimar una corrida de OpenAI daba de 0,9x a 12x de error.

    ``ESTRATEGIA_TOPE_FIJO`` (DeepSeek)
        Redimensiona toda imagen a ~1300×1300 —**agrandando** las chicas hasta un
        piso de ~544×544— y cobra siempre :data:`TOKENS_MAX_IMAGEN`. La resolución
        no cambia el costo: una foto de 800×600 y una de 5000×5000 pagan lo mismo.

    ``ESTRATEGIA_MOSAICOS`` (OpenAI)
        ``85 + 170 × mosaicos`` de 512×512, con dos recortes previos que **nunca
        agrandan**: el lado mayor a 2048 y el lado menor a 768 si supera ese
        valor. ``detail='low'`` es plano en 85 y **sí** ahorra (hasta 12x).

    Sin ``capacidades`` se asume el proveedor por defecto (DeepSeek), que es el
    comportamiento histórico. Es una **estimación**: el número que manda es el
    ``usage`` de la API.
    """
    if capacidades is None:
        return TOKENS_MAX_IMAGEN
    if capacidades.estrategia_imagen == ESTRATEGIA_MOSAICOS:
        return _tokens_mosaicos(ancho, alto, detalle)
    return capacidades.tokens_por_imagen


def _tokens_mosaicos(ancho: int, alto: int, detalle: str) -> int:
    """Fórmula de OpenAI: mosaicos de 512, con los dos recortes previos.

    Los recortes **solo achican**: una imagen chica no se agranda, así que cae en
    un único mosaico (255 tokens) en vez de los 85 planos de ``detail='low'``. Son
    dos caminos distintos del proveedor, no una escala.
    """
    if detalle == "low":
        return MOSAICO_TOKENS_DETALLE_BAJO
    if ancho <= 0 or alto <= 0:
        return MOSAICO_TOKENS_BASE

    # 1. El lado mayor no pasa de 2048.
    if max(ancho, alto) > MOSAICO_LADO_MAXIMO:
        escala = MOSAICO_LADO_MAXIMO / max(ancho, alto)
        ancho, alto = ancho * escala, alto * escala
    # 2. El lado menor baja hasta 768 (solo si lo supera).
    if min(ancho, alto) > MOSAICO_LADO_MINIMO:
        escala = MOSAICO_LADO_MINIMO / min(ancho, alto)
        ancho, alto = ancho * escala, alto * escala

    mosaicos = math.ceil(ancho / PATCH_MOSAICO) * math.ceil(alto / PATCH_MOSAICO)
    return MOSAICO_TOKENS_BASE + MOSAICO_TOKENS_POR_BLOQUE * mosaicos


def info_imagen(
    ruta: Path, detalle: str, capacidades: Capacidades | None = None
) -> dict[str, Any]:
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
        info["tokens_estimados"] = tokens_imagen(dims[0], dims[1], detalle, capacidades)
    return info


def codificar_imagen(
    ruta: Path, detalle: str, capacidades: Capacidades | None = None
) -> tuple[str, dict[str, Any]]:
    """Data URL base64 de la imagen + metadatos para el registro.

    Devuelve ``(data_url, info)``. El peso se informa aunque supere el límite de
    la API, para que el llamador pueda avisar en vez de mandar una petición
    condenada a fallar por una imagen demasiado grande.
    """
    info = info_imagen(ruta, detalle, capacidades)
    b64 = base64.b64encode(ruta.read_bytes()).decode("ascii")
    return f"data:{info['mime']};base64,{b64}", info



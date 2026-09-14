"""Medición y reencode de una imagen, sin agrandar nunca.

Dos backends: **Pillow** (por defecto, sin dependencias nuevas — ya está en el
entorno) y **ffmpeg** (reproduce el loop de shell original, ya corregido).

El loop base del que sale este módulo tenía cuatro defectos, y los tres primeros
son de *imagen*, por eso se corrigen acá:

1. **``scale=1024:-1`` agranda las imágenes chicas.** ffmpeg escala *siempre*:
   una captura de 600 px de ancho sale a 1024 px → más peso y **más tokens** que
   la original (lo contrario de lo buscado). Acá una imagen que ya entra en el
   objetivo **no se toca**.
2. **``scale=1024:-1`` fija el ANCHO, no el lado mayor.** En una foto vertical
   (p. ej. 3000x4000) el resultado es 1024x1365 ≈ 1.4 Mpx, cuando el mismo
   presupuesto de 1024 px sobre el **lado mayor** daría 768x1024 ≈ 0.79 Mpx
   (~45% menos tokens). Acá se reduce por lado mayor.
3. **Siempre escribe JPEG, incluso en ``.png``.** ``-q:v 5`` y la extensión de
   salida ``.png`` quedan desalineados (bytes JPEG en un archivo ``.png``). Acá
   el formato se elige por extensión (o se fuerza a JPEG con ``formato="jpg"``).

    find . -type f \\( -name "*.jpg" -o -name "*.png" -o -name "*.jpeg" \\) |
    while read -r img; do
        destino="var/processed/$(dirname "$img")"
        mkdir -p "$destino"
        ffmpeg -i "$img" -vf "scale=1024:-1" -q:v 5 "$destino/$(basename "$img")" -y
    done
"""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path


def medir(ruta: Path) -> tuple[int, int] | None:
    """Dimensiones ``(ancho, alto)`` de la imagen: Pillow y, si no, ``ffprobe``."""
    return _medir_con_pillow(ruta) or _medir_con_ffprobe(ruta)


def _medir_con_pillow(ruta: Path) -> tuple[int, int] | None:
    """Dimensiones (ancho, alto) de una imagen, respetando la orientación EXIF."""
    try:
        from PIL import Image, ImageOps
    except ImportError:
        return None
    try:
        with Image.open(ruta) as img:
            img = ImageOps.exif_transpose(img)
            ancho, alto = img.size
        return int(ancho), int(alto)
    except Exception:  # noqa: BLE001 - imagen ilegible/formatos raros
        return None


def _medir_con_ffprobe(ruta: Path) -> tuple[int, int] | None:
    """Fallback de medición vía ``ffprobe`` (si no hay Pillow)."""
    if not shutil.which("ffprobe"):
        return None
    try:
        salida = subprocess.run(
            [
                "ffprobe",
                "-v",
                "error",
                "-select_streams",
                "v:0",
                "-show_entries",
                "stream=width,height",
                "-of",
                "csv=p=0:s=x",
                str(ruta),
            ],
            capture_output=True,
            text=True,
            timeout=60,
        )
        ancho, alto = salida.stdout.strip().split("x")[:2]
        return int(ancho), int(alto)
    except Exception:  # noqa: BLE001
        return None


def extension_destino(origen: Path, formato: str) -> str:
    """Extensión del archivo de salida según ``formato`` (``mismo`` o ``jpg``)."""
    if formato == "jpg":
        return ".jpg"
    return origen.suffix.lower() or ".jpg"


def reducir(
    origen: Path,
    destino: Path,
    dims: tuple[int, int],
    *,
    backend: str,
    calidad: int,
) -> None:
    """Reduce ``origen`` a ``dims`` y lo escribe en ``destino``.

    ``dims`` ya viene calculado (ver :mod:`voucherflow.corpus.dimensiones`): acá
    no se decide nada de tamaño, solo se aplica. Escribe el destino tal cual, sin
    comprobar si existe — esa decisión (reanudar o forzar) es de la corrida.
    """
    if backend == "ffmpeg":
        _reducir_ffmpeg(origen, destino, dims, calidad)
    else:
        _reducir_pillow(origen, destino, dims, calidad)


def _reducir_pillow(
    origen: Path, destino: Path, dims: tuple[int, int], calidad: int
) -> None:
    """Reduce y reencoda con Pillow (backend por defecto)."""
    from PIL import Image, ImageOps

    with Image.open(origen) as img:
        img = ImageOps.exif_transpose(img)  # respeta orientación EXIF
        img = img.convert("RGB")  # PNG con alfa / paleta → RGB para JPEG
        reducida = img.resize(dims, Image.Resampling.LANCZOS)
        if origen.suffix.lower() == ".png" and destino.suffix.lower() == ".png":
            reducida.save(destino, "PNG", optimize=True)
        else:
            reducida.save(destino, "JPEG", quality=calidad, optimize=True)


def _reducir_ffmpeg(
    origen: Path, destino: Path, dims: tuple[int, int], calidad: int
) -> None:
    """Reduce con ffmpeg (backend alternativo; sin agrandar, dims explícitas).

    Se pasan las dimensiones ya calculadas (``scale=W:H``) en vez de la
    expresión ``scale=1024:-1`` del loop base: así el lado mayor es el que se
    respeta y no hay que escapar expresiones dentro del filtro.
    """
    problema = ffmpeg_no_disponible()
    if problema is not None:
        raise RuntimeError(problema)
    # Equivalencia aproximada entre la calidad 0-100 (JPEG de Pillow) y la
    # escala qscale de ffmpeg (2 = mejor, 31 = peor).
    q = max(2, min(31, round(31 - (calidad / 100) * 29)))
    proc = subprocess.run(
        [
            "ffmpeg",
            "-nostdin",
            "-loglevel",
            "error",
            "-y",
            "-i",
            str(origen),
            "-vf",
            f"scale={dims[0]}:{dims[1]}",
            "-q:v",
            str(q),
            str(destino),
        ],
        capture_output=True,
        text=True,
        timeout=300,
    )
    if proc.returncode != 0:
        raise RuntimeError(
            f"ffmpeg falló (código {proc.returncode}): {recortar(proc.stderr)}"
        )


#: Resultado cacheado del chequeo de ffmpeg (``False`` = todavía sin chequear).
_FFMPEG_CACHE: dict[str, str | None] = {}


def ffmpeg_no_disponible() -> str | None:
    """Chequeo **una sola vez** de que ffmpeg exista y arranque.

    En macOS es común que ``which ffmpeg`` lo encuentre pero el binario no
    arranque por una dependencia rota de Homebrew (p. ej. ``libx265`` movida de
    versión). Sin este preflight, ese fallo se replica como un volcado de dyld
    por cada archivo. Devuelve el motivo (str) o ``None`` si está sano.
    """
    if "motivo" in _FFMPEG_CACHE:
        return _FFMPEG_CACHE["motivo"]
    motivo: str | None = None
    if not shutil.which("ffmpeg"):
        motivo = "ffmpeg no está en el PATH (usá backend pillow)"
    else:
        try:
            proc = subprocess.run(
                ["ffmpeg", "-version"], capture_output=True, text=True, timeout=30
            )
            if proc.returncode != 0:
                motivo = (
                    "ffmpeg está en el PATH pero no arranca: "
                    f"{recortar(proc.stderr)} — usá backend pillow"
                )
        except Exception as exc:  # noqa: BLE001
            motivo = f"no se pudo ejecutar ffmpeg: {exc} — usá backend pillow"
    _FFMPEG_CACHE["motivo"] = motivo
    return motivo


def _resetear_cache_ffmpeg() -> None:
    """Olvida el resultado del preflight (para tests)."""
    _FFMPEG_CACHE.clear()


def recortar(texto: str | None, limite: int = 240) -> str:
    """Primera línea no vacía de la salida de un proceso, recortada a ``limite``."""
    linea = next(
        (linea.strip() for linea in (texto or "").splitlines() if linea.strip()),
        "sin detalle",
    )
    return f"{linea[:limite]}…" if len(linea) > limite else linea

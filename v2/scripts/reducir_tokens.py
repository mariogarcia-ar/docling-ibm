#!/usr/bin/env python
"""Reduce peso y tokens de las imágenes de un corpus (pre-procesamiento barato).

Recorre las imágenes de las rutas indicadas, las reduce a un **lado mayor
objetivo** (por defecto 1024 px, sin agrandar nunca) y las reencoda con calidad
moderada, replicando el árbol de carpetas bajo una carpeta de salida
(``procesadas/`` por defecto). El objetivo es **bajar el costo de
procesamiento**: menos bytes en disco/red y, sobre todo, **menos tokens de
visión** si después se le pasan a un VLM (qwen2.5vl, docling VLM, etc.).

Porta y corrige el loop de shell original:

    find . -type f \\( -name "*.jpg" -o -name "*.png" -o -name "*.jpeg" \\) |
    while read -r img; do
        destino="./procesadas/$(dirname "$img")"
        mkdir -p "$destino"
        ffmpeg -i "$img" -vf "scale=1024:-1" -q:v 5 "$destino/$(basename "$img")" -y
    done

Defectos del loop base que este script corrige (y conviene tener presentes):

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
   el formato se elige por extensión (o se fuerza con ``--formato jpg``).
4. **Sin paralelismo, sin reanudación y sin control de re-ejecución.** Acá hay
   ``--workers``, se saltea el destino ya existente (reanudable) y hay reporte.

**Alineación al VLM (el punto fino).** Cuando el objetivo es un modelo de
visión, las dimensiones no dan igual: el preprocesador de Qwen2.5-VL
(``smart_resize``) redondea a múltiplos de 28 (``patch_size=14`` ×
``merge_size=2``) y **re-escalea** si no coincide, con lo que el conteo de
tokens deja de ser predecible. Este script calcula las dimensiones con
``voucherflow.validation.prompt_qween.dimensiones_objetivo_vlm`` — el **mismo**
``smart_resize`` que usa el pipeline v2 (F2/T-202/T-203) — para que el servidor
no re-escale. Si la librería no se puede importar, cae a una alineación local
equivalente (múltiplos de 28). Los defaults (1024 px / calidad 80 / piso de lado
menor 256) también se toman de la librería si está disponible: **una sola fuente
de verdad** para el presupuesto de la vista de revisión (E-QWE-2).

**Tokens estimados.** El reporte estima los tokens de visión como
``ceil(ancho/28) * ceil(alto/28)``, que es el gridding de Qwen2.5-VL para
imágenes ya alineadas. Es una **estimación** (el overhead del template y del
texto del prompt no se cuenta), útil para comparar antes/después, no una
medición del servidor.

⚠️ **Ojo con el OCR.** Reducir *antes* de un OCR clásico (RapidOCR/EasyOCR vía
Docling, ``v1/ocr_documents.py``) puede degradar la letra chica: el OCR lee
píxeles. Bajá la reducción (``--lado-mayor 1536`` o ``2048``) o usá
``--solo-medir`` para decidir antes de tocar el corpus. Para el camino **VLM**
(la imagen viaja al modelo) reducir es lo correcto: v2 ya lo hace al enviar
(``_bytes_imagen_para_envio``, vista rápida 512 / revisión 1024), así que este
script sirve para **pre-reducir el corpus en disco** (almacenamiento, OCR más
rápido, o alimentar otra herramienta).

Uso:
    python scripts/reducir_tokens.py <ruta|carpeta>... [opciones]

Ejemplos:
    # Medir sin escribir nada (siempre conviene empezar acá)
    python scripts/reducir_tokens.py ../files --solo-medir

    # Probar con 20 imágenes y ver el detalle
    python scripts/reducir_tokens.py ../files --limite 20 --detalle

    # Correr el corpus completo, 4 workers, reporte JSON
    python scripts/reducir_tokens.py ../files --workers 4 --reporte reporte.json

    # Backend ffmpeg (reproduce el loop base, ya corregido)
    python scripts/reducir_tokens.py ../files/2025-08 --backend ffmpeg

    # Correr de nuevo para reanudar (saltea destinos ya escritos)
    python scripts/reducir_tokens.py ../files --workers 4

Códigos de salida: 0 = todo ok (o nada que hacer); 1 = hubo fallos;
2 = error de uso; 130 = interrumpido (Ctrl-C).
"""

from __future__ import annotations

import argparse
import json
import math
import os
import shutil
import subprocess
import sys
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from pathlib import Path
from typing import Sequence

# El paquete usa layout src/; si se corre sin instalar, se agrega src/ al path
# (misma convención que el resto de scripts/).
_SRC = Path(__file__).resolve().parents[1] / "src"
if str(_SRC) not in sys.path:
    sys.path.insert(0, str(_SRC))

#: Extensiones de imagen por defecto (las mismas del loop base).
EXTENSIONES_POR_DEFECTO = frozenset({".jpg", ".jpeg", ".png"})

# --- Defaults (una sola fuente de verdad: la librería v2 si se puede importar) --

#: Piso del lado menor: evita perder detalle en imágenes muy alargadas.
LADO_MENOR_MINIMO_PX = 256
#: Factor de patch*merge de Qwen2.5-VL (``patch_size=14`` × ``merge_size=2``).
FACTOR_PATCH_QWEN2VL = 28
#: Lado mayor objetivo por defecto: la resolución de la **vista de revisión**
#: de la librería (E-QWE-2).
LADO_MAYOR_PX = 1024
#: Calidad de reencode por defecto.
CALIDAD = 80
_ORIGEN_DEFAULTS = "valores locales del script (librería v2 no importable)"

#: ``dimensiones_objetivo_vlm`` de la librería, si está disponible (reutiliza el
#: ``smart_resize`` de Qwen2.5-VL que Docling aplica en su pipeline VLM).
_dimensiones_libreria = None

try:  # pragma: no cover - depende del entorno de importación
    from voucherflow.validation.prompt_qween import (  # noqa: E402
        CALIDAD_JPEG_ENVIO,
        FACTOR_PATCH_QWEN2VL as _FACTOR_LIB,
        LADO_MENOR_MINIMO_ENVIO_PX,
        RESOLUCION_VISTA_REVISION_PX,
        dimensiones_objetivo_vlm,
    )

    LADO_MENOR_MINIMO_PX = LADO_MENOR_MINIMO_ENVIO_PX
    FACTOR_PATCH_QWEN2VL = _FACTOR_LIB
    LADO_MAYOR_PX = RESOLUCION_VISTA_REVISION_PX
    CALIDAD = CALIDAD_JPEG_ENVIO
    _dimensiones_libreria = dimensiones_objetivo_vlm
    _ORIGEN_DEFAULTS = "librería v2 (voucherflow.validation.prompt_qween)"
except Exception:  # noqa: BLE001 - script operativo: no debe romper al importar
    pass


# --------------------------------------------------------------------------- #
# Cálculo de dimensiones y estimación de tokens
# --------------------------------------------------------------------------- #


def dimensiones_objetivo(
    ancho: int,
    alto: int,
    lado_mayor: int,
    *,
    piso_lado_menor: int = LADO_MENOR_MINIMO_PX,
    alinear: bool = True,
) -> tuple[int, int]:
    """Dimensiones destino para una imagen de ``ancho`` x ``alto``.

    Nunca agranda: si la imagen ya entra en ``lado_mayor`` devuelve sus
    dimensiones originales. Reutiliza ``dimensiones_objetivo_vlm`` de la
    librería (mismo ``smart_resize`` de Qwen2.5-VL) y, si no está disponible,
    cae a una alineación local a múltiplos de :data:`FACTOR_PATCH_QWEN2VL`.
    """
    if lado_mayor <= 0:
        return ancho, alto
    lado_mayor_real = max(ancho, alto)
    if lado_mayor_real <= lado_mayor:
        return ancho, alto

    if _dimensiones_libreria is not None:
        try:
            nuevo = _dimensiones_libreria(
                ancho, alto, lado_mayor, lado_menor_minimo=piso_lado_menor
            )
            n_ancho, n_alto = int(nuevo[0]), int(nuevo[1])
            if 0 < n_ancho <= ancho and 0 < n_alto <= alto:
                return n_ancho, n_alto
        except Exception:  # noqa: BLE001 - API semi-interna de Docling
            pass

    return _dimensiones_local(
        ancho, alto, lado_mayor, piso_lado_menor, alinear=alinear
    )


def _dimensiones_local(
    ancho: int,
    alto: int,
    lado_mayor: int,
    piso_lado_menor: int,
    *,
    alinear: bool = True,
) -> tuple[int, int]:
    """Alineación local (fallback): reduce por lado mayor, sin agrandar.

    Respeta el piso del lado menor subiendo el ``max_size`` efectivo en
    imágenes muy alargadas (nunca por encima del tamaño original).
    """
    lado_mayor_real = max(ancho, alto)
    lado_menor_real = min(ancho, alto)
    max_size = lado_mayor
    if lado_menor_real >= piso_lado_menor and lado_mayor_real:
        requerido = math.ceil(piso_lado_menor * lado_mayor_real / lado_menor_real)
        if requerido > max_size:
            max_size = min(requerido, lado_mayor_real)

    escala = min(1.0, max_size / lado_mayor_real) if lado_mayor_real else 1.0
    n_ancho = max(1, round(ancho * escala))
    n_alto = max(1, round(alto * escala))
    if alinear:
        f = FACTOR_PATCH_QWEN2VL
        n_ancho = max(f, round(n_ancho / f) * f)
        n_alto = max(f, round(n_alto / f) * f)
        # La alineación podría haber agrandado respecto del original.
        n_ancho, n_alto = min(n_ancho, ancho), min(n_alto, alto)
    return n_ancho, n_alto


def tokens_estimados_vlm(ancho: int, alto: int) -> int:
    """Tokens de visión estimados para Qwen2.5-VL con la imagen ya alineada.

    ``gridding`` de ``patch_size=14`` con ``merge_size=2`` ⇒ cada token cubre
    28x28 px de la imagen alineada. Es una **estimación** de comparación
    (no incluye el template ni el texto del prompt).
    """
    return math.ceil(ancho / FACTOR_PATCH_QWEN2VL) * math.ceil(
        alto / FACTOR_PATCH_QWEN2VL
    )


# --------------------------------------------------------------------------- #
# Modelo de resultados
# --------------------------------------------------------------------------- #


@dataclass
class Opciones:
    """Opciones efectivas de una corrida."""

    salida: Path
    lado_mayor: int
    calidad: int
    piso_lado_menor: int
    alinear: bool
    backend: str
    formato: str
    forzar: bool
    escribir: bool
    copiar_no_reducidas: bool
    workers: int
    detalle: bool


@dataclass
class Resultado:
    """Resultado por archivo (exitosa, omitida o fallida)."""

    origen: Path
    destino: Path | None
    estado: str  # reducido | omitido | fallo
    motivo: str = ""
    dims_origen: tuple[int, int] | None = None
    dims_destino: tuple[int, int] | None = None
    peso_origen: int | None = None
    peso_destino: int | None = None

    @property
    def ok(self) -> bool:
        return self.estado != "fallo"

    def a_dict(self) -> dict:
        return {
            "origen": str(self.origen),
            "destino": str(self.destino) if self.destino else None,
            "estado": self.estado,
            "motivo": self.motivo,
            "dims_origen": list(self.dims_origen) if self.dims_origen else None,
            "dims_destino": list(self.dims_destino) if self.dims_destino else None,
            "peso_origen_bytes": self.peso_origen,
            "peso_destino_bytes": self.peso_destino,
            "tokens_origen": (
                tokens_estimados_vlm(*self.dims_origen) if self.dims_origen else None
            ),
            "tokens_destino": (
                tokens_estimados_vlm(*self.dims_destino) if self.dims_destino else None
            ),
        }


# --------------------------------------------------------------------------- #
# Medición y reducción
# --------------------------------------------------------------------------- #


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


def medir(ruta: Path) -> tuple[int, int] | None:
    """Dimensiones de la imagen: Pillow y, si no está, ``ffprobe``."""
    return _medir_con_pillow(ruta) or _medir_con_ffprobe(ruta)


def _extension_destino(origen: Path, formato: str) -> str:
    """Extensión del archivo de salida según ``--formato``."""
    if formato == "jpg":
        return ".jpg"
    return origen.suffix.lower() or ".jpg"


def _reducir_pillow(
    origen: Path, destino: Path, dims: tuple[int, int], opciones: Opciones
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
            reducida.save(
                destino, "JPEG", quality=opciones.calidad, optimize=True
            )


def _reducir_ffmpeg(
    origen: Path, destino: Path, dims: tuple[int, int], opciones: Opciones
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
    q = max(2, min(31, round(31 - (opciones.calidad / 100) * 29)))
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
            f"ffmpeg falló (código {proc.returncode}): {_recortar(proc.stderr)}"
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
        motivo = "ffmpeg no está en el PATH (usá --backend pillow)"
    else:
        try:
            proc = subprocess.run(
                ["ffmpeg", "-version"], capture_output=True, text=True, timeout=30
            )
            if proc.returncode != 0:
                motivo = (
                    "ffmpeg está en el PATH pero no arranca: "
                    f"{_recortar(proc.stderr)} — usá --backend pillow"
                )
        except Exception as exc:  # noqa: BLE001
            motivo = f"no se pudo ejecutar ffmpeg: {exc} — usá --backend pillow"
    _FFMPEG_CACHE["motivo"] = motivo
    return motivo


def _recortar(texto: str | None, limite: int = 240) -> str:
    """Primera línea no vacía de la salida de un proceso, recortada a ``limite``."""
    linea = next(
        (l.strip() for l in (texto or "").splitlines() if l.strip()), "sin detalle"
    )
    return f"{linea[:limite]}…" if len(linea) > limite else linea


def procesar(origen: Path, destino: Path, opciones: Opciones) -> Resultado:
    """Procesa una imagen: mide, decide (reducir/omitir/copiar) y escribe."""
    try:
        peso_origen = origen.stat().st_size
    except OSError as exc:
        return Resultado(origen, None, "fallo", f"no se pudo leer: {exc}")

    dims_origen = medir(origen)
    if dims_origen is None:
        return Resultado(
            origen,
            None,
            "fallo",
            "no se pudieron leer las dimensiones (imagen ilegible)",
            peso_origen=peso_origen,
        )

    # Seguridad: nunca escribir sobre la entrada (lección de T-604 para `process`).
    if destino.resolve() == origen.resolve():
        return Resultado(
            origen,
            destino,
            "omitido",
            "el destino coincide con el origen; se evita sobrescribir la entrada",
            dims_origen=dims_origen,
            peso_origen=peso_origen,
        )

    dims_destino = dimensiones_objetivo(
        *dims_origen,
        opciones.lado_mayor,
        piso_lado_menor=opciones.piso_lado_menor,
        alinear=opciones.alinear,
    )

    if dims_destino == dims_origen:
        # Ya entra en el objetivo: NO se toca (el loop base la agrandaba).
        if not opciones.copiar_no_reducidas:
            return Resultado(
                origen,
                destino,
                "omitido",
                f"{dims_origen[0]}x{dims_origen[1]}px ya es <= {opciones.lado_mayor}px",
                dims_origen=dims_origen,
                dims_destino=dims_origen,
                peso_origen=peso_origen,
                peso_destino=None if not opciones.escribir else peso_origen,
            )
        if not opciones.escribir:
            return Resultado(
                origen,
                destino,
                "omitido",
                "se copiaría sin reducir",
                dims_origen=dims_origen,
                dims_destino=dims_origen,
                peso_origen=peso_origen,
            )
        try:
            ya_existe = destino.exists() and not opciones.forzar
            if not ya_existe:
                destino.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(origen, destino)
        except OSError as exc:
            return Resultado(origen, destino, "fallo", f"no se pudo copiar: {exc}")
        return Resultado(
            origen,
            destino,
            "omitido",
            (
                "copia ya existente (usar --forzar para reescribir)"
                if ya_existe
                else "copiada sin reducir (ya entra en el objetivo)"
            ),
            dims_origen=dims_origen,
            dims_destino=dims_origen,
            peso_origen=peso_origen,
            peso_destino=(
                destino.stat().st_size if destino.exists() else None
            ),
        )

    if destino.exists() and not opciones.forzar:
        # Reanudación: el destino ya está. NO se asume que tenga las
        # dimensiones calculadas (pudo generarse con otros parámetros) → se
        # mide el archivo real para que el reporte no invente el resultado.
        dims_reales = medir(destino)
        return Resultado(
            origen,
            destino,
            "omitido",
            "el destino ya existe (usar --forzar para reescribir)",
            dims_origen=dims_origen,
            dims_destino=dims_reales,
            peso_origen=peso_origen,
            peso_destino=destino.stat().st_size,
        )

    if not opciones.escribir:
        return Resultado(
            origen,
            destino,
            "reducido",
            "simulación (--solo-medir): no se escribió",
            dims_origen=dims_origen,
            dims_destino=dims_destino,
            peso_origen=peso_origen,
        )

    try:
        destino.parent.mkdir(parents=True, exist_ok=True)
        if opciones.backend == "ffmpeg":
            _reducir_ffmpeg(origen, destino, dims_destino, opciones)
        else:
            _reducir_pillow(origen, destino, dims_destino, opciones)
        peso_destino = destino.stat().st_size
    except Exception as exc:  # noqa: BLE001 - un archivo malo no corta el lote
        return Resultado(
            origen,
            destino,
            "fallo",
            f"{type(exc).__name__}: {exc}",
            dims_origen=dims_origen,
            dims_destino=dims_destino,
            peso_origen=peso_origen,
        )

    return Resultado(
        origen,
        destino,
        "reducido",
        "",
        dims_origen=dims_origen,
        dims_destino=dims_destino,
        peso_origen=peso_origen,
        peso_destino=peso_destino,
    )


# --------------------------------------------------------------------------- #
# Recorrido de archivos
# --------------------------------------------------------------------------- #


def _expandir(rutas: Sequence[Path], extensiones: frozenset[str]) -> list[Path]:
    """Expande archivos/carpetas a la lista de imágenes, en orden determinista."""
    encontrados: list[Path] = []
    for ruta in rutas:
        if ruta.is_dir():
            encontrados.extend(
                q
                for q in sorted(ruta.rglob("*"))
                if q.is_file() and q.suffix.lower() in extensiones
            )
        elif ruta.is_file():
            if ruta.suffix.lower() in extensiones:
                encontrados.append(ruta)
            else:
                print(
                    f"⚠  Se ignora (extensión fuera de {sorted(extensiones)}): {ruta}",
                    file=sys.stderr,
                )
        else:
            print(f"⚠  Se ignora (no existe): {ruta}", file=sys.stderr)
    return sorted(set(encontrados))


def _raiz_comun(rutas: Sequence[Path]) -> Path:
    """Raíz desde la cual se espeja el árbol en la carpeta de salida.

    Las carpetas se toman tal cual y los archivos sueltos por su carpeta padre;
    con eso ``python scripts/reducir_tokens.py ../files`` escribe
    ``procesadas/2025-08/<hash>/img.jpg`` (sin repetir el nivel ``files/``).
    """
    bases = [r if r.is_dir() else r.parent for r in rutas if r.exists()]
    if not bases:
        return Path.cwd()
    if len(bases) == 1:
        return bases[0]
    try:
        return Path(os.path.commonpath([str(b.resolve()) for b in bases]))
    except ValueError:  # rutas sin ancestro común (volúmenes distintos)
        return Path.cwd()


def _esta_dentro(hijo: Path, padre: Path) -> bool:
    """True si ``hijo`` está dentro de ``padre`` (evita reprocesar la salida)."""
    try:
        hijo.resolve().relative_to(padre.resolve())
        return True
    except (ValueError, OSError):
        return False


# --------------------------------------------------------------------------- #
# Reporte
# --------------------------------------------------------------------------- #


def _formato_bytes(n: int | None) -> str:
    """Bytes legibles (B/KiB/MiB/GiB)."""
    if n is None:
        return "—"
    valor = float(n)
    for unidad in ("B", "KiB", "MiB", "GiB"):
        if valor < 1024 or unidad == "GiB":
            return f"{valor:.1f} {unidad}" if unidad != "B" else f"{int(valor)} B"
        valor /= 1024
    return f"{valor:.1f} GiB"


def _valor(r: Resultado, campo: str, *, origen: bool) -> int | None:
    """``peso_*`` o ``tokens_*`` de un resultado (``None`` si no se midió)."""
    if campo == "peso":
        return r.peso_origen if origen else r.peso_destino
    dims = r.dims_origen if origen else r.dims_destino
    return tokens_estimados_vlm(*dims) if dims else None


def _sumatoria(resultados: Sequence[Resultado], campo: str, *, origen: bool) -> int:
    """Suma ``peso_*`` o ``tokens_*`` de los resultados, ignorando ``None``."""
    return sum(
        valor
        for valor in (_valor(r, campo, origen=origen) for r in resultados)
        if valor
    )


def _comparar(
    resultados: Sequence[Resultado], campo: str
) -> tuple[int, int, float | None]:
    """Compara antes/después **solo donde hay medición de destino**.

    En ``--solo-medir`` el peso destino no existe (no se escribió): comparar
    contra un 0 daría un «-100%» inventado. Los tokens sí se pueden comparar
    siempre que las dos dimensiones se hayan podido calcular.

    Devuelve ``(antes, despues, reduccion_pct | None)``.
    """
    pares = [
        (_valor(r, campo, origen=True), _valor(r, campo, origen=False))
        for r in resultados
    ]
    medidos = [(a, d) for a, d in pares if a and d is not None]
    antes = sum(a for a, _ in medidos)
    despues = sum(d for _, d in medidos)
    if not antes:
        return 0, 0, None
    return antes, despues, round((1 - despues / antes) * 100, 1)


def resumen(resultados: Sequence[Resultado], opciones: Opciones) -> dict:
    """Resumen agregado de la corrida (dict, serializable a JSON).

    ``peso_origen_bytes`` es el total de **todo** lo recorrido; los porcentajes
    de reducción se calculan solo sobre los pares medidos (ver :func:`_comparar`)
    para no reportar una reducción que no se midió.
    """
    por_estado: dict[str, int] = {}
    for r in resultados:
        por_estado[r.estado] = por_estado.get(r.estado, 0) + 1

    peso_o_cmp, peso_d_cmp, pct_peso = _comparar(resultados, "peso")
    tok_o, tok_d, pct_tok = _comparar(resultados, "tokens")

    return {
        "archivos": len(resultados),
        "reducidos": por_estado.get("reducido", 0),
        "omitidos": por_estado.get("omitido", 0),
        "fallos": por_estado.get("fallo", 0),
        "peso_origen_bytes": _sumatoria(resultados, "peso", origen=True),
        "peso_destino_bytes": _sumatoria(resultados, "peso", origen=False),
        "peso_comparado_origen_bytes": peso_o_cmp,
        "peso_comparado_destino_bytes": peso_d_cmp,
        "reduccion_peso_pct": pct_peso,
        "tokens_origen_estimados": _sumatoria(resultados, "tokens", origen=True),
        "tokens_destino_estimados": _sumatoria(resultados, "tokens", origen=False),
        "tokens_comparados_origen": tok_o,
        "tokens_comparados_destino": tok_d,
        "reduccion_tokens_pct": pct_tok,
        "opciones": {
            "salida": str(opciones.salida),
            "lado_mayor_px": opciones.lado_mayor,
            "calidad": opciones.calidad,
            "piso_lado_menor_px": opciones.piso_lado_menor,
            "alinear_patch_qwen2vl": opciones.alinear,
            "backend": opciones.backend,
            "formato": opciones.formato,
            "solo_medir": not opciones.escribir,
            "simulacion": not opciones.escribir,
        },
        "defaults_desde": _ORIGEN_DEFAULTS,
    }


def _imprimir_resumen(rep: dict, resultados: Sequence[Resultado]) -> None:
    """Imprime el resumen en stdout (los fallos van a stderr)."""
    print("\n=== Resumen ===")
    print(f"archivos                  : {rep['archivos']}")
    print(f"  reducidos               : {rep['reducidos']}")
    print(f"  omitidos                : {rep['omitidos']}")
    print(f"  fallos                  : {rep['fallos']}")

    # Si no hubo medición de destino (simulación), se informa el total de
    # origen y se aclara que la reducción de peso NO se midió.
    pct_peso = rep["reduccion_peso_pct"]
    if pct_peso is None:
        print(
            f"peso                      : "
            f"{_formato_bytes(rep['peso_origen_bytes'])}"
            "  →  (no medido: simulación)"
        )
    else:
        print(
            f"peso                      : {_formato_bytes(rep['peso_origen_bytes'])}"
            f"  →  {_formato_bytes(rep['peso_destino_bytes'])}"
            f"   (-{pct_peso}%)"
        )

    if rep["tokens_origen_estimados"]:
        pct_tok = rep["reduccion_tokens_pct"]
        sufijo = f"   (-{pct_tok}%)" if pct_tok is not None else ""
        print(
            f"tokens de visión (estim.) : {rep['tokens_origen_estimados']:,}"
            f"  →  {rep['tokens_destino_estimados']:,}{sufijo}"
        )
    print(
        f"backend / defaults        : {rep['opciones']['backend']} / "
        f"{rep['defaults_desde']}"
    )
    if rep["opciones"]["solo_medir"]:
        print("modo                      : --solo-medir (no se escribió nada)")

    fallos = [r for r in resultados if r.estado == "fallo"]
    if fallos:
        print(f"\n--- Fallos ({len(fallos)}) ---", file=sys.stderr)
        for r in fallos[:20]:
            print(f"  {r.origen}: {r.motivo}", file=sys.stderr)
        if len(fallos) > 20:
            print(f"  … y {len(fallos) - 20} más", file=sys.stderr)


# --------------------------------------------------------------------------- #
# CLI
# --------------------------------------------------------------------------- #


def construir_parser() -> argparse.ArgumentParser:
    """Arma el parser de la CLI (``argparse`` de la stdlib, como el resto del repo)."""
    parser = argparse.ArgumentParser(
        prog="reducir_tokens",
        description=(
            "Reduce el lado mayor y el peso de las imágenes de un corpus, "
            "espejando la estructura de carpetas en la salida."
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=(
            "Ejemplos:\n"
            "  python scripts/reducir_tokens.py ../files --solo-medir\n"
            "  python scripts/reducir_tokens.py ../files --limite 20 --detalle\n"
            "  python scripts/reducir_tokens.py ../files --workers 4 --reporte rep.json\n"
        ),
    )
    parser.add_argument(
        "rutas",
        nargs="+",
        help="Archivos y/o carpetas a procesar (las carpetas se recorren recursivo).",
    )
    parser.add_argument(
        "-o",
        "--salida",
        default="procesadas",
        help="Carpeta raíz de salida (default: procesadas).",
    )
    parser.add_argument(
        "--lado-mayor",
        type=int,
        default=LADO_MAYOR_PX,
        metavar="PX",
        help=(
            "Lado mayor objetivo en px (default: %(default)s, la vista de "
            "revisión de la librería). Nunca agranda."
        ),
    )
    parser.add_argument(
        "--calidad",
        type=int,
        default=CALIDAD,
        metavar="1-100",
        help="Calidad de reencode (default: %(default)s).",
    )
    parser.add_argument(
        "--piso-lado-menor",
        type=int,
        default=LADO_MENOR_MINIMO_PX,
        metavar="PX",
        help=(
            "Piso del lado menor en px, para imágenes muy alargadas "
            "(default: %(default)s)."
        ),
    )
    parser.add_argument(
        "--sin-alinear",
        dest="alinear",
        action="store_false",
        help=(
            "No alinear las dimensiones a múltiplos de "
            f"{FACTOR_PATCH_QWEN2VL} (desactivá solo si el destino NO es "
            "Qwen2.5-VL: la alineación hace predecible el conteo de tokens)."
        ),
    )
    parser.add_argument(
        "--backend",
        choices=("pillow", "ffmpeg"),
        default="pillow",
        help=(
            "Motor de reencode (default: pillow). ffmpeg reproduce el loop "
            "base, ya corregido."
        ),
    )
    parser.add_argument(
        "--formato",
        choices=("mismo", "jpg"),
        default="mismo",
        help=(
            "Formato de salida (default: mismo). «mismo» conserva la "
            "extensión; «jpg» fuerza JPEG y reescribe la extensión a .jpg."
        ),
    )
    parser.add_argument(
        "--extensiones",
        default=",".join(sorted(EXTENSIONES_POR_DEFECTO)),
        metavar="LISTA",
        help=(
            "Extensiones a procesar, separadas por coma "
            "(default: %(default)s)."
        ),
    )
    parser.add_argument(
        "--forzar",
        action="store_true",
        help="Reescribe el destino aunque ya exista (sin esto, reanuda).",
    )
    parser.add_argument(
        "--copiar-no-reducidas",
        action="store_true",
        help=(
            "Copia sin tocar las imágenes que ya entran en el objetivo, para "
            "que la salida quede completa (default: se omiten)."
        ),
    )
    parser.add_argument(
        "--solo-medir",
        action="store_true",
        help="No escribe nada: solo mide y reporta qué se reduciría.",
    )
    parser.add_argument(
        "--workers",
        type=int,
        default=4,
        metavar="N",
        help="Procesos concurrentes (default: %(default)s). Usá 1 si el equipo se calienta.",
    )
    parser.add_argument(
        "--limite",
        type=int,
        default=0,
        metavar="N",
        help="Procesa solo las primeras N imágenes (0 = todas). Útil para probar.",
    )
    parser.add_argument(
        "--detalle",
        action="store_true",
        help="Imprime una línea por archivo (a stderr).",
    )
    parser.add_argument(
        "--reporte",
        metavar="ARCHIVO.json",
        help="Escribe el reporte completo (resumen + por archivo) en JSON.",
    )
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    """Punto de entrada. Devuelve el código de salida (no llama a ``sys.exit``)."""
    args = construir_parser().parse_args(argv)

    if args.lado_mayor <= 0:
        print("error: --lado-mayor debe ser > 0", file=sys.stderr)
        return 2
    if not 1 <= args.calidad <= 100:
        print("error: --calidad debe estar entre 1 y 100", file=sys.stderr)
        return 2
    if args.workers < 1:
        print("error: --workers debe ser >= 1", file=sys.stderr)
        return 2

    extensiones = frozenset(
        e if e.startswith(".") else f".{e}"
        for e in (p.strip().lower() for p in args.extensiones.split(","))
        if e
    )
    if not extensiones:
        print("error: --extensiones quedó vacío", file=sys.stderr)
        return 2

    rutas = [Path(r) for r in args.rutas]
    salida = Path(args.salida)

    # Preflight del backend: avisar ANTES de recorrer el corpus (y una sola vez)
    # en vez de replicar el mismo error por archivo.
    if args.backend == "ffmpeg":
        problema = ffmpeg_no_disponible()
        if problema is not None:
            print(f"error: {problema}", file=sys.stderr)
            return 2

    opciones = Opciones(
        salida=salida,
        lado_mayor=args.lado_mayor,
        calidad=args.calidad,
        piso_lado_menor=args.piso_lado_menor,
        alinear=args.alinear,
        backend=args.backend,
        formato=args.formato,
        forzar=args.forzar,
        escribir=not args.solo_medir,
        copiar_no_reducidas=args.copiar_no_reducidas,
        workers=args.workers,
        detalle=args.detalle,
    )

    raiz = _raiz_comun(rutas)
    imagenes = [
        img for img in _expandir(rutas, extensiones) if not _esta_dentro(img, salida)
    ]
    if args.limite > 0:
        imagenes = imagenes[: args.limite]

    if not imagenes:
        print("No hay imágenes que procesar.", file=sys.stderr)
        return 0

    print(
        f"raíz de espejado : {raiz}\n"
        f"salida           : {salida}\n"
        f"imágenes         : {len(imagenes)}\n"
        f"objetivo         : lado mayor <= {opciones.lado_mayor}px, "
        f"calidad {opciones.calidad}, backend {opciones.backend}",
        file=sys.stderr,
    )

    tareas: list[tuple[Path, Path]] = []
    for img in imagenes:
        try:
            relativo = img.resolve().relative_to(raiz.resolve())
        except (ValueError, OSError):
            relativo = Path(img.name)
        relativo = relativo.with_suffix(
            _extension_destino(img, opciones.formato)
        )
        tareas.append((img, salida / relativo))

    resultados: list[Resultado] = []
    try:
        if opciones.workers == 1:
            for origen, destino in tareas:
                resultados.append(procesar(origen, destino, opciones))
                if opciones.detalle:
                    _imprimir_detalle(resultados[-1])
        else:
            with ThreadPoolExecutor(max_workers=opciones.workers) as pool:
                futuros = [
                    pool.submit(procesar, origen, destino, opciones)
                    for origen, destino in tareas
                ]
                for i, futuro in enumerate(futuros, 1):
                    resultado = futuro.result()
                    resultados.append(resultado)
                    if opciones.detalle:
                        _imprimir_detalle(resultado)
                    elif i % 250 == 0:
                        print(f"  … {i}/{len(tareas)}", file=sys.stderr)
    except KeyboardInterrupt:
        print("\nInterrumpido por el usuario.", file=sys.stderr)
        return 130

    rep = resumen(resultados, opciones)
    _imprimir_resumen(rep, resultados)

    if args.reporte:
        destino_reporte = Path(args.reporte)
        destino_reporte.parent.mkdir(parents=True, exist_ok=True)
        destino_reporte.write_text(
            json.dumps(
                {**rep, "archivos_detalle": [r.a_dict() for r in resultados]},
                ensure_ascii=False,
                indent=2,
            ),
            encoding="utf-8",
        )
        print(f"\nreporte escrito: {destino_reporte}")

    return 1 if rep["fallos"] else 0


def _imprimir_detalle(r: Resultado) -> None:
    """Una línea por archivo (a stderr, para no ensuciar el reporte)."""
    dims = (
        f"{r.dims_origen[0]}x{r.dims_origen[1]}→{r.dims_destino[0]}x{r.dims_destino[1]}"
        if r.dims_origen and r.dims_destino
        else "?"
    )
    tokens = ""
    if r.dims_origen and r.dims_destino:
        tokens = (
            f" tokens {tokens_estimados_vlm(*r.dims_origen):,}"
            f"→{tokens_estimados_vlm(*r.dims_destino):,}"
        )
    print(
        f"  [{r.estado}] {r.origen}  {dims}  "
        f"{_formato_bytes(r.peso_origen)}→{_formato_bytes(r.peso_destino)}{tokens}"
        + (f"  ({r.motivo})" if r.motivo else ""),
        file=sys.stderr,
    )


if __name__ == "__main__":
    sys.exit(main())

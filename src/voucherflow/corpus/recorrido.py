"""Recorrido de un corpus y espejado del árbol en la salida.

Este módulo existe porque dos consumidores necesitan **exactamente la misma**
regla de espejado: el pipeline (``voucherflow.llm``, que escribe una validación
por documento) y la reducción de imágenes (``voucherflow.corpus``, que escribe
una imagen por documento).

⚠️ **La raíz de espejado es la regla que más caro sale equivocar.** Si el nivel
de carpetas de la salida cambia entre corridas, la reanudación —que saltea lo ya
hecho— no encuentra los archivos y **se vuelve a pagar** por documentos ya
procesados (pasó de verdad: 5 documentos, dos veces). Por eso la regla vive acá,
una sola vez, y ``raiz_espejado()`` **sube** hasta un nivel que no dependa de
desde dónde se invoque.
"""

from __future__ import annotations

import re
import sys
from collections.abc import Sequence
from pathlib import Path

from ..rutas import ancestro_comun

#: Motivo que se declara cuando no hay rutas existentes de dónde derivar la raíz.
MOTIVO_CWD = "cwd (las rutas no existen)"


def expandir(rutas: Sequence[Path], extensiones: frozenset[str]) -> list[Path]:
    """Expande archivos/carpetas a la lista de archivos, en orden determinista.

    Un archivo que no tiene una extensión pedida, o una ruta que no existe, se
    avisa por ``stderr`` y se ignora: es un recorrido de corpus, no una
    validación de argumentos (un archivo raro no debe abortar el lote).
    """
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


def raiz_espejado(
    rutas: Sequence[Path], explicita: Path | None, salida: Path | None = None
) -> tuple[Path, str]:
    """Raíz desde la cual se espeja el árbol, con su motivo (para declararlo).

    ⚠️ **La raíz no puede depender de la ruta que se pasa**, o el nivel de
    carpetas de la salida cambia entre corridas: procesar ``files`` escribía
    ``var/processed/2025-08/<hash>/img.jpg``, pero procesar ``var/files/2025-08``
    escribía ``var/processed/<hash>/img.jpg``. Por eso, si no hay ``--raiz``, se
    **sube** desde las rutas hasta la primera carpeta que no tenga un nombre de
    mes (``AAAA-MM``) — un nivel que no depende de desde dónde se invoque.
    ``--raiz`` manda siempre.

    Devuelve ``(raiz, motivo)``.
    """
    if explicita is not None:
        return explicita, "--raiz (explícita)"

    bases = [r if r.is_dir() else r.parent for r in rutas if r.exists()]
    if not bases:
        return Path.cwd(), MOTIVO_CWD
    if len(bases) == 1:
        raiz = bases[0]
    else:
        comun = ancestro_comun(bases)
        if comun is None:
            # Se declara en vez de devolver un `cwd` que parecería válido.
            return Path.cwd(), "cwd (rutas sin ancestro común)"
        raiz = comun

    subidas: list[str] = []
    # Sube mientras el último nivel sea un mes (AAAA-MM), un hash de lote
    # (hexadecimal) o esté dentro de la salida (p. ej. se apuntó a la carpeta de
    # salida por error). Así la raíz queda en un nivel estable y no cambia según
    # qué subcarpeta se haya pasado.
    while raiz.parent != raiz:
        if salida is not None and esta_dentro(raiz, salida) and raiz != salida:
            subidas.append(raiz.name)
            raiz = raiz.parent
            continue
        if es_nivel_de_corpus(raiz.name):
            subidas.append(raiz.name)
            raiz = raiz.parent
            continue
        break

    if subidas:
        return raiz, f"ascendida desde {bases[0]} (salteando: {', '.join(subidas)})"
    return raiz, "derivada de las rutas"


def es_nivel_de_corpus(nombre: str) -> bool:
    """True si el nombre es un nivel del corpus, no una raíz elegida.

    Dos formas: mes (``2025-08``, ``2025_8``) e identificador de lote —el hash
    hexadecimal de 8 caracteres que usa este corpus como carpeta por documento
    (``2D2C9343``)—. Una carpeta elegida a mano (``files``, ``lote-final``,
    ``mi_corpus``) no matchea ninguna, así que la subida se detiene ahí.
    """
    return bool(re.fullmatch(r"\d{4}[-_.]?\d{1,2}", nombre)) or bool(
        re.fullmatch(r"[0-9A-Fa-f]{8}", nombre)
    )


def esta_dentro(hijo: Path, padre: Path) -> bool:
    """True si ``hijo`` está dentro de ``padre`` (evita reprocesar la salida)."""
    try:
        hijo.resolve().relative_to(padre.resolve())
        return True
    except (ValueError, OSError):
        return False


def salida_de(archivo: Path, raiz: Path, salida: Path, sufijo: str) -> Path:
    """Dónde se guarda el resultado de ``archivo`` espejando desde ``raiz``.

    Función única para escribir y para buscar: si el cálculo se duplicara, la
    reanudación y la escritura podrían divergir (y se pagaría dos veces el mismo
    documento).
    """
    try:
        relativo = archivo.resolve().relative_to(raiz.resolve())
    except (ValueError, OSError):
        relativo = Path(archivo.name)
    return salida / relativo.with_suffix(f".{sufijo}.json")

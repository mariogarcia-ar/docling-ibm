"""Orquestación de una corrida de reducción: planificar, procesar, reportar.

Separado del CLI a propósito: la planificación (qué archivo va a dónde) y el
bucle de trabajo son lógica, y el CLI solo traduce argumentos a :class:`Opciones`
y presenta el resultado. Así una corrida se puede testear sin pasar por
``argparse``.
"""

from __future__ import annotations

import json
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Callable, Sequence

from .imagen import extension_destino, ffmpeg_no_disponible
from .modelo import ESTADO_FALLO, Opciones, Resultado
from .recorrido import esta_dentro, expandir, raiz_espejado
from .reduccion import procesar
from .reporte import resumen

#: Versión del contrato de la capacidad (para el reporte).
VERSION_CORPUS = "voucherflow-corpus@1"

#: Extensiones de imagen por defecto (las mismas del loop base).
EXTENSIONES_POR_DEFECTO = frozenset({".jpg", ".jpeg", ".png"})

#: Cada cuántos archivos se informa el avance (solo en modo no detallado).
CADA_CUANTOS_PROGRESO = 250


class ErrorCorpus(RuntimeError):
    """Error de uso de la corrida (argumentos incoherentes o backend inservible)."""


def normalizar_extensiones(texto: str) -> frozenset[str]:
    """Extensiones separadas por coma, normalizadas con punto y en minúscula."""
    extensiones = frozenset(
        e if e.startswith(".") else f".{e}"
        for e in (p.strip().lower() for p in texto.split(","))
        if e
    )
    if not extensiones:
        raise ErrorCorpus("--extensiones no puede quedar vacío")
    return extensiones


def validar(opciones: Opciones) -> None:
    """Valida las opciones que un rango no puede expresar (antes de recorrer)."""
    if opciones.lado_mayor <= 0:
        raise ErrorCorpus("--lado-mayor debe ser > 0")
    if not 1 <= opciones.calidad <= 100:
        raise ErrorCorpus("--calidad debe estar entre 1 y 100")
    if opciones.workers < 1:
        raise ErrorCorpus("--workers debe ser >= 1")
    if opciones.backend == "ffmpeg":
        # Preflight ANTES de recorrer el corpus (y una sola vez) en vez de
        # replicar el mismo error por archivo.
        problema = ffmpeg_no_disponible()
        if problema is not None:
            raise ErrorCorpus(problema)


def planificar(
    rutas: Sequence[Path],
    opciones: Opciones,
    *,
    extensiones: frozenset[str] = EXTENSIONES_POR_DEFECTO,
    raiz: Path | None = None,
    limite: int = 0,
) -> tuple[Path, str, list[tuple[Path, Path]]]:
    """Calcula ``(raiz, motivo, tareas)`` sin tocar ninguna imagen.

    Separa la decisión de *qué hacer* de *hacerlo*: así el modo de solo medición
    y una corrida real usan exactamente el mismo plan (si divergieran, la
    simulación mentiría).
    """
    raiz_efectiva, motivo = raiz_espejado(rutas, raiz, opciones.salida)
    imagenes = [
        img
        for img in expandir(rutas, extensiones)
        if not esta_dentro(img, opciones.salida)
    ]
    if limite > 0:
        imagenes = imagenes[:limite]

    tareas: list[tuple[Path, Path]] = []
    for img in imagenes:
        try:
            relativo = img.resolve().relative_to(raiz_efectiva.resolve())
        except (ValueError, OSError):
            relativo = Path(img.name)
        relativo = relativo.with_suffix(extension_destino(img, opciones.formato))
        tareas.append((img, opciones.salida / relativo))
    return raiz_efectiva, motivo, tareas


def ejecutar(
    tareas: Sequence[tuple[Path, Path]],
    opciones: Opciones,
    *,
    on_resultado: Callable[[Resultado, int], None] | None = None,
) -> list[Resultado]:
    """Procesa las tareas (en paralelo si ``workers > 1``) y devuelve los resultados.

    ``on_resultado(resultado, indice)`` se llama a medida que terminan, para que
    el CLI muestre el detalle o el avance sin que esta función sepa de impresión.
    """
    resultados: list[Resultado] = []
    total = len(tareas)

    if opciones.workers == 1:
        for i, (origen, destino) in enumerate(tareas, 1):
            resultado = procesar(origen, destino, opciones)
            resultados.append(resultado)
            if on_resultado is not None:
                on_resultado(resultado, i)
        return resultados

    with ThreadPoolExecutor(max_workers=opciones.workers) as pool:
        futuros = [
            pool.submit(procesar, origen, destino, opciones)
            for origen, destino in tareas
        ]
        for i, futuro in enumerate(futuros, 1):
            resultado = futuro.result()
            resultados.append(resultado)
            if on_resultado is not None:
                on_resultado(resultado, i)
    return resultados


def correr(
    rutas: Sequence[Path],
    opciones: Opciones,
    *,
    extensiones: frozenset[str] = EXTENSIONES_POR_DEFECTO,
    raiz: Path | None = None,
    limite: int = 0,
    on_resultado: Callable[[Resultado, int], None] | None = None,
) -> tuple[dict, list[Resultado]]:
    """Planifica, procesa y devuelve ``(resumen, resultados)``."""
    validar(opciones)
    _, _, tareas = planificar(
        rutas, opciones, extensiones=extensiones, raiz=raiz, limite=limite
    )
    resultados = ejecutar(tareas, opciones, on_resultado=on_resultado)
    return resumen(resultados, opciones), resultados


def escribir_reporte(
    destino: Path, rep: dict, resultados: Sequence[Resultado]
) -> None:
    """Escribe el reporte completo (resumen + detalle por archivo) en JSON."""
    destino.parent.mkdir(parents=True, exist_ok=True)
    destino.write_text(
        json.dumps(
            {
                "version": VERSION_CORPUS,
                **rep,
                "archivos_detalle": [r.a_dict() for r in resultados],
            },
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )


def contar_fallos(resultados: Sequence[Resultado]) -> int:
    """Cuántos archivos fallaron (código de salida del CLI)."""
    return sum(1 for r in resultados if r.estado == ESTADO_FALLO)

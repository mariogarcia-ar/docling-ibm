"""Orquestación de una corrida de reducción: planificar, procesar, reportar.

Separado del CLI a propósito: la planificación (qué archivo va a dónde) y el
bucle de trabajo son lógica, y el CLI solo traduce argumentos a :class:`Opciones`
y presenta el resultado. Así una corrida se puede testear sin pasar por
``argparse``.
"""

from __future__ import annotations

import json
from collections.abc import Callable, Sequence
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from .imagen import extension_destino, ffmpeg_no_disponible
from .modelo import ESTADO_FALLO, Opciones, Resultado
from .recorrido import esta_dentro, expandir, raiz_espejado, salida_de
from .reduccion import procesar

#: Versión del contrato de la capacidad (para el reporte). Se sube a ``@2``
#: en T-604-fix: el reporte ahora declara ``destinos_colisionados`` y
#: ``errores_lectura``, y el plan avisa de dos originales que escribirían el
#: mismo archivo.
VERSION_CORPUS = "voucherflow-corpus@2"

#: Extensiones de imagen por defecto (las mismas del loop base).
EXTENSIONES_POR_DEFECTO = frozenset({".jpg", ".jpeg", ".png"})

#: Cada cuántos archivos se informa el avance (solo en modo no detallado).
CADA_CUANTOS_PROGRESO = 250

#: ``salida_de`` sufija el resultado (``.validacion.json``); la reducción escribe
#: la imagen con su propia extensión, así que el sufijo va vacío.
NINGUN_SUFIJO = ""


class ErrorCorpus(RuntimeError):
    """Error de uso de la corrida (argumentos incoherentes o backend inservible)."""


def _clave(ruta: Path) -> Path:
    """Ruta canónica para decidir colisiones (resuelve ``..`` y symlinks)."""
    try:
        return ruta.resolve()
    except OSError:  # pragma: no cover - rutas raras del sistema
        return ruta


def detectar_colisiones(
    tareas: Sequence[tuple[Path, Path]],
) -> dict[Path, list[Path]]:
    """Destinos que dos o más originales DISTINTOS escribirían.

    ⚠️ **Por qué existe.** Con ``--formato jpg`` (o con ``foto.JPG`` y
    ``foto.jpg``, que colisionan ya con el default) dos originales calculan el
    mismo destino. La corrida quedaba con un solo archivo y el reporte los daba
    por reducidos a los dos: una pérdida silenciosa, peor por lo que el reporte
    afirmaba que por el archivo faltante.

    Solo cuenta colisiones entre **originales distintos**: pasar la carpeta y
    una imagen de su interior apunta dos veces al mismo origen, y eso ya lo
    resuelve :func:`~voucherflow.corpus.recorrido.expandir` (no es una
    colisión: es el mismo archivo pedido dos veces).

    Devuelve ``{destino: [orígenes, …]}`` solo con los destinos repetidos.
    """
    por_destino: dict[Path, list[Path]] = {}
    for origen, destino in tareas:
        por_destino.setdefault(_clave(destino), []).append(origen)
    return {
        destino: origenes
        for destino, origenes in por_destino.items()
        if len({_clave(o) for o in origenes}) > 1
    }


def describir_colisiones(colisiones: dict[Path, list[Path]]) -> str:
    """Texto legible de las colisiones, para rechazar el lote antes de tocarlo."""
    lineas = [
        "dos o más originales escribirían el mismo archivo de salida:",
        "",
    ]
    for destino in sorted(colisiones):
        lineas.append(f"  destino: {destino}")
        lineas.extend(f"    ← {origen}" for origen in sorted(colisiones[destino]))
    lineas.extend(
        [
            "",
            "Se aborta sin escribir nada: seguir perdería archivos y el reporte",
            "los daría por reducidos igual.",
            "  · «--formato mismo» conserva la extensión de cada original.",
            "  · «--raiz» (o «--salida») cambia el nivel desde el que se espeja.",
        ]
    )
    return "\n".join(lineas)


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
        # Se reusa ``salida_de`` (la función única de espejado) y encima se
        # aplica la extensión pedida: con ``--formato jpg`` el nombre cambia,
        # pero el NIVEL de carpetas no puede diferir del que usa la reanudación.
        destino = salida_de(img, raiz_efectiva, opciones.salida, NINGUN_SUFIJO)
        tareas.append(
            (img, destino.with_suffix(extension_destino(img, opciones.formato)))
        )
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

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
from .lectura import (
    Clasificacion,
    PdfExpandido,
    clasificar,
    directorio_temporal,
    expandir_pdf,
    limpiar_expansion,
)
from .modelo import ESTADO_FALLO, Opciones, Resultado
from .recorrido import (
    esta_dentro,
    expandir_todo,
    raiz_espejado,
    salida_de,
)
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
) -> tuple[Path, str, list[tuple[Path, Path]], Clasificacion]:
    """Calcula ``(raiz, motivo, tareas, clasificacion)`` sin tocar ninguna imagen.

    Separa la decisión de *qué hacer* de *hacerlo*: así el modo de solo medición
    y una corrida real usan exactamente el mismo plan (si divergieran, la
    simulación mentiría).

    ⚠️ Devuelve además la :class:`~voucherflow.corpus.lectura.Clasificacion`, que
    es lo que permite **declarar lo que queda afuera**. Antes, un archivo con una
    extensión no pedida desaparecía del conteo sin dejar rastro: un corpus con
    3.846 archivos informaba «3.582 imágenes» y nadie podía saber que faltaban 264
    (190 de ellos comprobantes fiscales).
    """
    raiz_efectiva, motivo = raiz_espejado(rutas, raiz, opciones.salida)
    encontrados = [
        f for f in expandir_todo(rutas) if not esta_dentro(f, opciones.salida)
    ]
    clasificacion = clasificar(encontrados, extensiones)

    imagenes = list(clasificacion.procesables)
    # Los PDF entran solo si se pidió: renderizar 264 PDFs de 3 páginas es trabajo
    # y disco, no algo que deba pasar por sorpresa.
    if opciones.incluir_pdf and clasificacion.pdfs:
        imagenes.extend(_expandir_pdfs(clasificacion, opciones, raiz_efectiva))

    imagenes.sort()
    if limite > 0:
        imagenes = imagenes[:limite]

    tareas: list[tuple[Path, Path]] = []
    for img in imagenes:
        # ⚠️ Los renders de PDF viven en un temporal, así que su propia ruta NO
        # sirve para calcular el destino (saldría plano, sin el nivel de carpeta).
        # Se usa el PDF original: es el que define dónde va el resultado.
        logico = _origen_logico(img, clasificacion)
        destino = salida_de(logico, raiz_efectiva, opciones.salida, NINGUN_SUFIJO)
        tareas.append(
            (img, destino.with_suffix(extension_destino(img, opciones.formato)))
        )
    return raiz_efectiva, motivo, tareas, clasificacion


def _origen_logico(imagen: Path, clasificacion: Clasificacion) -> Path:
    """El archivo del corpus que representa esta imagen (el PDF, si es un render).

    Para una imagen normal es ella misma. Para un render de PDF es el PDF: el
    render es un insumo temporal y no tiene por qué aparecer en la salida.
    """
    if clasificacion.expansion_pdf:
        for render, pdf in clasificacion.expansion_pdf.pares:
            if render == imagen:
                return pdf
    return imagen


def _expandir_pdfs(
    clasificacion: Clasificacion, opciones: Opciones, raiz: Path
) -> list[Path]:
    """Renderiza los PDF a imágenes temporales (una por página).

    Los archivos van a un temporal del proceso: son insumos para el lote, no
    salida del usuario. Se borran en :func:`limpiar_renders`. El árbol se espeja
    desde ``raiz`` para que el destino conserve el nivel de carpeta del PDF.
    """
    temporal = directorio_temporal()
    pares: list[tuple[Path, Path]] = []
    for pdf in clasificacion.pdfs:
        for render in expandir_pdf(pdf, temporal, raiz=raiz, dpi=opciones.dpi_pdf):
            pares.append((render, pdf))
    expansion = PdfExpandido(temporal=temporal, pares=pares)
    clasificacion.expansion_pdf = expansion
    return expansion.renders


def limpiar_renders(clasificacion: Clasificacion) -> None:
    """Borra el temporal de renders de PDF, si se creó."""
    limpiar_expansion(clasificacion.expansion_pdf)


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

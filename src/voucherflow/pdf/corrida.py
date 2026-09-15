"""Ejecución del plan: renderizar cada página y escribir el destino.

Dos propiedades que la corrida hereda del resto del repo y que no son obvias:

* **Reanudable por existencia.** Un destino que ya existe **y no está vacío** no se
  vuelve a renderizar (salvo ``--forzar``). El render es barato, pero un lote de
  3.846 archivos sí se nota; y el mismo criterio que la reducción de corpus hace
  que los dos comandos se comporten igual.
* **Un fallo no aborta el lote ni se pierde**: se registra con su motivo y la
  corrida sigue (es la regla de ``corpus``, y también la de
  ``lectura.expandir_pdf``, que saltea la página rota en vez de perder el PDF).
"""

from __future__ import annotations

from collections.abc import Callable, Sequence

from ..processing.orquestacion import render_pdf_a_jpg
from .modelo import (
    ESTADO_ESCRITO,
    ESTADO_FALLO,
    ESTADO_REANUDADO,
    Opciones,
    Resultado,
    Tarea,
)

#: Cada cuántos archivos se informa el progreso (el mismo valor que ``corpus``).
CADA_CUANTOS_PROGRESO = 50


def ejecutar(
    tareas: Sequence[Tarea],
    opciones: Opciones,
    *,
    on_resultado: Callable[[Resultado, int], None] | None = None,
) -> list[Resultado]:
    """Renderiza las tareas del plan y devuelve un resultado por página.

    ``on_resultado`` se llama con cada resultado y su índice (para el progreso y
    el detalle). El orden de la lista es el del plan.
    """
    resultados: list[Resultado] = []
    for i, tarea in enumerate(tareas, start=1):
        if not opciones.forzar and _ya_escrito(tarea.destino):
            resultado = Resultado(
                origen=tarea.pdf,
                pagina=tarea.pagina,
                destino=tarea.destino,
                estado=ESTADO_REANUDADO,
                motivo="ya existía",
            )
        elif opciones.solo_medir:
            resultado = Resultado(
                origen=tarea.pdf,
                pagina=tarea.pagina,
                destino=tarea.destino,
                estado=ESTADO_ESCRITO,
                motivo="simulación: no se escribió",
            )
        else:
            resultado = _renderizar(tarea, opciones)
        resultados.append(resultado)
        if on_resultado is not None:
            on_resultado(resultado, i)
    return resultados


def _ya_escrito(destino) -> bool:
    """True si el destino existe **y tiene contenido**.

    ⚠️ El chequeo de tamaño no es decorativo: una corrida interrumpida a mitad de
    un ``save`` puede dejar el archivo creado y vacío, y darlo por hecho dejaría un
    hueco silencioso en la salida. El render es atómico (``processing.orquestacion``
    escribe con temporal + ``os.replace``), así que esto solo cubre un archivo
    preexistente de una corrida vieja o de otro proceso.
    """
    return destino.exists() and destino.stat().st_size > 0


def _renderizar(tarea: Tarea, opciones: Opciones) -> Resultado:
    """Renderiza una página, devolviendo el resultado (nunca levanta)."""
    try:
        render_pdf_a_jpg(
            tarea.pdf,
            pagina=tarea.pagina - 1,  # el modelo es 1-based, PyMuPDF 0-based
            dpi=opciones.dpi,
            recortar=opciones.recortar,
            destino=tarea.destino,
            calidad=opciones.calidad,
        )
    except Exception as exc:  # noqa: BLE001 - una página rota no corta el lote
        return Resultado(
            origen=tarea.pdf,
            pagina=tarea.pagina,
            destino=None,
            estado=ESTADO_FALLO,
            motivo=_motivo(exc),
        )

    dims, peso = _medir(tarea.destino)
    return Resultado(
        origen=tarea.pdf,
        pagina=tarea.pagina,
        destino=tarea.destino,
        estado=ESTADO_ESCRITO,
        dims=dims,
        peso=peso,
    )


def _medir(destino) -> tuple[tuple[int, int] | None, int | None]:
    """Dimensiones y peso del render. Si no se puede medir, no es un fallo."""
    try:
        peso = destino.stat().st_size
    except OSError:  # pragma: no cover - el archivo se acaba de escribir
        return None, None
    try:
        from PIL import Image

        with Image.open(destino) as img:
            return img.size, peso
    except Exception:  # noqa: BLE001 - no poder medir no invalida el render
        return None, peso


def _motivo(exc: BaseException, limite: int = 200) -> str:
    """Resume una excepción en una línea, sin volcar el stack de PyMuPDF."""
    for linea in str(exc).splitlines():
        limpia = " ".join(linea.split())
        if limpia:
            return limpia[:limite]
    return exc.__class__.__name__


def contar_fallos(resultados: Sequence[Resultado]) -> int:
    """Cuántas páginas no se pudieron renderizar (para el código de salida)."""
    return sum(1 for r in resultados if r.estado == ESTADO_FALLO)


__all__ = ["CADA_CUANTOS_PROGRESO", "contar_fallos", "ejecutar"]

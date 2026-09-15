"""Reducción de una imagen: mide, decide (reducir/omitir/copiar) y escribe.

La decisión es **código, no heurística**: si la imagen ya entra en el objetivo no
se toca (el loop de shell la agrandaba), y nunca se escribe sobre la entrada.
"""

from __future__ import annotations

import shutil
from pathlib import Path

from ..persistencia import ya_escrito
from .dimensiones import dimensiones_objetivo
from .imagen import medir, reducir, tipo_salida
from .modelo import (
    CATEGORIA_COPIA,
    CATEGORIA_LECTURA,
    CATEGORIA_REANUDADO,
    ESTADO_FALLO,
    ESTADO_OMITIDO,
    ESTADO_REDUCIDO,
    Opciones,
    Resultado,
)

#: Motivo de omisión reutilizado (lo lee también el reporte).
MOTIVO_YA_ENTRA = "ya entra en el objetivo"


def procesar(origen: Path, destino: Path, opciones: Opciones) -> Resultado:
    """Procesa una imagen: mide, decide (reducir/omitir/copiar) y escribe."""
    try:
        peso_origen = origen.stat().st_size
    except OSError as exc:
        return Resultado(
            origen,
            None,
            ESTADO_FALLO,
            f"no se pudo leer: {exc}",
            categoria=CATEGORIA_LECTURA,
        )

    dims_origen = medir(origen)
    if dims_origen is None:
        return Resultado(
            origen,
            None,
            ESTADO_FALLO,
            "no se pudieron leer las dimensiones (imagen ilegible)",
            peso_origen=peso_origen,
            categoria=CATEGORIA_LECTURA,
        )

    # Seguridad: nunca escribir sobre la entrada (lección de T-604 para `process`).
    if destino.resolve() == origen.resolve():
        return Resultado(
            origen,
            destino,
            ESTADO_OMITIDO,
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
        return _ya_entra(origen, destino, dims_origen, peso_origen, opciones)

    if ya_escrito(destino) and not opciones.forzar:
        # Reanudación: el destino ya está. NO se asume que tenga las
        # dimensiones calculadas (pudo generarse con otros parámetros) → se
        # mide el archivo real para que el reporte no invente el resultado.
        return Resultado(
            origen,
            destino,
            ESTADO_OMITIDO,
            "el destino ya existe (usar --forzar para reescribir)",
            dims_origen=dims_origen,
            dims_destino=medir(destino),
            peso_origen=peso_origen,
            peso_destino=destino.stat().st_size,
            categoria=CATEGORIA_REANUDADO,
        )

    if not opciones.escribir:
        return Resultado(
            origen,
            destino,
            ESTADO_REDUCIDO,
            "simulación (--solo-medir): no se escribió",
            dims_origen=dims_origen,
            dims_destino=dims_destino,
            peso_origen=peso_origen,
        )

    try:
        destino.parent.mkdir(parents=True, exist_ok=True)
        reducir(
            origen,
            destino,
            dims_destino,
            backend=opciones.backend,
            calidad=opciones.calidad,
        )
        peso_destino = destino.stat().st_size
    except Exception as exc:  # noqa: BLE001 - un archivo malo no corta el lote
        return Resultado(
            origen,
            destino,
            ESTADO_FALLO,
            f"{type(exc).__name__}: {exc}",
            dims_origen=dims_origen,
            dims_destino=dims_destino,
            peso_origen=peso_origen,
        )

    resultado = Resultado(
        origen,
        destino,
        ESTADO_REDUCIDO,
        "",
        dims_origen=dims_origen,
        dims_destino=dims_destino,
        peso_origen=peso_origen,
        peso_destino=peso_destino,
    )
    if resultado.engordo:
        # No es un fallo (los tokens bajan igual), pero el operador tiene que
        # poder verlo: dentro del total del corpus queda invisible.
        resultado.motivo = (
            f"el peso subió ({peso_destino} > {peso_origen} bytes): la imagen ya "
            "venía más comprimida que el reencode"
        )
    return resultado


def _ya_entra(
    origen: Path,
    destino: Path,
    dims: tuple[int, int],
    peso_origen: int,
    opciones: Opciones,
) -> Resultado:
    """La imagen ya entra en el objetivo: se omite o se copia, sin reducir.

    ``--copiar-no-reducidas`` existe para que la salida quede **completa** (un
    corpus reducido con huecos no sirve para alimentar otra herramienta).
    """
    motivo = f"{dims[0]}x{dims[1]}px {MOTIVO_YA_ENTRA}"

    if not opciones.copiar_no_reducidas:
        return Resultado(
            origen,
            destino,
            ESTADO_OMITIDO,
            motivo,
            dims_origen=dims,
            dims_destino=dims,
            peso_origen=peso_origen,
            peso_destino=None if not opciones.escribir else peso_origen,
        )
    if not opciones.escribir:
        return Resultado(
            origen,
            destino,
            ESTADO_OMITIDO,
            "se copiaría sin reducir",
            dims_origen=dims,
            dims_destino=dims,
            peso_origen=peso_origen,
        )
    ya_existe = ya_escrito(destino) and not opciones.forzar
    # ⚠️ Copiar los bytes no sirve si el formato pedido no es el del original:
    # un PNG copiado a ``foto.jpg`` queda con nombre que miente sobre el
    # contenido (y ningún visor le cree — el defecto 3 del loop base, en la
    # rama de copia). Ahí se reencoda SIN cambiar el tamaño.
    if tipo_salida(origen.suffix) != tipo_salida(destino.suffix):
        if ya_existe:
            return Resultado(
                origen,
                destino,
                ESTADO_OMITIDO,
                "el destino ya existe (usar --forzar para reescribir)",
                dims_origen=dims,
                dims_destino=medir(destino),
                peso_origen=peso_origen,
                peso_destino=destino.stat().st_size,
                categoria=CATEGORIA_REANUDADO,
            )
        try:
            destino.parent.mkdir(parents=True, exist_ok=True)
            reducir(
                origen,
                destino,
                dims,
                backend=opciones.backend,
                calidad=opciones.calidad,
            )
        except Exception as exc:  # noqa: BLE001 - un archivo malo no corta el lote
            return Resultado(
                origen, destino, ESTADO_FALLO, f"{type(exc).__name__}: {exc}"
            )
        return Resultado(
            origen,
            destino,
            ESTADO_OMITIDO,
            "reencodada al formato pedido, sin reducir (ya entra en el objetivo)",
            dims_origen=dims,
            dims_destino=dims,
            peso_origen=peso_origen,
            peso_destino=destino.stat().st_size,
            categoria=CATEGORIA_COPIA,
        )

    try:
        if not ya_existe:
            destino.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(origen, destino)
    except OSError as exc:
        return Resultado(
            origen, destino, ESTADO_FALLO, f"no se pudo copiar: {exc}"
        )
    return Resultado(
        origen,
        destino,
        ESTADO_OMITIDO,
        (
            "copia ya existente (usar --forzar para reescribir)"
            if ya_existe
            else "copiada sin reducir (ya entra en el objetivo)"
        ),
        dims_origen=dims,
        dims_destino=dims,
        peso_origen=peso_origen,
        peso_destino=(destino.stat().st_size if destino.exists() else None),
        # Una copia sigue siendo una copia aunque ya estuviera: hay archivo.
        categoria=CATEGORIA_COPIA if not ya_existe else CATEGORIA_REANUDADO,
    )

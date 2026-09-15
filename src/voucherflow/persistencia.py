"""Escritura atómica de archivos: un temporal, ``fsync`` y un ``os.replace``.

Este módulo existe porque el mismo invariante estaba escrito **cuatro veces**
(idéntico tres veces, y una cuarta con una variante más débil) en `batch`,
`trace/agregado`, `trace/recorder` y `llm/corrida`. Lo destapó `pylint`
(``R0801``) y después el análisis estructural; ``jscpd`` solo veía la mitad.

**Por qué importa unificarlo.** Un archivo a medio escribir es peor que no
tenerlo: la reanudación lo lee como «hecho» y sigue adelante con el dato
corrupto sin que nadie lo note. Y los cuatro usos son exactamente los archivos
de los que depende esa reanudación: los checkpoints del lote, el sidecar y el
índice de trazabilidad, el agregado, y el JSON de una corrida.

**Las tres garantías que da el patrón** (y que una copia floja puede perder):

1. **Temporal en el mismo directorio** que el destino: ``os.replace`` es atómico
   solo dentro del mismo sistema de archivos. Un temporal en ``/tmp`` podría
   estar en otro volumen y el reemplazo dejaría de ser atómico.
2. **``fsync`` antes del ``replace``**: sin esto, el rename puede llegar al
   disco *antes* que el contenido, y un corte de energía deja el archivo
   renombrado y vacío. Es la diferencia entre «atómico» y «atómico de verdad».
3. **El temporal se limpia si algo falla**: si no, quedan ``.nombre.XXX.tmp``
   acumulados al lado de cada archivo.

Las tres se dan igual para texto (:func:`escribir_atomico`), JSON
(:func:`escribir_json_atomico`) y binario (:func:`escribir_bytes_atomico`): las
tres variantes comparten la mecánica, y tener una copia aparte para bytes fue
exactamente el problema que este módulo vino a cerrar.
"""

from __future__ import annotations

import json
import os
import tempfile
from pathlib import Path
from typing import Any


def escribir_atomico(destino: Path, contenido: str) -> None:
    """Escribe ``contenido`` en ``destino`` sin que exista un estado intermedio.

    Temporal en el mismo directorio + ``fsync`` + ``os.replace``: si el proceso
    muere a mitad, **el archivo anterior queda intacto** y nunca se lee un JSON
    truncado. Si algo falla, el temporal se borra y la excepción se propaga (el
    llamador decide si eso es fatal: el lote lo anota y sigue).
    """
    destino.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporal = tempfile.mkstemp(
        dir=str(destino.parent), prefix=f".{destino.name}.", suffix=".tmp"
    )
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as archivo:
            archivo.write(contenido)
            archivo.flush()
            os.fsync(archivo.fileno())
        os.replace(temporal, destino)
    except BaseException:
        try:
            os.unlink(temporal)
        except OSError:
            pass
        raise


def escribir_bytes_atomico(destino: Path, datos: bytes, *, sufijo: str = ".tmp") -> None:
    """Escribe ``datos`` (binario) en ``destino`` sin estado intermedio.

    Misma mecánica que :func:`escribir_atomico` para el caso binario (una imagen
    renderizada), con un detalle que **no** es cosmético:

    ⚠️ ``sufijo`` es el del **contenido**, no ``.tmp``, porque hay escritores que
    deducen el formato de la extensión (PyMuPDF, con los pixmaps) y un
    ``x.jpg.abc.tmp`` no les dice que tienen que escribir un JPEG. El default
    ``.tmp`` sirve para formatos que se declaran solos; quien necesite que la
    extensión hable, pasa la extensión real del destino.

    El ``fsync`` también se hace acá: un JPEG a medio escribir con el nombre final
    es peor que no tenerlo, porque la reanudación lo daría por bueno.
    """
    destino.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporal = tempfile.mkstemp(
        dir=str(destino.parent), prefix=f".{destino.name}.", suffix=sufijo
    )
    try:
        with os.fdopen(descriptor, "wb") as archivo:
            archivo.write(datos)
            archivo.flush()
            os.fsync(archivo.fileno())
        os.replace(temporal, destino)
    except BaseException:
        try:
            os.unlink(temporal)
        except OSError:
            pass
        raise


def escribir_json_atomico(destino: Path, datos: Any) -> None:
    """Serializa ``datos`` a JSON y lo escribe de forma atómica.

    ``default=str`` es deliberado: hay campos de fecha en los registros y se
    prefiere su representación textual a que la escritura falle al final de una
    corrida larga.
    """
    escribir_atomico(
        destino, json.dumps(datos, ensure_ascii=False, indent=2, default=str)
    )

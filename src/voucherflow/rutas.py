"""Rutas: el cálculo que hay que escribir una sola vez.

Hoy tiene una sola cosa, y la tiene porque **estaba escrita dos veces** (una en
``corpus/recorrido.py`` y otra —muerta— en ``llm/corrida.py``). Es el tipo de
función que se copia sin pensar: dos líneas de ``os.path.commonpath`` con un
``try/except``, que parecen obvias hasta que una de las copias decide distinto
qué hacer cuando no hay ancestro común.

⚠️ **El caso que importa es el del fallo.** ``os.path.commonpath`` levanta
``ValueError`` cuando las rutas no comparten nada (volúmenes distintos), y ahí
hay dos decisiones posibles: devolver algo (el ``cwd``) o decir que no hay. Las
dos se usaban en el mismo bloque de ``raiz_espejado`` —y llamar a
``commonpath`` **dos veces** para averiguarlo era el síntoma de que faltaba una
función—. Acá la decisión es explícita: se devuelve ``None``.
"""

from __future__ import annotations

import os
from collections.abc import Sequence
from pathlib import Path


def ancestro_comun(bases: Sequence[Path]) -> Path | None:
    """Ancestro común de varias rutas, o ``None`` si no comparten ninguno.

    Las rutas se **resuelven** antes de compararlas: ``os.path.commonpath``
    trabaja sobre texto, así que ``a/../a`` y ``a`` darían un resultado distinto
    sin normalizar (y el mismo archivo tiene que calcular siempre la misma raíz,
    de la que depende la reanudación).

    Devuelve ``None`` —en vez de un ``cwd`` de relleno— cuando el ancestro no
    existe, para que el llamador pueda **declararlo** en lugar de recibir una
    respuesta que parece válida. Una lista vacía tampoco tiene ancestro.

    Lanza ``ValueError`` si se mezclan rutas absolutas y relativas: eso es un
    error de programación, no un caso a resolver en silencio.
    """
    if not bases:
        return None
    try:
        comun = os.path.commonpath([str(b.resolve()) for b in bases])
    except ValueError:  # sin ancestro común (o rutas absolutas y relativas mezcladas)
        return None
    return Path(comun)

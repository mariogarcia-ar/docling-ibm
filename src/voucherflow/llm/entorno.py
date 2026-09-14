"""Credenciales y variables de entorno de los proveedores.

La clave se lee de la variable del proveedor o de un ``.env`` (sin pisar lo que
ya esté en el entorno). **Nunca** se imprime ni se guarda en la salida: viaja
solo al cliente del SDK.
"""

from __future__ import annotations

import os
from pathlib import Path

# --------------------------------------------------------------------------- #
# Entorno / credenciales
# --------------------------------------------------------------------------- #


def cargar_env(ruta: Path | None) -> None:
    """Carga ``KEY=VALUE`` de un ``.env`` sin pisar lo ya presente en el entorno.

    Parser mínimo a propósito: el repo no suma dependencias (``python-dotenv``
    sería una) y el formato que hace falta es trivial.
    """
    if ruta is None or not ruta.is_file():
        return
    for linea in ruta.read_text(encoding="utf-8").splitlines():
        linea = linea.strip()
        if not linea or linea.startswith("#") or "=" not in linea:
            continue
        clave, _, valor = linea.partition("=")
        clave = clave.strip()
        valor = valor.strip().strip("'\"")
        if clave and clave not in os.environ:
            os.environ[clave] = valor


def resolver_api_key(
    explicita: str | None, proveedor: str | None = None
) -> str | None:
    """Devuelve la clave de API: la del argumento o la del **proveedor elegido**.

    ⚠️ La variable depende del proveedor (``OPENAI_API_KEY``,
    ``DEEPSEEK_API_KEY``, ``GEMINI_API_KEY``). Estuvo cableada a
    ``DEEPSEEK_API_KEY`` — el proveedor que se usó primero —, y eso rompía a los
    demás de dos maneras: con la clave correcta en el entorno igual decía «falta
    la credencial», y con la de DeepSeek presente le mandaba **esa** clave al
    endpoint de otro proveedor. Si no se indica proveedor, se usa la variable
    del adaptador por defecto.
    """
    if explicita:
        return explicita
    variable = _variable_de(proveedor)
    return os.environ.get(variable) or None


def _variable_de(proveedor: str | None) -> str:
    """Nombre de la variable de entorno de la credencial de ``proveedor``.

    Se importa acá adentro (no arriba) porque ``proveedores`` importa a este
    módulo: hacerlo a nivel de módulo sería un ciclo.
    """
    from .proveedores import PROVEEDOR_POR_DEFECTO, proveedor_por_nombre

    return proveedor_por_nombre(proveedor or PROVEEDOR_POR_DEFECTO).capacidades.variable_api_key



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


def resolver_api_key(explicita: str | None) -> str | None:
    """Devuelve la clave de API: la del argumento o la del entorno."""
    return explicita or os.environ.get("DEEPSEEK_API_KEY") or None



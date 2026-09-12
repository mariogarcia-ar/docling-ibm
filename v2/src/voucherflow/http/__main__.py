"""Arranque del servidor HTTP: ``python -m voucherflow.http`` — F6/T-606.

Es el punto de entrada del **binario** ``voucherflow-http`` (declarado en
``pyproject.toml``). Se mantiene **fuera** del CLI de once subcomandos a
propósito:

1. En el diagrama C4 (doc 03 §3) el **CLI** y la **API HTTP** son dos contenedores
   **separados** del nivel Cliente, los dos apuntando al orquestador. Un entry
   point propio materializa esa separación en vez de mezclar dos superficies.
2. El contrato de E-CLI-1 ("once subcomandos") está **verificado** por T-604
   (paridad) y T-605 (documentación). Agregar un duodécimo comando reabriría un
   contrato cerrado para una tarea que es fase 2 / no bloqueante.
3. Un subcomando que **bloquea** hasta que lo interrumpan no encaja con el resto
   del CLI, donde cada comando hace su trabajo y termina.

Uso:
    python -m voucherflow.http                    # 127.0.0.1:8000
    python -m voucherflow.http --puerto 9000
    python -m voucherflow.http --host 0.0.0.0     # decisión explícita (avisa)
    voucherflow-http --puerto 9000                # el binario instalado

Las banderas son de **arranque** (dónde escuchar), no de negocio: lo que se pide
se pide por HTTP a las rutas del servidor. La única excepción es ``--raiz``, que
es el directorio contra el que se resuelven las rutas relativas de los documentos
(el mismo concepto que el ``cwd`` del CLI).
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from .server import HOST_DEFAULT, PUERTO_DEFAULT, VERSION_HTTP, EntornoHTTP, servir


def construir_parser() -> argparse.ArgumentParser:
    """Parser de arranque del servidor (``argparse`` de la stdlib)."""
    parser = argparse.ArgumentParser(
        prog="voucherflow-http",
        description=(
            "API HTTP de voucherflow (F6/T-606, fase 2). Expone por red lo que la "
            "librería ya sabe hacer: run, extract, ask."
        ),
    )
    parser.add_argument(
        "--host",
        default=HOST_DEFAULT,
        help=(
            f"Interfaz donde escuchar (default: {HOST_DEFAULT}). El default es "
            "localhost a propósito: el servidor no trae autenticación."
        ),
    )
    parser.add_argument(
        "--puerto",
        type=int,
        default=PUERTO_DEFAULT,
        help=(
            f"Puerto (default: {PUERTO_DEFAULT}). Con 0 el SO elige uno libre "
            "(útil para pruebas)."
        ),
    )
    parser.add_argument(
        "--raiz",
        type=Path,
        default=None,
        help=(
            "Directorio contra el que se resuelven las rutas relativas de los "
            "documentos (default: el directorio actual)."
        ),
    )
    parser.add_argument(
        "--version",
        action="version",
        version=f"voucherflow-http {VERSION_HTTP}",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    """Punto de entrada del servidor. Devuelve el código de salida.

    Igual que ``cli.main`` (T-601 §2.4): **devuelve** el código en vez de llamar
    a ``sys.exit``, así el arranque se puede ejercitar in-process.
    """
    args = construir_parser().parse_args(argv)
    entorno = EntornoHTTP(raiz=args.raiz or Path.cwd())
    try:
        servir(args.host, args.puerto, entorno=entorno)
    except OSError as exc:
        print(f"ERROR: no se pudo escuchar en {args.host}:{args.puerto}: {exc}",
              file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":  # pragma: no cover - cobertura por el entry point
    raise SystemExit(main())

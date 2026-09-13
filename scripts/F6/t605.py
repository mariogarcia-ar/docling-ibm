#!/usr/bin/env python
"""Inspecciona T-605 (F6) — documentación de usuario y README de v2.

**Fase**: F6 (cliente) · **Tarea**: T-605 · **Épica**: E-CLI.

Verifica la **cobertura** de la guía del operador contra la fuente factual — el
contrato del CLI — **sin** Ollama, **sin** Docling y **sin** red.

Qué muestra
-----------

1. **Los archivos de la guía**: cada documento que el índice promete existe (si
   falta uno, el operador llega a un enlace roto).
2. **La cobertura de comandos**: cada uno de los once subcomandos del contrato
   (E-CLI-1) tiene su sección en la referencia, y la referencia **no documenta**
   comandos que no existen. Las dos direcciones importan: un comando sin
   documentar es indescubrible; un comando inventado manda al operador a un
   error.
3. **Las banderas documentadas**: cada bandera que el parser real acepta aparece
   en la doc. Es la diferencia entre "el comando está documentado" y "el comando
   se puede usar" (un `--workers` sin explicar no sirve).
4. **La navegación**: los enlaces relativos entre los documentos de la guía
   resuelven (incluido `../../../BATCH.md`, que sale del árbol de `docs/`).
5. **Las fronteras**: la doc **no promete** lo que el sistema no hace — que el
   CLI no carga correcciones HITL, que `--force`/`--workers` solo tienen efecto
   real en `batch`, y que un rechazo firme es certeza alta, no un error.

Uso:
    python scripts/F6/t605.py                    # cobertura + navegación + fronteras
    python scripts/F6/t605.py --detalle          # + una línea por archivo/bandera
    python scripts/F6/t605.py --json /tmp/t605.json

La suite default cubre lo mismo en ``tests/test_docs_usuario_t605.py``.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path
from typing import Any

# El paquete usa layout src/; si se corre sin instalar, se agrega src/ al sys.path.
_SRC = Path(__file__).resolve().parents[2] / "src"
if str(_SRC) not in sys.path:
    sys.path.insert(0, str(_SRC))

from voucherflow.cli.main import COMANDOS, VERSION_CLI, construir_parser  # noqa: E402

RAIZ_V2 = Path(__file__).resolve().parents[2]
RAIZ_REPO = RAIZ_V2.parent
DOC_USUARIO = RAIZ_V2 / "docs" / "usuario"

DOCS_REQUERIDOS = (
    "README.md",
    "01-instalacion.md",
    "02-comandos.md",
    "03-revision-humana.md",
    "04-salidas.md",
)

#: El README de v2 debe apuntar a la guía del operador (T-605).
README_V2 = RAIZ_V2 / "README.md"

#: La guía de lotes vive en la raíz del repo (T-602).
BATCH_MD = RAIZ_REPO / "BATCH.md"

#: Documento donde vive la referencia de cada comando.
REFERENCIA = "02-comandos.md"


def _texto(nombre: str) -> str:
    return (DOC_USUARIO / nombre).read_text(encoding="utf-8")


def _enlaces(texto: str) -> list[str]:
    return re.findall(r"\[[^\]]*\]\(([^)]+)\)", texto)


def _secciones(texto: str, nivel: int = 2) -> list[str]:
    return re.findall(rf"^{'#' * nivel} (.+)$", texto, re.MULTILINE)


def _comandos_documentados() -> set[str]:
    """Los comandos con sección en la referencia (``## `x` …``)."""
    return {
        coincide.group(1)
        for seccion in _secciones(_texto(REFERENCIA))
        if (coincide := re.match(r"^`([a-z][a-z-]*)`", seccion))
    }


def _banderas_de(comando: str) -> list[str]:
    """Las banderas **largas** que el parser real acepta para un subcomando."""
    parser = construir_parser()
    subparsers = next(
        accion
        for accion in parser._actions
        if isinstance(accion, argparse._SubParsersAction)  # noqa: SLF001
    )
    sub = subparsers.choices[comando]
    largas: list[str] = []
    for accion in sub._actions:  # noqa: SLF001
        largas.extend(op for op in accion.option_strings if op.startswith("--"))
    # ``--help`` lo agrega argparse en todos: no es una bandera del contrato.
    return [b for b in largas if b != "--help"]


def _seccion_de_comando(comando: str) -> str:
    """El texto de la sección ``## `comando` …`` (hasta el próximo ``## ``)."""
    texto = _texto(REFERENCIA)
    partes = re.split(r"^## ", texto, flags=re.MULTILINE)
    for parte in partes:
        if parte.startswith(f"`{comando}`"):
            return parte
    return ""


def _seccion_comunes() -> str:
    """La sección ``## Banderas comunes`` de la referencia."""
    for parte in re.split(r"^## ", _texto(REFERENCIA), flags=re.MULTILINE):
        if parte.startswith("Banderas comunes"):
            return parte
    return ""


# ---------------------------------------------------------------------------
# Chequeos
# ---------------------------------------------------------------------------


def _archivos() -> list[dict[str, Any]]:
    """Cada documento de la guía: existe y tiene contenido."""
    salida: list[dict[str, Any]] = []
    for nombre in DOCS_REQUERIDOS:
        ruta = DOC_USUARIO / nombre
        existe = ruta.is_file()
        lineas = len(ruta.read_text(encoding="utf-8").splitlines()) if existe else 0
        salida.append(
            {
                "que": f"docs/usuario/{nombre}",
                "ok": existe and lineas > 0,
                "lineas": lineas,
                "detalle": f"{lineas} líneas" if existe else "NO EXISTE",
            }
        )
    salida.append(
        {
            "que": "BATCH.md (raíz del repo)",
            "ok": BATCH_MD.is_file(),
            "lineas": len(BATCH_MD.read_text(encoding="utf-8").splitlines())
            if BATCH_MD.is_file()
            else 0,
            "detalle": "guía de lotes (T-602)"
            if BATCH_MD.is_file()
            else "NO EXISTE",
        }
    )
    salida.append(
        {
            "que": "v2/README.md enlaza la guía del operador",
            "ok": README_V2.is_file()
            and "docs/usuario/" in README_V2.read_text(encoding="utf-8"),
            "lineas": 0,
            "detalle": "enlace presente"
            if README_V2.is_file()
            and "docs/usuario/" in README_V2.read_text(encoding="utf-8")
            else "FALTA el enlace a docs/usuario/",
        }
    )
    return salida


def _cobertura_comandos() -> list[dict[str, Any]]:
    """Los comandos del contrato y los documentados: las dos direcciones."""
    documentados = _comandos_documentados()
    faltantes = sorted(set(COMANDOS) - documentados)
    sobrantes = sorted(documentados - set(COMANDOS))
    return [
        {
            "que": "cada comando del contrato tiene sección",
            "ok": not faltantes,
            "detalle": f"{len(COMANDOS) - len(faltantes)}/{len(COMANDOS)} documentados"
            + (f" · faltan: {faltantes}" if faltantes else ""),
        },
        {
            "que": "la referencia no documenta comandos inexistentes",
            "ok": not sobrantes,
            "detalle": "sin comandos inventados"
            if not sobrantes
            else f"inventados: {sobrantes}",
        },
        {
            "que": "el conjunto documentado == el contrato",
            "ok": documentados == set(COMANDOS),
            "detalle": f"{len(documentados)} documentados / {len(COMANDOS)} reales",
        },
    ]


def _cobertura_banderas() -> list[dict[str, Any]]:
    """Cada bandera larga real aparece **en la sección de su comando**.

    Se busca en la sección del comando y, para las banderas compartidas, en la
    sección "Banderas comunes" (que es donde la referencia las documenta una sola
    vez). Buscar en todo el documento sería más laxo: una bandera documentada en
    la sección equivocada pasaría el chequeo, y el operador que lee la sección de
    *ese* comando no la encontraría.
    """
    comunes = _seccion_comunes()
    salida: list[dict[str, Any]] = []
    for comando in COMANDOS:
        seccion = _seccion_de_comando(comando)
        banderas = _banderas_de(comando)
        faltan = [
            b
            for b in banderas
            if b not in seccion and b not in comunes
        ]
        salida.append(
            {
                "que": f"banderas de {comando}",
                "ok": bool(seccion) and not faltan,
                "detalle": (
                    f"{len(banderas) - len(faltan)}/{len(banderas)} en su sección"
                    + (f" · faltan: {faltan}" if faltan else "")
                    if seccion
                    else "NO tiene sección"
                ),
            }
        )
    return salida


def _navegacion() -> list[dict[str, Any]]:
    """Los enlaces relativos de la guía resuelven a archivos reales."""
    roto: list[str] = []
    for nombre in DOCS_REQUERIDOS:
        for destino in _enlaces(_texto(nombre)):
            if "://" in destino or destino.startswith("#"):
                continue
            if not (DOC_USUARIO / destino).resolve().exists():
                roto.append(f"{nombre} → {destino}")
    return [
        {
            "que": "todo enlace relativo de la guía existe",
            "ok": not roto,
            "detalle": "sin enlaces rotos" if not roto else f"rotos: {roto}",
        },
        {
            "que": "cada documento vuelve al índice",
            "ok": all(
                "README.md" in _texto(nombre) for nombre in DOCS_REQUERIDOS[1:]
            ),
            "detalle": "los cuatro documentos enlazan a README.md",
        },
    ]


def _fronteras() -> list[dict[str, Any]]:
    """La doc **no promete** lo que el sistema no hace."""
    from voucherflow.cli.main import DESPACHO

    revision = _texto("03-revision-humana.md")
    comandos = _texto(REFERENCIA)
    readme = _texto("README.md")

    hay_comando_correccion = any(
        "correccion" in destino or "revision" in destino for destino in DESPACHO
    )
    declarado_ausente = (
        "no está implementado" in revision or "no existe" in revision
    )

    return [
        {
            "que": "declara que el CLI no carga correcciones HITL",
            "ok": declarado_ausente and not hay_comando_correccion,
            "detalle": (
                "la doc lo declara y el CLI no expone el comando"
                if declarado_ausente and not hay_comando_correccion
                else "la doc o el CLI se desincronizaron"
            ),
        },
        {
            "que": "declara que --force/--workers solo aplican a batch",
            "ok": "solo aplican a `batch`" in comandos
            or "solo `batch` previene" in comandos
            or "Con efecto real en `batch`" in comandos,
            "detalle": "la referencia acota el efecto de las banderas comunes",
        },
        {
            "que": "declara que un rechazo firme es certeza alta",
            "ok": "rechazo firme también es certeza alta" in readme
            or "Un **rechazo firme también es certeza alta**" in readme,
            "detalle": "el README del operador lo explicita",
        },
        {
            "que": "advierte que ask no produce evidencia auditable",
            "ok": "no** produce evidencia" in comandos.replace("\n", " ")
            or "produce evidencia ni queda auditado" in comandos.replace("\n", " "),
            "detalle": "la referencia de ask lo advierte",
        },
    ]


def _imprimir(titulo: str) -> None:
    print(f"\n{titulo}")
    print("-" * len(titulo))


def _marca(ok: bool) -> str:
    return "✅" if ok else "❌"


def main_cli() -> None:  # noqa: D401
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--detalle", action="store_true", help="Una línea por archivo y por comando."
    )
    parser.add_argument("--json", type=Path, default=None, help="Guardar el reporte.")
    args = parser.parse_args()

    reporte: dict[str, Any] = {
        "tarea": "T-605",
        "version_cli": VERSION_CLI,
        "archivos": _archivos(),
        "cobertura_comandos": _cobertura_comandos(),
        "cobertura_banderas": _cobertura_banderas(),
        "navegacion": _navegacion(),
        "fronteras": _fronteras(),
    }

    print("Documentación de usuario — F6/T-605")
    print(f"Guía del operador: {DOC_USUARIO}")

    _imprimir("Archivos de la guía")
    for item in reporte["archivos"]:
        print(f"  {_marca(item['ok'])} {item['que']:<44} {item['detalle']}")
        if args.detalle and item["lineas"]:
            print(f"       {item['lineas']} líneas")

    _imprimir("Cobertura de comandos")
    for item in reporte["cobertura_comandos"]:
        print(f"  {_marca(item['ok'])} {item['que']:<46} {item['detalle']}")
    documentados = _comandos_documentados()
    print(f"  comandos del contrato: {len(COMANDOS)}")
    print(f"  comandos con sección:  {len(documentados)}")

    _imprimir("Banderas documentadas")
    for item in reporte["cobertura_banderas"]:
        print(f"  {_marca(item['ok'])} {item['que']:<28} {item['detalle']}")

    _imprimir("Navegación")
    for item in reporte["navegacion"]:
        print(f"  {_marca(item['ok'])} {item['que']:<44} {item['detalle']}")

    _imprimir("Fronteras (lo que la doc NO promete)")
    for item in reporte["fronteras"]:
        print(f"  {_marca(item['ok'])} {item['que']}")
        if args.detalle:
            print(f"       {item['detalle']}")

    todos = (
        reporte["archivos"]
        + reporte["cobertura_comandos"]
        + reporte["cobertura_banderas"]
        + reporte["navegacion"]
        + reporte["fronteras"]
    )
    fallos = [item for item in todos if not item["ok"]]

    print(f"\nVerificaciones: {len(todos) - len(fallos)}/{len(todos)} en verde")
    if fallos:
        print("Fallos:")
        for item in fallos:
            print(f"  ❌ {item['que']}: {item['detalle']}")

    reporte["fallos"] = len(fallos)
    if args.json:
        args.json.write_text(
            json.dumps(reporte, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        print(f"Reporte guardado en {args.json}")
    sys.exit(1 if fallos else 0)


if __name__ == "__main__":
    main_cli()

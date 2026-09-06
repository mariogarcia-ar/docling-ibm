#!/usr/bin/env python
"""Inspección T-102: aplica clasificador + gate de imagen sobre fixtures.

Recorre las imágenes de ``v2/tests/fixtures`` (y opcionalmente cualquier
carpeta pasada por CLI), aplica ``clasificar()`` y ``verificar_procesabilidad()``
de ``voucherflow.processing`` y muestra:

  - un resumen agregado por clase y por resultado del gate,
  - una tabla por archivo con clase, dimensiones, ratio y veredicto.

Uso:
    python scripts/inspeccionar_t102.py [ruta...] [--detalle]

Ejemplos:
    python scripts/inspeccionar_t102.py                      # fixtures default
    python scripts/inspeccionar_t102.py tests/fixtures/golden
    python scripts/inspeccionar_t102.py files/2025-08 --detalle
    python scripts/inspeccionar_t102.py tests/fixtures/pdf_escaneados --detalle
"""

from __future__ import annotations

import argparse
import sys
from collections import Counter
from pathlib import Path

# El paquete usa layout src/; si se corre sin instalar, se agrega src/ al path.
_SRC = Path(__file__).resolve().parents[1] / "src"
if str(_SRC) not in sys.path:
    sys.path.insert(0, str(_SRC))

from voucherflow.processing.image_classifier import (  # noqa: E402
    clasificar,
    verificar_procesabilidad,
)
from voucherflow.processing.type_detector import EXTENSIONES_IMAGEN  # noqa: E402

#: Fila de la tabla por archivo.
_CABECERA = ("archivo", "clase", "ancho", "alto", "ratio", "gate", "razon")
_FMT = "{:<38} {:<14} {:>6} {:>6} {:>7}  {:<10} {}"


def _rutas_default() -> list[Path]:
    """Fixtures de imagen del golden set (copia estable en tests/fixtures)."""
    base = Path(__file__).resolve().parents[1] / "tests" / "fixtures"
    if not base.exists():
        return []
    return sorted(
        p for p in base.rglob("*") if p.suffix.lower() in EXTENSIONES_IMAGEN
    )


def _rutas_desde_cli(args: list[str]) -> list[Path]:
    """Expande las rutas pasadas por CLI a archivos de imagen."""
    rutas: list[Path] = []
    for raw in args:
        p = Path(raw)
        if p.is_dir():
            rutas.extend(
                q for q in p.rglob("*") if q.suffix.lower() in EXTENSIONES_IMAGEN
            )
        elif p.is_file() and p.suffix.lower() in EXTENSIONES_IMAGEN:
            rutas.append(p)
        else:
            print(f"⚠  Se ignora (no es imagen o no existe): {p}", file=sys.stderr)
    return sorted(set(rutas))


def _resumen(rutas: list[Path]) -> tuple[Counter, Counter, Counter]:
    """Aplica clasificador+gate y devuelve contadores agregados."""
    clases: Counter = Counter()
    gates: Counter = Counter()
    razones: Counter = Counter()
    for p in rutas:
        c = clasificar(p)
        v = verificar_procesabilidad(p)
        clases[c.clase.value] += 1
        gates["procesable" if v.procesable else "rechazada"] += 1
        razones[v.razon_rechazo or "(ok)"] += 1
    return clases, gates, razones


def _detalle(rutas: list[Path]) -> None:
    """Imprime una fila por archivo."""
    print(_FMT.format(*_CABECERA))
    print(_FMT.format(*("─" * 38, "─" * 14, "─" * 6, "─" * 6, "─" * 7, "─" * 10, "─" * 24)))
    for p in rutas:
        c = clasificar(p)
        v = verificar_procesabilidad(p)
        ch = c.caracteristicas
        gate = "✅ procesable" if v.procesable else "❌ rechazada"
        detalle = v.razon_rechazo or v.motivo
        print(
            _FMT.format(
                p.name,
                c.clase.value,
                ch.ancho or "-",
                ch.alto or "-",
                f"{ch.ratio:.2f}" if ch.ratio else "-",
                gate,
                detalle[:52],
            )
        )


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Inspección T-102: clasificador + gate sobre imágenes de fixtures."
    )
    parser.add_argument("rutas", nargs="*", help="Archivos/carpetas de imagen (default: tests/fixtures).")
    parser.add_argument("--detalle", action="store_true", help="Muestra una fila por archivo.")
    args = parser.parse_args()

    rutas = _rutas_desde_cli(args.rutas) if args.rutas else _rutas_default()
    if not rutas:
        print("No se encontraron imágenes para inspeccionar.", file=sys.stderr)
        return

    print(f"Imágenes a inspeccionar: {len(rutas)}\n")

    clases, gates, razones = _resumen(rutas)

    print("== Resumen por clase ==")
    for clase, n in clases.most_common():
        print(f"  {clase:<16}: {n}")
    print("\n== Resumen del gate ==")
    for g, n in gates.most_common():
        print(f"  {g:<12}: {n}")
    print("\n== Razones del gate ==")
    for razon, n in razones.most_common():
        print(f"  {razon:<24}: {n}")

    if args.detalle:
        print("\n== Detalle por archivo ==")
        _detalle(rutas)

    # Código de salida útil: 1 si hubo rechazos inesperados (para CI/QA).
    n_rechazadas = gates.get("rechazada", 0)
    if n_rechazadas:
        print(f"\n⚠  {n_rechazadas} imagen(es) rechazada(s) por el gate.")
        sys.exit(2)


if __name__ == "__main__":
    main()

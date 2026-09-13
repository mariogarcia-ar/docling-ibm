#!/usr/bin/env python
"""Procesa archivos/carpetas aplicando T-102 (gate + clasificador de imagen).

Para cada **imagen** de las rutas pasadas, aplica ``clasificar()`` y
``verificar_procesabilidad()`` de ``voucherflow.processing`` (T-102) y
muestra la clase (foto/escaneo_plano/screenshot/manuscrito), si supera el
gate de procesabilidad y, si procede, la sugerencia de motor (T-104).

Solo las imágenes directas entran al clasificador/gate: un ``pdf_escaneado``
se convierte a imagen en la orquestación y NO se le aplica T-102 aquí
(ver ``inspeccionar_t103.py`` / orquestación).

Uso:
    python scripts/F1/t102.py <archivo|carpeta>...

Ejemplos:
    python scripts/F1/t102.py tests/fixtures/golden/2991f57d-*.jpg
    python scripts/F1/t102.py tests/fixtures/golden
    python scripts/F1/t102.py ../files/2025-08/2D2C9343
    python scripts/F1/t102.py mi.jpg otro.png docs/
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

# El paquete usa layout src/; si se corre sin instalar, se agrega src/ al path.
_SRC = Path(__file__).resolve().parents[2] / "src"
if str(_SRC) not in sys.path:
    sys.path.insert(0, str(_SRC))

from voucherflow.processing.image_classifier import (  # noqa: E402
    clasificar,
    sospechar_manuscrito,
    verificar_procesabilidad,
)
from voucherflow.processing.type_detector import (  # noqa: E402
    EXTENSIONES_IMAGEN,
    detectar,
)


def _expandir(args: list[str]) -> list[Path]:
    """Expande archivos/carpetas de la CLI a una lista de imágenes."""
    rutas: list[Path] = []
    for raw in args:
        p = Path(raw)
        if p.is_dir():
            rutas.extend(
                q for q in sorted(p.rglob("*"))
                if q.is_file() and q.suffix.lower() in EXTENSIONES_IMAGEN
            )
        elif p.is_file() and p.suffix.lower() in EXTENSIONES_IMAGEN:
            rutas.append(p)
        elif p.is_file():
            # Existe pero no es imagen: se informa por qué se saltea (solo las
            # imágenes directas entran al gate/clasificador T-102).
            print(f"⚠  Se ignora (no es imagen): {p}", file=sys.stderr)
        else:
            print(f"⚠  Se ignora (no existe): {p}", file=sys.stderr)
    return sorted(set(rutas))


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Aplica gate de procesabilidad + clasificador de imagen (F1 / T-102)."
    )
    parser.add_argument("rutas", nargs="+", help="Imágenes o carpetas con imágenes a procesar.")
    args = parser.parse_args()

    archivos = _expandir(args.rutas)
    if not archivos:
        print("No se encontraron imágenes para procesar.", file=sys.stderr)
        sys.exit(2)

    print(f"Imágenes a procesar: {len(archivos)}\n")

    n_ok = n_rechazadas = 0
    for archivo in archivos:
        try:
            # T-101: confirmar que es una imagen directa (solo estas entran a T-102).
            te = detectar(archivo)
            if te.tipo != "imagen":
                print(f"  {archivo.name[:40]:42} tipo={te.tipo} (no entra a T-102: {te.motivo})")
                continue

            c = clasificar(archivo)
            v = verificar_procesabilidad(archivo)
        except Exception as exc:
            print(f"❌ {archivo.name}: error {type(exc).__name__}: {exc}")
            continue

        gate = "✅ procesable" if v.procesable else "❌ rechazada"
        if v.procesable:
            n_ok += 1
        else:
            n_rechazadas += 1

        ch = c.caracteristicas
        dims = f"{ch.ancho}x{ch.alto}" if ch.ancho and ch.alto else "dim.?"
        print(f"  {archivo.name[:40]:42} clase={c.clase.value:<14} {dims:>12}  {gate}")
        if c.motivo:
            print(f"      → {c.motivo}")
        if v.razon_rechazo:
            print(f"      → gate: {v.razon_rechazo} — {v.motivo}")
        elif v.motivo and v.motivo != c.motivo:
            print(f"      → gate: {v.motivo}")
        # Hook manuscrito: señal para que T-104 decida motor VLM (doc 02 E-DOC-2).
        if sospechar_manuscrito(c):
            print(f"      → ⚠  posible manuscrito/sello/firma: priorizar motor VLM (T-104)")

    print(f"\nResumen: {n_ok} procesable(s), {n_rechazadas} rechazada(s) por el gate.")

    # Código de salida útil para CI/QA: 1 si alguna imagen no supera el gate.
    if n_rechazadas:
        sys.exit(1)


if __name__ == "__main__":
    main()

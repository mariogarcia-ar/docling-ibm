#!/usr/bin/env python
"""Procesa archivos/carpetas aplicando el detector T-101.

Para cada archivo (o cada archivo de las carpetas pasadas), aplica
``voucherflow.processing.detectar()`` (T-101) y muestra tipo de entrada y
si requiere ruta de OCR.

Uso:
    python scripts/F1/t101.py <archivo|carpeta>...

Ejemplos:
    python scripts/F1/t101.py tests/fixtures/golden/2991f57d-*.jpg
    python scripts/F1/t101.py ../files/2025-08
    python scripts/F1/t101.py mi.pdf otro.png docs/
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

# El paquete usa layout src/; si se corre sin instalar, se agrega src/ al path.
_SRC = Path(__file__).resolve().parents[2] / "src"
if str(_SRC) not in sys.path:
    sys.path.insert(0, str(_SRC))

from voucherflow.processing.type_detector import detectar  # noqa: E402


def _expandir(args: list[str]) -> list[Path]:
    """Expande archivos/carpetas de la CLI a una lista de archivos."""
    rutas: list[Path] = []
    for raw in args:
        p = Path(raw)
        if p.is_dir():
            rutas.extend(q for q in sorted(p.rglob("*")) if q.is_file())
        elif p.is_file():
            rutas.append(p)
        else:
            print(f"⚠  Se ignora (no existe): {p}", file=sys.stderr)
    return sorted(set(rutas))


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Aplica el detector de tipo de entrada (F1 / T-101) a archivos."
    )
    parser.add_argument("rutas", nargs="+", help="Archivos o carpetas a detectar.")
    args = parser.parse_args()

    archivos = _expandir(args.rutas)
    if not archivos:
        print("No se encontraron archivos.", file=sys.stderr)
        sys.exit(2)

    print(f"Archivos a detectar: {len(archivos)}\n")
    for archivo in archivos:
        try:
            r = detectar(archivo)
        except Exception as exc:
            print(f"❌ {archivo.name}: error {type(exc).__name__}: {exc}")
            continue
        ruta = "ocr" if r.ruta_ocr else "texto_nativo"
        print(f"  {archivo.name[:40]:42} tipo={r.tipo:<14} ruta={ruta}")
        if r.motivo:
            print(f"      → {r.motivo}")


if __name__ == "__main__":
    main()


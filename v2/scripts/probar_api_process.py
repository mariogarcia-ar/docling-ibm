#!/usr/bin/env python
"""Prueba manual de ``voucherflow.api.process`` con documentos reales.

Procesa uno o más archivos con la fachada pública (F1 / T-105/ORQ) y muestra
los metadatos del ``ProcessedDocument`` resultante + un vistazo del markdown.
Uso: python scripts/probar_api_process.py <archivo> [<archivo>...]
"""
from __future__ import annotations

import sys
import time
from pathlib import Path

_SRC = Path(__file__).resolve().parents[1] / "src"
if str(_SRC) not in sys.path:
    sys.path.insert(0, str(_SRC))

from voucherflow.api import DocumentoNoProcesableError, process  # noqa: E402


def main() -> None:
    if len(sys.argv) < 2:
        print("Uso: python scripts/probar_api_process.py <archivo> [...]")
        sys.exit(2)

    for raw in sys.argv[1:]:
        ruta = Path(raw)
        print("=" * 70)
        print(f"Archivo: {ruta}  ({ruta.stat().st_size} bytes)")
        t0 = time.time()
        try:
            doc = process(str(ruta))
        except (DocumentoNoProcesableError, ValueError, FileNotFoundError) as exc:
            print(f"  ❌ {type(exc).__name__}: {exc}")
            continue
        dt = time.time() - t0

        print(f"  ✅ procesado en {dt:.1f}s")
        print(f"     tipo_entrada : {doc.tipo_entrada}")
        print(f"     ruta         : {doc.ruta}")
        print(f"     orientacion  : {doc.orientacion}")
        print(f"     motor        : {doc.motor}")
        print(f"     n_items      : {doc.n_items}")
        print(f"     n_boxes      : {len(doc.boxes)}")
        print(f"     calidad      : {doc.calidad}")
        md = (doc.markdown or "").strip()
        print(f"     markdown     : {len(md)} chars")
        print("-" * 70)
        print(md[:1200] if md else "(sin markdown)")
        print()


if __name__ == "__main__":
    main()

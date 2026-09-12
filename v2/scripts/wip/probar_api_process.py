#!/usr/bin/env python
"""Prueba manual de ``voucherflow.api.process`` con documentos reales.

Procesa uno o más archivos con la fachada pública (F1 / T-105/ORQ) y muestra
los metadatos del ``ProcessedDocument`` resultante + un vistazo del markdown.

Uso:
    python scripts/probar_api_process.py <archivo> [<archivo>...] [--docling-raw]

Opciones:
    --docling-raw  Devuelve en ``markdown`` el crudo de Docling (sin reordenar
                   por posición); equivale a ``v1/run_raw.py`` (Opción A,
                   subplan F1 §2.5). Default: markdown ordenado / política
                   combinada (contrato F2/F3/F4).

python scripts/probar_api_process.py 'tests/fixtures/golden/9dfc597f-34c5-41ec-99ae-cf35544c7af8.pdf' --docling-raw

"""
from __future__ import annotations

import argparse
import sys
import time
from pathlib import Path

_SRC = Path(__file__).resolve().parents[1] / "src"
if str(_SRC) not in sys.path:
    sys.path.insert(0, str(_SRC))

from voucherflow.api import DocumentoNoProcesableError, process  # noqa: E402


def main() -> None:
    parser = argparse.ArgumentParser(
        prog="probar_api_process.py",
        description="Prueba manual de voucherflow.api.process con documentos reales.",
    )
    parser.add_argument(
        "archivos",
        nargs="+",
        metavar="archivo",
        help="ruta(s) al documento a procesar (pdf/imagen/office/txt/...).",
    )
    parser.add_argument(
        "--docling-raw",
        action="store_true",
        help="Devuelve el markdown crudo de Docling (sin reordenar por "
             "posición); equivale a v1/run_raw.py.",
    )
    args = parser.parse_args()

    for raw in args.archivos:
        ruta = Path(raw)
        print("=" * 70)
        print(f"Archivo: {ruta}  ({ruta.stat().st_size} bytes)")
        t0 = time.time()
        try:
            doc = process(str(ruta), docling_raw=args.docling_raw)
        except (DocumentoNoProcesableError, ValueError, FileNotFoundError) as exc:
            print(f"  ❌ {type(exc).__name__}: {exc}")
            continue
        dt = time.time() - t0

        print(f"  ✅ procesado en {dt:.1f}s")
        modo = "RAW Docling" if args.docling_raw else "ordenado/política combinada"
        print(f"     modo         : {modo}")
        if args.docling_raw:
            print("     aviso        : el markdown es el crudo de Docling "
                  "(sin reordenar por posición; puede no estar alineado con boxes)")
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

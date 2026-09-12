#!/usr/bin/env python
"""Detecta PDFs que solo contienen imágenes (escaneados) usando PyMuPDF.

Inspecciona cada página de un PDF: si **no tiene capa de texto** y contiene
imágenes (típicamente 1 imagen = página escaneada), la página se marca como
"solo imagen". Clasifica el PDF completo según sus páginas:

  - ``solo_imagen``  : TODAS las páginas son solo imagen (PDF escaneado).
  - ``con_texto``    : TODAS las páginas tienen texto.
  - ``mixto``        : algunas páginas son solo imagen y otras tienen texto.

Esto complementa al detector heurístico de T-101 (que mira los bytes /Font vs
/Subtype /Image) con un análisis **por página** y real del contenido.

Uso:
    python scripts/detectar_pdf_escaneados.py [ruta...] [--detalle] [--solo]

Ejemplos:
    python scripts/detectar_pdf_escaneados.py                  # tests/fixtures
    python scripts/detectar_pdf_escaneados.py ../files
    python scripts/detectar_pdf_escaneados.py ../files --solo  # solo lista escaneados
    python scripts/detectar_pdf_escaneados.py archivo.pdf --detalle

Requiere PyMuPDF:  python -m pip install pymupdf
"""

from __future__ import annotations

import argparse
import sys
from collections import Counter
from pathlib import Path

try:
    import pymupdf as fitz  # PyMuPDF
except ImportError as exc:  # pragma: no cover
    sys.exit(
        "Falta PyMuPDF. Instalalo con:  python -m pip install pymupdf\n"
        f"(detalle: {exc})"
    )


def _clasificar_pagina(pagina: "fitz.Page") -> str:
    """Clasifica una página: 'solo_imagen' | 'con_texto' | 'solo_texto_sin_img'.

    Criterio (según lo pedido): si no hay texto y hay exactamente 1 imagen →
    página escaneada de una sola imagen. Si no hay texto y hay varias imágenes
    → también se considera escaneada (''varias_imagenes''). Si hay texto →
    ''con_texto''.
    """
    texto = pagina.get_text().strip()
    imagenes = pagina.get_images(full=True)
    if not texto and len(imagenes) == 1:
        return "solo_imagen"
    if not texto and len(imagenes) > 1:
        return "solo_imagen_varias"
    if not texto and len(imagenes) == 0:
        return "pagina_vacia"
    return "con_texto"


def _clasificar_pdf(ruta: Path) -> tuple[str, list[tuple[int, str, int]]]:
    """Clasifica un PDF completo; devuelve (tipo, detalle por página).

    ``tipo``: 'solo_imagen' | 'con_texto' | 'mixto' | 'error'.
    Detalle: lista de (n_pagina, clase_pagina, n_imagenes) 1-indexada.
    """
    detalle: list[tuple[int, str, int]] = []
    try:
        doc = fitz.open(str(ruta))
    except Exception as exc:
        return "error", [(0, f"no_abre:{type(exc).__name__}", 0)]

    try:
        for numero, pagina in enumerate(doc):
            clase = _clasificar_pagina(pagina)
            n_imgs = len(pagina.get_images(full=True))
            detalle.append((numero + 1, clase, n_imgs))
    finally:
        doc.close()

    clases = {c for _, c, _ in detalle}
    if not clases:
        return "vacio", detalle
    if clases <= {"solo_imagen", "solo_imagen_varias"}:
        return "solo_imagen", detalle
    if clases <= {"con_texto"}:
        return "con_texto", detalle
    if clases <= {"con_texto", "pagina_vacia"}:
        return "con_texto", detalle
    return "mixto", detalle


def _rutas_default() -> list[Path]:
    """PDFs de fixtures (copia estable en tests/fixtures)."""
    base = Path(__file__).resolve().parents[1] / "tests" / "fixtures"
    if not base.exists():
        return []
    return sorted(base.rglob("*.pdf"))


def _rutas_desde_cli(args: list[str]) -> list[Path]:
    rutas: list[Path] = []
    for raw in args:
        p = Path(raw)
        if p.is_dir():
            rutas.extend(q for q in p.rglob("*.pdf") if q.is_file())
        elif p.is_file() and p.suffix.lower() == ".pdf":
            rutas.append(p)
        else:
            print(f"⚠  Se ignora (no es PDF o no existe): {p}", file=sys.stderr)
    return sorted(set(rutas))


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Detecta PDFs de solo imagen (escaneados) con PyMuPDF."
    )
    parser.add_argument("rutas", nargs="*", help="Archivos/carpetas PDF (default: tests/fixtures).")
    parser.add_argument("--detalle", action="store_true", help="Muestra el detalle por página.")
    parser.add_argument("--solo", action="store_true", help="Lista solo los PDFs escaneados (solo_imagen).")
    args = parser.parse_args()

    rutas = _rutas_desde_cli(args.rutas) if args.rutas else _rutas_default()
    if not rutas:
        print("No se encontraron PDFs.", file=sys.stderr)
        return

    print(f"PDFs a analizar: {len(rutas)}\n")

    conteo: Counter = Counter()
    escaneados: list[Path] = []

    for ruta in rutas:
        tipo, detalle = _clasificar_pdf(ruta)
        conteo[tipo] += 1
        if tipo == "solo_imagen":
            escaneados.append(ruta)

        if args.detalle:
            marca = {"solo_imagen": "📄 SOLO IMAGEN", "solo_imagen_varias": "📄 solo img (varias)",
                     "con_texto": "📝 con texto", "mixto": "🔀 mixto"}.get(tipo, tipo)
            print(f"{marca:<22} {ruta}")
            if tipo in ("mixto",) or args.detalle:
                for n, clase, n_img in detalle:
                    if clase != "con_texto":
                        print(f"    pág {n:>3}: {clase:<20} imágenes={n_img}")
        elif args.solo and tipo == "solo_imagen":
            print(f"  {ruta}")

    if not args.solo:
        print("\n== Resumen ==")
        for tipo, n in conteo.most_common():
            print(f"  {tipo:<14}: {n}")
        print(f"\nPDFs escaneados (solo imagen): {len(escaneados)}")
        if not args.detalle:
            print("\nLista de escaneados (--solo para ver solo esto):")
            for p in escaneados:
                print(f"  {p}")


if __name__ == "__main__":
    main()

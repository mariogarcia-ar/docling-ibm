#!/usr/bin/env python
"""Detecta si un PDF es APTO para extraerle texto con ``pdftotext --layout``.

Es el **opuesto** de ``detectar_pdf_escaneados.py``: ahí se buscaba el escenario
"una sola imagen por página (escaneo)"; acá se busca que el texto nativo real
**supere a la imagen** y que ese texto sea de calidad, porque así la disposición
visual (columnas, tablas, campos alineados) se puede recuperar con ``--layout``.

Qué decide por página (en orden de precedencia):

  - ``vacia``        → sin texto y sin imágenes (página en blanco).
  - ``escaneada``    → sin texto real y con imagen(es): la página es un escaneo.
  - ``corrupta``     → con texto, pero > 10% de caracteres de control raros
                       (fuentes mal codificadas / ToUnicode roto).
  - ``apta_layout``  → con texto real suficiente y las imágenes NO dominan el
                       área de la página. Es el caso "vale la pena --layout".

Veredicto por PDF:

  - ``apto``          : TODAS las páginas son ``apta_layout`` (o algún ``vacia``
                        de relleno): usar ``pdftotext --layout``.
  - ``parcial``       : mezcla de páginas aptas y escaneadas/corruptas.
  - ``requiere_ocr``  : TODAS las páginas son escaneadas/corruptas/vacías: el
                        texto no se puede recuperar con pdftotext; aplicar OCR.

Umbrales (calibrados 2026-09-06 contra ``v2/tests/fixtures``):
  - caracteres raros: ``ratio > 0.10`` (mismo umbral que el script de referencia).
  - una página "escaneada" puede tener texto residual (p. ej. marcas de agua):
    se considera escaneada si ``len(texto) < TEXTO_MINIMO_CHARS`` (40) o si el
    texto es despreciable frente a un mínimo por área.
  - cobertura de imágenes por página: ``> COBERTURA_IMAGEN_MAX`` (60 %) y texto
    por área < ``TEXTO_AREA_MIN`` (0.15 chars/punto²) → la imagen domina.

Uso:
    python scripts/detectar_aptos_pdftotext_layout.py [ruta...] [--detalle]
    python scripts/detectar_aptos_pdftotext_layout.py [ruta...] --solo-aptos

Ejemplos:
    python scripts/detectar_aptos_pdftotext_layout.py tests/fixtures
    python scripts/detectar_aptos_pdftotext_layout.py archivo.pdf
    python scripts/detectar_aptos_pdftotext_layout.py ../files --detalle
    python scripts/detectar_aptos_pdftotext_layout.py ../files --solo-aptos

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

# ---------------------------------------------------------------------------
# Umbrales
# ---------------------------------------------------------------------------

#: Máxima proporción de caracteres de control "raros" (no salto/línea/tab) para
#: considerar el texto legible (mismo umbral que el script de referencia).
RATIO_RAROS_MAX = 0.10

#: Debajo de esta cantidad de caracteres una página "con texto" se trata como
#: escaneada (texto residual: marcas de agua, sellos, ruido del escaneo).
TEXTO_MINIMO_CHARS = 40

#: Si la cobertura de imágenes supera esto y el texto por área es bajo, la
#: página es escaneada aunque tenga algo de texto.
COBERTURA_IMAGEN_MAX = 0.60  # 60 % del área de la página

#: Texto por unidad de área (chars / punto²) por debajo del cual, sumado a alta
#: cobertura de imagen, se considera que la imagen domina.
TEXTO_AREA_MIN = 0.15


def _caracteres_raros(texto: str) -> int:
    """Cuenta caracteres de control que no son salto/línea/tab (fuentes rotas)."""
    return sum(1 for c in texto if ord(c) < 32 and c not in "\n\r\t ")


def _cobertura_imagenes(pagina: "fitz.Page") -> float:
    """Fracción (0–1) del área de la página cubierta por imágenes dibujadas."""
    area_img = 0.0
    for info in pagina.get_image_info():
        r = fitz.Rect(info["bbox"])
        area_img += r.width * r.height
    area_pag = pagina.rect.width * pagina.rect.height
    return (area_img / area_pag) if area_pag else 0.0


def _clasificar_pagina(pagina: "fitz.Page") -> tuple[str, dict]:
    """Clasifica una página; devuelve (clase, métricas).

    Clases: 'apta_layout' | 'escaneada' | 'vacia' | 'corrupta'.
    """
    texto = pagina.get_text()
    texto_limpio = texto.strip()
    n_chars = len(texto_limpio)
    n_imagenes = len(pagina.get_images(full=True))
    n_raros = _caracteres_raros(texto)
    cobertura = _cobertura_imagenes(pagina)
    area_pag = pagina.rect.width * pagina.rect.height
    texto_por_area = (n_chars / area_pag) if area_pag else 0.0

    metricas = {
        "chars": n_chars,
        "imagenes": n_imagenes,
        "raros": n_raros,
        "cobertura_img": cobertura,
        "texto_por_area": texto_por_area,
    }

    # 1. Sin texto y sin imagen → página en blanco.
    if n_chars == 0 and n_imagenes == 0:
        return "vacia", metricas

    # 2. Control de calidad: texto con demasiados caracteres corruptos.
    if n_chars > 0 and (n_raros / n_chars) > RATIO_RAROS_MAX:
        return "corrupta", metricas

    # 3. Texto despreciable (escaneo) o la imagen domina el área → escaneada.
    if n_chars < TEXTO_MINIMO_CHARS:
        return "escaneada", metricas
    if cobertura > COBERTURA_IMAGEN_MAX and texto_por_area < TEXTO_AREA_MIN:
        return "escaneada", metricas

    # 4. Texto real suficiente y las imágenes no dominan → apta para --layout.
    return "apta_layout", metricas


def _clasificar_pdf(ruta: Path) -> tuple[str, list[tuple[int, str, dict]], str]:
    """Clasifica un PDF completo; devuelve (veredicto, detalle, resumen).

    ``veredicto``: 'apto' | 'parcial' | 'requiere_ocr' | 'error'.
    Detalle: lista de (n_pagina, clase, métricas) 1-indexada.
    """
    detalle: list[tuple[int, str, dict]] = []
    try:
        doc = fitz.open(str(ruta))
    except Exception as exc:
        return "error", [], f"No se pudo abrir: {type(exc).__name__}"

    try:
        for numero, pagina in enumerate(doc):
            clase, metricas = _clasificar_pagina(pagina)
            detalle.append((numero + 1, clase, metricas))
    finally:
        doc.close()

    clases = {c for _, c, _ in detalle}
    if not clases or clases <= {"vacia"}:
        return "requiere_ocr", detalle, "PDF sin contenido (todas las páginas vacías)."
    if clases <= {"apta_layout", "vacia"}:
        return "apto", detalle, (
            "Todo el texto es nativo y las imágenes no dominan: usar pdftotext --layout."
        )
    if clases <= {"escaneada", "corrupta", "vacia"}:
        return "requiere_ocr", detalle, (
            "Sin texto nativo aprovechable: aplicar OCR (p. ej. Tesseract/Docling)."
        )
    return "parcial", detalle, (
        "Mezcla de páginas aptas y escaneadas/corruptas: revisar el detalle por página."
    )


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


_MARCA_CLASE = {
    "apta_layout": "✅ APTA  --layout",
    "escaneada": "🖼  escaneada",
    "corrupta": "⚠  corrupta",
    "vacia": "⬜ vacía",
}


def main() -> None:
    parser = argparse.ArgumentParser(
        description=(
            "Detecta PDFs APTOS para pdftotext --layout "
            "(texto nativo real por encima de la imagen)."
        )
    )
    parser.add_argument(
        "rutas", nargs="*", help="Archivos/carpetas PDF (default: tests/fixtures)."
    )
    parser.add_argument(
        "--detalle", action="store_true", help="Muestra métricas por página."
    )
    parser.add_argument(
        "--solo-aptos", action="store_true", help="Lista solo los PDFs aptos (apto)."
    )
    args = parser.parse_args()

    rutas = _rutas_desde_cli(args.rutas) if args.rutas else _rutas_default()
    if not rutas:
        print("No se encontraron PDFs.", file=sys.stderr)
        return

    print(f"PDFs a analizar: {len(rutas)}\n")

    conteo: Counter = Counter()
    aptos: list[Path] = []

    for ruta in rutas:
        veredicto, detalle, resumen = _clasificar_pdf(ruta)
        conteo[veredicto] += 1
        if veredicto == "apto":
            aptos.append(ruta)

        if veredicto == "error":
            print(f"❌ {ruta}: {resumen}")
            continue

        marca = {
            "apto": "✅ APTO  pdftotext --layout",
            "parcial": "🔀 PARCIAL",
            "requiere_ocr": "🖼  REQUIERE OCR",
        }.get(veredicto, veredicto)

        if args.solo_aptos:
            if veredicto == "apto":
                print(f"  {ruta}")
            continue

        print(f"{marca:<24} {ruta}  —  {resumen}")
        if veredicto != "apto" or args.detalle:
            for n, clase, m in detalle:
                extra = f"chars={m['chars']} img={m['imagenes']}"
                if m["raros"]:
                    extra += f" raros={m['raros']}"
                if m["cobertura_img"]:
                    extra += f" cobertura={100 * m['cobertura_img']:.0f}%"
                print(f"    pág {n:>3}: {_MARCA_CLASE.get(clase, clase):<20} {extra}")

    if not args.solo_aptos:
        print("\n== Resumen ==")
        for veredicto, n in conteo.most_common():
            print(f"  {veredicto:<14}: {n}")
        print(f"\nPDFs aptos para 'pdftotext --layout': {len(aptos)}")
        if not args.detalle:
            print("\nLista de aptos (--solo-aptos para ver solo esto):")
            for p in aptos:
                print(f"  {p}")


if __name__ == "__main__":
    main()

#!/usr/bin/env python
"""Procesa archivos/carpetas aplicando T-104 (motor OCR/VLM + exportador).

Para cada archivo de las rutas pasadas (imagen/pdf/office/texto), aplica la
cadena completa de F1 hasta T-104 y muestra el **motor efectivo** y el
**markdown de salida**:

  1. T-101 ``detectar()``: tipo de entrada (imagen/pdf_texto/pdf_escaneado/...).
  2. En imágenes: T-102 ``clasificar()`` + ``verificar_procesabilidad()``
     (gate) → clase.
  3. T-103 ``evaluar_calidad()`` → calidad; y orientación por boxes.
  4. Ruta por tipo (orquestación T-105/ORQ, PROC.md §5):
       - PDF **apto** (routing) → ``pdftotext --layout`` preferido
         (recupera columnas que Docling aplana) con fallback a Docling
         directo si no hay poppler (decisión 2026-09-07, subplan F1 §2.6).
       - PDF escaneado/parcial → render→imagen→OCR (Docling).
       - Imagen directa → T-104 ``elegir_motor()`` (ocr/vlm/auto; en F1 el
         motor efectivo es Docling, subplan §2.2 — no se llama a Ollama).
       - Office/texto → Docling directo (texto nativo).
  5. T-104 ``exportar_documento()`` → Markdown final (política combinada:
     conserva tablas del crudo + ordena texto por posición), o el crudo de
     Docling con ``--raw`` (equiv. ``v1/run_raw.py``).

Uso:
    python scripts/F1/t104.py <archivo|carpeta>... [--motor MODO] [--raw]
    python scripts/F1/t104.py <archivo|carpeta>... [--outdir DIR] [--print]

Ejemplos:
    python scripts/F1/t104.py tests/fixtures/golden/2991f57d-*.jpg
    python scripts/F1/t104.py tests/fixtures/golden
    python scripts/F1/t104.py tests/fixtures/pdf_aptos_layout/*.pdf --outdir /tmp/md
    python scripts/F1/t104.py tests/fixtures/pdf_escaneados/*.pdf --print
    python scripts/F1/t104.py tests/fixtures/golden/2991f57d-*.jpg --motor vlm --print
    python scripts/F1/t104.py tests/fixtures/golden/2991f57d-*.jpg --raw

Nota: convierte con Docling real (descarga modelos la 1ra vez, lento) o usa
pdftotext (poppler) para PDF apto. Para la suite esto es
``@pytest.mark.integration``; acá es una herramienta de uso manual.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

# El paquete usa layout src/; si se corre sin instalar, se agrega src/ al path.
_SRC = Path(__file__).resolve().parents[2] / "src"
if str(_SRC) not in sys.path:
    sys.path.insert(0, str(_SRC))

from voucherflow.models.docling import DoclingConverter  # noqa: E402
from voucherflow.processing.image_classifier import (  # noqa: E402
    clasificar,
    verificar_procesabilidad,
)
from voucherflow.processing.markdown_exporter import exportar_documento  # noqa: E402
from voucherflow.processing.ocr import MODOS_VALIDOS, elegir_motor  # noqa: E402
from voucherflow.processing.orientation import detectar_orientacion  # noqa: E402
from voucherflow.processing.orquestacion import procesar_documento  # noqa: E402
from voucherflow.processing.preprocessing import evaluar_calidad  # noqa: E402
from voucherflow.processing.type_detector import (  # noqa: E402
    EXTENSIONES_IMAGEN,
    EXTENSIONES_OFFICE,
    EXTENSIONES_TEXTO,
    detectar,
)

#: Extensiones procesables con Docling (imagen/pdf/office/texto).
_EXTENSIONES_PROCESABLES = (
    EXTENSIONES_IMAGEN | {".pdf"} | EXTENSIONES_OFFICE | EXTENSIONES_TEXTO
)

_FMT = "{:<34} {:<13} {:>5} {:>5}  {:<9} {:<7} {:>6}  {}"
_CABECERA = ("archivo", "tipo", "clase", "gate", "calidad", "motor", "orient", "chars")


def _expandir(args: list[str]) -> list[Path]:
    """Expande archivos/carpetas de la CLI a una lista de archivos procesables."""
    rutas: list[Path] = []
    for raw in args:
        p = Path(raw)
        if p.is_dir():
            rutas.extend(
                q for q in sorted(p.rglob("*"))
                if q.is_file() and q.suffix.lower() in _EXTENSIONES_PROCESABLES
            )
        elif p.is_file() and p.suffix.lower() in _EXTENSIONES_PROCESABLES:
            rutas.append(p)
        elif p.is_file():
            print(f"⚠  Se ignora (no procesable o no existe): {p}", file=sys.stderr)
        else:
            print(f"⚠  Se ignora (no existe): {p}", file=sys.stderr)
    return sorted(set(rutas))


def _procesar_archivo(
    archivo: Path,
    *,
    modo_motor: str,
    docling_raw: bool,
) -> dict:
    """Procesa un archivo con la cadena F1 hasta T-104; devuelve la fila+markdown.

    Ruteo por tipo (PROC.md §5 / orquestación T-105/ORQ):
      - PDF → ``procesar_documento`` (decide por página con ``routing``): apto
        usa ``pdftotext --layout`` si hay poppler (motor ``pdftotext``) con
        fallback a Docling; escaneado/parcial renderiza a imagen + Docling.
      - Imagen directa → gate/clase T-102 + calidad T-103 + motor T-104
        (``elegir_motor``) + exportador T-104 (Docling real).
      - Office/texto → Docling directo (texto nativo) + exportador.
    """
    fila: dict = {
        "archivo": archivo.name,
        "tipo": "-", "clase": "-", "gate": "-", "calidad": "-",
        "motor": "-", "orient": "-", "chars": 0, "markdown": "",
    }
    tipo = detectar(archivo).tipo
    fila["tipo"] = tipo

    # PDF con cualquier veredicto (apto/requiere_ocr/parcial): la orquestación
    # resuelve la ruta por página (T-105/ORQ). En PDF apto usa pdftotext
    # --layout (si hay poppler) y expone el motor efectivo ("pdftotext" o
    # "docling"); en escaneado/parcial renderiza a imagen + Docling.
    if archivo.suffix.lower() == ".pdf":
        doc = procesar_documento(
            archivo, modo_motor=modo_motor, docling_raw=docling_raw
        )
        fila["clase"] = "(pdf)"
        fila["motor"] = str(doc.motor or "docling")
        fila["orient"] = doc.orientacion or "-"
        fila["markdown"] = doc.markdown
        fila["chars"] = len(doc.markdown)
        return fila

    # Imagen directa: T-102 gate + clase ANTES de gastar OCR (doc 00: el gate
    # evita invertir recursos en imágenes que no pueden leerse). Si no pasa el
    # gate, NO se convierte con Docling (igual que procesar_imagen).
    cl = clasificar(archivo) if tipo == "imagen" else None
    if cl is not None:
        v = verificar_procesabilidad(archivo)
        q = evaluar_calidad(cl)
        fila["clase"] = cl.clase.value
        fila["gate"] = "ok" if v.procesable else "rechazada"
        fila["calidad"] = ",".join(q.acciones) if q.acciones else "-"
        fila["motor"] = elegir_motor(cl, modo=modo_motor).value
        if not v.procesable:
            # Gate rechaza (E-DOC-2 / doc 03 §4.1: GATE -->|no pasa| REJ): sin
            # OCR. Se deja la fila sin markdown para no guardar un .md vacío.
            fila["orient"] = "-"
            fila["chars"] = 0
            return fila

    # Imagen que superó el gate / office / texto: Docling directo + cadena
    # T-104 (exportador ordenado o crudo con --raw).
    converter = DoclingConverter()
    doc = converter.convert(archivo)

    if cl is None:
        # office/texto: texto nativo; motor Docling (sin gate de imagen).
        fila["motor"] = "docling"

    # T-103 orientación (para reportar) y T-104 exportación ordenada.
    fila["orient"] = detectar_orientacion(doc.boxes)
    fila["markdown"] = (
        doc.markdown if docling_raw else exportar_documento(doc)
    )  # raw = crudo de Docling (equiv. v1/run_raw.py)
    fila["chars"] = len(fila["markdown"])
    return fila


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Aplica selección de motor OCR/VLM + exportador ordenado (F1 / T-104)."
    )
    parser.add_argument("rutas", nargs="+", help="Archivos o carpetas (imagen/pdf/office/texto).")
    parser.add_argument("--motor", default="auto", choices=sorted(MODOS_VALIDOS),
                        help="Modo de selección de motor (default: auto).")
    parser.add_argument("--raw", action="store_true",
                        help="Markdown crudo de Docling (sin ordenar por posición; equiv. run_raw).")
    parser.add_argument("--outdir", help="Carpeta de salida para los .md (default: junto a cada archivo).")
    parser.add_argument("--print", action="store_true", help="Además de guardar, imprime el markdown.")
    args = parser.parse_args()

    archivos = _expandir(args.rutas)
    if not archivos:
        print("No se encontraron archivos procesables.", file=sys.stderr)
        sys.exit(2)

    print(f"Archivos a procesar: {len(archivos)}  [motor={args.motor}"
          + (", raw" if args.raw else "") + "]\n")

    print(_FMT.format(*_CABECERA))
    print(_FMT.format(*("─" * 34, "─" * 13, "─" * 5, "─" * 5, "─" * 9, "─" * 7, "─" * 6, "─" * 6)))

    n_rechazadas = 0
    for archivo in archivos:
        try:
            fila = _procesar_archivo(
                archivo, modo_motor=args.motor, docling_raw=args.raw
            )
        except Exception as exc:
            print(f"❌ {archivo.name}: error {type(exc).__name__}: {exc}")
            continue
        if fila["gate"] == "rechazada":
            n_rechazadas += 1

        print(_FMT.format(
            fila["archivo"][:34],
            fila["tipo"][:13],
            fila["clase"][:5],
            fila["gate"][:5],
            fila["calidad"][:9],
            fila["motor"][:7],
            fila["orient"][:6],
            fila["chars"],
        ))

        # Guardar .md (solo si hay markdown; una imagen rechazada no se guarda).
        if fila["markdown"]:
            if args.outdir:
                outdir = Path(args.outdir)
                outdir.mkdir(parents=True, exist_ok=True)
                salida = outdir / f"{archivo.stem}.md"
            else:
                salida = archivo.with_suffix(".md")
            salida.write_text(fila["markdown"], encoding="utf-8")
            print(f"    guardado: {salida}")
        elif fila["gate"] == "rechazada":
            print(f"    (sin guardar: rechazada por el gate de T-102)")

        if args.print:
            print("\n" + "=" * 60)
            print(fila["markdown"])
            print("=" * 60)

    if n_rechazadas:
        print(f"\n⚠  {n_rechazadas} imagen(es) rechazada(s) por el gate de T-102.")
        sys.exit(1)


if __name__ == "__main__":
    main()

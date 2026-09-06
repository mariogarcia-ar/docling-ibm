#!/usr/bin/env python
"""Inspección T-103: aplica preprocesamiento + orientación sobre archivos reales.

Recorre imágenes/PDFs (fixtures por defecto o rutas por CLI), convierte cada
archivo con Docling real para obtener los ``Box`` y sobre ellos:

  - T-102 (clase + gate) y T-103 ``evaluar_calidad()`` (preprocesamiento);
  - T-103 ``detectar_orientacion()`` / ``requiere_rotacion()`` por boxes
    (doc 03 §4.1, E-DOC-2).

Uso:
    python scripts/inspeccionar_t103.py [ruta...] [--sin-ocr] [--detalle]

Ejemplos:
    python scripts/inspeccionar_t103.py                         # fixtures (con Docling)
    python scripts/inspeccionar_t103.py --sin-ocr               # solo calidad (rápido)
    python scripts/inspeccionar_t103.py tests/fixtures/golden/4c261bc8-*.jpeg
    python scripts/inspeccionar_t103.py files/2025-08/2D2C9343 --detalle

Nota: convertir con Docling descarga modelos la primera vez y es lento (por eso
en la suite estos casos se marcan ``integration``).
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

from voucherflow.models.docling import DoclingConverter  # noqa: E402
from voucherflow.processing.image_classifier import (  # noqa: E402
    clasificar,
    verificar_procesabilidad,
)
from voucherflow.processing.orientation import (  # noqa: E402
    detectar_orientacion,
    orientacion_por_box,
    requiere_rotacion,
)
from voucherflow.processing.preprocessing import evaluar_calidad  # noqa: E402
from voucherflow.processing.type_detector import (  # noqa: E402
    EXTENSIONES_IMAGEN,
    EXTENSIONES_OFFICE,
    EXTENSIONES_TEXTO,
    detectar,
)

#: Extensiones que Docling convierte y pueden tener boxes (imagen/pdf/office).
_EXTENSIONES_CONVERTIBLES = EXTENSIONES_IMAGEN | {".pdf"} | EXTENSIONES_OFFICE | EXTENSIONES_TEXTO

_FMT = "{:<34} {:<13} {:>5} {:>5}  {:<10} {:<9} {:<14} {}"


def _rutas_default() -> list[Path]:
    """Fixtures de imagen/pdf del golden set (copia estable)."""
    base = Path(__file__).resolve().parents[1] / "tests" / "fixtures"
    if not base.exists():
        return []
    return sorted(p for p in base.rglob("*") if p.suffix.lower() in _EXTENSIONES_CONVERTIBLES)


def _rutas_desde_cli(args: list[str]) -> list[Path]:
    rutas: list[Path] = []
    for raw in args:
        p = Path(raw)
        if p.is_dir():
            rutas.extend(q for q in p.rglob("*") if q.suffix.lower() in _EXTENSIONES_CONVERTIBLES)
        elif p.is_file() and p.suffix.lower() in _EXTENSIONES_CONVERTIBLES:
            rutas.append(p)
        else:
            print(f"⚠  Se ignora (no convertible o no existe): {p}", file=sys.stderr)
    return sorted(set(rutas))


def _analizar_con_docling(rutas: list[Path], converter: DoclingConverter, sin_ocr: bool):
    """Analiza cada archivo y devuelve filas (dict) + contadores."""
    filas: list[dict] = []
    clases: Counter = Counter()
    orients: Counter = Counter()
    rotados = 0
    errores = 0

    for p in rutas:
        # T-101: tipo de entrada
        te = detectar(p)

        # T-102: el clasificador/gate es SOLO para imágenes directas. Un
        # pdf_escaneado NO entra a T-102 hasta convertirse a imagen (orquestación);
        # aplicarle el gate aquí daría un falso "rechazada" (el PDF no es imagen).
        clase = "-"
        gate = "-"
        calidad = "-"
        if te.tipo == "imagen":
            cl = clasificar(p)
            v = verificar_procesabilidad(p)
            clase = cl.clase.value
            gate = "ok" if v.procesable else "rechazada"
            q = evaluar_calidad(cl)
            calidad = ",".join(q.acciones) if q.acciones else ("preproc" if q.requiere_preprocesamiento else "-")
        elif te.tipo == "pdf_escaneado":
            clase = "(escaneado)"  # se convierte a imagen en orquestación

        clases[clase] += 1

        # T-103 orientación por boxes (requiere convertir con Docling)
        orient = "-"
        rotado = False
        n_h = n_v = 0
        if not sin_ocr and te.tipo in ("imagen", "pdf_escaneado", "pdf_texto"):
            try:
                doc = converter.convert(p)
                boxes = doc.boxes
                orient = detectar_orientacion(boxes)
                rotado = requiere_rotacion(boxes)
                n_h = sum(1 for b in boxes if orientacion_por_box(b) == "horizontal")
                n_v = sum(1 for b in boxes if orientacion_por_box(b) == "vertical")
            except Exception as exc:
                orient = f"ERROR:{type(exc).__name__}"
                errores += 1

        if rotado:
            rotados += 1
        orients[orient] += 1

        filas.append({
            "archivo": p.name,
            "tipo": te.tipo,
            "clase": clase,
            "gate": gate,
            "calidad": calidad,
            "orient": orient,
            "rotado": "SÍ" if rotado else ("no" if orient != "-" else "-"),
            "h/v": f"{n_h}/{n_v}" if not sin_ocr and orient != "-" else "-",
        })
    return filas, clases, orients, rotados, errores


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Inspección T-103: calidad/preprocesamiento + orientación sobre archivos reales."
    )
    parser.add_argument("rutas", nargs="*", help="Archivos/carpetas (default: tests/fixtures).")
    parser.add_argument("--sin-ocr", action="store_true", help="No corre Docling: solo T-102 + calidad (rápido).")
    parser.add_argument("--detalle", action="store_true", help="Muestra una fila por archivo.")
    parser.add_argument("--max", type=int, default=12, help="Máx. archivos a convertir con Docling (default 12).")
    args = parser.parse_args()

    rutas = _rutas_desde_cli(args.rutas) if args.rutas else _rutas_default()
    if not rutas:
        print("No se encontraron archivos para inspeccionar.", file=sys.stderr)
        return

    # Limitar cuántos convertimos con Docling (es lento).
    if not args.sin_ocr:
        rutas = rutas[: args.max]

    print(f"Archivos a inspeccionar: {len(rutas)}"
          + ("  [--sin-ocr: sin Docling]" if args.sin_ocr else "  [con Docling]"))
    print()

    converter = None if args.sin_ocr else DoclingConverter()
    filas, clases, orients, rotados, errores = _analizar_con_docling(rutas, converter, args.sin_ocr)

    print("== Resumen por tipo de entrada (T-101) ==")
    tipos: Counter = Counter(f["tipo"] for f in filas)
    for t, n in tipos.most_common():
        print(f"  {t:<16}: {n}")

    if not args.sin_ocr:
        print("\n== Orientación dominante (T-103, por boxes Docling) ==")
        for o, n in orients.most_common():
            print(f"  {str(o):<16}: {n}")
        print(f"\n  Documentos que requieren rotación (vertical): {rotados}")
        if errores:
            print(f"  ⚠  Errores de conversión: {errores}")

    if args.detalle:
        print(f"\n== Detalle por archivo ({len(filas)}) ==")
        print(_FMT.format("archivo", "tipo", "clase", "gate", "calidad", "orient", "rotado", "h/v"))
        for f in filas:
            print(_FMT.format(
                f["archivo"][:34], f["tipo"][:13], f["clase"][:5], f["gate"][:5],
                f["calidad"][:10], f["orient"][:9], f["rotado"][:4], f["h/v"],
            ))


if __name__ == "__main__":
    main()

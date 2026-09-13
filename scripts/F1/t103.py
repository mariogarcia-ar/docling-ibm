#!/usr/bin/env python
"""Procesa archivos/carpetas aplicando T-103 (preprocesamiento + orientación).

Para cada archivo procesable de las rutas pasadas (imágenes y PDFs), aplica
la cadena de F1/T-103:

  1. T-101 ``detectar()``: tipo de entrada (imagen/pdf_texto/pdf_escaneado/...).
  2. T-102 ``clasificar()`` + ``verificar_procesabilidad()``: SOLO sobre
     imágenes directas (un ``pdf_escaneado`` no entra a T-102 hasta convertirse
     a imagen en la orquestación).
  3. T-103 ``evaluar_calidad()``: decide si la imagen requiere preprocesamiento
     (calidad, resolución, perspectiva) según su clase T-102.
  4. T-103 ``detectar_orientacion()`` / ``requiere_rotacion()``: orientación
     dominante por boxes (doc 03 §4.1, E-DOC-2). Requiere convertir con
     Docling para obtener los ``Box`` (por eso el modo ``--sin-ocr`` salta la
     conversión y es rápido).

En F1 el preprocesamiento real de píxeles lo hace Docling al convertir
(``preprocesar`` es un stub idempotente sin CV, subplan §2.1); este script
muestra el **diagnóstico** de calidad/orientación que decide el pipeline.

Uso:
    python scripts/F1/t103.py <archivo|carpeta>... [--sin-ocr]

Ejemplos:
    python scripts/F1/t103.py tests/fixtures/golden/2991f57d-*.jpg
    python scripts/F1/t103.py tests/fixtures/golden              # con Docling
    python scripts/F1/t103.py tests/fixtures/golden --sin-ocr    # solo T-102+calidad
    python scripts/F1/t103.py ../files/2025-08/2D2C9343 --sin-ocr
    python scripts/F1/t103.py tests/fixtures/pdf_escaneados

Nota: convertir con Docling descarga modelos la primera vez y es lento (por
eso en la suite estos casos se marcan ``integration``).
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
from voucherflow.processing.orientation import (  # noqa: E402
    detectar_orientacion,
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


def _expandir(args: list[str]) -> list[Path]:
    """Expande archivos/carpetas de la CLI a una lista de archivos convertibles."""
    rutas: list[Path] = []
    for raw in args:
        p = Path(raw)
        if p.is_dir():
            rutas.extend(
                q for q in sorted(p.rglob("*"))
                if q.is_file() and q.suffix.lower() in _EXTENSIONES_CONVERTIBLES
            )
        elif p.is_file() and p.suffix.lower() in _EXTENSIONES_CONVERTIBLES:
            rutas.append(p)
        elif p.is_file():
            print(f"⚠  Se ignora (no convertible o no existe): {p}", file=sys.stderr)
        else:
            print(f"⚠  Se ignora (no existe): {p}", file=sys.stderr)
    return sorted(set(rutas))


def _analizar_archivo(
    archivo: Path,
    converter: DoclingConverter | None,
) -> dict:
    """Aplica T-101/T-102/T-103 a un archivo y devuelve la fila de resultado.

    La orientación por boxes (T-103) requiere convertir con Docling
    (``converter``). Si ``converter`` es ``None`` (modo --sin-ocr) se salta la
    conversión y la orientación queda como ``-``.
    """
    fila: dict = {
        "archivo": archivo.name,
        "tipo": "-",
        "clase": "-",
        "gate": "-",
        "calidad": "-",
        "orient": "-",
        "rotado": "-",
        "detalle": "",
    }

    # 1. T-101: tipo de entrada.
    te = detectar(archivo)
    fila["tipo"] = te.tipo

    # 2. T-102 (gate + clase): SOLO para imágenes directas. Un pdf_escaneado
    #    NO entra a T-102 hasta convertirse a imagen (orquestación); aplicarle
    #    el gate aquí daría un falso "rechazada" (el PDF no es imagen).
    if te.tipo == "imagen":
        cl = clasificar(archivo)
        v = verificar_procesabilidad(archivo)
        fila["clase"] = cl.clase.value
        fila["gate"] = "ok" if v.procesable else "rechazada"

        # 3. T-103 calidad (preprocesamiento heurístico, sin CV).
        q = evaluar_calidad(cl)
        fila["calidad"] = ",".join(q.acciones) if q.acciones else ("preproc" if q.requiere_preprocesamiento else "-")
        fila["detalle"] = q.motivo
        if v.razon_rechazo:
            fila["detalle"] = f"gate: {v.razon_rechazo} — {v.motivo}"
        elif cl.motivo:
            fila["detalle"] = f"{cl.motivo} | {q.motivo}"
    elif te.tipo == "pdf_escaneado":
        fila["clase"] = "(escaneado)"  # se convierte a imagen en orquestación

    # 4. T-103 orientación por boxes (requiere Docling real).
    if converter is not None and te.tipo in ("imagen", "pdf_escaneado", "pdf_texto"):
        try:
            doc = converter.convert(archivo)
            orient = detectar_orientacion(doc.boxes)
            fila["orient"] = orient
            fila["rotado"] = "SÍ" if requiere_rotacion(doc.boxes) else "no"
            if doc.boxes:
                fila["detalle"] = (fila["detalle"] + " | " if fila["detalle"] else "") + \
                    f"{len(doc.boxes)} boxes"
        except Exception as exc:
            fila["orient"] = f"ERROR:{type(exc).__name__}"
            fila["detalle"] = (fila["detalle"] + " | " if fila["detalle"] else "") + str(exc)[:60]

    return fila


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Aplica preprocesamiento (calidad) + orientación (F1 / T-103) a archivos."
    )
    parser.add_argument("rutas", nargs="+", help="Archivos o carpetas (imagen/pdf/office).")
    parser.add_argument("--sin-ocr", action="store_true",
                        help="No corre Docling: solo T-101 + T-102 + calidad (rápido).")
    args = parser.parse_args()

    archivos = _expandir(args.rutas)
    if not archivos:
        print("No se encontraron archivos procesables.", file=sys.stderr)
        sys.exit(2)

    print(f"Archivos a inspeccionar: {len(archivos)}"
          + ("  [--sin-ocr: sin Docling]" if args.sin_ocr else "  [con Docling]"))
    print()

    converter = None if args.sin_ocr else DoclingConverter()
    cabecera = ("archivo", "tipo", "clase", "gate", "calidad", "orient", "rotado", "detalle")
    print(_FMT.format(*cabecera))
    print(_FMT.format(*("─" * 34, "─" * 13, "─" * 5, "─" * 5, "─" * 10, "─" * 9, "─" * 14, "─" * 40)))

    n_rechazadas = 0
    for archivo in archivos:
        try:
            fila = _analizar_archivo(archivo, converter)
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
            fila["calidad"][:10],
            fila["orient"][:9],
            fila["rotado"][:14],
            fila["detalle"][:40],
        ))

    if n_rechazadas:
        print(f"\n⚠  {n_rechazadas} imagen(es) rechazada(s) por el gate de T-102.")
        sys.exit(1)


if __name__ == "__main__":
    main()

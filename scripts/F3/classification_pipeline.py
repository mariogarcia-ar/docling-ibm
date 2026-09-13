#!/usr/bin/env python
"""CLI de la cadena contable 01→02→03 (F3 / T-304) — portado de v1.

**Fase**: F3 (clasificación) · **Tarea**: T-304 · **Épica**: E-CLAS-2.

Herramienta de **paridad** con `v1/classification_pipeline.py` (F3-subplan §3.5):
corre la cadena de la librería (`voucherflow.classification.contable`) sobre uno
o varios Markdown con la **misma interfaz** que v1, para poder comparar ambos
lados con el mismo modelo.

Portado literal de v1 (salvo el motor, que ahora es la librería):

============================  ==================================================
v1                            scripts/F3/classification_pipeline.py
============================  ==================================================
`source` posicional           igual (archivo o carpeta recursiva `*.md`)
`-m/--model`                  igual (default: el del rol `llm` de `Settings`)
`--condicion-impositiva`      igual (`21` default; `10_5`, `27`, `2_5`, `exento_no_gravado`)
`-o/--output`                 igual (JSON agregado; sin él, un sidecar por Markdown)
`<doc>_classification.json`   igual (`ruta_checkpoint()` de la librería)
reanudación por checkpoint    igual (`ejecutar_cadena` no repite pasos resueltos)
`ClassificationError`         `ErrorCadenaContable` (con `error.pasos`)
============================  ==================================================

**No importa nada de `v1/`** (regla dura F3-subplan §4): la lógica vive en
`voucherflow.classification`, y este script solo hace CLI + recorrido de
archivos + escritura de resultados (que es lo que la regla dura prohíbe que viva
en la librería).

Uso:
    python scripts/F3/classification_pipeline.py documento.md
    python scripts/F3/classification_pipeline.py files/2025-08
    python scripts/F3/classification_pipeline.py files/2025-08 -o resultados.json
    python scripts/F3/classification_pipeline.py doc.md --condicion-impositiva 10_5
    python scripts/F3/classification_pipeline.py doc.md -m qwen2.5:7b

Requiere Ollama local (modelo de texto del rol `llm`; ver `Settings.modelos`).
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from tempfile import NamedTemporaryFile
from typing import Any

# El paquete usa layout src/; si se corre sin instalar, se agrega src/ al path.
_SRC = Path(__file__).resolve().parents[2] / "src"
if str(_SRC) not in sys.path:
    sys.path.insert(0, str(_SRC))

from voucherflow.classification import (  # noqa: E402
    CONDICION_IMPOSITIVA_DEFAULT,
    CONDICIONES_IMPOSITIVAS,
    ErrorCadenaContable,
    condicion_impositiva_no_valida,
    ejecutar_cadena,
    ruta_checkpoint,
)

# ---------------------------------------------------------------------------
# Recorrido de archivos y escritura (portado de v1/lib/pipeline.py)
# ---------------------------------------------------------------------------


def encontrar_markdown(source: Path) -> list[Path]:
    """Devuelve los Markdown de ``source`` (archivo suelto o carpeta recursiva).

    Portado de ``find_markdown_files`` de v1: si es un archivo, se devuelve tal
    cual (aunque no sea ``.md``: el llamador lo pidió explícitamente); si es una
    carpeta, se recorren los ``*.md`` ordenados para que la corrida sea
    reproducible.
    """
    if source.is_file():
        return [source]
    return sorted(source.rglob("*.md"))


def requerir_markdown(source: Path) -> list[Path]:
    """Como :func:`encontrar_markdown` pero aborta con mensaje claro (v1)."""
    if not source.exists():
        print(f"Error: no existe '{source}'", file=sys.stderr)
        raise SystemExit(1)
    archivos = encontrar_markdown(source)
    if not archivos:
        print(f"No se encontraron archivos Markdown en '{source}'", file=sys.stderr)
        raise SystemExit(1)
    return archivos


def escribir_resultados(salida: Path, resultados: Any) -> None:
    """Escribe un JSON agregado de forma **atómica** (portado de v1).

    v1 usaba un ``NamedTemporaryFile`` en el mismo directorio y ``replace``: una
    corrida larga interrumpida no debe dejar el resultado a medio escribir.
    """
    salida.parent.mkdir(parents=True, exist_ok=True)
    with NamedTemporaryFile(
        mode="w",
        encoding="utf-8",
        dir=salida.parent,
        prefix=f".{salida.name}.",
        delete=False,
    ) as temporal:
        temporal.write(json.dumps(resultados, ensure_ascii=False, indent=2))
        ruta_temporal = Path(temporal.name)
    ruta_temporal.replace(salida)
    print(f"Guardado: {salida}", file=sys.stderr)


# ---------------------------------------------------------------------------
# Corrida
# ---------------------------------------------------------------------------


def clasificar_documento(
    document_path: Path,
    modelo: str,
    condicion_impositiva: str,
) -> dict[str, Any]:
    """Clasifica un Markdown con la cadena de la librería (T-304).

    Portado de ``classify_document`` de v1: usa el propio Markdown como
    ``descripcion`` (v1 volcaba ``load_document_text()``), el sidecar
    ``<doc>_classification.json`` como checkpoint y devuelve el mismo shape
    (``{"archivo": ..., "pasos": {...}}``) para que la comparación sea directa.
    """
    from voucherflow.models.ollama import OllamaClient

    texto = document_path.read_text(encoding="utf-8")
    resultado = ejecutar_cadena(
        OllamaClient(),
        descripcion=texto,
        condicion_impositiva=condicion_impositiva,
        documento=document_path,
        modelo=modelo,
    )
    return {
        "archivo": str(document_path),
        "pasos": resultado.pasos,
        "clasificacion": resultado.como_clasificacion(),
        "requiere_revision_humana": resultado.requiere_revision_humana,
    }


def main() -> None:
    parser = argparse.ArgumentParser(
        description="""
Clasifica uno o varios Markdown en tres pasos contables:
  01  centros de costo
  02  macro categorías
  03  concepto y código final

El primer resultado de cada paso se usa como entrada del siguiente.
El JSON final conserva las respuestas de los tres pasos para evaluación.

Portado de v1/classification_pipeline.py (T-304): misma interfaz, motor de la
librería (voucherflow.classification.contable).
""",
        epilog="""
Ejemplos:
  python scripts/F3/classification_pipeline.py documento.md
  python scripts/F3/classification_pipeline.py documento.md --condicion-impositiva 10_5
  python scripts/F3/classification_pipeline.py files/2025-08
  python scripts/F3/classification_pipeline.py files/2025-08 -o resultados.json
  python scripts/F3/classification_pipeline.py files/2025-08 -m qwen2.5:7b -o resultados.json
""",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument(
        "source",
        type=Path,
        help="Ruta de un Markdown o carpeta raíz; las carpetas se recorren recursivamente",
    )
    parser.add_argument(
        "-m",
        "--model",
        default=None,
        help="Modelo de Ollama (por defecto: el del rol 'llm' de Settings)",
    )
    parser.add_argument(
        "--condicion-impositiva",
        default=CONDICION_IMPOSITIVA_DEFAULT,
        help=f"Condición para el paso 03: {', '.join(CONDICIONES_IMPOSITIVAS)}",
    )
    parser.add_argument(
        "-o",
        "--output",
        type=Path,
        help="JSON agregado; sin esta opción crea un JSON junto a cada Markdown",
    )
    args = parser.parse_args()

    problema = condicion_impositiva_no_valida(args.condicion_impositiva)
    if problema:
        print(f"Error: {problema}", file=sys.stderr)
        raise SystemExit(2)

    archivos = requerir_markdown(args.source)

    resultados: list[dict[str, Any]] = []
    for indice, document_path in enumerate(archivos, start=1):
        print(
            f"[{indice}/{len(archivos)}] Clasificando {document_path}", file=sys.stderr
        )
        try:
            resultados.append(
                clasificar_documento(document_path, args.model, args.condicion_impositiva)
            )
        except ErrorCadenaContable as error:
            print(f"Error en '{document_path}': {error}", file=sys.stderr)
            resultados.append(
                {
                    "archivo": str(document_path),
                    "pasos": error.pasos,
                    "error": str(error),
                }
            )
        except (ValueError, json.JSONDecodeError, KeyError, OSError) as error:
            print(f"Error en '{document_path}': {error}", file=sys.stderr)
            resultados.append(
                {"archivo": str(document_path), "pasos": {}, "error": str(error)}
            )

    if args.output:
        escribir_resultados(args.output, resultados)
    else:
        for resultado in resultados:
            escribir_resultados(
                ruta_checkpoint(Path(resultado["archivo"])), resultado
            )


if __name__ == "__main__":
    main()

#!/usr/bin/env python
"""Reporte de la métrica del gate qween sobre el golden F2 etiquetado (T-204).

**Fase**: F2 (validación / refactor qween) · **Tarea**: T-204 · **Épicas**:
E-QWE (E-QWE-1, E-QWE-2).

Implementa la **métrica acordada** de la decisión F2-subplan §2.6 sobre el
**subconjunto acotado y etiquetado** del golden set (decisión §2.5):

  1. **Exactitud del gate**: aciertos / total (comparando el ``veredicto_final``
     del doble paso contra la columna ``veredicto`` del ``casos.csv``).
  2. **% de no-comprobantes que NO llegan a extracción**: de los casos
     clasificados ``no_comprobante`` por el gate, cuántos terminaron **sin**
     ``vista_fiel`` (objetivo: 100% — ahorro de costo E-QWE).
  3. **% de indeterminación**: casos que necesitaron la 2ª pasada de revisión
     (indeterminado en la 1ª pasada o en la 2ª), para monitorear el costo del
     doble paso.

Corre el gate **real** (``validation.validar_y_procesar``, T-203) sobre cada
caso etiquetado: para imágenes usa una ``ProcessedDocument`` mínima (sin
Docling) y para PDF/office/texto corre ``procesar_documento`` (F1). Por eso
este script es una **herramienta de reporte manual** — requiere el **Ollama
local** (modelo VLM, default ``qwen2.5vl:3b``) y **no** forma parte de la suite
default (que corre sin servicios reales; ver ``tests/test_validation_gate_ahorro.py``).

La lógica de cálculo (:func:`calcular_metricas`) es **pura** y está cubierta por
tests con datos sintéticos, sin Ollama.

Uso:
    cd v2 && python scripts/F2/t204.py --help
    cd v2 && python scripts/F2/t204.py                 # golden etiquetado completo
    cd v2 && python scripts/F2/t204.py --split eval    # solo el split de evaluación
    cd v2 && python scripts/F2/t204.py --detalle       # por caso + trazabilidad
    cd v2 && python scripts/F2/t204.py --json /tmp/t204.json

Salida (ejemplo):
    EXACTITUD DEL GATE          7/9 = 77.8%   (umbral 90%)
    NO-COMP. SIN EXTRACCIÓN     4/4 = 100.0%  (objetivo 100%)
    INDETERMINACIÓN             2/9 = 22.2%
"""

from __future__ import annotations

import argparse
import csv
import json
import sys
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path

# El paquete usa layout src/; si se corre sin instalar, se agrega src/ al path.
_V2 = Path(__file__).resolve().parents[2]
_SRC = _V2 / "src"
if str(_SRC) not in sys.path:
    sys.path.insert(0, str(_SRC))

#: Raíz del repo (``v2/`` es el workspace del proyecto): las rutas del
#: ``casos.csv`` son relativas a la raíz del repo (p. ej. ``v2/tests/fixtures/…``).
_REPO_ROOT = _V2.parent

#: Veredictos válidos de la columna ``veredicto`` del golden etiquetado (F2 §2.5).
VEREDICTOS_VALIDOS = ("comprobante", "no_comprobante", "indeterminado")

#: Umbral objetivo de exactitud del gate acordado en F2-subplan §2.6.
UMBRAL_EXACTITUD = 0.90


# ---------------------------------------------------------------------------
# Modelo de datos del reporte y cálculo de métricas (puro, sin Ollama)
# ---------------------------------------------------------------------------


@dataclass
class MetricaCaso:
    """Resultado del gate sobre **un** caso del golden etiquetado (T-204).

    Campos:
        id: identificador del caso (columna ``id`` del ``casos.csv``).
        split: ``train`` | ``eval`` (columna ``split``).
        veredicto_real: etiqueta de referencia (columna ``veredicto``); una de
            :data:`VEREDICTOS_VALIDOS`.
        veredicto_pred: ``veredicto_final`` del doble paso (``comprobante`` o
            ``no_comprobante``; nunca ``indeterminado`` por la política
            E-QWE-1). ``None`` si el caso falló (``error``).
        pasadas: número de pasadas ejecutadas (1 = solo rápida; 2 = rápida +
            revisión).
        indeterminado: ``True`` si alguna pasada devolvió ``indeterminado``
            (el caso necesitó revisión o fue rechazado por no confirmado).
        vista_fiel: ``True`` si se preparó la vista fiel (el caso **sí** llega
            a extracción); ``False`` = ahorro (no llega a extracción).
        error: mensaje del error si el caso no se pudo evaluar (se excluye de la
            exactitud, no así de la trazabilidad).
    """

    id: str
    split: str
    veredicto_real: str
    veredicto_pred: str | None = None
    pasadas: int = 0
    indeterminado: bool = False
    vista_fiel: bool = False
    error: str | None = None

    @property
    def evaluado(self) -> bool:
        """True si el caso se pudo evaluar (no hubo error y hay predicción)."""
        return self.error is None and self.veredicto_pred is not None

    @property
    def acierto(self) -> bool:
        """True si la predicción coincide con la etiqueta de referencia."""
        return self.evaluado and self.veredicto_pred == self.veredicto_real


@dataclass
class MetricasGate:
    """Métrica acordada de F2 (T-204 / F2-subplan §2.6).

    Se calcula con :func:`calcular_metricas`; es serializable
    (``asdict``) para el reporte ``--json``.
    """

    total: int = 0
    evaluados: int = 0
    errores: int = 0
    aciertos: int = 0
    exactitud: float = 0.0
    indeterminacion: int = 0
    pct_indeterminado: float = 0.0
    no_comprobantes_clasificados: int = 0
    no_comprobantes_sin_extraccion: int = 0
    pct_no_comprobantes_sin_extraccion: float = 0.0
    segundos: float | None = None
    umbral_exactitud: float = UMBRAL_EXACTITUD
    cumple_umbral: bool = False
    por_clase: dict[str, dict[str, int]] = field(default_factory=dict)
    por_split: dict[str, dict[str, int]] = field(default_factory=dict)

    def resumen(self) -> str:
        """Resumen textual de la métrica (español, para la consola/bitácora)."""
        umbral = "sí" if self.cumple_umbral else "NO"
        lineas = [
            "MÉTRICA ACORDADA DEL GATE (F2 / T-204; subplan §2.6)",
            f"  • Exactitud del gate        : {self.aciertos}/{self.evaluados} = "
            f"{100 * self.exactitud:.1f}%  (umbral {100 * self.umbral_exactitud:.0f}%: {umbral})",
            f"  • Indeterminación (2ª pasada): {self.indeterminacion}/{self.evaluados} = "
            f"{100 * self.pct_indeterminado:.1f}%",
            f"  • No-comp. sin extracción    : {self.no_comprobantes_sin_extraccion}/"
            f"{self.no_comprobantes_clasificados} = "
            f"{100 * self.pct_no_comprobantes_sin_extraccion:.1f}%  (objetivo 100%)",
        ]
        if self.errores:
            lineas.append(f"  • Errores                    : {self.errores}")
        return "\n".join(lineas)


def calcular_metricas(casos: list[MetricaCaso]) -> MetricasGate:
    """Calcula la métrica acordada de F2 sobre los casos evaluados (T-204).

    Definiciones (F2-subplan §2.6, decisión del equipo):

      - **Exactitud del gate** = aciertos / evaluados, sobre las etiquetas del
        golden (``comprobante`` / ``no_comprobante`` / ``indeterminado``). Se
        excluyen de la exactitud los casos con error (no evaluados), que se
        reportan aparte.
      - **% de indeterminación** = casos con alguna pasada ``indeterminado`` /
        evaluados. Mide el costo del doble paso (indeterminado ⇒ 2ª llamada al
        VLM o rechazo por no confirmado, E-QWE-1).
      - **% de no-comprobantes que NO llegan a extracción** = casos clasificados
        ``no_comprobante`` por el gate cuyo ``vista_fiel`` es ``None`` /
        casos clasificados ``no_comprobante``. **Debe ser 100%** (ahorro de
        costo E-QWE: los rechazados nunca alimentan la extracción de F4).

    Es una función **pura** (sin Ollama): la suite default la cubre con datos
    sintéticos (ver ``tests/test_validation_gate_ahorro.py``).
    """
    total = len(casos)
    evaluados = [c for c in casos if c.evaluado]
    n = len(evaluados)

    aciertos = sum(1 for c in evaluados if c.acierto)
    indeterminacion = sum(1 for c in evaluados if c.indeterminado)

    # Métrica de costo: de lo que el gate clasificó no_comprobante, cuánto NO
    # llegó a extracción (vista_fiel ausente). Objetivo: 100%.
    no_comp = [c for c in evaluados if c.veredicto_pred == "no_comprobante"]
    sin_extraccion = sum(1 for c in no_comp if not c.vista_fiel)

    # Desagregados de exactitud por clase real y por split (reporte §5 de 06).
    por_clase: dict[str, dict[str, int]] = {}
    por_split: dict[str, dict[str, int]] = {}
    for c in evaluados:
        clase = por_clase.setdefault(c.veredicto_real, {"aciertos": 0, "total": 0})
        clase["total"] += 1
        clase["aciertos"] += int(c.acierto)
        sp = por_split.setdefault(c.split or "-", {"aciertos": 0, "total": 0})
        sp["total"] += 1
        sp["aciertos"] += int(c.acierto)

    exactitud = aciertos / n if n else 0.0
    return MetricasGate(
        total=total,
        evaluados=n,
        errores=total - n,
        aciertos=aciertos,
        exactitud=exactitud,
        indeterminacion=indeterminacion,
        pct_indeterminado=(indeterminacion / n if n else 0.0),
        no_comprobantes_clasificados=len(no_comp),
        no_comprobantes_sin_extraccion=sin_extraccion,
        pct_no_comprobantes_sin_extraccion=(sin_extraccion / len(no_comp) if no_comp else 0.0),
        umbral_exactitud=UMBRAL_EXACTITUD,
        cumple_umbral=exactitud >= UMBRAL_EXACTITUD,
        por_clase=por_clase,
        por_split=por_split,
    )


# ---------------------------------------------------------------------------
# Lectura del golden etiquetado
# ---------------------------------------------------------------------------


def leer_golden_etiquetado(
    csv_path: Path,
    *,
    split: str | None = None,
    limite: int | None = None,
) -> list[dict[str, str]]:
    """Lee el ``casos.csv`` y devuelve solo las filas **etiquetadas** (T-204).

    Una fila está etiquetada cuando ``veredicto`` ∈ :data:`VEREDICTOS_VALIDOS`
    (es decir, ya no es ``pendiente``): ese es el subconjunto acotado de F2
    (decisión §2.5). ``split`` filtra por ``train``/``eval`` y ``limite`` acota
    la cantidad de casos (útil para una corrida de humo).
    """
    filas: list[dict[str, str]] = []
    with csv_path.open(encoding="utf-8") as fh:
        for fila in csv.DictReader(fh):
            if fila.get("veredicto") not in VEREDICTOS_VALIDOS:
                continue
            if split and fila.get("split") != split:
                continue
            filas.append(fila)
    if limite:
        filas = filas[:limite]
    return filas


def resolver_ruta(fila: dict[str, str]) -> Path:
    """Resuelve la ruta del caso: el CSV la guarda relativa a la raíz del repo."""
    return (_REPO_ROOT / fila["ruta"]).resolve()


# ---------------------------------------------------------------------------
# Evaluación de un caso contra el gate real (requiere Ollama)
# ---------------------------------------------------------------------------


def _documento_del_caso(ruta: Path):
    """Obtiene el ``ProcessedDocument`` del caso (imagen sin Docling).

    Mismo criterio que ``scripts/F2/t201.py``/``t203.py``: las imágenes se
    envuelven en un ``ProcessedDocument`` mínimo (la vista se deriva de la
    imagen; no hace falta el OCR de F1) y el resto pasa por
    ``procesar_documento`` (F1, Docling o pdftotext). Import diferido para no
    cargar Docling cuando solo hay imágenes.
    """
    from voucherflow.models.docling import ProcessedDocument
    from voucherflow.processing.type_detector import EXTENSIONES_IMAGEN

    if ruta.suffix.lower() in EXTENSIONES_IMAGEN:
        return ProcessedDocument(tipo_entrada="imagen", ruta=str(ruta), markdown="")

    from voucherflow.processing.orquestacion import procesar_documento

    return procesar_documento(ruta)


def evaluar_caso(
    fila: dict[str, str],
    cliente,
    *,
    modelo: str | None = None,
    settings=None,
) -> MetricaCaso:
    """Corre el gate (T-203) sobre un caso del golden y arma su ``MetricaCaso``.

    Nunca lanza: si F1 o el gate fallan, devuelve un caso con ``error`` (el
    error real se propaga con ``OllamaError``/excepciones de F1, pero aquí se
    captura para que un caso roto no aborte el reporte completo).
    """
    from voucherflow.validation import VeredictoGate, validar_y_procesar

    metrica = MetricaCaso(
        id=fila["id"],
        split=fila.get("split", ""),
        veredicto_real=fila["veredicto"],
    )
    try:
        documento = _documento_del_caso(resolver_ruta(fila))
        rv = validar_y_procesar(documento, cliente, modelo=modelo, settings=settings)
        metrica.veredicto_pred = rv.veredicto_final.value
        metrica.pasadas = len(rv.pasadas)
        metrica.indeterminado = any(
            p.veredicto is VeredictoGate.indeterminado for p in rv.pasadas
        )
        metrica.vista_fiel = rv.vista_fiel is not None
    except Exception as exc:  # noqa: BLE001 — un caso roto no debe abortar el reporte
        metrica.error = f"{type(exc).__name__}: {exc}"
    return metrica


# ---------------------------------------------------------------------------
# CLI / reporte
# ---------------------------------------------------------------------------


def _imprimir_tabla(casos: list[MetricaCaso], *, detalle: bool) -> None:
    """Imprime el resultado por caso (una línea por caso)."""
    fmt = "{:<28} {:<6} {:<16} {:<16} {:<3} {:<7} {:<6}"
    print(fmt.format("id", "split", "real", "pred", "ok", "pasadas", "fiel"))
    print(fmt.format(*("-" * 28, "-" * 6, "-" * 16, "-" * 16, "-" * 3, "-" * 7, "-" * 6)))
    for c in casos:
        if c.error:
            print(f"✗ {c.id:<26} {c.split:<6} {c.veredicto_real:<16} ERROR: {c.error[:60]}")
            continue
        pred = c.veredicto_pred or "-"
        print(fmt.format(
            c.id[:28],
            c.split[:6],
            c.veredicto_real[:16],
            pred[:16],
            "✓" if c.acierto else "✗",
            f"{c.pasadas}{'*' if c.indeterminado else ''}",
            "sí" if c.vista_fiel else "no",
        ))
    if detalle:
        print("\n  * = el caso tuvo una pasada 'indeterminado' (2ª pasada de revisión o rechazo por no confirmado)")
        print("  fiel=no ⇒ el documento NO llega a extracción (ahorro E-QWE)")


def main() -> None:
    parser = argparse.ArgumentParser(
        description=(
            "Reporte de la métrica del gate qween (F2 / T-204) sobre el golden "
            "etiquetado: exactitud, % indeterminado y % de no-comprobantes que "
            "no llegan a extracción (subplan §2.6). Requiere Ollama local."
        )
    )
    parser.add_argument(
        "--csv",
        type=Path,
        default=_V2 / "tests" / "golden" / "casos.csv",
        help="Ruta del casos.csv del golden (default: tests/golden/casos.csv).",
    )
    parser.add_argument(
        "--split",
        choices=("train", "eval"),
        help="Filtrar por split (default: todos los etiquetados).",
    )
    parser.add_argument("--limite", type=int, help="Máximo de casos a evaluar (humo).")
    parser.add_argument("--modelo", help="Modelo del gate (default: Settings.modelos.vlm).")
    parser.add_argument("--url", help="URL de Ollama (default: Settings.ollama.url).")
    parser.add_argument("--timeout", type=float, default=120.0, help="Timeout por llamada (s).")
    parser.add_argument(
        "--detalle", action="store_true", help="Imprime una línea por caso y notas."
    )
    parser.add_argument(
        "--json", type=Path, help="Escribe el reporte (métricas + casos) en JSON."
    )
    args = parser.parse_args()

    if not args.csv.exists():
        print(f"No existe el CSV del golden: {args.csv}", file=sys.stderr)
        sys.exit(2)

    filas = leer_golden_etiquetado(args.csv, split=args.split, limite=args.limite)
    if not filas:
        print(
            f"No hay casos etiquetados en {args.csv}"
            + (f" para el split '{args.split}'" if args.split else "")
            + " (columna 'veredicto' en {comprobante, no_comprobante, indeterminado}).",
            file=sys.stderr,
        )
        sys.exit(2)

    from voucherflow.models.ollama import OllamaClient
    from voucherflow.settings.config import cargar_settings

    settings = cargar_settings()
    url = args.url or settings.url_ollama
    modelo = args.modelo or settings.modelo_vlm
    if not modelo:
        print("No hay modelo VLM configurado; pasá --modelo.", file=sys.stderr)
        sys.exit(2)

    cliente = OllamaClient(url=url, timeout_s=args.timeout)
    print(
        f"Golden etiquetado: {len(filas)} caso(s)"
        + (f" [split={args.split}]" if args.split else "")
        + f"  [Ollama={url} modelo={modelo}]\n"
    )

    t0 = time.time()
    casos: list[MetricaCaso] = []
    for fila in filas:
        caso = evaluar_caso(fila, cliente, modelo=modelo, settings=settings)
        casos.append(caso)
        estado = "✗" if caso.error else ("✓" if caso.acierto else "✗")
        pred = caso.veredicto_pred or f"ERROR {caso.error}"
        print(f"  {estado} {caso.id:<28} real={caso.veredicto_real:<16} pred={pred}")
    segundos = time.time() - t0

    if args.detalle:
        print()
        _imprimir_tabla(casos, detalle=True)

    metricas = calcular_metricas(casos)
    metricas.segundos = segundos

    print("\n" + "=" * 64)
    print(metricas.resumen())
    print("=" * 64)

    print("\nExactitud por clase real:")
    for clase, d in sorted(metricas.por_clase.items()):
        print(f"  • {clase:<16} {d['aciertos']}/{d['total']}")
    print("\nExactitud por split:")
    for sp, d in sorted(metricas.por_split.items()):
        print(f"  • {sp:<16} {d['aciertos']}/{d['total']}")
    print(f"\nTiempo total: {segundos:.1f}s ({segundos / max(1, metricas.evaluados):.2f}s por caso).")

    if args.json:
        args.json.write_text(
            json.dumps(
                {
                    "tarea": "T-204",
                    "fase": "F2",
                    "modelo": modelo,
                    "golden_version": "0.2",
                    "metricas": asdict(metricas),
                    "casos": [asdict(c) for c in casos],
                },
                ensure_ascii=False,
                indent=2,
            ),
            encoding="utf-8",
        )
        print(f"Reporte JSON: {args.json}")


if __name__ == "__main__":
    main()

#!/usr/bin/env python
"""Paridad de extracción v2 vs. v1 — `extraction_pipeline.py` y `-M kvi/kvg` (F4/T-405).

**Fase**: F4 (extracción) · **Tarea**: T-405 · **Épica**: E-EXT.

Mide el DoD de F4 ("paridad de extracción con v1 en campos normalizados sobre el
golden set") comparando los **campos** que ambos lados devuelven para el mismo
documento, con el mismo modelo.

Por qué la comparación no es "misma salida del mismo modelo"
------------------------------------------------------------
**ADR-001** cambió qué devuelve el modelo: v1 le pedía el dato **ya normalizado y
decidido** (`cuit_emisor` cortado, `fecha_emision` en ISO, `comprobante_valido`
evaluado, `categoria_gasto` inferida); v2 le pide el valor **tal como se lee** +
su fragmento de sustento, y normaliza/decide en **código** (T-402) o en la
conclusión (F5). Por eso este script compara los **campos normalizados de
lectura** y deja explícitamente afuera los campos de **decisión** (ver
`tests/golden/F4/subconjunto.json` → `campos_fuera_de_paridad`): compararlos
reportaría como "regresión" algo que es el objetivo del rediseño.

Qué corre
---------
* **v2**: `api.process()` (F1) → `preparar_vista_fiel()` (F2) → `extraer()` (F4:
  los dos flujos en paralelo) → `combinar_evidencia()` (T-404). Se comparan los
  campos **normalizados** del ganador de cada campo.
* **v1**: `v1/extraction_pipeline.py` (pasos `10` genérico y `11` factura) sobre
  el **mismo markdown** y el mismo modelo, en un `v1/` preparado en un directorio
  temporal (v1 resuelve sus prompts relativos a `v1/prompts/`, que en este repo
  está vacío: los YAML viven en `prompts/` en la raíz — mismo ajuste que
  `scripts/F3/paridad_contable.py`).

Uso:
    python scripts/F4/paridad_extraccion.py                       # subconjunto real
    python scripts/F4/paridad_extraccion.py --detalle             # + traza por campo
    python scripts/F4/paridad_extraccion.py -m qwen2.5vl:3b
    python scripts/F4/paridad_extraccion.py --json /tmp/paridad_f4.json

Requiere **Ollama local** y `v1/` en el repo (la suite default cubre el nivel
determinista en `tests/test_extraction_paridad.py`).
"""

from __future__ import annotations

import argparse
import json
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Any

# El paquete usa layout src/; si se corre sin instalar, se agrega src/ al path.
_SRC = Path(__file__).resolve().parents[2] / "src"
if str(_SRC) not in sys.path:
    sys.path.insert(0, str(_SRC))

from voucherflow.extraction import (  # noqa: E402
    CAMPOS_EXTRACCION,
    CAMPOS_SOSTEN_ESTRUCTURADO,
    combinar_evidencia,
    extraer,
)
from voucherflow.schemas.evidence import CombinedEvidence  # noqa: E402

# ---------------------------------------------------------------------------
# Rutas
# ---------------------------------------------------------------------------

#: Raíz del repo (``v2/scripts/F4/paridad_extraccion.py`` → ``parents[3]``).
RAIZ_REPO = Path(__file__).resolve().parents[3]

#: Raíz de v2.
RAIZ_V2 = RAIZ_REPO / "v2"

#: v1 tal como está en el repo (sin los prompts, ver `preparar_v1`).
V1_DIR = RAIZ_REPO / "v1"

#: Prompts YAML de la raíz (los que v1 esperaba en ``v1/prompts/``).
PROMPTS_RAIZ = RAIZ_REPO / "prompts"

#: Manifiesto del subconjunto de paridad de F4.
SUBCONJUNTO = RAIZ_V2 / "tests" / "golden" / "F4" / "subconjunto.json"

#: Modelo por defecto (el que usa v1 para los pasos de extracción).
MODELO_POR_DEFECTO = "qwen2.5vl:3b"

#: Nombre del paso de factura en la salida de `extraction_pipeline.py` de v1.
PASO_FACTURA = "11_extraccion_factura"

#: Nombre del paso genérico en la salida de `extraction_pipeline.py` de v1.
PASO_GENERICO = "10_extraccion_generica"

#: Diferencias que el diseño de v2 **espera** y que no son regresiones (T-405).
#: Se usan como nota cuando un campo difiere, para que el reporte no deje la
#: interpretación al lector (mismo criterio que las notas de
#: ``scripts/F3/paridad_contable.py``).
NOTAS_ESPERADAS: dict[str, str] = {
    "tipo_comprobante": (
        "v2 publica el valor TAL COMO SE LEYÓ (agregado de vocabulario: acepta "
        "'090'/'099' de los tiques, que el motor R1-R7 de F3 deja fuera por D-13). "
        "Comparar contra la letra final de v1 mezcla lectura con decisión."
    ),
    "moneda": (
        "v1 aplicaba el default 'ARS' cuando no había indicio explícito; v2 NO lo "
        "hace (sería inventar la moneda). Un 'sin valor' de v2 contra 'ARS' de v1 "
        "es la diferencia deliberada de T-402."
    ),
    "descripcion": (
        "v1 redactaba la descripción con el modelo; v2 la normaliza en código "
        "(minúsculas + espacios colapsados), así que el texto puede diferir en la "
        "forma aunque describa lo mismo."
    ),
}

#: Nota por defecto para un campo que difiere sin causa declarada.
NOTA_DIFERENCIA_GENERICA = (
    "Diferencia a revisar: el DoD admite paridad **o mejora** documentada "
    "(F3-subplan §3.5). Revisá el caso antes de reportarlo como regresión."
)


# ---------------------------------------------------------------------------
# Proyección de la evidencia de v2 al shape plano de v1 (función pura)
# ---------------------------------------------------------------------------


def campos_desde_v2(
    combinada: CombinedEvidence,
    *,
    campos: list[str] | None = None,
) -> dict[str, Any]:
    """Proyecta la evidencia **combinada** de v2 al dict plano de v1 (T-405).

    Devuelve ``campo -> valor`` del **ganador** de cada campo (la resolución por
    precedencia de T-404 ya eligió la fuente), limitado a ``campos`` (por defecto,
    el contrato de extracción). Un campo que ninguna fuente declaró **no entra**:
    la ausencia es información, no un ``None`` que después se confunda con "no
    comparable".

    Es la operación que hace comparables los dos lados: v1 devolvía un JSON plano
    ``{campo: valor}`` y v2 devuelve evidencia por fuente con resolución; esta
    función es la **proyección** de v2 al shape de v1, sin perder nada (la
    evidencia completa sigue disponible en la `CombinedEvidence`).
    """
    universo = campos if campos is not None else list(CAMPOS_EXTRACCION)
    salida: dict[str, Any] = {}
    for campo in universo:
        combinado = combinada.campos.get(campo)
        if combinado is None or combinado.fuente is None:
            continue
        salida[campo] = combinado.valor
    return salida


def notas_de_proyeccion(combinada: CombinedEvidence) -> dict[str, str]:
    """Notas de la proyección, por campo (T-405).

    Devuelve una nota para los campos cuya comparación exige una **advertencia
    de contexto** —hoy, los de formato volátil, que v2 publica crudos (T-401) y
    v1 pedía ya normalizados— para que el reporte no deje la interpretación al
    lector y no reporte como regresión algo que el diseño espera.
    """
    notas: dict[str, str] = {}
    for campo in CAMPOS_SOSTEN_ESTRUCTURADO:
        if campo in combinada.campos and combinada.campos[campo].fuente is not None:
            notas[campo] = "formato_volatil"
    return notas


def comparar(
    v1: dict[str, Any],
    v2: dict[str, Any],
    *,
    notas: dict[str, str] | None = None,
) -> dict[str, dict[str, Any]]:
    """Compara campo por campo los dos lados (T-405), con estado y nota.

    El parámetro ``notas`` se mantiene por compatibilidad con el contrato de
    ``scripts/F3/paridad_contable.py`` (que lo recibe del llamador); las
diferencias **esperadas por diseño** salen de :data:`NOTAS_ESPERADAS`, así que
    un llamador que no pase nada igual obtiene notas útiles.

    Estados (mismo criterio que ``scripts/F3/paridad_contable.py``):

    * ``coincide`` — los dos lados devolvieron el mismo valor.
    * ``difiere`` — valores distintos; la nota explica la diferencia **esperada**
      por diseño (``NOTAS_ESPERADAS``) o pide revisar el caso.
    * ``no_comparable`` — algún lado no devolvió el campo (la celda de la tabla es
      "—" y no se puede juzgar).
    """
    campos = sorted(set(v1) | set(v2))
    salida: dict[str, dict[str, Any]] = {}
    for campo in campos:
        a, b = v1.get(campo), v2.get(campo)
        if a is None or b is None:
            estado = "no_comparable"
            nota = (
                f"v1={'sin valor' if a is None else a!r}; "
                f"v2={'sin valor' if b is None else b!r}"
            )
            if campo in NOTAS_ESPERADAS:
                nota += f" {NOTAS_ESPERADAS[campo]}"
        elif _equivalentes(campo, a, b):
            estado = "coincide"
            nota = ""
        else:
            estado = "difiere"
            nota = NOTAS_ESPERADAS.get(campo, NOTA_DIFERENCIA_GENERICA)
        salida[campo] = {"v1": a, "v2": b, "estado": estado, "nota": nota}
    return salida


def _equivalentes(campo: str, a: Any, b: Any) -> bool:
    """Igualdad de dos valores de extracción, tolerando número vs. texto (T-405).

    Un monto puede venir como ``10203.03`` (número, v2) y como ``"10203.03"``
    (texto, v1): son el mismo dato. Se comparan normalizando a texto **solo** para
    los números — nunca se interpreta el texto como número (eso sería re-hacer la
    normalización de T-402, que tiene sus propios tests).
    """
    if isinstance(a, (int, float)) and isinstance(b, str):
        return str(a) == b
    if isinstance(b, (int, float)) and isinstance(a, str):
        return str(b) == a
    if campo == "tipo_comprobante" and isinstance(a, str) and isinstance(b, str):
        return a.strip().upper() == b.strip().upper()
    return a == b


# ---------------------------------------------------------------------------
# Lado v1: entorno preparado y corrida (patrón de scripts/F3/paridad_contable.py)
# ---------------------------------------------------------------------------


def preparar_v1(destino: Path) -> Path:
    """Arma un ``v1/`` ejecutable en ``destino`` (T-405).

    v1 resuelve sus prompts como ``Path(__file__).resolve().parent / "prompts"``
    (``v1/extraction_pipeline.py``) y en este repo esa carpeta está **vacía** (no
    versionada): los YAML viven en ``prompts/`` en la raíz. Se copia ``v1/`` (sin
    ``__pycache__``) y se le agregan los YAML de la raíz, reproduciendo la
    disposición que v1 espera. Se hace en un directorio temporal para **no mutar
    el repo** ni dejar sidecars.
    """
    v1_destino = destino / "v1"
    shutil.copytree(
        V1_DIR,
        v1_destino,
        ignore=shutil.ignore_patterns("__pycache__", "*.pyc"),
    )
    (v1_destino / "prompts").mkdir(parents=True, exist_ok=True)
    for yaml in sorted(PROMPTS_RAIZ.glob("*.yaml")):
        shutil.copy2(yaml, v1_destino / "prompts" / yaml.name)
    facturacion = PROMPTS_RAIZ / "facturacion"
    if facturacion.is_dir():
        destino_facturacion = v1_destino / "prompts" / "facturacion"
        destino_facturacion.mkdir(parents=True, exist_ok=True)
        for yaml in sorted(facturacion.glob("*.yaml")):
            shutil.copy2(yaml, destino_facturacion / yaml.name)
    return v1_destino


def correr_v1(
    v1_preparado: Path,
    markdown: Path,
    *,
    modelo: str,
    salida_dir: Path,
) -> dict[str, Any]:
    """Corre `extraction_pipeline.py` de v1 sobre un markdown (T-405).

    Invoca el script como **subproceso** (v1 no es un paquete instalable: usa
    imports absolutos ``from lib...``) y le pasa ``-o`` para que agregue el
    resultado en un JSON propio en vez de escribir el sidecar junto al markdown.

    Devuelve el dict del documento (``{"archivo", "extracciones": {...}}``) o
    ``{}`` si no se pudo leer la salida.
    """
    salida_json = salida_dir / f"v1_{markdown.stem}.json"
    comando = [
        sys.executable,
        "extraction_pipeline.py",
        str(markdown),
        "-m",
        modelo,
        "-o",
        str(salida_json),
    ]
    proceso = subprocess.run(
        comando,
        cwd=v1_preparado,
        capture_output=True,
        text=True,
        timeout=3600,
    )
    if not salida_json.exists():
        print(
            f"  ⚠️  v1 no produjo salida para {markdown.name}: "
            f"{(proceso.stderr or proceso.stdout or '').strip()[:200]}",
            file=sys.stderr,
        )
        return {}
    try:
        datos = json.loads(salida_json.read_text(encoding="utf-8"))
    except ValueError:
        return {}
    if isinstance(datos, list):
        return datos[0] if datos else {}
    return datos if isinstance(datos, dict) else {}


def campos_desde_v1(entrada: dict[str, Any], *, paso: str = PASO_FACTURA) -> dict[str, Any]:
    """Campos del paso indicado de la salida de v1 (T-405).

    v1 devuelve el JSON plano del prompt: ``{campo: valor}``. Se toman solo los
    campos pedidos (el resto son las decisiones de v1: ``comprobante_valido``,
    ``categoria_gasto``, etc., que quedan fuera de la paridad por ADR-001).
    """
    extracciones = entrada.get("extracciones") or {}
    paso_datos = extracciones.get(paso) or {}
    if not isinstance(paso_datos, dict):
        return {}
    return {campo: valor for campo, valor in paso_datos.items()}


# ---------------------------------------------------------------------------
# Lado v2: pipeline real
# ---------------------------------------------------------------------------


def correr_v2(
    documento: Path,
    markdown: str,
    *,
    modelo: str | None,
    max_workers: int | None = None,
):
    """Corre el pipeline de extracción de v2 sobre un documento (T-405).

    Recibe el ``markdown`` ya procesado por F1 (el llamador lo corre una sola vez
    y se lo pasa también a v1, para que los dos lados vean exactamente el mismo
    texto). Devuelve la ``CombinedEvidence`` (T-404) y la ``ExtraccionEvidencia``
    (para el detalle de fuentes).
    """
    from voucherflow.models.ollama import OllamaClient
    from voucherflow.validation.vistas import preparar_vista_fiel

    vista = preparar_vista_fiel(documento, str(documento))
    extraccion = extraer(
        OllamaClient(),
        markdown=markdown,
        vista=vista,
        documento_id=str(documento),
        modelo=modelo,
        max_workers=max_workers,
    )
    combinada = combinar_evidencia(
        str(documento), list(extraccion.evidencias_por_fuente().values())
    )
    return combinada, extraccion


# ---------------------------------------------------------------------------
# Reporte
# ---------------------------------------------------------------------------


def _leyenda(estado: str) -> str:
    """Símbolo del estado (mismo criterio que el reporte de F3)."""
    return {"coincide": "=", "difiere": "≠", "no_comparable": "?"}.get(estado, "?")


def main() -> None:
    parser = argparse.ArgumentParser(
        description=(
            "Paridad de extracción v2 vs. v1 sobre el subconjunto real de F4 "
            "(requiere Ollama y v1/)"
        )
    )
    parser.add_argument(
        "-m",
        "--modelo",
        default=MODELO_POR_DEFECTO,
        help=f"Modelo de Ollama para los dos lados (default: {MODELO_POR_DEFECTO})",
    )
    parser.add_argument("--detalle", action="store_true", help="traza por campo")
    parser.add_argument("--json", type=Path, help="reporte agregado en JSON")
    parser.add_argument(
        "--solo-normalizacion",
        action="store_true",
        help="no corre los modelos: solo verifica la procedencia de las reglas",
    )
    args = parser.parse_args()

    if args.solo_normalizacion:
        print(
            "La verificación de las reglas de normalización vive en la suite "
            "default: `python -m pytest tests/test_extraction_paridad.py -q`"
        )
        return

    if not V1_DIR.exists():
        print(f"No existe {V1_DIR}: la paridad real requiere v1/ en el repo.", file=sys.stderr)
        sys.exit(2)

    manifiesto = json.loads(SUBCONJUNTO.read_text(encoding="utf-8"))
    documentos = [
        RAIZ_REPO / caso["ruta"] for caso in manifiesto.get("real", [])
    ]
    documentos = [d for d in documentos if d.exists()]

    print(
        "Paridad de extracción F4 (T-405)\n"
        f"    modelo: {args.modelo}\n"
        f"    documentos: {len(documentos)}\n"
        "    campos comparados: los NORMALIZADOS de lectura "
        "(los de decisión quedan fuera por ADR-001)"
    )

    totales: dict[str, int] = {}
    reporte: list[dict[str, Any]] = []

    with tempfile.TemporaryDirectory(prefix="paridad_f4_") as temporal:
        temporal_dir = Path(temporal)
        v1_preparado = preparar_v1(temporal_dir)
        salida_dir = temporal_dir / "salidas"
        salida_dir.mkdir(parents=True, exist_ok=True)

        for documento in documentos:
            print(f"\n{documento.name}")
            # v2 corre F1→F2→F4 y devuelve además el markdown de F1, que es el
            # mismo insumo que consume v1 (así los dos lados ven el mismo texto).
            from voucherflow.api import process as process_v2

            resultado_f1 = process_v2(str(documento))
            combinada, extraccion = correr_v2(
                documento, resultado_f1.markdown, modelo=args.modelo
            )
            markdown_path = salida_dir / f"{documento.stem}.md"
            markdown_path.write_text(resultado_f1.markdown or "", encoding="utf-8")

            entrada_v1 = correr_v1(
                v1_preparado, markdown_path, modelo=args.modelo, salida_dir=salida_dir
            )
            campos_subconjunto = list(manifiesto.get("campos_paridad", CAMPOS_EXTRACCION))
            v1_campos = campos_desde_v1(entrada_v1)
            v2_campos = campos_desde_v2(combinada, campos=campos_subconjunto)
            notas = notas_de_proyeccion(combinada)

            comparacion = comparar(
                {c: v for c, v in v1_campos.items() if c in campos_subconjunto},
                v2_campos,
                notas=notas,
            )
            coincidencias = sum(
                1 for info in comparacion.values() if info["estado"] == "coincide"
            )
            comparables = sum(
                1 for info in comparacion.values() if info["estado"] != "no_comparable"
            )
            fuentes = [fuente.value for fuente in extraccion.evidencias_por_fuente()]
            print(
                f"  fuentes: {fuentes} · campos comparables: {comparables} · "
                f"coinciden: {coincidencias}"
            )
            for campo, info in comparacion.items():
                if info["estado"] == "coincide" and not args.detalle:
                    continue
                print(
                    f"    {_leyenda(info['estado'])} {campo}: "
                    f"v1={info['v1']!r} v2={info['v2']!r}"
                )
                if info["nota"]:
                    print(f"        {info['nota']}")
                if info["estado"] in totales:
                    totales[info["estado"]] += 1
                else:
                    totales[info["estado"]] = 1
            reporte.append(
                {
                    "documento": str(documento),
                    "comparacion": comparacion,
                    "v1_campos": v1_campos,
                    "v2_campos": v2_campos,
                }
            )

    print("\nTotales por estado")
    for estado in ("coincide", "difiere", "no_comparable"):
        if estado in totales:
            print(f"  {_leyenda(estado)} {estado}: {totales[estado]}")

    if args.json:
        args.json.write_text(
            json.dumps(
                {"modelo": args.modelo, "totales": totales, "casos": reporte},
                ensure_ascii=False,
                indent=2,
            ),
            encoding="utf-8",
        )
        print(f"\nReporte guardado en {args.json}")

    # Honestidad del alcance: un "difiere" no es automáticamente una regresión.
    if totales.get("difiere"):
        print(
            "\n⚠️  Hay diferencias. El DoD admite **paridad o mejora**: revisá cada "
            "caso (el detalle de la nota explica la diferencia esperable) antes de "
            "reportarlo como regresión."
        )


if __name__ == "__main__":
    main()

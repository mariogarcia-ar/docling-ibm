#!/usr/bin/env python
"""Inspecciona T-301 (F3) — motor de reglas R1-R7 del tipo/letra en código.

**Fase**: F3 (clasificación) · **Tarea**: T-301 · **Épica**: E-CLAS-1.

Muestra, sin Ollama ni Docling (el motor es **determinístico**, ADR-006):

  1. Los **tres registros** de reglas con su `id`, `prioridad`, `tipo` y
     `detalle` legible (``--reglas``).
  2. La **decisión** de :func:`voucherflow.classification.clasificar_tipo_comprobante`
     sobre un conjunto de **escenarios sintéticos** que cubren R1..R7, el cruce
     negocio-vs-documento, la discrepancia con alerta R7 y los casos parciales
     (sin evidencia de lectura / sin condiciones fiscales).
  3. Opcionalmente, la decisión sobre un **contexto propio** en JSON
     (shape plano o el anidado del prompt WIP: `emisor`/`receptor`/`ocr`), o
     sobre un contexto de referencia con `--preferencia-letra negocio`.

Por cada escenario imprime: tipo esperado por negocio, tipo detectado por
documento, **letra final**, certeza, reglas aplicadas, alertas y campos que
faltaron (`campos_desconocidos`). Con `--json` escribe el reporte completo
(incluido el `detalle` de auditoría de cada caso) para adjuntar a la bitácora.

Los escenarios declaran su **expectativa**; el script marca ✅/❌ comparando
contra el resultado real (es una verificación de humo legible, no reemplaza a
`tests/test_rules_tipo_comprobante.py`).

Uso:
    python scripts/F3/t301.py                     # escenarios sintéticos
    python scripts/F3/t301.py --reglas            # además, la tabla de reglas
    python scripts/F3/t301.py --detalle           # traza de reglas por caso
    python scripts/F3/t301.py --caso R3_exportacion --caso R2A_R4_B_conflicto_R7
    python scripts/F3/t301.py --contexto contexto.json
    python scripts/F3/t301.py --contexto contexto.json --preferencia-letra negocio
    python scripts/F3/t301.py --json /tmp/t301.json

Ejemplos:
    python scripts/F3/t301.py --reglas
    python scripts/F3/t301.py --contexto ../files/2025-08/2D2C9343/contexto.json --detalle

Nota: no requiere Ollama ni Docling (motor de reglas en código, ADR-006). Para
la paridad real con v1 (`-M 11.1`) ver T-305 (`scripts/F3/paridad_11_1.py`).
"""

from __future__ import annotations

import argparse
import json
import sys
from dataclasses import asdict
from pathlib import Path
from typing import Any

# El paquete usa layout src/; si se corre sin instalar, se agrega src/ al path.
_SRC = Path(__file__).resolve().parents[2] / "src"
if str(_SRC) not in sys.path:
    sys.path.insert(0, str(_SRC))

from voucherflow.classification import clasificar_tipo_comprobante  # noqa: E402
from voucherflow.rules import (  # noqa: E402
    REGISTRO_CONFLICTO,
    REGISTRO_LECTURA,
    REGISTRO_NEGOCIO,
    ContextoTipoComprobante,
)
from voucherflow.rules.contexto import (  # noqa: E402
    CONDICION_CONSUMIDOR_FINAL,
    CONDICION_EXENTO,
    CONDICION_MONOTRIBUTO,
    CONDICION_RI,
)

ARGENTINA = "Argentina"

# ---------------------------------------------------------------------------
# Escenarios sintéticos (deterministas) que cubren R1..R7
# ---------------------------------------------------------------------------
#
# Cada escenario es (nombre, contexto, expectativa). La expectativa declara los
# campos que el motor debe producir; los ausentes no se verifican. Portados de
# los criterios de aceptación (Gherkin) de E-CLAS-1 y del prompt WIP
# `prompts/wip/deteccion_tipo_factura.yaml`.


def _ctx(emisor: str | None, receptor: str | None, **extra: Any) -> ContextoTipoComprobante:
    """Contexto doméstico con las condiciones fiscales informadas."""
    return ContextoTipoComprobante(
        emisor_condicion_fiscal=emisor,
        receptor_condicion_fiscal=receptor,
        receptor_pais=extra.pop("receptor_pais", ARGENTINA),
        **extra,
    )


ESCENARIOS: list[tuple[str, ContextoTipoComprobante, dict[str, Any]]] = [
    # --- Reglas de negocio (R1/R2A/R2B/R3) ---
    (
        "R1_emisor_monotributo",
        _ctx(CONDICION_MONOTRIBUTO, CONDICION_CONSUMIDOR_FINAL),
        {"esperado": "C", "detectado": None, "letra": "C", "certeza": "baja", "reglas": ["R1"]},
    ),
    (
        "R2A_emisor_ri_receptor_ri",
        _ctx(CONDICION_RI, CONDICION_RI),
        {"esperado": "A", "detectado": None, "letra": "A", "certeza": "baja", "reglas": ["R2A"]},
    ),
    (
        "R2B_emisor_ri_receptor_cf",
        _ctx(CONDICION_RI, CONDICION_CONSUMIDOR_FINAL),
        {"esperado": "B", "detectado": None, "letra": "B", "certeza": "baja", "reglas": ["R2B"]},
    ),
    (
        "R3_exportacion_pisa_r1",
        _ctx(CONDICION_MONOTRIBUTO, CONDICION_CONSUMIDOR_FINAL, receptor_pais="Chile"),
        {"esperado": "E", "detectado": None, "letra": "E", "certeza": "alta", "reglas": ["R3"]},
    ),
    # --- Reglas de lectura (R4/R5/R6) en coincidencia (certeza alta) ---
    (
        "R4_recuadro_vlm",  # R2A espera A y el recuadro dice A → coincidencia
        _ctx(CONDICION_RI, CONDICION_RI, letra_recuadro_vlm="A"),
        {"esperado": "A", "detectado": "A", "letra": "A", "certeza": "alta", "reglas": ["R2A", "R4"]},
    ),
    (
        "R5_regex_texto",  # sin recuadro: resuelve la regex del encabezado
        _ctx(CONDICION_RI, CONDICION_RI, texto_encabezado_llm="FACTURA A COD. 001"),
        {"esperado": "A", "detectado": "A", "letra": "A", "certeza": "alta", "reglas": ["R2A", "R5"]},
    ),
    (
        "R6_desglose_subtotal_unico",
        _ctx(CONDICION_RI, CONDICION_CONSUMIDOR_FINAL, campos_totales="subtotal_unico"),
        {"esperado": "B", "detectado": "B", "letra": "B", "certeza": "alta", "reglas": ["R2B", "R6"]},
    ),
    (
        "R6_desglose_discriminado",
        _ctx(CONDICION_RI, CONDICION_RI, campos_totales="discriminado"),
        {"esperado": "A", "detectado": "A", "letra": "A", "certeza": "alta", "reglas": ["R2A", "R6"]},
    ),
    # --- Discrepancia + conflicto (R7) — caso del ejemplo del prompt WIP ---
    (
        "R2A_R4_B_conflicto_R7",
        _ctx(CONDICION_RI, CONDICION_RI, letra_recuadro_vlm="B"),
        {
            "esperado": "A",
            "detectado": "B",
            "letra": "B",  # preferencia_letra="documento" (default de 11.1 de v1)
            "certeza": "baja",
            "reglas": ["R2A", "R4", "R7"],
            "alertas": ["R7"],
        },
    ),
    (
        "R2A_R4_A_con_negocio",  # misma evidencia, preferencia "negocio" (WIP)
        _ctx(CONDICION_RI, CONDICION_RI, letra_recuadro_vlm="B"),
        {
            "preferencia": "negocio",
            "esperado": "A",
            "detectado": "B",
            "letra": "A",
            "certeza": "baja",
            "reglas": ["R2A", "R4", "R7"],
            "alertas": ["R7"],
        },
    ),
    # --- Casos parciales (certeza baja + campos desconocidos) ---
    (
        "solo_negocio_sin_lectura",
        _ctx(CONDICION_RI, CONDICION_RI),
        {
            "esperado": "A",
            "detectado": None,
            "letra": "A",
            "certeza": "baja",
            "campos_desconocidos": ["evidencia_lectura"],
        },
    ),
    (
        "solo_documento_sin_fiscal",
        ContextoTipoComprobante(letra_recuadro_vlm="C"),
        {
            "esperado": None,
            "detectado": "C",
            "letra": "C",
            "certeza": "baja",
            "campos_desconocidos": ["emisor.condicion_fiscal", "receptor.condicion_fiscal"],
        },
    ),
    (
        "sin_evidencia",  # nada: no se inventa letra
        ContextoTipoComprobante(),
        {
            "esperado": None,
            "detectado": None,
            "letra": None,
            "certeza": "baja",
            "reglas": [],
        },
    ),
    (
        "fuera_del_vocabulario",  # el VLM leyó "Z": no se inventa una letra
        _ctx(CONDICION_RI, CONDICION_RI, letra_recuadro_vlm="Z"),
        {"esperado": "A", "detectado": None, "letra": "A", "certeza": "baja", "reglas": ["R2A"]},
    ),
]


# ---------------------------------------------------------------------------
# Impresión
# ---------------------------------------------------------------------------


def _imprimir_reglas() -> None:
    """Imprime los tres registros declarativos con su prioridad y detalle."""
    registros = (
        ("REGISTRO_NEGOCIO", REGISTRO_NEGOCIO),
        ("REGISTRO_LECTURA", REGISTRO_LECTURA),
        ("REGISTRO_CONFLICTO", REGISTRO_CONFLICTO),
    )
    print("Registros de reglas R1-R7 (motor en código, ADR-006)")
    print("=" * 78)
    for nombre, registro in registros:
        print(f"\n{nombre} — {len(registro.reglas)} regla(s)")
        for regla in sorted(registro.reglas, key=lambda r: r.prioridad):
            print(f"  • {regla.id:<4} prioridad={regla.prioridad:<3} tipo={regla.tipo}")
            print(f"      {regla.detalle}")
    print()


def _verificar(resultado, expectativa: dict[str, Any]) -> list[str]:
    """Compara el resultado real con la expectativa; devuelve las diferencias."""
    problemas: list[str] = []
    comparaciones = (
        ("esperado", resultado.tipo_esperado_por_negocio),
        ("detectado", resultado.tipo_detectado_por_documento),
        ("letra", resultado.letra),
        ("certeza", resultado.certeza),
    )
    for clave, real in comparaciones:
        if clave in expectativa and expectativa[clave] != real:
            problemas.append(f"{clave}={real!r} (esperado {expectativa[clave]!r})")
    if "reglas" in expectativa and sorted(expectativa["reglas"]) != sorted(resultado.reglas_aplicadas):
        problemas.append(
            f"reglas={resultado.reglas_aplicadas} (esperado {expectativa['reglas']})"
        )
    if "alertas" in expectativa:
        disparadas = [a["regla"] for a in resultado.alertas]
        if sorted(expectativa["alertas"]) != sorted(disparadas):
            problemas.append(f"alertas={disparadas} (esperado {expectativa['alertas']})")
    if "campos_desconocidos" in expectativa:
        faltan = [c for c in expectativa["campos_desconocidos"] if c not in resultado.campos_desconocidos]
        if faltan:
            problemas.append(f"campos_desconocidos sin {faltan}")
    return problemas


def _imprimir_resultado(
    nombre: str,
    resultado,
    expectativa: dict[str, Any],
    problemas: list[str],
    *,
    detalle: bool,
) -> None:
    """Imprime una línea por escenario (y la traza de reglas si ``--detalle``)."""
    estado = "✅" if not problemas else "❌"
    letra = resultado.letra if resultado.letra is not None else "-"
    print(
        f"  {estado} {nombre:<26} "
        f"negocio={resultado.tipo_esperado_por_negocio or '-':<4} "
        f"documento={resultado.tipo_detectado_por_documento or '-':<4} "
        f"letra={letra:<4} certeza={resultado.certeza or '-':<5} "
        f"reglas={','.join(resultado.reglas_aplicadas) or '-'}"
    )
    if resultado.alertas:
        print(f"      ⚠ {resultado.alertas[0]['mensaje'].split('.')[0]}.")
    if resultado.campos_desconocidos:
        print(f"      campos desconocidos: {', '.join(resultado.campos_desconocidos)}")
    if problemas:
        print(f"      ❌ no coincide con la expectativa: {'; '.join(problemas)}")
    if detalle:
        print(f"      disparadas: {resultado.detalle.get('reglas_disparadas', {})}")
        print(f"      criterio  : {resultado.detalle.get('criterio', '')}")


def _leer_contexto(ruta: Path) -> dict[str, Any]:
    """Lee un contexto JSON (shape plano o anidado del WIP) desde disco."""
    datos = json.loads(ruta.read_text(encoding="utf-8"))
    if not isinstance(datos, dict):
        raise TypeError(
            f"El JSON de contexto debe ser un objeto; recibido: {type(datos).__name__}."
        )
    return datos


def main() -> None:
    parser = argparse.ArgumentParser(
        description=(
            "Inspecciona el motor de reglas R1-R7 del tipo/letra (F3 / T-301) "
            "sobre escenarios sintéticos o un contexto JSON. No requiere Ollama."
        )
    )
    parser.add_argument(
        "--caso",
        action="append",
        help="Filtra por nombre de escenario (repetible). Default: todos.",
    )
    parser.add_argument("--contexto", type=Path, help="Contexto propio en JSON (plano o WIP).")
    parser.add_argument(
        "--preferencia-letra",
        choices=("documento", "negocio"),
        default="documento",
        help="Precedencia ante discrepancia (default: documento, política de 11.1).",
    )
    parser.add_argument("--reglas", action="store_true", help="Imprime la tabla de reglas.")
    parser.add_argument("--detalle", action="store_true", help="Traza de reglas por escenario.")
    parser.add_argument("--json", type=Path, help="Escribe el reporte completo en JSON.")
    args = parser.parse_args()

    if args.reglas:
        _imprimir_reglas()

    reporte: dict[str, Any] = {
        "tarea": "T-301",
        "fase": "F3",
        "preferencia_letra": args.preferencia_letra,
        "escenarios": [],
    }
    fallos = 0

    # --- Contexto propio (--contexto) ---
    if args.contexto is not None:
        if not args.contexto.exists():
            print(f"No existe el contexto: {args.contexto}", file=sys.stderr)
            sys.exit(2)
        ctx = ContextoTipoComprobante.desde_dict(_leer_contexto(args.contexto))
        resultado = clasificar_tipo_comprobante(ctx, preferencia_letra=args.preferencia_letra)
        print(f"Contexto propio: {args.contexto}")
        print("=" * 78)
        _imprimir_resultado(
            "contexto_json", resultado, {}, [], detalle=True
        )
        print()
        reporte["contexto_json"] = {
            "contexto": ctx.como_dict(),
            "resultado": asdict(resultado),
        }

    # --- Escenarios sintéticos ---
    seleccionados = [
        (nombre, ctx, exp)
        for nombre, ctx, exp in ESCENARIOS
        if not args.caso or nombre in set(args.caso)
    ]
    if args.caso and not seleccionados:
        print(
            f"Ningún escenario coincide con {args.caso}. "
            f"Disponibles: {', '.join(n for n, _, _ in ESCENARIOS)}",
            file=sys.stderr,
        )
        sys.exit(2)

    if seleccionados:
        print(f"Escenarios sintéticos del motor R1-R7 (preferencia_letra={args.preferencia_letra})")
        print("=" * 78)
        for nombre, ctx, expectativa in seleccionados:
            preferencia = expectativa.get("preferencia", args.preferencia_letra)
            resultado = clasificar_tipo_comprobante(ctx, preferencia_letra=preferencia)
            problemas = _verificar(resultado, expectativa)
            fallos += int(bool(problemas))
            _imprimir_resultado(nombre, resultado, expectativa, problemas, detalle=args.detalle)
            reporte["escenarios"].append(
                {
                    "nombre": nombre,
                    "preferencia_letra": preferencia,
                    "contexto": ctx.como_dict(),
                    "expectativa": expectativa,
                    "coincide": not problemas,
                    "diferencias": problemas,
                    "resultado": asdict(resultado),
                }
            )
        print()
        total = len(seleccionados)
        print(f"Escenarios verificados: {total - fallos}/{total} coinciden con la expectativa.")
        reporte["escenarios_total"] = total
        reporte["escenarios_ok"] = total - fallos

    if args.json:
        args.json.write_text(
            json.dumps(reporte, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        print(f"Reporte JSON: {args.json}")

    # Salida no-cero si algún escenario no coincide (útil en CI/smoke manual).
    if fallos:
        sys.exit(1)


if __name__ == "__main__":
    main()

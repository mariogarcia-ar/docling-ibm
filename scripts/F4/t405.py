#!/usr/bin/env python
"""Métricas del DoD de F4 — extracción (F4 / T-405).

**Fase**: F4 (extracción) · **Tarea**: T-405 · **Épica**: E-EXT.

Es el **reporte de cierre** de la fase (F4-subplan §10, mismo patrón que
``scripts/F3/t305.py``): agrega en un solo lugar las métricas del DoD de F4 y,
cuando se pide, corre también la **paridad real** con v1 delegando en
``scripts/F4/paridad_extraccion.py`` (no la reimplementa).

Dos niveles de ejecución (``--subset``)
---------------------------------------

==============================  =============================================
Nivel                           Qué corre
==============================  =============================================
``determinista`` (**default**)  El tramo sin red: paridad de las **reglas de
                                normalización** con las de v1 y paridad
                                **estructural** de la extracción (la evidencia
                                de v2 proyectada al JSON plano de v1). Sin
                                Ollama, sin Docling y sin v1.
``origen``                      Además corre la **paridad real** con v1
                                (`paridad_extraccion.py`) y la extracción real
                                sobre los documentos del golden.
==============================  =============================================

Métricas que reporta
--------------------

Del **tramo determinista** (siempre):

1. **Exactitud de las reglas de normalización** — cada regla de v2 que cita una
   regla de v1 debe dar el valor canónico en todos sus casos (100%: es código).
2. **Paridad estructural de la extracción** — campos normalizados que coinciden
   con el JSON plano que v1 habría devuelto, por caso y en total.
3. **% de campos con fragmento de sustento** — el contrato de F0 exige sostén no
   vacío; debe ser 100%.
4. **Cobertura del modo genérico** (`kvg`) — las claves obligatorias del modo.

De la **corrida real** (con ``--subset origen``):

5. **Paridad de campos normalizados con v1** (coincidencia por campo).
6. **Tasa de acuerdo VLM vs. LLM** — informativa: alimenta la tabla de
   precedencia de T-404.

Decisión de alcance
-------------------
La curación del golden con contador sigue pendiente (F2 §2.5), así que **no** se
reporta como medida la exactitud del golden completo (F4-subplan §3.5). El DoD de
F4 exige **paridad o mejora** con v1 en campos normalizados: si un campo difiere,
el reporte muestra la diferencia y su nota, y **no** la convierte en verde. El
script lo declara explícitamente en su salida.

Uso:
    python scripts/F4/t405.py                        # métricas deterministas
    python scripts/F4/t405.py --detalle              # + traza por caso
    python scripts/F4/t405.py --subset origen        # + paridad real con v1
    python scripts/F4/t405.py --json /tmp/t405.json  # reporte para la bitácora

Nota: ``--subset determinista`` corre en cualquier entorno (sin Ollama ni
Docling). ``origen`` requiere Ollama local y ``v1/`` en el repo.
"""

from __future__ import annotations

import argparse
import importlib.util
import json
import sys
from pathlib import Path
from typing import Any

# El paquete usa layout src/; si se corre sin instalar, se agrega src/ al path.
_SRC = Path(__file__).resolve().parents[2] / "src"
if str(_SRC) not in sys.path:
    sys.path.insert(0, str(_SRC))

from voucherflow.extraction import (  # noqa: E402
    CAMPOS_EXTRACCION,
    CAMPO_FUENTE_LECTURA,
    CLAVE_CAMPOS,
    combinar_evidencia,
    construir_source_evidence,
    normalizar_campo,
    parsear_evidencia_extraccion,
    veredicto_raw_de_evidencia,
)
from voucherflow.extraction.key_value import (  # noqa: E402
    REGLA_POR_CAMPO,
    normalizar_evidencia,
    regla_de_campo,
)
from voucherflow.schemas.evidence import SourceEvidence  # noqa: E402

# ---------------------------------------------------------------------------
# Rutas
# ---------------------------------------------------------------------------

RAIZ_V2 = Path(__file__).resolve().parents[2]
RAIZ_REPO = RAIZ_V2.parent
F4_DIR = RAIZ_V2 / "tests" / "golden" / "F4"
SUBCONJUNTO = F4_DIR / "subconjunto.json"
PROMPTS_RAIZ = RAIZ_REPO / "prompts"
PARIDAD_EXTRAECCION = RAIZ_V2 / "scripts" / "F4" / "paridad_extraccion.py"

#: Umbrales del tramo determinista (deben ser 100%: la regla es código).
UMBRAL_REGLAS = 100.0
UMBRAL_SUSTENTO = 100.0


def _cargar_json(ruta: Path) -> dict:
    return json.loads(ruta.read_text(encoding="utf-8"))


def _cargar_modulo_script(ruta: Path):
    """Carga un script de ``scripts/F4/`` sin ejecutar su ``main`` (patrón T-305)."""
    especificacion = importlib.util.spec_from_file_location(f"_{ruta.stem}_t405", ruta)
    modulo = importlib.util.module_from_spec(especificacion)
    assert especificacion.loader is not None
    especificacion.loader.exec_module(modulo)
    return modulo


# ---------------------------------------------------------------------------
# Métricas del tramo determinista
# ---------------------------------------------------------------------------


def _evidencia_combinada(lectura: dict[str, Any], fuente: str):
    """Pipeline determinista de F4 sobre una lectura sintética (T-401..T-404)."""
    crudo = json.dumps(
        {CAMPO_FUENTE_LECTURA: fuente, CLAVE_CAMPOS: lectura}, ensure_ascii=False
    )
    interpretada = parsear_evidencia_extraccion(crudo, fuente=fuente)
    normalizada = normalizar_evidencia(interpretada).evidencia
    veredicto = veredicto_raw_de_evidencia(normalizada)
    source = construir_source_evidence(normalizada, veredicto=veredicto)
    return combinar_evidencia("t405", [source]), source


def metrica_reglas(subconjunto: dict) -> dict[str, Any]:
    """Exactitud de las reglas de normalización contra las de v1 (métrica 1).

    Cada regla declara de qué regla de v1 viene; acá se corre v2 sobre sus casos y
    se cuenta la coincidencia. Además se verifica que el texto citado siga
    existiendo en el prompt de v1 (si no, la procedencia sería falsa).
    """
    total = coinciden = 0
    procedencia_ok = True
    detalle: list[dict[str, Any]] = []
    for regla in subconjunto["normalizacion"]:
        prompt = PROMPTS_RAIZ / Path(regla["prompt_v1"]).relative_to("prompts")
        if not prompt.exists() or regla["texto_v1"] not in prompt.read_text(encoding="utf-8"):
            procedencia_ok = False
        casos_ok = 0
        for caso in regla["casos"]:
            total += 1
            resultado = normalizar_campo(caso["campo"], caso["crudo"])
            if caso["esperado"] == "estructurado":
                ok = bool(resultado.items)
            else:
                ok = resultado.valor == caso["esperado"]
            coinciden += int(ok)
            casos_ok += int(ok)
        detalle.append(
            {
                "regla": regla["id"],
                "regla_v2": regla["regla_v2"],
                "regla_v1": regla["regla_v1"],
                "casos": len(regla["casos"]),
                "coinciden": casos_ok,
            }
        )
    return {
        "reglas": len(subconjunto["normalizacion"]),
        "casos": total,
        "coinciden": coinciden,
        "exactitud": round(100.0 * coinciden / total, 2) if total else 0.0,
        "procedencia_verificada": procedencia_ok,
        "detalle": detalle,
    }


def metrica_extraccion(subconjunto: dict, script: Any) -> dict[str, Any]:
    """Paridad estructural de la extracción y sostén de los campos (métricas 2 y 3)."""
    total = coinciden = 0
    campos_totales = campos_con_sustento = 0
    detalle: list[dict[str, Any]] = []
    for caso in subconjunto["extraccion"]:
        combinada, source = _evidencia_combinada(caso["lectura"], caso["fuente"])
        campos = caso.get("campos") or subconjunto["campos_paridad"]
        v2 = script.campos_desde_v2(combinada, campos=campos)
        esperados = caso.get("v1_esperado") or caso.get("v1_esperado_generico") or {}
        comparacion = script.comparar(esperados, v2)
        caso_ok = caso_total = 0
        for campo, esperado in esperados.items():
            caso_total += 1
            total += 1
            if comparacion[campo]["estado"] == "coincide":
                coinciden += 1
                caso_ok += 1
        # Sostén: el contrato de F0 exige fragmento no vacío en todos los campos.
        for campo in source.campos.values():
            campos_totales += 1
            campos_con_sustento += int(bool(campo.fragmento_sustento.strip()))
        detalle.append(
            {
                "caso": caso["id"],
                "campos": caso_total,
                "coinciden": caso_ok,
                "v2": v2,
            }
        )
    return {
        "casos": len(subconjunto["extraccion"]),
        "campos_comparados": total,
        "campos_coincidentes": coinciden,
        "exactitud": round(100.0 * coinciden / total, 2) if total else 0.0,
        "campos_totales": campos_totales,
        "campos_con_sustento": campos_con_sustento,
        "sustento": (
            round(100.0 * campos_con_sustento / campos_totales, 2)
            if campos_totales
            else 0.0
        ),
        "detalle": detalle,
    }


def metrica_generico(subconjunto: dict) -> dict[str, Any]:
    """Cobertura del modo genérico (``kvg``): sus claves obligatorias (métrica 4).

    Se consulta :func:`~voucherflow.extraction.key_value.regla_de_campo` y **no**
    el mapa ``REGLA_POR_CAMPO``: los alias del modo genérico (``monto``,
    ``fecha``…) se resuelven por la función, así que el diccionario solo los
    reportaría como "sin regla" aunque la tengan.
    """
    obligatorias = subconjunto["campos_paridad_generico"]
    con_regla = [c for c in obligatorias if regla_de_campo(c) is not None]
    return {
        "campos": obligatorias,
        "con_regla": con_regla,
        "cobertura": round(100.0 * len(con_regla) / len(obligatorias), 2)
        if obligatorias
        else 0.0,
    }


def metrica_norm_vs_contrato() -> dict[str, Any]:
    """Integridad del mapa de reglas contra el contrato del prompt (apoyo).

    No es una métrica del DoD, pero es el chequeo que hace que las métricas de
    normalización signifiquen algo: si un campo del contrato no tuviera regla, los
    casos "pasarían" sin cubrirlo.
    """
    sin_regla = [c for c in CAMPOS_EXTRACCION if c not in REGLA_POR_CAMPO]
    return {"campos_del_contrato": len(CAMPOS_EXTRACCION), "sin_regla": sin_regla}


# ---------------------------------------------------------------------------
# Corrida real (--subset origen)
# ---------------------------------------------------------------------------


def correr_origen(modelo: str | None, detalle: bool) -> dict[str, Any]:
    """Delega la paridad real en ``paridad_extraccion.py`` (T-405, nivel real)."""
    if not PARIDAD_EXTRAECCION.exists():
        print(f"No existe {PARIDAD_EXTRAECCION}", file=sys.stderr)
        return {}
    print("\nParidad REAL con v1 (delega en paridad_extraccion.py)")
    import subprocess

    comando = [sys.executable, str(PARIDAD_EXTRAECCION)]
    if modelo:
        comando.extend(["-m", modelo])
    if detalle:
        comando.append("--detalle")
    proceso = subprocess.run(comando, capture_output=True, text=True, timeout=7200)
    salida = (proceso.stdout or "") + (proceso.stderr or "")
    print(salida)
    return {"returncode": proceso.returncode, "salida": salida}


# ---------------------------------------------------------------------------
# Reporte
# ---------------------------------------------------------------------------


def _imprimir_seccion(titulo: str) -> None:
    print(f"\n{titulo}")
    print("-" * len(titulo))


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Métricas del DoD de F4 — extracción (T-405)"
    )
    parser.add_argument(
        "--subset",
        choices=("determinista", "origen"),
        default="determinista",
        help="determinista (sin red; default) u origen (+ paridad real con v1)",
    )
    parser.add_argument("--detalle", action="store_true", help="traza por caso")
    parser.add_argument("--modelo", help="modelo de Ollama para --subset origen")
    parser.add_argument("--json", type=Path, help="reporte agregado en JSON")
    args = parser.parse_args()

    subconjunto = _cargar_json(SUBCONJUNTO)
    script = _cargar_modulo_script(PARIDAD_EXTRAECCION)

    print(
        "Métricas del DoD de F4 — extracción (T-405)\n"
        f"    subconjunto: {SUBCONJUNTO.relative_to(RAIZ_REPO)} "
        f"({subconjunto['golden_version']})\n"
        f"    nivel: {args.subset}"
    )

    reporte: dict[str, Any] = {"golden_version": subconjunto["golden_version"]}

    # --- Métrica 1: reglas de normalización ---------------------------------
    _imprimir_seccion("1. Paridad de las reglas de normalización (vs. prompts de v1)")
    reglas = metrica_reglas(subconjunto)
    reporte["reglas_normalizacion"] = reglas
    print(
        f"  reglas: {reglas['reglas']} · casos: {reglas['casos']} · "
        f"coinciden: {reglas['coinciden']} → {reglas['exactitud']}%"
    )
    print(
        f"  procedencia verificada en los prompts de v1: "
        f"{'sí' if reglas['procedencia_verificada'] else 'NO'}"
    )
    if args.detalle:
        for info in reglas["detalle"]:
            print(
                f"    {info['regla']:26} {info['regla_v2']:16} "
                f"(de {info['regla_v1']}): {info['coinciden']}/{info['casos']}"
            )

    # --- Métricas 2 y 3: extracción y sostén --------------------------------
    _imprimir_seccion("2. Paridad estructural de la extracción (proyección a v1)")
    extraccion = metrica_extraccion(subconjunto, script)
    reporte["extraccion"] = extraccion
    print(
        f"  casos: {extraccion['casos']} · campos comparados: "
        f"{extraccion['campos_comparados']} · coinciden: "
        f"{extraccion['campos_coincidentes']} → {extraccion['exactitud']}%"
    )
    _imprimir_seccion("3. Sostén de los campos (contrato de F0)")
    print(
        f"  campos con fragmento de sustento: {extraccion['campos_con_sustento']}/"
        f"{extraccion['campos_totales']} → {extraccion['sustento']}%"
    )
    if args.detalle:
        for info in extraccion["detalle"]:
            print(
                f"    {info['caso']:28} campos={info['campos']:2} "
                f"coinciden={info['coinciden']}"
            )
            for campo, valor in info["v2"].items():
                print(f"        {campo}: {valor!r}")

    # --- Métrica 4: modo genérico ------------------------------------------
    _imprimir_seccion("4. Cobertura del modo genérico (kvg)")
    generico = metrica_generico(subconjunto)
    reporte["generico"] = generico
    print(
        f"  campos declarados: {len(generico['campos'])} · con regla: "
        f"{len(generico['con_regla'])} → {generico['cobertura']}%"
    )
    print(f"  (contrato fiscal: {len(CAMPOS_EXTRACCION)} campos)")

    integridad = metrica_norm_vs_contrato()
    reporte["integridad_contrato"] = integridad
    if integridad["sin_regla"]:
        print(f"  ⚠️  campos del contrato sin regla: {integridad['sin_regla']}")

    # --- Nivel real ---------------------------------------------------------
    if args.subset == "origen":
        reporte["origen"] = correr_origen(args.modelo, args.detalle)

    # --- Veredicto ----------------------------------------------------------
    fallos = 0
    _imprimir_seccion("Veredicto del tramo determinista")
    if reglas["exactitud"] < UMBRAL_REGLAS:
        print(f"  ❌ reglas de normalización: {reglas['exactitud']}% (< {UMBRAL_REGLAS}%)")
        fallos += 1
    else:
        print(f"  ✅ reglas de normalización: {reglas['exactitud']}%")
    if not reglas["procedencia_verificada"]:
        print("  ❌ la procedencia de alguna regla no está en el prompt de v1")
        fallos += 1
    if extraccion["sustento"] < UMBRAL_SUSTENTO:
        print(f"  ❌ sostén de los campos: {extraccion['sustento']}% (< {UMBRAL_SUSTENTO}%)")
        fallos += 1
    else:
        print(f"  ✅ sostén de los campos: {extraccion['sustento']}%")
    if extraccion["exactitud"] < 100.0:
        print(
            f"  ⚠️  paridad estructural: {extraccion['exactitud']}% — revisá los "
            "casos (el DoD admite paridad **o mejora** documentada)"
        )
    else:
        print(f"  ✅ paridad estructural: {extraccion['exactitud']}%")

    print(
        "\nAlcance: esto NO es la medición del golden completo (la curación con "
        "contador sigue pendiente, F2 §2.5). Mide las reglas de normalización y la "
        "proyección de la extracción sobre el subconjunto de F4; la paridad real "
        "la corre `--subset origen` (requiere Ollama y v1/)."
    )

    reporte["fallos"] = fallos
    if args.json:
        args.json.write_text(
            json.dumps(reporte, ensure_ascii=False, indent=2, default=str),
            encoding="utf-8",
        )
        print(f"\nReporte guardado en {args.json}")
    sys.exit(1 if fallos else 0)


if __name__ == "__main__":
    main()

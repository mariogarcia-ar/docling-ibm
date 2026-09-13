#!/usr/bin/env python
"""Métricas del DoD de F4 — extracción (F4 / T-405).

**Fase**: F4 (extracción) · **Tarea**: T-405 · **Épica**: E-EXT.

Es el **reporte de cierre** de la fase (F4-subplan §10, mismo patrón que
``scripts/F3/t305.py``): agrega en un solo lugar las métricas del DoD de F4 sobre
el tramo **determinista** (sin Ollama, sin Docling y sin red).

Métricas que reporta
--------------------

1. **Exactitud de las reglas de normalización** — las reglas de normalización
   deben dar el valor canónico en todos sus casos (100%: es código).
2. **Paridad estructural de la extracción** — campos normalizados que coinciden
   con el shape plano esperado, por caso y en total.
3. **% de campos con fragmento de sustento** — el contrato de F0 exige sostén no
   vacío; debe ser 100%.
4. **Cobertura del modo genérico** (``kvg``) — las claves obligatorias del modo.

Decisión de alcance
-------------------
La curación del golden con contador sigue pendiente (F2 §2.5), así que **no** se
reporta como medida la exactitud del golden completo (F4-subplan §3.5). El script
lo declara explícitamente en su salida.

Uso:
    python scripts/F4/t405.py                        # métricas deterministas
    python scripts/F4/t405.py --detalle              # + traza por caso
    python scripts/F4/t405.py --json /tmp/t405.json  # reporte para la bitácora
"""

from __future__ import annotations

import argparse
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

RAIZ_REPO = Path(__file__).resolve().parents[2]
F4_DIR = RAIZ_REPO / "tests" / "golden" / "F4"
SUBCONJUNTO = F4_DIR / "subconjunto.json"
PROMPTS_RAIZ = RAIZ_REPO / "prompts"

#: Umbrales del tramo determinista (deben ser 100%: la regla es código).
UMBRAL_REGLAS = 100.0
UMBRAL_SUSTENTO = 100.0


def _cargar_json(ruta: Path) -> dict:
    return json.loads(ruta.read_text(encoding="utf-8"))


def _proyeccion_plano(combinada: Any, campos: list[str]) -> dict[str, Any]:
    """Proyecta la evidencia combinada al shape plano (un valor por campo).

    Devuelve ``campo → valor`` del **ganador** de cada campo (la resolución por
    precedencia de T-404 ya eligió la fuente), limitado a ``campos``. Un campo
    que ninguna fuente declaró **no entra**: la ausencia es información, no un
    ``None`` que después se confunda con "no comparable".
    """
    valores: dict[str, Any] = {}
    for campo in campos:
        evidencia = combinada.campos.get(campo)
        if evidencia is None or evidencia.fuente is None:
            continue
        valores[campo] = evidencia.valor
    return valores


def _equivalentes(a: Any, b: Any) -> bool:
    """Igualdad de dos valores de extracción, tolerando número vs. texto.

    Un monto puede venir como ``12345.67`` (número) y como ``"12345.67"``
    (texto): es el mismo dato. Se compara normalizando a texto **solo** para los
    números — nunca se interpreta el texto como número (eso sería re-hacer la
    normalización de T-402, que tiene sus propios tests).
    """
    if isinstance(a, (int, float)) and isinstance(b, str):
        return str(a) == b
    if isinstance(b, (int, float)) and isinstance(a, str):
        return str(b) == a
    return a == b


def _comparar(esperados: dict[str, Any], obtenidos: dict[str, Any]) -> dict[str, dict]:
    """Compara dos proyecciones campo a campo (``coincide``/``difiere``)."""
    comparacion: dict[str, dict] = {}
    for campo, esperado in esperados.items():
        obtenido = obtenidos.get(campo)
        if esperado is None and obtenido is None:
            estado = "coincide"
        elif _equivalentes(esperado, obtenido):
            estado = "coincide"
        else:
            estado = "difiere"
        comparacion[campo] = {
            "estado": estado,
            "esperado": esperado,
            "obtenido": obtenido,
        }
    return comparacion


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
    """Exactitud de las reglas de normalización (métrica 1).

    Cada regla cita la regla de los prompts de referencia de la que viene; acá se
    corre el normalizador sobre sus casos y se cuenta la coincidencia. Además se
    verifica que el texto citado siga existiendo en el prompt de referencia (si
    no, la procedencia sería falsa).
    """
    total = coinciden = 0
    procedencia_ok = True
    detalle: list[dict[str, Any]] = []
    for regla in subconjunto["normalizacion"]:
        prompt = PROMPTS_RAIZ / Path(regla["prompt_referencia"]).relative_to("prompts")
        if not prompt.exists() or regla["texto_referencia"] not in prompt.read_text(encoding="utf-8"):
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
                "regla_normalizacion": regla["regla_normalizacion"],
                "regla_referencia": regla["regla_referencia"],
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


def metrica_extraccion(subconjunto: dict) -> dict[str, Any]:
    """Paridad estructural de la extracción y sostén de los campos (métricas 2 y 3)."""
    total = coinciden = 0
    campos_totales = campos_con_sustento = 0
    detalle: list[dict[str, Any]] = []
    for caso in subconjunto["extraccion"]:
        combinada, source = _evidencia_combinada(caso["lectura"], caso["fuente"])
        campos = caso.get("campos") or subconjunto["campos_paridad"]
        obtenidos = _proyeccion_plano(combinada, campos)
        esperados = (
            caso.get("esperado_plano")
            or caso.get("esperado_plano_generico")
            or {}
        )
        comparacion = _comparar(esperados, obtenidos)
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
                "obtenidos": obtenidos,
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
# Reporte
# ---------------------------------------------------------------------------


def _imprimir_seccion(titulo: str) -> None:
    print(f"\n{titulo}")
    print("-" * len(titulo))


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Métricas del DoD de F4 — extracción (T-405)"
    )
    parser.add_argument("--detalle", action="store_true", help="traza por caso")
    parser.add_argument("--json", type=Path, help="reporte agregado en JSON")
    args = parser.parse_args()

    subconjunto = _cargar_json(SUBCONJUNTO)

    print(
        "Métricas del DoD de F4 — extracción (T-405)\n"
        f"    subconjunto: {SUBCONJUNTO.relative_to(RAIZ_REPO)} "
        f"({subconjunto['golden_version']})"
    )

    reporte: dict[str, Any] = {"golden_version": subconjunto["golden_version"]}

    # --- Métrica 1: reglas de normalización ---------------------------------
    _imprimir_seccion("1. Exactitud de las reglas de normalización")
    reglas = metrica_reglas(subconjunto)
    reporte["reglas_normalizacion"] = reglas
    print(
        f"  reglas: {reglas['reglas']} · casos: {reglas['casos']} · "
        f"coinciden: {reglas['coinciden']} → {reglas['exactitud']}%"
    )
    print(
        f"  procedencia verificada en los prompts de referencia: "
        f"{'sí' if reglas['procedencia_verificada'] else 'NO'}"
    )
    if args.detalle:
        for info in reglas["detalle"]:
            print(
                f"    {info['regla']:26} {info['regla_normalizacion']:16} "
                f"(de {info['regla_referencia']}): {info['coinciden']}/{info['casos']}"
            )

    # --- Métricas 2 y 3: extracción y sostén --------------------------------
    _imprimir_seccion("2. Paridad estructural de la extracción")
    extraccion = metrica_extraccion(subconjunto)
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
            for campo, valor in info["obtenidos"].items():
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

    # --- Veredicto ----------------------------------------------------------
    fallos = 0
    _imprimir_seccion("Veredicto del tramo determinista")
    if reglas["exactitud"] < UMBRAL_REGLAS:
        print(f"  ❌ reglas de normalización: {reglas['exactitud']}% (< {UMBRAL_REGLAS}%)")
        fallos += 1
    else:
        print(f"  ✅ reglas de normalización: {reglas['exactitud']}%")
    if not reglas["procedencia_verificada"]:
        print("  ❌ la procedencia de alguna regla no está en el prompt de referencia")
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
        "proyección de la extracción sobre el subconjunto de F4."
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

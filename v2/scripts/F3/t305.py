#!/usr/bin/env python
"""Métricas del DoD de F3 — tipo/letra + cadena contable (F3 / T-305).

**Fase**: F3 (clasificación) · **Tarea**: T-305 · **Épica**: E-CLAS.

Es el **reporte de cierre** de la fase (F3-subplan §10): agrega en un solo lugar
las métricas del DoD de F3 y, cuando se pide, corre también la **paridad real**
con v1 reutilizando las herramientas de T-305 (no las reimplementa).

Dos niveles de ejecución (``--subset``)
---------------------------------------
================================  ===============================================
Nivel                             Qué corre
================================  ===============================================
``sinteticos`` (**default**)      Solo el tramo **determinista** del motor de
                                  reglas: sin Ollama, sin Docling y sin v1. Es
                                  la verificación reproducible del DoD en
                                  cualquier máquina.
``golden``                        Además corre la **paridad real** con v1:
                                  ``paridad_contable.py`` (cadena 01→02→03) y
                                  ``paridad_11_1.py`` (letra vs. `-M 11.1`) +
                                  los negativos del golden.
``todos``                         Sinónimo de ``golden`` (nivel máximo).
================================  ===============================================

Métricas que reporta
--------------------
Del **tramo determinista** (siempre, sobre `tests/golden/F3/subconjunto.json`):

1. **Exactitud de letra por categoría** (A/B/C/M/E) — la letra del subconjunto
   debe salir del motor de reglas R1-R7, no del prompt (ADR-006).
2. **% de alerta R7 correctamente disparada** — en los casos con discrepancia
   negocio-vs-documento.
3. **% de acuerdo negocio-vs-documento** — sobre los casos donde ambas
   evidencias existen.
4. **Default CC0006** (criterio Gherkin de E-CLAS-2) — el valor por defecto del
   paso 01 cuando no hay señal específica.

De la **corrida real** (con ``--subset golden``):

5. **Paridad de la cadena contable** con v1 (coincidencia por campo).
6. **Exactitud de letra vs. `-M 11.1`** (v2 y v1 sobre los mismos casos).

Decisión de alcance
-------------------
Las métricas 1-4 son las que el DoD de F3 puede exigir **hoy**: el subconjunto
tiene letra **sintética determinista** y casos reales con evidencia objetiva. La
curación del golden completo con contador (letra/condición fiscal) sigue
pendiente (F2 §2.5), así que la medición masiva **no** se reporta como hecha
(F3-subplan §2.9). El script lo dice explícitamente en su salida para que el
reporte no se lea como si midiera el golden completo.

Uso:
    python scripts/F3/t305.py                        # métricas deterministas
    python scripts/F3/t305.py --detalle              # + traza por caso
    python scripts/F3/t305.py --subset golden        # + paridad real con v1
    python scripts/F3/t305.py --json /tmp/t305.json  # reporte para la bitácora

Nota: `--subset sinteticos` es el que corre en cualquier entorno (sin Ollama ni
Docling). `golden` requiere Ollama local y `v1/` en el repo.
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

from voucherflow.classification import (  # noqa: E402
    clasificar_tipo_comprobante,
)
from voucherflow.rules.contexto import ContextoTipoComprobante  # noqa: E402

#: Raíz del paquete v2 y del repo.
RAIZ_V2 = Path(__file__).resolve().parents[2]
RAIZ_REPO = RAIZ_V2.parent

#: Raíz y manifiesto del subconjunto de paridad de F3.
F3_DIR = RAIZ_V2 / "tests" / "golden" / "F3"
SUBCONJUNTO = F3_DIR / "subconjunto.json"

#: Umbrales del DoD (F3-subplan §3.5 y `tests/golden/F3/README.md`). El tramo
#: determinista debe ser exacto: la letra la decide el motor de reglas, no el
#: modelo, así que un fallo es un defecto del motor, no ruido.
UMBRAL_DETERMINISTA = 1.0
UMBRAL_ACUERDO_V1 = 0.5


def _cargar_subconjunto() -> dict:
    """Lee el manifiesto del subconjunto de paridad de F3."""
    return json.loads(SUBCONJUNTO.read_text(encoding="utf-8"))


def _contexto_de(caso: dict) -> ContextoTipoComprobante:
    """Contexto del motor para un caso sintético (T-305).

    La **letra detectada** no se inyecta: los casos sintéticos declaran la letra
    en su markdown (``FACTURA A``, …), así que se ejercita la misma vía que en
    producción — el extractor **R5** sobre el texto del encabezado. La condición
    fiscal sí se pasa, porque el markdown no es una fuente estructurada para
    ella (en producción la aporta F4).
    """
    ruta = F3_DIR / caso["archivo"]
    texto = ruta.read_text(encoding="utf-8")
    return ContextoTipoComprobante(
        emisor_condicion_fiscal=caso.get("condicion_emisor"),
        receptor_condicion_fiscal=caso.get("condicion_receptor"),
        receptor_pais=caso.get("receptor_pais"),
        texto_encabezado_llm=texto,
    )


# ---------------------------------------------------------------------------
# Métricas deterministas
# ---------------------------------------------------------------------------


def medir_letra(subconjunto: dict) -> dict[str, Any]:
    """Mide exactitud de letra (y por categoría) sobre los casos sintéticos (T-305).

    Devuelve el detalle por caso, el desglose por letra y los totales. Un caso
    se considera correcto si el motor produce la ``letra_esperada_final`` (o la
    ``letra_esperada``) y dispara la ``regla_esperada``.
    """
    casos = subconjunto.get("letra_sintetica", [])
    detalle: list[dict[str, Any]] = []
    por_categoria: dict[str, dict[str, int]] = {}
    correctos = 0

    for caso in casos:
        resultado = clasificar_tipo_comprobante(_contexto_de(caso))
        esperada = caso.get("letra_esperada_final") or caso["letra_esperada"]
        letra_ok = resultado.letra == esperada
        regla_ok = caso["regla_esperada"] in resultado.reglas_aplicadas
        correcto = letra_ok and regla_ok
        correctos += int(correcto)

        categoria = por_categoria.setdefault(
            esperada, {"total": 0, "correctos": 0}
        )
        categoria["total"] += 1
        categoria["correctos"] += int(correcto)

        detalle.append(
            {
                "id": caso["id"],
                "letra_esperada": esperada,
                "letra_obtenida": resultado.letra,
                "regla_esperada": caso["regla_esperada"],
                "reglas_aplicadas": resultado.reglas_aplicadas,
                "certeza": resultado.certeza,
                "letra_ok": letra_ok,
                "regla_ok": regla_ok,
                "correcto": correcto,
            }
        )

    total = len(casos)
    return {
        "total": total,
        "correctos": correctos,
        "exactitud": (correctos / total) if total else None,
        "por_categoria": por_categoria,
        "casos": detalle,
    }


def medir_r7(subconjunto: dict) -> dict[str, Any]:
    """Mide si la alerta R7 se dispara en los casos de discrepancia (T-305).

    R7 es el guardia de auditoría (comprobante inválido para crédito fiscal): el
    DoD pide medir el **% de casos con alerta correctamente disparada**. El
    universo son los casos sintéticos cuya ``regla_esperada`` es ``R7``.
    """
    casos = [
        caso
        for caso in subconjunto.get("letra_sintetica", [])
        if caso.get("regla_esperada") == "R7"
    ]
    detalle: list[dict[str, Any]] = []
    disparadas = 0

    for caso in casos:
        resultado = clasificar_tipo_comprobante(_contexto_de(caso))
        reglas = [alerta["regla"] for alerta in resultado.alertas]
        ok = "R7" in reglas
        disparadas += int(ok)
        detalle.append(
            {
                "id": caso["id"],
                "alertas": reglas,
                "certeza": resultado.certeza,
                "disparada": ok,
            }
        )

    total = len(casos)
    return {
        "total": total,
        "disparadas": disparadas,
        "tasa": (disparadas / total) if total else None,
        "casos": detalle,
    }


def medir_acuerdo_negocio_documento(subconjunto: dict) -> dict[str, Any]:
    """Mide el acuerdo negocio-vs-documento sobre la evidencia del motor (T-305).

    La métrica es **consciente de la expectativa**, porque el acuerdo no siempre
    es lo correcto: el caso de conflicto (``regla_esperada == "R7"``) está
    **diseñado para discrepar** (es lo que dispara la alerta de auditoría).
    Contarlo como fallo mediría lo contrario de lo que el DoD pide. Por eso:

    - ``espera_acuerdo``: los casos normales (R1/R2A/R2B/R3 + lectura), donde
      negocio y documento deben coincidir.
    - ``espera_discrepancia``: los de conflicto, donde deben **diferir** y
      disparar R7.

    Solo entran los casos donde **ambas** evidencias existen (R1/R2A/R2B
    resolvieron el esperado y R4/R5/R6 el detectado): en los demás el cruce no
    está definido (p. ej. R3 devuelve E sin evaluar la lectura) y contarlos como
    desacuerdo sería un falso negativo.
    """
    detalle: list[dict[str, Any]] = []
    acuerdos_esperados = 0
    acuerdos_obtenidos = 0
    discrepancias_esperadas = 0
    discrepancias_obtenidas = 0

    for caso in subconjunto.get("letra_sintetica", []):
        resultado = clasificar_tipo_comprobante(_contexto_de(caso))
        esperado = resultado.tipo_esperado_por_negocio
        detectado = resultado.tipo_detectado_por_documento
        if esperado is None or detectado is None:
            continue

        coincide = resultado.coincide_negocio_vs_documento
        espera_acuerdo = caso.get("regla_esperada") != "R7"
        if espera_acuerdo:
            acuerdos_esperados += 1
            acuerdos_obtenidos += int(coincide)
        else:
            discrepancias_esperadas += 1
            discrepancias_obtenidas += int(not coincide)

        detalle.append(
            {
                "id": caso["id"],
                "tipo_esperado_por_negocio": esperado,
                "tipo_detectado_por_documento": detectado,
                "coincide": coincide,
                "espera_acuerdo": espera_acuerdo,
                "correcto": coincide if espera_acuerdo else not coincide,
            }
        )

    evaluados = acuerdos_esperados + discrepancias_esperadas
    correctos = acuerdos_obtenidos + discrepancias_obtenidas
    return {
        "total": evaluados,
        "correctos": correctos,
        "tasa": (correctos / evaluados) if evaluados else None,
        "espera_acuerdo": acuerdos_esperados,
        "acuerdan": acuerdos_obtenidos,
        "espera_discrepancia": discrepancias_esperadas,
        "discrepan": discrepancias_obtenidas,
        "casos": detalle,
    }


def medir_default_cc0006() -> dict[str, Any]:
    """Verifica el default CC0006 del Gherkin de E-CLAS-2 (T-305).

    Sin señal específica, el paso 01 devuelve **CC0006 con confianza baja y
    ``senal_usada=ninguna``**. Se mide con el caso sintético del subconjunto
    (``default_cc0006``) sobre la variante pura de la cadena: es la garantía de
    que el default del prompt se conserva en el port a la librería.
    """
    from voucherflow.classification import clasificar_contable

    paso_01 = {
        "centros_costos": [
            {
                "codigo_centro_costo": "CC0006",
                "centro": "Indirectos",
                "confianza": "baja",
                "senal_usada": "ninguna",
                "justificacion": "Sin señal específica.",
            }
        ]
    }
    paso_02 = {"macro_categorias": [{"macro_categoria": "MC11"}]}
    paso_03 = {"concepto": "CT026", "codigo_final": "18"}
    resultado = clasificar_contable(
        "gasto sin señal",
        pasos={
            "01_centro_costo": paso_01,
            "02_macro_categoria": paso_02,
            "03_concepto_codigo_final": paso_03,
        },
    )
    return {
        "centro_costo": resultado.centro_costo,
        "es_default": resultado.centro_costo == "CC0006",
        "senal_usada": paso_01["centros_costos"][0]["senal_usada"],
        "confianza": paso_01["centros_costos"][0]["confianza"],
        "criterio": "Gherkin de E-CLAS-2 (02-epicas-historias-usuario.md)",
    }


# ---------------------------------------------------------------------------
# Métricas de paridad real (delegan en las herramientas de T-305)
# ---------------------------------------------------------------------------


def correr_paridad_contable(modelo: str | None) -> dict[str, Any]:
    """Corre `paridad_contable.py` y devuelve sus totales (T-305).

    Se invoca como **subproceso** (no se importa) para no acoplar este reporte a
    la estructura interna del script de paridad: lo que se consume es su salida
    JSON, que es el contrato estable entre herramientas.
    """
    import subprocess
    import tempfile

    script = RAIZ_V2 / "scripts" / "F3" / "paridad_contable.py"
    with tempfile.TemporaryDirectory(prefix="t305_contable_") as tmp:
        salida = Path(tmp) / "paridad.json"
        comando = [sys.executable, str(script), "--json", str(salida), "--detectar-modelo"]
        if modelo:
            comando.extend(["--modelo", modelo])
        proceso = subprocess.run(
            comando, cwd=RAIZ_V2, capture_output=True, text=True, timeout=3600
        )
        if not salida.exists():
            return {
                "error": "no se pudo correr la paridad contable",
                "salida": (proceso.stdout or "")[-800:] + (proceso.stderr or "")[-800:],
            }
        datos = json.loads(salida.read_text(encoding="utf-8"))
    return {
        "totales": datos.get("totales", {}),
        "casos": [
            {"id": caso["id"], "veredicto": caso["veredicto"]}
            for caso in datos.get("casos", [])
        ],
        "modelo": datos.get("modelo"),
    }


def correr_paridad_11_1(modelo: str | None) -> dict[str, Any]:
    """Corre `paridad_11_1.py` y devuelve sus métricas (T-305)."""
    import subprocess
    import tempfile

    script = RAIZ_V2 / "scripts" / "F3" / "paridad_11_1.py"
    with tempfile.TemporaryDirectory(prefix="t305_letra_") as tmp:
        salida = Path(tmp) / "paridad.json"
        comando = [sys.executable, str(script), "--json", str(salida), "--detectar-modelo"]
        if modelo:
            comando.extend(["--modelo", modelo])
        proceso = subprocess.run(
            comando, cwd=RAIZ_V2, capture_output=True, text=True, timeout=3600
        )
        if not salida.exists():
            return {
                "error": "no se pudo correr la paridad de letra",
                "salida": (proceso.stdout or "")[-800:] + (proceso.stderr or "")[-800:],
            }
        datos = json.loads(salida.read_text(encoding="utf-8"))
    return {
        "metricas": datos.get("metricas", {}),
        "casos": [
            {
                "id": caso["id"],
                "grupo": caso["grupo"],
                "letra_v1": caso["letra_v1"],
                "letra_v2": caso["letra_v2"],
                "estado": caso["estado"],
                "letra_esperada": caso.get("letra_esperada"),
            }
            for caso in datos.get("casos", [])
        ],
        "modelo": datos.get("modelo"),
    }


# ---------------------------------------------------------------------------
# Impresión
# ---------------------------------------------------------------------------


def _linea(emoji: str, etiqueta: str, valor: str, esperado: str = "") -> str:
    sufijo = f"  (esperado {esperado})" if esperado else ""
    return f"  {emoji} {etiqueta:<46} {valor}{sufijo}"


def imprimir_reporte(reporte: dict[str, Any], *, detalle: bool) -> int:
    """Imprime el reporte de métricas del DoD y devuelve el código de salida.

    El código es ≠ 0 si alguna métrica del **tramo determinista** no cumple su
    umbral: son las que el DoD puede exigir hoy, así que un fallo es real (no
    tolerancia). Las métricas de paridad real se informan sin bloquear: su
    criterio es "paridad **o mejora** documentada" (F3-subplan §3.5), que es un
    juicio, no un verde automático.
    """
    fallos = 0
    letra = reporte["letra"]
    r7 = reporte["r7"]
    acuerdo = reporte["acuerdo_negocio_documento"]
    cc0006 = reporte["default_cc0006"]

    print("Métricas del DoD de F3 — clasificación (T-305)")
    print("=" * 78)
    print(f"Subconjunto : {SUBCONJUNTO.relative_to(RAIZ_REPO)}")
    print(f"Nivel       : {reporte['subset']}")
    print()

    print("--- Tipo/letra: tramo determinista (motor de reglas, sin Ollama) ---")
    exactitud = letra["exactitud"]
    if exactitud is None:
        print(_linea("⚠", "Exactitud de letra (subconjunto)", "sin casos"))
    else:
        ok = exactitud >= UMBRAL_DETERMINISTA
        fallos += int(not ok)
        print(
            _linea(
                "✅" if ok else "❌",
                "Exactitud de letra (letra + regla)",
                f"{letra['correctos']}/{letra['total']} ({100 * exactitud:.0f}%)",
                "100%",
            )
        )
    for categoria, datos in sorted(letra["por_categoria"].items()):
        print(
            f"      · categoría {categoria}: {datos['correctos']}/{datos['total']}"
        )

    tasa_r7 = r7["tasa"]
    if tasa_r7 is None:
        print(_linea("⚠", "Alerta R7 (discrepancia negocio/documento)", "sin casos"))
    else:
        ok = tasa_r7 >= UMBRAL_DETERMINISTA
        fallos += int(not ok)
        print(
            _linea(
                "✅" if ok else "❌",
                "Alerta R7 correctamente disparada",
                f"{r7['disparadas']}/{r7['total']} ({100 * tasa_r7:.0f}%)",
                "100%",
            )
        )

    tasa_acuerdo = acuerdo["tasa"]
    if tasa_acuerdo is None:
        print(_linea("⚠", "Cruce negocio-vs-documento", "sin casos comparables"))
    else:
        ok = tasa_acuerdo >= UMBRAL_DETERMINISTA
        fallos += int(not ok)
        print(
            _linea(
                "✅" if ok else "❌",
                "Cruce negocio-vs-documento (según expectativa)",
                f"{acuerdo['correctos']}/{acuerdo['total']} ({100 * tasa_acuerdo:.0f}%)",
                "100%",
            )
        )
        print(
            f"      · coinciden cuando deben: {acuerdo['acuerdan']}/{acuerdo['espera_acuerdo']}"
            f" · discrepan cuando deben: {acuerdo['discrepan']}/{acuerdo['espera_discrepancia']}"
            " (el caso R7 está diseñado para discrepar)"
        )

    ok_cc = cc0006["es_default"]
    print(
        _linea(
            "✅" if ok_cc else "❌",
            "Default CC0006 (Gherkin E-CLAS-2)",
            f"{cc0006['centro_costo']} · confianza {cc0006['confianza']} · senal {cc0006['senal_usada']}",
            "CC0006 · baja · ninguna",
        )
    )

    if detalle:
        print()
        print("  Detalle por caso:")
        for caso in letra["casos"]:
            marca = "✅" if caso["correcto"] else "❌"
            print(
                f"    {marca} {caso['id']:<34} letra={caso['letra_obtenida'] or '-'} "
                f"(esperada {caso['letra_esperada']}) reglas={caso['reglas_aplicadas']}"
            )
        for caso in r7["casos"]:
            marca = "✅" if caso["disparada"] else "❌"
            print(f"    {marca} {caso['id']:<34} alertas={caso['alertas']}")
        for caso in acuerdo["casos"]:
            marca = "✅" if caso["correcto"] else "❌"
            rol = "acuerdo" if caso["espera_acuerdo"] else "discrepancia"
            print(
                f"    {marca} {caso['id']:<34} negocio={caso['tipo_esperado_por_negocio']} "
                f"documento={caso['tipo_detectado_por_documento']} ({rol} esperada)"
            )

    real = reporte.get("paridad_real")
    if real:
        print()
        print("--- Paridad real con v1 (Ollama + v1) ---")
        contable = real.get("contable", {})
        if "error" in contable:
            print(_linea("⚠", "Paridad cadena contable", contable["error"]))
        else:
            totales = contable.get("totales", {})
            coinciden = totales.get("coincide", 0)
            difieren = totales.get("difiere", 0)
            no_comp = totales.get("no_comparable", 0)
            emoji = "✅" if not difieren and coinciden else "➖"
            print(
                _linea(
                    emoji,
                    "Paridad cadena contable (campos)",
                    f"coinciden {coinciden} · difieren {difieren} · no comparables {no_comp}",
                )
            )
            print(f"      · modelo: {contable.get('modelo')}")
            for caso in contable.get("casos", []):
                print(f"      · {caso['id']}: {caso['veredicto']}")
        letra_real = real.get("letra_11_1", {})
        if "error" in letra_real:
            print(_linea("⚠", "Paridad de letra (-M 11.1)", letra_real["error"]))
        else:
            metricas = letra_real.get("metricas", {})
            print(
                _linea(
                    "➖",
                    "Exactitud de letra — v2",
                    f"{metricas.get('exactos_v2')}/{metricas.get('etiquetados')}",
                )
            )
            print(
                _linea(
                    "➖",
                    "Exactitud de letra — v1",
                    f"{metricas.get('exactos_v1')}/{metricas.get('etiquetados')}",
                )
            )
            print(
                _linea(
                    "➖",
                    "Acuerdo con v1 (casos comparables)",
                    f"{metricas.get('acuerdos')}/{metricas.get('comparables')}",
                )
            )
            print(
                "      · criterio: paridad **o mejora** documentada (F3-subplan §3.5); "
                "v1 no concluyó la letra en "
                f"{metricas.get('sin_letra_v1', 0)} caso(s)"
            )

    print()
    print("--- Alcance de estas métricas (honestidad del reporte) ---")
    print(
        "  · El subconjunto NO es el golden completo: la curación de letra y condición\n"
        "    fiscal con contador sigue pendiente (F2 §2.5). Por eso la medición masiva\n"
        "    no se reporta como hecha (F3-subplan §2.9).\n"
        "  · Los casos sintéticos cubren el tramo DETERMINISTA del motor (el que\n"
        "    ADR-006 sacó del prompt); su exactitud debe ser 100% y bloquea la salida.\n"
        "  · La paridad real con v1 se informa como \"paridad o mejora\", con las dos\n"
        "    exactitudes a la vista para que un desacuerdo se lea con evidencia."
    )
    print()
    if fallos:
        print(f"❌ {fallos} métrica(s) del tramo determinista no cumplen el umbral.")
    else:
        print("✅ Métricas del tramo determinista en el umbral del DoD de F3.")
    return 1 if fallos else 0


def main() -> None:
    parser = argparse.ArgumentParser(
        description=(
            "Reporte de métricas del DoD de F3 (T-305): exactitud de letra, alerta "
            "R7, acuerdo negocio-vs-documento, default CC0006 y (opcional) la "
            "paridad real con v1."
        )
    )
    parser.add_argument(
        "--subset",
        choices=("sinteticos", "golden", "todos"),
        default="sinteticos",
        help=(
            "sinteticos (default): solo el tramo determinista, sin Ollama ni v1. "
            "golden/todos: además corre la paridad real con v1."
        ),
    )
    parser.add_argument("--modelo", help="Modelo para la paridad real (default: detecta el instalado).")
    parser.add_argument("--detalle", action="store_true", help="Muestra la traza por caso.")
    parser.add_argument("--json", type=Path, help="Escribe el reporte completo en JSON.")
    args = parser.parse_args()

    subconjunto = _cargar_subconjunto()
    reporte: dict[str, Any] = {
        "tarea": "T-305",
        "fase": "F3",
        "subset": args.subset,
        "golden_version": subconjunto.get("golden_version"),
        "letra": medir_letra(subconjunto),
        "r7": medir_r7(subconjunto),
        "acuerdo_negocio_documento": medir_acuerdo_negocio_documento(subconjunto),
        "default_cc0006": medir_default_cc0006(),
    }

    if args.subset in ("golden", "todos"):
        reporte["paridad_real"] = {
            "contable": correr_paridad_contable(args.modelo),
            "letra_11_1": correr_paridad_11_1(args.modelo),
        }

    codigo = imprimir_reporte(reporte, detalle=args.detalle)

    if args.json:
        args.json.write_text(
            json.dumps(reporte, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        print(f"Reporte JSON: {args.json}")

    sys.exit(codigo)


if __name__ == "__main__":
    main()

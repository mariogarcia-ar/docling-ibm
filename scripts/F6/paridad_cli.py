#!/usr/bin/env python
"""Paridad de la superficie del CLI v2 vs. v1 (F6 / T-604).

**Fase**: F6 (cliente) · **Tarea**: T-604 · **Épica**: E-CLI.

Mide el DoD de F6 —"los comandos de v1 tienen equivalente en v2 con resultados
comparables (o mejores) sobre una muestra acordada de `files/`"— en los dos
niveles que **sí** tienen sustento objetivo, y **declara** lo que queda afuera.

Qué mide
--------

**(a) La superficie de invocación.** Cada script de v1 con CLI tiene su equivalente
en v2 y sus banderas no se perdieron. El chequeo se hace contra el **parser real**
(introspección de ``argparse``): si alguien renombra un comando o borra un flag, la
comparación lo detecta; una lista escrita a mano en el JSON no lo haría.

**(b) Los artefactos.** Cada corrida produce archivos con un nombre y una forma.
Los que **coinciden** con v1 se verifican como coincidentes; los que **cambian** se
declaran con su motivo (nunca se declaran "iguales" ni se omiten).

Por qué no se compara el veredicto
----------------------------------
v1 no separa lectura de decisión (ADR-001/ADR-006): comparar las salidas documento
a documento mezclaría una **mejora de diseño** con una **regresión**. La exactitud
de la letra contra v1 ya se midió en F3/T-305 (v2 5/5 vs. v1 2/5) y la paridad de
la extracción en F4/T-405 (reglas 20/20, campos 29/29, sostén 32/32); acá se
apunta a `fuera_de_paridad`, que es lo que hace honesto el mapa.

Uso::

    python scripts/F6/paridad_cli.py                 # mapa de paridad (sin red)
    python scripts/F6/paridad_cli.py --detalle       # + el detalle por comando
    python scripts/F6/paridad_cli.py --json out.json

Las funciones de este módulo son **puras** (no corren ``main`` al importarse): el
test ``tests/test_paridad_t604.py`` las carga por ``importlib`` y las ejercita. El
nivel determinista **no** necesita Ollama, Docling ni red.
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

# ---------------------------------------------------------------------------
# Rutas
# ---------------------------------------------------------------------------

#: Raíz del repo (``v2/scripts/F6/paridad_cli.py`` → ``parents[3]``).
RAIZ_REPO = Path(__file__).resolve().parents[3]

#: Raíz de v2.
RAIZ_V2 = RAIZ_REPO / "v2"

#: v1 tal como está en el repo.
V1_DIR = RAIZ_REPO / "v1"

#: Manifiesto del subconjunto de paridad de F6.
SUBCONJUNTO = RAIZ_V2 / "tests" / "golden" / "F6" / "subconjunto.json"

#: Muestra acordada: la copia estable del golden versionada en el repo.
MUESTRA = RAIZ_V2 / "tests" / "fixtures" / "golden"


def cargar_subconjunto(ruta: Path | None = None) -> dict[str, Any]:
    """Lee el manifiesto del subconjunto de paridad de F6."""
    destino = ruta or SUBCONJUNTO
    with destino.open(encoding="utf-8") as archivo:
        datos = json.load(archivo)
    if not isinstance(datos, dict):
        raise ValueError(f"El subconjunto {destino} debe ser un objeto JSON.")
    return datos


def parser_v2() -> Any:
    """El parser **real** del CLI de v2 (la fuente de verdad de la superficie).

    Se importa acá y no a nivel de módulo para que cargar este script por
    ``importlib`` no arrastre el paquete completo antes de que exista el ``sys.path``
    correcto (mismo criterio que los otros scripts de paridad).
    """
    from voucherflow.cli.main import construir_parser

    return construir_parser()


def subcomandos_v2() -> dict[str, Any]:
    """``nombre -> subparser`` de cada subcomando del CLI de v2."""
    parser = parser_v2()
    for accion in parser._subparsers._group_actions:  # noqa: SLF001 - introspección documentada
        return dict(accion.choices)
    return {}


def flags_de_subcomando(nombre: str, *, subcomandos: dict[str, Any] | None = None) -> set[str]:
    """Banderas largas que acepta un subcomando de v2 (introspección real).

    Devuelve los ``--flag`` del subparser **más** las **posiciones** que declara
    (``-``/``-o`` de v1 se corresponden con posiciones o con ``--output`` según el
    caso), porque de eso se trata la comparación: de que el operador pueda hacer lo
    mismo, no de que la bandera se llame igual.
    """
    mapa = subcomandos if subcomandos is not None else subcomandos_v2()
    sub = mapa.get(nombre)
    if sub is None:
        return set()
    largas = {
        bandera for accion in sub._actions for bandera in accion.option_strings  # noqa: SLF001
        if bandera.startswith("--")
    }
    posiciones = {
        accion.dest for accion in sub._actions if not accion.option_strings  # noqa: SLF001
    }
    return largas | posiciones


def _flags_internos() -> set[str]:
    """Banderas que ``argparse`` agrega solo (no cuentan como superficie propia)."""
    return {"--help"}


def normalizar_flag(flag: str) -> str:
    """Normaliza una bandera de v1 a la forma que se busca en v2.

    ``-o``/``-m`` de v1 son atajos cortos; en v2 la superficie se declara con
    banderas largas. El mapa del subconjunto ya declara, por comando, cuáles son
    equivalentes, así que acá solo se tolera el guion bajo vs. guion y el guion
    inicial (``--output_dir`` → ``output-dir``).
    """
    limpio = flag.lstrip("-").replace("_", "-")
    return limpio


def verificar_comando(comando: dict[str, Any], *, subcomandos: dict[str, Any] | None = None) -> dict[str, Any]:
    """Verifica el equivalente de v2 de **un** comando de v1 (T-604).

    Devuelve un resultado con lo que se pudo verificar y lo que no:

    - ``existe``: el (los) subcomando(s) de v2 declarados existen en el CLI.
    - ``flags_faltantes``: para cada bandera de v1 con equivalente declarado en
      ``mapa_flags_v1_v2``, la bandera de v2 a la que corresponde **no** está en la
      superficie del subcomando. Eso es una brecha real: el operador no puede hacer
      con v2 lo que hacía con v1.
    - ``sin_equivalente_sin_motivo``: banderas declaradas sin equivalente pero sin
      explicación. Declararlas sin motivo convertiría "no implementado" en "no
      comparable" por omisión.

    Por qué el mapa y no "el flag se llama igual": ``-o`` de v1 es ``--output`` en
    v2 y ``--input`` de v1 es la **posición** ``origen``. Comparar nombres de
    bandera reportaría diferencias que no son brechas y taparía las que sí lo son;
    el mapa declara la correspondencia y el test exige que exista.

    El comando ``v2`` del subconjunto puede nombrar **varios** comandos separados
    por ``|`` (p. ej. ``run | batch``): la equivalencia puede repartirse.
    """
    mapa = subcomandos if subcomandos is not None else subcomandos_v2()
    nombres_v2 = [parte.strip() for parte in str(comando.get("v2", "")).split("|")]
    nombres_v2 = [n.split()[0] for n in nombres_v2 if n]

    inexistentes = [nombre for nombre in nombres_v2 if nombre not in mapa]
    existe = not inexistentes

    superficie: set[str] = set()
    posiciones: set[str] = set()
    for nombre in nombres_v2:
        superficie |= {normalizar_flag(f) for f in flags_de_subcomando(nombre, subcomandos=mapa)}
        sub = mapa.get(nombre)
        if sub is not None:
            posiciones |= {
                accion.dest for accion in sub._actions if not accion.option_strings  # noqa: SLF001
            }

    mapeo: dict[str, str] = comando.get("mapa_flags_v1_v2", {})
    faltan_en_v2: list[str] = []
    verificado: dict[str, str] = {}
    for flag_v1, equivalente in mapeo.items():
        destino = normalizar_flag(equivalente)
        if equivalente in posiciones or destino in superficie:
            verificado[flag_v1] = equivalente
        else:
            faltan_en_v2.append(f"{flag_v1}→{equivalente}")

    sin_equivalente = sorted(str(f) for f in comando.get("flags_v1_sin_equivalente", []))
    # Declarar banderas sin equivalente exige un motivo: si no, el mapa se puede
    # "completar" borrando la bandera problemática de las dos listas.
    sin_motivo = sin_equivalente if not str(comando.get("nota", "")).strip() else []

    # Toda bandera de v1 tiene que estar en una de las dos listas: si no, el
    # subconjunto está incompleto y la paridad no se está midiendo de verdad.
    declaradas = set(mapeo) | set(sin_equivalente)
    no_declaradas = sorted(set(comando.get("flags_v1", [])) - declaradas)

    return {
        "id": comando.get("id"),
        "v1": comando.get("v1"),
        "v2": comando.get("v2"),
        "que": comando.get("que"),
        "existe": existe,
        "nombres_v2_inexistentes": inexistentes,
        "flags_verificadas": verificado,
        "flags_faltantes_en_v2": faltan_en_v2,
        "flags_sin_equivalente": sin_equivalente,
        "flags_no_declaradas": no_declaradas,
        "sin_equivalente_sin_motivo": sin_motivo,
        "ok": existe and not faltan_en_v2 and not sin_motivo and not no_declaradas,
    }


def verificar_artefacto(artefacto: dict[str, Any]) -> dict[str, Any]:
    """Verifica la coherencia de **un** artefacto declarado (T-604).

    Reglas:

    - Si ``coincide`` es ``True``, los patrones de v1 y v2 tienen que ser iguales
      (declarar coincidencia donde no la hay sería el peor error del mapa).
    - Si ``coincide`` es ``False``, tiene que haber ``nota`` con el motivo: una
      diferencia sin explicación es una brecha disfrazada.
    - Un artefacto sin contraparte en v1 (``patron_v1`` igual a ``"(no existia)"``)
      solo es válido si se declara ``mejor_que_v1``.
    """
    coincide = bool(artefacto.get("coincide"))
    patron_v1 = str(artefacto.get("patron_v1", ""))
    patron_v2 = str(artefacto.get("patron_v2", ""))
    sin_contraparte = patron_v1.startswith("(")

    problemas: list[str] = []
    if coincide and patron_v1 != patron_v2:
        problemas.append(
            f"declara coincidencia pero los patrones difieren: {patron_v1!r} != {patron_v2!r}"
        )
    if not coincide and not str(artefacto.get("nota", "")).strip():
        problemas.append("declara diferencia sin motivo")
    if sin_contraparte and not artefacto.get("mejor_que_v1"):
        problemas.append(
            "no tiene contraparte en v1 pero no se declara como mejora "
            "(`mejor_que_v1`)"
        )

    return {
        "id": artefacto.get("id"),
        "v1": artefacto.get("v1"),
        "v2": artefacto.get("v2"),
        "coincide": coincide,
        "problemas": problemas,
        "ok": not problemas,
    }


def verificar_referencias(subconjunto: dict[str, Any], *, raiz: Path | None = None) -> dict[str, Any]:
    """Verifica que los scripts de v1 citados por el subconjunto existan.

    Un mapa de paridad que cita archivos inexistentes declara procedencias falsas:
    si alguien mueve o borra un script de v1, el mapa tiene que dejar de estar en
    verde (misma idea que el chequeo del `texto_v1` en la paridad de F4).
    """
    base = raiz or RAIZ_REPO
    faltantes: list[str] = []
    for comando in subconjunto.get("comandos", []):
        referencia = str(comando.get("v1", "")).strip()
        if not referencia:
            faltantes.append(f"(comando {comando.get('id')} sin archivo de v1)")
            continue
        if not (base / referencia).exists():
            faltantes.append(referencia)
    return {"faltantes": faltantes, "ok": not faltantes}


def mapa_de_paridad(subconjunto: dict[str, Any] | None = None) -> dict[str, Any]:
    """El mapa completo de paridad de F6 (la medición determinista de T-604).

    Corre los tres niveles y devuelve un reporte con el conteo por estado, más las
    listas de lo que quedó fuera de paridad y de las equivalencias de capacidad.
    **No** necesita modelos ni red.
    """
    datos = subconjunto or cargar_subconjunto()
    mapa_sub = subcomandos_v2()

    comandos = [verificar_comando(c, subcomandos=mapa_sub) for c in datos.get("comandos", [])]
    artefactos = [verificar_artefacto(a) for a in datos.get("artefactos", [])]
    referencias = verificar_referencias(datos)

    con_equivalente = sum(1 for c in comandos if c["existe"])
    con_flags_completas = sum(1 for c in comandos if c["ok"])
    artefactos_ok = sum(1 for a in artefactos if a["ok"])
    coincidentes = sum(1 for a in artefactos if a["coincide"])

    return {
        "version": datos.get("golden_version"),
        "muestra": datos.get("muestra", {}),
        "comandos": comandos,
        "artefactos": artefactos,
        "referencias_v1": referencias,
        "equivalencias_de_capacidad": datos.get("equivalencias_de_capacidad", []),
        "fuera_de_paridad": datos.get("fuera_de_paridad", []),
        "resumen": {
            "comandos_declarados": len(comandos),
            "comandos_con_equivalente": con_equivalente,
            "comandos_con_banderas_completas": con_flags_completas,
            "artefactos_declarados": len(artefactos),
            "artefactos_ok": artefactos_ok,
            "artefactos_coincidentes_con_v1": coincidentes,
            "artefactos_que_cambian_con_motivo": len(artefactos) - coincidentes,
            "capacidades_mejor_o_distintas": len(datos.get("equivalencias_de_capacidad", [])),
            "fuera_de_paridad_declarados": len(datos.get("fuera_de_paridad", [])),
        },
        "ok": (
            con_equivalente == len(comandos)
            and con_flags_completas == len(comandos)
            and artefactos_ok == len(artefactos)
            and referencias["ok"]
        ),
    }


def cargar_modulo(ruta: Path) -> Any:
    """Carga un módulo por ruta sin ejecutar su ``main`` (para los tests).

    Es la misma técnica que usan los tests de F3/T-305 y F4/T-405 para reutilizar
    las funciones puras de un script sin correrlo como programa.
    """
    especificacion = importlib.util.spec_from_file_location(ruta.stem, ruta)
    if especificacion is None or especificacion.loader is None:  # pragma: no cover
        raise ImportError(f"No se pudo cargar el módulo {ruta}")
    modulo = importlib.util.module_from_spec(especificacion)
    especificacion.loader.exec_module(modulo)
    return modulo


def main() -> None:  # pragma: no cover - la verificación vive en los tests
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--detalle", action="store_true", help="Detalle por comando y artefacto.")
    parser.add_argument("--json", type=Path, default=None, help="Guardar el mapa en JSON.")
    args = parser.parse_args()

    mapa = mapa_de_paridad()
    resumen = mapa["resumen"]

    print(f"Paridad F6 ({mapa['version']}) — muestra: {mapa['muestra'].get('carpeta')}")
    print(f"  comandos con equivalente: {resumen['comandos_con_equivalente']}/{resumen['comandos_declarados']}")
    print(f"  comandos con banderas completas: {resumen['comandos_con_banderas_completas']}/{resumen['comandos_declarados']}")
    print(f"  artefactos verificados: {resumen['artefactos_ok']}/{resumen['artefactos_declarados']}")
    print(f"  artefactos que coinciden con v1: {resumen['artefactos_coincidentes_con_v1']}")
    print(f"  fuera de paridad declarados: {resumen['fuera_de_paridad_declarados']}")

    if args.detalle:
        print("\nComandos")
        for comando in mapa["comandos"]:
            marca = "OK " if comando["ok"] else "!! "
            print(f"  {marca}{comando['v1']:<34} -> {comando['v2']}")
            if comando["flags_faltantes_en_v2"]:
                print(f"      faltan en v2: {comando['flags_faltantes_en_v2']}")
        print("\nArtefactos")
        for artefacto in mapa["artefactos"]:
            estado = "coincide" if artefacto["coincide"] else "cambia"
            print(f"  {artefacto['id']:<28} {estado:<9} {artefacto['v2']}")
            for problema in artefacto["problemas"]:
                print(f"      !! {problema}")

    if args.json:
        args.json.write_text(json.dumps(mapa, ensure_ascii=False, indent=2), encoding="utf-8")
        print(f"\nMapa guardado en {args.json}")

    sys.exit(0 if mapa["ok"] else 1)


if __name__ == "__main__":  # pragma: no cover
    main()

#!/usr/bin/env python
"""Inspecciona T-604 (F6) — mapa de paridad v1→v2 y corte de v1.

**Fase**: F6 (cliente) · **Tarea**: T-604 · **Épica**: E-CLI.

Es el **reporte de cierre del DoD de F6**: "los comandos de v1 tienen equivalente
en v2 con resultados comparables (o mejores) sobre una muestra acordada de
`files/`; aquí se declara el corte de v1".

Qué muestra
-----------

1. **La superficie**: cada script de v1 con CLI y su equivalente en v2, con el
   **mapeo de banderas** verificado contra el parser real del CLI (no contra una
   lista escrita a mano).
2. **Los artefactos**: los nombres de archivo que **coinciden** con v1 y los que
   **cambian**, cada uno con su motivo. Se distingue "coincide" de "mejor" de
   "cambia por diseño": las tres cosas existen y confundirlas sería el error.
3. **Las capacidades**: dónde v2 mejora o difiere **por diseño** (nunca declaradas
   como paridad donde no la hay).
4. **Lo que queda fuera de paridad**, con el motivo y **dónde se mide** (la
   comparación del veredicto está derivada a F3/T-305 y F4/T-405, donde ya se
   midió).
5. **El corte de v1**: qué significa congelar v1 como referencia.

Uso:
    python scripts/F6/t604.py                     # el mapa completo (determinista)
    python scripts/F6/t604.py --detalle           # + el mapeo de banderas por comando
    python scripts/F6/t604.py --json /tmp/t604.json
    python scripts/F6/t604.py --real [--dir files/]   # corrida REAL (necesita Ollama)

El modo default corre **sin** Ollama, **sin** Docling y **sin** red: mide la
superficie y los artefactos, que es lo que la suite default verifica
(``tests/test_paridad_t604.py``). El modo ``--real`` es **informativo**: requiere
Ollama con los modelos de cada rol y no forma parte del criterio (una corrida que
depende de qué modelo está instalado no puede decidir si la paridad se alcanzó).
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

_SCRIPT_PARIDAD = Path(__file__).resolve().parent / "paridad_cli.py"


def cargar_paridad() -> Any:
    """Carga ``paridad_cli.py`` por ruta (sin ejecutar su ``main``)."""
    especificacion = importlib.util.spec_from_file_location("paridad_cli", _SCRIPT_PARIDAD)
    if especificacion is None or especificacion.loader is None:  # pragma: no cover
        raise ImportError(f"No se pudo cargar {_SCRIPT_PARIDAD}")
    modulo = importlib.util.module_from_spec(especificacion)
    especificacion.loader.exec_module(modulo)
    return modulo


def _imprimir_seccion(titulo: str) -> None:
    print(f"\n{titulo}")
    print("-" * len(titulo))


def _marca(ok: bool) -> str:
    return "✅" if ok else "❌"


def correr_real(carpeta: Path, *, modelo: str | None) -> dict[str, Any]:
    """Corrida **real** de los comandos equivalentes (informativa).

    Procesa hasta 3 documentos de la carpeta con ``process`` (v2) y compara, para
    cada uno, que el markdown de salida **exista**: es la verificación de que el
    camino real del comando llega a escribir el artefacto. No compara contenido
    contra v1 (esa comparación no es válida entre arquitecturas, ver el README del
    subconjunto): verifica que el comando **funcione** de punta a punta.

    Requiere Ollama si el documento necesita OCR/VLM. Los documentos de texto nativo
    (``.md``) no lo necesitan.
    """
    from voucherflow.cli.main import EntornoCLI, main
    import io

    documentos = sorted(p for p in carpeta.rglob("*") if p.suffix.lower() in {".md", ".txt"})
    if not documentos:
        return {
            "ok": False,
            "motivo": (
                f"No hay documentos de texto en {carpeta}. La corrida real sobre PDFs e "
                "imágenes necesita Ollama con los modelos de cada rol; usá una carpeta "
                "con .md o corré la suite de integración."
            ),
            "resultados": [],
        }

    resultados: list[dict[str, Any]] = []
    # Se guarda el contenido original para verificar que la corrida no pise la
    # entrada: es el riesgo que se detectó al escribir esta medición (v1 no corría
    # `process` sobre texto plano; v2 sí).
    originales = {p: p.read_text(encoding="utf-8") for p in documentos[:3]}
    for documento in documentos[:3]:
        destino = documento.parent / "v2-salida"
        entorno = EntornoCLI(stdout=io.StringIO(), stderr=io.StringIO(), cwd=documento.parent)
        codigo = main(
            ["process", str(documento), "-o", str(destino)], entorno=entorno
        )
        esperado = destino / f"{documento.stem}.md"
        resultados.append(
            {
                "archivo": str(documento),
                "exit": codigo,
                "salida_esperada": str(esperado),
                "salida_existe": esperado.exists(),
                "entrada_intacta": documento.read_text(encoding="utf-8")
                == originales[documento],
            }
        )
    return {
        "ok": all(r["exit"] == 0 and r["salida_existe"] for r in resultados),
        "modelo": modelo,
        "documentos": len(resultados),
        "resultados": resultados,
        "nota": (
            "Corrida informativa: verifica que los comandos FUNCIONEN de punta a punta. "
            "No compara el veredicto contra v1 (no es comparable entre arquitecturas: "
            "ADR-001/ADR-006)."
        ),
    }


def main() -> None:  # pragma: no cover - la verificación vive en los tests
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--detalle", action="store_true", help="Mapeo de banderas por comando.")
    parser.add_argument("--json", type=Path, default=None, help="Guardar el reporte.")
    parser.add_argument(
        "--real",
        action="store_true",
        help="Corre los comandos de verdad sobre una carpeta (necesita Ollama si hay imágenes).",
    )
    parser.add_argument(
        "--dir",
        type=Path,
        default=None,
        help="Carpeta de la corrida real (default: la muestra del subconjunto).",
    )
    parser.add_argument("--model", default=None, help="Modelo para la corrida real.")
    args = parser.parse_args()

    paridad = cargar_paridad()
    subconjunto = paridad.cargar_subconjunto()
    mapa = paridad.mapa_de_paridad(subconjunto)
    resumen = mapa["resumen"]

    print(f"Paridad v1→v2 — F6/T-604 (subconjunto {mapa['version']})")
    print(f"Muestra: {mapa['muestra'].get('carpeta')}")
    print(f"  {mapa['muestra'].get('motivo', '')}")

    _imprimir_seccion("Superficie de invocación")
    print(
        f"  {_marca(resumen['comandos_con_equivalente'] == resumen['comandos_declarados'])} "
        f"comandos con equivalente en v2: {resumen['comandos_con_equivalente']}/"
        f"{resumen['comandos_declarados']}"
    )
    print(
        f"  {_marca(resumen['comandos_con_banderas_completas'] == resumen['comandos_declarados'])} "
        f"comandos con banderas completas: {resumen['comandos_con_banderas_completas']}/"
        f"{resumen['comandos_declarados']}"
    )
    for comando in mapa["comandos"]:
        print(f"    {_marca(comando['ok'])} {comando['v1']:<34} → {comando['v2']}")
        if args.detalle:
            for v1, v2 in comando["flags_verificadas"].items():
                print(f"        {v1:<24} → {v2}")
            if comando["flags_sin_equivalente"]:
                print(f"        sin equivalente: {', '.join(comando['flags_sin_equivalente'])}")

    _imprimir_seccion("Artefactos")
    print(
        f"  {_marca(resumen['artefactos_ok'] == resumen['artefactos_declarados'])} "
        f"artefactos verificados: {resumen['artefactos_ok']}/{resumen['artefactos_declarados']}"
    )
    print(f"    coinciden con v1: {resumen['artefactos_coincidentes_con_v1']}")
    print(f"    cambian (con motivo): {resumen['artefactos_que_cambian_con_motivo']}")
    for artefacto in mapa["artefactos"]:
        estado = "coincide" if artefacto["coincide"] else "cambia"
        print(f"    {_marca(artefacto['ok'])} {artefacto['id']:<28} {estado}")
        if args.detalle and not artefacto["coincide"]:
            nota = next(
                (a.get("nota", "") for a in subconjunto["artefactos"] if a["id"] == artefacto["id"]),
                "",
            )
            print(f"        {nota[:160]}")

    _imprimir_seccion("Capacidades: dónde v2 mejora o difiere por diseño")
    for capacidad in mapa["equivalencias_de_capacidad"]:
        print(f"  • {capacidad['capacidad']} [{capacidad['estado']}]")
        if args.detalle:
            print(f"      v1: {capacidad['v1']}")
            print(f"      v2: {capacidad['v2']}")
            print(f"      {capacidad['nota'][:160]}")

    _imprimir_seccion("Fuera de paridad (declarado, con dónde se mide)")
    for item in mapa["fuera_de_paridad"]:
        print(f"  • {item['que']}")
        if args.detalle:
            print(f"      motivo: {item['motivo'][:160]}")
            print(f"      dónde:  {item['donde_se_mide'][:160]}")

    corte = subconjunto.get("corte_de_v1", {})
    _imprimir_seccion("Corte de v1")
    print(f"  estado: {corte.get('estado', '(no declarado)')} — {corte.get('fecha', '')}")
    if args.detalle:
        print(f"  {corte.get('decision', '')}")
        for implicancia in corte.get("que_implica", []):
            print(f"    - {implicancia}")
    print(f"  pendiente: {corte.get('pendiente', '(nada)')}")

    resultado_real: dict[str, Any] | None = None
    if args.real:
        _imprimir_seccion("Corrida real (informativa)")
        carpeta = args.dir or (paridad.RAIZ_REPO / mapa["muestra"]["carpeta"])
        resultado_real = correr_real(carpeta, modelo=args.model)
        print(f"  {_marca(resultado_real['ok'])} {resultado_real.get('motivo', '')}")
        for item in resultado_real.get("resultados", []):
            print(f"    - exit={item['exit']} salida={item['salida_existe']} {item['archivo']}")

    _imprimir_seccion("Resultado")
    print(f"  {'✅' if mapa['ok'] else '❌'} mapa de paridad: {'en verde' if mapa['ok'] else 'con brechas'}")
    print(f"  corte de v1: {corte.get('estado', 'no declarado')}")

    reporte = {
        "version": mapa["version"],
        "resumen": resumen,
        "mapa": mapa,
        "corte_de_v1": corte,
        "corrida_real": resultado_real,
    }
    if args.json:
        args.json.write_text(json.dumps(reporte, ensure_ascii=False, indent=2), encoding="utf-8")
        print(f"\nReporte guardado en {args.json}")

    sys.exit(0 if mapa["ok"] else 1)


if __name__ == "__main__":  # pragma: no cover
    main()

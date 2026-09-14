#!/usr/bin/env python
"""Nivel B — mide el **acuerdo** entre la referencia y el pipeline local.

**Plan**: [`docs/plan/07-extracciones-esperadas.md`](../../docs/plan/07-extracciones-esperadas.md) §7.

Qué hace
--------

Corre el pipeline **de verdad** (`api.extract`: Docling + gate + los dos flujos
VLM/LLM + combinación) sobre las imágenes de `tests/expected-extraction/`, y
compara el resultado contra la lectura de referencia, campo por campo.

⚠️ **Mide ACUERDO, no exactitud.** La referencia es la lectura de otro modelo, con
errores medidos (5 de 28 CUIT con el dígito verificador inválido). Un `difiere` en
`cuit_emisor` puede ser un **acierto del pipeline**. Ver el README del artefacto.

⛔ **Este script NO publica un "% de acuerdo" único.** Con la referencia sucia y 23
de 30 documentos siendo facturas A, ese número sería a la vez **optimista**
(comparte errores con cualquier modelo que lea parecido) y **opaco** (no dice qué
campo falla). Publica el recuento por estado y **lista los desacuerdos**.

Requisitos
----------

- **Ollama** corriendo, con los modelos de `Settings.modelos` (`vlm` y `llm`).
  ⚠️ El default del repo para `llm` es `qwen2.5:7b` y **puede no estar instalado**:
  el script lo dice con un mensaje claro en vez de fallar con un traceback.
- **Docling** (ya es dependencia de la librería).

Como es la única herramienta del artefacto que necesita servicios reales, **no
corre en la suite default**: se invoca a mano.

Uso:
    python scripts/verificacion/acuerdo-extraccion.py --listar
    python scripts/verificacion/acuerdo-extraccion.py --limite 3
    python scripts/verificacion/acuerdo-extraccion.py                    # todo
    python scripts/verificacion/acuerdo-extraccion.py --json /tmp/acuerdo.json

Sale con código 0 si se pudo medir, 2 si faltan los servicios o el artefacto.
Un **desacuerdo no es un fallo del script**: es el hallazgo que se va a leer.
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

# El paquete usa layout src/; si se corre sin instalar, se agrega src/ al path.
_SRC = Path(__file__).resolve().parents[2] / "src"
if str(_SRC) not in sys.path:
    sys.path.insert(0, str(_SRC))

from voucherflow.llm import comparacion as cmp  # noqa: E402

RAIZ = Path(__file__).resolve().parents[2]
ARTEFACTO = RAIZ / "tests" / "expected-extraction"
FIXTURES = RAIZ / "tests" / "fixtures"


def _json(ruta: Path) -> Any:
    return json.loads(ruta.read_text(encoding="utf-8"))


def _preflight() -> str | None:
    """Devuelve un motivo de aborto, o ``None`` si se puede correr.

    Se chequearlo **antes** de procesar: si falta un modelo, es mejor saberlo en
    un segundo que después de Docling sobre 30 documentos.
    """
    if not (ARTEFACTO / "manifiesto.json").is_file():
        return (
            "falta el artefacto. Generálo con "
            "`python scripts/operacion/generar-extracciones-esperadas.py`"
        )

    try:
        import urllib.error
        import urllib.request

        from voucherflow.settings.config import cargar_settings

        ajustes = cargar_settings()
        url = ajustes.url_ollama.rstrip("/") + "/api/tags"
        try:
            with urllib.request.urlopen(url, timeout=3) as respuesta:
                instalados = {
                    m["name"] for m in json.loads(respuesta.read()).get("models", [])
                }
        except (urllib.error.URLError, OSError, ValueError) as exc:
            return (
                f"Ollama no responde en {ajustes.url_ollama} ({exc}). "
                "Arrancalo con `ollama serve`."
            )

        # ⚠️ Los modelos se comparan tolerando el tag ausente (`qwen2.5` vs
        # `qwen2.5:latest`): exigir el nombre exacto daría un falso "falta".
        def falta(modelo: str | None) -> bool:
            if not modelo:
                return False
            return not any(
                instalado == modelo or instalado.split(":")[0] == modelo.split(":")[0]
                for instalado in instalados
            )

        faltantes = [
            modelo
            for modelo in (ajustes.modelo_vlm, ajustes.modelo_llm)
            if falta(modelo)
        ]
        if faltantes:
            return (
                f"faltan modelos en Ollama: {faltantes}. "
                f"Instalados: {sorted(instalados)}. "
                "⚠️ El rol `llm` del repo es `qwen2.5:7b`; si no está, bajalo con "
                "`ollama pull qwen2.5:7b` o corré con `-m` para usar el mismo VLM "
                "en las dos fuentes (y declaralo en el reporte)."
            )
    except ImportError as exc:  # pragma: no cover - depende del entorno
        return f"no se pudo preparar el entorno: {exc}"

    return None


def _medir(entrada: dict, mapa: dict, max_workers: int) -> dict[str, Any]:
    """Corre el pipeline sobre una imagen y la compara contra la referencia.

    Devuelve un dict con el resultado de la comparación **o** con el motivo por el
    que no se pudo. Nunca lanza: un documento que falla no puede abortar el lote
    (si no, un solo archivo ilegible dejaría el reporte sin hacer).
    """
    from voucherflow.api import DocumentoNoProcesableError, extract
    from voucherflow.schemas.evidence import derivar_evidencia

    imagen = entrada.get("imagen_en_fixtures")
    if not imagen:
        return {"documento_id": entrada["documento_id"], "motivo": "sin_imagen"}

    ruta = FIXTURES / imagen
    if not ruta.is_file():
        return {
            "documento_id": entrada["documento_id"],
            "motivo": f"imagen declarada pero no está: {imagen}",
        }

    lectura = _json(ARTEFACTO / entrada["ruta"])[manifiesto_clave()]
    try:
        evidencia = extract(str(ruta))
    except DocumentoNoProcesableError as exc:
        return {"documento_id": entrada["documento_id"], "motivo": f"no procesable: {exc}"}
    except Exception as exc:  # noqa: BLE001 - un fallo no puede abortar el lote
        return {
            "documento_id": entrada["documento_id"],
            "motivo": f"{type(exc).__name__}: {exc}",
        }

    # `extract` de la fachada no adjunta la comparación de la referencia: se anota
    # en la trazabilidad para que quede registrado de dónde salió el "esperado".
    #
    # ⚠️ Se leen los contadores de la propia combinación del pipeline
    # (`lecturas_descartadas_por_invalidas`) porque explican la mayoría de los
    # `ausente` del reporte: si el pipeline descartó 6 de sus lecturas y solo
    # resolvió 4 campos, los `ausente` no son "no comparables" sino **cobertura**.
    combinacion = (evidencia.trazabilidad or {}).get("combinacion") or {}
    comparado = cmp.comparar(
        entrada["documento_id"],
        lectura,
        derivar_evidencia(
            evidencia,
            api_acuerdo={
                "referencia": {
                    "modelo": entrada.get("modelo"),
                    "corrida": entrada.get("corrida"),
                    "prompt_hash": entrada.get("prompt_hash"),
                },
                "pipeline": {
                    "campos_totales": combinacion.get("campos_totales"),
                    "campos_resueltos": combinacion.get("campos_resueltos"),
                    "lecturas_descartadas_por_invalidas": combinacion.get(
                        "lecturas_descartadas_por_invalidas"
                    ),
                    "desacuerdos_entre_fuentes": combinacion.get("desacuerdos"),
                },
                "nota": (
                    "Comparación contra la lectura de referencia del artefacto "
                    "tests/expected-extraction. Mide ACUERDO, no exactitud."
                ),
            },
        ),
        mapa,
    )
    return {
        "documento_id": entrada["documento_id"],
        "cobertura_pipeline": {
            "campos_totales": combinacion.get("campos_totales"),
            "campos_resueltos": combinacion.get("campos_resueltos"),
            "lecturas_descartadas_por_invalidas": combinacion.get(
                "lecturas_descartadas_por_invalidas"
            ),
            "desacuerdos_entre_fuentes": combinacion.get("desacuerdos") or [],
        },
        **comparado.como_dict(),
    }


def manifiesto_clave() -> str:
    """Clave del bloque de extracción del registro (la fija el generador)."""
    return _json(ARTEFACTO / "manifiesto.json").get("clave_extraccion", "extraccion")


# --------------------------------------------------------------------------- #
# Reporte
# --------------------------------------------------------------------------- #


def _imprimir(medidos: list[dict], fallidos: list[dict], manifiesto: dict) -> dict:
    entradas = manifiesto["entradas"]
    print("=== Acuerdo con la referencia (nivel B) ===")
    print(f"artefacto          : tests/expected-extraction (v{manifiesto['version']})")
    print(f"referencia         : {manifiesto['cobertura']['por_modelo']}")
    print(f"corridas medidas   : {len(medidos)} de {len(entradas)}")
    if fallidos:
        print(f"no medibles        : {len(fallidos)}")

    totales: dict[str, int] = {}
    comparables = coincidencias = 0
    for m in medidos:
        for estado, n in m["recuento"].items():
            totales[estado] = totales.get(estado, 0) + n
        comparables += m["comparables"]
        coincidencias += m["coincidencias"]

    print(f"campos comparables : {comparables}")
    print("\n--- por estado ---")
    for estado in (*cmp.ESTADOS_QUE_PUNTUAN, cmp.AUSENTE, cmp.NO_COMPARABLE):
        n = totales.get(estado, 0)
        print(f"  {estado:22} {n:4}")

    # ⚠️ Los `ausente` tienen dos causas muy distintas y hay que poder separarlas.
    # `ausente` porque el pipeline lee poco = **cobertura** del pipeline; `ausente`
    # porque la referencia no lo trae = límite de la referencia. Sin este bloque,
    # un `ausente` masivo se lee como "los dos están de acuerdo en que no está",
    # que es lo contrario de lo que pasa.
    coberturas = [
        m["cobertura_pipeline"]
        for m in medidos
        if m.get("cobertura_pipeline", {}).get("campos_totales") is not None
    ]
    if coberturas:
        print("\n--- cobertura del pipeline (por qué hay `ausente`) ---")
        print(f"  documentos medidos : {len(coberturas)}")
        print(
            f"  campos resueltos   : "
            f"{sum(c['campos_resueltos'] or 0 for c in coberturas)} de "
            f"{sum(c['campos_totales'] or 0 for c in coberturas)} (16 del contrato)"
        )
        descartadas = sum(c["lecturas_descartadas_por_invalidas"] or 0 for c in coberturas)
        print(f"  lecturas descartadas por inválidas : {descartadas}")
        con_desacuerdo = [c for c in coberturas if c["desacuerdos_entre_fuentes"]]
        if con_desacuerdo:
            print(
                f"  ⚠ documentos donde las DOS fuentes del pipeline no coinciden: "
                f"{len(con_desacuerdo)}"
            )
            print(
                "     (el valor publicado es el de la fuente que ganó por "
                "precedencia, marcado como no confiable)"
            )

    # ⚠️ Deliberadamente NO hay "% de acuerdo": ver el encabezado del módulo.
    print("\n  ⚠ sin porcentaje único: la referencia tiene errores medidos")
    print("     (5 de 28 CUIT con el DV inválido). Ver el README del artefacto.")

    # ------------------------------------------------ desacuerdos (lo importante)
    desacuerdos: dict[str, list[tuple[str, Any, Any, str | None]]] = {}
    for m in medidos:
        for campo in m["campos"]:
            if campo["estado"] == cmp.DIFIERE:
                desacuerdos.setdefault(campo["campo"], []).append(
                    (
                        m["documento_id"],
                        campo["valor_referencia"],
                        campo["valor_pipeline"],
                        campo["nota"],
                    )
                )
    if desacuerdos:
        print(f"\n--- desacuerdos (el hallazgo) : {len(desacuerdos)} campo(s) ---")
        for campo, casos in sorted(desacuerdos.items(), key=lambda kv: -len(kv[1])):
            print(f"\n  {campo}  ({len(casos)} documento/s)")
            for doc, ref, pipe, nota in casos[:5]:
                print(f"    {doc}")
                print(f"      referencia : {str(ref)[:70]}")
                print(f"      pipeline   : {str(pipe)[:70]}")
                if nota:
                    print(f"      ⚠ {nota}")
    else:
        print("\n--- sin desacuerdos ---")

    if fallidos:
        print(f"\n--- no medibles ({len(fallidos)}) ---")
        for f in fallidos:
            print(f"  {f['documento_id']}: {f['motivo']}")

    return {
        "generado_utc": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "medidos": len(medidos),
        "no_medibles": len(fallidos),
        "totales_por_estado": totales,
        "campos_comparables": comparables,
        "coincidencias": coincidencias,
        "cobertura_pipeline": [
            m["cobertura_pipeline"] for m in medidos if "cobertura_pipeline" in m
        ],
        "desacuerdos": {
            campo: [
                {"documento_id": d, "referencia": str(r), "pipeline": str(p), "nota": n}
                for d, r, p, n in casos
            ]
            for campo, casos in desacuerdos.items()
        },
        "no_medibles_detalle": fallidos,
        "detalle_por_documento": medidos,
        "advertencia": (
            "Mide ACUERDO, no exactitud. La referencia tiene 5 de 28 CUIT con el "
            "digito verificador invalido: un `difiere` en cuit_emisor puede ser un "
            "acierto del pipeline. Ver tests/expected-extraction/README.md."
        ),
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="acuerdo-extraccion.py", description=__doc__.splitlines()[0]
    )
    parser.add_argument("--limite", type=int, default=0, help="medir solo las primeras N (0 = todas)")
    parser.add_argument("--json", type=Path, help="escribir el reporte en JSON")
    parser.add_argument("--listar", action="store_true", help="listar las corridas y salir")
    parser.add_argument(
        "--workers", type=int, default=1,
        help="procesos concurrentes (default: 1: Docling sobre 30 imágenes no es liviano)",
    )
    args = parser.parse_args(argv)

    if not (ARTEFACTO / "manifiesto.json").is_file():
        print(
            "error: falta el artefacto tests/expected-extraction. Generálo con "
            "`python scripts/operacion/generar-extracciones-esperadas.py`.",
            file=sys.stderr,
        )
        return 2

    manifiesto = _json(ARTEFACTO / "manifiesto.json")
    mapa = _json(ARTEFACTO / "mapa_de_campos.json")
    entradas = manifiesto["entradas"]

    if args.listar:
        print(f"artefacto: tests/expected-extraction ({len(entradas)} corridas)")
        sin = 0
        for e in entradas:
            img = e.get("imagen_en_fixtures")
            sin += not img
            print(f"  {e['documento_id']}  {e['modelo']}  {e['corrida']}  {img or '⚠ SIN IMAGEN'}")
        print(f"\ncon imagen: {len(entradas) - sin} · sin imagen: {sin}")
        return 0

    motivo = _preflight()
    if motivo:
        print(f"error: {motivo}", file=sys.stderr)
        return 2

    if args.limite:
        entradas = entradas[: args.limite]

    # Se agrupa por documento para no procesar la MISMA imagen dos veces cuando hay
    # dos corridas (el pipeline es determinista en la lectura; comparar contra dos
    # referencias de la misma imagen no pide una segunda pasada).
    por_documento: dict[str, list[dict]] = {}
    for e in entradas:
        por_documento.setdefault(e["documento_id"], []).append(e)

    print(f"midiendo {len(por_documento)} documento(s) con el pipeline local…")
    medidos: list[dict] = []
    fallidos: list[dict] = []
    for i, (documento, corridas) in enumerate(sorted(por_documento.items()), 1):
        print(f"  [{i}/{len(por_documento)}] {documento}", flush=True)
        for entrada in corridas:
            resultado = _medir(entrada, mapa, args.workers)
            if "motivo" in resultado:
                fallidos.append(resultado)
            else:
                medidos.append(resultado)

    print()
    reporte = _imprimir(medidos, fallidos, manifiesto)

    if args.json:
        args.json.write_text(
            json.dumps(reporte, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
        )
        print(f"\nreporte            : {args.json}")

    return 0 if medidos else 2


if __name__ == "__main__":
    raise SystemExit(main())

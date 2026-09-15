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

⚠️ **Los `ausente` se separan por causa** (2026-09-14). «Los dos no lo leyeron»,
«solo lo leyó la referencia» y «solo lo leyó el pipeline» son tres hechos
distintos, y el número agregado de `ausente` los confunde: un `ausente` masivo se
leería como «los dos están de acuerdo en que no está», que es lo contrario de lo
que pasa. La causa se deriva de los **valores** (`valor_referencia`/`valor_pipeline`
en `None`), nunca buscando texto en la `nota` — retocar un mensaje cambiaría los
números en silencio.

Requisitos
----------

- **Ollama** corriendo, con los modelos de `Settings.modelos` (`vlm` y `llm`).
  ⚠️ El default del repo para `llm` es `qwen2.5:7b` y **puede no estar instalado**:
  el script lo dice con un mensaje claro en vez de fallar con un traceback, y
  ofrece la salida de `--sustituir-llm` (ver abajo).
- **Docling** (ya es dependencia de la librería).

⚠️ **Medido: sin el rol `llm`, el pipeline resuelve 4 de 16 campos.** La fuente
textual no aporta nada y la combinación queda con lo poco que el gate alcanzó a
leer. Medir así no mide el pipeline: mide la ausencia de un modelo. Por eso el
script **rechaza** correr en ese estado salvo que se pida el sustituto.

`--sustituir-llm` (⚠️ declarado en el reporte)
----------------------------------------------

Corre las **dos fuentes con el modelo del rol `vlm`**. Es lo que el plan §7
contemplaba («correr con el `qwen2.5vl` para las dos fuentes»), y **no es lo
mismo** que correr el pipeline completo:

| | Qué significa |
|---|---|
| ✅ | Las dos fuentes existen, así que el pipeline se ejercita entero (gate → 2 flujos → combinación). |
| ⚠️ | **Las dos fuentes son el MISMO modelo.** El «acuerdo VLM/LLM» del pipeline pasa a ser *un modelo contra sí mismo* y **no mide dos lecturas independientes**. |
| ⚠️ | Medido en un documento: **no sube la cobertura** (4 campos igual), pero sí destapa **6 lecturas descartadas por inválidas**. El sustituto razona peor en modo texto. |
| ⛔ | No es un resultado publicable como «acuerdo del pipeline»: es un **piso, medido con un modelo degradado**. Va declarado en el reporte, no escondido. |

Como es la única herramienta del artefacto que necesita servicios reales, **no
corre en la suite default**: se invoca a mano.

Uso:
    python scripts/verificacion/acuerdo-extraccion.py --listar
    python scripts/verificacion/acuerdo-extraccion.py --limite 3 --sustituir-llm
    python scripts/verificacion/acuerdo-extraccion.py --json /tmp/acuerdo.json

Sale con código 0 si se pudo medir, 2 si faltan los servicios o el artefacto.
Un **desacuerdo no es un fallo del script**: es el hallazgo que se va a leer.

⚠️ **`--workers` no acelera tanto como parece, y está medido.** El trabajo se
reparte por documento en hilos (Docling es nativo y suelta el GIL; `extract` no
comparte estado), pero **Ollama serializa la inferencia**: dos documentos en
paralelo dieron **55,8 s → 46,1 s (1,2x)**, con una sola llamada al VLM costando
35 s. Sirve para solapar el preprocesamiento con la lectura y para no esperar de
a uno, no para 4x. El default es 1 a propósito: el reporte declara con cuántos se
midió.
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


def _artefacto_presente() -> bool:
    """¿Está el artefacto en disco? (seam para el test del preflight)."""
    return (ARTEFACTO / "manifiesto.json").is_file()


def _modelos_instalados(ajustes: Any) -> set[str]:
    """Nombres de modelo que Ollama tiene ahora.

    Se separa del preflight para que el test pueda ejercitar la **decisión** (¿se
    puede medir?) sin depender de que Ollama esté corriendo ni de qué modelos haya
    en la máquina que corre la suite.
    """
    import urllib.request

    url = ajustes.url_ollama.rstrip("/") + "/api/tags"
    with urllib.request.urlopen(url, timeout=3) as respuesta:
        return {m["name"] for m in json.loads(respuesta.read()).get("models", [])}


def _preflight(sustituir_llm: bool = False) -> str | None:
    """Devuelve un motivo de aborto, o ``None`` si se puede correr.

    Se chequearlo **antes** de procesar: si falta un modelo, es mejor saberlo en
    un segundo que después de Docling sobre 30 documentos.
    """
    if not _artefacto_presente():
        return (
            "falta el artefacto. Generálo con "
            "`python scripts/operacion/generar-extracciones-esperadas.py`"
        )

    try:
        import urllib.error

        from voucherflow.settings.config import cargar_settings

        ajustes = cargar_settings()
        try:
            instalados = _modelos_instalados(ajustes)
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

        # ⚠️ El sustituto cubre **solo el rol `llm`**: sin el VLM no hay ninguna
        # fuente posible, así que ese faltante se reporta igual.
        requeridos = [ajustes.modelo_vlm]
        if not sustituir_llm:
            requeridos.append(ajustes.modelo_llm)
        faltantes = [modelo for modelo in requeridos if falta(modelo)]

        if faltantes and not sustituir_llm:
            # ⚠️ No se corre igual: sin el rol `llm` el pipeline resuelve 4 de 16
            # campos, así que el reporte mediría la AUSENCIA de un modelo y no la
            # lectura. Se exige la decisión explícita (`--sustituir-llm`) para que
            # nadie publique un piso degradado creyendo que midió el pipeline.
            return (
                f"faltan modelos en Ollama: {faltantes}. "
                f"Instalados: {sorted(instalados)}. "
                "⚠️ El rol `llm` del repo es `qwen2.5:7b`; bajalo con "
                "`ollama pull qwen2.5:7b`. Alternativa declarada: corré con "
                "`--sustituir-llm` para usar el mismo VLM en las dos fuentes "
                "(⚠️ las dos fuentes quedan siendo el mismo modelo: mide un piso, "
                "no el acuerdo del pipeline, y así lo declara el reporte)."
            )
        if faltantes:
            return (
                f"faltan modelos en Ollama: {faltantes} (el sustituto no cubre "
                "este faltante). "
                f"Instalados: {sorted(instalados)}."
            )
    except ImportError as exc:  # pragma: no cover - depende del entorno
        return f"no se pudo preparar el entorno: {exc}"

    return None


def _settings_de_corrida(sustituir_llm: bool) -> Any:
    """``Settings`` de la corrida, con el rol ``llm`` sustituido si se pidió.

    El sustituto **reemplaza el modelo del rol**, no lo agrega: así el pipeline
    sigue resolviendo dos fuentes (y ejercita la combinación entera), en vez de
    quedar con una sola.
    """
    from dataclasses import replace

    from voucherflow.settings.config import cargar_settings

    ajustes = cargar_settings()
    if not sustituir_llm:
        return ajustes

    rol_llm = ajustes.modelos.get("llm")
    if rol_llm is None or not ajustes.modelo_vlm:
        return ajustes
    # `Settings` es un dataclass mutable pero el rol puede compartirse entre
    # corridas: se copia con `replace` para no mutar el objeto global.
    return replace(
        ajustes,
        modelos={**ajustes.modelos, "llm": replace(rol_llm, modelo=ajustes.modelo_vlm)},
    )


def _medir(entrada: dict, mapa: dict, settings: Any = None) -> dict[str, Any]:
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
        evidencia = extract(str(ruta), settings=settings)
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
# Clasificación de los `ausente`
# --------------------------------------------------------------------------- #

#: Ninguna de las dos puntas declaró el campo.
AUSENTE_AMBOS = "ninguna"
#: Solo la referencia lo declaró: el pipeline no llegó a leerlo.
AUSENTE_SOLO_REFERENCIA = "solo_referencia"
#: Solo el pipeline lo declaró: la referencia no lo leyó (o no lo declaró).
AUSENTE_SOLO_PIPELINE = "solo_pipeline"

#: Orden de presentación (de lo que más importa a lo que menos).
CAUSAS_AUSENTE = (
    AUSENTE_SOLO_REFERENCIA,
    AUSENTE_SOLO_PIPELINE,
    AUSENTE_AMBOS,
)

#: Cómo se lee cada causa en el reporte.
LECTURA_AUSENTE = {
    AUSENTE_SOLO_REFERENCIA: (
        "la referencia lo leyó y el pipeline NO → cobertura del pipeline"
    ),
    AUSENTE_SOLO_PIPELINE: (
        "el pipeline lo leyó y la referencia NO → puede ser el pipeline leyendo más"
    ),
    AUSENTE_AMBOS: (
        "ninguna de las dos → acuerdo en que no está (no es un desacuerdo)"
    ),
}


def causa_de_ausente(campo: dict) -> str:
    """Clasifica un ``ausente`` por los **valores**, no por el texto de la nota.

    ⚠️ Leer los valores y no la `nota` es deliberado: los tres mensajes de
    ``comparacion.comparar`` describen la misma realidad, pero derivar la causa
    de un mensaje haría que retocar una redacción cambiara los números del
    reporte **en silencio** (misma lección que el reporte del corpus, que
    distinguía copia de omisión buscando texto en `motivo`).
    """
    tiene_ref = campo.get("valor_referencia") is not None
    tiene_pipe = campo.get("valor_pipeline") is not None
    if tiene_ref and not tiene_pipe:
        return AUSENTE_SOLO_REFERENCIA
    if tiene_pipe and not tiene_ref:
        return AUSENTE_SOLO_PIPELINE
    return AUSENTE_AMBOS


# --------------------------------------------------------------------------- #
# Reporte
# --------------------------------------------------------------------------- #


def _imprimir(
    medidos: list[dict],
    fallidos: list[dict],
    manifiesto: dict,
    corrida: dict | None = None,
) -> dict:
    corrida = corrida or {}
    entradas = manifiesto["entradas"]
    print("=== Acuerdo con la referencia (nivel B) ===")
    print(f"artefacto          : tests/expected-extraction (v{manifiesto['version']})")
    print(f"referencia         : {manifiesto['cobertura']['por_modelo']}")
    print(f"corridas medidas   : {len(medidos)} de {len(entradas)}")
    if fallidos:
        print(f"no medibles        : {len(fallidos)}")

    # ⚠️ Con qué modelos se midió: sin esto, un reporte hecho con el sustituto se
    # leería como si fuera el pipeline completo.
    if corrida.get("modelos"):
        print(f"modelos            : {corrida['modelos']}")
    if corrida.get("sustituido_llm"):
        print(
            "  ⚠ --sustituir-llm: las DOS fuentes son el mismo modelo. Mide un "
            "PISO, no el acuerdo de dos lecturas independientes."
        )
    if corrida.get("workers"):
        print(
            f"workers            : {corrida['workers']} "
            "(Ollama serializa la inferencia: el paralelismo acelera el "
            "preprocesamiento, no la lectura)"
        )

    totales: dict[str, int] = {}
    comparables = coincidencias = 0
    causas: dict[str, int] = {causa: 0 for causa in CAUSAS_AUSENTE}
    for m in medidos:
        for estado, n in m["recuento"].items():
            totales[estado] = totales.get(estado, 0) + n
        comparables += m["comparables"]
        coincidencias += m["coincidencias"]
        for campo in m["campos"]:
            if campo["estado"] == cmp.AUSENTE:
                causas[causa_de_ausente(campo)] += 1

    print(f"campos comparables : {comparables}")
    print("\n--- por estado ---")
    for estado in (*cmp.ESTADOS_QUE_PUNTUAN, cmp.AUSENTE, cmp.NO_COMPARABLE):
        n = totales.get(estado, 0)
        print(f"  {estado:22} {n:4}")

    # ⚠️ Un `ausente` agregado mezcla tres hechos distintos: "los dos no lo
    # leyeron", "solo lo leyó la referencia" y "solo lo leyó el pipeline". El
    # primero es un acuerdo; el segundo es falta de cobertura del pipeline.
    # Mostrarlos juntos deja que el número grande se lea como el primero.
    if any(causas.values()):
        print("\n--- `ausente`, por causa ---")
        for causa in CAUSAS_AUSENTE:
            print(f"  {causa:18} {causas[causa]:4}   {LECTURA_AUSENTE[causa]}")

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
            f"  campos en la combinación : "
            f"{sum(c['campos_resueltos'] or 0 for c in coberturas)} resueltos de "
            f"{sum(c['campos_totales'] or 0 for c in coberturas)}"
            "   (el contrato tiene 16: lo que falta no llegó a la combinación)"
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
        "ausente_por_causa": causas,
        "campos_comparables": comparables,
        "coincidencias": coincidencias,
        "corrida": corrida,
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
        "--sustituir-llm", action="store_true", dest="sustituir_llm",
        help=(
            "correr las dos fuentes con el modelo del rol `vlm` cuando el `llm` no "
            "está instalado (⚠ mide un piso: las dos fuentes son el mismo modelo)"
        ),
    )
    parser.add_argument(
        "--workers", type=int, default=1,
        help="documentos concurrentes (default: 1; ver la nota de --workers más abajo)",
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

    motivo = _preflight(args.sustituir_llm)
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

    settings = _settings_de_corrida(args.sustituir_llm)
    documentos = sorted(por_documento.items())

    print(f"midiendo {len(documentos)} documento(s) con el pipeline local…")
    medidos: list[dict] = []
    fallidos: list[dict] = []

    def _procesar(indice_y_par: tuple[int, tuple[str, list[dict]]]) -> list[dict]:
        """Mide un documento (todas sus corridas). Se corre en un hilo si hay workers.

        Se paraleliza **por documento**: las corridas de un mismo documento comparten
        la pasada del pipeline (agruparlas afuera es lo que evita procesar la imagen
        dos veces), así que no se pueden repartir entre hilos.
        """
        i, (documento, corridas) = indice_y_par
        print(f"  [{i}/{len(documentos)}] {documento}", flush=True)
        return [_medir(entrada, mapa, settings) for entrada in corridas]

    if args.workers > 1:
        from concurrent.futures import ThreadPoolExecutor

        with ThreadPoolExecutor(max_workers=args.workers) as pool:
            for resultados in pool.map(_procesar, enumerate(documentos, 1)):
                for resultado in resultados:
                    (fallidos if "motivo" in resultado else medidos).append(resultado)
    else:
        for par in enumerate(documentos, 1):
            for resultado in _procesar(par):
                (fallidos if "motivo" in resultado else medidos).append(resultado)

    print()
    corrida = {
        "modelos": {
            "vlm": settings.modelo_vlm,
            "llm": settings.modelo_llm,
        },
        "sustituido_llm": args.sustituir_llm,
        "workers": args.workers,
    }
    reporte = _imprimir(medidos, fallidos, manifiesto, corrida)

    if args.json:
        args.json.write_text(
            json.dumps(reporte, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
        )
        print(f"\nreporte            : {args.json}")

    return 0 if medidos else 2


if __name__ == "__main__":
    raise SystemExit(main())

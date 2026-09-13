#!/usr/bin/env python
"""Paridad de la **cadena contable** v2 vs. v1 (F3 / T-305, épica E-CLAS-2).

**Fase**: F3 (clasificación) · **Tarea**: T-305 · **Épica**: E-CLAS-2.

Mide el DoD de F3 ("la cadena contable reproduce v1", mitigando R-02) corriendo
**ambos lados con el mismo modelo** sobre los mismos markdown del subconjunto de
paridad (`tests/golden/F3/`) y comparando, caso por caso:

- ``centro_costo`` (1º del paso 01),
- ``macro_categoria`` (1ª del paso 02),
- ``concepto`` y ``codigo`` (paso 03).

Cómo corre cada lado
--------------------
- **v2**: `voucherflow.classification.contable.ejecutar_cadena` (la librería).
- **v1**: `v1/classification_pipeline.py` como **subproceso**, en un
  **directorio temporal** que reproduce la disposición que v1 espera
  (`PROMPTS_DIR = Path(__file__).parent / "prompts"`), porque en este repo la
  carpeta `v1/prompts/` está vacía y los YAML viven en `prompts/` (raíz). El
  script copia `v1/` + los YAML y corre desde ahí: **no muta el repo** y **no
  importa v1** (regla dura F3-subplan §4: la librería no se acopla a `v1/`; un
  script de paridad puede invocarlo como proceso).

Criterio de paridad (F3-subplan §3.5)
-------------------------------------
El DoD es **paridad o mejora documentada**. El reporte distingue:

- ``coincide``: mismo valor en ambos lados.
- ``difiere``: valores distintos (con ambos valores a la vista para explicarlo).
- ``no_comparable``: un lado no produjo valor (p. ej. v1 sin `codigo_final`
  porque la celda de la tabla es "—").

Las diferencias **esperables** se documentan en el reporte, no se ocultan: p.
ej. v1 acepta como `codigo_final` cualquier cosa que el modelo devuelva (en el
``11.1`` de v1 el modelo ponía la cuenta contable ahí), mientras que v2 toma el
``codigo_final`` del contrato del paso 03.

Uso:
    # Ambos lados con Ollama real (requiere el servicio y v1 en disco).
    python scripts/F3/paridad_contable.py
    python scripts/F3/paridad_contable.py --modelo qwen2.5:7b --condicion-impositiva 21
    python scripts/F3/paridad_contable.py --caso contable_repuestos --detalle
    python scripts/F3/paridad_contable.py --json /tmp/paridad_contable.json

    # Ver la disposición que se le arma a v1 sin correr nada (diagnóstico).
    python scripts/F3/paridad_contable.py --solo-preparar-v1

Nota: esta herramienta **no** corre en la suite default (necesita Ollama y v1).
La parte determinista vive en ``tests/test_classification_paridad.py``.
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

from voucherflow.classification import (  # noqa: E402
    CONDICION_IMPOSITIVA_DEFAULT,
    ejecutar_cadena,
)

# ---------------------------------------------------------------------------
# Rutas del repo
# ---------------------------------------------------------------------------

#: Raíz del repo (``v2/scripts/F3/paridad_contable.py`` → ``parents[3]``).
RAIZ_REPO = Path(__file__).resolve().parents[3]

#: Raíz del subconjunto de paridad de F3 (``tests/golden/F3``).
SUBCONJUNTO_DIR = RAIZ_REPO / "v2" / "tests" / "golden" / "F3"

#: Manifiesto del subconjunto.
SUBCONJUNTO = SUBCONJUNTO_DIR / "subconjunto.json"

#: Carpeta de casos sintéticos del subconjunto (``tests/golden/F3/casos``); las
#: rutas del manifiesto son relativas a :data:`SUBCONJUNTO_DIR`.
CASOS_BASE = SUBCONJUNTO_DIR

#: Carpeta ``v1/`` del repo.
V1_DIR = RAIZ_REPO / "v1"

#: Carpeta ``prompts/`` de la raíz (los YAML que v1 espera dentro de ``v1/``).
PROMPTS_RAIZ = RAIZ_REPO / "prompts"

#: Valores que v2 considera "sin dato" en el reporte.
VACIO = ("", "—", "none", "None", "null")


# ---------------------------------------------------------------------------
# Preparación del entorno de v1 (sin mutar el repo)
# ---------------------------------------------------------------------------


def preparar_v1(destino: Path) -> Path:
    """Arma un ``v1/`` ejecutable en ``destino`` (T-305).

    v1 resuelve sus prompts como ``Path(__file__).resolve().parent / "prompts"``
    (``v1/document_extraction.py``), y en este repo esa carpeta está **vacía**
    (no versionada): los YAML de los pasos 01/02/03 viven en ``prompts/`` en la
    raíz. Por eso se copia ``v1/`` (sin ``__pycache__``) y se le agregan los YAML
    de la raíz, reproduciendo la disposición que v1 espera.

    Se hace en un directorio temporal para **no mutar el repo** ni dejar estado
    (los sidecar que v1 escribe caen también ahí).

    Devuelve la ruta del ``v1/`` preparado.
    """
    v1_destino = destino / "v1"
    shutil.copytree(
        V1_DIR,
        v1_destino,
        ignore=shutil.ignore_patterns("__pycache__", "*.pyc"),
    )
    for yaml in sorted(PROMPTS_RAIZ.glob("*.yaml")):
        shutil.copy2(yaml, v1_destino / "prompts" / yaml.name)
    facturacion = PROMPTS_RAIZ / "facturacion"
    if facturacion.is_dir():
        for yaml in sorted(facturacion.glob("*.yaml")):
            shutil.copy2(yaml, v1_destino / "prompts" / "facturacion" / yaml.name)
    return v1_destino


def correr_v1(
    v1_preparado: Path,
    documentos: list[Path],
    *,
    modelo: str | None,
    condicion_impositiva: str,
    salida_dir: Path,
) -> tuple[dict[str, dict[str, Any]], str]:
    """Corre `v1/classification_pipeline.py` sobre los documentos (T-305).

    Invoca el script como **subproceso** (v1 no es un paquete instalable: usa
    imports absolutos ``from lib...``). Se corre **un proceso por documento**:
    la posición ``source`` de v1 es un único path (acepta un archivo o una
    carpeta, no una lista), así que pasarle varios documentos hace que argparse
    falle con el mensaje de uso. Se le pasa ``-o`` explícito para que agregue el
    resultado en un JSON propio en vez de escribir el sidecar junto al fixture.

    Devuelve ``(resultados_por_archivo, salida_completa)``. Un fallo de un
    documento no aborta el reporte: se devuelve lo que haya y la salida para
    diagnóstico.
    """
    resultados: dict[str, dict[str, Any]] = {}
    salidas: list[str] = []

    # Los documentos se **copian** a un subdirectorio temporal: v1 escribe
    # SIEMPRE un sidecar ``<doc>_classification.json`` junto al markdown
    # (``sidecar_output()``, además del ``-o``), así que correrlo sobre el
    # fixture dejaría archivos sucios en ``tests/golden/F3/casos/``. La copia
    # tiene el mismo nombre y contenido, y el reporte mapea por carpeta del
    # original (ver ``documentos`` en ``main``).
    docs_temporales = salida_dir.parent / "docs"
    docs_temporales.mkdir(parents=True, exist_ok=True)

    for documento in documentos:
        copia = docs_temporales / documento.name
        shutil.copy2(documento, copia)
        salida_json = salida_dir / f"v1_{documento.stem}.json"
        comando = [
            sys.executable,
            "classification_pipeline.py",
            str(copia),
            "--condicion-impositiva",
            condicion_impositiva,
            "-o",
            str(salida_json),
        ]
        if modelo:
            comando.extend(["-m", modelo])

        proceso = subprocess.run(
            comando,
            cwd=v1_preparado,
            capture_output=True,
            text=True,
            timeout=1800,
        )
        salidas.append((proceso.stdout or "") + (proceso.stderr or ""))

        if not salida_json.exists():
            continue
        try:
            datos = json.loads(salida_json.read_text(encoding="utf-8"))
        except ValueError:
            continue
        if isinstance(datos, dict):
            datos = [datos]
        for entrada in datos:
            if isinstance(entrada, dict) and entrada.get("archivo"):
                # Se indexa por el nombre del documento original (la copia tiene
                # el mismo nombre), para que el llamador busque por el fixture.
                resultados[str(documento)] = entrada

    return resultados, "\n".join(salidas)


# ---------------------------------------------------------------------------
# Lectura de resultados de cada lado
# ---------------------------------------------------------------------------


def _primero(paso: dict[str, Any], clave: str, campo: str) -> str | None:
    """Primer valor de ``paso[clave][0][campo]`` (o ``None``)."""
    opciones = paso.get(clave) if isinstance(paso, dict) else None
    if not isinstance(opciones, list) or not opciones:
        return None
    primera = opciones[0]
    if not isinstance(primera, dict):
        return None
    valor = primera.get(campo)
    if valor is None:
        return None
    texto = str(valor).strip()
    return texto or None


def campos_desde_v1(sidecar: dict[str, Any]) -> dict[str, str | None]:
    """Extrae los campos comparables del sidecar de v1 (T-305).

    Portado de la lógica de v1 (``primary_center_cost`` / ``primary_macro_category``
    + el paso 03): el centro y la macro salen del **primero** de cada lista.
    """
    pasos = sidecar.get("pasos") or {}
    paso_03 = pasos.get("03_concepto_codigo_final") or {}
    return {
        "centro_costo": _primero(pasos.get("01_centro_costo", {}), "centros_costos", "codigo_centro_costo"),
        "macro_categoria": _primero(pasos.get("02_macro_categoria", {}), "macro_categorias", "macro_categoria"),
        "concepto": (str(paso_03.get("concepto")).strip() or None) if paso_03.get("concepto") else None,
        "codigo": (str(paso_03.get("codigo_final")).strip() or None) if paso_03.get("codigo_final") else None,
    }


def campos_desde_v2(resultado: Any) -> dict[str, str | None]:
    """Extrae los campos comparables de un ``ResultadoCadenaContable`` (T-305)."""
    return {
        "centro_costo": resultado.centro_costo,
        "macro_categoria": resultado.macro_categoria,
        "concepto": resultado.concepto,
        "codigo": resultado.codigo,
    }


# ---------------------------------------------------------------------------
# Comparación
# ---------------------------------------------------------------------------


def comparar(
    v1: dict[str, str | None], v2: dict[str, str | None]
) -> dict[str, dict[str, Any]]:
    """Compara campo por campo los dos lados (T-305).

    Devuelve, por campo: ``v1``, ``v2``, ``estado`` (``coincide`` / ``difiere`` /
    ``no_comparable``) y ``nota`` cuando hace falta explicar la diferencia.
    """
    salida: dict[str, dict[str, Any]] = {}
    for campo in ("centro_costo", "macro_categoria", "concepto", "codigo"):
        a, b = v1.get(campo), v2.get(campo)
        if a is None or b is None:
            estado = "no_comparable"
            nota = (
                f"v1={'sin valor' if a is None else a}; "
                f"v2={'sin valor' if b is None else b} "
                "(la celda de la tabla puede ser '—' o un lado no resolvió la etapa)"
            )
        elif a == b:
            estado = "coincide"
            nota = ""
        else:
            estado = "difiere"
            nota = (
                "Diferencia esperable si el modelo devolvió la cuenta contable en "
                "el paso 03 de v1 (v1 aceptaba cualquier 'codigo_final'; v2 usa el "
                "contrato del prompt). Revisar el caso antes de reportarlo como "
                "regresión (F3-subplan §3.5: paridad **o mejora** documentada)."
            )
        salida[campo] = {"v1": a, "v2": b, "estado": estado, "nota": nota if estado != "coincide" else ""}
    return salida


def modelos_instalados() -> list[str]:
    """Nombres de los modelos que Ollama tiene instalados (best-effort).

    Devuelve lista vacía si Ollama no responde (el llamador decide qué hacer).
    Se consulta ``/api/tags`` directamente con ``urllib`` (stdlib) para no
    depender del cliente de la librería al diagnosticar el entorno.
    """
    import urllib.error
    import urllib.request

    try:
        with urllib.request.urlopen("http://localhost:11434/api/tags", timeout=5) as resp:
            datos = json.loads(resp.read().decode("utf-8"))
    except (urllib.error.URLError, OSError, ValueError):
        return []
    return [m.get("name", "") for m in datos.get("models", []) if m.get("name")]


def resolver_modelo(modelo: str | None, detectar: bool) -> tuple[str | None, str]:
    """Resuelve el modelo para la corrida de paridad (T-305).

    Reglas (documentadas en el reporte):

    1. ``--modelo`` explícito gana siempre (y se verifica que esté instalado).
    2. Si no, se usa el rol ``llm`` de ``Settings`` (``qwen2.5:7b``).
    3. Con ``--detectar-modelo``, si el elegido **no está instalado** se cae al
       rol ``vlm`` (``qwen2.5vl:3b``) y se devuelve la explicación.

    La caída al rol ``vlm`` es legítima para esta medición: lo que se compara es
    la **paridad de la cadena** (mismos prompts, mismo modelo en ambos lados),
    no qué modelo es el óptimo. Se informa para que el reporte no se lea como si
    hubiera corrido con el modelo de producción.

    Devuelve ``(modelo, explicacion)``.
    """
    if modelo:
        return modelo, f"modelo explícito (--modelo): {modelo}"

    from voucherflow.settings.config import cargar_settings

    settings = cargar_settings()
    rol_llm = settings.modelo_para("llm")
    elegido = rol_llm.modelo if rol_llm else None
    if not detectar:
        return elegido, f"rol 'llm' de Settings: {elegido}"

    instalados = modelos_instalados()
    if not instalados:
        return elegido, "rol 'llm' de Settings (no se pudo consultar Ollama para detectar)"
    if elegido in instalados:
        return elegido, f"rol 'llm' de Settings: {elegido} (instalado)"

    rol_vlm = settings.modelo_para("vlm")
    alternativo = rol_vlm.modelo if rol_vlm else None
    if alternativo in instalados:
        return (
            alternativo,
            (
                f"el rol 'llm' ({elegido}) NO está instalado en Ollama; se usa el "
                f"rol 'vlm' ({alternativo}) para ambos lados. La medición de paridad "
                "no depende del modelo elegido (mismos prompts y mismo modelo en v1 y v2)."
            ),
        )
    return elegido, f"rol 'llm' de Settings: {elegido} (no instalado; instalados: {instalados})"


def _leyenda(estado: str) -> str:
    return {"coincide": "=", "difiere": "≠", "no_comparable": "?"}.get(estado, "?")


# ---------------------------------------------------------------------------
# main
# ---------------------------------------------------------------------------


def main() -> None:
    parser = argparse.ArgumentParser(
        description=(
            "Paridad de la cadena contable 01→02→03 entre v2 (librería) y v1 "
            "(classification_pipeline.py) sobre el subconjunto F3 (T-305)."
        )
    )
    parser.add_argument("--modelo", help="Modelo de Ollama para ambos lados.")
    parser.add_argument(
        "--condicion-impositiva",
        default=CONDICION_IMPOSITIVA_DEFAULT,
        help=f"Condición del paso 03 (default {CONDICION_IMPOSITIVA_DEFAULT}).",
    )
    parser.add_argument(
        "--detectar-modelo",
        action="store_true",
        help=(
            "Elige el modelo disponible: si el rol 'llm' de Settings no está "
            "instalado en Ollama, cae al rol 'vlm' (qwen2.5vl:3b) y lo informa."
        ),
    )
    parser.add_argument("--caso", action="append", help="Filtra por id (repetible).")
    parser.add_argument("--detalle", action="store_true", help="Muestra los pasos crudos de cada lado.")
    parser.add_argument("--json", type=Path, help="Escribe el reporte completo en JSON.")
    parser.add_argument(
        "--solo-preparar-v1",
        action="store_true",
        help="Solo arma el entorno temporal de v1 y muestra la ruta (diagnóstico).",
    )
    args = parser.parse_args()

    subconjunto = json.loads(SUBCONJUNTO.read_text(encoding="utf-8"))
    casos = subconjunto.get("contable", [])
    if args.caso:
        pedidos = set(args.caso)
        casos = [caso for caso in casos if caso["id"] in pedidos]
    if not casos:
        print("No hay casos contables en el subconjunto (o el filtro no matchea).", file=sys.stderr)
        sys.exit(2)

    documentos = [CASOS_BASE / caso["archivo"] for caso in casos]
    faltantes = [str(doc) for doc in documentos if not doc.exists()]
    if faltantes:
        print(f"Faltan casos del subconjunto: {faltantes}", file=sys.stderr)
        sys.exit(2)

    print("Paridad de la cadena contable 01→02→03 (F3 / T-305)")
    print("=" * 78)
    print(f"Subconjunto : {SUBCONJUNTO.relative_to(RAIZ_REPO)}")
    print(f"Casos       : {', '.join(caso['id'] for caso in casos)}")
    modelo_usado, explicacion_modelo = resolver_modelo(args.modelo, args.detectar_modelo)
    print(f"Condición   : {args.condicion_impositiva}")
    print(f"Modelo      : {modelo_usado or '(sin modelo)'} — {explicacion_modelo}")
    print()

    reporte: dict[str, Any] = {
        "tarea": "T-305",
        "fase": "F3",
        "eje": "paridad_contable",
        "golden_version": subconjunto.get("golden_version"),
        "condicion_impositiva": args.condicion_impositiva,
        "modelo": modelo_usado,
        "modelo_explicacion": explicacion_modelo,
        "casos": [],
    }

    with tempfile.TemporaryDirectory(prefix="paridad_contable_") as tmp:
        tmpdir = Path(tmp)
        v1_preparado = preparar_v1(tmpdir)
        print(f"v1 preparado en: {v1_preparado}")
        if args.solo_preparar_v1:
            print("(--solo-preparar-v1: no se corre nada más)")
            return
        salida_v1_dir = tmpdir / "salidas_v1"
        salida_v1_dir.mkdir()
        print("Corriendo v1 (subproceso, un proceso por documento)…")
        resultados_v1, salida_v1 = correr_v1(
            v1_preparado,
            documentos,
            modelo=modelo_usado,
            condicion_impositiva=args.condicion_impositiva,
            salida_dir=salida_v1_dir,
        )
        print("Corriendo v2 (librería, Ollama real)…\n")

        from voucherflow.models.ollama import OllamaClient

        totales = {"coincide": 0, "difiere": 0, "no_comparable": 0}
        for caso, documento in zip(casos, documentos):
            texto = documento.read_text(encoding="utf-8")
            lado_v2: dict[str, str | None] = {}
            error_v2 = None
            pasos_v2: dict[str, Any] = {}
            try:
                resultado = ejecutar_cadena(
                    OllamaClient(),
                    descripcion=texto,
                    condicion_impositiva=args.condicion_impositiva,
                    checkpoint=tmpdir / f"v2_{caso['id']}_classification.json",
                    modelo=modelo_usado,
                )
                lado_v2 = campos_desde_v2(resultado)
                pasos_v2 = resultado.pasos
            except Exception as exc:  # noqa: BLE001 - herramienta manual
                error_v2 = f"{type(exc).__name__}: {exc}"

            sidecar_v1 = resultados_v1.get(str(documento), {})
            lado_v1 = campos_desde_v1(sidecar_v1) if sidecar_v1 else {}
            comparacion = comparar(lado_v1, lado_v2) if lado_v2 else {}

            estados = [info["estado"] for info in comparacion.values()]
            if error_v2 or not comparacion:
                veredicto = "sin_datos"
            elif "difiere" in estados:
                veredicto = "difiere"
            elif all(estado == "no_comparable" for estado in estados):
                veredicto = "no_comparable"
            else:
                veredicto = "coincide"
            for estado in estados:
                if estado in totales:
                    totales[estado] += 1

            simbolo = {"coincide": "✅", "difiere": "❌", "no_comparable": "➖", "sin_datos": "⚠"}[veredicto]
            codigo_v1 = lado_v1.get("codigo") or "-"
            codigo_v2 = lado_v2.get("codigo") or "-"
            print(f"  {simbolo} {caso['id']:<26} {veredicto:<14} codigo v1={codigo_v1} v2={codigo_v2}")
            for campo, info in comparacion.items():
                if info["estado"] == "coincide":
                    continue
                print(f"      {_leyenda(info['estado'])} {campo}: v1={info['v1']!r} v2={info['v2']!r}")
                if info["nota"]:
                    print(f"        ↳ {info['nota']}")
            if error_v2:
                print(f"      ⚠ v2: {error_v2}")
            if args.detalle:
                print(f"      v1 pasos: {json.dumps(sidecar_v1.get('pasos', {}), ensure_ascii=False)[:220]}")
                print(f"      v2 pasos: {json.dumps(pasos_v2, ensure_ascii=False)[:220]}")

            reporte["casos"].append(
                {
                    "id": caso["id"],
                    "archivo": caso["archivo"],
                    "veredicto": veredicto,
                    "comparacion": comparacion,
                    "v1": lado_v1,
                    "v2": lado_v2,
                    "error_v2": error_v2,
                    "pasos_v1": sidecar_v1.get("pasos", {}),
                    "pasos_v2": pasos_v2,
                }
            )

        print()
        print("--- Métricas del DoD (F3-subplan §3.5) ---")
        print(f"  campos que coinciden      : {totales['coincide']}")
        print(f"  campos que difieren       : {totales['difiere']}")
        print(f"  campos no comparables     : {totales['no_comparable']}")
        print(f"  salida de v1 (primeras líneas): {salida_v1.strip().splitlines()[:2]}")
        reporte["totales"] = totales
        reporte["salida_v1"] = salida_v1[-2000:]

    if args.json:
        args.json.write_text(json.dumps(reporte, ensure_ascii=False, indent=2), encoding="utf-8")
        print(f"Reporte JSON: {args.json}")


if __name__ == "__main__":
    main()

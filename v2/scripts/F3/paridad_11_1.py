#!/usr/bin/env python
"""Paridad del **tipo/letra** v2 vs. el modo `-M 11.1` de v1 (F3 / T-305).

**Fase**: F3 (clasificación) · **Tarea**: T-305 · **Épica**: E-CLAS-1.

Mide el eje de **tipo/letra** del DoD de F3 (mitiga R-02): corre el prompt
`11.1` de v1 (`v1/document_extraction.py -M 11.1`) y el flujo de evidencia de v2
(T-302/T-303 → motor de reglas T-301) sobre los **mismos documentos** del
subconjunto de paridad y compara la **letra**.

Qué se compara (y qué no)
-------------------------
- v1 devuelve un JSON con ``tipo_comprobante`` (la letra que **el modelo**
  decidió aplicando el prompt, que incluía las reglas R1-R3 y la validación R7).
- v2 **no le pide la letra al modelo** (ADR-006): el modelo reporta *evidencia*
  y la letra la decide el motor de reglas R1-R7. El script le pasa al contexto la
  misma letra del prompt de v2 (T-302) y arma el contexto con las condiciones
  fiscales del caso.

Por eso hay **dos modos** de caso, y el reporte los distingue:

1. **Sintéticos** (``letra_sintetica`` del subconjunto): el markdown declara la
   letra y el caso declara las condiciones fiscales. Acá la comparación es
   *apples-to-apples*: a v2 se le pasan las mismas condiciones que v1 infiere del
   texto. La letra esperada además está **etiquetada**, así que se reporta
   exactitud, no solo acuerdo.
2. **Reales** (``letra_real`` del subconjunto): nadie etiquetó la condición
   fiscal todavía (requiere contador, F2 §2.5), así que ambos lados corren
   **sin** ella. La comparación mide el **acuerdo del extractor de letra** — no
   la exactitud de la letra final — y así se reporta.

Ese recorte es la decisión de alcance F3-subplan §2.9: no se espera la curación
masiva del golden, se mide sobre lo que tiene **sustento objetivo**.

Uso:
    python scripts/F3/paridad_11_1.py                    # sintéticos + reales
    python scripts/F3/paridad_11_1.py --grupo sinteticos
    python scripts/F3/paridad_11_1.py --grupo reales
    python scripts/F3/paridad_11_1.py --detectar-modelo --detalle
    python scripts/F3/paridad_11_1.py --json /tmp/paridad_11_1.json

Nota: necesita Ollama y `v1/`; **no** corre en la suite default. La parte
determinista vive en ``tests/test_classification_paridad.py``.
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

from voucherflow.classification import clasificar_tipo_comprobante  # noqa: E402
from voucherflow.rules.contexto import ContextoTipoComprobante  # noqa: E402

#: Raíz del repo.
RAIZ_REPO = Path(__file__).resolve().parents[3]

#: Raíz del subconjunto de paridad de F3.
SUBCONJUNTO_DIR = RAIZ_REPO / "v2" / "tests" / "golden" / "F3"

#: Manifiesto del subconjunto.
SUBCONJUNTO = SUBCONJUNTO_DIR / "subconjunto.json"

#: Carpeta ``v1/`` y YAML de prompts de la raíz (v1 espera los suyos en ``v1/prompts``).
V1_DIR = RAIZ_REPO / "v1"
PROMPTS_RAIZ = RAIZ_REPO / "prompts"

#: Etiquetas que v1 puede devolver en ``tipo_comprobante`` y que **no** son una
#: letra (van a ``tipo_comprobante`` como None con la letra en otro campo).
VALORES_NO_LETRA = {"", "none", "null", "n/a", "desconocido"}


# ---------------------------------------------------------------------------
# Entorno de v1 (misma estrategia que paridad_contable.py)
# ---------------------------------------------------------------------------


def preparar_v1(destino: Path) -> Path:
    """Copia ``v1/`` + los YAML de ``prompts/`` a ``destino`` (T-305).

    v1 resuelve sus prompts como ``Path(__file__).parent / "prompts"`` y en este
    repo esa carpeta está vacía; los YAML viven en ``prompts/`` (raíz). Se
    reproduce esa disposición en un temporal para **no mutar el repo**.
    """
    v1_destino = destino / "v1"
    shutil.copytree(
        V1_DIR, v1_destino, ignore=shutil.ignore_patterns("__pycache__", "*.pyc")
    )
    for yaml in sorted(PROMPTS_RAIZ.glob("*.yaml")):
        shutil.copy2(yaml, v1_destino / "prompts" / yaml.name)
    facturacion = PROMPTS_RAIZ / "facturacion"
    if facturacion.is_dir():
        for yaml in sorted(facturacion.glob("*.yaml")):
            shutil.copy2(yaml, v1_destino / "prompts" / "facturacion" / yaml.name)
    return v1_destino


def correr_11_1(
    v1_preparado: Path,
    documento: Path,
    *,
    modelo: str | None,
    modo: str = "11.1",
) -> tuple[dict[str, Any], str]:
    """Corre ``document_extraction.py -M 11.1`` sobre un documento (T-305).

    v1 imprime el JSON de la respuesta del modelo por **stdout**; se captura y se
    parsea el primer objeto JSON. Devuelve ``(json_del_modelo, salida_cruda)``.
    Un fallo devuelve ``({}, salida)`` (no aborta el reporte).
    """
    comando = [sys.executable, "document_extraction.py", str(documento), "-M", modo]
    if modelo:
        comando.extend(["--model", modelo])
    proceso = subprocess.run(
        comando, cwd=v1_preparado, capture_output=True, text=True, timeout=1800
    )
    salida = (proceso.stdout or "") + (proceso.stderr or "")
    return _primer_json(salida), salida


def _primer_json(texto: str) -> dict[str, Any]:
    """Extrae el primer objeto JSON de una salida de texto (T-305).

    Portado de la idea de ``extract_json`` de v1 (que busca el primer ``{`` y el
    último ``}``): acá se usa ``raw_decode`` desde cada ``{``, que es más robusto
    que contar llaves cuando la salida trae prosa alrededor.
    """
    for indice, caracter in enumerate(texto):
        if caracter != "{":
            continue
        try:
            datos, _fin = json.JSONDecoder().raw_decode(texto[indice:])
        except ValueError:
            continue
        if isinstance(datos, dict):
            return datos
    return {}


# ---------------------------------------------------------------------------
# Letra de cada lado
# ---------------------------------------------------------------------------


def letra_de_v1(respuesta: dict[str, Any]) -> str | None:
    """Letra que devolvió el `11.1` de v1 (T-305).

    v1 devolvía la **decisión** del modelo en ``tipo_comprobante`` (aplicando
    R1-R3 + R7 en el prompt). Si el valor no es una letra del vocabulario (el
    modelo respondió "pendiente"/null), se devuelve ``None`` y queda registrado
    como no comparable en el reporte.
    """
    valor = respuesta.get("tipo_comprobante")
    if valor is None:
        return None
    texto = str(valor).strip().upper()
    if texto.lower() in VALORES_NO_LETRA:
        return None
    return texto or None


def letra_de_v2(markdown: str, caso: dict[str, Any]) -> tuple[str | None, dict[str, Any]]:
    """Letra que decide el motor de reglas de v2 sobre el **texto** (T-305).

    Construye el contexto con las condiciones fiscales del caso (si están
    declaradas) y el markdown como ``texto_encabezado_llm`` — que es el insumo
    del extractor R5 — y llama ``clasificar_tipo_comprobante``.

    Devuelve ``(letra, resultado_dict)``.
    """
    campos_ausentes = []
    if not caso.get("condicion_emisor"):
        campos_ausentes.append("emisor.condicion_fiscal")
    if not caso.get("condicion_receptor"):
        campos_ausentes.append("receptor.condicion_fiscal")

    contexto = ContextoTipoComprobante(
        emisor_condicion_fiscal=caso.get("condicion_emisor"),
        receptor_condicion_fiscal=caso.get("condicion_receptor"),
        receptor_pais=caso.get("receptor_pais"),
        texto_encabezado_llm=markdown,
        campos_ausentes=campos_ausentes,
    )
    resultado = clasificar_tipo_comprobante(contexto)
    return resultado.letra, {
        "letra": resultado.letra,
        "certeza": resultado.certeza,
        "tipo_esperado_por_negocio": resultado.tipo_esperado_por_negocio,
        "tipo_detectado_por_documento": resultado.tipo_detectado_por_documento,
        "reglas_aplicadas": resultado.reglas_aplicadas,
        "alertas": [alerta["regla"] for alerta in resultado.alertas],
        "campos_desconocidos": resultado.campos_desconocidos,
    }


def modelos_instalados() -> list[str]:
    """Nombres de los modelos instalados en Ollama (best-effort, stdlib)."""
    import urllib.error
    import urllib.request

    try:
        with urllib.request.urlopen("http://localhost:11434/api/tags", timeout=5) as resp:
            datos = json.loads(resp.read().decode("utf-8"))
    except (urllib.error.URLError, OSError, ValueError):
        return []
    return [m.get("name", "") for m in datos.get("models", []) if m.get("name")]


def resolver_modelo(modelo: str | None, detectar: bool) -> tuple[str | None, str]:
    """Resuelve el modelo para la comparación (misma lógica que paridad_contable).

    Acá el modelo importa más que en la cadena contable: el `11.1` de v1 es un
    prompt **visual** (busca la letra en el recuadro de la imagen), así que el
    rol natural es ``vlm`` (``qwen2.5vl:3b``), que es además el default con el
    que v1 corre (``DEFAULT_MODEL``). Se documenta la elección en el reporte.
    """
    if modelo:
        return modelo, f"modelo explícito (--modelo): {modelo}"

    from voucherflow.settings.config import cargar_settings

    settings = cargar_settings()
    rol_vlm = settings.modelo_para("vlm")
    elegido = rol_vlm.modelo if rol_vlm else None
    if not detectar:
        return elegido, f"rol 'vlm' de Settings: {elegido}"

    instalados = modelos_instalados()
    if not instalados:
        return elegido, "rol 'vlm' de Settings (no se pudo consultar Ollama)"
    if elegido in instalados:
        return elegido, f"rol 'vlm' de Settings: {elegido} (instalado)"
    return elegido, f"rol 'vlm' de Settings: {elegido} (NO instalado; instalados: {instalados})"


# ---------------------------------------------------------------------------
# main
# ---------------------------------------------------------------------------


def _leyenda(estado: str) -> str:
    return {"coincide": "=", "difiere": "≠", "no_comparable": "?"}.get(estado, "?")


def main() -> None:
    parser = argparse.ArgumentParser(
        description=(
            "Paridad de tipo/letra entre v2 (motor de reglas sobre evidencia) y "
            "el modo -M 11.1 de v1, sobre el subconjunto F3 (T-305)."
        )
    )
    parser.add_argument("--modelo", help="Modelo de Ollama para v1 (default: rol vlm de Settings).")
    parser.add_argument("--detectar-modelo", action="store_true", help="Verifica que el modelo esté instalado.")
    parser.add_argument(
        "--grupo",
        choices=("sinteticos", "reales", "todos"),
        default="todos",
        help="Qué grupo del subconjunto correr (default: todos).",
    )
    parser.add_argument("--caso", action="append", help="Filtra por id (repetible).")
    parser.add_argument("--detalle", action="store_true", help="Muestra la salida de v1 y el contexto de v2.")
    parser.add_argument("--json", type=Path, help="Escribe el reporte completo en JSON.")
    args = parser.parse_args()

    subconjunto = json.loads(SUBCONJUNTO.read_text(encoding="utf-8"))
    casos: list[dict[str, Any]] = []
    if args.grupo in ("todos", "sinteticos"):
        casos.extend({**caso, "grupo": "sintetico"} for caso in subconjunto.get("letra_sintetica", []))
    if args.grupo in ("todos", "reales"):
        casos.extend({**caso, "grupo": "real"} for caso in subconjunto.get("letra_real", []))
    if args.caso:
        pedidos = set(args.caso)
        casos = [caso for caso in casos if caso["id"] in pedidos]
    if not casos:
        print("No hay casos de letra en el subconjunto (o el filtro no matchea).", file=sys.stderr)
        sys.exit(2)

    modelo_usado, explicacion_modelo = resolver_modelo(args.modelo, args.detectar_modelo)

    print("Paridad de tipo/letra: v2 (motor R1-R7) vs. v1 (-M 11.1) (F3 / T-305)")
    print("=" * 78)
    print(f"Grupo  : {args.grupo}")
    print(f"Modelo : {modelo_usado or '(sin modelo)'} — {explicacion_modelo}")
    print()

    reporte: dict[str, Any] = {
        "tarea": "T-305",
        "fase": "F3",
        "eje": "paridad_11_1",
        "grupo": args.grupo,
        "modelo": modelo_usado,
        "modelo_explicacion": explicacion_modelo,
        "golden_version": subconjunto.get("golden_version"),
        "casos": [],
    }

    comparables = 0
    acuerdos = 0
    exactos_v2 = 0
    exactos_v1 = 0
    etiquetados = 0
    sin_letra_v1 = 0

    with tempfile.TemporaryDirectory(prefix="paridad_11_1_") as tmp:
        tmpdir = Path(tmp)
        v1_preparado = preparar_v1(tmpdir)
        print(f"v1 preparado en: {v1_preparado}\n")

        for caso in casos:
            ruta = (
                SUBCONJUNTO_DIR / caso["archivo"]
                if "archivo" in caso
                else RAIZ_REPO / caso["ruta"]
            )
            if not ruta.exists():
                print(f"  ⚠ {caso['id']:<32} falta el documento: {ruta}")
                continue

            # v1 corre sobre una copia: v1/ask.py puede convertir el documento
            # (Docling) y no queremos que escriba nada junto al fixture.
            copia = tmpdir / ruta.name
            shutil.copy2(ruta, copia)

            respuesta_v1, salida_v1 = correr_11_1(
                v1_preparado, copia, modelo=modelo_usado
            )
            letra_v1 = letra_de_v1(respuesta_v1)

            if ruta.suffix.lower() in {".md", ".txt"}:
                markdown = ruta.read_text(encoding="utf-8")
            else:
                # Documento real: se usa el texto del propio prompt de v1 (lo que
                # el modelo vio) solo si v1 no pudo convertir; si no, el markdown
                # se genera con F1 (api.process) para que v2 lea lo mismo.
                try:
                    from voucherflow.api import process

                    markdown = process(str(ruta)).markdown
                except Exception as exc:  # noqa: BLE001 - herramienta manual
                    print(f"  ⚠ {caso['id']:<32} no se pudo procesar con F1: {exc}")
                    markdown = ""

            letra_v2, detalle_v2 = letra_de_v2(markdown, caso)

            if letra_v1 is None or letra_v2 is None:
                estado = "no_comparable"
                nota = (
                    f"v1={'sin letra' if letra_v1 is None else letra_v1}; "
                    f"v2={'sin letra' if letra_v2 is None else letra_v2} "
                    "(algún lado no concluyó la letra: no se computa como desacuerdo)"
                )
            elif letra_v1 == letra_v2:
                estado = "coincide"
                nota = ""
            else:
                estado = "difiere"
                nota = (
                    "Diferencia de criterio: v1 le pide la letra al modelo (aplicando "
                    "R1-R3/R7 en el prompt) y v2 la decide por reglas sobre la evidencia "
                    "(ADR-006). Revisar el caso antes de reportarlo como regresión "
                    "(F3-subplan §3.5: paridad o mejora documentada)."
                )

            if estado != "no_comparable":
                comparables += 1
                acuerdos += int(estado == "coincide")
            if letra_v1 is None:
                sin_letra_v1 += 1
            esperada = caso.get("letra_esperada_final") or caso.get("letra_esperada")
            if esperada:
                etiquetados += 1
                exactos_v2 += int(letra_v2 == esperada)
                # La exactitud de v1 sobre el mismo caso etiquetado: es lo que
                # convierte un "desacuerdo" en "mejora" (o en regresión).
                exactos_v1 += int(letra_v1 == esperada)

            simbolo = {"coincide": "✅", "difiere": "❌", "no_comparable": "➖"}[estado]
            print(
                f"  {simbolo} {caso['id']:<32} {estado:<14} "
                f"letra v1={letra_v1 or '-'} v2={letra_v2 or '-'} "
                f"({caso['grupo']})"
            )
            if nota:
                print(f"      {_leyenda(estado)} {nota}")
            if args.detalle:
                print(f"      v2: {json.dumps(detalle_v2, ensure_ascii=False)}")
                print(f"      v1 crudo: {json.dumps(respuesta_v1, ensure_ascii=False)[:220]}")

            reporte["casos"].append(
                {
                    "id": caso["id"],
                    "grupo": caso["grupo"],
                    "documento": str(ruta.relative_to(RAIZ_REPO)),
                    "letra_v1": letra_v1,
                    "letra_v2": letra_v2,
                    "estado": estado,
                    "nota": nota,
                    "letra_esperada": esperada,
                    "acierto_v2": (letra_v2 == esperada) if esperada else None,
                    "acierto_v1": (letra_v1 == esperada) if esperada else None,
                    "detalle_v2": detalle_v2,
                    "respuesta_v1": respuesta_v1,
                    "salida_v1": salida_v1[-800:],
                }
            )

        print()
        print("--- Métricas del DoD (F3-subplan §3.5) ---")
        if comparables:
            print(f"  acuerdo con v1 (letra)         : {acuerdos}/{comparables} "
                  f"({100 * acuerdos / comparables:.0f}%)")
        else:
            print("  acuerdo con v1 (letra)         : sin casos comparables")
        if etiquetados:
            print(f"  exactitud de letra — v2        : {exactos_v2}/{etiquetados} "
                  f"({100 * exactos_v2 / etiquetados:.0f}%)")
            print(f"  exactitud de letra — v1        : {exactos_v1}/{etiquetados} "
                  f"({100 * exactos_v1 / etiquetados:.0f}%)")
        print(f"  casos donde v1 no concluyó letra: {sin_letra_v1} "
              "(tipo_comprobante vacío: la comparación no cuenta como desacuerdo)")
        print(
            "  Criterio (F3-subplan §3.5): el DoD es paridad **o mejora** documentada. "
            "Se reportan las dos exactitudes para que un desacuerdo se lea como mejora "
            "(o regresión) con evidencia, no como un número aislado."
        )
        print(
            "  Nota: en los casos 'reales' la comparación mide el acuerdo del "
            "extractor (sin condición fiscal etiquetada); en los 'sinteticos' hay "
            "condiciones declaradas y letra esperada, así que además se reporta exactitud."
        )
        reporte["metricas"] = {
            "comparables": comparables,
            "acuerdos": acuerdos,
            "etiquetados": etiquetados,
            "exactos_v2": exactos_v2,
            "exactos_v1": exactos_v1,
            "sin_letra_v1": sin_letra_v1,
        }

    if args.json:
        args.json.write_text(json.dumps(reporte, ensure_ascii=False, indent=2), encoding="utf-8")
        print(f"Reporte JSON: {args.json}")


if __name__ == "__main__":
    main()

#!/usr/bin/env python
"""Inspecciona T-304 (F3) — cadena contable 01→02→03 (contratos + checkpoints).

**Fase**: F3 (clasificación) · **Tarea**: T-304 · **Épica**: E-CLAS-2.

Muestra, **sin Ollama ni Docling** (el cliente se inyecta como doble):

  1. Los **contratos por paso** y las **versiones de prompt** de la cadena
     (`--prompts` imprime además el system/user de cada paso).
  2. La **cadena completa 01→02→03** sobre escenarios sintéticos que cubren la
     cadena feliz, el **default CC0006 / senal_usada=ninguna** (criterio Gherkin
     de E-CLAS-2), el paso 03 sin código (celda "—"), el error con resultados
     parciales y la **reanudación por checkpoint**.
  3. La **variante pura** `clasificar_contable()` sobre los mismos pasos, y la
     verificación de que **coincide** con la cadena real (misma centro/macro/
     concepto/código) — la garantía de que ambos caminos no divergen.

Con `--origen <markdown>` corre la cadena **real** contra Ollama (los tres pasos
con el modelo de texto de `Settings`, rol `llm`). Es la corrida que T-305
comparará contra `v1/classification_pipeline.py`.

Uso:
    python scripts/F3/t304.py                          # escenarios sintéticos
    python scripts/F3/t304.py --prompts                # además, los prompts
    python scripts/F3/t304.py --detalle                # traza por escenario
    python scripts/F3/t304.py --caso default_cc0006
    python scripts/F3/t304.py --json /tmp/t304.json
    python scripts/F3/t304.py --origen files/2025-08/2D2C9343/<doc>.md --detalle

Nota: la suite default de pytest cubre lo mismo en
`tests/test_classification_contable.py`; este script es la verificación de humo
legible para la bitácora (sale con código ≠ 0 si algún escenario falla).
"""

from __future__ import annotations

import argparse
import json
import sys
import tempfile
from dataclasses import asdict
from pathlib import Path
from typing import Any

# El paquete usa layout src/; si se corre sin instalar, se agrega src/ al PATH.
_SRC = Path(__file__).resolve().parents[2] / "src"
if str(_SRC) not in sys.path:
    sys.path.insert(0, str(_SRC))

from voucherflow.classification import (  # noqa: E402
    CONDICION_IMPOSITIVA_DEFAULT,
    PASOS_CONTABLES,
    REGLAS_CADENA_CONTABLE,
    VALOR_NO_INFORMADO,
    ErrorCadenaContable,
    clasificar_contable,
    ejecutar_cadena,
    leer_checkpoint,
    renderizar_user,
    ruta_checkpoint,
)
from voucherflow.models.ollama import RespuestaOllama  # noqa: E402

# ---------------------------------------------------------------------------
# Doble del cliente (sin Ollama real)
# ---------------------------------------------------------------------------


class LectorDoble:
    """Lector doble: responde por paso y registra las llamadas (sin Ollama)."""

    def __init__(
        self,
        respuestas: dict[str, Any] | None = None,
        contenido_crudo: dict[str, str] | None = None,
    ) -> None:
        self.por_paso = dict(respuestas or {})
        self.contenido_crudo = dict(contenido_crudo or {})
        self.llamadas: list[dict[str, Any]] = []

    def _paso(self, messages: list[dict[str, Any]]) -> str:
        sistema = messages[0].get("content", "") if messages else ""
        for paso, definicion in PASOS_CONTABLES.items():
            if definicion["system"] == sistema:
                return paso
        return ""

    def ask(self, messages, model, json_format=False, options=None, num_ctx=None):
        paso = self._paso(messages)
        self.llamadas.append({"paso": paso, "model": model, "messages": messages})
        if paso in self.contenido_crudo:
            contenido = self.contenido_crudo[paso]
        else:
            contenido = json.dumps(self.por_paso.get(paso, {}), ensure_ascii=False)
        return RespuestaOllama(
            contenido=contenido, modelo=model, latencia_s=0.0, status=200
        )


# ---------------------------------------------------------------------------
# Respuestas sintéticas (shape del prompt de v1)
# ---------------------------------------------------------------------------

PASO_01 = {
    "centros_costos": [
        {
            "codigo_centro_costo": "CC0004",
            "centro": "Repuestos",
            "confianza": "alta",
            "senal_usada": "descripcion",
            "justificacion": "La descripción menciona repuestos.",
        },
        {"codigo_centro_costo": "CC0005", "centro": "Servicios", "confianza": "baja"},
    ]
}

PASO_01_DEFAULT = {
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

PASO_02 = {
    "macro_categorias": [
        {
            "macro_categoria": "MC07",
            "nombre": "Operaciones y Logística",
            "confianza": "alta",
            "justificacion": "Materiales e insumos.",
        },
        {"macro_categoria": "MC08", "nombre": "Activos y Mantenimiento", "confianza": "baja"},
    ]
}

PASO_03 = {
    "macro_categoria": "MC07",
    "concepto": "CT017",
    "nombre_concepto": "Materiales, insumos y herramientas",
    "cuenta_contable": "4221,16",
    "condicion_impositiva": "21",
    "codigo_final": "48",
    "candidatos_codigo_final": [],
    "requiere_revision_humana": False,
    "confianza": "alta",
    "justificacion": "Materiales.",
}

PASO_03_SIN_CODIGO = {
    "macro_categoria": "MC03",
    "concepto": "CT007",
    "nombre_concepto": "Publicidad",
    "cuenta_contable": "4221,10",
    "condicion_impositiva": "27",
    "codigo_final": None,
    "candidatos_codigo_final": [],
    "requiere_revision_humana": True,
    "confianza": "baja",
    "justificacion": "No existe código para publicidad al 27%.",
}

#: Escenarios: (nombre, respuestas por paso, expectativa). La expectativa declara
#: lo que la cadena debe producir (o el error esperado).
ESCENARIOS: list[dict[str, Any]] = [
    {
        "nombre": "cadena_feliz",
        "respuestas": {"01": PASO_01, "02": PASO_02, "03": PASO_03},
        "expectativa": {
            "centro": "CC0004",
            "macro": "MC07",
            "concepto": "CT017",
            "codigo": "48",
            "llamadas": ["01", "02", "03"],
        },
    },
    {
        "nombre": "default_cc0006",
        "respuestas": {"01": PASO_01_DEFAULT, "02": PASO_02, "03": PASO_03},
        "expectativa": {
            "centro": "CC0006",
            "macro": "MC07",
            "codigo": "48",
            "senal_usada": "ninguna",
            "confianza_centro": "baja",
        },
    },
    {
        "nombre": "paso_03_sin_codigo",
        "respuestas": {"01": PASO_01, "02": PASO_02, "03": PASO_03_SIN_CODIGO},
        "expectativa": {
            "centro": "CC0004",
            "macro": "MC07",
            "concepto": "CT007",
            "codigo": None,
            "requiere_revision_humana": True,
        },
    },
    {
        "nombre": "condicion_10_5",
        "respuestas": {"01": PASO_01, "02": PASO_02, "03": PASO_03},
        "condicion": "10_5",
        "expectativa": {
            "centro": "CC0004",
            "condicion_impositiva": "10_5",
            "placeholder_en_paso_03": "condicion_impositiva: 10_5",
        },
    },
    {
        "nombre": "paso_01_sin_opciones",
        "respuestas": {"01": {"centros_costos": []}},
        # El paso 01 se registra en el checkpoint antes de leerle las opciones
        # (igual que v1), así que aparece en los parciales. v1 dejaba escapar un
        # ``ValueError`` pelado acá; T-304 lo envuelve y preserva lo resuelto.
        "expectativa": {"error": ErrorCadenaContable, "parciales_esperados": ["01_centro_costo"]},
    },
    {
        "nombre": "paso_02_sin_opciones",
        "respuestas": {"01": PASO_01, "02": {"macro_categorias": []}},
        "expectativa": {
            "error": ErrorCadenaContable,
            "parciales_esperados": ["01_centro_costo", "02_macro_categoria"],
        },
    },
    {
        "nombre": "json_invalido_paso_02",
        "respuestas": {"01": PASO_01},
        "contenido_crudo": {"02": "no soy JSON"},
        "expectativa": {"error": ErrorCadenaContable, "parciales_esperados": ["01_centro_costo"]},
    },
    {
        "nombre": "reanudacion_por_checkpoint",
        "respuestas": {"01": PASO_01, "02": PASO_02, "03": PASO_03},
        "expectativa": {
            "centro": "CC0004",
            "macro": "MC07",
            "codigo": "48",
            "llamadas": [],  # la 2ª corrida no debe llamar al modelo
        },
        "reanudar": True,
    },
]


# ---------------------------------------------------------------------------
# Impresión
# ---------------------------------------------------------------------------


def _imprimir_prompts() -> None:
    """Imprime las versiones y el system prompt de cada paso contable."""
    print("Prompts contables versionados (T-304)")
    print("=" * 78)
    for paso, definicion in sorted(PASOS_CONTABLES.items()):
        print(f"\n--- paso {paso} · versión {definicion['version']} ---")
        print(f"placeholders: {definicion['placeholders']}")
        print("system:")
        print(
            "\n".join(
                f"  {linea}" for linea in str(definicion["system"]).splitlines()[:14]
            )
        )
        print("  …")
    print()


def _verificar(
    resultado, error, llamadas: list[dict[str, Any]], expectativa: dict[str, Any]
) -> list[str]:
    """Compara la corrida real con la expectativa; devuelve las diferencias."""
    problemas: list[str] = []

    if "error" in expectativa:
        if error is None:
            return [f"se esperaba {expectativa['error'].__name__} y no hubo error"]
        if not isinstance(error, expectativa["error"]):
            problemas.append(
                f"error={type(error).__name__} (esperado {expectativa['error'].__name__})"
            )
        parciales = sorted(getattr(error, "pasos", {}) or {})
        esperados = sorted(expectativa.get("parciales_esperados", []))
        if parciales != esperados:
            problemas.append(
                f"parciales={parciales} (esperado {esperados})"
            )
        return problemas

    if error is not None:
        return [f"error inesperado: {type(error).__name__}: {error}"]

    for clave, atributo in (
        ("centro", "centro_costo"),
        ("macro", "macro_categoria"),
        ("concepto", "concepto"),
        ("codigo", "codigo"),
        ("condicion_impositiva", "condicion_impositiva"),
        ("requiere_revision_humana", "requiere_revision_humana"),
    ):
        if clave in expectativa and getattr(resultado, atributo) != expectativa[clave]:
            problemas.append(
                f"{atributo}={getattr(resultado, atributo)!r} "
                f"(esperado {expectativa[clave]!r})"
            )

    if "senal_usada" in expectativa:
        opcion = resultado.opciones_centro_costo[0] if resultado.opciones_centro_costo else None
        real = opcion.senal_usada if opcion else None
        if real != expectativa["senal_usada"]:
            problemas.append(f"senal_usada={real!r} (esperado {expectativa['senal_usada']!r})")
    if "confianza_centro" in expectativa:
        opcion = resultado.opciones_centro_costo[0] if resultado.opciones_centro_costo else None
        real = opcion.confianza if opcion else None
        if real != expectativa["confianza_centro"]:
            problemas.append(
                f"confianza_centro={real!r} (esperado {expectativa['confianza_centro']!r})"
            )

    if "llamadas" in expectativa:
        reales = [llamada["paso"] for llamada in llamadas]
        if reales != expectativa["llamadas"]:
            problemas.append(f"llamadas={reales} (esperado {expectativa['llamadas']})")

    if "placeholder_en_paso_03" in expectativa:
        if len(llamadas) >= 3:
            user_03 = llamadas[2]["messages"][1]["content"]
            if expectativa["placeholder_en_paso_03"] not in user_03:
                problemas.append(
                    f"el user del paso 03 no contiene "
                    f"{expectativa['placeholder_en_paso_03']!r}"
                )
    return problemas


def _imprimir_escenario(
    escenario: dict[str, Any],
    resultado,
    error,
    llamadas: list[dict[str, Any]],
    problemas: list[str],
    *,
    detalle: bool,
) -> None:
    """Imprime una línea por escenario (y el detalle si se pidió)."""
    estado = "✅" if not problemas else "❌"
    pasos = ",".join(llamada["paso"] for llamada in llamadas) or "-"
    if error is not None:
        print(
            f"  {estado} {escenario['nombre']:<28} error={type(error).__name__:<22} "
            f"parciales={sorted(getattr(error, 'pasos', {}) or {})}"
        )
    else:
        codigo = resultado.codigo if resultado.codigo is not None else "-"
        print(
            f"  {estado} {escenario['nombre']:<28} "
            f"{resultado.centro_costo} → {resultado.macro_categoria} → "
            f"{resultado.concepto} → {codigo}  "
            f"(cond={resultado.condicion_impositiva}, llamadas={pasos})"
        )
        if resultado.requiere_revision_humana:
            print("      ⚠ el paso 03 pidió revisión humana (celda sin código o con alternativas)")
    if problemas:
        print(f"      ❌ no coincide con la expectativa: {'; '.join(problemas)}")
    if detalle and resultado is not None:
        print(f"      versiones: {resultado.detalle.get('versiones_prompt', {})}")
        print(f"      cuenta: {resultado.detalle.get('cuenta_contable', '-')} "
              f"| confianza: {resultado.detalle.get('confianza', '-')}")


def _correr_escenario(escenario: dict[str, Any], tmpdir: Path):
    """Corre un escenario: cadena real (doble) + variante pura cuando aplica."""
    lector = LectorDoble(escenario.get("respuestas"), escenario.get("contenido_crudo"))
    documento = tmpdir / f"{escenario['nombre']}.md"
    kwargs: dict[str, Any] = {
        "descripcion": "Compra de repuestos para el taller",
        "condicion_impositiva": escenario.get("condicion", CONDICION_IMPOSITIVA_DEFAULT),
        "documento": documento,
    }
    resultado = None
    error = None
    try:
        resultado = ejecutar_cadena(lector, **kwargs)
    except ErrorCadenaContable as exc:
        error = exc

    # Reanudación: una segunda corrida sobre el checkpoint no debe llamar al
    # modelo para los pasos ya resueltos.
    if escenario.get("reanudar") and error is None:
        segundo = LectorDoble(escenario.get("respuestas"), escenario.get("contenido_crudo"))
        resultado = ejecutar_cadena(segundo, **kwargs)
        llamadas = segundo.llamadas
    else:
        llamadas = lector.llamadas

    return resultado, error, llamadas, documento


def _correr_origen(origen: Path, detalle: bool) -> dict[str, Any]:
    """Corre la cadena real contra Ollama sobre un markdown (T-304)."""
    print(f"Markdown real: {origen} (cadena contable 01→02→03 con Ollama)")
    print("=" * 78)
    reporte: dict[str, Any] = {"origen": str(origen)}
    try:
        from voucherflow.models.ollama import OllamaClient

        texto = origen.read_text(encoding="utf-8")
        resultado = ejecutar_cadena(
            OllamaClient(), descripcion=texto, documento=origen
        )
        print(
            f"  {resultado.centro_costo} → {resultado.macro_categoria} → "
            f"{resultado.concepto} → {resultado.codigo or '-'}"
        )
        print(f"  condición: {resultado.condicion_impositiva}")
        if resultado.requiere_revision_humana:
            print("  ⚠ requiere revisión humana")
        if detalle:
            print(f"  detalle: {json.dumps(resultado.detalle, ensure_ascii=False)}")
        print(f"  checkpoint: {ruta_checkpoint(origen)}")
        reporte["resultado"] = {
            "clasificacion": resultado.como_clasificacion(),
            "requiere_revision_humana": resultado.requiere_revision_humana,
            "detalle": resultado.detalle,
        }
    except Exception as exc:  # noqa: BLE001 - herramienta manual: se reporta todo
        print(f"  ❌ {type(exc).__name__}: {exc}", file=sys.stderr)
        reporte["error"] = f"{type(exc).__name__}: {exc}"
    print()
    return reporte


def main() -> None:
    parser = argparse.ArgumentParser(
        description=(
            "Inspecciona la cadena contable 01→02→03 (F3 / T-304): contratos por "
            "paso, default CC0006, checkpoints y la variante pura. Los escenarios "
            "sintéticos no requieren Ollama."
        )
    )
    parser.add_argument(
        "--caso",
        action="append",
        help="Filtra por nombre de escenario (repetible). Default: todos.",
    )
    parser.add_argument("--prompts", action="store_true", help="Imprime los prompts versionados.")
    parser.add_argument("--detalle", action="store_true", help="Traza por escenario.")
    parser.add_argument("--json", type=Path, help="Escribe el reporte completo en JSON.")
    parser.add_argument(
        "--origen",
        type=Path,
        help="Markdown para correr la cadena REAL contra Ollama (requiere el servicio).",
    )
    args = parser.parse_args()

    if args.prompts:
        _imprimir_prompts()

    reporte: dict[str, Any] = {
        "tarea": "T-304",
        "fase": "F3",
        "versiones_prompt": {
            paso: definicion["version"] for paso, definicion in PASOS_CONTABLES.items()
        },
        "reglas_cadena": list(REGLAS_CADENA_CONTABLE),
        "escenarios": [],
    }
    fallos = 0

    if args.origen is not None:
        if not args.origen.exists():
            print(f"No existe el markdown: {args.origen}", file=sys.stderr)
            sys.exit(2)
        reporte["origen"] = _correr_origen(args.origen, args.detalle)
        if reporte["origen"].get("error"):
            fallos += 1

    seleccionados = [
        escenario
        for escenario in ESCENARIOS
        if not args.caso or escenario["nombre"] in set(args.caso)
    ]
    if args.caso and not seleccionados:
        print(
            f"Ningún escenario coincide con {args.caso}. "
            f"Disponibles: {', '.join(e['nombre'] for e in ESCENARIOS)}",
            file=sys.stderr,
        )
        sys.exit(2)

    if seleccionados:
        print(f"Cadena contable 01→02→03 (prompts T-304, condición default "
              f"{CONDICION_IMPOSITIVA_DEFAULT})")
        print("=" * 78)
        with tempfile.TemporaryDirectory() as tmp:
            tmpdir = Path(tmp)
            for escenario in seleccionados:
                resultado, error, llamadas, documento = _correr_escenario(escenario, tmpdir)
                problemas = _verificar(resultado, error, llamadas, escenario["expectativa"])
                fallos += int(bool(problemas))
                _imprimir_escenario(
                    escenario, resultado, error, llamadas, problemas, detalle=args.detalle
                )
                entrada: dict[str, Any] = {
                    "nombre": escenario["nombre"],
                    "coincide": not problemas,
                    "diferencias": problemas,
                    "llamadas": [llamada["paso"] for llamada in llamadas],
                    "modelo": llamadas[0]["model"] if llamadas else None,
                }
                if resultado is not None:
                    entrada["resultado"] = {
                        "clasificacion": resultado.como_clasificacion(),
                        "requiere_revision_humana": resultado.requiere_revision_humana,
                        "detalle": resultado.detalle,
                    }
                if error is not None:
                    entrada["error"] = {
                        "tipo": type(error).__name__,
                        "mensaje": str(error),
                        "parciales": sorted((getattr(error, "pasos", {}) or {})),
                    }
                if escenario.get("reanudar"):
                    entrada["checkpoint"] = str(documento)
                    entrada["checkpoint_pasos"] = sorted(leer_checkpoint(ruta_checkpoint(documento)))
                reporte["escenarios"].append(entrada)
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

    if fallos:
        sys.exit(1)


if __name__ == "__main__":
    main()

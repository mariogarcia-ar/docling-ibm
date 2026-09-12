#!/usr/bin/env python
"""Inspecciona T-603 (F6) — sidecars con trazabilidad y salida agregada.

**Fase**: F6 (cliente) · **Tarea**: T-603 · **Épica**: E-CLI-2.

Verifica el Gherkin de E-CLI-2, que tiene **dos mitades**:

    Dado un documento procesado por el CLI
    Cuando se guarda el resultado
    Entonces se genera un JSON con resultado + evidencia + trazabilidad (sidecar)
    Y en modo lote se puede consolidar en un único JSON agregado

  1. La **mitad del sidecar** (F5/T-506 + T-601): se genera el JSON por documento
     con resultado + evidencia + trazabilidad, y se lee de vuelta.
  2. La **mitad del agregado** (T-603): un único JSON consolidado con una entrada
     por documento (veredicto + puntero al sidecar), la síntesis del lote y las
     métricas.
  3. Que el agregado **no duplique la evidencia** (es un índice, no una copia de
     los `CaseRecord`), **acumule** entre corridas y **no degrade** una entrada
     que ya venía del histórico.
  4. La **honestidad de las métricas**: se derivan del histórico persistido
     (F5/T-507) o el agregado **declara** que no hay de dónde sacarlas.
  5. Las **fronteras**: un agregado ilegible **no** se sobreescribe, un documento
     sin veredicto no se cuenta como `revision`, y el agregado no corre el
     pipeline (es una proyección).

Uso:
    python scripts/F6/t603.py                    # escenarios + fronteras
    python scripts/F6/t603.py --manual           # un lote chico, paso a paso
    python scripts/F6/t603.py --json /tmp/t603.json

Todo corre **sin** Ollama, **sin** Docling y **sin** red (dobles inyectados por
`EntornoCLI`); el agregado se escribe siempre en un directorio temporal. La suite
default cubre lo mismo en ``tests/test_agregado_t603.py``.
"""

from __future__ import annotations

import argparse
import io
import json
import sys
import tempfile
from pathlib import Path
from typing import Any

# El paquete usa layout src/; si se corre sin instalar, se agrega src/ al sys.path.
_SRC = Path(__file__).resolve().parents[2] / "src"
if str(_SRC) not in sys.path:
    sys.path.insert(0, str(_SRC))

from voucherflow.cli.main import (  # noqa: E402
    VERSION_CLI,
    EntornoCLI,
    main,
)
from voucherflow.orchestrator import (  # noqa: E402
    PipelineOrchestrator,
    PipelineResult,
    identificador_de_archivo,
)
from voucherflow.trace.agregado import (  # noqa: E402
    NO_AGREGADOS,
    VERSION_AGREGADO,
    agregado_del_recorder,
    agregar_a_archivo,
    construir_agregado,
    entrada_de_resultado,
    escribir_agregado,
    leer_agregado,
)
from voucherflow.trace.construccion import construir_case_record  # noqa: E402
from voucherflow.trace.recorder import VERSION_TRAZA, CaseRecorder  # noqa: E402
from voucherflow.settings.config import cargar_desde_dict  # noqa: E402

# ---------------------------------------------------------------------------
# Dobles
# ---------------------------------------------------------------------------

CAMPOS = {
    "tipo_comprobante": {"valor": "A", "fragmento_sustento": "FACTURA A"},
    "cuit_emisor": {"valor": "30-12345678-9", "fragmento_sustento": "CUIT 30-12345678-9"},
    "cuit_receptor": {"valor": "27-30111222-4", "fragmento_sustento": "CUIT 27-30111222-4"},
    "fecha_emision": {"valor": "14/08/2025", "fragmento_sustento": "Fecha: 14/08/2025"},
    "nro_comprobante": {"valor": "0001-00000001", "fragmento_sustento": "Nro: 0001-00000001"},
    "importe_total_facturado": {"valor": "121,00", "fragmento_sustento": "Total: 121,00"},
}


class DobleLector:
    """Doble del ``OllamaClient``: responde por system prompt de cada etapa."""

    def __init__(self, *, gate: str = "comprobante") -> None:
        self.gate = gate

    def _etapa(self, sistema: str) -> str:
        from voucherflow.classification.prompt_tipo_comprobante import (
            SYSTEM_PROMPT_TIPO_COMPROBANTE_LLM,
            SYSTEM_PROMPT_TIPO_COMPROBANTE_VLM,
        )
        from voucherflow.extraction.prompt_extraccion import (
            SYSTEM_PROMPT_EXTRACCION_LLM,
            SYSTEM_PROMPT_EXTRACCION_VLM,
        )
        from voucherflow.validation.prompt_qween import SYSTEM_PROMPT_QWEEN

        if sistema == SYSTEM_PROMPT_QWEEN:
            return "gate"
        if sistema in (SYSTEM_PROMPT_TIPO_COMPROBANTE_VLM, SYSTEM_PROMPT_TIPO_COMPROBANTE_LLM):
            return "tipo"
        if sistema in (SYSTEM_PROMPT_EXTRACCION_VLM, SYSTEM_PROMPT_EXTRACCION_LLM):
            return "extraccion"
        return "otro"

    def ask(self, messages, model, json_format=False, options=None, num_ctx=None):
        from voucherflow.models.ollama import RespuestaOllama

        sistema = messages[0].get("content", "") if messages else ""
        etapa = self._etapa(sistema)
        if etapa == "gate":
            contenido = self.gate
        elif etapa == "extraccion":
            contenido = json.dumps({"fuente_lectura": "llm", "campos": CAMPOS})
        else:
            contenido = json.dumps(
                {
                    "fuente_lectura": "vlm",
                    "tipo_detectado_por_documento": "A",
                    "tipo_detectado_por_documento_explicacion": "FACTURA A",
                    "candidatos_restantes": [],
                }
            )
        return RespuestaOllama(contenido=contenido, modelo=model, latencia_s=0.0, status=200)


class DobleConverter:
    """Doble del ``DoclingConverter``: documento de texto nativo."""

    def convert(self, origen: str) -> Any:
        from voucherflow.models.docling import ProcessedDocument

        return ProcessedDocument(
            tipo_entrada="texto", ruta=str(origen), markdown="FACTURA A\n", boxes=[], n_items=1
        )


def _settings() -> Any:
    return cargar_desde_dict(
        {
            "modelos": {
                "vlm": {"rol": "vlm", "modelo": "modelo-vlm-test"},
                "llm": {"rol": "llm", "modelo": "modelo-llm-test"},
                "agente": {"rol": "agente", "modelo": "modelo-agente-test"},
            }
        }
    )


def _orquestador(*, gate: str = "comprobante") -> PipelineOrchestrator:
    return PipelineOrchestrator(
        cliente=DobleLector(gate=gate), converter=DobleConverter(), settings=_settings()
    )


def _entorno(tmp: Path) -> EntornoCLI:
    return EntornoCLI(
        orquestador=_orquestador(), stdout=io.StringIO(), stderr=io.StringIO(), cwd=tmp
    )


def _carpeta(tmp: Path, cantidad: int, *, desde: int = 0) -> list[Path]:
    rutas = []
    for indice in range(desde, desde + cantidad):
        ruta = tmp / f"doc-{indice:02d}.md"
        ruta.write_text(f"FACTURA A\ncomprobante {indice}\n", encoding="utf-8")
        rutas.append(ruta)
    return rutas


def _evidencia_vacia(documento_id: str):
    from voucherflow.schemas.evidence import CombinedEvidence

    return CombinedEvidence(documento_id=documento_id, campos={})


# ---------------------------------------------------------------------------
# Escenarios
# ---------------------------------------------------------------------------


def _escenarios() -> list[dict[str, Any]]:
    escenarios: list[dict[str, Any]] = []

    with tempfile.TemporaryDirectory() as tmp:
        tmp_path = Path(tmp)
        cases = tmp_path / "cases"
        agregado_path = tmp_path / "lote.json"

        # 1. La mitad del sidecar: resultado + evidencia + trazabilidad.
        _carpeta(tmp_path, 2)
        entorno = _entorno(tmp_path)
        codigo = main(
            [
                "run",
                str(tmp_path / "doc-00.md"),
                "--cases",
                str(cases),
            ],
            entorno=entorno,
        )
        sidecars = list(cases.glob("*.case.json"))
        caso = json.loads(sidecars[0].read_text(encoding="utf-8")) if sidecars else {}
        escenarios.append(
            {
                "nombre": "sidecar_por_documento",
                "que": "cada resultado deja un JSON con resultado + evidencia + trazabilidad",
                "ok": codigo == 0
                and len(sidecars) == 1
                and caso.get("resultado", {}).get("estado") == "aprobado"
                and bool(caso.get("evidencia_por_fuente")),
                "esperado": "1 sidecar con resultado, evidencia y trazabilidad",
                "obtenido": (
                    f"{len(sidecars)} sidecar(s), "
                    f"fuentes={len(caso.get('evidencia_por_fuente', {}))}"
                ),
            }
        )

        # 2. El sidecar vuelve al contrato sin pérdida.
        recorder = CaseRecorder(cases)
        documento_id = next(iter(recorder.buscar(estado="aprobado")))["documento_id"]
        releido = recorder.leer(documento_id)
        escenarios.append(
            {
                "nombre": "sidecar_se_lee_de_vuelta",
                "que": "el sidecar es reconstruible desde el contrato de F0",
                "ok": releido.documento_id == documento_id
                and releido.resultado is not None,
                "esperado": "round-trip sin pérdida",
                "obtenido": f"{releido.documento_id[:18]}…",
            }
        )

        # 3. El agregado: un único JSON con una entrada por documento.
        _carpeta(tmp_path, 3, desde=10)
        entorno2 = _entorno(tmp_path)
        codigo2 = main(["batch", str(tmp_path), "-o", str(agregado_path)], entorno=entorno2)
        datos = json.loads(agregado_path.read_text(encoding="utf-8"))
        escenarios.append(
            {
                "nombre": "agregado_unico",
                "que": "el lote se consolida en UN JSON con una entrada por documento",
                "ok": codigo2 == 0
                and datos["version"] == VERSION_AGREGADO
                and datos["resumen"]["documentos"] == len(datos["documentos"]) == 5,
                "esperado": "1 archivo, 5 documentos, 5 entradas",
                "obtenido": (
                    f"{datos['version']}, {datos['resumen']['documentos']} documentos, "
                    f"{len(datos['documentos'])} entradas"
                ),
            }
        )

        # 4. La entrada apunta al sidecar (el agregado es un índice).
        con_sidecar = [d for d in datos["documentos"] if d.get("sidecar")]
        escenarios.append(
            {
                "nombre": "la_entrada_apunta_al_sidecar",
                "que": "cada entrada dice dónde está la evidencia completa",
                "ok": bool(con_sidecar) and all(
                    d["sidecar"].endswith(".case.json") for d in con_sidecar
                ),
                "esperado": "punteros <doc>.case.json",
                "obtenido": f"{len(con_sidecar)} de {len(datos['documentos'])} con puntero",
            }
        )

        # 5. El agregado NO duplica la evidencia.
        serializado = json.dumps(datos["documentos"])
        escenarios.append(
            {
                "nombre": "no_duplica_la_evidencia",
                "que": "el agregado es un índice: no copia los CaseRecord",
                "ok": NO_AGREGADOS[0] not in serializado
                and "fragmento_sustento" not in serializado,
                "esperado": "sin evidencia embebida",
                "obtenido": f"{len(serializado)} bytes de entradas",
            }
        )

        # 6. Se acumula entre corridas (el archivo es el estado de la carpeta).
        _carpeta(tmp_path, 2, desde=20)
        main(["batch", str(tmp_path), "-o", str(agregado_path)], entorno=_entorno(tmp_path))
        final = json.loads(agregado_path.read_text(encoding="utf-8"))
        escenarios.append(
            {
                "nombre": "acumula_entre_corridas",
                "que": "cada corrida suma; el agregado es el estado de la carpeta",
                "ok": final["resumen"]["documentos"] == 7,
                "esperado": "7 documentos tras la 2ª corrida",
                "obtenido": f"{final['resumen']['documentos']} documentos",
            }
        )

        # 7. Las métricas se derivan del histórico o se declaran.
        sin_cases = construir_agregado(resultados=[]).como_dict()
        entorno3 = _entorno(tmp_path)
        main(["case", "aggregate", "--dir", str(cases)], entorno=entorno3)
        con_cases = json.loads(entorno3.stdout.getvalue())
        escenarios.append(
            {
                "nombre": "metricas_honestas",
                "que": "con histórico las métricas se calculan; sin él, el agregado lo declara",
                "ok": sin_cases["metricas"] is None
                and bool(sin_cases["metricas_no_disponibles"])
                and con_cases["metricas"] is not None,
                "esperado": "null + motivo sin histórico; métricas con histórico",
                "obtenido": (
                    f"sin histórico: {sin_cases['metricas']} | "
                    f"con histórico: {'calculadas' if con_cases['metricas'] else 'no'}"
                ),
            }
        )

        # 8. El agregado se reconstruye del histórico, sin correr el pipeline.
        reconstruido = agregado_del_recorder(CaseRecorder(cases))
        escenarios.append(
            {
                "nombre": "reconstruccion_del_historico",
                "que": "`case aggregate` arma el agregado leyendo los sidecars",
                "ok": reconstruido.total == 1
                and reconstruido.documentos[0].sidecar is not None,
                "esperado": "1 documento reconstruido con su puntero",
                "obtenido": f"{reconstruido.total} documento(s)",
            }
        )

    return escenarios


# ---------------------------------------------------------------------------
# Fronteras
# ---------------------------------------------------------------------------


def _fronteras() -> list[dict[str, Any]]:
    fronteras: list[dict[str, Any]] = []

    with tempfile.TemporaryDirectory() as tmp:
        tmp_path = Path(tmp)

        # Un agregado ilegible NO se sobreescribe (se perdería el lote anterior).
        roto = tmp_path / "roto.json"
        roto.write_text("{no es json", encoding="utf-8")
        se_protegio = False
        try:
            agregar_a_archivo(roto, resultados=[])
        except ValueError:
            se_protegio = True
        fronteras.append(
            {
                "que": "un agregado ilegible no se sobreescribe (no se pierde el lote)",
                "ok": se_protegio and roto.read_text(encoding="utf-8") == "{no es json",
                "detalle": "ValueError y el archivo intacto",
            }
        )

        # Un archivo que no es un agregado tampoco se pisa.
        ajeno = tmp_path / "ajeno.json"
        ajeno.write_text(json.dumps({"otra": "cosa"}), encoding="utf-8")
        se_protegio = False
        try:
            agregar_a_archivo(ajeno, resultados=[])
        except ValueError:
            se_protegio = True
        fronteras.append(
            {
                "que": "un JSON ajeno no se confunde con un agregado",
                "ok": se_protegio,
                "detalle": "ValueError por falta de 'documentos'",
            }
        )

        # Un documento sin veredicto no se cuenta como revisión.
        sin_estado = construir_agregado(
            resultados=[
                PipelineResult(documento_id="x", archivo="/x/y.jpg", ok=False, error="ilegible")
            ]
        )
        fronteras.append(
            {
                "que": "un documento sin veredicto se cuenta como sin_estado, no como revisión",
                "ok": sin_estado.por_estado() == {"sin_estado": 1}
                and sin_estado.resumen()["errores"] == 1,
                "detalle": f"por_estado={sin_estado.por_estado()}",
            }
        )

        # El agregado es una proyección: no corre el pipeline.
        sin_documentos = construir_agregado(
            resultados=[
                PipelineResult(
                    documento_id="a",
                    archivo="/no/existe/a.jpg",
                    ok=True,
                    resumen={"estado": "aprobado"},
                )
            ]
        )
        fronteras.append(
            {
                "que": "el agregado es una proyección: no necesita documentos ni modelos",
                "ok": sin_documentos.total == 1,
                "detalle": "1 entrada desde un resultado sintético",
            }
        )

        # El histórico manda sobre la proyección de la corrida.
        caso = construir_case_record(_evidencia_vacia("a"), archivo="/x/a.jpg")
        mixto = construir_agregado(
            casos=[caso],
            resultados=[
                PipelineResult(
                    documento_id="a", archivo="/x/a.jpg", ok=True,
                    resumen={"estado": "rechazado"}, detalle={},
                )
            ],
        )
        fronteras.append(
            {
                "que": "el CaseRecord es el dato durable: no se pisa con la corrida",
                "ok": mixto.total == 1 and mixto.documentos[0].sidecar is not None,
                "detalle": "1 entrada, con puntero al sidecar",
            }
        )

        # Una entrada sin sidecar no inventa el puntero.
        sin_puntero = entrada_de_resultado(
            PipelineResult(documento_id="a", archivo="/x/a.jpg", ok=True, resumen={})
        )
        fronteras.append(
            {
                "que": "no se inventa un puntero a un sidecar que no existe",
                "ok": "sidecar" not in sin_puntero.como_dict(),
                "detalle": "la clave se omite, no se pone en null",
            }
        )

        # El contrato dice dónde buscar la evidencia.
        contrato = construir_agregado(resultados=[]).como_dict()["contrato"]
        fronteras.append(
            {
                "que": "el contrato declara dónde está la evidencia completa",
                "ok": contrato["version_traza"] == VERSION_TRAZA
                and "case show" in contrato["nota"],
                "detalle": f"version_traza={contrato['version_traza']}",
            }
        )

        # El agregado se escribe de forma atómica.
        destino = tmp_path / "atomico.json"
        escribir_agregado(destino, construir_agregado(resultados=[]))
        fronteras.append(
            {
                "que": "la escritura del agregado es atómica (sin temporales)",
                "ok": not list(tmp_path.glob(".*.tmp")),
                "detalle": "sin archivos .tmp",
            }
        )

        # Un documento cambiado actualiza su entrada (no agrega otra).
        destino2 = tmp_path / "acumula.json"
        _resultado = lambda estado: PipelineResult(  # noqa: E731
            documento_id="a", archivo="/x/a.jpg", ok=True, resumen={"estado": estado}
        )
        agregar_a_archivo(destino2, resultados=[_resultado("revision")])
        agregar_a_archivo(destino2, resultados=[_resultado("aprobado")])
        final = leer_agregado(destino2)
        fronteras.append(
            {
                "que": "reprocesar un documento actualiza su entrada (no la duplica)",
                "ok": final.total == 1 and final.documentos[0].estado == "aprobado",
                "detalle": f"{final.total} entrada(s), estado={final.documentos[0].estado}",
            }
        )

    return fronteras


def main_cli() -> None:  # noqa: D401
    """Corre la inspección y sale con código ≠ 0 si algún chequeo falla."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--manual", action="store_true", help="Un lote chico, paso a paso.")
    parser.add_argument("--json", type=Path, default=None, help="Guardar el reporte JSON.")
    args = parser.parse_args()

    print(f"T-603 — sidecars y salida agregada ({VERSION_AGREGADO}, {VERSION_CLI})")
    reporte: dict[str, Any] = {"version_agregado": VERSION_AGREGADO, "version_cli": VERSION_CLI}
    fallos = 0

    print("\nEscenarios")
    reporte["escenarios"] = _escenarios()
    for escenario in reporte["escenarios"]:
        marca = "✅" if escenario["ok"] else "❌"
        print(f"  {marca} {escenario['nombre']:<28} {escenario['que']}")
        print(f"       esperado: {escenario['esperado']}")
        print(f"       obtenido: {escenario['obtenido']}")
        if not escenario["ok"]:
            fallos += 1

    print("\nFronteras de T-603 (lo que NO hace)")
    reporte["fronteras"] = _fronteras()
    for frontera in reporte["fronteras"]:
        marca = "✅" if frontera["ok"] else "❌"
        print(f"  {marca} {frontera['que']}")
        print(f"       {frontera['detalle']}")
        if not frontera["ok"]:
            fallos += 1

    if args.manual:
        print("\nUn lote chico, paso a paso")
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            cases = tmp_path / "cases"
            agregado_path = tmp_path / "lote.json"
            _carpeta(tmp_path, 3)
            entorno = _entorno(tmp_path)
            main(
                [
                    "batch",
                    str(tmp_path),
                    "--cases",
                    str(cases),
                    "-o",
                    str(agregado_path),
                ],
                entorno=entorno,
            )
            datos = json.loads(agregado_path.read_text(encoding="utf-8"))
            resumen = datos["resumen"]
            print(f"  documentos: {resumen['documentos']} | ok: {resumen['ok']} | por estado: {resumen['por_estado']}")
            print(f"  con puntero al sidecar: {resumen['con_sidecar']}")
            print(f"  métricas: {'sí' if datos['metricas'] else 'no'}")
            print("  entradas del agregado:")
            for entrada in datos["documentos"]:
                estado = entrada.get("estado", "sin_estado")
                letra = entrada.get("tipo_comprobante") or "-"
                puntero = entrada.get("sidecar", "(sin sidecar)")
                print(f"    - {estado:<10} letra={letra:<3} → {puntero}")

    escenarios_ok = sum(1 for e in reporte["escenarios"] if e["ok"])
    fronteras_ok = sum(1 for f in reporte["fronteras"] if f["ok"])
    print(f"\nEscenarios verificados: {escenarios_ok}/{len(reporte['escenarios'])}")
    print(f"Fronteras verificadas: {fronteras_ok}/{len(reporte['fronteras'])}")

    reporte["fallos"] = fallos
    if args.json:
        args.json.write_text(json.dumps(reporte, ensure_ascii=False, indent=2), encoding="utf-8")
        print(f"Reporte guardado en {args.json}")
    sys.exit(1 if fallos else 0)


if __name__ == "__main__":
    main_cli()

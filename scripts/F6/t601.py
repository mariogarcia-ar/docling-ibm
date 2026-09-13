#!/usr/bin/env python
"""Inspecciona T-601 (F6) — CLI ``voucherflow`` + orquestador + fachada.

**Fase**: F6 (cliente) · **Tarea**: T-601 · **Épica**: E-CLI-1/E-CLI-2 ·
**ADR-007**.

Verifica, **sin Ollama, sin Docling y sin red** (el lector de modelos, el
convertidor de F1 y el agente de la conclusión se inyectan con dobles):

  1. El **contrato del CLI**: los once subcomandos del DoD, los cinco flags
     comunes y el código de salida de cada uno (``main`` devuelve el código, no
     llama a ``sys.exit``).
  2. El **orquestador**: la secuencia completa processing → validation →
     extraction → combinación → conclusión → traza, con el ``VoucherResult`` y
     el ``CaseRecord``.
  3. El **fast-fail del gate**: un documento que no es comprobante se resuelve
     ``rechazado`` con certeza alta (el código concluyó que no) y **sin** gastar
     extracción ni caché de modelos.
  4. Los **subcomandos sobre el filesystem**: ``process`` escribe el markdown,
     ``run``/``batch`` producen el JSON y ``batch`` recorre una carpeta
     **recursiva** excluyendo los artefactos derivados (sidecars, checkpoints).
  5. La **fachada**: ``api.run`` → ``VoucherResult`` y ``api.extract`` →
     ``CombinedEvidence`` (los dos esqueletos de F0 que T-601 cierra).
  6. Las **fronteras** de la tarea: ``--workers`` se declara como solicitado (el
     pool es T-602), ``--force`` como no-op explícito, ``--orientation`` en texto
     nativo declara que no aplica, un documento ilegible devuelve ``ok=False`` y
     ``arca check`` sin configuración reporta *no disponible* (ADR-003).

Uso:
    python scripts/F6/t601.py                      # contrato + escenarios + fronteras
    python scripts/F6/t601.py --contrato           # solo el contrato del CLI
    python scripts/F6/t601.py --manual             # un caso paso a paso
    python scripts/F6/t601.py --json /tmp/t601.json

Nota: la suite default cubre lo mismo en ``tests/test_cli_t601.py``; este script
es la verificación de humo legible para la bitácora y sale con código ≠ 0 si algo
falla (mismo patrón que ``scripts/F3/t301.py`` y ``scripts/F5/t5NN.py``).
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

from voucherflow.api import extract as api_extract  # noqa: E402
from voucherflow.api import run as api_run  # noqa: E402
from voucherflow.cli.main import (  # noqa: E402
    COMANDOS,
    DESPACHO,
    VERSION_CLI,
    EntornoCLI,
    construir_parser,
    main,
)
from voucherflow.models.docling import Box, ProcessedDocument  # noqa: E402
from voucherflow.models.ollama import RespuestaOllama  # noqa: E402
from voucherflow.orchestrator import (  # noqa: E402
    ETAPA_CONCLUSION,
    ETAPA_EXTRACTION,
    ETAPA_PROCESSING,
    ETAPA_TRAZABILIDAD,
    ETAPA_VALIDATION,
    VERSION_ORQUESTADOR,
    PipelineOrchestrator,
    identificador_de_archivo,
    iterar_documentos,
)
from voucherflow.schemas.result import VoucherResult  # noqa: E402
from voucherflow.settings.config import cargar_desde_dict  # noqa: E402

# ---------------------------------------------------------------------------
# Dobles (sin red: el mismo criterio que la suite default)
# ---------------------------------------------------------------------------

PASO_01 = {"centros_costos": [{"codigo_centro_costo": "CC0004", "centro": "Repuestos", "confianza": "alta"}]}
PASO_02 = {"macro_categorias": [{"macro_categoria": "MC07", "nombre": "Operaciones", "confianza": "alta"}]}
PASO_03 = {
    "macro_categoria": "MC07",
    "concepto": "CT017",
    "nombre_concepto": "Materiales",
    "cuenta_contable": "4221,16",
    "condicion_impositiva": "21",
    "codigo_final": "48",
    "candidatos_codigo_final": [],
    "requiere_revision_humana": False,
    "confianza": "alta",
}

CAMPOS_FACTURA_A = {
    "tipo_comprobante": "A",
    "cuit_emisor": "30-12345678-9",
    "cuit_receptor": "27-30111222-4",
    "fecha_emision": "14/08/2025",
    "nro_comprobante": "0001-00000001",
    "importe_total_facturado": "121,00",
}


class DobleLector:
    """Doble del ``OllamaClient`` que responde por system prompt de cada etapa."""

    def __init__(self, *, gate: str = "comprobante", campos: dict[str, str] | None = None) -> None:
        self.gate = gate
        self.campos = campos or CAMPOS_FACTURA_A
        self.llamadas: list[str] = []

    def _etapa(self, sistema: str) -> str:
        from voucherflow.classification.prompt_tipo_comprobante import (
            SYSTEM_PROMPT_TIPO_COMPROBANTE_LLM,
            SYSTEM_PROMPT_TIPO_COMPROBANTE_VLM,
        )
        from voucherflow.classification.prompts_contable import PASOS_CONTABLES
        from voucherflow.extraction.prompt_extraccion import (
            SYSTEM_PROMPT_EXTRACCION_LLM,
            SYSTEM_PROMPT_EXTRACCION_VLM,
        )
        from voucherflow.validation.prompt_qween import SYSTEM_PROMPT_QWEEN

        if sistema.startswith("Sos un asistente que responde preguntas"):
            return "ask"
        if sistema == SYSTEM_PROMPT_QWEEN:
            return "gate"
        if sistema in (SYSTEM_PROMPT_TIPO_COMPROBANTE_VLM, SYSTEM_PROMPT_TIPO_COMPROBANTE_LLM):
            return "tipo"
        if sistema in (SYSTEM_PROMPT_EXTRACCION_VLM, SYSTEM_PROMPT_EXTRACCION_LLM):
            return "extraccion"
        for paso, definicion in PASOS_CONTABLES.items():
            if definicion["system"] == sistema:
                return f"contable_{paso}"
        return "desconocido"

    def ask(self, messages, model, json_format=False, options=None, num_ctx=None):
        sistema = messages[0].get("content", "") if messages else ""
        etapa = self._etapa(sistema)
        self.llamadas.append(etapa)
        if etapa == "ask":
            contenido = "El total del comprobante es 121,00."
        elif etapa.startswith("contable_"):
            paso = etapa.split("_", 1)[1]
            contenido = json.dumps({"01": PASO_01, "02": PASO_02, "03": PASO_03}[paso])
        elif etapa == "gate":
            contenido = self.gate
        elif etapa == "extraccion":
            contenido = json.dumps(
                {
                    "fuente_lectura": "llm",
                    "campos": {
                        nombre: {"valor": valor, "fragmento_sustento": f"{nombre}: {valor}"}
                        for nombre, valor in self.campos.items()
                    },
                }
            )
        else:
            contenido = json.dumps(
                {
                    "fuente_lectura": "vlm",
                    "tipo_detectado_por_documento": "A",
                    "tipo_detectado_por_documento_explicacion": "FACTURA A en el recuadro.",
                    "candidatos_restantes": [],
                }
            )
        return RespuestaOllama(contenido=contenido, modelo=model, latencia_s=0.0, status=200)


class DobleConverter:
    """Doble del ``DoclingConverter``: devuelve un ``ProcessedDocument`` armado.

    Devuelve un documento de **texto nativo** (sin ``boxes``): es la ruta que
    ``procesar_documento`` no filtra por el gate de imagen de T-102, así que el
    script ejerce la orquestación sin depender de las dimensiones de un binario
    de prueba. La ausencia de boxes es además la que permite verificar la
    frontera de ``--orientation`` (texto nativo: la orientación no aplica).
    """

    def convert(self, origen: str) -> ProcessedDocument:
        return ProcessedDocument(
            tipo_entrada="texto",
            ruta=str(origen),
            markdown="FACTURA A\nACME SA\nCUIT 30-12345678-9\nTotal: 121,00\n",
            boxes=[],
            n_items=2,
        )


def _settings() -> Any:
    return cargar_desde_dict(
        {
            "modelos": {
                "vlm": {"rol": "vlm", "modelo": "modelo-vlm-test", "num_ctx": 4096},
                "llm": {"rol": "llm", "modelo": "modelo-llm-test", "num_ctx": 8192},
                "agente": {"rol": "agente", "modelo": "modelo-agente-test", "num_ctx": 8192},
            }
        }
    )


def _orquestador(*, gate: str = "comprobante") -> PipelineOrchestrator:
    return PipelineOrchestrator(
        cliente=DobleLector(gate=gate), converter=DobleConverter(), settings=_settings()
    )


def _entorno(tmp: Path, *, gate: str = "comprobante") -> EntornoCLI:
    return EntornoCLI(
        orquestador=_orquestador(gate=gate),
        stdout=io.StringIO(),
        stderr=io.StringIO(),
        cwd=tmp,
    )


def _documento(tmp: Path) -> Path:
    ruta = tmp / "factura.md"
    ruta.write_text("FACTURA A\nACME SA\nTotal: 121,00\n", encoding="utf-8")
    return ruta


# ---------------------------------------------------------------------------
# Reporte
# ---------------------------------------------------------------------------


def _contrato() -> list[dict[str, Any]]:
    """Verifica el contrato del CLI (E-CLI-1): subcomandos y flags comunes."""
    esperados = [
        "process",
        "validate",
        "classify",
        "extract",
        "extract-detect",
        "run",
        "batch",
        "ask",
        "arca",
        "case",
        "hitl",
    ]
    parser = construir_parser()
    args = parser.parse_args(
        [
            "run",
            "x.md",
            "--force",
            "--orientation",
            "vertical",
            "--condicion-impositiva",
            "27",
            "--model",
            "m",
            "--workers",
            "4",
        ]
    )
    chequeos = [
        {"que": "los once subcomandos del DoD", "ok": set(COMANDOS) == set(esperados)},
        {"que": "el despacho cubre exactamente los comandos", "ok": set(DESPACHO) == set(COMANDOS)},
        {"que": "--force", "ok": args.force is True},
        {"que": "--orientation", "ok": args.orientation == "vertical"},
        {"que": "--condicion-impositiva", "ok": args.condicion_impositiva == "27"},
        {"que": "--model", "ok": args.model == "m"},
        {"que": "--workers", "ok": args.workers == 4},
        {"que": "main() devuelve el código (no llama a sys.exit)", "ok": _main_devuelve_codigo()},
    ]
    return chequeos


def _main_devuelve_codigo() -> bool:
    with tempfile.TemporaryDirectory() as tmp:
        entorno = _entorno(Path(tmp))
        return main([], entorno=entorno) == 2


def _escenarios() -> list[dict[str, Any]]:
    """Escenarios de la corrida completa y de los subcomandos (uno por fila)."""
    escenarios: list[dict[str, Any]] = []

    with tempfile.TemporaryDirectory() as tmp:
        tmp_path = Path(tmp)
        doc = _documento(tmp_path)

        # 1. Orquestador: corrida completa.
        resultado = _orquestador().ejecutar(doc)
        escenarios.append(
            {
                "nombre": "orquestador_corrida_completa",
                "que": "processing → validation → extraction → combinación → conclusión → traza",
                "ok": resultado.ok
                and resultado.etapas_completadas
                == [
                    ETAPA_PROCESSING,
                    ETAPA_VALIDATION,
                    ETAPA_EXTRACTION,
                    "combinacion",
                    ETAPA_CONCLUSION,
                    ETAPA_TRAZABILIDAD,
                ],
                "esperado": "6 etapas, resultado consolidado y CaseRecord",
                "obtenido": (
                    f"{len(resultado.etapas_completadas)} etapas, "
                    f"estado={resultado.estado}, caso={'sí' if resultado.caso else 'no'}"
                ),
            }
        )

        # 2. Veredicto del motor (no del modelo).
        escenarios.append(
            {
                "nombre": "veredicto_del_motor",
                "que": "FACTURA A coherente → aprobado, certeza alta, origen programa",
                "ok": (
                    resultado.resultado.tipo_comprobante == "A"
                    and resultado.resultado.certeza.value == "alta"
                    and resultado.resultado.origen.value == "programa"
                ),
                "esperado": "A / alta / programa",
                "obtenido": (
                    f"{resultado.resultado.tipo_comprobante} / "
                    f"{resultado.resultado.certeza.value} / {resultado.resultado.origen.value}"
                ),
            }
        )

        # 3. Fast-fail del gate.
        rechazo = _orquestador(gate="no_comprobante").ejecutar(doc)
        escenarios.append(
            {
                "nombre": "fast_fail_del_gate",
                "que": "no es comprobante → rechazado, certeza alta y SIN extracción",
                "ok": (
                    rechazo.ok
                    and rechazo.resultado.estado.value == "rechazado"
                    and rechazo.resultado.certeza.value == "alta"
                    and ETAPA_EXTRACTION not in rechazo.etapas_completadas
                ),
                "esperado": "rechazado / alta / sin etapa extraction",
                "obtenido": f"{rechazo.estado} / {rechazo.resumen.get('certeza')} / {len(rechazo.etapas_completadas)} etapas",
            }
        )

        # 4. Fachada: api.run.
        voucher = api_run(str(doc), cliente=DobleLector(), converter=DobleConverter(), settings=_settings())
        escenarios.append(
            {
                "nombre": "fachada_run",
                "que": "api.run devuelve el VoucherResult consolidado",
                "ok": isinstance(voucher, VoucherResult) and voucher.estado.value == "aprobado",
                "esperado": "VoucherResult aprobado",
                "obtenido": f"{type(voucher).__name__} {voucher.estado.value}",
            }
        )

        # 5. Fachada: api.extract.
        evidencia = api_extract(str(doc), cliente=DobleLector(), converter=DobleConverter(), settings=_settings())
        escenarios.append(
            {
                "nombre": "fachada_extract",
                "que": "api.extract devuelve la CombinedEvidence (F4/T-404)",
                "ok": bool(evidencia.campos) and evidencia.campos["tipo_comprobante"].llm is not None,
                "esperado": "evidencia combinada con lectura",
                "obtenido": f"{len(evidencia.campos)} campos",
            }
        )

        # 6. Comandos sobre el filesystem.
        entorno = _entorno(tmp_path)
        codigo_process = main(["process", str(doc), "-o", str(tmp_path / "out")], entorno=entorno)
        escenarios.append(
            {
                "nombre": "comando_process",
                "que": "process escribe el markdown de F1",
                "ok": codigo_process == 0 and (tmp_path / "out" / "factura.md").is_file(),
                "esperado": "exit 0 + factura.md",
                "obtenido": f"exit {codigo_process}",
            }
        )

        entorno = _entorno(tmp_path)
        codigo_run = main(["run", str(doc), "--cases", str(tmp_path / "cases")], entorno=entorno)
        datos_run = json.loads(entorno.stdout.getvalue())
        escenarios.append(
            {
                "nombre": "comando_run",
                "que": "run publica el resultado y persiste el CaseRecord",
                "ok": codigo_run == 0 and datos_run["estado"] == "aprobado" and (tmp_path / "cases" / "index.jsonl").is_file(),
                "esperado": "exit 0 + estado aprobado + índice",
                "obtenido": f"exit {codigo_run} / {datos_run['estado']}",
            }
        )

        # 7. Lote recursivo + exclusión de artefactos derivados. Se usa un
        # directorio **limpio**: el ``process`` de arriba escribió un markdown en
        # ``out/`` y contarlo sería medir el ruido del propio arnés, no el lote.
        lote_dir = tmp_path / "lote"
        (lote_dir / "sub").mkdir(parents=True)
        (lote_dir / "uno.md").write_text("uno", encoding="utf-8")
        (lote_dir / "sub" / "otra.md").write_text("otra", encoding="utf-8")
        (lote_dir / "otra.case.json").write_text("{}", encoding="utf-8")
        descubiertos = sorted(p.name for p in iterar_documentos(lote_dir))
        entorno = _entorno(lote_dir)
        agregado = lote_dir / "lote.json"
        codigo_batch = main(
            ["batch", str(lote_dir), "-o", str(agregado)], entorno=entorno
        )
        lote = json.loads(agregado.read_text(encoding="utf-8"))
        escenarios.append(
            {
                "nombre": "comando_batch_recursivo",
                "que": "batch recorre subcarpetas y excluye los sidecars derivados",
                "ok": (
                    codigo_batch == 0
                    and lote["resumen"]["documentos"] == 2
                    and descubiertos == ["otra.md", "uno.md"]
                ),
                "esperado": "2 documentos, sin el sidecar",
                "obtenido": (
                    f"{lote['resumen']['documentos']} documentos, "
                    f"descubiertos={descubiertos}"
                ),
            }
        )

        # 8. Auditoría: case list / show sobre lo persistido.
        entorno = _entorno(tmp_path)
        codigo_case = main(
            ["case", "show", identificador_de_archivo(doc), "--dir", str(tmp_path / "cases")],
            entorno=entorno,
        )
        caso = json.loads(entorno.stdout.getvalue())
        escenarios.append(
            {
                "nombre": "auditoria_case_show",
                "que": "case show reconstruye el CaseRecord desde el sidecar (F5/T-506)",
                "ok": codigo_case == 0 and caso["resultado"]["estado"] == "aprobado",
                "esperado": "exit 0 + caso del documento",
                "obtenido": f"exit {codigo_case} / {caso.get('resultado', {}).get('estado')}",
            }
        )

    return escenarios


def _fronteras() -> list[dict[str, Any]]:
    """Lo que T-601 **no** hace (y lo declara en vez de simularlo)."""
    fronteras: list[dict[str, Any]] = []

    with tempfile.TemporaryDirectory() as tmp:
        tmp_path = Path(tmp)
        doc = _documento(tmp_path)

        # --workers se acepta y se declara. Desde T-602 el lote SÍ usa workers
        # (pool de procesos) cuando no hay orquestador inyectado; acá el entorno
        # del script inyecta dobles, así que corre serial y lo declara en la traza.
        entorno = _entorno(tmp_path)
        agregado_lote = tmp_path / "lote-workers.json"
        main(
            ["batch", str(tmp_path), "--workers", "8", "-o", str(agregado_lote)],
            entorno=entorno,
        )
        lote = json.loads(agregado_lote.read_text(encoding="utf-8"))["lote"]
        fronteras.append(
            {
                "que": (
                    "--workers se registra como solicitado; con dobles el lote corre "
                    "serial y lo declara (el pool real es T-602, ver t602.py)"
                ),
                "ok": lote["max_workers_solicitado"] == 8
                and lote["max_workers_aplicado"] == 1
                and any("serial" in nota for nota in lote["notas"]),
                "detalle": (
                    f"solicitado={lote['max_workers_solicitado']} "
                    f"aplicado={lote['max_workers_aplicado']}"
                ),
            }
        )

        # --force es no-op explícito hasta el checkpoint de T-602.
        entorno = _entorno(tmp_path)
        main(["run", str(doc), "--force"], entorno=entorno)
        datos = json.loads(entorno.stdout.getvalue())
        fronteras.append(
            {
                "que": "--force se declara no-op explícito (checkpoint = T-602)",
                "ok": "T-602" in datos["detalle"].get("force", ""),
                "detalle": datos["detalle"].get("force", "(sin declarar)")[:70],
            }
        )

        # --orientation en texto nativo declara que no aplica.
        resultado = _orquestador().ejecutar(doc, orientation="horizontal")
        fronteras.append(
            {
                "que": "--orientation sin boxes declara que no aplica",
                "ok": "no trae boxes" in resultado.detalle["processing"].get("orientacion_motivo", ""),
                "detalle": resultado.detalle["processing"].get("orientacion_motivo", "")[:70],
            }
        )

        # Documento ilegible: ok=False con el error declarado (no una excepción).
        ilegible = _orquestador().ejecutar(tmp_path / "no-existe.md")
        fronteras.append(
            {
                "que": "documento ilegible → ok=False con el error declarado",
                "ok": ilegible.ok is False and "No se pudo leer" in (ilegible.error or ""),
                "detalle": (ilegible.error or "")[:70],
            }
        )

        # arca sin configuración: no disponible (ADR-003).
        entorno = _entorno(tmp_path)
        codigo_arca = main(["arca", "check", str(doc)], entorno=entorno)
        arca = json.loads(entorno.stdout.getvalue())
        fronteras.append(
            {
                "que": "arca check sin URL/token → no disponible, exit 1 (ADR-003)",
                "ok": codigo_arca == 1 and arca["disponible"] is False,
                "detalle": f"exit {codigo_arca} disponible={arca['disponible']}",
            }
        )

        # El contrato de F0 de PipelineResult sigue construible tal cual.
        from voucherflow.orchestrator import PipelineResult

        base = PipelineResult(documento_id="x", ok=True)
        fronteras.append(
            {
                "que": "PipelineResult conserva el contrato de F0 (campos con default)",
                "ok": base.resumen == {} and base.error is None,
                "detalle": "5 campos originales intactos",
            }
        )

    return fronteras


def main_cli() -> None:  # noqa: D401
    """Corre la inspección y sale con código ≠ 0 si algún chequeo falla."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--contrato", action="store_true", help="Solo el contrato del CLI.")
    parser.add_argument("--manual", action="store_true", help="Un caso, paso a paso.")
    parser.add_argument("--json", type=Path, default=None, help="Guardar el reporte JSON.")
    args = parser.parse_args()

    print(f"T-601 — CLI voucherflow + orquestador + fachada ({VERSION_CLI}, {VERSION_ORQUESTADOR})")
    reporte: dict[str, Any] = {"version_cli": VERSION_CLI, "version_orquestador": VERSION_ORQUESTADOR}
    fallos = 0

    print("\nContrato del CLI (E-CLI-1)")
    reporte["contrato"] = _contrato()
    for chequeo in reporte["contrato"]:
        marca = "✅" if chequeo["ok"] else "❌"
        print(f"  {marca} {chequeo['que']}")
        if not chequeo["ok"]:
            fallos += 1

    if not args.contrato:
        print("\nEscenarios (corrida completa y subcomandos)")
        reporte["escenarios"] = _escenarios()
        for escenario in reporte["escenarios"]:
            marca = "✅" if escenario["ok"] else "❌"
            print(f"  {marca} {escenario['nombre']:<28} {escenario['que']}")
            print(f"       esperado: {escenario['esperado']}")
            print(f"       obtenido: {escenario['obtenido']}")
            if not escenario["ok"]:
                fallos += 1

        print("\nFronteras de T-601 (lo que NO hace)")
        reporte["fronteras"] = _fronteras()
        for frontera in reporte["fronteras"]:
            marca = "✅" if frontera["ok"] else "❌"
            print(f"  {marca} {frontera['que']}")
            print(f"       {frontera['detalle']}")
            if not frontera["ok"]:
                fallos += 1

        if args.manual:
            print("\nCaso paso a paso (sin GPU)")
            with tempfile.TemporaryDirectory() as tmp:
                doc = _documento(Path(tmp))
                paso = _orquestador().ejecutar(doc)
                print(f"  archivo: {doc.name} → id {paso.documento_id[:20]}…")
                print(f"  etapas: {' → '.join(paso.etapas_completadas)}")
                print(f"  resumen: {json.dumps(paso.resumen, ensure_ascii=False)}")

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

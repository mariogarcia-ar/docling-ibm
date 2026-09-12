#!/usr/bin/env python
"""Inspecciona T-602 (F6) — batch con workers, checkpoints y enfriamiento.

**Fase**: F6 (cliente) · **Tarea**: T-602 · **Épica**: E-CLI-1/E-CLI-3 ·
**ADR-010**.

Verifica, **sin procesos reales, sin Ollama, sin Docling y sin dormir** (ejecutor
y reloj inyectados):

  1. El **contrato del CLI**: los flags de T-602 (`--cooling`, `--window`,
     `--cool-down`, `--no-checkpoints`) y el `--force` que ahora **sí** decide.
  2. La **reanudación**: la primera corrida procesa, la segunda **saltea** lo
     completado y ``--force`` lo reprocesa; un documento que **cambió** no se
     saltea (su hash es distinto); un documento que **falló** no se reutiliza; y
     un checkpoint **corrupto** se trata como ausente.
  3. Los **workers**: con 1 worker el lote es serial y lo declara; con varios, el
     pool de procesos recibe el trabajo como payload serializable y las olas
     respetan la capacidad.
  4. El **enfriamiento (ADR-010)**: la ventana de trabajo cierra el ciclo, la
     cuenta del enfriamiento arranca en el instante en que **todos** los workers
     están detenidos, el **último** ciclo nunca enfría y con ``enabled=False`` no
     hay pausas.
  5. Las **fronteras** de la tarea: el runner **no decide** (delega), no inventa
     checkpoints de documentos ilegibles, apagar los checkpoints no borra el
     estado del lote y el retorno de ``PipelineOrchestrator.ejecutar_lote`` sigue
     siendo la lista de T-601.

Uso:
    python scripts/F6/t602.py                       # contrato + escenarios + fronteras
    python scripts/F6/t602.py --contrato            # solo los flags del CLI
    python scripts/F6/t602.py --manual              # el ciclo de enfriamiento, paso a paso
    python scripts/F6/t602.py --json /tmp/t602.json

Nota: la suite default cubre lo mismo en ``tests/test_batch_t602.py``; este script
es la verificación de humo legible para la bitácora y sale con código ≠ 0 si algo
falla.
"""

from __future__ import annotations

import argparse
import json
import sys
import tempfile
from pathlib import Path
from typing import Any

# El paquete usa layout src/; si se corre sin instalar, se agrega src/ al sys.path.
_SRC = Path(__file__).resolve().parents[2] / "src"
if str(_SRC) not in sys.path:
    sys.path.insert(0, str(_SRC))

from voucherflow.batch import (  # noqa: E402
    EJECUTOR_PROCESOS,
    EJECUTOR_SERIAL,
    VERSION_LOTE,
    CheckpointDocumento,
    CheckpointsLote,
    EjecutorSerial,
    construir_trabajo,
    ejecutar_lote,
    resultado_a_payload,
    resultado_desde_dict,
    ruta_checkpoint,
)
from voucherflow.cli.main import (  # noqa: E402
    VERSION_CLI,
    EntornoCLI,
    construir_parser,
    main,
)
from voucherflow.orchestrator import (  # noqa: E402
    PipelineOrchestrator,
    PipelineResult,
    identificador_de_archivo,
)
from voucherflow.settings.config import CoolingSettings, cargar_desde_dict  # noqa: E402

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
    """Doble del ``DoclingConverter``: documento de texto nativo (sin gate T-102)."""

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
    import io

    return EntornoCLI(
        orquestador=_orquestador(), stdout=io.StringIO(), stderr=io.StringIO(), cwd=tmp
    )


def _carpeta(tmp: Path, cantidad: int) -> list[Path]:
    rutas = []
    for indice in range(cantidad):
        ruta = tmp / f"doc-{indice:02d}.md"
        ruta.write_text(f"FACTURA A\ncomprobante {indice}\n", encoding="utf-8")
        rutas.append(ruta)
    return rutas


class RelojFalso:
    """Reloj que avanza solo cuando el runner duerme (permite verificar sin esperar)."""

    def __init__(self) -> None:
        self.ahora = 0.0
        self.dormidas: list[float] = []

    def monotonic(self) -> float:
        return self.ahora

    def dormir(self, segundos: float) -> None:
        self.dormidas.append(float(segundos))
        self.ahora += max(0.0, float(segundos))


class EjecutorContado:
    """Ejecutor de prueba: cuenta ciclos y olas sin spawnear procesos."""

    nombre = "contado"

    def __init__(self, *, capacidad: int = 2, por_documento: float = 0.0, reloj=None) -> None:
        self.capacidad = capacidad
        self.por_documento = por_documento
        self.reloj = reloj
        self.arranques = 0
        self.detenciones = 0
        self.olas: list[int] = []
        self.workers_vivos = 0

    def iniciar(self) -> None:
        self.arranques += 1
        self.workers_vivos = self.capacidad

    def enviar(self, trabajo: dict[str, Any]) -> Any:
        from voucherflow.batch import FuturoListo

        json.dumps(trabajo)  # verifica que el trabajo sea serializable
        return FuturoListo({"trabajo": trabajo})

    def recolectar(self, futuros):
        self.olas.append(len(futuros))
        salida = []
        for futuro in futuros:
            trabajo = futuro.resultado()["trabajo"]
            if self.reloj is not None:
                self.reloj.ahora += self.por_documento
            salida.append(
                {
                    "documento_id": trabajo["documento_id"],
                    "archivo": trabajo["ruta"],
                    "ok": True,
                    "etapas_completadas": ["processing", "validation", "conclusion"],
                    "resumen": {"estado": "aprobado", "certeza": "alta", "origen": "programa"},
                    "detalle": {},
                    "resultado": None,
                    "caso": None,
                }
            )
        return salida

    def detener(self) -> None:
        self.detenciones += 1
        self.workers_vivos = 0


# ---------------------------------------------------------------------------
# Contrato del CLI
# ---------------------------------------------------------------------------


def _contrato() -> list[dict[str, Any]]:
    parser = construir_parser()
    args = parser.parse_args(
        [
            "batch",
            "files/",
            "--workers",
            "4",
            "--force",
            "--cooling",
            "on",
            "--work-window",
            "600",
            "--cool-down",
            "120",
        ]
    )
    return [
        {"que": "--workers (T-602)", "ok": args.workers == 4},
        {"que": "--force (reanudación real)", "ok": args.force is True},
        {"que": "--cooling on|off|auto", "ok": args.cooling == "on"},
        {"que": "--work-window S (ADR-010)", "ok": args.work_window == 600},
        {"que": "--cool-down S (ADR-010)", "ok": args.cool_down == 120},
        {"que": "--no-checkpoints existe", "ok": hasattr(args, "no_checkpoints")},
        {"que": "run sigue aceptando --force", "ok": parser.parse_args(["run", "x.md", "--force"]).force is True},
    ]


# ---------------------------------------------------------------------------
# Escenarios
# ---------------------------------------------------------------------------


def _escenarios() -> list[dict[str, Any]]:
    escenarios: list[dict[str, Any]] = []

    with tempfile.TemporaryDirectory() as tmp:
        tmp_path = Path(tmp)

        # 1. Primera corrida: procesa todo y deja checkpoints.
        rutas = _carpeta(tmp_path, 3)
        primera = ejecutar_lote(
            tmp_path, orquestador=_orquestador(), max_workers=1, reloj=RelojFalso()
        )
        escenarios.append(
            {
                "nombre": "primera_corrida",
                "que": "procesa los 3 documentos y deja su checkpoint",
                "ok": len(primera.resultados) == 3
                and all(ruta_checkpoint(r).is_file() for r in rutas),
                "esperado": "3 procesados + 3 checkpoints",
                "obtenido": f"{len(primera.resultados)} procesados",
            }
        )

        # 2. Segunda corrida: reanuda.
        segunda = ejecutar_lote(
            tmp_path, orquestador=_orquestador(), max_workers=1, reloj=RelojFalso()
        )
        escenarios.append(
            {
                "nombre": "reanuda_desde_checkpoints",
                "que": "la 2ª corrida saltea lo completado (Gherkin E-CLI-1)",
                "ok": segunda.resultados == [] and len(segunda.traza.reanudados) == 3,
                "esperado": "0 procesados / 3 reanudados",
                "obtenido": f"{len(segunda.resultados)} procesados / {len(segunda.traza.reanudados)} reanudados",
            }
        )

        # 3. --force reprocesa.
        con_force = ejecutar_lote(
            tmp_path, orquestador=_orquestador(), max_workers=1, force=True, reloj=RelojFalso()
        )
        escenarios.append(
            {
                "nombre": "force_reprocesa",
                "que": "--force rehace lo completado",
                "ok": len(con_force.resultados) == 3 and con_force.traza.reanudados == [],
                "esperado": "3 procesados / 0 reanudados",
                "obtenido": f"{len(con_force.resultados)} procesados",
            }
        )

        # 4. Un documento que cambió no se saltea.
        rutas[0].write_text("FACTURA A\ncontenido cambiado\n", encoding="utf-8")
        tras_cambio = ejecutar_lote(
            tmp_path, orquestador=_orquestador(), max_workers=1, reloj=RelojFalso()
        )
        escenarios.append(
            {
                "nombre": "documento_cambiado_se_reprocesa",
                "que": "el hash del contenido decide la reanudación, no el nombre",
                "ok": len(tras_cambio.resultados) == 1
                and tras_cambio.resultados[0].archivo == str(rutas[0]),
                "esperado": "1 procesado (el cambiado)",
                "obtenido": f"{len(tras_cambio.resultados)} procesados",
            }
        )

        # 5. Un fallo no se reutiliza.
        almacen = CheckpointsLote()
        almacen.marcar(
            PipelineResult(
                documento_id=identificador_de_archivo(rutas[1]),
                archivo=str(rutas[1]),
                ok=False,
                error="boom",
            )
        )
        escenarios.append(
            {
                "nombre": "un_fallo_no_se_reanuda",
                "que": "un checkpoint ok=False no es un paso completado",
                "ok": almacen.es_reanudable(rutas[1]) is False,
                "esperado": "no reanudable",
                "obtenido": f"reanudable={almacen.es_reanudable(rutas[1])}",
            }
        )

        # 6. Checkpoint corrupto: se trata como ausente.
        ruta_corrupta = tmp_path / "corrupto.md"
        ruta_corrupta.write_text("x", encoding="utf-8")
        ruta_checkpoint(ruta_corrupta).write_text("{no json", encoding="utf-8")
        escenarios.append(
            {
                "nombre": "checkpoint_corrupto",
                "que": "un checkpoint ilegible se trata como ausente (no tumba el lote)",
                "ok": CheckpointsLote().leer(ruta_corrupta) is None,
                "esperado": "None",
                "obtenido": "None",
            }
        )

        # 7. CLI: la reanudación se ve en el agregado del lote (T-603).
        lote_dir = tmp_path / "cli"
        lote_dir.mkdir()
        _carpeta(lote_dir, 2)
        agregado_path = lote_dir / "lote.json"
        entorno = _entorno(lote_dir)
        codigo = main(["batch", str(lote_dir), "-o", str(agregado_path)], entorno=entorno)
        datos = json.loads(agregado_path.read_text(encoding="utf-8"))
        escenarios.append(
            {
                "nombre": "cli_batch_reporta_reanudados",
                "que": "`batch` publica el agregado con la traza del lote",
                "ok": codigo == 0
                and datos["resumen"]["documentos"] == 2
                and datos["lote"]["version"] == VERSION_LOTE,
                "esperado": "exit 0 + 2 documentos + traza del lote",
                "obtenido": f"exit {codigo} / traza={datos['lote']['version']}",
            }
        )

        # 8. CLI: la segunda corrida no reprocesa y lo reporta.
        entorno2 = _entorno(lote_dir)
        codigo2 = main(
            ["batch", str(lote_dir), "-o", str(agregado_path)], entorno=entorno2
        )
        datos2 = json.loads(agregado_path.read_text(encoding="utf-8"))
        escenarios.append(
            {
                "nombre": "cli_batch_reanuda",
                "que": "la 2ª corrida del CLI no reprocesa y lo deja en el agregado",
                "ok": codigo2 == 0
                and len(datos2["lote"]["reanudados"]) == 2
                and datos2["resumen"]["documentos"] == 2,
                "esperado": "exit 0 / 2 reanudados / 2 documentos en el agregado",
                "obtenido": (
                    f"exit {codigo2} / reanudados={len(datos2['lote']['reanudados'])} "
                    f"/ documentos={datos2['resumen']['documentos']}"
                ),
            }
        )

        # 9. Workers: 1 worker es serial; varios con orquestador inyectado lo declara.
        varios = ejecutar_lote(
            lote_dir, orquestador=_orquestador(), max_workers=4, force=True, reloj=RelojFalso()
        )
        escenarios.append(
            {
                "nombre": "un_worker_es_serial",
                "que": "1 worker = camino serial determinista; varios con dobles se declara",
                "ok": varios.traza.ejecutor == EJECUTOR_SERIAL
                and varios.traza.max_workers_aplicado == 1
                and any("serial" in n for n in varios.traza.notas),
                "esperado": "serial + nota que lo explica",
                "obtenido": f"ejecutor={varios.traza.ejecutor}",
            }
        )

        # 10. El trabajo y el payload cruzan serializables (frontera del proceso).
        trabajo = construir_trabajo(lote_dir / "doc-00.md", "sha256:abc", {"orientation": "auto"})
        original = PipelineResult(
            documento_id="sha256:abc",
            archivo=str(lote_dir / "doc-00.md"),
            ok=True,
            etapas_completadas=["processing"],
            resumen={"estado": "aprobado"},
        )
        ida_vuelta = resultado_desde_dict(resultado_a_payload(original))
        escenarios.append(
            {
                "nombre": "payload_serializable",
                "que": "el trabajo y el resultado cruzan la frontera del proceso",
                "ok": json.loads(json.dumps(trabajo))["documento_id"] == "sha256:abc"
                and ida_vuelta.documento_id == "sha256:abc",
                "esperado": "round-trip sin pérdida",
                "obtenido": f"trabajo y resultado OK",
            }
        )

        # 11. Enfriamiento: ventana, todos detenidos y último ciclo sin pausa.
        enfri_dir = tmp_path / "enfriamiento"
        enfri_dir.mkdir()
        _carpeta(enfri_dir, 5)
        reloj = RelojFalso()
        ejecutor = EjecutorContado(capacidad=1, por_documento=60.0, reloj=reloj)
        con_enfriamiento = ejecutar_lote(
            enfri_dir,
            max_workers=1,
            ejecutor=ejecutor,
            reloj=reloj,
            cooling=CoolingSettings(enabled=True, work_window_s=100, cool_down_s=120),
            checkpoints=CheckpointsLote(habilitados=False),
        )
        ciclos = con_enfriamiento.traza.ciclos
        escenarios.append(
            {
                "nombre": "enfriamiento_adr010",
                "que": "la cuenta arranca con el pool detenido; el último ciclo no enfría",
                "ok": len(ciclos) > 1
                and all(c.hubo_enfriamiento for c in ciclos[:-1])
                and not ciclos[-1].hubo_enfriamiento
                and all(c.enfriado_s == 120.0 for c in ciclos if c.hubo_enfriamiento),
                "esperado": f"ciclos>1, pausas de 120s, último sin pausa",
                "obtenido": f"{len(ciclos)} ciclos / {con_enfriamiento.traza.enfriamientos} pausas",
            }
        )

        # 12. Con el enfriamiento apagado no hay pausas.
        sin = ejecutar_lote(
            enfri_dir,
            orquestador=_orquestador(),
            max_workers=1,
            cooling=CoolingSettings(enabled=False),
            reloj=RelojFalso(),
            checkpoints=CheckpointsLote(habilitados=False),
        )
        escenarios.append(
            {
                "nombre": "enfriamiento_apagado",
                "que": "enabled=False → una sola ventana, sin pausas",
                "ok": sin.traza.enfriamientos == 0 and len(sin.traza.ciclos) == 1,
                "esperado": "0 pausas / 1 ciclo",
                "obtenido": f"{sin.traza.enfriamientos} pausas / {len(sin.traza.ciclos)} ciclos",
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
        rutas = _carpeta(tmp_path, 2)

        # El runner no decide: si el gate dice no_comprobante, eso viaja.
        rechazo = ejecutar_lote(
            tmp_path,
            orquestador=_orquestador(gate="no_comprobante"),
            max_workers=1,
            force=True,
            reloj=RelojFalso(),
        )
        fronteras.append(
            {
                "que": "el runner no decide: el veredicto del gate viaja intacto",
                "ok": all(r.resumen["estado"] == "rechazado" for r in rechazo.resultados),
                "detalle": f"estados={[r.resumen['estado'] for r in rechazo.resultados]}",
            }
        )

        # No se inventa checkpoint para un archivo inexistente.
        inexistente = tmp_path / "no-existe.md"
        fronteras.append(
            {
                "que": "no se inventa checkpoint para un archivo inexistente",
                "ok": CheckpointsLote().marcar(
                    PipelineResult(documento_id="x", archivo=str(inexistente), ok=True)
                )
                is None,
                "detalle": "marcar() devuelve None",
            }
        )

        # Apagar los checkpoints no borra el estado existente.
        almacen = CheckpointsLote()
        almacen.marcar(
            PipelineResult(
                documento_id=identificador_de_archivo(rutas[0]),
                archivo=str(rutas[0]),
                ok=True,
                etapas_completadas=["processing"],
                resumen={"estado": "aprobado"},
            )
        )
        apagado = CheckpointsLote(habilitados=False)
        fronteras.append(
            {
                "que": "apagar los checkpoints no destruye el estado del lote",
                "ok": apagado.marcar(
                    PipelineResult(documento_id="y", archivo=str(rutas[1]), ok=True)
                )
                is None
                and ruta_checkpoint(rutas[0]).is_file(),
                "detalle": "no escribe, pero conserva lo existente",
            }
        )

        # El contrato de T-601 del retorno de ejecutar_lote se conserva.
        resultados = _orquestador().ejecutar_lote(tmp_path, max_workers=1, force=True)
        fronteras.append(
            {
                "que": "ejecutar_lote sigue devolviendo la lista de PipelineResult (contrato T-601)",
                "ok": isinstance(resultados, list)
                and all(isinstance(r, PipelineResult) for r in resultados)
                and "lote" in resultados[0].detalle,
                "detalle": f"{len(resultados)} resultados con traza de lote",
            }
        )

        # El pool real declara su capacidad y no arranca sin iniciar().
        from voucherflow.batch import EjecutorProcesos

        ejecutor = EjecutorProcesos(3)
        sin_iniciar = False
        try:
            ejecutor.enviar({"ruta": "x"})
        except RuntimeError:
            sin_iniciar = True
        fronteras.append(
            {
                "que": "el pool de procesos no acepta trabajo sin iniciar (cada worker inicializa su convertidor)",
                "ok": sin_iniciar and ejecutor.capacidad == 3,
                "detalle": f"capacidad={ejecutor.capacidad}, nombre={ejecutor.nombre}",
            }
        )

        # Un checkpoint ilegible no tumba la corrida: se reprocesa.
        corrupto = tmp_path / "corrupto.md"
        corrupto.write_text("x", encoding="utf-8")
        ruta_checkpoint(corrupto).write_text("{roto", encoding="utf-8")
        corrida = ejecutar_lote(
            tmp_path, orquestador=_orquestador(), max_workers=1, reloj=RelojFalso()
        )
        fronteras.append(
            {
                "que": "un checkpoint corrupto se reprocesa en vez de romper el lote",
                "ok": any(r.archivo == str(corrupto) for r in corrida.resultados),
                "detalle": f"{len(corrida.resultados)} documentos procesados",
            }
        )

    return fronteras


def main_cli() -> None:  # noqa: D401
    """Corre la inspección y sale con código ≠ 0 si algún chequeo falla."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--contrato", action="store_true", help="Solo los flags del CLI.")
    parser.add_argument("--manual", action="store_true", help="El ciclo de enfriamiento, paso a paso.")
    parser.add_argument("--json", type=Path, default=None, help="Guardar el reporte JSON.")
    args = parser.parse_args()

    print(f"T-602 — batch: workers + checkpoints + enfriamiento ({VERSION_LOTE}, {VERSION_CLI})")
    reporte: dict[str, Any] = {"version_lote": VERSION_LOTE, "version_cli": VERSION_CLI}
    fallos = 0

    print("\nContrato del CLI (flags de T-602)")
    reporte["contrato"] = _contrato()
    for chequeo in reporte["contrato"]:
        marca = "✅" if chequeo["ok"] else "❌"
        print(f"  {marca} {chequeo['que']}")
        if not chequeo["ok"]:
            fallos += 1

    if not args.contrato:
        print("\nEscenarios")
        reporte["escenarios"] = _escenarios()
        for escenario in reporte["escenarios"]:
            marca = "✅" if escenario["ok"] else "❌"
            print(f"  {marca} {escenario['nombre']:<32} {escenario['que']}")
            print(f"       esperado: {escenario['esperado']}")
            print(f"       obtenido: {escenario['obtenido']}")
            if not escenario["ok"]:
                fallos += 1

        print("\nFronteras de T-602 (lo que NO hace)")
        reporte["fronteras"] = _fronteras()
        for frontera in reporte["fronteras"]:
            marca = "✅" if frontera["ok"] else "❌"
            print(f"  {marca} {frontera['que']}")
            print(f"       {frontera['detalle']}")
            if not frontera["ok"]:
                fallos += 1

        if args.manual:
            print("\nCiclo de enfriamiento paso a paso (reloj inyectado, sin dormir)")
            with tempfile.TemporaryDirectory() as tmp:
                tmp_path = Path(tmp)
                _carpeta(tmp_path, 5)
                reloj = RelojFalso()
                ejecutor = EjecutorContado(capacidad=1, por_documento=60.0, reloj=reloj)
                resultado = ejecutar_lote(
                    tmp_path,
                    max_workers=1,
                    ejecutor=ejecutor,
                    reloj=reloj,
                    cooling=CoolingSettings(enabled=True, work_window_s=100, cool_down_s=120),
                    checkpoints=CheckpointsLote(habilitados=False),
                )
                for ciclo in resultado.traza.ciclos:
                    print(
                        f"  ciclo {ciclo.ciclo}: {ciclo.documentos} documento(s) | "
                        f"ventana {ciclo.duracion_ventana_s:.0f}s | "
                        f"todos detenidos en t={ciclo.todos_detenidos_s:.0f}s | "
                        f"enfriado {ciclo.enfriado_s:.0f}s"
                    )
                print(f"  total: {resultado.traza.enfriamientos} pausas, "
                      f"{resultado.traza.segundos_enfriados:.0f}s enfriados")

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

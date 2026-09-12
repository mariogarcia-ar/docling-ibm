"""Implementación del CLI ``voucherflow`` (F6 / T-601, E-CLI-1/E-CLI-2).

Subcomandos (doc 03 §8.1 y `ORCH-CLI.md` §3):

``process``        procesa un archivo/carpeta a Markdown ordenado (F1).
``validate``       gate "¿es comprobante?" doble paso (F2).
``classify``       tipo/letra + cadena contable 01→02→03 (F3 sobre markdown).
``extract``        extracción VLM+LLM combinada (F4) sobre archivo o carpeta.
``extract-detect`` letra por VLM/LLM (F3) sobre archivo o carpeta.
``run``            pipeline completo de un archivo (F6).
``batch``          pipeline completo de una carpeta recursiva (F6; workers en T-602).
``ask``            pregunta puntual sobre un documento (equivale a ``v1/ask.py``).
``arca``           consulta el padrón ARCA/WSCDC (opcional, ADR-003).
``case``           ``show``/``list`` la trazabilidad persistida (F5/T-506).
``hitl``           ``list`` la cola de revisión humana (F5/T-505).

Argumentos comunes (E-CLI-1): ``--force``, ``--orientation``,
``--condicion-impositiva``, ``--model``, ``--workers``. Cada uno viaja al módulo
que lo entiende y su **efecto real** queda registrado en la traza de la corrida
(el CLI no promete más de lo que aplica: ver ``--workers`` y T-602).

Contrato de proceso: ``main([...]) -> int``. La CLI **no** llama a ``sys.exit``
salvo en el entry point, para que los tests puedan invocarla in-process.
"""

from __future__ import annotations

import argparse
import json
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Sequence, TextIO

from ..orchestrator import (
    PipelineOrchestrator,
    PipelineResult,
    iterar_documentos,
)

#: Subcomandos que expone el CLI (el contrato de E-CLI-1 + ``extract-detect`` y
#: los de auditoría de `ORCH-CLI.md` §3).
COMANDOS: tuple[str, ...] = (
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
)

#: Condición impositiva por defecto de la cadena contable (F3/T-304).
CONDICION_DEFAULT = "21"

#: Extensiones de Markdown que la CLI reconoce como documento ya procesado.
EXTENSIONES_TEXTO = (".md", ".txt")

#: Versión del contrato del CLI (para el reporte y la paridad de T-604).
VERSION_CLI = "voucherflow-cli@1"


@dataclass
class EntornoCLI:
    """Colaboraciones del CLI, inyectables para la suite (sin red).

    Campos:
        orquestador: el :class:`~voucherflow.orchestrator.PipelineOrchestrator` a
            usar. ``None`` construye uno real (la CLI de producción).
        stdout / stderr: flujos de salida (``sys.stdout``/``sys.stderr``).
        cwd: directorio de trabajo para resolver rutas relativas.
    """

    orquestador: PipelineOrchestrator | None = None
    stdout: TextIO = field(default_factory=lambda: sys.stdout)
    stderr: TextIO = field(default_factory=lambda: sys.stderr)
    cwd: Path = field(default_factory=Path.cwd)

    def orch(self) -> PipelineOrchestrator:
        """El orquestador de la corrida (lo construye si no se inyectó)."""
        if self.orquestador is None:
            self.orquestador = PipelineOrchestrator()
        return self.orquestador

    def ruta(self, valor: str) -> Path:
        """Resuelve una ruta relativa contra el directorio de trabajo."""
        camino = Path(valor).expanduser()
        return camino if camino.is_absolute() else (self.cwd / camino)

    def log(self, mensaje: str) -> None:
        """Progreso/errores a ``stderr`` (``stdout`` es para el dato)."""
        print(mensaje, file=self.stderr)

    def dato(self, texto: str) -> None:
        """El dato de la corrida a ``stdout``."""
        print(texto, file=self.stdout)


class ErrorCLI(RuntimeError):
    """Error de uso o de la corrida que el CLI reporta con código ≠ 0."""


# ---------------------------------------------------------------------------
# Construcción del parser
# ---------------------------------------------------------------------------


def _agregar_comunes(parser: argparse.ArgumentParser) -> None:
    """Opciones que comparten los subcomandos que llaman al pipeline.

    ``--force`` se acepta por paridad de contrato (E-CLI-1) y se resuelve como
    "no saltear lo ya procesado": en T-601 cada corrida reprocesa, así que el
    flag se documenta como **no-op explícito** hasta que el checkpoint de T-602
    lo convierta en la diferencia entre reprocesar y reanudar. Declararlo evita
    que el operador crea que tiene un efecto que no tiene.
    """
    parser.add_argument(
        "--force",
        action="store_true",
        help=(
            "Reprocesa aunque existan salidas previas. En T-601 la corrida ya "
            "reprocesa siempre (el checkpoint/reanudación es T-602): el flag se "
            "acepta por paridad y su efecto se declara en la traza."
        ),
    )
    parser.add_argument(
        "--orientation",
        choices=["auto", "horizontal", "vertical"],
        default="auto",
        help="Orientación del texto a extraer (default: auto = la dominante).",
    )
    parser.add_argument(
        "--condicion-impositiva",
        default=CONDICION_DEFAULT,
        help=f"Condición impositiva de la cadena contable (default: {CONDICION_DEFAULT}).",
    )
    parser.add_argument(
        "--model",
        default=None,
        help="Modelo de Ollama a usar (default: el rol configurado de cada etapa).",
    )
    parser.add_argument(
        "--workers",
        type=int,
        default=1,
        help=(
            "Workers del modo batch. En T-601 el lote corre secuencial y el "
            "número se registra como solicitado; el pool real es T-602."
        ),
    )


def construir_parser() -> argparse.ArgumentParser:
    """Construye el parser de ``voucherflow`` con sus subcomandos (T-601)."""
    parser = argparse.ArgumentParser(
        prog="voucherflow",
        description=(
            "Cliente de la librería voucherflow: procesamiento, validación, "
            "clasificación, extracción y conclusión de comprobantes."
        ),
    )
    parser.add_argument("--version", action="version", version=f"voucherflow {VERSION_CLI}")
    sub = parser.add_subparsers(dest="comando", metavar="COMANDO")

    p = sub.add_parser("process", help="Procesa un archivo o carpeta a Markdown (F1).")
    p.add_argument("origen", help="Archivo o carpeta a procesar.")
    p.add_argument("-o", "--output", default=None, help="Directorio de salida (default: junto al archivo).")
    p.add_argument("--raw", action="store_true", help="Markdown crudo de Docling (equivale a v1/run_raw.py).")
    _agregar_comunes(p)

    p = sub.add_parser("validate", help="Gate '¿es comprobante?' doble paso (F2).")
    p.add_argument("origen")
    p.add_argument("--quick", action="store_true", help="Reservado: hoy el gate siempre hace doble paso (F2/T-203).")
    p.add_argument("--model", default=None, help="Modelo del gate (default: rol vlm).")

    p = sub.add_parser("classify", help="Tipo/letra + cadena contable sobre un markdown (F3).")
    p.add_argument("origen", help="Archivo markdown/OCR (o cualquier documento: se procesa con F1).")
    p.add_argument("--condicion-impositiva", default=CONDICION_DEFAULT)
    p.add_argument("--model", default=None)

    p = sub.add_parser("extract", help="Extracción VLM+LLM combinada (F4).")
    p.add_argument("origen", help="Archivo o carpeta.")
    p.add_argument("-M", "--mode", default="kvi", help="Modo heredado de v1 (kvi/kvg/10/11).")
    p.add_argument("-o", "--output", default=None, help="Archivo JSON de salida (default: stdout).")
    _agregar_comunes(p)

    p = sub.add_parser("extract-detect", help="Detecta la letra por VLM/LLM (equivale a -M 11.1 de v1).")
    p.add_argument("origen", help="Archivo o carpeta.")
    p.add_argument("-o", "--output", default=None, help="Archivo JSON de salida (default: stdout).")
    _agregar_comunes(p)

    p = sub.add_parser("run", help="Pipeline completo de un archivo (F6).")
    p.add_argument("origen")
    p.add_argument("-o", "--output", default=None, help="Archivo JSON del resultado (default: stdout).")
    p.add_argument("--cases", default=None, metavar="DIR", help="Persiste el CaseRecord (sidecar + índice, F5/T-506).")
    p.add_argument("--clasificar-contable", action="store_true", help="Corre la cadena contable 01→02→03 (tres llamadas al modelo).")
    p.add_argument("--no-gate", action="store_true", help="No corre el gate de F2 antes de extraer.")
    p.add_argument("--no-agente", action="store_true", help="No escala al agente IA si el código no concluye.")
    _agregar_comunes(p)

    p = sub.add_parser("batch", help="Pipeline completo de una carpeta recursiva (F6; workers en T-602).")
    p.add_argument("origen", help="Carpeta (o archivo) a procesar.")
    p.add_argument("-o", "--output", default=None, help="Archivo JSON con la salida agregada.")
    p.add_argument("--cases", default=None, metavar="DIR", help="Persiste los CaseRecord (sidecar + índice).")
    p.add_argument("--clasificar-contable", action="store_true")
    p.add_argument("--no-gate", action="store_true")
    p.add_argument("--no-agente", action="store_true")
    _agregar_comunes(p)

    p = sub.add_parser("ask", help="Pregunta puntual sobre un documento (equivale a v1/ask.py).")
    p.add_argument("origen")
    p.add_argument("-q", "--question", required=True, help="Pregunta a responder.")
    p.add_argument("--model", default=None)

    p = sub.add_parser("arca", help="Consulta el padrón ARCA/WSCDC (opcional, ADR-003).")
    p.add_argument("accion", choices=["check"], help="Acción a ejecutar.")
    p.add_argument("origen", help="Documento cuyo comprobante se quiere constatar.")
    p.add_argument("--cuit", default=None, help="CUIT del emisor para la autenticación (si aplica).")
    p.add_argument("--url", default=None, help="URL del WSCDC (default: la de la configuración).")
    p.add_argument("--token", default=None, help="Token de autorización (flujo WSAA; fuera del MVP).")
    p.add_argument("--timeout", type=float, default=15.0)
    p.add_argument("--condicion-impositiva", default=CONDICION_DEFAULT)
    p.add_argument("--model", default=None)

    p = sub.add_parser("case", help="Consulta la trazabilidad persistida (F5/T-506).")
    p.add_argument("accion", choices=["show", "list"], help="show <documento_id> | list [--filtro]")
    p.add_argument("documento_id", nargs="?", help="Id del documento (para 'show').")
    p.add_argument("--dir", dest="dir_casos", default=".", help="Directorio de los sidecars (default: '.').")
    p.add_argument("--filtro", action="append", default=[], metavar="CAMPO=VALOR", help="Filtro del índice (repetible).")

    p = sub.add_parser("hitl", help="Consulta la cola de revisión humana (F5/T-505).")
    p.add_argument("accion", choices=["list"], help="Acción a ejecutar.")
    p.add_argument("--dir", dest="dir_casos", default=None, help="Directorio de sidecars (índice, F5/T-506).")
    p.add_argument("--certeza", choices=["alta", "baja"], default=None, help="Filtra por certeza del caso.")
    p.add_argument("--prioridad", choices=["alta", "baja"], default=None, help="Filtra por prioridad de la revisión.")

    return parser


# ---------------------------------------------------------------------------
# Helpers de salida
# ---------------------------------------------------------------------------


def _json_salida(datos: Any, destino: str | None, entorno: EntornoCLI) -> None:
    """Escribe ``datos`` como JSON: a archivo si hay destino, si no a stdout."""
    texto = json.dumps(datos, ensure_ascii=False, indent=2)
    if destino:
        ruta = entorno.ruta(destino)
        ruta.parent.mkdir(parents=True, exist_ok=True)
        ruta.write_text(texto + "\n", encoding="utf-8")
        entorno.log(f"Guardado: {ruta}")
    else:
        entorno.dato(texto)


def _resultado_a_dict(resultado: PipelineResult) -> dict[str, Any]:
    """Vista JSON del ``PipelineResult`` (incluye el ``CaseRecord`` completo)."""
    datos = resultado.como_dict()
    datos["caso"] = (
        resultado.caso.model_dump(mode="json") if resultado.caso is not None else None
    )
    return datos


def _resumen_legible(resultado: PipelineResult) -> str:
    """Línea corta de la corrida para el log (stderr)."""
    return (
        f"[{resultado.estado or 'sin estado'}] {resultado.archivo} "
        f"(certeza={resultado.resumen.get('certeza')}, "
        f"origen={resultado.resumen.get('origen')}, "
        f"letra={resultado.resumen.get('tipo_comprobante')})"
    )


# ---------------------------------------------------------------------------
# Implementación de cada subcomando
# ---------------------------------------------------------------------------


def _cmd_process(args: argparse.Namespace, entorno: EntornoCLI) -> int:
    """``process``: procesa con F1 y escribe el markdown (equivale a v1/ocr_documents)."""
    raiz = entorno.ruta(args.origen)
    documentos = iterar_documentos(raiz, extensiones=_extensiones_de_proceso())
    if not documentos:
        raise ErrorCLI(
            f"No hay documentos procesables en {raiz}. Formatos soportados: "
            "pdf/imagen/office/texto."
        )
    for ruta in documentos:
        documento = entorno.orch().procesar(
            ruta, docling_raw=args.raw, orientation=args.orientation
        )[0]
        destino = _destino_markdown(ruta, args.output, entorno)
        destino.parent.mkdir(parents=True, exist_ok=True)
        destino.write_text(documento.markdown or "", encoding="utf-8")
        entorno.log(f"OK: {destino} (orientación={documento.orientacion})")
    return 0


def _extensiones_de_proceso() -> set[str]:
    """Extensiones de ``process``: las de Docling **más** el texto plano ``.txt``."""
    from ..models.docling import EXTENSIONES_SOPORTADAS

    return set(EXTENSIONES_SOPORTADAS) | {".txt", ".csv", ".log"}


def _destino_markdown(ruta: Path, output: str | None, entorno: EntornoCLI) -> Path:
    """Resuelve el markdown de salida (junto al archivo o en ``-o DIR``)."""
    if not output:
        return ruta.with_suffix(".md")
    return entorno.ruta(output) / f"{ruta.stem}.md"


def _cmd_validate(args: argparse.Namespace, entorno: EntornoCLI) -> int:
    """``validate``: gate doble paso (F2). Sale con ≠ 0 si no es comprobante."""
    ruta = entorno.ruta(args.origen)
    resultado = entorno.orch().validar(
        entorno.orch().procesar(ruta)[0], modelo=args.model
    )
    entorno.dato(
        json.dumps(
            {
                "archivo": str(ruta),
                "veredicto_final": resultado.veredicto_final.value,
                "vista_fiel_preparada": resultado.vista_fiel is not None,
                "pasadas": [
                    {
                        "vista": p.vista_usada,
                        "veredicto": p.veredicto.value,
                        "confianza": p.confianza_fuente,
                    }
                    for p in resultado.pasadas
                ],
            },
            ensure_ascii=False,
            indent=2,
        )
    )
    return 0 if resultado.veredicto_final.value == "comprobante" else 1


def _cmd_classify(args: argparse.Namespace, entorno: EntornoCLI) -> int:
    """``classify``: tipo/letra + cadena contable (F3) sobre el markdown del archivo."""
    ruta = entorno.ruta(args.origen)
    if ruta.suffix.lower() in EXTENSIONES_TEXTO:
        markdown = ruta.read_text(encoding="utf-8")
    else:
        markdown = entorno.orch().procesar(ruta)[0].markdown

    from ..classification.tipo_comprobante import clasificar_tipo_comprobante
    from ..rules.contexto import ContextoTipoComprobante

    tipo = clasificar_tipo_comprobante(ContextoTipoComprobante(texto_encabezado_llm=markdown))
    clasificacion, detalle_contable = entorno.orch().clasificar_contable(
        markdown,
        condicion_impositiva=args.condicion_impositiva,
        documento=ruta,
        modelo=args.model,
    )
    entorno.dato(
        json.dumps(
            {
                "archivo": str(ruta),
                "tipo_comprobante": tipo.letra,
                "certeza": tipo.certeza,
                "origen": tipo.origen,
                "reglas_aplicadas": list(tipo.reglas_aplicadas),
                "campos_desconocidos": list(tipo.campos_desconocidos),
                "clasificacion_contable": (
                    clasificacion.model_dump(mode="json") if clasificacion else None
                ),
                "detalle_contable": detalle_contable,
            },
            ensure_ascii=False,
            indent=2,
        )
    )
    return 0 if detalle_contable.get("ok") else 1


def _cmd_extract(args: argparse.Namespace, entorno: EntornoCLI) -> int:
    """``extract``: extracción VLM+LLM combinada (F4) por archivo o carpeta."""
    raiz = entorno.ruta(args.origen)
    documentos = iterar_documentos(raiz)
    if not documentos:
        raise ErrorCLI(f"No hay documentos extraíbles en {raiz}.")

    salidas: list[dict[str, Any]] = []
    fallos = 0
    for ruta in documentos:
        try:
            evidencia = _extraer_archivo(ruta, args.mode, args, entorno)
            salidas.append(
                {
                    "archivo": str(ruta),
                    "documento_id": evidencia.documento_id,
                    "modo": args.mode,
                    "evidencia": evidencia.model_dump(mode="json"),
                }
            )
            entorno.log(f"OK: {ruta} ({len(evidencia.campos)} campos)")
        except Exception as exc:  # noqa: BLE001 - un documento no tumba el lote
            fallos += 1
            salidas.append({"archivo": str(ruta), "error": str(exc)})
            entorno.log(f"ERROR: {ruta}: {exc}")

    _json_salida(salidas, args.output, entorno)
    return 1 if fallos else 0


def _extraer_archivo(
    ruta: Path, mode: str, args: argparse.Namespace, entorno: EntornoCLI
) -> Any:
    """Extrae y combina la evidencia de un archivo (F4), reutilizando el orquestador."""
    orch = entorno.orch()
    documento, _ = orch.procesar(ruta, orientation=args.orientation)
    gate = orch.validar(documento, modelo=args.model)
    from ..orchestrator import identificador_de_archivo

    documento_id = identificador_de_archivo(ruta)
    extraccion = orch.extraer(
        documento,
        vista=gate.vista_fiel,
        documento_id=documento_id,
        modelo=args.model,
    )
    evidencia = orch.combinar(documento_id, extraccion.evidencias)
    trazabilidad = dict(evidencia.trazabilidad)
    trazabilidad["cli_extract"] = {
        "mode_heredado": mode,
        "version_cli": VERSION_CLI,
        "nota": (
            "El modo heredado de v1 (kvi/kvg/10/11) se registra para la paridad de "
            "T-604; el contrato de extracción de v2 es uno solo."
        ),
    }
    from ..schemas.evidence import CombinedEvidence

    return CombinedEvidence(
        documento_id=evidencia.documento_id,
        campos=dict(evidencia.campos),
        decision=evidencia.decision,
        trazabilidad=trazabilidad,
    )


def _cmd_extract_detect(args: argparse.Namespace, entorno: EntornoCLI) -> int:
    """``extract-detect``: letra por VLM/LLM (equivale a ``document_extraction.py -M 11.1``)."""
    raiz = entorno.ruta(args.origen)
    documentos = iterar_documentos(raiz)
    if not documentos:
        raise ErrorCLI(f"No hay documentos en {raiz}.")

    from ..classification.evidencia import leer_evidencia
    from ..classification.tipo_comprobante import clasificar_tipo_comprobante

    salidas: list[dict[str, Any]] = []
    fallos = 0
    for ruta in documentos:
        try:
            orch = entorno.orch()
            documento, _ = orch.procesar(ruta, orientation=args.orientation)
            gate = orch.validar(documento, modelo=args.model)
            lectura = leer_evidencia(
                orch._cliente(),
                markdown=documento.markdown,
                vista=gate.vista_fiel,
                settings=orch.settings_efectivos(),
                modelo=args.model,
            )
            tipo = clasificar_tipo_comprobante(lectura.contexto or {})
            salidas.append(
                {
                    "archivo": str(ruta),
                    "tipo_comprobante": tipo.letra,
                    "certeza": tipo.certeza,
                    "origen": tipo.origen,
                    "reglas_aplicadas": list(tipo.reglas_aplicadas),
                    "candidatos_descartados": list(tipo.candidatos_descartados),
                    "candidatos_restantes": list(tipo.candidatos_restantes),
                    "detalle_lectura": lectura.detalle,
                }
            )
            entorno.log(f"OK: {ruta} → letra {tipo.letra!r}")
        except Exception as exc:  # noqa: BLE001 - un documento no tumba el lote
            fallos += 1
            salidas.append({"archivo": str(ruta), "error": str(exc)})
            entorno.log(f"ERROR: {ruta}: {exc}")

    _json_salida(salidas, args.output, entorno)
    return 1 if fallos else 0


def _opciones_pipeline(args: argparse.Namespace) -> dict[str, Any]:
    """Traduce los flags comunes a los keywords del orquestador."""
    return {
        "condicion_impositiva": args.condicion_impositiva,
        "modelo": args.model,
        "orientation": args.orientation,
        "clasificar_contable": bool(getattr(args, "clasificar_contable", False)),
        "usar_gate": not bool(getattr(args, "no_gate", False)),
        "escalar": not bool(getattr(args, "no_agente", False)),
    }


def _cmd_run(args: argparse.Namespace, entorno: EntornoCLI) -> int:
    """``run``: pipeline completo de **un** archivo (F6/T-601)."""
    ruta = entorno.ruta(args.origen)
    resultado = entorno.orch().ejecutar(
        ruta,
        persistir=bool(args.cases),
        dir_salida=args.cases,
        **_opciones_pipeline(args),
    )
    if args.force:
        resultado.detalle["force"] = (
            "Aceptado por paridad (E-CLI-1): en T-601 la corrida reprocesa siempre "
            "y el checkpoint/reanudación llega con T-602."
        )
    _json_salida(_resultado_a_dict(resultado), args.output, entorno)
    entorno.log(_resumen_legible(resultado))
    return 0 if resultado.ok else 1


def _cmd_batch(args: argparse.Namespace, entorno: EntornoCLI) -> int:
    """``batch``: pipeline completo de una **carpeta recursiva** (F6/T-601).

    El recorrido es secuencial y determinista (T-601); el pool con workers,
    checkpoints y enfriamiento es T-602. ``--workers`` viaja al orquestador y su
    efecto real (aplicado/secuencial) queda en el detalle de cada corrida.
    """
    raiz = entorno.ruta(args.origen)
    resultados = entorno.orch().ejecutar_lote(
        raiz,
        max_workers=args.workers,
        persistir=bool(args.cases),
        dir_salida=args.cases,
        **_opciones_pipeline(args),
    )
    if not resultados:
        raise ErrorCLI(f"No hay documentos procesables en {raiz}.")

    ok = sum(1 for r in resultados if r.ok)
    resumen = {
        "version": VERSION_CLI,
        "raiz": str(raiz),
        "documentos": len(resultados),
        "ok": ok,
        "errores": len(resultados) - ok,
        "max_workers_solicitado": args.workers,
        "max_workers_aplicado": 1,
        "nota": (
            "T-601 recorre el lote de forma secuencial. Los workers con su "
            "convertidor por worker, los checkpoints y la política de enfriamiento "
            "(ADR-010) son T-602."
        ),
        "resultados": [_resultado_a_dict(r) for r in resultados],
    }
    _json_salida(resumen, args.output, entorno)
    entorno.log(f"Lote: {ok}/{len(resultados)} documentos procesados")
    return 0 if ok == len(resultados) else 1


def _cmd_ask(args: argparse.Namespace, entorno: EntornoCLI) -> int:
    """``ask``: pregunta puntual (equivalente a ``v1/ask.py``)."""
    from ..api import ask as api_ask

    respuesta = api_ask(
        str(entorno.ruta(args.origen)),
        args.question,
        modelo=args.model,
        cliente=entorno.orch().cliente,
        settings=entorno.orch().settings_efectivos(),
        converter=entorno.orch().converter,
    )
    entorno.dato(respuesta)
    return 0


def _cmd_arca(args: argparse.Namespace, entorno: EntornoCLI) -> int:
    """``arca check``: constata un comprobante contra el padrón (ADR-003, opcional).

    Es el único subcomando que mantiene el flujo WSAA **fuera** del MVP: si no
    hay URL/token configurados, el resultado es *no disponible* (un error de uso,
    no un fallo del pipeline) y se reporta con código ≠ 0 — a diferencia de la
    búsqueda del pipeline, donde el hook apagado es un caso **normal**.
    """
    from ..models.arca import ArcaClient
    from ..rules.gaps import Gap, PresupuestoBusqueda, detectar_gaps
    from ..rules.contexto_conclusion import ContextoConclusion

    ruta = entorno.ruta(args.origen)
    orch = entorno.orch()
    documento, _ = orch.procesar(ruta)
    gate = orch.validar(documento, modelo=args.model)
    from ..orchestrator import identificador_de_archivo

    documento_id = identificador_de_archivo(ruta)
    extraccion = orch.extraer(
        documento,
        vista=gate.vista_fiel,
        documento_id=documento_id,
        modelo=args.model,
    )
    evidencia = orch.combinar(documento_id, extraccion.evidencias)
    contexto = ContextoConclusion.desde_evidencia(evidencia)
    deteccion = detectar_gaps(contexto)

    cliente = ArcaClient(
        url=args.url,
        timeout_s=args.timeout,
        cuit=args.cuit,
        token=args.token,
    )
    consultas = [
        cliente.buscar(gap, contexto)
        for gap in deteccion.gaps
        if gap.buscable
    ][: PresupuestoBusqueda().max_consultas]

    disponible = any(c.disponible for c in consultas)
    entorno.dato(
        json.dumps(
            {
                "archivo": str(ruta),
                "documento_id": documento_id,
                "gaps": [
                    {"campo": g.campo, "busqueda_habilitada": g.buscable, "objetivo": g.objetivo}
                    for g in deteccion.gaps
                ],
                "consultas": [c.como_dict() if hasattr(c, "como_dict") else c.__dict__ for c in consultas],
                "disponible": disponible,
                "nota": (
                    "ADR-003: el hook ARCA es opcional y no bloquea el MVP. El flujo "
                    "WSAA (certificados) es de la integración real; sin URL/token "
                    "configurados el resultado es 'no disponible'."
                ),
            },
            ensure_ascii=False,
            indent=2,
        )
    )
    return 0 if disponible else 1


def _cmd_case(args: argparse.Namespace, entorno: EntornoCLI) -> int:
    """``case show|list``: consulta la trazabilidad persistida (F5/T-506)."""
    from ..trace.recorder import CaseRecorder

    recorder = CaseRecorder(entorno.ruta(args.dir_casos))

    if args.accion == "list":
        filtros = _filtros(args.filtro)
        filas = recorder.buscar(**filtros) if filtros else recorder.leer_indice()
        entorno.dato(json.dumps(filas, ensure_ascii=False, indent=2))
        return 0

    if not args.documento_id:
        raise ErrorCLI("'case show' necesita el documento_id (ver 'case list').")
    caso = recorder.leer(args.documento_id)
    entorno.dato(caso.model_dump_json(indent=2))
    return 0


def _cmd_hitl(args: argparse.Namespace, entorno: EntornoCLI) -> int:
    """``hitl list``: la cola de revisión humana (F5/T-505 + índice T-506).

    La cola **en memoria** de T-505 se pierde al terminar el proceso (el store
    durable es el sidecar/índice de T-506); por eso ``hitl list --dir DIR`` lee el
    **histórico persistido** y filtra por ``hitl_requerido``/prioridad/certeza.
    Sin ``--dir`` se reporta la cola viva de la corrida (si el entorno inyectó
    una): es la diferencia entre "lo que hay que revisar ahora" y "lo que quedó
    pendiente en el histórico".
    """
    if args.dir_casos:
        from ..trace.recorder import CaseRecorder

        recorder = CaseRecorder(entorno.ruta(args.dir_casos))
        filas = recorder.buscar(hitl_requerido=True)
        if args.prioridad:
            filas = [f for f in filas if f.get("hitl_prioridad") == args.prioridad]
        if args.certeza:
            filas = [f for f in filas if f.get("certeza") == args.certeza]
        entorno.dato(json.dumps(filas, ensure_ascii=False, indent=2))
        return 0

    cola = entorno.orch().cola
    if cola is None:
        entorno.dato(json.dumps([], ensure_ascii=False, indent=2))
        entorno.log(
            "No hay cola HITL en memoria en esta corrida: usá '--dir DIR' para leer "
            "el histórico persistido (T-506) o corré un batch con cola."
        )
        return 0

    pendientes = cola.pendientes()
    if args.prioridad:
        pendientes = [e for e in pendientes if e.prioridad == args.prioridad]
    entorno.dato(
        json.dumps(
            [e.como_dict() if hasattr(e, "como_dict") else e.__dict__ for e in pendientes],
            ensure_ascii=False,
            indent=2,
        )
    )
    return 0


def _filtros(pares: Sequence[str]) -> dict[str, Any]:
    """Convierte ``CAMPO=VALOR`` repetidos al filtro del índice (F5/T-506).

    ``None``/``true``/``false`` se coercionan para poder filtrar los campos
    booleanos y nulos del índice (``hitl_requerido=false``); cualquier otro valor
    viaja como texto.
    """
    filtros: dict[str, Any] = {}
    for par in pares:
        if "=" not in par:
            raise ErrorCLI(f"Filtro inválido {par!r}: se espera CAMPO=VALOR.")
        campo, valor = par.split("=", 1)
        bajo = valor.strip().lower()
        if bajo in {"true", "false"}:
            filtros[campo] = bajo == "true"
        elif bajo == "null":
            filtros[campo] = None
        else:
            filtros[campo] = valor
    return filtros


#: Tabla de subcomandos → implementación. Es una constante (y no un ``if`` largo)
#: para que la lista de comandos del contrato (E-CLI-1) sea verificable en tests.
DESPACHO: dict[str, Callable[[argparse.Namespace, EntornoCLI], int]] = {
    "process": _cmd_process,
    "validate": _cmd_validate,
    "classify": _cmd_classify,
    "extract": _cmd_extract,
    "extract-detect": _cmd_extract_detect,
    "run": _cmd_run,
    "batch": _cmd_batch,
    "ask": _cmd_ask,
    "arca": _cmd_arca,
    "case": _cmd_case,
    "hitl": _cmd_hitl,
}


def main(argv: Sequence[str] | None = None, entorno: EntornoCLI | None = None) -> int:
    """Punto de entrada del CLI (T-601). Devuelve el **código de salida**.

    Argumentos:
        argv: argumentos (default: ``sys.argv[1:]``).
        entorno: colaboraciones inyectables (default: el entorno real).

    Devuelve:
        ``0`` si el comando terminó bien; ``1`` si el comando reportó un fallo de
        la corrida; ``2`` en errores de uso (``argparse``).
    """
    parser = construir_parser()
    args = parser.parse_args(argv)
    ctx = entorno or EntornoCLI()

    if not args.comando:
        parser.print_help(file=ctx.stderr)
        return 2

    despacho = DESPACHO.get(args.comando)
    if despacho is None:  # pragma: no cover - argparse ya restringe los comandos
        ctx.log(f"Comando desconocido: {args.comando}")
        return 2

    try:
        return despacho(args, ctx)
    except ErrorCLI as exc:
        ctx.log(f"ERROR: {exc}")
        return 1
    except FileNotFoundError as exc:
        ctx.log(f"ERROR: {exc}")
        return 1
    except KeyboardInterrupt:  # pragma: no cover - interrupción del usuario
        ctx.log("Interrumpido por el usuario.")
        return 130


def entrypoint() -> None:  # pragma: no cover - el ``sys.exit`` vive acá, no en main
    """Entry point del script ``voucherflow`` (``pyproject.toml``)."""
    raise SystemExit(main())


if __name__ == "__main__":  # pragma: no cover
    entrypoint()


__all__ = [
    "COMANDOS",
    "CONDICION_DEFAULT",
    "DESPACHO",
    "ErrorCLI",
    "EntornoCLI",
    "VERSION_CLI",
    "construir_parser",
    "entrypoint",
    "main",
]

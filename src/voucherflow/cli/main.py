"""Implementación del CLI ``voucherflow`` (F6 / T-601, E-CLI-1/E-CLI-2).

Subcomandos (doc 03 §8.1 y `ORCH-CLI.md` §3):

``process``        procesa un archivo/carpeta a Markdown ordenado (F1).
``validate``       gate "¿es comprobante?" doble paso (F2).
``classify``       tipo/letra + cadena contable 01→02→03 (F3 sobre markdown).
``extract``        extracción VLM+LLM combinada (F4) sobre archivo o carpeta.
``extract-detect`` letra por VLM/LLM (F3) sobre archivo o carpeta.
``run``            pipeline completo de un archivo (F6).
``batch``          pipeline completo de una carpeta recursiva (F6/T-602): workers,
                   checkpoints/reanudación y enfriamiento; escribe el **agregado**
                   del lote (F6/T-603).
``ask``            pregunta puntual sobre un documento (equivale a el cliente de preguntas original).
``arca``           consulta el padrón ARCA/WSCDC (opcional, ADR-003).
``case``           ``show``/``list`` la trazabilidad persistida (F5/T-506) y
                   ``aggregate`` reconstruye el agregado del lote (F6/T-603).
``hitl``           ``list`` la cola de revisión humana (F5/T-505).
``corpus``         pre-reduce peso y tokens de visión de un corpus de imágenes.

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
import os
import sys
from collections.abc import Callable, Iterator, Sequence
from contextlib import contextmanager
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, TextIO

from ..corpus.recorrido import esta_dentro, raiz_espejado, salida_de
from ..corridas import ESPERA_DEFECTO
from ..orchestrator import (
    PipelineOrchestrator,
    PipelineResult,
    identificador_de_archivo,
    iterar_documentos,
)
from ..persistencia import escribir_atomico, escribir_json_atomico, ya_escrito

#: Subcomandos que expone el CLI (el contrato de E-CLI-1 + ``extract-detect`` y
#: los de auditoría de `ORCH-CLI.md` §3, + ``corpus`` y ``pdf``).
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
    "corpus",
    "pdf",
    "stop",
)

#: Condición impositiva por defecto de la cadena contable (F3/T-304).
CONDICION_DEFAULT = "21"

#: Extensiones de Markdown que la CLI reconoce como documento ya procesado.
EXTENSIONES_TEXTO = (".md", ".txt")

#: Versión del contrato del CLI (para el reporte y la paridad de T-604).
VERSION_CLI = "voucherflow-cli@1"

#: Marca que este CLI le pone a las entradas de ``extract`` para poder reutilizarlas.
#: Cambiar el conjunto de campos que se extrae (o su forma) **invalida el reúso**: una
#: entrada sin esta marca viene de otra versión del CLI y se vuelve a extraer en vez de
#: reusarse (una evidencia vieja presentada como vigente es peor que no reusar).
MARCA_EXTRACT = "cli_extract"

#: Modo con el que ``extract-detect`` marca sus entradas. Es un modo propio (no es un
#: modo heredado de ``extract``) justamente para que las dos salidas **no** se
#: confundan entre sí: el contrato de cada una es distinto.
MARCA_DETECT = "detect"

#: A partir de este tamaño (bytes) la salida de ``extract`` por ``stdout`` se
#: declara en el log: no cambia el contrato, avisa del costo de leerla.
LIMITE_STDOUT_BYTES = 1_000_000

#: Nombre del archivo donde ``process -o DIR`` deja los documentos que fallaron.
#: ⚠️ No es ``*.md`` a propósito: un markdown ahí sería un documento de entrada
#: (``iterar_documentos``) y la corrida siguiente lo reprocesaría como si fuera una fuente.
NOMBRE_FALLOS = "fallos.json"


def _reusables_de_extract(
    previas: Any, *, modo: str, version: str = VERSION_CLI
) -> dict[str, dict[str, Any]]:
    """Entradas de un ``extract`` anterior que se pueden reutilizar, por ``documento_id``.

    ⚠️ Existe porque ``extract`` **reprocesaba y volvía a consultar al modelo** en cada
    corrida (medido: 2 llamadas por documento), y además **sobreescribía** su archivo de
    salida: correr sobre una carpeta ya hecha pagaba todo de nuevo y el resultado anterior
    se perdía. Es el mismo problema que tenían ``process``/``pdf``/``corpus``, pero el
    contrato de salida es distinto (un **único** JSON del lote, no un archivo por
    documento), así que la marca de "ya hecho" es una entrada de ese lote.

    Qué se exige para reutilizar una entrada (las cuatro importan):

    1. **``documento_id``**: es el ``sha256`` del contenido, así que un documento
       **modificado** cambia de id y se vuelve a extraer solo (es la misma propiedad que
       hace segura la reanudación de ``batch``). Además el id ya es **único por
       definición**, mientras que la ruta es relativa a la invocación y el nombre de
       archivo no distingue dos homónimos de carpetas distintas.
    2. **Sin ``error``**: un error **no es un paso completado** (lección de T-603). Si no
       se filtrara, una re-corrida después de arreglar la causa (clave, red, modelo)
       saltearía ese documento y el resumen reportaría éxito.
    3. **El mismo ``modo``**: el modo heredado viaja en el resultado, y reusar lo
       extraído en otro modo afirmaría algo falso sobre esa corrida.
    4. **La marca del CLI** (``MARCA_EXTRACT``): una entrada sin la marca viene de otra
       versión, con otros campos o prompts.

    Devuelve ``{documento_id: entrada}``. Un archivo previo ilegible o de otra forma
    devuelve ``{}``: no se reusa nada y la corrida reprocesa (un derivado roto no puede
    tirar la corrida, misma regla que el índice de T-506 y los checkpoints de T-602).
    """
    if not isinstance(previas, list):
        return {}
    reusables: dict[str, dict[str, Any]] = {}
    for entrada in previas:
        if not isinstance(entrada, dict):
            continue
        if entrada.get("error"):
            continue
        if entrada.get("modo") != modo:
            continue
        if MARCA_EXTRACT not in entrada:
            continue
        documento_id = entrada.get("documento_id")
        if isinstance(documento_id, str) and documento_id:
            reusables[documento_id] = entrada
    return reusables


def _leer_previas(destino: Path) -> list[Any]:
    """El JSON previo de una salida de ``extract``, o ``[]`` si no hay o no se puede leer.

    Nada de esto es fatal: el archivo previo es una **ayuda** para no volver a pagar, no
    el dato de la corrida. Si está corrupto o lo escribió otro comando, se ignora.
    """
    if not destino.is_file():
        return []
    try:
        datos = json.loads(destino.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return []
    return datos if isinstance(datos, list) else []


def _limite_texto(texto: str, limite: int) -> str:
    """``texto`` recortado a ``limite``, declarando cuánto se omitió."""
    if len(texto) <= limite:
        return texto
    return (
        texto[:limite]
        + f"\n… (recortado: {len(texto) - limite} caracteres más; "
        f"el JSON completo está en el archivo de -o)\n"
    )


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


class ErrorDeUsoCLI(ErrorCLI):
    """Error de **uso** (código ``2``), no de la corrida.

    ⚠️ La distinción no es cosmética: ``2`` es el código que ``argparse`` ya usa para
    "escribiste mal el comando", y un script que encadena comandos decide con él si
    vale la pena reintentar. Pasar una carpeta a un comando que necesita **un** archivo
    es un error de quien invoca —no una falla de la corrida— y cuando salía como
    ``1`` (o peor, como un ``ValueError`` sin capturar con traceback) el operador no
    podía distinguirlo de un documento ilegible.
    """


def exigir_archivo(ruta: Path, *, comando: str, alternativa: str) -> None:
    """Valida que ``ruta`` sea un archivo, con un error de uso que dice qué hacer.

    ⚠️ Existe porque pasar una **carpeta** a los comandos de un solo documento
    (``run``/``ask``/``arca``) no fallaba con un mensaje: ``procesar`` levantaba
    ``ValueError: Se esperaba un archivo, no un directorio``, que **no** capturaba
    nadie y salía por pantalla como traceback (lo peor: en ``run`` el orquestador
    atrapaba el error y reportaba "No se pudo leer el archivo <DIR>", que hace pensar
    en un archivo corrupto y no en un directorio).

    El mensaje nombra la alternativa real del comando —cada uno tiene una— en vez de un
    "ingresá un archivo" genérico.
    """
    if ruta.is_dir():
        raise ErrorDeUsoCLI(
            f"'{comando}' necesita un **archivo**, y {ruta} es una carpeta. "
            f"{alternativa}"
        )


# ---------------------------------------------------------------------------
# Construcción del parser
# ---------------------------------------------------------------------------


def _agregar_comunes(parser: argparse.ArgumentParser) -> None:
    """Opciones que comparten los subcomandos que llaman al pipeline.

    ``--force`` (E-CLI-1) reprocesa los documentos que ya tienen checkpoint
    válido: es la diferencia entre **reanudar** (default) y **rehacer** el lote
    (T-602). Un checkpoint de un documento que cambió de contenido no se
    reutiliza aunque no se pase ``--force``: el hash del archivo es lo que decide.
    """
    parser.add_argument(
        "--force",
        action="store_true",
        help=(
            "Reprocesa los documentos que ya tienen checkpoint válido. Sin el flag, "
            "el lote reanuda y saltea lo completado (E-CLI-1, T-602)."
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
            "Workers del modo batch (T-602): cada worker inicializa su propio "
            "convertidor de Docling. Con 1 worker el lote corre en el proceso "
            "actual (determinista)."
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

    p = sub.add_parser(
        "process",
        help="Procesa un archivo o carpeta a Markdown (F1).",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=(
            "Con -o, la salida ESPEJA el árbol desde la raíz de la entrada (sin\n"
            "repetir el nombre de la carpeta de entrada), igual que `corpus` y\n"
            "`pdf`:\n"
            "  var/fixtures/chicos/x.pdf  →  salida/chicos/x.md\n\n"
            "Sin -o el markdown va junto al documento de origen."
        ),
    )
    p.add_argument("origen", help="Archivo o carpeta a procesar.")
    p.add_argument(
        "-o",
        "--output",
        default=None,
        metavar="DIR",
        help=(
            "Directorio de salida (default: junto al archivo). El árbol se "
            "espeja desde --raiz (o desde el nivel que no sea un mes)."
        ),
    )
    p.add_argument(
        "--raiz",
        type=Path,
        help=(
            "Raíz desde la cual se espeja el árbol en la salida. Si se omite, se "
            "sube hasta el nivel que no sea un mes, así la MISMA entrada escribe "
            "siempre los MISMOS archivos."
        ),
    )
    p.add_argument("--raw", action="store_true", help="Markdown crudo de Docling (equivale a el modo crudo).")
    _agregar_comunes(p)

    p = sub.add_parser(
        "validate",
        help="Gate '¿es comprobante?' doble paso (F2).",
        description=(
            "Decide si un documento es un comprobante válido para rendición. "
            "Acepta un archivo o una **carpeta** (recursiva): con una carpeta "
            "devuelve un veredicto por documento, que es el uso de filtrar un "
            "corpus antes de procesarlo."
        ),
    )
    p.add_argument("origen", help="Archivo o carpeta (recursiva).")
    p.add_argument("--quick", action="store_true", help="Reservado: hoy el gate siempre hace doble paso (F2/T-203).")
    p.add_argument(
        "-o",
        "--output",
        default=None,
        metavar="SALIDA.json",
        help=(
            "Archivo JSON de salida (default: stdout). Con una carpeta conviene: "
            "la salida trae un veredicto por documento."
        ),
    )
    p.add_argument("--model", default=None, help="Modelo del gate (default: rol vlm).")

    p = sub.add_parser(
        "classify",
        help="Tipo/letra + cadena contable sobre un markdown (F3).",
        description=(
            "Determina el tipo de comprobante y corre la cadena contable. Acepta un "
            "archivo o una **carpeta** (recursiva): con una carpeta devuelve un "
            "resultado por documento."
        ),
    )
    p.add_argument(
        "origen",
        help="Archivo markdown/OCR, o carpeta (recursiva). Cualquier documento: se procesa con F1.",
    )
    p.add_argument("--condicion-impositiva", default=CONDICION_DEFAULT)
    p.add_argument("--model", default=None)
    p.add_argument(
        "-o",
        "--output",
        default=None,
        metavar="SALIDA.json",
        help="Archivo JSON de salida (default: stdout). Equivale a -o de la cadena contable original.",
    )

    p = sub.add_parser("extract", help="Extracción VLM+LLM combinada (F4).")
    p.add_argument("origen", help="Archivo o carpeta.")
    p.add_argument("-M", "--mode", default="kvi", help="Modo heredado (kvi/kvg/10/11).")
    p.add_argument("-o", "--output", default=None, help="Archivo JSON de salida (default: stdout).")
    _agregar_comunes(p)

    p = sub.add_parser("extract-detect", help="Detecta la letra por VLM/LLM (equivale a -M 11.1 del sistema anterior).")
    p.add_argument("origen", help="Archivo o carpeta.")
    p.add_argument("-o", "--output", default=None, help="Archivo JSON de salida (default: stdout).")
    _agregar_comunes(p)

    p = sub.add_parser("run", help="Pipeline completo de un archivo (F6).")
    p.add_argument("origen", help="Documento a procesar (un archivo; para una carpeta, `batch`).")
    p.add_argument("-o", "--output", default=None, help="Archivo JSON del resultado (default: stdout).")
    p.add_argument("--cases", default=None, metavar="DIR", help="Persiste el CaseRecord (sidecar + índice, F5/T-506).")
    p.add_argument("--clasificar-contable", action="store_true", help="Corre la cadena contable 01→02→03 (tres llamadas al modelo).")
    p.add_argument("--no-gate", action="store_true", help="No corre el gate de F2 antes de extraer.")
    p.add_argument("--no-agente", action="store_true", help="No escala al agente IA si el código no concluye.")
    _agregar_comunes(p)

    p = sub.add_parser("batch", help="Pipeline completo de una carpeta recursiva (F6/T-602: workers + checkpoints + enfriamiento).")
    p.add_argument("origen", help="Carpeta (o archivo) a procesar.")
    p.add_argument(
        "-o",
        "--output",
        default=None,
        metavar="AGREGADO.json",
        help=(
            "Archivo del **agregado** del lote (F6/T-603): una entrada por "
            "documento, la síntesis y las métricas. Se ACUMULA entre corridas "
            "(default: lote.agregado.json)."
        ),
    )
    p.add_argument("--cases", default=None, metavar="DIR", help="Persiste los CaseRecord (sidecar + índice).")
    p.add_argument("--clasificar-contable", action="store_true")
    p.add_argument("--no-gate", action="store_true")
    p.add_argument("--no-agente", action="store_true")
    p.add_argument(
        "--cooling",
        choices=["auto", "on", "off"],
        default="auto",
        help=(
            "Enfriamiento por temperatura (ADR-010): 'auto' usa la configuración "
            "(CoolingSettings.enabled), 'on'/'off' la fuerzan para esta corrida."
        ),
    )
    p.add_argument(
        "--work-window",
        type=int,
        default=None,
        metavar="S",
        help="Segundos de trabajo continuo antes de detener los workers (ADR-010; default: config).",
    )
    p.add_argument(
        "--cool-down",
        type=int,
        default=None,
        metavar="S",
        help=(
            "Segundos de enfriamiento, contados desde que TODOS los workers están "
            "detenidos (ADR-010; default: config)."
        ),
    )
    p.add_argument(
        "--no-checkpoints",
        action="store_true",
        help="Ignora y no escribe checkpoints: reprocesa todo el lote sin reanudar.",
    )
    _agregar_comunes(p)

    p = sub.add_parser("ask", help="Pregunta puntual sobre un documento (equivale a el cliente de preguntas original).")
    p.add_argument("origen", help="Documento a consultar (un archivo).")
    p.add_argument("-q", "--question", required=True, help="Pregunta a responder.")
    p.add_argument("--model", default=None)

    p = sub.add_parser("arca", help="Consulta el padrón ARCA/WSCDC (opcional, ADR-003).")
    p.add_argument("accion", choices=["check"], help="Acción a ejecutar.")
    p.add_argument("origen", help="Documento cuyo comprobante se quiere constatar (un archivo).")
    p.add_argument("--cuit", default=None, help="CUIT del emisor para la autenticación (si aplica).")
    p.add_argument("--url", default=None, help="URL del WSCDC (default: la de la configuración).")
    p.add_argument("--token", default=None, help="Token de autorización (flujo WSAA; fuera del MVP).")
    p.add_argument("--timeout", type=float, default=15.0)
    p.add_argument("--condicion-impositiva", default=CONDICION_DEFAULT)
    p.add_argument("--model", default=None)

    p = sub.add_parser("case", help="Consulta la trazabilidad persistida (F5/T-506) y el agregado del lote (T-603).")
    p.add_argument(
        "accion",
        choices=["show", "list", "aggregate"],
        help="show <documento_id> | list [--filtro] | aggregate (sintetiza el histórico en un JSON)",
    )
    p.add_argument("documento_id", nargs="?", help="Id del documento (para 'show').")
    p.add_argument("--dir", dest="dir_casos", default=".", help="Directorio de los sidecars (default: '.').")
    p.add_argument("--filtro", action="append", default=[], metavar="CAMPO=VALOR", help="Filtro del índice (repetible).")
    p.add_argument(
        "-o",
        "--output",
        default=None,
        metavar="AGREGADO.json",
        help="Destino del agregado (solo para 'aggregate'; default: stdout).",
    )

    p = sub.add_parser("hitl", help="Consulta la cola de revisión humana (F5/T-505).")
    p.add_argument("accion", choices=["list"], help="Acción a ejecutar.")
    p.add_argument("--dir", dest="dir_casos", default=None, help="Directorio de sidecars (índice, F5/T-506).")
    p.add_argument("--certeza", choices=["alta", "baja"], default=None, help="Filtra por certeza del caso.")
    p.add_argument("--prioridad", choices=["alta", "baja"], default=None, help="Filtra por prioridad de la revisión.")

    _cmd_corpus_parser(sub)

    _cmd_pdf_parser(sub)

    p = sub.add_parser(
        "stop",
        help="Para las corridas que están en curso (y sus subprocesos).",
        description=(
            "Frena los comandos largos que estén corriendo (process/batch/corpus/pdf): "
            "manda SIGTERM al grupo de procesos, espera y, si no muere, escala a SIGKILL. "
            "Sin argumentos los para todos."
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=(
            "Sin --force le da a la corrida la chance de terminar el documento en curso\n"
            "(lo que ya escribió queda y la próxima corrida lo saltea). Con --force va\n"
            "directo al SIGKILL.\n"
        ),
    )
    p.add_argument(
        "--force",
        action="store_true",
        help="SIGKILL directo, sin esperar a que termine el documento en curso.",
    )
    p.add_argument(
        "--espera",
        type=float,
        default=None,
        metavar="S",
        help=(
            "Segundos a esperar tras el SIGTERM antes de escalar a SIGKILL "
            f"(default: {ESPERA_DEFECTO:g})."
        ),
    )
    p.add_argument(
        "--id",
        default=None,
        metavar="ID",
        help="Para una corrida puntual (el id lo lista `stop` sin argumentos).",
    )
    p.add_argument(
        "--json",
        action="store_true",
        help="Salida JSON (default: texto legible).",
    )

    return parser


# ---------------------------------------------------------------------------
# Helpers de salida
# ---------------------------------------------------------------------------


def _json_salida(datos: Any, destino: str | None, entorno: EntornoCLI) -> None:
    """Escribe ``datos`` como JSON: a archivo si hay destino, si no a stdout.

    ⚠️ **La escritura a archivo es atómica** (:func:`persistencia.escribir_atomico`), y no
    por prolijidad: varios de estos destinos los escribe **también** otra ruta del sistema.
    El caso concreto es el agregado del lote — ``batch -o A.json`` lo escribe con
    :func:`trace.agregado.escribir_agregado` (atómico) y ``case aggregate -o A.json``
    llegaba acá con un ``write_text`` pelado. El mismo archivo escrito por dos caminos con
    garantías distintas es un riesgo silencioso: el ``write_text`` deja el archivo truncado
    a la mitad si el proceso muere, y la corrida siguiente lo lee como un agregado válido
    (o peor, lo pisa con la mitad del histórico).

    El formato se conserva byte a byte (``ensure_ascii=False``, ``indent=2`` y el salto de
    línea final que ya tenía), así que los dos caminos producen **el mismo** archivo.
    """
    texto = json.dumps(datos, ensure_ascii=False, indent=2)
    if destino:
        ruta = entorno.ruta(destino)
        # ``escribir_atomico`` crea el árbol de salida y escribe con temporal + fsync.
        escribir_atomico(ruta, texto + "\n")
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


def _resumen_de_entradas(salidas: Sequence[dict[str, Any]]) -> list[dict[str, Any]]:
    """Resumen de un lote de ``extract``: una línea por documento, sin la evidencia.

    ⚠️ No es la salida del comando (el contrato de ``stdout`` es el JSON completo, ver
    :func:`_salida_de_lote`): es el material del **log** para lotes largos, donde una
    línea por documento se lee y el detalle por campo no.
    """
    resumen: list[dict[str, Any]] = []
    for entrada in salidas:
        if entrada.get("error"):
            resumen.append({"archivo": entrada.get("archivo"), "error": entrada["error"]})
            continue
        campos = ((entrada.get("evidencia") or {}).get("campos")) or {}
        fila: dict[str, Any] = {
            "archivo": entrada.get("archivo"),
            "modo": entrada.get("modo"),
            "campos": len(campos),
        }
        # `extract-detect` no trae `evidencia` (trae la letra suelta).
        for clave in ("tipo_comprobante", "certeza", "origen"):
            if clave in entrada:
                fila[clave] = entrada[clave]
        resumen.append(fila)
    return resumen


# ---------------------------------------------------------------------------
# Implementación de cada subcomando
# ---------------------------------------------------------------------------


def _cmd_process(args: argparse.Namespace, entorno: EntornoCLI) -> int:
    """``process``: procesa con F1 y escribe el markdown.

    Con ``--raw`` (equivalente a el modo crudo) el markdown es el **crudo** de
    Docling y va a ``<doc>.raw.md`` — nunca encima del documento de entrada. Es el
    nombre heredado y evita el peor desenlace posible: pisar el archivo de
    origen de la corrida (T-604).

    Con ``-o`` la salida **espeja el árbol** desde una raíz estable, con la misma
    regla que ``corpus`` y ``pdf`` (:func:`corpus.recorrido.salida_de`). Sin el
    espejado, procesar ``var/fixtures`` con ``-o`` aplanaba todos los ``.md`` en el
    nivel raíz de la salida: dos documentos homónimos de carpetas distintas
    escribían el MISMO archivo y uno se perdía en silencio, y no había forma de
    saber de qué documento era cada markdown. La regla **no se reimplementa acá**:
    una segunda copia de dónde va cada archivo es exactamente lo que divergencea y
    hace pagar dos veces (ver ``corpus/recorrido.py``).

    **Reanuda**: un documento cuyo markdown ya existe **y tiene contenido** se
    saltea (``--force`` lo rehace). Antes reprocesaba todo cada vez — medido: el
    61% del tiempo de una corrida con PDF nativos, y en un PDF escaneado el camino
    OCR se paga entero de nuevo. La regla es la misma de ``pdf`` y ``corpus``
    (:func:`persistencia.ya_escrito`) y el proceso es **determinista** (dos corridas
    dan markdown byte a byte idéntico), así que saltear no cambia la salida.

    ⚠️ Es reanudación por **existencia**, no por frescura: si se cambia
    ``--orientation`` (o ``--raw``), el destino ya escrito se reutiliza. Se
    **declara** en el log cuántos se saltearon para que el cambio de opciones sin
    ``--force`` no pase inadvertido.

    **Un documento que falla no corta el lote.** Es la regla de ``corpus``, ``pdf`` y
    ``extract``, y acá es más crítica que en ellos: ``processing`` rechaza lo que no
    parece un documento (una foto con relación de aspecto extrema, un archivo
    ilegible) con :class:`~voucherflow.api.DocumentoNoProcesableError`, y esa excepción
    **no** la capturaba el comando — abortaba la corrida entera con un traceback. Medido
    sobre el corpus real: un solo archivo con aspecto 4,92 tumbaba los 3.729 pendientes
    después de haber recorrido 117. Ahora el fallo se anota, se declara y la corrida
    sigue; los que fallaron van a un archivo para reintentarlos con ``--forzar`` y el
    código de salida es ``1`` (igual que ``extract``).
    """
    raiz = entorno.ruta(args.origen)
    documentos = iterar_documentos(raiz, extensiones=_extensiones_de_proceso())
    if not documentos:
        raise ErrorCLI(
            f"No hay documentos procesables en {raiz}. Formatos soportados: "
            "pdf/imagen/office/texto."
        )

    with _corrida_anotada(entorno, "process"):
        salida = entorno.ruta(args.output) if args.output else None
        if salida is not None:
            # La salida no puede ser su propia entrada: si ``-o`` cae dentro de la
            # entrada, un ``.md`` recién escrito sería un documento procesable en la
            # corrida siguiente (misma guarda que ``corpus``, que la aplica siempre).
            documentos = [d for d in documentos if not esta_dentro(d, salida)]
            if not documentos:
                raise ErrorCLI(
                    f"Todos los documentos de {raiz} están dentro de la salida "
                    f"({salida}): no hay nada que procesar fuera de ella."
                )

        raiz_explicita = entorno.ruta(str(args.raiz)) if args.raiz else None
        raiz_efectiva, motivo = raiz_espejado([raiz], raiz_explicita, salida)
        if salida is not None:
            entorno.log(f"raíz de espejado : {raiz_efectiva}   [{motivo}]")
            entorno.log(f"salida           : {salida}")
            _declarar_fuera_de_la_raiz(documentos, raiz_efectiva, motivo, entorno)

        reanudados = 0
        fallos = 0
        fallos_detalle: list[dict[str, str]] = []
        for ruta in documentos:
            destino = _destino_markdown(ruta, salida, raiz_efectiva, raw=args.raw)
            # ⚠️ El skip va ANTES de procesar: el punto es no pagar la conversión.
            if not args.force and ya_escrito(destino):
                reanudados += 1
                entorno.log(f"· saltado: {destino} (ya existía; usá --force)")
                continue
            try:
                documento = entorno.orch().procesar(
                    ruta, docling_raw=args.raw, orientation=args.orientation
                )[0]
            except Exception as exc:  # noqa: BLE001 - un documento no tumba el lote
                # ⚠️ NO se atrapa `Exception` por comodidad: `processing` rechaza lo que
                # no parece un documento, y sin esto una sola foto rara abortaba la
                # corrida entera (con traceback y sin escribir nada de lo pendiente).
                fallos += 1
                fallos_detalle.append({"archivo": str(ruta), "error": str(exc)})
                entorno.log(f"ERROR: {ruta}: {exc}")
                continue
            # La escritura es atómica (igual que `corpus` y `pdf`): un markdown a medio
            # escribir con el nombre final rompería la reanudación de la próxima
            # corrida, que lo daría por bueno.
            escribir_atomico(destino, documento.markdown or "")
            entorno.log(f"OK: {destino} (orientación={documento.orientacion})")

        if reanudados:
            entorno.log(
                f"{reanudados} documento(s) salteado(s) por estar ya procesados "
                "(--force para rehacerlos)."
            )
        if fallos:
            _escribir_fallos(fallos_detalle, salida, entorno)
    return 1 if fallos else 0


def _escribir_fallos(
    fallos: list[dict[str, str]], salida: Path | None, entorno: EntornoCLI
) -> None:
    """Declara los documentos que fallaron, y los deja en un archivo si hay ``-o``.

    ⚠️ El archivo no es un detalle de comodidad: es la lista de **qué reintentar**. La
    reanudación saltea lo que **existe**, así que un fallo simplemente no deja markdown
    y la próxima corrida lo vuelve a intentar sola; pero saber *cuáles* son y *por qué*
    es lo que permite decidir si conviene arreglar algo antes (por ejemplo, si los
    rechazos son todos "relación de aspecto extrema", el problema es el corpus, no el
    comando). Se escribe con nombre propio (``fallos.json``) para no confundirlo con un
    markdown y que ``iterar_documentos`` no lo tome como entrada.
    """
    por_motivo: dict[str, int] = {}
    for fallo in fallos:
        # El motivo se agrupa por su forma general, no por el archivo: es lo que
        # convierte 40 líneas en un diagnóstico.
        clave = fallo["error"].split("(")[0].strip()[:160]
        por_motivo[clave] = por_motivo.get(clave, 0) + 1
    entorno.log(f"{len(fallos)} documento(s) fallaron (no se escribió su markdown):")
    for motivo, cantidad in sorted(por_motivo.items(), key=lambda kv: -kv[1]):
        entorno.log(f"     {cantidad:>5} × {motivo}")
    if salida is None:
        entorno.log(
            "     (con -o DIR el detalle por archivo queda en 'fallos.json')"
        )
        return
    destino = salida / NOMBRE_FALLOS
    escribir_json_atomico(
        destino, {"version": VERSION_CLI, "total": len(fallos), "fallos": fallos}
    )
    entorno.log(f"     detalle: {destino}")


def _declarar_fuera_de_la_raiz(
    documentos: Sequence[Path], raiz: Path, motivo: str, entorno: EntornoCLI
) -> None:
    """Avisa si ``--raiz`` no contiene a los documentos: el destino sale PLANO.

    ``salida_de`` cae al nombre suelto cuando la raíz no es ancestro del archivo,
    así que un ``--raiz`` mal puesto produce el mismo síntoma que el bug que este
    cambio arregla (todo en el nivel raíz). Se declara en vez de dejarlo silencioso:
    un espejado que no espeja y no lo dice es lo que hace perder tiempo buscando la
    causa. Solo aplica a ``--raiz`` explícita: cuando la raíz se **asciende** desde
    las rutas, la contención está garantizada por construcción.
    """
    if not motivo.startswith("--raiz"):
        return
    fuera = [d for d in documentos if not esta_dentro(d, raiz)]
    if fuera:
        entorno.log(
            f"⚠  {len(fuera)} documento(s) no están bajo --raiz {raiz}: su salida "
            "queda plana (sin el nivel de carpetas)."
        )


def _extensiones_de_proceso() -> set[str]:
    """Extensiones de ``process``: las de Docling **más** el texto plano ``.txt``."""
    from ..models.docling import EXTENSIONES_SOPORTADAS

    return set(EXTENSIONES_SOPORTADAS) | {".txt", ".csv", ".log"}


# El espejado **no** se reimplementa acá: ``esta_dentro``/``raiz_espejado``/
# ``salida_de`` son las mismas funciones que usan ``corpus`` y ``pdf``, así que
# los tres comandos no pueden divergir en dónde escriben (una segunda copia de
# esa regla es exactamente lo que hace pagar dos veces; ver `corpus/recorrido.py`).


def _destino_markdown(
    ruta: Path,
    salida: Path | None,
    raiz: Path,
    *,
    raw: bool = False,
) -> Path:
    """Resuelve el markdown de salida (junto al archivo o espejando en ``-o DIR``).

    Con ``raw=True`` el sufijo es ``.raw.md`` (la convención de el modo crudo):
    el crudo y el markdown ordenado son **dos artefactos distintos** y no pueden
    compartir archivo — escribirlos en el mismo lugar haría que la segunda corrida
    pisara a la primera (T-604).

    Con ``salida``, el destino sale de :func:`corpus.recorrido.salida_de` (la
    función única del espejado, compartida con ``corpus`` y ``pdf``) y solo se le
    cambia la extensión: así ``var/fixtures/chicos/x.pdf`` escribe
    ``salida/chicos/x.md`` y dos homónimos de carpetas distintas no colisionan.

    **Nunca devuelve la ruta de entrada.** ``process`` acepta documentos que ya son
    texto (``.md``/``.txt``), y para esos el sufijo coincide con el del archivo: sin
    este chequeo la corrida **sobrescribiría su propia entrada** (el sistema anterior no corría el
    riesgo porque su lista de extensiones no incluía texto plano). Cuando el destino
    colisiona se usa ``<doc>.processed.md`` / ``<doc>.processed.raw.md`` y se declara
    en el log: un nombre distinto es infinitamente mejor que perder el original.
    """
    sufijo = ".raw.md" if raw else ".md"
    if salida is not None:
        destino = salida_de(ruta, raiz, salida, "").with_suffix(sufijo)
    else:
        destino = ruta.with_suffix(sufijo)

    if _es_la_entrada(destino, ruta):
        destino = ruta.with_name(f"{ruta.stem}.processed{sufijo}")
    return destino


def _es_la_entrada(destino: Path, ruta: Path) -> bool:
    """True si el destino es el **mismo archivo** que la entrada.

    Se comparan las rutas resueltas: con ``-o`` el destino se arma con una raíz
    distinta de la que trajo el recorrido, así que un ``==`` textual puede no
    detectar la colisión (y el desenlace de no detectarla es perder el original).
    """
    try:
        return destino.resolve() == ruta.resolve()
    except OSError:  # pragma: no cover - rutas raras del sistema
        return destino == ruta


def _cmd_validate(args: argparse.Namespace, entorno: EntornoCLI) -> int:
    """``validate``: gate doble paso (F2) sobre un archivo o una carpeta.

    Con una **carpeta** (o varios documentos) devuelve un veredicto por documento y sale
    con ≠ 0 solo si **ningún** documento es comprobante: es la forma de filtrar un
    corpus antes de gastar la extracción, que es justo lo que la guía documentaba y el
    comando no hacía (pasaba la carpeta a `procesar` y moría con un `ValueError`
    crudo — el traceback sin capturar que se veía era ese).

    ⚠️ El código de salida separa dos cosas que no son lo mismo:

    * **un archivo** → ``0`` si es comprobante, ``1`` si no (contrato original, T-601);
    * **un lote** → ``0`` si *alguno* lo es, ``1`` si **ninguno** lo es (y ``1`` si hubo
      fallos). Devolver ``1`` cuando *alguno* era válido haría que un `&&` en un script
      cortara el flujo por buenos documentos; y devolver ``0`` con **todos** rechazados
      obligaría a parsear la salida para enterarse de que el corpus no sirve.
    """
    raiz = entorno.ruta(args.origen)
    documentos = iterar_documentos(raiz, extensiones=_extensiones_de_proceso())
    if not documentos:
        raise ErrorCLI(
            f"No hay documentos validables en {raiz}. Formatos soportados: "
            "pdf/imagen/office/texto."
        )

    salidas: list[dict[str, Any]] = []
    fallos = 0
    comprobantes = 0
    for ruta in documentos:
        try:
            resultado = entorno.orch().validar(
                entorno.orch().procesar(ruta)[0], modelo=args.model
            )
            veredicto = resultado.veredicto_final.value
            if veredicto == "comprobante":
                comprobantes += 1
            salidas.append(
                {
                    "archivo": str(ruta),
                    "veredicto_final": veredicto,
                    "vista_fiel_preparada": resultado.vista_fiel is not None,
                    "pasadas": [
                        {
                            "vista": p.vista_usada,
                            "veredicto": p.veredicto.value,
                            "confianza": p.confianza_fuente,
                        }
                        for p in resultado.pasadas
                    ],
                }
            )
            entorno.log(f"OK: {ruta} → {veredicto}")
        except Exception as exc:  # noqa: BLE001 - un documento no tumba el lote
            # Misma regla que `process`: un documento que no se puede leer se declara y
            # la corrida sigue (antes acá el fallo subía hasta `main` como traceback).
            fallos += 1
            salidas.append({"archivo": str(ruta), "error": str(exc)})
            entorno.log(f"ERROR: {ruta}: {exc}")

    # Con UN documento la salida mantiene la forma de siempre (un objeto, no una lista):
    # es el contrato de T-601 y hay scripts que leen `veredicto_final` de la raíz.
    payload: Any = salidas[0] if len(documentos) == 1 else salidas
    _salida_lote_o_objeto(payload, salidas, args.output, entorno)

    if len(documentos) == 1:
        return 0 if comprobantes else 1
    if fallos:
        entorno.log(f"{fallos} documento(s) fallaron.")
    if not comprobantes:
        entorno.log("Ningún documento del lote resultó comprobante.")
        return 1
    entorno.log(f"{comprobantes} de {len(documentos)} documento(s) son comprobante.")
    return 0


def _salida_lote_o_objeto(
    payload: Any, salidas: list[dict[str, Any]], destino: str | None, entorno: EntornoCLI
) -> None:
    """Escribe el resultado del lote (o del documento único) con el aviso de tamaño.

    El aviso es el mismo criterio que ``extract``: el contrato de ``stdout`` no cambia,
    pero un lote grande por ``stdout`` se declara (el detalle por pasada no es lo que se
    mira cuando se filtra un corpus, y con ``-o`` queda en un archivo).
    """
    if destino:
        _json_salida(payload, destino, entorno)
        entorno.log(f"{len(salidas)} documento(s) en {destino}")
        return
    texto = json.dumps(payload, ensure_ascii=False, indent=2)
    if len(texto) > LIMITE_STDOUT_BYTES:
        entorno.log(
            f"⚠  {len(salidas)} documento(s) → {len(texto) / 1e6:.1f} MB por stdout. "
            "Con -o ARCHIVO.json el detalle va a un archivo."
        )
    entorno.dato(texto)


def _cmd_classify(args: argparse.Namespace, entorno: EntornoCLI) -> int:
    """``classify``: tipo/letra + cadena contable (F3) sobre un archivo o una carpeta.

    Con una carpeta devuelve un resultado por documento (tipo, certeza y cadena
    contable), que es el uso natural del comando sobre un lote ya procesado. El
    checkpoint de la cadena contable queda **junto a cada documento** (T-304), así que
    la reanudación ya funcionaba por documento sin que el comando lo supiera.

    ⚠️ El código de salida de un lote separa los dos casos que no son lo mismo:
    ``0`` si **todas** las cadenas contables terminaron, ``1`` si alguna falló. Con un
    archivo se conserva el contrato de siempre (``0``/``1`` según ``detalle_contable``).
    """
    raiz = entorno.ruta(args.origen)
    documentos = iterar_documentos(raiz, extensiones=_extensiones_de_proceso())
    if not documentos:
        raise ErrorCLI(
            f"No hay documentos clasificables en {raiz}. Formatos soportados: "
            "pdf/imagen/office/texto."
        )

    from ..classification.tipo_comprobante import clasificar_tipo_comprobante
    from ..rules.contexto import ContextoTipoComprobante

    salidas: list[dict[str, Any]] = []
    fallos = 0
    for ruta in documentos:
        try:
            if ruta.suffix.lower() in EXTENSIONES_TEXTO:
                # El checkpoint de la cadena contable vive junto al documento, así que
                # un `.md` ya procesado se lee como texto (no se vuelve a convertir).
                markdown = ruta.read_text(encoding="utf-8")
            else:
                markdown = entorno.orch().procesar(ruta)[0].markdown

            tipo = clasificar_tipo_comprobante(
                ContextoTipoComprobante(texto_encabezado_llm=markdown)
            )
            clasificacion, detalle_contable = entorno.orch().clasificar_contable(
                markdown,
                condicion_impositiva=args.condicion_impositiva,
                documento=ruta,
                modelo=args.model,
            )
            if not detalle_contable.get("ok"):
                fallos += 1
            salidas.append(
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
                }
            )
            entorno.log(f"OK: {ruta} → letra {tipo.letra!r}")
        except Exception as exc:  # noqa: BLE001 - un documento no tumba el lote
            fallos += 1
            salidas.append({"archivo": str(ruta), "error": str(exc)})
            entorno.log(f"ERROR: {ruta}: {exc}")

    # Con UN documento, la salida mantiene la forma de siempre (un objeto), igual que en
    # `validate`: es el contrato de T-601 y hay consumidores que leen de la raíz.
    payload: Any = salidas[0] if len(documentos) == 1 else salidas
    _salida_lote_o_objeto(payload, salidas, args.output, entorno)

    if len(documentos) == 1:
        return 0 if not fallos else 1
    if fallos:
        entorno.log(f"{fallos} de {len(documentos)} documento(s) fallaron.")
    return 1 if fallos else 0


def _cmd_extract(args: argparse.Namespace, entorno: EntornoCLI) -> int:
    """``extract``: extracción VLM+LLM combinada (F4) por archivo o carpeta.

    **Reanuda y acumula.** Si ``-o`` apunta a un archivo de una corrida anterior, las
    entradas de los documentos que **no cambiaron** se reutilizan (no se vuelve a
    consultar al modelo) y las nuevas se **suman** a las viejas en vez de reemplazarlas.
    ``--force`` rehace todo.

    ⚠️ Antes reprocesaba todo y **sobreescribía** el archivo: correr sobre una carpeta ya
    hecha costaba 2 llamadas al modelo por documento y **perdía** el resultado anterior.
    No alcanzaba con saltear los archivos: el markdown que ``process`` escribe **al lado**
    del documento es un documento de entrada válido, así que sólo mirar "¿existe la
    salida?" habría salteado el PDF y su markdown como si fueran el mismo trabajo.
    """
    raiz = entorno.ruta(args.origen)
    documentos = iterar_documentos(raiz)
    if not documentos:
        raise ErrorCLI(f"No hay documentos extraíbles en {raiz}.")

    destino = entorno.ruta(args.output) if args.output else None
    previas = _leer_previas(destino) if destino else []
    reusables = (
        {} if args.force else _reusables_de_extract(previas, modo=args.mode)
    )

    # Las entradas que NO se reusan (de otro modo, con error, u otra versión) no se
    # pierden: el archivo es el estado de la carpeta, así que se reescriben tal cual.
    otras: list[dict[str, Any]] = [
        entrada
        for entrada in previas
        if not (
            isinstance(entrada, dict)
            and isinstance(entrada.get("documento_id"), str)
            and entrada["documento_id"] in reusables
        )
    ]

    salidas: list[dict[str, Any]] = []
    fallos = 0
    reanudados = 0
    for ruta in documentos:
        try:
            documento_id = identificador_de_archivo(ruta)
            if documento_id in reusables:
                salidas.append(reusables[documento_id])
                reanudados += 1
                entorno.log(f"· saltado: {ruta} (ya extraído; usá --force)")
                continue
            evidencia = _extraer_archivo(ruta, args.mode, args, entorno)
            salidas.append(
                {
                    "archivo": str(ruta),
                    "documento_id": evidencia.documento_id,
                    "modo": args.mode,
                    MARCA_EXTRACT: {"version_cli": VERSION_CLI},
                    "evidencia": evidencia.model_dump(mode="json"),
                }
            )
            entorno.log(f"OK: {ruta} ({len(evidencia.campos)} campos)")
        except Exception as exc:  # noqa: BLE001 - un documento no tumba el lote
            fallos += 1
            salidas.append({"archivo": str(ruta), "error": str(exc)})
            entorno.log(f"ERROR: {ruta}: {exc}")

    if reanudados:
        entorno.log(
            f"{reanudados} documento(s) reutilizado(s) de la corrida anterior "
            "(--force para rehacerlos)."
        )
    _salida_de_lote([*otras, *salidas], destino, entorno)
    return 1 if fallos else 0


def _extraer_archivo(
    ruta: Path,
    mode: str,
    args: argparse.Namespace,
    entorno: EntornoCLI,
    documento_id: str | None = None,
) -> Any:
    """Extrae y combina la evidencia de un archivo (F4), reutilizando el orquestador.

    ``documento_id`` se acepta para no volver a hashear el archivo: el llamador ya lo
    calcula para decidir si puede reutilizar una extracción previa.
    """
    orch = entorno.orch()
    documento, _ = orch.procesar(ruta, orientation=args.orientation)
    gate = orch.validar(documento, modelo=args.model)

    documento_id = documento_id or identificador_de_archivo(ruta)
    extraccion = orch.extraer(
        documento,
        vista=gate.vista_fiel,
        documento_id=documento_id,
        modelo=args.model,
    )
    evidencia = orch.combinar(documento_id, extraccion.evidencias)
    from ..schemas.evidence import derivar_evidencia

    return derivar_evidencia(
        evidencia,
        cli_extract={
            "mode_heredado": mode,
            "version_cli": VERSION_CLI,
            "nota": (
                "El modo heredado (kvi/kvg/10/11) se registra para la paridad de "
                "T-604; el contrato de extracción del paquete es uno solo."
            ),
        },
    )


def _cmd_extract_detect(args: argparse.Namespace, entorno: EntornoCLI) -> int:
    """``extract-detect``: letra por VLM/LLM (equivale al modo histórico ``-M 11.1``).

    Reanuda y acumula con la misma regla que ``extract`` (misma clave: el
    ``documento_id``, que es el hash del contenido). El contrato de cada entrada es
    distinto (letra + certeza en vez de evidencia por campo), así que se guardan bajo
    otra marca: una entrada de ``extract`` no se puede reusar como una letra.
    """
    raiz = entorno.ruta(args.origen)
    documentos = iterar_documentos(raiz)
    if not documentos:
        raise ErrorCLI(f"No hay documentos en {raiz}.")

    from ..classification.evidencia import leer_evidencia
    from ..classification.tipo_comprobante import clasificar_tipo_comprobante

    destino = entorno.ruta(args.output) if args.output else None
    previas = _leer_previas(destino) if destino else []
    reusables = (
        {}
        if args.force
        else _reusables_de_extract(previas, modo=MARCA_DETECT, version=VERSION_CLI)
    )
    propias: set[str] = set()
    otras: list[dict[str, Any]] = []
    for entrada in previas:
        if not isinstance(entrada, dict):
            continue
        clave = entrada.get("documento_id")
        if isinstance(clave, str) and clave in reusables:
            propias.add(clave)
        else:
            otras.append(entrada)

    salidas: list[dict[str, Any]] = []
    fallos = 0
    reanudados = 0
    for ruta in documentos:
        try:
            documento_id = identificador_de_archivo(ruta)
            if documento_id in reusables:
                salidas.append(reusables[documento_id])
                reanudados += 1
                entorno.log(f"· saltado: {ruta} (ya detectado; usá --force)")
                continue
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
                    "documento_id": documento_id,
                    "modo": MARCA_DETECT,
                    MARCA_EXTRACT: {"version_cli": VERSION_CLI},
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

    if reanudados:
        entorno.log(
            f"{reanudados} documento(s) reutilizado(s) de la corrida anterior "
            "(--force para rehacerlos)."
        )
    _salida_de_lote([*otras, *salidas], destino, entorno)
    return 1 if fallos else 0


def _salida_de_lote(
    salidas: list[dict[str, Any]], destino: Path | None, entorno: EntornoCLI
) -> None:
    """Escribe el lote a ``-o``, o lo imprime por ``stdout`` (contrato de T-604).

    ⚠️ **El contrato de ``stdout`` no se toca**: ``extract`` imprime el JSON completo
    sin ``-o`` desde T-601 y la guía del operador lo documenta (T-605). Lo que se
    agrega es la **advertencia de tamaño** cuando el lote es grande: la evidencia pesa
    ~11,7 KB por documento (medido), así que los 3.846 documentos del corpus son ~45 MB
    — una terminal los ahoga y un pipe los procesa nadie. Se avisa con el peso real
    calculado, no con una constante estimada, y se sugiere ``-o``.
    """
    if destino is not None:
        _json_salida(salidas, str(destino), entorno)
        entorno.log(f"{len(salidas)} documento(s) en {destino}")
        return

    texto = json.dumps(salidas, ensure_ascii=False, indent=2)
    if len(texto) > LIMITE_STDOUT_BYTES:
        entorno.log(
            f"⚠  {len(salidas)} documento(s) → {len(texto) / 1e6:.1f} MB por stdout. "
            "Con -o ARCHIVO.json el detalle va a un archivo y la terminal queda libre."
        )
    entorno.dato(texto)


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
    """``run``: pipeline completo de **un** archivo (F6/T-601).

    ``--force`` **no** cambia el resultado de ``run``: reprocesa siempre, porque un
    documento suelto no tiene lote del cual reanudar. El flag se declara así en la
    traza en vez de fingir un efecto (la reanudación por checkpoint es del lote,
    T-602).

    ⚠️ Con una **carpeta**, acá se cortaba con un error engañoso: el orquestador atrapa
    la falla y devuelve ``ok=False`` con "No se pudo leer el archivo <DIR>", que suena
    a archivo corrupto cuando el problema es que es un directorio. Ahora es un error de
    **uso** (código 2) que nombra `batch`.
    """
    ruta = entorno.ruta(args.origen)
    exigir_archivo(
        ruta,
        comando="run",
        alternativa="Para un lote, usá `batch <carpeta>` (workers, reanudación y agregado).",
    )
    resultado = entorno.orch().ejecutar(
        ruta,
        persistir=bool(args.cases),
        dir_salida=args.cases,
        **_opciones_pipeline(args),
    )
    if args.force:
        resultado.detalle["force"] = (
            "`run` reprocesa siempre (un documento suelto no tiene lote del cual "
            "reanudar): el checkpoint/reanudación con --force aplica a `batch` "
            "(T-602)."
        )
    _json_salida(_resultado_a_dict(resultado), args.output, entorno)
    entorno.log(_resumen_legible(resultado))
    return 0 if resultado.ok else 1


def _cmd_batch(args: argparse.Namespace, entorno: EntornoCLI) -> int:
    """``batch``: pipeline completo de una **carpeta recursiva** (F6/T-602).

    Corre el lote con workers, checkpoints y enfriamiento (ADR-010):

    - **workers**: ``--workers N`` → pool de procesos con **un convertidor de
      Docling por worker** (patrón ``init_worker`` del sistema anterior). Con 1 worker el lote
      corre en el proceso actual (determinista).
    - **checkpoints/reanudación**: cada documento completado deja
      ``<doc>.batch.json`` (con el hash de su contenido) y la corrida siguiente
      lo **saltea**; ``--force`` reprocesa. Un documento que **cambió** no se
      saltea aunque no se pase ``--force``.
    - **enfriamiento**: al vencer ``--work-window`` se detiene el pool y **recién
      entonces** arranca la cuenta de ``--cool-down`` (todos los workers
      detenidos, ADR-010). El último ciclo nunca enfría.

    La salida agregada incluye la **traza del lote**: workers aplicados,
    reanudados, ciclos con ``todos_detenidos_s`` y segundos enfriados. El
    formato del agregado canónico es T-603.
    """
    raiz = entorno.ruta(args.origen)
    from ..batch import ejecutar_lote

    politica = _politica_cooling(args, entorno)
    # ⚠️ El `with` envuelve al lote ENTERO (no solo el armado): es el comando que más
    # necesita poder frenarse, porque levanta un pool de procesos y `kill -9` al padre
    # deja los workers vivos (medido).
    with _corrida_anotada(entorno, "batch"):
        resultado_lote = ejecutar_lote(
            raiz,
            orquestador=entorno.orch(),
            max_workers=args.workers,
            force=args.force,
            persistir=bool(args.cases),
            dir_salida=args.cases,
            cooling=politica,
            checkpoints=None if not args.no_checkpoints else _CheckpointsDesactivados(),
            settings=entorno.orch().settings_efectivos(),
            **_opciones_pipeline(args),
        )
    resultados = resultado_lote.resultados
    traza = resultado_lote.traza

    if not resultados and not traza.reanudados:
        raise ErrorCLI(f"No hay documentos procesables en {raiz}.")

    ok = sum(1 for r in resultados if r.ok)

    # --- Salida agregada del lote (F6/T-603, E-CLI-2) ---------------------
    # Un único JSON consolidado: una entrada por documento (veredicto + puntero al
    # sidecar) más la síntesis del lote y las métricas. Se ACUMULA entre corridas,
    # así que el archivo es el estado de la carpeta y no el reporte de la última
    # corrida. Si el lote persistió los casos con --cases, las métricas salen del
    # histórico (F5/T-507); si no, el agregado lo declara.
    from ..trace.agregado import agregar_a_archivo

    casos = (
        [r.caso for r in resultados if r.caso is not None] if args.cases else []
    )
    agregado = agregar_a_archivo(
        args.output or (entorno.ruta("lote.agregado.json")),
        resultados=resultados,
        casos=casos,
        raiz=raiz,
        lote=traza.como_dict(),
    )
    resumen = agregado.resumen()

    entorno.log(
        f"Lote: {ok}/{len(resultados)} documentos procesados"
        + (f", {len(traza.reanudados)} reanudados" if traza.reanudados else "")
        + f" ({traza.max_workers_aplicado} worker(s), {traza.enfriamientos} enfriamiento(s))"
        + (
            f" | {resumen['requieren_revision']} requieren revisión"
            if resumen["requieren_revision"]
            else ""
        )
        + f" | agregado: {agregado.total} documento(s) en total"
    )
    if args.output:
        entorno.log(f"Agregado: {entorno.ruta(args.output)}")
    return 0 if (ok == len(resultados) and not traza.errores) else 1


def _politica_cooling(args: argparse.Namespace, entorno: EntornoCLI) -> Any:
    """Resuelve la política de enfriamiento de la corrida (ADR-010).

    ``--cooling auto`` respeta la configuración (``CoolingSettings.enabled``);
    ``on``/``off`` la fuerzan, y ``--work-window``/``--cool-down`` permiten
    acortar la ventana sin tocar el YAML — que es lo que hace testeable la
    política y utilizable una máquina más rápida o más lenta que la de referencia.
    """
    from ..settings.config import CoolingSettings

    base = entorno.orch().settings_efectivos().cooling
    enabled = base.enabled
    if args.cooling == "on":
        enabled = True
    elif args.cooling == "off":
        enabled = False
    return CoolingSettings(
        enabled=enabled,
        work_window_s=args.work_window or base.work_window_s,
        cool_down_s=args.cool_down if args.cool_down is not None else base.cool_down_s,
    )


class _CheckpointsDesactivados:
    """Almacén de checkpoints apagado (``--no-checkpoints``).

    Implementa el contrato mínimo que el runner usa (``es_reanudable``/``marcar``)
    sin tocar el filesystem: nada se reanuda y nada se escribe. Se declara aparte
    en vez de "limpiar" los checkpoints existentes: apagar el mecanismo no puede
    destruir el estado del lote.
    """

    habilitados = False

    def __init__(self) -> None:
        self.no_escribibles: list[str] = []

    def es_reanudable(self, documento: Any, *, force: bool = False) -> bool:
        return False

    def marcar(self, resultado: Any) -> None:
        return None


def _cmd_ask(args: argparse.Namespace, entorno: EntornoCLI) -> int:
    """``ask``: pregunta puntual (equivalente a el cliente de preguntas original).

    ⚠️ Con una carpeta, `api.ask` levantaba `ValueError` sin capturar → traceback en
    pantalla. Es un error de uso: no hay forma de "preguntar a una carpeta".
    """
    from ..api import ask as api_ask

    ruta = entorno.ruta(args.origen)
    exigir_archivo(
        ruta,
        comando="ask",
        alternativa=(
            "Para preguntar sobre varios documentos, corré el comando por archivo "
            "(o usá `extract` para sacar los campos de todos)."
        ),
    )
    respuesta = api_ask(
        str(ruta),
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
    from ..rules.contexto_conclusion import ContextoConclusion
    from ..rules.gaps import PresupuestoBusqueda, detectar_gaps

    ruta = entorno.ruta(args.origen)
    exigir_archivo(
        ruta,
        comando="arca check",
        alternativa=(
            "La constatación es de **un** comprobante: corré el comando por archivo."
        ),
    )
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


def _cmd_stop(args: argparse.Namespace, entorno: EntornoCLI) -> int:
    """``stop``: para las corridas en curso y sus subprocesos.

    **Por qué existe como comando y no como "acordate del Ctrl-C".** Un lote largo es un
    árbol de procesos (padre + workers del pool de ``batch``). Medido antes de escribirlo:
    ``kill -9`` al padre deja los workers **vivos y trabajando**, y un SIGINT directo al
    PID del padre no lo mata (``shutdown(wait=True)`` espera a los workers). Ctrl-C sirve
    solo si la corrida está en primer plano; una lanzada con ``&`` o ``nohup`` no tiene
    forma cómoda de frenarse.

    Sin argumentos para **todas** las corridas anotadas; con ``--id``, una. La lista de
    "qué está corriendo" sale del registro (``var/run``), y cada entrada se verifica
    contra el proceso real antes de señalarla: una entrada vieja (o un PID reciclado) se
    reporta como obsoleta y **no se mata nada**.
    """
    from ..corridas import ESPERA_DEFECTO, parar_todas

    var = _var_de_datos(entorno)
    espera = ESPERA_DEFECTO if args.espera is None else max(0.0, args.espera)

    # ── Sin --force y sin nada corriendo: informar y salir en 0 (no es un error) ──
    from ..corridas import leer, limpiar_obsoletas

    estado = leer(var)
    if not estado.vivas:
        obsoletas = limpiar_obsoletas(var, estado)
        if args.json:
            entorno.dato(
                json.dumps(
                    {"paradas": [], "obsoletas": [c.id for c in obsoletas]},
                    ensure_ascii=False,
                    indent=2,
                )
            )
        else:
            entorno.dato("No hay ninguna corrida de voucherflow en curso.")
            if obsoletas:
                entorno.log(
                    f"Se limpiaron {len(obsoletas)} entrada(s) obsoleta(s) del registro "
                    f"({', '.join(c.id for c in obsoletas)})."
                )
        return 0

    try:
        resultados = parar_todas(var, force=args.force, espera=espera, solo_id=args.id)
    except ValueError as exc:
        raise ErrorDeUsoCLI(str(exc)) from exc

    if args.json:
        entorno.dato(
            json.dumps(
                {"paradas": [r.como_dict() for r in resultados]},
                ensure_ascii=False,
                indent=2,
            )
        )
    else:
        for r in resultados:
            estado_txt = "parada" if r.termino else "NO se pudo parar"
            entorno.dato(
                f"{estado_txt}: {r.corrida.comando} (id {r.corrida.id}, "
                f"pid {r.corrida.pid}) — {r.senal_enviada} a {r.destino}"
            )
            if r.motivo:
                entorno.log(f"   {r.motivo}")

    # ⚠️ Un `stop` que corrió pero no logró parar algo NO puede salir en 0: el operador
    # necesita saber que el árbol sigue vivo (misma lógica que un fallo por documento).
    fallidos = [r for r in resultados if not r.termino]
    if fallidos:
        entorno.log(
            f"⚠  {len(fallidos)} corrida(s) siguen vivas. Probá de nuevo; si el proceso "
            "está en un estado que no acepta señales, hace falta `kill -9` a mano."
        )
        return 1
    return 0


def _var_de_datos(entorno: EntornoCLI) -> Path:
    """La raíz ``var/`` de datos, para el registro de corridas (``var/run``).

    Se resuelve por la configuración (``paths.var``) y **no** por un literal, para que
    una instalación con las rutas movidas siga teniendo un solo registro.
    """
    try:
        from ..settings.config import cargar_settings

        return cargar_settings().paths.resolver("var", entorno.cwd)
    except Exception:  # noqa: BLE001 - sin configuración legible, el default del repo
        return entorno.cwd / "var"


@contextmanager
def _corrida_anotada(
    entorno: EntornoCLI, comando: str
) -> Iterator[None]:
    """Anota la corrida en el registro mientras dura el ``with``.

    Hace las tres cosas que necesita un comando largo para poder ser frenado:

    1. **Sesión propia** (:func:`corridas.preparar_sesion`): le da al proceso un grupo
       propio. ⚠️ Es lo que hace **seguro** señalar el grupo — sin esto, el grupo es el del
       shell del operador y un ``killpg`` mataría su terminal (pasó durante el desarrollo
       de ``stop``).
    2. **Anotar** la entrada en ``var/run`` con pid, pgid e identidad (hora de arranque).
    3. **Desanotar** al salir, incluso por señal: si no, cada Ctrl-C dejaría una entrada
       (que el chequeo de identidad salva de matar a nadie, pero ensucia el registro).

    El id es el PID: dos corridas simultáneas no comparten entrada, y el operador puede
    referirse a una con ``stop --id <pid>``.
    """
    from ..corridas import anotar, desanotar, instalar_limpieza, preparar_sesion

    var = _var_de_datos(entorno)
    preparar_sesion()
    id_corrida = str(os.getpid())
    anotar(var, id=id_corrida, comando=comando)
    instalar_limpieza(var, id_corrida)
    try:
        yield
    finally:
        desanotar(var, id_corrida)


def _cmd_case(args: argparse.Namespace, entorno: EntornoCLI) -> int:
    """``case show|list|aggregate``: trazabilidad persistida y agregado del lote.

    Es la puerta de la **mitad de auditoría** de E-CLI-2: ``show`` devuelve el
    ``CaseRecord`` completo de un documento (la evidencia y la trazabilidad),
    ``list`` consulta el índice del histórico y ``aggregate`` **reconstruye** el
    JSON consolidado de la carpeta a partir de los sidecars (T-603), sin volver a
    correr el pipeline.
    """
    from ..trace.recorder import CaseRecorder

    recorder = CaseRecorder(entorno.ruta(args.dir_casos))

    if args.accion == "list":
        filtros = _filtros(args.filtro)
        filas = recorder.buscar(**filtros) if filtros else recorder.leer_indice()
        entorno.dato(json.dumps(filas, ensure_ascii=False, indent=2))
        return 0

    if args.accion == "aggregate":
        # Reconstruye el agregado desde el histórico: es el camino para una
        # carpeta procesada en varias sesiones (o para recuperar el agregado si
        # se perdió el archivo).
        from ..trace.agregado import agregado_del_recorder

        agregado = agregado_del_recorder(recorder)
        if args.output:
            _json_salida(agregado.como_dict(), args.output, entorno)
        else:
            entorno.dato(json.dumps(agregado.como_dict(), ensure_ascii=False, indent=2))
        resumen = agregado.resumen()
        entorno.log(
            f"Agregado del histórico: {resumen['documentos']} documento(s) "
            f"({resumen['requieren_revision']} requieren revisión)"
        )
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
def _cmd_corpus_parser(sub: argparse._SubParsersAction) -> None:
    """Registra el subcomando ``corpus`` (la CLI vive en ``voucherflow.corpus.cli``)."""
    from ..corpus.cli import agregar_parser

    agregar_parser(sub)


def _cmd_corpus(args: argparse.Namespace, entorno: EntornoCLI) -> int:
    """``corpus``: pre-reduce peso y tokens de visión de un corpus (F1..F5 no lo hacían).

    El adaptador solo conecta los flujos del entorno con el subcomando: la
    lógica (planificar, procesar, reportar) vive en ``voucherflow.corpus``.
    """
    from ..corpus.cli import EntornoCorpus
    from ..corpus.cli import main as main_corpus

    with _corrida_anotada(entorno, "corpus"):
        return main_corpus(
            args,
            entorno=EntornoCorpus(stdout=entorno.stdout, stderr=entorno.stderr),
        )


def _cmd_pdf_parser(sub: argparse._SubParsersAction) -> None:
    """Registra el subcomando ``pdf`` (la CLI vive en ``voucherflow.pdf.cli``)."""
    from ..pdf.cli import agregar_parser

    agregar_parser(sub)


def _cmd_pdf(args: argparse.Namespace, entorno: EntornoCLI) -> int:
    """``pdf``: convierte PDF a imágenes JPG (una por página) con PyMuPDF.

    El adaptador solo conecta los flujos del entorno con el subcomando: la
    lógica (planificar, renderizar, reportar) vive en ``voucherflow.pdf``.
    """
    from ..pdf.cli import EntornoPdf
    from ..pdf.cli import main as main_pdf

    with _corrida_anotada(entorno, "pdf"):
        return main_pdf(
            args,
            entorno=EntornoPdf(stdout=entorno.stdout, stderr=entorno.stderr),
        )


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
    "corpus": _cmd_corpus,
    "pdf": _cmd_pdf,
    "stop": _cmd_stop,
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
    except ErrorDeUsoCLI as exc:
        # ⚠️ ANTES que `ErrorCLI`: es una subclase, y el orden de las ramas es lo que
        # distingue "lo invocaste mal" (2) de "la corrida falló" (1).
        ctx.log(f"ERROR DE USO: {exc}")
        return 2
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

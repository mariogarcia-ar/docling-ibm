"""Runner de lotes con workers, checkpoints y enfriamiento (F6 / T-602).

**Fase**: F6 · **Tarea**: T-602 · **Épica**: E-CLI-1/E-CLI-3 · **ADR-010**.

T-601 dejó el lote **secuencial y determinista**, con el punto de extensión
declarado (`ejecutar_lote(max_workers=…)` + `CoolingSettings`). **T-602 lo
implementa**: el lote corre con workers —**cada uno con su propio convertidor de
Docling**—, con checkpoints/reanudación y con la política de enfriamiento del
ADR-010.

Las tres piezas del DoD
-----------------------

**(1) Workers.** ``EjecutorProcesos`` usa ``ProcessPoolExecutor`` con un
``initializer`` por worker, que es el patrón de ``v1/full_pipeline.py``
(``init_worker``): un convertidor de Docling por **proceso** (los modelos son
pesados y no se comparten entre procesos) y un cliente de modelos por proceso.
El trabajo cruza la frontera del proceso como **dict serializable**: el worker
devuelve el resultado como datos (``resultado_a_payload``) y el padre lo
reconstruye con el contrato (``resultado_desde_dict``). Así el lote no depende de
que los objetos internos —sesiones HTTP, convertidores— sean picklables.

**(2) Checkpoints y reanudación.** Cada documento completado deja un checkpoint
``<documento>.batch.json`` (escritura atómica) con su ``documento_id`` —el
``sha256`` del contenido—, el estado y el resumen. Volver a correr sobre la misma
carpeta **saltea lo ya completado** (Gherkin E-CLI-1: "retoma desde los
checkpoints sin repetir pasos completados") y ``--force`` lo reprocesa.

Un detalle que decide la honestidad de la reanudación: el checkpoint guarda el
**hash del contenido**, así que un documento que **cambió** no se saltea aunque
tenga checkpoint — se reprocesa. Reanudar por nombre de archivo afirmaría que el
documento es el mismo cuando ya no lo es. Y un checkpoint de un documento que
**falló** no se reutiliza: un error no es un paso completado.

**(3) Enfriamiento (ADR-010).** El requisito literal de `my_prompt.md` es que la
cuenta de los 2 minutos **arranque cuando TODOS los workers están detenidos**, es
decir desde la última parada. El runner lo hace por construcción: el ciclo de
trabajo se organiza en **olas** del tamaño del pool; al vencer la ventana de
trabajo (`work_window_s`) se termina el ciclo, se **detiene el pool**
(``shutdown(wait=True)``: no queda ningún worker vivo), **recién entonces** se
marca ``todos_detenidos`` y se duerme ``cool_down_s``. La traza registra los tres
instantes (inicio de ventana, todos detenidos, fin del enfriamiento), así que la
semántica es auditable y no una promesa.

Por qué olas y no un pool que se pausa
--------------------------------------
Un proceso detenido es lo que hace real la pausa térmica: no consume CPU ni
retiene los modelos cargados. Un pool "pausado" que sigue vivo no enfría nada.
Además, la ola da una unidad determinista de planificación: la ventana de trabajo
se evalúa entre olas, de modo que el lote no depende de cuánto tarde cada
documento, y con un reloj inyectado el enfriamiento se prueba **sin dormir**.

Determinismo y testeo (regla dura del repo)
-------------------------------------------
``EjecutorLote`` (el pool) y ``Reloj`` (el tiempo) se **inyectan**.
``EjecutorSerial`` corre en el proceso actual con un orquestador inyectado, y con
un reloj de prueba el ciclo de enfriamiento se ejercita sin procesos reales, sin
Ollama, sin Docling y **sin dormir**. La suite default no spawnea procesos.

Alcance de T-602 (lo que **no** hace)
-------------------------------------
- **No** define la salida agregada del lote (T-603): devuelve los resultados y la
  traza; el agregado canónico y su formato son T-603.
- **No** cambia la persistencia de la trazabilidad (F5/T-506): ``persistir`` usa
  el ``CaseRecorder`` tal cual.
- **No** reintenta documentos fallidos dentro de la misma corrida: un fallo queda
  registrado y **sin** checkpoint reutilizable, así que la corrida siguiente lo
  reprocesa (que es el reintento correcto: entre corridas, no en un loop).
- **No** mide temperaturas reales: la política es por tiempo de trabajo continuo,
  que es lo que el ADR-010 define.

Referencias: ADR-010, Gherkin E-CLI-1 (checkpoint/resumir) y E-CLI-3 (workers +
enfriamiento), doc 03 §10 (NFR), `ORCH-CLI.md` §3, F6-subplan §3.2, `my_prompt.md`.
"""

from __future__ import annotations

import json
import os
import tempfile
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable, Protocol, Sequence

from .orchestrator import (
    PipelineResult,
    identificador_de_archivo,
    iterar_documentos,
)
from .schemas.result import CaseRecord, VoucherResult
from .settings.config import CoolingSettings, Settings, cargar_settings

#: Versión del runner de lotes (se registra en la traza del lote).
VERSION_LOTE = "lote-batch@1"

#: Sufijo del checkpoint por documento. Se conserva junto al documento (patrón de
#: v1: ``<doc>_pipeline.json``) porque así la reanudación funciona sin tener que
#: pasar un directorio de salida, y ``iterar_documentos`` lo excluye del
#: descubrimiento.
SUFIJO_CHECKPOINT = ".batch.json"

#: Nombre del ejecutor serial (una sola ventana, sin pool de procesos).
EJECUTOR_SERIAL = "serial"

#: Nombre del ejecutor con pool de procesos.
EJECUTOR_PROCESOS = "procesos"


# ---------------------------------------------------------------------------
# Reloj (inyectable para probar el enfriamiento sin dormir)
# ---------------------------------------------------------------------------


class Reloj(Protocol):
    """Fuente de tiempo del runner: medir y esperar (ADR-010).

    Se inyecta para que el enfriamiento se pueda **verificar** sin esperar dos
    minutos reales: un reloj de prueba avanza cuando el runner duerme y registra
    cuánto se durmió.
    """

    def monotonic(self) -> float:  # pragma: no cover - contrato
        """Segundos monótonos (nunca retroceden)."""
        ...

    def dormir(self, segundos: float) -> None:  # pragma: no cover - contrato
        """Espera ``segundos`` (la pausa de enfriamiento)."""
        ...


class RelojReal:
    """Reloj de producción: ``time.monotonic`` + ``time.sleep``."""

    def monotonic(self) -> float:
        return time.monotonic()

    def dormir(self, segundos: float) -> None:
        # Un valor negativo no tiene sentido físico: se recorta a 0.
        time.sleep(max(0.0, segundos))


# ---------------------------------------------------------------------------
# Checkpoints por documento
# ---------------------------------------------------------------------------


def ruta_checkpoint(documento: str | Path) -> Path:
    """Ruta del checkpoint de un documento (``<doc>.batch.json``).

    Igual que en v1 (``<doc>_pipeline.json``) el checkpoint vive **junto al
    documento**: así la reanudación no exige recordar el directorio de salida y
    dos documentos homónimos en subcarpetas distintas no comparten archivo.
    """
    ruta = Path(documento)
    return ruta.with_name(f"{ruta.stem}{SUFIJO_CHECKPOINT}")


def _escribir_atomico(destino: Path, contenido: str) -> None:
    """Escribe ``contenido`` en ``destino`` de forma atómica (patrón F5/T-506).

    Temporal en el mismo directorio + ``os.replace``: si la corrida muere a mitad,
    el checkpoint anterior queda intacto y nunca se lee un JSON truncado — que en
    un mecanismo de reanudación sería peor que no tener checkpoint, porque
    haría saltear un documento con la marca de "completado".
    """
    destino.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporal = tempfile.mkstemp(
        dir=str(destino.parent), prefix=f".{destino.name}.", suffix=".tmp"
    )
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as archivo:
            archivo.write(contenido)
            archivo.flush()
            os.fsync(archivo.fileno())
        os.replace(temporal, destino)
    except BaseException:
        try:
            os.unlink(temporal)
        except OSError:
            pass
        raise


@dataclass(frozen=True)
class CheckpointDocumento:
    """Checkpoint de un documento completado (T-602).

    Campos:
        documento_id: ``sha256`` del **contenido** con el que se completó. Es lo
            que permite decidir si un checkpoint sigue siendo válido: si el
            archivo cambió, su hash cambia y el checkpoint no se reutiliza.
        archivo: ruta del documento (referencial).
        timestamp: cuándo se completó (UTC, ISO-8601).
        ok: si el documento terminó bien. Un documento que **falló** deja
            checkpoint (para auditar que se intentó) pero **no** se reutiliza.
        estado / certeza / origen / tipo_comprobante: resumen del veredicto, para
            poder reportar el lote sin abrir los sidecars.
        etapas: etapas completadas (trazabilidad corta).
        caso: nombre del sidecar del ``CaseRecord`` (F5/T-506) si se persistió.
        version: versión del formato del checkpoint.
    """

    documento_id: str
    archivo: str
    timestamp: str
    ok: bool
    estado: str | None = None
    certeza: str | None = None
    origen: str | None = None
    tipo_comprobante: str | None = None
    etapas: tuple[str, ...] = ()
    caso: str | None = None
    version: str = VERSION_LOTE

    def como_dict(self) -> dict[str, Any]:
        return {
            "version": self.version,
            "documento_id": self.documento_id,
            "archivo": self.archivo,
            "timestamp": self.timestamp,
            "ok": self.ok,
            "estado": self.estado,
            "certeza": self.certeza,
            "origen": self.origen,
            "tipo_comprobante": self.tipo_comprobante,
            "etapas": list(self.etapas),
            "caso": self.caso,
        }

    @classmethod
    def desde_dict(cls, datos: dict[str, Any]) -> "CheckpointDocumento":
        return cls(
            documento_id=str(datos.get("documento_id") or ""),
            archivo=str(datos.get("archivo") or ""),
            timestamp=str(datos.get("timestamp") or ""),
            ok=bool(datos.get("ok")),
            estado=datos.get("estado"),
            certeza=datos.get("certeza"),
            origen=datos.get("origen"),
            tipo_comprobante=datos.get("tipo_comprobante"),
            etapas=tuple(datos.get("etapas") or ()),
            caso=datos.get("caso"),
            version=str(datos.get("version") or VERSION_LOTE),
        )


class CheckpointsLote:
    """Lee y escribe los checkpoints por documento del lote (T-602).

    Una **escritura por documento**, del lado del padre: los workers solo
    procesan. Así no hay dos procesos escribiendo el mismo archivo y la
    reanudación queda con un único escritor.

    La escritura es **best-effort declarado**: si el directorio no se puede
    escribir (entrada de solo lectura), el documento **igual quedó procesado** y
    el checkpoint se reporta como no escribible en la traza, en lugar de tirar el
    trabajo hecho (mismo criterio que el índice del ``CaseRecorder``,
    F5/T-506).
    """

    def __init__(self, *, habilitados: bool = True) -> None:
        self.habilitados = habilitados
        self.no_escribibles: list[str] = []

    def leer(self, documento: str | Path) -> CheckpointDocumento | None:
        """Checkpoint del documento, o ``None`` si no hay o está ilegible.

        Un checkpoint corrupto **no** se reutiliza ni tumba la corrida: se trata
        como ausente (se reprocesa) y el archivo se reescribe al completar. Igual
        que el índice de T-506, un derivado roto no puede invalidar el trabajo.
        """
        if not self.habilitados:
            return None
        ruta = ruta_checkpoint(documento)
        if not ruta.is_file():
            return None
        try:
            datos = json.loads(ruta.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return None
        if not isinstance(datos, dict):
            return None
        return CheckpointDocumento.desde_dict(datos)

    def es_reanudable(self, documento: str | Path, *, force: bool = False) -> bool:
        """True si el documento se puede saltear (ya completado **con este contenido**).

        Tres condiciones, y las tres importan:

        1. ``force`` es ``False`` (``--force`` reprocesa lo completado);
        2. hay checkpoint y su ``ok`` es ``True`` — un error no es un paso
           completado: se reintenta en la próxima corrida;
        3. el ``documento_id`` del checkpoint coincide con el **hash actual** del
           archivo. Un documento que cambió no es el mismo documento: saltearlo
           afirmaría algo falso.
        """
        if force:
            return False
        checkpoint = self.leer(documento)
        if checkpoint is None or not checkpoint.ok:
            return False
        try:
            actual = identificador_de_archivo(documento)
        except OSError:
            return False
        return checkpoint.documento_id == actual

    def marcar(self, resultado: PipelineResult) -> CheckpointDocumento | None:
        """Escribe el checkpoint de un resultado (best-effort, atómico).

        Devuelve el checkpoint escrito, o ``None`` si no se pudo (el motivo queda
        en :attr:`no_escribibles`). Un documento sin ``archivo`` —o cuyo archivo ya
        no existe— no se puede checkpointear: no hay dónde ni qué reanudar. Escribir
        un checkpoint para un archivo ausente dejaría una marca que la reanudación
        siguiente no podría validar (su hash no se puede calcular), es decir un
        archivo muerto con apariencia de estado.
        """
        if not self.habilitados or not resultado.archivo:
            return None
        if not Path(resultado.archivo).is_file():
            return None

        from .trace.recorder import sidecar_para

        resumen = resultado.resumen or {}
        caso = None
        if resultado.detalle.get("persistencia"):
            caso = os.path.basename(str(resultado.detalle["persistencia"].get("sidecar") or ""))
        elif resultado.caso is not None:
            caso = sidecar_para(resultado.caso.documento_id)

        checkpoint = CheckpointDocumento(
            documento_id=resultado.documento_id or "",
            archivo=str(resultado.archivo),
            timestamp=datetime.now(timezone.utc).isoformat(),
            ok=bool(resultado.ok),
            estado=resultado.estado,
            certeza=resumen.get("certeza"),
            origen=resumen.get("origen"),
            tipo_comprobante=resumen.get("tipo_comprobante"),
            etapas=tuple(resultado.etapas_completadas),
            caso=caso,
        )
        destino = ruta_checkpoint(resultado.archivo)
        try:
            _escribir_atomico(
                destino,
                json.dumps(checkpoint.como_dict(), ensure_ascii=False, indent=2),
            )
        except OSError as exc:
            self.no_escribibles.append(f"{destino}: {exc}")
            return None
        return checkpoint

    def limpiar(self, documento: str | Path) -> bool:
        """Borra el checkpoint de un documento (``--force`` lo regenera igual).

        Sirve para el caso inverso: dejar constancia de que un documento **dejó**
        de estar completado. Devuelve ``True`` si había algo para borrar.
        """
        ruta = ruta_checkpoint(documento)
        try:
            ruta.unlink()
            return True
        except FileNotFoundError:
            return False
        except OSError:
            return False


# ---------------------------------------------------------------------------
# Ejecutores (el pool; inyectables)
# ---------------------------------------------------------------------------


def construir_trabajo(
    documento: str | Path,
    documento_id: str,
    opciones: dict[str, Any],
) -> dict[str, Any]:
    """Arma el trabajo de un documento (el payload que cruza al worker).

    Es un **dict de tipos simples** a propósito: es lo que se puede serializar
    entre procesos sin depender de que los objetos internos (sesiones HTTP,
    convertidores con modelos cargados) sean picklables. El ``documento_id`` lo
    calcula el padre (una sola lectura de hash por documento) y viaja para que el
    worker no lo recalcule ni pueda discrepar.
    """
    return {
        "ruta": str(documento),
        "documento_id": documento_id,
        "opciones": dict(opciones),
    }


def resultado_a_payload(resultado: PipelineResult) -> dict[str, Any]:
    """Vista serializable de un ``PipelineResult`` (frontera del proceso).

    Incluye el ``VoucherResult`` y el ``CaseRecord`` en su forma JSON: el lote
    transporta los **artefactos auditables**, no los objetos internos. La
    ``evidencia`` no viaja (es grande y vive en el sidecar, F5/T-506): el lote
    mueve el resultado y su trazabilidad, que es lo que el agregado necesita
    (T-603).
    """
    return {
        "documento_id": resultado.documento_id,
        "archivo": resultado.archivo,
        "ok": resultado.ok,
        "error": resultado.error,
        "etapas_completadas": list(resultado.etapas_completadas),
        "resumen": dict(resultado.resumen),
        "detalle": dict(resultado.detalle),
        "resultado": (
            resultado.resultado.model_dump(mode="json")
            if resultado.resultado is not None
            else None
        ),
        "caso": (
            resultado.caso.model_dump(mode="json")
            if resultado.caso is not None
            else None
        ),
    }


def resultado_desde_dict(datos: dict[str, Any]) -> PipelineResult:
    """Reconstruye el ``PipelineResult`` desde el payload del worker (T-602).

    Es el camino inverso de :func:`resultado_a_payload` y se valida contra los
    contratos congelados (``VoucherResult``/``CaseRecord``): si el payload no
    respeta el schema, falla acá y no más adelante con un objeto a medias.
    """
    resultado = (
        VoucherResult.model_validate(datos["resultado"])
        if datos.get("resultado")
        else None
    )
    caso = CaseRecord.model_validate(datos["caso"]) if datos.get("caso") else None
    return PipelineResult(
        documento_id=datos.get("documento_id"),
        archivo=datos.get("archivo"),
        ok=bool(datos.get("ok")),
        etapas_completadas=list(datos.get("etapas_completadas") or []),
        resumen=dict(datos.get("resumen") or {}),
        error=datos.get("error"),
        resultado=resultado,
        evidencia=None,
        caso=caso,
        detalle=dict(datos.get("detalle") or {}),
    )


class FuturoLote(Protocol):
    """Handle del trabajo enviado a un ejecutor (``resultado()`` bloquea).

    El runner conoce **este** contrato, no el de ``concurrent.futures``: el
    ``Future`` real expone ``result()``, así que el ejecutor de procesos lo
    **adapta** (:class:`_FuturoProceso`) en vez de filtrar la API de la stdlib al
    runner. Así cambiar de backend no cambia el resto del runner.
    """

    def resultado(self) -> dict[str, Any]:  # pragma: no cover - contrato
        ...


@dataclass
class FuturoListo:
    """Futuro ya resuelto (lo que devuelve el ejecutor serial y los dobles)."""

    payload: dict[str, Any]

    def resultado(self) -> dict[str, Any]:
        return self.payload


@dataclass
class _FuturoProceso:
    """Adapta un ``concurrent.futures.Future`` al contrato ``FuturoLote``.

    El ``Future`` real tiene ``result()``; el runner habla ``resultado()``. La
    diferencia se resuelve acá y no en quien consume: un detalle de la biblioteca
    estándar no debe condicionar el contrato del runner (y si algún día se cambia
    el backend, se adapta igual).
    """

    futuro: Any

    def resultado(self) -> dict[str, Any]:
        return self.futuro.result()


class EjecutorLote(Protocol):
    """Ciclo de vida del pool de workers (ADR-010, inyectable).

    Contrato mínimo del runner: **arrancar** el pool (es donde cada worker
    inicializa su convertidor), **enviar** un trabajo, **recolectar** una ola y
    **detener** el pool (``shutdown(wait=True)``: al volver, **todos** los workers
    están detenidos, que es lo que el ADR-010 necesita para empezar a contar el
    enfriamiento).
    """

    capacidad: int
    nombre: str

    def iniciar(self) -> None:  # pragma: no cover - contrato
        ...

    def enviar(self, trabajo: dict[str, Any]) -> FuturoLote:  # pragma: no cover
        ...

    def recolectar(
        self, futuros: Sequence[FuturoLote]
    ) -> list[dict[str, Any]]:  # pragma: no cover - contrato
        ...

    def detener(self) -> None:  # pragma: no cover - contrato
        ...


class EjecutorSerial:
    """Ejecutor en el proceso actual, sin pool (T-602; también el de los tests).

    Es el camino determinista: ``procesar`` se ejecuta en el mismo proceso, así
    que sirve tanto para ``max_workers == 1`` en producción (no tiene sentido
    pagar un proceso por documento) como para la suite default, que inyecta un
    orquestador con dobles.

    ``capacidad`` es 1: con un solo worker, cada ola es un documento y "todos los
    workers detenidos" ocurre después de cada uno. La ventana de trabajo se evalúa
    igual, así que el enfriamiento **sí** se aplica a un lote de un worker (una
    máquina que procesa sola también se calienta).
    """

    capacidad = 1
    nombre = EJECUTOR_SERIAL

    def __init__(self, procesar: Any, *, capacidad: int = 1) -> None:
        """
        Argumentos:
            procesar: callable ``(trabajo: dict) -> dict`` que produce el payload
                del resultado. En producción envuelve al orquestador; en los
                tests, un doble.
            capacidad: tamaño de ola del ejecutor serial. Se permite > 1 para
                ejercitar la planificación (olas y ventanas) **sin** spawnear
                procesos: la capacidad es del ejecutor, no del número de procesos
                reales, y así el test dice la verdad sobre lo que verifica.
        """
        self.procesar = procesar
        self.capacidad = max(1, int(capacidad))
        self._activo = False

    def iniciar(self) -> None:
        self._activo = True

    def enviar(self, trabajo: dict[str, Any]) -> FuturoLote:
        return FuturoListo(self.procesar(trabajo))

    def recolectar(self, futuros: Sequence[FuturoLote]) -> list[dict[str, Any]]:
        return [futuro.resultado() for futuro in futuros]

    def detener(self) -> None:
        self._activo = False


def _init_worker() -> None:
    """Inicializa el estado por worker del pool (patrón ``v1/full_pipeline.py``).

    El worker construye su convertidor de Docling y su cliente de modelos de forma
    **perezosa** (``_worker_orquestador``): crear el convertidor acá obligaría a
    que todo worker pague la carga de modelos aunque el lote tenga un solo
    documento, y la inicialización perezosa deja el costo donde corresponde (el
    primer trabajo del worker).

    El ``initializer`` se declara igual porque es el punto donde el pool garantiza
    "una vez por proceso", y porque deja el contrato explícito para quien agregue
    estado por worker.
    """


#: Estado por worker (convertidor, cliente y settings). Global del proceso worker.
_WORKER: dict[str, Any] = {}


def _worker_orquestador(opciones: dict[str, Any]) -> Any:
    """Orquestador del worker, con **su** convertidor y **su** cliente (T-602).

    Se cachea por proceso: el convertidor de Docling es caro y los modelos no se
    comparten entre procesos. Los ``Settings`` se cargan en el worker (no se
    picklean desde el padre): es el mismo contrato que ``init_worker`` de v1 y
    evita transportar configuración que podría tener objetos no serializables.
    """
    from .conclusion.agent import AgenteOllama
    from .models.docling import DoclingConverter
    from .models.ollama import OllamaClient
    from .orchestrator import PipelineOrchestrator

    if "orquestador" not in _WORKER:
        cliente = OllamaClient()
        converter = DoclingConverter()
        settings = cargar_settings()
        agente = AgenteOllama(cliente) if opciones.get("escalar", True) else None
        _WORKER["orquestador"] = PipelineOrchestrator(
            cliente=cliente,
            agente=agente,
            converter=converter,
            settings=settings,
        )
    return _WORKER["orquestador"]


def procesar_trabajo(trabajo: dict[str, Any]) -> dict[str, Any]:
    """Procesa un documento dentro de un worker y devuelve el payload (T-602).

    Es una función **de módulo** (no un método ni un closure) porque tiene que ser
    importable en el proceso worker. Construye el orquestador del proceso la
    primera vez y delega en ``ejecutar`` con las opciones del lote.
    """
    orquestador = _worker_orquestador(trabajo.get("opciones") or {})
    resultado = orquestador.ejecutar(
        trabajo["ruta"],
        documento_id=trabajo.get("documento_id"),
        **(trabajo.get("opciones") or {}),
    )
    return resultado_a_payload(resultado)


class EjecutorProcesos:
    """Ejecutor con pool de procesos (T-602 / ADR-010).

    Un ``ProcessPoolExecutor`` por **ciclo** de trabajo, con ``initializer`` por
    worker. Detener el pool es lo que hace real la pausa de enfriamiento: al
    volver de ``detener()`` no queda ningún worker vivo, así que la cuenta del
    ADR-010 ("cuando TODOS los workers están detenidos") empieza en un instante
    verificable.
    """

    nombre = EJECUTOR_PROCESOS

    def __init__(self, max_workers: int, *, funcion: Any = None) -> None:
        self.capacidad = max(1, int(max_workers))
        self._funcion = funcion or procesar_trabajo
        self._pool: Any = None

    def iniciar(self) -> None:
        from concurrent.futures import ProcessPoolExecutor

        self._pool = ProcessPoolExecutor(
            max_workers=self.capacidad, initializer=_init_worker
        )

    def enviar(self, trabajo: dict[str, Any]) -> FuturoLote:
        if self._pool is None:
            raise RuntimeError(
                "EjecutorProcesos.enviar() antes de iniciar(): el pool tiene que "
                "estar arrancado (cada worker inicializa su convertidor al "
                "arrancar, ADR-010)."
            )
        # El ``Future`` de la stdlib se adapta al contrato del runner.
        return _FuturoProceso(self._pool.submit(self._funcion, trabajo))

    def recolectar(self, futuros: Sequence[FuturoLote]) -> list[dict[str, Any]]:
        return [futuro.resultado() for futuro in futuros]

    def detener(self) -> None:
        if self._pool is not None:
            # ``wait=True``: al volver, TODOS los workers terminaron (ADR-010).
            self._pool.shutdown(wait=True)
            self._pool = None


# ---------------------------------------------------------------------------
# La traza del lote
# ---------------------------------------------------------------------------


@dataclass
class CicloEnfriamiento:
    """Un ciclo de trabajo del lote: ventana de trabajo + (si toca) enfriamiento.

    Campos:
        ciclo: número de ciclo (1-based).
        documentos: cuántos documentos se procesaron en la ventana.
        inicio_ventana_s: instante (del reloj) en que arrancó la ventana.
        todos_detenidos_s: instante en que se **detuvo el pool**. Es el momento en
            que arranca la cuenta del enfriamiento (ADR-010), no el del último
            documento.
        enfriado_s: cuántos segundos se durmió (0 si no hubo pausa).
        hubo_enfriamiento: ``True`` si este ciclo terminó con pausa. El **último**
            ciclo nunca la tiene: no queda trabajo para el que reanudar.
        duracion_ventana_s: cuánto duró la ventana de trabajo.
    """

    ciclo: int
    documentos: int
    inicio_ventana_s: float
    todos_detenidos_s: float
    enfriado_s: float = 0.0
    hubo_enfriamiento: bool = False

    @property
    def duracion_ventana_s(self) -> float:
        return self.todos_detenidos_s - self.inicio_ventana_s

    def como_dict(self) -> dict[str, Any]:
        return {
            "ciclo": self.ciclo,
            "documentos": self.documentos,
            "inicio_ventana_s": round(self.inicio_ventana_s, 6),
            "todos_detenidos_s": round(self.todos_detenidos_s, 6),
            "duracion_ventana_s": round(self.duracion_ventana_s, 6),
            "enfriado_s": round(self.enfriado_s, 6),
            "hubo_enfriamiento": self.hubo_enfriamiento,
        }


@dataclass
class TrazaLote:
    """Traza del lote: qué se procesó, qué se reanudó y cómo se enfrió (T-602).

    Es la respuesta verificable al DoD: ``max_workers_aplicado`` (real, no el
    solicitado), ``reanudados`` (los que el checkpoint salteó), ``ciclos`` (con
    ``todos_detenidos_s`` y el enfriamiento) y el estado de los checkpoints.
    """

    raiz: str
    max_workers_solicitado: int = 1
    max_workers_aplicado: int = 1
    ejecutor: str = EJECUTOR_SERIAL
    cooling: dict[str, Any] = field(default_factory=dict)
    ciclos: list[CicloEnfriamiento] = field(default_factory=list)
    reanudados: list[str] = field(default_factory=list)
    procesados: list[str] = field(default_factory=list)
    errores: list[str] = field(default_factory=list)
    checkpoints: dict[str, Any] = field(default_factory=dict)
    notas: list[str] = field(default_factory=list)

    @property
    def enfriamientos(self) -> int:
        """Cuántas pausas de enfriamiento hubo (ciclos que enfriaron)."""
        return sum(1 for ciclo in self.ciclos if ciclo.hubo_enfriamiento)

    @property
    def segundos_enfriados(self) -> float:
        """Total de segundos dormidos por enfriamiento."""
        return float(sum(ciclo.enfriado_s for ciclo in self.ciclos))

    @property
    def secuencial(self) -> bool:
        """``True`` si el lote corrió con un solo worker (sin paralelismo real)."""
        return self.max_workers_aplicado <= 1

    def como_dict(self) -> dict[str, Any]:
        datos: dict[str, Any] = {
            "version": VERSION_LOTE,
            "raiz": self.raiz,
            "max_workers_solicitado": self.max_workers_solicitado,
            "max_workers_aplicado": self.max_workers_aplicado,
            "ejecutor": self.ejecutor,
            "secuencial": self.secuencial,
            "documentos": len(self.procesados) + len(self.reanudados),
            "procesados": len(self.procesados),
            "reanudados": list(self.reanudados),
            "errores": list(self.errores),
            "cooling": dict(self.cooling),
            "ciclos": [ciclo.como_dict() for ciclo in self.ciclos],
            "enfriamientos": self.enfriamientos,
            "segundos_enfriados": round(self.segundos_enfriados, 6),
            "checkpoints": dict(self.checkpoints),
            "notas": list(self.notas),
        }
        return datos


def traza_del_lote(resultados: Sequence[PipelineResult]) -> dict[str, Any]:
    """Traza del lote que el runner dejó en el detalle de cada resultado (T-602).

    El lote comparte **una** traza (es del lote, no de cada documento): se lee del
    primer resultado que la tenga. Los documentos reanudados no producen un
    resultado (no se procesan), así que su cantidad viaja en la traza.
    """
    for resultado in resultados:
        lote = (resultado.detalle or {}).get("lote")
        if lote:
            return dict(lote)
    return {}


# ---------------------------------------------------------------------------
# El runner
# ---------------------------------------------------------------------------


@dataclass
class ResultadoLote:
    """Resultado de una corrida de lote (T-602): los casos y la traza del lote."""

    resultados: list[PipelineResult]
    traza: TrazaLote

    @property
    def ok(self) -> bool:
        """``True`` si ningún documento quedó con error."""
        return not self.traza.errores

    def como_dict(self) -> dict[str, Any]:
        return {
            "traza": self.traza.como_dict(),
            "resultados": [resultado_a_payload(r) for r in self.resultados],
        }


def ejecutar_lote(
    raiz: str | Path,
    *,
    orquestador: Any = None,
    max_workers: int = 1,
    force: bool = False,
    persistir: bool = False,
    dir_salida: str | Path | None = None,
    ejecutor: EjecutorLote | None = None,
    reloj: Reloj | None = None,
    cooling: CoolingSettings | None = None,
    checkpoints: CheckpointsLote | None = None,
    settings: Settings | None = None,
    **opciones: Any,
) -> ResultadoLote:
    """Corre el pipeline sobre una carpeta con workers, checkpoints y enfriamiento.

    Implementa el DoD de T-602: el lote corre con ``max_workers`` workers (cada uno
    con su convertidor), **reanuda** desde los checkpoints sin repetir lo
    completado (salvo ``force``) y aplica la política del ADR-010 — la cuenta del
    enfriamiento arranca cuando **todos** los workers están detenidos.

    La planificación es por **olas** del tamaño del pool: se envía una ola, se
    espera entera, y entre olas se evalúa la ventana de trabajo. Al vencerla se
    detiene el pool, se marca ``todos_detenidos`` y recién ahí se duerme
    ``cool_down_s``. El último ciclo nunca enfría (no queda trabajo pendiente).

    Argumentos:
        raiz: carpeta (recursiva) o archivo.
        orquestador: el orquestador del camino **serial** (inyectable; en
            producción se construye uno real).
        max_workers: workers solicitados. ``1`` usa el camino serial; ``>1`` usa
            un pool de procesos (salvo que ``ejecutor`` lo reemplace).
        force: reprocesa los documentos que ya tienen checkpoint válido.
        persistir: persiste el ``CaseRecord`` de cada caso (F5/T-506).
        dir_salida: directorio de la persistencia (``--cases``).
        ejecutor: el pool (inyectable en tests; ``None`` lo elige por
            ``max_workers``).
        reloj: la fuente de tiempo (``None`` = reloj real).
        cooling: política de enfriamiento (``None`` = la de ``settings``).
        checkpoints: el almacén de checkpoints (``None`` = junto a los documentos).
        settings: ``Settings`` de la corrida (``None`` = ``cargar_settings()``).
        **opciones: opciones del pipeline (``condicion_impositiva``, ``modelo``,
            ``orientation``, ``docling_raw``, ``usar_gate``, ``clasificar_contable``,
            ``escalar``) que se pasan a cada corrida.

    Devuelve:
        :class:`ResultadoLote` con los resultados **en orden de descubrimiento** y
        la traza del lote (workers aplicados, reanudados, ciclos y enfriamiento).
    """
    ruta_raiz = Path(raiz).expanduser()
    reloj = reloj or RelojReal()
    ajustes = settings or cargar_settings()
    politica = cooling or ajustes.cooling
    almacen = checkpoints or CheckpointsLote()

    documentos = iterar_documentos(ruta_raiz)
    traza = TrazaLote(raiz=str(ruta_raiz), max_workers_solicitado=int(max_workers))
    traza.cooling = _cooling_dict(politica)

    # --- 1. Descubrimiento + reanudación por checkpoint -------------------
    pendientes: list[tuple[Path, str]] = []
    for documento in documentos:
        if almacen.es_reanudable(documento, force=force):
            traza.reanudados.append(str(documento))
            continue
        try:
            pendientes.append((documento, identificador_de_archivo(documento)))
        except OSError as exc:
            traza.errores.append(str(documento))
            traza.notas.append(f"No se pudo leer {documento}: {exc}")

    # --- 2. El ejecutor ---------------------------------------------------
    if ejecutor is None:
        ejecutor = _elegir_ejecutor(
            max_workers,
            traza=traza,
            orquestador=orquestador,
            opciones=opciones,
            persistir=persistir,
            dir_salida=dir_salida,
        )
    traza.max_workers_aplicado = ejecutor.capacidad
    traza.ejecutor = ejecutor.nombre
    if ejecutor.capacidad != int(max_workers):
        traza.notas.append(
            f"El ejecutor '{ejecutor.nombre}' aplica {ejecutor.capacidad} worker(s) "
            f"de los {max_workers} solicitado(s)."
        )

    # --- 3. Ciclos de trabajo + enfriamiento (ADR-010) --------------------
    resultados = (
        _correr_ciclos(
            pendientes,
            traza=traza,
            ejecutor=ejecutor,
            reloj=reloj,
            politica=politica,
            almacen=almacen,
            opciones=opciones,
        )
        if pendientes
        else []
    )

    traza.checkpoints = {
        "habilitados": almacen.habilitados,
        "escritos": len(traza.procesados),
        "no_escribibles": list(almacen.no_escribibles),
    }
    # La traza vive en el detalle de cada resultado (el lote comparte una sola).
    for resultado in resultados:
        resultado.detalle["lote"] = traza.como_dict()
    return ResultadoLote(resultados=resultados, traza=traza)


def _cooling_dict(politica: CoolingSettings) -> dict[str, Any]:
    """La política de enfriamiento tal como se aplicó (auditoría del lote)."""
    return {
        "enabled": bool(politica.enabled),
        "work_window_s": int(politica.work_window_s),
        "cool_down_s": int(politica.cool_down_s),
        "nota": (
            "ADR-010: la ventana de trabajo se mide entre olas y la cuenta del "
            "enfriamiento arranca cuando el pool está detenido (ningún worker "
            "vivo), no cuando termina el último documento."
        ),
    }


def _elegir_ejecutor(
    max_workers: int,
    *,
    traza: TrazaLote,
    orquestador: Any,
    opciones: dict[str, Any],
    persistir: bool,
    dir_salida: str | Path | None,
) -> EjecutorLote:
    """Elige el ejecutor: serial con 1 worker, pool de procesos con varios.

    Un solo worker no justifica un proceso propio (el costo de serializar y de
    recargar el convertidor es mayor al beneficio), así que el camino de
    ``max_workers == 1`` es serial y **determinista**. Con más de uno, el pool de
    procesos es lo que permite que cada worker tenga **su** convertidor de Docling.
    """
    if int(max_workers) <= 1:
        return EjecutorSerial(
            _procesar_serial(
                orquestador=orquestador,
                opciones=opciones,
                persistir=persistir,
                dir_salida=dir_salida,
            )
        )
    if orquestador is not None:
        traza.notas.append(
            "Se pidieron varios workers con un orquestador inyectado: se usa el "
            "camino serial en el proceso actual (un doble no se puede transportar "
            "a otro proceso). El paralelismo real se verifica con un ejecutor "
            "inyectado."
        )
        return EjecutorSerial(
            _procesar_serial(
                orquestador=orquestador,
                opciones=opciones,
                persistir=persistir,
                dir_salida=dir_salida,
            )
        )
    return EjecutorProcesos(int(max_workers))


def _procesar_serial(
    *,
    orquestador: Any,
    opciones: dict[str, Any],
    persistir: bool,
    dir_salida: str | Path | None,
) -> Any:
    """Devuelve el callable del ejecutor serial (payload → payload)."""

    def procesar(trabajo: dict[str, Any]) -> dict[str, Any]:
        from .orchestrator import PipelineOrchestrator

        orch = orquestador or PipelineOrchestrator()
        resultado = orch.ejecutar(
            trabajo["ruta"],
            documento_id=trabajo.get("documento_id"),
            persistir=persistir,
            dir_salida=dir_salida,
            **(trabajo.get("opciones") or {}),
        )
        return resultado_a_payload(resultado)

    return procesar


def _correr_ciclos(
    pendientes: list[tuple[Path, str]],
    *,
    traza: TrazaLote,
    ejecutor: EjecutorLote,
    reloj: Reloj,
    politica: CoolingSettings,
    almacen: CheckpointsLote,
    opciones: dict[str, Any],
) -> list[PipelineResult]:
    """Corre las olas del lote con la ventana de trabajo y el enfriamiento.

    Estructura del ciclo (ADR-010):

    1. ``iniciar()`` el pool (los workers quedan vivos, con su convertidor).
    2. Enviar olas de a ``ejecutor.capacidad`` y recolectar cada una. Entre olas
       se evalúa la **ventana de trabajo**: si venció y quedan documentos, se
       cierra el ciclo.
    3. ``detener()`` el pool: al volver, **todos** los workers están detenidos →
       se marca ``todos_detenidos_s`` (el instante que pide el ADR).
    4. Si quedan documentos y el enfriamiento está activo, dormir ``cool_down_s``
       **desde** ese instante y abrir el ciclo siguiente.

    La ventana se evalúa **entre** olas y no dentro de una: una ola es la unidad
    atómica de trabajo del pool (los workers que la tomaron terminan su documento),
    y no se interrumpe un documento a la mitad para pausar. Se mide desde el
    **inicio del ciclo** (cuando el pool arranca), porque el trabajo continuo
    incluye la inicialización de los workers — que también calienta la máquina.
    """
    resultados: list[PipelineResult] = []
    indice = 0
    total = len(pendientes)
    ciclo = 0

    while indice < total:
        ciclo += 1
        inicio_ventana = reloj.monotonic()
        ejecutor.iniciar()
        documentos_ciclo = 0
        try:
            while indice < total:
                if (
                    politica.enabled
                    and documentos_ciclo > 0
                    and (reloj.monotonic() - inicio_ventana) >= politica.work_window_s
                ):
                    break  # la ventana de trabajo venció: se cierra el ciclo
                ola = pendientes[indice : indice + ejecutor.capacidad]
                indice += len(ola)
                documentos_ciclo += len(ola)
                trabajos = [
                    construir_trabajo(documento, documento_id, opciones)
                    for documento, documento_id in ola
                ]
                futuros = [ejecutor.enviar(trabajo) for trabajo in trabajos]
                payloads = ejecutor.recolectar(futuros)
                resultados.extend(
                    _registrar_ola(payloads, traza=traza, almacen=almacen)
                )
        finally:
            # Detener el pool es lo que hace real la pausa: al volver de acá no
            # queda ningún worker vivo (ADR-010).
            ejecutor.detener()

        todos_detenidos = reloj.monotonic()
        quedan = indice < total
        enfriar = bool(quedan and politica.enabled and politica.cool_down_s > 0)
        ciclo_traza = CicloEnfriamiento(
            ciclo=ciclo,
            documentos=documentos_ciclo,
            inicio_ventana_s=inicio_ventana,
            todos_detenidos_s=todos_detenidos,
            hubo_enfriamiento=enfriar,
        )
        if enfriar:
            # La cuenta del enfriamiento arranca en ``todos_detenidos``: el reloj
            # avanza recién acá, después de que el pool dejó de existir.
            reloj.dormir(politica.cool_down_s)
            ciclo_traza.enfriado_s = float(politica.cool_down_s)
        traza.ciclos.append(ciclo_traza)

    return resultados


def _registrar_ola(
    payloads: Sequence[dict[str, Any]],
    *,
    traza: TrazaLote,
    almacen: CheckpointsLote,
) -> list[PipelineResult]:
    """Convierte los payloads de una ola en resultados y deja los checkpoints.

    El checkpoint se escribe **después** de que el documento terminó (y con su
    resultado), así que un documento reanudable es uno que completó: no hay
    ventana en la que un documento esté marcado como hecho sin serlo.
    """
    resultados: list[PipelineResult] = []
    for payload in payloads:
        resultado = resultado_desde_dict(payload)
        resultados.append(resultado)
        if resultado.ok:
            traza.procesados.append(str(resultado.archivo))
        else:
            traza.errores.append(str(resultado.archivo))
        almacen.marcar(resultado)
    return resultados


__all__ = [
    "VERSION_LOTE",
    "SUFIJO_CHECKPOINT",
    "EJECUTOR_SERIAL",
    "EJECUTOR_PROCESOS",
    "Reloj",
    "RelojReal",
    "ruta_checkpoint",
    "CheckpointDocumento",
    "CheckpointsLote",
    "FuturoLote",
    "FuturoListo",
    "EjecutorLote",
    "EjecutorSerial",
    "EjecutorProcesos",
    "construir_trabajo",
    "resultado_a_payload",
    "resultado_desde_dict",
    "procesar_trabajo",
    "CicloEnfriamiento",
    "TrazaLote",
    "traza_del_lote",
    "ResultadoLote",
    "ejecutar_lote",
]

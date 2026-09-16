"""Registro de corridas vivas y su parada (``voucherflow stop``).

**Para qué existe.** Un lote largo (``process``, ``batch``, ``corpus``, ``pdf``) es un
árbol de procesos: el padre más los workers del pool. Medido antes de escribir esto:

* ``kill -9`` al padre deja **los workers vivos y trabajando** (huérfanos): hay que
  cazarlos a mano, y lo que hacen es justamente usar CPU durante horas.
* Ctrl-C **sí** funciona si la corrida está en primer plano, porque la señal va al
  **grupo** de procesos y los workers la reciben con el padre.
* Pero si se lanzó con ``&`` o ``nohup``, el shell ya no tiene ese grupo en el
  foreground y **no hay atajo**: hay que saber qué PIDs matar.

Este módulo resuelve los tres casos. La clave es que cada corrida se **anota** al
arrancar (``var/run/*.json``) y que el grupo de procesos se señala entero, en vez de
enumerar hijos (enumerar tiene una carrera: un worker puede crear otro ``subprocess``
entre el listado y el kill).

**El registro no se cree a sí mismo.** Un archivo de ``var/run`` es un dato *viejo*:
puede quedar de un corte de luz, y su PID puede haberse **reciclado** a un proceso
ajeno. Por eso antes de señalar se verifica la **identidad** del proceso contra lo que
se anotó (hora de arranque e inicio de la línea de comando) y, si no coincide, la
entrada se declara **obsoleta y se limpia sin matar nada**. Es la diferencia entre un
``stop`` que sirve y uno que mata el proceso equivocado.

**Sin dependencias nuevas**: ``os``, ``signal``, ``subprocess`` y ``json`` de la stdlib
(``psutil`` está en el entorno, pero lo traen ``accelerate``/``ipykernel``/
``pymupdf4llm``: no es una dependencia declarada del proyecto y la regla del repo es no
apoyarse en eso).

Uso:
    voucherflow stop [--force] [--espera S] [--id ID] [--json]
"""

from __future__ import annotations

import json
import os
import signal
import subprocess
import time
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any

#: Versión del formato del registro. Va en cada archivo para poder migrar el layout
#: sin confundir una entrada vieja con una rota.
VERSION_REGISTRO = "corridas@1"

#: Subdirectorio de ``var/`` donde viven las entradas (relativo a la raíz de datos).
SUBDIR_REGISTRO = "run"

#: Segundos de espera por defecto entre el SIGTERM y el escalado a SIGKILL.
ESPERA_DEFECTO = 15.0

#: Cada cuánto se consulta si el árbol murió (para no hacer busy-wait).
INTERVALO_SONDEO = 0.2


def directorio_registro(var: Path) -> Path:
    """Carpeta del registro de corridas dentro de la raíz de datos (``var/run``)."""
    return var / SUBDIR_REGISTRO


def _lstart(pid: int) -> str | None:
    """Hora de arranque de un PID según ``ps``, o ``None`` si no existe.

    Es la pieza que permite distinguir un proceso propio de un PID **reciclado**: el
    número de PID se reusa, la hora de arranque no. Se consulta por ``subprocess`` y no
    por ``psutil`` a propósito (ver el encabezado del módulo).
    """
    try:
        resultado = subprocess.run(
            ["ps", "-o", "lstart=", "-p", str(pid)],
            capture_output=True,
            text=True,
            check=False,
        )
    except OSError:  # pragma: no cover - sin `ps` en el sistema
        return None
    return resultado.stdout.strip() or None


def _estado(pid: int) -> str:
    """El estado del proceso según ``ps`` (``''`` si no existe).

    ⚠️ Hace falta porque ``os.kill(pid, 0)`` **no distingue un zombie de un proceso
    vivo**: un proceso muerto cuyo padre todavía no lo "reapeó" sigue existiendo para el
    sistema, así que matarlo "no funciona" y el reporte diría que no murió cuando en
    realidad ya está muerto (medido: ``ps`` lo marca ``Z`` y ``os.kill`` no lanza).
    """
    try:
        resultado = subprocess.run(
            ["ps", "-o", "state=", "-p", str(pid)],
            capture_output=True,
            text=True,
            check=False,
        )
    except OSError:  # pragma: no cover - sin `ps` en el sistema
        return ""
    return resultado.stdout.strip()


def _muerto(pid: int) -> bool:
    """True si el proceso **no está corriendo** (no existe, o es un zombie).

    Un zombie es un proceso que ya terminó y solo espera que su padre recoja el código de
    salida: para los efectos de "¿la corrida sigue trabajando?" está **muerto**.
    """
    if not _vivo(pid):
        return True
    return _estado(pid).startswith("Z")


def _vivo(pid: int) -> bool:
    """True si el PID existe (sin depender de permisos sobre él)."""
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True  # existe, pero es de otro usuario
    return True


@dataclass
class Corrida:
    """Una corrida anotada en el registro (lo que hace falta para poder pararla)."""

    id: str
    pid: int
    pgid: int
    comando: str
    inicio: str
    cwd: str
    lstart: str
    linea_comando: str = ""
    #: Si el proceso era **líder de su propio grupo**. Solo entonces señalar el grupo
    #: es seguro; si no, el pgid anotado es el de un grupo **heredado** (el shell que
    #: lanzó la corrida) y ``killpg`` mataría al shell del operador.
    grupo_propio: bool = False
    version: str = VERSION_REGISTRO

    def como_dict(self) -> dict[str, Any]:
        return {
            "version": self.version,
            "id": self.id,
            "pid": self.pid,
            "pgid": self.pgid,
            "comando": self.comando,
            "inicio": self.inicio,
            "cwd": self.cwd,
            "lstart": self.lstart,
            "linea_comando": self.linea_comando,
            "grupo_propio": self.grupo_propio,
        }

    @classmethod
    def desde_dict(cls, datos: dict[str, Any]) -> Corrida:
        return cls(
            id=str(datos.get("id") or ""),
            pid=int(datos.get("pid") or 0),
            pgid=int(datos.get("pgid") or 0),
            comando=str(datos.get("comando") or ""),
            inicio=str(datos.get("inicio") or ""),
            cwd=str(datos.get("cwd") or ""),
            lstart=str(datos.get("lstart") or ""),
            linea_comando=str(datos.get("linea_comando") or ""),
            # Una entrada vieja sin el campo se asume **no** propia: es la suposición
            # segura (señalar el PID en vez del grupo nunca puede matar al shell).
            grupo_propio=bool(datos.get("grupo_propio") or False),
            version=str(datos.get("version") or VERSION_REGISTRO),
        )

    # -- identidad ------------------------------------------------------

    def identidad_coincide(self) -> bool:
        """True si el proceso que tiene este PID es **el mismo** que se anotó.

        Se compara la hora de arranque (y, si hace falta, el inicio de la línea de
        comando). Un PID reciclado tiene otra hora de arranque, así que este chequeo es
        lo que impide que un ``stop`` sobre una entrada vieja mate un proceso ajeno.
        """
        if not self.pid:
            return False
        actual = _lstart(self.pid)
        if actual is None:
            return False  # el proceso no existe
        if self.lstart and actual != self.lstart:
            return False  # PID reciclado
        if self.linea_comando:
            linea = self.linea_comando.strip()
            if linea and not self._linea_actual().startswith(linea[:60]):
                return False
        return True

    def _linea_actual(self) -> str:
        try:
            resultado = subprocess.run(
                ["ps", "-o", "command=", "-p", str(self.pid)],
                capture_output=True,
                text=True,
                check=False,
            )
        except OSError:  # pragma: no cover
            return ""
        return resultado.stdout.strip()

    def viva(self) -> bool:
        """True si la corrida existe, es ella (no un PID reciclado) y sigue corriendo."""
        return not _muerto(self.pid) and self.identidad_coincide()


@dataclass
class EstadoRegistro:
    """Lo que hay en el registro, separado por lo que se puede hacer con cada entrada."""

    vivas: list[Corrida] = field(default_factory=list)
    obsoletas: list[Corrida] = field(default_factory=list)


# ---------------------------------------------------------------------------
# Anotar y desanotar (lo usan los comandos largos)
# ---------------------------------------------------------------------------


def anotar(
    var: Path,
    *,
    id: str,
    comando: str,
    pid: int | None = None,
) -> Corrida:
    """Anota la corrida actual en el registro y devuelve su entrada.

    ``id`` tiene que ser único por corrida (se usa el PID): el archivo se llama
    ``<id>.json``. El grupo de procesos se anota como el del propio proceso — para que
    sea un grupo **propio**, el llamador tiene que haber arrancado el proceso en una
    sesión nueva (``start_new_session=True``), y si no, señalar el grupo mataría
    también al shell que lo lanzó. :func:`preparar_sesion` hace esa parte.
    """
    pid = pid or os.getpid()
    # ⚠️ ``os.getpgid`` levanta ``ProcessLookupError`` si el proceso ya no está (pasa
    # cuando se anota a alguien que terminó entre el lanzamiento y la anotación, o en
    # un test con un proceso recién muerto). No es un error del registro: sin grupo se
    # anota 0 y la entrada queda sin ``grupo_propio``, que es la suposición segura
    # (nunca se señalará un grupo heredado).
    try:
        pgid = os.getpgid(pid)
    except ProcessLookupError:
        pgid = 0
    entrada = Corrida(
        id=id,
        pid=pid,
        pgid=pgid,
        comando=comando,
        inicio=datetime.now().astimezone().isoformat(timespec="seconds"),
        cwd=str(Path.cwd()),
        lstart=_lstart(pid) or "",
        linea_comando=_linea_de(pid),
        # ``pgid == pid`` significa que el proceso es líder de su grupo (sesión propia).
        grupo_propio=pgid == pid and pid != 0,
    )
    destino = directorio_registro(var) / f"{id}.json"
    destino.parent.mkdir(parents=True, exist_ok=True)
    destino.write_text(
        json.dumps(entrada.como_dict(), ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    return entrada


def _linea_de(pid: int) -> str:
    try:
        resultado = subprocess.run(
            ["ps", "-o", "command=", "-p", str(pid)],
            capture_output=True,
            text=True,
            check=False,
        )
    except OSError:  # pragma: no cover
        return ""
    return resultado.stdout.strip()[:200]


def desanotar(var: Path, id: str) -> bool:
    """Borra la entrada de una corrida (al terminar). True si había algo que borrar."""
    destino = directorio_registro(var) / f"{id}.json"
    try:
        destino.unlink()
        return True
    except FileNotFoundError:
        return False
    except OSError:  # pragma: no cover - un registro que no se puede borrar no es fatal
        return False


def preparar_sesion() -> bool:
    """Pone al proceso en su **propia sesión** para que tenga un grupo propio.

    ⚠️ Es lo que hace seguro señalar el grupo: sin esto, el grupo del proceso es el del
    shell que lo lanzó (o el de la terminal), y ``killpg`` mataría **también al shell
    del operador**. Se hace una sola vez y se ignora si no se puede (en ese caso el
    ``stop`` sigue funcionando, pero señala el grupo heredado: por eso se declara).

    Devuelve ``True`` si la sesión quedó propia.
    """
    try:
        os.setsid()
    except (OSError, AttributeError):  # pragma: no cover - Windows o ya líder
        return False
    return True


# ---------------------------------------------------------------------------
# Leer el registro
# ---------------------------------------------------------------------------


def leer(var: Path) -> EstadoRegistro:
    """Lee el registro y separa las corridas vivas de las obsoletas.

    Una entrada ilegible **no** se cuenta como viva ni como obsoleta-mata: se trata como
    obsoleta y se declara (igual que un checkpoint corrupto, que degrada a reprocesar en
    vez de romper la corrida). El punto es no matar nunca por un dato que no se pudo
    leer.
    """
    carpeta = directorio_registro(var)
    estado = EstadoRegistro()
    if not carpeta.is_dir():
        return estado
    for archivo in sorted(carpeta.glob("*.json")):
        try:
            datos = json.loads(archivo.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            continue
        if not isinstance(datos, dict):
            continue
        corrida = Corrida.desde_dict(datos)
        if corrida.viva():
            estado.vivas.append(corrida)
        else:
            estado.obsoletas.append(corrida)
    return estado


def limpiar_obsoletas(var: Path, estado: EstadoRegistro | None = None) -> list[Corrida]:
    """Borra las entradas obsoletas (proceso muerto o PID reciclado). Devuelve cuáles."""
    estado = estado or leer(var)
    for corrida in estado.obsoletas:
        desanotar(var, corrida.id)
    return estado.obsoletas


# ---------------------------------------------------------------------------
# Parar
# ---------------------------------------------------------------------------


@dataclass
class ResultadoParada:
    """Qué pasó con una corrida que se intentó parar (para reportar sin adivinar)."""

    corrida: Corrida
    senal_enviada: str
    termino: bool
    escalado: bool = False
    motivo: str = ""
    #: A quién se le mandó la señal (``grupo N`` / ``proceso N``). Se declara porque
    #: señalar el proceso en vez del grupo pierde la carrera contra un worker que cree
    #: otro ``subprocess``: el operador tiene que poder saber en qué caso está.
    destino: str = ""

    def como_dict(self) -> dict[str, Any]:
        return {
            "id": self.corrida.id,
            "comando": self.corrida.comando,
            "pid": self.corrida.pid,
            "senal": self.senal_enviada,
            "destino": self.destino,
            "termino": self.termino,
            "escalado_a_sigkill": self.escalado,
            "motivo": self.motivo,
        }


def _senalar(corrida: Corrida, sig: int) -> str:
    """Señala la corrida y devuelve **a qué** se le mandó la señal (para declararlo).

    ⚠️ **La guarda es lo más importante de este módulo.** Al grupo se señala **solo si el
    proceso es líder de su propio grupo** (``pgid == pid``), que es lo que garantiza
    :func:`preparar_sesion` o un ``Popen(start_new_session=True)``. Si no lo es, el
    ``pgid`` anotado es el de un grupo **heredado** —el shell del operador— y un
    ``killpg`` ahí mataría la terminal en vez de la corrida.

    Eso no es hipotético: pasó durante el desarrollo de este módulo. Un test anotó un
    subproceso sin sesión propia y el ``killpg`` mató el shell que corría el test
    (``zsh: terminated``). La guarda convierte ese accidente en un caso imposible.

    Cuando el grupo no es propio se señala **el proceso**: los workers del pool son hijos
    suyos y mueren con él (``ProcessPoolExecutor`` cierra el pool al salir). Lo que se
    pierde es la carrera contra un worker que cree otro ``subprocess``, y eso se declara
    en el resultado.
    """
    if corrida.grupo_propio and corrida.pgid == corrida.pid:
        try:
            os.killpg(corrida.pgid, sig)
            return f"grupo {corrida.pgid}"
        except (ProcessLookupError, PermissionError):
            pass
    os.kill(corrida.pid, sig)
    return f"proceso {corrida.pid}"


def _esperar_a_que_muera(corrida: Corrida, espera: float) -> bool:
    """Espera hasta ``espera`` segundos a que el PID deje de correr. True si murió.

    Se considera muerto también al **zombie** (ver :func:`_estado`): un proceso que ya
    terminó y espera a que su padre lo recoja no está trabajando, y tratarlo como vivo
    haría escalar a SIGKILL sin motivo y reportar un fallo que no ocurrió.
    """
    limite = time.monotonic() + espera
    while time.monotonic() < limite:
        if _muerto(corrida.pid):
            return True
        time.sleep(INTERVALO_SONDEO)
    return _muerto(corrida.pid)


def parar(
    corrida: Corrida,
    *,
    force: bool = False,
    espera: float = ESPERA_DEFECTO,
) -> ResultadoParada:
    """Para una corrida: SIGTERM al grupo y, si no muere, SIGKILL.

    Con ``force`` va directo al SIGKILL (no le da la chance de terminar el documento en
    curso). Sin ``force`` usa SIGTERM, espera ``espera`` segundos y **escala** si sigue
    viva: un SIGTERM que no se atiende no puede dejar el árbol corriendo, porque el
    motivo por el que existe este comando es no tener que matar a mano.
    """
    if not corrida.viva():
        return ResultadoParada(
            corrida, "ninguna", True, motivo="la corrida ya no estaba (entrada obsoleta)"
        )

    if force:
        destino = _senalar(corrida, signal.SIGKILL)
        termino = _esperar_a_que_muera(corrida, 5.0)
        return ResultadoParada(
            corrida,
            "SIGKILL",
            termino,
            escalado=True,
            motivo="--force: sin espera" if termino else "no murió tras SIGKILL",
            destino=destino,
        )

    destino = _senalar(corrida, signal.SIGTERM)
    if _esperar_a_que_muera(corrida, espera):
        return ResultadoParada(
            corrida, "SIGTERM", True, motivo="terminó por SIGTERM", destino=destino
        )

    _senalar(corrida, signal.SIGKILL)
    termino = _esperar_a_que_muera(corrida, 5.0)
    return ResultadoParada(
        corrida,
        "SIGTERM→SIGKILL",
        termino,
        escalado=True,
        motivo=(
            "no terminó por SIGTERM en la espera: se escaló a SIGKILL"
            if termino
            else "no murió ni con SIGKILL"
        ),
        destino=destino,
    )


def parar_todas(
    var: Path,
    *,
    force: bool = False,
    espera: float = ESPERA_DEFECTO,
    solo_id: str | None = None,
) -> list[ResultadoParada]:
    """Para todas las corridas vivas (o una, si ``solo_id``), y limpia el registro."""
    estado = leer(var)
    limpiar_obsoletas(var, estado)

    objetivo = estado.vivas
    if solo_id is not None:
        objetivo = [c for c in estado.vivas if c.id == solo_id]
        if not objetivo:
            raise ValueError(
                f"No hay ninguna corrida viva con id {solo_id!r}. "
                f"Vivas: {[c.id for c in estado.vivas] or 'ninguna'}."
            )

    resultados = [parar(corrida, force=force, espera=espera) for corrida in objetivo]
    for resultado in resultados:
        if resultado.termino:
            desanotar(var, resultado.corrida.id)
    return resultados


def instalar_limpieza(var: Path, id: str) -> None:
    """Registra el desanotado al salir (por salida normal o por señal).

    ⚠️ Sin esto, una corrida que muere por Ctrl-C **deja su entrada**: la próxima
    ``voucherflow stop`` la vería como obsoleta (el chequeo de identidad la salva de
    matar a nadie) pero el registro se llenaría de basura. Se encadena el handler
    anterior para no pisar el comportamiento que ya exista.
    """
    for sig in (signal.SIGTERM, signal.SIGINT, signal.SIGHUP):
        anterior = signal.getsignal(sig)

        def _handler(signum: int, marco: Any, _anterior: Any = anterior) -> None:
            desanotar(var, id)
            if callable(_anterior):
                _anterior(signum, marco)
            elif _anterior == signal.SIG_DFL:
                # Comportamiento por defecto: terminar con el código de la señal.
                signal.signal(signum, signal.SIG_DFL)
                os.kill(os.getpid(), signum)

        try:
            signal.signal(sig, _handler)
        except (ValueError, OSError):  # pragma: no cover - fuera del hilo principal
            pass


__all__ = [
    "ESPERA_DEFECTO",
    "SUBDIR_REGISTRO",
    "VERSION_REGISTRO",
    "Corrida",
    "EstadoRegistro",
    "ResultadoParada",
    "anotar",
    "desanotar",
    "directorio_registro",
    "instalar_limpieza",
    "leer",
    "limpiar_obsoletas",
    "parar",
    "parar_todas",
    "preparar_sesion",
]

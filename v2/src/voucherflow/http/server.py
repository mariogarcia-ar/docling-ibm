"""Servidor HTTP de ``voucherflow`` — F6/T-606 (fase 2, no bloqueante).

**Qué es.** Una API HTTP **básica** que expone las capacidades de la fachada
(:mod:`voucherflow.api`) por red, para que un consumidor que no puede importar el
paquete (otro servicio, un front, un script en otra máquina) pueda pedir el
pipeline completo, la extracción o la consulta puntual.

**Qué NO es.** No es una segunda implementación: cada ruta **delega** en
``api.*`` (misma decisión de alcance que la CLI de T-601, §2.1 del subplan). El
servidor aporta el transporte (HTTP), el parseo de la petición y el mapeo de
errores a códigos de estado; la lógica del sistema sigue viviendo en la librería.

**Por qué ``http.server`` de la stdlib y no FastAPI.** El diagrama C4 (doc 03 §3)
menciona FastAPI como etiqueta de la caja "API HTTP (fase 2)". La regla dura del
repo es **no agregar dependencias** (subplan §2.3 y §4), y hay un precedente
directo: T-601 eligió ``argparse`` de la stdlib sobre ``typer`` por el mismo
motivo. Un servidor de un archivo es auditable y no obliga a instalar nada; el
día que haga falta OpenAPI, streaming o auth, FastAPI entra como **decisión
explícita** (y este módulo se reemplaza conservando las rutas, que es lo que el
consumidor conoce).

**Cómo se separa lo testeable del transporte.** El módulo tiene dos capas:

1. :func:`manejar` — **pura**: recibe método, ruta, query y cuerpo (ya leídos) y
   devuelve ``(estado, objeto)``. No abre sockets, no conoce ``http.server``. Es
   lo que la suite ejercita, in-process y sin red.
2. :class:`ServidorVoucherflow` — el :class:`~http.server.BaseHTTPRequestHandler`
   que lee el socket, llama a :func:`manejar` y escribe la respuesta.

Separarlas es lo mismo que hizo ``EntornoCLI`` con el CLI: la lógica se prueba sin
levantar un proceso, y el transporte queda con lo mínimo posible (leer bytes,
escribir bytes).

**Seguridad — alcance declarado.** El servidor **no** trae autenticación, TLS ni
CORS: es la versión "básica" de la fase 2 y está pensado para red local o para
correr detrás de un proxy que ponga esas capas (que es lo que el diagrama C4
sugiere al ubicarlo en el borde). Está **declarado**, no escondido: la ruta raíz
``GET /`` lo dice, y el propio diseño ata el servidor a ``127.0.0.1`` por default
—exponerlo en toda la red es una decisión explícita del operador, con
``--host 0.0.0.0``—. Un servidor sin auth que se publica por accidente es un
problema; uno que se publica a propósito, es una decisión.

**Rutas** (ver :data:`RUTAS` para el contrato completo):

===========================  ======  ==========================================
Ruta                         Método  Equivalente en la CLI
===========================  ======  ==========================================
``GET  /``                   GET     (índice: lista las rutas)
``GET  /salud``              GET     (liveness; no toca modelos)
``POST /run``                POST    ``voucherflow run``
``POST /extract``            POST    ``voucherflow extract``
``POST /ask``                POST    ``voucherflow ask``
``GET  /version``            GET     ``voucherflow --version``
===========================  ======  ==========================================

**Mapeo de errores a códigos de estado.** El código HTTP tiene que decir lo
mismo que dice el error (una API que devuelve 200 con un error adentro obliga al
consumidor a leer el cuerpo para saber si falló):

- ``400`` — la petición está mal (falta un campo, JSON inválido). Es culpa del
  consumidor y la puede corregir.
- ``404`` — la ruta no existe.
- ``405`` — la ruta existe pero no con ese método.
- ``422`` — el documento se leyó pero **no se pudo procesar**
  (``DocumentoNoProcesableError``: formato no soportado, archivo ilegible). Es un
  problema del **dato**, no de la petición.
- ``503`` — un modelo no responde (``OllamaError``). Es **transitorio**: el
  consumidor puede reintentar, y por eso no se mezcla con el 422.
- ``500`` — cualquier otra cosa: un error inesperado, que se reporta con su
  mensaje en vez de taparse.

**Un rechazo no es un error HTTP.** Un documento que no es comprobante se
resuelve ``200`` con ``estado=rechazado``: el sistema **sí** concluyó (concluyó
que no), y devolver 4xx/5xx haría que el consumidor tratara una conclusión válida
como una falla de infraestructura (misma distinción que T-601 §2.6).
"""

from __future__ import annotations

import json
import sys
from dataclasses import dataclass, field
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any, Callable, Mapping
from urllib.parse import parse_qs, urlparse

VERSION_HTTP = "voucherflow-http@1"

#: Host por defecto. Deliberadamente ``127.0.0.1`` y no ``0.0.0.0``: el servidor
#: no trae autenticación, así que "escuchar solo en local" es el default seguro y
#: publicarlo en la red es una decisión explícita (``--host``).
HOST_DEFAULT = "127.0.0.1"
PUERTO_DEFAULT = 8000

#: Tope del cuerpo aceptado, en bytes. Un documento puede ser grande, pero un
#: cuerpo sin límite es un agujero: se corta y se responde 400 en vez de acumular
#: memoria sin control. 256 MB cubre de sobra los 50 MB que acepta el pipeline.
MAX_CUERPO = 256 * 1024 * 1024


# ---------------------------------------------------------------------------
# Contrato de las rutas (declarado, no implícito)
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class Ruta:
    """Una ruta del contrato HTTP.

    Campos:
        metodo: verbo HTTP esperado (``GET``/``POST``).
        camino: la ruta (``/run``).
        descripcion: qué hace, en una línea (se publica en ``GET /``).
        equivalente_cli: el subcomando del CLI que hace lo mismo (trazabilidad
            entre las dos superficies: si divergen, es un bug).
        campos: los campos del cuerpo que espera (para el índice y para validar).
    """

    metodo: str
    camino: str
    descripcion: str
    equivalente_cli: str
    campos: tuple[str, ...] = ()


RUTAS: tuple[Ruta, ...] = (
    Ruta("GET", "/", "Índice: las rutas disponibles y su versión.", "—"),
    Ruta(
        "GET",
        "/salud",
        "Liveness: responde sin tocar modelos ni archivos.",
        "—",
    ),
    Ruta(
        "POST",
        "/run",
        "Pipeline completo de un documento → veredicto.",
        "run",
        ("origen",),
    ),
    Ruta(
        "POST",
        "/extract",
        "Extracción VLM+LLM de un documento → evidencia combinada.",
        "extract",
        ("origen",),
    ),
    Ruta(
        "POST",
        "/ask",
        "Pregunta puntual sobre un documento (no produce evidencia).",
        "ask",
        ("origen", "pregunta"),
    ),
    Ruta("GET", "/version", "Versión del servidor y del contrato de extracción.", "—"),
)

#: Índice ``(metodo, camino) → Ruta`` para el despacho (y para detectar 405).
_POR_CLAVE: dict[tuple[str, str], Ruta] = {
    (r.metodo, r.camino): r for r in RUTAS
}

#: Caminos existentes, sin importar el método (para distinguir 404 de 405).
_CAMINOS: frozenset[str] = frozenset(r.camino for r in RUTAS)


class ErrorPeticion(ValueError):
    """La petición está mal armada (falta un campo, JSON inválido) → 400."""


# ---------------------------------------------------------------------------
# Colaboraciones (inyectables: la suite ejerce las rutas sin red)
# ---------------------------------------------------------------------------


@dataclass
class EntornoHTTP:
    """Las colaboraciones del servidor, inyectables para la suite.

    Es el análogo de ``EntornoCLI`` (T-601): el despacho recibe funciones, no
    importa los módulos de la fachada hasta que las necesita. Con dobles, las
    cuatro rutas se ejercitan sin Ollama, sin Docling y sin abrir un socket.

    Campos:
        run: ``(origen, **opciones) -> VoucherResult``.
        extract: ``(origen, mode) -> CombinedEvidence``.
        ask: ``(origen, pregunta, **opciones) -> str``.
        version: ``() -> str``.
        raiz: directorio contra el que se resuelven las rutas relativas (el mismo
            criterio que ``EntornoCLI.ruta``: el consumidor manda una ruta, el
            servidor decide desde dónde).
    """

    run: Callable[..., Any] | None = None
    extract: Callable[..., Any] | None = None
    ask: Callable[..., Any] | None = None
    version: Callable[[], str] | None = None
    raiz: Path = field(default_factory=Path.cwd)

    def f_run(self) -> Callable[..., Any]:
        if self.run is None:
            from ..api import run as api_run

            self.run = api_run
        return self.run

    def f_extract(self) -> Callable[..., Any]:
        if self.extract is None:
            from ..api import extract as api_extract

            self.extract = api_extract
        return self.extract

    def f_ask(self) -> Callable[..., Any]:
        if self.ask is None:
            from ..api import ask as api_ask

            self.ask = api_ask
        return self.ask

    def f_version(self) -> Callable[[], str]:
        if self.version is None:
            from ..api import extraction_version

            self.version = extraction_version
        return self.version

    def ruta(self, valor: str) -> str:
        """Resuelve una ruta relativa contra ``raiz`` (deja las absolutas)."""
        camino = Path(valor).expanduser()
        return str(camino if camino.is_absolute() else (self.raiz / camino))


# ---------------------------------------------------------------------------
# Serialización del resultado
# ---------------------------------------------------------------------------


def _como_dato(valor: Any) -> Any:
    """Convierte un contrato (pydantic o dataclass) a algo serializable.

    Se apoya en ``model_dump(mode="json")`` cuando existe —es el camino de los
    contratos de F0/F5— y cae a ``como_dict()``/``__dict__`` para los objetos que
    no son pydantic. No se inventa un esquema nuevo: se publica el contrato tal
    como la librería lo expone.
    """
    if valor is None or isinstance(valor, (str, int, float, bool)):
        return valor
    if isinstance(valor, Mapping):
        return {k: _como_dato(v) for k, v in valor.items()}
    if isinstance(valor, (list, tuple)):
        return [_como_dato(v) for v in valor]
    if hasattr(valor, "model_dump"):
        return valor.model_dump(mode="json")
    if hasattr(valor, "como_dict"):
        return _como_dato(valor.como_dict())
    return str(valor)


# ---------------------------------------------------------------------------
# La lógica de ruteo (pura: sin sockets)
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class Respuesta:
    """Lo que el despacho devuelve: el estado HTTP y el objeto a serializar."""

    estado: int
    cuerpo: Any


def _cuerpo_json(cuerpo: dict[str, Any], campos: tuple[str, ...]) -> dict[str, Any]:
    """Valida los campos obligatorios del cuerpo (400 si falta alguno)."""
    faltan = [c for c in campos if not str(cuerpo.get(c) or "").strip()]
    if faltan:
        raise ErrorPeticion(
            f"Faltan campos obligatorios: {', '.join(faltan)}. "
            f"Esperados: {', '.join(campos)}."
        )
    return cuerpo


def _mapear_error(exc: Exception) -> Respuesta:
    """Mapea una excepción de la librería al código HTTP que le corresponde.

    La distinción importante es **422 vs 503**: ``DocumentoNoProcesableError``
    dice que el dato no sirve (reintentar no cambia nada) y ``OllamaError`` dice
    que el modelo no respondió (reintentar es exactamente lo que hay que hacer).
    Aplanar los dos en un 500 dejaría al consumidor sin poder elegir.

    ⚠️ **El orden de las ramas importa** y no es cosmético: las jerarquías se
    cruzan, así que una rama genérica antes que una específica se come el caso.

    - ``DocumentoNoProcesableError`` ⊂ ``VoucherflowError`` (y ambos ⊂
      ``RuntimeError``): va **antes** de ``VoucherflowError``.
    - ``OllamaError`` ⊂ ``RuntimeError`` pero **no** ⊂ ``VoucherflowError``: va
      antes de la rama genérica.
    - ``ErrorPeticion`` ⊂ ``ValueError``: va **antes** de la rama de ``ValueError``.

    Un test de frontera reordena mentalmente el mapeo (422 ≠ 503 ≠ 400) para que
    un cambio de orden se detecte en vez de degradar en silencio a un 500.
    """
    from ..api import DocumentoNoProcesableError, VoucherflowError
    from ..models.ollama import OllamaError

    nombre = type(exc).__name__
    if isinstance(exc, DocumentoNoProcesableError):
        return Respuesta(422, {"error": nombre, "detalle": str(exc)})
    if isinstance(exc, FileNotFoundError):
        return Respuesta(422, {"error": nombre, "detalle": str(exc)})
    if isinstance(exc, OllamaError):
        return Respuesta(503, {"error": nombre, "detalle": str(exc)})
    if isinstance(exc, ErrorPeticion):
        return Respuesta(400, {"error": nombre, "detalle": str(exc)})
    if isinstance(exc, (VoucherflowError, ValueError, KeyError)):
        return Respuesta(400, {"error": nombre, "detalle": str(exc)})
    return Respuesta(500, {"error": nombre, "detalle": str(exc)})


def manejar(
    metodo: str,
    camino: str,
    *,
    body: bytes | str | None = None,
    query: Mapping[str, list[str]] | None = None,
    entorno: EntornoHTTP | None = None,
    version_http: str = VERSION_HTTP,
) -> Respuesta:
    """Despacha una petición HTTP y devuelve la respuesta (sin tocar sockets).

    Es la capa **pura** del servidor: recibe lo que ya se leyó del socket
    (método, camino, cuerpo, query) y devuelve ``(estado, objeto)``. Se prueba
    in-process y sin red, que es la razón de separarla de
    :class:`ServidorVoucherflow`.

    Argumentos:
        metodo: verbo HTTP (se normaliza a mayúsculas).
        camino: la ruta (``/run``); el query string se pasa aparte.
        body: el cuerpo crudo (``bytes`` o ``str``); JSON para ``/run``,
            ``/extract`` y ``/ask``.
        query: el query string ya parseado (``parse_qs``).
        entorno: colaboraciones; ``None`` construye el real (fachada).
        version_http: versión del contrato HTTP (para ``GET /`` y ``/version``).

    Devuelve:
        :class:`Respuesta` con el estado y el objeto a serializar.
    """
    ctx = entorno or EntornoHTTP()
    verbo = (metodo or "").upper()
    partes = urlparse(camino or "/")
    ruta_normalizada = partes.path or "/"
    if not ruta_normalizada.startswith("/"):
        ruta_normalizada = "/" + ruta_normalizada
    # El query puede venir en el camino (``/x?a=1``) o aparte: se combinan.
    parametros: dict[str, list[str]] = dict(query or {})
    if partes.query:
        for clave, valores in parse_qs(partes.query).items():
            parametros.setdefault(clave, []).extend(valores)

    try:
        if ruta_normalizada not in _CAMINOS:
            return Respuesta(
                404,
                {
                    "error": "RutaNoEncontrada",
                    "detalle": f"No existe la ruta {ruta_normalizada!r}.",
                    "rutas": [f"{r.metodo} {r.camino}" for r in RUTAS],
                },
            )

        if (verbo, ruta_normalizada) not in _POR_CLAVE:
            permitidos = sorted(
                r.metodo for r in RUTAS if r.camino == ruta_normalizada
            )
            return Respuesta(
                405,
                {
                    "error": "MetodoNoPermitido",
                    "detalle": (
                        f"{ruta_normalizada!r} no acepta {verbo}; "
                        f"acepta {', '.join(permitidos)}."
                    ),
                    "permitidos": permitidos,
                },
            )

        if ruta_normalizada == "/":
            return Respuesta(
                200,
                {
                    "servicio": "voucherflow",
                    "version_http": version_http,
                    "rutas": [
                        {
                            "metodo": r.metodo,
                            "camino": r.camino,
                            "descripcion": r.descripcion,
                            "equivalente_cli": r.equivalente_cli,
                            "campos": list(r.campos),
                        }
                        for r in RUTAS
                    ],
                    "nota": (
                        "API HTTP básica (F6/T-606, fase 2): sin autenticación ni "
                        "TLS. Pensada para red local o detrás de un proxy. El "
                        "rechazo de un documento no es un error HTTP: se responde "
                        "200 con estado=rechazado."
                    ),
                },
            )

        if ruta_normalizada == "/salud":
            # Liveness: no toca modelos, archivos ni la fachada. Un health check
            # que depende de Ollama reporta caído al servidor cuando lo que falla
            # es un modelo; esta ruta dice "el proceso está vivo" y nada más.
            return Respuesta(
                200,
                {"estado": "ok", "servicio": "voucherflow", "version_http": version_http},
            )

        if ruta_normalizada == "/version":
            return Respuesta(
                200,
                {
                    "servicio": "voucherflow",
                    "version_http": version_http,
                    "version_extraccion": ctx.f_version()(),
                },
            )

        # --- rutas POST: el cuerpo manda el trabajo ------------------------
        datos = _leer_cuerpo_json(body)

        if ruta_normalizada == "/run":
            campos = _cuerpo_json(datos, ("origen",))
            resultado = ctx.f_run()(
                ctx.ruta(str(campos["origen"])),
                condicion_impositiva=campos.get("condicion_impositiva"),
                modelo=campos.get("modelo"),
                orientation=campos.get("orientation", "auto"),
                docling_raw=bool(campos.get("docling_raw", False)),
                clasificar_contable=bool(campos.get("clasificar_contable", False)),
            )
            return Respuesta(200, {"resultado": _como_dato(resultado)})

        if ruta_normalizada == "/extract":
            campos = _cuerpo_json(datos, ("origen",))
            evidencia = ctx.f_extract()(
                ctx.ruta(str(campos["origen"])),
                str(campos.get("mode", "kvi")),
            )
            return Respuesta(200, {"evidencia": _como_dato(evidencia)})

        if ruta_normalizada == "/ask":
            campos = _cuerpo_json(datos, ("origen", "pregunta"))
            respuesta = ctx.f_ask()(
                ctx.ruta(str(campos["origen"])),
                str(campos["pregunta"]),
                modelo=campos.get("modelo"),
            )
            return Respuesta(200, {"respuesta": respuesta})

        # Inalcanzable: toda ruta de _CAMINOS está cubierta arriba. Si se agrega
        # una Ruta y se olvida su rama, este error lo dice en vez de devolver 200
        # con un cuerpo vacío (que se leería como "no hay nada que hacer").
        return Respuesta(
            500,
            {
                "error": "RutaSinImplementar",
                "detalle": (
                    f"La ruta {ruta_normalizada!r} está declarada pero no tiene "
                    "rama de despacho: es un bug del servidor."
                ),
            },
        )
    except Exception as exc:  # noqa: BLE001 - el mapeo a HTTP es el contrato
        return _mapear_error(exc)


def _leer_cuerpo_json(body: bytes | str | None) -> dict[str, Any]:
    """Parsea el cuerpo como objeto JSON (400 si no lo es)."""
    if body is None or body == b"" or body == "":
        raise ErrorPeticion("El cuerpo de la petición está vacío: se espera JSON.")
    texto = body.decode("utf-8") if isinstance(body, (bytes, bytearray)) else body
    try:
        datos = json.loads(texto)
    except json.JSONDecodeError as exc:
        raise ErrorPeticion(f"El cuerpo no es JSON válido: {exc}.") from exc
    if not isinstance(datos, dict):
        raise ErrorPeticion(
            f"El cuerpo debe ser un objeto JSON; llegó {type(datos).__name__}."
        )
    return datos


# ---------------------------------------------------------------------------
# El transporte (http.server de la stdlib)
# ---------------------------------------------------------------------------


class ServidorVoucherflow(BaseHTTPRequestHandler):
    """Handler de ``http.server``: lee el socket, delega en :func:`manejar`.

    Tiene **solo** lo que la capa pura no puede hacer: leer bytes del socket,
    invocar el despacho y escribir la respuesta. Toda la decisión (código de
    estado, forma del cuerpo) ya está tomada en :func:`manejar`.

    Clase (no instancia) porque ``http.server`` construye un handler **por
    petición**: el estado compartido (las colaboraciones) vive en atributos de
    clase, que es el mecanismo que el framework ofrece.
    """

    #: Inyectables de la clase (los setea :func:`servir`).
    entorno: EntornoHTTP = EntornoHTTP()
    version_http: str = VERSION_HTTP

    protocol_version = "HTTP/1.1"

    # -- helpers ---------------------------------------------------------

    def _responder(self, respuesta: Respuesta) -> None:
        """Escribe la respuesta JSON con su código de estado."""
        cuerpo = json.dumps(
            _como_dato(respuesta.cuerpo), ensure_ascii=False, indent=2
        ).encode("utf-8")
        self.send_response(respuesta.estado)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(cuerpo)))
        self.end_headers()
        self.wfile.write(cuerpo)

    def _leer_entrada(self) -> bytes:
        """Lee el cuerpo de la petición, con el tope de ``MAX_CUERPO``."""
        try:
            largo = int(self.headers.get("Content-Length") or 0)
        except ValueError as exc:
            raise ErrorPeticion("Content-Length no es un número.") from exc
        if largo < 0:
            raise ErrorPeticion("Content-Length negativo.")
        if largo > MAX_CUERPO:
            raise ErrorPeticion(
                f"El cuerpo supera el tope de {MAX_CUERPO} bytes ({largo})."
            )
        return self.rfile.read(largo) if largo else b""

    # -- verbos ----------------------------------------------------------

    def _atender(self, verbo: str) -> None:
        try:
            body = self._leer_entrada()
        except ErrorPeticion as exc:
            self._responder(_mapear_error(exc))
            return
        respuesta = manejar(
            verbo,
            self.path,
            body=body,
            entorno=self.entorno,
            version_http=self.version_http,
        )
        self._responder(respuesta)

    def do_GET(self) -> None:  # noqa: N802 - nombre impuesto por http.server
        self._atender("GET")

    def do_POST(self) -> None:  # noqa: N802 - nombre impuesto por http.server
        self._atender("POST")

    def log_message(self, formato: str, *args: Any) -> None:
        """Progreso a ``stderr`` (el dato va en el cuerpo de la respuesta).

        Mismo criterio que el CLI (T-601 §2.4): el log del servidor no contamina
        el resultado que el consumidor lee.
        """
        print(f"[voucherflow-http] {self.address_string()} - {formato % args}",
              file=sys.stderr)


def construir_servidor(
    host: str = HOST_DEFAULT,
    puerto: int = PUERTO_DEFAULT,
    *,
    entorno: EntornoHTTP | None = None,
) -> ThreadingHTTPServer:
    """Construye el servidor (sin arrancarlo).

    ``ThreadingHTTPServer`` —y no ``HTTPServer``— porque las rutas llaman a Ollama
    y eso **bloquea**: con el servidor de un solo hilo, una extracción lenta
    dejaría al resto de los consumidores esperando; con hilos, el que consulta
    ``/salud`` recibe respuesta mientras otro documento se procesa.

    Devuelve el servidor; arrancarlo es ``serve_forever()`` (lo hace
    :func:`servir`). Se separa para que la suite pueda construirlo con el puerto
    ``0`` (que el SO elige) y consultarlo sin una llamada bloqueante.
    """
    ctx = entorno or EntornoHTTP()

    class _Handler(ServidorVoucherflow):
        # Subclase por servidor: los inyectables quedan en su propia clase, sin
        # pisar la de la clase base (dos servidores con entornos distintos no se
        # contaminan).
        pass

    _Handler.entorno = ctx
    servidor = ThreadingHTTPServer((host, puerto), _Handler)
    servidor.daemon_threads = True
    return servidor


def servir(
    host: str = HOST_DEFAULT,
    puerto: int = PUERTO_DEFAULT,
    *,
    entorno: EntornoHTTP | None = None,
) -> None:
    """Arranca el servidor y atiende hasta que lo interrumpan (bloqueante)."""
    servidor = construir_servidor(host, puerto, entorno=entorno)
    destino = f"http://{host}:{servidor.server_address[1]}"
    print(f"voucherflow HTTP {VERSION_HTTP} escuchando en {destino}", file=sys.stderr)
    if host not in {"127.0.0.1", "localhost", "::1"}:
        print(
            "AVISO: el servidor no trae autenticación ni TLS y está escuchando "
            f"fuera de localhost ({host}). Ponelo detrás de un proxy con auth "
            "antes de exponerlo a la red.",
            file=sys.stderr,
        )
    try:
        servidor.serve_forever()
    except KeyboardInterrupt:
        print("\nDeteniendo el servidor…", file=sys.stderr)
    finally:
        servidor.server_close()


__all__ = [
    "HOST_DEFAULT",
    "MAX_CUERPO",
    "PUERTO_DEFAULT",
    "RUTAS",
    "VERSION_HTTP",
    "EntornoHTTP",
    "ErrorPeticion",
    "Respuesta",
    "Ruta",
    "ServidorVoucherflow",
    "construir_servidor",
    "manejar",
    "servir",
]

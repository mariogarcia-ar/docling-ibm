"""Tests de la API HTTP (F6/T-606).

La API HTTP es la **segunda superficie** del sistema después del CLI, y su
principio es el mismo (E-LIB-1 / subplan §2.1): **transporta**, no reimplementa.
Los tests se organizan sobre esa frontera:

1. **La capa pura** (:func:`manejar`): despacho, validación del cuerpo y mapeo de
   errores a códigos de estado. Es lo que se ejercita in-process, sin sockets.
2. **La delegación**: cada ruta llama a ``api.*`` con los argumentos correctos y
   **no** decide nada por su cuenta (no hay reglas ni promesas duplicadas).
3. **El transporte**: un servidor real en un puerto efímero responde por HTTP —
   lo que prueba que el pegamento ``http.server`` ↔ :func:`manejar` funciona. Sin
   él, la capa pura podría estar perfecta y el servidor roto.
4. **Las fronteras**: sin autenticación declarada, rechazo ≠ error HTTP, el tope
   del cuerpo, y el mapeo de errores que **no** se aplana a 500.

Sin Ollama, sin Docling y sin red externa: los dobles entran por
:class:`EntornoHTTP` (el análogo de ``EntornoCLI`` de T-601).
"""

from __future__ import annotations

import json
import threading
import urllib.error
import urllib.request

import pytest

from voucherflow.api import DocumentoNoProcesableError, VoucherflowError
from voucherflow.http import (
    HOST_DEFAULT,
    RUTAS,
    VERSION_HTTP,
    EntornoHTTP,
    construir_servidor,
    manejar,
)
from voucherflow.http.__main__ import construir_parser, main
from voucherflow.models.ollama import OllamaError

# ---------------------------------------------------------------------------
# Dobles
# ---------------------------------------------------------------------------


class VeredictoFalso:
    """Un resultado con ``model_dump`` (el camino real de los contratos)."""

    def __init__(self, estado: str = "aprobado") -> None:
        self.estado = estado

    def model_dump(self, mode: str = "python") -> dict[str, object]:
        return {
            "documento_id": "sha256:0000",
            "estado": self.estado,
            "certeza": "alta",
            "origen": "programa",
            "tipo_comprobante": "A",
        }


class ContratoComoDict:
    """Un contrato que solo expone ``como_dict`` (el otro camino soportado)."""

    def como_dict(self) -> dict[str, object]:
        return {"campos": {"total": 100.0}, "decision": None}


def _entorno(**kwargs: object) -> EntornoHTTP:
    """Entorno con dobles por defecto; los kwargs los sobrescriben."""
    base: dict[str, object] = {
        "run": lambda origen, **kw: VeredictoFalso(),
        "extract": lambda origen, mode="kvi": ContratoComoDict(),
        "ask": lambda origen, pregunta, **kw: f"{pregunta}@{origen}",
        "version": lambda: "extraccion-kv@9",
    }
    base.update(kwargs)
    return EntornoHTTP(**base)  # type: ignore[arg-type]


def _cuerpo(datos: object) -> str:
    return json.dumps(datos)


# ---------------------------------------------------------------------------
# 1. La capa pura: despacho y contrato de rutas
# ---------------------------------------------------------------------------


def test_el_indice_lista_todas_las_rutas_declaradas() -> None:
    respuesta = manejar("GET", "/", entorno=_entorno())
    assert respuesta.estado == 200
    rutas = respuesta.cuerpo["rutas"]
    assert len(rutas) == len(RUTAS)
    declaradas = {(r.metodo, r.camino) for r in RUTAS}
    listadas = {(r["metodo"], r["camino"]) for r in rutas}
    assert listadas == declaradas


def test_el_indice_publica_la_version_del_servidor() -> None:
    respuesta = manejar("GET", "/")
    assert respuesta.cuerpo["version_http"] == VERSION_HTTP


@pytest.mark.parametrize("ruta", [r for r in RUTAS])
def test_cada_ruta_declarada_tiene_rama_de_despacho(ruta) -> None:
    """Una ruta declarada sin implementación es un bug, no un 200 vacío.

    Se ejercita cada ruta del contrato con un cuerpo mínimo válido y se exige que
    **no** devuelva el error ``RutaSinImplementar`` (que es lo que devolvería si
    alguien agrega una ``Ruta`` a ``RUTAS`` y olvida su rama).
    """
    cuerpo = _cuerpo(
        {campo: "x" for campo in ruta.campos}
    )
    respuesta = manejar(
        ruta.metodo,
        ruta.camino,
        body=cuerpo if ruta.metodo == "POST" else None,
        entorno=_entorno(),
    )
    assert respuesta.estado != 500, (
        f"{ruta.metodo} {ruta.camino} devolvió 500: falta su rama de despacho."
    )
    if isinstance(respuesta.cuerpo, dict):
        assert respuesta.cuerpo.get("error") != "RutaSinImplementar"


def test_salud_no_toca_modelos_ni_archivos() -> None:
    """``/salud`` responde aunque toda la fachada explote.

    Un health check que depende de Ollama reporta caído al **servidor** cuando lo
    que falla es un modelo. Se le pasa un entorno cuyas funciones revientan: si
    ``/salud`` las llamara, el test falla.
    """

    def revienta(*_a: object, **_k: object) -> object:
        raise AssertionError("/salud no debe llamar a la fachada")

    respuesta = manejar(
        "GET",
        "/salud",
        entorno=EntornoHTTP(run=revienta, extract=revienta, ask=revienta),
    )
    assert respuesta.estado == 200
    assert respuesta.cuerpo["estado"] == "ok"


def test_version_expone_la_version_de_extraccion() -> None:
    respuesta = manejar("GET", "/version", entorno=_entorno())
    assert respuesta.estado == 200
    assert respuesta.cuerpo["version_extraccion"] == "extraccion-kv@9"


def test_el_metodo_http_es_insensible_al_caso() -> None:
    assert manejar("get", "/salud", entorno=_entorno()).estado == 200


# ---------------------------------------------------------------------------
# 2. Delegación: la ruta llama a la fachada, no decide
# ---------------------------------------------------------------------------


def test_run_delega_en_la_fachada_con_las_opciones() -> None:
    visto: dict[str, object] = {}

    def f_run(origen: str, **kw: object) -> VeredictoFalso:
        visto["origen"] = origen
        visto.update(kw)
        return VeredictoFalso("revision")

    respuesta = manejar(
        "POST",
        "/run",
        body=_cuerpo(
            {
                "origen": "factura.pdf",
                "condicion_impositiva": "27",
                "orientation": "vertical",
                "clasificar_contable": True,
            }
        ),
        entorno=_entorno(run=f_run),
    )
    assert respuesta.estado == 200
    assert visto["condicion_impositiva"] == "27"
    assert visto["orientation"] == "vertical"
    assert visto["clasificar_contable"] is True
    assert respuesta.cuerpo["resultado"]["estado"] == "revision"


def test_run_usa_los_defaults_de_la_fachada_cuando_no_se_pasan() -> None:
    visto: dict[str, object] = {}

    def f_run(origen: str, **kw: object) -> VeredictoFalso:
        visto.update(kw)
        return VeredictoFalso()

    manejar("POST", "/run", body=_cuerpo({"origen": "a.pdf"}), entorno=_entorno(run=f_run))
    assert visto["orientation"] == "auto"
    assert visto["docling_raw"] is False
    assert visto["clasificar_contable"] is False
    assert visto["condicion_impositiva"] is None


def test_run_resuelve_la_ruta_contra_la_raiz() -> None:
    visto: dict[str, object] = {}

    def f_run(origen: str, **kw: object) -> VeredictoFalso:
        visto["origen"] = origen
        return VeredictoFalso()

    entorno = _entorno(run=f_run)
    entorno.raiz = __import__("pathlib").Path("/tmp/raiz")
    manejar("POST", "/run", body=_cuerpo({"origen": "doc.pdf"}), entorno=entorno)
    assert visto["origen"] == "/tmp/raiz/doc.pdf"


def test_una_ruta_absoluta_no_se_prefija_con_la_raiz() -> None:
    visto: dict[str, object] = {}

    def f_run(origen: str, **kw: object) -> VeredictoFalso:
        visto["origen"] = origen
        return VeredictoFalso()

    entorno = _entorno(run=f_run)
    entorno.raiz = __import__("pathlib").Path("/tmp/raiz")
    manejar("POST", "/run", body=_cuerpo({"origen": "/abs/doc.pdf"}), entorno=entorno)
    assert visto["origen"] == "/abs/doc.pdf"


def test_extract_delega_y_publica_el_contrato() -> None:
    visto: dict[str, object] = {}

    def f_extract(origen: str, mode: str = "kvi") -> ContratoComoDict:
        visto["mode"] = mode
        return ContratoComoDict()

    respuesta = manejar(
        "POST",
        "/extract",
        body=_cuerpo({"origen": "a.pdf", "mode": "11"}),
        entorno=_entorno(extract=f_extract),
    )
    assert respuesta.estado == 200
    assert visto["mode"] == "11"
    # El contrato se publica tal como la librería lo expone (``como_dict``).
    assert respuesta.cuerpo["evidencia"]["campos"] == {"total": 100.0}


def test_extract_usa_el_modo_por_defecto() -> None:
    visto: dict[str, object] = {}

    def f_extract(origen: str, mode: str = "kvi") -> ContratoComoDict:
        visto["mode"] = mode
        return ContratoComoDict()

    manejar(
        "POST", "/extract", body=_cuerpo({"origen": "a.pdf"}), entorno=_entorno(extract=f_extract)
    )
    assert visto["mode"] == "kvi"


def test_ask_delega_la_pregunta() -> None:
    visto: dict[str, object] = {}

    def f_ask(origen: str, pregunta: str, **kw: object) -> str:
        visto["pregunta"] = pregunta
        return "42"

    respuesta = manejar(
        "POST",
        "/ask",
        body=_cuerpo({"origen": "a.pdf", "pregunta": "¿total?"}),
        entorno=_entorno(ask=f_ask),
    )
    assert respuesta.estado == 200
    assert visto["pregunta"] == "¿total?"
    assert respuesta.cuerpo["respuesta"] == "42"


def test_las_rutas_post_copian_el_modo_del_equivalente_cli() -> None:
    """La trazabilidad CLI ↔ HTTP del contrato está declarada.

    Si una ruta se agrega sin decir a qué subcomando equivale, el consumidor no
    tiene forma de saber que las dos superficies hacen lo mismo.
    """
    por_camino = {r.camino: r.equivalente_cli for r in RUTAS}
    assert por_camino["/run"] == "run"
    assert por_camino["/extract"] == "extract"
    assert por_camino["/ask"] == "ask"


# ---------------------------------------------------------------------------
# 3. Mapeo de errores a códigos de estado
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("excepcion", "esperado", "por_que"),
    [
        (DocumentoNoProcesableError("formato raro"), 422, "el dato no sirve"),
        (OllamaError("sin conexión"), 503, "el modelo no responde (transitorio)"),
        (RuntimeError("inesperado"), 500, "no se sabe qué pasó"),
        (FileNotFoundError("no existe"), 422, "el archivo no está"),
        (ValueError("campo malo"), 400, "la petición está mal"),
    ],
)
def test_el_error_de_la_fachada_se_mapea_al_codigo_correcto(
    excepcion: Exception, esperado: int, por_que: str
) -> None:
    """El mapeo **no aplana** todo a 500: 422 ≠ 503 ≠ 400.

    Es la diferencia entre "reintentar no sirve" (422), "reintentar es lo que hay
    que hacer" (503) y "corregí la petición" (400).
    """

    def revienta(*_a: object, **_k: object) -> object:
        raise excepcion

    respuesta = manejar(
        "POST", "/run", body=_cuerpo({"origen": "a.pdf"}), entorno=_entorno(run=revienta)
    )
    assert respuesta.estado == esperado, f"{type(excepcion).__name__}: {por_que}"
    assert respuesta.cuerpo["error"] == type(excepcion).__name__
    assert respuesta.cuerpo["detalle"]


def test_error_peticion_no_se_confunde_con_422() -> None:
    """Un 400 (petición mal armada) no es un 422 (documento no procesable).

    Las dos clases existen para distinguir "vos te equivocaste" de "el documento
    no sirve": si se mezclaran, el consumidor no sabría si corregir su request o
    descartar el archivo.
    """
    falta_campo = manejar("POST", "/run", body=_cuerpo({}), entorno=_entorno())
    assert falta_campo.estado == 400

    def ilegible(*_a: object, **_k: object) -> object:
        raise DocumentoNoProcesableError("no soportado")

    doc_malo = manejar(
        "POST", "/run", body=_cuerpo({"origen": "x.bin"}), entorno=_entorno(run=ilegible)
    )
    assert doc_malo.estado == 422


def test_el_mapeo_soporta_las_jerarquias_cruzadas() -> None:
    """``OllamaError`` ⊂ ``RuntimeError`` y ``ErrorPeticion`` ⊂ ``ValueError``.

    El orden de las ramas de ``_mapear_error`` importa: una rama genérica antes
    que una específica se come el caso. Este test fija las cuatro relaciones que
    el orden depende.
    """
    from voucherflow.http import ErrorPeticion

    assert issubclass(DocumentoNoProcesableError, VoucherflowError)
    assert issubclass(VoucherflowError, RuntimeError)
    assert issubclass(ErrorPeticion, ValueError)
    assert issubclass(OllamaError, RuntimeError)
    assert not issubclass(OllamaError, VoucherflowError)

    # Y el efecto observable:
    def peticion_mala(*_a: object, **_k: object) -> object:
        raise ErrorPeticion("cuerpo malo")

    respuesta = manejar(
        "POST", "/run", body=_cuerpo({"origen": "a.pdf"}), entorno=_entorno(run=peticion_mala)
    )
    assert respuesta.estado == 400, (
        "ErrorPeticion debe dar 400: si una rama genérica lo captura antes, "
        "deja de ser un error de petición."
    )


# ---------------------------------------------------------------------------
# 4. Validación del cuerpo y 404/405
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("metodo", "camino", "campos"),
    [("POST", "/run", ("origen",)), ("POST", "/extract", ("origen",)),
     ("POST", "/ask", ("origen", "pregunta"))],
)
def test_falta_un_campo_obligatorio(metodo: str, camino: str, campos: tuple[str, ...]) -> None:
    """Cada campo obligatorio se exige uno por uno (400 con el nombre)."""
    for campo in campos:
        cuerpo = {c: "x" for c in campos if c != campo}
        respuesta = manejar(metodo, camino, body=_cuerpo(cuerpo), entorno=_entorno())
        assert respuesta.estado == 400, f"Faltando {campo!r} debería dar 400."
        assert campo in respuesta.cuerpo["detalle"]


def test_un_campo_vacio_cuenta_como_faltante() -> None:
    """``origen: ""`` no es un origen: es un campo sin valor."""
    respuesta = manejar(
        "POST", "/run", body=_cuerpo({"origen": "   "}), entorno=_entorno()
    )
    assert respuesta.estado == 400


@pytest.mark.parametrize("cuerpo", ["no soy json", "{incompleto", "[1,2,3]", '"texto"'])
def test_un_cuerpo_que_no_es_objeto_json_da_400(cuerpo: str) -> None:
    respuesta = manejar("POST", "/run", body=cuerpo, entorno=_entorno())
    assert respuesta.estado == 400


def test_un_cuerpo_vacio_da_400() -> None:
    for vacio in (None, b"", ""):
        assert manejar("POST", "/run", body=vacio, entorno=_entorno()).estado == 400


def test_una_ruta_inexistente_da_404_con_las_rutas_validas() -> None:
    respuesta = manejar("GET", "/no-existe", entorno=_entorno())
    assert respuesta.estado == 404
    assert respuesta.cuerpo["error"] == "RutaNoEncontrada"
    assert len(respuesta.cuerpo["rutas"]) == len(RUTAS)


def test_un_metodo_incorrecto_da_405_y_dice_cuales_valen() -> None:
    """405 ≠ 404: la ruta existe, el problema es el verbo."""
    respuesta = manejar("POST", "/salud", body="{}", entorno=_entorno())
    assert respuesta.estado == 405
    assert respuesta.cuerpo["error"] == "MetodoNoPermitido"
    assert respuesta.cuerpo["permitidos"] == ["GET"]

    # Una ruta GET con el método correcto no da 405.
    assert manejar("GET", "/salud", entorno=_entorno()).estado == 200


def test_el_query_string_se_acepta_en_el_camino_y_aparte() -> None:
    """Ambas formas del query se aceptan y no rompen el despacho."""
    assert manejar("GET", "/salud?x=1", entorno=_entorno()).estado == 200
    assert manejar("GET", "/salud", query={"x": ["1"]}, entorno=_entorno()).estado == 200


# ---------------------------------------------------------------------------
# 5. El transporte real (servidor en un puerto efímero)
# ---------------------------------------------------------------------------


@pytest.fixture()
def servidor():
    """Un servidor real en el puerto que el SO elija, con dobles."""
    srv = construir_servidor("127.0.0.1", 0, entorno=_entorno())
    hilo = threading.Thread(target=srv.serve_forever, daemon=True)
    hilo.start()
    try:
        yield srv
    finally:
        srv.shutdown()
        srv.server_close()
        hilo.join(timeout=5)


def _pedir(servidor, metodo: str, ruta: str, cuerpo: object = None):
    """Hace una petición HTTP real y devuelve ``(estado, cuerpo)``."""
    datos = _cuerpo(cuerpo).encode("utf-8") if cuerpo is not None else None
    pedido = urllib.request.Request(
        f"http://127.0.0.1:{servidor.server_address[1]}{ruta}",
        data=datos,
        method=metodo,
        headers={"Content-Type": "application/json"},
    )
    try:
        with urllib.request.urlopen(pedido, timeout=10) as respuesta:
            return respuesta.status, json.loads(respuesta.read())
    except urllib.error.HTTPError as exc:
        return exc.code, json.loads(exc.read())


def test_el_servidor_real_responde_el_indice(servidor) -> None:
    estado, cuerpo = _pedir(servidor, "GET", "/")
    assert estado == 200
    assert cuerpo["servicio"] == "voucherflow"
    assert len(cuerpo["rutas"]) == len(RUTAS)


def test_el_servidor_real_corre_el_pipeline(servidor) -> None:
    estado, cuerpo = _pedir(servidor, "POST", "/run", {"origen": "factura.pdf"})
    assert estado == 200
    assert cuerpo["resultado"]["estado"] == "aprobado"


def test_el_servidor_real_declara_los_errores_con_su_codigo(servidor) -> None:
    assert _pedir(servidor, "GET", "/nada")[0] == 404
    assert _pedir(servidor, "POST", "/salud", {})[0] == 405
    assert _pedir(servidor, "POST", "/run", {})[0] == 400


def test_el_servidor_real_responde_json() -> None:
    """El ``Content-Type`` es JSON: el consumidor no tiene que adivinar."""
    srv = construir_servidor("127.0.0.1", 0, entorno=_entorno())
    hilo = threading.Thread(target=srv.serve_forever, daemon=True)
    hilo.start()
    try:
        with urllib.request.urlopen(
            f"http://127.0.0.1:{srv.server_address[1]}/salud", timeout=10
        ) as respuesta:
            assert "application/json" in respuesta.headers["Content-Type"]
            json.loads(respuesta.read())  # es JSON parseable
    finally:
        srv.shutdown()
        srv.server_close()
        hilo.join(timeout=5)


def test_el_servidor_atiende_mientras_otra_peticion_esta_en_curso() -> None:
    """Es *threading*: una petición lenta no bloquea a las demás.

    Con ``HTTPServer`` de un solo hilo, ``/salud`` quedaría esperando a que
    termine una extracción. Se simula una ruta lenta y se comprueba que ``/salud``
    responde antes de que la otra libere.
    """
    import time

    liberado = threading.Event()

    def lento(*_a: object, **_k: object) -> VeredictoFalso:
        liberado.wait(timeout=5)
        return VeredictoFalso()

    srv = construir_servidor("127.0.0.1", 0, entorno=_entorno(run=lento))
    hilo = threading.Thread(target=srv.serve_forever, daemon=True)
    hilo.start()
    try:
        resultado: dict[str, object] = {}

        def lanzar() -> None:
            resultado["lento"] = _pedir(srv, "POST", "/run", {"origen": "a.pdf"})

        trabajador = threading.Thread(target=lanzar, daemon=True)
        trabajador.start()
        time.sleep(0.15)  # que la petición lenta llegue primero
        estado, _ = _pedir(srv, "GET", "/salud")
        assert estado == 200, "salud quedó bloqueada por la petición lenta"
        liberado.set()
        trabajador.join(timeout=5)
        assert resultado["lento"][0] == 200
    finally:
        liberado.set()
        srv.shutdown()
        srv.server_close()
        hilo.join(timeout=5)


# ---------------------------------------------------------------------------
# 6. Fronteras: lo que el servidor NO hace
# ---------------------------------------------------------------------------


def test_el_default_no_escucha_en_toda_la_red() -> None:
    """El host por defecto es localhost: el servidor no trae autenticación.

    Publicarlo es una decisión explícita (``--host 0.0.0.0``), no un descuido.
    """
    assert HOST_DEFAULT in {"127.0.0.1", "localhost", "::1"}


def test_el_indice_declara_que_no_hay_autenticacion() -> None:
    """La ausencia de auth está **declarada** en la superficie, no escondida."""
    cuerpo = manejar("GET", "/").cuerpo
    nota = cuerpo["nota"].lower()
    assert "autenticación" in nota or "autenticacion" in nota
    assert "tls" in nota


def test_un_rechazo_no_es_un_error_http() -> None:
    """Un rechazo firme se responde 200 con ``estado=rechazado``.

    El sistema **sí** concluyó (concluyó que no): devolver 4xx/5xx haría que el
    consumidor tratara una conclusión válida como una falla de infraestructura
    (misma distinción que la CLI, subplan §2.6).
    """
    respuesta = manejar(
        "POST",
        "/run",
        body=_cuerpo({"origen": "no-comprobante.pdf"}),
        entorno=_entorno(run=lambda origen, **kw: VeredictoFalso("rechazado")),
    )
    assert respuesta.estado == 200
    assert respuesta.cuerpo["resultado"]["estado"] == "rechazado"
    assert "error" not in respuesta.cuerpo


def test_el_cuerpo_tiene_tope() -> None:
    """Un cuerpo sin límite es un agujero: el tope existe y se declara."""
    from voucherflow.http import MAX_CUERPO

    assert 0 < MAX_CUERPO <= 1024 * 1024 * 1024

    srv = construir_servidor("127.0.0.1", 0, entorno=_entorno())
    hilo = threading.Thread(target=srv.serve_forever, daemon=True)
    hilo.start()
    try:
        import http.client

        conexion = http.client.HTTPConnection(
            "127.0.0.1", srv.server_address[1], timeout=10
        )
        # Se declara un Content-Length enorme sin enviar el cuerpo: el servidor
        # tiene que rechazarlo por el tope, no intentar leerlo.
        conexion.putrequest("POST", "/run")
        conexion.putheader("Content-Length", str(MAX_CUERPO + 1))
        conexion.endheaders()
        respuesta = conexion.getresponse()
        assert respuesta.status == 400
        conexion.close()
    finally:
        srv.shutdown()
        srv.server_close()
        hilo.join(timeout=5)


def test_el_servidor_no_reimplementa_el_pipeline() -> None:
    """Las rutas llaman a ``api.*``; no hay reglas ni promesas en el servidor.

    La verificación es de **diseño**: el módulo del servidor no importa los
    módulos de capacidad (processing/validation/classification/extraction/
    conclusion) — solo la fachada. Si importara un módulo interno, sería una
    segunda implementación (violación de E-LIB-1).
    """
    from pathlib import Path

    fuente = Path(
        __import__("voucherflow.http.server", fromlist=["x"]).__file__
    ).read_text(encoding="utf-8")
    # Los imports permitidos son de la fachada y de los modelos (por los errores).
    for modulo in (
        "from ..processing import",
        "from ..validation import",
        "from ..classification import",
        "from ..conclusion import",
    ):
        assert modulo not in fuente, (
            f"El servidor importa {modulo!r}: estaría reimplementando el pipeline "
            "en vez de delegar en la fachada (E-LIB-1)."
        )
    # Y sí usa la fachada.
    assert "from ..api import" in fuente


def test_la_version_http_esta_versionada() -> None:
    """El contrato HTTP tiene versión: un consumidor puede detectar un cambio."""
    assert VERSION_HTTP == "voucherflow-http@1"


# ---------------------------------------------------------------------------
# 7. El punto de entrada (``python -m voucherflow.http``)
# ---------------------------------------------------------------------------


def test_el_parser_del_servidor_acepta_host_puerto_y_raiz() -> None:
    args = construir_parser().parse_args(
        ["--host", "0.0.0.0", "--puerto", "9000", "--raiz", "/tmp/x"]
    )
    assert (args.host, args.puerto) == ("0.0.0.0", 9000)
    assert str(args.raiz) == "/tmp/x"


def test_el_parser_usa_los_defaults_seguros() -> None:
    args = construir_parser().parse_args([])
    assert args.host == HOST_DEFAULT
    assert args.puerto == 8000


def test_el_servidor_es_un_binario_aparte_del_cli() -> None:
    """El servidor **no** es el duodécimo subcomando del CLI.

    El contrato de once subcomandos (E-CLI-1) está verificado por T-604/T-605; el
    diagrama C4 muestra CLI y API HTTP como contenedores separados. Este test fija
    esa decisión: si alguien agrega ``serve`` al CLI, tiene que actualizar a
    propósito la paridad y la documentación (y este test falla).
    """
    from voucherflow.cli.main import COMANDOS

    assert len(COMANDOS) == 11
    assert "serve" not in COMANDOS
    assert "http" not in COMANDOS


def test_un_puerto_ocupado_no_propaga_una_excepcion_cruda() -> None:
    """``main`` reporta el fallo con código ≠ 0 en vez de volcar el traceback."""
    import socket

    ocupado = socket.socket()
    ocupado.bind(("127.0.0.1", 0))
    ocupado.listen(1)
    puerto = ocupado.getsockname()[1]
    try:
        codigo = main(["--host", "127.0.0.1", "--puerto", str(puerto)])
        assert codigo == 1
    finally:
        ocupado.close()

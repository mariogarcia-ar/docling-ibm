#!/usr/bin/env python
"""Inspecciona T-606 (F6) — API HTTP (fase 2, no bloqueante).

**Fase**: F6 (cliente) · **Tarea**: T-606 · **Épica**: E-CLI.

Verifica la **segunda superficie** del sistema: la API HTTP. Corre **sin** Ollama,
**sin** Docling y **sin** red externa (los dobles entran por ``EntornoHTTP``, el
análogo de ``EntornoCLI``).

Qué muestra
-----------

1. **El contrato de rutas**: cada ruta declarada en ``RUTAS`` tiene su rama de
   despacho y su equivalente en el CLI. Una ruta declarada sin implementación es
   un bug, no un 200 vacío.
2. **La delegación**: las rutas llaman a ``api.*``; el servidor no reimplementa
   el pipeline (E-LIB-1). Se comprueba que no importe los módulos de capacidad.
3. **El mapeo de errores**: ``422`` (el dato no sirve) ≠ ``503`` (el modelo no
   responde: reintentar sirve) ≠ ``400`` (la petición está mal) ≠ ``500``.
4. **El transporte real**: un servidor en un puerto efímero responde por HTTP y
   atiende en paralelo (es *threading*).
5. **Las fronteras**: el default escucha solo en localhost (no hay auth), el
   rechazo **no** es error HTTP, el cuerpo tiene tope, y el servidor es un binario
   **aparte** del CLI (el contrato de once subcomandos no se reabre).

Uso:
    python scripts/F6/t606.py                    # contrato + escenarios + fronteras
    python scripts/F6/t606.py --detalle          # + una línea por ruta
    python scripts/F6/t606.py --manual           # un pedido HTTP real, paso a paso
    python scripts/F6/t606.py --json /tmp/t606.json

La suite default cubre lo mismo en ``tests/test_http_t606.py``.
"""

from __future__ import annotations

import argparse
import json
import sys
import threading
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any

# El paquete usa layout src/; si se corre sin instalar, se agrega src/ al sys.path.
_SRC = Path(__file__).resolve().parents[2] / "src"
if str(_SRC) not in sys.path:
    sys.path.insert(0, str(_SRC))

from voucherflow.api import DocumentoNoProcesableError  # noqa: E402
from voucherflow.cli.main import COMANDOS  # noqa: E402
from voucherflow.http import (  # noqa: E402
    HOST_DEFAULT,
    MAX_CUERPO,
    RUTAS,
    VERSION_HTTP,
    EntornoHTTP,
    ErrorPeticion,
    construir_servidor,
    manejar,
)
from voucherflow.http.__main__ import construir_parser  # noqa: E402
from voucherflow.models.ollama import OllamaError  # noqa: E402


class VeredictoFalso:
    """Resultado con ``model_dump`` (el camino real de los contratos)."""

    def __init__(self, estado: str = "aprobado") -> None:
        self.estado = estado

    def model_dump(self, mode: str = "python") -> dict[str, Any]:
        return {
            "documento_id": "sha256:0000",
            "estado": self.estado,
            "certeza": "alta",
            "origen": "programa",
            "tipo_comprobante": "A",
        }


class ContratoComoDict:
    """Contrato que solo expone ``como_dict``."""

    def como_dict(self) -> dict[str, Any]:
        return {"campos": {"total": 100.0}, "decision": None}


def _entorno(**kwargs: Any) -> EntornoHTTP:
    base: dict[str, Any] = {
        "run": lambda origen, **kw: VeredictoFalso(),
        "extract": lambda origen, mode="kvi": ContratoComoDict(),
        "ask": lambda origen, pregunta, **kw: f"{pregunta}@{origen}",
        "version": lambda: "extraccion-kv@9",
    }
    base.update(kwargs)
    return EntornoHTTP(**base)


def _revienta(excepcion: Exception):
    def inner(*_a: Any, **_k: Any) -> Any:
        raise excepcion

    return inner


# ---------------------------------------------------------------------------
# Escenarios
# ---------------------------------------------------------------------------


def _escenarios() -> list[dict[str, Any]]:
    salida: list[dict[str, Any]] = []

    def registrar(nombre: str, que: str, esperado: Any, obtenido: Any) -> None:
        salida.append(
            {
                "nombre": nombre,
                "que": que,
                "esperado": esperado,
                "obtenido": obtenido,
                "ok": esperado == obtenido,
            }
        )

    # -- contrato de rutas ----------------------------------------------
    registrar(
        "indice",
        "GET / lista todas las rutas declaradas",
        len(RUTAS),
        len(manejar("GET", "/", entorno=_entorno()).cuerpo["rutas"]),
    )
    registrar(
        "version-http",
        "la versión del contrato viaja en el índice",
        VERSION_HTTP,
        manejar("GET", "/", entorno=_entorno()).cuerpo["version_http"],
    )
    registrar(
        "salud",
        "GET /salud responde sin tocar modelos (liveness)",
        200,
        manejar("GET", "/salud", entorno=_entorno()).estado,
    )
    registrar(
        "version-extraccion",
        "GET /version publica la versión de extracción de la librería",
        "extraccion-kv@9",
        manejar("GET", "/version", entorno=_entorno()).cuerpo["version_extraccion"],
    )

    # -- delegación -----------------------------------------------------
    visto: dict[str, Any] = {}

    def f_run(origen: str, **kw: Any) -> VeredictoFalso:
        visto["origen"] = origen
        visto.update(kw)
        return VeredictoFalso("revision")

    r = manejar(
        "POST",
        "/run",
        body=json.dumps(
            {"origen": "f.pdf", "condicion_impositiva": "27", "orientation": "vertical"}
        ),
        entorno=_entorno(run=f_run),
    )
    registrar("run-delega", "POST /run llama a la fachada con las opciones", 200, r.estado)
    registrar(
        "run-opciones",
        "las opciones del cuerpo llegan a la fachada",
        ("27", "vertical", "revision"),
        (visto.get("condicion_impositiva"), visto.get("orientation"),
         r.cuerpo["resultado"]["estado"]),
    )
    registrar(
        "extract-publica-contrato",
        "POST /extract publica el contrato tal como la librería lo expone",
        {"total": 100.0},
        manejar(
            "POST", "/extract", body=json.dumps({"origen": "a.pdf"}), entorno=_entorno()
        ).cuerpo["evidencia"]["campos"],
    )
    registrar(
        "ask-delega",
        "POST /ask devuelve la respuesta del modelo",
        "42",
        manejar(
            "POST",
            "/ask",
            body=json.dumps({"origen": "a.pdf", "pregunta": "x"}),
            entorno=_entorno(ask=lambda o, p, **k: "42"),
        ).cuerpo["respuesta"],
    )

    # -- mapeo de errores -----------------------------------------------
    casos = [
        (DocumentoNoProcesableError("formato"), 422, "el dato no sirve"),
        (OllamaError("sin conexión"), 503, "el modelo no responde (reintentar)"),
        (RuntimeError("inesperado"), 500, "no se sabe qué pasó"),
        (FileNotFoundError("no está"), 422, "el archivo no existe"),
    ]
    for excepcion, esperado, por_que in casos:
        registrar(
            f"error-{esperado}-{type(excepcion).__name__}",
            por_que,
            esperado,
            manejar(
                "POST",
                "/run",
                body=json.dumps({"origen": "a.pdf"}),
                entorno=_entorno(run=_revienta(excepcion)),
            ).estado,
        )
    registrar(
        "400-falta-campo",
        "un cuerpo sin el campo obligatorio da 400 (petición mal armada)",
        400,
        manejar("POST", "/run", body="{}", entorno=_entorno()).estado,
    )
    registrar(
        "400-json-malo",
        "un cuerpo que no es JSON da 400",
        400,
        manejar("POST", "/run", body="no json", entorno=_entorno()).estado,
    )
    registrar(
        "404",
        "una ruta inexistente da 404 con las rutas válidas",
        (404, len(RUTAS)),
        (
            manejar("GET", "/nada", entorno=_entorno()).estado,
            len(manejar("GET", "/nada", entorno=_entorno()).cuerpo["rutas"]),
        ),
    )
    registrar(
        "405",
        "un método incorrecto da 405 y dice cuál vale",
        (405, ["GET"]),
        (
            manejar("POST", "/salud", body="{}", entorno=_entorno()).estado,
            manejar("POST", "/salud", body="{}", entorno=_entorno()).cuerpo["permitidos"],
        ),
    )
    return salida


def _rutas() -> list[dict[str, Any]]:
    """Cada ruta: tiene rama de despacho y equivalente en el CLI."""
    salida: list[dict[str, Any]] = []
    for ruta in RUTAS:
        cuerpo = json.dumps({campo: "x" for campo in ruta.campos})
        respuesta = manejar(
            ruta.metodo,
            ruta.camino,
            body=cuerpo if ruta.metodo == "POST" else None,
            entorno=_entorno(),
        )
        sin_rama = (
            isinstance(respuesta.cuerpo, dict)
            and respuesta.cuerpo.get("error") == "RutaSinImplementar"
        )
        equivalente_ok = ruta.equivalente_cli == "—" or ruta.equivalente_cli in COMANDOS
        salida.append(
            {
                "ruta": f"{ruta.metodo} {ruta.camino}",
                "ok": not sin_rama and equivalente_ok,
                "estado": respuesta.estado,
                "equivalente_cli": ruta.equivalente_cli,
                "detalle": (
                    "tiene rama"
                    if not sin_rama
                    else "SIN RAMA DE DESPACHO"
                )
                + (
                    f" · CLI {ruta.equivalente_cli}"
                    if ruta.equivalente_cli != "—"
                    else ""
                ),
            }
        )
    return salida


def _fronteras() -> list[dict[str, Any]]:
    """Lo que el servidor **no** hace (declarado, no escondido)."""
    salida: list[dict[str, Any]] = []

    def registrar(que: str, ok: bool, detalle: str) -> None:
        salida.append({"que": que, "ok": ok, "detalle": detalle})

    # 1. El default escucha solo en local. Un servidor sin auth publicado por
    #    accidente es el riesgo; el default lo evita.
    registrar(
        "el default escucha solo en localhost",
        HOST_DEFAULT in {"127.0.0.1", "localhost", "::1"},
        f"host default = {HOST_DEFAULT} (publicar es explícito: --host)",
    )

    # 2. La falta de auth está declarada en la superficie.
    nota = manejar("GET", "/").cuerpo["nota"].lower()
    registrar(
        "declara que no hay autenticación ni TLS",
        "autenticaci" in nota and "tls" in nota,
        "el índice lo dice en su nota",
    )

    # 3. Un rechazo no es error HTTP.
    r = manejar(
        "POST",
        "/run",
        body=json.dumps({"origen": "x.pdf"}),
        entorno=_entorno(run=lambda o, **k: VeredictoFalso("rechazado")),
    )
    registrar(
        "un rechazo se responde 200 con estado=rechazado",
        r.estado == 200 and r.cuerpo["resultado"]["estado"] == "rechazado",
        "concluir que no es concluir: no es una falla de infraestructura",
    )

    # 4. El 400 y el 422 no se mezclan.
    r400 = manejar("POST", "/run", body="{}", entorno=_entorno()).estado
    r422 = manejar(
        "POST", "/run", body=json.dumps({"origen": "x"}), entorno=_entorno(run=_revienta(DocumentoNoProcesableError("x")))
    ).estado
    registrar(
        "400 (petición mal) ≠ 422 (dato que no sirve)",
        r400 == 400 and r422 == 422,
        "el consumidor sabe si corregir el request o descartar el archivo",
    )

    # 5. El servidor no reimplementa el pipeline.
    fuente = Path(
        __import__("voucherflow.http.server", fromlist=["x"]).__file__
    ).read_text(encoding="utf-8")
    internos = [
        m
        for m in ("from ..processing import", "from ..validation import",
                  "from ..classification import", "from ..conclusion import")
        if m in fuente
    ]
    registrar(
        "no reimplementa el pipeline (delega en la fachada)",
        not internos and "from ..api import" in fuente,
        "solo importa `api.*`; no hay reglas ni prompts en el servidor",
    )

    # 6. El servidor es un binario aparte: el contrato de once subcomandos del
    #    CLI (verificado por T-604/T-605) no se reabre por una tarea de fase 2.
    registrar(
        "el servidor es un binario aparte del CLI",
        len(COMANDOS) == 11 and "serve" not in COMANDOS and "http" not in COMANDOS,
        f"el CLI sigue con {len(COMANDOS)} subcomandos; el server entra por "
        "`voucherflow-http`",
    )

    # 7. El cuerpo tiene tope.
    registrar(
        "el cuerpo tiene tope declarado",
        0 < MAX_CUERPO <= 1024 * 1024 * 1024,
        f"MAX_CUERPO = {MAX_CUERPO} bytes",
    )

    # 8. El arranque no se mezcla con el CLI: mismo patrón (`main` devuelve).
    parser = construir_parser()
    args = parser.parse_args([])
    registrar(
        "el arranque tiene defaults seguros",
        args.host == HOST_DEFAULT and args.puerto == 8000,
        f"--host default {args.host}, --puerto default {args.puerto}",
    )
    registrar(
        "el arranque acepta puerto 0 (el SO elige)",
        parser.parse_args(["--puerto", "0"]).puerto == 0,
        "útil para pruebas y para no fijar un puerto",
    )
    return salida


def _manual() -> None:
    """Un pedido HTTP **real** contra un servidor en un puerto efímero."""
    print("\nUn pedido HTTP real, paso a paso")
    entorno = _entorno()
    srv = construir_servidor("127.0.0.1", 0, entorno=entorno)
    puerto = srv.server_address[1]
    hilo = threading.Thread(target=srv.serve_forever, daemon=True)
    hilo.start()
    base = f"http://127.0.0.1:{puerto}"
    print(f"  servidor en {base} (en un hilo, con dobles: sin Ollama)")
    try:
        for metodo, ruta, cuerpo in (
            ("GET", "/", None),
            ("GET", "/salud", None),
            ("POST", "/run", {"origen": "comprobante.pdf"}),
            ("POST", "/ask", {"origen": "comprobante.pdf", "pregunta": "¿total?"}),
            ("GET", "/ruta-que-no-existe", None),
        ):
            datos = json.dumps(cuerpo).encode() if cuerpo else None
            req = urllib.request.Request(
                base + ruta, data=datos, method=metodo,
                headers={"Content-Type": "application/json"},
            )
            try:
                with urllib.request.urlopen(req, timeout=10) as resp:
                    estado, contenido = resp.status, json.loads(resp.read())
            except urllib.error.HTTPError as exc:
                estado, contenido = exc.code, json.loads(exc.read())
            etiqueta = (
                contenido.get("resultado")
                or contenido.get("respuesta")
                or contenido.get("detalle")
                or contenido.get("estado")
                or f"{len(contenido.get('rutas', []))} rutas"
            )
            print(f"    {metodo:5} {ruta:<22} → {estado}  {str(etiqueta)[:58]}")
    finally:
        srv.shutdown()
        srv.server_close()
        hilo.join(timeout=5)
        print("  servidor detenido")


def main_cli() -> None:  # noqa: D401
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--detalle", action="store_true", help="Una línea por ruta.")
    parser.add_argument("--manual", action="store_true", help="Un pedido HTTP real.")
    parser.add_argument("--json", type=Path, default=None, help="Guardar el reporte.")
    args = parser.parse_args()

    reporte: dict[str, Any] = {
        "tarea": "T-606",
        "version_http": VERSION_HTTP,
        "rutas": _rutas(),
        "escenarios": _escenarios(),
        "fronteras": _fronteras(),
    }

    print("API HTTP — F6/T-606 (fase 2, no bloqueante)")
    print(f"Contrato: {VERSION_HTTP} · {len(RUTAS)} rutas · host default {HOST_DEFAULT}")

    print("\nContrato de rutas")
    print("-" * 18)
    for ruta in reporte["rutas"]:
        marca = "✅" if ruta["ok"] else "❌"
        print(f"  {marca} {ruta['ruta']:<18} {ruta['detalle']}")
        if args.detalle:
            print(f"       estado con cuerpo mínimo: {ruta['estado']}")

    print("\nEscenarios")
    print("-" * 10)
    fallos = 0
    for escenario in reporte["escenarios"]:
        marca = "✅" if escenario["ok"] else "❌"
        print(f"  {marca} {escenario['nombre']:<28} {escenario['que']}")
        if not escenario["ok"]:
            print(f"       esperado: {escenario['esperado']}")
            print(f"       obtenido: {escenario['obtenido']}")
            fallos += 1

    print("\nFronteras de T-606 (lo que NO hace)")
    print("-" * 34)
    for frontera in reporte["fronteras"]:
        marca = "✅" if frontera["ok"] else "❌"
        print(f"  {marca} {frontera['que']}")
        print(f"       {frontera['detalle']}")
        if not frontera["ok"]:
            fallos += 1

    if args.manual:
        _manual()

    ok = sum(1 for e in reporte["escenarios"] if e["ok"])
    ok_f = sum(1 for f in reporte["fronteras"] if f["ok"])
    rutas_ok = sum(1 for r in reporte["rutas"] if r["ok"])
    print(f"\nRutas verificadas: {rutas_ok}/{len(reporte['rutas'])}")
    print(f"Escenarios verificados: {ok}/{len(reporte['escenarios'])}")
    print(f"Fronteras verificadas: {ok_f}/{len(reporte['fronteras'])}")

    reporte["fallos"] = fallos + (len(reporte["rutas"]) - rutas_ok)
    if args.json:
        args.json.write_text(
            json.dumps(reporte, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        print(f"Reporte guardado en {args.json}")
    sys.exit(1 if reporte["fallos"] else 0)


if __name__ == "__main__":
    main_cli()

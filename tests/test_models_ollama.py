"""Tests de ``OllamaClient`` con mock de HTTP (F0/T-005).

Cubren (R-04): retry/backoff ante 429 y errores transitorios, timeout, mensajes
de error claros y parseo de respuesta. **No** llaman a Ollama real: se inyecta
una ``session`` falsa (``FakeSession``) que simula respuestas HTTP sin red.
"""

from __future__ import annotations

import json
from typing import Any

import pytest

from voucherflow.models.ollama import (
    OllamaClient,
    OllamaError,
    OllamaHTTPError,
    _calcular_backoff,
)


class FakeResponse:
    """Respuesta HTTP falsa con status, json() y text."""

    def __init__(self, status: int, body: dict[str, Any] | None = None, text: str = "") -> None:
        self.status_code = status
        self._body = body
        self._text = text

    def json(self) -> dict[str, Any]:
        if self._body is None:
            raise ValueError("no json")
        return self._body

    @property
    def text(self) -> str:
        return self._text or json.dumps(self._body or {})


class FakeSession:
    """Sesión falsa que devuelve respuestas en secuencia (o repetidas).

    - ``respuestas``: lista de respuestas a devolver en orden; si se agota,
      repite la última (para probar que se reintenta N veces).
    - ``errores``: lista de excepciones a lanzar en orden (para timeout/conn).
    """

    def __init__(
        self,
        respuestas: list[FakeResponse] | None = None,
        errores: list[Exception] | None = None,
    ) -> None:
        self.respuestas = list(respuestas or [])
        self.errores = list(errores or [])
        self.llamadas: list[dict[str, Any]] = []
        # Control manual del sleep para no esperar en tests.
        self.sleeps: list[float] = []

    def post(self, url: str, data: str, headers: dict | None = None, timeout: float | None = None):
        self.llamadas.append({"url": url, "data": data, "headers": headers, "timeout": timeout})
        if self.errores:
            exc = self.errores.pop(0)
            if not self.errores:  # repite el último error si se agota (persistente)
                self.errores.append(exc)
            raise exc
        if self.respuestas:
            resp = self.respuestas.pop(0)
            if not self.respuestas:  # repite la última si se agota
                self.respuestas.append(resp)
            return resp
        return FakeResponse(200, {"message": {"content": "ok"}})


def _cliente(session: FakeSession, **kwargs) -> OllamaClient:
    """Cliente con la sesión falsa y backoff en 0 para no dormir en tests."""
    kwargs.setdefault("backoff_base_s", 0.0)
    kwargs.setdefault("backoff_max_s", 0.0)
    kwargs.setdefault("max_reintentos", 3)
    return OllamaClient(session=session, **kwargs)


def _payload_ok() -> FakeResponse:
    return FakeResponse(200, {"message": {"content": "Hola, soy el asistente"}})


def _payload_429() -> FakeResponse:
    return FakeResponse(429, {"error": "Request Rate Too Large"})


def _payload_500() -> FakeResponse:
    return FakeResponse(500, {"error": "Internal Server Error"})


class TestOllamaClientAsk:
    def test_respuesta_ok(self):
        session = FakeSession(respuestas=[_payload_ok()])
        cli = _cliente(session)
        resp = cli.ask(
            messages=[{"role": "user", "content": "hola"}],
            model="qwen2.5vl:3b",
        )
        assert resp.contenido == "Hola, soy el asistente"
        assert resp.status == 200
        assert resp.reintentos == 0
        # payload correcto
        payload = json.loads(session.llamadas[0]["data"])
        assert payload["model"] == "qwen2.5vl:3b"
        assert payload["stream"] is False
        assert payload["messages"][0]["role"] == "user"

    def test_json_format_envia_format_json(self):
        session = FakeSession(respuestas=[FakeResponse(200, {"message": {"content": '{"a":1}'}})])
        cli = _cliente(session)
        resp = cli.ask([{"role": "user", "content": "x"}], model="m", json_format=True)
        payload = json.loads(session.llamadas[0]["data"])
        assert payload["format"] == "json"
        assert resp.json() == {"a": 1}

    def test_num_ctx_se_inyecta_en_options(self):
        session = FakeSession(respuestas=[_payload_ok()])
        cli = _cliente(session)
        cli.ask([{"role": "user", "content": "x"}], model="m", num_ctx=4096)
        payload = json.loads(session.llamadas[0]["data"])
        assert payload["options"]["num_ctx"] == 4096

    def test_reintenta_429_y_luego_ok(self):
        # 429 → reintenta → 200.
        session = FakeSession(respuestas=[_payload_429(), _payload_ok()])
        cli = _cliente(session)
        resp = cli.ask([{"role": "user", "content": "x"}], model="m")
        assert resp.status == 200
        assert len(session.llamadas) == 2
        assert resp.reintentos == 1

    def test_429_repetido_agota_reintentos_con_error_claro(self):
        session = FakeSession(respuestas=[_payload_429()])  # se repite siempre
        cli = _cliente(session, max_reintentos=2)
        with pytest.raises(OllamaError) as exc:
            cli.ask([{"role": "user", "content": "x"}], model="m")
        msg = str(exc.value)
        assert "reintento" in msg.lower() or "429" in msg
        # 1 intento + 2 reintentos = 3 llamadas
        assert len(session.llamadas) == 3

    def test_status_no_reintentable_lanza_error_http(self):
        session = FakeSession(respuestas=[_payload_500()])
        cli = _cliente(session)
        with pytest.raises(OllamaHTTPError) as exc:
            cli.ask([{"role": "user", "content": "x"}], model="m")
        assert exc.value.status == 500
        # 500 no está en los reintentables por defecto → 1 sola llamada
        assert len(session.llamadas) == 1

    def test_timeout_reintenta_y_agota_con_mensaje_claro(self):
        import requests

        session = FakeSession(errores=[requests.exceptions.Timeout()])
        cli = _cliente(session, max_reintentos=1)
        with pytest.raises(OllamaError) as exc:
            cli.ask([{"role": "user", "content": "x"}], model="m")
        assert "timeout" in str(exc.value).lower()

    def test_connection_error_reintenta(self):
        import requests

        session = FakeSession(errores=[requests.exceptions.ConnectionError()])
        cli = _cliente(session, max_reintentos=1)
        # 1er intento falla (conn), reintento 1 falla de nuevo → agota
        with pytest.raises(OllamaError):
            cli.ask([{"role": "user", "content": "x"}], model="m")
        assert len(session.llamadas) == 2

    def test_cuerpo_sin_message_content_error_claro(self):
        session = FakeSession(respuestas=[FakeResponse(200, {"raro": True})])
        cli = _cliente(session)
        with pytest.raises(OllamaError) as exc:
            cli.ask([{"role": "user", "content": "x"}], model="m")
        assert "message.content" in str(exc.value)

    def test_reintentar_false_desactiva_reintentos(self):
        session = FakeSession(respuestas=[_payload_429()])
        cli = _cliente(session, max_reintentos=3)
        with pytest.raises(OllamaError):
            cli.ask([{"role": "user", "content": "x"}], model="m", reintentar=False)
        assert len(session.llamadas) == 1


class TestBackoff:
    def test_backoff_exponencial_con_tope(self):
        # reintento 0 → base; reintento 1 → base*factor; ... tope max
        assert _calcular_backoff(0, 1.0, 15.0) == 1.0
        assert _calcular_backoff(1, 1.0, 15.0) == 2.0
        assert _calcular_backoff(2, 1.0, 15.0) == 4.0
        # tope: 1.0 * 2**6 = 64 > 15 → 15
        assert _calcular_backoff(6, 1.0, 15.0) == 15.0

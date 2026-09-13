"""Cliente Ollama robusto (F0, T-005) — chat VLM/LLM con retry y diagnóstico.

Encapsula la lógica de ``ask_ollama`` de v1 (``v1/extraction_invoice/ask.py``:
URL, ``json_format``, ``num_ctx``, manejo de 429/backoff, mensajes claros) en
una clase reutilizable y testeable (E-LIB-3 / ADR-007).

Responsabilidades:
* Construir el payload de chat de Ollama (``/api/chat``, ``stream=False``).
* Reintentar con **backoff exponencial** ante 429 (Request Rate Too Large) y
  errores transitorios de conexión.
* Timeout de conexión/lectura configurable.
* Mensajes de error **claros en español** (mismo espíritu que v1).
* Log de diagnóstico (latencia, status, reintentos) aprovechable luego por el
  patrón de observabilidad (E-LIB-5): ante latencia > umbral o status
  inesperado se registra el detalle.

El cliente **no** valida respuestas contra el schema de evidencia: esa
responsabilidad vive en los módulos consumidores (extraction/classification),
según el diseño de ``models/`` (doc 03-arquitectura/MODELS.md §3).
"""

from __future__ import annotations

import json
import logging
import time
from dataclasses import dataclass
from typing import Any

import requests

logger = logging.getLogger("voucherflow.models.ollama")

#: Endpoint de chat de la API de Ollama (relativo a la URL base).
ENDPOINT_CHAT = "/api/chat"

#: Status HTTP que se consideran transitorios y reintentables.
STATUS_REINTENTABLES = (429, 502, 503, 504)

#: Umbral de latencia (s) a partir del cual se registra diagnóstico E-LIB-5.
UMBRAL_LATENCIA_DIAGNOSTICO_S = 30.0


class OllamaError(RuntimeError):
    """Error de comunicación con Ollama, con mensaje claro en español."""


class OllamaHTTPError(OllamaError):
    """El servidor respondió con un status HTTP de error (no reintentable)."""

    def __init__(self, status: int, detalle: str, url: str) -> None:
        self.status = status
        self.detalle = detalle
        super().__init__(
            f"Ollama rechazó la solicitud (HTTP {status}) en {url}: {detalle}"
        )


class OllamaTimeoutError(OllamaError):
    """Timeout de conexión/lectura contra Ollama."""


@dataclass
class RespuestaOllama:
    """Respuesta normalizada de una llamada a Ollama."""

    contenido: str  # texto del mensaje (para json_format, es el JSON crudo)
    modelo: str
    latencia_s: float
    status: int
    reintentos: int = 0

    def json(self) -> Any:
        """Parsea ``contenido`` como JSON (útil cuando ``json_format=True``).

        Lanza ``json.JSONDecodeError`` si el contenido no es JSON válido.
        """
        return json.loads(self.contenido)


def _calcular_backoff(
    reintento: int, base_s: float, max_s: float, factor: float = 2.0
) -> float:
    """Backoff exponencial: ``base * factor**reintento``, tope ``max_s``.

    ``reintento`` es 0-based (primer reintento usa ``base_s``).
    """
    espera = base_s * (factor ** max(0, reintento))
    return min(espera, max_s)


class OllamaClient:
    """Cliente de chat contra un servidor Ollama (VLM/LLM).

    Parámetros de construcción (además de la config):
        url: URL base del servidor (default ``http://localhost:11434``).
        timeout_s: Timeout de conexión/lectura por intento.
        max_reintentos: Máximo de reintentos ante 429/errores transitorios.
        backoff_base_s / backoff_max_s: Ventana de espera del backoff.
        reintentar_status: Status HTTP que disparan reintento.
        session: Sesión ``requests`` inyectable (para tests con mock HTTP).
        logger_diag: Logger opcional para el diagnóstico (default: el de módulo).

    Para tests se recomienda inyectar una ``session`` cuyo ``transport``/adapter
    responda sin red real (mock de HTTP), o mockear ``requests.Session.post``.
    """

    def __init__(
        self,
        url: str = "http://localhost:11434",
        timeout_s: float = 120.0,
        max_reintentos: int = 3,
        backoff_base_s: float = 1.0,
        backoff_max_s: float = 15.0,
        reintentar_status: tuple[int, ...] = STATUS_REINTENTABLES,
        session: requests.Session | None = None,
        logger_diag: logging.Logger | None = None,
    ) -> None:
        self.url_base = url.rstrip("/")
        self.timeout_s = timeout_s
        self.max_reintentos = max_reintentos
        self.backoff_base_s = backoff_base_s
        self.backoff_max_s = backoff_max_s
        self.reintentar_status = tuple(reintentar_status)
        self._session = session if session is not None else requests.Session()
        self._logger = logger_diag or logger

    # ------------------------------------------------------------------ API

    def ask(
        self,
        messages: list[dict[str, str]],
        model: str,
        json_format: bool = False,
        options: dict[str, Any] | None = None,
        num_ctx: int | None = None,
        reintentar: bool | None = None,
    ) -> RespuestaOllama:
        """Envía un chat a Ollama y devuelve la respuesta normalizada.

        Compatible en parámetros con ``ask_ollama`` de v1: ``messages``,
        ``model``, ``json_format``, ``options``. Agrega ``num_ctx`` explícito
        (se inyecta en ``options``) y devuelve un objeto tipado en lugar de un
        ``str`` (el contenido queda en ``respuesta.contenido``).

        Devuelve:
            :class:`RespuestaOllama` con el contenido del mensaje.

        Lanza:
            :class:`OllamaError` (o subclases) con mensaje claro en español si
            no se pudo completar tras los reintentos.
        """
        reintentos_max = self.max_reintentos if reintentar is None else (int(reintentar) if reintentar else 0)
        return self._ask_con_reintentos(
            messages=messages,
            model=model,
            json_format=json_format,
            options=options,
            num_ctx=num_ctx,
            reintentos_max=max(0, reintentos_max),
        )

    def _ask_con_reintentos(
        self,
        *,
        messages: list[dict[str, str]],
        model: str,
        json_format: bool,
        options: dict[str, Any] | None,
        num_ctx: int | None,
        reintentos_max: int,
    ) -> RespuestaOllama:
        url = f"{self.url_base}{ENDPOINT_CHAT}"
        opciones = dict(options or {})
        if num_ctx is not None:
            opciones["num_ctx"] = num_ctx
        payload: dict[str, Any] = {"model": model, "messages": messages, "stream": False}
        if json_format:
            payload["format"] = "json"
        if opciones:
            payload["options"] = opciones

        reintento = 0
        while True:
            try:
                return self._intento_unico(url, payload, reintento)
            except _Reintentar as exc:
                reintento += 1
                if reintento > reintentos_max:
                    raise OllamaError(
                        f"No se pudo completar la llamada a Ollama tras {reintentos_max} "
                        f"reintento(s) (último error: {exc})"
                    ) from exc
                espera = _calcular_backoff(reintento - 1, self.backoff_base_s, self.backoff_max_s)
                self._logger.warning(
                    "Ollama: reintento %d/%d en %.1fs por %s (modelo=%s)",
                    reintento,
                    reintentos_max,
                    espera,
                    exc,
                    model,
                )
                time.sleep(espera)

    def _intento_unico(self, url: str, payload: dict[str, Any], reintento: int) -> RespuestaOllama:
        inicio = time.monotonic()
        try:
            resp = self._session.post(
                url,
                data=json.dumps(payload),
                headers={"Content-Type": "application/json"},
                timeout=self.timeout_s,
            )
        except requests.exceptions.Timeout as exc:
            latencia = time.monotonic() - inicio
            self._diagnostico(latencia, None, "timeout", reintento)
            raise _Reintentar(f"timeout de {self.timeout_s}s") from exc
        except requests.exceptions.ConnectionError as exc:
            latencia = time.monotonic() - inicio
            self._diagnostico(latencia, None, "connection_error", reintento)
            raise _Reintentar(
                f"no se pudo conectar con Ollama en {self.url_base}. "
                "¿Está corriendo el servicio? ('ollama serve')"
            ) from exc
        except requests.exceptions.RequestException as exc:
            latencia = time.monotonic() - inicio
            self._diagnostico(latencia, None, "request_error", reintento)
            raise OllamaError(
                f"Error de comunicación con Ollama en {self.url_base}: {exc}"
            ) from exc

        latencia = time.monotonic() - inicio
        status = resp.status_code

        if status == 200:
            self._diagnostico(latencia, status, "ok", reintento)
            try:
                body = resp.json()
            except ValueError as exc:
                raise OllamaError(
                    f"Ollama devolvió una respuesta no JSON válida (HTTP 200): {resp.text[:200]!r}"
                ) from exc
            try:
                contenido = body["message"]["content"]
            except (KeyError, TypeError) as exc:
                raise OllamaError(
                    "Ollama devolvió un cuerpo sin 'message.content': "
                    f"{json.dumps(body, ensure_ascii=False)[:200]}"
                ) from exc
            return RespuestaOllama(
                contenido=contenido,
                modelo=payload.get("model", ""),
                latencia_s=latencia,
                status=status,
                reintentos=reintento,
            )

        if status in self.reintentar_status:
            detalle = self._detalle_error(resp)
            self._diagnostico(latencia, status, "retryable", reintento)
            raise _Reintentar(f"HTTP {status}: {detalle}")

        # Status no reintentable → error claro
        detalle = self._detalle_error(resp)
        self._diagnostico(latencia, status, "error", reintento)
        raise OllamaHTTPError(status=status, detalle=detalle, url=url)

    # ------------------------------------------------------------- helpers

    @staticmethod
    def _detalle_error(resp: requests.Response) -> str:
        try:
            cuerpo = resp.json()
            if isinstance(cuerpo, dict) and cuerpo.get("error"):
                return str(cuerpo["error"])
        except ValueError:
            pass
        return resp.text[:300] or "(sin detalle)"

    def _diagnostico(
        self,
        latencia: float,
        status: int | None,
        tipo: str,
        reintento: int,
    ) -> None:
        """Log de diagnóstico (E-LIB-5): latencia > umbral o status inesperado.

        Aprovechable por el patrón de observabilidad: ante latencia alta o
        status no esperado se registra el detalle completo.
        """
        if status is None:
            self._logger.warning(
                "Ollama diagnóstico: tipo=%s latencia=%.1fs reintento=%d url=%s",
                tipo,
                latencia,
                reintento,
                self.url_base,
            )
        elif status == 200:
            if latencia > UMBRAL_LATENCIA_DIAGNOSTICO_S:
                self._logger.warning(
                    "Ollama diagnóstico: latencia %.1fs supera umbral %.1fs url=%s",
                    latencia,
                    UMBRAL_LATENCIA_DIAGNOSTICO_S,
                    self.url_base,
                )
        elif tipo == "retryable":
            self._logger.warning(
                "Ollama diagnóstico: status=%d (reintentable) latencia=%.1fs url=%s",
                status,
                latencia,
                self.url_base,
            )
        else:
            self._logger.error(
                "Ollama diagnóstico: status=%d tipo=%s latencia=%.1fs url=%s",
                status,
                tipo,
                latencia,
                self.url_base,
            )


class _Reintentar(Exception):
    """Señal interna: reintentar la llamada (429 o error transitorio)."""


__all__ = [
    "ENDPOINT_CHAT",
    "STATUS_REINTENTABLES",
    "OllamaClient",
    "OllamaError",
    "OllamaHTTPError",
    "OllamaTimeoutError",
    "RespuestaOllama",
    "calcular_backoff",
]

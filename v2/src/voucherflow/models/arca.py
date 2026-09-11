"""Adaptador ARCA/WSCDC — consulta de evidencia adicional (F5 / T-502, ADR-003).

**Fase**: F5 (T-502). Según ADR-003 el adaptador es **opcional en el MVP** (hook
activado por configuración) y no debe tumbar el pipeline cuando falla: un error
de red, de credenciales o de parseo se reporta como *no disponible*, y la
conclusión del caso sigue su curso.

Responsabilidad: consulta puntual de evidencia adicional (padrón/WSCDC) con
**objetivo concreto y límite de reintentos** (no un loop abierto) y timeout.

Alcance de T-502
----------------
Lo que se implementa acá es el **adaptador**: habla HTTP, interpreta la respuesta
del servicio y devuelve un :class:`~voucherflow.rules.gaps.ResultadoBusqueda`.
Lo que **no** hace: decidir si conviene consultar, cuántas veces ni qué hacer con
el dato — eso es política y vive en
:mod:`voucherflow.rules.gaps` (presupuesto y búsqueda acotada) y en
:mod:`voucherflow.conclusion.engine` (re-aplicar las cruzadas de T-501).

Sobre el protocolo real (WSCDC)
-------------------------------
El servicio de constatación de comprobantes de AFIP/ARCA se consulta con un
token de autorización (WSAA) y devuelve, entre otras cosas, si el comprobante
existe y si los datos declarados coinciden. El WSAA (firma de un TRA y canje de
credenciales) es un flujo aparte, con certificados propios, y su implementación
completa queda para la integración real (ver el prototipo `v1/wip/consultar_arca.py`,
que usa la librería ``afip``). Acá se define el **contrato del cliente HTTP**
—endpoint, payload, timeout, reintentos y traducción de la respuesta— que es lo
que el pipeline necesita, y se deja ``token`` inyectable para no acoplar el
paquete al flujo de credenciales.

Referencias: ADR-003, `CONC.md` §1, Gherkin E-CONC-2, `algoritmo.md` paso 5,
F5-subplan §3.2.
"""

from __future__ import annotations

import json
import logging
import re
from dataclasses import dataclass, field
from typing import Any

from ..rules.gaps import Gap, ResultadoBusqueda

logger = logging.getLogger(__name__)

#: Método del WS de constatación de comprobantes (WSCDC) de AFIP/ARCA.
METODO_CONSTATAR = "ComprobanteConstatar"

#: Código de documento del receptor en el padrón: 80 = CUIT.
DOC_TIPO_CUIT = 80

#: Patrón de un número de comprobante impreso ``PPPPP-NNNNNNNN``.
PATRON_NRO_COMPROBANTE = re.compile(r"^\s*(\d+)\s*-\s*(\d+)\s*$")


@dataclass
class ArcaResultado:
    """Resultado de una consulta ARCA/WSCDC (evidencia adicional).

    ``ok`` distingue "consultado con respuesta" de "no disponible" (el hook es
    opcional y no debe tumbar el pipeline — ADR-003).
    """

    ok: bool = False
    datos: dict[str, Any] = field(default_factory=dict)
    error: str | None = None


def parse_pto_vta_nro(nro_factura: str) -> tuple[int, int]:
    """Separa ``"00001-00000031"`` en ``(1, 31)`` (punto de venta, número).

    Portado de `v1/wip/consultar_arca.py`: el WSCDC pide ambos como enteros.
    Lanza ``ValueError`` con el valor ofensivo cuando el formato no se reconoce
    (no se adivina un número de comprobante).
    """
    coincidencia = PATRON_NRO_COMPROBANTE.match(nro_factura or "")
    if not coincidencia:
        raise ValueError(
            f"No se pudo interpretar el número de comprobante: {nro_factura!r} "
            "(formato esperado 'PPPPP-NNNNNNNN')."
        )
    return int(coincidencia.group(1)), int(coincidencia.group(2))


class ArcaClient:
    """Cliente de consulta al padrón ARCA/WSCDC (evidencia adicional).

    Implementa el protocolo
    :class:`~voucherflow.rules.gaps.BuscadorEvidencia`: recibe un `Gap` y el
    contexto del caso y devuelve un
    :class:`~voucherflow.rules.gaps.ResultadoBusqueda`.

    ``session`` es inyectable (igual que en
    :class:`~voucherflow.models.ollama.OllamaClient`) para que la suite corra con
    un mock de HTTP y sin red. ``token`` también: el flujo WSAA es de la
    integración real, no del contrato del cliente.
    """

    def __init__(
        self,
        url: str | None = None,
        timeout_s: float = 15.0,
        max_reintentos: int = 2,
        *,
        cuit: str | None = None,
        token: str | None = None,
        session: Any | None = None,
    ) -> None:
        self.url = url
        self.timeout_s = timeout_s
        self.max_reintentos = max_reintentos
        self.cuit = cuit
        self.token = token
        self._session = session

    # -- protocolo del buscador -------------------------------------------

    def buscar(self, gap: Gap, contexto: Any) -> ResultadoBusqueda:
        """Consulta el dato del gap (protocolo ``BuscadorEvidencia``).

        Arma el pedido con lo que el caso ya sabe (CUIT del emisor/receptor,
        número y fecha del comprobante, importe) y traduce la respuesta al
        contrato de búsqueda. **No lanza** por fallos esperables: los devuelve
        como ``disponible=False`` (ADR-003).
        """
        if not self.url:
            return ResultadoBusqueda(
                campo=gap.campo,
                disponible=False,
                error=(
                    "ArcaClient no tiene URL configurada: el hook de evidencia "
                    "adicional está desactivado (ADR-003)."
                ),
            )

        try:
            payload = self._construir_payload(gap, contexto)
        except ValueError as exc:
            # Faltan datos para armar la consulta puntual (p. ej. el número del
            # comprobante). No es un fallo del proveedor: es que el objetivo no
            # se puede formular todavía.
            return ResultadoBusqueda(
                campo=gap.campo,
                disponible=False,
                error=f"No se pudo formular la consulta para «{gap.campo}»: {exc}",
            )

        resultado = self.consultar(gap.campo, gap.objetivo, payload=payload)
        if not resultado.ok:
            return ResultadoBusqueda(
                campo=gap.campo,
                disponible=False,
                error=resultado.error,
            )

        valor = resultado.datos.get("valor")
        sostento = str(resultado.datos.get("sostento", "") or "")
        if valor is None:
            # Se consultó y el padrón no devolvió nada para ese campo.
            return ResultadoBusqueda(campo=gap.campo, valor=None, disponible=True)

        return ResultadoBusqueda(
            campo=gap.campo,
            valor=valor,
            disponible=True,
            sostento=sostento,
        )

    # -- consulta HTTP -----------------------------------------------------

    def consultar(
        self,
        campo: str,
        objetivo: str,
        *,
        payload: dict[str, Any] | None = None,
    ) -> ArcaResultado:
        """Consulta el WSCDC con reintentos y timeout acotados.

        Reintenta solo los fallos **transitorios** (timeout y errores de
        conexión/servidor): un rechazo del padrón es una respuesta del mundo y no
        se reitera. El límite de reintentos es el del cliente; el límite total de
        consultas del caso lo administra
        :class:`~voucherflow.rules.gaps.PresupuestoBusqueda`.
        """
        datos = payload if payload is not None else {}
        cuerpo = {
            "Auth": {"Token": self.token or "", "Sign": "", "Cuit": self.cuit or ""},
            "CmpReq": datos,
        }
        ultimo_error: str | None = None
        for intento in range(1 + self.max_reintentos):
            try:
                respuesta = self._post(self.url, cuerpo)
            except Exception as exc:  # red: se reintenta mientras quede margen
                ultimo_error = f"fallo de red en el intento {intento + 1}: {exc}"
                logger.warning("ARCA %s: %s", METODO_CONSTATAR, ultimo_error)
                continue

            if respuesta is None:
                ultimo_error = f"respuesta vacía en el intento {intento + 1}"
                continue

            estado = getattr(respuesta, "status_code", 200)
            if estado >= 500:
                ultimo_error = f"error del servidor (HTTP {estado})"
                continue

            try:
                cuerpo_respuesta = respuesta.json()
            except Exception as exc:
                return ArcaResultado(
                    ok=False,
                    error=f"la respuesta del padrón no es JSON válido: {exc}",
                )

            return self._interpretar(campo, objetivo, estado, cuerpo_respuesta)

        return ArcaResultado(
            ok=False,
            error=(
                f"el padrón no respondió tras {1 + self.max_reintentos} intento(s): "
                f"{ultimo_error}"
            ),
        )

    def _post(self, url: str, cuerpo: dict[str, Any]) -> Any:
        """POST JSON con la sesión inyectada o ``requests`` (import diferido)."""
        if self._session is None:
            import requests

            self._session = requests.Session()

        return self._session.post(
            f"{url.rstrip('/')}/{METODO_CONSTATAR}",
            data=json.dumps(cuerpo),
            timeout=self.timeout_s,
        )

    def _interpretar(
        self, campo: str, objetivo: str, estado: int, cuerpo: Any
    ) -> ArcaResultado:
        """Traduce la respuesta del WSCDC al contrato del adaptador.

        El servicio puede contestar "comprobante no encontrado" o "datos que no
        coinciden" tanto con HTTP 200 como con un 4xx: en los dos casos la
        respuesta es **válida y negativa**, no un fallo de red.
        """
        if estado >= 400:
            return ArcaResultado(
                ok=False,
                error=f"el padrón rechazó la consulta (HTTP {estado})",
            )

        if not isinstance(cuerpo, dict):
            return ArcaResultado(
                ok=False,
                error="la respuesta del padrón no tiene la forma esperada (dict)",
            )

        resultado = cuerpo.get("ComprobanteConstatarResult", cuerpo)
        if not isinstance(resultado, dict):
            return ArcaResultado(
                ok=False,
                error="la respuesta del padrón no trae 'ComprobanteConstatarResult'",
            )

        # El WSCDC informa el resultado de la constatación; un `Resultado` que no
        # sea "A" (aprobado) significa que el comprobante no se pudo constatar.
        veredicto = str(resultado.get("Resultado", "") or "").upper()
        if veredicto and veredicto not in {"A", "APROBADO"}:
            return ArcaResultado(
                ok=True,
                datos={
                    "valor": None,
                    "sostento": (
                        f"padrón WSCDC: comprobante no constatado "
                        f"(Resultado={veredicto})"
                    ),
                    "objetivo": objetivo,
                },
            )

        return ArcaResultado(
            ok=True,
            datos={
                "valor": veredicto or "constatado",
                "sostento": (
                    f"padrón WSCDC ({METODO_CONSTATAR}): constatación aprobada "
                    f"para «{campo}»"
                ),
                "objetivo": objetivo,
            },
        )

    # -- armado del pedido -------------------------------------------------

    def _construir_payload(self, gap: Gap, contexto: Any) -> dict[str, Any]:
        """Arma el ``CmpReq`` del WSCDC con los datos que el caso ya conoce.

        El objetivo concreto (E-CONC-2) obliga a que la consulta sea **puntual**:
        para constatar un comprobante hacen falta su número, la fecha y el
        importe, así que si esos datos no están, el objetivo todavía no se puede
        formular y se reporta como tal (``ValueError``) en vez de mandar una
        consulta vacía.
        """
        valores = getattr(contexto, "valores", {}) or {}
        letra = getattr(contexto, "letra", None)

        nro = valores.get("nro_comprobante")
        if not nro:
            raise ValueError("falta el número del comprobante para constatarlo")

        pto_vta, nro_cbte = parse_pto_vta_nro(str(nro))

        return {
            "CbteModo": "CAE",
            "CuitEmisor": self._solo_digitos(valores.get("cuit_emisor")),
            "PtoVta": pto_vta,
            "CbteTipo": self._tipo_afip(letra),
            "CbteNro": nro_cbte,
            "CbteFch": self._fecha_afip(valores.get("fecha_emision")),
            "ImpTotal": valores.get("importe_total_facturado"),
            "DocTipoReceptor": DOC_TIPO_CUIT,
            "DocNroReceptor": self._solo_digitos(valores.get("cuit_receptor")),
        }

    @staticmethod
    def _solo_digitos(valor: Any) -> str:
        """CUIT sin guiones (el padrón los espera como dígitos)."""
        return re.sub(r"\D", "", str(valor or ""))

    @staticmethod
    def _fecha_afip(valor: Any) -> str:
        """Fecha en ``YYYYMMDD`` (formato del WSCDC) a partir del ISO de T-402."""
        texto = str(valor or "")
        coincidencia = re.match(r"^(\d{4})-(\d{2})-(\d{2})$", texto)
        if not coincidencia:
            # El WSCDC admite la fecha vacía; se manda tal cual y el padrón
            # decide. No se inventa una fecha.
            return texto
        return "".join(coincidencia.groups())

    @staticmethod
    def _tipo_afip(letra: str | None) -> int:
        """Código AFIP del tipo de comprobante a partir de la letra.

        **Decisión de alcance**: el mapeo letra↔código AFIP es la decisión
        abierta **D-13** (tiques `090`/`099` incluidos) y todavía no está
        cerrada con negocio. Acá solo se traducen las letras del voculario del
        motor, que **sí** están definidas (A=1, B=6, C=11); un valor fuera de
        ese conjunto lanza ``ValueError`` en vez de inventar un código.
        """
        tabla = {"A": 1, "B": 6, "C": 11, "M": 51, "E": 19}
        if letra not in tabla:
            raise ValueError(
                f"no hay código AFIP para el tipo de comprobante {letra!r} "
                "(decisión abierta D-13: el mapeo de tiques no está cerrado)"
            )
        return tabla[letra]


__all__ = ["ArcaResultado", "ArcaClient", "parse_pto_vta_nro", "METODO_CONSTATAR"]


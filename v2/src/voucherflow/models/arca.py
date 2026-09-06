"""Adaptador ARCA/WSCDC (esqueleto opcional — F5 / T-502, ADR-003).

**Fase**: F5 (T-502). En F0 queda solo el esqueleto con la firma pública.
Según ADR-003 el adaptador es **opcional en el MVP** (hook activado por
configuración) y reutiliza la lógica de ``v1/wip/consultar_arca.py`` cuando se
implemente.

Responsabilidad: consulta puntual de evidencia adicional (padrón/WSCDC) con
**objetivo concreto y límite de reintentos** (no un loop abierto) y timeout.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass
class ArcaResultado:
    """Resultado de una consulta ARCA/WSCDC (evidencia adicional).

    ``ok`` distingue "consultado con respuesta" de "no disponible" (el hook es
    opcional y no debe tumbar el pipeline — ADR-003).
    """

    ok: bool = False
    datos: dict[str, Any] = field(default_factory=dict)
    error: str | None = None


class ArcaClient:
    """Cliente de consulta al padrón ARCA/WSCDC (evidencia adicional).

    Esqueleto F0 — se implementa en F5/T-502 con ``max_reintentos`` y timeout
    configurables. Su uso está condicionado a configuración (ADR-003).
    """

    def __init__(
        self,
        url: str | None = None,
        timeout_s: float = 15.0,
        max_reintentos: int = 2,
    ) -> None:
        self.url = url
        self.timeout_s = timeout_s
        self.max_reintentos = max_reintentos

    def consultar(self, campo: str, objetivo: str) -> ArcaResultado:
        """Consulta evidencia adicional con un objetivo concreto.

        Esqueleto F0 — se implementa en F5/T-502 (reutiliza
        ``v1/wip/consultar_arca.py``).
        """
        raise NotImplementedError("ArcaClient.consultar(): se implementa en F5 (T-502, ADR-003).")


__all__ = ["ArcaResultado", "ArcaClient"]

"""Módulo ``validation`` (esqueleto — F2) — gate "¿es comprobante?" doble-paso qween.

**Fase**: F2 (refactor qween). En F0 solo se deja el **esqueleto** con la
firma pública y los contratos de entrada/salida, sin lógica de negocio ni
acoplamiento a scripts de ``v1/``.

Responsabilidades (doc 03 §4.2 y `VAL.md`): preparar la vista rápida
(thumbnail/calidad baja), decidir de forma barata si el documento es un
comprobante (3 salidas: comprobante / no / indeterminado) y, en caso de
indeterminado, subir a una vista de revisión; nunca reutilizar la vista rápida
para extracción (principio de doble calidad).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum


class VeredictoGate(str, Enum):
    """Salida de la decisión binaria barata "¿es comprobante?" (E-QWE-1)."""

    comprobante = "comprobante"
    no_comprobante = "no_comprobante"
    indeterminado = "indeterminado"


@dataclass
class ValidationResult:
    """Contrato de salida del gate qween (doc 03 §9 ``ValidationResult``).

    ``veredicto``: comprobante / no_comprobante / indeterminado.
    ``vista_usada``: rápida | revisión (nunca se reutiliza la rápida para extraer).
    """

    veredicto: VeredictoGate
    confianza_fuente: str = "media"
    vista_usada: str = "rapida"
    detalle: dict = field(default_factory=dict)


def validar_comprobante(origen: str, quick: bool = True) -> ValidationResult:
    """Gate doble-paso: decide si ``origen`` es un comprobante (F2).

    Esqueleto F0 — se implementa en F2 (módulo ``validation.qween``).
    """
    raise NotImplementedError("validar_comprobante(): se implementa en F2 (T-201..T-204).")


__all__ = ["VeredictoGate", "ValidationResult", "validar_comprobante"]

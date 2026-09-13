"""Módulo ``rules`` (esqueleto — F0/F3/F5) — motor de reglas declarativo.

**Fase**: F0 deja la base declarativa de una ``Rule`` y el registro/``Registry``
(motor). Las reglas de negocio R1-R7 se migran del prompt WIP al código en F3
(ADR-006, T-301) y las reglas cruzadas/gaps en F5 (T-501/T-502). La tabla de
precedencia por campo se define en F4 (ADR-002).

El motor es **100% determinista** (principio de diseño #3) y cada regla reporta
su ``id`` para trazabilidad (E-CONC-5). Las condiciones se evalúan contra un
``contexto`` tipado que las fases pobladoras construirán (evidencia por fuente,
candidatos, etc.).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Callable


@dataclass(frozen=True)
class Rule:
    """Regla declarativa unitaria (id, prioridad, condición, resultado).

    Atributos:
        id: Identificador único de la regla (p. ej. ``R1``, ``PREC_1``). Se
            registra en la trazabilidad del caso (E-CONC-5).
        prioridad: Orden de evaluación (menor número = mayor prioridad).
        condicion: ``callable(contexto) -> bool``. Debe ser **determinista** y
            sin efectos colaterales (solo lectura del contexto).
        resultado: Valor/etiqueta que produce la regla al dispararse (p. ej.
            letra ``"C"``, o el id de la fuente ganadora en precedencia).
        tipo: Clasificación de la regla: ``negocio`` | ``lectura`` | ``raw`` |
            ``cruzada`` | ``precedencia`` (para reportes).
        detalle: Descripción legible (aparece en auditoría/alertas).
    """

    id: str
    prioridad: int
    condicion: Callable[[Any], bool]
    resultado: Any = None
    tipo: str = "negocio"
    detalle: str = ""

    def evaluar(self, contexto: Any) -> bool:
        """Evalúa la condición sobre el contexto (determinista)."""
        return bool(self.condicion(contexto))


@dataclass
class Registry:
    """Registro de reglas ordenado por prioridad (motor de reglas).

    Mantiene las reglas declarativas de un dominio (p. ej. tipo de comprobante)
    y las evalúa en orden. Cada regla disparada se registra para auditoría.
    """

    reglas: list[Rule] = field(default_factory=list)

    def registrar(self, regla: Rule) -> None:
        """Registra una regla manteniendo el orden por prioridad."""
        self.reglas.append(regla)
        self.reglas.sort(key=lambda r: r.prioridad)

    def evaluar_todas(self, contexto: Any) -> list[Rule]:
        """Evalúa todas las reglas y devuelve las que se disparan (en orden)."""
        return [r for r in self.reglas if r.evaluar(contexto)]

    def ids_disparados(self, contexto: Any) -> list[str]:
        """Ids de las reglas disparadas (para trazabilidad E-CONC-5)."""
        return [r.id for r in self.evaluar_todas(contexto)]


__all__ = ["Rule", "Registry"]

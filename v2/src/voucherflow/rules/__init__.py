"""Módulo ``rules`` — motor de reglas declarativo (F0) + reglas R1-R7 (F3).

**Fase**: F0 deja el motor declarativo (``Rule`` + ``Registry``) como base del
ADR-006 (reglas en código). **F3/T-301** migra las reglas R1-R7 del prompt WIP
(``prompts/wip/deteccion_tipo_factura.yaml``) a un motor de reglas en código,
organizadas en tres registros:

- :data:`REGISTRO_NEGOCIO` (R1/R2A/R2B/R3) → ``tipo_esperado_por_negocio``.
- :data:`REGISTRO_LECTURA` (R4/R5/R6) → ``tipo_detectado_por_documento``.
- :data:`REGISTRO_CONFLICTO` (R7) → alertas de auditoría.

``Rule`` y ``Registry`` se siguen exportando sin cambios (contrato congelado de
F0). Las reglas cruzadas de F5 y la tabla de precedencia por campo de F4 se
agregan en sus propias fases.
"""

from __future__ import annotations

from .contexto import ContextoTipoComprobante, normalizar_letra
from .registry import Registry, Rule
from .tipo_comprobante_rules import (
    MENSAJE_R7,
    REGEX_LETRA_ENCABEZADO,
    REGISTRO_CONFLICTO,
    REGISTRO_LECTURA,
    REGISTRO_NEGOCIO,
    TABLA_INFERENCIA_R6,
    construir_alerta,
    construir_registros,
    evaluar_conflicto,
    evaluar_lectura,
    evaluar_negocio,
    letra_de_campos_totales,
    letra_de_encabezado,
    letra_de_recuadro,
    regla_lectura_resolutoria,
    regla_negocio_resolutoria,
    reglas_lectura_disparadas,
    reglas_negocio_disparadas,
)

__all__ = [
    # Motor declarativo (F0, contrato congelado).
    "Rule",
    "Registry",
    # Contexto tipado (F3/T-301).
    "ContextoTipoComprobante",
    "normalizar_letra",
    # Reglas R1-R7 y registros (F3/T-301).
    "REGISTRO_NEGOCIO",
    "REGISTRO_LECTURA",
    "REGISTRO_CONFLICTO",
    "REGEX_LETRA_ENCABEZADO",
    "MENSAJE_R7",
    "TABLA_INFERENCIA_R6",
    "construir_registros",
    "evaluar_negocio",
    "evaluar_lectura",
    "evaluar_conflicto",
    "regla_negocio_resolutoria",
    "regla_lectura_resolutoria",
    "reglas_negocio_disparadas",
    "reglas_lectura_disparadas",
    "construir_alerta",
    "letra_de_recuadro",
    "letra_de_encabezado",
    "letra_de_campos_totales",
]


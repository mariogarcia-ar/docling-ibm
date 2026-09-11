"""Módulo ``trace`` — trazabilidad ``CaseRecord`` y persistencia (F5/T-506).

**Fase**: F5 · **Tarea**: T-506 · **Épica**: E-CONC-5 · **ADR-005 / ADR-009**.

F0 dejó acá el esqueleto del registrador y el contrato ``CaseRecord`` congelado
en ``schemas/result.py``. **T-506 lo implementa** en dos piezas con
responsabilidades distintas:

- :mod:`~voucherflow.trace.construccion` **arma** el registro auditable de un
  caso a partir de lo que las etapas produjeron (evidencia + decisión +
  resultado). Es una proyección: no corre reglas ni modelos.
- :mod:`~voucherflow.trace.recorder` **persiste** el registro: sidecar JSON por
  documento (escritura atómica) + índice JSONL de una línea por caso.

Uso típico::

    from voucherflow.trace import CaseRecorder, construir_case_record

    caso = construir_case_record(evidencia, resultado=resultado, archivo=ruta)
    CaseRecorder("salida/cases").registrar(caso)     # sidecar + índice
"""

from __future__ import annotations

from .construccion import (
    BLOQUES_CONCLUSION,
    ETAPA_CONCLUSION,
    NOTA_EVIDENCIA_DIRECTA,
    NOTA_EVIDENCIA_RECONSTRUIDA,
    construir_case_record,
    detalles_de_conclusion,
    evidencia_por_fuente,
    modelos_y_prompts,
    reglas_disparadas,
    resumen_case_record,
)
from .recorder import (
    CAMPOS_INDICE,
    NOMBRE_INDICE,
    VERSION_TRAZA,
    CaseRecorder,
    ResultadoPersistencia,
    sidecar_para,
)

__all__ = [
    # Persistencia (sidecar + índice)
    "CaseRecorder",
    "ResultadoPersistencia",
    "sidecar_para",
    "VERSION_TRAZA",
    "NOMBRE_INDICE",
    "CAMPOS_INDICE",
    # Construcción del registro auditable
    "construir_case_record",
    "resumen_case_record",
    "evidencia_por_fuente",
    "modelos_y_prompts",
    "reglas_disparadas",
    "detalles_de_conclusion",
    "ETAPA_CONCLUSION",
    "BLOQUES_CONCLUSION",
    "NOTA_EVIDENCIA_DIRECTA",
    "NOTA_EVIDENCIA_RECONSTRUIDA",
]

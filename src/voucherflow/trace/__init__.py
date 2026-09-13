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
- :mod:`~voucherflow.trace.metricas` **agrega** el histórico (T-507 / E-LIB-5):
  % certeza alta, % agente, % rechazado, acuerdo VLM/LLM, cobertura HITL y tasa de
  alertas, calculados sobre los `CaseRecord` persistidos.
- :mod:`~voucherflow.trace.agregado` **consolida la corrida** en un único JSON
  (T-603 / E-CLI-2): un índice por documento (veredicto + puntero al sidecar) con
  la síntesis del lote y las métricas. No copia los `CaseRecord`: apunta a ellos.

Uso típico::

    from voucherflow.trace import CaseRecorder, construir_case_record, metricas_del_recorder

    caso = construir_case_record(evidencia, resultado=resultado, archivo=ruta)
    recorder = CaseRecorder("salida/cases")
    recorder.registrar(caso)                # sidecar + índice
    metricas_del_recorder(recorder)         # el reporte de la corrida
"""

from __future__ import annotations

from .agregado import (
    NO_AGREGADOS,
    VERSION_AGREGADO,
    Agregado,
    EntradaDocumento,
    agregado_del_recorder,
    agregar_a_archivo,
    construir_agregado,
    entrada_de_caso,
    entrada_de_resultado,
    escribir_agregado,
    leer_agregado,
)
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
from .metricas import (
    ESTADO_RECHAZADO,
    MINIMO_LOTE_CONFIABLE,
    VERSION_METRICAS,
    acuerdo_vlm_llm,
    casos_del_historico,
    cobertura_hitl,
    metricas_certidumbre,
    metricas_de,
    metricas_del_recorder,
    metricas_rechazo,
    resumen_legible,
    tasa_alertas,
)

__all__ = [
    # Persistencia (sidecar + índice)
    "CaseRecorder",
    "ResultadoPersistencia",
    "sidecar_para",
    "VERSION_TRAZA",
    "NOMBRE_INDICE",
    "CAMPOS_INDICE",
    # Métricas (T-507 / E-LIB-5)
    "metricas_de",
    "metricas_del_recorder",
    "metricas_certidumbre",
    "metricas_rechazo",
    "acuerdo_vlm_llm",
    "cobertura_hitl",
    "tasa_alertas",
    "casos_del_historico",
    "resumen_legible",
    "VERSION_METRICAS",
    "MINIMO_LOTE_CONFIABLE",
    "ESTADO_RECHAZADO",
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
    # Salida agregada del lote (T-603 / E-CLI-2)
    "VERSION_AGREGADO",
    "NO_AGREGADOS",
    "EntradaDocumento",
    "Agregado",
    "entrada_de_resultado",
    "entrada_de_caso",
    "construir_agregado",
    "agregado_del_recorder",
    "escribir_agregado",
    "leer_agregado",
    "agregar_a_archivo",
]

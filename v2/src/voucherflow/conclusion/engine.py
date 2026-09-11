"""Módulo ``conclusion.engine`` — motor de conclusión (F5).

**Fase**: F5 (refactor conclusión). En F0 quedó el **esqueleto** con las firmas
públicas; **T-501** implementa :func:`concluir`, la etapa de la **pasada 2**:
corre las reglas cruzadas sobre la evidencia combinada de F4 y produce el
``Decision`` del caso (contrato congelado de F0).

Responsabilidades (doc 03 §4.5, `CONC.md`): reglas cruzadas sobre la evidencia
combinada (negocio + fast-fail + conflicto R7), detección de gaps con límite
(hook ARCA, ADR-003, T-502), consolidación con certeza **por etapa** (programa →
alta; agente IA → baja + HITL, T-503/T-504), cola HITL con muestreo de auditoría
(ADR-004, T-505) y persistencia de ``CaseRecord`` (ADR-005, T-506). El agente
solo elige entre candidatos no descartados (ADR-008).

Alcance de T-501
----------------
:func:`concluir` implementa la decisión (pasada 2) y **devuelve la evidencia
combinada con su ``Decision`` ya adjunta**. Lo que **no** hace todavía, por ser
de otras tareas de F5: buscar evidencia para los gaps que detecta (T-502),
consolidar el ``VoucherResult`` completo con la clasificación contable (T-503),
llamar al agente (T-504) ni encolar HITL (T-505). Las firmas de
:func:`escalar_a_agente` y :func:`encolar_hitl` siguen siendo el esqueleto de F0:
se implementan en T-504/T-505.
"""

from __future__ import annotations

from ..rules.contexto import ContextoTipoComprobante
from ..rules.contexto_conclusion import ContextoConclusion
from ..rules.cruzadas import (
    VERSION_CRUZADAS,
    ConclusionResult,
    construir_conclusion,
    evaluar_cruzadas,
    resumen_cruzadas,
)
from ..schemas.evidence import CombinedEvidence
from ..schemas.result import VoucherResult


def _correr_pasada_2(
    evidencia: CombinedEvidence,
    contexto_tipo: ContextoTipoComprobante | None,
) -> tuple[ContextoConclusion, ConclusionResult]:
    """Proyecta la evidencia y corre la pasada 2 (compartido por la API pública)."""
    if not isinstance(evidencia, CombinedEvidence):
        raise TypeError(
            "concluir()/concluir_caso() esperan una CombinedEvidence (la salida "
            f"de la combinación de F4/T-404); recibido: {type(evidencia).__name__}."
        )
    contexto = ContextoConclusion.desde_evidencia(
        evidencia, contexto_tipo=contexto_tipo
    )
    veredicto = evaluar_cruzadas(contexto)
    return contexto, construir_conclusion(contexto, veredicto)


def concluir(
    evidencia: CombinedEvidence,
    contexto_tipo: ContextoTipoComprobante | None = None,
) -> CombinedEvidence:
    """Concluye el caso: corre la pasada 2 y adjunta el ``Decision`` (F5/T-501).

    Es la etapa que **sí** decide, y por eso es la que llena el ``Decision`` que
    F4 dejó en ``None`` a propósito (combinar no es decidir). La decisión se
    apoya en las reglas cruzadas (:mod:`voucherflow.rules.cruzadas`):

    - **negocio** sobre los valores vigentes (R1-R7 re-aplicadas, no
      reimplementadas);
    - **fast-fail** (la letra contradice los campos del comprobante; o no hay
      letra ni candidatos);
    - **conflicto R7** (letra sin derecho a crédito fiscal → revisión, no
      rechazo).

    La **certeza se deriva de la etapa** (glosario §2): como acá decide el
    código, el origen es ``programa`` y la certeza es ``alta`` **solo** cuando el
    código concluyó (aprobado o rechazado). Un caso ambiguo queda en ``baja``
    sin concluir, para que T-504 (agente) y T-505 (HITL) lo tomen.

    Es una función **pura y determinista, sin red**: no llama a ningún modelo.
    Eso la hace apta para la suite default (regla dura del repo) y para el gate
    de F6.

    Argumentos:
        evidencia: la ``CombinedEvidence`` de F4/T-404 (con la resolución por
            campo). No se muta: se devuelve una copia con la decisión adjunta.
        contexto_tipo: el contexto fiscal del caso (condiciones fiscales de
            emisor/receptor y país) cuando se conoce — lo aporta la clasificación
            de F3 o el padrón. Es **opcional**: sin él, la pasada 2 concluye por
            la coherencia del documento; con él, además contrasta la letra
            esperada por el negocio contra la vigente (y detecta el conflicto
            R7). Las condiciones fiscales **no** son campos del contrato de
            extracción, así que no pueden viajar dentro de la evidencia.

    Devuelve:
        Una ``CombinedEvidence`` nueva, con el mismo ``documento_id``, los mismos
        ``campos`` y una ``trazabilidad`` que conserva la de F4 y agrega el
        bloque de la conclusión (reglas disparadas por familia, estado, motivo,
        alertas, gaps y la versión de las reglas).

    Lanza:
        ``TypeError`` si ``evidencia`` no es una ``CombinedEvidence``.
    """
    contexto, conclusion = _correr_pasada_2(evidencia, contexto_tipo)

    trazabilidad = dict(evidencia.trazabilidad)
    trazabilidad["etapa"] = VERSION_CRUZADAS
    trazabilidad["conclusion"] = {
        **conclusion.como_dict(),
        "letra_vigente": contexto.letra,
        "fuente_letra": (
            contexto.fuente_letra.value if contexto.fuente_letra else None
        ),
        "campos_criticos_ausentes": list(contexto.campos_criticos_ausentes),
        "campos_ausentes": list(contexto.campos_ausentes),
        "lectura_invalida": contexto.lectura_invalida,
        "coherente": contexto.coherente,
        "incoherencias": list(contexto.incoherencias),
        "nota": (
            "Conclusión T-501 (pasada 2, E-CONC-1): reglas cruzadas "
            "(negocio + fast-fail + conflicto R7) sobre la evidencia combinada. "
            "La certeza se deriva de la etapa que decidió (glosario §2). El "
            "Decision de F0 se adjunta solo si el código concluyó: un caso "
            "ambiguo no lo decidió nadie (viaja sin Decision, con su "
            "ConclusionResult en la traza) y espera al agente (T-504) o al "
            "HITL (T-505). La consolidación del VoucherResult es T-503."
        ),
    }

    return CombinedEvidence(
        documento_id=evidencia.documento_id,
        campos=dict(evidencia.campos),
        # ``decision`` es el veredicto **final** del caso (contrato de F0): se
        # adjunta solo cuando el código concluyó. El estado completo —incluido
        # el caso ambiguo— viaja en ``trazabilidad['conclusion']`` y en el
        # ``ConclusionResult`` que devuelve el módulo ``conclusion``.
        decision=conclusion.decision,
        trazabilidad=trazabilidad,
    )


def concluir_caso(
    evidencia: CombinedEvidence,
    contexto_tipo: ContextoTipoComprobante | None = None,
) -> ConclusionResult:
    """Concluye el caso y devuelve el :class:`ConclusionResult` completo (F5/T-501).

    Es la interfaz del diseño (doc 03 §4.5 / `CONC.md` §1) para quien necesita
    **el veredicto entero**, no solo el ``Decision`` de F0: incluye el estado
    (``aprobado``/``rechazado``/``revision``), los gaps, los conflictos, las
    alertas, los candidatos y la expectativa de HITL. Es la vía natural para el
    orquestador y el reporte de la corrida.

    :func:`concluir` es la variante que devuelve la evidencia combinada con la
    decisión adjunta (la que encadena el pipeline); esta devuelve el veredicto
    solo. Ambas comparten exactamente la misma evaluación y aceptan el
    ``contexto_tipo`` opcional (ver :func:`concluir`).
    """
    _, conclusion = _correr_pasada_2(evidencia, contexto_tipo)
    return conclusion


def escalar_a_agente(evidencia: CombinedEvidence, candidatos: list[str]) -> VoucherResult:
    """Escala a agente IA solo con los candidatos restantes (F5, ADR-008).

    Esqueleto F0 — se implementa en F5 (T-504).
    """
    raise NotImplementedError("escalar_a_agente(): se implementa en F5 (T-504).")


def encolar_hitl(resultado: VoucherResult) -> VoucherResult:
    """Encola a revisión humana los casos de certeza baja + muestreo (F5).

    Esqueleto F0 — se implementa en F5 (T-505).
    """
    raise NotImplementedError("encolar_hitl(): se implementa en F5 (T-505).")


__all__ = ["concluir", "concluir_caso", "escalar_a_agente", "encolar_hitl"]


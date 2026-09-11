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

from dataclasses import dataclass, field
from typing import Any

from ..rules.contexto import ContextoTipoComprobante
from ..rules.contexto_conclusion import ContextoConclusion
from ..rules.cruzadas import (
    VERSION_CRUZADAS,
    ConclusionResult,
    construir_conclusion,
    evaluar_cruzadas,
)
from ..rules.gaps import (
    VERSION_GAPS,
    BuscadorEvidencia,
    DeteccionGaps,
    PresupuestoBusqueda,
    ResultadoBusquedaAdicional,
    buscar_evidencia_adicional,
    detectar_gaps,
)
from ..schemas.evidence import CombinedEvidence, SourceEvidence
from ..schemas.result import ClasificacionContable, HitlDecision, VoucherResult
from .consolidacion import Consolidacion, consolidar


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
    return _adjuntar_conclusion(evidencia, contexto, conclusion)


def _adjuntar_conclusion(
    evidencia: CombinedEvidence,
    contexto: ContextoConclusion,
    conclusion: ConclusionResult,
) -> CombinedEvidence:
    """Devuelve la evidencia con el veredicto adjunto y su traza (T-501).

    Es el único lugar donde se arma el ``Decision`` de F0 y se anota la
    conclusión en la trazabilidad, para que :func:`concluir` y
    :func:`concluir_con_busqueda` (T-502) compartan exactamente el mismo camino
    — la decisión no puede depender de si hubo búsqueda o no.
    """
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


# ---------------------------------------------------------------------------
# Búsqueda de evidencia adicional (T-502, E-CONC-2 / ADR-003)
# ---------------------------------------------------------------------------


@dataclass
class ConclusionConBusqueda:
    """Resultado de concluir un caso **con** búsqueda de evidencia adicional (T-502).

    Campos:
        evidencia: la ``CombinedEvidence`` final (ya con los campos que aportó
            la búsqueda, si hubo).
        conclusion: el :class:`~voucherflow.rules.cruzadas.ConclusionResult` del
            veredicto **final** (tras re-aplicar las cruzadas).
        deteccion: los gaps que se detectaron en la primera pasada.
        busqueda: lo que se recuperó y qué pasó con cada intento.
        gaps_restantes: los gaps que **quedaron** tras la búsqueda. Es la señal
            para el paso siguiente del flujo: si hay gaps restantes bloqueantes,
            el caso escala al agente (T-504).
        consolidacion: el :class:`~voucherflow.conclusion.consolidacion.Consolidacion`
            (el ``VoucherResult`` final con la traza de la certeza, T-503) cuando
            se pidió ``consolidar=True``.
    """

    evidencia: CombinedEvidence
    conclusion: ConclusionResult
    deteccion: DeteccionGaps
    busqueda: ResultadoBusquedaAdicional
    gaps_restantes: list[str] = field(default_factory=list)
    consolidacion: "Consolidacion | None" = None

    @property
    def resultado(self) -> VoucherResult | None:
        """El ``VoucherResult`` final, si se pidió consolidar (T-503)."""
        return self.consolidacion.valor if self.consolidacion else None

    @property
    def hubo_busqueda(self) -> bool:
        return self.busqueda.hubo_busqueda

    def como_dict(self) -> dict[str, Any]:
        resumen: dict[str, Any] = {
            "version": VERSION_GAPS,
            "deteccion": self.deteccion.como_dict(),
            "busqueda": self.busqueda.como_dict(),
            "gaps_restantes": list(self.gaps_restantes),
            "conclusion": self.conclusion.como_dict(),
        }
        if self.consolidacion is not None:
            resumen["consolidacion"] = self.consolidacion.como_dict()
        return resumen


def concluir_con_busqueda(
    evidencia: CombinedEvidence,
    *,
    buscador: BuscadorEvidencia | None = None,
    presupuesto: PresupuestoBusqueda | None = None,
    contexto_tipo: ContextoTipoComprobante | None = None,
    consolidar_resultado: bool = False,
    clasificacion: ClasificacionContable | None = None,
) -> ConclusionConBusqueda:
    """Concluye y, si faltan datos, busca evidencia adicional **acotada** (T-502).

    Implementa el paso del pseudocódigo de `algoritmo.md`:

        si resultado.faltan_datos:
            evidencia += buscar_evidencia_adicional(gaps, max_reintentos=N)
            resultado = aplicar_reglas_cruzadas(evidencia)

    Secuencia:

    1. Corre la pasada 2 de T-501 sobre la evidencia original.
    2. **Detecta los gaps** (determinístico, sin red).
    3. Si hay gaps buscables y hay buscador, consulta con el presupuesto como
       tope; si no hay buscador (hook desactivado, ADR-003), lo registra como
       *no disponible* y **sigue** — el MVP no depende de ARCA.
    4. **Fusiona** los campos recuperados en la evidencia (con su fuente y su
       sostén) y **re-aplica las cruzadas** sobre el caso enriquecido.

    La re-conclusión es lo que cierra el círculo: un dato del padrón puede
    desbloquear el veredicto (dejar de faltar un campo crítico) o, al contrario,
    contradecir lo que el documento decía (y entonces el fast-fail de T-501 lo
    rechaza).

    **No hay loop abierto** (E-CONC-2): la búsqueda se corre **una vez**, con el
    presupuesto como tope. Si los gaps siguen ahí, se reportan en
    ``gaps_restantes`` y el caso sigue al paso siguiente del flujo — no se vuelve
    a intentar.

    Es determinística y **sin red** cuando el buscador es ``None`` o un doble:
    apta para la suite default.

    Argumentos:
        evidencia: la ``CombinedEvidence`` de F4/T-404.
        buscador: el hook de evidencia adicional (``ArcaClient`` en producción)
            o ``None`` si está desactivado.
        presupuesto: los topes de la búsqueda (ADR-003). Default: 3 consultas y
            2 reintentos por gap.
        contexto_tipo: el contexto fiscal opcional (ver :func:`concluir`).
        consolidar_resultado: si es ``True``, además **consolida** el
            ``VoucherResult`` final (T-503) y lo deja en
            :attr:`ConclusionConBusqueda.resultado`. Es opt-in para que el paso
            de búsqueda se pueda ejercitar solo.
        clasificacion: la clasificación contable ya resuelta (F3/T-304) para
            publicar en el resultado consolidado. Si no se pasa, el resultado
            viaja sin ella (no se inventa: ver ``consolidacion.py``).

    Devuelve:
        :class:`ConclusionConBusqueda` con la evidencia final, el veredicto, la
        detección, la traza de la búsqueda, los gaps que quedaron y —si se pidió—
        la consolidación (T-503).

    Lanza:
        ``TypeError`` si ``evidencia`` no es una ``CombinedEvidence``.
    """
    if not isinstance(evidencia, CombinedEvidence):
        raise TypeError(
            "concluir_con_busqueda() espera una CombinedEvidence (la salida de la "
            f"combinación de F4/T-404); recibido: {type(evidencia).__name__}."
        )

    # 1. La pasada 2 sobre el caso tal como llegó.
    contexto_inicial, _ = _correr_pasada_2(evidencia, contexto_tipo)

    # 2. Qué falta (determinístico).
    deteccion = detectar_gaps(contexto_inicial)

    # 3. Búsqueda acotada (no-op si no hay gaps buscables o el hook está apagado).
    busqueda = buscar_evidencia_adicional(
        deteccion, contexto_inicial, buscador=buscador, presupuesto=presupuesto
    )

    # 4. Fusionar lo recuperado y re-concluir sobre el caso enriquecido.
    if busqueda.campos:
        evidencia_final = _fusionar_evidencia(evidencia, busqueda)
    else:
        evidencia_final = evidencia

    contexto_final, conclusion = _correr_pasada_2(evidencia_final, contexto_tipo)
    evidencia_final = _adjuntar_conclusion(evidencia_final, contexto_final, conclusion)
    evidencia_final = _anotar_busqueda(evidencia_final, deteccion, busqueda)

    gaps_restantes = sorted(
        campo
        for campo in deteccion.campos_faltantes
        if campo not in busqueda.campos and campo in contexto_final.campos_ausentes
    )

    consolidacion = None
    if consolidar_resultado:
        # T-503: el veredicto se vuelve el ``VoucherResult`` final (certeza
        # derivada del veredicto; alta + programa ⇔ el código concluyó sin
        # ambigüedad). Se le pasa la evidencia **ya concluida** para que la traza
        # conserve el bloque de T-501 además del de la consolidación.
        consolidacion = consolidar(
            evidencia_final,
            conclusion,
            contexto_tipo=contexto_tipo,
            contexto=contexto_final,
            clasificacion=clasificacion,
            evidencia_con_traza=evidencia_final,
        )

    return ConclusionConBusqueda(
        evidencia=evidencia_final,
        conclusion=conclusion,
        deteccion=deteccion,
        busqueda=busqueda,
        gaps_restantes=gaps_restantes,
        consolidacion=consolidacion,
    )


def consolidar_caso(
    evidencia: CombinedEvidence,
    *,
    contexto_tipo: ContextoTipoComprobante | None = None,
    clasificacion: ClasificacionContable | None = None,
    hitl: HitlDecision | None = None,
) -> Consolidacion:
    """Concluye y **consolida** el caso en el ``VoucherResult`` final (F5/T-503).

    Es el atajo cuando no hace falta la búsqueda de evidencia adicional: corre la
    pasada 2 (T-501) y devuelve el
    :class:`~voucherflow.conclusion.consolidacion.Consolidacion` — el contrato
    congelado de F0 (glosario §2.4) más la traza de **por qué** la certeza quedó
    como quedó.

    Regla que implementa (Gherkin E-CONC-1, "concluye por programa"): la certeza
    es ``alta`` con origen ``programa`` **si y solo si** el código concluyó de
    forma consistente (``concluye``, sin alertas pendientes y con letra). Un caso
    ambiguo sale en ``revision``, ``certeza=baja`` y **sin** ``origen``: no lo
    decidió nadie todavía (lo tomarán T-504/T-505).

    Determinística y sin red.

    Argumentos:
        evidencia: la ``CombinedEvidence`` de F4/T-404.
        contexto_tipo: el contexto fiscal opcional (ver :func:`concluir`).
        clasificacion: la clasificación contable ya resuelta (F3/T-304).
        hitl: la ``HitlDecision`` a publicar (default: la del veredicto).
    """
    contexto, conclusion = _correr_pasada_2(evidencia, contexto_tipo)
    # Se concluye la evidencia internamente para que la traza del resultado
    # conserve el bloque de T-501, y se consolida sobre esa misma evidencia.
    evidencia_concluida = _adjuntar_conclusion(evidencia, contexto, conclusion)
    return consolidar(
        evidencia_concluida,
        conclusion,
        contexto_tipo=contexto_tipo,
        contexto=contexto,
        clasificacion=clasificacion,
        hitl=hitl,
    )


def _fusionar_evidencia(
    evidencia: CombinedEvidence, busqueda: ResultadoBusquedaAdicional
) -> CombinedEvidence:
    """Fusiona los campos recuperados en la evidencia combinada y la re-resuelve.

    Los campos **no se inyectan a mano**: se reconstruyen las ``SourceEvidence``
    a partir de las lecturas que la combinación de F4 conservó (cada
    ``CampoCombinado`` guarda su lado ``vlm``/``llm``/``programa``/``arca``/
    ``hitl``), se les agrega la fuente nueva (el padrón) y se vuelve a llamar a
    la **combinación** de F4.

    Esa es la forma de que el dato del padrón entre con la precedencia correcta
    (ADR-002: una fuente que **no es lectura** va por delante de las lecturas) y
    de que la resolución quede trazada como cualquier otra — en lugar de pisar un
    valor por código, que sería exactamente lo que la tabla de precedencia existe
    para evitar.
    """
    from ..rules.precedencia import combinar, resumen_combinacion
    from ..schemas.evidence import Fuente

    # El padrón puede aportar varios campos en una sola consulta: se agrupan por
    # fuente para reconstruir las SourceEvidence.
    por_fuente: dict[Fuente, dict[str, Any]] = {}
    for campo, field in busqueda.campos.items():
        por_fuente.setdefault(field.fuente, {})[campo] = field

    # 1. Reconstruir las fuentes que ya participaban, campo por campo.
    fuentes: dict[Fuente, dict[str, Any]] = {}
    for campo, combinado in evidencia.campos.items():
        for fuente in (Fuente.vlm, Fuente.llm, Fuente.programa, Fuente.arca, Fuente.hitl):
            lectura = getattr(combinado, fuente.value, None)
            if lectura is not None:
                fuentes.setdefault(fuente, {})[campo] = lectura

    # 2. Sumar (o completar) la fuente de la evidencia adicional.
    for fuente, campos in por_fuente.items():
        fuentes.setdefault(fuente, {}).update(campos)

    sources = [
        SourceEvidence(fuente=fuente, campos=campos, valida=True)
        for fuente, campos in fuentes.items()
        if campos
    ]

    combinacion = combinar(sources, documento_id=evidencia.documento_id)

    trazabilidad = dict(evidencia.trazabilidad)
    trazabilidad["combinacion_adicional"] = resumen_combinacion(combinacion)
    return CombinedEvidence(
        documento_id=evidencia.documento_id,
        campos=combinacion.campos,
        # ``decision`` vuelve a ``None``: se va a re-concluir sobre el caso
        # enriquecido, así que el veredicto anterior ya no rige.
        decision=None,
        trazabilidad=trazabilidad,
    )


def _anotar_busqueda(
    evidencia: CombinedEvidence,
    deteccion: DeteccionGaps,
    busqueda: ResultadoBusquedaAdicional,
) -> CombinedEvidence:
    """Agrega a la traza lo que pasó con la detección y la búsqueda (E-CONC-5)."""
    trazabilidad = dict(evidencia.trazabilidad)
    trazabilidad["gaps"] = deteccion.como_dict()
    trazabilidad["busqueda_evidencia_adicional"] = busqueda.como_dict()
    return CombinedEvidence(
        documento_id=evidencia.documento_id,
        campos=dict(evidencia.campos),
        decision=evidencia.decision,
        trazabilidad=trazabilidad,
    )


__all__ = [
    "concluir",
    "concluir_caso",
    "concluir_con_busqueda",
    "consolidar_caso",
    "escalar_a_agente",
    "encolar_hitl",
    "ConclusionConBusqueda",
]


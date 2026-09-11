"""Reglas cruzadas de conclusión — pasada 2 (F5 / T-501, E-CONC-1).

**Fase**: F5 — Conclusión. La pasada 1 (:mod:`voucherflow.rules.raw`, T-303/
T-403) califica **cada fuente por separado**; la pasada 2 corre sobre la
evidencia **combinada** (el valor vigente de cada campo, resuelto por la tabla
de precedencia de ADR-002) y produce el ``Decision`` del caso.

Qué se decide acá (y qué no)
----------------------------
Se decide **el veredicto del caso**: si el código concluye, con qué estado,
certeza y origen. No se decide la consolidación en ``VoucherResult`` (T-503),
no se buscan datos que falten (T-502), no se llama al agente (T-504) ni se
encola HITL (T-505).

Las tres familias de la pasada 2 (doc 03 §4.5, ``algoritmo.md`` paso 5)
---------------------------------------------------------------------
``negocio``
    Re-aplica R1-R7 de F3 **sobre los valores vigentes** (no sobre una fuente):
    la letra esperada por negocio contra la letra resuelta. Reutiliza
    ``clasificar_tipo_comprobante()`` sin reimplementar el motor.
``fast_fail``
    Los cortes que cierran el caso: la letra **contradice** los campos del
    comprobante (``CRUZ_3``) o el caso no tiene sostén suficiente para afirmar
    nada (``CRUZ_2``/``CRUZ_4``). Un fast-fail es una conclusión, no una duda:
    el código sí resolvió, y resolvió que no.
``conflicto``
    ``CRUZ_5`` — el conflicto financiero de R7 (letra sin derecho a crédito
    fiscal): **sospecha**, no contradicción. No rechaza; degrada a revisión.
    Es el caso del enunciado "RI emisor + RI receptor + documento B → alerta".

Decisiones de diseño (T-501)
----------------------------
1. **Las reglas son ``Rule`` del motor de F0** (``tipo="cruzada"``), como en F3
   y F4: ``Registry`` no se reescribe (contrato congelado de F0).
2. **La certeza se deriva de la etapa** (glosario §2). El veredicto dice
   ``concluye``; la certeza la fija la etapa que decidió — y como acá decide el
   código, es ``programa``. Ninguna regla "elige" su certeza.
3. **Distinguir "está mal" de "no sé"** (decisión de alcance §2.7 del subplan):
   la contradicción es ``rechazado`` + certeza ``alta`` (el código concluyó);
   la ambigüedad es ``revision`` + certeza ``baja`` **sin** ``concluye``.
   Mezclarlas mandaría a revisión casos ya resueltos o haría pasar por aprobado
   un caso sin resolver.
4. **El fast-fail gana a la ambigüedad**: una contradicción dura cierra el caso
   aunque falten otros datos. Si el nro. de comprobante falta *y* la letra se
   contradice, lo que importa es la contradicción.
5. **Lo ausente no se castiga como si fuera un valor** (ADR-001): un IVA que
   nadie declaró no es "IVA cero" ni "IVA discriminado"; no dispara la
   incompatibilidad de la letra B.

Referencias: doc 03 §4.5, `CONC.md` §1/§3, Gherkin E-CONC-1, `algoritmo.md`
paso 5, ADR-001/ADR-002/ADR-006/ADR-008, F5-subplan §2 y §3.1.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from ..schemas.evidence import Decision, EstadoResultado
from .contexto import ContextoTipoComprobante
from .contexto_conclusion import (
    CRUZ_1_NEGOCIO,
    CRUZ_2_LETRA_SIN_SOSTEN,
    CRUZ_3_COHERENCIA_LETRA,
    CRUZ_4_DATOS_FALTANTES,
    CRUZ_5_CONFLICTO_CREDITO,
    FAMILIA_CONFLICTO,
    FAMILIA_FAST_FAIL,
    FAMILIA_NEGOCIO,
    ContextoConclusion,
)
from .registry import Registry, Rule

#: Versión de las reglas cruzadas (se registra en la trazabilidad del caso).
VERSION_CRUZADAS = "conclusion-cruzadas@1"

#: Prioridades de la pasada 2. El fast-fail va primero (contradicción dura),
#: después la falta de sostén y los gaps, y al final el conflicto de crédito
#: fiscal —que sospecha, no rechaza— y el negocio.
PRIORIDAD_COHERENCIA = 10
PRIORIDAD_SIN_LETRA = 20
PRIORIDAD_GAP = 30
PRIORIDAD_CONFLICTO = 40
PRIORIDAD_NEGOCIO = 50

#: Estado resultante de cada desenlace. Es parte del contrato de F0
#: (``EstadoResultado``): un caso siempre sale con un estado explícito.
ESTADO_APROBADO = EstadoResultado.aprobado.value
ESTADO_RECHAZADO = EstadoResultado.rechazado.value
ESTADO_REVISION = EstadoResultado.revision.value

#: Motivos canónicos (viajan en las alertas/el detalle; en español y citando el
#: origen de la regla).
MOTIVO_SIN_LETRA = (
    "Ninguna fuente resolvió la letra del comprobante: sin letra no se puede "
    "afirmar nada del caso (CRUZ_2)."
)
MOTIVO_GAP = (
    "Faltan campos críticos para concluir con certeza alta ({campos}); el caso "
    "necesita evidencia adicional o revisión (CRUZ_4)."
)
MOTIVO_CONFLICTO_CREDITO = (
    "Conflicto financiero (R7): el emisor y el receptor son Responsable "
    "Inscripto y el comprobante no habilita el cómputo del crédito fiscal. El "
    "sistema no afirma que esté mal: requiere revisión humana (CRUZ_5)."
)
MOTIVO_NEGOCIO_CONCLUYE = (
    "El comprobante se sostiene solo (letra del vocabulario, campos críticos "
    "presentes y sin contradicciones internas) y el negocio —si aportó "
    "condiciones fiscales— no espera otra letra (CRUZ_1)."
)


# ---------------------------------------------------------------------------
# Condiciones (funciones puras del contexto)
# ---------------------------------------------------------------------------


def condicion_coherencia_letra(ctx: ContextoConclusion) -> bool:
    """CRUZ_3 (fast-fail): la letra vigente contradice los campos del caso.

    Dispara cuando la evaluación de ``COHERENCIA_POR_CAMPO`` (T-403) sobre el
    **valor vigente** encontró incoherencias. Es una contradicción del
    documento consigo mismo: el caso se cierra como rechazado.
    """
    return not ctx.coherente


def condicion_sin_letra(ctx: ContextoConclusion) -> bool:
    """CRUZ_2: no hay letra vigente ni candidatos: nada que concluir.

    Un caso sin letra y sin candidatos posibles no se puede afirmar (no se
    inventa la letra): queda para revisión. Si hay candidatos restantes, la
    ambigüedad es resoluble (agente/T-504), así que **no** dispara.
    """
    return ctx.letra is None and not ctx.candidatos_restantes and not ctx.hay_gap


def condicion_faltan_datos(ctx: ContextoConclusion) -> bool:
    """CRUZ_4: faltan campos críticos para concluir con certeza alta.

    Es la señal del **gap** (E-CONC-2, insumo de T-502): el caso no está
    contradicho, simplemente no alcanza a afirmarse. Degrada a revisión.
    """
    return ctx.hay_gap


def condicion_conflicto_credito(ctx: ContextoConclusion) -> bool:
    """CRUZ_5: hay un conflicto entre el negocio y el comprobante (R7 + cruce).

    Dos formas del mismo problema — el caso **no está mal**, pero no se puede
    afirmar sin un ojo humano:

    1. **R7** (conflicto financiero): emisor RI ∧ receptor RI ∧ letra sin derecho
       a crédito fiscal. Es el caso del enunciado ("RI emisor + RI receptor +
       documento B → alerta").
    2. **Negocio vs. documento**: cuando hay contexto fiscal y la letra que el
       negocio espera (R1-R3) **no** coincide con la letra vigente del documento
       (p. ej. un emisor Monotributo con una Factura A). Es la discrepancia de
       D-14: el documento manda (default de F3), pero con certeza baja.

    En los dos casos el desenlace es revisión, no rechazo: el sistema sospecha
    que hay un problema, no lo afirma.
    """
    from .tipo_comprobante_rules import condicion_r7

    if condicion_r7(ctx.contexto_tipo):
        return True
    return _negocio_contradice(ctx)


def condicion_negocio_concluye(ctx: ContextoConclusion) -> bool:
    """CRUZ_1: el comprobante se sostiene solo y el negocio no lo contradice.

    El código concluye cuando el caso queda **completo y coherente**:

    - hay una letra del vocabulario (si no, lo reporta ``CRUZ_2``);
    - están los campos críticos (si no, el gap lo reporta ``CRUZ_4``);
    - la letra no se contradice con los campos (si se contradijera, el
      fast-fail ``CRUZ_3`` cerraría el caso antes de llegar acá);
    - **si hay contexto fiscal**, el negocio no espera otra letra (si esperara
      otra, ``CRUZ_5`` lo trataría como conflicto).

    Nota de diseño: la coherencia del **documento** alcanza para concluir; el
    contexto fiscal (padrón/negocio) **refina** el veredicto cuando está
    disponible, pero no es un requisito. Exigirlo dejaría sin concluir a todo
    comprobante que se sostiene solo, que es justamente el caso en que el código
    sí puede decidir (y por lo tanto el que no debe escalar al agente).
    """
    if ctx.letra is None or ctx.hay_gap or not ctx.coherente:
        return False
    return not _negocio_contradice(ctx)


# ---------------------------------------------------------------------------
# Registro
# ---------------------------------------------------------------------------


def construir_registro_cruzadas() -> Registry:
    """Construye el registro de reglas cruzadas (pasada 2) de F5/T-501.

    Devuelve una instancia **nueva** (no el singleton de módulo) para que los
    tests puedan inspeccionarlo o mutarlo sin afectar el motor global (mismo
    criterio que ``construir_registros()`` de F3).
    """
    registro = Registry()
    registro.registrar(
        Rule(
            id=CRUZ_3_COHERENCIA_LETRA,
            prioridad=PRIORIDAD_COHERENCIA,
            condicion=condicion_coherencia_letra,
            resultado=ESTADO_RECHAZADO,
            tipo="cruzada",
            detalle=(
                "CRUZ_3 (fast-fail, coherencia de la letra): la letra vigente "
                "contradice los campos del comprobante (una Factura A sin los "
                "dos CUIT, una B con IVA discriminado). Reutiliza "
                "COHERENCIA_POR_CAMPO de F4/T-403 evaluada sobre el valor "
                "vigente del caso (E-EXT-2). Cierra el caso: el documento se "
                "contradice a sí mismo."
            ),
        )
    )
    registro.registrar(
        Rule(
            id=CRUZ_2_LETRA_SIN_SOSTEN,
            prioridad=PRIORIDAD_SIN_LETRA,
            condicion=condicion_sin_letra,
            resultado=ESTADO_REVISION,
            tipo="cruzada",
            detalle=(
                "CRUZ_2 (fast-fail por falta de sostén): ninguna fuente resolvió "
                "la letra y no quedan candidatos. Sin letra no hay veredicto "
                "posible y el sistema no la inventa (ADR-001)."
            ),
        )
    )
    registro.registrar(
        Rule(
            id=CRUZ_4_DATOS_FALTANTES,
            prioridad=PRIORIDAD_GAP,
            condicion=condicion_faltan_datos,
            resultado=ESTADO_REVISION,
            tipo="cruzada",
            detalle=(
                "CRUZ_4 (gap): faltan campos críticos para concluir con certeza "
                "alta. Es la señal que consume T-502 (búsqueda puntual de "
                "evidencia adicional con límite, E-CONC-2); si el gap no se "
                "cubre, el caso escala (T-504)."
            ),
        )
    )
    registro.registrar(
        Rule(
            id=CRUZ_5_CONFLICTO_CREDITO,
            prioridad=PRIORIDAD_CONFLICTO,
            condicion=condicion_conflicto_credito,
            resultado="ALERTA_ERROR_FINANCIERO",
            tipo="cruzada",
            detalle=(
                "CRUZ_5 (conflicto R7): emisor RI ∧ receptor RI ∧ letra sin "
                "derecho a crédito fiscal. Es sospecha, no contradicción: no "
                "rechaza, exige revisión humana. Fuente: R7 de F3/T-301 "
                "(prompt WIP §reglas.conflicto_auditoria) y Gherkin E-CONC-1."
            ),
        )
    )
    registro.registrar(
        Rule(
            id=CRUZ_1_NEGOCIO,
            prioridad=PRIORIDAD_NEGOCIO,
            condicion=condicion_negocio_concluye,
            resultado=ESTADO_APROBADO,
            tipo="cruzada",
            detalle=(
                "CRUZ_1 (negocio): R1-R7 re-aplicadas sobre los valores vigentes "
                "concluyen la letra con certeza alta (coincidencia negocio vs. "
                "documento o exportación R3). origen=programa, certeza=alta: el "
                "caso NO pasa por el agente de IA (Gherkin E-CONC-1)."
            ),
        )
    )
    return registro


#: Registro singleton de las reglas cruzadas (pasada 2, F5/T-501).
REGISTRO_CRUZADAS: Registry = construir_registro_cruzadas()


# ---------------------------------------------------------------------------
# Evaluación
# ---------------------------------------------------------------------------


@dataclass
class VeredictoCruzadas:
    """Resultado de la pasada 2 (E-CONC-1) — insumo del ``Decision`` de F0.

    Campos:
        disparadas: ids de las reglas cruzadas que se dispararon, en orden de
            prioridad (trazabilidad E-CONC-5).
        fast_fail: ``True`` si alguna regla de fast-fail disparó (el caso se
            **cierra**: contradicción dura o falta total de sostén).
        concluye: ``True`` cuando el código alcanzó un veredicto definitivo
            (aprobado o rechazado) sin necesitar más datos ni al agente.
        estado: ``aprobado`` | ``rechazado`` | ``revision`` (contrato de F0).
        motivo: explicación legible del veredicto (auditoría).
        alertas: conflicto R7 y demás alertas de auditoría (dicts serializables).
        faltan_datos: campos críticos ausentes (insumo de T-502).
        conflictos: motivos de las incoherencias, las "reglas que fallaron" que
            el agente recibe en T-504 (``reglas_que_fallaron`` del pseudocódigo).
        reglas_por_familia: ``familia -> ids`` disparados, para el reporte.
    """

    disparadas: list[str] = field(default_factory=list)
    fast_fail: bool = False
    concluye: bool = False
    estado: str = ESTADO_REVISION
    motivo: str = ""
    alertas: list[dict[str, Any]] = field(default_factory=list)
    faltan_datos: list[str] = field(default_factory=list)
    conflictos: list[str] = field(default_factory=list)
    reglas_por_familia: dict[str, list[str]] = field(default_factory=dict)

    @property
    def alertas_de_auditoria(self) -> bool:
        """True si hay alertas que degradan el caso a revisión (R7)."""
        return bool(self.alertas)

    @property
    def decidido(self) -> bool:
        """True si **alguna etapa** resolvió el caso (no requiere agente/HITL).

        Es la condición para poder emitir un ``Decision`` del contrato de F0: un
        ``Decision`` afirma quién decidió y con qué certeza, así que solo existe
        cuando el caso quedó resuelto. Un caso ambiguo viaja **sin** ``Decision``
        (``concluye=False``): todavía no decidió nadie, y el validador de F0 lo
        exige — ``origen=programa`` implica ``certeza=alta`` (glosario §2).
        """
        return self.concluye


#: Familia de cada regla cruzada (para el reporte por familia).
FAMILIA_POR_REGLA: dict[str, str] = {
    CRUZ_1_NEGOCIO: FAMILIA_NEGOCIO,
    CRUZ_2_LETRA_SIN_SOSTEN: FAMILIA_FAST_FAIL,
    CRUZ_3_COHERENCIA_LETRA: FAMILIA_FAST_FAIL,
    CRUZ_4_DATOS_FALTANTES: FAMILIA_FAST_FAIL,
    CRUZ_5_CONFLICTO_CREDITO: FAMILIA_CONFLICTO,
}

#: Orden de prioridad para decidir (las reglas se evalúan todas, pero el
#: veredicto lo fija la primera familia que resuelve).
_ORDEN_DECISION: tuple[str, ...] = (
    CRUZ_3_COHERENCIA_LETRA,
    CRUZ_2_LETRA_SIN_SOSTEN,
    CRUZ_4_DATOS_FALTANTES,
    CRUZ_5_CONFLICTO_CREDITO,
    CRUZ_1_NEGOCIO,
)


def evaluar_cruzadas(ctx: ContextoConclusion) -> VeredictoCruzadas:
    """Corre la pasada 2 sobre el contexto combinado y resuelve el veredicto.

    Evalúa **todas** las reglas del registro (para que la traza muestre qué más
    estaba en juego) y luego resuelve el desenlace con esta precedencia:

    1. **CRUZ_3** (contradicción de la letra) → ``rechazado``, certeza ``alta``.
       El fast-fail gana a todo lo demás: si el documento se contradice, el
       resto de los datos no cambia el veredicto.
    2. **CRUZ_2** (sin letra ni candidatos) → ``revision``, certeza ``baja``.
    3. **CRUZ_4** (gap de campos críticos) → ``revision``, certeza ``baja``.
    4. **CRUZ_5** (conflicto R7) → ``revision``, certeza ``baja`` **con alerta**.
    5. **CRUZ_1** (el negocio concluye con certeza alta) → ``aprobado``,
       certeza ``alta``, origen ``programa``.
    6. Si **nada** disparó → ``revision``, certeza ``baja``, ``concluye=False``:
       el caso es ambiguo y escala (T-504). No se aprueba por descarte, que es
       justamente lo que el patrón prohíbe.
    """
    disparadas = [regla.id for regla in REGISTRO_CRUZADAS.evaluar_todas(ctx)]
    ids = set(disparadas)

    veredicto = VeredictoCruzadas(
        disparadas=disparadas,
        faltan_datos=list(ctx.campos_criticos_ausentes),
        conflictos=list(ctx.incoherencias),
        reglas_por_familia=_por_familia(disparadas),
    )

    # --- 1. Fast-fail: el documento se contradice a sí mismo ---
    if CRUZ_3_COHERENCIA_LETRA in ids:
        veredicto.estado = ESTADO_RECHAZADO
        veredicto.concluye = True
        veredicto.fast_fail = True
        veredicto.motivo = (
            f"La letra vigente ({ctx.letra}) contradice los campos del "
            f"comprobante: {' '.join(ctx.incoherencias)} "
            "(CRUZ_3, fast-fail)."
        )
        if ctx.letra is not None and CRUZ_5_CONFLICTO_CREDITO in ids:
            veredicto.alertas.append(_alerta_conflicto(ctx))
        return veredicto

    # --- 2. Sin letra y sin candidatos: nada que afirmar ---
    if CRUZ_2_LETRA_SIN_SOSTEN in ids:
        veredicto.estado = ESTADO_REVISION
        veredicto.concluye = False
        veredicto.motivo = MOTIVO_SIN_LETRA
        return veredicto

    # --- 3. Gap de datos críticos ---
    if CRUZ_4_DATOS_FALTANTES in ids:
        veredicto.estado = ESTADO_REVISION
        veredicto.concluye = False
        veredicto.motivo = MOTIVO_GAP.format(
            campos=", ".join(sorted(ctx.campos_criticos_ausentes))
        )
        return veredicto

    # --- 4. Conflicto R7: sospecha, no contradicción ---
    if CRUZ_5_CONFLICTO_CREDITO in ids:
        veredicto.estado = ESTADO_REVISION
        veredicto.concluye = False
        veredicto.motivo = MOTIVO_CONFLICTO_CREDITO
        veredicto.alertas.append(_alerta_conflicto(ctx))
        return veredicto

    # --- 5. El negocio concluye: certeza alta por programa ---
    if CRUZ_1_NEGOCIO in ids:
        veredicto.estado = ESTADO_APROBADO
        veredicto.concluye = True
        veredicto.motivo = MOTIVO_NEGOCIO_CONCLUYE
        return veredicto

    # --- 6. Ambigüedad: no se aprueba por descarte ---
    #
    # Rama **defensiva**: por construcción es inalcanzable, porque el
    # complemento de CRUZ_1 (letra ausente ∨ gap ∨ incoherencia ∨ negocio que
    # espera otra letra) es exactamente la unión de los disparadores de
    # CRUZ_2/CRUZ_3/CRUZ_4/CRUZ_5. Se conserva por si una regla futura relaja su
    # condición: el peor resultado posible tiene que seguir siendo ``revision``
    # (nunca aprobar por descarte), no una excepción ni un caso sin estado.
    veredicto.estado = ESTADO_REVISION  # pragma: no cover - defensivo
    veredicto.concluye = False  # pragma: no cover - defensivo
    veredicto.motivo = (  # pragma: no cover - defensivo
        "Las reglas cruzadas no resolvieron el caso: ninguna regla disparó. El "
        "caso escala (T-504/T-505); no se aprueba por descarte."
    )
    return veredicto  # pragma: no cover - defensivo


def construir_decision(
    ctx: ContextoConclusion, veredicto: VeredictoCruzadas
) -> Decision | None:
    """Arma el ``Decision`` (contrato congelado de F0) del veredicto de T-501.

    Devuelve ``None`` cuando el caso **no** quedó resuelto por la pasada 2
    (``concluye=False``). Es una restricción del contrato de F0, no una omisión:
    ``Decision`` registra *quién decidió* y *con qué certeza*, y el validador de
    ``CombinedEvidence`` exige que ``origen=programa`` implique
    ``certeza=alta`` (glosario §2: la certeza se deriva de la etapa que decidió).
    Un caso ambiguo no lo decidió nadie todavía: lo resolverán el agente (T-504,
    con ``origen=agente_ia``/``certeza=baja`` de una etapa que sí decide) o el
    HITL (T-505). Emitir acá un ``Decision`` con ``origen=programa`` a secas
    afirmaría que el programa decidió cuando en realidad no pudo — y el contrato
    lo rechaza.

    Cuando devuelve un ``Decision``, este es el **final del caso** (porque la
    pasada 2 concluyó): ``certeza=alta`` (el código concluyó) y ``origen=programa``.
    """
    if not veredicto.concluye:
        return None

    return Decision(
        concluye=True,
        certeza="alta",
        origen="programa",
        candidatos_descartados=list(ctx.candidatos_descartados),
        candidatos_restantes=list(ctx.candidatos_restantes),
        reglas_aplicadas=list(veredicto.disparadas),
        alertas=list(veredicto.alertas),
    )


def resumen_cruzadas(veredicto: VeredictoCruzadas) -> dict[str, Any]:
    """Resumen serializable de la pasada 2, para el detalle de la corrida."""
    return {
        "version": VERSION_CRUZADAS,
        "reglas_disparadas": list(veredicto.disparadas),
        "reglas_por_familia": {k: list(v) for k, v in veredicto.reglas_por_familia.items()},
        "fast_fail": veredicto.fast_fail,
        "concluye": veredicto.concluye,
        "estado": veredicto.estado,
        "motivo": veredicto.motivo,
        "alertas": list(veredicto.alertas),
        "faltan_datos": list(veredicto.faltan_datos),
        "conflictos": list(veredicto.conflictos),
    }


# ---------------------------------------------------------------------------
# Resultado de la conclusión (doc 03 §4.5 / CONC.md §1)
# ---------------------------------------------------------------------------


@dataclass
class ConclusionResult:
    """Resultado de la pasada 2 de conclusión (doc 03 §4.5, E-CONC-1).

    Es la **interfaz acordada** del módulo ``conclusion`` (``CONC.md`` §3) y la
    vista completa del veredicto, incluido el caso que **no** concluyó. Existe
    porque el contrato de F0 (``Decision``) es más estrecho: ``Decision`` afirma
    *quién* decidió, y por eso solo puede emitirse cuando alguien decidió (su
    validador exige ``programa ⇒ certeza=alta``). Un caso ambiguo —el que va al
    agente o al HITL— necesita un lugar donde vivir con su estado, sus gaps y sus
    conflictos, y ese es este dataclass.

    Campos (los del diseño §4.5, más la traza que el caso necesita):
        concluye: ``True`` si el código (o, más adelante, el agente) resolvió el
            caso sin necesitar revisión humana.
        certeza / origen: ``"alta"``/``"programa"`` cuando el código concluyó;
            ``None`` cuando todavía no decidió nadie (lo fijará la etapa que
            decida en T-504/T-505).
        estado: ``aprobado`` | ``rechazado`` | ``revision`` (contrato de F0).
        candidatos_descartados / candidatos_restantes: el conjunto cerrado que
            dejó el código (blindaje ADR-008).
        reglas_aplicadas: ids de las reglas cruzadas disparadas (E-CONC-5).
        alertas: alertas de auditoría (R7).
        hitl: ``HitlDecision`` **informativo** del paso que el caso necesita
            (T-505 lo materializa); acá viaja como expectativa.
        decision: el ``Decision`` de F0 cuando el caso quedó resuelto por el
            código (``None`` si es ambiguo).
        faltan_datos / conflictos / motivo: el diagnóstico del veredicto —
            insumo de T-502 (gaps) y T-504 (reglas que fallaron).
        reglas_por_familia: ``negocio``/``fast_fail``/``conflicto`` → ids, para
            el reporte y la auditoría.
    """

    concluye: bool
    certeza: str | None
    origen: str | None
    estado: str
    candidatos_descartados: list[str] = field(default_factory=list)
    candidatos_restantes: list[str] = field(default_factory=list)
    reglas_aplicadas: list[str] = field(default_factory=list)
    alertas: list[dict[str, Any]] = field(default_factory=list)
    hitl: "HitlDecision | None" = None
    decision: Decision | None = None
    faltan_datos: list[str] = field(default_factory=list)
    conflictos: list[str] = field(default_factory=list)
    motivo: str = ""
    fast_fail: bool = False
    reglas_por_familia: dict[str, list[str]] = field(default_factory=dict)

    def como_dict(self) -> dict[str, Any]:
        """Resumen serializable (trazabilidad del caso / reporte del script)."""
        return {
            "version": VERSION_CRUZADAS,
            "concluye": self.concluye,
            "estado": self.estado,
            "certeza": self.certeza,
            "origen": self.origen,
            "fast_fail": self.fast_fail,
            "motivo": self.motivo,
            "reglas_aplicadas": list(self.reglas_aplicadas),
            "reglas_por_familia": {
                k: list(v) for k, v in self.reglas_por_familia.items()
            },
            "candidatos_descartados": list(self.candidatos_descartados),
            "candidatos_restantes": list(self.candidatos_restantes),
            "alertas": list(self.alertas),
            "faltan_datos": list(self.faltan_datos),
            "conflictos": list(self.conflictos),
        }


def construir_conclusion(
    ctx: ContextoConclusion, veredicto: VeredictoCruzadas
) -> ConclusionResult:
    """Arma el :class:`ConclusionResult` del veredicto de la pasada 2 (T-501).

    Traduce el veredicto al contrato del diseño §4.5 derivando la certeza y el
    origen **de la etapa que decidió** (glosario §2):

    - si el código concluyó → ``certeza="alta"``, ``origen="programa"`` y un
      ``Decision`` de F0 (el veredicto es final: no pasa por el agente);
    - si no concluyó → ``certeza=None``, ``origen=None`` y **sin** ``Decision``,
      con la expectativa de HITL que corresponda (prioridad ``alta`` si el caso
      quedó ambiguo: lo resolverá el agente/HITL de T-504/T-505).
    """
    from ..schemas.result import HitlDecision

    decision = construir_decision(ctx, veredicto)
    if veredicto.concluye:
        hitl = HitlDecision(
            requerido=False,
            prioridad="baja",
            estado="no_aplica",
        )
        certeza: str | None = "alta"
        origen: str | None = "programa"
    else:
        # El caso no quedó resuelto: alguien tiene que mirarlo. La prioridad es
        # alta porque no hay veredicto firme (es el caso que T-505 encola).
        hitl = HitlDecision(
            requerido=True,
            prioridad="alta",
            estado="pendiente",
        )
        certeza = None
        origen = None

    return ConclusionResult(
        concluye=veredicto.concluye,
        certeza=certeza,
        origen=origen,
        estado=veredicto.estado,
        candidatos_descartados=list(ctx.candidatos_descartados),
        candidatos_restantes=list(ctx.candidatos_restantes),
        reglas_aplicadas=list(veredicto.disparadas),
        alertas=list(veredicto.alertas),
        hitl=hitl,
        decision=decision,
        faltan_datos=list(veredicto.faltan_datos),
        conflictos=list(veredicto.conflictos),
        motivo=veredicto.motivo,
        fast_fail=veredicto.fast_fail,
        reglas_por_familia={k: list(v) for k, v in veredicto.reglas_por_familia.items()},
    )


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _negocio_contradice(ctx: ContextoConclusion) -> bool:
    """True si el contexto fiscal espera una letra **distinta** de la vigente.

    Solo puede responder que sí cuando hay contexto fiscal suficiente para que
    R1-R3 resuelvan una letra esperada; sin condiciones fiscales, el negocio no
    contradice nada (no se inventa un esperado). Es el cruce de D-14: negocio vs.
    documento.
    """
    esperado = _letra_esperada_por_negocio(ctx)
    return esperado is not None and ctx.letra is not None and esperado != ctx.letra


def _letra_esperada_por_negocio(ctx: ContextoConclusion) -> str | None:
    """Letra que espera el negocio (R1-R3) o ``None`` si no se puede saber.

    Es puramente tributario (condiciones fiscales y país del receptor): no mira
    el documento, así que sirve como contraste independiente de la lectura.
    """
    try:
        from .tipo_comprobante_rules import evaluar_negocio

        return evaluar_negocio(ctx.contexto_tipo)
    except Exception:  # pragma: no cover - defensivo: una regla no tumba la corrida
        return None


def _clasificar(ctx: ContextoConclusion) -> Any:
    """Re-aplica el motor R1-R7 de F3 sobre los valores vigentes (CRUZ_1).

    Import diferido para no acoplar ``rules`` a ``classification`` a nivel de
    módulo (``classification`` importa ``rules.contexto``); se resuelve la
    primera vez que se evalúa la regla. Devuelve ``None`` si el motor no puede
    correr (contexto insuficiente), en cuyo caso la regla no dispara.
    """
    try:
        from ..classification.tipo_comprobante import clasificar_tipo_comprobante

        return clasificar_tipo_comprobante(
            ctx.contexto_tipo,
            preferencia_letra="documento",
        )
    except Exception:  # pragma: no cover - defensivo: una regla nunca debe tumbar la corrida
        return None


def _alerta_conflicto(ctx: ContextoConclusion) -> dict[str, Any]:
    """Alerta de auditoría del conflicto R7 (E-CONC-1 / R7 de F3)."""
    from .tipo_comprobante_rules import MENSAJE_R7

    return {
        "regla": CRUZ_5_CONFLICTO_CREDITO,
        "familia": FAMILIA_CONFLICTO,
        "tipo": "ALERTA_ERROR_FINANCIERO",
        "mensaje": MENSAJE_R7,
        "letra": ctx.letra,
        "fuente_letra": ctx.fuente_letra.value if ctx.fuente_letra else None,
        "emisor_condicion_fiscal": ctx.contexto_tipo.emisor_condicion_fiscal,
        "receptor_condicion_fiscal": ctx.contexto_tipo.receptor_condicion_fiscal,
    }


def _por_familia(ids: list[str]) -> dict[str, list[str]]:
    """Agrupa los ids disparados por familia, en orden de prioridad."""
    familias: dict[str, list[str]] = {}
    for identificador in ids:
        familia = FAMILIA_POR_REGLA.get(identificador, "otra")
        familias.setdefault(familia, []).append(identificador)
    return familias


__all__ = [
    "VERSION_CRUZADAS",
    "REGISTRO_CRUZADAS",
    "FAMILIA_POR_REGLA",
    "ESTADO_APROBADO",
    "ESTADO_RECHAZADO",
    "ESTADO_REVISION",
    "MOTIVO_SIN_LETRA",
    "MOTIVO_GAP",
    "MOTIVO_CONFLICTO_CREDITO",
    "MOTIVO_NEGOCIO_CONCLUYE",
    "VeredictoCruzadas",
    "ConclusionResult",
    "construir_registro_cruzadas",
    "evaluar_cruzadas",
    "construir_decision",
    "construir_conclusion",
    "resumen_cruzadas",
    "condicion_coherencia_letra",
    "condicion_sin_letra",
    "condicion_faltan_datos",
    "condicion_conflicto_credito",
    "condicion_negocio_concluye",
]

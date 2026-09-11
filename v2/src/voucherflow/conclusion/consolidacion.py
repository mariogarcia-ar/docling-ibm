"""Consolidación del ``VoucherResult`` — "certeza alta por programa" (F5 / T-503).

**Fase**: F5 — Conclusión. **Tarea**: T-503 · **Épica**: E-CONC-1.

T-501 **resuelve** el caso (la pasada 2 de reglas cruzadas produce el veredicto) y
T-502 **cubre los gaps** que el veredicto nombra. Este módulo hace la tercera
cosa, que es la que pide el Gherkin de E-CONC-1:

    Regla: concluye por programa
      Dado que las reglas concluyen de forma consistente
      Cuando se consolida el resultado
      Entonces se marca certeza=alta y origen=programa
      Y NO pasó por el agente de IA para decidir

Es decir: convierte el veredicto en el **objeto final** que consume la API/CLI
(``VoucherResult``, E-LIB-1) — estado, tipo, certeza, origen, campos planos,
clasificación contable y HITL — y **declara por qué** la certeza es la que es.

Qué se consolida (y qué no)
---------------------------
Se consolida **el caso resuelto por el código**. Lo que **no** se hace acá: llamar
al agente IA (T-504) ni encolar la revisión humana (T-505). Un caso que el código
no pudo concluir sale marcado como ``revision`` con ``certeza=baja`` y **sin**
``origen`` (todavía no lo decidió nadie): es la señal para que T-504/T-505 tomen
el caso, no un resultado final.

La regla de la certeza (el corazón de la tarea)
-----------------------------------------------
``certeza=alta`` + ``origen=programa`` **si y solo si** el código concluyó **sin
ambigüedad**. Se traduce a una condición verificable:

1. el veredicto de la pasada 2 **concluye** (``concluye=True``);
2. **no hay alertas pendientes** — una alerta de R7 significa que el conflicto
   quedó **sin resolver**, y el Gherkin pide "concluyen **de forma consistente**";
3. hay una **letra** que reportar (un caso aprobado sin letra sería una
   aprobación vacía).

Las tres juntas son "sin ambigüedad". Cualquier otra combinación es ``baja``: el
caso puede estar perfectamente **rechazado** con certeza alta (el código concluyó
que no, y lo afirma), pero un caso con el conflicto abierto **no** puede
declararse de certeza alta aunque el código haya llegado a un estado.

Decisiones de diseño (T-503)
----------------------------
1. **La certeza se deriva, no se declara** (glosario §2). El consolidador **no**
   recibe una certeza: la calcula del veredicto. Es la forma de que no haya dos
   fuentes de verdad sobre "qué tan seguro estamos".
2. **Un rechazo firme es certeza alta.** Es contraintuitivo pero correcto: "esto
   no es un comprobante válido" es una conclusión, no una duda. La certeza mide
   *cuánto sabe el sistema*, no si le gustó el resultado.
3. **Una alerta sin resolver baja la certeza.** El fast-fail puede coexistir con
   R7 (un caso rechazado igual deja la alerta para auditoría, T-501); mientras la
   alerta siga ahí, el caso no es "consistente".
4. **La trazabilidad de la certeza queda escrita.** El ``VoucherResult`` guarda el
   **motivo** de la certeza (qué reglas concluyeron, qué alertas quedaron, por qué
   no es alta). Es el insumo de la auditoría (E-CONC-5) y lo que hace verificable
   la regla del Gherkin en lugar de dejarla implícita.
5. **Sin clasificación contable no se inventa una.** La cadena 01→02→03 necesita
   los pasos resueltos (F3/T-304) o el modelo; si no se los pasa, el
   ``VoucherResult`` sale **sin** ``clasificacion_contable`` y la traza lo declara.
   Un caso sin clasificar no es un caso mal clasificado.
6. **Los campos se publican planos y normalizados** (glosario §2.4): el valor
   vigente por campo de la resolución de T-404, tal como lo espera el consumidor
   de la librería.

Referencias: doc 03 §4.5, `CONC.md` §1/§3, Gherkin E-CONC-1 ("concluye por
programa"), glosario §2/§2.4, ADR-001/ADR-002, F5-subplan §3.3.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from ..rules.contexto import ContextoTipoComprobante
from ..rules.contexto_conclusion import ContextoConclusion
from ..rules.cruzadas import (
    ESTADO_APROBADO,
    ESTADO_RECHAZADO,
    ESTADO_REVISION,
    ConclusionResult,
)
from ..schemas.evidence import (
    Certeza,
    CombinedEvidence,
    EstadoResultado,
    Origen,
)
from ..schemas.result import (
    ClasificacionContable,
    HitlDecision,
    VoucherResult,
)

#: Versión de la consolidación (se registra en la trazabilidad del caso).
VERSION_CONSOLIDACION = "conclusion-consolidacion@1"

#: Motivos canónicos de la certeza (van a la traza; en español y citando el
#: Gherkin, que es lo que la regla implementa).
MOTIVO_ALTA_POR_PROGRAMA = (
    "El código concluyó de forma consistente ({reglas}) y no quedaron alertas "
    "pendientes: certeza alta, origen programa. El caso NO pasó por el agente de "
    "IA para decidir (Gherkin E-CONC-1: «concluye por programa»)."
)
MOTIVO_RECHAZO_FIRME = (
    "El código concluyó que el comprobante no es válido ({reglas}). Un rechazo "
    "firme es una conclusión, no una duda: certeza alta, origen programa."
)
MOTIVO_SIN_LETRA = (
    "El veredicto concluyó pero no hay una letra de comprobante que reportar: no "
    "se consolida como certeza alta (una aprobación sin letra sería vacía)."
)
MOTIVO_ALERTA_PENDIENTE = (
    "El conflicto quedó sin resolver: hay {n} alerta(s) pendiente(s) ({reglas}). "
    "El Gherkin de E-CONC-1 exige que las reglas concluyan «de forma "
    "consistente» para la certeza alta, así que la certeza baja."
)
MOTIVO_AMBIGUO = (
    "El código no pudo concluir el caso: queda para el agente (T-504) o para la "
    "revisión humana (T-505). Sin veredicto no hay origen ni certeza que "
    "consolidar."
)
MOTIVO_AGENTE = (
    "El veredicto lo produjo el **agente de IA** ({desenlace}), no el código: la "
    "certeza es baja y el origen agente_ia (glosario §2). No se consolida como "
    "certeza alta por programa — el Gherkin E-CONC-1 exige que para eso hayan "
    "concluido las reglas en código."
)
MOTIVO_SIN_CLASIFICACION = (
    "No se aportaron los pasos contables resueltos (F3/T-304), así que el "
    "resultado viaja sin la clasificación contable. No se inventa una."
)


@dataclass
class Consolidacion:
    """``VoucherResult`` con la traza de **por qué** quedó con esa certeza (T-503).

    ``valor`` es el contrato congelado de F0 que consume la API/CLI;
    ``motivo_certeza`` la explicación verificable (lo que el Gherkin pide que
    ocurra "cuando se consolida el resultado") y ``concluyo_por_programa`` el
    booleano directo de la historia: ``True`` si y solo si la certeza quedó en
    ``alta`` por ``programa``, sin pasar por el agente.
    """

    valor: VoucherResult
    motivo_certeza: str = ""
    concluyo_por_programa: bool = False
    alertas_pendientes: list[dict[str, Any]] = field(default_factory=list)
    clasificacion_disponible: bool = False

    @property
    def certeza(self) -> Certeza | None:
        return self.valor.certeza

    @property
    def origen(self) -> Origen | None:
        return self.valor.origen

    @property
    def estado(self) -> EstadoResultado:
        return self.valor.estado

    def como_dict(self) -> dict[str, Any]:
        return {
            "version": VERSION_CONSOLIDACION,
            "documento_id": self.valor.documento_id,
            "estado": self.valor.estado.value,
            "tipo_comprobante": self.valor.tipo_comprobante,
            "certeza": self.valor.certeza.value if self.valor.certeza else None,
            "origen": self.valor.origen.value if self.valor.origen else None,
            "concluyo_por_programa": self.concluyo_por_programa,
            "motivo_certeza": self.motivo_certeza,
            "alertas_pendientes": list(self.alertas_pendientes),
            "campos_extraidos": len(self.valor.campos_extraidos),
            "clasificacion_disponible": self.clasificacion_disponible,
            "hitl": {
                "requerido": self.valor.hitl.requerido,
                "prioridad": self.valor.hitl.prioridad,
                "estado": self.valor.hitl.estado,
            },
        }


# ---------------------------------------------------------------------------
# La regla de la certeza
# ---------------------------------------------------------------------------


def es_certeza_alta_por_programa(
    conclusion: ConclusionResult,
    *,
    tipo_comprobante: str | None,
) -> tuple[bool, str]:
    """Decide si el caso se consolida con certeza ``alta`` + origen ``programa``.

    Implementa la condición del Gherkin de E-CONC-1 ("las reglas concluyen de
    forma consistente") como algo verificable:

    - **lo decidió el código** (``origen == programa``): si el veredicto lo
      produjo el **agente** (T-504), la certeza es baja y el origen
      ``agente_ia`` aunque el veredicto esté cerrado — el Gherkin pide que la
      certeza alta por programa venga de las reglas en código;
    - ``concluye`` — el código alcanzó un veredicto;
    - **sin alertas pendientes** — el conflicto no quedó abierto;
    - **con letra** — hay algo que afirmar.

    Devuelve ``(es_alta, motivo)``. El motivo viaja a la traza: es lo que permite
    auditar después por qué un caso quedó en certeza baja.
    """
    if conclusion.origen == Origen.agente_ia:
        return False, MOTIVO_AGENTE.format(
            desenlace=conclusion.estado or "sin estado"
        )

    if not conclusion.concluye:
        return False, MOTIVO_AMBIGUO

    if conclusion.alertas:
        reglas = ", ".join(
            str(alerta.get("regla", "?")) for alerta in conclusion.alertas
        )
        return False, MOTIVO_ALERTA_PENDIENTE.format(
            n=len(conclusion.alertas), reglas=reglas
        )

    if not tipo_comprobante:
        return False, MOTIVO_SIN_LETRA

    if conclusion.estado == ESTADO_RECHAZADO:
        return True, MOTIVO_RECHAZO_FIRME.format(
            reglas=", ".join(conclusion.reglas_aplicadas) or "fast-fail"
        )

    return True, MOTIVO_ALTA_POR_PROGRAMA.format(
        reglas=", ".join(conclusion.reglas_aplicadas) or "negocio"
    )


# ---------------------------------------------------------------------------
# La consolidación
# ---------------------------------------------------------------------------


def consolidar(
    evidencia: CombinedEvidence,
    conclusion: ConclusionResult,
    *,
    contexto_tipo: ContextoTipoComprobante | None = None,
    contexto: ContextoConclusion | None = None,
    clasificacion: ClasificacionContable | None = None,
    hitl: HitlDecision | None = None,
    evidencia_con_traza: CombinedEvidence | None = None,
) -> Consolidacion:
    """Consolida el veredicto en el ``VoucherResult`` final (F5/T-503).

    Arma el contrato congelado de F0 (glosario §2.4) a partir de:

    - **estado** — el del veredicto (``aprobado``/``rechazado``/``revision``);
    - **tipo_comprobante** — la letra vigente de la resolución por campo (T-404);
    - **certeza/origen** — **derivados** por :func:`es_certeza_alta_por_programa`
      (nunca declarados por el llamador: una sola fuente de verdad);
    - **campos_extraidos** — los valores vigentes por campo, planos;
    - **clasificacion_contable** — la cadena de F3 si se aportó (no se inventa);
    - **hitl** — la expectativa del veredicto (T-505 la materializa);
    - **trazabilidad** — la de la corrida más el bloque de la consolidación.

    Es **determinística y sin red**.

    Argumentos:
        evidencia: la ``CombinedEvidence`` (ya concluida o no: el estado lo manda
            ``conclusion``).
        conclusion: el :class:`ConclusionResult` de la pasada 2 (T-501).
        contexto_tipo: el contexto fiscal (para las señales de trazabilidad y la
            condición impositiva de la clasificación).
        contexto: el :class:`ContextoConclusion` de la corrida, si se tiene: es la
            vía para conocer la **letra vigente** cuando no hay ``contexto_tipo``.
        clasificacion: el resultado de la cadena contable (F3/T-304), si se corrió.
        hitl: la ``HitlDecision`` a publicar. Default: la del ``ConclusionResult``.
        evidencia_con_traza: la evidencia **ya concluida** (con el bloque
            ``conclusion`` de T-501 en su trazabilidad), cuando el llamador la
            tenga. La traza de la consolidación se construye sobre esta; si no se
            pasa, se usa ``evidencia`` tal cual. Es lo que permite que el
            ``VoucherResult`` conserve **todas** las etapas (combinación →
            conclusión → consolidación) aunque el llamador reciba la evidencia
            sin concluir.

    Devuelve:
        :class:`Consolidacion`, con el ``VoucherResult`` y la traza de la certeza.

    Lanza:
        ``TypeError`` si ``evidencia`` no es una ``CombinedEvidence``.
    """
    if not isinstance(evidencia, CombinedEvidence):
        raise TypeError(
            "consolidar() espera una CombinedEvidence (la salida de la combinación "
            f"de F4/T-404); recibido: {type(evidencia).__name__}."
        )

    letra = _letra_vigente(evidencia, contexto)
    es_alta, motivo = es_certeza_alta_por_programa(
        conclusion, tipo_comprobante=letra
    )
    decidido_por_agente = conclusion.origen == Origen.agente_ia

    resultado = VoucherResult(
        documento_id=evidencia.documento_id,
        estado=EstadoResultado(conclusion.estado),
        tipo_comprobante=letra,
        certeza=Certeza.alta if es_alta else Certeza.baja,
        # ``origen`` solo cuando **alguien** concluyó: el código (``programa``) o
        # el agente (``agente_ia``, T-504). Si el caso quedó ambiguo no lo decidió
        # nadie todavía (lo llenarán T-504/T-505) y poner ``programa`` afirmaría
        # un veredicto que el código no alcanzó.
        origen=(
            Origen.agente_ia
            if decidido_por_agente
            else (Origen.programa if conclusion.concluye else None)
        ),
        campos_extraidos=_campos_planos(evidencia),
        clasificacion_contable=clasificacion,
        evidencia=evidencia,
        hitl=hitl if hitl is not None else _hitl_de(conclusion),
        trazabilidad=_trazabilidad(
            evidencia_con_traza if evidencia_con_traza is not None else evidencia,
            conclusion,
            contexto_tipo,
            motivo,
            clasificacion is not None,
        ),
    )

    return Consolidacion(
        valor=resultado,
        motivo_certeza=motivo,
        concluyo_por_programa=es_alta,
        alertas_pendientes=list(conclusion.alertas) if not es_alta else [],
        clasificacion_disponible=clasificacion is not None,
    )


# ---------------------------------------------------------------------------
# Piezas de la consolidación
# ---------------------------------------------------------------------------


def _letra_vigente(
    evidencia: CombinedEvidence, contexto: ContextoConclusion | None
) -> str | None:
    """La letra del comprobante que quedó vigente (T-404), si la hay.

    Se prefiere el contexto de la conclusión (que ya normalizó la letra contra el
    vocabulario del motor); si no se pasó, se lee el valor vigente del campo y se
    exige que sea una letra conocida — un ``"090"`` no se reporta como letra
    (D-13), porque el motor no lo reconoce como tal.
    """
    if contexto is not None and contexto.letra:
        return contexto.letra

    campo = evidencia.campos.get("tipo_comprobante")
    if campo is None or campo.valor is None:
        return None

    texto = str(campo.valor).strip().upper()
    from ..rules.contexto import LETRAS_COMPROBANTE

    return texto if texto in LETRAS_COMPROBANTE else None


def _campos_planos(evidencia: CombinedEvidence) -> dict[str, Any]:
    """``campo -> valor`` vigente, plano (glosario §2.4).

    Solo los campos que alguna fuente declaró: un campo ausente no se rellena con
    ``None`` (la ausencia se reporta en la traza, no como un valor).
    """
    return {
        campo: combinado.valor
        for campo, combinado in sorted(evidencia.campos.items())
        if combinado.fuente is not None
    }


def _hitl_de(conclusion: ConclusionResult) -> HitlDecision:
    """``HitlDecision`` del veredicto (el que T-505 va a materializar)."""
    if conclusion.hitl is not None:
        return conclusion.hitl
    return HitlDecision(requerido=False, prioridad="baja", estado="no_aplica")


def _trazabilidad(
    evidencia: CombinedEvidence,
    conclusion: ConclusionResult,
    contexto_tipo: ContextoTipoComprobante | None,
    motivo_certeza: str,
    tiene_clasificacion: bool,
) -> dict[str, Any]:
    """Traza completa de la corrida + el bloque de la consolidación (E-CONC-5)."""
    trazabilidad = dict(evidencia.trazabilidad)
    trazabilidad["consolidacion"] = {
        "version": VERSION_CONSOLIDACION,
        "estado": conclusion.estado,
        "concluye": conclusion.concluye,
        "motivo_certeza": motivo_certeza,
        "reglas_aplicadas": list(conclusion.reglas_aplicadas),
        "reglas_por_familia": {
            familia: list(ids) for familia, ids in conclusion.reglas_por_familia.items()
        },
        "alertas": list(conclusion.alertas),
        "faltan_datos": list(conclusion.faltan_datos),
        "conflictos": list(conclusion.conflictos),
        "candidatos_descartados": list(conclusion.candidatos_descartados),
        "candidatos_restantes": list(conclusion.candidatos_restantes),
        "contexto_fiscal_disponible": contexto_tipo is not None,
        "condicion_impositiva": _condicion_impositiva(contexto_tipo),
        "clasificacion_disponible": tiene_clasificacion,
        "nota": (
            "Consolidación T-503 (E-CONC-1): la certeza se deriva del veredicto, "
            "no se declara. Alta + programa ⇔ el código concluyó de forma "
            "consistente (concluye, sin alertas pendientes y con letra). El "
            "agente (T-504) y la cola HITL (T-505) son las etapas siguientes; el "
            "`CaseRecord` persistido es T-506."
        ),
    }
    if not tiene_clasificacion:
        trazabilidad["consolidacion"]["nota_clasificacion"] = MOTIVO_SIN_CLASIFICACION
    return trazabilidad


def _condicion_impositiva(
    contexto_tipo: ContextoTipoComprobante | None,
) -> str | None:
    """Condición impositiva a partir del contexto fiscal, si se puede inferir.

    El mapeo condición fiscal → alícuota es el que usa la cadena contable de F3
    (``21`` para Responsable Inscripto, etc.). Sin contexto no se inventa: una
    condición impositiva inventada cambiaría la clasificación contable.
    """
    if contexto_tipo is None:
        return None

    from ..rules.contexto import (
        CONDICION_EXENTO,
        CONDICION_MONOTRIBUTO,
        CONDICION_RI,
    )

    condicion = contexto_tipo.emisor_condicion_fiscal
    if condicion == CONDICION_RI:
        return "21"
    if condicion in {CONDICION_MONOTRIBUTO, CONDICION_EXENTO}:
        return "exento_no_gravado"
    return None


__all__ = [
    "VERSION_CONSOLIDACION",
    "MOTIVO_ALTA_POR_PROGRAMA",
    "MOTIVO_RECHAZO_FIRME",
    "MOTIVO_SIN_LETRA",
    "MOTIVO_ALERTA_PENDIENTE",
    "MOTIVO_AMBIGUO",
    "MOTIVO_AGENTE",
    "MOTIVO_SIN_CLASIFICACION",
    "Consolidacion",
    "es_certeza_alta_por_programa",
    "consolidar",
]

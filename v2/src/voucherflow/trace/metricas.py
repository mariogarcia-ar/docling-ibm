"""Métricas del pipeline sobre el histórico persistido (F5/T-507, E-LIB-5).

**Fase**: F5 · **Tarea**: T-507 · **Épica**: E-LIB-5.

Las métricas del DoD de F5 (`06-estrategia-calidad.md` §5) calculadas sobre el
**`CaseRecord` persistido** de T-506, sin volver a correr el pipeline:

=========================  ==========================================  =========
métrica                    definición                                  objetivo
=========================  ==========================================  =========
% certeza alta por          casos `origen=programa` / total             creciente
programa                                                               (madurez)
% casos agente IA           casos `origen=agente_ia` / total            decreciente
                                                                       (R-09)
% rechazado                 casos con `estado=rechazado` / total        reportar
acuerdo VLM/LLM             campos donde ambas fuentes coinciden /      reportar
                            campos leídos por ambas
cobertura HITL              revisión obligatoria + muestreo efectivo   100% baja;
                                                                       tasa alta
tasa de alertas R7          casos con alerta de conflicto / total       reportar
=========================  ==========================================  =========

El Gherkin de E-LIB-5::

    Dado el procesamiento de un lote
    Cuando termina
    Entonces se producen métricas: documentos procesados, % certeza alta,
    % agente, % rechazados, latencias

Decisiones de diseño (T-507)
----------------------------
1. **Solo cuenta lo que puede contar, y declara el resto.** El histórico son los
   `CaseRecord` persistidos; no todo están consolidados. Cada métrica se calcula
   sobre su **propio denominador** y reporta `calculable`/`motivo`: un caso sin
   resultado consolidado no entra en el % de rechazados (no se sabe si lo es) y el
   reporte **lo dice** en vez de contarlo como "no rechazado". Un 0% silencioso
   por falta de datos es peor que un "no calculable" explícito.
2. **Si el lote es chico, se avisa.** Un `% agente` de 1/2 = 50% no significa lo
   mismo que 500/1000. Cada porcentaje lleva su `n` y el reporte marca
   `lote_chico` (por debajo de `MINIMO_LOTE_CONFIABLE`) para que nadie lea una
   tendencia donde hay dos casos.
3. **El acuerdo VLM/LLM es sobre los campos leídos por ambas**, no sobre el total
   del contrato. Un campo que solo leyó una fuente **no** es un desacuerdo ni una
   coincidencia: dividir por el total haría parecer mal acuerdo lo que en realidad
   es cobertura. Un campo donde las dos leyeron lo mismo cuenta como acuerdo;
   distinto, como desacuerdo; y los desacuerdos se listan (los formatos nuevos
   aparecen ahí, que es lo que la métrica tiene que hacer visible).
4. **La cobertura HITL separa obligatorios de muestreo.** La revisión obligatoria
   (certeza baja) tiene objetivo 100%; el muestreo de certeza alta tiene la tasa
   configurada. Mezclarlos daría un número que no se puede comparar con ninguno de
   los dos objetivos, así que se reportan por separado.
5. **Sin dependencias nuevas**: stdlib + los módulos del paquete.

Alcance honesto
---------------
- El **acuerdo VLM/LLM** se deriva de la evidencia por fuente del `CaseRecord`. Es
  un acuerdo **de lectura**, no de veredicto: no dice quién tiene razón (para eso
  está la revisión humana), dice dónde las dos fuentes se contradicen — que es la
  señal de "formato nuevo" de la tabla §5.
- El reporte lleva la **versión** de la librería, del contrato y del formato de
  traza (`06-estrategia-calidad.md` §6): una métrica sin la versión con que se
  produjo no es comparable con la siguiente corrida.
- El **diagnóstico del cliente de modelos** ante latencia o status inesperado es
  la otra mitad de E-LIB-5 y ya está implementado desde F0/T-005
  (`models/ollama.py::_diagnostico`); T-507 no lo duplica.

Referencias: `06-estrategia-calidad.md` §5/§6, Gherkin E-LIB-5, doc 03 §11
(`TRACE.md`), F5-subplan §3.7.
"""

from __future__ import annotations

from collections import Counter
from typing import Any, Iterable

from .. import __version__
from ..schemas.evidence import Certeza, Fuente, Origen
from ..schemas.result import SCHEMA_VERSION, CaseRecord
from .recorder import VERSION_TRAZA, CaseRecorder

#: Versión del reporte de métricas (va en el JSON para poder comparar corridas).
VERSION_METRICAS = "metricas-f5@1"

#: Por debajo de este número de casos, un porcentaje es anecdótico y se avisa.
MINIMO_LOTE_CONFIABLE = 20

#: Estados que cuentan como "rechazado" para la tasa de rechazo.
ESTADO_RECHAZADO = "rechazado"

#: Motivo estándar cuando una métrica no tiene denominador.
_MOTIVO_SIN_CASOS = "No hay casos sobre los que calcularla."
_MOTIVO_SIN_RESULTADOS = (
    "Ningún caso del histórico tiene resultado consolidado (los casos sin "
    "consolidar no se cuentan como 'no rechazados': no se sabe qué son)."
)
_MOTIVO_SIN_LECTURAS = (
    "No hay campos que las dos fuentes (VLM y LLM) hayan leído, así que no hay "
    "nada que comparar."
)


def _pct(numerador: int, denominador: int) -> float | None:
    """Porcentaje seguro: ``None`` cuando no hay denominador (no un 0% falso)."""
    if denominador <= 0:
        return None
    return round(100.0 * numerador / denominador, 2)


def _metrica(
    numerador: int,
    denominador: int,
    *,
    definicion: str,
    objetivo: str,
    motivo: str = _MOTIVO_SIN_CASOS,
    detalle: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Un bloque de métrica consistente: valor, n, definición, objetivo y por qué.

    ``valor`` es ``None`` (no 0) cuando no hay denominador: no saber es distinto
    de saber que es cero, y el reporte tiene que poder distinguirlo.
    """
    metrica: dict[str, Any] = {
        "valor_pct": _pct(numerador, denominador),
        "numerador": numerador,
        "denominador": denominador,
        "definicion": definicion,
        "objetivo": objetivo,
        "calculable": denominador > 0,
    }
    if denominador <= 0:
        metrica["motivo"] = motivo
    if detalle:
        metrica.update(detalle)
    return metrica


# ---------------------------------------------------------------------------
# Lectura del histórico
# ---------------------------------------------------------------------------


def casos_del_historico(recorder: CaseRecorder) -> list[CaseRecord]:
    """Los ``CaseRecord`` del directorio de salida del registrador (T-506).

    Se leen los **sidecars** (no el índice) porque las métricas de acuerdo VLM/LLM
    necesitan la evidencia por fuente, que el índice —derivado y deliberadamente
    mínimo— no lleva. El índice queda para las consultas de "qué casos hay".
    """
    return recorder.casos()


def _con_resultado(casos: Iterable[CaseRecord]) -> list[CaseRecord]:
    return [caso for caso in casos if caso.resultado is not None]


# ---------------------------------------------------------------------------
# Las métricas
# ---------------------------------------------------------------------------


def metricas_certidumbre(casos: Iterable[CaseRecord]) -> dict[str, Any]:
    """% de certeza alta por programa, % de agente IA y distribución de origen.

    Es el par de fuerzas del proyecto: `% certeza alta` debería **crecer** entre
    iteraciones y `% agente` **decrecer** (la casuística del agente se mueve a
    reglas, R-09). Se miden sobre los casos **con resultado**, que son los únicos
    donde la certeza existe.
    """
    casos = list(casos)
    con_resultado = _con_resultado(casos)
    total = len(con_resultado)

    por_origen: Counter[str] = Counter()
    for caso in con_resultado:
        origen = caso.resultado.origen
        por_origen[origen.value if origen else "sin_origen"] += 1

    altos = sum(
        1
        for caso in con_resultado
        if caso.resultado.certeza == Certeza.alta
        and caso.resultado.origen == Origen.programa
    )
    agente = por_origen[Origen.agente_ia.value]

    return {
        "casos_totales": len(casos),
        "casos_con_resultado": total,
        "certeza_alta_programa": _metrica(
            altos,
            total,
            definicion=(
                "Casos con certeza=alta **y** origen=programa sobre casos con "
                "resultado consolidado (la certeza se deriva de la etapa que decidió: "
                "se verifican las dos, no se asume la implicación del contrato)."
            ),
            objetivo="Creciente por iteración (objetivo de madurez).",
            motivo=_MOTIVO_SIN_RESULTADOS,
        ),
        "agente_ia": _metrica(
            agente,
            total,
            definicion="Casos con origen=agente_ia sobre casos con resultado consolidado.",
            objetivo="Decreciente por iteración (la casuística se mueve a reglas, R-09).",
            motivo=_MOTIVO_SIN_RESULTADOS,
        ),
        "por_origen": dict(sorted(por_origen.items())),
        "por_certeza": dict(
            sorted(
                Counter(
                    caso.resultado.certeza.value
                    for caso in con_resultado
                    if caso.resultado.certeza
                ).items()
            )
        ),
    }


def metricas_rechazo(casos: Iterable[CaseRecord]) -> dict[str, Any]:
    """% de rechazo y distribución de estados (`aprobado` / `rechazado` / `revision`).

    El rechazo es el **fast-fail** del pipeline: la tasa de rechazo del gate se
    reporta por lote (tabla §5), y su distribución de estados dice si el lote
    quedó mayormente resuelto o mayormente pendiente de revisión.
    """
    con_resultado = _con_resultado(list(casos))
    por_estado = Counter(caso.resultado.estado.value for caso in con_resultado)
    total = len(con_resultado)
    rechazados = por_estado[ESTADO_RECHAZADO]

    # La distinción `rechazado` vs `revision` es la tesis del proyecto: un rechazo
    # es una conclusión (certeza alta) y una revisión es una duda. Que el
    # "rechazado" caiga casi todo en `revision` significaría que el fast-fail no
    # está concluyendo, y la métrica tiene que dejarlo ver.
    rechazados_certeza_alta = sum(
        1
        for caso in con_resultado
        if caso.resultado.estado.value == ESTADO_RECHAZADO
        and caso.resultado.certeza == Certeza.alta
    )

    return {
        "rechazado": _metrica(
            rechazados,
            total,
            definicion="Casos con estado=rechazado sobre casos con resultado consolidado.",
            objetivo="Reportar por lote (tasa de rechazo del gate).",
            motivo=_MOTIVO_SIN_RESULTADOS,
        ),
        "rechazado_con_certeza_alta": {
            "casos": rechazados_certeza_alta,
            "de": rechazados,
            "nota": (
                "Un rechazo firme es certeza alta ('esto no es válido' es una "
                "conclusión, no una duda). Si este número no acompaña a la tasa de "
                "rechazo, el fast-fail no está concluyendo: está mandando a revisión."
            ),
        },
        "por_estado": dict(sorted(por_estado.items())),
        "por_tipo_comprobante": dict(
            sorted(
                Counter(
                    caso.resultado.tipo_comprobante
                    for caso in con_resultado
                    if caso.resultado.tipo_comprobante
                ).items()
            )
        ),
    }


def acuerdo_vlm_llm(casos: Iterable[CaseRecord], *, max_ejemplos: int = 5) -> dict[str, Any]:
    """% de campos donde VLM y LLM leen lo **mismo** (tabla §5).

    El denominador son los campos que **las dos** fuentes leyeron: un campo que
    solo leyó una no es acuerdo ni desacuerdo, es cobertura. Se comparan los
    valores **vigentes** de cada campo dentro del `CaseRecord` (los que la
    evidencia combinada dejó por fuente), y los desacuerdos se listan con sus
    valores: ahí es donde aparecen los formatos nuevos.

    Es un acuerdo **de lectura**, no de veredicto: no dice quién tiene razón.
    """
    coincidencias = 0
    comparados = 0
    desacuerdos: list[dict[str, Any]] = []

    for caso in casos:
        por_fuente = caso.evidencia_por_fuente
        vlm = por_fuente.get(Fuente.vlm.value)
        llm = por_fuente.get(Fuente.llm.value)
        if vlm is None or llm is None:
            continue
        for campo in sorted(set(vlm.campos) & set(llm.campos)):
            comparados += 1
            valor_vlm = vlm.campos[campo].valor
            valor_llm = llm.campos[campo].valor
            if valor_vlm == valor_llm:
                coincidencias += 1
            elif len(desacuerdos) < max_ejemplos:
                desacuerdos.append(
                    {
                        "documento_id": caso.documento_id,
                        "campo": campo,
                        "vlm": valor_vlm,
                        "llm": valor_llm,
                    }
                )

    metrica = _metrica(
        coincidencias,
        comparados,
        definicion="Campos donde VLM y LLM leyeron el mismo valor, sobre campos leídos por ambas.",
        objetivo="Reportar; los desacuerdos altos señalan formatos nuevos.",
        motivo=_MOTIVO_SIN_LECTURAS,
    )
    metrica["desacuerdos"] = desacuerdos
    return metrica


def cobertura_hitl(casos: Iterable[CaseRecord]) -> dict[str, Any]:
    """Cobertura de la revisión humana, separando obligatorios de muestreo (T-505).

    - **Obligatorios** (certeza baja): objetivo **100%**. Acá lo que se mide es la
      *revisión*, no el encolado: un caso encolado y todavía pendiente está
      cubierto por la cola pero no revisado, y el reporte lo distingue.
    - **Muestreo** (certeza alta): objetivo la **tasa configurada** — la métrica
      útil es cuántos de los muestreados se revisaron, no cuántos se encolaron
      (la selección ya es determinista por T-505).
    """
    casos = list(casos)
    obligatorios = muestreados = 0
    obligatorios_revisados = muestreados_revisados = 0
    pendientes = 0

    for caso in casos:
        resultado = caso.resultado
        if resultado is None:
            continue
        hitl = resultado.hitl
        if not hitl.requerido:
            continue
        revisado = hitl.estado == "revisado"
        if hitl.prioridad == "alta":
            obligatorios += 1
            obligatorios_revisados += int(revisado)
        else:
            muestreados += 1
            muestreados_revisados += int(revisado)
        pendientes += int(not revisado)

    return {
        "obligatorios": _metrica(
            obligatorios_revisados,
            obligatorios,
            definicion="Casos de certeza baja efectivamente revisados sobre los que requirieron revisión obligatoria.",
            objetivo="100% de los casos de certeza baja revisados.",
            motivo="No hubo casos de revisión obligatoria en el histórico.",
        ),
        "muestreados": _metrica(
            muestreados_revisados,
            muestreados,
            definicion="Casos del muestreo de auditoría efectivamente revisados sobre los muestreados.",
            objetivo="La tasa de muestreo configurada (ADR-004); acá se mide la revisión efectiva.",
            motivo="El muestreo no seleccionó casos en el histórico (tasa 0 o lote chico).",
        ),
        "pendientes": pendientes,
    }


def tasa_alertas(casos: Iterable[CaseRecord]) -> dict[str, Any]:
    """% de casos con alerta de conflicto (R7) — tabla §5.

    Las alertas viven en el bloque `conclusion` de la traza que T-501/T-503
    dejaron en el resultado: se cuentan casos **con al menos una alerta**, que es
    lo que la métrica quiere (una tasa de casos, no de alertas).
    """
    casos = list(casos)
    con_resultado = _con_resultado(casos)
    con_alerta = 0
    motivos: Counter[str] = Counter()

    for caso in con_resultado:
        bloque = caso.resultado.trazabilidad.get("conclusion")
        alertas = bloque.get("alertas") if isinstance(bloque, dict) else None
        if alertas:
            con_alerta += 1
            for alerta in alertas:
                if isinstance(alerta, dict):
                    motivos[str(alerta.get("regla") or alerta.get("tipo") or "sin_id")] += 1

    return _metrica(
        con_alerta,
        len(con_resultado),
        definicion="Casos con al menos una alerta de conflicto sobre casos con resultado.",
        objetivo="Reportar; revisar falsos positivos.",
        motivo=_MOTIVO_SIN_RESULTADOS,
        detalle={"por_regla": dict(sorted(motivos.items()))},
    )


def metricas_de(casos: Iterable[CaseRecord], *, origen_historico: str | None = None) -> dict[str, Any]:
    """El **reporte de métricas** completo (lo que pide el Gherkin de E-LIB-5).

    Incluye documentos procesados y las cuatro métricas del DoD de F5 (% certeza
    alta, % agente, % rechazado, acuerdo VLM/LLM) más las de la tabla §5
    (cobertura HITL, tasa de alertas). Cada bloque dice su `n`, su objetivo y, si
    no se puede calcular, **por qué**.

    El reporte se versiona (librería + contrato + formato de traza) porque una
    métrica sin la versión con la que se produjo no es comparable con la próxima
    corrida (`06-estrategia-calidad.md` §6).
    """
    casos = list(casos)

    # Cuántos documentos distintos hay: el histórico es por documento (T-506), pero
    # se reporta explícito para que el "procesados" del Gherkin sea verificable.
    documentos = {caso.documento_id for caso in casos}

    reporte: dict[str, Any] = {
        "version": VERSION_METRICAS,
        "version_libreria": __version__,
        "schema_version": SCHEMA_VERSION,
        "version_traza": VERSION_TRAZA,
        "documentos_procesados": len(documentos),
        "registros": len(casos),
        "minimo_lote_confiable": MINIMO_LOTE_CONFIABLE,
        "certidumbre": metricas_certidumbre(casos),
        "rechazo": metricas_rechazo(casos),
        "acuerdo_vlm_llm": acuerdo_vlm_llm(casos),
        "cobertura_hitl": cobertura_hitl(casos),
        "alertas": tasa_alertas(casos),
    }

    con_resultado = len(_con_resultado(casos))
    reporte["lote_chico"] = con_resultado < MINIMO_LOTE_CONFIABLE
    if reporte["lote_chico"]:
        reporte["aviso_lote_chico"] = (
            f"Hay {con_resultado} caso(s) con resultado consolidado (< "
            f"{MINIMO_LOTE_CONFIABLE}): los porcentajes son anecdóticos. No leer "
            "tendencias donde todavía hay pocos casos (06-estrategia-calidad.md §5)."
        )
    if origen_historico:
        reporte["origen_historico"] = str(origen_historico)

    return reporte


def metricas_del_recorder(recorder: CaseRecorder) -> dict[str, Any]:
    """Atajo: las métricas del histórico de un `CaseRecorder` (T-506).

    Es la vía normal — el reporte se calcula sobre lo que el registrador persistió::

        recorder = CaseRecorder("salida/cases")
        reporte = metricas_del_recorder(recorder)
        reporte["certidumbre"]["certeza_alta_programa"]["valor_pct"]
    """
    return metricas_de(casos_del_historico(recorder), origen_historico=recorder.dir_salida)


def resumen_legible(reporte: dict[str, Any]) -> list[tuple[str, str]]:
    """El reporte como filas `(métrica, valor)` para imprimir en el script.

    Es una vista, no un cálculo: no agrega ni recalcula nada, solo formatea lo que
    el reporte ya dice (incluido el "no calculable" con su motivo).
    """
    def _fmt(bloque: dict[str, Any]) -> str:
        if not bloque.get("calculable"):
            return f"n/d ({bloque.get('motivo', 'sin datos')})"
        return f"{bloque['valor_pct']}% ({bloque['numerador']}/{bloque['denominador']})"

    certidumbre = reporte["certidumbre"]
    return [
        ("documentos procesados", str(reporte["documentos_procesados"])),
        ("% certeza alta (programa)", _fmt(certidumbre["certeza_alta_programa"])),
        ("% agente IA", _fmt(certidumbre["agente_ia"])),
        ("% rechazado", _fmt(reporte["rechazo"]["rechazado"])),
        ("acuerdo VLM/LLM", _fmt(reporte["acuerdo_vlm_llm"])),
        ("cobertura HITL (obligatorios)", _fmt(reporte["cobertura_hitl"]["obligatorios"])),
        ("cobertura HITL (muestreo)", _fmt(reporte["cobertura_hitl"]["muestreados"])),
        ("tasa de alertas (R7)", _fmt(reporte["alertas"])),
    ]


__all__ = [
    "VERSION_METRICAS",
    "MINIMO_LOTE_CONFIABLE",
    "ESTADO_RECHAZADO",
    "metricas_de",
    "metricas_del_recorder",
    "metricas_certidumbre",
    "metricas_rechazo",
    "acuerdo_vlm_llm",
    "cobertura_hitl",
    "tasa_alertas",
    "casos_del_historico",
    "resumen_legible",
]

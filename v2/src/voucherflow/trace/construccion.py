"""Construcción del ``CaseRecord`` a partir de la corrida (F5/T-506, E-CONC-5).

**Fase**: F5 · **Tarea**: T-506 · **Épica**: E-CONC-5 · **ADR-005**.

:mod:`voucherflow.trace.recorder` persiste un ``CaseRecord``; este módulo lo
**construye** a partir de lo que las etapas ya produjeron (la
``CombinedEvidence`` de F4 con su decisión de F5, más el ``VoucherResult``
consolidado). Separa dos responsabilidades que conviene no mezclar: armar el
registro auditable y guardarlo.

Por qué un constructor y no "que cada etapa escriba su parte"
-----------------------------------------------------------
El ``CaseRecord`` es un **resumen de la corrida**, no un estado que las etapas
van mutando. Construirlo al final, desde los artefactos finales, tiene tres
ventajas:

1. **Nada se inventa y nada se pierde por olvido.** Todo lo que entra sale de un
   artefacto que existe (la evidencia, la decisión, el resultado, la traza). Si
   un dato no está, el campo viaja vacío — no se rellena con una suposición.
2. **Es determinístico y testeable sin red.** Construir el registro no ejecuta
   reglas ni modelos: es una proyección.
3. **La auditoría es verificable de punta a punta** (el Gherkin de E-CONC-5):
   dado el ``CaseRecord`` se puede responder versión de prompt, modelos,
   evidencia por fuente, reglas disparadas y quién decidió — sin volver a correr
   nada.

Regla de oro
------------
**Se registra lo que pasó; no se completa lo que falta.** Si la corrida no usó
agente, no hay modelo de agente. Si una fuente no expuso su versión de prompt, el
campo queda sin esa clave. Un ``CaseRecord`` con huecos es auditable; uno con
datos inventados es peor que ninguno, porque miente con apariencia de rigor.

Sobre la reconstrucción de la evidencia por fuente
-------------------------------------------------
La ``CombinedEvidence`` de F4 guarda, **por campo**, la lectura de cada fuente
(``CampoCombinado.vlm``/``llm``/``programa``/``arca``/``hitl``), y cada lectura
lleva su ``meta`` (modelo y versión de prompt). Eso alcanza para reconstruir la
evidencia por fuente **de los campos leídos**, que es lo que la auditoría
necesita.

Lo que *no* alcanza es el nivel de **fuente**: ``valida``,
``SourceEvidence.reglas_aplicadas`` y ``debilidades`` viven en la pasada 1 (F4)
y no viajan en la evidencia combinada. Por eso:

- si el llamador **tiene** las ``SourceEvidence`` originales, las pasa por
  ``fuentes=`` y se persisten **exactas**;
- si no las tiene, la reconstrucción desde ``campos`` es correcta en las lecturas
  y deja la trazabilidad de nivel de fuente declarada como reconstruida (el
  ``detalle`` de la etapa lo dice), en vez de aparentar una fidelidad que no
  tiene.

Referencias: ADR-005, Gherkin E-CONC-5, doc 03 §9/§11, F5-subplan §3.6.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from typing import Any

from ..schemas.evidence import CombinedEvidence, Fuente, SourceEvidence
from ..schemas.result import CaseRecord, RegistroEtapa, SCHEMA_VERSION, VoucherResult

#: Etapa que representa la **conclusión** (donde vive la decisión del caso).
ETAPA_CONCLUSION = "conclusion"

#: Fuentes de lectura cuyo ``meta`` trae el modelo que las produjo.
_FUENTES_CON_MODELO = (Fuente.vlm, Fuente.llm)

#: Mapeo fuente → rol de modelo (lo que espera ``modelo_por_etapa``).
_ROL_POR_FUENTE = {
    Fuente.vlm: "vlm",
    Fuente.llm: "llm",
}

#: Bloques de ``trazabilidad`` que pertenecen a la etapa de conclusión (F5).
#: Se copian al ``detalle`` del registro para que el sidecar sea
#: **autosuficiente**: un auditor no debería tener que volver a correr el
#: pipeline para ver qué reglas se evaluaron o por qué.
BLOQUES_CONCLUSION = (
    "conclusion",
    "consolidacion",
    "gaps",
    "busqueda_evidencia_adicional",
    "combinacion_adicional",
    "agente",
    "hitl",
)

#: Nota de trazabilidad que declara cómo se armó la evidencia por fuente.
NOTA_EVIDENCIA_RECONSTRUIDA = (
    "La evidencia por fuente se reconstruyó desde la evidencia combinada: las "
    "lecturas por campo son exactas (salen de `CampoCombinado`), pero el nivel de "
    "fuente (`valida`, `reglas_aplicadas`, `debilidades`) no viaja en la "
    "evidencia combinada y por eso queda en su valor neutro. Para persistirlo "
    "exacto, pasar las `SourceEvidence` de la corrida por `fuentes=`."
)
NOTA_EVIDENCIA_DIRECTA = (
    "La evidencia por fuente se persistió tal como la produjo la corrida "
    "(`SourceEvidence` completas: `valida`, `reglas_aplicadas`, `debilidades`)."
)


# ---------------------------------------------------------------------------
# Proyecciones de la corrida
# ---------------------------------------------------------------------------


def _valor_vigente(evidencia: CombinedEvidence) -> dict[str, Any]:
    """``campo -> valor`` vigente del caso (para el ``detalle`` de la etapa).

    Es el atajo operativo de T-404 (``CampoCombinado.valor``): lo que las reglas
    cruzadas evaluaron. Se incluye en la traza porque es lo que hace legible el
    veredicto sin abrir la evidencia completa campo por campo.
    """
    return {
        campo: combinado.valor
        for campo, combinado in evidencia.campos.items()
        if combinado.fuente is not None
    }


def _fuente_de_campo(evidencia: CombinedEvidence) -> dict[str, str | None]:
    """``campo -> fuente responsable`` (quién sostiene el valor vigente)."""
    return {
        campo: (combinado.fuente.value if combinado.fuente else None)
        for campo, combinado in evidencia.campos.items()
    }


def _evidencia_desde_campos(evidencia: CombinedEvidence) -> dict[str, SourceEvidence]:
    """Reconstruye la evidencia por fuente desde la combinada (ver módulo).

    Cada ``EvidenceField`` ya está en ``CampoCombinado`` con su ``fuente``, su
    ``meta`` y su ``fragmento_sustento``; agrupar por fuente los devuelve a su
    forma original. Los campos que ninguna fuente declaró no aparecen (no se crea
    una lectura nula: no declarar un campo no es declararlo vacío).
    """
    por_fuente: dict[Fuente, dict[str, Any]] = {}
    for combinado in evidencia.campos.values():
        for fuente in (
            Fuente.vlm,
            Fuente.llm,
            Fuente.programa,
            Fuente.arca,
            Fuente.hitl,
        ):
            lectura = getattr(combinado, fuente.value, None)
            if lectura is not None:
                por_fuente.setdefault(fuente, {})[lectura.campo] = lectura

    return {
        fuente.value: SourceEvidence(fuente=fuente, campos=campos)
        for fuente, campos in por_fuente.items()
    }


def evidencia_por_fuente(
    evidencia: CombinedEvidence,
    *,
    fuentes: Iterable[SourceEvidence] | None = None,
) -> dict[str, SourceEvidence]:
    """Evidencia por fuente a persistir (exacta si se pasan las originales).

    Argumentos:
        evidencia: la evidencia combinada del caso.
        fuentes: las ``SourceEvidence`` de la corrida, si el llamador las tiene.
            Tienen **precedencia** sobre la reconstrucción porque conservan el
            nivel de fuente (`valida`, `reglas_aplicadas`, `debilidades`).

    Devuelve:
        ``fuente -> SourceEvidence``. Nunca ``None``: la ausencia de fuentes se
        representa como un dict vacío, no como un registro inválido.
    """
    if fuentes is not None:
        return {fuente.fuente.value: fuente for fuente in fuentes}
    return _evidencia_desde_campos(evidencia)


def modelos_y_prompts(
    evidencia: CombinedEvidence,
    *,
    fuentes: Iterable[SourceEvidence] | None = None,
    resultado: VoucherResult | None = None,
) -> tuple[dict[str, str], dict[str, str]]:
    """``(modelo_por_etapa, version_prompt)`` derivados de lo que la corrida usó.

    Se lee el ``meta`` de cada lectura (que es donde la extracción de F4 registra
    ``modelo`` y ``version_prompt``, ver ``nueva_meta``) y, para el agente, el
    bloque ``agente`` de la traza del resultado (que es donde T-504 registra su
    modelo y su prompt).

    Lo que **no** aparece no se agrega: si la corrida resolvió el caso por
    programa, ``modelo_por_etapa`` no lleva una clave ``agente`` vacía ni un
    modelo que no se llamó.
    """
    modelo_por_etapa: dict[str, str] = {}
    version_prompt: dict[str, str] = {}

    lecturas: dict[Fuente, dict[str, Any]] = {}
    for combinado in evidencia.campos.values():
        for fuente in _FUENTES_CON_MODELO:
            lectura = getattr(combinado, fuente.value, None)
            if lectura is not None:
                lecturas.setdefault(fuente, lectura.meta or {})

    for fuente, meta in lecturas.items():
        rol = _ROL_POR_FUENTE[fuente]
        if meta.get("modelo"):
            modelo_por_etapa[rol] = meta["modelo"]
        if meta.get("version_prompt"):
            version_prompt[fuente.value] = meta["version_prompt"]

    # Si se pasaron las fuentes originales, su `meta` a nivel de fuente manda.
    if fuentes is not None:
        for fuente in fuentes:
            for campo in (fuente.campos or {}).values():
                meta = campo.meta or {}
                if campo.fuente in _ROL_POR_FUENTE and meta.get("modelo"):
                    modelo_por_etapa[_ROL_POR_FUENTE[campo.fuente]] = meta["modelo"]
                if meta.get("version_prompt"):
                    version_prompt[campo.fuente.value] = meta["version_prompt"]

    if resultado is not None:
        agente = resultado.trazabilidad.get("agente")
        if isinstance(agente, Mapping) and agente.get("escalado"):
            if agente.get("modelo"):
                modelo_por_etapa["agente"] = agente["modelo"]
            if agente.get("version_prompt"):
                version_prompt["agente"] = agente["version_prompt"]

    return modelo_por_etapa, version_prompt


def reglas_disparadas(
    evidencia: CombinedEvidence,
    *,
    fuentes: Iterable[SourceEvidence] | None = None,
    resultado: VoucherResult | None = None,
) -> list[str]:
    """Ids de las reglas que se **dispararon** en el caso, sin repetir.

    Se unen las tres procedencias reales, en orden de evaluación:

    1. las reglas raw de la pasada 1, por fuente (`SourceEvidence.reglas_aplicadas`,
       presente solo si el llamador pasó las fuentes originales);
    2. las reglas de la pasada 2 que concluyeron el caso
       (`CombinedEvidence.decision.reglas_aplicadas`, T-501);
    3. el bloque `conclusion` de la traza del resultado (donde T-501 publica las
       reglas de la **casuística**, aunque el caso haya quedado ambiguo).

    El orden se conserva (primero aparición) y se deduplica: la auditoría quiere
    saber **qué** reglas corrió, no cuántas veces se nombró cada una.
    """
    vistas: list[str] = []

    def _agregar(ids: Iterable[Any]) -> None:
        for identificador in ids:
            texto = str(identificador)
            if texto and texto not in vistas:
                vistas.append(texto)

    if fuentes is not None:
        for fuente in fuentes:
            _agregar(fuente.reglas_aplicadas or [])
    if evidencia.decision is not None:
        _agregar(evidencia.decision.reglas_aplicadas or [])
    if resultado is not None:
        bloque = resultado.trazabilidad.get("conclusion")
        if isinstance(bloque, Mapping):
            _agregar(bloque.get("reglas_aplicadas") or [])
            por_familia = bloque.get("reglas_por_familia")
            if isinstance(por_familia, Mapping):
                for ids in por_familia.values():
                    _agregar(ids or [])

    return vistas


def detalles_de_conclusion(
    evidencia: CombinedEvidence,
    *,
    resultado: VoucherResult | None = None,
) -> dict[str, Any]:
    """El ``detalle`` de la etapa de conclusión: lo que hace el sidecar autosuficiente.

    Incluye los bloques de traza de la etapa (conclusión, consolidación, gaps,
    búsqueda, agente, HITL), el valor vigente por campo y quién lo sostiene.
    Nada se recalcula: se copia lo que las etapas dejaron escrito.
    """
    detalle: dict[str, Any] = {
        "valor_vigente_por_campo": _valor_vigente(evidencia),
        "fuente_por_campo": _fuente_de_campo(evidencia),
    }
    if resultado is not None:
        for bloque in BLOQUES_CONCLUSION:
            if bloque in resultado.trazabilidad:
                detalle[bloque] = resultado.trazabilidad[bloque]
    if evidencia.trazabilidad:
        # La combinación (T-404) vive en la traza de la evidencia, no del resultado.
        detalle["combinacion"] = evidencia.trazabilidad.get("combinacion")
    return {clave: valor for clave, valor in detalle.items() if valor is not None}


# ---------------------------------------------------------------------------
# El constructor
# ---------------------------------------------------------------------------


def construir_case_record(
    evidencia: CombinedEvidence,
    *,
    resultado: VoucherResult | None = None,
    archivo: str | None = None,
    fuentes: Iterable[SourceEvidence] | None = None,
    etapa: str = ETAPA_CONCLUSION,
) -> CaseRecord:
    """Arma el ``CaseRecord`` auditable de un caso (E-CONC-5 / ADR-005).

    Es una **proyección** de la corrida: no ejecuta reglas ni modelos, solo
    reúne lo que las etapas dejaron en la evidencia, en la decisión y en el
    resultado.

    Argumentos:
        evidencia: la ``CombinedEvidence`` del caso (F4), con la decisión
            adjunta si el código concluyó (F5/T-501).
        resultado: el ``VoucherResult`` consolidado, si existe. De acá salen el
            estado final, la certeza, el ``origen`` (quién decidió) y los bloques
            de traza de la conclusión/HITL.
        archivo: ruta del archivo de origen (referencial, para el auditor).
        fuentes: las ``SourceEvidence`` de la corrida, si el llamador las tiene
            (su ``valida``/``reglas_aplicadas``/``debilidades`` se persiste
            exacto; ver el módulo sobre la reconstrucción).
        etapa: nombre de la etapa que registra la conclusión.

    Devuelve:
        Un ``CaseRecord`` con identidad, versiones, evidencia por fuente, reglas
        disparadas, quién decidió, la etapa de conclusión y el resultado.

    Sobre ``quien_decidio``: es el **origen del resultado** (o el de la decisión
    si todavía no hay resultado). La revisión humana del HITL **no** lo cambia:
    revisar un caso no es decidirlo. Una decisión originada en el humano se
    registra como ``Origen.hitl`` cuando su corrección **se aplica** al
    resultado — y eso es explícito, no un efecto colateral de haber revisado.
    """
    fuentes_lista = list(fuentes) if fuentes is not None else None
    evidencia_fuentes = evidencia_por_fuente(evidencia, fuentes=fuentes_lista)
    modelos, prompts = modelos_y_prompts(
        evidencia, fuentes=fuentes_lista, resultado=resultado
    )
    reglas = reglas_disparadas(evidencia, fuentes=fuentes_lista, resultado=resultado)

    quien_decidio = None
    if resultado is not None:
        quien_decidio = resultado.origen
    elif evidencia.decision is not None:
        quien_decidio = evidencia.decision.origen

    detalle = detalles_de_conclusion(evidencia, resultado=resultado)
    detalle["construccion"] = {
        "evidencia_por_fuente": (
            "directa" if fuentes_lista is not None else "reconstruida_desde_campos"
        ),
        "nota": (
            NOTA_EVIDENCIA_DIRECTA
            if fuentes_lista is not None
            else NOTA_EVIDENCIA_RECONSTRUIDA
        ),
        "tiene_resultado": resultado is not None,
        "tiene_decision": evidencia.decision is not None,
    }

    registro = RegistroEtapa(
        etapa=etapa,
        modelo=modelos.get("agente"),
        version_prompt=prompts.get("agente"),
        evidencia_por_fuente=evidencia_fuentes,
        reglas_disparadas=reglas,
        detalle=detalle,
    )

    return CaseRecord(
        documento_id=evidencia.documento_id,
        archivo=archivo,
        schema_version=SCHEMA_VERSION,
        version_prompt=prompts,
        modelo_por_etapa=modelos,
        evidencia_por_fuente=evidencia_fuentes,
        reglas_disparadas=reglas,
        quien_decidio=quien_decidio,
        etapas=[registro],
        resultado=resultado,
    )


def resumen_case_record(caso: CaseRecord) -> dict[str, Any]:
    """Lo que el ``CaseRecord`` permite responder de un caso (para el reporte).

    Es la verificación ejecutable del Gherkin de E-CONC-5: los cinco datos que
    una auditoría pide —versión de prompt, modelo, evidencia por fuente, reglas
    disparadas y quién decidió— leídos **del registro**, sin volver a correr el
    pipeline. Sirve para el reporte del script y para comprobar que el registro
    es autosuficiente.
    """
    return {
        "documento_id": caso.documento_id,
        "schema_version": caso.schema_version,
        "archivo": caso.archivo,
        "timestamp": caso.timestamp,
        "version_prompt": dict(caso.version_prompt),
        "modelo_por_etapa": dict(caso.modelo_por_etapa),
        "evidencia_por_fuente": {
            fuente: {
                "valida": evidencia.valida,
                "campos": sorted(evidencia.campos),
                "n_campos": len(evidencia.campos),
                "reglas_aplicadas": list(evidencia.reglas_aplicadas),
                "debilidades": list(evidencia.debilidades),
            }
            for fuente, evidencia in caso.evidencia_por_fuente.items()
        },
        "reglas_disparadas": list(caso.reglas_disparadas),
        "quien_decidio": caso.quien_decidio.value if caso.quien_decidio else None,
        "etapas": [registro.etapa for registro in caso.etapas],
        "resultado": (
            {
                "estado": caso.resultado.estado.value,
                "certeza": caso.resultado.certeza.value if caso.resultado.certeza else None,
                "origen": caso.resultado.origen.value if caso.resultado.origen else None,
                "tipo_comprobante": caso.resultado.tipo_comprobante,
                "hitl": {
                    "requerido": caso.resultado.hitl.requerido,
                    "prioridad": caso.resultado.hitl.prioridad,
                    "estado": caso.resultado.hitl.estado,
                },
            }
            if caso.resultado is not None
            else None
        ),
    }


__all__ = [
    "ETAPA_CONCLUSION",
    "BLOQUES_CONCLUSION",
    "NOTA_EVIDENCIA_RECONSTRUIDA",
    "NOTA_EVIDENCIA_DIRECTA",
    "construir_case_record",
    "evidencia_por_fuente",
    "modelos_y_prompts",
    "reglas_disparadas",
    "detalles_de_conclusion",
    "resumen_case_record",
]

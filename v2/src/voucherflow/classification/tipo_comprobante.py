"""Módulo ``classification`` — tipo/letra (F3/T-301) + cadena contable (F3/T-304).

**Fase**: F3 (refactor clasificación). F0 dejó el **esqueleto** con los contratos
``TipoComprobanteResult`` y ``ClasificacionContableResult``. **T-301** implementa
``clasificar_tipo_comprobante()``: la decisión de la letra deja de vivir en el
prompt y pasa a un **motor de reglas en código** (ADR-006) sobre el contexto
tipado de ``rules/contexto.py`` y las reglas R1-R7 de
``rules/tipo_comprobante_rules.py``. ``clasificar_contable()`` sigue siendo
``NotImplementedError`` (lo implementa **T-304**).

Responsabilidades (doc 03 §4.3, `CLAS.md`): decidir el tipo/letra de
comprobante (A/B/C/M/E, 090/099) cruzando la condición fiscal esperada por
negocio con la letra detectada en el documento, y resolver la cadena contable
01 → 02 → 03 (centro de costo → macro categoría → concepto/código).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Iterable, Mapping

from ..rules.contexto import ContextoTipoComprobante
from ..rules.tipo_comprobante_rules import (
    construir_alerta,
    evaluar_conflicto,
    letra_de_regla,
    regla_lectura_resolutoria,
    regla_negocio_resolutoria,
    reglas_lectura_disparadas,
    reglas_negocio_disparadas,
)


@dataclass
class TipoComprobanteResult:
    """Salida de la clasificación de tipo/letra (E-CLAS-1).

    ``letra``: A/B/C/M/E/090/099 (o ``None`` si no se concluye).
    ``certeza`` y ``origen``: regla de oro (programa → alta).
    ``reglas_aplicadas``: ids R1..R7 disparadas (trazabilidad E-CONC-5).

    Campos **agregados en F3/T-301** (todos con default: el contrato de F0
    sigue siendo construible tal cual, F3-subplan §4):

    - ``tipo_esperado_por_negocio`` / ``tipo_detectado_por_documento``: las dos
      entradas del cruce (contrato del prompt WIP §``output_esperado``); útiles
      para auditoría y para distinguir "solo negocio" de "solo documento".
    - ``coincide_negocio_vs_documento``: ``True`` solo cuando ambas entradas
      existen y son iguales (la discrepancia con evidencia documental queda
      explícita).
    - ``campos_desconocidos``: campos que faltaron para poder concluir con
      certeza alta (contrato del WIP); vacío cuando la certeza es alta.
    - ``detalle``: traza de la decisión (reglas resolutorias y reglas
      disparadas por familia, contexto de entrada), para ``CaseRecord``/
      auditoría (E-CONC-5).
    """

    letra: str | None = None
    certeza: str | None = None  # alta | baja
    origen: str | None = None  # programa | agente_ia | hitl
    candidatos_descartados: list[str] = field(default_factory=list)
    candidatos_restantes: list[str] = field(default_factory=list)
    reglas_aplicadas: list[str] = field(default_factory=list)
    alertas: list[dict[str, Any]] = field(default_factory=list)

    # --- Trazabilidad agregada en F3/T-301 (aditiva, con default) ---
    tipo_esperado_por_negocio: str | None = None
    tipo_detectado_por_documento: str | None = None
    coincide_negocio_vs_documento: bool = False
    campos_desconocidos: list[str] = field(default_factory=list)
    detalle: dict[str, Any] = field(default_factory=dict)


@dataclass
class ClasificacionContableResult:
    """Salida de la cadena contable 01 → 02 → 03 (E-CLAS-2)."""

    centro_costo: str | None = None
    macro_categoria: str | None = None
    concepto: str | None = None
    codigo: str | None = None
    reglas_aplicadas: list[str] = field(default_factory=list)


def clasificar_tipo_comprobante(
    contexto: "ContextoTipoComprobante | Mapping[str, Any]",
    *,
    preferencia_letra: str = "documento",
) -> TipoComprobanteResult:
    """Decide el tipo/letra cruzando negocio (R1-R3) y lectura (R4-R6) — F3/T-301.

    Portado del orden de evaluación del prompt WIP
    (``prompts/wip/deteccion_tipo_factura.yaml`` §``orden_de_evaluacion``, ver
    F3-subplan §5):

    1. **R3 (exportación)** tiene prioridad máxima: si aplica → ``E`` con
       certeza ``alta`` (la ley comprobada manda; no hay nada que cruzar).
    2. **R1/R2A/R2B** resuelven el ``tipo_esperado_por_negocio``.
    3. **R4 → R5 → R6** (cascada) resuelven el ``tipo_detectado_por_documento``.
    4. **Coincidencia** (ambos existen e iguales) → letra final = detectada,
       certeza ``alta``.
    5. **Discrepancia** → se dispara R7 (si corresponde), se registra la alerta
       y la letra final sigue :paramref:`preferencia_letra`; certeza ``baja``.
    6. **Casos parciales** (solo negocio / solo documento / nada) → se conserva
       la letra disponible, certeza ``baja`` y ``campos_desconocidos`` refleja
       lo que faltó (contrato del WIP: "si faltan datos críticos, confianza
       baja").

    ``origen`` es siempre ``"programa"`` (motor determinístico en código,
    ADR-006). La certeza ``alta`` **solo** se alcanza en coincidencia o R3; en
    cualquier otro caso es ``baja`` (regla de oro del contrato: ``programa`` →
    alta solo cuando hay evidencia concluyente).

    Trazabilidad (``detalle``, E-CONC-5): se registra la traza de reglas
    **resolutorias** (``reglas_resolutorias``) y la traza **completa** de reglas
    disparadas por familia (``reglas_disparadas``: negocio/lectura), que puede
    incluir reglas pisadas (p. ej. R1 cuando R3 resuelve). Esta versión T-301
    deriva ``candidatos_descartados``/``candidatos_restantes`` de las letras que
    produjeron las reglas disparadas (simple y honesta); **T-303** la
    enriquecerá con las reglas *raw* por fuente.

    Argumentos:
        contexto: :class:`~voucherflow.rules.contexto.ContextoTipoComprobante`
            (recomendado) o un ``dict``/mapping con el shape plano o anidado del
            WIP (se construye el contexto con ``desde_dict``).
        preferencia_letra: ``"documento"`` (default, la decisión de ``11.1`` de
            v1: si la letra detectada contradice la esperada, manda la
            detectada) o ``"negocio"`` (el WIP ``deteccion_tipo_factura.yaml``:
            "la ley manda sobre el papel"). Ver F3-subplan §2.4.

    Lanza:
        ``ValueError`` si :paramref:`preferencia_letra` no es un valor válido.
        ``TypeError`` si :paramref:`contexto` no es un contexto ni un mapping.

    Devuelve:
        :class:`TipoComprobanteResult` con ``candidatos_descartados``,
        ``candidatos_restantes``, ``reglas_aplicadas`` y ``alertas`` trazables.
    """
    if preferencia_letra not in ("documento", "negocio"):
        raise ValueError(
            "preferencia_letra debe ser 'documento' o 'negocio'; "
            f"recibido: {preferencia_letra!r} (F3-subplan §2.4)."
        )

    ctx = _como_contexto(contexto)

    # Traza completa de reglas disparadas por familia (incluye las pisadas, p.
    # ej. R1 cuando R3 resuelve). Es el insumo que T-303 usará para enriquecer
    # candidatos y detalle con las reglas *raw* por fuente.
    ids_negocio = reglas_negocio_disparadas(ctx)
    ids_lectura = reglas_lectura_disparadas(ctx)

    regla_negocio = regla_negocio_resolutoria(ctx)
    esperado = letra_de_regla(regla_negocio, ctx) if regla_negocio is not None else None
    regla_lectura = regla_lectura_resolutoria(ctx)
    detectado = letra_de_regla(regla_lectura, ctx) if regla_lectura is not None else None

    traza: dict[str, list[str]] = {"negocio": ids_negocio, "lectura": ids_lectura}

    # -- Paso 1: R3 (exportación) pisa todo ---------------------------------
    # ``regla_negocio_resolutoria`` devuelve la regla de **mayor prioridad**
    # disparada; si es R3, la exportación gana y no hay cruce con la lectura
    # (orden del WIP §``orden_de_evaluacion`` paso 1: "si aplica, resultado = E,
    # fin"). La letra detectada no se evalúa ni entra en ``reglas_aplicadas``.
    if regla_negocio is not None and regla_negocio.id == "R3":
        return _resultado_exportacion(ctx, esperado or "E", [regla_negocio.id], traza)

    # ``reglas_aplicadas``: ids de las reglas que **resolvieron** (negocio →
    # lectura → conflicto), en orden de evaluación y sin duplicados.
    aplicadas: list[str] = []
    if regla_negocio is not None:
        aplicadas.append(regla_negocio.id)
    if regla_lectura is not None:
        aplicadas.append(regla_lectura.id)

    alertas: list[dict[str, Any]] = []

    # -- Paso 2: coincidencia ----------------------------------------------
    if esperado is not None and detectado is not None and esperado == detectado:
        return _resultado_coincidencia(ctx, esperado, detectado, aplicadas, traza)

    # -- Paso 3: discrepancia → R7 + alerta --------------------------------
    if esperado is not None and detectado is not None and esperado != detectado:
        for regla in evaluar_conflicto(ctx):
            alertas.append(construir_alerta(regla, ctx))
            aplicadas.append(regla.id)
        letra = detectado if preferencia_letra == "documento" else esperado
        return _resultado_discrepancia(ctx, letra, esperado, detectado, aplicadas, alertas, traza)

    # -- Paso 4: solo negocio / solo documento / nada ----------------------
    return _resultado_parcial(ctx, esperado, detectado, aplicadas, alertas, traza)


def _como_contexto(
    contexto: "ContextoTipoComprobante | Mapping[str, Any]",
) -> ContextoTipoComprobante:
    """Normaliza la entrada a ``ContextoTipoComprobante`` (tipado o dict).

    Se acepta un ``dict``/``Mapping`` (shape plano o anidado del WIP) para que
    el llamador de T-302/T-303 no tenga que importar el dataclass; si el valor
    no es ninguno de los dos, se lanza ``TypeError`` explícito.
    """
    if isinstance(contexto, ContextoTipoComprobante):
        return contexto
    if isinstance(contexto, Mapping):
        return ContextoTipoComprobante.desde_dict(contexto)
    raise TypeError(
        "clasificar_tipo_comprobante() espera un ContextoTipoComprobante o un "
        f"mapping con la evidencia; recibido: {type(contexto).__name__} (F3/T-301)."
    )


def _candidatos(
    esperado: str | None, detectado: str | None, letra_final: str | None
) -> tuple[list[str], list[str]]:
    """Deriva ``candidatos_descartados`` y ``candidatos_restantes`` (T-301).

    Versión **simple y honesta**: los candidatos conocidos son las letras que
    produjeron las reglas disparadas (``esperado`` por negocio, ``detectado`` por
    lectura), no el vocabulario completo. Sobrevive como **restante** la letra
    final; el resto pasa a **descartados**.

    Ejemplo del enunciado: esperado ``A`` y detectado ``B`` con letra final
    ``A`` → ``descartados == ["B"]``, ``restantes == ["A"]``. Sin candidatos
    conocidos (ni negocio ni lectura resolvieron) ambas listas quedan vacías: no
    se inventan candidatos del vocabulario completo.

    T-303 enriquecerá este cálculo con las reglas *raw* por fuente y con los
    candidatos que el modelo descartó con sustento.
    """
    conocidos: list[str] = []
    for letra in (esperado, detectado, letra_final):
        if letra is not None and letra not in conocidos:
            conocidos.append(letra)
    if letra_final is None:
        return [], []
    descartados = [letra for letra in conocidos if letra != letra_final]
    return descartados, [letra_final]


def _detalle(
    ctx: ContextoTipoComprobante,
    criterio: str,
    reglas: Iterable[str],
    traza: Mapping[str, list[str]],
) -> dict[str, Any]:
    """Arma el ``detalle`` de auditoría (E-CONC-5) con la traza de reglas.

    Incluye las reglas que **resolvieron** (``reglas_resolutorias``) y la
    **traza completa** de reglas disparadas por familia (``reglas_disparadas``:
    ``negocio``/``lectura``), que puede contener reglas pisadas (p. ej. R1
    cuando R3 resuelve). T-303 usará esa traza para el detalle por fuente.
    """
    return {
        "criterio": criterio,
        "id_requisito": "T-301 (E-CLAS-1, doc 03 sección 7)",
        "reglas_resolutorias": _unicos(reglas),
        "reglas_disparadas": {
            "negocio": list(traza.get("negocio", [])),
            "lectura": list(traza.get("lectura", [])),
        },
        "contexto": ctx.como_dict(),
    }


def _resultado_exportacion(
    ctx: ContextoTipoComprobante,
    letra: str,
    reglas: list[str],
    traza: Mapping[str, list[str]],
) -> TipoComprobanteResult:
    """R3 disparó: tipo ``E`` con certeza ``alta`` (nada que cruzar)."""
    descartados, restantes = _candidatos(letra, None, letra)
    return TipoComprobanteResult(
        letra=letra,
        certeza="alta",
        origen="programa",
        candidatos_descartados=descartados,
        candidatos_restantes=restantes,
        reglas_aplicadas=_unicos(reglas),
        alertas=[],
        tipo_esperado_por_negocio=letra,
        tipo_detectado_por_documento=None,
        coincide_negocio_vs_documento=False,
        campos_desconocidos=[],
        detalle=_detalle(
            ctx,
            "R3 exportación: prioridad máxima, se resuelve sin cruzar con la lectura",
            reglas,
            traza,
        ),
    )


def _resultado_coincidencia(
    ctx: ContextoTipoComprobante,
    esperado: str,
    detectado: str,
    reglas: list[str],
    traza: Mapping[str, list[str]],
) -> TipoComprobanteResult:
    """Negocio y documento coinciden: letra final con certeza ``alta``."""
    descartados, restantes = _candidatos(esperado, detectado, detectado)
    return TipoComprobanteResult(
        letra=detectado,
        certeza="alta",
        origen="programa",
        candidatos_descartados=descartados,
        candidatos_restantes=restantes,
        reglas_aplicadas=_unicos(reglas),
        alertas=[],
        tipo_esperado_por_negocio=esperado,
        tipo_detectado_por_documento=detectado,
        coincide_negocio_vs_documento=True,
        campos_desconocidos=[],
        detalle=_detalle(
            ctx, "Coincidencia negocio vs. documento (certeza alta)", reglas, traza
        ),
    )


def _resultado_discrepancia(
    ctx: ContextoTipoComprobante,
    letra: str,
    esperado: str,
    detectado: str,
    reglas: list[str],
    alertas: list[dict[str, Any]],
    traza: Mapping[str, list[str]],
) -> TipoComprobanteResult:
    """Discrepancia: R7 (si aplica), letra según ``preferencia_letra``, certeza baja."""
    descartados, restantes = _candidatos(esperado, detectado, letra)
    return TipoComprobanteResult(
        letra=letra,
        certeza="baja",
        origen="programa",
        candidatos_descartados=descartados,
        candidatos_restantes=restantes,
        reglas_aplicadas=_unicos(reglas),
        alertas=alertas,
        tipo_esperado_por_negocio=esperado,
        tipo_detectado_por_documento=detectado,
        coincide_negocio_vs_documento=False,
        campos_desconocidos=[],
        detalle=_detalle(
            ctx,
            (
                "Discrepancia negocio vs. documento: se conserva la letra "
                f"'{letra}' por preferencia_letra (F3-subplan §2.4); la "
                "discrepancia queda documentada en las alertas (R7) y en candidatos."
            ),
            reglas,
            traza,
        ),
    )


def _resultado_parcial(
    ctx: ContextoTipoComprobante,
    esperado: str | None,
    detectado: str | None,
    reglas: list[str],
    alertas: list[dict[str, Any]],
    traza: Mapping[str, list[str]],
) -> TipoComprobanteResult:
    """Casos parciales: solo negocio, solo documento o sin datos (certeza baja)."""
    if esperado is not None:
        letra = esperado
        criterio = "Solo evidencia de negocio (sin letra detectada en el documento)"
    elif detectado is not None:
        letra = detectado
        criterio = "Solo evidencia de lectura (condición fiscal incompleta)"
    else:
        letra = None
        criterio = "Sin evidencia suficiente: ni negocio ni lectura resolvieron la letra"
    descartados, restantes = _candidatos(esperado, detectado, letra)
    return TipoComprobanteResult(
        letra=letra,
        certeza="baja",
        origen="programa",
        candidatos_descartados=descartados,
        candidatos_restantes=restantes,
        reglas_aplicadas=_unicos(reglas),
        alertas=alertas,
        tipo_esperado_por_negocio=esperado,
        tipo_detectado_por_documento=detectado,
        coincide_negocio_vs_documento=False,
        campos_desconocidos=ctx.campos_desconocidos(),
        detalle=_detalle(ctx, criterio, reglas, traza),
    )


def _unicos(valores: Iterable[str]) -> list[str]:
    """Devuelve los valores sin repetir, preservando el orden de aparición."""
    vistos: set[str] = set()
    salida: list[str] = []
    for valor in valores:
        if valor not in vistos:
            vistos.add(valor)
            salida.append(valor)
    return salida


def clasificar_contable(
    markdown: str, condicion_impositiva: str | None = None
) -> ClasificacionContableResult:
    """Resuelve la cadena contable 01 → 02 → 03 (F3).

    Esqueleto F0 — se implementa en F3 (T-304).
    """
    raise NotImplementedError("clasificar_contable(): se implementa en F3 (T-304).")


__all__ = ["TipoComprobanteResult", "ClasificacionContableResult", "clasificar_tipo_comprobante", "clasificar_contable"]

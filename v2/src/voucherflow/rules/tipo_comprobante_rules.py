"""Reglas R1-R7 del tipo/letra de comprobante (F3 / T-301, épica E-CLAS-1).

**Fase**: F3 — Clasificación. Este módulo **porta literalmente** las reglas del
prompt WIP ``prompts/wip/deteccion_tipo_factura.yaml`` (sección ``reglas`` y
``orden_de_evaluacion``) a un **motor de reglas en código** (ADR-006): cada
regla es una instancia declarativa de :class:`~voucherflow.rules.registry.Rule`
con su ``id``, ``prioridad``, ``condicion``, ``resultado``, ``tipo`` y
``detalle`` legible en español para auditoría (E-CONC-5). No hay ``if``
dispersos en la lógica de decisión: **la regla ES el dato declarativo**.

Tres registros separados (decisión cerrada §2.3 del F3-subplan)
--------------------------------------------------------------
- :data:`REGISTRO_NEGOCIO` — R1 (emisor Monotributo/Exento → C), R2A (emisor RI
  + receptor RI → A), R2B (emisor RI + receptor Monotributo/Exento/Consumidor
  Final → B) y R3 (receptor fuera de Argentina → E, prioridad máxima). Resuelve
  el ``tipo_esperado_por_negocio``.
- :data:`REGISTRO_LECTURA` — R4 (letra del recuadro que vio el VLM), R5 (regex
  sobre el texto de encabezado ``FACTURA\\s+([A-CME])|COMPROBANTE\\s+([A-CME])``)
  y R6 (inferencia por ``campos_totales`` con desempate por condición fiscal del
  emisor). Resuelven el ``tipo_detectado_por_documento`` **en cascada**
  R4 → R5 → R6 (las condiciones de R5/R6 incluyen explícitamente que las
  anteriores no dieron resultado, tal como dice el WIP).
- :data:`REGISTRO_CONFLICTO` — R7 (emisor RI ∧ receptor RI ∧ letra detectada B →
  alerta "comprobante inválido para crédito fiscal").

Prioridades (decisión de diseño T-301, coherente con el WIP y con el subplan §5)
-------------------------------------------------------------------------------
====================  =========
Regla                 prioridad
====================  =========
R3 (exportación)      0
R1 (mono/exento)      1
R2A / R2B             2
R4 (recuadro VLM)     10
R5 (regex texto)      11
R6 (inferencia)       12
R7 (conflicto)        20
====================  =========

R3=0 porque el WIP dice "prioridad 0 — máxima prioridad: pisa a R1/R2". Las de
lectura arrancan en 10 para que quede claro que son otra familia y no compitan
con las de negocio (el orden entre familias lo fija el orquestador de
``classification/tipo_comprobante.py``, no la prioridad). R7=20 porque se evalúa
al final, sobre la discrepancia.

Qué **no** hace este módulo (alcance estricto T-301)
----------------------------------------------------
- No decide la letra final ni arma ``TipoComprobanteResult``: eso es
  :func:`voucherflow.classification.tipo_comprobante.clasificar_tipo_comprobante`
  (F3 / T-301).
- No implementa las reglas *raw* por fuente (T-303, ``rules/raw.py``) ni el
  mapeo de tiques ``090``/``099`` (decisión abierta D-13, F3-subplan §2.8).
"""

from __future__ import annotations

import re
from typing import Any, Mapping

from .contexto import (
    CAMPOS_TOTALES_DISCRIMINADO,
    CAMPOS_TOTALES_SUBTOTAL_UNICO,
    CONDICION_EXENTO,
    CONDICION_MONOTRIBUTO,
    CONDICION_RI,
    LETRAS_COMPROBANTE,
    PAIS_ARGENTINA,
    ContextoTipoComprobante,
    letra_en_vocabulario,
)
from .registry import Registry, Rule

# ---------------------------------------------------------------------------
# Constantes portadas del prompt WIP
# ---------------------------------------------------------------------------

#: Regex de R5 (portada del ``reglas.extraccion_ocr`` del WIP).
#:
#: **Ajuste de T-305 (bug encontrado en la verificación de paridad)**: el patrón
#: literal del WIP era ``FACTURA\s+([A-CME])|COMPROBANTE\s+([A-CME])`` —con
#: ``\s+``— y por eso el ``\s+`` se comía el **salto de línea** del markdown de
#: Docling y capturaba la primera letra de la línea siguiente: el encabezado
#: ``"FACTURA\n  Código: 1"`` producía la letra ``C`` (el ``C`` de
#: "**C**ódigo") sobre **dos PDFs reales del golden** cuyo encabezado dice
#: ``FACTURA A``. El patrón del WIP nunca se ejecutó como código en v1 (vivía en
#: el prompt como ``criterio`` descriptivo), así que el defecto recién apareció
#: al portarlo a un motor determinístico (T-301) y ejercitarlo con documentos
#: reales (T-305).
#:
#: El ajuste es **mínimo y conserva la semántica** del ``criterio`` del WIP
#: ("letra junto a FACTURA"): se reemplaza ``\s+`` por ``[ \t]+`` —espacios y
#: tabulaciones, **nunca** un salto de línea— y se agrega ``\b`` después de la
#: letra para no capturar la inicial de la palabra siguiente (``"FACTURA
#: Código"`` no debe dar ``C``, porque ``C`` va seguida de una letra). El ``\b``
#: es de Unicode, así que ``C`` + ``ó`` tampoco produce frontera.
REGEX_LETRA_ENCABEZADO = re.compile(
    r"(?:FACTURA|COMPROBANTE)[ \t]+([A-CME])\b",
    re.IGNORECASE,
)

#: Mensaje de la alerta de R7 (portado literal del WIP §``conflicto_auditoria``).
MENSAJE_R7 = (
    "Comprobante inválido para cómputo de crédito fiscal. Requiere rechazo de "
    "rendición y solicitud de Nota de Crédito + Factura A correcta."
)

#: Tabla declarativa de inferencia de R6 (``campos_totales`` → letra).
#:
#: Es **dato**, no código: ``"*"`` significa "cualquier condición del emisor" y
#: las claves restantes son la condición fiscal del **emisor** que desempata el
#: caso ``subtotal_unico`` (emisor RI → B; Monotributo/Exento → C). Portado del
#: WIP §``reglas.extraccion_ocr`` (R6) + §``system`` ("Emisor Monotributo o
#: Exento: C" / R2B).
TABLA_INFERENCIA_R6: dict[str, dict[str, str]] = {
    CAMPOS_TOTALES_DISCRIMINADO: {"*": "A"},
    CAMPOS_TOTALES_SUBTOTAL_UNICO: {
        CONDICION_RI: "B",
        CONDICION_MONOTRIBUTO: "C",
        CONDICION_EXENTO: "C",
    },
}


# ---------------------------------------------------------------------------
# Evaluadores puros de cada regla de lectura (cascada R4 → R5 → R6)
# ---------------------------------------------------------------------------


def letra_de_recuadro(ctx: ContextoTipoComprobante) -> str | None:
    """R4 — letra única del recuadro del encabezado leída por el VLM.

    Devuelve la letra normalizada si está en ``{A,B,C,M,E}``; si el VLM devolvió
    ``None`` o algo fuera del vocabulario (p. ej. ``"Z"``), devuelve ``None``
    (no se inventa una letra).
    """
    return letra_en_vocabulario(ctx.letra_recuadro_vlm)


def letra_de_encabezado(ctx: ContextoTipoComprobante) -> str | None:
    """R5 — letra extraída por regex del texto de encabezado (segunda fuente).

    Portado del WIP: ``FACTURA\\s+([A-CME])|COMPROBANTE\\s+([A-CME])`` → el grupo
    capturado. Solo se considera si R4 no dio resultado (cascada).
    """
    if letra_de_recuadro(ctx) is not None:
        return None
    if not ctx.texto_encabezado_llm:
        return None
    return letra_en_vocabulario(extraer_letra_encabezado(ctx.texto_encabezado_llm))


def letra_de_campos_totales(ctx: ContextoTipoComprobante) -> str | None:
    """R6 — inferencia por ``campos_totales`` con desempate por condición fiscal.

    Solo se considera si R4 y R5 fallaron (cascada). Usa :data:`TABLA_INFERENCIA_R6`
    como dato declarativo: ``discriminado`` → ``A``; ``subtotal_unico`` →
    ``B`` (emisor RI) o ``C`` (emisor Monotributo/Exento). Si no puede
    desempatar (p. ej. emisor desconocido) devuelve ``None``.
    """
    if letra_de_recuadro(ctx) is not None or letra_de_encabezado(ctx) is not None:
        return None
    return resolver_tabla_inferencia(TABLA_INFERENCIA_R6, ctx)


def extraer_letra_encabezado(texto: str | None) -> str | None:
    """Extrae la letra del encabezado con :data:`REGEX_LETRA_ENCABEZADO`.

    Devuelve el grupo capturado (``FACTURA X`` o ``COMPROBANTE X``, con la letra
    **en la misma línea** que la palabra) o ``None`` si no hay coincidencia. La
    letra se devuelve en **mayúscula** (el patrón es ``IGNORECASE`` para tolerar
    OCR/LLM que escriben "Factura A"), así el valor ya viene en el vocabulario
    del motor.
    """
    if not texto:
        return None
    coincidencia = REGEX_LETRA_ENCABEZADO.search(texto)
    if not coincidencia:
        return None
    return coincidencia.group(1).upper()


def resolver_tabla_inferencia(
    tabla: Mapping[str, Mapping[str, str]], ctx: ContextoTipoComprobante
) -> str | None:
    """Resuelve la inferencia de R6 contra una tabla declarativa.

    ``tabla[campos_totales]`` es una fila que mapea condición fiscal del emisor
    a letra; la clave especial ``"*"`` aplica a cualquier condición. Devuelve
    ``None`` si no hay fila para los ``campos_totales`` del contexto o si la
    fila no cubre la condición fiscal del emisor (no se inventa desempate).
    """
    fila = tabla.get(ctx.campos_totales or "")
    if fila is None:
        return None
    if "*" in fila:
        return letra_en_vocabulario(fila["*"])
    return letra_en_vocabulario(fila.get(ctx.emisor_condicion_fiscal or ""))


def letra_de_regla(regla: Rule, ctx: ContextoTipoComprobante) -> str | None:
    """Letra que produce una regla disparada (resultado fijo, dinámico o tabla).

    El ``resultado`` de una ``Rule`` puede ser:
    - una **letra fija** (``"A"``, ``"B"``, ``"C"``, ``"E"``): se normaliza;
    - un **callable** ``ctx -> letra`` (R4/R5, cuya letra depende de la lectura);
    - una **tabla de decisión** (``Mapping``, R6): se resuelve contra el contexto
      con :func:`resolver_tabla_inferencia`.

    Devuelve ``None`` si la regla no produce una letra del vocabulario (p. ej.
    R6 con emisor desconocido en ``subtotal_unico``).
    """
    resultado = regla.resultado
    if callable(resultado):
        resultado = resultado(ctx)
    if isinstance(resultado, Mapping):
        return resolver_tabla_inferencia(resultado, ctx)
    return letra_en_vocabulario(resultado)


# ---------------------------------------------------------------------------
# Condiciones (funciones puras sobre el contexto; sin efectos colaterales)
# ---------------------------------------------------------------------------


def _receptor_en_argentina(ctx: ContextoTipoComprobante) -> bool:
    """True si el país del receptor está informado y es Argentina (no exportación)."""
    if ctx.receptor_pais is None:
        return False
    return ctx.receptor_pais.strip().lower() == PAIS_ARGENTINA.lower()


def condicion_r1(ctx: ContextoTipoComprobante) -> bool:
    """R1: emisor Monotributo o Exento → C (independiente del receptor)."""
    return ctx.emisor_condicion_fiscal in (CONDICION_MONOTRIBUTO, CONDICION_EXENTO)


def condicion_r2a(ctx: ContextoTipoComprobante) -> bool:
    """R2A: emisor Responsable Inscripto + receptor Responsable Inscripto → A."""
    return (
        ctx.emisor_condicion_fiscal == CONDICION_RI
        and ctx.receptor_condicion_fiscal == CONDICION_RI
    )


def condicion_r2b(ctx: ContextoTipoComprobante) -> bool:
    """R2B: emisor RI + receptor Monotributo/Exento/Consumidor Final → B."""
    return ctx.emisor_condicion_fiscal == CONDICION_RI and ctx.receptor_condicion_fiscal in (
        CONDICION_MONOTRIBUTO,
        CONDICION_EXENTO,
        "Consumidor Final",
    )


def condicion_r3(ctx: ContextoTipoComprobante) -> bool:
    """R3: receptor con país distinto de Argentina → E (pisa R1/R2).

    Un ``receptor_pais`` ausente (``None``) **no** dispara la regla: no se
    asume exportación sin evidencia (prompt WIP: "No inventes datos").
    """
    if ctx.receptor_pais is None:
        return False
    return not _receptor_en_argentina(ctx)


def condicion_r4(ctx: ContextoTipoComprobante) -> bool:
    """R4: hay letra válida en el recuadro del encabezado leída por el VLM."""
    return letra_de_recuadro(ctx) is not None


def condicion_r5(ctx: ContextoTipoComprobante) -> bool:
    """R5: R4 no dio resultado y la regex del encabezado captura una letra."""
    return letra_de_encabezado(ctx) is not None


def condicion_r6(ctx: ContextoTipoComprobante) -> bool:
    """R6: R4 y R5 fallaron y los ``campos_totales`` permiten inferir una letra.

    La regla "aplica" (se registra su disparo para auditoría) cuando hay una
    señal de totales reconocida; puede resolver ``None`` si no alcanza para
    desempatar (emisor desconocido en ``subtotal_unico``), en cuyo caso el
    orquestador deja el ``tipo_detectado_por_documento`` en ``None``.
    """
    if letra_de_recuadro(ctx) is not None or letra_de_encabezado(ctx) is not None:
        return False
    return ctx.campos_totales in TABLA_INFERENCIA_R6


def condicion_r7(ctx: ContextoTipoComprobante) -> bool:
    """R7: emisor RI ∧ receptor RI ∧ letra **detectada** B → conflicto financiero.

    "Letra detectada" es la que resuelve la cascada de lectura (R4 → R5 → R6)
    sobre el mismo contexto; se usa el motor de lectura para no duplicar la
    lógica de detección (el ``cuit_propio`` del WIP queda como refuerzo opcional
    de la alerta, no como condición obligatoria — Gherkin E-CLAS-1 R7).
    """
    return (
        ctx.emisor_condicion_fiscal == CONDICION_RI
        and ctx.receptor_condicion_fiscal == CONDICION_RI
        and evaluar_lectura(ctx) == "B"
    )

# ---------------------------------------------------------------------------
# Registros declarativos (tres familias separadas, §2.3 del subplan)
# ---------------------------------------------------------------------------


def _construir_negocio() -> Registry:
    """Construye ``REGISTRO_NEGOCIO`` (R1, R2A, R2B, R3)."""
    registro = Registry()
    registro.registrar(
        Rule(
            id="R3",
            prioridad=0,
            condicion=condicion_r3,
            resultado="E",
            tipo="negocio",
            detalle=(
                "R3 (exportación): el país del receptor es distinto de Argentina → "
                "tipo esperado E. Prioridad máxima (0): pisa a R1/R2. Fuente: prompt WIP "
                "prompts/wip/deteccion_tipo_factura.yaml §reglas.negocio_tributario (R3) "
                "y §orden_de_evaluacion (paso 1); Gherkin E-CLAS-1 · R3 exportación."
            ),
        )
    )
    registro.registrar(
        Rule(
            id="R1",
            prioridad=1,
            condicion=condicion_r1,
            resultado="C",
            tipo="negocio",
            detalle=(
                "R1 (emisor Monotributo o Exento): el tipo esperado es C sin importar "
                "la condición del receptor. Fuente: prompt WIP "
                "§reglas.negocio_tributario (R1); Gherkin E-CLAS-1 · R1 emisor "
                "monotributo/exento."
            ),
        )
    )
    registro.registrar(
        Rule(
            id="R2A",
            prioridad=2,
            condicion=condicion_r2a,
            resultado="A",
            tipo="negocio",
            detalle=(
                "R2A (responsabilidad fiscal): emisor Responsable Inscripto y receptor "
                "Responsable Inscripto → tipo esperado A. Fuente: prompt WIP "
                "§reglas.negocio_tributario (R2A); Gherkin E-CLAS-1 · R2A/R2B "
                "responsabilidad fiscal."
            ),
        )
    )
    registro.registrar(
        Rule(
            id="R2B",
            prioridad=2,
            condicion=condicion_r2b,
            resultado="B",
            tipo="negocio",
            detalle=(
                "R2B (responsabilidad fiscal): emisor Responsable Inscripto y receptor "
                "Monotributo, Exento o Consumidor Final → tipo esperado B. Fuente: "
                "prompt WIP §reglas.negocio_tributario (R2B); Gherkin E-CLAS-1 · "
                "R2A/R2B responsabilidad fiscal."
            ),
        )
    )
    return registro


def _construir_lectura() -> Registry:
    """Construye ``REGISTRO_LECTURA`` (R4, R5, R6) en cascada."""
    registro = Registry()
    registro.registrar(
        Rule(
            id="R4",
            prioridad=10,
            condicion=condicion_r4,
            resultado=lambda ctx: letra_de_recuadro(ctx),
            tipo="lectura",
            detalle=(
                "R4 (recuadro VLM): bounding box superior central/izquierda con una "
                "letra única en {A,B,C,M,E}; se usa esa letra. El VLM prioriza lo que "
                "ve. Fuente: prompt WIP §reglas.extraccion_ocr (R4) y "
                "§orden_de_evaluacion (paso 3); Gherkin E-CLAS-1 · R4/R5 extracción "
                "OCR/VLM."
            ),
        )
    )
    registro.registrar(
        Rule(
            id="R5",
            prioridad=11,
            condicion=condicion_r5,
            resultado=lambda ctx: letra_de_encabezado(ctx),
            tipo="lectura",
            detalle=(
                "R5 (regex sobre el texto de encabezado): si R4 no dio resultado, se "
                "aplica FACTURA\\s+([A-CME])|COMPROBANTE\\s+([A-CME]) y se usa el grupo "
                "capturado. Fuente: prompt WIP §reglas.extraccion_ocr (R5) y "
                "§orden_de_evaluacion (paso 3); Gherkin E-CLAS-1 · R4/R5 extracción "
                "OCR/VLM."
            ),
        )
    )
    registro.registrar(
        Rule(
            id="R6",
            prioridad=12,
            condicion=condicion_r6,
            resultado=TABLA_INFERENCIA_R6,
            tipo="lectura",
            detalle=(
                "R6 (inferencia por campos totales): si R4 y R5 fallan, campos "
                "discriminado → A (candidato); subtotal único → B si el emisor es "
                "Responsable Inscripto o C si es Monotributo/Exento. Fuente: prompt WIP "
                "§reglas.extraccion_ocr (R6) y §orden_de_evaluacion (paso 3); Gherkin "
                "E-CLAS-1 · R6 inferencia por campos totales."
            ),
        )
    )
    return registro


def _construir_conflicto() -> Registry:
    """Construye ``REGISTRO_CONFLICTO`` (R7)."""
    registro = Registry()
    registro.registrar(
        Rule(
            id="R7",
            prioridad=20,
            condicion=condicion_r7,
            resultado="ALERTA_ERROR_FINANCIERO",
            tipo="cruzada",
            detalle=(
                "R7 (conflicto financiero): emisor Responsable Inscripto, receptor "
                "Responsable Inscripto y letra detectada B → alerta de comprobante "
                "inválido para cómputo de crédito fiscal. Fuente: prompt WIP "
                "§reglas.conflicto_auditoria (R7) y §orden_de_evaluacion (paso 6); "
                "Gherkin E-CLAS-1 · R7 conflicto financiero."
            ),
        )
    )
    return registro


#: Registro de reglas de negocio (R1/R2A/R2B/R3) — resuelve el tipo esperado.
REGISTRO_NEGOCIO: Registry = _construir_negocio()
#: Registro de reglas de lectura del documento (R4/R5/R6) — cascada de detección.
REGISTRO_LECTURA: Registry = _construir_lectura()
#: Registro de reglas de conflicto (R7) — alertas de auditoría.
REGISTRO_CONFLICTO: Registry = _construir_conflicto()


def construir_registros() -> dict[str, Registry]:
    """Devuelve **instancias nuevas** de los tres registros, indexadas por clave.

    Claves (contrato T-301): ``"negocio"``, ``"lectura"`` y ``"conflicto"``.
    Devuelve instancias nuevas (no los singleton de módulo) para que los tests
    y las fases futuras (F5) puedan inspeccionar/mutar una copia sin afectar el
    motor global.
    """
    return {
        "negocio": _construir_negocio(),
        "lectura": _construir_lectura(),
        "conflicto": _construir_conflicto(),
    }


# ---------------------------------------------------------------------------
# Helpers públicos de evaluación por familia (los consume classification/)
# ---------------------------------------------------------------------------


def regla_negocio_resolutoria(ctx: ContextoTipoComprobante) -> Rule | None:
    """Regla de negocio que **resuelve** el tipo esperado (la de mayor prioridad).

    Como el registro está ordenado por prioridad y R3 (0) precede a R1 (1) y a
    R2A/R2B (2), la primera regla disparada es la que "manda": R3 pisa a R1/R2.
    Devuelve ``None`` si ninguna regla de negocio aplica (condiciones fiscales
    incompletas o no reconocidas).
    """
    disparadas = REGISTRO_NEGOCIO.evaluar_todas(ctx)
    return disparadas[0] if disparadas else None


def evaluar_negocio(ctx: ContextoTipoComprobante) -> str | None:
    """Tipo **esperado por negocio** (R3 → R1 → R2A/R2B) o ``None``.

    Devuelve la letra de la regla resolutoria (la de mayor prioridad que
    dispara): R3 (exportación) gana sobre R1/R2, tal como dice el WIP
    ("si aplica, resultado = E, fin").
    """
    regla = regla_negocio_resolutoria(ctx)
    if regla is None:
        return None
    return letra_de_regla(regla, ctx)


def reglas_negocio_disparadas(ctx: ContextoTipoComprobante) -> list[str]:
    """Ids de **todas** las reglas de negocio disparadas, en orden de prioridad.

    Es una traza completa para auditoría: puede incluir reglas que dispararon
    pero fueron pisadas (p. ej. R1 junto a R3). La regla que efectivamente
    resolvió se obtiene con :func:`regla_negocio_resolutoria`; el orquestador
    usa solo la resolutoria para ``reglas_aplicadas``.
    """
    return REGISTRO_NEGOCIO.ids_disparados(ctx)


def evaluar_lectura(ctx: ContextoTipoComprobante) -> str | None:
    """Tipo **detectado por documento** (cascada R4 → R5 → R6) o ``None``.

    Las condiciones de R5/R6 ya exigen que las anteriores no hayan dado
    resultado, de modo que la primera regla disparada es la resolutoria.
    """
    regla = regla_lectura_resolutoria(ctx)
    if regla is None:
        return None
    return letra_de_regla(regla, ctx)


def regla_lectura_resolutoria(ctx: ContextoTipoComprobante) -> Rule | None:
    """Regla de lectura que resuelve la letra detectada (cascada R4 → R5 → R6).

    Devuelve ``None`` si ninguna fuente de lectura aporta una letra.
    """
    disparadas = REGISTRO_LECTURA.evaluar_todas(ctx)
    return disparadas[0] if disparadas else None


def reglas_lectura_disparadas(ctx: ContextoTipoComprobante) -> list[str]:
    """Ids de las reglas de lectura aplicadas (condiciones mutuamente excluyentes).

    A lo sumo una regla de lectura dispara por contexto (R5 exige que R4 no
    haya resuelto y R6 exige que R4 y R5 no hayan resuelto), por lo que la traza
    coincide con la regla resolutoria.
    """
    return REGISTRO_LECTURA.ids_disparados(ctx)


def evaluar_conflicto(ctx: ContextoTipoComprobante) -> list[Rule]:
    """Reglas de conflicto disparadas (R7) para el contexto, en orden de prioridad."""
    return REGISTRO_CONFLICTO.evaluar_todas(ctx)


def construir_alerta(regla: Rule, ctx: ContextoTipoComprobante) -> dict[str, Any]:
    """Construye el dict de alerta trazable de una regla de conflicto (R7).

    Incluye el ``id`` de la regla (E-CONC-5), el mensaje y el detalle portados
    del WIP, y el **refuerzo opcional** del ``cuit_propio`` (``cuit_scania_ri``
    en el WIP).

    Decisión T-301 (documentada): el Gherkin de E-CLAS-1 define R7 solo con las
    condiciones fiscales (emisor RI + receptor RI + letra detectada B), así que
    el CUIT **no** es condición obligatoria de la regla. Si ``cuit_propio`` y
    ``receptor_cuit`` están informados y **coinciden**, el ``detalle`` de la
    alerta lo menciona explícitamente como refuerzo (el comprobante es del
    propio contribuyente) y se marca ``refuerzo_cuit=True``.
    """
    refuerzo = (
        ctx.cuit_propio is not None
        and ctx.receptor_cuit is not None
        and ctx.cuit_propio.strip() == ctx.receptor_cuit.strip()
    )
    detalle = regla.detalle
    if refuerzo:
        detalle = (
            f"{detalle} Refuerzo: el CUIT propio ({ctx.cuit_propio}) coincide con el "
            "CUIT del receptor, por lo que el comprobante es del propio contribuyente "
            "(dato de despliegue opcional de R7, no condición obligatoria)."
        )
    alerta: dict[str, Any] = {
        "disparada": True,
        "regla": regla.id,
        "mensaje": MENSAJE_R7 if regla.id == "R7" else str(regla.resultado),
        "detalle": detalle,
        "refuerzo_cuit": refuerzo,
    }
    if ctx.cuit_propio is not None:
        alerta["cuit_propio"] = ctx.cuit_propio
    return alerta


__all__ = [
    "REGISTRO_NEGOCIO",
    "REGISTRO_LECTURA",
    "REGISTRO_CONFLICTO",
    "REGEX_LETRA_ENCABEZADO",
    "MENSAJE_R7",
    "TABLA_INFERENCIA_R6",
    "construir_registros",
    "evaluar_negocio",
    "regla_negocio_resolutoria",
    "regla_lectura_resolutoria",
    "evaluar_lectura",
    "evaluar_conflicto",
    "reglas_negocio_disparadas",
    "reglas_lectura_disparadas",
    "construir_alerta",
    "letra_de_regla",
    "letra_de_recuadro",
    "letra_de_encabezado",
    "letra_de_campos_totales",
    "extraer_letra_encabezado",
    "resolver_tabla_inferencia",
    "condicion_r1",
    "condicion_r2a",
    "condicion_r2b",
    "condicion_r3",
    "condicion_r4",
    "condicion_r5",
    "condicion_r6",
    "condicion_r7",
]

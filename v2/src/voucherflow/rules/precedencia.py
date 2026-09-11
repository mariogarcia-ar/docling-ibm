"""Tabla de precedencia por campo y resolución de desacuerdos (F4 / T-404).

**Fase**: F4 (extracción) · **Tarea**: T-404 · **Épica**: E-EXT-1 · **ADR**: ADR-002.

Qué resuelve este módulo
------------------------
T-401 corre los dos flujos **siempre** (VLM sobre la imagen y LLM sobre el
OCR/Markdown) y conserva las dos ``SourceEvidence`` **sin colapsar**; T-402 deja
sus valores en forma canónica y T-403 califica cada fuente **por sí sola**. Lo
que falta es el paso de ADR-002: cuando las dos fuentes **discrepan** en un
campo, decidir **cuál gana**, por campo y de forma determinista, dejando la
regla y el motivo en la resolución (auditoría E-CONC-5).

La regla de oro de ADR-002 es la que ordena la tabla:

    *la ley/negocio comprobado manda sobre el papel para descartar;
    el papel (evidencia visual/textual) manda sobre la inferencia para detectar.*

Es decir: lo que **se ve/se lee** en el documento es la fuente de verdad de la
**lectura** (con la letra del recuadro por delante de lo inferido), y ninguna
lectura puede pisar un dato **comprobado** por negocio o padrón (``programa``,
``arca``, ``hitl``). Este módulo implementa esa precedencia para la evidencia de
extracción; el motor R1-R7 (F3/T-301) resuelve aparte la **letra** del
comprobante, con su propio parámetro de preferencia (D-14).

Determinismo: la resolución no depende del orden de llegada
----------------------------------------------------------
La combinación **no** puede depender de qué hilo terminó antes (los flujos corren
en paralelo, T-401). La resolución se calcula ordenando las fuentes presentes por
la precedencia declarada del campo y desempatando por el **orden canónico** de
fuentes (``programa`` > ``arca`` > ``vlm`` > ``llm`` > ``hitl``). Dos corridas del
mismo caso dan la misma resolución.

Qué gana cuando las dos fuentes coinciden
-----------------------------------------
Si los valores canónicos coinciden, la fuente **ganadora** es la de mayor
precedencia (la que se usaría si discreparan) y la resolución lo registra como
acuerdo. Así el consumidor tiene siempre **una** fuente responsable del valor,
sin inventar una "tercera" lectura.

Qué pasa cuando la ganadora está debilitada
-------------------------------------------
La pasada 1 (T-403) puede dejar una fuente **no utilizable** (``valida=False``):
un valor fuera del vocabulario, por ejemplo. Esa fuente no puede ganar un campo
que la otra **sí** resolvió: el módulo elige la mejor fuente **utilizable** y
deja constancia de que descartó una lectura por inválida. Si **ninguna** fuente
es utilizable para el campo, gana igual la de mayor precedencia (no se inventa un
valor) pero la resolución lo declara explícitamente como lectura **no confiable**.

Decisiones de alcance
---------------------
* **La precedencia es por campo, no por fuente**: ``tipo_comprobante`` y
  ``nro_comprobante`` priorizan la lectura **visual** (el recuadro y el número
  impreso son lo que el VLM ve); ``fecha_emision``, ``moneda`` y el
  ``nro_comprobante`` priorizan el **texto** (la fecha y la moneda se leen junto
  a su etiqueta); los **importes** y los datos derivados priorizan el **texto**
  (el OCR conserva los dígitos y los separadores que el VLM puede confundir).
* **Las fuentes que no son de lectura ganan siempre**: ``programa`` (lo que
  calculó el sistema: ``punto_venta``/``numero_comprobante``), ``arca`` (padrón)
  y ``hitl`` (corrección humana) están por encima de ``vlm``/``llm`` en **todos**
  los campos. Un dato comprobado o corregido no se discute con una lectura.
* **La tabla es declarativa y auditable**: cada campo declara su orden de
  precedencia; un campo que no esté en la tabla usa el orden por defecto
  (``programa`` > ``arca`` > ``vlm`` > ``llm`` > ``hitl``), que es la regla de
  oro llevada al caso general (el papel manda a la inferencia).
* **No se pierde nada**: el ``CampoCombinado`` conserva las lecturas de **todas**
  las fuentes, con la resolución al lado. La combinación resuelve, no descarta.

Qué **no** hace T-404
---------------------
* No decide la **letra** del comprobante: eso es R1-R7 (F3/T-301) sobre el
  contexto, y su precedencia es D-14.
* No concluye el caso ni arma el ``VoucherResult``: eso es F5/T-501.
* No re-calcula la pasada raw ni la normalización: recibe la evidencia ya
  calificada (T-403) y ya canónica (T-402).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Iterable, Mapping

from ..schemas.evidence import (
    CampoCombinado,
    EvidenceField,
    FieldResolution,
    Fuente,
    SourceEvidence,
)

# ---------------------------------------------------------------------------
# Orden de las fuentes
# ---------------------------------------------------------------------------

#: Fuentes que **no son lectura** del documento, en orden de autoridad: la
#: corrección humana tiene la última palabra (``hitl``), después el dato
#: comprobado contra un padrón (``arca``) y finalmente lo que **calculó el
#: programa** (``programa``). Ninguna se discute con una lectura.
FUENTES_NO_LECTURA: tuple[Fuente, ...] = (
    Fuente.hitl,
    Fuente.arca,
    Fuente.programa,
)

#: Fuentes que son **lectura del documento**, en el orden por defecto: el papel
#: manda a la inferencia (regla de oro de ADR-002), y la lectura **visual**
#: (el recuadro, el membrete) por delante de la **textual**.
FUENTES_LECTURA: tuple[Fuente, ...] = (Fuente.vlm, Fuente.llm)

#: Orden total de todas las fuentes del contrato de F0, de mayor a menor
#: autoridad: primero las que no son lectura (``FUENTES_NO_LECTURA``) y después
#: las lecturas (``FUENTES_LECTURA``). Es el orden con el que se recorren las
#: fuentes al combinar y el que desempata.
ORDEN_CANONICO_FUENTES: tuple[Fuente, ...] = FUENTES_NO_LECTURA + FUENTES_LECTURA

# ---------------------------------------------------------------------------
# Ids de las reglas de precedencia (trazabilidad, ADR-002 / E-CONC-5)
# ---------------------------------------------------------------------------

PREC_LECTURA_VISUAL = "PREC_1"
"""El campo prioriza la lectura **visual** (VLM): el recuadro del encabezado y el
número impreso son lo que el flujo de imagen ve directamente."""

PREC_LECTURA_TEXTO = "PREC_2"
"""El campo prioriza la lectura **textual** (LLM): la fecha, la moneda y los
importes se leen junto a su etiqueta impresa, y el OCR conserva los dígitos y los
separadores que la visión puede confundir."""

PREC_DATO_COMPUTADO = "PREC_3"
"""El campo lo **calcula el programa** (p. ej. ``punto_venta``/``numero_comprobante``
derivados del número impreso): ninguna lectura lo discute."""

PREC_REGLA_DE_ORO = "PREC_0"
"""Orden por defecto para los campos sin precedencia declarada: la regla de oro de
ADR-002 — lo comprobado (programa/arca/hitl) manda, y entre lecturas manda el
papel (visual sobre textual)."""


@dataclass(frozen=True)
class PrecedenciaCampo:
    """Orden de precedencia declarado para **un** campo (T-404 / ADR-002).

    Campos:
        campo: nombre del campo (``tipo_comprobante``, ``cuit_emisor``, …).
        orden: **orden de las fuentes de lectura** del campo, de mayor a menor
            precedencia. Las lecturas que no se nombren se agregan detrás en el
            orden por defecto; las fuentes que **no** son lectura
            (``FUENTES_NO_LECTURA``) van siempre **por delante**, en su orden de
            autoridad (ver :meth:`orden_completo`). Así un campo declara solo lo
            que le importa ("gana el visual") sin enumerar las cinco fuentes.
        regla: id de la regla que se registra en la resolución cuando este campo
            gana por su orden declarado (``PREC_1``…).
        motivo: explicación legible del porqué, que se copia a la resolución
            (auditoría: el consumidor no tiene que interpretar el id).
    """

    campo: str
    orden: tuple[Fuente, ...]
    regla: str
    motivo: str

    def orden_completo(self) -> tuple[Fuente, ...]:
        """Orden **total** de las fuentes para este campo.

        Primero las que no son lectura, en su orden de autoridad
        (``hitl`` > ``arca`` > ``programa``): lo corregido y lo comprobado no se
        discute con una lectura. Después las lecturas en el orden que declaró el
        campo, completadas con las restantes en el orden por defecto (visual
        antes que textual). Es el orden que aplica :func:`resolver_campo`, y por
        eso no puede depender del orden en que llegaron las fuentes.
        """
        return FUENTES_NO_LECTURA + self.orden_lecturas()

    def orden_lecturas(self) -> tuple[Fuente, ...]:
        """Orden de las **lecturas** del campo (visual vs. textual).

        Es lo que la tabla declara (``PREC_1`` visual, ``PREC_2`` textual) y lo
        que se lee en ``TABLA_PRECEDENCIA`` para auditar el criterio: las fuentes
        que no son lectura no participan de esta decisión (van siempre delante).
        """
        lecturas = [f for f in self.orden if f in FUENTES_LECTURA]
        for fuente in FUENTES_LECTURA:
            if fuente not in lecturas:
                lecturas.append(fuente)
        return tuple(lecturas)


def _orden_visual(campo: str, motivo: str) -> PrecedenciaCampo:
    """Atajo: el campo prioriza la lectura visual sobre la textual (PREC_1)."""
    return PrecedenciaCampo(
        campo=campo,
        orden=(Fuente.vlm, Fuente.llm),
        regla=PREC_LECTURA_VISUAL,
        motivo=motivo,
    )


def _orden_texto(campo: str, motivo: str) -> PrecedenciaCampo:
    """Atajo: el campo prioriza la lectura textual sobre la visual (PREC_2)."""
    return PrecedenciaCampo(
        campo=campo,
        orden=(Fuente.llm, Fuente.vlm),
        regla=PREC_LECTURA_TEXTO,
        motivo=motivo,
    )


def _orden_computado(campo: str, motivo: str) -> PrecedenciaCampo:
    """Atajo: el campo lo calcula el programa y ninguna lectura lo discute.

    El orden de lecturas se deja en el de la regla de oro (visual > textual):
    solo importa si una lectura declarara el campo igual, porque ``programa``
    —que no es lectura— va siempre por delante.
    """
    return PrecedenciaCampo(
        campo=campo,
        orden=(Fuente.vlm, Fuente.llm),
        regla=PREC_DATO_COMPUTADO,
        motivo=motivo,
    )


#: Tabla de precedencia **del contrato de extracción** (T-404). El orden explícito
#: es el que la resolución registra como ``PREC_n``; las fuentes no nombradas
#: quedan detrás en el orden canónico.
#:
#: Criterio de cada entrada (ver `EXT.md` §3 y ADR-002):
#:
#: * ``tipo_comprobante`` — **visual**: la letra del recuadro del encabezado es lo
#:   que el VLM ve; es la fuente que v1 (`11.1`) declaraba prioritaria y la que
#:   ADR-002 cita como ejemplo ("visual gana en la letra del encabezado si el
#:   recuadro se detectó con claridad").
#: * ``nro_comprobante`` — **visual**: el número impreso está en el encabezado,
#:   junto al recuadro, y el OCR lo suele pegar al campo siguiente.
#: * ``razon_social_emisor`` — **visual**: el membrete es un elemento gráfico
#:   (tipografía, logo); el OCR lo aplana en texto corrido.
#: * ``fecha_emision`` — **texto**: la fecha se lee junto a su etiqueta impresa;
#:   el VLM puede confundir día y mes en un sello.
#: * ``moneda`` — **texto**: el símbolo viaja junto a las etiquetas de importe.
#: * importes (``subtotal``, ``iva``, …, ``importe_total_facturado``) — **texto**:
#:   el OCR conserva los dígitos y separadores; el VLM puede leer un importe de
#:   otra columna.
#: * ``cuit_emisor``/``cuit_receptor`` — **visual**: el CUIT es un dato del
#:   encabezado y el OCR lo corta o lo pega al campo siguiente (el caso que T-402
#:   documenta); la lectura visual lo ve como bloque.
#: * ``punto_venta``/``numero_comprobante`` — **programa**: los deriva T-402 del
#:   número impreso; no se leen.
TABLA_PRECEDENCIA: dict[str, PrecedenciaCampo] = {
    campo.campo: campo
    for campo in (
        _orden_visual(
            "tipo_comprobante",
            "la letra del recuadro del encabezado es lo que la lectura visual ve "
            "directamente (ADR-002: 'visual gana en la letra del encabezado si el "
            "recuadro se detectó con claridad').",
        ),
        _orden_visual(
            "nro_comprobante",
            "el número impreso está en el encabezado, junto al recuadro; el OCR "
            "suele pegarlo al campo siguiente (regla 2b de v1).",
        ),
        _orden_visual(
            "razon_social_emisor",
            "el membrete es un elemento gráfico (tipografía y disposición); el OCR "
            "lo aplana en texto corrido.",
        ),
        _orden_visual(
            "cuit_emisor",
            "el CUIT del emisor está en el encabezado; la lectura visual lo ve "
            "como bloque y el OCR lo corta o lo pega al campo siguiente.",
        ),
        _orden_visual(
            "cuit_receptor",
            "el CUIT del receptor está en el encabezado, después del emisor; misma "
            "razón que el del emisor.",
        ),
        _orden_texto(
            "fecha_emision",
            "la fecha se lee junto a su etiqueta impresa; la visión puede confundir "
            "día y mes en un sello.",
        ),
        _orden_texto(
            "moneda",
            "el símbolo de moneda viaja junto a las etiquetas de importe, que el "
            "OCR conserva.",
        ),
        _orden_texto(
            "razon_social_receptor",
            "el receptor aparece como texto corrido (sin membrete propio): ahí la "
            "lectura textual es la que tiene la etiqueta.",
        ),
        _orden_texto(
            "descripcion",
            "la descripción se arma desde los ítems impresos: es texto.",
        ),
        # Importes: el OCR conserva dígitos y separadores.
        *(
            _orden_texto(
                campo,
                "los importes se leen de las columnas impresas: el OCR conserva "
                "los dígitos y separadores, y la visión puede tomar un importe de "
                "otra columna.",
            )
            for campo in (
                "subtotal",
                "iva",
                "impuestos_internos",
                "percepcion_iibb",
                "otros_impuestos",
                "monto_no_gravado",
                "importe_total_facturado",
            )
        ),
        # Derivados del número impreso: los calcula el programa (T-402).
        _orden_computado(
            "punto_venta",
            "lo deriva el programa del número impreso (T-402): ninguna lectura lo "
            "discute.",
        ),
        _orden_computado(
            "numero_comprobante",
            "lo deriva el programa del número impreso (T-402): ninguna lectura lo "
            "discute.",
        ),
    )
}


def _precedencia_de(campo: str) -> PrecedenciaCampo:
    """Precedencia declarada del campo, o la de la **regla de oro** (T-404).

    Un campo que no está en la tabla (los del modo genérico ``kvg``, o uno nuevo
    del contrato) usa :data:`PREC_REGLA_DE_ORO`: lo comprobado por delante de lo
    leído y, entre lecturas, el papel (visual sobre textual). No se inventa una
    precedencia específica sin declararla.
    """
    declarada = TABLA_PRECEDENCIA.get(campo)
    if declarada is not None:
        return declarada
    return PrecedenciaCampo(
        campo=campo,
        orden=(Fuente.vlm, Fuente.llm),
        regla=PREC_REGLA_DE_ORO,
        motivo=(
            "campo sin precedencia declarada: se aplica la regla de oro de "
            "ADR-002 (lo comprobado manda sobre lo leído y, entre lecturas, el "
            "papel: visual sobre textual)."
        ),
    )


# ---------------------------------------------------------------------------
# Resolución de un campo
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class ResolucionCampo:
    """Resultado de resolver **un** campo (T-404 / ADR-002).

    Es la contraparte "rica" de :class:`~voucherflow.schemas.evidence.FieldResolution`:
    además del contrato de F0 lleva la información que la auditoría y los tests
    necesitan (por qué ganó, quién quedó fuera y si hubo acuerdo).

    Campos:
        campo: nombre del campo.
        ganador: fuente que prevalece (``None`` si ninguna fuente declaró el
            campo).
        regla: id de la regla de precedencia aplicada (``PREC_0``…``PREC_3``).
        motivo: explicación legible (regla declarada + situación concreta).
        acuerdo: ``True`` si las fuentes presentes declararon el **mismo** valor
            canónico (no hubo desacuerdo que resolver).
        descartadas: fuentes que **perdieron** con un valor distinto al ganador,
            con el valor que declararon (auditoría: qué se dejó afuera y por qué).
        invalidas: fuentes que la pasada 1 marcó como no utilizables
            (``valida=False``) y por eso no pudieron ganar un campo que otra
            fuente sí resolvió.
        confiable: ``False`` cuando ganó una lectura pero **ninguna** fuente
            utilizable declaró el campo (queda como indicio, no como dato).
    """

    campo: str
    ganador: Fuente | None = None
    regla: str | None = None
    motivo: str = ""
    acuerdo: bool = False
    descartadas: tuple[tuple[Fuente, Any], ...] = ()
    invalidas: tuple[Fuente, ...] = ()
    confiable: bool = True

    def como_field_resolution(self) -> FieldResolution:
        """Traduce al contrato congelado de F0 (``FieldResolution``)."""
        return FieldResolution(
            ganador=self.ganador, regla=self.regla, motivo=self.motivo
        )

    def como_dict(self) -> dict[str, Any]:
        """Representación serializable (para el detalle/reporte de la corrida)."""
        return {
            "campo": self.campo,
            "ganador": self.ganador.value if self.ganador else None,
            "regla": self.regla,
            "motivo": self.motivo,
            "acuerdo": self.acuerdo,
            "descartadas": [
                {"fuente": fuente.value, "valor": valor}
                for fuente, valor in self.descartadas
            ],
            "invalidas": [fuente.value for fuente in self.invalidas],
            "confiable": self.confiable,
        }


def _campo_de(source: SourceEvidence, campo: str) -> EvidenceField | None:
    """``EvidenceField`` del campo en esa fuente, o ``None`` si no lo declaró."""
    return source.campos.get(campo)


def valor_de(source: SourceEvidence, campo: str) -> Any:
    """Valor que la fuente declaró para el campo (``None`` si no lo declaró).

    Es el comparador de la resolución: T-402 deja los valores en forma canónica,
    así que comparar los publicados es comparar manzanas con manzanas.
    """
    evidencia = _campo_de(source, campo)
    return None if evidencia is None else evidencia.valor


def _fuentes_declarantes(fuentes: Mapping[Fuente, SourceEvidence], campo: str) -> dict[Fuente, EvidenceField]:
    """Subconjunto de fuentes que **declararon** el campo (con su evidencia)."""
    declarantes: dict[Fuente, EvidenceField] = {}
    for fuente, source in fuentes.items():
        evidencia = _campo_de(source, campo)
        if evidencia is not None:
            declarantes[fuente] = evidencia
    return declarantes


def resolver_campo(
    campo: str,
    fuentes: Mapping[Fuente, SourceEvidence],
) -> ResolucionCampo:
    """Resuelve un campo entre las fuentes presentes (T-404 / ADR-002).

    Aplica, en este orden:

    1. Si **ninguna** fuente declaró el campo → resolución vacía (``ganador``
       ``None``): no se inventa un valor (ADR-001).
    2. Se ordenan las fuentes **declarantes** por la precedencia del campo
       (:func:`_precedencia_de`); ante empate, por el orden canónico.
    3. Si el valor canónico de las declarantes es **el mismo**, hay acuerdo: gana
       la de mayor precedencia (la que mandaría si discreparan) y se registra
       ``acuerdo=True``.
    4. Si **discrepan**, gana la primera declarante **utilizable** del orden. Las
       lecturas de fuentes inválidas (``valida=False`` en la pasada 1, T-403) no
       pueden ganar un campo que otra fuente resolvió.
    5. Si **ninguna** declarante es utilizable, gana la de mayor precedencia igual
       (no se inventa un valor) pero ``confiable=False``: el dato queda como
       indicio y la resolución lo declara.

    Argumentos:
        campo: nombre del campo a resolver.
        fuentes: mapping ``fuente -> SourceEvidence`` de la corrida. Solo las
            fuentes presentes participan (las que no corrieron no votan).

    Devuelve:
        :class:`ResolucionCampo` con el ganador, la regla, el motivo, las
        descartadas y la bandera de confiabilidad. Determinista: no depende del
        orden del mapping.
    """
    precedencia = _precedencia_de(campo)
    orden = precedencia.orden_completo()
    declarantes = _fuentes_declarantes(fuentes, campo)

    if not declarantes:
        return ResolucionCampo(
            campo=campo,
            motivo=(
                "ninguna fuente declaró el campo; no se inventa un valor "
                "(ADR-001). Queda registrado para el gate de conclusión (F5)."
            ),
        )

    ordenadas = [fuente for fuente in orden if fuente in declarantes]
    valores = {fuente: declarantes[fuente].valor for fuente in ordenadas}
    distintas = {valor for valor in valores.values()}
    acuerdo = len(distintas) == 1

    if acuerdo:
        ganador = ordenadas[0]
        regla = precedencia.regla
        motivo = (
            f"las fuentes coinciden en {valores[ganador]!r}; se registra como "
            f"responsable a la de mayor precedencia. {precedencia.motivo}"
        )
        return ResolucionCampo(
            campo=campo,
            ganador=ganador,
            regla=regla,
            motivo=motivo,
            acuerdo=True,
            descartadas=tuple(
                (fuente, valores[fuente]) for fuente in ordenadas[1:]
            ),
        )

    invalidas = tuple(
        fuente
        for fuente in ordenadas
        if not fuentes[fuente].valida
    )
    utilizables = [
        fuente for fuente in ordenadas if fuentes[fuente].valida
    ]

    if not utilizables:
        ganador = ordenadas[0]
        return ResolucionCampo(
            campo=campo,
            ganador=ganador,
            regla=precedencia.regla,
            motivo=(
                f"discrepan las fuentes y ninguna es utilizable (pasada 1, "
                f"T-403); se conserva {valores[ganador]!r} de {ganador.value} "
                f"como indicio, no como dato. {precedencia.motivo}"
            ),
            descartadas=tuple(
                (fuente, valores[fuente]) for fuente in ordenadas if fuente != ganador
            ),
            invalidas=invalidas,
            confiable=False,
        )

    ganador = utilizables[0]
    descartadas = tuple(
        (fuente, valores[fuente]) for fuente in ordenadas if fuente != ganador
    )
    detalle_invalidas = (
        f" Se descartaron por inválidas: {', '.join(f.value for f in invalidas)}."
        if invalidas
        else ""
    )
    return ResolucionCampo(
        campo=campo,
        ganador=ganador,
        regla=precedencia.regla,
        motivo=(
            f"discrepan las fuentes ({', '.join(f'{f.value}={valores[f]!r}' for f in ordenadas)}); "
            f"gana {ganador.value} por precedencia.{detalle_invalidas} "
            f"{precedencia.motivo}"
        ),
        descartadas=descartadas,
        invalidas=invalidas,
    )


# ---------------------------------------------------------------------------
# Combinación completa
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class CombinacionEvidencia:
    """Resultado de combinar las fuentes de una corrida (T-404).

    Campos:
        campos: ``campo -> CampoCombinado`` con las lecturas por fuente y la
            resolución (el contrato de F0).
        resoluciones: ``campo -> ResolucionCampo`` (la vista "rica": regla,
            motivo, descartadas, confiabilidad) para el reporte y los tests.
        fuentes: las fuentes que participaron, en orden canónico.
        documento_id: id del documento combinado.
    """

    campos: dict[str, CampoCombinado] = field(default_factory=dict)
    resoluciones: dict[str, ResolucionCampo] = field(default_factory=dict)
    fuentes: tuple[Fuente, ...] = ()
    documento_id: str = ""

    @property
    def campos_resueltos(self) -> dict[str, Any]:
        """``campo -> valor`` del ganador de cada campo (atajo para consumidores)."""
        return {
            campo: combinado.valor
            for campo, combinado in self.campos.items()
            if combinado.fuente is not None
        }

    @property
    def fuentes_por_campo(self) -> dict[str, Fuente]:
        """``campo -> fuente responsable`` del valor vigente (T-404)."""
        return {
            campo: combinado.fuente
            for campo, combinado in self.campos.items()
            if combinado.fuente is not None
        }

    @property
    def desacuerdos(self) -> dict[str, ResolucionCampo]:
        """Solo las resoluciones donde las fuentes **no** coincidieron."""
        return {
            campo: resolucion
            for campo, resolucion in self.resoluciones.items()
            if not resolucion.acuerdo and resolucion.ganador is not None
        }

    @property
    def no_confiables(self) -> dict[str, ResolucionCampo]:
        """Campos donde ganó una lectura pero ninguna fuente utilizable los declaró."""
        return {
            campo: resolucion
            for campo, resolucion in self.resoluciones.items()
            if not resolucion.confiable
        }


def combinar(
    fuentes: Mapping[Fuente, SourceEvidence] | Iterable[SourceEvidence],
    *,
    documento_id: str | None = None,
) -> CombinacionEvidencia:
    """Combina la evidencia de las fuentes y resuelve **campo a campo** (T-404).

    Es el corazón de ADR-002: junta las lecturas de cada campo en un
    :class:`~voucherflow.schemas.evidence.CampoCombinado` —conservando **todas**
    las fuentes, sin descartar información— y agrega la resolución por campo
    (:func:`resolver_campo`).

    El universo de campos es la **unión** de los que declaró cada fuente, en orden
    determinista: primero los del contrato de extracción
    (:data:`~voucherflow.extraction.prompt_extraccion.CAMPOS_EXTRACCION`, para que
    el orden sea el del prompt) y después los campos extra del modo genérico, en
    orden alfabético. Un campo que solo declaró una fuente también tiene su
    resolución (gana esa fuente, sin desacuerdo).

    Argumentos:
        fuentes: mapping ``fuente -> SourceEvidence`` o iterable de
            ``SourceEvidence`` (se indexa por ``source.fuente``). Solo las fuentes
            presentes participan.
        documento_id: id del documento; si se omite y las fuentes no lo traen, el
            llamador es responsable de completarlo (``combinar_evidencia`` lo
            recibe siempre).

    Devuelve:
        :class:`CombinacionEvidencia` con los campos combinados, las resoluciones
        y el orden de fuentes que participaron.
    """
    indice = _indexar(fuentes)
    campos = _universo_de_campos(indice)
    combinados: dict[str, CampoCombinado] = {}
    resoluciones: dict[str, ResolucionCampo] = {}

    for campo in campos:
        lecturas: dict[str, EvidenceField] = {}
        for fuente in ORDEN_CANONICO_FUENTES:
            source = indice.get(fuente)
            if source is None:
                continue
            evidencia = _campo_de(source, campo)
            if evidencia is not None:
                lecturas[fuente.value] = evidencia
        resolucion = resolver_campo(campo, indice)
        resoluciones[campo] = resolucion
        # ``valor``/``fuente`` son el atajo operativo: el valor vigente del campo
        # y quién lo sostiene (T-404). Sin esto, cada consumidor tendría que
        # re-derivar el ganador desde la resolución.
        ganador_evidencia = (
            lecturas.get(resolucion.ganador.value) if resolucion.ganador else None
        )
        combinados[campo] = CampoCombinado(
            **lecturas,
            resolucion=resolucion.como_field_resolution(),
            valor=None if ganador_evidencia is None else ganador_evidencia.valor,
            fuente=resolucion.ganador,
        )

    return CombinacionEvidencia(
        campos=combinados,
        resoluciones=resoluciones,
        fuentes=tuple(f for f in ORDEN_CANONICO_FUENTES if f in indice),
        documento_id=documento_id or "",
    )


def _indexar(
    fuentes: Mapping[Fuente, SourceEvidence] | Iterable[SourceEvidence],
) -> dict[Fuente, SourceEvidence]:
    """Indexa las fuentes por ``Fuente`` (acepta mapping o iterable; T-404)."""
    if isinstance(fuentes, Mapping):
        indice: dict[Fuente, SourceEvidence] = {}
        for clave, source in fuentes.items():
            fuente = clave if isinstance(clave, Fuente) else Fuente(clave)
            if not isinstance(source, SourceEvidence):
                raise TypeError(
                    "combinar(): el mapping debe mapear fuente -> SourceEvidence; "
                    f"recibido {type(source).__name__} (T-404)."
                )
            indice[fuente] = source
        return indice
    indice = {}
    for source in fuentes:
        if not isinstance(source, SourceEvidence):
            raise TypeError(
                "combinar(): el iterable debe contener SourceEvidence; recibido "
                f"{type(source).__name__} (T-404)."
            )
        indice[source.fuente] = source
    return indice


def _universo_de_campos(fuentes: Mapping[Fuente, SourceEvidence]) -> list[str]:
    """Campos a resolver: unión de los declarados, en orden determinista (T-404).

    Primero los del **contrato del prompt** (el orden en que se le piden al
    modelo, ver ``CAMPOS_EXTRACCION``), después los demás en orden alfabético (los
    del modo genérico ``kvg``). Así el resultado no depende del orden de las
    fuentes ni del orden de las claves del JSON.
    """
    declarados: set[str] = set()
    for source in fuentes.values():
        declarados.update(source.campos)

    from ..extraction.prompt_extraccion import CAMPOS_EXTRACCION  # noqa: PLC0415

    del_contrato = [campo for campo in CAMPOS_EXTRACCION if campo in declarados]
    restantes = sorted(declarados - set(del_contrato))
    return del_contrato + restantes


def resumen_combinacion(combinacion: CombinacionEvidencia) -> dict[str, Any]:
    """Resumen serializable de la combinación, para el detalle de la corrida (T-404).

    Publica: las fuentes que participaron, cuántos campos se resolvieron, cuántos
    por **acuerdo** y cuántos por **desacuerdo** (con la regla que los resolvió),
    qué campos quedaron **no confiables** y cuántas lecturas se descartaron por
    inválidas. Es la vista que consumen el reporte de la corrida y el gate de F5.
    """
    reglas: dict[str, int] = {}
    for resolucion in combinacion.resoluciones.values():
        if resolucion.ganador is None:
            continue
        reglas[resolucion.regla or ""] = reglas.get(resolucion.regla or "", 0) + 1
    return {
        "fuentes": [fuente.value for fuente in combinacion.fuentes],
        "campos_totales": len(combinacion.resoluciones),
        "campos_resueltos": len(combinacion.campos_resueltos),
        "acuerdos": sum(
            1 for r in combinacion.resoluciones.values() if r.acuerdo
        ),
        "desacuerdos": sorted(combinacion.desacuerdos),
        "no_confiables": sorted(combinacion.no_confiables),
        "lecturas_descartadas_por_invalidas": sum(
            len(r.invalidas) for r in combinacion.resoluciones.values()
        ),
        "reglas_aplicadas": dict(sorted(reglas.items())),
        "resoluciones": {
            campo: resolucion.como_dict()
            for campo, resolucion in combinacion.resoluciones.items()
            if not resolucion.acuerdo
        },
    }


__all__ = [
    "ORDEN_CANONICO_FUENTES",    "FUENTES_NO_LECTURA",    "FUENTES_NO_LECTURA",
    "FUENTES_LECTURA",
    "PREC_REGLA_DE_ORO",
    "PREC_LECTURA_VISUAL",
    "PREC_LECTURA_TEXTO",
    "PREC_DATO_COMPUTADO",
    "PrecedenciaCampo",
    "TABLA_PRECEDENCIA",
    "ResolucionCampo",
    "CombinacionEvidencia",
    "resolver_campo",
    "valor_de",
    "combinar",
    "resumen_combinacion",
]

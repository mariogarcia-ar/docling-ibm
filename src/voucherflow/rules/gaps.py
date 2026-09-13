"""Detección de gaps y búsqueda de evidencia adicional con límite (F5 / T-502).

**Fase**: F5 — Conclusión. **Tarea**: T-502 · **Épica**: E-CONC-2 · **ADR-003**.

El pseudocódigo de ``algoritmo.md`` lo dice en una línea:

    si resultado.faltan_datos:
        evidencia += buscar_evidencia_adicional(resultado.gaps, max_reintentos=N)
        resultado = aplicar_reglas_cruzadas(evidencia)

y el documento de ideas agrega la regla dura: *"con un objetivo concreto y un
límite de reintentos — esto no es un loop abierto de 'seguir buscando hasta
encontrar algo'"*.

Este módulo implementa las dos mitades del paso:

1. **La detección del gap** (:func:`detectar_gaps`): qué falta, por qué y **qué
   dato concreto lo cerraría**. No se busca "más evidencia" en general; se busca
   el campo que falta. La detección es 100% determinística y sin red, así que la
   suite default puede ejercitarla.
2. **La orquestación de la búsqueda** (:func:`buscar_evidencia_adicional`): pide
   cada gap a un **buscador inyectable** (:class:`BuscadorEvidencia`), con
   **presupuesto** (:class:`PresupuestoBusqueda`), y devuelve lo que se recuperó
   más la traza de lo que pasó con cada intento.

Decisiones de diseño (T-502)
----------------------------
1. **El gap es un campo, no una sensación.** Un `Gap` nombra el campo, su
   criticidad (¿bloquea el veredicto?) y **qué consulta** lo cerraría. Eso es lo
   que hace que la búsqueda sea "puntual por gap concreto" y no un barrido.
2. **Lo que no se puede buscar no se busca.** Un gap sin
   :attr:`Gap.buscable` (p. ej. un campo que solo puede venir del documento)
   queda registrado como **no buscable** en vez de disparar una consulta inútil.
   Es la forma de ser honesto con el límite del hook.
3. **El presupuesto es configurable y acotado** (ADR-003). ``max_reintentos`` y
   ``max_consultas`` se controlan por separado porque son límites distintos: uno
   protege contra el reintento de un proveedor que no responde, el otro contra
   el número total de consultas del caso (varios gaps × reintentos). Se agotan
   **en conjunto**: al llegar al tope, la búsqueda **termina** (no espera un
   milagro) y el caso sigue al paso siguiente del flujo.
4. **No hay loop abierto.** El bucle es acotado por construcción: cada intento
   consume presupuesto y el presupuesto agotado corta el bucle. Reintentar un
   gap que ya falló no es "seguir buscando hasta encontrar": es ``max_reintentos``
   intentos, y nada más.
5. **El buscador es inyectable** (:class:`BuscadorEvidencia`), como el
   :class:`~voucherflow.extraction.evidencia.Lector` de F4. Así la suite default
   corre sin red y la integración ARCA real es un adaptador opcional (ADR-003:
   el hook **no bloquea el MVP**).
6. **La búsqueda no decide.** Devuelve evidencia adicional (campos con su fuente
   y su sostén) y la traza; aplicarla y volver a concluir es de
   :mod:`voucherflow.conclusion.engine` (que re-aplica las cruzadas de T-501).

Referencias: doc 03 §4.5, `CONC.md` §1/§3, Gherkin E-CONC-2 ("no loop abierto"),
`algoritmo.md` paso 5, ADR-003, F5-subplan §3.2.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Mapping, Protocol, runtime_checkable

from ..schemas.evidence import EvidenceField, Fuente
from .contexto_conclusion import CAMPOS_CRITICOS, ContextoConclusion

#: Versión de la política de gaps/búsqueda (se registra en la trazabilidad).
VERSION_GAPS = "conclusion-gaps@1"

#: Niveles de criticidad de un gap. Un gap **bloqueante** impide dar un veredicto
#: con certeza alta; uno **informativo** no impide concluir pero mejora el caso.
CRITICIDAD_BLOQUEANTE = "bloqueante"
CRITICIDAD_INFORMATIVA = "informativa"

#: Resultado de intentar cubrir un gap.
INTENTO_CUBIERTO = "cubierto"
INTENTO_NO_DISPONIBLE = "no_disponible"
INTENTO_SIN_DATO = "sin_dato"
INTENTO_PRESUPUESTO_AGOTADO = "presupuesto_agotado"
INTENTO_NO_BUSCABLE = "no_buscable"

#: Motivo canónico de un gap que el sistema no puede ir a buscar.
MOTIVO_NO_BUSCABLE = (
    "El campo «{campo}» solo puede provenir del documento: no hay una fuente "
    "externa que lo aporte (ADR-003: se busca con un objetivo concreto, no "
    "'más evidencia' en general)."
)

#: Descripción por campo de **qué** consulta cierra el gap y **si** es buscable.
#:
#: La distinción es del negocio, no del motor (ADR-003: "definir con negocio qué
#: gaps son críticos"): el padrón ARCA/WSCDC puede **constatar** un comprobante
#: (tipo, número, fecha, importe, CUIT de emisor y receptor) pero **no** inventa
#: lo que el documento no dice — la descripción es de la lectura, y la letra o el
#: número salen del papel.
CATALOGO_GAPS: dict[str, dict[str, Any]] = {
    "tipo_comprobante": {
        "criticidad": CRITICIDAD_BLOQUEANTE,
        "objetivo": "constatar el tipo de comprobante del emisor en el padrón",
        "buscable": True,
        "fuente": Fuente.arca,
    },
    "nro_comprobante": {
        "criticidad": CRITICIDAD_BLOQUEANTE,
        "objetivo": "constatar el número del comprobante y su punto de venta",
        "buscable": True,
        "fuente": Fuente.arca,
    },
    "fecha_emision": {
        "criticidad": CRITICIDAD_BLOQUEANTE,
        "objetivo": "constatar la fecha de emisión declarada del comprobante",
        "buscable": True,
        "fuente": Fuente.arca,
    },
    "importe_total_facturado": {
        "criticidad": CRITICIDAD_BLOQUEANTE,
        "objetivo": "constatar el importe total del comprobante",
        "buscable": True,
        "fuente": Fuente.arca,
    },
    "cuit_emisor": {
        "criticidad": CRITICIDAD_BLOQUEANTE,
        "objetivo": "resolver el CUIT del emisor contra el padrón",
        "buscable": True,
        "fuente": Fuente.arca,
    },
    "cuit_receptor": {
        "criticidad": CRITICIDAD_BLOQUEANTE,
        "objetivo": "resolver el CUIT del receptor contra el padrón",
        "buscable": True,
        "fuente": Fuente.arca,
    },
    "descripcion": {
        "criticidad": CRITICIDAD_INFORMATIVA,
        "objetivo": "recuperar la descripción de los ítems del documento",
        "buscable": False,
        "fuente": None,
    },
    "razon_social_emisor": {
        "criticidad": CRITICIDAD_INFORMATIVA,
        "objetivo": "resolver la razón social del emisor contra el padrón",
        "buscable": True,
        "fuente": Fuente.arca,
    },
    "razon_social_receptor": {
        "criticidad": CRITICIDAD_INFORMATIVA,
        "objetivo": "resolver la razón social del receptor contra el padrón",
        "buscable": True,
        "fuente": Fuente.arca,
    },
}

#: Gap por defecto cuando el campo no está en :data:`CATALOGO_GAPS`: se lo trata
#: como **informativo y no buscable**. El default es deliberadamente conservador:
#: no se dispara una consulta externa por un campo que el catálogo no declara
#: (lo que no se conoce no se va a buscar).
GAP_POR_DEFECTO: dict[str, Any] = {
    "criticidad": CRITICIDAD_INFORMATIVA,
    "objetivo": "recuperar el campo «{campo}» del documento",
    "buscable": False,
    "fuente": None,
}


# ---------------------------------------------------------------------------
# El gap y la detección
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class Gap:
    """Un dato que falta para concluir, con su objetivo concreto (T-502).

    Campos:
        campo: el campo que falta (``"importe_total_facturado"``).
        criticidad: ``bloqueante`` (impide el veredicto con certeza alta) o
            ``informativa`` (mejora el caso pero no lo bloquea).
        objetivo: **qué consulta concreta** lo cerraría, en lenguaje legible —
            es el contrato de E-CONC-2 ("objetivo concreto por campo").
        buscable: ``False`` cuando ninguna fuente externa puede aportar el dato
            (p. ej. la descripción de los ítems, que solo está en el documento).
        fuente: la fuente que lo aportaría (:data:`Fuente.arca` para el padrón),
            o ``None`` si no es buscable.
    """

    campo: str
    criticidad: str = CRITICIDAD_INFORMATIVA
    objetivo: str = ""
    buscable: bool = False
    fuente: Fuente | None = None

    @property
    def bloqueante(self) -> bool:
        return self.criticidad == CRITICIDAD_BLOQUEANTE

    def como_dict(self) -> dict[str, Any]:
        return {
            "campo": self.campo,
            "criticidad": self.criticidad,
            "objetivo": self.objetivo,
            "buscable": self.buscable,
            "fuente": self.fuente.value if self.fuente else None,
        }


@dataclass
class DeteccionGaps:
    """Resultado de detectar los gaps de un caso (T-502).

    Campos:
        gaps: los gaps detectados, en orden de criticidad (bloqueantes primero)
            y luego alfabético (determinismo).
        campos_faltantes: todos los campos que faltan, salga o no un `Gap` con
            objetivo (es la vista cruda, para no perder ninguno).
        motivo: explicación legible del estado del caso.
    """

    gaps: list[Gap] = field(default_factory=list)
    campos_faltantes: list[str] = field(default_factory=list)
    motivo: str = ""

    @property
    def bloqueantes(self) -> list[Gap]:
        return [gap for gap in self.gaps if gap.bloqueante]

    @property
    def buscables(self) -> list[Gap]:
        return [gap for gap in self.gaps if gap.buscable]

    @property
    def hay_bloqueantes(self) -> bool:
        return any(gap.bloqueante for gap in self.gaps)

    def como_dict(self) -> dict[str, Any]:
        return {
            "version": VERSION_GAPS,
            "motivo": self.motivo,
            "campos_faltantes": list(self.campos_faltantes),
            "gaps": [gap.como_dict() for gap in self.gaps],
            "bloqueantes": [gap.campo for gap in self.bloqueantes],
            "buscables": [gap.campo for gap in self.buscables],
        }


def detectar_gaps(
    contexto: ContextoConclusion,
    *,
    catalogo: Mapping[str, Mapping[str, Any]] | None = None,
) -> DeteccionGaps:
    """Detecta qué falta para concluir y **qué consulta** cerraría cada falta.

    Es determinística y sin red: mira los campos ausentes del contexto
    (:attr:`ContextoConclusion.campos_ausentes`) y enriquece cada uno con su
    criticidad, su objetivo y si es buscable. Los **campos críticos** (los que
    :data:`~voucherflow.rules.contexto_conclusion.CAMPOS_CRITICOS` marca como
    necesarios para un veredicto con certeza alta) salen como bloqueantes.

    Un campo ausente que no esté en el catálogo igual se reporta (en
    ``campos_faltantes``) y produce un `Gap` informativo **no buscable**: no se
    pierde información, y tampoco se dispara una consulta por algo que el
    catálogo no sabe pedir.

    Lanza:
        ``TypeError`` si ``contexto`` no es un :class:`ContextoConclusion`.
    """
    if not isinstance(contexto, ContextoConclusion):
        raise TypeError(
            "detectar_gaps() espera un ContextoConclusion (la vista del caso "
            f"combinado, T-501); recibido: {type(contexto).__name__}."
        )

    tabla = catalogo if catalogo is not None else CATALOGO_GAPS
    faltantes = list(contexto.campos_ausentes)

    gaps: list[Gap] = []
    for campo in faltantes:
        entrada = tabla.get(campo) or {
            **GAP_POR_DEFECTO,
            "objetivo": GAP_POR_DEFECTO["objetivo"].format(campo=campo),
        }
        # La criticidad la manda el contrato de la conclusión: un campo crítico
        # es bloqueante aunque el catálogo no lo declare así (no hay dos
        # definiciones de "qué bloquea").
        criticidad = (
            CRITICIDAD_BLOQUEANTE if campo in CAMPOS_CRITICOS else entrada["criticidad"]
        )
        buscable = bool(entrada.get("buscable", False))
        fuente = entrada.get("fuente")
        gaps.append(
            Gap(
                campo=campo,
                criticidad=criticidad,
                objetivo=str(entrada.get("objetivo", "")),
                buscable=buscable,
                fuente=fuente if isinstance(fuente, Fuente) else None,
            )
        )

    gaps.sort(key=lambda gap: (not gap.bloqueante, gap.campo))
    bloqueantes = [gap for gap in gaps if gap.bloqueante]

    if not gaps:
        motivo = "El caso tiene todos los campos del contrato: no hay gaps."
    elif bloqueantes:
        motivo = (
            "Faltan campos críticos para concluir con certeza alta: "
            f"{', '.join(gap.campo for gap in bloqueantes)}."
        )
    else:
        motivo = (
            "Faltan campos informativos (no bloquean el veredicto): "
            f"{', '.join(gap.campo for gap in gaps)}."
        )

    return DeteccionGaps(gaps=gaps, campos_faltantes=faltantes, motivo=motivo)


# ---------------------------------------------------------------------------
# Presupuesto y buscador
# ---------------------------------------------------------------------------


@dataclass
class PresupuestoBusqueda:
    """Límite acotado de la búsqueda de evidencia adicional (ADR-003 / E-CONC-2).

    Dos topes distintos, porque protegen de dos cosas distintas:

    - ``max_consultas``: cuántas consultas **en total** puede hacer el caso
      (varios gaps × reintentos). Es el límite del "loop abierto".
    - ``max_reintentos``: cuántas veces se reintenta **el mismo** gap cuando el
      proveedor no responde (timeout/error). Es la resiliencia ante un proveedor
      intermitente, no una licencia para insistir.

    ``consultas`` cuenta lo consumido. Cuando se agota, la búsqueda **termina**:
    el caso sigue al paso siguiente del flujo (escalado a agente, T-504), no se
    queda esperando.
    """

    max_consultas: int = 3
    max_reintentos: int = 2
    consultas: int = 0

    def __post_init__(self) -> None:
        if self.max_consultas < 0:
            raise ValueError("max_consultas no puede ser negativo")
        if self.max_reintentos < 0:
            raise ValueError("max_reintentos no puede ser negativo")

    @property
    def agotado(self) -> bool:
        return self.consultas >= self.max_consultas

    @property
    def restantes(self) -> int:
        return max(0, self.max_consultas - self.consultas)

    def consumir(self) -> bool:
        """Consume una consulta. Devuelve ``False`` si ya no había presupuesto."""
        if self.agotado:
            return False
        self.consultas += 1
        return True

    def como_dict(self) -> dict[str, Any]:
        return {
            "max_consultas": self.max_consultas,
            "max_reintentos": self.max_reintentos,
            "consultas": self.consultas,
            "restantes": self.restantes,
            "agotado": self.agotado,
        }


@runtime_checkable
class BuscadorEvidencia(Protocol):
    """Protocolo del buscador de evidencia adicional (el "hook ARCA", ADR-003).

    Es el mismo criterio que el :class:`~voucherflow.extraction.evidencia.Lector`
    de F4: un objeto con un método, inyectable, para que la suite default corra
    **sin red** y la integración real (ARCA/WSCDC) sea un adaptador opcional.

    ``buscar`` recibe el **gap** (con su campo y su objetivo) y el **caso** (para
    que el buscador pueda usar lo que ya se sabe: el CUIT del emisor, el número
    del comprobante), y devuelve un :class:`ResultadoBusqueda`. No debe lanzar
    por un fallo esperable de red: eso se reporta como ``disponible=False`` para
    que el pipeline no se tumbe (ADR-003).
    """

    def buscar(self, gap: Gap, contexto: ContextoConclusion) -> "ResultadoBusqueda":
        """Consulta el dato del gap. Debe ser tolerante a fallos de red."""
        ...


@dataclass
class ResultadoBusqueda:
    """Lo que devuelve un buscador para un gap (T-502).

    ``disponible`` distingue "no se pudo consultar" (proveedor caído, sin
    credenciales, hook desactivado) de "se consultó y el dato no está". La
    diferencia importa para la auditoría: el primero es un límite de
    infraestructura, el segundo es una respuesta del mundo.

    Campos:
        campo: el campo consultado.
        valor: el valor recuperado (``None`` si no hay).
        disponible: ``False`` si no se pudo consultar.
        sostento: el fragmento/sostén del dato (ADR-001: sin sostén no es
            evidencia auditable).
        error: motivo legible cuando ``disponible`` es ``False``.
    """

    campo: str
    valor: Any = None
    disponible: bool = True
    sostento: str = ""
    error: str | None = None

    @property
    def ok(self) -> bool:
        """True si el dato se recuperó **con sostén** (evidencia auditable)."""
        return self.disponible and self.valor is not None and bool(self.sostento.strip())


@dataclass
class IntentoBusqueda:
    """Traza de un intento de cubrir un gap (uno por consulta).

    Campos:
        campo / objetivo: qué se buscó y con qué objetivo.
        resultado: uno de :data:`INTENTO_CUBIERTO`, :data:`INTENTO_NO_DISPONIBLE`,
            :data:`INTENTO_SIN_DATO`, :data:`INTENTO_PRESUPUESTO_AGOTADO`,
            :data:`INTENTO_NO_BUSCABLE`.
        intento: número de intento (1-based) para ese gap.
        valor: el valor recuperado, si lo hubo.
        motivo: explicación legible (incluye el error del proveedor cuando hubo).
    """

    campo: str
    objetivo: str = ""
    resultado: str = INTENTO_SIN_DATO
    intento: int = 1
    valor: Any = None
    motivo: str = ""

    def como_dict(self) -> dict[str, Any]:
        return {
            "campo": self.campo,
            "objetivo": self.objetivo,
            "resultado": self.resultado,
            "intento": self.intento,
            "valor": self.valor,
            "motivo": self.motivo,
        }


@dataclass
class ResultadoBusquedaAdicional:
    """Lo que se recuperó y lo que pasó con cada gap (T-502).

    Campos:
        campos: ``campo -> EvidenceField`` **listo para fusionar** en la
            evidencia combinada (con su fuente y su sostén, ADR-001).
        intentos: la traza de todos los intentos, en orden.
        presupuesto: el presupuesto consumido (serializable).
        motivo: resumen legible del resultado de la búsqueda.
    """

    campos: dict[str, EvidenceField] = field(default_factory=dict)
    intentos: list[IntentoBusqueda] = field(default_factory=list)
    presupuesto: dict[str, Any] = field(default_factory=dict)
    motivo: str = ""

    @property
    def cubiertos(self) -> list[str]:
        return sorted(self.campos)

    @property
    def hubo_busqueda(self) -> bool:
        return any(
            intento.resultado != INTENTO_NO_BUSCABLE for intento in self.intentos
        )

    def como_dict(self) -> dict[str, Any]:
        return {
            "version": VERSION_GAPS,
            "motivo": self.motivo,
            "cubiertos": self.cubiertos,
            "intentos": [intento.como_dict() for intento in self.intentos],
            "presupuesto": dict(self.presupuesto),
        }


# ---------------------------------------------------------------------------
# La búsqueda acotada
# ---------------------------------------------------------------------------


def buscar_evidencia_adicional(
    deteccion: DeteccionGaps,
    contexto: ContextoConclusion,
    *,
    buscador: BuscadorEvidencia | None,
    presupuesto: PresupuestoBusqueda | None = None,
) -> ResultadoBusquedaAdicional:
    """Busca el dato de cada gap buscable, con el presupuesto como tope (T-502).

    El bucle es **acotado por construcción** (E-CONC-2, "no loop abierto"):

    1. Recorre los gaps buscables, en orden (bloqueantes primero).
    2. Por cada gap consume **una** consulta del presupuesto; si ya no queda,
       el intento se registra como ``presupuesto_agotado`` y **la búsqueda
       termina** — no se sigue con los gaps siguientes buscando "suerte".
    3. Si el proveedor no respondió (``disponible=False``), reintenta ese mismo
       gap hasta ``max_reintentos`` **mientras quede presupuesto**. Al agotar
       los reintentos se anota ``no_disponible`` y se pasa al gap siguiente.
    4. Un gap **no buscable** no consume presupuesto ni dispara consultas:
       queda registrado con su motivo.

    Un ``buscador`` ``None`` (hook desactivado, ADR-003: el MVP no depende de
    ARCA) es un caso **normal**, no un error: los gaps buscables se registran
    como ``no_disponible`` con el motivo de que el hook está desactivado.

    Devuelve los campos recuperados (con fuente y sostén) más la traza completa.
    La fusión en la evidencia y la re-aplicación de las cruzadas son del motor
    (:func:`voucherflow.conclusion.engine.concluir_con_busqueda`), no de acá:
    este módulo **no decide**, solo busca y reporta.
    """
    if not isinstance(deteccion, DeteccionGaps):
        raise TypeError(
            "buscar_evidencia_adicional() espera una DeteccionGaps (la salida de "
            f"detectar_gaps()); recibido: {type(deteccion).__name__}."
        )

    limite = presupuesto if presupuesto is not None else PresupuestoBusqueda()
    resultado = ResultadoBusquedaAdicional(presupuesto=limite.como_dict())
    intenciones: dict[str, int] = {}

    for gap in deteccion.gaps:
        if not gap.buscable:
            resultado.intentos.append(
                IntentoBusqueda(
                    campo=gap.campo,
                    objetivo=gap.objetivo,
                    resultado=INTENTO_NO_BUSCABLE,
                    motivo=MOTIVO_NO_BUSCABLE.format(campo=gap.campo),
                )
            )
            continue

        if buscador is None:
            resultado.intentos.append(
                IntentoBusqueda(
                    campo=gap.campo,
                    objetivo=gap.objetivo,
                    resultado=INTENTO_NO_DISPONIBLE,
                    motivo=(
                        "El hook de evidencia adicional está desactivado (ADR-003: "
                        "no bloquea el MVP): no se consultó «"
                        f"{gap.campo}»."
                    ),
                )
            )
            continue

        # Reintentos del MISMO gap, acotados por max_reintentos y por el
        # presupuesto global: dos topes, dos protecciones distintas.
        for _ in range(1 + limite.max_reintentos):
            intenciones[gap.campo] = intenciones.get(gap.campo, 0) + 1
            numero = intenciones[gap.campo]

            if not limite.consumir():
                resultado.intentos.append(
                    IntentoBusqueda(
                        campo=gap.campo,
                        objetivo=gap.objetivo,
                        resultado=INTENTO_PRESUPUESTO_AGOTADO,
                        intento=numero,
                        motivo=(
                            "Presupuesto de consultas agotado "
                            f"({limite.max_consultas}): la búsqueda termina y el "
                            "caso sigue al paso siguiente del flujo (E-CONC-2: no "
                            "hay loop abierto)."
                        ),
                    )
                )
                resultado.presupuesto = limite.como_dict()
                resultado.motivo = _motivo_final(resultado, limite)
                return resultado

            respuesta = _consultar_seguro(buscador, gap, contexto)

            if respuesta.ok:
                resultado.campos[gap.campo] = _campo_de_evidencia(gap, respuesta)
                resultado.intentos.append(
                    IntentoBusqueda(
                        campo=gap.campo,
                        objetivo=gap.objetivo,
                        resultado=INTENTO_CUBIERTO,
                        intento=numero,
                        valor=respuesta.valor,
                        motivo=f"El gap «{gap.campo}» quedó cubierto con el padrón.",
                    )
                )
                break

            if not respuesta.disponible:
                # Proveedor caído/timeout: vale la pena reintentar (mientras
                # quede presupuesto y reintentos). Se registra el intento.
                resultado.intentos.append(
                    IntentoBusqueda(
                        campo=gap.campo,
                        objetivo=gap.objetivo,
                        resultado=INTENTO_NO_DISPONIBLE,
                        intento=numero,
                        motivo=respuesta.error
                        or f"El proveedor no respondió para «{gap.campo}».",
                    )
                )
                continue

            # Se consultó y el dato no está: reintentar NO va a cambiar la
            # respuesta del mundo. Se corta el gap (no se insiste de gusto).
            resultado.intentos.append(
                IntentoBusqueda(
                    campo=gap.campo,
                    objetivo=gap.objetivo,
                    resultado=INTENTO_SIN_DATO,
                    intento=numero,
                    motivo=(
                        f"Se consultó «{gap.campo}» y el dato no está disponible en "
                        "el padrón."
                    ),
                )
            )
            break

    resultado.presupuesto = limite.como_dict()
    resultado.motivo = _motivo_final(resultado, limite)
    return resultado


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _consultar_seguro(
    buscador: BuscadorEvidencia, gap: Gap, contexto: ContextoConclusion
) -> ResultadoBusqueda:
    """Consulta tolerando que el proveedor falle (ADR-003: no tumba el pipeline).

    Un adaptador de red puede lanzar por timeout, por credenciales o por un
    error de parseo; para el pipeline eso es "no disponible", no una excepción
    que aborte la conclusión del caso.
    """
    try:
        respuesta = buscador.buscar(gap, contexto)
    except Exception as exc:  # pragma: no cover - depende del adaptador real
        return ResultadoBusqueda(
            campo=gap.campo,
            disponible=False,
            error=f"El buscador falló al consultar «{gap.campo}»: {exc}",
        )

    if not isinstance(respuesta, ResultadoBusqueda):
        return ResultadoBusqueda(
            campo=gap.campo,
            disponible=False,
            error=(
                "El buscador devolvió "
                f"{type(respuesta).__name__} en vez de ResultadoBusqueda."
            ),
        )
    return respuesta


def _campo_de_evidencia(gap: Gap, respuesta: ResultadoBusqueda) -> EvidenceField:
    """Convierte el dato recuperado en un ``EvidenceField`` del contrato (F0).

    La fuente la declara el **catálogo** (el padrón aporta ``arca``), no el
    buscador: así el dato entra a la evidencia con la procedencia correcta para
    la precedencia (ADR-002: una fuente que no es lectura va por delante).
    """
    return EvidenceField(
        campo=gap.campo,
        valor=respuesta.valor,
        fuente=gap.fuente or Fuente.arca,
        fragmento_sustento=respuesta.sostento,
    )


def _motivo_final(
    resultado: ResultadoBusquedaAdicional, limite: PresupuestoBusqueda
) -> str:
    """Resumen legible de lo que pasó con la búsqueda."""
    cubiertos = resultado.cubiertos
    if cubiertos:
        return (
            f"Se cubrieron {len(cubiertos)} gap(s) con evidencia adicional: "
            f"{', '.join(cubiertos)} (consultas usadas: {limite.consultas}/"
            f"{limite.max_consultas})."
        )
    if limite.agotado:
        return (
            "La búsqueda agotó el presupuesto sin cubrir ningún gap: el caso sigue "
            "al paso siguiente del flujo (E-CONC-2: no hay loop abierto)."
        )
    return "La búsqueda no cubrió ningún gap: la evidencia adicional no aportó el dato."


__all__ = [
    "VERSION_GAPS",
    "CRITICIDAD_BLOQUEANTE",
    "CRITICIDAD_INFORMATIVA",
    "INTENTO_CUBIERTO",
    "INTENTO_NO_DISPONIBLE",
    "INTENTO_SIN_DATO",
    "INTENTO_PRESUPUESTO_AGOTADO",
    "INTENTO_NO_BUSCABLE",
    "MOTIVO_NO_BUSCABLE",
    "CATALOGO_GAPS",
    "GAP_POR_DEFECTO",
    "Gap",
    "DeteccionGaps",
    "PresupuestoBusqueda",
    "BuscadorEvidencia",
    "ResultadoBusqueda",
    "IntentoBusqueda",
    "ResultadoBusquedaAdicional",
    "detectar_gaps",
    "buscar_evidencia_adicional",
]

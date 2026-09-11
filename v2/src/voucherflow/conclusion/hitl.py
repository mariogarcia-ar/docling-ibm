"""Cola HITL y muestreo de auditoría (F5 / T-505, E-CONC-4 / ADR-004 / ADR-009).

**Fase**: F5 — Conclusión. **Tarea**: T-505 · **Épica**: E-CONC-4.

El HITL no es la bandeja de salida del pipeline: es la **autoridad final**. Todo
caso que no quedó de certeza alta por programa llega a revisión humana, y una
**muestra** de los que sí quedaron entra a auditoría. Sus correcciones son la
señal que alimenta el ajuste de reglas y prompts (`algoritmo.md` §8).

El Gherkin de E-CONC-4, implementado acá::

    Dado un caso de certeza baja (resuelto por agente IA)
    Cuando se consolida
    Entonces llega a revisión humana (HITL) con prioridad alta

    Dado un caso de certeza alta (resuelto por programa)
    Cuando se consolida
    Entonces entra en un muestreo de auditoría periódico con prioridad baja

    Regla: feedback
      Dado que el HITL corrige una decisión
      Cuando se registra la corrección
      Entonces la corrección queda disponible como señal para ajustar reglas y prompts

Qué entra a la cola (y con qué prioridad)
-----------------------------------------
La decisión la toma :func:`decidir_encolado`, que es **pura**:

===============  ====================  ===========  ==============================
caso             entra                 prioridad    por qué
===============  ====================  ===========  ==============================
certeza baja     siempre               ``alta``     no hay veredicto firme: revisión
                                                  obligatoria (Gherkin)
certeza alta     **solo si la muestra** ``baja``    muestreo de auditoría: detecta
 por programa     lo selecciona                      reglas que aciertan por accidente
                                                  (ADR-004 / R-03)
===============  ====================  ===========  ==============================

Decisiones de diseño (T-505)
----------------------------
1. **El muestreo es reproducible, no "aleatorio".** ADR-004 pide un muestreo
   "aleatorio estratificado configurable". Un ``random`` sin semilla haría
   **inauditable** la decisión de auditar: si mañana alguien pregunta por qué se
   revisó ese caso y no aquel, no habría respuesta. Acá la selección se deriva de
   una **semilla configurable + el id del documento**, así que es determinista,
   reproducible entre corridas y explicable caso por caso.
2. **La selección es estable**: el mismo documento con la misma semilla cae o no
   cae de la muestra siempre igual. Re-procesar un documento no cambia su suerte
   (eso sería un muestreo que se contradice a sí mismo).
3. **La corrección es un dato, no un texto libre.** El ``feedback`` registra el
   **campo**, el **valor anterior**, el **valor corregido** y quién/quándo, en un
   shape estable: es lo que un ajuste de reglas puede consumir. Un comentario
   suelto no es señal (no se puede agregar ni comparar).
4. **El origen de la corrección distingue las dos razones de auditar.** Un caso
   de certeza baja que el humano corrige dice "el agente se equivocó" (→ mover
   casuística del agente a reglas, R-09); uno de muestreo de certeza alta que se
   corrige dice "una regla acierta por accidente" (→ ajustar la regla, R-03).
   Mezclarlos perdería la señal para cada uno.
5. **La cola es una estructura de datos, no un archivo.** T-506 persiste
   ``CaseRecord``; ADR-009 pide además un índice SQLite para consultar ("listame
   los casos de certeza baja"). Acá se implementa la **cola en memoria** con las
   consultas que el review necesita; el store durable es de T-506/ADR-009.
6. **Sin red y sin dependencias nuevas.** La cola no habla con ningún modelo.

Referencias: ADR-004, ADR-009, Gherkin E-CONC-4, doc 03 §4.5, `CONC.md` §1/§3,
`algoritmo.md` §8, F5-subplan §3.5.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Iterable, Mapping

from ..schemas.evidence import Certeza, Origen
from ..schemas.result import HitlDecision, VoucherResult
from ..settings.config import HitlSettings, Settings

#: Versión de la política de HITL (se registra en la trazabilidad del caso).
VERSION_HITL = "conclusion-hitl@1"

#: Prioridades de la revisión (contrato de ``HitlDecision``).
PRIORIDAD_ALTA = "alta"
PRIORIDAD_BAJA = "baja"

#: Por qué entró el caso a la cola (el "motivo" de la revisión).
MOTIVO_CERTEZA_BAJA = "certeza_baja"
MOTIVO_MUESTREO_AUDITORIA = "muestreo_auditoria"

#: Escala del hash del muestreo: el id se mapea a un entero de ``[0, ESCALA)``.
ESCALA_MUESTREO = 1_000_000

#: Estados posibles de una entrada de la cola.
ESTADO_PENDIENTE = "pendiente"
ESTADO_REVISADO = "revisado"

#: Motivos canónicos (van a la traza y a la respuesta del encolado).
MOTIVO_NO_ENCOLA_ALTA = (
    "El caso quedó resuelto por el código con certeza alta y la muestra de "
    "auditoría no lo seleccionó: no entra a la cola. El muestreo es la red que "
    "detecta reglas que aciertan por accidente (ADR-004); no seleccionar un caso "
    "no significa que esté mal."
)
MOTIVO_ENCOLA_BAJA = (
    "El caso no quedó de certeza alta por programa: la revisión humana es "
    "obligatoria (Gherkin E-CONC-4). Prioridad alta."
)
MOTIVO_ENCOLA_MUESTREO = (
    "El caso quedó de certeza alta por programa y la muestra de auditoría lo "
    "seleccionó (ADR-004). Prioridad baja: es un chequeo periódico de que las "
    "reglas no acierten por accidente."
)
#: Motivo (código) de un caso que no entra por tener la revisión desactivada.
MOTIVO_REVISION_DESACTIVADA = "revision_desactivada"
MOTIVO_NO_APLICA = "no_aplica"

#: Explicación legible del caso que no entra por política desactivada.
EXPLICACION_REVISION_DESACTIVADA = (
    "La revisión obligatoria de certeza baja está desactivada por configuración "
    "(``revision_obligatoria_certeza_baja=False``). Es una política deliberada y "
    "riesgosa: el caso queda sin veredicto firme y sin nadie que lo mire. Se "
    "declara explícitamente en vez de silenciarse."
)


# ---------------------------------------------------------------------------
# El muestreo (determinista y auditable)
# ---------------------------------------------------------------------------


def seleccionado_para_auditoria(
    documento_id: str,
    *,
    tasa: float,
    semilla: int = 0,
) -> bool:
    """Decide si un caso de certeza alta entra a la muestra de auditoría (ADR-004).

    Reproducible a propósito: el "azar" se deriva de ``(semilla, documento_id)``
    por hash, no de un ``random`` global. Así:

    - el mismo documento con la misma semilla cae siempre igual (re-procesar no
      cambia la suerte del caso);
    - el **motivo** de auditar ese caso es explicable: ``hash % escala < tasa ×
      escala``, con los números a la vista;
    - la tasa es una **fracción** configurable (0.10 = 10%, la sugerencia del
      ADR-004 y la que mitiga R-03).

    ``tasa <= 0`` no selecciona nada y ``tasa >= 1`` selecciona todo: los extremos
    son válidos y explícitos (muestreo apagado o auditoría total).
    """
    if tasa <= 0.0:
        return False
    if tasa >= 1.0:
        return True

    digest = hashlib.sha256(f"{semilla}:{documento_id}".encode("utf-8")).hexdigest()
    # Se usa el entero del hash en el rango ``[0, ESCALA_MUESTREO)``.
    valor = int(digest[:12], 16) % ESCALA_MUESTREO
    return valor < int(tasa * ESCALA_MUESTREO)


# ---------------------------------------------------------------------------
# La decisión de encolar (pura)
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class Encolado:
    """Decisión de revisión humana para un caso (T-505).

    Campos:
        requerido: si el caso va a revisión (obligatoria por certeza baja o por
            haber caído en la muestra de auditoría).
        prioridad: ``alta`` (revisión obligatoria) o ``baja`` (auditoría).
        motivo: ``certeza_baja`` o ``muestreo_auditoria``.
        explicacion: por qué, en lenguaje legible (para la traza y el revisor).
        seleccionado_muestreo: si el caso cayó en la muestra (aunque no fuera
            elegible: se publica para poder auditar el muestreo).
    """

    requerido: bool
    prioridad: str
    motivo: str
    explicacion: str
    seleccionado_muestreo: bool = False

    def como_dict(self) -> dict[str, Any]:
        return {
            "requerido": self.requerido,
            "prioridad": self.prioridad,
            "motivo": self.motivo,
            "seleccionado_muestreo": self.seleccionado_muestreo,
            "explicacion": self.explicacion,
        }


def decidir_encolado(
    resultado: VoucherResult,
    *,
    hitl: HitlSettings | None = None,
) -> Encolado:
    """Decide si un caso va a revisión humana y con qué prioridad (T-505).

    Es **pura y determinista** (sin red, sin estado): el mismo resultado con la
    misma política decide siempre lo mismo.

    - **certeza baja** (lo resolvió el agente, o el código no pudo) → **siempre**
      a revisión, prioridad **alta** (Gherkin E-CONC-4: "revisión obligatoria").
    - **certeza alta por programa** → solo entra si la **muestra de auditoría** lo
      selecciona, prioridad **baja** (ADR-004).
    """
    politica = hitl if hitl is not None else HitlSettings()

    # ¿Cae en la muestra? Se calcula siempre: es lo que permite auditar el
    # muestreo (también para los casos que no eran elegibles).
    en_muestra = seleccionado_para_auditoria(
        resultado.documento_id,
        tasa=politica.muestreo_tasa,
        semilla=politica.muestreo_semilla,
    )

    es_alta_por_programa = (
        resultado.certeza == Certeza.alta
        and resultado.origen == Origen.programa
    )

    if not es_alta_por_programa:
        # Certeza baja (agente/HITL/nadie) → revisión obligatoria.
        #
        # El flag permite desactivarla, pero es una política deliberada y
        # riesgosa: dejar un caso sin veredicto firme sin que nadie lo mire es
        # justamente lo que el patrón prohíbe. Si se desactiva, el caso solo
        # entraría por muestreo (y un caso de certeza baja no es un caso
        # "resuelto por programa", así que normalmente no se audita).
        if politica.revision_obligatoria_certeza_baja:
            return Encolado(
                requerido=True,
                prioridad=PRIORIDAD_ALTA,
                motivo=MOTIVO_CERTEZA_BAJA,
                explicacion=MOTIVO_ENCOLA_BAJA,
                seleccionado_muestreo=en_muestra,
            )
        return Encolado(
            requerido=False,
            prioridad=PRIORIDAD_ALTA,
            motivo=MOTIVO_REVISION_DESACTIVADA,
            explicacion=EXPLICACION_REVISION_DESACTIVADA,
            seleccionado_muestreo=en_muestra,
        )

    if politica.muestreo_activo and en_muestra:
        return Encolado(
            requerido=True,
            prioridad=PRIORIDAD_BAJA,
            motivo=MOTIVO_MUESTREO_AUDITORIA,
            explicacion=MOTIVO_ENCOLA_MUESTREO,
            seleccionado_muestreo=True,
        )

    return Encolado(
        requerido=False,
        prioridad=PRIORIDAD_BAJA,
        motivo=MOTIVO_NO_APLICA,
        explicacion=MOTIVO_NO_ENCOLA_ALTA,
        seleccionado_muestreo=en_muestra,
    )


# ---------------------------------------------------------------------------
# La corrección humana (feedback)
# ---------------------------------------------------------------------------


@dataclass
class Correccion:
    """Una corrección humana sobre un campo del resultado (T-505, feedback).

    Es el dato que alimenta el ajuste de reglas y prompts (`algoritmo.md` §8). Se
    registra **estructurado** (campo / antes / después / quién / cuándo) en lugar
    de un texto libre: así se puede agregar entre casos (¿qué campo se corrige
    más?) y comparar contra la decisión original.

    Campos:
        campo: el campo corregido (``tipo_comprobante``, ``categoria_gasto``…).
        valor_anterior: lo que el sistema había decidido.
        valor_nuevo: lo que el revisor considera correcto.
        motivo: por qué se corrigió (opcional pero recomendado: es la señal).
        revisor: quién corrigió (trazabilidad E-CONC-5).
        timestamp: cuándo (ISO-8601 UTC).
    """

    campo: str
    valor_anterior: Any = None
    valor_nuevo: Any = None
    motivo: str = ""
    revisor: str | None = None
    timestamp: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())

    def como_dict(self) -> dict[str, Any]:
        return {
            "campo": self.campo,
            "valor_anterior": self.valor_anterior,
            "valor_nuevo": self.valor_nuevo,
            "motivo": self.motivo,
            "revisor": self.revisor,
            "timestamp": self.timestamp,
        }


# ---------------------------------------------------------------------------
# La entrada de la cola
# ---------------------------------------------------------------------------


@dataclass
class EntradaHitl:
    """Un caso en la cola de revisión humana (T-505).

    Campos:
        documento_id: el caso.
        prioridad: ``alta`` (revisión obligatoria) o ``baja`` (auditoría).
        motivo: por qué entró (``certeza_baja`` / ``muestreo_auditoria``).
        explicacion: por qué, legible.
        estado: ``pendiente`` o ``revisado``.
        certeza / origen: cómo lo resolvió el sistema (contexto del revisor).
        tipo_comprobante: la letra vigente, para que el revisor vea qué corregir.
        correcciones: las correcciones registradas (feedback).
        timestamp_alta / timestamp_revision: cuándo entró y cuándo se revisó.
    """

    documento_id: str
    prioridad: str = PRIORIDAD_ALTA
    motivo: str = MOTIVO_CERTEZA_BAJA
    explicacion: str = ""
    estado: str = ESTADO_PENDIENTE
    certeza: str | None = None
    origen: str | None = None
    tipo_comprobante: str | None = None
    correcciones: list[Correccion] = field(default_factory=list)
    timestamp_alta: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())
    timestamp_revision: str | None = None

    @property
    def revisado(self) -> bool:
        return self.estado == ESTADO_REVISADO

    @property
    def corregido(self) -> bool:
        """True si el revisor corrigió algo (no solo confirmó)."""
        return bool(self.correcciones)

    def como_dict(self) -> dict[str, Any]:
        return {
            "documento_id": self.documento_id,
            "prioridad": self.prioridad,
            "motivo": self.motivo,
            "explicacion": self.explicacion,
            "estado": self.estado,
            "certeza": self.certeza,
            "origen": self.origen,
            "tipo_comprobante": self.tipo_comprobante,
            "correcciones": [c.como_dict() for c in self.correcciones],
            "timestamp_alta": self.timestamp_alta,
            "timestamp_revision": self.timestamp_revision,
        }

    def como_hitl_decision(self) -> HitlDecision:
        """La ``HitlDecision`` del contrato de F0 (lo que viaja en el resultado).

        Es la proyección de la entrada de la cola al contrato congelado: el
        consumidor del ``VoucherResult`` ve ``requerido``/``prioridad``/``estado``
        y la **última** corrección (el contrato tiene un solo ``correccion``), sin
        necesidad de conocer la cola.
        """
        return HitlDecision(
            requerido=True,
            prioridad=self.prioridad,
            estado=self.estado,
            correccion=self.correcciones[-1].como_dict() if self.correcciones else {},
        )


# ---------------------------------------------------------------------------
# La cola
# ---------------------------------------------------------------------------


class ColaHitl:
    """Cola de revisión humana en memoria (T-505; el store durable es T-506).

    Mantiene las entradas indexadas por documento y ofrece las consultas que el
    revisor necesita ("qué está pendiente", "qué es obligatorio", "qué corregí").
    ADR-009 pide además un índice SQLite para consultas agregadas: acá se
    implementa la cola y sus consultas; la persistencia durable es T-506.
    """

    def __init__(self, *, hitl: HitlSettings | None = None) -> None:
        self.hitl = hitl if hitl is not None else HitlSettings()
        self._entradas: dict[str, EntradaHitl] = {}
        self._orden: list[str] = []

    # -- alta ------------------------------------------------------------

    def encolar(
        self, resultado: VoucherResult, *, hitl: HitlSettings | None = None
    ) -> EntradaHitl | None:
        """Evalúa un caso y lo encola si corresponde. Devuelve la entrada (o ``None``).

        Si el caso **no** entra a revisión, devuelve ``None``: no se crea una
        entrada "vacía" que después haya que filtrar. Si ya estaba encolado, se
        **actualiza** la entrada existente (el mismo documento no se duplica: la
        cola es por caso, no por corrida).
        """
        decision = decidir_encolado(resultado, hitl=hitl or self.hitl)
        if not decision.requerido:
            return None

        existente = self._entradas.get(resultado.documento_id)
        if existente is not None:
            # Se conserva el estado y las correcciones: re-encolar un caso ya
            # revisado no debe borrar el trabajo humano.
            existente.prioridad = decision.prioridad
            existente.motivo = decision.motivo
            existente.explicacion = decision.explicacion
            existente.certeza = (
                resultado.certeza.value if resultado.certeza else None
            )
            existente.origen = resultado.origen.value if resultado.origen else None
            existente.tipo_comprobante = resultado.tipo_comprobante
            return existente

        entrada = EntradaHitl(
            documento_id=resultado.documento_id,
            prioridad=decision.prioridad,
            motivo=decision.motivo,
            explicacion=decision.explicacion,
            certeza=resultado.certeza.value if resultado.certeza else None,
            origen=resultado.origen.value if resultado.origen else None,
            tipo_comprobante=resultado.tipo_comprobante,
        )
        self._entradas[resultado.documento_id] = entrada
        self._orden.append(resultado.documento_id)
        return entrada

    # -- consultas -------------------------------------------------------

    def pendientes(self) -> list[EntradaHitl]:
        """Los casos que esperan revisión, en orden de prioridad (alta primero).

        El orden importa: R-09 ("el agente escala de más y carga el HITL") se
        mitiga con una **cola priorizada**, no con una pila. Dentro de la misma
        prioridad se respeta el orden de llegada (estable).
        """
        return [
            self._entradas[doc]
            for doc in self._orden
            if not self._entradas[doc].revisado
            and self._entradas[doc].prioridad == PRIORIDAD_ALTA
        ] + [
            self._entradas[doc]
            for doc in self._orden
            if not self._entradas[doc].revisado
            and self._entradas[doc].prioridad == PRIORIDAD_BAJA
        ]

    def obligatorios(self) -> list[EntradaHitl]:
        """Los casos de **revisión obligatoria** (certeza baja, prioridad alta)."""
        return [
            self._entradas[doc]
            for doc in self._orden
            if self._entradas[doc].motivo == MOTIVO_CERTEZA_BAJA
        ]

    def muestreados(self) -> list[EntradaHitl]:
        """Los casos que entraron por **muestreo de auditoría** (ADR-004)."""
        return [
            self._entradas[doc]
            for doc in self._orden
            if self._entradas[doc].motivo == MOTIVO_MUESTREO_AUDITORIA
        ]

    def corregidos(self) -> list[EntradaHitl]:
        """Los casos que el revisor **corrigió** (señal de feedback)."""
        return [self._entradas[doc] for doc in self._orden if self._entradas[doc].corregido]

    def entrada(self, documento_id: str) -> EntradaHitl | None:
        return self._entradas.get(documento_id)

    def __len__(self) -> int:
        return len(self._entradas)

    def __contains__(self, documento_id: object) -> bool:
        return documento_id in self._entradas

    # -- revisión --------------------------------------------------------

    def registrar_correccion(
        self,
        documento_id: str,
        *,
        campo: str,
        valor_nuevo: Any,
        valor_anterior: Any = None,
        motivo: str = "",
        revisor: str | None = None,
    ) -> EntradaHitl:
        """Registra una corrección humana y marca el caso como revisado (T-505).

        Es el momento en que el HITL **ejerce su autoridad**: la corrección queda
        en la entrada (estructurada, lista para agregar) y el caso pasa a
        ``revisado``. No se aplica silenciosamente al ``VoucherResult``: el
        resultado original queda con su ``HitlDecision`` y el feedback vive en la
        traza (E-CONC-5), que es lo que permite auditar **qué** decidió el sistema
        y **qué** corrigió el humano.

        Lanza:
            ``KeyError`` si el documento no está en la cola: registrar una
            corrección de un caso que nunca se encoló sería un feedback huérfano.
            ``ValueError`` si ``campo`` es vacío.
        """
        if not campo or not str(campo).strip():
            raise ValueError("El campo corregido es obligatorio (feedback sin campo no es señal).")
        if documento_id not in self._entradas:
            raise KeyError(
                f"El documento {documento_id!r} no está en la cola HITL: no se puede "
                "registrar una corrección de un caso que no se encoló."
            )

        entrada = self._entradas[documento_id]
        entrada.correcciones.append(
            Correccion(
                campo=str(campo).strip(),
                valor_anterior=valor_anterior,
                valor_nuevo=valor_nuevo,
                motivo=motivo,
                revisor=revisor,
            )
        )
        entrada.estado = ESTADO_REVISADO
        entrada.timestamp_revision = datetime.now(timezone.utc).isoformat()
        return entrada

    def confirmar(self, documento_id: str, *, revisor: str | None = None) -> EntradaHitl:
        """Marca el caso como revisado **sin** corrección (el revisor confirmó).

        Es información distinta de "corregido": una confirmación dice "la regla
        acertó", que es exactamente lo que el muestreo de auditoría quiere saber
        (ADR-004). Sin esta distinción, la ausencia de correcciones sería ambigua
        entre "no se revisó" y "se revisó y estaba bien".
        """
        if documento_id not in self._entradas:
            raise KeyError(f"El documento {documento_id!r} no está en la cola HITL.")
        entrada = self._entradas[documento_id]
        entrada.estado = ESTADO_REVISADO
        entrada.timestamp_revision = datetime.now(timezone.utc).isoformat()
        return entrada

    # -- feedback --------------------------------------------------------

    def feedback(self) -> dict[str, Any]:
        """El **feedback** agregado de la cola: la señal para ajustar reglas y prompts.

        Es lo que el Gherkin pide que "quede disponible". Se separa por **motivo
        de la revisión**, porque las dos razones de auditar dicen cosas distintas:

        - correcciones de casos de **certeza baja** → el **agente** se equivocó:
          la señal para mover esa casuística a reglas (R-09);
        - correcciones de casos de **muestreo** (certeza alta) → una **regla**
          acierta por accidente: la señal para ajustarla (R-03).

        Se incluye también qué **campos** se corrigen más, que es el agregado más
        accionable (¿estamos leyendo mal la letra? ¿el tipo de gasto?).
        """
        campos: dict[str, int] = {}
        for entrada in self.corregidos():
            for correccion in entrada.correcciones:
                campos[correccion.campo] = campos.get(correccion.campo, 0) + 1

        return {
            "version": VERSION_HITL,
            "revisados": sum(1 for e in self._entradas.values() if e.revisado),
            "pendientes": len(self.pendientes()),
            "corregidos": len(self.corregidos()),
            # Confirmados: revisados sin corrección (la regla acertó).
            "confirmados": sum(
                1 for e in self._entradas.values() if e.revisado and not e.corregido
            ),
            "correcciones_por_campo": dict(sorted(campos.items())),
            "correcciones_de_certeza_baja": [
                c.como_dict()
                for e in self.obligatorios()
                for c in e.correcciones
            ],
            "correcciones_de_muestreo": [
                c.como_dict()
                for e in self.muestreados()
                for c in e.correcciones
            ],
            "nota": (
                "Feedback T-505 (E-CONC-4): las correcciones quedan disponibles "
                "como señal para ajustar reglas y prompts. Las de certeza baja "
                "señalan errores del agente (R-09); las del muestreo de certeza "
                "alta, reglas que aciertan por accidente (R-03)."
            ),
        }

    def resumen(self) -> dict[str, Any]:
        """Resumen serializable de la cola (para la traza y el reporte)."""
        return {
            "version": VERSION_HITL,
            "total": len(self._entradas),
            "pendientes": len(self.pendientes()),
            "obligatorios": len(self.obligatorios()),
            "muestreados": len(self.muestreados()),
            "corregidos": len(self.corregidos()),
            "politica": {
                "muestreo_tasa": self.hitl.muestreo_tasa,
                "muestreo_semilla": self.hitl.muestreo_semilla,
                "muestreo_activo": self.hitl.muestreo_activo,
            },
            "entradas": [e.como_dict() for e in self.pendientes()],
        }


# ---------------------------------------------------------------------------
# API de alto nivel
# ---------------------------------------------------------------------------


def encolar_hitl(
    resultado: VoucherResult,
    *,
    cola: ColaHitl | None = None,
    settings: Settings | None = None,
) -> VoucherResult:
    """Aplica la política HITL al resultado y lo encola si corresponde (T-505).

    Implementación de la firma que dejó el esqueleto de F0
    (``encolar_hitl(resultado) -> VoucherResult``): devuelve el **mismo
    resultado** con su ``HitlDecision`` actualizada según la cola (requerido,
    prioridad, estado). La cola —si se pasa— queda con la entrada registrada para
    el revisor.

    Es determinística y **sin red**.

    Argumentos:
        resultado: el ``VoucherResult`` consolidado (T-503).
        cola: la cola donde registrar la entrada. ``None`` calcula la decisión sin
            registrarla (útil para decidir sin efectos).
        settings: de dónde sale la política HITL. Default: la de la cola o los
            valores por defecto de ``HitlSettings``.

    Devuelve:
        El ``VoucherResult`` con la ``HitlDecision`` de la política aplicada.
    """
    politica = settings.hitl if settings is not None else (cola.hitl if cola else None)
    decision = decidir_encolado(resultado, hitl=politica)

    if cola is not None:
        entrada = cola.encolar(resultado, hitl=politica)
        if entrada is not None:
            resultado.hitl = entrada.como_hitl_decision()
        else:
            resultado.hitl = HitlDecision(
                requerido=False, prioridad=PRIORIDAD_BAJA, estado="no_aplica"
            )
    else:
        resultado.hitl = HitlDecision(
            requerido=decision.requerido,
            prioridad=decision.prioridad,
            estado=ESTADO_PENDIENTE if decision.requerido else "no_aplica",
        )

    resultado.trazabilidad["hitl"] = {
        **decision.como_dict(),
        "version": VERSION_HITL,
        "nota": (
            "Política HITL T-505 (E-CONC-4 / ADR-004): certeza baja → revisión "
            "obligatoria (prioridad alta); certeza alta por programa → muestreo "
            "de auditoría reproducible (prioridad baja). El muestreo se deriva de "
            "(semilla, documento_id) para que sea auditable."
        ),
    }
    return resultado


def encolar_lote(
    resultados: Iterable[VoucherResult],
    *,
    cola: ColaHitl | None = None,
    settings: Settings | None = None,
) -> ColaHitl:
    """Aplica la política HITL a un lote de resultados (T-505).

    Es la forma natural de procesar una corrida completa: devuelve la cola con
    todos los casos que correspondan, ya priorizada. El muestreo es reproducible,
    así que aplicar la política a un lote o caso por caso da el mismo resultado.
    """
    destino = cola if cola is not None else ColaHitl(
        hitl=settings.hitl if settings is not None else None
    )
    for resultado in resultados:
        encolar_hitl(resultado, cola=destino, settings=settings)
    return destino


__all__ = [
    "VERSION_HITL",
    "PRIORIDAD_ALTA",
    "PRIORIDAD_BAJA",
    "MOTIVO_CERTEZA_BAJA",
    "MOTIVO_MUESTREO_AUDITORIA",
    "ESTADO_PENDIENTE",
    "ESTADO_REVISADO",
    "ESCALA_MUESTREO",
    "MOTIVO_NO_ENCOLA_ALTA",
    "MOTIVO_ENCOLA_BAJA",
    "MOTIVO_ENCOLA_MUESTREO",
    "MOTIVO_REVISION_DESACTIVADA",
    "MOTIVO_NO_APLICA",
    "EXPLICACION_REVISION_DESACTIVADA",
    "Encolado",
    "Correccion",
    "EntradaHitl",
    "ColaHitl",
    "seleccionado_para_auditoria",
    "decidir_encolado",
    "encolar_hitl",
    "encolar_lote",
]

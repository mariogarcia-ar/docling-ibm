"""Reglas **raw** por fuente de lectura (F3 / T-303, épica E-CLAS-1).

**Fase**: F3 (clasificación) · **Tarea**: T-303 · **Épica**: E-CLAS-1.

Qué son las reglas raw
----------------------
La **pasada 1** del pipeline (doc 03 §4.3/§4.4): antes de que la lectura de una
fuente (VLM o LLM) se use como evidencia, se la **califica**. Las reglas raw **no
deciden** la letra — eso es del motor R1-R7 (T-301) — solo responden:

- ¿la fuente es **coherente consigo misma** (lo que declaró se sostiene con el
  fragmento que citó)?
- ¿qué **debilidades** tiene (letra fuera de vocabulario, sin sustento, fuente
  incompleta, contradicción entre el texto y la letra declarada)?

Su salida es :class:`VeredictoRaw` (``fuente``, ``valida``, ``reglas_aplicadas``,
``debilidades``, ``candidatos_descartados``, ``candidatos_restantes``) y es la
que puebla ``SourceEvidence.valida`` / ``SourceEvidence.debilidades`` /
``SourceEvidence.reglas_aplicadas`` (contrato congelado de F0, ADR-001).

Se implementa **una sola vez** acá (F3-subplan §3.3) porque F4/T-403 la reutiliza
para la pasada 1 de extracción; el registro es genérico sobre **campos
declarados**, no específico de la letra de comprobante.

Por qué el registro es por campo y no sobre el dominio del comprobante
----------------------------------------------------------------------
F4 necesita las mismas reglas sobre **cualquier** campo extraído (CUIT, fecha,
total, razón social). Por eso el registro trabaja sobre *valores declarados*
(``CampoDeclarado``), no sobre letras: la letra es un caso particular de valor
con vocabulario cerrado y con una "expresión esperada" en el sustento.

Tipos de regla (``tipo`` de la ``Rule`` de F0, valor ``"raw"``)
--------------------------------------------------------------
======================================  ====================================
Regla                                   Qué verifica
======================================  ====================================
``RAW_CAMPO``                           falta el valor o el fragmento de
                                        sustento (fuente incompleta)
``RAW_VOCABULARIO``                     el valor declarado no pertenece al
                                        vocabulario declarado del campo
``RAW_SUSTENTO``                        el fragmento no contiene el valor
                                        declarado (no lo sostiene)
``RAW_CONTRADICCION``                   el fragmento contiene **otro** valor
                                        del vocabulario (contradicción)
======================================  ====================================

Las cuatro son ``Rule`` declarativas (``rules/registry.py``, contrato F0
congelado) evaluadas con ``Registry``, tal como pide RULES.md §1 ("cada regla
reporta id, condición evaluada y resultado; orden por prioridad").

Gradación: ``valida`` / ``dudosa`` / ``invalida``
-------------------------------------------------
``SourceEvidence.valida`` es booleano, así que el veredicto expone una
:class:`Gravedad` interna y la degrada:

- ``valida`` → ``SourceEvidence.valida = True`` (sin debilidades).
- ``dudosa`` → ``SourceEvidence.valida = True`` **con** debilidades: la fuente se
  puede usar, pero con reservas (su lectura queda con certeza baja). Es el caso
  del LLM que reporta una letra que su fragmento no sostiene: el dato sirve como
  indicio, no como prueba.
- ``invalida`` → ``SourceEvidence.valida = False``: la evidencia no es
  utilizable (violación de contrato — el valor ni siquiera está en el
  vocabulario del campo).

Hallazgo documentado (v1 / prompt WIP)
--------------------------------------
El `11.1` de v1 usaba una regex **estricta** ``FACTURA\\s+([A-CME])`` para leer
la letra del texto, de modo que un texto que dijera ``FACTURA X`` no producía
coincidencia y la letra quedaba sin detectar (el caso caía a inferencia o a "sin
letra"). La fuente `grep` de ``v1/document_extraction.py`` documenta ese
comportamiento. Con las reglas raw, en cambio, el hecho de que el texto cite una
letra **distinta** de la declarada ya no se pierde: se registra como
**contradicción** (``RAW_CONTRADICION``) y la letra contradictoria pasa a
``candidatos_descartados`` (o a ``restantes`` si es la única lectura disponible y
por lo tanto no se puede tachar). Es información auditable que la regex estricta
descartaba en silencio.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Iterable, Mapping, Sequence

from .registry import Registry, Rule

# ---------------------------------------------------------------------------
# Vocabulario de gravedad y severidad
# ---------------------------------------------------------------------------


class Gravedad(str, Enum):
    """Gravedad del veredicto raw de una fuente (T-303).

    Es interna al módulo: hacia afuera solo se expone
    ``SourceEvidence.valida`` (bool) + ``debilidades``. La distinción existe para
    no tratar igual "el valor es basura" (``invalida``: no se puede usar) y "el
    valor puede servir pero no está probado" (``dudosa``: se usa con reservas).
    """

    valida = "valida"
    dudosa = "dudosa"
    invalida = "invalida"


#: Orden de severidad para acumular el veredicto (mayor = más grave).
_ORDEN_GRAVEDAD: dict[Gravedad, int] = {
    Gravedad.valida: 0,
    Gravedad.dudosa: 1,
    Gravedad.invalida: 2,
}


def _mas_grave(actual: Gravedad, nueva: Gravedad) -> Gravedad:
    """Devuelve la gravedad más severa de las dos (acumulación monotonía)."""
    return nueva if _ORDEN_GRAVEDAD[nueva] > _ORDEN_GRAVEDAD[actual] else actual


# ---------------------------------------------------------------------------
# Motivos (mensajes legibles, en español, con referencia a la regla)
# ---------------------------------------------------------------------------

MOTIVO_SIN_VALOR = (
    "La fuente no declaró un valor para el campo {campo} (fuente incompleta); "
    "no se puede validar nada con esta lectura."
)
MOTIVO_SIN_SUSTENTO = (
    "La fuente declaró {valor!r} en {campo} pero no citó fragmento de "
    "sustento; la lectura no es auditable (ADR-001: sin sustento la evidencia "
    "no se puede verificar)."
)
MOTIVO_FUERA_VOCABULARIO = (
    "La fuente declaró {valor!r} en {campo}, fuera del vocabulario admitido "
    "{vocabulario}; el valor se descarta y no puede sostener la decisión."
)
MOTIVO_SIN_SOSTEN = (
    "La fuente declaró {valor!r} en {campo} pero su fragmento de sustento "
    "({fragmento!r}) no contiene ese valor; el dato queda como indicio, no como "
    "prueba."
)
MOTIVO_CONTRADICCION = (
    "El fragmento de sustento de {campo} contiene {encontrado!r}, que "
    "contradice el valor declarado {declarado!r}; se registra la contradicción "
    "para auditoría (la lectura prevalece como indicio, ver T-303)."
)
MOTIVO_INCONSISTENCIA = (
    "La evidencia de la fuente es internamente inconsistente: declara "
    "{campo}={valor!r} pero {detalle} La fuente queda debilitada antes de "
    "combinarse (T-403/E-EXT-2)."
)
MOTIVO_INCONSISTENCIA_FALTANTE = (
    "La evidencia de la fuente es internamente inconsistente: declara "
    "{campo}={valor!r} pero no leyó {faltantes}, que ese tipo de comprobante "
    "exige. Queda como indicio incompleto hasta contrastarla con la otra fuente "
    "(T-403/E-EXT-2)."
)


# ---------------------------------------------------------------------------
# Entradas del registro raw
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class ImplicacionCoherencia:
    """Una implicación que una fuente debe cumplir **consigo misma** (F4/T-403).

    Modela los requisitos que un valor de un campo (``disparador``: la letra
    ``"A"``) impone sobre **otros** campos de esa misma fuente. Se evalúa sobre el
    conjunto de campos (no campo por campo), así que vive acá y no como ``Rule``
    del registro: es el punto de extensión que E-EXT-2 necesita para marcar a una
    fuente "internamente inconsistente antes de combinarse".

    Campos:
        disparador: valor **normalizado** del campo que activa la implicación
            (``"A"``). Se compara con el valor normalizado del campo que la
            declara (así ``"a"`` y ``"A"`` son lo mismo).
        motivo: explicación legible del requisito, con su origen (regla/prompt).
        requeridos: campos que **deben estar declarados** para que la fuente sea
            coherente (``("cuit_emisor", "cuit_receptor")`` para una Factura A).
            Un requerido ausente se reporta como **inconsistencia por
            implicación abierta** — no se inventa el dato, pero la fuente queda
            como indicio incompleto (ver :func:`violaciones_de_coherencia`).
        incompatibles: ``campo -> valores admitidos``. Si el campo afectado fue
            **declarado** con un valor que **no** está en la lista, la fuente se
            contradice (``{"iva": (0, "0")}`` para una Factura B, que no
            discrimina IVA: declarar ``2100.5`` en ``iva`` es la incoherencia).
            Se modela como "valores admitidos" y no como "valores prohibidos"
            porque es como lo expresa la regla: *la letra B admite IVA en cero*.
        pendientes: campos que la fuente **debe declarar para poder evaluar** la
            implicación. Mientras no se hayan declarado, la implicación **no se
            juzga** (ni se cumple ni se viola). Es la diferencia con
            ``requeridos``: un campo afectado simplemente ausente (p. ej. el
            ``razon_social_receptor``) no es un requisito del tipo de
            comprobante, así que no se reporta; el CUIT del receptor **sí** lo es
            para una Factura A y por eso va en ``requeridos``.

    Ejemplo (Factura A, E-EXT-2)::

        ImplicacionCoherencia(
            disparador="A",
            motivo="una Factura A discrimina IVA y exige CUIT de emisor y receptor.",
            requeridos=("cuit_emisor", "cuit_receptor"),
        )
    """

    disparador: Any
    motivo: str
    requeridos: tuple[str, ...] = ()
    incompatibles: Mapping[str, Any] = field(default_factory=dict)
    pendientes: tuple[str, ...] = ()


@dataclass(frozen=True)
class CampoDeclarado:
    """Un campo con el valor que una fuente declaró y su sustento (T-303).

    Es la entrada del motor raw: deliberadamente **agnóstico del dominio** (sirve
    para la letra de comprobante en F3 y para CUIT/fecha/total en F4/T-403).

    Campos:
        campo: nombre del campo (p. ej. ``tipo_detectado_por_documento``).
        valor: valor declarado por la fuente (cualquier tipo serializable; los
            ``None``/vacíos se tratan como ausencia).
        fragmento: fragmento de sustento que la fuente citó como prueba.
            Insumo de las reglas de sostenibilidad/contradicción.
        vocabulario: valores admitidos del campo. Si es ``None``, no se aplica
            la regla de vocabulario (campo de texto libre, p. ej. una razón
            social). Si es una tupla/frozenset, un valor fuera de ella es
            violación de contrato.
        normalizador: función opcional ``valor -> valor normalizado`` aplicada
            **antes** de comparar contra el vocabulario y el fragmento. Permite
            que "a" y "A" sean el mismo valor sin que el registro sepa del
            dominio (el llamador pasa ``normalizar_letra``).
        patron_sustento: regex opcional que debe **aparecer** en el fragmento
            para sostener el valor. Se usa para exigir una expresión concreta
            (p. ej. ``FACTURA\\s+A`` en vez de la letra suelta). Si es ``None``,
            el sostén se busca con el valor normalizado dentro del fragmento.
        exigir_sustento: si ``True``, la falta de fragmento es una debilidad.
            Default ``True`` (ADR-001: la evidencia necesita sustento).
        nota: aclaración opcional del llamador que se **agrega** al motivo que
            produce la regla. Sirve para explicar la consecuencia concreta en el
            dominio sin que el registro raw sepa del dominio (p. ej. "el motor no
            podrá confirmar la letra con la regex de R5").
        normalizador_valor: función ``valor -> valor canónico`` opcional, aplicada
            **además** del ``normalizador``, solo para el sostén y la
            presentación de los mensajes. Es el punto de extensión de
            **F4/T-403**: el `valor` sigue siendo el **crudo** (la regla de
            vocabulario necesita ver lo que la fuente dijo, T-301), pero el
            sostén se evalúa contra la forma canónica del valor
            (``30123456789`` ← ``30-12345678-9``). Si es ``None``, el valor
            normalizado es el que pasa el ``normalizador``.
        sostenedor: predicado opcional ``(valor_normalizado, fragmento) -> bool``
            consultado como **último** recurso del sostén (:func:`valor_sostenido`,
            punto 4). Es el punto de extensión de **F4/T-403** para los campos
            cuyo valor y fragmento están escritos en formatos distintos y no
            comparables por contención (``12345.67`` vs. ``"$ 12.345,67"``,
            ``2025-08-14`` vs. ``"14/08/2025"``): el llamador sabe cómo
            reconocerlos y el registro sigue sin saber del dominio. Si es
            ``None``, no hay respaldo: lo que la contención no sostiene es una
            debilidad.
        coherencia: implicaciones que la fuente debe cumplir **consigo misma**
            (ver :class:`ImplicacionCoherencia`). Es el punto de extensión de
            **F4/T-403** para E-EXT-2: la regla se evalúa sobre el **conjunto**
            de campos de la fuente, por eso vive en el campo y no en una ``Rule``
            que ve un campo por vez.
    """

    campo: str
    valor: Any = None
    fragmento: str = ""
    vocabulario: Sequence[str] | frozenset[str] | None = None
    normalizador: Any = None
    patron_sustento: re.Pattern[str] | None = None
    exigir_sustento: bool = True
    nota: str = ""
    normalizador_valor: Any = None
    sostenedor: Any = None
    coherencia: tuple["ImplicacionCoherencia", ...] = ()

    def normalizar(self) -> Any:
        """Valor normalizado (aplica ``normalizador`` si se declaró).

        Es la forma que usan la regla de **vocabulario** y la de **contradicción**
        (comparaciones cerradas, T-303): unifica la capitalización sin cambiar el
        valor.
        """
        if self.normalizador is not None and self.valor is not None:
            return self.normalizador(self.valor)
        return self.valor

    def valor_canonico(self) -> Any:
        """Forma **canónica** del valor, solo para el sostén (F4/T-403).

        Aplica ``normalizador`` y después ``normalizador_valor``: un CUIT
        declarado como ``"30-12345678-9"`` se compara con el fragmento como
        ``"30123456789"``, de modo que el fragmento sostiene al valor aunque el
        OCR lo haya impreso con separadores (y al revés). El valor **publicado**
        en la evidencia sigue siendo el crudo (T-402): esto es solo para comparar.
        """
        normalizado = self.normalizar()
        if self.normalizador_valor is not None and normalizado is not None:
            try:
                return self.normalizador_valor(normalizado)
            except Exception:  # noqa: BLE001 - un normalizador roto no rompe la pasada
                return normalizado
        return normalizado

    @property
    def valor_presente(self) -> bool:
        """True si hay un valor declarado (no ``None`` ni string vacío)."""
        if self.valor is None:
            return False
        if isinstance(self.valor, str) and not self.valor.strip():
            return False
        return True

    @property
    def sustento_presente(self) -> bool:
        """True si el fragmento de sustento tiene contenido."""
        return bool(self.fragmento and self.fragmento.strip())


@dataclass
class VeredictoRaw:
    """Resultado de la pasada 1 de reglas raw sobre **una** fuente (T-303).

    Campos:
        fuente: etiqueta de la fuente (``vlm``/``llm``/…). Trazabilidad.
        valida: ``True`` salvo gravedad ``invalida``. Es el valor que se copia a
            ``SourceEvidence.valida``.
        gravedad: :class:`Gravedad` del veredicto (``valida``/``dudosa``/
            ``invalida``). No sale del módulo; se usa para derivar ``valida`` y
            para reportes.
        reglas_aplicadas: ids de las reglas raw disparadas (trazabilidad
            E-CONC-5), en orden de prioridad.
        debilidades: motivos legibles de las debilidades detectadas (se copia a
            ``SourceEvidence.debilidades``). Incluye las violaciones de
            coherencia entre campos de la fuente (F4/T-403, ``RAW_COHERENCIA``),
            que no son ``Rule`` porque miran el conjunto y no un campo por vez.
        candidatos_descartados: valores del vocabulario **descartados** por
            contradicción (la fuente citó otro valor) o por estar fuera del
            vocabulario.
        candidatos_restantes: valores del vocabulario que **siguen vivos** (el
            declarado, y en ausencia de declaración las alternativas del
            vocabulario).
        incoherencias: campos de la fuente que quedaron internamente
            inconsistentes con otro campo que la misma fuente declaró (F4/T-403).
            Lista vacía = la fuente es coherente consigo misma (o no declaró lo
            suficiente para poder juzgarlo).
        reglas_por_campo: ``campo -> ids de las reglas raw que se dispararon por
            ese campo`` (F4/T-404). Lo consume la combinación para explicar, en la
            resolución, **con qué regla** quedó debilitada la fuente que no gana
            (la alternativa sería reportar la lista de reglas de la fuente entera,
            que no dice qué campo falla).
    """

    fuente: str
    valida: bool = True
    gravedad: Gravedad = Gravedad.valida
    reglas_aplicadas: list[str] = field(default_factory=list)
    debilidades: list[str] = field(default_factory=list)
    candidatos_descartados: list[str] = field(default_factory=list)
    candidatos_restantes: list[str] = field(default_factory=list)
    incoherencias: list[str] = field(default_factory=list)
    reglas_por_campo: dict[str, list[str]] = field(default_factory=dict)

    @property
    def es_valida(self) -> bool:
        """Alias explícito de :attr:`valida` (lectura del contrato F0)."""
        return self.valida

    @property
    def sin_debilidades(self) -> bool:
        """True si el veredicto no registró ninguna debilidad."""
        return not self.debilidades


# ---------------------------------------------------------------------------
# Evaluadores de cada regla raw (funciones puras sobre CampoDeclarado)
# ---------------------------------------------------------------------------


def _en_vocabulario(campo: CampoDeclarado) -> bool:
    """True si el valor pertenece al vocabulario declarado (o no hay vocabulario)."""
    if campo.vocabulario is None:
        return True
    return campo.normalizar() in set(campo.vocabulario)


def condicion_raw_campo(campo: CampoDeclarado) -> bool:
    """RAW_CAMPO — la fuente está incompleta (sin valor o sin sustento).

    Es el invariante de ADR-001: una evidencia sin valor o sin sustento no es
    auditable.
    """
    if not campo.valor_presente:
        return True
    return campo.exigir_sustento and not campo.sustento_presente


def condicion_raw_vocabulario(campo: CampoDeclarado) -> bool:
    """RAW_VOCABULARIO — el valor declarado no pertenece al vocabulario del campo."""
    if not campo.valor_presente or campo.vocabulario is None:
        return False
    return not _en_vocabulario(campo)


def coincidencias_en_sustento(campo: CampoDeclarado) -> list[str]:
    """Valores **del vocabulario** que aparecen en el fragmento de sustento.

    Es el corazón de RAW_SUSTENTO / RAW_CONTRADICCION: se busca en el fragmento
    cada valor admitido del campo (o el patrón declarado) y se devuelve lo que
    realmente se encontró, en orden de aparición.

    Si ``patron_sustento`` está declarado, se usa esa expresión para exigir una
    forma concreta (p. ej. ``FACTURA\\s+A``): cuenta como coincidencia solo el
    valor cuyo grupo capturado aparezca, y si el patrón existe y captura, los
    valores encontrados se restringen a lo capturado.

    Devuelve lista vacía si no hay vocabulario (no se puede buscar sin él) o si
    el fragmento está vacío.
    """
    if campo.vocabulario is None or not campo.sustento_presente:
        return []
    vocabulario = [str(v) for v in campo.vocabulario]

    if campo.patron_sustento is not None:
        encontrados: list[str] = []
        for coincidencia in campo.patron_sustento.finditer(campo.fragmento):
            capturado = None
            if campo.patron_sustento.groups:
                for grupo in coincidencia.groups():
                    if grupo:
                        capturado = grupo
                        break
            if capturado is None:
                capturado = coincidencia.group(0)
            capturado_norm = (
                campo.normalizador(capturado) if campo.normalizador else capturado
            )
            if campo.vocabulario is not None and capturado_norm in set(campo.vocabulario):
                if capturado_norm not in encontrados:
                    encontrados.append(capturado_norm)
        return encontrados

    # Sin patrón: se busca cada valor del vocabulario como palabra suelta
    # (fronteras de palabra para no tomar la "A" de "FACTURA").
    #
    # Cuidado con los valores de **un solo carácter** (letras de comprobante):
    # en un texto en español el artículo/preposición ``a`` y la conjunción ``e``
    # coinciden con las letras ``A``/``E`` del vocabulario, así que buscar
    # insensible a mayúsculas daría falsos positivos ("junto **a** COD. 006"
    # haría creer que el fragmento sostiene la letra A). Para los valores de un
    # solo carácter se exige coincidencia **exacta** (mayúscula), que es la
    # forma en que los comprobantes escriben la letra; los valores más largos
    # se buscan sin distinguir mayúsculas (una razón social puede venir en
    # cualquier capitalización).
    encontrados = []
    for valor in vocabulario:
        flags = 0 if len(valor) == 1 else re.IGNORECASE
        if re.search(rf"(?<!\w){re.escape(valor)}(?!\w)", campo.fragmento, flags):
            encontrados.append(valor)
    return encontrados


def valor_sostenido(campo: CampoDeclarado) -> bool:
    """True si el fragmento **sostiene** el valor declarado (RAW_SUSTENTO).

    Tres formas de sostén, en este orden:

    1. ``patron_sustento`` declarado → el fragmento debe contener esa expresión
       (p. ej. ``FACTURA\\s+([A-CME])``), que exige la forma concreta y no la
       letra suelta.
    2. ``vocabulario`` declarado (sin patrón) → el valor declarado debe estar
       entre los valores del vocabulario que aparecen en el fragmento.
    3. Sin vocabulario ni patrón (campo de texto libre, p. ej. una razón social
       o un CUIT) → se busca el valor como **texto literal** dentro del
       fragmento. Es el caso de F4/T-403, donde hay campos sin vocabulario
       cerrado; sin este fallback, todo campo libre se reportaba como no
       sostenido.

    Cuando el campo declara ``sostenedor`` (F4/T-403), ese predicado es la
    **autoridad** del sostén: sabe reconocer las formas equivalentes del valor
    (``12345.67`` en ``"$ 12.345,67"``, ``30123456789`` en
    ``"C.U.I.T. 30-12345678-9"``). Se consulta **antes** que la contención
    literal, porque la contención sola daría falsos positivos justo en los campos
    donde el formato importa: ``"1.234,56"`` está contenido en
    ``"Ajuste: 1.234,56-"``, pero ese fragmento sostiene ``-1234.56``, no
    ``1234.56``.
    """
    if campo.vocabulario is not None:
        declarado = campo.normalizar()
        if declarado is None:
            return False
        return declarado in coincidencias_en_sustento(campo)
    canonico = campo.valor_canonico()
    if canonico is None:
        return False
    if campo.sostenedor is not None:
        try:
            return bool(campo.sostenedor(canonico, campo.fragmento))
        except Exception:  # noqa: BLE001 - un sostenedor roto no rompe la pasada
            return False
    return str(canonico).casefold() in campo.fragmento.casefold()


def condicion_raw_sustento(campo: CampoDeclarado) -> bool:
    """RAW_SUSTENTO — el fragmento no contiene (ni sostiene) el valor declarado.

    Silencio deliberado cuando el valor no está en el vocabulario: de eso se
    ocupa RAW_VOCABULARIO (evita duplicar la misma debilidad dos veces). También
    calla si no hay fragmento: eso ya es RAW_CAMPO.
    """
    if not campo.valor_presente or not campo.sustento_presente:
        return False
    if not _en_vocabulario(campo):
        return False
    return not valor_sostenido(campo)


def condicion_raw_contradiccion(campo: CampoDeclarado) -> bool:
    """RAW_CONTRADICCION — el fragmento sostiene **otro** valor del vocabulario.

    Es la señal que la regex estricta de v1 perdía: el texto cita una letra
    distinta de la declarada. No cambia el valor declarado (la lectura sigue
    siendo el indicio), pero registra la contradicción y alimenta los candidatos
    descartados.
    """
    if not campo.valor_presente or not campo.sustento_presente:
        return False
    if not _en_vocabulario(campo):
        return False
    declarado = campo.normalizar()
    return any(valor != declarado for valor in coincidencias_en_sustento(campo))


# ---------------------------------------------------------------------------
# Coherencia entre campos de una misma fuente (F4/T-403, E-EXT-2)
# ---------------------------------------------------------------------------


def _valores(colector: Any) -> tuple[Any, ...]:
    """Normaliza un colector de valores (escalar, tupla, frozenset o ``None``).

    Un valor escalar se trata como colección de uno: ``"A"`` y ``("A",)`` son lo
    mismo. Es lo que permite declarar una implicación legible
    (``("IVA", "iva", "0", "…")``) sin obligar a envolver cada valor.
    """
    if colector is None:
        return ()
    if isinstance(colector, (str, bytes)):
        return (colector,)
    if isinstance(colector, Iterable):
        return tuple(colector)
    return (colector,)


def _compara(campo: CampoDeclarado | None, valor: Any) -> bool:
    """True si el campo declaró ``valor`` (comparando con su normalizador).

    La comparación usa el ``normalizador`` del campo (el mismo que la regla de
    vocabulario), de modo que ``"a"`` y ``"A"`` son el mismo valor sin que la
    coherencia sepa del dominio. Sin normalizador, la comparación es la igualdad
    directa.
    """
    if campo is None or not campo.valor_presente:
        return False
    return campo.normalizar() == valor


def violaciones_de_coherencia(
    campos: Mapping[str, CampoDeclarado],
) -> list[str]:
    """Implicaciones que la fuente violó **por sí sola** (F4/T-403, E-EXT-2).

    Implementa la validación de coherencia que pide E-EXT-2: una fuente
    internamente inconsistente (dice ``"A"`` pero no leyó los dos CUIT que esa
    letra exige, o declara IVA discriminado en una ``"B"``) queda **debilitada
    antes de combinarse** con la otra. Las implicaciones las declara el llamador
    en :attr:`CampoDeclarado.coherencia` (ver :class:`ImplicacionCoherencia`), así
    que el registro sigue siendo agnóstico del dominio: acá solo se evalúan.

    Semántica (documentada a propósito, para que no haya ambigüedad):

    * La implicación se evalúa solo si el campo que la declara normaliza al
      ``disparador`` (``"A"``).
    * **Requeridos**: cada campo de ``requeridos`` que la fuente **no declaró**
      produce una violación (implicación abierta: la letra exige ese dato y la
      fuente no lo trajo). Es exactamente el caso de E-EXT-2 — "dice Factura A
      pero no detectó los dos CUIT que esa letra exige".
    * **Incompatibles**: si el campo afectado fue declarado con un valor de la
      lista, la fuente se contradice.
    * **Pendientes**: mientras falte alguno, la implicación no se juzga (ni
      cumple ni viola). Sirve para declarar requisitos que solo tienen sentido si
      la fuente intentó evaluarlos.

    Argumentos:
        campos: mapping ``nombre -> CampoDeclarado`` de **una** fuente.

    Devuelve:
        Motivos legibles (uno por violación, en orden determinista: campo
        disparador, luego requeridos y después incompatibles) listos para
        ``debilidades``. Vacía si la fuente es coherente o si no se puede juzgar.
    """
    motivos: list[str] = []
    for nombre, campo in campos.items():
        if not campo.coherencia:
            continue
        disparado = campo.normalizar()
        for implicacion in campo.coherencia:
            if disparado != implicacion.disparador:
                continue
            if any(c in campos for c in implicacion.pendientes) and not all(
                c in campos for c in implicacion.pendientes
            ):
                continue  # evaluación incompleta: no se juzga
            faltantes = [
                requerido
                for requerido in implicacion.requeridos
                if requerido not in campos
            ]
            if faltantes:
                motivos.append(
                    MOTIVO_INCONSISTENCIA_FALTANTE.format(
                        campo=nombre,
                        valor=campo.valor,
                        faltantes=", ".join(faltantes),
                    )
                    + f" {implicacion.motivo}"
                )
            for admitido, valores_admitidos in implicacion.incompatibles.items():
                campo_afectado = campos.get(admitido)
                if campo_afectado is None or not campo_afectado.valor_presente:
                    continue  # no declarado: no se inventa ni se castiga
                if campo_afectado.normalizar() in _valores(valores_admitidos):
                    continue  # el valor declarado es uno de los admitidos
                motivos.append(
                    MOTIVO_INCONSISTENCIA.format(
                        campo=nombre,
                        valor=campo.valor,
                        detalle=(
                            f"declara {admitido}={campo_afectado.valor!r}, cuando "
                            f"admite {sorted(str(v) for v in _valores(valores_admitidos))}, y "
                            f"{implicacion.motivo}"
                        ),
                    )
                )
    return motivos


# ---------------------------------------------------------------------------
# Registro de reglas raw
# ---------------------------------------------------------------------------

#: Prioridad de las reglas raw. El orden importa: primero el contrato
#: (incompletitud/vocabulario) y después la calidad del sustento (sostén y
#: contradicción), de modo que un valor fuera de vocabulario no arrastre además
#: una queja de sostén.
PRIORIDAD_RAW_CAMPO = 1
PRIORIDAD_RAW_VOCABULARIO = 2
PRIORIDAD_RAW_SUSTENTO = 3
PRIORIDAD_RAW_CONTRADICCION = 4

#: Id con el que se **reporta** la coherencia entre campos de una fuente
#: (F4/T-403). No es una ``Rule`` del registro —las ``Rule`` ven un campo por
#: vez y la coherencia mira el conjunto— pero sí viaja en
#: ``VeredictoRaw.reglas_aplicadas`` para que la trazabilidad (E-CONC-5) nombre
#: la validación que se aplicó.
ID_RAW_COHERENCIA = "RAW_COHERENCIA"

#: Gravedad de la coherencia: es **dudosa**, no inválida. Una fuente
#: inconsistente aporta un indicio contradictorio, no un valor que no se pueda
#: usar: quien decide si la fuente sirve es el veredicto combinado (T-404).
GRAVEDAD_RAW_COHERENCIA = Gravedad.dudosa


def construir_registro_raw() -> Registry:
    """Construye el :class:`Registry` de reglas raw (T-303).

    Cada regla es una ``Rule`` declarativa del motor de F0 (``registry.py``, no
    se reescribe) con ``tipo="raw"``. El ``detalle`` es la descripción legible
    que aparece en auditoría.

    Devuelve un registro **nuevo** en cada llamada (mismo patrón que
    ``construir_registros()`` de T-301) para que nadie mute el global
    compartido.
    """
    registro = Registry()
    registro.registrar(
        Rule(
            id="RAW_CAMPO",
            prioridad=PRIORIDAD_RAW_CAMPO,
            condicion=condicion_raw_campo,
            resultado=Gravedad.dudosa,
            tipo="raw",
            detalle=(
                "La fuente está incompleta: no declaró valor o no citó fragmento "
                "de sustento (ADR-001; doc 03 §4.3 reglas raw)."
            ),
        )
    )
    registro.registrar(
        Rule(
            id="RAW_VOCABULARIO",
            prioridad=PRIORIDAD_RAW_VOCABULARIO,
            condicion=condicion_raw_vocabulario,
            resultado=Gravedad.invalida,
            tipo="raw",
            detalle=(
                "El valor declarado no pertenece al vocabulario admitido del "
                "campo; la evidencia no es utilizable."
            ),
        )
    )
    registro.registrar(
        Rule(
            id="RAW_SUSTENTO",
            prioridad=PRIORIDAD_RAW_SUSTENTO,
            condicion=condicion_raw_sustento,
            resultado=Gravedad.dudosa,
            tipo="raw",
            detalle=(
                "El fragmento de sustento no contiene el valor declarado; el dato "
                "queda como indicio, no como prueba (ADR-001)."
            ),
        )
    )
    registro.registrar(
        Rule(
            id="RAW_CONTRADICCION",
            prioridad=PRIORIDAD_RAW_CONTRADICCION,
            condicion=condicion_raw_contradiccion,
            resultado=Gravedad.dudosa,
            tipo="raw",
            detalle=(
                "El fragmento de sustento cita otro valor del vocabulario, que "
                "contradice el declarado; se registra para auditoría."
            ),
        )
    )
    return registro


#: Registro por defecto (copia de trabajo). Los consumidores deberían usar
#: :func:`construir_registro_raw` para obtener una instancia propia.
REGISTRO_RAW: Registry = construir_registro_raw()

#: Gravedad que aporta cada regla cuando se dispara.
GRAVEDAD_POR_REGLA: dict[str, Gravedad] = {
    "RAW_CAMPO": Gravedad.dudosa,
    "RAW_VOCABULARIO": Gravedad.invalida,
    "RAW_SUSTENTO": Gravedad.dudosa,
    "RAW_CONTRADICCION": Gravedad.dudosa,
}


# ---------------------------------------------------------------------------
# Evaluación (pasada 1)
# ---------------------------------------------------------------------------


def _valor_legible(campo: CampoDeclarado) -> Any:
    """Valor del campo para los mensajes: el normalizado, o el crudo si no hay.

    Cuando el normalizador descarta el valor (p. ej. ``normalizar_letra("Z")``
    devuelve ``None``), el normalizado no sirve para explicar qué pasó: el
    mensaje debe citar lo que la fuente **realmente declaró** (``"Z"``), no
    ``None``.
    """
    normalizado = campo.normalizar()
    return campo.valor if normalizado is None else normalizado


def _construir_motivo(regla: Rule, campo: CampoDeclarado) -> str:
    """Mensaje legible de la debilidad que produce ``regla`` sobre ``campo``.

    Si el llamador declaró ``CampoDeclarado.nota``, se agrega al final: es el
    lugar donde vive la consecuencia específica del dominio (el registro raw no
    la conoce).
    """
    motivo = _motivo_base(regla, campo)
    return f"{motivo} {campo.nota.strip()}" if campo.nota.strip() else motivo


def _motivo_base(regla: Rule, campo: CampoDeclarado) -> str:
    """Mensaje base de la regla, sin la nota del llamador."""
    normalizado = _valor_legible(campo)
    if regla.id == "RAW_CAMPO":
        if not campo.valor_presente:
            return MOTIVO_SIN_VALOR.format(campo=campo.campo)
        return MOTIVO_SIN_SUSTENTO.format(valor=normalizado, campo=campo.campo)
    if regla.id == "RAW_VOCABULARIO":
        return MOTIVO_FUERA_VOCABULARIO.format(
            valor=normalizado,
            campo=campo.campo,
            vocabulario=sorted(str(v) for v in (campo.vocabulario or [])),
        )
    if regla.id == "RAW_SUSTENTO":
        return MOTIVO_SIN_SOSTEN.format(
            valor=normalizado, campo=campo.campo, fragmento=campo.fragmento.strip()
        )
    # RAW_CONTRADICCION
    declarado = normalizado
    encontrados = [v for v in coincidencias_en_sustento(campo) if v != declarado]
    return MOTIVO_CONTRADICCION.format(
        campo=campo.campo,
        encontrado=encontrados[0] if encontrados else "?",
        declarado=declarado,
    )


def _candidatos(
    campo: CampoDeclarado, reglas: Iterable[Rule]
) -> tuple[list[str], list[str]]:
    """Deriva candidatos descartados/restantes de las reglas raw disparadas (T-303).

    Política (documentada, `F3-subplan` §3.3):

    - **Descartados**: lo que la fuente misma contradijo. Si el fragmento sostiene
      otro valor del vocabulario, ese valor queda descartado (la fuente citó
      ambas cosas, así que no puede ser la buena). Un valor **fuera del
      vocabulario** no se puede tachar (no es un candidato del dominio): se
      informa como debilidad, no como descarte.
    - **Restantes**: el valor declarado, si sobrevivió al veredicto. Si la fuente
      no declaró valor, el veredicto **no inventa** los candidatos del
      vocabulario: devuelve la lista vacía y el motor (R1-R7) decide con lo que
      tenga.
    - Si el único valor que la fuente aporta es el contradictorio (nadie declaró
      un valor válido), ese valor **no** se descarta: pasa a **restantes**, porque
      tachar la única lectura disponible dejaría el caso sin evidencia. Es el
      criterio conservador ya usado para el solapamiento descartados/restantes
      (blindaje ADR-008).
    """
    ids = {regla.id for regla in reglas}
    descartados: list[str] = []
    restantes: list[str] = []

    declarado = campo.normalizar()
    declarado_utilizable = (
        campo.valor_presente
        and campo.vocabulario is not None
        and declarado in set(campo.vocabulario)
    )

    if declarado_utilizable:
        restantes.append(declarado)

    contradichos: list[str] = []
    if "RAW_CONTRADICCION" in ids:
        contradichos = [v for v in coincidencias_en_sustento(campo) if v != declarado]

    if contradichos:
        if declarado_utilizable:
            # Hay un valor declarado que sostener: los otros se descartan.
            descartados.extend(v for v in contradichos if v not in descartados)
        else:
            # Nadie declaró un valor válido: la única lectura viva es la del
            # fragmento; se conserva como restante (no se tacha la única
            # evidencia disponible).
            restantes.extend(v for v in contradichos if v not in restantes)

    # Blindaje ADR-008 (por si el fragmento cita el declarado además del otro):
    # un valor no puede ser descartado y restante a la vez.
    descartados = [v for v in descartados if v not in restantes]

    # Vocabulario cerrado sin sustento ni valor: se listan las alternativas
    # conocidas como restantes solo si la fuente las mencionó (nunca inventadas).
    return descartados, restantes


def evaluar_raw(
    fuente: str,
    campos: CampoDeclarado | Mapping[str, CampoDeclarado],
    *,
    registro: Registry | None = None,
) -> VeredictoRaw:
    """Corre la pasada 1 (reglas raw) sobre los campos de **una** fuente (T-303).

    Evalúa :data:`REGISTRO_RAW` sobre cada campo declarado, acumula la gravedad
    (la más severa gana), junta los motivos en ``debilidades`` y deriva los
    candidatos. **No decide**: la letra final la resuelve R1-R7 (T-301); esto
    solo califica la evidencia.

    Argumentos:
        fuente: etiqueta de la fuente (``vlm``/``llm``/…). Viaja al veredicto para
            trazabilidad.
        campos: un :class:`CampoDeclarado` o un mapping ``nombre -> CampoDeclarado``.
            El mapping permite validar varios campos de una fuente en una sola
            pasada (p. ej. letra + texto en F3; CUIT + total en F4/T-403).
        registro: :class:`Registry` alternativo (tests/extensiones). Default
            :data:`REGISTRO_RAW`.

    Devuelve:
        :class:`VeredictoRaw` con ``valida`` (bool para
        ``SourceEvidence.valida``), ``debilidades``, ``reglas_aplicadas`` y los
        candidatos. El orden de ``reglas_aplicadas``/``debilidades`` sigue la
        prioridad de las reglas (menor primero) y, dentro de cada regla, el orden
        de los campos.

    Lanza:
        ``TypeError`` si ``campos`` no es un :class:`CampoDeclarado` ni un mapping.
    """
    registro_usado = registro or REGISTRO_RAW

    if isinstance(campos, CampoDeclarado):
        lista_campos: list[CampoDeclarado] = [campos]
    elif isinstance(campos, Mapping):
        if not all(isinstance(v, CampoDeclarado) for v in campos.values()):
            raise TypeError(
                "evaluar_raw(): el mapping de campos debe mapear nombre -> "
                "CampoDeclarado (T-303)."
            )
        lista_campos = list(campos.values())
    else:
        raise TypeError(
            "evaluar_raw(): se espera un CampoDeclarado o un mapping "
            f"nombre -> CampoDeclarado; recibido: {type(campos).__name__} (T-303)."
        )

    veredicto = VeredictoRaw(fuente=fuente)

    # Reglas en orden de prioridad; por cada una, todos los campos (así los
    # ids salen agrupados y sin duplicados, que es lo más legible en auditoría).
    for regla in sorted(registro_usado.reglas, key=lambda r: r.prioridad):
        disparos = [campo for campo in lista_campos if regla.evaluar(campo)]
        if not disparos:
            continue
        veredicto.reglas_aplicadas.append(regla.id)
        veredicto.gravedad = _mas_grave(
            veredicto.gravedad, GRAVEDAD_POR_REGLA.get(regla.id, Gravedad.dudosa)
        )
        for campo in disparos:
            veredicto.debilidades.append(_construir_motivo(regla, campo))
            # Trazabilidad por campo (F4/T-404): qué regla tocó a qué campo. Sin
            # esto, la combinación solo podría decir "la fuente quedó débil" sin
            # poder señalar el campo responsable.
            por_regla = veredicto.reglas_por_campo.setdefault(campo.campo, [])
            if regla.id not in por_regla:
                por_regla.append(regla.id)

    # Candidatos: se agregan los de todos los campos (una fuente puede aportar
    # más de un campo candidato).
    for campo in lista_campos:
        descartados, restantes = _candidatos(
            campo, sorted(registro_usado.reglas, key=lambda r: r.prioridad)
        )
        for valor in descartados:
            if valor not in veredicto.candidatos_descartados:
                veredicto.candidatos_descartados.append(valor)
        for valor in restantes:
            if valor not in veredicto.candidatos_restantes:
                veredicto.candidatos_restantes.append(valor)

    # Coherencia entre campos de la **misma** fuente (F4/T-403, E-EXT-2). No es
    # una ``Rule`` porque mira el conjunto y no un campo por vez; se evalúa al
    # final para que los motivos queden después de los de las reglas.
    incoherencias = violaciones_de_coherencia(
        {campo.campo: campo for campo in lista_campos}
    )
    if incoherencias:
        veredicto.reglas_aplicadas.append(ID_RAW_COHERENCIA)
        veredicto.gravedad = _mas_grave(veredicto.gravedad, GRAVEDAD_RAW_COHERENCIA)
        for motivo in incoherencias:
            veredicto.debilidades.append(motivo)
        veredicto.incoherencias = list(incoherencias)
        # La coherencia es una propiedad del **conjunto**: se anota en el campo que
        # la declara (el que disparó la implicación), para que la trazabilidad por
        # campo siga siendo cierta.
        for campo in lista_campos:
            if not campo.coherencia:
                continue
            disparado = campo.normalizar()
            if any(
                disparado == implicacion.disparador
                for implicacion in campo.coherencia
            ):
                por_regla = veredicto.reglas_por_campo.setdefault(campo.campo, [])
                if ID_RAW_COHERENCIA not in por_regla:
                    por_regla.append(ID_RAW_COHERENCIA)

    # Blindaje ADR-008 (contrato de CombinedEvidence): nunca en ambas listas.
    veredicto.candidatos_descartados = [
        v for v in veredicto.candidatos_descartados if v not in veredicto.candidatos_restantes
    ]

    veredicto.valida = veredicto.gravedad is not Gravedad.invalida
    return veredicto


__all__ = [
    "Gravedad",
    "CampoDeclarado",
    "VeredictoRaw",
    "REGISTRO_RAW",
    "GRAVEDAD_POR_REGLA",
    "PRIORIDAD_RAW_CAMPO",
    "PRIORIDAD_RAW_VOCABULARIO",
    "PRIORIDAD_RAW_SUSTENTO",
    "PRIORIDAD_RAW_CONTRADICCION",
    "MOTIVO_SIN_VALOR",
    "MOTIVO_SIN_SUSTENTO",
    "MOTIVO_FUERA_VOCABULARIO",
    "MOTIVO_SIN_SOSTEN",
    "MOTIVO_CONTRADICCION",
    "MOTIVO_INCONSISTENCIA",
    "ID_RAW_COHERENCIA",
    "GRAVEDAD_RAW_COHERENCIA",
    "construir_registro_raw",
    "condicion_raw_campo",
    "condicion_raw_vocabulario",
    "condicion_raw_sustento",
    "condicion_raw_contradiccion",
    "coincidencias_en_sustento",
    "valor_sostenido",
    "violaciones_de_coherencia",
    "evaluar_raw",
]

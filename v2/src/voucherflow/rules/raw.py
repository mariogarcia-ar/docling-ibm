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


# ---------------------------------------------------------------------------
# Entradas del registro raw
# ---------------------------------------------------------------------------


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
    """

    campo: str
    valor: Any = None
    fragmento: str = ""
    vocabulario: Sequence[str] | frozenset[str] | None = None
    normalizador: Any = None
    patron_sustento: re.Pattern[str] | None = None
    exigir_sustento: bool = True
    nota: str = ""

    def normalizar(self) -> Any:
        """Valor normalizado (aplica ``normalizador`` si se declaró)."""
        if self.normalizador is not None and self.valor is not None:
            return self.normalizador(self.valor)
        return self.valor

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
            ``SourceEvidence.debilidades``).
        candidatos_descartados: valores del vocabulario **descartados** por
            contradicción (la fuente citó otro valor) o por estar fuera del
            vocabulario.
        candidatos_restantes: valores del vocabulario que **siguen vivos** (el
            declarado, y en ausencia de declaración las alternativas del
            vocabulario).
    """

    fuente: str
    valida: bool = True
    gravedad: Gravedad = Gravedad.valida
    reglas_aplicadas: list[str] = field(default_factory=list)
    debilidades: list[str] = field(default_factory=list)
    candidatos_descartados: list[str] = field(default_factory=list)
    candidatos_restantes: list[str] = field(default_factory=list)

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
    """
    declarado = campo.normalizar()
    if declarado is None:
        return False
    if campo.vocabulario is not None:
        return declarado in coincidencias_en_sustento(campo)
    return str(declarado).casefold() in campo.fragmento.casefold()


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
    "construir_registro_raw",
    "condicion_raw_campo",
    "condicion_raw_vocabulario",
    "condicion_raw_sustento",
    "condicion_raw_contradiccion",
    "coincidencias_en_sustento",
    "valor_sostenido",
    "evaluar_raw",
]

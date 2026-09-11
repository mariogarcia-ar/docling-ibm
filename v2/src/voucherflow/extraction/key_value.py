"""Normalización key-value de la extracción (F4 / T-402).

**Fase**: F4 (extracción) · **Tarea**: T-402 · **Épica**: E-EXT-3.

Qué resuelve este módulo
-----------------------
T-401 dejó a propósito los valores **crudos**: el flujo reporta *qué se leyó*
(``CampoLectura.valor``) y *con qué sostén* (``fragmento_sustento``), y el
programa —no el prompt— es el que decide la representación canónica (ADR-001).
Este módulo es ese paso: convierte la lectura cruda en la **representación
normalizada** de E-EXT-3, reutilizando las reglas de normalización que en v1
vivían **dentro** de los prompts ``10``/``11`` (``kvi``/``kvg``) y que ahora se
pueden testear de forma determinística:

```text
"20-1 Ing, Brutas: 201641"  --NORM_CUIT-------->  "20-1"          (+aviso: incompleto)
"14/08/2025"                --NORM_FECHA------->  "2025-08-14"
"$ 12.345,67"               --NORM_MONTO------->  12345.67
"00005-00007344"            --NORM_COMPROBANTE->  "00005" + "00007344" (punto_venta + numero)
"ars"                       --NORM_MONEDA------>  "ARS"
```

Reglas portadas de v1 (``11-extraction_key_value_invoice`` /
``10-extraction_key_value_generic``) — ninguna inventa datos:

* **CUIT** (regla 2b de ``11``): solo dígitos y los guiones propios del número
  (``NN-NNNNNNNN-N``); se corta apenas aparece un carácter que no sea dígito o
  guión del propio número, aunque el resultado quede incompleto porque el OCR
  truncó o pegó el campo siguiente. Se arranca en el **primer dígito**, para
  tolerar que la lectura haya incluido la etiqueta (``"C.U.I.T. Nro.: 20-1"``).
* **Fechas** (``kvg``: "Fechas: ``YYYY-MM-DD``. Sin día/mes completo, omití"):
  solo se normaliza una fecha **completa**; un año/ mes suelto queda sin
  normalizar (no se completa con datos que el documento no trae).
* **Montos** (``kvg``: "número plano, ``.`` decimal, sin miles ni símbolo"):
  se quitan símbolos y separadores de miles; el resultado es numérico
  (``int``/``float``). El valor crudo queda en ``valor_crudo`` para reconciliar.
* **Número de comprobante** (``kvg``: "Separá comprobantes
  ``PPPPP-NNNNNNNN`` en ``punto_venta`` y ``numero_comprobante``"): se separan
  como **campos derivados** (con el sostén del número impreso) y el número
  impreso se conserva tal cual, como pide la regla 3 de ``11``.
* **Ítems** (``kvg``: ``"descripción xcantidad - precio_unitario"`` separados por
  ``" | "``): se estructuran sin inventar: lo que no se reconoce queda como
  descripción del ítem.
* **Moneda** (regla 14 de ``11``): ``USD``/``U$S`` → ``USD``; ``$``/``ARS`` →
  ``ARS``. **No** se aplica el default ``ARS`` de v1: eso sería inventar una
  moneda que el documento no declaró (el campo ausente lo reporta T-401).
* **Texto** (reglas 1/8 de ``11``): espacios colapsados; ``descripcion`` en
  minúsculas (la regla 8 la pide así). La corrección O/0 dentro de palabras
  **no** se hace acá: es una corrección de **lectura** y vive en el prompt
  (T-401).

Decisión de alcance: no se pierde el valor crudo
-----------------------------------------------
La regla dura de E-EXT-3 es "**no inventar**": un dato ausente se omite y un
dato ilegible no se completa. Pero "no normalizable" **no** es "ausente": un
monto escrito con palabras, o una fecha sin año, son lecturas reales que el
documento sí trae. Descartarlas perdería información, así que este módulo:

1. **conserva el valor crudo** como valor publicado cuando no puede normalizarlo
   (nunca inventa uno normalizado), y
2. **declara el motivo** en un :class:`AvisoNormalizacion`, que el llamador
   agrega a ``problemas`` → ``SourceEvidence.debilidades`` (las que son
   limitaciones reales) o deja como informativo (p. ej. "el número impreso no
   tiene la forma ``PPPPP-NNNNNNNN``, no se separa").

Decisiones de alcance documentadas
----------------------------------
* **Ambigüedad del separador de miles/decimales** (``"12.345"`` vs. ``"12,345"``):
  con un solo separador y exactamente 3 dígitos detrás el valor es
  intrínsecamente ambiguo. Se aplica la **convención argentina** (``.`` = miles,
  ``,`` = decimal — el comprobante es argentino) y se emite un **aviso de
  debilidad**: no se silencia la ambigüedad ni se elige "en silencio".
* **Montos**: se publican como ``int`` (sin decimales) o ``float`` (con
  decimales). El contrato de F0 (``EvidenceField.valor``) no admite ``Decimal``;
  la aritmética exacta de importes es responsabilidad de la capa de negocio, y el
  crudo (``"12.345,67"``) queda en ``meta['valor_crudo']`` para reconciliar.
* **Años de dos dígitos** (``14/08/25``): no se normalizan. Elegir el siglo es
  una invención; se avisa y se conserva el crudo.
* **Campos derivados** (``punto_venta``/``numero_comprobante``): se agregan a la
  evidencia como campos propios, con el mismo ``fragmento_sustento`` del número
  impreso y su ``meta`` de trazabilidad (``derivado_de``). No están en
  :data:`~voucherflow.extraction.prompt_extraccion.CAMPOS_EXTRACCION` porque no
  se le piden al modelo: los calcula el programa.
* **Modo genérico** (``kvg``): los campos del documento que no son del
  vocabulario fiscal se conservan tal cual, salvo los **alias** explícitos de
  monto/fecha (``monto``, ``total``, ``fecha``…) que ``kvg`` define con la misma
  semántica.

Qué **no** hace T-402
---------------------
* No valida el vocabulario ni el sostén (reglas raw): eso es T-303/T-403. Este
  módulo **normaliza**; la pasada raw sigue viendo el **crudo** (T-401), que es
  lo que le permite reportar *qué se leyó* cuando el valor no sirve.
* No combina las evidencias de las dos fuentes: T-404.
* No mide paridad con v1: T-405.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from typing import Any, Mapping, Sequence

from .evidencia import CampoLectura, EvidenciaExtraccion

# ---------------------------------------------------------------------------
# Versión y reglas (ids de trazabilidad, mismo estilo que RAW_* de T-303)
# ---------------------------------------------------------------------------

#: Versión del conjunto de reglas de normalización. Se registra en la evidencia
#: (``meta['normalizacion']['version']``) para que un caso se pueda reproducir:
#: si cambia una regla, sube la versión y la auditoría distingue los casos.
VERSION_NORMALIZACION = "extraccion-key-value-norm@1"

NORM_CUIT = "NORM_CUIT"
NORM_FECHA = "NORM_FECHA"
NORM_MONTO = "NORM_MONTO"
NORM_COMPROBANTE = "NORM_COMPROBANTE"
NORM_VOCABULARIO = "NORM_VOCABULARIO"
NORM_MONEDA = "NORM_MONEDA"
NORM_TEXTO = "NORM_TEXTO"
NORM_ITEMS = "NORM_ITEMS"

#: Campos **derivados** del número de comprobante impreso (E-EXT-3:
#: ``PPPPP-NNNNNNNN`` → ``punto_venta`` + ``numero_comprobante``). No se le piden
#: al modelo: los calcula la normalización a partir de lo leído.
CAMPOS_DERIVADOS_COMPROBANTE: tuple[str, ...] = (
    "punto_venta",
    "numero_comprobante",
)

#: Campos del **modo genérico** (``kvg``) que tienen regla propia y **no** están
#: en el contrato del prompt fiscal (``CAMPOS_EXTRACCION``): ``kvg`` define
#: ``productos`` como la lista de ítems del documento, así que se estructura.
#: Se declara explícito para que el test de integridad pueda exigir que
#: :data:`REGLA_POR_CAMPO` cubra el contrato **más** exactamente estos campos.
CAMPOS_GENERICOS_CON_REGLA: tuple[str, ...] = ("productos",)

#: Regla de normalización de cada campo del contrato de extracción
#: (:data:`~voucherflow.extraction.prompt_extraccion.CAMPOS_EXTRACCION`). Un
#: campo que no esté acá se publica tal como se leyó (modo genérico ``kvg``).
REGLA_POR_CAMPO: dict[str, str] = {
    "cuit_emisor": NORM_CUIT,
    "cuit_receptor": NORM_CUIT,
    "fecha_emision": NORM_FECHA,
    "nro_comprobante": NORM_COMPROBANTE,
    "tipo_comprobante": NORM_VOCABULARIO,
    "moneda": NORM_MONEDA,
    "razon_social_emisor": NORM_TEXTO,
    "razon_social_receptor": NORM_TEXTO,
    "descripcion": NORM_TEXTO,
    "productos": NORM_ITEMS,
    "subtotal": NORM_MONTO,
    "iva": NORM_MONTO,
    "impuestos_internos": NORM_MONTO,
    "percepcion_iibb": NORM_MONTO,
    "otros_impuestos": NORM_MONTO,
    "monto_no_gravado": NORM_MONTO,
    "importe_total_facturado": NORM_MONTO,
}

#: Alias del modo genérico (``kvg``) que tienen la misma semántica que un campo
#: del contrato: ``kvg`` define ``monto`` como "el importe total del documento" y
#: ``fecha`` como la fecha del documento, así que se normalizan igual.
ALIAS_MONTO: tuple[str, ...] = (
    "monto",
    "monto_total",
    "total",
    "importe",
    "importe_total",
    "neto",
    "neto_gravado",
)
ALIAS_FECHA: tuple[str, ...] = ("fecha", "fecha_comprobante", "fecha_factura")

#: Sufijos que se quitan de un campo desconocido para probar los alias
#: (p. ej. ``total_facturado`` → ``total``, ``fecha_alta`` → ``fecha``? no:
#: solo se prueban los alias exactos **y** el campo con el sufijo quitado si el
#: resto es un alias, para no convertir campos arbitrarios).
_SEPARADOR_ALIAS = re.compile(r"[_\-.]")

# ---------------------------------------------------------------------------
# Patrones de los valores (formas que se reconocen, no vocabulario del dominio)
# ---------------------------------------------------------------------------

#: Tokens numéricos de un texto (con sus separadores): el primer carácter es un
#: dígito y el último también, así que no se comen el punto final de una oración
#: ni un guión suelto. Es la forma de comparar un **número** contra un fragmento
#: sin depender de cómo lo escribió el OCR (T-403).
_RE_NUMERO_EN_TEXTO = re.compile(r"\d[\d.,]*\d|\d")

#: Monto con su signo **contextual**: paréntesis de cierre contable
#: (``"(1.234,56)"``), signo delante o detrás (``"-1.234,56"``,
#: ``"1.234,56-"``). El grupo 1 es el cuerpo numérico; los restantes marcan el
#: signo. Se usa para comparar importes contra un fragmento: el OCR imprime los
#: negativos con la convención contable y el modelo los reporta con ``-``.
_RE_MONTO_SIGNADO = re.compile(
    r"(?P<abre>\(\s*)?(?P<prefijo>-)?\s*(?P<cuerpo>\d[\d.,]*\d|\d)\s*"
    r"(?P<sufijo>-)?\s*(?P<cierra>\))?"
)

#: Símbolos que se descartan de un monto antes de interpretar separadores.
_RE_SIMBOLOS_MONTO = re.compile(r"[^\d.,\-()\s]")

#: Un separador decimal/de miles: se interpreta por **posición**, no por símbolo.
_RE_SEPARADORES = re.compile(r"[.,]")

#: Fecha numérica con separadores: ``14/08/2025``, ``14-08-2025``, ``2025.08.14``.
_RE_FECHA_NUMERICA = re.compile(r"(\d{1,4})\s*[/\-.]\s*(\d{1,2})\s*[/\-.]\s*(\d{1,4})")

#: Fecha compacta de 8 dígitos: ``14082025`` (sin separadores).
_RE_FECHA_COMPACTA = re.compile(r"(?<!\d)(\d{8})(?!\d)")

#: Fecha escrita en español: ``14 de agosto de 2025``.
_RE_FECHA_ES = re.compile(
    r"(\d{1,2})\s+de\s+([a-záéíóúüñ]+)\s+de\s+(\d{4})", re.IGNORECASE
)

#: Fecha ISO con hora (``2025-08-14T10:30:00``): se toma la parte de fecha.
_RE_ISO_CON_HORA = re.compile(r"(\d{4})-(\d{2})-(\d{2})[T ]\d")

_MESES_ES: dict[str, int] = {
    "enero": 1,
    "febrero": 2,
    "marzo": 3,
    "abril": 4,
    "mayo": 5,
    "junio": 6,
    "julio": 7,
    "agosto": 8,
    "septiembre": 9,
    "setiembre": 9,
    "octubre": 10,
    "noviembre": 11,
    "diciembre": 12,
}

#: Número de comprobante impreso: ``PPPPP-NNNNNNNN`` (los guiones son del
#: formato; los largos se aceptan porque los talonarios varían).
_RE_NRO_COMPROBANTE = re.compile(r"^(?P<punto>\d+)\s*-\s*(?P<numero>\d+)$")

#: Ítem del modo genérico: ``"descripción xcantidad - precio_unitario"``.
_RE_ITEM_PRECIO = re.compile(r"^(?P<desc>.*?)\s*-\s*(?P<precio>[-]?[\d.,]+)\s*$")
_RE_ITEM_CANTIDAD = re.compile(r"^(?P<desc>.*?)\s*[xX]\s*(?P<cantidad>[\d.,]+)\s*$")

#: Motivos (mensajes legibles, en español, con la regla que los produce).
MOTIVO_SIN_DIGITOS = (
    "no contiene un número reconocible; se conserva el valor tal como se leyó y "
    "no se inventa uno normalizado (T-402, regla de {etiqueta} de v1)"
)
MOTIVO_CUIT_INCOMPLETO = (
    "quedó {digitos} dígitos, no los 11 del CUIT completo ({forma}); el OCR "
    "pudo truncar o pegar el campo siguiente — se corta como pide la regla 2b de "
    "v1 y se conserva lo leído (T-402)"
)
MOTIVO_FECHA_NO_RECONOCIDA = (
    "no se reconoció una fecha completa (día, mes y año); se conserva el valor "
    "tal como se leyó, sin completar datos que el documento no trae (T-402)"
)
MOTIVO_FECHA_ANIO_CORTO = (
    "el año viene con dos dígitos; elegir el siglo sería inventar, así que no se "
    "normaliza y se conserva el valor (T-402)"
)
MOTIVO_FECHA_INEXISTENTE = (
    "la fecha {crudo!r} no existe en el calendario; no se normaliza y se "
    "conserva el valor tal como se leyó (T-402)"
)
MOTIVO_MONTO_NO_RECONOCIDO = (
    "no se reconoció un importe numérico; se conserva el valor tal como se leyó "
    "y no se inventa (T-402, regla de montos de v1)"
)
MOTIVO_MONTO_AMBIGUO = (
    "el separador {sep!r} con 3 dígitos detrás es ambiguo (miles o decimales); "
    "se aplicó la convención argentina ({interpretacion}) y se deja constancia "
    "para que la revisión lo pueda confirmar (T-402)"
)
MOTIVO_COMPROBANTE_SIN_FORMATO = (
    "el número impreso no tiene la forma PPPPP-NNNNNNNN, así que no se separan "
    "punto_venta ni numero_comprobante; se conserva el número tal como se leyó "
    "(T-402, sin inventar una separación)"
)
MOTIVO_ITEM_NO_ESTRUCTURADO = (
    "{cantidad} ítem(s) no responden a la forma \"descripción xcantidad - "
    "precio\"; se conservan como descripción, sin inventar cantidad ni precio "
    "(T-402)"
)
MOTIVO_VALOR_FUERA_VOCABULARIO_MONEDA = (
    "el valor {crudo!r} no corresponde a una moneda reconocida (ARS/USD); se "
    "conserva tal como se leyó (T-402)"
)


class ErrorNormalizacion(ValueError):
    """La entrada de la normalización no es del tipo esperado (T-402).

    Es un error **de contrato del programa**, no de lectura: la normalización
    recibe ``CampoLectura``/``EvidenciaExtraccion`` ya interpretados por T-401.
    Una lectura pobre no es un error acá (se normaliza lo que se pueda y se
    avisa).
    """


# ---------------------------------------------------------------------------
# Resultados: aviso, campo normalizado e informe
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class AvisoNormalizacion:
    """Constancia de que un valor **no** se pudo normalizar del todo (T-402).

    Existe para que la normalización nunca sea silenciosa: o el valor quedó en su
    forma canónica, o queda asentado **por qué** no (y con qué valor crudo se
    siguió). Los avisos con ``debilidad=True`` se agregan a los problemas de la
    lectura y por lo tanto a ``SourceEvidence.debilidades`` (son limitaciones
    reales: un monto ambiguo, un CUIT truncado); los informativos (p. ej. "el
    número impreso no es PPPPP-NNNNNNNN") solo quedan en la traza.

    Campos:
        campo: campo al que se refiere el aviso.
        regla: id de la regla que lo produce (:data:`NORM_*`).
        motivo: explicación legible (en español, con la regla de v1 citada).
        debilidad: si ``True``, el llamador lo agrega a ``problemas`` de la
            lectura (y de ahí a ``debilidades`` del ``SourceEvidence``).
        valor: valor **publicado** (el crudo conservado, o el normalizado con
            reservas). Se guarda para poder auditar el caso sin volver al JSON.
    """

    campo: str
    regla: str
    motivo: str
    debilidad: bool = True
    valor: Any = None

    def formateado(self) -> str:
        """Mensaje legible del aviso, con el campo y la regla (auditoría)."""
        return f"{self.campo}: {self.motivo} [{self.regla}]"


@dataclass(frozen=True)
class ItemExtraido:
    """Un ítem estructurado del modo genérico (``kvg``, T-402).

    La forma que pide ``kvg`` es ``"descripción xcantidad - precio_unitario"``.
    Se estructura lo que se reconoce y **no se inventa** lo que falta: un ítem
    sin ``x<cantidad>`` o sin ``- <precio>`` conserva esos campos en ``None``.

    Campos:
        descripcion: descripción del ítem (espacios colapsados).
        cantidad: cantidad reconocida (``int``/``float``) o ``None``.
        precio_unitario: precio reconocido (``int``/``float``) o ``None``.
        texto_crudo: el texto original del ítem (auditoría).
    """

    descripcion: str
    cantidad: Any = None
    precio_unitario: Any = None
    texto_crudo: str = ""

    def como_dict(self) -> dict[str, Any]:
        """Representación serializable del ítem (para el JSON de la evidencia)."""
        return {
            "descripcion": self.descripcion,
            "cantidad": self.cantidad,
            "precio_unitario": self.precio_unitario,
        }


@dataclass(frozen=True)
class CampoNormalizado:
    """Resultado de normalizar **un** campo (T-402).

    Campos:
        campo: nombre del campo.
        valor: valor publicado: el normalizado cuando se pudo, o el crudo
            conservado cuando no (nunca un valor inventado).
        valor_crudo: el valor tal como se leyó (T-401), para reconciliar.
        regla: regla aplicada (:data:`NORM_*`) o ``None`` si el campo no tiene
            regla (modo genérico: se publica tal como se leyó).
        normalizado: ``True`` si ``valor`` es una representación canónica.
        avisos: constancias emitidas durante la normalización.
        derivados: campos derivados generados (``punto_venta``,
            ``numero_comprobante``) con su valor.
        items: ítems estructurados, si el campo era una lista de ítems.
    """

    campo: str
    valor: Any
    valor_crudo: Any
    regla: str | None = None
    normalizado: bool = False
    avisos: tuple[AvisoNormalizacion, ...] = ()
    derivados: dict[str, Any] = field(default_factory=dict)
    items: tuple[ItemExtraido, ...] = ()


@dataclass(frozen=True)
class InformeNormalizacion:
    """Trazabilidad completa de una corrida de normalización (T-402).

    Es el análogo de ``VeredictoRaw`` (T-303) para esta pasada: no decide nada,
    solo deja asentado qué se normalizó, qué no y por qué. Es el insumo del
    ``detalle`` de la corrida (y, a futuro, de la paridad de T-405).

    Campos:
        version: versión del conjunto de reglas (:data:`VERSION_NORMALIZACION`).
        reglas_aplicadas: ids de las reglas que efectivamente se aplicaron, en
            orden de aparición de los campos y sin repetir.
        normalizados: ``campo -> valor`` de los campos que quedaron canónicos.
        no_normalizados: ``campo -> valor`` (crudo conservado) de los campos que
            no se pudieron normalizar.
        avisos: todas las constancias emitidas, en orden.
        derivados: ``campo derivado -> valor`` (punto_venta/numero_comprobante).
        items: ítems estructurados (``kvg``), si los hubo.
    """

    version: str = VERSION_NORMALIZACION
    reglas_aplicadas: tuple[str, ...] = ()
    normalizados: dict[str, Any] = field(default_factory=dict)
    no_normalizados: dict[str, Any] = field(default_factory=dict)
    avisos: tuple[AvisoNormalizacion, ...] = ()
    derivados: dict[str, Any] = field(default_factory=dict)
    items: tuple[ItemExtraido, ...] = ()

    @property
    def debilidades(self) -> list[str]:
        """Avisos que son limitaciones reales, ya formateados (→ problemas)."""
        return [aviso.formateado() for aviso in self.avisos if aviso.debilidad]

    @property
    def informativos(self) -> list[str]:
        """Avisos que solo documentan una decisión (no degradan la evidencia)."""
        return [aviso.formateado() for aviso in self.avisos if not aviso.debilidad]

    @property
    def hubo_normalizacion(self) -> bool:
        """``True`` si al menos un campo quedó en forma canónica."""
        return bool(self.normalizados)


@dataclass(frozen=True)
class NormalizacionEvidencia:
    """Evidencia normalizada + el informe de la corrida (T-402).

    La ``evidencia`` es una **copia** de la leída por T-401 con los campos ya
    normalizados (``valor`` canónico y ``valor_crudo`` intacto, más los campos
    derivados). El ``informe`` tiene la trazabilidad completa. El llamador decide
    si usa esta evidencia (es lo que hace ``ejecutar_flujo``: la normalización
    alimenta el ``SourceEvidence``) o la cruda.
    """

    evidencia: EvidenciaExtraccion
    informe: InformeNormalizacion


# ---------------------------------------------------------------------------
# Reglas de normalización, campo por campo (funciones puras)
# ---------------------------------------------------------------------------


def normalizar_texto(valor: Any) -> Any:
    """Colapsa los espacios de un texto (reglas 1/8 de v1; T-402).

    Quita espacios al principio/fin y colapsa los internos (el OCR pega columnas
    con espacios múltiples). **No** cambia mayúsculas ni corrige O/0: la
    corrección de lectura vive en el prompt (T-401) y el nombre de una empresa
    se publica tal como se leyó. Un valor no textual se devuelve sin tocar.
    """
    if not isinstance(valor, str):
        return valor
    return " ".join(valor.split())


def normalizar_descripcion(valor: Any) -> Any:
    """Normaliza la ``descripcion``: minúsculas + espacios colapsados (T-402).

    v1 (regla 8 de ``11``) pide la descripción "en minúsculas, basada en los
    ítems o el concepto impreso"; ese formato se aplica acá en código en vez de
    pedírselo al modelo (ADR-001: el prompt no normaliza).
    """
    if not isinstance(valor, str):
        return valor
    return " ".join(valor.split()).lower()


def normalizar_vocabulario(valor: Any) -> Any:
    """Lleva un valor de vocabulario cerrado a mayúsculas (T-402).

    El vocabulario de la extracción (``tipo_comprobante``, ``moneda``) está en
    mayúsculas y la pasada raw (T-303) ya compara sin distinguir capitalización;
    acá se publica la forma canónica (``"a"`` → ``"A"``, ``"ars"`` → ``"ARS"``).
    """
    if not isinstance(valor, str):
        return valor
    return valor.strip().upper()


def normalizar_cuit(valor: Any) -> str | None:
    """Normaliza un CUIT/CUIL: dígitos y guiones propios (regla 2b de v1, T-402).

    Política (literal de la regla de v1): se toma el número desde el **primer
    dígito** (tolera que la lectura haya incluido la etiqueta) y se avanza
    aceptando dígitos y guiones **del propio número** (un guión solo vale si está
    entre dígitos); cualquier otro carácter corta la lectura, aunque el resultado
    quede incompleto porque el OCR truncó o pegó el campo siguiente.

    Ejemplos: ``"20-12345678-9"`` → ``"20-12345678-9"``;
    ``"20-1 Ing, Brutas: 201641"`` → ``"20-1"``;
    ``"C.U.I.T. Nro.: 20-1"`` → ``"20-1"``; ``"CUIT ilegible"`` → ``None``.

    Devuelve ``None`` si no hay ningún dígito (no se inventa un número).
    """
    if valor is None:
        return None
    texto = str(valor).strip()
    inicio = next((i for i, c in enumerate(texto) if c.isdigit()), None)
    if inicio is None:
        return None
    fin = inicio
    for i in range(inicio, len(texto)):
        caracter = texto[i]
        if caracter.isdigit():
            fin = i + 1
            continue
        if (
            caracter == "-"
            and i > inicio
            and texto[i - 1].isdigit()
            and i + 1 < len(texto)
            and texto[i + 1].isdigit()
        ):
            fin = i + 1
            continue
        break
    return texto[inicio:fin]


def cuit_completo(valor: Any) -> bool:
    """``True`` si el valor tiene los 11 dígitos del CUIT (T-402).

    Solo cuenta dígitos (los guiones son formato). Se usa para **avisar** una
    lectura truncada; no se completa ni se valida el dígito verificador (eso es
    del padrón/ARCA, no de la extracción).
    """
    normalizado = normalizar_cuit(valor)
    if normalizado is None:
        return False
    return sum(1 for c in normalizado if c.isdigit()) == 11


def _fecha_valida(anio: int, mes: int, dia: int) -> bool:
    """Valida que la terna sea una fecha real del calendario (T-402).

    Rechaza lo imposible (mes 13, 31 de febrero, 30 de febrero) para no publicar
    una fecha que no existe: si el OCR leyó mal, se conserva el crudo con aviso.
    """
    if not (1 <= mes <= 12):
        return False
    if not (1 <= dia <= 31):
        return False
    if mes in (4, 6, 9, 11) and dia == 31:
        return False
    if mes == 2:
        bisiesto = (anio % 4 == 0 and anio % 100 != 0) or anio % 400 == 0
        return dia <= (29 if bisiesto else 28)
    return True


def _fecha_a_iso(anio: int, mes: int, dia: int) -> str | None:
    """Formatea ``YYYY-MM-DD`` si la terna es una fecha válida; si no, ``None``."""
    if not _fecha_valida(anio, mes, dia):
        return None
    return f"{anio:04d}-{mes:02d}-{dia:02d}"


def normalizar_fecha(valor: Any) -> str | None:
    """Normaliza una fecha completa a ``YYYY-MM-DD`` (T-402).

    Reconoce las formas que aparecen en los comprobantes:

    * ISO, con o sin hora: ``2025-08-14``, ``2025-08-14T10:30:00``.
    * numérica con separadores: ``14/08/2025``, ``14-08-2025``, ``2025.08.14``,
      ``14.08.2025`` (si el primer grupo tiene 4 dígitos es ``YYYY-MM-DD``; si no,
      se interpreta ``DD/MM/YYYY``, la convención del comprobante argentino).
    * compacta: ``14082025``.
    * escrita: ``14 de agosto de 2025``.

    **No** normaliza una fecha incompleta (un año o un mes sueltos), un año de
    dos dígitos (elegir el siglo sería inventar) ni una fecha inexistente: en
    esos casos devuelve ``None`` y el llamador conserva el crudo con aviso
    (regla ``kvg``: "Sin día/mes completo, omití").

    Devuelve la fecha ISO o ``None``.
    """
    if valor is None:
        return None
    if isinstance(valor, str):
        texto = valor.strip()
    else:
        texto = str(valor).strip()
    if not texto:
        return None

    iso = _RE_ISO_CON_HORA.search(texto)
    if iso:
        return _fecha_a_iso(int(iso.group(1)), int(iso.group(2)), int(iso.group(3)))

    numerica = _RE_FECHA_NUMERICA.search(texto)
    if numerica:
        primero, segundo, tercero = numerica.groups()
        if len(primero) == 4:  # YYYY-MM-DD
            return _fecha_a_iso(int(primero), int(segundo), int(tercero))
        if len(tercero) == 4:  # DD/MM/YYYY
            return _fecha_a_iso(int(tercero), int(segundo), int(primero))
        return None  # año de dos dígitos: no se inventa el siglo

    escrita = _RE_FECHA_ES.search(texto)
    if escrita:
        mes = _MESES_ES.get(escrita.group(2).lower())
        if mes is not None:
            return _fecha_a_iso(int(escrita.group(3)), mes, int(escrita.group(1)))
        return None

    compacta = _RE_FECHA_COMPACTA.search(texto)
    if compacta:
        digitos = compacta.group(1)
        return _fecha_a_iso(
            int(digitos[4:]), int(digitos[2:4]), int(digitos[:2])
        )
    return None


def _limpiar_monto(valor: Any) -> tuple[str, bool] | None:
    """Extrae el número de un monto y detecta el signo (T-402).

    Descarta símbolos de moneda, espacios y texto alrededor (``"$ 12.345,67"`` →
    ``"12.345,67"``). Acepta el signo negativo **delante**, **detrás**
    (``"1.234,56-"``) o entre paréntesis (``"(1.234,56)"``), que son las tres
    formas en que los comprobantes imprimen un ajuste negativo.

    Devuelve ``(numero, negativo)`` o ``None`` si no hay ningún dígito (no se
    inventa un importe a partir de texto).
    """
    if valor is None:
        return None
    if isinstance(valor, bool):
        return None
    if isinstance(valor, (int, float)):
        return str(valor), valor < 0

    texto = str(valor).strip()
    if not texto:
        return None
    negativo = False
    if texto.startswith("(") and texto.endswith(")"):
        negativo = True
        texto = texto[1:-1]
    texto = _RE_SIMBOLOS_MONTO.sub("", texto).strip()
    if not any(c.isdigit() for c in texto):
        return None
    if texto.startswith("-"):
        negativo = True
    if texto.endswith("-"):
        negativo = True
    texto = texto.replace("-", "").replace(" ", "")
    return (texto, negativo) if texto else None


def monto_ambiguo(valor: Any) -> bool:
    """``True`` si el separador del monto es ambiguo (T-402).

    Un solo tipo de separador con **exactamente 3 dígitos** detrás es ambiguo:
    ``"12.345"`` puede ser doce mil trescientos cuarenta y cinco o doce con tres
   cientos cuarenta y cinco milésimos, y ``"12,345"`` lo mismo al revés. Con dos
    tipos de separador (``"1.234,56"``) no hay ambigüedad: el último separador es
    el decimal.
    """
    limpiado = _limpiar_monto(valor)
    if limpiado is None:
        return False
    texto, _ = limpiado
    puntos = texto.count(".")
    comas = texto.count(",")
    if puntos and comas:
        return False
    total = puntos + comas
    if total != 1:
        return False
    separador = "." if puntos else ","
    _, _, resto = texto.partition(separador)
    return len(resto) == 3


def normalizar_monto(valor: Any) -> int | float | None:
    """Normaliza un monto a número sin separadores de miles (T-402).

    Porta la regla de ``kvg`` ("Montos: número plano, ``.`` decimal, sin miles ni
    símbolo") a código:

    * Quita símbolos de moneda, espacios y texto (``"$ 12.345,67"``) y detecta el
      signo (``-`` delante/detrás, paréntesis).
    * Con **dos** tipos de separador, el **último** es el decimal y el otro es de
      miles: ``"12.345,67"`` y ``"12,345.67"`` → ``12345.67``.
    * Con **un** tipo repetido, es separador de miles: ``"1.234.567"`` → ``1234567``.
    * Con **un** separador y 3 dígitos detrás la lectura es **ambigua**
      (:func:`monto_ambiguo`): se aplica la convención argentina (``.`` = miles,
      ``,`` = decimal) y el llamador emite constancia.
    * Sin separador → entero (``"1234"`` → ``1234``); con decimales → ``float``
      (``"12,50"`` → ``12.5``).

    Devuelve ``int`` (sin decimales), ``float`` (con decimales) o ``None`` si no
    hay un número reconocible (el llamador conserva el crudo con aviso). El
    ``0`` es un valor legítimo, no una ausencia.
    """
    limpiado = _limpiar_monto(valor)
    if limpiado is None:
        return None
    texto, negativo = limpiado

    if "." in texto and "," in texto:
        separador_decimal = "." if texto.rfind(".") > texto.rfind(",") else ","
        miles = "," if separador_decimal == "." else "."
        texto = texto.replace(miles, "")
        entero, _, decimales = texto.partition(separador_decimal)
    elif "." in texto or "," in texto:
        separador = "." if "." in texto else ","
        partes = texto.split(separador)
        if len(partes) > 2:
            texto = "".join(partes)  # separador repetido: miles
            entero, decimales = texto, ""
        elif len(partes[-1]) == 3:
            # Ambiguo: convención argentina (``.`` miles, ``,`` decimal).
            if separador == ".":
                entero, decimales = "".join(partes), ""
            else:
                entero, decimales = partes[0], partes[1]
        else:
            entero, decimales = partes[0], partes[1]
    else:
        entero, decimales = texto, ""

    entero = entero or "0"
    if not entero.isdigit() or (decimales and not decimales.isdigit()):
        return None
    numero: int | float = int(entero)
    if decimales:
        numero = float(f"{int(entero)}.{decimales}")
    else:
        numero = int(entero)
    return -numero if negativo else numero


def normalizar_moneda(valor: Any) -> str | None:
    """Normaliza la moneda a ``ARS``/``USD`` (regla 14 de v1, T-402).

    v1 aceptaba ``USD`` y ``U$S`` como dólar, y el ``$`` como peso en un
    comprobante argentino. La **diferencia** con v1 es que acá **no** se aplica su
    default ("sin indicio explícito usá ARS"): eso sería inventar una moneda que
    el documento no declaró — si el modelo no leyó la moneda, el campo queda
    ausente (T-401) y el negocio lo resuelve en la conclusión.

    Devuelve ``"ARS"``, ``"USD"`` o ``None`` si el valor no es reconocible.
    """
    if valor is None:
        return None
    texto = str(valor).strip().upper()
    if not texto:
        return None
    compacto = texto.replace(" ", "").replace(".", "")
    if compacto in {"USD", "U$S", "US$", "U$D", "DOLAR", "DÓLAR", "DOLARES", "DÓLARES"}:
        return "USD"
    if compacto in {"ARS", "$", "PESOS", "PESO", "ARG"}:
        return "ARS"
    return None


def candidatos_numericos(texto: Any) -> tuple[str, ...]:
    """Números que aparecen en un texto, con sus separadores originales (T-403).

    Es el insumo de los predicados de sostén de los campos de **formato
    volátil**: para saber si un fragmento sostiene ``12345.67`` hay que comparar
    **números**, no texto (``"$ 12.345,67"`` y ``"12345.67"`` son el mismo
    importe). El token conserva sus separadores para que
    :func:`normalizar_monto` los interprete con la misma convención que usa con
    el valor declarado — así el sostén y el valor no pueden divergir.

    Devuelve los tokens en orden de aparición, sin duplicados. Un texto sin
    dígitos devuelve tupla vacía (no se inventa ningún número).
    """
    if texto is None:
        return ()
    encontrados: list[str] = []
    for coincidencia in _RE_NUMERO_EN_TEXTO.finditer(str(texto)):
        token = coincidencia.group(0)
        if token not in encontrados:
            encontrados.append(token)
    return tuple(encontrados)


def montos_en_texto(texto: Any) -> tuple[int | float, ...]:
    """Importes **con su signo** que aparecen en un texto, ya normalizados (T-403).

    A diferencia de :func:`candidatos_numericos` (que devuelve el token crudo),
    acá se interpreta la **convención contable** del signo: un importe entre
    paréntesis (``"(1.234,56)"``), con el signo delante (``"-1.234,56"``) o
    detrás (``"1.234,56-"``) es negativo. Es lo que permite que un ajuste
    declarado como ``-1234.56`` quede sostenido por un fragmento que lo imprime
    como ``"Ajuste: (1.234,56)"``.

    Devuelve los importes normalizados en orden de aparición, sin duplicados. Un
    texto sin números devuelve tupla vacía.
    """
    if texto is None:
        return ()
    encontrados: list[int | float] = []
    for coincidencia in _RE_MONTO_SIGNADO.finditer(str(texto)):
        cuerpo = coincidencia.group("cuerpo")
        if not cuerpo:
            continue
        negativo = bool(
            coincidencia.group("abre")
            or coincidencia.group("prefijo")
            or coincidencia.group("sufijo")
            or coincidencia.group("cierra")
        )
        numero = normalizar_monto(cuerpo)
        if numero is None:
            continue
        valor = -numero if negativo else numero
        if valor not in encontrados:
            encontrados.append(valor)
    return tuple(encontrados)


def fechas_en_texto(texto: Any) -> tuple[str, ...]:
    """Fechas ISO que aparecen en un texto, en orden de aparición (T-403).

    Usa las **mismas** funciones de normalización que el valor declarado
    (:func:`normalizar_fecha`), así que un fragmento que menciona dos fechas
    (``"Período 01/08/2025 al 31/08/2025"``) permite sostener cualquiera de las
    dos: exigir la primera produciría una debilidad espuria.

    Devuelve ISO ``YYYY-MM-DD`` sin duplicados. Un texto sin fecha reconocible
    devuelve tupla vacía.
    """
    if texto is None:
        return ()
    crudo = str(texto)
    encontradas: list[str] = []
    for patron in (_RE_FECHA_NUMERICA, _RE_FECHA_COMPACTA, _RE_FECHA_ES):
        for coincidencia in patron.finditer(crudo):
            normalizada = normalizar_fecha(coincidencia.group(0))
            if normalizada and normalizada not in encontradas:
                encontradas.append(normalizada)
    # ISO con hora (``2025-08-14T10:30``): el patrón anterior ya toma la fecha,
    # pero el grupo puede incluir la hora según dónde empiece la coincidencia.
    iso = _RE_ISO_CON_HORA.search(crudo)
    if iso:
        normalizada = normalizar_fecha(iso.group(0))
        if normalizada and normalizada not in encontradas:
            encontradas.append(normalizada)
    return tuple(encontradas)


def separar_comprobante(valor: Any) -> tuple[str | None, str | None]:
    """Separa ``PPPPP-NNNNNNNN`` en ``(punto_venta, numero_comprobante)`` (T-402).

    Porta la regla de ``kvg``. Se separa por el **guión** del formato y se
    conservan los ceros a la izquierda (se publica **como está impreso**: la
    reforma de longitudes es una decisión de negocio, no de lectura).

    Devuelve ``(None, None)`` cuando el valor no tiene la forma esperada: no se
    inventa una separación (p. ej. un número sin guión puede ser legítimo).
    """
    if valor is None:
        return None, None
    coincidencia = _RE_NRO_COMPROBANTE.match(str(valor).strip())
    if not coincidencia:
        return None, None
    return coincidencia.group("punto"), coincidencia.group("numero")


def parsear_items(valor: Any) -> tuple[ItemExtraido, ...]:
    """Estructura los ítems del modo genérico (``kvg``, T-402).

    ``kvg`` pide ``"descripción xcantidad - precio_unitario"`` por ítem,
    separados por ``" | "``. Se acepta esa forma, una lista de strings (lo que
    suele devolver el modelo) y una lista de objetos con
    ``descripcion``/``cantidad``/``precio_unitario`` ya separados.

    **No se inventa**: si un ítem no tiene cantidad o precio reconocibles, esos
    campos quedan en ``None`` y la descripción conserva el texto leído.
    """
    crudos: list[str] = []
    if valor is None:
        return ()
    if isinstance(valor, Mapping):
        return (_item_desde_mapa(valor),)
    if isinstance(valor, str):
        crudos = [parte for parte in valor.split("|") if parte.strip()]
    elif isinstance(valor, Sequence):
        return tuple(
            _item_desde_mapa(elemento)
            if isinstance(elemento, Mapping)
            else _item_desde_texto(str(elemento))
            for elemento in valor
            if str(elemento).strip()
        )
    else:
        crudos = [str(valor)]

    return tuple(_item_desde_texto(texto) for texto in crudos if texto.strip())


def _item_desde_mapa(mapa: Mapping[str, Any]) -> ItemExtraido:
    """Ítem ya estructurado por el modelo: se normalizan sus partes (T-402)."""
    descripcion = normalizar_texto(mapa.get("descripcion") or mapa.get("detalle") or "")
    cantidad = normalizar_monto(mapa.get("cantidad"))
    precio = normalizar_monto(mapa.get("precio_unitario") or mapa.get("precio"))
    return ItemExtraido(
        descripcion=str(descripcion or ""),
        cantidad=cantidad,
        precio_unitario=precio,
        texto_crudo=json.dumps(dict(mapa), ensure_ascii=False),
    )


def _item_desde_texto(texto: str) -> ItemExtraido:
    """Ítem en texto: separa ``x<cantidad>`` y ``- <precio>`` sin inventar (T-402)."""
    original = texto.strip()
    resto = original
    precio = None
    coincidencia_precio = _RE_ITEM_PRECIO.match(resto)
    if coincidencia_precio:
        precio = normalizar_monto(coincidencia_precio.group("precio"))
        if precio is not None:
            resto = coincidencia_precio.group("desc").strip()
    cantidad = None
    coincidencia_cantidad = _RE_ITEM_CANTIDAD.match(resto)
    if coincidencia_cantidad:
        cantidad = normalizar_monto(coincidencia_cantidad.group("cantidad"))
        if cantidad is not None:
            resto = coincidencia_cantidad.group("desc").strip()
    return ItemExtraido(
        descripcion=normalizar_texto(resto) or original,
        cantidad=cantidad,
        precio_unitario=precio,
        texto_crudo=original,
    )


def regla_de_campo(campo: str) -> str | None:
    """Regla de normalización del campo, o ``None`` si no tiene (T-402).

    Primero busca el nombre exacto en :data:`REGLA_POR_CAMPO`; después prueba los
    **alias** del modo genérico (``kvg``) para monto y fecha, que tienen la misma
    semántica que los campos del contrato. Un campo desconocido se publica tal
    como se leyó: la normalización no adivina tipos.
    """
    if campo in REGLA_POR_CAMPO:
        return REGLA_POR_CAMPO[campo]
    if campo in ALIAS_MONTO:
        return NORM_MONTO
    if campo in ALIAS_FECHA:
        return NORM_FECHA
    resto = _SEPARADOR_ALIAS.split(campo)
    if len(resto) > 1:
        base = resto[0]
        if base in ALIAS_MONTO:
            return NORM_MONTO
        if base in ALIAS_FECHA:
            return NORM_FECHA
    return None


def normalizar_campo(campo: str, valor: Any) -> CampoNormalizado:
    """Normaliza **un** valor según la regla de su campo (T-402).

    Es la función de entrada de la normalización: aplica la regla del campo
    (CUIT, fecha, monto, comprobante, vocabulario, moneda, texto o ítems) y
    devuelve el valor canónico + los avisos + los campos derivados. Un campo sin
    regla se publica tal como se leyó (``normalizado=False``, sin aviso: no hay
    nada que normalizar).

    Nunca lanza por un valor feo: la lectura pobre se reporta con avisos.
    """
    regla = regla_de_campo(campo)
    if regla is None:
        return CampoNormalizado(campo=campo, valor=valor, valor_crudo=valor)

    if regla == NORM_CUIT:
        return _normalizar_campo_cuit(campo, valor)
    if regla == NORM_FECHA:
        return _normalizar_campo_fecha(campo, valor)
    if regla == NORM_MONTO:
        return _normalizar_campo_monto(campo, valor)
    if regla == NORM_COMPROBANTE:
        return _normalizar_campo_comprobante(campo, valor)
    if regla == NORM_VOCABULARIO:
        normalizado = normalizar_vocabulario(valor)
        return CampoNormalizado(
            campo=campo,
            valor=normalizado,
            valor_crudo=valor,
            regla=regla,
            normalizado=normalizado != valor,
        )
    if regla == NORM_MONEDA:
        return _normalizar_campo_moneda(campo, valor)
    if regla == NORM_TEXTO:
        normalizado = (
            normalizar_descripcion(valor)
            if campo == "descripcion"
            else normalizar_texto(valor)
        )
        return CampoNormalizado(
            campo=campo,
            valor=normalizado,
            valor_crudo=valor,
            regla=regla,
            normalizado=normalizado != valor,
        )
    if regla == NORM_ITEMS:
        return _normalizar_campo_items(campo, valor)
    raise ErrorNormalizacion(  # pragma: no cover - defensa ante un id nuevo
        f"regla de normalización desconocida: {regla!r} para el campo {campo!r} "
        "(T-402)."
    )


def _normalizar_campo_cuit(campo: str, valor: Any) -> CampoNormalizado:
    """CUIT: corte ante caracteres extraños + aviso si quedó incompleto (T-402)."""
    normalizado = normalizar_cuit(valor)
    if normalizado is None:
        return CampoNormalizado(
            campo=campo,
            valor=valor,
            valor_crudo=valor,
            regla=NORM_CUIT,
            avisos=(
                AvisoNormalizacion(
                    campo=campo,
                    regla=NORM_CUIT,
                    motivo=MOTIVO_SIN_DIGITOS.format(
                        etiqueta="CUIT (corte ante caracteres extraños)"
                    ),
                    valor=valor,
                ),
            ),
        )
    avisos: tuple[AvisoNormalizacion, ...] = ()
    digitos = sum(1 for c in normalizado if c.isdigit())
    if digitos != 11:
        avisos = (
            AvisoNormalizacion(
                campo=campo,
                regla=NORM_CUIT,
                motivo=MOTIVO_CUIT_INCOMPLETO.format(
                    digitos=digitos, forma="NN-NNNNNNNN-N"
                ),
                valor=normalizado,
            ),
        )
    return CampoNormalizado(
        campo=campo,
        valor=normalizado,
        valor_crudo=valor,
        regla=NORM_CUIT,
        normalizado=normalizado != valor,
        avisos=avisos,
    )


def _normalizar_campo_fecha(campo: str, valor: Any) -> CampoNormalizado:
    """Fecha: ISO si es completa y real; si no, crudo + aviso (T-402)."""
    normalizado = normalizar_fecha(valor)
    if normalizado is None:
        texto = str(valor).strip()
        numerica = _RE_FECHA_NUMERICA.search(texto)
        if numerica and not any(len(grupo) == 4 for grupo in numerica.groups()):
            motivo = MOTIVO_FECHA_ANIO_CORTO
        elif numerica or _RE_FECHA_ES.search(texto) or _RE_FECHA_COMPACTA.search(texto):
            motivo = MOTIVO_FECHA_INEXISTENTE.format(crudo=texto)
        else:
            motivo = MOTIVO_FECHA_NO_RECONOCIDA
        return CampoNormalizado(
            campo=campo,
            valor=valor,
            valor_crudo=valor,
            regla=NORM_FECHA,
            avisos=(
                AvisoNormalizacion(
                    campo=campo, regla=NORM_FECHA, motivo=motivo, valor=valor
                ),
            ),
        )
    return CampoNormalizado(
        campo=campo,
        valor=normalizado,
        valor_crudo=valor,
        regla=NORM_FECHA,
        normalizado=normalizado != valor,
    )


def _normalizar_campo_monto(campo: str, valor: Any) -> CampoNormalizado:
    """Monto: número sin separadores de miles + aviso si es ambiguo (T-402)."""
    normalizado = normalizar_monto(valor)
    if normalizado is None:
        return CampoNormalizado(
            campo=campo,
            valor=valor,
            valor_crudo=valor,
            regla=NORM_MONTO,
            avisos=(
                AvisoNormalizacion(
                    campo=campo,
                    regla=NORM_MONTO,
                    motivo=MOTIVO_MONTO_NO_RECONOCIDO,
                    valor=valor,
                ),
            ),
        )
    avisos: tuple[AvisoNormalizacion, ...] = ()
    if monto_ambiguo(valor):
        separador = "." if "." in str(valor) else ","
        interpretacion = (
            "'.' separador de miles y ',' decimal"
            if separador == "."
            else "',' separador decimal"
        )
        avisos = (
            AvisoNormalizacion(
                campo=campo,
                regla=NORM_MONTO,
                motivo=MOTIVO_MONTO_AMBIGUO.format(
                    sep=separador, interpretacion=interpretacion
                ),
                valor=normalizado,
            ),
        )
    return CampoNormalizado(
        campo=campo,
        valor=normalizado,
        valor_crudo=valor,
        regla=NORM_MONTO,
        normalizado=normalizado != valor,
        avisos=avisos,
    )


def _normalizar_campo_moneda(campo: str, valor: Any) -> CampoNormalizado:
    """Moneda: ``ARS``/``USD``; sin default (no se inventa la moneda; T-402)."""
    normalizado = normalizar_moneda(valor)
    if normalizado is None:
        return CampoNormalizado(
            campo=campo,
            valor=valor,
            valor_crudo=valor,
            regla=NORM_MONEDA,
            avisos=(
                AvisoNormalizacion(
                    campo=campo,
                    regla=NORM_MONEDA,
                    motivo=MOTIVO_VALOR_FUERA_VOCABULARIO_MONEDA.format(crudo=valor),
                    valor=valor,
                ),
            ),
        )
    return CampoNormalizado(
        campo=campo,
        valor=normalizado,
        valor_crudo=valor,
        regla=NORM_MONEDA,
        normalizado=normalizado != valor,
    )


def _normalizar_campo_comprobante(campo: str, valor: Any) -> CampoNormalizado:
    """Comprobante: número impreso tal cual + ``punto_venta``/``numero`` (T-402).

    El número impreso **no se altera** (regla 3 de ``11``: "tal como figura
    impreso, incluyendo el guión"); lo que se agrega es la separación derivada
    (regla de ``kvg``). Si el número no tiene la forma ``PPPPP-NNNNNNNN`` se deja
    constancia **informativa** (no es una debilidad: un número sin guión puede ser
    legítimo) y no se separan campos.
    """
    punto, numero = separar_comprobante(valor)
    if punto is None or numero is None:
        return CampoNormalizado(
            campo=campo,
            valor=valor,
            valor_crudo=valor,
            regla=NORM_COMPROBANTE,
            avisos=(
                AvisoNormalizacion(
                    campo=campo,
                    regla=NORM_COMPROBANTE,
                    motivo=MOTIVO_COMPROBANTE_SIN_FORMATO,
                    debilidad=False,
                    valor=valor,
                ),
            ),
        )
    return CampoNormalizado(
        campo=campo,
        valor=valor,
        valor_crudo=valor,
        regla=NORM_COMPROBANTE,
        normalizado=False,
        derivados={
            "punto_venta": punto,
            "numero_comprobante": numero,
        },
    )


def _normalizar_campo_items(campo: str, valor: Any) -> CampoNormalizado:
    """Ítems: estructura ``descripción xcantidad - precio`` sin inventar (T-402).

    El valor publicado es el JSON canónico de los ítems (el contrato de F0 no
    admite listas en ``EvidenceField.valor``; T-401 ya publicaba la
    representación textual). Los ítems estructurados quedan además en el
    ``informe`` para el consumo aguas abajo.
    """
    items = parsear_items(valor)
    if not items:
        return CampoNormalizado(
            campo=campo,
            valor=valor,
            valor_crudo=valor,
            regla=NORM_ITEMS,
        )
    sin_estructura = sum(
        1 for item in items if item.cantidad is None and item.precio_unitario is None
    )
    avisos: tuple[AvisoNormalizacion, ...] = ()
    if sin_estructura:
        avisos = (
            AvisoNormalizacion(
                campo=campo,
                regla=NORM_ITEMS,
                motivo=MOTIVO_ITEM_NO_ESTRUCTURADO.format(cantidad=sin_estructura),
                debilidad=False,
                valor=valor,
            ),
        )
    publicado = json.dumps(
        [item.como_dict() for item in items], ensure_ascii=False
    )
    return CampoNormalizado(
        campo=campo,
        valor=publicado,
        valor_crudo=valor,
        regla=NORM_ITEMS,
        normalizado=True,
        avisos=avisos,
        items=items,
    )


# ---------------------------------------------------------------------------
# Normalización de una lectura completa (la evidencia de T-401)
# ---------------------------------------------------------------------------


def _campo_normalizado_a_lectura(
    campo_normalizado: CampoNormalizado, lectura: CampoLectura
) -> CampoLectura:
    """Pasa un :class:`CampoNormalizado` a ``CampoLectura`` normalizado (T-402).

    Conserva el fragmento de sostén (el sustento no cambia porque el valor se
    represente distinto) y el valor crudo; agrega la trazabilidad de la
    normalización al propio campo, para que cualquier consumidor
    (``construir_source_evidence``, T-403, T-404, T-405) la vea sin plomería
    extra.
    """
    return CampoLectura(
        campo=lectura.campo,
        valor=campo_normalizado.valor,
        fragmento=lectura.fragmento,
        valor_crudo=lectura.valor_crudo,
        normalizado=campo_normalizado.normalizado,
        regla_normalizacion=campo_normalizado.regla,
        avisos_normalizacion=tuple(
            aviso.motivo for aviso in campo_normalizado.avisos
        ),
    )


def normalizar_evidencia(evidencia: EvidenciaExtraccion) -> NormalizacionEvidencia:
    """Normaliza **todos** los campos de una lectura y arma el informe (T-402).

    Devuelve una **copia** de la evidencia con los valores canónicos (los crudos
    siguen disponibles en ``CampoLectura.valor_crudo``) más los campos
    **derivados** (``punto_venta``/``numero_comprobante``, que se insertan justo
    después del número impreso para que el orden sea determinista). Los avisos
    que son limitaciones reales se agregan a ``problemas`` de la copia, así que
    terminan en ``SourceEvidence.debilidades`` sin que el llamador tenga que
    hacer nada.

    La evidencia original **no se muta** (es ``frozen``): el llamador puede
    conservarla si necesita la lectura cruda completa.
    """
    if not isinstance(evidencia, EvidenciaExtraccion):
        raise ErrorNormalizacion(
            "normalizar_evidencia(): se espera una EvidenciaExtraccion de T-401; "
            f"recibido: {type(evidencia).__name__} (T-402)."
        )

    campos: dict[str, CampoLectura] = {}
    reglas_aplicadas: list[str] = []
    normalizados: dict[str, Any] = {}
    no_normalizados: dict[str, Any] = {}
    avisos: list[AvisoNormalizacion] = []
    derivados: dict[str, Any] = {}
    items: list[ItemExtraido] = []

    for nombre, lectura in evidencia.campos.items():
        campo_normalizado = normalizar_campo(nombre, lectura.valor)
        campos[nombre] = _campo_normalizado_a_lectura(campo_normalizado, lectura)

        if campo_normalizado.regla and campo_normalizado.regla not in reglas_aplicadas:
            reglas_aplicadas.append(campo_normalizado.regla)
        if campo_normalizado.normalizado:
            normalizados[nombre] = campo_normalizado.valor
        elif campo_normalizado.regla and not campo_normalizado.derivados:
            # Regla aplicada pero sin forma canónica ni derivados: se registra
            # como no normalizado (con su aviso) para que la traza sea honesta.
            no_normalizados[nombre] = campo_normalizado.valor
        avisos.extend(campo_normalizado.avisos)
        if campo_normalizado.items:
            items.extend(campo_normalizado.items)

        # Campos derivados (p. ej. punto_venta/numero_comprobante): se publican
        # como campos propios, heredando el sostén del campo del que se derivan.
        for derivado, valor_derivado in campo_normalizado.derivados.items():
            derivados[derivado] = valor_derivado
            campos[derivado] = CampoLectura(
                campo=derivado,
                valor=valor_derivado,
                fragmento=lectura.fragmento,
                valor_crudo=valor_derivado,
                normalizado=True,
                regla_normalizacion=NORM_COMPROBANTE,
                derivado_de=nombre,
            )

    problemas = list(evidencia.problemas)
    for aviso in avisos:
        if not aviso.debilidad:
            continue
        formateado = aviso.formateado()
        if formateado not in problemas:
            problemas.append(formateado)

    normalizada = EvidenciaExtraccion(
        fuente=evidencia.fuente,
        campos=campos,
        campos_ausentes=list(evidencia.campos_ausentes),
        campos_extra=list(evidencia.campos_extra),
        campos_sin_sustento=list(evidencia.campos_sin_sustento),
        problemas=problemas,
        fuente_declarada=evidencia.fuente_declarada,
        crudo=evidencia.crudo,
    )
    informe = InformeNormalizacion(
        reglas_aplicadas=tuple(reglas_aplicadas),
        normalizados=normalizados,
        no_normalizados=no_normalizados,
        avisos=tuple(avisos),
        derivados=derivados,
        items=tuple(items),
    )
    return NormalizacionEvidencia(evidencia=normalizada, informe=informe)


def normalizar_evidencia_extraccion(
    evidencia: EvidenciaExtraccion,
) -> NormalizacionEvidencia:
    """Alias público y explícito de :func:`normalizar_evidencia` (T-402).

    Se usa en el flujo de extracción (``evidencia.ejecutar_flujo``) y en los
    consumidores que quieren que el nombre diga el dominio.
    """
    return normalizar_evidencia(evidencia)


def valores_normalizados(evidencia: EvidenciaExtraccion) -> dict[str, Any]:
    """Mapa ``campo -> valor normalizado`` de una lectura (atajo para T-404/T-405).

    Normaliza sobre la marcha (no exige haber pasado antes por
    :func:`normalizar_evidencia`) para que la paridad con v1 de T-405 pueda
    comparar campos sin armar la evidencia completa.
    """
    salida: dict[str, Any] = {}
    for nombre, lectura in evidencia.campos.items():
        salida[nombre] = normalizar_campo(nombre, lectura.valor).valor
    return salida


__all__ = [
    # versión y reglas
    "VERSION_NORMALIZACION",
    "NORM_CUIT",
    "NORM_FECHA",
    "NORM_MONTO",
    "NORM_COMPROBANTE",
    "NORM_VOCABULARIO",
    "NORM_MONEDA",
    "NORM_TEXTO",
    "NORM_ITEMS",
    "REGLA_POR_CAMPO",
    "ALIAS_MONTO",
    "ALIAS_FECHA",
    "CAMPOS_DERIVADOS_COMPROBANTE",
    "CAMPOS_GENERICOS_CON_REGLA",
    # resultados
    "ErrorNormalizacion",
    "AvisoNormalizacion",
    "ItemExtraido",
    "CampoNormalizado",
    "InformeNormalizacion",
    "NormalizacionEvidencia",
    # reglas por campo
    "normalizar_texto",
    "normalizar_descripcion",
    "normalizar_vocabulario",
    "normalizar_cuit",
    "cuit_completo",
    "normalizar_fecha",
    "normalizar_monto",
    "monto_ambiguo",
    "normalizar_moneda",
    "separar_comprobante",
    "parsear_items",
    "regla_de_campo",
    "normalizar_campo",
    # orquestación de la lectura completa
    "normalizar_evidencia",
    "normalizar_evidencia_extraccion",
    "valores_normalizados",
    # apoyo al sostén de campos estructurados (T-403)
    "candidatos_numericos",
    "montos_en_texto",
    "fechas_en_texto",
]

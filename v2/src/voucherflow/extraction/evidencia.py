"""Evidencia de extracción key-value + flujos VLM/LLM en **paralelo** (F4 / T-401).

**Fase**: F4 (extracción) · **Tarea**: T-401 · **Épica**: E-EXT-1.

Qué resuelve este módulo
-----------------------
El módulo `extraction` (doc 03 §4.4) corre **siempre** dos flujos en paralelo
sobre el mismo comprobante — el **VLM** sobre la imagen (vista fiel de F2) y el
**LLM** sobre el OCR/Markdown (F1) — y cada uno devuelve `SourceEvidence` con el
contrato congelado de F0 (ADR-001). Ese es exactamente el entregable de T-401:

```text
Vista fiel (F2) --\
                   >-- flujo VLM  --\
OCR/Markdown (F1) -/                >-- SourceEvidence (uno por fuente)
                   >-- flujo LLM  --/
```

Las piezas, en el orden en que se usan:

1. :func:`construir_messages_extraccion` (``prompt_extraccion.py``) — el *qué se
   le pide* a cada fuente (prompt versionado ``extraccion-key-value@1``).
2. :class:`Lector` — el **protocolo de lector** inyectable, el mismo contrato
   estructural que ya usan F2 (``qween``) y F3 (``evidencia.py``): un objeto con
   ``ask``. La suite default inyecta un doble; el flujo real usa
   :class:`~voucherflow.models.ollama.OllamaClient`.
3. :func:`parsear_evidencia_extraccion` — el *cómo se interpreta la respuesta*:
   normaliza el JSON al contrato, sin normalizar los **valores** (eso es T-402) y
   sin inventar los campos ausentes (ADR-001).
4. :func:`construir_source_evidence` — la conversión al contrato de F0
   (`SourceEvidence` con un `EvidenceField` por campo declarado, con su
   `fragmento_sustento` y su `meta` de trazabilidad — ADR-005).
5. :func:`extraer_evidencia` — la **orquestación en paralelo** de las fuentes
   disponibles, con la trazabilidad de las dos corridas (doc 03 §4.4/E-EXT-1).

Decisión de alcance: paralelismo real con hilos
-----------------------------------------------
La razón por la que los flujos corren en paralelo es **costo/tiempo**: son dos
llamadas a un modelo local, cada una de varios segundos. La orquestación las
lanza con ``ThreadPoolExecutor`` (una tarea por fuente). Es seguro porque el
trabajo es **I/O-bound**: `OllamaClient` bloquea esperando HTTP
(``requests.Session`` es thread-safe para este uso) y cada tarea solo construye
sus propios objetos; no hay estado mutable compartido. Se expone ``max_workers``
para poder forzar la serialización en tests y para acotar el paralelismo si un
entorno lo requiere.

Decisión de alcance: la pasada raw se **reutiliza** de F3/T-303
--------------------------------------------------------------
Las reglas raw (pasada 1) son de T-403, pero el contrato `SourceEvidence` de F0
exige ``valida``/``reglas_aplicadas``/``debilidades``, y F3/T-303 dejó el
registro **genérico** (`rules/raw.py`) justamente para que F4 lo reutilice sin
reimplementarlo. T-401 lo consume con una política **conservadora y explícita**
(no decide la combinación ni la resolución por campo, eso es T-404):

* **Campo declarado sin fragmento de sustento** → debilidad propia de T-401
  (ADR-001: sin sustento no hay evidencia auditable). Se registra con gravedad
  ``dudosa`` (la fuente aporta un indicio, no una prueba), igual que T-303.
* **Campo con vocabulario cerrado** (``tipo_comprobante``, ``moneda``) → reglas
  raw completas (vocabulario, sostén y contradicción).
* **Campo de texto** (CUIT, razón social, número…) → reglas raw completas; el
  sostén se busca **literal** en el fragmento (el prompt obliga a copiarlo).
* **Campo con formato volátil** (montos, fechas, descripción sintética) → el
  sostén literal **no se evalúa**: el OCR decide separadores de miles/decimales
  y el formato de fecha, así que exigir igualdad literal produciría debilidades
  **espurias** ("Subtotal: 12.345,67" vs. `12345.67`). Estos campos quedan
  explícitamente listados en la traza (``sosten_no_evaluado``) para que T-402
  (normalización) y T-403 (reglas raw por fuente) los cubran, sin silenciar
  nada y sin inventar un veredicto.
* **Campo ausente** (el modelo no lo reportó) → **no** es una debilidad: el
  prompt pide no inventar, así que no declarar un campo ilegible es la conducta
  correcta. La ausencia se registra en ``campos_ausentes`` para el gate de
  conclusión (F5), no degrada el veredicto raw.

La **combinación** de las dos evidencias por campo (`combinar_evidencia`, con la
precedencia ADR-002) es de **T-404** y sigue siendo un esqueleto acá: este
módulo devuelve las dos `SourceEvidence` **sin colapsarlas** (ADR-001/ADR-002).

Qué **no** hace T-401 (para no adelantar tareas)
-----------------------------------------------
* No normaliza valores (CUIT, fechas, montos, punto_venta/número): **T-402**.
* No combina ni resuelve desacuerdos entre fuentes: **T-404**.
* No mide paridad con v1 ni arma el `CombinedEvidence` final: **T-405/T-404**.
"""

from __future__ import annotations

import json
import re
import time
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from typing import Any, Iterable, Mapping, Protocol, runtime_checkable

from ..rules.raw import CampoDeclarado, VeredictoRaw, evaluar_raw
from ..schemas.evidence import (
    EvidenceField,
    Fuente,
    SourceEvidence,
    nueva_meta,
)
from ..settings.config import Settings, cargar_settings
from .prompt_extraccion import (
    CAMPOS_EXTRACCION,
    FUENTES_EXTRACCION,
    VERSION_PROMPT_EXTRACCION,
    construir_messages_extraccion,
)

# ---------------------------------------------------------------------------
# Constantes del contrato de evidencia de extracción
# ---------------------------------------------------------------------------

#: Nombre de la clave del contrato que agrupa los campos leídos.
CLAVE_CAMPOS = "campos"

#: Nombre de la clave del contrato que declara la fuente de la lectura (misma
#: convención que F3/T-302: ``fuente_lectura`` = ``vlm`` | ``llm``).
CAMPO_FUENTE_LECTURA = "fuente_lectura"

#: Claves admitidas **por campo** en la respuesta del modelo (contrato del
#: prompt, ADR-001). Cualquier otra clave del objeto de un campo se ignora.
CLAVES_POR_CAMPO: tuple[str, ...] = ("valor", "fragmento_sustento")

#: Fuente de ``schemas.evidence`` correspondiente a cada fuente de extracción.
_FUENTE_SCHEMA: dict[str, Fuente] = {"vlm": Fuente.vlm, "llm": Fuente.llm}

#: Vocabulario cerrado de los campos que lo tienen (T-401). Para el resto no se
#: aplica la regla de vocabulario del registro raw (son texto libre o números).
#:
#: ``tipo_comprobante`` incluye los códigos de tique ``090``/``099`` porque el
#: prompt los admite explícitamente para boletos de colectivo (v1, prompt 11,
#: regla 4) a diferencia del vocabulario del motor R1-R7 de F3 —que los deja
#: fuera por la decisión abierta **D-13**—: acá se evalúa **qué se leyó**, no qué
#: letra decide el negocio.
VOCABULARIO_TIPO_COMPROBANTE: tuple[str, ...] = ("A", "B", "C", "M", "E", "090", "099")
VOCABULARIO_MONEDA: tuple[str, ...] = ("ARS", "USD")

CAMPOS_CON_VOCABULARIO: dict[str, tuple[str, ...]] = {
    "tipo_comprobante": VOCABULARIO_TIPO_COMPROBANTE,
    "moneda": VOCABULARIO_MONEDA,
}

#: Campos cuyo valor tiene **formato volátil** (el OCR decide separadores de
#: miles/decimales y el formato de fecha) o que son **sintéticos** (la
#: ``descripcion`` es una frase que resume los ítems, no un texto que se copia).
#: Su sostén literal no se evalúa en T-401 (ver el docstring del módulo); T-402
#: los normaliza y T-403 afina su validación raw.
CAMPOS_SOSTEN_NO_EVALUADO: frozenset[str] = frozenset(
    {
        "fecha_emision",
        "subtotal",
        "iva",
        "impuestos_internos",
        "percepcion_iibb",
        "otros_impuestos",
        "monto_no_gravado",
        "importe_total_facturado",
        "descripcion",
    }
)

#: Patrón que reconoce un valor "de formato volátil" por su **forma** (se usa
#: para los campos que no están en la lista anterior, p. ej. los que agrega el
#: modo genérico ``kvg``): solo dígitos y separadores/símbolos de importe o
#: fecha. Un valor así no se puede validar por sostén literal (ver docstring).
PATRON_VALOR_FORMATO_VOLATIL = re.compile(r"^[\s\d.,:/\-$%°]+$")

#: Mensaje del veredicto de T-401 cuando un campo declarado no citó sustento.
MOTIVO_SIN_SUSTENTO_CAMPO = (
    "La fuente declaró un valor para el campo {campo} pero no citó fragmento de "
    "sustento; la lectura no es auditable (ADR-001: sin sustento la evidencia no "
    "se puede verificar)."
)

#: Mensaje con el que se documenta (sin marcarla debilidad) la ausencia de un
#: campo esperado en la lectura.
MOTIVO_CAMPO_AUSENTE = (
    "El campo no fue declarado por la fuente; no se inventa un valor "
    "(ADR-001). Se registra como ausente para el gate de conclusión (F5)."
)

#: Motivo con el que se marca una lectura cuando el ``fuente_lectura`` que
#: declaró el modelo no coincide con la fuente del system prompt usado (dato
#: informativo: la fuente autoritativa la fija el orquestador, no el modelo).
MOTIVO_FUENTE_DECLARADA_DISTINTA = (
    "El modelo declaró fuente_lectura='{declarada}' pero la extracción se pidió "
    "con el system prompt de '{real}'; se conserva la fuente real (el modelo no "
    "decide la trazabilidad)."
)

#: Motivo con el que se registra un campo declarado con un valor fuera del
#: vocabulario cerrado del campo. **No** lo emite el intérprete: la violación de
#: vocabulario es responsabilidad de la regla raw ``RAW_VOCABULARIO`` (T-303),
#: que ve el valor **crudo** declarado por el modelo y la reporta con su
#: gravedad (``invalida`` → ``SourceEvidence.valida=False``). Duplicarla acá
#: sería repetir la misma debilidad con dos textos distintos.
MOTIVO_VALOR_FUERA_VOCABULARIO = (
    "El valor {crudo!r} de {campo} no pertenece al vocabulario admitido "
    "{vocabulario}; el valor se descarta y no puede sostener la decisión."
)


class ErrorEvidencia(ValueError):
    """La respuesta del modelo no cumple el contrato de evidencia (E-LIB-2).

    Se lanza cuando el contenido **no es JSON** o no es un objeto JSON. Un JSON
    válido con campos faltantes o valores inesperados **no** es un error: es
    evidencia pobre y se reporta por ``campos_ausentes``/``problemas`` (el modelo
    no decide; una lectura pobre no debe romper el flujo).

    Hereda de ``ValueError`` —no de :class:`~voucherflow.api.ContratoError`, que
    es un ``RuntimeError``— a propósito: importar ``api`` desde ``extraction``
    cerraría un ciclo de import (mismo criterio que
    ``classification.evidencia.ErrorEvidencia`` de F3/T-302).
    """


class ErrorExtraccion(RuntimeError):
    """Ninguna fuente pudo aportar evidencia de extracción (T-401).

    Es un error **de dominio**: se lanza solo cuando todas las fuentes que
    tenían insumo **fallaron** (p. ej. Ollama caído). Una fuente que responde
    con pocos campos —o sin campos— **no** es un error (es un documento
    ilegible: se reporta, no se revienta). El mensaje incluye el detalle por
    fuente para poder diagnosticar.
    """

    def __init__(self, mensaje: str, fallos: Mapping[str, str] | None = None) -> None:
        self.fallos: dict[str, str] = dict(fallos or {})
        super().__init__(mensaje)


# ---------------------------------------------------------------------------
# Lector inyectable (mismo protocolo estructural que F2/T-202 y F3/T-302)
# ---------------------------------------------------------------------------


@runtime_checkable
class Lector(Protocol):
    """Protocolo del lector de extracción: un objeto con ``ask``.

    Es el **mismo** contrato estructural que ya usan
    :class:`~voucherflow.validation.qween` (gate) y
    :class:`~voucherflow.classification.evidencia.Lector` (tipo/letra), así que
    :class:`~voucherflow.models.ollama.OllamaClient` lo satisface sin cambios y
    la suite default puede inyectar un doble (sin Ollama real).

    Argumentos de ``ask`` que usa T-401: ``messages`` (del prompt versionado),
    ``model`` (rol ``vlm``/``llm`` de ``Settings``), ``json_format=True`` (el
    prompt pide JSON y se fuerza en la API) y ``num_ctx`` (ventana del modelo).

    Devuelve un objeto con ``.contenido`` (str): es lo único que se consume.
    """

    def ask(
        self,
        messages: list[dict[str, Any]],
        model: str,
        json_format: bool = False,
        options: dict[str, Any] | None = None,
        num_ctx: int | None = None,
    ) -> Any:  # pragma: no cover - contrato estructural
        ...


# ---------------------------------------------------------------------------
# Evidencia de extracción de una fuente
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class CampoLectura:
    """Un campo leído por una fuente, **sin normalizar** (T-401).

    Es la unidad que el modelo reportó: el valor tal como aparece en el
    documento y el fragmento (texto OCR o descripción visual) que lo sustenta
    (ADR-001). La normalización (CUIT sin caracteres extraños, fechas ISO,
    montos sin separadores) es de **T-402**: acá el valor se conserva **crudo**
    para que la normalización sea verificable y para que la regla de vocabulario
    pueda reportar *qué* se leyó cuando el valor queda fuera del vocabulario.

    Campos:
        campo: nombre del campo (vocabulario de ``prompt_extraccion`` o el que
            el documento proponga en el modo genérico).
        valor: valor declarado, sin normalizar (str/int/float/bool/None).
        fragmento: fragmento de sustento reportado (puede ser cadena vacía si el
            modelo declaró el valor sin explicarlo: eso es una debilidad).
        valor_crudo: el valor tal como vino en el JSON, antes de cualquier
            saneamiento (se conserva para auditoría; hoy coincide con ``valor``
            porque T-401 no normaliza, pero el contrato lo deja explícito para
            T-402).
    """

    campo: str
    valor: Any = None
    fragmento: str = ""
    valor_crudo: Any = None

    @property
    def con_sustento(self) -> bool:
        """True si el campo citó un fragmento de sustento no vacío (ADR-001)."""
        return bool(self.fragmento.strip())


@dataclass(frozen=True)
class EvidenciaExtraccion:
    """Evidencia de extracción de **una** fuente (T-401).

    Es el resultado de interpretar la respuesta del modelo para un flujo: los
    campos declarados (con su sostén), los campos esperados que **no** se
    declararon, los problemas de higiene detectados y la respuesta cruda para
    auditoría. No decide nada: la validación raw la aplica
    :func:`veredicto_raw_de_evidencia` y la combinación entre fuentes es T-404.

    Campos:
        fuente: ``"vlm"`` (imagen) o ``"llm"`` (texto). Es la fuente **real** de
            la corrida (no lo que declaró el modelo).
        campos: campos declarados, en orden de aparición, indexados por nombre.
        campos_ausentes: campos esperados (``CAMPOS_EXTRACCION``) que la fuente
            no declaró. **No** es una debilidad: no inventar es la conducta
            correcta; se registra para el gate de conclusión (F5).
        campos_extra: campos declarados que **no** están en el vocabulario
            esperado (los agrega el modo genérico ``kvg``). Se conservan: la
            evidencia no descarta datos que el documento sí trae.
        campos_sin_sustento: campos declarados que **no** citaron fragmento de
            sustento (ADR-001). Se lleva por **campo** (y no solo como texto en
            ``problemas``) para que :func:`construir_source_evidence` pueda
            reportar la debilidad **una sola vez**: los campos que la pasada raw
            evalúa ya la reciben de ``RAW_CAMPO`` (T-303); los de formato volátil
            —que el registro raw no evalúa— la reciben de T-401.
        problemas: motivos por los que la lectura quedó débil o se desvió del
            contrato (valor fuera del vocabulario cerrado, claves fuera del
            contrato, campo sin nombre, etc.).
        fuente_declarada: ``fuente_lectura`` que declaró el modelo (puede ser
            ``None`` si no lo declaró, o distinta de :attr:`fuente`).
        crudo: la respuesta textual del modelo (auditoría / diagnóstico).
    """

    fuente: str
    campos: dict[str, CampoLectura] = field(default_factory=dict)
    campos_ausentes: list[str] = field(default_factory=list)
    campos_extra: list[str] = field(default_factory=list)
    campos_sin_sustento: list[str] = field(default_factory=list)
    problemas: list[str] = field(default_factory=list)
    fuente_declarada: str | None = None
    crudo: str = ""

    @property
    def valida(self) -> bool:
        """True si la lectura es utilizable **sin reservas** (higiene T-401).

        Exige que todos los campos declarados citen sustento (ADR-001). No es el
        veredicto de las reglas raw —eso lo aporta
        :func:`veredicto_raw_de_evidencia`— sino la higiene mínima que T-401
        garantiza igual que T-302 en F3.
        """
        if not self.campos:
            return False
        return all(campo.con_sustento for campo in self.campos.values())

    @property
    def campos_legibles(self) -> dict[str, Any]:
        """Mapa ``campo -> valor`` de lo declarado (atajo para reportes/tests)."""
        return {nombre: campo.valor for nombre, campo in self.campos.items()}

    @property
    def motivos_ausencia(self) -> dict[str, str]:
        """Motivo de la ausencia de cada campo esperado que no se declaró.

        Deja explícito que la ausencia **no** es una debilidad (el prompt pide no
        inventar): es una limitación de la lectura que el gate de conclusión (F5)
        debe poder distinguir de un dato presente pero flojo.
        """
        return {campo: MOTIVO_CAMPO_AUSENTE for campo in self.campos_ausentes}


# ---------------------------------------------------------------------------
# Interpretación de la respuesta del modelo
# ---------------------------------------------------------------------------


def _parsear_json(contenido: str) -> Any:
    """Extrae el objeto JSON de la respuesta del modelo (T-401).

    Tolerante con las formas reales de los modelos locales (mismo problema que
    resolvía ``extract_json`` de v1 y ``_parsear_json`` de F3/T-302): JSON puro,
    JSON dentro de un bloque markdown ```` ```json ````, o JSON con prosa
    alrededor. Se intenta, en orden: ``json.loads`` directo → sin cercas de
    código → primer objeto JSON completo con ``raw_decode``.

    Nota de honestidad: la reparación del token aislado que hacía v1 no se
    porta. Al reproducir el incidente de v1 se comprobó que sustituciones
    razonables de ese patrón (``"v"`` → ``"``, borrado del token) **no**
    convierten esas respuestas en JSON válido: el texto de v1 solo dejaba
    constancia de la observación. Portar una reparación que no repara sería
    ruido; ``ErrorEvidencia`` conserva la respuesta cruda (``evidencia.crudo``)
    para poder diagnosticar el caso real si vuelve a aparecer.

    Lanza:
        :class:`ErrorEvidencia` si no hay ningún objeto JSON parseable. El
        mensaje incluye el inicio de la respuesta para poder diagnosticar.
    """
    texto = contenido.strip()
    if not texto:
        raise ErrorEvidencia(
            "El modelo devolvió una respuesta vacía donde se esperaba el JSON de "
            "evidencia de extracción (T-401)."
        )

    intentos: list[str] = [texto]

    # Quitar cercas de código markdown (```json ... ``` o ``` ... ```).
    if "```" in texto:
        partes = texto.split("```")
        for indice, parte in enumerate(partes):
            if indice % 2 == 1:  # el contenido entre cercas
                intentos.append(parte.removeprefix("json").strip())

    for candidato in intentos:
        try:
            return json.loads(candidato)
        except (ValueError, TypeError):
            continue

    # Último recurso: el primer objeto JSON completo dentro del texto.
    inicio = texto.find("{")
    if inicio >= 0:
        try:
            objeto, _ = json.JSONDecoder().raw_decode(texto[inicio:])
            return objeto
        except ValueError:
            pass

    raise ErrorEvidencia(
        "La respuesta del modelo no contiene un objeto JSON válido de evidencia "
        f"de extracción (T-401). Respuesta recibida (primeros 200 caracteres): "
        f"{texto[:200]!r}"
    )


def _normalizar_fragmento(valor: Any) -> str:
    """Normaliza ``fragmento_sustento`` a string (cadena vacía si no es texto).

    No se inventa un sostén: un fragmento no textual (número, lista) se descarta
    y el campo queda sin sustento, que es la información honesta.
    """
    if isinstance(valor, str):
        return valor.strip()
    return ""


def _normalizar_valor(valor: Any) -> Any:
    """Sanea el ``valor`` declarado sin **normalizarlo** (T-401).

    Reglas (mínimas y explícitas, para no adelantar T-402):

    * ``None`` → ausente (no se declara el campo).
    * ``str`` vacío o solo espacios → ausente (el modelo no inventa).
    * ``bool``/``int``/``float`` → se conservan tal cual (un monto puede venir
      como número JSON; convertirlo a texto sería una normalización de T-402).
    * ``list``/``dict`` → se conserva el valor: un campo puede ser una lista de
      ítems (el modelo genérico devuelve ``productos``); la interpretación
      estructurada de los ítems es de T-402.

    Devuelve el valor saneado o ``None`` si el campo debe considerarse ausente.
    """
    if valor is None:
        return None
    if isinstance(valor, str):
        limpio = valor.strip()
        return limpio or None
    return valor


def _vocabulario_de(campo: str) -> tuple[str, ...] | None:
    """Vocabulario cerrado del campo, si lo tiene (T-401)."""
    return CAMPOS_CON_VOCABULARIO.get(campo)


def _es_formato_volatil(campo: str, valor: Any) -> bool:
    """True si el sostén literal del campo **no** se puede evaluar (T-401).

    Tres casos, en orden (documentados en el docstring del módulo):

    1. El campo está en :data:`CAMPOS_SOSTEN_NO_EVALUADO` (montos, fechas,
       ``descripcion``): el OCR decide los separadores de miles/decimales y el
       formato de fecha, y la ``descripcion`` es una frase que resume los ítems
       (no un texto que se copia) — exigir igualdad literal produciría
       debilidades **espurias** ("Subtotal: 12.345,67" vs. `12345.67`).
    2. El valor es un **número JSON** (``int``/``float``): no se puede comparar
       literalmente contra el texto del OCR, que trae separadores de miles.
    3. El campo **no pertenece al vocabulario esperado** (los que agrega el modo
       genérico ``kvg``) y su valor tiene **forma de importe/fecha** (solo dígitos
       y separadores): sin saber qué es el campo, su sostén literal no es
       verificable.

    Para los campos esperados de texto (CUIT, razones sociales, número de
    comprobante) el sostén **sí** se evalúa: el prompt obliga a copiar el texto
    literal, así que la comparación es significativa.
    """
    if campo in CAMPOS_SOSTEN_NO_EVALUADO:
        return True
    if isinstance(valor, (int, float)) and not isinstance(valor, bool):
        return True
    if campo not in CAMPOS_EXTRACCION and isinstance(valor, str):
        return bool(PATRON_VALOR_FORMATO_VOLATIL.match(valor.strip()))
    return False


def parsear_evidencia_extraccion(contenido: str, *, fuente: str) -> EvidenciaExtraccion:
    """Interpreta la respuesta del modelo y devuelve la evidencia (T-401).

    Es el paso que separa *qué se le pidió* (``prompt_extraccion.py``) de *qué se
    entendió*. **Nunca normaliza los valores** (T-402) ni inventa campos
    ausentes (ADR-001).

    Normalizaciones aplicadas (todas documentadas, ninguna inventa datos):

    1. Se leen los campos de ``campos`` (objeto ``nombre -> {valor,
       fragmento_sustento}``). Si el modelo devuelve el shape **plano** de v1
       (``{"cuit_emisor": "20-1", ...}``) también se acepta —ver
       :func:`_campos_del_payload`—: es el modo ``kvi``/``kvg`` y no debe perder
       la lectura por una diferencia de envoltorio.
    2. Campo con ``valor`` nulo, vacío o solo espacios → **ausente**: no se
       declara (el prompt pide no inventar) y se lista en ``campos_ausentes`` si
       pertenece al vocabulario esperado.
    3. Campo declarado sin ``fragmento_sustento`` → se declara igual (el valor se
       leyó: descartarlo perdería información) y se registra en
       ``campos_sin_sustento`` (ADR-001: sin sustento la evidencia no es
       auditable).
    4. Los valores **no se normalizan ni se validan** acá: una fecha
       ``14/08/2025``, un CUIT con texto pegado o un ``tipo_comprobante`` fuera
       del vocabulario se conservan **crudos** (T-402 normaliza; la regla raw
       ``RAW_VOCABULARIO`` de T-303 califica el vocabulario con ese valor crudo).
    5. Campos declarados que no están en el vocabulario esperado → se conservan
       (``campos_extra``): el modo genérico agrega claves del documento.

    Argumentos:
        contenido: la respuesta textual del modelo.
        fuente: ``"vlm"`` o ``"llm"`` — la fuente **real** de la corrida.

    Lanza:
        ``ValueError`` si ``fuente`` no es una de :data:`FUENTES_EXTRACCION`.
        :class:`ErrorEvidencia` si la respuesta no es JSON de objeto.

    Devuelve:
        :class:`EvidenciaExtraccion` con la lectura y su trazabilidad.
    """
    if fuente not in FUENTES_EXTRACCION:
        raise ValueError(
            f"fuente inválida: {fuente!r}. Válidas: {FUENTES_EXTRACCION} (T-401)."
        )

    datos = _parsear_json(contenido)
    if not isinstance(datos, Mapping):
        raise ErrorEvidencia(
            "El JSON de evidencia de extracción debe ser un objeto (no una lista "
            f"ni un escalar); recibido: {type(datos).__name__} (T-401)."
        )

    problemas: list[str] = []
    campos_sin_sustento: list[str] = []
    crudos = _campos_del_payload(datos)

    campos: dict[str, CampoLectura] = {}
    for nombre, bruto in crudos.items():
        campo = str(nombre).strip()
        if not campo:
            problemas.append(
                "Se ignoró un campo sin nombre en la respuesta del modelo "
                "(T-401)."
            )
            continue
        valor_crudo, fragmento_crudo, claves_ignoradas = _partir_campo(bruto)
        if claves_ignoradas:
            problemas.append(
                f"El campo {campo} declaró claves fuera del contrato "
                f"{claves_ignoradas}; se ignoran (el contrato las fija en "
                f"{list(CLAVES_POR_CAMPO)}; T-401)."
            )
        valor = _normalizar_valor(valor_crudo)
        if valor is None:
            # Valor nulo/vacío: el campo no se declara (no se inventa).
            continue
        fragmento = _normalizar_fragmento(fragmento_crudo)
        if not fragmento:
            campos_sin_sustento.append(campo)
        # El valor **no se normaliza** (T-402) y tampoco se valida el vocabulario
        # acá: de eso se ocupa ``RAW_VOCABULARIO`` (T-303) con el valor crudo, y
        # el intérprete no debe duplicar la debilidad.
        campos[campo] = CampoLectura(
            campo=campo,
            valor=valor,
            fragmento=fragmento,
            valor_crudo=valor_crudo,
        )

    declarados = set(campos)
    campos_ausentes = [c for c in CAMPOS_EXTRACCION if c not in declarados]
    campos_extra = [c for c in campos if c not in CAMPOS_EXTRACCION]

    fuente_declarada = datos.get(CAMPO_FUENTE_LECTURA)
    fuente_declarada = (
        fuente_declarada.strip().lower() if isinstance(fuente_declarada, str) else None
    )
    if fuente_declarada and fuente_declarada != fuente:
        problemas.append(
            MOTIVO_FUENTE_DECLARADA_DISTINTA.format(
                declarada=fuente_declarada, real=fuente
            )
        )

    return EvidenciaExtraccion(
        fuente=fuente,
        campos=campos,
        campos_ausentes=campos_ausentes,
        campos_extra=campos_extra,
        campos_sin_sustento=campos_sin_sustento,
        problemas=problemas,
        fuente_declarada=fuente_declarada,
        crudo=contenido,
    )


def _normalizar_para_vocabulario(valor: Any) -> Any:
    """Normaliza un valor solo para **comparar** contra el vocabulario (T-401).

    No altera el valor que se publica en la evidencia (ese se conserva crudo,
    T-402): acá se unifica la capitalización de los strings (``"a"`` y ``"A"``
    son la misma letra; ``"ars"`` y ``"ARS"`` la misma moneda) y se dejan intactos
    los no-strings.
    """
    if isinstance(valor, str):
        return valor.strip().upper()
    return valor


def _campos_del_payload(datos: Mapping[str, Any]) -> dict[str, Any]:
    """Devuelve el mapa de campos de la respuesta, tolerando los dos shapes.

    Contrato de T-401 (el que pide el prompt)::

        {"fuente_lectura": "llm",
         "campos": {"cuit_emisor": {"valor": "20-1", "fragmento_sustento": "…"}}}

    Compatibilidad con v1 (modos ``kvi``/``kvg``): el JSON **plano** donde cada
    clave del documento es un campo::

        {"cuit_emisor": "20-1", "importe_total_facturado": 12345.67}

    En el shape plano, un valor que sea un objeto ``{"valor": …,
    ``fragmento_sustento`` …}`` se interpreta como campo con sustento (algunos
    modelos mezclan los dos formatos). Las claves de control
    (``fuente_lectura``) no son campos.
    """
    anidado = datos.get(CLAVE_CAMPOS)
    if isinstance(anidado, Mapping):
        return {str(k): v for k, v in anidado.items()}
    return {
        str(k): v
        for k, v in datos.items()
        if k != CAMPO_FUENTE_LECTURA
    }


def _partir_campo(bruto: Any) -> tuple[Any, Any, list[str]]:
    """Separa un campo en ``(valor, fragmento_sustento, claves_ignoradas)`` (T-401).

    Acepta las dos formas que pueden aparecer en una respuesta real:

    * ``{"valor": …, "fragmento_sustento": …}`` (contrato del prompt) → se toman
      esas dos claves (:data:`CLAVES_POR_CAMPO`) y las demás se **ignoran**,
      devolviéndolas aparte para poder reportar la deriva del contrato.
    * un escalar/lista (shape plano de v1) → el valor es tal cual y el sostén
      queda vacío. Es una lectura **sin sustento** (se registra en
      ``problemas``): v1 no pedía sostén, así que un modelo que responde al
      estilo v1 no puede aportarlo.
    """
    if isinstance(bruto, Mapping) and (
        "valor" in bruto or "fragmento_sustento" in bruto
    ):
        ignoradas = sorted(str(k) for k in bruto if k not in CLAVES_POR_CAMPO)
        return bruto.get("valor"), bruto.get("fragmento_sustento"), ignoradas
    return bruto, None, []


# ---------------------------------------------------------------------------
# Pasada 1: reglas raw (reutiliza el registro genérico de F3/T-303)
# ---------------------------------------------------------------------------


def campo_declarado_de_campo(campo_lectura: CampoLectura) -> CampoDeclarado | None:
    """Traduce un campo leído al ``CampoDeclarado`` del registro raw (T-401/T-403).

    Devuelve ``None`` cuando el sostén **no se puede evaluar** con las reglas
    genéricas (ver :func:`_es_formato_volatil`): en ese caso el campo queda
    explícitamente registrado como no evaluado (``sosten_no_evaluado``) para que
    T-402/T-403 lo cubran, en vez de producir una debilidad espuria.

    Cuando sí se evalúa:

    * el ``valor`` es el **crudo** declarado por el modelo (no el normalizado):
      la regla de vocabulario tiene que poder ver que la fuente dijo ``"X"`` para
      poder reportarlo;
    * el ``vocabulario`` es el cerrado del campo (si lo tiene);
    * el ``normalizador`` unifica la capitalización para comparar (``"a"`` →
      ``"A"``) sin cambiar el valor publicado;
    * no se declara ``patron_sustento``: el sostén se busca por contención
      literal del valor (texto) o por vocabulario (campos cerrados).
    """
    if _es_formato_volatil(campo_lectura.campo, campo_lectura.valor):
        return None
    return CampoDeclarado(
        campo=campo_lectura.campo,
        valor=campo_lectura.valor_crudo,
        fragmento=campo_lectura.fragmento,
        vocabulario=_vocabulario_de(campo_lectura.campo),
        normalizador=_normalizar_para_vocabulario,
        exigir_sustento=True,
    )


def veredicto_raw_de_evidencia(
    evidencia: EvidenciaExtraccion,
    *,
    registro: Any = None,
) -> VeredictoRaw:
    """Corre la **pasada 1** de reglas raw sobre una lectura de extracción (T-401).

    Envuelve :func:`~voucherflow.rules.raw.evaluar_raw` (el registro genérico de
    F3/T-303, que no se reimplementa) con los campos evaluables de la lectura.
    Los campos de formato volátil **no** entran (ver
    :func:`campo_declarado_de_campo`), así que la ausencia de debilidades por
    sostén en un monto no significa "está probado", sino "su sostén se evalúa en
    T-402/T-403" — y eso queda escrito en la traza de
    :func:`construir_source_evidence`.

    No decide nada: el veredicto solo **califica** la evidencia de la fuente.
    """
    declarados: dict[str, CampoDeclarado] = {}
    for nombre, campo_lectura in evidencia.campos.items():
        declarado = campo_declarado_de_campo(campo_lectura)
        if declarado is not None:
            declarados[nombre] = declarado
    if not declarados:
        # Nada evaluable: veredicto neutro (no se inventa una debilidad).
        return VeredictoRaw(fuente=evidencia.fuente)
    return evaluar_raw(evidencia.fuente, declarados, registro=registro)


# ---------------------------------------------------------------------------
# Conversión a los contratos de F0 (ADR-001)
# ---------------------------------------------------------------------------


def _confianza_fuente(campo_lectura: CampoLectura) -> str:
    """Autoevaluación de la fuente para un campo (glosario §2.3; T-401).

    ``alta`` con valor **y** sustento; ``media`` con valor y sostén flojo;
    ``baja`` sin sustento. **No** es la certeza final de la decisión (esa la fija
    la conclusión de F5 a partir de la evidencia combinada).
    """
    return "alta" if campo_lectura.con_sustento else "baja"


def construir_source_evidence(
    evidencia: EvidenciaExtraccion,
    *,
    modelo: str | None = None,
    veredicto: VeredictoRaw | None = None,
) -> SourceEvidence:
    """Convierte una :class:`EvidenciaExtraccion` en ``SourceEvidence`` (ADR-001).

    Emite **un** ``EvidenceField`` por cada campo declarado, con:

    * ``campo`` = nombre del campo,
    * ``valor`` = el valor leído (crudo; T-402 normaliza),
    * ``fuente`` = la fuente de la lectura (``vlm``/``llm``),
    * ``fragmento_sustento`` = el texto/descripción que lo sustenta. Si la fuente
      no citó sustento, se escribe un texto explícito que **declara la ausencia**
      (el contrato de F0 exige un fragmento no vacío y silenciarlo sería peor),
    * ``meta`` = :func:`~voucherflow.schemas.evidence.nueva_meta` con el modelo y
      ``version_prompt`` = ``extraccion-key-value@1`` (ADR-005), más la
      trazabilidad del campo: ``fuente_lectura``, ``valor_crudo``,
      ``vocabulario_campo`` y el resultado de la pasada raw del campo
      (``raw_evaluado``/``raw_valida``/``raw_gravedad``) cuando se evaluó.

    A nivel de fuente, ``valida``/``debilidades``/``reglas_aplicadas`` son el
    resultado de la **pasada 1** (T-303), tal como los publica el veredicto; los
    problemas de higiene detectados al interpretar la respuesta (campo sin
    sustento) se agregan a ``debilidades`` para que no se pierdan — con la misma
    gradación de T-303: ``invalida`` → ``valida=False``; el resto → ``valida=True``
    con debilidades (el dato sirve como indicio, no como prueba).

    Lanza:
        ``ValueError`` si el veredicto raw pertenece a otra fuente que la
        evidencia (un cruce así corrompería la trazabilidad en silencio).
    """
    veredicto_usado = veredicto or veredicto_raw_de_evidencia(evidencia)
    if veredicto_usado.fuente != evidencia.fuente:
        raise ValueError(
            "construir_source_evidence(): el veredicto raw es de la fuente "
            f"{veredicto_usado.fuente!r} y la evidencia de {evidencia.fuente!r}; "
            "deben coincidir (T-401)."
        )

    evaluados = {
        nombre: campo_declarado_de_campo(campo_lectura)
        for nombre, campo_lectura in evidencia.campos.items()
    }

    campos: dict[str, EvidenceField] = {}
    for nombre, campo_lectura in evidencia.campos.items():
        fragmento = campo_lectura.fragmento.strip() or (
            f"Sin fragmento de sustento reportado por la fuente "
            f"'{evidencia.fuente}' (T-401); la lectura no es auditable según "
            "ADR-001."
        )
        vocabulario = _vocabulario_de(nombre)
        # ``EvidenceField.valor`` acepta str/float/int/bool/None: una lista o un
        # objeto (los ítems del modo genérico ``kvg``) no entran en el contrato de
        # F0. En vez de perder el dato, se publica su representación JSON textual
        # y se deja anotado que hubo coerción —la estructuración de ítems es de
        # T-402— (se valida **antes** de construir el modelo, porque pydantic
        # valida en la construcción).
        valor = campo_lectura.valor
        coercionado = isinstance(valor, (list, dict))
        if coercionado:
            valor = json.dumps(valor, ensure_ascii=False)
        meta = nueva_meta(modelo=modelo, version_prompt=VERSION_PROMPT_EXTRACCION)
        meta.update(
            {
                "fuente_lectura": evidencia.fuente,
                "fuente_declarada": evidencia.fuente_declarada,
                "valor_crudo": campo_lectura.valor_crudo,
                "vocabulario_campo": list(vocabulario) if vocabulario else None,
                # ``raw_evaluado=False`` significa que el sostén de este campo NO
                # se evaluó con las reglas raw genéricas (formato volátil:
                # montos, fechas, descripción). No es "está probado": es "lo
                # cubren T-402 (normalización) y T-403 (reglas raw por fuente)";
                # la lista por fuente queda en ``ExtraccionEvidencia.detalle``.
                "raw_evaluado": evaluados[nombre] is not None,
                "valor_coercionado_a_texto": coercionado,
                # El detalle por campo permite auditar cuál trajo la debilidad
                # (la gravedad del veredicto es de la fuente completa).
                "raw_valida": veredicto_usado.valida,
                "raw_gravedad": (
                    veredicto_usado.gravedad.value
                    if evaluados[nombre] is not None
                    else None
                ),
            }
        )
        campos[nombre] = EvidenceField(
            campo=nombre,
            valor=valor,
            fuente=_FUENTE_SCHEMA[evidencia.fuente],
            fragmento_sustento=fragmento,
            confianza_fuente=_confianza_fuente(campo_lectura),
            meta=meta,
        )

    debilidades = list(veredicto_usado.debilidades)
    for problema in evidencia.problemas:
        if problema not in debilidades:
            debilidades.append(problema)

    # El campo sin sustento se reporta **una sola vez**: si la pasada raw lo
    # evaluó, ya lo dijo ``RAW_CAMPO``; los de formato volátil (que el registro
    # raw no evalúa) lo reciben de T-401.
    for nombre in evidencia.campos_sin_sustento:
        if evaluados[nombre] is not None:
            continue
        motivo = MOTIVO_SIN_SUSTENTO_CAMPO.format(campo=nombre)
        if motivo not in debilidades:
            debilidades.append(motivo)

    return SourceEvidence(
        fuente=_FUENTE_SCHEMA[evidencia.fuente],
        campos=campos,
        valida=veredicto_usado.valida,
        reglas_aplicadas=list(veredicto_usado.reglas_aplicadas),
        debilidades=debilidades,
    )


# ---------------------------------------------------------------------------
# Un flujo (una fuente) y la orquestación en paralelo
# ---------------------------------------------------------------------------


@dataclass
class ResultadoFlujo:
    """Resultado de correr **un** flujo de extracción (T-401).

    Campos:
        fuente: ``vlm`` | ``llm``.
        evidencia: la lectura normalizada (``EvidenciaExtraccion``).
        source_evidence: el contrato de F0 (ADR-001) para esa fuente.
        veredicto: el veredicto de la pasada raw (T-303).
        modelo / num_ctx: el modelo y la ventana usados (trazabilidad E-LIB-3).
        duracion_s: duración de la llamada al modelo (para reportar el
            paralelismo real: con dos flujos, el total es ≈ el máximo, no la
            suma).
    """

    fuente: str
    evidencia: EvidenciaExtraccion
    source_evidence: SourceEvidence
    veredicto: VeredictoRaw
    modelo: str = ""
    num_ctx: int | None = None
    duracion_s: float = 0.0


@dataclass
class ExtraccionEvidencia:
    """Resultado de extraer la evidencia de las dos fuentes (T-401).

    Agrupa las evidencias de **todas** las fuentes corridas (sin colapsarlas:
    ADR-002 resuelve el desacuerdo por campo en T-404) y la trazabilidad de la
    corrida. Es el insumo directo de T-404 (combinación) y de T-405 (paridad).

    Campos:
        evidencias: ``SourceEvidence`` por fuente corrida, en el orden pedido
            (``vlm`` antes que ``llm``), para que el resultado sea determinista
            aunque la ejecución sea en paralelo.
        resultados: los :class:`ResultadoFlujo` completos (lectura, veredicto,
            modelo, duración) por fuente.
        documento_id: id del documento (hash sha256 en producción; parámetro acá
            porque T-401 no lee el archivo — el lector es inyectado).
        detalle: trazabilidad de la corrida (fuentes pedidas/corridas/sin
            insumo, fallos, paralelismo, modelos y versión del prompt).
    """

    evidencias: list[SourceEvidence] = field(default_factory=list)
    resultados: dict[str, ResultadoFlujo] = field(default_factory=dict)
    documento_id: str = ""
    detalle: dict[str, Any] = field(default_factory=dict)

    def por_fuente(self, fuente: str) -> SourceEvidence | None:
        """``SourceEvidence`` de una fuente, o ``None`` si no se corrió."""
        resultado = self.resultados.get(fuente)
        return resultado.source_evidence if resultado else None

    def evidencias_por_fuente(self) -> dict[str, SourceEvidence]:
        """Mapa ``fuente -> SourceEvidence`` (atajo que consumirá T-404)."""
        return {fuente: r.source_evidence for fuente, r in self.resultados.items()}

    @property
    def debilidades(self) -> list[str]:
        """Debilidades de todas las fuentes, con la fuente delante (auditoría)."""
        return [
            f"[{fuente}] {debilidad}"
            for fuente, resultado in self.resultados.items()
            for debilidad in resultado.source_evidence.debilidades
        ]

    @property
    def fallos(self) -> dict[str, str]:
        """Errores por fuente que no pudieron aportar evidencia (T-401)."""
        return dict(self.detalle.get("fallos") or {})


def _fuentes_con_insumo(
    fuentes: Iterable[str], *, markdown: str | None, vista: Any
) -> tuple[list[str], list[str]]:
    """Separa las fuentes pedidas entre las que tienen insumo y las que no (T-401).

    Evita llamar al modelo sin material: la fuente ``llm`` necesita markdown y la
    ``vlm`` una vista con ``ruta_imagen_original``. Las que no tienen insumo se
    informan en el ``detalle`` (no se silencian: la ausencia de una fuente es una
    limitación de la evidencia, no un detalle cosmético — E-EXT-1: "corren
    ambos", y si uno no puede correr hay que decirlo).
    """
    con_insumo: list[str] = []
    sin_insumo: list[str] = []
    for fuente in fuentes:
        if fuente not in FUENTES_EXTRACCION:
            raise ValueError(
                f"fuente inválida: {fuente!r}. Válidas: {FUENTES_EXTRACCION} (T-401)."
            )
        tiene = (
            bool((markdown or "").strip())
            if fuente == "llm"
            else getattr(vista, "ruta_imagen_original", None)
        )
        (con_insumo if tiene else sin_insumo).append(fuente)
    return con_insumo, sin_insumo


def _resolver_modelo(
    fuente: str, modelo: str | None, settings: Settings
) -> tuple[str, int | None]:
    """Resuelve modelo y ``num_ctx`` de la fuente desde ``Settings`` (T-401).

    La extracción visual usa el rol ``vlm`` (``qwen2.5vl:3b``) y la textual el
    rol ``llm`` (``qwen2.5:7b``), con el ``num_ctx`` declarado para cada rol
    (E-LIB-3). Si el llamador fija ``modelo``, se usa tal cual y sin ``num_ctx``
    (el llamador es responsable), igual que en F3/T-302.
    """
    if modelo:
        return modelo, None
    rol = settings.modelo_para(fuente)
    if rol is None or not rol.modelo:
        raise ValueError(
            f"No hay modelo configurado para la fuente {fuente!r} "
            "(rol 'vlm'/'llm' de Settings, E-LIB-3); T-401 necesita un modelo "
            "para construir la llamada."
        )
    return rol.modelo, rol.num_ctx


def ejecutar_flujo(
    fuente: str,
    lector: Lector,
    *,
    markdown: str | None = None,
    vista: Any = None,
    modelo: str | None = None,
    settings: Settings | None = None,
) -> ResultadoFlujo:
    """Corre **un** flujo de extracción (VLM o LLM) y devuelve su evidencia (T-401).

    Construye los ``messages`` del prompt versionado, llama al ``lector`` (real:
    ``OllamaClient``), interpreta la respuesta y arma el ``SourceEvidence`` con
    la pasada raw. Es la unidad que el orquestador paraleliza (una tarea por
    fuente), y también la que usan :func:`flujo_vlm` / :func:`flujo_llm`.

    Lanza:
        ``ValueError`` si la fuente es inválida, si no hay modelo configurable o
        si falta el insumo de esa fuente.
        :class:`~voucherflow.classification.evidencia.ErrorEvidencia`
        (``extraction.ErrorEvidencia``): si la respuesta no es JSON de objeto.
        ``OllamaError``: si falla la comunicación (se propaga: una falla de
        comunicación no debe confundirse con "no se pudo leer el documento").
    """
    settings_usado = settings or cargar_settings()
    if fuente not in FUENTES_EXTRACCION:
        raise ValueError(
            f"fuente inválida: {fuente!r}. Válidas: {FUENTES_EXTRACCION} (T-401)."
        )
    modelo_usado, num_ctx = _resolver_modelo(fuente, modelo, settings_usado)
    messages = construir_messages_extraccion(
        fuente=fuente, markdown=markdown, vista=vista
    )

    inicio = time.monotonic()
    respuesta = lector.ask(
        messages,
        model=modelo_usado,
        json_format=True,
        num_ctx=num_ctx,
    )
    duracion = time.monotonic() - inicio

    evidencia = parsear_evidencia_extraccion(respuesta.contenido, fuente=fuente)
    veredicto = veredicto_raw_de_evidencia(evidencia)
    return ResultadoFlujo(
        fuente=fuente,
        evidencia=evidencia,
        source_evidence=construir_source_evidence(
            evidencia, modelo=modelo_usado, veredicto=veredicto
        ),
        veredicto=veredicto,
        modelo=modelo_usado,
        num_ctx=num_ctx,
        duracion_s=duracion,
    )


def extraer_evidencia(
    lector: Lector,
    *,
    markdown: str | None = None,
    vista: Any = None,
    documento_id: str = "documento",
    fuentes: Iterable[str] = FUENTES_EXTRACCION,
    modelo: str | None = None,
    settings: Settings | None = None,
    max_workers: int | None = None,
) -> ExtraccionEvidencia:
    """Extrae la evidencia de las fuentes disponibles **en paralelo** (T-401).

    Es el corazón de E-EXT-1: corre el flujo VLM (imagen de la vista fiel) y el
    flujo LLM (OCR/Markdown) **siempre juntos** —no se elige uno por documento—,
    cada uno devolviendo ``SourceEvidence`` con el mismo contrato (ADR-001), y
    conserva **ambas** evidencias sin colapsarlas (la resolución por campo es
    T-404/ADR-002).

    Paralelismo: cada fuente corre en su propio hilo
    (``ThreadPoolExecutor``); el trabajo es I/O-bound (dos llamadas HTTP al
    modelo local). ``max_workers`` permite forzar la serialización (tests) o
    acotar el paralelismo; por defecto se lanza una tarea por fuente con insumo.

    Tolerancia a fallos: si **una** fuente falla, la otra sigue aportando y el
    error queda en ``detalle["fallos"]`` (no se silencia). Si **todas** las que
    tenían insumo fallan, se lanza :class:`ErrorExtraccion` —no tiene sentido
    devolver una evidencia vacía como si el documento no tuviera datos.

    Argumentos:
        lector: objeto con ``ask`` (protocolo :class:`Lector`). En producción,
            :class:`~voucherflow.models.ollama.OllamaClient`.
        markdown: markdown/OCR de F1 para la fuente ``llm``.
        vista: :class:`~voucherflow.validation.vistas.VistaPreparada` de F2
            (idealmente la **vista fiel**, E-QWE-2) para la fuente ``vlm``.
        documento_id: id del documento (hash sha256 en producción; T-401 no lee
            el archivo, así que se recibe).
        fuentes: fuentes a correr, en orden. Default las dos (``vlm``, ``llm``).
        modelo: modelo explícito para **todas** las fuentes (sin ``num_ctx``).
        settings: ``Settings`` para resolver modelo/``num_ctx`` por rol.
        max_workers: tope de hilos (default: una tarea por fuente con insumo).

    Devuelve:
        :class:`ExtraccionEvidencia` con las evidencias por fuente y la
        trazabilidad. Si ninguna fuente tenía insumo, devuelve las listas vacías
        y el ``detalle`` explica por qué — no es un error (un documento sin
        material de lectura es un caso válido).

    Lanza:
        ``ValueError`` si una fuente pedida no es válida o si no hay modelo
        configurable para una fuente con insumo.
        :class:`ErrorExtraccion` si todas las fuentes con insumo fallaron.
    """
    settings_usado = settings or cargar_settings()
    pedidas = list(fuentes)
    con_insumo, sin_insumo = _fuentes_con_insumo(
        pedidas, markdown=markdown, vista=vista
    )
    # Resolver el modelo **antes** de paralelizar: un error de configuración debe
    # fallar rápido y no dentro de un hilo (donde se perdería como fallo de
    # fuente). Además hace que la verificación de roles sea determinista.
    for fuente in con_insumo:
        _resolver_modelo(fuente, modelo, settings_usado)

    resultados: dict[str, ResultadoFlujo] = {}
    fallos: dict[str, str] = {}

    if con_insumo:
        workers = max_workers if max_workers and max_workers > 0 else len(con_insumo)
        with ThreadPoolExecutor(max_workers=workers) as pool:
            futuros = {
                pool.submit(
                    ejecutar_flujo,
                    fuente,
                    lector,
                    markdown=markdown,
                    vista=vista,
                    modelo=modelo,
                    settings=settings_usado,
                ): fuente
                for fuente in con_insumo
            }
            for futuro, fuente in futuros.items():
                try:
                    resultados[fuente] = futuro.result()
                except Exception as exc:  # noqa: BLE001 - se reporta por fuente
                    fallos[fuente] = f"{type(exc).__name__}: {exc}"

    if con_insumo and not resultados:
        detalle = "; ".join(f"{f}: {m}" for f, m in fallos.items())
        raise ErrorExtraccion(
            "Ninguna de las fuentes de extracción pudo aportar evidencia "
            f"({detalle}). Revisá la conexión con el modelo y volvé a intentar "
            "(T-401).",
            fallos=fallos,
        )

    # Orden determinista: el de ``fuentes`` pedido, no el de finalización de los
    # hilos (el paralelismo no debe cambiar el resultado).
    resultados_ordenados = {
        fuente: resultados[fuente] for fuente in pedidas if fuente in resultados
    }

    detalle: dict[str, Any] = {
        "version_prompt": VERSION_PROMPT_EXTRACCION,
        "fuentes_pedidas": pedidas,
        "fuentes_corridas": [f for f in pedidas if f in resultados],
        "fuentes_sin_insumo": sin_insumo,
        "fallos": fallos,
        "paralelo": len(con_insumo) > 1 and (max_workers is None or max_workers > 1),
        "max_workers": (
            max_workers if max_workers and max_workers > 0 else len(con_insumo)
        ),
        "modelos": {
            fuente: {
                "modelo": resultado.modelo,
                "num_ctx": resultado.num_ctx,
                "valida": resultado.veredicto.valida,
                "gravedad": resultado.veredicto.gravedad.value,
                "reglas_raw": list(resultado.veredicto.reglas_aplicadas),
                "campos_declarados": len(resultado.evidencia.campos),
                "campos_ausentes": len(resultado.evidencia.campos_ausentes),
                "sosten_no_evaluado": sorted(
                    nombre
                    for nombre, campo in resultado.evidencia.campos.items()
                    if _es_formato_volatil(nombre, campo.valor)
                ),
                "duracion_s": round(resultado.duracion_s, 4),
            }
            for fuente, resultado in resultados_ordenados.items()
        },
        "nota": (
            "Extracción T-401: los dos flujos corren en paralelo y conservan su "
            "evidencia sin colapsar (ADR-001); la normalización es T-402, la "
            "combinación por campo (ADR-002) es T-404."
        ),
    }

    return ExtraccionEvidencia(
        evidencias=[r.source_evidence for r in resultados_ordenados.values()],
        resultados=resultados_ordenados,
        documento_id=documento_id,
        detalle=detalle,
    )


__all__ = [
    "CLAVE_CAMPOS",
    "CAMPO_FUENTE_LECTURA",
    "CLAVES_POR_CAMPO",
    "VOCABULARIO_TIPO_COMPROBANTE",
    "VOCABULARIO_MONEDA",
    "CAMPOS_CON_VOCABULARIO",
    "CAMPOS_SOSTEN_NO_EVALUADO",
    "PATRON_VALOR_FORMATO_VOLATIL",
    "MOTIVO_SIN_SUSTENTO_CAMPO",
    "MOTIVO_CAMPO_AUSENTE",
    "MOTIVO_FUENTE_DECLARADA_DISTINTA",
    "MOTIVO_VALOR_FUERA_VOCABULARIO",
    "ErrorEvidencia",
    "ErrorExtraccion",
    "Lector",
    "CampoLectura",
    "EvidenciaExtraccion",
    "ResultadoFlujo",
    "ExtraccionEvidencia",
    "parsear_evidencia_extraccion",
    "campo_declarado_de_campo",
    "veredicto_raw_de_evidencia",
    "construir_source_evidence",
    "ejecutar_flujo",
    "extraer_evidencia",
]

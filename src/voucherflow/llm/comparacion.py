"""Comparación entre las lecturas de referencia y la evidencia del pipeline.

**Plan**: [`docs/plan/07-extracciones-esperadas.md`](../../../docs/plan/07-extracciones-esperadas.md).

Qué hace
--------

Toma una lectura del laboratorio (DeepSeek) y una :class:`CombinedEvidence` del
pipeline local, y compara **campo por campo** el valor canónico de las dos puntas.

⚠️ **Mide ACUERDO, no exactitud.** La lectura de referencia es la salida de otro
modelo, no la verdad: tiene errores medidos (5 de 28 CUIT con el dígito
verificador inválido, §2.3 del plan). Si los dos modelos se equivocan igual, el
acuerdo es 100 %. Por eso el reporte **nunca** publica un único "% de acuerdo".

El módulo es **puro**: no lee archivos, no llama a modelos y no toca la red. Recibe
datos y devuelve un resultado, así que se puede testear sin fixtures ni servicios.

Los cinco estados
-----------------

| Estado | Significa |
|---|---|
| ``coincide`` | Los dos leyeron el mismo valor canónico. |
| ``coincide_normalizado`` | El mismo valor, pero la referencia necesitó normalizarse (no venía en forma canónica). |
| ``difiere`` | Valores distintos. ⚠️ **Puede ser un acierto del pipeline**: ver los 5 CUIT. |
| ``ausente`` | Una o las dos puntas no lo leyeron (``None`` / campo no declarado). |
| ``no_comparable`` | Sin contraparte, fuera del contrato, o sin puntuar (la prosa). |

⚠️ **Por qué ``coincide_normalizado`` se cuenta aparte**: la referencia devuelve
``29/08/2025`` y el pipeline publica ``2025-08-29``. Sin normalizar, 9 de 10 fechas
darían un ``difiere`` **falso**. Distinguir "leyeron lo mismo en otra forma" de
"leyeron lo mismo en la misma forma" evita que la normalización esconda un
desacuerdo real más adelante.

Qué NO puntúa, y por qué está medido
------------------------------------

``observaciones`` y ``rubro_emisor`` están **fuera** del cálculo: el mismo modelo,
sobre el mismo documento, los devuelve **distintos** entre dos corridas idénticas
(medido: completion 2.241 vs 4.179, y esos dos campos difieren). Si puntuaran, el
ruido del modelo se leería como desacuerdo de lectura. Ver §8.1 del plan.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any

from ..extraction.evidencia import parsear_evidencia_extraccion
from ..extraction.key_value import normalizar_evidencia_extraccion

#: Estado: los dos leyeron el mismo valor canónico.
COINCIDE = "coincide"
#: Estado: el mismo valor, pero la referencia venía en otra forma (la fecha).
COINCIDE_NORMALIZADO = "coincide_normalizado"
#: Estado: valores distintos. ⚠️ No implica que el pipeline esté mal.
DIFIERE = "difiere"
#: Estado: una o las dos puntas no lo leyeron.
AUSENTE = "ausente"
#: Estado: sin contraparte, fuera del contrato, o sin puntuar.
NO_COMPARABLE = "no_comparable"

#: Los estados que **cuentan** para el acuerdo. ``no_comparable`` y ``ausente``
#: quedan afuera: no son ni acuerdo ni desacuerdo.
ESTADOS_QUE_PUNTUAN = (COINCIDE, COINCIDE_NORMALIZADO, DIFIERE)

#: Fuente que se le asigna a la lectura de referencia al pasarla por el parser del
#: pipeline.
#:
#: ⚠️ **No es una elección libre**: ``parsear_evidencia_extraccion`` valida contra
#: ``FUENTES_EXTRACCION``, que sólo acepta ``vlm``/``llm`` (T-401). Se usa ``llm``
#: porque la referencia lee el **texto** de la imagen, no la imagen.
#:
#: ⚠️ **Y significa que la referencia NO se puede distinguir por la fuente dentro
#: del artefacto del pipeline.** No importa acá —el parser se usa **sólo** para
#: normalizar, y el resultado se descarta— pero si alguna vez se persiste una
#: evidencia construida así, quedaría declarada como "llm" y la trazabilidad
#: mentiría sobre su origen. El `documento_id` del reporte es lo que la identifica.
FUENTE_REFERENCIA = "llm"


@dataclass(frozen=True)
class ComparacionCampo:
    """Resultado de comparar **un** campo."""

    campo: str
    """Nombre del campo en el **contrato del pipeline** (el eje de la comparación)."""

    campo_referencia: str
    """Nombre del campo en el esquema del lab (igual, o el del mapa)."""

    estado: str
    """Uno de los cinco estados (ver el encabezado del módulo)."""

    valor_referencia: Any = None
    """Valor **canónico** de la referencia, o ``None`` si no lo declaró."""

    valor_pipeline: Any = None
    """Valor del pipeline, o ``None`` si no lo declaró."""

    nota: str | None = None
    """Explicación de la diferencia, cuando el diseño ya la conoce."""

    @property
    def puntua(self) -> bool:
        """¿Entra en el cálculo de acuerdo? (``no_comparable`` y ``ausente`` no)."""
        return self.estado in ESTADOS_QUE_PUNTUAN


@dataclass
class Comparacion:
    """Resultado de comparar una lectura de referencia contra una del pipeline."""

    documento_id: str
    campos: list[ComparacionCampo] = field(default_factory=list)
    #: Campos del contrato que no tienen contraparte en el esquema del lab.
    sin_contraparte: list[str] = field(default_factory=list)
    #: Campos que el lab emite y el contrato del pipeline no tiene.
    fuera_del_contrato: list[str] = field(default_factory=list)
    #: Campos con contraparte pero **excluidos** del acuerdo (la prosa: §8.1).
    no_puntua: list[str] = field(default_factory=list)

    def por_estado(self, estado: str) -> list[ComparacionCampo]:
        return [c for c in self.campos if c.estado == estado]

    def recuento(self) -> dict[str, int]:
        """Cuántos campos en cada estado (las cinco claves siempre presentes).

        ⚠️ Las cinco claves van siempre, incluso en cero: un estado que **no
        aparece** en el reporte es indistinguible de uno que no se evaluó.
        """
        cuenta = {e: 0 for e in (*ESTADOS_QUE_PUNTUAN, AUSENTE, NO_COMPARABLE)}
        for c in self.campos:
            cuenta[c.estado] += 1
        return cuenta

    @property
    def comparables(self) -> int:
        """Campos que entran en el acuerdo (denominador honesto)."""
        return sum(1 for c in self.campos if c.puntua)

    @property
    def coincidencias(self) -> int:
        return len(self.por_estado(COINCIDE)) + len(
            self.por_estado(COINCIDE_NORMALIZADO)
        )

    def como_dict(self) -> dict[str, Any]:
        return {
            "documento_id": self.documento_id,
            "recuento": self.recuento(),
            "comparables": self.comparables,
            "coincidencias": self.coincidencias,
            "campos": [
                {
                    "campo": c.campo,
                    "campo_referencia": c.campo_referencia,
                    "estado": c.estado,
                    "valor_referencia": c.valor_referencia,
                    "valor_pipeline": c.valor_pipeline,
                    "nota": c.nota,
                }
                for c in self.campos
            ],
            "sin_contraparte": self.sin_contraparte,
            "fuera_del_contrato": self.fuera_del_contrato,
            "no_puntua": self.no_puntua,
        }


# --------------------------------------------------------------------------- #
# Normalización de la referencia
# --------------------------------------------------------------------------- #


def normalizar_referencia(
    lectura: dict[str, Any], campos: list[str]
) -> dict[str, Any]:
    """Pasa la lectura del lab por los normalizadores **del pipeline**.

    ⚠️ **Este es el paso que evita el ``difiere`` falso.** El lab devuelve el
    valor ya interpretado (``"29/08/2025"``, ``52069.85``) y el pipeline publica
    el canónico (``"2025-08-29"``, ``52069.85``). Normalizar **la referencia con
    el normalizador del pipeline** es lo único que hace comparables las dos
    puntas: comparar string contra string daría 9 de 10 fechas en ``difiere``.

    Se le asigna la fuente ``llm`` (la única admisible, ver ``FUENTE_REFERENCIA``):
    la referencia lee el texto de la imagen, no la imagen.

    Devuelve ``campo → valor canónico``. Un campo que la referencia no declaró
    **no entra** (la ausencia es información; un ``None`` la confundiría con un
    valor nulo legítimo).
    """
    contenido = json.dumps(
        {
            "fuente_lectura": FUENTE_REFERENCIA,
            "campos": {
                campo: {
                    "valor": lectura.get(campo),
                    # El lab no guarda sostén por campo (§3.3 del plan): viaja un
                    # marcador para satisfacer el contrato del parser. NO se usa
                    # para evaluar nada — la comparación no mide sostén.
                    "fragmento_sustento": "no_evaluado",
                }
                for campo in campos
                if lectura.get(campo) is not None
            },
        },
        ensure_ascii=False,
    )
    evidencia = parsear_evidencia_extraccion(contenido, fuente=FUENTE_REFERENCIA)
    return {
        campo: lectura_normalizada.valor
        for campo, lectura_normalizada in normalizar_evidencia_extraccion(
            evidencia
        ).evidencia.campos.items()
    }


def _valores_pipeline(evidencia: Any) -> dict[str, Any]:
    """``campo → valor`` de la evidencia combinada, saltando lo que nadie declaró.

    Un campo cuya ``fuente`` es ``None`` no lo leyó ninguna fuente: no entra (es
    ``ausente``, no un valor nulo).
    """
    valores: dict[str, Any] = {}
    for campo, combinado in (evidencia.campos or {}).items():
        if getattr(combinado, "fuente", None) is None:
            continue
        valores[campo] = combinado.valor
    return valores


def _equivalente(a: Any, b: Any) -> bool:
    """Igualdad tolerando número vs. texto para el **mismo** dato.

    ``52069.85`` (número) y ``"52069.85"`` (texto) son el mismo monto. Se compara
    normalizando el número a texto, **nunca** interpretando el texto como número
    (eso sería rehacer la normalización, que ya ocurrió en las dos puntas).
    """
    if a is None or b is None:
        return False
    if isinstance(a, bool) or isinstance(b, bool):
        return a == b
    if isinstance(a, (int, float)) and isinstance(b, str):
        return str(a) == b.strip()
    if isinstance(b, (int, float)) and isinstance(a, str):
        return a.strip() == str(b)
    if isinstance(a, str) and isinstance(b, str):
        return a.strip() == b.strip()
    return a == b


# --------------------------------------------------------------------------- #
# Comparación
# --------------------------------------------------------------------------- #


def comparar(
    documento_id: str,
    lectura: dict[str, Any],
    evidencia: Any,
    mapa: dict[str, Any],
) -> Comparacion:
    """Compara una lectura de referencia contra la evidencia combinada.

    Argumentos:
        documento_id: id del documento (para el reporte).
        lectura: el bloque de extracción del lab (esquema **plano**).
        evidencia: ``CombinedEvidence`` del pipeline (o un doble con ``campos``).
        mapa: contenido de ``mapa_de_campos.json`` (la única fuente de verdad).

    Devuelve:
        :class:`Comparacion` con los cinco estados y las tres listas declaradas.
    """
    pares: list[tuple[str, str, str | None]] = []
    for item in mapa["comparables"]["campos"]:
        pares.append((item["lab"], item["pipeline"], None))
    for item in mapa["comparables_por_mapa"]["campos"]:
        pares.append((item["lab"], item["pipeline"], item.get("nota")))

    campos_lab = [lab for lab, _, _ in pares]
    canonica = normalizar_referencia(lectura, campos_lab)
    del_pipeline = _valores_pipeline(evidencia)

    comparaciones: list[ComparacionCampo] = []
    for lab, pipeline, nota_mapa in pares:
        # La referencia ya viene canónica; el pipeline también (lo normalizó el
        # pipeline al construir su evidencia). Comparar sobre eso es lo que hace
        # justas a las dos puntas.
        v_ref = canonica.get(lab)
        v_pipe = del_pipeline.get(pipeline)

        if v_ref is None and v_pipe is None:
            estado = AUSENTE
            nota = "ninguna de las dos puntas lo declaró."
        elif v_ref is None:
            estado = AUSENTE
            nota = "la referencia no lo leyó; el pipeline sí."
        elif v_pipe is None:
            estado = AUSENTE
            nota = "el pipeline no lo leyó; la referencia sí."
        elif _equivalente(v_ref, v_pipe):
            # ¿Hizo falta normalizar? Se compara contra el valor CRUDO del lab:
            # si el crudo ya era el canónico, la coincidencia es directa.
            crudo = lectura.get(lab)
            estado = COINCIDE if _equivalente(crudo, v_pipe) else COINCIDE_NORMALIZADO
            nota = None if estado == COINCIDE else "coinciden tras normalizar."
        else:
            estado = DIFIERE
            nota = nota_mapa or CAUSAS_CONOCIDAS.get(pipeline) or CAUSAS_CONOCIDAS.get(lab)

        comparaciones.append(
            ComparacionCampo(
                campo=pipeline,
                campo_referencia=lab,
                estado=estado,
                valor_referencia=v_ref,
                valor_pipeline=v_pipe,
                nota=nota,
            )
        )

    comparaciones.extend(_no_puntuan(lectura, mapa))

    return Comparacion(
        documento_id=documento_id,
        campos=comparaciones,
        sin_contraparte=[c["pipeline"] for c in mapa["sin_contraparte"]["campos"]],
        fuera_del_contrato=[c["lab"] for c in mapa["fuera_del_contrato"]["campos"]],
        no_puntua=[c["lab"] for c in mapa["no_puntua"]["campos"]],
    )


def _no_puntuan(lectura: dict[str, Any], mapa: dict[str, Any]) -> list[ComparacionCampo]:
    """Los campos con contraparte que **no** entran en el acuerdo (§8.1).

    La prosa del lab va como ``no_comparable`` con su valor, no se descarta: sigue
    siendo dato de contexto para revisión humana, pero **no puntúa**.
    """
    salida: list[ComparacionCampo] = []
    for item in mapa["no_puntua"]["campos"]:
        lab = item["lab"]
        salida.append(
            ComparacionCampo(
                campo=lab,
                campo_referencia=lab,
                estado=NO_COMPARABLE,
                valor_referencia=lectura.get(lab),
                valor_pipeline=None,
                nota=item.get("nota"),
            )
        )
    return salida


#: ⚠️ **Causas conocidas de una diferencia que NO es una regresión.**
#:
#: Mismo criterio que el README de `tests/golden/F4/`: una diferencia se **explica**
#: o se **declara**, nunca se promedia. Un `difiere` sin explicación en el reporte
#: se lee como un error del pipeline, y varias de estas no lo son.
#:
#: ⚠️ **Cada nota se verificó contra el primer documento medido**, no se escribió de
#: memoria: dos de ellas estaban al revés en la primera versión (decían que el lab
#: agregaba el nombre de fantasía y que la letra venía de otro vocabulario; la
#: medición mostró lo contrario). Cuando una nota afirma algo, tiene que ser cierto
#: o el reporte miente con más autoridad que si no dijera nada.
CAUSAS_CONOCIDAS: dict[str, str] = {
    "tipo_comprobante": (
        "Dos causas posibles, y el reporte no distingue cuál aplica: (a) los "
        "vocabularios difieren a propósito —el lab acepta códigos de tique "
        "(090/099) que el motor R1-R7 de F3 deja fuera por D-13—; (b) las dos "
        "fuentes del propio pipeline no coincidieron (medido: VLM 'TICKET DE "
        "VENTA' vs. LLM 'FACTURA'), y el valor publicado es el de la fuente que "
        "ganó por precedencia, marcado como no confiable."
    ),
    "moneda": (
        "El pipeline NO asume 'ARS' sin indicio explícito: inventar la moneda "
        "sería peor que no leerla."
    ),
    "nro_comprobante": (
        "Se comparan tal como se leyeron: el lab y el pipeline pueden trocear "
        "distinto el punto de venta y el número (0-padded vs. no)."
    ),
    "razon_social_emisor": (
        "Texto libre: las dos puntas pueden transcribir distinta porción del "
        "rótulo. ⚠️ Medido: la referencia puede traer MÁS que el pipeline "
        "('LUCIE SABORES S.R.L. (SANTA LUCIA VGG)' vs. 'SANTA LUCIA VGG'), así "
        "que la diferencia no siempre es ruido del lab."
    ),
    "cuit_emisor": (
        "⚠️ **Puede ser un ACIERTO del pipeline.** La referencia tiene 5 de 28 CUIT "
        "con el dígito verificador inválido (ver README.md): si el documento está "
        "en esa lista, la diferencia puede ser un error DE LA REFERENCIA."
    ),
}

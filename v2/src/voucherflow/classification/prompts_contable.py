"""Prompts contables versionados de la cadena 01→02→03 (F3 / T-304, E-CLAS-2).

**Fase**: F3 (clasificación) · **Tarea**: T-304 · **Épica**: E-CLAS-2.

Porta a la librería los tres prompts YAML de ``prompts/01..03-*.yaml`` de v1
(usados por ``v1/classification_pipeline.py``), **versionados en código**
(ADR-005) con el mismo esquema de identificador que ``prompt_qween.py``
(``qween-gate@2``) y ``prompt_tipo_comprobante.py`` (``tipo-comprobante@1``):

============================  ==================================================
Identificador                 Etapa
============================  ==================================================
``contable-01@1``             paso 01 — hasta 3 centros de costo
``contable-02@1``             paso 02 — hasta 3 macro categorías
``contable-03@1``             paso 03 — concepto + código final + condición
============================  ==================================================

Por qué el texto vive en código y no en un YAML leído en runtime
----------------------------------------------------------------
El plan pide **paridad** con v1 pero **sin acoplamiento a ``v1/``** (regla dura
F3-subplan §4: "sin acoplamiento a ``v1/``: nada de ``sys.path`` ni imports de
``classification_pipeline.py`` … ni prompts de ``v1/``"). Un YAML en runtime
obligaría a leer archivos de datos y a decidir su ubicación en el paquete; el
texto versionado en código es lo que ya hacen F2 y T-302, es auditable con
``git`` y permite registrar la versión exacta en la trazabilidad
(``RegistroEtapa.version_prompt``, E-CONC-5).

**Las tablas de clasificación se portan literales.** Son el conocimiento de
negocio del contador (centros de costo, macro categorías, conceptos y códigos
finales por condición impositiva): reescribirlas o resumirlas cambiaría el
resultado y rompería la paridad que T-305 debe verificar.

Sustitución de placeholders
---------------------------
v1 hacía ``user_prompt.replace("{{clave}}", valor)`` (``v1/lib/pipeline.py``).
Se conserva ese mecanismo **a propósito**: los prompts contienen llaves de los
ejemplos JSON (``{ "centros_costos": [...] }``), así que ``str.format`` fallaría
o exigiría escapar todo. :func:`renderizar_user` hace el reemplazo explícito y
**exige** que no queden placeholders sin resolver (un ``{{monto}}`` olvidado
llegaría al modelo como texto literal y contaminaría la clasificación).
"""

from __future__ import annotations

import re
from typing import Any, Mapping

# ---------------------------------------------------------------------------
# Versiones de los prompts (ADR-005: trazabilidad por versión)
# ---------------------------------------------------------------------------

#: Versión del prompt del paso 01 (centros de costo).
VERSION_PROMPT_CONTABLE_01 = "contable-01@1"

#: Versión del prompt del paso 02 (macro categorías).
VERSION_PROMPT_CONTABLE_02 = "contable-02@1"

#: Versión del prompt del paso 03 (concepto + código final).
VERSION_PROMPT_CONTABLE_03 = "contable-03@1"

#: Condiciones impositivas válidas del paso 03 (enum del prompt de v1 y del
#: contrato ``ClasificacionContable.condicion_impositiva``).
CONDICIONES_IMPOSITIVAS: tuple[str, ...] = ("21", "10_5", "27", "2_5", "exento_no_gravado")

#: Condición impositiva por defecto (igual que el CLI de v1).
CONDICION_IMPOSITIVA_DEFAULT = "21"

#: Valor con el que la cadena marca los datos que todavía no extrae (F4). Es
#: **provisional documentado** (F3-subplan §2.7): ``proveedor`` y ``monto`` se
#: pasan como "no informado" hasta que F4 entregue los campos extraídos; cuando
#: eso ocurra, la cadena los consume sin cambiar su contrato
#: (``base_values``).
VALOR_NO_INFORMADO = "no informado"

# ---------------------------------------------------------------------------
# System prompts (portados literales de prompts/01..03-*.yaml)
# ---------------------------------------------------------------------------

SYSTEM_PROMPT_CONTABLE_01 = """\
Clasificá el gasto y devolvé SOLO JSON válido, sin markdown ni texto extra.
Ordená hasta tres centros de costo, del más probable al menos probable.
No inventes opciones ni repitas códigos.

Interpretá estos campos:
- proveedor: comercio que emitió el comprobante.
- descripcion: concepto o ítems del gasto. Resumí varios ítems por rubro
  común; si no existe, elegí el de mayor monto.
- monto: importe total.

Aplicá esta tabla:

| Código | Centro      | Clasificar si                                                        | Excluir si                                    |
|--------|-------------|-----------------------------------------------------------------------|------------------------------------------------|
| CC0001 | Nuevas      | Vehículos 0 km: venta, compra, patentamiento, preparación.            | Usados, repuestos, talleres, motores, adm.      |
| CC0002 | Usadas      | Vehículos usados: compra, venta, reacondicionamiento.                 | 0 km, repuestos, motores, taller general.       |
| CC0003 | Motores     | Motores completos: compra, reparación, mantenimiento, componentes.   | Repuestos generales, vehículos, maquinaria.     |
| CC0004 | Repuestos   | Repuestos, accesorios, taller, service, mantenimiento de vehículos propios o de clientes. | Vehículos completos, motores, chapería. |
| CC0005 | Servicios   | Servicios internos o al cliente sin sector específico.                | Administración, repuestos, chapería, agro.      |
| CC0006 | Indirectos  | Gastos administrativos, RRHH, oficina, IT, financieros e impuestos. Valor por defecto. | Gastos claramente asignables a otro centro. |
| CC0007 | Agro        | Actividades, repuestos y servicios agropecuarios.                     | Vehículos, motores, administración.             |
| CC0008 | Chapería    | Chapa, pintura y reparación estética.                                 | Mecánica general, motores, repuestos.           |

Devolvé hasta tres opciones en este formato. Poné primero la clasificación
principal y devolvé menos opciones si no hay alternativas razonables:
{
  "centros_costos": [
    {
      "codigo_centro_costo": "CCNNNN",
      "centro": "nombre del centro tal como aparece en la tabla",
      "confianza": "alta | media | baja",
      "senal_usada": "proveedor | descripcion | proveedor+descripcion | ninguna",
      "justificacion": "una oración breve citando qué parte del proveedor o la descripción determinó la clasificación"
    }
  ]
}

Aplicá estas reglas:
- Buscá primero la señal específica en descripcion y luego en proveedor.
- Priorizá la fila cuya regla "Clasificar si" coincida y cuya exclusión no
  aplique.
- Ante contradicciones, priorizá la exclusión más específica y bajá la
  confianza.
- Usá monto sólo para desempatar; no lo uses como señal única.
- Asigná CC0006 para viáticos, comidas, combustible sin destino, peajes,
  estacionamiento, oficina, IT, cursos, farmacia, librería, limpieza y
  gastos administrativos, salvo evidencia explícita de otro centro.
- Si no encuentres una señal específica, devolvé sólo CC0006 con confianza
  baja y senal_usada ninguna.
- Usá "alta", "media" o "baja" para confianza.
- Usá sólo los cinco campos definidos para cada opción.
"""

SYSTEM_PROMPT_CONTABLE_02 = """\
Clasificá el gasto en hasta tres macro categorías. Ordenalas por
probabilidad y devolvé SOLO JSON válido, sin markdown ni texto extra.
No repitas códigos ni inventes opciones.

Usá estos datos:
- centro_costo: código ya asignado.
- proveedor: comercio emisor.
- descripcion: rubro o detalle dominante del gasto.
- monto: importe total.

Aplicá esta tabla:
| Código | Macro | Señales principales |
|--------|-------|---------------------|
| MC01 | RRHH | Uniformes, cursos y asistencia médica del personal. |
| MC02 | Movilidad y Representación | Viáticos, viajes, combustible, peajes, regalos y agasajos comerciales. |
| MC03 | Marketing y Comercial | Publicidad, promoción, cartelería y merchandising. |
| MC04 | Infraestructura y Edificios | Edificios, alquileres, servicios edilicios, limpieza y seguridad edilicia. |
| MC05 | Tecnología y Comunicaciones | Software, telefonía, internet, computación y licencias. |
| MC06 | Administración y Oficina | Papelería, artículos de escritorio e insumos administrativos genéricos. |
| MC07 | Operaciones y Logística | Materiales, insumos, herramientas operativas, fletes y envíos. |
| MC08 | Activos y Mantenimiento | Vehículos propios, maquinaria y vehículos en stock. |
| MC09 | Servicios Profesionales | Honorarios, asesoramiento y gastos legales. |
| MC10 | Impuestos y Tasas | Impuestos, tasas y débitos/créditos bancarios. |
| MC11 | Servicios Generales | Servicios sin otra categoría específica. Fallback de servicios. |
| MC12 | Seguros | Pólizas y coberturas. |
| MC13 | Gastos Financieros | Gastos bancarios, intereses, tarjetas y diferencias de cambio. |
| MC14 | Pérdidas y Ajustes | Incobrables, faltantes y gastos no-servicio sin encaje. |
| MC15 | Agricultura | Semillas y agroquímicos; usala sólo con centro_costo CC0007. |

Devolvé este formato. Poné primero la opción principal y devolvé menos
opciones si no existen alternativas razonables:
{
  "macro_categorias": [
    {
      "macro_categoria": "MCNN",
      "nombre": "nombre exacto de la tabla",
      "confianza": "alta | media | baja",
      "criterio_inferido": true,
      "justificacion": "una oración breve basada en proveedor o descripcion"
    }
  ]
}

Aplicá estas reglas:
- Buscá primero señales explícitas en descripcion y luego en proveedor.
- Priorizá la macro cuya señal coincida mejor con el gasto.
- Usá monto sólo para desempatar; no lo uses como señal única.
- Usá MC15 sólo si centro_costo es exactamente CC0007.
- Si parece agro pero el centro no es CC0007, elegí otra macro y explicá la inconsistencia.
- Usá MC11 si el gasto es un servicio sin categoría específica.
- Usá MC14 si no es un servicio y no encaja en otra macro.
- Usá MC06 sólo para oficina y administración genérica; no la uses como comodín.
- Bajá la confianza ante señales débiles o contradictorias.
- Usá sólo los cinco campos definidos para cada opción.
"""

SYSTEM_PROMPT_CONTABLE_03 = """\
Determiná el concepto y el código final. Devolvé SOLO JSON válido, sin
markdown ni texto extra.

Usá estos datos:
- macro_categoria: macro asignada en el paso anterior.
- proveedor: comercio emisor.
- descripcion: rubro o detalle dominante.
- monto: importe total.
- condicion_impositiva: 21, 10_5, 27, 2_5 o exento_no_gravado.

Elegí conceptos sólo dentro de la macro recibida:
| Macro | Concepto | Nombre | Cuenta |
|-------|----------|--------|---------|
| MC01 | CT001 | Indumentaria personal | 4221,05 |
| MC01 | CT002 | Entrenamiento al personal | 4221,07 |
| MC01 | CT003 | Asistencia médica al personal | 4221,25 |
| MC02 | CT004 | Movilidad y Viáticos | 4221,08 |
| MC02 | CT005 | Cortesías | 4221,14 |
| MC02 | CT006 | Gastos de representación | 4221,34 |
| MC03 | CT007 | Publicidad | 4221,10 |
| MC04 | CT008 | Mantenimiento edificio | 4221,11 |
| MC04 | CT009 | Alquileres | 4221,24 |
| MC04 | CT010 | Luz, agua y gas | 4221,29 |
| MC04 | CT011 | Limpieza | 4221,30 |
| MC04 | CT012 | Seguridad | 4221,35 |
| MC05 | CT013 | Suscripciones | 4221,19 |
| MC05 | CT014 | Teléfono, Correo e Internet | 4221,20 |
| MC05 | CT015 | Gastos / Servicios de computación | 4221,22 |
| MC06 | CT016 | Papelería y artículos de oficina | 4221,15 |
| MC07 | CT017 | Materiales, insumos y herramientas | 4221,16 |
| MC07 | CT018 | Fletes | 4221,13 |
| MC08 | CT019 | Gastos vehículos | 4221,12 |
| MC08 | CT020 | Mantenimiento maquinarias | 4221,27 |
| MC08 | CT021 | Mantenimiento vehículos en stock | 4221,28 |
| MC09 | CT022 | Honorarios | 4221,18 |
| MC09 | CT023 | Gastos legales | 4221,32 |
| MC10 | CT024 | Impuestos y tasas | 4221,21 |
| MC10 | CT025 | Impuesto Ley 25.413 | 4223,02 |
| MC11 | CT026 | Gastos por servicios varios | 4221,23 |
| MC12 | CT027 | Seguros | 4221,31 |
| MC13 | CT028 | Gastos bancarios | 4223,01 |
| MC13 | CT029 | Comisiones tarjetas | 4223,03 |
| MC13 | CT030 | Intereses pagados | 4223,04 |
| MC13 | CT031 | Intereses financiación | 4223,05 |
| MC13 | CT032 | Diferencia de cambio | 4221,33 |
| MC14 | CT033 | Incobrables | 4221,36 |
| MC14 | CT034 | Faltantes de caja | 4223,99 |
| MC14 | CT035 | Gastos varios | 4221,99 |
| MC15 | CT036 | Semillas | 4221,90 |
| MC15 | CT037 | Agroquímicos | 4221,91 |

Buscá el código final en esta tabla. Usá el primer código si una celda
contiene varios; copiá todos en candidatos_codigo_final:
| CT | 21% | 10,5% | 27% | 2,5% | Exento/No gravado |
|----|-----|--------|-----|------|-------------------|
| CT001 | 164 | 79 | — | — | — |
| CT002 | 73 | 241 | — | — | 57 |
| CT003 | 74 | 54 | — | — | — |
| CT004 | 7 | 41 | — | — | 42 |
| CT005 | 177 | — | — | — | 178 |
| CT006 | 215 | 67 | — | — | 153 |
| CT007 | 44 | 222 | — | 71 | 78 |
| CT008 | 53 | 112 | 342 | — | 117 |
| CT009 | 407 | — | — | — | 201 |
| CT010 | 264 | 328 | — | — | 64 |
| CT011 | 172 | — | — | — | 240 |
| CT012 | 173 | 263 | — | — | 235 |
| CT013 | 163 | — | — | — | 196 |
| CT014 | 24 | — | 65 | — | 66 |
| CT015 | 13 | 28 | 170 | — | 116 |
| CT016 | 55 | 181 | — | — | 189 |
| CT017 | 48 | 49 | — | — | 50 |
| CT018 | 92 | 333 | — | 193 | 194 |
| CT019 | 47 | 397 | — | — | 115 |
| CT020 | 46 | 135/167 | — | — | 166 |
| CT021 | 45/142 | — | — | — | 191 |
| CT022 | 20 | — | — | — | 76 |
| CT023 | 21 | 26 | — | — | 133 |
| CT024 | — | — | — | — | — |
| CT025 | — | — | — | — | — |
| CT026 | 18 | 27 | 111 | 157 | 144 |
| CT027 | 307 | — | — | — | 308 |
| CT028 | 108/216 | 152 | — | — | 118/225 |
| CT029 | 169 | — | — | — | 312 |
| CT030 | 340 | 109 | — | — | — |
| CT031 | 336 | 102/242 | — | — | 151 |
| CT032 | 68/93 | 99 | — | — | 195/221 |
| CT033 | — | — | — | — | — |
| CT034 | — | — | — | — | — |
| CT035 | 12 | 162 | — | — | 62 |
| CT036 | 126 | — | — | — | — |
| CT037 | — | 218 | — | 219 | — |

Devolvé:
{
  "macro_categoria": "MCNN",
  "concepto": "CTNNN",
  "nombre_concepto": "nombre exacto",
  "cuenta_contable": "NNNN,NN",
  "condicion_impositiva": "valor recibido",
  "codigo_final": "primer código de la celda o null",
  "candidatos_codigo_final": [],
  "requiere_revision_humana": false,
  "confianza": "alta | media | baja",
  "criterio_inferido": true,
  "justificacion": "una oración breve basada en proveedor o descripcion"
}

Aplicá estas reglas:
- Elegí el concepto que mejor coincida con descripcion y proveedor.
- Si macro_categoria es MC15, elegí CT036 para semillas o CT037 para
  agroquímicos.
- Si ningún concepto aplica y el gasto es un servicio, usá CT026/MC11.
- Si ningún concepto aplica y no es un servicio, usá CT035/MC14.
- Para un fallback, cambiá macro_categoria, usá confianza baja y explicalo.
- Si la celda tiene varios códigos separados por /, usá el primero,
  copiá todos en candidatos_codigo_final y poné requiere_revision_humana true.
- Si la celda es —, poné codigo_final null, requiere_revision_humana true
  y explicá que no existe código para esa combinación.
- Usá sólo los campos definidos.
"""

# ---------------------------------------------------------------------------
# User prompts (plantillas con placeholders ``{{clave}}``)
# ---------------------------------------------------------------------------

USER_PROMPT_CONTABLE_01 = """\
Clasificá el siguiente gasto según la tabla de centros de costo.

proveedor: {{proveedor}}
descripcion: {{descripcion}}
monto: {{monto}}
"""

USER_PROMPT_CONTABLE_02 = """\
Clasificá el siguiente gasto.

centro_costo: {{centro_costo}}
proveedor: {{proveedor}}
descripcion: {{descripcion}}
monto: {{monto}}
"""

USER_PROMPT_CONTABLE_03 = """\
Determiná el concepto y código final.

macro_categoria: {{macro_categoria}}
proveedor: {{proveedor}}
descripcion: {{descripcion}}
monto: {{monto}}
condicion_impositiva: {{condicion_impositiva}}
"""

#: Definición completa de cada paso: clave del JSON de ``pasos``, versión del
#: prompt, system prompt, plantilla de user y los placeholders que exige.
PASOS_CONTABLES: dict[str, dict[str, Any]] = {
    "01": {
        "clave": "01_centro_costo",
        "version": VERSION_PROMPT_CONTABLE_01,
        "system": SYSTEM_PROMPT_CONTABLE_01,
        "user": USER_PROMPT_CONTABLE_01,
        "placeholders": ("proveedor", "descripcion", "monto"),
        "etapa": "paso_01_centro_costo",
    },
    "02": {
        "clave": "02_macro_categoria",
        "version": VERSION_PROMPT_CONTABLE_02,
        "system": SYSTEM_PROMPT_CONTABLE_02,
        "user": USER_PROMPT_CONTABLE_02,
        "placeholders": ("centro_costo", "proveedor", "descripcion", "monto"),
        "etapa": "paso_02_macro_categoria",
    },
    "03": {
        "clave": "03_concepto_codigo_final",
        "version": VERSION_PROMPT_CONTABLE_03,
        "system": SYSTEM_PROMPT_CONTABLE_03,
        "user": USER_PROMPT_CONTABLE_03,
        "placeholders": (
            "macro_categoria",
            "proveedor",
            "descripcion",
            "monto",
            "condicion_impositiva",
        ),
        "etapa": "paso_03_concepto_codigo_final",
    },
}

#: Regex de los placeholders ``{{clave}}`` (para detectar los no resueltos).
_PATRON_PLACEHOLDER = re.compile(r"\{\{\s*([a-zA-Z_][a-zA-Z0-9_]*)\s*\}\}")


class PlaceholderFaltante(ValueError):
    """Falta un valor para un placeholder de la plantilla del ``user`` (T-304).

    Es un error de contrato del **llamador** (no del modelo): el prompt no debe
    llegar nunca al modelo con ``{{monto}}`` en el texto, porque el modelo
    clasificaría sobre una cadena literal.
    """


def renderizar_user(
    paso: str, valores: Mapping[str, Any] | None = None, **extra: Any
) -> str:
    """Renderiza la plantilla ``user`` de un paso contable (T-304).

    Sustituye cada ``{{clave}}`` por su valor **tal cual v1**
    (``str.replace``; ver la nota del módulo sobre por qué no se usa
    ``str.format``). Los valores ``None`` se renderizan como
    :data:`VALOR_NO_INFORMADO` — la cadena es explícita sobre lo que no sabe, y
    el prompt de v1 ya contempla ese texto ("no informado") como dato ausente.

    Argumentos:
        paso: ``"01"``, ``"02"`` o ``"03"``.
        valores: diccionario de valores (p. ej. ``base_values`` ampliado con
            ``centro_costo``/``macro_categoria``/``condicion_impositiva``).
        extra: valores adicionales (se combinan con ``valores``; ``extra`` gana).

    Lanza:
        ``KeyError`` si el paso no existe.
        :class:`PlaceholderFaltante` si la plantilla pide un valor que no se
        pasó (mensaje con el nombre del placeholder y la lista de los provistos).

    Devuelve:
        El texto del mensaje ``user`` con todos los placeholders resueltos.
    """
    if paso not in PASOS_CONTABLES:
        raise KeyError(
            f"Paso contable desconocido: {paso!r}. Válidos: "
            f"{sorted(PASOS_CONTABLES)} (T-304)."
        )
    definicion = PASOS_CONTABLES[paso]
    disponibles: dict[str, Any] = {**(valores or {}), **extra}

    texto = str(definicion["user"])
    for placeholder in definicion["placeholders"]:
        if placeholder not in disponibles:
            raise PlaceholderFaltante(
                f"Falta el valor '{placeholder}' para el paso {paso} "
                f"(placeholders del paso: {definicion['placeholders']}; "
                f"provistos: {sorted(disponibles)}) — T-304."
            )
        valor = disponibles[placeholder]
        texto = texto.replace(
            f"{{{{{placeholder}}}}}",
            VALOR_NO_INFORMADO if valor is None else str(valor),
        )

    restantes = _PATRON_PLACEHOLDER.findall(texto)
    if restantes:
        raise PlaceholderFaltante(
            f"Quedaron placeholders sin resolver en el paso {paso}: "
            f"{sorted(set(restantes))} (T-304)."
        )
    return texto


def construir_messages_contable(
    paso: str, valores: Mapping[str, Any] | None = None, **extra: Any
) -> list[dict[str, str]]:
    """Construye los ``messages`` de ``OllamaClient.ask`` para un paso (T-304).

    Devuelve ``[system, user]`` con el system prompt literal del paso y el
    ``user`` renderizado por :func:`renderizar_user`. Mismo patrón que
    ``construir_messages_qween`` (F2) y
    ``construir_messages_tipo_comprobante`` (T-302), para que el cliente sea
    intercambiable.
    """
    if paso not in PASOS_CONTABLES:
        raise KeyError(
            f"Paso contable desconocido: {paso!r}. Válidos: "
            f"{sorted(PASOS_CONTABLES)} (T-304)."
        )
    definicion = PASOS_CONTABLES[paso]
    return [
        {"role": "system", "content": str(definicion["system"])},
        {"role": "user", "content": renderizar_user(paso, valores, **extra)},
    ]


def condicion_impositiva_no_valida(condicion: str | None) -> str | None:
    """Valida la condición impositiva del paso 03 (T-304).

    Devuelve el **mensaje de error** (o ``None`` si es válida). Se devuelve el
    mensaje en lugar de lanzar para que el CLI decida cómo reportarlo (v1 no
    validaba nada y una condición desconocida llegaba al prompt como texto
    libre, con la consiguiente clasificación inventada).

    ``None``/vacío es válido: significa "usá el default" (:data:`CONDICION_IMPOSITIVA_DEFAULT`).
    """
    if condicion is None or not str(condicion).strip():
        return None
    valor = str(condicion).strip()
    if valor not in CONDICIONES_IMPOSITIVAS:
        return (
            f"condición impositiva inválida: {valor!r}. "
            f"Válidas: {', '.join(CONDICIONES_IMPOSITIVAS)} (T-304)."
        )
    return None


__all__ = [
    "VERSION_PROMPT_CONTABLE_01",
    "VERSION_PROMPT_CONTABLE_02",
    "VERSION_PROMPT_CONTABLE_03",
    "CONDICIONES_IMPOSITIVAS",
    "CONDICION_IMPOSITIVA_DEFAULT",
    "VALOR_NO_INFORMADO",
    "SYSTEM_PROMPT_CONTABLE_01",
    "SYSTEM_PROMPT_CONTABLE_02",
    "SYSTEM_PROMPT_CONTABLE_03",
    "USER_PROMPT_CONTABLE_01",
    "USER_PROMPT_CONTABLE_02",
    "USER_PROMPT_CONTABLE_03",
    "PASOS_CONTABLES",
    "PlaceholderFaltante",
    "renderizar_user",
    "construir_messages_contable",
    "condicion_impositiva_no_valida",
]

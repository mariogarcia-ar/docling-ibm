"""Los esquemas de las dos respuestas: la de comparación y la de lectura.

Son la **forma** que el consumidor espera. Cuando el proveedor impone el esquema
en el servidor (OpenAI con ``strict``), esto es la garantía; cuando no (DeepSeek,
Gemini), esto es lo que valida localmente y lo que genera el ejemplo del prompt.

Los objetos siguen siendo estrictos —todas las propiedades presentes, sin claves
de más— precisamente porque en los proveedores sin ``strict`` es lo único que
controla la forma.

Se construyen con helpers (``_texto_o_null``, ``_numero_o_null``…) en vez de
escribir el JSON a mano: un campo nuevo se agrega en un lugar y la forma queda
consistente.
"""

from __future__ import annotations

from typing import Any

from ..schemas.evidence import ClaseDocumento

#: Vocabulario cerrado de ``es_comprobante`` en el lab: el **mismo** del gate de
#: F2 (:class:`~voucherflow.validation.qween.VeredictoGate`) y del contrato F0
#: (:class:`~voucherflow.schemas.evidence.ClaseDocumento`).
#:
#: ⚠️ Se importa de ``schemas`` y **no** se copia: con dos listas, el lab podría
#: emitir un valor que el pipeline no conoce y la comparación de las dos puntas
#: fallaría en silencio (o peor, compararía vocabularios distintos creyendo que
#: son el mismo). ``schemas/evidence.py`` no importa ningún módulo del pipeline,
#: así que no hay ciclo.
#:
#: ⚠️ Los valores van en **minúsculas** (``no_comprobante``), a diferencia del
#: vocabulario de ``tipo_comprobante`` (``A``/``B``/``INTERNACIONAL``): es el
#: valor del gate, no una letra impresa en el papel. El normalizador de
#: vocabulario de la extracción pasa a MAYÚSCULAS, así que este campo **no** usa
#: esa regla (ver ``NORM_CLASE_DOCUMENTO`` en ``extraction/key_value.py``).
CLASES_DOCUMENTO: tuple[str, ...] = tuple(clase.value for clase in ClaseDocumento)


# --------------------------------------------------------------------------- #
# Esquemas (JSON Schema para la **validación local** de la respuesta)
# --------------------------------------------------------------------------- #
#
# ⚠️ DeepSeek **no** impone el esquema en el servidor: estos esquemas no viajan
# en la petición como los ``json_schema`` estrictos de OpenAI. Se usan para
# **validar localmente** lo que devuelve el modelo y para pedirle la corrección
# con el error concreto como feedback (ver :func:`llamar_api`).
#
# Por eso los objetos siguen siendo estrictos —todas las propiedades en
# ``required``, ``additionalProperties: false``, uniones con ``anyOf``, sin
# ``const``—: así la validación local detecta cualquier campo faltante o de más,
# que es justamente lo que el servidor no controla acá.


def _obj(props: dict[str, Any], descripcion: str | None = None) -> dict[str, Any]:
    """Objeto estricto: todas las propiedades requeridas, sin adicionales."""
    obj: dict[str, Any] = {
        "type": "object",
        "properties": props,
        "required": list(props),
        "additionalProperties": False,
    }
    if descripcion:
        obj["description"] = descripcion
    return obj


def _texto(descripcion: str | None = None) -> dict[str, Any]:
    d: dict[str, Any] = {"type": "string"}
    if descripcion:
        d["description"] = descripcion
    return d


def _texto_o_null(descripcion: str | None = None) -> dict[str, Any]:
    d: dict[str, Any] = {"anyOf": [{"type": "string"}, {"type": "null"}]}
    if descripcion:
        d["description"] = descripcion
    return d


def _valor_o_null(descripcion: str | None = None) -> dict[str, Any]:
    """Valor leído de la imagen: string, número o ``null`` (no legible)."""
    d: dict[str, Any] = {
        "anyOf": [{"type": "string"}, {"type": "number"}, {"type": "null"}]
    }
    if descripcion:
        d["description"] = descripcion
    return d


def _numero_o_null(descripcion: str | None = None) -> dict[str, Any]:
    d: dict[str, Any] = {"anyOf": [{"type": "number"}, {"type": "null"}]}
    if descripcion:
        d["description"] = descripcion
    return d


def _booleano_o_null(descripcion: str | None = None) -> dict[str, Any]:
    d: dict[str, Any] = {"anyOf": [{"type": "boolean"}, {"type": "null"}]}
    if descripcion:
        d["description"] = descripcion
    return d


def _bool_o_no_verificable(descripcion: str | None = None) -> dict[str, Any]:
    d: dict[str, Any] = {
        "anyOf": [
            {"type": "boolean"},
            {"type": "string", "enum": ["no_verificable"]},
        ]
    }
    if descripcion:
        d["description"] = descripcion
    return d


def _lista_textos(descripcion: str | None = None) -> dict[str, Any]:
    d: dict[str, Any] = {"type": "array", "items": {"type": "string"}}
    if descripcion:
        d["description"] = descripcion
    return d


def _campo_comparado(descripcion: str | None = None) -> dict[str, Any]:
    """Campo del modo ``validar``: valor del comprobante vs valor cargado."""
    return _obj(
        {
            "valor_comprobante": _valor_o_null("Valor leído de la imagen."),
            "valor_cargado": _valor_o_null("Valor cargado en Mendel."),
            "coincide": _bool_o_no_verificable("Coincidencia, o no_verificable."),
            "observacion": _texto_o_null("Observación breve."),
        },
        descripcion,
    )


def esquema_validacion() -> dict[str, Any]:
    """Esquema del modo ``validar`` (comparación contra los datos de Mendel)."""
    return _obj(
        {
            "estado_global": {
                "type": "string",
                "enum": ["OK", "REVISAR", "INCOMPLETO"],
                "description": "Estado global de la validación.",
            },
            "resumen": _texto("1-2 frases con el hallazgo principal."),
            "campos": _obj(
                {
                    "tipo_comprobante": _campo_comparado("Regla 1."),
                    "razon_social_emisor": _campo_comparado("Regla 2."),
                    "cuit_emisor": _campo_comparado("Regla 3."),
                    "fecha_emision": _campo_comparado("Regla 4."),
                    "nro_factura": _campo_comparado("Regla 5."),
                    "moneda": _campo_comparado("Regla 6."),
                    "subtotal": _campo_comparado("Regla 7: NETO si discrimina."),
                    "impuestos": _obj(
                        {
                            "iva": _campo_comparado(),
                            "impuestos_internos": _campo_comparado(),
                            "percepciones_iibb": _campo_comparado(),
                            "otros": _campo_comparado(),
                        },
                        "Regla 8: desglose de impuestos.",
                    ),
                    "monto_no_gravado": _obj(
                        {
                            "valor_cargado": _numero_o_null(
                                "Valor cargado en Mendel (ajuste manual)."
                            ),
                            "consistente_con_total": {
                                "anyOf": [
                                    {"type": "boolean"},
                                    {"type": "string", "enum": ["no_aplica"]},
                                ],
                                "description": "Regla 9.",
                            },
                            "observacion": _texto_o_null(),
                        },
                        "Regla 9: ajuste manual, no verificable contra la imagen.",
                    ),
                    "importe_total_facturado": _obj(
                        {
                            "valor_comprobante": _valor_o_null(
                                "Total impreso en el comprobante."
                            ),
                            "valor_cargado": _numero_o_null("Valor cargado en Mendel."),
                            "coincide": _bool_o_no_verificable(),
                            "diferencia": _numero_o_null(
                                "valor_cargado - valor_comprobante."
                            ),
                            "diferencia_explicada_por_monto_no_gravado": {
                                "anyOf": [
                                    {"type": "boolean"},
                                    {"type": "string", "enum": ["no_aplica"]},
                                ]
                            },
                            "observacion": _texto_o_null(),
                        },
                        "Regla 10.",
                    ),
                    "categoria_gasto": _obj(
                        {
                            "valor_cargado": _texto_o_null(),
                            "consistente_con_rubro_emisor": _bool_o_no_verificable(),
                            "observacion": _texto_o_null(),
                        },
                        "Regla 11.",
                    ),
                    "notas": _obj(
                        {
                            "valor_cargado": _texto_o_null(),
                            "aclaracion_sugerida": _booleano_o_null(
                                "Regla 12: si convendría una aclaración."
                            ),
                            "observacion": _texto_o_null(),
                        },
                        "Regla 12.",
                    ),
                    "cantidad_comensales_personas": _obj(
                        {
                            "requerido_por_categoria": {"type": "boolean"},
                            "valor_cargado": _numero_o_null(),
                            "observacion": _texto_o_null(),
                        },
                        "Regla 13.",
                    ),
                    "cantidad_litros": _obj(
                        {
                            "requerido_por_categoria": {"type": "boolean"},
                            "valor_comprobante": _numero_o_null(),
                            "valor_cargado": _numero_o_null(),
                            "coincide": _bool_o_no_verificable(),
                            "observacion": _texto_o_null(),
                        },
                        "Regla 14.",
                    ),
                    "centro_de_costo": _obj(
                        {
                            "valor_cargado": _texto_o_null(),
                            "nota": {
                                "type": "string",
                                "enum": ["Informativo, no controlado por aprobadores"],
                            },
                        },
                        "Regla 15: informativo.",
                    ),
                },
                "Resultado por campo.",
            ),
            "discrepancias_criticas": _lista_textos(
                "Campos con problemas relevantes de monto/CUIT/fecha/tipo."
            ),
            "campos_no_legibles": _lista_textos(
                "Campos que no se pudieron leer en la imagen."
            ),
        }
    )


def esquema_extraccion() -> dict[str, Any]:
    """Esquema del modo ``extraer`` (solo lectura de la imagen, sin comparar).

    Plano a propósito: cada campo es un valor simple (``null`` = no legible), lo
    que hace el *diff* determinístico posterior trivial de escribir y auditar.
    """
    return _obj(
        {
            "es_comprobante": {
                "type": "string",
                "enum": [clase.value for clase in ClaseDocumento],
                "description": (
                    "Clase documental: si el documento ES un comprobante fiscal/"
                    "comercial ('comprobante'), si NO lo es ('no_comprobante': un "
                    "DNI, un memo, una foto de pizarra, una captura de pantalla "
                    "que solo muestra un pago, un presupuesto, un resumen de "
                    "tarjeta), o si no alcanza para decidirlo ('indeterminado': "
                    "imagen ilegible o cortada). NO dice qué comprobante es (eso "
                    "es 'tipo_comprobante') ni si sirve para el gasto."
                ),
            },
            "legibilidad": {
                "type": "string",
                "enum": ["buena", "parcial", "mala"],
                "description": "Calidad general de la imagen para leer los datos.",
            },
            "tipo_comprobante": _texto_o_null(
                'Tipo o letra ("A", "B", "C", "090", "099", "INTERNACIONAL", u '
                'otro código AFIP). "INTERNACIONAL" es un comprobante emitido por '
                "un proveedor de otro país (una INVOICE sin letra AFIP); NO es "
                'una Factura E argentina.'
            ),
            "codigo_afip": _texto_o_null('Código impreso, p. ej. "COD. 001".'),
            "razon_social_emisor": _texto_o_null("Razón social del emisor."),
            "cuit_emisor": _texto_o_null("CUIT del emisor tal como está impreso."),
            "digito_verificador_cuit_valido": _booleano_o_null(
                "Si se pudo validar el dígito verificador del CUIT."
            ),
            "fecha_emision": _texto_o_null("Fecha impresa (DD/MM/AAAA u otro formato)."),
            "nro_factura": _texto_o_null("Punto de venta y número."),
            "moneda": _texto_o_null('Moneda ("ARS", "USD", …).'),
            "subtotal": _numero_o_null(
                "Neto gravado, si está discriminado (sin IVA ni otros impuestos)."
            ),
            "no_gravado": _numero_o_null(
                "Importe no gravado, si el comprobante lo muestra aparte."
            ),
            "exento": _numero_o_null(
                "Importe exento (p. ej. «SUBTOT. IMP. EXENTO»), si figura."
            ),
            "discrimina_impuestos": _booleano_o_null(
                "Si el comprobante desglosa impuestos."
            ),
            "iva": _numero_o_null("IVA discriminado."),
            "impuestos_internos": _numero_o_null(),
            "percepciones_iibb": _numero_o_null(),
            "otros_impuestos": _numero_o_null(),
            "importe_total": _numero_o_null("Total impreso en el comprobante."),
            "cierra_aritmetica": _booleano_o_null(
                "Si los importes transcriptos suman el total declarado."
            ),
            "rubro_emisor": _texto_o_null(
                "Rubro/actividad del emisor inferido de la imagen."
            ),
            "categoria_gasto_sugerida": _texto_o_null(
                "Categoría de gasto sugerida según el rubro."
            ),
            "cantidad_comensales_personas": _numero_o_null(
                "Cubiertos/comensales, si el ticket lo detalla."
            ),
            "cantidad_litros": _numero_o_null("Litros cargados, si es combustible."),
            "centro_de_costo": _texto_o_null("Dato informativo si está impreso."),
            "campos_no_legibles": _lista_textos(),
            "observaciones": _texto("Observaciones breves de la lectura."),
        }
    )



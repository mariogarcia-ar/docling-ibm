#!/usr/bin/env python
"""Validación/extracción de comprobantes con la API de OpenAI (visión).

Implementa el prompt de ``prompt_validacion_comprobantes_mendel.md``: recibe la
**imagen del comprobante** y, opcionalmente, los **datos que el empleado cargó
en Mendel**, y devuelve un JSON con el resultado por campo. Pensado para correr
sobre el corpus ya pre-reducido por ``reducir_tokens.py``.

Modos (``--modo``):

- ``validar`` (default): imagen + datos cargados → comparación campo a campo
  ("valor_comprobante" vs "valor_cargado", ``coincide``, ``estado_global``).
  Requiere ``--datos``.
- ``extraer``: **solo extracción**, sin datos cargados. Es la "primera pasada
  barata" que recomienda el §Notas de implementación del prompt.
- ``diff``: no llama a la API. Corre la extracción sólo si hace falta y hace el
  *diff* de campos **en Python**, determinístico y auditable — el prompt mismo
  señala que es más barato y más auditable que pedirle al LLM que compare
  números. Si ya hay una extracción previa, la reutiliza.

**Salida estructurada garantizada.** No se le pide al modelo "devolvé JSON y
ojalá salga bien": se usa ``response_format`` con ``json_schema`` y
``strict: true``, así el formato lo impone el servidor y el parseo no puede
fallar por un texto suelto alrededor del JSON.

**Ahorro de tokens en el prompt.** El template del ``.md`` incluye el bloque con
el **JSON de ejemplo de salida** (unas ~700 palabras). Con el esquema estricto
ese bloque es redundante: el servidor ya obliga a esa forma. Por defecto se
**omite** (``--prompt-fiel`` lo incluye para comparar). Además, el prompt
completo se manda una sola vez como ``system`` y las reglas de negocio quedan
cacheables del lado del servidor.

**Costo y reporte de gastos.** Cada salida guarda los **tokens reales** que
devuelve la API (``usage``), los precios aplicados y el **costo en USD** de esa
llamada, además de la fecha/hora. Con eso, el reporte de gastos
(``--reporte-gastos`` / ``--csv-gastos``) se arma del **histórico** de la carpeta
de salida: totales por día, por modelo y por modo, y una fila por extracción en
CSV. Los precios salen de una tabla de referencia editable (``PRECIOS_REFERENCIA``)
que se puede pisar con ``--precios`` o ``--precio-entrada``/``--precio-salida``;
si un modelo no tiene precio, el costo queda ``null`` y el reporte lo **declara**
en vez de sumar un cero que parece exacto. El período se agrupa con ``--tz``
(``local`` por defecto, o un offset como ``-03:00``).

Credenciales: la clave se lee de ``OPENAI_API_KEY`` (o ``--api-key``), y se
puede tener en un ``.env`` (se carga ``--env`` o ``./.env`` sin pisar lo que ya
esté en el entorno). La clave **nunca** se imprime ni se guarda en la salida.

Uso:
    python scripts/validar_comprobantes_openai.py <ruta|carpeta>... [opciones]
    python scripts/validar_comprobantes_openai.py --reporte-gastos gastos.json

Ejemplos:
    # Extracción pura sobre 5 imágenes (verificar conexión y formato primero)
    python scripts/validar_comprobantes_openai.py ../procesados \\
        --modo extraer --limite 5 --detalle-log

    # Validación contra los datos cargados por el empleado
    python scripts/validar_comprobantes_openai.py ../procesados \\
        --datos datos_mendel.json --workers 4 -o validaciones

    # Diff determinístico en Python (sin gastar tokens de comparación)
    python scripts/validar_comprobantes_openai.py ../procesados \\
        --modo diff --datos datos_mendel.json

    # Reporte de gastos del histórico (no llama a la API)
    python scripts/validar_comprobantes_openai.py --reporte-gastos gastos.json \\
        --csv-gastos gastos.csv --tz -03:00

    # Ver qué se enviaría, sin llamar a la API
    python scripts/validar_comprobantes_openai.py ../procesados --dry-run --limite 3

Códigos de salida: 0 = todo ok (o nada que hacer); 1 = hubo fallos o hay
extracciones sin precio; 2 = error de uso o de configuración;
130 = interrumpido (Ctrl-C).
"""

from __future__ import annotations

import argparse
import base64
import csv
import hashlib
import json
import math
import os
import re
import sys
import unicodedata
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Sequence

# --------------------------------------------------------------------------- #
# Constantes
# --------------------------------------------------------------------------- #

#: Extensiones de imagen que se envían a la API.
EXTENSIONES_IMAGEN = frozenset({".jpg", ".jpeg", ".png", ".webp", ".gif"})

#: MIME por extensión (para el data URL del base64).
MIME_POR_EXTENSION = {
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
    ".png": "image/png",
    ".webp": "image/webp",
    ".gif": "image/gif",
}

#: Modelo por defecto (visión + structured outputs).
MODELO_POR_DEFECTO = "gpt-4o"

#: Versión del prompt, para la trazabilidad del registro (estilo ADR-005).
VERSION_PROMPT = "mendel-validacion@1"

#: Categorías que requieren ``cantidad_comensales_personas`` (regla 13).
#: ⚠️ Se comparan contra ``_norm(categoria)``, que devuelve MAYÚSCULAS sin
#: tildes: el conjunto tiene que estar en esa misma forma o nunca coincide.
CATEGORIAS_CON_COMENSALES = frozenset(
    {"RESTAURANTE", "SUPERMERCADO", "HOSPEDAJE", "BAR", "CAFETERIA", "PANADERIA"}
)

#: Categorías que requieren ``cantidad_litros`` (regla 14).
CATEGORIAS_CON_LITROS = frozenset({"COMBUSTIBLE", "NAFTA", "GASOIL", "GNC"})

#: Tolerancia para comparar montos (redondeo de centavos).
TOLERANCIA_MONTO = 0.011

#: Campos cuya discrepancia lleva el estado global a REVISAR (regla de negocio).
CAMPOS_CRITICOS = (
    "importe_total_facturado",
    "subtotal",
    "impuestos",
    "cuit_emisor",
    "tipo_comprobante",
    "fecha_emision",
)


# --------------------------------------------------------------------------- #
# Entorno / credenciales
# --------------------------------------------------------------------------- #


def cargar_env(ruta: Path | None) -> None:
    """Carga ``KEY=VALUE`` de un ``.env`` sin pisar lo ya presente en el entorno.

    Parser mínimo a propósito: el repo no suma dependencias (``python-dotenv``
    sería una) y el formato que hace falta es trivial.
    """
    if ruta is None or not ruta.is_file():
        return
    for linea in ruta.read_text(encoding="utf-8").splitlines():
        linea = linea.strip()
        if not linea or linea.startswith("#") or "=" not in linea:
            continue
        clave, _, valor = linea.partition("=")
        clave = clave.strip()
        valor = valor.strip().strip("'\"")
        if clave and clave not in os.environ:
            os.environ[clave] = valor


def resolver_api_key(explicita: str | None) -> str | None:
    """Devuelve la clave de API: la del argumento o la del entorno."""
    return explicita or os.environ.get("OPENAI_API_KEY") or None


# --------------------------------------------------------------------------- #
# Prompt
# --------------------------------------------------------------------------- #

#: Bloque de fenced code con el SYSTEM PROMPT y el USER PROMPT del .md.
_RE_BLOQUE_MD = re.compile(r"```[a-zA-Z]*\n(.*?)```", re.DOTALL)


def cargar_prompt(ruta: Path) -> tuple[str, str]:
    """Extrae ``(system_prompt, user_template)`` del ``.md`` del prompt.

    Espera los dos bloques de código del documento, en orden:
    el del encabezado ``## SYSTEM PROMPT`` y el de ``## USER PROMPT (template)``.
    """
    if not ruta.is_file():
        raise FileNotFoundError(f"no se encontró el prompt: {ruta}")
    texto = ruta.read_text(encoding="utf-8")
    bloques = _RE_BLOQUE_MD.findall(texto)
    if len(bloques) < 2:
        raise ValueError(
            f"se esperaban al menos 2 bloques ```…``` en {ruta} "
            f"(SYSTEM PROMPT y USER PROMPT); se encontraron {len(bloques)}"
        )
    return bloques[0].strip(), bloques[1].strip()


#: Desde "Analizá el comprobante … formato JSON:" hasta el final del ejemplo.
_RE_EJEMPLO_SALIDA = re.compile(
    r"\n*Analizá el comprobante contra estos datos y devolvé el resultado en el\n"
    r"siguiente formato JSON:.*\Z",
    re.DOTALL,
)


def _separar_template(user_template: str) -> tuple[str, str]:
    """Parte el template en (cabecera con los datos, bloque de ejemplo de salida)."""
    coincidencia = _RE_EJEMPLO_SALIDA.search(user_template)
    if not coincidencia:
        return user_template, ""
    return user_template[: coincidencia.start()].rstrip(), user_template[
        coincidencia.start() :
    ].strip()


def construir_mensaje_usuario(
    user_template: str, datos: dict | None, *, incluir_ejemplo: bool
) -> str:
    """Arma el texto del mensaje ``user`` a partir del template del ``.md``.

    Sustituye ``[IMAGEN]`` por una nota (la imagen viaja como parte de contenido
    ``image_url``, que es el mecanismo de la API) y reemplaza el bloque de datos
    por el JSON real. Si ``incluir_ejemplo`` es falso, **quita el JSON de ejemplo
    de salida** (el esquema estricto ya lo garantiza).
    """
    cabecera, ejemplo = _separar_template(user_template)
    cabecera = cabecera.replace("[IMAGEN]", "(imagen adjunta a continuación)")

    if siguiente := re.search(r"\{.*\}", cabecera, re.DOTALL):
        bloque_datos = json.dumps(datos, ensure_ascii=False, indent=2) if datos else "{}"
        cabecera = (
            cabecera[: siguiente.start()] + bloque_datos + cabecera[siguiente.end() :]
        )

    if incluir_ejemplo and ejemplo:
        return f"{cabecera}\n\n{ejemplo}"
    return cabecera


#: Instrucción extra del modo ``extraer``. El template del ``.md`` está escrito
#: para *comparar* contra datos cargados; al extraer sin datos hay que pedir
#: explícitamente la transcripción de TODOS los importes y declarar los que no
#: se leen. Sin esto el modelo **omite en silencio** una línea que sí está
#: impresa (se comprobó: no capturó «SUBTOT. IMP. EXENTO: 10.118,12» y el total
#: no cerraba, sin marcarlo como no legible).
INSTRUCCION_EXTRACCION = (
    "\n\n---\n"
    "En esta pasada NO hay datos cargados que comparar: transcribí lo que "
    "muestra el comprobante.\n"
    "\n"
    "Reglas de transcripción:\n"
    "- Recorré TODAS las líneas de importes del comprobante y cargá cada una en "
    "su campo. Son fáciles de saltear: «SUBTOT. IMP. EXENTO», «SUBTOT. IMP. NETO "
    "GRAVADO», «NO GRAVADO», «EXENTO», «PERCEPCIONES», «OTROS TRIBUTOS».\n"
    "- No mezcles los rótulos: el NETO GRAVADO va en «subtotal», y el exento y el "
    "no gravado tienen su propio campo. El «subtotal» NO es el total.\n"
    "- Verificá la aritmética: subtotal + no_gravado + exento + impuestos debe dar "
    "el total. Si no cierra, buscá el importe que falta y cargalo; si aun así no "
    "cierra, poné «cierra_aritmetica» en false y explicá en «observaciones» que la "
    "suma no da.\n"
    "- Si un importe está impreso pero no lo podés leer, poné el campo en null y "
    "listalo en «campos_no_legibles». NUNCA lo dejes afuera sin avisar.\n"
    "- Transcribí sólo lo que ves. No calcules ni inventes importes."
)


def prompt_de_extraccion(usuario: str) -> str:
    """Agrega al mensaje del usuario las reglas de transcripción del modo extraer."""
    return usuario + INSTRUCCION_EXTRACCION


def hash_prompt(*partes: str) -> str:
    """Hash corto del prompt efectivo, para auditar qué prompt produjo cada salida."""
    h = hashlib.sha256()
    for parte in partes:
        h.update(parte.encode("utf-8"))
        h.update(b"\x00")
    return f"sha256:{h.hexdigest()[:16]}"


# --------------------------------------------------------------------------- #
# Esquemas (JSON Schema estricto para structured outputs)
# --------------------------------------------------------------------------- #
#
# Reglas del modo estricto de OpenAI que se respetan en todos los objetos:
#   - TODAS las propiedades figuran en ``required``.
#   - ``additionalProperties: false`` en cada objeto.
#   - Las uniones van con ``anyOf`` (incluye ``{"type": "null"}``).
#   - Sin ``const`` (se usa ``enum`` de un solo valor) y sin restricciones
#     numéricas (``minimum``/``maximum``), para no depender de qué soporta el
#     modelo del momento.


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
            "legibilidad": {
                "type": "string",
                "enum": ["buena", "parcial", "mala"],
                "description": "Calidad general de la imagen para leer los datos.",
            },
            "tipo_comprobante": _texto_o_null(
                'Tipo o letra ("A", "B", "C", "090", "099", u otro código AFIP).'
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


# --------------------------------------------------------------------------- #
# Imagen: codificación y estimación de tokens
# --------------------------------------------------------------------------- #


def _dimensiones(ruta: Path) -> tuple[int, int] | None:
    """Dimensiones de la imagen vía Pillow (best-effort)."""
    try:
        from PIL import Image, ImageOps
    except ImportError:
        return None
    try:
        with Image.open(ruta) as img:
            img = ImageOps.exif_transpose(img)
            return int(img.size[0]), int(img.size[1])
    except Exception:  # noqa: BLE001 - imagen ilegible
        return None


def tokens_imagen(ancho: int, alto: int, detalle: str) -> int:
    """Estimación de tokens de imagen con la fórmula de OpenAI.

    ``low``: 85 tokens fijos. ``high``: se ajusta a 2048x2048, luego el lado
    menor a 768 px, y se cuentan mosaicos de 512x512 (170 tokens cada uno) más
    los 85 de base. Es una **estimación** para comparar antes/después; el número
    que manda es el ``usage`` que devuelve la API.
    """
    if detalle == "low":
        return 85
    ancho, alto = float(ancho), float(alto)
    if max(ancho, alto) > 2048:  # entra en 2048x2048
        escala = 2048 / max(ancho, alto)
        ancho, alto = ancho * escala, alto * escala
    if min(ancho, alto) > 768:  # el lado menor baja a 768
        escala = 768 / min(ancho, alto)
        ancho, alto = ancho * escala, alto * escala
    mosaicos = math.ceil(ancho / 512) * math.ceil(alto / 512)
    return 85 + 170 * mosaicos


def info_imagen(ruta: Path, detalle: str) -> dict[str, Any]:
    """Metadatos de la imagen (peso, mime, dimensiones y tokens estimados).

    No lee el contenido: con ``stat`` alcanza para el peso y Pillow sólo mira el
    encabezado. Es lo que usa ``--dry-run``, que no necesita el base64.
    """
    info: dict[str, Any] = {
        "bytes": ruta.stat().st_size,
        "mime": MIME_POR_EXTENSION.get(ruta.suffix.lower(), "image/jpeg"),
        "detalle": detalle,
    }
    dims = _dimensiones(ruta)
    if dims:
        info["dimensiones"] = list(dims)
        info["tokens_estimados"] = tokens_imagen(dims[0], dims[1], detalle)
    return info


def codificar_imagen(ruta: Path, detalle: str) -> tuple[str, dict[str, Any]]:
    """Data URL base64 de la imagen + metadatos para el registro.

    Devuelve ``(data_url, info)``. El peso se informa aunque supere el límite de
    la API, para que el llamador pueda avisar en vez de mandar una petición
    condenada a fallar por una imagen demasiado grande.
    """
    info = info_imagen(ruta, detalle)
    b64 = base64.b64encode(ruta.read_bytes()).decode("ascii")
    return f"data:{info['mime']};base64,{b64}", info


# --------------------------------------------------------------------------- #
# Llamada a la API
# --------------------------------------------------------------------------- #


@dataclass
class Respuesta:
    """Resultado de una llamada: el JSON validado y el uso reportado."""

    datos: dict[str, Any] | None
    uso: dict[str, Any] = field(default_factory=dict)
    error: str | None = None
    modelo: str = ""
    sin_temperatura: bool = False

    @property
    def ok(self) -> bool:
        return self.error is None and self.datos is not None


def llamar_api(
    cliente: Any,
    *,
    modelo: str,
    sistema: str,
    usuario: str,
    data_url: str,
    esquema: dict[str, Any],
    nombre_esquema: str,
    temperatura: float | None,
    detalle: str,
    max_tokens: int | None,
    esfuerzo: str | None,
) -> Respuesta:
    """Una llamada a Chat Completions con imagen y salida estructurada.

    Si el modelo rechaza ``temperature`` (los de razonamiento no lo aceptan), se
    reintenta una vez sin ese parámetro y se deja constancia en el registro —
    mejor que fallar con un 400 críptico.
    """
    messages = [
        {"role": "system", "content": sistema},
        {
            "role": "user",
            "content": [
                {"type": "text", "text": usuario},
                {
                    "type": "image_url",
                    "image_url": {"url": data_url, "detail": detalle},
                },
            ],
        },
    ]
    extra: dict[str, Any] = {
        "model": modelo,
        "messages": messages,
        "response_format": {
            "type": "json_schema",
            "json_schema": {
                "name": nombre_esquema,
                "strict": True,
                "schema": esquema,
            },
        },
    }
    if temperatura is not None:
        extra["temperature"] = temperatura
    if max_tokens is not None:
        extra["max_completion_tokens"] = max_tokens
    if esfuerzo is not None:
        extra["reasoning_effort"] = esfuerzo

    sin_temperatura = False
    try:
        respuesta = cliente.chat.completions.create(**extra)
    except Exception as exc:  # noqa: BLE001 - se clasifica abajo
        if temperatura is not None and _es_error_de_temperatura(exc):
            # Modelo de razonamiento: reintenta sin temperature.
            extra.pop("temperature", None)
            sin_temperatura = True
            try:
                respuesta = cliente.chat.completions.create(**extra)
            except Exception as exc2:  # noqa: BLE001
                return Respuesta(None, error=_describir_error(exc2), modelo=modelo)
        else:
            return Respuesta(None, error=_describir_error(exc), modelo=modelo)

    contenido = (respuesta.choices[0].message.content or "").strip()
    uso = respuesta.usage
    uso_dict = {
        "prompt_tokens": getattr(uso, "prompt_tokens", None),
        "completion_tokens": getattr(uso, "completion_tokens", None),
        "total_tokens": getattr(uso, "total_tokens", None),
    }
    try:
        datos = json.loads(contenido)
    except json.JSONDecodeError as exc:
        return Respuesta(
            None,
            uso=uso_dict,
            error=f"la respuesta no es JSON válido ({exc}): {contenido[:200]}",
            modelo=modelo,
            sin_temperatura=sin_temperatura,
        )
    return Respuesta(
        datos,
        uso=uso_dict,
        modelo=getattr(respuesta, "model", modelo),
        sin_temperatura=sin_temperatura,
    )


def _es_error_de_temperatura(exc: Exception) -> bool:
    """Detecta el rechazo de ``temperature`` (modelos de razonamiento)."""
    texto = str(exc).lower()
    return "temperature" in texto and (
        "unsupported" in texto or "not supported" in texto or "invalid" in texto
    )


def _describir_error(exc: Exception) -> str:
    """Mensaje de error legible, sin volcar trazas ni credenciales."""
    nombre = type(exc).__name__
    mensaje = str(exc).strip().splitlines()[0] if str(exc).strip() else ""
    if "authentication" in nombre.lower() or "401" in mensaje:
        return (
            f"{nombre}: credencial rechazada. Revisá OPENAI_API_KEY "
            "(se lee del entorno o de --api-key)."
        )
    if "rate" in nombre.lower() or "429" in mensaje:
        return f"{nombre}: límite de tasa alcanzado. Bajá --workers o reintentá. {mensaje}"
    if "connection" in nombre.lower():
        return f"{nombre}: no se pudo conectar con la API. {mensaje}"
    return f"{nombre}: {mensaje}"


# --------------------------------------------------------------------------- #
# Diff determinístico en Python (modo ``diff``)
# --------------------------------------------------------------------------- #


def _norm(texto: Any) -> str:
    """Normaliza para comparar: sin tildes, mayúsculas, espacios colapsados."""
    if texto is None:
        return ""
    sin_acentos = (
        unicodedata.normalize("NFKD", str(texto))
        .encode("ascii", "ignore")
        .decode("ascii")
    )
    return re.sub(r"\s+", " ", sin_acentos).strip().upper()


def _solo_digitos(texto: Any) -> str:
    return re.sub(r"\D", "", str(texto or ""))


def _norm_razon_social(texto: Any) -> str:
    """Normaliza una razón social para comparar (regla 2 del prompt).

    Tolerar diferencias **menores de formato**: mayúsculas, tildes y sobre todo
    «S.A. vs SA» — por eso además de :func:`_norm` se quita toda puntuación y los
    espacios, que es lo único que suele cambiar entre lo impreso y lo cargado.
    """
    return re.sub(r"[^A-Z0-9]", "", _norm(texto))


def a_numero(valor: Any) -> float | None:
    """Convierte a número tolerando ``1.234,56`` y ``1,234.56``."""
    if valor is None or isinstance(valor, bool):
        return None
    if isinstance(valor, (int, float)):
        return float(valor)
    texto = str(valor).strip()
    if not texto:
        return None
    texto = re.sub(r"[^\d.,-]", "", texto)
    if not texto:
        return None
    if "," in texto and "." in texto:
        # El último separador es el decimal.
        if texto.rfind(",") > texto.rfind("."):
            texto = texto.replace(".", "").replace(",", ".")
        else:
            texto = texto.replace(",", "")
    elif "," in texto:
        texto = texto.replace(",", ".")
    try:
        return float(texto)
    except ValueError:
        return None


def _mismo_monto(a: Any, b: Any) -> bool | None:
    """Compara dos montos con tolerancia. ``None`` si alguno no es numérico."""
    na, nb = a_numero(a), a_numero(b)
    if na is None or nb is None:
        return None
    return abs(na - nb) < TOLERANCIA_MONTO


def _a_fecha(valor: Any) -> date | None:
    """Parsea fechas habituales de comprobantes."""
    texto = str(valor or "").strip()
    if not texto:
        return None
    for formato in ("%d/%m/%Y", "%d-%m-%Y", "%Y-%m-%d", "%d/%m/%y", "%d.%m.%Y"):
        try:
            return datetime.strptime(texto, formato).date()
        except ValueError:
            continue
    return None


def _campo(
    valor_comprobante: Any, valor_cargado: Any, coincide: Any, observacion: str | None
) -> dict[str, Any]:
    return {
        "valor_comprobante": valor_comprobante,
        "valor_cargado": valor_cargado,
        "coincide": coincide,
        "observacion": observacion,
    }


def _comparar_impuestos(extraccion: dict, datos: dict) -> dict[str, Any]:
    """Regla 8: sumar lo discriminado y comparar por categoría."""
    carga = datos.get("impuestos") or {}
    discriminado = extraccion.get("discrimina_impuestos")
    pares = {
        "iva": ("iva", "iva"),
        "impuestos_internos": ("impuestos_internos", "impuestos_internos"),
        "percepciones_iibb": ("percepciones_iibb", "percepciones_iibb"),
        "otros": ("otros_impuestos", "otros"),
    }
    salida: dict[str, Any] = {}
    for clave_salida, (clave_ext, clave_carga) in pares.items():
        leido = extraccion.get(clave_ext)
        cargado = carga.get(clave_carga)
        if discriminado is False and a_numero(cargado) not in (None, 0.0):
            salida[clave_salida] = _campo(
                leido,
                cargado,
                False,
                "El comprobante no discrimina impuestos y el campo tiene un valor cargado.",
            )
            continue
        if leido is None and cargado in (None, 0):
            salida[clave_salida] = _campo(
                None, cargado, "no_verificable", "Sin impuesto discriminado en el comprobante."
            )
            continue
        mismo = _mismo_monto(leido or 0, cargado or 0)
        salida[clave_salida] = _campo(
            leido,
            cargado,
            mismo if mismo is not None else "no_verificable",
            None
            if mismo
            else (
                "No discriminado en el comprobante."
                if discriminado is False
                else "Diferencia sin explicar por las reglas de negocio."
            ),
        )
    return salida


def diff_deterministico(extraccion: dict, datos: dict) -> dict[str, Any]:
    """Compara la extracción contra los datos cargados, con las reglas del prompt.

    Devuelve el **mismo formato** que el modo ``validar`` (``estado_global``,
    ``campos``, ``discrepancias_criticas``, ``campos_no_legibles``), para que
    aguas abajo el consumo sea uniforme.
    """
    campos: dict[str, Any] = {}
    criticas: list[str] = []

    def marcar(nombre: str, campo: dict[str, Any], critico: bool) -> None:
        campos[nombre] = campo
        if critico and campo["coincide"] is False:
            criticas.append(nombre)

    # 1. Tipo de comprobante: 090 y 099 son indistintos (regla 1).
    leido_tipo, cargado_tipo = _norm(extraccion.get("tipo_comprobante")), _norm(
        datos.get("tipo_comprobante")
    )
    if {leido_tipo, cargado_tipo} <= {"090", "099"} and leido_tipo and cargado_tipo:
        marcar(
            "tipo_comprobante",
            _campo(
                extraccion.get("tipo_comprobante"),
                datos.get("tipo_comprobante"),
                True,
                "090/099 son indistintos por regla de negocio.",
            ),
            True,
        )
    else:
        marcar(
            "tipo_comprobante",
            _campo(
                extraccion.get("tipo_comprobante"),
                datos.get("tipo_comprobante"),
                (leido_tipo == cargado_tipo) if leido_tipo and cargado_tipo else "no_verificable",
                None if leido_tipo == cargado_tipo else "El tipo leído no coincide con el cargado.",
            ),
            True,
        )

    # 2. Razón social (regla 2: tolera mayúsculas, tildes y «S.A. vs SA»).
    leido_rs = _norm_razon_social(extraccion.get("razon_social_emisor"))
    cargado_rs = _norm_razon_social(datos.get("razon_social_emisor"))
    marcar(
        "razon_social_emisor",
        _campo(
            extraccion.get("razon_social_emisor"),
            datos.get("razon_social_emisor"),
            (leido_rs == cargado_rs) if leido_rs and cargado_rs else "no_verificable",
            None
            if leido_rs == cargado_rs
            else "El nombre difiere más allá del formato (mayúsculas/tildes/puntuación).",
        ),
        False,
    )

    # 3. CUIT: dígito a dígito sobre los 11 dígitos.
    leido_cuit, cargado_cuit = _solo_digitos(extraccion.get("cuit_emisor")), _solo_digitos(
        datos.get("cuit_emisor")
    )
    marcar(
        "cuit_emisor",
        _campo(
            extraccion.get("cuit_emisor"),
            datos.get("cuit_emisor"),
            (leido_cuit == cargado_cuit) if leido_cuit and cargado_cuit else "no_verificable",
            None if leido_cuit == cargado_cuit else "El CUIT leído no coincide con el cargado.",
        ),
        True,
    )

    # 4. Fecha de emisión (se compara como fecha, no como texto).
    fecha_leida, fecha_cargada = _a_fecha(extraccion.get("fecha_emision")), _a_fecha(
        datos.get("fecha_emision")
    )
    marcar(
        "fecha_emision",
        _campo(
            extraccion.get("fecha_emision"),
            datos.get("fecha_emision"),
            (fecha_leida == fecha_cargada)
            if fecha_leida and fecha_cargada
            else "no_verificable",
            None
            if fecha_leida == fecha_cargada
            else "La fecha del comprobante no coincide con la cargada.",
        ),
        True,
    )

    # 5. Nro de factura (se ignora el separador punto de venta-número).
    leido_nro = re.sub(r"[^A-Z0-9]", "", _norm(extraccion.get("nro_factura")))
    cargado_nro = re.sub(r"[^A-Z0-9]", "", _norm(datos.get("nro_factura")))
    marcar(
        "nro_factura",
        _campo(
            extraccion.get("nro_factura"),
            datos.get("nro_factura"),
            (leido_nro == cargado_nro) if leido_nro and cargado_nro else "no_verificable",
            None if leido_nro == cargado_nro else "El número leído no coincide con el cargado.",
        ),
        False,
    )

    # 6. Moneda.
    leido_mon, cargado_mon = _norm(extraccion.get("moneda")), _norm(datos.get("moneda"))
    marcar(
        "moneda",
        _campo(
            extraccion.get("moneda"),
            datos.get("moneda"),
            (leido_mon == cargado_mon) if leido_mon and cargado_mon else "no_verificable",
            None if leido_mon == cargado_mon else "La moneda no coincide.",
        ),
        False,
    )

    # 7. Subtotal (NETO si discrimina; si no discrimina, subtotal == total).
    # Ojo: el neto gravado NO incluye el exento ni el no gravado, así que para
    # comprobantes tipo B/C sin discriminación se contrasta contra el total.
    mismo_subtotal = _mismo_monto(extraccion.get("subtotal"), datos.get("subtotal"))
    if mismo_subtotal is None and extraccion.get("discrimina_impuestos") is False:
        mismo_subtotal = _mismo_monto(
            extraccion.get("importe_total"), datos.get("subtotal")
        )
    marcar(
        "subtotal",
        _campo(
            extraccion.get("subtotal"),
            datos.get("subtotal"),
            mismo_subtotal if mismo_subtotal is not None else "no_verificable",
            None
            if mismo_subtotal
            else "El subtotal cargado no coincide con el neto del comprobante.",
        ),
        True,
    )

    # 7 bis. Exento / no gravado: el prompt los trata aparte del subtotal, así
    # que una diferencia ahí NO es un error de subtotal (es un dato que el
    # comprobante discrimina y la carga puede no reflejar como campo propio).
    for nombre_pdf, clave_ext, clave_datos in (
        ("importe_no_gravado", "no_gravado", "monto_no_gravado"),
        ("importe_exento", "exento", None),
    ):
        leido = extraccion.get(clave_ext)
        cargado = datos.get(clave_datos) if clave_datos else None
        if leido is None and cargado in (None, 0):
            coincide: Any = "no_verificable"
        elif clave_datos is None:
            coincide = "no_verificable"
        else:
            coincide = _mismo_monto(leido, cargado)
        campos[nombre_pdf] = _campo(
            leido,
            cargado,
            coincide,
            None
            if coincide in (True, "no_verificable")
            else "El importe no coincide con el cargado.",
        )

    # 8. Impuestos.
    campos["impuestos"] = _comparar_impuestos(extraccion, datos)
    if any(c["coincide"] is False for c in campos["impuestos"].values()):
        criticas.append("impuestos")

    # 9 y 10. Monto no gravado y total (la diferencia puede estar explicada).
    no_gravado = a_numero(datos.get("monto_no_gravado"))
    total_leido = a_numero(extraccion.get("importe_total"))
    total_cargado = a_numero(datos.get("importe_total_facturado"))
    diferencia = (
        round(total_cargado - total_leido, 2)
        if total_cargado is not None and total_leido is not None
        else None
    )
    if diferencia is None or abs(diferencia) < TOLERANCIA_MONTO:
        explicada: Any = "no_aplica" if not no_gravado else False
        coincide_total: Any = True if diferencia is not None else "no_verificable"
    elif no_gravado and abs(diferencia - no_gravado) < TOLERANCIA_MONTO:
        explicada, coincide_total = True, True
    else:
        explicada, coincide_total = False, False

    campos["monto_no_gravado"] = {
        "valor_cargado": datos.get("monto_no_gravado"),
        "consistente_con_total": (
            "no_aplica"
            if not no_gravado
            else (True if explicada is True else False)
        ),
        "observacion": (
            None
            if not no_gravado
            else (
                "El total cargado excede el impreso justo por el monto no gravado."
                if explicada is True
                else "Hay monto no gravado cargado pero el total cargado coincide con el del comprobante."
            )
        ),
    }
    marcar(
        "importe_total_facturado",
        {
            "valor_comprobante": extraccion.get("importe_total"),
            "valor_cargado": datos.get("importe_total_facturado"),
            "coincide": coincide_total,
            "diferencia": diferencia,
            "diferencia_explicada_por_monto_no_gravado": explicada,
            "observacion": None
            if coincide_total is True
            else "El total difiere y no lo explica el monto no gravado.",
        },
        True,
    )

    # 11. Categoría de gasto vs rubro sugerido.
    leido_cat = _norm(extraccion.get("categoria_gasto_sugerida"))
    cargado_cat = _norm(datos.get("categoria_gasto"))
    categoria_ok: Any = (
        (leido_cat == cargado_cat) if leido_cat and cargado_cat else "no_verificable"
    )
    campos["categoria_gasto"] = {
        "valor_cargado": datos.get("categoria_gasto"),
        "consistente_con_rubro_emisor": categoria_ok,
        "observacion": None
        if categoria_ok in (True, "no_verificable")
        else f"El rubro del emisor sugiere «{extraccion.get('categoria_gasto_sugerida')}».",
    }

    # 12. Notas: no se valida; se sugiere aclaración si hay discrepancias.
    campos["notas"] = {
        "valor_cargado": datos.get("notas"),
        "aclaracion_sugerida": bool(criticas) and not (datos.get("notas") or "").strip(),
        "observacion": None,
    }

    # 13. Comensales/personas.
    requiere_comensales = cargado_cat in CATEGORIAS_CON_COMENSALES
    comensales = a_numero(datos.get("cantidad_comensales_personas"))
    campos["cantidad_comensales_personas"] = {
        "requerido_por_categoria": requiere_comensales,
        "valor_cargado": datos.get("cantidad_comensales_personas"),
        "observacion": (
            "La categoría lo requiere y el campo está vacío o en cero."
            if requiere_comensales and not comensales
            else None
        ),
    }

    # 14. Litros (sí suele estar impreso en el ticket).
    requiere_litros = cargado_cat in CATEGORIAS_CON_LITROS
    mismo_litros = _mismo_monto(
        extraccion.get("cantidad_litros"), datos.get("cantidad_litros")
    )
    campos["cantidad_litros"] = {
        "requerido_por_categoria": requiere_litros,
        "valor_comprobante": extraccion.get("cantidad_litros"),
        "valor_cargado": datos.get("cantidad_litros"),
        "coincide": (mismo_litros if mismo_litros is not None else "no_verificable"),
        "observacion": None
        if mismo_litros in (True, None)
        else "Los litros leídos no coinciden con los cargados.",
    }

    # 15. Centro de costo: informativo.
    campos["centro_de_costo"] = {
        "valor_cargado": datos.get("centro_de_costo"),
        "nota": "Informativo, no controlado por aprobadores",
    }

    no_legibles = list(extraccion.get("campos_no_legibles") or [])
    if _norm(extraccion.get("legibilidad")) == "MALA":
        estado = "INCOMPLETO"
    elif criticas:
        estado = "REVISAR"
    else:
        estado = "OK"

    resumen = {
        "OK": "Todos los campos verificables coinciden con el comprobante.",
        "REVISAR": "Hay discrepancias relevantes: " + ", ".join(criticas) + ".",
        "INCOMPLETO": "La imagen no permite verificar los campos clave.",
    }[estado]

    return {
        "estado_global": estado,
        "resumen": resumen,
        "campos": campos,
        "discrepancias_criticas": criticas,
        "campos_no_legibles": no_legibles,
    }


# --------------------------------------------------------------------------- #
# Datos cargados por el empleado
# --------------------------------------------------------------------------- #

#: Claves que delatan que un JSON es UN documento y no un mapa de documentos.
_CLAVES_DOCUMENTO = frozenset(
    {
        "tipo_comprobante",
        "importe_total_facturado",
        "razon_social_emisor",
        "nro_factura",
    }
)


def cargar_datos(ruta: Path) -> dict[str, dict]:
    """Carga los datos de Mendel como ``{clave: documento}``.

    Acepta un archivo JSON (un único documento, o un mapa ``clave → documento``)
    o una carpeta con un ``<clave>.json`` por documento. La ``clave`` se usa para
    aparear con cada imagen (nombre de archivo o de la carpeta que la contiene).
    """
    if ruta.is_dir():
        datos: dict[str, dict] = {}
        for archivo in sorted(ruta.glob("*.json")):
            try:
                contenido = json.loads(archivo.read_text(encoding="utf-8"))
            except json.JSONDecodeError as exc:
                raise ValueError(f"{archivo} no es JSON válido: {exc}") from exc
            if isinstance(contenido, dict):
                datos[archivo.stem] = contenido
        return datos

    if not ruta.is_file():
        raise FileNotFoundError(f"no se encontró --datos: {ruta}")
    contenido = json.loads(ruta.read_text(encoding="utf-8"))

    if isinstance(contenido, list):
        datos = {}
        for i, item in enumerate(contenido):
            if not isinstance(item, dict):
                raise ValueError(f"el ítem {i} de {ruta} no es un objeto")
            clave = item.get("documento_id") or item.get("id") or item.get("archivo") or str(i)
            datos[str(clave)] = item
        return datos

    if isinstance(contenido, dict):
        if _CLAVES_DOCUMENTO & set(contenido):
            return {"__unico__": contenido}
        return {str(k): v for k, v in contenido.items() if isinstance(v, dict)}

    raise ValueError(f"forma de --datos no soportada en {ruta}")


def buscar_datos(img: Path, datos: dict[str, dict]) -> tuple[str | None, dict | None]:
    """Busca los datos que corresponden a una imagen, por varios candidatos.

    Candidatos, en orden: nombre del archivo, nombre de la carpeta contenedora
    (el hash del lote), y la ruta relativa sin extensión. Si sólo hay un
    documento cargado (``__unico__``), se usa ese.
    """
    if "__unico__" in datos and len(datos) == 1:
        return "__unico__", datos["__unico__"]
    candidatos = [img.stem, img.parent.name, str(img.with_suffix(""))]
    for candidato in candidatos:
        if candidato in datos:
            return candidato, datos[candidato]
    return None, None


# --------------------------------------------------------------------------- #
# Procesamiento por documento
# --------------------------------------------------------------------------- #


@dataclass
class Opciones:
    """Opciones efectivas de la corrida."""

    modo: str
    modelo: str
    detalle: str
    temperatura: float | None
    max_tokens: int | None
    esfuerzo: str | None
    salida: Path
    forzar: bool
    workers: int
    dry_run: bool
    incluir_ejemplo: bool
    precio_entrada: float | None = None
    precio_salida: float | None = None
    #: Precios por modelo (``{"gpt-4o": (entrada, salida), "*": (…)}``).
    precios: dict[str, tuple[float | None, float | None]] = field(default_factory=dict)
    #: Zona horaria para agrupar el gasto por día (``None`` = local).
    tz: timezone | None = None
    tz_etiqueta: str = "local"
    #: Delimitador y separador decimal del CSV de gastos (Excel es-AR usa «;» y «,»).
    csv_delim: str = ","
    csv_decimal: str = "."


def procesar(
    img: Path,
    raiz: Path,
    cliente: Any,
    sistema: str,
    user_template: str,
    datos: dict[str, dict],
    extracciones: dict[str, dict],
    opciones: Opciones,
) -> dict[str, Any]:
    """Procesa una imagen y devuelve el registro (con procedencia) para guardar."""
    try:
        relativo = img.resolve().relative_to(raiz.resolve())
    except (ValueError, OSError):
        relativo = Path(img.name)

    registro: dict[str, Any] = {
        "origen": str(img),
        "archivo_relativo": str(relativo),
        "procesado_utc": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "modelo": opciones.modelo,
        "modo": opciones.modo,
        "version_prompt": VERSION_PROMPT,
        "prompt_hash": hash_prompt(sistema, user_template),
        "fuente": "api",
    }

    clave_datos, datos_doc = (None, None)
    if opciones.modo in {"validar", "diff"}:
        clave_datos, datos_doc = buscar_datos(img, datos)
        registro["datos_clave"] = clave_datos
        if datos_doc is None:
            registro["error"] = (
                "no hay datos cargados para esta imagen "
                f"(claves probadas: {img.stem}, {img.parent.name})"
            )
            return registro

    # El modo diff reutiliza la extracción previa si existe: no vuelve a pagar.
    if opciones.modo == "diff":
        extraccion = extracciones.get(str(relativo))
        if extraccion is None:
            registro["error"] = (
                "modo diff sin extracción previa; corré primero --modo extraer "
                "(o usá --modo validar)"
            )
            return registro
        registro["fuente"] = "diff_local"
        registro["resultado"] = diff_deterministico(extraccion, datos_doc)
        return registro

    # En dry-run no hace falta el base64 ni gastar memoria codificando.
    if opciones.dry_run:
        try:
            registro["imagen"] = info_imagen(img, opciones.detalle)
        except OSError as exc:
            registro["error"] = f"no se pudo leer la imagen: {exc}"
            return registro
        registro["resultado"] = {
            "_dry_run": True,
            "esquema": "validacion_comprobante" if opciones.modo == "validar" else "extraccion_comprobante",
        }
        return registro

    try:
        data_url, info = codificar_imagen(img, opciones.detalle)
    except OSError as exc:
        registro["error"] = f"no se pudo leer la imagen: {exc}"
        return registro
    registro["imagen"] = info

    if info["bytes"] > 20 * 1024 * 1024:
        registro["error"] = (
            f"la imagen pesa {info['bytes'] / 1e6:.1f} MB y la API acepta hasta "
            "20 MB por imagen: reducíla con reducir_tokens.py"
        )
        return registro

    usuario = construir_mensaje_usuario(
        user_template, datos_doc, incluir_ejemplo=opciones.incluir_ejemplo
    )
    # Al extraer sin datos cargados, se agregan las reglas de transcripción:
    # el template del .md está pensado para comparar, no para transcribir.
    if opciones.modo == "extraer":
        usuario = prompt_de_extraccion(usuario)
    esquema = (
        esquema_validacion() if opciones.modo == "validar" else esquema_extraccion()
    )
    nombre = "validacion_comprobante" if opciones.modo == "validar" else "extraccion_comprobante"

    respuesta = llamar_api(
        cliente,
        modelo=opciones.modelo,
        sistema=sistema,
        usuario=usuario,
        data_url=data_url,
        esquema=esquema,
        nombre_esquema=nombre,
        temperatura=opciones.temperatura,
        detalle=opciones.detalle,
        max_tokens=opciones.max_tokens,
        esfuerzo=opciones.esfuerzo,
    )
    registro["uso"] = respuesta.uso
    if respuesta.sin_temperatura:
        registro["nota_temperatura"] = (
            "el modelo rechazó temperature; se reintentó sin ese parámetro"
        )
    if not respuesta.ok:
        registro["error"] = respuesta.error
        return registro

    # Costo con los precios de ESTA corrida, persistido junto al uso: así el
    # reporte de gastos no tiene que adivinar precios históricos después.
    p_entrada, p_salida = precio_de(opciones.modelo, opciones.precios)
    registro["precios_usd_1m"] = {
        "entrada": p_entrada,
        "salida": p_salida,
        "origen": "cli" if opciones.precios or opciones.precio_entrada else "referencia",
    }
    registro["costo_usd"] = costo_de_tokens(
        respuesta.uso.get("prompt_tokens") or 0,
        respuesta.uso.get("completion_tokens") or 0,
        p_entrada,
        p_salida,
    )

    registro["resultado"] = respuesta.datos
    if opciones.modo == "extraer":
        registro["extraccion"] = respuesta.datos
        # La aritmética se recalcula en Python: el `cierra_aritmetica` que
        # devuelve el modelo no es confiable (dijo `true` con diferencias de
        # 10,00 y 569,00 en comprobantes reales).
        aritmetica = verificar_aritmetica(respuesta.datos)
        registro["aritmetica"] = aritmetica
        if aritmetica["calculable"]:
            respuesta.datos["cierra_aritmetica"] = aritmetica["cierra"]
            if not aritmetica["cierra"]:
                aviso = (
                    f"Los importes leídos suman {aritmetica['suma']:,.2f} y el "
                    f"total impreso es {aritmetica['total']:,.2f} "
                    f"(diferencia {aritmetica['diferencia']:,.2f})."
                )
                if aritmetica["faltantes"]:
                    aviso += (
                        " Puede faltar alguno de: "
                        + ", ".join(aritmetica["faltantes"])
                        + "."
                    )
                registro["aritmetica"]["aviso"] = aviso
    return registro


def _expandir(rutas: Sequence[Path], extensiones: frozenset[str]) -> list[Path]:
    """Expande archivos/carpetas a la lista de imágenes, en orden determinista."""
    encontrados: list[Path] = []
    for ruta in rutas:
        if ruta.is_dir():
            encontrados.extend(
                q
                for q in sorted(ruta.rglob("*"))
                if q.is_file() and q.suffix.lower() in extensiones
            )
        elif ruta.is_file():
            if ruta.suffix.lower() in extensiones:
                encontrados.append(ruta)
            else:
                print(f"⚠  Se ignora (no es imagen): {ruta}", file=sys.stderr)
        else:
            print(f"⚠  Se ignora (no existe): {ruta}", file=sys.stderr)
    return sorted(set(encontrados))


def _raiz_comun(rutas: Sequence[Path]) -> Path:
    """Raíz desde la cual se espeja el árbol en la carpeta de salida."""
    bases = [r if r.is_dir() else r.parent for r in rutas if r.exists()]
    if not bases:
        return Path.cwd()
    if len(bases) == 1:
        return bases[0]
    try:
        return Path(os.path.commonpath([str(b.resolve()) for b in bases]))
    except ValueError:
        return Path.cwd()


def ya_procesado(destino: Path) -> bool:
    """True si el destino ya tiene un resultado **válido** (no un error).

    ⚠️ Un registro con ``error`` NO cuenta como hecho: si se lo tomara como tal,
    una re-corrida después de arreglar la causa (clave, red, imagen) saltearía ese
    documento y el resumen reportaría éxito. Misma lección que los checkpoints de
    T-603: *un error no es un paso completado*. Un archivo ilegible también se
    reprocesa.
    """
    if not destino.exists():
        return False
    try:
        registro = json.loads(destino.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return False
    return not registro.get("error")


def leer_extracciones_previas(salida: Path) -> dict[str, dict]:
    """Lee las extracciones ya guardadas para que el modo ``diff`` las reutilice."""
    previas: dict[str, dict] = {}
    if not salida.is_dir():
        return previas
    for archivo in salida.rglob("*.extraccion.json"):
        try:
            registro = json.loads(archivo.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            continue
        extraccion = registro.get("extraccion") or registro.get("resultado")
        relativo = registro.get("archivo_relativo")
        if extraccion and relativo:
            previas[relativo] = extraccion
    return previas


# --------------------------------------------------------------------------- #
# Reporte
# --------------------------------------------------------------------------- #


def resumen(registros: Sequence[dict], opciones: Opciones) -> dict[str, Any]:
    """Resumen agregado de **una corrida**: estados, errores, tokens y costo.

    Es el reporte de la corrida (qué se procesó ahora). Para el reporte de
    **gastos** acumulado y con fechas, ver :func:`ledger_de_registros`.
    """
    estados: dict[str, int] = {}
    errores: list[dict[str, str]] = []
    prompt_tokens = completion_tokens = 0
    tokens_imagen_estimados = 0

    for r in registros:
        if error := r.get("error"):
            errores.append({"origen": r["origen"], "error": error})
            continue
        resultado = r.get("resultado") or {}
        if estado := resultado.get("estado_global"):
            estados[estado] = estados.get(estado, 0) + 1
        uso = r.get("uso") or {}
        prompt_tokens += uso.get("prompt_tokens") or 0
        completion_tokens += uso.get("completion_tokens") or 0
        tokens_imagen_estimados += (r.get("imagen") or {}).get("tokens_estimados") or 0

    entrada, salida = precio_de(opciones.modelo, opciones.precios)
    costo = costo_de_tokens(prompt_tokens, completion_tokens, entrada, salida)

    # Señal de calidad de la lectura: comprobantes cuyos importes NO suman el
    # total. Se calcula en Python (no se le cree al modelo), así que es la
    # primera cosa a mirar cuando una extracción parece dudosa.
    aritmetica_ok = aritmetica_mal = aritmetica_nd = 0
    for r in registros:
        if r.get("error"):
            continue
        a = r.get("aritmetica")
        if not a or not a.get("calculable"):
            aritmetica_nd += 1
        elif a["cierra"]:
            aritmetica_ok += 1
        else:
            aritmetica_mal += 1

    return {
        "archivos": len(registros),
        "ok": len(registros) - len(errores),
        "errores": len(errores),
        "estados_globales": estados,
        "aritmetica": {
            "cierra": aritmetica_ok,
            "no_cierra": aritmetica_mal,
            "no_calculable": aritmetica_nd,
        },
        "tokens": {
            "prompt": prompt_tokens,
            "completion": completion_tokens,
            "imagen_estimados": tokens_imagen_estimados,
            "costo_usd_estimado": costo,
        },
        "opciones": {
            "modo": opciones.modo,
            "modelo": opciones.modelo,
            "detalle_imagen": opciones.detalle,
            "temperatura": opciones.temperatura,
            "salida": str(opciones.salida),
            "prompt_con_ejemplo_de_salida": opciones.incluir_ejemplo,
            "dry_run": opciones.dry_run,
        },
        "detalle_errores": errores[:20],
    }


def _imprimir_resumen(rep: dict) -> None:
    print("\n=== Resumen ===")
    print(f"archivos          : {rep['archivos']}")
    print(f"  ok              : {rep['ok']}")
    print(f"  errores         : {rep['errores']}")
    if rep["estados_globales"]:
        for estado, n in sorted(rep["estados_globales"].items()):
            print(f"    {estado:<13} : {n}")
    arit = rep.get("aritmetica") or {}
    if arit.get("no_cierra"):
        print(
            f"  ⚠ aritmética     : {arit['no_cierra']} comprobante(s) cuyos importes "
            "no suman el total (revisar la lectura)",
            file=sys.stderr,
        )
    elif arit.get("cierra"):
        print(f"  aritmética       : {arit['cierra']} cierran el total")
    t = rep["tokens"]
    if t["prompt"] or t["completion"]:
        print(f"tokens API        : prompt {t['prompt']:,} | completion {t['completion']:,}")
    if t["imagen_estimados"]:
        print(f"tokens img (est.) : {t['imagen_estimados']:,}")
    if t["costo_usd_estimado"] is not None:
        print(f"costo (est.)      : US$ {t['costo_usd_estimado']}")
    print(f"modo / modelo     : {rep['opciones']['modo']} / {rep['opciones']['modelo']}")
    if rep["opciones"]["dry_run"]:
        print("modo              : --dry-run (no se llamó a la API ni se escribió nada)")
    for error in rep["detalle_errores"]:
        print(f"  ✗ {error['origen']}: {error['error']}", file=sys.stderr)
    if rep["errores"] > len(rep["detalle_errores"]):
        print(f"  … y {rep['errores'] - len(rep['detalle_errores'])} errores más", file=sys.stderr)


#: Campos de importe que deben sumar el total (en el orden en que se suman).
COMPONENTES_DEL_TOTAL = (
    "subtotal",
    "no_gravado",
    "exento",
    "iva",
    "impuestos_internos",
    "percepciones_iibb",
    "otros_impuestos",
)


def verificar_aritmetica(extraccion: dict) -> dict[str, Any]:
    """Comprueba **en Python** si los importes leídos suman el total.

    No se le cree al modelo: su ``cierra_aritmetica`` sale ``true`` incluso
    cuando la suma no da (se comprobó con un comprobante real: declaró ``true``
    con 10,00 de diferencia). Es la misma regla que el prompt aplica al diff de
    campos — lo que se puede calcular, se calcula en código y se audita.

    Una diferencia acá es una **señal valiosa**: o falta un importe (p. ej. una
    línea que el modelo no transcribió) o el modelo leyó mal un dígito.

    Devuelve ``{"calculable", "suma", "total", "diferencia", "cierra", "faltantes"}``;
    ``calculable`` es ``False`` si no hay ningún importe o no hay total.
    """
    componentes = {k: a_numero(extraccion.get(k)) for k in COMPONENTES_DEL_TOTAL}
    presentes = {k: v for k, v in componentes.items() if v is not None}
    total = a_numero(extraccion.get("importe_total"))
    if not presentes or total is None:
        return {
            "calculable": False,
            "suma": sum(presentes.values()) if presentes else None,
            "total": total,
            "diferencia": None,
            "cierra": None,
            "faltantes": [],
        }

    suma = sum(presentes.values())
    diferencia = round(total - suma, 2)
    cierra = abs(diferencia) < 0.05
    # Cuando no cierra, el candidato más probable es un campo de importe que
    # quedó en null: el modelo no lo transcribió. Se listan todos los ausentes
    # (sin adivinar cuál), para que quien revise mire ahí primero.
    faltantes = (
        [k for k in COMPONENTES_DEL_TOTAL if componentes.get(k) is None]
        if not cierra
        else []
    )
    return {
        "calculable": True,
        "suma": round(suma, 2),
        "total": total,
        "diferencia": diferencia,
        "cierra": cierra,
        "faltantes": faltantes,
    }


# --------------------------------------------------------------------------- #
# Precios de referencia (editables / override por CLI)
# --------------------------------------------------------------------------- #
#
# ⚠️ Los precios CAMBIAN y dependen del modelo y de la cuenta. Estos valores son
# una **referencia** editable (USD por 1M de tokens, entrada/salida); el número
# que manda es el del proveedor. Cualquier precio pasado por CLI
# (``--precio-entrada`` / ``--precio-salida``) pisa esta tabla, y ``--precios``
# permite precios por modelo. Si no hay precio para un modelo, el costo queda
# en ``null`` en vez de inventarse (y el reporte lo declara).

#: Precios de referencia: ``modelo -> (USD/1M entrada, USD/1M salida)``.
PRECIOS_REFERENCIA: dict[str, tuple[float, float]] = {
    "gpt-4o": (2.50, 10.00),
    "gpt-4o-mini": (0.15, 0.60),
    "gpt-4.1": (2.00, 8.00),
    "gpt-4.1-mini": (0.40, 1.60),
    "gpt-4.1-nano": (0.10, 0.40),
}


def _clave_modelo(modelo: str, tabla: dict[str, Any]) -> str | None:
    """Busca el modelo en la tabla, tolerando el sufijo de fecha.

    ``gpt-4o-2026-08`` matchea la entrada ``gpt-4o``; el match exacto gana.
    """
    if modelo in tabla:
        return modelo
    for clave in sorted(tabla, key=len, reverse=True):
        if clave != "*" and modelo.startswith(clave):
            return clave
    return "*" if "*" in tabla else None


def parsear_precios(texto: str) -> dict[str, tuple[float | None, float | None]]:
    """Parsea ``--precios``: ``modelo=entrada/salida`` separados por coma.

    Ejemplo: ``"gpt-4o=2.5/10, gpt-4o-mini=0.15/0.6, *=1/3"``.
    ``*`` es el comodín para los modelos no listados. Cada precio puede ser
    ``-`` para dejarlo sin definir (el costo de esa parte queda en ``null``).
    """
    tabla: dict[str, tuple[float | None, float | None]] = {}
    for trozo in texto.split(","):
        trozo = trozo.strip()
        if not trozo:
            continue
        if "=" not in trozo or "/" not in trozo:
            raise ValueError(
                f"--precios: se esperaba «modelo=entrada/salida», llegó {trozo!r}"
            )
        modelo, _, valores = trozo.partition("=")
        entrada_txt, _, salida_txt = valores.partition("/")

        def _num(valor: str) -> float | None:
            valor = valor.strip()
            if valor in {"-", "", "none", "null"}:
                return None
            numero = float(valor)
            if numero < 0:
                raise ValueError(f"--precios: precio negativo en {trozo!r}")
            return numero

        tabla[modelo.strip()] = (_num(entrada_txt), _num(salida_txt))
    if not tabla:
        raise ValueError("--precios quedó vacío")
    return tabla


def precio_de(
    modelo: str,
    precios: dict[str, tuple[float | None, float | None]],
    *,
    por_defecto: bool = True,
) -> tuple[float | None, float | None]:
    """Precios (entrada, salida) del modelo, o ``(None, None)`` si no se sabe."""
    clave = _clave_modelo(modelo, precios)
    if clave is not None:
        return precios[clave]
    if por_defecto:
        clave = _clave_modelo(modelo, PRECIOS_REFERENCIA)
        if clave is not None:
            return PRECIOS_REFERENCIA[clave]
    return None, None


def costo_de_tokens(
    entrada_tokens: int,
    salida_tokens: int,
    precio_entrada: float | None,
    precio_salida: float | None,
) -> float | None:
    """Costo en USD, o ``None`` si no hay ningún precio con el que calcularlo.

    Si sólo se conoce uno de los dos precios, se cobra el que se sabe y se
    **declara** que el otro no (el número es un piso, no el total).
    """
    if precio_entrada is None and precio_salida is None:
        return None
    total = entrada_tokens * (precio_entrada or 0) + salida_tokens * (precio_salida or 0)
    return round(total / 1e6, 6)


# --------------------------------------------------------------------------- #
# Reporte de gastos (registro contable acumulado)
# --------------------------------------------------------------------------- #


def _tz_desde(texto: str) -> tuple[timezone | None, str]:
    """Interpreta ``--tz``: ``local`` (default), ``UTC`` o un offset como ``-03:00``."""
    valor = (texto or "local").strip()
    if valor.lower() in {"local", ""}:
        return None, "local"
    if valor.upper() == "UTC":
        return timezone.utc, "UTC"
    m = re.fullmatch(r"([+-])(\d{1,2}):?(\d{2})?", valor)
    if not m:
        raise ValueError(
            f"--tz inválida: {texto!r}. Usá «local», «UTC» o un offset como «-03:00»."
        )
    signo = -1 if m.group(1) == "-" else 1
    horas, minutos = int(m.group(2)), int(m.group(3) or 0)
    if horas > 23 or minutos > 59:
        raise ValueError(f"--tz inválida: {texto!r}")
    delta = signo * timedelta(hours=horas, minutes=minutos)
    return timezone(delta), f"UTC{m.group(1)}{horas:02d}:{minutos:02d}"


def _a_zona(momento: datetime, tz: timezone | None) -> datetime:
    """Convierte a la zona pedida (o a la local si ``tz`` es ``None``)."""
    return momento.astimezone(tz) if tz is not None else momento.astimezone()


def _parsear_momento(texto: str | None) -> datetime | None:
    """Parsea el ``procesado_utc`` de un registro (tolerante a formatos viejos)."""
    if not texto:
        return None
    limpio = str(texto).strip().replace("Z", "+00:00")
    try:
        momento = datetime.fromisoformat(limpio)
    except ValueError:
        return None
    return momento if momento.tzinfo else momento.replace(tzinfo=timezone.utc)


def _es_gasto(registro: dict) -> bool:
    """True si el registro representa **una llamada paga** a la API.

    Quedan afuera: los ``--dry-run``, los fallos y el modo ``diff`` (no llama a
    la API). Para que el total no mienta, un registro sin ``usage`` tampoco
    cuenta: no se puede afirmar que gastó si el proveedor no lo reportó.
    """
    if registro.get("dry_run") or registro.get("fuente") == "diff_local":
        return False
    if registro.get("error"):
        return False
    uso = registro.get("uso") or {}
    return (uso.get("prompt_tokens") or uso.get("completion_tokens")) is not None


def apunte_de_registro(
    registro: dict,
    opciones: Opciones,
    *,
    ahora: datetime | None = None,
) -> dict[str, Any] | None:
    """Convierte un registro de salida en un **apunte de gasto**, o ``None``.

    Los registros escritos por versiones anteriores no traen el costo ni la
    ``fuente``, así que se **recalcula** acá desde ``uso`` + la tabla de precios
    vigente (y se declara que el precio es el actual, no el histórico).
    """
    if not _es_gasto(registro):
        return None
    uso = registro.get("uso") or {}
    entrada = uso.get("prompt_tokens") or 0
    salida = uso.get("completion_tokens") or 0
    modelo = registro.get("modelo") or "desconocido"
    p_entrada, p_salida = precio_de(modelo, opciones.precios)
    # Si el registro ya trae el costo (y no hay precios nuevos), se respeta.
    costo = registro.get("costo_usd")
    if costo is None:
        costo = costo_de_tokens(entrada, salida, p_entrada, p_salida)

    momento = _parsear_momento(registro.get("procesado_utc")) or ahora or datetime.now(
        timezone.utc
    )
    local = _a_zona(momento, opciones.tz)
    return {
        "fecha_hora": local.isoformat(timespec="seconds"),
        "fecha": local.date().isoformat(),
        "hora": local.strftime("%H:%M:%S"),
        "documento": registro.get("archivo_relativo") or registro.get("origen"),
        "origen": registro.get("origen"),
        "modo": registro.get("modo"),
        "modelo": modelo,
        "detalle_imagen": (registro.get("imagen") or {}).get("detalle"),
        "tokens_prompt": entrada,
        "tokens_completion": salida,
        "tokens_total": (entrada + salida),
        "tokens_imagen_estimados": (registro.get("imagen") or {}).get(
            "tokens_estimados"
        ),
        "precio_entrada_usd_1m": p_entrada,
        "precio_salida_usd_1m": p_salida,
        "costo_usd": costo,
        "costo_confiable": p_entrada is not None or p_salida is not None,
        "fuente_costo": (
            "registro" if registro.get("costo_usd") is not None else "recalculado"
        ),
        "version_prompt": registro.get("version_prompt"),
        "prompt_hash": registro.get("prompt_hash"),
    }


def _clave_apunte(apunte: dict) -> tuple:
    """Clave de deduplicación: un documento+modo+modelo, en una fecha/hora."""
    return (
        apunte.get("documento"),
        apunte.get("modo"),
        apunte.get("modelo"),
        apunte.get("fecha_hora"),
    )


def leer_apuntes(salida: Path, opciones: Opciones) -> tuple[list[dict], int]:
    """Lee **todas** las salidas de una carpeta y las convierte en apuntes.

    Recorre ``*.json`` (los tres sufijos: validación, extracción y diff) porque
    el gasto es del **histórico** de la carpeta, no de la última corrida.
    Devuelve ``(apuntes, descartados)``; los descartados son registros que no
    representan una llamada paga (dry-run, fallos, diff) o no se pudieron leer.
    """
    apuntes: dict[tuple, dict] = {}
    descartados = 0
    if not salida.is_dir():
        return [], 0
    for archivo in sorted(salida.rglob("*.json")):
        # Los reportes que el propio script escribe no son registros de gasto.
        if archivo.name in {"reporte.json", "gastos.json"}:
            continue
        try:
            registro = json.loads(archivo.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            descartados += 1
            continue
        if not isinstance(registro, dict):
            descartados += 1
            continue
        apunte = apunte_de_registro(registro, opciones)
        if apunte is None:
            descartados += 1
            continue
        apuntes.setdefault(_clave_apunte(apunte), apunte)
    return sorted(apuntes.values(), key=lambda a: a["fecha_hora"]), descartados


def totalizar(apuntes: Sequence[dict]) -> dict[str, Any]:
    """Suma el gasto: total y por día, modelo y modo.

    Separa los totales **confiables** (con al menos un precio conocido) de los
    que no lo son, en vez de mezclarlos en un número que parecería exacto.
    """
    # Total, y agrupaciones por día, modelo y modo. Se distingue «sin precio»
    # (no hay ninguno para el modelo) de «precio incompleto» (sólo se conoce el
    # de entrada o el de salida): son cosas distintas y el aviso debe decir cuál.
    por_dia: dict[str, dict[str, Any]] = {}
    por_modelo: dict[str, dict[str, Any]] = {}
    por_modo: dict[str, dict[str, Any]] = {}
    total = {"apuntes": 0, "tokens_prompt": 0, "tokens_completion": 0, "costo_usd": 0.0}
    sin_precio = 0
    precio_incompleto = 0

    def _acumular(destino: dict[str, dict[str, Any]], clave: str) -> dict[str, Any]:
        return destino.setdefault(
            clave,
            {"apuntes": 0, "tokens_total": 0, "costo_usd": 0.0, "costo_confiable": True},
        )

    for a in apuntes:
        total["apuntes"] += 1
        total["tokens_prompt"] += a["tokens_prompt"]
        total["tokens_completion"] += a["tokens_completion"]
        if a["costo_usd"] is None:
            sin_precio += 1
        else:
            total["costo_usd"] += a["costo_usd"]
            if not a["costo_confiable"]:
                precio_incompleto += 1

        for destino, clave in (
            (por_dia, a["fecha"]),
            (por_modelo, a["modelo"]),
            (por_modo, a["modo"] or "desconocido"),
        ):
            fila = _acumular(destino, clave)
            fila["apuntes"] += 1
            fila["tokens_total"] += a["tokens_total"]
            if a["costo_usd"] is not None:
                fila["costo_usd"] = round(fila["costo_usd"] + a["costo_usd"], 6)
            if not a["costo_confiable"]:
                fila["costo_confiable"] = False

    total["costo_usd"] = round(total["costo_usd"], 6)
    return {
        "total": total,
        "por_dia": {k: por_dia[k] for k in sorted(por_dia)},
        "por_modelo": {k: por_modelo[k] for k in sorted(por_modelo)},
        "por_modo": {k: por_modo[k] for k in sorted(por_modo)},
        "sin_precio": sin_precio,
        "precio_incompleto": precio_incompleto,
        "costo_parcial": (sin_precio + precio_incompleto) > 0,
    }


def _formato_coste(valor: float | None, *, decimal: str = ".") -> str:
    """US$ con 6 decimales (una extracción suele costar centavos)."""
    if valor is None:
        return "—"
    texto = f"{valor:.6f}"
    return texto.replace(".", decimal) if decimal != "." else texto


def imprimir_reporte_gastos(rep: dict[str, Any], apuntes: Sequence[dict]) -> None:
    """Reporte de gastos en stdout: totales, por día, por modelo y por modo."""
    print("\n=== Reporte de gastos (API) ===")
    if not apuntes:
        print("Sin extracciones facturables registradas en la carpeta.")
        return
    t = rep["total"]
    desde = apuntes[0]["fecha"]
    hasta = apuntes[-1]["fecha"]
    print(f"período           : {desde} → {hasta} ({rep['opciones']['tz']})")
    print(f"extracciones      : {t['apuntes']}")
    print(f"tokens            : prompt {t['tokens_prompt']:,} | completion {t['tokens_completion']:,}")
    print(f"costo total       : US$ {_formato_coste(t['costo_usd'])}")
    if rep["sin_precio"]:
        print(
            f"  ⚠ SIN PRECIO    : {rep['sin_precio']} extracción(es) no tienen precio "
            f"para su modelo y suman US$ 0. El costo real es MAYOR. Pasá --precios "
            "o --precio-entrada/--precio-salida.",
            file=sys.stderr,
        )
    if rep["precio_incompleto"]:
        print(
            f"  ⚠ PARCIAL       : {rep['precio_incompleto']} extracción(es) sólo "
            "tienen precio de entrada o de salida; el total es un piso.",
            file=sys.stderr,
        )

    print("\n-- por día --")
    for dia, fila in rep["por_dia"].items():
        marca = "" if fila["costo_confiable"] else "  (precio parcial)"
        print(
            f"  {dia}  {fila['apuntes']:>4} ext.  "
            f"{fila['tokens_total']:>9,} tokens  "
            f"US$ {_formato_coste(fila['costo_usd'])}{marca}"
        )

    print("\n-- por modelo --")
    for modelo, fila in rep["por_modelo"].items():
        marca = "" if fila["costo_confiable"] else "  (precio parcial)"
        print(
            f"  {modelo:<18} {fila['apuntes']:>4} ext.  "
            f"US$ {_formato_coste(fila['costo_usd'])}{marca}"
        )

    print("\n-- por modo --")
    for modo, fila in rep["por_modo"].items():
        print(f"  {modo:<10} {fila['apuntes']:>4} ext.  US$ {_formato_coste(fila['costo_usd'])}")


def escribir_csv_gastos(
    ruta: Path, apuntes: Sequence[dict], *, delim: str = ",", decimal: str = "."
) -> None:
    """CSV del gasto **por extracción**, listo para abrir en Excel/Sheets.

    Se usa ``utf-8-sig`` (BOM) para que Excel respete los acentos, y se permite
    delimitador/decimal configurables porque Excel en es-AR espera ``;`` y ``,``.
    """
    campos = [
        "fecha",
        "hora",
        "fecha_hora",
        "documento",
        "modo",
        "modelo",
        "detalle_imagen",
        "tokens_prompt",
        "tokens_completion",
        "tokens_total",
        "precio_entrada_usd_1m",
        "precio_salida_usd_1m",
        "costo_usd",
        "costo_confiable",
        "version_prompt",
        "prompt_hash",
        "origen",
    ]
    ruta.parent.mkdir(parents=True, exist_ok=True)
    with ruta.open("w", encoding="utf-8-sig", newline="") as fh:
        escritor = csv.DictWriter(fh, fieldnames=campos, delimiter=delim, extrasaction="ignore")
        escritor.writeheader()
        for a in apuntes:
            fila = dict(a)
            if decimal != ".":
                for clave in ("costo_usd",):
                    if fila.get(clave) is not None:
                        fila[clave] = f"{fila[clave]:.6f}".replace(".", decimal)
                for clave in ("precio_entrada_usd_1m", "precio_salida_usd_1m"):
                    if fila.get(clave) is not None:
                        fila[clave] = f"{fila[clave]:.4f}".replace(".", decimal)
            escritor.writerow(fila)


# --------------------------------------------------------------------------- #
# CLI
# --------------------------------------------------------------------------- #


def construir_parser() -> argparse.ArgumentParser:
    """Parser de la CLI (``argparse`` de la stdlib, como el resto del repo)."""
    parser = argparse.ArgumentParser(
        prog="validar_comprobantes_openai",
        description=(
            "Valida/extrae campos de comprobantes con la API de OpenAI (visión), "
            "según el prompt de validación de comprobantes de Mendel."
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=(
            "Ejemplos:\n"
            "  python scripts/validar_comprobantes_openai.py ../procesados \\\n"
            "      --modo extraer --limite 5 --detalle-log\n"
            "  python scripts/validar_comprobantes_openai.py ../procesados \\\n"
            "      --datos datos_mendel.json --workers 4 -o validaciones\n"
            "  python scripts/validar_comprobantes_openai.py ../procesados \\\n"
            "      --modo diff --datos datos_mendel.json\n"
            "  python scripts/validar_comprobantes_openai.py \\\n"
            "      --reporte-gastos gastos.json --csv-gastos gastos.csv\n"
            "  python scripts/validar_comprobantes_openai.py ../procesados \\\n"
            "      --dry-run --limite 3\n"
            "\n"
            "El reporte de gastos se arma del histórico de la carpeta de salida\n"
            "(--salida), no sólo de la última corrida: totales por día, modelo y\n"
            "modo, más una fila por extracción en el CSV.\n"
        ),
    )
    parser.add_argument(
        "rutas",
        nargs="*",
        help=(
            "Imágenes y/o carpetas (se recorren recursivo). Se pueden omitir si "
            "sólo se quiere emitir el reporte de gastos de la carpeta de salida."
        ),
    )
    parser.add_argument(
        "--prompt",
        default=str(Path(__file__).with_name("prompt_validacion_comprobantes_mendel.md")),
        help="Archivo .md con el prompt (default: el de scripts/, junto a este script).",
    )
    parser.add_argument(
        "--modo",
        choices=("validar", "extraer", "diff"),
        default="validar",
        help=(
            "validar: imagen + datos → comparación (default). extraer: solo "
            "lectura de la imagen. diff: comparación determinística en Python."
        ),
    )
    parser.add_argument(
        "--datos",
        type=Path,
        help="JSON o carpeta con los datos cargados por el empleado (modos validar/diff).",
    )
    parser.add_argument(
        "-o",
        "--salida",
        default="validaciones",
        help="Carpeta de salida: un JSON por documento (default: %(default)s).",
    )
    parser.add_argument(
        "--modelo", default=MODELO_POR_DEFECTO, help="Modelo (default: %(default)s)."
    )
    parser.add_argument(
        "--detalle",
        choices=("low", "high", "auto"),
        default="high",
        help=(
            "Resolución con que la API mira la imagen (default: %(default)s). "
            "«low» gasta 85 tokens fijos."
        ),
    )
    parser.add_argument(
        "--temperatura",
        default=0.2,
        help=(
            "Temperatura (default: %(default)s, como recomienda el prompt). "
            "Usá «none» para omitirla (modelos de razonamiento)."
        ),
    )
    parser.add_argument(
        "--max-tokens", type=int, help="Tope de tokens de salida (opcional)."
    )
    parser.add_argument(
        "--esfuerzo",
        choices=("minimal", "low", "medium", "high"),
        help="reasoning_effort, solo para modelos de razonamiento (opcional).",
    )
    parser.add_argument(
        "--prompt-fiel",
        dest="incluir_ejemplo",
        action="store_true",
        help=(
            "Incluye el JSON de ejemplo de salida del template (por defecto se "
            "omite: el esquema estricto ya garantiza la forma)."
        ),
    )
    parser.add_argument(
        "--api-key", help="Clave de API (default: variable OPENAI_API_KEY)."
    )
    parser.add_argument(
        "--env",
        type=Path,
        default=Path(".env"),
        help="Archivo .env con OPENAI_API_KEY (default: ./.env).",
    )
    parser.add_argument(
        "--forzar",
        action="store_true",
        help="Reprocesa aunque ya exista la salida (sin esto, reanuda).",
    )
    parser.add_argument(
        "--workers", type=int, default=4, help="Llamadas concurrentes (default: %(default)s)."
    )
    parser.add_argument(
        "--limite", type=int, default=0, help="Procesa solo las primeras N (0 = todas)."
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help=(
            "No llama a la API ni escribe archivos: solo informa qué se "
            "enviaría (útil para verificar antes de gastar tokens)."
        ),
    )
    parser.add_argument("--detalle-log", action="store_true", help="Una línea por archivo.")
    parser.add_argument(
        "--reporte", metavar="ARCHIVO.json", help="Escribe el reporte agregado en JSON."
    )
    parser.add_argument(
        "--precio-entrada",
        type=float,
        metavar="USD_POR_1M",
        help=(
            "Precio por 1M de tokens de entrada (opcional; pisa la tabla de "
            "referencia). Si no se pasa ningún precio se usa la tabla interna."
        ),
    )
    parser.add_argument(
        "--precio-salida",
        type=float,
        metavar="USD_POR_1M",
        help="Precio por 1M de tokens de salida (opcional).",
    )
    parser.add_argument(
        "--precios",
        metavar="M=E/S[,M=E/S]",
        help=(
            "Precios por modelo, p. ej. «gpt-4o=2.5/10,*=1/3». «-» deja un "
            "precio sin definir. Pisa la tabla de referencia."
        ),
    )
    parser.add_argument(
        "--reporte-gastos",
        metavar="ARCHIVO.json",
        help=(
            "Escribe el REPORTE DE GASTOS acumulado de la carpeta de salida: "
            "totales por día, modelo y modo. Es independiente de --reporte."
        ),
    )
    parser.add_argument(
        "--csv-gastos",
        metavar="ARCHIVO.csv",
        help="Escribe el gasto por extracción en CSV (para Excel/Sheets).",
    )
    parser.add_argument(
        "--tz",
        default="local",
        help=(
            "Zona horaria para agrupar el gasto por día: «local» (default), "
            "«UTC» o un offset como «-03:00» (usá --tz=-03:00 o --tz -03:00)."
        ),
    )
    parser.add_argument(
        "--csv-delim",
        default=",",
        metavar="CARACTER",
        help="Delimitador del CSV (default «,»; Excel es-AR suele querer «;»).",
    )
    parser.add_argument(
        "--csv-decimal",
        default=".",
        choices=(",", "."),
        metavar="SEP",
        help=(
            "Separador decimal del CSV: «.» (default) o «,» (Excel es-AR). "
            "Se pasa así: --csv-decimal=,"
        ),
    )
    return parser


def _normalizar_argv(argv: Sequence[str]) -> list[str]:
    """Une las banderas que llevan un valor que empieza con ``-``.

    ``argparse`` lee ``--tz -03:00`` como si ``-03:00`` fuera otra bandera y falla
    con «expected one argument». Escribir ``--tz=-03:00`` funciona, pero es una
    trampa fácil de pisar, así que acá se une el par antes de parsear.
    """
    salida: list[str] = []
    i = 0
    while i < len(argv):
        actual = argv[i]
        siguiente = argv[i + 1] if i + 1 < len(argv) else None
        if (
            actual in {"--tz", "--reporte", "--csv-gastos", "--reporte-gastos"}
            and siguiente is not None
            and siguiente.startswith("-")
            and not siguiente.startswith("--")
        ):
            salida.append(f"{actual}={siguiente}")
            i += 2
            continue
        salida.append(actual)
        i += 1
    return salida


def _parsear_temperatura(valor: str) -> float | None:
    """``none`` → omitir el parámetro; si no, un float en [0, 2]."""
    if str(valor).strip().lower() in {"none", "null", ""}:
        return None
    try:
        numero = float(valor)
    except ValueError as exc:
        raise ValueError(f"--temperatura inválida: {valor!r}") from exc
    if not 0 <= numero <= 2:
        raise ValueError("--temperatura debe estar entre 0 y 2")
    return numero


def main(argv: Sequence[str] | None = None) -> int:
    """Punto de entrada. Devuelve el código de salida (no llama a ``sys.exit``)."""
    if argv is None:
        argv = sys.argv[1:]
    args = construir_parser().parse_args(_normalizar_argv(list(argv)))

    try:
        temperatura = _parsear_temperatura(args.temperatura)
    except ValueError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    if args.workers < 1:
        print("error: --workers debe ser >= 1", file=sys.stderr)
        return 2
    if args.modo in {"validar", "diff"} and not args.datos and args.rutas:
        print(
            f"error: --modo {args.modo} necesita --datos (los datos cargados en Mendel)",
            file=sys.stderr,
        )
        return 2

    # Precios y zona horaria del reporte de gastos.
    try:
        precios = parsear_precios(args.precios) if args.precios else {}
        tz, tz_etiqueta = _tz_desde(args.tz)
    except ValueError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2

    salida = Path(args.salida)
    # Precios: los de CLI pisan la tabla de referencia para esta corrida.
    precios_efectivos = dict(precios)
    if args.precio_entrada is not None or args.precio_salida is not None:
        p_e, p_s = precio_de(args.modelo, precios)
        precios_efectivos[args.modelo] = (
            args.precio_entrada if args.precio_entrada is not None else p_e,
            args.precio_salida if args.precio_salida is not None else p_s,
        )

    def _opciones(**extra: Any) -> Opciones:
        """Arma las opciones con los valores comunes ya resueltos."""
        base: dict[str, Any] = dict(
            modo=args.modo,
            modelo=args.modelo,
            detalle=args.detalle,
            temperatura=None,
            max_tokens=args.max_tokens,
            esfuerzo=args.esfuerzo,
            salida=salida,
            forzar=args.forzar,
            workers=args.workers,
            dry_run=args.dry_run,
            incluir_ejemplo=args.incluir_ejemplo,
            precios=precios_efectivos,
            tz=tz,
            tz_etiqueta=tz_etiqueta,
            csv_delim=args.csv_delim,
            csv_decimal=args.csv_decimal,
            precio_entrada=args.precio_entrada,
            precio_salida=args.precio_salida,
        )
        base.update(extra)
        return Opciones(**base)

    # Sin rutas: sólo se emite el reporte de gastos de la carpeta de salida
    # (a stdout, y además a archivo si se pidió).
    if not args.rutas:
        return _emitir_reporte_gastos(_opciones(), args)

    cargar_env(args.env)
    api_key = resolver_api_key(args.api_key)
    cliente = None
    # El modo diff compara en Python y reutiliza las extracciones ya guardadas:
    # NO llama a la API, así que no necesita clave ni el paquete openai.
    necesita_api = not args.dry_run and args.modo != "diff"
    if necesita_api:
        if not api_key:
            print(
                "error: falta la clave de API. Definí OPENAI_API_KEY (o un .env, o "
                "--api-key).",
                file=sys.stderr,
            )
            return 2
        try:
            from openai import OpenAI
        except ImportError:
            print(
                "error: falta el paquete openai. Instalalo con: pip install openai",
                file=sys.stderr,
            )
            return 2
        cliente = OpenAI(api_key=api_key)

    prompt_path = Path(args.prompt)
    try:
        sistema, user_template = cargar_prompt(prompt_path)
    except (FileNotFoundError, ValueError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2

    datos: dict[str, dict] = {}
    if args.datos:
        try:
            datos = cargar_datos(args.datos)
        except (FileNotFoundError, ValueError, json.JSONDecodeError) as exc:
            print(f"error: no se pudieron cargar los datos: {exc}", file=sys.stderr)
            return 2
        if not datos:
            print(f"error: --datos {args.datos} no contiene documentos", file=sys.stderr)
            return 2

    opciones = _opciones(temperatura=temperatura)

    rutas = [Path(r) for r in args.rutas]
    raiz = _raiz_comun(rutas)
    imagenes = _expandir(rutas, EXTENSIONES_IMAGEN)
    if args.limite > 0:
        imagenes = imagenes[: args.limite]
    if not imagenes:
        print("No hay imágenes que procesar.", file=sys.stderr)
        return 0

    extracciones = (
        leer_extracciones_previas(opciones.salida) if opciones.modo == "diff" else {}
    )

    sufijo = {
        "validar": "validacion",
        "extraer": "extraccion",
        "diff": "validacion",
    }[opciones.modo]

    tareas: list[tuple[Path, Path]] = []
    for img in imagenes:
        try:
            relativo = img.resolve().relative_to(raiz.resolve())
        except (ValueError, OSError):
            relativo = Path(img.name)
        destino = opciones.salida / relativo.with_suffix(f".{sufijo}.json")
        if not opciones.forzar and ya_procesado(destino):
            continue
        tareas.append((img, destino))

    print(
        f"modo             : {opciones.modo}\n"
        f"modelo           : {opciones.modelo} (detalle de imagen: {opciones.detalle})\n"
        f"raíz de espejado : {raiz}\n"
        f"salida           : {opciones.salida}\n"
        f"imágenes         : {len(imagenes)}"
        + (f" (pendientes: {len(tareas)})" if len(tareas) != len(imagenes) else "")
        + (f"\ndatos cargados   : {len(datos)} documento(s)" if datos else ""),
        file=sys.stderr,
    )

    if not tareas:
        print("Nada pendiente: todas las salidas ya existen (usá --forzar).", file=sys.stderr)
        return 0

    registros: list[dict] = []
    try:
        if opciones.workers == 1:
            for img, destino in tareas:
                registro = procesar(
                    img, raiz, cliente, sistema, user_template, datos, extracciones, opciones
                )
                _guardar(destino, registro, escribir=not opciones.dry_run)
                registros.append(registro)
                if args.detalle_log:
                    _imprimir_detalle(registro)
        else:
            with ThreadPoolExecutor(max_workers=opciones.workers) as pool:
                futuros = [
                    pool.submit(
                        procesar,
                        img,
                        raiz,
                        cliente,
                        sistema,
                        user_template,
                        datos,
                        extracciones,
                        opciones,
                    )
                    for img, _ in tareas
                ]
                for i, (futuro, (_, destino)) in enumerate(zip(futuros, tareas), 1):
                    registro = futuro.result()
                    _guardar(destino, registro, escribir=not opciones.dry_run)
                    registros.append(registro)
                    if args.detalle_log:
                        _imprimir_detalle(registro)
                    elif i % 25 == 0:
                        print(f"  … {i}/{len(tareas)}", file=sys.stderr)
    except KeyboardInterrupt:
        print("\nInterrumpido por el usuario.", file=sys.stderr)
        return 130

    rep = resumen(registros, opciones)
    _imprimir_resumen(rep)

    # Gasto de lo recién procesado (sólo las llamadas pagas).
    apuntes_nuevos = [
        apunte
        for registro in registros
        if (apunte := apunte_de_registro(registro, opciones)) is not None
    ]
    if apuntes_nuevos:
        nuevo = totalizar(apuntes_nuevos)
        print(
            f"\n--- Gasto de esta corrida ---\n"
            f"extracciones : {nuevo['total']['apuntes']}\n"
            f"costo        : US$ {_formato_coste(nuevo['total']['costo_usd'])}"
            + ("  (precio parcial)" if nuevo["costo_parcial"] else "")
        )
        if nuevo["sin_precio"]:
            print(
                f"  ⚠ {nuevo['sin_precio']} sin precio para su modelo; el costo real "
                "es mayor. Pasá --precios o --precio-entrada/--precio-salida.",
                file=sys.stderr,
            )

    if args.reporte:
        destino_reporte = Path(args.reporte)
        destino_reporte.parent.mkdir(parents=True, exist_ok=True)
        destino_reporte.write_text(
            json.dumps(rep, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        print(f"\nreporte escrito: {destino_reporte}")

    if args.reporte_gastos or args.csv_gastos:
        repo = _emitir_reporte_gastos(opciones, args)
        return 1 if (rep["errores"] or repo) else 0

    return 1 if rep["errores"] else 0


def _emitir_reporte_gastos(opciones: Opciones, args: argparse.Namespace) -> int:
    """Emite el reporte de gastos de la carpeta de salida (no procesa nada).

    Lee **todo** lo que hay en ``--salida`` (no sólo la última corrida), así el
    reporte es del histórico. Devuelve 1 si hay extracciones sin precio (el total
    quedaría menor al real) o 0 si no.
    """
    apuntes, descartados = leer_apuntes(opciones.salida, opciones)
    if not apuntes:
        print(
            f"No hay extracciones facturables en {opciones.salida}."
            + (f" ({descartados} archivo(s) sin gasto)" if descartados else ""),
            file=sys.stderr,
        )
        return 0

    rep = {
        **totalizar(apuntes),
        "opciones": {
            "salida": str(opciones.salida),
            "tz": opciones.tz_etiqueta,
            "precios": {k: list(v) for k, v in opciones.precios.items()},
            "precios_referencia_usados": not opciones.precios,
        },
        "registros_sin_gasto": descartados,
    }
    imprimir_reporte_gastos(rep, apuntes)

    if args.reporte_gastos:
        ruta = Path(args.reporte_gastos)
        ruta.parent.mkdir(parents=True, exist_ok=True)
        ruta.write_text(
            json.dumps({**rep, "apuntes": apuntes}, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        print(f"\nreporte de gastos escrito: {ruta}")

    if args.csv_gastos:
        ruta = Path(args.csv_gastos)
        escribir_csv_gastos(
            ruta, apuntes, delim=opciones.csv_delim, decimal=opciones.csv_decimal
        )
        print(f"CSV de gastos escrito: {ruta}")

    return 1 if rep["sin_precio"] else 0


def _guardar(destino: Path, registro: dict, *, escribir: bool = True) -> None:
    """Escribe el registro del documento (también los fallidos, para auditarlos).

    Con ``escribir=False`` (``--dry-run``) **no toca el disco**: si dejara los
    archivos en la carpeta de salida, una corrida real posterior los tomaría como
    ya procesados y saltearía esas imágenes (la reanudación se envenenaría).
    """
    if not escribir:
        return
    destino.parent.mkdir(parents=True, exist_ok=True)
    destino.write_text(
        json.dumps(registro, ensure_ascii=False, indent=2), encoding="utf-8"
    )


def _imprimir_detalle(registro: dict) -> None:
    """Una línea por archivo (a stderr)."""
    if error := registro.get("error"):
        print(f"  [error] {registro['origen']}: {error}", file=sys.stderr)
        return
    resultado = registro.get("resultado") or {}
    estado = resultado.get("estado_global") or (
        resultado.get("legibilidad") and f"legibilidad {resultado['legibilidad']}"
    ) or ("(dry-run)" if resultado.get("_dry_run") else "?")
    tokens = (registro.get("uso") or {}).get("total_tokens") or 0
    print(
        f"  [{estado}] {registro['origen']}  tokens={tokens}"
        + (f"  datos={registro.get('datos_clave')}" if registro.get("datos_clave") else ""),
        file=sys.stderr,
    )
    # La aritmética que no cierra es una señal: se avisa acá mismo.
    if aviso := (registro.get("aritmetica") or {}).get("aviso"):
        print(f"      ⚠ aritmética: {aviso}", file=sys.stderr)


if __name__ == "__main__":
    sys.exit(main())

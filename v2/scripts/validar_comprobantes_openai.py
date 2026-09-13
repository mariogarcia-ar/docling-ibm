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

**Costo.** El reporte siempre informa los **tokens reales** que devuelve la API
(``usage``) y una **estimación de tokens de imagen** (fórmula de OpenAI por
``detail``). El costo en USD solo se calcula si pasás ``--precio-entrada`` y
``--precio-salida``: el script **no inventa precios**.

Credenciales: la clave se lee de ``OPENAI_API_KEY`` (o ``--api-key``), y se
puede tener en un ``.env`` (se carga ``--env`` o ``./.env`` sin pisar lo que ya
esté en el entorno). La clave **nunca** se imprime ni se guarda en la salida.

Uso:
    python scripts/validar_comprobantes_openai.py <ruta|carpeta>... [opciones]

Ejemplos:
    # Extracción pura sobre 5 imágenes (verificar conexión y formato primero)
    python scripts/validar_comprobantes_openai.py ../procesados \\
        --modo extraer --limite 5 --detalle

    # Validación contra los datos cargados por el empleado
    python scripts/validar_comprobantes_openai.py ../procesados \\
        --datos datos_mendel.json --workers 4 -o validaciones

    # Diff determinístico en Python (sin gastar tokens de comparación)
    python scripts/validar_comprobantes_openai.py ../procesados \\
        --modo diff --datos datos_mendel.json

    # Ver qué se enviaría, sin llamar a la API
    python scripts/validar_comprobantes_openai.py ../procesados --dry-run --limite 3

Códigos de salida: 0 = todo ok (o nada que hacer); 1 = hubo fallos;
2 = error de uso o de configuración; 130 = interrumpido (Ctrl-C).
"""

from __future__ import annotations

import argparse
import base64
import hashlib
import json
import math
import os
import re
import sys
import unicodedata
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from datetime import date, datetime, timezone
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
            "subtotal": _numero_o_null("Neto sin impuestos, si está discriminado."),
            "discrimina_impuestos": _booleano_o_null(
                "Si el comprobante desglosa impuestos."
            ),
            "iva": _numero_o_null("IVA discriminado."),
            "impuestos_internos": _numero_o_null(),
            "percepciones_iibb": _numero_o_null(),
            "otros_impuestos": _numero_o_null(),
            "importe_total": _numero_o_null("Total impreso en el comprobante."),
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
    mismo_subtotal = _mismo_monto(extraccion.get("subtotal"), datos.get("subtotal"))
    if extraccion.get("discrimina_impuestos") is False and mismo_subtotal is None:
        mismo_subtotal = _mismo_monto(extraccion.get("importe_total"), datos.get("subtotal"))
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
    precio_entrada: float | None
    precio_salida: float | None


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

    registro["resultado"] = respuesta.datos
    if opciones.modo == "extraer":
        registro["extraccion"] = respuesta.datos
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
    """Resumen agregado: estados, errores, tokens reales y costo (si se informó)."""
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

    costo = None
    if opciones.precio_entrada is not None or opciones.precio_salida is not None:
        costo = round(
            prompt_tokens * (opciones.precio_entrada or 0) / 1e6
            + completion_tokens * (opciones.precio_salida or 0) / 1e6,
            4,
        )

    return {
        "archivos": len(registros),
        "ok": len(registros) - len(errores),
        "errores": len(errores),
        "estados_globales": estados,
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
            "      --modo extraer --limite 5 --detalle\n"
            "  python scripts/validar_comprobantes_openai.py ../procesados \\\n"
            "      --datos datos_mendel.json --workers 4 -o validaciones\n"
            "  python scripts/validar_comprobantes_openai.py ../procesados \\\n"
            "      --modo diff --datos datos_mendel.json\n"
            "  python scripts/validar_comprobantes_openai.py ../procesados \\\n"
            "      --dry-run --limite 3\n"
        ),
    )
    parser.add_argument(
        "rutas", nargs="+", help="Imágenes y/o carpetas (se recorren recursivo)."
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
        help="Precio por 1M de tokens de entrada, para estimar el costo (opcional).",
    )
    parser.add_argument(
        "--precio-salida",
        type=float,
        metavar="USD_POR_1M",
        help="Precio por 1M de tokens de salida, para estimar el costo (opcional).",
    )
    return parser


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
    args = construir_parser().parse_args(argv)

    try:
        temperatura = _parsear_temperatura(args.temperatura)
    except ValueError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    if args.workers < 1:
        print("error: --workers debe ser >= 1", file=sys.stderr)
        return 2
    if args.modo in {"validar", "diff"} and not args.datos:
        print(
            f"error: --modo {args.modo} necesita --datos (los datos cargados en Mendel)",
            file=sys.stderr,
        )
        return 2

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

    opciones = Opciones(
        modo=args.modo,
        modelo=args.modelo,
        detalle=args.detalle,
        temperatura=temperatura,
        max_tokens=args.max_tokens,
        esfuerzo=args.esfuerzo,
        salida=Path(args.salida),
        forzar=args.forzar,
        workers=args.workers,
        dry_run=args.dry_run,
        incluir_ejemplo=args.incluir_ejemplo,
        precio_entrada=args.precio_entrada,
        precio_salida=args.precio_salida,
    )

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

    if args.reporte:
        destino_reporte = Path(args.reporte)
        destino_reporte.parent.mkdir(parents=True, exist_ok=True)
        destino_reporte.write_text(
            json.dumps(rep, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        print(f"\nreporte escrito: {destino_reporte}")

    return 1 if rep["errores"] else 0


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


if __name__ == "__main__":
    sys.exit(main())

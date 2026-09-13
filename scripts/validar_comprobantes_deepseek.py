#!/usr/bin/env python
"""Validación/extracción de comprobantes con la API de DeepSeek (visión).

Es el **gemelo DeepSeek** de ``validar_comprobantes_openai.py``: mismo prompt,
mismos modos, mismo formato de salida y mismo reporte de gastos. Solo cambia el
proveedor. La comparación es directa porque el SDK ``openai`` habla con
``https://api.deepseek.com`` (endpoint OpenAI-compatible) y la imagen viaja con
la misma forma de contenido ``image_url`` que en OpenAI.

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

**Salida estructurada.** DeepSeek **no** soporta ``json_schema`` estricto: solo
``response_format={'type': 'json_object'}``, que garantiza *JSON válido* pero no
la **forma**. Por eso acá el formato se sostiene en tres patas:

1. el **JSON de ejemplo de salida** del propio prompt — la doc de DeepSeek lo
   pide explícitamente para JSON mode, así que acá **no se omite** por defecto
   (``--prompt-minimo`` lo quita, pero es un experimento);
2. ``response_format={'type': 'json_object'}``, que evita el texto suelto
   alrededor del JSON;
3. **validación local contra el esquema** (``jsonschema``, incluido en el
   entorno) y un **reintento con el error de validación como feedback**, porque
   un JSON válido puede tener la forma equivocada.

**Ahorro de tokens en el prompt.** El prompt completo se manda como ``system`` y
su prefijo es idéntico en todas las llamadas del lote: el **caché de contexto**
de DeepSeek lo cobra a una fracción del precio de entrada. El `usage` de esta
corrida reporta los ``prompt_cache_hit_tokens`` reales.

**Proveedor.** El SDK ``openai`` apuntado a ``https://api.deepseek.com``. El
modelo por defecto es ``deepseek-flash`` — el único del catálogo que **acepta
imágenes** (``deepseek-v4-pro`` no). El **modo de pensamiento** (thinking) viene
activado por defecto y acá no molesta, porque el script **no** pide
``temperature``: en thinking mode ese parámetro no tiene efecto, así que
cualquier ``--temperatura`` se **ignora y se declara** en vez de fingir que se
aplicó.

**Costo y reporte de gastos.** Cada salida guarda los **tokens reales** que
devuelve la API (``usage``), los precios aplicados y el **costo en USD** de cada
llamada, además de la fecha/hora. Antes de gastar, ``--dry-run`` **estima** el
costo de la corrida sin llamar a la API (prompt real + **tope fijo** de tokens de
imagen de DeepSeek, calibrado con el histórico si lo hay). Después, el reporte de
gastos (``--reporte-gastos`` / ``--csv-gastos``) se arma del **histórico** de la
carpeta de salida: totales por día, por modelo y por modo, y una fila por
extracción en CSV. Los precios salen de una tabla de referencia editable
(``PRECIOS_REFERENCIA``) que se puede pisar con ``--precios``,
``--precio-entrada``/``--precio-salida`` o ``--precio-cache``; si un modelo no
tiene precio, el costo queda ``null`` y el reporte lo **declara** en vez de sumar
un cero que parece exacto. El período se agrupa con ``--tz`` (``local`` por
defecto, o un offset como ``-03:00``).

⚠️ **Dos diferencias de fondo con la versión OpenAI, que el reporte declara:**

- **la resolución no abarata la imagen**: DeepSeek redimensiona *toda* imagen a
  ~1300×1300 px antes de inferir (y agranda las chicas), con un tope de 1.024
  tokens por imagen. ``--detalle low`` se manda, pero no cambia el costo;
- **un fallo puede ser un gasto**: si la respuesta vuelve con la forma equivocada
  y se reintenta, esos tokens ya se consumieron. El registro se marca
  ``fallo: true`` y el reporte lo declara aparte, en vez de esconderlo dentro del
  total como si fuera una extracción válida.

Credenciales: la clave se lee de ``DEEPSEEK_API_KEY`` (o ``--api-key``), y se
puede tener en un ``.env`` (se carga ``--env`` o ``./.env`` sin pisar lo que ya
esté en el entorno). La clave **nunca** se imprime ni se guarda en la salida.

Uso:
    python scripts/validar_comprobantes_deepseek.py <ruta|carpeta>... [opciones]
    python scripts/validar_comprobantes_deepseek.py --reporte-gastos gastos.json

Ejemplos:
    # Extracción pura sobre 5 imágenes (verificar conexión y formato primero)
    python scripts/validar_comprobantes_deepseek.py ../procesados \\
        --modo extraer --limite 5 --detalle-log

    # Validación contra los datos cargados por el empleado
    python scripts/validar_comprobantes_deepseek.py ../procesados \\
        --datos datos_mendel.json --workers 4 -o validaciones

    # Diff determinístico en Python (sin gastar tokens de comparación)
    python scripts/validar_comprobantes_deepseek.py ../procesados \\
        --modo diff --datos datos_mendel.json

    # Reporte de gastos del histórico (no llama a la API)
    python scripts/validar_comprobantes_deepseek.py --reporte-gastos gastos.json \\
        --csv-gastos gastos.csv --tz -03:00

    # Simular el costo de un lote antes de gastar (no llama a la API)
    python scripts/validar_comprobantes_deepseek.py ../procesados --modo extraer \\
        --dry-run --limite 500 --detalle-log

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

#: Modelo por defecto. ``deepseek-flash`` es el único del catálogo que acepta
#: imágenes (``deepseek-v4-pro`` no las soporta).
MODELO_POR_DEFECTO = "deepseek-flash"

#: Endpoint OpenAI-compatible de DeepSeek (el SDK ``openai`` se apunta acá).
BASE_URL_POR_DEFECTO = "https://api.deepseek.com"

#: Reintentos cuando el JSON vuelve con la forma equivocada. El 1º reintento usa
#: la respuesta completa + el error de validación como feedback; los siguientes
#: van con el mismo texto (el modelo no es determinístico).
MAX_REINTENTOS_ESQUEMA = 3

#: Tope de tokens de salida. Por defecto **no se manda**: DeepSeek ya usa 8K en
#: modo no-thinking y **64K** con thinking activado (el default acá).
#: ⚠️ Mandar un tope **menor** trunca el JSON a la mitad y el parseo falla con un
#: error críptico (`Unterminated string`): se comprobó con 8.192, que parecía
#: holgado pero es la octava parte del default del proveedor. Si hace falta
#: acotar el gasto, subilo con `--max-tokens`, no lo bajes a ciegas.
MAX_TOKENS_POR_DEFECTO: int | None = None

#: Lado del cuadro al que DeepSeek redimensiona toda imagen antes de inferir
#: (~1300×1300 = 1.690.000 px) y tope de tokens por imagen. Ver la guía
#: «Vision → Token Usage»: una imagen grande y una gigante cuestan lo mismo; una
#: chica se **agranda** (piso ~544×544) en vez de costar menos.
PIXELES_OBJETIVO_IMAGEN = 1300 * 1300
TOKENS_MAX_IMAGEN = 1024
PIXELES_MINIMOS_IMAGEN = 544 * 544

#: Límite de la API para una imagen inline (base64): 32 MiB por imagen (y 48 MiB
#: de cuerpo por petición). Se valida contra el peso del archivo, con el margen
#: que agrega el base64.
LIMITE_BYTES_IMAGEN = 32 * 1024 * 1024

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

#: Proporción caracteres/token del prompt para estimar en ``--dry-run`` sin
#: llamar a la API. Referencia heredada de la corrida medida con ``gpt-4o``
#: (10.919 caracteres = 2.786 tokens en 10 extracciones reales → 3,92 char/token).
#: ⚠️ Es de **otro** tokenizador: sirve sólo como arranque. Con ≥3 extracciones
#: del mismo modelo y modo, el histórico (``_tokens_de_texto_historicos``)
#: reemplaza esta constante y la estimación se declara como ``historico``.
CHARS_POR_TOKEN_ESTIMADO = 3.92

#: Tokens de salida típicos, para estimar cuando no hay histórico con qué
#: calibrar. Medido en las mismas extracciones reales (~240).
COMPLETION_TOKENS_TIPICO = 240

#: Mínimo de extracciones previas para calibrar con el histórico en vez de con la
#: fórmula. Con menos, el promedio sería ruido.
MIN_MUESTRAS_PARA_CALIBRAR = 3

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
    return explicita or os.environ.get("DEEPSEEK_API_KEY") or None


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
    user_template: str,
    datos: dict | None,
    *,
    incluir_ejemplo: bool,
    ejemplo_alternativo: str | None = None,
) -> str:
    """Arma el texto del mensaje ``user`` a partir del template del ``.md``.

    Sustituye ``[IMAGEN]`` por una nota (la imagen viaja como parte de contenido
    ``image_url``, que es el mecanismo de la API) y reemplaza el bloque de datos
    por el JSON real. Si ``incluir_ejemplo`` es falso, **quita el JSON de ejemplo
    de salida** (en DeepSeek es un experimento: el JSON mode lo pide explícito).

    ``ejemplo_alternativo`` reemplaza el ejemplo del ``.md``. ⚠️ Hace falta en el
    modo ``extraer``: el template trae el ejemplo del modo **comparar**
    (``estado_global``/``campos``), así que sin esto el modelo devuelve esa forma
    — y el validador, que espera la de extracción, la rechaza.
    """
    cabecera, ejemplo = _separar_template(user_template)
    cabecera = cabecera.replace("[IMAGEN]", "(imagen adjunta a continuación)")

    if siguiente := re.search(r"\{.*\}", cabecera, re.DOTALL):
        bloque_datos = json.dumps(datos, ensure_ascii=False, indent=2) if datos else "{}"
        cabecera = (
            cabecera[: siguiente.start()] + bloque_datos + cabecera[siguiente.end() :]
        )

    if not incluir_ejemplo:
        return cabecera
    return f"{cabecera}\n\n{ejemplo_alternativo or ejemplo}".rstrip()


#: Cierre del SYSTEM PROMPT del ``.md`` que pertenece al modo **comparar**:
#: desde «Instrucciones generales:» (ahí adentro está el ``estado_global`` y el
#: «Respondé ÚNICAMENTE en el formato JSON especificado»). En el modo ``extraer``
#: se reemplaza por :data:`INSTRUCCIONES_SISTEMA_EXTRACCION`.
#:
#: ⚠️ No es cosmético: el texto del ``.md`` está escrito para *comparar* contra
#: los datos de Mendel. En OpenAI eso quedaba tapado porque el ``json_schema``
#: estricto imponía la forma **en el servidor**; DeepSeek solo garantiza JSON
#: válido, así que el prompt manda — y con este cierre el modelo devolvía
#: ``estado_global``/``campos`` (la forma de *validar*) en vez de la extracción.
_RE_CIERRE_SISTEMA = re.compile(r"\nInstrucciones generales:.*\Z", re.DOTALL)

INSTRUCCIONES_SISTEMA_EXTRACCION = (
    "\n"
    "Instrucciones generales:\n"
    "- Esta pasada es de **transcripción**, no de comparación: NO hay datos\n"
    "  cargados con los que contrastar. No emitas un «estado global» ni\n"
    "  comparaciones campo a campo; transcribí lo que muestra el comprobante.\n"
    "- Completá **TODOS** los campos del formato JSON: ninguno es opcional. Si un\n"
    "  dato no figura en el comprobante, va `null` (o `false`, o `[]` según el\n"
    "  tipo), pero la clave tiene que estar.\n"
    "- Si un dato no es legible en la imagen (borroso, cortado, arrugado), poné el\n"
    "  campo en `null` y agregalo a `campos_no_legibles`; nunca lo omitas en\n"
    "  silencio ni asumas un valor.\n"
    "- Nunca inventes valores que no estén en la imagen.\n"
    "- Priorizá transcribir con exactitud los importes y el CUIT: son los que se\n"
    "  controlan después.\n"
    "\n"
    "Respondé ÚNICAMENTE con el objeto JSON del formato especificado, sin texto\n"
    "adicional ni bloques de código alrededor."
)


def sistema_de_extraccion(sistema: str) -> str:
    """Adapta el SYSTEM PROMPT del ``.md`` al modo ``extraer``.

    Conserva las 15 reglas de negocio (son conocimiento del dominio y varias
    —CUIT, importes, litros, legibilidad— aplican igual al transcribir) y
    reemplaza el cierre de *comparación* por las instrucciones de transcripción.
    Si el cierre no aparece (el ``.md`` cambió de forma), se agrega igual, así la
    corrida no queda sin encuadre.
    """
    adaptado, n = _RE_CIERRE_SISTEMA.subn(
        INSTRUCCIONES_SISTEMA_EXTRACCION, sistema
    )
    if n == 0:
        adaptado = sistema.rstrip() + "\n" + INSTRUCCIONES_SISTEMA_EXTRACCION
    return adaptado.strip()


def ejemplo_desde_esquema(esquema: dict[str, Any]) -> str:
    """Ejemplo de salida **generado desde el esquema** que se va a validar.

    El JSON mode de DeepSeek exige que el prompt traiga un ejemplo de la salida,
    y ese ejemplo es lo que el modelo copia. Escribirlo a mano permite que
    divergja del validador (fue exactamente el bug de arriba: el ejemplo del
    ``.md`` era el del modo *validar*). Generarlo del mismo esquema que valida
    hace que **no puedan separarse**: si el esquema cambia, el ejemplo cambia.
    """
    return json.dumps(_valor_de_ejemplo(esquema), ensure_ascii=False, indent=2)


def _valor_de_ejemplo(esquema: dict[str, Any]) -> Any:
    """Valor de relleno con la **forma** del esquema (los «<…>» son a completar)."""
    if "anyOf" in esquema:
        # Se prefiere el tipo real sobre ``null``: así se ve la forma completa.
        opciones = [o for o in esquema["anyOf"] if o.get("type") != "null"]
        return _valor_de_ejemplo((opciones or esquema["anyOf"])[0])
    if "enum" in esquema:
        return esquema["enum"][0]
    tipo = esquema.get("type")
    if tipo == "object":
        return {k: _valor_de_ejemplo(v) for k, v in esquema["properties"].items()}
    if tipo == "array":
        return ["<…>"]
    if tipo == "string":
        return f"<{esquema.get('description') or 'texto'}>"
    if tipo == "number":
        return 0
    if tipo == "boolean":
        return False
    return None


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


def armar_prompt_efectivo(
    modo: str,
    sistema: str,
    user_template: str,
    datos: dict | None,
    *,
    incluir_ejemplo: bool,
) -> tuple[str, str]:
    """Devuelve ``(system, user)`` **efectivos** del modo.

    ⚠️ Función única para correr y para estimar: si la estimación armara el
    prompt por su cuenta, el costo simulado dejaría de corresponder al real
    (misma lección que ``salida_de()``: escribir y calcular no pueden divergir).
    """
    esquema = esquema_validacion() if modo == "validar" else esquema_extraccion()
    usuario = construir_mensaje_usuario(
        user_template,
        datos,
        incluir_ejemplo=incluir_ejemplo,
        # En ``extraer`` el ejemplo del .md es el de *comparar*: se reemplaza por
        # uno generado del esquema de extracción (ver ``ejemplo_desde_esquema``).
        ejemplo_alternativo=(
            ejemplo_desde_esquema(esquema) if modo == "extraer" else None
        ),
    )
    if modo == "extraer":
        return sistema_de_extraccion(sistema), prompt_de_extraccion(usuario)
    return sistema, usuario



def hash_prompt(*partes: str) -> str:
    """Hash corto del prompt efectivo, para auditar qué prompt produjo cada salida."""
    h = hashlib.sha256()
    for parte in partes:
        h.update(parte.encode("utf-8"))
        h.update(b"\x00")
    return f"sha256:{h.hexdigest()[:16]}"


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
    """Estimación de tokens de imagen con la fórmula de **DeepSeek**.

    DeepSeek no cobra por mosaicos como OpenAI: redimensiona siempre a un total
    de ~1300×1300 px (agrandando las chicas hasta un piso de ~544×544) y **tope
    duro de 1.024 tokens por imagen**. Es decir: para DeepSeek la resolución no
    cambia el costo de la imagen — una foto de 800×600 y una de 5000×5000 pagan
    lo mismo.

    ``detalle='low'`` la baja a 512×512 antes de inferir, pero **no** ahorra
    tokens (el redimensionado posterior la vuelve a llevar al objetivo): se
    devuelve el mismo valor y el llamador lo declara.

    Es una **estimación**; el número que manda es el ``usage`` de la API.
    """
    del ancho, alto, detalle  # el costo no depende de las dimensiones
    return TOKENS_MAX_IMAGEN


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
    #: Se pidió temperatura pero NO se envió (thinking mode la ignora): se declara
    #: en el registro en vez de dar a entender que se aplicó.
    temperatura_ignorada: bool = False
    #: Cuántas repreguntas hizo falta por forma del JSON (0 = salió bien).
    reintentos: int = 0
    #: Detalle de los problemas de forma ya corregidos (para auditar el prompt).
    avisos_esquema: list[str] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return self.error is None and self.datos is not None


#: Centinela para distinguir «todavía no se buscó el validador» de «no está».
_SIN_RESOLVER = object()
_VALIDADOR_JSONSCHEMA_CACHE: Any = _SIN_RESOLVER


def _validador_jsonschema() -> Any:
    """Validador de JSON Schema (``jsonschema``), o ``None`` si no está instalado.

    DeepSeek **no** impone la forma del JSON en el servidor (no hay
    ``json_schema`` estricto, solo ``json_object``), así que la forma se valida
    acá. Si el paquete falta, se sigue sin validación y el registro lo declara
    (``esquema_validado: false``) en vez de fingir que se controló.
    """
    global _VALIDADOR_JSONSCHEMA_CACHE
    if _VALIDADOR_JSONSCHEMA_CACHE is _SIN_RESOLVER:
        try:
            from jsonschema import Draft202012Validator
        except ImportError:
            _VALIDADOR_JSONSCHEMA_CACHE = None
        else:
            _VALIDADOR_JSONSCHEMA_CACHE = Draft202012Validator
    return _VALIDADOR_JSONSCHEMA_CACHE


def _errores_de_esquema(validador_clase: Any, esquema: dict, datos: Any) -> list[str]:
    """Problemas de **forma** del JSON contra el esquema (``[]`` si está bien).

    Devuelve mensajes cortos y con la ruta del campo, que es lo que se le manda
    de vuelta al modelo como feedback. Sin ``jsonschema`` instalado no hay
    control posible: se devuelve lista vacía y el registro lo declara aparte.
    """
    if validador_clase is None:
        return []
    errores = []
    for error in sorted(
        validador_clase(esquema).iter_errors(datos),
        key=lambda e: [str(p) for p in e.absolute_path],
    ):
        ruta = "/".join(str(p) for p in error.absolute_path) or "(raíz)"
        # ⚠️ No se usa `error.message` tal cual: incluye el **valor** recibido
        # («'x' is not of type 'number'»), o sea la lectura del comprobante, y
        # este texto viaja al registro de salida y al log. Se nombra el problema
        # por su tipo, sin citar el contenido del modelo.
        tipo_esperado = (error.validator_value or {}).get("type") if isinstance(
            error.validator_value, dict
        ) else None
        if error.validator == "required":
            detalle = "falta una propiedad obligatoria"
        elif error.validator in {"anyOf", "oneOf"}:
            detalle = "no coincide con ninguno de los tipos permitidos"
        elif error.validator == "type":
            detalle = f"se esperaba el tipo «{tipo_esperado or 'declarado'}»"
        elif error.validator == "additionalProperties":
            detalle = "hay propiedades no declaradas en el esquema"
        else:
            detalle = f"no cumple la restricción «{error.validator}»"
        errores.append(f"{ruta}: {detalle}")
    return errores


def _tipo_compatible(esquema: dict[str, Any], valor: Any) -> bool:
    """True si ``valor`` es del tipo que declara ``esquema`` (para elegir rama)."""
    tipo = esquema.get("type")
    if tipo == "null":
        return valor is None
    if tipo == "boolean":
        return isinstance(valor, bool)
    if tipo == "number":
        return isinstance(valor, (int, float)) and not isinstance(valor, bool)
    if tipo == "string":
        return isinstance(valor, str)
    if tipo == "object":
        return isinstance(valor, dict)
    if tipo == "array":
        return isinstance(valor, list)
    return True


def _coincidencia_enum(valor: str, opciones: Sequence[Any]) -> str | None:
    """Opción canónica del ``enum`` que corresponde a ``valor``, o ``None``.

    Compara sin distinguir mayúsculas, tildes ni espacios: que el modelo devuelva
    «Buena» donde el esquema dice «buena» es una diferencia de **formato**, no un
    error de contenido. ``_norm`` (definida más abajo) es la misma normalización
    que usa el diff.
    """
    objetivo = _norm(valor)
    for opcion in opciones:
        if isinstance(opcion, str) and _norm(opcion) == objetivo:
            return opcion
    return None


def normalizar_por_esquema(
    esquema: dict[str, Any], datos: Any, notas: list[str], ruta: str = ""
) -> Any:
    """Normaliza diferencias de **forma** contra el esquema, dejando constancia.

    Hoy solo cubre los ``enum``: si el valor coincide con una de las opciones
    salvo mayúsculas/tildes/espacios, se reemplaza por la opción canónica y se
    anota en ``notas``. Es a propósito lo más acotado posible: cualquier otra
    diferencia sigue siendo un error y se repregunta.

    ⚠️ Por qué importa: cada repregunta **se paga**. Que el modelo devuelva
    «Buena» en vez de «buena» costaba un reintento completo por un detalle
    tipográfico (comprobado con un comprobante real). Se **declara** en el
    registro en vez de corregirlo en silencio.
    """
    if not isinstance(esquema, dict):
        return datos

    if "enum" in esquema and isinstance(datos, str):
        canonica = _coincidencia_enum(datos, esquema["enum"])
        if canonica is not None and canonica != datos:
            notas.append(
                f"{ruta or '(raíz)'}: «{datos}» normalizado a «{canonica}»"
            )
            return canonica
        return datos

    if "anyOf" in esquema:
        for opcion in esquema["anyOf"]:
            if _tipo_compatible(opcion, datos):
                return normalizar_por_esquema(opcion, datos, notas, ruta)
        return datos

    if esquema.get("type") == "object" and isinstance(datos, dict):
        propiedades = esquema.get("properties", {})
        return {
            clave: (
                normalizar_por_esquema(
                    propiedades[clave], valor, notas, f"{ruta}/{clave}" if ruta else clave
                )
                if clave in propiedades
                else valor
            )
            for clave, valor in datos.items()
        }

    if esquema.get("type") == "array" and isinstance(datos, list):
        items = esquema.get("items", {})
        return [
            normalizar_por_esquema(items, valor, notas, f"{ruta}/{i}")
            for i, valor in enumerate(datos)
        ]

    return datos


def _acumular_uso(acumulado: dict[str, Any], uso: Any) -> None:
    """Suma los tokens de cada intento: **los fallidos también se pagan**.

    Si se reintenta por forma del JSON, el gasto real es la suma de todos los
    intentos, no sólo el último. Incluye los tokens de caché de DeepSeek
    (``prompt_cache_hit_tokens``), que es lo que abarata el prompt repetido.
    """
    if uso is None:
        return
    for clave in (
        "prompt_tokens",
        "completion_tokens",
        "total_tokens",
        "prompt_cache_hit_tokens",
        "prompt_cache_miss_tokens",
    ):
        valor = getattr(uso, clave, None)
        if valor is not None:
            acumulado[clave] = (acumulado.get(clave) or 0) + valor


def _pedir_correccion(
    messages: list[dict[str, Any]], texto_previo: str, problema: str
) -> None:
    """Repregunta con el problema como feedback, sin volver a pedir la imagen.

    La imagen ya está en el primer mensaje ``user`` y no se repite; el prefijo
    (system + imagen) es idéntico, así que el caché de contexto lo cobra barato.
    Si la respuesta anterior no era texto, no se agrega el turno ``assistant``
    (un mensaje vacío lo rechazaría la API).
    """
    if texto_previo.strip():
        messages.append({"role": "assistant", "content": texto_previo[:4000]})
    messages.append(
        {
            "role": "user",
            "content": [
                {
                    "type": "text",
                    "text": (
                        "La respuesta anterior NO cumple el formato pedido: "
                        f"{problema}\n"
                        "Devolvé **solo** el JSON completo y corregido, con la "
                        "misma forma del ejemplo, sin texto alrededor."
                    ),
                }
            ],
        }
    )


def llamar_api(
    cliente: Any,
    *,
    modelo: str,
    sistema: str,
    usuario: str,
    data_url: str,
    esquema: dict[str, Any],
    temperatura: float | None,
    detalle: str,
    max_tokens: int | None,
    esfuerzo: str | None,
) -> Respuesta:
    """Una llamada a Chat Completions con imagen, en JSON mode y validada local.

    DeepSeek **no** tiene structured outputs estrictos: ``response_format`` sólo
    acepta ``{'type': 'json_object'}``, que garantiza un JSON *parseable* pero no
    la **forma**. Así que la forma se sostiene con dos cosas: el JSON de ejemplo
    que ya viaja en el prompt (obligatorio en este modo, según la doc de
    DeepSeek) y la **validación contra ``esquema``** en el cliente. Si el JSON no
    cierra, se repregunta con el error de validación como feedback hasta
    ``MAX_REINTENTOS_ESQUEMA`` veces — lo más cerca del structured output del
    servidor que permite este proveedor.

    ⚠️ ``temperatura`` **se ignora** y se declara: el modo de pensamiento de
    DeepSeek (activado por defecto) no admite ese parámetro. ``detalle`` se manda
    tal cual —la API acepta ``low``/``high``/``auto``— pero en DeepSeek no cambia
    el costo de la imagen, porque toda imagen se redimensiona antes de inferir.
    """
    validador = _validador_jsonschema()
    contenido: list[dict[str, Any]] = [
        {"type": "text", "text": usuario},
        {"type": "image_url", "image_url": {"url": data_url, "detail": detalle}},
    ]
    messages: list[dict[str, Any]] = [
        {"role": "system", "content": sistema},
        {"role": "user", "content": contenido},
    ]
    extra: dict[str, Any] = {
        "model": modelo,
        "messages": messages,
        # JSON mode: JSON *válido*, no con la forma del esquema (eso lo hace la
        # validación local + el reintento). Exige que el prompt mencione «json» y
        # traiga un ejemplo de salida, cosa que este prompt ya hace.
        "response_format": {"type": "json_object"},
    }
    if max_tokens is not None:
        extra["max_tokens"] = max_tokens
    if esfuerzo is not None:
        extra["reasoning_effort"] = esfuerzo

    uso_total: dict[str, Any] = {}
    avisos: list[str] = []
    for intento in range(1, MAX_REINTENTOS_ESQUEMA + 1):
        try:
            respuesta = cliente.chat.completions.create(**extra)
        except Exception as exc:  # noqa: BLE001 - se clasifica en _describir_error
            return Respuesta(
                None, uso=uso_total, error=_describir_error(exc), modelo=modelo
            )

        texto = (respuesta.choices[0].message.content or "").strip()
        _acumular_uso(uso_total, respuesta.usage)
        base = {
            "uso": uso_total,
            "modelo": getattr(respuesta, "model", modelo),
            "temperatura_ignorada": temperatura is not None,
            "reintentos": intento - 1,
            "avisos_esquema": list(avisos),
        }

        # ⚠️ Un `finish_reason` de longitud significa que la generación se cortó
        # por el tope de tokens: el JSON llega truncado y el error de parseo que
        # sigue («Unterminated string») no explica la causa. Se detecta acá para
        # poder decir **qué** pasó y cómo arreglarlo.
        recortada = getattr(respuesta.choices[0], "finish_reason", None) == "length"

        try:
            datos = json.loads(texto)
        except json.JSONDecodeError as exc:
            # La doc de DeepSeek avisa que en JSON mode puede volver contenido
            # vacío de vez en cuando: se reintenta en vez de fallar al primero.
            # ⚠️ Del error de `json` solo se toma la **posición**, no el mensaje:
            # `json.JSONDecodeError` cita el fragmento de texto alrededor del
            # fallo, que es la lectura del comprobante y no debe quedar en el
            # registro ni en el log.
            if recortada:
                return Respuesta(
                    None,
                    **{**base, "avisos_esquema": list(avisos)},
                    error=(
                        "la respuesta se cortó por el tope de tokens de salida "
                        "(finish_reason=length) y el JSON quedó incompleto. "
                        f"El modelo generó {base['uso'].get('completion_tokens')} "
                        "tokens. No lo reintentes con el mismo tope: subilo con "
                        "--max-tokens o quitalo (por defecto el proveedor usa "
                        "64K con thinking activado)."
                    ),
                )
            problema = (
                f"la respuesta no era JSON válido "
                f"(carácter {exc.pos}: {exc.msg})"
            )
            avisos.append(f"intento {intento}: {problema}")
            if intento == MAX_REINTENTOS_ESQUEMA:
                return Respuesta(
                    None,
                    **{**base, "avisos_esquema": list(avisos)},
                    error=f"{problema} tras {intento} intento(s); "
                    f"la respuesta fue de {len(texto)} caracteres",
                )
            _pedir_correccion(messages, texto, problema)
            continue

        errores = _errores_de_esquema(validador, esquema, datos)
        if errores:
            # Antes de gastar una repregunta, se prueban las diferencias de
            # **forma** (p. ej. «Buena» vs «buena»): si con eso el JSON ya cumple,
            # no hace falta repreguntar y no se paga otro intento.
            notas: list[str] = []
            normalizado = normalizar_por_esquema(esquema, datos, notas)
            if notas and not _errores_de_esquema(validador, esquema, normalizado):
                avisos.extend(f"normalizado: {n}" for n in notas)
                base["avisos_esquema"] = list(avisos)
                return Respuesta(normalizado, **base)

            avisos.append(f"intento {intento}: {errores[0]}")
            if intento == MAX_REINTENTOS_ESQUEMA:
                return Respuesta(
                    None,
                    **{**base, "avisos_esquema": list(avisos)},
                    error=(
                        "el JSON no respeta el esquema del prompt tras "
                        f"{intento} intento(s): {errores[0]}"
                    ),
                )
            _pedir_correccion(messages, texto, "\n".join(errores[:5]))
            continue

        return Respuesta(datos, **base)

    # Inalcanzable: el bucle siempre retorna (el último intento no continúa).
    return Respuesta(None, uso=uso_total, error="sin respuesta", modelo=modelo)


def _describir_error(exc: Exception) -> str:
    """Mensaje de error legible, sin volcar trazas ni credenciales."""
    nombre = type(exc).__name__
    mensaje = str(exc).strip().splitlines()[0] if str(exc).strip() else ""
    if "authentication" in nombre.lower() or "401" in mensaje:
        return (
            f"{nombre}: credencial rechazada. Revisá DEEPSEEK_API_KEY "
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
    #: Precios por modelo (``{"deepseek-flash": (entrada, salida, caché), "*": (…)}``).
    precios: dict[str, Precios] = field(default_factory=dict)
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

    # El dry-run no llega hasta acá: `main` estima el costo y sale antes de
    # llamar a la API (así no se codifica base64 ni se gasta memoria al pedo).
    try:
        data_url, info = codificar_imagen(img, opciones.detalle)
    except OSError as exc:
        registro["error"] = f"no se pudo leer la imagen: {exc}"
        return registro
    registro["imagen"] = info

    if info["bytes"] > LIMITE_BYTES_IMAGEN:
        registro["error"] = (
            f"la imagen pesa {info['bytes'] / 1e6:.1f} MB y la API acepta hasta "
            f"{LIMITE_BYTES_IMAGEN / 1024 / 1024:.0f} MB por imagen "
            "(base64): reducíla con reducir_tokens.py"
        )
        return registro

    # Prompt efectivo del modo (misma función que usa el estimador, así el costo
    # simulado corresponde al real).
    sistema_efectivo, usuario = armar_prompt_efectivo(
        opciones.modo,
        sistema,
        user_template,
        datos_doc,
        incluir_ejemplo=opciones.incluir_ejemplo,
    )
    esquema = (
        esquema_validacion() if opciones.modo == "validar" else esquema_extraccion()
    )
    # El hash es del prompt **efectivo** (system adaptado + user armado): es lo
    # que permite auditar después con qué prompt se generó cada salida.
    registro["prompt_hash"] = hash_prompt(sistema_efectivo, usuario)

    respuesta = llamar_api(
        cliente,
        modelo=opciones.modelo,
        sistema=sistema_efectivo,
        usuario=usuario,
        data_url=data_url,
        esquema=esquema,
        temperatura=opciones.temperatura,
        detalle=opciones.detalle,
        max_tokens=opciones.max_tokens,
        esfuerzo=opciones.esfuerzo,
    )
    registro["uso"] = respuesta.uso
    registro["esquema_validado"] = _validador_jsonschema() is not None
    # Deja constancia de lo que el proveedor no acepta, en vez de callarlo.
    if respuesta.temperatura_ignorada:
        registro["nota_temperatura"] = (
            "DeepSeek no aplica temperature (thinking mode): el valor pedido se "
            "ignoró; el proveedor decide el muestreo."
        )
    if respuesta.reintentos:
        registro["reintentos_esquema"] = respuesta.reintentos
        registro["avisos_esquema"] = respuesta.avisos_esquema
    if not respuesta.ok:
        # El error viene con la lectura cruda del modelo recortada; se guarda
        # entera en el registro para poder diagnosticar el fallo (el gasto ya
        # está en `uso`).
        registro["error"] = respuesta.error
        return registro

    # Costo con los precios de ESTA corrida, persistido junto al uso: así el
    # reporte de gastos no tiene que adivinar precios históricos después.
    p_entrada, p_salida, p_cache = precio_de(opciones.modelo, opciones.precios)
    registro["precios_usd_1m"] = {
        "entrada": p_entrada,
        "salida": p_salida,
        "entrada_cache": p_cache,
        "origen": "cli" if opciones.precios or opciones.precio_entrada else "referencia",
    }
    registro["costo_usd"] = costo_de_tokens(
        respuesta.uso.get("prompt_tokens") or 0,
        respuesta.uso.get("completion_tokens") or 0,
        p_entrada,
        p_salida,
        cache_hit=respuesta.uso.get("prompt_cache_hit_tokens") or 0,
        precio_cache=p_cache,
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


def _raiz_espejado(
    rutas: Sequence[Path], explicita: Path | None, salida: Path | None = None
) -> tuple[Path, str]:
    """Raíz desde la cual se espeja el árbol, con su motivo (para declararlo).

    ⚠️ **La raíz no puede depender de la ruta que se pasa**, o el nivel de
    carpetas de la salida cambia entre corridas: procesar ``procesados`` escribía
    ``salida/2025-08/<hash>/…``, pero procesar ``procesados/2025-08`` escribía
    ``salida/<hash>/…``. Peor: la reanudación (que saltea lo ya hecho) no
    encontraba esos archivos y **se volvía a pagar** por documentos ya
    procesados. Por eso, si no hay ``--raiz``, se **sube** desde las rutas hasta
    la primera carpeta que no tenga un nombre de mes (``AAAA-MM``) — un nivel que
    no depende de desde dónde se invoque. ``--raiz`` manda siempre.

    Devuelve ``(raiz, motivo)``.
    """
    if explicita is not None:
        return explicita, "--raiz (explícita)"

    bases = [r if r.is_dir() else r.parent for r in rutas if r.exists()]
    if not bases:
        return Path.cwd(), "cwd (las rutas no existen)"

    raiz = bases[0] if len(bases) == 1 else _commonpath(bases)
    subidas: list[str] = []
    # Sube mientras el último nivel sea un mes (AAAA-MM), un hash de lote
    # (hexadecimal) o esté dentro de la salida (p. ej. se apuntó a la carpeta de
    # salida por error). Así la raíz queda en un nivel estable y no cambia según
    # qué subcarpeta se haya pasado.
    while raiz.parent != raiz:
        if salida is not None and _esta_dentro(raiz, salida) and raiz != salida:
            subidas.append(raiz.name)
            raiz = raiz.parent
            continue
        if _es_nivel_de_corpus(raiz.name):
            subidas.append(raiz.name)
            raiz = raiz.parent
            continue
        break

    if subidas:
        return raiz, f"ascendida desde {bases[0]} (salteando: {', '.join(subidas)})"
    return raiz, "derivada de las rutas"


def _es_nivel_de_corpus(nombre: str) -> bool:
    """True si el nombre es un nivel del corpus, no una raíz elegida.

    Dos formas: mes (``2025-08``, ``2025_8``) e identificador de lote —el hash
    hexadecimal de 8 caracteres que usa este corpus como carpeta por documento
    (``2D2C9343``)—. Una carpeta elegida a mano (``procesados``, ``lote-final``,
    ``mi_corpus``) no matchea ninguna, así que la subida se detiene ahí.
    """
    return bool(re.fullmatch(r"\d{4}[-_.]?\d{1,2}", nombre)) or bool(
        re.fullmatch(r"[0-9A-Fa-f]{8}", nombre)
    )


def estimar_costo_corrida(
    imagenes: Sequence[Path],
    sistema: str,
    user_template: str,
    opciones: Opciones,
) -> dict[str, Any]:
    """Estima **cuánto costaría** procesar estas imágenes, sin llamar a la API.

    Es lo que responde ``--dry-run``. La estimación se arma con lo que sí se sabe
    sin gastar:

    - **tokens de imagen**: **tope fijo por imagen** (1.024 tokens; DeepSeek
      redimensiona toda imagen a ~1300×1300 px antes de inferir), así que la
      resolución no cambia el costo. Ver :func:`tokens_imagen`.
    - **tokens de texto**: se construye el prompt **de verdad** (system + user +
      esquema) y se convierte con :data:`CHARS_POR_TOKEN_ESTIMADO`, medido contra
      el uso real de la API. Si la carpeta de salida ya tiene extracciones pagas,
      se **calibra con ese histórico** (prompt real menos tokens de imagen), que
      es más fiel que la fórmula;
    - **tokens de salida**: promedio del histórico, o
      :data:`COMPLETION_TOKENS_TIPICO`.

    Devuelve el detalle por documento y los totales; ``confianza`` dice de dónde
    salió el número (``historico``, ``formula`` o ``mixta``) para no presentar una
    estimación como si fuera una factura.
    """
    # Prompt efectivo (el mismo que se enviaría para la primera imagen), con la
    # MISMA función que usa `procesar`: si el estimador lo armara por su cuenta,
    # el costo simulado dejaría de corresponder al real (p. ej. no contaba el
    # cierre de system del modo extraer).
    sistema_efectivo, usuario = armar_prompt_efectivo(
        opciones.modo, sistema, user_template, None, incluir_ejemplo=opciones.incluir_ejemplo
    )
    esquema = esquema_validacion() if opciones.modo == "validar" else esquema_extraccion()
    chars = len(sistema_efectivo) + len(usuario) + len(json.dumps(esquema))
    tokens_texto_formula = round(chars / CHARS_POR_TOKEN_ESTIMADO)

    # Calibración con el histórico de la carpeta de salida, si alcanza.
    medidas = _tokens_de_texto_historicos(opciones.salida, opciones)
    if len(medidas) >= MIN_MUESTRAS_PARA_CALIBRAR:
        tokens_texto = round(sum(medidas) / len(medidas))
        confianza = "historico"
    else:
        tokens_texto = tokens_texto_formula
        confianza = "formula"

    salidas_historicas = _tokens_de_salida_historicos(opciones.salida, opciones)
    tokens_salida = (
        round(sum(salidas_historicas) / len(salidas_historicas))
        if salidas_historicas
        else COMPLETION_TOKENS_TIPICO
    )
    if salidas_historicas and confianza == "historico":
        confianza = "historico"
    elif salidas_historicas:
        confianza = "mixta"

    precio_entrada, precio_salida, precio_cache = precio_de(
        opciones.modelo, opciones.precios
    )

    detalle: list[dict[str, Any]] = []
    total_entrada = total_salida = 0
    total_costo = 0.0
    sin_precio = 0
    for img in imagenes:
        try:
            info = info_imagen(img, opciones.detalle)
        except OSError:
            continue
        tokens_img = info.get("tokens_estimados")
        # Sin dimensiones legibles no se puede estimar la imagen: se cuenta el
        # texto y se declara que falta la parte más pesada.
        entrada = (tokens_img or 0) + tokens_texto
        # Cache hit y miss cuestan distinto, y no se sabe de antemano cuánto va a
        # pegar el caché: se estima el caso **sin caché** (el más caro) y la
        # estimación del log se ajusta después, con lo que crezca el prefijo
        # cacheado (el system es el mismo en todo el lote).
        costo = costo_de_tokens(
            entrada,
            tokens_salida,
            precio_entrada,
            precio_salida,
            cache_hit=0,
            precio_cache=precio_cache,
        )
        if costo is None:
            sin_precio += 1
        else:
            total_costo += costo
        total_entrada += entrada
        total_salida += tokens_salida
        detalle.append(
            {
                "imagen": str(img),
                "tokens_imagen": tokens_img,
                "tokens_texto": tokens_texto,
                "tokens_entrada": entrada,
                "tokens_salida": tokens_salida,
                "costo_usd": costo,
            }
        )

    return {
        "archivos": len(detalle),
        "tokens_texto_estimados": tokens_texto,
        "tokens_texto_formula": tokens_texto_formula,
        "prompt_chars": chars,
        "tokens_imagen": TOKENS_MAX_IMAGEN,
        "tokens_salida_estimados": tokens_salida,
        "tokens_entrada_totales": total_entrada,
        "tokens_salida_totales": total_salida,
        "cache_hit_historicos": sum(
            (registro.get("uso") or {}).get("prompt_cache_hit_tokens") or 0
            for registro in _registros_validos(opciones.salida, opciones)
        ),
        "costo_usd_total": round(total_costo, 6) if total_costo else None,
        "costo_usd_promedio": (
            round(total_costo / (len(detalle) - sin_precio), 6)
            if detalle and len(detalle) > sin_precio
            else None
        ),
        "precio_entrada_usd_1m": precio_entrada,
        "precio_salida_usd_1m": precio_salida,
        "precio_entrada_cache_usd_1m": precio_cache,
        "sin_precio": sin_precio,
        "confianza": confianza,
        "muestras_historico": len(medidas),
        "detalle": detalle,
        "opciones": {
            "modo": opciones.modo,
            "modelo": opciones.modelo,
            "detalle_imagen": opciones.detalle,
            "prompt_con_ejemplo_de_salida": opciones.incluir_ejemplo,
            # Se declara porque el cálculo de DeepSeek es distinto al de OpenAI:
            # la resolución no cambia el costo de la imagen (tope fijo por imagen).
            "tokens_imagen_fijos": TOKENS_MAX_IMAGEN,
        },
    }


def _tokens_de_texto_historicos(
    salida: Path, opciones: Opciones
) -> list[int]:
    """Tokens de **texto** (prompt menos imagen) de las extracciones ya pagas.

    Sirve para calibrar la estimación con datos propios en vez de con una
    constante: el prompt del proyecto puede cambiar (otro esquema, otro prompt) y
    la fórmula quedaría vieja.
    """
    medidas: list[int] = []
    for registro in _registros_validos(salida, opciones):
        uso = registro.get("uso") or {}
        prompt = uso.get("prompt_tokens")
        if prompt is None:
            continue
        img = (registro.get("imagen") or {}).get("tokens_estimados") or 0
        texto = prompt - img
        if texto > 0:
            medidas.append(texto)
    return medidas


def _tokens_de_salida_historicos(salida: Path, opciones: Opciones) -> list[int]:
    """Tokens de salida (``completion``) de las extracciones ya pagas."""
    salidas: list[int] = []
    for registro in _registros_validos(salida, opciones):
        salida_tok = (registro.get("uso") or {}).get("completion_tokens")
        if salida_tok:
            salidas.append(salida_tok)
    return salidas


def _registros_validos(salida: Path, opciones: Opciones) -> list[dict]:
    """Registros pagos de la carpeta de salida, del mismo modelo y modo.

    Se filtra por modelo y modo porque los tokens no son comparables entre
    modelos (ni entre comparar y solo extraer): mezclarlos daría una calibración
    con un promedio que no corresponde a lo que se va a correr.
    """
    if not salida.is_dir():
        return []
    registros: list[dict] = []
    for archivo in sorted(salida.rglob("*.json")):
        if archivo.name in {"reporte.json", "gastos.json"}:
            continue
        try:
            registro = json.loads(archivo.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            continue
        if not isinstance(registro, dict) or registro.get("error"):
            continue
        if registro.get("modelo") != opciones.modelo:
            continue
        if registro.get("modo") != opciones.modo:
            continue
        registros.append(registro)
    return registros


def imprimir_estimacion(est: dict[str, Any], *, detalle: bool = False) -> None:
    """Muestra la estimación de costo de una corrida (lo que imprime ``--dry-run``)."""
    print("\n=== Estimación de costo (simulación: no se llamó a la API) ===")
    if not est["archivos"]:
        print("No hay imágenes que estimar.")
        return
    print(f"archivos          : {est['archivos']}")
    print(
        f"tokens por archivo: entrada ≈ {est['tokens_texto_estimados']:,} (texto+esquema) "
        f"+ {est['tokens_imagen']:,} de imagen (tope fijo de DeepSeek) | "
        f"salida ≈ {est['tokens_salida_estimados']:,}"
    )
    print(
        f"tokens totales    : entrada {est['tokens_entrada_totales']:,} | "
        f"salida {est['tokens_salida_totales']:,}"
    )
    if est.get("cache_hit_historicos"):
        print(
            f"caché de contexto : {est['cache_hit_historicos']:,} tokens ya se "
            "sirvieron de caché en corridas previas (se cobran mucho más barato)"
        )
    if est["costo_usd_total"] is not None:
        print(f"COSTO ESTIMADO    : US$ {est['costo_usd_total']:.4f}", end="")
        if est["costo_usd_promedio"] is not None:
            print(f"   (≈ US$ {est['costo_usd_promedio']:.4f} por comprobante)")
        else:
            print()
        print(
            f"precios usados    : US$ {_fmt_precio(est['precio_entrada_usd_1m'])}/1M "
            f"entrada, US$ {_fmt_precio(est['precio_salida_usd_1m'])}/1M salida, "
            f"US$ {_fmt_precio(est.get('precio_entrada_cache_usd_1m'))}/1M entrada-caché "
            f"({est['opciones']['modelo']})"
        )
        print(
            "postura del precio: PICO (01:00-04:00 y 06:00-10:00 UTC, L-V) y SIN "
            "caché: es el techo; off-peak cuesta la mitad"
        )
    else:
        print(
            "COSTO ESTIMADO    : — (no hay precio para el modelo "
            f"{est['opciones']['modelo']}; pasá --precios o --precio-entrada/--precio-salida)"
        )
    # De dónde salió el número: una estimación no es una factura.
    fuentes = {
        "historico": f"calibrada con {est['muestras_historico']} extracción(es) previa(s)",
        "formula": f"fórmula ({CHARS_POR_TOKEN_ESTIMADO} car/token; medido contra la API)",
        "mixta": "texto por fórmula y salida por histórico",
    }
    print(f"confianza         : {fuentes.get(est['confianza'], est['confianza'])}")
    if est["sin_precio"]:
        print(
            f"  ⚠ {est['sin_precio']} archivo(s) sin precio para su modelo; no "
            "entran en el total",
            file=sys.stderr,
        )
    if detalle:
        print("\n-- por archivo --")
        for d in est["detalle"]:
            costo = f"US$ {d['costo_usd']:.4f}" if d["costo_usd"] is not None else "—"
            img_tok = d["tokens_imagen"] if d["tokens_imagen"] is not None else "?"
            print(
                f"  {Path(d['imagen']).name[:44]:46} img={img_tok:>5} "
                f"texto={d['tokens_texto']:>5} total={d['tokens_entrada']:>5}  {costo}"
            )


#: Componentes de un prompt que se construyen de verdad para estimar.
def _commonpath(bases: Sequence[Path]) -> Path:
    """Ancestro común de varias bases (``cwd`` si no comparten ninguno)."""
    try:
        return Path(os.path.commonpath([str(b.resolve()) for b in bases]))
    except ValueError:  # rutas sin ancestro común (volúmenes distintos)
        return Path.cwd()


def _esta_dentro(hijo: Path, padre: Path) -> bool:
    """True si ``hijo`` está dentro de ``padre`` (evita espejar desde la salida)."""
    try:
        hijo.resolve().relative_to(padre.resolve())
        return True
    except (ValueError, OSError):
        return False


def _es_nivel_mes(nombre: str) -> bool:
    """True si el nombre de carpeta es un mes tipo ``2025-08`` / ``2025_8``."""
    return bool(re.fullmatch(r"\d{4}[-_.]?\d{1,2}", nombre))


def salida_de(img: Path, raiz: Path, salida: Path, sufijo: str) -> Path:
    """Dónde se guarda el resultado de ``img`` espejando desde ``raiz``.

    Función única para escribir y para buscar: si el cálculo se duplicara, la
    reanudación y la escritura podrían divergir (y se pagaría dos veces el mismo
    documento).
    """
    try:
        relativo = img.resolve().relative_to(raiz.resolve())
    except (ValueError, OSError):
        relativo = Path(img.name)
    return salida / relativo.with_suffix(f".{sufijo}.json")


def _salidas_en_otras_raices(
    salida: Path, raiz: Path, imagenes: Sequence[Path], sufijo: str
) -> list[Path]:
    """Salidas de estas imágenes que existen en OTRA ubicación de ``salida``.

    Detecta el caso que hizo pagar dos veces: la misma imagen ya procesada con
    otra raíz de espejado (p. ej. ``salida/2025-08/<hash>/x.json`` vs
    ``salida/<hash>/x.json``). Devuelve los archivos encontrados, como aviso.
    """
    if not salida.is_dir():
        return []
    # Índice por nombre de archivo: la ruta esperada de cada imagen.
    esperadas = {
        salida_de(img, raiz, salida, sufijo).resolve() for img in imagenes
    }
    encontradas: list[Path] = []
    for archivo in salida.rglob(f"*.{sufijo}.json"):
        try:
            if archivo.resolve() in esperadas:
                continue
        except OSError:
            continue
        encontradas.append(archivo)
    return encontradas


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
    cache_hit_tokens = cache_miss_tokens = 0
    tokens_imagen_estimados = 0
    reintentos = 0

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
        cache_hit_tokens += uso.get("prompt_cache_hit_tokens") or 0
        cache_miss_tokens += uso.get("prompt_cache_miss_tokens") or 0
        reintentos += r.get("reintentos_esquema") or 0
        tokens_imagen_estimados += (r.get("imagen") or {}).get("tokens_estimados") or 0

    entrada, salida, cache = precio_de(opciones.modelo, opciones.precios)
    costo = costo_de_tokens(
        prompt_tokens,
        completion_tokens,
        entrada,
        salida,
        cache_hit=cache_hit_tokens,
        precio_cache=cache,
    )

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
            "cache_hit": cache_hit_tokens,
            "cache_miss": cache_miss_tokens,
            "imagen_estimados": tokens_imagen_estimados,
            "costo_usd_estimado": costo,
        },
        "reintentos_esquema": reintentos,
        "opciones": {
            "modo": opciones.modo,
            "modelo": opciones.modelo,
            "detalle_imagen": opciones.detalle,
            # DeepSeek no aplica temperature (thinking mode): se declara tal cual.
            "temperatura": (
                "ignorada (DeepSeek thinking mode)"
                if opciones.temperatura is not None
                else None
            ),
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
    if t.get("cache_hit"):
        print(
            f"caché de contexto : {t['cache_hit']:,} entrada-caché | "
            f"{t.get('cache_miss', 0):,} entrada-normal (se cobran distinto)"
        )
    if t["imagen_estimados"]:
        print(f"tokens img (est.) : {t['imagen_estimados']:,}")
    if rep.get("reintentos_esquema"):
        print(
            f"  ⚠ reintentos     : {rep['reintentos_esquema']} repregunta(s) por "
            "forma del JSON (cada intento se paga)",
            file=sys.stderr,
        )
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

#: Precios de referencia (USD por 1M de tokens), como
#: ``modelo -> (entrada, salida, entrada_cache_hit)``.
#:
#: ⚠️ DeepSeek cobra distinto en **horario pico** (01:00-04:00 y 06:00-10:00 UTC,
#: de lunes a viernes; el resto es «off-peak» a **mitad de precio**). Acá se usa
#: la tarifa **pico**, que es la más cara, así la estimación no queda corta. Si
#: corrés fuera de esa ventana, pasá ``--precios`` con la mitad.
#:
#: El **caché de contexto** (``prompt_cache_hit_tokens``) tiene su propia tarifa,
#: dos órdenes por debajo de la de entrada: es lo que abarata el prompt repetido
#: en todo el lote, porque el prefijo (system + imagen) es idéntico.
PRECIOS_REFERENCIA: dict[str, tuple[float, float, float]] = {
    "deepseek-flash": (0.30, 1.20, 0.006),
    "deepseek-v4-pro": (1.32, 3.96, 0.044),
    # Alias legacy: la API los sigue aceptando y los sirve con el Flash actual.
    "deepseek-v4-flash": (0.30, 1.20, 0.006),
    "deepseek-v4-flash-vision-exp": (0.30, 1.20, 0.006),
}

#: Tupla de precios tal como la maneja el resto del script.
Precios = tuple[float | None, float | None, float | None]


def _clave_modelo(modelo: str, tabla: dict[str, Any]) -> str | None:
    """Busca el modelo en la tabla, tolerando el sufijo de fecha o versión.

    ``deepseek-flash-2026-08`` matchea la entrada ``deepseek-flash``; el match
    exacto gana sobre el prefijo.
    """
    if modelo in tabla:
        return modelo
    for clave in sorted(tabla, key=len, reverse=True):
        if clave != "*" and modelo.startswith(clave):
            return clave
    return "*" if "*" in tabla else None


def _normalizar_precios(fila: Sequence[float | None]) -> Precios:
    """Lleva una fila de precios a la tupla de 3 ``(entrada, salida, caché)``."""
    valores = list(fila) + [None] * (3 - len(fila))
    return valores[0], valores[1], valores[2]


def parsear_precios(texto: str) -> dict[str, Precios]:
    """Parsea ``--precios``: ``modelo=entrada/salida[/cache]`` separados por coma.

    Ejemplo: ``"deepseek-flash=0.3/1.2/0.006, *=1/3"``.
    ``*`` es el comodín para los modelos no listados. Cada precio puede ser
    ``-`` para dejarlo sin definir (el costo de esa parte queda en ``null``).
    """
    tabla: dict[str, Precios] = {}
    for trozo in texto.split(","):
        trozo = trozo.strip()
        if not trozo:
            continue
        if "=" not in trozo or "/" not in trozo:
            raise ValueError(
                f"--precios: se esperaba «modelo=entrada/salida[/cache]», llegó {trozo!r}"
            )
        modelo, _, valores = trozo.partition("=")
        partes = valores.split("/")
        if len(partes) > 3:
            raise ValueError(
                f"--precios: demasiados valores en {trozo!r} "
                "(se esperaba entrada/salida o entrada/salida/caché)"
            )

        def _num(valor: str) -> float | None:
            valor = valor.strip()
            if valor in {"-", "", "none", "null"}:
                return None
            numero = float(valor)
            if numero < 0:
                raise ValueError(f"--precios: precio negativo en {trozo!r}")
            return numero

        tabla[modelo.strip()] = _normalizar_precios([_num(p) for p in partes])
    if not tabla:
        raise ValueError("--precios quedó vacío")
    return tabla


def precio_de(
    modelo: str,
    precios: dict[str, Precios],
    *,
    por_defecto: bool = True,
) -> Precios:
    """Precios ``(entrada, salida, caché)`` del modelo, o ``(None, None, None)``."""
    clave = _clave_modelo(modelo, precios)
    if clave is not None:
        return precios[clave]
    if por_defecto:
        clave = _clave_modelo(modelo, PRECIOS_REFERENCIA)
        if clave is not None:
            return _normalizar_precios(PRECIOS_REFERENCIA[clave])
    return None, None, None


def _fmt_precio(valor: float | None) -> str:
    """Precio para los avisos: el número o ``—`` si no está definido."""
    return f"{valor:g}" if valor is not None else "—"


def costo_de_tokens(
    entrada_tokens: int,
    salida_tokens: int,
    precio_entrada: float | None,
    precio_salida: float | None,
    *,
    cache_hit: int = 0,
    precio_cache: float | None = None,
) -> float | None:
    """Costo en USD, o ``None`` si no hay ningún precio con el que calcularlo.

    Si sólo se conoce uno de los dos precios, se cobra el que se sabe y se
    **declara** que el otro no (el número es un piso, no el total).

    ``cache_hit`` son los ``prompt_cache_hit_tokens`` que reporta DeepSeek: esa
    porción de la entrada se cobra a ``precio_cache`` (la tarifa de caché, mucho
    más baja) y el resto a la tarifa de entrada. Si no se conoce la tarifa de
    caché, esa porción se cobra como entrada normal: mejor un número conservador
    que uno inventado.
    """
    if precio_entrada is None and precio_salida is None:
        return None
    cache_hit = max(0, min(cache_hit, entrada_tokens))
    entrada_normal = entrada_tokens - cache_hit
    tarifa_cache = precio_cache if precio_cache is not None else precio_entrada
    total = (
        entrada_normal * (precio_entrada or 0)
        + cache_hit * (tarifa_cache or 0)
        + salida_tokens * (precio_salida or 0)
    )
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

    Quedan afuera los ``--dry-run`` y el modo ``diff`` (no llaman a la API), y
    todo registro sin ``usage``: no se puede afirmar que gastó si el proveedor no
    lo reportó.

    ⚠️ Sí cuenta un documento con ``error``: en DeepSeek un fallo *después* de
    reintentar por forma del JSON consumió (y facturó) tokens. Y se cuenta **una
    sola vez por documento**, no una vez por intento: los tokens de todos los
    intentos ya vienen sumados en ``uso`` (la conversación es una sola llamada
    lógica con repreguntas), así que un apunte por intento multiplicaría el
    gasto con el **mismo** ``prompt_tokens``. El apunte se marca ``fallo: true``
    para que el reporte lo declare en vez de esconderlo dentro del total.
    """
    if registro.get("dry_run") or registro.get("fuente") == "diff_local":
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
    cache_hit = uso.get("prompt_cache_hit_tokens") or 0
    modelo = registro.get("modelo") or "desconocido"
    p_entrada, p_salida, p_cache = precio_de(modelo, opciones.precios)
    # Si el registro ya trae el costo (y no hay precios nuevos), se respeta.
    costo = registro.get("costo_usd")
    if costo is None:
        costo = costo_de_tokens(
            entrada,
            salida,
            p_entrada,
            p_salida,
            cache_hit=cache_hit,
            precio_cache=p_cache,
        )

    momento = _parsear_momento(registro.get("procesado_utc")) or ahora or datetime.now(
        timezone.utc
    )
    local = _a_zona(momento, opciones.tz)
    return {
        "fecha_hora": local.isoformat(timespec="seconds"),
        "fecha": local.date().isoformat(),
        "hora": local.strftime("%H:%M:%S"),
        "documento": registro.get("archivo_relativo") or registro.get("origen"),
        # Identificador estable (sin la raíz de espejado) para deduplicar.
        "documento_id": identificador_documento(registro),
        "origen": registro.get("origen"),
        "modo": registro.get("modo"),
        "fuente": registro.get("fuente"),
        "modelo": modelo,
        "detalle_imagen": (registro.get("imagen") or {}).get("detalle"),
        # Un fallo pagado se declara: el token se consumió igual.
        "fallo": bool(registro.get("error")),
        "error": registro.get("error"),
        "reintentos_esquema": registro.get("reintentos_esquema") or 0,
        "tokens_prompt": entrada,
        "tokens_prompt_cache_hit": cache_hit,
        "tokens_completion": salida,
        "tokens_total": (entrada + salida),
        "tokens_imagen_estimados": (registro.get("imagen") or {}).get(
            "tokens_estimados"
        ),
        "precio_entrada_usd_1m": p_entrada,
        "precio_salida_usd_1m": p_salida,
        "precio_entrada_cache_usd_1m": p_cache,
        "costo_usd": costo,
        "costo_confiable": p_entrada is not None or p_salida is not None,
        "fuente_costo": (
            "registro" if registro.get("costo_usd") is not None else "recalculado"
        ),
        "version_prompt": registro.get("version_prompt"),
        "prompt_hash": registro.get("prompt_hash"),
    }


def identificador_documento(registro: dict) -> str | None:
    """Identificador del documento, **independiente de la raíz de espejado**.

    ⚠️ No sirve ``archivo_relativo``: la misma imagen escrita desde dos raíces
    distintas produce relativos distintos (``2025-08/<hash>/x.jpg`` vs
    ``<hash>/x.jpg``), y entonces el reporte de gastos contaba **dos veces** el
    mismo documento. Se usa la ruta **resuelta del origen** (la imagen real en
    disco), que no cambia según la raíz elegida; si la ruta ya no resuelve
    (lote movido), se cae a la última parte de la ruta, que en este corpus es el
    UUID del documento.
    """
    ruta = registro.get("origen")
    if not ruta:
        return registro.get("archivo_relativo")
    p = Path(str(ruta))
    try:
        return str(p.resolve())
    except OSError:
        return p.name


def _clave_apunte(apunte: dict) -> tuple:
    """Clave de deduplicación de un apunte de gasto.

    **Qué es el mismo gasto**: el mismo documento, con el mismo modo y modelo,
    en la **misma corrida** (misma fecha/hora). Esto es intencional: reprocesar
    un documento (otro día, o con `--forzar`) es una llamada que **se pagó de
    nuevo** y debe contarse aparte — si se colapsara, el reporte escondería
    justamente el gasto que se quiere vigilar.

    **Qué NO es un gasto distinto**: el mismo trabajo escrito en dos lugares por
    cambiar la raíz de espejado. Eso se colapsa acá porque el ``documento_id``
    (la imagen real) y la fecha coinciden, aunque la salida esté en otra carpeta.
    """
    return (
        apunte.get("documento_id"),
        apunte.get("modo"),
        apunte.get("modelo"),
        apunte.get("fuente"),
        apunte.get("fecha_hora"),
    )


#: Entrada que registra el **apunte del fallo**. Un ``error`` se guarda en el
#: registro para poder auditarlo, pero no representa ninguna extracción.
APUNTE_DE_FALLO = {"error": "fallo de la llamada"}


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
        # Los reportes que guardan los apuntes ya calculados no se re-cuentan:
        # ya están representados por los registros de documento que los generaron.
        if isinstance(registro.get("apuntes"), list):
            descartados += 1
            continue
        apunte = apunte_de_registro(registro, opciones)
        if apunte is None:
            descartados += 1
            continue
        # Un apunte de fallo nunca se guarda: puede tener el diccionario de error
        # con la lectura cruda del modelo (que es justamente lo que se paga).
        apunte.pop("error", None)
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
    total = {
        "apuntes": 0,
        "tokens_prompt": 0,
        "tokens_prompt_cache_hit": 0,
        "tokens_completion": 0,
        "costo_usd": 0.0,
    }
    sin_precio = 0
    precio_incompleto = 0
    fallos_pagados = 0
    costo_fallos = 0.0

    def _acumular(destino: dict[str, dict[str, Any]], clave: str) -> dict[str, Any]:
        return destino.setdefault(
            clave,
            {"apuntes": 0, "tokens_total": 0, "costo_usd": 0.0, "costo_confiable": True},
        )

    for a in apuntes:
        total["apuntes"] += 1
        total["tokens_prompt"] += a["tokens_prompt"]
        total["tokens_prompt_cache_hit"] += a.get("tokens_prompt_cache_hit") or 0
        total["tokens_completion"] += a["tokens_completion"]
        if a["costo_usd"] is None:
            sin_precio += 1
        else:
            total["costo_usd"] += a["costo_usd"]
            if not a["costo_confiable"]:
                precio_incompleto += 1
        # Un fallo después de reintentar ya consumió tokens: es gasto real, pero
        # no una extracción: se cuenta aparte para poder declararlo.
        if a.get("fallo"):
            fallos_pagados += 1
            costo_fallos += a["costo_usd"] or 0.0

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
        "fallos_pagados": fallos_pagados,
        "costo_fallos_usd": round(costo_fallos, 6),
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
    print(
        f"tokens            : prompt {t['tokens_prompt']:,} "
        f"(de los cuales {t.get('tokens_prompt_cache_hit', 0):,} de caché) | "
        f"completion {t['tokens_completion']:,}"
    )
    print(f"costo total       : US$ {_formato_coste(t['costo_usd'])}")
    if rep.get("fallos_pagados"):
        print(
            f"  ⚠ FALLOS PAGADOS: {rep['fallos_pagados']} intento(s) que terminaron "
            f"en error pero **consumieron tokens** (US$ "
            f"{_formato_coste(rep.get('costo_fallos_usd'))}). Ya están dentro del "
            "costo total; se declaran porque no son extracciones.",
            file=sys.stderr,
        )
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
        "documento_id",
        "modo",
        "fuente",
        "modelo",
        "detalle_imagen",
        "fallo",
        "error",
        "reintentos_esquema",
        "tokens_prompt",
        "tokens_prompt_cache_hit",
        "tokens_completion",
        "tokens_total",
        "precio_entrada_usd_1m",
        "precio_salida_usd_1m",
        "precio_entrada_cache_usd_1m",
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
                for clave in (
                    "precio_entrada_usd_1m",
                    "precio_salida_usd_1m",
                    "precio_entrada_cache_usd_1m",
                ):
                    if fila.get(clave) is not None:
                        fila[clave] = f"{fila[clave]:.4f}".replace(".", decimal)
            escritor.writerow(fila)


# --------------------------------------------------------------------------- #
# CLI
# --------------------------------------------------------------------------- #


def construir_parser() -> argparse.ArgumentParser:
    """Parser de la CLI (``argparse`` de la stdlib, como el resto del repo)."""
    parser = argparse.ArgumentParser(
        prog="validar_comprobantes_deepseek",
        description=(
            "Valida/extrae campos de comprobantes con la API de DeepSeek (visión), "
            "según el prompt de validación de comprobantes de Mendel."
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=(
            "Ejemplos:\n"
            "  python scripts/validar_comprobantes_deepseek.py ../procesados \\\n"
            "      --modo extraer --limite 5 --detalle-log\n"
            "  python scripts/validar_comprobantes_deepseek.py ../procesados \\\n"
            "      --datos datos_mendel.json --workers 4 -o validaciones\n"
            "  python scripts/validar_comprobantes_deepseek.py ../procesados \\\n"
            "      --modo diff --datos datos_mendel.json\n"
            "  python scripts/validar_comprobantes_deepseek.py \\\n"
            "      --reporte-gastos gastos.json --csv-gastos gastos.csv\n"
            "  python scripts/validar_comprobantes_deepseek.py ../procesados \\\n"
            "      --modo extraer --dry-run --limite 500 --detalle-log\n"
            "\n"
            "`--dry-run` SIMULA: estima el costo y sale sin llamar a la API ni\n"
            "escribir archivos. El reporte de gastos se arma del histórico de la\n"
            "carpeta de salida (--salida), no sólo de la última corrida.\n"
            "\n"
            "La salida espeja el árbol desde `--raiz` (o desde el nivel que no\n"
            "sea un mes, p. ej. `2025-08`): la MISMA imagen escribe siempre el\n"
            "mismo archivo, sin importar con qué subcarpeta se invoque.\n"
            "\n"
            "DeepSeek no acepta `temperature` (thinking mode) ni JSON Schema\n"
            "estricto: el formato se controla con el ejemplo del prompt + JSON\n"
            "mode + validación local, y se reintenta con el error como feedback.\n"
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
        help=(
            "Carpeta de salida: un JSON por documento (default: %(default)s). "
            "Sin rutas, es la carpeta de la que se lee el reporte de gastos."
        ),
    )
    parser.add_argument(
        "--raiz",
        type=Path,
        help=(
            "Raíz desde la cual se espeja el árbol en la salida. Si se omite, se "
            "sube hasta el nivel que no sea un mes (procesar «procesados/2025-08» "
            "espeja desde «procesados»), así la MISMA imagen escribe siempre el "
            "MISMO archivo. Fijala cuando el corpus no tenga esa forma."
        ),
    )
    parser.add_argument(
        "--modelo",
        default=MODELO_POR_DEFECTO,
        help=(
            "Modelo (default: %(default)s). ⚠️ Solo `deepseek-flash` acepta "
            "imágenes; `deepseek-v4-pro` no las soporta."
        ),
    )
    parser.add_argument(
        "--detalle",
        choices=("low", "high", "auto"),
        default="high",
        help=(
            "Resolución con que la API mira la imagen (default: %(default)s). "
            "En DeepSeek «low» la baja a 512×512 pero **no** ahorra tokens: "
            "toda imagen se redimensiona a ~1300×1300 antes de inferir."
        ),
    )
    parser.add_argument(
        "--temperatura",
        default=None,
        help=(
            "⤫ SIN EFECTO en DeepSeek: thinking mode no admite temperature y el "
            "valor se ignora (se declara en el registro). Se acepta solo para "
            "no romper la paridad de banderas con el script de OpenAI; usá "
            "«none» (default) para no pedirla."
        ),
    )
    parser.add_argument(
        "--max-tokens",
        type=int,
        default=MAX_TOKENS_POR_DEFECTO,
        metavar="N",
        help=(
            "Tope de tokens de salida. Por defecto NO se manda: DeepSeek usa 8K "
            "sin thinking y 64K con thinking (el default acá). ⚠️ Un tope "
            "**menor** al que el modelo necesita **trunca el JSON** a la mitad."
        ),
    )
    parser.add_argument(
        "--esfuerzo",
        choices=("none", "low", "high", "max"),
        help=(
            "reasoning_effort de DeepSeek: «none» apaga el modo de pensamiento "
            "(default del proveedor: high). Valores fuera de la lista del "
            "proveedor (minimal/medium/xhigh) los mapea la API sola."
        ),
    )
    parser.add_argument(
        "--prompt-fiel",
        dest="incluir_ejemplo",
        action="store_true",
        default=True,
        help=(
            "Incluye el JSON de ejemplo de salida del template. **Default: sí** "
            "— DeepSeek no tiene esquema estricto y su JSON mode exige que el "
            "prompt traiga el ejemplo. Usá --prompt-minimo para quitarlo."
        ),
    )
    parser.add_argument(
        "--prompt-minimo",
        dest="incluir_ejemplo",
        action="store_false",
        help=(
            "Quita el JSON de ejemplo del template (experimento de ahorro de "
            "tokens). En DeepSeek es arriesgado: sin el ejemplo, el JSON mode "
            "devuelve con más frecuencia una forma distinta a la esperada."
        ),
    )
    parser.add_argument(
        "--api-key", help="Clave de API (default: variable DEEPSEEK_API_KEY)."
    )
    parser.add_argument(
        "--env",
        type=Path,
        default=Path(".env"),
        help="Archivo .env con DEEPSEEK_API_KEY (default: ./.env).",
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
            "SIMULACIÓN: estima cuánto costaría la corrida y sale sin llamar a "
            "la API ni escribir archivos. Con --detalle-log agrega el detalle "
            "por comprobante."
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
        "--precio-cache",
        type=float,
        metavar="USD_POR_1M",
        help=(
            "Precio por 1M de tokens de entrada servidos de **caché** "
            "(opcional; el default sale de la tabla de referencia)."
        ),
    )
    parser.add_argument(
        "--precios",
        metavar="M=E/S[/C][,M=E/S[/C]]",
        help=(
            "Precios por modelo, p. ej. «deepseek-flash=0.3/1.2/0.006,*=1/3». "
            "El 3º valor es el precio de caché; «-» deja un precio sin definir. "
            "Pisa la tabla de referencia."
        ),
    )
    parser.add_argument(
        "--base-url",
        default=BASE_URL_POR_DEFECTO,
        help=(
            "Endpoint OpenAI-compatible (default: %(default)s). Útil para "
            "apuntar a un proxy o a una cuenta compatible."
        ),
    )
    parser.add_argument(
        "--reporte-gastos",
        metavar="ARCHIVO.json",
        help=(
            "Escribe el REPORTE DE GASTOS acumulado de la carpeta de salida: "
            "totales por día, modelo y modo. Es independiente de --reporte. "
            "Con --dry-run, ese mismo archivo recibe la ESTIMACIÓN."
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
    if (
        args.precio_entrada is not None
        or args.precio_salida is not None
        or args.precio_cache is not None
    ):
        p_e, p_s, p_c = precio_de(args.modelo, precios)
        precios_efectivos[args.modelo] = (
            args.precio_entrada if args.precio_entrada is not None else p_e,
            args.precio_salida if args.precio_salida is not None else p_s,
            args.precio_cache if args.precio_cache is not None else p_c,
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
                "error: falta la clave de API. Definí DEEPSEEK_API_KEY (o un .env, "
                "o --api-key).",
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
        # El SDK de OpenAI habla con el endpoint compatible de DeepSeek.
        cliente = OpenAI(api_key=api_key, base_url=args.base_url)

    # DeepSeek no acepta temperature (thinking mode): si se pidió una, se avisa.
    if temperatura is not None:
        print(
            "⚠  --temperatura se ignora: DeepSeek (thinking mode) no la aplica. "
            "Se deja constancia en cada registro.",
            file=sys.stderr,
        )

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
    raiz, motivo_raiz = _raiz_espejado(rutas, args.raiz, opciones.salida)
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
        destino = salida_de(img, raiz, opciones.salida, sufijo)
        if not opciones.forzar and ya_procesado(destino):
            continue
        tareas.append((img, destino))

    print(
        f"modo             : {opciones.modo}\n"
        f"modelo           : {opciones.modelo} (detalle de imagen: {opciones.detalle})\n"
        f"raíz de espejado : {raiz}   [{motivo_raiz}]\n"
        f"salida           : {opciones.salida}\n"
        f"imágenes         : {len(imagenes)}"
        + (f" (pendientes: {len(tareas)})" if len(tareas) != len(imagenes) else "")
        + (f"\ndatos cargados   : {len(datos)} documento(s)" if datos else ""),
        file=sys.stderr,
    )

    if not tareas:
        print(
            "Nada pendiente: las salidas de todas las imágenes ya existen "
            "(usá --forzar para rehacerlas).",
            file=sys.stderr,
        )
        return 0

    # En simulación se estima el costo de lo que se iba a procesar y se sale sin
    # llamar a la API ni escribir nada.
    if opciones.dry_run:
        est = estimar_costo_corrida(
            [img for img, _ in tareas], sistema, user_template, opciones
        )
        imprimir_estimacion(est, detalle=args.detalle_log)
        if args.reporte_gastos:
            ruta = Path(args.reporte_gastos)
            ruta.parent.mkdir(parents=True, exist_ok=True)
            ruta.write_text(
                json.dumps(est, ensure_ascii=False, indent=2), encoding="utf-8"
            )
            print(f"\nestimación escrita: {ruta}")
        return 0

    # Aviso cuando la corrida va a pagar por documentos que quizá ya se pagaron
    # en otra estructura de carpetas (pasó por cambiar la ruta de entrada).
    if len(tareas) == len(imagenes) and len(imagenes) > 1:
        otras = _salidas_en_otras_raices(opciones.salida, raiz, imagenes, sufijo)
        if otras:
            print(
                f"⚠  {len(otras)} imagen(es) ya tienen salida en OTRA ubicación de "
                f"{opciones.salida} (p. ej. {otras[0]}). Se van a volver a procesar "
                "y pagar. Unificá con --raiz.",
                file=sys.stderr,
            )

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

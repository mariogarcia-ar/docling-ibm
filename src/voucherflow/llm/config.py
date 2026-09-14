"""Constantes compartidas del laboratorio de LLM externos.

Lo que **no** tiene dueño natural en otro módulo. Cada constante acá declara su
motivo en el comentario de al lado: un número sin explicación se "corrige" sin
saber qué rompe.

⚠️ Las que son capacidades de un proveedor (tokens de imagen, esfuerzos, si
impone el esquema) viven en ``proveedores.py``, no acá: acá queda lo que es común
a todos.
"""

from __future__ import annotations

EXTENSIONES_IMAGEN = frozenset({".jpg", ".jpeg", ".png", ".webp", ".gif"})

MIME_POR_EXTENSION = {
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
    ".png": "image/png",
    ".webp": "image/webp",
    ".gif": "image/gif",
}

MODELO_POR_DEFECTO = "deepseek-flash"

BASE_URL_POR_DEFECTO = "https://api.deepseek.com"

MAX_REINTENTOS_ESQUEMA = 3

PIXELES_OBJETIVO_IMAGEN = 1300 * 1300

TOKENS_MAX_IMAGEN = 1024

PIXELES_MINIMOS_IMAGEN = 544 * 544

LIMITE_BYTES_IMAGEN = 32 * 1024 * 1024

VERSION_PROMPT = "mendel-validacion@1"

COMPLETION_TOKENS_TIPICO = 240

MIN_MUESTRAS_PARA_CALIBRAR = 3

# --- Estimación de tokens (sin llamar a la API) ---

#: Caracteres por token, para estimar el texto del prompt sin llamar a la API.
#: ⚠️ Medido sobre ``gpt-4o`` (10.919 chars = 2.786 tokens en 10 extracciones
#: reales). Es el tokenizador de **otro** modelo: sirve como arranque, y con
#: suficientes muestras del mismo modelo el histórico lo reemplaza.
CHARS_POR_TOKEN_ESTIMADO = 3.92

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

#: ⚠️ `BASE_URL_POR_DEFECTO`, `PIXELES_OBJETIVO_IMAGEN` y
#: `PIXELES_MINIMOS_IMAGEN` se eliminaron: la URL base y los topes de píxeles
#: son **capacidades de un proveedor** y viven en `proveedores.py` (``base_url``
#: y ``tokens_por_imagen``), como pide el encabezado de este módulo. Tenerlos acá
#: era una segunda fuente de verdad que nadie leía.

MAX_REINTENTOS_ESQUEMA = 3

TOKENS_MAX_IMAGEN = 1024

#: ⚠️ Default del límite de payload inline. Es una **capacidad del proveedor** y
#: cada uno lo pisa en `proveedores.py` (DeepSeek 32 MiB, Gemini 20 MB, OpenAI
#: 512 MB); acá queda el valor más restrictivo de los tres, para que un proveedor
#: nuevo herede el criterio conservador en vez de ninguno.
LIMITE_BYTES_IMAGEN = 32 * 1024 * 1024

#: Cuánto crece un archivo al codificarlo en base64 (4 caracteres por cada 3
#: bytes). Se usa para comparar el **payload** contra el límite del proveedor: el
#: límite es sobre lo que viaja, no sobre el archivo en disco.
FACTOR_BASE64 = 4 / 3

VERSION_PROMPT = "mendel-validacion@1"

#: Tokens de salida para estimar **cuando todavía no hay histórico** (el primer
#: dry-run sobre una carpeta vacía). Con histórico, la media medida lo reemplaza.
#:
#: ⚠️ **Estaba en 240 y era 12,5x corto.** Medido sobre el lote real de 95
#: comprobantes con thinking activo: **media 3.008 · mediana 2.180** tokens de
#: salida. El modo thinking emite tokens de razonamiento invisibles que se
#: facturan como ``completion_tokens``, así que un default pensado para una
#: respuesta corta **subestima el costo** — y la salida es el **91,8 %** del
#: gasto, de modo que el error no es cosmético: es la mayor parte del número.
#:
#: Se usa la **media** (3.008) y no la mediana: el total es ``n × salida_media``,
#: así que la media reproduce la suma real y la mediana la subestimaría 27 %
#: (medido). La cola es larga (min 331 · max 17.961), y proyectar con la mediana
#: en un lote de N documentos se queda corto justo cuando el lote es grande.
COMPLETION_TOKENS_TIPICO = 3000

MIN_MUESTRAS_PARA_CALIBRAR = 3

# --- Estimación de tokens (sin llamar a la API) ---

#: Caracteres por token, para estimar el texto del prompt sin llamar a la API.
#:
#: ⚠️ **Medido sobre `gpt-4o`** (10.919 chars = 2.786 tokens en 10 extracciones
#: reales). Es el tokenizador de **otro** modelo: sirve como arranque, y con
#: suficientes muestras del mismo modelo el histórico lo reemplaza.
#:
#: **Por qué no se bajó a 4,45 (el valor medido en DeepSeek)**: cuando hay
#: histórico (el caso normal) este número **no se usa** — la calibración lo pisa.
#: Y el efecto sobre el total es despreciable: la entrada es 7,7 % del costo del
#: lote real, así que 3,92 vs 4,45 mueve el total **0,005 %**. Bajarlo sería
#: sobre-ajustar a un proveedor y romper la honestidad de la anotación (dice de
#: dónde salió el número). Se deja como arranque declarado.
CHARS_POR_TOKEN_ESTIMADO = 3.92

"""Costos: precios de referencia, tokens y cálculo del gasto de una corrida.

Vive en la librería porque es lo que hace **comparable** el ajuste de un prompt
entre proveedores: sin un costo medido con el mismo criterio, elegir un modelo
termina siendo una opinión.

Tres decisiones que el módulo sostiene:

1. **No se inventa un precio.** Si un modelo no está en la tabla, el costo queda
   en ``None`` y el reporte lo declara. Un ``0`` silencioso se lee como «gratis»
   cuando en realidad es «no sé».
2. **Un precio a medias es un piso, no un total.** Si solo se conoce el precio
   de entrada, se cobra esa parte y se declara que la otra falta.
3. **Los precios son datos, no constantes enterradas**: la tabla es editable y
   cualquier valor que entre por la línea de comandos la pisa.

Lo que **no** hace este módulo, a propósito: hablar con ningún proveedor. Es
aritmética pura, así que se testea sin red.
"""

from __future__ import annotations

from typing import Any

from .config import (
    CHARS_POR_TOKEN_ESTIMADO,
    COMPLETION_TOKENS_TIPICO,
)

#: Precios de referencia (USD por 1M de tokens: entrada, salida).
#:
#: ⚠️ Los precios **cambian** y dependen del modelo y de la cuenta. Esta tabla es
#: un arranque editable; el número que manda es el del proveedor. Se puede pisar
#: por modelo (``--precios``) o para toda la corrida (``--precio-entrada`` /
#: ``--precio-salida``).
PRECIOS_REFERENCIA: dict[str, tuple[float, float]] = {
    # OpenAI
    "gpt-4o": (2.50, 10.00),
    "gpt-4o-mini": (0.15, 0.60),
    "gpt-4.1": (2.00, 8.00),
    "gpt-4.1-mini": (0.40, 1.60),
    "gpt-4.1-nano": (0.10, 0.40),
    # DeepSeek (tarifa pico; off-peak es la mitad). El tercer valor es el
    # precio de la entrada cacheada, que no entra en esta tupla: los precios de
    # caché se pasan aparte (``precio_cache``) porque solo algunos proveedores
    # los exponen.
    "deepseek-flash": (0.30, 1.20),
    "deepseek-v4-pro": (1.32, 3.96),
    # Gemini
    "gemini-2.5-flash": (0.30, 2.50),
    "gemini-2.5-pro": (1.25, 10.00),
    "gemini-2.0-flash": (0.10, 0.40),
}

#: Precio de la entrada **cacheada** (USD/1M), por modelo. Solo los proveedores
#: que exponen el dato lo tienen: leer un prompt cacheado es más barato que
#: mandarlo entero, y contarlo al precio lleno inflaría el gasto.
PRECIOS_CACHE: dict[str, float] = {
    "deepseek-flash": 0.006,
    "deepseek-v4-pro": 0.044,
}




#: Cómo se obtuvo una estimación. Se declara en el reporte: una estimación nunca
#: se presenta como una factura.
CONFIANZA_FORMULA = "formula"
CONFIANZA_HISTORICO = "historico"
CONFIANZA_MIXTA = "mixta"


def clave_modelo(modelo: str, tabla: dict[str, Any]) -> str | None:
    """Busca el modelo en la tabla tolerando el sufijo de fecha.

    ``gpt-4o-2026-08`` matchea la entrada ``gpt-4o`` (la versión con fecha usa
    el mismo precio que la base). El match exacto gana; ``*`` es el comodín.
    """
    if modelo in tabla:
        return modelo
    for clave in sorted(tabla, key=len, reverse=True):
        if clave != "*" and modelo.startswith(clave):
            return clave
    return "*" if "*" in tabla else None


def parsear_precios(texto: str) -> dict[str, tuple[float | None, float | None]]:
    """Parsea ``--precios``: ``modelo=entrada/salida`` separados por coma.

    Ejemplo: ``"gpt-4o=2.5/10, gemini-2.5-flash=0.3/2.5, *=1/3"``.
    ``*`` es el comodín de los modelos no listados. Un precio puede ser ``-``
    para dejarlo **sin definir** (esa parte del costo queda en ``None``, no en
    cero).
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
    clave = clave_modelo(modelo, precios)
    if clave is not None:
        return precios[clave]
    if por_defecto:
        clave = clave_modelo(modelo, PRECIOS_REFERENCIA)
        if clave is not None:
            return PRECIOS_REFERENCIA[clave]
    return None, None


def precio_cache_de(modelo: str, precios_cache: dict[str, float] | None = None) -> float | None:
    """Precio de la entrada cacheada del modelo, si el proveedor lo expone."""
    tabla = PRECIOS_CACHE if precios_cache is None else precios_cache
    clave = clave_modelo(modelo, tabla)
    return tabla[clave] if clave is not None else None


def costo_de_tokens(
    entrada_tokens: int,
    salida_tokens: int,
    precio_entrada: float | None,
    precio_salida: float | None,
    *,
    cache_hit_tokens: int = 0,
    precio_cache: float | None = None,
) -> float | None:
    """Costo en USD, o ``None`` si no hay ningún precio con el que calcularlo.

    Si solo se conoce uno de los dos precios, se cobra el que se sabe y el
    llamador **declara** que el otro falta: el número es un piso, no el total.

    ``cache_hit_tokens`` es la parte de la entrada que el proveedor sirvió desde
    su caché. Se cobra a ``precio_cache`` (más barato) en vez de al precio lleno:
    contarla al precio lleno inflaría el gasto de una corrida con prompts
    repetidos, que es justo el caso de procesar un corpus.
    """
    if precio_entrada is None and precio_salida is None and precio_cache is None:
        return None
    if cache_hit_tokens and precio_cache is not None:
        # La parte cacheada se descuenta de la entrada y se cobra aparte.
        entrada_tokens = max(0, entrada_tokens - cache_hit_tokens)
        total = cache_hit_tokens * precio_cache
    else:
        total = 0.0
    total += entrada_tokens * (precio_entrada or 0) + salida_tokens * (precio_salida or 0)
    return round(total / 1e6, 6)


def formatear_precio(valor: float | None) -> str:
    """Precio legible para un reporte (``—`` cuando no se conoce)."""
    return "—" if valor is None else f"{valor:g}"


def costo_legible(costo: float | None) -> str:
    """Costo legible: ``—`` si no se pudo calcular.

    No devuelve ``US$ 0.0000`` cuando el costo es ``None``: un cero se lee como
    «salió gratis» y el ``None`` significa «no sé cuánto salió».
    """
    return "—" if costo is None else f"US$ {costo:.4f}"


def estimar_tokens_de_texto(caracteres: int) -> int:
    """Estima tokens de un texto con la proporción medida (arranque)."""
    return max(1, round(caracteres / CHARS_POR_TOKEN_ESTIMADO))


def estimar_costo(
    *,
    caracteres_prompt: int,
    imagenes: int,
    tokens_por_imagen: int,
    salida_tokens: int = COMPLETION_TOKENS_TIPICO,
    precio_entrada: float | None,
    precio_salida: float | None,
    confianza: str = CONFIANZA_FORMULA,
) -> dict[str, Any]:
    """Estima el costo de una corrida **antes** de gastarla.

    Es la pieza que permite decidir con el presupuesto a la vista: el prompt real
    + los tokens de imagen + una salida típica, contra el precio del modelo.

    La ``confianza`` declara de dónde salió la estimación (``formula``,
    ``historico`` o ``mixta``): el llamador que calibró con el histórico lo dice
    acá, y el reporte lo muestra. Una estimación presentada como factura es peor
    que no tener estimación.
    """
    tokens_texto = estimar_tokens_de_texto(caracteres_prompt)
    entrada = tokens_texto + imagenes * tokens_por_imagen
    total = costo_de_tokens(entrada, salida_tokens, precio_entrada, precio_salida)
    return {
        "tokens_entrada_estimados": entrada,
        "tokens_texto_estimados": tokens_texto,
        "tokens_imagen_estimados": imagenes * tokens_por_imagen,
        "tokens_salida_estimados": salida_tokens,
        "costo_estimado_usd": total,
        "confianza": confianza,
        "precio_entrada": precio_entrada,
        "precio_salida": precio_salida,
        "calculable": total is not None,
    }

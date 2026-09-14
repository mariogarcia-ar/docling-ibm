"""Adaptadores de proveedor: la traducción, no la lógica.

Cada adaptador hace dos cosas y nada más: **armar los parámetros** de la llamada
según lo que su proveedor acepta, y **normalizar la respuesta**. El recorrido del
corpus, la reanudación, el costo y el reporte son comunes y viven en el núcleo.

Los tres hablan el protocolo de Chat Completions de OpenAI. Gemini entra por su
capa de compatibilidad, así que **no hace falta una dependencia nueva** — el
mismo SDK ``openai`` que ya usaban los scripts sirve para los tres.

Lo que cambia entre proveedores está en sus :class:`~voucherflow.llm.protocolo.
Capacidades`, y se declara en vez de esconderse en un ``if``. La consecuencia
práctica: cuando un proveedor no impone el esquema, el núcleo **lo sabe** y
valida localmente; cuando ignora la temperatura, **lo sabe** y lo registra.
"""

from __future__ import annotations

from typing import Any

from .protocolo import (
    CLAVE_CACHE_HIT,
    CLAVE_CACHE_MISS,
    ESTRATEGIA_MOSAICOS,
    ESTRATEGIA_TOPE_FIJO,
    Capacidades,
)

#: Nombre del parámetro del SDK para el techo de tokens de salida.
CAMPO_MAX_TOKENS = "max_completion_tokens"


def _mensajes(sistema: str, usuario: str, data_url: str, detalle: str | None) -> list[dict[str, Any]]:
    """Mensajes de Chat Completions con la imagen adjunta.

    El ``detail`` solo se agrega si el proveedor lo acepta: mandarlo a quien no
    lo soporta devuelve un 400 que no dice cuál es el campo de más.
    """
    imagen: dict[str, Any] = {"url": data_url}
    if detalle is not None:
        imagen["detail"] = detalle
    return [
        {"role": "system", "content": sistema},
        {
            "role": "user",
            "content": [
                {"type": "text", "text": usuario},
                {"type": "image_url", "image_url": imagen},
            ],
        },
    ]


def _uso_normalizado(uso: Any, *, con_cache: bool) -> dict[str, Any]:
    """Tokens de la respuesta, con los de caché cuando el proveedor los expone.

    Se leen con ``getattr`` porque los SDK no devuelven exactamente el mismo
    objeto. Un campo ausente queda en ``None``, no en cero: no es lo mismo «no
    hubo tokens de caché» que «el proveedor no me dijo».

    ⚠️ Las claves de caché son las del **SDK** (:data:`CLAVE_CACHE_HIT`), no
    nombres propios: el uso lo consume ``corrida.py``, que lee el campo tal como
    lo nombra el proveedor. Usar un nombre propio acá fue un bug real —el
    descuento de la caché dejó de aplicarse en silencio— y los nombres distintos
    en cada punta no lo delataban, porque un ``dict`` no tiene forma.
    """
    if uso is None:
        return {"prompt_tokens": None, "completion_tokens": None, "total_tokens": None}
    datos = {
        "prompt_tokens": getattr(uso, "prompt_tokens", None),
        "completion_tokens": getattr(uso, "completion_tokens", None),
        "total_tokens": getattr(uso, "total_tokens", None),
    }
    if con_cache:
        datos[CLAVE_CACHE_HIT] = getattr(uso, CLAVE_CACHE_HIT, None)
        datos[CLAVE_CACHE_MISS] = getattr(uso, CLAVE_CACHE_MISS, None)
    return datos


class _AdaptadorOpenAI:
    """Base de los tres: comparten la forma de la llamada.

    Que exista una base **no** significa que los proveedores sean iguales —
    significa que su protocolo lo es. Las diferencias van en :meth:`capacidades`
    y en los pocos ganchos que cada uno sobrescribe.
    """

    capacidades: Capacidades

    def parametros_de_llamada(
        self,
        *,
        modelo: str,
        sistema: str,
        usuario: str,
        data_url: str,
        esquema: dict[str, Any],
        nombre_esquema: str,
        temperatura: float | None = None,
        detalle: str | None = None,
        max_tokens: int | None = None,
        esfuerzo: str | None = None,
    ) -> dict[str, Any]:
        """Arma los ``kwargs`` de ``chat.completions.create``.

        Se omiten los parámetros que el proveedor no soporta, en vez de mandarlos
        y esperar que los ignore: un parámetro ignorado en silencio hace creer que
        se configuró algo.
        """
        cap = self.capacidades
        extra: dict[str, Any] = {
            "model": modelo,
            "messages": _mensajes(
                sistema, usuario, data_url, detalle if cap.acepta_detalle else None
            ),
            "response_format": self._formato_de_respuesta(esquema, nombre_esquema),
        }
        if temperatura is not None and cap.temperatura_efectiva:
            extra["temperature"] = temperatura
        if max_tokens is not None and cap.acepta_max_tokens:
            extra[CAMPO_MAX_TOKENS] = max_tokens
        if esfuerzo is not None and cap.esfuerzos:
            if esfuerzo not in cap.esfuerzos:
                raise ValueError(
                    f"{cap.nombre}: «{esfuerzo}» no es un esfuerzo válido "
                    f"(válidos: {', '.join(cap.esfuerzos)})"
                )
            extra["reasoning_effort"] = esfuerzo
        return extra

    def _formato_de_respuesta(self, esquema: dict[str, Any], nombre: str) -> dict[str, Any]:
        """El ``response_format`` que el proveedor entiende."""
        raise NotImplementedError

    def leer_respuesta(self, respuesta: Any) -> dict[str, Any]:
        """Normaliza la respuesta del SDK a ``{contenido, uso, error}``."""
        elecciones = getattr(respuesta, "choices", None) or []
        if not elecciones:
            return {"contenido": None, "uso": {}, "error": "la respuesta no trajo opciones"}
        mensaje = getattr(elecciones[0], "message", None)
        contenido = (getattr(mensaje, "content", None) or "").strip()
        fin = getattr(elecciones[0], "finish_reason", None)
        uso = _uso_normalizado(
            getattr(respuesta, "usage", None), con_cache=self.capacidades.expone_cache
        )
        if fin == "length":
            # El techo de tokens cortó la respuesta: es la causa más común de un
            # JSON incompleto, y sin decirlo el error se busca en el lugar equivocado.
            return {
                "contenido": contenido,
                "uso": uso,
                "error": (
                    "la respuesta se cortó por el límite de tokens "
                    f"({CAMPO_MAX_TOKENS}); subí el techo o quitalo"
                ),
                "truncado": True,
            }
        return {"contenido": contenido, "uso": uso, "error": None}


class AdaptadorOpenAI(_AdaptadorOpenAI):
    """OpenAI: impone el esquema en el servidor y respeta la temperatura."""

    capacidades = Capacidades(
        nombre="openai",
        base_url=None,
        variable_api_key="OPENAI_API_KEY",
        modelo_por_defecto="gpt-4o",
        esquema_estricto=True,
        temperatura_efectiva=True,
        esfuerzos=(),
        # OpenAI cobra por mosaicos de 512: la resolución SÍ cambia el costo
        # (de 255 a 1105+ tokens), y `detail=low` baja a 85 planos.
        estrategia_imagen=ESTRATEGIA_MOSAICOS,
        expone_cache=False,
        acepta_detalle=True,
        acepta_max_tokens=True,
    )

    def _formato_de_respuesta(self, esquema: dict[str, Any], nombre: str) -> dict[str, Any]:
        # `strict: True` es lo que hace que el servidor **imponga** la forma: el
        # parseo de la respuesta no puede fallar por un texto suelto alrededor.
        return {
            "type": "json_schema",
            "json_schema": {"name": nombre, "strict": True, "schema": esquema},
        }


class AdaptadorDeepSeek(_AdaptadorOpenAI):
    """DeepSeek: mismo ``base_url`` cambiado, sin esquema estricto.

    Dos diferencias que obligan a compensar y que el núcleo consulta:

    * **No impone el esquema.** Solo acepta ``json_object``, así que hay que
      validar localmente contra el esquema y reintentar pasándole el error.
    * **El modo de razonamiento ignora ``temperature``.** Mandarla haría creer que
      el ajuste del prompt influyó cuando no cambió nada.
    """

    capacidades = Capacidades(
        nombre="deepseek",
        base_url="https://api.deepseek.com",
        variable_api_key="DEEPSEEK_API_KEY",
        modelo_por_defecto="deepseek-flash",
        esquema_estricto=False,
        temperatura_efectiva=False,  # el thinking mode la ignora
        esfuerzos=("none", "low", "high", "max"),
        # Tope fijo: DeepSeek redimensiona toda imagen a ~1300×1300 y **agranda**
        # las chicas, así que el costo no depende del tamaño original.
        estrategia_imagen=ESTRATEGIA_TOPE_FIJO,
        tokens_por_imagen=1024,
        expone_cache=True,  # prompt_cache_hit_tokens
        acepta_detalle=False,  # `detail` no cambia el costo
        acepta_max_tokens=True,
        notas=(
            "no impone el esquema: el núcleo valida localmente y reintenta con el error",
            "la temperatura se ignora en el modo de razonamiento y se declara",
            "la entrada cacheada se cobra a su tarifa (el proveedor la expone)",
        ),
    )

    def _formato_de_respuesta(self, esquema: dict[str, Any], nombre: str) -> dict[str, Any]:
        # No hay `strict`: el formato se sostiene con el ejemplo del prompt, la
        # validación local y el reintento. El esquema viaja igual para que el
        # modelo tenga la forma a la vista.
        return {"type": "json_object"}


class AdaptadorGemini(_AdaptadorOpenAI):
    """Gemini por su capa de compatibilidad con OpenAI.

    Entra por el **mismo SDK** que los otros dos: la URL base apunta al endpoint
    compatible de Google, así que no hace falta la librería de Gemini ni una
    dependencia nueva.

    Acepta un subconjunto de JSON Schema en ``response_format`` (tipos simples,
    ``title`` y ``description``), así que el esquema se manda declarado y el
    núcleo **igualmente** valida localmente: la cobertura del subconjunto no está
    garantizada para esquemas anidados.
    """

    capacidades = Capacidades(
        nombre="gemini",
        base_url="https://generativelanguage.googleapis.com/v1beta/openai/",
        variable_api_key="GEMINI_API_KEY",
        modelo_por_defecto="gemini-2.5-flash",
        esquema_estricto=False,
        temperatura_efectiva=True,
        esfuerzos=("none", "minimal", "low", "medium", "high"),
        # Gemini no es OpenAI y su capa compatible **no** documenta la fórmula de
        # mosaicos ni acepta `detail`: se declara el tope fijo para no extrapolar
        # una fórmula que podría no aplicar. Si se mide lo contrario, se cambia acá.
        estrategia_imagen=ESTRATEGIA_TOPE_FIJO,
        tokens_por_imagen=1024,
        expone_cache=False,
        acepta_detalle=False,
        acepta_max_tokens=True,
        notas=(
            "compatibilidad OpenAI: mismo SDK, sin dependencia nueva",
            "acepta un subconjunto de JSON Schema; el núcleo valida localmente",
        ),
    )

    def _formato_de_respuesta(self, esquema: dict[str, Any], nombre: str) -> dict[str, Any]:
        # La capa compatible acepta `json_schema` pero sin `strict`.
        return {
            "type": "json_schema",
            "json_schema": {"name": nombre, "schema": esquema},
        }


#: Catálogo de proveedores disponibles, por nombre.
PROVEEDORES: dict[str, type[_AdaptadorOpenAI]] = {
    "openai": AdaptadorOpenAI,
    "deepseek": AdaptadorDeepSeek,
    "gemini": AdaptadorGemini,
}

#: Proveedor que se usa cuando no se indica ninguno (una sola fuente de verdad:
#: lo consumen el CLI y la resolución de la credencial).
PROVEEDOR_POR_DEFECTO = "deepseek"


def proveedor_por_nombre(nombre: str) -> _AdaptadorOpenAI:
    """Instancia un proveedor por su nombre, con un error que lista los válidos."""
    try:
        return PROVEEDORES[nombre]()
    except KeyError:
        validos = ", ".join(sorted(PROVEEDORES))
        raise ValueError(
            f"proveedor desconocido: {nombre!r} (válidos: {validos})"
        ) from None


def describir_proveedores() -> list[dict[str, Any]]:
    """Ficha de cada proveedor, para `--listar-proveedores` y la documentación."""
    return [p().capacidades.como_diccionario() for p in PROVEEDORES.values()]

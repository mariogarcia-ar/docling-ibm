"""Validación local contra el esquema, para los proveedores que no lo imponen.

Cuando el servidor **impone** el esquema (OpenAI con `strict`), el formato está
garantizado y no hay nada que validar. Cuando no lo hace —DeepSeek solo acepta
`json_object`, y la capa compatible de Gemini acepta un subconjunto de JSON
Schema— la forma depende del prompt, y un modelo puede devolver algo que el
consumidor no espera.

La respuesta a eso no es confiar ni fallar: es **validar localmente y reintentar
pasándole el error al modelo como feedback**. Ese reintento se paga, así que se
justifica solo si el error es de forma (enum con mayúsculas distintas, un tipo
cambiado) y no de contenido.

Dos cuidados que hacen a la diferencia entre reintentar bien y quemar tokens:

* :func:`normalizar_por_esquema` **evita** el reintento cuando la diferencia es
  trivial (un `enum` en otra caja). Un reintento por «Buena» vs «buena» cuesta
  una llamada entera.
* ⚠️ :func:`_errores_de_esquema` nombra el problema por su **tipo** y no cita el
  valor recibido: el mensaje de `jsonschema` incluye la lectura del comprobante,
  y ese texto viaja al registro y al log. No se filtra la lectura en un error.

`jsonschema` es opcional: sin él, la validación local no corre y se **declara**
(``esquema_validado: false``), en vez de dar por bueno lo que no se comprobó.
"""

from __future__ import annotations

from typing import Any, Sequence

from .evaluador import _norm

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

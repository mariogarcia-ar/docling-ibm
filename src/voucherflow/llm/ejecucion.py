"""Una llamada al modelo: la parte que no cambia entre proveedores.

Acá vive el ciclo completo —armar el prompt, mandar la imagen, leer la respuesta,
**validar la forma** y repreguntar si no cierra— y los adaptadores aportan solo lo
que cada proveedor hace distinto.

La decisión de diseño que importa: **el ciclo no pregunta de qué proveedor se
trata**. Consulta las capacidades declaradas y se adapta:

* si ``esquema_estricto`` es ``False``, valida localmente y repregunta con el
  error como feedback (lo más cerca del structured output del servidor que
  permite el proveedor);
* si ``temperatura_efectiva`` es ``False``, no manda la temperatura y **declara**
  que se ignoró, en vez de dar a entender que se aplicó;
* si ``expone_cache`` es ``False``, no busca tokens de caché.

Eso es lo que permite que Gemini o un proveedor futuro entren sin tocar este
módulo: alcanza con que declaren sus capacidades.

⚠️ La repregunta **se paga**. Por eso, antes de repreguntar, se intenta una
normalización local de las diferencias de **forma** (un `enum` en otra caja): que
el modelo devuelva «Buena» donde el esquema dice «buena» costaba un reintento
completo por un detalle tipográfico.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from .config import MAX_REINTENTOS_ESQUEMA
from .esquema import _errores_de_esquema, _validador_jsonschema, normalizar_por_esquema
from .proveedores import AdaptadorDeepSeek, proveedor_por_nombre


@dataclass
class Respuesta:
    """Resultado de una llamada: el JSON validado, el uso y lo que se compensó."""

    datos: dict[str, Any] | None
    uso: dict[str, Any] = field(default_factory=dict)
    error: str | None = None
    modelo: str = ""
    #: Se pidió temperatura pero NO se envió (el proveedor la ignora): se declara
    #: en el registro en vez de dar a entender que se aplicó.
    temperatura_ignorada: bool = False
    #: Cuántas repreguntas hizo falta por forma del JSON (0 = salió bien).
    reintentos: int = 0
    #: Detalle de los problemas de forma, ya corregidos, para auditar el prompt.
    avisos_esquema: list[str] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return self.error is None and self.datos is not None


def describir_error(exc: Exception, variable_clave: str = "API_KEY") -> str:
    """Mensaje de error legible, sin volcar trazas ni credenciales."""
    nombre = type(exc).__name__
    mensaje = str(exc).strip().splitlines()[0] if str(exc).strip() else ""
    if "authentication" in nombre.lower() or "401" in mensaje:
        return (
            f"{nombre}: credencial rechazada. Revisá {variable_clave} "
            "(se lee del entorno o de --api-key)."
        )
    if "rate" in nombre.lower() or "429" in mensaje:
        return f"{nombre}: límite de tasa alcanzado. Bajá --workers o reintentá. {mensaje}"
    if "connection" in nombre.lower():
        return f"{nombre}: no se pudo conectar con la API. {mensaje}"
    return f"{nombre}: {mensaje}"


def _pedir_correccion(
    messages: list[dict[str, Any]], texto_previo: str, problema: str
) -> None:
    """Repregunta con el problema como feedback, sin volver a mandar la imagen.

    El prefijo (system + imagen) es idéntico al primer pedido, así que el caché de
    contexto del proveedor lo cobra barato. Si la respuesta anterior no traía
    texto, no se agrega el turno ``assistant`` (un mensaje vacío la API lo
    rechaza).
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
                        "Devolvé **solo** el objeto JSON, completo, sin texto "
                        "alrededor ni bloques de código."
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
    temperatura: float | None = None,
    detalle: str | None = None,
    max_tokens: int | None = None,
    esfuerzo: str | None = None,
    proveedor: str | Any = None,
) -> Respuesta:
    """Una llamada a Chat Completions con imagen, validada según el proveedor.

    ``proveedor`` acepta el nombre (``"openai"``/``"deepseek"``/``"gemini"``), el
    adaptador ya instanciado, o ``None`` para el default. El ciclo consulta las
    capacidades y se adapta; ver el docstring del módulo.
    """
    adaptador = proveedor if hasattr(proveedor, "parametros_de_llamada") else None
    if adaptador is None:
        adaptador = (
            proveedor_por_nombre(proveedor) if isinstance(proveedor, str) else AdaptadorDeepSeek()
        )
    cap = adaptador.capacidades
    params = adaptador.parametros_de_llamada(
        modelo=modelo,
        sistema=sistema,
        usuario=usuario,
        data_url=data_url,
        esquema=esquema,
        nombre_esquema="comprobante",
        temperatura=temperatura,
        detalle=detalle,
        max_tokens=max_tokens,
        esfuerzo=esfuerzo,
    )
    # Se pidió temperatura y el proveedor la ignora: se declara, no se calla.
    temperatura_ignorada = temperatura is not None and not cap.temperatura_efectiva

    validador = _validador_jsonschema()
    # Sin esquema estricto, la forma depende de validar y repreguntar acá.
    exige_validacion = (not cap.esquema_estricto) and validador is not None
    reintentos = 0
    avisos: list[str] = []
    uso_acumulado: dict[str, Any] = {}
    texto = ""

    while True:
        try:
            # ⚠️ Se manda una **copia** de los mensajes en cada intento. Si se
            # pasara la lista y el doble/el SDK la retuviera, la repregunta
            # mutaría el pedido anterior y dos intentos distintos viajarían con
            # el mismo contenido.
            cruda = cliente.chat.completions.create(**{**params, "messages": list(params["messages"])})
        except Exception as exc:  # noqa: BLE001 - se clasifica en describir_error
            return Respuesta(
                None,
                uso=uso_acumulado,
                error=describir_error(exc, cap.variable_api_key),
                modelo=modelo,
                temperatura_ignorada=temperatura_ignorada,
                reintentos=reintentos,
                avisos_esquema=avisos,
            )

        lectura = adaptador.leer_respuesta(cruda)
        _acumular_uso(uso_acumulado, lectura.get("uso") or {})
        if lectura.get("error"):
            return Respuesta(
                None,
                uso=uso_acumulado,
                error=lectura["error"],
                modelo=modelo,
                temperatura_ignorada=temperatura_ignorada,
                reintentos=reintentos,
                avisos_esquema=avisos,
            )
        texto = lectura.get("contenido") or ""
        datos = _parsear_json(texto)
        if datos is None:
            problema = "no era JSON válido"
            if reintentos >= MAX_REINTENTOS_ESQUEMA:
                return Respuesta(
                    None, uso=uso_acumulado,
                    error=f"la respuesta no fue JSON válido tras {reintentos} reintento(s)",
                    modelo=modelo, temperatura_ignorada=temperatura_ignorada,
                    reintentos=reintentos, avisos_esquema=avisos,
                )
            reintentos += 1
            _pedir_correccion(params["messages"], texto, problema)
            continue

        # Diferencia de FORMA (un enum en otra caja): se normaliza sin repreguntar.
        if exige_validacion:
            datos = normalizar_por_esquema(esquema, datos, avisos)
            errores = _errores_de_esquema(validador, esquema, datos)
            if errores:
                if reintentos >= MAX_REINTENTOS_ESQUEMA:
                    return Respuesta(
                        None, uso=uso_acumulado,
                        error=(
                            "la respuesta no cumple el esquema tras "
                            f"{reintentos} reintento(s): {'; '.join(errores[:3])}"
                        ),
                        modelo=modelo, temperatura_ignorada=temperatura_ignorada,
                        reintentos=reintentos, avisos_esquema=avisos,
                    )
                reintentos += 1
                _pedir_correccion(params["messages"], texto, "; ".join(errores[:3]))
                continue

        return Respuesta(
            datos,
            uso=uso_acumulado,
            modelo=modelo,
            temperatura_ignorada=temperatura_ignorada,
            reintentos=reintentos,
            avisos_esquema=avisos,
        )


def _parsear_json(texto: str) -> dict[str, Any] | None:
    """JSON de la respuesta, tolerando un bloque de código alrededor.

    Un proveedor sin modo estricto a veces envuelve el objeto en ```json aunque
    se le pida que no: rechazarlo costaría un reintento por algo que se puede
    leer. No se intenta "arreglar" un JSON roto — eso ocultaría que el modelo no
    respetó la forma, que es justo lo que hay que medir.
    """
    import json
    import re

    limpio = texto.strip()
    if limpio.startswith("```"):
        limpio = re.sub(r"^```[a-zA-Z]*\n?|\n?```$", "", limpio).strip()
    try:
        datos = json.loads(limpio)
    except (json.JSONDecodeError, ValueError):
        return None
    return datos if isinstance(datos, dict) else None


def _acumular_uso(acumulado: dict[str, Any], uso: dict[str, Any]) -> None:
    """Suma el uso de todos los intentos.

    Cada repregunta **se paga**, así que el costo del registro tiene que incluir
    los intentos fallidos: contar solo el último haría parecer más barata una
    corrida que gastó de más justamente por un prompt flojo.
    """
    for clave, valor in uso.items():
        if isinstance(valor, (int, float)):
            acumulado[clave] = (acumulado.get(clave) or 0) + valor
        elif clave not in acumulado:
            acumulado[clave] = valor

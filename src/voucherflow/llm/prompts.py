"""Armado del prompt efectivo y del ejemplo que lo acompaña.

El prompt de validación se escribió para **comparar** contra los datos cargados:
su bloque final («Instrucciones generales») incluye el `estado_global` y una
tabla de reglas que solo aplican al modo `validar`. Cuando lo que se quiere es
**extraer** —sin datos con qué comparar— ese cierre sobra, y un modelo que no
tiene la forma impuesta por el servidor (DeepSeek, Gemini) lo obedece: devuelve
`estado_global` y `campos` en modo extracción, y el consumidor lo rechaza.

Por eso hay dos piezas:

* :func:`sistema_de_extraccion` reemplaza el cierre de comparación por una
  instrucción de extracción, conservando las reglas de negocio (son el dominio,
  no el modo). Aplica a los proveedores sin esquema estricto.
* :func:`ejemplo_desde_esquema` **genera** el JSON de ejemplo a partir del mismo
  esquema que valida, en vez de mantener una copia escrita a mano que puede
  divergir. El ejemplo es lo que sostiene la forma cuando el servidor no la
  impone.

⚠️ Un prompt con un ejemplo que no coincide con el esquema es peor que no tener
ejemplo: el modelo copia la forma equivocada y el error aparece como un fallo de
validación, no como el ejemplo desactualizado que es.
"""

from __future__ import annotations

import json
import re
from typing import Any

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

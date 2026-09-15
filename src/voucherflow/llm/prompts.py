"""Armado del prompt efectivo: del template del `.md` al mensaje que se manda.

El prompt no se escribe acá: vive en ``prompts/validacion-mendel.yaml``, que es
el prompt **efectivo** —lo que se manda al modelo—. Al lado está
``validacion-mendel.md``, el documento que lo explica. Este módulo lo **lee**
(acepta `.yaml` y `.md`), lo adapta al modo y lo hashea.

Tres piezas y el problema que resuelve cada una:

* :func:`cargar_prompt` lee el SYSTEM y el USER. Del `.yaml` toma las claves y
  vuelve a unir ``ejemplo_salida`` al ``user``; del `.md` extrae los bloques de
  código. Los dos formatos dan el mismo prompt, así que el archivo se puede
  mover sin tocar el código.
* :func:`construir_mensaje_usuario` arma el mensaje del usuario. El template trae
  el JSON de ejemplo de salida (~78% del texto): con esquema estricto es
  redundante, así que se **omite** por defecto. En modo extracción se reemplaza
  por un ejemplo generado del esquema que valida.
* :func:`armar_prompt_efectivo` es la **función única** que usan correr y
  estimar. Si la estimación de costo armara el prompt por su cuenta, el número
  simulado dejaría de corresponder al real (misma lección que ``salida_de()``:
  escribir y calcular no pueden divergir).

⚠️ :func:`hash_prompt` se calcula sobre el prompt **efectivo** —después de
adaptarlo—, no sobre el crudo. Hashear el crudo identificaría como iguales dos
corridas que mandaron textos distintos.
"""

from __future__ import annotations

import hashlib
import json
import re
from pathlib import Path
from typing import Any

from .esquemas import esquema_extraccion, esquema_validacion

#: Bloque de fenced code con el SYSTEM PROMPT y el USER PROMPT del .md.
_RE_BLOQUE_MD = re.compile(r"```[a-zA-Z]*\n(.*?)```", re.DOTALL)


def cargar_prompt(ruta: Path) -> tuple[str, str]:
    """Lee ``(system_prompt, user_template)`` del prompt.

    Acepta los **dos formatos**, así el prompt se puede mover sin tocar el código:

    * ``.yaml`` (**preferido**): el prompt efectivo, como los demás de
      ``prompts/``. Claves ``system`` y ``user``, más ``ejemplo_salida`` —que se
      vuelve a anexar al ``user`` para reconstruir el template original.
    * ``.md``: extrae los dos bloques de código del documento. Se conserva
      porque es el formato en el que el prompt se redacta y se revisa.
    """
    if not ruta.is_file():
        raise FileNotFoundError(f"no se encontró el prompt: {ruta}")
    if ruta.suffix.lower() in {".yaml", ".yml"}:
        return _cargar_prompt_yaml(ruta)

    texto = ruta.read_text(encoding="utf-8")
    bloques = _RE_BLOQUE_MD.findall(texto)
    if len(bloques) < 2:
        raise ValueError(
            f"se esperaban al menos 2 bloques ```…``` en {ruta} "
            f"(SYSTEM PROMPT y USER PROMPT); se encontraron {len(bloques)}"
        )
    return bloques[0].strip(), bloques[1].strip()


def _cargar_prompt_yaml(ruta: Path) -> tuple[str, str]:
    """Lee el prompt de un YAML y reconstruye el template del ``user``.

    El YAML separa ``user`` (el pedido + el template de datos) de
    ``ejemplo_salida`` (el formato de respuesta) porque el código los trata
    distinto: el primero se manda, el segundo se recorta o se reemplaza. Al
    leerlos se vuelven a unir con la **misma** separación del original, así el
    prompt que llega al modelo no cambia.
    """
    try:
        import yaml
    except ImportError as exc:  # pragma: no cover - depende del entorno
        raise RuntimeError(
            "hace falta PyYAML para leer el prompt en formato YAML "
            "(`pip install PyYAML`)"
        ) from exc

    datos = yaml.safe_load(ruta.read_text(encoding="utf-8"))
    if not isinstance(datos, dict) or "system" not in datos or "user" not in datos:
        raise ValueError(
            f"el YAML de {ruta} debe tener al menos las claves `system` y `user`"
        )
    system = str(datos["system"]).rstrip("\n")
    user = str(datos["user"]).rstrip("\n")
    ejemplo = str(datos.get("ejemplo_salida") or "").rstrip("\n")
    # La separación es la del documento original: una línea en blanco entre el
    # pedido y el ejemplo de salida.
    return system, f"{user}\n\n{ejemplo}" if ejemplo else user


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
    "\n"
    "Sobre el campo 'es_comprobante' (regla 0):\n"
    "- Es la PRIMERA pregunta, antes de leer cualquier dato: ¿el documento es un\n"
    "  comprobante fiscal o comercial?\n"
    "- 'comprobante': lo emitió el proveedor/vendedor y acredita la operación\n"
    "  (factura, ticket, recibo, boleto, nota de crédito/débito, INVOICE de un\n"
    "  proveedor de otro país).\n"
    "- 'no_comprobante': no lo es. Casos: una hoja o captura que dice «GASTOS\n"
    "  VARIOS / FALTA FACTURA», un DNI, un memo interno, una foto de pizarra, un\n"
    "  presupuesto, un resumen de tarjeta, o una captura de una billetera/home\n"
    "  banking que solo MUESTRA un pago o una consulta.\n"
    "- 'indeterminado': la imagen no alcanza para decidirlo (borrosa, cortada,\n"
    "  ilegible). NO lo uses por prudencia cuando el documento se ve bien.\n"
    "- No dice qué comprobante es (eso es 'tipo_comprobante') ni si sirve para\n"
    "  el gasto (eso lo resuelve otra etapa). NO fuerces una letra A/B/C para\n"
    "  «completar» un documento que no es un comprobante.\n"
    "\n"
    "- ⚠️ **Transcribí los datos que el documento SÍ trae, sea o no un\n"
    "  comprobante.** Un 'no_comprobante' (el DNI, el resumen de tarjeta, el\n"
    "  voucher del posnet) igual tiene datos impresos, y esos datos son el\n"
    "  rastro de por qué el gasto se rechazó: si el papel dice «Imp. Total:\n"
    "  $30.920,00» o una fecha, transcribilos en su campo como con cualquier\n"
    "  otro documento. Dejar en null un dato que estás leyendo **pierde la\n"
    "  lectura**; lo que no se transcribe es lo que el papel NO dice (no\n"
    "  inventes un emisor, un CUIT ni una letra A/B/C que no estén impresos).\n"
    "  Explicá en 'observaciones' por qué ese dato no sirve para el gasto.\n"
    "\n"
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



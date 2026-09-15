"""Los datos que el empleado cargó en Mendel, para contrastar.

Es la **verdad de negocio** de la comparación: el comprobante dice una cosa y la
carga dice otra, y el trabajo es encontrar dónde difieren. Por eso el cargador
acepta tanto un JSON de un documento como un mapa de documentos, y lo declara.
"""

from __future__ import annotations

import json
import re
from pathlib import Path

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

    ⚠️ ``img`` puede ser la **clave de página** de un PDF (``x_p01.pdf``): los
    datos se cargan por documento, así que se prueban también el nombre sin el
    sufijo de página (``x``). Sin esto, un PDF con datos cargados no encontraba
    su carga y la corrida fallaba con "no hay datos cargados".
    """
    if "__unico__" in datos and len(datos) == 1:
        return "__unico__", datos["__unico__"]
    candidatos = [img.stem, img.parent.name, str(img.with_suffix(""))]
    sin_pagina = sin_sufijo_de_pagina(img.stem)
    if sin_pagina != img.stem:
        candidatos.extend([sin_pagina, str(img.with_name(sin_pagina).with_suffix(""))])
    for candidato in candidatos:
        if candidato in datos:
            return candidato, datos[candidato]
    return None, None


def sin_sufijo_de_pagina(nombre: str) -> str:
    """Quita el ``_pNN`` de una clave de página (``doc_p01`` → ``doc``).

    Si no termina en ese sufijo, devuelve el nombre tal cual.
    """
    return re.sub(r"_p\d+$", "", nombre)



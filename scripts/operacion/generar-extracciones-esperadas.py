#!/usr/bin/env python
"""Genera el artefacto ``tests/expected-extraction/`` desde las salidas del lab.

**Plan**: [`docs/plan/07-extracciones-esperadas.md`](../../docs/plan/07-extracciones-esperadas.md).

Qué hace, y por qué existe
--------------------------

Las extracciones que produce ``voucherflow-lab`` viven en ``var/``, que está en
``.gitignore``: **no se versionan**, no las ve nadie más que esta máquina y no
sirven como evidencia en un test. Este script las **gradúa** al repo para poder
responder, sin volver a pagar la API:

    ¿El pipeline local (Docling + qwen2.5vl + qwen2.5) lee lo mismo que DeepSeek?

⚠️ **Lo que el artefacto NO es**: no contiene "la verdad". Contiene la lectura de
**otro modelo**, y esa lectura tiene errores medidos (5 de 28 CUIT con el dígito
verificador inválido, §2.3 del plan). Sirve para medir **acuerdo** y
**divergencia**, nunca para declarar que el pipeline es correcto.

⛔ **Lo que este script NO hace, a propósito** (decisión D-6 y §10.1 del plan):

  - **No** marca los 5 CUIT sospechosos en el manifiesto. Sería implementar el
    auto-chequeo, que quedó **fuera de alcance**. Los **cuenta** y los nombra en
    su salida — declarar no es decidir.
  - **No** valida el dígito verificador como regla. La validación fiscal es del
    padrón (``key_value.cuit_completo`` lo documenta a propósito), no de acá.
  - **No** inventa ni normaliza valores: copia la lectura **tal cual** salió del
    lab. Una versión normalizada a mano sería un "esperado" que el modelo nunca
    dijo, y una segunda fuente de verdad que puede divergir.

Estructura que escribe (D-2 y D-3 del plan)::

    tests/expected-extraction/
      README.md
      manifiesto.json            índice: una entrada por corrida
      mapa_de_campos.json        el mapa lab ↔ pipeline (única fuente de verdad)
      <documento-id>/
        <modelo>/
          <corrida>/
            extraccion.json      la lectura, tal cual salió del lab
      fixtures/                  las imágenes que NO estaban versionadas

⚠️ **Por qué ``<modelo>/<corrida>`` y no una carpeta plana**: el mismo modelo,
sobre el mismo documento, produce lecturas distintas entre corridas (medido:
``observaciones`` y ``rubro_emisor`` difieren, y el costo varió 1,8x). Sin el
nivel ``corrida``, dos lecturas del mismo modelo **se pisan** en silencio.

Las imágenes faltantes se copian a ``tests/fixtures/expected-extraction/``, **no**
a los grupos existentes: ``tests/test_fixtures.py`` fija que ``manifest.json``
tenga exactamente 50 archivos con 5 conteos por grupo, así que agregar ahí
rompería la suite. Y ``test_validation_vistas.py`` toma "la primera imagen" con
un ``rglob`` ordenado por ruta: un subdirectorio nuevo lo ordena después de los
grupos existentes y no le cambia lo que recibe.

⚠️ **D-5: este script es regenerable y NO destructivo.** Si una corrida ya está
en el artefacto, se respeta: un regenerado que sobreescribe borraría la única
copia de una lectura cuyo original ya no esté en ``var/``. Y ojo con el límite:
el script puede re-extraer lo que ``var/`` todavía tenga, pero **no puede
recuperar** lo que se haya borrado.

Uso:
    python scripts/operacion/generar-extracciones-esperadas.py --listar
    python scripts/operacion/generar-extracciones-esperadas.py --dry-run
    python scripts/operacion/generar-extracciones-esperadas.py

Sale con código 0 si no hubo errores, y **declara en su salida** qué quedó afuera
(documentos sin imagen, lotes ilegibles): un barrido que se come archivos en
silencio es el patrón que ya costó caro en ``corpus/lectura.py``.
"""

from __future__ import annotations

import argparse
import json
import re
import shutil
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

RAIZ = Path(__file__).resolve().parents[2]

#: Salidas del lab, en orden de precedencia. Si el mismo documento aparece en dos,
#: se guardan **las dos** (son corridas distintas, §6.3 del plan).
FUENTES: tuple[tuple[str, str], ...] = (
    ("var/validations", "validations"),
    ("var/piloto/out", "piloto"),
)

DESTINO = RAIZ / "tests" / "expected-extraction"
FIXTURES = RAIZ / "tests" / "fixtures"
#: Subdirectorio propio: no se mezcla con los grupos versionados (ver el docstring).
FIXTURES_ARTEFACTO = FIXTURES / "expected-extraction"

#: Bloque del registro que se versiona. ``resultado`` y ``extraccion`` son byte a
#: byte idénticos (medido), así que se fija **uno** y un test exige que exista.
CLAVE_EXTRACCION = "extraccion"

#: Campos del registro que viajan al manifiesto. Lo demás se descarta a propósito:
#: el manifiesto es un **índice**, no una copia del registro (mismo criterio que
#: `trace/agregado.py`, que apunta al sidecar en vez de embeberlo).
CAMPOS_MANIFIESTO = (
    "archivo_relativo",
    "procesado_utc",
    "modelo",
    "modo",
    "version_prompt",
    "prompt_hash",
    "fuente",
    "uso",
    "costo_usd",
    "precios_usd_1m",
    "esquema_validado",
    "reintentos_esquema",
    "aritmetica",
)

#: ⚠️ El esquema viejo (`piloto`) no tiene estos campos. Se tolera su ausencia en
#: vez de rellenarlos con un default inventado (§2.2 del plan).
CAMPOS_OPCIONALES = frozenset(
    {"esquema_validado", "reintentos_esquema", "avisos_esquema"}
)


# --------------------------------------------------------------------------- #
# Lectura de las salidas del lab
# --------------------------------------------------------------------------- #


def _leer_registros() -> tuple[list[tuple[Path, dict[str, Any], str]], list[str]]:
    """Devuelve ``[(ruta, registro, lote)]`` y la lista de problemas.

    Un archivo ilegible **no aborta** el barrido: se lista y se sigue, para que el
    operador vea todo lo que hay. Abortar en el primero escondería el resto.
    """
    encontrados: list[tuple[Path, dict[str, Any], str]] = []
    problemas: list[str] = []
    for relativo, lote in FUENTES:
        carpeta = RAIZ / relativo
        if not carpeta.is_dir():
            problemas.append(f"no existe la carpeta de salida: {relativo}")
            continue
        for archivo in sorted(carpeta.rglob("*.json")):
            try:
                datos = json.loads(archivo.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError) as exc:
                problemas.append(f"{archivo}: no se pudo leer ({exc})")
                continue
            if not isinstance(datos, dict) or CLAVE_EXTRACCION not in datos:
                problemas.append(
                    f"{archivo}: no parece un registro del lab "
                    f"(falta {CLAVE_EXTRACCION!r})"
                )
                continue
            encontrados.append((archivo, datos, lote))
    return encontrados, problemas


#: ``2026-09-14T17:05:09+00:00`` → ``20260914T170509Z``.
#:
#: ⚠️ Se normaliza a UTC y sin separadores: un nombre con ``:`` o espacio rompe en
#: Windows y en varias herramientas, y la hora local cambia según la máquina. El
#: ``procesado_utc`` del registro ya viene en UTC.
def _nombre_de_corrida(procesado_utc: str) -> str:
    try:
        momento = datetime.fromisoformat(procesado_utc)
    except (TypeError, ValueError):
        return re.sub(r"[^0-9A-Za-z]", "", str(procesado_utc))[:15] or "sin-fecha"
    return momento.astimezone(timezone.utc).strftime("%Y%m%dT%H%M%SZ")


def _id_documento(registro: dict[str, Any]) -> str:
    """Id del documento: el **stem** del archivo de origen.

    Es estable respecto de cómo se haya invocado el lab (a diferencia de
    ``archivo_relativo``, que cambia con la raíz) y es el mismo nombre con el que
    se guarda la fixture. El hash del contenido no se puede recalcular si la
    imagen no está, y entonces el id dependería de tener la imagen.
    """
    return Path(registro["origen"]).stem


# --------------------------------------------------------------------------- #
# Imágenes: copiarlas al repo (D-1)
# --------------------------------------------------------------------------- #


def _fixture_de(nombre: str) -> Path | None:
    """Busca una imagen ya versionada en ``tests/fixtures`` (cualquier grupo)."""
    for candidata in sorted(FIXTURES.rglob(nombre)):
        if "expected-extraction" not in candidata.parts:
            return candidata
    return None


def _copiar_imagen(origen: Path, nombre: str, dry_run: bool) -> tuple[str | None, str]:
    """Copia la imagen al artefacto si no estaba versionada.

    Devuelve ``(ruta_relativa | None, acción)``. La ruta es relativa a
    ``tests/fixtures`` para que el manifiesto no dependa de dónde se clonó el
    repo.
    """
    ya = _fixture_de(nombre)
    if ya is not None:
        return str(ya.relative_to(FIXTURES)), "ya_versionada"

    destino = FIXTURES_ARTEFACTO / nombre
    if not origen.is_file():
        return None, "sin_imagen"
    if destino.is_file():
        return str(destino.relative_to(FIXTURES)), "ya_copiada"
    if dry_run:
        return str(destino.relative_to(FIXTURES)), "copiaria"
    destino.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(origen, destino)
    return str(destino.relative_to(FIXTURES)), "copiada"


# --------------------------------------------------------------------------- #
# Generación del artefacto
# --------------------------------------------------------------------------- #


def _entrada(
    registro: dict[str, Any],
    lote: str,
    archivo: Path,
    imagen: str | None,
    accion_imagen: str,
) -> dict[str, Any]:
    """Arma la entrada del manifiesto para una corrida."""
    entrada: dict[str, Any] = {
        "documento_id": _id_documento(registro),
        "archivo": Path(registro["origen"]).name,
        "modelo": registro.get("modelo"),
        "corrida": _nombre_de_corrida(registro.get("procesado_utc", "")),
        "fuente_lote": lote,
        "origen_var": str(archivo.relative_to(RAIZ)),
        # Dónde quedó la imagen, o ``null``: el bloqueante §3.1 declarado **en el
        # dato**, no en un comentario. Un `null` deja ver la falta de cobertura.
        "imagen_en_fixtures": imagen,
        "imagen_accion": accion_imagen,
        "dimensiones": (registro.get("imagen") or {}).get("dimensiones"),
        "extraido_utc": registro.get("procesado_utc"),
    }
    for campo in CAMPOS_MANIFIESTO:
        if campo in registro:
            entrada[campo] = registro[campo]
        elif campo not in CAMPOS_OPCIONALES:
            entrada[campo] = None
    # Ruta relativa dentro del artefacto (la que usa el test para leer).
    entrada["ruta"] = (
        f"{entrada['documento_id']}/{entrada['modelo']}/{entrada['corrida']}"
        f"/{CLAVE_EXTRACCION}.json"
    )
    return entrada


def _escribir_json(destino: Path, datos: Any, dry_run: bool) -> None:
    if dry_run:
        return
    destino.parent.mkdir(parents=True, exist_ok=True)
    destino.write_text(
        json.dumps(datos, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )


def generar(dry_run: bool = False) -> int:
    registros, problemas = _leer_registros()
    if not registros:
        print("error: no se encontró ninguna extracción en var/", file=sys.stderr)
        for p in problemas:
            print(f"  · {p}", file=sys.stderr)
        return 2

    entradas: list[dict[str, Any]] = []
    acciones: dict[str, int] = {}
    #: ⚠️ Documentos con el MISMO id y la MISMA corrida: es una colisión real y
    #: hay que declararla. Con `<modelo>/<corrida>` en la ruta, dos corridas del
    #: mismo modelo con el mismo timestamp se pisarían.
    vistas: dict[str, list[str]] = {}

    for archivo, registro, lote in registros:
        nombre = Path(registro["origen"]).name
        imagen, accion = _copiar_imagen(Path(registro["origen"]), nombre, dry_run)
        acciones[accion] = acciones.get(accion, 0) + 1
        entrada = _entrada(registro, lote, archivo, imagen, accion)
        vistas.setdefault(entrada["ruta"], []).append(entrada["origen_var"])
        entradas.append(entrada)

    colisiones = {r: v for r, v in vistas.items() if len(v) > 1}

    # Se ordena para que el manifiesto sea estable entre corridas (un diff del
    # artefacto debe mostrar solo lo que cambió de verdad).
    entradas.sort(key=lambda e: (e["documento_id"], str(e["modelo"]), str(e["corrida"])))

    for entrada in entradas:
        destino = DESTINO / entrada["ruta"]
        if destino.is_file():
            # ⚠️ D-5: no destructivo. Una lectura ya versionada no se pisa.
            entrada["accion"] = "ya_en_artefacto"
            continue
        origen_var = RAIZ / entrada["origen_var"]
        registro = json.loads(origen_var.read_text(encoding="utf-8"))
        _escribir_json(destino, registro, dry_run)
        entrada["accion"] = "escrita"

    manifiesto = {
        "version": "1.0",
        "descripcion": (
            "Lecturas de referencia (DeepSeek) para comparar contra el pipeline "
            "local. NO es un golden: mide ACUERDO, no exactitud. Ver README.md."
        ),
        "generado_utc": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "generador": "scripts/operacion/generar-extracciones-esperadas.py",
        "clave_extraccion": CLAVE_EXTRACCION,
        "fuentes": [relativo for relativo, _ in FUENTES],
        "cobertura": {
            "corridas": len(entradas),
            "documentos": len({e["documento_id"] for e in entradas}),
            "con_imagen": sum(1 for e in entradas if e["imagen_en_fixtures"]),
            "sin_imagen": sum(1 for e in entradas if not e["imagen_en_fixtures"]),
            "por_modelo": _contar(entradas, "modelo"),
            "por_lote": _contar(entradas, "fuente_lote"),
        },
        "entradas": entradas,
    }
    _escribir_json(DESTINO / "manifiesto.json", manifiesto, dry_run)

    # ------------------------------------------------------------------ salida
    modo = " --dry-run" if dry_run else ""
    print(f"generador de extracciones esperadas{modo}")
    print(f"  destino            : {DESTINO.relative_to(RAIZ)}")
    print(f"  corridas           : {len(entradas)}")
    print(
        f"  documentos únicos  : {len({e['documento_id'] for e in entradas})}"
        "   (menos que corridas = el mismo documento extraído más de una vez)"
    )
    print(f"  imágenes           : {acciones}")
    print(f"  por modelo         : {_contar(entradas, 'modelo')}")
    print(f"  por lote           : {_contar(entradas, 'fuente_lote')}")

    sin_imagen = [e for e in entradas if not e["imagen_en_fixtures"]]
    if sin_imagen:
        print(f"\n  ⚠ SIN IMAGEN ({len(sin_imagen)}): no se pueden comparar con el pipeline")
        for e in sin_imagen:
            print(f"      {e['archivo']} (origen: {e['origen_var']})")

    if colisiones:
        print(f"\n  ⚠ COLISIÓN DE RUTA ({len(colisiones)}): se pisarían entre sí")
        for ruta, origenes in colisiones.items():
            print(f"      {ruta}")
            for o in origenes:
                print(f"        ← {o}")

    if problemas:
        print(f"\n  ⚠ NO LEÍDOS ({len(problemas)}):")
        for p in problemas:
            print(f"      {p}")

    # ------------------------------------------------------------------- aviso
    # ⛔ D-6: el auto-chequeo NO se implementa. Acá solo se **cuenta** (declarar
    # no es decidir) para que el README pueda listar los casos sospechosos.
    sospechosos = _cuit_sospechosos(entradas)
    if sospechosos:
        print(
            f"\n  ℹ CUIT sin dígito verificador válido: {len(sospechosos)} "
            "de los completos  (⛔ sin marcar en el manifiesto: ver D-6)"
        )
        for doc, cuit, emisor in sospechosos:
            print(f"      {cuit:18} {emisor}")

    print(f"\n  manifiesto         : {(DESTINO / 'manifiesto.json').relative_to(RAIZ)}")
    return 0 if not problemas else 1


def _contar(entradas: list[dict[str, Any]], clave: str) -> dict[str, int]:
    cuenta: dict[str, int] = {}
    for e in entradas:
        valor = str(e.get(clave))
        cuenta[valor] = cuenta.get(valor, 0) + 1
    return dict(sorted(cuenta.items()))


def _digito_verificador_valido(cuit: Any) -> bool | None:
    """Módulo 11 del CUIT. ``None`` si no hay 11 dígitos.

    ⚠️ Esto **no** es una regla de la extracción ni una validación fiscal: es un
    chequeo de la **medición**, para poder decir si la referencia es creíble.
    ``key_value.cuit_completo`` documenta a propósito que la extracción no valida
    el dígito verificador (eso es del padrón), y hay un test que lo fija. Este
    script no la toca: calcula acá, para informar.
    """
    digitos = re.sub(r"\D", "", str(cuit or ""))
    if len(digitos) != 11:
        return None
    pesos = (5, 4, 3, 2, 7, 6, 5, 4, 3, 2)
    suma = sum(int(d) * p for d, p in zip(digitos[:10], pesos))
    resto = 11 - (suma % 11)
    return (0 if resto == 11 else resto) % 10 == int(digitos[10])


def _cuit_sospechosos(entradas: list[dict[str, Any]]) -> list[tuple[str, str, Any]]:
    """Lista ``(documento, cuit, emisor)`` con el DV inválido.

    Se lee del **registro versionado** y, si todavía no está (``--dry-run``), del
    original en ``var/``: el dato sale de la fuente, no de un campo del manifiesto
    que alguien podría editar.
    """
    sospechosos: list[tuple[str, str, Any]] = []
    vistos: set[tuple[str, str]] = set()
    for entrada in entradas:
        archivo = DESTINO / entrada["ruta"]
        if not archivo.is_file():
            archivo = RAIZ / entrada["origen_var"]
        if not archivo.is_file():
            continue
        lectura = json.loads(archivo.read_text(encoding="utf-8"))[CLAVE_EXTRACCION]
        cuit = lectura.get("cuit_emisor")
        clave = (entrada["documento_id"], str(cuit))
        if clave in vistos:
            continue
        vistos.add(clave)
        if _digito_verificador_valido(cuit) is False:
            sospechosos.append(
                (entrada["documento_id"], str(cuit), lectura.get("razon_social_emisor"))
            )
    return sorted(sospechosos)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="generar-extracciones-esperadas.py",
        description=__doc__.splitlines()[0],
    )
    parser.add_argument(
        "--dry-run", action="store_true",
        help="mostrar qué haría, sin escribir nada (ni el artefacto ni las imágenes)",
    )
    parser.add_argument(
        "--listar", action="store_true",
        help="listar las fuentes encontradas y salir",
    )
    args = parser.parse_args(argv)

    if args.listar:
        registros, problemas = _leer_registros()
        print(f"fuentes: {', '.join(r for r, _ in FUENTES)}")
        print(f"corridas encontradas: {len(registros)}")
        for archivo, registro, lote in registros:
            print(
                f"  [{lote:11}] {_id_documento(registro)}  "
                f"{registro.get('modelo')}  {_nombre_de_corrida(registro.get('procesado_utc', ''))}"
            )
        for p in problemas:
            print(f"  ⚠ {p}")
        return 0

    return generar(dry_run=args.dry_run)


if __name__ == "__main__":
    raise SystemExit(main())

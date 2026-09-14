"""La corrida sobre el corpus: recorrer, reanudar, estimar y reportar.

Es el cuerpo del laboratorio, portado del script original: recorre la carpeta,
salta lo ya hecho, llama al modelo por cada imagen y guarda un registro con la
**procedencia** de cada dato (qué prompt, qué modelo, qué costo).

Lo que hace confiable a una corrida larga:

* **Reanudable**: un registro con ``error`` **no** cuenta como hecho. Si contara,
  una corrida que falló por red dejaría el archivo marcado como procesado y la
  re-corrida no lo tocaría (esa lección costó pagar dos veces el mismo lote).
* **Procedencia**: cada salida guarda el hash del prompt efectivo, el uso real de
  tokens y los precios aplicados. Sin eso, comparar dos corridas es adivinar.
* **El gasto acumula los reintentos**: cada repregunta se paga, así que un prompt
  flojo se ve en el costo.
* **El costo no se inventa**: si falta el precio, queda ``null`` y se declara.
"""

from __future__ import annotations

import csv
import json
import re
import sys
from collections.abc import Sequence
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

# El recorrido del corpus y la regla de espejado son de `voucherflow.corpus`:
# las comparte la reducción de imágenes. Tener una copia acá fue el bug que hizo
# pagar dos veces el mismo documento, así que se importan (con alias privado,
# para no tocar los call sites) en vez de redefinirlas.
from ..corpus.recorrido import (  # noqa: E402
    expandir as _expandir,
)
from ..corpus.recorrido import (
    raiz_espejado as _raiz_espejado,
)
from ..corpus.recorrido import (
    salida_de,
)
from ..persistencia import escribir_json_atomico
from . import costos
from .config import (
    CHARS_POR_TOKEN_ESTIMADO,
    COMPLETION_TOKENS_TIPICO,
    EXTENSIONES_IMAGEN,
    LIMITE_BYTES_IMAGEN,
    MIN_MUESTRAS_PARA_CALIBRAR,
    TOKENS_MAX_IMAGEN,  # noqa: E402
    VERSION_PROMPT,
)
from .costos import (
    CONFIANZA_FORMULA,
    CONFIANZA_HISTORICO,
    CONFIANZA_MIXTA,
    costo_de_tokens,
    formatear_precio,
    precio_cache_de,
    precio_de,
)
from .datos import buscar_datos
from .ejecucion import llamar_api  # noqa: E402
from .esquema import _validador_jsonschema
from .esquemas import esquema_extraccion, esquema_validacion
from .evaluador import diff_deterministico, verificar_aritmetica
from .imagenes import codificar_imagen, info_imagen
from .prompts import armar_prompt_efectivo, hash_prompt
from .protocolo import CLAVE_CACHE_HIT, CLAVE_CACHE_MISS
from .proveedores import proveedor_por_nombre


@dataclass
class Opciones:
    """Opciones efectivas de la corrida."""

    modo: str
    modelo: str
    detalle: str
    temperatura: float | None
    max_tokens: int | None
    esfuerzo: str | None
    salida: Path
    forzar: bool
    workers: int
    dry_run: bool
    incluir_ejemplo: bool
    #: Precio único para todos los modelos (pisa la tabla si viene de la CLI).
    precio_entrada: float | None = None
    precio_salida: float | None = None
    #: Precios por modelo, en USD por 1M: ``{"deepseek-flash": (entrada, salida)}``.
    #: El comodín ``"*"`` cubre los modelos no listados. Una tabla vacía usa la
    #: de referencia (``costos.PRECIOS_REFERENCIA``).
    precios: dict[str, tuple[float | None, float | None]] = field(default_factory=dict)
    #: Precios de la entrada **cacheada**, por modelo. Solo los proveedores que
    #: la exponen la tienen: cobrarla al precio lleno inflaría el gasto.
    precios_cache: dict[str, float] = field(default_factory=dict)
    #: Zona horaria para agrupar el gasto por día (``None`` = local).
    tz: timezone | None = None
    tz_etiqueta: str = "local"
    #: Delimitador y separador decimal del CSV de gastos (Excel es-AR usa «;» y «,»).
    csv_delim: str = ","
    csv_decimal: str = "."
    #: Sufijo de los archivos de salida. La reanudación lo usa para saber si un
    #: documento ya está procesado: si cambia, los anteriores **no** cuentan como
    #: hechos —y es a propósito—: son de otra corrida.
    sufijo: str = ".json"


def procesar(
    img: Path,
    raiz: Path,
    cliente: Any,
    sistema: str,
    user_template: str,
    datos: dict[str, dict],
    extracciones: dict[str, dict],
    opciones: Opciones,
    proveedor: str = "deepseek",
) -> dict[str, Any]:
    """Procesa una imagen y devuelve el registro (con procedencia) para guardar."""
    try:
        relativo = img.resolve().relative_to(raiz.resolve())
    except (ValueError, OSError):
        relativo = Path(img.name)

    registro: dict[str, Any] = {
        "origen": str(img),
        "archivo_relativo": str(relativo),
        "procesado_utc": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "modelo": opciones.modelo,
        "modo": opciones.modo,
        "version_prompt": VERSION_PROMPT,
        "fuente": "api",
    }

    clave_datos, datos_doc = (None, None)
    if opciones.modo in {"validar", "diff"}:
        clave_datos, datos_doc = buscar_datos(img, datos)
        registro["datos_clave"] = clave_datos
        if datos_doc is None:
            registro["error"] = (
                "no hay datos cargados para esta imagen "
                f"(claves probadas: {img.stem}, {img.parent.name})"
            )
            return registro

    # El modo diff reutiliza la extracción previa si existe: no vuelve a pagar.
    if opciones.modo == "diff":
        extraccion = extracciones.get(str(relativo))
        if extraccion is None:
            registro["error"] = (
                "modo diff sin extracción previa; corré primero --modo extraer "
                "(o usá --modo validar)"
            )
            return registro
        registro["fuente"] = "diff_local"
        registro["resultado"] = diff_deterministico(extraccion, datos_doc)
        return registro

    # El dry-run no llega hasta acá: `main` estima el costo y sale antes de
    # llamar a la API (así no se codifica base64 ni se gasta memoria al pedo).
    try:
        data_url, info = codificar_imagen(img, opciones.detalle)
    except OSError as exc:
        registro["error"] = f"no se pudo leer la imagen: {exc}"
        return registro
    registro["imagen"] = info

    if info["bytes"] > LIMITE_BYTES_IMAGEN:
        registro["error"] = (
            f"la imagen pesa {info['bytes'] / 1e6:.1f} MB y la API acepta hasta "
            f"{LIMITE_BYTES_IMAGEN / 1024 / 1024:.0f} MB por imagen "
            "(base64): reducíla con `voucherflow corpus`"
        )
        return registro

    # Prompt efectivo del modo (misma función que usa el estimador, así el costo
    # simulado corresponde al real).
    sistema_efectivo, usuario = armar_prompt_efectivo(
        opciones.modo,
        sistema,
        user_template,
        datos_doc,
        incluir_ejemplo=opciones.incluir_ejemplo,
    )
    esquema = (
        esquema_validacion() if opciones.modo == "validar" else esquema_extraccion()
    )
    # El hash es del prompt **efectivo** (system adaptado + user armado): es lo
    # que permite auditar después con qué prompt se generó cada salida.
    registro["prompt_hash"] = hash_prompt(sistema_efectivo, usuario)

    respuesta = llamar_api(
        cliente,
        modelo=opciones.modelo,
        sistema=sistema_efectivo,
        usuario=usuario,
        data_url=data_url,
        esquema=esquema,
        temperatura=opciones.temperatura,
        detalle=opciones.detalle,
        max_tokens=opciones.max_tokens,
        esfuerzo=opciones.esfuerzo,
        # Sin esto, la llamada iría siempre al endpoint del default: un cliente
        # de Gemini hablaría con DeepSeek.
        proveedor=proveedor,
    )
    registro["uso"] = respuesta.uso
    registro["esquema_validado"] = _validador_jsonschema() is not None
    # Deja constancia de lo que el proveedor no acepta, en vez de callarlo.
    if respuesta.temperatura_ignorada:
        registro["nota_temperatura"] = (
            "DeepSeek no aplica temperature (thinking mode): el valor pedido se "
            "ignoró; el proveedor decide el muestreo."
        )
    if respuesta.reintentos:
        registro["reintentos_esquema"] = respuesta.reintentos
        registro["avisos_esquema"] = respuesta.avisos_esquema
    if not respuesta.ok:
        # El error viene con la lectura cruda del modelo recortada; se guarda
        # entera en el registro para poder diagnosticar el fallo (el gasto ya
        # está en `uso`).
        registro["error"] = respuesta.error
        return registro

    # Costo con los precios de ESTA corrida, persistido junto al uso: así el
    # reporte de gastos no tiene que adivinar precios históricos después.
    p_entrada, p_salida = costos.precio_de(opciones.modelo, opciones.precios)
    p_cache = precio_cache_de(opciones.modelo, opciones.precios_cache)
    registro["precios_usd_1m"] = {
        "entrada": p_entrada,
        "salida": p_salida,
        "entrada_cache": p_cache,
        "origen": "cli" if opciones.precios or opciones.precio_entrada else "referencia",
    }
    registro["costo_usd"] = costo_de_tokens(
        respuesta.uso.get("prompt_tokens") or 0,
        respuesta.uso.get("completion_tokens") or 0,
        p_entrada,
        p_salida,
        # Las claves son las del SDK, compartidas con el adaptador: cuando cada
        # punta usaba su propio nombre, la caché no se descontaba nunca.
        cache_hit_tokens=respuesta.uso.get(CLAVE_CACHE_HIT) or 0,
        precio_cache=p_cache,
    )

    registro["resultado"] = respuesta.datos
    if opciones.modo == "extraer":
        registro["extraccion"] = respuesta.datos
        # La aritmética se recalcula en Python: el `cierra_aritmetica` que
        # devuelve el modelo no es confiable (dijo `true` con diferencias de
        # 10,00 y 569,00 en comprobantes reales).
        aritmetica = verificar_aritmetica(respuesta.datos)
        registro["aritmetica"] = aritmetica
        if aritmetica["calculable"]:
            respuesta.datos["cierra_aritmetica"] = aritmetica["cierra"]
            if not aritmetica["cierra"]:
                aviso = (
                    f"Los importes leídos suman {aritmetica['suma']:,.2f} y el "
                    f"total impreso es {aritmetica['total']:,.2f} "
                    f"(diferencia {aritmetica['diferencia']:,.2f})."
                )
                if aritmetica["faltantes"]:
                    aviso += (
                        " Puede faltar alguno de: "
                        + ", ".join(aritmetica["faltantes"])
                        + "."
                    )
                registro["aritmetica"]["aviso"] = aviso
    return registro


def estimar_costo_corrida(
    imagenes: Sequence[Path],
    sistema: str,
    user_template: str,
    opciones: Opciones,
) -> dict[str, Any]:
    """Estima **cuánto costaría** procesar estas imágenes, sin llamar a la API.

    Es lo que responde ``--dry-run``. La estimación se arma con lo que sí se sabe
    sin gastar:

    - **tokens de imagen**: **tope fijo por imagen** (1.024 tokens; DeepSeek
      redimensiona toda imagen a ~1300×1300 px antes de inferir), así que la
      resolución no cambia el costo. Ver :func:`tokens_imagen`.
    - **tokens de texto**: se construye el prompt **de verdad** (system + user +
      esquema) y se convierte con :data:`CHARS_POR_TOKEN_ESTIMADO`, medido contra
      el uso real de la API. Si la carpeta de salida ya tiene extracciones pagas,
      se **calibra con ese histórico** (prompt real menos tokens de imagen), que
      es más fiel que la fórmula;
    - **tokens de salida**: promedio del histórico, o
      :data:`COMPLETION_TOKENS_TIPICO`.

    Devuelve el detalle por documento y los totales; ``confianza`` dice de dónde
    salió el número (``historico``, ``formula`` o ``mixta``) para no presentar una
    estimación como si fuera una factura.
    """
    # Prompt efectivo (el mismo que se enviaría para la primera imagen), con la
    # MISMA función que usa `procesar`: si el estimador lo armara por su cuenta,
    # el costo simulado dejaría de corresponder al real (p. ej. no contaba el
    # cierre de system del modo extraer).
    sistema_efectivo, usuario = armar_prompt_efectivo(
        opciones.modo, sistema, user_template, None, incluir_ejemplo=opciones.incluir_ejemplo
    )
    esquema = esquema_validacion() if opciones.modo == "validar" else esquema_extraccion()
    chars = len(sistema_efectivo) + len(usuario) + len(json.dumps(esquema))
    tokens_texto_formula = round(chars / CHARS_POR_TOKEN_ESTIMADO)

    # Calibración con el histórico de la carpeta de salida, si alcanza.
    medidas = _tokens_de_texto_historicos(opciones.salida, opciones)
    if len(medidas) >= MIN_MUESTRAS_PARA_CALIBRAR:
        tokens_texto = round(sum(medidas) / len(medidas))
        confianza = CONFIANZA_HISTORICO
    else:
        tokens_texto = tokens_texto_formula
        confianza = CONFIANZA_FORMULA

    salidas_historicas = _tokens_de_salida_historicos(opciones.salida, opciones)
    tokens_salida = (
        round(sum(salidas_historicas) / len(salidas_historicas))
        if salidas_historicas
        else COMPLETION_TOKENS_TIPICO
    )
    if salidas_historicas and confianza == CONFIANZA_HISTORICO:
        confianza = CONFIANZA_HISTORICO
    elif salidas_historicas:
        confianza = CONFIANZA_MIXTA

    precio_entrada, precio_salida = precio_de(opciones.modelo, opciones.precios)
    # El caché no se sabe de antemano: se estima el caso **sin caché** (el más
    # caro) y se declara, en vez de suponer un ahorro que puede no darse.
    precio_cache = precio_cache_de(opciones.modelo, opciones.precios_cache)

    detalle: list[dict[str, Any]] = []
    total_entrada = total_salida = 0
    total_costo = 0.0
    sin_precio = 0
    for img in imagenes:
        try:
            info = info_imagen(img, opciones.detalle)
        except OSError:
            continue
        tokens_img = info.get("tokens_estimados")
        # Sin dimensiones legibles no se puede estimar la imagen: se cuenta el
        # texto y se declara que falta la parte más pesada.
        entrada = (tokens_img or 0) + tokens_texto
        # Cache hit y miss cuestan distinto, y no se sabe de antemano cuánto va a
        # pegar el caché: se estima el caso **sin caché** (el más caro) y la
        # estimación del log se ajusta después, con lo que crezca el prefijo
        # cacheado (el system es el mismo en todo el lote).
        costo = costo_de_tokens(
            entrada,
            tokens_salida,
            precio_entrada,
            precio_salida,
            cache_hit_tokens=0,
            precio_cache=precio_cache,
        )
        if costo is None:
            sin_precio += 1
        else:
            total_costo += costo
        total_entrada += entrada
        total_salida += tokens_salida
        detalle.append(
            {
                "imagen": str(img),
                "tokens_imagen": tokens_img,
                "tokens_texto": tokens_texto,
                "tokens_entrada": entrada,
                "tokens_salida": tokens_salida,
                "costo_usd": costo,
            }
        )

    return {
        "archivos": len(detalle),
        "tokens_texto_estimados": tokens_texto,
        "tokens_texto_formula": tokens_texto_formula,
        "prompt_chars": chars,
        "tokens_imagen": TOKENS_MAX_IMAGEN,
        "tokens_salida_estimados": tokens_salida,
        "tokens_entrada_totales": total_entrada,
        "tokens_salida_totales": total_salida,
        "cache_hit_historicos": sum(
            (registro.get("uso") or {}).get(CLAVE_CACHE_HIT) or 0
            for registro in _registros_validos(opciones.salida, opciones)
        ),
        "costo_usd_total": round(total_costo, 6) if total_costo else None,
        "costo_usd_promedio": (
            round(total_costo / (len(detalle) - sin_precio), 6)
            if detalle and len(detalle) > sin_precio
            else None
        ),
        "precio_entrada_usd_1m": precio_entrada,
        "precio_salida_usd_1m": precio_salida,
        "precio_entrada_cache_usd_1m": precio_cache,
        "sin_precio": sin_precio,
        "confianza": confianza,
        "muestras_historico": len(medidas),
        "detalle": detalle,
        "opciones": {
            "modo": opciones.modo,
            "modelo": opciones.modelo,
            "detalle_imagen": opciones.detalle,
            "prompt_con_ejemplo_de_salida": opciones.incluir_ejemplo,
            # Se declara porque el cálculo de DeepSeek es distinto al de OpenAI:
            # la resolución no cambia el costo de la imagen (tope fijo por imagen).
            "tokens_imagen_fijos": TOKENS_MAX_IMAGEN,
        },
    }


def _tokens_de_texto_historicos(
    salida: Path, opciones: Opciones
) -> list[int]:
    """Tokens de **texto** (prompt menos imagen) de las extracciones ya pagas.

    Sirve para calibrar la estimación con datos propios en vez de con una
    constante: el prompt del proyecto puede cambiar (otro esquema, otro prompt) y
    la fórmula quedaría vieja.
    """
    medidas: list[int] = []
    for registro in _registros_validos(salida, opciones):
        uso = registro.get("uso") or {}
        prompt = uso.get("prompt_tokens")
        if prompt is None:
            continue
        img = (registro.get("imagen") or {}).get("tokens_estimados") or 0
        texto = prompt - img
        if texto > 0:
            medidas.append(texto)
    return medidas


def _tokens_de_salida_historicos(salida: Path, opciones: Opciones) -> list[int]:
    """Tokens de salida (``completion``) de las extracciones ya pagas."""
    salidas: list[int] = []
    for registro in _registros_validos(salida, opciones):
        salida_tok = (registro.get("uso") or {}).get("completion_tokens")
        if salida_tok:
            salidas.append(salida_tok)
    return salidas


def _registros_validos(salida: Path, opciones: Opciones) -> list[dict]:
    """Registros pagos de la carpeta de salida, del mismo modelo y modo.

    Se filtra por modelo y modo porque los tokens no son comparables entre
    modelos (ni entre comparar y solo extraer): mezclarlos daría una calibración
    con un promedio que no corresponde a lo que se va a correr.
    """
    if not salida.is_dir():
        return []
    registros: list[dict] = []
    for archivo in sorted(salida.rglob("*.json")):
        if archivo.name in {"reporte.json", "gastos.json"}:
            continue
        try:
            registro = json.loads(archivo.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            continue
        if not isinstance(registro, dict) or registro.get("error"):
            continue
        if registro.get("modelo") != opciones.modelo:
            continue
        if registro.get("modo") != opciones.modo:
            continue
        registros.append(registro)
    return registros


def imprimir_estimacion(est: dict[str, Any], *, detalle: bool = False) -> None:
    """Muestra la estimación de costo de una corrida (lo que imprime ``--dry-run``)."""
    print("\n=== Estimación de costo (simulación: no se llamó a la API) ===")
    if not est["archivos"]:
        print("No hay imágenes que estimar.")
        return
    print(f"archivos          : {est['archivos']}")
    print(
        f"tokens por archivo: entrada ≈ {est['tokens_texto_estimados']:,} (texto+esquema) "
        f"+ {est['tokens_imagen']:,} de imagen (tope fijo de DeepSeek) | "
        f"salida ≈ {est['tokens_salida_estimados']:,}"
    )
    print(
        f"tokens totales    : entrada {est['tokens_entrada_totales']:,} | "
        f"salida {est['tokens_salida_totales']:,}"
    )
    if est.get("cache_hit_historicos"):
        print(
            f"caché de contexto : {est['cache_hit_historicos']:,} tokens ya se "
            "sirvieron de caché en corridas previas (se cobran mucho más barato)"
        )
    if est["costo_usd_total"] is not None:
        print(f"COSTO ESTIMADO    : US$ {est['costo_usd_total']:.4f}", end="")
        if est["costo_usd_promedio"] is not None:
            print(f"   (≈ US$ {est['costo_usd_promedio']:.4f} por comprobante)")
        else:
            print()
        print(
            f"precios usados    : US$ {formatear_precio(est['precio_entrada_usd_1m'])}/1M "
            f"entrada, US$ {formatear_precio(est['precio_salida_usd_1m'])}/1M salida, "
            f"US$ {formatear_precio(est.get('precio_entrada_cache_usd_1m'))}/1M entrada-caché "
            f"({est['opciones']['modelo']})"
        )
        print(
            "postura del precio: PICO (01:00-04:00 y 06:00-10:00 UTC, L-V) y SIN "
            "caché: es el techo; off-peak cuesta la mitad"
        )
    else:
        print(
            "COSTO ESTIMADO    : — (no hay precio para el modelo "
            f"{est['opciones']['modelo']}; pasá --precios o --precio-entrada/--precio-salida)"
        )
    # De dónde salió el número: una estimación no es una factura.
    fuentes = {
        "historico": f"calibrada con {est['muestras_historico']} extracción(es) previa(s)",
        "formula": f"fórmula ({CHARS_POR_TOKEN_ESTIMADO} car/token; medido contra la API)",
        "mixta": "texto por fórmula y salida por histórico",
    }
    print(f"confianza         : {fuentes.get(est['confianza'], est['confianza'])}")
    if est["sin_precio"]:
        print(
            f"  ⚠ {est['sin_precio']} archivo(s) sin precio para su modelo; no "
            "entran en el total",
            file=sys.stderr,
        )
    if detalle:
        print("\n-- por archivo --")
        for d in est["detalle"]:
            costo = f"US$ {d['costo_usd']:.4f}" if d["costo_usd"] is not None else "—"
            img_tok = d["tokens_imagen"] if d["tokens_imagen"] is not None else "?"
            print(
                f"  {Path(d['imagen']).name[:44]:46} img={img_tok:>5} "
                f"texto={d['tokens_texto']:>5} total={d['tokens_entrada']:>5}  {costo}"
            )


def _salidas_en_otras_raices(
    salida: Path, raiz: Path, imagenes: Sequence[Path], sufijo: str
) -> list[Path]:
    """Salidas de **estas** imágenes que ya existen en OTRA ubicación de ``salida``.

    Detecta el caso que hizo pagar dos veces: la misma imagen ya procesada con
    otra raíz de espejado (``salida/2025-08/<hash>/x.json`` vs
    ``salida/<hash>/x.json``). La reanudación no las encuentra —busca la ruta
    calculada— así que el documento se vuelve a pagar sin que nada lo avise.

    ⚠️ El emparejamiento es por **cola de ruta** (carpeta contenedora + nombre),
    no por el nombre suelto ni por «todo lo que no sea la ruta esperada»:
    cualquiera de esas dos cosas marcaría archivos de otros documentos y el aviso
    se volvería ruido que nadie lee.
    """
    if not salida.is_dir():
        return []
    esperadas = {salida_de(img, raiz, salida, sufijo).resolve() for img in imagenes}
    # Cola de cada salida esperada: es lo que sobrevive a un cambio de raíz.
    colas = {(p.parent.name, p.name) for p in esperadas}
    encontradas: list[Path] = []
    for archivo in salida.rglob(f"*.{sufijo}.json"):
        if (archivo.parent.name, archivo.name) not in colas:
            continue
        try:
            if archivo.resolve() in esperadas:
                continue
        except OSError:  # pragma: no cover - rutas raras del sistema
            continue
        encontradas.append(archivo)
    return encontradas


def ya_procesado(destino: Path) -> bool:
    """True si el destino ya tiene un resultado **válido** (no un error).

    ⚠️ Un registro con ``error`` NO cuenta como hecho: si se lo tomara como tal,
    una re-corrida después de arreglar la causa (clave, red, imagen) saltearía ese
    documento y el resumen reportaría éxito. Misma lección que los checkpoints de
    T-603: *un error no es un paso completado*. Un archivo ilegible también se
    reprocesa.
    """
    if not destino.exists():
        return False
    try:
        registro = json.loads(destino.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return False
    return not registro.get("error")


def leer_extracciones_previas(salida: Path) -> dict[str, dict]:
    """Lee las extracciones ya guardadas para que el modo ``diff`` las reutilice."""
    previas: dict[str, dict] = {}
    if not salida.is_dir():
        return previas
    for archivo in salida.rglob("*.extraccion.json"):
        try:
            registro = json.loads(archivo.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            continue
        extraccion = registro.get("extraccion") or registro.get("resultado")
        relativo = registro.get("archivo_relativo")
        if extraccion and relativo:
            previas[relativo] = extraccion
    return previas


# --------------------------------------------------------------------------- #
# Reporte
# --------------------------------------------------------------------------- #


def resumen(registros: Sequence[dict], opciones: Opciones) -> dict[str, Any]:
    """Resumen agregado de **una corrida**: estados, errores, tokens y costo.

    Es el reporte de la corrida (qué se procesó ahora). Para el reporte de
    **gastos** acumulado y con fechas, ver :func:`reporte_de_gastos`.
    """
    estados: dict[str, int] = {}
    errores: list[dict[str, str]] = []
    prompt_tokens = completion_tokens = 0
    cache_hit_tokens = cache_miss_tokens = 0
    tokens_imagen_estimados = 0
    reintentos = 0

    for r in registros:
        if error := r.get("error"):
            errores.append({"origen": r["origen"], "error": error})
            continue
        resultado = r.get("resultado") or {}
        if estado := resultado.get("estado_global"):
            estados[estado] = estados.get(estado, 0) + 1
        uso = r.get("uso") or {}
        prompt_tokens += uso.get("prompt_tokens") or 0
        completion_tokens += uso.get("completion_tokens") or 0
        cache_hit_tokens += uso.get(CLAVE_CACHE_HIT) or 0
        cache_miss_tokens += uso.get(CLAVE_CACHE_MISS) or 0
        reintentos += r.get("reintentos_esquema") or 0
        tokens_imagen_estimados += (r.get("imagen") or {}).get("tokens_estimados") or 0

    entrada, salida = precio_de(opciones.modelo, opciones.precios)
    # La entrada cacheada se cobra a su tarifa: contarla al precio lleno inflaría
    # el gasto de una corrida con el mismo prefijo repetido (el caso del lote).
    p_cache = precio_cache_de(opciones.modelo, opciones.precios_cache)
    costo = costo_de_tokens(
        prompt_tokens,
        completion_tokens,
        entrada,
        salida,
        cache_hit_tokens=cache_hit_tokens,
        precio_cache=p_cache,
    )

    # Señal de calidad de la lectura: comprobantes cuyos importes NO suman el
    # total. Se calcula en Python (no se le cree al modelo), así que es la
    # primera cosa a mirar cuando una extracción parece dudosa.
    aritmetica_ok = aritmetica_mal = aritmetica_nd = 0
    for r in registros:
        if r.get("error"):
            continue
        a = r.get("aritmetica")
        if not a or not a.get("calculable"):
            aritmetica_nd += 1
        elif a["cierra"]:
            aritmetica_ok += 1
        else:
            aritmetica_mal += 1

    return {
        "archivos": len(registros),
        "ok": len(registros) - len(errores),
        "errores": len(errores),
        "estados_globales": estados,
        "aritmetica": {
            "cierra": aritmetica_ok,
            "no_cierra": aritmetica_mal,
            "no_calculable": aritmetica_nd,
        },
        "tokens": {
            "prompt": prompt_tokens,
            "completion": completion_tokens,
            "cache_hit": cache_hit_tokens,
            "cache_miss": cache_miss_tokens,
            "imagen_estimados": tokens_imagen_estimados,
            "costo_usd_estimado": costo,
        },
        "reintentos_esquema": reintentos,
        "opciones": {
            "modo": opciones.modo,
            "modelo": opciones.modelo,
            "detalle_imagen": opciones.detalle,
            # DeepSeek no aplica temperature (thinking mode): se declara tal cual.
            "temperatura": (
                "ignorada (DeepSeek thinking mode)"
                if opciones.temperatura is not None
                else None
            ),
            "salida": str(opciones.salida),
            "prompt_con_ejemplo_de_salida": opciones.incluir_ejemplo,
            "dry_run": opciones.dry_run,
        },
        "detalle_errores": errores[:20],
    }


def _imprimir_resumen(rep: dict) -> None:
    print("\n=== Resumen ===")
    print(f"archivos          : {rep['archivos']}")
    print(f"  ok              : {rep['ok']}")
    print(f"  errores         : {rep['errores']}")
    if rep["estados_globales"]:
        for estado, n in sorted(rep["estados_globales"].items()):
            print(f"    {estado:<13} : {n}")
    arit = rep.get("aritmetica") or {}
    if arit.get("no_cierra"):
        print(
            f"  ⚠ aritmética     : {arit['no_cierra']} comprobante(s) cuyos importes "
            "no suman el total (revisar la lectura)",
            file=sys.stderr,
        )
    elif arit.get("cierra"):
        print(f"  aritmética       : {arit['cierra']} cierran el total")
    t = rep["tokens"]
    if t["prompt"] or t["completion"]:
        print(f"tokens API        : prompt {t['prompt']:,} | completion {t['completion']:,}")
    if t.get("cache_hit"):
        print(
            f"caché de contexto : {t['cache_hit']:,} entrada-caché | "
            f"{t.get('cache_miss', 0):,} entrada-normal (se cobran distinto)"
        )
    if t["imagen_estimados"]:
        print(f"tokens img (est.) : {t['imagen_estimados']:,}")
    if rep.get("reintentos_esquema"):
        print(
            f"  ⚠ reintentos     : {rep['reintentos_esquema']} repregunta(s) por "
            "forma del JSON (cada intento se paga)",
            file=sys.stderr,
        )
    if t["costo_usd_estimado"] is not None:
        print(f"costo (est.)      : US$ {t['costo_usd_estimado']}")
    print(f"modo / modelo     : {rep['opciones']['modo']} / {rep['opciones']['modelo']}")
    if rep["opciones"]["dry_run"]:
        print("modo              : --dry-run (no se llamó a la API ni se escribió nada)")
    for error in rep["detalle_errores"]:
        print(f"  ✗ {error['origen']}: {error['error']}", file=sys.stderr)
    if rep["errores"] > len(rep["detalle_errores"]):
        print(f"  … y {rep['errores'] - len(rep['detalle_errores'])} errores más", file=sys.stderr)


# --------------------------------------------------------------------------- #
# Reporte de gastos (registro contable acumulado)
# --------------------------------------------------------------------------- #
#
# ⚠️ Acá vivía una **segunda** tabla de precios (`PRECIOS_REFERENCIA`), un tipo
# `Precios` de tres elementos y el helper `_fmt_precio`. Los tres duplicaban lo
# que ya está en `costos.py` (`PRECIOS_REFERENCIA`, con la tupla de dos precios
# que el resto del código usa, y `formatear_precio`), y ninguno se leía: dos
# tablas de precios son una deriva silenciosa en el dato que el usuario ve.
# La única fuente es `costos.py`.


def _tz_desde(texto: str) -> tuple[timezone | None, str]:
    """Interpreta ``--tz``: ``local`` (default), ``UTC`` o un offset como ``-03:00``."""
    valor = (texto or "local").strip()
    if valor.lower() in {"local", ""}:
        return None, "local"
    if valor.upper() == "UTC":
        return timezone.utc, "UTC"
    m = re.fullmatch(r"([+-])(\d{1,2}):?(\d{2})?", valor)
    if not m:
        raise ValueError(
            f"--tz inválida: {texto!r}. Usá «local», «UTC» o un offset como «-03:00»."
        )
    signo = -1 if m.group(1) == "-" else 1
    horas, minutos = int(m.group(2)), int(m.group(3) or 0)
    if horas > 23 or minutos > 59:
        raise ValueError(f"--tz inválida: {texto!r}")
    delta = signo * timedelta(hours=horas, minutes=minutos)
    return timezone(delta), f"UTC{m.group(1)}{horas:02d}:{minutos:02d}"


def _a_zona(momento: datetime, tz: timezone | None) -> datetime:
    """Convierte a la zona pedida (o a la local si ``tz`` es ``None``)."""
    return momento.astimezone(tz) if tz is not None else momento.astimezone()


def _parsear_momento(texto: str | None) -> datetime | None:
    """Parsea el ``procesado_utc`` de un registro (tolerante a formatos viejos)."""
    if not texto:
        return None
    limpio = str(texto).strip().replace("Z", "+00:00")
    try:
        momento = datetime.fromisoformat(limpio)
    except ValueError:
        return None
    return momento if momento.tzinfo else momento.replace(tzinfo=timezone.utc)


def _es_gasto(registro: dict) -> bool:
    """True si el registro representa **una llamada paga** a la API.

    Quedan afuera los ``--dry-run`` y el modo ``diff`` (no llaman a la API), y
    todo registro sin ``usage``: no se puede afirmar que gastó si el proveedor no
    lo reportó.

    ⚠️ Sí cuenta un documento con ``error``: en DeepSeek un fallo *después* de
    reintentar por forma del JSON consumió (y facturó) tokens. Y se cuenta **una
    sola vez por documento**, no una vez por intento: los tokens de todos los
    intentos ya vienen sumados en ``uso`` (la conversación es una sola llamada
    lógica con repreguntas), así que un apunte por intento multiplicaría el
    gasto con el **mismo** ``prompt_tokens``. El apunte se marca ``fallo: true``
    para que el reporte lo declare en vez de esconderlo dentro del total.
    """
    if registro.get("dry_run") or registro.get("fuente") == "diff_local":
        return False
    uso = registro.get("uso") or {}
    return (uso.get("prompt_tokens") or uso.get("completion_tokens")) is not None


def apunte_de_registro(
    registro: dict,
    opciones: Opciones,
    *,
    ahora: datetime | None = None,
) -> dict[str, Any] | None:
    """Convierte un registro de salida en un **apunte de gasto**, o ``None``.

    Los registros escritos por versiones anteriores no traen el costo ni la
    ``fuente``, así que se **recalcula** acá desde ``uso`` + la tabla de precios
    vigente (y se declara que el precio es el actual, no el histórico).
    """
    if not _es_gasto(registro):
        return None
    uso = registro.get("uso") or {}
    entrada = uso.get("prompt_tokens") or 0
    salida = uso.get("completion_tokens") or 0
    cache_hit = uso.get(CLAVE_CACHE_HIT) or 0
    modelo = registro.get("modelo") or "desconocido"
    p_entrada, p_salida = precio_de(modelo, opciones.precios)
    p_cache = precio_cache_de(modelo, opciones.precios_cache)
    # Si el registro ya trae el costo (y no hay precios nuevos), se respeta.
    costo = registro.get("costo_usd")
    if costo is None:
        costo = costo_de_tokens(
            entrada,
            salida,
            p_entrada,
            p_salida,
            cache_hit_tokens=cache_hit,
            precio_cache=p_cache,
        )

    momento = _parsear_momento(registro.get("procesado_utc")) or ahora or datetime.now(
        timezone.utc
    )
    local = _a_zona(momento, opciones.tz)
    return {
        "fecha_hora": local.isoformat(timespec="seconds"),
        "fecha": local.date().isoformat(),
        "hora": local.strftime("%H:%M:%S"),
        "documento": registro.get("archivo_relativo") or registro.get("origen"),
        # Identificador estable (sin la raíz de espejado) para deduplicar.
        "documento_id": identificador_documento(registro),
        "origen": registro.get("origen"),
        "modo": registro.get("modo"),
        "fuente": registro.get("fuente"),
        "modelo": modelo,
        "detalle_imagen": (registro.get("imagen") or {}).get("detalle"),
        # Un fallo pagado se declara: el token se consumió igual.
        "fallo": bool(registro.get("error")),
        "error": registro.get("error"),
        "reintentos_esquema": registro.get("reintentos_esquema") or 0,
        "tokens_prompt": entrada,
        "tokens_prompt_cache_hit": cache_hit,
        "tokens_completion": salida,
        "tokens_total": (entrada + salida),
        "tokens_imagen_estimados": (registro.get("imagen") or {}).get(
            "tokens_estimados"
        ),
        "precio_entrada_usd_1m": p_entrada,
        "precio_salida_usd_1m": p_salida,
        "precio_entrada_cache_usd_1m": p_cache,
        "costo_usd": costo,
        "costo_confiable": p_entrada is not None or p_salida is not None,
        "fuente_costo": (
            "registro" if registro.get("costo_usd") is not None else "recalculado"
        ),
        "version_prompt": registro.get("version_prompt"),
        "prompt_hash": registro.get("prompt_hash"),
    }


def identificador_documento(registro: dict) -> str | None:
    """Identificador del documento, **independiente de la raíz de espejado**.

    ⚠️ No sirve ``archivo_relativo``: la misma imagen escrita desde dos raíces
    distintas produce relativos distintos (``2025-08/<hash>/x.jpg`` vs
    ``<hash>/x.jpg``), y entonces el reporte de gastos contaba **dos veces** el
    mismo documento. Se usa la ruta **resuelta del origen** (la imagen real en
    disco), que no cambia según la raíz elegida; si la ruta ya no resuelve
    (lote movido), se cae a la última parte de la ruta, que en este corpus es el
    UUID del documento.
    """
    ruta = registro.get("origen")
    if not ruta:
        return registro.get("archivo_relativo")
    p = Path(str(ruta))
    try:
        return str(p.resolve())
    except OSError:
        return p.name


def _clave_apunte(apunte: dict) -> tuple:
    """Clave de deduplicación de un apunte de gasto.

    **Qué es el mismo gasto**: el mismo documento, con el mismo modo y modelo,
    en la **misma corrida** (misma fecha/hora). Esto es intencional: reprocesar
    un documento (otro día, o con `--forzar`) es una llamada que **se pagó de
    nuevo** y debe contarse aparte — si se colapsara, el reporte escondería
    justamente el gasto que se quiere vigilar.

    **Qué NO es un gasto distinto**: el mismo trabajo escrito en dos lugares por
    cambiar la raíz de espejado. Eso se colapsa acá porque el ``documento_id``
    (la imagen real) y la fecha coinciden, aunque la salida esté en otra carpeta.
    """
    return (
        apunte.get("documento_id"),
        apunte.get("modo"),
        apunte.get("modelo"),
        apunte.get("fuente"),
        apunte.get("fecha_hora"),
    )


def leer_apuntes(salida: Path, opciones: Opciones) -> tuple[list[dict], int]:
    """Lee **todas** las salidas de una carpeta y las convierte en apuntes.

    Recorre ``*.json`` (los tres sufijos: validación, extracción y diff) porque
    el gasto es del **histórico** de la carpeta, no de la última corrida.
    Devuelve ``(apuntes, descartados)``; los descartados son registros que no
    representan una llamada paga (dry-run, fallos, diff) o no se pudieron leer.
    """
    apuntes: dict[tuple, dict] = {}
    descartados = 0
    if not salida.is_dir():
        return [], 0
    for archivo in sorted(salida.rglob("*.json")):
        # Los reportes que el propio script escribe no son registros de gasto.
        if archivo.name in {"reporte.json", "gastos.json"}:
            continue
        try:
            registro = json.loads(archivo.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            descartados += 1
            continue
        if not isinstance(registro, dict):
            descartados += 1
            continue
        # Los reportes que guardan los apuntes ya calculados no se re-cuentan:
        # ya están representados por los registros de documento que los generaron.
        if isinstance(registro.get("apuntes"), list):
            descartados += 1
            continue
        apunte = apunte_de_registro(registro, opciones)
        if apunte is None:
            descartados += 1
            continue
        # Un apunte de fallo nunca se guarda: puede tener el diccionario de error
        # con la lectura cruda del modelo (que es justamente lo que se paga).
        apunte.pop("error", None)
        apuntes.setdefault(_clave_apunte(apunte), apunte)
    return sorted(apuntes.values(), key=lambda a: a["fecha_hora"]), descartados


def totalizar(apuntes: Sequence[dict]) -> dict[str, Any]:
    """Suma el gasto: total y por día, modelo y modo.

    Separa los totales **confiables** (con al menos un precio conocido) de los
    que no lo son, en vez de mezclarlos en un número que parecería exacto.
    """
    # Total, y agrupaciones por día, modelo y modo. Se distingue «sin precio»
    # (no hay ninguno para el modelo) de «precio incompleto» (sólo se conoce el
    # de entrada o el de salida): son cosas distintas y el aviso debe decir cuál.
    por_dia: dict[str, dict[str, Any]] = {}
    por_modelo: dict[str, dict[str, Any]] = {}
    por_modo: dict[str, dict[str, Any]] = {}
    total = {
        "apuntes": 0,
        "tokens_prompt": 0,
        "tokens_prompt_cache_hit": 0,
        "tokens_completion": 0,
        "costo_usd": 0.0,
    }
    sin_precio = 0
    precio_incompleto = 0
    fallos_pagados = 0
    costo_fallos = 0.0

    def _acumular(destino: dict[str, dict[str, Any]], clave: str) -> dict[str, Any]:
        return destino.setdefault(
            clave,
            {"apuntes": 0, "tokens_total": 0, "costo_usd": 0.0, "costo_confiable": True},
        )

    for a in apuntes:
        total["apuntes"] += 1
        total["tokens_prompt"] += a["tokens_prompt"]
        total["tokens_prompt_cache_hit"] += a.get("tokens_prompt_cache_hit") or 0
        total["tokens_completion"] += a["tokens_completion"]
        if a["costo_usd"] is None:
            sin_precio += 1
        else:
            total["costo_usd"] += a["costo_usd"]
            if not a["costo_confiable"]:
                precio_incompleto += 1
        # Un fallo después de reintentar ya consumió tokens: es gasto real, pero
        # no una extracción: se cuenta aparte para poder declararlo.
        if a.get("fallo"):
            fallos_pagados += 1
            costo_fallos += a["costo_usd"] or 0.0

        for destino, clave in (
            (por_dia, a["fecha"]),
            (por_modelo, a["modelo"]),
            (por_modo, a["modo"] or "desconocido"),
        ):
            fila = _acumular(destino, clave)
            fila["apuntes"] += 1
            fila["tokens_total"] += a["tokens_total"]
            if a["costo_usd"] is not None:
                fila["costo_usd"] = round(fila["costo_usd"] + a["costo_usd"], 6)
            if not a["costo_confiable"]:
                fila["costo_confiable"] = False

    total["costo_usd"] = round(total["costo_usd"], 6)
    return {
        "total": total,
        "por_dia": {k: por_dia[k] for k in sorted(por_dia)},
        "por_modelo": {k: por_modelo[k] for k in sorted(por_modelo)},
        "por_modo": {k: por_modo[k] for k in sorted(por_modo)},
        "sin_precio": sin_precio,
        "precio_incompleto": precio_incompleto,
        "fallos_pagados": fallos_pagados,
        "costo_fallos_usd": round(costo_fallos, 6),
        "costo_parcial": (sin_precio + precio_incompleto) > 0,
    }


def _formato_coste(valor: float | None, *, decimal: str = ".") -> str:
    """US$ con 6 decimales (una extracción suele costar centavos)."""
    if valor is None:
        return "—"
    texto = f"{valor:.6f}"
    return texto.replace(".", decimal) if decimal != "." else texto


def imprimir_reporte_gastos(rep: dict[str, Any], apuntes: Sequence[dict]) -> None:
    """Reporte de gastos en stdout: totales, por día, por modelo y por modo."""
    print("\n=== Reporte de gastos (API) ===")
    if not apuntes:
        print("Sin extracciones facturables registradas en la carpeta.")
        return
    t = rep["total"]
    desde = apuntes[0]["fecha"]
    hasta = apuntes[-1]["fecha"]
    print(f"período           : {desde} → {hasta} ({rep['opciones']['tz']})")
    print(f"extracciones      : {t['apuntes']}")
    print(
        f"tokens            : prompt {t['tokens_prompt']:,} "
        f"(de los cuales {t.get('tokens_prompt_cache_hit', 0):,} de caché) | "
        f"completion {t['tokens_completion']:,}"
    )
    print(f"costo total       : US$ {_formato_coste(t['costo_usd'])}")
    if rep.get("fallos_pagados"):
        print(
            f"  ⚠ FALLOS PAGADOS: {rep['fallos_pagados']} intento(s) que terminaron "
            f"en error pero **consumieron tokens** (US$ "
            f"{_formato_coste(rep.get('costo_fallos_usd'))}). Ya están dentro del "
            "costo total; se declaran porque no son extracciones.",
            file=sys.stderr,
        )
    if rep["sin_precio"]:
        print(
            f"  ⚠ SIN PRECIO    : {rep['sin_precio']} extracción(es) no tienen precio "
            f"para su modelo y suman US$ 0. El costo real es MAYOR. Pasá --precios "
            "o --precio-entrada/--precio-salida.",
            file=sys.stderr,
        )
    if rep["precio_incompleto"]:
        print(
            f"  ⚠ PARCIAL       : {rep['precio_incompleto']} extracción(es) sólo "
            "tienen precio de entrada o de salida; el total es un piso.",
            file=sys.stderr,
        )

    print("\n-- por día --")
    for dia, fila in rep["por_dia"].items():
        marca = "" if fila["costo_confiable"] else "  (precio parcial)"
        print(
            f"  {dia}  {fila['apuntes']:>4} ext.  "
            f"{fila['tokens_total']:>9,} tokens  "
            f"US$ {_formato_coste(fila['costo_usd'])}{marca}"
        )

    print("\n-- por modelo --")
    for modelo, fila in rep["por_modelo"].items():
        marca = "" if fila["costo_confiable"] else "  (precio parcial)"
        print(
            f"  {modelo:<18} {fila['apuntes']:>4} ext.  "
            f"US$ {_formato_coste(fila['costo_usd'])}{marca}"
        )

    print("\n-- por modo --")
    for modo, fila in rep["por_modo"].items():
        print(f"  {modo:<10} {fila['apuntes']:>4} ext.  US$ {_formato_coste(fila['costo_usd'])}")


def escribir_csv_gastos(
    ruta: Path, apuntes: Sequence[dict], *, delim: str = ",", decimal: str = "."
) -> None:
    """CSV del gasto **por extracción**, listo para abrir en Excel/Sheets.

    Se usa ``utf-8-sig`` (BOM) para que Excel respete los acentos, y se permite
    delimitador/decimal configurables porque Excel en es-AR espera ``;`` y ``,``.
    """
    campos = [
        "fecha",
        "hora",
        "fecha_hora",
        "documento",
        "documento_id",
        "modo",
        "fuente",
        "modelo",
        "detalle_imagen",
        "fallo",
        "error",
        "reintentos_esquema",
        "tokens_prompt",
        "tokens_prompt_cache_hit",
        "tokens_completion",
        "tokens_total",
        "precio_entrada_usd_1m",
        "precio_salida_usd_1m",
        "precio_entrada_cache_usd_1m",
        "costo_usd",
        "costo_confiable",
        "version_prompt",
        "prompt_hash",
        "origen",
    ]
    ruta.parent.mkdir(parents=True, exist_ok=True)
    with ruta.open("w", encoding="utf-8-sig", newline="") as fh:
        escritor = csv.DictWriter(fh, fieldnames=campos, delimiter=delim, extrasaction="ignore")
        escritor.writeheader()
        for a in apuntes:
            fila = dict(a)
            if decimal != ".":
                for clave in ("costo_usd",):
                    if fila.get(clave) is not None:
                        fila[clave] = f"{fila[clave]:.6f}".replace(".", decimal)
                for clave in (
                    "precio_entrada_usd_1m",
                    "precio_salida_usd_1m",
                    "precio_entrada_cache_usd_1m",
                ):
                    if fila.get(clave) is not None:
                        fila[clave] = f"{fila[clave]:.4f}".replace(".", decimal)
            escritor.writerow(fila)


def reporte_de_gastos(
    salida: Path,
    opciones: Opciones,
    *,
    json_salida: Path | None = None,
    csv_salida: Path | None = None,
    escribir: bool = True,
) -> tuple[dict[str, Any], list[dict]]:
    """Arma el reporte de gastos del **histórico** de la carpeta de salida.

    Es el punto de entrada que faltaba: ``leer_apuntes`` / ``totalizar`` /
    ``imprimir_reporte_gastos`` / ``escribir_csv_gastos`` estaban escritos y
    documentados, pero nadie los llamaba —las banderas ``--reporte-gastos`` y
    ``--csv-gastos`` se registraban en el CLI y no se leían—, así que el reporte
    existía sin ser alcanzable.

    Devuelve ``(reporte, apuntes)``. Con ``escribir=False`` no toca el disco (lo
    usan los tests y un futuro ``--solo-medir`` del reporte).
    """
    apuntes, descartados = leer_apuntes(salida, opciones)
    rep = totalizar(apuntes)
    rep["descartados"] = descartados
    rep["opciones"] = {"tz": opciones.tz_etiqueta, "salida": str(salida)}
    if escribir:
        if json_salida:
            _guardar(json_salida, rep)
        if csv_salida:
            escribir_csv_gastos(
                csv_salida,
                apuntes,
                delim=opciones.csv_delim,
                decimal=opciones.csv_decimal,
            )
    return rep, apuntes


# ---------------------------------------------------------------------------
# Orquestación: de las rutas al resultado
# ---------------------------------------------------------------------------


def _cliente_del_proveedor(proveedor: str, api_key: str | None) -> Any:
    """Cliente del SDK apuntado al endpoint del proveedor.

    Se importa ``openai`` **acá** y no arriba: es una dependencia de los scripts
    operativos, no de la librería. Si falta, el error lo dice con claridad en vez
    de romper el import del paquete entero.
    """
    try:
        from openai import OpenAI
    except ImportError as exc:  # pragma: no cover - depende del entorno
        raise RuntimeError(
            "falta el paquete `openai`: instalalo con `pip install openai` "
            "para usar el laboratorio de LLM externos"
        ) from exc

    cap = proveedor_por_nombre(proveedor).capacidades
    if not api_key:
        raise RuntimeError(
            f"falta la credencial: definí {cap.variable_api_key} en el entorno "
            "o pasala con --api-key"
        )
    return OpenAI(api_key=api_key, base_url=cap.base_url)


def ejecutar(
    *,
    rutas: list[Path],
    opciones: Opciones,
    sistema: str,
    user_template: str,
    datos: dict[str, dict] | None = None,
    proveedor: str = "deepseek",
    api_key: str | None = None,
    limite: int = 0,
    detalle_log: bool = False,
    json_salida: Path | None = None,
) -> int:
    """Corre la operación sobre las rutas y devuelve el código de salida.

    El código es ``0`` si no hubo **ningún** fallo y ``1`` si alguno quedó
    registrado como error: una corrida con documentos fallidos no es exitosa,
    aunque el resto haya salido bien.
    """
    from concurrent.futures import ThreadPoolExecutor

    # La raíz del espejado es explícita (y viene con su motivo para declararlo):
    # derivarla de la ruta pasada hacía que el mismo documento escribiera en dos
    # lugares distintos según cómo se invocara el comando.
    raiz, motivo_raiz = _raiz_espejado(rutas, None, opciones.salida)
    imagenes_rutas = _expandir(rutas, EXTENSIONES_IMAGEN)
    if limite:
        imagenes_rutas = imagenes_rutas[:limite]
    if not imagenes_rutas:
        print("No hay imágenes que procesar.", file=sys.stderr)
        return 0

    datos = datos or {}
    cliente = None
    if not opciones.dry_run and opciones.modo != "diff":
        try:
            cliente = _cliente_del_proveedor(proveedor, api_key)
        except RuntimeError as exc:
            # Falta la credencial o el SDK: es un error de uso, no una falla de
            # la corrida. Se reporta limpio, sin traceback.
            print(f"error: {exc}", file=sys.stderr)
            return 2

    # --dry-run: estima y sale sin llamar a la API ni escribir nada.
    if opciones.dry_run:
        estimacion = estimar_costo_corrida(
            imagenes_rutas, sistema, user_template, opciones
        )
        imprimir_estimacion(estimacion, detalle=detalle_log)
        if json_salida:
            _guardar(json_salida, {"estimacion": estimacion})
        return 0

    # El modo `diff` reutiliza las extracciones previas: no vuelve a pagar.
    extracciones = (
        leer_extracciones_previas(opciones.salida) if opciones.modo == "diff" else {}
    )
    # El sufijo distingue una extracción de una validación: una corrida de
    # `validar` no pisa lo que dejó `extraer`.
    sufijo = {"validar": "validacion", "extraer": "extraccion", "diff": "validacion"}[
        opciones.modo
    ]

    pendientes: list[Path] = []
    for img in imagenes_rutas:
        if not opciones.forzar and ya_procesado(salida_de(img, raiz, opciones.salida, sufijo)):
            continue
        pendientes.append(img)

    # ⚠️ Aviso antes de pagar: si estas imágenes ya tienen salida en OTRA
    # ubicación de la carpeta, la reanudación no las ve y se van a pagar dos
    # veces (es el bug que costó 5 documentos duplicados). No se aborta porque
    # puede ser intencional (otra raíz pedida a propósito), pero se declara.
    if pendientes:
        repetidas = _salidas_en_otras_raices(
            opciones.salida, raiz, pendientes, sufijo
        )
        if repetidas:
            print(
                f"⚠  {len(repetidas)} imagen(es) de este lote ya tienen salida en "
                "OTRA ubicación de la carpeta de salida. La reanudación no las "
                "encuentra, así que se van a volver a pagar:",
                file=sys.stderr,
            )
            for archivo in repetidas[:5]:
                print(f"      {archivo}", file=sys.stderr)
            if len(repetidas) > 5:
                print(f"      … y {len(repetidas) - 5} más", file=sys.stderr)

    print(f"a procesar: {len(pendientes)} de {len(imagenes_rutas)}  (raíz: {motivo_raiz})")
    print()

    registros: list[dict[str, Any]] = []

    def _una(img: Path) -> dict[str, Any]:
        return procesar(
            img, raiz, cliente, sistema, user_template, datos, extracciones,
            opciones, proveedor,
        )

    with ThreadPoolExecutor(max_workers=max(1, opciones.workers)) as pool:
        for registro in pool.map(_una, pendientes):
            registros.append(registro)
            destino = salida_de(Path(registro["origen"]), raiz, opciones.salida, sufijo)
            if registro.get("error"):
                print(
                    f"  ✗ {Path(registro['origen']).name}: "
                    f"{str(registro['error'])[:80]}",
                    file=sys.stderr,
                )
            else:
                _guardar(destino, registro)
                if detalle_log:
                    print(f"  ✓ {Path(registro['origen']).name}")

    rep = resumen(registros, opciones)
    _imprimir_resumen(rep)
    if json_salida:
        _guardar(json_salida, rep)
    return 1 if rep.get("errores") else 0


# ---------------------------------------------------------------------------
# Persistencia
# ---------------------------------------------------------------------------


def _guardar(destino: Path, contenido: dict[str, Any]) -> None:
    """Escribe un JSON con **escritura atómica** (tmp + replace).

    Un archivo a medio escribir es peor que no tenerlo: la reanudación lo leería
    como "hecho" y el dato quedaría corrupto sin que nadie lo note.
    """
    escribir_json_atomico(destino, contenido)

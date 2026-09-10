"""Prompt corto versionado del gate qween (F2 / T-202, épica E-QWE-1).

La decisión binaria "¿es comprobante?" (3 salidas) se hace con un **prompt
corto** sobre la vista de decisión (F2-subplan §3.2). Este módulo congela el
**texto del prompt** (versionado — ADR-005) y la construcción de los
``messages`` de ``OllamaClient.ask`` a partir de una :class:`VistaPreparada`
(T-201), separando el *qué se le pide* (este módulo) del *cómo se decide*
(parseo a :class:`VeredictoGate` en ``qween.decidir_es_comprobante``, T-202).

El texto del prompt se porta de la idea fuente ``docs/ideas/qween.md`` §1
("Fase de validación rápida: ¿es comprobante?") y pide una respuesta breve
orientada a decisión con **tres salidas** (``comprobante | no_comprobante |
indeterminado``). El prompt es **determinista en la salida**: pide solo la
etiqueta, sin explicar; la normalización/tolerancia de la respuesta la hace el
paso siguiente (T-202).

Formato de la imagen en ``messages`` (T-202):
  - Vista con ``ruta_imagen_original`` (imagen): el mensaje ``user`` agrega
    ``images`` con la imagen en **base64**
    (``{"role": "user", "content": ..., "images": [<base64>]}``). Es el formato
    que la API ``/api/chat`` de Ollama espera para el modelo local
    ``qwen2.5vl:3b`` de este entorno: **validado empíricamente** — enviar la
    ruta (absoluta o relativa) en ``images`` devuelve HTTP 400 ``illegal
    base64 data`` porque Ollama interpreta cada ítem de ``images`` como base64.
    Precedente del repo: ``v1/document_extraction.py`` (modalidad VLM) usa el
    mismo patrón (``base64.b64encode(ruta.read_bytes()).decode("ascii")``).
    **Antes de codificar se REDUCE la imagen** al lado mayor objetivo de la
    vista (:data:`LADO_MAYOR_OBJETIVO_POR_VISTA`: rápida 512 px / revisión
    1024 px / fiel sin reducción), con Pillow (best-effort). Esto realiza de
    verdad la vista barata (E-QWE-1: no transportar la imagen completa) y
    **evita exceder el ``num_ctx`` del VLM** (una imagen grande sin reducir
    supera los 4096 tokens del ``qwen2.5vl:3b`` y Ollama responde HTTP 400
    ``exceed_context_size_error``). Las dimensiones se calculan con
    :func:`dimensiones_objetivo_vlm`, que usa el **mismo ``smart_resize`` de
    Qwen2.5-VL** que Docling (múltiplos de :data:`FACTOR_PATCH_QWEN2VL`), para
    que el servidor no re-escale y el conteo de tokens sea predecible. Ver
    :func:`imagen_envio_base64`.
  - Vista textual (``ruta_imagen_original is None``): el markdown /
    representación de F1 va en ``content`` (F2-subplan §2.4: no aplica
    thumbnail).
"""

from __future__ import annotations

import base64
import io
import math
from pathlib import Path
from typing import Any

from .vistas import (
    RESOLUCION_VISTA_RAPIDA_PX,
    RESOLUCION_VISTA_REVISION_PX,
    VistaPreparada,
)

#: Versión del prompt corto del gate (ADR-005: trazabilidad de prompt por
#: versión/hash). Se registra en ``EvidenceField.meta.version_prompt`` vía
#: ``nueva_meta`` en ``qween._construir_evidencia_gate`` para poder auditar qué
#: prompt produjo cada decisión (E-CONC-5 / ADR-005).
#
# Historial de versiones:
#   - ``qween-gate@1``: prompt inicial (3 salidas) portado de la idea qween.md.
#   - ``qween-gate@2``: define qué ES y qué NO ES un comprobante. Fix de falsos
#     positivos: el modelo clasificaba como "comprobante" capturas de pantalla
#     de sistemas/apps que muestran un movimiento financiero y piden la factura
#     (caso golden ``2926bed9``), solo porque el texto contenía las palabras
#     "comprobante"/"impuesto"/montos. Ahora se aclara que la captura de un
#     movimiento/consulta NO es el comprobante en sí.
VERSION_PROMPT_QWEEN = "qween-gate@2"

#: System prompt del gate "¿es comprobante?" (E-QWE-1). Portado de la idea
#: fuente ``docs/ideas/qween.md`` §1, con definición de qué ES / qué NO ES un
#: comprobante (v2, fix de falsos positivos — caso ``2926bed9``). Pide una
#: sola etiqueta de tres opciones (``comprobante | no_comprobante |
#: indeterminado``), sin explicación.
SYSTEM_PROMPT_QWEEN = (
    "Analiza esta imagen/documento de forma rápida.\n"
    "Tu tarea es decidir si corresponde a un COMPROBANTE FISCAL O COMERCIAL "
    "válido para rendición de gastos.\n"
    "\n"
    "Se considera COMPROBANTE únicamente el documento emitido por el "
    "proveedor/vendedor que acredita la operación: factura, ticket, recibo "
    "oficial, boleto, nota de crédito/débito, o comprobante electrónico "
    "(con o sin discriminación de IVA).\n"
    "\n"
    "NO es comprobante (responder no_comprobante):\n"
    "- Capturas de pantalla de apps, billeteras, home banking o sistemas que "
    "solo MUESTRAN un movimiento, un pago, una transferencia o una consulta "
    "y piden/adjuntan la factura aparte.\n"
    "- Extractos bancarios, resúmenes de tarjeta, listados o reportes.\n"
    "- Pantallas de aprobación, mensajes, chats, correos o avisos.\n"
    "- Documentos personales (DNI, licencia), selfies, fotos ajenas al gasto, "
    "memos o capturas sin datos fiscales del emisor.\n"
    "\n"
    "Regla práctica: si la imagen es solo una captura de un sistema que "
    "describe un movimiento y NO es el documento emitido por el comercio, "
    "entonces no_comprobante.\n"
    "\n"
    "Responder solo con una de estas opciones:\n"
    "- comprobante\n"
    "- no_comprobante\n"
    "- indeterminado\n"
    "\n"
    "No expliques demasiado. Solo decide."
)

#: Contenido del mensaje ``user`` cuando la vista es una imagen: el artefacto
#: va en ``images`` (base64) y este texto corto acompaña a la imagen para el VLM.
USER_SOLO_IMAGEN = (
    "Analizá la imagen adjunta y decidí si es un comprobante fiscal o "
    "comercial. Respondé solo con una de las tres opciones indicadas."
)

#: Plantilla del mensaje ``user`` cuando la vista es textual (markdown de F1).
USER_TEXTO = (
    "Analizá el siguiente documento (markdown) y decidí si es un comprobante "
    "fiscal o comercial. Respondé solo con una de las tres opciones "
    "indicadas.\n\n--- DOCUMENTO ---\n{documento}\n--- FIN DOCUMENTO ---"
)

#: Lado mayor objetivo (px) de la imagen que se envía al modelo, por tipo de
#: vista (decisión barata E-QWE-1 / doble calidad E-QWE-2):
#:   - ``rapida``   → 512 px  (máxima reducción: la decisión es barata).
#:   - ``revision`` → 1024 px (detalle intermedio para la 2ª pasada).
#:   - ``fiel``     → ``None`` (sin reducción: máxima fidelidad para F4).
#: El valor ``None`` significa "no reducir" (la vista fiel no se degrada);
#: cualquier otro valor es el lado mayor objetivo tras el remuestreo.
LADO_MAYOR_OBJETIVO_POR_VISTA: dict[str, int | None] = {
    "rapida": RESOLUCION_VISTA_RAPIDA_PX,
    "revision": RESOLUCION_VISTA_REVISION_PX,
    "fiel": None,
}

#: Calidad JPEG (0-100) de la imagen reducida enviada al modelo. 80 conserva
#: legibilidad de texto/QR/sellos en la vista barata sin inflar el payload.
CALIDAD_JPEG_ENVIO = 80

#: Piso del lado menor (px) de la imagen reducida: evita perder detalle en
#: imágenes muy alargadas (p. ej. capturas panorámicas) al bajar el lado mayor.
LADO_MENOR_MINIMO_ENVIO_PX = 256

#: Factor de patch*merge de Qwen2.5-VL (``patch_size=14`` × ``merge_size=2``).
#: El preprocesador del modelo (``smart_resize``) redondea las dimensiones a
#: múltiplos de este valor; alinearse evita que el servidor re-escale la imagen
#: y hace **predecible** el conteo de tokens que consume (mitiga el
#: ``exceed_context_size_error`` de Ollama; T-202/T-203).
FACTOR_PATCH_QWEN2VL = 28


def dimensiones_objetivo_vlm(
    ancho: int,
    alto: int,
    lado_mayor_objetivo: int,
    *,
    lado_menor_minimo: int = LADO_MENOR_MINIMO_ENVIO_PX,
) -> tuple[int, int]:
    """Dimensiones objetivo alineadas al preprocesador de Qwen2.5-VL (T-202/T-203).

    Usa ``docling.utils.vlm_utils.compute_qwen2vl_image_size`` — el **mismo**
    ``smart_resize`` que Docling aplica en su pipeline VLM — para calcular las
    dimensiones finales: clampa el lado mayor a ``lado_mayor_objetivo`` y
    redondea a múltiplos de :data:`FACTOR_PATCH_QWEN2VL`, de modo que el
    servidor no re-escale la imagen y el conteo de tokens sea predecible.

    Se pasa ``min_pixels=0`` para **no agrandar** imágenes chicas (la vista
    barata de E-QWE-1 no debe inflar píxeles/tokens; el presupuesto mínimo real
    del modelo lo aplica el servidor). El **piso del lado menor**
    (``lado_menor_minimo``) se respeta subiendo el ``max_size`` efectivo en
    imágenes muy alargadas, sin agrandar la imagen (nunca supera sus
    dimensiones originales).

    Fallback: si la utilidad de Docling no está disponible (API semi-interna
    que puede cambiar entre versiones; se usa en ``docling/pipeline/
    vlm_pipeline.py``), se calcula por lado mayor con la grilla sin alinear
    (comportamiento previo).

    Devuelve ``(ancho_objetivo, alto_objetivo)``.
    """
    lado_mayor = max(ancho, alto)
    lado_menor = min(ancho, alto)

    # Piso del lado menor: en imágenes muy alargadas, reducir a
    # ``lado_mayor_objetivo`` dejaría el lado menor por debajo del piso; se sube
    # el ``max_size`` efectivo lo justo para respetarlo (sin agrandar). Como las
    # dimensiones resultantes son múltiplos de ``FACTOR_PATCH_QWEN2VL``, el piso
    # se alinea **hacia arriba** a la grilla (p. ej. 256 → 280) para que el
    # resultado lo cumpla realmente y no quede en el múltiplo inferior (252).
    max_size = lado_mayor_objetivo
    if lado_mayor and lado_menor and lado_menor >= lado_menor_minimo:
        piso_grilla = (
            math.ceil(lado_menor_minimo / FACTOR_PATCH_QWEN2VL) * FACTOR_PATCH_QWEN2VL
        )
        requerido = math.ceil(piso_grilla * lado_mayor / lado_menor)
        if requerido > max_size:
            max_size = min(requerido, lado_mayor)  # nunca agranda

    try:
        from docling.utils.vlm_utils import compute_qwen2vl_image_size

        tam = compute_qwen2vl_image_size(
            width=ancho,
            height=alto,
            max_size=max_size,
            min_pixels=0,  # E-QWE-1: no agrandar la vista barata
        )
        return int(tam.width), int(tam.height)
    except Exception:  # API de Docling no disponible → escalado por lado mayor
        escala = min(1.0, max_size / lado_mayor) if lado_mayor else 1.0
        return max(1, round(ancho * escala)), max(1, round(alto * escala))


def _bytes_imagen_para_envio(
    ruta: str | Path, lado_mayor_objetivo: int | None
) -> tuple[bytes, dict[str, Any]]:
    """Bytes de la imagen a enviar al modelo (reducida si aplica).

    Reduce la imagen con Pillow (best-effort: si no está disponible o la imagen
    no se puede abrir, devuelve los bytes originales) a las dimensiones que
    calcula :func:`dimensiones_objetivo_vlm` — alineadas al ``smart_resize`` de
    Qwen2.5-VL (múltiplos de :data:`FACTOR_PATCH_QWEN2VL`, sin agrandar,
    respetando el piso del lado menor) — y la guarda como JPEG de calidad
    :data:`CALIDAD_JPEG_ENVIO`. Respeta la orientación EXIF.
    ``lado_mayor_objetivo`` ``None`` = sin reducción (vista fiel).

    Devuelve ``(datos, info)``; ``info`` traza la reducción (dimensiones y
    pesos original/envío, alineación) para la evidencia y los reportes.
    """
    ruta = Path(ruta)
    datos_originales = ruta.read_bytes()

    if lado_mayor_objetivo is None:
        return datos_originales, {
            "reducida": False,
            "motivo": "sin reducción (vista fiel: máxima fidelidad E-QWE-2)",
            "peso_original_bytes": len(datos_originales),
        }

    try:
        from PIL import Image, ImageOps
    except ImportError:
        return datos_originales, {
            "reducida": False,
            "motivo": "Pillow no disponible; se envía la imagen sin reducir",
            "peso_original_bytes": len(datos_originales),
        }

    try:
        with Image.open(ruta) as img:
            img = ImageOps.exif_transpose(img)  # respeta orientación EXIF
            ancho, alto = img.size
            img = img.convert("RGB")
            # Solo se reduce si la imagen supera el objetivo: si ya es más
            # chica no se toca (evita re-encodear/degradar y no agranda;
            # E-QWE-1). Cuando se reduce, las dimensiones se alinean al
            # preprocesador de Qwen2.5-VL (múltiplos de 28) para que el
            # servidor no re-escale y el conteo de tokens sea predecible.
            if max(ancho, alto) <= lado_mayor_objetivo:
                return datos_originales, {
                    "reducida": False,
                    "motivo": (
                        f"imagen {ancho}x{alto}px ya es <= objetivo "
                        f"{lado_mayor_objetivo}px; sin reducir"
                    ),
                    "peso_original_bytes": len(datos_originales),
                }
            nuevo_ancho, nuevo_alto = dimensiones_objetivo_vlm(
                ancho, alto, lado_mayor_objetivo
            )
            reducida = img.resize(
                (nuevo_ancho, nuevo_alto), Image.Resampling.LANCZOS
            )
            buf = io.BytesIO()
            reducida.save(buf, "JPEG", quality=CALIDAD_JPEG_ENVIO, optimize=True)
            datos = buf.getvalue()
            return datos, {
                "reducida": True,
                "ancho_original": ancho,
                "alto_original": alto,
                "ancho_envio": nuevo_ancho,
                "alto_envio": nuevo_alto,
                "alineado_qwen2vl": True,
                "factor_patch": FACTOR_PATCH_QWEN2VL,
                "peso_original_bytes": len(datos_originales),
                "peso_envio_bytes": len(datos),
            }
    except Exception as exc:  # imagen ilegible/formatos raros → original
        return datos_originales, {
            "reducida": False,
            "motivo": (
                f"no se pudo reducir ({type(exc).__name__}: {exc}); "
                "se envía la imagen original"
            ),
            "peso_original_bytes": len(datos_originales),
        }


def imagen_envio_base64(vista: VistaPreparada) -> tuple[str, dict[str, Any]]:
    """Base64 de la imagen de la vista para el envío al modelo (T-202/T-203).

    Reduce la imagen al lado mayor objetivo de la vista
    (:data:`LADO_MAYOR_OBJETIVO_POR_VISTA`: rápida 512 px / revisión 1024 px /
    fiel sin reducción) **antes** de codificar, para que la decisión barata
    (E-QWE-1) no transporte la imagen completa y no exceda el ``num_ctx`` del
    VLM. Devuelve ``(base64, info)`` con la trazabilidad de la reducción
    (dimensiones y pesos original/envío).

    Lanza:
        ``ValueError`` si la vista es textual (no tiene ``ruta_imagen_original``)
        o si no se pudo leer la imagen.
    """
    if not vista.ruta_imagen_original:
        raise ValueError(
            "imagen_envio_base64(): la vista no tiene imagen "
            "(ruta_imagen_original es None); es una vista textual (T-202/T-203)."
        )
    lado_mayor_objetivo = LADO_MAYOR_OBJETIVO_POR_VISTA.get(
        vista.tipo_vista, RESOLUCION_VISTA_RAPIDA_PX
    )
    try:
        datos, info = _bytes_imagen_para_envio(
            vista.ruta_imagen_original, lado_mayor_objetivo
        )
    except OSError as exc:
        raise ValueError(
            "imagen_envio_base64(): no se pudo leer la imagen de la "
            f"vista '{vista.ruta_imagen_original}' (T-202/T-203): {exc}"
        ) from exc
    return base64.b64encode(datos).decode("ascii"), info


def construir_messages_gate(vista: VistaPreparada) -> list[dict[str, Any]]:
    """Construye los ``messages`` de ``OllamaClient.ask`` para la vista (T-202).

    Devuelve ``[system, user]`` con el system prompt del gate
    (:data:`SYSTEM_PROMPT_QWEEN`) y, según la vista:

      - Imagen (``ruta_imagen_original``): el mensaje ``user`` incluye
        ``images`` con la imagen **reducida** (según el tipo de vista) y
        codificada en **base64** (formato que exige la API ``/api/chat`` de
        Ollama para el modelo local — validado empíricamente) y un texto corto
        de acompañamiento (:data:`USER_SOLO_IMAGEN`).
      - Textual (``ruta_imagen_original is None``): el mensaje ``user`` incluye
        el markdown/representación de la vista (:data:`USER_TEXTO`).

    Lanza:
        ``ValueError`` si la vista es textual y no trae contenido (no hay
        representación sobre la cual decidir).
    """
    system: dict[str, Any] = {"role": "system", "content": SYSTEM_PROMPT_QWEEN}
    if vista.ruta_imagen_original:
        # Reducción real al lado mayor objetivo de la vista (E-QWE-1) + base64.
        # Ollama /api/chat (modelo local qwen2.5vl:3b) espera la imagen en
        # base64 y una imagen grande sin reducir excede el num_ctx del modelo.
        imagen_b64, _info = imagen_envio_base64(vista)
        user: dict[str, Any] = {
            "role": "user",
            "content": USER_SOLO_IMAGEN,
            "images": [imagen_b64],
        }
    else:
        markdown = (vista.representacion or "").strip()
        if not markdown:
            raise ValueError(
                "construir_messages_gate(): la vista textual no tiene "
                "markdown/representación para construir el mensaje (T-202)."
            )
        user = {
            "role": "user",
            "content": USER_TEXTO.format(documento=markdown),
        }
    return [system, user]


__all__ = [
    "VERSION_PROMPT_QWEEN",
    "SYSTEM_PROMPT_QWEEN",
    "USER_SOLO_IMAGEN",
    "USER_TEXTO",
    "LADO_MAYOR_OBJETIVO_POR_VISTA",
    "CALIDAD_JPEG_ENVIO",
    "LADO_MENOR_MINIMO_ENVIO_PX",
    "FACTOR_PATCH_QWEN2VL",
    "dimensiones_objetivo_vlm",
    "imagen_envio_base64",
    "construir_messages_gate",
]

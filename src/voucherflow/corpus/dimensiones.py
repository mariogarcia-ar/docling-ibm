"""Dimensiones objetivo y tokens de visión, alineados a Qwen2.5-VL.

**Alineación al VLM (el punto fino).** Cuando el objetivo es un modelo de
visión, las dimensiones no dan igual: el preprocesador de Qwen2.5-VL
(``smart_resize``) redondea a múltiplos de 28 (``patch_size=14`` ×
``merge_size=2``) y **re-escalea** si no coincide, con lo que el conteo de
tokens deja de ser predecible. Acá se calculan las dimensiones con
``voucherflow.validation.prompt_qween.dimensiones_objetivo_vlm`` — el **mismo**
``smart_resize`` que usa el pipeline (F2/T-202/T-203) — para que el servidor
no re-escale. Si la librería no se puede importar, cae a una alineación local
equivalente (múltiplos de 28). Los defaults (1024 px / calidad 80 / piso de lado
menor 256) también se toman de la librería si está disponible: **una sola fuente
de verdad** para el presupuesto de la vista de revisión (E-QWE-2).

**Tokens estimados.** El reporte estima los tokens de visión como
``ceil(ancho/28) * ceil(alto/28)``, que es el gridding de Qwen2.5-VL para
imágenes ya alineadas. Es una **estimación** (el overhead del template y del
texto del prompt no se cuenta), útil para comparar antes/después, no una
medición del servidor.

⚠️ **Ojo con el OCR.** Reducir *antes* de un OCR clásico (RapidOCR/EasyOCR vía
Docling) puede degradar la letra chica: el OCR lee píxeles. Bajá la reducción
(``--lado-mayor 1536`` o ``2048``) o usá el modo de solo medición para decidir
antes de tocar el corpus. Para el camino **VLM** (la imagen viaja al modelo)
reducir es lo correcto: el pipeline ya lo hace al enviar
(``_bytes_imagen_para_envio``, vista rápida 512 / revisión 1024), así que este
módulo sirve para **pre-reducir el corpus en disco** (almacenamiento, OCR más
rápido, o alimentar otra herramienta).
"""

from __future__ import annotations

import math

# --- Defaults (una sola fuente de verdad: la librería si se puede importar) --

#: Piso del lado menor: evita perder detalle en imágenes muy alargadas.
LADO_MENOR_MINIMO_PX = 256
#: Factor de patch*merge de Qwen2.5-VL (``patch_size=14`` × ``merge_size=2``).
FACTOR_PATCH_QWEN2VL = 28
#: Lado mayor objetivo por defecto: la resolución de la **vista de revisión**
#: de la librería (E-QWE-2).
LADO_MAYOR_PX = 1024
#: Calidad de reencode por defecto.
CALIDAD = 80
#: De dónde salieron los defaults efectivos (se declara en el reporte).
ORIGEN_DEFAULTS = "valores locales del paquete (librería no importable)"

#: ``dimensiones_objetivo_vlm`` de la librería, si está disponible (reutiliza el
#: ``smart_resize`` de Qwen2.5-VL que Docling aplica en su pipeline VLM).
_dimensiones_libreria = None

try:  # pragma: no cover - depende del entorno de importación
    from voucherflow.validation.prompt_qween import (  # noqa: E402
        CALIDAD_JPEG_ENVIO,
        LADO_MENOR_MINIMO_ENVIO_PX,
        RESOLUCION_VISTA_REVISION_PX,
        dimensiones_objetivo_vlm,
    )
    from voucherflow.validation.prompt_qween import (
        FACTOR_PATCH_QWEN2VL as _FACTOR_LIB,
    )

    LADO_MENOR_MINIMO_PX = LADO_MENOR_MINIMO_ENVIO_PX
    FACTOR_PATCH_QWEN2VL = _FACTOR_LIB
    LADO_MAYOR_PX = RESOLUCION_VISTA_REVISION_PX
    CALIDAD = CALIDAD_JPEG_ENVIO
    _dimensiones_libreria = dimensiones_objetivo_vlm
    ORIGEN_DEFAULTS = "librería (voucherflow.validation.prompt_qween)"
except Exception:  # noqa: BLE001 - el paquete no debe romper al importar
    pass


# --------------------------------------------------------------------------- #
# Cálculo de dimensiones y estimación de tokens
# --------------------------------------------------------------------------- #


def dimensiones_objetivo(
    ancho: int,
    alto: int,
    lado_mayor: int,
    *,
    piso_lado_menor: int = LADO_MENOR_MINIMO_PX,
    alinear: bool = True,
) -> tuple[int, int]:
    """Dimensiones destino para una imagen de ``ancho`` x ``alto``.

    Nunca agranda: si la imagen ya entra en ``lado_mayor`` devuelve sus
    dimensiones originales. Reutiliza ``dimensiones_objetivo_vlm`` de la
    librería (mismo ``smart_resize`` de Qwen2.5-VL) y, si no está disponible,
    cae a una alineación local a múltiplos de :data:`FACTOR_PATCH_QWEN2VL`.
    """
    if lado_mayor <= 0:
        return ancho, alto
    lado_mayor_real = max(ancho, alto)
    if lado_mayor_real <= lado_mayor:
        return ancho, alto

    if _dimensiones_libreria is not None:
        try:
            nuevo = _dimensiones_libreria(
                ancho, alto, lado_mayor, lado_menor_minimo=piso_lado_menor
            )
            n_ancho, n_alto = int(nuevo[0]), int(nuevo[1])
            if 0 < n_ancho <= ancho and 0 < n_alto <= alto:
                return n_ancho, n_alto
        except Exception:  # noqa: BLE001 - API semi-interna de Docling
            pass

    return _dimensiones_local(
        ancho, alto, lado_mayor, piso_lado_menor, alinear=alinear
    )


def _dimensiones_local(
    ancho: int,
    alto: int,
    lado_mayor: int,
    piso_lado_menor: int,
    *,
    alinear: bool = True,
) -> tuple[int, int]:
    """Alineación local (fallback): reduce por lado mayor, sin agrandar.

    Respeta el piso del lado menor subiendo el ``max_size`` efectivo en
    imágenes muy alargadas (nunca por encima del tamaño original).
    """
    lado_mayor_real = max(ancho, alto)
    lado_menor_real = min(ancho, alto)
    max_size = lado_mayor
    if lado_menor_real >= piso_lado_menor and lado_mayor_real:
        requerido = math.ceil(piso_lado_menor * lado_mayor_real / lado_menor_real)
        if requerido > max_size:
            max_size = min(requerido, lado_mayor_real)

    escala = min(1.0, max_size / lado_mayor_real) if lado_mayor_real else 1.0
    n_ancho = max(1, round(ancho * escala))
    n_alto = max(1, round(alto * escala))
    if alinear:
        f = FACTOR_PATCH_QWEN2VL
        n_ancho = max(f, round(n_ancho / f) * f)
        n_alto = max(f, round(n_alto / f) * f)
        # La alineación podría haber agrandado respecto del original.
        n_ancho, n_alto = min(n_ancho, ancho), min(n_alto, alto)
    return n_ancho, n_alto


def tokens_estimados_vlm(ancho: int, alto: int) -> int:
    """Tokens de visión estimados para Qwen2.5-VL con la imagen ya alineada.

    ``gridding`` de ``patch_size=14`` con ``merge_size=2`` ⇒ cada token cubre
    28x28 px de la imagen alineada. Es una **estimación** de comparación
    (no incluye el template ni el texto del prompt).
    """
    return math.ceil(ancho / FACTOR_PATCH_QWEN2VL) * math.ceil(
        alto / FACTOR_PATCH_QWEN2VL
    )

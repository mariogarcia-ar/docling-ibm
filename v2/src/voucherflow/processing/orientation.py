"""Orientación de un documento por boxes (F1 / T-103, épica E-DOC-2).

Detecta si la orientación predominante de un documento es **horizontal** o
**vertical** a partir de los ``Box`` ya extraídos (doc 03 §4.1: "Detección de
orientación + rotación"; E-DOC-2 regla "orientación"). Es la versión v2 del
``get_dominant_orientation``/``get_orientation_for_item`` de ``v1/lib/
orientation.py``, pero operando sobre la lista de :class:`Box` del contrato
``ProcessedDocument`` (no sobre el documento Docling crudo).

Semántica (idéntica a v1):
  - Cada box es ``horizontal`` si su ancho >= su alto (el texto corre en el
    eje X), ``vertical`` en caso contrario.
  - La orientación del documento es la **dominante**; empate → ``horizontal``.
  - Si la dominante es ``vertical``, el documento está rotado ~90° y la salida
    debe ordenarse por ``center_x`` (T-104 lo consume) — ver doc 02 E-DOC-3.
"""

from __future__ import annotations

from ..models.docling import Box

#: Valores canónicos de orientación (doc 03 §4.1 / E-DOC-2).
ORIENTACION_HORIZONTAL = "horizontal"
ORIENTACION_VERTICAL = "vertical"

#: Orientaciones válidas.
ORIENTACIONES_VALIDAS = frozenset({ORIENTACION_HORIZONTAL, ORIENTACION_VERTICAL})


def orientacion_por_box(box: Box) -> str | None:
    """Devuelve la orientación de un box según sus dimensiones (v1).

    Un box es ``horizontal`` si su ancho >= su alto; ``vertical`` en caso
    contrario. Devuelve ``None`` si el box no tiene ``bbox`` (sin posición).
    """
    if box.bbox is None:
        return None
    l, t, r, b = box.bbox
    ancho = abs(r - l)
    alto = abs(b - t)  # coord. normalizadas; el signo del alto no importa
    return ORIENTACION_HORIZONTAL if ancho >= alto else ORIENTACION_VERTICAL


def detectar_orientacion(boxes: list[Box]) -> str:
    """Devuelve la orientación dominante de un documento (v1 get_dominant_orientation).

    Cuenta cuántos boxes son ``horizontal`` vs ``vertical`` y devuelve la
    dominante. Si no hay boxes con posición o hay empate, devuelve
    ``horizontal`` (default de lectura; coincide con v1).

    Argumentos:
        boxes: lista de :class:`Box` del ``ProcessedDocument``.

    Devuelve:
        ``"horizontal"`` o ``"vertical"``.
    """
    conteo = {ORIENTACION_HORIZONTAL: 0, ORIENTACION_VERTICAL: 0}
    for box in boxes:
        orient = orientacion_por_box(box)
        if orient is not None:
            conteo[orient] += 1

    if conteo[ORIENTACION_VERTICAL] > conteo[ORIENTACION_HORIZONTAL]:
        return ORIENTACION_VERTICAL
    return ORIENTACION_HORIZONTAL


def requiere_rotacion(boxes: list[Box]) -> bool:
    """Indica si el documento está rotado y hay que corregir la lectura.

    Devuelve ``True`` cuando la orientación dominante es ``vertical`` (el
    documento se leyó rotado 90°). El orden de lectura correcto lo aplica el
    exportador (T-104) usando ``center_x`` en ese caso (E-DOC-3).
    """
    return detectar_orientacion(boxes) == ORIENTACION_VERTICAL


def orientacion_de(boxes: list[Box] | None) -> str:
    """Atajo: orientación dominante de ``boxes`` (o ``horizontal`` si es None).

    Pensado para completar ``ProcessedDocument.orientacion`` tras la
    conversión Docling sin boxes (p. ej. texto nativo/office).
    """
    return detectar_orientacion(boxes or [])


__all__ = [
    "ORIENTACION_HORIZONTAL",
    "ORIENTACION_VERTICAL",
    "ORIENTACIONES_VALIDAS",
    "orientacion_por_box",
    "detectar_orientacion",
    "requiere_rotacion",
    "orientacion_de",
]

"""Exportador de Markdown ordenado por posición (F1 / T-104, épica E-DOC-3).

Porta el algoritmo de ``v1/lib/orientation.py`` (``export_orientation_text``)
de forma **byte-compatible**, pero operando sobre la lista de :class:`Box` del
contrato ``ProcessedDocument`` (no sobre el documento Docling crudo).

Reglas (doc 03 §4.1 y E-DOC-3; subplan F1 §5):

  - Agrupar en **líneas** por ``center_y`` (horizontal) o ``center_x``
    (vertical), con tolerancia **25.0**.
  - Horizontal: líneas ordenadas ``reverse=True`` (de abajo hacia arriba, como
    v1); dentro de la línea por ``left`` ascendente.
  - Vertical: líneas por ``center_x`` sin reverse; dentro por ``center_y`` con
    reverse.
  - Tablas (``Box.es_tabla``) → ítem único con su Markdown exportado y
    orientación forzada horizontal (paridad v1: ``label=='table'``).
  - Join de línea con ``' | '``, ``'\n'`` entre líneas y ``'\n'`` final.
  - Sin boxes → ``"No se encontraron textos en orientación {orient}.\n"``.

Nota sobre coordenadas: v1 trabajaba con coordenadas Docling en puntos (escala
de página). El ``Box`` de F0 conserva esas coordenadas tal cual (ver
``convert()``), por lo que la tolerancia 25.0 se mantiene con el mismo
significado.
"""

from __future__ import annotations

from ..models.docling import Box

#: Tolerancia de agrupación en línea (subplan F1 §5, igual que v1).
TOLERANCIA_LINEA = 25.0


def _left_de_box(box: Box) -> float:
    """Devuelve el ``left`` (borde izquierdo) derivado del bbox.

    En v1: ``left = min(bbox.l, bbox.r)``. Si no hay bbox, usa ``center_x``.
    """
    if box.bbox is not None:
        l, _t, r, _b = box.bbox
        return min(l, r)
    return box.center_x


def _texto_de_box(box: Box) -> str:
    """Texto a exportar: el markdown de la tabla si es tabla, si no el texto."""
    if box.es_tabla and box.markdown_tabla:
        return box.markdown_tabla.strip()
    return box.texto.strip()


def exportar_por_posicion(boxes: list[Box], orientacion: str = "horizontal") -> str:
    """Exporta los ``Box`` a Markdown ordenado por posición visual (E-DOC-3).

    Argumentos:
        boxes: lista de :class:`Box` del ``ProcessedDocument``.
        orientacion: ``"horizontal"`` (default) o ``"vertical"`` (T-103 decide
            la dominante; doc 03 §4.1).

    Devuelve:
        El Markdown con los textos agrupados en líneas y ordenados por
        posición. Si no hay boxes que coincidan con la orientación, devuelve
        ``"No se encontraron textos en orientación {orientacion}.\n"``.
    """
    is_horizontal = orientacion == "horizontal"

    # 1. Filtrar: un box participa si su orientación coincide con la pedida.
    #    Las tablas SIEMPRE se tratan como horizontal (paridad v1).
    boxes_utiles: list[dict] = []
    for box in boxes:
        if box.es_tabla:
            box_orient = "horizontal"
        else:
            if box.bbox is None:
                continue  # sin posición no se puede ordenar (v1: sin prov -> skip)
            ancho = abs(box.bbox[2] - box.bbox[0])
            alto = abs(box.bbox[3] - box.bbox[1])
            box_orient = "horizontal" if ancho >= alto else "vertical"

        if box_orient != orientacion:
            continue

        texto = _texto_de_box(box)
        if not texto:
            continue

        boxes_utiles.append(
            {
                "text": texto,
                "left": _left_de_box(box),
                "center_x": box.center_x,
                "center_y": box.center_y,
            }
        )

    if not boxes_utiles:
        return f"No se encontraron textos en orientación {orientacion}.\n"

    # 2. Agrupar en líneas por la coordenada de lectura (center_y horiz /
    #    center_x vert), tolerancia TOLERANCIA_LINEA.
    line_coordinate = "center_y" if is_horizontal else "center_x"
    line_sort_reverse = is_horizontal
    item_sort_key = "left" if is_horizontal else "center_y"

    lines: list[dict] = []
    for box in sorted(
        boxes_utiles,
        key=lambda value: value[line_coordinate],
        reverse=line_sort_reverse,
    ):
        line = min(
            lines,
            key=lambda candidate: abs(candidate["coordinate"] - box[line_coordinate]),
            default=None,
        )
        if line is None or abs(line["coordinate"] - box[line_coordinate]) > TOLERANCIA_LINEA:
            lines.append({"coordinate": box[line_coordinate], "boxes": [box]})
            continue

        line["boxes"].append(box)
        line["coordinate"] = sum(
            item[line_coordinate] for item in line["boxes"]
        ) / len(line["boxes"])

    # 3. Ordenar líneas y, dentro de cada línea, los boxes.
    lines.sort(key=lambda line: line["coordinate"], reverse=line_sort_reverse)
    return "\n".join(
        " | ".join(
            box["text"]
            for box in sorted(
                line["boxes"],
                key=lambda box: box[item_sort_key],
                reverse=not is_horizontal,
            )
        )
        for line in lines
    ) + "\n"


__all__ = [
    "TOLERANCIA_LINEA",
    "exportar_por_posicion",
]

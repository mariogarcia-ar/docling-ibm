def get_orientation_for_item(item):
    """Devuelve la orientación del box según sus dimensiones."""
    if not getattr(item, 'prov', None):
        return None

    bbox = item.prov[0].bbox
    width = abs(bbox.r - bbox.l)
    height = abs(bbox.t - bbox.b)
    return 'horizontal' if width >= height else 'vertical'


def get_dominant_orientation(doc):
    """Calcula la orientación predominante de un documento."""
    orientations = []
    for item, _ in doc.iterate_items():
        orientation = get_orientation_for_item(item)
        if orientation:
            orientations.append(orientation)

    if not orientations:
        return 'horizontal'

    horizontal_count = orientations.count('horizontal')
    vertical_count = orientations.count('vertical')
    return 'horizontal' if horizontal_count >= vertical_count else 'vertical'


def export_orientation_text(doc, selected_orientation):
    """Agrupa boxes por línea y los ordena según su orientación."""
    boxes = []

    for item, _ in doc.iterate_items():
        if not hasattr(item, 'text'):
            continue

        text = str(item.text).strip()
        if not text or get_orientation_for_item(item) != selected_orientation:
            continue
        if not getattr(item, 'prov', None):
            continue

        bbox = item.prov[0].bbox
        boxes.append({
            'text': text,
            'left': min(bbox.l, bbox.r),
            'center_x': (bbox.l + bbox.r) / 2,
            'center_y': (bbox.t + bbox.b) / 2,
        })

    if not boxes:
        return f"No se encontraron textos en orientación {selected_orientation}.\n"

    is_horizontal = selected_orientation == 'horizontal'
    line_coordinate = 'center_y' if is_horizontal else 'center_x'
    line_sort_reverse = is_horizontal
    item_sort_key = 'left' if is_horizontal else 'center_y'
    lines = []

    for box in sorted(
        boxes,
        key=lambda value: value[line_coordinate],
        reverse=line_sort_reverse,
    ):
        line = min(
            lines,
            key=lambda candidate: abs(candidate['coordinate'] - box[line_coordinate]),
            default=None,
        )
        if line is None or abs(line['coordinate'] - box[line_coordinate]) > 25.0:
            lines.append({'coordinate': box[line_coordinate], 'boxes': [box]})
            continue

        line['boxes'].append(box)
        line['coordinate'] = sum(
            item[line_coordinate] for item in line['boxes']
        ) / len(line['boxes'])

    lines.sort(key=lambda line: line['coordinate'], reverse=line_sort_reverse)
    return '\n'.join(
        ' | '.join(
            box['text']
            for box in sorted(
                line['boxes'],
                key=lambda box: box[item_sort_key],
                reverse=not is_horizontal,
            )
        )
        for line in lines
    ) + '\n'

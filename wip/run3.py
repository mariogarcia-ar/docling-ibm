from pathlib import Path

from rapidocr_onnxruntime import RapidOCR
from reportlab.pdfgen import canvas

IMG_PATH = Path('files/2025-08/2D2C9343/2991f57d-c143-4b23-9f87-4dfb1214ef53.jpg')
OUT_PDF = IMG_PATH.with_name(f'{IMG_PATH.stem}_ticket.pdf')


def extract_text_blocks(result):
    """Convierte cada bloque OCR en un diccionario con posición visual."""
    blocks = []
    for bbox, text, score in result:
        text = (text or '').strip()
        if not text:
            continue

        xs = [point[0] for point in bbox]
        ys = [point[1] for point in bbox]
        x_center = sum(xs) / len(xs)
        y_center = sum(ys) / len(ys)

        blocks.append({
            'text': text,
            'score': float(score),
            'x': x_center,
            'y': y_center,
        })

    return sorted(blocks, key=lambda item: (item['y'], item['x']))


def build_rows(blocks, y_threshold=30):
    """Agrupa textos por filas usando la posición vertical."""
    if not blocks:
        return []

    rows = []
    current = [blocks[0]]

    for block in blocks[1:]:
        if abs(block['y'] - current[-1]['y']) <= y_threshold:
            current.append(block)
        else:
            rows.append(sorted(current, key=lambda item: item['x']))
            current = [block]

    rows.append(sorted(current, key=lambda item: item['x']))
    return rows


def render_ticket(rows):
    """Muestra el ticket como filas organizadas en columnas."""
    if not rows:
        return

    max_x = max(item['x'] for row in rows for item in row)
    split_x = max_x * 0.55

    for row in rows:
        left_items = [item for item in row if item['x'] < split_x]
        right_items = [item for item in row if item['x'] >= split_x]

        left_text = ' '.join(item['text'] for item in left_items)
        right_text = ' '.join(item['text'] for item in right_items)

        if left_text or right_text:
            if left_text and right_text:
                print(f'{left_text:<60} {right_text}')
            elif left_text:
                print(left_text)
            else:
                print(f'{right_text:>25}')


def export_ticket_pdf(rows, output_path):
    """Genera un PDF con un layout tipo ticket usando columnas izquierda/derecha."""
    c = canvas.Canvas(str(output_path), pagesize=(612, 792))
    c.setTitle('Ticket OCR')
    c.setFont('Helvetica', 9)

    max_x = max((item['x'] for row in rows for item in row), default=600)
    split_x = max_x * 0.55

    x_left = 50
    x_right = 420
    y = 760

    for row in rows:
        left_items = [item for item in row if item['x'] < split_x]
        right_items = [item for item in row if item['x'] >= split_x]

        left_text = ' '.join(item['text'] for item in left_items)
        right_text = ' '.join(item['text'] for item in right_items)

        if left_text or right_text:
            if left_text:
                c.drawString(x_left, y, left_text[:90])
            if right_text:
                c.drawString(x_right, y, right_text[:25])
            y -= 18

        if y < 40:
            c.showPage()
            y = 760

    c.save()


if __name__ == '__main__':
    engine = RapidOCR(language='latin')
    result, _ = engine(str(IMG_PATH))

    if not result:
        print('No se detectó texto en la imagen.')
        raise SystemExit(0)

    blocks = extract_text_blocks(result)
    rows = build_rows(blocks)

    print('Ticket reconstruido:')
    render_ticket(rows)
    export_ticket_pdf(rows, OUT_PDF)
    print(f'\nPDF guardado en: {OUT_PDF}')

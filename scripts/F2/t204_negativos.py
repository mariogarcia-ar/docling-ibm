#!/usr/bin/env python
"""Genera los fixtures **negativos sintéticos** del golden F2 (T-204).

F2-subplan §2.5: además de los 9 casos reales del golden (etiquetados con
evidencia OCR/texto nativo), el subconjunto acotado de F2 incorpora
**no-comprobantes explícitos** para poder medir la métrica del gate (exactitud y
% de no-comprobantes que no llegan a extracción, §2.6). Los negativos reales de
``fixtures/`` son pocos (2) y este script agrega un conjunto **sintético,
determinista y versionado** que cubre los tipos de "no comprobante" que el
prompt del gate declara (`prompt_qween.SYSTEM_PROMPT_QWEEN`): capturas de
sistemas/billeteras, extractos/resúmenes, documentos personales, memos,
presupuestos y fotos ajenas al gasto.

Los archivos se escriben en ``v2/tests/fixtures/negativos/`` (carpeta
versionada) y quedan **commiteados**: los tests de la suite default no
regeneran nada (solo leen las rutas del ``casos.csv``). Este script existe para
*documentar y reproducir* su origen (trazabilidad del golden), no para correr
en la suite.

Sin dependencias nuevas declaradas:

  - **Imágenes** (PNG/JPG): se dibujan con Pillow **si está disponible en el
    entorno** (best-effort; en ``py313_env`` lo está, la usa el preprocesador
    de vistas de F2/T-202). Si Pillow no está, el script aborta con un mensaje
    claro: los fixtures ya están commiteados y no hace falta regenerarlos.
  - **PDF**: se escribe un PDF 1.4 mínimo **a mano con la stdlib** (texto
    nativo con Helvetica, apto para ``pdftotext`` y para el detector de F1).

Uso:
    cd v2 && python scripts/F2/t204_negativos.py           # regenera todo
    cd v2 && python scripts/F2/t204_negativos.py --listar  # solo lista

Los contenidos son **genéricos y sin PII** (no hay CUIT, nombres ni montos
reales): son plantillas sintéticas para el gate, según la regla del golden de
no versionar datos personales innecesarios (README del golden set).
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

# Raíz de fixtures de F2 (mismo destino que referencia tests/golden/casos.csv).
_DESTINO = Path(__file__).resolve().parents[2] / "tests" / "fixtures" / "negativos"

#: Contenido de cada fixture: ``nombre`` → líneas de texto. La primera línea es
#: el "título" (se dibuja más grande/negrita en imágenes y centrado en PDF).
#: Todas las plantillas incluyen una frase que declara explícitamente que **no
#: es un comprobante**, para que el negativo sea inequívoco (además de no tener
#: datos fiscales del emisor, que es lo que la regla dura del gate exige).
NEGATIVOS: dict[str, list[str]] = {
    "neg_2026-02_gastos_varios.png": [
        "GASTOS VARIOS",
        "Comprobante: FALTA FACTURA",
        "Detalle: sin documento respaldatorio",
        "NO ES COMPROBANTE",
    ],
    "neg_2026-03_dni_dorso.jpg": [
        "REPUBLICA ARGENTINA",
        "DOCUMENTO NACIONAL DE IDENTIDAD",
        "Documento personal - no es comprobante fiscal",
    ],
    "neg_2026-04_selfie_obra.jpg": [
        "FOTO DE OBRA",
        "Registro fotografico del avance",
        "Sin datos fiscales del emisor - no es comprobante",
    ],
    "neg_2026-05_chat_pago.png": [
        "CHAT DE PAGOS",
        "Pagaste $ 0,00 - operacion enviada",
        "Adjunta la factura por separado",
        "NO ES COMPROBANTE",
    ],
    "neg_2026-08_memo_interno.png": [
        "MEMORANDUM INTERNO",
        "Asunto: novedades administrativas",
        "Sin datos fiscales del emisor - no es comprobante",
    ],
    "neg_2026-10_pantalla_aprobacion.png": [
        "SISTEMA DE PAGOS",
        "OPERACION APROBADA",
        "Pantalla de confirmacion del sistema",
        "NO ES COMPROBANTE",
    ],
    "neg_2026-11_foto_pizarra.jpg": [
        "NOTA DE REUNION",
        "Pizarra con pendientes del equipo",
        "Sin datos fiscales - no es comprobante",
    ],
    "neg_2026-06_correo_liquidacion.pdf": [
        "AVISO DE LIQUIDACION DE HABERES",
        "Estimado colaborador: se informa el detalle del periodo.",
        "Este aviso no es un comprobante fiscal ni comercial.",
    ],
    "neg_2026-07_resumen_tarjeta.pdf": [
        "RESUMEN DE TARJETA DE CREDITO",
        "Listado de consumos y vencimientos del periodo.",
        "El resumen no es el comprobante emitido por el comercio.",
    ],
    "neg_2026-09_presupuesto.pdf": [
        "PRESUPUESTO",
        "Detalle de trabajos estimados a realizar.",
        "Presupuesto no acredita la operacion - no es comprobante.",
    ],
}

#: Dimensiones/rango de los negativos sintéticos (chicos: la suite de T-204
#: usa un caso por vista y no debe ser lenta; además el gate debe ver imágenes
#: legibles, no necesariamente grandes).
_ANCHO, _ALTO = 720, 480


def _dibujar_imagen(ruta: Path, lineas: list[str]) -> None:
    """Dibuja un PNG/JPG sintético con ``lineas`` (Pillow; best-effort)."""
    from PIL import Image, ImageDraw

    img = Image.new("RGB", (_ANCHO, _ALTO), "white")
    draw = ImageDraw.Draw(img)
    draw.rectangle([8, 8, _ANCHO - 8, _ALTO - 8], outline="black", width=2)
    y = 40
    for i, linea in enumerate(lineas):
        # La primera línea es el título: más grande y subrayada (simula un
        # encabezado real para el OCR del gate).
        if i == 0:
            draw.text((30, y), linea, fill="black")
            draw.line([30, y + 16, 30 + 8 * len(linea), y + 16], fill="black", width=1)
            y += 44
        else:
            draw.text((30, y), linea, fill="black")
            y += 30
    # Se guarda con la extensión pedida (PNG/JPG) para respetar el CSV.
    if ruta.suffix.lower() in (".jpg", ".jpeg"):
        img.save(ruta, "JPEG", quality=85)
    else:
        img.save(ruta, "PNG")


def _pdf_con_lineas(lineas: list[str]) -> bytes:
    """Construye un PDF 1.4 mínimo con las ``lineas`` como texto nativo (stdlib).

    Escribe una única página A4 con Helvetica 12 pt; el texto queda en la capa
    de contenido (apto para ``pdftotext`` y para el detector de F1). Es
    deliberadamente mínimo (sin fuentes embebidas, sin compresión) para no
    agregar dependencias: los objetos/cross-reference se arman a mano.
    """
    contenido_txt = ["BT", "/F1 12 Tf", "72 760 Td", "16 TL"]
    for i, linea in enumerate(lineas):
        # Escapa paréntesis/backslash (sintaxis literal string del PDF).
        escapada = linea.replace("\\", r"\\").replace("(", r"\(").replace(")", r"\)")
        if i == 0:
            contenido_txt.append("/F1 16 Tf")
            contenido_txt.append(f"({escapada}) Tj")
            contenido_txt.append("T*")
            contenido_txt.append("/F1 12 Tf")
        else:
            contenido_txt.append(f"({escapada}) Tj")
            contenido_txt.append("T*")
    contenido_txt.append("ET")
    stream = "\n".join(contenido_txt).encode("latin-1")

    objetos: list[bytes] = [
        b"<< /Type /Catalog /Pages 2 0 R >>",
        b"<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
        (
            b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 595 842] "
            b"/Resources << /Font << /F1 5 0 R >> >> /Contents 4 0 R >>"
        ),
        b"<< /Length %d >>\nstream\n" % len(stream) + stream + b"\nendstream",
        b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>",
    ]

    out = bytearray(b"%PDF-1.4\n")
    offsets: list[int] = []
    for num, obj in enumerate(objetos, start=1):
        offsets.append(len(out))
        out += b"%d 0 obj\n" % num + obj + b"\nendobj\n"
    inicio_xref = len(out)
    out += b"xref\n0 %d\n" % (len(objetos) + 1)
    out += b"0000000000 65535 f \n"
    for offset in offsets:
        out += b"%010d 00000 n \n" % offset
    out += (
        b"trailer\n<< /Size %d /Root 1 0 R >>\nstartxref\n%d\n%%%%EOF\n"
        % (len(objetos) + 1, inicio_xref)
    )
    return bytes(out)


def generar(destino: Path = _DESTINO) -> list[Path]:
    """Genera todos los fixtures negativos y devuelve sus rutas (T-204)."""
    destino.mkdir(parents=True, exist_ok=True)
    generados: list[Path] = []
    for nombre, lineas in NEGATIVOS.items():
        ruta = destino / nombre
        if ruta.suffix.lower() == ".pdf":
            ruta.write_bytes(_pdf_con_lineas(lineas))
        else:
            _dibujar_imagen(ruta, lineas)
        generados.append(ruta)
    return generados


def main() -> None:
    parser = argparse.ArgumentParser(
        description=(
            "Genera los fixtures negativos sintéticos del golden F2 (T-204; "
            "F2-subplan §2.5). Los archivos quedan versionados en "
            "tests/fixtures/negativos/."
        )
    )
    parser.add_argument(
        "--destino",
        type=Path,
        default=_DESTINO,
        help=f"Carpeta destino (default: {_DESTINO}).",
    )
    parser.add_argument(
        "--listar",
        action="store_true",
        help="Solo lista los archivos que se generarían (no escribe nada).",
    )
    args = parser.parse_args()

    if args.listar:
        for nombre in NEGATIVOS:
            print(f"  • {args.destino / nombre}")
        return

    try:
        generados = generar(args.destino)
    except ImportError as exc:  # Pillow ausente: los fixtures ya están commiteados
        print(
            f"⚠  No se pudieron regenerar los negativos ({exc}). "
            "Los fixtures versionados en tests/fixtures/negativos/ ya existen; "
            "ver el README del golden set (T-204).",
            file=sys.stderr,
        )
        raise SystemExit(2)

    for ruta in generados:
        print(f"✓ {ruta.relative_to(ruta.parents[3])} ({ruta.stat().st_size} bytes)")


if __name__ == "__main__":
    main()

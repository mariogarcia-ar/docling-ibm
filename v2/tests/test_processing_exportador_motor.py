"""Tests del motor OCR/VLM + exportador ordenado (F1 / T-104).

Validan:
  - ``markdown_exporter.exportar_por_posicion``: orden por posición visual
    (center_y horizontal / center_x vertical), tablas como ítem único
    (E-DOC-3) — portado byte-compatible de ``v1/lib/orientation.py``.
  - ``ocr.elegir_motor``: selección ``ocr``/``vlm``/``auto`` (E-DOC-2) y hook
    ``transcribir_vlm`` (F1: no llama a Ollama, subplan §2.2).

Sin Docling real: se usan :class:`Box` sintéticos.
"""

from __future__ import annotations

import pytest

from voucherflow.models.docling import Box
from voucherflow.processing.image_classifier import ClaseImagen, ClasificacionImagen
from voucherflow.processing.markdown_exporter import (
    TOLERANCIA_LINEA,
    exportar_por_posicion,
)
from voucherflow.processing.ocr import (
    MODOS_VALIDOS,
    MotorOCR,
    elegir_motor,
    transcribir_vlm,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _box(texto: str, l: float, t: float, r: float, b: float, **kw) -> Box:
    """Box con bbox y centros derivados."""
    return Box(
        texto=texto,
        center_x=(l + r) / 2,
        center_y=(t + b) / 2,
        bbox=(l, t, r, b),
        **kw,
    )


def _clasif(clase: ClaseImagen, motor_sugerido: str = "ocr") -> ClasificacionImagen:
    from voucherflow.processing.image_classifier import CaracteristicasImagen

    carac = CaracteristicasImagen(ancho=1000, alto=1000, formato=".png", ratio=1.0)
    return ClasificacionImagen(clase=clase, caracteristicas=carac, motivo="test",
                               motor_sugerido=motor_sugerido)


# ---------------------------------------------------------------------------
# Exportador horizontal
# ---------------------------------------------------------------------------

class TestExportadorHorizontal:
    def test_agrupa_por_center_y_y_ordena_reverse(self):
        # 3 líneas (y=900, 500, 100). En horizontal las líneas salen de mayor a
        # menor center_y (reverse=True, como v1: de abajo hacia arriba).
        boxes = [
            _box("SUPERIOR", 20, 90, 180, 110),      # y=100
            _box("MEDIA", 20, 490, 180, 510),        # y=500
            _box("INFERIOR", 20, 890, 180, 910),     # y=900
        ]
        out = exportar_por_posicion(boxes, "horizontal")
        lineas = out.strip().split("\n")
        assert lineas == ["INFERIOR", "MEDIA", "SUPERIOR"], out

    def test_dentro_de_linea_por_left_ascendente(self):
        # 2 items en la misma línea (mismo center_y): izquierda -> derecha.
        boxes = [
            _box("DERECHA", 210, 490, 290, 510),  # left=210
            _box("IZQUIERDA", 20, 490, 180, 510),  # left=20
        ]
        out = exportar_por_posicion(boxes, "horizontal")
        assert out.strip() == "IZQUIERDA | DERECHA", out

    def test_tolerancia_agrupa_cercanos(self):
        # Items con center_y dentro de 25.0 -> misma línea.
        boxes = [
            _box("A", 20, 480, 180, 500),   # y=490
            _box("B", 200, 500, 300, 520),  # y=510 (diff 20 < 25)
        ]
        out = exportar_por_posicion(boxes, "horizontal")
        assert out.count("|") == 1, f"Deben agruparse en una línea: {out!r}"

    def test_join_y_newline_final(self):
        boxes = [_box("A", 0, 0, 10, 10)]
        out = exportar_por_posicion(boxes, "horizontal")
        assert out.endswith("\n")
        assert out == "A\n"

    def test_sin_boxes_devuelve_mensaje(self):
        out = exportar_por_posicion([], "horizontal")
        assert out == "No se encontraron textos en orientación horizontal.\n"

    def test_sin_boxes_vertical_mensaje(self):
        out = exportar_por_posicion([], "vertical")
        assert out == "No se encontraron textos en orientación vertical.\n"


# ---------------------------------------------------------------------------
# Exportador vertical
# ---------------------------------------------------------------------------

class TestExportadorVertical:
    def test_agrupa_por_center_x_sin_reverse(self):
        # Documento vertical: columnas en x=100 y x=300 -> x asc.
        boxes = [
            _box("COL2", 290, 100, 310, 900),  # x=300
            _box("COL1", 90, 100, 110, 900),   # x=100
        ]
        out = exportar_por_posicion(boxes, "vertical")
        lineas = out.strip().split("\n")
        assert lineas[0].startswith("COL1"), out  # x menor primero

    def test_dentro_de_columna_por_center_y_reverse(self):
        # Misma columna (x=100): center_y mayor primero (reverse).
        boxes = [
            _box("ARRIBA_COL", 90, 100, 110, 500),    # y=300
            _box("ABAJO_COL", 90, 500, 110, 900),     # y=700
        ]
        out = exportar_por_posicion(boxes, "vertical")
        # center_y reverse -> primero el de mayor y (700).
        assert out.strip() == "ABAJO_COL | ARRIBA_COL", out


# ---------------------------------------------------------------------------
# Exportador con tablas
# ---------------------------------------------------------------------------

class TestExportadorTablas:
    def test_tabla_es_item_unico_con_su_markdown(self):
        boxes = [
            _box("Titulo", 10, 790, 200, 810),
            _box("| A | B |\n|---|---|\n| 1 | 2 |", 10, 480, 300, 520,
                 es_tabla=True, markdown_tabla="| A | B |\n|---|---|\n| 1 | 2 |"),
            _box("Pie", 10, 90, 200, 110),
        ]
        out = exportar_por_posicion(boxes, "horizontal")
        # La tabla se exporta completa (con sus saltos de línea internos).
        assert "| A | B |\n|---|---|\n| 1 | 2 |" in out, out
        assert "Titulo" in out and "Pie" in out

    def test_tabla_se_filtra_en_vertical_como_horizontal(self):
        # Una tabla en un doc "vertical" se fuerza horizontal: en orientación
        # vertical NO debe aparecer (porque la tabla es horizontal).
        boxes = [
            _box("TABLA", 10, 480, 300, 520, es_tabla=True,
                 markdown_tabla="| A |\n|---|\n| 1 |"),
            _box("COLUMNA", 90, 100, 110, 900),  # box vertical
        ]
        out = exportar_por_posicion(boxes, "vertical")
        assert "| A |" not in out, "La tabla (horizontal) no va en export vertical"


# ---------------------------------------------------------------------------
# Selección de motor OCR/VLM
# ---------------------------------------------------------------------------

class TestElegirMotor:
    def test_auto_impreso_usa_ocr(self):
        c = _clasif(ClaseImagen.escaneo_plano)
        assert elegir_motor(c, "auto") == MotorOCR.ocr

    def test_auto_screenshot_usa_ocr(self):
        c = _clasif(ClaseImagen.screenshot)
        assert elegir_motor(c, "auto") == MotorOCR.ocr

    def test_auto_foto_usa_ocr(self):
        # Foto de documento con impreso legible -> OCR (enderezar primero).
        c = _clasif(ClaseImagen.foto)
        assert elegir_motor(c, "auto") == MotorOCR.ocr

    def test_auto_manuscrito_usa_vlm(self):
        # Manuscrito/sello/firma -> priorizar VLM (E-DOC-2).
        c = _clasif(ClaseImagen.manuscrito)
        assert elegir_motor(c, "auto") == MotorOCR.vlm

    def test_auto_con_senal_sello_usa_vlm(self):
        c = _clasif(ClaseImagen.escaneo_plano)
        assert elegir_motor(c, "auto", {"contiene_sello": True}) == MotorOCR.vlm

    def test_forzar_ocr(self):
        c = _clasif(ClaseImagen.manuscrito)
        assert elegir_motor(c, "ocr") == MotorOCR.ocr

    def test_forzar_vlm(self):
        c = _clasif(ClaseImagen.escaneo_plano)
        assert elegir_motor(c, "vlm") == MotorOCR.vlm

    def test_modo_invalido_lanza_valueerror(self):
        c = _clasif(ClaseImagen.escaneo_plano)
        with pytest.raises(ValueError):
            elegir_motor(c, "no_existe")

    def test_modos_validos(self):
        assert MODOS_VALIDOS == {"ocr", "vlm", "auto"}


# ---------------------------------------------------------------------------
# Hook VLM (F1)
# ---------------------------------------------------------------------------

class TestHookVLM:
    def test_transcribir_vlm_no_implementado_en_f1(self):
        # En F1 NO se llama a Ollama (subplan §2.2): el hook lanza y dice F4.
        with pytest.raises(NotImplementedError) as exc:
            transcribir_vlm("x.jpg")
        assert "F4" in str(exc.value)

"""Tests de orientación + preprocesamiento (F1 / T-103, épica E-DOC-2).

Validan la detección de orientación dominante horizontal/vertical por boxes
(doc 03 §4.1; E-DOC-2 regla "orientación") y el stub heurístico de
preprocesamiento (QualityReport + evaluar_calidad), sin OpenCV/Pillow
(subplan §2.1).
"""

from __future__ import annotations

import struct
import zlib
from pathlib import Path

import pytest

from voucherflow.models.docling import Box
from voucherflow.processing.image_classifier import ClaseImagen, ClasificacionImagen
from voucherflow.processing.orientation import (
    ORIENTACIONES_VALIDAS,
    ORIENTACION_HORIZONTAL,
    ORIENTACION_VERTICAL,
    detectar_orientacion,
    orientacion_de,
    orientacion_por_box,
    requiere_rotacion,
)
from voucherflow.processing.preprocessing import (
    QualityReport,
    evaluar_calidad,
    preprocesar,
)


# ---------------------------------------------------------------------------
# Helpers de boxes
# ---------------------------------------------------------------------------

def _box(texto: str, l: float, t: float, r: float, b: float) -> Box:
    """Box con bbox y centros derivados (coordenadas 0..1)."""
    return Box(
        texto=texto,
        center_x=(l + r) / 2,
        center_y=(t + b) / 2,
        bbox=(l, t, r, b),
    )


def _box_horizontal(texto: str, y: float = 0.5) -> Box:
    """Box ancho (texto en línea horizontal)."""
    return _box(texto, 0.1, y - 0.02, 0.9, y + 0.02)


def _box_vertical(texto: str, x: float = 0.5) -> Box:
    """Box alto (texto en columna vertical)."""
    return _box(texto, x - 0.02, 0.1, x + 0.02, 0.9)


# ---------------------------------------------------------------------------
# Orientación por box
# ---------------------------------------------------------------------------

class TestOrientacionPorBox:
    def test_box_ancho_es_horizontal(self):
        assert orientacion_por_box(_box_horizontal("A")) == ORIENTACION_HORIZONTAL

    def test_box_alto_es_vertical(self):
        assert orientacion_por_box(_box_vertical("A")) == ORIENTACION_VERTICAL

    def test_box_sin_bbox_devuelve_none(self):
        assert orientacion_por_box(Box(texto="sin bbox")) is None

    def test_box_cuadrado_es_horizontal(self):
        # ancho == alto -> horizontal (igual que v1: width >= height).
        b = _box("A", 0.1, 0.1, 0.3, 0.3)
        assert orientacion_por_box(b) == ORIENTACION_HORIZONTAL


# ---------------------------------------------------------------------------
# Orientación dominante
# ---------------------------------------------------------------------------

class TestDetectarOrientacion:
    def test_documento_horizontal(self):
        boxes = [_box_horizontal("1", 0.9), _box_horizontal("2", 0.5)]
        assert detectar_orientacion(boxes) == ORIENTACION_HORIZONTAL

    def test_documento_vertical(self):
        boxes = [_box_vertical("1", 0.2), _box_vertical("2", 0.5)]
        assert detectar_orientacion(boxes) == ORIENTACION_VERTICAL

    def test_sin_boxes_devuelve_horizontal(self):
        # default horizontal (v1: si no hay orientaciones -> horizontal).
        assert detectar_orientacion([]) == ORIENTACION_HORIZONTAL

    def test_solo_boxes_sin_posicion_devuelve_horizontal(self):
        boxes = [Box(texto="a"), Box(texto="b")]
        assert detectar_orientacion(boxes) == ORIENTACION_HORIZONTAL

    def test_empate_devuelve_horizontal(self):
        # 1 vertical + 1 horizontal -> horizontal (v1: >=).
        boxes = [_box_vertical("v"), _box_horizontal("h")]
        assert detectar_orientacion(boxes) == ORIENTACION_HORIZONTAL

    def test_orientacion_de_acepta_none(self):
        assert orientacion_de(None) == ORIENTACION_HORIZONTAL
        assert orientacion_de([_box_vertical("v")]) == ORIENTACION_VERTICAL


# ---------------------------------------------------------------------------
# Requiere rotación
# ---------------------------------------------------------------------------

class TestRequiereRotacion:
    def test_horizontal_no_requiere(self):
        assert requiere_rotacion([_box_horizontal("A")]) is False

    def test_vertical_requiere(self):
        # Documento rotado 90°: la lectura correcta es vertical.
        assert requiere_rotacion([_box_vertical("A")]) is True

    def test_valores_de_orientacion_validos(self):
        assert ORIENTACIONES_VALIDAS == {ORIENTACION_HORIZONTAL, ORIENTACION_VERTICAL}


# ---------------------------------------------------------------------------
# Preprocesamiento (QualityReport / evaluar_calidad)
# ---------------------------------------------------------------------------

def _clasif(clase: ClaseImagen, ancho: int, alto: int) -> ClasificacionImagen:
    from voucherflow.processing.image_classifier import CaracteristicasImagen

    carac = CaracteristicasImagen(
        ancho=ancho,
        alto=alto,
        formato=".png",
        ratio=round(max(ancho, alto) / min(ancho, alto), 4) if ancho and alto else 0.0,
    )
    return ClasificacionImagen(clase=clase, caracteristicas=carac, motivo="test")


class TestEvaluarCalidad:
    def test_escaneo_plano_buena_resolucion_sin_preproc(self):
        c = _clasif(ClaseImagen.escaneo_plano, 2200, 2700)
        q = evaluar_calidad(c)
        assert q.requiere_preprocesamiento is False
        assert q.acciones == ()

    def test_foto_requiere_enderezar_perspectiva(self):
        c = _clasif(ClaseImagen.foto, 1600, 1200)
        q = evaluar_calidad(c)
        assert q.requiere_preprocesamiento is True
        assert "enderezar_perspectiva" in q.acciones

    def test_screenshot_no_preproc(self):
        c = _clasif(ClaseImagen.screenshot, 1152, 648)
        q = evaluar_calidad(c)
        assert q.requiere_preprocesamiento is False

    def test_baja_resolucion_requiere_mejorar_resolucion(self):
        # 400px < minimo (600) pero > critico (200) -> mejorar_resolucion.
        c = _clasif(ClaseImagen.escaneo_plano, 400, 500)
        q = evaluar_calidad(c)
        assert q.requiere_preprocesamiento is True
        assert "mejorar_resolucion" in q.acciones

    def test_quality_report_serializa_a_dict(self):
        q = QualityReport(
            requiere_preprocesamiento=True,
            acciones=("enderezar_perspectiva",),
            resolucion_lado_menor=800,
            motivo="x",
        )
        d = q.a_dict()
        assert d["requiere_preprocesamiento"] is True
        assert d["acciones"] == ["enderezar_perspectiva"]
        assert d["resolucion_lado_menor"] == 800


# ---------------------------------------------------------------------------
# Hook de preprocesamiento (stub F1)
# ---------------------------------------------------------------------------

class TestPreprocesarStub:
    def test_devuelve_la_misma_ruta_en_f1(self, tmp_path):
        # En F1 no hay transformación de píxeles (sin CV); Docling la hace.
        ruta = tmp_path / "x.png"
        ruta.write_bytes(b"x")
        c = _clasif(ClaseImagen.foto, 1600, 1200)
        q = evaluar_calidad(c)
        assert preprocesar(str(ruta), c, q) == str(ruta)

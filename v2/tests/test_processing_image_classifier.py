"""Tests del clasificador de imagen + gate de procesabilidad (F1 / T-102).

Validan la clasificación heurística (foto/escaneo_plano/screenshot/manuscrito)
y el gate de procesabilidad (doc 03 §4.1, E-DOC-2; doc 00 glosario).

Reglas duras del subplan F1 (§4): la suite default corre **sin** OpenCV/Pillow.
Las imágenes de prueba se generan como PNG sintéticos con stdlib (struct+zlib)
y el clasificador lee solo cabeceras (sin decodificar píxeles).
"""

from __future__ import annotations

import struct
import zlib
from pathlib import Path

import pytest

from voucherflow.processing.image_classifier import (
    ANCHO_SCREENSHOT_MIN,
    RESOLUCION_CRITICA_PX,
    RESOLUCION_MINIMA_PX,
    ClaseImagen,
    CaracteristicasImagen,
    ClasificacionImagen,
    VeredictoGate,
    clasificar,
    leer_caracteristicas,
    sospechar_manuscrito,
    verificar_procesabilidad,
)
from voucherflow.processing.type_detector import EXTENSIONES_IMAGEN


# ---------------------------------------------------------------------------
# Helpers: PNG sintéticos con stdlib (sin Pillow/OpenCV)
# ---------------------------------------------------------------------------

def _chunk(tipo: bytes, data: bytes) -> bytes:
    c = tipo + data
    return struct.pack(">I", len(data)) + c + struct.pack(">I", zlib.crc32(c))


def _png(w: int, h: int, rgba: bool = False) -> bytes:
    """PNG sintético válido (cabecera IHDR real, píxeles en blanco)."""
    ct = 6 if rgba else 2
    bpp = 4 if rgba else 3
    raw = b"".join(b"\x00" + b"\x00" * (w * bpp) for _ in range(h))
    return (
        b"\x89PNG\r\n\x1a\n"
        + _chunk(b"IHDR", struct.pack(">IIBBBBB", w, h, 8, ct, 0, 0, 0))
        + _chunk(b"IDAT", zlib.compress(raw))
        + _chunk(b"IEND", b"")
    )


def _escribir(tmp_path: Path, nombre: str, contenido: bytes) -> Path:
    ruta = tmp_path / nombre
    ruta.write_bytes(contenido)
    return ruta


def _png_documento(tmp_path: Path, w: int = 2200, h: int = 2700) -> Path:
    """PNG de documento (ratio ~1.23, RGB, alta resolución) → escaneo plano."""
    return _escribir(tmp_path, "doc.png", _png(w, h, rgba=False))


def _png_screenshot(tmp_path: Path, w: int = 1152, h: int = 648) -> Path:
    """PNG de pantalla 16:9 con alpha → screenshot."""
    return _escribir(tmp_path, "shot.png", _png(w, h, rgba=True))


# ---------------------------------------------------------------------------
# Lectura de características (stdlib, sin decodificar píxeles)
# ---------------------------------------------------------------------------

class TestLeerCaracteristicas:
    def test_png_rgb_lee_dimensiones(self, tmp_path):
        r = _escribir(tmp_path, "a.png", _png(1000, 2000))
        c = leer_caracteristicas(r)
        assert c.ancho == 1000 and c.alto == 2000
        assert c.es_rgba is False

    def test_png_rgba_detecta_alpha(self, tmp_path):
        r = _escribir(tmp_path, "b.png", _png(1152, 648, rgba=True))
        c = leer_caracteristicas(r)
        assert c.es_rgba is True

    def test_ratio_se_calcula_mayor_menor(self, tmp_path):
        r = _escribir(tmp_path, "c.png", _png(648, 1152))  # vertical
        c = leer_caracteristicas(r)
        assert c.ratio == pytest.approx(1152 / 648, rel=1e-3)

    def test_archivo_corrupto_devuelve_dimensiones_cero(self, tmp_path):
        r = _escribir(tmp_path, "roto.png", b"no es un png")
        c = leer_caracteristicas(r)
        assert c.ancho == 0 and c.alto == 0

    def test_archivo_inexistente_no_lanza(self, tmp_path):
        c = leer_caracteristicas(tmp_path / "no_existe.png")
        assert c.ancho == 0 and c.alto == 0


# ---------------------------------------------------------------------------
# Clasificador
# ---------------------------------------------------------------------------

class TestClasificador:
    def test_png_documento_es_escaneo_plano(self, tmp_path):
        ruta = _png_documento(tmp_path)
        c = clasificar(ruta)
        assert c.clase == ClaseImagen.escaneo_plano, c.motivo
        assert c.motor_sugerido == "ocr"

    def test_png_rgba_pantalla_es_screenshot(self, tmp_path):
        ruta = _png_screenshot(tmp_path)
        c = clasificar(ruta)
        assert c.clase == ClaseImagen.screenshot, c.motivo

    def test_png_16_9_sin_alpha_pero_ancho_pantalla_es_screenshot(self, tmp_path):
        # 1280x720 RGB (16:9) con ancho de pantalla -> screenshot por ratio.
        ruta = _escribir(tmp_path, "s.png", _png(1280, 720, rgba=False))
        c = clasificar(ruta)
        assert c.clase == ClaseImagen.screenshot, c.motivo

    def test_png_3_2_es_foto(self, tmp_path):
        # 1200x800 (3:2) -> foto de cámara.
        ruta = _escribir(tmp_path, "f.png", _png(1200, 800, rgba=False))
        c = clasificar(ruta)
        assert c.clase == ClaseImagen.foto, c.motivo

    def test_png_casi_cuadrado_baja_resolucion_es_foto(self, tmp_path):
        # 300x280 casi cuadrado, baja resolución -> foto.
        ruta = _escribir(tmp_path, "q.png", _png(300, 280, rgba=False))
        c = clasificar(ruta)
        assert c.clase == ClaseImagen.foto, c.motivo

    def test_png_casi_cuadrado_alta_resolucion_es_escaneo(self, tmp_path):
        # 2000x2000 casi cuadrado, alta resolución -> escaneo plano.
        ruta = _escribir(tmp_path, "cuad.png", _png(2000, 2000, rgba=False))
        c = clasificar(ruta)
        assert c.clase == ClaseImagen.escaneo_plano, c.motivo

    def test_recorte_panoramico_pequeno_no_es_screenshot(self, tmp_path):
        # Ancho < umbral de pantalla: no debe clasificar screenshot.
        ruta = _escribir(tmp_path, "pan.png", _png(455, 206, rgba=False))
        c = clasificar(ruta)
        assert c.clase != ClaseImagen.screenshot, c.motivo

    def test_caracteristicas_se_adjuntan_al_resultado(self, tmp_path):
        ruta = _png_documento(tmp_path)
        c = clasificar(ruta)
        assert c.caracteristicas.ancho == 2200
        assert c.caracteristicas.alto == 2700


# ---------------------------------------------------------------------------
# Gate de procesabilidad
# ---------------------------------------------------------------------------

class TestGate:
    def test_imagen_valida_es_procesable(self, tmp_path):
        ruta = _png_documento(tmp_path)
        v = verificar_procesabilidad(ruta)
        assert v.procesable is True
        assert v.razon_rechazo is None

    def test_archivo_vacio_rechazado(self, tmp_path):
        ruta = _escribir(tmp_path, "vacio.png", b"")
        v = verificar_procesabilidad(ruta)
        assert v.procesable is False
        assert v.razon_rechazo == "archivo_vacio"

    def test_archivo_corrupto_rechazado(self, tmp_path):
        ruta = _escribir(tmp_path, "corrupto.png", b"basura")
        v = verificar_procesabilidad(ruta)
        assert v.procesable is False
        assert v.razon_rechazo == "dimensiones_ilegibles"

    def test_extension_no_imagen_rechazada(self, tmp_path):
        ruta = _escribir(tmp_path, "datos.txt", b"texto")
        v = verificar_procesabilidad(ruta)
        assert v.procesable is False
        assert v.razon_rechazo == "formato_no_soportado"

    def test_resolucion_critica_rechazada(self, tmp_path):
        # Lado menor por debajo del crítico -> ilegible.
        ruta = _escribir(tmp_path, "tiny.png", _png(100, 150))
        v = verificar_procesabilidad(ruta)
        assert v.procesable is False
        assert v.razon_rechazo == "resolucion_muy_baja"

    def test_resolucion_baja_pero_procesable(self, tmp_path):
        # Entre crítica y mínima: procesable con advertencia (no rechazo falso).
        lado = (RESOLUCION_CRITICA_PX + RESOLUCION_MINIMA_PX) // 2
        ruta = _escribir(tmp_path, "media.png", _png(lado, lado + 200))
        v = verificar_procesabilidad(ruta)
        assert v.procesable is True
        assert v.razon_rechazo == "resolucion_baja"

    def test_ratio_extremo_rechazado(self, tmp_path):
        # Banner 4000x100 -> ratio 40 > máximo.
        ruta = _escribir(tmp_path, "banner.png", _png(4000, 100))
        v = verificar_procesabilidad(ruta)
        assert v.procesable is False
        assert v.razon_rechazo == "ratio_extremo"


# ---------------------------------------------------------------------------
# Coherencia de clases y hook de manuscrito (T-104)
# ---------------------------------------------------------------------------

class TestCoherencia:
    def test_clases_son_las_del_dominio(self):
        # doc 00 glosario / doc 03 §4.1: foto, escaneo_plano, screenshot, manuscrito.
        valores = {c.value for c in ClaseImagen}
        assert {"foto", "escaneo_plano", "screenshot", "manuscrito"} <= valores

    def test_extensiones_imagen_cubren_gate(self):
        # El gate acepta justo las extensiones que el detector marca como imagen.
        assert EXTENSIONES_IMAGEN <= {".jpg", ".jpeg", ".png", ".tif", ".tiff", ".bmp"}

    def test_hook_sospechar_manuscrito_sin_senal_es_false(self):
        c = ClasificacionImagen(clase=ClaseImagen.escaneo_plano, motivo="x")
        assert sospechar_manuscrito(c) is False  # no forzar VLM en default (F1)

    def test_hook_sospechar_manuscrito_con_senal_es_true(self):
        c = ClasificacionImagen(clase=ClaseImagen.escaneo_plano, motivo="x")
        assert sospechar_manuscrito(c, {"contiene_sello": True}) is True
        assert sospechar_manuscrito(c, {"manuscrito_detectado": True}) is True

    def test_veredicto_y_clasificacion_son_dataclasses_inmutables(self):
        v = VeredictoGate(procesable=True, motivo="ok")
        assert v.procesable is True and v.razon_rechazo is None
        car = CaracteristicasImagen(ancho=10, alto=20)
        assert car.ratio == 0.0  # no calculado automáticamente

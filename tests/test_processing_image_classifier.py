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

    def test_jpeg_exif_orientacion_en_subifd(self, tmp_path):
        # JPEG mínimo con APP1-EXIF cuyo tag Orientation (274) vive en el subIFD
        # EXIF (tag 34665), como lo escriben muchas cámaras (caso 4c261bc8).
        import struct as _struct

        def _campo(endian, tag, tipo, valor):
            # entrada IFD de 12 bytes con valor inline (SHORT=3)
            return _struct.pack(f"{endian}HHI", tag, tipo, 1) + valor

        # Construir bloque EXIF (TIFF little-endian) con IFD0 -> subIFD EXIF.
        endian = "<"
        # IFD0: 1 entrada (tag 34665 ExifIFDPointer), luego offset del subIFD.
        ifd0_offset = 8
        subifd_offset = ifd0_offset + 2 + 12 + 4  # header(8) + 1 entrada + next IFD ptr
        ifd0 = _struct.pack(f"{endian}H", 1)  # 1 entrada
        # ExifIFDPointer: tag 34665, tipo 4 (LONG), count 1, valor = offset
        ifd0 += _struct.pack(f"{endian}HHI", 34665, 4, 1) + _struct.pack(f"{endian}I", subifd_offset)
        ifd0 += _struct.pack(f"{endian}I", 0)  # next IFD = 0
        # subIFD EXIF: 1 entrada con Orientation (tag 274, SHORT=3) = 8
        subifd = _struct.pack(f"{endian}H", 1)
        subifd += _campo(endian, 274, 3, _struct.pack(f"{endian}H", 8)) + b"\x00\x00"
        subifd += _struct.pack(f"{endian}I", 0)

        tiff = b"II" + _struct.pack(f"{endian}H", 42) + _struct.pack(f"{endian}I", ifd0_offset)
        tiff += ifd0 + subifd
        app1 = b"Exif\x00\x00" + tiff
        jpeg = (
            b"\xff\xd8"
            + b"\xff\xe1" + _struct.pack(">H", len(app1) + 2) + app1
            + b"\xff\xd9"
        )
        ruta = _escribir(tmp_path, "exif_subifd.jpg", jpeg)
        ch = leer_caracteristicas(ruta)
        assert ch.exif_orientacion == 8, "Debe leer Orientation del subIFD EXIF"

    def test_jpeg_con_exif_rotacion_se_clasifica_foto(self, tmp_path):
        # JPEG con SOF0 bien formado (dimensiones legibles) + APP1-EXIF con
        # Orientation=8 en subIFD -> debe clasificar como foto (no escaneo).
        import struct as _struct

        # --- APP1-EXIF con Orientation=8 en el subIFD EXIF ---
        endian = "<"
        ifd0_offset = 8
        subifd_offset = ifd0_offset + 2 + 12 + 4
        ifd0 = _struct.pack(f"{endian}H", 1)
        ifd0 += _struct.pack(f"{endian}HHI", 34665, 4, 1) + _struct.pack(f"{endian}I", subifd_offset)
        ifd0 += _struct.pack(f"{endian}I", 0)
        subifd = _struct.pack(f"{endian}H", 1)
        subifd += _struct.pack(f"{endian}HHI", 274, 3, 1) + _struct.pack(f"{endian}H", 8) + b"\x00\x00"
        subifd += _struct.pack(f"{endian}I", 0)
        tiff = b"II" + _struct.pack(f"{endian}H", 42) + _struct.pack(f"{endian}I", ifd0_offset)
        tiff += ifd0 + subifd
        app1_payload = b"Exif\x00\x00" + tiff
        app1 = b"\xff\xe1" + _struct.pack(">H", len(app1_payload) + 2) + app1_payload

        # --- SOF0 bien formado (alto 1200, ancho 1600, 1 componente Y) ---
        # payload: precisión(1) + alto(2) + ancho(2) + n_comp(1) + comp(3) = 9
        sof_payload = b"\x08" + _struct.pack(">HH", 1200, 1600) + b"\x01\x01\x11\x00"
        sof0 = b"\xff\xc0" + _struct.pack(">H", len(sof_payload)) + sof_payload

        jpeg = b"\xff\xd8" + app1 + sof0 + b"\xff\xd9"
        ruta = _escribir(tmp_path, "foto_exif.jpg", jpeg)
        ch = leer_caracteristicas(ruta)
        assert ch.exif_orientacion == 8
        c = clasificar(ruta)
        assert c.clase == ClaseImagen.foto, c.motivo


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

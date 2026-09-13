"""Tests del detector de tipo de entrada (F1 / T-101, épica E-DOC-1).

Validan la clasificación por extensión y la distinción ``pdf_texto``/
``pdf_escaneado`` (doc 03 §4.1; regla "PDF escaneado" de E-DOC-1).

Reglas duras del subplan F1 (§4): la suite default corre **sin** Docling real —
los PDFs de prueba se generan con **PyMuPDF** (que crea PDFs reales abribles,
con y sin capa de texto) y se escriben en ``tmp_path``. ``pymupdf`` es
dependencia del paquete desde F1/T-101.
"""

from __future__ import annotations

from pathlib import Path

import pymupdf as fitz  # PyMuPDF
import pytest

from voucherflow.processing.type_detector import (
    EXTENSIONES_IMAGEN,
    EXTENSIONES_OFFICE,
    EXTENSIONES_NO_SOPORTADAS,
    EXTENSIONES_TEXTO,
    TIPOS_VALIDOS,
    TipoEntrada,
    detectar,
)


# ---------------------------------------------------------------------------
# Helpers: archivos de prueba por tipo
# ---------------------------------------------------------------------------

def _escribir(tmp_path: Path, nombre: str, contenido: bytes) -> Path:
    ruta = tmp_path / nombre
    ruta.write_bytes(contenido)
    return ruta


def _pdf_con_texto(tmp_path: Path, nombre: str = "texto.pdf") -> Path:
    """PDF real con capa de texto (inserta texto vectorial)."""
    ruta = tmp_path / nombre
    doc = fitz.open()
    pagina = doc.new_page()
    pagina.insert_text((72, 72), "FACTURA A 0001-00000001")
    pagina.insert_text((72, 100), "Total: $ 1.234,56")
    doc.save(str(ruta))
    doc.close()
    return ruta


def _pdf_solo_imagen(tmp_path: Path, nombre: str = "escaneado.pdf") -> Path:
    """PDF real escaneado: una imagen a página completa, sin texto."""
    ruta = tmp_path / nombre
    # Crea un PNG simple en memoria (1x1 blanco no alcanza; usar pixmap).
    pix = fitz.Pixmap(fitz.csRGB, fitz.IRect(0, 0, 200, 300), False)
    pix.clear_with(255)  # blanco
    doc = fitz.open()
    pagina = doc.new_page(width=200, height=300)
    pagina.insert_image(pagina.rect, pixmap=pix)
    doc.save(str(ruta))
    doc.close()
    return ruta


# ---------------------------------------------------------------------------
# Contrato del dataclass TipoEntrada
# ---------------------------------------------------------------------------

class TestTipoEntrada:
    def test_es_dataclass_inmutable(self):
        te = TipoEntrada(tipo="imagen", ruta_ocr=True)
        assert te.tipo == "imagen"
        assert te.ruta_ocr is True
        assert te.motivo == ""  # default
        with pytest.raises(Exception):  # frozen=True -> no se puede mutar
            te.tipo = "texto"  # type: ignore[misc]

    def test_tipos_validos_cubren_los_del_diseno(self):
        # doc 03 §4.1: pdf_texto | pdf_escaneado | imagen | office | texto | no_soportado
        assert TIPOS_VALIDOS == {
            "pdf_texto",
            "pdf_escaneado",
            "imagen",
            "office",
            "texto",
            "no_soportado",
        }


# ---------------------------------------------------------------------------
# Clasificación por extensión (formatos no-PDF)
# ---------------------------------------------------------------------------

class TestClasificacionPorExtension:
    @pytest.mark.parametrize(
        "ext, tipo_esperado, ruta_ocr_esperada",
        [
            (".jpg", "imagen", True),
            (".jpeg", "imagen", True),
            (".png", "imagen", True),
            (".tif", "imagen", True),
            (".tiff", "imagen", True),
            (".bmp", "imagen", True),
            (".docx", "office", False),
            (".xlsx", "office", False),
            (".pptx", "office", False),
            (".txt", "texto", False),
            (".csv", "texto", False),
            (".log", "texto", False),
            (".html", "texto", False),
            (".md", "texto", False),
        ],
    )
    def test_ext_mapea_a_tipo(self, tmp_path, ext, tipo_esperado, ruta_ocr_esperada):
        ruta = _escribir(tmp_path, f"archivo{ext}", b"contenido")
        te = detectar(ruta)
        assert te.tipo == tipo_esperado, te.motivo
        assert te.ruta_ocr is ruta_ocr_esperada, te.motivo

    def test_extension_mayuscula_es_insensible(self, tmp_path):
        ruta = _escribir(tmp_path, "FOTO.JPG", b"x")
        te = detectar(ruta)
        assert te.tipo == "imagen" and te.ruta_ocr is True

    def test_no_soportado_explicito_no_fuerza_ocr(self, tmp_path):
        # Los formatos no soportados se rechazan/reencolan sin forzar OCR (E-DOC-1).
        for ext in sorted(EXTENSIONES_NO_SOPORTADAS):
            ruta = _escribir(tmp_path, f"archivo{ext}", b"x")
            te = detectar(ruta)
            assert te.tipo == "no_soportado"
            assert te.ruta_ocr is False, "no_soportado nunca debe forzar OCR"

    def test_extension_desconocida_no_fuerza_ocr(self, tmp_path):
        ruta = _escribir(tmp_path, "archivo.xyz", b"x")
        te = detectar(ruta)
        assert te.tipo == "no_soportado"
        assert te.ruta_ocr is False

    def test_sin_extension_no_soportado(self, tmp_path):
        ruta = _escribir(tmp_path, "SIN_EXTENSION", b"x")
        te = detectar(ruta)
        assert te.tipo == "no_soportado"
        assert te.ruta_ocr is False


# ---------------------------------------------------------------------------
# Distinción pdf_texto / pdf_escaneado (con PyMuPDF)
# ---------------------------------------------------------------------------

class TestClasificacionPdf:
    def test_pdf_con_capa_de_texto_es_pdf_texto(self, tmp_path):
        # PDF con texto vectorial real -> pdf_texto (texto nativo, sin OCR).
        ruta = _pdf_con_texto(tmp_path)
        te = detectar(ruta)
        assert te.tipo == "pdf_texto"
        assert te.ruta_ocr is False

    def test_pdf_solo_imagen_es_pdf_escaneado(self, tmp_path):
        # PDF con solo una imagen (sin capa de texto) -> pdf_escaneado.
        ruta = _pdf_solo_imagen(tmp_path)
        te = detectar(ruta)
        assert te.tipo == "pdf_escaneado"
        assert te.ruta_ocr is True  # convertir a imagen + OCR

    def test_pdf_texto_con_logo_no_es_escaneado(self, tmp_path):
        # PDF con texto REAL + una imagen (logo): NO debe clasificar escaneado.
        # (Regresión del bug detectado con PyMuPDF: la heurística de bytes
        # marcaba como escaneado los PDFs Type0 con logo — 11 falsos positivos.)
        ruta = tmp_path / "texto_con_logo.pdf"
        pix = fitz.Pixmap(fitz.csRGB, fitz.IRect(0, 0, 30, 30), False)
        pix.clear_with(200)
        doc = fitz.open()
        pagina = doc.new_page(width=300, height=200)
        pagina.insert_image(fitz.Rect(10, 10, 40, 40), pixmap=pix)  # logo
        pagina.insert_text((60, 40), "FACTURA A 0001-00000001")
        pagina.insert_text((60, 70), "CUIT 20-12345678-9")
        doc.save(str(ruta))
        doc.close()

        te = detectar(ruta)
        assert te.tipo == "pdf_texto", te.motivo
        assert te.ruta_ocr is False

    def test_pdf_mixto_una_pagina_texto_una_imagen_es_pdf_texto(self, tmp_path):
        # Mixto (1 pág texto + 1 pág imagen): se prioriza texto (E-DOC-1).
        ruta = tmp_path / "mixto.pdf"
        pix = fitz.Pixmap(fitz.csRGB, fitz.IRect(0, 0, 100, 150), False)
        pix.clear_with(255)
        doc = fitz.open()
        p1 = doc.new_page(width=300, height=400)
        p1.insert_text((72, 72), "Pagina con texto")
        p2 = doc.new_page(width=100, height=150)
        p2.insert_image(p2.rect, pixmap=pix)
        doc.save(str(ruta))
        doc.close()

        te = detectar(ruta)
        assert te.tipo == "pdf_texto", te.motivo
        assert te.ruta_ocr is False

    def test_pdf_corrupto_no_fuerza_ocr(self, tmp_path):
        # PDF corrupto (no abrible) -> se asume pdf_texto sin forzar OCR.
        ruta = _escribir(tmp_path, "corrupto.pdf", b"%PDF-1.4\nno valido")
        te = detectar(ruta)
        assert te.tipo == "pdf_texto"
        assert te.ruta_ocr is False


# ---------------------------------------------------------------------------
# Errores de entrada
# ---------------------------------------------------------------------------

class TestErrores:
    def test_archivo_inexistente_lanza_filenotfound(self, tmp_path):
        with pytest.raises(FileNotFoundError):
            detectar(tmp_path / "no_existe.pdf")

    def test_directorio_lanza_valueerror(self, tmp_path):
        with pytest.raises(ValueError):
            detectar(tmp_path)

    def test_acepta_path_o_str(self, tmp_path):
        ruta = _escribir(tmp_path, "a.png", b"x")
        te_path = detectar(ruta)
        te_str = detectar(str(ruta))
        assert te_path == te_str  # dataclass frozen -> eq por valor


# ---------------------------------------------------------------------------
# Coherencia con los sets de extensiones exportados
# ---------------------------------------------------------------------------

class TestCoherenciaSets:
    def test_sets_de_extensiones_son_disjuntos(self):
        # Ninguna extensión debe pertenecer a dos categorías a la vez.
        sets = [EXTENSIONES_IMAGEN, EXTENSIONES_OFFICE, EXTENSIONES_TEXTO, EXTENSIONES_NO_SOPORTADAS]
        for i, s1 in enumerate(sets):
            for s2 in sets[i + 1:]:
                assert not (s1 & s2), f"Extensiones solapadas: {s1 & s2}"

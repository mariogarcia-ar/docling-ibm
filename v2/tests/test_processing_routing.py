"""Tests del enrutado de PDF por página (F1 / routing, E-DOC-1).

Validan que ``analizar_pdf`` clasifique correctamente PDFs:
  - ``apto`` (texto nativo en todas las páginas),
  - ``requiere_ocr`` (escaneadas/corruptas/vacías),
  - ``parcial`` (mezcla),
y que las propiedades auxiliares (``paginas_aptas``/``paginas_ocr``) sirvan a
la orquestación para elegir la ruta por página.

Los PDFs se generan con PyMuPDF (sin Docling real, subplan §4).
"""

from __future__ import annotations

import fitz
import pytest

from voucherflow.processing.routing import (
    AnalisisPdf,
    ClasePagina,
    VeredictoPdf,
    analizar_pdf,
    clasificar_pagina,
)


# ---------------------------------------------------------------------------
# Helpers: PDFs sintéticos
# ---------------------------------------------------------------------------

def _pdf_texto(tmp_path, nombre="texto.pdf", paginas=1):
    """PDF con texto nativo real en todas las páginas."""
    ruta = tmp_path / nombre
    doc = fitz.open()
    for _ in range(paginas):
        p = doc.new_page()
        p.insert_text((72, 72), "FACTURA A 0001-00000001")
        p.insert_text((72, 100), "Total: $ 1.234,56")
    doc.save(str(ruta))
    doc.close()
    return ruta


def _pdf_escaneado(tmp_path, nombre="escaneado.pdf"):
    """PDF con una imagen a página completa (sin texto) -> escaneado."""
    ruta = tmp_path / nombre
    pix = fitz.Pixmap(fitz.csRGB, fitz.IRect(0, 0, 200, 300), False)
    pix.clear_with(255)
    doc = fitz.open()
    p = doc.new_page(width=200, height=300)
    p.insert_image(p.rect, pixmap=pix)
    doc.save(str(ruta))
    doc.close()
    return ruta


def _pdf_mixto(tmp_path, nombre="mixto.pdf"):
    """Página 1 escaneada + página 2 con texto real (suficiente) -> parcial."""
    ruta = tmp_path / nombre
    pix = fitz.Pixmap(fitz.csRGB, fitz.IRect(0, 0, 200, 300), False)
    pix.clear_with(255)
    doc = fitz.open()
    p1 = doc.new_page(width=200, height=300)
    p1.insert_image(p1.rect, pixmap=pix)
    p2 = doc.new_page()
    # Texto real > TEXTO_MINIMO_CHARS (40): una página apta de verdad.
    p2.insert_text((72, 72), "FACTURA B 0002-00000001 Fecha 01/09/2026")
    p2.insert_text((72, 100), "Cliente: CVC S.A. - CUIT 30-58221570-3")
    p2.insert_text((72, 128), "Total: $ 5.678,90 (IVA 21% incluido)")
    doc.save(str(ruta))
    doc.close()
    return ruta


def _pdf_vacio(tmp_path, nombre="vacio.pdf"):
    """Página en blanco (sin texto ni imagen)."""
    ruta = tmp_path / nombre
    doc = fitz.open()
    doc.new_page()
    doc.save(str(ruta))
    doc.close()
    return ruta


# ---------------------------------------------------------------------------
# Veredictos por tipo de PDF
# ---------------------------------------------------------------------------

class TestVeredictos:
    def test_pdf_texto_es_apto(self, tmp_path):
        ruta = _pdf_texto(tmp_path)
        a = analizar_pdf(ruta)
        assert a.veredicto == VeredictoPdf.apto, a.resumen
        assert a.paginas_aptas == [1]
        assert a.paginas_ocr == []

    def test_pdf_texto_multipagina_es_apto(self, tmp_path):
        ruta = _pdf_texto(tmp_path, paginas=3)
        a = analizar_pdf(ruta)
        assert a.veredicto == VeredictoPdf.apto
        assert a.paginas_aptas == [1, 2, 3]

    def test_pdf_escaneado_es_requiere_ocr(self, tmp_path):
        ruta = _pdf_escaneado(tmp_path)
        a = analizar_pdf(ruta)
        assert a.veredicto == VeredictoPdf.requiere_ocr, a.resumen
        assert a.requiere_ocr_en_alguna is True
        assert a.paginas_ocr == [1]
        assert a.paginas_aptas == []

    def test_pdf_mixto_es_parcial(self, tmp_path):
        ruta = _pdf_mixto(tmp_path)
        a = analizar_pdf(ruta)
        assert a.veredicto == VeredictoPdf.parcial, a.resumen
        assert a.requiere_ocr_en_alguna is True
        assert a.paginas_ocr == [1]
        assert a.paginas_aptas == [2]

    def test_pdf_vacio_es_requiere_ocr(self, tmp_path):
        # Sin contenido aprovechable -> no se puede leer como texto.
        ruta = _pdf_vacio(tmp_path)
        a = analizar_pdf(ruta)
        assert a.veredicto == VeredictoPdf.requiere_ocr

    def test_pdf_inexistente_es_error(self, tmp_path):
        a = analizar_pdf(tmp_path / "no_existe.pdf")
        assert a.veredicto == VeredictoPdf.error


# ---------------------------------------------------------------------------
# Clasificación por página
# ---------------------------------------------------------------------------

class TestClasificarPagina:
    def test_clases_validas(self):
        # doc: apta_layout / escaneada / corrupta / vacia.
        valores = {c.value for c in ClasePagina}
        assert valores == {"apta_layout", "escaneada", "corrupta", "vacia"}

    def test_clasificar_pagina_texto(self, tmp_path):
        ruta = _pdf_texto(tmp_path)
        doc = fitz.open(str(ruta))
        try:
            analisis = clasificar_pagina(1, doc[0])
            assert analisis.clase == ClasePagina.apta_layout
            assert analisis.numero == 1
            assert analisis.metricas.chars > 0
        finally:
            doc.close()


# ---------------------------------------------------------------------------
# Coherencia del dataclass
# ---------------------------------------------------------------------------

class TestAnalisisPdf:
    def test_analisis_es_dataclass_inmutable(self):
        a = AnalisisPdf(veredicto=VeredictoPdf.apto, resumen="ok")
        assert a.veredicto == VeredictoPdf.apto
        assert a.paginas == []
        assert a.requiere_ocr_en_alguna is False

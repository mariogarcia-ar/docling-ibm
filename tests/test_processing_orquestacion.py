"""Tests de la orquestación de la Fase F1 (T-105/ORQ, épica E-DOC).

Validan ``procesar_documento`` (entrada de ``api.process``), ``procesar_imagen``
(subrutina de imagen) y ``render_pdf_a_jpg`` (helper de render, PROC.md §5)
sobre las rutas del doc 03 §4.1:

  - imagen → gate T-102 → clase → preprocesamiento → orientación → motor →
    exportador ordenado (E-DOC-2/E-DOC-3).
  - pdf escaneado → render→imagen→OCR (PROC.md §5).
  - pdf texto apto / office / texto → Docling directo (texto nativo).
  - no_soportado → ``DocumentoNoProcesableError`` (rechazo/reencolado).
  - parcial → ruteo por página (mezcla).

Reglas duras (subplan F1 §4): la suite default corre **sin** Docling real ni
Ollama — se usan dobles ``FakeConverter``/``FakeDoc``/``FakeItem`` (patrón de
``tests/test_models_docling.py``) y PDFs/PNG sintéticos generados con PyMuPDF.
"""
from __future__ import annotations

import struct
import zlib
from pathlib import Path

import pymupdf as fitz
import pytest

from voucherflow.api import DocumentoNoProcesableError, process
from voucherflow.models.docling import Box, DoclingConverter, ProcessedDocument
from voucherflow.processing.orquestacion import (
    procesar_documento,
    procesar_imagen,
    render_pdf_a_jpg,
)


# ---------------------------------------------------------------------------
# Dobles de Docling (patrón de test_models_docling.py; sin Docling real)
# ---------------------------------------------------------------------------

class FakeItem:
    """Ítem mínimo de Docling (texto + prov con bbox)."""

    def __init__(self, texto: str, l=0.0, t=0.0, r=10.0, b=5.0, tabla=False) -> None:
        self.text = texto
        if tabla:
            self.label = type("L", (), {"value": "table"})()
            self.markdown = f"| {texto} |"
        else:
            self.label = type("L", (), {"value": "text"})()
        self.prov = [type("P", (), {"bbox": type("B", (), {"l": l, "t": t, "r": r, "b": b})()})()]

    def export_to_markdown(self, doc=None) -> str:  # noqa: ARG002
        return getattr(self, "markdown", self.text)


class FakeDoc:
    """Documento Docling falso: exporta markdown e itera ítems."""

    def __init__(self, markdown: str = "# Factura\n\nA", items: list | None = None) -> None:
        self._markdown = markdown
        self._items = items or [FakeItem("A", 0, 0, 10, 5)]

    def export_to_markdown(self) -> str:
        return self._markdown

    def iterate_items(self):
        for it in self._items:
            yield it, 0


class _FakeDocling:
    """Doble del converter Docling real (devuelve ``resultado.document``).

    Es el que se inyecta DENTRO de :class:`DoclingConverter` (patrón fiel de
    ``test_models_docling.py``): ``DoclingConverter.convert`` es quien convierte
    ``resultado.document`` a un :class:`ProcessedDocument` (boxes, markdown,
    orientación), sin Docling real ni modelos (subplan F1 §4).
    """

    def __init__(self, doc: FakeDoc | None = None) -> None:
        self._doc = doc or FakeDoc()
        self.convert_calls: list[str] = []

    def convert(self, ruta: str):
        self.convert_calls.append(str(ruta))
        return type("R", (), {"document": self._doc})()


class FakeConverter:
    """Adaptador inyectable con la interfaz pública de ``DoclingConverter``.

    La orquestación (``procesar_documento``/``procesar_imagen``) inyecta el
    ``converter`` con el contrato de F0 (``convert() -> ProcessedDocument``;
    PROC.md §4.6: processing consume el adaptador ``DoclingConverter``). Por eso
    este doble **envuelve** un :class:`_FakeDocling` dentro de un
    :class:`DoclingConverter` real: la conversión a ``ProcessedDocument`` la
    hace el código de producción de F0/T-006 (sin Docling ni modelos).
    """

    def __init__(self, doc: FakeDoc | None = None) -> None:
        self._docling = _FakeDocling(doc)
        self._adaptador = DoclingConverter(converter=self._docling)
        self.convert_calls = self._docling.convert_calls

    def convert(self, ruta: str):
        return self._adaptador.convert(ruta)


# ---------------------------------------------------------------------------
# Helpers: PNG y PDF sintéticos (PyMuPDF / stdlib)
# ---------------------------------------------------------------------------

def _chunk(tipo: bytes, data: bytes) -> bytes:
    c = tipo + data
    return struct.pack(">I", len(data)) + c + struct.pack(">I", zlib.crc32(c))


def _png_bytes(w: int, h: int, rgba: bool = False) -> bytes:
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


def _png_documento(tmp_path, nombre="doc.png", w: int = 1000, h: int = 1300) -> Path:
    """PNG de documento (ratio ~1.3, RGB, lado menor 1000px >= 600) → gate OK."""
    ruta = tmp_path / nombre
    ruta.write_bytes(_png_bytes(w, h))
    return ruta


def _png_pequena(tmp_path, nombre="chica.png", w: int = 50, h: int = 60) -> Path:
    """PNG diminuto: no supera el gate (resolución < crítico 200px)."""
    ruta = tmp_path / nombre
    ruta.write_bytes(_png_bytes(w, h))
    return ruta


def _pdf_texto(tmp_path, nombre="texto.pdf", paginas=1):
    """PDF con texto nativo real en todas las páginas (routing apto)."""
    ruta = tmp_path / nombre
    doc = fitz.open()
    for _ in range(paginas):
        p = doc.new_page()
        p.insert_text((72, 72), "FACTURA A 0001-00000001")
        p.insert_text((72, 100), "Total: $ 1.234,56")
    doc.save(str(ruta))
    doc.close()
    return ruta


def _pdf_escaneado(tmp_path, nombre="escaneado.pdf", paginas=1):
    """PDF con imagen a página completa (sin texto) → requiere OCR."""
    ruta = tmp_path / nombre
    doc = fitz.open()
    for _ in range(paginas):
        pix = fitz.Pixmap(fitz.csRGB, fitz.IRect(0, 0, 1200, 1600), False)
        pix.clear_with(255)
        p = doc.new_page(width=1200, height=1600)
        p.insert_image(p.rect, pixmap=pix)
    doc.save(str(ruta))
    doc.close()
    return ruta


def _pdf_mixto(tmp_path, nombre="mixto.pdf"):
    """Página 1 escaneada + página 2 con texto real → parcial."""
    ruta = tmp_path / nombre
    pix = fitz.Pixmap(fitz.csRGB, fitz.IRect(0, 0, 200, 300), False)
    pix.clear_with(255)
    doc = fitz.open()
    p1 = doc.new_page(width=200, height=300)
    p1.insert_image(p1.rect, pixmap=pix)
    p2 = doc.new_page()
    p2.insert_text((72, 72), "FACTURA B 0002-00000001 Fecha 01/09/2026")
    p2.insert_text((72, 100), "Cliente: CVC S.A. - CUIT 30-58221570-3")
    p2.insert_text((72, 128), "Total: $ 5.678,90 (IVA 21% incluido)")
    doc.save(str(ruta))
    doc.close()
    return ruta


# ---------------------------------------------------------------------------
# procesar_documento: imagen válida
# ---------------------------------------------------------------------------

class TestProcesarDocumentoImagen:
    def test_imagen_valida_devuelve_processdocument(self, tmp_path):
        ruta = _png_documento(tmp_path)  # 1000x1300: gate OK
        conv = FakeConverter()
        doc = procesar_documento(ruta, converter=conv)

        assert isinstance(doc, ProcessedDocument)
        assert doc.tipo_entrada == "imagen"
        assert doc.ruta == str(ruta)
        assert doc.motor == "docling"
        # Se convirtió (una vez) el archivo con el converter inyectado.
        assert len(conv.convert_calls) == 1
        assert conv.convert_calls[0] == str(ruta)

    def test_imagen_valida_exporta_markdown(self, tmp_path):
        # Un box de texto real se exporta ordenado por posición (E-DOC-3).
        ruta = _png_documento(tmp_path)
        item = FakeItem("FACTURA", 50, 1100, 500, 1130)  # box horizontal
        conv = FakeConverter(FakeDoc(markdown="# crudo sin tabla", items=[item]))
        doc = procesar_documento(ruta, converter=conv)

        assert "FACTURA" in doc.markdown

    def test_imagen_valida_calidad_con_clase_y_gate(self, tmp_path):
        ruta = _png_documento(tmp_path)
        conv = FakeConverter()
        doc = procesar_documento(ruta, converter=conv)

        assert isinstance(doc.calidad, dict)
        assert doc.calidad["motor_seleccionado"] in ("ocr", "vlm")
        assert "clase_imagen" in doc.calidad
        assert "gate" in doc.calidad
        assert doc.calidad["tipo_entrada_origen"] == "imagen"

    def test_imagen_con_modo_vlm_no_llama_transcribir(self, tmp_path):
        # F1: aunque se fuerce vlm, el motor efectivo es docling y NO se llama
        # a transcribir_vlm (subplan §2.2). Aquí lo valida el converter real
        # inyectado (que no lanza NotImplementedError).
        ruta = _png_documento(tmp_path)
        conv = FakeConverter()
        doc = procesar_documento(ruta, converter=conv, modo_motor="vlm")
        assert doc.motor == "docling"
        assert doc.calidad["motor_seleccionado"] == "vlm"
        assert doc.calidad.get("motor_efectivo") == "docling"

    def test_imagen_inexistente_lanza_filenotfound(self, tmp_path):
        with pytest.raises(FileNotFoundError):
            procesar_documento(tmp_path / "no_existe.png", converter=FakeConverter())

    def test_imagen_no_pasa_el_gate_lanza_documento_no_procesable(self, tmp_path):
        # PNG diminuto (50x60 < 200px crítico) → rechazo de dominio.
        ruta = _png_pequena(tmp_path)
        conv = FakeConverter()
        with pytest.raises(DocumentoNoProcesableError) as exc:
            procesar_documento(ruta, converter=conv)
        assert "gate de procesabilidad" in str(exc.value)
        # No se gastó conversión (converter no llamado).
        assert conv.convert_calls == []

    def test_procesar_imagen_archivo_vacio_lanza(self, tmp_path):
        # Imagen corrupta/vacía: no supera el gate (T-102) → error de dominio.
        ruta = tmp_path / "vacia.png"
        ruta.write_bytes(b"")
        with pytest.raises(DocumentoNoProcesableError):
            procesar_imagen(ruta, converter=FakeConverter())


# ---------------------------------------------------------------------------
# procesar_documento: texto / office
# ---------------------------------------------------------------------------

class TestProcesarDocumentoTextoOffice:
    def test_texto_txt_usa_texto_nativo(self, tmp_path):
        ruta = tmp_path / "nota.txt"
        ruta.write_text("Factura de prueba\nTotal 100\n", encoding="utf-8")
        conv = FakeConverter()
        doc = procesar_documento(ruta, converter=conv)

        assert isinstance(doc, ProcessedDocument)
        assert doc.tipo_entrada == "texto"
        assert doc.motor == "docling"
        # No pasa por gate de imagen; convierte directo.
        assert len(conv.convert_calls) == 1
        assert conv.convert_calls[0] == str(ruta)

    def test_office_docx_usa_texto_nativo(self, tmp_path):
        # detectar() solo mira la extensión; el docx no necesita ser válido.
        ruta = tmp_path / "factura.docx"
        ruta.write_bytes(b"no es un docx real")
        conv = FakeConverter()
        doc = procesar_documento(ruta, converter=conv)

        assert doc.tipo_entrada == "office"
        assert doc.motor == "docling"
        assert len(conv.convert_calls) == 1

    def test_texto_no_supera_gate_pero_no_importa(self, tmp_path):
        # txt de 10 bytes no pasa por el gate (no es imagen).
        ruta = tmp_path / "a.txt"
        ruta.write_text("hola", encoding="utf-8")
        doc = procesar_documento(ruta, converter=FakeConverter())
        assert doc.tipo_entrada == "texto"


# ---------------------------------------------------------------------------
# procesar_documento: no soportado
# ---------------------------------------------------------------------------

class TestProcesarDocumentoNoSoportado:
    def test_extension_no_soportada_lanza_y_no_llama_converter(self, tmp_path):
        ruta = tmp_path / "archivo.xyz"
        ruta.write_bytes(b"x")
        conv = FakeConverter()
        with pytest.raises(DocumentoNoProcesableError) as exc:
            procesar_documento(ruta, converter=conv)
        assert "no soportado" in str(exc.value).lower() or "no_soportado" in str(exc.value)
        assert conv.convert_calls == []

    def test_ruta_inexistente_lanza_filenotfound(self, tmp_path):
        with pytest.raises(FileNotFoundError):
            procesar_documento(tmp_path / "no_existe.xyz", converter=FakeConverter())

    def test_directorio_lanza_valueerror(self, tmp_path):
        with pytest.raises(ValueError):
            procesar_documento(tmp_path, converter=FakeConverter())


# ---------------------------------------------------------------------------
# procesar_documento: PDF (texto apto / escaneado / parcial)
# ---------------------------------------------------------------------------

class TestProcesarDocumentoPdf:
    def test_pdf_texto_apto_devuelve_pdf_texto(self, tmp_path, monkeypatch):
        ruta = _pdf_texto(tmp_path)
        conv = FakeConverter()
        # Sin pdftotext (mock) → la ruta apto hace fallback a Docling directo.
        monkeypatch.setattr(
            "voucherflow.processing.pdftotext.extraer_con_pdftotext_layout",
            lambda pdf: None,
        )
        doc = procesar_documento(ruta, converter=conv)

        assert isinstance(doc, ProcessedDocument)
        assert doc.tipo_entrada == "pdf_texto"
        assert doc.ruta == str(ruta)
        assert doc.motor == "docling"
        # Routing apto → fallback a Docling directo sobre el PDF (1 sola llamada).
        assert len(conv.convert_calls) == 1
        assert conv.convert_calls[0] == str(ruta)

    def test_pdf_apto_con_pdftotext_usa_layout(self, tmp_path, monkeypatch):
        # pdftotext disponible y devuelve texto → se usa el layout (motor
        # pdftotext), sin llamar al converter Docling.
        ruta = _pdf_texto(tmp_path)
        conv = FakeConverter()
        layout = "FACTURA A 0001-00000001\nTotal: $ 1.234,56\n"
        monkeypatch.setattr(
            "voucherflow.processing.pdftotext.extraer_con_pdftotext_layout",
            lambda pdf: layout,
        )
        doc = procesar_documento(ruta, converter=conv)

        assert doc.tipo_entrada == "pdf_texto"
        assert doc.motor == "pdftotext"
        assert doc.markdown == layout
        assert doc.orientacion == "horizontal"
        assert isinstance(doc.calidad, dict)
        assert doc.calidad.get("salida") == "pdftotext_layout"
        assert doc.calidad.get("routing") == "apto"
        # No se gastó Docling (pdftotext fue suficiente).
        assert conv.convert_calls == []

    def test_pdf_escaneado_renderiza_a_imagen_y_ocr(self, tmp_path):
        ruta = _pdf_escaneado(tmp_path)
        conv = FakeConverter()
        doc = procesar_documento(ruta, converter=conv)

        assert doc.tipo_entrada == "pdf_escaneado"
        assert doc.ruta == str(ruta)  # la ruta pública es el PDF original
        assert doc.motor == "docling"
        # Se convirtió el JPG renderizado (no el PDF directo): PROC.md §5.
        assert len(conv.convert_calls) == 1
        assert conv.convert_calls[0].endswith(".jpg")

    def test_pdf_escaneado_limpia_temporal(self, tmp_path, monkeypatch):
        ruta = _pdf_escaneado(tmp_path)
        conv = FakeConverter()

        creados: list[Path] = []
        real_render = render_pdf_a_jpg

        def _render_rastreado(pdf, pagina=0, dpi=300):
            img = real_render(pdf, pagina=pagina, dpi=dpi)
            creados.append(img)
            return img

        monkeypatch.setattr("voucherflow.processing.orquestacion.render_pdf_a_jpg", _render_rastreado)
        procesar_documento(ruta, converter=conv)
        # El JPG temporal debe haberse borrado tras convertir (finally).
        assert creados, "El render debe haberse invocado"
        for img in creados:
            assert not img.exists(), f"El temporal debe limpiarse: {img}"

    def test_pdf_mixto_parcial_no_lanza(self, tmp_path):
        # Página 1 escaneada + página 2 con texto → routing parcial.
        ruta = _pdf_mixto(tmp_path)
        conv = FakeConverter()
        doc = procesar_documento(ruta, converter=conv)

        assert isinstance(doc, ProcessedDocument)
        assert doc.motor == "docling"
        assert doc.tipo_entrada in ("pdf_texto", "pdf_escaneado")


# ---------------------------------------------------------------------------
# render_pdf_a_jpg (helper exportado)
# ---------------------------------------------------------------------------

class TestRenderPdfAJpg:
    def test_render_devuelve_jpg_existente(self, tmp_path):
        ruta = _pdf_texto(tmp_path)
        img = render_pdf_a_jpg(ruta)
        try:
            assert img.exists()
            assert img.suffix == ".jpg"
            assert img.stat().st_size > 0
        finally:
            img.unlink(missing_ok=True)

    def test_render_pagina_fuera_de_rango_lanza(self, tmp_path):
        ruta = _pdf_texto(tmp_path)
        with pytest.raises(IndexError):
            render_pdf_a_jpg(ruta, pagina=99)

    def test_el_llamador_puede_borrarlo(self, tmp_path):
        ruta = _pdf_texto(tmp_path)
        img = render_pdf_a_jpg(ruta)
        assert img.exists()
        img.unlink(missing_ok=True)
        assert not img.exists()


# ---------------------------------------------------------------------------
# procesar_imagen (subrutina de imagen, E-DOC-2)
# ---------------------------------------------------------------------------

class TestProcesarImagen:
    def test_imagen_valida_devuelve_documento(self, tmp_path):
        ruta = _png_documento(tmp_path)
        conv = FakeConverter()
        doc = procesar_imagen(ruta, converter=conv, tipo_entrada="imagen")

        assert doc.tipo_entrada == "imagen"
        assert doc.motor == "docling"
        assert doc.calidad["clase_imagen"] in (
            "escaneo_plano", "foto", "screenshot", "manuscrito", "desconocida",
        )

    def test_no_supera_gate_lanza(self, tmp_path):
        ruta = tmp_path / "rara.bin"
        ruta.write_bytes(b"\x00" * 10)
        with pytest.raises(DocumentoNoProcesableError):
            procesar_imagen(ruta, converter=FakeConverter())

    def test_inexistente_lanza_filenotfound(self, tmp_path):
        with pytest.raises(FileNotFoundError):
            procesar_imagen(tmp_path / "no.png", converter=FakeConverter())


# ---------------------------------------------------------------------------
# Opción A: flag docling_raw (subplan F1 §2.5) — raw de Docling sin reordenar
# ---------------------------------------------------------------------------

class TestDoclingRaw:
    """``docling_raw=True`` expone el crudo de Docling (sin reordenar por
    posición; equivale a ``v1/run_raw.py``). Decisión de alcance subplan F1
    §2.5. El crudo se captura de ``conv.convert`` ANTES de pisarlo (no se
    vuelve a correr Docling); default ``False`` preserva la política combinada
    (E-DOC-3, contrato F2/F3/F4).
    """

    def test_imagen_docling_raw_devuelve_crudo_no_exportado(self, tmp_path):
        # Crudo sin tabla (no reproducible por los boxes): el exportador por
        # posición (E-DOC-3) devolvería solo el texto de los boxes; el modo raw
        # debe devolver el markdown crudo del FakeDoc tal cual.
        ruta = _png_documento(tmp_path)
        crudo = "# FACTURA ELECTRONICA\n\nRAW DOCLING sin reordenar"
        item = FakeItem("FACTURA", 50, 1100, 500, 1130)  # box horizontal
        conv = FakeConverter(FakeDoc(markdown=crudo, items=[item]))
        doc = procesar_documento(ruta, converter=conv, docling_raw=True)

        assert doc.markdown == crudo, (
            "docling_raw=True debe devolver el crudo de Docling (sin reordenar "
            "por posición), no el exportado por los boxes."
        )
        # Marca de modo raw en calidad (Opción A).
        assert isinstance(doc.calidad, dict)
        assert doc.calidad.get("docling_raw") is True
        assert doc.calidad.get("salida") == "markdown_crudo_docling"
        # No se volvió a correr Docling: 1 sola conversión.
        assert len(conv.convert_calls) == 1

    def test_imagen_docling_raw_false_preserva_exportado(self, tmp_path):
        # Default (docling_raw=False): comportamiento actual — exportador por
        # posición cuando el crudo no tiene tabla (contrato F2/F3/F4).
        ruta = _png_documento(tmp_path)
        crudo = "# FACTURA ELECTRONICA sin tabla"
        item = FakeItem("FACTURA", 50, 1100, 500, 1130)
        conv = FakeConverter(FakeDoc(markdown=crudo, items=[item]))
        doc = procesar_documento(ruta, converter=conv, docling_raw=False)

        # El crudo NO se devuelve tal cual: se reordena por posición (E-DOC-3).
        assert doc.markdown != crudo, (
            "docling_raw=False debe conservar la política combinada (E-DOC-3): "
            "si no hay tabla en el crudo, se ordena el texto por posición."
        )
        assert "FACTURA" in doc.markdown
        assert "sin tabla" not in doc.markdown
        assert "docling_raw" not in (doc.calidad or {})
        # Nota informativa de política de salida (no rompe tests existentes).
        assert (doc.calidad or {}).get("salida") == "exportado_por_posicion"

    def test_imagen_con_tabla_cruda_raw_y_no_raw_coinciden(self, tmp_path):
        # Cuando el crudo SÍ tiene tabla (y los boxes no la reproducen), la
        # política combinada ya conserva el crudo (E-DOC-3): el modo raw y el
        # default coinciden en el contenido de la tabla. Diferencias esperadas:
        # el modo raw devuelve el crudo EXACTO de Docling (byte a byte, sin
        # normalizar); la política combinada puede añadir un ``\\n`` final.
        ruta = _png_documento(tmp_path)
        crudo_tabla = "| Concepto | Importe |\n|----------|---------|\n| A       | $100    |"
        # Sin items: sin boxes; la política combinada conserva el crudo con |.
        conv = FakeConverter(FakeDoc(markdown=crudo_tabla, items=[]))
        doc_raw = procesar_documento(ruta, converter=conv, docling_raw=True)
        assert doc_raw.markdown == crudo_tabla, (
            "docling_raw=True devuelve el crudo exacto de Docling."
        )
        assert doc_raw.calidad.get("docling_raw") is True

        conv2 = FakeConverter(FakeDoc(markdown=crudo_tabla, items=[]))
        doc_normal = procesar_documento(ruta, converter=conv2, docling_raw=False)
        # La política combinada conserva la tabla del crudo (E-DOC-3), con la
        # misma estructura (solo puede diferir en el salto de línea final).
        assert doc_normal.markdown.rstrip("\n") == crudo_tabla, (
            "La política combinada ya conserva el crudo con tabla (E-DOC-3)."
        )
        assert (doc_normal.calidad or {}).get("salida") == "politica_combinada"

    def test_texto_nativo_docling_raw_devuelve_crudo(self, tmp_path):
        # Ruta texto/office: _procesar_texto_nativo con docling_raw=True.
        ruta = tmp_path / "nota.txt"
        ruta.write_text("Factura de prueba\nTotal 100\n", encoding="utf-8")
        crudo = "# Factura\n\nA"
        conv = FakeConverter(FakeDoc(markdown=crudo))
        doc = procesar_documento(ruta, converter=conv, docling_raw=True)

        assert doc.tipo_entrada == "texto"
        assert doc.motor == "docling"
        assert doc.markdown == crudo, (
            "docling_raw=True en texto nativo debe devolver el crudo de Docling."
        )
        assert doc.calidad.get("docling_raw") is True
        assert doc.calidad.get("salida") == "markdown_crudo_docling"
        # Una sola conversión (no se corre Docling dos veces).
        assert len(conv.convert_calls) == 1

    def test_pdf_apto_docling_raw_devuelve_crudo(self, tmp_path):
        # PDF con texto nativo en todas las páginas → routing apto → texto
        # nativo directo; docling_raw=True devuelve el crudo (sin reordenar).
        ruta = _pdf_texto(tmp_path)
        crudo = "# FACTURA PDF APTO\n\nRAW DOCLING"
        conv = FakeConverter(FakeDoc(markdown=crudo))
        doc = procesar_documento(ruta, converter=conv, docling_raw=True)

        assert doc.tipo_entrada == "pdf_texto"
        assert doc.markdown == crudo, (
            "docling_raw=True en PDF apto debe devolver el crudo de Docling."
        )
        assert doc.calidad.get("docling_raw") is True
        assert doc.calidad.get("routing") == "apto"
        assert len(conv.convert_calls) == 1

    def test_pdf_parcial_docling_raw_anota_no_aplica(self, tmp_path):
        # Decisión Opción A (subplan F1 §2.5): en un PDF mixto/parcial el crudo
        # pleno de Docling NO existe (páginas aptas usan PyMuPDF, no Docling por
        # página). docling_raw=True mantiene la concatenación actual y anota la
        # marca de no-aplicación en calidad.
        ruta = _pdf_mixto(tmp_path)
        conv = FakeConverter()
        doc = procesar_documento(ruta, converter=conv, docling_raw=True)

        assert isinstance(doc, ProcessedDocument)
        assert doc.motor == "docling"
        assert doc.calidad.get("routing") == "parcial"
        assert doc.calidad.get("docling_raw") == "parcial_no_aplica", (
            "En PDF parcial el crudo pleno no aplica: debe anotarse "
            "docling_raw='parcial_no_aplica' en calidad."
        )
        assert doc.calidad.get("salida") == "concatenado_parcial"
        assert "nota_raw" in doc.calidad

    def test_pdf_parcial_docling_raw_false_sin_marcas(self, tmp_path):
        # Default en PDF parcial: sin marcas de modo raw (comportamiento actual).
        ruta = _pdf_mixto(tmp_path)
        doc = procesar_documento(ruta, converter=FakeConverter(), docling_raw=False)
        assert doc.calidad.get("routing") == "parcial"
        assert "docling_raw" not in doc.calidad


# ---------------------------------------------------------------------------
# api.process (delegación, F1)
# ---------------------------------------------------------------------------

class TestApiProcess:
    def test_process_delega_y_no_lanza_notimplemented(self, tmp_path, monkeypatch):
        ruta = _png_documento(tmp_path)
        conv = FakeConverter()

        # api.process llama a procesar_documento(origen) sin inyectar converter:
        # se monkeypatchinga procesar_documento para devolver un documento sin
        # Docling real (valida la delegación y que process ya no es esqueleto).
        from voucherflow.processing import orquestacion

        def _fake_procesar(origen, **kwargs):
            assert Path(origen) == ruta
            return ProcessedDocument(
                tipo_entrada="imagen", ruta=str(ruta), markdown="# ok", motor="docling"
            )

        monkeypatch.setattr(orquestacion, "procesar_documento", _fake_procesar)
        doc = process(str(ruta))
        assert isinstance(doc, ProcessedDocument)
        assert doc.markdown == "# ok"

        # El converter inyectado por el test NO es necesario: la delegación es
        # sobre el orquestador, no sobre Docling real.
        assert conv.convert_calls == []

    def test_process_exporta_la_funcion(self):
        import voucherflow.api

        assert callable(voucherflow.api.process)

    def test_process_propaga_docling_raw_a_procesar_documento(self, tmp_path, monkeypatch):
        # Opción A (subplan F1 §2.5): api.process acepta el keyword
        # ``docling_raw`` y lo propaga a procesar_documento (spy de delegación,
        # sin Docling real). También valida que la firma posicional process
        # (origen) sigue intacta.
        ruta = _png_documento(tmp_path)
        from voucherflow.processing import orquestacion

        llamadas: list[dict] = []

        def _fake_procesar(origen, **kwargs):
            llamadas.append({"origen": Path(origen), "kwargs": kwargs})
            return ProcessedDocument(
                tipo_entrada="imagen", ruta=str(ruta),
                markdown="| crudo |", motor="docling",
                calidad={"docling_raw": kwargs.get("docling_raw", False)},
            )

        monkeypatch.setattr(orquestacion, "procesar_documento", _fake_procesar)

        # 1) Con el flag en True → se propaga.
        doc = process(str(ruta), docling_raw=True)
        assert doc.calidad["docling_raw"] is True
        assert llamadas and llamadas[0]["kwargs"].get("docling_raw") is True, (
            "api.process(docling_raw=True) debe propagar el flag a "
            "procesar_documento."
        )

        # 2) Default (False) → no se altera el comportamiento actual.
        llamadas.clear()
        doc2 = process(str(ruta))
        assert doc2.calidad["docling_raw"] is False
        assert llamadas[0]["kwargs"].get("docling_raw") is False

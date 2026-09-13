"""Tests del adaptador Docling (F0/T-006).

**Importante**: los tests de F0 NO convierten archivos reales con Docling
(descarga modelos y es lento; se marcan ``integration``). Aquí se prueba la
construcción del convertidor, la validación de entradas y el contrato de salida
``ProcessedDocument`` con un **converter falso** inyectado (sin Docling real).
"""

from __future__ import annotations

import pytest

from voucherflow.models.docling import (
    EXTENSIONES_SOPORTADAS,
    Box,
    DoclingConverter,
    ProcessedDocument,
    _tipo_entrada_basico,
)


class FakeItem:
    """Ítem mínimo de Docling (texto + prov con bbox)."""

    def __init__(self, texto: str, l=0.0, t=0.0, r=10.0, b=5.0) -> None:
        self.text = texto
        self.prov = [type("P", (), {"bbox": type("B", (), {"l": l, "t": t, "r": r, "b": b})()})()]


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


class FakeConverter:
    """Convertidor Docling falso inyectado (evita red/modelos)."""

    def __init__(self, doc: FakeDoc | None = None) -> None:
        self._doc = doc or FakeDoc()
        self.convert_calls: list[str] = []

    def convert(self, ruta: str):
        self.convert_calls.append(ruta)
        return type("R", (), {"document": self._doc})()


class TestDoclingConverter:
    def test_convierte_a_processdocument(self, tmp_path):
        archivo = tmp_path / "factura.jpg"
        archivo.write_bytes(b"\xff\xd8\xff\xe0")  # bytes falsos de imagen

        converter = DoclingConverter(converter=FakeConverter())
        doc = converter.convert(archivo)

        assert isinstance(doc, ProcessedDocument)
        assert doc.markdown == "# Factura\n\nA"
        assert doc.tipo_entrada == "imagen"
        assert doc.motor == "docling"
        assert doc.ruta == str(archivo)
        assert len(doc.boxes) >= 1
        assert isinstance(doc.boxes[0], Box)

    def test_archivo_no_existe_lanza_filenotfound(self):
        converter = DoclingConverter(converter=FakeConverter())
        with pytest.raises(FileNotFoundError):
            converter.convert("/no/existe/archivo.jpg")

    def test_extension_no_soportada_lanza_valueerror(self, tmp_path):
        archivo = tmp_path / "archivo.xyz"
        archivo.write_bytes(b"x")
        converter = DoclingConverter(converter=FakeConverter())
        with pytest.raises(ValueError) as exc:
            converter.convert(archivo)
        assert "no soportada" in str(exc.value)

    def test_tipo_entrada_basico_por_extension(self):
        assert _tipo_entrada_basico(".pdf") == "pdf"
        assert _tipo_entrada_basico(".jpg") == "imagen"
        assert _tipo_entrada_basico(".png") == "imagen"
        assert _tipo_entrada_basico(".docx") == "office"
        assert _tipo_entrada_basico(".txt") == "texto"
        assert _tipo_entrada_basico(".zzz") == "desconocido"

    def test_extensiones_soportadas_incluyen_principales(self):
        assert {".pdf", ".jpg", ".png", ".docx", ".txt", ".md"} <= EXTENSIONES_SOPORTADAS

    def test_construlle_sin_docling_instanciado(self):
        # Construir no debe importar/instanciar Docling (lazy).
        converter = DoclingConverter()
        assert converter._converter is None
        assert converter._converter_propio is True

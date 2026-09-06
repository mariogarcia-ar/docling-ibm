"""Tests del detector de tipo de entrada (F1 / T-101, épica E-DOC-1).

Validan la clasificación por extensión y la heurística barata que distingue
``pdf_texto`` de ``pdf_escaneado`` (doc 03 §4.1; regla "PDF escaneado" de
E-DOC-1).

Reglas duras del subplan F1 (§4): la suite default corre **sin** Docling real —
los PDFs de prueba se generan como bytes sintéticos (uno con ``/Font`` y otro
con ``/Subtype /Image``) y se escriben en ``tmp_path``.
"""

from __future__ import annotations

from pathlib import Path

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
# Helpers: archivos sintéticos por tipo
# ---------------------------------------------------------------------------

def _escribir(tmp_path: Path, nombre: str, contenido: bytes) -> Path:
    ruta = tmp_path / nombre
    ruta.write_bytes(contenido)
    return ruta


def _pdf_texto_sintetico() -> bytes:
    """PDF mínimo con capa de texto (declara una fuente ``/Font``)."""
    return b"%PDF-1.4\n1 0 obj\n<< /Type /Font >>\nendobj\n%%EOF\n"


def _pdf_escaneado_sintetico() -> bytes:
    """PDF mínimo escaneado (imagen de página, sin fuentes)."""
    return b"%PDF-1.4\n1 0 obj\n<< /Subtype /Image /Width 10 /Height 10 >>\nendobj\n%%EOF\n"


def _pdf_sin_senales() -> bytes:
    """PDF sin fuentes ni imágenes (caso borde: se asume capa de texto)."""
    return b"%PDF-1.4\n1 0 obj\n<< /Type /Catalog >>\nendobj\n%%EOF\n"


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
# Heurística pdf_texto / pdf_escaneado
# ---------------------------------------------------------------------------

class TestClasificacionPdf:
    def test_pdf_con_fuente_es_pdf_texto(self, tmp_path):
        ruta = _escribir(tmp_path, "texto.pdf", _pdf_texto_sintetico())
        te = detectar(ruta)
        assert te.tipo == "pdf_texto"
        assert te.ruta_ocr is False  # texto nativo, sin OCR

    def test_pdf_solo_imagen_es_pdf_escaneado(self, tmp_path):
        ruta = _escribir(tmp_path, "escaneado.pdf", _pdf_escaneado_sintetico())
        te = detectar(ruta)
        assert te.tipo == "pdf_escaneado"
        assert te.ruta_ocr is True  # convertir a imagen + OCR

    def test_pdf_sin_senales_se_asume_pdf_texto_sin_forzar_ocr(self, tmp_path):
        # Caso borde: sin /Font ni /Image no debe forzar OCR (E-DOC-1).
        ruta = _escribir(tmp_path, "borde.pdf", _pdf_sin_senales())
        te = detectar(ruta)
        assert te.tipo == "pdf_texto"
        assert te.ruta_ocr is False

    def test_pdf_mixto_dominante_imagen_es_escaneado(self, tmp_path):
        # 8 imágenes vs 1 fuente -> dominan las imágenes -> escaneado.
        contenido = b"%PDF-1.4\n"
        contenido += b"<< /Subtype /Image /Width 1 /Height 1 >>\n" * 8
        contenido += b"<< /Type /Font >>\n"
        contenido += b"%%EOF\n"
        ruta = _escribir(tmp_path, "mixto.pdf", contenido)
        te = detectar(ruta)
        assert te.tipo == "pdf_escaneado"
        assert te.ruta_ocr is True

    def test_pdf_mixto_dominante_texto_es_pdf_texto(self, tmp_path):
        # 1 imagen vs 4 fuentes -> domina el texto -> pdf_texto.
        contenido = b"%PDF-1.4\n"
        contenido += b"<< /Subtype /Image /Width 1 /Height 1 >>\n"
        contenido += b"<< /Type /Font >>\n" * 4
        contenido += b"%%EOF\n"
        ruta = _escribir(tmp_path, "mixto2.pdf", contenido)
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

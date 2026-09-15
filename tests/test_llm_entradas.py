"""Entradas del lote del laboratorio: imágenes **y** PDF renderizados.

⚠️ **La brecha que estos tests cierran.** ``voucherflow-lab`` mandaba al proveedor
solo lo que tuviera extensión de imagen, y un PDF no la tiene: en el corpus real
eso dejaba afuera **264 de 3.846 archivos** (6,9%), 190 de ellos comprobantes
fiscales, sin que el conteo lo dijera.

Los casos que fijan el comportamiento (todos medidos sobre el corpus):

* **Un PDF no es un archivo del lote, son sus páginas.** El corpus tiene 100 PDF
  de 2 y 3 páginas (441 páginas en total): la unidad del lote es la página.
* **Dos páginas no pueden escribir el mismo nombre.** Es el bug que se arregló:
  sin el ``_pNN`` en la clave, un PDF de 3 páginas producía una sola salida y el
  gasto de las otras dos se perdía (la deduplicación las colapsaba).
* **El render vive en un temporal y no puede ser el ``origen``** de nada: el
  archivo se borra al terminar la corrida, así que un registro que lo apunte deja
  de poder auditarse.
* **Lo que no entra se declara** (misma regla que ``corpus``).
"""

from __future__ import annotations

from pathlib import Path

import pymupdf as fitz
import pytest

from voucherflow.corpus.recorrido import salida_de
from voucherflow.llm import entradas
from voucherflow.llm.datos import buscar_datos, sin_sufijo_de_pagina


def _pdf(ruta: Path, paginas: int = 1, *, con_texto: bool = True) -> Path:
    """Un PDF real: con texto nativo (born-digital) o con una imagen (escaneado)."""
    ruta.parent.mkdir(parents=True, exist_ok=True)
    doc = fitz.open()
    for i in range(paginas):
        p = doc.new_page()
        if con_texto:
            p.insert_text((72, 72), f"FACTURA A 0001-0000000{i + 1}")
            p.insert_text((72, 100), "Total: $ 1.234,56")
        else:
            pix = fitz.Pixmap(fitz.csRGB, fitz.IRect(0, 0, 1190, 1684), False)
            pix.clear_with(255)
            p.insert_image(p.rect, pixmap=pix)
    doc.save(str(ruta))
    doc.close()
    return ruta


def _imagen(ruta: Path) -> Path:
    from PIL import Image

    ruta.parent.mkdir(parents=True, exist_ok=True)
    Image.new("RGB", (600, 800), (200, 180, 160)).save(ruta, "JPEG")
    return ruta


@pytest.fixture
def lote_con_pdf(tmp_path: Path) -> Path:
    """Corpus con la forma real: 2 imágenes y 1 PDF de 3 páginas."""
    raiz = tmp_path / "files"
    sub = raiz / "2025-08" / "2D2C9343"
    _imagen(sub / "una.jpg")
    _imagen(sub / "dos.png")
    _pdf(sub / "comprobante.pdf", paginas=3)
    return raiz


class TestArmarLote:
    def test_el_pdf_entra_como_una_imagen_por_pagina(self, lote_con_pdf):
        lote = entradas.armar([lote_con_pdf], lote_con_pdf)
        try:
            assert lote.pdfs == 1
            assert lote.total_paginas == 3
            assert len(lote.imagenes) == 5, "2 imágenes + 3 páginas"
            assert lote.imagenes_del_corpus == 2
        finally:
            entradas.limpiar(lote)

    def test_sin_pdf_no_hay_temporal(self, tmp_path):
        """Sin PDF no se crea ningún temporal (nada que limpiar)."""
        raiz = tmp_path / "files"
        _imagen(raiz / "2025-08" / "HASH" / "a.jpg")
        lote = entradas.armar([raiz], raiz)
        assert lote.temporal is None
        assert lote.pdfs == 0
        assert len(lote.imagenes) == 1

    def test_el_temporal_se_borra(self, lote_con_pdf):
        """⚠️ Sin la limpieza, cada corrida dejaba cientos de JPG en /tmp."""
        lote = entradas.armar([lote_con_pdf], lote_con_pdf)
        temporal = lote.temporal
        assert temporal is not None and temporal.is_dir()
        entradas.limpiar(lote)
        assert not temporal.exists()
        assert lote.temporal is None

    def test_un_pdf_ilegible_no_rompe_el_lote(self, tmp_path):
        """El resto del lote se procesa igual (mismo criterio que `corpus`)."""
        raiz = tmp_path / "files"
        _imagen(raiz / "a.jpg")
        roto = raiz / "roto.pdf"
        roto.write_text("esto no es un PDF", encoding="utf-8")

        lote = entradas.armar([raiz], raiz)
        try:
            assert lote.pdfs == 1, "se encontró el PDF..."
            assert lote.total_paginas == 0, "...pero no se pudo renderizar"
            assert len(lote.imagenes) == 1, "y la imagen sigue en el lote"
            assert lote.temporal is None
        finally:
            entradas.limpiar(lote)

    def test_lo_que_no_entra_se_declara(self, tmp_path):
        """Un `.docx` no desaparece del conteo: se declara con su extensión.

        ⚠️ El aviso tiene que nombrar la extensión: «no_soportado ×1» a secas no
        le dice al operador qué archivo revisar, y ese detalle es el que hace
        accionable el aviso (misma regla que ``corpus``).
        """
        raiz = tmp_path / "files"
        _imagen(raiz / "a.jpg")
        (raiz / "informe.docx").write_bytes(b"x")

        lote = entradas.armar([raiz], raiz)
        texto = "\n".join(lote.describir_ignorados())
        assert lote.total_ignorados == 1
        assert "1 archivo(s) fuera del lote" in texto
        assert "docx" in texto, f"el aviso tiene que nombrar la extensión:\n{texto}"

    def test_el_resumen_distingue_imagenes_de_paginas(self, lote_con_pdf):
        lote = entradas.armar([lote_con_pdf], lote_con_pdf)
        try:
            resumen = lote.resumen()
            assert "2 imagen(es)" in resumen
            assert "3 página(s)" in resumen
        finally:
            entradas.limpiar(lote)


class TestPaginasNoColisionan:
    """⚠️ El bug que estos tests fijan: dos páginas escribían el MISMO archivo.

    Sin el ``_pNN`` en la clave de salida, un PDF de 3 páginas producía una sola
    salida (la última pisaba a las anteriores) y la deduplicación del reporte de
    gastos colapsaba las tres llamadas en un apunte: el gasto real de dos páginas
    quedaba invisible.
    """

    def test_cada_pagina_tiene_su_destino(self, lote_con_pdf, tmp_path):
        lote = entradas.armar([lote_con_pdf], lote_con_pdf)
        try:
            destinos = [
                salida_de(lote.clave_de(img), lote_con_pdf, tmp_path / "out", "extraccion")
                for img in lote.imagenes
            ]
            assert len(set(destinos)) == len(destinos), (
                "dos páginas del mismo PDF no pueden compartir destino:\n"
                + "\n".join(str(d) for d in destinos)
            )
        finally:
            entradas.limpiar(lote)

    def test_el_destino_de_la_pagina_conserva_la_carpeta(self, lote_con_pdf, tmp_path):
        """El render vive en un temporal: sin el documento lógico, la salida
        perdería el nivel de carpeta (`2025-08/2D2C9343/`)."""
        lote = entradas.armar([lote_con_pdf], lote_con_pdf)
        try:
            pagina = next(i for i in lote.imagenes if lote.numero_de_pagina(i) == 2)
            destino = salida_de(lote.clave_de(pagina), lote_con_pdf, tmp_path / "out", "extraccion")
            assert destino.parent.name == "2D2C9343"
            assert destino.parent.parent.name == "2025-08"
            assert destino.name == "comprobante_p02.extraccion.json"
        finally:
            entradas.limpiar(lote)

    def test_la_pagina_conserva_la_extension_del_pdf(self, tmp_path):
        """La clave es ``x_p01.pdf``: identifica documento **y** página."""
        clave = entradas.clave_de_pagina(tmp_path / "doc.pdf", 1)
        assert clave.name == "doc_p01.pdf"
        clave = entradas.clave_de_pagina(tmp_path / "doc.pdf", 12)
        assert clave.name == "doc_p12.pdf", "el número va a 2 dígitos, ordenable"

    def test_el_render_no_es_el_documento(self, lote_con_pdf):
        """``documento_de`` devuelve el PDF; ``numero_de_pagina``, la página."""
        lote = entradas.armar([lote_con_pdf], lote_con_pdf)
        try:
            pagina = next(iter(lote.paginas))
            assert lote.documento_de(pagina).name == "comprobante.pdf"
            assert lote.numero_de_pagina(pagina) == 1
            assert lote.clave_de(pagina).name == "comprobante_p01.pdf"
        finally:
            entradas.limpiar(lote)


class TestDatosDeUnaPagina:
    """Los datos se cargan por **documento**, no por página."""

    def test_la_clave_de_pagina_encuentra_los_datos_del_pdf(self, tmp_path):
        """⚠️ Sin esto, un PDF con datos cargados fallaba con "no hay datos"."""
        datos = {"comprobante": {"tipo_comprobante": "A"}}
        clave, doc = buscar_datos(tmp_path / "comprobante_p01.pdf", datos)
        assert clave == "comprobante"
        assert doc == {"tipo_comprobante": "A"}

    def test_los_datos_por_carpeta_siguen_funcionando(self, tmp_path):
        """El hash del lote como clave (el caso del corpus)."""
        datos = {"2D2C9343": {"tipo_comprobante": "B"}}
        clave, doc = buscar_datos(tmp_path / "2D2C9343" / "x.jpg", datos)
        assert clave == "2D2C9343"
        assert doc == {"tipo_comprobante": "B"}

    def test_el_sufijo_de_pagina_se_quita_solo_si_esta(self):
        assert sin_sufijo_de_pagina("doc_p01") == "doc"
        assert sin_sufijo_de_pagina("doc_p123") == "doc"
        assert sin_sufijo_de_pagina("comprobante") == "comprobante"
        assert sin_sufijo_de_pagina("2025_p01_final") == "2025_p01_final", (
            "no es un sufijo de página: no se toca"
        )

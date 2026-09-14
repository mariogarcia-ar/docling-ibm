"""Lectura del corpus: qué entra, qué queda afuera y por qué.

⚠️ **El silencio que estos tests cierran.** `expandir` filtra por extensión y
avisa solo de las rutas que se le pasan **sueltas**; en un recorrido por carpeta,
lo que no matchea desaparecía. Un corpus real de 3.846 archivos informaba «3.582
imágenes»: los 264 restantes —**190 comprobantes fiscales en PDF**— no aparecían
en ningún conteo ni en ningún reporte.

Los PDF se detectaron justamente por esa diferencia de conteo, y ninguno estaba
duplicado como imagen (264 stems únicos, 0 coincidencias): se perdían completos.
"""

from __future__ import annotations

from pathlib import Path

from PIL import Image

from voucherflow.corpus import corrida
from voucherflow.corpus.lectura import (
    CATEGORIA_NO_SOPORTADO,
    CATEGORIA_SIN_EXTENSION,
    clasificar,
    expandir_pdf,
    limpiar_expansion,
)
from voucherflow.corpus.modelo import Opciones

EXTENSIONES = frozenset({".jpg", ".jpeg", ".png"})


def _pdf_con_paginas(ruta: Path, paginas: int = 1) -> Path:
    """Crea un PDF real de N páginas (para ejercitar el render de verdad)."""
    try:
        import pymupdf as fitz
    except ImportError:  # pragma: no cover
        import fitz

    doc = fitz.open()
    for _ in range(paginas):
        doc.new_page()
    doc.save(str(ruta))
    doc.close()
    return ruta


def _opciones(salida: Path, **over) -> Opciones:
    base = dict(
        salida=salida,
        lado_mayor=1024,
        calidad=80,
        piso_lado_menor=256,
        alinear=True,
        backend="pillow",
        formato="mismo",
        forzar=False,
        escribir=False,
        copiar_no_reducidas=False,
        workers=1,
        detalle=False,
    )
    base.update(over)
    return Opciones(**base)


class TestClasificar:
    def test_separa_procesables_pdf_e_ignorados(self, tmp_path):
        """Las tres categorías, con la extensión de cada exclusión."""
        archivos = [
            tmp_path / "a.jpg",
            tmp_path / "b.png",
            tmp_path / "c.pdf",
            tmp_path / "d.docx",
            tmp_path / "e.docx",
            tmp_path / "sin-extension",
        ]
        for archivo in archivos:
            archivo.write_bytes(b"x")

        r = clasificar(archivos, EXTENSIONES)

        assert [p.name for p in r.procesables] == ["a.jpg", "b.png"]
        assert [p.name for p in r.pdfs] == ["c.pdf"]
        assert r.ignorados[CATEGORIA_NO_SOPORTADO] == {"docx": 2}
        assert r.ignorados[CATEGORIA_SIN_EXTENSION] == {"": 1}
        assert r.total_ignorados == 3

    def test_el_total_cierra(self, tmp_path):
        """⚠️ El invariante: nada desaparece del conteo."""
        archivos = []
        for nombre in ("a.jpg", "b.pdf", "c.docx", "d.xlsx", "e.tiff"):
            archivo = tmp_path / nombre
            archivo.write_bytes(b"x")
            archivos.append(archivo)

        r = clasificar(archivos, EXTENSIONES)
        contados = len(r.procesables) + len(r.pdfs) + r.total_ignorados
        assert contados == len(archivos)

    def test_la_extension_no_pedida_es_una_categoria_distinta_de_pdf(self, tmp_path):
        """Un `.tif` no pedido no es lo mismo que un PDF: el aviso los separa."""
        archivo = tmp_path / "scan.tif"
        archivo.write_bytes(b"x")
        r = clasificar([archivo], EXTENSIONES)
        assert r.pdfs == []
        assert r.ignorados[CATEGORIA_NO_SOPORTADO] == {"tif": 1}

    def test_sin_ignorados_el_reporte_esta_vacio(self, tmp_path):
        """El aviso no aparece cuando no hay nada que declarar."""
        archivo = tmp_path / "a.jpg"
        archivo.write_bytes(b"x")
        r = clasificar([archivo], EXTENSIONES)
        assert r.describir_ignorados() == []

    def test_el_detalle_agrupa_y_ordena_por_cantidad(self, tmp_path):
        archivos = []
        for nombre in ("a.docx", "b.docx", "c.docx", "d.xlsx"):
            archivo = tmp_path / nombre
            archivo.write_bytes(b"x")
            archivos.append(archivo)
        r = clasificar(archivos, EXTENSIONES)
        (linea,) = r.describir_ignorados()
        assert "docx ×3" in linea
        assert linea.index("docx") < linea.index("xlsx")


class TestExpandirPdf:
    def test_una_pagina_por_pagina_del_pdf(self, tmp_path):
        pdf = _pdf_con_paginas(tmp_path / "doc.pdf", paginas=3)
        destino = tmp_path / "renders"

        creadas = expandir_pdf(pdf, destino, raiz=tmp_path)

        assert len(creadas) == 3
        assert [p.name for p in creadas] == [
            "doc_p01.jpg",
            "doc_p02.jpg",
            "doc_p03.jpg",
        ]
        for ruta in creadas:
            assert ruta.is_file()
            assert ruta.suffix == ".jpg"

    def test_el_render_espeja_las_carpetas_del_pdf(self, tmp_path):
        """⚠️ Sin espejar, dos PDF homónimos en meses distintos colisionarían.

        Además el destino de salida perdía el nivel de carpeta: el render vivía en
        un temporal, y su propia ruta no dice nada del documento.
        """
        sub = tmp_path / "2025-08" / "2D2C9343"
        sub.mkdir(parents=True)
        pdf = _pdf_con_paginas(sub / "doc.pdf", paginas=1)

        creadas = expandir_pdf(pdf, tmp_path / "renders", raiz=tmp_path)

        assert creadas[0].parent.name == "2D2C9343"
        assert creadas[0].parent.parent.name == "2025-08"

    def test_dos_pdf_homonimos_no_se_pisan(self, tmp_path):
        """El caso que el espejado evita: mismo nombre, distinto mes."""
        for mes in ("2025-08", "2025-09"):
            carpeta = tmp_path / mes / "HASH"
            carpeta.mkdir(parents=True)
            _pdf_con_paginas(carpeta / "doc.pdf", paginas=1)

        primera = expandir_pdf(
            tmp_path / "2025-08" / "HASH" / "doc.pdf",
            tmp_path / "renders",
            raiz=tmp_path,
        )
        segunda = expandir_pdf(
            tmp_path / "2025-09" / "HASH" / "doc.pdf",
            tmp_path / "renders",
            raiz=tmp_path,
        )
        assert primera[0] != segunda[0]
        assert primera[0].is_file() and segunda[0].is_file()

    def test_un_pdf_ilegible_no_rompe_el_lote(self, tmp_path):
        """Un PDF roto devuelve lista vacía: no aborta la corrida."""
        roto = tmp_path / "roto.pdf"
        roto.write_text("esto no es un PDF", encoding="utf-8")
        assert expandir_pdf(roto, tmp_path / "renders", raiz=tmp_path) == []

    def test_limpiar_borra_el_temporal(self, tmp_path):
        """⚠️ Sin esto, cada corrida con `--incluir-pdf` dejaba cientos de JPG."""
        from voucherflow.corpus.lectura import PdfExpandido

        temporal = tmp_path / "tmp"
        temporal.mkdir()
        (temporal / "x.jpg").write_bytes(b"x")
        limpiar_expansion(PdfExpandido(temporal=temporal, pares=[]))
        assert not temporal.exists()

    def test_limpiar_sin_expansion_no_falla(self):
        limpiar_expansion(None)


class TestPlanificarDeclaraLoIgnorado:
    """La costura: el plan tiene que exponer lo que dejó afuera."""

    def _corpus(self, tmp_path) -> Path:
        """Corpus con la forma real (`files/2025-08/<hash>/…`) y devuelve ``files``.

        ⚠️ Hay que devolver el nivel **`files`** y no el `<hash>`: la raíz de
        espejado se deriva de lo que se le pasa, así que apuntando al hash el
        destino sale plano (y eso es correcto, no un bug).
        """
        carpeta = tmp_path / "files" / "2025-08" / "HASH"
        carpeta.mkdir(parents=True)
        Image.new("RGB", (2000, 1500), "white").save(carpeta / "a.jpg")
        Image.new("RGB", (2000, 1500), "white").save(carpeta / "b.png")
        _pdf_con_paginas(carpeta / "c.pdf", paginas=1)
        (carpeta / "notas.docx").write_bytes(b"x")
        return tmp_path / "files"

    def test_sin_incluir_pdf_los_pdf_se_declaran(self, tmp_path):
        """El corpus real: 3.582 imágenes y 264 PDF ausentes, ahora nombrados."""
        corpus = self._corpus(tmp_path)
        _, _, tareas, clasificacion = corrida.planificar(
            [corpus], _opciones(tmp_path / "out")
        )
        assert len(tareas) == 2  # solo las imágenes
        assert len(clasificacion.pdfs) == 1
        assert clasificacion.total_ignorados == 1  # el docx

    def test_con_incluir_pdf_entran_al_lote(self, tmp_path):
        corpus = self._corpus(tmp_path)
        _, _, tareas, clasificacion = corrida.planificar(
            [corpus], _opciones(tmp_path / "out", incluir_pdf=True)
        )
        assert len(tareas) == 3  # 2 imágenes + 1 PDF
        assert clasificacion.expansion_pdf is not None
        corrida.limpiar_renders(clasificacion)

    def test_el_destino_del_pdf_usa_el_nivel_del_pdf_y_no_del_temporal(
        self, tmp_path
    ):
        """⚠️ El render vive en un temporal: su ruta no dice dónde va la salida.

        Sin esto la salida salía plana (`comprobante.jpg` en la raíz) en vez de
        `2025-08/HASH/comprobante.jpg`, y la reanudación no encontraría nada.
        """
        corpus = self._corpus(tmp_path)
        _, _, tareas, clasificacion = corrida.planificar(
            [corpus], _opciones(tmp_path / "out", incluir_pdf=True)
        )
        try:
            # El render del PDF se reconoce porque su origen está en el temporal.
            temporal = clasificacion.expansion_pdf.temporal
            pdf_tareas = [t for t in tareas if temporal in t[0].parents]
            assert len(pdf_tareas) == 1, "el PDF tiene que estar en el plan"
            _, destino = pdf_tareas[0]
            assert destino.parent.name == "HASH"
            assert destino.parent.parent.name == "2025-08"
        finally:
            corrida.limpiar_renders(clasificacion)

    def test_el_reporte_del_reporte_incluye_ignorados(self, tmp_path):
        """El JSON del reporte tiene que llevar lo que quedó afuera."""
        corpus = self._corpus(tmp_path)
        _, _, _, clasificacion = corrida.planificar(
            [corpus], _opciones(tmp_path / "out")
        )
        assert sum(clasificacion.ignorados[CATEGORIA_NO_SOPORTADO].values()) == 1


class TestAVisoEnElCLI:
    """El usuario tiene que ver el aviso, no solo el objeto interno."""

    def _correr(self, tmp_path, args_extra=()):
        from io import StringIO

        from voucherflow.cli.main import construir_parser
        from voucherflow.corpus import cli

        corpus = tmp_path / "files" / "2025-08" / "HASH"
        corpus.mkdir(parents=True)
        Image.new("RGB", (2000, 1500), "white").save(corpus / "a.jpg")
        _pdf_con_paginas(corpus / "c.pdf", paginas=1)
        (corpus / "notas.docx").write_bytes(b"x")

        # Se parsea con el parser REAL (mismo patrón que test_corpus_cli) y se
        # captura `stderr`, que es donde `log` escribe los avisos.
        args = construir_parser().parse_args(
            [
                "corpus",
                str(corpus),
                "--solo-medir",
                "-o",
                str(tmp_path / "out"),
                *args_extra,
            ]
        )
        buffer = StringIO()
        codigo = cli.main(args, entorno=cli.EntornoCorpus(stderr=buffer))
        return codigo, buffer.getvalue()

    def test_avisa_los_pdf_no_incluidos_y_menciona_la_bandera(self, tmp_path):
        _, err = self._correr(tmp_path)
        assert "PDF" in err
        assert "--incluir-pdf" in err
        assert "no incluidos" in err

    def test_avisa_los_ignorados_con_su_extension(self, tmp_path):
        _, err = self._correr(tmp_path)
        assert "ignorados" in err
        assert "docx" in err

    def test_con_la_bandera_deja_de_avisar(self, tmp_path):
        _, err = self._correr(tmp_path, ("--incluir-pdf",))
        assert "no incluidos" not in err
        assert "renderizados" in err

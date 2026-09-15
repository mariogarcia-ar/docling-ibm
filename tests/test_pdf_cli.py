"""Subcomando ``voucherflow pdf`` (``voucherflow.pdf.cli``).

⚠️ **Este archivo también fija el contrato de subcomandos del CLI.** Eran 12
(E-CLI-1 + los de auditoría + ``corpus``) y estaban congelados por
``test_cli_t601.py``. Sumar ``pdf`` fue una **decisión explícita**: la capacidad
—convertir PDF a imágenes— se necesitaba en la librería (el laboratorio manda
imágenes, no PDF) y existía como script suelto que no se podía importar ni
testear. Los tests que fijaban 12 se actualizaron a 13 a propósito.

Lo que estos tests cubren y no es cosmético:

* **La simulación y la corrida calculan el MISMO destino.** ``--solo-medir`` tiene
  que decir la verdad sobre qué archivos crea; si divergieran, la reanudación
  buscaría en otro lado que el que escribe (el bug que costó pagar dos veces un
  lote en el laboratorio).
* **El árbol se espeja desde una raíz estable.** Convertir ``var/files`` o
  ``var/files/2025-08`` tiene que escribir los mismos archivos.
* **Un fallo no aborta el lote** y lo que no es PDF **se declara**.
"""

from __future__ import annotations

from io import StringIO
from pathlib import Path

import pymupdf as fitz
import pytest

from voucherflow.cli.main import COMANDOS, DESPACHO, construir_parser, main
from voucherflow.pdf.cli import EntornoPdf
from voucherflow.pdf.cli import main as main_pdf

#: Lo que el contrato debe seguir diciendo: los 11 del DoD + ``extract-detect``
#: + ``corpus`` (12.º) + ``pdf`` (13.º, este cambio).
COMANDOS_ESPERADOS = {
    "process",
    "validate",
    "classify",
    "extract",
    "extract-detect",
    "run",
    "batch",
    "ask",
    "arca",
    "case",
    "hitl",
    "corpus",
    "pdf",
}


def _pdf(ruta: Path, paginas: int = 1, *, con_texto: bool = True) -> Path:
    """Un PDF real: con texto nativo o con una imagen a página completa."""
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


def _parsear(argv: list[str]):
    """Parsea ``pdf <argv>`` con el parser REAL del CLI (no uno de prueba)."""
    return construir_parser().parse_args(["pdf", *argv])


def _correr(argv: list[str], entorno: EntornoPdf | None = None) -> tuple[int, str, str]:
    """Corre el subcomando y devuelve ``(codigo, stdout, stderr)``."""
    entorno = entorno or EntornoPdf(stdout=StringIO(), stderr=StringIO())
    args = _parsear(argv)
    codigo = main_pdf(args, entorno=entorno)
    return codigo, entorno.stdout.getvalue(), entorno.stderr.getvalue()


@pytest.fixture
def entorno() -> EntornoPdf:
    return EntornoPdf(stdout=StringIO(), stderr=StringIO())


@pytest.fixture
def lote(tmp_path: Path) -> Path:
    """Corpus con la forma real: un PDF de 3 páginas y un `.jpg` que no es PDF."""
    raiz = tmp_path / "files"
    sub = raiz / "2025-08" / "2D2C9343"
    _pdf(sub / "comprobante.pdf", paginas=3)
    from PIL import Image

    Image.new("RGB", (300, 400), "white").save(sub / "foto.jpg", "JPEG")
    return raiz


class TestContrato:
    """El 13.º subcomando, declarado a propósito."""

    def test_son_trece_y_estan_todos(self):
        assert set(COMANDOS) == COMANDOS_ESPERADOS
        assert len(COMANDOS) == 13

    def test_el_despacho_cubre_los_comandos(self):
        assert set(DESPACHO) == COMANDOS_ESPERADOS

    def test_pdf_esta_en_el_parser(self):
        assert _parsear(["x.pdf"]).comando == "pdf"

    def test_no_se_agrego_ningun_otro_comando(self):
        """Control del contrato: si aparece uno nuevo, esta lista lo delata."""
        assert set(COMANDOS) - COMANDOS_ESPERADOS == set()

    def test_main_despacha_pdf(self, lote, tmp_path, capsys):
        """El despacho del CLI principal llega al subcomando (import diferido)."""
        codigo = main(["pdf", str(lote), "-o", str(tmp_path / "out")])
        assert codigo == 0
        assert "Resumen" in capsys.readouterr().out


class TestConversion:
    def test_una_imagen_por_pagina(self, lote, tmp_path, entorno):
        codigo, salida, _ = _correr([str(lote), "-o", str(tmp_path / "out")], entorno)
        assert codigo == 0
        escritas = sorted((tmp_path / "out").rglob("*.jpg"))
        assert len(escritas) == 3, "un PDF de 3 páginas → 3 imágenes"
        assert "páginas                   : 3" in salida

    def test_el_nombre_lleva_el_documento_con_varios_pdf(self, lote, tmp_path, entorno):
        """⚠️ Con varios PDF, `pagina_1.jpg` se pisaría entre documentos."""
        sub = lote / "2025-08" / "2D2C9343"
        _pdf(sub / "otro.pdf", paginas=1)

        _correr([str(lote), "-o", str(tmp_path / "out")], entorno)
        nombres = sorted(p.name for p in (tmp_path / "out").rglob("*.jpg"))
        assert "comprobante_pagina_1.jpg" in nombres
        assert "otro_pagina_1.jpg" in nombres
        assert len(nombres) == 4, "y ninguna se pisó"

    def test_con_un_solo_pdf_usa_el_patron_original(self, tmp_path, entorno):
        """Compatibilidad con el script que se retiró (`pagina_1.jpg`)."""
        pdf = _pdf(tmp_path / "uno.pdf", paginas=1)
        _correr([str(pdf), "-o", str(tmp_path / "out")], entorno)
        assert (tmp_path / "out" / "pagina_1.jpg").is_file()

    def test_el_render_es_la_pagina_completa(self, lote, tmp_path, entorno):
        """⚠️ El bug que este comando hereda arreglado: un PDF con texto nativo
        recortado a su logo daba 2001x776 en vez de la A4 completa."""
        from PIL import Image

        _correr([str(lote), "-o", str(tmp_path / "out")], entorno)
        render = next((tmp_path / "out").rglob("pagina_1.jpg"))
        ancho, alto = Image.open(render).size
        assert alto > ancho, "la A4 es vertical; un logo no"
        assert ancho > 2000, f"esperaba la página a 300 dpi, no un logo ({ancho}px)"

    def test_el_arbol_se_espeja(self, lote, tmp_path, entorno):
        """⚠️ Sin espejar, dos PDF homónimos de meses distintos colisionarían."""
        _correr([str(lote), "-o", str(tmp_path / "out")], entorno)
        render = next((tmp_path / "out").rglob("pagina_1.jpg"))
        assert render.parent.name == "2D2C9343"
        assert render.parent.parent.name == "2025-08"

    def test_la_raiz_no_depende_de_la_ruta_pasada(self, lote, tmp_path, entorno):
        """Convertir `files` o `files/2025-08` escribe LOS MISMOS archivos."""
        _correr([str(lote), "-o", str(tmp_path / "a")], entorno)
        _correr([str(lote / "2025-08"), "-o", str(tmp_path / "b")], entorno)
        primera = sorted(p.relative_to(tmp_path / "a") for p in (tmp_path / "a").rglob("*.jpg"))
        segunda = sorted(p.relative_to(tmp_path / "b") for p in (tmp_path / "b").rglob("*.jpg"))
        assert primera == segunda

    def test_los_pdf_no_entran_dos_veces_con_la_misma_raiz(self, lote, tmp_path, entorno):
        """Reanudación: la segunda corrida no re-renderiza nada."""
        _correr([str(lote), "-o", str(tmp_path / "out")], entorno)
        _, salida, _ = _correr([str(lote), "-o", str(tmp_path / "out")], entorno)
        assert "ya existían             : 3" in salida

    def test_forzar_rehace(self, lote, tmp_path, entorno):
        _correr([str(lote), "-o", str(tmp_path / "out")], entorno)
        _, salida, _ = _correr([str(lote), "-o", str(tmp_path / "out"), "--forzar"], entorno)
        assert "escritas                : 3" in salida

    def test_el_dpi_cambia_las_dimensiones(self, tmp_path, entorno):
        from PIL import Image

        pdf = _pdf(tmp_path / "uno.pdf", paginas=1)
        _correr([str(pdf), "-o", str(tmp_path / "a"), "--dpi", "72"], entorno)
        chico = Image.open(tmp_path / "a" / "pagina_1.jpg").size
        _correr([str(pdf), "-o", str(tmp_path / "b"), "--dpi", "150"], entorno)
        grande = Image.open(tmp_path / "b" / "pagina_1.jpg").size
        assert grande[0] > chico[0] * 1.5


class TestRangoYPatron:
    def test_primera_y_ultima(self, lote, tmp_path, entorno):
        _correr(
            [str(lote), "-o", str(tmp_path / "out"), "--primera", "2", "--ultima", "3"],
            entorno,
        )
        nombres = sorted(p.name for p in (tmp_path / "out").rglob("*.jpg"))
        assert len(nombres) == 2
        assert any("pagina_2" in n for n in nombres)
        assert not any("pagina_1" in n for n in nombres)

    def test_ultima_mayor_al_total_se_recorta(self, tmp_path, entorno):
        pdf = _pdf(tmp_path / "uno.pdf", paginas=2)
        codigo, salida, _ = _correr(
            [str(pdf), "-o", str(tmp_path / "out"), "--ultima", "99"], entorno
        )
        assert codigo == 0
        assert "páginas                   : 2" in salida

    def test_un_rango_vacio_es_error_de_uso(self, tmp_path, entorno):
        """⚠️ Pedir la página 9 de un PDF de 2 se declara y sale con 2.

        No es una corrida exitosa (no se hizo nada) ni un fallo de la corrida: es
        una combinación de argumentos que no puede cumplirse.
        """
        pdf = _pdf(tmp_path / "uno.pdf", paginas=2)
        codigo, _, err = _correr(
            [str(pdf), "-o", str(tmp_path / "out"), "--primera", "9"], entorno
        )
        assert codigo == 2
        assert "rango pedido sale vacío" in err
        assert not (tmp_path / "out").exists()

    def test_patron_con_tokens(self, tmp_path, entorno):
        pdf = _pdf(tmp_path / "doc.pdf", paginas=2)
        _correr(
            [str(pdf), "-o", str(tmp_path / "out"), "--patron", "c{pagina}_de_{total}"],
            entorno,
        )
        nombres = sorted(p.name for p in (tmp_path / "out").rglob("*.jpg"))
        assert nombres == ["c1_de_2.jpg", "c2_de_2.jpg"]

    def test_patron_invalido_da_error_de_uso(self, tmp_path, entorno):
        pdf = _pdf(tmp_path / "doc.pdf", paginas=1)
        codigo, _, err = _correr([str(pdf), "--patron", "c{i}"], entorno)
        assert codigo == 2
        assert "no es válido" in err and "pagina" in err

    def test_patron_con_separador_se_rechaza(self, tmp_path, entorno):
        """Un patrón no puede escribir fuera de la carpeta de salida."""
        pdf = _pdf(tmp_path / "doc.pdf", paginas=1)
        codigo, _, err = _correr([str(pdf), "--patron", "../{pagina}"], entorno)
        assert codigo == 2
        assert "separadores de carpeta" in err


class TestDeclaraciones:
    def test_lo_que_no_es_pdf_se_declara(self, lote, tmp_path, entorno):
        """⚠️ Un barrido que se come archivos en silencio ya costó caro."""
        _, _, err = _correr([str(lote), "-o", str(tmp_path / "out")], entorno)
        assert "fuera (no son PDF" in err
        assert "jpg" in err

    def test_sin_pdf_da_error_de_uso(self, tmp_path, entorno):
        carpeta = tmp_path / "vacia"
        carpeta.mkdir()
        (carpeta / "a.jpg").write_bytes(b"x")
        codigo, _, err = _correr([str(carpeta), "-o", str(tmp_path / "out")], entorno)
        assert codigo == 2
        assert "no hay ningún PDF" in err

    def test_una_ruta_inexistente_da_error_de_uso(self, tmp_path, entorno):
        codigo, _, err = _correr([str(tmp_path / "no-existe"), "-o", str(tmp_path / "out")], entorno)
        assert codigo == 2
        assert "no existe" in err

    def test_un_pdf_ilegible_no_aborta_el_lote(self, lote, tmp_path, entorno):
        (lote / "2025-08" / "2D2C9343" / "roto.pdf").write_text("no soy un pdf", encoding="utf-8")
        codigo, _, err = _correr([str(lote), "-o", str(tmp_path / "out")], entorno)
        assert "no se pudo leer" in err
        assert len(list((tmp_path / "out").rglob("*.jpg"))) == 3, "el resto se procesó"
        assert codigo == 0, "un PDF roto es un problema declarado, no un fallo del comando"

    def test_el_plan_se_informa_antes_de_trabajar(self, lote, tmp_path, entorno):
        _, _, err = _correr([str(lote), "-o", str(tmp_path / "out")], entorno)
        assert "raíz de espejado" in err
        assert "render           : 300 dpi" in err
        assert "recorte automático" in err


class TestSimulacion:
    def test_no_escribe_ni_un_archivo(self, lote, tmp_path, entorno):
        codigo, salida, _ = _correr(
            [str(lote), "-o", str(tmp_path / "out"), "--solo-medir"], entorno
        )
        assert codigo == 0
        assert not (tmp_path / "out").exists(), "--solo-medir no debe crear la salida"
        assert "se escribirían" in salida

    def test_el_destino_que_anuncia_es_el_que_usa(self, lote, tmp_path, entorno):
        """⚠️ Si el plan y la escritura divergieran, la simulación mentiría."""
        _correr([str(lote), "-o", str(tmp_path / "out"), "--solo-medir"], entorno)
        codigo = main_pdf(
            _parsear([str(lote), "-o", str(tmp_path / "out")]),
            entorno=EntornoPdf(stdout=StringIO(), stderr=StringIO()),
        )
        assert codigo == 0
        assert len(list((tmp_path / "out").rglob("*.jpg"))) == 3


class TestOpciones:
    def test_la_salida_por_defecto_cuelga_de_var(self):
        """El default vive bajo `var/`, la carpeta de datos (gitignoreada)."""
        from voucherflow.pdf.cli import carpeta_por_defecto

        assert carpeta_por_defecto().endswith("paginas")
        assert "var" in carpeta_por_defecto()

    def test_la_salida_pedida_gana(self, tmp_path, entorno):
        codigo, _, _ = _correr(
            [str(_pdf(tmp_path / "d.pdf")), "-o", str(tmp_path / "mia")], entorno
        )
        assert codigo == 0
        assert (tmp_path / "mia" / "pagina_1.jpg").is_file()

    def test_calidad_invalida_da_2(self):
        with pytest.raises(SystemExit) as excinfo:
            _parsear(["x.pdf", "--calidad", "0"])
        assert excinfo.value.code == 2

    def test_dpi_invalido_da_2(self):
        with pytest.raises(SystemExit) as excinfo:
            _parsear(["x.pdf", "--dpi", "0"])
        assert excinfo.value.code == 2


class TestRecorte:
    def test_por_defecto_decide_por_pagina(self, tmp_path, entorno):
        """Un escaneado (imagen a página completa) se recorta; el born-digital no."""
        from PIL import Image

        escaneado = _pdf(tmp_path / "scan.pdf", con_texto=False)
        _correr([str(escaneado), "-o", str(tmp_path / "out")], entorno)
        ancho, alto = Image.open(tmp_path / "out" / "pagina_1.jpg").size
        assert alto > ancho, "el escaneado también tiene que salir como la página"

    def test_sin_recortar_fuerza_la_pagina(self, tmp_path, entorno):
        from PIL import Image

        escaneado = _pdf(tmp_path / "scan.pdf", con_texto=False)
        _correr([str(escaneado), "-o", str(tmp_path / "out"), "--sin-recortar"], entorno)
        assert Image.open(tmp_path / "out" / "pagina_1.jpg").size[1] > 0

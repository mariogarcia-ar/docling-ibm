"""Recorrido del corpus y espejado del árbol (``voucherflow.corpus.recorrido``).

⚠️ **Este archivo es el que más importa de la suite de corpus.** ``raiz_espejado``
decide dónde se escribe cada resultado, y de esa decisión depende la
**reanudación**: si el nivel de carpetas cambia entre corridas, lo ya hecho no se
encuentra y **se vuelve a pagar** (el bug real: 5 documentos, dos veces). Por eso
se fija acá, con la propiedad que lo hace imposible:

    la MISMA imagen escribe SIEMPRE el MISMO archivo,
    sin importar desde qué subcarpeta se invoque la corrida.

Esa regla la comparten el pipeline (``voucherflow.llm``) y la reducción de
imágenes (``voucherflow.corpus``) — antes cada uno tenía su copia.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from voucherflow.corpus.recorrido import (
    es_nivel_de_corpus,
    esta_dentro,
    expandir,
    raiz_espejado,
    salida_de,
)


@pytest.fixture
def corpus(tmp_path: Path) -> Path:
    """Un corpus con la forma real: ``files/AAAA-MM/<hash8>/<archivo>``."""
    for mes, lote, nombre in (
        ("2025-08", "2D2C9343", "factura.jpg"),
        ("2025-08", "2D2C9343", "otra.png"),
        ("2025-09", "AABBCCDD", "tercera.jpeg"),
    ):
        carpeta = tmp_path / "files" / mes / lote
        carpeta.mkdir(parents=True, exist_ok=True)
        (carpeta / nombre).write_bytes(b"x")
    return tmp_path / "files"


class TestEsNivelDeCorpus:
    """Distingue un nivel del corpus de una raíz elegida a mano."""

    @pytest.mark.parametrize("nombre", ["2025-08", "2025_8", "2025.8", "202508"])
    def test_reconoce_un_mes(self, nombre):
        assert es_nivel_de_corpus(nombre) is True

    @pytest.mark.parametrize("nombre", ["2D2C9343", "AABBCCDD", "00000000"])
    def test_reconoce_un_hash_de_lote(self, nombre):
        assert es_nivel_de_corpus(nombre) is True

    @pytest.mark.parametrize(
        "nombre", ["files", "lote-final", "mi_corpus", "procesados", "2025"]
    )
    def test_una_raiz_elegida_no_es_nivel_de_corpus(self, nombre):
        """Si estos dieran True, la raíz subiría de más y cambiaría la salida."""
        assert es_nivel_de_corpus(nombre) is False

    def test_un_hash_mas_corto_o_mas_largo_no_matchea(self):
        assert es_nivel_de_corpus("2D2C934") is False
        assert es_nivel_de_corpus("2D2C93434") is False


class TestRaizEspejado:
    """La raíz no puede depender de la ruta pasada."""

    def test_la_misma_imagen_escribe_el_mismo_archivo_desde_tres_rutas(self, corpus):
        """LA PROPIEDAD CLAVE: el bug que hizo pagar dos veces, hecho imposible."""
        imagen = corpus / "2025-08" / "2D2C9343" / "factura.jpg"
        salida = Path("/tmp/salida")

        destinos = set()
        for ruta in (corpus, corpus / "2025-08", corpus / "2025-08" / "2D2C9343"):
            raiz, _ = raiz_espejado([ruta], None)
            destinos.add(str(salida_de(imagen, raiz, salida, "validacion")))

        assert destinos == {"/tmp/salida/2025-08/2D2C9343/factura.validacion.json"}

    def test_asciende_salteando_el_mes(self, corpus):
        raiz, motivo = raiz_espejado([corpus / "2025-08"], None)
        assert raiz == corpus
        assert "2025-08" in motivo

    def test_asciende_salteando_el_mes_y_el_hash(self, corpus):
        raiz, motivo = raiz_espejado([corpus / "2025-08" / "2D2C9343"], None)
        assert raiz == corpus
        assert "2D2C9343" in motivo and "2025-08" in motivo

    def test_una_carpeta_elegida_detiene_la_subida(self, corpus):
        """``files`` no es un nivel de corpus: la raíz se queda ahí."""
        raiz, motivo = raiz_espejado([corpus], None)
        assert raiz == corpus
        assert motivo == "derivada de las rutas"

    def test_raiz_explicita_manda_siempre(self, corpus):
        """``--raiz`` gana incluso si la ruta ya tiene forma de corpus."""
        explicita = corpus.parent / "otra"
        raiz, motivo = raiz_espejado([corpus / "2025-08"], explicita)
        assert raiz == explicita
        assert "--raiz" in motivo

    def test_sin_rutas_existentes_cae_al_cwd(self, tmp_path):
        """Un motivo declarado es mejor que una raíz inventada en silencio."""
        raiz, motivo = raiz_espejado([tmp_path / "no-existe"], None)
        assert raiz == Path.cwd()
        assert "cwd" in motivo

    def test_varias_rutas_comparten_ancestro(self, corpus):
        raiz, _ = raiz_espejado([corpus / "2025-08", corpus / "2025-09"], None)
        assert raiz == corpus

    def test_sube_si_la_ruta_esta_dentro_de_la_salida(self, tmp_path):
        """Apuntar a una subcarpeta de la salida no debe ANIDAR la salida.

        Sin esto, ``corpus --salida OUT OUT/2025-08`` escribiría
        ``OUT/OUT/2025-08/…``: la salida se comería a sí misma en cada corrida.
        La raíz sube hasta la salida misma.
        """
        salida = tmp_path / "salida"
        dentro = salida / "2025-08"
        dentro.mkdir(parents=True)
        raiz, motivo = raiz_espejado([dentro], None, salida)
        assert raiz == salida
        assert "2025-08" in motivo


class TestExpansión:
    """El recorrido es determinista y avisa lo que ignora."""

    def test_encuentra_los_archivos_recursivo(self, corpus):
        assert len(expandir([corpus], frozenset({".jpg", ".jpeg", ".png"}))) == 3

    def test_es_determinista(self, corpus):
        ext = frozenset({".jpg", ".jpeg", ".png"})
        assert expandir([corpus], ext) == expandir([corpus], ext)

    def test_filtra_por_extension(self, corpus):
        assert len(expandir([corpus], frozenset({".png"}))) == 1

    def test_un_archivo_suelto_se_acepta(self, corpus):
        imagen = corpus / "2025-08" / "2D2C9343" / "factura.jpg"
        assert expandir([imagen], frozenset({".jpg"})) == [imagen]

    def test_ignora_extension_no_pedida_y_lo_dice(self, corpus, capsys):
        """Un archivo raro no aborta el lote: se avisa por stderr."""
        otro = corpus / "notas.txt"
        otro.write_text("no es una imagen", encoding="utf-8")
        assert expandir([otro], frozenset({".jpg"})) == []
        assert "Se ignora" in capsys.readouterr().err

    def test_ignora_una_ruta_inexistente_y_lo_dice(self, tmp_path, capsys):
        assert expandir([tmp_path / "no-existe"], frozenset({".jpg"})) == []
        assert "no existe" in capsys.readouterr().err

    def test_no_duplica_si_se_pasa_el_archivo_y_la_carpeta(self, corpus):
        """Pasar la carpeta y una imagen de adentro no procesa la imagen dos veces.

        Importa: procesar dos veces el mismo archivo es pagar dos veces (en el
        pipeline) o reencodear dos veces (acá).
        """
        ext = frozenset({".jpg"})
        imagen = corpus / "2025-08" / "2D2C9343" / "factura.jpg"
        assert expandir([corpus, imagen], ext) == [imagen]
        assert expandir([imagen, imagen], ext) == [imagen]


class TestEstaDentro:
    """Evita reprocesar la propia salida (bucle infinito de corpus)."""

    def test_detecta_un_hijo(self, tmp_path):
        (tmp_path / "a" / "b").mkdir(parents=True)
        assert esta_dentro(tmp_path / "a" / "b", tmp_path) is True

    def test_un_hermano_no_esta_dentro(self, tmp_path):
        (tmp_path / "a").mkdir()
        (tmp_path / "b").mkdir()
        assert esta_dentro(tmp_path / "b", tmp_path / "a") is False

    def test_esta_dentro_resuelve_sin_que_exista(self, tmp_path):
        """``resolve()`` no exige que la ruta exista: una futura entra igual.

        Es lo que se quiere: la salida todavía no está creada cuando se decide
        qué archivos procesar.
        """
        assert esta_dentro(tmp_path / "no-existe", tmp_path) is True

    def test_una_ruta_de_afuera_no_esta_dentro(self, tmp_path):
        assert esta_dentro(Path("/etc"), tmp_path) is False


class TestSalidaDe:
    """``salida_de`` es la función ÚNICA: escribir y reanudar no pueden divergir."""

    def test_conserva_la_estructura_y_cambia_el_sufijo(self, corpus):
        imagen = corpus / "2025-08" / "2D2C9343" / "factura.jpg"
        destino = salida_de(imagen, corpus, Path("/out"), "extraccion")
        assert destino == Path("/out/2025-08/2D2C9343/factura.extraccion.json")

    def test_una_imagen_fuera_de_la_raiz_usa_solo_el_nombre(self, tmp_path, corpus):
        """Sin parentesco no se inventa una jerarquía: el archivo va a la raíz."""
        ajena = tmp_path / "ajena.jpg"
        destino = salida_de(ajena, corpus, Path("/out"), "v")
        assert destino == Path("/out/ajena.v.json")

    def test_es_estable_entre_llamadas(self, corpus):
        imagen = corpus / "2025-08" / "2D2C9343" / "factura.jpg"
        assert salida_de(imagen, corpus, Path("/out"), "v") == salida_de(
            imagen, corpus, Path("/out"), "v"
        )

    def test_sufijo_vacio_reemplaza_la_extension(self, corpus):
        """El caso de la reducción de imágenes: escribe la imagen, no un sidecar.

        Con sufijo vacío la extensión se REEMPLAZA: ``factura.jpg`` →
        ``out/…/factura.jpg`` (y quien llame decide qué extensión quiere).
        """
        imagen = corpus / "2025-08" / "2D2C9343" / "factura.jpg"
        destino = salida_de(imagen, corpus, Path("/out"), "")
        assert destino == Path("/out/2025-08/2D2C9343/factura.jpg")

    def test_sufijo_vacio_conserva_el_nivel_de_carpetas(self, corpus):
        """La propiedad que importa: el espejado no depende del llamador."""
        imagen = corpus / "2025-08" / "2D2C9343" / "factura.jpg"
        desde_raiz = salida_de(imagen, corpus, Path("/out"), "")
        raiz_mes, _ = raiz_espejado([corpus / "2025-08"], None)
        desde_mes = salida_de(imagen, raiz_mes, Path("/out"), "")
        assert desde_raiz == desde_mes

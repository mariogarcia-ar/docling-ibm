"""Tests de los fixtures de datos reales (v2/tests/fixtures).

`files/` es una carpeta temporal e ignorada por git (`.gitignore`), por lo que
los tests usan copias versionadas en `v2/tests/fixtures/`. Este módulo valida
que el set de fixtures esté completo y sea consistente con su `manifest.json`
(10 grandes + 10 chicos + 20 otros + 5 pdf_escaneados).

Criterio (ver `fixtures/README.md`): archivos de `files/` ordenados por tamaño
→ 10 más grandes, 10 más chicos y 20 del rango medio; en F1/T-105 se agregó un
grupo ``pdf_escaneados`` con 5 PDFs sin capa de texto (paridad de PDF escaneado).
"""

from __future__ import annotations


class TestManifest:
    def test_manifest_existe_y_tiene_45(self, fixtures_manifest):
        assert fixtures_manifest, "Falta tests/fixtures/manifest.json"
        archivos = fixtures_manifest["archivos"]
        assert len(archivos) == 45, f"El manifest debe listar 45 archivos, tiene {len(archivos)}"

    def test_conteo_por_grupo(self, fixtures_manifest):
        from collections import Counter

        grupos = Counter(a["grupo"] for a in fixtures_manifest["archivos"])
        assert grupos["grandes"] == 10, grupos
        assert grupos["chicos"] == 10, grupos
        assert grupos["otros"] == 20, grupos
        assert grupos["pdf_escaneados"] == 5, grupos

    def test_manifest_archivos_tienen_metadata(self, fixtures_manifest):
        for a in fixtures_manifest["archivos"]:
            for campo in ("id", "grupo", "archivo", "extension", "tamano_bytes"):
                assert campo in a, f"Falta {campo} en {a}"
            assert a["tamano_bytes"] > 0


class TestArchivos:
    def test_todos_los_archivos_del_manifest_existen(self, fixtures_dir, fixtures_manifest):
        for a in fixtures_manifest["archivos"]:
            p = fixtures_dir / a["archivo"]
            assert p.exists(), f"No existe {p}"

    def test_no_vacios_y_tamano_consistente(self, fixtures_dir, fixtures_manifest):
        for a in fixtures_manifest["archivos"]:
            p = fixtures_dir / a["archivo"]
            assert p.stat().st_size == a["tamano_bytes"], f"Tamaño cambiado: {p}"
            assert p.stat().st_size > 0

    def test_grupos_cubren_tipos_de_entrada(self, fixtures_archivos):
        # Debe haber al menos una imagen raster (jpg/jpeg/png) y un pdf entre
        # los fixtures (cubren los tipos de entrada de F1).
        exts = {p.suffix.lower() for p in fixtures_archivos}
        assert exts & {".jpg", ".jpeg", ".png"}, f"Faltan imágenes raster: {exts}"
        assert ".pdf" in exts, f"Faltan pdfs: {exts}"

    def test_pdfs_escaneados_no_tienen_capa_de_texto(self, fixtures_dir):
        # Los PDFs del grupo pdf_escaneados deben detectarse como pdf_escaneado
        # (T-101, PyMuPDF): son la materia prima de la paridad T-105.
        from voucherflow.processing.type_detector import detectar

        carpeta = fixtures_dir / "pdf_escaneados"
        pdfs = sorted(carpeta.glob("*.pdf"))
        assert len(pdfs) == 5, f"Esperaba 5 pdfs escaneados, hay {len(pdfs)}"
        for p in pdfs:
            te = detectar(p)
            assert te.tipo == "pdf_escaneado", f"{p.name} no es pdf_escaneado: {te.motivo}"
            assert te.ruta_ocr is True


class TestGoldenReferencia:
    def test_casos_csv_apuntan_a_fixtures_no_a_files(self, golden_dir):
        # El golden set debe referenciar copias versionadas (fixtures), nunca
        # la carpeta temporal files/.
        import csv

        with (golden_dir / "casos.csv").open(encoding="utf-8") as fh:
            for fila in csv.DictReader(fh):
                ruta = fila["ruta"]
                assert "v2/tests/fixtures/" in ruta, f"Ruta no apunta a fixtures: {ruta}"
                assert not ruta.startswith("files/"), f"Ruta a files/ no permitida: {ruta}"

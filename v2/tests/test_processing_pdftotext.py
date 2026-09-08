"""Tests del helper ``pdftotext --layout`` (F1 / T-104, ruta texto nativo).

Validan ``extraer_con_pdftotext_layout`` y ``pdftotext_disponible`` de
``voucherflow.processing.pdftotext`` (PROC.md §5.2: para PDF apto,
``pdftotext --layout`` recupera el layout de columnas que Docling aplana).

Reglas duras (subplan F1 §4): la suite default corre **sin** poppler real ni
Docling — se mockean ``shutil.which`` y ``subprocess.run`` para no depender de
que ``pdftotext`` esté instalado en el sistema que corre los tests.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from voucherflow.processing.pdftotext import (
    PDFTOTEXT_BIN,
    extraer_con_pdftotext_layout,
    pdftotext_disponible,
)


class TestPdftotextDisponible:
    def test_true_cuando_which_encuentra_binario(self, monkeypatch):
        monkeypatch.setattr(
            "voucherflow.processing.pdftotext.shutil.which", lambda _bin: "/usr/bin/pdftotext"
        )
        assert pdftotext_disponible() is True

    def test_false_cuando_which_no_encuentra(self, monkeypatch):
        monkeypatch.setattr(
            "voucherflow.processing.pdftotext.shutil.which", lambda _bin: None
        )
        assert pdftotext_disponible() is False


class TestExtraerConPdftotextLayout:
    def test_devuelve_texto_cuando_subprocess_ok(self, tmp_path, monkeypatch):
        pdf = tmp_path / "apto.pdf"
        pdf.write_bytes(b"%PDF-1.4 (ficticio)")

        def _fake_run(cmd, **kwargs):  # noqa: ARG001
            # Escribe el archivo de salida que pdftotext dejaría.
            salida = Path(cmd[-1])
            salida.write_text("COL1   | COL2\nValor1 | Valor2\n", encoding="utf-8")
            return type("R", (), {"returncode": 0})()

        monkeypatch.setattr(
            "voucherflow.processing.pdftotext.shutil.which", lambda _bin: "/usr/bin/pdftotext"
        )
        monkeypatch.setattr(
            "voucherflow.processing.pdftotext.subprocess.run", _fake_run
        )
        texto = extraer_con_pdftotext_layout(pdf)
        assert texto is not None
        assert "COL1" in texto and "COL2" in texto

    def test_devuelve_none_si_no_disponible(self, tmp_path, monkeypatch):
        pdf = tmp_path / "apto.pdf"
        pdf.write_bytes(b"%PDF-1.4")
        monkeypatch.setattr(
            "voucherflow.processing.pdftotext.shutil.which", lambda _bin: None
        )
        assert extraer_con_pdftotext_layout(pdf) is None

    def test_devuelve_none_si_subprocess_falla(self, tmp_path, monkeypatch):
        pdf = tmp_path / "apto.pdf"
        pdf.write_bytes(b"%PDF-1.4")
        monkeypatch.setattr(
            "voucherflow.processing.pdftotext.shutil.which", lambda _bin: "/usr/bin/pdftotext"
        )
        monkeypatch.setattr(
            "voucherflow.processing.pdftotext.subprocess.run",
            lambda cmd, **kwargs: (_ for _ in ()).throw(RuntimeError("boom")),
        )
        assert extraer_con_pdftotext_layout(pdf) is None

    def test_devuelve_none_si_archivo_no_existe(self, tmp_path, monkeypatch):
        monkeypatch.setattr(
            "voucherflow.processing.pdftotext.shutil.which", lambda _bin: "/usr/bin/pdftotext"
        )
        assert extraer_con_pdftotext_layout(tmp_path / "no.pdf") is None

    def test_devuelve_none_si_salida_vacia(self, tmp_path, monkeypatch):
        pdf = tmp_path / "apto.pdf"
        pdf.write_bytes(b"%PDF-1.4")

        def _fake_run(cmd, **kwargs):  # noqa: ARG001
            Path(cmd[-1]).write_text("   \n  ", encoding="utf-8")  # solo espacios
            return type("R", (), {"returncode": 0})()

        monkeypatch.setattr(
            "voucherflow.processing.pdftotext.shutil.which", lambda _bin: "/usr/bin/pdftotext"
        )
        monkeypatch.setattr(
            "voucherflow.processing.pdftotext.subprocess.run", _fake_run
        )
        assert extraer_con_pdftotext_layout(pdf) is None

    def test_constante_binario(self):
        assert PDFTOTEXT_BIN == "pdftotext"


if __name__ == "__main__":  # pragma: no cover
    pytest.main([__file__, "-v"])

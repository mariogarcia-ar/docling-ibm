"""Fechas: la política del sistema (``voucherflow.fechas``).

⚠️ **Este módulo existe porque había dos políticas de fecha y divergían en 5 de
12 casos.** Es el mismo patrón que el bug de los montos: el **pipeline**
normaliza con `extraction.key_value.normalizar_fecha` (tolera ISO con hora,
compacto, fecha escrita; **rechaza** el año de dos dígitos) y el **evaluador**
comparaba con una lista de formatos propia que hacía lo contrario —no entendía el
ISO con hora y **sí** inventaba el siglo en `14/08/25`—.

Cada diferencia cambiaba un veredicto sin que nada fallara: la regla 4 (fecha de
emisión) quedaba en `no_verificable` cuando la fecha **sí** era comparable.

Lo que se fija acá es la **política**, no la implementación: ISO como forma
canónica, no inventar lo que falta, y comparar fechas (no textos).
"""

from __future__ import annotations

from datetime import date

import pytest

from voucherflow.fechas import (
    ahora_utc_iso,
    como_fecha,
    es_afip_valido,
    formato_afip,
    sin_hora,
)


class TestComoFecha:
    """El único punto de entrada para comparar fechas."""

    @pytest.mark.parametrize(
        "entrada",
        [
            "2025-08-14",
            "14/08/2025",
            "14-08-2025",
            "14.08.2025",
            "2025.08.14",
            "14082025",
            "14 de agosto de 2025",
            "2025-08-14T10:30:00",
            "2025-08-14 10:30:00",
        ],
    )
    def test_reconoce_las_formas_de_un_comprobante(self, entrada):
        assert como_fecha(entrada) == date(2025, 8, 14)

    def test_acepta_un_date(self):
        assert como_fecha(date(2025, 8, 14)) == date(2025, 8, 14)

    def test_una_fecha_inexistente_no_se_normaliza(self):
        """31 de febrero no existe: se declara ilegible, no se corrige."""
        assert como_fecha("31/02/2025") is None

    def test_no_inventa_el_siglo_con_el_anio_de_dos_digitos(self):
        """⚠️ Lo que hacía la copia del evaluador: `14/08/25` → 2025.

        Elegir el siglo es una decisión que nadie tomó; el pipeline lo rechaza a
        propósito y la copia lo aceptaba. Es la diferencia que cambiaba el
        veredicto.
        """
        assert como_fecha("14/08/25") is None

    @pytest.mark.parametrize("entrada", [None, "", "   ", "no es fecha", 42, []])
    def test_lo_ilegible_devuelve_none(self, entrada):
        assert como_fecha(entrada) is None

    def test_no_normaliza_un_mes_suelto(self):
        assert como_fecha("08/2025") is None

    def test_delega_en_la_regla_del_pipeline(self):
        """No tiene lista de formatos propia: si la tuviera, volvería a divergir."""
        from voucherflow.extraction.key_value import normalizar_fecha

        for entrada in ("14/08/2025", "2025-08-14T10:30:00", "14082025", "14/08/25"):
            esperado = normalizar_fecha(entrada)
            obtenido = como_fecha(entrada)
            assert (str(obtenido) if obtenido else None) == esperado


class TestComparacion:
    """La razón de existir: dos formas de la misma fecha son la misma fecha."""

    def test_dos_formatos_de_la_misma_fecha_coinciden(self):
        assert como_fecha("14/08/2025") == como_fecha("2025-08-14T10:30:00")

    def test_dos_fechas_distintas_no_coinciden(self):
        assert como_fecha("14/08/2025") != como_fecha("15/08/2025")

    def test_el_caso_que_antes_era_no_verificable(self):
        """⚠️ El bug: la carga con hora hacía que la regla 4 no pudiera comparar."""
        leida = como_fecha("14/08/2025")
        cargada = como_fecha("2025-08-14T10:30:00")
        assert leida is not None and cargada is not None
        assert leida == cargada

    def test_comparar_como_texto_daria_otro_resultado(self):
        """Control: si se comparara el string, estas dos parecerían distintas."""
        assert "14/08/2025" != "2025-08-14T10:30:00"


class TestSinHora:
    """Descartar la parte horaria al leer una fecha de afuera."""

    @pytest.mark.parametrize(
        "entrada,esperado",
        [
            ("2025-08-14T10:30:00", "2025-08-14"),
            ("2025-08-14 10:30:00", "2025-08-14"),
            ("2025-08-14", "2025-08-14"),
            ("", ""),
            (None, ""),
        ],
    )
    def test_descarta_la_hora(self, entrada, esperado):
        assert sin_hora(entrada) == esperado

    def test_no_toca_una_fecha_sin_hora(self):
        assert sin_hora("14/08/2025") == "14/08/2025"


class TestFormatoAfip:
    """El ``YYYYMMDD`` del WSCDC es de la frontera, no del dominio."""

    def test_convierte_iso(self):
        assert formato_afip("2025-08-14") == "20250814"

    def test_con_el_componente_horario_da_igual(self):
        """El padrón pide la fecha, no el instante."""
        assert formato_afip(sin_hora("2025-08-14T10:30:00")) == "20250814"

    def test_lo_que_no_es_iso_se_manda_tal_cual(self):
        """El WSCDC admite la fecha vacía y decide él: no se inventa una fecha."""
        assert formato_afip("") == ""
        assert formato_afip(None) == ""
        assert formato_afip("no es fecha") == "no es fecha"

    def test_es_afip_valido_reconoce_el_formato(self):
        assert es_afip_valido("20250814") is True

    @pytest.mark.parametrize("entrada", ["2025-08-14", "2025081", "", None, "2025"])
    def test_es_afip_valido_rechaza_lo_demas(self, entrada):
        assert es_afip_valido(entrada) is False

    def test_es_afip_valido_rechaza_una_fecha_inexistente(self):
        assert es_afip_valido("20250231") is False


class TestAhoraUtcIso:
    def test_devuelve_iso_con_zona(self):
        """UTC y con offset a propósito: un registro no depende de la máquina."""
        texto = ahora_utc_iso()
        assert texto.endswith("+00:00")
        assert "T" in texto

    def test_es_parseable(self):
        from datetime import datetime

        assert datetime.fromisoformat(ahora_utc_iso()).year >= 2026


class TestUnaSolaPolitica:
    """⚠️ La propiedad del refactor: los tres consumidores usan este módulo."""

    def test_el_evaluador_usa_el_modulo(self):
        """El consumidor que tenía la copia divergente."""
        from voucherflow.llm import evaluador

        assert evaluador.como_fecha is como_fecha

    def test_arca_usa_el_modulo(self):
        from voucherflow.models import arca

        assert arca.formato_afip is formato_afip

    def test_schemas_usa_el_modulo(self):
        from voucherflow.schemas import evidence

        assert evidence.ahora_utc_iso is ahora_utc_iso

    def test_ningun_modulo_redefine_un_helper_de_fecha(self):
        """Ni lista de formatos propia, ni timestamp propio."""
        from pathlib import Path

        raiz = Path(__file__).resolve().parents[1] / "src" / "voucherflow"
        marcas = {
            "def _a_fecha(": "usar `voucherflow.fechas.como_fecha`",
            "def _fecha_afip(": "usar `voucherflow.fechas.formato_afip`",
            "def _utc_now_iso(": "usar `voucherflow.fechas.ahora_utc_iso`",
        }
        culpables = []
        for archivo in raiz.rglob("*.py"):
            if archivo.name == "fechas.py":
                continue
            texto = archivo.read_text(encoding="utf-8")
            for marca, sugerencia in marcas.items():
                if marca in texto:
                    culpables.append(f"{archivo.name}: {marca} → {sugerencia}")
        assert not culpables, f"volvieron a definirse helpers de fecha: {culpables}"

    def test_no_quedan_listas_de_formato_con_anio_corto(self):
        """``%d/%m/%y`` inventa el siglo: no debe reaparecer en el código."""
        from pathlib import Path

        raiz = Path(__file__).resolve().parents[1] / "src" / "voucherflow"
        culpables = [
            archivo.name
            for archivo in raiz.rglob("*.py")
            if "%y" in archivo.read_text(encoding="utf-8")
        ]
        assert not culpables, (
            f"estos módulos usan un formato de año de 2 dígitos: {culpables} "
            "(inventa el siglo; `normalizar_fecha` lo rechaza a propósito)"
        )

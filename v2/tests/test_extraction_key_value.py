"""Tests de la normalización key-value de la extracción (F4/T-402).

**DoD de T-402** (F4.md §3, E-EXT-3): "Los campos clave (CUIT, fechas, montos,
ítems) quedan normalizados reutilizando las reglas de los prompts 10/11/kvi/kvg,
sin degradar la calidad".

Qué se verifica, en el orden del entregable:

1. **CUIT** (regla 2b de ``11``/``kvg``): solo dígitos y guiones propios, cortando
   ante caracteres extraños aunque el resultado quede incompleto; se arranca en el
   primer dígito para tolerar la etiqueta pegada por el OCR.
2. **Fechas** (``kvg``): ``YYYY-MM-DD`` solo si la fecha es completa y real; una
   fecha sin año, con año de dos dígitos o inexistente **no se normaliza** (no se
   completa con lo que el documento no trae).
3. **Montos** (``kvg``): número plano sin símbolos ni separadores de miles, con
   detección de signo; la ambigüedad de un separador único se resuelve con la
   convención argentina **y se deja constancia** (no en silencio).
4. **Comprobante**: el número impreso se conserva tal cual (regla 3 de ``11``) y
   ``PPPPP-NNNNNNNN`` se separa en ``punto_venta``/``numero_comprobante`` como
   campos **derivados** (regla de ``kvg``).
5. **Moneda** (regla 14 de ``11``) e **ítems** (``kvg``).
6. **No inventar** (regla dura de E-EXT-3): un dato ilegible conserva el valor
   crudo con aviso; un dato ausente sigue ausente.
7. **Integración con el flujo** (``ejecutar_flujo``/``extraer``): el
   ``SourceEvidence`` publica el valor normalizado, el crudo sigue disponible en
   ``meta['valor_crudo']`` y ``normalizar=False`` devuelve la lectura cruda.
8. **La combinación sigue siendo T-404** y el registro raw sigue viendo el crudo
   (T-402 no reemplaza a las reglas raw de T-303/T-403).

Reglas duras: la suite default corre **sin** Ollama real (doble del lector) y
**sin** Docling real (``VistaPreparada`` inyectada); no se agregan dependencias.
"""

from __future__ import annotations

import json
import tempfile
from pathlib import Path
from typing import Any

import pytest

from voucherflow.extraction import (
    ALIAS_FECHA,
    ALIAS_MONTO,
    CAMPO_FUENTE_LECTURA,
    CAMPOS_DERIVADOS_COMPROBANTE,
    CAMPOS_EXTRACCION,
    CLAVE_CAMPOS,
    NORM_COMPROBANTE,
    NORM_CUIT,
    NORM_FECHA,
    NORM_ITEMS,
    NORM_MONEDA,
    NORM_MONTO,
    NORM_TEXTO,
    NORM_VOCABULARIO,
    REGLA_POR_CAMPO,
    CAMPOS_GENERICOS_CON_REGLA,
    VERSION_NORMALIZACION,
    CampoLectura,
    ErrorNormalizacion,
    EvidenciaExtraccion,
    ItemExtraido,
    cuit_completo,
    ejecutar_flujo,
    extraer,
    monto_ambiguo,
    normalizar_campo,
    normalizar_cuit,
    normalizar_descripcion,
    normalizar_evidencia,
    normalizar_fecha,
    normalizar_moneda,
    normalizar_monto,
    normalizar_texto,
    normalizar_vocabulario,
    parsear_evidencia_extraccion,
    parsear_items,
    regla_de_campo,
    separar_comprobante,
    valores_normalizados,
)
from voucherflow.extraction.prompt_extraccion import (
    CAMPOS_EXTRACCION as CAMPOS_DEL_PROMPT,
)
from voucherflow.extraction.prompt_extraccion import (
    SYSTEM_PROMPT_POR_FUENTE,
)
from voucherflow.schemas.evidence import SourceEvidence
from voucherflow.settings.config import cargar_desde_dict
from voucherflow.validation.vistas import VistaPreparada

# ---------------------------------------------------------------------------
# Dobles y utilidades (mismo patrón que tests/test_extraction_flujos.py)
# ---------------------------------------------------------------------------

_PNG_1PX = (
    b"\x89PNG\r\n\x1a\n"
    b"\x00\x00\x00\rIHDR\x00\x00\x00\x01\x00\x00\x00\x01\x08\x02\x00\x00\x00"
    b"\x90wS\xde\x00\x00\x00\x0cIDATx\x9cc\xf8\xcf\xc0\x00\x00\x00\x03\x00\x01"
    b"\x86\xa0\xb5\x9d\x00\x00\x00\x00IEND\xaeB`\x82"
)


class FakeRespuesta:
    """Respuesta mínima de un lector (solo se usa ``contenido``)."""

    def __init__(self, contenido: str) -> None:
        self.contenido = contenido


class FakeLector:
    """Doble del ``OllamaClient``: responde por fuente y registra las llamadas."""

    def __init__(self, contenido: str = "{}", por_fuente: dict[str, str] | None = None) -> None:
        self.contenido = contenido
        self.por_fuente = por_fuente or {}
        self.llamadas: list[dict[str, Any]] = []

    def _fuente(self, messages: list[dict[str, Any]]) -> str:
        sistema = messages[0].get("content", "") if messages else ""
        for fuente, prompt in SYSTEM_PROMPT_POR_FUENTE.items():
            if prompt == sistema:
                return fuente
        return ""

    def ask(self, messages, model, json_format=False, options=None, num_ctx=None):
        fuente = self._fuente(messages)
        self.llamadas.append({"fuente": fuente, "messages": messages})
        return FakeRespuesta(self.por_fuente.get(fuente, self.contenido))


def _vista_fiel() -> VistaPreparada:
    """Vista fiel de F2 con una imagen válida (entrada del flujo VLM)."""
    tmp = tempfile.NamedTemporaryFile(suffix=".png", delete=False)
    tmp.write(_PNG_1PX)
    tmp.close()
    return VistaPreparada(
        tipo_vista="fiel",
        calidad="alta",
        representacion=tmp.name,
        resolucion_objetivo=2048,
        origen=tmp.name,
        ruta_imagen_original=tmp.name,
        nota="Vista fiel sintética (T-402)",
    )


def _campo(valor: Any, sustento: str) -> dict[str, Any]:
    """Campo con el shape del contrato (``valor`` + ``fragmento_sustento``)."""
    return {"valor": valor, "fragmento_sustento": sustento}


def _lectura(campos: dict[str, Any], fuente: str = "llm") -> EvidenciaExtraccion:
    """``EvidenciaExtraccion`` armada a mano (sin pasar por el intérprete)."""
    return EvidenciaExtraccion(
        fuente=fuente,
        campos={
            nombre: CampoLectura(
                campo=nombre,
                valor=valor,
                fragmento=f"fragmento de {nombre}",
                valor_crudo=valor,
            )
            for nombre, valor in campos.items()
        },
    )


def _json_extraccion(**campos: Any) -> str:
    """JSON de evidencia con los campos indicados (shape del contrato)."""
    return json.dumps(
        {CAMPO_FUENTE_LECTURA: "llm", CLAVE_CAMPOS: campos}, ensure_ascii=False
    )


def _settings_dobles() -> Any:
    """``Settings`` con los roles ``vlm``/``llm`` (sin Ollama real)."""
    return cargar_desde_dict(
        {
            "modelos": {
                "vlm": {"rol": "vlm", "modelo": "vlm-doble", "num_ctx": 4096},
                "llm": {"rol": "llm", "modelo": "llm-doble", "num_ctx": 8192},
            }
        }
    )


# ---------------------------------------------------------------------------
# 1. CUIT (regla 2b de v1: corte ante caracteres extraños)
# ---------------------------------------------------------------------------


class TestNormalizarCuit:
    """El CUIT queda solo con dígitos y los guiones propios del número."""

    def test_cuit_completo_se_conserva(self):
        assert normalizar_cuit("20-12345678-9") == "20-12345678-9"
        assert cuit_completo("20-12345678-9") is True

    def test_el_caso_de_v1_corta_ante_caracteres_extranos(self):
        # Regla 2b textual: "C.U.I.T. Nro.: 20-1 Ing, Brutas: 201641" -> "20-1".
        # Es el caso que en v1 el modelo arrastraba completo.
        assert normalizar_cuit("20-1 Ing, Brutas: 201641") == "20-1"
        assert normalizar_cuit("C.U.I.T. Nro.: 20-1") == "20-1"
        assert cuit_completo("20-1") is False

    def test_tolera_la_etiqueta_pegada_por_el_ocr(self):
        # Se arranca en el primer dígito: el OCR pega la etiqueta al número.
        assert normalizar_cuit("CUIT: 20301112223") == "20301112223"
        assert normalizar_cuit("Nro. CUIT 20-12345678-9 (emisor)") == "20-12345678-9"

    def test_sin_guiones_tambien_vale(self):
        # La regla pide "dígitos y los guiones propios": los guiones son formato.
        assert normalizar_cuit("20301112223") == "20301112223"
        assert cuit_completo("20301112223") is True

    def test_un_guion_colgante_no_se_arrastra(self):
        # Un guión solo vale si está **entre** dígitos: "20-" corta en "20".
        assert normalizar_cuit("20-") == "20"
        assert normalizar_cuit("-20-1") == "20-1"

    def test_sin_digitos_no_hay_cuit(self):
        # No inventar: sin dígitos no hay número que publicar.
        assert normalizar_cuit("CUIT ilegible") is None
        assert normalizar_cuit("") is None
        assert normalizar_cuit(None) is None
        assert normalizar_cuit("   ") is None

    def test_no_valida_el_digito_verificador(self):
        # La validación fiscal es del padrón (ARCA), no de la extracción: acá
        # solo se cuenta que estén los 11 dígitos.
        assert cuit_completo("99-99999999-9") is True

    def test_normalizar_campo_avisa_si_quedo_incompleto(self):
        resultado = normalizar_campo("cuit_emisor", "20-1 Ing, Brutas: 201641")
        assert resultado.valor == "20-1"
        assert resultado.regla == NORM_CUIT
        assert resultado.valor_crudo == "20-1 Ing, Brutas: 201641"
        assert len(resultado.avisos) == 1
        assert resultado.avisos[0].debilidad is True
        assert "11 del CUIT" in resultado.avisos[0].motivo

    def test_normalizar_campo_avisa_si_no_hay_digitos(self):
        resultado = normalizar_campo("cuit_emisor", "ilegible")
        assert resultado.valor == "ilegible"  # se conserva el crudo, no se inventa
        assert resultado.avisos[0].debilidad is True

    def test_cuit_completo_no_avisa(self):
        resultado = normalizar_campo("cuit_emisor", "20-12345678-9")
        assert resultado.valor == "20-12345678-9"
        assert resultado.avisos == ()


# ---------------------------------------------------------------------------
# 2. Fechas (YYYY-MM-DD; sin completar lo que falta)
# ---------------------------------------------------------------------------


class TestNormalizarFecha:
    """Las fechas completas y reales quedan en ISO; el resto no se toca."""

    @pytest.mark.parametrize(
        ("crudo", "esperado"),
        [
            ("2025-08-14", "2025-08-14"),
            ("14/08/2025", "2025-08-14"),
            ("14-08-2025", "2025-08-14"),
            ("2025.08.14", "2025-08-14"),
            ("14.08.2025", "2025-08-14"),
            ("14082025", "2025-08-14"),
            ("14 de agosto de 2025", "2025-08-14"),
            ("2025-08-14T10:30:00", "2025-08-14"),
            ("Fecha: 14/08/2025", "2025-08-14"),
        ],
    )
    def test_formas_reconocidas(self, crudo, esperado):
        assert normalizar_fecha(crudo) == esperado

    def test_el_anio_de_dos_digitos_no_se_normaliza(self):
        # Elegir el siglo (19xx/20xx) sería inventar: se conserva el crudo.
        assert normalizar_fecha("14/08/25") is None
        resultado = normalizar_campo("fecha_emision", "14/08/25")
        assert resultado.valor == "14/08/25"
        assert "dos dígitos" in resultado.avisos[0].motivo

    def test_una_fecha_incompleta_no_se_completa(self):
        # La regla de kvg es explícita: "Sin día/mes completo, omití".
        assert normalizar_fecha("2025-08") is None
        assert normalizar_fecha("08/2025") is None
        assert normalizar_fecha("2025") is None

    def test_una_fecha_inexistente_no_se_publica(self):
        # 31 de febrero no existe: el OCR leyó mal y no se arregla inventando.
        assert normalizar_fecha("31/02/2025") is None
        assert normalizar_fecha("32/01/2025") is None
        assert normalizar_fecha("14/13/2025") is None
        resultado = normalizar_campo("fecha_emision", "31/02/2025")
        assert resultado.valor == "31/02/2025"
        assert resultado.avisos[0].debilidad is True

    def test_el_29_de_febrero_se_valida_por_bisiesto(self):
        assert normalizar_fecha("29/02/2024") == "2024-02-29"
        assert normalizar_fecha("29/02/2025") is None

    def test_texto_sin_fecha_avisa(self):
        resultado = normalizar_campo("fecha_emision", "no se lee")
        assert resultado.valor == "no se lee"
        assert resultado.avisos[0].debilidad is True

    def test_una_fecha_ya_iso_no_genera_aviso(self):
        resultado = normalizar_campo("fecha_emision", "2025-08-14")
        assert resultado.valor == "2025-08-14"
        assert resultado.avisos == ()


# ---------------------------------------------------------------------------
# 3. Montos (número plano, sin separadores de miles ni símbolo)
# ---------------------------------------------------------------------------


class TestNormalizarMonto:
    """Los importes quedan numéricos, sin separadores de miles ni símbolo."""

    @pytest.mark.parametrize(
        ("crudo", "esperado"),
        [
            ("12.345,67", 12345.67),
            ("$ 12.345,67", 12345.67),
            ("12,345.67", 12345.67),  # convención inglesa
            ("1.234.567", 1234567),
            ("1234", 1234),
            ("12,50", 12.5),
            ("0", 0),
            ("0,00", 0.0),
            (12345.67, 12345.67),
            (0, 0),
        ],
    )
    def test_formas_reconocidas(self, crudo, esperado):
        assert normalizar_monto(crudo) == esperado

    def test_el_cero_es_un_valor_no_una_ausencia(self):
        # El ``0`` es un importe legítimo (una factura sin IVA); tratarlo como
        # ausencia perdería información (la ausencia la define T-401).
        assert normalizar_monto("0") == 0
        assert normalizar_monto(0.0) == 0.0

    def test_detecta_el_signo_de_un_ajuste_negativo(self):
        # Las tres formas en que un comprobante imprime un negativo.
        assert normalizar_monto("-1.234,56") == -1234.56
        assert normalizar_monto("1.234,56-") == -1234.56
        assert normalizar_monto("(1.234,56)") == -1234.56

    def test_un_separador_unico_con_tres_digitos_es_ambiguo(self):
        # "12.345" puede ser 12345 o 12,345: se aplica la convención argentina
        # ('.' = miles) y se deja constancia; no se elige en silencio.
        assert monto_ambiguo("12.345") is True
        assert normalizar_monto("12.345") == 12345
        resultado = normalizar_campo("importe_total_facturado", "12.345")
        assert "ambiguo" in resultado.avisos[0].motivo
        assert resultado.avisos[0].debilidad is True

    def test_con_dos_separadores_no_hay_ambiguedad(self):
        assert monto_ambiguo("1.234,56") is False
        assert monto_ambiguo("1,234.56") is False
        # "," con dos dígitos es decimal (no ambiguo).
        assert monto_ambiguo("12,50") is False

    def test_un_monto_ilegible_conserva_el_crudo_con_aviso(self):
        # "No inventar": no se convierte texto en un importe.
        assert normalizar_monto("monto ilegible") is None
        resultado = normalizar_campo("importe_total_facturado", "monto ilegible")
        assert resultado.valor == "monto ilegible"
        assert resultado.avisos[0].debilidad is True
        assert resultado.normalizado is False

    def test_no_interpreta_un_booleano_como_monto(self):
        assert normalizar_monto(True) is None

    def test_monto_ya_numerico_no_genera_aviso(self):
        resultado = normalizar_campo("subtotal", 12345.67)
        assert resultado.valor == 12345.67
        assert resultado.avisos == ()


# ---------------------------------------------------------------------------
# 4. Número de comprobante y campos derivados
# ---------------------------------------------------------------------------


class TestSepararComprobante:
    """``PPPPP-NNNNNNNN`` se separa sin alterar el número impreso."""

    def test_separa_punto_venta_y_numero(self):
        assert separar_comprobante("00005-00007344") == ("00005", "00007344")
        assert separar_comprobante("1-1") == ("1", "1")

    def test_conserva_los_ceros_a_la_izquierda(self):
        # El número se publica como está impreso: normalizar la longitud es una
        # decisión de negocio, no de lectura.
        assert separar_comprobante("00005-00007344")[0] == "00005"

    def test_sin_la_forma_esperada_no_se_inventa_una_separacion(self):
        assert separar_comprobante("0000500007344") == (None, None)
        assert separar_comprobante("sin guion") == (None, None)
        assert separar_comprobante(None) == (None, None)

    def test_el_numero_impreso_se_conserva_tal_cual(self):
        # Regla 3 de v1: "tal como figura impreso, incluyendo el guión".
        resultado = normalizar_campo("nro_comprobante", "00005-00007344")
        assert resultado.valor == "00005-00007344"
        assert resultado.derivados == {"punto_venta": "00005", "numero_comprobante": "00007344"}

    def test_sin_formato_avisa_sin_degradar_la_evidencia(self):
        # Un número sin guión puede ser legítimo: es informativo, no debilidad.
        resultado = normalizar_campo("nro_comprobante", "0000500007344")
        assert resultado.valor == "0000500007344"
        assert resultado.avisos[0].debilidad is False
        assert resultado.derivados == {}

    def test_los_derivados_son_del_programa_no_del_modelo(self):
        # No se le piden al modelo (no están en el contrato del prompt): los
        # calcula la normalización, así que no aparecen en CAMPOS_EXTRACCION.
        for derivado in CAMPOS_DERIVADOS_COMPROBANTE:
            assert derivado not in CAMPOS_EXTRACCION


# ---------------------------------------------------------------------------
# 5. Moneda, vocabulario, texto e ítems
# ---------------------------------------------------------------------------


class TestNormalizarVocabularioYTexto:
    """Moneda, vocabulario cerrado, texto libre e ítems del modo genérico."""

    @pytest.mark.parametrize(
        ("crudo", "esperado"),
        [
            ("ARS", "ARS"),
            ("ars", "ARS"),
            ("$", "ARS"),
            ("pesos", "ARS"),
            ("USD", "USD"),
            ("u$s", "USD"),
            ("U$D", "USD"),
            ("Dólares", "USD"),
        ],
    )
    def test_monedas_reconocidas(self, crudo, esperado):
        assert normalizar_moneda(crudo) == esperado

    def test_la_moneda_no_tiene_default(self):
        # Diferencia deliberada con v1 ("sin indicio explícito, usá ARS"):
        # asumir ARS sería inventar una moneda que el documento no declaró.
        assert normalizar_moneda("EUR") is None
        assert normalizar_moneda("") is None
        resultado = normalizar_campo("moneda", "EUR")
        assert resultado.valor == "EUR"
        assert resultado.avisos[0].debilidad is True

    def test_el_vocabulario_cerrado_pasa_a_mayusculas(self):
        assert normalizar_vocabulario("a") == "A"
        assert normalizar_vocabulario("ars") == "ARS"
        assert normalizar_campo("tipo_comprobante", "a").valor == "A"

    def test_el_texto_colapsa_espacios(self):
        assert normalizar_texto("  ACME   S.A.  ") == "ACME S.A."

    def test_la_descripcion_queda_en_minusculas(self):
        # Regla 8 de v1: "una frase breve... en minúsculas".
        assert normalizar_descripcion("  Café  CON Leche ") == "café con leche"
        assert normalizar_campo("descripcion", "COMPRA DE INSUMOS").valor == "compra de insumos"

    def test_la_razon_social_no_se_pasa_a_minusculas(self):
        # El nombre de la empresa se publica como se leyó (no es una frase).
        assert normalizar_campo("razon_social_emisor", "  ACME  S.A. ").valor == "ACME S.A."

    def test_los_items_se_estructuran_sin_inventar(self):
        items = parsear_items("café x2 - 1000 | pan - 250 | servicio")
        assert len(items) == 3
        assert items[0] == ItemExtraido("café", 2, 1000, "café x2 - 1000")
        assert items[1].cantidad is None and items[1].precio_unitario == 250
        assert items[2].cantidad is None and items[2].precio_unitario is None

    def test_los_items_aceptan_lista_y_objetos(self):
        # El modelo puede devolver una lista de strings o de objetos ya armados.
        desde_lista = parsear_items(["café x2 - 1000", "pan - 250"])
        assert [i.descripcion for i in desde_lista] == ["café", "pan"]
        desde_objetos = parsear_items([{"descripcion": "café", "cantidad": "2", "precio_unitario": "1.000"}])
        assert desde_objetos[0].cantidad == 2
        assert desde_objetos[0].precio_unitario == 1000

    def test_un_item_sin_estructura_se_conserva_como_descripcion(self):
        resultado = normalizar_campo("productos", "servicio de limpieza")
        assert resultado.items[0].descripcion == "servicio de limpieza"
        assert resultado.avisos[0].debilidad is False  # informativo

    def test_sin_items_no_se_inventa_nada(self):
        assert parsear_items(None) == ()
        assert parsear_items("") == ()


# ---------------------------------------------------------------------------
# 6. Selección de regla (campos del contrato y alias del modo genérico)
# ---------------------------------------------------------------------------


class TestReglaDeCampo:
    """Cada campo del contrato tiene regla; el modo genérico usa alias."""

    def test_todos_los_campos_del_contrato_tienen_regla(self):
        # Un campo del contrato sin regla se publicaría crudo en silencio.
        sin_regla = [c for c in CAMPOS_EXTRACCION if regla_de_campo(c) is None]
        assert sin_regla == [], f"campos del contrato sin regla de normalización: {sin_regla}"

    def test_el_mapa_de_reglas_cubre_el_contrato_del_prompt(self):
        # El contrato del prompt y el mapa de reglas no pueden divergir: si el
        # prompt pide un campo nuevo, la normalización lo tiene que conocer.
        assert CAMPOS_EXTRACCION == CAMPOS_DEL_PROMPT
        faltan = [c for c in CAMPOS_EXTRACCION if c not in REGLA_POR_CAMPO]
        assert faltan == [], f"campos del contrato sin entrada en REGLA_POR_CAMPO: {faltan}"
        # Las únicas entradas fuera del contrato son los campos del modo genérico
        # (kvg) que sí tienen semántica conocida, declarados explícitamente.
        sobran = [c for c in REGLA_POR_CAMPO if c not in CAMPOS_EXTRACCION]
        assert sorted(sobran) == sorted(CAMPOS_GENERICOS_CON_REGLA), (
            "REGLAS_POR_CAMPO declara campos fuera del contrato y fuera del modo "
            f"genérico conocido: {sobran}"
        )

    def test_los_campos_del_contrato_mapean_a_las_reglas_esperadas(self):
        assert regla_de_campo("cuit_emisor") == NORM_CUIT
        assert regla_de_campo("cuit_receptor") == NORM_CUIT
        assert regla_de_campo("fecha_emision") == NORM_FECHA
        assert regla_de_campo("importe_total_facturado") == NORM_MONTO
        assert regla_de_campo("nro_comprobante") == NORM_COMPROBANTE
        assert regla_de_campo("tipo_comprobante") == NORM_VOCABULARIO
        assert regla_de_campo("moneda") == NORM_MONEDA
        assert regla_de_campo("descripcion") == NORM_TEXTO
        assert regla_de_campo("productos") == NORM_ITEMS

    def test_los_alias_de_monto_y_fecha_del_modo_generico(self):
        # kvg define "monto" como el total del documento y "fecha" como su fecha:
        # misma semántica, misma regla.
        assert regla_de_campo("monto") == NORM_MONTO
        assert regla_de_campo("monto_total") == NORM_MONTO
        assert regla_de_campo("total_facturado") == NORM_MONTO
        assert regla_de_campo("fecha") == NORM_FECHA
        assert "monto" in ALIAS_MONTO and "fecha" in ALIAS_FECHA

    def test_un_campo_desconocido_no_tiene_regla(self):
        # La normalización no adivina tipos: un campo genérico se publica tal
        # como se leyó (el modo kvg no tiene estructura conocida).
        assert regla_de_campo("alicuota_21") is None
        resultado = normalizar_campo("alicuota_21", "21%")
        assert resultado.valor == "21%"
        assert resultado.regla is None
        assert resultado.normalizado is False

    def test_la_version_de_las_reglas_esta_congelada(self):
        # ADR-005: la versión viaja en la evidencia para poder reproducir un caso.
        assert VERSION_NORMALIZACION == "extraccion-key-value-norm@1"


# ---------------------------------------------------------------------------
# 7. Normalización de una lectura completa (informe y trazabilidad)
# ---------------------------------------------------------------------------


class TestNormalizarEvidencia:
    """``normalizar_evidencia`` normaliza todos los campos y deja el informe."""

    def test_normaliza_los_campos_conocidos(self):
        lectura = _lectura(
            {
                "cuit_emisor": "20-1 Ing, Brutas: 201641",
                "fecha_emision": "14/08/2025",
                "importe_total_facturado": "$ 12.345,67",
                "moneda": "ars",
            }
        )
        resultado = normalizar_evidencia(lectura)
        campos = resultado.evidencia.campos
        assert campos["cuit_emisor"].valor == "20-1"
        assert campos["fecha_emision"].valor == "2025-08-14"
        assert campos["importe_total_facturado"].valor == 12345.67
        assert campos["moneda"].valor == "ARS"

    def test_el_valor_crudo_siempre_sobrevive(self):
        # Es la garantía de auditable: normalizar no borra lo que el modelo leyó.
        lectura = _lectura({"fecha_emision": "14/08/2025", "importe_total_facturado": "$ 12.345,67"})
        resultado = normalizar_evidencia(lectura)
        assert resultado.evidencia.campos["fecha_emision"].valor_crudo == "14/08/2025"
        assert (
            resultado.evidencia.campos["importe_total_facturado"].valor_crudo
            == "$ 12.345,67"
        )

    def test_la_evidencia_original_no_se_muta(self):
        lectura = _lectura({"fecha_emision": "14/08/2025"})
        normalizar_evidencia(lectura)
        assert lectura.campos["fecha_emision"].valor == "14/08/2025"

    def test_el_informe_registra_reglas_normalizados_y_no_normalizados(self):
        lectura = _lectura(
            {
                "fecha_emision": "14/08/2025",
                "cuit_emisor": "20-1 Ing, Brutas: 201641",
                "alicuota_21": "21%",
            }
        )
        informe = normalizar_evidencia(lectura).informe
        assert informe.version == VERSION_NORMALIZACION
        assert set(informe.reglas_aplicadas) == {NORM_CUIT, NORM_FECHA}
        # El CUIT se **cortó** (cambió su representación) pero quedó incompleto:
        # cuenta como normalizado *y* emite aviso — no se esconde la reserva.
        assert informe.normalizados == {
            "fecha_emision": "2025-08-14",
            "cuit_emisor": "20-1",
        }
        assert informe.no_normalizados == {}
        assert informe.debilidades and "NORM_CUIT" in informe.debilidades[0]
        # El campo sin regla no ensucia el informe.
        assert "alicuota_21" not in informe.normalizados
        assert "alicuota_21" not in informe.no_normalizados

    def test_los_avisos_informativos_no_son_debilidades(self):
        lectura = _lectura({"nro_comprobante": "0000500007344"})
        informe = normalizar_evidencia(lectura).informe
        assert informe.debilidades == []
        assert informe.informativos and "NORM_COMPROBANTE" in informe.informativos[0]

    def test_los_campos_derivados_se_agregan_a_la_evidencia(self):
        lectura = _lectura({"nro_comprobante": "00005-00007344"})
        evidencia = normalizar_evidencia(lectura).evidencia
        assert evidencia.campos["punto_venta"].valor == "00005"
        assert evidencia.campos["numero_comprobante"].valor == "00007344"
        # Heredan el sostén del número impreso y declaran de dónde salen: no son
        # lecturas independientes de la fuente.
        assert evidencia.campos["punto_venta"].fragmento == lectura.campos["nro_comprobante"].fragmento
        assert evidencia.campos["punto_venta"].derivado_de == "nro_comprobante"
        assert evidencia.campos["punto_venta"].es_derivado is True

    def test_las_debilidades_llegan_a_los_problemas_de_la_lectura(self):
        # Así terminan en ``SourceEvidence.debilidades`` sin plomería extra.
        lectura = _lectura({"importe_total_facturado": "monto ilegible"})
        evidencia = normalizar_evidencia(lectura).evidencia
        assert any("NORM_MONTO" in p for p in evidencia.problemas)

    def test_los_problemas_de_la_lectura_se_conservan(self):
        lectura = _lectura({"fecha_emision": "14/08/2025"})
        lectura = EvidenciaExtraccion(
            fuente=lectura.fuente,
            campos=lectura.campos,
            problemas=["problema previo del intérprete"],
        )
        evidencia = normalizar_evidencia(lectura).evidencia
        assert "problema previo del intérprete" in evidencia.problemas

    def test_no_muta_la_lista_de_problemas_original(self):
        lectura = _lectura({"importe_total_facturado": "monto ilegible"})
        normalizar_evidencia(lectura)
        assert lectura.problemas == []

    def test_los_campos_sin_valor_normalizable_no_avisan_dos_veces(self):
        # Un campo ilegible genera **un** aviso, no uno por cada regla.
        lectura = _lectura({"importe_total_facturado": "ilegible"})
        informe = normalizar_evidencia(lectura).informe
        assert len([a for a in informe.avisos if a.campo == "importe_total_facturado"]) == 1

    def test_entrada_invalida_lanza_error_de_contrato(self):
        with pytest.raises(ErrorNormalizacion) as exc:
            normalizar_evidencia({"no": "es evidencia"})  # type: ignore[arg-type]
        assert "EvidenciaExtraccion" in str(exc.value)

    def test_valores_normalizados_es_un_atajo_para_t404_t405(self):
        lectura = _lectura({"fecha_emision": "14/08/2025", "moneda": "ars"})
        assert valores_normalizados(lectura) == {
            "fecha_emision": "2025-08-14",
            "moneda": "ARS",
        }

    def test_las_ausencias_siguen_ausentes(self):
        # Regla dura de E-EXT-3: un dato ausente no se inventa al normalizar.
        lectura = _lectura({"fecha_emision": "14/08/2025"})
        lectura = EvidenciaExtraccion(
            fuente="llm", campos=lectura.campos, campos_ausentes=["cuit_receptor"]
        )
        evidencia = normalizar_evidencia(lectura).evidencia
        assert "cuit_receptor" not in evidencia.campos
        assert evidencia.campos_ausentes == ["cuit_receptor"]


# ---------------------------------------------------------------------------
# 8. Integración con el flujo y el contrato de F0
# ---------------------------------------------------------------------------


class TestIntegracionConElFlujo:
    """El ``SourceEvidence`` publica el valor normalizado y conserva el crudo."""

    def test_ejecutar_flujo_publica_valores_normalizados(self):
        lector = FakeLector(
            _json_extraccion(
                cuit_emisor=_campo("20-1 Ing, Brutas: 201641", "C.U.I.T. 20-1"),
                fecha_emision=_campo("14/08/2025", "Fecha: 14/08/2025"),
                importe_total_facturado=_campo("$ 12.345,67", "Importe Total: $ 12.345,67"),
            )
        )
        resultado = ejecutar_flujo(
            "llm", lector, markdown="FACTURA", settings=_settings_dobles()
        )
        campos = resultado.source_evidence.campos
        assert campos["cuit_emisor"].valor == "20-1"
        assert campos["fecha_emision"].valor == "2025-08-14"
        assert campos["importe_total_facturado"].valor == 12345.67

    def test_el_crudo_queda_en_la_meta_del_contrato(self):
        # El consumidor aguas abajo (T-404/T-405, auditoría) no necesita volver
        # al JSON del modelo para reconciliar: el crudo viaja en la evidencia.
        lector = FakeLector(
            _json_extraccion(fecha_emision=_campo("14/08/2025", "Fecha: 14/08/2025"))
        )
        resultado = ejecutar_flujo(
            "llm", lector, markdown="FACTURA", settings=_settings_dobles()
        )
        meta = resultado.source_evidence.campos["fecha_emision"].meta
        assert meta["valor_crudo"] == "14/08/2025"
        assert meta["version_prompt"].startswith("extraccion-key-value@")

    def test_normalizar_false_devuelve_la_lectura_cruda_de_t401(self):
        lector = FakeLector(
            _json_extraccion(fecha_emision=_campo("14/08/2025", "Fecha: 14/08/2025"))
        )
        resultado = ejecutar_flujo(
            "llm",
            lector,
            markdown="FACTURA",
            settings=_settings_dobles(),
            normalizar=False,
        )
        assert resultado.source_evidence.campos["fecha_emision"].valor == "14/08/2025"
        assert resultado.informe_normalizacion is None

    def test_extraer_normaliza_las_dos_fuentes_en_paralelo(self):
        lector = FakeLector(
            por_fuente={
                "vlm": _json_extraccion(moneda=_campo("u$s", "Moneda: U$S")),
                "llm": _json_extraccion(moneda=_campo("u$s", "Moneda: U$S")),
            }
        )
        resultado = extraer(
            lector,
            markdown="FACTURA",
            vista=_vista_fiel(),
            settings=_settings_dobles(),
        )
        for fuente in ("vlm", "llm"):
            assert resultado.por_fuente(fuente).campos["moneda"].valor == "USD"
            assert resultado.por_fuente(fuente).campos["moneda"].meta["valor_crudo"] == "u$s"

    def test_el_detalle_de_la_corrida_reporta_la_normalizacion(self):
        lector = FakeLector(
            _json_extraccion(fecha_emision=_campo("14/08/2025", "Fecha: 14/08/2025"))
        )
        resultado = extraer(lector, markdown="FACTURA", settings=_settings_dobles())
        detalle = resultado.detalle
        assert detalle["normalizado"] is True
        assert detalle["version_normalizacion"] == VERSION_NORMALIZACION
        info = detalle["modelos"]["llm"]["normalizacion"]
        assert info["version"] == VERSION_NORMALIZACION
        assert NORM_FECHA in info["reglas_aplicadas"]
        assert "fecha_emision" in info["campos_normalizados"]

    def test_el_numero_de_comprobante_deriva_los_campos_en_la_corrida(self):
        lector = FakeLector(
            _json_extraccion(
                nro_comprobante=_campo("00005-00007344", "Nro: 00005-00007344")
            )
        )
        resultado = ejecutar_flujo(
            "llm", lector, markdown="FACTURA", settings=_settings_dobles()
        )
        campos = resultado.source_evidence.campos
        assert campos["punto_venta"].valor == "00005"
        assert campos["numero_comprobante"].valor == "00007344"
        assert campos["punto_venta"].meta["derivado_de"] == "nro_comprobante"

    def test_la_evidencia_normalizada_es_serializable(self):
        lector = FakeLector(
            _json_extraccion(
                cuit_emisor=_campo("20-12345678-9", "CUIT 20-12345678-9"),
                nro_comprobante=_campo("00005-00007344", "Nro: 00005-00007344"),
            )
        )
        resultado = ejecutar_flujo(
            "llm", lector, markdown="FACTURA", settings=_settings_dobles()
        )
        crudo = resultado.source_evidence.model_dump_json()
        assert SourceEvidence.model_validate_json(crudo) == resultado.source_evidence


# ---------------------------------------------------------------------------
# 9. Lo que T-402 NO hace (para no adelantar tareas)
# ---------------------------------------------------------------------------


class TestAlcance:
    """Fronteras de T-402: no combina, no reemplaza la pasada raw, no mide v1."""

    def test_la_pasada_raw_sigue_viendo_el_valor_crudo(self):
        # Es lo que le permite a T-303 reportar *qué se leyó* cuando el valor no
        # sirve; si la normalización se adelantara, el reporte perdería el dato.
        lector = FakeLector(
            _json_extraccion(tipo_comprobante=_campo("X", "Recuadro 'X'"))
        )
        resultado = ejecutar_flujo(
            "llm", lector, markdown="FACTURA", settings=_settings_dobles()
        )
        assert resultado.source_evidence.campos["tipo_comprobante"].valor == "X"
        assert resultado.source_evidence.campos["tipo_comprobante"].meta["valor_crudo"] == "X"
        assert resultado.source_evidence.valida is False
        assert "RAW_VOCABULARIO" in resultado.source_evidence.reglas_aplicadas

    def test_un_cuit_truncado_no_se_convierte_en_debilidad_raw(self):
        # El corte del CUIT es una regla de **normalización** (T-402), no de
        # lectura: la debilidad la reporta NORM_CUIT, no RAW_SUSTENTO.
        lector = FakeLector(
            _json_extraccion(
                cuit_emisor=_campo("20-1 Ing, Brutas: 201641", "C.U.I.T. 20-1 Ing, Brutas: 201641")
            )
        )
        resultado = ejecutar_flujo(
            "llm", lector, markdown="FACTURA", settings=_settings_dobles()
        )
        assert resultado.source_evidence.campos["cuit_emisor"].valor == "20-1"
        debilidades = " ".join(resultado.source_evidence.debilidades)
        assert "NORM_CUIT" in debilidades
        assert "RAW_SUSTENTO" not in resultado.source_evidence.reglas_aplicadas

    def test_combinar_evidencia_sigue_siendo_esqueleto_de_t404(self):
        from voucherflow.extraction import combinar_evidencia

        with pytest.raises(NotImplementedError) as exc:
            combinar_evidencia("doc-1", [])
        assert "T-404" in str(exc.value)

    def test_la_evidencia_cruda_se_puede_recuperar(self):
        # La normalización no destruye la lectura de T-401: con
        # ``normalizar=False`` se obtiene la evidencia cruda completa.
        lector = FakeLector(
            _json_extraccion(cuit_emisor=_campo("20-1 Ing, Brutas: 201641", "CUIT"))
        )
        cruda = ejecutar_flujo(
            "llm", lector, markdown="FACTURA", settings=_settings_dobles(), normalizar=False
        )
        assert cruda.evidencia.campos["cuit_emisor"].valor == "20-1 Ing, Brutas: 201641"
        assert cruda.evidencia.campos["cuit_emisor"].valor_crudo == "20-1 Ing, Brutas: 201641"

    def test_el_intérprete_de_t401_no_normaliza(self):
        # La frontera T-401/T-402: interpretar la respuesta NO normaliza valores.
        ev = parsear_evidencia_extraccion(
            _json_extraccion(fecha_emision=_campo("14/08/2025", "Fecha: 14/08/2025")),
            fuente="llm",
        )
        assert ev.campos["fecha_emision"].valor == "14/08/2025"
        assert ev.campos["fecha_emision"].normalizado is False

    def test_los_dobles_de_la_suite_no_tocan_la_red(self):
        # Regla dura de la suite default: sin Ollama real.
        lector = FakeLector(_json_extraccion(fecha_emision=_campo("14/08/2025", "Fecha")))
        ejecutar_flujo("llm", lector, markdown="FACTURA", settings=_settings_dobles())
        assert lector.llamadas and lector.llamadas[0]["fuente"] == "llm"

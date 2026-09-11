"""Tests de las reglas raw **por fuente de extracción** (F4/T-403, E-EXT-2).

**DoD de T-403** (F4.md §3): "Las reglas raw por fuente (pasada 1) extraen
candidatos y quedan registradas con trazabilidad".

T-303 (F3) dejó el registro raw genérico y T-401 lo reutilizó para que
``SourceEvidence`` cumpliera el contrato de F0. **T-403** completa la pasada 1
para la extracción, sobre los huecos que T-401 declaró explícitamente:

1. **Sostén de los campos de formato estructurado** (montos y fechas, que T-401
   dejaba sin evaluar para no producir debilidades espurias): se comparan
   **formas canónicas** (``12345.67`` ← ``"$ 12.345,67"``, ``2025-08-14`` ←
   ``"14/08/2025"``), así que la comparación es significativa y sigue sin haber
   falsos positivos.
2. **Sostén de los identificadores** (CUIT): el OCR los imprime con separadores y
   el modelo los puede reportar sin ellos (o al revés); la comparación es por
   **secuencias de dígitos**.
3. **Coherencia de la fuente consigo misma** (el corazón de E-EXT-2): la fuente
   que dice ``A`` sin haber leído los dos CUIT que esa letra exige, o que declara
   ``B`` con IVA discriminado, queda **debilitada antes de combinarse**.
4. **Trazabilidad**: qué campos se evaluaron, con qué criterio y por qué; y el
   ``RAW_COHERENCIA`` en ``reglas_aplicadas``.
5. **Las fronteras** (lo que T-403 **no** hace): la combinación por campo sigue
   siendo T-404; la descripción sigue sin evaluarse; una implicación que la fuente
   no puede cumplir por no haber leído el dato se reporta como limitación (no se
   inventa el dato ni se castiga la ausencia); y la pasada raw **no cambia** los
   valores publicados.

Reglas duras: suite default **sin** Ollama real (doble del lector) ni Docling real
(``VistaPreparada`` inyectada); los contratos congelados de F0/F3 no se tocan.
"""

from __future__ import annotations

import json
import tempfile
from typing import Any

import pytest

from voucherflow.extraction import (
    CAMPO_FUENTE_LECTURA,
    CAMPOS_EXTRACCION,
    CAMPOS_SOSTEN_ESTRUCTURADO,
    CAMPOS_SOSTEN_NO_EVALUADO,
    CLAVE_CAMPOS,
    COHERENCIA_POR_CAMPO,
    CampoLectura,
    EvidenciaExtraccion,
    campo_declarado_de_campo,
    construir_source_evidence,
    ejecutar_flujo,
    extraer_evidencia,
    parsear_evidencia_extraccion,
    veredicto_raw_de_evidencia,
)
from voucherflow.rules.raw import (
    ID_RAW_COHERENCIA,
    ImplicacionCoherencia,
    evaluar_raw,
    violaciones_de_coherencia,
)
from voucherflow.settings.config import cargar_desde_dict
from voucherflow.validation.vistas import VistaPreparada

# ---------------------------------------------------------------------------
# Utilidades (mismo patrón que las suites de T-401/T-402)
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
    """Doble del ``OllamaClient``: devuelve el JSON configurado, sin red."""

    def __init__(self, contenido: str = "{}") -> None:
        self.contenido = contenido
        self.llamadas: list[dict[str, Any]] = []

    def ask(self, messages, model, json_format=False, options=None, num_ctx=None):
        self.llamadas.append({"model": model, "num_ctx": num_ctx})
        return FakeRespuesta(self.contenido)


def _vista_fiel() -> VistaPreparada:
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
        nota="Vista fiel sintética (T-403)",
    )


def _campo(valor: Any, sustento: str) -> dict[str, Any]:
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


def _evidencia(campos: dict[str, Any], fuente: str = "llm") -> EvidenciaExtraccion:
    """Lectura desde el JSON del contrato (sin normalizar: es la de T-401)."""
    crudo = json.dumps({CAMPO_FUENTE_LECTURA: fuente, CLAVE_CAMPO: campos}, ensure_ascii=False)
    return parsear_evidencia_extraccion(crudo, fuente=fuente)


#: Alias para no repetir la constante del contrato en cada helper.
CLAVE_CAMPO = CLAVE_CAMPOS


def _settings_dobles() -> Any:
    return cargar_desde_dict(
        {
            "modelos": {
                "vlm": {"rol": "vlm", "modelo": "vlm-doble", "num_ctx": 4096},
                "llm": {"rol": "llm", "modelo": "llm-doble", "num_ctx": 8192},
            }
        }
    )


def _factura_a_completa(**extra: Any) -> dict[str, Any]:
    """Campos de una Factura A coherente (los dos CUIT + IVA discriminado)."""
    campos = {
        "tipo_comprobante": _campo("A", "Recuadro 'A' COD. 01"),
        "razon_social_emisor": _campo("ACME S.A.", "ACME S.A."),
        "cuit_emisor": _campo("30-12345678-9", "C.U.I.T. 30-12345678-9"),
        "cuit_receptor": _campo("27-30111222-4", "C.U.I.T. 27-30111222-4"),
        "fecha_emision": _campo("14/08/2025", "Fecha de Emisión: 14/08/2025"),
        "importe_total_facturado": _campo("$ 12.345,67", "Importe Total: $ 12.345,67"),
        "subtotal": _campo("10.203,03", "Subtotal: $ 10.203,03"),
        "iva": _campo("2.142,64", "IVA 21%: $ 2.142,64"),
    }
    campos.update(extra)
    return campos


# ---------------------------------------------------------------------------
# 1. Sostén de los campos estructurados (montos y fechas)
# ---------------------------------------------------------------------------


class TestSostenEstructurado:
    """Montos y fechas se evalúan contra su forma canónica (T-403)."""

    def test_el_monto_con_separadores_queda_sostenido(self):
        # El caso que T-401 dejaba sin evaluar: el valor normalizado (12345.67)
        # contra el fragmento impreso ("$ 12.345,67") — son el mismo importe.
        ev = _evidencia(_factura_a_completa())
        veredicto = veredicto_raw_de_evidencia(ev)
        assert "RAW_SUSTENTO" not in veredicto.reglas_aplicadas
        assert not any("importe_total_facturado" in d for d in veredicto.debilidades)

    def test_el_monto_que_el_fragmento_no_contiene_es_debilidad(self):
        # Ahora que el sostén **sí** se evalúa, un monto que no está en el
        # fragmento deja de pasar en silencio.
        ev = _evidencia(
            _factura_a_completa(
                importe_total_facturado=_campo("$ 99.999,99", "Importe Total: $ 12.345,67")
            )
        )
        veredicto = veredicto_raw_de_evidencia(ev)
        assert "RAW_SUSTENTO" in veredicto.reglas_aplicadas
        assert any("importe_total_facturado" in d for d in veredicto.debilidades)

    def test_la_fecha_impresa_sostiene_la_fecha_iso(self):
        ev = _evidencia(_factura_a_completa())
        veredicto = veredicto_raw_de_evidencia(ev)
        assert not any("fecha_emision" in d for d in veredicto.debilidades)

    def test_una_fecha_distinta_a_la_del_fragmento_es_debilidad(self):
        ev = _evidencia(
            _factura_a_completa(fecha_emision=_campo("01/01/2020", "Fecha: 14/08/2025"))
        )
        veredicto = veredicto_raw_de_evidencia(ev)
        assert any("fecha_emision" in d for d in veredicto.debilidades)

    def test_un_fragmento_con_varias_fechas_sostiene_cualquiera_de_ellas(self):
        # Exigir la primera produciría una debilidad espuria: un período tiene dos
        # fechas y ambas son legítimas.
        ev = _evidencia(
            _factura_a_completa(
                fecha_emision=_campo(
                    "31/08/2025", "Período: 01/08/2025 al 31/08/2025"
                )
            )
        )
        veredicto = veredicto_raw_de_evidencia(ev)
        assert not any("fecha_emision" in d for d in veredicto.debilidades)

    def test_sin_fragmento_gana_la_regla_de_campo(self):
        # El campo sin sustento lo reporta RAW_CAMPO (T-303), no RAW_SUSTENTO:
        # una sola autoridad por debilidad (criterio de T-401 §2.9).
        ev = _evidencia(_factura_a_completa(iva={"valor": "2.142,64"}))
        veredicto = veredicto_raw_de_evidencia(ev)
        assert "RAW_CAMPO" in veredicto.reglas_aplicadas
        assert "RAW_SUSTENTO" not in veredicto.reglas_aplicadas

    def test_los_montos_con_signo_o_parentesis_tambien_se_comparan(self):
        for declarado, fragmento in (
            ("-1.234,56", "Ajuste: (1.234,56)"),
            ("1.234,56", "Otros: 1.234,56"),
        ):
            ev = _evidencia(
                _factura_a_completa(otros_impuestos=_campo(declarado, fragmento))
            )
            veredicto = veredicto_raw_de_evidencia(ev)
            assert not any("otros_impuestos" in d for d in veredicto.debilidades), (
                declarado,
                fragmento,
            )

    def test_un_valor_ilegible_no_puede_sostenerse_y_lo_dice(self):
        # Un monto que no se puede normalizar (texto libre) tampoco se puede
        # sostener: el sostén no se inventa a favor del dato.
        ev = _evidencia(
            _factura_a_completa(iva=_campo("ilegible", "IVA 21%: 2.142,64"))
        )
        veredicto = veredicto_raw_de_evidencia(ev)
        assert any("iva" in d for d in veredicto.debilidades)

    def test_el_sostenedor_del_campo_es_explicito(self):
        # La traza dice con qué criterio se evaluó cada campo.
        declarado = campo_declarado_de_campo(
            CampoLectura(campo="importe_total_facturado", valor="12345.67", fragmento="Total: 12.345,67")
        )
        assert declarado is not None
        assert declarado.sostenedor is not None
        fecha = campo_declarado_de_campo(
            CampoLectura(campo="fecha_emision", valor="2025-08-14", fragmento="Fecha: 14/08/2025")
        )
        assert fecha is not None and fecha.sostenedor is not None

    def test_la_descripcion_sigue_sin_evaluarse(self):
        # Es una frase sintética, no un dato que se copie del documento.
        assert campo_declarado_de_campo(
            CampoLectura(campo="descripcion", valor="compra de insumos", fragmento="Ítems varios")
        ) is None
        assert CAMPOS_SOSTEN_NO_EVALUADO == frozenset({"descripcion"})


# ---------------------------------------------------------------------------
# 2. Sostén de los identificadores (CUIT) y forma canónica
# ---------------------------------------------------------------------------


class TestSostenIdentificadores:
    """El CUIT se sostiene aunque los separadores no coincidan."""

    def test_el_cuit_con_guiones_y_sin_guiones_se_sostienen_entre_si(self):
        for declarado, fragmento in (
            ("30-12345678-9", "C.U.I.T. 30123456789"),
            ("30123456789", "C.U.I.T. 30-12345678-9"),
            ("30-12345678-9", "CUIT: 30/12345678/9"),
        ):
            ev = _evidencia(
                _factura_a_completa(cuit_emisor=_campo(declarado, fragmento))
            )
            veredicto = veredicto_raw_de_evidencia(ev)
            assert not any("cuit_emisor" in d for d in veredicto.debilidades), (
                declarado,
                fragmento,
            )

    def test_un_cuit_distinto_al_del_fragmento_es_debilidad(self):
        # El fragmento cita OTRO CUIT: la coincidencia por cola de número no debe
        # dar falso positivo (por eso la comparación es por ventana de secuencias).
        ev = _evidencia(
            _factura_a_completa(
                cuit_emisor=_campo("30-12345678-9", "C.U.I.T. 20-12345678-9")
            )
        )
        veredicto = veredicto_raw_de_evidencia(ev)
        assert any("cuit_emisor" in d for d in veredicto.debilidades)

    def test_la_forma_canonica_no_cambia_el_valor_publicado(self):
        # El sostén compara la forma canónica, pero lo que se publica sigue siendo
        # el valor normalizado de T-402 (con guiones) y el crudo en la meta.
        ev = _evidencia(_factura_a_completa())
        source = construir_source_evidence(ev)
        campo = source.campos["cuit_emisor"]
        assert campo.valor == "30-12345678-9"
        assert campo.meta["valor_crudo"] == "30-12345678-9"

    def test_el_normalizador_canonico_solo_aplica_a_los_cuit(self):
        cuit = campo_declarado_de_campo(
            CampoLectura(campo="cuit_emisor", valor="30-12345678-9", fragmento="CUIT 30123456789")
        )
        assert cuit is not None and cuit.normalizador_valor is not None
        razon = campo_declarado_de_campo(
            CampoLectura(campo="razon_social_emisor", valor="ACME S.A.", fragmento="ACME S.A.")
        )
        assert razon is not None and razon.normalizador_valor is None


# ---------------------------------------------------------------------------
# 3. Coherencia de la fuente consigo misma (el corazón de E-EXT-2)
# ---------------------------------------------------------------------------


class TestCoherenciaFuente:
    """Una fuente internamente inconsistente queda debilitada antes de combinarse."""

    def test_una_factura_a_sin_los_dos_cuit_queda_debilitada(self):
        # El caso textual de E-EXT-2: "la evidencia VLM dice Factura A sin
        # detectar dos CUIT → la fuente VLM queda marcada como debilitada antes
        # de combinarse".
        ev = _evidencia(
            {
                "tipo_comprobante": _campo("A", "Recuadro 'A' COD. 01"),
                "cuit_emisor": _campo("30-12345678-9", "C.U.I.T. 30-12345678-9"),
            }
        )
        veredicto = veredicto_raw_de_evidencia(ev)
        assert ID_RAW_COHERENCIA in veredicto.reglas_aplicadas
        assert veredicto.incoherencias
        assert any("cuit_receptor" in d for d in veredicto.debilidades)
        # Es **dudosa**, no inválida: el dato sirve como indicio (gradación T-303).
        assert veredicto.valida is True

    def test_una_factura_a_completa_es_coherente(self):
        veredicto = veredicto_raw_de_evidencia(_evidencia(_factura_a_completa()))
        assert veredicto.incoherencias == []
        assert ID_RAW_COHERENCIA not in veredicto.reglas_aplicadas
        assert veredicto.valida is True
        assert veredicto.debilidades == []

    def test_una_factura_b_con_iva_discriminado_queda_debilitada(self):
        # La otra regla del prompt 11.1: "B es incompatible con IVA discriminado".
        ev = _evidencia(
            {
                "tipo_comprobante": _campo("B", "Recuadro 'B' COD. 06"),
                "iva": _campo("2.100,50", "IVA 21%: 2.100,50"),
            }
        )
        veredicto = veredicto_raw_de_evidencia(ev)
        assert ID_RAW_COHERENCIA in veredicto.reglas_aplicadas
        assert any("iva" in d for d in veredicto.debilidades)

    def test_una_factura_b_con_iva_en_cero_es_coherente(self):
        ev = _evidencia(
            {
                "tipo_comprobante": _campo("B", "Recuadro 'B' COD. 06"),
                "iva": _campo("0,00", "IVA: 0,00"),
            }
        )
        veredicto = veredicto_raw_de_evidencia(ev)
        assert veredicto.incoherencias == []
        assert veredicto.valida is True

    def test_una_implicacion_que_no_se_puede_juzgar_no_se_reporta(self):
        # Si la fuente no declaró el IVA, no se sabe si una B lo discrimina: la
        # ausencia ya viaja en campos_ausentes (no se castiga la lectura honesta).
        ev = _evidencia(
            {
                "tipo_comprobante": _campo("B", "Recuadro 'B' COD. 06"),
                "importe_total_facturado": _campo("1.210,00", "Total: 1.210,00"),
            }
        )
        veredicto = veredicto_raw_de_evidencia(ev)
        assert ID_RAW_COHERENCIA not in veredicto.reglas_aplicadas

    def test_la_letra_sin_datos_tributarios_no_se_juzga(self):
        # Una lectura que solo trae la letra y la letra no impone requisitos
        # evaluables (C no tiene implicaciones declaradas) queda limpia.
        ev = _evidencia(
            {"tipo_comprobante": _campo("C", "Recuadro 'C' COD. 011")}
        )
        veredicto = veredicto_raw_de_evidencia(ev)
        assert veredicto.debilidades == []

    def test_las_dos_implicaciones_pueden_dispararse_juntas(self):
        # A sin receptor y con un IVA que no se puede leer: dos debilidades
        # distintas, cada una con su motivo.
        ev = _evidencia(
            {
                "tipo_comprobante": _campo("A", "Recuadro 'A' COD. 01"),
                "iva": _campo("?", "IVA 21%: ilegible"),
            }
        )
        veredicto = veredicto_raw_de_evidencia(ev)
        assert ID_RAW_COHERENCIA in veredicto.reglas_aplicadas
        assert len(veredicto.incoherencias) >= 1

    def test_violaciones_de_coherencia_es_utilizable_por_si_sola(self):
        # La función es la superficie del contrato de coherencia (T-403): sirve
        # sin pasar por `evaluar_raw` (p. ej. para un reporte de auditoría).
        campo = campo_declarado_de_campo(
            CampoLectura(campo="tipo_comprobante", valor="A", fragmento="'A'")
        )
        assert campo is not None
        from voucherflow.rules.raw import CampoDeclarado, Gravedad  # noqa: PLC0415

        solo_letra = CampoDeclarado(
            campo="tipo_comprobante",
            valor="A",
            fragmento="'A'",
            vocabulario=("A", "B", "C", "M", "E"),
            coherencia=campo.coherencia,
        )
        motivos = violaciones_de_coherencia({"tipo_comprobante": solo_letra})
        assert len(motivos) == 1
        assert "cuit_receptor" in motivos[0]

    def test_una_implicacion_declarada_a_mano_funciona(self):
        # El punto de extensión es del llamador: se puede declarar otra
        # implicación sin tocar el registro raw.
        implicacion = ImplicacionCoherencia(
            disparador="M",
            motivo="una Factura M exige CUIT del emisor.",
            requeridos=("cuit_emisor",),
        )
        campo = campo_declarado_de_campo(
            CampoLectura(campo="tipo_comprobante", valor="M", fragmento="'M'")
        )
        assert campo is not None
        from voucherflow.rules.raw import CampoDeclarado  # noqa: PLC0415

        declarado = CampoDeclarado(
            campo="tipo_comprobante",
            valor="M",
            fragmento="'M'",
            vocabulario=("A", "B", "C", "M", "E"),
            coherencia=(implicacion,),
        )
        assert violaciones_de_coherencia({"tipo_comprobante": declarado})


# ---------------------------------------------------------------------------
# 4. Trazabilidad y contrato de la pasada raw
# ---------------------------------------------------------------------------


class TestTrazabilidadRaw:
    """Qué se evaluó, con qué criterio y con qué reglas queda registrado."""

    def test_el_veredicto_reporta_incoherencias_y_reglas(self):
        ev = _evidencia(
            {
                "tipo_comprobante": _campo("A", "Recuadro 'A' COD. 01"),
                "importe_total_facturado": _campo("$ 1,00", "Total: $ 9,99"),
            }
        )
        veredicto = veredicto_raw_de_evidencia(ev)
        assert ID_RAW_COHERENCIA in veredicto.reglas_aplicadas
        assert "RAW_SUSTENTO" in veredicto.reglas_aplicadas
        # Los ids salen en orden de prioridad y la coherencia al final.
        assert veredicto.reglas_aplicadas[-1] == ID_RAW_COHERENCIA

    def test_una_fuente_limpia_no_arrastra_reglas(self):
        veredicto = veredicto_raw_de_evidencia(_evidencia(_factura_a_completa()))
        assert veredicto.reglas_aplicadas == []
        assert veredicto.gravedad.value == "valida"
        assert veredicto.valida is True

    def test_el_veredicto_sigue_sin_decidir(self):
        # T-403 califica la evidencia; no resuelve la letra (eso es R1-R7/F5).
        ev = _evidencia(
            {
                "tipo_comprobante": _campo("A", "Recuadro 'A' COD. 01"),
                "cuit_emisor": _campo("30-12345678-9", "C.U.I.T. 30-12345678-9"),
            }
        )
        veredicto = veredicto_raw_de_evidencia(ev)
        assert veredicto.incoherencias
        # El valor declarado no se toca: la coherencia no "corrige" la lectura.
        assert ev.campos["tipo_comprobante"].valor == "A"

    def test_la_meta_del_campo_declara_el_criterio_de_sosten(self):
        ev = _evidencia(
            _factura_a_completa(
                descripcion=_campo("compra de insumos", "Compra de insumos")
            )
        )
        source = construir_source_evidence(ev)
        # Los montos y las fechas: se evalúan contra su forma canónica.
        assert source.campos["importe_total_facturado"].meta["sosten_estructurado"] is True
        assert source.campos["importe_total_facturado"].meta["raw_evaluado"] is True
        # El texto: se evalúa por contención del valor (no es "estructurado").
        assert source.campos["razon_social_emisor"].meta["sosten_estructurado"] is False
        assert source.campos["razon_social_emisor"].meta["raw_evaluado"] is True
        # La descripción: no se evalúa (frase sintética, T-403 mantiene el criterio).
        assert source.campos["descripcion"].meta["raw_evaluado"] is False
        assert source.campos["descripcion"].meta["sosten_estructurado"] is False

    def test_las_debilidades_de_incoherencia_llegan_al_source_evidence(self):
        ev = _evidencia(
            {
                "tipo_comprobante": _campo("A", "Recuadro 'A' COD. 01"),
                "cuit_emisor": _campo("30-12345678-9", "C.U.I.T. 30-12345678-9"),
            }
        )
        source = construir_source_evidence(ev)
        assert ID_RAW_COHERENCIA in source.reglas_aplicadas
        assert any("no leyó cuit_receptor" in d for d in source.debilidades)

    def test_los_campos_estructurados_son_los_del_contrato(self):
        assert CAMPOS_SOSTEN_ESTRUCTURADO <= set(CAMPOS_EXTRACCION)
        assert CAMPOS_SOSTEN_ESTRUCTURADO.isdisjoint(CAMPOS_SOSTEN_NO_EVALUADO)
        # Con T-403 los montos y las fechas entran; la descripción no.
        for campo in ("importe_total_facturado", "iva", "fecha_emision"):
            assert campo in CAMPOS_SOSTEN_ESTRUCTURADO
        assert "descripcion" not in CAMPOS_SOSTEN_ESTRUCTURADO

    def test_la_coherencia_declarada_es_del_campo_de_la_letra(self):
        assert set(COHERENCIA_POR_CAMPO) == {"tipo_comprobante"}
        for implicaciones in COHERENCIA_POR_CAMPO.values():
            for implicacion in implicaciones:
                assert implicacion.disparador
                assert implicacion.motivo


# ---------------------------------------------------------------------------
# 5. Integración con el flujo y fronteras de la tarea
# ---------------------------------------------------------------------------


class TestIntegracionYFronteras:
    """La pasada raw de T-403 dentro de la corrida, y lo que NO hace."""

    def test_el_flujo_reporta_incoherencias_en_el_detalle(self):
        lector = FakeLector(
            json.dumps(
                {
                    CAMPO_FUENTE_LECTURA: "llm",
                    CLAVE_CAMPOS: {
                        "tipo_comprobante": _campo("A", "Recuadro 'A' COD. 01"),
                    },
                },
                ensure_ascii=False,
            )
        )
        resultado = ejecutar_flujo(
            "llm", lector, markdown="FACTURA A", settings=_settings_dobles()
        )
        assert resultado.veredicto.incoherencias
        assert resultado.source_evidence.debilidades

    def test_las_dos_fuentes_se_califican_por_separado(self):
        # E-EXT-2: cada fuente se valida **antes** de combinarse. Una fuente
        # incoherente no contamina a la otra.
        lector = FakeLector(
            json.dumps(
                {
                    CAMPO_FUENTE_LECTURA: "llm",
                    CLAVE_CAMPOS: {
                        "tipo_comprobante": _campo("A", "Recuadro 'A' COD. 01"),
                        "cuit_emisor": _campo("30-12345678-9", "C.U.I.T. 30-12345678-9"),
                        "cuit_receptor": _campo("27-30111222-4", "C.U.I.T. 27-30111222-4"),
                    },
                },
                ensure_ascii=False,
            )
        )
        resultado = extraer_evidencia(
            lector, markdown="FACTURA A", settings=_settings_dobles()
        )
        evidencia = resultado.por_fuente("llm")
        assert evidencia is not None
        assert evidencia.reglas_aplicadas == []
        assert evidencia.debilidades == []

    def test_la_evidencia_cruda_sigue_disponible(self):
        # La pasada raw **califica**; no reescribe los valores publicados.
        ev = _evidencia(_factura_a_completa())
        veredicto = veredicto_raw_de_evidencia(ev)
        assert veredicto.valida is True
        assert ev.campos["importe_total_facturado"].valor == "$ 12.345,67"

    def test_combinar_evidencia_sigue_siendo_esqueleto_de_t404(self):
        from voucherflow.extraction import combinar_evidencia

        with pytest.raises(NotImplementedError) as exc:
            combinar_evidencia("doc-1", [])
        assert "T-404" in str(exc.value)

    def test_el_registro_raw_de_t303_no_se_reescribio(self):
        # T-403 extiende el **uso** del registro (sostenedor/normalizador_valor/
        # coherencia por campo), no el motor: las cuatro reglas siguen intactas.
        from voucherflow.rules.raw import REGISTRO_RAW

        assert [r.id for r in REGISTRO_RAW.reglas] == [
            "RAW_CAMPO",
            "RAW_VOCABULARIO",
            "RAW_SUSTENTO",
            "RAW_CONTRADICCION",
        ]

    def test_el_evaluador_sigue_aceptando_un_mapping_completo(self):
        # El contrato de T-303 no cambió: `evaluar_raw` acepta varios campos de
        # una fuente (que es exactamente lo que T-403 necesita).
        ev = _evidencia(_factura_a_completa())
        declarados = {
            nombre: campo_declarado_de_campo(c)
            for nombre, c in ev.campos.items()
        }
        veredicto = evaluar_raw("llm", {k: v for k, v in declarados.items() if v})
        assert veredicto.fuente == "llm"
        assert veredicto.incoherencias == []

    def test_los_dobles_de_la_suite_no_tocan_la_red(self):
        lector = FakeLector(
            json.dumps(
                {
                    CAMPO_FUENTE_LECTURA: "llm",
                    CLAVE_CAMPOS: _factura_a_completa(),
                },
                ensure_ascii=False,
            )
        )
        ejecutar_flujo("llm", lector, markdown="x", settings=_settings_dobles())
        assert lector.llamadas

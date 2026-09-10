"""Tests de las reglas **raw** por fuente — pasada 1 (F3 / T-303, E-CLAS-1).

**DoD de T-303** (F3-subplan §3.3): "Las reglas raw por fuente determinan
candidatos descartados/restantes y alimentan la decisión con trazabilidad" y "el
veredicto raw no decide la letra final (solo la califica)".

Qué se verifica, en el orden del subplan:

1. Las **cuatro reglas** del registro por separado (``RAW_CAMPO``,
   ``RAW_VOCABULARIO``, ``RAW_SUSTENTO``, ``RAW_CONTRADICCION``), cada una con su
   caso positivo y negativo, sobre el vocabulario de tipo/letra.
2. ``REGISTRO_RAW`` es un registro de ``Rule`` declarativas del motor de F0 (no
   se reescribió ``registry.py``), ordenado por prioridad y con ``tipo="raw"``.
3. El **contrato de vocabulario**: una letra fuera de ``{A,B,C,M,E}`` (``Z``,
   ``090``, ``099``) invalida la evidencia y **reporta el valor crudo** (D-13).
4. La **gradación**: ``valida`` / ``dudosa`` / ``invalida`` se traduce a
   ``SourceEvidence.valida`` + ``debilidades`` — y ``dudosa`` **no** invalida.
5. ``candidatos_descartados`` incluye la letra que el **sustento contradijo**;
   ``candidatos_restantes`` refleja las alternativas viables; nunca hay
   intersección (blindaje ADR-008).
6. La integración con T-302/T-301: ``veredicto_raw_de_evidencia`` califica la
   lectura, ``construir_source_evidence`` publica el veredicto y
   ``clasificar_tipo_comprobante(..., candidatos_raw=...)` enriquece los
   candidatos **sin cambiar la letra**.
7. **Reutilización F4/T-403**: el registro es genérico sobre campos declarados
   (se prueba con un campo que no es la letra: un CUIT/campo numérico), de modo
   que F4 lo consuma sin reimplementarlo.

Reglas duras (F3-subplan §4): suite default sin Ollama ni Docling; no se toca
``rules/registry.py`` ni los contratos congelados de ``classification``; sin
dependencias nuevas.
"""

from __future__ import annotations

import re

import pytest

from voucherflow.classification import (
    construir_source_evidence,
    contexto_desde_evidencia,
    parsear_evidencia_lectura,
    veredicto_raw_de_evidencia,
)
from voucherflow.classification.tipo_comprobante import clasificar_tipo_comprobante
from voucherflow.rules.contexto import (
    CONDICION_RI,
    LETRAS_COMPROBANTE,
    ContextoTipoComprobante,
    normalizar_letra,
)
from voucherflow.rules.raw import (
    GRAVEDAD_POR_REGLA,
    REGISTRO_RAW,
    CampoDeclarado,
    Gravedad,
    VeredictoRaw,
    coincidencias_en_sustento,
    condicion_raw_campo,
    condicion_raw_contradiccion,
    condicion_raw_sustento,
    condicion_raw_vocabulario,
    construir_registro_raw,
    evaluar_raw,
)
from voucherflow.rules.registry import Registry, Rule
from voucherflow.rules.tipo_comprobante_rules import REGEX_LETRA_ENCABEZADO

# ---------------------------------------------------------------------------
# Fixtures de campos declarados (vocabulario de letras, como lo usa T-302)
# ---------------------------------------------------------------------------


def _campo_letra(
    valor: str | None = "A",
    fragmento: str = "FACTURA A COD. 001",
    *,
    patron: bool = True,
) -> CampoDeclarado:
    """``CampoDeclarado`` de la letra, tal como lo arma ``classification``."""
    return CampoDeclarado(
        campo="tipo_detectado_por_documento",
        valor=valor,
        fragmento=fragmento,
        vocabulario=LETRAS_COMPROBANTE,
        normalizador=normalizar_letra,
        patron_sustento=REGEX_LETRA_ENCABEZADO if patron else None,
    )


def _veredicto(campo: CampoDeclarado, fuente: str = "vlm") -> VeredictoRaw:
    return evaluar_raw(fuente, campo)


# ---------------------------------------------------------------------------
# 1. Cada regla por separado
# ---------------------------------------------------------------------------


class TestReglaRawCampo:
    """RAW_CAMPO — fuente incompleta (sin valor o sin fragmento de sustento)."""

    def test_sin_valor_dispara(self):
        campo = _campo_letra(valor=None)
        assert condicion_raw_campo(campo) is True
        veredicto = _veredicto(campo)
        assert veredicto.reglas_aplicadas == ["RAW_CAMPO"]
        assert any("no declaró un valor" in debilidad for debilidad in veredicto.debilidades)

    def test_valor_vacio_o_solo_espacios_dispara(self):
        # Un string vacío es ausencia, no un valor (no se puede validar).
        assert condicion_raw_campo(_campo_letra(valor="   ")) is True

    def test_sin_sustento_dispara(self):
        campo = _campo_letra(fragmento="")
        assert condicion_raw_campo(campo) is True
        assert any("no citó fragmento" in d for d in _veredicto(campo).debilidades)

    def test_completo_no_dispara(self):
        assert condicion_raw_campo(_campo_letra()) is False

    def test_no_exigir_sustento_lo_desactiva(self):
        # Un llamador de F4 puede validar un campo sin fragmento (p. ej. un valor
        # derivado por programa): ``exigir_sustento=False`` lo permite.
        campo = CampoDeclarado(
            campo="total",
            valor="100.00",
            fragmento="",
            vocabulario=None,
            exigir_sustento=False,
        )
        assert condicion_raw_campo(campo) is False


class TestReglaRawVocabulario:
    """RAW_VOCABULARIO — el valor declarado no pertenece al vocabulario."""

    def test_letra_fuera_del_vocabulario_dispara(self):
        for crudo in ("Z", "X", "AB", "factura A"):
            campo = _campo_letra(valor=crudo, fragmento=f"Recuadro: {crudo}")
            assert condicion_raw_vocabulario(campo) is True, crudo

    def test_codigos_de_tique_090_099_quedan_fuera(self):
        # D-13: el vocabulario del motor no incluye los tiques; una fuente que
        # los declare queda fuera de vocabulario (no se inventa el mapeo).
        for crudo in ("090", "099"):
            assert condicion_raw_vocabulario(_campo_letra(valor=crudo)) is True

    def test_sin_vocabulario_no_aplica(self):
        campo = CampoDeclarado(campo="razon_social", valor="Ejemplo S.A.", fragmento="Ejemplo S.A.")
        assert condicion_raw_vocabulario(campo) is False

    def test_valor_valido_no_dispara(self):
        assert condicion_raw_vocabulario(_campo_letra(valor="a")) is False  # se normaliza

    def test_la_gravedad_es_invalida(self):
        veredicto = _veredicto(_campo_letra(valor="Z", fragmento="Recuadro con Z"))
        assert veredicto.valida is False
        assert veredicto.gravedad is Gravedad.invalida
        assert GRAVEDAD_POR_REGLA["RAW_VOCABULARIO"] is Gravedad.invalida

    def test_el_motivo_reporta_el_valor_crudo(self):
        # El punto de la regla: distinguir "declaró algo inválido" de "no declaró
        # nada". El mensaje debe citar lo que la fuente dijo, no ``None``.
        veredicto = _veredicto(_campo_letra(valor="Z", fragmento="Recuadro con Z"))
        assert "'Z'" in veredicto.debilidades[0]
        assert "fuera del vocabulario" in veredicto.debilidades[0]


class TestReglaRawSustento:
    """RAW_SUSTENTO — el fragmento no contiene el valor declarado."""

    def test_sustento_que_no_sostiene_dispara(self):
        campo = _campo_letra(fragmento="Recuadro ilegible en el encabezado")
        assert condicion_raw_sustento(campo) is True
        assert any("indicio, no como prueba" in d for d in _veredicto(campo).debilidades)

    def test_sustento_que_sostiene_no_dispara(self):
        assert condicion_raw_sustento(_campo_letra(fragmento="FACTURA A COD. 001")) is False

    def test_calla_si_no_hay_fragmento(self):
        # De la ausencia de fragmento se ocupa RAW_CAMPO (no se cuenta dos veces).
        assert condicion_raw_sustento(_campo_letra(fragmento="")) is False

    def test_calla_si_el_valor_no_esta_en_el_vocabulario(self):
        # De eso se ocupa RAW_VOCABULARIO (una debilidad por problema).
        campo = _campo_letra(valor="Z", fragmento="Recuadro ilegible")
        assert condicion_raw_sustento(campo) is False

    def test_la_gravedad_es_dudosa_no_invalida(self):
        # La letra es válida y el dato sirve como indicio: la evidencia NO se
        # invalida (se degrada a dudosa).
        veredicto = _veredicto(_campo_letra(fragmento="Recuadro ilegible"))
        assert veredicto.gravedad is Gravedad.dudosa
        assert veredicto.valida is True

    def test_patron_estricto_de_texto(self):
        # Con el patrón de R5, la letra suelta no alcanza: hace falta la
        # expresión ``FACTURA <letra>``.
        campo = CampoDeclarado(
            campo="tipo_detectado_por_documento",
            valor="A",
            fragmento="Letra A en el margen superior",
            vocabulario=LETRAS_COMPROBANTE,
            normalizador=normalizar_letra,
            patron_sustento=REGEX_LETRA_ENCABEZADO,
        )
        assert condicion_raw_sustento(campo) is True
        sostenido = CampoDeclarado(
            campo="tipo_detectado_por_documento",
            valor="A",
            fragmento="FACTURA A COD. 001",
            vocabulario=LETRAS_COMPROBANTE,
            normalizador=normalizar_letra,
            patron_sustento=REGEX_LETRA_ENCABEZADO,
        )
        assert condicion_raw_sustento(sostenido) is False


class TestReglaRawContradiccion:
    """RAW_CONTRADICCION — el fragmento sostiene otro valor del vocabulario."""

    def test_contradiccion_dispara(self):
        campo = _campo_letra(valor="A", fragmento="FACTURA B COD. 006")
        assert condicion_raw_contradiccion(campo) is True
        veredicto = _veredicto(campo)
        assert "RAW_CONTRADICCION" in veredicto.reglas_aplicadas
        assert any("contradice" in debilidad for debilidad in veredicto.debilidades)

    def test_sin_contradiccion_no_dispara(self):
        assert condicion_raw_contradiccion(_campo_letra(valor="A")) is False

    def test_la_contradiccion_no_pisa_el_valor_declarado(self):
        # La lectura declarada sigue siendo el indicio; la contradicción se
        # registra para auditoría (T-303), no cambia el valor.
        campo = _campo_letra(valor="A", fragmento="FACTURA B COD. 006")
        veredicto = _veredicto(campo)
        assert "A" in veredicto.candidatos_restantes
        assert "B" in veredicto.candidatos_descartados

    def test_coincidencias_en_sustento_encuentra_los_valores_del_vocabulario(self):
        campo = _campo_letra(valor="A", fragmento="FACTURA B COD. 006")
        assert coincidencias_en_sustento(campo) == ["B"]
        campo_dos = _campo_letra(valor="A", fragmento="FACTURA B / COMPROBANTE C")
        assert coincidencias_en_sustento(campo_dos) == ["B", "C"]

    def test_no_confunde_preposiciones_con_letras(self):
        # Regresión: en español la preposición "a" y la conjunción "e" son
        # palabras sueltas que coinciden con las letras A/E del vocabulario.
        # Una descripción del VLM como "Recuadro con 'B' junto a COD. 006" NO
        # sostiene la letra A solo por la preposición. Aplica al camino **sin
        # patrón** (el del VLM: su fragmento es una descripción, no texto OCR).
        campo = _campo_letra(
            valor="B",
            fragmento="Recuadro grande con 'B' junto a COD. 006",
            patron=False,
        )
        assert coincidencias_en_sustento(campo) == ["B"]
        assert condicion_raw_sustento(campo) is False

    def test_los_valores_de_un_caracter_exigen_mayuscula_exacta(self):
        # La letra se escribe en mayúscula en los comprobantes; una "a"
        # minúscula es la preposición, no la letra. Camino sin patrón (VLM).
        campo_a = _campo_letra(
            valor="A", fragmento="recuadro junto a un código", patron=False
        )
        assert "A" not in coincidencias_en_sustento(campo_a)
        campo_ok = _campo_letra(
            valor="A", fragmento="recuadro con A grande", patron=False
        )
        assert "A" in coincidencias_en_sustento(campo_ok)

    def test_sin_vocabulario_no_busca(self):
        campo = CampoDeclarado(campo="total", valor="100", fragmento="Total: 100")
        assert coincidencias_en_sustento(campo) == []


# ---------------------------------------------------------------------------
# 2. El registro es de ``Rule`` del motor F0
# ---------------------------------------------------------------------------


class TestRegistroRaw:
    """``REGISTRO_RAW`` usa el motor declarativo congelado de F0."""

    def test_es_un_registry_con_las_cuatro_reglas(self):
        assert isinstance(REGISTRO_RAW, Registry)
        assert [r.id for r in REGISTRO_RAW.reglas] == [
            "RAW_CAMPO",
            "RAW_VOCABULARIO",
            "RAW_SUSTENTO",
            "RAW_CONTRADICCION",
        ]

    def test_todas_son_rule_de_tipo_raw_ordenadas_por_prioridad(self):
        for regla in REGISTRO_RAW.reglas:
            assert isinstance(regla, Rule)
            assert regla.tipo == "raw"
            assert regla.detalle  # legible para auditoría
        prioridades = [r.prioridad for r in REGISTRO_RAW.reglas]
        assert prioridades == sorted(prioridades)

    def test_construir_registro_raw_devuelve_una_instancia_nueva(self):
        # Nadie debe mutar el registro compartido (mismo criterio que T-301).
        otro = construir_registro_raw()
        otro.registrar(Rule(id="RAW_X", prioridad=99, condicion=lambda c: False))
        assert "RAW_X" not in [r.id for r in REGISTRO_RAW.reglas]

    def test_las_reglas_de_f0_siguen_intactas(self):
        # Regla dura: no se reescribió ``registry.py``.
        from voucherflow.rules.registry import Registry as RegistryF0

        registro = RegistryF0()
        registro.registrar(Rule(id="R1", prioridad=1, condicion=lambda ctx: True, resultado="A"))
        assert registro.ids_disparados({}) == ["R1"]


# ---------------------------------------------------------------------------
# 3/4. Veredicto: gradación y multiplicidad de reglas
# ---------------------------------------------------------------------------


class TestVeredictoRaw:
    """El veredicto acumula gravedad y traduce a ``valida``/``debilidades``."""

    def test_lectura_sana_es_valida_y_sin_debilidades(self):
        veredicto = _veredicto(_campo_letra())
        assert veredicto.gravedad is Gravedad.valida
        assert veredicto.valida is True
        assert veredicto.sin_debilidades is True
        assert veredicto.es_valida is True

    def test_varias_reglas_se_acumulan_en_orden_de_prioridad(self):
        # Sin valor Y sin fragmento: RAW_CAMPO es la única aplicable (las otras
        # necesitan un valor declarado para comparar).
        campo = _campo_letra(valor=None, fragmento="")
        veredicto = _veredicto(campo)
        assert veredicto.reglas_aplicadas == ["RAW_CAMPO"]

    def test_contradiccion_mas_sustento_debilitan_sin_invalidar(self):
        campo = _campo_letra(valor="A", fragmento="FACTURA B COD. 006")
        veredicto = _veredicto(campo)
        assert veredicto.reglas_aplicadas == ["RAW_SUSTENTO", "RAW_CONTRADICCION"]
        assert veredicto.gravedad is Gravedad.dudosa
        assert veredicto.valida is True
        assert len(veredicto.debilidades) == 2

    def test_la_gravedad_mas_severa_gana(self):
        # Valor fuera de vocabulario: invalida, aunque también falte sostén.
        veredicto = _veredicto(_campo_letra(valor="Z", fragmento=""))
        assert veredicto.gravedad is Gravedad.invalida
        assert veredicto.valida is False

    def test_acepta_un_mapping_de_campos(self):
        # F4/T-403 valida varios campos de una fuente en una sola pasada.
        veredicto = evaluar_raw(
            "vlm",
            {
                "letra": _campo_letra(valor="A", fragmento="FACTURA A"),
                "cuit": CampoDeclarado(campo="cuit", valor="30123456789", fragmento="CUIT 30-12345678-9"),
            },
        )
        assert veredicto.fuente == "vlm"
        assert veredicto.valida is True

    def test_mapping_con_valores_invalidos_lanza_type_error(self):
        with pytest.raises(TypeError, match="CampoDeclarado"):
            evaluar_raw("vlm", {"letra": "A"})
        with pytest.raises(TypeError, match="CampoDeclarado"):
            evaluar_raw("vlm", "no soy un campo")

    def test_acepta_un_registro_alternativo(self):
        registro = Registry()
        registro.registrar(
            Rule(
                id="RAW_SOLO_MI_REGLA",
                prioridad=1,
                condicion=lambda campo: True,
                resultado=Gravedad.dudosa,
                tipo="raw",
                detalle="regla de prueba",
            )
        )
        veredicto = evaluar_raw("vlm", _campo_letra(), registro=registro)
        assert veredicto.reglas_aplicadas == ["RAW_SOLO_MI_REGLA"]
        assert veredicto.gravedad is Gravedad.dudosa


# ---------------------------------------------------------------------------
# 5. Candidatos descartados / restantes
# ---------------------------------------------------------------------------


class TestCandidatosRaw:
    """Los candidatos salen de lo que el **sustento** sostiene o contradice."""

    def test_contradiccion_descarta_la_letra_del_sustento(self):
        veredicto = _veredicto(_campo_letra(valor="A", fragmento="FACTURA B COD. 006"))
        assert veredicto.candidatos_descartados == ["B"]
        assert "A" in veredicto.candidatos_restantes

    def test_lectura_sana_no_descarta_nada(self):
        veredicto = _veredicto(_campo_letra(valor="A", fragmento="FACTURA A COD. 001"))
        assert veredicto.candidatos_descartados == []
        assert veredicto.candidatos_restantes == ["A"]

    def test_sin_valor_no_se_inventan_candidatos(self):
        # Sin lectura no hay vocabulario que proponer: el motor decide con lo suyo.
        veredicto = _veredicto(_campo_letra(valor=None, fragmento=""))
        assert veredicto.candidatos_descartados == []
        assert veredicto.candidatos_restantes == []

    def test_la_unica_lectura_disponible_no_se_tacha(self):
        # Si nadie declaró un valor válido, la única lectura viva (la del
        # fragmento) se conserva como restante: tacharla dejaría el caso sin
        # evidencia (criterio conservador).
        campo = CampoDeclarado(
            campo="tipo_detectado_por_documento",
            valor=None,
            fragmento="FACTURA B COD. 006",
            vocabulario=LETRAS_COMPROBANTE,
            normalizador=normalizar_letra,
            patron_sustento=REGEX_LETRA_ENCABEZADO,
        )
        veredicto = _veredicto(campo)
        assert veredicto.candidatos_descartados == []
        assert veredicto.candidatos_restantes == ["B"]

    def test_nunca_hay_interseccion_entre_descartados_y_restantes(self):
        # Blindaje ADR-008 (contrato de CombinedEvidence).
        campo = _campo_letra(valor="A", fragmento="FACTURA A y FACTURA B")
        veredicto = _veredicto(campo)
        assert set(veredicto.candidatos_descartados) & set(veredicto.candidatos_restantes) == set()
        assert "A" in veredicto.candidatos_restantes

    def test_el_veredicto_no_decide_la_letra(self):
        # El veredicto solo califica: no expone una "letra final" — la resuelve
        # R1-R7 (T-301).
        veredicto = _veredicto(_campo_letra(valor="A", fragmento="FACTURA B"))
        assert not hasattr(veredicto, "letra")
        assert not hasattr(veredicto, "letra_final")


# ---------------------------------------------------------------------------
# 6. Integración con T-302 / T-301
# ---------------------------------------------------------------------------


def _evidencia_llm(
    letra: str | None = "A",
    fragmento: str = "FACTURA A COD. 001",
):
    """Evidencia del LLM (fuente de texto) lista para la pasada raw."""
    import json

    return parsear_evidencia_lectura(
        json.dumps(
            {
                "tipo_detectado_por_documento": letra,
                "tipo_detectado_por_documento_explicacion": fragmento,
                "candidatos_descartados": [],
                "candidatos_restantes": [],
                "campos_desconocidos": [],
                "fuente_lectura": "llm",
            },
            ensure_ascii=False,
        ),
        fuente="llm",
    )


class TestIntegracionClasificacion:
    """La pasada raw califica la lectura y alimenta los candidatos del motor."""

    def test_lectura_valida_no_genera_debilidades(self):
        veredicto = veredicto_raw_de_evidencia(_evidencia_llm())
        assert veredicto.valida is True
        assert veredicto.reglas_aplicadas == []

    def test_letra_invalida_invalida_la_evidencia_y_reporta_el_crudo(self):
        veredicto = veredicto_raw_de_evidencia(_evidencia_llm(letra="Z", fragmento="Recuadro Z"))
        assert veredicto.valida is False
        assert veredicto.reglas_aplicadas == ["RAW_VOCABULARIO"]
        assert "'Z'" in veredicto.debilidades[0]

    def test_el_source_evidence_publea_el_veredicto(self):
        # ``SourceEvidence.valida``/``debilidades``/``reglas_aplicadas`` vienen
        # de la pasada raw (ADR-001).
        fuente_ev = construir_source_evidence(_evidencia_llm())
        assert fuente_ev.valida is True
        assert fuente_ev.debilidades == []

        dudosa = construir_source_evidence(_evidencia_llm(fragmento="Recuadro ilegible"))
        assert dudosa.valida is True  # dudosa sigue siendo utilizable
        assert dudosa.debilidades
        assert "RAW_SUSTENTO" in dudosa.reglas_aplicadas

        invalida = construir_source_evidence(_evidencia_llm(letra="Z", fragmento="Recuadro Z"))
        assert invalida.valida is False
        assert "RAW_VOCABULARIO" in invalida.reglas_aplicadas

    def test_la_nota_de_r5_aparece_solo_en_la_fuente_de_texto(self):
        # El patrón de R5 es el insumo de la fuente de texto: un fragmento sin
        # ``FACTURA <letra>`` deja la nota correspondiente.
        veredicto = veredicto_raw_de_evidencia(
            _evidencia_llm(letra="A", fragmento="Letra A en el margen")
        )
        assert any("R5" in debilidad for debilidad in veredicto.debilidades)

    def test_el_motor_no_cambia_la_letra_con_los_candidatos_raw(self):
        # El veredicto raw **califica**; la letra la decide R1-R7.
        from voucherflow.rules.raw import VeredictoRaw as _Veredicto

        base = ContextoTipoComprobante(
            emisor_condicion_fiscal=CONDICION_RI,
            receptor_condicion_fiscal=CONDICION_RI,
        )
        evidencia = _evidencia_llm(letra="A", fragmento="FACTURA A COD. 001")
        ctx = contexto_desde_evidencia(evidencia, base)
        sin_raw = clasificar_tipo_comprobante(ctx)
        veredicto = veredicto_raw_de_evidencia(evidencia)
        con_raw = clasificar_tipo_comprobante(ctx, candidatos_raw=[veredicto])
        assert sin_raw.letra == con_raw.letra == "A"
        assert sin_raw.reglas_aplicadas == con_raw.reglas_aplicadas

    def test_los_candidatos_curados_enriquecen_el_detalle(self):
        base = ContextoTipoComprobante(
            emisor_condicion_fiscal=CONDICION_RI,
            receptor_condicion_fiscal=CONDICION_RI,
        )
        # El texto declara A pero cita ``FACTURA B``: B queda descartado con
        # sustento (información que el motor no puede ver solo).
        evidencia = _evidencia_llm(letra="A", fragmento="FACTURA B COD. 006")
        ctx = contexto_desde_evidencia(evidencia, base)
        veredicto = veredicto_raw_de_evidencia(evidencia)
        resultado = clasificar_tipo_comprobante(ctx, candidatos_raw=[veredicto])
        assert resultado.detalle["reglas_raw"] == ["RAW_SUSTENTO", "RAW_CONTRADICCION"]
        assert resultado.detalle["candidatos_curados"]["descartados"] == ["B"]

    def test_acepta_la_lectura_completa_como_candidatos_raw(self):
        # ``candidatos_raw`` acepta la ``LecturaTipoComprobante`` de T-302.
        base = ContextoTipoComprobante(
            emisor_condicion_fiscal=CONDICION_RI,
            receptor_condicion_fiscal=CONDICION_RI,
        )

        class _Lector:
            def ask(self, messages, model, json_format=False, options=None, num_ctx=None):
                import json

                class _Resp:
                    contenido = json.dumps(
                        {
                            "tipo_detectado_por_documento": "A",
                            "tipo_detectado_por_documento_explicacion": "FACTURA A COD. 001",
                            "fuente_lectura": "llm",
                        }
                    )

                return _Resp()

        from voucherflow.classification import leer_evidencia

        lectura = leer_evidencia(
            _Lector(), markdown="FACTURA A", contexto_base=base
        )
        assert lectura.candidatos_restantes == ["A"]
        resultado = clasificar_tipo_comprobante(lectura.contexto, candidatos_raw=lectura)
        assert resultado.letra == "A"

    def test_candidatos_raw_none_mantiene_la_derivacion_simple(self):
        base = ContextoTipoComprobante(
            emisor_condicion_fiscal=CONDICION_RI,
            receptor_condicion_fiscal=CONDICION_RI,
        )
        ctx = contexto_desde_evidencia(_evidencia_llm(), base)
        resultado = clasificar_tipo_comprobante(ctx)
        assert "reglas_raw" not in resultado.detalle


# ---------------------------------------------------------------------------
# 7. Reutilización por F4/T-403 (el registro es agnóstico del dominio)
# ---------------------------------------------------------------------------


class TestReutilizacionF4:
    """El mismo registro sirve para campos que no son la letra (F4/T-403)."""

    def test_valida_un_campo_numerico_con_sustento(self):
        cuit = CampoDeclarado(
            campo="cuit_emisor",
            valor="30-12345678-9",
            fragmento="CUIT: 30-12345678-9",
            vocabulario=None,  # texto libre: sin vocabulario cerrado
        )
        veredicto = evaluar_raw("llm", cuit)
        assert veredicto.valida is True
        assert veredicto.reglas_aplicadas == []

    def test_detecta_un_valor_que_el_sustento_no_contiene(self):
        total = CampoDeclarado(
            campo="importe_total",
            valor="1000.00",
            fragmento="Total: 250.00",
            vocabulario=None,
        )
        veredicto = evaluar_raw("llm", total)
        assert veredicto.gravedad is Gravedad.dudosa
        assert "RAW_SUSTENTO" in veredicto.reglas_aplicadas

    def test_detecta_un_valor_fuera_de_un_vocabulario_cerrado(self):
        # Vocabulario cerrado de otro dominio (p. ej. tipo_documento).
        tipo = CampoDeclarado(
            campo="tipo_documento",
            valor="Remito",
            fragmento="Remito",
            vocabulario=("Factura", "Nota de Crédito", "Nota de Débito"),
        )
        veredicto = evaluar_raw("vlm", tipo)
        assert veredicto.valida is False
        assert "RAW_VOCABULARIO" in veredicto.reglas_aplicadas

    def test_el_registro_no_depende_del_dominio_del_comprobante(self):
        # Ninguna regla menciona "letra": el vocabulario y el normalizador son
        # datos que pasa el llamador.
        for regla in REGISTRO_RAW.reglas:
            assert "letra" not in regla.detalle.lower()

"""Tests unitarios de las reglas R1-R7 del tipo/letra (F3 / T-301, E-CLAS-1).

**DoD de T-301** (F3-subplan §3.1): "Las reglas R1-R7 del prompt WIP viven en un
motor de reglas en código (ADR-006) con **tests unitarios por regla**". Este
archivo tiene **una clase por regla** (R1, R2A, R2B, R3, R4, R5, R6, R7) más las
combinaciones y la trazabilidad del orquestador.

Las reglas se portan **literalmente** de
``prompts/wip/deteccion_tipo_factura.yaml`` (§``reglas`` + §``orden_de_evaluacion``);
los criterios de aceptación son el Gherkin de E-CLAS-1
(``v2/docs/plan/02-epicas/E-CLAS.md``).

La suite es **pura** (sin Ollama, sin Docling): el motor de reglas es
determinístico y se evalúa sobre el contexto tipado
(:class:`~voucherflow.rules.contexto.ContextoTipoComprobante`).
"""

from __future__ import annotations

import pytest

from voucherflow.classification import TipoComprobanteResult, clasificar_tipo_comprobante
from voucherflow.rules import (
    MENSAJE_R7,
    REGEX_LETRA_ENCABEZADO,
    REGISTRO_CONFLICTO,
    REGISTRO_LECTURA,
    REGISTRO_NEGOCIO,
    TABLA_INFERENCIA_R6,
    ContextoTipoComprobante,
    construir_alerta,
    construir_registros,
    evaluar_conflicto,
    evaluar_lectura,
    evaluar_negocio,
    extraer_letra_encabezado,
    letra_de_campos_totales,
    letra_de_encabezado,
    letra_de_recuadro,
)
from voucherflow.rules.contexto import (
    CONDICION_CONSUMIDOR_FINAL,
    CONDICION_EXENTO,
    CONDICION_MONOTRIBUTO,
    CONDICION_RI,
)

# Contexto base "todo lo sabe" que las clases particulares ajustan.
ARGENTINA = "Argentina"


def ctx_negocio(emisor: str | None, receptor: str | None, pais: str | None = ARGENTINA, **extra):
    """Contexto con condiciones fiscales informadas y sin evidencia de lectura."""
    return ContextoTipoComprobante(
        emisor_condicion_fiscal=emisor,
        receptor_condicion_fiscal=receptor,
        receptor_pais=pais,
        **extra,
    )


# ---------------------------------------------------------------------------
# Registro de negocio
# ---------------------------------------------------------------------------


class TestR1EmisorMonotributoExento:
    """R1 — emisor Monotributo o Exento → C (independiente del receptor)."""

    @pytest.mark.parametrize(
        "receptor",
        [CONDICION_RI, CONDICION_MONOTRIBUTO, CONDICION_EXENTO, CONDICION_CONSUMIDOR_FINAL, None],
    )
    def test_emisor_monotributo_da_c(self, receptor):
        ctx = ctx_negocio(CONDICION_MONOTRIBUTO, receptor)
        assert evaluar_negocio(ctx) == "C", (
            "R1: un emisor Monotributo debe dar tipo esperado C sin importar el receptor "
            "(Gherkin E-CLAS-1 · R1)"
        )
        assert "R1" in REGISTRO_NEGOCIO.ids_disparados(ctx), "R1 debe figurar entre las reglas disparadas"

    def test_emisor_exento_da_c(self):
        ctx = ctx_negocio(CONDICION_EXENTO, CONDICION_RI)
        assert evaluar_negocio(ctx) == "C", "R1: un emisor Exento también espera C"
        assert "R1" in REGISTRO_NEGOCIO.ids_disparados(ctx)

    def test_emisor_ri_no_dispara_r1(self):
        ctx = ctx_negocio(CONDICION_RI, CONDICION_RI)
        assert "R1" not in REGISTRO_NEGOCIO.ids_disparados(ctx), (
            "R1 es exclusiva de emisor Monotributo/Exento; un emisor RI no la dispara"
        )


class TestR2AResponsabilidadFiscal:
    """R2A — emisor RI + receptor RI → A."""

    def test_ri_ri_da_a(self):
        ctx = ctx_negocio(CONDICION_RI, CONDICION_RI)
        assert evaluar_negocio(ctx) == "A", (
            "R2A: emisor RI con receptor RI debe dar tipo esperado A (Gherkin E-CLAS-1 · R2A)"
        )
        assert "R2A" in REGISTRO_NEGOCIO.ids_disparados(ctx)

    def test_r2a_no_aplica_con_emisor_monotributo(self):
        ctx = ctx_negocio(CONDICION_MONOTRIBUTO, CONDICION_RI)
        assert "R2A" not in REGISTRO_NEGOCIO.ids_disparados(ctx), (
            "R2A exige emisor RI; con emisor Monotributo solo aplica R1"
        )

    def test_r2a_no_aplica_con_receptor_no_ri(self):
        ctx = ctx_negocio(CONDICION_RI, CONDICION_MONOTRIBUTO)
        assert "R2A" not in REGISTRO_NEGOCIO.ids_disparados(ctx), (
            "R2A exige receptor RI; con receptor Monotributo aplica R2B"
        )


class TestR2BResponsabilidadFiscal:
    """R2B — emisor RI + receptor Monotributo/Exento/Consumidor Final → B."""

    @pytest.mark.parametrize(
        "receptor", [CONDICION_MONOTRIBUTO, CONDICION_EXENTO, CONDICION_CONSUMIDOR_FINAL]
    )
    def test_ri_con_receptor_no_ri_da_b(self, receptor):
        ctx = ctx_negocio(CONDICION_RI, receptor)
        assert evaluar_negocio(ctx) == "B", (
            f"R2B: emisor RI con receptor {receptor} debe dar tipo esperado B "
            "(Gherkin E-CLAS-1 · R2B)"
        )
        assert "R2B" in REGISTRO_NEGOCIO.ids_disparados(ctx)

    def test_receptor_desconocido_no_dispara_r2b(self):
        ctx = ctx_negocio(CONDICION_RI, None)
        assert evaluar_negocio(ctx) is None, (
            "Sin condición fiscal del receptor no se puede concluir A ni B (no se inventa)"
        )
        assert REGISTRO_NEGOCIO.ids_disparados(ctx) == [], "No debe disparar ninguna regla de negocio"


class TestR3Exportacion:
    """R3 — receptor con país != Argentina → E, prioridad máxima (pisa R1/R2)."""

    def test_receptor_extranjero_da_e(self):
        ctx = ctx_negocio(CONDICION_RI, CONDICION_RI, pais="Uruguay")
        assert evaluar_negocio(ctx) == "E", (
            "R3: receptor fuera de Argentina debe dar tipo esperado E (Gherkin E-CLAS-1 · R3)"
        )

    def test_r3_pisa_r1(self):
        ctx = ctx_negocio(CONDICION_MONOTRIBUTO, CONDICION_CONSUMIDOR_FINAL, pais="Chile")
        assert evaluar_negocio(ctx) == "E", (
            "R3 tiene prioridad 0: debe pisar a R1 (emisor Monotributo) aunque R1 dispare"
        )
        disparadas = REGISTRO_NEGOCIO.ids_disparados(ctx)
        assert disparadas == ["R3", "R1"], (
            "La traza completa muestra R3 primero (prioridad 0) y luego R1 (prioridad 1)"
        )

    def test_r3_pisa_r2a_y_r2b(self):
        ctx_ri_extranjero = ctx_negocio(CONDICION_RI, CONDICION_RI, pais="Brasil")
        assert evaluar_negocio(ctx_ri_extranjero) == "E", "R3 pisa a R2A"
        ctx_r2b_extranjero = ctx_negocio(CONDICION_RI, CONDICION_CONSUMIDOR_FINAL, pais="Brasil")
        assert evaluar_negocio(ctx_r2b_extranjero) == "E", "R3 pisa a R2B"

    def test_pais_ausente_no_dispara_r3(self):
        ctx = ctx_negocio(CONDICION_RI, CONDICION_RI, pais=None)
        assert "R3" not in REGISTRO_NEGOCIO.ids_disparados(ctx), (
            "Sin país informado no se asume exportación (no se inventa evidencia)"
        )

    def test_pais_argentina_no_dispara_r3(self):
        ctx = ctx_negocio(CONDICION_RI, CONDICION_RI, pais="argentina")
        assert "R3" not in REGISTRO_NEGOCIO.ids_disparados(ctx), (
            "El país Argentina (case-insensitive) no dispara exportación"
        )

    def test_regla_r3_tiene_prioridad_maxima(self):
        prioridad_r3 = next(r.prioridad for r in REGISTRO_NEGOCIO.reglas if r.id == "R3")
        assert prioridad_r3 == min(r.prioridad for r in REGISTRO_NEGOCIO.reglas), (
            "R3 debe ser la regla de mayor prioridad (menor número) del registro de negocio"
        )


# ---------------------------------------------------------------------------
# Registro de lectura (cascada R4 → R5 → R6)
# ---------------------------------------------------------------------------


class TestR4RecuadroVlm:
    """R4 — letra única del recuadro del encabezado leída por el VLM."""

    @pytest.mark.parametrize("letra", ["A", "B", "C", "M", "E"])
    def test_recuadro_devuelve_la_letra(self, letra):
        ctx = ContextoTipoComprobante(letra_recuadro_vlm=letra)
        assert evaluar_lectura(ctx) == letra, (
            f"R4: la letra '{letra}' del recuadro debe ser la letra detectada"
        )
        assert "R4" in REGISTRO_LECTURA.ids_disparados(ctx)

    def test_letra_minuscula_se_normaliza(self):
        ctx = ContextoTipoComprobante(letra_recuadro_vlm="a")
        assert evaluar_lectura(ctx) == "A", "R4 normaliza la letra a mayúscula"

    def test_letra_fuera_de_vocabulario_no_dispara(self):
        ctx = ContextoTipoComprobante(letra_recuadro_vlm="Z")
        assert "R4" not in REGISTRO_LECTURA.ids_disparados(ctx), (
            "Una letra fuera de {A,B,C,M,E} no debe disparar R4 (no se inventa)"
        )
        assert letra_de_recuadro(ctx) is None


class TestR5RegexEncabezado:
    """R5 — regex ``FACTURA\\s+([A-CME])|COMPROBANTE\\s+([A-CME])`` sobre el texto."""

    @pytest.mark.parametrize(
        "texto,esperado",
        [
            ("FACTURA A", "A"),
            ("FACTURA B COD. 006", "B"),
            ("COMPROBANTE C", "C"),
            ("Factura M", "M"),
            ("FACTURA E", "E"),
            ("texto previo COMPROBANTE B texto posterior", "B"),
        ],
    )
    def test_regex_captura_la_letra(self, texto, esperado):
        ctx = ContextoTipoComprobante(texto_encabezado_llm=texto)
        assert evaluar_lectura(ctx) == esperado, (
            f"R5: '{texto}' debe capturar la letra {esperado} (regex del prompt WIP)"
        )
        assert "R5" in REGISTRO_LECTURA.ids_disparados(ctx)

    def test_regex_no_captura_letras_invalidas(self):
        ctx = ContextoTipoComprobante(texto_encabezado_llm="FACTURA X")
        assert "R5" not in REGISTRO_LECTURA.ids_disparados(ctx), (
            "La regex solo admite [A-CME]; 'X' no debe disparar R5"
        )

    def test_regex_sin_patron_no_dispara(self):
        ctx = ContextoTipoComprobante(texto_encabezado_llm="DOCUMENTO NO FISCAL")
        assert letra_de_encabezado(ctx) is None, "Sin patrón no hay letra detectada"

    def test_regex_y_grupo(self):
        # T-305: el patrón pasó a un único grupo (``FACTURA``/``COMPROBANTE``
        # comparten la captura), porque ahora la alternancia cubre la palabra
        # entera y la letra es siempre el grupo 1.
        m = REGEX_LETRA_ENCABEZADO.search("FACTURA B")
        assert m is not None and m.group(1) == "B", (
            "El grupo 1 corresponde a la letra junto a FACTURA"
        )
        m2 = REGEX_LETRA_ENCABEZADO.search("COMPROBANTE E")
        assert m2 is not None and m2.group(1) == "E", (
            "El grupo 1 corresponde a la letra junto a COMPROBANTE"
        )

    def test_regex_no_cruza_el_salto_de_linea(self):
        # Regresión del bug encontrado en T-305: con ``\s+`` (patrón literal del
        # WIP) el salto de línea del markdown de Docling hacía que la letra se
        # capturara de la línea siguiente — ``"FACTURA\n  Código: 1"`` daba ``C``
        # (el ``C`` de "**C**ódigo") sobre dos PDFs reales cuyo encabezado dice
        # ``FACTURA A``. La letra tiene que estar en la **misma línea**.
        assert extraer_letra_encabezado("FACTURA\n  Código: 1") is None
        assert extraer_letra_encabezado("FACTURA\n      C") is None
        assert extraer_letra_encabezado("  A\n  FACTURA\nCOD.01") is None
        # Y sigue funcionando cuando la letra está en la misma línea.
        assert extraer_letra_encabezado("FACTURA A\nCOD. 001") == "A"

    def test_r5_solo_si_r4_no_dio_resultado(self):
        # R4 con letra válida gana; R5 no debe ni dispararse en la cascada.
        ctx = ContextoTipoComprobante(letra_recuadro_vlm="A", texto_encabezado_llm="FACTURA B")
        assert evaluar_lectura(ctx) == "A", "R4 (recuadro) debe ganar sobre R5 (regex)"
        assert "R5" not in REGISTRO_LECTURA.ids_disparados(ctx), (
            "R5 es de segunda instancia: no debe aplicarse si R4 dio resultado"
        )


class TestR6InferenciaPorTotales:
    """R6 — inferencia por ``campos_totales`` con desempate por condición fiscal."""

    def test_discriminado_da_a_candidato(self):
        ctx = ContextoTipoComprobante(campos_totales="discriminado")
        assert evaluar_lectura(ctx) == "A", (
            "R6: IVA discriminado (Neto + IVA separados) → A candidato (Gherkin E-CLAS-1 · R6)"
        )
        assert "R6" in REGISTRO_LECTURA.ids_disparados(ctx)

    def test_subtotal_unico_con_emisor_ri_da_b(self):
        ctx = ContextoTipoComprobante(
            emisor_condicion_fiscal=CONDICION_RI, campos_totales="subtotal_unico"
        )
        assert evaluar_lectura(ctx) == "B", (
            "R6: subtotal único con emisor RI se desempata como B (según R2B)"
        )

    @pytest.mark.parametrize("emisor", [CONDICION_MONOTRIBUTO, CONDICION_EXENTO])
    def test_subtotal_unico_con_emisor_mono_exento_da_c(self, emisor):
        ctx = ContextoTipoComprobante(emisor_condicion_fiscal=emisor, campos_totales="subtotal_unico")
        assert evaluar_lectura(ctx) == "C", (
            f"R6: subtotal único con emisor {emisor} se desempata como C (según R1)"
        )

    def test_subtotal_unico_sin_condicion_no_desempata(self):
        ctx = ContextoTipoComprobante(campos_totales="subtotal_unico")
        assert "R6" in REGISTRO_LECTURA.ids_disparados(ctx), (
            "R6 aplica (hay señal de totales) aunque no pueda resolver la letra"
        )
        assert letra_de_campos_totales(ctx) is None, (
            "Sin condición fiscal del emisor no hay desempate posible (no se inventa)"
        )
        assert evaluar_lectura(ctx) is None, "Sin desempate la letra detectada queda indefinida"

    def test_r6_solo_si_r4_y_r5_fallan(self):
        ctx = ContextoTipoComprobante(
            letra_recuadro_vlm="C", texto_encabezado_llm="FACTURA B", campos_totales="discriminado"
        )
        assert evaluar_lectura(ctx) == "C", "La cascada se detiene en R4"
        assert "R6" not in REGISTRO_LECTURA.ids_disparados(ctx), (
            "R6 es de tercera instancia: no debe aplicarse si R4 o R5 dieron resultado"
        )

    def test_tabla_de_inferencia_es_dato_declarativo(self):
        assert TABLA_INFERENCIA_R6["discriminado"]["*"] == "A", (
            "La tabla de R6 declara discriminado → A para cualquier emisor"
        )
        assert TABLA_INFERENCIA_R6["subtotal_unico"][CONDICION_RI] == "B", (
            "La tabla de R6 declara subtotal único + emisor RI → B"
        )
        assert TABLA_INFERENCIA_R6["subtotal_unico"][CONDICION_EXENTO] == "C"

    def test_campos_totales_desconocido_no_dispara_r6(self):
        ctx = ContextoTipoComprobante(campos_totales="desconocido")
        assert "R6" not in REGISTRO_LECTURA.ids_disparados(ctx), (
            "El valor 'desconocido' no aporta señal: R6 no debe disparar"
        )


# ---------------------------------------------------------------------------
# Registro de conflicto
# ---------------------------------------------------------------------------


class TestR7ConflictoFinanciero:
    """R7 — emisor RI ∧ receptor RI ∧ letra detectada B → alerta."""

    def test_ri_ri_con_letra_b_dispara_alerta(self):
        ctx = ctx_negocio(CONDICION_RI, CONDICION_RI, letra_recuadro_vlm="B")
        disparadas = evaluar_conflicto(ctx)
        assert [r.id for r in disparadas] == ["R7"], (
            "R7 debe dispararse con emisor RI + receptor RI + letra detectada B "
            "(Gherkin E-CLAS-1 · R7)"
        )

    def test_ri_ri_con_letra_a_no_dispara(self):
        ctx = ctx_negocio(CONDICION_RI, CONDICION_RI, letra_recuadro_vlm="A")
        assert evaluar_conflicto(ctx) == [], "Una letra A no es conflicto financiero"

    def test_emisor_no_ri_no_dispara(self):
        ctx = ctx_negocio(CONDICION_MONOTRIBUTO, CONDICION_RI, letra_recuadro_vlm="B")
        assert evaluar_conflicto(ctx) == [], "R7 exige emisor RI"

    def test_receptor_no_ri_no_dispara(self):
        ctx = ctx_negocio(CONDICION_RI, CONDICION_CONSUMIDOR_FINAL, letra_recuadro_vlm="B")
        assert evaluar_conflicto(ctx) == [], "R7 exige receptor RI"

    def test_letra_b_inferida_por_r6_tambien_dispara(self):
        # "Letra detectada" incluye la inferida por campos totales (cascada de lectura).
        ctx = ctx_negocio(CONDICION_RI, CONDICION_RI, campos_totales="subtotal_unico")
        assert [r.id for r in evaluar_conflicto(ctx)] == ["R7"], (
            "La letra B inferida por R6 también constituye conflicto con R2A"
        )

    def test_alerta_trazable_con_mensaje_del_wip(self):
        ctx = ctx_negocio(CONDICION_RI, CONDICION_RI, letra_recuadro_vlm="B")
        regla = evaluar_conflicto(ctx)[0]
        alerta = construir_alerta(regla, ctx)
        assert alerta["disparada"] is True
        assert alerta["regla"] == "R7", "La alerta debe citar el id de la regla (E-CONC-5)"
        assert alerta["mensaje"] == MENSAJE_R7, (
            "El mensaje debe ser el portado literal del prompt WIP §conflicto_auditoria"
        )
        assert alerta["refuerzo_cuit"] is False, (
            "Sin CUIT propio informado el refuerzo queda en False (no es condición obligatoria)"
        )
        assert "R7" in alerta["detalle"], "El detalle de la alerta debe citar la regla"

    def test_refuerzo_cuit_cuando_coincide(self):
        ctx = ContextoTipoComprobante(
            emisor_condicion_fiscal=CONDICION_RI,
            receptor_condicion_fiscal=CONDICION_RI,
            receptor_pais=ARGENTINA,
            letra_recuadro_vlm="B",
            cuit_propio="30-11111111-1",
            receptor_cuit="30-11111111-1",
        )
        alerta = construir_alerta(evaluar_conflicto(ctx)[0], ctx)
        assert alerta["refuerzo_cuit"] is True, (
            "Si el CUIT propio coincide con el del receptor se marca el refuerzo (dato de despliegue)"
        )
        assert alerta["cuit_propio"] == "30-11111111-1", "El CUIT propio se conserva para auditoría"
        assert "30-11111111-1" in alerta["detalle"], (
            "El detalle de la alerta debe mencionar el CUIT como refuerzo cuando coincide"
        )

    def test_cuit_no_es_condicion_obligatoria(self):
        # El Gherkin de E-CLAS-1 no menciona CUIT: R7 debe disparar sin él.
        ctx = ctx_negocio(CONDICION_RI, CONDICION_RI, letra_recuadro_vlm="B")
        assert ctx.cuit_propio is None
        assert evaluar_conflicto(ctx), "R7 no debe exigir cuit_propio (dato de despliegue opcional)"


# ---------------------------------------------------------------------------
# Registro / motor
# ---------------------------------------------------------------------------


class TestRegistrosYMotor:
    """Contratos del motor declarativo (``Rule``/``Registry``, ADR-006)."""

    def test_tres_registros_separados(self):
        assert [r.id for r in REGISTRO_NEGOCIO.reglas] == ["R3", "R1", "R2A", "R2B"], (
            "REGISTRO_NEGOCIO debe contener R3/R1/R2A/R2B ordenadas por prioridad"
        )
        assert [r.id for r in REGISTRO_LECTURA.reglas] == ["R4", "R5", "R6"], (
            "REGISTRO_LECTURA debe contener R4/R5/R6 en cascada"
        )
        assert [r.id for r in REGISTRO_CONFLICTO.reglas] == ["R7"], (
            "REGISTRO_CONFLICTO debe contener R7"
        )

    def test_prioridades_documentadas(self):
        prioridades = {r.id: r.prioridad for r in REGISTRO_NEGOCIO.reglas}
        prioridades.update({r.id: r.prioridad for r in REGISTRO_LECTURA.reglas})
        prioridades.update({r.id: r.prioridad for r in REGISTRO_CONFLICTO.reglas})
        assert prioridades == {
            "R3": 0,
            "R1": 1,
            "R2A": 2,
            "R2B": 2,
            "R4": 10,
            "R5": 11,
            "R6": 12,
            "R7": 20,
        }, "Las prioridades deben ser las decididas en T-301 (negocio < lectura < conflicto)"

    def test_tipos_de_regla(self):
        tipos = {r.id: r.tipo for r in REGISTRO_NEGOCIO.reglas}
        tipos.update({r.id: r.tipo for r in REGISTRO_LECTURA.reglas})
        tipos.update({r.id: r.tipo for r in REGISTRO_CONFLICTO.reglas})
        assert tipos["R4"] == "lectura" and tipos["R5"] == "lectura" and tipos["R6"] == "lectura"
        assert tipos["R7"] == "cruzada", "R7 es una regla cruzada (negocio vs. lectura)"

    def test_detalle_cita_id_y_prompt_fuente(self):
        todas = list(REGISTRO_NEGOCIO.reglas) + list(REGISTRO_LECTURA.reglas) + list(REGISTRO_CONFLICTO.reglas)
        for regla in todas:
            assert regla.detalle, f"{regla.id} debe tener detalle legible para auditoría"
            assert regla.id in regla.detalle, f"El detalle de {regla.id} debe citar su propio id"
            assert "prompt WIP" in regla.detalle, (
                f"El detalle de {regla.id} debe citar el prompt fuente (trazabilidad)"
            )

    def test_construir_registros_devuelve_instancias_nuevas(self):
        registros = construir_registros()
        assert set(registros) == {"negocio", "lectura", "conflicto"}, (
            "construir_registros() debe devolver un dict con las claves 'negocio', 'lectura' y 'conflicto'"
        )
        negocio, lectura, conflicto = (
            registros["negocio"],
            registros["lectura"],
            registros["conflicto"],
        )
        assert negocio is not REGISTRO_NEGOCIO, "construir_registros() debe dar instancias nuevas"
        assert lectura is not REGISTRO_LECTURA, "construir_registros() debe dar instancias nuevas"
        assert conflicto is not REGISTRO_CONFLICTO, "construir_registros() debe dar instancias nuevas"
        assert [r.id for r in negocio.reglas] == ["R3", "R1", "R2A", "R2B"]
        assert [r.id for r in lectura.reglas] == ["R4", "R5", "R6"]
        assert [r.id for r in conflicto.reglas] == ["R7"]

    def test_condiciones_son_puras(self):
        # Las condiciones no mutan el contexto (frozen) y son deterministas.
        ctx = ctx_negocio(CONDICION_RI, CONDICION_RI, letra_recuadro_vlm="A")
        for regla in REGISTRO_NEGOCIO.reglas + REGISTRO_LECTURA.reglas + REGISTRO_CONFLICTO.reglas:
            primera = regla.evaluar(ctx)
            segunda = regla.evaluar(ctx)
            assert primera == segunda, f"La condición de {regla.id} debe ser determinista"


# ---------------------------------------------------------------------------
# Combinaciones y orquestación (clasificar_tipo_comprobante)
# ---------------------------------------------------------------------------


class TestCruceNegocioVsDocumento:
    """Cruce de ``tipo_esperado_por_negocio`` con ``tipo_detectado_por_documento``."""

    def _res(self, ctx, **kw) -> TipoComprobanteResult:
        return clasificar_tipo_comprobante(ctx, **kw)

    def test_r3_pisa_todo_y_devuelve_e_alta(self):
        ctx = ctx_negocio(CONDICION_RI, CONDICION_RI, pais="Chile", letra_recuadro_vlm="B")
        res = self._res(ctx)
        assert res.letra == "E", "R3 (exportación) pisa el cruce y resuelve E"
        assert res.certeza == "alta", "R3 es concluyente: certeza alta (no hay cruce)"
        assert res.reglas_aplicadas == ["R3"], "Solo R3 resuelve: la traza refleja la regla resolutoria"
        assert res.alertas == [], "Sin cruce no hay alertas"
        assert res.tipo_detectado_por_documento is None, "R3 no evalúa la lectura"

    def test_r2a_mas_r4_b_discrepancia_y_r7(self):
        # Caso del ejemplo del prompt WIP: esperado A (R2A), recuadro B (R4) → R7.
        ctx = ctx_negocio(CONDICION_RI, CONDICION_RI, letra_recuadro_vlm="B")
        res = self._res(ctx)
        assert res.tipo_esperado_por_negocio == "A", "R2A: el negocio espera A"
        assert res.tipo_detectado_por_documento == "B", "R4: el documento muestra B"
        assert res.letra == "B", "Con preferencia_letra='documento' (default) manda la letra detectada"
        assert res.certeza == "baja", "Una discrepancia no puede tener certeza alta"
        assert res.coincide_negocio_vs_documento is False
        assert [a["regla"] for a in res.alertas] == ["R7"], "Debe dispararse la alerta R7"
        assert "R7" in res.reglas_aplicadas
        assert set(res.reglas_aplicadas) == {"R2A", "R4", "R7"}, (
            "La traza debe citar negocio (R2A), lectura (R4) y conflicto (R7)"
        )
        assert res.candidatos_descartados == ["A"], (
            "Con preferencia 'documento' la letra final es B: la candidata esperada A "
            "queda descartada y trazada (sin inventar letras del vocabulario completo)"
        )
        assert res.candidatos_restantes == ["B"], (
            "La única candidata que sobrevive es la letra final (B)"
        )

    def test_preferencia_negocio_cambia_la_letra(self):
        ctx = ctx_negocio(CONDICION_RI, CONDICION_RI, letra_recuadro_vlm="B")
        res = self._res(ctx, preferencia_letra="negocio")
        assert res.letra == "A", (
            "Con preferencia_letra='negocio' la letra final es la esperada por negocio "
            "(semántica del WIP: 'la ley manda sobre el papel')"
        )
        assert res.certeza == "baja"
        assert [a["regla"] for a in res.alertas] == ["R7"], "La discrepancia se alerta igual"
        assert res.candidatos_descartados == ["B"], (
            "Al elegir A, la letra detectada B queda descartada (trazabilidad del descarte)"
        )
        assert res.candidatos_restantes == ["A"]

    def test_preferencia_invalida_lanza(self):
        with pytest.raises(ValueError, match="preferencia_letra"):
            self._res(ctx_negocio(CONDICION_RI, CONDICION_RI), preferencia_letra="otra")

    def test_coincidencia_negocio_documento_da_alta(self):
        ctx = ctx_negocio(CONDICION_RI, CONDICION_RI, letra_recuadro_vlm="A")
        res = self._res(ctx)
        assert res.letra == "A" and res.certeza == "alta", (
            "Cuando negocio y documento coinciden la letra es concluyente (certeza alta)"
        )
        assert res.coincide_negocio_vs_documento is True
        assert res.alertas == [], "Sin discrepancia no hay alertas"
        assert res.campos_desconocidos == [], "Con coincidencia no hay campos desconocidos"

    def test_r4_gana_sobre_r5(self):
        ctx = ctx_negocio(CONDICION_RI, CONDICION_RI, letra_recuadro_vlm="A", texto_encabezado_llm="FACTURA B")
        res = self._res(ctx)
        assert res.tipo_detectado_por_documento == "A", (
            "R4 (recuadro VLM) debe ganar sobre R5 (regex del texto)"
        )
        assert res.reglas_aplicadas == ["R2A", "R4"], "Solo se registra la regla de lectura que resolvió"

    def test_r5_solo_cuando_r4_no_dio(self):
        ctx = ctx_negocio(CONDICION_RI, CONDICION_RI, texto_encabezado_llm="FACTURA A")
        res = self._res(ctx)
        assert res.tipo_detectado_por_documento == "A"
        assert res.reglas_aplicadas == ["R2A", "R5"], "Sin recuadro, resuelve la regex (R5)"

    def test_r6_desempate_por_condicion_fiscal_ri(self):
        ctx = ctx_negocio(CONDICION_RI, CONDICION_CONSUMIDOR_FINAL, campos_totales="subtotal_unico")
        res = self._res(ctx)
        assert res.tipo_esperado_por_negocio == "B", "R2B: negocio espera B"
        assert res.tipo_detectado_por_documento == "B", "R6 desempata B (emisor RI)"
        assert res.letra == "B" and res.certeza == "alta", "Coincidencia → certeza alta"

    def test_r6_desempate_por_condicion_fiscal_monotributo(self):
        ctx = ctx_negocio(CONDICION_MONOTRIBUTO, CONDICION_CONSUMIDOR_FINAL, campos_totales="subtotal_unico")
        res = self._res(ctx)
        assert res.tipo_detectado_por_documento == "C", "R6 desempata C (emisor Monotributo)"
        assert res.letra == "C" and res.certeza == "alta"

    def test_r6_sin_desempate_cae_en_solo_negocio(self):
        ctx = ctx_negocio(CONDICION_RI, CONDICION_RI, campos_totales="subtotal_unico")
        # Emisor RI → R6 desempata B; negocio espera A → discrepancia con R7.
        res = self._res(ctx)
        assert res.tipo_detectado_por_documento == "B"
        assert res.letra == "B" and res.certeza == "baja"
        assert [a["regla"] for a in res.alertas] == ["R7"]


class TestCasosParcialesYDesconocidos:
    """Solo negocio, solo documento o nada → certeza baja + campos desconocidos."""

    def test_solo_negocio(self):
        ctx = ctx_negocio(CONDICION_RI, CONDICION_RI)  # sin evidencia de lectura
        res = clasificar_tipo_comprobante(ctx)
        assert res.letra == "A", "Con solo negocio se conserva la letra esperada"
        assert res.certeza == "baja", "Sin evidencia de lectura no puede haber certeza alta"
        assert res.tipo_detectado_por_documento is None
        assert "evidencia_lectura" in res.campos_desconocidos, (
            "Debe reportarse la falta de evidencia de lectura"
        )
        assert res.alertas == [], "Sin letra detectada no hay conflicto"
        assert res.candidatos_restantes == ["A"] and res.candidatos_descartados == [], (
            "Con solo negocio no hay letra detectada que descartar: el candidato es la esperada"
        )

    def test_solo_documento(self):
        ctx = ContextoTipoComprobante(letra_recuadro_vlm="C")
        res = clasificar_tipo_comprobante(ctx)
        assert res.letra == "C", "Con solo lectura se conserva la letra detectada"
        assert res.certeza == "baja"
        assert res.tipo_esperado_por_negocio is None
        assert "emisor.condicion_fiscal" in res.campos_desconocidos
        assert "receptor.condicion_fiscal" in res.campos_desconocidos

    def test_nada(self):
        res = clasificar_tipo_comprobante(ContextoTipoComprobante())
        assert res.letra is None, "Sin evidencia no se concluye letra (no se inventa)"
        assert res.certeza == "baja"
        assert res.origen == "programa", "El origen es el motor determinístico"
        assert res.candidatos_restantes == [], (
            "Sin letras de reglas disparadas no se inventan candidatos del vocabulario completo"
        )
        assert res.candidatos_descartados == []
        assert res.reglas_aplicadas == []
        for campo in ("emisor.condicion_fiscal", "receptor.condicion_fiscal", "evidencia_lectura"):
            assert campo in res.campos_desconocidos, f"Debe reportarse '{campo}' como desconocido"

    def test_traza_de_reglas_disparadas_en_detalle(self):
        # R3 resuelve, pero el detalle conserva la traza completa (R1 también disparó).
        ctx = ctx_negocio(CONDICION_MONOTRIBUTO, CONDICION_CONSUMIDOR_FINAL, pais="Chile")
        res = clasificar_tipo_comprobante(ctx)
        assert res.reglas_aplicadas == ["R3"], "Solo la regla resolutoria va a reglas_aplicadas"
        assert res.detalle["reglas_disparadas"]["negocio"] == ["R3", "R1"], (
            "El detalle debe conservar la traza completa (incluye la regla pisada R1)"
        )
        assert res.detalle["id_requisito"].startswith("T-301"), (
            "El detalle debe citar el requisito T-301 (trazabilidad E-CONC-5)"
        )

    def test_condiciones_no_reconocidas_bajan_certeza(self):
        ctx = ContextoTipoComprobante(
            emisor_condicion_fiscal="Agente de retención",
            receptor_condicion_fiscal="Agente de retención",
            receptor_pais=ARGENTINA,
            letra_recuadro_vlm="A",
        )
        res = clasificar_tipo_comprobante(ctx)
        assert res.certeza == "baja", "Una condición fiscal no reconocida no permite certeza alta"
        assert res.letra == "A", "La letra detectada se conserva"
        assert "emisor.condicion_fiscal" in res.campos_desconocidos
        assert "receptor.condicion_fiscal" in res.campos_desconocidos

    def test_condiciones_desconocidas_sin_lectura_dan_none(self):
        # Sin condición fiscal reconocida y sin lectura no hay letra posible (no se inventa).
        ctx = ContextoTipoComprobante(
            emisor_condicion_fiscal="desconocido",
            receptor_condicion_fiscal=None,
        )
        res = clasificar_tipo_comprobante(ctx)
        assert res.letra is None, (
            "Con condiciones fiscales desconocidas y sin lectura la letra debe ser None "
            "(prompt WIP: 'No inventes datos')"
        )
        assert res.certeza == "baja", "Sin evidencia concluyente la certeza es baja"
        assert res.tipo_esperado_por_negocio is None, "Ninguna regla de negocio debe resolver"
        assert res.reglas_aplicadas == [], "Sin reglas disparadas la traza queda vacía"
        assert "emisor.condicion_fiscal" in res.campos_desconocidos
        assert "receptor.condicion_fiscal" in res.campos_desconocidos
        assert "evidencia_lectura" in res.campos_desconocidos

    def test_acepta_mapping_anidado_del_wip(self):
        # Compatibilidad: el llamador puede pasar el shape del WIP sin construir el dataclass.
        res = clasificar_tipo_comprobante(
            {
                "emisor": {"condicion_fiscal": "Responsable Inscripto"},
                "receptor": {"condicion_fiscal": "Responsable Inscripto", "pais": "Argentina"},
                "ocr": {"letra_detectada_cabecera": "A"},
            }
        )
        assert res.letra == "A" and res.certeza == "alta", (
            "El mapping anidado del WIP debe construir el contexto y clasificar igual"
        )

    def test_rechaza_tipo_invalido(self):
        with pytest.raises(TypeError, match="ContextoTipoComprobante"):
            clasificar_tipo_comprobante("no soy un contexto")  # type: ignore[arg-type]

    def test_trazabilidad_del_detalle(self):
        ctx = ctx_negocio(CONDICION_RI, CONDICION_RI, letra_recuadro_vlm="A")
        res = clasificar_tipo_comprobante(ctx)
        assert "contexto" in res.detalle, "El detalle debe incluir el contexto de entrada (auditoría)"
        assert res.detalle["contexto"]["letra_recuadro_vlm"] == "A"
        assert "criterio" in res.detalle, "El detalle debe explicar el criterio de decisión"

    def test_contrato_f0_sigue_construible(self):
        # Regla dura F3-subplan §4: el contrato congelado no se rompe.
        resultado = TipoComprobanteResult(letra="A", certeza="alta", origen="programa")
        assert resultado.candidatos_descartados == []
        assert resultado.detalle == {}, "Los campos nuevos tienen default (aditivos)"

    def test_no_contamina_descartados_y_restantes(self):
        # Blindaje mínimo (ADR-008): un candidato no puede estar en ambas listas.
        for ctx in (
            ContextoTipoComprobante(),
            ctx_negocio(CONDICION_RI, CONDICION_RI, letra_recuadro_vlm="A"),
            ctx_negocio(CONDICION_RI, CONDICION_RI, letra_recuadro_vlm="B"),
            ContextoTipoComprobante(letra_recuadro_vlm="C"),
        ):
            res = clasificar_tipo_comprobante(ctx)
            cruce = set(res.candidatos_descartados) & set(res.candidatos_restantes)
            assert not cruce, f"Un candidato no puede ser descartado y restante a la vez: {cruce}"

    def test_origen_siempre_programa(self):
        for ctx in (
            ContextoTipoComprobante(),
            ctx_negocio(CONDICION_RI, CONDICION_RI, letra_recuadro_vlm="A"),
            ctx_negocio(CONDICION_RI, CONDICION_RI, letra_recuadro_vlm="B"),
        ):
            assert clasificar_tipo_comprobante(ctx).origen == "programa", (
                "La decisión la toma el motor determinístico (ADR-006)"
            )

"""El comprobante de un proveedor **internacional** (``INTERNACIONAL``).

**Origen**: el hallazgo sobre ``22f0e9af-6bd6-4882-bb69-0991ef3db3ed.pdf`` — una
``INVOICE`` de *Miami IT Smart Tech LLC* (Florida, EE.UU.)— destapó que el sistema
no tenía dónde declarar un comprobante emitido por un proveedor de otro país. El
modelo improvisó de **tres** formas distintas sobre el corpus real: ``null`` (el
dato se perdía), texto libre (``"INVOICE (no es comprobante AFIP…)"``, que el
vocabulario cerrado marca como inválido) y ``"E"``.

El eje que se agregó es **ortogonal** a la letra ``E`` y por eso convive con ella:

- ``E`` — **Factura E argentina**: un emisor argentino que le factura *al
exterior* (R3: el *receptor* está fuera del país).
- ``INTERNACIONAL`` — un proveedor de otro país que nos factura a **nosotros**
(el *emisor* está fuera del país).

Confundirlos haría que el mismo campo signifique dos cosas a la vez.

⚠️ **Por qué ``INTERNACIONAL`` y no ``EXTERIOR``**: el dominio ya usa "exterior"
para la exportación (la Factura E). Un valor con ese nombre ponía los dos
significados **opuestos** en la misma palabra: "comprobante del exterior"
describía tanto el caso de ``E`` como el que se quería marcar. El renombre deja
"exterior" para el eje de R3.

Qué se protege acá
------------------
1. **No es una letra impresa**, así que no se le exige sostén literal en el
   fragmento (``RAW_SUSTENTO``). Sin esta excepción, **todos** los comprobantes
   internacionales quedaban marcados como "lectura sin prueba": el mismo falso
   positivo que T-403 ya había combatido para los campos de formato volátil.
   El valor **sigue** sujeto a la regla de vocabulario (lo que detecta un valor
   inventado).
2. **Llega al motor**: ``LETRAS_COMPROBANTE`` lo incluye, así que F5 no lo
   descarta y el caso no cae en ``CRUZ_2`` ("sin letra") → revisión perpetua.
3. **No se busca en el padrón ARCA**: el emisor no es un contribuyente argentino,
   así que una consulta al WSCDC no tiene sujeto. El gap se conserva (el dato
   sigue faltando) pero deja de ser buscable.
4. **No colisiona con ``E``**: la regla 1 del lab no los hace equivalentes.
5. **La palabra "exterior" no es un valor**: nombra el eje de R3, no este.
"""

from __future__ import annotations

from voucherflow.llm.evaluador import diff_deterministico
from voucherflow.rules.contexto import (
    INTERNACIONAL,
    LETRAS_COMPROBANTE,
    normalizar_letra,
)
from voucherflow.rules.contexto_conclusion import ContextoConclusion
from voucherflow.rules.gaps import detectar_gaps, es_comprobante_internacional
from voucherflow.rules.raw import CampoDeclarado, evaluar_raw

VOCABULARIO = ("A", "B", "C", "M", "E", "090", "099", INTERNACIONAL)

#: Fragmento realista: lo que el modelo ve en una factura de Miami.
FRAGMENTO_INVOICE = (
    "INVOICE de Miami IT Smart Tech LLC, Waterford District Dr. 5200 Suite 120, "
    "Miami, Florida, EE.UU."
)


def _campo(valor: str, fragmento: str = FRAGMENTO_INVOICE) -> CampoDeclarado:
    """``CampoDeclarado`` de ``tipo_comprobante`` con el contrato de F4 (T-403)."""
    from voucherflow.extraction.evidencia import _sin_sosten_literal_de

    return CampoDeclarado(
        campo="tipo_comprobante",
        valor=valor,
        fragmento=fragmento,
        vocabulario=VOCABULARIO,
        normalizador=lambda v: str(v).strip().upper(),
        sin_sosten_literal=_sin_sosten_literal_de("tipo_comprobante"),
    )


# ---------------------------------------------------------------------------
# 1. El vocabulario
# ---------------------------------------------------------------------------


class TestVocabulario:
    def test_es_una_letra_del_motor(self):
        # Si no estuviera, F5 (`_es_letra_valida`) lo descartaría y el caso
        # quedaría en revisión para siempre por "sin letra".
        assert INTERNACIONAL in LETRAS_COMPROBANTE

    def test_se_canoniza_desde_cualquier_caja(self):
        assert normalizar_letra("internacional") == INTERNACIONAL
        assert normalizar_letra("INTERNACIONAL") == INTERNACIONAL
        assert normalizar_letra(" Internacional ") == INTERNACIONAL

    def test_la_palabra_exterior_no_es_un_valor(self):
        """⚠️ El conflicto de términos que motivó el renombre.

        El dominio usa "exterior" para la **exportación** (``E``: se factura *al
        exterior*). Si ``"exterior"`` fuera además un valor del vocabulario, la
        misma palabra nombraría los dos casos **opuestos**, que es exactamente lo
        que el renombre vino a evitar.
        """
        assert normalizar_letra("exterior") is None
        assert normalizar_letra("EXTERIOR") is None

    def test_no_reemplaza_a_la_factura_e(self):
        """Son ejes distintos y los dos tienen que seguir existiendo.

        ``E`` es una Factura E argentina (emisor argentino, receptor de afuera);
        ``INTERNACIONAL`` es un proveedor de afuera facturándonos. Si
        ``INTERNACIONAL`` hubiera reemplazado a ``E``, R3 (exportación) habría
        quedado sin salida.
        """
        assert normalizar_letra("E") == "E"
        assert normalizar_letra(INTERNACIONAL) == INTERNACIONAL
        assert "E" in LETRAS_COMPROBANTE


# ---------------------------------------------------------------------------
# 2. El sostén: es una inferencia, no una palabra impresa
# ---------------------------------------------------------------------------


class TestSosten:
    """``INTERNACIONAL`` es una **inferencia**; no está impreso en el papel."""

    def test_no_exige_la_palabra_en_el_fragmento(self):
        resultado = evaluar_raw("vlm", _campo(INTERNACIONAL))
        assert resultado.valida is True
        assert "RAW_SUSTENTO" not in resultado.reglas_aplicadas, (
            "Llenaría de debilidades espurias a todos los comprobantes "
            "internacionales: ningún papel imprime esa palabra."
        )
        assert resultado.gravedad.value == "valida"

    def test_un_texto_en_ingles_con_letras_no_contradice(self):
        """El fragmento de una INVOICE puede contener 'A'/'B'/'E' en inglés.

        Sin el corte de ``RAW_CONTRADICCION``, la "A" de "TAX INVOICE A" se leía
        como una contradicción del comprobante internacional.
        """
        resultado = evaluar_raw(
            "vlm", _campo(INTERNACIONAL, "TAX INVOICE A de Miami IT Smart Tech LLC")
        )
        assert resultado.valida is True
        assert "RAW_CONTRADICCION" not in resultado.reglas_aplicadas

    def test_una_letra_inventada_sigue_marcada(self):
        """La excepción NO afloja el control: una letra sin sostén se reporta."""
        resultado = evaluar_raw(
            "vlm", _campo("A", "FACTURA emitida en Miami, Florida")
        )
        assert "RAW_SUSTENTO" in resultado.reglas_aplicadas
        assert resultado.valida is True, (
            "Es 'dudosa', no 'invalida': el dato sirve como indicio"
        )

    def test_un_valor_fuera_del_vocabulario_sigue_siendo_invalido(self):
        resultado = evaluar_raw("vlm", _campo("Z"))
        assert resultado.valida is False
        assert "RAW_VOCABULARIO" in resultado.reglas_aplicadas

    def test_un_valor_no_hashable_no_rompe_la_pasada_raw(self):
        """Regresión: el modo genérico (``kvg``) declara listas.

        ``lista in frozenset`` levanta ``TypeError``, y sin el guard ese error
        tumbaba **toda** la pasada raw de la fuente, no solo la evaluación de ese
        campo.
        """
        campo = CampoDeclarado(
            campo="productos",
            valor=["café x1 - 1000"],
            fragmento="Ítems",
            vocabulario=VOCABULARIO,
            sin_sosten_literal=frozenset({INTERNACIONAL}),
        )
        evaluar_raw("llm", campo)  # no debe lanzar


# ---------------------------------------------------------------------------
# 3. El diff del lab: no se confunde con E
# ---------------------------------------------------------------------------


class TestDiffDelLab:
    """La regla 1 del prompt, en código (``evaluador``)."""

    def test_internacional_contra_internacional_coincide(self):
        r = diff_deterministico(
            {"tipo_comprobante": "INTERNACIONAL"},
            {"tipo_comprobante": "INTERNACIONAL"},
        )
        assert r["campos"]["tipo_comprobante"]["coincide"] is True

    def test_internacional_contra_una_letra_discrepa(self):
        r = diff_deterministico(
            {"tipo_comprobante": "INTERNACIONAL"}, {"tipo_comprobante": "A"}
        )
        assert r["campos"]["tipo_comprobante"]["coincide"] is False
        assert "tipo_comprobante" in r["discrepancias_criticas"]

    def test_internacional_no_es_equivalente_a_la_factura_e(self):
        """El error más probable de una regla escrita de más."""
        r = diff_deterministico(
            {"tipo_comprobante": "INTERNACIONAL"}, {"tipo_comprobante": "E"}
        )
        assert r["campos"]["tipo_comprobante"]["coincide"] is False, (
            "'E' es una Factura E argentina (emisor argentino al exterior); "
            "'INTERNACIONAL' es un proveedor de afuera facturándonos. Son "
            "opuestos."
        )
        assert "tipo_comprobante" in r["discrepancias_criticas"]

    def test_los_tiques_siguen_siendo_indistintos(self):
        # La regla de 090/099 no se tocó al agregar INTERNACIONAL.
        r = diff_deterministico(
            {"tipo_comprobante": "090"}, {"tipo_comprobante": "099"}
        )
        assert r["campos"]["tipo_comprobante"]["coincide"] is True


# ---------------------------------------------------------------------------
# 4. F5: no se consulta el padrón argentino
# ---------------------------------------------------------------------------


def _contexto(letra: str | None) -> ContextoConclusion:
    """Contexto de conclusión mínimo con la letra vigente indicada."""
    return ContextoConclusion(
        letra=letra,
        campos_ausentes=["nro_comprobante", "fecha_emision"],
    )


class TestGaps:
    """El padrón ARCA/WSCDC no puede constatar un comprobante internacional."""

    def test_el_comprobante_internacional_no_se_busca_en_el_padron(self):
        deteccion = detectar_gaps(_contexto(INTERNACIONAL))
        assert deteccion.gaps, "los campos ausentes siguen reportándose"
        assert not deteccion.buscables, (
            "Un emisor de otro país no es un contribuyente argentino: la "
            "consulta al WSCDC no tiene sujeto."
        )

    def test_un_comprobante_argentino_si_se_busca(self):
        """El corte es específico del comprobante internacional, no una
        desactivación general."""
        deteccion = detectar_gaps(_contexto("A"))
        assert deteccion.buscables, (
            "Una Factura A con campos ausentes sí se puede constatar en el padrón"
        )

    def test_los_gaps_internacionales_conservan_su_criticidad(self):
        deteccion = detectar_gaps(_contexto(INTERNACIONAL))
        bloqueantes = {gap.campo for gap in deteccion.bloqueantes}
        assert "nro_comprobante" in bloqueantes, (
            "No se puede volver no bloqueante un campo crítico solo porque no "
            "sea buscable: siguen faltando datos para concluir."
        )

    def test_el_motivo_explica_por_que_no_se_busca(self):
        deteccion = detectar_gaps(_contexto(INTERNACIONAL))
        objetivo = deteccion.gaps[0].objetivo
        assert "internacional" in objetivo.lower()

    def test_el_predicado_del_comprobante_internacional(self):
        assert es_comprobante_internacional(_contexto(INTERNACIONAL)) is True
        assert es_comprobante_internacional(_contexto("internacional")) is True
        assert es_comprobante_internacional(_contexto("A")) is False
        assert es_comprobante_internacional(_contexto("E")) is False, (
            "La Factura E argentina SÍ se constata en el padrón"
        )
        assert es_comprobante_internacional(_contexto(None)) is False
        assert es_comprobante_internacional(_contexto("exterior")) is False, (
            "'exterior' ya no es un valor del vocabulario"
        )


# ---------------------------------------------------------------------------
# 5. El código AFIP: no se inventa
# ---------------------------------------------------------------------------


class TestCodigoAfip:
    def test_no_tiene_codigo_afip(self):
        """No está en el padrón: inventarle un ``CbteTipo`` sería peor que fallar."""
        import pytest

        from voucherflow.models.arca import ArcaClient

        with pytest.raises(ValueError, match="código AFIP"):
            ArcaClient._tipo_afip(INTERNACIONAL)

    def test_las_letras_argentinas_si_lo_tienen(self):
        from voucherflow.models.arca import ArcaClient

        assert ArcaClient._tipo_afip("A") == 1
        assert ArcaClient._tipo_afip("E") == 19


# ---------------------------------------------------------------------------
# 6. La conclusión: el comprobante internacional SÍ puede aprobarse
# ---------------------------------------------------------------------------


def _conclusion(letra: str, contexto_tipo):
    """Corre la pasada 2 (T-501) con los campos críticos completos."""
    from voucherflow.rules.cruzadas import evaluar_cruzadas

    return evaluar_cruzadas(
        ContextoConclusion(
            letra=letra,
            valores={
                "tipo_comprobante": letra,
                "nro_comprobante": "Z202606170449",
                "fecha_emision": "2026-06-17",
                "importe_total_facturado": "550.00",
            },
            campos_ausentes=[],
            contexto_tipo=contexto_tipo,
        )
    )


class TestConclusion:
    """Decisión de negocio: un servicio internacional **se reembolsa**.

    Antes de ``INTERNACIONAL``, un comprobante de un proveedor de afuera quedaba en
    ``null`` → ``CRUZ_2`` ("sin letra") → revisión humana **siempre**, incluso
    cuando la lectura era perfecta. Con la marca en el vocabulario el caso
    concluye; lo que queda es la decisión (explícita acá) de que eso es lo que
    se quiere.
    """

    def test_con_la_condicion_del_emisor_desconocida_concluye(self):
        """El caso **real**: ARCA no puede resolver la condición de un emisor
        extranjero, así que llega como ``None`` y el negocio no espera letra."""
        from voucherflow.rules.contexto import ContextoTipoComprobante

        caso = _conclusion(
            INTERNACIONAL,
            ContextoTipoComprobante(
                receptor_condicion_fiscal="Responsable Inscripto",
                receptor_pais="Argentina",
            ),
        )
        assert caso.concluye is True
        assert caso.estado == "aprobado"
        assert not caso.alertas, (
            "Sin una letra argentina esperada no hay conflicto que reportar"
        )

    def test_una_condicion_argentina_inventada_no_manda_a_revision(self):
        """⚠️ El límite de la marca: ``INTERNACIONAL`` no participa del cruce R1-R3.

        Si el emisor tuviera una condición fiscal argentina (dato que **no**
        puede venir del documento ni del padrón para un emisor de afuera), el
        negocio esperaría A/B/C y la letra ``INTERNACIONAL`` no coincide con ninguna:
        ``CRUZ_5`` lo trata como conflicto y manda a revisión. Es el
        comportamiento conservador correcto —el sistema no afirma que esté bien—
        pero conviene que esté **declarado**: no es un caso alcanzable desde el
        pipeline real, y si alguna vez lo fuera, es mejor una revisión de más
        que una aprobación silenciosa.
        """
        from voucherflow.rules.contexto import CONDICION_RI, ContextoTipoComprobante

        caso = _conclusion(
            INTERNACIONAL,
            ContextoTipoComprobante(
                emisor_condicion_fiscal=CONDICION_RI,
                receptor_condicion_fiscal=CONDICION_RI,
            ),
        )
        assert caso.concluye is False
        assert caso.estado == "revision"

    def test_la_factura_e_argentina_sigue_aprobando_por_r3(self):
        """El otro eje: emisor argentino facturando al exterior (R3)."""
        from voucherflow.rules.contexto import ContextoTipoComprobante

        caso = _conclusion("E", ContextoTipoComprobante(receptor_pais="Chile"))
        assert caso.concluye is True
        assert caso.estado == "aprobado", (
            "R3 resuelve E sin cruzar con la lectura: es la exportación "
            "argentina, un caso distinto del proveedor internacional"
        )

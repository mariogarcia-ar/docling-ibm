"""Derivación de evidencia (``schemas.evidence.derivar_evidencia``).

⚠️ **Este helper existe porque el mismo patrón estaba escrito cinco veces**:
`api.py`, `cli/main.py` y tres sitios de `conclusion/engine.py` construían un
``CombinedEvidence`` copiando el anterior y ampliando su traza, cada uno con su
propio ``trazabilidad = dict(evidencia.trazabilidad)`` y su propio
``documento_id=…, campos=dict(…), decision=…, trazabilidad=…``.

No es cosmético: ese patrón **pierde evidencia** si alguna copia se olvida de
partir de la traza anterior — la etapa previa desaparece del registro y el caso
deja de ser auditable. Y son las etapas de las que depende la conclusión.

Los tests de cada ruta ya existen (`test_cli_t601.py`, `test_conclusion_*`). Acá
se fija el **contrato del helper**, incluida la parte que es fácil de romper: la
diferencia entre *no pasar* ``decision`` y pasar ``decision=None``.
"""

from __future__ import annotations

import pytest

from voucherflow.schemas.evidence import (
    CampoCombinado,
    CombinedEvidence,
    Decision,
    Fuente,
    derivar_evidencia,
)


def _decision() -> Decision:
    return Decision(concluye=True, certeza="alta", origen="programa")


def _base(**kwargs) -> CombinedEvidence:
    campos = kwargs.pop("campos", None)
    if campos is None:
        campos = {"tipo_comprobante": CampoCombinado(valor="A", fuente=Fuente.vlm)}
    return CombinedEvidence(
        documento_id=kwargs.pop("documento_id", "doc-1"),
        campos=campos,
        decision=kwargs.pop("decision", None),
        trazabilidad=kwargs.pop("trazabilidad", {"etapa_previa": 1}),
    )


class TestAmpliaLaTraza:
    def test_agrega_una_clave(self):
        r = derivar_evidencia(_base(), gaps={"n": 1})
        assert r.trazabilidad == {"etapa_previa": 1, "gaps": {"n": 1}}

    def test_agrega_varias_claves(self):
        r = derivar_evidencia(_base(), gaps={"n": 1}, busqueda={"b": 2})
        assert r.trazabilidad["gaps"] == {"n": 1}
        assert r.trazabilidad["busqueda"] == {"b": 2}

    def test_conserva_la_traza_previa(self):
        """⚠️ Lo que una copia manual puede perder: la evidencia de la etapa anterior."""
        r = derivar_evidencia(_base(trazabilidad={"a": 1, "b": 2}), c=3)
        assert r.trazabilidad == {"a": 1, "b": 2, "c": 3}

    def test_una_clave_repetida_se_pisa(self):
        """Mismo comportamiento que un ``dict.update``: la nueva gana."""
        r = derivar_evidencia(_base(trazabilidad={"etapa_previa": "viejo"}), etapa_previa="nuevo")
        assert r.trazabilidad["etapa_previa"] == "nuevo"

    def test_sin_anotaciones_solo_copia(self):
        r = derivar_evidencia(_base(trazabilidad={"x": 1}))
        assert r.trazabilidad == {"x": 1}

    def test_no_muta_la_evidencia_original(self):
        original = _base(trazabilidad={"x": 1})
        derivar_evidencia(original, nueva=2)
        assert original.trazabilidad == {"x": 1}

    def test_la_traza_nueva_no_comparte_el_dict(self):
        """Si compartiera el dict, anotar contaminaría la evidencia original."""
        original = _base(trazabilidad={"x": 1})
        r = derivar_evidencia(original, nueva=2)
        assert r.trazabilidad is not original.trazabilidad


class TestMismoDocumento:
    def test_conserva_el_documento_id(self):
        assert derivar_evidencia(_base(documento_id="abc"), x=1).documento_id == "abc"

    def test_no_se_puede_cambiar_el_documento(self):
        """No es parámetro: permitirlo abriría la puerta a mezclar casos.

        La firma solo acepta ``decision``, ``campos`` y las anotaciones de traza.
        Un ``documento_id`` de más cae en ``**anotaciones`` y termina como una
        clave de la traza, no como el id — que es el comportamiento seguro.
        """
        r = derivar_evidencia(_base(documento_id="abc"), x=1)
        assert r.documento_id == "abc"


class TestDecision:
    """La parte sutil: omitirla conserva el veredicto; pasar ``None`` lo limpia."""

    def test_omitir_la_conserva(self):
        """Una anotación de traza no cambia el veredicto."""
        base = _base(decision=_decision())
        assert derivar_evidencia(base, x=1).decision is base.decision

    def test_pasar_none_la_limpia(self):
        """El caso real: se va a re-concluir, el veredicto anterior ya no rige."""
        base = _base(decision=_decision())
        assert derivar_evidencia(base, decision=None, x=1).decision is None

    def test_omitir_y_pasar_none_son_distintos(self):
        """⚠️ El test que justifica el centinela `_SIN_CAMBIO`.

        Sin él, ``if decision is None: conservar`` haría indistinguibles los dos
        casos y ``_fusionar_evidencia`` (que limpia el veredicto) dejaría pegado
        el de la etapa anterior.
        """
        base = _base(decision=_decision())
        conserva = derivar_evidencia(base, x=1).decision
        limpia = derivar_evidencia(base, decision=None, x=1).decision
        assert conserva is not None
        assert limpia is None

    def test_puede_reemplazarla(self):
        nueva = Decision(concluye=False, certeza="baja", origen="agente_ia")
        r = derivar_evidencia(_base(decision=_decision()), decision=nueva, x=1)
        assert r.decision is nueva


class TestCampos:
    def test_por_defecto_los_copia(self):
        base = _base()
        r = derivar_evidencia(base, x=1)
        assert r.campos == base.campos

    def test_el_dict_de_campos_no_se_comparte(self):
        base = _base()
        r = derivar_evidencia(base, x=1)
        assert r.campos is not base.campos

    def test_se_pueden_reemplazar(self):
        """El caso de la combinación adicional: los campos se recalculan."""
        nuevos = {"tipo_comprobante": CampoCombinado(valor="B", fuente=Fuente.arca)}
        r = derivar_evidencia(_base(), campos=nuevos, x=1)
        assert r.campos["tipo_comprobante"].valor == "B"

    def test_unos_campos_vacios_reemplazan(self):
        """``campos={}`` es un valor válido (no se confunde con «no pasar»)."""
        r = derivar_evidencia(_base(), campos={}, x=1)
        assert r.campos == {}

    def test_el_contrato_rechaza_extras(self):
        """``extra='forbid'`` en el modelo: no se cuelan campos por accidente."""
        with pytest.raises(Exception):
            CombinedEvidence(documento_id="d", inventado=1)  # type: ignore[call-arg]


class TestTodasLasEtapasUsanElHelper:
    """⚠️ La propiedad del refactor: cinco sitios, una implementación."""

    def test_los_modulos_delegan_en_el_helper(self):
        import ast
        from pathlib import Path

        raiz = Path(__file__).resolve().parents[1] / "src" / "voucherflow"
        # Los cinco sitios que derivan de una evidencia ya no arman el objeto a
        # mano: tres estaban en conclusion/engine.py, uno en api.py y uno en
        # cli/main.py.
        for archivo in ("api.py", "cli/main.py", "conclusion/engine.py"):
            texto = (raiz / archivo).read_text(encoding="utf-8")
            a_mano = [
                n
                for n in ast.walk(ast.parse(texto))
                if isinstance(n, ast.Call)
                and isinstance(n.func, ast.Name)
                and n.func.id == "CombinedEvidence"
            ]
            assert not a_mano, (
                f"{archivo}: quedan {len(a_mano)} construcciones de "
                "CombinedEvidence a mano; usar `derivar_evidencia`"
            )

    def test_ningun_sitio_arma_la_traza_a_mano(self):
        """El ``trazabilidad = dict(evidencia.trazabilidad)`` repetido ya no está.

        ⚠️ Excepción declarada: ``consolidacion._trazabilidad`` devuelve un
        **dict**, no una evidencia derivada (consolida el veredicto, no enriquece
        la evidencia). Es otro patrón y el helper no aplica.
        """
        from pathlib import Path

        raiz = Path(__file__).resolve().parents[1] / "src" / "voucherflow"
        patron = "trazabilidad = dict(evidencia.trazabilidad)"
        #: Excepción declarada: ``consolidacion._trazabilidad`` copia la traza
        #: para devolver un **dict** (consolida el veredicto, no enriquece la
        #: evidencia). El helper deriva una evidencia, así que no aplica.
        exceptuados = {"consolidacion.py"}
        culpables = [
            archivo.name
            for archivo in raiz.rglob("*.py")
            if patron in archivo.read_text(encoding="utf-8")
            and archivo.name not in exceptuados
        ]
        assert not culpables, (
            f"estos módulos copian la traza a mano: {culpables} "
            "(usar `derivar_evidencia`, que además no puede olvidarse)"
        )

    def test_la_excepcion_sigue_siendo_un_dict(self):
        """Control: si consolidación pasara a derivar evidencia, sacar la excepción."""
        from pathlib import Path

        raiz = Path(__file__).resolve().parents[1] / "src" / "voucherflow"
        texto = (raiz / "conclusion" / "consolidacion.py").read_text(encoding="utf-8")
        assert "-> dict[str, Any]:" in texto, (
            "consolidacion._trazabilidad ya no devuelve un dict: revisar la "
            "excepción de test_ningun_sitio_arma_la_traza_a_mano"
        )

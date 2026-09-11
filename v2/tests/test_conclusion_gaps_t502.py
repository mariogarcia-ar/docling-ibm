"""Tests de la detección de gaps y la búsqueda acotada de evidencia (F5/T-502).

**DoD de T-502** (F5.md §3, E-CONC-2 / ADR-003): "Se detectan gaps y se busca
evidencia adicional con un límite acotado mediante el hook ARCA".

Qué se verifica, en el orden del entregable:

1. **La detección de gaps** (`rules/gaps.py::detectar_gaps`): qué falta, con qué
   criticidad y **con qué objetivo concreto** (E-CONC-2: "objetivo concreto por
   campo"). Un campo que el catálogo no declara se reporta igual, como informativo
   y **no buscable** (no se dispara una consulta por algo que no se sabe pedir).
2. **El presupuesto** (`PresupuestoBusqueda`): los dos topes (consultas totales y
   reintentos por gap) y su agotamiento.
3. **La búsqueda acotada** (`buscar_evidencia_adicional`): cubre el gap cuando el
   buscador responde; reintenta solo lo **transitorio**; **no insiste** cuando el
   mundo ya contestó "no está"; registra el hook desactivado como caso normal; y
   **corta** al agotar el presupuesto (la regla dura de E-CONC-2: no hay loop
   abierto).
4. **La re-conclusión** (`conclusion.engine::concluir_con_busqueda`): el dato
   recuperado entra a la evidencia con la precedencia correcta (ADR-002: el padrón
   es una fuente que **no es lectura**, así que va por delante) y las cruzadas de
   T-501 se **vuelven a aplicar** sobre el caso enriquecido.
5. **El adaptador ARCA** (`models/arca.py`): el pedido se arma con lo que el caso
   ya sabe, la respuesta del padrón se traduce al contrato de búsqueda, los fallos
   de red son *no disponible* (ADR-003: no tumban el pipeline) y **no se inventa**
   un código AFIP para un tipo de comprobante que no está en el vocabulario
   (decisión abierta D-13).
6. **Fronteras**: la búsqueda **no decide** (solo busca y reporta), no muta la
   evidencia de entrada, no busca lo no buscable y no consulta dos veces el mismo
   gap resuelto.

Reglas duras: suite default **sin** Ollama real, **sin** Docling real y **sin**
red (el buscador es un doble inyectado).
"""

from __future__ import annotations

from typing import Any

import pytest

from voucherflow.conclusion import concluir_con_busqueda
from voucherflow.conclusion.engine import ConclusionConBusqueda
from voucherflow.extraction.flows import combinar_evidencia
from voucherflow.models.arca import (
    ArcaClient,
    ArcaResultado,
    parse_pto_vta_nro,
)
from voucherflow.rules.contexto_conclusion import ContextoConclusion
from voucherflow.rules.cruzadas import ESTADO_APROBADO, ESTADO_REVISION
from voucherflow.rules.gaps import (
    CATALOGO_GAPS,
    CRITICIDAD_BLOQUEANTE,
    CRITICIDAD_INFORMATIVA,
    INTENTO_CUBIERTO,
    INTENTO_NO_BUSCABLE,
    INTENTO_NO_DISPONIBLE,
    INTENTO_PRESUPUESTO_AGOTADO,
    INTENTO_SIN_DATO,
    VERSION_GAPS,
    BuscadorEvidencia,
    DeteccionGaps,
    PresupuestoBusqueda,
    ResultadoBusqueda,
    buscar_evidencia_adicional,
    detectar_gaps,
)
from voucherflow.schemas.evidence import (
    CombinedEvidence,
    EvidenceField,
    Fuente,
    SourceEvidence,
)

# ---------------------------------------------------------------------------
# Utilidades
# ---------------------------------------------------------------------------

#: Caso completo: todos los campos del contrato presentes (sin gaps).
COMPLETO: dict[str, Any] = {
    "tipo_comprobante": "A",
    "razon_social_emisor": "ACME SA",
    "cuit_emisor": "30-12345678-9",
    "razon_social_receptor": "Cliente SA",
    "cuit_receptor": "27-30111222-4",
    "fecha_emision": "2025-08-14",
    "nro_comprobante": "0001-00000001",
    "moneda": "ARS",
    "subtotal": "100.00",
    "iva": "21.00",
    "impuestos_internos": "0.00",
    "percepcion_iibb": "0.00",
    "otros_impuestos": "0.00",
    "monto_no_gravado": "0.00",
    "importe_total_facturado": "121.00",
    "descripcion": "servicios",
}

#: Caso con un gap **bloqueante**: falta el importe total (campo crítico).
SIN_IMPORTE: dict[str, Any] = {
    k: v for k, v in COMPLETO.items() if k != "importe_total_facturado"
}

#: Caso sin ningún campo crítico: muchos gaps bloqueantes.
VACIO: dict[str, Any] = {"razon_social_emisor": "ACME SA"}


def _source(fuente: Fuente, campos: dict[str, Any]) -> SourceEvidence:
    return SourceEvidence(
        fuente=fuente,
        campos={
            nombre: EvidenceField(
                campo=nombre,
                valor=valor,
                fuente=fuente,
                fragmento_sustento=f"soporte de {nombre}",
            )
            for nombre, valor in campos.items()
        },
    )


def _evidencia(campos: dict[str, Any]) -> CombinedEvidence:
    return combinar_evidencia(
        "doc-1", [_source(Fuente.vlm, campos), _source(Fuente.llm, campos)]
    )


def _contexto(campos: dict[str, Any]) -> ContextoConclusion:
    return ContextoConclusion.desde_evidencia(_evidencia(campos))


class BuscadorDoble:
    """Buscador de prueba: responde por campo y registra las consultas.

    ``respuestas`` mapea campo -> secuencia de respuestas (la última se repite),
    así se puede simular "falla y después responde" sin tocar la red.
    """

    def __init__(self, respuestas: dict[str, list[ResultadoBusqueda]] | None = None):
        self.respuestas = respuestas or {}
        self.consultas: list[str] = []

    def buscar(self, gap: Any, contexto: ContextoConclusion) -> ResultadoBusqueda:
        self.consultas.append(gap.campo)
        secuencia = self.respuestas.get(gap.campo)
        if not secuencia:
            return ResultadoBusqueda(campo=gap.campo, disponible=True, valor=None)
        if len(secuencia) == 1:
            return secuencia[0]
        return secuencia.pop(0)


def _cubre(campo: str, valor: Any = "dato-del-padron", sostento: str = "padrón: constatado") -> ResultadoBusqueda:
    return ResultadoBusqueda(campo=campo, valor=valor, disponible=True, sostento=sostento)


def _caido(campo: str, error: str = "timeout") -> ResultadoBusqueda:
    return ResultadoBusqueda(campo=campo, disponible=False, error=error)


def _sin_dato(campo: str) -> ResultadoBusqueda:
    return ResultadoBusqueda(campo=campo, disponible=True, valor=None)


# ---------------------------------------------------------------------------
# 1. Detección de gaps
# ---------------------------------------------------------------------------


class TestDeteccionGaps:
    """La detección es determinística y nombra el objetivo concreto."""

    def test_un_caso_completo_no_tiene_gaps(self):
        deteccion = detectar_gaps(_contexto(COMPLETO))

        assert deteccion.gaps == []
        assert deteccion.campos_faltantes == []
        assert not deteccion.hay_bloqueantes
        assert "no hay gaps" in deteccion.motivo

    def test_detecta_el_campo_critico_faltante_como_bloqueante(self):
        deteccion = detectar_gaps(_contexto(SIN_IMPORTE))

        bloqueantes = [gap.campo for gap in deteccion.bloqueantes]
        assert "importe_total_facturado" in bloqueantes
        assert deteccion.hay_bloqueantes

    def test_cada_gap_trae_un_objetivo_concreto(self):
        # E-CONC-2: se busca "con un objetivo concreto", no "más evidencia".
        deteccion = detectar_gaps(_contexto(SIN_IMPORTE))

        for gap in deteccion.gaps:
            assert gap.objetivo.strip(), gap.campo

    def test_un_campo_fuera_del_catalogo_igual_se_reporta(self):
        # No se pierde información: aparece en campos_faltantes y como gap
        # informativo no buscable.
        deteccion = detectar_gaps(_contexto(VACIO))

        assert "descripcion" in deteccion.campos_faltantes
        descripcion = next(g for g in deteccion.gaps if g.campo == "descripcion")
        assert not descripcion.buscable
        assert descripcion.criticidad == CRITICIDAD_INFORMATIVA

    def test_lo_no_buscable_declara_que_solo_viene_del_documento(self):
        # La descripción de los ítems no está en el padrón: se declara, no se
        # dispara una consulta inútil.
        deteccion = detectar_gaps(_contexto(VACIO))
        descripcion = next(g for g in deteccion.gaps if g.campo == "descripcion")

        assert not descripcion.buscable
        assert descripcion.fuente is None
        assert "documento" in descripcion.objetivo

    def test_los_criticos_del_contrato_son_bloqueantes(self):
        # La criticidad la manda CAMPOS_CRITICOS, no el catálogo: no hay dos
        # definiciones de "qué bloquea".
        from voucherflow.rules.contexto_conclusion import CAMPOS_CRITICOS

        deteccion = detectar_gaps(_contexto(VACIO))
        bloqueantes = {gap.campo for gap in deteccion.bloqueantes}

        assert set(CAMPOS_CRITICOS) <= bloqueantes

    def test_los_gaps_se_ordenan_bloqueantes_primero_y_luego_alfabetico(self):
        deteccion = detectar_gaps(_contexto(VACIO))
        claves = [(not gap.bloqueante, gap.campo) for gap in deteccion.gaps]

        assert claves == sorted(claves)

    def test_es_determinista(self):
        a = detectar_gaps(_contexto(VACIO)).como_dict()
        b = detectar_gaps(_contexto(VACIO)).como_dict()

        assert a == b

    def test_el_resumen_es_serializable(self):
        resumen = detectar_gaps(_contexto(VACIO)).como_dict()

        assert resumen["version"] == VERSION_GAPS
        assert resumen["buscables"]
        assert resumen["bloqueantes"]

    def test_el_catalogo_declara_la_fuente_de_cada_gap_buscable(self):
        for campo, entrada in CATALOGO_GAPS.items():
            if entrada["buscable"]:
                assert entrada["fuente"] is not None, campo
                assert entrada["objetivo"].strip(), campo

    def test_rechaza_algo_que_no_es_contexto(self):
        with pytest.raises(TypeError, match="ContextoConclusion"):
            detectar_gaps({"campos_ausentes": []})  # type: ignore[arg-type]


# ---------------------------------------------------------------------------
# 2. Presupuesto
# ---------------------------------------------------------------------------


class TestPresupuesto:
    """Los dos topes del presupuesto y su agotamiento."""

    def test_un_presupuesto_nuevo_tiene_todo_disponible(self):
        limite = PresupuestoBusqueda(max_consultas=3)

        assert limite.restantes == 3
        assert not limite.agotado

    def test_consumir_gasta_una_consulta(self):
        limite = PresupuestoBusqueda(max_consultas=2)

        assert limite.consumir()
        assert limite.consultas == 1
        assert limite.restantes == 1

    def test_no_se_puede_consumir_de_mas(self):
        limite = PresupuestoBusqueda(max_consultas=1)

        assert limite.consumir()
        assert not limite.consumir()
        assert limite.agotado

    def test_los_dos_topes_son_independientes(self):
        # Consultas totales y reintentos por gap protegen de cosas distintas.
        limite = PresupuestoBusqueda(max_consultas=10, max_reintentos=0)

        assert limite.max_consultas == 10
        assert limite.max_reintentos == 0

    @pytest.mark.parametrize("campo", ["max_consultas", "max_reintentos"])
    def test_un_tope_negativo_es_invalido(self, campo: str):
        with pytest.raises(ValueError, match=campo):
            PresupuestoBusqueda(**{campo: -1})

    def test_el_resumen_es_serializable(self):
        limite = PresupuestoBusqueda(max_consultas=2)
        limite.consumir()

        resumen = limite.como_dict()
        assert resumen["consultas"] == 1
        assert resumen["restantes"] == 1
        assert resumen["agotado"] is False


# ---------------------------------------------------------------------------
# 3. Búsqueda acotada
# ---------------------------------------------------------------------------


class TestBusquedaAcotada:
    """El bucle es acotado por construcción (E-CONC-2: no loop abierto)."""

    def test_cubre_el_gap_cuando_el_buscador_responde(self):
        deteccion = detectar_gaps(_contexto(SIN_IMPORTE))
        buscador = BuscadorDoble({"importe_total_facturado": [_cubre("importe_total_facturado", "121.00")]})

        resultado = buscar_evidencia_adicional(deteccion, _contexto(SIN_IMPORTE), buscador=buscador)

        assert "importe_total_facturado" in resultado.campos
        assert resultado.campos["importe_total_facturado"].valor == "121.00"
        assert resultado.campos["importe_total_facturado"].fuente == Fuente.arca

    def test_el_campo_recuperado_conserva_su_sosten(self):
        # ADR-001: sin sostén no es evidencia auditable.
        deteccion = detectar_gaps(_contexto(SIN_IMPORTE))
        buscador = BuscadorDoble({"importe_total_facturado": [_cubre("importe_total_facturado", "121.00", "padrón: total constatado")]})

        resultado = buscar_evidencia_adicional(deteccion, _contexto(SIN_IMPORTE), buscador=buscador)

        assert resultado.campos["importe_total_facturado"].fragmento_sustento == "padrón: total constatado"

    def test_un_buscador_none_es_un_caso_normal(self):
        # ADR-003: el hook ARCA no bloquea el MVP. No es un error: se registra.
        deteccion = detectar_gaps(_contexto(SIN_IMPORTE))

        resultado = buscar_evidencia_adicional(deteccion, _contexto(SIN_IMPORTE), buscador=None)

        assert resultado.campos == {}
        assert any(i.resultado == INTENTO_NO_DISPONIBLE for i in resultado.intentos)
        assert "desactivado" in " ".join(i.motivo for i in resultado.intentos)

    def test_lo_no_buscable_no_consume_presupuesto(self):
        deteccion = detectar_gaps(_contexto(VACIO))
        buscador = BuscadorDoble({})
        limite = PresupuestoBusqueda(max_consultas=100)

        resultado = buscar_evidencia_adicional(deteccion, _contexto(VACIO), buscador=buscador, presupuesto=limite)

        assert "descripcion" not in buscador.consultas
        assert any(i.resultado == INTENTO_NO_BUSCABLE for i in resultado.intentos)

    def test_reintenta_solo_lo_transitorio(self):
        # Proveedor caído -> reintenta; si al final responde, el gap se cubre.
        deteccion = detectar_gaps(_contexto(SIN_IMPORTE))
        buscador = BuscadorDoble({
            "importe_total_facturado": [_caido("importe_total_facturado"), _cubre("importe_total_facturado", "121.00")]
        })

        resultado = buscar_evidencia_adicional(
            deteccion, _contexto(SIN_IMPORTE), buscador=buscador,
            presupuesto=PresupuestoBusqueda(max_consultas=10, max_reintentos=2),
        )

        assert "importe_total_facturado" in resultado.campos
        assert buscador.consultas.count("importe_total_facturado") == 2

    def test_no_insiste_cuando_el_mundo_ya_contesto(self):
        # "Se consultó y el dato no está" es una respuesta del mundo: reintentar
        # no la va a cambiar. Una sola consulta para ese gap.
        deteccion = detectar_gaps(_contexto(SIN_IMPORTE))
        buscador = BuscadorDoble({"importe_total_facturado": [_sin_dato("importe_total_facturado")]})

        resultado = buscar_evidencia_adicional(
            deteccion, _contexto(SIN_IMPORTE), buscador=buscador,
            presupuesto=PresupuestoBusqueda(max_consultas=10, max_reintentos=3),
        )

        assert buscador.consultas.count("importe_total_facturado") == 1
        assert any(i.resultado == INTENTO_SIN_DATO for i in resultado.intentos)

    def test_el_presupuesto_agotado_corta_la_busqueda(self):
        # La regla dura de E-CONC-2: al agotar el límite, se avanza.
        deteccion = detectar_gaps(_contexto(VACIO))
        buscador = BuscadorDoble({})
        limite = PresupuestoBusqueda(max_consultas=2)

        resultado = buscar_evidencia_adicional(deteccion, _contexto(VACIO), buscador=buscador, presupuesto=limite)

        assert limite.agotado
        assert len(buscador.consultas) <= 2
        assert any(i.resultado == INTENTO_PRESUPUESTO_AGOTADO for i in resultado.intentos)

    def test_la_busqueda_termina_al_agotar_no_cuando_aparece_un_gap_nuevo(self):
        # El corte no depende de cómo salió el último intento: se corta.
        deteccion = detectar_gaps(_contexto(VACIO))
        buscador = BuscadorDoble({})
        limite = PresupuestoBusqueda(max_consultas=1, max_reintentos=1)

        buscar_evidencia_adicional(deteccion, _contexto(VACIO), buscador=buscador, presupuesto=limite)

        assert len(buscador.consultas) == 1

    def test_es_determinista(self):
        deteccion = detectar_gaps(_contexto(SIN_IMPORTE))
        a = buscar_evidencia_adicional(deteccion, _contexto(SIN_IMPORTE), buscador=BuscadorDoble({"importe_total_facturado": [_cubre("importe_total_facturado")]}))
        b = buscar_evidencia_adicional(deteccion, _contexto(SIN_IMPORTE), buscador=BuscadorDoble({"importe_total_facturado": [_cubre("importe_total_facturado")]}))

        assert a.como_dict() == b.como_dict()

    def test_un_buscador_que_lanza_no_tumba_la_busqueda(self):
        # ADR-003: el proveedor es opcional y no debe romper el pipeline.
        class BuscadorRoto:
            def buscar(self, gap, contexto):
                raise RuntimeError("sin credenciales del padrón")

        deteccion = detectar_gaps(_contexto(SIN_IMPORTE))

        resultado = buscar_evidencia_adicional(
            deteccion, _contexto(SIN_IMPORTE), buscador=BuscadorRoto(),
            presupuesto=PresupuestoBusqueda(max_consultas=5, max_reintentos=0),
        )

        assert resultado.campos == {}
        assert any("sin credenciales" in i.motivo for i in resultado.intentos)

    def test_un_buscador_que_devuelve_otra_cosa_se_reporta(self):
        class BuscadorMalo:
            def buscar(self, gap, contexto):
                return "no soy un ResultadoBusqueda"

        deteccion = detectar_gaps(_contexto(SIN_IMPORTE))

        resultado = buscar_evidencia_adicional(
            deteccion, _contexto(SIN_IMPORTE), buscador=BuscadorMalo(),  # type: ignore[arg-type]
            presupuesto=PresupuestoBusqueda(max_consultas=5, max_reintentos=0),
        )

        assert resultado.campos == {}
        assert any("ResultadoBusqueda" in i.motivo for i in resultado.intentos)

    def test_la_traza_es_serializable(self):
        deteccion = detectar_gaps(_contexto(SIN_IMPORTE))
        resultado = buscar_evidencia_adicional(
            deteccion, _contexto(SIN_IMPORTE), buscador=BuscadorDoble({"importe_total_facturado": [_cubre("importe_total_facturado")]})
        )

        resumen = resultado.como_dict()
        assert resumen["version"] == VERSION_GAPS
        assert resumen["cubiertos"] == ["importe_total_facturado"]
        assert resumen["intentos"]

    def test_rechaza_algo_que_no_es_una_deteccion(self):
        with pytest.raises(TypeError, match="DeteccionGaps"):
            buscar_evidencia_adicional(["gap"], _contexto(SIN_IMPORTE), buscador=None)  # type: ignore[arg-type]

    def test_el_protocolo_del_buscador_es_estructural(self):
        # Un doble con `buscar` satisface el protocolo sin heredar de nada.
        assert isinstance(BuscadorDoble(), BuscadorEvidencia)


# ---------------------------------------------------------------------------
# 4. Re-conclusión sobre el caso enriquecido
# ---------------------------------------------------------------------------


class TestConcluirConBusqueda:
    """El dato recuperado entra a la evidencia y las cruzadas se re-aplican."""

    def test_sin_gaps_no_hay_busqueda(self):
        resultado = concluir_con_busqueda(_evidencia(COMPLETO), buscador=BuscadorDoble({}))

        assert isinstance(resultado, ConclusionConBusqueda)
        assert not resultado.hubo_busqueda
        assert resultado.busqueda.campos == {}

    def test_el_gap_bloqueante_deja_el_caso_en_revision_sin_buscador(self):
        resultado = concluir_con_busqueda(_evidencia(SIN_IMPORTE), buscador=None)

        assert resultado.conclusion.estado == ESTADO_REVISION
        assert not resultado.conclusion.concluye
        assert "importe_total_facturado" in resultado.gaps_restantes

    def test_cubrir_el_gap_desbloquea_el_veredicto(self):
        # El dato del padrón cierra el gap crítico: el caso pasa de revisión a
        # aprobado por programa.
        buscador = BuscadorDoble({"importe_total_facturado": [_cubre("importe_total_facturado", "121.00")]})
        resultado = concluir_con_busqueda(_evidencia(SIN_IMPORTE), buscador=buscador)

        assert resultado.conclusion.estado == ESTADO_APROBADO
        assert resultado.conclusion.certeza == "alta"
        assert "importe_total_facturado" not in resultado.gaps_restantes

    def test_el_dato_del_padron_entra_con_su_fuente(self):
        buscador = BuscadorDoble({"importe_total_facturado": [_cubre("importe_total_facturado", "121.00")]})
        resultado = concluir_con_busqueda(_evidencia(SIN_IMPORTE), buscador=buscador)

        campo = resultado.evidencia.campos["importe_total_facturado"]
        assert campo.valor == "121.00"
        assert campo.fuente == Fuente.arca
        assert campo.arca is not None

    def test_el_padron_va_por_delante_de_las_lecturas(self):
        # ADR-002: una fuente que no es lectura va primero. La propiedad se
        # comprueba sobre la tabla, que es donde vive el orden.
        from voucherflow.rules.precedencia import _precedencia_de

        for campo in ("importe_total_facturado", "fecha_emision", "tipo_comprobante"):
            orden = _precedencia_de(campo).orden_completo()
            assert orden.index(Fuente.arca) < orden.index(Fuente.llm)
            assert orden.index(Fuente.arca) < orden.index(Fuente.vlm)

    def test_el_padron_gana_cuando_ambos_declaran_el_campo(self):
        # La contracara del test anterior, end-to-end: con el padrón y las
        # lecturas declarando el mismo campo, el valor vigente es el del padrón
        # y la resolución lo registra.
        campos = dict(SIN_IMPORTE)
        lecturas = [_source(Fuente.vlm, campos), _source(Fuente.llm, campos)]
        lecturas.append(
            _source(Fuente.arca, {"importe_total_facturado": "121.00"})
        )
        evidencia = combinar_evidencia("doc-1", lecturas)

        campo = evidencia.campos["importe_total_facturado"]
        assert campo.valor == "121.00"
        assert campo.fuente == Fuente.arca
        assert campo.resolucion is not None
        assert campo.resolucion.ganador == Fuente.arca

    def test_la_resolucion_del_campo_queda_trazada(self):
        buscador = BuscadorDoble({"importe_total_facturado": [_cubre("importe_total_facturado", "121.00")]})
        resultado = concluir_con_busqueda(_evidencia(SIN_IMPORTE), buscador=buscador)

        resolucion = resultado.evidencia.campos["importe_total_facturado"].resolucion
        assert resolucion is not None
        assert resolucion.ganador == Fuente.arca
        assert resolucion.regla

    def test_el_caso_ambiguo_sigue_sin_decision(self):
        # La búsqueda no cambia el contrato: si el caso no quedó resuelto, el
        # ``Decision`` de F0 sigue sin adjuntarse (lo llenarán T-504/T-505).
        resultado = concluir_con_busqueda(_evidencia(SIN_IMPORTE), buscador=None)

        assert resultado.evidencia.decision is None
        assert resultado.conclusion.estado == ESTADO_REVISION

    def test_las_lecturas_originales_se_conservan(self):
        # Fusionar no es descartar: el lado de cada lectura sigue ahí.
        campos = {**SIN_IMPORTE, "importe_total_facturado": "999.00"}
        buscador = BuscadorDoble({"importe_total_facturado": [_cubre("importe_total_facturado", "121.00")]})
        resultado = concluir_con_busqueda(_evidencia(campos), buscador=buscador)

        campo = resultado.evidencia.campos["importe_total_facturado"]
        assert campo.vlm is not None and campo.vlm.valor == "999.00"
        assert campo.llm is not None and campo.llm.valor == "999.00"

    def test_el_veredicto_anterior_no_sobrevive(self):
        # Se re-concluye: la decisión previa ya no rige.
        buscador = BuscadorDoble({"importe_total_facturado": [_cubre("importe_total_facturado", "121.00")]})
        resultado = concluir_con_busqueda(_evidencia(SIN_IMPORTE), buscador=buscador)

        assert resultado.evidencia.decision is not None
        assert resultado.evidencia.decision.origen.value == "programa"
        assert resultado.evidencia.decision.certeza.value == "alta"

    def test_el_padron_complementa_sin_pisar_lo_que_el_caso_ya_sabia(self):
        # Frontera: un campo que **ya estaba** no se re-consulta, así que el
        # padrón no pisa la lectura. La búsqueda es para lo que falta.
        buscador = BuscadorDoble({"importe_total_facturado": [_cubre("importe_total_facturado", "121.00")]})
        resultado = concluir_con_busqueda(_evidencia(COMPLETO), buscador=buscador)

        campo = resultado.evidencia.campos["importe_total_facturado"]
        assert campo.valor == "121.00"
        assert campo.fuente != Fuente.arca

    def test_la_falta_de_una_fuente_se_lleva_la_traza(self):
        # El pipeline de F4 ya declara que una fuente no corrió (sin vista o
        # sin markdown): esa falta se cruza con el gap, no se ignora.
        evidencia = combinar_evidencia("doc-1", [_source(Fuente.vlm, SIN_IMPORTE)])
        resultado = concluir_con_busqueda(evidencia, buscador=None)

        assert resultado.conclusion.estado == ESTADO_REVISION

    def test_la_traza_de_la_corrida_incluye_gaps_y_busqueda(self):
        buscador = BuscadorDoble({"importe_total_facturado": [_cubre("importe_total_facturado", "121.00")]})
        resultado = concluir_con_busqueda(_evidencia(SIN_IMPORTE), buscador=buscador)

        assert "gaps" in resultado.evidencia.trazabilidad
        assert "busqueda_evidencia_adicional" in resultado.evidencia.trazabilidad
        assert resultado.evidencia.trazabilidad["gaps"]["version"] == VERSION_GAPS

    def test_el_resumen_es_serializable(self):
        resultado = concluir_con_busqueda(_evidencia(SIN_IMPORTE), buscador=None)

        resumen = resultado.como_dict()
        assert resumen["version"] == VERSION_GAPS
        assert resumen["conclusion"]["estado"] == ESTADO_REVISION
        assert "deteccion" in resumen and "busqueda" in resumen

    def test_rechaza_algo_que_no_es_evidencia_combinada(self):
        with pytest.raises(TypeError, match="CombinedEvidence"):
            concluir_con_busqueda("no soy evidencia")  # type: ignore[arg-type]

    def test_los_sin_importe_posee_un_solo_gap_bloqueante(self):
        # Frontera del caso de prueba: el único crítico ausente es el importe.
        resultado = concluir_con_busqueda(_evidencia(SIN_IMPORTE), buscador=None)

        bloqueantes = [gap.campo for gap in resultado.deteccion.bloqueantes]
        assert bloqueantes == ["importe_total_facturado"]


# ---------------------------------------------------------------------------
# 5. El adaptador ARCA
# ---------------------------------------------------------------------------


class _RespuestaFalsa:
    """Doble mínimo de ``requests.Response``."""

    def __init__(self, cuerpo: Any, status: int = 200):
        self._cuerpo = cuerpo
        self.status_code = status

    def json(self) -> Any:
        return self._cuerpo


class _SesionFalsa:
    """Sesión HTTP falsa: encola respuestas o lanza."""

    def __init__(self, respuestas: list[Any]):
        self.respuestas = respuestas
        self.llamadas: list[tuple[str, dict[str, Any], float]] = []

    def post(self, url: str, data: Any = None, timeout: float | None = None) -> Any:
        self.llamadas.append((url, data, timeout))
        respuesta = self.respuestas.pop(0) if len(self.respuestas) > 1 else self.respuestas[0]
        if isinstance(respuesta, Exception):
            raise respuesta
        return respuesta


class TestParseNumeroComprobante:
    """El número impreso ``PPPPP-NNNNNNNN`` se separa sin adivinar."""

    def test_separa_punto_de_venta_y_numero(self):
        assert parse_pto_vta_nro("00001-00000031") == (1, 31)

    def test_tolera_espacios(self):
        assert parse_pto_vta_nro("  0002 - 00000007 ") == (2, 7)

    @pytest.mark.parametrize("valor", ["", "sin-formato", "0001", None])
    def test_un_formato_invalido_no_se_adivina(self, valor: Any):
        with pytest.raises(ValueError, match="No se pudo interpretar"):
            parse_pto_vta_nro(valor)  # type: ignore[arg-type]


class TestArcaClient:
    """El adaptador habla HTTP y traduce; no decide ni inventa."""

    def test_sin_url_el_hook_esta_desactivado(self):
        deteccion = detectar_gaps(_contexto(SIN_IMPORTE))
        cliente = ArcaClient(url=None)

        resultado = cliente.buscar(next(g for g in deteccion.gaps if g.campo == "importe_total_facturado"), _contexto(SIN_IMPORTE))

        assert not resultado.disponible
        assert "desactivado" in (resultado.error or "")

    def test_parsea_la_respuesta_del_padron(self):
        sesion = _SesionFalsa([_RespuestaFalsa({"ComprobanteConstatarResult": {"Resultado": "A"}})])
        cliente = ArcaClient(url="http://arca.local", session=sesion)
        deteccion = detectar_gaps(_contexto(SIN_IMPORTE))

        resultado = cliente.buscar(next(g for g in deteccion.gaps if g.campo == "importe_total_facturado"), _contexto(SIN_IMPORTE))

        assert resultado.disponible
        assert resultado.valor is not None
        assert resultado.sostento.strip()

    def test_un_comprobante_no_constatado_no_es_un_fallo_de_red(self):
        # HTTP 200 con Resultado distinto de "A": se consultó y no constató.
        sesion = _SesionFalsa([_RespuestaFalsa({"ComprobanteConstatarResult": {"Resultado": "R"}})])
        cliente = ArcaClient(url="http://arca.local", session=sesion)
        deteccion = detectar_gaps(_contexto(SIN_IMPORTE))

        resultado = cliente.buscar(next(g for g in deteccion.gaps if g.campo == "importe_total_facturado"), _contexto(SIN_IMPORTE))

        assert resultado.disponible
        assert resultado.valor is None

    def test_arma_el_pedido_con_lo_que_el_caso_ya_sabe(self):
        sesion = _SesionFalsa([_RespuestaFalsa({"ComprobanteConstatarResult": {"Resultado": "A"}})])
        cliente = ArcaClient(url="http://arca.local", cuit="20111111112", session=sesion)
        deteccion = detectar_gaps(_contexto(SIN_IMPORTE))

        cliente.buscar(next(g for g in deteccion.gaps if g.campo == "importe_total_facturado"), _contexto(COMPLETO))

        _, cuerpo, _ = sesion.llamadas[0]
        import json as _json

        payload = _json.loads(cuerpo)["CmpReq"]
        assert payload["PtoVta"] == 1
        assert payload["CbteNro"] == 1
        assert payload["CuitEmisor"] == "30123456789"
        assert payload["DocNroReceptor"] == "27301112224"
        assert payload["DocTipoReceptor"] == 80
        assert payload["CbteFch"] == "20250814"
        assert payload["CbteTipo"] == 1

    def test_sin_numero_de_comprobante_no_se_formula_la_consulta(self):
        # Objetivo concreto (E-CONC-2): sin el número no hay consulta puntual.
        cliente = ArcaClient(url="http://arca.local", session=_SesionFalsa([_RespuestaFalsa({})]))
        deteccion = detectar_gaps(_contexto(SIN_IMPORTE))
        contextos = _contexto({"tipo_comprobante": "A", "cuit_emisor": "30-12345678-9"})

        resultado = cliente.buscar(next(g for g in deteccion.gaps if g.campo == "importe_total_facturado"), contextos)

        assert not resultado.disponible
        assert "número" in (resultado.error or "")

    def test_un_fallo_de_red_no_tumba_el_pipeline(self):
        sesion = _SesionFalsa([ConnectionError("el padrón no contesta")])
        cliente = ArcaClient(url="http://arca.local", session=sesion, max_reintentos=0)
        deteccion = detectar_gaps(_contexto(SIN_IMPORTE))

        resultado = cliente.buscar(next(g for g in deteccion.gaps if g.campo == "importe_total_facturado"), _contexto(COMPLETO))

        assert not resultado.disponible
        assert resultado.error

    def test_reintenta_los_fallos_transitorios(self):
        sesion = _SesionFalsa([
            ConnectionError("timeout"),
            _RespuestaFalsa({"ComprobanteConstatarResult": {"Resultado": "A"}}),
        ])
        cliente = ArcaClient(url="http://arca.local", session=sesion, max_reintentos=2)
        deteccion = detectar_gaps(_contexto(SIN_IMPORTE))

        resultado = cliente.buscar(next(g for g in deteccion.gaps if g.campo == "importe_total_facturado"), _contexto(COMPLETO))

        assert resultado.disponible
        assert len(sesion.llamadas) >= 2

    def test_el_timeout_se_pasa_a_la_sesion(self):
        sesion = _SesionFalsa([_RespuestaFalsa({"ComprobanteConstatarResult": {"Resultado": "A"}})])
        cliente = ArcaClient(url="http://arca.local", timeout_s=7.5, session=sesion)
        deteccion = detectar_gaps(_contexto(SIN_IMPORTE))

        cliente.buscar(next(g for g in deteccion.gaps if g.campo == "importe_total_facturado"), _contexto(COMPLETO))

        assert sesion.llamadas[0][2] == 7.5

    def test_un_error_del_servidor_se_reintenta_y_luego_se_reporta(self):
        sesion = _SesionFalsa([_RespuestaFalsa({}, status=503)])
        cliente = ArcaClient(url="http://arca.local", session=sesion, max_reintentos=1)
        deteccion = detectar_gaps(_contexto(SIN_IMPORTE))

        resultado = cliente.buscar(next(g for g in deteccion.gaps if g.campo == "importe_total_facturado"), _contexto(COMPLETO))

        assert not resultado.disponible
        assert len(sesion.llamadas) == 2

    def test_no_inventa_un_codigo_afip_para_un_tipo_no_mapeado(self):
        # Decisión abierta D-13: no se inventa el mapeo de tiques.
        cliente = ArcaClient(url="http://arca.local", session=_SesionFalsa([_RespuestaFalsa({})]))
        deteccion = detectar_gaps(_contexto(SIN_IMPORTE))
        contexto = _contexto({**COMPLETO, "tipo_comprobante": "090"})

        resultado = cliente.buscar(next(g for g in deteccion.gaps if g.campo == "importe_total_facturado"), contexto)

        assert not resultado.disponible
        assert "D-13" in (resultado.error or "")

    def test_es_un_buscador_del_protocolo(self):
        assert isinstance(ArcaClient(), BuscadorEvidencia)

    def test_consultar_devuelve_el_contrato_del_adaptador(self):
        sesion = _SesionFalsa([_RespuestaFalsa({"ComprobanteConstatarResult": {"Resultado": "A"}})])
        cliente = ArcaClient(url="http://arca.local", session=sesion)

        resultado = cliente.consultar("importe_total_facturado", "constatar el importe")

        assert isinstance(resultado, ArcaResultado)
        assert resultado.ok
        assert resultado.datos["valor"]


# ---------------------------------------------------------------------------
# 6. Fronteras
# ---------------------------------------------------------------------------


class TestFronteras:
    """Lo que la búsqueda NO hace (es de otras tareas de F5)."""

    def test_no_muta_la_evidencia_de_entrada(self):
        original = _evidencia(SIN_IMPORTE)
        antes = dict(original.trazabilidad)

        concluir_con_busqueda(original, buscador=BuscadorDoble({}))

        assert dict(original.trazabilidad) == antes
        assert original.decision is None

    def test_no_busca_cuando_no_hay_gaps(self):
        buscador = BuscadorDoble({})

        concluir_con_busqueda(_evidencia(COMPLETO), buscador=buscador)

        assert buscador.consultas == []

    def test_la_busqueda_no_decide(self):
        # `buscar_evidencia_adicional` devuelve datos y traza; no un veredicto.
        deteccion = detectar_gaps(_contexto(SIN_IMPORTE))
        resultado = buscar_evidencia_adicional(deteccion, _contexto(SIN_IMPORTE), buscador=None)

        assert not hasattr(resultado, "estado")
        assert not hasattr(resultado, "concluye")

    def test_la_busqueda_corre_una_sola_vez(self):
        # No hay loop abierto: re-concluir NO dispara otra búsqueda, aunque el
        # gap siga ahí.
        buscador = BuscadorDoble({})
        resultado = concluir_con_busqueda(_evidencia(SIN_IMPORTE), buscador=buscador)

        assert resultado.gaps_restantes
        assert len(set(buscador.consultas)) == len(buscador.consultas)

    def test_no_llama_al_agente_ni_encola_hitl(self):
        # T-504/T-505 siguen siendo esqueleto.
        from voucherflow.conclusion.engine import encolar_hitl, escalar_a_agente

        with pytest.raises(NotImplementedError):
            escalar_a_agente(_evidencia(COMPLETO), ["A"])
        with pytest.raises(NotImplementedError):
            encolar_hitl(None)  # type: ignore[arg-type]

    def test_un_caso_sin_gaps_ni_buscador_no_cambia_el_veredicto(self):
        # El hook desactivado no degrada un caso que ya estaba completo.
        resultado = concluir_con_busqueda(_evidencia(COMPLETO), buscador=None)

        assert resultado.conclusion.estado == ESTADO_APROBADO

    def test_el_deteccion_no_inventa_gaps_de_campos_presentes(self):
        deteccion = detectar_gaps(_contexto(COMPLETO))

        assert all(gap.campo not in COMPLETO for gap in deteccion.gaps)

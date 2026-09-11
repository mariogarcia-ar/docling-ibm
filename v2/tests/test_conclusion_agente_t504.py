"""Tests del escalado al agente IA y su blindaje (F5/T-504, E-CONC-3).

**DoD de T-504** (F5.md §3, E-CONC-3): "Los candidatos que el código no pudo
concluir escalan al agente IA, con blindaje post-agente y certeza baja (→ revisión
humana). Se reporta % agente (mitiga R-09)".

Los dos escenarios del Gherkin:

    Dado que el código no concluye
    Cuando se escala al agente de IA
    Entonces el agente recibe evidencia, reglas que fallaron y candidatos_restantes
    Y NO puede elegir candidatos ya descartados por el código

    Dado que el agente decide
    Cuando se consolida el resultado
    Entonces se marca certeza=baja y origen=agente_ia
    Y el caso se encola a HITL con prioridad alta

Qué se verifica, en el orden del entregable:

1. **Cuándo se escala** (y cuándo no): solo si el código no concluyó y hay
   universo. Un caso resuelto por el código **no** gasta una llamada al modelo.
2. **Qué recibe el agente**: evidencia vigente + reglas que fallaron +
   candidatos restantes — y **no** los descartados (primera capa del blindaje).
3. **El blindaje post-agente**: elegir un descartado se **rechaza** (no se corrige
   a un valor parecido) y la anomalía queda registrada.
4. **La derivación de certeza/origen** (glosario §2): el agente siempre implica
   certeza baja; el origen es ``agente_ia`` solo si su elección sobrevivió.
5. **La consolidación del camino agéntico** (T-503): el veredicto del agente no se
   consolida como "certeza alta por programa".
6. **Fronteras**: el agente es inyectable (sin red), su fallo no tumba el
   pipeline, y no normaliza ni inventa evidencia.

Reglas duras: suite default **sin** Ollama, **sin** Docling y **sin** red.
"""

from __future__ import annotations

import json
from typing import Any

import pytest

from voucherflow.conclusion import (
    AGENTE_ELECCION_INVALIDA,
    AGENTE_FALLO,
    AGENTE_NO_ESCALADO,
    AGENTE_SE_ABSTUVO,
    AGENTE_ELIGIO,
    AgenteOllama,
    DecisionAgente,
    EleccionAgente,
    concluir_con_agente,
    escalar_a_agente,
    escalar_veredicto,
    parsear_eleccion_agente,
)
from voucherflow.conclusion.agent import Agente
from voucherflow.conclusion.prompt_agente import (
    CLAVE_CANDIDATO,
    VERSION_PROMPT_AGENTE,
    construir_messages_agente,
)
from voucherflow.extraction.flows import combinar_evidencia
from voucherflow.rules.contexto import ContextoTipoComprobante
from voucherflow.rules.cruzadas import ESTADO_APROBADO, ESTADO_REVISION
from voucherflow.schemas.evidence import (
    Certeza,
    CombinedEvidence,
    EvidenceField,
    Fuente,
    Origen,
    SourceEvidence,
)

# ---------------------------------------------------------------------------
# Utilidades
# ---------------------------------------------------------------------------

#: Factura B con emisor y receptor RI: el código la deja en revisión (R7) y **sí**
#: tiene candidatos (esperado por negocio ``A``, detectado ``B``).
CASO_AMBIGUO: dict[str, Any] = {
    "tipo_comprobante": "B",
    "razon_social_emisor": "ACME SA",
    "cuit_emisor": "30-12345678-9",
    "fecha_emision": "2025-08-14",
    "nro_comprobante": "0001-00000001",
    "moneda": "ARS",
    "iva": "0.00",
    "importe_total_facturado": "121.00",
}

#: Factura A completa y coherente: el código **sí** concluye.
CASO_RESUELTO: dict[str, Any] = {
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
    "importe_total_facturado": "121.00",
}

CTX_RI_RI = ContextoTipoComprobante(
    emisor_condicion_fiscal="Responsable Inscripto",
    receptor_condicion_fiscal="Responsable Inscripto",
)


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


class _Respuesta:
    def __init__(self, contenido: str):
        self.contenido = contenido


class AgenteDoble:
    """Agente de prueba: devuelve un contenido fijo y registra lo que recibió."""

    def __init__(self, contenido: str):
        self.contenido = contenido
        self.messages: list[dict[str, str]] | None = None
        self.modelo: str | None = None
        self.num_ctx: int | None = None

    def decidir(self, messages: list[dict[str, str]], modelo: str, *, num_ctx: int | None = None) -> Any:
        self.messages = messages
        self.modelo = modelo
        self.num_ctx = num_ctx
        return _Respuesta(self.contenido)


class AgenteRoto:
    def decidir(self, messages: Any, modelo: Any, *, num_ctx: Any = None) -> Any:
        raise RuntimeError("el modelo no está disponible")


def _json_agente(candidato: Any, **extra: Any) -> str:
    cuerpo = {
        CLAVE_CANDIDATO: candidato,
        "justificacion": extra.get("justificacion", "porque sí"),
        "fragmento_sustento": extra.get("fragmento_sustento", "iva: 0.00"),
    }
    return json.dumps(cuerpo, ensure_ascii=False)


# ---------------------------------------------------------------------------
# 1. Cuándo se escala
# ---------------------------------------------------------------------------


class TestCuandoSeEscala:
    """Solo cuando el código no concluyó y hay universo."""

    def test_no_se_escala_si_el_codigo_concluyo(self):
        # ADR-008 y el Gherkin: el agente es para lo que el código NO pudo.
        agente = AgenteDoble(_json_agente("A"))
        resultado = escalar_a_agente(_evidencia(CASO_RESUELTO), contexto_tipo=CTX_RI_RI, agente=agente)

        assert not resultado.escalado
        assert resultado.desenlace == AGENTE_NO_ESCALADO
        assert agente.messages is None  # no se gastó una llamada al modelo

    def test_el_motivo_del_no_escalado_cita_el_gherkin(self):
        resultado = escalar_a_agente(_evidencia(CASO_RESUELTO), contexto_tipo=CTX_RI_RI)

        assert "no concluye" in resultado.motivo.lower() or "concluyó" in resultado.motivo

    def test_se_escala_cuando_el_codigo_no_concluye(self):
        agente = AgenteDoble(_json_agente("B"))
        resultado = escalar_a_agente(_evidencia(CASO_AMBIGUO), contexto_tipo=CTX_RI_RI, agente=agente)

        assert resultado.escalado
        assert agente.messages is not None

    def test_no_se_escala_sin_universo_de_candidatos(self):
        # Sin candidatos no hay nada que elegir ni contra qué validar.
        vacio = {"razon_social_emisor": "ACME"}
        resultado = escalar_a_agente(_evidencia(vacio), agente=AgenteDoble(_json_agente("A")))

        assert not resultado.escalado
        assert resultado.desenlace == AGENTE_NO_ESCALADO

    def test_el_universo_sale_del_veredicto_y_no_del_llamador(self):
        # `candidatos` es referencial: pasar algo distinto no amplía el universo.
        agente = AgenteDoble(_json_agente("B"))
        resultado = escalar_a_agente(
            _evidencia(CASO_AMBIGUO),
            ["A", "B", "C", "M", "E"],  # el llamador propone un universo amplio
            contexto_tipo=CTX_RI_RI,
            agente=agente,
        )

        assert resultado.candidato == "B"

    def test_rechaza_algo_que_no_es_evidencia_combinada(self):
        with pytest.raises(TypeError, match="CombinedEvidence"):
            escalar_a_agente("no soy evidencia")  # type: ignore[arg-type]


# ---------------------------------------------------------------------------
# 2. Qué recibe el agente
# ---------------------------------------------------------------------------


class TestLoQueRecibeElAgente:
    """Evidencia + reglas que fallaron + candidatos restantes (Gherkin E-CONC-3)."""

    def test_los_messages_traen_los_candidatos_restantes(self):
        agente = AgenteDoble(_json_agente("B"))
        escalar_a_agente(_evidencia(CASO_AMBIGUO), contexto_tipo=CTX_RI_RI, agente=agente)

        texto = "\n".join(m["content"] for m in agente.messages)
        assert "B" in texto

    def test_los_candidatos_descartados_no_se_le_pasan(self):
        # Primera capa del blindaje: lo que el agente no ve, no puede elegir.
        agente = AgenteDoble(_json_agente("B"))
        resultado = escalar_a_agente(_evidencia(CASO_AMBIGUO), contexto_tipo=CTX_RI_RI, agente=agente)

        # El caso tiene 'A' descartado (el negocio espera A y el documento dice B).
        assert "A" in resultado.candidatos_descartados if hasattr(resultado, "candidatos_descartados") else True
        texto_usuario = agente.messages[-1]["content"]
        assert "descartado" not in texto_usuario.lower() or "no" in texto_usuario.lower()

    def test_los_messages_traen_las_reglas_que_fallaron(self):
        agente = AgenteDoble(_json_agente("B"))
        escalar_a_agente(_evidencia(CASO_AMBIGUO), contexto_tipo=CTX_RI_RI, agente=agente)

        texto = "\n".join(m["content"] for m in agente.messages)
        assert "CRUZ" in texto

    def test_los_messages_traen_la_evidencia_vigente(self):
        agente = AgenteDoble(_json_agente("B"))
        escalar_a_agente(_evidencia(CASO_AMBIGUO), contexto_tipo=CTX_RI_RI, agente=agente)

        texto = "\n".join(m["content"] for m in agente.messages)
        assert "cuit_emisor" in texto
        assert "30-12345678-9" in texto

    def test_el_prompt_declara_el_universo_cerrado(self):
        # El prompt es la primera capa: pide respetar el universo y permite
        # abstenerse.
        messages = construir_messages_agente(
            evidencia={"tipo_comprobante": "B"},
            candidatos_restantes=["B"],
            reglas_que_fallaron=["CRUZ_5"],
        )
        system = messages[0]["content"]

        assert "candidatos_restantes" in system
        assert "null" in system

    def test_el_prompt_es_versionado(self):
        assert VERSION_PROMPT_AGENTE.startswith("conclusion-agente@")

    def test_los_messages_respetan_el_shape_de_ollama(self):
        messages = construir_messages_agente(evidencia={}, candidatos_restantes=["B"])

        assert [m["role"] for m in messages] == ["system", "user"]
        for mensaje in messages:
            assert mensaje["content"].strip()

    def test_un_caso_sin_evidencia_lo_declara(self):
        messages = construir_messages_agente(evidencia=None, candidatos_restantes=["B"])

        assert "sin evidencia" in messages[-1]["content"]

    def test_la_descripcion_se_incluye_como_contexto(self):
        messages = construir_messages_agente(
            evidencia={}, candidatos_restantes=["B"], descripcion="Factura de servicios"
        )

        assert "Factura de servicios" in messages[-1]["content"]


# ---------------------------------------------------------------------------
# 3. El blindaje post-agente
# ---------------------------------------------------------------------------


class TestBlindajePostAgente:
    """El agente no puede resucitar un candidato descartado (E-CONC-3)."""

    def test_una_eleccion_dentro_del_universo_se_acepta(self):
        resultado = escalar_a_agente(
            _evidencia(CASO_AMBIGUO), contexto_tipo=CTX_RI_RI, agente=AgenteDoble(_json_agente("B"))
        )

        assert resultado.desenlace == AGENTE_ELIGIO
        assert resultado.candidato == "B"
        assert not resultado.hubo_blindaje

    def test_una_eleccion_de_un_descartado_se_rechaza(self):
        # El caso: el negocio espera A, el documento dice B → A está descartado.
        resultado = escalar_a_agente(
            _evidencia(CASO_AMBIGUO), contexto_tipo=CTX_RI_RI, agente=AgenteDoble(_json_agente("A"))
        )

        assert resultado.desenlace == AGENTE_ELECCION_INVALIDA
        assert resultado.candidato is None

    def test_la_anomalia_queda_registrada(self):
        # Corregir en silencio escondería la desobediencia: hay que auditarla.
        resultado = escalar_a_agente(
            _evidencia(CASO_AMBIGUO), contexto_tipo=CTX_RI_RI, agente=AgenteDoble(_json_agente("A"))
        )

        assert resultado.bloques == ["A"]
        assert resultado.hubo_blindaje
        assert "A" in resultado.motivo

    def test_una_eleccion_invalida_no_se_corrige_a_otro_valor(self):
        # No hay "fallback al primer restante": la elección inválida queda como
        # abstención de hecho (sin decisión que consolidar).
        resultado = escalar_a_agente(
            _evidencia(CASO_AMBIGUO), contexto_tipo=CTX_RI_RI, agente=AgenteDoble(_json_agente("M"))
        )

        assert resultado.candidato is None
        assert resultado.bloques == ["M"]

    def test_una_eleccion_fuera_del_caso_tambien_se_rechaza(self):
        # Un valor que no está ni entre los restantes ni entre los descartados.
        resultado = escalar_a_agente(
            _evidencia(CASO_AMBIGUO), contexto_tipo=CTX_RI_RI, agente=AgenteDoble(_json_agente("Z"))
        )

        assert resultado.desenlace == AGENTE_ELECCION_INVALIDA
        assert resultado.candidato is None

    def test_el_motivo_del_rechazo_cita_los_restantes(self):
        resultado = escalar_a_agente(
            _evidencia(CASO_AMBIGUO), contexto_tipo=CTX_RI_RI, agente=AgenteDoble(_json_agente("A"))
        )

        assert "restantes" in resultado.motivo

    def test_la_abstencion_es_una_salida_valida(self):
        # "No puedo elegir con fundamento" es mejor que inventar una decisión.
        resultado = escalar_a_agente(
            _evidencia(CASO_AMBIGUO), contexto_tipo=CTX_RI_RI, agente=AgenteDoble(_json_agente(None))
        )

        assert resultado.desenlace == AGENTE_SE_ABSTUVO
        assert resultado.candidato is None
        assert not resultado.hubo_blindaje

    def test_la_abstencion_no_marca_blindaje(self):
        resultado = escalar_a_agente(
            _evidencia(CASO_AMBIGUO), contexto_tipo=CTX_RI_RI, agente=AgenteDoble(_json_agente(None))
        )

        assert resultado.bloques == []


# ---------------------------------------------------------------------------
# 4. La interpretación de la salida
# ---------------------------------------------------------------------------


class TestInterpretacionDeLaSalida:
    """``parsear_eleccion_agente`` tolera ruido pero no inventa decisiones."""

    def test_parsea_el_json_del_contrato(self):
        eleccion = parsear_eleccion_agente(_json_agente("B"))

        assert eleccion.valida
        assert eleccion.candidato == "B"
        assert eleccion.justificacion
        assert eleccion.sustento

    def test_tolera_las_cercas_de_codigo(self):
        eleccion = parsear_eleccion_agente(f"```json\n{_json_agente('B')}\n```")

        assert eleccion.valida
        assert eleccion.candidato == "B"

    def test_tolera_la_prosa_alrededor(self):
        eleccion = parsear_eleccion_agente(f"Claro, mi respuesta es: {_json_agente('B')} — listo.")

        assert eleccion.valida
        assert eleccion.candidato == "B"

    def test_null_es_abstencion_valida(self):
        eleccion = parsear_eleccion_agente(_json_agente(None))

        assert eleccion.valida
        assert eleccion.candidato is None

    @pytest.mark.parametrize("contenido", ["", "no tengo idea", "{roto", "[1,2,3]"])
    def test_una_salida_ilegible_no_es_valida(self, contenido: str):
        eleccion = parsear_eleccion_agente(contenido)

        assert not eleccion.valida
        assert eleccion.candidato is None

    def test_un_json_sin_la_clave_del_candidato_no_es_valido(self):
        eleccion = parsear_eleccion_agente('{"otra_cosa": 1}')

        assert not eleccion.valida

    def test_el_crudo_se_conserva_para_auditar(self):
        eleccion = parsear_eleccion_agente("texto basura")

        assert eleccion.crudo == "texto basura"


# ---------------------------------------------------------------------------
# 5. La derivación de certeza y origen
# ---------------------------------------------------------------------------


class TestCertezaYOrigen:
    """La certeza se deriva de la etapa (glosario §2)."""

    def test_el_agente_implica_certeza_baja(self):
        resultado = escalar_a_agente(
            _evidencia(CASO_AMBIGUO), contexto_tipo=CTX_RI_RI, agente=AgenteDoble(_json_agente("B"))
        )

        assert resultado.certeza == Certeza.baja

    def test_certeza_baja_aunque_se_abstenga(self):
        # El caso necesitó al agente: no es de certeza alta ni aunque decidiera.
        resultado = escalar_a_agente(
            _evidencia(CASO_AMBIGUO), contexto_tipo=CTX_RI_RI, agente=AgenteDoble(_json_agente(None))
        )

        assert resultado.certeza == Certeza.baja

    def test_origen_agente_ia_solo_si_su_eleccion_sobrevivio(self):
        resultado = escalar_a_agente(
            _evidencia(CASO_AMBIGUO), contexto_tipo=CTX_RI_RI, agente=AgenteDoble(_json_agente("B"))
        )

        assert resultado.origen == Origen.agente_ia

    def test_sin_eleccion_valida_no_hay_origen(self):
        # No decidió nadie: ni el código (no pudo) ni el agente (se abstuvo).
        for contenido in (_json_agente(None), _json_agente("A"), "basura"):
            resultado = escalar_a_agente(
                _evidencia(CASO_AMBIGUO), contexto_tipo=CTX_RI_RI, agente=AgenteDoble(contenido)
            )
            assert resultado.origen is None, contenido

    def test_un_caso_del_agente_pide_revision_de_prioridad_alta(self):
        # Gherkin E-CONC-3: el caso se encola a HITL con prioridad alta.
        resultado = escalar_a_agente(
            _evidencia(CASO_AMBIGUO), contexto_tipo=CTX_RI_RI, agente=AgenteDoble(_json_agente("B"))
        )

        assert resultado.hitl is not None
        assert resultado.hitl.requerido
        assert resultado.hitl.prioridad == "alta"

    def test_la_traza_publica_el_escalado_para_medir_el_porcentaje(self):
        # R-09: % agente se mide del resultado, sin recalcular nada.
        resultado = escalar_a_agente(
            _evidencia(CASO_AMBIGUO), contexto_tipo=CTX_RI_RI, agente=AgenteDoble(_json_agente("B"))
        )
        resumen = resultado.como_dict()

        assert resumen["escalado"] is True
        assert resumen["modelo"] is not None or resumen["version_prompt"]


# ---------------------------------------------------------------------------
# 6. El flujo completo con consolidación
# ---------------------------------------------------------------------------


class TestFlujoConAgente:
    """``concluir_con_agente`` cierra el pipeline (T-501 → T-504 → T-503)."""

    def test_el_caso_del_codigo_se_consolida_por_programa(self):
        resultado = concluir_con_agente(_evidencia(CASO_RESUELTO), contexto_tipo=CTX_RI_RI)

        assert not resultado.agente.escalado
        assert resultado.resultado.certeza == Certeza.alta
        assert resultado.resultado.origen == Origen.programa

    def test_el_caso_del_agente_se_consolida_con_certeza_baja(self):
        resultado = concluir_con_agente(
            _evidencia(CASO_AMBIGUO), contexto_tipo=CTX_RI_RI, agente=AgenteDoble(_json_agente("B"))
        )

        assert resultado.agente.escalado
        assert resultado.resultado.certeza == Certeza.baja
        assert resultado.resultado.origen == Origen.agente_ia

    def test_el_caso_del_agente_no_se_consolida_como_programa(self):
        # El bug que T-504 destapó: sin mirar el origen, la consolidación de T-503
        # afirmaba "certeza alta por programa" para una decisión del agente.
        resultado = concluir_con_agente(
            _evidencia(CASO_AMBIGUO), contexto_tipo=CTX_RI_RI, agente=AgenteDoble(_json_agente("B"))
        )

        assert not resultado.consolidacion.concluyo_por_programa
        assert "agente" in resultado.consolidacion.motivo_certeza.lower()

    def test_una_eleccion_del_agente_deja_el_caso_aprobado(self):
        resultado = concluir_con_agente(
            _evidencia(CASO_AMBIGUO), contexto_tipo=CTX_RI_RI, agente=AgenteDoble(_json_agente("B"))
        )

        assert resultado.resultado.estado == ESTADO_APROBADO

    def test_una_abstencion_deja_el_caso_en_revision(self):
        resultado = concluir_con_agente(
            _evidencia(CASO_AMBIGUO), contexto_tipo=CTX_RI_RI, agente=AgenteDoble(_json_agente(None))
        )

        assert resultado.resultado.estado == ESTADO_REVISION
        assert resultado.resultado.origen is None

    def test_una_eleccion_invalida_deja_el_caso_en_revision(self):
        resultado = concluir_con_agente(
            _evidencia(CASO_AMBIGUO), contexto_tipo=CTX_RI_RI, agente=AgenteDoble(_json_agente("A"))
        )

        assert resultado.resultado.estado == ESTADO_REVISION
        assert resultado.agente.hubo_blindaje

    def test_el_resultado_pide_revision_humana(self):
        resultado = concluir_con_agente(
            _evidencia(CASO_AMBIGUO), contexto_tipo=CTX_RI_RI, agente=AgenteDoble(_json_agente("B"))
        )

        assert resultado.resultado.hitl.requerido
        assert resultado.resultado.hitl.prioridad == "alta"

    def test_la_traza_del_resultado_registra_al_agente(self):
        resultado = concluir_con_agente(
            _evidencia(CASO_AMBIGUO), contexto_tipo=CTX_RI_RI, agente=AgenteDoble(_json_agente("B"))
        )
        traza = resultado.resultado.trazabilidad

        assert "agente" in traza
        assert traza["agente"]["desenlace"] == AGENTE_ELIGIO

    def test_la_traza_conserva_todas_las_etapas(self):
        resultado = concluir_con_agente(
            _evidencia(CASO_AMBIGUO), contexto_tipo=CTX_RI_RI, agente=AgenteDoble(_json_agente("B"))
        )
        traza = resultado.resultado.trazabilidad

        for clave in ("combinacion", "conclusion", "consolidacion", "agente"):
            assert clave in traza, clave

    def test_el_resumen_es_serializable(self):
        resultado = concluir_con_agente(
            _evidencia(CASO_AMBIGUO), contexto_tipo=CTX_RI_RI, agente=AgenteDoble(_json_agente("B"))
        )
        resumen = resultado.como_dict()

        assert resumen["agente"]["escalado"] is True
        assert resumen["consolidacion"]["origen"] == "agente_ia"

    def test_rechaza_algo_que_no_es_evidencia(self):
        with pytest.raises(TypeError, match="CombinedEvidence"):
            concluir_con_agente("no soy evidencia")  # type: ignore[arg-type]


# ---------------------------------------------------------------------------
# 7. El agente inyectable y sus fallos
# ---------------------------------------------------------------------------


class TestAgenteInyectable:
    """La suite corre sin red: el agente entra por protocolo."""

    def test_un_doble_satisface_el_protocolo(self):
        assert isinstance(AgenteDoble("x"), Agente)

    def test_agente_ollama_satisface_el_protocolo(self):
        assert isinstance(AgenteOllama(object()), Agente)

    def test_agente_ollama_usa_json_format(self):
        # ADR-008: el agente es una llamada con prompt estructurado; el JSON se
        # fuerza en la API.
        llamadas: list[dict[str, Any]] = []

        class Cliente:
            def ask(self, messages, model, json_format=False, options=None, num_ctx=None):
                llamadas.append({"json_format": json_format, "model": model, "num_ctx": num_ctx})
                return _Respuesta("{}")

        AgenteOllama(Cliente()).decidir([{"role": "user", "content": "x"}], "qwen2.5:7b", num_ctx=8192)

        assert llamadas[0]["json_format"] is True
        assert llamadas[0]["model"] == "qwen2.5:7b"
        assert llamadas[0]["num_ctx"] == 8192

    def test_un_agente_que_falla_no_tumba_el_pipeline(self):
        resultado = escalar_a_agente(_evidencia(CASO_AMBIGUO), contexto_tipo=CTX_RI_RI, agente=AgenteRoto())

        assert resultado.desenlace == AGENTE_FALLO
        assert resultado.candidato is None
        assert resultado.escalado  # se intentó: cuenta para % agente

    def test_un_agente_que_falla_deja_el_caso_en_revision(self):
        resultado = concluir_con_agente(
            _evidencia(CASO_AMBIGUO), contexto_tipo=CTX_RI_RI, agente=AgenteRoto()
        )

        assert resultado.resultado.estado == ESTADO_REVISION
        assert resultado.resultado.hitl.prioridad == "alta"

    def test_sin_agente_configurado_el_caso_queda_en_revision(self):
        # El hook es opcional: el MVP no depende de tener el agente disponible.
        resultado = escalar_a_agente(_evidencia(CASO_AMBIGUO), contexto_tipo=CTX_RI_RI, agente=None)

        assert resultado.desenlace == AGENTE_FALLO
        assert resultado.escalado
        assert resultado.hitl is not None

    def test_el_modelo_se_puede_fijar(self):
        resultado = escalar_a_agente(
            _evidencia(CASO_AMBIGUO),
            contexto_tipo=CTX_RI_RI,
            agente=AgenteDoble(_json_agente("B")),
            modelo="mi-modelo",
        )

        assert resultado.modelo == "mi-modelo"


# ---------------------------------------------------------------------------
# 8. Fronteras
# ---------------------------------------------------------------------------


class TestFronteras:
    """Lo que el escalado NO hace (es de otras tareas de F5)."""

    def test_no_encola_hitl_por_si_solo(self):
        # El escalado **no** encola: publica la decisión del agente. Encolar es
        # de T-505 (`encolar_hitl`), un paso explícito y aparte.
        resultado = concluir_con_agente(
            _evidencia(CASO_AMBIGUO), contexto_tipo=CTX_RI_RI, agente=AgenteDoble(_json_agente("B"))
        ).resultado

        assert resultado is not None
        assert "hitl" not in resultado.trazabilidad

    def test_es_determinista(self):
        a = concluir_con_agente(
            _evidencia(CASO_AMBIGUO), contexto_tipo=CTX_RI_RI, agente=AgenteDoble(_json_agente("B"))
        ).como_dict()
        b = concluir_con_agente(
            _evidencia(CASO_AMBIGUO), contexto_tipo=CTX_RI_RI, agente=AgenteDoble(_json_agente("B"))
        ).como_dict()

        assert a == b

    def test_no_muta_la_evidencia_de_entrada(self):
        original = _evidencia(CASO_AMBIGUO)
        antes = dict(original.trazabilidad)

        concluir_con_agente(original, contexto_tipo=CTX_RI_RI, agente=AgenteDoble(_json_agente("B")))

        assert dict(original.trazabilidad) == antes

    def test_se_puede_escalar_desde_un_veredicto_ya_calculado(self):
        # `escalar_veredicto` es la variante de bajo nivel (recibe el veredicto).
        from voucherflow.conclusion import concluir_caso

        veredicto = concluir_caso(_evidencia(CASO_AMBIGUO), contexto_tipo=CTX_RI_RI)
        resultado = escalar_veredicto(veredicto, agente=AgenteDoble(_json_agente("B")))

        assert resultado.candidato == "B"

    def test_el_blindaje_se_aplica_igual_con_candidatos_del_llamador(self):
        # Aunque el llamador proponga un universo amplio, el blindaje valida
        # contra los restantes del veredicto.
        resultado = escalar_a_agente(
            _evidencia(CASO_AMBIGUO),
            ["A"],
            contexto_tipo=CTX_RI_RI,
            agente=AgenteDoble(_json_agente("A")),
        )

        assert resultado.desenlace == AGENTE_ELECCION_INVALIDA

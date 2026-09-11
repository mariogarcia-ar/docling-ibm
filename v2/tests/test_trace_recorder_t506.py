"""Tests de la trazabilidad `CaseRecord` persistida (F5/T-506, E-CONC-5).

**DoD de T-506** (F5.md §3, E-CONC-5 / ADR-005 / ADR-009): "Todo caso persiste su
`CaseRecord` (prompts/modelos/reglas/decisión) en sidecar + índice, base de la
auditoría".

Los dos escenarios del Gherkin:

    Dado un caso procesado
    Cuando se consulta su trazabilidad
    Entonces incluye versión de prompt, modelo usado, evidencia de cada fuente,
    reglas disparadas y origen de la decisión (programa | agente_ia | hitl)

    Regla: persistencia
      Dado cualquier caso consolidado
      Cuando se guarda el resultado
      Entonces la trazabilidad se persiste junto al resultado (JSON sidecar o
      store)

Qué se verifica, en el orden del entregable:

1. **El registro auditable** (`construir_case_record`): los cinco datos del
   Gherkin se pueden leer del `CaseRecord` **sin volver a correr el pipeline**
   (versión de prompt, modelo, evidencia por fuente, reglas y quién decidió).
2. **Nada se inventa**: lo que la corrida no usó no aparece (sin claves falsas);
   el registro declara si la evidencia por fuente es exacta o reconstruida.
3. **El sidecar**: escritura atómica, round-trip sin pérdida al contrato congelado
   y versionado del formato.
4. **El índice**: una línea por caso, consultable y, sobre todo, **una fila por
   documento** (re-procesar no duplica: si duplicara, inflaría las métricas).
5. **La reconstrucción** (`reindexar`): el índice es un derivado y se regenera
   desde los sidecars sin pérdida.
6. **La resiliencia**: un índice corrupto o ausente no pierde el histórico; un
   sidecar ilegible no tumba la reconstrucción.
7. **Fronteras**: sin red, determinístico, y los errores explícitos.

Reglas duras: suite default **sin** Ollama, **sin** Docling y **sin** red.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from voucherflow.conclusion import ColaHitl, consolidar_caso, encolar_hitl
from voucherflow.extraction.flows import combinar_evidencia
from voucherflow.rules.contexto import ContextoTipoComprobante
from voucherflow.schemas.evidence import (
    Certeza,
    EvidenceField,
    Fuente,
    Origen,
    SourceEvidence,
    nueva_meta,
)
from voucherflow.schemas.result import CaseRecord, EstadoResultado, SCHEMA_VERSION
from voucherflow.settings.config import HitlSettings
from voucherflow.trace import (
    CAMPOS_INDICE,
    ETAPA_CONCLUSION,
    NOMBRE_INDICE,
    VERSION_TRAZA,
    CaseRecorder,
    ResultadoPersistencia,
    construir_case_record,
    evidencia_por_fuente,
    modelos_y_prompts,
    reglas_disparadas,
    resumen_case_record,
    sidecar_para,
)

# ---------------------------------------------------------------------------
# Utilidades
# ---------------------------------------------------------------------------

CTX_RI_RI = ContextoTipoComprobante(
    emisor_condicion_fiscal="Responsable Inscripto",
    receptor_condicion_fiscal="Responsable Inscripto",
)

BASE: dict[str, Any] = {
    "fecha_emision": "2025-08-14",
    "nro_comprobante": "0001-00000001",
    "importe_total_facturado": "121.00",
}

#: Factura A coherente: el código concluye con certeza alta.
A_COMPLETA: dict[str, Any] = {
    **BASE,
    "tipo_comprobante": "A",
    "cuit_emisor": "30-12345678-9",
    "cuit_receptor": "27-12345678-4",
    "subtotal": "100.00",
    "iva": "21.00",
}

#: Factura B con emisor y receptor RI: el código la deja en revisión (R7).
AMBIGUO: dict[str, Any] = {
    **BASE,
    "tipo_comprobante": "B",
    "cuit_emisor": "30-12345678-9",
    "iva": "0.00",
}

MODELO_VLM = "qwen2.5vl:3b"
MODELO_LLM = "qwen2.5:7b"
PROMPT = "extraccion-key-value@1"


def _source(
    fuente: Fuente,
    campos: dict[str, Any],
    *,
    modelo: str | None = None,
    version_prompt: str | None = None,
    valida: bool = True,
    reglas: list[str] | None = None,
    debilidades: list[str] | None = None,
) -> SourceEvidence:
    return SourceEvidence(
        fuente=fuente,
        valida=valida,
        reglas_aplicadas=list(reglas or []),
        debilidades=list(debilidades or []),
        campos={
            campo: EvidenceField(
                campo=campo,
                valor=valor,
                fuente=fuente,
                fragmento_sustento=f"soporte de {campo}",
                meta=nueva_meta(modelo, version_prompt),
            )
            for campo, valor in campos.items()
        },
    )


def _evidencia(campos: dict[str, Any], *, documento_id: str = "doc-1") -> Any:
    return combinar_evidencia(
        documento_id,
        [
            _source(
                Fuente.vlm,
                campos,
                modelo=MODELO_VLM,
                version_prompt=PROMPT,
                reglas=["R1", "R4"],
            ),
            _source(Fuente.llm, campos, modelo=MODELO_LLM, version_prompt=PROMPT),
        ],
    )


def _fuentes(campos: dict[str, Any]) -> list[SourceEvidence]:
    return [
        _source(
            Fuente.vlm,
            campos,
            modelo=MODELO_VLM,
            version_prompt=PROMPT,
            reglas=["R1", "R4"],
            debilidades=["campo borroso"],
        ),
        _source(Fuente.llm, campos, modelo=MODELO_LLM, version_prompt=PROMPT),
    ]


def _caso(
    campos: dict[str, Any] | None = None,
    *,
    documento_id: str = "doc-1",
    contexto: ContextoTipoComprobante | None = CTX_RI_RI,
    archivo: str | None = None,
    con_fuentes: bool = False,
) -> CaseRecord:
    campos = A_COMPLETA if campos is None else campos
    evidencia = _evidencia(campos, documento_id=documento_id)
    consolidado = consolidar_caso(evidencia, contexto_tipo=contexto)
    return construir_case_record(
        evidencia,
        resultado=consolidado.valor,
        archivo=archivo,
        fuentes=_fuentes(campos) if con_fuentes else None,
    )


# ---------------------------------------------------------------------------
# 1. El registro auditable (el Gherkin)
# ---------------------------------------------------------------------------


class TestRegistroAuditable:
    """Los cinco datos que una auditoría pide, leídos del registro."""

    def test_responde_version_de_prompt(self):
        # Gherkin: "Entonces incluye versión de prompt".
        caso = _caso()

        assert caso.version_prompt["vlm"] == PROMPT
        assert caso.version_prompt["llm"] == PROMPT

    def test_responde_modelo_usado(self):
        # Gherkin: "modelo usado".
        caso = _caso()

        assert caso.modelo_por_etapa["vlm"] == MODELO_VLM
        assert caso.modelo_por_etapa["llm"] == MODELO_LLM

    def test_responde_evidencia_de_cada_fuente(self):
        # Gherkin: "evidencia de cada fuente".
        caso = _caso()

        assert set(caso.evidencia_por_fuente) == {"vlm", "llm"}
        assert len(caso.evidencia_por_fuente["vlm"].campos) == len(A_COMPLETA)

    def test_responde_las_reglas_disparadas(self):
        # Gherkin: "reglas disparadas".
        caso = _caso()

        assert caso.reglas_disparadas  # la pasada 2 concluyó el caso
        assert any(r.startswith("CRUZ") for r in caso.reglas_disparadas)

    def test_responde_quien_decidio(self):
        # Gherkin: "origen de la decisión (programa | agente_ia | hitl)".
        caso = _caso()

        assert caso.quien_decidio == Origen.programa

    def test_responde_quien_decidio_cuando_lo_decidio_el_agente(self):
        from voucherflow.conclusion import concluir_con_agente

        class AgenteDoble:
            def decidir(self, messages, modelo, *, num_ctx=None):
                class R:
                    contenido = json.dumps({"candidato": "B", "justificacion": "x"})

                return R()

        evidencia = _evidencia(AMBIGUO)
        corrida = concluir_con_agente(
            evidencia, contexto_tipo=CTX_RI_RI, agente=AgenteDoble(), modelo="m"
        )
        caso = construir_case_record(evidencia, resultado=corrida.resultado)

        assert caso.quien_decidio == Origen.agente_ia
        assert caso.modelo_por_etapa.get("agente") == "m"

    def test_el_registro_es_autosuficiente(self):
        # La prueba del Gherkin: se puede responder todo desde el registro,
        # sin volver a correr el pipeline (no hay acceso a la evidencia acá).
        caso = _caso()
        resumen = resumen_case_record(caso)

        assert resumen["version_prompt"]
        assert resumen["modelo_por_etapa"]
        assert resumen["evidencia_por_fuente"]
        assert resumen["reglas_disparadas"]
        assert resumen["quien_decidio"] == "programa"

    def test_incluye_el_resultado_consolidado(self):
        # "la trazabilidad se persiste **junto al resultado**" (regla del Gherkin).
        caso = _caso()

        assert caso.resultado is not None
        assert caso.resultado.estado == EstadoResultado.aprobado
        assert caso.resultado.certeza == Certeza.alta

    def test_incluye_la_etapa_de_conclusion(self):
        caso = _caso()

        assert [registro.etapa for registro in caso.etapas] == [ETAPA_CONCLUSION]

    def test_el_detalle_de_la_etapa_hace_el_sidecar_autosuficiente(self):
        # Un auditor no debería tener que re-correr el pipeline para ver por qué.
        caso = _caso()
        detalle = caso.etapas[0].detalle

        assert "valor_vigente_por_campo" in detalle
        assert "conclusion" in detalle
        assert "consolidacion" in detalle

    def test_registra_el_caso_ambiguo_sin_origen(self):
        # Un caso sin veredicto firme también se persiste: es el más interesante
        # de auditar. No lo decidió nadie, así que `quien_decidio` es None.
        caso = _caso(AMBIGUO)

        assert caso.quien_decidio is None
        assert caso.resultado.estado == EstadoResultado.revision

    def test_registra_el_estado_de_la_cola_hitl(self):
        evidencia = _evidencia(AMBIGUO)
        resultado = consolidar_caso(evidencia, contexto_tipo=CTX_RI_RI).valor
        encolar_hitl(resultado, cola=ColaHitl(hitl=HitlSettings(muestreo_tasa=0.0)))

        caso = construir_case_record(evidencia, resultado=resultado)

        assert caso.resultado.hitl.requerido is True
        assert "hitl" in caso.etapas[0].detalle


# ---------------------------------------------------------------------------
# 2. Nada se inventa
# ---------------------------------------------------------------------------


class TestHonestidadDelRegistro:
    """Lo que la corrida no usó no aparece en el registro."""

    def test_sin_agente_no_hay_modelo_de_agente(self):
        caso = _caso()

        assert "agente" not in caso.modelo_por_etapa
        assert caso.etapas[0].modelo is None

    def test_una_etapa_no_ejecutada_no_inventa_su_prompt(self):
        # El registry no incluye prompts de etapas que no corrieron.
        caso = _caso()

        assert "qween" not in " ".join(caso.version_prompt)
        assert "agente" not in caso.version_prompt

    def test_el_registro_declara_como_armo_la_evidencia(self):
        # Reconstruida desde los campos vs. directa: es una diferencia de
        # fidelidad y el registro la declara en vez de aparentar.
        reconstruido = _caso(con_fuentes=False)
        directo = _caso(con_fuentes=True)

        assert (
            reconstruido.etapas[0].detalle["construccion"]["evidencia_por_fuente"]
            == "reconstruida_desde_campos"
        )
        assert (
            directo.etapas[0].detalle["construccion"]["evidencia_por_fuente"] == "directa"
        )

    def test_directo_conserva_el_nivel_de_fuente_y_el_reconstruido_no(self):
        # `valida`/`debilidades` viven en la pasada 1 y no viajan en la evidencia
        # combinada: por eso el registro declara la diferencia.
        directo = _caso(con_fuentes=True)
        reconstruido = _caso(con_fuentes=False)

        assert directo.evidencia_por_fuente["vlm"].debilidades == ["campo borroso"]
        assert reconstruido.evidencia_por_fuente["vlm"].debilidades == []

    def test_las_lecturas_son_exactas_en_ambos_caminos(self):
        # Lo que sí es idéntico: las lecturas por campo (valor, meta, sostén).
        directo = _caso(con_fuentes=True)
        reconstruido = _caso(con_fuentes=False)

        assert (
            directo.evidencia_por_fuente["vlm"].campos.keys()
            == reconstruido.evidencia_por_fuente["vlm"].campos.keys()
        )
        assert (
            reconstruido.evidencia_por_fuente["vlm"].campos["iva"].meta["modelo"]
            == MODELO_VLM
        )

    def test_un_campo_que_nadie_declaro_no_aparece(self):
        # No declarar un campo no es declararlo vacío.
        evidencia = _evidencia({"tipo_comprobante": "A", **BASE})
        caso = construir_case_record(evidencia)

        campos = caso.evidencia_por_fuente["vlm"].campos
        assert "cuit_receptor" not in campos

    def test_sin_resultado_el_registro_igual_sirve(self):
        # Persistir un caso cuya evidencia todavía no se concluyó es un caso
        # contemplado: la trazabilidad de las etapas es el dato, el resultado
        # puede faltar. Nada se inventa (ni quién decidió ni qué reglas).
        caso = construir_case_record(_evidencia(A_COMPLETA))

        assert caso.resultado is None
        assert caso.quien_decidio is None
        assert caso.reglas_disparadas == []

    def test_con_la_decision_adjunta_si_responde_quien_decidio(self):
        # Cuando la pasada 2 (T-501) ya concluyó, la decisión viaja en la
        # evidencia y el registro la toma de ahí aunque no haya `VoucherResult`.
        from voucherflow.conclusion import concluir

        evidencia = concluir(_evidencia(A_COMPLETA), CTX_RI_RI)
        caso = construir_case_record(evidencia)

        assert caso.resultado is None
        assert caso.quien_decidio == Origen.programa
        assert caso.reglas_disparadas


# ---------------------------------------------------------------------------
# 3. El sidecar
# ---------------------------------------------------------------------------


class TestSidecar:
    """Persistencia atómica y round-trip sin pérdida."""

    def test_escribe_el_sidecar(self, tmp_path: Path):
        recorder = CaseRecorder(tmp_path)
        resultado = recorder.registrar(_caso())

        assert isinstance(resultado, ResultadoPersistencia)
        assert resultado.sidecar.is_file()
        assert resultado.sidecar.name == sidecar_para("doc-1")
        assert resultado.ok is True

    def test_el_nombre_del_sidecar_es_el_canonico(self, tmp_path: Path):
        recorder = CaseRecorder(tmp_path)

        assert recorder.registrar(_caso()).sidecar.name == "doc-1.case.json"

    def test_el_sidecar_es_un_json_valido(self, tmp_path: Path):
        recorder = CaseRecorder(tmp_path)
        ruta = recorder.registrar(_caso()).sidecar

        datos = json.loads(ruta.read_text(encoding="utf-8"))

        assert datos["documento_id"] == "doc-1"
        assert datos["_persistencia"]["version"] == VERSION_TRAZA

    def test_round_trip_sin_perdida(self, tmp_path: Path):
        # La auditoría exige reconstruir el caso desde su registro.
        recorder = CaseRecorder(tmp_path)
        original = _caso()
        recorder.registrar(original)

        vuelto = recorder.leer("doc-1")

        assert vuelto.model_dump() == original.model_dump()

    def test_el_round_trip_conserva_lo_que_la_auditoria_necesita(self, tmp_path: Path):
        recorder = CaseRecorder(tmp_path)
        recorder.registrar(_caso(con_fuentes=True))

        vuelto = recorder.leer("doc-1")
        resumen = resumen_case_record(vuelto)

        assert resumen["quien_decidio"] == "programa"
        assert resumen["version_prompt"]["vlm"] == PROMPT
        assert resumen["modelo_por_etapa"]["vlm"] == MODELO_VLM
        assert resumen["reglas_disparadas"]

    def test_la_escritura_es_atomica_y_no_deja_temporales(self, tmp_path: Path):
        recorder = CaseRecorder(tmp_path)
        recorder.registrar(_caso())

        basura = [p.name for p in tmp_path.iterdir() if p.name.endswith(".tmp")]

        assert basura == []

    def test_no_deja_un_sidecar_a_medias_si_falla_la_escritura(self, tmp_path: Path):
        # Si el registro no se puede escribir, el sidecar anterior queda intacto.
        recorder = CaseRecorder(tmp_path)
        recorder.registrar(_caso())
        antes = recorder.ruta_sidecar("doc-1").read_text(encoding="utf-8")

        # Un caso que no se puede serializar (no es un CaseRecord) debe fallar.
        with pytest.raises(AttributeError):
            recorder.registrar("no soy un CaseRecord")  # type: ignore[arg-type]

        assert recorder.ruta_sidecar("doc-1").read_text(encoding="utf-8") == antes

    def test_reprocesar_sobrescribe_solo_su_sidecar(self, tmp_path: Path):
        recorder = CaseRecorder(tmp_path)
        recorder.registrar(_caso(documento_id="doc-1"))
        recorder.registrar(_caso(A_COMPLETA, documento_id="doc-2"))
        recorder.registrar(_caso(AMBIGUO, documento_id="doc-1"))

        assert recorder.leer("doc-1").resultado.estado == EstadoResultado.revision
        assert recorder.leer("doc-2").resultado.estado == EstadoResultado.aprobado
        assert len(recorder.sidecars()) == 2

    def test_el_sidecar_lleva_la_version_del_contrato(self, tmp_path: Path):
        recorder = CaseRecorder(tmp_path)
        recorder.registrar(_caso())

        assert recorder.leer("doc-1").schema_version == SCHEMA_VERSION

    def test_un_documento_sin_sidecar_es_un_error_explicito(self, tmp_path: Path):
        with pytest.raises(FileNotFoundError):
            CaseRecorder(tmp_path).leer("nope")

    def test_un_id_con_dos_puntos_no_rompe_el_nombre_de_archivo(self, tmp_path: Path):
        # Los `documento_id` reales son `sha256:…`.
        recorder = CaseRecorder(tmp_path)
        resultado = recorder.registrar(_caso(documento_id="sha256:abc123"))

        assert resultado.sidecar.name == "sha256_abc123.case.json"
        assert recorder.leer("sha256:abc123").documento_id == "sha256:abc123"

    def test_un_id_con_barras_no_escapa_del_directorio(self, tmp_path: Path):
        recorder = CaseRecorder(tmp_path)
        resultado = recorder.registrar(_caso(documento_id="../../afuera"))

        assert resultado.sidecar.parent == tmp_path

    def test_registrar_un_lote(self, tmp_path: Path):
        recorder = CaseRecorder(tmp_path)

        resultados = recorder.registrar_lote(
            [_caso(documento_id=f"doc-{i}") for i in range(3)]
        )

        assert len(resultados) == 3
        assert all(r.ok for r in resultados)
        assert len(recorder.sidecars()) == 3


# ---------------------------------------------------------------------------
# 4. El índice
# ---------------------------------------------------------------------------


class TestIndice:
    """El derivado que hace consultable el histórico."""

    def test_escribe_una_linea_por_caso(self, tmp_path: Path):
        recorder = CaseRecorder(tmp_path)
        recorder.registrar(_caso(documento_id="doc-1"))
        recorder.registrar(_caso(documento_id="doc-2"))

        assert recorder.indice.name == NOMBRE_INDICE
        assert len(recorder.leer_indice()) == 2

    def test_la_fila_tiene_los_campos_canonicos(self, tmp_path: Path):
        recorder = CaseRecorder(tmp_path)
        recorder.registrar(_caso())

        fila = recorder.leer_indice()[0]

        assert tuple(fila) == CAMPOS_INDICE

    def test_la_fila_resume_lo_que_permite_filtrar(self, tmp_path: Path):
        recorder = CaseRecorder(tmp_path)
        recorder.registrar(_caso())

        fila = recorder.leer_indice()[0]

        assert fila["documento_id"] == "doc-1"
        assert fila["estado"] == "aprobado"
        assert fila["certeza"] == "alta"
        assert fila["quien_decidio"] == "programa"
        assert fila["sidecar"] == "doc-1.case.json"

    def test_es_append_only(self, tmp_path: Path):
        # Agregar un caso no reescribe el índice: es un append.
        recorder = CaseRecorder(tmp_path)
        recorder.registrar(_caso(documento_id="doc-1"))
        primera = recorder.indice.read_text(encoding="utf-8")

        recorder.registrar(_caso(documento_id="doc-2"))

        assert recorder.indice.read_text(encoding="utf-8").startswith(primera)

    def test_una_fila_por_documento_no_por_corrida(self, tmp_path: Path):
        # ADR-005: el CaseRecord es por documento. Si el índice devolviera una
        # fila por corrida, re-procesar inflaría cualquier agregado (T-507).
        recorder = CaseRecorder(tmp_path)
        recorder.registrar(_caso(documento_id="doc-1"))
        recorder.registrar(_caso(documento_id="doc-1"))
        recorder.registrar(_caso(documento_id="doc-1"))

        assert len(recorder.leer_indice(unico=False)) == 3  # líneas físicas
        assert len(recorder.leer_indice()) == 1  # filas lógicas

    def test_la_ultima_corrida_gana(self, tmp_path: Path):
        recorder = CaseRecorder(tmp_path)
        recorder.registrar(_caso(documento_id="doc-1"))
        recorder.registrar(_caso(AMBIGUO, documento_id="doc-1"))

        fila = recorder.leer_indice()[0]

        assert fila["estado"] == "revision"

    def test_buscar_por_estado(self, tmp_path: Path):
        recorder = CaseRecorder(tmp_path)
        recorder.registrar(_caso(documento_id="ok"))
        recorder.registrar(_caso(AMBIGUO, documento_id="revisar"))

        assert [f["documento_id"] for f in recorder.buscar(estado="aprobado")] == ["ok"]
        assert [f["documento_id"] for f in recorder.buscar(estado="revision")] == [
            "revisar"
        ]

    def test_buscar_por_quien_decidio(self, tmp_path: Path):
        recorder = CaseRecorder(tmp_path)
        recorder.registrar(_caso(documento_id="decidido"))
        recorder.registrar(_caso(AMBIGUO, documento_id="sin_decidir"))

        assert len(recorder.buscar(quien_decidio="programa")) == 1
        assert len(recorder.buscar(quien_decidio=None)) == 1

    def test_buscar_por_hitl(self, tmp_path: Path):
        evidencia = _evidencia(AMBIGUO, documento_id="doc-hitl")
        resultado = consolidar_caso(evidencia, contexto_tipo=CTX_RI_RI).valor
        encolar_hitl(resultado, cola=ColaHitl(hitl=HitlSettings(muestreo_tasa=0.0)))

        recorder = CaseRecorder(tmp_path)
        recorder.registrar(construir_case_record(evidencia, resultado=resultado))
        recorder.registrar(_caso(documento_id="doc-ok"))

        filas = recorder.buscar(hitl_requerido=True)

        assert [f["documento_id"] for f in filas] == ["doc-hitl"]
        assert filas[0]["hitl_prioridad"] == "alta"

    def test_buscar_por_varios_filtros(self, tmp_path: Path):
        recorder = CaseRecorder(tmp_path)
        recorder.registrar(_caso(documento_id="a"))
        recorder.registrar(_caso(A_COMPLETA, documento_id="b"))

        assert len(recorder.buscar(estado="aprobado", tipo_comprobante="A")) == 2

    def test_buscar_un_campo_inexistente_es_un_error(self, tmp_path: Path):
        recorder = CaseRecorder(tmp_path)
        recorder.registrar(_caso())

        with pytest.raises(KeyError):
            recorder.buscar(nope=True)

    def test_sin_indice_no_hay_filas(self, tmp_path: Path):
        assert CaseRecorder(tmp_path).leer_indice() == []

    def test_un_indice_corrupto_no_pierde_las_filas_buenas(self, tmp_path: Path):
        recorder = CaseRecorder(tmp_path)
        recorder.registrar(_caso(documento_id="doc-1"))
        with recorder.indice.open("a", encoding="utf-8") as archivo:
            archivo.write("{ esto no es json\n")

        filas = recorder.leer_indice()

        assert [f["documento_id"] for f in filas] == ["doc-1"]

    def test_el_indice_es_legible_linea_por_linea(self, tmp_path: Path):
        recorder = CaseRecorder(tmp_path)
        recorder.registrar(_caso(documento_id="doc-1"))

        linea = recorder.indice.read_text(encoding="utf-8").strip()

        assert json.loads(linea)["documento_id"] == "doc-1"

    def test_un_caso_sin_resultado_se_indexa_igual(self, tmp_path: Path):
        # Un caso sin consolidar es parte del histórico (y de los más
        # interesantes de auditar): se indexa con esos campos en None en lugar
        # de saltarlo.
        recorder = CaseRecorder(tmp_path)
        recorder.registrar(construir_case_record(_evidencia(A_COMPLETA)))

        fila = recorder.leer_indice()[0]

        assert fila["documento_id"] == "doc-1"
        assert fila["estado"] is None
        assert fila["certeza"] is None
        assert fila["quien_decidio"] is None
        assert fila["sidecar"] == "doc-1.case.json"


# ---------------------------------------------------------------------------
# 5. La reconstrucción
# ---------------------------------------------------------------------------


class TestReindexar:
    """El índice es un derivado: se regenera desde los sidecars sin pérdida."""

    def test_reconstruye_un_indice_borrado(self, tmp_path: Path):
        recorder = CaseRecorder(tmp_path)
        recorder.registrar(_caso(documento_id="doc-1"))
        recorder.registrar(_caso(documento_id="doc-2"))
        recorder.indice.unlink()

        filas = recorder.reindexar()

        assert len(filas) == 2
        assert len(recorder.leer_indice()) == 2

    def test_reconstruye_un_indice_corrupto(self, tmp_path: Path):
        recorder = CaseRecorder(tmp_path)
        recorder.registrar(_caso(documento_id="doc-1"))
        recorder.indice.write_text("basura\nbasura\n", encoding="utf-8")

        recorder.reindexar()

        assert [f["documento_id"] for f in recorder.leer_indice()] == ["doc-1"]

    def test_reconstruye_desde_sidecars_de_otra_corrida(self, tmp_path: Path):
        # Un directorio con sidecars pero sin índice se puede indexar.
        CaseRecorder(tmp_path).registrar(_caso(documento_id="doc-1"))
        (tmp_path / NOMBRE_INDICE).unlink()
        otro = CaseRecorder(tmp_path)

        filas = otro.reindexar()

        assert len(filas) == 1
        assert filas[0]["sidecar"] == "doc-1.case.json"

    def test_la_reconstruccion_coincide_con_lo_indexado(self, tmp_path: Path):
        recorder = CaseRecorder(tmp_path)
        recorder.registrar(_caso(documento_id="doc-1"))
        recorder.registrar(_caso(AMBIGUO, documento_id="doc-2"))
        antes = recorder.leer_indice()

        despues = recorder.reindexar()

        assert {f["documento_id"]: f["estado"] for f in despues} == {
            f["documento_id"]: f["estado"] for f in antes
        }

    def test_no_pierde_casos_al_deduplicar(self, tmp_path: Path):
        recorder = CaseRecorder(tmp_path)
        recorder.registrar(_caso(documento_id="doc-1"))
        recorder.registrar(_caso(documento_id="doc-1"))

        filas = recorder.reindexar()

        assert len(filas) == 1

    def test_inserta_lo_que_falta_sin_purgar(self, tmp_path: Path):
        recorder = CaseRecorder(tmp_path)
        recorder.registrar(_caso(documento_id="doc-1"))
        # Un sidecar escrito "por fuera" del registrador de este índice.
        carpeta = CaseRecorder(tmp_path)
        (tmp_path / "doc-9.case.json").write_text(
            (tmp_path / "doc-1.case.json").read_text(encoding="utf-8").replace(
                '"doc-1"', '"doc-9"'
            ),
            encoding="utf-8",
        )

        filas = carpeta.reindexar(purgar=False)

        assert [f["documento_id"] for f in filas] == ["doc-1", "doc-9"]

    def test_un_sidecar_ilegible_no_tumba_la_reconstruccion(self, tmp_path: Path):
        recorder = CaseRecorder(tmp_path)
        recorder.registrar(_caso(documento_id="doc-1"))
        (tmp_path / "roto.case.json").write_text("{ no es json", encoding="utf-8")

        filas = recorder.reindexar()

        assert [f["documento_id"] for f in filas] == ["doc-1"]

    def test_ignora_archivos_que_no_son_sidecars(self, tmp_path: Path):
        recorder = CaseRecorder(tmp_path)
        recorder.registrar(_caso(documento_id="doc-1"))
        (tmp_path / "otra-cosa.json").write_text("{}", encoding="utf-8")

        assert len(recorder.reindexar()) == 1


# ---------------------------------------------------------------------------
# 6. Navegación y lectura directa
# ---------------------------------------------------------------------------


class TestLecturaDirecta:
    """El camino de auditoría que no depende del índice."""

    def test_lista_los_sidecars(self, tmp_path: Path):
        recorder = CaseRecorder(tmp_path)
        recorder.registrar(_caso(documento_id="b"))
        recorder.registrar(_caso(documento_id="a"))

        assert [p.name for p in recorder.sidecars()] == ["a.case.json", "b.case.json"]

    def test_lee_todos_los_casos(self, tmp_path: Path):
        recorder = CaseRecorder(tmp_path)
        recorder.registrar(_caso(documento_id="doc-1"))
        recorder.registrar(_caso(documento_id="doc-2"))

        casos = recorder.casos()

        assert [c.documento_id for c in casos] == ["doc-1", "doc-2"]
        assert all(isinstance(c, CaseRecord) for c in casos)

    def test_la_lectura_directa_no_depende_del_indice(self, tmp_path: Path):
        recorder = CaseRecorder(tmp_path)
        recorder.registrar(_caso(documento_id="doc-1"))
        recorder.indice.unlink()

        assert len(recorder.casos()) == 1

    def test_un_sidecar_ilegible_no_tumba_la_lectura(self, tmp_path: Path):
        recorder = CaseRecorder(tmp_path)
        recorder.registrar(_caso(documento_id="doc-1"))
        (tmp_path / "roto.case.json").write_text("no json", encoding="utf-8")

        assert len(recorder.casos()) == 1


# ---------------------------------------------------------------------------
# 7. Las piezas por separado
# ---------------------------------------------------------------------------


class TestPiezas:
    """Los helpers del constructor, verificables en aislamiento."""

    def test_evidencia_por_fuente_agrupa_por_fuente(self):
        por_fuente = evidencia_por_fuente(_evidencia(A_COMPLETA))

        assert set(por_fuente) == {"vlm", "llm"}
        assert por_fuente["vlm"].fuente == Fuente.vlm

    def test_modelos_y_prompts_no_inventa_claves(self):
        # Una evidencia cuyas lecturas no declaran modelo ni prompt (p. ej. las
        # computadas por programa) no produce claves: no se inventa un modelo.
        sin_meta = combinar_evidencia(
            "doc-1",
            [
                _source(Fuente.programa, {"tipo_comprobante": "A"}),
            ],
        )
        modelos, prompts = modelos_y_prompts(sin_meta)

        assert modelos == {}
        assert prompts == {}

    def test_reglas_disparadas_sin_fuentes_ni_resultado(self):
        # Sin las fuentes de la pasada 1 ni el resultado, solo queda la decisión.
        evidencia = _evidencia(A_COMPLETA)
        assert reglas_disparadas(evidencia) == list(
            evidencia.decision.reglas_aplicadas if evidencia.decision else []
        )

    def test_reglas_disparadas_deduplica_y_conserva_orden(self):
        evidencia = _evidencia(A_COMPLETA)

        reglas = reglas_disparadas(evidencia, fuentes=_fuentes(A_COMPLETA))

        assert reglas[:2] == ["R1", "R4"]
        assert len(reglas) == len(set(reglas))

    def test_reglas_disparadas_incluye_la_casuistica_del_caso_ambiguo(self):
        # Un caso ambiguo no tiene `Decision`, pero sí reglas de casuística en la
        # traza: la auditoría las quiere ver.
        evidencia = _evidencia(AMBIGUO)
        resultado = consolidar_caso(evidencia, contexto_tipo=CTX_RI_RI).valor

        reglas = reglas_disparadas(evidencia, resultado=resultado)

        assert reglas
        assert all(isinstance(r, str) for r in reglas)


# ---------------------------------------------------------------------------
# 8. Fronteras
# ---------------------------------------------------------------------------


class TestFronteras:
    """Lo que la persistencia no hace y los errores que declara."""

    def test_no_hay_red(self):
        import inspect

        from voucherflow.trace import construccion, recorder

        for modulo in (construccion, recorder):
            fuente = inspect.getsource(modulo)
            assert "requests" not in fuente
            assert "urllib" not in fuente
            assert "http" not in fuente

    def test_es_deterministico(self):
        # El registro auditable es determinístico: los datos que responden el
        # Gherkin son los mismos. (Los timestamps —del `CaseRecord` y de cada
        # `meta` de F4— son lo único que cambia por corrida, y no son respuestas
        # de auditoría, son marcas de cuándo.)
        def _sin_timestamp(caso: CaseRecord) -> dict[str, Any]:
            resumen = resumen_case_record(caso)
            resumen.pop("timestamp")
            return resumen

        assert _sin_timestamp(_caso()) == _sin_timestamp(_caso())

    def test_el_timestamp_marca_cuando_se_produjo_el_caso(self):
        caso = _caso()

        assert caso.timestamp  # ISO-8601 UTC, autogenerado por el contrato

    def test_no_muta_la_evidencia_ni_el_resultado(self):
        evidencia = _evidencia(A_COMPLETA)
        resultado = consolidar_caso(evidencia, contexto_tipo=CTX_RI_RI).valor
        antes_ev = dict(evidencia.trazabilidad)
        antes_res = resultado.model_dump()

        construir_case_record(evidencia, resultado=resultado, fuentes=_fuentes(A_COMPLETA))

        assert dict(evidencia.trazabilidad) == antes_ev
        assert resultado.model_dump() == antes_res

    def test_el_registro_cumple_el_contrato_congelado(self):
        caso = _caso()

        assert isinstance(caso, CaseRecord)
        assert caso.schema_version == SCHEMA_VERSION
        assert caso.model_dump()["documento_id"] == "doc-1"

    def test_no_persiste_un_caso_sin_documento(self, tmp_path: Path):
        recorder = CaseRecorder(tmp_path)
        # El contrato lo impide; el guard del registrador lo declara igual.
        caso = _caso()
        object.__setattr__(caso, "documento_id", "  ")

        with pytest.raises(ValueError):
            recorder.registrar(caso)

    def test_el_directorio_de_salida_se_crea_si_no_existe(self, tmp_path: Path):
        destino = tmp_path / "anidado" / "casos"

        CaseRecorder(destino).registrar(_caso())

        assert destino.is_dir()

    def test_la_persistencia_no_ejecuta_reglas_ni_modelos(self, tmp_path: Path):
        # Es una proyección: no hay ninguna llamada a un modelo.
        import inspect

        from voucherflow.trace import construccion

        fuente = inspect.getsource(construccion)
        assert ".ask(" not in fuente
        assert "Ollama" not in fuente

    def test_rechaza_un_caso_que_no_es_case_record(self, tmp_path: Path):
        with pytest.raises(AttributeError):
            CaseRecorder(tmp_path).registrar({"documento_id": "doc-1"})  # type: ignore[arg-type]

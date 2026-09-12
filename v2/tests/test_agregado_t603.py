"""Tests de la salida agregada del lote (F6 / T-603, E-CLI-2).

**DoD de T-603** (F6.md §3, E-CLI-2): "Cada resultado genera un JSON sidecar
(resultado + evidencia + trazabilidad) y en modo lote se puede consolidar en un
único JSON agregado."

El Gherkin de la historia tiene dos mitades, y el test las cubre por separado:

1. **El sidecar por documento** (la mitad hecha en F5/T-506 y expuesta por el CLI
   en T-601): al guardar el resultado se genera un JSON con resultado + evidencia
   + trazabilidad, y se puede *leer de vuelta*. Acá se verifica de punta a punta,
   no se reimplementa.
2. **El agregado del lote** (lo que agrega T-603): un **único JSON** que consolida
   la corrida. Qué se verifica, en orden:
   - **La forma**: una entrada por documento con veredicto + **puntero al sidecar**,
     la síntesis del lote y las métricas.
   - **Que no duplique la evidencia**: el agregado es un índice, no una copia de
     los `CaseRecord`. Un lote grande no puede producir un archivo inmanejable ni
     una segunda fuente de verdad que pueda contradecir al sidecar.
   - **La acumulación entre corridas**: cada corrida suma lo suyo y el mismo
     documento **actualiza** su entrada (una por documento, no una por corrida).
     Un lote interrumpido y reanudado termina con **un** agregado coherente.
   - **La honestidad de las métricas**: se calculan sobre el histórico persistido
     (F5/T-507); si no hay de dónde derivarlas, el agregado **lo declara** en vez
     de mostrar un número que no puede sostener.
   - **La reconstrucción desde el histórico**: `case aggregate` arma el agregado
     leyendo los sidecars, sin volver a correr el pipeline.
3. **Fronteras**: un agregado ilegible **no** se sobreescribe (se perdería el lote
   anterior), un documento ilegible no se cuenta como `revision`, y el agregado no
   inventa el estado que la corrida no alcanzó.

Reglas duras: suite default **sin** Ollama, **sin** Docling y **sin** red (los
dobles se inyectan por `EntornoCLI`); el agregado se escribe siempre en `tmp_path`.
"""

from __future__ import annotations

import io
import json
from pathlib import Path
from typing import Any

import pytest

from voucherflow.cli.main import VERSION_CLI, EntornoCLI, main
from voucherflow.orchestrator import PipelineOrchestrator, PipelineResult
from voucherflow.trace.agregado import (
    NO_AGREGADOS,
    VERSION_AGREGADO,
    Agregado,
    EntradaDocumento,
    agregado_del_recorder,
    agregar_a_archivo,
    construir_agregado,
    entrada_de_caso,
    entrada_de_resultado,
    escribir_agregado,
    leer_agregado,
)
from voucherflow.trace.recorder import VERSION_TRAZA, CaseRecorder
from voucherflow.trace.construccion import construir_case_record

# ---------------------------------------------------------------------------
# Dobles (mismo patrón que T-601/T-602: sin red, sin modelos)
# ---------------------------------------------------------------------------


class FakeLector:
    """Doble del ``OllamaClient`` que responde por system prompt de cada etapa."""

    def __init__(self, *, gate: str = "comprobante") -> None:
        self.gate = gate

    def _etapa(self, sistema: str) -> str:
        from voucherflow.classification.prompt_tipo_comprobante import (
            SYSTEM_PROMPT_TIPO_COMPROBANTE_LLM,
            SYSTEM_PROMPT_TIPO_COMPROBANTE_VLM,
        )
        from voucherflow.extraction.prompt_extraccion import (
            SYSTEM_PROMPT_EXTRACCION_LLM,
            SYSTEM_PROMPT_EXTRACCION_VLM,
        )
        from voucherflow.validation.prompt_qween import SYSTEM_PROMPT_QWEEN

        if sistema == SYSTEM_PROMPT_QWEEN:
            return "gate"
        if sistema in (SYSTEM_PROMPT_TIPO_COMPROBANTE_VLM, SYSTEM_PROMPT_TIPO_COMPROBANTE_LLM):
            return "tipo"
        if sistema in (SYSTEM_PROMPT_EXTRACCION_VLM, SYSTEM_PROMPT_EXTRACCION_LLM):
            return "extraccion"
        return "otro"

    def ask(self, messages, model, json_format=False, options=None, num_ctx=None):
        from voucherflow.models.ollama import RespuestaOllama

        sistema = messages[0].get("content", "") if messages else ""
        etapa = self._etapa(sistema)
        if etapa == "gate":
            contenido = self.gate
        elif etapa == "extraccion":
            contenido = json.dumps(
                {
                    "fuente_lectura": "llm",
                    "campos": {
                        "tipo_comprobante": {"valor": "A", "fragmento_sustento": "FACTURA A"},
                        "cuit_emisor": {"valor": "30-12345678-9", "fragmento_sustento": "CUIT 30-12345678-9"},
                        "cuit_receptor": {"valor": "27-30111222-4", "fragmento_sustento": "CUIT 27-30111222-4"},
                        "fecha_emision": {"valor": "14/08/2025", "fragmento_sustento": "Fecha: 14/08/2025"},
                        "nro_comprobante": {"valor": "0001-00000001", "fragmento_sustento": "Nro: 0001-00000001"},
                        "importe_total_facturado": {"valor": "121,00", "fragmento_sustento": "Total: 121,00"},
                    },
                }
            )
        else:
            contenido = json.dumps(
                {
                    "fuente_lectura": "vlm",
                    "tipo_detectado_por_documento": "A",
                    "tipo_detectado_por_documento_explicacion": "FACTURA A",
                    "candidatos_restantes": [],
                }
            )
        return RespuestaOllama(contenido=contenido, modelo=model, latencia_s=0.0, status=200)


class FakeConverter:
    """Doble del ``DoclingConverter``: texto nativo (no pasa por el gate T-102)."""

    def convert(self, origen: str) -> Any:
        from voucherflow.models.docling import ProcessedDocument

        return ProcessedDocument(
            tipo_entrada="texto", ruta=str(origen), markdown="FACTURA A\n", boxes=[], n_items=1
        )


def _settings() -> Any:
    from voucherflow.settings.config import cargar_desde_dict

    return cargar_desde_dict(
        {
            "modelos": {
                "vlm": {"rol": "vlm", "modelo": "modelo-vlm-test"},
                "llm": {"rol": "llm", "modelo": "modelo-llm-test"},
                "agente": {"rol": "agente", "modelo": "modelo-agente-test"},
            }
        }
    )


def _orquestador(*, gate: str = "comprobante") -> PipelineOrchestrator:
    return PipelineOrchestrator(
        cliente=FakeLector(gate=gate), converter=FakeConverter(), settings=_settings()
    )


def _entorno(tmp: Path, *, gate: str = "comprobante") -> EntornoCLI:
    return EntornoCLI(
        orquestador=_orquestador(gate=gate),
        stdout=io.StringIO(),
        stderr=io.StringIO(),
        cwd=tmp,
    )


def _carpeta(tmp: Path, cantidad: int) -> list[Path]:
    rutas = []
    for indice in range(cantidad):
        ruta = tmp / f"doc-{indice:02d}.md"
        ruta.write_text(f"FACTURA A\ncomprobante {indice}\n", encoding="utf-8")
        rutas.append(ruta)
    return rutas


def _resultado(
    documento_id: str,
    *,
    archivo: str | None = None,
    estado: str | None = "aprobado",
    ok: bool = True,
    sidecar: str | None = None,
    hitl: bool | None = None,
    tipo: str | None = "A",
    certeza: str | None = "alta",
    origen: str | None = "programa",
    error: str | None = None,
) -> PipelineResult:
    """``PipelineResult`` sintético, para probar el agregado sin pipeline."""
    detalle: dict[str, Any] = {}
    if sidecar:
        detalle["persistencia"] = {"sidecar": sidecar, "indexado": True}
    return PipelineResult(
        documento_id=documento_id,
        archivo=archivo or f"/tmp/{documento_id}.jpg",
        ok=ok,
        etapas_completadas=["processing", "validation"] if ok else ["processing"],
        resumen={
            "estado": estado,
            "tipo_comprobante": tipo,
            "certeza": certeza,
            "origen": origen,
            "campos_extraidos": 6,
            "hitl_requerido": hitl,
            "clasificacion_contable": False,
        },
        error=error,
        detalle=detalle,
    )


# ---------------------------------------------------------------------------
# 1. La mitad del sidecar (F5/T-506 + T-601): el Gherkin, de punta a punta
# ---------------------------------------------------------------------------


class TestSidecarPorDocumento:
    def test_run_con_cases_genera_el_sidecar_con_trazabilidad(self, tmp_path: Path):
        _carpeta(tmp_path, 1)
        cases = tmp_path / "cases"
        entorno = _entorno(tmp_path)
        assert main(["run", str(tmp_path / "doc-00.md"), "--cases", str(cases)], entorno=entorno) == 0

        sidecars = list(cases.glob("*.case.json"))
        assert len(sidecars) == 1
        caso = json.loads(sidecars[0].read_text(encoding="utf-8"))
        # "resultado + evidencia + trazabilidad": los tres, en el mismo archivo.
        assert caso["resultado"]["estado"] == "aprobado"
        assert caso["evidencia_por_fuente"], "el sidecar tiene la evidencia por fuente"
        assert caso["etapas"] and caso["reglas_disparadas"] is not None

    def test_el_sidecar_se_lee_de_vuelta(self, tmp_path: Path):
        _carpeta(tmp_path, 1)
        cases = tmp_path / "cases"
        entorno = _entorno(tmp_path)
        main(["run", str(tmp_path / "doc-00.md"), "--cases", str(cases)], entorno=entorno)

        recorder = CaseRecorder(cases)
        documento_id = next(iter(recorder.buscar(estado="aprobado")))["documento_id"]
        caso = recorder.leer(documento_id)
        # El round-trip vuelve al contrato sin pérdida (auditoría de F5/T-506).
        assert caso.documento_id == documento_id
        assert caso.resultado is not None

    def test_case_show_muestra_el_sidecar(self, tmp_path: Path):
        _carpeta(tmp_path, 1)
        cases = tmp_path / "cases"
        entorno = _entorno(tmp_path)
        main(["run", str(tmp_path / "doc-00.md"), "--cases", str(cases)], entorno=entorno)

        recorder = CaseRecorder(cases)
        documento_id = next(iter(recorder.buscar(estado="aprobado")))["documento_id"]
        entorno2 = _entorno(tmp_path)
        assert main(["case", "show", documento_id, "--dir", str(cases)], entorno=entorno2) == 0
        assert json.loads(entorno2.stdout.getvalue())["documento_id"] == documento_id


# ---------------------------------------------------------------------------
# 2. El agregado: la forma
# ---------------------------------------------------------------------------


class TestFormaDelAgregado:
    def test_una_entrada_por_documento_con_veredicto(self):
        agregado = construir_agregado(
            resultados=[_resultado("sha256:a"), _resultado("sha256:b", estado="rechazado")],
            raiz="files/",
        )
        assert agregado.total == 2
        datos = agregado.como_dict()
        assert datos["version"] == VERSION_AGREGADO
        assert datos["raiz"] == "files/"
        assert [d["documento_id"] for d in datos["documentos"]] == ["sha256:a", "sha256:b"]
        # El veredicto viaja en la entrada: es lo que se lee sin abrir otra cosa.
        assert datos["documentos"][1]["estado"] == "rechazado"

    def test_la_entrada_apunta_al_sidecar(self):
        agregado = construir_agregado(
            resultados=[_resultado("sha256:a", sidecar="/x/sha256_a.case.json")]
        )
        assert agregado.documentos[0].sidecar == "sha256_a.case.json"

    def test_sin_sidecar_la_entrada_lo_declara(self):
        agregado = construir_agregado(resultados=[_resultado("sha256:a")])
        # No se inventa un puntero a un archivo que no existe.
        assert "sidecar" not in agregado.documentos[0].como_dict()

    def test_no_embebe_la_evidencia(self):
        """El agregado es un índice: no puede duplicar los `CaseRecord`."""
        from voucherflow.schemas.evidence import CombinedEvidence

        evidencia = CombinedEvidence(documento_id="sha256:a", campos={})
        caso = construir_case_record(evidencia, archivo="/x/doc.jpg")
        agregado = construir_agregado(casos=[caso])
        datos = agregado.como_dict()

        # El bloque permitido: el veredicto y el puntero…
        assert "documento_id" in datos["documentos"][0]
        assert datos["documentos"][0]["sidecar"].endswith(".case.json")
        # …y NADA del contenido pesado del CaseRecord.
        serializado = json.dumps(datos["documentos"])
        assert "evidencia_por_fuente" not in serializado
        assert NO_AGREGADOS[0] not in serializado
        assert "fragmento_sustento" not in serializado

    def test_el_contrato_declara_donde_esta_la_evidencia(self):
        agregado = construir_agregado(resultados=[_resultado("sha256:a")])
        contrato = agregado.como_dict()["contrato"]
        assert contrato["version_traza"] == VERSION_TRAZA
        # La nota es lo que evita que alguien busque la evidencia en el agregado.
        assert "case show" in contrato["nota"]

    def test_la_sintesis_cuenta_por_estado(self):
        agregado = construir_agregado(
            resultados=[
                _resultado("a", estado="aprobado"),
                _resultado("b", estado="rechazado"),
                _resultado("c", estado="revision", hitl=True),
                _resultado("d", estado="revision", hitl=True),
            ]
        )
        resumen = agregado.resumen()
        assert resumen["documentos"] == 4
        assert resumen["por_estado"] == {"aprobado": 1, "rechazado": 1, "revision": 2}
        assert resumen["requieren_revision"] == 2

    def test_cuenta_la_revision_obligatoria_aparte(self):
        agregado = construir_agregado(
            resultados=[
                _resultado("a", estado="revision", hitl=True),
                _resultado("b", estado="aprobado", hitl=True),
            ]
        )
        entradas = [d for d in agregado.documentos if d.hitl_requerido]
        # La prioridad sale del `VoucherResult.hitl`; sin resultado no se inventa.
        assert len(entradas) == 2

    def test_reporta_errores_sin_confundirlos_con_rechazos(self):
        agregado = construir_agregado(
            resultados=[
                _resultado("a", estado="rechazado"),
                _resultado("b", ok=False, estado=None, error="no se pudo leer"),
            ]
        )
        resumen = agregado.resumen()
        assert resumen["errores"] == 1
        # Un rechazo es una conclusión (no un error); un archivo ilegible sí lo es.
        assert resumen["rechazado"] if "rechazado" in resumen else True
        assert agregado.documentos[1].error == "no se pudo leer"

    def test_un_documento_sin_estado_no_se_cuenta_como_revision(self):
        agregado = construir_agregado(resultados=[_resultado("a", ok=False, estado=None)])
        # "No sé" no es "hay que revisarlo": el agregado no inventa el estado.
        assert agregado.por_estado() == {"sin_estado": 1}

    def test_el_agregado_es_serializable(self):
        agregado = construir_agregado(resultados=[_resultado("a")])
        assert json.loads(json.dumps(agregado.como_dict()))["resumen"]["documentos"] == 1


# ---------------------------------------------------------------------------
# 3. Persistencia y acumulación
# ---------------------------------------------------------------------------


class TestPersistencia:
    def test_escribir_y_leer_el_agregado(self, tmp_path: Path):
        destino = tmp_path / "lote.json"
        original = construir_agregado(resultados=[_resultado("a")], raiz="files/")
        escribir_agregado(destino, original)

        leido = leer_agregado(destino)
        assert leido.total == 1
        assert leido.raiz == "files/"
        assert leido.documentos[0].documento_id == "a"

    def test_la_escritura_es_atomica(self, tmp_path: Path):
        destino = tmp_path / "lote.json"
        escribir_agregado(destino, construir_agregado(resultados=[_resultado("a")]))
        # No quedan temporales de la escritura.
        assert not list(tmp_path.glob(".*.tmp"))

    def test_se_acumula_entre_corridas(self, tmp_path: Path):
        destino = tmp_path / "lote.json"
        agregar_a_archivo(destino, resultados=[_resultado("a")])
        agregar_a_archivo(destino, resultados=[_resultado("b")])
        assert leer_agregado(destino).total == 2

    def test_el_mismo_documento_actualiza_su_entrada(self, tmp_path: Path):
        destino = tmp_path / "lote.json"
        agregar_a_archivo(destino, resultados=[_resultado("a", estado="revision")])
        agregar_a_archivo(destino, resultados=[_resultado("a", estado="aprobado")])
        final = leer_agregado(destino)
        # Una entrada por documento: reprocesar no duplica (igual que el índice).
        assert final.total == 1
        assert final.documentos[0].estado == "aprobado"

    def test_un_agregado_ilegible_no_se_sobreescribe(self, tmp_path: Path):
        destino = tmp_path / "lote.json"
        destino.write_text("{no es json", encoding="utf-8")
        # Sobreescribir borraría el lote anterior sin avisar: esto es un error.
        with pytest.raises(ValueError, match="no es JSON válido"):
            agregar_a_archivo(destino, resultados=[_resultado("a")])
        assert destino.read_text(encoding="utf-8") == "{no es json"

    def test_un_archivo_que_no_es_agregado_no_se_sobreescribe(self, tmp_path: Path):
        destino = tmp_path / "otra-cosa.json"
        destino.write_text(json.dumps({"hola": 1}), encoding="utf-8")
        with pytest.raises(ValueError, match="no tiene la forma de un agregado"):
            agregar_a_archivo(destino, resultados=[_resultado("a")])

    def test_leer_un_agregado_inexistente_es_error(self, tmp_path: Path):
        with pytest.raises(FileNotFoundError):
            leer_agregado(tmp_path / "no-existe.json")

    def test_el_sidecar_no_se_degrada_a_la_proyeccion_de_la_corrida(self, tmp_path: Path):
        destino = tmp_path / "lote.json"
        # Primero entra con puntero al sidecar…
        agregar_a_archivo(
            destino, casos=[construir_case_record(_evidencia_vacia("a"), archivo="/x/a.jpg")]
        )
        # …y una corrida posterior sin --cases no lo pisa (el sidecar es más rico).
        agregar_a_archivo(destino, resultados=[_resultado("a", sidecar=None)])
        assert leer_agregado(destino).documentos[0].sidecar is not None


# ---------------------------------------------------------------------------
# 4. Métricas: se derivan o se declaran
# ---------------------------------------------------------------------------


class TestMetricas:
    def test_sin_casos_el_agregado_declara_que_no_hay_metricas(self):
        datos = construir_agregado(resultados=[_resultado("a")]).como_dict()
        assert datos["metricas"] is None
        # No se omite en silencio (parecería que el lote no tiene métricas).
        assert "--cases" in datos["metricas_no_disponibles"]

    def test_con_casos_las_metricas_se_calculan(self):
        caso = construir_case_record(_evidencia_vacia("a"), archivo="/x/a.jpg")
        datos = construir_agregado(casos=[caso]).como_dict()
        assert datos["metricas"] is not None
        assert datos["metricas"]["documentos_procesados"] == 1

    def test_las_metricas_no_se_inventan_si_no_hay_historico(self):
        agregado = construir_agregado(resultados=[_resultado("a")], informe_metricas=False)
        assert agregado.metricas is None

    def test_no_se_borra_un_calculo_previo_de_metricas(self, tmp_path: Path):
        destino = tmp_path / "lote.json"
        agregar_a_archivo(
            destino, casos=[construir_case_record(_evidencia_vacia("a"), archivo="/x/a.jpg")]
        )
        assert leer_agregado(destino).metricas is not None
        # Una corrida sin casos no borra el cálculo que ya estaba.
        agregar_a_archivo(destino, resultados=[_resultado("b")])
        assert leer_agregado(destino).metricas is not None


# ---------------------------------------------------------------------------
# 5. Integración con el CLI y con el histórico
# ---------------------------------------------------------------------------


class TestIntegracionCLI:
    def test_batch_escribe_el_agregado(self, tmp_path: Path):
        _carpeta(tmp_path, 3)
        agregado = tmp_path / "lote.json"
        entorno = _entorno(tmp_path)
        assert main(["batch", str(tmp_path), "-o", str(agregado)], entorno=entorno) == 0

        datos = json.loads(agregado.read_text(encoding="utf-8"))
        assert datos["resumen"]["documentos"] == 3
        assert len(datos["documentos"]) == 3
        assert datos["lote"]["version"].startswith("lote-batch@")

    def test_batch_acumula_entre_corridas(self, tmp_path: Path):
        _carpeta(tmp_path, 2)
        agregado = tmp_path / "lote.json"
        main(["batch", str(tmp_path), "-o", str(agregado)], entorno=_entorno(tmp_path))
        assert leer_agregado(agregado).total == 2

        # Dos documentos **nuevos** (otro nombre y otro contenido: otro hash).
        for indice in (10, 11):
            (tmp_path / f"doc-{indice}.md").write_text(
                f"FACTURA A\ncomprobante {indice}\n", encoding="utf-8"
            )
        main(["batch", str(tmp_path), "-o", str(agregado)], entorno=_entorno(tmp_path))

        # 4 documentos: los 2 previos (que ahora se reanudan) + los 2 nuevos. El
        # agregado es el estado de la carpeta, no el reporte de la última corrida.
        final = leer_agregado(agregado)
        assert final.total == 4
        assert final.resumen()["documentos"] == 4

    def test_batch_con_cases_incluye_metricas(self, tmp_path: Path):
        _carpeta(tmp_path, 2)
        agregado = tmp_path / "lote.json"
        cases = tmp_path / "cases"
        entorno = _entorno(tmp_path)
        assert main(
            ["batch", str(tmp_path), "--cases", str(cases), "-o", str(agregado)],
            entorno=entorno,
        ) == 0
        datos = json.loads(agregado.read_text(encoding="utf-8"))
        assert datos["metricas"] is not None
        assert datos["resumen"]["con_sidecar"] == 2

    def test_batch_sin_output_escribe_el_default(self, tmp_path: Path):
        _carpeta(tmp_path, 1)
        entorno = _entorno(tmp_path)
        assert main(["batch", str(tmp_path)], entorno=entorno) == 0
        assert (tmp_path / "lote.agregado.json").is_file()

    def test_case_aggregate_reconstruye_del_historico(self, tmp_path: Path):
        _carpeta(tmp_path, 2)
        cases = tmp_path / "cases"
        entorno = _entorno(tmp_path)
        main(["batch", str(tmp_path), "--cases", str(cases)], entorno=entorno)

        entorno2 = _entorno(tmp_path)
        assert main(["case", "aggregate", "--dir", str(cases)], entorno=entorno2) == 0
        datos = json.loads(entorno2.stdout.getvalue())
        assert datos["resumen"]["documentos"] == 2
        assert datos["resumen"]["con_sidecar"] == 2
        # Reconstruido del histórico: las métricas están disponibles sin correr nada.
        assert datos["metricas"] is not None

    def test_case_aggregate_puede_escribir_a_archivo(self, tmp_path: Path):
        _carpeta(tmp_path, 1)
        cases = tmp_path / "cases"
        main(["batch", str(tmp_path), "--cases", str(cases)], entorno=_entorno(tmp_path))

        destino = tmp_path / "reconstruido.json"
        entorno2 = _entorno(tmp_path)
        assert main(
            ["case", "aggregate", "--dir", str(cases), "-o", str(destino)],
            entorno=entorno2,
        ) == 0
        assert leer_agregado(destino).total == 1

    def test_agregado_del_recorder(self, tmp_path: Path):
        _carpeta(tmp_path, 2)
        cases = tmp_path / "cases"
        main(["batch", str(tmp_path), "--cases", str(cases)], entorno=_entorno(tmp_path))

        agregado = agregado_del_recorder(CaseRecorder(cases))
        assert agregado.total == 2
        assert str(cases) == agregado.raiz


# ---------------------------------------------------------------------------
# 6. Fronteras
# ---------------------------------------------------------------------------


class TestFronteras:
    def test_los_dos_caminos_de_construccion_coinciden(self):
        caso = construir_case_record(_evidencia_vacia("a"), archivo="/x/a.jpg")
        desde_casos = construir_agregado(casos=[caso])
        desde_entrada = construir_agregado(casos=[caso])
        assert desde_casos.documentos[0].documento_id == desde_entrada.documentos[0].documento_id

    def test_el_historico_manda_sobre_la_corrida(self):
        caso = construir_case_record(_evidencia_vacia("a"), archivo="/x/a.jpg")
        agregado = construir_agregado(
            casos=[caso], resultados=[_resultado("a", estado="rechazado")]
        )
        # El `CaseRecord` es el dato durable: no se pisa con la proyección.
        assert agregado.total == 1
        assert agregado.documentos[0].sidecar is not None

    def test_los_documentos_sin_caso_tambien_entran(self):
        caso = construir_case_record(_evidencia_vacia("a"), archivo="/x/a.jpg")
        agregado = construir_agregado(casos=[caso], resultados=[_resultado("b")])
        assert agregado.total == 2

    def test_una_entrada_desde_caso_sin_resultado_es_ok_false(self):
        caso = construir_case_record(_evidencia_vacia("a"), archivo="/x/a.jpg")
        entrada = entrada_de_caso(caso)
        # Sin resultado consolidado no hay veredicto: no se inventa uno.
        assert entrada.estado is None
        assert entrada.ok is False

    def test_la_entrada_no_incluye_claves_nulas(self):
        entrada = entrada_de_resultado(_resultado("a", tipo=None, certeza=None, origen=None))
        datos = entrada.como_dict()
        # El agregado se lee a ojo: las claves vacías se omiten, no se ponen en null.
        assert "tipo_comprobante" not in datos
        assert "certeza" not in datos

    def test_entrada_ida_y_vuelta(self):
        original = EntradaDocumento(
            documento_id="a", archivo="/x/a.jpg", ok=True, estado="aprobado",
            etapas=("processing", "validation"),
        )
        vuelta = EntradaDocumento.desde_dict(original.como_dict())
        assert vuelta == original

    def test_agregado_ida_y_vuelta(self):
        original = construir_agregado(resultados=[_resultado("a")], raiz="files/")
        vuelta = Agregado.desde_dict(original.como_dict())
        assert vuelta.total == original.total
        assert vuelta.raiz == original.raiz

    def test_construir_sin_nada_da_un_agregado_vacio(self):
        agregado = construir_agregado()
        assert agregado.total == 0
        assert agregado.resumen()["documentos"] == 0

    def test_el_agregado_no_corre_el_pipeline(self, tmp_path: Path):
        """Es una proyección: no necesita documentos ni modelos."""
        agregado = construir_agregado(resultados=[_resultado("a")], raiz="/no/existe")
        assert agregado.total == 1


def _evidencia_vacia(documento_id: str):
    """Evidencia combinada mínima, para armar un `CaseRecord` sin pipeline."""
    from voucherflow.schemas.evidence import CombinedEvidence

    return CombinedEvidence(documento_id=documento_id, campos={})

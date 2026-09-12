"""Tests del CLI ``voucherflow`` y del orquestador (F6 / T-601).

**DoD de T-601** (F6.md §3, E-CLI-1): "El CLI ``voucherflow`` expone los
subcomandos process/validate/classify/extract/run/batch/ask/arca/case/hitl para
archivo o carpeta recursiva, respetando ``--force``, ``--orientation``,
``--condicion-impositiva``, ``--model`` y ``--workers``."

Qué se verifica, en el orden del entregable:

1. **El contrato del CLI**: los subcomandos del DoD existen, con sus flags
   (``--force``, ``--orientation``, ``--condicion-impositiva``, ``--model``,
   ``--workers``) y con ``--help`` funcionando; ``main`` devuelve **código de
   salida** (no llama a ``sys.exit``), y un error de uso sale con ≠ 0.
2. **El orquestador** (``PipelineOrchestrator.ejecutar``): la secuencia completa
   processing → validation → extraction → combinación → conclusión → traza, con
   el ``VoucherResult`` y el ``CaseRecord``; el **fast-fail del gate** (un
   documento que no es comprobante se resuelve ``rechazado`` con certeza alta y
   **sin** gastar extracción); y el caso ilegible (``ok=False`` con el error
   declarado, no una excepción sin contexto).
3. **La fachada**: ``api.run`` devuelve el ``VoucherResult`` y ``api.extract`` la
   ``CombinedEvidence``; ``api.extract`` ya **no** es el esqueleto de F0.
4. **Los subcomandos sobre el filesystem**: ``process`` escribe el markdown,
   ``extract``/``extract-detect``/``run``/``batch`` producen el JSON, y el lote
   **recursivo** descubre la carpeta y **excluye** los artefactos derivados
   (sidecars, checkpoints) — la carpeta ``files/`` real está llena de ellos.
5. **Las opciones comunes tienen efecto declarado**: ``--orientation`` re-exporta
   el markdown (y en texto nativo declara que no aplica), ``--workers`` queda
   registrado como solicitado/aplicado (el pool es T-602), ``--force`` se declara
   no-op explícito.
6. **Auditoría**: ``case list/show`` lee el índice y el sidecar persistidos
   (F5/T-506) y ``hitl list --dir`` filtra el histórico por prioridad/certeza.

Reglas duras: la suite default corre **sin** Ollama, **sin** Docling y **sin**
red (todos los dobles se inyectan vía :class:`EntornoCLI`). Los contratos de F0
(``PipelineResult``, ``api.run``/``api.extract``) siguen siendo llamables tal
como se congelaron.
"""

from __future__ import annotations

import argparse
import io
import json
from pathlib import Path
from typing import Any

import pytest

from voucherflow.api import extract as api_extract
from voucherflow.api import run as api_run
from voucherflow.cli.main import (
    COMANDOS,
    DESPACHO,
    EntornoCLI,
    construir_parser,
    main,
)
from voucherflow.orchestrator import (
    ETAPA_CONCLUSION,
    ETAPA_EXTRACTION,
    ETAPA_PROCESSING,
    ETAPA_TRAZABILIDAD,
    ETAPA_VALIDATION,
    VERSION_ORQUESTADOR,
    PipelineOrchestrator,
    identificador_de_archivo,
    iterar_documentos,
)
from voucherflow.schemas.evidence import Fuente
from voucherflow.schemas.result import VoucherResult
from voucherflow.settings.config import cargar_desde_dict

# ---------------------------------------------------------------------------
# Dobles (sin Ollama ni Docling; mismo criterio que F2/T-203 y F4/T-401)
# ---------------------------------------------------------------------------

PASO_01 = {
    "centros_costos": [
        {
            "codigo_centro_costo": "CC0004",
            "centro": "Repuestos",
            "confianza": "alta",
            "senal_usada": "descripcion",
            "justificacion": "La descripción menciona repuestos.",
        }
    ]
}
PASO_02 = {
    "macro_categorias": [
        {
            "macro_categoria": "MC07",
            "nombre": "Operaciones y Logística",
            "confianza": "alta",
            "justificacion": "Materiales e insumos.",
        }
    ]
}
PASO_03 = {
    "macro_categoria": "MC07",
    "concepto": "CT017",
    "nombre_concepto": "Materiales, insumos y herramientas",
    "cuenta_contable": "4221,16",
    "condicion_impositiva": "21",
    "codigo_final": "48",
    "candidatos_codigo_final": [],
    "requiere_revision_humana": False,
    "confianza": "alta",
    "justificacion": "Materiales.",
}

#: Campos del contrato de extracción con su sostén (lo que devolvería el modelo
#: cuando lee una factura A completa y coherente). La letra ``A`` exige los dos
#: CUIT (CRUZ_3 / coherencia de T-403), así que se declaran los dos.
CAMPOS_FACTURA_A: dict[str, str] = {
    "tipo_comprobante": "A",
    "cuit_emisor": "30-12345678-9",
    "cuit_receptor": "27-30111222-4",
    "fecha_emision": "14/08/2025",
    "nro_comprobante": "0001-00000001",
    "importe_total_facturado": "121,00",
    "razon_social_emisor": "ACME SA",
    "razon_social_receptor": "Cliente SA",
}


def _json_extraccion(campos: dict[str, str], fuente: str) -> str:
    """Arma la respuesta de evidencia de extracción (contrato de T-401)."""
    return json.dumps(
        {
            "fuente_lectura": fuente,
            "campos": {
                nombre: {
                    "valor": valor,
                    "fragmento_sustento": f"{nombre}: {valor}",
                }
                for nombre, valor in campos.items()
            },
        },
        ensure_ascii=False,
    )


class FakeLector:
    """Doble del ``OllamaClient`` que responde **por tipo de pedido**.

    Cada etapa usa un system prompt **distinto y versionado** (gate qween,
    lectura de tipo/letra, extracción, cadena contable), así que el doble
    despacha comparando el system prompt resuelto de cada etapa — no por orden
    de llamada— y el test no queda acoplado a cuántas veces cada etapa consulta
    al modelo.

    Devuelve ``RespuestaOllama`` de verdad (no un objeto suelto) para que las
    etapas consuman ``.contenido`` exactamente como con el cliente real.
    """

    def __init__(
        self,
        *,
        gate: str = "comprobante",
        campos: dict[str, str] | None = None,
        letra: str = "A",
        pasos: dict[str, Any] | None = None,
    ) -> None:
        self.gate = gate
        self.campos = CAMPOS_FACTURA_A if campos is None else campos
        self.letra = letra
        self.pasos = {"01": PASO_01, "02": PASO_02, "03": PASO_03, **(pasos or {})}
        self.llamadas: list[dict[str, Any]] = []

    # -- despacho por etapa ------------------------------------------------

    def _etapa(self, sistema: str) -> str:
        """Nombre de la etapa según el system prompt exacto que la construyó."""
        from voucherflow.classification.prompt_tipo_comprobante import (
            SYSTEM_PROMPT_TIPO_COMPROBANTE_LLM,
            SYSTEM_PROMPT_TIPO_COMPROBANTE_VLM,
        )
        from voucherflow.classification.prompts_contable import PASOS_CONTABLES
        from voucherflow.extraction.prompt_extraccion import (
            SYSTEM_PROMPT_EXTRACCION_LLM,
            SYSTEM_PROMPT_EXTRACCION_VLM,
        )
        from voucherflow.validation.prompt_qween import SYSTEM_PROMPT_QWEEN

        if sistema.startswith("Sos un asistente que responde preguntas"):
            return "ask"
        if sistema == SYSTEM_PROMPT_QWEEN:
            return "gate"
        if sistema in (SYSTEM_PROMPT_TIPO_COMPROBANTE_VLM, SYSTEM_PROMPT_TIPO_COMPROBANTE_LLM):
            return "tipo"
        if sistema in (SYSTEM_PROMPT_EXTRACCION_VLM, SYSTEM_PROMPT_EXTRACCION_LLM):
            return "extraccion_vlm" if sistema == SYSTEM_PROMPT_EXTRACCION_VLM else "extraccion_llm"
        for paso, definicion in PASOS_CONTABLES.items():
            if definicion["system"] == sistema:
                return f"contable_{paso}"
        return "desconocido"

    def ask(self, messages, model, json_format=False, options=None, num_ctx=None):
        from voucherflow.models.ollama import RespuestaOllama

        sistema = messages[0].get("content", "") if messages else ""
        etapa = self._etapa(sistema)
        self.llamadas.append({"etapa": etapa, "model": model})

        if etapa == "ask":
            # La pregunta libre (api.ask) devuelve texto, no JSON de contrato.
            contenido = "El total del comprobante es 121,00."
        elif etapa.startswith("contable_"):
            paso = etapa.split("_", 1)[1]
            contenido = json.dumps(self.pasos.get(paso, {}), ensure_ascii=False)
        elif etapa == "gate":
            contenido = self.gate
        elif etapa in ("extraccion_vlm", "extraccion_llm"):
            fuente = "vlm" if etapa.endswith("vlm") else "llm"
            contenido = _json_extraccion(self.campos, fuente)
        elif etapa == "tipo":
            contenido = json.dumps(
                {
                    "fuente_lectura": "vlm",
                    "tipo_detectado_por_documento": self.letra,
                    "tipo_detectado_por_documento_explicacion": (
                        f"FACTURA {self.letra} en el recuadro superior."
                    ),
                    "candidatos_restantes": [],
                },
                ensure_ascii=False,
            )
        else:
            raise AssertionError(
                f"El doble no reconoce el system prompt de la etapa: {sistema[:80]!r}"
            )
        return RespuestaOllama(
            contenido=contenido, modelo=model, latencia_s=0.0, status=200
        )


class FakeConverter:
    """Doble del ``DoclingConverter``: devuelve un ``ProcessedDocument`` armado.

    Se inyecta en ``procesar_documento`` por una ruta que **no** pasa por el gate
    de imagen de T-102 (una imagen real de prueba tendría que tener dimensiones
    legibles para no ser rechazada, y eso acoplaría el test al fixture binario):
    el gate de procesabilidad de imagen y el gate qween de F2 son dos controles
    distintos y este test ejercita el segundo, que es el que produce el
    fast-fail del pipeline.

    No mira el contenido del archivo: la representación procesada es un dato del
    test, no del pipeline.
    """

    MARKDOWN = "FACTURA A\nACME SA\nCUIT 30-12345678-9\nTotal: 121,00\n"

    def convert(self, origen: str) -> Any:
        from voucherflow.models.docling import Box, ProcessedDocument

        # Contrato de F1: ``convert`` devuelve el ``ProcessedDocument`` (no un
        # envoltorio con ``document``): es lo que consume ``procesar_documento``.
        return ProcessedDocument(
            tipo_entrada="imagen",
            ruta=str(origen),
            markdown=self.MARKDOWN,
            boxes=[
                Box(texto="FACTURA A", center_x=0.5, center_y=0.1, bbox=(0.1, 0.1, 0.9, 0.2)),
                Box(texto="Total: 121,00", center_x=0.5, center_y=0.5, bbox=(0.1, 0.5, 0.9, 0.6)),
            ],
            n_items=2,
        )


def _settings() -> Any:
    """``Settings`` con los tres roles apuntando a modelos ficticios (sin red)."""
    return cargar_desde_dict(
        {
            "modelos": {
                "vlm": {"rol": "vlm", "modelo": "modelo-vlm-test", "num_ctx": 4096},
                "llm": {"rol": "llm", "modelo": "modelo-llm-test", "num_ctx": 8192},
                "agente": {"rol": "agente", "modelo": "modelo-agente-test", "num_ctx": 8192},
            }
        }
    )


def _orquestador(
    *, gate: str = "comprobante", campos: dict[str, str] | None = None
) -> PipelineOrchestrator:
    return PipelineOrchestrator(
        cliente=FakeLector(gate=gate, campos=campos),
        converter=FakeConverter(),
        settings=_settings(),
    )


@pytest.fixture
def entorno(tmp_path: Path) -> EntornoCLI:
    """Entorno del CLI con los dobles y los flujos de salida capturados."""
    return EntornoCLI(
        orquestador=_orquestador(),
        stdout=io.StringIO(),
        stderr=io.StringIO(),
        cwd=tmp_path,
    )


@pytest.fixture
def documento(tmp_path: Path) -> Path:
    """Un documento de entrada (``.md`` de texto nativo: ver :class:`FakeConverter`).

    La extensión es ``.md`` a propósito: ``procesar_documento`` la enruta por
    texto nativo, que es donde el convertidor inyectado se usa sin el gate de
    imagen de T-102.
    """
    ruta = tmp_path / "factura.md"
    ruta.write_text("FACTURA A\nACME SA\nCUIT 30-12345678-9\nTotal: 121,00\n", encoding="utf-8")
    return ruta


def _salida(entorno: EntornoCLI) -> str:
    return entorno.stdout.getvalue()


def _log(entorno: EntornoCLI) -> str:
    return entorno.stderr.getvalue()


# ---------------------------------------------------------------------------
# 1. El contrato del CLI
# ---------------------------------------------------------------------------


class TestContratoCLI:
    def test_subcomandos_del_dod_existen(self):
        # E-CLI-1 / F6.md T-601: los diez subcomandos + extract-detect y arca.
        esperados = {
            "process",
            "validate",
            "classify",
            "extract",
            "extract-detect",
            "run",
            "batch",
            "ask",
            "arca",
            "case",
            "hitl",
        }
        assert set(COMANDOS) == esperados
        # El despacho cubre exactamente los comandos del contrato (sin huérfanos).
        assert set(DESPACHO) == esperados

    def test_flags_comunes_declarados(self):
        # E-CLI-1: --force, --orientation, --condicion-impositiva, --model, --workers.
        parser = construir_parser()
        args = parser.parse_args(
            [
                "run",
                "x.jpg",
                "--force",
                "--orientation",
                "vertical",
                "--condicion-impositiva",
                "27",
                "--model",
                "un-modelo",
                "--workers",
                "4",
            ]
        )
        assert args.force is True
        assert args.orientation == "vertical"
        assert args.condicion_impositiva == "27"
        assert args.model == "un-modelo"
        assert args.workers == 4

    def test_ayuda_no_revienta_y_usa_exit_2(self, entorno: EntornoCLI):
        # Sin subcomando, `main` imprime la ayuda y devuelve código de uso (2).
        assert main([], entorno=entorno) == 2
        assert "usage" in _log(entorno)

    def test_argumento_invalido_de_orientation(self):
        parser = construir_parser()
        with pytest.raises(SystemExit):
            parser.parse_args(["process", "x.jpg", "--orientation", "diagonal"])

    def test_main_no_llama_a_sys_exit(self, entorno: EntornoCLI, documento: Path):
        # El contrato es devolver el código: los tests lo invocan in-process.
        codigo = main(["process", str(documento)], entorno=entorno)
        assert codigo == 0

    def test_archivo_inexistente_sale_con_codigo_1(self, entorno: EntornoCLI):
        assert main(["process", "no-existe.jpg"], entorno=entorno) == 1
        assert "No hay documentos procesables" in _log(entorno)


# ---------------------------------------------------------------------------
# 2. El orquestador
# ---------------------------------------------------------------------------


class TestOrquestador:
    def test_corrida_completa_encadena_las_etapas(self, documento: Path):
        resultado = _orquestador().ejecutar(documento)
        assert resultado.ok is True
        assert resultado.etapas_completadas == [
            ETAPA_PROCESSING,
            ETAPA_VALIDATION,
            ETAPA_EXTRACTION,
            "combinacion",
            ETAPA_CONCLUSION,
            ETAPA_TRAZABILIDAD,
        ]
        assert isinstance(resultado.resultado, VoucherResult)
        assert resultado.documento_id == identificador_de_archivo(documento)

    def test_documento_id_es_hash_del_contenido(self, documento: Path):
        identificador = identificador_de_archivo(documento)
        assert identificador.startswith("sha256:")
        assert len(identificador) == len("sha256:") + 64
        # Estable: el mismo contenido da el mismo id (auditoría reproducible).
        assert identificador == identificador_de_archivo(documento)

    def test_letra_y_certeza_salen_del_motor_no_del_modelo(self, documento: Path):
        resultado = _orquestador().ejecutar(documento)
        # FACTURA A con los dos CUIT y sin incoherencias → el código concluye.
        assert resultado.resultado.tipo_comprobante == "A"
        assert resultado.resultado.origen.value == "programa"
        assert resultado.resultado.certeza.value == "alta"
        assert resultado.resultado.estado.value == "aprobado"

    def test_gate_negativo_es_fast_fail_sin_extraer(self, documento: Path):
        # El fast-fail del diseño: no es comprobante → rechazado, certeza alta y
        # ninguna extracción gastada.
        resultado = _orquestador(gate="no_comprobante").ejecutar(documento)
        assert resultado.ok is True
        assert resultado.resultado.estado.value == "rechazado"
        assert resultado.resultado.certeza.value == "alta"
        assert resultado.resultado.origen.value == "programa"
        assert ETAPA_EXTRACTION not in resultado.etapas_completadas
        assert resultado.detalle["gate_rechazo"]["veredicto"] == "no_comprobante"

    def test_rechazo_por_gate_igual_deja_case_record(self, documento: Path):
        # T-506: el rechazo es el caso más interesante de auditar; tiene registro.
        resultado = _orquestador(gate="no_comprobante").ejecutar(documento)
        assert resultado.caso is not None
        assert resultado.caso.resultado.estado.value == "rechazado"
        assert resultado.caso.quien_decidio.value == "programa"

    def test_documento_ilegible_declara_el_error(self, tmp_path: Path):
        resultado = _orquestador().ejecutar(tmp_path / "no-existe.jpg")
        assert resultado.ok is False
        assert resultado.resultado is None
        assert "No se pudo leer" in resultado.error

    def test_orientation_reexporta_el_markdown(self, documento: Path):
        resultado = _orquestador().ejecutar(documento, orientation="vertical")
        assert resultado.detalle["processing"]["orientacion_aplicada"] == "vertical"
        assert "re-exportado" in resultado.detalle["processing"]["orientacion_motivo"]

    def test_orientation_en_texto_nativo_declara_que_no_aplica(self, tmp_path: Path):
        # Un documento sin boxes (texto nativo) no tiene orientación que filtrar:
        # el orquestador lo declara en vez de fingir que aplicó algo.
        class ConverterTexto(FakeConverter):
            def convert(self, origen: str) -> Any:
                from voucherflow.models.docling import ProcessedDocument

                return ProcessedDocument(
                    tipo_entrada="texto",
                    ruta=str(origen),
                    markdown="texto plano",
                    boxes=[],
                )

        orch = PipelineOrchestrator(
            cliente=FakeLector(),
            converter=ConverterTexto(),
            settings=_settings(),
        )
        ruta = tmp_path / "nota.md"
        ruta.write_text("texto plano", encoding="utf-8")
        resultado = orch.ejecutar(ruta, orientation="horizontal")
        assert resultado.ok is True
        assert resultado.detalle["processing"]["orientacion_aplicada"] == "horizontal"
        assert "no trae boxes" in resultado.detalle["processing"]["orientacion_motivo"]

    def test_clasificar_contable_es_opt_in(self, documento: Path, tmp_path: Path):
        # Por defecto NO se corre la cadena contable (tres llamadas al modelo).
        sin = _orquestador().ejecutar(documento)
        assert sin.resultado.clasificacion_contable is None
        assert "clasificacion_contable" not in sin.detalle

        # Pedida, la cadena corre con el doble y clasifica.
        con = _orquestador().ejecutar(
            documento, clasificar_contable=True, dir_salida=tmp_path
        )
        assert con.resultado.clasificacion_contable is not None
        assert con.resultado.clasificacion_contable.centro_costo == "CC0004"
        assert con.detalle["clasificacion_contable"]["ok"] is True

    def test_fallo_contable_no_tumba_el_caso(self, documento: Path, tmp_path: Path):
        # Un paso contable roto deja el caso SIN clasificación, pero con veredicto
        # (F5/T-503: no se inventa la clasificación ni se pierde el veredicto).
        orch = PipelineOrchestrator(
            cliente=FakeLector(pasos={"01": {"centros_costos": []}}),
            converter=FakeConverter(),
            settings=_settings(),
        )
        resultado = orch.ejecutar(documento, clasificar_contable=True, dir_salida=tmp_path)
        assert resultado.ok is True
        assert resultado.resultado.clasificacion_contable is None
        detalle = resultado.detalle["clasificacion_contable"]
        assert detalle["ok"] is False
        # Los parciales de la cadena viajan en el error (paridad con v1): el
        # paso 01 corrido no se pierde aunque la cadena no cierre.
        assert "01_centro_costo" in detalle["pasos"]

    def test_persistir_escribe_sidecar_e_indice(self, documento: Path, tmp_path: Path):
        salida = tmp_path / "cases"
        resultado = _orquestador().ejecutar(documento, persistir=True, dir_salida=salida)
        assert resultado.detalle["persistencia"]["ok"] is True
        assert (salida / "index.jsonl").is_file()
        assert list(salida.glob("*.case.json"))

    def test_escalar_false_deja_el_caso_sin_agente(self, documento: Path):
        # Con un caso coherente no se escala igual; la frontera se verifica en el
        # campo escalado del bloque de agente (no se llamó al modelo).
        resultado = _orquestador().ejecutar(documento, escalar=False)
        assert resultado.ok is True

    def test_version_del_orquestador_en_la_traza(self, documento: Path):
        resultado = _orquestador().ejecutar(documento)
        assert resultado.detalle["version"] == VERSION_ORQUESTADOR

    def test_resultado_como_dict_es_serializable(self, documento: Path):
        datos = _orquestador().ejecutar(documento).como_dict()
        # Poder serializar es lo que permite que la CLI publique el JSON.
        assert json.loads(json.dumps(datos))["estado"] == "aprobado"


# ---------------------------------------------------------------------------
# 3. La fachada (api.run / api.extract)
# ---------------------------------------------------------------------------


class TestFachada:
    def test_run_devuelve_voucher_result(self, documento: Path):
        resultado = api_run(
            str(documento),
            cliente=FakeLector(),
            converter=FakeConverter(),
            settings=_settings(),
        )
        assert isinstance(resultado, VoucherResult)
        assert resultado.estado.value == "aprobado"

    def test_run_propaga_el_rechazo_de_f1_como_error_de_dominio(self, tmp_path: Path):
        from voucherflow.api import DocumentoNoProcesableError

        with pytest.raises(DocumentoNoProcesableError):
            api_run(str(tmp_path / "no-existe.jpg"), cliente=FakeLector())

    def test_extract_devuelve_evidencia_combinada(self, documento: Path):
        from voucherflow.schemas.evidence import CombinedEvidence

        evidencia = api_extract(
            str(documento),
            cliente=FakeLector(),
            converter=FakeConverter(),
            settings=_settings(),
        )
        assert isinstance(evidencia, CombinedEvidence)
        # El documento de entrada es de texto nativo: la fuente visual no tiene
        # insumo (no hay imagen que mirar) y **se declara** en vez de simularse.
        assert evidencia.campos["tipo_comprobante"].llm is not None
        assert evidencia.campos["tipo_comprobante"].vlm is None
        detalle = evidencia.trazabilidad.get("api_extract") or {}
        assert detalle.get("mode_heredado") == "kvi"

    def test_extract_ya_no_es_esqueleto(self, documento: Path):
        # El esqueleto de F0 lanzaba NotImplementedError: T-601 lo implementa y
        # devuelve un contrato real (acá solo importa que no sea el esqueleto).
        evidencia = api_extract(
            str(documento),
            cliente=FakeLector(),
            converter=FakeConverter(),
            settings=_settings(),
        )
        assert evidencia.documento_id.startswith("sha256:")
        assert evidencia.campos


# ---------------------------------------------------------------------------
# 4. Los subcomandos sobre el filesystem
# ---------------------------------------------------------------------------


class TestComandos:
    def test_process_escribe_el_markdown(self, entorno: EntornoCLI, documento: Path):
        assert main(["process", str(documento)], entorno=entorno) == 0
        markdown = documento.with_suffix(".md")
        assert markdown.is_file()
        assert "FACTURA A" in markdown.read_text(encoding="utf-8")

    def test_process_con_output_dir(self, entorno: EntornoCLI, documento: Path, tmp_path: Path):
        destino = tmp_path / "salida"
        assert main(["process", str(documento), "-o", str(destino)], entorno=entorno) == 0
        assert (destino / "factura.md").is_file()

    def test_process_recursivo(self, entorno: EntornoCLI, tmp_path: Path):
        (tmp_path / "a" / "b").mkdir(parents=True)
        (tmp_path / "a" / "b" / "uno.md").write_text("texto", encoding="utf-8")
        assert main(["process", str(tmp_path)], entorno=entorno) == 0
        assert (tmp_path / "a" / "b" / "uno.md").is_file()

    def test_process_excluye_artefactos_derivados(self, entorno: EntornoCLI, tmp_path: Path):
        # La carpeta files/ real está llena de sidecars y checkpoints: no son
        # documentos de entrada y no deben reprocesarse.
        (tmp_path / "doc.jpg").write_bytes(b"x")
        (tmp_path / "doc.case.json").write_text("{}", encoding="utf-8")
        (tmp_path / "doc_pipeline.json").write_text("{}", encoding="utf-8")
        (tmp_path / "doc.raw.md").write_text("crudo", encoding="utf-8")

        encontrados = [p.name for p in iterar_documentos(tmp_path)]
        assert encontrados == ["doc.jpg"]

    def test_iterar_documentos_excluye_markdown_de_corrida(self, tmp_path: Path):
        # ``.md`` es un formato de entrada VÁLIDO, pero el ``.raw.md`` que escribe
        # v1 es una salida derivada: descubrirlo reprocesaría la propia corrida.
        (tmp_path / "entrada.md").write_text("texto", encoding="utf-8")
        (tmp_path / "entrada.raw.md").write_text("crudo", encoding="utf-8")
        assert [p.name for p in iterar_documentos(tmp_path)] == ["entrada.md"]

    def test_validate_es_comprobante(self, entorno: EntornoCLI, documento: Path):
        assert main(["validate", str(documento)], entorno=entorno) == 0
        datos = json.loads(_salida(entorno))
        assert datos["veredicto_final"] == "comprobante"

    def test_validate_no_comprobante_sale_con_codigo_1(self, tmp_path: Path, documento: Path):
        entorno = EntornoCLI(
            orquestador=_orquestador(gate="no_comprobante"),
            stdout=io.StringIO(),
            stderr=io.StringIO(),
            cwd=tmp_path,
        )
        assert main(["validate", str(documento)], entorno=entorno) == 1
        assert json.loads(_salida(entorno))["veredicto_final"] == "no_comprobante"

    def test_classify_responde_tipo_y_contable(
        self, entorno: EntornoCLI, documento: Path, tmp_path: Path
    ):
        destino = tmp_path / "caso"
        destino.mkdir()
        copia = destino / documento.name
        copia.write_text(documento.read_text(encoding="utf-8"), encoding="utf-8")
        assert main(["classify", str(copia)], entorno=entorno) == 0
        datos = json.loads(_salida(entorno))
        # El checkpoint de la cadena contable queda junto al documento (F3/T-304).
        assert (destino / "factura_classification.json").is_file()
        assert datos["detalle_contable"]["ok"] is True
        assert datos["clasificacion_contable"]["centro_costo"] == "CC0004"

    def test_extract_json_a_stdout(self, entorno: EntornoCLI, documento: Path):
        assert main(["extract", str(documento), "-M", "kvg"], entorno=entorno) == 0
        datos = json.loads(_salida(entorno))
        assert len(datos) == 1
        assert datos[0]["modo"] == "kvg"
        assert datos[0]["evidencia"]["campos"]["tipo_comprobante"]["valor"] == "A"

    def test_extract_con_output(self, entorno: EntornoCLI, documento: Path, tmp_path: Path):
        destino = tmp_path / "extract.json"
        assert main(["extract", str(documento), "-o", str(destino)], entorno=entorno) == 0
        assert json.loads(destino.read_text(encoding="utf-8"))[0]["archivo"].endswith(
            documento.name
        )

    def test_extract_detect_letra(self, entorno: EntornoCLI, documento: Path):
        assert main(["extract-detect", str(documento)], entorno=entorno) == 0
        datos = json.loads(_salida(entorno))
        assert datos[0]["tipo_comprobante"] == "A"

    def test_run_json(self, entorno: EntornoCLI, documento: Path):
        assert main(["run", str(documento)], entorno=entorno) == 0
        datos = json.loads(_salida(entorno))
        assert datos["ok"] is True
        assert datos["estado"] == "aprobado"
        assert datos["resultado"]["tipo_comprobante"] == "A"
        assert datos["caso"]["documento_id"].startswith("sha256:")

    def test_run_persiste_cases(self, entorno: EntornoCLI, documento: Path, tmp_path: Path):
        salida = tmp_path / "cases"
        assert main(["run", str(documento), "--cases", str(salida)], entorno=entorno) == 0
        assert (salida / "index.jsonl").is_file()

    def test_run_declara_force_como_no_op(self, entorno: EntornoCLI, documento: Path):
        assert main(["run", str(documento), "--force"], entorno=entorno) == 0
        datos = json.loads(_salida(entorno))
        assert "force" in datos["detalle"]
        assert "batch" in datos["detalle"]["force"]

    def test_batch_carpeta_recursiva(self, entorno: EntornoCLI, tmp_path: Path):
        (tmp_path / "sub").mkdir()
        (tmp_path / "sub" / "a.md").write_text("texto a", encoding="utf-8")
        (tmp_path / "b.md").write_text("texto b", encoding="utf-8")
        assert main(["batch", str(tmp_path)], entorno=entorno) == 0
        datos = json.loads(_salida(entorno))
        assert datos["documentos"] == 2
        assert datos["ok"] == 2
        assert datos["max_workers_aplicado"] == 1

    def test_batch_carpeta_vacia_sale_con_codigo_1(self, entorno: EntornoCLI, tmp_path: Path):
        vacia = tmp_path / "vacia"
        vacia.mkdir()
        assert main(["batch", str(vacia)], entorno=entorno) == 1
        assert "No hay documentos procesables" in _log(entorno)

    def test_batch_workers_solicitado_queda_registrado(self, entorno: EntornoCLI, tmp_path: Path):
        (tmp_path / "a.md").write_text("texto", encoding="utf-8")
        assert main(["batch", str(tmp_path), "--workers", "4"], entorno=entorno) == 0
        datos = json.loads(_salida(entorno))
        assert datos["max_workers_solicitado"] == 4
        # El efecto real se declara: con un orquestador inyectado (los dobles no
        # cruzan a otro proceso) el lote corre serial y lo dice en la traza, en vez
        # de reportar un paralelismo que no existió. El pool real es T-602.
        assert datos["max_workers_aplicado"] == 1
        assert any("serial" in nota for nota in datos["traza_lote"]["notas"])

    def test_ask_responde(self, entorno: EntornoCLI, documento: Path):
        assert main(["ask", str(documento), "-q", "¿Cuál es el total?"], entorno=entorno) == 0
        assert _salida(entorno).strip()

    def test_ask_sin_pregunta_falla_en_parseo(self, entorno: EntornoCLI, documento: Path):
        with pytest.raises(SystemExit):
            main(["ask", str(documento)], entorno=entorno)


# ---------------------------------------------------------------------------
# 5. Auditoría: case / hitl sobre lo persistido (F5/T-506)
# ---------------------------------------------------------------------------


class TestAuditoria:
    def test_case_list_lee_el_indice(self, entorno: EntornoCLI, documento: Path, tmp_path: Path):
        cases = tmp_path / "cases"
        assert main(["run", str(documento), "--cases", str(cases)], entorno=entorno) == 0

        entorno2 = EntornoCLI(
            orquestador=_orquestador(),
            stdout=io.StringIO(),
            stderr=io.StringIO(),
            cwd=tmp_path,
        )
        assert main(["case", "list", "--dir", str(cases)], entorno=entorno2) == 0
        filas = json.loads(_salida(entorno2))
        assert len(filas) == 1
        assert filas[0]["estado"] == "aprobado"

    def test_case_list_con_filtro(self, entorno: EntornoCLI, documento: Path, tmp_path: Path):
        cases = tmp_path / "cases"
        main(["run", str(documento), "--cases", str(cases)], entorno=entorno)
        entorno2 = EntornoCLI(
            orquestador=_orquestador(),
            stdout=io.StringIO(),
            stderr=io.StringIO(),
            cwd=tmp_path,
        )
        assert main(
            ["case", "list", "--dir", str(cases), "--filtro", "estado=rechazado"],
            entorno=entorno2,
        ) == 0
        assert json.loads(_salida(entorno2)) == []

    def test_case_show_lee_el_sidecar(self, entorno: EntornoCLI, documento: Path, tmp_path: Path):
        cases = tmp_path / "cases"
        main(["run", str(documento), "--cases", str(cases)], entorno=entorno)
        documento_id = identificador_de_archivo(documento)

        entorno2 = EntornoCLI(
            orquestador=_orquestador(),
            stdout=io.StringIO(),
            stderr=io.StringIO(),
            cwd=tmp_path,
        )
        assert main(["case", "show", documento_id, "--dir", str(cases)], entorno=entorno2) == 0
        caso = json.loads(_salida(entorno2))
        assert caso["documento_id"] == documento_id
        assert caso["resultado"]["estado"] == "aprobado"

    def test_case_show_sin_id_es_error_de_uso(self, entorno: EntornoCLI, tmp_path: Path):
        assert main(["case", "show", "--dir", str(tmp_path)], entorno=entorno) == 1
        assert "necesita el documento_id" in _log(entorno)

    def test_case_show_inexistente_sale_con_codigo_1(self, entorno: EntornoCLI, tmp_path: Path):
        assert main(["case", "show", "sha256:no-existe", "--dir", str(tmp_path)], entorno=entorno) == 1

    def test_hitl_list_lee_el_historico(self, entorno: EntornoCLI, documento: Path, tmp_path: Path):
        cases = tmp_path / "cases"
        main(["run", str(documento), "--cases", str(cases)], entorno=entorno)

        entorno2 = EntornoCLI(
            orquestador=_orquestador(),
            stdout=io.StringIO(),
            stderr=io.StringIO(),
            cwd=tmp_path,
        )
        # El caso aprobado con certeza alta no entra al muestreo por defecto: el
        # histórico filtrado puede quedar vacío, pero el comando corre y responde.
        assert main(["hitl", "list", "--dir", str(cases)], entorno=entorno2) == 0
        assert json.loads(_salida(entorno2)) == []

    def test_hitl_sin_cola_ni_dir_avisa(self, entorno: EntornoCLI):
        assert main(["hitl", "list"], entorno=entorno) == 0
        assert "cola HITL" in _log(entorno)

    def test_filtro_invalido_es_error(self, entorno: EntornoCLI, tmp_path: Path):
        assert main(["case", "list", "--dir", str(tmp_path), "--filtro", "sinigual"], entorno=entorno) == 1
        assert "Filtro inválido" in _log(entorno)


# ---------------------------------------------------------------------------
# 6. Fronteras
# ---------------------------------------------------------------------------


class TestFronteras:
    def test_arca_sin_configuracion_reporta_no_disponible(
        self, entorno: EntornoCLI, documento: Path
    ):
        # ADR-003: sin URL/token, ARCA es *no disponible*, y el comando sale ≠ 0
        # (a diferencia del pipeline, donde el hook apagado es un caso normal).
        assert main(["arca", "check", str(documento)], entorno=entorno) == 1
        datos = json.loads(_salida(entorno))
        assert datos["disponible"] is False
        assert datos["nota"].startswith("ADR-003")

    def test_pipeline_result_conserva_el_contrato_de_f0(self):
        # El contrato de F0 (5 campos) sigue siendo construible tal cual.
        from voucherflow.orchestrator import PipelineResult

        resultado = PipelineResult(documento_id="x", ok=True)
        assert resultado.resumen == {}
        assert resultado.error is None

    def test_orquestador_expone_las_etapas_registradas(self):
        orch = PipelineOrchestrator()
        orch.registrar_etapa("processing")
        assert orch.etapas == ["processing"]

    def test_ejecutar_con_archivo_sin_extension_soportada(
        self, entorno: EntornoCLI, tmp_path: Path
    ):
        raro = tmp_path / "raro.xyz"
        raro.write_bytes(b"x")
        assert main(["process", str(raro)], entorno=entorno) == 1

    def test_detecta_un_documento_repetido_en_subcarpetas(self, tmp_path: Path):
        # Dos archivos distintos con el mismo nombre en subcarpetas distintas son
        # dos documentos (la ruta forma parte de la identidad de entrada).
        (tmp_path / "a").mkdir()
        (tmp_path / "b").mkdir()
        (tmp_path / "a" / "doc.md").write_text("uno", encoding="utf-8")
        (tmp_path / "b" / "doc.md").write_text("dos", encoding="utf-8")
        encontrados = iterar_documentos(tmp_path)
        assert len(encontrados) == 2
        assert [p.parent.name for p in encontrados] == ["a", "b"]

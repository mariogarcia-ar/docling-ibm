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
    NOMBRE_FALLOS,
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


@pytest.fixture
def corpus_proceso(tmp_path: Path) -> Path:
    """Corpus con la forma real y **dos homónimos**: ``files/AAAA-MM/<hash8>/``.

    El segundo homónimo es el que destapa el aplanado: con `-o` los dos
    ``factura.md`` calculaban el mismo destino y uno se perdía.
    """
    raiz = tmp_path / "files"
    for mes, lote in (("2025-08", "2D2C9343"), ("2025-08", "AABBCCDD")):
        carpeta = raiz / mes / lote
        carpeta.mkdir(parents=True, exist_ok=True)
        (carpeta / "factura.md").write_text(
            f"FACTURA A\nTotal: 121,00\n{lote}\n", encoding="utf-8"
        )
    return raiz


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
        # ``corpus`` se sumó después (viene de scripts/operacion/reducir-tokens.py,
        # que se refactorizó a src/voucherflow/corpus/): es una decisión explícita,
        # no un comando accidental. ``pdf`` es el 13.º, por el mismo motivo (viene
        # de scripts/operacion/pdf-a-imagen.py → src/voucherflow/pdf/). Si alguien
        # agrega un 14.º, este test falla.
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
            "corpus",
            "pdf",
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
    def test_el_convertidor_se_construye_una_sola_vez(self):
        """⚠️ Un `DoclingConverter` por documento cargaba los modelos de OCR cada vez.

        Medido sobre el corpus real: 18 cargas de pesos para 19 documentos (≈1 por
        documento) y el lote a ~8,5 documentos/min. La causa era que `procesar_*`
        construía `DoclingConverter()` cuando no le pasaban uno, y el orquestador le
        pasaba `None`. Ahora el orquestador lo cachea: **una** construcción por
        corrida, y el motor queda vivo entre documentos.

        Es el mismo patrón que `batch` ya usaba por worker (`_WORKER["orquestador"]`).
        """
        import voucherflow.models.docling as docling
        import voucherflow.orchestrator as orq

        construidos: list[int] = []
        original = docling.DoclingConverter

        class Espia(original):  # type: ignore[misc,valid-type]
            def __init__(self, *args: Any, **kwargs: Any) -> None:
                construidos.append(1)
                super().__init__(*args, **kwargs)

        # `procesar_*` importa el símbolo dentro del módulo `orchestrator`.
        monkeypatch = pytest.MonkeyPatch()
        monkeypatch.setattr(docling, "DoclingConverter", Espia)
        try:
            orch = orq.PipelineOrchestrator()
            convertidores = [orch.converter_efectivo() for _ in range(3)]
        finally:
            monkeypatch.undo()

        assert len(construidos) == 1, (
            f"se construyeron {len(construidos)} convertidores: los modelos de OCR "
            "se cargarían una vez por documento"
        )
        assert convertidores[0] is convertidores[1] is convertidores[2]

    def test_un_converter_inyectado_gana_y_no_se_reemplaza(self):
        """El doble de la suite manda: el orquestador no lo pisa con el real."""
        doble = FakeConverter()
        orch = PipelineOrchestrator(
            cliente=FakeLector(), converter=doble, settings=_settings()
        )
        assert orch.converter_efectivo() is doble

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

    def test_la_clase_documental_llega_al_resultado_del_run_aprobado(self, documento: Path):
        """⚠️ El veredicto del gate **no** se pierde cuando el caso pasa.

        El gate respondió "¿esto es un comprobante?" antes de extraer, pero esa
        respuesta vivía solo en el ``detalle`` de la corrida: el resultado y el
        ``CaseRecord`` de un caso aprobado no la tenían. Es justo el dato que el
        laboratorio de LLM externos emite (``es_comprobante``), así que sin esto
        las dos puntas no se podían comparar — y la única respuesta a "¿qué es
        este documento?" desaparecía en los casos que sí importan.
        """
        resultado = _orquestador().ejecutar(documento)
        campos = resultado.resultado.campos_extraidos

        assert campos.get("es_comprobante") == "comprobante"
        # Y viaja con su fuente: la decidió el **código**, no una lectura.
        combinado = resultado.evidencia.campos["es_comprobante"]
        assert combinado.fuente.value == "programa"
        assert combinado.valor == "comprobante"
        # El sostén cita la vista y la pasada que decidieron (auditable, ADR-001).
        assert "gate qween" in combinado.programa.fragmento_sustento

    def test_la_clase_documental_la_decide_el_programa_no_una_lectura(self, documento: Path):
        """La lectura **no** puede pisar el veredicto del gate.

        El modelo de extracción devuelve sus campos, pero la clase documental la
        resolvió el código: si una lectura pudiera ganar ese campo, el veredicto
        del gate se discutiría con una opinión (ADR-002: lo comprobado manda).
        """
        resultado = _orquestador(campos={"es_comprobante": "no_comprobante"}).ejecutar(documento)

        combinado = resultado.evidencia.campos["es_comprobante"]
        assert combinado.fuente.value == "programa"
        assert combinado.valor == "comprobante"
        # La lectura que discrepó queda registrada, no borrada (combinar no descarta).
        assert combinado.llm is not None
        assert combinado.llm.valor == "no_comprobante"

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
        # Los parciales de la cadena viajan en el error: el
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

    def test_process_con_output_espeja_el_arbol(
        self, entorno: EntornoCLI, corpus_proceso: Path, tmp_path: Path
    ):
        """⚠️ El bug: sin espejar, `-o` aplanaba todos los `.md` en el nivel raíz.

        Dos consecuencias, y la segunda es la grave: no se podía saber de qué
        documento era cada markdown, y dos homónimos de carpetas distintas
        escribían el MISMO archivo (uno se perdía sin que nada lo dijera). La
        salida ahora espeja desde la raíz, igual que `corpus` y `pdf`.
        """
        salida = tmp_path / "out"
        assert main(["process", str(corpus_proceso), "-o", str(salida)], entorno=entorno) == 0
        assert (salida / "2025-08" / "2D2C9343" / "factura.md").is_file()
        assert (salida / "2025-08" / "AABBCCDD" / "factura.md").is_file()
        assert not (salida / "factura.md").exists(), "el nivel de carpeta se conserva"

    def test_process_dos_homonimos_no_se_pisan(
        self, entorno: EntornoCLI, corpus_proceso: Path, tmp_path: Path
    ):
        salida = tmp_path / "out"
        main(["process", str(corpus_proceso), "-o", str(salida)], entorno=entorno)
        assert len(list(salida.rglob("*.md"))) == 2, (
            "dos `factura.md` de carpetas distintas: dos salidas, no una"
        )

    def test_process_la_raiz_no_depende_de_la_ruta_pasada(
        self, entorno: EntornoCLI, corpus_proceso: Path, tmp_path: Path
    ):
        """Procesar `files` o `files/2025-08` escribe los MISMOS archivos.

        Es la propiedad que hace reanudable la corrida (misma regla que
        `corpus`/`pdf`: la raíz sube hasta el nivel que no sea un mes).
        """
        main(["process", str(corpus_proceso), "-o", str(tmp_path / "a")], entorno=entorno)
        main(
            ["process", str(corpus_proceso / "2025-08"), "-o", str(tmp_path / "b")],
            entorno=entorno,
        )
        primera = sorted(p.relative_to(tmp_path / "a") for p in (tmp_path / "a").rglob("*.md"))
        segunda = sorted(p.relative_to(tmp_path / "b") for p in (tmp_path / "b").rglob("*.md"))
        assert primera == segunda

    def test_process_una_raiz_mal_puesta_lo_declara(
        self, entorno: EntornoCLI, corpus_proceso: Path, tmp_path: Path
    ):
        """Un `--raiz` que no contiene a los documentos deja la salida PLANA.

        Ese es el mismo síntoma que el bug que este cambio arregla, así que se
        declara en vez de dejarlo silencioso: un espejado que no espeja y no lo
        dice es lo que hace perder tiempo buscando la causa.
        """
        codigo = main(
            [
                "process",
                str(corpus_proceso),
                "-o",
                str(tmp_path / "out"),
                "--raiz",
                str(tmp_path / "otra"),
            ],
            entorno=entorno,
        )
        assert codigo == 0
        assert "queda plana" in _log(entorno)

    def test_process_no_reprocesa_su_propia_salida(
        self, entorno: EntornoCLI, tmp_path: Path
    ):
        """Con `-o` dentro de la entrada, lo ya escrito no cuenta como entrada.

        Sin esta guarda, la segunda corrida descubriría los `.md` de la primera
        (son documentos procesables) y el lote crecería solo.
        """
        entrada = tmp_path / "entrada"
        salida = entrada / "salida"
        salida.mkdir(parents=True)
        (entrada / "doc.md").write_text("FACTURA A\nTotal: 121,00\n", encoding="utf-8")
        (salida / "viejo.md").write_text("salida de una corrida anterior", encoding="utf-8")

        assert main(["process", str(entrada), "-o", str(salida)], entorno=entorno) == 0
        assert _log(entorno).count("OK:") == 1, "solo el documento de la entrada"
        assert (salida / "doc.md").is_file(), "el árbol se espeja desde la entrada"

    def test_process_con_la_salida_sobre_la_entrada_lo_declara(
        self, entorno: EntornoCLI, tmp_path: Path
    ):
        """`-o` apuntando al directorio de los documentos: se declara, no se escribe.

        Antes este caso **pisaba la entrada**: el destino se armaba por `stem`
        (`<origen>/doc.md` para el origen `<origen>/doc.md`), y el chequeo de
        colisión solo existía en la rama sin `-o`. El sistema anterior no corría
        el riesgo porque su lista de extensiones no incluía texto plano; para
        `process` (que sí acepta `.md`) era una pérdida de datos silenciosa.
        """
        entrada = tmp_path / "entrada"
        entrada.mkdir()
        original = entrada / "doc.md"
        original.write_text("FACTURA A\nTotal: 121,00\n", encoding="utf-8")

        codigo = main(["process", str(entrada), "-o", str(entrada)], entorno=entorno)
        assert codigo == 1
        assert "están dentro de la salida" in _log(entorno)
        assert original.read_text(encoding="utf-8").startswith("FACTURA A"), (
            "el documento de entrada quedó intacto"
        )

    def test_process_excluye_artefactos_derivados(self, entorno: EntornoCLI, tmp_path: Path):
        # La carpeta files/ real está llena de sidecars y checkpoints: no son
        # documentos de entrada y no deben reprocesarse.
        (tmp_path / "doc.jpg").write_bytes(b"x")
        (tmp_path / "doc.case.json").write_text("{}", encoding="utf-8")
        (tmp_path / "doc_pipeline.json").write_text("{}", encoding="utf-8")
        (tmp_path / "doc.raw.md").write_text("crudo", encoding="utf-8")

        encontrados = [p.name for p in iterar_documentos(tmp_path)]
        assert encontrados == ["doc.jpg"]

    def test_process_un_documento_no_procesable_no_aborta_el_lote(self, tmp_path: Path):
        """⚠️ Una sola foto rara abortaba los 3.729 pendientes del corpus real.

        `processing` rechaza lo que no parece un documento (relación de aspecto
        extrema, archivo ilegible) con `DocumentoNoProcesableError`, y esa excepción no
        la capturaba el comando: subía hasta `main()` y cortaba la corrida con
        traceback. Medido: 117 saltados, 0 escritos, y el resto perdido.

        Es la regla que `corpus` y `pdf` ya aplicaban (un fallo no tumba el lote). El
        caso se reproduce con un documento cuyo markdown el orquestador no puede
        producir, que es la única forma de disparar el rechazo con los dobles.
        """
        class ConverterQueRechaza(FakeConverter):
            def convert(self, origen: str) -> Any:
                if "raro" in str(origen):
                    from voucherflow.api import DocumentoNoProcesableError

                    raise DocumentoNoProcesableError(
                        "La imagen 'raro.jpg' no superó el gate de procesabilidad "
                        "(T-102): Ratio de aspecto extremo (4.92)"
                    )
                return super().convert(origen)

        entrada = tmp_path / "entrada"
        entrada.mkdir()
        for nombre in ("uno.md", "raro.md", "dos.md"):
            (entrada / nombre).write_text(f"FACTURA A\n{nombre}\n", encoding="utf-8")

        entorno_lote = EntornoCLI(
            orquestador=PipelineOrchestrator(
                cliente=FakeLector(), converter=ConverterQueRechaza(), settings=_settings()
            ),
            stdout=io.StringIO(),
            stderr=io.StringIO(),
            cwd=tmp_path,
        )
        salida = tmp_path / "out"
        codigo = main(["process", str(entrada), "-o", str(salida)], entorno=entorno_lote)

        assert codigo == 1, "el lote siguió, pero hubo un fallo → código 1"
        assert _log(entorno_lote).count("OK:") == 2, "los dos buenos se escribieron"
        assert (salida / "uno.md").is_file()
        assert (salida / "dos.md").is_file(), "el fallo del medio no cortó el lote"
        assert not (salida / "raro.md").exists()
        assert "1 documento(s) fallaron" in _log(entorno_lote)

    def test_process_deja_los_fallos_en_un_archivo(self, tmp_path: Path):
        """⚠️ La lista de fallos es lo que dice **qué reintentar** y por qué.

        La reanudación saltea lo que existe, así que un fallo se reintenta solo en la
        corrida siguiente; pero saber cuáles son y agrupados por motivo es lo que
        permite decidir si hay que arreglar algo del corpus antes de insistir.
        """
        class ConverterQueRechaza(FakeConverter):
            def convert(self, origen: str) -> Any:
                from voucherflow.api import DocumentoNoProcesableError

                raise DocumentoNoProcesableError("no parece un documento (E-DOC-2)")

        entrada = tmp_path / "entrada"
        entrada.mkdir()
        (entrada / "a.md").write_text("FACTURA A\n", encoding="utf-8")

        entorno_lote = EntornoCLI(
            orquestador=PipelineOrchestrator(
                cliente=FakeLector(), converter=ConverterQueRechaza(), settings=_settings()
            ),
            stdout=io.StringIO(),
            stderr=io.StringIO(),
            cwd=tmp_path,
        )
        salida = tmp_path / "out"
        assert main(["process", str(entrada), "-o", str(salida)], entorno=entorno_lote) == 1

        fallos = json.loads((salida / NOMBRE_FALLOS).read_text(encoding="utf-8"))
        assert fallos["total"] == 1
        assert fallos["fallos"][0]["archivo"].endswith("a.md")
        assert "no parece un documento" in fallos["fallos"][0]["error"]
        # Y no deja un `.md` que la corrida siguiente tomaría como documento de entrada.
        assert not list(salida.glob("*.md"))

    def test_process_un_markdown_ilegible_no_aborta_el_lote(self, tmp_path: Path):
        """La otra cara: un error inesperado de `processing` tampoco corta la corrida."""
        class ConverterQueExplota(FakeConverter):
            def convert(self, origen: str) -> Any:
                if "roto" in str(origen):
                    raise OSError("el archivo no se puede leer")
                return super().convert(origen)

        entrada = tmp_path / "entrada"
        entrada.mkdir()
        for nombre in ("ok.md", "roto.md"):
            (entrada / nombre).write_text("FACTURA A\n", encoding="utf-8")

        entorno_lote = EntornoCLI(
            orquestador=PipelineOrchestrator(
                cliente=FakeLector(), converter=ConverterQueExplota(), settings=_settings()
            ),
            stdout=io.StringIO(),
            stderr=io.StringIO(),
            cwd=tmp_path,
        )
        salida = tmp_path / "out"
        assert main(["process", str(entrada), "-o", str(salida)], entorno=entorno_lote) == 1
        assert (salida / "ok.md").is_file()
        assert "ERROR:" in _log(entorno_lote)

    def test_process_reanuda_y_saltea_lo_ya_hecho(
        self, entorno: EntornoCLI, corpus_proceso: Path, tmp_path: Path
    ):
        """⚠️ Sin reanudación, `process` reprocesaba todo en cada corrida.

        Medido sobre PDF nativos: el 61% del tiempo era trabajo repetido, y en un
        PDF escaneado el camino OCR se pagaba entero de nuevo (3,07 s de 8,87 s).
        `pdf` y `corpus` ya reanudaban; `process` era el único sin la regla.
        """
        salida = tmp_path / "out"
        assert main(["process", str(corpus_proceso), "-o", str(salida)], entorno=entorno) == 0
        assert _log(entorno).count("OK:") == 2, "la primera corrida procesa los dos"

        # Segunda corrida: los dos ya están → ninguna conversión, y se declara.
        entorno2 = EntornoCLI(
            orquestador=entorno.orquestador,
            stdout=io.StringIO(),
            stderr=io.StringIO(),
            cwd=tmp_path,
        )
        assert main(["process", str(corpus_proceso), "-o", str(salida)], entorno=entorno2) == 0
        assert _log(entorno2).count("OK:") == 0, "no debe reprocesar nada"
        assert _log(entorno2).count("saltado:") == 2
        assert "2 documento(s) salteado(s)" in _log(entorno2)
        assert "--force" in _log(entorno2), "tiene que decir cómo rehacerlos"

    def test_process_con_force_rehace(
        self, entorno: EntornoCLI, corpus_proceso: Path, tmp_path: Path
    ):
        salida = tmp_path / "out"
        main(["process", str(corpus_proceso), "-o", str(salida)], entorno=entorno)
        entorno2 = EntornoCLI(
            orquestador=entorno.orquestador,
            stdout=io.StringIO(),
            stderr=io.StringIO(),
            cwd=tmp_path,
        )
        codigo = main(
            ["process", str(corpus_proceso), "-o", str(salida), "--force"], entorno=entorno2
        )
        assert codigo == 0
        assert _log(entorno2).count("OK:") == 2, "--force reprocesa todo"
        assert "saltado:" not in _log(entorno2)

    def test_process_no_saltea_un_markdown_vacio(
        self, entorno: EntornoCLI, tmp_path: Path
    ):
        """⚠️ Un archivo creado y vacío NO es un paso completado.

        Es el residuo de una corrida interrumpida a mitad de escritura: darlo por
        hecho dejaría un hueco silencioso en la salida. La regla es la compartida
        (`persistencia.ya_escrito`, que exige tamaño > 0), no un `exists()` pelado.
        """
        entrada = tmp_path / "entrada"
        entrada.mkdir()
        (entrada / "doc.md").write_text("FACTURA A\nTotal: 121,00\n", encoding="utf-8")
        salida = tmp_path / "out"
        salida.mkdir()
        # El destino que la corrida va a calcular, creado pero VACÍO.
        (salida / "doc.md").write_text("", encoding="utf-8")

        assert main(["process", str(entrada), "-o", str(salida)], entorno=entorno) == 0
        assert _log(entorno).count("OK:") == 1, "el vacío se reprocesa"
        assert (salida / "doc.md").stat().st_size > 0

    def test_process_la_escritura_es_atomica(
        self, entorno: EntornoCLI, tmp_path: Path, monkeypatch
    ):
        """⚠️ Un markdown a medio escribir con el nombre final rompe la reanudación.

        La próxima corrida lo daría por bueno (es la marca de "ya hecho"), así que
        escribir atómico es lo que hace confiable al `ya_escrito`.

        ⚠️ Verificar que el import existe **no alcanza**: una versión que importe el
        helper y siga usando `write_text` pasaría esa aserción (lo verifiqué con
        mutación: el test solo-import no detectaba la regresión). Hay que comprobar
        que la corrida **pasa por el helper**.

        ⚠️ ``import voucherflow.cli.main as cli`` da la función ``main``, no el
        módulo: ``voucherflow/cli/__init__.py`` la reexporta y ensombrece el
        submódulo (mismo footgun que documenta ``test_corpus_cli.py``).
        """
        from importlib import import_module

        cli = import_module("voucherflow.cli.main")
        entrada = tmp_path / "entrada"
        entrada.mkdir()
        (entrada / "doc.md").write_text("FACTURA A\nTotal: 121,00\n", encoding="utf-8")

        llamadas: list[Path] = []
        original = cli.escribir_atomico

        def espia(destino, contenido):
            llamadas.append(Path(destino))
            return original(destino, contenido)

        monkeypatch.setattr(cli, "escribir_atomico", espia)
        assert main(["process", str(entrada), "-o", str(tmp_path / "out")], entorno=entorno) == 0
        assert llamadas == [tmp_path / "out" / "doc.md"], (
            "la corrida tiene que escribir por el helper atómico, no con write_text"
        )
        assert not list(tmp_path.rglob("*.tmp")), "no debe quedar ningún temporal"

    def test_process_escribe_en_un_directorio_que_no_existe(
        self, entorno: EntornoCLI, tmp_path: Path
    ):
        """El helper atómico crea el árbol de salida (antes lo hacía el comando)."""
        entrada = tmp_path / "entrada"
        entrada.mkdir()
        (entrada / "doc.md").write_text("FACTURA A\n", encoding="utf-8")

        destino = tmp_path / "nuevo" / "bien" / "adentro"
        assert main(["process", str(entrada), "-o", str(destino)], entorno=entorno) == 0
        assert (destino / "doc.md").is_file()

    def test_iterar_documentos_excluye_markdown_de_corrida(self, tmp_path: Path):
        # ``.md`` es un formato de entrada VÁLIDO, pero el ``.raw.md`` que escribe
        # Es una salida derivada: descubrirla reprocesaría la propia corrida.
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
        agregado = tmp_path / "lote.json"
        assert main(["batch", str(tmp_path), "-o", str(agregado)], entorno=entorno) == 0
        # T-603: `-o` es el **agregado** del lote (una entrada por documento).
        datos = json.loads(agregado.read_text(encoding="utf-8"))
        assert datos["resumen"]["documentos"] == 2
        assert datos["resumen"]["ok"] == 2
        assert len(datos["documentos"]) == 2
        assert datos["lote"]["max_workers_aplicado"] == 1

    def test_batch_carpeta_vacia_sale_con_codigo_1(self, entorno: EntornoCLI, tmp_path: Path):
        vacia = tmp_path / "vacia"
        vacia.mkdir()
        assert main(["batch", str(vacia)], entorno=entorno) == 1
        assert "No hay documentos procesables" in _log(entorno)

    def test_batch_workers_solicitado_queda_registrado(self, entorno: EntornoCLI, tmp_path: Path):
        (tmp_path / "a.md").write_text("texto", encoding="utf-8")
        agregado = tmp_path / "lote.json"
        assert main(
            ["batch", str(tmp_path), "--workers", "4", "-o", str(agregado)],
            entorno=entorno,
        ) == 0
        datos = json.loads(agregado.read_text(encoding="utf-8"))
        lote = datos["lote"]
        assert lote["max_workers_solicitado"] == 4
        # El efecto real se declara: con un orquestador inyectado (los dobles no
        # cruzan a otro proceso) el lote corre serial y lo dice en la traza, en vez
        # de reportar un paralelismo que no existió. El pool real es T-602.
        assert lote["max_workers_aplicado"] == 1
        assert any("serial" in nota for nota in lote["notas"])

    def test_ask_responde(self, entorno: EntornoCLI, documento: Path):
        assert main(["ask", str(documento), "-q", "¿Cuál es el total?"], entorno=entorno) == 0
        assert _salida(entorno).strip()

    def test_ask_sin_pregunta_falla_en_parseo(self, entorno: EntornoCLI, documento: Path):
        with pytest.raises(SystemExit):
            main(["ask", str(documento)], entorno=entorno)


class TestReanudacionExtract:
    """``extract``/``extract-detect`` reutilizan lo ya extraído (2026-09-15).

    ⚠️ Antes reprocesaban todo y **volvían a consultar al modelo** en cada corrida
    (medido: 2 llamadas por documento), y además **sobreescribían** el archivo de salida,
    así que el resultado anterior se perdía.
    """

    @pytest.fixture
    def lote(self, tmp_path: Path) -> Path:
        carpeta = tmp_path / "lote"
        carpeta.mkdir()
        (carpeta / "a.md").write_text("FACTURA A\nTotal: 121,00\n", encoding="utf-8")
        (carpeta / "b.md").write_text("FACTURA A\nTotal: 50,00\n", encoding="utf-8")
        return carpeta

    def _entorno(self, tmp_path: Path, lector: Any) -> EntornoCLI:
        return EntornoCLI(
            orquestador=PipelineOrchestrator(
                cliente=lector, converter=FakeConverter(), settings=_settings()
            ),
            stdout=io.StringIO(),
            stderr=io.StringIO(),
            cwd=tmp_path,
        )

    def test_la_segunda_corrida_no_vuelve_a_llamar_al_modelo(
        self, tmp_path: Path, lote: Path
    ):
        """⚠️ Es el punto entero: reanudar sin pagar se mide en llamadas, no en tiempo."""
        salida = tmp_path / "extract.json"
        lector = FakeLector()
        main(["extract", str(lote), "-o", str(salida)], entorno=self._entorno(tmp_path, lector))
        assert len(lector.llamadas) == 4, "2 por documento (VLM + LLM)"

        lector.llamadas.clear()
        entorno2 = self._entorno(tmp_path, lector)
        assert main(["extract", str(lote), "-o", str(salida)], entorno=entorno2) == 0
        assert lector.llamadas == [], "no debe volver a consultar al modelo"
        assert _log(entorno2).count("saltado:") == 2
        assert "2 documento(s) reutilizado(s)" in _log(entorno2)

    def test_con_force_vuelve_a_extraer(self, tmp_path: Path, lote: Path):
        salida = tmp_path / "extract.json"
        lector = FakeLector()
        main(["extract", str(lote), "-o", str(salida)], entorno=self._entorno(tmp_path, lector))

        lector.llamadas.clear()
        entorno2 = self._entorno(tmp_path, lector)
        assert main(
            ["extract", str(lote), "-o", str(salida), "--force"], entorno=entorno2
        ) == 0
        assert len(lector.llamadas) == 4, "--force rehace todo"
        assert "saltado:" not in _log(entorno2)

    def test_acumula_las_entradas_de_la_corrida_anterior(self, tmp_path: Path, lote: Path):
        """El archivo es el estado de la carpeta, no el reporte de la última corrida.

        Se ACUMULA (misma decisión que el agregado de T-603): agregar un documento a
        la carpeta y volver a correr no puede perder lo ya extraído.
        """
        salida = tmp_path / "extract.json"
        lector = FakeLector()
        main(["extract", str(lote), "-o", str(salida)], entorno=self._entorno(tmp_path, lector))
        assert len(json.loads(salida.read_text(encoding="utf-8"))) == 2

        (lote / "c.md").write_text("FACTURA A\nTotal: 10,00\n", encoding="utf-8")
        lector.llamadas.clear()
        main(["extract", str(lote), "-o", str(salida)], entorno=self._entorno(tmp_path, lector))

        datos = json.loads(salida.read_text(encoding="utf-8"))
        assert len(datos) == 3, "las dos anteriores se conservan y se suma la nueva"
        assert len(lector.llamadas) == 2, "solo se extrae el documento nuevo"

    def test_un_documento_cambiado_se_vuelve_a_extraer(self, tmp_path: Path, lote: Path):
        """⚠️ La clave es el ``documento_id`` (hash del contenido), no el nombre.

        Saltear por nombre dejaría un documento **modificado** sin reprocesar para
        siempre (misma lección que el checkpoint del lote, T-602).
        """
        salida = tmp_path / "extract.json"
        lector = FakeLector()
        main(["extract", str(lote), "-o", str(salida)], entorno=self._entorno(tmp_path, lector))

        (lote / "a.md").write_text("FACTURA A\nTotal: 999,99\n", encoding="utf-8")
        lector.llamadas.clear()
        main(["extract", str(lote), "-o", str(salida)], entorno=self._entorno(tmp_path, lector))
        assert len(lector.llamadas) == 2, "el modificado sí se reprocesa"

    def test_un_error_no_cuenta_como_hecho(self, tmp_path: Path, lote: Path):
        """⚠️ Un error NO es un paso completado (lección de T-603).

        Si contara, una re-corrida después de arreglar la causa (clave, red, modelo)
        saltearía el documento y el resumen reportaría un éxito que no ocurrió.

        ⚠️ La entrada tiene que estar **completa y bien marcada** (modo y `cli_extract`):
        si le faltara la marca, el filtro la descartaría por *eso* y el test pasaría sin
        haber evaluado nunca la regla del error — que es lo que pasó en la primera
        versión de este test (lo destapó el mutation testing: la mutación "un error
        cuenta como hecho" no la detectaba).
        """
        from importlib import import_module

        cli = import_module("voucherflow.cli.main")
        salida = tmp_path / "extract.json"
        salida.write_text(
            json.dumps(
                [
                    {
                        "archivo": "/x/a.md",
                        "documento_id": identificador_de_archivo(lote / "a.md"),
                        "modo": "kvi",
                        cli.MARCA_EXTRACT: {"version_cli": cli.VERSION_CLI},
                        "error": "OllamaError: no responde",
                    }
                ]
            ),
            encoding="utf-8",
        )

        # Control: la misma entrada, sin el error, SÍ se reutiliza (si no, el test
        # pasaría por la razón equivocada otra vez).
        lector = FakeLector()
        main(["extract", str(lote), "-o", str(salida)], entorno=self._entorno(tmp_path, lector))
        assert len(lector.llamadas) == 4, "el que había fallado se reintenta"

    def test_otro_modo_no_se_reutiliza(self, tmp_path: Path, lote: Path):
        salida = tmp_path / "extract.json"
        lector = FakeLector()
        main(
            ["extract", str(lote), "-o", str(salida), "-M", "kvi"],
            entorno=self._entorno(tmp_path, lector),
        )
        lector.llamadas.clear()
        main(
            ["extract", str(lote), "-o", str(salida), "-M", "kvg"],
            entorno=self._entorno(tmp_path, lector),
        )
        assert len(lector.llamadas) == 4, "el modo es parte de la identidad del trabajo"

    def test_una_salida_de_otra_version_no_se_reutiliza(self, tmp_path: Path, lote: Path):
        """Una entrada sin la marca del CLI viene de otra versión: se rehace."""
        salida = tmp_path / "extract.json"
        salida.write_text(
            json.dumps(
                [
                    {
                        "archivo": "/x/a.md",
                        "documento_id": identificador_de_archivo(lote / "a.md"),
                        "modo": "kvi",
                        "evidencia": {"campos": {}},
                    }
                ]
            ),
            encoding="utf-8",
        )
        lector = FakeLector()
        main(["extract", str(lote), "-o", str(salida)], entorno=self._entorno(tmp_path, lector))
        assert len(lector.llamadas) == 4

    def test_una_salida_ilegible_no_tumba_la_corrida(self, tmp_path: Path, lote: Path):
        salida = tmp_path / "extract.json"
        salida.write_text("{no es json", encoding="utf-8")
        lector = FakeLector()
        assert main(
            ["extract", str(lote), "-o", str(salida)],
            entorno=self._entorno(tmp_path, lector),
        ) == 0
        assert len(lector.llamadas) == 4, "sin nada reutilizable, se extrae todo"

    def test_las_entradas_de_otro_modo_no_se_pierden(self, tmp_path: Path, lote: Path):
        """El archivo es el estado de la carpeta: lo ajeno se conserva."""
        salida = tmp_path / "extract.json"
        lector = FakeLector()
        main(
            ["extract", str(lote), "-o", str(salida), "-M", "kvi"],
            entorno=self._entorno(tmp_path, lector),
        )
        main(
            ["extract", str(lote), "-o", str(salida), "-M", "kvg"],
            entorno=self._entorno(tmp_path, lector),
        )
        datos = json.loads(salida.read_text(encoding="utf-8"))
        modos = sorted({entrada["modo"] for entrada in datos})
        assert modos == sorted(["kvi", "kvg"]), "no se sobreescribe el modo anterior"

    def test_extract_detect_tambien_reanuda(self, tmp_path: Path, lote: Path):
        salida = tmp_path / "detect.json"
        lector = FakeLector()
        main(
            ["extract-detect", str(lote), "-o", str(salida)],
            entorno=self._entorno(tmp_path, lector),
        )
        primera = len(lector.llamadas)
        assert primera > 0

        lector.llamadas.clear()
        main(
            ["extract-detect", str(lote), "-o", str(salida)],
            entorno=self._entorno(tmp_path, lector),
        )
        assert lector.llamadas == [], "tampoco vuelve a consultar al modelo"

    def test_las_salidas_de_extract_y_detect_no_se_confunden(self, tmp_path: Path, lote: Path):
        """⚠️ El contrato de cada entrada es distinto: una letra no es una evidencia."""
        archivo = tmp_path / "compartido.json"
        lector = FakeLector()
        main(
            ["extract", str(lote), "-o", str(archivo)],
            entorno=self._entorno(tmp_path, lector),
        )
        lector.llamadas.clear()
        main(
            ["extract-detect", str(lote), "-o", str(archivo)],
            entorno=self._entorno(tmp_path, lector),
        )
        assert len(lector.llamadas) > 0, (
            "extract-detect no puede reusar entradas de extract"
        )

    def test_sin_output_avisa_del_tamano_sin_cambiar_el_contrato(
        self, tmp_path: Path, lote: Path, monkeypatch
    ):
        """⚠️ La evidencia pesa ~11,7 KB por documento (45 MB el corpus entero).

        El contrato de `stdout` **no** se toca (lo fijan T-601/T-605): sin `-o` sigue
        saliendo el JSON completo. Lo que se agrega es la advertencia con el peso real
        —una terminal con 45 MB de salida es inusable igual— para que se pueda pedir `-o`.

        El umbral se baja con monkeypatch para no generar 1 MB en el test.
        """
        from importlib import import_module

        cli = import_module("voucherflow.cli.main")
        monkeypatch.setattr(cli, "LIMITE_STDOUT_BYTES", 10)

        lector = FakeLector()
        entorno = self._entorno(tmp_path, lector)
        assert main(["extract", str(lote)], entorno=entorno) == 0

        # El dato sigue siendo el JSON completo (contrato intacto)…
        datos = json.loads(_salida(entorno))
        assert datos[0]["evidencia"]["campos"], "el detalle no se recorta"
        # …y el aviso aparece en el log, con el peso y la sugerencia.
        assert "MB por stdout" in _log(entorno)
        assert "-o ARCHIVO.json" in _log(entorno)

    def test_lotes_chicos_no_avisan(self, tmp_path: Path, lote: Path):
        """El aviso es para el lote grande: el caso normal no tiene que hacer ruido."""
        lector = FakeLector()
        entorno = self._entorno(tmp_path, lector)
        main(["extract", str(lote)], entorno=entorno)
        assert "MB por stdout" not in _log(entorno)

    def test_con_output_deja_el_detalle_en_el_archivo(self, tmp_path: Path, lote: Path):
        salida = tmp_path / "detalle.json"
        lector = FakeLector()
        entorno = self._entorno(tmp_path, lector)
        assert main(["extract", str(lote), "-o", str(salida)], entorno=entorno) == 0
        assert _salida(entorno) == "", "el dato va al archivo, no a stdout"
        assert json.loads(salida.read_text(encoding="utf-8"))[0]["evidencia"]["campos"]
        assert "documento(s) en" in _log(entorno)


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

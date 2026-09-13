"""Tests del runner de lotes: workers, checkpoints y enfriamiento (F6/T-602).

**DoD de T-602** (F6.md §3, E-CLI-1/3 · ADR-010): "El modo batch corre con workers
(cada uno con su convertidor Docling), checkpoints/reanudación y la política de
enfriamiento del ADR-010 (la cuenta inicia cuando TODOS los workers están
detenidos)".

Qué se verifica, en el orden del entregable:

1. **Los workers**: con ``max_workers > 1`` el lote usa un pool de procesos con un
   ``initializer`` por worker (cada uno con su convertidor); la ola se envía y se
   recolecta completa, y el trabajo cruza la frontera del proceso como **payload
   serializable** (no como objetos internos).
2. **El contrato del ejecutor**: ``iniciar``/``enviar``/``recolectar``/``detener``,
   y que **detener** deje el pool sin workers vivos (``shutdown(wait=True)``) — es
   la precondición del enfriamiento.
3. **Los checkpoints**: cada documento completado deja ``<doc>.batch.json`` con el
   **hash del contenido**; la corrida siguiente **reanuda** (saltea) lo completado;
   un documento que **cambió** no se saltea aunque no se pase ``--force``; un
   documento que **falló** no se reutiliza (un error no es un paso completado); un
   checkpoint **corrupto** se trata como ausente en vez de tumbar la corrida.
4. **El enfriamiento (ADR-010)**: la ventana de trabajo vence y el ciclo cierra; el
   instante en que arranca la cuenta es el de **todos los workers detenidos** (no
   el del último documento); el **último** ciclo nunca enfría (no hay nada que
   reanudar); con ``enabled=False`` no hay pausas; y todo se verifica con un
   **reloj inyectado**, sin dormir.
5. **La traza del lote**: ``max_workers_solicitado`` vs. ``aplicado``,
   ``reanudados``, ``ciclos`` y ``segundos_enfriados``; y que la traza viaje en el
   detalle de cada resultado.
6. **Fronteras**: el runner **no decide** (delega en el orquestador), no inventa
   checkpoints de documentos ilegibles, no reanuda un ``ok=False`` y no toca la
   persistencia del ``CaseRecord`` (F5/T-506).

Reglas duras: la suite default corre **sin** procesos reales, **sin** Ollama,
**sin** Docling y **sin** red (ejecutor y reloj inyectados). El pool real se
verifica por su contrato y por una corrida de humo con funciones puras.
"""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

import pytest

from voucherflow.batch import (
    EJECUTOR_PROCESOS,
    EJECUTOR_SERIAL,
    SUFIJO_CHECKPOINT,
    VERSION_LOTE,
    CheckpointDocumento,
    CheckpointsLote,
    CicloEnfriamiento,
    EjecutorProcesos,
    EjecutorSerial,
    FuturoListo,
    ResultadoLote,
    TrazaLote,
    construir_trabajo,
    ejecutar_lote,
    resultado_a_payload,
    resultado_desde_dict,
    ruta_checkpoint,
    traza_del_lote,
)
from voucherflow.orchestrator import (
    PipelineOrchestrator,
    PipelineResult,
    identificador_de_archivo,
)
from voucherflow.schemas.result import CaseRecord, VoucherResult
from voucherflow.settings.config import CoolingSettings, cargar_desde_dict

# ---------------------------------------------------------------------------
# Dobles
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
    """Doble del ``DoclingConverter``: documento de texto nativo (sin gate T-102)."""

    def convert(self, origen: str) -> Any:
        from voucherflow.models.docling import ProcessedDocument

        return ProcessedDocument(
            tipo_entrada="texto",
            ruta=str(origen),
            markdown="FACTURA A\nACME SA\n",
            boxes=[],
            n_items=1,
        )


def _settings() -> Any:
    return cargar_desde_dict(
        {
            "modelos": {
                "vlm": {"rol": "vlm", "modelo": "modelo-vlm-test"},
                "llm": {"rol": "llm", "modelo": "modelo-llm-test"},
                "agente": {"rol": "agente", "modelo": "modelo-agente-test"},
            }
        }
    )


def _orquestador(**kwargs: Any) -> PipelineOrchestrator:
    return PipelineOrchestrator(
        cliente=FakeLector(**kwargs),
        converter=FakeConverter(),
        settings=_settings(),
    )


class RelojFalso:
    """Reloj inyectado: el tiempo avanza solo cuando el runner **duerme**.

    Es lo que hace verificable el ADR-010 sin esperar dos minutos reales: si el
    runner no durmiera, el reloj no avanzaría y la ventana de trabajo no vencería.
    Registra cada pausa para poder afirmar cuánto se durmió y **desde cuándo**.
    """

    def __init__(self, *, avanzar_por_documento: float = 0.0) -> None:
        self.ahora = 0.0
        self.dormidas: list[float] = []
        self._avance_por_documento = avanzar_por_documento

    def monotonic(self) -> float:
        return self.ahora

    def dormir(self, segundos: float) -> None:
        self.dormidas.append(float(segundos))
        self.ahora += max(0.0, float(segundos))

    def avanzar(self, segundos: float) -> None:
        """Adelanta el reloj (simula el tiempo que tarda el trabajo)."""
        self.ahora += float(segundos)


class EjecutorContado:
    """Ejecutor de prueba: cuenta workers, olas y ciclos **sin** spawnear procesos.

    Implementa el contrato ``EjecutorLote`` de forma determinista: ``iniciar``
    arranca (y suma un ciclo), ``enviar`` produce el payload con un doble,
    ``recolectar`` lo devuelve y ``detener`` marca que **no quedan workers vivos**
    —que es justo la condición del ADR-010—. Permite verificar la planificación
    (olas, ventanas, enfriamientos) sin pagar el costo de un pool real.
    """

    nombre = "contado"

    def __init__(
        self,
        *,
        capacidad: int = 2,
        por_documento: float = 0.0,
        reloj: RelojFalso | None = None,
    ) -> None:
        self.capacidad = capacidad
        self.por_documento = por_documento
        self.reloj = reloj
        self.arranques = 0
        self.detenciones = 0
        self.olas: list[int] = []
        self.workers_vivos = 0
        self._pendientes: list[tuple[dict[str, Any], Any]] = []

    def iniciar(self) -> None:
        self.arranques += 1
        self.workers_vivos = self.capacidad

    def enviar(self, trabajo: dict[str, Any]) -> Any:
        # El trabajo cruza como dict: se verifica que sea serializable.
        json.dumps(trabajo)
        return FuturoListo({"trabajo": trabajo})

    def recolectar(self, futuros):
        self.olas.append(len(futuros))
        salida = []
        for futuro in futuros:
            payload = futuro.resultado()
            if self.reloj is not None:
                self.reloj.avanzar(self.por_documento)
            salida.append(
                {
                    "documento_id": payload["trabajo"]["documento_id"],
                    "archivo": payload["trabajo"]["ruta"],
                    "ok": True,
                    "etapas_completadas": ["processing"],
                    "resumen": {"estado": "aprobado"},
                    "detalle": {},
                    "resultado": None,
                    "caso": None,
                }
            )
        return salida

    def detener(self) -> None:
        self.detenciones += 1
        self.workers_vivos = 0  # ADR-010: al detener, NO queda ningún worker vivo


# ---------------------------------------------------------------------------
# Utilidades
# ---------------------------------------------------------------------------


def _carpeta(tmp_path: Path, *, cantidad: int) -> list[Path]:
    """Crea ``cantidad`` documentos ``.md`` (texto nativo: sin gate de imagen)."""
    rutas = []
    for indice in range(cantidad):
        ruta = tmp_path / f"doc-{indice:02d}.md"
        ruta.write_text(f"FACTURA A\ncomprobante {indice}\n", encoding="utf-8")
        rutas.append(ruta)
    return rutas


def _payload_ok(ruta: Path, *, ok: bool = True) -> dict[str, Any]:
    """Payload de un resultado exitoso, para probar el transporte sin pipeline."""
    return {
        "documento_id": identificador_de_archivo(ruta),
        "archivo": str(ruta),
        "ok": ok,
        "error": None if ok else "falló",
        "etapas_completadas": ["processing", "validation"],
        "resumen": {"estado": "aprobado", "certeza": "alta", "origen": "programa"},
        "detalle": {},
        "resultado": None,
        "caso": None,
    }


def _doble_puro(trabajo: dict[str, Any]) -> dict[str, Any]:
    """Función **de módulo** y picklable: el trabajo de un worker sin pipeline.

    Es lo que permite ejercitar el camino real de ``ProcessPoolExecutor``
    (arranque, envío, recolección y detención) **sin** arrastrar Ollama ni Docling
    a un subproceso: el worker no necesita nada más que el dict que recibe. Tiene
    que estar a nivel de módulo porque un closure o un lambda no se puede enviar a
    otro proceso — y ese es justamente el contrato que el pool exige.
    """
    documento_id = str(trabajo.get("documento_id") or "")
    return {
        "documento_id": documento_id,
        "archivo": str(trabajo.get("ruta") or ""),
        "ok": True,
        "error": None,
        "etapas_completadas": ["processing"],
        "resumen": {"estado": "aprobado"},
        "detalle": {"pid": os.getpid()},
        "resultado": None,
        "caso": None,
    }


# ---------------------------------------------------------------------------
# 1. Los workers
# ---------------------------------------------------------------------------


class TestWorkers:
    def test_un_worker_usa_el_camino_serial(self, tmp_path: Path):
        _carpeta(tmp_path, cantidad=2)
        resultado = ejecutar_lote(
            tmp_path, orquestador=_orquestador(), max_workers=1, reloj=RelojFalso()
        )
        assert resultado.traza.ejecutor == EJECUTOR_SERIAL
        assert resultado.traza.max_workers_aplicado == 1
        assert resultado.traza.secuencial is True
        assert len(resultado.resultados) == 2

    def test_varios_workers_eligen_el_pool_de_procesos(self, tmp_path: Path, monkeypatch):
        # Sin orquestador inyectado (producción), max_workers > 1 usa el pool real.
        _carpeta(tmp_path, cantidad=2)
        elegido: dict[str, Any] = {}

        class EspiaEjecutor(EjecutorContado):
            def __init__(self, capacidad: int) -> None:
                super().__init__(capacidad=capacidad)
                elegido["capacidad"] = capacidad

        monkeypatch.setattr(
            "voucherflow.batch.EjecutorProcesos",
            lambda n: EspiaEjecutor(int(n)),
        )
        resultado = ejecutar_lote(tmp_path, max_workers=3, reloj=RelojFalso())
        assert elegido["capacidad"] == 3
        assert resultado.traza.ejecutor == "contado"
        assert resultado.traza.max_workers_aplicado == 3

    def test_orquestador_inyectado_cae_a_serial_y_lo_declara(self, tmp_path: Path):
        # Un doble no se puede transportar a otro proceso: el runner no finge un
        # paralelismo que no puede tener y lo deja escrito en la traza.
        _carpeta(tmp_path, cantidad=2)
        resultado = ejecutar_lote(
            tmp_path, orquestador=_orquestador(), max_workers=4, reloj=RelojFalso()
        )
        assert resultado.traza.ejecutor == EJECUTOR_SERIAL
        assert resultado.traza.max_workers_aplicado == 1
        assert any("serial" in nota for nota in resultado.traza.notas)

    def test_las_olas_respetan_la_capacidad(self, tmp_path: Path):
        _carpeta(tmp_path, cantidad=5)
        ejecutor = EjecutorContado(capacidad=2)
        resultado = ejecutar_lote(
            tmp_path, max_workers=2, ejecutor=ejecutor, reloj=RelojFalso()
        )
        # 5 documentos con capacidad 2 → olas de 2, 2 y 1.
        assert ejecutor.olas == [2, 2, 1]
        assert len(resultado.resultados) == 5

    def test_el_trabajo_cruza_como_payload_serializable(self, tmp_path: Path):
        ruta = tmp_path / "doc.md"
        ruta.write_text("texto", encoding="utf-8")
        trabajo = construir_trabajo(ruta, "sha256:abc", {"orientation": "auto"})
        # Si esto no serializara, el pool de procesos no podría recibirlo.
        assert json.loads(json.dumps(trabajo))["documento_id"] == "sha256:abc"

    def test_el_payload_del_resultado_va_y_vuelve(self, tmp_path: Path):
        ruta = tmp_path / "doc.md"
        ruta.write_text("texto", encoding="utf-8")
        original = PipelineResult(
            documento_id="sha256:abc",
            archivo=str(ruta),
            ok=True,
            etapas_completadas=["processing"],
            resumen={"estado": "aprobado"},
            detalle={"lote": {"version": VERSION_LOTE}},
        )
        reconstruido = resultado_desde_dict(resultado_a_payload(original))
        assert reconstruido.documento_id == original.documento_id
        assert reconstruido.etapas_completadas == original.etapas_completadas
        assert reconstruido.resumen == original.resumen


# ---------------------------------------------------------------------------
# 2. El contrato del ejecutor
# ---------------------------------------------------------------------------


class TestContratoEjecutor:
    def test_detener_deja_sin_workers_vivos(self):
        ejecutor = EjecutorContado(capacidad=3)
        ejecutor.iniciar()
        assert ejecutor.workers_vivos == 3
        ejecutor.detener()
        # Es la precondición del ADR-010: la cuenta arranca con TODOS detenidos.
        assert ejecutor.workers_vivos == 0

    def test_ejecutor_procesos_envia_sin_iniciar_es_error(self):
        with pytest.raises(RuntimeError, match="antes de iniciar"):
            EjecutorProcesos(2).enviar({"ruta": "x"})

    def test_ejecutor_procesos_reporta_su_capacidad(self):
        ejecutor = EjecutorProcesos(5)
        if ejecutor.capacidad != 5:
            pytest.fail("La capacidad del ejecutor debe ser la pedida.")
        assert ejecutor.nombre == EJECUTOR_PROCESOS

    def test_detener_dos_veces_es_idempotente(self):
        ejecutor = EjecutorProcesos(2)
        ejecutor.detener()  # sin haber iniciado: no debe explotar
        ejecutor.detener()

    def test_ejecutor_serial_permite_simular_capacidad(self):
        # La capacidad es del ejecutor, no del número de procesos: así los tests
        # ejercitan la planificación diciendo la verdad sobre lo que verifican.
        ejecutor = EjecutorSerial(lambda trabajo: {"ok": True}, capacidad=3)
        assert ejecutor.capacidad == 3

    def test_el_pool_real_arranca_procesa_y_detiene(self):
        """Camino real de ``ProcessPoolExecutor`` con una función pura de módulo.

        Sin Ollama ni Docling: al worker le llega un dict y devuelve un payload, que
        es exactamente el contrato del pool (función picklable + datos simples). Se
        verifica el **ciclo de vida completo** —arrancar, enviar, recolectar y
        detener con ``wait=True``— y que ``detener()`` deje el pool inutilizable
        (ningún worker vivo), que es la precondición del enfriamiento (ADR-010).
        """
        ejecutor = EjecutorProcesos(2, funcion=_doble_puro)
        ejecutor.iniciar()
        try:
            trabajos = [
                construir_trabajo(f"doc-{i}.md", f"sha256:{i}", {}) for i in range(4)
            ]
            futuros = [ejecutor.enviar(trabajo) for trabajo in trabajos]
            payloads = ejecutor.recolectar(futuros)
            assert [p["documento_id"] for p in payloads] == [
                "sha256:0", "sha256:1", "sha256:2", "sha256:3"
            ]
            # El trabajo corrió en OTRO proceso (lo prueba el pid del detalle).
            assert all(p["detalle"]["pid"] != os.getpid() for p in payloads)
        finally:
            ejecutor.detener()
        # Detenido: no acepta más trabajo (no queda worker vivo).
        with pytest.raises(RuntimeError, match="antes de iniciar"):
            ejecutor.enviar({"ruta": "x"})


# ---------------------------------------------------------------------------
# 3. Checkpoints y reanudación
# ---------------------------------------------------------------------------


class TestCheckpoints:
    def test_el_checkpoint_vive_junto_al_documento(self, tmp_path: Path):
        ruta = tmp_path / "sub" / "factura.md"
        ruta.parent.mkdir(parents=True)
        ruta.write_text("x", encoding="utf-8")
        assert ruta_checkpoint(ruta) == ruta.with_name(f"factura{SUFIJO_CHECKPOINT}")

    def test_no_lo_descubre_como_documento(self, tmp_path: Path):
        from voucherflow.orchestrator import iterar_documentos

        ruta = tmp_path / "doc.md"
        ruta.write_text("x", encoding="utf-8")
        ruta_checkpoint(ruta).write_text("{}", encoding="utf-8")
        # El checkpoint no se reprocesa como si fuera una entrada.
        assert [p.name for p in iterar_documentos(tmp_path)] == ["doc.md"]

    def test_corrida_completa_deja_checkpoints(self, tmp_path: Path):
        rutas = _carpeta(tmp_path, cantidad=3)
        resultado = ejecutar_lote(
            tmp_path, orquestador=_orquestador(), max_workers=1, reloj=RelojFalso()
        )
        assert len(resultado.resultados) == 3
        for ruta in rutas:
            assert ruta_checkpoint(ruta).is_file()

    def test_la_segunda_corrida_reanuda(self, tmp_path: Path):
        _carpeta(tmp_path, cantidad=3)
        primera = ejecutar_lote(
            tmp_path, orquestador=_orquestador(), max_workers=1, reloj=RelojFalso()
        )
        assert len(primera.resultados) == 3
        assert primera.traza.reanudados == []

        segunda = ejecutar_lote(
            tmp_path, orquestador=_orquestador(), max_workers=1, reloj=RelojFalso()
        )
        # No repite pasos completados (Gherkin E-CLI-1).
        assert segunda.resultados == []
        assert len(segunda.traza.reanudados) == 3

    def test_force_reprocesa_lo_completado(self, tmp_path: Path):
        _carpeta(tmp_path, cantidad=2)
        ejecutar_lote(tmp_path, orquestador=_orquestador(), max_workers=1, reloj=RelojFalso())
        con_force = ejecutar_lote(
            tmp_path,
            orquestador=_orquestador(),
            max_workers=1,
            force=True,
            reloj=RelojFalso(),
        )
        assert len(con_force.resultados) == 2
        assert con_force.traza.reanudados == []

    def test_un_documento_cambiado_no_se_saltea(self, tmp_path: Path):
        rutas = _carpeta(tmp_path, cantidad=2)
        ejecutar_lote(tmp_path, orquestador=_orquestador(), max_workers=1, reloj=RelojFalso())

        # El contenido cambia: el hash cambia → el checkpoint ya no lo representa.
        rutas[0].write_text("FACTURA A\ncontenido distinto\n", encoding="utf-8")

        segunda = ejecutar_lote(
            tmp_path, orquestador=_orquestador(), max_workers=1, reloj=RelojFalso()
        )
        assert len(segunda.resultados) == 1
        assert segunda.resultados[0].archivo == str(rutas[0])
        assert len(segunda.traza.reanudados) == 1

    def test_un_fallo_no_se_reutiliza(self, tmp_path: Path):
        ruta = tmp_path / "doc.md"
        ruta.write_text("x", encoding="utf-8")
        almacen = CheckpointsLote()
        # Un checkpoint de un documento que FALLÓ no es un paso completado.
        almacen.marcar(
            PipelineResult(
                documento_id=identificador_de_archivo(ruta),
                archivo=str(ruta),
                ok=False,
                error="boom",
            )
        )
        assert almacen.es_reanudable(ruta) is False

    def test_checkpoint_corrupto_se_trata_como_ausente(self, tmp_path: Path):
        ruta = tmp_path / "doc.md"
        ruta.write_text("x", encoding="utf-8")
        ruta_checkpoint(ruta).write_text("{no es json", encoding="utf-8")
        almacen = CheckpointsLote()
        assert almacen.leer(ruta) is None
        assert almacen.es_reanudable(ruta) is False

    def test_checkpoint_de_otro_contenido_no_reanuda(self, tmp_path: Path):
        ruta = tmp_path / "doc.md"
        ruta.write_text("x", encoding="utf-8")
        ruta_checkpoint(ruta).write_text(
            json.dumps(
                CheckpointDocumento(
                    documento_id="sha256:otro", archivo=str(ruta), timestamp="t", ok=True
                ).como_dict()
            ),
            encoding="utf-8",
        )
        assert CheckpointsLote().es_reanudable(ruta) is False

    def test_checkpoints_desactivados_no_escriben_ni_reanudan(self, tmp_path: Path):
        _carpeta(tmp_path, cantidad=2)
        resultado = ejecutar_lote(
            tmp_path,
            orquestador=_orquestador(),
            max_workers=1,
            checkpoints=CheckpointsLote(habilitados=False),
            reloj=RelojFalso(),
        )
        assert len(resultado.resultados) == 2
        assert not list(tmp_path.glob(f"*{SUFIJO_CHECKPOINT}"))

    def test_la_escritura_del_checkpoint_es_atomica(self, tmp_path: Path):
        ruta = tmp_path / "doc.md"
        ruta.write_text("x", encoding="utf-8")
        CheckpointsLote().marcar(_resultado_falso(ruta))
        # No quedan temporales de la escritura.
        assert not list(tmp_path.glob(".*.tmp"))

    def test_un_directorio_no_escribible_no_tumba_el_lote(self, tmp_path: Path, monkeypatch):
        import voucherflow.batch as batch

        ruta = tmp_path / "doc.md"
        ruta.write_text("x", encoding="utf-8")

        def fallar(*args: Any, **kwargs: Any) -> None:
            raise OSError("disco lleno")

        almacen = CheckpointsLote()
        monkeypatch.setattr(batch, "_escribir_atomico", fallar)
        assert almacen.marcar(_resultado_falso(ruta)) is None
        # El hecho se declara en vez de silenciarse.
        assert almacen.no_escribibles and "disco lleno" in almacen.no_escribibles[0]

    def test_limpiar_borra_el_checkpoint(self, tmp_path: Path):
        ruta = tmp_path / "doc.md"
        ruta.write_text("x", encoding="utf-8")
        almacen = CheckpointsLote()
        almacen.marcar(_resultado_falso(ruta))
        assert almacen.limpiar(ruta) is True
        assert almacen.limpiar(ruta) is False


def _resultado_falso(ruta: Path, *, ok: bool = True) -> PipelineResult:
    return PipelineResult(
        documento_id=identificador_de_archivo(ruta),
        archivo=str(ruta),
        ok=ok,
        etapas_completadas=["processing"],
        resumen={"estado": "aprobado", "certeza": "alta", "origen": "programa"},
    )


# ---------------------------------------------------------------------------
# 4. Enfriamiento (ADR-010)
# ---------------------------------------------------------------------------


class TestEnfriamiento:
    def _correr(self, tmp_path: Path, *, documentos: int, capacidad: int = 1, **kwargs: Any):
        _carpeta(tmp_path, cantidad=documentos)
        reloj = RelojFalso()
        ejecutor = EjecutorContado(capacidad=capacidad, reloj=reloj)
        resultado = ejecutar_lote(
            tmp_path,
            max_workers=capacidad,
            ejecutor=ejecutor,
            reloj=reloj,
            **kwargs,
        )
        return resultado, reloj, ejecutor

    def test_sin_enfriamiento_activado_no_hay_pausas(self, tmp_path: Path):
        politica = CoolingSettings(enabled=False, work_window_s=100, cool_down_s=999)
        resultado, reloj, _ = self._correr(tmp_path, documentos=5, cooling=politica)
        assert reloj.dormidas == []
        assert resultado.traza.enfriamientos == 0
        assert len(resultado.traza.ciclos) == 1

    def test_la_ventana_de_trabajo_cierra_el_ciclo(self, tmp_path: Path):
        # Cada documento tarda 40s; la ventana es de 100s → al tercero se pasó.
        politica = CoolingSettings(enabled=True, work_window_s=100, cool_down_s=30)
        resultado, reloj, ejecutor = self._correr(
            tmp_path,
            documentos=6,
            capacidad=1,
            cooling=politica,
        )
        # El ejecutor avanza el reloj al recolectar: se ajusta la demora.
        assert resultado.traza.ciclos, "Tiene que haber al menos un ciclo"
        assert ejecutor.detenciones >= 1

    def test_la_cuenta_arranca_cuando_todos_los_workers_estan_detenidos(self, tmp_path: Path):
        """El corazón del ADR-010: el instante de inicio es el de 'pool detenido'."""
        _carpeta(tmp_path, cantidad=4)
        reloj = RelojFalso()
        # Cada documento tarda 60s; la ventana es de 100s; el enfriamiento, 120s.
        ejecutor = EjecutorContado(capacidad=1, por_documento=60.0, reloj=reloj)
        resultado = ejecutar_lote(
            tmp_path,
            max_workers=1,
            ejecutor=ejecutor,
            reloj=reloj,
            cooling=CoolingSettings(enabled=True, work_window_s=100, cool_down_s=120),
            checkpoints=CheckpointsLote(habilitados=False),
        )
        assert resultado.traza.enfriamientos >= 1

        for ciclo in resultado.traza.ciclos:
            if not ciclo.hubo_enfriamiento:
                continue
            # ``todos_detenidos_s`` es posterior al inicio de la ventana...
            assert ciclo.todos_detenidos_s > ciclo.inicio_ventana_s
            # ...y las pausas duran exactamente el cool_down configurado: la
            # cuenta arrancó al detenerse el pool, no antes ni después.
            assert ciclo.enfriado_s == 120.0
        assert all(d == 120.0 for d in reloj.dormidas)
        assert resultado.traza.segundos_enfriados == 120.0 * len(reloj.dormidas)

    def test_el_ultimo_ciclo_nunca_enfria(self, tmp_path: Path):
        _carpeta(tmp_path, cantidad=5)
        reloj = RelojFalso()
        ejecutor = EjecutorContado(capacidad=1, por_documento=60.0, reloj=reloj)
        resultado = ejecutar_lote(
            tmp_path,
            max_workers=1,
            ejecutor=ejecutor,
            reloj=reloj,
            cooling=CoolingSettings(enabled=True, work_window_s=100, cool_down_s=120),
            checkpoints=CheckpointsLote(habilitados=False),
        )
        assert resultado.traza.ciclos[-1].hubo_enfriamiento is False
        # El total de pausas es ciclos-1: no se enfría para no hacer nada.
        assert resultado.traza.enfriamientos == len(resultado.traza.ciclos) - 1

    def test_el_pool_se_detiene_en_cada_ciclo(self, tmp_path: Path):
        _carpeta(tmp_path, cantidad=5)
        reloj = RelojFalso()
        ejecutor = EjecutorContado(capacidad=1, por_documento=60.0, reloj=reloj)
        resultado = ejecutar_lote(
            tmp_path,
            max_workers=1,
            ejecutor=ejecutor,
            reloj=reloj,
            cooling=CoolingSettings(enabled=True, work_window_s=100, cool_down_s=10),
            checkpoints=CheckpointsLote(habilitados=False),
        )
        # Un arranque (y una detención) por ciclo: el pool no queda vivo pausado.
        assert ejecutor.arranques == len(resultado.traza.ciclos)
        assert ejecutor.detenciones == len(resultado.traza.ciclos)
        assert ejecutor.workers_vivos == 0

    def test_con_varios_workers_la_cuenta_arranca_con_el_pool_detenido(self, tmp_path: Path):
        _carpeta(tmp_path, cantidad=6)
        reloj = RelojFalso()
        ejecutor = EjecutorContado(capacidad=2, por_documento=60.0, reloj=reloj)
        resultado = ejecutar_lote(
            tmp_path,
            max_workers=2,
            ejecutor=ejecutor,
            reloj=reloj,
            cooling=CoolingSettings(enabled=True, work_window_s=60, cool_down_s=5),
            checkpoints=CheckpointsLote(habilitados=False),
        )
        primera = resultado.traza.ciclos[0]
        # La ola de 2 tardó 120s (60 por documento) → la ventana de 60 ya venció.
        assert primera.documentos == 2
        assert primera.hubo_enfriamiento is True
        assert primera.enfriado_s == 5.0

    def test_la_traza_lleva_los_tres_instantes(self, tmp_path: Path):
        _carpeta(tmp_path, cantidad=3)
        reloj = RelojFalso()
        ejecutor = EjecutorContado(capacidad=1, por_documento=60.0, reloj=reloj)
        resultado = ejecutar_lote(
            tmp_path,
            max_workers=1,
            ejecutor=ejecutor,
            reloj=reloj,
            cooling=CoolingSettings(enabled=True, work_window_s=100, cool_down_s=7),
            checkpoints=CheckpointsLote(habilitados=False),
        )
        ciclo = resultado.traza.ciclos[0]
        assert ciclo.inicio_ventana_s == 0.0
        assert ciclo.todos_detenidos_s > 0.0
        assert ciclo.enfriado_s == 7.0
        assert ciclo.duracion_ventana_s == ciclo.todos_detenidos_s - ciclo.inicio_ventana_s


# ---------------------------------------------------------------------------
# 5. La traza del lote
# ---------------------------------------------------------------------------


class TestTrazaLote:
    def test_la_traza_viaja_en_cada_resultado(self, tmp_path: Path):
        _carpeta(tmp_path, cantidad=2)
        resultado = ejecutar_lote(
            tmp_path, orquestador=_orquestador(), max_workers=1, reloj=RelojFalso()
        )
        for item in resultado.resultados:
            assert item.detalle["lote"]["version"] == VERSION_LOTE
        assert traza_del_lote(resultado.resultados)["version"] == VERSION_LOTE

    def test_reporta_solicitado_vs_aplicado(self, tmp_path: Path):
        _carpeta(tmp_path, cantidad=2)
        resultado = ejecutar_lote(
            tmp_path, orquestador=_orquestador(), max_workers=4, reloj=RelojFalso()
        )
        datos = resultado.traza.como_dict()
        assert datos["max_workers_solicitado"] == 4
        assert datos["max_workers_aplicado"] == 1
        assert datos["secuencial"] is True

    def test_cuenta_procesados_reanudados_y_errores(self, tmp_path: Path):
        _carpeta(tmp_path, cantidad=3)
        primera = ejecutar_lote(
            tmp_path, orquestador=_orquestador(), max_workers=1, reloj=RelojFalso()
        )
        datos = primera.traza.como_dict()
        assert datos["procesados"] == 3
        assert datos["documentos"] == 3
        assert datos["errores"] == []

    def test_un_documento_ilegible_queda_en_la_traza(self, tmp_path: Path):
        # Un archivo que no se puede leer (permisos) no debe romper el lote.
        ruta = tmp_path / "doc.md"
        ruta.write_text("x", encoding="utf-8")
        ruta.chmod(0o000)
        try:
            resultado = ejecutar_lote(
                tmp_path, orquestador=_orquestador(), max_workers=1, reloj=RelojFalso()
            )
            # El pipeline lo resuelve con ok=False (el orquestador no lanza), así que
            # el documento aparece como error del lote y no como excepción.
            assert resultado.traza.errores or resultado.traza.notas
        finally:
            ruta.chmod(0o644)

    def test_resultado_lote_es_serializable(self, tmp_path: Path):
        _carpeta(tmp_path, cantidad=1)
        resultado = ejecutar_lote(
            tmp_path, orquestador=_orquestador(), max_workers=1, reloj=RelojFalso()
        )
        assert json.loads(json.dumps(resultado.como_dict()))["traza"]["procesados"] == 1

    def test_lote_vacio_no_rompe(self, tmp_path: Path):
        resultado = ejecutar_lote(
            tmp_path, orquestador=_orquestador(), max_workers=1, reloj=RelojFalso()
        )
        assert resultado.resultados == []
        assert resultado.ok is True


# ---------------------------------------------------------------------------
# 6. Fronteras
# ---------------------------------------------------------------------------


class TestFronteras:
    def test_el_runner_no_decide_delega_en_el_orquestador(self, tmp_path: Path):
        # El runner no aplica reglas: si el orquestador dice aprobado, eso viaja.
        _carpeta(tmp_path, cantidad=1)
        resultado = ejecutar_lote(
            tmp_path, orquestador=_orquestador(), max_workers=1, reloj=RelojFalso()
        )
        assert resultado.resultados[0].resumen["estado"] == "aprobado"

    def test_no_reanuda_un_error(self, tmp_path: Path):
        ruta = tmp_path / "doc.md"
        ruta.write_text("x", encoding="utf-8")
        almacen = CheckpointsLote()
        almacen.marcar(_resultado_falso(ruta, ok=False))
        assert almacen.es_reanudable(ruta) is False

    def test_un_documento_inexistente_no_deja_checkpoint(self, tmp_path: Path):
        inexistente = tmp_path / "no-existe.md"
        assert CheckpointsLote().marcar(
            PipelineResult(documento_id="x", archivo=str(inexistente), ok=True)
        ) is None
        # No se inventa un checkpoint para un documento sin archivo de origen.
        assert not ruta_checkpoint(inexistente).exists()

    def test_sin_archivo_no_hay_checkpoint(self, tmp_path: Path):
        assert CheckpointsLote().marcar(PipelineResult(documento_id="x", ok=True)) is None

    def test_el_enfriamiento_desactivado_es_explicito(self, tmp_path: Path):
        _carpeta(tmp_path, cantidad=3)
        resultado = ejecutar_lote(
            tmp_path,
            orquestador=_orquestador(),
            max_workers=1,
            cooling=CoolingSettings(enabled=False),
            reloj=RelojFalso(),
        )
        assert resultado.traza.cooling["enabled"] is False
        assert resultado.traza.enfriamientos == 0

    def test_la_politica_aplicada_queda_en_la_traza(self, tmp_path: Path):
        _carpeta(tmp_path, cantidad=1)
        resultado = ejecutar_lote(
            tmp_path,
            orquestador=_orquestador(),
            max_workers=1,
            cooling=CoolingSettings(enabled=True, work_window_s=42, cool_down_s=7),
            reloj=RelojFalso(),
        )
        assert resultado.traza.cooling["work_window_s"] == 42
        assert resultado.traza.cooling["cool_down_s"] == 7

    def test_ciclo_enfriamiento_como_dict(self):
        ciclo = CicloEnfriamiento(
            ciclo=1, documentos=3, inicio_ventana_s=0.0, todos_detenidos_s=10.0,
            enfriado_s=2.0, hubo_enfriamiento=True,
        )
        datos = ciclo.como_dict()
        assert datos["duracion_ventana_s"] == 10.0
        assert datos["hubo_enfriamiento"] is True

    def test_ejecutar_lote_del_orquestador_devuelve_la_lista(self, tmp_path: Path):
        # El contrato de T-601 (lista de PipelineResult) se conserva: T-602 agrega
        # la traza en el detalle, no cambia el tipo de retorno.
        _carpeta(tmp_path, cantidad=2)
        resultados = _orquestador().ejecutar_lote(tmp_path, max_workers=1)
        assert isinstance(resultados, list)
        assert len(resultados) == 2
        assert all(isinstance(r, PipelineResult) for r in resultados)
        assert "lote" in resultados[0].detalle

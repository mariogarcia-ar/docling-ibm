# Orquestador + API de alto nivel + Cliente CLI (Seguimiento)

> Documento de seguimiento generado a partir de
> [`03-arquitectura-solucion.md`](../03-arquitectura-solucion.md).

## 1. Ficha del módulo

| Campo | Valor |
|---|---|
| **Módulo (paquete)** | `voucherflow/orchestrator.py` (`PipelineOrchestrator`) · `voucherflow/api.py` (facade de alto nivel) + `cli/` (`main.py` — subcomandos). Ver doc 03 §11. |
| **Responsabilidad** | Orquestar el pipeline determinístico + asistido por IA coordinando los módulos de capacidad (processing → validation → classification → extraction → conclusion) y registrando en trace; exponer una API de alto nivel por sub-capacidad y un CLI `voucherflow` (process/validate/classify/extract/run/batch/ask/arca/case/hitl) como consumidor de la librería. |
| **Épicas asociadas** | E-LIB-1 (API estable de alto nivel), E-CLI (E-CLI-1 CLI por archivo/carpeta, E-CLI-2 sidecars, E-CLI-3 workers/enfriamiento/reintentos) |
| **Fase(s) del plan** | F5 (integración del pipeline) y F6 (T-601 CLI, T-602 batch, T-603 sidecars, T-604 paridad, T-605 docs, T-606 API HTTP fase 2) |
| **Contratos que expone/consume** | Expone: `VoucherResult` (resultado tipado de la API/CLI) + sidecar de trazabilidad. Consume: `ProcessedDocument`, `ValidationResult`, candidatos/`CombinedEvidence`, `Decision`/`ConclusionResult` y `CaseRecord` (los contratos §9 de todos los módulos). |
| **ADRs relacionados** | ADR-007 (layout: CLI como entry point o paquete separado); ADR-008 (el agente es orquestado desde aquí); ADR-009 (cola HITL consultada por `case`/`hitl`); ADR-010 (política de enfriamiento del modo batch — config); ADR-005 (sidecars de salida). |
| **Interfaces clave** | `PipelineOrchestrator` (C4 nivel 2) con métodos de sub-capacidad; API facade: `procesar_documento`, `procesar_imagen`, `validar_y_procesar`, `clasificar`, `extraer`, `concluir_caso` (E-LIB-1); CLI `voucherflow` subcomandos: `process`, `validate`, `classify`, `extract`, `extract-detect`, `run`, `batch`, `ask`, `arca`, `case`, `hitl`. |
| **Responsable ciclo** | team analysis (BA/SA/PM) → team implementation |
| **Estado de diseño** | 🔴 Borrador |
| **Fecha inicio** |  |
| **Fecha fin** |  |

## 2. Estado de trazabilidad del módulo

| Elemento de arquitectura (doc 03) | Sección | Épica/Historia | Fase/Tarea | Estado |
|---|---|---|---|---|
| `PipelineOrchestrator` (orquesta PROC→VAL→CLAS→EXT→CONC→TRACE) | §3 (C4 N2) + §5.1 | E-LIB-1 | F5/F6 / T-601 | [ ] pendiente |
| API de alto nivel (facade): `procesar_documento`, `procesar_imagen`, `validar_y_procesar`, `concluir_caso`, … | §8 (cliente) + §3 (NOTEBOOK) | E-LIB-1 | F5/F6 | [ ] pendiente |
| CLI `voucherflow` subcomandos (process/validate/classify/extract/run/batch/ask/arca/case/hitl) | §8.1 | E-CLI-1 | F6 / T-601 | [ ] pendiente |
| CLI por archivo y por carpeta recursiva + flags (--force, --orientation, --condicion-impositiva, --model, --workers) | §8.1 | E-CLI-1 | F6 / T-601 | [ ] pendiente |
| Modo batch: workers, checkpoints/reanudación y política de enfriamiento (ADR-010) | §10 | E-CLI-1/3 | F6 / T-602 | [ ] pendiente |
| Sidecars con trazabilidad y salida agregada (resultado + evidencia + trazabilidad) | §9 (`CaseRecord`) | E-CLI-2 | F6 / T-603 | [ ] pendiente |
| Mapa de paridad v1→v2 verificado sobre carpetas reales de `files/` | §8.2 | E-CLI | F6 / T-604 | [ ] pendiente |
| Comandos HITL/case (cola de revisión, trazabilidad completa) | §8.1 (`hitl list`, `case show`) | E-CLI / E-CONC-4 | F6 / T-601 | [ ] pendiente |
| API HTTP (fase 2, no bloqueante para MVP) | §3 (C4 N2) | E-CLI | F6 / T-606 | [ ] pendiente |
| Secuencia general end-to-end (roles CLI/Orchestrator/módulos) | §5.1 | E-LIB-1 / E-CLI | F6 / T-601 | [ ] pendiente |
| Equivalencias CLI v1→v2 (ocr_documents→process, full_pipeline→run, ask.py→ask, …) | §8.2 | E-CLI | F6 / T-604 | [ ] pendiente |

## 3. Definition of Design / contratos a congelar

- [ ] Interfaz pública acordada: métodos de `PipelineOrchestrator` + API facade (un punto de entrada por sub-capacidad) devolviendo `VoucherResult` tipado (E-LIB-1).
- [ ] Contrato de entrada/salida alineado al schema de evidencia: la API/CLI entregan `VoucherResult` con estado/certeza/origen/trazabilidad y persisten el `CaseRecord` en sidecar (E-CLI-2).
- [ ] ADR(s) asociado(s) resueltos: ADR-007 (dónde vive el CLI), ADR-010 (config de enfriamiento del batch), ADR-009 (cola HITL consultada por `hitl list`).
- [ ] Casos de golden set / tests que lo validan: tests de integración del pipeline completo sobre golden set; paridad de comandos v1→v2 sobre muestra real de `files/` (T-604, DoD F6).

## 4. Decisiones abiertas que lo afectan

- **ADR-007 (D-7)** — Organización del paquete: CLI como entry point del mismo paquete o paquete separado; nombre de la librería.
- **ADR-010 (D-10, config)** — Política de enfriamiento por temperatura del modo batch (`cooling.enabled`, `work_window_s=600`, `cool_down_s=120`, cuenta cuando TODOS los workers están detenidos); condiciona T-602.
- **ADR-009 (D-9)** — Persistencia y cola HITL: los subcomandos `case`/`hitl` dependen de que exista el store/índice (sidecar en MVP; SQLite cuando se requiera consulta).
- **ADR-008 (D-8)** — El agente IA se orquesta desde `conclusion` vía `OllamaClient`; el orquestador no debe acoplarse a la implementación del agente.
- **E-CLI-3 matiz MoSCoW** — Enfriamiento/retries avanzados → Should; la API HTTP (T-606) es fase 2 / no bloqueante para el MVP.
- **R-05 / R-07** — Sobrecalentamiento en lotes largos (ADR-010) y riesgo de alcance creep (MoSCoW explícito) condicionan el alcance de F6.

## 5. Bitácora de seguimiento del módulo

| Fecha | Acción / hito | Responsable | Estado |
|---|---|---|---|
| _(vacío)_ | | | |

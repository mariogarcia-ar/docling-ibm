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
| **Estado de diseño** | 🟡 En implementación (T-601: orquestador + fachada + CLI; T-602/T-603/T-606 pendientes) |
| **Fecha inicio** | 2026-09-12 |
| **Fecha fin** |  |

## 2. Estado de trazabilidad del módulo

| Elemento de arquitectura (doc 03) | Sección | Épica/Historia | Fase/Tarea | Estado |
|---|---|---|---|---|
| `PipelineOrchestrator` (orquesta PROC→VAL→CLAS→EXT→CONC→TRACE) | §3 (C4 N2) + §5.1 | E-LIB-1 | F5/F6 / T-601 | [x] hecho |
| API de alto nivel (facade): `procesar_documento`, `procesar_imagen`, `validar_y_procesar`, `concluir_caso`, … | §8 (cliente) + §3 (NOTEBOOK) | E-LIB-1 | F5/F6 | [x] hecho (T-105/T-203/T-304/T-601) |
| CLI `voucherflow` subcomandos (process/validate/classify/extract/run/batch/ask/arca/case/hitl) | §8.1 | E-CLI-1 | F6 / T-601 | [x] hecho |
| CLI por archivo y por carpeta recursiva + flags (--force, --orientation, --condicion-impositiva, --model, --workers) | §8.1 | E-CLI-1 | F6 / T-601 | [x] hecho (efecto de cada flag declarado; el pool es T-602) |
| Modo batch: workers, checkpoints/reanudación y política de enfriamiento (ADR-010) | §10 | E-CLI-1/3 | F6 / T-602 | [ ] pendiente |
| Sidecars con trazabilidad y salida agregada (resultado + evidencia + trazabilidad) | §9 (`CaseRecord`) | E-CLI-2 | F6 / T-603 | [ ] pendiente (sidecar por caso vía F5/T-506 ya disponible con `--cases`) |
| Mapa de paridad v1→v2 verificado sobre carpetas reales de `files/` | §8.2 | E-CLI | F6 / T-604 | [ ] pendiente (mapa de equivalencias en el F6-subplan §3.1) |
| Comandos HITL/case (cola de revisión, trazabilidad completa) | §8.1 (`hitl list`, `case show`) | E-CLI / E-CONC-4 | F6 / T-601 | [x] hecho |
| API HTTP (fase 2, no bloqueante para MVP) | §3 (C4 N2) | E-CLI | F6 / T-606 | [ ] pendiente (fase 2) |
| Secuencia general end-to-end (roles CLI/Orchestrator/módulos) | §5.1 | E-LIB-1 / E-CLI | F6 / T-601 | [x] hecho |
| Equivalencias CLI v1→v2 (ocr_documents→process, full_pipeline→run, ask.py→ask, …) | §8.2 | E-CLI | F6 / T-604 | [ ] declaradas en el F6-subplan §3.1; **medición** pendiente (T-604) |

## 3. Definition of Design / contratos a congelar

- [x] Interfaz pública acordada: métodos de `PipelineOrchestrator` + API facade (un punto de entrada por sub-capacidad) devolviendo `VoucherResult` tipado (E-LIB-1).
  *(T-601: `ejecutar`/`ejecutar_lote` + `procesar`/`validar`/`extraer`/`combinar`/`concluir`/`clasificar_contable`, y `api.process/validate/classify/extract/run/ask`. Los keywords nuevos son aditivos: el contrato de F0 sigue siendo llamable tal cual.)*
- [x] Contrato de entrada/salida alineado al schema de evidencia: la API/CLI entregan `VoucherResult` con estado/certeza/origen/trazabilidad y persisten el `CaseRecord` en sidecar (E-CLI-2).
  *(T-601 expone el `CaseRecord` y `--cases` usa el `CaseRecorder` de F5/T-506; la **salida agregada** del lote es T-603.)*
- [ ] ADR(s) asociado(s) resueltos: ADR-007 (dónde vive el CLI), ADR-010 (config de enfriamiento del batch), ADR-009 (cola HITL consultada por `hitl list`).
  *(ADR-007 ✅ (paquete `src/` + entry point `[project.scripts]`); ADR-010 pendiente de implementar en T-602; ADR-009 parcial —`hitl list` lee el histórico del índice de T-506—.)*
- [ ] Casos de golden set / tests que lo validan: tests de integración del pipeline completo sobre golden set; paridad de comandos v1→v2 sobre muestra real de `files/` (T-604, DoD F6).
  *(T-601: `tests/test_cli_t601.py` (57) + `scripts/F6/t601.py` (9/9 + 6/6) ejercitan el pipeline completo **con dobles**; la corrida real sobre `files/` es T-604.)*

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
| 2026-09-12 | **T-601 implementado**: `PipelineOrchestrator.ejecutar` encadena F1→F5 (processing → validation → extraction → combinación → conclusión → traza) y puebla el `PipelineResult`; la **fachada** implementa `extract`/`run`/`ask`; y `cli/main.py` expone los **once** subcomandos con `argparse` (stdlib, sin dependencias nuevas) y `main()` que devuelve el código de salida. El **fast-fail del gate** resuelve un no-comprobante como `rechazado`/certeza `alta` **sin** extracción; `iterar_documentos` recorre la carpeta recursiva excluyendo los artefactos derivados. Suites: `tests/test_cli_t601.py` (57) y `scripts/F6/t601.py` (9/9 + 6/6). Pendientes del módulo: T-602 (pool/checkpoints/enfriamiento), T-603 (salida agregada) y T-606 (API HTTP, fase 2). | team implementation | Hecho |

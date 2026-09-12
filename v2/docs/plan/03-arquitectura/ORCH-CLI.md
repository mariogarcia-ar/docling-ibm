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
| **Estado de diseño** | 🟡 En implementación (T-601: orquestador + fachada + CLI; T-602: batch; T-603: sidecars + agregado; T-604: paridad v1→v2; T-605: guía del operador; T-606 pendiente) |
| **Fecha inicio** | 2026-09-12 |
| **Fecha fin** |  |

## 2. Estado de trazabilidad del módulo

| Elemento de arquitectura (doc 03) | Sección | Épica/Historia | Fase/Tarea | Estado |
|---|---|---|---|---|
| `PipelineOrchestrator` (orquesta PROC→VAL→CLAS→EXT→CONC→TRACE) | §3 (C4 N2) + §5.1 | E-LIB-1 | F5/F6 / T-601 | [x] hecho |
| API de alto nivel (facade): `procesar_documento`, `procesar_imagen`, `validar_y_procesar`, `concluir_caso`, … | §8 (cliente) + §3 (NOTEBOOK) | E-LIB-1 | F5/F6 | [x] hecho (T-105/T-203/T-304/T-601) |
| CLI `voucherflow` subcomandos (process/validate/classify/extract/run/batch/ask/arca/case/hitl) | §8.1 | E-CLI-1 | F6 / T-601 | [x] hecho |
| CLI por archivo y por carpeta recursiva + flags (--force, --orientation, --condicion-impositiva, --model, --workers) | §8.1 | E-CLI-1 | F6 / T-601 | [x] hecho (efecto de cada flag declarado; el pool es T-602) |
| Modo batch: workers, checkpoints/reanudación y política de enfriamiento (ADR-010) | §10 | E-CLI-1/3 | F6 / T-602 | [x] hecho |
| Sidecars con trazabilidad y salida agregada (resultado + evidencia + trazabilidad) | §9 (`CaseRecord`) | E-CLI-2 | F6 / T-603 | [x] hecho |
| Mapa de paridad v1→v2 verificado sobre carpetas reales de `files/` | §8.2 | E-CLI | F6 / T-604 | [x] hecho (mapa medido en 3 niveles deterministas + corte de v1 declarado) |
| Comandos HITL/case (cola de revisión, trazabilidad completa) | §8.1 (`hitl list`, `case show`) | E-CLI / E-CONC-4 | F6 / T-601 | [x] hecho |
| API HTTP (fase 2, no bloqueante para MVP) | §3 (C4 N2) | E-CLI | F6 / T-606 | [ ] pendiente (fase 2) |
| Secuencia general end-to-end (roles CLI/Orchestrator/módulos) | §5.1 | E-LIB-1 / E-CLI | F6 / T-601 | [x] hecho |
| Equivalencias CLI v1→v2 (ocr_documents→process, full_pipeline→run, ask.py→ask, …) | §8.2 | E-CLI | F6 / T-604 | [x] hecho (8/8 comandos con equivalente y **mapeo bandera por bandera** en `tests/golden/F6/subconjunto.json`) |

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
| 2026-09-12 | **T-604 implementado**: el mapa de paridad v1→v2. La comparación del veredicto **no** se hace (v1 y v2 difieren a propósito: ADR-001/ADR-006; compararla mezclaría diseño con regresión) y se declara como fuera de paridad con su derivación a F3/T-305 y F4/T-405. Se mide lo que sí es objetivo: **procedencia**, **superficie** (8/8 comandos; cada bandera de v1 mapeada a la de v2 contra el **parser real** o declarada sin equivalente con motivo) y **artefactos** (3/7 coinciden con v1). Suites: `tests/test_paridad_t604.py` (29) y `scripts/F6/t604.py`. **Hallazgos**: `process` sobrescribía su entrada con un `.md` (riesgo introducido en v2: v1 no procesaba texto plano) — se aparta a `<doc>.processed.md` y `--raw` usa `<doc>.raw.md` como v1; y el chequeo destapó dos brechas de superficie (`classify` sin `-o`, un mapeo de `--modality` inventado). | team implementation | Hecho |
| 2026-09-12 | **T-601 implementado**: `PipelineOrchestrator.ejecutar` encadena F1→F5 (processing → validation → extraction → combinación → conclusión → traza) y puebla el `PipelineResult`; la **fachada** implementa `extract`/`run`/`ask`; y `cli/main.py` expone los **once** subcomandos con `argparse` (stdlib, sin dependencias nuevas) y `main()` que devuelve el código de salida. El **fast-fail del gate** resuelve un no-comprobante como `rechazado`/certeza `alta` **sin** extracción; `iterar_documentos` recorre la carpeta recursiva excluyendo los artefactos derivados. Suites: `tests/test_cli_t601.py` (57) y `scripts/F6/t601.py` (9/9 + 6/6). | team implementation | Hecho |
| 2026-09-12 | **T-603 implementado**: la salida agregada del lote (`trace/agregado.py`). Un **único JSON** con una entrada por documento (veredicto + **puntero al sidecar**), la síntesis del lote y las métricas (F5/T-507). Es un **índice**, no una copia de los `CaseRecord`: embeberlos daría un archivo inmanejable y una segunda fuente de verdad que puede divergir del sidecar. Se **acumula** entre corridas con una entrada por documento (reprocesar actualiza, no duplica) y la escritura es atómica. En el CLI, `batch -o` escribe el agregado y `case aggregate` lo reconstruye del histórico. Suites: `tests/test_agregado_t603.py` (41) y `scripts/F6/t603.py` (8/8 + 9/9). **Hallazgos**: el agregado perdía el veredicto si el resultado llegaba sin el `VoucherResult` tipado (ahora cae al `resumen`), y recalcular las métricas solo con casos nuevos borraba un cálculo previo en una corrida sin `--cases`. | team implementation | Hecho |
| 2026-09-12 | **T-602 implementado**: `batch.py` corre el lote con **workers** (`ProcessPoolExecutor` con `initializer` por worker: cada proceso con su convertidor de Docling y su cliente, perezosos), **checkpoints/reanudación** (`<doc>.batch.json` atómicos con el hash del contenido; `--force` reprocesa) y el **enfriamiento del ADR-010** (olas → detener el pool → `todos_detenidos` → dormir `cool_down_s`; el último ciclo no enfría). El trabajo cruza la frontera del proceso como payload serializable. El CLI suma `--cooling on|off|auto`, `--work-window`, `--cool-down` y `--no-checkpoints`. Suites: `tests/test_batch_t602.py` (46, con reloj inyectado) y `scripts/F6/t602.py` (12/12 + 6/6). **Hallazgo**: `concurrent.futures.Future` expone `result()`, no `resultado()`; el test del pool destapó que el camino real con >1 worker habría roto (se adapta en la frontera). Pendientes del módulo: T-603 (salida agregada) y T-606 (API HTTP, fase 2). | team implementation | Hecho |
| 2026-09-12 | **T-605 implementado**: la **guía del operador** (`docs/usuario/`, cinco documentos) y el README de v2, que es la superficie de documentación del módulo. El contrato del CLI (`--help`, códigos de salida, defaults de `Settings`) pasa a ser la **fuente factual** de la doc y la doc se **verifica contra él por tests**: cobertura de los once subcomandos en las dos direcciones (ninguno sin documentar, ninguno inventado), cada bandera en la sección de su comando, bloques YAML contra los defaults reales y navegación sin enlaces rotos. Se documentan las **fronteras** del módulo: el CLI no carga correcciones HITL (la revisión desde la terminal es de lectura), `--force`/`--workers` solo tienen efecto real en `batch` y `ask` no produce evidencia auditable. Suites: `tests/test_docs_usuario_t605.py` (30) y `scripts/F6/t605.py` (27/27). **Hallazgos**: los tests destaparon cuatro errores en la doc recién escrita (tres enlaces relativos rotos y `hitl.*` en el documento equivocado), y la bitácora de `F6.md` arrastraba texto duplicado de una tarea previa (mismo defecto en `E-CLI.md`). Pendiente del módulo: T-606 (API HTTP, fase 2). | team implementation | Hecho |

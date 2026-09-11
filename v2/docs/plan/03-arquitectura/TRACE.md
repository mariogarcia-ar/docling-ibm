# Módulo trace — CaseRecord, trazabilidad, sidecar (Seguimiento)

> Documento de seguimiento generado a partir de
> [`03-arquitectura-solucion.md`](../03-arquitectura-solucion.md).

## 1. Ficha del módulo

| Campo | Valor |
|---|---|
| **Módulo (paquete)** | `voucherflow/trace/` (doc 03 §11: `recorder.py` — `CaseRecord` + persistencia) |
| **Responsabilidad** | Registro persistente por caso (`CaseRecord`): entrada, decisiones por etapa, prompts (hash/versión), modelos, evidencia, reglas y resultado; salida a JSON sidecar y/o a un store simple (SQLite) en fases posteriores. Da soporte al requisito de auditoría (E-CONC-5). |
| **Épicas asociadas** | E-CONC-5 (trazabilidad completa por caso), E-LIB-5 (observabilidad/diagnóstico), E-CLI-2 (sidecars con trazabilidad) |
| **Fase(s) del plan** | F5 (T-506 `CaseRecord` persistida — sidecar + índice) y F6 (T-603 sidecars con trazabilidad y salida agregada) |
| **Contratos que expone/consume** | Expone: `CaseRecord` (registro completo por caso, schema en `schemas/result.py`) y su persistencia (JSON sidecar + store en fases posteriores). Consume: evidencia/resultado/decisiones de las etapas (`CombinedEvidence`, `VoucherResult`) que registra. |
| **ADRs relacionados** | ADR-005 (trazabilidad completa y persistencia — decisión D-5); ADR-009 (persistencia de resultados y cola HITL: sidecar + SQLite `hitl_queue`); ADR-004 (el feedback del muestreo de auditoría se registra aquí como corrección). |
| **Interfaces clave** | `construir_case_record(evidencia, *, resultado, archivo, fuentes)` (arma el registro: es una proyección, no ejecuta reglas ni modelos); `CaseRecorder.registrar(caso)` → `ResultadoPersistencia(sidecar, indice, indexado)`; `leer(documento_id)` (round-trip al contrato congelado), `buscar(**filtros)` (consulta sobre el índice), `reindexar()` (reconstruye el índice desde los sidecars), `casos()`/`sidecars()` (lectura directa sin depender del índice). Contenido de `CaseRecord`: identidad, versiones (contrato + prompts), modelos por etapa, evidencia por fuente, reglas disparadas, quién decidió y el resultado consolidado. Persistencia: sidecar `<documento>.case.json` con **escritura atómica** (temporal + `os.replace` + `fsync`) + índice `index.jsonl` (**append**, deduplicado **al leer**: una fila por documento). Patrón de v1 heredado (`lib/pipeline.py`), base de los checkpoints de F6/T-602. |
| **Responsable ciclo** | team analysis (BA/SA/PM) → team implementation |
| **Estado de diseño** | ✅ Implementado (T-506: `CaseRecord` persistido en sidecar + índice; T-507: métricas sobre ese histórico) |
| **Fecha inicio** | 2026-09-11 |
| **Fecha fin** | 2026-09-11 (T-506/T-507; T-602/T-603 de F6) |

## 2. Estado de trazabilidad del módulo

| Elemento de arquitectura (doc 03) | Sección | Épica/Historia | Fase/Tarea | Estado |
|---|---|---|---|---|
| Registro persistente por caso `CaseRecord` (entrada, decisiones por etapa, prompts, modelos, evidencia, reglas, resultado) | §4.7 | E-CONC-5 | F5 / T-506 | [x] hecho (`trace/construccion.py::construir_case_record()` arma el registro desde la corrida; `trace/recorder.py::CaseRecorder.registrar()` lo persiste) |
| Persistencia: JSON sidecar y/o store simple (SQLite) en fases posteriores | §4.7 + ADR-005 | E-CONC-5 | F5 / T-506 | [x] hecho (sidecar `<documento>.case.json` con escritura **atómica** + índice `index.jsonl` consultable; el store SQLite del ADR-009 sigue siendo la fase posterior) |
| Prompts con hash/versión y modelos por etapa | §4.7 | E-CONC-5 | F5 / T-506 | [x] hecho (`version_prompt`/`modelo_por_etapa` se derivan del `meta` que F4 puso en cada lectura + el bloque `agente` de T-504; lo que la corrida no usó **no se inventa**) |
| Escritura atómica + checkpoints por documento (patrón v1 `write_results`) | §10 (NFR reanudación) | E-CLI-1 | F6 / T-602 | [ ] pendiente |
| Sidecar JSON por resultado (resultado + evidencia + trazabilidad) | §9 (`CaseRecord`) + E-CLI-2 | E-CLI-2 | F6 / T-603 | [ ] pendiente |
| `CaseRecord` como contrato de trazabilidad (§9) | §9 | E-CONC-5 | F0 / T-001 (schema) + F5 / T-506 | [x] hecho (contrato de F0 sin cambios — lo persiste T-506 y el round-trip sidecar → `CaseRecord` es sin pérdida) |
| Registro de correcciones HITL como feedback (semilla de ajuste de reglas/prompts) | §4.5 (Feedback) | E-CONC-4 | F5 / T-505 | [x] hecho (`hitl.Correccion` estructurada + `ColaHitl.feedback()` separado por motivo: agente R-09 vs regla R-03; su estado viaja al `CaseRecord` en T-506) |
| Logs estructurados por caso + diagnóstico del SDK ante latencia/status inesperado | §10 (NFR observabilidad) | E-LIB-5 | F5 / T-507 | [x] hecho (diagnóstico del cliente en `models/ollama.py::_diagnostico` desde F0/T-005; **métricas** del lote en `trace/metricas.py` sobre el histórico de T-506) |

## 3. Definition of Design / contratos a congelar

- [x] Interfaz pública acordada: estructura del `CaseRecord` (schema pydantic en `schemas/result.py`, sin cambios) y API del `recorder` (`CaseRecorder.registrar()` + `construir_case_record()`), con el contenido mínimo exigido por auditoría (E-CONC-5) verificado por el Gherkin. **T-506**.
- [x] Contrato de entrada/salida alineado al schema de evidencia: el `CaseRecord` proyecta la `CombinedEvidence`/`VoucherResult` y la persistencia acompaña al resultado en el mismo sidecar. **T-506**.
- [x] ADR(s) asociado(s) resueltos: ADR-005 (qué y cómo se persiste en MVP — sidecar + índice, **implementado** en T-506) y ADR-009 (el store SQLite para consultas por más dimensiones sigue siendo la fase posterior).
- [x] Casos de golden set / tests que lo validan: reconstrucción de un caso desde su `CaseRecord` (versión de prompt, modelo, evidencia por fuente, reglas disparadas, origen de la decisión) — `tests/test_trace_recorder_t506.py` (74) y `scripts/F5/t506.py` (4/4 + 14/14). El test de reanudación por checkpoint queda para T-602.

## 4. Decisiones abiertas que lo afectan

- **ADR-005 (D-5)** — Formato y granularidad de la trazabilidad: qué campos por etapa exige la auditoría; sidecar JSON vs. store desde el inicio (recomendación: sidecar en MVP + store posterior).
- **ADR-009 (D-9)** — Persistencia de resultados/cola HITL: en MVP sidecar + tabla SQLite local `hitl_queue` para la cola de revisión; definir cuándo indexar el histórico para consultas agregadas.
- **ADR-004 (D-4)** — El resultado del muestreo de auditoría (casos de certeza alta revisados) se registra como corrección/feedback; condiciona el modelo de datos de `trace`.
- **E-CLI-2 / E-CLI-1 (checkpoint)** — La escritura atómica y los checkpoints para reanudación (NFR §10) definen cómo `trace` interactúa con el runner de lotes (F6/T-602).

## 5. Bitácora de seguimiento del módulo

| Fecha | Acción / hito | Responsable | Estado |
|---|---|---|---|
| 2026-09-11 | **T-506 hecha**: el módulo deja de ser esqueleto. `construccion.py` **arma** el `CaseRecord` (proyección de la corrida: evidencia + decisión + resultado) y `recorder.py` lo **persiste** en **sidecar** (`<documento>.case.json`, escritura **atómica** por temporal + `os.replace` + `fsync`) + **índice** (`index.jsonl`, *append*, deduplicado **al leer**: una fila por documento). El registro responde el Gherkin de E-CONC-5 sin volver a correr el pipeline. Suites: `tests/test_trace_recorder_t506.py` (74) y `scripts/F5/t506.py` (4/4 + 14/14). | team implementation | Hecho |
| 2026-09-11 | **T-507 hecha**: `metricas.py` agrega el histórico persistido y calcula las métricas del DoD de F5 (`06-estrategia-calidad.md` §5): % certeza alta por programa, % agente IA, % rechazado (con la distinción de los que fueron de **certeza alta**), **acuerdo VLM/LLM** (sobre los campos leídos por ambas fuentes, listando los desacuerdos), cobertura HITL (obligatorios vs. muestreo) y tasa de alertas R7. Cada métrica usa su propio denominador y declara el motivo cuando no es calculable; el reporte se versiona y avisa si el lote es chico. Suites: `tests/test_metricas_t507.py` (47) y `scripts/F5/t507.py` (6/6 + 11/11). | team implementation | Hecho |

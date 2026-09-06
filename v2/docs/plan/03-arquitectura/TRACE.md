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
| **Interfaces clave** | `recorder.registrar_caso(case_record)`; contenido de `CaseRecord`: entrada, decisiones por etapa, prompts (hash/versión), modelos, evidencia, reglas y resultado; salida a JSON sidecar y/o store simple (SQLite) en fases posteriores; patrón de escritura atómica y checkpoints por documento (heredero de `lib/pipeline.py` v1). |
| **Responsable ciclo** | team analysis (BA/SA/PM) → team implementation |
| **Estado de diseño** | 🔴 Borrador |
| **Fecha inicio** |  |
| **Fecha fin** |  |

## 2. Estado de trazabilidad del módulo

| Elemento de arquitectura (doc 03) | Sección | Épica/Historia | Fase/Tarea | Estado |
|---|---|---|---|---|
| Registro persistente por caso `CaseRecord` (entrada, decisiones por etapa, prompts, modelos, evidencia, reglas, resultado) | §4.7 | E-CONC-5 | F5 / T-506 | [ ] pendiente |
| Persistencia: JSON sidecar y/o store simple (SQLite) en fases posteriores | §4.7 + ADR-005 | E-CONC-5 | F5 / T-506 | [ ] pendiente |
| Prompts con hash/versión y modelos por etapa | §4.7 | E-CONC-5 | F5 / T-506 | [ ] pendiente |
| Escritura atómica + checkpoints por documento (patrón v1 `write_results`) | §10 (NFR reanudación) | E-CLI-1 | F6 / T-602 | [ ] pendiente |
| Sidecar JSON por resultado (resultado + evidencia + trazabilidad) | §9 (`CaseRecord`) + E-CLI-2 | E-CLI-2 | F6 / T-603 | [ ] pendiente |
| `CaseRecord` como contrato de trazabilidad (§9) | §9 | E-CONC-5 | F0 / T-001 (schema) + F5 / T-506 | [ ] pendiente |
| Registro de correcciones HITL como feedback (semilla de ajuste de reglas/prompts) | §4.5 (Feedback) | E-CONC-4 | F5 / T-505 | [ ] pendiente |
| Logs estructurados por caso + diagnóstico del SDK ante latencia/status inesperado | §10 (NFR observabilidad) | E-LIB-5 | F5 / T-507 | [ ] pendiente |

## 3. Definition of Design / contratos a congelar

- [ ] Interfaz pública acordada: estructura del `CaseRecord` (schema pydantic en `schemas/result.py`) y API del `recorder` (`registrar_caso`), con contenido mínimo exigido por auditoría (E-CONC-5).
- [ ] Contrato de entrada/salida alineado al schema de evidencia: `CaseRecord` referencia `CombinedEvidence`/`VoucherResult`; la persistencia acompaña al resultado en el mismo sidecar.
- [ ] ADR(s) asociado(s) resueltos: ADR-005 (qué y cómo se persiste en MVP) y ADR-009 (cuándo agregar el store SQLite para cola HITL/consultas).
- [ ] Casos de golden set / tests que lo validan: reconstrucción de un caso desde su `CaseRecord` (versión prompt, modelo, evidencia por fuente, reglas disparadas, origen de la decisión) y test de reanudación por checkpoint (T-602).

## 4. Decisiones abiertas que lo afectan

- **ADR-005 (D-5)** — Formato y granularidad de la trazabilidad: qué campos por etapa exige la auditoría; sidecar JSON vs. store desde el inicio (recomendación: sidecar en MVP + store posterior).
- **ADR-009 (D-9)** — Persistencia de resultados/cola HITL: en MVP sidecar + tabla SQLite local `hitl_queue` para la cola de revisión; definir cuándo indexar el histórico para consultas agregadas.
- **ADR-004 (D-4)** — El resultado del muestreo de auditoría (casos de certeza alta revisados) se registra como corrección/feedback; condiciona el modelo de datos de `trace`.
- **E-CLI-2 / E-CLI-1 (checkpoint)** — La escritura atómica y los checkpoints para reanudación (NFR §10) definen cómo `trace` interactúa con el runner de lotes (F6/T-602).

## 5. Bitácora de seguimiento del módulo

| Fecha | Acción / hito | Responsable | Estado |
|---|---|---|---|
| _(vacío)_ | | | |

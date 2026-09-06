# Módulo conclusion — Reglas → agente → HITL (Seguimiento)

> Documento de seguimiento generado a partir de
> [`03-arquitectura-solucion.md`](../03-arquitectura-solucion.md).

## 1. Ficha del módulo

| Campo | Valor |
|---|---|
| **Módulo (paquete)** | `voucherflow/conclusion/` (doc 03 §11: `engine.py` — reglas cruzadas, gaps, agente, HITL; `agent.py` — agente IA; `hitl.py` — cola HITL + muestreo auditoría) |
| **Responsabilidad** | El corazón del patrón: aplicar reglas cruzadas (negocio + fast-fail + conflicto) sobre la evidencia combinada, buscar evidencia adicional por gap con límite, consolidar "certeza alta por programa" cuando el código concluye, escalar a un agente IA solo entre candidatos restantes (certeza baja), y encolar a HITL como autoridad final con muestreo de auditoría. |
| **Épicas asociadas** | E-CONC (E-CONC-1 reglas cruzadas, E-CONC-2 evidencia adicional con límite, E-CONC-3 escalado a agente, E-CONC-4 HITL + muestreo, E-CONC-5 trazabilidad) y E-LIB-5 (métricas T-507) |
| **Fase(s) del plan** | F5 (T-501..T-507); depende de F4 (T-404 evidencia combinada) y F1 (representación procesada); habilita F6 |
| **Contratos que expone/consume** | Expone: `Decision` (concluye, certeza, origen, candidatos, reglas aplicadas, alertas) dentro de `CombinedEvidence`, y `VoucherResult` (estado, tipo, certeza, origen, campos, clasificación contable, evidencia, hitl, trazabilidad). Consume: `CombinedEvidence` (extraction), motor `rules/` (cruzadas, gaps), `models/` (agente Ollama, ArcaClient opcional) y `trace/` (CaseRecord). |
| **ADRs relacionados** | ADR-002 (precedencia, insumo de reglas cruzadas); ADR-003 (búsqueda de evidencia adicional con límite — hook ARCA); ADR-004 (muestreo de auditoría de certeza alta); ADR-005 (trazabilidad `CaseRecord`); ADR-008 (implementación del agente IA); ADR-009 (persistencia resultados + cola HITL SQLite). |
| **Interfaces clave** | `concluir(evidencia)` / `concluir_caso()`; dataclass `ConclusionResult { concluye, certeza, origen, candidatos_descartados, candidatos_restantes, reglas_aplicadas, alertas, hitl }`; flujo: evidencia combinada → reglas cruzadas → ¿faltan datos? (sí → evidencia adicional con max_reintentos=N → re-aplicar) → ¿el código concluye? (sí → consolidar certeza alta/origen programa + muestreo auditoría prioridad baja; no → agente IA entre candidatos_restantes → certeza baja/origen agente_ia + HITL prioridad alta) → persistir trazabilidad. |
| **Responsable ciclo** | team analysis (BA/SA/PM) → team implementation |
| **Estado de diseño** | 🔴 Borrador |
| **Fecha inicio** |  |
| **Fecha fin** |  |

## 2. Estado de trazabilidad del módulo

| Elemento de arquitectura (doc 03) | Sección | Épica/Historia | Fase/Tarea | Estado |
|---|---|---|---|---|
| Reglas cruzadas sobre evidencia combinada (negocio + fast-fail + conflicto R7) | §4.5 | E-CONC-1 | F5 / T-501 | [ ] pendiente |
| Detección de gaps → búsqueda de evidencia adicional (max_reintentos=N, sin loop abierto) | §4.5 | E-CONC-2 | F5 / T-502 | [ ] pendiente |
| Consolidar "certeza alta · origen programa" cuando el código concluye | §4.5 | E-CONC-1 | F5 / T-503 | [ ] pendiente |
| Escalado a agente IA entre `candidatos_restantes` (no puede resucitar descartados) | §4.5 | E-CONC-3 | F5 / T-504 | [ ] pendiente |
| Consolidar "certeza baja · origen agente_ia" | §4.5 | E-CONC-3 | F5 / T-504 | [ ] pendiente |
| HITL muestra de auditoría (certeza alta, prioridad baja) | §4.5 | E-CONC-4 | F5 / T-505 | [ ] pendiente |
| HITL revisión obligatoria (certeza baja, prioridad alta) | §4.5 | E-CONC-4 | F5 / T-505 | [ ] pendiente |
| Feedback → reglas y prompts (de HITL y muestreo) | §4.5 | E-CONC-4 | F5 / T-505 | [ ] pendiente |
| Persistir trazabilidad (`CaseRecord`, sidecar + índice) | §4.5 + §4.7 | E-CONC-5 | F5 / T-506 | [ ] pendiente |
| Dataclass `ConclusionResult` + schema `Decision`/`VoucherResult` | §4.5 + §6 | E-CONC / E-LIB-2 | F0 / T-001 | [ ] pendiente |
| Métricas: % certeza alta, % agente, % rechazado, acuerdo VLM/LLM | §10 (NFR) | E-LIB-5 | F5 / T-507 | [ ] pendiente |

## 3. Definition of Design / contratos a congelar

- [ ] Interfaz pública acordada: dataclass `ConclusionResult { concluye, certeza, origen, candidatos_descartados, candidatos_restantes, reglas_aplicadas, alertas, hitl }` y schema `Decision`/`VoucherResult` (enumerados Certeza/Origen/estado).
- [ ] Contrato de entrada/salida alineado al schema de evidencia: la conclusión consume `CombinedEvidence` (con resolución por campo) y produce `VoucherResult` que se persiste junto al `CaseRecord`.
- [ ] ADR(s) asociado(s) resueltos: ADR-003 (hook ARCA con límite), ADR-004 (tasa de muestreo auditoría, sugerida 5-10%), ADR-008 (agente = llamada Ollama con prompt estructurado + blindaje post-agente), ADR-005/ADR-009 (persistencia sidecar + cola HITL SQLite).
- [ ] Casos de golden set / tests que lo validan: casos donde el código concluye (certeza alta), casos que escalan al agente (certeza baja), casos con gap cubierto por ARCA, y casos de muestreo/corrección HITL (registro de feedback).

## 4. Decisiones abiertas que lo afectan

- **ADR-003 (D-3)** — Alcance de "buscar más evidencia": en MVP el hook ARCA puede ir desactivado (no bloquea) o como extensión; definir qué gaps son críticos con negocio.
- **ADR-004 (D-4)** — Muestreo de auditoría de certeza alta: definir la tasa inicial (5-10%) en Fase 1.
- **ADR-008 (D-8)** — Implementación del agente IA: recomendación = llamada a Ollama con prompt de decisión estructurado (sin framework); validar contrato entrada/salida del agente.
- **ADR-009 (D-9)** — Persistencia de resultados y cola HITL: sidecar JSON en MVP + tabla SQLite local `hitl_queue`; decidir cuándo indexar histórico.
- **ADR-002 (D-2, bloqueante)** — La precedencia por campo de F4 condiciona las reglas cruzadas que evalúa la conclusión (riesgo R-01).
- **ADR-005 (D-5)** — Trazabilidad `CaseRecord` es requisito de auditoría (E-CONC-5); su persistencia es base del DoD de F5.

## 5. Bitácora de seguimiento del módulo

| Fecha | Acción / hito | Responsable | Estado |
|---|---|---|---|
| _(vacío)_ | | | |

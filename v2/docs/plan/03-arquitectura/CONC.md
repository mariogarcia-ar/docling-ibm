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
| **Estado de diseño** | 🟡 En implementación (T-501 y T-502 hechas: reglas cruzadas, `ConclusionResult` y búsqueda acotada de evidencia adicional; T-503..T-507 pendientes) |
| **Fecha inicio** | 2026-09-11 |
| **Fecha fin** |  |

## 2. Estado de trazabilidad del módulo

| Elemento de arquitectura (doc 03) | Sección | Épica/Historia | Fase/Tarea | Estado |
|---|---|---|---|---|
| Reglas cruzadas sobre evidencia combinada (negocio + fast-fail + conflicto R7) | §4.5 | E-CONC-1 | F5 / T-501 | [x] hecho (`rules/cruzadas.py`: `REGISTRO_CRUZADAS` con `CRUZ_1`..`CRUZ_5` como `Rule` de `tipo="cruzada"` sobre el valor vigente de cada campo, y `rules/contexto_conclusion.py::ContextoConclusion`) |
| Detección de gaps → búsqueda de evidencia adicional (max_reintentos=N, sin loop abierto) | §4.5 | E-CONC-2 | F5 / T-502 | [x] hecho (`rules/gaps.py`: `detectar_gaps()` + `CATALOGO_GAPS` + `PresupuestoBusqueda` + `buscar_evidencia_adicional()` con el `BuscadorEvidencia` inyectable; `models/arca.py` con el adaptador WSCDC; `conclusion/engine.py::concluir_con_busqueda()` re-aplica las cruzadas) |
| Consolidar "certeza alta · origen programa" cuando el código concluye | §4.5 | E-CONC-1 | F5 / T-503 | [ ] pendiente (T-501 ya deriva `certeza`/`origen` de la etapa; falta consolidar el `VoucherResult`) |
| Escalado a agente IA entre `candidatos_restantes` (no puede resucitar descartados) | §4.5 | E-CONC-3 | F5 / T-504 | [ ] pendiente |
| Consolidar "certeza baja · origen agente_ia" | §4.5 | E-CONC-3 | F5 / T-504 | [ ] pendiente |
| HITL muestra de auditoría (certeza alta, prioridad baja) | §4.5 | E-CONC-4 | F5 / T-505 | [ ] pendiente |
| HITL revisión obligatoria (certeza baja, prioridad alta) | §4.5 | E-CONC-4 | F5 / T-505 | [ ] pendiente |
| Feedback → reglas y prompts (de HITL y muestreo) | §4.5 | E-CONC-4 | F5 / T-505 | [ ] pendiente |
| Persistir trazabilidad (`CaseRecord`, sidecar + índice) | §4.5 + §4.7 | E-CONC-5 | F5 / T-506 | [ ] pendiente |
| Dataclass `ConclusionResult` + schema `Decision`/`VoucherResult` | §4.5 + §6 | E-CONC / E-LIB-2 | F0 / T-001 | [x] contrato listo (`Decision`/`VoucherResult` en F0; el `ConclusionResult` del diseño §4.5 se materializa en T-501 con los mismos campos) |
| Métricas: % certeza alta, % agente, % rechazado, acuerdo VLM/LLM | §10 (NFR) | E-LIB-5 | F5 / T-507 | [ ] pendiente |

## 3. Definition of Design / contratos a congelar

- [x] Interfaz pública acordada: dataclass `ConclusionResult { concluye, certeza, origen, candidatos_descartados, candidatos_restantes, reglas_aplicadas, alertas, hitl }` y schema `Decision`/`VoucherResult` (enumerados Certeza/Origen/estado). **T-501** la materializa en `rules/cruzadas.py` (agregando `estado`, `decision`, `faltan_datos`, `conflictos`, `motivo`, `fast_fail` y `reglas_por_familia` para la traza) y expone `conclusion.concluir()` / `conclusion.concluir_caso()`.
- [x] Contrato de entrada/salida alineado al schema de evidencia: la conclusión consume `CombinedEvidence` (con resolución por campo) y produce `VoucherResult` que se persiste junto al `CaseRecord`. **T-501** cierra el lado de la **decisión**: `concluir()` devuelve la `CombinedEvidence` con el `Decision` adjunto (solo si el código concluyó) y el `ConclusionResult` viaja en la traza; el `VoucherResult` completo (con la clasificación contable) lo consolida T-503.
- [ ] ADR(s) asociado(s) resueltos: ADR-003 (hook ARCA con límite), ADR-004 (tasa de muestreo auditoría, sugerida 5-10%), ADR-008 (agente = llamada Ollama con prompt estructurado + blindaje post-agente), ADR-005/ADR-009 (persistencia sidecar + cola HITL SQLite). **ADR-003 aplicado en T-502**: el hook es **opcional** (protocolo `BuscadorEvidencia` inyectable; sin URL el adaptador reporta desactivado y el caso sigue), la búsqueda es **puntual por gap con objetivo concreto** y el **presupuesto** tiene dos topes (consultas totales del caso y reintentos por gap). **ADR-008 parcialmente**: T-501 ya curó los candidatos a un **conjunto cerrado** (un valor no puede estar descartado y restante a la vez); el blindaje post-agente del agente real es T-504.
- [ ] Casos de golden set / tests que lo validan: casos donde el código concluye (certeza alta), casos que escalan al agente (certeza baja), casos con gap cubierto por ARCA, y casos de muestreo/corrección HITL (registro de feedback). **T-501 cubre los tres desenlaces y el gap**: `tests/test_conclusion_cruzadas_t501.py` (94) — aprobado (negocio), rechazado (fast-fail por contradicción) y revisión (gap, R7, negocio vs. documento, letra fuera de vocabulario) — más `scripts/F5/t501.py` (8/8 escenarios + 8/8 fronteras). La búsqueda por ARCA y los casos HITL son de T-502/T-505.

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
| 2026-09-11 | **T-501 hecha**: la **pasada 2** de reglas cruzadas corre sobre la evidencia **combinada** de F4. `rules/contexto_conclusion.py::ContextoConclusion` proyecta la resolución por campo de T-404 (valor vigente + fuente responsable) y evalúa la coherencia de la letra **reutilizando** `COHERENCIA_POR_CAMPO` de F4/T-403 (una sola definición de la tabla, dos usos: la fuente en la pasada 1, el caso en la pasada 2). `rules/cruzadas.py` declara `REGISTRO_CRUZADAS` con cinco `Rule` de `tipo="cruzada"` del motor de F0 (no se reescribe `Registry`): **negocio** (`CRUZ_1` → `aprobado`), **fast-fail** (`CRUZ_3` contradicción de la letra → `rechazado` con certeza alta; `CRUZ_2`/`CRUZ_4` falta de sostén → `revision`) y **conflicto** (`CRUZ_5` R7 + cruce negocio-vs-documento → `revision`). `conclusion/engine.py` deja de ser esqueleto: `concluir()` adjunta el `Decision` de F0 (que T-404 dejó en `None`) y `concluir_caso()` devuelve el `ConclusionResult` del diseño §4.5. **Hallazgo de contrato**: el validador de F0 exige `origen=programa ⇒ certeza=alta`, así que `Decision` es el veredicto **final** y un caso ambiguo viaja sin él (lo resolverán T-504/T-505); de ahí que el `ConclusionResult` sea necesario. Suites: `tests/test_conclusion_cruzadas_t501.py` (94) y `scripts/F5/t501.py` (8/8 + 8/8). | team implementation | Hecho |
| 2026-09-11 | **T-502 hecha**: la búsqueda de evidencia adicional es **puntual por gap concreto** y **acotada** (E-CONC-2). `rules/gaps.py` separa **qué falta** de **cómo se busca**: `detectar_gaps()` nombra cada falta con su criticidad y su objetivo, `CATALOGO_GAPS` declara qué es buscable (el padrón **constata**; no inventa lo que solo está en el documento) y `PresupuestoBusqueda` administra los **dos topes** de ADR-003 (consultas totales del caso y reintentos por gap). El bucle es acotado por construcción: lo no buscable no consume presupuesto, el proveedor **caído** reintenta, un **"consulté y no está"** no se insiste y el presupuesto agotado **corta** la búsqueda — no hay loop abierto. El hook es **opcional** (protocolo `BuscadorEvidencia` inyectable; `ArcaClient` sin URL reporta desactivado y el caso sigue, ADR-003). `models/arca.py` implementa el adaptador WSCDC (payload del `CmpReq`, reintentos solo de lo transitorio, traducción de la respuesta) y `conclusion/engine.py::concluir_con_busqueda()` cierra el ciclo del pseudocódigo: concluir → detectar → buscar → **fusionar** → **re-aplicar las cruzadas**. La fusión **reconstruye las `SourceEvidence` y vuelve a combinar**, para que el padrón entre por la precedencia de ADR-002 (fuente no-lectura, va primero) en lugar de pisar el valor por código. Suites: `tests/test_conclusion_gaps_t502.py` (73) y `scripts/F5/t502.py` (6/6 + 8/8). | team implementation | Hecho |

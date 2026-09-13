# Épica E-CONC — Conclusión (Seguimiento)

> Documento de seguimiento generado a partir de
> [`02-epicas-historias-usuario.md`](../02-epicas-historias-usuario.md) y
> [`05-plan-ejecucion.md`](../05-plan-ejecucion.md).

## 1. Ficha de la épica

| Campo | Valor |
|---|---|
| **Código** | E-CONC |
| **Objetivo(s) que cubre** | OBJ-5 — Refactorizar la conclusión (reglas raw + cruzadas → agente IA → HITL) |
| **Fuente de ideas** | `v2/docs/ideas/algoritmo.md` + `v2/docs/ideas/flujo_deteccion_tipo_comprobante.md` |
| **Módulo de librería** | `conclusion/` + `rules/` |
| **Fase(s) del plan** | F5 (T-501..T-507) |
| **Prioridad MoSCoW** | Must (MVP) — E-CONC-1/2/3/4/5 |
| **Responsable ciclo** | team analysis (BA/SA/PM) → team implementation |
| **Estado épica** | ✅ **DoD verificado** (E-CONC-1..5 hechas con T-501..T-507; las métricas de T-507 cierran la épica) |
| **DoR cumplido** | [x] sí |
| **Fecha inicio** | 2026-09-11 |
| **Fecha fin** | 2026-09-11 |

## 2. Definition of Done de la épica (criterios de aceptación a nivel épica)

- [x] Se aplican reglas determinísticas de negocio tributario, fast-fail por letra y conflicto sobre la evidencia combinada (pasada 2), con resultado: concluye, certeza, origen, candidatos y reglas aplicadas. *(**T-501 hecho**: `rules/cruzadas.py` declara las cinco `Rule` de `tipo="cruzada"` y `conclusion/engine.py::concluir()` corre la pasada 2 sobre el valor vigente de cada campo (la resolución de T-404). `ConclusionResult` (diseño §4.5) lleva `concluye`/`certeza`/`origen`/`estado`/candidatos/reglas/alertas y el `Decision` de F0 se adjunta cuando el código concluyó. Las tres familias: **negocio** (`CRUZ_1`), **fast-fail** (`CRUZ_3` contradicción de la letra → `rechazado`; `CRUZ_2`/`CRUZ_4` falta de sostén → `revision`) y **conflicto** (`CRUZ_5` R7 y el cruce negocio-vs-documento → `revision`, no rechazo). Suites: `tests/test_conclusion_cruzadas_t501.py` (94) y `scripts/F5/t501.py` (8/8 + 8/8))*
- [x] Cuando el código concluye de forma consistente se marca certeza=alta y origen=programa (sin pasar por el agente IA). *(**T-501 + T-503 hechos**: T-501 deriva `certeza`/`origen` de la **etapa que decidió** y el validador de F0 hace cumplir `origen=programa ⇒ certeza=alta` (un caso ambiguo viaja sin `Decision`). **T-503** cierra la consolidación en el `VoucherResult`: `certeza=alta` + `origen=programa` **si y solo si** el código concluyó de forma consistente — `concluye` ∧ **sin alertas pendientes** ∧ **con letra**. Un **rechazo firme también es certeza alta** ("no es válido" es una conclusión, no una duda); una **alerta de R7 sin resolver la baja**; y un caso ambiguo sale en revisión, `certeza=baja` y **sin** `origen` porque no lo decidió nadie. `consolidar_caso()` y `concluir_con_busqueda(consolidar_resultado=True)` exponen el flujo completo)*
- [x] La búsqueda de evidencia adicional es puntual por gap concreto, con límite de reintentos y sin loop abierto (hook ARCA opcional, ADR-003). *(**T-502 hecho**: `rules/gaps.py` nombra cada falta con su **objetivo concreto** y su criticidad, declara qué es buscable (`CATALOGO_GAPS`: el padrón constata, no inventa lo que el documento no dice) y administra los **dos topes** del presupuesto (consultas totales del caso y reintentos por gap). El bucle es **acotado por construcción**: lo no buscable no consume presupuesto, el proveedor caído reintenta, un "consulté y no está" **no** se insiste, y el presupuesto agotado **corta** la búsqueda. El hook es **opcional** (`BuscadorEvidencia` inyectable; `ArcaClient` sin URL reporta desactivado) y su reaplicación de las cruzadas vive en `conclusion/engine.py::concluir_con_busqueda()`)*
- [x] El escalado a agente IA solo ocurre cuando el código no concluye y solo entre candidatos_restantes (no puede resucitar descartados); el resultado agente se marca certeza=baja y origen=agente_ia y se encola a HITL con prioridad alta. *(**T-504 hecho**: `conclusion/agent.py` + `prompt_agente.py` (ADR-008 alternativa (a): llamada a Ollama con prompt estructurado, sin framework). Se escala **solo si el código no concluyó y hay universo**, y el agente recibe evidencia + reglas que fallaron + `candidatos_restantes` **sin** los descartados (primera capa del blindaje). El **blindaje post-agente** valida la elección contra el universo y **rechaza** la que sale de él — no la corrige a un valor parecido: la anomalía queda auditada. La certeza se deriva de la etapa (siempre `baja`); el origen es `agente_ia` solo si la elección sobrevivió. `concluir_con_agente()` cierra el pipeline. La **cola** HITL es T-505: acá se publica la expectativa de revisión con prioridad alta)*
- [x] HITL como autoridad final: revisión obligatoria de casos de certeza baja + muestreo periódico de auditoría de casos de certeza alta (tasa configurable), con registro de correcciones como feedback. *(**T-505 hecho**: `conclusion/hitl.py` implementa la política — `decidir_encolado()` es **pura** y decide, `encolar_hitl()` la materializa en el `VoucherResult`. Certeza baja → revisión **obligatoria**, prioridad `alta`; certeza alta por programa → **solo si la muestra la elige**, prioridad `baja` (10% por defecto, `HitlSettings` configurable). El muestreo es **reproducible** (`sha256(semilla:documento_id)`): auditable y estable caso por caso — un `random` sin semilla no podría explicar por qué se auditó un caso y no otro. `ColaHitl` es la cola en memoria (el store durable es T-506/ADR-009) con `pendientes()` **priorizada** (R-09: lo obligatorio primero), `obligatorios()`, `muestreados()` y `corregidos()`; el mismo caso **no se duplica** y re-encolar uno revisado no borra el trabajo humano. `registrar_correccion()` guarda el feedback **estructurado** (campo / antes / después / quién) y `confirmar()` distingue «la regla acertó» de «no se revisó». `feedback()` **separa** las correcciones de certeza baja (el agente se equivocó, R-09) de las del muestreo (una regla acierta por accidente, R-03). Suites: `tests/test_conclusion_hitl_t505.py` (58) y `scripts/F5/t505.py` (6/6 + 13/13))*
- [x] Trazabilidad completa por caso (`CaseRecord` persistida junto al resultado — JSON sidecar/store) con versión de prompt, modelo, evidencia por fuente, reglas disparadas y origen de la decisión. *(**T-506 hecho**: `trace/construccion.py` **arma** el registro auditable desde los artefactos de la corrida (una **proyección**: no corre reglas ni modelos) y `trace/recorder.py` lo **persiste**: **sidecar** `<documento>.case.json` con **escritura atómica** (temporal + `os.replace` + `fsync`: nunca se lee un JSON truncado) más un **índice** `index.jsonl` de una línea por caso, consultable (`buscar(estado="rechazado")`, `buscar(hitl_requerido=True)`). El registro responde los cinco datos del Gherkin **leyendo un archivo**, sin volver a correr el pipeline. El índice es un **derivado**: *append* (barato y seguro en concurrencia), deduplicación **al leer** (una fila por documento —una por corrida inflaría los agregados de T-507—) y `reindexar()` que lo reconstruye desde los sidecars. Suites: `tests/test_trace_recorder_t506.py` (74) y `scripts/F5/t506.py` (4/4 + 14/14))*
- [x] Métricas del pipeline reportadas: % certeza alta, % agente, % rechazado, acuerdo VLM/LLM (T-507 / E-LIB-5). *(**T-507 hecho**: `trace/metricas.py` calcula las métricas sobre el `CaseRecord` persistido de T-506: % certeza alta por programa, % agente IA, % rechazado —con la distinción de cuántos rechazos fueron de **certeza alta**, porque un rechazo firme es una conclusión—, **acuerdo VLM/LLM** (sobre los campos que leyeron *ambas* fuentes: un campo que solo leyó una es cobertura, no desacuerdo), cobertura HITL (obligatorios vs. muestreo) y tasa de alertas R7. Cada métrica usa su propio denominador y declara **por qué** cuando no es calculable («no saber» ≠ «saber que es cero»); el reporte se versiona y avisa si el lote es chico. Suites: `tests/test_metricas_t507.py` (47) y `scripts/F5/t507.py` (6/6 + 11/11, con modo `--historico DIR`))*
- [ ] Paridad verificable con v1 sobre el golden set donde aplique (DoD de F5 en `05-plan-ejecucion.md`).
- [ ] Documentación/contratos actualizados (README/ADR si cambia una decisión).

## 3. Historias de usuario y seguimiento

### E-CONC-1 · Reglas cruzadas sobre evidencia combinada (pasada 2)
- **Estado**: [ ] Pendiente · [ ] En desarrollo · [ ] En QA · [x] **Hecho** (T-501, 2026-09-11)
- **Responsable**: team analysis / team implementation
- **Como** sistema,
  **quiero** aplicar reglas determinísticas de negocio tributario, fast-fail por
  letra y conflicto sobre la evidencia combinada
  **para** concluir el caso con certeza alta cuando el código alcanza.
- **Criterios de aceptación (Gherkin):**

```gherkin
Dado evidencia combinada de VLM y LLM
Cuando se aplican las reglas cruzadas
Entonces corren reglas de negocio tributario, fast-fail por letra y conflicto
Y el resultado incluye: concluye, certeza, origen, candidatos y reglas aplicadas

Regla: concluye por programa
  Dado que las reglas concluyen de forma consistente
  Cuando se consolida el resultado
  Entonces se marca certeza=alta y origen=programa
  Y NO pasó por el agente de IA para decidir
```

### E-CONC-2 · Búsqueda puntual de evidencia adicional (con límite)
- **Estado**: [ ] Pendiente · [ ] En desarrollo · [ ] En QA · [x] **Hecho** (T-502, 2026-09-11)
- **Responsable**: team analysis / team implementation
- **Como** sistema,
  **quiero** buscar evidencia adicional solo cuando hay un gap concreto y con un
  límite de reintentos (ej. consulta al padrón ARCA)
  **para** cubrir datos faltantes sin entrar en un loop abierto de búsqueda.
- **Criterios de aceptación (Gherkin):**

```gherkin
Dado que faltan datos (resultado.faltan_datos)
Cuando se ejecuta la conclusión
Entonces se busca evidencia adicional por gap concreto con max_reintentos=N
Y al agotar el límite sin resolver, el caso escala al agente IA

Regla: no loop abierto
  Dado que la evidencia adicional no cubre el gap
  Cuando se alcanza el límite de reintentos
  Entonces NO se sigue buscando indefinidamente
  Y el caso continúa al siguiente paso del flujo
```

### E-CONC-3 · Escalado a agente IA (solo entre candidatos no descartados)
- **Estado**: [ ] Pendiente · [ ] En desarrollo · [ ] En QA · [x] **Hecho** (T-504, 2026-09-11)
- **Responsable**: team analysis / team implementation
- **Como** sistema,
  **quiero** escalar a un agente de IA únicamente cuando el código no concluye y
  solo entre los candidatos que sobrevivieron al descarte
  **para** que el agente no pueda resucitar una opción ya eliminada con certeza.
- **Criterios de aceptación (Gherkin):**

```gherkin
Dado que el código no concluye
Cuando se escala al agente de IA
Entonces el agente recibe evidencia, reglas que fallaron y candidatos_restantes
Y NO puede elegir candidatos ya descartados por el código

Dado que el agente decide
Cuando se consolida el resultado
Entonces se marca certeza=baja y origen=agente_ia
Y el caso se encola a HITL con prioridad alta
```

### E-CONC-4 · HITL como autoridad final y muestreo de auditoría
- **Estado**: [ ] Pendiente · [ ] En desarrollo · [ ] En QA · [x] **Hecho** (T-505, 2026-09-11)
- **Responsable**: team analysis / team implementation
- **Como** contador revisor,
  **quiero** revisar todo caso de certeza baja y una muestra periódica de casos de
  certeza alta
  **para** corregir decisiones del agente y detectar reglas que matchean por
  accidente.
- **Criterios de aceptación (Gherkin):**

```gherkin
Dado un caso de certeza baja (resuelto por agente IA)
Cuando se consolida
Entonces llega a revisión humana (HITL) con prioridad alta

Dado un caso de certeza alta (resuelto por programa)
Cuando se consolida
Entonces entra en un muestreo de auditoría periódico con prioridad baja

Regla: feedback
  Dado que el HITL corrige una decisión
  Cuando se registra la corrección
  Entonces la corrección queda disponible como señal para ajustar reglas y prompts
```

### E-CONC-5 · Trazabilidad completa por caso
- **Estado**: [ ] Pendiente · [ ] En desarrollo · [ ] En QA · [x] **Hecho** (T-506, 2026-09-11)
- **Responsable**: team analysis / team implementation
- **Como** auditor externo o interno,
  **quiero** poder reconstruir qué pasó con cada caso (versión de prompt, modelo,
  evidencia por fuente, regla disparada y quién decidió)
  **para** justificar una clasificación fiscal si alguna vez se audita.
- **Criterios de aceptación (Gherkin):**

```gherkin
Dado un caso procesado
Cuando se consulta su trazabilidad
Entonces incluye versión de prompt, modelo usado, evidencia de cada fuente,
reglas disparadas y origen de la decisión (programa | agente_ia | hitl)

Regla: persistencia
  Dado cualquier caso consolidado
  Cuando se guarda el resultado
  Entonces la trazabilidad se persiste junto al resultado (JSON sidecar o store)
```

## 4. Bitácora de seguimiento

| Fecha | Acción / hito | Responsable | Estado |
|---|---|---|---|
| 2026-09-11 | **E-CONC-1 hecha (T-501)**: la **pasada 2** de reglas corre sobre la evidencia **combinada** de F4 (no por fuente, como la pasada 1): `rules/contexto_conclusion.py` proyecta la resolución por campo de T-404 (valor vigente + fuente responsable) y `rules/cruzadas.py` declara las cinco reglas cruzadas como `Rule` del motor de F0. **Negocio** (`CRUZ_1`: el comprobante se sostiene solo y el negocio no lo contradice → `aprobado`), **fast-fail** (`CRUZ_3`: la letra contradice los campos —A sin los dos CUIT, B con IVA discriminado— → `rechazado` con certeza **alta**, porque el código sí resolvió; `CRUZ_2`/`CRUZ_4`: falta de sostén → `revision`) y **conflicto** (`CRUZ_5`: R7 y el cruce negocio-vs-documento → `revision`, **no** rechazo: es sospecha, no contradicción). `conclusion/engine.py` deja de ser esqueleto: `concluir()` adjunta el `Decision` de F0 (que T-404 dejó en `None` a propósito) y `concluir_caso()` devuelve el `ConclusionResult` del diseño §4.5. La coherencia de la letra se evalúa **reutilizando** `COHERENCIA_POR_CAMPO` de T-403 sobre el valor vigente del caso (una sola definición de la tabla, dos usos: la fuente en la pasada 1, el caso en la pasada 2). El contexto fiscal entra como parámetro **opcional** (no son campos del contrato de extracción). Suites: `tests/test_conclusion_cruzadas_t501.py` (94) y `scripts/F5/t501.py` (8/8 escenarios + 8/8 fronteras). **Hallazgos**: el contrato de F0 rechazó un `Decision` de `programa` con certeza baja (de ahí el `ConclusionResult`); dos bugs reales detectados ejecutando (el índice de la tabla de coherencia —está por campo disparador, no por letra— y el formato contable `"1.234,56"`); y exigir contexto fiscal para concluir mandaba al agente casos que el código sí resuelve. | team implementation | Hecho |
| 2026-09-11 | **E-CONC-2 hecha (T-502)**: la búsqueda de evidencia adicional es **puntual por gap concreto** y **acotada**. `rules/gaps.py` separa **qué falta** (determinístico, sin red) de **cómo se busca** (hook inyectable): `detectar_gaps()` nombra cada falta con su criticidad y su objetivo, `CATALOGO_GAPS` declara qué es buscable (el padrón **constata** comprobante/CUIT/importe/fecha, pero **no inventa** la descripción, que solo está en el documento) y `PresupuestoBusqueda` administra los **dos topes** de ADR-003 (consultas totales del caso contra el loop abierto; reintentos por gap contra un proveedor intermitente). El bucle es acotado por construcción: lo no buscable no consume presupuesto, el proveedor **caído** reintenta (es transitorio), un **"consulté y no está"** no se insiste (es una respuesta del mundo) y el presupuesto agotado **corta** la búsqueda. El hook es **opcional**: `BuscadorEvidencia` es un protocolo inyectable y `ArcaClient` sin URL reporta el hook desactivado sin romper nada (ADR-003: no bloquea el MVP). `models/arca.py` implementa el adaptador WSCDC (payload del `CmpReq` armado con lo que el caso ya sabe, reintentos solo de lo transitorio, traducción de la respuesta) y `conclusion/engine.py::concluir_con_busqueda()` cierra el círculo del pseudocódigo: concluir → detectar → buscar → fusionar → **re-aplicar las cruzadas de T-501**. La fusión **reconstruye las `SourceEvidence` y vuelve a combinar** para que el padrón entre por la precedencia de ADR-002 en lugar de pisar el valor por código. Suites: `tests/test_conclusion_gaps_t502.py` (73) y `scripts/F5/t502.py` (6/6 escenarios + 8/8 fronteras). **Hallazgos**: la búsqueda "con objetivo concreto" solo es verificable si cada falta se nombra; reintentar un proveedor caído y reintentar una respuesta negativa del padrón son cosas distintas; la primera fusión pisaba valores a mano (la destapó un test). **Alcance honesto**: el flujo **WSAA** (certificados) queda para la integración real y el mapeo letra↔código AFIP de los tiques (**D-13**) no se inventa. | team implementation | Hecho |
| 2026-09-11 | **E-CONC-1 completada (T-503, consolidación)**: el Gherkin "concluye por programa" implementado en `conclusion/consolidacion.py`. La regla **`certeza=alta` + `origen=programa` ⇔ el código concluyó de forma consistente** se hace verificable con tres condiciones: `concluye` ∧ **sin alertas pendientes** ∧ **con letra**. El módulo arma el contrato congelado de F0 (`VoucherResult`, glosario §2.4) — estado, tipo, certeza, origen, campos planos, clasificación contable, HITL y traza — y expone `es_certeza_alta_por_programa()` (la regla con su motivo legible), `consolidar()` y `Consolidacion`. En el motor: `consolidar_caso()` y `concluir_con_busqueda(consolidar_resultado=True)`. **Hallazgos**: (1) un **rechazo firme es certeza alta** — "esto no es válido" es una conclusión, no una duda; la certeza mide cuánto sabe el sistema, no si le gustó el resultado; (2) **una alerta de R7 sin resolver impide la certeza alta** (el Gherkin pide conclusiones "consistentes"); (3) un **caso ambiguo no puede llevar `origen`** — no lo decidió nadie; (4) la traza perdía el bloque `conclusion` de T-501 y se corrigió (lo destapó un test de integración). Suites: `tests/test_conclusion_consolidacion_t503.py` (46) y `scripts/F5/t503.py` (6/6 + 8/8). Alcance honesto: la **clasificación contable no se inventa** — sin los pasos de F3/T-304 el resultado viaja sin ella y la traza lo declara. | team implementation | Hecho |
| 2026-09-11 | **E-CONC-3 hecha (T-504)**: los candidatos que el código no pudo concluir escalan al **agente IA**, con blindaje post-agente. `conclusion/agent.py` + `conclusion/prompt_agente.py` (prompt versionado `conclusion-agente@1`): el agente es una llamada a Ollama con prompt estructurado (ADR-008 alternativa (a), sin framework). Se escala **solo si el código no concluyó y hay universo** (un caso resuelto no gasta una llamada al modelo; uno sin candidatos no se escala porque no habría contra qué validar). El **blindaje** tiene tres capas: el prompt declara el universo cerrado y **no** incluye los descartados; el orquestador valida la elección contra `candidatos_restantes` y **rechaza** la que sale del universo; el contrato rechaza la intersección descartados/restantes. Desenlaces: `eligio`, `eleccion_invalida` (resucitar un descartado **no se corrige**: se rechaza y se audita), `se_abstuvo` (`candidato: null`, salida válida), `fallo` y `no_escalado`. `concluir_con_agente()` cierra el pipeline (T-501 → T-504 → T-503). **Hallazgos**: (1) **bug real de T-503** — la consolidación afirmaba «certeza alta por programa» para una decisión del agente porque no miraba la etapa que decidió; (2) **los candidatos llegaban vacíos**: F3 los derivaba bien pero `ContextoConclusion` los descartaba al construirse; (3) una elección inválida no se corrige; (4) abstenerse es una salida, no un fallo. Suites: `tests/test_conclusion_agente_t504.py` (62) y `scripts/F5/t504.py` (6/6 + 9/9, `% agente` 5/5). Alcance honesto: el origen es `agente_ia` **solo** si la elección sobrevivió al blindaje (si se abstuvo o falló queda en `None`: no lo decidió nadie). | team implementation | Hecho |
| 2026-09-11 | **E-CONC-4 hecha (T-505)**: el HITL pasa a ser **autoridad final** del pipeline, no una bandeja de salida. `conclusion/hitl.py`: `decidir_encolado()` decide de forma **pura** (certeza baja → revisión obligatoria/prioridad alta; certeza alta por programa → solo si la muestra de auditoría lo elige, prioridad baja) y `encolar_hitl()` lo materializa en el `VoucherResult`. El muestreo es **reproducible** por diseño (`sha256(semilla:documento_id)`, tasa 10% configurable en `HitlSettings`): el ADR-004 pide un muestreo «aleatorio estratificado configurable», pero un `random` sin semilla haría **inauditable** la decisión de auditar — no se podría responder «¿por qué éste y no aquél?». La cola (`ColaHitl`, en memoria; el store durable es T-506/ADR-009) expone `pendientes()` **priorizada** (R-09), `obligatorios()`, `muestreados()` y `corregidos()`, y no duplica casos ni pierde el trabajo humano al re-encolar. El **feedback** (Gherkin de la historia) queda **estructurado** (campo/antes/después/quién/cuándo) y **separado por motivo**: las correcciones de certeza baja señalan un error del **agente** (R-09) y las del muestreo una **regla** que acierta por accidente (R-03) — mezclarlas perdería la señal de cada una. `confirmar()` registra «la regla acertó» sin corrección, que es justo lo que la auditoría necesita. Suites: `tests/test_conclusion_hitl_t505.py` (58) y `scripts/F5/t505.py` (6/6 + 13/13). **Alcance honesto**: la cola es en memoria (la persistencia del `CaseRecord` es T-506) y el feedback se registra, no se aplica automáticamente al resultado. | team implementation | Hecho |
| 2026-09-11 | **E-CONC-5 hecha (T-506)**: todo caso persiste su trazabilidad auditable. `trace/construccion.py` **arma** el `CaseRecord` (proyección de la corrida: evidencia + decisión + resultado, sin ejecutar reglas ni modelos) y `trace/recorder.py` lo **persiste**. El sidecar `<documento>.case.json` lleva el registro completo con **escritura atómica** (temporal en el mismo directorio + `os.replace` + `fsync`): si el proceso muere a mitad, el sidecar anterior queda intacto. El índice `index.jsonl` agrega una línea por caso con lo mínimo para encontrar y filtrar, y es un **derivado**: *append* barato y seguro en concurrencia, deduplicación **al leer** (una fila por documento: el índice por corrida inflaría las métricas de T-507) y `reindexar()` que lo reconstruye desde los sidecars. El Gherkin se verifica de punta a punta: el registro responde versión de prompt, modelo, evidencia por fuente, reglas disparadas y quién decidió **leyendo un archivo**, sin volver a correr el pipeline. Nada se inventa: sin agente no hay modelo de agente, y el registro declara si la evidencia por fuente es `directa` o `reconstruida_desde_campos` (el nivel de fuente de la pasada 1 no viaja en la evidencia combinada). Suites: `tests/test_trace_recorder_t506.py` (74) y `scripts/F5/t506.py` (4/4 + 14/14). **Alcance honesto**: el store SQLite del ADR-009, para consultas por más dimensiones, sigue siendo la fase posterior. | team implementation | Hecho |
| 2026-09-11 | **E-CONC cerrada — F5 completa (T-501..T-507)**: la épica cierra su DoD. El pipeline **concluye** con la certeza derivada de la etapa que decidió (reglas cruzadas de la pasada 2), busca evidencia **acotada** si falta un dato (hook ARCA opcional), **consolida** el `VoucherResult`, escala al **agente IA** con blindaje cuando el código no pudo, **encola a revisión humana** lo que no quedó de certeza alta más un **muestreo reproducible** de los que sí (con las correcciones registradas como feedback separado por motivo), **persiste la trazabilidad** de cada caso (sidecar con escritura atómica + índice consultable) y **reporta las métricas** del lote. Suite de la fase: 454 tests nuevos (T-501..T-507) sobre los 949 de F4; los 12 scripts `scripts/F<n>/t*.py` salen con código 0. | team implementation | Hecho |

## 5. Referencias cruzadas

- Historias fuente: [`02-epicas-historias-usuario.md`](../02-epicas-historias-usuario.md)
- Objetivo y alcance: [`01-vision-alcance.md`](../01-vision-alcance.md) (OBJ-5)
- Fases/tareas/DoR-DoD: [`05-plan-ejecucion.md`](../05-plan-ejecucion.md) (F5: T-501..T-507; depende de ADR-005)
- Calidad/golden set: [`06-estrategia-calidad.md`](../06-estrategia-calidad.md)
- Dependencias: E-EXT (evidencia combinada), E-CLAS (reglas R1-R7/letra), E-LIB (motor de reglas y trazabilidad), E-CLI (cola HITL/batch)

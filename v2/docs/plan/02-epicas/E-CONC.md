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
| **Estado épica** | 🟡 En implementación (T-501 y T-502 hechas: E-CONC-1 y E-CONC-2; E-CONC-3/4/5 pendientes) |
| **DoR cumplido** | [x] sí |
| **Fecha inicio** | 2026-09-11 |
| **Fecha fin** |  |

## 2. Definition of Done de la épica (criterios de aceptación a nivel épica)

- [x] Se aplican reglas determinísticas de negocio tributario, fast-fail por letra y conflicto sobre la evidencia combinada (pasada 2), con resultado: concluye, certeza, origen, candidatos y reglas aplicadas. *(**T-501 hecho**: `rules/cruzadas.py` declara las cinco `Rule` de `tipo="cruzada"` y `conclusion/engine.py::concluir()` corre la pasada 2 sobre el valor vigente de cada campo (la resolución de T-404). `ConclusionResult` (diseño §4.5) lleva `concluye`/`certeza`/`origen`/`estado`/candidatos/reglas/alertas y el `Decision` de F0 se adjunta cuando el código concluyó. Las tres familias: **negocio** (`CRUZ_1`), **fast-fail** (`CRUZ_3` contradicción de la letra → `rechazado`; `CRUZ_2`/`CRUZ_4` falta de sostén → `revision`) y **conflicto** (`CRUZ_5` R7 y el cruce negocio-vs-documento → `revision`, no rechazo). Suites: `tests/test_conclusion_cruzadas_t501.py` (94) y `scripts/F5/t501.py` (8/8 + 8/8))*
- [x] Cuando el código concluye de forma consistente se marca certeza=alta y origen=programa (sin pasar por el agente IA). *(**T-501 hecho**: `certeza` y `origen` se derivan de la **etapa que decidió** (glosario §2); cuando concluye el código, `origen=programa` y `certeza=alta`. El validador del contrato de F0 **hace cumplir** esa regla: rechaza un `Decision` de `programa` con certeza baja, y por eso un caso ambiguo viaja sin `Decision` (lo resolverá T-504/T-505))*
- [x] La búsqueda de evidencia adicional es puntual por gap concreto, con límite de reintentos y sin loop abierto (hook ARCA opcional, ADR-003). *(**T-502 hecho**: `rules/gaps.py` nombra cada falta con su **objetivo concreto** y su criticidad, declara qué es buscable (`CATALOGO_GAPS`: el padrón constata, no inventa lo que el documento no dice) y administra los **dos topes** del presupuesto (consultas totales del caso y reintentos por gap). El bucle es **acotado por construcción**: lo no buscable no consume presupuesto, el proveedor caído reintenta, un "consulté y no está" **no** se insiste, y el presupuesto agotado **corta** la búsqueda. El hook es **opcional** (`BuscadorEvidencia` inyectable; `ArcaClient` sin URL reporta desactivado) y su reaplicación de las cruzadas vive en `conclusion/engine.py::concluir_con_busqueda()`)*
- [ ] El escalado a agente IA solo ocurre cuando el código no concluye y solo entre candidatos_restantes (no puede resucitar descartados); el resultado agente se marca certeza=baja y origen=agente_ia y se encola a HITL con prioridad alta.
- [ ] HITL como autoridad final: revisión obligatoria de casos de certeza baja + muestreo periódico de auditoría de casos de certeza alta (tasa configurable), con registro de correcciones como feedback.
- [ ] Trazabilidad completa por caso (`CaseRecord` persistida junto al resultado — JSON sidecar/store) con versión de prompt, modelo, evidencia por fuente, reglas disparadas y origen de la decisión.
- [ ] Métricas del pipeline reportadas: % certeza alta, % agente, % rechazado, acuerdo VLM/LLM (T-507 / E-LIB-5).
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
- **Estado**: [ ] Pendiente · [ ] En desarrollo · [ ] En QA · [ ] Hecho
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
- **Estado**: [ ] Pendiente · [ ] En desarrollo · [ ] En QA · [ ] Hecho
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
- **Estado**: [ ] Pendiente · [ ] En desarrollo · [ ] En QA · [ ] Hecho
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

## 5. Referencias cruzadas

- Historias fuente: [`02-epicas-historias-usuario.md`](../02-epicas-historias-usuario.md)
- Objetivo y alcance: [`01-vision-alcance.md`](../01-vision-alcance.md) (OBJ-5)
- Fases/tareas/DoR-DoD: [`05-plan-ejecucion.md`](../05-plan-ejecucion.md) (F5: T-501..T-507; depende de ADR-005)
- Calidad/golden set: [`06-estrategia-calidad.md`](../06-estrategia-calidad.md)
- Dependencias: E-EXT (evidencia combinada), E-CLAS (reglas R1-R7/letra), E-LIB (motor de reglas y trazabilidad), E-CLI (cola HITL/batch)

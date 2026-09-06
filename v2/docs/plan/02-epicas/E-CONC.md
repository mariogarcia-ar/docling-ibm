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
| **Estado épica** | 🔴 Backlog |
| **DoR cumplido** | [ ] pendiente |
| **Fecha inicio** |  |
| **Fecha fin** |  |

## 2. Definition of Done de la épica (criterios de aceptación a nivel épica)

- [ ] Se aplican reglas determinísticas de negocio tributario, fast-fail por letra y conflicto sobre la evidencia combinada (pasada 2), con resultado: concluye, certeza, origen, candidatos y reglas aplicadas.
- [ ] Cuando el código concluye de forma consistente se marca certeza=alta y origen=programa (sin pasar por el agente IA).
- [ ] La búsqueda de evidencia adicional es puntual por gap concreto, con límite de reintentos y sin loop abierto (hook ARCA opcional, ADR-003).
- [ ] El escalado a agente IA solo ocurre cuando el código no concluye y solo entre candidatos_restantes (no puede resucitar descartados); el resultado agente se marca certeza=baja y origen=agente_ia y se encola a HITL con prioridad alta.
- [ ] HITL como autoridad final: revisión obligatoria de casos de certeza baja + muestreo periódico de auditoría de casos de certeza alta (tasa configurable), con registro de correcciones como feedback.
- [ ] Trazabilidad completa por caso (`CaseRecord` persistida junto al resultado — JSON sidecar/store) con versión de prompt, modelo, evidencia por fuente, reglas disparadas y origen de la decisión.
- [ ] Métricas del pipeline reportadas: % certeza alta, % agente, % rechazado, acuerdo VLM/LLM (T-507 / E-LIB-5).
- [ ] Paridad verificable con v1 sobre el golden set donde aplique (DoD de F5 en `05-plan-ejecucion.md`).
- [ ] Documentación/contratos actualizados (README/ADR si cambia una decisión).

## 3. Historias de usuario y seguimiento

### E-CONC-1 · Reglas cruzadas sobre evidencia combinada (pasada 2)
- **Estado**: [ ] Pendiente · [ ] En desarrollo · [ ] En QA · [ ] Hecho
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
- **Estado**: [ ] Pendiente · [ ] En desarrollo · [ ] En QA · [ ] Hecho
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
|  | | | |

## 5. Referencias cruzadas

- Historias fuente: [`02-epicas-historias-usuario.md`](../02-epicas-historias-usuario.md)
- Objetivo y alcance: [`01-vision-alcance.md`](../01-vision-alcance.md) (OBJ-5)
- Fases/tareas/DoR-DoD: [`05-plan-ejecucion.md`](../05-plan-ejecucion.md) (F5: T-501..T-507; depende de ADR-005)
- Calidad/golden set: [`06-estrategia-calidad.md`](../06-estrategia-calidad.md)
- Dependencias: E-EXT (evidencia combinada), E-CLAS (reglas R1-R7/letra), E-LIB (motor de reglas y trazabilidad), E-CLI (cola HITL/batch)

# F5-subplan — Subplan de implementación F5 (Conclusión + HITL)

> Documento de trabajo para la implementación de la **Fase F5** por
> `team implementation`. Complementa el seguimiento de la fase
> ([`F5.md`](F5.md)) y el diseño del módulo
> ([`../03-arquitectura/CONC.md`](../03-arquitectura/CONC.md)).
> **Fecha**: 2026-09-11 · **Rama**: `v2` · **Estado**: 🟡 En implementación
> (T-501 hecha; T-502..T-507 pendientes).

## 1. Ficha del subplan

| Campo | Valor |
|---|---|
| **Fase** | F5 — Conclusión (refactor de la conclusión de v1) y HITL |
| **Tareas que cubre** | T-501, T-502, T-503, T-504, T-505, T-506, T-507 (ver [`F5.md`](F5.md)) |
| **Épicas asociadas** | E-CONC (E-CONC-1 cruzadas, E-CONC-2 gaps, E-CONC-3 agente, E-CONC-4 HITL, E-CONC-5 trazabilidad) y E-LIB-5 (métricas) |
| **Módulos** | `voucherflow/conclusion/` (`engine.py`, `agent.py`, `hitl.py`) + `voucherflow/rules/` (`cruzadas.py`, `gaps.py`, `contexto_conclusion.py`) + `voucherflow/trace/` (`recorder.py`) |
| **Responsable** | team implementation |
| **Decisiones de alcance** | Cerradas con el equipo (ver §2) |
| **DoD de referencia** | "El pipeline concluye con certeza/origen correctos; todo caso tiene `CaseRecord`; el muestreo de alta y la cola de baja funcionan y registran feedback" (DoD de F5 en `05-plan-ejecucion.md`) |
| **Depende de** | F4 (T-404: `CombinedEvidence` con resolución por campo) y F3 (motor R1-R7, cadena contable) |
| **Habilita a** | F6 (el cliente CLI/batch expone `run`/`conclusion`/`hitl`) |

## 2. Decisiones de alcance cerradas (2026-09-10)

1. **La conclusión es la etapa que decide — y por eso es la única que llena
   `Decision`**. F4/T-404 dejó `CombinedEvidence.decision` en `None` a propósito:
   combinar no es decidir (ADR-001, glosario §2). F5/T-501 es la etapa que
   **sí** decide, así que es la que produce el `Decision` completo
   (`concluye`/`certeza`/`origen`/candidatos/reglas/alertas) y lo adjunta a la
   evidencia combinada. No se crea un contrato nuevo: se llena el que F0 congeló.
2. **La certeza se deriva de la etapa, nunca se autodeclara** (glosario §2,
   regla dura de F4/T-404): programa → `alta`; agente IA → `baja`; HITL →
   `hitl`. Una regla cruzada **no elige** su certeza: la elige el hecho de
   haber concluido en código.
3. **Las reglas cruzadas se declaran como `Rule`s del motor de F0**
   (`tipo="cruzada"`), igual que hicieron F3/T-301 (R1-R7) y F4/T-403 (raw).
   Reutilizan `Registry` sin reescribirlo (contrato congelado de F0) y su
   contexto es un dataclass `frozen=True` nuevo, no el de F3.
4. **La pasada 2 es un gate de consistencia, no una segunda extracción**: la
   lógica de "esta letra es incompatible con estos campos" **ya existe** en
   `extraction/evidencia.py::COHERENCIA_POR_CAMPO` (F4/T-403, E-EXT-2). T-501 la
   **promueve a regla cruzada sobre el caso combinado** (no por fuente): hasta
   T-403 la incoherencia debilitaba una **fuente**; acá la incoherencia entre el
   **valor vigente** de la letra y los campos del caso decide el estado. Se
   escribe una sola vez la tabla y se comparte; no se duplica la semántica.
5. **El resultado nunca queda "sin estado"**: todo caso sale con un
   `EstadoResultado` explícito — `aprobado` (el código concluyó o el agente
   resolvió), `revision` (no se pudo concluir con confianza o hay conflicto) o
   `rechazado` (fast-fail: no es comprobante / inconsistencia dura). Un caso sin
   estado sería un agujero de auditoría.
6. **El fast-fail corta primero y no admite escalado**: si el caso es
   irremediable (documento no procesable, o una incompatibilidad **dura** de la
   letra) se resuelve como `rechazado` con certeza `alta` y origen `programa`
   — el código sí concluyó, y concluyó que no. Escalar al agente un caso que el
   código ya descartó con certeza va contra ADR-008.
7. **La ambigüedad es `revision`, no `rechazado`**: "no pude concluir" y "esto
   está mal" son cosas distintas. La primera escala (agente en T-504, HITL en
   T-505); la segunda cierra el caso. Confundirlas mandaría a revisión humana
   casos que el sistema ya resolvió, y haría pasar por aprobado un caso sin
   resolver.
8. **Los candidatos son un conjunto cerrado y degradan, nunca crecen** (ADR-008):
   `candidatos_descartados` sale del descarte del código (coherencia de la
   letra + R7). El agente de T-504 y el HITL solo pueden elegir entre
   `candidatos_restantes`; el post-agente (T-504) **valida** la elección contra
   esa lista y la rechaza si el agente "resucitó" un descartado.
9. **Determinismo sin modelo (regla dura del repo)**: la suite default no puede
   depender de Ollama. T-501 (y T-502/T-503) son **100% deterministas y sin
   red**; el agente de T-504 entra por inyección (mismo patrón que el protocolo
   `Lector` de F4) y la corrida real vive en `--origen`/`@pytest.mark.integration`.
10. **Una herramienta de inspección por tarea** (`scripts/F5/t501.py`…), con el
    patrón de F3/F4: escenarios con ✅/❌, verificación de **fronteras** y salida
    con código ≠ 0 si algo falla. Es la forma de ver la conclusión **sin GPU**.

## 3. Alcance por tarea (T-501..T-507)

### 3.1 T-501 · Reglas cruzadas sobre evidencia combinada (negocio + fast-fail + conflicto R7) ✅ Hecha

> **Estado 2026-09-11**: **Hecha** por `team implementation`. Suite completa en
> verde (**1043 passed, 10 skipped**, 94 nuevos); `python scripts/F5/t501.py`
> reporta **8/8** escenarios de conclusión + **8/8** fronteras (exit 0). `F5.md`
> pasa de 🔴 Backlog a 🟡 En implementación.

**Qué se hace.** Una **pasada 2** de reglas que corre sobre la evidencia
**combinada** (no por fuente, como la pasada 1 de T-403) y produce el veredicto
del caso: negocio tributario, fast-fail por letra y conflicto R7.

**Apartado (1) — negocio tributario.** Se re-aplica el motor R1-R7 de F3
**sobre los valores vigentes** de la evidencia combinada. La diferencia con F3:
allá el contexto se armaba con la lectura de **una** fuente; acá se arma con la
**resolución por campo** de ADR-002 (`CampoCombinado.valor`/`fuente`), es decir
con el valor que ganó la precedencia y su fuente responsable. Se reutiliza
`clasificar_tipo_comprobante()` tal cual (no se reimplementa R1-R7) y se conserva
su `detalle` para trazabilidad (E-CONC-5).

**Apartado (2) — fast-fail.** Los cortes que cierran el caso sin escalar:

| Disparador | Estado | Certeza | Origen | Por qué |
|---|---|---|---|---|
| El documento no superó el gate de procesabilidad (F1/F2) | `rechazado` | `alta` | `programa` | no es un comprobante: no hay nada que concluir |
| La letra del comprobante es **incompatible de forma dura** con el desglose (p. ej. `B` con IVA discriminado > 0) | `rechazado` | `alta` | `programa` | el dato contradice la letra; no es ambigüedad, es invalidez |

**Apartado (3) — conflicto R7.** El conflicto financiero (letra `B`/`C` con
condición fiscal que habilitaría crédito fiscal) **no rechaza**: dispara la
alerta ya definida en F3/T-301 (`construir_alerta`, `MENSAJE_R7`) y **degrada a
`revision` con certeza `baja`**. Es el caso del recuadro del enunciado
("Responsable Inscripto emisor + receptor Responsable Inscripto + documento
detectado como B → alerta"): el sistema **no** afirma que esté mal, pide un ojo
humano. Diferencia deliberada con el fast-fail: R7 es sospecha, no contradicción.

**Salida.** El veredicto en dos formas, porque el contrato de F0 es más estrecho
que el diseño:

- `ConclusionResult` (el dataclass del diseño §4.5) — la vista **completa**:
  `concluye`, `certeza`, `origen`, `estado`, candidatos, reglas aplicadas,
  alertas, `hitl`, gaps y conflictos. Existe siempre, incluido el caso que **no**
  concluyó.
- `Decision` (el schema congelado de F0) — el veredicto **final**, adjunto a la
  evidencia combinada. **Solo** se emite cuando el código concluyó: su validador
  exige que `origen=programa` implique `certeza=alta` (glosario §2), así que un
  caso ambiguo no puede llevarlo — no lo decidió nadie todavía, y lo resolverán el
  agente (T-504) o el HITL (T-505).

**Archivos.**

- `src/voucherflow/rules/contexto_conclusion.py` (nuevo) — `ContextoConclusion`
  (`frozen=True`), construido desde la `CombinedEvidence`: valores vigentes por
  campo, letra vigente y su fuente, coherencia del caso, gaps, campos ausentes y
  candidatos (con la curaduría de ADR-008).
- `src/voucherflow/rules/cruzadas.py` (nuevo) — el registro
  `REGISTRO_CRUZADAS` con las `Rule` de `tipo="cruzada"`, la evaluación
  (`evaluar_cruzadas`) y el armado del veredicto (`construir_conclusion`,
  `construir_decision`).
- `src/voucherflow/conclusion/engine.py` — `concluir()` deja de ser esqueleto
  (adjunta el `Decision` a la evidencia) y aparece `concluir_caso()` (devuelve el
  `ConclusionResult`).
- `src/voucherflow/conclusion/__init__.py` y `rules/__init__.py` — exportes.
- `tests/test_conclusion_cruzadas_t501.py` (nuevo, 94 tests).
- `scripts/F5/t501.py` (nuevo).

**Cómo se prueba (sin red).**

- Las reglas cruzadas se evalúan contra contextos construidos a mano
  (deterministas) y contra una `CombinedEvidence` real fabricada con el pipeline
  de F4 sobre lecturas sintéticas.
- Fronteras: sin evidencia combinada / sin decisión / caso ya decidido; letra
  vigente ausente (no se inventa veredicto); candidato descartado y restante a
  la vez (blindaje ADR-008); alerta R7 sin letra final.
- El script `scripts/F5/t501.py` corre N escenarios ✅/❌, imprime la tabla de
  reglas y verifica las fronteras, con salida ≠ 0 si falla.

**Fronteras de la tarea (lo que **no** hace).**

- **No** busca evidencia adicional para los gaps que detecta (eso es T-502).
- **No** decide la consolidación "certeza alta por programa" como contrato
  propio (T-503 lo formaliza); acá se deja el veredicto con la certeza derivada de
  la etapa.
- **No** llama al agente IA (T-504) ni encola HITL (T-505).
- **No** persiste nada (T-506).
- **No** muta la evidencia de entrada: `concluir` devuelve una copia con la
  decisión adjunta.

**Hallazgos de la implementación.**

1. **El contrato de F0 hace cumplir la regla de oro.** `CombinedEvidence` valida
   que `origen=programa` implique `certeza=alta`; un primer diseño emitía un
   `Decision` de `programa` con certeza `baja` para los casos ambiguos y el
   validador lo rechazó. De ahí el `ConclusionResult` (§4.5) y que `Decision` se
   adjunte solo si el código concluyó.
2. **Dos bugs reales, detectados ejecutando**: la tabla `COHERENCIA_POR_CAMPO`
   está indexada por **campo disparador** (`tipo_comprobante`) y no por letra (el
   lookup por letra nunca disparaba); y el intérprete de importes no entendía el
   formato contable (`"1.234,56"`).
3. **La coherencia del documento alcanza para concluir.** Exigir el contexto
   fiscal dejaba sin resolver a todo comprobante que se sostiene solo — el caso
   que el código sí puede decidir — y lo mandaba al agente. El contexto fiscal
   **refina** (R7 y cruce negocio-vs-documento); no es un requisito.
4. **La rama "ninguna regla disparó" es inalcanzable por construcción**: el
   complemento de `CRUZ_1` es exactamente la unión de los disparadores de
   `CRUZ_2`/`CRUZ_3`/`CRUZ_4`/`CRUZ_5`. Se conserva como red de seguridad
   (el peor caso tiene que seguir siendo `revision`, nunca aprobar por descarte).

### 3.2 T-502 · Detección de gaps + búsqueda de evidencia adicional con límite (hook ARCA)

> **Estado**: pendiente. Alcance según [`F5.md`](F5.md) y ADR-003.

- `rules/gaps.py`: detección de los campos que faltaron para concluir
  (`campos_desconocidos`, la superficie que F3 ya expone y T-501 propaga).
- Búsqueda **puntual por gap** con `max_reintentos=N` configurable — no es un
  loop abierto (regla dura de E-CONC-2). Al agotar el límite sin resolver, el
  caso sigue al escalado (T-504), no vuelve a intentar.
- `ArcaClient` **opcional** (ADR-003: el hook no bloquea el MVP; la variante
  online es Should / R-10). Se inyecta por protocolo, como el `Lector` de F4.
- Re-aplicación de las cruzadas de T-501 tras cubrir el gap (es lo que el
  pseudocódigo de `algoritmo.md` llama `aplicar_reglas_cruzadas` de nuevo).

### 3.3 T-503 · Consolidación "certeza alta por programa"

> **Estado**: pendiente.

- Formaliza el contrato de la consolidación: `certeza=alta` + `origen=programa`
  **si y solo si** el código concluyó sin ambigüedad, con `concluye=True` y sin
  alertas de R7 pendientes.
- Es la parte que el `Decision` de T-501 **habilita** pero no cierra: T-501
  resuelve el veredicto, T-503 lo consolida en el `VoucherResult` (estado,
  certeza, origen, campos, clasificación contable de F3).
- **No** pasa por el agente IA: ese es el punto de la historia (E-CONC-1,
  "NO pasó por el agente de IA para decidir").

### 3.4 T-504 · Escalado a agente IA + blindaje post-agente

> **Estado**: pendiente. ADR-008.

- `conclusion/agent.py`: el agente recibe evidencia + reglas que fallaron +
  `candidatos_restantes`, y **solo** puede elegir entre esos.
- **Blindaje post-agente**: se valida la elección contra `candidatos_restantes`;
  si el agente devuelve un candidato descartado, se rechaza y se registra la
  anomalía (el agente no puede resucitar un descartado).
- Resultado: `certeza=baja` + `origen=agente_ia` → HITL prioridad alta.
- El agente se inyecta por protocolo (suite default sin Ollama).

### 3.5 T-505 · Cola HITL + registro de correcciones

> **Estado**: pendiente. ADR-004 y ADR-009.

- `conclusion/hitl.py`: revisión obligatoria de certeza baja (prioridad `alta`)
  + muestreo de auditoría de certeza alta (prioridad `baja`, tasa configurable
  sugerida 5-10%).
- Registro de correcciones como `feedback` (alimenta reglas y prompts).
- El muestreo debe ser **determinista y auditable** (semilla/configuración), no
  aleatorio silencioso: un caso muestreado hoy debe poder explicarse mañana.

### 3.6 T-506 · Trazabilidad completa `CaseRecord` persistida

> **Estado**: pendiente. ADR-005.

- `trace/recorder.py`: `CaseRecorder.registrar()` deja de ser esqueleto.
- Persistencia **JSON sidecar** + índice (ADR-005/ADR-009: el store SQLite para
  consultas agregadas es posterior).
- El `CaseRecord` (schema congelado de F0) ya tiene su shape:
  `version_prompt`/`modelo_por_etapa`/`evidencia_por_fuente`/`reglas_disparadas`/
  `quien_decidio`/`etapas`/`resultado`.
- Escritura **atómica** (patrón de v1 que v2 debe respetar, §4 de los ADR).

### 3.7 T-507 · Métricas

> **Estado**: pendiente. E-LIB-5.

- % certeza alta, % agente, % rechazado, acuerdo VLM/LLM (ver `06`).
- Reporte sobre el `CaseRecord` persistido; herramienta de inspección con el
  patrón de `scripts/F3/t305.py` y `scripts/F4/t405.py`.

## 4. Reglas duras (no romper F0/F3/F4)

- **No cambiar el contrato de evidencia ni el de resultados** de F0
  (`SCHEMA_VERSION = 1.0.0`). `Decision`, `VoucherResult` y `CaseRecord` ya
  existen: F5 los **llena**, no los rediseña. Si hiciera falta un cambio
  incompatible, bump mayor + revisión de F3/F4 (criterio en el propio módulo).
- **La certeza se deriva de la etapa que decidió** (glosario §2). Ninguna regla
  escribe `certeza=alta` "porque sí": la alta es consecuencia de haber concluido
  en código.
- **El agente no puede elegir candidatos descartados** (ADR-008): el blindaje es
  parte del entregable de T-504, no un extra.
- **Reglas de negocio en código, no en prompt** (ADR-006): la pasada 2 es código
  determinístico; los prompts solo reportan lectura.
- **La suite default corre sin Ollama ni Docling reales**: el agente y el
  `ArcaClient` entran por inyección (protocolo), igual que el `Lector` de F4.
- **No se agregan dependencias nuevas** (el muestreo usa `random` de la stdlib
  con semilla; la persistencia usa `json` + escritura atómica).
- **No loop abierto** (E-CONC-2): la búsqueda de evidencia adicional tiene
  `max_reintentos=N` y al agotarlo el caso **avanza**, no reintenta.
- **No tocar `processing`/`validation`/`classification`/`extraction`**: F5
  **consume** sus artefactos (`CombinedEvidence`, `TipoComprobanteResult`,
  `ClasificacionContableResult`). La única excepción deliberada es **compartir**
  la tabla de coherencia de T-403 (§2.4), sin moverla de donde vive.
- Estilo: docstrings y mensajes en español citando los docs (doc 03 §4.5, IDs de
  épica/tarea); asserts con mensaje explicativo.

## 5. Flujo de la conclusión (referencia de implementación)

```text
funcion concluir(evidencia: CombinedEvidence, *, contexto_extra=None):
    # T-501: pasada 2 sobre la evidencia COMBINADA (no por fuente)
    ctx = ContextoConclusion.desde_evidencia(evidencia)
    cruzadas = evaluar_cruzadas(ctx)              # REGISTRO_CRUZADAS

    si cruzadas.fast_fail:                        # T-501
        decision = decidir(concluye=True, estado="rechazado",
                           certeza="alta", origen="programa")   # T-503 consolida
        retornar decision

    si cruzadas.alertas:                          # R7: sospecha, no contradicción
        decision = decidir(concluye=False, estado="revision",
                           certeza="baja", origen="programa")

    si cruzadas.faltan_datos:                     # T-502
        evidencia = buscar_evidencia_adicional(ctx.gaps, max_reintentos=N)   # hook ARCA opcional
        cruzadas = evaluar_cruzadas(ContextoConclusion.desde_evidencia(evidencia))

    si cruzadas.concluye:                         # T-503
        consolidar(certeza="alta", origen="programa")     # NO pasó por el agente
        retornar

    # T-504: solo entre candidatos no descartados (ADR-008)
    decision_agente = agente_ia_decide(
        evidencia=evidencia,
        candidatos_restantes=cruzadas.candidatos_restantes,   # nunca los descartados
        reglas_que_fallaron=cruzadas.conflictos,
    )
    blindar_post_agente(decision_agente, cruzadas.candidatos_restantes)
    consolidar(certeza="baja", origen="agente_ia")

    # T-505: HITL es autoridad final
    encolar_hitl(prioridad="alta")                           # certeza baja
    si es_muestra_auditoria(tasa=config):                    # certeza alta
        encolar_hitl(prioridad="baja")

    # T-506: trazabilidad
    CaseRecorder.registrar(case_record)
```

- El `detalle` de la corrida publica las reglas cruzadas disparadas por familia,
  los campos que faltaron, los candidatos, las alertas y la razón del veredicto
  (para `CaseRecord`/auditoría, E-CONC-5).
- La **certeza** que sale de acá no la elige la regla: la elige la etapa
  (`programa` → `alta`; `agente_ia` → `baja`), y T-503 la consolida con esa
  regla de oro.

## 6. Archivos previstos

### T-501
- `src/voucherflow/rules/contexto_conclusion.py` (nuevo).
- `src/voucherflow/rules/cruzadas.py` (nuevo).
- `src/voucherflow/conclusion/engine.py` (implementado; deja de ser esqueleto).
- `src/voucherflow/conclusion/__init__.py` y `src/voucherflow/rules/__init__.py` (exportes).
- `tests/test_conclusion_cruzadas_t501.py` (nuevo, 94 tests).
- `scripts/F5/t501.py` (nuevo).

### T-502..T-507
- `src/voucherflow/rules/gaps.py` (T-502) + `models/arca.py` (hook, opcional).
- `src/voucherflow/conclusion/agent.py` (T-504) + `conclusion/hitl.py` (T-505).
- `src/voucherflow/trace/recorder.py` (T-506).
- `tests/test_conclusion_*_t5NN.py` y `scripts/F5/t5NN.py` por tarea.

## 7. Avance

- **Estado (2026-09-11)**: **T-501 hecha**; subplan creado y las decisiones de
  alcance de §2 cerradas. La **pasada 2** corre sobre la evidencia combinada de F4
  y produce el veredicto del caso (negocio + fast-fail + conflicto R7), con
  `ConclusionResult` (diseño §4.5) y el `Decision` de F0 adjunto solo cuando el
  código concluyó. Suite completa **1043 passed / 10 skipped** (94 nuevos);
  `scripts/F5/t501.py` → **8/8** escenarios + **8/8** fronteras. T-502..T-507
  quedan pendientes con su alcance definido en §3.2–§3.7. `F5.md` pasa de 🔴
  Backlog a 🟡 En implementación.
- **Punto de partida real**: la evidencia combinada de F4 (T-404) está
  disponible y con el valor vigente por campo resuelto (`CampoCombinado.valor`/
  `fuente`), y el motor R1-R7 de F3 (`evaluar_negocio`, `condicion_r7`) es
  reutilizable tal cual — así que T-501 se implementó sin tocar F3/F4.
  `CombinedEvidence.decision` ya era opcional (T-404) precisamente para que F5
  fuera quien la llene.

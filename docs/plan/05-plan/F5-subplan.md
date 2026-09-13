# F5-subplan — Subplan de implementación F5 (Conclusión + HITL)

> Documento de trabajo para la implementación de la **Fase F5** por
> `team implementation`. Complementa el seguimiento de la fase
> ([`F5.md`](F5.md)) y el diseño del módulo
> ([`../03-arquitectura/CONC.md`](../03-arquitectura/CONC.md)).
> **Fecha**: 2026-09-11 · **Rama**: `v2` · **Estado**: ✅ **DoD verificado**
> (T-501..T-507 hechas).

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

### 3.2 T-502 · Detección de gaps + búsqueda de evidencia adicional con límite (hook ARCA) ✅ Hecha

> **Estado 2026-09-11**: **Hecha** por `team implementation`. Suite completa en
> verde (**1116 passed, 10 skipped**, 73 nuevos); `python scripts/F5/t502.py`
> reporta **6/6** escenarios de búsqueda + **8/8** fronteras (exit 0).

**Qué se hace.** El paso del pseudocódigo de `algoritmo.md` —"si faltan datos,
buscar evidencia adicional con `max_reintentos=N` y re-aplicar las cruzadas"—
implementado de modo que **no sea un loop abierto**. Se separan dos cosas que
suelen ir juntas y no son lo mismo: **qué falta** (determinístico, sin red) y
**cómo se busca** (el hook inyectable).

| Pieza | Módulo | Qué hace |
|---|---|---|
| `detectar_gaps()` / `Gap` / `CATALOGO_GAPS` | `rules/gaps.py` | nombra cada falta con su **criticidad** y su **objetivo concreto**; declara qué es buscable y qué solo puede venir del documento |
| `PresupuestoBusqueda` | `rules/gaps.py` | los **dos topes**: consultas totales del caso y reintentos por gap |
| `buscar_evidencia_adicional()` | `rules/gaps.py` | el bucle acotado: cubre, reintenta lo transitorio, no insiste ante un "no está", y **corta** al agotar el presupuesto |
| `BuscadorEvidencia` (Protocol) | `rules/gaps.py` | el hook inyectable (mismo criterio que el `Lector` de F4) |
| `ArcaClient` | `models/arca.py` | el adaptador WSCDC real: arma el pedido, reintenta, traduce la respuesta; un fallo de red es *no disponible* |
| `concluir_con_busqueda()` | `conclusion/engine.py` | el círculo completo: concluir → detectar → buscar → fusionar → **re-aplicar las cruzadas** |

**Archivos.**

- `src/voucherflow/rules/gaps.py` (nuevo).
- `src/voucherflow/models/arca.py` (implementado; deja de ser esqueleto).
- `src/voucherflow/conclusion/engine.py` (`concluir_con_busqueda()`, `ConclusionConBusqueda`).
- `src/voucherflow/rules/__init__.py` y `src/voucherflow/conclusion/__init__.py` (exportes).
- `tests/test_conclusion_gaps_t502.py` (nuevo, 73 tests).
- `scripts/F5/t502.py` (nuevo).

**Cómo se prueba (sin red).**

- Detección: el caso completo no tiene gaps; falta un crítico → gap **bloqueante**;
  un campo fuera del catálogo se reporta igual (informativo y no buscable).
- Presupuesto: los dos topes, su consumo y el agotamiento.
- Búsqueda: cubrir, reintentar lo transitorio, no insistir ante un "no está",
  hook desactivado como caso normal, y **corte** al agotar el presupuesto.
- Re-conclusión: cubrir el gap desbloquea el veredicto; el dato entra con su
  fuente y su sostén; las lecturas originales se conservan; el veredicto anterior
  no sobrevive.
- Adaptador: payload con lo que el caso ya sabe, traducción de la respuesta,
  reintentos, y que **no se inventa** un código AFIP fuera del vocabulario (D-13).

**Fronteras (lo que **no** hace).**

- **No** decide: busca y reporta; el veredicto lo produce la pasada 2.
- **No** muta la evidencia de entrada.
- **No** busca lo no buscable ni consulta dos veces el mismo gap resuelto.
- **No** llama al agente (T-504) ni encola HITL (T-505).
- **No** hay loop abierto: la búsqueda corre **una vez**, con el presupuesto como tope.

**Hallazgos.**

1. **El gap es un campo, no una sensación**: la búsqueda "puntual con objetivo
   concreto" de E-CONC-2 solo es verificable si cada falta se nombra.
2. **Reintentar no siempre tiene sentido**: un proveedor caído es transitorio
   (se reintenta); que el padrón **haya contestado que no está** es una respuesta
   del mundo (no se reintenta).
3. **La fusión no puede pisar**: parchear los campos a mano gana la precedencia
   por código, que es justo lo que ADR-002 evita. La correcta reconstruye las
   `SourceEvidence` y vuelve a combinar.

### 3.3 T-503 · Consolidación "certeza alta por programa" ✅ Hecha

> **Estado 2026-09-11**: **Hecha** por `team implementation`. Suite completa en
> verde (**1162 passed, 10 skipped**, 46 nuevos); `python scripts/F5/t503.py`
> reporta **6/6** escenarios de consolidación + **8/8** fronteras (exit 0).

**Qué se hace.** El Gherkin de E-CONC-1, implementado:

    Regla: concluye por programa
      Dado que las reglas concluyen de forma consistente
      Cuando se consolida el resultado
      Entonces se marca certeza=alta y origen=programa
      Y NO pasó por el agente de IA para decidir

T-501 **resuelve** y T-502 **cubre gaps**; T-503 **publica**: convierte el
veredicto en el contrato congelado de F0 (`VoucherResult`, glosario §2.4) que
consume la API/CLI — estado, tipo, certeza, origen, campos planos, clasificación
contable, HITL y traza.

**La condición "sin ambigüedad"** (lo que hay que hacer verificable):

| Condición | Por qué |
|---|---|
| `concluye` | el código alcanzó un veredicto; sin él no hay nada que consolidar |
| **sin alertas pendientes** | el Gherkin pide que las reglas concluyan *de forma consistente*: una alerta de R7 sin resolver es lo contrario |
| **con letra** | una aprobación sin letra sería vacía (y un `090` no es letra del motor, D-13) |

**Archivos.**

- `src/voucherflow/conclusion/consolidacion.py` (nuevo).
- `src/voucherflow/conclusion/engine.py` (`consolidar_caso()`, y `consolidar_resultado=True` en `concluir_con_busqueda()`).
- `src/voucherflow/conclusion/__init__.py` (exportes).
- `tests/test_conclusion_consolidacion_t503.py` (nuevo, 46 tests).
- `scripts/F5/t503.py` (nuevo).

**Cómo se prueba (sin red).**

- La regla en aislamiento: cada disyunto (no concluye / con alerta / sin letra)
  baja la certeza **y lo explica**.
- El `VoucherResult`: un aprobado y un **rechazo firme** salen con certeza alta y
  origen programa; un caso ambiguo sale en revisión, baja y **sin** origen.
- La derivación: el consolidador no recibe `certeza` ni `origen` (un test lo
  verifica por firma); un `090` no se reporta como letra.
- La clasificación contable y el HITL; la integración con T-501/T-502.

**Fronteras (lo que **no** hace).**

- **No** declara la certeza: la deriva del veredicto.
- **No** inventa la clasificación contable (sin los pasos de F3 viaja sin ella).
- **No** llama al agente (T-504) ni encola HITL (T-505).
- **No** muta la evidencia de entrada ni re-calcula el veredicto.

**Hallazgos.**

1. **Un rechazo firme es certeza alta**: "esto no es válido" es una conclusión,
   no una duda. La certeza mide *cuánto sabe el sistema*.
2. **Una alerta abierta impide la certeza alta**: el fast-fail puede coexistir
   con R7; mientras la alerta siga ahí, el caso no es "consistente".
3. **El caso ambiguo no puede llevar `origen`**: no lo decidió nadie.
4. **La traza debe conservar todas las etapas**: el primer `consolidar_caso()`
   perdía el bloque `conclusion` de T-501 (lo destapó un test de integración).

### 3.4 T-504 · Escalado a agente IA + blindaje post-agente ✅ Hecha

> **Estado 2026-09-11**: **Hecha** por `team implementation`. Suite completa en
> verde (**1224 passed, 10 skipped**, 62 nuevos); `python scripts/F5/t504.py`
> reporta **6/6** escenarios de escalado + **9/9** fronteras (exit 0), con
> `% agente` **5/5**.

**Qué se hace.** ADR-008 alternativa **(a)**: el agente es una llamada a Ollama
con prompt de decisión estructurado, sin framework.

| Pieza | Módulo | Rol |
|---|---|---|
| `escalar_a_agente()` / `DecisionAgente` | `conclusion/agent.py` | el escalado y el blindaje |
| `AgenteOllama` / `Agente` (Protocol) | `conclusion/agent.py` | el adaptador real y el protocolo inyectable |
| `construir_messages_agente()` | `conclusion/prompt_agente.py` | el prompt versionado `conclusion-agente@1` |
| `concluir_con_agente()` | `conclusion/engine.py` | el pipeline completo (T-501 → T-504 → T-503) |

**El blindaje, en tres capas:**

| Capa | Qué hace | Por qué |
|---|---|---|
| El prompt | declara el universo cerrado y **no** incluye los descartados | lo que el agente no ve, no puede elegir |
| El orquestador | valida la elección contra `candidatos_restantes` | una elección fuera del universo **se rechaza** |
| El contrato | `Decision` rechaza la intersección descartados/restantes | lo hace cumplir el schema |

**Desenlaces:** `eligio`, `eleccion_invalida` (rechazada y **auditada**),
`se_abstuvo` (`null` es una salida válida), `fallo`, `no_escalado`.

**Archivos.**

- `src/voucherflow/conclusion/agent.py` (nuevo).
- `src/voucherflow/conclusion/prompt_agente.py` (nuevo).
- `src/voucherflow/conclusion/engine.py` (`escalar_a_agente()`, `concluir_con_agente()`, `ConclusionConAgente`).
- `src/voucherflow/conclusion/consolidacion.py` (**corrección**: mira el origen del veredicto).
- `src/voucherflow/rules/contexto_conclusion.py` (**corrección**: cablea los candidatos).
- `src/voucherflow/conclusion/__init__.py` (exportes).
- `tests/test_conclusion_agente_t504.py` (nuevo, 62 tests).
- `scripts/F5/t504.py` (nuevo).

**Fronteras (lo que **no** hace).**

- **No** decide si el código concluyó (no se gasta una llamada al modelo).
- **No** declara la certeza: se deriva de la etapa (baja / `agente_ia`).
- **No** corrige una elección inválida: la rechaza y la audita.
- **No** encola HITL (T-505); publica la expectativa de revisión.
- **No** normaliza ni inventa evidencia.

**Hallazgos.**

1. **Bug real de T-503**: la consolidación afirmaba "certeza alta por programa"
   para una decisión del **agente**, porque `es_certeza_alta_por_programa()` no
   miraba la etapa que decidió.
2. **Los candidatos llegaban vacíos**: `ContextoConclusion` declaraba las listas
   desde T-501 pero nadie las poblaba; el motor de F3 ya las derivaba bien ("negocio
   espera A, el documento dice B" → `['A']`/`['B']`) y se descartaban al construir
   el contexto. Sin ese cableado el agente **nunca** se habría escalado.
3. **Una elección inválida no se corrige**: corregir en silencio escondería la
   desobediencia, que es justo lo que hay que poder auditar.
4. **Abstenerse es una salida, no un fallo**: `candidato: null` se distingue de
   "no pude interpretar la salida".

### 3.5 T-505 · Cola HITL + registro de correcciones ✅ Hecha

> **Estado**: ✅ Hecha (2026-09-11). ADR-004 y ADR-009.

- `conclusion/hitl.py`: revisión obligatoria de certeza baja (prioridad `alta`)
  + muestreo de auditoría de certeza alta (prioridad `baja`, tasa configurable,
  **10%** por defecto — el extremo alto de la sugerencia del ADR-004, que es el
  que arranca la mitigación de R-03).
- `decidir_encolado()` es **pura**: devuelve un `Encolado`
  (`requerido`/`prioridad`/`motivo`/`explicacion`) sin tocar el resultado. La
  materialización en el `VoucherResult` la hace `encolar_hitl()`, que además
  registra la entrada en la cola.
- El muestreo es **reproducible**, no aleatorio: se deriva de
  `sha256(f"{semilla}:{documento_id}")` (`seleccionado_para_auditoria()`). El
  ADR-004 pide un muestreo "aleatorio estratificado configurable"; con `random`
  sin semilla la decisión de auditar un caso sería **inauditable** (no se podría
  responder por qué ése y no aquél). Además, la selección es **estable**: el
  mismo documento con la misma semilla cae siempre igual, así que re-procesar no
  cambia la suerte del caso.
- `ColaHitl` (en memoria; el store durable es T-506/ADR-009): `pendientes()`
  **priorizada** (alta primero, R-09), `obligatorios()`, `muestreados()`,
  `corregidos()`; el mismo caso **no se duplica** y re-encolar uno revisado no
  borra el trabajo humano.
- Registro de correcciones como `feedback` (alimenta reglas y prompts):
  `registrar_correccion()` guarda campo / valor anterior / valor nuevo / motivo /
  revisor / timestamp; `confirmar()` registra «la regla acertó» **sin** corrección
  (sin esa distinción, la ausencia de correcciones es ambigua entre "no se
  revisó" y "se revisó y estaba bien").
- `feedback()` **separa** las dos razones de auditar: correcciones de certeza
  baja → el **agente** se equivocó (señal para mover casuística a reglas, R-09);
  correcciones de muestreo → una **regla** acierta por accidente (señal para
  ajustarla, R-03). Mezclarlas perdería la señal de cada una.
- `HitlSettings` (nuevo en `settings/config.py`): `muestreo_tasa` (0.10),
  `muestreo_semilla` (0), `muestreo_activo`, `revision_obligatoria_certeza_baja`.
  El flag de revisión obligatoria decide **de verdad** (se limpió un `or True:`
  que lo volvía inerte) y desactivarlo **se declara** en el motivo, no se
  silencia.
- Suites: `tests/test_conclusion_hitl_t505.py` (58) y `scripts/F5/t505.py`
  (6/6 escenarios + 13/13 fronteras, `% revisión obligatoria` 2/2).
- **Cuidado al tocar las tareas anteriores**: sus tests y scripts de frontera
  verificaban que `encolar_hitl` era esqueleto (`NotImplementedError`). Con la
  tarea hecha, esa aserción deja de describir el sistema y se **reescribió** para
  verificar lo que esas fronteras querían decir: que la pasada 2 / la búsqueda /
  la consolidación / el escalado **no encolan por su cuenta** (el encolado es un
  paso explícito), y que la política de T-505 **sí** materializa la expectativa.

### 3.6 T-506 · Trazabilidad completa `CaseRecord` persistida ✅ Hecha

> **Estado**: ✅ Hecha (2026-09-11). ADR-005 / ADR-009.

- Dos piezas con responsabilidades separadas, para no mezclar "armar el registro"
  con "escribir archivos":
  - `trace/construccion.py` **arma** el `CaseRecord` desde los artefactos de la
    corrida (evidencia + decisión + resultado). Es una **proyección**: no ejecuta
    reglas ni modelos.
  - `trace/recorder.py` **persiste**: sidecar JSON + índice JSONL. `registrar()`
    deja de ser esqueleto.
- **El registro responde el Gherkin sin re-correr nada**: versión de prompt y
  modelo (del `meta` que F4 ya puso en cada lectura, más el bloque `agente` de
  T-504), evidencia por fuente, reglas disparadas y quién decidió.
- **Sidecar** `<documento>.case.json`: el `CaseRecord` completo, con **escritura
  atómica** (temporal en el mismo directorio + `os.replace` + `fsync`): si el
  proceso muere a mitad, el sidecar anterior queda intacto y nunca se lee un JSON
  truncado. Es el patrón de v1 que el §4 de los ADR manda respetar y la base de
  la reanudación por checkpoint (F6/T-602).
- **Índice** `index.jsonl`: una línea por caso con lo mínimo para encontrar y
  filtrar (`buscar(estado="rechazado")`, `buscar(hitl_requerido=True)`). Es un
  **derivado**: se hace *append* (barato y seguro en concurrencia), se
  **deduplica al leer** (una fila por documento: el `CaseRecord` es por
  documento, y una fila por corrida inflaría los agregados de T-507) y
  `reindexar()` lo reconstruye desde los sidecars.
- **Nada se inventa**: si la corrida no usó el agente, no hay modelo de agente;
  si un dato no está, el campo viaja vacío. Un registro con huecos es auditable;
  uno con datos inventados miente con apariencia de rigor.
- **Alcance honesto**: el nivel de *fuente* de la pasada 1 (`valida`,
  `debilidades`) **no viaja** en la evidencia combinada de F4. La reconstrucción
  desde `campos` es exacta en las lecturas, y el registro **declara** cuál de los
  dos caminos se usó (`directa` si el llamador pasa las `SourceEvidence`).
- Suites: `tests/test_trace_recorder_t506.py` (74) y `scripts/F5/t506.py`
  (4/4 escenarios + 14/14 fronteras).

### 3.7 T-507 · Métricas ✅ Hecha

> **Estado**: ✅ Hecha (2026-09-11). E-LIB-5.

- `trace/metricas.py`: % certeza alta, % agente, % rechazado, **acuerdo VLM/LLM**,
  cobertura HITL (obligatorios y muestreo) y tasa de alertas R7, calculados sobre
  el `CaseRecord` persistido de T-506 (`06-estrategia-calidad.md` §5).
- **Cada métrica usa su propio denominador y declara el resto**: un caso sin
  resultado consolidado no entra como "no rechazado" (no se sabe qué es) y una
  métrica sin denominador sale **no calculable con motivo**, nunca un 0% que
  miente. El reporte avisa si el lote es chico (una tendencia no se lee sobre dos
  casos) y lleva las versiones (librería + contrato + formato de traza) porque una
  métrica sin ellas no es comparable con la próxima corrida (§6).
- **El acuerdo VLM/LLM se mide sobre los campos que leyeron las dos fuentes**, no
  sobre el contrato: un campo que solo leyó una es fuente de cobertura, no de
  desacuerdo.
- El **diagnóstico del cliente de modelos** (la otra mitad de E-LIB-5) ya estaba
  desde F0/T-005 (`models/ollama.py::_diagnostico`): T-507 lo declara y no lo
  duplica.
- Herramienta con el patrón de `scripts/F3/t305.py` y `scripts/F4/t405.py`:
  `scripts/F5/t507.py` (lote sintético por defecto; `--historico DIR` para un lote
  real). Suites: `tests/test_metricas_t507.py` (47) y el script (6/6 + 11/11).

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
- **No se agregan dependencias nuevas** (el muestreo usa `hashlib` de la stdlib
  —no `random`: con semilla explícita por caso el muestreo queda reproducible y
  auditable sin estado global—; la persistencia usa `json` + escritura atómica).
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
- `src/voucherflow/conclusion/agent.py` (T-504) + `conclusion/hitl.py` (T-505) +
  `settings/config.py` (`HitlSettings`, T-505).
- `src/voucherflow/trace/recorder.py` + `trace/construccion.py` (T-506).
- `tests/test_conclusion_*_t5NN.py`, `tests/test_trace_recorder_t506.py` y
  `scripts/F5/t5NN.py` por tarea.

## 7. Avance

- **Estado (2026-09-11)**: **T-501..T-507 hechas — F5 CERRADA con su DoD
  verificado**. La **pasada 2** corre sobre la evidencia combinada de F4 y produce
  el veredicto del caso (negocio + fast-fail + conflicto R7), con
  `ConclusionResult` (diseño §4.5) y el `Decision` de F0 adjunto solo cuando el
  código concluyó. Encima se apilan la búsqueda acotada (T-502), la consolidación
  del `VoucherResult` (T-503), el escalado al agente con blindaje (T-504), la
  **cola HITL con muestreo de auditoría y feedback** (T-505), la **trazabilidad
  `CaseRecord` persistida** (T-506) y las **métricas** del cierre (T-507). Suite
  completa **1403 passed / 10 skipped**; los **12** scripts `scripts/F<n>/t*.py`
  salen con código 0.
- **Detalle por tarea**: T-501 → 94 tests + `t501.py` (8/8 + 8/8); T-502 → 73
  tests + `t502.py` (6/6 + 8/8); T-503 → 46 tests + `t503.py` (6/6 + 8/8); T-504
  → 62 tests + `t504.py` (6/6 + 9/9); T-505 → 58 tests + `t505.py` (6/6 + 13/13);
  T-506 → 74 tests + `t506.py` (4/4 + 14/14); T-507 → 47 tests + `t507.py`
  (6/6 + 11/11).

- **Punto de partida real**: la evidencia combinada de F4 (T-404) está
  disponible y con el valor vigente por campo resuelto (`CampoCombinado.valor`/
  `fuente`), y el motor R1-R7 de F3 (`evaluar_negocio`, `condicion_r7`) es
  reutilizable tal cual — así que T-501 se implementó sin tocar F3/F4.
  `CombinedEvidence.decision` ya era opcional (T-404) precisamente para que F5
  fuera quien la llene.

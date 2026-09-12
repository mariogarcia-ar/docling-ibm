# F6-subplan — Subplan de implementación F6 (Cliente CLI/batch e integración final)

> Documento de trabajo para la implementación de la **Fase F6** por
> `team implementation`. Complementa el seguimiento de la fase
> ([`F6.md`](F6.md)) y el diseño del módulo
> ([`../03-arquitectura/ORCH-CLI.md`](../03-arquitectura/ORCH-CLI.md)).
> **Fecha**: 2026-09-12 · **Rama**: `v2` · **Estado**: 🟡 **En implementación**
> (T-601..T-605 hechas; T-606 pendiente).

## 1. Ficha del subplan

| Campo | Valor |
|---|---|
| **Fase** | F6 — Cliente (CLI/batch) e integración final |
| **Tareas que cubre** | T-601, T-602, T-603, T-604, T-605, T-606 (ver [`F6.md`](F6.md)) |
| **Épicas asociadas** | E-CLI (E-CLI-1, E-CLI-2, E-CLI-3) y E-LIB-1 (API estable de alto nivel) |
| **Módulos** | `voucherflow/cli/` (`main.py`) + `voucherflow/orchestrator.py` + `voucherflow/api.py` |
| **Responsable** | team implementation |
| **Decisiones de alcance** | Cerradas con el equipo (ver §2) |
| **DoD de referencia** | "Los comandos de v1 tienen equivalente en v2 con resultados comparables (o mejores) sobre una muestra acordada de `files/`; documentación lista" (DoD de F6 en `05-plan-ejecucion.md`) |
| **Depende de** | F1–F5 (las capacidades que expone cada subcomando) |
| **Habilita a** | — (fase final; el corte de v1 se declara en T-604) |

## 2. Decisiones de alcance cerradas (2026-09-12)

1. **La CLI es un cliente fino de la fachada, no una segunda implementación.**
   Cada subcomando delega en `api.*` o en `PipelineOrchestrator.*`; la CLI no
   reimplementa reglas, prompts ni normalización. Es la materialización de
   E-LIB-1 ("librería primero, cliente después") y lo que hace que la paridad de
   T-604 mida **la librería** y no dos caminos distintos.

2. **El esqueleto de F0 se llena, no se rediseña.** `api.extract`, `api.run` y
   `PipelineOrchestrator.ejecutar` conservan su **nombre** y su primer parámetro;
   los keywords nuevos (inyección de dobles, política de la corrida) son
   aditivos con default. El contrato de F0 —incluido `PipelineResult`— sigue
   siendo construible tal cual.

3. **Sin dependencias nuevas: `argparse` de la stdlib.** El extra `cli` de
   `pyproject.toml` declaraba `typer` como posibilidad; se deja **vacío** y el
   entry point real es `[project.scripts] voucherflow`. Un CLI legible de un
   archivo es más auditable que uno que exige conocer un framework, y la regla
   dura del repo es no sumar dependencias.

4. **El resultado es dato; el progreso y el error son otra cosa.** El CLI escribe
   a `stdout` lo que el operador pidió (markdown, JSON) y a `stderr` el
   progreso/errores, y sale con **código ≠ 0** cuando algo falla. `main()`
   **devuelve** el código (no llama a `sys.exit`): así el cliente se puede
   encadenar en un script y la suite lo ejercita in-process.

5. **Lo que no está implementado se declara, no se simula.** `--workers` viaja al
   orquestador y su efecto real (`max_workers_solicitado` vs.
   `max_workers_aplicado=1`, `secuencial: true`) queda en la traza de cada
   corrida; `--force` se documenta como no-op explícito hasta que el checkpoint
   de T-602 lo vuelva la diferencia entre reprocesar y reanudar. Aceptar un flag
   y no decir qué hizo sería peor que no aceptarlo.

6. **Un rechazo no es un fallo del cliente.** El documento que no pasa el gate se
   resuelve con `ok=True` y `estado=rechazado` (el código **sí** concluyó, y
   concluyó que no); `ok=False` queda para el documento que no se pudo leer
   (inexistente, formato no soportado) y lleva el error declarado. T-601 no
   inventa una `CombinedEvidence` vacía para "poder" concluir un rechazo: fabricar
   el insumo de una decisión sería peor que la falta.

7. **El lote de T-601 es secuencial y determinista.** El descubrimiento de la
   carpeta ordena la salida (no depende del filesystem: una corrida reproducible
   es requisito de auditoría) y **excluye los artefactos derivados** (`*.case.json`,
   `*_pipeline.json`, `*_classification.json`, `*.raw.md`): la carpeta `files/`
   real está llena de salidas de v1 y reprocesarlas sería procesar la corrida
   anterior. El pool con workers —cada uno con su convertidor de Docling—, los
   checkpoints y la política de enfriamiento son **T-602** (ADR-010).

8. **La persistencia es la de F5/T-506.** `--cases DIR` usa el `CaseRecorder`
   (sidecar atómico + índice JSONL) ya implementado, en vez de inventar otra
   persistencia. La salida agregada canónica del lote es **T-603**.

9. **La clasificación contable es opt-in y su fallo no tumba el caso.** La cadena
   01→02→03 hace tres llamadas al modelo, así que se pide con
   `--clasificar-contable`; si falla, queda registrada en la traza y el resultado
   viaja **sin** clasificación (F5/T-503: "un caso sin clasificar no es un caso
   mal clasificado"). Perder el veredicto por un error de una etapa posterior
   sería lo contrario de lo que el pipeline de v1 hacía.

10. **Determinismo sin red (regla dura del repo).** Todas las colaboraciones
    (lector de modelos, agente, convertidor, buscador) entran **inyectadas**:
    `EntornoCLI` para el cliente y los keywords del orquestador. Con dobles, los
    once subcomandos y la corrida completa se ejercitan sin Ollama ni Docling.

## 3. Alcance por tarea (T-601..T-606)

### 3.1 T-601 · CLI `voucherflow` con subcomandos ✅ Hecha

> **Estado 2026-09-12**: **Hecha** por `team implementation`. Suite completa en
> verde (**1460 passed, 10 skipped**, 57 nuevos); `python scripts/F6/t601.py`
> reporta **9/9** escenarios + **6/6** fronteras (exit 0). `F6.md` pasa de
> 🔴 Backlog a 🟡 En implementación.

**Qué se hace.** El cliente del DoD: el paquete `voucherflow/cli/` con el parser
de subcomandos y, detrás, las dos piezas que faltaban para que una corrida real
exista —el **orquestador** que encadena F1→F5 y la **fachada** que publica
`extract`/`run`.

**Los tres artefactos.**

| Pieza | Archivo | Qué aporta |
|---|---|---|
| Orquestador | `orchestrator.py` | `PipelineOrchestrator.ejecutar` (processing → validation → extraction → combinación → conclusión → traza), `ejecutar_lote`, `iterar_documentos`, `identificador_de_archivo`, `PipelineResult` poblado |
| Fachada | `api.py` | `extract` (procesa + gate + flujos + combinación → `CombinedEvidence`), `run` (→ `VoucherResult`), `ask` (pregunta puntual, equiv. `v1/ask.py`) y `extraction_version()` |
| Cliente | `cli/main.py` + `cli/__init__.py` | los **once** subcomandos, los cinco flags comunes, `EntornoCLI` inyectable y `main()` que devuelve código |

**Los subcomandos** (E-CLI-1 + `extract-detect`/`arca`/`case`/`hitl` de
`ORCH-CLI.md` §3): `process`, `validate`, `classify`, `extract`,
`extract-detect`, `run`, `batch`, `ask`, `arca`, `case` (`show`/`list`), `hitl`
(`list`).

**Equivalencias con v1** (la base del mapa que T-604 verifica):

| v1 | v2 (CLI) |
|---|---|
| `ocr_documents.py` | `voucherflow process` |
| `run_raw.py` | `voucherflow process --raw` |
| `classification_pipeline.py` | `voucherflow classify` |
| `extraction_pipeline.py` | `voucherflow extract` |
| `document_extraction.py -M 11.1` | `voucherflow extract-detect` |
| `full_pipeline.py` | `voucherflow run` / `batch` |
| `ask.py` | `voucherflow ask` |
| `wip/consultar_arca.py` | `voucherflow arca check` |

**El fast-fail del gate, en el orquestador.** Un documento que no es comprobante
se resuelve como `rechazado` con certeza `alta` y origen `programa`, **sin** gastar
extracción ni llamada de conclusión. Es la aplicación directa de las reglas de F5
(un rechazo firme es una conclusión, no una duda) al primer corte del pipeline, y
el `CaseRecord` del rechazo se arma igual (es el caso más interesante de auditar).

**Cómo se prueba (sin red).**

- **Contrato del CLI**: los once subcomandos existen, el despacho los cubre
  exactamente, los cinco flags comunes parsean y `main([])` devuelve 2 (uso).
- **Orquestador**: la secuencia de seis etapas, el veredicto del motor
  (`A`/`alta`/`programa`), el fast-fail del gate sin etapa de extracción, el
  documento ilegible (`ok=False` + error), `--orientation` (re-exporta con boxes;
  declara que no aplica en texto nativo), la clasificación contable opt-in y su
  fallo contenido, la persistencia del `CaseRecord` y la serialización.
- **Fachada**: `api.run` → `VoucherResult`, `api.extract` → `CombinedEvidence`
  con el modo heredado registrado, y que ya **no** son esqueletos.
- **Subcomandos sobre el filesystem** (con `tmp_path`): `process` escribe el
  markdown, `extract`/`extract-detect`/`run`/`batch` producen su JSON, el lote
  descubre subcarpetas y `case show/list` reconstruye del sidecar.
- **Fronteras**: `--workers` declarado como solicitado, `--force` no-op
  explícito, `arca check` sin configuración *no disponible* (ADR-003), el
  contrato de F0 de `PipelineResult` intacto y el descubrimiento de dos documentos
  homónimos en subcarpetas distintas.

**Fronteras de la tarea (lo que **no** hace).**

- **No** implementa el pool de workers, los checkpoints/reanudación ni la
  política de enfriamiento (T-602): el lote es secuencial y lo declara.
- **No** define la política de sidecars ni el formato de la salida agregada
  (T-603): ofrece el `CaseRecord` y reutiliza el `CaseRecorder` de T-506.
- **No** mide ni declara el corte de v1 (T-604) ni escribe la documentación de
  usuario (T-605) ni la API HTTP (T-606, fase 2).
- **No** llama a ARCA desde el pipeline: el hook es inyectable y desactivado por
  defecto (ADR-003).

**Hallazgos de la implementación.**

1. **Los esqueletos de F0 se cierran, no se borran.** `test_esqueletos_lanzan_notimplemented`
   era la lista de pendientes de la fachada; con T-601 queda **vacía** y el test
   se reescribió para fijar la frontera nueva (ninguna operación pública lanza
   `NotImplementedError` y el orquestador resuelve el archivo inexistente con el
   resultado declarado). Es la lección de F5/T-505 aplicada a una frontera:
   cuando la aserción deja de describir el sistema, se **reescribe** para decir
   lo que quería decir.
2. **`procesar_imagen` tiene su propio gate (T-102), distinto del gate qween.**
   Ejercitar el fast-fail de F2 con una imagen de prueba exigiría que el binario
   tuviera dimensiones legibles; el test usa **texto nativo** para no acoplar la
   orquestación al fixture binario. Los dos gates son controles distintos y el
   pipeline los corre en momentos distintos.
3. **`api.run` no debe confundir "no se pudo leer" con "rechazado".** El
   orquestador devuelve `ok=False` para el archivo ilegible y `ok=True` +
   `estado=rechazado` para el gate negativo; la fachada convierte **solo** el
   primero en `DocumentoNoProcesableError`. Aplanar los dos habría hecho que el
   cliente tratara un rechazo firme como una excepción de infraestructura.
4. **El modo heredado de v1 no cambia el contrato.** `--mode kvi|kvg|10|11` se
   registra para la paridad de T-604, pero v2 tiene **un** contrato de extracción
   versionado: fingir una diferencia de prompt que no existe haría la paridad
   inauditable.

### 3.2 T-602 · Modo batch con workers, checkpoints y enfriamiento (ADR-010) ✅ Hecha

> **Estado 2026-09-12**: **Hecha** por `team implementation`. Suite completa en
> verde (**1506 passed, 10 skipped**, 46 nuevos); `python scripts/F6/t602.py`
> reporta **12/12** escenarios + **6/6** fronteras (exit 0).

**Qué se hace.** `batch.py` (nuevo) es el runner de lotes que T-601 dejó
preparado. `orchestrator.ejecutar_lote` y `cli/main.py::_cmd_batch` delegan en
él; el contrato de retorno de T-601 (lista de `PipelineResult`) se conserva y la
traza del lote viaja en `detalle['lote']` de cada resultado.

**(1) Workers.** `EjecutorProcesos` usa `ProcessPoolExecutor` con un
`initializer` por worker (patrón `init_worker` de `v1/full_pipeline.py`): cada
proceso construye **su** convertidor de Docling y **su** cliente de modelos, de
forma **perezosa** (el costo se paga en el primer trabajo del worker, no al
arrancar el pool, y un lote de un documento no carga modelos de más).

El trabajo cruza la frontera del proceso como **dict serializable**
(`construir_trabajo` → `resultado_a_payload` → `resultado_desde_dict`): el worker
devuelve datos y el padre los **revalida contra los contratos congelados**. Así el
lote no depende de que las sesiones HTTP ni los convertidores sean picklables, y
un payload que no respeta el schema falla en la frontera y no más adelante con un
objeto a medias.

Con **1 worker** el lote corre en el proceso actual (`EjecutorSerial`,
determinista): un proceso propio no se justifica frente al costo de serializar y
recargar el convertidor. Con un orquestador **inyectado** (los dobles no cruzan a
otro proceso) también cae a serial y **lo declara** en la traza, en vez de
reportar un paralelismo que no existió.

**(2) Checkpoints y reanudación.** Cada documento completado deja
`<doc>.batch.json` —junto al documento, como el `_pipeline.json` de v1— con
escritura **atómica** (temporal + `os.replace`, patrón F5/T-506). La corrida
siguiente **saltea** lo completado (Gherkin E-CLI-1: "retoma desde los checkpoints
sin repetir pasos completados") y `--force` lo reprocesa.

Tres decisiones de honestidad, y las tres tienen test:

| Regla | Por qué |
|---|---|
| El checkpoint guarda el **hash del contenido**, no el nombre | un documento que **cambió** no se saltea aunque no se pase `--force`: saltearlo afirmaría que es el mismo documento |
| Un checkpoint con `ok=False` **no** se reutiliza | un error no es un paso completado; si se reutilizara, un documento fallido quedaría salteado **para siempre** |
| Un checkpoint **corrupto** se trata como ausente | un derivado roto no puede hacer saltear trabajo; igual criterio que el índice de T-506 |

**(3) Enfriamiento (ADR-010).** El requisito literal de `my_prompt.md` —"la
cuenta de los 2 minutos empieza cuando todos los workers están detenidos"— se
cumple **por construcción**: el ciclo de trabajo se organiza en **olas** del
tamaño del pool; al vencer `work_window_s` se cierra el ciclo, se **detiene el
pool** (`shutdown(wait=True)`: ningún worker vivo), **recién entonces** se marca
`todos_detenidos_s` y se duerme `cool_down_s`. El **último** ciclo nunca enfría
(no queda trabajo para reanudar). La traza guarda los tres instantes
(`inicio_ventana_s`, `todos_detenidos_s`, `enfriado_s`), así que la semántica es
auditable y no una promesa.

**Por qué olas y no un pool que se pausa**: un proceso detenido es lo que hace
real la pausa térmica (no consume CPU ni retiene los modelos cargados); un pool
"pausado" pero vivo no enfría nada. Y la ola da una unidad determinista de
planificación: la ventana se evalúa **entre** olas, así que no se interrumpe un
documento a la mitad.

**El CLI.** `--cooling on|off|auto` (respeta o fuerza `CoolingSettings.enabled`),
`--work-window S`, `--cool-down S` (acortar la ventana sin tocar el YAML: es lo que
hace testeable la política) y `--no-checkpoints` (ignora y no escribe, **sin**
borrar el estado existente: apagar el mecanismo no puede destruir el lote).
`--force` ahora **decide** de verdad; en `run` sigue sin efecto porque un documento
suelto no tiene lote del cual reanudar, y lo declara.

**Cómo se prueba (sin procesos reales, sin dormir).**

- **Workers**: 1 worker = serial; varios = pool (con un espía que verifica la
  capacidad); el trabajo y el resultado cruzan serializables; las olas respetan la
  capacidad (5 documentos con capacidad 2 → olas de 2, 2 y 1); y el **ciclo real**
  del `ProcessPoolExecutor` con una función pura de módulo (verifica que el trabajo
  corrió en **otro** proceso por el `pid` del resultado).
- **Checkpoints**: se dejan al completar, no se descubren como documentos, la
  segunda corrida reanuda, `--force` reprocesa, el cambiado no se saltea, el fallido
  no se reutiliza y el corrupto se reprocesa. Además: escritura atómica (sin
  temporales), directorio no escribible declarado (no tumba el lote) y apagar los
  checkpoints no borra nada.
- **Enfriamiento**: el ciclo, la ventana, el instante de "todos detenidos", el
  último ciclo sin pausa, `enabled=False` sin pausas, y que el pool se detenga en
  cada ciclo. Todo con **reloj y ejecutor inyectados**: la suite no spawnea procesos
  ni duerme.
- **Fronteras**: el runner no decide (el veredicto del gate viaja intacto), no se
  inventa checkpoint de un archivo inexistente, el contrato de T-601 se conserva y
  el pool no acepta trabajo sin `iniciar()`.

**Fronteras de la tarea (lo que **no** hace).**

- **No** define la salida agregada del lote ni su formato canónico (T-603).
- **No** cambia la persistencia de la trazabilidad: `--cases` sigue siendo el
  `CaseRecorder` de F5/T-506.
- **No** reintenta documentos fallidos dentro de la misma corrida (el reintento es
  **entre corridas**, que es donde tiene sentido); los retries/backoff avanzados de
  E-CLI-3 siguen como Should.
- **No** mide temperaturas reales: la política es por tiempo de trabajo continuo,
  que es lo que define el ADR-010.

**Hallazgos de la implementación.**

1. **Un bug real, destapado por el test del pool**: `EjecutorProcesos` devolvía el
   `Future` de `concurrent.futures`, que expone **`result()`**, mientras el contrato
   del runner es **`resultado()`**. El camino de **producción** con más de un worker
   habría roto al primer lote — y los dobles no lo veían porque usaban el contrato
   del runner. Fix: `_FuturoProceso` adapta el `Future` en la frontera. Lección: un
   detalle de la stdlib no debe condicionar el contrato propio, y el doble tiene que
   respetar el contrato **del componente**, no el de la librería.
2. **Un error no es un paso completado**: la primera versión de `es_reanudable()`
   solo miraba que existiera el checkpoint. Con eso, un documento que falló quedaría
   salteado para siempre y el lote nunca lo reintentaría.
3. **El hash es la identidad, no el nombre**: reanudar por nombre de archivo es la
   trampa obvia y **silenciosa** (no falla: saltea trabajo que había que rehacer).
   El `sha256` del contenido hace que el caso correcto sea el que sale gratis y el
   incorrecto el que exige `--force`.

### 3.3 T-603 · Sidecars con trazabilidad y salida agregada ✅ Hecha

> **Estado 2026-09-12**: **Hecha** por `team implementation`. Suite completa en
> verde (**1547 passed, 10 skipped**, 41 nuevos); `python scripts/F6/t603.py`
> reporta **8/8** escenarios + **9/9** fronteras (exit 0).

**Qué se hace.** El Gherkin de E-CLI-2 tiene dos mitades, y llegaron en momentos
distintos: la del **sidecar** ya estaba hecha en F5/T-506 y expuesta por el CLI en
T-601, y T-603 la **verifica de punta a punta** sin reimplementarla. Lo nuevo es la
segunda: el **único JSON agregado** del lote (`trace/agregado.py`).

**El agregado es un índice, no una copia.** Es la decisión central de la tarea:

| Alternativa | Por qué no |
|---|---|
| Embeber los `CaseRecord` enteros | Cientos de KB por caso (todas las lecturas por fuente, con su sostén): un lote de mil documentos daría un archivo de cientos de MB que nadie puede abrir, y **duplicaría** el dato |
| Embeber solo la evidencia | Mismo problema, y crea una **segunda fuente de verdad** que puede divergir: corregir un caso dejaría el agregado mintiendo sin que nadie lo note |
| **Índice + punteros** | El agregado no puede contradecir al sidecar (solo apunta a él), el archivo queda chico, y cada pregunta vive donde corresponde: *"¿qué pasó en el lote?"* → agregado; *"¿por qué se decidió así?"* → `case show <id>` |

**Lo que hace:**

- **Una entrada por documento** con el veredicto y el **puntero al sidecar**.
- **Síntesis del lote**: cuántos, cómo salieron (`por_estado`), cuántos requieren
  revisión y cuántos traen puntero.
- **Métricas** derivadas del histórico (F5/T-507) o **declaradas ausentes** con su
  motivo (`metricas_no_disponibles`) — nunca ceros que no se pueden sostener.
- **Acumulación entre corridas** (`agregar_a_archivo`) con una entrada por
  documento: reprocesar **actualiza**, no duplica. Es lo que hace que el archivo
  sea el **estado de la carpeta** y no el reporte de la última corrida.
- **Escritura atómica** e **inmune a errores de lectura**: un agregado ilegible o
  un JSON ajeno **no** se sobreescriben (sobreescribirlos borraría el lote
  anterior), y una entrada que ya venía del sidecar no se degrada a la proyección
  de la corrida.

**En el CLI**: `batch -o` pasa a escribir el agregado (es el JSON consolidado que
pide el Gherkin; default `lote.agregado.json`) y se agrega `case aggregate`, que lo
**reconstruye** leyendo los sidecars — el camino para una carpeta procesada en
varias sesiones o para recuperar el archivo si se perdió.

**Cómo se prueba (sin red).**

- **La mitad del sidecar**: `run --cases` genera el JSON con resultado + evidencia
  + trazabilidad, se lee de vuelta al contrato y `case show` lo muestra.
- **La forma del agregado**: una entrada por documento con su veredicto, punteros
  a los sidecars, la síntesis por estado, la revisión obligatoria separada, y que
  los errores **no** se confundan con los rechazos (un rechazo es una conclusión).
- **Que no duplique**: se verifica sobre el serializado que no haya
  `evidencia_por_fuente` ni fragmentos de sostén, y que el contrato declare dónde
  está la evidencia.
- **Persistencia**: round-trip del agregado, escritura atómica, acumulación, el
  mismo documento actualizándose, y que un archivo ilegible o ajeno no se pise.
- **Métricas**: sin histórico salen `null` **con motivo**; con histórico se
  calculan; y una corrida sin `--cases` no borra un cálculo previo.
- **Integración**: `batch` escribe y acumula el agregado, incluye métricas con
  `--cases`, y `case aggregate` reconstruye del histórico (a stdout o a archivo).

**Fronteras de la tarea (lo que **no** hace).**

- **No** reimplementa la persistencia por caso (F5/T-506) ni el runner (T-602).
- **No** embebe la evidencia (§ arriba): apunta al sidecar.
- **No** corre el pipeline: es una proyección de lo que las etapas produjeron.
- **No** inventa el puntero cuando no hay sidecar (la clave se omite), ni el
  estado cuando la corrida no alcanzó un veredicto (se cuenta `sin_estado`, no
  `revision`).

**Hallazgos de la implementación.**

1. **El agregado perdía el veredicto**: la primera versión leía el estado **solo**
   del `VoucherResult` tipado. Un resultado que llegaba sin ese objeto entraba como
   `sin_estado` — afirmando que el caso no se resolvió cuando sí se había resuelto.
   Ahora se lee del `VoucherResult` y, si no está, del `resumen` (las dos fuentes
   llevan el mismo dato que publica el orquestador, así que no se inventa nada). Lo
   destapó un test que arma el `PipelineResult` a mano.
2. **Una corrida sin casos borraba las métricas**: recalcular el bloque solo
   cuando la corrida traía `CaseRecord` hacía que una corrida posterior sin
   `--cases` perdiera un cálculo previo. Eso no es honestidad, es perder trabajo:
   la honestidad es **no inventar** métricas cuando nunca hubo datos —y eso lo
   declara el agregado—, no olvidar las que sí se calcularon.
3. **La tolerancia a errores de lectura es de seguridad, no de robustez**: que un
   agregado corrupto **no** se sobreescriba importa porque el agregado es el
   archivo que se mira para saber si el lote terminó. Tratarlo como vacío habría
   borrado el lote anterior en silencio.

### 3.4 T-604 · Mapa de paridad v1→v2 sobre carpetas reales de `files/` ✅ Hecha

> **Estado 2026-09-12**: **Hecha** por `team implementation`. Suite completa en
> verde (**1576 passed, 10 skipped**, 29 nuevos); `python scripts/F6/t604.py`
> reporta el mapa **en verde** (8/8 comandos con banderas completas, 7/7
> artefactos) — exit 0.

**Qué se hace.** El DoD de la fase ("los comandos de v1 tienen equivalente en v2
con resultados comparables o mejores sobre una muestra acordada de `files/`") se
mide en los tres niveles que tienen sustento objetivo, y **declara** lo que queda
afuera.

**Lo que NO se compara, y por qué.** El veredicto documento a documento entre v1 y
v2. v1 y v2 tienen arquitecturas **distintas a propósito**:

| | v1 | v2 |
|---|---|---|
| Quién lee | el modelo, con un prompt por modo | dos flujos (VLM+LLM) con **un** contrato de evidencia |
| Quién normaliza | el modelo, dentro del prompt | el programa (`key_value.py`) |
| Quién decide la letra | el modelo (`comprobante_valido`) | el motor R1-R7 en código |

Comparar las salidas y declararlas "comparables" mezclaría **una mejora de diseño
con una regresión**. La comparación está excluida y **derivada**: la letra se midió
en F3/T-305 (v2 5/5 vs. v1 2/5) y la extracción en F4/T-405 (reglas 20/20, campos
29/29, sostén 32/32).

**Los tres niveles que sí se miden:**

1. **Procedencia** — cada comando del mapa cita el script de v1 que reemplaza, y el
   script **existe**. Un mapa que cita archivos inexistentes declara procedencias
   falsas.
2. **Superficie** — 8/8 comandos con equivalente, y **cada bandera de v1
   declarada**: o mapeada a su bandera de v2 (verificada contra el **parser real**,
   no contra una lista escrita a mano) o marcada *sin equivalente* con su motivo.
   Un test exige que toda bandera esté en una de las dos listas: una olvidada se
   leería como "no existía".
3. **Artefactos** — 7 declarados: 3 **coinciden** con v1 (`<doc>.md`,
   `<doc>.raw.md`, `<doc>_classification.json`) y 4 **cambian con motivo**
   (checkpoint del lote, salida de extracción, agregado, sidecar). Se distingue
   "coincide" de "mejor" de "cambia por diseño": confundirlas es el error que el
   mapa existe para evitar.

El manifiesto declara además las **equivalencias de capacidad** (mejora o
diferencia por diseño, **nunca** "paridad"), lo que queda **fuera de paridad** con
el motivo y dónde se mide, y el **corte de v1**.

**La muestra**: `tests/fixtures/golden` (la copia versionada). `files/` es temporal
e ignorada por git, así que una medición que dependa de ella no sería reproducible
entre clones. El DoD pide "una muestra acordada" y esa copia **es** el acuerdo.

**Archivos.** `tests/golden/F6/{subconjunto.json,README.md}` (el manifiesto y su
documentación), `scripts/F6/paridad_cli.py` (funciones puras + el mapa),
`scripts/F6/t604.py` (el reporte) y `tests/test_paridad_t604.py` (29).

**Cómo se prueba (sin red).**

- **Procedencia**: los scripts de v1 citados existen y el subconjunto declara su
  decisión de alcance y su muestra.
- **Superficie**: todas las banderas equivalentes existen en v2, ninguna bandera de
  v1 quedó sin declarar, y el mapa está en verde. Con **controles negativos**: el
  chequeo tiene que poder fallar (un comando inexistente y una bandera equivalente
  ausente se detectan).
- **Artefactos**: coherencia del mapa (no se puede declarar coincidencia donde no
  la hay) y **verificación en vivo** de los nombres que se declaran coincidentes
  (el checkpoint contable, el `.md` por posición, el `.raw.md`).
- **Fronteras**: lo que queda fuera está declarado con motivo y destino, la
  comparación del veredicto está excluida y derivada, las capacidades mejoradas se
  declaran como tales (nunca como paridad) y el **corte de v1** es explícito.

**Fronteras de la tarea (lo que **no** hace).**

- **No** compara el veredicto (excluido y derivado, ver arriba).
- **No** mide la corrida real con modelos como criterio: `--real` es informativa
  (requiere Ollama y `files/` no versionada).
- **No** migra ni actualiza v1: se congela como referencia.

**Hallazgos de la implementación.**

1. **Un riesgo de pérdida de datos, introducido en v2**: `process` acepta
   documentos de texto plano (`.md`/`.txt`) y para un `.md` **el sufijo de salida
   coincide con el de la entrada** — la corrida escribía el markdown **encima del
   documento original**. v1 no corría el riesgo (su lista de extensiones era solo
   imágenes y PDF). Se aparta el destino a `<doc>.processed.md` cuando colisiona, y
   se arregla de paso `--raw`, que escribía en `<doc>.md` en vez del `<doc>.raw.md`
   de v1: el crudo y el ordenado son dos artefactos y no pueden compartir archivo.
   Ambos casos quedan con test de regresión.
2. **Un chequeo de superficie solo vale si el mapa declara la correspondencia
   bandera por bandera**: con la lista de flags de v1 sola, los atajos cortos
   (`-o`, `-m`) hacían que **todos** los comandos aparecieran como brecha; con el
   mapeo explícito aparecieron **dos brechas reales** que la lista ocultaba
   (`classify` no aceptaba `-o`, que v1 sí tenía; y un mapeo de `--modality` que
   había escrito era inventado y **tapaba** la ausencia de esa capacidad). Las dos
   se resolvieron: `-o` se implementó y `--modality` se declaró *sin equivalente*
   con el motivo real (v2 corre **siempre** los dos flujos, así que no hay motor
   que elegir).

### 3.5 T-605 · Documentación de usuario + README v2 ✅ Hecha

> **Estado 2026-09-12**: **Hecha** por `team implementation`. Suite completa en
> verde (**1606 passed, 10 skipped**, 30 nuevos); `python scripts/F6/t605.py`
> reporta **27/27** verificaciones — exit 0.

**Qué se hizo.** La guía del operador en `docs/usuario/` (cinco documentos: el
índice, instalación, referencia de los once subcomandos, revisión humana y
salidas) y el `README.md` de v2 con la sección *Documentación para quien opera* —
que es lo que T-601 dejó como base factual (`--help`) convertido en guía usable.

**La decisión central: la doc no repite lo que la máquina dice mejor.** El
`--help` de cada comando sigue siendo la fuente más actualizada y la guía lo dice
explícitamente. Lo que la guía aporta es lo que el `--help` no puede dar:
*cuándo* usar cada comando (y cuándo no), *por qué* el resultado tiene el estado
que tiene y *qué hacer* con él. Por eso el documento central no es la referencia
de banderas sino el **índice**, que enseña los tres conceptos que hacen usable el
sistema:

| Concepto | Qué enseña |
|---|---|
| **Estado del caso** | `aprobado` / `revision` / `rechazado`, y que **un rechazo firme es una conclusión** (certeza alta), no un error de la corrida |
| **Certeza** | Mide cuánto sabe el sistema (alta = lo decidió el código con reglas; baja = un modelo o una persona), no si el resultado "gusta" |
| **Auditabilidad** | `--cases` + `case show` es lo que permite responder *"¿por qué se decidió así?"* |

**Los límites se declaran, no se esconden.** Tres fronteras quedaron explícitas
porque el operador las va a encontrar:

- El CLI **no** carga correcciones HITL: la cola de la librería es de esa corrida
  (`registrar_correccion` exige el caso ya encolado y lanza `KeyError` si no
  está), así que desde la terminal la revisión es de **lectura** (`hitl list
  --dir`, `case show`). El camino por librería se documenta con su alcance.
- `--force` y `--workers` se aceptan en todos los comandos del pipeline pero
  **solo `batch`** les da efecto real. `run` lo declara en su traza
  (`detalle.force`) en vez de fingir un efecto.
- `ask` **no** produce evidencia ni se audita: es una consulta libre. Lo que haya
  que justificar va por `run`.

**Archivos.** `docs/usuario/{README,01-instalacion,02-comandos,03-revision-humana,04-salidas}.md`
(nuevos), `v2/README.md` (actualizado), `tests/test_docs_usuario_t605.py` (30) y
`scripts/F6/t605.py` (el reporte).

**Cómo se prueba (sin red).** La documentación es un **artefacto verificable**
contra la fuente factual, no un texto que se revisa a ojo:

- **Cobertura de comandos, en las dos direcciones**: cada uno de los once
  subcomandos del contrato (E-CLI-1) tiene sección, y la referencia **no
  documenta comandos que no existen**. Un comando sin documentar es
  indescubrible; uno inventado manda al operador a un error.
- **Banderas en la sección de su comando**: cada bandera larga que el parser
  **real** acepta aparece en la sección de *ese* comando (o en "Banderas
  comunes"). Buscar en todo el documento sería más laxo —una bandera documentada
  en la sección equivocada pasaría el chequeo— y el operador que lee la sección
  de ese comando no la encontraría.
- **Navegación**: todo enlace relativo resuelve (incluido `../../../BATCH.md`,
  que sale del árbol de `docs/`), y cada documento vuelve al índice.
- **Configuración real**: las claves YAML que la guía muestra existen en los
  defaults de `Settings`, y ningún bloque YAML usa una clave inventada (un typo
  se ignora en silencio en el merge: el sistema arrancaría con el default sin
  avisar).
- **Fronteras**: la doc declara que no hay comando de correcciones, que
  `--force`/`--workers` solo aplican a `batch`, que un rechazo firme es certeza
  alta y que `ask` no es auditable. Si alguna de esas capacidades cambia, el test
  falla y **obliga a actualizar la doc a propósito**.

**Hallazgos de la implementación.**

1. **Los tests nuevos destaparon cuatro errores en la doc recién escrita** —tres
   enlaces relativos rotos (`../docs/plan/` desde `docs/usuario/` resolvía a
   `docs/usuario/docs/plan/`; y `../../BATCH.md` apuntaba a `v2/BATCH.md` cuando
   la guía vive en la **raíz del repo**) y `hitl.*` documentado en el doc de
   instalación en vez del de revisión—. Sin los tests, los cuatro habrían
   llegado al operador como callejones sin salida. La verificación automatizada
   de la doc no es ceremonia.
2. **Un defecto de edición previo en la bitácora de `F6.md`**: la fila de T-602
   arrastraba **1759 caracteres del texto de T-601 pegados al final** (dos
   cierres de fila en una misma línea). Se detectó al contar filas **por total**
   (y no por fecha, que es lo que lo ocultaba) para insertar la fila nueva, y se
   recortó por índice de línea. Tercera vez que el patrón "append a una tabla
   markdown" muerde en esta fase.
3. **Escribir la referencia destapó tres imprecisiones propias** que se
   corrigieron antes de publicar: el atributo real es `entrada.correcciones`
   (lista) y no `entrada.correccion`; `hitl` sin `--dir` **no** lee el histórico
   (reporta la cola viva de esa corrida, que en un proceso nuevo es vacía); y el
   filtro de `case list` valida los campos contra `CAMPOS_INDICE` y **falla**
   ante uno desconocido en vez de devolver cero resultados (que se leería como
   "no hay ninguno").

**Fronteras de la tarea (lo que **no** hace).**

- **No** implementa el comando de carga de correcciones HITL: se documenta la
  frontera y el camino por librería. Era parte del entregable "documentación
  lista", no un desarrollo nuevo.
- **No** documenta la API HTTP (T-606): no existe todavía.
- **No** duplica el `--help`: lo complementa.

### 3.6 T-606 · API HTTP (fase 2, no bloqueante) ⬜ Pendiente

**Qué hay que hacer**: la API HTTP básica del C4 nivel 2, declarada como fase 2 /
no bloqueante para el MVP (MoSCoW §4 del doc 05). T-601 deja `api.run`/
`api.extract` como la superficie que la API HTTP expondría.

## 4. Reglas duras (no romper F0–F5)

- **No cambiar los contratos congelados**: `SCHEMA_VERSION = 1.0.0`, `Decision`,
  `VoucherResult`, `CaseRecord` y las firmas de `api.process/validate/classify`.
  T-601 **llena** los esqueletos (`extract`/`run`/`ejecutar`), no los rediseña.
- **La certeza se deriva de la etapa que decidió** (glosario §2): el fast-fail del
  gate sale `alta`/`programa` porque el código concluyó; ninguna capa del cliente
  la escribe a mano.
- **El agente no puede elegir candidatos descartados** (ADR-008): el cliente
  **no** revalida el blindaje de T-504, lo consume.
- **Sin dependencias nuevas**: `argparse`/`json`/`pathlib` de la stdlib. El pool
  de T-602 usa `concurrent.futures` (stdlib), como v1.
- **La suite default corre sin Ollama ni Docling reales**: el cliente y el
  orquestador reciben los dobles por `EntornoCLI`/keywords.
- **Un documento no se pierde**: formato no soportado o archivo ilegible se
  reportan con su error; un gate negativo es un `rechazado` con certeza alta.
- Estilo: docstrings y mensajes en español citando los docs (doc 03 §5.1/§8.1,
  IDs de épica/tarea); asserts con mensaje explicativo.

## 5. Flujo del cliente (referencia de implementación)

```text
CLI (argparse)                 Orquestador                    Librería
  voucherflow run X      →   PipelineOrchestrator.ejecutar
                               ├─ processing   (F1/T-105)  →  ProcessedDocument
                               ├─ validation   (F2/T-203)  →  ResultadoValidacion
                               │     └─ no comprobante → rechazado/alta (fast-fail)
                               ├─ extraction   (F4/T-401)  →  SourceEvidence × 2
                               ├─ combinación  (F4/T-404)  →  CombinedEvidence
                               ├─ conclusión   (F5/T-501..T-504) → VoucherResult
                               └─ traza        (F5/T-506)  →  CaseRecord
                                                             │
                         →   PipelineResult  ←───────────────┘
                             (resultado + evidencia + caso + detalle)
```

- El **detalle** de la corrida publica cada etapa (tipo de entrada, gate, fuentes,
  balance de la extracción, bloques de la conclusión, clasificación contable,
  persistencia) para que el `CaseRecord` y el reporte de la CLI sean auditables.
- La CLI agrega la capa de **presentación** (JSON a stdout o a `-o`, progreso a
  stderr, código de salida) y **no** vuelve a decidir nada.

## 6. Archivos previstos

### T-601
- `src/voucherflow/cli/__init__.py` y `src/voucherflow/cli/main.py` (nuevos).
- `src/voucherflow/orchestrator.py` (implementado; deja de ser esqueleto).
- `src/voucherflow/api.py` (`extract`/`run`/`ask` implementados; `__all__`).
- `pyproject.toml` (extra `cli` vacío + `[project.scripts] voucherflow`).
- `tests/test_cli_t601.py` (nuevo) y `tests/test_golden_y_esqueleto.py`
  (frontera de esqueletos reescrita).
- `scripts/F6/t601.py` (nuevo).

### T-602..T-606
- `src/voucherflow/cli/batch.py` (pool + checkpoints + enfriamiento, T-602).
- `src/voucherflow/trace/agregado.py` (salida agregada, T-603) — nombre tentativo.
- `tests/golden/F6/` + `scripts/F6/paridad_*.py` (T-604).
- `docs/usuario/` + `README.md` (T-605).
- `src/voucherflow/http/` (T-606, fase 2) — no bloqueante para el MVP.

## 7. Avance

- **Estado (2026-09-12)**: **T-601, T-602, T-603, T-604 y T-605 hechas**. El
  cliente existe (once subcomandos), el lote corre con workers,
  checkpoints/reanudación y la política de enfriamiento del ADR-010, y la corrida
  se consolida en un **único JSON agregado** (índice + punteros + síntesis +
  métricas) que se acumula entre corridas y se reconstruye del histórico. **T-604**
  cerró el DoD de la fase: el **mapa de paridad v1→v2** en tres niveles
  deterministas y el **corte de v1** declarado. **T-605** cerró el otro extremo
  del DoD ("documentación lista"): la **guía del operador** (`docs/usuario/`, cinco
  documentos) y el README de v2, con la **cobertura verificada por tests** contra
  el contrato del CLI. Suite completa **1606 passed / 10 skipped** (57 nuevos de
  T-601 + 46 de T-602 + 41 de T-603 + 29 de T-604 + 30 de T-605);
  `scripts/F6/t601.py` → **9/9** + **6/6**, `t602.py` → **12/12** + **6/6**,
  `t603.py` → **8/8** + **9/9**, `t604.py` → mapa en verde y `t605.py` → **27/27**
  (todos exit 0).
- **Punto de partida real**: F5 dejó el `CaseRecord` persistible (`CaseRecorder`) y
  el `VoucherResult` consolidado; T-601 puso el encadenamiento y la superficie de
  invocación; T-602 el **runner** que hace viable un lote largo; T-603 la
  **consolidación** que hace que el resultado de un lote se lea en un archivo; y
  T-605 la **guía** que hace que todo eso sea usable sin leer el código.
- **Lo que sigue**: **T-606** (API HTTP, fase 2 / no bloqueante). El **DoD de F6
  está cumplido**: paridad con el corte de v1 declarado (T-604) y documentación
  lista (T-605).

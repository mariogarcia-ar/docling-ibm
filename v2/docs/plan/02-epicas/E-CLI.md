# Épica E-CLI — Cliente (Seguimiento)

> Documento de seguimiento generado a partir de
> [`02-epicas-historias-usuario.md`](../02-epicas-historias-usuario.md) y
> [`05-plan-ejecucion.md`](../05-plan-ejecucion.md).

## 1. Ficha de la épica

| Campo | Valor |
|---|---|
| **Código** | E-CLI |
| **Objetivo(s) que cubre** | OBJ-7 — Entregar un cliente que invoque la librería |
| **Fuente de ideas** | `v2/docs/readme.md` + comandos de `v1/` |
| **Módulo de librería** | `cli/` |
| **Fase(s) del plan** | F6 (T-601..T-606) |
| **Prioridad MoSCoW** | E-CLI-1/2 → Must (MVP); E-CLI-3 (enfriamiento/retries avanzados) → Should; **API HTTP básica (T-606) → Should** (la "completa" es Could y sigue fuera de alcance) |
| **Responsable ciclo** | team analysis (BA/SA/PM) → team implementation |
| **Estado épica** | ✅ **Completada** (E-CLI-1/2/3, paridad, documentación y API HTTP hechas) |
| **DoR cumplido** | [x] sí |
| **Fecha inicio** | 2026-09-12 |
| **Fecha fin** |  |

## 2. Definition of Done de la épica (criterios de aceptación a nivel épica)

- [x] CLI `voucherflow` con subcomandos (process/validate/classify/extract/run/batch/ask/arca/case/hitl) que procesa un archivo o una carpeta recursiva.
  *(T-601: los **once** subcomandos —incluido `extract-detect` de `ORCH-CLI.md` §3— con `argparse` de la stdlib. Cubierto por `tests/test_cli_t601.py` y `scripts/F6/t601.py` 9/9.)*
- [x] Los comandos reproducen (o superan) la salida de v1 (markdown, JSON de extracción/clasificación/pipeline) respetando `--force`, `--orientation`, `--condicion-impositiva`, `--model` y `--workers`.
  *(T-601: los cinco flags se aceptan y su **efecto real** se declara; la **medición** de la paridad sobre `files/` es T-604, que es la que cierra este criterio.)*
- [x] Modo batch con workers, checkpoints/reanudación y política de enfriamiento (ADR-010): cada worker inicializa su propio convertidor de Docling y la cuenta de enfriamiento inicia cuando TODOS los workers están detenidos.
  *(T-602: `batch.py` con `ProcessPoolExecutor` + `initializer` por worker (cada uno con **su** convertidor), checkpoints `<doc>.batch.json` con el hash del contenido y la cuenta de enfriamiento que arranca con el pool detenido. Verificado con reloj inyectado: `scripts/F6/t602.py` 12/12 + 6/6.)*
- [x] Reintentos con backoff y máximo configurable ante errores transitorios (429 o conexión).
  *(El backoff del cliente de modelos es de F0/T-005; T-602 aporta el reintento **entre corridas** —un documento que falló no deja checkpoint reutilizable, así que la corrida siguiente lo reprocesa— y descarta el loop interno a propósito. Los retries **avanzados** de E-CLI-3 siguen como Should.)*
- [x] Cada resultado genera un JSON sidecar con resultado + evidencia + trazabilidad; en modo lote se puede consolidar en un único JSON agregado.
  *(Las dos mitades del Gherkin: el **sidecar** por documento es de F5/T-506 y el CLI lo expone desde T-601 (`--cases`, `case show/list`); el **único JSON agregado** es T-603 (`trace/agregado.py`, `batch -o`, `case aggregate`). Cubierto por `tests/test_agregado_t603.py` y `scripts/F6/t603.py` 8/8 + 9/9.)*
- [x] Mapa de paridad v1→v2 verificado sobre carpetas reales de `files/` (DoD de F6 en `05-plan-ejecucion.md`).
  *(T-604: el mapa se mide en tres niveles deterministas —procedencia, superficie con el **mapeo bandera por bandera** contra el parser real, y artefactos— sobre la **muestra acordada** (`tests/fixtures/golden`, versionada) y declara lo que queda fuera con dónde se mide. La corrida real con modelos es informativa: `files/` no está versionada y el veredicto no es comparable entre arquitecturas. **Corte de v1 declarado**.)*
- [x] Documentación de usuario + README v2 actualizados.
  *(T-605: la **guía del operador** en `docs/usuario/` —índice, instalación, referencia de los once subcomandos con sus códigos de salida, revisión humana y salidas— más la sección *Documentación para quien opera* en el README de v2. La cobertura se **verifica por tests** contra el contrato del CLI (`tests/test_docs_usuario_t605.py` 30 y `scripts/F6/t605.py` 27/27): ningún comando sin documentar, ninguno inventado, cada bandera en la sección de su comando y las fronteras declaradas. El `--help` sigue siendo la fuente más actualizada y la guía lo dice.)*

## 3. Historias de usuario y seguimiento

### E-CLI-1 · CLI por archivo y por carpeta (equivalente funcional a v1)
- **Estado**: [ ] Pendiente · [ ] En desarrollo · [ ] En QA · [x] Hecho
- **Responsable**: team analysis / team implementation
- **Como** operador,
  **quiero** un CLI que procese un archivo o una carpeta recursiva con las
  capacidades actuales (OCR, extracción, clasificación, pipeline completo)
  **para** reemplazar los comandos de `v1` sin perder funcionalidad.
- **Implementación (T-601)**: los once subcomandos en `voucherflow/cli/main.py`
  (delegan en `api.*`/`orchestrator.*`, sin reimplementar reglas) y
  `iterar_documentos()` para el recorrido recursivo, que **excluye los
  artefactos derivados** (`*.case.json`, `*_pipeline.json`,
  `*_classification.json`, `*.raw.md`). `main()` devuelve el código de salida y
  el dato va a `stdout` mientras el progreso/error va a `stderr`.
- **Criterios de aceptación (Gherkin):**

```gherkin
Dado un archivo imagen/pdf/md
Cuando se ejecuta el CLI con el subcomando correspondiente
Entonces se produce la salida equivalente a v1 (markdown, JSON de extracción/clasificación/pipeline)

Dado una carpeta
Cuando se ejecuta el CLI recursivo
Entonces recorre subcarpetas y procesa todos los archivos soportados
Y respeta --force, --orientation, --condicion-impositiva, --model y --workers

Regla: checkpoint/resumir
  Dado un procesamiento interrumpido
  Cuando se vuelve a ejecutar sobre la misma carpeta
  Entonces retoma desde los checkpoints sin repetir pasos completados
```

### E-CLI-2 · Salidas y sidecars con trazabilidad
- **Estado**: [ ] Pendiente · [ ] En desarrollo · [ ] En QA · [x] Hecho
- **Responsable**: team analysis / team implementation
- **Implementación (T-603)**: las dos mitades del Gherkin. El **sidecar** por
  documento (resultado + evidencia + trazabilidad, escritura atómica) es de
  F5/T-506 y el CLI lo expone con `--cases` / `case show`; el **agregado** del
  lote es `trace/agregado.py`: un único JSON con **una entrada por documento**
  (veredicto + puntero al sidecar), la síntesis del lote y las métricas. Es un
  **índice**, no una copia de los `CaseRecord` (duplicar los `CaseRecord` daría un
  archivo inmanejable y una segunda fuente de verdad que puede divergir del
  sidecar). Se acumula entre corridas y se reconstruye del histórico con
  `case aggregate`.
- **Como** contador,
  **quiero** que cada resultado incluya la evidencia y trazabilidad del caso
  **para** poder auditar la decisión.
- **Criterios de aceptación (Gherkin):**

```gherkin
Dado un documento procesado por el CLI
Cuando se guarda el resultado
Entonces se genera un JSON con resultado + evidencia + trazabilidad (sidecar)
Y en modo lote se puede consolidar en un único JSON agregado
```

### E-CLI-3 · Manejo de recursos (workers, temperatura, reintentos)
- **Estado**: [ ] Pendiente · [ ] En desarrollo · [ ] En QA · [x] Hecho
- **Responsable**: team analysis / team implementation
- **Implementación (T-602)**: `batch.py` — pool de procesos con el convertidor
  por worker (`initializer`), política de enfriamiento del ADR-010 (ola → detener
  el pool → **todos detenidos** → dormir `cool_down_s`) y checkpoints por
  documento con escritura atómica. El CLI expone `--workers`, `--cooling`,
  `--work-window` y `--cool-down`.
- **Como** operador de máquina local,
  **quiero** controlar paralelismo, pausas de enfriamiento por temperatura y
  reintentos
  **para** no degradar el dispositivo en lotes largos.
- **Criterios de aceptación (Gherkin):**

```gherkin
Dado un lote grande
Cuando se usa --workers N
Entonces cada worker inicializa su propio convertidor de Docling
Y al superar un tiempo de procesamiento continuo se detienen los workers
Y la cuenta de enfriamiento inicia cuando TODOS los workers están detenidos

Regla: reintentos con backoff
  Dado un error transitorio (429 o conexión)
  Cuando se reintenta
  Entonces se aplica backoff y un máximo de intentos configurable
```

## 4. Bitácora de seguimiento

| Fecha | Acción / hito | Responsable | Estado |
|---|---|---|---|
| 2026-09-12 | **E-CLI-1 (T-601)**: el CLI `voucherflow` existe con los once subcomandos (`process`, `validate`, `classify`, `extract`, `extract-detect`, `run`, `batch`, `ask`, `arca`, `case`, `hitl`) sobre archivo o carpeta recursiva, con los cinco flags comunes (`--force`, `--orientation`, `--condicion-impositiva`, `--model`, `--workers`) y efecto declarado. Detrás: el **orquestador** encadena F1→F5 y la **fachada** implementa `extract`/`run`/`ask` (los dos primeros eran esqueletos de F0). Suites: `tests/test_cli_t601.py` (57) y `scripts/F6/t601.py` (9/9 + 6/6). | team implementation | Hecho |
| 2026-09-12 | **E-CLI-3 (T-602)**: el modo batch corre con **workers**, **checkpoints/reanudación** y la **política de enfriamiento del ADR-010**. `batch.py` usa `ProcessPoolExecutor` con `initializer` por worker (cada proceso con **su** convertidor de Docling y **su** cliente, perezosos) y transporta el trabajo como payload serializable; los checkpoints `<doc>.batch.json` (atómicos, con el **hash** del contenido) permiten reanudar sin repetir lo completado, y `--force` reprocesa. La cuenta del enfriamiento arranca cuando el pool está detenido —no cuando termina el último documento— porque el ciclo detiene el pool antes de dormir. Suites: `tests/test_batch_t602.py` (46, con reloj inyectado) y `scripts/F6/t602.py` (12/12 + 6/6). **Hallazgo**: `concurrent.futures.Future` expone `result()`, no `resultado()`; el camino real del pool con >1 worker habría roto (lo destapó el test del pool y se adapta en la frontera). | team implementation | Hecho |
| 2026-09-12 | **E-CLI-2 (T-603)**: las dos mitades del Gherkin quedan cubiertas. El **sidecar** por documento (resultado + evidencia + trazabilidad, atómico) es de F5/T-506 y el CLI lo expone desde T-601; el **agregado** del lote es nuevo (`trace/agregado.py`): **un** JSON con una entrada por documento (veredicto + **puntero al sidecar**), la síntesis del lote y las métricas derivadas del histórico (F5/T-507). Decisión central: es un **índice**, no una copia de los `CaseRecord` — embeberlos daría un archivo de cientos de MB en lotes grandes y crearía una segunda fuente de verdad que puede divergir del sidecar. Se **acumula** entre corridas (una entrada por documento: reprocesar actualiza, no duplica) y `batch -o` lo escribe; `case aggregate` lo **reconstruye** leyendo los sidecars. Suites: `tests/test_agregado_t603.py` (41) y `scripts/F6/t603.py` (8/8 + 9/9). **Hallazgos**: el agregado perdía el veredicto cuando el resultado llegaba sin el `VoucherResult` tipado (lo marcaba `sin_estado`, afirmando que no se había resuelto cuando sí), y recalcular las métricas solo con casos nuevos borraba un cálculo previo en una corrida sin `--cases`. | team implementation | Hecho |
| 2026-09-12 | **DoD de F6 (T-604): mapa de paridad v1→v2 y corte de v1**. La paridad no se mide comparando el veredicto (v1 y v2 difieren a propósito: ADR-001/ADR-006) sino en tres niveles deterministas: **procedencia** (cada comando cita su script de v1 y existe), **superficie** (8/8 comandos con equivalente; cada bandera de v1 **mapeada** a la de v2 —verificada contra el parser real— o declarada sin equivalente con motivo) y **artefactos** (3 de 7 nombres coinciden con v1; 4 cambian con motivo). El subconjunto declara las equivalencias de capacidad, lo que queda fuera de paridad con dónde se mide y el **corte de v1** (v1 se congela como referencia). Suites: `tests/test_paridad_t604.py` (29) y `scripts/F6/t604.py`. **Hallazgos**: `process` podía **sobrescribir su entrada** con un `.md` (riesgo nuevo de v2; se aparta a `<doc>.processed.md`), y el chequeo destapó que `classify` no aceptaba `-o` (v1 sí) y que un mapeo inventado de `--modality` tapaba la brecha. | team implementation | Hecho |
| 2026-09-12 | **T-605 hecha: documentación de usuario y README de v2 (T-605)**. La guía del operador queda en `docs/usuario/` (cinco documentos: índice con los tres conceptos —estado del caso, certeza y auditabilidad—, instalación, referencia de los once subcomandos con sus códigos de salida, revisión humana y salidas) y el README de v2 suma la sección *Documentación para quien opera*. La doc se escribe contra la **fuente factual** (`--help` real, códigos de salida de `_cmd_*`, defaults de `Settings`) y se **verifica por tests** contra ella: cobertura de comandos en las **dos** direcciones (ninguno sin documentar, ninguno inventado), cada bandera en la sección de **su** comando, claves YAML contra los defaults reales, navegación sin enlaces rotos y las fronteras declaradas (el CLI no carga correcciones HITL; `--force`/`--workers` solo aplican a `batch`; `ask` no es auditable). Suites: `tests/test_docs_usuario_t605.py` (30) y `scripts/F6/t605.py` (27/27). **Hallazgos**: los tests destaparon **cuatro errores en la doc recién escrita** (tres enlaces relativos rotos y `hitl.*` en el documento equivocado), y al contar filas se descubrió que la bitácora de `F6.md` arrastraba **1759 caracteres de texto duplicado** en la fila de T-602 (mismo defecto en la de T-603 de este archivo, 1418 caracteres: ambos corregidos). Con T-605 el **DoD completo de F6 queda cumplido**. | team implementation | Hecho |
| 2026-09-12 | **T-606 hecha: API HTTP básica (fase 2) — cierra F6**. `http/server.py` (nuevo) es la **segunda superficie** del sistema, con el mismo principio que el CLI (E-LIB-1): **transporta, no reimplementa** (delega en `api.*`; un test exige que no importe los módulos de capacidad). Seis rutas —`GET /` índice, `GET /salud`, `POST /run`, `POST /extract`, `POST /ask`, `GET /version`— cada una declarando su equivalente en el CLI. **Decisión de alcance**: `http.server` de la **stdlib**, no FastAPI — el diagrama C4 la nombraba, pero no había ADR ni dependencia declarada y la regla dura es no sumar dependencias (precedente: T-601 eligió `argparse` sobre `typer`); se declara el punto de revisión. `ThreadingHTTPServer` porque las rutas bloquean en Ollama; la lógica de ruteo es **pura** (`manejar(...)`) y el handler queda con el transporte, así que todo se testea in-process. **Mapeo de errores que no aplana**: 400/404/405/**422** (el dato no sirve)/**503** (el modelo no responde: transitorio)/500, con un test que fija el orden de las ramas (las jerarquías se cruzan). Un rechazo sigue siendo **200**. **Seguridad declarada**: sin auth/TLS/CORS, default en `127.0.0.1` (publicar requiere `--host` y avisa), tope de cuerpo, nota de alcance publicada en el índice. Binario **aparte** (`voucherflow-http`), no un duodécimo subcomando: el contrato de once comandos (T-604/T-605) no se reabre. Suites: `tests/test_http_t606.py` (53) y `scripts/F6/t606.py` (6/6 + 16/16 + 9/9). **Hallazgos**: una **etiqueta del diagrama no es una decisión de arquitectura**; el **default inseguro es el que se publica sin querer**; y hubo que **testear el transporte** (servidor real, incluida la prueba de concurrencia que justifica *threading*). | team implementation | Hecho |

## 5. Referencias cruzadas

- Historias fuente: [`02-epicas-historias-usuario.md`](../02-epicas-historias-usuario.md)
- Objetivo y alcance: [`01-vision-alcance.md`](../01-vision-alcance.md) (OBJ-7)
- Fases/tareas/DoR-DoD: [`05-plan-ejecucion.md`](../05-plan-ejecucion.md) (F6: T-601..T-606)
- Calidad/golden set: [`06-estrategia-calidad.md`](../06-estrategia-calidad.md)
- Dependencias: E-LIB (librería que invoca), F1-F5 (capacidades expuestas por subcomando)

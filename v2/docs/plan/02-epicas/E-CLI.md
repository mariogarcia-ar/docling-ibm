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
| **Prioridad MoSCoW** | E-CLI-1/2 → Must (MVP); E-CLI-3 (enfriamiento/retries avanzados) → Should; API HTTP (T-606) → fase 2 / Could |
| **Responsable ciclo** | team analysis (BA/SA/PM) → team implementation |
| **Estado épica** | 🟡 En implementación (E-CLI-1 hecha en T-601; E-CLI-2/3 pendientes) |
| **DoR cumplido** | [x] sí |
| **Fecha inicio** | 2026-09-12 |
| **Fecha fin** |  |

## 2. Definition of Done de la épica (criterios de aceptación a nivel épica)

- [x] CLI `voucherflow` con subcomandos (process/validate/classify/extract/run/batch/ask/arca/case/hitl) que procesa un archivo o una carpeta recursiva.
  *(T-601: los **once** subcomandos —incluido `extract-detect` de `ORCH-CLI.md` §3— con `argparse` de la stdlib. Cubierto por `tests/test_cli_t601.py` y `scripts/F6/t601.py` 9/9.)*
- [x] Los comandos reproducen (o superan) la salida de v1 (markdown, JSON de extracción/clasificación/pipeline) respetando `--force`, `--orientation`, `--condicion-impositiva`, `--model` y `--workers`.
  *(T-601: los cinco flags se aceptan y su **efecto real** se declara; la **medición** de la paridad sobre `files/` es T-604, que es la que cierra este criterio.)*
- [ ] Modo batch con workers, checkpoints/reanudación y política de enfriamiento (ADR-010): cada worker inicializa su propio convertidor de Docling y la cuenta de enfriamiento inicia cuando TODOS los workers están detenidos.
  *(El lote secuencial y determinista está en T-601 —con el punto de extensión declarado—; el pool/checkpoints/enfriamiento es **T-602**.)*
- [ ] Reintentos con backoff y máximo configurable ante errores transitorios (429 o conexión).
  *(Existe en el cliente de modelos desde F0/T-005; falta exponer el máximo configurable por el CLI — T-605/T-602.)*
- [x] Cada resultado genera un JSON sidecar con resultado + evidencia + trazabilidad; en modo lote se puede consolidar en un único JSON agregado.
  *(T-601: `--cases DIR` persiste el `CaseRecord` de F5/T-506 —sidecar atómico + índice— y `case show/list` lo consulta; la **salida agregada** consolidada del lote es T-603.)*
- [ ] Mapa de paridad v1→v2 verificado sobre carpetas reales de `files/` (DoD de F6 en `05-plan-ejecucion.md`).
  *(El mapa de equivalencias está en el F6-subplan §3.1; la **medición** sobre `files/` es T-604.)*
- [ ] Documentación de usuario + README v2 actualizados.
  *(El `--help` de cada comando es la base factual; la guía es T-605.)*

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
- **Estado**: [ ] Pendiente · [ ] En desarrollo · [ ] En QA · [ ] Hecho
- **Responsable**: team analysis / team implementation
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
- **Estado**: [ ] Pendiente · [ ] En desarrollo · [ ] En QA · [ ] Hecho
- **Responsable**: team analysis / team implementation
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
| 2026-09-12 | **E-CLI-1 (T-601)**: el CLI `voucherflow` existe con los once subcomandos (`process`, `validate`, `classify`, `extract`, `extract-detect`, `run`, `batch`, `ask`, `arca`, `case`, `hitl`) sobre archivo o carpeta recursiva, con los cinco flags comunes (`--force`, `--orientation`, `--condicion-impositiva`, `--model`, `--workers`) y efecto declarado. Detrás: el **orquestador** encadena F1→F5 y la **fachada** implementa `extract`/`run`/`ask` (los dos primeros eran esqueletos de F0). Suites: `tests/test_cli_t601.py` (57) y `scripts/F6/t601.py` (9/9 + 6/6). E-CLI-2 (sidecars/agregado, T-603) y E-CLI-3 (workers/enfriamiento, T-602) siguen pendientes. | team implementation | Hecho |

## 5. Referencias cruzadas

- Historias fuente: [`02-epicas-historias-usuario.md`](../02-epicas-historias-usuario.md)
- Objetivo y alcance: [`01-vision-alcance.md`](../01-vision-alcance.md) (OBJ-7)
- Fases/tareas/DoR-DoD: [`05-plan-ejecucion.md`](../05-plan-ejecucion.md) (F6: T-601..T-606)
- Calidad/golden set: [`06-estrategia-calidad.md`](../06-estrategia-calidad.md)
- Dependencias: E-LIB (librería que invoca), F1-F5 (capacidades expuestas por subcomando)

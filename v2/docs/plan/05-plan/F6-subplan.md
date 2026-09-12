# F6-subplan — Subplan de implementación F6 (Cliente CLI/batch e integración final)

> Documento de trabajo para la implementación de la **Fase F6** por
> `team implementation`. Complementa el seguimiento de la fase
> ([`F6.md`](F6.md)) y el diseño del módulo
> ([`../03-arquitectura/ORCH-CLI.md`](../03-arquitectura/ORCH-CLI.md)).
> **Fecha**: 2026-09-12 · **Rama**: `v2` · **Estado**: 🟡 **En implementación**
> (T-601 hecha; T-602..T-606 pendientes).

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

### 3.2 T-602 · Modo batch con workers, checkpoints y enfriamiento (ADR-010) ⬜ Pendiente

**Qué hay que hacer** (lo que T-601 dejó preparado): el pool de
`ProcessPoolExecutor` con **el convertidor de Docling inicializado por worker**
(patrón `init_worker`/`process_image_worker` de `v1/full_pipeline.py`), los
checkpoints por documento con escritura atómica (`CaseRecorder`/`write_results`)
y la política de enfriamiento del ADR-010: tras `work_window_s` de trabajo
continuo se detienen los workers y **la cuenta de `cool_down_s` arranca cuando
TODOS están detenidos** (requisito explícito de `my_prompt.md`).

**Lo que T-601 ya dejó listo**: `ejecutar_lote(max_workers=...)` con el
descubrimiento determinista y el contrato de cada corrida; el detalle declara
`solicitado` vs. `aplicado` para que el cambio de T-602 sea verificable en la
traza y no silencioso. La configuración `CoolingSettings`
(`enabled`/`work_window_s`/`cool_down_s`) ya existe en `settings/config.py`.

### 3.3 T-603 · Sidecars con trazabilidad y salida agregada ⬜ Pendiente

**Qué hay que hacer**: la política de sidecars por caso (resultado + evidencia +
trazabilidad) y la salida agregada del lote en un único JSON. T-601 ya expone el
`CaseRecord` de cada corrida y persiste el de T-506 con `--cases`; lo que falta es
la **consolidación** (qué campos van al agregado, cómo se resume el lote, dónde
vive) y su contrato.

### 3.4 T-604 · Mapa de paridad v1→v2 sobre carpetas reales de `files/` ⬜ Pendiente

**Qué hay que hacer**: correr los comandos equivalentes de v1 y v2 sobre una
**muestra acordada** de `files/` y declarar el corte de v1 (DoD de F6). El mapa de
T-601 (§3.1) es el punto de partida; la medición sigue la convención de F3/T-305,
F4/T-405 y F5/T-507: subconjunto del golden en `tests/golden/F6/` + `README.md`,
scripts `paridad_*.py` con funciones puras y un reporte que **declara todo lo que
queda fuera** (así "no comparable" no se lee como "no implementado").

### 3.5 T-605 · Documentación de usuario + README v2 ⬜ Pendiente

**Qué hay que hacer**: la guía del operador (instalación, cada subcomando con su
flag, los códigos de salida, qué hacer cuando un caso queda en revisión) y el
`README.md` de v2 actualizado. T-601 deja el `--help` de cada comando como la
base factual de esa documentación.

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

- **Estado (2026-09-12)**: **T-601 hecha**. El cliente existe: los once
  subcomandos, el orquestador que encadena F1→F5 y la fachada que publica
  `extract`/`run`/`ask`. Suite completa **1460 passed / 10 skipped** (57 nuevos);
  `scripts/F6/t601.py` → **9/9** escenarios + **6/6** fronteras (exit 0).
- **Punto de partida real**: F5 dejó el `CaseRecord` persistible (`CaseRecorder`)
  y el `VoucherResult` consolidado; lo único que faltaba era **el encadenamiento**
  y **la superficie de invocación**. Con T-601 el pipeline de extremo a extremo
  existe y es determinista sin GPU.
- **Lo que sigue**: T-602 (batch con workers/checkpoints/enfriamiento) es la
  continuación natural porque T-601 dejó su punto de extensión declarado
  (`ejecutar_lote` + `CoolingSettings`); T-603 cierra la salida agregada y T-604
  mide la paridad para declarar el corte de v1 (el DoD de la fase).

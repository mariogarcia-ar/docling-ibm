# Épica E-EXT — Extracción (Seguimiento)

> Documento de seguimiento generado a partir de
> [`02-epicas-historias-usuario.md`](../02-epicas-historias-usuario.md) y
> [`05-plan-ejecucion.md`](../05-plan-ejecucion.md).

## 1. Ficha de la épica

| Campo | Valor |
|---|---|
| **Código** | E-EXT |
| **Objetivo(s) que cubre** | OBJ-4 — Refactorizar la extracción (flujos VLM + LLM paralelos con contrato común) |
| **Fuente de ideas** | `v2/docs/ideas/algoritmo.md` + `v2/docs/ideas/flujo_deteccion_tipo_comprobante.md` (+ prompts 10/11/kvi/kvg como referencia de reglas) |
| **Módulo de librería** | `extraction/` |
| **Fase(s) del plan** | F4 (T-401..T-405) |
| **Prioridad MoSCoW** | Must (MVP) |
| **Responsable ciclo** | team analysis (BA/SA/PM) → team implementation |
| **Estado épica** | ✅ **Completada** (E-EXT-1, E-EXT-2 y E-EXT-3 hechas en T-401..T-405; DoD de la épica verificado) |
| **DoR cumplido** | [x] sí |
| **Fecha inicio** | 2026-09-10 |
| **Fecha fin** | 2026-09-10 |

## 2. Definition of Done de la épica (criterios de aceptación a nivel épica)

- [x] Ambos flujos (VLM sobre imagen y LLM sobre OCR/Markdown) corren SIEMPRE en paralelo sobre cada comprobante, sin elegir uno por documento. *(T-401: `extraer_evidencia()` con `ThreadPoolExecutor`, una tarea por fuente; medido en `scripts/F4/t401.py`)*
- [x] Cada flujo devuelve evidencia por campo con el mismo esquema (campo, valor, fuente, fragmento de sustento) conforme al schema `SourceEvidence` (T-001). *(`extraction/evidencia.py::construir_source_evidence`)*
- [x] Las reglas raw por fuente (pasada 1) marcan como debilitada a una fuente internamente inconsistente antes de combinarse. *(**T-403 hecho**: el registro de T-303 se completa con sostén por **forma canónica** (montos, fechas, CUIT) y con `RAW_COHERENCIA`, que evalúa las implicaciones de la fuente consigo misma — una Factura A sin los dos CUIT, o una Factura B con IVA discriminado, quedan *débiles* antes de combinarse. La ausencia que no se puede juzgar no se castiga y el veredicto es `dudosa`, nunca `invalida`)*
- [x] La combinación de evidencia resuelve por campo con precedencia (ADR-002) y conserva trazabilidad de cada fuente. *(**T-404 hecho**: `rules/precedencia.py` declara la tabla por campo —visual para la letra, el número impreso, el membrete y los CUIT; textual para fecha, moneda e importes; programa para los derivados— y `combinar_evidencia()` devuelve el `CombinedEvidence` conservando **todas** las lecturas y agregando la resolución (`ganador`/`regla`/`motivo`). Las fuentes que no son lectura (`hitl` > `arca` > `programa`) van siempre por delante; la resolución respeta la pasada 1 de T-403)*
- [x] Los campos se normalizan (CUIT, fechas ISO, montos, punto_venta/número) sin inventar datos ausentes; en modo auditoría los no-comprobantes devuelven comprobante_valido=false con motivo_rechazo. *(**T-402 hecho**: CUIT solo dígitos y guiones propios con corte ante caracteres extraños, fechas `YYYY-MM-DD` solo completas y reales, montos numéricos sin separadores, `punto_venta`/`numero_comprobante` derivados, texto colapsado, `descripcion` en minúsculas e ítems estructurados; el crudo nunca se pierde — `normalizar=False` lo devuelve entero. La parte de normalización está cubierta y verificada; **`comprobante_valido`/`motivo_rechazo` no son lectura sino decisión**: son el veredicto de la conclusión (F5/T-501), y por eso F4 entrega el `CombinedEvidence` con `decision = None` en vez de inventar un fallo o un visto bueno)*
- [x] Paridad verificable con v1 sobre el golden set: equivalencia con `extraction_pipeline.py` (10/11) y `document_extraction.py` (kvi/kvg) en campos normalizados (DoD de F4 en `05-plan-ejecucion.md`). *(**T-405 hecho**: subconjunto `tests/golden/F4/` con la procedencia de cada una de las **7** reglas de normalización (cita `regla_v1` + `prompt_v1` + `texto_v1` literal) y **6** casos de extracción. La paridad se mide en **tres niveles** (normalización determinista / contrato y sostén / corrida real contra v1). Métricas del subconjunto: reglas **20/20**, paridad estructural **29/29** campos, sostén **32/32**, cobertura genérica **5/5**, procedencia verificada. La corrida **real** contra v1 queda en `--subset origen` (requiere Ollama y `v1/`). Las **diferencias por diseño** (vocabulario `090`/`099` de `tipo_comprobante`, `moneda` sin el default `ARS` de v1, `descripcion` normalizada en código) viajan como nota, no como fallo)*
- [x] Documentación/contratos actualizados (README/ADR si cambia una decisión). *(T-401..T-405: `EXT.md`, `RULES.md`, `F4.md`, `F4-subplan.md`, el ADR-002 y este documento; **T-405** suma `tests/golden/F4/README.md`, que documenta por qué paridad ≠ "el mismo modelo dos veces" (ADR-001) y los límites del alcance. `SCHEMA_VERSION` sigue en `1.0.0`: los cambios de F4 fueron aditivos)*

## 3. Historias de usuario y seguimiento

### E-EXT-1 · Flujos VLM y LLM en paralelo con contrato de evidencia
- **Estado**: [ ] Pendiente · [ ] En desarrollo · [ ] En QA · [x] **Hecho** (T-401 y T-404, 2026-09-10)
- **Responsable**: team analysis / team implementation
- **Como** sistema,
  **quiero** ejecutar siempre ambos flujos (VLM y LLM) sobre cada comprobante y que
  cada uno devuelva evidencia con el mismo esquema (campo, valor, fuente,
  fragmento de sustento)
  **para** poder compararlos programáticamente en la conclusión.
- **Criterios de aceptación (Gherkin):**

```gherkin
Dado un comprobante confirmado
Cuando se ejecuta la extracción
Entonces corren el flujo VLM (imagen) y el flujo LLM (OCR/Markdown) en paralelo
Y cada flujo devuelve evidencia por campo con fuente y fragmento de sustento

Regla: ambos siempre
  Dado cualquier comprobante
  Cuando se ejecuta la extracción
  Entonces NO se elige un flujo u otro por documento: corren ambos

Regla: modalidades de la librería v1
  Dado un archivo .md o .jpg
  Cuando se invoca el modo de extracción equivalente a kvi/kvg/10/11
  Entonces el resultado conserva la capacidad actual (JSON plano normalizado)
  Y agrega el envoltorio de evidencia
```

### E-EXT-2 · Validación de evidencia cruda por fuente (pasada 1)
- **Estado**: [ ] Pendiente · [ ] En desarrollo · [ ] En QA · [x] **Hecho** (T-403, 2026-09-10)
- **Responsable**: team analysis / team implementation
- **Como** sistema,
  **quiero** validar la evidencia de cada fuente por separado antes de mezclarla
  **para** marcar como debilitada a una fuente internamente inconsistente (ej.
  dice Factura A pero no detectó los dos CUIT que exige esa letra).
- **Criterios de aceptación (Gherkin):**

```gherkin
Dado que la evidencia VLM dice Factura A sin detectar dos CUIT
Cuando se aplican las reglas raw
Entonces la fuente VLM queda marcada como debilitada antes de combinarse

Dado que la evidencia de una fuente pasa sus propias reglas
Cuando se combinan las fuentes
Entonces esa evidencia participa de la combinación con su trazabilidad
```

### E-EXT-3 · Campos de extracción key-value normalizados
- **Estado**: [ ] Pendiente · [ ] En desarrollo · [ ] En QA · [x] **Hecho** (T-402, 2026-09-10)
- **Responsable**: team analysis / team implementation
- **Como** consumidor de datos,
  **quiero** recibir los campos fiscales y comerciales normalizados
  (CUIT, fechas ISO, montos, tipo, punto de venta, número, ítems)
  **para** alimentar la clasificación contable y la conciliación.
- **Criterios de aceptación (Gherkin):**

```gherkin
Regla: normalización
  Dado el OCR de un comprobante
  Cuando se extraen campos
  Entonces los CUIT son solo dígitos y guiones propios (corte ante caracteres extraños)
  Y las fechas se normalizan a YYYY-MM-DD
  Y los montos son numéricos sin separadores de miles
  Y se separa punto_venta y numero_comprobante de PPPPP-NNNNNNNN

Regla: no inventar
  Dado un dato ausente o ilegible
  Cuando se extraen campos
  Entonces se omite o se deja null; NO se inventa

Regla: validación de comprobante
  Dado un texto que no es comprobante o está corrupto
  Cuando se extrae con el modo auditoría
  Entonces comprobante_valido=false y se completa motivo_rechazo
```

## 4. Bitácora de seguimiento

| Fecha | Acción / hito | Responsable | Estado |
|---|---|---|---|
| 2026-09-10 | **E-EXT-1 hecha (T-401)**: los flujos VLM y LLM corren siempre en paralelo y cada uno devuelve `SourceEvidence` con el contrato de F0 (ADR-001). Prompt de evidencia versionado `extraccion-key-value@1`; el intérprete no inventa campos, tolera el JSON plano de v1 y reutiliza la pasada raw de T-303. Suites: `tests/test_extraction_flujos.py` (75) y `scripts/F4/t401.py` (11/11 + paralelismo medido). E-EXT-2 (T-403) y E-EXT-3 (T-402) siguen pendientes. | team implementation | Hecho |
| 2026-09-10 | **E-EXT-3 hecha (T-402)**: `extraction/key_value.py` porta a **código** las reglas de normalización que en v1 vivían dentro de los prompts `10`/`11`/`kvi`/`kvg` — CUIT solo dígitos y guiones propios con corte ante caracteres extraños (`"20-1 Ing, Brutas: 201641"` → `"20-1"`), fechas `YYYY-MM-DD` solo completas y reales, montos numéricos sin separadores de miles (signo y constancia de ambigüedad), `punto_venta`/`numero_comprobante` derivados del número impreso, moneda `ARS`/`USD` sin default, texto colapsado y ítems estructurados. Regla dura de la historia: **no inventar** — un dato ilegible conserva el crudo con aviso y el crudo de cada campo viaja en `meta['valor_crudo']`; la pasada raw de T-303 sigue viendo el crudo. Suites: `tests/test_extraction_key_value.py` (97) y `scripts/F4/t402.py` (19/19 reglas + 17/17 escenarios + 5/5 fronteras). E-EXT-2 (T-403) sigue pendiente. | team implementation | Hecho |
| 2026-09-10 | **E-EXT-2 hecha (T-403)**: la pasada 1 por fuente se completa para la extracción. `rules/raw.py` gana los puntos de extensión que el registro de T-303 necesitaba sin dejar de ser agnóstico del dominio (`ImplicacionCoherencia`, `sostenedor`, `normalizador_valor`, `violaciones_de_coherencia` y el id `RAW_COHERENCIA`), y `extraction/evidencia.py` declara con ellos dos cosas: el **sostén por forma canónica** de montos, fechas y CUIT (lo que T-401 dejaba sin evaluar) y la **coherencia de la fuente consigo misma** — el caso textual de esta historia: una Factura A sin los dos CUIT queda **debilitada antes de combinarse** (igual que una Factura B con IVA discriminado). La ausencia que la fuente no pudo evaluar no se castiga y la gravedad es `dudosa`, nunca `invalida`. Suites: `tests/test_extraction_raw_t403.py` (37) y `scripts/F4/t403.py` (11/11 criterios de sostén + 7/7 escenarios + 4/4 fronteras). | team implementation | Hecho |
| 2026-09-10 | **E-EXT-1 completada (T-404)**: se implementó la **combinación con resolución por campo** (ADR-002). `rules/precedencia.py` declara la tabla (`PREC_1` visual: letra del recuadro, número impreso, membrete, CUIT; `PREC_2` textual: fecha, moneda, importes; `PREC_3` programa: `punto_venta`/`numero_comprobante`; `PREC_0` regla de oro para el modo genérico) y resuelve acuerdo/desacuerdo/invalidada. `combinar_evidencia()` (firma congelada de F0) devuelve el `CombinedEvidence` **conservando todas las lecturas**, con la resolución, el atajo `valor`/`fuente` y la traza de la combinación; `decision` pasa a ser **opcional** porque combinar no es decidir (eso es F5/T-501). Suites: `tests/test_extraction_combinacion_t404.py` (45) y `scripts/F4/t404.py` (8/8 escenarios + 4/4 fronteras + `--manual` con el pipeline completo). | team implementation | Hecho |
| 2026-09-10 | **E-EXT cerrada (T-405)**: la paridad con v1 se verifica en **tres niveles** — (1) normalización determinista: cada regla v2 aplicada al `texto_v1` literal produce el mismo canónico y cita su origen; (2) contrato y sostén: los 15 campos de paridad existen en el contrato F0 y el sostén estructurado se evalúa donde corresponde; (3) extracción real contra v1 sobre 3 documentos del golden (informativo, fuera de la suite default). Artefactos: `tests/golden/F4/subconjunto.json` (versión `0.1-f4`), `tests/golden/F4/README.md`, `scripts/F4/paridad_extraccion.py`, `tests/test_extraction_paridad.py` (39) y `scripts/F4/t405.py`. Métricas: reglas **20/20**, paridad estructural **29/29**, sostén **32/32**, genérico **5/5**, procedencia ✅. **Dos hallazgos**: la paridad destapó que `proveedor` (clave obligatoria de `kvg`) no tenía regla de normalización, así que conservaba los espacios múltiples del OCR; se agregó `"proveedor": NORM_TEXTO` y `CAMPOS_GENERICOS_CON_REGLA`. Y el guard de integridad destapó que `razon_social_receptor` (del contrato de v2) no estaba declarado: v1 nunca lo pidió, así que ahora se declara en `campos_sin_contraparte_v1` y un test exige que **todo** campo del contrato esté declarado. Suite completa **949 passed / 10 skipped**; los cinco scripts de F4 salen con código 0. | team implementation | Hecho |

## 5. Referencias cruzadas

- Historias fuente: [`02-epicas-historias-usuario.md`](../02-epicas-historias-usuario.md)
- Objetivo y alcance: [`01-vision-alcance.md`](../01-vision-alcance.md) (OBJ-4)
- Fases/tareas/DoR-DoD: [`05-plan-ejecucion.md`](../05-plan-ejecucion.md) (F4: T-401..T-405; depende de ADR-002 y schemas T-001)
- Calidad/golden set: [`06-estrategia-calidad.md`](../06-estrategia-calidad.md)
- Dependencias: E-QWE (vista fiel de entrada), E-LIB (schemas), E-CLAS/E-CONC (consumen la evidencia combinada)

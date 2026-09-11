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
| **Estado épica** | 🟡 En implementación (E-EXT-1, E-EXT-2 y E-EXT-3 hechas en T-401/T-402/T-403) |
| **DoR cumplido** | [x] sí |
| **Fecha inicio** | 2026-09-10 |
| **Fecha fin** |  |

## 2. Definition of Done de la épica (criterios de aceptación a nivel épica)

- [x] Ambos flujos (VLM sobre imagen y LLM sobre OCR/Markdown) corren SIEMPRE en paralelo sobre cada comprobante, sin elegir uno por documento. *(T-401: `extraer_evidencia()` con `ThreadPoolExecutor`, una tarea por fuente; medido en `scripts/F4/t401.py`)*
- [x] Cada flujo devuelve evidencia por campo con el mismo esquema (campo, valor, fuente, fragmento de sustento) conforme al schema `SourceEvidence` (T-001). *(`extraction/evidencia.py::construir_source_evidence`)*
- [x] Las reglas raw por fuente (pasada 1) marcan como debilitada a una fuente internamente inconsistente antes de combinarse. *(**T-403 hecho**: el registro de T-303 se completa con sostén por **forma canónica** (montos, fechas, CUIT) y con `RAW_COHERENCIA`, que evalúa las implicaciones de la fuente consigo misma — una Factura A sin los dos CUIT, o una Factura B con IVA discriminado, quedan *débiles* antes de combinarse. La ausencia que no se puede juzgar no se castiga y el veredicto es `dudosa`, nunca `invalida`)*
- [ ] La combinación de evidencia resuelve por campo con precedencia (ADR-002) y conserva trazabilidad de cada fuente. *(T-401 conserva **ambas** evidencias sin colapsar; la resolución es T-404)*
- [ ] Los campos se normalizan (CUIT, fechas ISO, montos, punto_venta/número) sin inventar datos ausentes; en modo auditoría los no-comprobantes devuelven comprobante_valido=false con motivo_rechazo. *(**T-402 hecho**: CUIT solo dígitos y guiones propios con corte ante caracteres extraños, fechas `YYYY-MM-DD` solo completas y reales, montos numéricos sin separadores, `punto_venta`/`numero_comprobante` derivados, texto colapsado, `descripcion` en minúsculas e ítems estructurados; el crudo nunca se pierde — `normalizar=False` lo devuelve entero. Falta `comprobante_valido`/`motivo_rechazo`, que son decisión de F5, no lectura)*
- [ ] Paridad verificable con v1 sobre el golden set: equivalencia con `extraction_pipeline.py` (10/11) y `document_extraction.py` (kvi/kvg) en campos normalizados (DoD de F4 en `05-plan-ejecucion.md`). *(T-405)*
- [ ] Documentación/contratos actualizados (README/ADR si cambia una decisión). *(parcial: T-401 actualizó `EXT.md`, `F4.md` y `F4-subplan.md`)*

## 3. Historias de usuario y seguimiento

### E-EXT-1 · Flujos VLM y LLM en paralelo con contrato de evidencia
- **Estado**: [ ] Pendiente · [ ] En desarrollo · [ ] En QA · [x] **Hecho** (T-401, 2026-09-10)
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

## 5. Referencias cruzadas

- Historias fuente: [`02-epicas-historias-usuario.md`](../02-epicas-historias-usuario.md)
- Objetivo y alcance: [`01-vision-alcance.md`](../01-vision-alcance.md) (OBJ-4)
- Fases/tareas/DoR-DoD: [`05-plan-ejecucion.md`](../05-plan-ejecucion.md) (F4: T-401..T-405; depende de ADR-002 y schemas T-001)
- Calidad/golden set: [`06-estrategia-calidad.md`](../06-estrategia-calidad.md)
- Dependencias: E-QWE (vista fiel de entrada), E-LIB (schemas), E-CLAS/E-CONC (consumen la evidencia combinada)

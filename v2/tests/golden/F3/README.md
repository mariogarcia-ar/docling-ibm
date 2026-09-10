# Subconjunto de paridad F3 (T-305) — clasificación

> **Fase**: F3 (clasificación) · **Tarea**: T-305 · **Épicas**: E-CLAS-1 (tipo/letra)
> y E-CLAS-2 (cadena contable).
>
> Este subconjunto existe para poder medir el **DoD de F3** ("la cadena contable
> reproduce v1") sin esperar la curación masiva del golden con contador
> (decisión de alcance F3-subplan §2.9).

## Por qué un subconjunto y no el golden completo

La columna `letra` de `../casos.csv` sigue en `pendiente` porque etiquetarla
requiere **criterio de contador** (F2 §2.5 / `../README.md`). F3 no puede esperar
esa curación, así que —igual que hizo F2 con el `veredicto`— acota el alcance a
lo que tiene **sustento objetivo**:

1. **Casos reales con evidencia objetiva de letra**: el `veredicto` ya está
   etiquetado por OCR/texto nativo en `evidencia_veredicto`, y esa misma
   evidencia **nombra la letra** (`"FACTURA A"`, `"TIQUE FACTURA A"`). Son los
   casos utilizables para medir paridad de letra sin criterio de contador.
2. **Casos contables sintéticos deterministas**: la cadena 01→02→03 se mide
   contra v1 sobre un markdown de texto **conocido**, para que la comparación no
   dependa del OCR (que es de F1/F2 y ya tiene su propia paridad).

## Casos del subconjunto

| Grupo | Caso | Qué mide | Evidencia / por qué |
|---|---|---|---|
| Letra (real) | `img_2025-08_2D2C9343` | paridad de **letra** con `-M 11.1` | OCR: "TIQUE FACTURA A" → letra sustentada |
| Letra (real) | `pdf_2026-02_6D03019B` | paridad de **letra** con `-M 11.1` | Texto nativo: "FACTURA A", CAE |
| Letra (real) | `img_2026-08_1CDFCDA0` | paridad de **letra** con `-M 11.1` | Texto nativo: "FACTURA A", CAE, Total |
| Letra (sintético) | `casos/letra_a.md`, `letra_b.md`, `letra_c.md` | reglas R1-R7 y extractores R4/R5/R6 | texto determinista; cubre la letra **por regla** sin OCR |
| Contable | `casos/contable_repuestos.md`, `contable_servicios.md` | paridad de la **cadena 01→02→03** con `classification_pipeline.py` | markdown conocido; compara `centro_costo`/`macro_categoria`/`concepto`/`codigo` |

Los casos de letra **sintéticos** cubren lo que el golden no puede sin contador:
la letra decidida **por reglas** (R1/R2A/R2B/R3) sobre condiciones fiscales
conocidas. No reemplazan la curación del golden: la complementan con el tramo
determinista del motor (que es, justamente, el que ADR-006 sacó del prompt).

## Cómo se mide (herramientas)

### (a) Paridad de la cadena contable → `python scripts/F3/paridad_contable.py`

Corre la cadena v2 (`voucherflow.classification.contable`) y la de v1
(`v1/classification_pipeline.py`) sobre los mismos markdown con el **mismo
modelo**, y compara `centro_costo` / `macro_categoria` / `concepto` / `codigo`
por caso. Reporta la coincidencia por campo.

> **Nota de entorno** (importante para interpretar el resultado): v1 resuelve sus
> prompts como `v1/prompts/` (`PROMPTS_DIR = Path(__file__).parent / "prompts"`),
> pero esa carpeta está **vacía/no versionada**; los YAML viven en `prompts/` en
> la raíz. El script reproduce en un **directorio temporal** la disposición que
> v1 espera (copia de `v1/` + los YAML de `prompts/`) y corre v1 desde ahí, sin
> mutar el repo. Sin ese ajuste, v1 falla con `FileNotFoundError` en el paso 01.

### (b) Paridad de tipo/letra → `python scripts/F3/paridad_11_1.py`

Corre el modo `-M 11.1` de v1 y el flujo de evidencia de v2
(`clasificar_tipo_comprobante` con la evidencia de texto, T-302/T-303) sobre los
mismos documentos, y compara la **letra**.

> **Nota metodológica**: el `11.1` de v1 recibía en su prompt las condiciones
> fiscales, así que para los casos **sintéticos** la comparación es
> *apples-to-apples* (el script le pasa al contexto de v2 las mismas condiciones
> que el markdown declara). Para los casos **reales** del golden nadie etiquetó
> la condición fiscal todavía (requiere contador), así que ambos lados corren
> **sin** ella: la comparación mide el acuerdo del **extractor de letra** y se
> reporta como tal, no como exactitud de la letra final.

### (c) Verificación determinista (sin red) → `tests/test_classification_paridad.py`

Los scripts de (a) y (b) requieren Ollama y v1; la parte que **no** necesita red
vive en el test: la **fidelidad de los prompts portados** (los `system`/`user` de
v2 deben ser idénticos a los YAML de `prompts/`) y la **coincidencia del
etiquetado** del subconjunto. Esa parte corre en la suite default.

### (d) Reporte agregado del DoD → `python scripts/F3/t305.py`

Es el **reporte de cierre** de la fase (subplan §10). Agrupa las métricas en dos
niveles:

- `--subset sinteticos` (**default**): solo el tramo determinista (exactitud de
  letra por categoría, % de alerta R7, cruce negocio-vs-documento y el default
  CC0006). **Sin Ollama, sin Docling y sin v1**: corre en cualquier entorno y
  sale con código ≠ 0 si alguna métrica no llega al umbral.
- `--subset golden` (o `todos`): **además** delega en `paridad_contable.py` y
  `paridad_11_1.py` para la paridad real (requiere Ollama local y `v1/`).

El reporte declara explícitamente su alcance: el subconjunto **no** es el golden
completo (la curación con contador sigue pendiente) y la exactitud del tramo
determinista debe ser 100% (la letra la decide el motor, no el modelo), mientras
que la paridad con v1 se informa como *paridad **o mejora*** con las dos
exactitudes a la vista.

## Métricas reportadas (DoD de F3)

| Métrica | Fuente | Objetivo |
|---|---|---|
| Paridad de la cadena contable | `paridad_contable.py` | coincidencia de `codigo`/`concepto` por caso (paridad **o mejora** documentada) |
| Acuerdo de letra con `-M 11.1` | `paridad_11_1.py` | acuerdo alto sobre el subconjunto |
| Exactitud de letra — v2 | `paridad_11_1.py` (casos etiquetados) | 100% sobre el tramo determinista (reglas R1-R7) |
| Exactitud de letra — v1 | `paridad_11_1.py` (mismos casos) | referencia para leer un desacuerdo como mejora o regresión |
| Default `CC0006` (Gherkin de E-CLAS-2) | `tests/test_classification_contable.py` | 100% (sin señal específica → CC0006, confianza baja, `senal_usada=ninguna`) |
| % de alerta R7 correctamente disparada | `test_classification_paridad.py` | 100% sobre el tramo determinista |
| % de acuerdo negocio-vs-documento | `test_classification_paridad.py` | 100% sobre el tramo determinista |

**Honestidad del alcance** (F3-subplan §3.5): si la paridad exacta con v1 no se
alcanza, el criterio del DoD es **paridad o mejora** documentada caso por caso
con la diferencia explicada — no un verde artificial. Las diferencias esperables
(por ejemplo, que v1 acepte un `codigo_final` que no es un código AFIP) se
registran en el reporte de cada script.

## Bug encontrado por esta verificación (T-305)

La corrida real de paridad **encontró un defecto** en el motor que la suite
determinista no podía ver, porque necesitaba documentos reales:

- **Síntoma**: sobre dos PDFs del golden cuyo encabezado dice `FACTURA A`, el
  extractor R5 devolvía la letra **`C`**.
- **Causa**: el patrón de R5 se había portado **literal** del prompt WIP
  (`FACTURA\s+([A-CME])`). El `\s+` se come el **salto de línea** que Docling
  deja en el markdown, así que la letra se capturaba de la línea siguiente:
  `"FACTURA\n  Código: 1"` → `C` (el `C` de "**C**ódigo").
- **Por qué no se detectó antes**: en v1 ese patrón nunca se ejecutó como
  código — vivía en el prompt WIP como `criterio` **descriptivo** para el
  modelo. Recién al portarlo a un motor determinístico (T-301) y ejercitarlo con
  documentos reales (T-305) se manifestó.
- **Corrección**: `\s+` → `[ \t]+` (espacios y tabulaciones, nunca un salto de
  línea) y `\b` después de la letra (para no tomar la inicial de la palabra
  siguiente). La semántica del `criterio` ("letra junto a FACTURA") se conserva.
- **Cobertura**: `tests/test_classification_paridad.py`
  (`TestRegresionR5SaltoDeLinea`), que fija el comportamiento correcto y falla si
  el patrón vuelve a usar `\s+`.

Es el tipo de hallazgo que justifica la tarea: la paridad se mide con documentos
y modelos **reales**, no solo con dobles.

## Estado de etiquetas

| Etiqueta | Estado | Nota |
|---|---|---|
| `veredicto` (F2) | etiquetada con evidencia objetiva | ver `../README.md` §"Etiquetado del `veredicto`" |
| `letra` (casos reales) | **derivada** de la evidencia objetiva, sin contador | se usa para paridad, no como verdad de negocio |
| `condicion_fiscal` | **pendiente** (contador) | por eso la paridad de letra real se mide sin ella |
| `letra` (casos sintéticos) | conocida por construcción | es el tramo determinista del motor |
| Clasificación contable | **pendiente** (contador) | la paridad con v1 es lo medible hoy |

## Cómo se amplía

1. Agregar el caso real al subconjunto solo si su `evidencia_veredicto` **nombra
   la letra** (si no, no hay sustento objetivo y va a curación con contador).
2. Para casos contables, sumar un markdown en `casos/` con texto conocido y
   correr `paridad_contable.py`.
3. Cuando el contador cure `letra`/`condicion_fiscal` del golden completo, este
   subconjunto se puede retirar (o quedar como caso de regresión acotado).

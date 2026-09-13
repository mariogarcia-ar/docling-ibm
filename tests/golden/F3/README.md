# Subconjunto de clasificación F3 (T-305) — tipo/letra + cadena contable

> **Fase**: F3 (clasificación) · **Tarea**: T-305 · **Épicas**: E-CLAS-1 (tipo/letra)
> y E-CLAS-2 (cadena contable).
>
> Este subconjunto existe para poder medir el **DoD de F3** sin esperar la
> curación masiva del golden con contador (decisión de alcance F3-subplan §2.9).

## Por qué un subconjunto y no el golden completo

La columna `letra` de `../casos.csv` sigue en `pendiente` porque etiquetarla
requiere **criterio de contador** (F2 §2.5 / `../README.md`). F3 no puede esperar
esa curación, así que —igual que hizo F2 con el `veredicto`— acota el alcance a
lo que tiene **sustento objetivo**:

1. **Casos reales con evidencia objetiva de letra**: el `veredicto` ya está
   etiquetado por OCR/texto nativo en `evidencia_veredicto`, y esa misma
   evidencia **nombra la letra** (`"FACTURA A"`, `"TIQUE FACTURA A"`).
2. **Casos sintéticos deterministas**: la letra y la cadena contable se miden
   sobre un markdown de texto **conocido**, para que la medición no dependa del
   OCR (que es de F1/F2 y ya tiene su propia cobertura).

## Casos del subconjunto

| Grupo | Caso | Qué mide | Evidencia / por qué |
|---|---|---|---|
| Letra (real) | `img_2025-08_2D2C9343` | letra sustentada por OCR | OCR: "TIQUE FACTURA A" |
| Letra (real) | `pdf_2026-02_6D03019B` | letra sustentada por texto nativo | Texto nativo: "FACTURA A", CAE |
| Letra (real) | `img_2026-08_1CDFCDA0` | letra sustentada por texto nativo | Texto nativo: "FACTURA A", CAE, Total |
| Letra (sintético) | `casos/letra_*.md`, `casos/conflicto_r7.md` | reglas R1-R7 y extractores R4/R5/R6 | texto determinista; cubre la letra **por regla** sin OCR |
| Contable (sintético) | `casos/contable_repuestos.md`, `casos/contable_servicios.md` | cadena 01→02→03 | markdown conocido; los pasos se resuelven por contrato |

Los casos de letra **sintéticos** cubren lo que el golden no puede sin contador:
la letra decidida **por reglas** (R1/R2A/R2B/R3) sobre condiciones fiscales
conocidas. No reemplazan la curación del golden: la complementan con el tramo
determinista del motor (que es, justamente, el que ADR-006 sacó del prompt).

## Cómo se mide

### (a) Fidelidad de los prompts → `tests/test_classification_paridad.py`

Los prompts de la cadena contable (`01..03`) viven **versionados en código**
(`classification/prompts_contable.py`) y tienen su copia de referencia en los
YAML de `prompts/`. El test exige que el `system`/`user` de código sea
**idéntico** al YAML: la cadena depende de que el texto sea el mismo, así que un
retoque obliga a justificar el cambio y a actualizar la referencia.

El `11.1` de detección de tipo conserva además la **guía de lectura** del YAML
(el recuadro, `COD. 01`, el encabezado): la evidencia que llega al motor es la
que el sistema usa para decidir.

### (b) Exactitud determinista de la letra → `tests/test_classification_paridad.py`

Sobre los casos **sintéticos** del subconjunto: el motor debe decidir la letra y
disparar la regla esperadas (R1/R2A/R2B/R3 y la cascada R4→R5 con la alerta R7).
Es exactamente el tramo que ADR-006 sacó del prompt, así que se exige exactitud,
no acuerdo.

### (c) Reporte agregado del DoD → `python scripts/verificacion/etapa-clasificacion.py`

Es el **reporte de cierre** de la fase (subplan §10). Agrupa las métricas del
tramo determinista —**sin Ollama, sin Docling y sin red**— y sale con código ≠ 0
si alguna no llega al umbral:

- exactitud de letra por categoría (A/B/C/M/E);
- % de alerta R7 correctamente disparada;
- % de acuerdo negocio-vs-documento (consciente de que el caso R7 debe
  **discrepar**);
- default `CC0006` (criterio Gherkin de E-CLAS-2).

El reporte declara explícitamente su alcance: el subconjunto **no** es el golden
completo (la curación con contador sigue pendiente) y la exactitud del tramo
determinista debe ser 100% porque la letra la decide el motor, no el modelo.

## Métricas reportadas (DoD de F3)

| Métrica | Fuente | Objetivo |
|---|---|---|
| Exactitud de letra (regla + letra) | `scripts/verificacion/etapa-clasificacion.py` | 100% sobre los casos sintéticos |
| Default `CC0006` (Gherkin de E-CLAS-2) | `scripts/verificacion/etapa-clasificacion.py` | 100% (sin señal específica → CC0006, confianza baja, `senal_usada=ninguna`) |
| % de alerta R7 correctamente disparada | `scripts/verificacion/etapa-clasificacion.py` | 100% sobre los casos de discrepancia |
| % de acuerdo negocio-vs-documento | `scripts/verificacion/etapa-clasificacion.py` | 100% (coincide cuando debe / discrepa cuando debe) |
| Fidelidad de los prompts contables | `test_classification_paridad.py` | idénticos a los YAML de referencia |
| Integridad del etiquetado | `test_classification_paridad.py` | la letra derivada está sustentada por la evidencia del golden |

## Bug encontrado por esta verificación (T-305)

La corrida con documentos **reales** encontró un defecto que la suite
determinista no podía ver:

- **Síntoma**: sobre dos PDFs del golden cuyo encabezado dice `FACTURA A`, el
  extractor R5 devolvía la letra **`C`**.
- **Causa**: el patrón de R5 se había portado **literal** del prompt WIP
  (`FACTURA\s+([A-CME])`). El `\s+` se come el **salto de línea** que Docling
  deja en el markdown, así que la letra se capturaba de la línea siguiente:
  `"FACTURA\n  Código: 1"` → `C` (el `C` de "**C**ódigo").
- **Por qué no se detectó antes**: ese patrón nunca había corrido como código —
  vivía en un prompt como `criterio` **descriptivo** para el modelo. Recién al
  portarlo a un motor determinístico (T-301) y ejercitarlo con documentos reales
  (T-305) se manifestó.
- **Corrección**: `\s+` → `[ \t]+` (espacios y tabulaciones, nunca un salto de
  línea) y `\b` después de la letra (para no tomar la inicial de la palabra
  siguiente). La semántica del `criterio` ("letra junto a FACTURA") se conserva.
- **Cobertura**: `tests/test_classification_paridad.py`
  (`TestRegresionR5SaltoDeLinea`), que fija el comportamiento correcto y falla si
  el patrón vuelve a usar `\s+`.

Es el tipo de hallazgo que justifica la tarea: medir con documentos **reales**,
no solo con dobles.

## Estado de etiquetas

| Etiqueta | Estado | Nota |
|---|---|---|
| `veredicto` (F2) | etiquetada con evidencia objetiva | ver `../README.md` §"Etiquetado del `veredicto`" |
| `letra` (casos reales) | **derivada** de la evidencia, sin contador | se usa como referencia acotada, no como verdad de negocio |
| `condicion_fiscal` | **pendiente** (contador) | por eso la letra real se mide sin ella |
| `letra` (casos sintéticos) | conocida por construcción | es el tramo determinista del motor |
| Clasificación contable | **pendiente** (contador) | los casos sintéticos cubren el contrato |

## Cómo se amplía

1. Agregar el caso real al subconjunto solo si su `evidencia_veredicto` **nombra
   la letra** (si no, no hay sustento objetivo y va a curación con contador).
2. Para casos contables, sumar un markdown en `casos/` con texto conocido.
3. Cuando el contador cure `letra`/`condicion_fiscal` del golden completo, este
   subconjunto se puede retirar (o quedar como caso de regresión acotado).

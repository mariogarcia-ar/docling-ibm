# Golden set inicial — v2 (F0, T-004; etiquetado F2 en T-204)

Dataset de referencia etiquetado para medir el pipeline de `voucherflow`.
Definido en `v2/docs/plan/06-estrategia-calidad.md` §3.

## Qué contiene esta versión (golden v0.2)

- **`casos.csv`**: índice de casos con: id, ruta relativa al archivo en
  `v2/tests/fixtures/` (copia versionada), tipo de entrada, formato, y
  etiquetas de referencia. Desde **F2/T-204** incluye el `veredicto` etiquetado
  del subconjunto acotado (subplan §2.5) y la columna `evidencia_veredicto`.
- **`casos/`**: (reservado) copias normalizadas de documentos **sin PII
  innecesaria** para casos que lo requieran (el plan §3.2 propone
  `original.jpg` + `original.md` + `etiqueta.json`). En esta versión inicial
  los casos se **referencian por ruta** en `casos.csv` (no se duplican
  binarios), criterio pragmático documentado abajo.
- **`splits/`**: separación train/evaluación (el plan exige no calibrar reglas
  sobre el split de evaluación). v0.2 amplía ambos splits con los casos del
  subconjunto etiquetado de F2 (sin cruces train/eval).
- **`tests/fixtures/negativos/`**: negativos sintéticos (sin PII) generados con
  `scripts/F2/t204_negativos.py` para cubrir los tipos de “no comprobante” del
  gate (ver §“Etiquetado del `veredicto` (F2)”).

## Criterio de esta versión inicial (pragmático, documentado)

1. **Origen**: muestras reales de `files/` (jpg/pdf/jpeg/png). `files/` es una
   carpeta **temporal e ignorada por git** (`.gitignore`); por eso los archivos
   referenciados se **copian a `v2/tests/fixtures/`** (carpeta versionada) para
   que los tests no dependan de `files/`. Al momento de F0 `files/` **no
   contiene** `.md`/`.raw.md` generados (se producen con F1/v1); por eso el
   etiquetado de *letra* y *condición fiscal* queda **`pendiente`** (requiere
   OCR/Markdown o criterio de contador). El *tipo de entrada* se etiqueta por
   extensión y el veredicto "es comprobante" queda `pendiente` salvo
   confirmación. **Ajuste F2/T-204**: el `veredicto` del subconjunto acotado SÍ
   se etiqueta ahora con evidencia objetiva (OCR/texto nativo) y se documenta en
   `evidencia_veredicto`; el resto de las etiquetas de negocio sigue
   `pendiente` (ver §“Etiquetado del `veredicto` (F2)”).
2. **Referencia por ruta, no duplicación**: `casos.csv` referencia el archivo
   copiado en `v2/tests/fixtures/` con ruta relativa desde la raíz del repo
   (p. ej. `v2/tests/fixtures/golden/<id>.jpg`). La mayoría vive en
   `fixtures/golden/`; si el archivo ya formaba parte de los grupos
   `grandes/chicos/otros`, se referencia a esa copia (sin duplicar bytes).
3. **Etiquetado por contador**: toda etiqueta de negocio (letra A/B/C/M/E,
   condición fiscal, clasificación contable, veredicto) debe validarla una
   segunda persona (contador) sobre una muestra, según el plan §3.4. En esta
   versión, las etiquetas de negocio están en `pendiente` salvo las marcadas
   `verificada` por OCR/inspección.
4. **Versionado**: cada versión del golden set se referencia en los resultados
   (`golden_version`) para poder comparar métricas entre versiones.

## Cómo se amplía

1. Copiar el/los archivo(s) desde `files/` a `v2/tests/fixtures/` (o a
   `fixtures/golden/` si es exclusivo del golden).
2. Agregar filas a `casos.csv` apuntando a la ruta en `fixtures/`.
3. Si el caso requiere copia normalizada (sin PII), crearla bajo `casos/`.
4. Actualizar `splits/` (reglas_train vs. evaluacion). El split de evaluación
   **no** se usa para calibrar reglas (plan §3.4).
5. Versionar: subir `golden_version` y actualizar este README.

## Columnas de `casos.csv`

| Columna | Descripción |
|---------|-------------|
| `id` | Identificador del caso (hash corto o nombre de archivo sin extensión). |
| `ruta` | Ruta relativa (desde la raíz del repo) al archivo en `v2/tests/fixtures/`. |
| `mes` | Carpeta de mes de `files/` de origen (p. ej. `2026-08`). |
| `tipo_entrada` | `imagen` \| `pdf` \| `pdf_escaneado` \| `office` \| `texto`. |
| `formato` | Extensión real (`jpg`/`pdf`/`png`/`jpeg`/...). |
| `letra` | Letra/tipo de comprobante A/B/C/M/E/090/099 o `pendiente`. |
| `condicion_fiscal` | Condición fiscal emisor/receptor o `pendiente`. |
| `veredicto` | `comprobante` / `no_comprobante` / `indeterminado` / `pendiente`. |
| `calidad` | `nitido`/`ruidoso`/`baja_resolucion`/`rotado`/`pendiente`. |
| `split` | `train` \| `eval` (nunca calibrar sobre `eval`). |
| `etiqueta_estado` | `verificada` (por OCR/inspección) \| `pendiente` (requiere contador). |
| `evidencia_veredicto` | Sustento objetivo del `veredicto` (OCR o texto nativo), o vacío si sigue `pendiente`. **Nueva en v0.2 (F2/T-204)**. |

## Etiquetado del `veredicto` (F2 / T-204)

**Decisión de alcance** (subplan F2 §2.5): F2 **no espera** la curación masiva
con contador; etiqueta un **subconjunto acotado y representativo** del golden
con el `veredicto` del gate (`comprobante` / `no_comprobante` /
`indeterminado`) para poder medir la métrica acordada (§2.6). La curación
completa de las etiquetas de negocio (letra, condición fiscal, clasificación
contable) sigue pendiente para F3+.

### Criterio de etiquetado (evidencia objetiva, no predicción del modelo)

El `veredicto` se etiqueta con **evidencia objetiva** — OCR real (Docling) o
**texto nativo** del PDF — y no con la salida del gate: usar el modelo para
etiquetar y medirlo sería circular. La evidencia de cada fila se registra en la
columna `evidencia_veredicto`.

- **Positivos reales** (9 casos, con OCR/texto nativo que muestra encabezado
  fiscal/CAE/CUIT y emisor): `img_2025-08_2D2C9343`, `img_2025-08_A527FC11`,
  `img_2026-01_009550B7`, `pdf_2026-02_6D03019B`, `img_2026-06_6E0AD164`,
  `pdf_2026-08_2DC73C08`, `img_2026-08_1CDFCDA0` → `comprobante`.
- **Negativos reales** (OCR que declara la ausencia de comprobante o que es una
  captura de sistema): `img_2026-02_E527948C` (OCR: “GASTOS VARIOS, **FALTA
  FACTURA**”), `img_2026-02_4FAD7638` (OCR: “**no hay comprobante**”, captura
  de un movimiento) → `no_comprobante`.
- **Negativos sintéticos** (`neg_2026-*`, en `tests/fixtures/negativos/`):
  plantillas **sin PII** que cubren los tipos de “no comprobante” que el prompt
  del gate declara (capturas de apps/billeteras, extractos/resúmenes,
  documentos personales, memos, presupuestos, pantallas de aprobación, fotos
  ajenas al gasto). Se generan de forma reproducible con
  `python scripts/F2/t204_negativos.py` (Pillow best-effort para imágenes; PDF
  1.4 mínimo escrito con la stdlib). Su etiqueta es inequívoca (texto que
  declara “NO ES COMPROBANTE” y ausencia de datos fiscales del emisor), pero al
  ser sintéticos la validación del contador queda **pendiente**
  (`etiqueta_estado=pendiente`).

### Choque con `etiqueta_estado` / `letra` (resolución documentada)

El test `test_calidad_pendiente_explicada` (F0) exigía que un caso
`etiqueta_estado=verificada` tuviera una etiqueta de negocio real (`letra !=
pendiente`). En F2 el `veredicto` se verifica por OCR/texto nativo **sin** que
la `letra` (que requiere criterio de contador) esté curada. Resolución elegida
(F2/T-204, **cambio de criterio documentado aquí**):

1. Se **mantiene** `etiqueta_estado=pendiente` en las filas del subconjunto F2
   (la etiqueta de negocio “verificada por contador” sigue pendiente).
2. Se agrega la columna **`evidencia_veredicto`** para registrar el sustento
   objetivo del `veredicto` (OCR/texto nativo).
3. El test F0 se **ajusta** para aceptar la nueva evidencia: un caso
   `verificada` debe tener `letra` real **o** `evidencia_veredicto`; y se
   agregan dos tests de integridad nuevos (veredicto etiquetado del subconjunto
   F2 con evidencia; `golden_version` declarada en los splits).

Los splits `splits/*.json` se ampliaron con los casos etiquetados (sin cruces
train/eval) y declaran `golden_version: 0.2`.

## Métrica acordada de F2 (subplan §2.6)

Sobre el subconjunto etiquetado se reporta (herramienta:
`python scripts/F2/t204.py`, que corre el gate real con Ollama):

| Métrica | Definición | Objetivo |
|---------|-----------|----------|
| **Exactitud del gate** | aciertos / casos evaluados (contra el `veredicto` del golden) | ≥ 90% sobre el subconjunto (ajustable al cerrar) |
| **% de no-comprobantes que NO llegan a extracción** | de lo clasificado `no_comprobante`, cuánto queda **sin** `vista_fiel` | **100%** (ahorro de costo E-QWE) |
| **% de indeterminación** | casos con alguna pasada `indeterminado` / evaluados | Reportar (costo del doble paso) |

La lógica de cálculo es una función pura (`calcular_metricas`) cubierta por
tests **sin Ollama** en `tests/test_validation_gate_ahorro.py`; la corrida real
del gate sobre el golden es la herramienta manual (requiere Ollama local).

---

_Última actualización: 2026-09-09 (F2, T-204). Golden set v0.2._


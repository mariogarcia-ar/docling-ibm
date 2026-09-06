# Golden set inicial — v2 (F0, T-004)

Dataset de referencia etiquetado para medir el pipeline de `voucherflow`.
Definido en `v2/docs/plan/06-estrategia-calidad.md` §3.

## Qué contiene esta versión (golden v0.1)

- **`casos.csv`**: índice de casos con: id, ruta relativa al archivo en
  `v2/tests/fixtures/` (copia versionada), tipo de entrada, formato, y
  etiquetas de referencia.
- **`casos/`**: (reservado) copias normalizadas de documentos **sin PII
  innecesaria** para casos que lo requieran (el plan §3.2 propone
  `original.jpg` + `original.md` + `etiqueta.json`). En esta versión inicial
  los casos se **referencian por ruta** en `casos.csv` (no se duplican
  binarios), criterio pragmático documentado abajo.
- **`splits/`**: separación train/evaluación (el plan exige no calibrar reglas
  sobre el split de evaluación). Inicial: `evaluacion.json` con los casos
  etiquetados.

## Criterio de esta versión inicial (pragmático, documentado)

1. **Origen**: muestras reales de `files/` (jpg/pdf/jpeg/png). `files/` es una
   carpeta **temporal e ignorada por git** (`.gitignore`); por eso los archivos
   referenciados se **copian a `v2/tests/fixtures/`** (carpeta versionada) para
   que los tests no dependan de `files/`. Al momento de F0 `files/` **no
   contiene** `.md`/`.raw.md` generados (se producen con F1/v1); por eso el
   etiquetado de *letra* y *condición fiscal* queda **`pendiente`** (requiere
   OCR/Markdown o criterio de contador). El *tipo de entrada* se etiqueta por
   extensión y el veredicto "es comprobante" queda `pendiente` salvo
   confirmación.
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

---

_Última actualización: 2026-09-06 (F0, T-004). Golden set v0.1._

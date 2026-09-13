# Qué archivos genera

> [← Volver a la guía del operador](README.md)

Cada comando deja archivos distintos. Esta página dice **qué escribe cada uno**,
**dónde** y **para qué sirve**.

---

## Resumen

| Archivo | Lo genera | Para qué sirve |
|---|---|---|
| `<doc>.md` | `process` | El texto del documento, ordenado por posición |
| `<doc>.raw.md` | `process --raw` | El mismo texto, **sin** reordenar (referencia) |
| `<doc>_classification.json` | `classify` | Checkpoint de la cadena contable (permite reanudar) |
| `<doc>.batch.json` | `batch` | Checkpoint del lote (permite reanudar sin repetir) |
| `<doc>.case.json` | `run`/`batch --cases` | **El registro auditable** del caso |
| `index.jsonl` | `run`/`batch --cases` | Índice consultable de los casos |
| `<agregado>.json` | `batch -o` | **El agregado del lote** |

Todos los nombres siguen una convención: `<doc>` es el nombre del archivo de
origen **sin extensión**.

---

## Los que se generan siempre

### `<doc>.md` — el texto del documento

Lo escribe `process`. Es el documento convertido a Markdown, con el texto
**ordenado por posición** (de arriba hacia abajo, y en el orden que la orientación
indique). Es el insumo de las etapas que leen texto.

Se escribe:
- junto al documento original, o
- en el directorio de `-o`, si se pasó.

### `<doc>.raw.md` — el crudo de Docling

Lo escribe `process --raw`. Es el texto **sin** el reordenado por posición: sirve
para comparar cuando el orden automático no convence, o para diagnosticar.

**El crudo y el ordenado son archivos distintos a propósito.** Si compartieran
nombre, la segunda corrida pisaría a la primera y se perdería la comparación.

---

## Los que permiten reanudar

### `<doc>_classification.json` — checkpoint contable

Lo escribe `classify` **después de cada paso** de la cadena (centro de costo →
macro categoría → concepto). Si se interrumpe la cadena y se vuelve a correr, los
pasos ya resueltos **no se vuelven a preguntar al modelo**.

### `<doc>.batch.json` — checkpoint del lote

Lo escribe `batch` por cada documento que termina. Es lo que permite que una
segunda corrida sobre la misma carpeta **saltee lo ya hecho**.

Contiene, entre otras cosas, el **hash del contenido** del documento. Por eso:

| Situación | Qué pasa |
|---|---|
| Se vuelve a correr la misma carpeta | Los documentos ya hechos se saltean |
| Un documento **cambió** | Se reprocesa solo (su contenido es otro) |
| Un documento **falló** | Se reintenta (un error no cuenta como hecho) |
| Se pasa `--force` | Se reprocesan todos |

El hash es lo que hace segura la reanudación: saltear por **nombre de archivo**
haría que un documento modificado quedara sin reprocesar para siempre.

> **Se pueden borrar sin perder nada importante.** Si se borran los checkpoints,
> la próxima corrida reprocesa esos documentos. Los resultados ya obtenidos viven
> en el agregado y en los registros de trazabilidad.

---

## El registro auditable

### `<doc>.case.json` — el registro del caso

Lo escribe `run --cases DIR` o `batch --cases DIR`. Es **el archivo más completo**
del sistema: contiene todo lo que hace falta para auditar la decisión.

| Contenido | Qué hay adentro |
|---|---|
| **Identidad** | El id del documento y su ruta de origen |
| **Evidencia por fuente** | Qué leyó la imagen y qué leyó el texto, campo por campo, **con el fragmento que sostiene cada valor** |
| **Reglas disparadas** | Las reglas de lectura, las cruzadas y las que detectaron problemas |
| **Modelos y prompts** | Qué modelo y qué versión de prompt se usó en cada etapa |
| **Quién decidió** | `programa`, `agente_ia` o `hitl` |
| **El resultado** | El veredicto consolidado del caso |

**Es lo que permite responder "¿por qué se decidió así?"** sin volver a correr
nada:

```bash
voucherflow case show sha256:9f2c… --dir salida/cases
```

Se escribe de forma **atómica** (a un archivo temporal y luego se reemplaza), así
que una interrupción no deja un archivo a medias: el registro anterior queda
intacto.

### `index.jsonl` — el índice de casos

Lo escribe el mismo `--cases`. Es una línea por caso con lo mínimo para
**encontrarlo y filtrarlo**:

```bash
voucherflow case list --dir salida/cases
voucherflow case list --dir salida/cases --filtro estado=rechazado
```

Es un **derivado**: si se pierde o se corrompe, se puede reconstruir desde los
registros. Una línea corrupta no invalida el resto del índice.

---

## El agregado del lote

### `<agregado>.json` — el resultado consolidado

Lo escribe `batch -o`. Por defecto se llama `lote.agregado.json`.

Es **un único archivo** que responde *"¿qué pasó en el lote?"*:

| Sección | Qué contiene |
|---|---|
| `resumen` | Cuántos documentos, cómo salieron por estado, cuántos requieren revisión |
| `documentos[]` | Una entrada por documento: veredicto + **puntero a su registro auditable** |
| `metricas` | Los porcentajes del lote (si se usó `--cases`) |
| `lote` | La traza del runner: workers, ciclos y enfriamiento |

**El agregado no copia la evidencia.** Por cada documento guarda el veredicto y
un **puntero** (`sidecar`) al registro auditable. Copiar la evidencia daría un
archivo de cientos de MB en un lote grande, y —peor— **dos lugares que dicen lo
mismo** que pueden quedar desincronizados.

Así, cada pregunta tiene su archivo:

| Pregunta | Se responde con |
|---|---|
| ¿Qué pasó en el lote? | El **agregado** |
| ¿Por qué se decidió así **este** caso? | El **registro** de ese caso (`case show`) |

**El agregado se acumula**: cada corrida suma lo suyo, y un documento reprocesado
actualiza su entrada (no agrega otra). Después de interrumpir y reanudar un lote,
el archivo describe **la carpeta completa**, no la última corrida.

Si se pierde, se reconstruye desde los registros:

```bash
voucherflow case aggregate --dir salida/cases -o lote.json
```

---

## Dónde queda cada cosa

Con un comando típico:

```bash
voucherflow batch files/2025-08 --cases salida/cases -o salida/lote.json
```

```
files/2025-08/
  factura.pdf
  factura.md                        ← el texto (si se corrió process)
  factura.batch.json                ← checkpoint del lote (junto al documento)

salida/
  lote.json                         ← el agregado del lote
  cases/
    index.jsonl                     ← el índice de casos
    sha256_9f2c….case.json          ← el registro auditable de cada caso
```

Los **checkpoints viven junto a los documentos** (es lo que hace que la
reanudación funcione sin tener que recordar un directorio), y el **agregado y los
registros** van al directorio de salida.

> **Los derivados no se reprocesan como documentos.** El sistema solo mira
> archivos de los tipos soportados (PDF, imágenes, Office, HTML, `.md`, `.txt`),
> y además descarta por nombre los artefactos que él mismo escribe
> (`*.case.json`, `*_pipeline.json`, `*_classification.json`, `*.raw.md`). Los
> checkpoints `.batch.json` y el `index.jsonl` quedan afuera simplemente porque
> `.json` no es un tipo de documento. Dejar los derivados junto a los documentos
> es seguro.

---

## Qué se puede borrar

| Archivo | Si se borra… |
|---|---|
| Checkpoints (`.batch.json`, `_classification.json`) | La próxima corrida reprocesa esos documentos. **No se pierde ningún resultado** |
| Agregado del lote | Se reconstruye con `case aggregate` |
| Índice (`index.jsonl`) | Se reconstruye desde los registros |
| **Registro de un caso** (`.case.json`) | **Se pierde la evidencia de ese caso.** Es el único que no se puede recuperar |
| Markdown (`<doc>.md`) | Se regenera con `process` |

**Lo único irrecuperable es el registro de los casos**: es el que guarda la
evidencia. Los `--cases` no son opcionales si los resultados se van a auditar o
revisar.

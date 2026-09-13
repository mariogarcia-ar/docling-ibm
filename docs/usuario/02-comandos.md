# Referencia de comandos

> [← Volver a la guía del operador](README.md)

Los once subcomandos de `voucherflow`. Para cada uno: qué hace, cuándo usarlo,
sus banderas y su código de salida.

**La ayuda de la terminal es la fuente más actualizada**: `voucherflow <comando>
--help`.

---

## Códigos de salida

Todos los comandos comparten la convención:

| Código | Significado |
|---|---|
| `0` | El comando hizo su trabajo |
| `1` | El comando corrió pero el resultado no es "todo bien" (ver cada comando) |
| `2` | Error de uso: falta un argumento, una bandera inválida |
| `130` | Interrumpido por el usuario (Ctrl-C) |

**El dato va a `stdout` y el progreso a `stderr`.** Eso permite encadenar el
comando en un script sin que el progreso contamine la salida:

```bash
voucherflow run factura.pdf > resultado.json      # el JSON, limpio
voucherflow run factura.pdf | jq .resultado.estado
```

---

## Banderas comunes

Estas las aceptan los comandos del pipeline (`process`, `extract`,
`extract-detect`, `run`, `batch`):

| Bandera | Qué hace |
|---|---|
| `--force` | Reprocesa lo que ya estaba hecho. **Con efecto real en `batch`** (reanudación) |
| `--orientation {auto,horizontal,vertical}` | Qué orientación del texto extraer (default: `auto`, la dominante) |
| `--condicion-impositiva` | Condición para la cadena contable (default: `21`) |
| `--model MODEL` | Modelo de Ollama a usar (default: el rol configurado de cada etapa) |
| `--workers N` | Workers del lote (default: `1`). **Con efecto real en `batch`** |

> **Dos banderas que solo aplican a `batch`.** El parser las acepta en todos los
> comandos del pipeline (para que las banderas no cambien de nombre según el
> comando), pero el efecto real es del lote:
>
> - **`--force`**: en `batch` decide si se reanuda o se rehace. Un documento
>   suelto (`run`) no tiene lote del cual reanudar, así que reprocesa siempre —
>   y `run` **lo declara en su traza** (`detalle.force`) en vez de fingir un
>   efecto. En `process` y `extract`, que tampoco tienen reanudación, la bandera
>   se acepta y no cambia nada.
> - **`--workers`**: solo `batch` levanta el pool de procesos. En los demás
>   comandos se acepta y se ignora (el documento se procesa en el proceso
>   actual).

---

## `process` — procesar a Markdown

Convierte un documento (o una carpeta) a Markdown, ordenado por posición.

**Cuándo usarlo**: cuando solo hace falta el texto. Para obtener un resultado
(¿es comprobante? ¿qué tipo?) usar `run`.

```bash
voucherflow process factura.pdf                    # → factura.md
voucherflow process factura.pdf -o salida/         # → salida/factura.md
voucherflow process files/2025-08                  # toda la carpeta
voucherflow process factura.pdf --raw              # → factura.raw.md (crudo de Docling)
voucherflow process factura.pdf --orientation vertical
```

| Bandera | Qué hace |
|---|---|
| `origen` | Archivo o carpeta (recursiva) |
| `-o, --output DIR` | Directorio de salida (default: junto al archivo) |
| `--raw` | El **crudo** de Docling, sin reordenar por posición |

**Qué genera**: un `.md` por documento (o `<doc>.raw.md` con `--raw`). Ver
[qué archivos genera](04-salidas.md).

**Código de salida**: `0` siempre que haya procesado algo; `1` si no encontró
documentos procesables.

> **Nunca sobrescribe el documento de entrada.** Si el archivo de origen es un
> `.md` y el destino sería el mismo archivo, se escribe `<doc>.processed.md`.

---

## `validate` — ¿es un comprobante?

Corre el gate de validación: decide si el documento es un comprobante válido para
rendición de gastos.

**Cuándo usarlo**: para filtrar una carpeta antes de procesarla, o para entender
por qué un documento fue rechazado.

```bash
voucherflow validate factura.pdf
voucherflow validate factura.pdf --model qwen2.5vl:3b
```

| Bandera | Qué hace |
|---|---|
| `origen` | Archivo a validar |
| `--quick` | Reservado (hoy el gate siempre hace el doble paso) |
| `--model MODEL` | Modelo del gate |

**Qué imprime**: un JSON con el veredicto (`comprobante` / `no_comprobante` /
`indeterminado`), las pasadas que hizo y si preparó la vista para extraer.

**Código de salida**: `0` si es comprobante, `1` si no lo es.

---

## `classify` — tipo/letra y clasificación contable

Determina el tipo de comprobante (A/B/C/M/E) y corre la cadena contable
(centro de costo → macro categoría → concepto/código).

**Cuándo usarlo**: cuando hace falta la imputación contable. Acepta un `.md` o
cualquier documento (si no es texto, lo procesa primero).

```bash
voucherflow classify factura.md
voucherflow classify factura.md --condicion-impositiva 27
voucherflow classify factura.md -o clasificacion.json
```

| Bandera | Qué hace |
|---|---|
| `origen` | Archivo markdown/OCR (o cualquier documento) |
| `--condicion-impositiva` | `21` (default) · `10_5` · `27` · `2_5` · `exento_no_gravado` |
| `--model MODEL` | Modelo de la cadena |
| `-o, --output` | Archivo JSON de salida (default: stdout) |

**Qué imprime**: tipo/letra, certeza, reglas aplicadas, la clasificación contable
y el detalle de la cadena.

**Qué genera además**: un checkpoint `<doc>_classification.json` junto al
documento, que permite **reanudar** la cadena sin repetir los pasos ya resueltos.

**Código de salida**: `0` si la cadena contable terminó, `1` si falló algún paso.

---

## `extract` — extraer los campos

Extrae los campos del comprobante (CUIT, fecha, importes, número…) leyendo por
**dos vías en paralelo** —la imagen y el texto— y las contrasta.

```bash
voucherflow extract factura.pdf                    # archivo o carpeta
voucherflow extract factura.pdf -M kvi -o extract.json
voucherflow extract files/2025-08 -o extract.json
```

| Bandera | Qué hace |
|---|---|
| `origen` | Archivo o carpeta |
| `-M, --mode` | Modo heredado de v1 (`kvi`/`kvg`/`10`/`11`; default `kvi`) — se **registra** para trazabilidad |
| `-o, --output` | Archivo JSON (default: stdout) |
| *(comunes)* | `--force`, `--orientation`, `--condicion-impositiva`, `--model`, `--workers` |

> **Sobre `-M/--mode`**: v2 tiene **un** contrato de extracción, así que el modo
> no cambia lo que se lee. Se registra para poder comparar con las corridas
> históricas de v1.

**Qué imprime**: un JSON con la evidencia por campo: el **valor** y el
**fragmento del documento** que lo sostiene.

**Código de salida**: `0` si todos los documentos se extrajeron, `1` si alguno
falló (los errores van en la salida, por documento).

---

## `extract-detect` — detectar la letra

Detecta el tipo de comprobante leyendo la **imagen y el texto** (equivale a
`document_extraction.py -M 11.1` de v1), sin correr la cadena contable.

```bash
voucherflow extract-detect factura.pdf
voucherflow extract-detect files/2025-08 -o letras.json
```

| Bandera | Qué hace |
|---|---|
| `origen` | Archivo o carpeta |
| `-o, --output` | Archivo JSON (default: stdout) |
| *(comunes)* | Igual que `extract` |

**Qué imprime**: por documento, la letra detectada, la certeza, las reglas
aplicadas y los candidatos que quedaron descartados y vivos.

**Código de salida**: `0` si todos, `1` si alguno falló.

---

## `run` — el pipeline completo de un documento

**Es el comando principal para un documento suelto.** Encadena todo:
procesamiento → validación → extracción → conclusión, y devuelve el resultado
consolidado.

```bash
voucherflow run factura.pdf
voucherflow run factura.pdf --cases salida/cases       # guarda la trazabilidad
voucherflow run factura.pdf -o resultado.json
voucherflow run factura.pdf --clasificar-contable      # + cadena contable
```

| Bandera | Qué hace |
|---|---|
| `origen` | Documento a procesar |
| `-o, --output` | Archivo JSON del resultado (default: stdout) |
| `--cases DIR` | Guarda el **registro auditable** del caso (sidecar + índice) |
| `--clasificar-contable` | Corre además la cadena contable (tres llamadas al modelo) |
| `--no-gate` | No corre el gate de validación antes de extraer |
| `--no-agente` | No escala al agente de IA si el código no pudo concluir |
| *(comunes)* | `--force`, `--orientation`, `--condicion-impositiva`, `--model` |

**Qué imprime**: un JSON con el estado, el tipo, la certeza, el origen de la
decisión, los campos extraídos y la traza.

**Cuándo usar `--cases`**: siempre que el resultado se vaya a auditar o a
revisar. Es lo que permite después responder *"¿por qué se decidió así?"*.

**Cuándo usar `--clasificar-contable`**: solo si hace falta la imputación
contable; agrega tres llamadas al modelo por documento.

**Código de salida**: `0` si el caso se resolvió (incluido un rechazo: rechazar
es concluir); `1` si el documento no se pudo procesar.

---

## `batch` — el pipeline completo de una carpeta

**El comando para lotes.** Igual que `run` pero sobre una carpeta recursiva, con
workers, reanudación y enfriamiento.

```bash
voucherflow batch files/2025-08 -o salida/lote.json
voucherflow batch files/2025-08 --workers 4 --cooling on --cases salida/cases
voucherflow batch files/2025-08 --force             # rehace todo
```

| Bandera | Qué hace |
|---|---|
| `origen` | Carpeta (o archivo) |
| `-o, --output` | El **agregado** del lote (default: `lote.agregado.json`) |
| `--cases DIR` | Guarda el registro auditable de cada caso |
| `--clasificar-contable` | Igual que en `run` |
| `--no-gate`, `--no-agente` | Igual que en `run` |
| `--cooling {auto,on,off}` | Fuerza o desactiva el enfriamiento para esta corrida |
| `--work-window S` | Segundos de trabajo continuo antes de pausar |
| `--cool-down S` | Segundos de enfriamiento |
| `--no-checkpoints` | No lee ni escribe checkpoints: reprocesa todo |
| *(comunes)* | `--workers`, `--force`, `--orientation`, `--condicion-impositiva`, `--model` |

**Qué genera**: el **agregado** del lote (una entrada por documento, con el
veredicto y un puntero a su trazabilidad) y, si se pide `--cases`, el registro de
cada caso.

**Reanudación**: si un documento ya se procesó, se saltea. `--force` lo rehace. Si
un documento **cambió**, se reprocesa solo (el sistema compara el contenido, no
el nombre).

**Código de salida**: `0` si ningún documento falló; `1` si alguno falló o no
había nada que procesar.

> **Tiene su propia guía**: [trabajo con lotes largos](../../../BATCH.md), con el
> detalle de workers, checkpoints y enfriamiento.

---

## `ask` — preguntar sobre un documento

Responde una pregunta puntual sobre el contenido de un documento.

```bash
voucherflow ask factura.pdf -q "¿Cuál es el importe total?"
voucherflow ask factura.pdf -q "¿Quién es el emisor?" --model qwen2.5vl:3b
```

| Bandera | Qué hace |
|---|---|
| `origen` | Documento a consultar |
| `-q, --question` | **Obligatoria**: la pregunta |
| `--model MODEL` | Modelo a usar |

**Qué imprime**: la respuesta, en texto plano.

> **Importante**: `ask` **no** produce evidencia ni queda auditado. Es una
> consulta libre: si la respuesta se va a usar para algo que hay que poder
> justificar, usar `run` (que genera el resultado con su trazabilidad).

**Código de salida**: `0` si respondió.

---

## `arca` — consultar el padrón (opcional)

Consulta el padrón de AFIP/ARCA para constatar un comprobante.

```bash
voucherflow arca check factura.pdf --cuit 20123456789 --url https://… --token …
```

| Bandera | Qué hace |
|---|---|
| `accion` | `check` |
| `origen` | Documento a constatar |
| `--cuit`, `--url`, `--token` | Credenciales y endpoint |
| `--timeout` | Timeout de la consulta (default: 15 s) |
| `--condicion-impositiva`, `--model` | Como en los otros comandos |

**Sin `--url`/`--token`, el resultado es *no disponible*** y el comando sale con
`1`. Es lo esperado: la consulta al padrón es **opcional** y no bloquea nada del
resto del sistema.

**Qué hace exactamente**: procesa el documento, corre el gate, extrae y combina la
evidencia, detecta los huecos (los campos que faltan y serían buscables) y
consulta el padrón **solo por esos huecos**, dentro de un presupuesto de consultas.
Imprime los huecos detectados, las consultas hechas y si alguna trajo datos.

> **Alcance**: el sistema trae el contrato HTTP de la consulta. El flujo de
> certificados (WSAA) queda para una integración posterior, así que en la práctica
> esta función requiere credenciales ya gestionadas por el operador.

---

## `case` — consultar la trazabilidad

Consulta lo que quedó guardado con `--cases`.

```bash
voucherflow case list --dir salida/cases                       # todos los casos
voucherflow case list --dir salida/cases --filtro estado=revision
voucherflow case show sha256:9f2c… --dir salida/cases           # uno, completo
voucherflow case aggregate --dir salida/cases -o lote.json      # reconstruir el agregado
```

| Bandera | Qué hace |
|---|---|
| `accion` | `list` · `show <id>` · `aggregate` |
| `documento_id` | El id del caso (solo para `show`) |
| `--dir DIR` | Directorio de los registros (default: el directorio actual) |
| `--filtro CAMPO=VALOR` | Filtro del índice (repetible) |
| `-o, --output` | Destino del agregado (solo para `aggregate`; default: stdout) |

**`list`**: las filas del índice, con lo mínimo para encontrar un caso (id, fecha,
estado, certeza, quién decidió, tipo, si requiere revisión).

Los campos filtrables son **los del índice, y solo esos**:

```
documento_id  timestamp  schema_version  version_traza  archivo
estado  certeza  origen  quien_decidio  tipo_comprobante
hitl_requerido  hitl_prioridad  hitl_estado
reglas_disparadas  n_etapas  sidecar
```

Un filtro con un campo que no esté en esa lista es un **error**, no una
búsqueda vacía: `buscar` valida los campos y falla en vez de devolver cero
resultados (que se leería como *"no hay ninguno"* y sería falso).
Los valores booleanos y nulos se escriben literal: `--filtro hitl_requerido=false`,
`--filtro sidecar=null`.

**`show <id>`**: el registro **completo** de un caso — la evidencia de cada
fuente, las reglas que se dispararon, los modelos y prompts usados y quién
decidió. Es la respuesta a *"¿por qué se decidió así?"*.

**`aggregate`**: reconstruye el JSON consolidado del lote leyendo los registros.
Útil si se perdió el archivo del agregado o si la carpeta se procesó en varias
sesiones.

**Código de salida**: `0` si respondió; `1` si el caso pedido no existe (`case
show`).

---

## `hitl` — casos para revisar

Lista los casos que requieren revisión humana.

```bash
voucherflow hitl list --dir salida/cases
voucherflow hitl list --dir salida/cases --prioridad alta
voucherflow hitl list --dir salida/cases --certeza baja
```

| Bandera | Qué hace |
|---|---|
| `accion` | `list` |
| `--dir DIR` | Directorio de los registros. **Sin esta bandera no lee el histórico** |
| `--certeza {alta,baja}` | Filtra por certeza |
| `--prioridad {alta,baja}` | Filtra por prioridad de la revisión |

**Con `--dir`** (lo habitual): lee el **histórico persistido** — los casos que
quedaron marcados como `hitl_requerido`, con su prioridad y su motivo. Es la
forma de responder *"¿qué quedó pendiente de revisar?"*.

**Sin `--dir`**: reporta la **cola viva de la corrida en curso**, que solo
existe dentro del proceso que encoló los casos. Como un comando de terminal es un
proceso nuevo, sin `--dir` devuelve una lista vacía y lo avisa. No es un error:
es la diferencia entre *"lo que hay que revisar ahora"* y *"lo que quedó
pendiente en el histórico"*.

**Qué lista**: cada caso con su documento, prioridad y motivo. Prioridad `alta` =
revisión obligatoria (el sistema no pudo concluir con confianza); `baja` =
muestreo de auditoría (un caso resuelto que se revisa por control de calidad).

**Código de salida**: `0` siempre (incluso con la lista vacía o sin `--dir`).

**Qué hacer con lo que aparece**: ver [flujo de revisión humana](03-revision-humana.md).

---

## Combinaciones útiles

```bash
# Solo los rechazados de una carpeta
voucherflow batch files/2025-08 --cases salida/cases -o /dev/null
voucherflow case list --dir salida/cases --filtro estado=rechazado

# Los que necesitan un ojo humano, con prioridad obligatoria
voucherflow hitl list --dir salida/cases --prioridad alta

# Reprocesar solo lo que cambió (sin --force: el sistema compara el contenido)
voucherflow batch files/2025-08 --cases salida/cases -o salida/lote.json

# El resultado de un documento puntual, en una línea de comando
voucherflow run factura.pdf | python -c "import json,sys; d=json.load(sys.stdin); print(d['estado'], d['resumen']['tipo_comprobante'])"
```

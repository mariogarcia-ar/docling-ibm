# Referencia de comandos

> [← Volver a la guía del operador](README.md)

Los trece subcomandos de `voucherflow`. Para cada uno: qué hace, cuándo usarlo,
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
| `--force` | Reprocesa lo que ya estaba hecho. **Con efecto real en `process` y `batch`** (reanudación) |
| `--orientation {auto,horizontal,vertical}` | Qué orientación del texto extraer (default: `auto`, la dominante) |
| `--condicion-impositiva` | Condición para la cadena contable (default: `21`) |
| `--model MODEL` | Modelo de Ollama a usar (default: el rol configurado de cada etapa) |
| `--workers N` | Workers del lote (default: `1`). **Con efecto real en `batch`** |

> **Dónde tiene efecto `--force`.** El parser la acepta en todos los comandos del
> pipeline (para que las banderas no cambien de nombre según el comando), pero no
> en todos cambia algo:
>
> - **`process`**: reanuda por existencia del markdown de salida. Sin `--force`,
>   un documento ya procesado **se saltea** (y el comando lo declara, con cuántos).
> - **`batch`**: reanuda por el checkpoint del lote (`<doc>.batch.json`, que además
>   guarda el hash del contenido: un documento **cambiado** se reprocesa solo).
> - **`run`**: reprocesa siempre —un documento suelto no tiene lote del cual
>   reanudar— y **lo declara en su traza** (`detalle.force`) en vez de fingir un
>   efecto.
> - **`extract` / `extract-detect`**: reanudan por el **contenido** del documento
>   (reutilizan la entrada de una corrida anterior y acumulan; `--force` rehace).
>
> **`--workers`**: solo `batch` levanta el pool de procesos. En los demás
> comandos se acepta y se ignora (el documento se procesa en el proceso
> actual).

---

## `process` — procesar a Markdown

Convierte un documento (o una carpeta) a Markdown, ordenado por posición.

**Cuándo usarlo**: cuando solo hace falta el texto. Para obtener un resultado
(¿es comprobante? ¿qué tipo?) usar `run`.

```bash
voucherflow process factura.pdf                    # → factura.md
voucherflow process factura.pdf -o salida/         # → salida/factura.md
voucherflow process var/files/2025-08                  # toda la carpeta
voucherflow process var/files -o var/procesados    # espejando el árbol
voucherflow process factura.pdf --raw              # → factura.raw.md (crudo de Docling)
voucherflow process factura.pdf --orientation vertical
```

| Bandera | Qué hace |
|---|---|
| `origen` | Archivo o carpeta (recursiva) |
| `-o, --output DIR` | Directorio de salida (default: junto al archivo) |
| `--raiz DIR` | Raíz desde la cual se espeja el árbol **con `-o`** (sin `-o` no aplica: la salida va junto al archivo) |
| `--raw` | El **crudo** de Docling, sin reordenar por posición |

**Con `-o`, la salida espeja el árbol** desde la raíz de la entrada, sin repetir
el nombre de la carpeta de entrada:

| Entrada | Salida (`-o var/procesados`) |
|---|---|
| `var/files/2025-08/2D2C9343/factura.pdf` | `var/procesados/2025-08/2D2C9343/factura.md` |
| `var/files/2025-08` | `var/procesados/2025-08/2D2C9343/factura.md` |
| `var/files/2025-08/2D2C9343` | `var/procesados/2025-08/2D2C9343/factura.md` |

La raíz se **sube** automáticamente salteando los niveles de mes (`2025-08`) y de
lote, así que **el mismo documento escribe siempre el mismo archivo** sin importar
desde qué subcarpeta invoques la corrida: los tres casos de la tabla escriben los
mismos archivos. Es la misma regla que usan [`corpus`](#corpus--pre-reducir-las-imágenes)
y [`pdf`](#pdf--convertir-pdf-a-imágenes), a propósito: un solo criterio para los
tres comandos.

Sin el espejado, todos los `.md` caían en el nivel raíz de la salida: dos
documentos homónimos de carpetas distintas escribían **el mismo archivo** (uno se
perdía sin que nada lo dijera) y no había forma de saber de qué documento era cada
markdown.

**Qué genera**: un `.md` por documento (o `<doc>.raw.md` con `--raw`). Ver
[qué archivos genera](04-salidas.md).

**Reanudación**: un documento cuyo markdown ya existe **se saltea**, y el comando
lo declara al final (`N documento(s) salteado(s)`). `--force` lo rehace. Antes
`process` reprocesaba todo en cada corrida: con PDF nativos era el **61% del
tiempo**, y en un PDF escaneado el OCR se pagaba entero de nuevo. La regla es la
misma que usan `pdf` y `corpus`.

⚠️ **Es reanudación por existencia, no por frescura**: si cambiás `--orientation`
(o `--raw`), el destino ya escrito **se reutiliza**. En ese caso usá `--force`.

```bash
voucherflow process var/files -o var/procesados            # 1ª vez: procesa todo
voucherflow process var/files -o var/procesados            # 2ª: saltea lo hecho
voucherflow process var/files -o var/procesados --force    # rehace todo
```

**Un documento que falla no corta el lote.** Un archivo que no parece un documento
(una foto con relación de aspecto extrema, un PDF ilegible) se anota, se declara en
el log y la corrida **sigue** con el resto. Al final imprime cuántos fallaron,
agrupados por motivo, y con `-o DIR` deja el detalle por archivo en `DIR/fallos.json`
— que es la lista de **qué reintentar**:

```
1 documento(s) fallaron (no se escribió su markdown):
         1 × La imagen 'pagina.jpg' no superó el gate de procesabilidad (T-102)
     detalle: var/procesados/fallos.json
```

Los que fallaron se reintentan solos en la corrida siguiente (no dejan markdown, así
que no quedan "hechos"). Si querés rehacerlos ya, `--force`.

**Código de salida**: `0` si todo salió bien (o todo ya estaba hecho); `1` si algún
documento falló o no se encontró ninguno procesable.

> **Nunca sobrescribe el documento de entrada.** Si el archivo de origen es un
> `.md` y el destino sería el mismo archivo, se escribe `<doc>.processed.md`. Por
> eso `-o` apuntando a la carpeta de los documentos es un **error de uso**
> (código `2`): escribir ahí sería pisar los originales. Usá un directorio
> aparte — o una subcarpeta, que se excluye del recorrido para que la corrida no
> reprocese su propia salida.

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
voucherflow extract var/files/2025-08 -o extract.json
```

| Bandera | Qué hace |
|---|---|
| `origen` | Archivo o carpeta |
| `-M, --mode` | Modo heredado (`kvi`/`kvg`/`10`/`11`; default `kvi`) — se **registra** para trazabilidad |
| `-o, --output` | Archivo JSON (default: stdout) |
| *(comunes)* | `--force`, `--orientation`, `--condicion-impositiva`, `--model`, `--workers` |

> **Sobre `-M/--mode`**: el extractor tiene **un** contrato único, así que el modo
> no cambia lo que se lee. Se registra para poder comparar con las corridas
> históricas.

**Qué imprime**: un JSON con la evidencia por campo: el **valor** y el
**fragmento del documento** que lo sostiene.

**Reanudación y acumulación**: si `-o` apunta al archivo de una corrida anterior, los
documentos que **no cambiaron** se reutilizan (no se vuelve a consultar al modelo) y
las entradas nuevas se **suman** a las viejas. `--force` rehace todo.

```bash
voucherflow extract var/files -o extract.json            # 1ª vez: extrae todo
voucherflow extract var/files -o extract.json            # 2ª: reutiliza (0 llamadas)
voucherflow extract var/files -o extract.json --force    # rehace todo
```

Se reutiliza por el **contenido** del documento (su hash), no por su nombre: un
documento **modificado** se vuelve a extraer solo, y uno que **falló** se reintenta
(un error no es un paso completado).

> ⚠️ **Sin `-o`, el lote grande se avisa.** La evidencia pesa ~11,7 KB por documento,
así que los 3.846 del corpus son ~45 MB de `stdout`. El comando lo declara con el peso
real y sugiere `-o`; el contrato de la salida no cambia (el JSON completo sigue
saliendo por `stdout`).

**Código de salida**: `0` si todos los documentos se extrajeron, `1` si alguno
falló (los errores van en la salida, por documento).

---

## `extract-detect` — detectar la letra

Detecta el tipo de comprobante leyendo la **imagen y el texto** (equivale al
modo histórico `-M 11.1`), sin correr la cadena contable.

```bash
voucherflow extract-detect factura.pdf
voucherflow extract-detect var/files/2025-08 -o letras.json
```

| Bandera | Qué hace |
|---|---|
| `origen` | Archivo o carpeta |
| `-o, --output` | Archivo JSON (default: stdout) |
| *(comunes)* | Igual que `extract` |

**Qué imprime**: por documento, la letra detectada, la certeza, las reglas
aplicadas y los candidatos que quedaron descartados y vivos.

**Reanudación**: igual que `extract` (reutiliza lo ya detectado y acumula;
`--force` rehace).

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
voucherflow batch var/files/2025-08 -o salida/lote.json
voucherflow batch var/files/2025-08 --workers 4 --cooling on --cases salida/cases
voucherflow batch var/files/2025-08 --force             # rehace todo
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

> **Tiene su propia guía**: [trabajo con lotes largos](../../BATCH.md), con el
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

## `corpus` — pre-reducir las imágenes

```bash
voucherflow corpus var/files --solo-medir                 # medir, sin escribir
voucherflow corpus var/files -o var/processed --workers 4
voucherflow corpus var/files/2025-08 --limite 20 --detalle
```

**Para qué sirve**: baja el **peso** y los **tokens de visión** de un corpus de
imágenes antes de procesarlo. Reducir el lado mayor a 1024 px y reencodear con
calidad moderada baja el costo de las llamadas a los modelos de visión y el
espacio en disco, sobre todo si el corpus se va a mandar más de una vez.

**Empezá siempre con `--solo-medir`**: calcula qué se reduciría y cuánto, sin
escribir nada. En ese modo el porcentaje de **peso** no se reporta (no se midió),
y la salida lo dice en vez de inventar un número.

**Alineación al VLM**: las dimensiones se redondean a múltiplos de 28 para que el
preprocesador de Qwen2.5-VL no re-escale la imagen por su cuenta (si re-escala, el
conteo de tokens deja de ser predecible). Por eso el objetivo es **aproximado**:
1024 puede quedar en 1036. Usá `--sin-alinear` solo si el destino **no** es
Qwen2.5-VL: con esa bandera el objetivo es exacto (1000 en vez de 1008).

⚠️ **Reducir puede subir el peso.** Un escaneo en blanco y negro puro (1-bit) o
en escala de grises ya viene muy comprimido: el reencode puede pesar más que el
original. El resumen del comando lo avisa y lista los archivos que engordaron
(dentro del total quedarían invisibles). Los **tokens de visión bajan igual**, y
ese es el objetivo real de la herramienta.

| Bandera | Qué hace |
|---|---|
| `-o, --salida DIR` | Carpeta raíz de salida (default: `var/processed`, de la configuración). |
| `--raiz DIR` | Raíz desde la cual se espeja el árbol. Fijala si el corpus no tiene forma de mes. |
| `--lado-mayor PX` | Lado mayor objetivo (default: `1024`). **Nunca agranda.** |
| `--calidad 1-100` | Calidad del reencode (default: `80`). |
| `--piso-lado-menor PX` | Piso del lado menor, para imágenes muy alargadas (default: `256`). |
| `--sin-alinear` | No alinear a múltiplos de 28 (solo si el destino no es Qwen2.5-VL). |
| `--backend {pillow,ffmpeg}` | Motor de reencode (default: `pillow`). |
| `--formato {mismo,jpg}` | `mismo` conserva la extensión; `jpg` fuerza JPEG. |
| `--incluir-pdf` | Incluye los PDF del corpus, renderizando una imagen por página. |
| `--dpi-pdf DPI` | Resolución del render de PDF a imagen (default: `300`). |

⚠️ **`--formato jpg` reescribe la extensión de todo el lote**, así que dos
originales con el mismo nombre base (`factura.jpg` y `factura.png`) apuntarían al
mismo archivo. El comando detecta la colisión y **rechaza el lote sin escribir
nada** (código `2`), en vez de perder un archivo y reportar los dos como
reducidos. Con `--formato mismo` no hay colisión entre formatos distintos.
| `--extensiones LISTA` | Extensiones a procesar (default: `.jpeg,.jpg,.png`). |
| `--forzar` | Reescribe el destino aunque exista (sin esto, **reanuda**). |
| `--copiar-no-reducidas` | Copia sin tocar las que ya entran en el objetivo (salida completa). |
| `--solo-medir` | No escribe nada: solo mide y reporta. |
| `--workers N` | Hilos concurrentes (default: `4`). Usá `1` si el equipo se calienta. |
| `--limite N` | Procesa solo las primeras N imágenes (`0` = todas). |
| `--detalle` | Una línea por archivo (a stderr). |
| `--reporte ARCHIVO.json` | Reporte completo (resumen + por archivo). |

**La salida espeja el árbol desde la raíz de la entrada**, sin repetir el nombre
de la carpeta de entrada: `var/files/2025-08/2D2C9343/foto.jpg` sale a
`var/processed/2025-08/2D2C9343/foto.jpg`. La raíz se **sube** automáticamente
salteando los niveles de mes (`2025-08`) y de lote, así que **la misma imagen
escribe siempre el mismo archivo** sin importar desde qué subcarpeta invoques la
corrida. Eso es lo que hace que la reanudación funcione (y que no se reprocese lo
que ya está).

**Qué significa cada estado**: `reducido` (se escribió), `omitido` (no se tocó:
ya entraba en el objetivo, o el destino ya existía) y `fallo` (imagen ilegible o
error de escritura). Un `omitido` **no** es un error.

**Código de salida**: `0` si todo salió bien (o no había nada que hacer), `1` si
hubo algún fallo, `2` si los argumentos no son válidos.

**⚠️ Ojo con el OCR**: reducir *antes* de un OCR clásico (RapidOCR/EasyOCR) puede
degradar la letra chica, porque el OCR lee píxeles. En ese caso bajá la reducción
(`--lado-mayor 1536` o `2048`) o medí primero con `--solo-medir`. Para el camino
**VLM** (la imagen viaja al modelo) reducir es lo correcto.

---

## `pdf` — convertir PDF a imágenes

```bash
voucherflow pdf comprobante.pdf                          # → var/paginas/pagina_1.jpg
voucherflow pdf var/files -o var/paginas --dpi 300        # todo el corpus
voucherflow pdf var/files -o var/paginas --solo-medir     # ver sin escribir
voucherflow pdf lote.pdf --primera 1 --ultima 2 --forzar
```

**Para qué sirve**: materializa las **páginas** de un PDF como imágenes JPG.
Tres usos concretos: mirar un PDF antes o después de procesarlo, armar un corpus
de imágenes, y alimentar al **laboratorio de LLM externos** (`voucherflow-lab`),
que manda imágenes al proveedor y por eso renderiza los PDF de su lote con esta
misma pieza.

⚠️ **No es lo mismo que `process`.** `voucherflow process` decide solo si un PDF
se lee como texto nativo o hay que rasterizarlo, y **descarta** la imagen cuando
termina. Este comando **conserva** las imágenes, que es otra cosa: no lo uses para
"procesar un PDF", usalo cuando necesitás las imágenes.

| Bandera | Qué hace |
|---|---|
| `-o, --salida DIR` | Carpeta de salida (default: `var/paginas`, de la configuración). |
| `--raiz DIR` | Raíz desde la cual se espeja el árbol. Fijala si el corpus no tiene forma de mes. |
| `--dpi DPI` | Resolución del render (default: `300`, la que usa el pipeline para OCR). |
| `--calidad 1-100` | Calidad JPEG (default: `95`). |
| `--recortar` | Recorta **siempre** a la imagen más grande de la página. |
| `--sin-recortar` | Renderiza la página completa, **siempre**. |
| `--primera N` / `--ultima N` | Rango de páginas, 1-based e inclusive. |
| `--patron PLANTILLA` | Nombre de cada imagen. Tokens: `{nombre}`, `{pagina}`, `{total}`. |
| `--forzar` | Re-renderiza aunque el archivo exista (sin esto, **reanuda**). |
| `--solo-medir` | Muestra qué escribiría, sin crear nada. |
| `--limite N` | Convierte solo las primeras N páginas (`0` = todas). |

**El recorte, por defecto, se decide por página.** Un `--recortar` fijo es
correcto para un **escaneado** (ahí la imagen grande *es* el documento: es el caso
de un ticket chico centrado en una hoja A4, que a página completa queda diminuto e
ilegible). Pero en un **PDF generado por sistema**, con texto nativo, la imagen
más grande del archivo suele ser el **logo del emisor**: recortar ahí devuelve un
logo de 60 pt en vez del comprobante. El default evalúa la cobertura de la imagen
y elige por página, que es lo correcto en los dos casos sin que tengas que saber
cuál tenés enfrente. Usá `--recortar` / `--sin-recortar` solo para forzarlo.

**El nombre de la imagen**: con **un solo** PDF es `pagina_1.jpg`; con **varios**
lleva el del documento (`<nombre>_pagina_1.jpg`), porque si no dos PDF homónimos
de meses distintos se pisarían.

**La salida espeja el árbol** desde la raíz de la entrada (igual que `corpus`), así
que la misma entrada escribe siempre los mismos archivos: eso es lo que hace que
la reanudación funcione.

**Código de salida**: `0` si todo salió bien, `1` si alguna página falló, `2` si
los argumentos no son válidos (por ejemplo un rango de páginas que deja el plan
vacío, o una ruta sin ningún PDF).

---

## Combinaciones útiles

```bash
# Solo los rechazados de una carpeta
voucherflow batch var/files/2025-08 --cases salida/cases -o /dev/null
voucherflow case list --dir salida/cases --filtro estado=rechazado

# Los que necesitan un ojo humano, con prioridad obligatoria
voucherflow hitl list --dir salida/cases --prioridad alta

# Reprocesar solo lo que cambió (sin --force: el sistema compara el contenido)
voucherflow batch var/files/2025-08 --cases salida/cases -o salida/lote.json

# El resultado de un documento puntual, en una línea de comando
voucherflow run factura.pdf | python -c "import json,sys; d=json.load(sys.stdin); print(d['estado'], d['resumen']['tipo_comprobante'])"
```

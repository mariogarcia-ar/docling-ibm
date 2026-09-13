# `scripts/` — verificación por tarea y utilidades operativas

Dos tipos de script conviven acá:

1. **Verificación por tarea** (`F<n>/t<n><nn>.py`): cada tarea del plan tiene un
   script que la ejercita de punta a punta y sale con **código 0**. Son la
   contraparte ejecutable de los tests de `tests/` (misma convención que
   `tests/golden/F<n>/` + `scripts/F<n>/paridad_*.py`).

   ⚠️ Los de `F1/*` y `F2/t20*.py` **piden argumentos** (`rutas`): invocados
   pelados salen con exit 2. No es un fallo.

   ```bash
   python scripts/F6/t602.py          # 12 escenarios + 6 fronteras
   python scripts/F3/t305.py          # exactitud de la letra
   ```

2. **Utilidades operativas** (raíz de `scripts/`): herramientas para preparar y
   diagnosticar el corpus **antes** de procesarlo. No pertenecen a una fase.

   | Script | Para qué |
   |---|---|
   | `reducir_tokens.py` | Pre-reduce el peso y los tokens de visión de un corpus de imágenes. |
   | `validar_comprobantes_openai.py` | Valida/extrae campos de comprobantes con la API de OpenAI (visión), según el prompt de Mendel. |
   | `validar_comprobantes_deepseek.py` | Lo mismo con la API de DeepSeek (visión): gemelo de la anterior, con las diferencias del proveedor. |

---

## `reducir_tokens.py` — bajar tokens del corpus

Reduce las imágenes a un **lado mayor objetivo** (1024 px por defecto, sin
agrandar nunca), las reencoda con calidad moderada y espeja el árbol de carpetas
bajo una salida (`procesadas/`). Menos bytes en disco/red y **menos tokens de
visión** cuando la imagen después viaja a un VLM.

Porta el loop de shell con `ffmpeg` y le corrige cuatro defectos:

1. `scale=1024:-1` **agranda** las imágenes chicas (más peso y más tokens que el
   original). Acá una imagen que ya entra en el objetivo no se toca.
2. `scale=1024:-1` fija el **ancho**, no el lado mayor: una foto vertical de
   3000×4000 salía 1024×1365 en vez de 768×1024 (~45% más tokens). Acá se
   reduce por lado mayor.
3. Siempre escribía JPEG, incluso con salida `.png` (bytes JPEG en un archivo
   `.png`). Acá el formato se elige por extensión, o se fuerza con `--formato jpg`.
4. Sin paralelismo, sin reanudación ni reporte. Acá hay `--workers`, se saltea
   el destino ya existente y hay resumen + reporte JSON.

**Dimensiones alineadas al VLM.** Las dimensiones destino se calculan con
`voucherflow.validation.prompt_qween.dimensiones_objetivo_vlm` — el **mismo**
`smart_resize` de Qwen2.5-VL que usa el pipeline — así el servidor no
re-escalea y el conteo de tokens es predecible. Los defaults (1024 px / calidad
80 / piso de lado menor 256) salen de la librería: una sola fuente de verdad con
la vista de revisión (E-QWE-2). Si la librería no se puede importar, cae a una
alineación local a múltiplos de 28 y lo **declara** en el resumen
(`defaults_desde`).

```bash
# Siempre conviene empezar midiendo (no escribe nada)
python scripts/reducir_tokens.py ../files --solo-medir

# Elegir la carpeta de salida (default: procesadas)
python scripts/reducir_tokens.py ../files -o salida

# Prueba con 20 imágenes, con una línea por archivo
python scripts/reducir_tokens.py ../files --limite 20 --detalle

# Corpus completo: 4 workers y reporte JSON
python scripts/reducir_tokens.py ../files -o salida --workers 4 \
  --reporte salida/reporte.json

# Otra resolución (p. ej. antes de un OCR clásico)
python scripts/reducir_tokens.py ../files --lado-mayor 1536 --workers 4
```

**Carpeta de salida (`-o` / `--salida`).** El árbol se espeja desde `--raiz` (o
desde el nivel que no sea un mes), sin repetir el nombre de la carpeta de
entrada. Con `../files/2025-08/2D2C9343/foto.jpg`:

| Invocación | Archivo resultante |
|---|---|
| `../files` | `procesadas/2025-08/2D2C9343/foto.jpg` |
| `../files/2025-08` | `procesadas/2025-08/2D2C9343/foto.jpg` |
| `../files/2025-08/2D2C9343` | `procesadas/2025-08/2D2C9343/foto.jpg` |
| `-o /tmp/reducido` | `/tmp/reducido/2025-08/2D2C9343/foto.jpg` |

⚠️ **La misma imagen escribe siempre el mismo archivo**, sin importar con qué
subcarpeta se invoque. La raíz **no** se deriva de la ruta pasada: se **sube**
hasta el primer nivel que no sea un mes (`2025-08`) ni un hash de lote
(`2D2C9343`). Sin esto, procesar `files` y después `files/2025-08` escribía dos
árboles distintos y **volvía a pagar** por lo ya procesado. Para corpus con otra
forma, fijá `--raiz`.

⚠️ `-o` es **solo para las imágenes**; el JSON del reporte va aparte con
`--reporte`. Y si la salida cae dentro de la entrada (p. ej. `-o .`), el script
**excluye** del recorrido los archivos que ya están dentro de ella, para no
reprocesar lo reducido.

Salida (ejemplo real, 200 imágenes de `../files`):

```text
archivos                  : 200
  reducidos               : 199
  omitidos                : 1
  fallos                  : 0
peso                      : 573.6 MiB  →  20.0 MiB   (-96.5%)
tokens de visión (estim.) : 1,423,303  →  214,020   (-85.0%)
```

### Banderas

| Bandera | Efecto |
|---|---|
| `-o, --salida` | Carpeta raíz de salida (default `procesadas`). El árbol se espeja desde `--raiz`. |
| `--raiz DIR` | Raíz desde la cual se espeja el árbol. Si se omite, sube hasta el nivel que no sea mes/hash. |
| `--lado-mayor PX` | Lado mayor objetivo (default 1024). **Nunca agranda.** |
| `--calidad 1-100` | Calidad de reencode (default 80). |
| `--piso-lado-menor PX` | Piso del lado menor, para imágenes muy alargadas (default 256). |
| `--sin-alinear` | No alinear a múltiplos de 28 (solo si el destino NO es Qwen2.5-VL). |
| `--backend pillow\|ffmpeg` | Motor de reencode (default `pillow`). |
| `--formato mismo\|jpg` | Conserva la extensión o fuerza JPEG (default `mismo`). |
| `--extensiones LISTA` | Extensiones a procesar (default `.jpeg,.jpg,.png`). |
| `--forzar` | Reescribe el destino aunque exista; sin esto **reanuda**. |
| `--copiar-no-reducidas` | Copia sin tocar las que ya entran en el objetivo (salida completa). |
| `--solo-medir` | No escribe nada: mide y reporta qué se reduciría. |
| `--workers N` | Concurrencia (default 4). Usá 1 si el equipo se calienta. |
| `--limite N` | Procesa solo las primeras N (0 = todas). |
| `--detalle` | Una línea por archivo (a stderr). |
| `--reporte ARCHIVO.json` | Reporte completo (resumen + por archivo). |

Códigos de salida: `0` ok (o nada que hacer) · `1` hubo fallos · `2` error de uso
o backend no disponible · `130` interrumpido.

### Cosas que conviene saber

- **Es reanudable.** Si el destino existe, se saltea. Ese es el modo de retomar
  un lote cortado; `--forzar` lo reescribe.
- **Mide el destino real al reanudar**, no lo que *habría* calculado: así el
  reporte no miente si el archivo existente se generó con otros parámetros.
- **En `--solo-medir` el peso destino no se mide** (no se escribió) y el resumen
  lo declara en vez de reportar un «-100%» inventado. Los tokens sí se comparan:
  se derivan de las dimensiones calculadas, no del archivo escrito.
- **Los tokens son una estimación** (`ceil(ancho/28) × ceil(alto/28)`, el
  gridding de Qwen2.5-VL). Sirve para comparar antes/después; no es lo que
  reporta el servidor.
- ⚠️ **Cuidado con el OCR clásico.** Reducir *antes* de un OCR por píxeles
  (RapidOCR/EasyOCR vía Docling) puede degradar la letra
  chica. Usá `--solo-medir`, subí `--lado-mayor` o no reduzcas. Para el camino
  **VLM** (la imagen viaja al modelo) reducir es lo correcto.
- **`--backend ffmpeg`** requiere un ffmpeg sano. El script hace un *preflight*
  y, si el binario no arranca (típico en macOS con dependencias de Homebrew
  rotas), falla **una vez** con un mensaje claro en vez de repetir el error de
  dyld por archivo.

---

## `validar_comprobantes_openai.py` — validación/extracción con OpenAI

Implementa `prompt_validacion_comprobantes_mendel.md`: manda la **imagen del
comprobante** (y, opcionalmente, los **datos cargados en Mendel**) a la API de
OpenAI y devuelve el JSON del prompt, un archivo por documento. Pensado para
correr sobre lo que dejó `reducir_tokens.py`.

```bash
# 1) La clave va en el entorno o en un .env (el .gitignore ya lo excluye).
export OPENAI_API_KEY=sk-...
#    o:  echo 'OPENAI_API_KEY=sk-...' > .env
#    Los dos pasos, con la plantilla documentada:  (ver ../.env.example)
#      cp ../.env.example .env  &&  set -a && source .env && set +a

# 2) Verificar el flujo sin gastar tokens
python scripts/validar_comprobantes_openai.py ../procesados --modo extraer \
  --limite 3 --dry-run --detalle-log

# 3) Los 3 modos
python scripts/validar_comprobantes_openai.py ../procesados --modo extraer --limite 5
python scripts/validar_comprobantes_openai.py ../procesados --datos datos.json -o validaciones
python scripts/validar_comprobantes_openai.py ../procesados --modo diff --datos datos.json
```

### Los 3 modos

| Modo | Llama a la API | Para qué |
|---|---|---|
| `validar` (default) | sí | Imagen + datos → comparación campo a campo (`estado_global`, `coincide`, `discrepancias_criticas`). Requiere `--datos`. |
| `extraer` | sí | **Solo lectura** de la imagen (sin comparar). Es la "primera pasada barata" del prompt. No requiere datos. |
| `diff` | **no** | Corre el diff de campos **en Python**: determinístico y auditable. Reutiliza las extracciones ya guardadas por `extraer`; si no hay, avisa. |

El modo `diff` implementa las reglas de negocio en Python en vez de pedirle al
LLM que compare números — que es lo que el propio prompt recomienda en sus notas
de implementación ("es más barato y más auditable"). Están implementadas las 15:
tipos `090`/`099` indistintos, tolerancia de formato en la razón social
(`S.A.` vs `SA`, tildes, mayúsculas), CUIT dígito a dígito, fechas por fecha (no
por texto), número de factura sin separadores, subtotal = NETO cuando se
discrimina IVA, impuestos por categoría, y la diferencia del total explicada por
`monto_no_gravado`.

### Salida estructurada, no "JSON deseado"

No se le pide al modelo que devuelva JSON y se espera que salga bien: se usa
`response_format` con `json_schema` y `strict: true`, así **el formato lo impone
el servidor**. Los dos esquemas están escritos cumpliendo las reglas del modo
estricto (todas las propiedades en `required`, `additionalProperties: false`,
uniones con `anyOf`, sin `const` ni restricciones numéricas).

### Ahorro de tokens

El template del `.md` incluye el bloque con el **JSON de ejemplo de salida**
(~700 palabras). Con el esquema estricto ese bloque es redundante — el servidor
ya obliga a esa forma — así que **se omite por defecto** (77,9% menos texto de
`user` en una prueba real). `--prompt-fiel` lo incluye para comparar.

El reporte informa siempre los **tokens reales** que devuelve la API (`usage`) y
una **estimación de tokens de imagen** (fórmula de OpenAI según `--detalle`), y
persiste el **costo en USD** de cada llamada (ver *Reporte de gastos* más abajo).

### Datos cargados (`--datos`)

Acepta un JSON con **un** documento, un JSON **mapa** `clave → documento`, una
**lista**, o una **carpeta** con un `.json` por documento. La clave se aparea con
cada imagen por nombre de archivo, nombre de la carpeta contenedora (el hash del
lote) o ruta relativa. Si no encuentra datos para una imagen, lo **reporta como
error** en vez de inventar una comparación.

### Simular el costo antes de gastar (`--dry-run`)

`--dry-run` estima **cuánto costaría** la corrida y sale sin llamar a la API ni
escribir archivos. Siempre conviene empezar por acá:

```bash
# ¿Cuánto cuesta procesar el corpus completo?
python scripts/validar_comprobantes_openai.py ../procesados --modo extraer \
  --dry-run -o mendel

# Con el detalle por comprobante
python scripts/validar_comprobantes_openai.py ../procesados --modo extraer \
  --dry-run --limite 500 --detalle-log

# Guardar la estimación en JSON
python scripts/validar_comprobantes_openai.py ../procesados --modo extraer \
  --dry-run --reporte-gastos estimacion.json
```

```text
=== Estimación de costo (simulación: no se llamó a la API) ===
archivos          : 3570
tokens por archivo: entrada ≈ 2,785 (texto+esquema) + los píxeles de cada imagen | salida ≈ 240
tokens totales    : entrada 12,629,810 | salida 856,800
COSTO ESTIMADO    : US$ 40.1425   (≈ US$ 0.0112 por comprobante)
precios usados    : US$ 2.5/1M entrada, US$ 10.0/1M salida (gpt-4o)
confianza         : fórmula (3.92 car/token; medido contra la API)
```

**De dónde sale el número.** Se construye el prompt **de verdad** (system + user +
esquema) y se convierte con **3,92 caracteres/token**, medido contra el `usage`
real de la API. Los tokens de imagen se calculan con la fórmula de OpenAI según
`--detalle` y el tamaño real de cada archivo. Si la carpeta de salida ya tiene
extracciones del **mismo modelo y modo**, se **calibra con ese histórico** (que es
más fiel) y el reporte lo declara: `confianza: calibrada con N extracción(es)`. El
número nunca se presenta como una factura, sino con su fuente.

Ojo con dos cosas:

- **`--dry-run` no escribe nada.** Para estimar, corre con `--forzar` (o hacia una
  carpeta vacía), porque si no estimaría solo lo pendiente y no lo ya procesado.
- **Los precios son la tabla de referencia** (o los de `--precios`). Si tu tarifa
  real difiere, pasala explícita o la estimación no va a coincidir con la factura.

Comparar escenarios de `--detalle` es la forma rápida de ver el ahorro:

| Escenario (3.570 comprobantes) | Costo estimado |
|---|---|
| `--detalle high` (default) | US$ 40.14 |
| `--detalle low` (85 tokens fijos por imagen) | US$ 34.18 |

### Reporte de gastos (cuánto costó cada extracción)

Cada salida guarda, por documento, la **fecha y hora**, los **tokens reales** del
`usage`, los **precios aplicados** y el **costo en USD** de esa llamada. El
reporte de gastos se arma de todo el **histórico** de la carpeta de salida (no
sólo de la última corrida) y **no llama a la API**:

```bash
# En stdout: total, por día, por modelo y por modo
python scripts/validar_comprobantes_openai.py --salida validaciones

# Además, a archivo: JSON para procesar y CSV para Excel/Sheets
python scripts/validar_comprobantes_openai.py --salida validaciones \
  --reporte-gastos gastos.json --csv-gastos gastos.csv

# Excel es-AR (separador «;» y decimal «,») y zona horaria explícita
python scripts/validar_comprobantes_openai.py --salida validaciones \
  --csv-gastos gastos.csv --csv-delim ';' --csv-decimal , --tz -03:00
```

Salida típica:

```text
=== Reporte de gastos (API) ===
período           : 2026-09-10 → 2026-09-11 (UTC-03:00)
extracciones      : 4
tokens            : prompt 6,020 | completion 1,140
costo total       : US$ 0.020810

-- por día --
  2026-09-10     3 ext.      5,250 tokens  US$ 0.013710
  2026-09-11     1 ext.      1,910 tokens  US$ 0.007100

-- por modelo --
  gpt-4o                3 ext.  US$ 0.020450
  gpt-4o-mini           1 ext.  US$ 0.000360

-- por modo --
  extraer       4 ext.  US$ 0.020810
```

**El CSV tiene una fila por extracción**, con lo necesario para armar la
rendición: `fecha`, `hora`, `documento`, `modelo`, `modo`, `tokens_prompt`,
`tokens_completion`, `tokens_total`, `costo_usd`, `precio_entrada_usd_1m`,
`precio_salida_usd_1m`, `version_prompt` y `prompt_hash` (para poder auditar con
qué prompt se generó cada gasto). Se escribe con BOM (`utf-8-sig`) para que Excel
respete los acentos.

**Qué cuenta como gasto y qué no.** No se cuentan los `--dry-run`, los fallos
(un error no gastó lo que no completó) ni el modo `diff` (no llama a la API). Si
el proveedor no devolvió `usage`, el registro tampoco se cuenta: no se afirma un
gasto que no se puede respaldar.

**Precios: tabla de referencia, editable.** Los precios cambian y dependen del
modelo y de la cuenta, así que hay una tabla interna (`PRECIOS_REFERENCIA`) que
se puede pisar por CLI:

```bash
# Sólo para esta corrida
python scripts/validar_comprobantes_openai.py ../procesados \
  --precios "gpt-4o=2.5/10,gpt-4o-mini=0.15/0.6,*=1/3"

# Precios puntuales del modelo de la corrida
python scripts/validar_comprobantes_openai.py ../procesados \
  --precio-entrada 2.5 --precio-salida 10
```

⚠️ **Si un modelo no tiene precio, el costo queda `null`** y el reporte lo
declara (`⚠ SIN PRECIO … el costo real es MAYOR`) en vez de sumar un cero que
parecería exacto; el exit code pasa a `1`. Un precio a medias (sólo entrada o
sólo salida) se marca como `⚠ PARCIAL`: el total es un piso, no el total.

⚠️ Los precios se persisten **con cada extracción** (los de la corrida que la
hizo). Los registros escritos por versiones anteriores, o sin precio, se
recalculan con la tabla vigente y el apunte lo declara en `fuente_costo`.

### Banderas

| Bandera | Efecto |
|---|---|
| `--prompt` | `.md` con el prompt (default: el de `scripts/`). |
| `--modo validar\|extraer\|diff` | Qué hacer (default `validar`). |
| `--datos JSON\|DIR` | Datos cargados en Mendel (modos `validar`/`diff`). |
| `-o, --salida` | Carpeta de salida, un JSON por documento (default `validaciones`). Sin rutas, es la carpeta de la que se lee el reporte de gastos. |
| `--raiz DIR` | Raíz desde la cual se espeja el árbol. Si se omite, sube hasta el nivel que no sea mes/hash (procesar `procesados/2025-08` espeja desde `procesados`). |
| `--modelo` | Modelo (default `gpt-4o`). |
| `--detalle low\|high\|auto` | Resolución con que la API mira la imagen (default `high`). |
| `--temperatura` | Default `0.2`, como recomienda el prompt; `none` para omitirla. |
| `--max-tokens` / `--esfuerzo` | Tope de salida / `reasoning_effort` (modelos de razonamiento). |
| `--prompt-fiel` | Incluye el JSON de ejemplo de salida del template. |
| `--api-key` / `--env` | Clave explícita / archivo `.env` (default `./.env`). |
| `--forzar` | Reprocesa aunque exista la salida; sin esto **reanuda**. |
| `--workers` / `--limite` | Concurrencia (default 4) / procesar solo las primeras N. |
| `--dry-run` | **Simula el costo** y sale: no llama a la API **ni escribe**. Con `--detalle-log`, detalle por comprobante. |
| `--detalle-log` / `--reporte` | Una línea por archivo / reporte de la corrida en JSON. |
| `--reporte-gastos` / `--csv-gastos` | Reporte de gastos acumulado (JSON) / gasto por extracción (CSV). Con `--dry-run`, el JSON recibe la **estimación**. |
| `--precios` / `--precio-entrada` / `--precio-salida` | Precios USD por 1M de tokens (pisan la tabla de referencia). |
| `--tz` / `--csv-delim` / `--csv-decimal` | Zona horaria del período / separadores del CSV. |

Códigos de salida: `0` ok (o nada que hacer) · `1` hubo fallos **o hay
extracciones sin precio** · `2` error de uso o de configuración · `130`
interrumpido.

### Cosas que conviene saber

- **La aritmética se verifica en Python, no se le cree al modelo.** Al extraer,
  el script suma los importes leídos (`subtotal + no_gravado + exento + iva +
  impuestos`) y los compara con el total impreso. ⚠️ **El `cierra_aritmetica` que
  devuelve el modelo no es confiable**: en comprobantes reales declaró `true` con
  diferencias de $10,00 y $548,46. El veredicto del script queda en
  `aritmetica` (con `suma`, `total`, `diferencia` y `faltantes`) y, si no cierra,
  avisa en el log y en el resumen. Es la primera cosa a mirar cuando una
  extracción parece dudosa: suele ser una línea de importe que el modelo no
  transcribió.
- **El modo `extraer` agrega reglas de transcripción.** El template del `.md`
  está escrito para *comparar* contra datos cargados; al extraer sin datos, el
  modelo **omitía en silencio** líneas que sí estaban impresas (no capturó
  «SUBTOT. IMP. EXENTO: 10.118,12»). Por eso se le piden explícitamente todos los
  rótulos de importe y que declare en `campos_no_legibles` lo que no puede leer —
  nunca que lo deje afuera sin avisar.
- **Reanudable.** Si el JSON de salida ya existe, se saltea; `--forzar` lo rehace.
  Un archivo de salida por documento, así un lote cortado se retoma sin repagar.
  `--dry-run` no escribe nada, justamente para que no envenene esa reanudación
  (y de paso estima el costo de lo pendiente).
  ⚠️ Un registro **con error no cuenta como hecho**: se reintenta, para que
  arreglar la causa (clave, red, imagen) y volver a correr alcance (misma lección
  que los checkpoints de `batch` en T-603).
- ⚠️ **La ruta de salida no depende de cómo se invoca.** Igual que en
  `reducir_tokens.py`, la raíz de espejado **sube** hasta el nivel que no sea un
  mes (`2025-08`) ni un hash de lote (`2D2C9343`): procesar `procesados`,
  `procesados/2025-08` o `procesados/2025-08/<hash>` escribe **el mismo** archivo.
  Antes no era así: cambiar la ruta de entrada movía las salidas, la reanudación
  no las encontraba y **se volvía a pagar** por documentos ya procesados (pasó de
  verdad, con 5 documentos). Si el corpus tiene otra forma, fijá `--raiz`. Cuando
  detecta salidas de las mismas imágenes en otra ubicación, la corrida **avisa**
  antes de gastar.
- **Los fallos también se guardan** (con su registro y procedencia), para poder
  auditarlos; el resumen los lista y el exit code pasa a 1.
- **Sin fuga de credenciales.** La clave se lee del entorno o del `.env` y nunca
  se imprime ni se escribe en las salidas; los errores se clasifican en mensajes
  cortos (auth, rate limit, conexión) sin volcar trazas.
- **Trazabilidad por registro**: cada salida guarda modelo, `version_prompt`,
  `prompt_hash` (del prompt efectivamente enviado), fecha UTC, bytes y
  dimensiones de la imagen y los tokens de la llamada.
- **`temperature`**: si el modelo la rechaza (los de razonamiento no la aceptan),
  el script reintenta **una vez** sin ese parámetro y lo deja anotado.
- **Imágenes grandes**: la API acepta hasta 20 MB por imagen; si una los supera,
  el script lo informa en vez de mandar una petición condenada a fallar.

---

## `validar_comprobantes_deepseek.py` — lo mismo, con la API de DeepSeek

Es el **gemelo** del anterior: mismo prompt, mismos 3 modos, mismo formato de
salida, misma reanudación y mismo reporte de gastos. Solo cambia el proveedor, y
por eso se puede comparar la lectura de los dos modelos sobre el mismo corpus
con exactamente el mismo prompt y el mismo `diff` determinístico.

```bash
# La clave va en el entorno o en un .env (el .gitignore ya lo excluye):
# la plantilla documentada es ../.env.example, con las DOS claves de API.
export DEEPSEEK_API_KEY=sk-...
#    o:  echo 'DEEPSEEK_API_KEY=sk-...' >> .env
#    Los dos pasos, desde la plantilla:  (ver ../.env.example)
#      cp ../.env.example .env  &&  set -a && source .env && set +a

# 1) Simular (no gasta) y después extraer 5
python scripts/validar_comprobantes_deepseek.py ../procesados \
  --modo extraer --limite 5 --dry-run --detalle-log
python scripts/validar_comprobantes_deepseek.py ../procesados --modo extraer --limite 5

# 2) Validar contra los datos cargados, y el diff local (no llama a la API)
python scripts/validar_comprobantes_deepseek.py ../procesados --datos datos.json -o validaciones-deepseek
python scripts/validar_comprobantes_deepseek.py ../procesados --modo diff --datos datos.json

# 3) Reporte de gastos del histórico
python scripts/validar_comprobantes_deepseek.py --salida validaciones-deepseek
```

⚠️ **Poné las dos claves en el mismo `.env` si vas a comparar los modelos.** Los
dos scripts leen `./.env` por defecto y cada uno busca la suya
(`OPENAI_API_KEY` / `DEEPSEEK_API_KEY`), así que con las dos cargadas podés
correr la misma extracción con cada proveedor sin cambiar de entorno.

### Diferencias con la versión OpenAI (todas del proveedor)

| Tema | OpenAI (`gpt-4o`) | DeepSeek (`deepseek-flash`) |
|---|---|---|
| **Forma del JSON** | `json_schema` + `strict: true`: la impone el **servidor** | Solo `json_object` (JSON *válido*, no con la forma): se valida **local** con `jsonschema` y se **reintenta** con el error como feedback (hasta 3, declarado en `reintentos_esquema`) |
| **JSON de ejemplo del prompt** | Se **omite** (lo reemplaza el esquema estricto) | **Obligatorio**: el JSON mode de DeepSeek exige que el prompt mencione «json» y traiga el ejemplo. Default: se incluye; `--prompt-minimo` lo quita (experimento) |
| **Tokens de imagen** | Fórmula por mosaicos según `--detalle` y el tamaño real | **Tope fijo: 1.024 tokens por imagen.** DeepSeek redimensiona todo a ~1300×1300 px (y agranda las chicas a ~544×544): la resolución **no cambia** el costo |
| **`temperature`** | Se manda (default `0.2`) | **No se manda**: thinking mode la ignora. Si se pide, se **declara** que se ignoró en vez de fingir que se aplicó |
| **`reasoning_effort`** | `minimal/low/medium/high` | `none/low/high/max` (`none` apaga el thinking; default del proveedor: `high`) |
| **Caché** | No expone el dato en `usage` | `prompt_cache_hit_tokens` / `prompt_cache_miss_tokens`: el prefijo repetido (system + imagen) se cobra a la tarifa de caché |
| **Precios** | Tabla USD/1M (entrada/salida) | Tabla USD/1M con **3** valores: entrada, salida, **entrada-caché**, en tarifa **pico** (off-peak = mitad) |
| **Fallo pagado** | Un registro con `error` no se cuenta como gasto | Si el fallo fue **después de reintentar**, los tokens ya se consumieron: se cuenta y se marca `fallo: true` |
| **Límite de imagen** | 20 MB | 32 MiB (base64 inline) |
| **`--base-url`** | — | Apuntable a un proxy (`https://api.deepseek.com` por defecto) |

### Lo que se conserva igual (y se puede comparar)

- Los **3 modos** (`validar` / `extraer` / `diff`) y el `diff` **sin gastar tokens**.
- El **esquema** es exactamente el mismo objeto JSON; en DeepSeek se usa para
  **validar** la respuesta en vez de imponerla en el servidor.
- La **aritmética se recalcula en Python** (`cierra_aritmetica` + `faltantes`) y
  gana sobre el veredicto del modelo, también acá.
- La **reanudación**, el aviso de salidas en otra raíz y la verificación de
  tamaño de imagen.
- El **reporte de gastos** (por día, modelo y modo) del histórico de `--salida`.
- La clave **nunca** se imprime ni se guarda.

### Cotización

Los precios de referencia son la tarifa **pico** (01:00-04:00 y 06:00-10:00 UTC,
L-V) y el cálculo de `--dry-run` asume **sin caché**: es el techo, no el promedio.
Fuera de esa ventana DeepSeek cobra la mitad, y el caché de contexto abarata la
entrada repetida todavía más (una fracción del precio), así que el gasto real
tiende a quedar por debajo de la estimación. Se pisan con `--precios`
(`modelo=entrada/salida/caché`), `--precio-entrada`/`--precio-salida`/`--precio-cache`.
Si un modelo no tiene precio, el costo queda `null` y el reporte lo declara en
vez de sumar un cero que parecería exacto.

# `voucherflow corpus` — preparar las imágenes del corpus

Reduce el **peso** de las imágenes y, sobre todo, sus **tokens de visión**: menos
bytes en disco y en red, y menos costo cada vez que una imagen viaja a un modelo
de visión.

```bash
voucherflow corpus <ruta|carpeta>... [opciones]
```

La ayuda de la terminal es la fuente más actualizada:

```bash
voucherflow corpus --help
```

---

## Cuándo usarlo

| Situación | Conviene |
|---|---|
| El corpus se va a mandar a un VLM (qwen2.5vl, Docling VLM) | **Sí.** El costo se cobra por token de imagen. |
| Se quiere bajar el espacio en disco o respaldar más rápido | **Sí.** |
| El OCR va a ser clásico (RapidOCR/EasyOCR vía Docling) | **Medí antes.** El OCR lee píxeles: reducir puede degradar la letra chica. Ver [Cuidado con el OCR](#cuidado-con-el-ocr). |
| Los documentos son PDF con texto nativo | No hace falta. |

Lo que hace, en una frase: reduce el **lado mayor** a un objetivo (1024 px por
defecto) sin agrandar nunca, reencoda con calidad moderada, y **espeja la
estructura de carpetas** en la salida.

---

## Empezá siempre midiendo

```bash
voucherflow corpus var/files --solo-medir
```

No escribe nada: recorre, calcula y reporta qué se reduciría. Es la forma de
decidir con números antes de tocar el corpus.

```
raíz de espejado : var/files   [ascendida desde var/files/2026-08 (salteando: 2026-08)]
salida           : var/processed
imágenes         : 40
objetivo         : lado mayor <= 1024px, calidad 80, backend pillow

=== Resumen ===
archivos                  : 40
  reducidos               : 35
  omitidos                : 5
  fallos                  : 0
peso                      : 10.5 MiB  →  (no medido: simulación)
tokens de visión (estim.) : 126,960  →  36,260   (-71.4%)
modo                      : --solo-medir (no se escribió nada)
```

Dos cosas que el reporte **no** inventa y conviene saber leer:

- En `--solo-medir` el **peso destino no se mide** (no se escribió): dice
  «no medido» en vez de un `-100%` falso. Los tokens sí se comparan, porque se
  derivan de las dimensiones calculadas, no del archivo escrito.
- Los tokens son una **estimación** (`ceil(ancho/28) × ceil(alto/28)`, el
  *gridding* de Qwen2.5-VL). Sirve para comparar antes/después; no es lo que
  reporta el servidor.

⚠️ **Ojo con los escaneos 1-bit o en blanco y negro puro.** Un PNG de una sola
capa de color pesa una fracción de lo que pesa el mismo contenido en RGB. La
reducción conserva el modo del original, así que el peso baja; pero si por algún
motivo subiera, el resumen lo dice y lista los archivos que engordaron. Los
tokens de visión bajan igual: **el objetivo real de la herramienta son los
tokens**, no los bytes.

---

## La corrida real

```bash
voucherflow corpus var/files -o var/processed --workers 4
```

```
=== Resumen ===
archivos                  : 40
  reducidos               : 35
  omitidos                : 5
  fallos                  : 0
peso                      : 10.5 MiB  →  536.7 KiB   (-60.1%)
tokens de visión (estim.) : 126,960  →  36,260   (-71.4%)
```

Bajar el **60% del peso** y el **71% de los tokens** es lo típico en un corpus de
fotos de celular: son imágenes de 1500-4000 px que el modelo no necesita
mayores.

Si algún archivo **engordó** (pasa con escaneos ya optimizados), el resumen lo
avisa y lo lista, porque dentro del total queda invisible:

```
peso                      : 4.4 KiB  →  15.0 KiB   (+238.0%)

⚠  el peso SUBIÓ: la imagen original ya venía más comprimida que el reencode
   (típico de escaneos 1-bit/PNG). Los tokens de visión igual bajan; mirá
   --detalle para ver qué archivos engordaron.

--- El peso subió (1) ---
  var/files/escaneo.png: 2.2 KiB → 7.5 KiB (x3.4)
```

**Probar primero con pocas** y ver el detalle:

```bash
voucherflow corpus var/files --limite 20 --detalle
```

```
  [reducido] var/files/2026-08/002B0C61/fec99960-….jpg  1564x1920→840x1036  261.5 KiB→102.5 KiB tokens 3,864→1,110
  [omitido]  var/files/2026-08/0103B304/c5a01c3b-….jpg  893x1920→476x1036  198.7 KiB→79.1 KiB tokens 2,208→629  (el destino ya existe (usar --forzar para reescribir))
```

Cada línea muestra dimensiones y tokens antes → después, y el motivo cuando la
imagen no se tocó.

---

## Dónde queda cada archivo

**La salida espeja el árbol desde la raíz de la entrada**, sin repetir el nombre
de la carpeta de entrada. Con `var/files/2025-08/2D2C9343/foto.jpg`:

| Invocación | Archivo resultante |
|---|---|
| `var/files` | `var/processed/2025-08/2D2C9343/foto.jpg` |
| `var/files/2025-08` | `var/processed/2025-08/2D2C9343/foto.jpg` |
| `var/files/2025-08/2D2C9343` | `var/processed/2025-08/2D2C9343/foto.jpg` |
| `-o /tmp/reducido` | `/tmp/reducido/2025-08/2D2C9343/foto.jpg` |

⚠️ **La misma imagen escribe siempre el mismo archivo**, sin importar con qué
subcarpeta se invoque. La raíz **no** se deriva de la ruta que le pasás: se
**sube** hasta el primer nivel que no sea un mes (`2025-08`) ni un hash de lote
(`2D2C9343`), y el resumen lo declara:

```
raíz de espejado : var/files   [ascendida desde var/files/2026-08 (salteando: 2026-08)]
```

Esto no es un detalle cosmético: de esa regla depende la **reanudación**. Si el
nivel de carpetas cambiara entre corridas, lo ya hecho no se encontraría y
**se volvería a procesar** (en el pipeline de pago, eso es pagar dos veces). Si
tu corpus no tiene esa forma, fijá `--raiz` explícito.

---

## Qué significa cada estado

| Estado | Qué pasó | ¿Es un problema? |
|---|---|---|
| `reducido` | Se escribió una versión reducida. | No |
| `omitido` | No se tocó la imagen: ya entraba en el objetivo, o el destino ya existía. | No |
| `fallo` | Imagen ilegible o error de escritura. | Sí (exit `1`) |

Dentro de los omitidos, la **categoría** del reporte separa dos casos que
significan lo contrario para vos:

| `categoria` | Qué pasó | La salida… |
|---|---|---|
| `copia` | se escribió el archivo **sin reducir** (ya entraba en el objetivo, o cambió el formato pedido) | queda **completa** |
| `reanudado` | el destino ya existía de una corrida anterior | ya estaba, no se repitió |
| `lectura` | (solo dentro de `fallo`) no se pudo leer el original | — |

Una imagen que **ya entra** en el objetivo no se toca: no se copia ni se
reencoda. Si necesitás que la salida quede **completa** (por ejemplo, para
alimentar otra herramienta con el mismo árbol), usá `--copiar-no-reducidas`.

---

## Reanudar

El comando **es reanudable**: si el destino ya existe, lo saltea. Ese es el modo
de retomar un lote cortado — corré lo mismo otra vez.

```bash
voucherflow corpus var/files --workers 4     # cortado a la mitad
voucherflow corpus var/files --workers 4     # retoma desde donde quedó
```

Para rehacer todo, `--forzar`.

Al reanudar, el reporte **mide el archivo que existe** en vez de asumir las
dimensiones calculadas: si ese archivo se generó con otros parámetros, el resumen
lo refleja en lugar de mentir.

---

## Alineación al modelo (el punto fino)

Las dimensiones de salida se redondean a **múltiplos de 28**, que es el
`patch_size=14 × merge_size=2` de Qwen2.5-VL. El preprocesador del modelo
(*smart_resize*) hace ese mismo redondeo, y **re-escalea la imagen si no
coincide**: cuando eso pasa, el conteo de tokens deja de ser predecible.

Por eso el objetivo es **aproximado**: pedir 1024 puede dar **1036**. No es un
error de redondeo, es el precio de que el modelo no re-escale.

La alineación se calcula con el **mismo** `dimensiones_objetivo_vlm` que usa el
pipeline, así que los presupuestos de vista no pueden divergir. Los defaults
(1024 px / calidad 80 / piso 256) salen de la misma fuente, y el resumen declara
de dónde vinieron:

```
backend / defaults        : pillow / librería (voucherflow.validation.prompt_qween)
```

Usá `--sin-alinear` **solo** si el destino no es Qwen2.5-VL.

---

## Banderas

| Bandera | Qué hace |
|---|---|
| `-o, --salida DIR` | Carpeta raíz de salida (default: `var/processed`, de la configuración). |
| `--raiz DIR` | Raíz desde la cual se espeja el árbol. Fijala si el corpus no tiene forma de mes. |
| `--lado-mayor PX` | Lado mayor objetivo (default: `1024`). **Nunca agranda.** |
| `--calidad 1-100` | Calidad del reencode (default: `80`). |
| `--piso-lado-menor PX` | Piso del lado menor, para imágenes muy alargadas (default: `256`). |
| `--sin-alinear` | No alinear a múltiplos de 28 (solo si el destino no es Qwen2.5-VL). |
| `--backend {pillow,ffmpeg}` | Motor de reencode (default: `pillow`). |
| `--formato {mismo,jpg}` | `mismo` conserva la extensión; `jpg` fuerza JPEG y reescribe la extensión. || `--extensiones LISTA` | Extensiones a procesar (default: `.jpeg,.jpg,.png`). |
| `--forzar` | Reescribe el destino aunque exista (sin esto, **reanuda**). |
| `--copiar-no-reducidas` | Copia sin tocar las que ya entran en el objetivo (salida completa). |
| `--solo-medir` | No escribe nada: solo mide y reporta. |
| `--workers N` | Hilos concurrentes (default: `4`). Usá `1` si el equipo se calienta. |
| `--limite N` | Procesa solo las primeras N imágenes (`0` = todas). |
| `--detalle` | Una línea por archivo (a stderr). |
| `--reporte ARCHIVO.json` | Escribe el reporte completo (resumen + detalle por archivo) en JSON. |

---

## Códigos de salida

Los mismos del resto del CLI:

| Código | Significado |
|---|---|
| `0` | Todo bien (o no había nada que hacer). |
| `1` | Hubo **algún fallo** (una imagen ilegible alcanza). |
| `2` | Error de uso: bandera inválida, `--calidad` fuera de rango, etc. |
| `130` | Interrumpido por el usuario (Ctrl-C). |

Si no hay imágenes que procesar, sale `0` y avisa por `stderr`: no es un error.

---

## El reporte JSON

```bash
voucherflow corpus var/files --reporte var/processed/reporte.json
```

Trae el resumen **y** el detalle por archivo, así que sirve de registro de la
corrida:

```json
{
  "version": "voucherflow-corpus@2",
  "archivos": 40,
  "reducidos": 35,
  "omitidos": 5,
  "fallos": 0,
  "copiadas": 0,
  "ya_estaba": 5,
  "fallos_lectura": 0,
  "reduccion_peso_pct": 60.1,
  "reduccion_tokens_pct": 71.4,
  "opciones": {
    "salida": "var/processed",
    "lado_mayor_px": 1024,
    "calidad": 80,
    "piso_lado_menor_px": 256,
    "alinear_patch_qwen2vl": true,
    "backend": "pillow",
    "formato": "mismo",
    "forzar": false,
    "solo_medir": false,
    "copiar_no_reducidas": false,
    "workers": 4,
    "detalle": false
  },
  "archivos_detalle": [
    {
      "origen": "var/files/2026-08/002B0C61/fec99960-….jpg",
      "destino": "var/processed/2026-08/002B0C61/fec99960-….jpg",
      "estado": "reducido",
      "categoria": "",
      "motivo": "",
      "dims_origen": [1564, 1920],
      "dims_destino": [840, 1036],
      "peso_origen_bytes": 267776,
      "peso_destino_bytes": 104960,
      "tokens_origen": 3864,
      "tokens_destino": 1110
    }
  ]
}
```

Se guardan también las **opciones efectivas** de la corrida, así que el reporte
es autodescriptivo: meses después se puede saber con qué parámetros se generó esa
salida.

⚠️ **Ojo con el signo**: en el JSON el porcentaje es **positivo** (cuánto se
redujo), mientras que en la consola se muestra entre paréntesis y con signo
negativo: `(-60.1%)`.

⚠️ `-o` es **solo para las imágenes**; el reporte va aparte con `--reporte`.

---

## Cuidado con el OCR

Reducir *antes* de un OCR clásico (RapidOCR/EasyOCR vía Docling) puede degradar
la **letra chica**: el OCR lee píxeles, y el texto de un comprobante impreso es
chico. Si vas por ese camino:

```bash
voucherflow corpus var/files --solo-medir                 # ver qué pasaría
voucherflow corpus var/files --lado-mayor 1536 --workers 4   # reducir menos
```

Para el camino **VLM** (la imagen viaja al modelo) reducir es lo correcto: el
pipeline ya lo hace al enviar (vista rápida 512 / revisión 1024), así que
pre-reducir en disco **no** le quita información al modelo; solo evita guardar y
transportar píxeles que se iban a descartar.

---

## Casos de uso

```bash
# 1. Medir el corpus completo sin escribir nada
voucherflow corpus var/files --solo-medir

# 2. Probar con 20 y ver el detalle de cada una
voucherflow corpus var/files --limite 20 --detalle

# 3. Reducir el corpus, 4 hilos, con reporte
voucherflow corpus var/files -o var/processed --workers 4 \
  --reporte var/processed/reporte.json

# 4. Reducir solo un mes
voucherflow corpus var/files/2025-08 -o var/processed --workers 4

# 5. Retomar un lote cortado (sin --forzar: saltea lo hecho)
voucherflow corpus var/files -o var/processed --workers 4

# 6. Dejar la salida completa, con las que ya entraban copiadas
voucherflow corpus var/files -o var/processed --copiar-no-reducidas

# 7. Antes de un OCR clásico: reducir menos
voucherflow corpus var/files -o var/processed --lado-mayor 1536
```

---

## Notas técnicas

**Backends.** `pillow` (default) es puro Python y está en el entorno. `ffmpeg`
reproduce el loop de shell original, ya corregido; requiere un ffmpeg sano (en
macOS es común que `which ffmpeg` lo encuentre y el binario no arranque por una
dependencia rota de Homebrew). El comando hace un *preflight* y falla **una vez**
con un mensaje claro, en vez de repetir el error por archivo.

**Tres defectos del loop original que esto corrige:**

```bash
find . -type f \( -name "*.jpg" -o -name "*.png" \) | while read -r img; do
    destino="var/processed/$(dirname "$img")"
    mkdir -p "$destino"
    ffmpeg -i "$img" -vf "scale=1024:-1" -q:v 5 "$destino/$(basename "$img")" -y
done
```

1. **`scale=1024:-1` agranda las chicas.** ffmpeg escala *siempre*: una captura
   de 600 px salía a 1024 px → más peso y **más tokens** que la original. Acá una
   imagen que ya entra en el objetivo **no se toca**.
2. **`scale=1024:-1` fija el ancho, no el lado mayor.** Una foto vertical de
   3000×4000 salía 1024×1365 (~1.4 Mpx) en vez de 768×1024 (~0.79 Mpx): ~45% más
   tokens para el mismo presupuesto. Acá se reduce por **lado mayor**.
3. **Siempre escribía JPEG, incluso en un archivo `.png`** (bytes JPEG con
   nombre de PNG). Acá el formato se elige por la **extensión del destino**, así
   que `foto.webp` sale WEBP y `foto.tif` sale TIFF, o se fuerza con
   `--formato jpg`.

A eso se le suman las tres que sí eran del script: sin paralelismo, sin
reanudación y sin reporte.

**Nunca se escribe sobre la entrada.** Si el destino coincide con el origen, la
imagen se omite con un motivo explícito. La comparación es sobre rutas resueltas,
así que `a/../a/x.jpg` también se detecta.

**Dos originales no pueden escribir el mismo archivo.** Con `--formato jpg`,
`factura.jpg` y `factura.png` apuntan los dos a `factura.jpg`; también colisionan
`foto.JPG` y `foto.jpg` con el `--formato mismo`. Si eso pasara, la corrida
terminaría con un archivo y el reporte daría por reducidos a los dos. El comando
**rechaza el lote entero antes de escribir nada** (código de salida `2`) y muestra
qué originales chocan:

```
error: dos o más originales escribirían el mismo archivo de salida:

  destino: var/processed/2025-08/2D2C9343/factura.jpg
    ← var/files/2025-08/2D2C9343/factura.jpg
    ← var/files/2025-08/2D2C9343/factura.png

Se aborta sin escribir nada: seguir perdería archivos y el reporte los daría por
reducidos igual.
  · «--formato mismo» conserva la extensión de cada original.
  · «--raiz» (o «--salida») cambia el nivel desde el que se espeja.
```

**`--sin-alinear` desactiva de verdad la alineación** a múltiplos de 28, también
cuando el cálculo pasaría por la librería de Docling (que alinea siempre). Con la
bandera, un objetivo de 1000 px da 750×1000 en vez de 756×1008.

**Dónde vive el código.** `src/voucherflow/corpus/`. El recorrido y la regla de
espejado (`recorrido.py`) son **compartidos** con el pipeline
(`voucherflow.llm`), que necesita exactamente la misma regla para no pagar dos
veces un documento ya procesado.

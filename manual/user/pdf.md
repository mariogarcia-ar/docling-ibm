# `voucherflow pdf` — convertir PDF a imágenes

Convierte las **páginas** de un PDF en imágenes JPG. Es la pieza que permite
*mirar* un PDF, armar un corpus de imágenes y —sobre todo— **darle PDF a los
modelos de visión**, que reciben imágenes y no documentos.

```bash
voucherflow pdf <ruta|carpeta>... [opciones]
```

La ayuda de la terminal es la fuente más actualizada:

```bash
voucherflow pdf --help
```

---

## Cuándo usarlo

| Situación | Conviene |
|---|---|
| Hay que mandar un PDF a un VLM (el lab, un modelo de visión) | **Sí.** Es su motivo. |
| Se quiere ver qué contiene un PDF, o revisar uno por uno | **Sí.** Cualquier visor de imágenes lo abre. |
| Un lote que el pipeline va a procesar | **No.** `voucherflow process` ya renderiza lo que necesita y no conserva las imágenes. |
| Bajar el precio de las imágenes antes de mandarlas | Después: `voucherflow corpus` (ver `corpus.md`). |
| El PDF tiene texto seleccionable y se puede leer con texto | **Probablemente no.** Ver [PDF de texto nativo](#pdf-de-texto-nativo). |

---

## La regla que importa: recortar o no recortar

Es la decisión que más cambia el resultado, y la que el comando resuelve solo.

Cuando un comprobante está **escaneado**, la imagen grande de la página *es* el
documento. Pero un escaneo suele ser un ticket chico centrado en una hoja A4: a
página completa queda diminuto y ningún OCR lo lee. Para eso existe el **recorte**
a la imagen más grande de la página.

El problema es que esa regla aplicada **siempre** falla en el otro caso: en un PDF
**generado por sistema** (con texto seleccionable), la imagen más grande de la
página suele ser el **logo** del emisor, no el comprobante. Recortar ahí devuelve
un logo.

Medido sobre el corpus real —264 PDF— la diferencia es la mayoría de los casos:

| | PDF | Qué es la imagen más grande |
|---|---|---|
| **Nacidos digitales** | 236 | el **logo** (recortar devuelve un logo de 16×6 pt) |
| **Escaneados** | 28 | la **página** (recortar es lo correcto) |

Por eso el default **no** es una política fija: mira cada página, y recorta solo
si la imagen cubre al menos la mitad de la hoja. En los mismos 264 PDF, la
cobertura es **bimodal con un hueco limpio** (todos por debajo de 0,36 o por
encima de 0,83), así que la frontera no depende de un valor ajustado a mano.

El mismo PDF, con las tres políticas:

| Política | Resultado | Peso |
|---|---|---|
| *(default)* automático | página completa (2481×3508) | 470 KiB |
| `--recortar` | el logo, ampliado (2001×1601) | 145 KiB |
| `--sin-recortar` | página completa (2481×3508) | 470 KiB |

⚠️ **Ese PDF pesa menos con `--recortar` y es la peor salida posible**: 145 KiB de
un logo. El peso no dice si la imagen sirve.

Las dos banderas existen para forzar el comportamiento, no para elegirlo en cada
corrida: **el default es el correcto en los dos universos**.

---

## Convertir un PDF

```bash
voucherflow pdf comprobante.pdf
```

```
raíz de espejado : tests/fixtures/chicos   [derivada de las rutas]
salida           : var/paginas
plan             : 1 PDF → 1 página(s) a renderizar
render           : 300 dpi, calidad 95, recorte automático (por página)
  escrito: var/paginas/pagina_1.jpg

=== Resumen ===
páginas                   : 1
  escritas                : 1
peso total               : 470.4 KiB
salida                    : var/paginas
```

**Con un solo PDF** el archivo se llama `pagina_1.jpg`, `pagina_2.jpg`… y no lleva
el nombre del documento: es el nombre que usaba el script del que viene esta
herramienta, y con un único archivo no hay ambigüedad posible.

---

## Convertir una carpeta

```bash
voucherflow pdf var/files -o var/paginas
```

```
⚠  3582 archivo(s) fuera (no son PDF: .jpg ×3545, .jpeg ×26, .png ×11)
raíz de espejado : var/files   [derivada de las rutas]
salida           : var/paginas
plan             : 264 PDF → 441 página(s) a renderizar
render           : 300 dpi, calidad 95, recorte automático (por página)
  escrito: var/paginas/2025-08/2D2C9343/comprobante_pagina_1.jpg
  …
  … 50/441
  … 100/441
  …

=== Resumen ===
páginas                   : 441
  escritas                : 441
peso total               : 275.8 MiB
salida                    : var/paginas
```

Los 264 PDF tardan **32 segundos** y ocupan **276 MiB** de JPEG. Ese peso es el
motivo por el que `voucherflow corpus` existe: después de reducirlas, las mismas
441 páginas bajan a una fracción (y con eso los tokens de visión, si el destino es
un modelo).

**Con varios PDF** el nombre **sí** lleva el del documento
(`comprobante_pagina_1.jpg`): sin eso, dos PDF llamados `comprobante.pdf` en meses
distintos escribirían el mismo archivo y uno de los dos se perdería.

⚠️ Un PDF de 3 páginas produce **3 imágenes** y cada una es un archivo propio. En
el corpus real, **100 de los 264 PDF tienen más de una página** (441 páginas en
total): el plan dice `264 PDF → 441 página(s)`, y conviene mirar ese número antes
de lanzar el lote.

---

## Dónde queda cada archivo

**La salida espeja el árbol desde la raíz de la entrada**, sin repetir el nombre
de la carpeta de entrada. Con `var/files/2025-08/2D2C9343/comprobante.pdf`:

| Invocación | Archivos resultantes |
|---|---|
| `var/files` | `var/paginas/2025-08/2D2C9343/comprobante_pagina_1.jpg`… |
| `var/files/2025-08` | `var/paginas/2025-08/2D2C9343/comprobante_pagina_1.jpg`… |
| `var/files/2025-08/2D2C9343` | `var/paginas/2025-08/2D2C9343/comprobante_pagina_1.jpg`… |
| `-o /tmp/pdf` | `/tmp/pdf/2025-08/2D2C9343/comprobante_pagina_1.jpg`… |

⚠️ **La misma entrada escribe siempre los mismos archivos**, sin importar con qué
subcarpeta se invoque. La raíz **no** se deriva de la ruta que le pasás: se
**sube** hasta el primer nivel que no sea un mes (`2025-08`) ni un hash de lote
(`2D2C9343`), y el resumen lo declara:

```
raíz de espejado : var/files   [ascendida desde var/files/2026-08 (salteando: 2026-08)]
```

No es cosmético: de esa regla depende la **reanudación**. Si el nivel de carpetas
cambiara entre corridas, lo ya hecho no se encontraría y se volvería a renderizar.

La salida por defecto es `var/paginas`, bajo la carpeta de datos del proyecto
(que git ignora). Igual que `corpus`, se puede fijar absoluta en
`voucherflow.yaml`.

---

## Reanudar

Sin `--forzar`, un archivo que ya existe **y no está vacío** se saltea:

```bash
voucherflow pdf var/files -o var/paginas      # segunda corrida
```

```
=== Resumen ===
páginas                   : 441
  escritas                : 0
  ya existían             : 441
```

Un lote cortado con Ctrl-C se retoma corriendo lo mismo. `--forzar` re-renderiza
todo (útil si cambió el `--dpi`, la calidad o la política de recorte).

---

## Empezá midiendo

```bash
voucherflow pdf var/files --solo-medir
```

No escribe nada: recorre, cuenta páginas y muestra los destinos que usaría.

```
⚠  3582 archivo(s) fuera (no son PDF: .jpg ×3545, .jpeg ×26, .png ×11)
raíz de espejado : var/files   [derivada de las rutas]
salida           : var/paginas
plan             : 264 PDF → 441 página(s) a renderizar
render           : 300 dpi, calidad 95, recorte automático (por página)
modo             : --solo-medir (no se escribe nada)

=== Resumen ===
páginas                   : 441
  se escribirían          : 441
```

El plan y la corrida real calculan el destino con **la misma función**, así que
lo que anuncia la simulación es exactamente lo que va a escribir.

---

## Banderas

| Bandera | Qué hace |
|---|---|
| `-o, --salida DIR` | Carpeta de salida (default: `var/paginas`, de la configuración). |
| `--raiz DIR` | Raíz desde la cual se espeja el árbol. Fijala si el corpus no tiene forma de mes. |
| `--dpi DPI` | Resolución del render (default: `300`). |
| `--calidad 1-100` | Calidad JPEG (default: `95`). |
| `--recortar` | Recorta **siempre** a la imagen más grande de la página. |
| `--sin-recortar` | Renderiza la página completa, **siempre**. |
| `--primera N` | Primera página a convertir, 1-based (default: la 1). |
| `--ultima N` | Última página a convertir, inclusive (default: la última del PDF). |
| `--patron PLANTILLA` | Nombre de cada imagen. Tokens: `{nombre}`, `{pagina}`, `{total}`. |
| `--forzar` | Re-renderiza aunque el archivo exista (sin esto, reanuda). |
| `--solo-medir` | Muestra qué escribiría, sin crear nada. |
| `--limite N` | Convierte solo las primeras N páginas (`0` = todas). |

### El DPI

`300` es el default porque es el que usa el pipeline para OCR: si cambiara, el
mismo PDF se leería con distinta resolución según el comando. Un A4 a 300 dpi sale
2481×3508 px.

### El peso, medido

En un A4 a 300 dpi, con el default de calidad:

| `--dpi` | `--calidad` | Dimensiones | Peso |
|---|---|---|---|
| `300` | `95` *(default)* | 2481×3508 | 460 KiB |
| `300` | `85` | 2481×3508 | 353 KiB |
| `300` | `60` | 2481×3508 | 261 KiB |
| `150` | `95` | 1241×1754 | 180 KiB |

Bajar el DPI achica los lados de la imagen (y con eso los tokens de visión, si el
destino es un modelo); bajar la calidad solo comprime más. Para un OCR clásico,
**el DPI es lo que no conviene bajar**: el OCR lee píxeles.

### El patrón de nombres

Con un PDF, `--patron` permite nombrar distinto:

```bash
voucherflow pdf comprobante.pdf --patron "c{pagina}_de_{total}.jpg"
# c1_de_3.jpg  c2_de_3.jpg  c3_de_3.jpg
```

⚠️ La plantilla **no puede contener separadores de carpeta** (`../`): escribiría
fuera de la carpeta de salida y el comando lo rechaza.

---

## PDF de texto nativo

⚠️ **Si el PDF se puede leer como texto, rasterizarlo es perder información.**
Las 236 páginas nacidas digitales del corpus tienen el texto seleccionable: pasarlo
por OCR es una degradación innecesaria, y además se paga.

Para esos documentos el camino es el pipeline:

```bash
voucherflow process comprobante.pdf      # usa el texto nativo; renderiza solo si hace falta
```

La excepción es cuando el destino **es un modelo de visión**. Un VLM no recibe
texto, recibe píxeles: ahí `voucherflow pdf` es la herramienta correcta, aunque el
PDF tenga texto. Para el laboratorio, de hecho, no hace falta llamarlo: el lab
renderiza los PDF de su lote por su cuenta.

---

## Códigos de salida

Los mismos del resto del CLI:

| Código | Significado |
|---|---|
| `0` | Todo bien (o no había nada que hacer). |
| `1` | Alguna **página falló** (el resto sí se escribió). |
| `2` | Error de uso: ruta inexistente, carpeta sin ningún PDF, patrón inválido, rango de páginas vacío. |
| `130` | Interrumpido por el usuario (Ctrl-C). |

Dos aclaraciones que el código no dice solo:

- **Un PDF ilegible no aborta el lote.** Se declara y se sigue:

  ```
  ⚠  1 PDF no se pudieron leer:
       roto.pdf: no se pudo leer (Failed to open file)
  ```

  Sale `0` si el resto se pudo convertir: el archivo roto es un problema del
  corpus, no del comando.

- **Un rango de páginas vacío sí es error de uso** (código `2`), porque no se hizo
  nada de lo pedido:

  ```
  error: comprobante.pdf: el rango pedido sale vacío (se pidió 9-1, el PDF tiene 1 página(s))
  error: no hay ninguna página que convertir
  ```

---

## Casos de uso

```bash
# 1. Ver qué tiene un PDF, páginas 1 y 2
voucherflow pdf comprobante.pdf -o /tmp/ver

# 2. Ver antes de escribir nada
voucherflow pdf var/files --solo-medir

# 3. Convertir el corpus completo
voucherflow pdf var/files -o var/paginas

# 4. Un mes, con 4 hilos no aplica: el render es secuencial (ver Notas técnicas)
voucherflow pdf var/files/2025-08 -o var/paginas

# 5. Retomar un lote cortado (sin --forzar: saltea lo hecho)
voucherflow pdf var/files -o var/paginas

# 6. Más chico para ahorrar disco (ojo con la letra chica)
voucherflow pdf var/files -o var/paginas --dpi 150 --calidad 80
# 7. Forzar el recorte, para un corpus que se sabe escaneado
voucherflow pdf var/escaneos -o var/paginas --recortar

# 8. Solo la primera página de cada PDF
voucherflow pdf var/files -o var/first --ultima 1

# 9. Nombres propios
voucherflow pdf comprobante.pdf -o /tmp/x --patron "hoja{pagina}.jpg"
```

---

## Notas técnicas

**Motor.** Usa **PyMuPDF**, que ya es dependencia del paquete. Antes esto era un
script con **`pdf2image`** (poppler), que tenía dos problemas: era una dependencia
de facto **sin declarar** (nadie la requería) y arrastraba un binario de sistema
con un `fork` por página. Además era una **segunda implementación** del render, del
espejado y del recorte que ya existían en el paquete —y que podía divergir.

⚠️ **El render y la codificación del JPEG usan motores distintos, a propósito**
(medido, mismo A4 a 300 dpi):

| Etapa | Motor | Tiempo |
|---|---|---|
| Renderizar la página | PyMuPDF (`get_pixmap`) | 0,014 s |
| *(con poppler, para comparar)* | `pdf2image` | 0,111 s |
| Codificar el JPEG | **Pillow** | 0,014 s |
| Codificar el JPEG | PyMuPDF | **0,176 s** |

O sea: **conviene renderizar con PyMuPDF y codificar con Pillow**, que es lo que
hace el comando (0,057 s por página en total, con la escritura atómica incluida).
Usar el codificador de PyMuPDF para las dos cosas costaría 0,196 s: **la misma
imagen, tres veces y media más lenta**. El detalle está comentado en el código,
en `render_pdf_a_jpg`, porque es contraintuitivo.

⚠️ **Y no es más rápido que poppler**: renderizar sí (0,014 contra 0,111 s), pero
poppler ya trae el JPEG codificado. El motivo para soltarlo no fue la velocidad,
**fue quitar una dependencia sin declarar y una segunda implementación** de reglas
que ya estaban en el paquete.

**El render es la misma pieza que el pipeline.** No hay dos motores: `voucherflow
pdf`, `voucherflow process` y el laboratorio renderizan con la misma función
(`render_pdf_a_jpg`). Por eso el recorte, el DPI y el tratamiento del color no
pueden divergir entre lo que ves y lo que lee el pipeline.

**La escritura es atómica.** Cada imagen se escribe en un temporal del mismo
directorio, con `fsync`, y recién ahí se renombra. Un `Ctrl-C` a mitad de render
no deja un JPEG truncado con el nombre final, que la reanudación daría por bueno.

**Es secuencial.** No tiene `--workers` como `corpus`: medido, renderizar una
página cuesta ~0,2 s y el cuello de botella real es el disco. Si un corpus grande
lo justifica, paralelizarlo es una tarea aparte.

**Rendimiento de referencia** (corpus real): las 441 páginas de los 264 PDF tardan
**32 segundos** y pesan **276 MiB**. Un A4 a 300 dpi pesa ~460 KiB.

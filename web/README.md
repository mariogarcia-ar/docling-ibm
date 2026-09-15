# `web/` — visor de fixtures y extracciones

Herramienta de inspección: explora `tests/fixtures/` y muestra, al lado de cada
documento, la extracción que el laboratorio dejó en `tests/fixtures-extraction/`.

No es parte del producto (el paquete es `src/voucherflow/`) ni de la suite de
tests. Es una **herramienta de operación**, como las de `scripts/`: existe para
mirar con los ojos lo que los tests miden con números.

> **Por qué existe.** El laboratorio ya deja todo en disco y los tests verifican
> la paridad, pero para *entender* una lectura hay que ver la imagen y los campos
> juntos. Sin esto la comparación se hace a mano, abriendo el JPEG en un visor y
> el JSON en otro.

---

## 1. Correrlo

```bash
php -S 127.0.0.1:8080 -t web
```

Abrir <http://127.0.0.1:8080>. Requiere **PHP ≥ 8.1** (usa `never`, `str_starts_with`
y `mixed`) y **nada más**: sin Composer, sin dependencias, sin build.

⚠️ El `-t web` es obligatorio. `tests/fixtures` está **fuera** del docroot, así que
el servidor embebido de PHP no puede servirlo solo: los bytes viajan por
`archivo.php`, que es la única puerta hacia esas carpetas.

---

## 2. Las dos pantallas

| Archivo | Qué es |
|---|---|
| `index.php` | El listado: un bloque por grupo, una fila por documento, con su estado de extracción. |
| `documento.php?doc=<grupo>/<archivo>` | La ficha: el documento a la izquierda, la extracción a la derecha, y la barra de navegación abajo. |
| `archivo.php?c=fixtures\|extracciones&ref=<grupo>/<archivo>` | Sirve los bytes. Valida la ruta contra traversal. |
| `assets/visor.css`, `assets/visor.js` | Estilo, el filtro de campos vacíos y los atajos de teclado. |

**El listado no esconde lo que no cerró.** Las extracciones que no se pueden atar a
ningún documento aparecen en un bloque aparte, con el campo que se usó para
buscarlas y el motivo del fallo. Un documento sin extracción se muestra en gris,
con el comando exacto para generarla.

**En la ficha**, cada extracción muestra: banderas arriba (lo que se mira primero:
la clase documental, legibilidad, tipo, CUIT, total, si cierra la aritmética), la
tabla de los 27 campos con su clave cruda del JSON al lado, la procedencia (modelo,
prompt, costo, tokens, la aritmética recalculada en código) y el JSON crudo
plegable.

Un PDF multipágina pagó **una extracción por página**: la ficha apila un bloque por
página y el documento queda **fijo** en su columna al hacer scroll, para poder
comparar sin volver a subir. El medio se embebe **una sola vez** (un PDF grande
incrustado N veces es N veces la descarga).

---

## 3. Recorrer los documentos

La barra de abajo permite avanzar y volver sin pasar por el listado: 94 documentos
a mano, volviendo al índice cada vez, no se recorren.

| Cómo | Qué hace |
|---|---|
| `← anterior` / `→ siguiente` | Va al documento vecino. |
| Teclas `←` y `→` | Lo mismo, sin tocar el mouse. |
| `1 / 94` | Dónde estás en la secuencia. |
| El nombre al lado del botón | A dónde vas, **antes** de ir. |

El orden es el del listado: grupos alfabéticos y, dentro de cada uno, los
documentos por nombre. Se recorre la secuencia entera, cruzando de un grupo al
siguiente, así que no hay que volver al índice al terminar `chicos/`.

Decisiones que no son obvias:

- **En los extremos el botón se deshabilita, no da la vuelta.** Un «anterior» en el
  primer documento que saltara al último rompe la lectura de dónde estás.
- **El destino se muestra al lado del botón**, no en un `title`: en un recorrido
  rápido hay que saber a dónde se va sin pasar el mouse.
- **Los botones son `<a>`**, no `<button>` con JS: el recorrido funciona sin
  JavaScript, se puede abrir en otra pestaña y el navegador muestra el destino en
  la barra de estado. El JS solo **agrega** las teclas, y lee el destino del `href`
  que emitió el servidor — así el atajo no puede desincronizarse de la barra.
- **Las teclas no pisan lo que estás escribiendo.** Con el foco en un campo de
  texto, `←`/`→` mueven el cursor y no cambian de documento. La guarda mira el
  **tipo** del control: un checkbox no consume las flechas, así que la casilla
  «Mostrar campos sin dato» (el control que más se usa acá) no deja las flechas
  muertas.

---

## 4. Cómo se empareja un documento con su extracción

Es la parte con más casos raros del visor, y no es obvia. Medido sobre el corpus
real (**95 extracciones / 94 documentos**), el nombre del JSON **no** coincide con
el del documento en 53 casos, por tres causas distintas:

1. **`archivo_relativo` no tiene una forma única.** Para `otros/` y `negativos/`
   viene con la carpeta (`negativos/x.jpg`); para el resto viene con el basename
   pelado (`x.jpg`). Un emparejador que asuma una sola forma pierde la mitad.
2. **Los PDF multipágina.** El laboratorio nombra una extracción por página
   (`x_p01.extraccion.json`, `x_p02…`) y el `origen` de ésas apunta a un render
   temporal (`lab_pdf_<hash>/x_p01.jpg`) que **ya no existe**. Sin despojar el
   `_pNN` del stem, esas extracciones no se atan a nada.
3. **Basenames repetidos en grupos distintos.** `a6d79e19….png` está en `chicos/`
   **y** en `golden/`. El basename solo no alcanza: desempata el grupo de la
   carpeta donde vive la extracción.

`catalogo_emparejar()` intenta, en este orden: la ruta completa del `origen` → el
`archivo_relativo` (con y sin grupo) → el basename → el basename sin `_pNN`.

⚠️ **Si el desempate no es concluyente, devuelve `null` en vez de elegir.** Un
emparejamiento inventado mostraría una lectura que no corresponde al documento, que
es peor que decir «no sé». Lo no resuelto sale como huérfano.

Resultado actual: **95/95 emparejadas, 0 huérfanas**. Si un cambio del laboratorio
rompe alguna, deja de estar en silencio: aparece el bloque de huérfanas.

---

## 5. Decisiones que no son de estilo

- **`null` ≠ `0`.** El laboratorio usa `null` para «no figura / no legible» y `0`
  para «figura y vale cero» (el caso medido: el monto 0 que no cierra la
  aritmética). Un `if (!$valor)` colapsaría los dos y borraría esa distinción de la
  pantalla. `presentacion_leido()` pregunta por `null`, nunca por truthiness.
- **Una lista vacía es una respuesta, no un dato faltante.** `campos_no_legibles: []`
  significa «no hubo ninguno» — el mejor resultado posible. Se muestra como
  «ninguno» y **no** se esconde con el filtro. Sin esto, un documento perfecto se
  veía igual que uno con todo ilegible.
- **El filtro se llama igual en los tres lados.** La clase es `fila-sin-dato` en
  `vista.php`, en `visor.js` y en `visor.css`. Un nombre abreviado en cualquiera de
  los tres apaga el filtro **en silencio** (pasó al escribirlo: el HTML emitía
  `no-leido` y el JS buscaba `fila-no-leido`; el checkbox no hacía nada).
- **Etiquetas traducidas, no derivadas del nombre técnico.** `cuit_emisor` es «CUIT
  del emisor», no «Cuit Emisor». La tabla de `presentacion.php` sale del esquema
  real (`src/voucherflow/llm/esquemas.py`, `esquema_extraccion`); si el esquema
  cambia, es el archivo a mirar. Un campo nuevo del laboratorio **no desaparece**:
  cae al final con su clave cruda como etiqueta.
- **Un vocabulario cerrado con tres estados no es un texto ni un booleano.** Ver
  `es_comprobante`, abajo.
- **El tema oscuro es el default** y el claro se activa con `prefers-color-scheme`.
  Contrastar una factura clara contra un fondo oscuro es más cómodo y evita el
  flash blanco al abrir.
- **Cada flecha se ata a su botón por posición** (`[0]` anterior, `[1]` siguiente),
  no buscando entre los que tienen `href`. Lo segundo parece más robusto y es lo
  contrario: en el primer documento no hay «anterior», así que el único con `href`
  es «siguiente» y `←` **avanzaba**. Medido, y con un comentario que afirmaba la
  garantía que el código no daba.
- **`archivo.php` valida de más.** Rechaza `..`, rutas absolutas y todo lo que
  resuelva fuera de la raíz; un fallo es siempre 404 (distinguir «no existe» de
  «no permitido» filtraría información del disco). `Cache-Control: no-cache` +
  `ETag`, porque los `.extraccion.json` **se regeneran** y una copia sin revalidar
  mostraría una lectura vieja.

### El campo `es_comprobante` (la clase documental)

Es el campo que responde **qué es** el documento antes de *qué dice*, y tiene tres
estados que **no** son un sí/no:

| Valor | En la ficha | En el listado | Tono |
|---|---|---|---|
| `comprobante` | sí | *(se omite)* | verde |
| `no_comprobante` | no | no es comprobante | rojo |
| `indeterminado` | no se pudo decidir | no se pudo decidir | **ámbar** |

Decisiones que no son de estilo:

- **Va primero, en las banderas y en la tabla.** Todas las demás banderas (tipo,
  CUIT, total, aritmética) presuponen que el documento es un comprobante. Ponerlo
  al final obligaba a leer seis banderas vacías para llegar a la que explica por
  qué están vacías.
- **`indeterminado` es ámbar, no rojo.** `no_comprobante` es una **respuesta
  firme** —el documento no acredita la operación— y no un error de la herramienta.
  `indeterminado` es distinto: la imagen no alcanzó y **pide una acción** (volver a
  mirar el documento), así que es el único de los tres que no debería pasar sin que
  alguien lo vea. Es el primer uso real de `--medio`, que estaba declarado en el CSS
  sin consumidores.
- **El texto del listado no es el de la ficha.** En la ficha el valor va debajo del
  rótulo «Es un comprobante», así que un «no» se lee solo. En el listado va suelto
  en una columna: ahí dice **«no es comprobante»**, porque un «no» pelado no dice
  *no qué*. Son dos columnas de la **misma** tabla, para que no puedan divergir.
- **El resumen del listado lo antepone.** Sin eso, una fila de un negativo mostraba
  el texto libre de las observaciones, que empieza igual en todos: había que abrir
  la ficha para saber que la respuesta era «no es un comprobante».
- **Un valor fuera del vocabulario no se colorea.** Si el modelo devuelve algo que
  no está en los tres estados, se muestra crudo y sin tono: pintarlo de verde o de
  rojo afirmaría algo que no se sabe.

⚠️ **El vocabulario es el del contrato** (`schemas.evidence.ClaseDocumento`), el
mismo que usa el gate del pipeline. Si acá se inventara una traducción distinta, el
visor mentiría sobre el dato que compara el resto del sistema.

### El alto del encabezado se mide, no se adivina

La barra superior es `sticky` y su subtítulo pasa a una **segunda línea** en
ventanas angostas, así que su alto cambia:

| Ancho de ventana | Alto del encabezado |
|---|---|
| ≥ ~502px (subtítulo en una línea) | **52px** |
| ≤ ~500px (subtítulo en dos líneas) | **86px** |

Todo lo que se pega debajo de ella —la cabecera de la tabla de campos y la columna
del documento en la vista multipágina— fija su `top` con `--alto-encabezado`, y el
`scroll-margin-top` de los grupos del listado usa `--offset-bajo-encabezado`
(derivado: `alto + 18px`). **Un solo lugar define el número**; los tres sitios lo
heredan, así que no pueden desincronizarse.

⚠️ **Ese alto NO se puede fijar con una media query.** El punto de quiebre depende
del renderizado de la fuente, no solo del ancho: se midió primero en 700px, después
en 502px. Un breakpoint cableado estaba **200px corrido** y el desfase solo se veía
en una franja angosta de anchos. Por eso lo **mide `visor.js`** con un
`ResizeObserver` y lo escribe como variable; el valor del CSS es el fallback para
cuando el JS no corre (verificado: acierta igual sin JS).

### Trampa: `overflow: hidden` anula `position: sticky`

`.tabla-scroll` envolvía la tabla con `overflow: hidden` para recortar las esquinas
redondeadas. Eso crea un contenedor de scroll y **desactiva el `position: sticky`**
de la cabecera, que se deslizaba por debajo de la barra superior y quedaba tapada
por la primera fila — el síntoma reportado («una fila tapa el detalle»).

Se usa **`overflow: clip`**, que recorta igual sin crear el contenedor de scroll.
Medido con las tres opciones: `hidden` rompe el sticky, `auto` también, `clip` no.

⚠️ **No «arreglar» el desborde horizontal de la tabla** dándole `overflow-x: auto`
a ese contenedor: vuelve a crear el contenedor de scroll y rompe el sticky otra
vez.

### El ancho de la tabla de campos

La tabla entra en su contenedor a **cualquier ancho** gracias a
`table-layout: fixed` con anchos en **porcentaje**. Sin eso, el layout automático
toma el ancho de contenido de cada celda como piso, los pisos no se negocian y en
500px la tabla desbordaba 36px (156px a 380px) — un desborde que además era
**inalcanzable**, porque el contenedor recorta en vez de scrollear.

⚠️ Con `table-layout: fixed` **un `min-width` en `em` o un `width: max-content`
dentro de una celda no se respeta**: la única forma de que una columna no se
aplaste es un ancho en porcentaje. Probé los dos caminos por separado (un `em` de
piso, `max-content`) y los dos dejaban desborde en algún ancho; el reparto por
porcentaje no deja ninguno en los 11 anchos medidos (320px a 1400px).

⚠️ **La trampa que causó el bug reportado** («los campos están todos rotos»):
`overflow-wrap: anywhere` en una celda cuyo ancho depende de su contenido. Combinado
con quitarle el `white-space: nowrap` a la columna de la clave, esa columna colapsó
a **7px** y cada fila se estiró a 247px —y una a 645px, 32 líneas de una letra—.
Con `table-layout: fixed` el `overflow-wrap` es necesario y seguro: el ancho ya no
depende del contenido, así que partir un token largo no aplasta la columna.

---

## 6. Qué NO hace

- **No edita nada.** Es de solo lectura: no hay correcciones HITL ni re-extracción
  desde acá.
- **No genera extracciones.** Solo lee lo que el laboratorio escribió.
- **No reemplaza a los tests.** Su verificación de referencia es
  `scripts/verificacion/acuerdo-extraccion.py` y la suite. Acá se mira; allá se mide.
- **No sirve para publicar.** Sin autenticación, sin TLS, y el `archivo.php` da
  acceso de lectura a todo `tests/`. Es para `127.0.0.1`.

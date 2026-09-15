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
| `documento.php?doc=<grupo>/<archivo>` | La ficha: el documento a la izquierda y la extracción a la derecha. |
| `archivo.php?c=fixtures\|extracciones&ref=<grupo>/<archivo>` | Sirve los bytes. Valida la ruta contra traversal. |
| `assets/visor.css`, `assets/visor.js` | Estilo y el único JS (el filtro de campos vacíos). |

**El listado no esconde lo que no cerró.** Las extracciones que no se pueden atar a
ningún documento aparecen en un bloque aparte, con el campo que se usó para
buscarlas y el motivo del fallo. Un documento sin extracción se muestra en gris,
con el comando exacto para generarla.

**En la ficha**, cada extracción muestra: banderas arriba (lo que se mira primero:
legibilidad, tipo, CUIT, total, si cierra la aritmética), la tabla de los 26 campos
con su clave cruda del JSON al lado, la procedencia (modelo, prompt, costo, tokens,
la aritmética recalculada en código) y el JSON crudo plegable.

Un PDF multipágina pagó **una extracción por página**: la ficha apila un bloque por
página y el documento queda **fijo** en su columna al hacer scroll, para poder
comparar sin volver a subir. El medio se embebe **una sola vez** (un PDF grande
incrustado N veces es N veces la descarga).

---

## 3. Cómo se empareja un documento con su extracción

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

## 4. Decisiones que no son de estilo

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
- **El tema oscuro es el default** y el claro se activa con `prefers-color-scheme`.
  Contrastar una factura clara contra un fondo oscuro es más cómodo y evita el
  flash blanco al abrir.
- **`archivo.php` valida de más.** Rechaza `..`, rutas absolutas y todo lo que
  resuelva fuera de la raíz; un fallo es siempre 404 (distinguir «no existe» de
  «no permitido» filtraría información del disco). `Cache-Control: no-cache` +
  `ETag`, porque los `.extraccion.json` **se regeneran** y una copia sin revalidar
  mostraría una lectura vieja.

---

## 5. Qué NO hace

- **No edita nada.** Es de solo lectura: no hay correcciones HITL ni re-extracción
  desde acá.
- **No genera extracciones.** Solo lee lo que el laboratorio escribió.
- **No reemplaza a los tests.** Su verificación de referencia es
  `scripts/verificacion/acuerdo-extraccion.py` y la suite. Acá se mira; allá se mide.
- **No sirve para publicar.** Sin autenticación, sin TLS, y el `archivo.php` da
  acceso de lectura a todo `tests/`. Es para `127.0.0.1`.

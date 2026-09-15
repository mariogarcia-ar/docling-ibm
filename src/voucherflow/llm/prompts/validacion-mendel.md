# Prompt de validación de comprobantes — Mendel (primera aprobación)

> **El prompt vive en [`validacion-mendel.yaml`](validacion-mendel.yaml).** Este
> documento lo explica: de dónde salió, qué decide cada regla y qué se puede
> tocar sin romper la comparación.

**Origen**: el mail de Agustín del 31/08/2026, en respuesta al pedido de FlowBoss.

**Objetivo**: que un LLM con visión reciba **la imagen del comprobante** + **los
datos que el empleado cargó en Mendel**, y determine si cada campo controlado en
la primera aprobación es correcto, corregible o requiere revisión humana.

---

## Cómo se lee este prompt

El archivo YAML tiene tres claves, y **no todas llegan al modelo de la misma
forma**:

| Clave | Qué es | Cuándo llega al modelo |
|---|---|---|
| `system` | Las 15 reglas de negocio y los criterios del auditor | **Siempre** || `user` | El pedido + el **template** del JSON de entrada | Siempre, con los datos reales **sustituidos** |
| `ejemplo_salida` | El formato de la respuesta | Según el proveedor (ver abajo) |

⚠️ **Los valores del `user` son un template, no datos.** El bloque `{…}` se
reemplaza **entero** en runtime por los datos reales de Mendel: editar `<texto>`,
`<DD/MM/AAAA>` o `<ARS|USD>` **no cambia nada** de lo que se manda. Lo que sí
importa ahí es el texto del pedido y los **nombres** de las claves.

### Cuándo se usa el ejemplo de salida

Depende de si el proveedor **impone** el esquema en el servidor:

| Proveedor | Forma de la respuesta | El ejemplo |
|---|---|---|
| OpenAI (`json_schema` + `strict`) | La garantiza el servidor | **Se recorta**: es redundante |
| DeepSeek / Gemini | Depende del prompt | **Viaja**, o se genera del esquema |

En el modo `extraer` el ejemplo del YAML se **reemplaza** por uno generado del
esquema que valida. Es a propósito: el ejemplo escrito a mano puede divergir del
validador (fue un bug real — el ejemplo del template era el de *comparar* y el
modelo lo copiaba), y generarlo del mismo esquema hace que no puedan separarse.

---

## Las 15 reglas de negocio

No son instrucciones de formato: son **decisiones de dominio** que no se pueden
deducir de la imagen. Las que más importan, porque evitan falsos «REVISAR»:

| # | Regla | Qué decide |
|---|---|---|
| 1 | Tipo | `090` y `099` son **indistintos** (boletos). Mapea `COD. 001/006/011` → A/B/C. `INTERNACIONAL` = comprobante de un proveedor de otro país (no es una Factura E) |
| 2 | Razón social | Tolerar formato: mayúsculas, tildes, «S.A. vs SA» |
| 3 | CUIT | Comparar dígito a dígito. No rechazar por el verificador |
| 4 | Fecha | Contra la **impresa**, no la de carga |
| 5 | Nro de factura | Señalar si falta el punto de venta o vino incompleto |
| 6 | Moneda | Contra el símbolo/leyenda impreso |
| 7 | Subtotal | Si discrimina, es el **NETO**. Si es B/C sin discriminar, subtotal = total y **no es error** |
| 8 | Impuestos | Sumar lo discriminado y comparar por categoría. Si no discrimina, los campos deben estar en cero |
| **9** | **Monto no gravado** | Es un **ajuste manual** (recargo de Mercado Pago que no está en la factura): **no verificable** por definición |
| **10** | **Importe total** | Si difiere, **primero** verificar si la diferencia coincide con el monto no gravado |
| 11 | Categoría | Consistente con el rubro del emisor; si no se infiere, «no verificable» |
| 12 | Notas | No se valida; solo sugerir aclaración si hay discrepancias |
| 13 | Comensales | Requerido por categoría; **no verificable** salvo que el ticket detalle cubiertos → dato **faltante**, no error |
| 14 | Litros | **Sí** suele estar impreso en el ticket de combustible: comparar |
| 15 | Centro de costo | Informativo, no lo controlan los aprobadores |

Las reglas **9 y 10** son el corazón del criterio: *una diferencia no es un error
hasta descartar que esté justificada*.

### Estado global

| Estado | Cuándo |
|---|---|
| `OK` | Todos los campos verificables coinciden, o las diferencias están justificadas por las reglas |
| `REVISAR` | Hay una discrepancia relevante en montos, CUIT, tipo o fecha |
| `INCOMPLETO` | La imagen no permite verificar campos clave |

Y tres criterios que ordenan el juicio: **«no legible» ≠ inventar** un valor;
priorizar discrepancias de **monto** sobre las cosméticas; **nunca inventar**.

---

## El campo `es_comprobante` (la «regla 0»)

Es el campo que responde **qué es** el documento, antes de *qué dice*. Existe
porque el corpus real trae documentos que **no son facturas ni notas** —una
captura con «GASTOS VARIOS, FALTA FACTURA», un remito, un resumen de tarjeta— y
sin este campo el modelo improvisaba la respuesta sobre `tipo_comprobante`:
`null` (el dato se perdía), texto libre (que el vocabulario cerrado marca
inválido) o forzando una letra A/B/C que el papel no tiene.

| Valor | Qué significa |
|---|---|
| `comprobante` | Lo emitió el proveedor/vendedor y acredita la operación |
| `no_comprobante` | No es un comprobante: DNI, memo, foto de pizarra, presupuesto, resumen de tarjeta, captura que solo muestra un pago |
| `indeterminado` | La imagen no alcanza para decidirlo |

⚠️ **`no_comprobante` NO significa «no transcribas nada».** La primera versión de
esta guía decía que en un `no_comprobante` «los campos fiscales van en null: no hay
emisor, CUIT, fecha ni importes que transcribir», y el modelo lo obedeció al pie de
la letra: en el voucher de posnet `125cbe9f` **citó** «Imp. Total: $30.920,00» en
`observaciones` y dejó `importe_total` en `null`. Leyó el dato y lo tiró — la peor
pérdida posible, porque la lectura existía y ya no se podía recuperar.

La confusión era mezclar dos cosas distintas:

| | Qué se decide |
|---|---|
| **Qué es** el documento | `es_comprobante` — una lectura |
| **Si sirve** para el gasto | otra etapa (la conclusión) — una decisión |

Un DNI, un resumen de tarjeta o el voucher de un posnet **igual tienen datos
impresos**, y esos datos son el rastro de por qué el gasto no se acredita. Lo que
no se transcribe es lo que el papel **no dice**: no se inventa un emisor, un CUIT
ni una letra A/B/C. La guía ahora pide transcribir lo impreso **sea o no** un
comprobante, y explica el motivo en `observaciones`.

⚠️ **Medido, porque la intuición acá engaña** (A/B sobre el mismo documento, mismo
modelo):

| prompt | `importe_total` |
|---|---|
| con la frase supresora | `null` en **4 de 5** corridas |
| sin la frase | **30920.0 en 4 de 4** |

La **clasificación** (`no_comprobante`) varía por su cuenta en las dos ramas: es
varianza del modelo, no de la guía. Lo que la guía controla es la transcripción, y
eso es lo que el test fija.

⚠️ **No es una decisión de negocio, es una lectura.** No dice si el comprobante
*sirve* para el gasto (eso es `comprobante_valido`, que resuelve F5 y sigue
fuera del contrato) ni qué comprobante es (eso es `tipo_comprobante`). Es la
misma pregunta que el **gate** de F2 le hace a una vista barata antes de gastar
en extracción, y por eso los dos usan el **mismo** campo y el **mismo**
vocabulario (`schemas.evidence.ClaseDocumento`).

⚠️ **Va en el cierre de extracción, no en las 15 reglas.** El `system` del YAML
se manda siempre, pero el esquema del modo `validar` no tiene este campo: un
modelo al que se le pide un dato que su esquema no admite devuelve un JSON
inválido y el núcleo **repregunta** (y cada intento se paga). Por eso la guía
vive en `INSTRUCCIONES_SISTEMA_EXTRACCION` (`prompts.py`), que reemplaza el
cierre de comparación **solo** en modo `extraer`.

---

## Notas de implementación

- **La primera pasada puede ser solo de extracción**, sin el bloque de
  comparación, y el *diff* de campos resolverse después con lógica
  determinística. Es lo que el propio prompt recomienda —«más barato y más
  auditable que pedirle al LLM que compare números»— y es exactamente lo que hace
  [`voucherflow.llm.evaluador`](../../evaluador.py): las 15 reglas en código.
- `monto_no_gravado`, `cantidad_comensales_personas` y `centro_de_costo` son por
  diseño **no verificables** contra la imagen. El prompt los trata como
  informativos para evitar falsos `REVISAR`; el evaluador hace lo mismo.
- **`temperature` baja (0–0.2)**: es una tarea de extracción y comparación, no
  generativa. Ojo: los modelos de razonamiento de DeepSeek **la ignoran**, así que
  el registro lo declara en vez de dar a entender que se aplicó.

---

## Cómo se usa

```bash
# El modo y el proveedor deciden cómo se arma el prompt
voucherflow-lab <carpeta> --operacion extraer   # transcribe (sin datos)
voucherflow-lab <carpeta> --operacion validar --datos datos.json

# Otro prompt, si hace falta probar una variante
voucherflow-lab <carpeta> --prompt mi-variante.yaml
```

Cada salida guarda el **hash del prompt efectivo** —después de adaptarlo, no el
crudo—: es lo que permite auditar con qué prompt se generó cada dato y comparar
dos corridas.

## Referencia

| Ruta | Qué |
|---|---|
| [`validacion-mendel.yaml`](validacion-mendel.yaml) | El prompt efectivo |
| [`../../prompts.py`](../../prompts.py) | `cargar_prompt` (YAML o `.md`), armado de los dos modos, hash |
| [`../../esquemas.py`](../../esquemas.py) | Los esquemas que validan la respuesta |
| [`../../evaluador.py`](../../evaluador.py) | Las 15 reglas, en código |
| [`docs/laboratorio-llm.md`](../../../../../docs/laboratorio-llm.md) | El ciclo de ajuste completo |

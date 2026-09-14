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
| `system` | Las 15 reglas de negocio y los criterios del auditor | **Siempre** |
| `user` | El pedido + el **template** del JSON de entrada | Siempre, con los datos reales **sustituidos** |
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
| 1 | Tipo | `090` y `099` son **indistintos** (boletos). Mapea `COD. 001/006/011` → A/B/C |
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

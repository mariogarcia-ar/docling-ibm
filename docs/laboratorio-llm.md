# Laboratorio de LLM externos — ajuste de prompt y evaluación

Herramientas para **ajustar el prompt** del control de comprobantes y **evaluarlo**
contra los datos cargados, usando modelos externos (OpenAI, DeepSeek, Gemini).

Es un laboratorio: no forma parte del pipeline de producción (`voucherflow run`),
que corre contra Ollama local. Acá se prueba el prompt con modelos más potentes,
se mide su calidad y se decide qué vale la pena llevar al pipeline.

```bash
voucherflow-lab <carpeta> --operacion extraer
```

## El ciclo de ajuste

```
     ┌──────────────────────────────────────────────────────────┐
     │  1. ESTIMAR   --dry-run                                  │
     │     ¿cuánto cuesta esta corrida?                         │
     └────────────────────────┬─────────────────────────────────┘
                              ▼
     ┌──────────────────────────────────────────────────────────┐
     │  2. EXTRAER   --operacion extraer                        │
     │     leer el corpus (la pasada que se paga)               │
     └────────────────────────┬─────────────────────────────────┘
                              ▼
     ┌──────────────────────────────────────────────────────────┐
     │  3. EVALUAR   --operacion diff                           │
     │     comparar contra los datos cargados (NO llama a la API)│
     └────────────────────────┬─────────────────────────────────┘
                              ▼
                        cambiar el prompt
                              │
                              └──────────► volver al paso 1
```

El paso 3 es el que hace que el ciclo tenga sentido: mide **contra la verdad de
negocio** y sin volver a gastar. Sin él, "el prompt mejoró" es una impresión.

### 1. Estimar antes de gastar

```bash
# El lote completo, sin llamar a la API ni escribir nada
voucherflow-lab var/processed --dry-run
```

```
archivos          : 3542
tokens por archivo: entrada ≈ 2,769 (texto+esquema) + 1,024 de imagen
tokens totales    : entrada 13,434,182 | salida 850,080
COSTO ESTIMADO    : US$ 40.14   (≈ US$ 0.0113 por comprobante)
precios usados    : US$ 2.5/1M entrada, US$ 10/1M salida (gpt-4o)
postura del precio: PICO y SIN caché: es el techo
confianza         : fórmula (3.92 car/token; medido contra la API)
```

El `--dry-run` recorre **todo el lote**, incluidas las imágenes ya procesadas: no
saltea nada (saltear es cosa de la corrida real, que reanuda). Por eso `--forzar`
es innecesario acá —si lo pasás igual, se avisa y no cambia la estimación—.

La postura del precio es el **techo** (tarifa pico y sin caché): la corrida real
sale igual o menos.

### 2. Extraer el corpus

```bash
voucherflow-lab var/processed --operacion extraer --proveedor deepseek --workers 4
```

Un archivo JSON por documento, con la lectura y su **procedencia**: qué modelo,
qué prompt (hash), cuántos tokens y cuánto costó. Es reanudable: lo ya hecho se
saltea, y un documento con `error` **no** cuenta como hecho.

### 3. Evaluar sin gastar

```bash
voucherflow-lab var/processed --operacion diff --datos datos_mendel.json
```

Compara cada extracción contra los datos cargados, con las **15 reglas de
negocio** implementadas en código —no le pregunta al modelo si los números
coinciden—. Es determinístico y auditable, y no toca la red.

| Estado | Qué significa |
|---|---|
| `OK` | Todos los campos verificables coinciden. |
| `REVISAR` | Hay discrepancias relevantes (lista cuáles). |
| `INCOMPLETO` | La imagen no permite verificar los campos clave. |

## Elegir proveedor

```bash
voucherflow-lab --listar-proveedores
```

| | OpenAI | DeepSeek | Gemini |
|---|---|---|---|
| Endpoint | (default) | `api.deepseek.com` | capa compatible de Google |
| Credencial | `OPENAI_API_KEY` | `DEEPSEEK_API_KEY` | `GEMINI_API_KEY` |
| Modelo por defecto | `gpt-4o` | `deepseek-flash` | `gemini-2.5-flash` |
| Impone el esquema | **sí** (`strict`) | no → valida local | no → valida local |
| Temperatura | tiene efecto | **la ignora** → se declara | tiene efecto |
| Esfuerzo | — | `none/low/high/max` | `none/minimal/low/medium/high` |
| Entrada cacheada | — | **expone** (se cobra barato) | — |

**Comparar modelos** es correr el mismo comando con `--proveedor` distinto y
comparar los `diff`:

```bash
for p in openai deepseek gemini; do
  voucherflow-lab var/processed -p $p -o var/validations/$p
  voucherflow-lab var/processed -p $p -o var/validations/$p -M validar --datos datos.json
done
```

Como el evaluador es el **mismo** para los tres, la diferencia que se mide es la
del modelo, no la de la vara.

## El prompt

El prompt **efectivo** vive en `src/voucherflow/llm/prompts/validacion-mendel.yaml`
y se edita ahí: el código lo lee (`--prompt` apunta a otro archivo si hace falta).
Al lado, `validacion-mendel.md` es el **documento** que lo explica —de dónde
salió, qué decide cada regla y qué no hay que tocar—, y no lo duplica: si copiara
las reglas habría dos versiones y una mentiría.

El YAML tiene tres claves: `system` (las 15 reglas), `user` (el pedido + el
*template* del JSON de entrada) y `ejemplo_salida` (el formato de la respuesta).
Las tres se pueden ver por separado porque el código las trata distintas.

### El vocabulario de `tipo_comprobante`

| Valor | Qué es |
|---|---|
| `A` `B` `C` `M` `E` | La letra del encabezado de un comprobante **argentino** |
| `090` `099` | Códigos de otros comprobantes (boletos); son **indistintos** entre sí |
| `INTERNACIONAL` | Un comprobante de un proveedor de **otro país** (`INVOICE` de una LLC de EE.UU.) |

⚠️ **`E` e `INTERNACIONAL` son ejes opuestos, no variantes:**

- `E` — **Factura E argentina**: la emite un contribuyente argentino *hacia* el
  exterior (R3: el *receptor* está fuera del país).
- `INTERNACIONAL` — un proveedor de afuera que **nos** factura a nosotros (el
  *emisor* está fuera del país).

⚠️ **Por qué no se llama `EXTERIOR`**: el dominio ya usa "exterior" para la
exportación (se factura *al exterior*). Un valor con ese nombre hacía que
"comprobante del exterior" describiera **los dos** casos a la vez. La palabra
quedó reservada para el eje de R3.

Sin `INTERNACIONAL` el modelo improvisaba de tres formas sobre el corpus real:
`null` (el dato se perdía), texto libre —`"INVOICE (no es comprobante AFIP…)"`,
que el vocabulario cerrado marca como inválido— y `"E"`.

⚠️ **Un `INTERNACIONAL` no se constata en el padrón ARCA**: el emisor no es un
contribuyente argentino, así que la búsqueda de evidencia adicional no se dispara
para ese caso (el gap se sigue reportando, pero como no buscable).

Dos modos de armado:

- **`validar`**: compara contra los datos cargados. Es para lo que fue escrito el
  template.
- **`extraer`**: solo transcribe. El cierre de comparación del template se
  reemplaza por instrucciones de transcripción, y el **ejemplo de salida se
  genera del esquema** que valida — así no pueden divergir. (El bug original fue
  justo ese: el ejemplo del `.md` era el de comparar, y el modelo lo copiaba.)

Cada salida guarda el **hash del prompt efectivo** (después de adaptarlo): es lo
que permite auditar con qué prompt se generó cada dato.

## Costos

Los precios son **datos editables**, no verdades: cambian y dependen del modelo y
de la cuenta. La tabla de referencia es un arranque; cualquier valor que entre por
la línea de comandos la pisa.

```bash
# Precios por modelo
voucherflow-lab var/processed --precios "gpt-4o=2.5/10, *=1/3"

# Un precio para toda la corrida
voucherflow-lab var/processed --precio-entrada 2.5 --precio-salida 10
```

Tres cosas que el reporte **declara** en vez de disimular:

- **Sin precio no hay costo**: queda `null` y se avisa, en vez de un `0` que se
  leería como «gratis».
- **Un precio a medias es un piso**: si solo se conoce el de entrada, el total es
  un mínimo, no el total.
- **Cada repregunta se paga**: el uso acumula todos los intentos, no solo el
  último.

### Reporte de gastos

```bash
voucherflow-lab --reporte-gastos gastos.json -o var/processed --tz -03:00
voucherflow-lab --reporte-gastos gastos.json --csv-gastos gastos.csv \
    -o var/processed --csv-delim ';' --csv-decimal ','
```

Lee **todo el histórico** de la carpeta de salida (no la última corrida) y agrupa
por día, modelo y modo. Como es una consulta, **no recorre rutas ni necesita
credencial**: alcanza con `-o` (o la carpeta de la configuración).

```
=== Reporte de gastos (API) ===
período           : 2026-09-13 → 2026-09-13 (UTC-03:00)
extracciones      : 1
tokens            : prompt 3,017 (de los cuales 2,816 de caché) | completion 250
costo total       : US$ 0.000377
```

No cuentan los `--dry-run` (no se llamó a la API) ni el modo `diff`. Un fallo que
**sí** consumió tokens se suma al total y se declara aparte («FALLOS PAGADOS»):
es gasto real, pero no es una extracción.

Dos avisos que el reporte no esconde:

- **SIN PRECIO**: si el modelo no está en la tabla, esas extracciones suman US$ 0
  y el costo real es mayor (el comando sale con código 1).
- **PARCIAL**: si solo se conoce el precio de entrada o el de salida, el total es
  un **piso**, no el total.

El `--tz` acepta `local` (default), `UTC` o un offset como `-03:00`. Los apuntes
se deduplican por **documento real + momento**: el mismo comprobante escrito en
dos carpetas (por invocar el comando con otra raíz) no se cobra dos veces, pero
reprocesarlo en otro momento sí cuenta (se pagó de nuevo).

## Lo que este laboratorio no hace

- **No reemplaza al pipeline.** `voucherflow run` procesa con Ollama local; esto
  es para ajustar el prompt con modelos externos y evaluarlo.
- **No elige el modelo por vos.** Mide para que la elección se pueda tomar con
  números.
- **No esconde las diferencias de proveedor.** Si el proveedor ignora la
  temperatura, el registro lo dice; si no impone el esquema, la validación es
  local y se declara.

## Referencia técnica

- `voucherflow.llm.proveedores` — los adaptadores y sus capacidades declaradas.
- `voucherflow.llm.evaluador` — las 15 reglas, en código.
- `voucherflow.llm.ejecucion` — el ciclo de llamada, que se adapta a las
  capacidades en vez de preguntar por el proveedor.
- `voucherflow.llm.costos` — precios, tokens y estimación.

# 07 — Extracciones esperadas (dataset de referencia de lectura)

> **Documento**: especificación del artefacto `tests/expected-extraction/`
> **Rol**: BA (qué y por qué) + SA (cómo) + QA (cómo se verifica)
> **Fecha**: 2026-09-14 · **Estado**: 🟡 **Propuesta para revisión — NO implementado**
> **Complementa**: [`06-estrategia-calidad.md`](06-estrategia-calidad.md) §3 (golden set)
> **Versión**: v0.2 — revisada tras medir que **5 de 28 CUIT de la referencia
> tienen el dígito verificador inválido** (§2.3). Ese hallazgo obligó a pasar de
> "2 tiers de verdad" a **3** (§1.1) y de 5 a **7** estados en el reporte (§8).

> ## ⚠️ Estado
>
> Esto es un **plan**. No hay código ni carpetas creadas. Las decisiones abiertas
> están en §11 (ahora **6**, D-6 es nueva) y necesitan respuesta antes de empezar.
>
> ## ⚠️ Leer antes que nada
>
> **El nombre "valores esperados" es engañoso y este plan lo corrige (§1).** El
> artefacto **no** contiene la verdad: contiene la lectura de **otro modelo**, y
> esa lectura tiene errores medidos. Sirve para medir **acuerdo** y **divergencia**
> — nunca para declarar que el pipeline local es correcto.

---

## 1. Qué queremos y por qué

Hoy tenemos **30 extracciones pagadas** de `voucherflow-lab` (DeepSeek sobre el
corpus real), guardadas en `var/`. `var/` está en `.gitignore`: esas salidas
**no se versionan**, no las ve nadie más que esta máquina, y no sirven como
evidencia en un test.

El objetivo es **graduar una muestra de esas lecturas a un artefacto versionado**
que permita responder una pregunta que hoy no se puede contestar sin volver a
pagar:

> ¿El pipeline local (Docling + qwen2.5-vl + qwen2.5) lee el mismo comprobante
> **igual** que DeepSeek? ¿Y dónde **diverge**?

Y, como efecto secundario declarado por el usuario, dejar el lote como
**referencia para un entrenamiento posterior** (`my_prompt.md`).

### ⚠️ Formulación incorrecta que hay que desterrar

> ❌ *"Usar DeepSeek para crear los valores esperados, y así evaluar la **calidad**
> de la extracción local."*

Es la lectura natural del objetivo y es **incorrecta por tres motivos**, uno de
ellos medido (§2.3). Hay que decirla para matarla, porque el nombre
"valores esperados" invita exactamente a esa confusión.

| # | Por qué el acuerdo **no** es calidad |
|---|---|
| 1 | **Es circular como verdad.** Contra una referencia que es otro modelo, "coincide" significa "es parecido a DeepSeek", no "está bien". |
| 2 | **La referencia no es confiable, y hay prueba.** 5 de los 28 CUIT del dataset tienen el dígito verificador inválido (§2.3): son lecturas incorrectas, declaradas por el propio modelo. |
| 3 | **Castiga la mejora.** Si el pipeline local lee **bien** uno de esos CUIT, la comparación lo marca `difiere` — o sea, un acierto se cuenta como error. |

**Lo que sí se puede afirmar:** el dataset mide **acuerdo**, **divergencia** y
—cuando un auto-chequeo de código lo decide— **cuál de los dos está mal** (§1.2).
Nunca "el pipeline es correcto".

### 1.1 Tres tiers de referencia, no dos

⚠️ **Corrección de diseño respecto de la versión anterior de este plan.** Decía
"golden = verdad, esto = lectura de modelo". Faltaba el tier intermedio, que es
el único objetivable **sin modelo y sin humano** y que ya existe en el repo.

| Tier | Quién lo emite | Qué permite | Costo | Estado |
|---|---|---|---|---|
| **Auto-chequeos de código** | El **programa** (DV del CUIT, aritmética) | **Objetivo**: "esto está mal" | **$0** | ✅ ya existe en las dos puntas |
| **Acuerdo entre modelos** | Lab ↔ pipeline | **Relativo**: divergencia | el lab ya se pagó | ← esto es este plan |
| **Etiqueta humana** | Un contador | **Absoluto**: exactitud | caro | golden, otro tier |

El tier 1 es el hallazgo de esta revisión: **no hay que pagar ni esperar un
contador para tener verdad objetiva sobre una parte del dataset**. Y ya está
implementado de los dos lados — el lab calcula el dígito verificador, y el
evaluador **no le cree el `cierra_aritmetica` al modelo**: lo recalcula en Python
(`corrida.py:281`, `llm/evaluador.py::verificar_aritmetica`). El plan explota
justamente eso.

### 1.2 La regla que ordena todo

> Cuando un **auto-chequeo de código** puede decidir quién tiene razón, se le cree
> al código y se reporta **quién está mal**. Cuando no puede, sólo se reporta que
> **los dos difieren**, y no se declara un ganador.

| Situación | Qué reporta el artefacto |
|---|---|
| El lab falla el auto-chequeo (DV inválido, aritmética abierta) | `referencia_dudosa` en ese campo: **no cuenta** como acierto ni como error |
| El lab pasa el auto-chequeo y el pipeline no | `pipeline_dudoso` (el auto-chequeo señala al local) |
| Los dos fallan el auto-chequeo | `ambos_dudosos` (ninguno sirve: el documento o el preprocesamiento) |
| Sin auto-chequeo disponible | `coincide` / `difiere`, **sin veredicto** |

Esto evita los dos errores simétricos: **canonizar una lectura mala** como
estándar, y **reprocharle al pipeline** haber leído mejor que la referencia.

### 1.3 Lo que este artefacto **no** es

| | Golden set (`tests/golden/`) | **Extracciones esperadas** (esto) |
|---|---|---|
| Qué contiene | La **verdad** del negocio | La **lectura de un modelo** |
| Quién la produce | Un contador (validado por una 2ª persona) | DeepSeek, sin revisión |
| Sirve para | Medir **exactitud** | Medir **acuerdo** y detectar divergencias |
| Estado | Curado a mano | Automático |

⚠️ **La distinción no es cosmética: es la regla de honestidad del repo.** Comparar
contra una lectura de modelo **no mide si el pipeline acierta**; mide si
*coincide*. Si DeepSeek y el pipeline se equivocan igual, el acuerdo es 100 %.
Esto va escrito en el README del artefacto, no en una nota al pie — mismo criterio
que el `campos_fuera_de_paridad` de T-305/T-405: **"no comparable" no puede
leerse como "correcto"**.

---

## 2. Inventario medido de lo que hay hoy

No es una estimación: es el conteo de `var/validations/` (10) + `var/piloto/out/` (20).

| | Valor |
|---|---|
| Archivos de extracción | **30** |
| Documentos únicos | **29** (1 documento está en los dos lotes: `9fa45f1d…`) |
| Modelo | `deepseek-flash` en los 30 |
| Costo total pagado | **US$ 0,1833** |
| Prompt | `mendel-validacion@1` (`modo: extraer`) |

### 2.1 Cobertura real (lo que se puede medir)

| Dimensión | Distribución | Lectura |
|---|---|---|
| `tipo_comprobante` | A ×23 · B ×1 · C ×1 · 090 ×1 · 083 ×1 · texto libre ×1 · `null` ×2 | **Muy sesgada a A.** No hay E, M, ni 099. |
| `codigo_afip` | 001 ×14 · 081 ×5 · 01 ×4 · 083 ×3 · 090 ×1 · 011 ×1 · `null` ×2 | — |
| `legibilidad` | `buena` ×30 | **Sin casos difíciles.** El corpus fue reducido a lado mayor ~1036 px, así que todas entran "legibles". |
| **DV del `cuit_emisor`** | válido ×23 · **inválido ×5** (§2.3) | **5 lecturas incorrectas**, detectables en código. La referencia no es confiable. |
| `cierra_aritmetica` (recalculado en Python) | `True` ×28 · `False` ×2 | 2 casos con la aritmética abierta: son **los más valiosos** para la comparación. |
| `reintentos_esquema` | ausente ×24 · 1 ×5 · 2 ×1 | El campo no existía en el esquema viejo (`piloto`). Hay que tolerar los dos. |

### 2.2 Los dos lotes no son homogéneos

`var/piloto/out/` es de un esquema **anterior**: le faltan `reintentos_esquema` y
`avisos_esquema`. Cualquier cargador tiene que aceptar las dos formas o declarar
que ignora una.

Otro detalle medido: en el registro, `resultado` y `extraccion` son **byte a byte
idénticos**. Un test que lea el bloque equivocado no falla — lee lo mismo. Hay
que fijar cuál es el canónico (propuesta: `extraccion`) con un test que verifique
que exista, para que un cambio de forma futura no pase en silencio.
### 2.3 🔴 HALLAZGO: 5 de 28 CUIT del dataset son lectura incorrecta

**Esta es la medición que cambia el diseño del plan.** El CUIT lleva **dígito
verificador** (módulo 11 sobre los 10 primeros dígitos): se verifica en **código
puro**, sin modelo y sin contador. Corrido sobre las 30 extracciones:

| | |
|---|---|
| Documentos | 30 |
| `cuit_emisor` **completo** (11 dígitos) | **28** |
| …con dígito verificador **válido** | **23** |
| …con dígito verificador **INVÁLIDO** | **5** ← **18 % de los CUIT del dataset** |

Los cinco, con su emisor:

| CUIT (referencia) | Emisor | ¿Corregible con 1 dígito? |
|---|---|---|
| `30-62221785-4` | Diarco S.A. | **No** |
| `30586221578` | HOTEL JARDIN SRL | **No** |
| `30-71530218-5` | ROCK & FELLERS | **No** |
| `30-71144495-3` | José Genna Repuestos S.R.L. | **No** |
| `30-70715163-1` | ALESO S.R.L. - SAN LORENZO | **No** |

**Calibración del método** (para que el número sea creíble y no un artefacto del
script): 23 de 28 pasan el dígito verificador. Por azar pasaría ~1 de cada 10, así
que el algoritmo está bien y los 5 son lecturas reales malas. Se verificó además
contra CUITs conocidos.

**Que no cierren con un solo dígito es la parte importante:** se probaron las 10
posiciones × 10 dígitos con el prefijo intacto, y **ninguno** se arregla cambiando
un dígito. No son typos: es una transcripción mal leída (el modelo leyó otro
número, o pegó dígitos de un campo vecino).

**Y el lab lo sabe:** los 30 registros tienen `digito_verificador_cuit_valido`
coherente con el cálculo real → el modelo **declara** que esos 5 están mal. No es
un error oculto: es un error declarado que el plan anterior iba a canonizar.

#### Por qué esto obliga a cambiar el plan

| Si congelamos las salidas como "valores esperados" sin más… | Consecuencia |
|---|---|
| …el artefacto declara **correcto** un CUIT que no existe | Se canoniza una lectura mala como estándar |
| …el pipeline local lee **bien** ese CUIT | Se marca `difiere`: **un acierto se cuenta como error** |
| …el reporte promedia todo en "X % de acuerdo" | Los 5 casos envenenan la métrica y el motivo queda invisible |

⇒ Es exactamente el escenario del motivo 3 de §1: la comparación **castiga la
mejora**. De ahí las tres capas de §1.1 y la regla de §1.2.

---

## 3. Los dos blockers medidos (esto es lo que hay que resolver)

### 3.1 🔴 Faltan las imágenes: 26 de 29 documentos no están en `tests/fixtures/`

Medido — de los 29 documentos con extracción, sólo **3** tienen su imagen
versionada:

| Documento | Dónde está | Lote |
|---|---|---|
| `2991f57d-c143-4b23-9f87-4dfb1214ef53.jpg` | `fixtures/golden/` | lab |
| `9e461da9-8f9b-44f8-a7e8-8da98c11abf4.jpg` | `fixtures/grandes/` | lab |
| `9fa45f1d-f6ad-4cea-b585-432aa3b39dad.jpg` | `fixtures/otros/` | lab + piloto |

Los otros **26** viven en `var/processed/…` (ignorado por git) o en
`var/piloto/lote/…` (hard-links a `var/files`, symlink a `~/Desktop/ibm-docling-2`).

**Consecuencia:** si sólo copiamos los JSON, el artefacto es **incompleto**: 26
extracciones no se pueden comparar con nada, porque el pipeline necesita la
imagen y la suite default no puede leer `var/`. Y la suite default **no puede
depender de `var/`**: es gitignored y en CI no existe.

**Opciones** (§11, D-1):

| | Cómo | Costo | Reproducible en CI |
|---|---|---|---|
| **A** | Copiar las 26 imágenes a `tests/fixtures/` | +~2,5 MB (las de `processed` pesan 78–125 KB) | ✅ sí |
| **B** | Versionar sólo las extracciones y marcar las 26 como `sin_imagen` | 0 MB | ⚠️ sólo 3 comparables |
| **C** | Ampliar con una corrida nueva del lab sobre documentos que **sí** están en `fixtures/` | US$ + ver §3.2 | ✅ sí |

⚠️ **Ojo con el peso medido**: las 284 imágenes de `var/processed/2025-08` y
`2025-09` suman **972 KB**; `tests/fixtures/` ya pesa **66 MB**. Copiar los 26 no
es un problema de tamaño.

⚠️ **Y hay una razón extra para copiarlas (§2.3)**: sin la imagen, los 26
documentos no se pueden comparar **ni** sirven para verificar en código si su
lectura de referencia es correcta. Un CUIT con DV inválido se detecta sobre el
JSON, pero **corregirlo o descartarlo** requiere mirar el comprobante.

> ⚠️ `tests/fixtures/**/*.md` está en `.gitignore` (línea 61). Si en algún momento
> el artefacto necesita el markdown de referencia, **no se puede** dejar ahí.

### 3.2 🔴 La comparación de tokens de imagen **no es válida**

El laboratorio registra `imagen.dimensiones` (ej. `[840, 1036]`). El pipeline
calcula sus propias dimensiones con Docling. Misma imagen → mismos píxeles →
mismos enteros.

**Pero usarlo como métrica de comparación sería un error**, por dos motivos
medidos:

1. `imagen.bytes` del lab (84.324) es el peso del **JPEG del lab**. Una fixture
   re-exportada o re-renderizada de un PDF da **los mismos píxeles y otros
   bytes** → un `difiere` que no es una divergencia de lectura.
2. `detalle` (`high`/`low`) y `tokens_estimados` son **conceptos del lab**, no
   del pipeline (OpenAI cobra por mosaicos, DeepSeek a tope fijo).

**Propuesta:** `dimensiones` se usa como **verificación de identidad** (¿la
fixture es la misma imagen?), jamás como métrica de calidad. Es exactamente el
patrón que el repo ya usa para detectar colisiones de destino en `corpus`
(memoria: *"el hash es la identidad, no el nombre"*).

### 3.3 🟠 El lab no guarda sostén (`fragmento_sustento`)

Medido: alimentando el motor de reglas del pipeline con una extracción del lab,
salen **17 debilidades por documento** del tipo *"declaró 'A' pero no citó
fragmento de sustento; la lectura no es auditable"*.

Eso **no es un hallazgo sobre la calidad de DeepSeek**: es que el lab pone la
evidencia en el campo de prosa `observaciones` (627–1 716 caracteres, con el
desglose completo: ítems, pagos, cálculos) en lugar del contrato por campo del
pipeline (ADR-001). Si la comparación pasa por `veredicto_raw_de_evidencia`,
**todas** las lecturas salen "dudosas" y la medición mide el formato, no la
lectura.

**Propuesta:** la comparación **no** evalúa sostén. Se declara explícitamente en
el reporte como `sosten_no_comparable`, y `observaciones` se preserva como dato
de contexto para revisión humana.

---

## 4. El mapeo de campos (medido, no supuesto)

El esquema del lab es **plano** y tiene **26** campos; el contrato del pipeline
(`CAMPOS_EXTRACCION`) tiene **16**. Se llaman distinto y se solapan parcialmente.

### 4.1 Mismo nombre — comparación directa (9)

`tipo_comprobante` · `razon_social_emisor` · `cuit_emisor` · `fecha_emision` ·
`moneda` · `subtotal` · `iva` · `impuestos_internos` · `otros_impuestos`

### 4.2 Mismo concepto, distinto nombre (4) — requieren un mapa declarado

| Lab | Pipeline |
|---|---|
| `nro_factura` | `nro_comprobante` |
| `importe_total` | `importe_total_facturado` |
| `no_gravado` | `monto_no_gravado` |
| `percepciones_iibb` | `percepcion_iibb` |

⚠️ **`percepciones_iibb` / `percepcion_iibb` se distinguen por un plural.** Es
justo el tipo de pareja que un renombre futuro rompe en silencio. El mapa va en
**un solo lugar** y con un test que exija que cada nombre del mapa siga existiendo
en las dos puntas (mismo criterio que `derivar_evidencia` y `_claves_reales` de
`test_env_example`).

### 4.3 El pipeline tiene y el lab no (3) — se declara `sin_contraparte`

`razon_social_receptor` · `cuit_receptor` · `descripcion`

⚠️ Los tres **existen en el lab pero dentro de `observaciones`**: en el caso
medido, el CUIT del receptor (`30-58221570-3`) y la razón social (`CUC S.A.`)
están en la prosa. **No se extraen con regex**: parsear prosa para fabricar un
"esperado" es inventar la contraparte y haría la comparación inauditable.

### 4.4 El lab tiene y el pipeline no (13) — se declara `fuera_del_contrato`

`legibilidad` · `codigo_afip` · `digito_verificador_cuit_valido` · `exento` ·
`discrimina_impuestos` · `cierra_aritmetica` · `rubro_emisor` ·
`categoria_gasto_sugerida` · `cantidad_comensales_personas` · `cantidad_litros` ·
`centro_de_costo` · `campos_no_legibles` · `observaciones`

La mayoría son **decisiones** que el pipeline resuelve en otra etapa a propósito
(ADR-001: `categoria_gasto` y `centro_de_costo` son de F3/F5, no de extracción).

⚠️ **Pero tres de estos NO se declaran "fuera de alcance": son los auto-chequeos
del tier 1 (§1.1), y son lo más valioso del artefacto.** No se comparan *contra*
el pipeline (el pipeline no emite esos campos): se usan para **juzgar la
referencia** y para validar los campos que sí se comparan.

| Campo del lab | Cómo se usa |
|---|---|
| `digito_verificador_cuit_valido` | **Auto-chequeo del tier 1.** Juzga si el `cuit_emisor` de la referencia es creíble (§2.3). Es la entrada del estado `referencia_dudosa`. |
| `cierra_aritmetica` | Auto-chequeo de los importes. ⚠️ Comparar el del lab contra `verificar_aritmetica()` del pipeline; **nunca** contra el `cierra_aritmetica` que el pipeline le devuelve al modelo (a ese no se le cree, por diseño). |
| `exento` / `no_gravado` | Contra los componentes de `COMPONENTES_DEL_TOTAL`. ⚠️ `exento` **no tiene contraparte** en `CAMPOS_EXTRACCION` (la extracción no lo pide), así que sólo entra en el auto-chequeo aritmético, no en la comparación campo a campo. |

⚠️ `campo_no_legible` merece trato especial: ver §5.3.

### 4.5 Guard de integridad (no negociable)

**Cada** campo de las dos puntas tiene que estar en exactamente una lista:
`comparables` / `comparables_por_mapa` / `sin_contraparte` / `fuera_del_contrato`
/ `auto_chequeo` (los tres de §4.4, que **no** se comparan pero se usan).

Un test lo exige. Sin ese guard, un campo nuevo desaparece de la medición **en
silencio** y el reporte parece cubrir todo el contrato sin cubrirlo. Es el mismo
guard que F4/T-405 ya tiene y que valió la pena: ahí destapó que
`razon_social_receptor` no tenía contraparte en los prompts de referencia.

⚠️ El guard tiene **dos direcciones**, y la segunda es la que importa acá: además
de "todo campo está en una lista", exige que **la lista `auto_chequeo` sea
suficiente** para juzgar la referencia (§2.3). Si un campo comparable puede estar
mal y ningún auto-chequeo lo detecta, se declara `sin_auto_chequeo` — así "no
puedo juzgarlo" no se lee como "está bien".

---

## 5. Normalización: qué se compara y cómo

### 5.1 Los valores vienen en espacios distintos

**Medido, y es el punto más delicado del diseño.** El lab devuelve el valor ya
interpretado; el pipeline espera el valor **tal como se lee** y normaliza en
código:

| | El lab devuelve | El pipeline espera |
|---|---|---|
| Monto | `52069.85` (número JSON) | `"52.069,85"` (texto impreso) → normaliza |
| Fecha | `"29/08/2025"` | igual, pero publica `"2025-08-29"` |
| Total | `importe_total: 73122.64` | `importe_total_facturado` → `73122.64` |

Medición del round-trip sobre las 10 extracciones del lab:

| Campo | Valores que difieren del canónico |
|---|---|
| `fecha_emision` | **9 de 10** (`29/08/2025` → `2025-08-29`) |
| todos los demás (no nulos) | **0** — coinciden exactamente |

⚠️ **Conclusión: sin normalizar, el 90 % de las fechas daría un `difiere` falso.**
Y no es que el normalizador "no sirva": es que **la comparación tiene que pasar
por el normalizador del pipeline** (`normalizar_campo` / `normalizar_evidencia`),
que ya maneja las dos formas (verificado: acepta `"52.069,85"` y `52069.85`).

**Propuesta:** se compara el **valor canónico de las dos puntas**. Nunca string
contra string.

### 5.2 Los `null` no se descartan

Medido: hay **19 valores `null`** en las 10 extracciones del lab.

Un `null` en el lab es una **declaración** ("no lo pude leer"), y en el caso
medido está acompañado de `campos_no_legibles: ["cuit_emisor", "domicilio_emisor", …]`.
Descartar los `null` antes de comparar borraría la diferencia más interesante que
hay en el dataset: **una punta dice "no lo leí" y la otra dice un valor** (o al
revés).

**Propuesta:** los `null` entran en la comparación como `ausente`, que es un
estado distinto de `difiere` y de `no_comparable`. La regla del repo ya lo dice
("un valor no normalizable conserva el crudo; *no normalizable* ≠ *ausente*").

### 5.3 `campos_no_legibles` cruza los dos mundos

El lab declara en prosa qué campos no pudo leer. El pipeline los reporta en
`campos_ausentes`. Son comparables **como conjuntos**, y la divergencia es
información de primera calidad:

| Lab | Pipeline | Lectura |
|---|---|---|
| `cuit_emisor` en `campos_no_legibles` | `cuit_emisor` en `campos_ausentes` | **Acuerdo**: los dos dicen "no está". |
| `cuit_emisor` en `campos_no_legibles` | `cuit_emisor` con valor | El pipeline **leyó más** que DeepSeek. |
| `null` sin declarar | `cuit_emisor` en `campos_ausentes` | DeepSeek **omitió declarar** la ilegibilidad (peor práctica, no peor lectura). |

**Propuesta:** `campos_no_legibles` se compara, pero en su propia tabla, porque
mide **honestidad al declarar**, no capacidad de lectura.

---

## 6. Estructura propuesta del artefacto

> ⚠️ El nombre de la carpeta está en discusión (§11, D-2). Se escribe acá el que
> propongo, no el que pidió el usuario, para que la diferencia se vea.

```text
tests/expected-extraction/
  README.md                       # qué es, qué mide, qué NO mide (§1, §8)
  manifiesto.json                 # índice + procedencia + cobertura + auto-chequeos
  mapa_de_campos.json             # el mapa de §4 (única fuente de verdad)
  <id-documento>/                 # ej. 2991f57d-c143-4b23-9f87-4dfb1214ef53/
    extraccion.json               # la lectura, tal cual salió del lab
```

### 6.1 `manifiesto.json` — qué declara

Por documento y por lote de origen:

| Clave | Para qué |
|---|---|
| `documento_id` | El id (mismo criterio que `identificador_de_archivo`) |
| `archivo` | Nombre del archivo, para aparearlo con la fixture |
| `imagen_en_fixtures` | **`tests/fixtures/golden/…`** o `null` si no está (el blocker §3.1, declarado en el dato y no en un comentario) |
| `dimensiones` | Para la verificación de identidad de §3.2 |
| `fuente_lote` | `validations` o `piloto` (los dos lotes no son homogéneos, §2.2) |
| `modelo` · `version_prompt` · `modo` | Procedencia |
| `costo_usd` · `uso` | Trazabilidad del gasto |
| `extraido_utc` | Cuándo |
| **`auto_chequeos`** | **Nuevo (§2.3, §4.4)**: por campo, el resultado del DV y la aritmética. Es lo que sostiene la partición `limpio`/`ruidoso` de §8.1 |
| **`referencia_dudosa`** | **Nuevo**: la lista de campos que **no** se deben usar como estándar, con el motivo (`dv_invalido` / `aritmetica_abierta`) |

⚠️ `auto_chequeos` se **calcula en la generación** (§10 paso 4), no se escribe a
mano: mismo criterio que `_json_ejemplo` en `prompt_extraccion` ("se genera, no
se escribe a mano, para que el ejemplo impreso y el contrato validado no puedan
divergir"). Un valor escrito a mano en un manifiesto que se declara verificado es
una promesa que nadie controla.

⚠️ Se **excluyen a propósito** `precios_usd_1m` y el `costo_usd` como dato
versionado del gasto (cambian con la tabla de precios): si se versionan, un
cambio de tarifa hace que el artefacto "mienta" sobre lo que costó. Se guardan
como **cita histórica**, marcados como tal.

### 6.2 Qué se guarda: ¿crudo o normalizado?

**Propuesta: crudo, y se normaliza en el test.**

| | Guardar normalizado | **Guardar crudo** (propuesto) |
|---|---|---|
| Fidelidad para entrenamiento | ❌ se pierde la forma original | ✅ es lo que el modelo escribió |
| Riesgo de segunda verdad | 🔴 alto: dos copias que pueden divergir | ✅ ninguna |
| Normalización | a mano, una vez | **la del pipeline**, en cada corrida |

Guardar una versión normalizada a mano crearía un "esperado" que **no lo dijo el
modelo**: si el normalizador del pipeline mejora, el artefacto quedaría con la
normalización vieja y la comparación mediría la divergencia entre dos
normalizadores en vez de entre dos modelos. Es el mismo razonamiento que el
`_json_ejemplo` de `prompt_extraccion` (se genera, no se escribe a mano, "para
que el ejemplo impreso y el contrato validado no puedan divergir").

---

## 7. Cómo se compara (dos niveles, como el resto del repo)

### Nivel A — suite default (sin Ollama, sin Docling, sin red)

Compara **lógica pura**, igual que `scripts/verificacion/etapa-extraccion.py`:

1. **Integridad del artefacto**: manifiesto ↔ carpeta ↔ documento; cada campo de
   cada punta en una lista de §4.5; el mapa de nombres existe en las dos puntas.
2. **Autoevaluación de la referencia** (tier 1, §1.1): corre los auto-chequeos
   sobre el artefacto y **fija por test** que los 5 CUIT con DV inválido sigan
   marcados como `referencia_dudosa`. ⚠️ Este test es el que impide que un
   regenerado del artefacto **pierda** la marca y vuelva a canonizar las 5
   lecturas malas.
3. **Normalización**: los valores canónicos del lab coinciden con el canónico del
   pipeline (el caso de la fecha, §5.1).
4. **Comparación** contra una `CombinedEvidence` **sintética** (construida en el
   test desde la lectura del lab). Verifica el motor de comparación, no la lectura.
5. **Round-trip**: `normalizar(leer(esperado)) == normalizar(pipeline)` para los
   campos comparables.
6. **Cobertura declarada**: todo documento sin fixture está listado como
   `sin_imagen` (que el silencio no se lea como cobertura).

⚠️ **El punto 2 no necesita imágenes ni Ollama**: el DV del CUIT y la aritmética
son funciones puras sobre el JSON. Es el control de calidad más barato del
proyecto y es el que sostiene la credibilidad del artefacto.

### Nivel B — verificación manual / `@pytest.mark.integration` (con Ollama)

Corre el pipeline **de verdad** sobre las fixtures y produce el reporte de
acuerdo. Es el único nivel que responde la pregunta de §1.

⚠️ **Bloqueante medido:** `modelos.llm` es `qwen2.5:7b` y **no está instalado** en
esta máquina (los instalados son `qwen2.5vl:3b`, `smollm2`, `deepseek-r1`). El
nivel B corre hoy con el `qwen2.5vl` para las dos fuentes, o hay que bajar el otro
modelo. Se declara en el reporte, no se disimula.

| Nivel | Herramienta | Requiere |
|---|---|---|
| A | `tests/test_expected_extraction.py` | nada |
| B | `scripts/verificacion/acuerdo-extraccion.py` | Ollama + modelos |

---

## 8. El reporte de acuerdo: cómo se lee un resultado

Campo por campo, con **siete** estados — no dos:

| Estado | Significa |
|---|---|
| `coincide` | Los dos leyeron lo mismo. |
| `coincide_normalizado` | Lo mismo tras normalizar (ej. la fecha). **Se cuenta aparte**: dice que los dos leyeron, con otra forma. |
| `difiere` | Valores distintos, y **ningún auto-chequeo puede decidir** quién tiene razón. |
| `ausente` | Una o las dos puntas no lo leyeron (`null` / `campos_ausentes`). |
| `referencia_dudosa` | **El lab falla el auto-chequeo** (DV inválido, aritmética abierta): no cuenta como acierto **ni** como error. §1.2 |
| `pipeline_dudoso` | El lab pasa el auto-chequeo y el pipeline no: el auto-chequeo **señala al local**. |
| `ambos_dudosos` | Los dos fallan: el problema puede ser el documento o el preprocesamiento, no el modelo. |
| `no_comparable` | Sin contraparte, fuera del contrato, o sin sostén (§3.3). |

⚠️ **Los tres estados `*_dudoso*` son los que evitan el error grave del plan
anterior.** Sin ellos, los 5 CUIT de §2.3 se reportarían como `difiere` y el
pipeline quedaría **castigado por leer bien**. Y un `difiere` con auto-chequeo
disponible es información mucho más débil de lo que parece: hay que decir quién
tiene razón, o decir que no se sabe.

### 8.1 Las dos particiones del dataset

El artefacto se parte por **la calidad de su propia referencia**, cosa que se sabe
en código (no es una opinión):

| Partición | Criterio | Documentos | Uso |
|---|---|---|---|
| `limpio` | Pasa **todos** los auto-chequeos | **23** | Medir **acuerdo**: una divergencia es material útil. |
| `ruidoso` | Falla ≥1 auto-chequeo | **7** (5 con DV inválido + 2 con aritmética abierta) | Estudiar **dónde fallan los dos**, no medir acuerdo. |

⚠️ **Un documento puede estar en las dos particiones parcialmente**: el `cuit_emisor`
de un documento puede ser dudoso y sus montos ser sólidos. La partición se aplica
**por campo**, no por documento — empeorar la granularidad a documento tiraría
información buena (los importes de los 5 casos con DV malo no tienen por qué
estar mal). La tabla de arriba es la vista por documento, para dimensionar.

### Lo que explica una diferencia (y no es una regresión)

Mismo criterio que el README de `tests/golden/F4/`: una diferencia se **explica**
o se **declara**, nunca se promedia en un "94 % de acuerdo" que esconde el motivo.

| Diferencia | Por qué **no** es una regresión |
|---|---|
| `tipo_comprobante` A vs `090` | Son vocabularios distintos a propósito: el lab acepta los códigos de tique; el motor R1-R7 de F3 los deja fuera por D-13. Comparar contra la **letra final** mezcla lectura con decisión. |
| `moneda` `null` vs `"ARS"` | El pipeline **no asume** `ARS` sin indicio explícito: inventar la moneda sería peor que no leerla. |
| `descripcion` | El lab la pone en prosa; el pipeline la lee estructurada. §4.3. |
| `observaciones` | No es un campo del contrato: es prosa. |
| Campos de decisión | Fuera por ADR-001. No es un desacuerdo. |

### Honestidad de alcance (va en el reporte, arriba)

1. **No mide exactitud.** Mide acuerdo entre dos modelos (§1).
2. **La referencia tiene errores medidos**: 5 de 28 CUIT con DV inválido (§2.3).
   Por eso el reporte publica las siete categorías y **nunca** un único "% de
   acuerdo": ese número, con la referencia sucia, no significa nada.
3. **El corpus está sesgado**: 23 de 30 son facturas A, todas `legibilidad: buena`,
   ninguna rotada ni borrosa (§2.1). **No se puede extrapolar** a "el pipeline
   lee el corpus".
4. **La referencia es DeepSeek, y tiene errores conocidos de comportamiento**: en
   el caso medido declaró `cuit_emisor: null` correctamente (tapado por cinta) pero
   en otra corrida de la misma imagen **confundió emisor con receptor** (memoria:
   `--esfuerzo none`). Un desacuerdo puede ser un error **de la referencia**.

---

## 9. Qué se toca si esto avanza

| Archivo | Cambio |
|---|---|
| `tests/expected-extraction/**` | **Nuevo**: el artefacto (§6). |
| `tests/test_expected_extraction.py` | **Nuevo**: nivel A (§7). |
| `src/voucherflow/extraction/key_value.py` | ⚠️ **Posible**: hoy `cuit_completo()` cuenta los 11 dígitos pero **no valida el DV** ("eso es del padrón/ARCA, no de la extracción", y es correcto para la *extracción*). El DV que necesita el artefacto es un **auto-chequeo de la medición**, no una regla del pipeline: la propuesta es una función nueva y explícita (p. ej. `digito_verificador_valido` en el módulo de la herramienta, **no** cambiar el contrato de extracción). Decidir en D-6. |
| `scripts/verificacion/acuerdo-extraccion.py` | **Nuevo**: nivel B (§7). |
| `tests/fixtures/` | **Según D-1**: las 26 imágenes faltantes. |
| `README.md` (§ Documentación) | Mencionar el dataset y su límite. |
| `docs/plan/06-estrategia-calidad.md` §3 | Nota: el golden (contador) y esto son **dos tiers distintos**. |
| `scripts/readme.md` | Fila de la herramienta nueva. |
| `.gitignore` | ⚠️ Verificar que `tests/expected-extraction/**/*.json` **no** caiga en una regla existente. |

**No se toca**: `var/`, el lab, el pipeline. Esto es un artefacto de **medición**:
el día que se implemente, el único cambio en `src/` sería el que pida un hallazgo.

---

## 10. Plan de ejecución propuesto (si se aprueba)

| # | Paso | Salida | Est. |
|---|---|---|---|
| 1 | Cerrar §11 (D-1 a D-6) | decisiones | — |
| 2 | Copiar las 26 imágenes faltantes a `tests/fixtures/` (si D-1 = A) | fixtures | 0,2 dh |
| 3 | Generar el artefacto desde `var/` con un script **operativo** (no a mano) | `expected-extraction/` | 0,5 dh |
| 4 | **Auto-chequeos (DV + aritmética) y marca `referencia_dudosa`** | campo declarado | 0,3 dh |
| 5 | `manifiesto.json` + `mapa_de_campos.json` + `README.md` | artefacto | 0,3 dh |
| 6 | Motor de comparación + nivel A | tests | 0,5 dh |
| 7 | Nivel B + reporte (7 categorías) | script | 0,5 dh |
| 8 | Correr nivel B y **leer los hallazgos** | reporte | 0,3 dh |
| 9 | Docs (§9) | docs | 0,2 dh |

**Total ≈ 2,8 dh** (0,3 dh más que antes: el paso 4 es nuevo).

⚠️ El paso 3 **no puede ser a mano**: copiar 30 JSON a mano garantiza que el
manifiesto y la carpeta diverjan. Se genera con un script que lee `var/`, y ese
script **declara en su salida** los documentos que quedaron sin imagen — el mismo
patrón que `corpus/lectura.py` cuando destapó que 264 PDF desaparecían en
silencio (memoria: *"en un barrido de carpeta lo que no matchea desaparece"*).

⚠️ **El paso 4 es el que no se puede saltear.** Los auto-chequeos tienen que
correr **en la generación**, no en el test: así el artefacto nace marcado y el
test sólo verifica que la marca siga ahí. Si se dejan para después, queda un
período en el que el dataset canoniza 5 CUIT inválidos.

### 10.1 La regla para las extracciones nuevas (si D-1 = C)

Si se amplía el dataset con corridas nuevas, **exigir el auto-chequeo antes de
aceptar la muestra**:

| Chequeo | Qué descarta |
|---|---|
| DV del `cuit_emisor` inválido | La lectura se sospecha mala: no entra como referencia (o entra marcada). |
| Aritmética abierta | Falta un importe o un dígito: **sí entra** (es material valioso), marcado `referencia_dudosa`. |

En el dataset actual esto significa que **5 de 30 no se habrían aceptado**. Es la
diferencia entre un dataset que se puede citar y uno que hay que aclarar cada vez.

---

## 11. Decisiones abiertas (necesito respuesta)

### D-1 · Las 26 imágenes que faltan (§3.1)
- **A** · Copiarlas a `tests/fixtures/` (~2,5 MB) → 29/29 comparables, corre en CI.
- **B** · No copiarlas, declarar `sin_imagen` → 3/29 comparables.
- **C** · Correr el lab sobre documentos que **ya** están en `fixtures/` y ampliar
  el dataset con eso (cuesta plata, ~US$ 0,006/documento).

> Mi recomendación: **A + C**. A da el dataset completo hoy con lo ya pagado; C
> agrega cobertura fiscal (B, C, E, M) que hoy **no existe** (§2.1) usando
> documentos que ya son fixtures y por lo tanto quedan comparables para siempre.
>
> ⚠️ **Refuerzo por §2.3**: hoy el 18 % de los CUIT del dataset es lectura
> incorrecta. C es además la forma de conseguir un dataset **aceptable bajo el
> criterio de §10.1**, en vez de heredar los errores del lote viejo.

### D-2 · El nombre de la carpeta
El pedido fue `test/expected-extration/`. Propongo **`tests/expected-extraction/`**:
`tests/` es el directorio real (plural) y `extration` es un typo de `extraction`.
Alternativas: `tests/extracciones-esperadas/` (el repo nombra en español los
conceptos, en inglés las carpetas de tests), o dejarlo adentro de `fixtures/`.

⚠️ **Con el hallazgo de §2.3, "esperadas" es un nombre riesgoso**: "valor esperado"
se lee como "valor correcto", y 5 no lo son. Alternativas más honestas:
`tests/expected-extraction/` con el README explicando el límite, o
`tests/lecturas-de-referencia/` (que no promete que la referencia sea la verdad).

### D-3 · ¿Un documento o un lote por archivo?
Hoy los 30 archivos son **30 documentos** (1 por documento). Si en el futuro el
mismo documento tiene varias lecturas (otro modelo, otra corrida), ¿la carpeta es
`<id>/<fuente>.json` o se agrega un nivel `<id>/<modelo>/<corrida>.json`?
Decide si el manifiesto apunta a un archivo o a una lista.

### D-4 · ¿Comparamos también el markdown / OCR?
El pipeline produce `<doc>.md` (Docling). El lab **no** lo produce — lee la imagen
directo. Si el objetivo incluye "comparar el procesamiento" en el sentido amplio,
eso es otro artefacto (markdown esperado) y otro plan.

### D-5 · ¿El artefacto se congela o se regenera?
- **Congelado**: se versiona una vez y es un `golden_version` (como F4 `0.1-f4`).
- **Regenerable**: el script se puede volver a correr y actualizar.

> Mi recomendación: **congelado con versión declarada** (`por qué sirve comparar
> métricas entre versiones del dataset`, §3.4 del doc 06), y el script de
> generación queda versionado para auditar cómo se armó.

### D-6 · ¿Dónde vive el auto-chequeo del dígito verificador?
- **A** · Función nueva en el **script de la herramienta** (no toca `src/`): el DV
  es un chequeo de la **medición**, no una regla de extracción.
- **B** · Función en la librería, junto a `cuit_completo()`
  (`extraction/key_value.py`), para que la use el pipeline **y** la herramienta.
- **C** · Las dos: la función vive en la librería y la herramienta la importa.

> ⚠️ **Ojo con el contrato**: `cuit_completo()` documenta explícitamente *"no se
> completa ni se valida el dígito verificador (eso es del padrón/ARCA, no de la
> extracción)"*, y hay un test que lo fija
> (`test_extraction_key_value.py::test_no_valida_el_digito_verificador`). **Eso es
> correcto y no hay que cambiarlo**: la extracción no debe rechazar una lectura por
> su DV (el padrón es la autoridad). Lo que hace falta es un chequeo **para la
> medición**, que puede vivir perfectamente fuera del camino de producción.
>
> Mi recomendación: **A**, y si más adelante el padrón de F5 necesita el mismo
> cálculo, se promueve a **C** con el ADR correspondiente.

---

## 12. Enlaces

- [`06-estrategia-calidad.md`](06-estrategia-calidad.md) §3 — golden set (el tier 3).
- [`tests/golden/F4/README.md`](../../tests/golden/F4/README.md) — el precedente de
  "qué explica una diferencia": el criterio que este plan reutiliza.
- [`docs/laboratorio-llm.md`](../laboratorio-llm.md) — el lab que produce las salidas.
- [`manual/user/llm.md`](../../manual/user/llm.md) — la guía del lab.
- `src/voucherflow/extraction/prompt_extraccion.py` — `CAMPOS_EXTRACCION` (el contrato).
- `src/voucherflow/extraction/key_value.py` — los normalizadores (§5.1) y
  `cuit_completo()` (⚠️ **cuenta 11 dígitos, no valida el DV**: ver D-6).
- `src/voucherflow/llm/evaluador.py` — `COMPONENTES_DEL_TOTAL` y
  `verificar_aritmetica` (el precedente del tier 1: **al modelo no se le cree el
  `cierra_aritmetica`, se recalcula en código**).
- `src/voucherflow/llm/corrida.py` — línea 281: dónde se sobrescribe el
  `cierra_aritmetica` del modelo con el recalculado.

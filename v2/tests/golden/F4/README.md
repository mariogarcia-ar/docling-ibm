# Subconjunto de paridad F4 (T-405) — extracción

> **Fase**: F4 (extracción) · **Tarea**: T-405 · **Épica**: E-EXT.
>
> Este subconjunto existe para poder medir el **DoD de F4** ("paridad de
> extracción con v1 en campos normalizados sobre el golden set", mitiga R-02/R-08)
> **sin** depender de una corrida con Ollama ni de la curación del golden con
> contador.

## Qué se compara, y por qué así

La paridad de F4 **no** se puede medir como la de F3 (dos salidas del mismo
modelo) por una razón de diseño: **v2 cambió qué devuelve el modelo**.

| | v1 | v2 (ADR-001) |
|---|---|---|
| El prompt devuelve | el dato **ya normalizado y decidido** (`cuit_emisor` cortado, `fecha_emision` ISO, `comprobante_valido`) | el valor **tal como se lee** + su fragmento de sustento |
| Quién normaliza | el modelo, dentro del prompt | el programa (`key_value.py`, T-402) |
| Quién decide | el modelo (`comprobante_valido`, `categoria_gasto`) | la conclusión (F5) |

Entonces la paridad se mide en **dos niveles**, cada uno con su fuente de verdad
objetiva:

### (a) Paridad de las **reglas de normalización** — paridad **estructural**

Las reglas de normalización de v2 son el **port a código** de las reglas que v1
pedía dentro de sus prompts `10`/`11`/`kvi`/`kvg`. La paridad se mide así:

1. Cada regla del subconjunto declara de qué **regla de v1** viene (`regla_v1`,
   `prompt_v1` y el `texto_v1` **literal** del YAML).
2. El test verifica que ese texto siga existiendo en el prompt de v1 (si alguien
   cambiara el YAML, la regla v2 quedaría declarando una procedencia falsa).
3. Se corre la normalización de v2 sobre casos `(crudo → esperado)` **derivados
   del texto de v1** y se exige coincidencia exacta.

Esto es lo que hace verificable la afirmación "se reutilizan las reglas de los
prompts 10/11/kvi/kvg" que pide la tarea: la regla de v1 **cita su origen** y el
origen **existe en el prompt**.

### (b) Paridad **estructural de la extracción** — proyección sobre el JSON plano

Para comparar la extracción sin correr dos veces el modelo, el subconjunto
declara:

- la **lectura** que el modelo de v2 devuelve (valor crudo + sustento), y
- el **JSON plano que v1 habría devuelto** para esa misma lectura
  (`v1_esperado`).

El test corre el pipeline **completo de v2** sobre esa lectura
(interpretar → normalizar → pasada 1 → combinar) y **proyecta** la evidencia
combinada al **shape plano de v1** (`proyectar_a_v1`), comparando campo por campo
con estado `coincide` / `difiere` / `no_comparable`. Es exactamente lo que hace
`paridad_contable.py` de F3: compara los **campos**, no el envoltorio.

### (c) Corrida **real** con v1 — `scripts/F4/paridad_extraccion.py`

Cuando hay Ollama y `v1/`, el script corre **los dos lados de verdad** sobre los
mismos documentos del golden y compara los campos normalizados. Es el nivel que
puede encontrar defectos que la suite determinista no ve (como el bug de R5 que
encontró T-305).

## Casos del subconjunto

| Grupo | Caso | Qué mide |
|---|---|---|
| Normalización | `cuit_digitos_y_guiones` | regla 2b de `11` / IDs de `kvg`: el corte `"20-1 Ing, Brutas: 201641"` → `"20-1"` |
| Normalización | `fechas_iso` | `kvg`: `YYYY-MM-DD` (incluida la fecha escrita en palabras) |
| Normalización | `montos_planos` | `kvg`: número plano sin miles ni símbolo (con signo) |
| Normalización | `comprobante_ppppp` | `kvg`: `PPPPP-NNNNNNNN` → `punto_venta`/`numero_comprobante` |
| Normalización | `moneda` | regla 14 de `11`; **diferencia deliberada**: v1 asumía `ARS`, v2 no |
| Normalización | `productos` | `kvg`: ítems `descripción xcantidad - precio_unitario` |
| Normalización | `descripcion` | regla 8 de `11`: frase breve **en minúsculas** |
| Normalización | `no_inventar` | la regla dura compartida: un ilegible conserva el crudo |
| Extracción | `factura_a_completa` | los 11 campos del contrato, crudo → canónico |
| Extracción | `cuit_truncado_por_ocr` | el caso textual de la regla 2b |
| Extracción | `tique_090_fecha_escrita` | tique con la fecha en palabras |
| Extracción | `factura_b_no_discrimina` | regla 5 de `11`: B → subtotal = total, IVA = 0 |
| Extracción | `modo_generico_kvg` | `proveedor`/`monto`/`productos` del modo genérico |
| Extracción | `valores_no_normalizables` | "no inventar" end-to-end |
| Real | 3 documentos del golden | paridad con v1 sobre documentos reales (`--origen`) |

## Qué explica una diferencia (y qué no es una regresión)

Los montos y las fechas **sí** son comparables: con `normalizar=True` (el default
 de T-402) v2 publica el valor canónico (`12345.67`, `2025-08-14`), que es
justamente el formato que v1 pedía. Las diferencias que el diseño **espera** son
estas, y el script las reconoce con una nota en vez de reportarlas como
regresión:

| Campo | Por qué puede diferir |
|---|---|
| `tipo_comprobante` | v2 publica el valor **tal como se leyó**: su vocabulario de lectura acepta `090`/`099` de los tiques, que el motor R1-R7 de F3 deja fuera por D-13. Comparar contra la **letra final** de v1 mezcla lectura con decisión. |
| `moneda` | v1 aplicaba el default `ARS` sin indicio explícito; v2 **no** (sería inventar la moneda). Un "sin valor" de v2 contra `ARS` de v1 es la diferencia deliberada de T-402. |
| `descripcion` | v1 la redactaba con el modelo; v2 la normaliza en código (minúsculas + espacios colapsados): el texto puede diferir en la forma aunque describa lo mismo. |
| Campos de **decisión** | Fuera de la comparación por ADR-001 (`campos_fuera_de_paridad`). No son un desacuerdo: son el objetivo del rediseño. |
| `razon_social_receptor` | v2 lo lee (está en `CAMPOS_EXTRACCION`, con regla `NORM_TEXTO`) pero el prompt de factura de v1 (`11`) **nunca lo pidió**: no hay valor de v1 contra el que comparar. Se declara en `campos_sin_contraparte_v1` — es "no comparable", no "no implementado". |

### Cobertura declarada de los campos del contrato

El contrato `CAMPOS_EXTRACCION` tiene **16** campos; la paridad compara **15**.
La diferencia es `razon_social_receptor`, que se declara en
`campos_sin_contraparte_v1`. Un test de integridad
(`test_todo_campo_del_contrato_esta_declarado`) exige que **cada** campo del
contrato esté en una de las tres listas —paridad, fuera de paridad o sin
contraparte—: sin ese guard, un campo podía desaparecer de la paridad en
silencio y el conteo parecía cubrir todo el contrato sin cubrirlo.

## Cómo se mide (herramientas)

| Nivel | Herramienta | Requiere |
|---|---|---|
| Reglas de normalización + integridad del subconjunto | `tests/test_extraction_paridad.py` | nada (suite default) |
| Proyección de la extracción a v1 | `tests/test_extraction_paridad.py` | nada |
| Reporte agregado del DoD | `python scripts/F4/t405.py` | nada por defecto; `--origen` requiere Ollama + `v1/` |
| Paridad real con v1 | `python scripts/F4/paridad_extraccion.py` | Ollama + `v1/` |

## Métricas reportadas (DoD de F4)

| Métrica | Fuente | Objetivo |
|---|---|---|
| Exactitud de las reglas de normalización vs. las de v1 | `test_extraction_paridad.py` | 100% (la regla es código: o coincide o es un defecto) |
| Paridad de campos normalizados (proyección) | `test_extraction_paridad.py` | coincidencia campo por campo; diferencias **explicadas** |
| Paridad de campos normalizados (real) | `paridad_extraccion.py` | *paridad **o mejora*** documentada caso por caso (F3-subplan §3.5) |
| Exactitud por campo vs. v1 | `scripts/F4/t405.py` | informativa: la decide el modelo, se reporta sin umbral |
| % de campos con fragmento de sustento | `scripts/F4/t405.py` | 100% (el contrato de F0 exige sostén no vacío) |
| Tasa de acuerdo VLM vs. LLM | `scripts/F4/t405.py` | informativa (alimenta la tabla de precedencia de T-404) |

**Honestidad del alcance** (mismo criterio que T-305): la curación del golden con
contador sigue pendiente, así que esto **no** es la medición del golden completo.
Si la paridad exacta con v1 no se alcanza, el DoD admite **paridad o mejora**
documentada con la diferencia explicada — no un verde artificial. Las diferencias
**esperadas por diseño** (los campos de decisión que v2 ya no lee a propósito)
están declaradas en `campos_fuera_de_paridad` y no cuentan como regresión.

## Estado de etiquetas

| Etiqueta | Estado | Nota |
|---|---|---|
| Campos de lectura (CUIT, fecha, montos, número, moneda) | **derivada** de la regla de v1 | el origen es el texto del prompt, no un juicio de negocio |
| `tipo_comprobante` | **derivada** de la evidencia | el `veredicto` del golden ya tenía sustento objetivo (F2) |
| Campos de decisión | **fuera de alcance** por diseño | ADR-001: los resuelve la conclusión (F5) |
| Montos/fechas en los casos reales | **pendiente** (contador) | por eso la corrida real se reporta sin umbral de exactitud |

## Cómo se amplía

1. Para sumar una regla de normalización: agregarla a `normalizacion` **citando**
   el `prompt_v1` y el `texto_v1` (el test falla si el texto no está en el YAML).
2. Para sumar un caso de extracción: declarar `lectura` + `v1_esperado` y correr
   el test; la proyección se encarga del resto.
3. Cuando el contador cure los montos/fechas del golden completo, la corrida real
   se puede volver a correr con umbral de exactitud.

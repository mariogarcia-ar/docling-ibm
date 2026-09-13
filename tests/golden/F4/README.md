# Subconjunto de extracción F4 (T-405) — extracción

> **Fase**: F4 (extracción) · **Tarea**: T-405 · **Épica**: E-EXT.
>
> Este subconjunto existe para poder medir el **DoD de F4** ("paridad de
> extracción en campos normalizados sobre el golden set", mitiga R-02/R-08)
> **sin** depender de una corrida con Ollama ni de la curación del golden con
> contador.

## Qué se mide, y por qué así

La extracción **no** le pide al modelo el dato ya normalizado: el modelo devuelve
el valor **tal como se lee** + su fragmento de sustento, y el programa normaliza
y decide.

| | El prompt devuelve | Quién normaliza | Quién decide |
|---|---|---|---|
| Diseño del extractor | el valor **tal como se lee** + fragmento de sustento | el programa (`key_value.py`, T-402) | la conclusión (F5) |

Entonces la medición se hace en **dos niveles**, cada uno con su fuente de verdad
objetiva:

### (a) Exactitud de las **reglas de normalización** — verificación estructural

Las reglas de normalización son el **port a código** de las reglas que los
prompts de referencia (`10`/`11`/`kvi`/`kvg`) le pedían al modelo. La medición
se hace así:

1. Cada regla del subconjunto declara su **origen** (`regla_referencia`,
   `prompt_referencia` y el `texto_referencia` **literal** del YAML).
2. El test verifica que ese texto siga existiendo en el prompt de referencia (si
   alguien cambiara el YAML, la regla quedaría declarando una procedencia falsa).
3. Se corre la normalización sobre casos `(crudo → esperado)` **derivados del
   texto de referencia** y se exige coincidencia exacta.

Esto es lo que hace verificable la afirmación "se reutilizan las reglas de los
prompts 10/11/kvi/kvg": la regla **cita su origen** y el origen **existe**.

### (b) Paridad **estructural de la extracción** — proyección sobre el shape plano

Para comparar la extracción sin correr dos veces el modelo, el subconjunto
declara:

- la **lectura** que el modelo devuelve (valor crudo + sostén), y
- el **shape plano esperado** (`esperado_plano`): un diccionario
  `campo → valor normalizado`, sin sostén.

El test corre el pipeline **completo** sobre esa lectura (interpretar →
normalizar → pasada 1 → combinar) y **proyecta** la evidencia combinada al shape
plano, comparando campo por campo con estado `coincide` / `difiere`.

## Casos del subconjunto

| Grupo | Caso | Qué mide |
|---|---|---|
| Normalización | `cuit_digitos_y_guiones` | corte `"20-1 Ing, Brutas: 201641"` → `"20-1"` |
| Normalización | `fechas_iso` | `YYYY-MM-DD` (incluida la fecha escrita en palabras) |
| Normalización | `montos_planos` | número plano sin miles ni símbolo (con signo) |
| Normalización | `comprobante_ppppp` | `PPPPP-NNNNNNNN` → `punto_venta`/`numero_comprobante` |
| Normalización | `moneda` | **diferencia deliberada**: no se asume `ARS` |
| Normalización | `productos` | ítems `descripción xcantidad - precio_unitario` |
| Normalización | `descripcion` | frase breve **en minúsculas** |
| Normalización | `no_inventar` | la regla dura: un ilegible conserva el crudo |
| Extracción | `factura_a_completa` | los campos del contrato, crudo → canónico |
| Extracción | `cuit_truncado_por_ocr` | el caso textual de la regla de corte |
| Extracción | `tique_090_fecha_escrita` | tique con la fecha en palabras |
| Extracción | `factura_b_no_discrimina` | B → subtotal = total, IVA = 0 |
| Extracción | `modo_generico_kvg` | `proveedor`/`monto`/`productos` del modo genérico |
| Extracción | `valores_no_normalizables` | "no inventar" end-to-end |
| Real | 3 documentos del golden | referencia acotada para la extracción real |

## Qué explica una diferencia (y qué no es una regresión)

Los montos y las fechas **sí** son comparables: con `normalizar=True` (el default
de T-402) se publica el valor canónico (`12345.67`, `2025-08-14`). Las diferencias
que el diseño **espera** son estas, y se reconocen con una nota en vez de
reportarse como regresión:

| Campo | Por qué puede diferir |
|---|---|
| `tipo_comprobante` | se publica el valor **tal como se leyó**: el vocabulario de lectura acepta `090`/`099` de los tiques, que el motor R1-R7 de F3 deja fuera por D-13. Comparar contra la **letra final** mezcla lectura con decisión. |
| `moneda` | no se aplica un default `ARS` sin indicio explícito (sería inventar la moneda). |
| `descripcion` | se normaliza en código (minúsculas + espacios colapsados): el texto puede diferir en la forma aunque describa lo mismo. |
| Campos de **decisión** | Fuera de la medición por ADR-001 (`campos_fuera_de_alcance`). No son un desacuerdo: son el objetivo del rediseño. |
| `razon_social_receptor` | el extractor lo lee (está en `CAMPOS_EXTRACCION`, con regla `NORM_TEXTO`) pero el prompt de factura de referencia (`11`) **nunca lo pidió**: no hay valor contra el que comparar. Se declara en `campos_sin_contraparte` — es "no comparable", no "no implementado". |

### Cobertura declarada de los campos del contrato

El contrato `CAMPOS_EXTRACCION` tiene **16** campos; la medición compara **15**.
La diferencia es `razon_social_receptor`, declarada en `campos_sin_contraparte`.
Un test de integridad exige que **cada** campo del contrato esté en una de las
tres listas —medibles, fuera de alcance o sin contraparte—: sin ese guard, un
campo podía desaparecer de la medición en silencio y el conteo parecía cubrir
todo el contrato sin cubrirlo.

## Cómo se mide (herramientas)

| Nivel | Herramienta | Requiere |
|---|---|---|
| Reglas de normalización + integridad del subconjunto | `tests/test_extraction_flujos.py` | nada (suite default) |
| Proyección de la extracción al shape plano | `tests/test_extraction_flujos.py` | nada |
| Reporte agregado del DoD | `python scripts/verificacion/etapa-extraccion.py` | nada (sin Ollama, sin Docling, sin red) |

## Métricas reportadas (DoD de F4)

| Métrica | Fuente | Objetivo |
|---|---|---|
| Exactitud de las reglas de normalización | `scripts/verificacion/etapa-extraccion.py` | 100% (la regla es código: o coincide o es un defecto) |
| Paridad estructural (proyección de campos) | `scripts/verificacion/etapa-extraccion.py` | coincidencia campo por campo; diferencias **explicadas** |
| % de campos con fragmento de sustento | `scripts/verificacion/etapa-extraccion.py` | 100% (el contrato de F0 exige sostén no vacío) |
| Cobertura del modo genérico (`kvg`) | `scripts/verificacion/etapa-extraccion.py` | 100% de las claves obligatorias |

**Honestidad del alcance** (mismo criterio que T-305): la curación del golden con
contador sigue pendiente, así que esto **no** es la medición del golden completo.
Las diferencias **esperadas por diseño** (los campos de decisión que el extractor
ya no lee a propósito) están declaradas en `campos_fuera_de_alcance` y no cuentan
como regresión.

## Estado de etiquetas

| Etiqueta | Estado | Nota |
|---|---|---|
| Campos de lectura (CUIT, fecha, montos, número, moneda) | **derivada** de la regla de referencia | el origen es el texto del prompt, no un juicio de negocio |
| `tipo_comprobante` | **derivada** de la evidencia | el `veredicto` del golden ya tenía sustento objetivo (F2) |
| Campos de decisión | **fuera de alcance** por diseño | ADR-001: los resuelve la conclusión (F5) |
| Montos/fechas en los casos reales | **pendiente** (contador) | por eso la corrida real se reporta sin umbral de exactitud |

## Cómo se amplía

1. Para sumar una regla de normalización: agregarla a `normalizacion` **citando**
   el `prompt_referencia` y el `texto_referencia` (el test falla si el texto no
   está en el YAML).
2. Para sumar un caso de extracción: declarar `lectura` + `esperado_plano` y
   correr el test; la proyección se encarga del resto.
3. Cuando el contador cure los montos/fechas del golden completo, la corrida real
   se puede volver a correr con umbral de exactitud.

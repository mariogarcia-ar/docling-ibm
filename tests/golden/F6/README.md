# Subconjunto de paridad F6 (T-604) — cliente CLI

> **Fase**: F6 (cliente) · **Tarea**: T-604 · **Épica**: E-CLI.
>
> Este subconjunto existe para poder medir el **DoD de F6** —"los comandos de v1
> tienen equivalente en v2 con resultados comparables (o mejores) sobre una muestra
> acordada de `files/`"— **sin** depender de una corrida con Ollama ni de la carpeta
> `files/` (que es temporal e ignorada por git).

## Qué se compara, y por qué así

La paridad de F6 **no** se puede medir igual que la de F3 o F4. Ahí se comparaban
dos salidas del mismo modelo sobre el mismo documento. Acá eso mediría lo que
**ADR-001 y ADR-006 cambiaron a propósito**:

| | v1 | v2 |
|---|---|---|
| Quién lee el documento | el modelo, con un prompt por modo (`10`/`11`/`kvi`/`kvg`) | dos flujos (VLM + LLM) con **un** contrato de evidencia |
| Quién normaliza | el modelo, dentro del prompt | el programa (`key_value.py`, T-402) |
| Quién decide el tipo/letra | el modelo (`comprobante_valido`) | el motor R1-R7 en código (F3/T-301) + reglas cruzadas (F5/T-501) |
| Qué devuelve | el dato ya decidido | el valor leído + su fragmento de sustento |

Comparar el veredicto de v1 contra el de v2 y declararlo "comparable" mezclaría una
**mejora de diseño** con una **regresión**. Por eso la paridad se mide en dos
niveles que sí tienen sustento objetivo:

### (a) Paridad de la **superficie de invocación**

Cada script de v1 con CLI tiene su equivalente en v2, y sus banderas **no se
perdieron**. El test verifica, contra el parser **real** del CLI (introspección de
`argparse`, no una lista escrita a mano):

1. que el comando de v2 exista;
2. que acepte las banderas que el subconjunto declara equivalentes;
3. que cada bandera de v1 que **no** tiene equivalente esté declarada como tal y
   con su motivo.

Esto es lo que hace verificable la frase "los comandos de v1 tienen equivalente en
v2": si alguien renombra o borra un comando o un flag, el test lo dice.

### (b) Paridad de los **artefactos**

Cada corrida produce archivos con nombre y forma. Los que **coinciden** con v1 se
verifican como coincidentes (el nombre del checkpoint contable, el `.md` por
posición, el `.raw.md`) y los que **cambian** se declaran con su motivo — no se
declaran "iguales" ni se omiten.

La distinción importa: `ocr_documents.py` deja `<doc>.md` y v2 también (paridad
real, verificada por un test); el checkpoint del lote cambia de
`<doc>_pipeline.json` a `<doc>.batch.json` **porque ahora guarda el hash del
contenido** (reanudar por nombre saltea un documento que cambió). Una diferencia
con motivo no es una brecha: es una decisión documentada.

### (c) Equivalencias de capacidad (lo que **mejora**)

Capacidades donde v2 no tiene contraparte en v1 o la supera. Se declaran como
`mejor` o `diferente_por_diseno` — nunca como "paridad"— y cada una apunta a dónde
se midió su calidad (por ejemplo, la exactitud de la letra se midió en F3/T-305:
v2 5/5 vs v1 2/5).

## La muestra

`v2/tests/fixtures/golden` — la copia estable y versionada que el repo mantiene
como referencia.

**Por qué no `files/` directamente**: `files/` es temporal y está ignorada por git
(ver `tests/conftest.py`), así que una medición que dependa de ella no es
reproducible entre clones. El DoD pide "una muestra acordada" y la copia del golden
**es** ese acuerdo: el conjunto de documentos que el repo conserva a propósito. El
script sí puede correr contra `files/` (`--real`), pero eso es una medición
informativa, no un criterio de la suite.

## Qué queda fuera de paridad (y dónde se mide)

`fuera_de_paridad` del subconjunto declara cada comparación que **no** se hace, con
su motivo:

| Queda fuera | Por qué | Dónde se mide |
|---|---|---|
| El veredicto final documento a documento | v1 no separa lectura de decisión: comparar salidas mezcla diseño con regresión | `scripts/F3/paridad_11_1.py` (letra: v2 5/5 vs v1 2/5) y `scripts/F4/paridad_extraccion.py` (reglas 20/20, campos 29/29, sostén 32/32) |
| El YAML de prompt elegible por bandera (`-p`) | en v2 los prompts son código versionado (ADR-006): no hay archivo que el operador pase | No aplica: reemplazado |
| El flujo WSAA de `consultar_arca` | ADR-003: el hook ARCA es opcional y no bloquea el MVP | No aplica (fuera de alcance) |
| La corrida real con modelos sobre `files/` | Requiere Ollama con los modelos de cada rol y `files/` (no versionada) | `scripts/F6/paridad_cli.py --real` (informativa) |

**Clave de honestidad** (misma convención que F3/T-305, F4/T-405 y F5/T-507): un
test exige que **todo** comando del subconjunto esté declarado con su equivalente y
que toda bandera de v1 esté en una de las dos listas (equivalente o sin
equivalente con motivo). Así "no comparable" no se puede leer como "no
implementado": si algo no está, el test falla.

## Archivos

| Archivo | Qué es |
|---|---|
| `subconjunto.json` | El manifiesto: comandos, artefactos, equivalencias de capacidad y lo que queda fuera |
| `README.md` | Este documento |
| `../../scripts/F6/paridad_cli.py` | Las funciones puras de comparación (las usa el test por `importlib`, sin ejecutar `main`) |
| `../../scripts/F6/t604.py` | El reporte: corre el nivel determinista y imprime el mapa de paridad; sale con ≠ 0 si falla |
| `../../tests/test_paridad_t604.py` | La verificación en la suite default (sin red, sin modelos) |

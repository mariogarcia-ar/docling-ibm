# Extracciones esperadas — lecturas de referencia

> **Plan**: [`docs/plan/07-extracciones-esperadas.md`](../../docs/plan/07-extracciones-esperadas.md)
> **Generado por**: `scripts/operacion/generar-extracciones-esperadas.py`
> **Versión del artefacto**: 1.0 (2026-09-14)

---

## ⚠️ Leer esto primero: esto NO es un golden

**Este artefacto no contiene "la verdad".** Contiene la lectura de **otro modelo**
(DeepSeek) sobre 29 comprobantes reales. Sirve para medir **acuerdo** y
**divergencia** entre ese modelo y el pipeline local — **nunca** para declarar que
el pipeline es correcto.

Si los dos modelos se equivocan igual, el acuerdo es 100 %.

| | Golden set (`tests/golden/`) | **Esto** |
|---|---|---|
| Qué contiene | La **verdad** del negocio | La **lectura de un modelo** |
| Quién lo produce | Un contador (validado por una 2ª persona) | DeepSeek, **sin revisión** |
| Mide | **Exactitud** | **Acuerdo** |
| Estado | Curado a mano | Automático |

---

## 🔴 La referencia tiene errores medidos

**5 de los 28 CUIT del dataset tienen el dígito verificador inválido** (18 %). No
son typos: ninguno se corrige cambiando un solo dígito. Son transcripciones mal
leídas.

| CUIT (referencia) | Emisor |
|---|---|
| `30-62221785-4` | Diarco S.A. |
| `30586221578` | HOTEL JARDIN SRL |
| `30-71530218-5` | ROCK & FELLERS |
| `30-71144495-3` | José Genna Repuestos S.R.L. |
| `30-70715163-1` | ALESO S.R.L. - SAN LORENZO |

### ⚠️ Qué significa esto en la práctica

**Si el pipeline local lee BIEN uno de esos 5 CUIT, la comparación lo va a marcar
como `difiere`** — o sea, un acierto se cuenta como error.

⚠️ **Y no hay nada en el dato que lo desambigüe.** El auto-chequeo del dígito
verificador quedó **fuera de alcance** por decisión explícita (D-6 del plan), así
que esos 5 valores **viajan sin marcar**, igual que los 23 válidos.

**La mitigación es esta lista.** Si al comparar aparece un `difiere` en
`cuit_emisor`, consultá primero si el documento es uno de estos 5: mirá el campo
`razon_social_emisor` de la lectura.

> Reabrir esa decisión cuesta ~0,3 dh y no obliga a rehacer el artefacto: se
> agrega la marca y los estados `referencia_dudosa` / `pipeline_dudoso`.

---

## ⚠️ La referencia no es reproducible

Medido sobre el **único** documento con dos corridas del mismo modelo
(`9fa45f1d-f6ad-4cea-b585-432aa3b39dad`):

| | Corrida 1 | Corrida 2 |
|---|---|---|
| `completion_tokens` | 2.241 | **4.179** (1,9x) |
| `costo_usd` | US$ 0,002766 | **US$ 0,005092** (**1,8x**) |
| Campos estructurados que difieren | **0 de 23** | — |
| `observaciones` · `rubro_emisor` | **difieren** | **difieren** |

**Cómo leerlo:**

- ✅ **Los campos estructurados son estables.** El mismo modelo coincide consigo
  mismo en todos los campos del contrato: mismo CUIT, mismo total, misma
  aritmética (cierra con diferencia `-0.0`). **La conclusión del documento no se
  mueve.**
- ⚠️ **La prosa NO es estable**, y por eso **no puntúa** en la comparación
  (`observaciones`, `rubro_emisor`): si puntuara, el ruido del modelo se leería
  como desacuerdo de lectura.
- ⚠️ **El costo no es proyectable**: dos corridas idénticas difirieron 1,8x. Ya
  estaba documentado con más amplitud: cuatro corridas de la misma imagen dieron
  completion **528 / 3.346 / 8.999 / 12.632** (**10x**), por los tokens de
  razonamiento (*thinking mode*) que se facturan como `completion_tokens`.

⚠️ **La variabilidad se midió con n=1**: ese documento tiene réplica **por
casualidad** (estaba en los dos lotes), no por diseño.

---

## Qué hay adentro

```text
tests/expected-extraction/
  README.md                  este archivo
  manifiesto.json            índice: una entrada por corrida
  mapa_de_campos.json        el mapa lab ↔ pipeline (única fuente de verdad)
  <documento-id>/            = stem del archivo de imagen
    <modelo>/                ej. deepseek-flash
      <corrida>/             timestamp UTC: 20260914T170509Z
        extraccion.json      la lectura, TAL CUAL salió del lab
```

Las imágenes que no estaban versionadas se copiaron a
`tests/fixtures/expected-extraction/` (subdirectorio propio, ver abajo).

### Por qué `<modelo>/<corrida>` y no una carpeta plana

1. **El mismo modelo produce lecturas distintas entre corridas** (ver arriba): sin
   el nivel `corrida`, dos lecturas del mismo modelo **se pisan** en silencio.
2. **Permite ampliar el eje `modelo`** sin migrar nada (carpeta plana forzaría un
   nombre como `doc-gemini.json`, y el mapeo pasaría a vivir en el nombre del
   archivo, que nadie valida).
3. **Habilita separar** *"el pipeline difiere de la referencia"* de *"la
   referencia no coincide consigo misma"*.

⚠️ **La ruta guarda `id`/`modelo`/`corrida`, pero NO el `prompt_hash`.** Dos
corridas del mismo modelo pueden haber usado prompts distintos (el prompt es
editable). Por eso `version_prompt` y `prompt_hash` están en el manifiesto: **la
ruta no es el único lugar donde vive el contexto**.

### Por qué las imágenes van a `tests/fixtures/expected-extraction/`

No es una preferencia estética: es una restricción de la suite existente.

- `tests/test_fixtures.py` fija que `manifest.json` tenga **exactamente 50**
  archivos con conteos por grupo (10 grandes + 10 chicos + 20 otros + 5 + 5).
  Agregarlas a los grupos existentes **rompe la suite**. Por eso van a un
  subdirectorio propio y se declaran en **este** manifiesto, no en el de fixtures.

⚠️ **Efecto secundario que conviene saber** (está cubierto por un test, pero no es
obvio): `tests/test_validation_vistas.py` elige "la primera imagen" con
`sorted(fixtures_dir.rglob("*.jpg"))`. Medido: **este subdirectorio se ordena
primero** (alfabéticamente `chicos` < `expected-extraction` < `golden` <
`grandes`…), así que esa búsqueda ahora devuelve `expected-extraction/0fc44015-…jpg`
en vez del fixture de `golden/` que devolvía antes.

Eso **no rompe nada**, y la razón importa: ese test es agnóstico a la imagen —
verifica que la vista rápida se derive sin excepciones y que la calidad sea
`baja`/`media`, y `preparar_vista_rapida` **no mira las dimensiones** (la calidad
se deriva de `tipo_vista`, no del tamaño). Cualquier imagen legible sirve. Se
verificó corriendo los tests, no leyendo el código.

---

## Cobertura real (lo que se puede medir)

| | |
|---|---|
| Corridas | **30** |
| Documentos únicos | **29** (uno está en los dos lotes) |
| Con imagen versionada | **30 / 30** |
| Modelo | `deepseek-flash` en las 30 |

| Dimensión | Distribución | Lectura |
|---|---|---|
| `tipo_comprobante` | A ×23 · B ×1 · C ×1 · 090 ×1 · 083 ×1 · texto libre ×1 · `null` ×2 | **Muy sesgada a A.** No hay E, M ni 099. |
| `legibilidad` | `buena` ×30 | **Sin casos difíciles**: el corpus se redujo a lado mayor ~1036 px. |
| DV del `cuit_emisor` | válido ×23 · **inválido ×5** | Ver arriba. |
| Aritmética (recalculada) | cierra ×28 · **no cierra ×2** | Los 2 que no cierran son **los más valiosos**: señalan un importe mal leído. |

⚠️ **No se puede extrapolar** nada de este dataset al corpus completo: 23 de 30 son
facturas A y ninguna está rotada ni borrosa.

---

## Cómo se compara

La comparación **no** es string contra string. Los dos modelos no devuelven el
valor en la misma forma:

| | El lab devuelve | El pipeline espera |
|---|---|---|
| Monto | `52069.85` (número JSON) | `"52.069,85"` (texto impreso) → normaliza |
| Fecha | `"29/08/2025"` | igual, pero publica `"2025-08-29"` |

⚠️ **Sin normalizar, 9 de 10 fechas darían un `difiere` falso.** Se compara el
**valor canónico de las dos puntas**, pasando por los normalizadores del pipeline
(`normalizar_campo` / `normalizar_evidencia`), que ya aceptan las dos formas.

### Los `null` entran en la comparación

Hay **19 valores `null`** en las 10 extracciones de `validations`. Un `null` en el
lab es una **declaración** ("no lo pude leer"), y descartarlo borraría la
diferencia más interesante: **una punta dice "no lo leí" y la otra dice un valor**.

### Los cinco estados

| Estado | Significa |
|---|---|
| `coincide` | Los dos leyeron lo mismo. |
| `coincide_normalizado` | Lo mismo tras normalizar (la fecha). **Se cuenta aparte.** |
| `difiere` | Valores distintos. ⚠️ **Puede ser un acierto del pipeline** (ver los 5 CUIT). |
| `ausente` | Una o las dos puntas no lo leyeron. |
| `no_comparable` | Sin contraparte, fuera del contrato, o sin puntuar (la prosa). |

---

## Cómo regenerarlo

```bash
python scripts/operacion/generar-extracciones-esperadas.py --listar   # ver las fuentes
python scripts/operacion/generar-extracciones-esperadas.py --dry-run  # simular
python scripts/operacion/generar-extracciones-esperadas.py            # generar
```

⚠️ **El script NO es destructivo**: una corrida ya versionada se respeta. Un
regenerado que sobreescribiera borraría la única copia de una lectura cuyo
original ya no esté en `var/`.

### ⚠️ Límite de la regeneración

El script puede re-extraer de `var/` **lo que todavía esté ahí**. No puede
recuperar lo que se haya borrado — y `var/` está en `.gitignore` y se puede
borrar. **Si `var/` desaparece, este artefacto es la única copia de esas 30
lecturas.** Ese es, de hecho, el motivo por el que se versiona.

---

## Honestidad de alcance

1. **No mide exactitud.** Mide acuerdo entre dos modelos.
2. **La referencia tiene errores medidos**: 5 de 28 CUIT con DV inválido, **sin
   marcar en el dato** (D-6). La lista está arriba.
3. **La referencia no es reproducible** en prosa y en costo; sí en los campos que
   puntúan. Medido con **n=1**.
4. **El corpus está sesgado** (23/30 facturas A, todas legibles).
5. **DeepSeek tiene errores de comportamiento**: en un caso declaró bien
   `cuit_emisor: null` (tapado por cinta); en otra corrida de la **misma** imagen
   **confundió emisor con receptor**. Un desacuerdo puede ser un error **de la
   referencia**.

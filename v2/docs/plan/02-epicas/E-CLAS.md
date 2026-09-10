# Épica E-CLAS — Clasificación (Seguimiento)

> Documento de seguimiento generado a partir de
> [`02-epicas-historias-usuario.md`](../02-epicas-historias-usuario.md) y
> [`05-plan-ejecucion.md`](../05-plan-ejecucion.md).

## 1. Ficha de la épica

| Campo | Valor |
|---|---|
| **Código** | E-CLAS |
| **Objetivo(s) que cubre** | OBJ-3 — Refactorizar la clasificación (tipo/letra + contable 01→02→03) |
| **Fuente de ideas** | Prompt WIP `prompts/wip/deteccion_tipo_factura.yaml` (R1-R7) + `11.1` + prompts 01/02/03 |
| **Módulo de librería** | `classification/` |
| **Fase(s) del plan** | F3 (T-301..T-305) |
| **Prioridad MoSCoW** | Must (MVP) — E-CLAS-1 (tipo/letra) y E-CLAS-2 (contable) |
| **Responsable ciclo** | team analysis (BA/SA/PM) → team implementation |
| **Estado épica** | 🟢 DoD verificado (F3: T-301..T-305 hechas; cierre documental pendiente) |
| **DoR cumplido** | [x] sí |
| **Fecha inicio** | 2026-09-10 |
| **Fecha fin** |  |

## 2. Definition of Done de la épica (criterios de aceptación a nivel épica)

- [x] Las reglas R1-R7 del prompt WIP se migran a un motor de reglas en código (sin depender del prompt para decidir la letra). — **T-301** (`rules/tipo_comprobante_rules.py` + `clasificar_tipo_comprobante()`).
- [x] El prompt `11.1` reescrito devuelve evidencia (VLM recuadro + LLM texto) y reglas raw por fuente con candidatos_descartados/candidatos_restantes/reglas_aplicadas. — **T-302** (evidencia: `prompt_tipo_comprobante.py` + `evidencia.py`) y **T-303** (reglas raw: `rules/raw.py`; los candidatos curados y las `debilidades`/`valida` de cada fuente salen de ahí).
- [x] La letra (A/B/C/M/E) se determina cruzando la condición fiscal esperada por negocio y la letra detectada, disparando alerta/conflicto cuando corresponde (R7). — **T-301** (pendiente: reemplazar el lector por el real en T-302/T-303).
- [x] La cadena contable 01→02→03 queda refactorizada con contratos entre pasos (centro de costo → macro categoría → concepto/código), incluyendo el default CC0006. — **T-304** (`classification/contable.py` + `prompts_contable.py`: `OpcionCentroCosto`/`OpcionMacroCategoria`/`PasoConceptoCodigo`, `primary_*`, checkpoints y `clasificar_contable()` como variante pura).
- [x] Paridad verificable con v1 sobre el golden set: equivalencia con `classification_pipeline.py` y `-M 11.1` de v1; tests unitarios de reglas R1-R7 (DoD de F3 en `05-plan-ejecucion.md`). — **T-305**: subconjunto `tests/golden/F3/` + `scripts/F3/paridad_contable.py` (**8/8** campos coinciden), `scripts/F3/paridad_11_1.py` (exactitud de letra **v2 5/5** vs. **v1 2/5**) y `tests/test_classification_paridad.py`.
- [x] Documentación/contratos actualizados (README/ADR si cambia una decisión). — **T-305** registró el ajuste del patrón de R5 (ver bitácora) y el subconjunto de paridad con su criterio de etiquetas.

## 3. Historias de usuario y seguimiento

### E-CLAS-1 · Detección de tipo y letra de comprobante (con reglas determinísticas)
- **Estado**: [ ] Pendiente · [x] **En desarrollo** (T-301, T-302 y T-303 hechas) · [ ] En QA · [ ] Hecho
- **Responsable**: team analysis / team implementation
- **Como** auditor contable,
  **quiero** que la letra (A/B/C/M/E) se determine cruzando la condición fiscal
  esperada por negocio y la letra detectada en el documento
  **para** detectar inconsistencias y evitar aceptar comprobantes no válidos para
  crédito fiscal.
- **Criterios de aceptación (Gherkin):**

```gherkin
Regla: R1 emisor monotributo/exento
  Dado un emisor Monotributo o Exento
  Cuando se aplican las reglas de negocio
  Entonces el tipo esperado es C (independiente del receptor)

Regla: R2A / R2B responsabilidad fiscal
  Dado un emisor Responsable Inscripto y un receptor Responsable Inscripto
  Cuando se aplican las reglas de negocio
  Entonces el tipo esperado es A

  Dado un emisor Responsable Inscripto y un receptor Monotributo/Exento/Consumidor Final
  Cuando se aplican las reglas de negocio
  Entonces el tipo esperado es B

Regla: R3 exportación
  Dado un receptor con país distinto de Argentina
  Cuando se aplican las reglas de negocio
  Entonces el tipo esperado es E (prioridad sobre R1/R2)

Regla: R4/R5 extracción OCR/VLM
  Dado un documento con recuadro de letra grande en el encabezado
  Cuando se detecta la letra en el documento
  Entonces se usa la letra del recuadro (VLM prioriza lo que ve)
  Y si no hay recuadro, se usa regex sobre el texto (FACTURA [A-CME])

Regla: R6 inferencia por campos totales
  Dado que no hay letra visible en recuadro ni texto
  Cuando se infiere el tipo
  Entonces se usa el desglose de campos totales (discriminado => A candidato; subtotal único => B/C)

Regla: R7 conflicto financiero
  Dado un emisor Responsable Inscripto, receptor Responsable Inscripto y letra detectada B
  Cuando se aplican las reglas de conflicto
  Entonces se dispara alerta de comprobante inválido para crédito fiscal
  Y el resultado conserva el tipo esperado por negocio con confianza media y la discrepancia documentada

Regla: candidatos
  Cuando se produce el resultado de clasificación
  Entonces se incluyen candidatos_descartados, candidatos_restantes y reglas_aplicadas
```

### E-CLAS-2 · Clasificación contable 01→02→03 (centro de costo → macro categoría → concepto/código)
- **Estado**: [ ] Pendiente · [x] **En desarrollo** (T-304 y T-305 hechas: DoD de la historia verificado) · [ ] En QA · [ ] Hecho
- **Responsable**: team analysis / team implementation
- **Como** área de administración,
  **quiero** clasificar cada comprobante en centro de costo, macro categoría y
  concepto/código final usando la cadena de prompts encadenados
  **para** asignar correctamente el gasto.
- **Criterios de aceptación (Gherkin):**

```gherkin
Dado un comprobante con proveedor, descripcion y monto
Cuando se ejecuta el paso 01
Entonces devuelve hasta tres centros de costo ordenados por probabilidad
Y cada opción incluye codigo, centro, confianza, senal_usada y justificacion

Dado el primer centro de costo resultante del paso 01
Cuando se ejecuta el paso 02
Entonces devuelve hasta tres macro categorías

Dado el primer resultado de 02 + condición impositiva
Cuando se ejecuta el paso 03
Entonces devuelve concepto y código final

Regla: default
  Dado un gasto sin señal específica
  Cuando se ejecuta el paso 01
  Entonces se devuelve CC0006 con confianza baja y senal_usada=none
```

## 4. Bitácora de seguimiento

| Fecha | Acción / hito | Responsable | Estado |
|---|---|---|---|
| 2026-09-10 | **E-CLAS-1 (T-301)**: R1-R7 migradas del prompt WIP al motor de reglas en código (`rules/contexto.py`, `rules/tipo_comprobante_rules.py` con `REGISTRO_NEGOCIO`/`REGISTRO_LECTURA`/`REGISTRO_CONFLICTO`) y `clasificar_tipo_comprobante()` implementado con `preferencia_letra`. Los criterios Gherkin de R1/R2A/R2B/R3/R4/R5/R6/R7 y de candidatos quedan cubiertos por tests unitarios (`tests/test_rules_tipo_comprobante.py`, 81 tests) y verificados por `scripts/F3/t301.py` (14/14). | team implementation | Hecho |
| 2026-09-10 | **E-CLAS-1 (T-302)**: `11.1` reescrito como prompt de **evidencia** versionado (`tipo-comprobante@1`): el modelo reporta la lectura (recuadro del VLM / texto del LLM) con su fragmento de sustento y **no** decide la letra. `classification/evidencia.py` normaliza la lectura al vocabulario del motor (D-13), construye `SourceEvidence` (ADR-001), corre las dos fuentes conservando ambas evidencias (ADR-002) y puebla el contexto de R4/R5 para que decida el motor de T-301. Tests: `tests/test_classification_prompt_tipo.py` (50); herramienta: `scripts/F3/t302.py` (10/10, punta a punta T-302 → T-301). | team implementation | Hecho |
| 2026-09-10 | **E-CLAS (T-305)**: subconjunto de paridad de F3 (`tests/golden/F3/`) y las tres herramientas — `paridad_contable.py` (**8/8** campos de la cadena contable coinciden con v1 sobre 2 casos), `paridad_11_1.py` (exactitud de letra **v2 5/5** vs. **v1 2/5** sobre los 5 casos etiquetados; v1 no concluyó en 4) y `tests/test_classification_paridad.py` (32 tests deterministas: fidelidad de prompts contra los YAML de v1, integridad del subconjunto, paridad de letra y lógica de comparación). **Hallazgo**: bug real de R5 (`\s+` cruzaba el salto de línea del markdown de Docling y tomaba la letra de la línea siguiente: `"FACTURA\n  Código: 1"` → `C`) que sobrevivía porque en v1 el patrón nunca se ejecutó como código; corregido a `[ \t]+` + `\b` con test de regresión. | team implementation | Hecho |

## 5. Referencias cruzadas

- Historias fuente: [`02-epicas-historias-usuario.md`](../02-epicas-historias-usuario.md)
- Objetivo y alcance: [`01-vision-alcance.md`](../01-vision-alcance.md) (OBJ-3)
- Fases/tareas/DoR-DoD: [`05-plan-ejecucion.md`](../05-plan-ejecucion.md) (F3: T-301..T-305; depende de ADR-006)
- Calidad/golden set: [`06-estrategia-calidad.md`](../06-estrategia-calidad.md)
- Dependencias: E-LIB (motor de reglas), E-DOC/E-EXT (markdown/evidencia de entrada)

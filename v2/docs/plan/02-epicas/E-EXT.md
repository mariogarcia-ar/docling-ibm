# Épica E-EXT — Extracción (Seguimiento)

> Documento de seguimiento generado a partir de
> [`02-epicas-historias-usuario.md`](../02-epicas-historias-usuario.md) y
> [`05-plan-ejecucion.md`](../05-plan-ejecucion.md).

## 1. Ficha de la épica

| Campo | Valor |
|---|---|
| **Código** | E-EXT |
| **Objetivo(s) que cubre** | OBJ-4 — Refactorizar la extracción (flujos VLM + LLM paralelos con contrato común) |
| **Fuente de ideas** | `v2/docs/ideas/algoritmo.md` + `v2/docs/ideas/flujo_deteccion_tipo_comprobante.md` (+ prompts 10/11/kvi/kvg como referencia de reglas) |
| **Módulo de librería** | `extraction/` |
| **Fase(s) del plan** | F4 (T-401..T-405) |
| **Prioridad MoSCoW** | Must (MVP) |
| **Responsable ciclo** | team analysis (BA/SA/PM) → team implementation |
| **Estado épica** | 🔴 Backlog |
| **DoR cumplido** | [ ] pendiente |
| **Fecha inicio** |  |
| **Fecha fin** |  |

## 2. Definition of Done de la épica (criterios de aceptación a nivel épica)

- [ ] Ambos flujos (VLM sobre imagen y LLM sobre OCR/Markdown) corren SIEMPRE en paralelo sobre cada comprobante, sin elegir uno por documento.
- [ ] Cada flujo devuelve evidencia por campo con el mismo esquema (campo, valor, fuente, fragmento de sustento) conforme al schema `SourceEvidence` (T-001).
- [ ] Las reglas raw por fuente (pasada 1) marcan como debilitada a una fuente internamente inconsistente antes de combinarse.
- [ ] La combinación de evidencia resuelve por campo con precedencia (ADR-002) y conserva trazabilidad de cada fuente.
- [ ] Los campos se normalizan (CUIT, fechas ISO, montos, punto_venta/número) sin inventar datos ausentes; en modo auditoría los no-comprobantes devuelven comprobante_valido=false con motivo_rechazo.
- [ ] Paridad verificable con v1 sobre el golden set: equivalencia con `extraction_pipeline.py` (10/11) y `document_extraction.py` (kvi/kvg) en campos normalizados (DoD de F4 en `05-plan-ejecucion.md`).
- [ ] Documentación/contratos actualizados (README/ADR si cambia una decisión).

## 3. Historias de usuario y seguimiento

### E-EXT-1 · Flujos VLM y LLM en paralelo con contrato de evidencia
- **Estado**: [ ] Pendiente · [ ] En desarrollo · [ ] En QA · [ ] Hecho
- **Responsable**: team analysis / team implementation
- **Como** sistema,
  **quiero** ejecutar siempre ambos flujos (VLM y LLM) sobre cada comprobante y que
  cada uno devuelva evidencia con el mismo esquema (campo, valor, fuente,
  fragmento de sustento)
  **para** poder compararlos programáticamente en la conclusión.
- **Criterios de aceptación (Gherkin):**

```gherkin
Dado un comprobante confirmado
Cuando se ejecuta la extracción
Entonces corren el flujo VLM (imagen) y el flujo LLM (OCR/Markdown) en paralelo
Y cada flujo devuelve evidencia por campo con fuente y fragmento de sustento

Regla: ambos siempre
  Dado cualquier comprobante
  Cuando se ejecuta la extracción
  Entonces NO se elige un flujo u otro por documento: corren ambos

Regla: modalidades de la librería v1
  Dado un archivo .md o .jpg
  Cuando se invoca el modo de extracción equivalente a kvi/kvg/10/11
  Entonces el resultado conserva la capacidad actual (JSON plano normalizado)
  Y agrega el envoltorio de evidencia
```

### E-EXT-2 · Validación de evidencia cruda por fuente (pasada 1)
- **Estado**: [ ] Pendiente · [ ] En desarrollo · [ ] En QA · [ ] Hecho
- **Responsable**: team analysis / team implementation
- **Como** sistema,
  **quiero** validar la evidencia de cada fuente por separado antes de mezclarla
  **para** marcar como debilitada a una fuente internamente inconsistente (ej.
  dice Factura A pero no detectó los dos CUIT que exige esa letra).
- **Criterios de aceptación (Gherkin):**

```gherkin
Dado que la evidencia VLM dice Factura A sin detectar dos CUIT
Cuando se aplican las reglas raw
Entonces la fuente VLM queda marcada como debilitada antes de combinarse

Dado que la evidencia de una fuente pasa sus propias reglas
Cuando se combinan las fuentes
Entonces esa evidencia participa de la combinación con su trazabilidad
```

### E-EXT-3 · Campos de extracción key-value normalizados
- **Estado**: [ ] Pendiente · [ ] En desarrollo · [ ] En QA · [ ] Hecho
- **Responsable**: team analysis / team implementation
- **Como** consumidor de datos,
  **quiero** recibir los campos fiscales y comerciales normalizados
  (CUIT, fechas ISO, montos, tipo, punto de venta, número, ítems)
  **para** alimentar la clasificación contable y la conciliación.
- **Criterios de aceptación (Gherkin):**

```gherkin
Regla: normalización
  Dado el OCR de un comprobante
  Cuando se extraen campos
  Entonces los CUIT son solo dígitos y guiones propios (corte ante caracteres extraños)
  Y las fechas se normalizan a YYYY-MM-DD
  Y los montos son numéricos sin separadores de miles
  Y se separa punto_venta y numero_comprobante de PPPPP-NNNNNNNN

Regla: no inventar
  Dado un dato ausente o ilegible
  Cuando se extraen campos
  Entonces se omite o se deja null; NO se inventa

Regla: validación de comprobante
  Dado un texto que no es comprobante o está corrupto
  Cuando se extrae con el modo auditoría
  Entonces comprobante_valido=false y se completa motivo_rechazo
```

## 4. Bitácora de seguimiento

| Fecha | Acción / hito | Responsable | Estado |
|---|---|---|---|
|  | | | |

## 5. Referencias cruzadas

- Historias fuente: [`02-epicas-historias-usuario.md`](../02-epicas-historias-usuario.md)
- Objetivo y alcance: [`01-vision-alcance.md`](../01-vision-alcance.md) (OBJ-4)
- Fases/tareas/DoR-DoD: [`05-plan-ejecucion.md`](../05-plan-ejecucion.md) (F4: T-401..T-405; depende de ADR-002 y schemas T-001)
- Calidad/golden set: [`06-estrategia-calidad.md`](../06-estrategia-calidad.md)
- Dependencias: E-QWE (vista fiel de entrada), E-LIB (schemas), E-CLAS/E-CONC (consumen la evidencia combinada)

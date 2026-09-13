# Épica E-QWE — Validación qween doble paso (Seguimiento)

> Documento de seguimiento generado a partir de
> [`02-epicas-historias-usuario.md`](../02-epicas-historias-usuario.md) y
> [`05-plan-ejecucion.md`](../05-plan-ejecucion.md).

## 1. Ficha de la épica

| Campo | Valor |
|---|---|
| **Código** | E-QWE |
| **Objetivo(s) que cubre** | OBJ-2 — Refactorizar qween (validación de comprobante / doble paso con distinta calidad) en un módulo |
| **Fuente de ideas** | `v2/docs/ideas/qween.md` |
| **Módulo de librería** | `validation/` |
| **Fase(s) del plan** | F2 (T-201..T-204) |
| **Prioridad MoSCoW** | Must (MVP) |
| **Responsable ciclo** | team analysis (BA/SA/PM) → team implementation |
| **Estado épica** | 🔴 Backlog |
| **DoR cumplido** | [ ] pendiente |
| **Fecha inicio** |  |
| **Fecha fin** |  |

## 2. Definition of Done de la épica (criterios de aceptación a nivel épica)

- [ ] El gate decide con una vista barata (thumbnail/calidad baja) y un prompt binario corto con solo 3 salidas: comprobante | no_comprobante | indeterminado.
- [ ] Los documentos no-comprobante se rechazan o reencolan sin ejecutar la extracción costosa.
- [ ] Los indeterminados se redeciden con una vista de revisión de mayor calidad; si siguen sin ser comprobante, se rechazan.
- [ ] Para comprobantes confirmados se prepara una vista fiel (resolución suficiente, orientación corregida, limpieza leve) que NO reutiliza la vista rápida degradada.
- [ ] La vista fiel conserva tabla, sello, firma, QR y texto pequeño cuando existen; comprobantes complejos consideran enfoque por zonas/partes.
- [ ] Paridad verificable con v1 sobre el golden set: tests de ahorro de costo (no extraer no-comprobantes) y de calidad distinta entre vistas (DoD de F2 en `05-plan-ejecucion.md`).
- [ ] Documentación/contratos actualizados (README/ADR si cambia una decisión).

## 3. Historias de usuario y seguimiento

### E-QWE-1 · Gate de decisión rápida "¿es comprobante?"
- **Estado**: [ ] Pendiente · [ ] En desarrollo · [ ] En QA · [ ] Hecho
- **Responsable**: team analysis / team implementation
- **Como** sistema de rendiciones,
  **quiero** decidir con una vista barata y un prompt binario si un documento es
  comprobante
  **para** no gastar calidad/costo en documentos que no corresponden.
- **Criterios de aceptación (Gherkin):**

```gherkin
Regla: respuesta binaria con vista reducida
  Dado un documento candidato
  Cuando se ejecuta la validación rápida
  Entonces se usa una vista simplificada/thumbnail (calidad baja/moderada)
  Y el modelo responde solo: comprobante | no_comprobante | indeterminado

Regla: no comprobante
  Dado que la validación rápida responde no_comprobante
  Cuando se procesa el documento
  Entonces se rechaza o reencola
  Y NO se ejecuta la extracción costosa

Regla: indeterminado
  Dado que la validación rápida responde indeterminado
  Cuando se procesa el documento
  Entonces se prepara una vista de revisión de mayor calidad
  Y se vuelve a decidir; si sigue sin ser comprobante, se rechaza
```

### E-QWE-2 · Preparación de vista fiel para extracción
- **Estado**: [ ] Pendiente · [ ] En desarrollo · [ ] En QA · [ ] Hecho
- **Responsable**: team analysis / team implementation
- **Como** sistema,
  **quiero** preparar una versión limpia, orientada y de alta fidelidad del
  documento cuando ya es comprobante
  **para** que la extracción final no pierda texto pequeño, sellos, QR o detalle.
- **Criterios de aceptación (Gherkin):**

```gherkin
Dado un documento confirmado como comprobante
Cuando se prepara la vista de extracción
Entonces NO se reutiliza la vista rápida degradada
Y se entrega una versión con resolución suficiente, orientación corregida y limpieza leve
Y la salida conserva tabla, sello, firma, QR y texto pequeño cuando existen

Regla: complejidad
  Dado un comprobante complejo (tabla + sello + firma)
  Cuando se extrae
  Entonces se considera un enfoque por zonas o por partes si el envío único fuera ambiguo
```

## 4. Bitácora de seguimiento

| Fecha | Acción / hito | Responsable | Estado |
|---|---|---|---|
|  | | | |

## 5. Referencias cruzadas

- Historias fuente: [`02-epicas-historias-usuario.md`](../02-epicas-historias-usuario.md)
- Objetivo y alcance: [`01-vision-alcance.md`](../01-vision-alcance.md) (OBJ-2)
- Fases/tareas/DoR-DoD: [`05-plan-ejecucion.md`](../05-plan-ejecucion.md) (F2: T-201..T-204)
- Calidad/golden set: [`06-estrategia-calidad.md`](../06-estrategia-calidad.md)
- Dependencias: E-DOC (representación de entrada), E-LIB (configuración y `OllamaClient` T-005), E-EXT (la vista fiel alimenta la extracción)

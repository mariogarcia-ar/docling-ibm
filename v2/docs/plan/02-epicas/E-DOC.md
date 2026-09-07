# Épica E-DOC — Procesamiento adaptativo docling (Seguimiento)

> Documento de seguimiento generado a partir de
> [`02-epicas-historias-usuario.md`](../02-epicas-historias-usuario.md) y
> [`05-plan-ejecucion.md`](../05-plan-ejecucion.md).

## 1. Ficha de la épica

| Campo | Valor |
|---|---|
| **Código** | E-DOC |
| **Objetivo(s) que cubre** | OBJ-1 — Refactorizar docling (procesamiento multi-tipo adaptativo) en un módulo de la librería |
| **Fuente de ideas** | `v2/docs/ideas/docling.md` |
| **Módulo de librería** | `processing/` |
| **Fase(s) del plan** | F0 (T-006 adaptador Docling) y F1 (T-101..T-105 + orquestación) |
| **Prioridad MoSCoW** | Must (MVP) |
| **Responsable ciclo** | team analysis (BA/SA/PM) → team implementation |
| **Estado épica** | 🟡 En implementación (F1: T-101..T-105/ORQ hechos, incl. orquestación + `docling_raw`; falta T-105 paridad) |
| **DoR cumplido** | [x] |
| **Fecha inicio** | 2026-09-06 |
| **Fecha fin** |  |

## 2. Definition of Done de la épica (criterios de aceptación a nivel épica)

- [x] El detector de tipo de entrada decide la ruta correcta (PDF texto, PDF escaneado, imagen, DOCX/XLSX/PPTX/TXT/CSV/LOG/HTML/Markdown, no soportado) y los formatos no soportados se rechazan/reencolan sin forzar OCR.
- [x] La imagen se clasifica (foto, escaneo plano, screenshot, manuscrito/sello/firma), se preprocesa y se endereza la perspectiva cuando hace falta.
- [x] La orientación se detecta y corrige, ordenando el texto según la lectura real (center_y o center_x).
- [x] La elección de motor funciona: OCR tradicional para impreso estándar y VLM para manuscrito/firma/sello.
- [x] La salida es Markdown ordenado por posición visual real y conserva las tablas detectadas como tablas Markdown.
- [x] La orquestación `procesar_documento()` + `api.process()` está implementada y expone el crudo de Docling vía `docling_raw` (decisión subplan F1 §2.5).
- [ ] Paridad verificable con v1 sobre el golden set: procesar los formatos de las ideas produce Markdown ordenado equivalente o superior a `ocr_documents.py`/`run.py`/`run_raw.py` (DoD de F1 en `05-plan-ejecucion.md`). *(requiere T-105 integración)*
- [ ] Documentación/contratos actualizados (README/ADR si cambia una decisión).

> **Nota 2026-09-06**: la ruta texto nativo se mantiene **solo con Docling**;
> `pdftotext --layout` queda como alternativa futura para layout de columnas
> (decisión subplan F1 §2.6; caso `9dfc597f`).

## 3. Historias de usuario y seguimiento

### E-DOC-1 · Detección del tipo de entrada y ruta de procesamiento
- **Estado**: [x] Pendiente · [ ] En desarrollo · [ ] En QA · [x] Hecho (T-101 + routing + orquestación)
- **Responsable**: team analysis / team implementation
- **Como** sistema de procesamiento,
  **quiero** detectar el tipo de documento de entrada (PDF texto, PDF escaneado,
  imagen, DOCX, XLSX, PPTX, TXT, CSV, LOG, HTML/Markdown o no soportado)
  **para** elegir la ruta de extracción correcta (texto nativo, imagen+OCR, tabla,
  rechazo/reencolado).
- **Criterios de aceptación (Gherkin):**

```gherkin
Regla: PDF con texto extraíble
  Dado un PDF con capa de texto válida
  Cuando se procesa el documento
  Entonces se extrae el texto directamente SIN OCR visual
  Y la salida es una representación estructurada (Markdown)

Regla: PDF escaneado
  Dado un PDF sin capa de texto (escaneado)
  Cuando se procesa el documento
  Entonces se convierte a imagen
  Y se procesa por la ruta de imagen (OCR/VLM)

Regla: formatos Office y planos
  Dado un DOCX/XLSX/PPTX/TXT/CSV/LOG/HTML/Markdown
  Cuando se procesa el documento
  Entonces se aplica la ruta específica del formato
  Y se normaliza a la representación estructurada común

Regla: formato no soportado
  Dado un archivo de formato desconocido o corrupto
  Cuando se procesa el documento
  Entonces se marca como rechazado o reencolado
  Y NO se intenta forzar un OCR
```

### E-DOC-2 · Procesamiento adaptativo de imágenes
- **Estado**: [x] Pendiente · [ ] En desarrollo · [ ] En QA · [x] Hecho (T-102..T-104 + integrado en orquestación)
- **Responsable**: team analysis / team implementation
- **Como** sistema,
  **quiero** clasificar la imagen (foto de documento, escaneo plano, captura
  digital/screenshot, manuscrito/sello/firma), preprocesar si hace falta,
  enderezar perspectiva, detectar orientación y elegir motor OCR/VLM
  **para** maximizar la calidad de lectura de cada tipo de imagen.
- **Criterios de aceptación (Gherkin):**

```gherkin
Regla: clasificación de imagen
  Dado una imagen con perspectiva notable
  Cuando se procesa la imagen
  Entonces se clasifica como fotografía de documento
  Y se endereza la perspectiva antes de OCR

  Dado una imagen de baja resolución o ruido
  Cuando se procesa la imagen
  Entonces se aplica preprocesamiento (mejora de calidad / binarización) antes de OCR

Regla: orientación
  Dado una imagen rotada (vertical predominante)
  Cuando se detecta la orientación
  Entonces se rota a la orientación correcta
  Y el texto resultante se ordena según la orientación (center_y o center_x)

Regla: elección de motor
  Dado una imagen con texto impreso legible y estructura estándar
  Cuando se elige el motor
  Entonces se usa OCR tradicional

  Dado una imagen con texto manuscrito, firma o sello
  Cuando se elige el motor
  Entonces se prioriza un modelo visual (VLM)
```

### E-DOC-3 · Salida ordenada y estructura de lectura
- **Estado**: [x] Pendiente · [ ] En desarrollo · [ ] En QA · [x] Hecho (T-104 exportador por posición + `docling_raw` para crudo Docling)
- **Responsable**: team analysis / team implementation
- **Como** consumidor de la salida (flujo LLM),
  **quiero** recibir texto ordenado por posición visual real
  **para** que el razonamiento sobre el texto no pierda el orden del documento.
- **Criterios de aceptación (Gherkin):**

```gherkin
Dado un documento horizontal
Cuando se exporta la representación
Entonces los boxes se agrupan por center_y y se ordenan por posición en la línea

Dado un documento vertical
Cuando se exporta la representación
Entonces los boxes se agrupan por center_x y se ordenan por posición en la columna

Dado un documento con tablas detectadas
Cuando se exporta la representación
Entonces las tablas se conservan como tablas Markdown
```

## 4. Bitácora de seguimiento

| Fecha | Acción / hito | Responsable | Estado |
|---|---|---|---|
| 2026-09-06 | F0/T-006: adaptador Docling encapsulado (`DoclingConverter` + `ProcessedDocument`) | team implementation | Hecho |
| 2026-09-06 | F1/T-101 y T-102: detector de tipo + clasificador de imagen con gate | team implementation | Hecho |
| 2026-09-06 | F1/T-103 y T-104: orientación/preprocesamiento + motor/exportador por posición | team implementation | Hecho |
| 2026-09-06 | F1/`routing.py`: enrutado de PDF por página para la orquestación | team implementation | Hecho |
| 2026-09-06 | F1/T-105/ORQ: orquestación `procesar_documento()` + `api.process()` implementadas, con flag `docling_raw` (crudo Docling) y `--docling-raw` en el script de prueba. | team analysis → team implementation | Hecho |
| 2026-09-06 | Decisión A1: ruta texto nativo solo con Docling; `pdftotext --layout` queda como alternativa futura (layout de columnas, caso `9dfc597f`). | team analysis | Cerrada |
| 2026-09-06 | Pendiente: T-105 (tests integración paridad) y cierre de docs. | team implementation | Pendiente |

## 5. Referencias cruzadas

- Historias fuente: [`02-epicas-historias-usuario.md`](../02-epicas-historias-usuario.md)
- Objetivo y alcance: [`01-vision-alcance.md`](../01-vision-alcance.md) (OBJ-1)
- Fases/tareas/DoR-DoD: [`05-plan-ejecucion.md`](../05-plan-ejecucion.md) (F0: T-006; F1: T-101..T-105)
- Calidad/golden set: [`06-estrategia-calidad.md`](../06-estrategia-calidad.md)
- Dependencias: E-LIB (esqueleto/módulos), E-QWE (consume el Markdown/representación de E-DOC)

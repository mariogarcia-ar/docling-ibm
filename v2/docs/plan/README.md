# Plan de Trabajo — Refactor v2 (Librería robusta + Cliente)

> **Documento**: Plan de trabajo integral con roles **BA · SA · PM**
> **Proyecto**: `ibm-docling` — v2 (refactor de docling, qween, clasificación, extracción y conclusión)
> **Fuentes**: `v2/docs/readme.md`, `v2/docs/ideas/*.md`, estado actual en `v1/` y `prompts/`
> **Fecha**: 2026-09-06 · **Estado**: Borrador v0.1 para revisión

---

## Resumen ejecutivo

v2 refactoriza cinco capacidades que hoy conviven como scripts en `v1/`
(OCR con Docling, extracción `kvi/kvg`, clasificación `01→02→03`, detección de
tipo de comprobante `11.1` y validación) y las reorganiza como **una librería
robusta más un cliente que la invoca**. La librería implementa un pipeline
determinístico + asistido por IA con evidencia trazable:

1. **Docling (procesamiento)** → ingestión multi-formato, gate de
   procesabilidad, clasificación de imagen, preprocesamiento, orientación y
   elección de motor OCR/VLM.
2. **Qween (doble paso con distinta calidad)** → una vista barata decide si el
   documento es comprobante; si lo es, una vista fiel y de alta calidad
   alimenta la extracción real.
3. **Clasificación** → tipo/letra de comprobante y clasificación contable
   (centro de costo → macro categoría → concepto/código).
4. **Extracción** → dos flujos en paralelo (VLM sobre imagen y LLM sobre
   OCR/Markdown) que devuelven evidencia con el mismo contrato.
5. **Conclusión** → reglas de programación determinísticas (raw + cruzadas),
   búsqueda puntual de evidencia adicional (ej. padrón ARCA), escalado a un
   agente de IA solo cuando el código no concluye, y HITL como autoridad final
   con muestreo de auditoría.

La **certeza** de una conclusión se deriva de *qué etapa resolvió el caso*
(programa = alta, agente IA = baja → revisión humana), no de la confianza que
reporta un modelo. Todo queda registrado con trazabilidad completa para poder
justificar una clasificación ante una auditoría.

---

## Índice del plan

| # | Documento | Rol | Contenido |
|---|-----------|-----|-----------|
| 00 | [`00-glosario.md`](00-glosario.md) | BA/SA | Glosario del dominio y modelo de evidencia |
| 01 | [`01-vision-alcance.md`](01-vision-alcance.md) | BA | Visión, problema, objetivos, entregables, alcance (dentro/fuera, MVP vs. futuro) |
| 02 | [`02-epicas-historias-usuario.md`](02-epicas-historias-usuario.md) · carpeta [`02-epicas/`](02-epicas/) ([E-DOC](02-epicas/E-DOC.md) · [E-QWE](02-epicas/E-QWE.md) · [E-CLAS](02-epicas/E-CLAS.md) · [E-EXT](02-epicas/E-EXT.md) · [E-CONC](02-epicas/E-CONC.md) · [E-LIB](02-epicas/E-LIB.md) · [E-CLI](02-epicas/E-CLI.md)) | BA | Épicas y user stories INVEST con criterios Gherkin + archivo de seguimiento (tracking) por épica |
| 03 | [`03-arquitectura-solucion.md`](03-arquitectura-solucion.md) | SA | Arquitectura (C4), componentes de la librería, cliente, modelos de datos, secuencias |
| 04 | [`04-decisiones-abiertas-adr.md`](04-decisiones-abiertas-adr.md) | SA | Decisiones técnicas abiertas y ADR preliminares |
| 05 | [`05-plan-ejecucion.md`](05-plan-ejecucion.md) · carpeta [`05-plan/`](05-plan/) ([F0](05-plan/F0.md) · [F1](05-plan/F1.md) · [F2](05-plan/F2.md) · [F3](05-plan/F3.md) · [F4](05-plan/F4.md) · [F5](05-plan/F5.md) · [F6](05-plan/F6.md)) | PM | WBS/fases, MoSCoW, estimaciones, riesgos, DoR/DoD + archivo de seguimiento (tracking) por fase |
| 06 | [`06-estrategia-calidad.md`](06-estrategia-calidad.md) | BA/SA/PM→DEV/QA | Estrategia de pruebas, golden set, métricas y criterios de salida por fase |

---

## Cómo leer este plan

- **BA** (qué y por qué): `00`, `01`, `02`.
- **SA** (cómo): `03`, `04`.
- **PM** (cuándo y con qué orden): `05`.
- **Calidad** (cómo se valida): `06`.

Los diagramas están en Mermaid. Cada documento es autocontenido y referencia a
los demás cuando hace falta.

---

## Estado actual (as-is) que se refactoriza — resumen

Referencia rápida del código y prompts existentes (detalle en `01-vision-alcance.md`):

| Capacidad | Scripts v1 / prompts | Problema que motiva el refactor |
|-----------|----------------------|---------------------------------|
| OCR (docling) | `ocr_documents.py`, `lib/converter.py`, `lib/orientation.py`, `lib/processor.py`, `run.py`, `run_raw.py` | Acoplado a Docling, sin gate de procesabilidad, sin clasificación de imagen ni elección de motor, orientación sólo auto/h/v |
| Extracción | `document_extraction.py`, `extraction_pipeline.py`, prompts `10`, `11`, `kvi`, `kvg`, `11.1` | Prompts "single-shot" por script, sin contrato de evidencia, sin reglas programáticas, modalidad vlm/llm manual |
| Clasificación | `classification_pipeline.py`, prompts `01`, `02`, `03` | Cadena secuencial rígida, sin candidatos ni confianza estructurada entre pasos |
| Detección tipo | `prompts/facturacion/11.1-*.yaml`, `prompts/wip/deteccion_tipo_factura.yaml` (R1-R7) | Reglas embebidas en prompts; hay WIP de reglas determinísticas sin consolidar |
| Validación/conclusión | `wip/consultar_arca.py`, `ask.py`, `my_prompt.md` (idea de flujo) | No hay pipeline de conclusión ni HITL; lógica dispersa en WIP |

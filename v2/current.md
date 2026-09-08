# v2 — Estado actual y qué se puede probar (fase F1)

> **Documento**: estado vivo de la versión 2 de `ibm-docling`.
> **Fecha**: 2026-09-07 · **Rama**: `v2` (ruta texto nativo con `pdftotext --layout` para PDF apto)
> **Fuentes**: `v2/README.md`, `v2/docs/plan/05-plan/F0.md`, `v2/docs/plan/05-plan/F1.md` y `F1-subplan.md`, `v2/src/voucherflow/`

---

## 1. Fase actual

| Campo | Valor |
|---|---|
| **Fase en curso** | **F1 — Procesamiento (refactor docling)** (T-101..T-105/ORQ + T-105) |
| **Estado** | 🟢 **F0 completada** (77 tests al cierre). F1 **en implementación**: T-101 ✅, T-102 ✅, T-103 ✅, T-104 ✅, enrutado PDF por página ✅ (`routing.py`), **orquestación `procesar_documento()` + `api.process()` ✅** (T-105/ORQ, con flag `docling_raw` para crudo Docling) y **ruta PDF apto con `pdftotext --layout` ✅** (poppler, con fallback Docling; A1 revertida); resta T-105 (integración paridad) y docs de cierre |
| **F0** | ✅ Fundación completada (schemas, esqueleto, golden set, adaptadores) |
| **F2–F6** | 🔴 Backlog (validación, clasificación, extracción, conclusión, cliente) |
| **Paquete** | `voucherflow` v`0.1.0` (layout `src/`, ADR-007) |
| **Contrato** | `SCHEMA_VERSION = 1.0.0` (congelado, ver criterio de cambio en `schemas/evidence.py`) |
| **Suite de tests** | ✅ **187 tests en verde** (`python -m pytest tests -q`) en env `py313_env` |

**Resumen**: F0 dejó la **fundación de la librería**: contratos de evidencia
congelados, configuración centralizada, adaptadores `OllamaClient`/
`DoclingConverter`, base del motor de reglas, el **golden set inicial** y el
esqueleto de las 5 capacidades (F1–F5). Sobre esa base, **F1** (en curso)
refactoriza Docling en `voucherflow/processing/`: están implementados el
**detector de tipo de entrada** (T-101), el **clasificador de imagen + gate de
procesabilidad** (T-102), la **orientación/preprocesamiento** (T-103), el
**motor OCR/VLM + exportador ordenado por posición** (T-104), el **enrutado de
PDF por página** (`routing.py`), la **orquestación `procesar_documento()` +
`api.process()`** (T-105/ORQ, con flag `docling_raw` que expone el crudo de
Docling — equiv. `v1/run_raw.py`) y la **extracción de PDF apto con
`pdftotext --layout`** (`processing/pdftotext.py`; recupera columnas que
Docling aplana, con fallback a Docling si no hay poppler — decisión 2026-09-07
que revierte A1), todo heurístico liviano, **sin dependencias Python nuevas**
(poppler es un utilitario de sistema) y cubierto por tests. Ya **hay pipeline
funcional de extremo a extremo** de procesamiento (`api.process`); resta la
paridad de integración T-105 y los docs de cierre de F1.

---

## 2. Qué hay hasta el momento (F0)

### 2.1 Implementado y probado (F0)

| Módulo | Archivo | Qué ofrece / se puede probar |
|---|---|---|
| **Contrato de evidencia** | `src/voucherflow/schemas/evidence.py` | Modelos pydantic congelados: `EvidenceField`, `SourceEvidence`, `FieldResolution`, `CampoCombinado`, `Decision`, `CombinedEvidence` + enums `Fuente`/`Certeza`/`Origen`/`EstadoResultado`/`TipoComprobante` y helper `nueva_meta`. |
| **Contrato de resultado/trazabilidad** | `src/voucherflow/schemas/result.py` | `VoucherResult`, `CaseRecord`, `RegistroEtapa`, `CampoExtraido`, `ClasificacionContable`, `HitlDecision` (base de la trazabilidad ADR-005). |
| **Configuración centralizada** | `src/voucherflow/settings/config.py` | `Settings` con defaults → YAML → env `VOUCHERFLOW_*`. |
| **Cliente Ollama robusto** | `src/voucherflow/models/ollama.py` | `OllamaClient` con retry/backoff ante 429/502/503/504, timeout y diagnóstico (E-LIB-3). Probado con mocks HTTP. |
| **Adaptador Docling** | `src/voucherflow/models/docling.py` | `DoclingConverter` + contrato `ProcessedDocument` (markdown + `Box` + metadatos) y `EXTENSIONES_SOPORTADAS`. |
| **Base del motor de reglas** | `src/voucherflow/rules/registry.py` | `Rule` declarativa + `Registry` (evaluación determinista ordenada por prioridad, trazable por `id`). Las reglas de negocio R1-R7 se migran en F3. |
| **Helper de sidecar** | `src/voucherflow/trace/recorder.py` | `sidecar_para()` (nomenclatura canónica `<doc>.case.json`); el `CaseRecorder` se implementa en F5. |
| **Golden set inicial** | `v2/tests/golden/` | `casos.csv` con **9 casos** reales (jpg/pdf/jpeg/png) referenciados en `v2/tests/fixtures/`, splits `train/eval`, README con criterio (T-004). |

### 2.2 Esqueletos (contratos de F0, lógica en F1–F5)

Estos módulos existen con su **firma pública y contratos** pero **no tienen
lógica de negocio**: sus funciones lanzan `NotImplementedError` hasta su fase.

| Capacidad (fase) | Módulo | Contratos ya definidos |
|---|---|---|
| Procesamiento (F1) | `processing/type_detector.py` | `TipoEntrada` (tipo, `ruta_ocr`) |
| Validación (F2) | `validation/qween.py` | `VeredictoGate` (comprobante/no/indeterminado), `ValidationResult` |
| Clasificación (F3) | `classification/tipo_comprobante.py` | `TipoComprobanteResult`, `ClasificacionContableResult` |
| Extracción (F4) | `extraction/flows.py` | `flujo_vlm()`, `flujo_llm()`, `combinar_evidencia()` |
| Conclusión (F5) | `conclusion/engine.py` | `concluir()`, `escalar_a_agente()`, `encolar_hitl()` |
| Conclusión (F5) | `models/arca.py` | `ArcaClient`, `ArcaResultado` (opcional, ADR-003) |
| Trazabilidad (F5) | `trace/recorder.py` | `CaseRecorder` |
| Fachada / orquestador | `api.py`, `orchestrator.py` | Esqueleto de la API pública (`VoucherResult`) y `PipelineResult` |

---

## 2.3 Avance de F1 (en curso)

| Tarea | Módulo | Qué ofrece / se puede probar | Tests |
|---|---|---|---|
| **T-101** (✅) | `processing/type_detector.py` | `detectar() -> TipoEntrada` (pdf_texto/pdf_escaneado/imagen/office/texto/no_soportado) con distinción pdf_texto/pdf_escaneado por capa real de texto (PyMuPDF). | `test_processing_type_detector.py` (29) |
| **T-102** (✅) | `processing/image_classifier.py` | `ClaseImagen` (foto/escaneo_plano/screenshot/manuscrito), `clasificar()`, gate `verificar_procesabilidad() -> VeredictoGate` y hook `sospechar_manuscrito()`. Lee dimensiones JPEG/PNG/BMP/TIFF con stdlib (sin Pillow/OpenCV). | `test_processing_image_classifier.py` (27) |
| **T-103** (✅) | `processing/preprocessing.py` + `orientation.py` | Orientación por boxes (horizontal/vertical dominante) + preprocesamiento heurístico (`QualityReport`/`evaluar_calidad`), sin CV. | `test_processing_orientation.py` (19) |
| **T-104** (✅) | `processing/ocr.py` + `markdown_exporter.py` | Motor OCR/VLM (`elegir_motor`: ocr/vlm/auto) + hook `transcribir_vlm` (sin llamar a Ollama en F1) y exportador ordenado por posición (portado de `v1/lib/orientation.py`, paridad byte-compatible; tablas Markdown como ítem único). | `test_processing_exportador_motor.py` (24) |
| **PDF apto** (✅) | `processing/pdftotext.py` | Extracción de PDF apto con `pdftotext --layout` (poppler) preferido + fallback a Docling directo (A1 revertida 2026-09-07). La orquestación usa motor `pdftotext` (layout de columnas, caso `9dfc597f`); `office`/`texto` y `docling_raw` siguen con Docling. | `test_processing_pdftotext.py` (7) |
| **Apoyo orq.** (✅) | `processing/routing.py` | Enrutado de PDF por página (apta_layout/escaneada/corrupta/vacía) y veredicto por PDF (apto/requiere_ocr/parcial) para la orquestación. | `test_processing_routing.py` (9) |
| **Inspección** | `scripts/inspeccionar_t102.py` | Aplica T-102 sobre fixtures (o carpeta CLI) y muestra resumen/detalle por clase y gate. | — |

> Detalle: sobre los 38 fixtures de imagen, T-102 clasifica 23 `escaneo_plano`,
> 13 `screenshot` y 2 `foto`, con **0 rechazos** en falso del gate.

> **Ruta texto nativo (2026-09-07)**: PDF apto se extrae con `pdftotext
> --layout` preferido (recupera columnas que Docling aplana, caso `9dfc597f`)
> con fallback a Docling directo si no hay poppler (decisión subplan F1 §2.6;
> revierte A1 de 2026-09-06).

> **Pendiente de F1**: T-105 (tests de integración de paridad contra v1,
> `@pytest.mark.integration`) y docs de cierre.

---

## 3. Qué se puede probar en esta fase (F1)

### 3.1 Rápido — instalación e import

```bash
# Desde la raíz del repo (entorno py313_env)
python -m pip install -e v2/
python -c "import voucherflow; print(voucherflow.__version__, voucherflow.SCHEMA_VERSION)"
# → 0.1.0 1.0.0
```

### 3.2 Suite de tests (187 en verde)

```bash
cd v2
python -m pytest tests -q
```

Cobertura de la suite por archivo (F0 + F1):

| Archivo de test | Qué valida |
|---|---|
| `test_schemas_evidence.py` | Contrato de evidencia: construcción/validación de `SourceEvidence`, `CombinedEvidence`, enums, helper `nueva_meta`, criterio de cambio. |
| `test_schemas_result.py` | `VoucherResult`, `CaseRecord`, `RegistroEtapa`, `HitlDecision` y serialización. |
| `test_models_ollama.py` | `OllamaClient` con **mock HTTP**: payload de chat, reintentos/backoff, timeouts, mensajes de error. |
| `test_models_docling.py` | Construcción del `DoclingConverter` y contrato `ProcessedDocument` (sin conversión real de archivos, marcada integración). |
| `test_settings_config.py` | `Settings`: defaults, override por env `VOUCHERFLOW_*`, precedencia de fuentes. |
| `test_golden_y_esqueleto.py` | Golden set (integridad de `casos.csv` vs. archivos en `fixtures/`, splits sin cruce) y esqueletos (firmas presentes, lanzan `NotImplementedError`). |
| `test_fixtures.py` | Integridad/consistencia de los fixtures del golden set. |
| `test_processing_type_detector.py` | **F1/T-101**: `detectar()` por extensión y por capa de texto pdf_texto/pdf_escaneado (PDFs sintéticos). |
| `test_processing_image_classifier.py` | **F1/T-102**: clasificador (foto/escaneo/screenshot por ratio/EXIF/RGBA), gate de procesabilidad y hook de manuscrito (PNG/JPEG sintéticos con stdlib). |
| `test_processing_orientation.py` | **F1/T-103**: orientación por boxes (horizontal/vertical), `requiere_rotacion` y preprocesamiento heurístico (`QualityReport`). |
| `test_processing_exportador_motor.py` | **F1/T-104**: exportador ordenado por posición (horizontal/vertical/tablas) + elección de motor ocr/vlm/auto + hook `transcribir_vlm` sin Ollama. |
| `test_processing_routing.py` | **Apoyo orquestación**: clasificación de página (apta/escaneada/corrupta/vacía) y veredicto por PDF (apto/requiere_ocr/parcial). |

### 3.3 Probar el contrato de evidencia a mano (ejemplos)

```python
from voucherflow.schemas.evidence import (
    EvidenceField, SourceEvidence, CombinedEvidence, nueva_meta, Fuente,
)
from voucherflow.schemas.result import CaseRecord

meta = nueva_meta("doc-1", modelo="qwen2.5vl")
campo = EvidenceField(valor="FC A 0001-00000001", certeza="alta")
src = SourceEvidence(documento_id="doc-1", fuente=Fuente.vlm, campos={"nro_comprobante": campo}, meta=meta)
combinada = CombinedEvidence(documento_id="doc-1", campos={})
```

> ⚠️ La validación **rechaza** valores mal formados con errores de contrato
> claros (diseño E-LIB-2). Probá enviar un campo sin `valor`, una `certeza`
> inválida o un `CampoCombinado` sin fuente válida y verificá el mensaje.

### 3.4 Probar el motor de reglas base

```python
from voucherflow.rules.registry import Rule, Registry

reglas = Registry()
reglas.registrar(Rule(id="R1", prioridad=1, condicion=lambda ctx: ctx["monto"] > 0, resultado="A", detalle="demo"))
reglas.registrar(Rule(id="R2", prioridad=2, condicion=lambda ctx: "IVA" in ctx["texto"], resultado="B", detalle="demo"))
reglas.ids_disparados({"monto": 100, "texto": "tiene IVA"})  # ["R1", "R2"]
```

### 3.5 Verificar el golden set y sus fixtures

| Ítem | Dónde |
|---|---|
| Índice de casos (9) | `v2/tests/golden/casos.csv` |
| Splits train/eval | `v2/tests/golden/splits/` |
| Copias versionadas | `v2/tests/fixtures/{golden,chicos,grandes,otros}/` |
| Criterio de etiquetado | `v2/tests/golden/README.md` |

> ⚠️ **Importante**: las **etiquetas de negocio** (`letra`, `condicion_fiscal`,
> `veredicto`, `calidad`) están en `pendiente`. Solo el `tipo_entrada` está
> etiquetado (por extensión). El etiquetado completo requiere el OCR de F1/v1 o
> el criterio de contador (plan §3.4) — **pendiente de F0**.

### 3.6 Lo que NO se puede probar todavía

- ❌ Pipeline de extremo a extremo de un documento (orquestación `procesar_documento()` + `api.process()`; llega en **F1**).
- ❌ Paridad de integración T-105 contra v1 sobre fixtures reales (tests `@pytest.mark.integration`; llega en **F1**).
- ❌ Gate "¿es comprobante?" estilo qween (llega en **F2**, T-201..T-204).
- ❌ Clasificar tipo/letra y cadena contable (llega en **F3**, T-301..T-305).
- ❌ Extracción VLM/LLM con evidencia combinada (llega en **F4**, T-401..T-405).
- ❌ Conclusión reglas→agente→HITL + trazabilidad persistida (llega en **F5**).
- ❌ CLI/batch (`voucherflow …`) y paridad v1 sobre `files/` (llega en **F6**).

Las funciones de esqueleto de fases futuras (`validate`, `classify`,
`extract`, `run`, `validar_comprobante`, `clasificar_*`, `flujo_vlm/llm`,
`combinar_evidencia`, `concluir`, `escalar_a_agente`, `encolar_hitl`,
`CaseRecorder.registrar`) y la orquestación de F1 (`api.process`,
`procesar_documento`) lanzan `NotImplementedError` a propósito.

---

## 4. Pendientes de F0 (para cerrar la fase)

- [ ] Curar **etiquetas de negocio** del golden set (`letra`, `condición
      fiscal`, `veredicto`) con el contador (T-004 / plan §3.4 y §4).
- [ ] Reportar **métricas de la fase** y dejar sin deuda técnica bloqueante
      para F1 (DoD transversal de calidad — `docs/plan/06-estrategia-calidad.md`).
- [x] F0 en revisión en `docs/plan/05-plan/F0.md` y F1 arrancada
      (`docs/plan/05-plan/F1.md`, T-101..T-105 en curso).

---

## 5. Referencias

- Documentación de arquitectura/plan: `v2/docs/plan/` (00-glosario, 03-arquitectura, 04-ADRs, 05-plan, 06-calidad).
- README del paquete: `v2/README.md`.
- Seguimiento por fase: `v2/docs/plan/05-plan/F0.md` … `F6.md`.

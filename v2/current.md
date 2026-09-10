# v2 — Estado actual y qué se puede probar (fase F4)

> **Documento**: estado vivo de la versión 2 de `ibm-docling`.
> **Fecha**: 2026-09-10 · **Rama**: `v2`
> **Fuentes**: `v2/README.md`, `v2/docs/plan/05-plan/F0.md`..`F4.md` y subplanes, `v2/src/voucherflow/`

---

## 1. Fase actual

| Campo | Valor |
|---|---|
| **Fase en curso** | **F4 — Extracción** (T-401..T-405) |
| **Estado** | **T-401 ✅ y T-402 ✅ hechas**: los flujos VLM (vista fiel de F2) y LLM (OCR/Markdown de F1) corren **en paralelo** y devuelven `SourceEvidence` con el contrato de F0; prompt de evidencia versionado `extraccion-key-value@1`, intérprete que no inventa, pasada raw reutilizada de T-303, medición real de paralelismo y **normalización key-value** (CUIT cortado, fecha ISO, montos numéricos, `punto_venta`/`numero_comprobante` derivados) con el crudo siempre preservado. Restan T-403 (reglas raw de extracción), T-404 (combinación ADR-002) y T-405 (paridad con v1). |
| **F0** | ✅ Fundación completada (schemas, esqueleto, golden set, adaptadores) |
| **F1** | ✅ Implementada (T-101..T-105/ORQ, `api.process()`; paridad de integración en `@pytest.mark.integration`) |
| **F2** | ✅ DoD verificado (T-201..T-204; doble paso qween) |
| **F3** | ✅ DoD verificado (T-301..T-305; motor de reglas R1-R7, evidencia, reglas raw, cadena contable y paridad con v1) |
| **F4** | 🟡 En implementación (**T-401 y T-402 hechas**; T-403..T-405 pendientes) |
| **F5–F6** | 🔴 Backlog (conclusión + HITL; CLI/batch y paridad sobre `files/`) |
| **Paquete** | `voucherflow` v`0.1.0` (layout `src/`, ADR-007) |
| **Contrato** | `SCHEMA_VERSION = 1.0.0` (congelado, ver criterio de cambio en `schemas/evidence.py`) |
| **Suite de tests** | ✅ **827 tests en verde + 10 skipped** (`python -m pytest tests -q`) en env `py313_env` |

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
F1–F4 ya implementaron las capacidades de las filas marcadas; las de F5 siguen
pendientes.

| Capacidad (fase) | Módulo | Contratos ya definidos |
|---|---|---|
| Procesamiento (F1) | `processing/type_detector.py` | `TipoEntrada` (tipo, `ruta_ocr`) |
| Validación (F2) | `validation/qween.py` | `VeredictoGate` (comprobante/no/indeterminado), `ValidationResult` |
| Clasificación (F3) | `classification/tipo_comprobante.py` | `TipoComprobanteResult`, `ClasificacionContableResult` |
| Extracción (F4) | `extraction/flows.py` | `flujo_vlm()`, `flujo_llm()`, `extraer()` (**implementados en T-401**); `combinar_evidencia()` (esqueleto de T-404) |
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

## 2.4 Avance de F4 (en curso)

| Tarea | Módulo | Qué ofrece / se puede probar | Tests |
|---|---|---|---|
| **T-401** (✅) | `extraction/{flows,evidencia,prompt_extraccion}.py` | Los flujos **VLM** (vista fiel de F2) y **LLM** (OCR/Markdown de F1) corren **en paralelo** (`ThreadPoolExecutor`, una tarea por fuente) y devuelven cada uno `SourceEvidence` con el contrato de F0 (ADR-001). Prompt de evidencia versionado `extraccion-key-value@1` (reporta `valor` + `fragmento_sustento` por campo; no normaliza ni decide). El intérprete no inventa campos (los lista en `campos_ausentes`), tolera el JSON plano de v1 (`kvi`/`kvg`) y reutiliza la pasada raw de T-303. Una fuente caída no tumba a la otra; todas caídas lanzan `ErrorExtraccion`. Las dos evidencias se conservan **sin colapsar** (T-404). | `test_extraction_flujos.py` (75) |
| **T-402** (✅) | `extraction/key_value.py` | **Normalización key-value (E-EXT-3)** en código: CUIT a dígitos y guiones propios con corte ante caracteres extraños (`"20-1 Ing, Brutas: 201641"` → `"20-1"`), fechas `YYYY-MM-DD` solo si son completas y reales, montos numéricos sin separadores de miles (signo y constancia de ambigüedad), `punto_venta`/`numero_comprobante` derivados del número impreso, moneda `ARS`/`USD` sin default, texto colapsado, `descripcion` en minúsculas e ítems estructurados. **No inventa**: un dato ilegible conserva el crudo con aviso y el crudo de cada campo queda en `meta['valor_crudo']` (la pasada raw de T-303 sigue viendo el crudo). `normalizar=False` devuelve la lectura cruda de T-401. | `test_extraction_key_value.py` (97) |
| **T-403..T-405** (🔴) | `rules/raw.py`, `rules/precedencia.py`, `tests/golden/F4/` | Reglas raw por fuente afinadas para extracción, combinación por campo (ADR-002) y paridad con v1. | — |

> **Inspección de T-401**: `python scripts/F4/t401.py` corre **11/11** escenarios
> sintéticos (dos fuentes coincidiendo, discrepancia conservando ambas, campo sin
> sustento, valor fuera del vocabulario, CUIT no sostenido, JSON plano de v1,
> montos no evaluados por sostén, sin vista, JSON inválido aislado, fuente caída
> aislada, todas caídas) y **mide el paralelismo** (en paralelo ≈ el máximo de las
> dos llamadas, no la suma). Con `--origen <doc>` corre la extracción real
> (F1 + vista fiel de F2 + Ollama).
>
> **Decisión de alcance**: los campos de **formato volátil** (montos, fechas,
> `descripcion`) no se evalúan por sostén literal en T-401 (el OCR decide
> separadores y formato) y quedan listados en
> `detalle["modelos"][fuente]["sosten_no_evaluado"]` para que T-403 los cubra —
> no se inventa un veredicto favorable. **T-402 ya les dio forma canónica.**
>
> **Inspección de T-402**: `python scripts/F4/t402.py` corre **19/19** casos de
> regla (el CUIT pegado al campo siguiente, nueve formas de fecha, montos con
> separadores/signos/ambigüedad, `PPPPP-NNNNNNNN`, moneda, texto, ítems) y
> **17/17** escenarios de la regla dura de E-EXT-3 (*no inventar*), más **5/5**
> fronteras de la tarea (el crudo sobrevive, `normalizar=False`, la pasada raw
> sigue viendo el crudo, `combinar_evidencia` sigue siendo T-404). Con
> `--reglas --detalle` imprime el catálogo de reglas caso por caso.
>
> **Decisión de alcance (T-402)**: **"no normalizable" no es "ausente"**. Un
> monto escrito con palabras o una fecha con año de dos dígitos son lecturas
> reales del documento: se **conserva el crudo con un aviso** (que llega a
> `SourceEvidence.debilidades` cuando es una limitación real) en lugar de
> descartar el dato. El **0** es un importe legítimo, no una ausencia.

---

## 3. Qué se puede probar en esta fase (F1)

### 3.1 Rápido — instalación e import

```bash
# Desde la raíz del repo (entorno py313_env)
python -m pip install -e v2/
python -c "import voucherflow; print(voucherflow.__version__, voucherflow.SCHEMA_VERSION)"
# → 0.1.0 1.0.0
```

### 3.2 Suite de tests (827 en verde + 10 skipped)

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
| `test_rules_contexto.py` · `test_rules_tipo_comprobante.py` · `test_rules_raw.py` | **F3/T-301 y T-303**: contexto tipado, una clase por regla R1..R7 + combinaciones, y las cuatro reglas raw por fuente (`RAW_CAMPO`/`RAW_VOCABULARIO`/`RAW_SUSTENTO`/`RAW_CONTRADICCION`). |
| `test_classification_prompt_tipo.py` · `test_classification_contable.py` · `test_classification_paridad.py` | **F3/T-302, T-304 y T-305**: prompt de evidencia `tipo-comprobante@1` + lector inyectable; cadena contable 01→02→03 con checkpoints y contratos por paso; paridad con v1 (fidelidad de prompts portados, subconjunto del golden, regresión de R5). |
| `test_extraction_flujos.py` | **F4/T-401**: prompt de evidencia `extraccion-key-value@1`, `messages` por fuente, intérprete (sin normalizar ni inventar; tolera el JSON plano de v1), contrato `SourceEvidence`, pasada raw reutilizada de T-303 y **paralelismo real** de los dos flujos (incluye fallos por fuente). |
| `test_extraction_key_value.py` | **F4/T-402**: cada regla de normalización con los casos de v1 (CUIT cortado, nueve formas de fecha, montos con separadores/signos/ambigüedad, `PPPPP-NNNNNNNN` + derivados, moneda sin default, texto, `descripcion`, ítems), la regla dura de **no inventar** (crudo conservado con aviso), el informe de la corrida (reglas, normalizados/no normalizados, derivados, inmutabilidad) y la integración con el flujo y el contrato de F0. |

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

- ✅ Pipeline de extremo a extremo de un documento (orquestación `procesar_documento()` + `api.process()`; **hecho en F1**).
- 🟡 Paridad de integración T-105 contra v1 sobre fixtures reales (tests `@pytest.mark.integration`; **F1, pendiente de corrida real**).
- ✅ Gate "¿es comprobante?" estilo qween (**hecho en F2**, T-201..T-204).
- ✅ Clasificar tipo/letra y cadena contable (**F3** completa: T-301..T-305 — motor de reglas R1-R7, prompt de evidencia, reglas raw, cadena contable 01→02→03 y paridad verificada con v1: **8/8** en la cadena y **5/5** de exactitud de letra vs. **2/5** de v1).
- 🟡 **Extracción VLM/LLM con contrato de evidencia (T-401 ✅) y normalización key-value (T-402 ✅)**: `python scripts/F4/t401.py` corre **11/11** escenarios sin Ollama (prompt versionado, evidencia por fuente, paralelismo medido) y `python scripts/F4/t402.py` corre **19/19** casos de regla + **17/17** escenarios + **5/5** fronteras; con `--origen` los dos corren la extracción real (F1 + vista fiel de F2 + Ollama). Faltan las reglas raw por fuente (T-403), la combinación por campo (T-404) y la paridad con v1 (T-405).
- ❌ Conclusión reglas→agente→HITL + trazabilidad persistida (llega en **F5**).
- ❌ CLI/batch (`voucherflow …`) y paridad v1 sobre `files/` (llega en **F6**).

### 3.7 Comprobación rápida de T-301 (F3)

```python
from voucherflow.classification.tipo_comprobante import clasificar_tipo_comprobante
from voucherflow.rules.contexto import ContextoTipoComprobante

ctx = ContextoTipoComprobante(
    emisor_condicion_fiscal="Responsable Inscripto",
    receptor_condicion_fiscal="Responsable Inscripto",
    letra_recuadro_vlm="B",
)
res = clasificar_tipo_comprobante(ctx)
res.letra              # 'B' (default preferencia_letra="documento")
res.reglas_aplicadas   # ['R2A', 'R4', 'R7']
res.alertas            # [{'regla': 'R7', ...}] comprobante inválido para crédito fiscal
```

```bash
python scripts/F3/t301.py            # 14 escenarios R1..R7 contra la expectativa
python -m pytest tests/test_rules_tipo_comprobante.py -q
```

### 3.8 Comprobación rápida de T-401/T-402 (F4)

```bash
python scripts/F4/t401.py                      # 11 escenarios + paralelismo medido
python scripts/F4/t401.py --prompt             # el prompt de evidencia versionado
python scripts/F4/t402.py                      # 19 casos de regla + 17 escenarios + 5 fronteras
python scripts/F4/t402.py --reglas --detalle   # el catálogo de reglas, caso por caso
python -m pytest tests/test_extraction_flujos.py tests/test_extraction_key_value.py -q
```

```python
from voucherflow.extraction import extraer
from voucherflow.models.ollama import OllamaClient
from voucherflow.validation.vistas import preparar_vista_fiel
from voucherflow import api

documento = api.process("comprobante.pdf")
vista = preparar_vista_fiel(documento, "comprobante.pdf")   # vista fiel (E-QWE-2)
resultado = extraer(
    OllamaClient(),
    markdown=documento.markdown,
    vista=vista,
    documento_id="doc-1",
)
resultado.evidencias_por_fuente()   # {'vlm': SourceEvidence, 'llm': SourceEvidence}
resultado.debilidades               # ['[llm] La fuente declaró ...']
resultado.detalle["fuentes_sin_insumo"]
```

> Los valores que publica el `SourceEvidence` están **normalizados** (T-402: CUIT
> cortado, fecha ISO, montos numéricos) y el valor crudo de cada campo viaja en
> `meta['valor_crudo']`; `extraer(..., normalizar=False)` devuelve la lectura cruda
> de T-401. Las dos evidencias se conservan **sin colapsar** (la combinación por
> campo con precedencia ADR-002 es T-404: `combinar_evidencia` sigue lanzando
> `NotImplementedError`).

Las funciones de esqueleto de fases futuras (`run`, `concluir`,
`escalar_a_agente`, `encolar_hitl`, `CaseRecorder.registrar`,
`combinar_evidencia`) lanzan `NotImplementedError` a propósito. Ya **no** son
esqueletos `process` (F1), `validate` (F2), `classify` (F3),
`flujo_vlm`/`flujo_llm`/`extract` (F4/T-401) ni la normalización key-value
(F4/T-402).

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

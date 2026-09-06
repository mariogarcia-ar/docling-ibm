# v2 — Estado actual y qué se puede probar (fase F0)

> **Documento**: estado vivo de la versión 2 de `ibm-docling`.
> **Fecha**: 2026-09-06 · **Rama**: `v2` · **Commit**: `df5efe5` (fase F0)
> **Fuentes**: `v2/README.md`, `v2/docs/plan/05-plan/F0.md`, `v2/src/voucherflow/`

---

## 1. Fase actual

| Campo | Valor |
|---|---|
| **Fase en curso** | **F0 — Fundación** (schemas, esqueleto, golden set) |
| **Estado** | 🟡 Implementación de F0 **completada** (T-001..T-006 hechas) — en **revisión**; resta la curación de etiquetas de negocio del golden set con contador |
| **F1–F6** | 🔴 Backlog (procesamiento, validación, clasificación, extracción, conclusión, cliente) |
| **Paquete** | `voucherflow` v`0.1.0` (layout `src/`, ADR-007) |
| **Contrato** | `SCHEMA_VERSION = 1.0.0` (congelado, ver criterio de cambio en `schemas/evidence.py`) |
| **Suite de tests** | ✅ **77 tests en verde** (`python -m pytest tests -q`) en env `py313_env` |

**Resumen**: en esta fase lo que existe es la **fundación de la librería**:
contratos de evidencia congelados, configuración centralizada, adaptadores
`OllamaClient`/`DoclingConverter`, base del motor de reglas, el **golden set
inicial** y el esqueleto (contratos de entrada/salida) de las 5 capacidades que
se implementarán en F1–F5. **Todavía no hay pipeline funcional de extremo a
extremo** (eso llega con F1–F5/F6).

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

## 3. Qué se puede probar en esta fase (F0)

### 3.1 Rápido — instalación e import

```bash
# Desde la raíz del repo (entorno py313_env)
python -m pip install -e v2/
python -c "import voucherflow; print(voucherflow.__version__, voucherflow.SCHEMA_VERSION)"
# → 0.1.0 1.0.0
```

### 3.2 Suite de tests (77 en verde)

```bash
cd v2
python -m pytest tests -q
```

Cobertura de la suite por archivo:

| Archivo de test | Qué valida |
|---|---|
| `test_schemas_evidence.py` | Contrato de evidencia: construcción/validación de `SourceEvidence`, `CombinedEvidence`, enums, helper `nueva_meta`, criterio de cambio. |
| `test_schemas_result.py` | `VoucherResult`, `CaseRecord`, `RegistroEtapa`, `HitlDecision` y serialización. |
| `test_models_ollama.py` | `OllamaClient` con **mock HTTP**: payload de chat, reintentos/backoff, timeouts, mensajes de error. |
| `test_models_docling.py` | Construcción del `DoclingConverter` y contrato `ProcessedDocument` (sin conversión real de archivos, marcada integración). |
| `test_settings_config.py` | `Settings`: defaults, override por env `VOUCHERFLOW_*`, precedencia de fuentes. |
| `test_golden_y_esqueleto.py` | Golden set (integridad de `casos.csv` vs. archivos en `fixtures/`, splits sin cruce) y esqueletos (firmas presentes, lanzan `NotImplementedError`). |
| `test_fixtures.py` | Integridad/consistencia de los fixtures del golden set. |

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

- ❌ Procesar/convertir un documento real con Docling (llega en **F1**, T-101..T-105).
- ❌ Gate "¿es comprobante?" estilo qween (llega en **F2**, T-201..T-204).
- ❌ Clasificar tipo/letra y cadena contable (llega en **F3**, T-301..T-305).
- ❌ Extracción VLM/LLM con evidencia combinada (llega en **F4**, T-401..T-405).
- ❌ Conclusión reglas→agente→HITL + trazabilidad persistida (llega en **F5**).
- ❌ CLI/batch (`voucherflow …`) y paridad v1 sobre `files/` (llega en **F6**).

Todas las funciones de esqueleto (`detectar`, `validar_comprobante`,
`clasificar_*`, `flujo_vlm/llm`, `combinar_evidencia`, `concluir`,
`escalar_a_agente`, `encolar_hitl`, `CaseRecorder.registrar`) lanzan
`NotImplementedError` a propósito.

---

## 4. Pendientes de F0 (para cerrar la fase)

- [ ] Curar **etiquetas de negocio** del golden set (`letra`, `condición
      fiscal`, `veredicto`) con el contador (T-004 / plan §3.4 y §4).
- [ ] Reportar **métricas de la fase** y dejar sin deuda técnica bloqueante
      para F1 (DoD transversal de calidad — `docs/plan/06-estrategia-calidad.md`).
- [ ] Actualizar el estado de F0 a "cerrada" en `docs/plan/05-plan/F0.md` y
      arrancar F1 (`docs/plan/05-plan/F1.md`, T-101).

---

## 5. Referencias

- Documentación de arquitectura/plan: `v2/docs/plan/` (00-glosario, 03-arquitectura, 04-ADRs, 05-plan, 06-calidad).
- README del paquete: `v2/README.md`.
- Seguimiento por fase: `v2/docs/plan/05-plan/F0.md` … `F6.md`.

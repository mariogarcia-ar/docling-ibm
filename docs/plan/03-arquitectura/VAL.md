# Módulo validation — Validación qween doble-paso (Seguimiento)

> Documento de seguimiento generado a partir de
> [`03-arquitectura-solucion.md`](../03-arquitectura-solucion.md).

## 1. Ficha del módulo

| Campo | Valor |
|---|---|
| **Módulo (paquete)** | `voucherflow/validation/` (doc 03 §11: `qween.py` — doble-paso vistas rápida/revisión/fiel) |
| **Responsabilidad** | Decidir con una vista barata (thumbnail/calidad baja) y un prompt binario si el documento es comprobante (3 salidas); si es indeterminado, redecidir con vista de revisión; y para comprobantes, preparar una vista fiel de alta calidad que alimenta la extracción (sin reutilizar la vista rápida). |
| **Épicas asociadas** | E-QWE (E-QWE-1 gate de decisión rápida, E-QWE-2 preparación de vista fiel) |
| **Fase(s) del plan** | F2 (T-201..T-204); depende de F0 (T-005 config/`OllamaClient`) y F1 (representación procesada) |
| **Contratos que expone/consume** | Expone: `ValidationResult { decision, vista_usada, evidencia: SourceEvidence }`. Consume: `ProcessedDocument` (de processing) y el cliente `OllamaClient`/VLM (models) para la decisión binaria. |
| **ADRs relacionados** | ADR-001 (contrato de evidencia: la decisión del gate reporta `SourceEvidence`); ADR-007 (layout); decisión heredada de v1 (modelo VLM por defecto vía Ollama). |
| **Interfaces clave** | `validar_y_procesar()` (invocada por el orquestador); flujo: `preparar_vista_rapida()` → decisión binaria → `no_comprobante` → rechazar/reencolar; `indeterminado` → `preparar_vista_revision()` → segunda pasada; `comprobante` → `preparar_vista_fiel()` (alta calidad + orientación) → flujo de extracción. |
| **Responsable ciclo** | team analysis (BA/SA/PM) → team implementation |
| **Estado de diseño** | 🔴 Borrador |
| **Fecha inicio** |  |
| **Fecha fin** |  |

## 2. Estado de trazabilidad del módulo

| Elemento de arquitectura (doc 03) | Sección | Épica/Historia | Fase/Tarea | Estado |
|---|---|---|---|---|
| `preparar_vista_rapida` (thumbnail / baja calidad) | §4.2 | E-QWE-1 | F2 / T-201 | [ ] pendiente |
| Decisión binaria `decidir_es_comprobante` (prompt corto, 3 salidas) | §4.2 | E-QWE-1 | F2 / T-202 | [ ] pendiente |
| Rechazo/reencolado si `no_comprobante` (no llega a extracción) | §4.2 + §5.1 | E-QWE-1 | F2 / T-202 | [ ] pendiente |
| `preparar_vista_revision` (segunda pasada para `indeterminado`) | §4.2 + §5.2 | E-QWE-1 | F2 / T-203 | [ ] pendiente |
| `preparar_vista_fiel` (alta calidad + orientación, NO reutiliza vista rápida) | §4.2 + §5.2 | E-QWE-2 | F2 / T-203 | [ ] pendiente |
| Dataclass `ValidationResult { decision, vista_usada, evidencia }` | §4.2 + §9 | E-QWE | F2 / T-202 | [ ] pendiente |
| Secuencia del "doble paso" qween (detalle) | §5.2 | E-QWE | F2 / T-203 | [ ] pendiente |
| Tests: ahorro de costo (no extraer no-comprobantes) y calidad distinta | DoD F2 (doc 05) | E-QWE | F2 / T-204 | [ ] pendiente |
| Principio de diseño "doble calidad" (nunca se reutiliza la vista rápida para extraer) | §1 (principio 4) | E-QWE | F2 / T-203 | [ ] pendiente |

## 3. Definition of Design / contratos a congelar

- [ ] Interfaz pública acordada: dataclass `ValidationResult { decision, vista_usada, evidencia: SourceEvidence }` — fijar el enumerado de `decision` (comprobante | no_comprobante | indeterminado).
- [ ] Contrato de entrada/salida alineado al schema de evidencia: la decisión del gate debe reportar `SourceEvidence` (fragmento de sustento) — ADR-001; la vista fiel es la entrada de classification/extraction.
- [ ] ADR(s) asociado(s) resueltos: ADR-001 (formato de la evidencia de decisión); confirmar qué insumos de procesamiento (resolución/orientación) garantizan la "fidelidad" exigida por E-QWE-2.
- [ ] Casos de golden set / tests que lo validan: casos comprobante / no-comprobante / indeterminado + test de que los no-comprobantes no llegan a extracción y de calidad distinta entre vistas (T-204).

## 4. Decisiones abiertas que lo afectan

- **ADR-001 (D-1)** — Contrato de evidencia: define si el gate reporta evidencia estructurada (`SourceEvidence`) o solo la decisión; bloquea el diseño del prompt binario (T-202).
- **D-11 (sin ADR)** — Modalidades `llm`/`vlm`/`auto`: cómo se elige el modelo para la decisión binaria (vista rápida/revisión/fiel) se define en diseño detallado.
- **ADR-010 (D-10, config)** — Indirecto: el costo de la decisión rápida se mide en el ahorro de extracción; la política de enfriamiento del runner no condiciona el módulo pero sí las métricas de costo del gate.
- **ADRs de conclusión (D-4/ADR-004)** — Indirecto: la "certeza" del caso la decide la conclusión, no el gate; no debe confundirse la confianza del gate con la certeza final (principio 1).

## 5. Bitácora de seguimiento del módulo

| Fecha | Acción / hito | Responsable | Estado |
|---|---|---|---|
| _(vacío)_ | | | |

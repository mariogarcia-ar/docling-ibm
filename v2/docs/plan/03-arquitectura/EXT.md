# Módulo extraction — Extracción VLM + LLM (Seguimiento)

> Documento de seguimiento generado a partir de
> [`03-arquitectura-solucion.md`](../03-arquitectura-solucion.md).

## 1. Ficha del módulo

| Campo | Valor |
|---|---|
| **Módulo (paquete)** | `voucherflow/extraction/` (doc 03 §11: `flows.py` — flujo VLM y flujo LLM; `key_value.py` — normalización kvi/kvg/10/11) |
| **Responsabilidad** | Ejecutar siempre ambos flujos en paralelo (VLM sobre la imagen, LLM sobre OCR/Markdown) devolviendo evidencia con el mismo contrato (`SourceEvidence`); validar cada fuente con reglas raw (pasada 1) y combinar la evidencia por campo con fuente y resolución. |
| **Épicas asociadas** | E-EXT (E-EXT-1 flujos VLM/LLM en paralelo con contrato, E-EXT-2 validación raw por fuente pasada 1, E-EXT-3 campos key-value normalizados) |
| **Fase(s) del plan** | F4 (T-401..T-405); depende de F0 (T-001 schemas de evidencia) y de las fases de procesamiento previas |
| **Contratos que expone/consume** | Expone: `EvidenceField`/`SourceEvidence` (por flujo) y `CombinedEvidence` (combinada con `FieldResolution` por campo). Consume: vista fiel (validation) + OCR/Markdown (processing) + `OllamaClient` (VLM/LLM) + tabla de precedencia `rules/precedencia` (ADR-002). |
| **ADRs relacionados** | ADR-001 (contrato de evidencia compartido VLM/LLM — bloqueante); ADR-002 (tabla de precedencia por campo en la combinación — bloqueante); ADR-006 (reglas raw de lectura en código); ADR-007 (layout). |
| **Interfaces clave** | `extraer()` (VLM imagen + LLM OCR en paralelo); combinación modelada como `{ campo: { "vlm": EvidenceField, "llm": EvidenceField, "resolucion": FieldResolution { ganador, regla, motivo } } }`; normalización key-value (CUIT dígitos+guiones, fechas YYYY-MM-DD, montos sin separadores, punto_venta/número de PPPPP-NNNNNNNN). |
| **Responsable ciclo** | team analysis (BA/SA/PM) → team implementation |
| **Estado de diseño** | 🔴 Borrador |
| **Fecha inicio** |  |
| **Fecha fin** |  |

## 2. Estado de trazabilidad del módulo

| Elemento de arquitectura (doc 03) | Sección | Épica/Historia | Fase/Tarea | Estado |
|---|---|---|---|---|
| Flujo VLM (lee la imagen) | §4.4 | E-EXT-1 | F4 / T-401 | [ ] pendiente |
| Flujo LLM (razona el OCR/Markdown) | §4.4 | E-EXT-1 | F4 / T-401 | [ ] pendiente |
| Ambos flujos siempre en paralelo (no elegir por documento) | §4.4 | E-EXT-1 | F4 / T-401 | [ ] pendiente |
| Reglas raw VLM / reglas raw LLM (pasada 1 por fuente) | §4.4 | E-EXT-2 | F4 / T-403 | [ ] pendiente |
| Combinar evidencia por campo con fuente | §4.4 + §6 (nota de diseño) | E-EXT-1 | F4 / T-404 | [ ] pendiente |
| Resolución por campo (`FieldResolution` con precedencia ADR-002) | §6 | E-EXT-1 | F4 / T-404 | [ ] pendiente |
| Normalización key-value (CUIT, fechas, montos, punto_venta/número, ítems) | E-EXT-3 (doc 02) + §10 heredado | E-EXT-3 | F4 / T-402 | [ ] pendiente |
| Contrato `EvidenceField`/`SourceEvidence` (schema pydantic, T-001) | §6 + §9 | E-EXT / E-LIB-2 | F0 / T-001 | [ ] pendiente |
| Evidencia combinada (`CombinedEvidence`) como entrada de la conclusión | §4.5 + §9 | E-EXT / E-CONC | F4 → F5 / T-404→T-501 | [ ] pendiente |
| Paridad con `extraction_pipeline.py` (10/11) y `document_extraction.py` (kvi/kvg) | §8.2 (mapeo v1→v2) | E-EXT | F4 / T-405 | [ ] pendiente |

## 3. Definition of Design / contratos a congelar

- [ ] Interfaz pública acordada: firmas de los flujos VLM/LLM devolviendo `SourceEvidence` y de la combinación devolviendo `CombinedEvidence` (con `FieldResolution` por campo).
- [ ] Contrato de entrada/salida alineado al schema de evidencia: `EvidenceField { campo, valor, fuente, fragmento_sustento, confianza_fuente, meta }` validado con pydantic — rechazar si falta campo/valor/fuente/fragmento (E-LIB-2).
- [ ] ADR(s) asociado(s) resueltos: ADR-001 (schema estricto por campo y prompts reescritos a evidencia) y ADR-002 (tabla de precedencia definida campo por campo en workshop con negocio).
- [ ] Casos de golden set / tests que lo validan: casos donde VLM y LLM discrepan (para probar precedencia), casos de fuente internamente inconsistente (debilitada en pasada 1) y paridad de campos normalizados contra v1 (T-405).

## 4. Decisiones abiertas que lo afectan

- **ADR-001 (D-1, bloqueante)** — Contrato de evidencia: si no se congela en F0, la extracción no puede comparar VLM/LLM programáticamente (riesgo R-01).
- **ADR-002 (D-2, bloqueante)** — Tabla de precedencia por campo: sin ella la combinación no resuelve desacuerdos de forma determinista; requiere workshop con negocio por tipo de campo.
- **ADR-006 (D-6)** — Reglas raw (R4-R6) en código apoyadas en la evidencia de VLM/LLM; condiciona cómo se valida la pasada 1 por fuente.
- **D-11 (sin ADR)** — Modalidades `llm`/`vlm`/`auto` y su mapeo a los nuevos flujos; afecta la paridad con los modos `kvi/kvg/10/11` de v1 (E-EXT-1 "modalidades de la librería v1").
- **R-02 (riesgo)** — La migración de prompts (10/11/kvi/kvg) puede degradar calidad de extracción; mitigar con golden set y paridad por fase.

## 5. Bitácora de seguimiento del módulo

| Fecha | Acción / hito | Responsable | Estado |
|---|---|---|---|
| _(vacío)_ | | | |

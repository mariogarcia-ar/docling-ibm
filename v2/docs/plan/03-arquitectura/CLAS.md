# Módulo classification — Clasificación tipo/letra + contable (Seguimiento)

> Documento de seguimiento generado a partir de
> [`03-arquitectura-solucion.md`](../03-arquitectura-solucion.md).

## 1. Ficha del módulo

| Campo | Valor |
|---|---|
| **Módulo (paquete)** | `voucherflow/classification/` (doc 03 §11: `tipo_comprobante.py` — letra A/B/C/M/E con reglas R1-R7 + flujos; `contable.py` — cadena 01 → 02 → 03) |
| **Responsabilidad** | Dos subflujos: (1) clasificación tipo/letra cruzando condición fiscal esperada por negocio (reglas R1-R7) con la letra detectada en el documento (VLM recuadro + LLM texto); (2) clasificación contable por cadena encadenada centro de costo → macro categoría → concepto/código final. |
| **Épicas asociadas** | E-CLAS (E-CLAS-1 tipo/letra con reglas determinísticas, E-CLAS-2 clasificación contable 01→02→03) |
| **Fase(s) del plan** | F3 (T-301..T-305); depende de F0 (T-003/ADR-006 reglas en código) y F1 (markdown de la cadena contable) |
| **Contratos que expone/consume** | Expone: candidatos tipo/letra (descarte por reglas R1-R7, alimenta conclusion) y clasificación contable (`centro_costo`, `macro_categoria`, `concepto`, `codigo` dentro de `VoucherResult.clasificacion_contable`). Consume: `ProcessedDocument`/vista fiel + evidencia `SourceEvidence` (VLM/LLM) y el motor `rules/` (R1-R7). |
| **ADRs relacionados** | ADR-006 (reglas en código vs. prompt — bloqueante); ADR-001 (contrato de evidencia: `11.1` pasa a devolver evidencia); ADR-002 (precedencia por campo en la resolución de la letra); ADR-007 (layout). |
| **Interfaces clave** | `clasificar()` (tipo/letra + contable); subflujo tipo/letra: flujo VLM (letra del recuadro) + flujo LLM (regex/razonamiento) → reglas raw por fuente → reglas negocio R1-R3 → reglas extracción R4-R6 → reglas conflicto R7 → candidatos descartados/restantes. Subflujo contable: extract (proveedor, descripcion, monto) → paso 01 centro de costo → paso 02 macro categoría → paso 03 concepto/código + condición impositiva. |
| **Responsable ciclo** | team analysis (BA/SA/PM) → team implementation |
| **Estado de diseño** | 🔴 Borrador |
| **Fecha inicio** |  |
| **Fecha fin** |  |

## 2. Estado de trazabilidad del módulo

| Elemento de arquitectura (doc 03) | Sección | Épica/Historia | Fase/Tarea | Estado |
|---|---|---|---|---|
| Subflujo tipo/letra: vista fiel + OCR → VLM (recuadro) / LLM (regex) | §4.3 | E-CLAS-1 | F3 / T-302 | [ ] pendiente |
| Reglas raw por fuente (R4-R6 de lectura) → candidatos descartados/restantes | §4.3 | E-CLAS-1 | F3 / T-303 | [ ] pendiente |
| Reglas de negocio R1-R3 (monotributo/exento→C, R2A→A, R2B→B, R3 exportación→E) | §4.3 + §7 | E-CLAS-1 | F3 / T-301 | [ ] pendiente |
| Regla de conflicto R7 (comprobante inválido para crédito fiscal + alerta) | §4.3 + §7 | E-CLAS-1 | F3 / T-301 | [ ] pendiente |
| Migración R1-R7 del prompt WIP a motor de reglas en código | §7 | E-CLAS-1 | F3 / T-301 | [ ] pendiente |
| Reescritura de `11.1` para devolver evidencia (no la decisión) | §7 (ver decisión abierta #1/#6) | E-CLAS-1 | F3 / T-302 | [ ] pendiente |
| Subflujo contable: cadena 01 (centro de costo) → 02 (macro categoría) → 03 (concepto/código + condición impositiva) | §4.3 | E-CLAS-2 | F3 / T-304 | [ ] pendiente |
| Candidatos tipo/letra como insumo de la conclusión (reglas cruzadas) | §4.5 (entrada a conclusion) | E-CLAS-1 / E-CONC-1 | F3 → F5 / T-501 | [ ] pendiente |
| Paridad con `classification_pipeline.py` y `-M 11.1` de v1 | §8.2 (mapeo v1→v2) | E-CLAS | F3 / T-305 | [ ] pendiente |

## 3. Definition of Design / contratos a congelar

- [ ] Interfaz pública acordada: salida de tipo/letra con `candidatos_descartados`, `candidatos_restantes` y `reglas_aplicadas` (alineado a `Decision`/`CombinedEvidence`) y clasificación contable tipada (centro_costo, macro_categoria, concepto, codigo).
- [ ] Contrato de entrada/salida alineado al schema de evidencia: `11.1` reescrito debe devolver `SourceEvidence` (campo, valor, fuente, fragmento_sustento) y no la letra final (ADR-001/ADR-006).
- [ ] ADR(s) asociado(s) resueltos: ADR-006 (reglas en código), ADR-001 (prompt devuelve evidencia), ADR-002 (precedencia al resolver la letra: visual/recuadro vs. inferencia vs. padrón).
- [ ] Casos de golden set / tests que lo validan: tests unitarios de reglas R1-R7 + casos por tipo/letra (A/B/C/M/E, exportación, conflicto R7) y de la cadena contable 01→02→03 (incluye default CC0006); paridad con v1 (T-305).

## 4. Decisiones abiertas que lo afectan

- **ADR-006 (D-6, bloqueante)** — Dónde viven las reglas: si no se resuelve, el módulo no puede decidir la letra por código y queda atado al prompt (rediseño de F3; riesgo R-01).
- **ADR-001 (D-1, bloqueante)** — Contrato de evidencia: condiciona la reescritura de `11.1` y la compatibilidad con el modo plano legado de v1.
- **ADR-002 (D-2, bloqueante)** — Tabla de precedencia por campo: define qué fuente gana al resolver la letra del recuadro vs. la inferencia por desglose (R6).
- **D-11 (sin ADR)** — Mapeo de modalidades `llm`/`vlm`/`auto` heredadas de `document_extraction.py` a los nuevos flujos de tipo/letra.
- **ADR-004 (D-4)** — Indirecto: el feedback del muestreo de auditoría puede revelar reglas de letra que matchean por accidente → nueva casuística para R1-R7.

## 5. Bitácora de seguimiento del módulo

| Fecha | Acción / hito | Responsable | Estado |
|---|---|---|---|
| _(vacío)_ | | | |

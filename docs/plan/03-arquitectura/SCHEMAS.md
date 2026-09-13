# Módulo schemas — Contrato de evidencia (pydantic) (Seguimiento)

> Documento de seguimiento generado a partir de
> [`03-arquitectura-solucion.md`](../03-arquitectura-solucion.md).

## 1. Ficha del módulo

| Campo | Valor |
|---|---|
| **Módulo (paquete)** | `voucherflow/schemas/` (doc 03 §11: `evidence.py` — `EvidenceField`, `SourceEvidence`, `CombinedEvidence`; `result.py` — `VoucherResult`, `CaseRecord`) |
| **Responsabilidad** | Definir el contrato central de datos de la librería: modelos pydantic de evidencia por campo/fuente/combinada, resultado consolidado y trazabilidad, para que VLM/LLM/reglas/agente/HITL hablen el mismo esquema y los errores de contrato se detecten temprano. |
| **Épicas asociadas** | E-LIB-2 (esquemas de evidencia validados), E-EXT (consume contrato), E-CONC (resultado/trazabilidad) |
| **Fase(s) del plan** | F0 (T-001 definir y congelar schemas) — contrato base que condiciona F3/F4/F5 |
| **Contratos que expone/consume** | Expone TODOS los contratos §9: `EvidenceField`, `SourceEvidence`, `CombinedEvidence`, `VoucherResult`, `CaseRecord` (este último también en `trace/`). Consume: definiciones del glosario (`00-glosario.md` §2). No consume contratos de otros módulos (es la base). |
| **ADRs relacionados** | ADR-001 (schema estricto pydantic por campo — decisión D-1); ADR-002 (estructura de resolución por campo `FieldResolution`); ADR-005 (trazabilidad `CaseRecord`); ADR-007 (layout del paquete). |
| **Interfaces clave** | Enumerados `Fuente (vlm/llm/programa/arca/hitl)`, `Certeza (alta/baja)`, `Origen (programa/agente_ia/hitl)`; modelos `EvidenceField { campo, valor, fuente, fragmento_sustento, confianza_fuente, meta }`, `SourceEvidence { fuente, campos, valida, reglas_aplicadas, debilidades }`, `FieldResolution { ganador, regla, motivo }`, `CombinedEvidence { documento_id, campos, decision, trazabilidad }`, `Decision { concluye, certeza, origen, candidatos_descartados, candidatos_restantes, reglas_aplicadas, alertas }`, `VoucherResult { estado, tipo_comprobante, certeza, origen, campos_extraidos, clasificacion_contable, evidencia, hitl, trazabilidad }`. |
| **Responsable ciclo** | team analysis (BA/SA/PM) → team implementation |
| **Estado de diseño** | 🔴 Borrador (contrato aún *propuesto*; se congela en F0/T-001) |
| **Fecha inicio** |  |
| **Fecha fin** |  |

## 2. Estado de trazabilidad del módulo

| Elemento de arquitectura (doc 03) | Sección | Épica/Historia | Fase/Tarea | Estado |
|---|---|---|---|---|
| Esquemas pydantic de evidencia (`schemas/evidence.py`) | §6 | E-LIB-2 | F0 / T-001 | [ ] pendiente |
| `EvidenceField` y `SourceEvidence` (unidad + fuente) | §6 | E-EXT-1 / E-LIB-2 | F0 / T-001 | [ ] pendiente |
| `CombinedEvidence` con `FieldResolution` por campo (nota de diseño) | §6 | E-EXT-1 / E-CONC | F0 / T-001 | [ ] pendiente |
| `Decision` (concluye, certeza, origen, candidatos, reglas, alertas) | §6 | E-CONC-1 | F0 / T-001 | [ ] pendiente |
| `VoucherResult` (resultado consolidado con enumerados) | §6 | E-CONC / E-LIB-2 | F0 / T-001 | [ ] pendiente |
| `CaseRecord` (trazabilidad — también en `trace/`) | §4.7 + §6 | E-CONC-5 | F0 / T-001 + F5 / T-506 | [ ] pendiente |
| Diagrama ER de evidencia/caso/regla/traza/HITL | §6 | E-LIB-2 | F0 / T-001 | [ ] pendiente |
| Validación temprana: rechazo con error de contrato si falta campo/valor/fuente/fragmento | E-LIB-2 (doc 02) | E-LIB-2 | F0 / T-001 | [ ] pendiente |
| Contrato completo definido en `00-glosario.md` §2 (fuente) | §6 (referencia) | E-LIB-2 | F0 / T-001 | [ ] pendiente |

## 3. Definition of Design / contratos a congelar

- [ ] Interfaz pública acordada: modelos pydantic de `evidence.py` y `result.py` aprobados tal cual (o con ajustes) por BA/SA/negocio.
- [ ] Contrato de entrada/salida alineado al schema de evidencia: TODOS los flujos (VLM/LLM/reglas/agente/HITL) deben producir/consumir estos schemas; agregar error de contrato claro al validar (E-LIB-2).
- [ ] ADR(s) asociado(s) resueltos: ADR-001 (schema estricto por campo), ADR-005 (qué debe contener `CaseRecord` para auditoría). Congelar con criterio de cambio versionado.
- [ ] Casos de golden set / tests que lo validan: tests de schema (JSON válido/inválido por flujo), casos de combinación campo a campo con resolución, y un `VoucherResult` completo de referencia.

## 4. Decisiones abiertas que lo afectan

- **ADR-001 (D-1, bloqueante)** — Adoptar schema estricto pydantic por campo vs. JSON plano; la decisión fija TODO el contrato y es la base de F3/F4/F5 (riesgo R-01).
- **ADR-002 (D-2)** — La estructura `{ campo: { vlm, llm, resolucion } }` de `CombinedEvidence` debe soportar la tabla de precedencia; validar el shape con el workshop de precedencia. **Implementado en F4/T-404**: la tabla vive en `rules/precedencia.py` y el shape se validó en `tests/test_extraction_combinacion_t404.py` (que además cubre el atajo aditivo `CampoCombinado.valor`/`fuente` y `decision` opcional).
- **ADR-005 (D-5)** — Definir qué campos de trazabilidad exige auditoría (versión prompt, modelo, evidencia por fuente, reglas, quién decidió) y cómo se modelan en `CaseRecord`.
- **ADR-007 (D-7)** — Ubicación del paquete y nombres de archivo (`schemas/evidence.py`, `schemas/result.py`) dependen del layout `src/` decidido.

## 5. Bitácora de seguimiento del módulo

| Fecha | Acción / hito | Responsable | Estado |
|---|---|---|---|
| 2026-09-10 | **Cambios aditivos de F4/T-404** (sin bump de `SCHEMA_VERSION`, que sigue en `1.0.0`): `CampoCombinado` gana `valor` y `fuente` (opcionales, default `None`) como **atajo operativo** — el valor vigente del campo y la fuente responsable tras la resolución por precedencia—, y `CombinedEvidence.decision` pasa a ser **opcional** (default `None`). La razón del segundo: el validador exige que la certeza se derive de la etapa que decidió (glosario §2) y la **combinación (F4/T-404) no decide** — la decisión es de F5/T-501. Con el campo obligatorio, la combinación habría tenido que inventar un veredicto. Ambos cambios son compatibles hacia atrás (los consumidores que ya construían `CombinedEvidence` con `decision` siguen funcionando); el validador de coherencia de la decisión se mantiene intacto cuando la decisión está presente. | team implementation | Hecho |
| _(vacío)_ | | | |

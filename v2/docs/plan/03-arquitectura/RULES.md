# Módulo rules — Motor de reglas R1-R7, precedencia, gaps (Seguimiento)

> Documento de seguimiento generado a partir de
> [`03-arquitectura-solucion.md`](../03-arquitectura-solucion.md).

## 1. Ficha del módulo

| Campo | Valor |
|---|---|
| **Módulo (paquete)** | `voucherflow/rules/` (doc 03 §11: `registry.py` — motor de reglas; `tipo_comprobante_rules.py` — R1-R7; `precedencia.py` — tabla de precedencia por campo; `gaps.py`) |
| **Responsabilidad** | Motor de reglas declarativo que consume **evidencia** (no decisiones del modelo): registrar reglas con id y prioridad, evaluar condiciones sobre el contexto, ordenar por precedencia y reportar cuáles se dispararon. Incluye reglas de negocio R1-R3, de extracción R4-R6, de conflicto R7, reglas raw por fuente, precedencia por campo y detección de gaps. |
| **Épicas asociadas** | E-CLAS-1 (R1-R7 tipo/letra), E-EXT-2 (reglas raw pasada 1), E-CONC-1/2 (reglas cruzadas y gaps), E-LIB-4 (motor de reglas determinísticas) |
| **Fase(s) del plan** | F0 (T-003 resolver ADR-006), F3 (T-301 migrar R1-R7, T-303 reglas raw por fuente) y F5 (T-501 reglas cruzadas, T-502 gaps) |
| **Contratos que expone/consume** | Expone: resultado de reglas disparadas (id, condición, resultado) que alimenta `Decision.reglas_aplicadas` y `SourceEvidence.reglas_aplicadas`/`valida`. Consume: contexto de evidencia (`SourceEvidence`, `CombinedEvidence`, condiciones fiscales del emisor/receptor). |
| **ADRs relacionados** | ADR-006 (reglas de negocio en código vs. prompt — decisión D-6, bloqueante); ADR-002 (tabla de precedencia por campo); ADR-003 (gatillo de gaps para evidencia adicional). |
| **Interfaces clave** | Dataclass `Rule { id, prioridad, condicion(ctx), resultado, tipo }` (ej. `REGLA_R1 = Rule(id="R1", prioridad=1, condicion=..., resultado="C", tipo="negocio")`); registro/motor: ejecutar sobre evidencia, ordenar por prioridad y registrar disparos; `rules/precedencia.py` (PREC_1..PREC_N por campo); `rules/gaps.py` (faltan_datos por gap concreto). |
| **Responsable ciclo** | team analysis (BA/SA/PM) → team implementation |
| **Estado de diseño** | 🔴 Borrador |
| **Fecha inicio** |  |
| **Fecha fin** |  |

## 2. Estado de trazabilidad del módulo

| Elemento de arquitectura (doc 03) | Sección | Épica/Historia | Fase/Tarea | Estado |
|---|---|---|---|---|
| Motor de reglas declarativo (registro `Rule` + evaluación + prioridad + disparos) | §7 | E-LIB-4 | F0 / T-001 (schema afín) + F3 | [ ] pendiente |
| Migración R1-R7 del prompt WIP a código (`rules/tipo_comprobante_rules.py`) | §7 | E-CLAS-1 | F3 / T-301 | [ ] pendiente |
| Regla R1 (emisor monotributo/exento → C) | §7 + doc 02 | E-CLAS-1 | F3 / T-301 | [ ] pendiente |
| Reglas R2A/R2B (responsable inscripto emisor/receptor → A/B) | §7 + doc 02 | E-CLAS-1 | F3 / T-301 | [ ] pendiente |
| Regla R3 (exportación → E, prioridad sobre R1/R2) | §7 + doc 02 | E-CLAS-1 | F3 / T-301 | [ ] pendiente |
| Reglas R4-R6 (extracción: recuadro VLM, regex texto, inferencia por desglose) | §7 + doc 02 | E-CLAS-1 | F3 / T-301 | [ ] pendiente |
| Regla R7 (conflicto financiero → alerta comprobante inválido crédito fiscal) | §7 + doc 02 | E-CLAS-1 | F3 / T-301 | [ ] pendiente |
| Reglas raw por fuente (pasada 1) → `SourceEvidence.valida`/`debilidades` | §4.4 (reglas raw VLM/LLM) | E-EXT-2 | F4 / T-403 | [ ] pendiente |
| Tabla de precedencia por campo (`rules/precedencia.py`, PREC_n) | §6 + ADR-002 | E-EXT-1 | F4 / T-404 | [ ] pendiente |
| Reglas cruzadas de conclusión (negocio + fast-fail + conflicto) | §4.5 | E-CONC-1 | F5 / T-501 | [ ] pendiente |
| Detección de gaps (`rules/gaps.py`) → gatillo de evidencia adicional | §4.5 | E-CONC-2 | F5 / T-502 | [ ] pendiente |
| LLM/VLM dejan de "aplicar reglas" y devuelven evidencia; el motor decide | §7 (ver decisión #1/#6) | E-CLAS-1 / E-EXT | F3/F4 | [ ] pendiente |
| Casuística observada en HITL → nuevas reglas (crece % certeza alta por programa) | §7 | E-CONC-4 / E-LIB-4 | F5 + post-MVP | [ ] pendiente |

## 3. Definition of Design / contratos a congelar

- [ ] Interfaz pública acordada: dataclass `Rule` y contrato del motor (cada regla reporta id, condición evaluada y resultado; orden por prioridad; registro de disparos) — E-LIB-4.
- [ ] Contrato de entrada/salida alineado al schema de evidencia: el motor consume `SourceEvidence`/`CombinedEvidence` y produce las listas `reglas_aplicadas` de `Decision`/`SourceEvidence`; los prompts dejan de decidir (ADR-006 + ADR-001).
- [ ] ADR(s) asociado(s) resueltos: ADR-006 (reglas en código, bloqueante), ADR-002 (precedencia por campo con regla de oro "ley comprobada manda para descartar; papel manda para detectar").
- [ ] Casos de golden set / tests que lo validan: tests unitarios por regla R1-R7 (doc 02 E-CLAS-1), casos de desacuerdo VLM/LLM para precedencia, y casos con gaps para `gaps.py`.

## 4. Decisiones abiertas que lo afectan

- **ADR-006 (D-6, bloqueante)** — Dónde viven las reglas: adoptar (a) motor en código con evidencia; el prompt conserva "qué buscar" pero devuelve evidencia. Impacta directamente a `tipo_comprobante_rules.py` y a la reescritura de `11.1`.
- **ADR-002 (D-2, bloqueante)** — Tabla de precedencia por campo: define la resolución `FieldResolution { ganador, regla, motivo }`; requiere workshop con negocio en Fase 1.
- **ADR-003 (D-3)** — Gaps "críticos" que disparan búsqueda de evidencia adicional (ej. CAE para constatar); definir con negocio qué gaps son críticos.
- **ADR-004 (D-4)** — El muestreo de auditoría alimenta el feedback que se codifica como reglas nuevas; define el mecanismo de extensibilidad del registro.

## 5. Bitácora de seguimiento del módulo

| Fecha | Acción / hito | Responsable | Estado |
|---|---|---|---|
| _(vacío)_ | | | |

# 03-arquitectura — Seguimiento por módulo de la librería

> Documento de seguimiento generado a partir de
> [`03-arquitectura-solucion.md`](../03-arquitectura-solucion.md), con trazabilidad
> contra épicas ([`02-epicas-historias-usuario.md`](../02-epicas-historias-usuario.md)),
> fases/tareas ([`05-plan-ejecucion.md`](../05-plan-ejecucion.md)) y ADRs
> ([`04-decisiones-abiertas-adr.md`](../04-decisiones-abiertas-adr.md)).

Esta carpeta convierte la arquitectura de solución en un conjunto de archivos de
**seguimiento por módulo de la librería**: cada pieza de arquitectura (componente,
flujo, dataclass, regla, adaptador) queda rastreada contra su épica, fase, tarea,
contrato y ADR, y se registra su estado de diseño/implementación.

---

## 1. Cuadro resumen de módulos

| Archivo | Módulo (paquete) | Responsabilidad | Estado de diseño | Fase(s) |
|---|---|---|---|---|
| [`SCHEMAS.md`](SCHEMAS.md) | `voucherflow/schemas/` | Contrato de evidencia pydantic (`EvidenceField`, `SourceEvidence`, `CombinedEvidence`, `VoucherResult`, `CaseRecord`) | 🔴 Borrador | F0 (T-001) |
| [`PROC.md`](PROC.md) | `voucherflow/processing/` | Ingestión multi-formato: detector de tipo, gate de procesabilidad, clasificador de imagen, preprocesamiento, orientación, motor OCR/VLM, enrutado PDF por página y exportación ordenada | 🟡 En implementación (T-101..T-104 + routing; falta orquestación + T-105) | F0 (T-006) + F1 (T-101..T-105) |
| [`VAL.md`](VAL.md) | `voucherflow/validation/` | Gate "¿es comprobante?" por doble paso qween (vista rápida → revisión → vista fiel) | 🔴 Borrador | F2 (T-201..T-204) |
| [`CLAS.md`](CLAS.md) | `voucherflow/classification/` | Tipo/letra (R1-R7 + VLM/LLM) y clasificación contable (cadena 01→02→03) | ✅ T-301..T-305 implementados (DoD de F3 verificado) | F3 (T-301..T-305) |
| [`EXT.md`](EXT.md) | `voucherflow/extraction/` | Extracción con flujos VLM y LLM en paralelo, reglas raw (pasada 1) y combinación por campo | ✅ T-401..T-405 implementados (DoD de F4 verificado; paridad con v1 medida en tres niveles) | F4 (T-401..T-405) |
| [`RULES.md`](RULES.md) | `voucherflow/rules/` | Motor de reglas declarativo: R1-R7, precedencia por campo, fast-fail y gaps | 🟡 R1-R7 (T-301) + evidencia (T-302) + reglas raw (T-303, ampliadas en F4/T-403) + precedencia por campo (F4/T-404, validada por la paridad de F4/T-405); cruzadas/gaps (F5) pendientes | F0 (T-003/ADR-006) + F3 (T-301/T-303) + F4 (T-403/T-404) + F5 (T-501/T-502) |
| [`CONC.md`](CONC.md) | `voucherflow/conclusion/` | Reglas cruzadas → búsqueda de evidencia adicional → agente IA → HITL → consolidación | 🟡 En implementación (T-501/T-502 hechas: reglas cruzadas y búsqueda acotada de evidencia; T-503..T-507 pendientes) | F5 (T-501..T-507) |
| [`MODELS.md`](MODELS.md) | `voucherflow/models/` | Adaptadores de modelo: `OllamaClient`, `DoclingConverter`, `ArcaClient` (opcional) | 🔴 Borrador | F0 (T-005/T-006) |
| [`TRACE.md`](TRACE.md) | `voucherflow/trace/` | Registro por caso (`CaseRecord`), trazabilidad y persistencia (JSON sidecar) | 🔴 Borrador | F5 (T-506) + F6 (T-603) |
| [`ORCH-CLI.md`](ORCH-CLI.md) | `voucherflow/orchestrator.py` · `api.py` + `cli/` | Orquestador del pipeline, API de alto nivel y cliente CLI (orquestador/consumidor) | 🔴 Borrador | F5/F6 (T-601..T-606) |

> **Nota de estado**: los módulos de fases futuras (VAL, TRACE, ORCH-CLI,
> y SCHEMAS/MODELS pendientes de confirmar su trazabilidad) siguen en **🔴
> Borrador**. **`PROC.md` (processing)** ya pasó a **🟡 En
> implementación** (F1): T-101..T-104 + enrutado por página hechos; falta la
> orquestación `procesar_documento()` + `api.process()` y la paridad T-105.
> **`CLAS.md` (classification, F3)** y **`EXT.md` (extraction, F4)** ya están
> **✅ implementados con su DoD verificado**; **`CONC.md` (conclusion, F5)** está
> en **🟡 En implementación** (T-501/T-502 hechos: reglas cruzadas de la pasada 2
> y búsqueda acotada de evidencia adicional) y **`RULES.md`** está 🟡 con el motor
> completo hasta F5/T-502 — solo le faltan las reglas cruzadas de la casuística
> HITL (post-MVP).

---

## 2. Estado de los elementos transversales (no son de un módulo)

> Elementos de arquitectura que cruzan todos los módulos y que se siguen desde
> esta carpeta para que ninguna pieza quede sin trazabilidad.

| Elemento transversal | Sección (doc 03) | Qué condiciona | Estado de revisión |
|---|---|---|---|
| Principios de diseño (1-7) | §1 | Deciden el comportamiento de TODOS los módulos (etapa decide certeza, evidencia como contrato, determinista primero, doble calidad, trazable, librería primero, modular) | 🟡 En revisión — se validan contra las ideas y el negocio en F0 |
| C4 Nivel 1 (contexto) | §2 | Actores (operador, contador/auditor, sistema futuro), dependencias externas (Ollama, Docling, ARCA) y `files/` (sidecars + store) | 🔴 Borrador — validar alcance MVP y opcionalidad de ARCA (decisión abierta D-3/ADR-003) |
| C4 Nivel 2 (contenedores) | §3 | CLI / API HTTP / uso embebido + orquestador + módulos + motor de reglas + schemas + modelos + trace + settings | 🔴 Borrador — la API HTTP (fase 2) no bloquea el MVP (T-606 / E-CLI) |
| Pipeline end-to-end y escenarios | §5 | Secuencia general y secuencia del doble paso qween (interacción entre módulos vía orquestador) | 🔴 Borrador — fijar contratos entre etapas en F0 (T-001) |
| Mapeo v1 → v2 | §8.2 | Equivalencias verificables por fase (paridad como criterio de salida en F1..F6) | 🔴 Borrador — se completa fase a fase sobre el golden set |
| NFRs / despliegue y recursos | §10 | Paralelismo, enfriamiento (ADR-010), checkpoints/reanudación, observabilidad y seguridad | 🟡 En revisión — ADR-010 ya está redactado como política configurable |
| Estructura de paquete propuesta | §11 | Layout `src/voucherflow/` + `cli/` + `tests/` — la ubicación `src/` y el nombre son decisión abierta (ADR-007) | 🔴 Borrador — propuesta de trabajo, no decisión tomada |

**Elementos que cruzan toda la trazabilidad:**

- **Contratos de integración** (tabla §9 del doc 03): `ProcessedDocument`,
  `ValidationResult`, `EvidenceField`/`SourceEvidence`, `CombinedEvidence`,
  `VoucherResult`, `CaseRecord`. Se siguen desde [`SCHEMAS.md`](SCHEMAS.md) y
  desde cada ficha de módulo que los expone/consume.
- **ADRs bloqueantes** (doc 04): ADR-001/002/005/006/007 deben resolverse en F0
  (T-003) antes de codificar F3/F4/F5; su estado se refleja en las fichas de los
  módulos afectados.

---

## 3. Enlaces

- Arquitectura fuente: [`03-arquitectura-solucion.md`](../03-arquitectura-solucion.md)
- Épicas y tracking: [`02-epicas-historias-usuario.md`](../02-epicas-historias-usuario.md) · carpeta [`02-epicas/`](../02-epicas/)
- Fases y WBS: [`05-plan-ejecucion.md`](../05-plan-ejecucion.md) · carpeta [`05-plan/`](../05-plan/)
- Decisiones/ADRs: [`04-decisiones-abiertas-adr.md`](../04-decisiones-abiertas-adr.md)
- Contratos/glosario: [`00-glosario.md`](../00-glosario.md)

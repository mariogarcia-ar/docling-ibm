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
| **Estado de diseño** | 🟡 Motor declarativo (F0) + R1-R7 y contexto tipado (F3/T-301) + evidencia de lectura (F3/T-302) + reglas raw por fuente (F3/T-303) implementados; precedencia (T-404) y cruzadas/gaps (F5) pendientes |
| **Fecha inicio** | 2026-09-10 |
| **Fecha fin** |  |

## 2. Estado de trazabilidad del módulo

| Elemento de arquitectura (doc 03) | Sección | Épica/Historia | Fase/Tarea | Estado |
|---|---|---|---|---|
| Motor de reglas declarativo (registro `Rule` + evaluación + prioridad + disparos) | §7 | E-LIB-4 | F0 / T-001 (schema afín) + F3 | [ ] pendiente |
| Migración R1-R7 del prompt WIP a código (`rules/tipo_comprobante_rules.py`) | §7 | E-CLAS-1 | F3 / T-301 | [x] hecho (3 registros: negocio/lectura/conflicto) |
| Regla R1 (emisor monotributo/exento → C) | §7 + doc 02 | E-CLAS-1 | F3 / T-301 | [x] hecho (`condicion_r1`) |
| Reglas R2A/R2B (responsable inscripto emisor/receptor → A/B) | §7 + doc 02 | E-CLAS-1 | F3 / T-301 | [x] hecho (`condicion_r2a`/`condicion_r2b`) |
| Regla R3 (exportación → E, prioridad sobre R1/R2) | §7 + doc 02 | E-CLAS-1 | F3 / T-301 | [x] hecho (`prioridad=0`; pisa a R1/R2) |
| Reglas R4-R6 (extracción: recuadro VLM, regex texto, inferencia por desglose) | §7 + doc 02 | E-CLAS-1 | F3 / T-301 | [x] hecho en cascada (`REGISTRO_LECTURA`). **T-305** ajustó el patrón de R5: el `\s+` literal del WIP cruzaba el salto de línea del markdown y tomaba la letra de la línea siguiente (`"FACTURA\n  Código: 1"` → `C`); ahora usa `[ \t]+` + `\b` (ver `CLAS.md` para el detalle) |
| Regla R7 (conflicto financiero → alerta comprobante inválido crédito fiscal) | §7 + doc 02 | E-CLAS-1 | F3 / T-301 | [x] hecho (`condicion_r7` + `construir_alerta()`) |
| Reglas raw por fuente (pasada 1) → `SourceEvidence.valida`/`debilidades` | §4.4 (reglas raw VLM/LLM) | E-EXT-2 | F4 / T-403 | [x] hecho: registro de F3/T-303 (`RAW_CAMPO`/`RAW_VOCABULARIO`/`RAW_SUSTENTO`/`RAW_CONTRADICCION`) **extendido** por T-403 con `ImplicacionCoherencia` + `CampoDeclarado.sostenedor`/`normalizador_valor` + `violaciones_de_coherencia()` (id `RAW_COHERENCIA`, `VeredictoRaw.incoherencias`); el llamador (`extraction/evidencia.py`) declara el sostén por forma canónica y las implicaciones de E-EXT-2 sin que el registro sepa del dominio |
| Tabla de precedencia por campo (`rules/precedencia.py`, PREC_n) | §6 + ADR-002 | E-EXT-1 | F4 / T-404 | [ ] pendiente |
| Reglas cruzadas de conclusión (negocio + fast-fail + conflicto) | §4.5 | E-CONC-1 | F5 / T-501 | [ ] pendiente |
| Detección de gaps (`rules/gaps.py`) → gatillo de evidencia adicional | §4.5 | E-CONC-2 | F5 / T-502 | [ ] pendiente |
| LLM/VLM dejan de "aplicar reglas" y devuelven evidencia; el motor decide | §7 (ver decisión #1/#6) | E-CLAS-1 / E-EXT | F3/F4 | [x] hecho para tipo/letra en F3/T-302 (`tipo-comprobante@1` no pide la decisión; `evidencia.py` vuelca la lectura al contexto y decide el motor R1-R7); los flujos de F4/T-401 reutilizan el patrón |
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
| 2026-09-10 | T-301 implementado: R1-R7 viven como `Rule` declarativas en `rules/tipo_comprobante_rules.py` (tres registros separados por familia: negocio/lectura/conflicto, decisión F3-subplan §2.3) y se evalúan sobre `rules/contexto.py` (`ContextoTipoComprobante`, inmutable). `registry.py` **no** se reescribió (contrato F0 congelado). Las reglas *raw* por fuente (T-303) y la precedencia por campo (T-404) se agregan en sus tareas sin tocar esta base. | team implementation | Hecho |
| 2026-09-10 | T-303 (F3): la **pasada 1** de reglas raw por fuente vive en `rules/raw.py` como cuatro `Rule` declarativas del motor de F0 (`tipo="raw"`, `Registry` intacto): `RAW_CAMPO` (fuente incompleta), `RAW_VOCABULARIO` (valor fuera del vocabulario), `RAW_SUSTENTO` (el fragmento no contiene el valor) y `RAW_CONTRADICCION` (el fragmento cita otro valor). El registro es **agnóstico del dominio** (recibe `CampoDeclarado` con vocabulario/normalizador/patrón que pasa el llamador), de modo que F4/T-403 lo reutiliza sin reimplementarlo. Gradación válida/dudosa/inválida → `SourceEvidence.valida` + `debilidades`; los candidatos descartados/restantes salen de lo que el **sustento** contradice, no de lo que el modelo lista. `registry.py` **no** se reescribió (contrato F0 congelado). | team implementation | Hecho |
| 2026-09-10 | T-305 (F3): la verificación de paridad con v1 encontró y corrigió un defecto del patrón de **R5**. El patrón se había portado **literal** del prompt WIP (`FACTURA\s+([A-CME])|COMPROBANTE\s+([A-CME])`), y el `\s+` se come el **salto de línea** del markdown de Docling: sobre dos PDFs reales del golden cuyo encabezado dice `FACTURA A`, R5 devolvía la letra `C` (el `C` de "**C**ódigo" en `"FACTURA\n  Código: 1"`). El defecto sobrevivió a T-301 porque **en v1 ese patrón nunca se ejecutó como código**: vivía en el prompt WIP como `criterio` descriptivo para el modelo. Corrección mínima y conservadora de la semántica del `criterio` ("letra junto a FACTURA"): `\s+` → `[ \t]+` (espacios y tabulaciones, nunca salto de línea) + `\b` tras la letra (para no tomar la inicial de la palabra siguiente). Cubierto por `tests/test_classification_paridad.py` (`TestRegresionR5SaltoDeLinea`). | team implementation | Hecho |
| 2026-09-10 | T-403 (F4): el registro raw **se extiende sin reescribirse** para completar la pasada 1 de extracción (E-EXT-2). El motor de F0 y las cuatro reglas de T-303 quedan intactos; lo que crece son los puntos de extensión **por campo** que declara el llamador: `CampoDeclarado.sostenedor` (predicado de sostén equivalente, **autoridad** del sostén cuando el formato importa) y `CampoDeclarado.normalizador_valor` (forma canónica del valor, solo para comparar), más `ImplicacionCoherencia` para las implicaciones de la fuente consigo misma, evaluadas sobre el conjunto por `violaciones_de_coherencia()` y reportadas con el id `RAW_COHERENCIA` + `VeredictoRaw.incoherencias`. Hallazgo del diseño: el `sostenedor` se consulta **antes** que la contención literal, porque `"1.234,56"` está contenido en `"Ajuste: 1.234,56-"` (que sostiene `-1234.56`) — la contención sola produce falsos positivos justo donde el formato importa. Gravedad de la incoherencia: `dudosa` (una fuente inconsistente aporta un indicio contradictorio; decide la combinación, T-404). | team implementation | Hecho |

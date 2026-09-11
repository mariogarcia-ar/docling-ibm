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
| **Estado de diseño** | 🟡 En implementación (T-401: flujos en paralelo + contrato de evidencia; T-402: normalización key-value; T-403: reglas raw por fuente; T-404..T-405 pendientes) |
| **Fecha inicio** | 2026-09-10 |
| **Fecha fin** |  |

## 2. Estado de trazabilidad del módulo

| Elemento de arquitectura (doc 03) | Sección | Épica/Historia | Fase/Tarea | Estado |
|---|---|---|---|---|
| Flujo VLM (lee la imagen) | §4.4 | E-EXT-1 | F4 / T-401 | [x] hecho (`extraction/flows.py::flujo_vlm` + `evidencia.py`: prompt de evidencia `extraccion-key-value@1`, un `EvidenceField` por campo con sustento) |
| Flujo LLM (razona el OCR/Markdown) | §4.4 | E-EXT-1 | F4 / T-401 | [x] hecho (`extraction/flows.py::flujo_llm`, mismo contrato que el VLM) |
| Ambos flujos siempre en paralelo (no elegir por documento) | §4.4 | E-EXT-1 | F4 / T-401 | [x] hecho (`extraer_evidencia()` con `ThreadPoolExecutor`, una tarea por fuente; `max_workers=1` serializa solo si se pide) |
| Reglas raw VLM / reglas raw LLM (pasada 1 por fuente) | §4.4 | E-EXT-2 | F4 / T-403 | [x] hecho (`rules/raw.py`: registro de T-303 + sostén por **forma canónica** de montos/fechas/CUIT + `RAW_COHERENCIA`; `extraction/evidencia.py`: `CAMPOS_SOSTEN_ESTRUCTURADO`, sostenedores de dominio y `COHERENCIA_POR_CAMPO`) |
| Combinar evidencia por campo con fuente | §4.4 + §6 (nota de diseño) | E-EXT-1 | F4 / T-404 | [ ] pendiente (`combinar_evidencia` sigue esqueleto; T-403 deja cada fuente calificada por sí sola, que es su insumo) |
| Resolución por campo (`FieldResolution` con precedencia ADR-002) | §6 | E-EXT-1 | F4 / T-404 | [ ] pendiente |
| Normalización key-value (CUIT, fechas, montos, punto_venta/número, ítems) | E-EXT-3 (doc 02) + §10 heredado | E-EXT-3 | F4 / T-402 | [x] hecho (`extraction/key_value.py`: reglas portadas a código de los prompts `10`/`11`/`kvi`/`kvg`; `normalizar=True` por default en `ejecutar_flujo`; el crudo sobrevive en `meta['valor_crudo']`) |
| Contrato `EvidenceField`/`SourceEvidence` (schema pydantic, T-001) | §6 + §9 | E-EXT / E-LIB-2 | F0 / T-001 | [x] hecho (F0; consumido por T-401) |
| Evidencia combinada (`CombinedEvidence`) como entrada de la conclusión | §4.5 + §9 | E-EXT / E-CONC | F4 → F5 / T-404→T-501 | [ ] pendiente (T-402 deja los valores comparables campo a campo y T-403 deja cada fuente calificada por sí sola) |
| Paridad con `extraction_pipeline.py` (10/11) y `document_extraction.py` (kvi/kvg) | §8.2 (mapeo v1→v2) | E-EXT | F4 / T-405 | [ ] pendiente (el intérprete ya tolera el JSON plano de `kvi`/`kvg` para poder medirla) |

> **Nota de alcance (T-401 → T-403)**: el sostén de los campos de **formato
> estructurado** (montos, fechas, CUIT) **no** se evalúa por igualdad literal —el
> OCR decide los separadores y el formato, así que exigir literalidad produciría
> debilidades espurias— pero **sí se evalúa**: desde **T-403** se compara la
> **forma canónica** del valor contra la del fragmento (`12345.67` ←
> `"$ 12.345,67"`, `-1234.56` ← `"(1.234,56)"`, `2025-08-14` ←
> `"14/08/2025"`, `30123456789` ← `"30-12345678-9"`). La única entrada que queda
> sin evaluar es la `descripcion` (frase sintética) y vive declarada en
> `ExtraccionEvidencia.detalle["modelos"][fuente]["sosten_no_evaluado"]`; los
> campos con sostenedor propio se listan en `sosten_forma_canonica`.
>
> **Nota de alcance (T-403)**: además del sostén, la pasada 1 evalúa la
> **coherencia de la fuente consigo misma** (E-EXT-2): una fuente que dice `A`
> sin los dos CUIT, o `B` con IVA discriminado, queda **debilitada antes de
> combinarse** (`RAW_COHERENCIA`, gravedad `dudosa` — decide la combinación,
> T-404). Lo que la fuente no pudo evaluar **no se castiga** y las reglas que
> dependen de la **condición fiscal** del emisor quedan para el padrón/F5: la
> extracción no lee esa condición.
>
> **Nota de alcance (T-402)**: normalizar **no** borra la lectura. El valor
> publicado en el `SourceEvidence` es el canónico de E-EXT-3 y el que reportó el
> modelo queda en `meta['valor_crudo']`; `normalizar=False` devuelve la lectura
> cruda de T-401 completa. Un valor **no normalizable** (un monto con palabras, un
> año de dos dígitos) **conserva el crudo con un aviso** en vez de descartarse:
> "no normalizable" no es "ausente". Los avisos que son limitaciones reales
> (CUIT truncado, monto ambiguo, fecha inexistente) llegan a
> `SourceEvidence.debilidades`; los que solo documentan una decisión (el número no
> tiene la forma `PPPPP-NNNNNNNN`) quedan en la traza sin degradar la evidencia.

## 3. Definition of Design / contratos a congelar

- [x] Interfaz pública acordada: firmas de los flujos VLM/LLM devolviendo `SourceEvidence` (`extraction/flows.py::flujo_vlm`/`flujo_llm`, T-401) y de la combinación devolviendo `CombinedEvidence` (**pendiente**: T-404, `combinar_evidencia` sigue esqueleto). El lector de modelo es un parámetro inyectable (protocolo `extraction.Lector`, mismo criterio que F3-subplan §2.6).
- [x] Contrato de entrada/salida alineado al schema de evidencia: `EvidenceField { campo, valor, fuente, fragmento_sustento, confianza_fuente, meta }` validado con pydantic — el intérprete de T-401 rechaza con error de contrato claro lo que no es JSON de objeto (`ErrorEvidencia`) y declara explícitamente el fragmento ausente (E-LIB-2). El `valor` publicado es el **normalizado** (T-402) y el crudo queda en `meta['valor_crudo']`, junto a la trazabilidad de la regla (`regla_normalizacion`, `avisos_normalizacion`, `derivado_de`) y del sostén (`sosten_estructurado`, T-403).
- [x] ADR(s) asociado(s) resueltos: ADR-001 (schema estricto por campo y prompts reescritos a evidencia: `extraccion-key-value@1` no normaliza ni decide — **la normalización vive en código**, `key_value.py`, T-402) y ADR-002 (**parcial**: T-401 conserva ambas evidencias sin colapsar; la tabla de precedencia por campo la aplica T-404). **ADR-006** se materializa en T-403: la pasada 1 por fuente (contrato verificado en código, no pedido al prompt) incluye el sostén por forma canónica y la coherencia de la fuente consigo misma.
- [ ] Casos de golden set / tests que lo validan: los casos de discrepancia VLM/LLM y de fuente inconsistentemente sostenida están cubiertos por `tests/test_extraction_flujos.py` + `scripts/F4/t401.py`, las reglas de normalización por `tests/test_extraction_key_value.py` (97) + `scripts/F4/t402.py` y la pasada 1 por `tests/test_extraction_raw_t403.py` (37) + `scripts/F4/t403.py` (11/11 criterios de sostén · 7/7 escenarios · 4/4 fronteras); el subconjunto del golden y la paridad de campos normalizados contra v1 son de **T-405**.

## 4. Decisiones abiertas que lo afectan

- **ADR-001 (D-1, bloqueante)** — Contrato de evidencia: si no se congela en F0, la extracción no puede comparar VLM/LLM programáticamente (riesgo R-01). **Resuelto en F0 y consumido por T-401**: los dos flujos devuelven el mismo `SourceEvidence`.
- **ADR-002 (D-2, bloqueante)** — Tabla de precedencia por campo: sin ella la combinación no resuelve desacuerdos de forma determinista; requiere workshop con negocio por tipo de campo. **Sigue bloqueando T-404** (T-401 no lo suple: conserva las dos evidencias).
- **ADR-006 (D-6)** — Reglas raw (R4-R6) en código apoyadas en la evidencia de VLM/LLM; condiciona cómo se valida la pasada 1 por fuente. **Materializado en F4/T-403**: la pasada 1 es código (`rules/raw.py`) sobre la evidencia, con los puntos de extensión (`sostenedor`, `ImplicacionCoherencia`) declarados por el llamador — el prompt sigue sin decidir nada.
- **D-11 (sin ADR)** — Modalidades `llm`/`vlm`/`auto` y su mapeo a los nuevos flujos; afecta la paridad con los modos `kvi/kvg/10/11` de v1 (E-EXT-1 "modalidades de la librería v1"). **Parcial**: T-401 corre siempre las dos fuentes (no hay modalidad que elija una) y su intérprete tolera el JSON plano de `kvi`/`kvg`, así que la paridad de T-405 es medible.
- **R-02 (riesgo)** — La migración de prompts (10/11/kvi/kvg) puede degradar calidad de extracción; mitigar con golden set y paridad por fase (T-405).

## 5. Bitácora de seguimiento del módulo

| Fecha | Acción / hito | Responsable | Estado |
|---|---|---|---|
| 2026-09-10 | **T-401 hecha**: los flujos VLM (vista fiel de F2) y LLM (OCR/Markdown de F1) corren **en paralelo** (`ThreadPoolExecutor`, una tarea por fuente) y cada uno devuelve `SourceEvidence` con el contrato de F0 (ADR-001). Prompt de evidencia versionado `extraccion-key-value@1` (el modelo reporta valor + fragmento de sustento; no normaliza ni decide). El intérprete no inventa campos ausentes, tolera el JSON plano de v1 (`kvi`/`kvg`) y reutiliza la pasada raw de T-303; los campos de formato volátil quedan explícitamente sin evaluar por sostén (T-402/T-403). Una fuente caída no tumba a la otra; todas caídas lanzan `ErrorExtraccion`. Módulo en 🟡 En implementación. | team implementation | Hecho |
| 2026-09-10 | **T-402 hecha**: `extraction/key_value.py` normaliza los campos clave (E-EXT-3) portando a **código** las reglas de los prompts `10`/`11`/`kvi`/`kvg`: CUIT solo dígitos y guiones propios con corte ante caracteres extraños, fechas `YYYY-MM-DD` solo completas y reales, montos numéricos sin separadores de miles (con signo y constancia de ambigüedad), `punto_venta`/`numero_comprobante` derivados del número impreso, moneda `ARS`/`USD` sin default, texto colapsado, `descripcion` en minúsculas e ítems estructurados. El `SourceEvidence` publica el valor canónico y conserva el crudo en `meta['valor_crudo']`; `normalizar=False` devuelve la lectura cruda de T-401 y la pasada raw de T-303 sigue evaluando el crudo. Regla dura: un dato ilegible conserva el crudo con aviso (no se inventa un canónico). | team implementation | Hecho |
| 2026-09-10 | **T-403 hecha**: la pasada 1 por fuente (E-EXT-2) se completa sobre los dos huecos que T-401 declaró. El sostén de los campos de **formato estructurado** (montos y fechas) ahora se evalúa contra la **forma canónica** (`12345.67` ← `"$ 12.345,67"`, `-1234.56` ← `"(1.234,56)"`, `2025-08-14` ← `"14/08/2025"`) y el de los **CUIT** por secuencias de dígitos; además entra la **coherencia de la fuente consigo misma** (`RAW_COHERENCIA`): una Factura A sin los dos CUIT —o una B con IVA discriminado— queda **debilitada antes de combinarse**. Los puntos de extensión (`sostenedor`, `ImplicacionCoherencia`) los declara el llamador, así que `rules/raw.py` sigue agnóstico del dominio y el motor de F0 intacto. La `descripcion` sigue sin evaluarse y las reglas que dependen de la condición fiscal del emisor quedan para el padrón/F5. | team implementation | Hecho |

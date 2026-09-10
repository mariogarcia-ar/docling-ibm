# F3-subplan — Subplan de implementación F3 (Clasificación)

> Documento de trabajo para la implementación de la **Fase F3** por
> `team implementation`. Complementa el seguimiento de la fase
> ([`F3.md`](F3.md)) y el diseño de los módulos
> ([`../03-arquitectura/CLAS.md`](../03-arquitectura/CLAS.md) y
> [`../03-arquitectura/RULES.md`](../03-arquitectura/RULES.md)).
> **Fecha**: 2026-09-10 · **Rama**: `v2` · **Estado**: DoD verificado
> (T-301..T-305 hechas; cierre documental pendiente).

## 1. Ficha del subplan

| Campo | Valor |
|---|---|
| **Fase** | F3 — Clasificación (tipo/letra por reglas + cadena contable 01→02→03) |
| **Tareas que cubre** | T-301, T-302, T-303, T-304, T-305 (ver [`F3.md`](F3.md)) |
| **Épicas asociadas** | E-CLAS (E-CLAS-1 tipo/letra, E-CLAS-2 contable) |
| **Módulos** | `voucherflow/classification/` (nuevo) + `voucherflow/rules/` (ampliado) |
| **Responsable** | team implementation |
| **Decisiones de alcance** | Cerradas con el negocio/equipo (ver §2) |
| **DoD de referencia** | "La letra se decide por reglas sobre evidencia (no por el prompt); la cadena contable reproduce v1; tests de reglas R1-R7 unitarios" (DoD de F3 en `05-plan-ejecucion.md`) |
| **Depende de** | F0 (ADR-006 aceptado, `Rule`/`Registry`, contrato de evidencia, `OllamaClient`) y F1 (markdown de la cadena contable, `ProcessedDocument`) |
| **Habilita a** | F4 (la evidencia de tipo/letra y las reglas raw alimentan la extracción), F5 (candidatos → conclusión) |

## 2. Decisiones de alcance cerradas (2026-09-10)

1. **ADR-006 ya está aceptado (F0) → F3 no re-decede**: T-301 **implementa** el
   motor en código sobre la base declarativa de F0 (`Rule` + `Registry` de
   `rules/registry.py`, congelada). No se pierde tiempo re-discutiendo "código
   vs. prompt"; el prompt conserva *qué buscar* y devuelve **evidencia**
   (ADR-001).
2. **R1-R7 como datos declarativos, no como `if` dispersos**: las siete reglas
   viven en `rules/tipo_comprobante_rules.py` como instancias `Rule` con `id`,
   `prioridad`, `condicion` y `resultado` portados **literalmente** del prompt
   WIP (`prompts/wip/deteccion_tipo_factura.yaml`), con su `detalle` legible
   para auditoría. `registry.py` **no se reescribe**: si hace falta ampliarlo
   (p. ej. helper de evaluación con contexto tipado), la ampliación debe ser
   compatible con los tests de F0.
3. **Dos registros, no uno (separación lectura vs. negocio)**: hay **dos**
   familias de reglas con semántica distinta y no se mezclan en el mismo
   registro:
   - `REGISTRO_NEGOCIO` (R1, R2A, R2B, R3) → `tipo_esperado_por_negocio`.
   - `REGISTRO_LECTURA` (R4 recuadro VLM, R5 regex texto, R6 inferencia por
     desglose) → `tipo_detectado_por_documento`, en **cascada** R4 → R5 → R6.
   - `REGISTRO_CONFLICTO` (R7) → alertas (`alerta.disparada/regla/mensaje`).
   El módulo expone un único `clasificar_tipo_comprobante()` que los orquesta
   (orden de evaluación del WIP §`orden_de_evaluacion`) y reporta
   `reglas_aplicadas`.
4. **Precedencia de la letra final (decisión de alcance, revisable)**: el
   default implementado es el de `11.1` de v1 — **si la letra detectada
   contradice la esperada, la letra final es la detectada**, con confianza baja,
   la discrepancia documentada y la alerta R7 disparada cuando aplica. El WIP
   `deteccion_tipo_factura.yaml` resolvía lo contrario ("la ley manda sobre el
   papel": letra final = esperada por negocio). Ambas semánticas se conservan en
   un parámetro (`preferencia_letra: "documento" | "negocio"`, default
   `"documento"`) y **la elección definitiva se valida con negocio** (se
   documenta en `CLAS.md`; si negocio objeta, el cambio es de una línea).
5. **T-302: `11.1` se reescribe como prompt de evidencia, versionado en código**:
   `classification/prompt_tipo_comprobante.py` congela el texto (portado de
   `prompts/facturacion/11.1-deteccion_tipo_factura.yaml`, unificando `system`,
   `system_llm` y `system_vlm`) con identificador `tipo-comprobante@1`. La
   salida **no** incluye la decisión final: incluye
   `tipo_detectado_por_documento`, `tipo_detectado_por_documento_explicacion`
   (el **recuadro** que vio el VLM o el texto OCR que sustenta la letra),
   `candidatos_descartados`, `candidatos_restantes` y `campos_desconocidos`. La
   letra la decide el motor de reglas (§3.1), no el modelo.
6. **Los flujos VLM/LLM reales son de F4 → F3 usa un lector inyectable**: los
   flujos `flujo_vlm`/`flujo_llm` de `extraction/flows.py` todavía lanzan
   `NotImplementedError` (F4/T-401). F3 **no** los implementa ni los llama:
   define un **protocolo de lector** (callable que recibe la vista fiel de F2 o
   el markdown de F1 y devuelve un objeto con la evidencia de lectura) y lo
   **inyecta** desde los tests con dobles. Sin Ollama real en la suite default
   (criterio F1 §4 / F2 §2.3). F3 deja la integración real documentada en
   `CLAS.md` para que F4 la conecte sin refactor.
7. **La cadena contable sí ejecuta Ollama en F3 (con doble en tests)**: a
   diferencia de los flujos de lectura, el pipeline 01→02→03 **es** el
   refactor de `v1/classification_pipeline.py` y su ejecución real pertenece a
   esta fase. Se expone en dos capas:
   - `contable.ejecutar_paso_01/02/03(...)` → pipeline real (prompts 01/02/03 +
     `OllamaClient` de F0), con portado fiel de `primary_center_cost()` /
     `primary_macro_category()` y del patrón de **checkpoints** de v1
     (`*_classification.json`, reanudación por paso).
   - `clasificar_contable(markdown, condicion_impositiva, ...)` (contrato F0)
     → variante **pura**: recibe los tres JSON ya resueltos (o un `pasos` de
     checkpoint) y devuelve `ClasificacionContableResult` **sin red**, testeable
     en la suite default.
   **Provisional documentado**: hasta F4, `proveedor`/`monto` se pasan como
   `"no informado"` y `descripcion` = markdown procesado (portado literal de
   `classify_document()` de v1). Cuando F4 entregue los campos extraídos, la
   cadena los consume sin cambiar su contrato (parámetro `base_values`).
8. **`090`/`099` (tiques) quedan fuera de R1-R7 (decisión abierta D-13)**: el
   enum `TipoComprobante` contempla `090`/`099` y el golden ya tiene evidencia
   de tiques ("TIQUE FACTURA A"), pero el prompt WIP **no** define reglas para
   ellos. F3 no inventa mapeo letra↔código AFIP: se registra como decisión
   abierta (**D-13**, a cerrar con contador) y el motor los trata como letra no
   concluida (queda la letra del tique si la lectura la aporta, con certeza
   baja) en lugar de forzar A/B/C.
9. **Golden set: no se etiqueta la `letra` masivamente en F3**: la columna
   `letra` del `casos.csv` sigue `pendiente` porque requiere criterio de
   contador (F2 §2.5 / `tests/golden/README.md`). Para el DoD se construye un
   **subconjunto de paridad acotado** (`tests/golden/F3/`) con casos cuya letra
   está **sustentada por evidencia objetiva** (texto nativo del PDF u OCR real)
   y casos **sintéticos deterministas** para R1-R7; se documenta en
   `tests/golden/F3/README.md`. La curación masiva con contador sigue siendo
   post-F3.
10. **Cliente/CLI: el portado del pipeline es herramienta, no librería**: el
    pipeline 01→02→03 corre con `scripts/F3/classification_pipeline.py` (portado
    de v1 con `--condicion-impositiva`, `-m`, `-o` y checkpoints) y con
    `scripts/F3/t3xx.py` (inspección/reportes). La **librería** expone
    `api.classify()`; ningún módulo de `voucherflow/` importa `scripts/`.

## 3. Alcance por tarea (T-301..T-305)

### 3.1 T-301 · Migrar R1-R7 del prompt WIP a motor de reglas en código ✅ Hecho

> **Estado 2026-09-10**: **Hecho** por `team implementation`. Suite completa en
> verde (**553 passed, 10 skipped**, tras T-303); `python -c "import voucherflow.classification,
> voucherflow.rules, voucherflow.api"` devuelve `ok` y `python scripts/F3/t301.py`
> reporta **14/14** escenarios sintéticos coincidentes con la expectativa. Ver
> bitácora en [`F3.md`](F3.md) §4.

- **Qué**: portar R1-R7 a `Rule` declarativas + contexto tipado de entrada +
  orquestación de los tres registros (§2.3) que decide la letra final y arma
  `TipoComprobanteResult` (contrato F0: `letra`, `certeza`, `origen`,
  `candidatos_descartados`, `candidatos_restantes`, `reglas_aplicadas`,
  `alertas`).
- **Archivos**:
  - `rules/contexto.py` — `ContextoTipoComprobante` (dataclass): condiciones
    fiscales de emisor/receptor + país, evidencia de lectura (letra del
    recuadro VLM, texto de encabezado LLM, desglose IVA, campos totales),
    datos ausentes. Es el "contexto tipado" que anticipa `RULES.md`. ✅
  - `rules/tipo_comprobante_rules.py` — `REGISTRO_NEGOCIO` (R1/R2A/R2B/R3),
    `REGISTRO_LECTURA` (R4/R5/R6), `REGISTRO_CONFLICTO` (R7) + helpers de
    construcción (`construir_registros()`). ✅
  - `rules/__init__.py` (ampliado: exportar los registros y el contexto;
    **no** romper `Rule`/`Registry`). ✅
  - `classification/tipo_comprobante.py` — `clasificar_tipo_comprobante()`
    implementado; `clasificar_contable()` sigue `NotImplementedError` (T-304). ✅
- **Contrato de decisión** (portado del WIP §`orden_de_evaluacion`):
  1. R3 (exportación) tiene prioridad máxima: si aplica → `E`, fin.
  2. R1/R2A/R2B → `tipo_esperado_por_negocio`.
  3. R4 → R5 → R6 en cascada → `tipo_detectado_por_documento`.
  4. Comparar; coincidencia → letra final, certeza alta.
  5. Discrepancia → R7 (y reglas análogas) → alerta; letra final según
     `preferencia_letra` (§2.4); `campos_desconocidos` a la vista.
- **Prioridades implementadas**: R3=0, R1=1, R2A/R2B=2, R4=10, R5=11, R6=12,
  R7=20 (R4-R6 arrancan en 10 para marcar que son otra familia; el orden entre
  familias lo fija el orquestador, no la prioridad).
- **Tests** (T-301, unitarios y deterministas, sin Ollama) ✅:
  `tests/test_rules_tipo_comprobante.py` (81 tests) — **una clase por regla** (R1,
  R2A, R2B, R3 con prioridad sobre R1/R2, R4 recuadro, R5 regex
  `FACTURA\s+([A-CME])|COMPROBANTE\s+([A-CME])`, R6 desglose, R7 alerta) +
  combinaciones (R3 pisa R1/R2, R2A+R4-B conflicto+R7, R4 gana sobre R5, R5 solo
  si R4 no dio, R6 desempate por condición fiscal, casos parciales con
  `campos_desconocidos` → certeza baja), `candidatos_descartados/restantes`,
  `reglas_aplicadas` trazable y sin cruce descartados∩restantes (ADR-008).
  `tests/test_rules_contexto.py` (41 tests) — construcción incremental,
  normalización defensiva, inmutabilidad, `campos_desconocidos()` y `desde_dict`
  (shape del WIP).
- **Herramienta de inspección** ✅: `scripts/F3/t301.py` — imprime los tres
  registros (`--reglas`), corre **14 escenarios sintéticos** que cubren R1..R7,
  el cruce negocio-vs-documento, la discrepancia con alerta R7 y los casos
  parciales (marca ✅/❌ contra la expectativa; salida no-cero si alguno falla) y
  acepta un `--contexto` JSON propio con el shape plano o el anidado del WIP
  (`emisor`/`receptor`/`ocr`), con `--preferencia-letra` y reporte `--json`.
- **Pendiente de otras tareas**: ~~T-303 enriquece `candidatos_descartados/
  restantes` con las reglas raw por fuente; T-302 puebla el contexto desde la
  evidencia del lector~~ → **ambas hechas** (T-302 §3.2 y T-303 §3.3).

### 3.2 T-302 · Reescribir `11.1` para devolver **evidencia** (VLM recuadro + LLM texto) ✅ Hecho

> **Estado 2026-09-10**: **Hecho** por `team implementation`. Suite completa en
> verde (**553 passed, 10 skipped**, tras T-303); `python scripts/F3/t302.py`
> reporta **10/10** escenarios (T-302 → T-301 de punta a punta) coincidentes con
> la expectativa. Ver bitácora en [`F3.md`](F3.md) §4.

- **Qué**: reescribir el prompt de tipo/letra para que devuelva **evidencia
  trazable** (letra del recuadro leída por el VLM, texto OCR que sustenta la
  lectura por LLM, explicación de dónde se vio) y no la decisión; el motor de
  T-301 decide. ✅
- **Archivos**:
  - `classification/prompt_tipo_comprobante.py` — prompt versionado
    `tipo-comprobante@1` (base común que **declara que el modelo no decide** +
    guía de lectura por fuente `system_vlm`/`system_llm`), el esquema JSON del
    contrato de evidencia (§6) y `construir_messages_tipo_comprobante()` para
    `OllamaClient` (reutiliza la reducción + base64 de F2: `imagen_envio_base64`).
  - `classification/evidencia.py` — `EvidenciaLectura` (fuente, letra,
    fragmento de sustento, candidatos, campos faltantes, problemas), el
    **protocolo de lector** (§2.6), la conversión a
    `schemas.evidence.SourceEvidence` (ADR-001), `contexto_desde_evidencia()`
    (puebla R4/R5) y `leer_evidencia()` (corre las fuentes y conserva **ambas**
    evidencias, ADR-002).
- **Contrato de evidencia (ADR-001)**: cada dato leído es un `EvidenceField`
  con `campo`, `valor`, `fuente` (`vlm`/`llm`/`programa`), `fragmento_sustento`
  y `certeza`. El **recuadro** del VLM y el **texto** del LLM son evidencias
  separadas que se conservan ambas (no se colapsan).
- **Qué quedó fuera del prompt (ADR-006)**: `tipo_comprobante`,
  `tipo_esperado_por_negocio`, `reglas_aplicadas`,
  `coincide_negocio_vs_documento`, `confianza` y `alerta` (los calcula el motor
  de T-301); `CAMPOS_FUERA_DEL_CONTRATO` lo deja explícito y hay tests que
  verifican que no aparecen en el texto del prompt.
- **Decisión clave documentada**: R5 no lee una letra suelta, aplica su **regex**
  sobre `texto_encabezado_llm`, así que `contexto_desde_evidencia()` puebla ese
  campo con el **fragmento de sustento literal** del LLM, no con la letra.
- **Tests** (`tests/test_classification_prompt_tipo.py`, 50) ✅: contrato y
  versión del prompt (que no decide); `messages` por fuente (imagen en base64 /
  markdown en `content`); doble de lector con evidencia VLM y LLM →
  `SourceEvidence` válidas y reconstruibles; salida sin letra → `None` +
  `campos_desconocidos`; JSON inválido → `ErrorEvidencia`; y el puente completo
  T-302 → T-301 (R4 del recuadro, R5 del texto, R7 en la discrepancia).
- **Herramienta de inspección** ✅: `scripts/F3/t302.py` — imprime el prompt
  (`--prompt`), corre **10 escenarios** de punta a punta con ✅/❌, acepta
  `--json`/`--detalle` y con `--origen` corre la lectura **real** (F1 + vista de
  revisión de F2 + Ollama local).

### 3.3 T-303 · Reglas raw por fuente + candidatos descartados/restantes ✅ Hecho

> **Estado 2026-09-10**: **Hecho** por `team implementation`. Suite completa en
> verde (**553 passed, 10 skipped**); `python scripts/F3/t303.py` reporta
> **9/9** escenarios y `t301.py`/`t302.py` siguen en 14/14 y 10/10. Ver bitácora
> en [`F3.md`](F3.md) §4.

- **Qué**: reglas **raw** que validan lo que devolvió cada fuente de lectura
  (VLM/LLM) antes de usarlo como evidencia: letra fuera de {A,B,C,M,E}, texto
  que contradice la letra declarada, evidencia sin fragmento de sustento, fuente
  incompleta. Marcan `valida`/`debilidades` y alimentan la decisión con
  trazabilidad (y `candidatos_descartados` / `candidatos_restantes`). ✅
- **Archivos**: `rules/raw.py` (`REGISTRO_RAW` + `evaluar_raw(fuente, campos) ->
  VeredictoRaw`) + integración en `classification/evidencia.py` y en
  `classification/tipo_comprobante.py` (parámetro `candidatos_raw`). ✅
- **Las cuatro reglas** (todas `Rule` del motor de F0, `tipo="raw"`):

  ======================  ==============================  ====================
  Regla                   Qué verifica                    Gravedad si dispara
  ======================  ==============================  ====================
  `RAW_CAMPO`             sin valor o sin sustento        `dudosa`
  `RAW_VOCABULARIO`       valor fuera del vocabulario     `invalida`
  `RAW_SUSTENTO`          el fragmento no sostiene el valor `dudosa`
  `RAW_CONTRADICCION`     el fragmento cita otro valor    `dudosa`
  ======================  ==============================  ====================

- **Gradación `valida`/`dudosa`/`invalida`** (decisión documentada):
  `SourceEvidence.valida` es booleano, así que la gravedad se degrada —
  `invalida` → `valida=False` (violación de contrato: el valor ni siquiera
  pertenece al vocabulario), `dudosa` → `valida=True` **con** debilidades (el
  dato sirve como indicio, no como prueba), `valida` → sin debilidades.
- **Reutilización F4**: el registro es **agnóstico del dominio** (trabaja sobre
  *campos declarados*, no sobre letras): el vocabulario, el normalizador y el
  patrón de sustento los pasa el llamador. F4/T-403 lo consume sin
  reimplementarlo (hay tests que lo ejercitan con un CUIT y con un
  `tipo_documento` de vocabulario cerrado). ✅
- **Tests** (`tests/test_rules_raw.py`, 53) ✅: cada regla con su caso positivo y
  negativo; el registro como `Rule`/`Registry` de F0; vocabulario cerrado con
  `Z`/`090`/`099` (D-13) reportando el **valor crudo**; gradación y acumulación;
  candidatos descartados por contradicción y el blindaje ADR-008; la integración
  T-302 → T-303 → T-301; y la reutilización por F4.
- **Herramienta de inspección** ✅: `scripts/F3/t303.py` — imprime el registro
  (`--reglas`), corre **9 escenarios** de la cadena T-302 → T-303 → T-301
  (lectura sana, letra fuera de vocabulario, tique `090`, sustento que no
  sostiene, contradicción, fuente incompleta, recuadro del VLM sin patrón de R5,
  dos fuentes con una débil, conflicto R7) con ✅/❌ y salida no-cero, acepta
  `--detalle`/`--json` y `--campo` para validar un campo propio (el uso de F4).
- **Bugs encontrados por la herramienta** (y cubiertos con tests de regresión):
  1. `RAW_VOCABULARIO` reportaba `None` en lugar del valor crudo, porque T-302
     normalizaba la letra antes de la pasada raw. Se agregó
     `EvidenciaLectura.valor_crudo`.
  2. El sostén laxo confundía la preposición española "a" con la letra A
     ("junto **a** COD. 006"): los valores de un solo carácter ahora exigen
     mayúscula exacta.
- **Pendiente de otras tareas**: la **letra** la sigue decidiendo R1-R7 (T-301);
  el veredicto raw solo la califica. La precedencia por campo entre fuentes
  sigue siendo F4/T-404 (ADR-002).

### 3.4 T-304 · Refactor cadena contable 01→02→03 (con contratos entre pasos) ✅ Hecho

> **Estado 2026-09-10**: **Hecho** por `team implementation`. Suite completa en
> verde (**614 passed, 10 skipped**); `python scripts/F3/t304.py` reporta **8/8**
> escenarios y `t301.py`/`t302.py`/`t303.py` siguen en 14/14, 10/10 y 9/9. Ver
> bitácora en [`F3.md`](F3.md) §4.

- **Qué**: refactorizar `v1/classification_pipeline.py` a la librería con
  **contratos tipados entre pasos** (§2.7): extract → paso 01 (hasta 3 centros
  de costo) → paso 02 (hasta 3 macro categorías, entrada = 1º de 01) → paso 03
  (concepto + código final + condición impositiva). ✅
- **Archivos**:
  - `classification/prompts_contable.py` — los tres prompts de
    `prompts/01..03-*.yaml` portados **literales** y versionados
    (`contable-01@1`/`contable-02@1`/`contable-03@1`, ADR-005), con
    `renderizar_user()`, `construir_messages_contable()` y la validación de la
    condición impositiva. **Verificado**: los `system`/`user` son idénticos a
    los YAML de v1.
  - `classification/contable.py` — contratos por paso
    (`OpcionCentroCosto`/`OpcionMacroCategoria`/`PasoConceptoCodigo`),
    `primary_centro_costo()`/`primary_macro_categoria()` portados de v1,
    `ejecutar_paso_01/02/03()`, `ejecutar_cadena()` (con checkpoints),
    `ErrorCadenaContable`/`RespuestaContableInvalida` y la variante pura
    `clasificar_pasos_contables()`/`ResultadoCadenaContable`.
  - `classification/tipo_comprobante.py` (ampliado) — `clasificar_contable()`
    deja de lanzar `NotImplementedError` y es la variante pura; el contrato
    `ClasificacionContableResult` se amplía de forma **aditiva**.
  - `api.py` — `classify()` implementado (motor de letra + cadena contable).
  - `__init__.py` (ampliado).
- **Default CC0006** (criterio Gherkin de E-CLAS-2): sin señal específica, el
  paso 01 devuelve **CC0006, confianza baja, `senal_usada=none`**; se testea
  explícitamente (y el script lo muestra en el escenario `default_cc0006`). ✅
- **Contrato entre pasos**: si un paso no devuelve opciones, se propaga error de
  dominio con **resultados parciales** (portado de `ClassificationError` de v1:
  `error.steps`); la cadena nunca inventa la entrada del paso siguiente. ✅
- **Checkpoints**: `<doc>_classification.json` con el nombre y el shape de v1
  (`{"archivo": ..., "pasos": {...}}`), escritos **después de cada paso** y de
  forma atómica; reejecutar **no** vuelve a llamar al modelo para los pasos ya
  resueltos. Un sidecar corrupto degrada a re-ejecutar (no rompe la corrida). ✅
- **Tests** (`tests/test_classification_contable.py`, 60) ✅:
  - contratos por paso (tipados, inmutables, con los campos del prompt);
  - cadena feliz con `FakeOllamaClient` (01→02→03 encadenados con el 1º de cada
    paso; los `messages` del paso siguiente contienen el centro/macro anterior);
  - default CC0006 / `senal_usada=none`;
  - checkpoint: reejecutar con `*_classification.json` **no** vuelve a llamar al
    modelo; reanudación parcial; sidecar corrupto;
  - error a mitad de cadena → resultados parciales preservados;
  - **variante pura sin red** (``monkeypatch`` sobre `OllamaClient`) y la
    garantía de que **coincide** con la cadena real;
  - `api.classify` punta a punta (letra + contable + trazabilidad).
- **Pendiente de otras tareas**: la **medición** de paridad contra v1 sobre el
  golden set es **T-305** (`scripts/F3/paridad_contable.py`); acá se construyó la
  estructura y el CLI para poder correr ambos lados.

### 3.5 T-305 · Paridad con `classification_pipeline.py` y `-M 11.1` de v1 ✅ Hecho

> **Estado 2026-09-10**: **Hecho** por `team implementation`. Corrida real:
> cadena contable **8/8** campos coincidentes y exactitud de letra **v2 5/5**
> vs. **v1 2/5**. Suite **646 passed / 10 skipped**. Ver bitácora en
> [`F3.md`](F3.md) §4 y el detalle en
> [`tests/golden/F3/README.md`](../../../tests/golden/F3/README.md).

- **Qué**: verificar el DoD de F3 (y mitigar R-02): (a) **paridad funcional de
  la cadena contable** contra `v1/classification_pipeline.py` y (b) **paridad
  del flujo de tipo/letra** contra el modo `-M 11.1` de
  `v1/document_extraction.py`, sobre el subconjunto de paridad (§2.9). ✅
- **Archivos**: `tests/golden/F3/` (`subconjunto.json`, `README.md`, 7 casos
  sintéticos) + `scripts/F3/paridad_contable.py` + `scripts/F3/paridad_11_1.py`
  + `tests/test_classification_paridad.py` (32 tests). ✅
- **Hallazgo (el mayor valor de la tarea)**: sobre **dos PDFs reales del golden**
  cuyo encabezado dice `FACTURA A`, R5 devolvía la letra **`C`**. El patrón
  portado **literal** del WIP usaba `\s+`, que se come el **salto de línea** del
  markdown de Docling y captura la letra de la línea siguiente:
  `"FACTURA\n  Código: 1"` → `C` (el `C` de "**C**ódigo"). No se había visto
  antes porque **en v1 ese patrón nunca se ejecutó como código**: vivía en el
  prompt WIP como `criterio` **descriptivo** para el modelo. Corrección mínima
  (`\s+` → `[ \t]+` + `\b`), conservando la semántica del `criterio`, con test
  de regresión.
- **Métricas del DoD de F3** (reportadas en `06-estrategia-calidad.md` y
  `tests/golden/F3/README.md`): exactitud de letra por categoría (v2 **100%**
  sobre los casos etiquetados), % de alerta R7 correctamente disparada y % de
  acuerdo negocio-vs-documento (ambos **100%** sobre el tramo determinista), y
  paridad de la cadena contable (**8/8** campos).
- **Notas de entorno**: `v1/prompts/` está vacío (v1 resuelve sus prompts como
  `Path(__file__).parent / "prompts"`), así que los scripts arman la disposición
  que v1 espera en un **temporal** (sin mutar el repo) y corren v1 sobre
  **copias** (v1 escribe siempre un sidecar junto al markdown). El rol `llm` de
  `Settings` no está instalado en este entorno: `--detectar-modelo` cae al rol
  `vlm` y lo informa (la paridad no depende del modelo elegido).
- **Nota de honestidad**: el acuerdo con v1 en la letra es **2/3 comparables**;
  el criterio del DoD es **paridad o mejora** y el reporte publica las dos
  exactitudes (v2 y v1) para que un desacuerdo se lea como mejora —o
  regresión— con evidencia, no como un número aislado.
- **Herramienta de paridad del pipeline completo**: `scripts/F3/classification_pipeline.py`
  (portado de v1: `--condicion-impositiva`, `-m/--model`, `-o/--output`, sidecar
  `<doc>_classification.json`, reanudación por checkpoint) para correr ambos
  lados con el **mismo modelo** y comparar.
- **Métricas del DoD de F3** (reportadas en `06-estrategia-calidad.md` y
  `tests/golden/F3/README.md`): exactitud de letra por categoría (A/B/C/M/E),
  % de alerta R7 correctamente disparada, % de acuerdo negocio-vs-documento, y
  paridad de la cadena contable (coincidencia de `codigo`/`concepto` por caso).
- **Nota de honestidad**: si la paridad exacta con v1 no se alcanza (p. ej. por
  prompts reescritos), el criterio del DoD es **paridad o mejora** documentada
  caso por caso con la diferencia explicada — no un verde artificial.

### 3.6 Avance

- **Estado (2026-09-10)**: F3 con el **DoD verificado**. **T-301: Hecha**
  (motor de reglas R1-R7 en código + contexto tipado + `clasificar_tipo_comprobante()`),
  **T-302: Hecha** (prompt de evidencia `tipo-comprobante@1` + lector inyectable),
  **T-303: Hecha** (pasada 1 de reglas raw por fuente), **T-304: Hecha** (cadena
  contable 01→02→03 con contratos entre pasos, checkpoints y `api.classify()`)
  y **T-305: Hecha** (paridad con v1 medida con documentos y modelos reales).
  Suite en verde: **646 passed, 10 skipped** — 317 tests de F3 (122 de T-301 + 50
  de T-302 + 53 de T-303 + 60 de T-304 + 32 de T-305) sobre una base de 328.
  `classify` **ya no** está en
  `test_esqueletos_lanzan_notimplemented` (quedan `extract` y `run`, F4/F5).
- `F3.md` pasó de 🔴 Backlog a 🟡 En implementación (T-301 marcada Hecho).
- **Punto de partida real**: ADR-006 ya aceptado en F0 (base `Rule`/`Registry`
  congelada y testeada); F1 cerrando T-105 (markdown de entrada disponible vía
  `api.process`); F2 **completada** (vista fiel de extracción disponible vía
  `validation.validar_y_procesar` → `vista_fiel`, exactitud del gate 19/19).
- **Ajuste F0 pendiente de aplicar**: cuando `api.classify()` quede
  implementado (T-304), `classify` **sale** de la lista de esqueletos en
  `test_esqueletos_lanzan_notimplemented` (mismo criterio que F1 con `process` y
  F2 con `validate`, subplan F1 §2.4 / F2 §2.7). Cuidado: `extract` y `run`
  **siguen** en la lista (F4/F5).
- Suite default en verde al inicio (referencia: 187 tests al cierre de F1 + los
  de F2; **646 passed / 10 skipped** tras T-301..T-305).

## 4. Reglas duras (no romper F0/F1/F2)

- **`rules/registry.py` congelado**: `Rule(id, prioridad, condicion, resultado,
  tipo, detalle)` y `Registry(registrar/evaluar_todas/ids_disparados)` deben
  seguir funcionando tal como los fija F0 (`test_golden_y_esqueleto.py`).
  Ampliar módulos es válido; **no** cambiar los existentes.
- **Contratos de `classification/` congelados**: `TipoComprobanteResult` y
  `ClasificacionContableResult` se conservan construibles con sus campos
  actuales (`classification/__init__.py` debe seguir exportándolos). Agregar
  campos con default es válido.
- **Vocabulario de letras**: usar el enum `TipoComprobante` de
  `schemas/evidence.py` (A/B/C/M/E/090/099); no inventar valores nuevos.
- **La suite default corre sin Ollama ni Docling real**: dobles
  (`FakeOllamaClient`, lectores inyectados) o fixtures; las corridas reales
  quedan en `@pytest.mark.integration`.
- **Sin dependencias Python nuevas** (política F1 §4 / F2 §4).
- **Sin acoplamiento a `v1/`**: nada de `sys.path` ni imports de
  `classification_pipeline.py`, `document_extraction.py` ni prompts de `v1/`;
  la paridad se mide con scripts, no se importa (test de acoplamiento de F0).
- **Sin acoplamiento a `scripts/`**: `voucherflow/` no importa herramientas.
- **`settings`**: ampliar solo si hace falta y **sin** alterar los defaults
  congelados (`test_settings_config.py`); el modelo `llm` por rol ya existe
  (`qwen2.5:7b`).
- **Estilo**: docstrings y mensajes en español citando los docs (doc 03 §4.3/§7,
  IDs de épica/tarea/regla); asserts con mensaje explicativo.

## 5. Orden de evaluación del motor (referencia de implementación)

Portado literal de `prompts/wip/deteccion_tipo_factura.yaml`
§`orden_de_evaluacion`:

```text
funcion clasificar_tipo_comprobante(contexto, *, preferencia_letra="documento"):
    # 1. Exportación pisa todo (R3, prioridad 0)
    si REGISTRO_NEGOCIO.dispara("R3"): retornar letra="E", certeza="alta"

    # 2..3  Esperado por negocio (R1 / R2A / R2B) y detectado por documento (R4→R5→R6)
    esperado  = evaluar_negocio(contexto)     # R1 | R2A | R2B | None
    detectado = evaluar_lectura(contexto)     # R4 → R5 → R6 (cascada), + raw (T-303)

    # 4..5  Coincidencia
    si esperado and detectado == esperado:
        letra, certeza = detectado, "alta"

    # 6  Discrepancia → conflicto (R7) y decisión según preferencia_letra
    si esperado and detectado and detectado != esperado:
        alertas = REGISTRO_CONFLICTO.evaluar(contexto)     # R7
        letra   = detectado si preferencia_letra=="documento" si no esperado
        certeza = "baja"   # discrepancia documentada en `alertas`/`origen`

    # 7  Solo negocio, solo documento, o nada → certeza baja + campos_desconocidos
    retornar TipoComprobanteResult(letra, certeza, origen="programa",
                                   candidatos_descartados, candidatos_restantes,
                                   reglas_aplicadas, alertas)
```

- `origen` = `programa` cuando la letra salió del motor determinístico (regla de
  oro del contrato: `programa` → certeza alta solo en coincidencia o R3).
- `detalle["evidencia"]` del caso conserva las `SourceEvidence` por fuente
  (ADR-001) — mismo patrón que F2 (`qween.py`).

## 6. Contrato de evidencia de `tipo-comprobante@1` (T-302)

La salida del modelo **no** decide; reporta evidencia de lectura:

```json
{
  "tipo_detectado_por_documento": "A",
  "tipo_detectado_por_documento_explicacion": "Recuadro grande con 'A' junto a 'COD. 01'",
  "candidatos_descartados": ["B", "C"],
  "candidatos_restantes": ["A"],
  "campos_desconocidos": [],
  "fuente_lectura": "vlm"
}
```

- `fuente_lectura` ∈ `vlm` (recuadro, `system_vlm`) | `llm` (texto/regex,
  `system_llm`); el orquestador corre **ambas** cuando hay material (imagen de
  la vista fiel de F2 y markdown de F1) y conserva las dos evidencias.
- `tipo_*_explicacion` es el `fragmento_sustento` que exige ADR-001 (auditoría
  E-CONC-5): el recuadro visto o el texto OCR literal.
- Los campos que el WIP llamaba `tipo_esperado_por_negocio`, `alerta`,
  `coincide_negocio_vs_documento` y `confianza` **salen del contrato del
  prompt**: los calcula el motor (T-301). Se listan aquí para dejar explícito
  qué se elimina y por qué.

## 7. Cadena contable: mapeo v1 → v2 (T-304)

| v1 (`classification_pipeline.py`) | v2 (`classification/contable.py`) |
|---|---|
| `PROMPT_FILES["01"/"02"/"03"]` (YAML en `prompts/`) | prompts versionados en `classification/prompts_contable.py` (`contable-01@1`, `contable-02@1`, `contable-03@1`), portados de `prompts/01..03-*.yaml` |
| `load_document_text()` | `ProcessedDocument.markdown` de F1 (o el `markdown` recibido) |
| `base_values` con `"no informado"` | `base_values` con `"no informado"` hasta F4 (§2.7), inyectable |
| `execute_prompt(...)` + `lib.pipeline` | `OllamaClient.ask()` (F0/T-005) + `OllamaError` con retry/backoff |
| `primary_center_cost(step_01)` | `primary_centro_costo(PasoCentroCosto)` |
| `primary_macro_category(step_02)` | `primary_macro_categoria(PasoMacroCategoria)` |
| `write_checkpoint()` (sidecar JSON) | `escribir_checkpoint()` (mismo nombre `<doc>_classification.json`) |
| `ClassificationError(message, steps)` | `ErrorCadenaContable(message, pasos)` (resultados parciales) |
| `--condicion-impositiva {21,10_5,27,2_5,exento_no_gravado}` | idéntico, en `ejecutar_cadena()`/`clasificar_contable()` y en `scripts/F3/classification_pipeline.py` (que además **valida** el valor: v1 no lo hacía) |

## 8. Archivos a crear

- `src/voucherflow/rules/contexto.py`
- `src/voucherflow/rules/tipo_comprobante_rules.py`
- `src/voucherflow/rules/raw.py`
- `src/voucherflow/rules/__init__.py` (ampliado)
- `src/voucherflow/classification/tipo_comprobante.py` (implementado)
- `src/voucherflow/classification/evidencia.py`
- `src/voucherflow/classification/prompt_tipo_comprobante.py`
- `src/voucherflow/classification/contable.py`
- `src/voucherflow/classification/prompts_contable.py`
- `src/voucherflow/classification/__init__.py` (ampliado)
- `src/voucherflow/api.py` (`classify` implementado; import diferido, mismo
  patrón que `process`/`validate`)
- `scripts/F3/classification_pipeline.py`, `scripts/F3/paridad_contable.py`,
  `scripts/F3/paridad_11_1.py`, `scripts/F3/t301.py`, `scripts/F3/t302.py`,
  `scripts/F3/t303.py`, `scripts/F3/t304.py`, `scripts/F3/t305.py`
- Tests: `tests/test_rules_contexto.py`, `tests/test_rules_tipo_comprobante.py`,
  `tests/test_rules_raw.py`, `tests/test_classification_prompt_tipo.py`,
  `tests/test_classification_contable.py`, `tests/test_classification_paridad.py`
- Golden: `tests/golden/F3/README.md` + casos del subconjunto de paridad;
  `tests/golden/casos.csv` (ampliar `evidencia_veredicto`/`letra` **solo** en los
  casos con sustento objetivo)

## 9. Docs a actualizar al cierre

- `v2/docs/plan/05-plan/F3.md` (estados T-301..T-305 → Hecho/QA + bitácora).
- `v2/docs/plan/03-arquitectura/CLAS.md` (estado de diseño → implementado;
  precedencia de la letra §2.4; protocolo de lector §2.6; D-11 resuelta).
- `v2/docs/plan/03-arquitectura/RULES.md` (contexto tipado, registros, reglas
  raw reutilizadas por F4).
- `v2/docs/plan/02-epicas/E-CLAS.md` (estado de E-CLAS-1/E-CLAS-2).
- `v2/docs/plan/04-decisiones-abiertas-adr.md` (**D-13** mapeo
  090/099/tiques y **D-14** precedencia de la letra si negocio la objeta; ambas
  registradas en el cierre de T-301).
- `v2/docs/plan/06-estrategia-calidad.md` (métricas de F3 §Fase 3).
- `v2/current.md` (F3 en curso / cerrada; `api.classify` disponible).
- No adelantar fases previas como cerradas si no corresponde (F1 tenía T-105
  pendiente; verificar su estado antes de dar F3 por cerrada).

## 10. Verificación final

```bash
cd v2 && python -m pytest tests -q          # suite default (sin Ollama/Docling real)
python -c "import voucherflow.classification, voucherflow.rules, voucherflow.api"
python scripts/F3/t305.py --subset golden   # métricas del DoD (requiere Ollama local)
```

- Suite completa en verde; `classify` ya **no** está en
  `test_esqueletos_lanzan_notimplemented`.
- `import voucherflow.classification` / `voucherflow.rules` funciona;
  `api.classify(markdown)` devuelve `VoucherResult` con `tipo_comprobante` y
  `clasificacion_contable` (no lanza `NotImplementedError`).
- Tests de R1-R7 unitarios (una clase por regla) y cadena contable con dobles.
- Sin dependencias nuevas; sin imports de `v1/` ni de `scripts/`.
- Métricas de F3 reportadas y paridad con v1 documentada caso por caso.

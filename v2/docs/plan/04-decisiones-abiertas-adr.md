# 04 — Decisiones técnicas abiertas y ADRs (SA)

> **Rol**: Solution Architect
> **Fuente primaria**: `v2/docs/ideas/flujo_deteccion_tipo_comprobante.md`
> ("Decisiones que quedan abiertas para definir antes de implementar") +
> decisiones surgidas del refactor.

Este documento lista las decisiones abiertas, propone una **recomendación** y
redacta **ADRs preliminares** (estado *propuesto*) para las más relevantes.

> **Estado de resolución (F0, 2026-09-06)**: los ADR-001/002/005/006/007 que
> bloquean el MVP se resolvieron en la **Fase F0** y pasan a estado
> **Aceptado** (ver [`05-plan/F0.md`](05-plan/F0.md), T-003). Los ADR-003/004/
> 008/009 quedan *propuestos* y se resuelven en su fase (F5/F6). El contrato de
> evidencia congelado vive en `v2/src/voucherflow/schemas/` (F0/T-001).

---

## 1. Decisiones abiertas (mapa)

| # | Decisión | Proviene de | ¿Bloquea MVP? | ADR | Estado |
|---|----------|-------------|---------------|-----|--------|
| D-1 | Contrato de evidencia entre VLM y LLM | ideas (flujo) | Sí (bloqueante) | ADR-001 | ✅ Aceptado (F0) |
| D-2 | Tabla de precedencia por campo | ideas (flujo) | Sí (bloqueante) | ADR-002 | ✅ Aceptado (F0) |
| D-3 | Alcance de "buscar más evidencia" (gatillo + límite ARCA) | ideas (flujo + algoritmo) | Parcial (se puede dejar como hook) | ADR-003 | Propuesto (F5) |
| D-4 | Auditoría / muestreo de "certeza alta" | ideas (flujo) | No (se diseña en conclusión) | ADR-004 | Propuesto (F5) |
| D-5 | Trazabilidad completa y su persistencia | ideas (flujo) | Sí (requisito de negocio) | ADR-005 | ✅ Aceptado (F0) |
| D-6 | Dónde viven las reglas de negocio (código vs. prompt) | v1 `11.1`/WIP R1-R7 | Sí (bloqueante) | ADR-006 | ✅ Aceptado (F0) |
| D-7 | Organización del paquete y nombre de la librería | readme v2 | No (baja) | ADR-007 | ✅ Aceptado (F0) |
| D-8 | Cómo se implementa el "agente de IA" de la conclusión | ideas (algoritmo) | No (baja) | ADR-008 | Propuesto (F5) |
| D-9 | Persistencia de resultados/HITL (JSON sidecar vs. SQLite vs. servicio) | PM/NFR | No (media) | ADR-009 | Propuesto (F6) |
| D-10 | Política de enfriamiento por temperatura en lotes | `my_prompt.md` (contexto operativo) | No (media) | — (config) | Config (F6) |
| D-11 | Modalidades `llm`/`vlm`/`auto` y su mapeo a los nuevos flujos | v1 `document_extraction.py` | No | — (diseño detallado) | Abierta |
| D-12 | Volumen de `files/` como dataset: tamaño y criterio del golden set | contexto repo | No | — (ver 06) | Abierta |
| D-13 | Mapeo de comprobantes `090`/`099` (tiques) a la letra y su código AFIP | WIP R1-R7 (sin reglas para tiques) + golden | No (post-MVP) | — (a cerrar con contador) | Abierta (F3, 2026-09-10) |
| D-14 | Precedencia de la letra ante discrepancia negocio vs. documento | `11.1` de v1 (manda el documento) vs. WIP (manda la ley) | Parcial (hay default) | — (parámetro `preferencia_letra`) | Abierta — validar con negocio (F3, 2026-09-10) |

### D-13 · Mapeo de `090`/`099` (tiques) — registrada en F3/T-301

El enum `TipoComprobante` de `schemas/evidence.py` contempla `090`/`099` y el
golden ya tiene evidencia de tiques ("TIQUE FACTURA A"), pero el prompt WIP
`deteccion_tipo_factura.yaml` **no** define reglas R1-R7 para ellos. F3 **no
inventa** un mapeo letra↔código AFIP: el motor los trata como **letra no
concluida** (si la lectura aporta una letra de tique, se conserva con certeza
baja) en lugar de forzar A/B/C. **Cierre**: con contador, definiendo la regla y
el mapeo antes de considerarlos clasificados.

### D-14 · Precedencia de la letra ante discrepancia — registrada en F3/T-301

Ante discrepancia entre la letra esperada por negocio (R1/R2A/R2B) y la
detectada en el documento (R4/R5/R6), las dos fuentes históricas resolvían
distinto: `11.1` de v1 dejaba **el documento**; el WIP
`deteccion_tipo_factura.yaml` decía "la ley manda sobre el papel" (**negocio**).
F3 conserva **ambas semánticas** en un parámetro de `clasificar_tipo_comprobante()`:

- `preferencia_letra="documento"` (**default**, paridad con `11.1` de v1),
- `preferencia_letra="negocio"` (semántica del WIP).

En ambos casos la certeza es **baja**, la discrepancia queda documentada en
`detalle` y se dispara R7 cuando corresponde. **Cierre**: validar el default con
negocio; si negocio objeta, el cambio es de una línea (parámetro con default
explícito, no `if` disperso).

---

## 2. ADRs preliminares

### ADR-001 · Contrato de evidencia compartido entre VLM y LLM

- **Estado**: ✅ **Aceptado** (F0, 2026-09-06) · **Prioridad**: Alta (bloqueante) · **Decisión D-1**
- **Implementación**: `v2/src/voucherflow/schemas/evidence.py` (congelado,
  `SCHEMA_VERSION=1.0.0`, criterio de cambio documentado en el módulo). El lado
  **prompt** se materializó en **F4/T-401**: `extraction/prompt_extraccion.py`
  congela `extraccion-key-value@1` (el modelo reporta `valor` +
  `fragmento_sustento` por campo; **no** normaliza ni decide) y
  `extraction/flows.py` devuelve `SourceEvidence` con `meta.version_prompt`.
  El modo plano legado de `kvi`/`kvg` sigue aceptándose en el intérprete
  (`parsear_evidencia_extraccion`) para medir la paridad de T-405.
  **F4/T-402** cerró la otra mitad del ADR: la normalización que v1 le pedía al
  prompt vive ahora en `extraction/key_value.py` (reglas `NORM_*` versionadas
  como `extraccion-key-value-norm@1`) y el contrato publica el valor canónico sin
  perder el crudo (`meta['valor_crudo']`, `normalizar=False`).

**Contexto**
Hoy cada modalidad devuelve JSON libre según el prompt (`kvi`, `kvg`, `11.1`).
Las ideas exigen que VLM y LLM devuelvan el **mismo esquema** (campo, valor,
fuente, fragmento de sustento) para poder comparar programáticamente y aplicar
reglas determinísticas.

**Alternativas evaluadas**
1. **(a) Schema estricto pydantic por campo** (propuesta en `00-glosario.md` §2).
2. (b) Un único JSON plano por flujo y comparación "campo a campo" sin
   fragmento de sustento (más simple pero sin trazabilidad del "de dónde salió").
3. (c) Texto libre + parser (frágil, inviable para auditoría).

**Decisión (recomendación)**
Adoptar **(a)**: `EvidenceField { campo, valor, fuente, fragmento_sustento,
confianza_fuente, meta }` y `SourceEvidence` validados con pydantic. El prompt
se reescribe para devolver **evidencia**, no decisión final.

**Consecuencias**
- Los prompts actuales (`kvi/kvg/10/11`) deben adaptarse a un contrato de
  salida; se conserva compatibilidad con un modo "plano legado" mientras se
  migra el golden set.
- + Trazabilidad real por campo; + comparabilidad programática.
- − Mayor trabajo de migración de prompts y normalización de respuestas.

---

### ADR-002 · Tabla de precedencia por campo (resolución de desacuerdos)

- **Estado**: ✅ **Aceptado** (F0, 2026-09-06) · **Prioridad**: Alta (bloqueante) · **Decisión D-2**
- **Implementación**: el shape de resolución por campo (`FieldResolution` +
  `CampoCombinado`) está congelado en `schemas/evidence.py` y la tabla concreta
  (PREC_1…) se codificó en **F4/T-404**: `rules/precedencia.py`
  (`TABLA_PRECEDENCIA` + `resolver_campo()` + `combinar()`) y
  `extraction/flows.py::combinar_evidencia()`.
- **Implementación — avance por tarea**:
  * **F4/T-401**: los dos flujos entregan las dos `SourceEvidence` completas
    **sin colapsar** (`extraction/evidencia.py::extraer_evidencia`).
  * **F4/T-402**: los dos lados llegan con los **mismos valores canónicos**
    (`normalizar=True` por defecto), así que la comparación campo a campo es
    directa — sin "ganadores" espurios por formato (`"14/08/2025"` vs.
    `"2025-08-14"`).
  * **F4/T-404**: la tabla está implementada con tres reglas de lectura
    (`PREC_1` visual, `PREC_2` textual, `PREC_3` programa) más la **regla de oro**
    (`PREC_0`) para los campos sin precedencia declarada; las fuentes que no son
    lectura (`hitl` > `arca` > `programa`) van siempre por delante. La resolución
    registra `ganador`/`regla`/`motivo`, respeta el veredicto de la pasada 1
    (T-403) y conserva todas las lecturas en el `CampoCombinado`.
- **Nota de contrato (F4/T-404)**: `CombinedEvidence.decision` pasó a ser
  **opcional** (default `None`) para que la combinación no tenga que inventar un
  veredicto: la certeza se deriva de la etapa que decidió (glosario §2) y la
  decisión es de F5/T-501. Es un cambio **aditivo/compatible**, sin bump de
  `SCHEMA_VERSION`.

**Contexto**
Cuando VLM y LLM discrepan en un campo, hay que decidir qué fuente gana por tipo
de campo y condición. La idea lo plantea: "visual gana en la letra del
encabezado si el recuadro se detectó con claridad".

**Alternativas**
1. **(a) Tabla declarativa de precedencia por campo + regla** (ej.
   `PREC_1`: tipo_comprobante → gana VLM si `recuadro` detectado con claridad;
   `PREC_2`: campos numéricos → gana LLM si el OCR es legible; en general
   "papel manda": la letra detectada gana sobre inferencia, pero la condición
   fiscal comprobada (padrón) puede descartar candidatos).
2. (b) Confianza autodeclarada de cada flujo como criterio.
3. (c) Heurística ad-hoc en el código de conclusión.

**Decisión (recomendación)**
Adoptar **(a)** con la regla de oro: *la ley/negocio comprobado manda sobre el
papel para descartar; el papel (evidencia visual/textual) manda sobre la
inferencia para detectar*. La precedencia se registra en la resolución de cada
campo para auditoría.

**Consecuencias**
- + Determinismo; + auditable (cada resolución tiene regla y motivo).
- − Requiere definir la tabla campo por campo con el negocio en Fase 1
  (workshop).

---

### ADR-003 · Alcance de "buscar más evidencia" (gatillo, límites, ARCA)

- **Estado**: Propuesto · **Prioridad**: Media · **Decisión D-3**

**Contexto**
El algoritmo permite consultar evidencia adicional (ej. padrón ARCA/WSCDC) para
cubrir gaps, "con un objetivo concreto y límite de reintentos — no un loop
abierto".

**Alternativas**
1. **(a) Hook opcional con política configurable**: gatillo por reglas de gap
   (`faltan_datos`), objetivo concreto por campo, `max_reintentos=N`, timeout.
   En MVP la consulta ARCA puede ejecutarse desactivada (no bloquea) o como
   extensión.
2. (b) ARCA obligatorio en el MVP para casos con CAE legible.
3. (c) Sin evidencia adicional en v2 (se escala directo al agente).

**Decisión (recomendación)**
Adoptar **(a)**: el motor de conclusión soporta *búsqueda de evidencia
adicional* con política configurable; la integración ARCA real (WSCDC) se
implementa como adaptador opcional (reutilizando `v1/wip/consultar_arca.py`),
activado por configuración. Definir con negocio qué gaps son "críticos" (ej.
necesito CAE para constatar) y cuáles no.

**Consecuencias**
- + MVP no depende de disponibilidad/credenciales de ARCA.
- + El pipeline ya soporta el paso sin rediseñar.
- − La constatación real queda para una iteración posterior a validar con casos
  reales.

---

### ADR-004 · Auditoría y muestreo de casos de "certeza alta"

- **Estado**: Propuesto · **Prioridad**: Media · **Decisión D-4**

**Contexto**
Una regla puede matchear por accidente (ej. agente/comisionista con más de dos
CUIT) y consolidarse como correcto sin pasar por HITL nunca. Se necesita un
muestreo periódico de casos resueltos por programa.

**Alternativas**
1. **(a) Muestreo aleatorio estratificado configurable** (ej. X% de casos
   certeza alta por lote/período) encolado a HITL con prioridad baja.
2. (b) Revisión 100% de casos altos (caro, inviable en volumen).
3. (c) Sin muestreo (riesgo de drift silencioso de reglas).

**Decisión (recomendación)**
Adoptar **(a)** con tasa configurable; el resultado del muestreo alimenta el
mismo mecanismo de feedback que las correcciones de certeza baja. Definir la
tasa inicial en Fase 1 (sugerida 5-10%).

**Consecuencias**
- + Detecta falsos positivos de reglas.
- + El feedback del muestreo es la señal para mover casuística del agente → regla
  o corregir reglas existentes.
- − Carga adicional de revisión (controlada por la tasa).

---

### ADR-005 · Trazabilidad completa y persistencia

- **Estado**: ✅ **Aceptado** (F0, 2026-09-06) · **Prioridad**: Alta (requisito de auditoría) · **Decisión D-5**
- **Implementación**: contrato `CaseRecord` congelado en
  `v2/src/voucherflow/schemas/result.py`; el registrador/persistencia
  (`trace/`) se implementa en F5 (T-506).

**Contexto**
Para justificar una clasificación fiscal ante una auditoría se necesita: versión
de prompt, modelo, evidencia de cada fuente, regla disparada y quién (código o
agente) decidió.

**Alternativas**
1. **(a) `CaseRecord` JSON sidecar por documento + registro en store simple
   (SQLite) en fases posteriores**.
2. (b) Solo JSON sidecar (como v1) sin store.
3. (c) Base de datos desde el inicio.

**Decisión (recomendación)**
Adoptar **(a)**: `CaseRecord` versionado y completo por caso. En el MVP se
persiste como JSON sidecar (equivalente a los `_pipeline.json` actuales,
ampliado con `trazabilidad`); el índice/store (SQLite) se agrega cuando haya
consulta/auditoría sobre el histórico (Fase futura / E-CLI HITL list).

**Consecuencias**
- + Compatible con el patrón de sidecars de v1 (checkpoints, reanudación).
- + Trazabilidad desde el día 1 sin infraestructura nueva.
- − Las consultas agregadas sobre trazabilidad requieren indexar (post-MVP).

---

### ADR-006 · Dónde viven las reglas de negocio: código vs. prompt

- **Estado**: ✅ **Aceptado** (F0, 2026-09-06) · **Prioridad**: Alta (bloqueante) · **Decisión D-6**
- **Implementación**: base del motor declarativo (`Rule` + `Registry`) en
  `v2/src/voucherflow/rules/registry.py`; las reglas R1-R7 se migran en F3
  (T-301) y las **raw por fuente** en F3/T-303 + **F4/T-403**. El lado de la
  extracción quedó cerrado en **T-403**: la pasada 1 es `rules/raw.py` (registro
  de T-303 **extendido**, no reescrito) con los puntos de extensión
  (`CampoDeclarado.sostenedor`, `ImplicacionCoherencia`) que declara el llamador
  — `extraction/evidencia.py` define el sostén por forma canónica de montos,
  fechas y CUIT y las implicaciones de la fuente consigo misma (E-EXT-2). El
  prompt de extracción (`extraccion-key-value@1`) sigue sin decidir ni validar
  nada: reporta valor + fragmento de sustento.

**Contexto**
Hoy las reglas R1-R7 están embebidas en el prompt WIP
(`prompts/wip/deteccion_tipo_factura.yaml`) y el prompt `11.1`. La conclusión
determinística exige reglas ejecutadas por programa (no por el modelo).

**Alternativas**
1. **(a) Motor de reglas en código** (registro declarativo `Rule`) que consume
   **evidencia** de los flujos; el prompt deja de "decidir" y pasa a "reportar
   evidencia".
2. (b) Mantener reglas en prompt + parsear la decisión (como hoy; no testeable
   determinísticamente).
3. (c) Híbrido: reglas de negocio en código, reglas de "lectura" (R4-R6) como
   heurísticas de código sobre la evidencia VLM/LLM.

**Decisión (recomendación)**
Adoptar **(a)** (con heurísticas de lectura R4-R6 también en código, apoyadas en
ta la evidencia de VLM/LLM). El prompt conserva el **conocimiento de qué buscar**
(recuadro, desglose, condiciones fiscales) pero devuelve evidencia.

**Consecuencias**
- + Reglas unit-testables, versionables y auditables (cada regla reporta id).
- + El % de casos "certeza alta por programa" puede crecer codificando
  casuística observada.
- − Migración importante de `11.1` y del WIP a código + nuevo contrato de
  evidencia (impacta ADR-001).

---

### ADR-007 · Organización y nombre del paquete de la librería

- **Estado**: ✅ **Aceptado** (F0, 2026-09-06) · **Prioridad**: Baja · **Decisión D-7**
- **Implementación**: paquete `voucherflow` con layout `src/` en
  `v2/src/voucherflow/` + `v2/pyproject.toml` (instalable).

**Contexto**
"Necesito tener una librería robusta y luego un cliente para invocar al
utilitario". No está definido el nombre ni el layout.

**Alternativas**
1. **(a) Paquete `src/` layout + `pyproject.toml`, nombre tentativo
   `voucherflow`**.
2. (b) Paquete plano en `v2/` (raíz) sin `src/`.
3. (c) Mantener scripts + carpeta `lib/` como v1.

**Decisión (recomendación)**
Adoptar **(a)** con `src/` layout (mejor aislamiento de imports y tests) y un
nombre corto neutral (el nombre definitivo lo define el dueño del repo; ver
también ADR sobre branding). Cliente (CLI) en paquete separado o como entry
point del mismo paquete.

**Consecuencias**
- + Instalable (`pip install -e .`), importable desde cualquier proyecto.
- + Tests sin conflictos de ruta (se elimina el acoplamiento por `sys.path` de v1).
- − Reestructuración del código existente (es el objetivo del refactor).

---

### ADR-008 · Implementación del "agente de IA" de la conclusión

- **Estado**: Propuesto · **Prioridad**: Baja · **Decisión D-8**

**Contexto**
El flujo escala a un "agente de IA" que decide entre candidatos restantes.

**Alternativas**
1. **(a) Agente = llamada a Ollama (LLM o VLM) con prompt de decisión
   estructurado** que recibe evidencia + candidatos_restantes + reglas que
   fallaron y devuelve un JSON acotado (elección + justificación). Sin framework
   de agentes.
2. (b) Framework de agentes (langchain/crew) — sobrecarga innecesaria para una
   decisión acotada.
3. (c) Reutilizar el flujo LLM como "agente" — mezcla roles (extracción vs.
   decisión) y rompe la separación de etapas.

**Decisión (recomendación)**
Adoptar **(a)** en el MVP: el agente es una etapa *orquestada* que recibe
evidencia consolidada y un universo de candidatos ya filtrado. Se le puede
aplicar "reglas de blindaje" posteriores a su salida (nunca puede elegir un
candidato descartado).

**Consecuencias**
- + Simple, sin dependencias nuevas, alineado a la arquitectura local (Ollama).
- + El contrato de entrada/salida del agente queda estable para, en el futuro,
  reemplazar la implementación por un agente más sofisticado sin tocar el resto.

---

### ADR-009 · Persistencia de resultados y cola HITL

- **Estado**: Propuesto · **Prioridad**: Media · **Decisión D-9**

**Contexto**
Hay que persistir resultados, evidencia y decisiones HITL (correcciones,
muestreo).

**Alternativas**
1. **(a) JSON sidecar por documento (MVP) + store SQLite para índice HITL**.
2. (b) Solo sidecars (como v1).
3. (c) Servicio/API + DB desde el inicio.

**Decisión (recomendación)**
Adoptar **(a)**: sidecars para resultados/trazabilidad (continuidad con v1 y
checkpoints) y una **tabla SQLite local** (`hitl_queue`) para la cola de
revisión y las correcciones, que alimente el feedback a reglas/prompts.

**Consecuencias**
- + El HITL necesita consultas ("listame casos de certeza baja") que un store
  simple resuelve mejor que leer sidecars.
- + La corrección registrada es la semilla del feedback loop (fase futura).

---

### ADR-010 · Política de enfriamiento por temperatura (config)

- **Estado**: Propuesto (config) · **Prioridad**: Media · **Decisión D-10**

**Contexto**
En `my_prompt.md` (operativo) se documenta: tras ~10 min de procesamiento el
dispositivo se calienta; deben detenerse los workers y esperar ~2 min de
enfriamiento, y **la cuenta de los 2 min empieza cuando todos los workers están
detenidos**.

**Decisión (recomendación)**
Modelar como **política configurable** del runner de lotes:
`cooling.enabled`, `work_window_s` (600), `cool_down_s` (120), `agente` (todos
detenidos). El runner detiene el pool, espera la ventana y reanuda; los
checkpoints permiten reanudar sin reprocesar.

**Consecuencias**
- + Evita degradación térmica en lotes largos.
- + La política es testeable sin hardware real (simulación con ventanas cortas).

---

## 3. Decisiones heredadas que v2 debe *respetar* (no reabrir en el refactor)

| Decisión previa (v1) | Estado en v2 |
|----------------------|--------------|
| Modelos locales vía Ollama en `localhost:11434` (VLM por defecto `qwen2.5vl:3b`) | Se conserva como default (configurable) |
| Docling como motor de OCR/conversión multi-formato | Se conserva encapsulado en `processing` |
| Nomenclatura de prompts 10/11/11.1/01/02/03/kvi/kvg/ccc/mcc/cfc | Se mapea, no se pierde capacidad |
| Formato de salida JSON plano normalizado (CUIT, fechas, montos) | Se conserva como capa de normalización |
| Sidecars JSON + checkpoints por documento + escritura atómica | Se conserva como patrón de persistencia |
| Condiciones impositivas `21/10_5/27/2_5/exento_no_gravado` | Se conservan como enum de negocio |
| Reglas fiscales R1-R7 (WIP) | Se migran a código (ADR-006) |

---

## 4. Enlaces

- Contratos: [`00-glosario.md`](00-glosario.md)
- Arquitectura: [`03-arquitectura-solucion.md`](03-arquitectura-solucion.md)
- Cómo se resuelven en el cronograma: [`05-plan-ejecucion.md`](05-plan-ejecucion.md)

# F4-subplan — Subplan de implementación F4 (Extracción)

> Documento de trabajo para la implementación de la **Fase F4** por
> `team implementation`. Complementa el seguimiento de la fase
> ([`F4.md`](F4.md)) y el diseño del módulo
> ([`../03-arquitectura/EXT.md`](../03-arquitectura/EXT.md)).
> **Fecha**: 2026-09-10 · **Rama**: `v2` · **Estado**: ✅ **Completada**
> (T-401..T-405 hechas; DoD de F4 verificado).

## 1. Ficha del subplan

| Campo | Valor |
|---|---|
| **Fase** | F4 — Extracción (refactor de la extracción de v1) |
| **Tareas que cubre** | T-401, T-402, T-403, T-404, T-405 (ver [`F4.md`](F4.md)) |
| **Épicas asociadas** | E-EXT (E-EXT-1 flujos en paralelo, E-EXT-2 reglas raw, E-EXT-3 campos normalizados) |
| **Módulos** | `voucherflow/extraction/` (`flows.py`, `evidencia.py`, `prompt_extraccion.py`, `key_value.py`) + `voucherflow/rules/` (`raw.py`: T-303 y su extensión de T-403; `precedencia.py`: T-404) |
| **Responsable** | team implementation |
| **Decisiones de alcance** | Cerradas con el equipo (ver §2) |
| **DoD de referencia** | "Dos flujos siempre en paralelo con evidencia trazable; paridad de extracción con v1 en campos normalizados sobre golden set" (DoD de F4 en `05-plan-ejecucion.md`) |
| **Depende de** | F0 (contrato de evidencia T-001, `OllamaClient`) y las fases de procesamiento previas (F1 markdown, F2 vista fiel) |
| **Habilita a** | F5 (la evidencia combinada es la entrada de la conclusión), F6 (los modos `kvi`/`kvg` del CLI) |

## 2. Decisiones de alcance cerradas (2026-09-10)

1. **La extracción devuelve evidencia, no un JSON plano decidido**: los prompts
   `10`/`11` de v1 (`kvg`/`kvi`) pedían el dato **ya normalizado y decidido**
   (`cuit_emisor` cortado, `fecha_emision` en ISO, `comprobante_valido`
   evaluado, `categoria_gasto` inferida). **ADR-001** exige lo contrario: cada
   flujo reporta el valor **tal como se lee** con su **fragmento de sustento**, y
   la normalización (`T-402`) y la decisión (`F5`) las hace el programa. T-401
   implementa ese contrato; el prompt versionado es
    `extraccion-key-value@1`. **T-402** materializa esa normalización en código
    (`key_value.py`): el modelo sigue sin normalizar y ahora la regla de v1 es un
    test, no un párrafo de prompt.
2. **Los dos flujos corren siempre, y en paralelo real**: es la regla dura de
   E-EXT-1 ("no se elige un flujo u otro por documento"). T-401 los lanza con
   `ThreadPoolExecutor` (una tarea por fuente con insumo): el trabajo es
   I/O-bound (dos llamadas HTTP al modelo local) y no hay estado compartido. El
   script `t401.py` **mide** el paralelismo (dos llamadas de 0,2 s tardan ≈ 0,2 s,
   no 0,4 s) para que la afirmación no quede en el docstring.
3. **La ausencia de una fuente con insumo se reporta, no se silencia**: si no
   hay vista fiel (F2) no se puede correr el VLM; si no hay markdown (F1) no se
   puede correr el LLM. La corrida sigue con la fuente disponible y el
   `detalle["fuentes_sin_insumo"]` lo declara. Correr "sin un flujo" es una
   **limitación de la evidencia**, no un caso normal: el gate de conclusión (F5)
   necesita saberlo.
4. **Una fuente caída no tumba a la otra; todas caídas es error de dominio**: si
   una fuente falla (Ollama, JSON inválido), el error queda en
   `detalle["fallos"]` con la otra evidencia intacta. Si **todas** las fuentes
   con insumo fallan se lanza `ErrorExtraccion`: devolver una evidencia vacía
   haría creer que el documento no tiene datos.
5. **Las reglas raw se reutilizan de F3/T-303, no se reimplementan**: el registro
   `rules/raw.py` se diseñó **agnóstico del dominio** justamente para que F4 lo
   consuma (`RAW_CAMPO`/`RAW_VOCABULARIO`/`RAW_SUSTENTO`/`RAW_CONTRADICION`). T-401
   lo aplica para que `SourceEvidence` cumpla el contrato de F0
   (`valida`/`debilidades`/`reglas_aplicadas`); la **curaduría fina** de la
   pasada 1 de extracción es T-403 (que puede enriquecerla sin romper el contrato).
6. **El sostén literal solo se evalúa donde es significativo** (decisión
   documentada en `evidencia.py`): los campos de **formato volátil** (montos,
   fechas, descripción) **no** se evalúan por sostén literal, porque el OCR
   decide separadores de miles/decimales y el formato de fecha (`"Subtotal:
   12.345,67"` vs. `12345.67`) y la `descripcion` es una frase sintética, no un
   texto que se copia. Exigir igualdad literal ahí produciría **debilidades
   espurias**. Esos campos se listan explícitamente en
   `detalle["modelos"][fuente]["sosten_no_evaluado"]` para que T-402/T-403 los
   cubran: **no se inventa un veredicto favorable**.
7. **La ausencia de un campo esperado no es una debilidad**: el prompt pide no
   inventar, así que no declarar un campo ilegible es la conducta correcta. Se
   registra en `campos_ausentes` (con su motivo) para el gate de F5, sin degradar
   el veredicto raw.
8. **El vocabulario cerrado de T-401 es explícito y alineado con v1**:
   `tipo_comprobante ∈ {A, B, C, M, E, 090, 099}` (el prompt de v1 admite
   `090`/`099` para boletos, regla 4) y `moneda ∈ {ARS, USD}`. Esto **no**
   contradice la decisión abierta **D-13** de F3: acá se evalúa **qué se leyó**,
   no qué letra decide el negocio (esa distinción sigue siendo del motor R1-R7).
9. **Una sola autoridad por debilidad** (hallazgo de implementación): la
   violación de vocabulario la reporta **solo** `RAW_VOCABULARIO` (con el valor
   crudo) y el campo sin sustento lo reporta **solo** una vez (la regla raw si el
   campo se evalúa, T-401 si es de formato volátil). Duplicar la misma debilidad
   con dos textos distintos ensucia la auditoría sin agregar información.
10. **La combinación por campo sigue siendo T-404**: T-401 **conserva las dos
    evidencias** (`vlm` y `llm`) sin colapsarlas (ADR-001/ADR-002) y
    `combinar_evidencia()` sigue lanzando `NotImplementedError` a propósito —
    implementarla acá sería adelantar la tarea que depende de la tabla de
    precedencia por campo.
11. **La normalización (T-402) es del programa, no del prompt** (frontera dura de
    ADR-001): las reglas de v1 que vivían dentro de los prompts `10`/`11`
    (`kvi`/`kvg`) se **portan a código** en `key_value.py`, donde son
    determinísticas y testeables. El prompt sigue sin normalizar; el modelo
    reporta el valor tal como se lee.
12. **Normalizar no es borrar: el crudo siempre sobrevive** (T-402). El valor
    publicado en el `SourceEvidence` es el canónico (E-EXT-3) y el de T-401 queda
    en `meta['valor_crudo']`. `normalizar=False` devuelve la lectura cruda
    completa: es la frontera que permite a T-303 seguir reportando *qué se leyó* y
    a T-405 comparar contra v1.
13. **"No normalizable" no es "ausente"** (hallazgo de implementación): un monto
    escrito con palabras o una fecha sin año son lecturas reales del documento.
    Descartarlas perdería información, así que T-402 **conserva el crudo** y emite
    un :class:`AvisoNormalizacion`; los avisos que son limitaciones reales
    (CUIT truncado, monto ambiguo, fecha inexistente) llegan a
    `SourceEvidence.debilidades`, y los que solo documentan una decisión (el
    número no tiene la forma `PPPPP-NNNNNNNN`) quedan en la traza sin degradar.
14. **La ambigüedad del separador se resuelve con constancia, no en silencio**
    (T-402): `"12.345"` con un solo separador y 3 dígitos detrás es ambiguo; se
    aplica la convención argentina (`.` = miles) **y** se deja el aviso para que
    la revisión lo pueda confirmar.
15. **La moneda no tiene default** (T-402): v1 pedía "sin indicio explícito usá
    ARS"; acá eso sería inventar una moneda que el documento no declaró. Una
    moneda no reconocida conserva el crudo con aviso — el default es una decisión
    de negocio, no de lectura.
16. **La pasada raw de extracción se completa por dos vías, no reescribiendo el
    registro** (T-403): el registro de T-303 sigue siendo el mismo (cuatro
    ``Rule``); lo que crece son los **puntos de extensión por campo** que el
    llamador declara — ``sostenedor`` (predicado de sostén equivalente) y
    ``coherencia`` (implicaciones de la fuente consigo misma). Así el registro
    sigue **agnóstico del dominio** y F3 no se toca.
17. **El sostén de un valor con formato es una pregunta semántica, no textual**
    (hallazgo de implementación de T-403): ``"1.234,56"`` **está contenido** en
    ``"Ajuste: 1.234,56-"``, pero ese fragmento sostiene ``-1234.56``. Por eso el
    ``sostenedor`` se consulta **antes** que la contención literal: la contención
    sola daría falsos positivos justo en los campos donde el formato importa.
18. **La coherencia reporta la implicación que la fuente sí pudo evaluar**
    (T-403): si la fuente dice ``A`` sin el CUIT del receptor, eso es una
    incoherencia (E-EXT-2 textual); si simplemente no declaró el IVA de una ``B``,
    **no se juzga** — la ausencia ya viaja en ``campos_ausentes`` y castigarla
    premiaría al que inventa datos. La coherencia es ``dudosa``, nunca
    ``invalida``: una fuente inconsistente aporta un indicio contradictorio, y
    quien decide si sirve es la combinación (T-404).
19. **La descripción sigue sin evaluarse** (T-403): es una frase que resume los
    ítems, no un texto que se copie del documento, así que ni la contención ni un
    sostenedor tienen sentido. Es la única entrada que queda en
    ``CAMPOS_SOSTEN_NO_EVALUADO``.
20. **Las reglas de conflicto que necesitan la condición fiscal quedan afuera**
    (alcance de T-403): "C no admite emisor Responsable Inscripto" (prompt 11.1)
    requiere un dato que la extracción **no lee** (lo resuelve el padrón, F5).
    Declararla con el CUIT como sustituto sería inventar una inferencia.
21. **La precedencia es un orden, no una regla del motor** (T-404): la tabla vive
    en `rules/precedencia.py` como `PrecedenciaCampo` declarativo (con `regla` y
    `motivo`), no como `Rule` del registro. Una `Rule` es una condición booleana
    sobre un contexto; esto es un **orden entre fuentes** que se aplica al
    resolver un campo. Meterlo en el motor habría forzado una `Rule` por campo y
    por fuente.
22. **Las fuentes que no son lectura van siempre por delante** (hallazgo de
    implementación de T-404): el orden **no** puede ser una lista plana por campo
    — si lo fuera, declarar "gana el visual" dejaría a `programa`/`arca`/`hitl`
    detrás de las lecturas. La tabla decide el orden **entre lecturas**
    (`orden_lecturas()`) y el orden total antepone siempre las no-lectura en su
    orden de autoridad (`hitl` > `arca` > `programa`). Un dato corregido por una
    persona o comprobado contra el padrón no se discute con una lectura.
23. **Combinar no es decidir** (T-404): `CombinedEvidence.decision` pasa a ser
    **opcional**. El contrato de F0 exige que la certeza se derive de la etapa que
    decidió (glosario §2), y la combinación no decide: la decisión es de
    F5/T-501. Antes de T-404 el campo era obligatorio, así que la combinación
    habría tenido que **inventar** un veredicto — exactamente lo que el glosario
    prohíbe. Es un cambio **aditivo/compatible** (default `None`), no un bump de
    `SCHEMA_VERSION`.
24. **La resolución respeta la pasada 1** (T-404): una fuente que T-403 dejó
    invalidada (`valida=False`) **no gana** un campo que otra fuente sí resolvió
    —aunque ese campo priorice su tipo de lectura— y la resolución lo deja
    escrito. Si **ninguna** declarante es utilizable, gana igual la de mayor
    precedencia (no se inventa un valor) pero la resolución marca
    `confiable=False`: el dato queda como indicio.
25. **La combinación no descarta nada** (T-404): el `CampoCombinado` conserva la
    lectura de **todas** las fuentes con su sostén; la que pierde queda
    registrada en la resolución ("descartadas") pero no se borra. Es lo que
    permite auditar un caso y lo que F5 necesita para las reglas cruzadas.

## 3. Alcance por tarea (T-401..T-405)

### 3.1 T-401 · Flujo VLM y flujo LLM en paralelo devolviendo `SourceEvidence` ✅ Hecha

> **Estado 2026-09-10**: **Hecha** por `team implementation`. Suite completa en
> verde (**730 passed, 10 skipped**, 75 nuevos); `python scripts/F4/t401.py`
> reporta **11/11** escenarios y el paralelismo real verificado. Ver bitácora en
> [`F4.md`](F4.md) §4.

- **Qué**: implementar los dos flujos de extracción —VLM sobre la **vista fiel**
  de F2 (E-QWE-2), LLM sobre el **OCR/Markdown** de F1— corriendo **en paralelo**
  y devolviendo cada uno `SourceEvidence` con el contrato de evidencia de F0
  (ADR-001), conservando **ambas** evidencias sin colapsar (ADR-002 → T-404). ✅
- **Archivos**:
  - `extraction/prompt_extraccion.py` — prompt de **evidencia** versionado
    `extraccion-key-value@1`: base común (declara que el modelo **no** normaliza
    ni decide, conserva la corrección O/0 de v1 y la prohibición de inventar) +
    guía por fuente (`vlm` describe lo que ve; `llm` copia el texto OCR literal)
    + **lista de campos** + esquema JSON del contrato (`campos` con `valor` y
    `fragmento_sustento`) y `construir_messages_extraccion()` (reutiliza la
    reducción + base64 de F2). ✅
  - `extraction/evidencia.py` — `CampoLectura`/`EvidenciaExtraccion`
    (lectura cruda, sin normalizar), `Lector` (protocolo inyectable),
    `parsear_evidencia_extraccion()` (tolera el shape por campo y el **plano de
    v1**, no inventa campos, registra los campos sin sustento), la reutilización
    de la pasada raw (`campo_declarado_de_campo`,
    `veredicto_raw_de_evidencia`), `construir_source_evidence()` (contrato de F0
    con `meta.version_prompt`) y la orquestación **paralela**
    (`ejecutar_flujo`, `extraer_evidencia`) con `ErrorEvidencia`/
    `ErrorExtraccion`. ✅
  - `extraction/flows.py` — las firmas congeladas de F0 implementadas
    (`flujo_vlm`, `flujo_llm`, `extraer`); `combinar_evidencia` sigue siendo el
    esqueleto de T-404. ✅
  - `extraction/__init__.py` — exporta el contrato completo (flujos, lectura,
    prompt, errores, constantes). ✅
- **Contrato de evidencia (ADR-001)**: un `EvidenceField` por campo declarado,
  con `campo`, `valor` (normalizado por T-402; el crudo va en `meta`), `fuente`
  (`vlm`/`llm`), `fragmento_sustento`
  (texto OCR o descripción visual; si falta, se declara explícitamente la
  ausencia: el contrato exige fragmento no vacío) y `meta` con `modelo`,
  `version_prompt` = `extraccion-key-value@1`, `valor_crudo`, `vocabulario_campo`,
  `raw_evaluado`/`raw_valida`/`raw_gravedad`, `valor_coercionado_a_texto` y la
  trazabilidad de la normalización (`normalizado`, `regla_normalizacion`,
  `avisos_normalizacion`, `derivado_de` — agregada en T-402).
- **Paralelismo**: `ThreadPoolExecutor` con una tarea por fuente con insumo;
  `max_workers=1` serializa (tests y entornos acotados). El resultado se **ordena
  por el orden pedido** (`vlm`, `llm`) y no por el de finalización de los hilos:
  el paralelismo no puede cambiar el resultado. La medición (`t401.py`) y el test
  de tiempos lo verifican.
- **Tests** (`tests/test_extraction_flujos.py`, 75) ✅: contrato y versión del
  prompt (que no normaliza ni decide; campos de decisión fuera del contrato);
  `messages` por fuente (imagen base64 / markdown en `content`); interpretación
  (sin normalizar valores, sin inventar, shapes de v1, JSON con cercas/prosa,
  errores de contrato); conversión a `SourceEvidence` (un `EvidenceField` por
  campo, serializable, sin duplicar debilidades); pasada raw reutilizada
  (vocabulario, sostén, formato volátil); **paralelismo** (corren las dos, no se
  colapsan, el orden es determinista, el tiempo total ≈ el máximo, `max_workers=1`
  serializa); tolerancia a fallos (una fuente cae / respuesta inválida / todas
  caen); flujos públicos y el esqueleto de T-404.
- **Herramienta de inspección** ✅: `scripts/F4/t401.py` — imprime el prompt
  (`--prompt`), corre **11 escenarios** sintéticos (dos fuentes coincidiendo,
  discrepancia conservando ambas, campo sin sustento, valor fuera de vocabulario,
  CUIT no sostenido, shape plano de v1, montos no evaluados, sin vista, JSON
  inválido aislado, fuente caída aislada, todas caídas) con ✅/❌ y salida no-cero,
  **mide el paralelismo** (paralelo vs. serial con demora artificial) y con
  `--origen` corre la extracción **real** (F1 + vista fiel de F2 + Ollama).
- **Evolución posterior (ya resuelta)**: la normalización de los valores la
  aporta `key_value.py` (T-402) y `ejecutar_flujo` la aplica por default; las
  reglas raw afinadas para extracción son T-403; la combinación por campo con
  precedencia ADR-002 es T-404 (`combinar_evidencia` **ya está implementada**, no
  lanza `NotImplementedError`) y la paridad con v1 sobre el golden set es T-405.
  Las cuatro están **Hechas**. En T-401 `combinar_evidencia` sí era un esqueleto:
  eso es lo que describe el resto de esta sección.

### 3.2 T-402 · Normalización key-value (E-EXT-3) ✅ Hecha

> **Estado 2026-09-10**: **Hecha** por `team implementation`. Suite completa en
> verde (**827 passed, 10 skipped**, 97 nuevos); `python scripts/F4/t402.py`
> reporta **19/19** casos de regla + **17/17** escenarios + **5/5** fronteras.
> Ver bitácora en [`F4.md`](F4.md) §4.

- **Qué**: normalizar los campos clave (CUIT, fechas, montos, ítems, número de
  comprobante, moneda, texto) **reutilizando las reglas de los prompts
  `10`/`11`/`kvi`/`kvg`** (mitiga R-02), en código determinístico y sin degradar
  la calidad (E-EXT-3). ✅
- **Archivos**:
  - `extraction/key_value.py` (nuevo) — el módulo completo: versión de reglas
    (`VERSION_NORMALIZACION`), las reglas por campo (`NORM_CUIT`, `NORM_FECHA`,
    `NORM_MONTO`, `NORM_COMPROBANTE`, `NORM_VOCABULARIO`, `NORM_MONEDA`,
    `NORM_TEXTO`, `NORM_ITEMS`), el mapa `REGLA_POR_CAMPO` (contrato + alias del
    modo genérico `kvg`), las funciones puras (`normalizar_cuit`,
    `normalizar_fecha`, `normalizar_monto`, `normalizar_moneda`,
    `separar_comprobante`, `parsear_items`, `normalizar_texto`,
    `normalizar_descripcion`, `normalizar_vocabulario`), los tipos del informe
    (`AvisoNormalizacion`, `CampoNormalizado`, `ItemExtraido`,
    `InformeNormalizacion`, `NormalizacionEvidencia`) y la orquestación
    (`normalizar_campo`, `normalizar_evidencia`, `valores_normalizados`). ✅
  - `extraction/evidencia.py` — `CampoLectura` ganó la trazabilidad de la
    normalización (`normalizado`, `regla_normalizacion`, `avisos_normalizacion`,
    `derivado_de` + propiedad `es_derivado`); `ResultadoFlujo` lleva
    `informe_normalizacion`; `ejecutar_flujo`/`extraer_evidencia` aceptan
    `normalizar` (default `True`) y el `detalle` publica `version_normalizacion`,
    `normalizado` y el resumen del informe por fuente (reglas, campos
    normalizados/no normalizados, derivados y avisos). `construir_source_evidence`
    copia esa trazabilidad a la `meta` del `EvidenceField` (junto al
    `valor_crudo`). El import de `key_value` es **diferido** (`_normalizar` y
    `version_normalizacion`), porque `key_value` importa los tipos de este módulo:
    así `evidencia` sigue siendo importable solo. ✅
  - `extraction/flows.py` — `flujo_vlm`/`flujo_llm`/`extraer` exponen
    `normalizar: bool = True` (el default entrega la forma canónica de E-EXT-3).
    ✅
  - `extraction/__init__.py` — exporta el contrato completo de la normalización.
    ✅
- **Reglas portadas de v1** (con el número de regla del prompt de origen):
  - **CUIT** (regla 2b de `11` y regla de IDs de `kvg`): solo dígitos y los
    guiones propios; se arranca en el **primer dígito** (tolera la etiqueta
    pegada) y se corta ante cualquier carácter que no sea dígito o guión **entre
    dígitos**, aunque quede incompleto. `"20-1 Ing, Brutas: 201641"` → `"20-1"`.
    Aviso cuando no quedan los 11 dígitos (el OCR truncó o pegó el campo).
  - **Fechas** (`kvg`): a `YYYY-MM-DD` solo si son **completas y reales**;
    reconoce ISO (con/sin hora), `DD/MM/YYYY`, `DD-MM-YYYY`, `YYYY.MM.DD`,
    compacta `14082025` y escrita (`14 de agosto de 2025`). Sin día/mes completo,
    con año de dos dígitos o inexistente **no se normaliza** (no se inventa).
  - **Montos** (`kvg`): número plano, `.` decimal, sin miles ni símbolo; signo
    delante/detrás/paréntesis; el separador único con 3 dígitos detrás se marca
    **ambiguo** y se resuelve con la convención argentina dejando constancia. El
    `0` es un valor, no una ausencia.
  - **Comprobante** (regla 3 de `11` + regla de `kvg`): el número impreso se
    conserva **tal cual** (con su guión) y `PPPPP-NNNNNNNN` se separa en
    `punto_venta`/`numero_comprobante` como campos **derivados**. Sin la forma
    esperada no se separa (aviso informativo, no debilidad).
  - **Moneda** (regla 14 de `11`): `USD`/`U$S`/`Dólares` → `USD`,
    `$`/`ARS`/`pesos` → `ARS`. **Sin** el default `ARS` de v1.
  - **Texto** (reglas 1/8 de `11`): espacios colapsados y `descripcion` en
    minúsculas. La corrección O/0 sigue en el prompt (es corrección de lectura).
  - **Ítems** (`kvg`): `"descripción xcantidad - precio_unitario"` separados por
    `" | "`, o lista/objetos; se estructura lo reconocible y lo que falta queda
    en `None` (no se inventa cantidad ni precio).
- **Regla dura de E-EXT-3 ("no inventar")**: un valor ilegible **conserva el
  crudo** con un `AvisoNormalizacion` (nunca se publica un canónico inventado);
  un dato **ausente** sigue ausente (T-401 lo reporta en `campos_ausentes`).
- **Fronteras verificadas**: la pasada raw de T-303 sigue evaluando el **crudo**
  (es lo que le permite reportar *qué se leyó*); `normalizar=False` devuelve la
  lectura cruda de T-401; `combinar_evidencia` sigue siendo el esqueleto de T-404.
- **Tests** (`tests/test_extraction_key_value.py`, 97) ✅: CUIT (corte, etiqueta
  pegada, guiones, sin dígitos), fechas (nueve formas, año corto, incompletas,
  inexistentes, bisiesto), montos (separadores, signos, ambigüedad, `0`, no
  numéricos), comprobante (separación, ceros a la izquierda, sin formato,
  derivados), moneda (sin default), texto/descripción, ítems, mapa de reglas vs.
  contrato del prompt, informe (reglas, normalizados/no normalizados, avisos,
  derivados, inmutabilidad) e integración con el flujo y el contrato de F0.
- **Herramienta de inspección** ✅: `scripts/F4/t402.py` — imprime las **reglas
  caso por caso** (`--reglas --detalle`), corre **17 escenarios** con ✅/❌ y
  salida no-cero, verifica **5 fronteras** de la tarea y con `--origen` corre la
  extracción real y muestra la comparación `crudo → normalizado` por fuente.
- **Evolución posterior (ya resuelta)**: la afinación de las reglas raw por
  fuente era **T-403** — **hecha** (ver §3.3): los campos de formato volátil ya
  no solo tienen forma canónica, sino que su sostén **se evalúa** contra ella. La
  combinación por campo (**T-404**) y la paridad con v1 (**T-405**) también están
  hechas.

### 3.3 T-403 · Reglas raw por fuente (pasada 1) ✅ Hecha

> **Estado 2026-09-10**: **Hecha** por `team implementation`. Suite completa en
> verde (**865 passed, 10 skipped**, 37 nuevos); `python scripts/F4/t403.py`
> reporta **11/11** criterios de sostén + **7/7** escenarios de coherencia +
> **4/4** fronteras. Ver bitácora en [`F4.md`](F4.md) §4.

- **Qué**: completar la **pasada 1** por fuente para la extracción —validar cada
  fuente **antes** de combinarla (E-EXT-2)— sobre los dos huecos que T-401 había
  declarado explícitamente: el sostén de los campos de formato volátil y la
  coherencia de la fuente consigo misma. ✅
- **Archivos**:
  - `rules/raw.py` (ampliado, **sin** tocar el registro ni el motor de F0):
    `ImplicacionCoherencia` (implicaciones que la fuente debe cumplir consigo
    misma: ``disparador``/``requeridos``/``incompatibles``/``pendientes``),
    `CampoDeclarado.normalizador_valor` (forma canónica del valor, solo para el
    sostén) y `CampoDeclarado.sostenedor` (predicado
    ``(valor_canónico, fragmento) -> bool``, **autoridad** del sostén cuando el
    formato importa), `violaciones_de_coherencia()` y el id `RAW_COHERENCIA`
    (con ``VeredictoRaw.incoherencias``). ✅
  - `extraction/evidencia.py` — `CAMPOS_SOSTEN_ESTRUCTURADO` (montos y fechas,
    que T-401 dejaba sin evaluar), `CAMPOS_SOSTEN_NO_EVALUADO` reducido a la
    ``descripcion``, los sostenedores de dominio (`_sostiene_monto`,
    `_sostiene_fecha`, `_sostiene_identificador`), el normalizador canónico de
    los CUIT, `COHERENCIA_POR_CAMPO` (las implicaciones de E-EXT-2 sobre la letra)
    y la traza (`sosten_forma_canonica`, `incoherencias`, `sosten_estructurado`
    en la meta del campo). ✅
  - `extraction/key_value.py` — `candidatos_numericos`, `montos_en_texto`
    (importes con la convención contable del signo) y `fechas_en_texto`: el apoyo
    que los sostenedores necesitan, con las **mismas** funciones de normalización
    que publica el valor (así el sostén y el valor no pueden divergir). ✅
  - `extraction/__init__.py` — exporta el contrato nuevo. ✅
- **Qué se evalúa ahora** (y qué se sigue dejando afuera), por campo:

  | Campo | Criterio de sostén | Desde |
  |---|---|---|
  | `tipo_comprobante`, `moneda` | vocabulario cerrado (T-303) | F3 |
  | CUIT (emisor/receptor) | secuencias de dígitos (``30/12345678/9`` sostiene ``30-12345678-9``) | T-403 |
  | razones sociales, `nro_comprobante` | contención del valor en el fragmento (T-303) | F3 |
  | montos (`subtotal`, `iva`, total…) | importes: números con la convención contable del signo | T-403 |
  | `fecha_emision` | fechas normalizadas a ISO (sostiene cualquiera de las del período) | T-403 |
  | `descripcion` | **no se evalúa**: frase sintética, no un texto que se copie | T-401 |

- **Coherencia de la fuente (E-EXT-2)**, declarada en `COHERENCIA_POR_CAMPO`:
  - **Factura A sin los dos CUIT** → la fuente queda debilitada (el caso textual
    de la historia: "dice Factura A pero no detectó los dos CUIT que esa letra
    exige").
  - **Factura B con IVA discriminado** → la fuente queda debilitada (regla 5 del
    prompt `11` de v1 / prompt `11.1`).
  - **No se juzga** lo que la fuente no pudo evaluar (una ``B`` sin IVA declarado,
    una ``C`` sin datos tributarios): la ausencia ya viaja en `campos_ausentes` y
    castigarla premiaría al que inventa datos.
  - Gravedad **`dudosa`**, nunca `invalida`: la fuente inconsistente aporta un
    indicio contradictorio, y quien decide si sirve es la combinación (T-404).
- **Tests** (`tests/test_extraction_raw_t403.py`, 37) ✅: sostén de montos (con
  separadores, signos y paréntesis), fechas (incluida la ventana de un período),
  identificadores (con y sin separadores, y con otro CUIT en el fragmento),
  coherencia en sus cuatro combinaciones, trazabilidad (`RAW_COHERENCIA`,
  `incoherencias`, meta del campo) e integración con el flujo; más las fronteras.
- **Herramienta de inspección** ✅: `scripts/F4/t403.py` — imprime los **criterios
  de sostén** caso por caso (`--sosten --detalle`), corre **7 escenarios de
  coherencia** con ✅/❌ y salida no-cero, verifica **4 fronteras** y con
  `--origen` corre la extracción real y muestra, por fuente, el veredicto raw.
- **Evolución posterior (ya resuelta)**: la combinación por campo es **T-404**
  (hecha; la coherencia es una de sus entradas: deja a la fuente debilitada) y la
  paridad con v1 es **T-405** (hecha). Las reglas de conflicto que dependen de la
  **condición fiscal** del emisor quedan para el padrón/F5 (la extracción no la
  lee).

### 3.4 T-404 · Combinación de evidencia con resolución por campo ✅ Hecha

> **Estado 2026-09-10**: **Hecha** por `team implementation`. Suite completa en
> verde (**910 passed, 10 skipped**, 45 nuevos); `python scripts/F4/t404.py`
> reporta **8/8** escenarios de resolución + **4/4** fronteras, y `--manual` corre
> el pipeline F4 completo (T-401→T-402→T-403→T-404). Ver bitácora en
> [`F4.md`](F4.md) §4.

- **Qué**: combinar la evidencia de las fuentes **conservando todas las lecturas**
  y resolver campo a campo según la precedencia de **ADR-002**, dejando la regla y
  el motivo en la resolución (auditoría E-CONC-5). ✅
- **Archivos**:
  - `rules/precedencia.py` (nuevo) — `TABLA_PRECEDENCIA` (`PrecedenciaCampo` por
    campo: `regla` `PREC_1`..`PREC_3` + `motivo` legible),
    `resolver_campo()` (acuerdo / desacuerdo / una sola fuente / ninguna /
    ganadora invalidada / ninguna utilizable), `combinar()` (arma los
    `CampoCombinado`) y `resumen_combinacion()` (traza de la corrida). ✅
  - `extraction/flows.py` — `combinar_evidencia()` **implementada** con la firma
    congelada de F0: `CombinedEvidence` con la resolución por campo, el atajo
    `valor`/`fuente` y la trazabilidad (`VERSION_COMBINACION`). ✅
  - `schemas/evidence.py` — `CampoCombinado.valor`/`fuente` (aditivos: el valor
    vigente y su responsable) y `CombinedEvidence.decision` **opcional**
    (combinar no es decidir). ✅
  - `rules/raw.py` — `VeredictoRaw.reglas_por_campo` (qué regla tocó a qué campo,
    para que la resolución pueda explicar **con qué** quedó débil la fuente que no
    gana). ✅
  - `rules/__init__.py` y `extraction/__init__.py` — exportes. ✅
- **La tabla por campo** (regla de oro: *lo comprobado manda para descartar; el
  papel manda para detectar*):

  | Regla | Campos | Por qué |
  |---|---|---|
  | **PREC_1** visual | `tipo_comprobante`, `nro_comprobante`, `razon_social_emisor`, `cuit_emisor`, `cuit_receptor` | el recuadro del encabezado, el número impreso, el membrete y los identificadores son lo que la lectura visual ve como bloque; el OCR los corta o los pega al campo siguiente (regla 2b de v1) |
  | **PREC_2** textual | `fecha_emision`, `moneda`, `razon_social_receptor`, `descripcion` y **todos los importes** | la fecha y la moneda se leen junto a su etiqueta impresa, y el OCR conserva los dígitos y separadores que la visión puede confundir de columna |
  | **PREC_3** programa | `punto_venta`, `numero_comprobante` | los deriva T-402 del número impreso: no se leen |
  | **PREC_0** regla de oro | cualquier campo fuera de la tabla (modo genérico `kvg`) | lo comprobado manda sobre lo leído y, entre lecturas, el papel (visual sobre textual) |

  Además, las fuentes que **no son lectura** van siempre por delante, en orden de
  autoridad: `hitl` (corrección humana) > `arca` (padrón) > `programa` (calculado).
- **Los cinco casos de la resolución**:
  1. **Acuerdo** (mismo valor canónico): gana la de mayor precedencia y se
     registra como acuerdo — el consumidor tiene **una** fuente responsable.
  2. **Desacuerdo**: gana la de mayor precedencia **utilizable**; las otras
     quedan en `descartadas` con el valor que declararon.
  3. **Una sola fuente** declaró el campo: gana esa (sin desacuerdo).
  4. **Ninguna** declaró: no se inventa un valor; queda para el gate de F5.
  5. **Ganadora invalidada** por la pasada 1: cede a la otra fuente; si **ninguna**
     es utilizable, gana por precedencia pero `confiable=False`.
- **Determinismo**: la resolución no depende del orden de las fuentes ni del
  orden de las claves del JSON (los flujos corren en paralelo, T-401); los campos
  se resuelven en el orden del contrato del prompt y los extra al final en orden
  alfabético.
- **Tests** (`tests/test_extraction_combinacion_t404.py`, 45) ✅: la tabla
  (cobertura del contrato, regla y motivo por campo, orden de lecturas, regla de
  oro, no-lectura por delante), `resolver_campo` (los cinco casos + determinismo),
  `combinar` (conserva lecturas, universo/orden de campos, acuerdos vs.
  desacuerdos, errores de tipo), el contrato de F0 (`valor`/`fuente`,
  `decision is None`, traza, `documento_id` vacío) y la integración con el
  pipeline real (letra que discrepa, valores que T-402 volvió comparables, fuente
  invalidada por T-403, derivados por programa, serialización).
- **Herramienta de inspección** ✅: `scripts/F4/t404.py` — imprime la **tabla de
  precedencia** (`--tabla`), corre **8 escenarios de resolución** con ✅/❌ y
  salida no-cero, verifica **4 fronteras**, y con `--manual`/`--origen` muestra,
  campo por campo, quién ganó y por qué.
### 3.5 T-405 · Paridad con v1 y cierre de la fase ✅ Hecha

**Qué se hizo.** Se verificó que lo que F4 hace **en código** reproduce las
reglas que v1 tenía **dentro de los prompts** (10/11 y kvi/kvg), y que la
extracción v2 es **comparable** con la de v1 sobre el golden set. La paridad se
mide en **tres niveles**, no en uno, porque comparar la salida de dos modelos no
prueba nada por sí solo (ADR-001: el prompt reporta, el código decide).

**Archivos.**

- `tests/golden/F4/subconjunto.json` — subconjunto de paridad `0.1-f4`:
  - `normalizacion`: **7 reglas** (`NORM_CUIT`, `NORM_FECHA`, `NORM_MONTO`,
    `NORM_COMPROBANTE`, `NORM_VOCABULARIO`, `NORM_MONEDA`, `NORM_TEXTO`), cada
    una citando la `regla_v1`, el `prompt_v1` de origen y el `texto_v1` literal;
  - `no_inventar`: las fronteras de "no inventar" y el destino del valor crudo;
  - `campos_paridad`: los **15** campos del contrato con contraparte en v1
    (el contrato tiene 16: `razon_social_receptor` **no** lo pedía el prompt de
    factura de v1, así que se declara en `campos_sin_contraparte_v1` — no hay
    valor de v1 contra el que comparar);
  - `campos_paridad_generico`: `proveedor`, `monto`, `productos`, `cuit_emisor`,
    `fecha` (modo `kvg`);
  - `campos_sin_contraparte_v1`: los campos que v2 lee pero v1 no pedía
    (`razon_social_receptor`), declarados con motivo para que "no comparable" no
    se lea como "no implementado";
  - `campos_fuera_de_paridad`: **9** campos de decisión, con su motivo y el ADR
    que los excluye (no son "extracción", son "conclusión": F5);
  - `extraccion`: **6 casos** (`factura_a_completa`, `cuit_truncado_por_ocr`,
    `tique_090_fecha_escrita`, `factura_b_no_discrimina`, `modo_generico_kvg`
    con su clave `campos`, `valores_no_normalizables`);
  - `real`: **3** documentos del golden para la corrida contra v1.
- `tests/golden/F4/README.md` — por qué paridad ≠ "el mismo modelo dos veces",
  los tres niveles de medición, la tabla de diferencias esperadas por diseño y
  los límites honestos del alcance.
- `scripts/F4/paridad_extraccion.py` — funciones puras y testables:
  `campos_desde_v2()` (proyección a la forma plana de v1; omite campos ausentes),
  `comparar()` → `coincide`/`difiere`/`no_comparable` + nota, `_equivalentes()`
  (tolerancia número↔texto **solo** en números; dobla mayúsculas en
  `tipo_comprobante`), `notas_de_proyeccion()`, `preparar_v1()`, `correr_v1()`,
  `campos_desde_v1()`, `correr_v2()`. `NOTAS_ESPERADAS` explica las diferencias
  por diseño.
- `tests/test_extraction_paridad.py` (**39** tests, 6 clases):
  `TestProcedenciaDeLasReglas`, `TestParidadNormalizacion`, `TestNoInventar`,
  `TestIntegridadSubconjunto`, `TestParidadExtraccion`,
  `TestLogicaDeComparacion`. Carga el script por `importlib` (patrón de T-305)
  **sin** ejecutar `main`. La integridad del subconjunto incluye un guard que
  exige que **todo** campo del contrato esté declarado (en paridad, fuera de
  paridad o sin contraparte en v1): sin él, `razon_social_receptor` quedaba en
  tierra de nadie y la paridad parecía cubrir 16 campos cuando cubría 15.
- `scripts/F4/t405.py` — reporte de métricas (patrón `scripts/F3/t305.py`):
  `metrica_reglas()`, `metrica_extraccion()` (paridad + sostén),
  `metrica_generico()` (usa `regla_de_campo()` — no `REGLA_POR_CAMPO` — para que
  resuelvan los alias), `metrica_norm_vs_contrato()`, `correr_origen()`.
  `--subset determinista` (default, sin red) / `origen`. Sale con código
  distinto de cero si fallan reglas, sostén o procedencia.

**Cómo se mide (los tres niveles).**

1. **Nivel 1 — normalización (determinista, sin modelos).** Cada regla v2 se
   aplica al `texto_v1` literal del subconjunto y debe producir el mismo valor
   canónico; y cada regla cita su origen (`regla_v1` + `prompt_v1` + `texto_v1`).
   Es la paridad **fuerte**: no depende de que el modelo devuelva lo mismo.
2. **Nivel 2 — contrato y sostén (determinista, sin modelos).** Los 15 campos
   de paridad existen en el contrato F0 y el sostén estructurado se evalúa donde
   corresponde (montos, fechas, CUIT); `descripcion` queda en
   `sosten_no_evaluado` (un texto libre no se sostiene por forma canónica).
3. **Nivel 3 — extracción real (con red, opcional).** Se corre v1 y v2 sobre los
   3 documentos del golden y se comparan **campo por campo** con `comparar()`.
   Este nivel es **informativo**: si el modelo devuelve otra cosa, `difiere` no
   significa que v2 esté mal (para eso están los niveles 1 y 2 y los tests).

**Diferencias esperadas por diseño** (NO son fallos de paridad):

| Campo | v1 | v2 | Por qué es correcto |
|---|---|---|---|
| `tipo_comprobante` | códigos del vocabulario `090`/`099` según el prompt | normalizado a la letra (`A`/`B`/`C`) | ADR-001: el código decide, el prompt solo reporta lo que ve |
| `moneda` | a veces asume `ARS` sin decirlo | solo `ARS` si está impreso; si no, queda crudo + aviso | no inventar (T-402): un default silencioso es una invención |
| `descripcion` | texto tal cual del modelo | normalizado por código (espacios OCR colapsados) | regla en código, no en prompt (ADR-006) |

**Métricas (`scripts/F4/t405.py --subset determinista`).**

| Métrica | Resultado |
|---|---|
| Reglas de normalización con procedencia v1 verificada | **20/20 (100 %)** |
| Paridad estructural de campos (contrato ∪ genéricos) | **29/29 (100 %)** |
| Sostén estructurado evaluado donde corresponde | **32/32 (100 %)** |
| Cobertura del modo genérico (`kvg`) | **5/5 (100 %)** |
| Procedencia verificada | **sí** |

Suite completa tras T-405: **949 passed / 10 skipped**.

**Alcance honesto.** La paridad se prueba sobre el **subconjunto** documentado y
sobre **3** documentos del golden, no sobre todo el corpus. El nivel 3 requiere
Ollama y `v1/` presentes, así que la suite default **no** lo corre (queda en
`--origen`). v1 y v2 no comparten el mismo prompt ni el mismo modelo: lo que se
compara es **el resultado de las reglas**, no la salida cruda del LLM.

**Hallazgo real.** La paridad destapó un hueco: `proveedor` (clave obligatoria de
`kvg`) **no tenía** regla de normalización, así que `"Estación  de  Servicio  SUR"`
conservaba los espacios múltiples del OCR. Se agregó `"proveedor": NORM_TEXTO` a
`REGLA_POR_CAMPO` y `CAMPOS_GENERICOS_CON_REGLA = ("productos", "proveedor")`.
Sin la paridad, el hueco no se habría visto: los tests de T-402/T-403 solo
cubrían los campos del contrato de factura.

## 4. Reglas duras (no romper F0/F2/F3)

- No cambiar el **contrato de evidencia** de F0 (`schemas/evidence.py`,
  `SCHEMA_VERSION = 1.0.0`): si hiciera falta un cambio incompatible, bump mayor +
  revisión de F3/F4/F5 (criterio documentado en el propio módulo).
- Las firmas de `flujo_vlm`/`flujo_llm`/`combinar_evidencia` siguen existiendo
  (los tests de esqueleto de F0 las congelan). Se les agregó el parámetro
  **obligatorio** `lector` (protocolo inyectable, mismo criterio que F3-subplan
  §2.6): el esqueleto no tenía forma de llamar al modelo y la suite default no
  puede depender de Ollama real.
- `combinar_evidencia` **no** se implementa hasta T-404 (precedencia ADR-002).
- La suite default corre **sin** Ollama real y **sin** Docling real (doble del
  lector + `VistaPreparada` inyectada); las corridas reales van en
  `--origen`/`@pytest.mark.integration`.
- No se agregan dependencias nuevas (el paralelismo usa `concurrent.futures` de
  la stdlib).
- No tocar `processing`/`validation`: el camino documento→markdown es de F1 y
  documento→vista (fiel) es de F2; la extracción recibe esos artefactos.
- Estilo: docstrings y mensajes en español citando los docs (doc 03 §4.4, IDs de
  épica/tarea); asserts con mensaje explicativo.

## 5. Flujo de la extracción (referencia de implementación)

```text
funcion extraer_evidencia(lector, markdown, vista, normalizar=True):
    con_insumo, sin_insumo = fuentes_con_insumo(markdown, vista)   # E-EXT-1
    resolver modelo/num_ctx por rol (vlm/llm) desde Settings       # E-LIB-3
    en paralelo, una tarea por fuente con insumo:
        mensajes = construir_messages_extraccion(fuente, markdown, vista)
        respuesta = lector.ask(mensajes, model, json_format=True, num_ctx)
        evidencia = parsear_evidencia_extraccion(respuesta.contenido, fuente)
        si normalizar:                                                # T-402
            evidencia, informe = normalizar_evidencia(evidencia)      # E-EXT-3
        veredicto = veredicto_raw_de_evidencia(evidencia)           # T-303 + T-403
            # RAW_CAMPO/VOCABULARIO/SUSTENTO/CONTRADICCION (T-303)
            #   + sostén por forma canónica (montos, fechas, CUIT)
            #   + RAW_COHERENCIA: implicaciones de la fuente (E-EXT-2)
        source    = construir_source_evidence(evidencia, veredicto) # ADR-001
    si todas las fuentes con insumo fallaron: error de dominio      # ErrorExtraccion
    combinada = combinar_evidencia(documento_id, evidencias)        # T-404/ADR-002
        # conserva todas las lecturas; resuelve campo a campo (valor + fuente)
    retornar evidencias por fuente (orden pedido) + detalle         # sin colapsar
```

- El `detalle` de la corrida publica: fuentes pedidas/corridas/sin insumo,
  fallos por fuente, si hubo paralelismo y el `max_workers`, por fuente el
  modelo/`num_ctx`/`valida`/`gravedad`/`reglas_raw`/cantidad de campos/
  `sosten_no_evaluado`/`sosten_forma_canonica`/`incoherencias`/duración/
  `normalizacion` (reglas aplicadas, campos normalizados y no normalizados,
  derivados y avisos), la versión del prompt, la versión de las reglas de
  normalización y la nota de alcance.
- La **combinación** de las dos evidencias (precedencia por campo) es T-404 y
  **ya está implementada**: conserva todas las lecturas, resuelve el desacuerdo
  con la tabla de precedencia y deja `decision` en `None` (concluir es F5). Lo que
  esta fase entrega a F5 es la evidencia combinada con cada campo **resuelto**
  (valor + fuente responsable) y la traza de por qué ganó esa fuente.

## 6. Archivos creados/modificados

### T-401
- `src/voucherflow/extraction/prompt_extraccion.py` (nuevo).
- `src/voucherflow/extraction/evidencia.py` (nuevo).
- `src/voucherflow/extraction/flows.py` (reescrito: de esqueleto a implementación).
- `src/voucherflow/extraction/__init__.py` (ampliado).
- `tests/test_extraction_flujos.py` (nuevo, 75 tests).
- `scripts/F4/t401.py` (nuevo).

### T-402
- `src/voucherflow/extraction/key_value.py` (nuevo: reglas, informe y orquestación de la normalización).
- `src/voucherflow/extraction/evidencia.py` (trazabilidad de normalización en `CampoLectura`/`meta`, `normalizar` en `ejecutar_flujo`/`extraer_evidencia` y detalle de la corrida).
- `src/voucherflow/extraction/flows.py` (`normalizar` en los flujos públicos).
- `src/voucherflow/extraction/__init__.py` (superficie de la normalización).
- `tests/test_extraction_key_value.py` (nuevo, 97 tests).
- `scripts/F4/t402.py` (nuevo).

### T-403
- `src/voucherflow/rules/raw.py` (ampliado: `ImplicacionCoherencia`, `sostenedor`, `normalizador_valor`, `violaciones_de_coherencia`, `RAW_COHERENCIA`).
- `src/voucherflow/extraction/evidencia.py` (`CAMPOS_SOSTEN_ESTRUCTURADO`, sostenedores de dominio, `COHERENCIA_POR_CAMPO`, traza).
- `src/voucherflow/extraction/key_value.py` (`candidatos_numericos`, `montos_en_texto`, `fechas_en_texto`).
- `src/voucherflow/extraction/__init__.py` (exportes).
- `tests/test_extraction_raw_t403.py` (nuevo, 37 tests).
- `scripts/F4/t403.py` (nuevo).
- Ajustes de expectativas en `tests/test_extraction_flujos.py` y `scripts/F4/t401.py` (el comportamiento de T-403 reemplaza el "sin evaluar" que T-401 congelaba).

### T-404
- `src/voucherflow/rules/precedencia.py` (nuevo, con la tabla y la resolución).
- `src/voucherflow/extraction/flows.py` (`combinar_evidencia()` implementada + `VERSION_COMBINACION`).
- `src/voucherflow/schemas/evidence.py` (`CampoCombinado.valor`/`fuente` aditivos; `CombinedEvidence.decision` opcional).
- `src/voucherflow/rules/raw.py` (`VeredictoRaw.reglas_por_campo`).
- `src/voucherflow/rules/__init__.py` y `src/voucherflow/extraction/__init__.py` (exportes).
- `tests/test_extraction_combinacion_t404.py` (nuevo, 45 tests).
- `scripts/F4/t404.py` (nuevo).
- Ajustes de expectativas en `tests/test_extraction_flujos.py`, `tests/test_extraction_key_value.py` y `tests/test_extraction_raw_t403.py` (los tests que congelaban el esqueleto de `combinar_evidencia`).

### T-405
- `tests/golden/F4/subconjunto.json` (nuevo: subconjunto de paridad `0.1-f4` con reglas, procedencia, no-inventar, campos de paridad y 6 casos de extracción).
- `tests/golden/F4/README.md` (nuevo: los tres niveles de paridad, diferencias esperadas y límites).
- `scripts/F4/paridad_extraccion.py` (nuevo: proyección, comparación y runners de v1/v2).
- `tests/test_extraction_paridad.py` (nuevo, 37 tests).
- `scripts/F4/t405.py` (nuevo: métricas de la paridad).
- `src/voucherflow/extraction/key_value.py` (`"proveedor": NORM_TEXTO` en `REGLA_POR_CAMPO` y `CAMPOS_GENERICOS_CON_REGLA` — hueco que destapó la paridad).

## 7. Avance

- **Estado (2026-09-10)**: **F4 cerrada**. Las cinco tareas están **Hechas**:
  **T-401** (los dos flujos en paralelo devolviendo `SourceEvidence`, con prompt
  de evidencia versionado y tolerancia a fallos por fuente), **T-402**
  (normalización key-value de E-EXT-3 portada a código desde los prompts
  `10`/`11`/`kvi`/`kvg`, con el crudo siempre preservado), **T-403** (pasada 1
  completa: sostén por forma canónica de montos/fechas/CUIT y coherencia de la
  fuente consigo misma), **T-404** (combinación con resolución por campo, la
  tabla de precedencia de ADR-002) y **T-405** (paridad con v1 sobre el golden
  set, en tres niveles, con las diferencias por diseño documentadas). Suite en
  verde: **949 passed, 10 skipped** (75 de T-401 + 97 de T-402 + 37 de T-403 +
  45 de T-404 + 39 de T-405 sobre una base de 656). Los cinco scripts de
  inspección (`t401`..`t405`) salen con código 0. `F4.md` queda en ✅
  **Completada** y **el DoD de F4 está verificado** (ver §7.1).
- **Punto de partida real**: el contrato de evidencia de F0 está congelado y la
  vista fiel de F2 (`preparar_vista_fiel`) y el markdown de F1
  (`api.process`) están disponibles, así que T-401 pudo implementarse sin tocar
  las fases previas (los flujos reciben esos artefactos); T-402 normaliza esa
  lectura, T-403 la califica y T-404 la combina, también sin volver a tocar
  F1/F2.

### 7.1 DoD de F4 — verificación

El DoD de la fase (copia literal en `05-plan/F4.md` §2) tiene **un** criterio:

> Dos flujos siempre en paralelo con evidencia trazable; paridad de extracción
> con v1 en campos normalizados sobre golden set.

| Parte del criterio | Cómo se verificó | Evidencia |
|---|---|---|
| **Dos flujos siempre en paralelo** | `extraer_evidencia()` corre una tarea por fuente con `ThreadPoolExecutor`; el detalle publica si hubo paralelismo y el `max_workers` | T-401: `scripts/F4/t401.py` mide **0.206 s** en paralelo vs. **0.412 s** en serie (dos llamadas de 0,2 s) |
| **Evidencia trazable** | Cada campo lleva `fragmento_sustento`, `sosten_estructurado`, `normalizado`/`regla_normalizacion`, `derivado_de` y el veredicto raw por fuente; la `CombinedEvidence` conserva **todas** las lecturas y deja `decision = None` | T-401..T-404: tests de `test_extraction_flujos.py`, `test_extraction_raw_t403.py`, `test_extraction_combinacion_t404.py` |
| **Paridad en campos normalizados sobre golden set** | Subconjunto `tests/golden/F4/`; la paridad se mide en 3 niveles (normalización / contrato-sostén / extracción real) | T-405: `scripts/F4/t405.py --subset determinista` → reglas **20/20**, paridad estructural **29/29**, sostén **32/32**, genérico **5/5**, procedencia ✅ |
| **Alcance admitido del DoD** | "paridad **o mejora**", documentada caso por caso; los campos de **decisión** quedan fuera por ADR-001 | `tests/golden/F4/subconjunto.json` → `campos_fuera_de_paridad` (9 campos con motivo) y `tests/golden/F4/README.md` |

**Límites declarados (lo que el DoD *no* cubre).**

- La corrida **real** contra v1 no está en la suite default: requiere Ollama y
  `v1/` presentes, así que vive en `--subset origen` / `@pytest.mark.integration`.
- La medición es sobre el **subconjunto** documentado y **3** documentos del
  golden, no sobre el golden completo (la curación con contador de montos/fechas
  sigue pendiente de F2 §2.5).
- Los campos de **decisión** (`comprobante_valido`, `categoria_gasto`,
  `centro_de_costo`…) no se comparan: son el objetivo del rediseño, no una
  regresión de la extracción (ADR-001).

**Resultado**: DoD de F4 **verificado**. La fase queda ✅ **Completada**.

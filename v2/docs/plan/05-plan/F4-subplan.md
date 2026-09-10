# F4-subplan — Subplan de implementación F4 (Extracción)

> Documento de trabajo para la implementación de la **Fase F4** por
> `team implementation`. Complementa el seguimiento de la fase
> ([`F4.md`](F4.md)) y el diseño del módulo
> ([`../03-arquitectura/EXT.md`](../03-arquitectura/EXT.md)).
> **Fecha**: 2026-09-10 · **Rama**: `v2` · **Estado**: En implementación
> (T-401 hecha; T-402..T-405 pendientes).

## 1. Ficha del subplan

| Campo | Valor |
|---|---|
| **Fase** | F4 — Extracción (refactor de la extracción de v1) |
| **Tareas que cubre** | T-401, T-402, T-403, T-404, T-405 (ver [`F4.md`](F4.md)) |
| **Épicas asociadas** | E-EXT (E-EXT-1 flujos en paralelo, E-EXT-2 reglas raw, E-EXT-3 campos normalizados) |
| **Módulos** | `voucherflow/extraction/` (`flows.py`, `evidencia.py`, `prompt_extraccion.py`) |
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
   `extraccion-key-value@1`.
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
  con `campo`, `valor` (crudo), `fuente` (`vlm`/`llm`), `fragmento_sustento`
  (texto OCR o descripción visual; si falta, se declara explícitamente la
  ausencia: el contrato exige fragmento no vacío) y `meta` con `modelo`,
  `version_prompt` = `extraccion-key-value@1`, `valor_crudo`, `vocabulario_campo`,
  `raw_evaluado`/`raw_valida`/`raw_gravedad` y `valor_coercionado_a_texto`.
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
- **Pendiente de otras tareas**: la normalización de los valores es **T-402**; las
  reglas raw por fuente afinadas para extracción son **T-403**; la combinación por
  campo con precedencia ADR-002 es **T-404** (`combinar_evidencia` sigue en
  `NotImplementedError`); la paridad con v1 sobre el golden set es **T-405**.

### 3.2 T-402..T-405 · Pendientes

> Se documentarán al implementarse. Alcance según
> [`F4.md`](F4.md) §3 y el DoD de F4 en `05-plan-ejecucion.md`.

| Tarea | Alcance | Archivos previstos |
|---|---|---|
| **T-402** | Normalización key-value (CUIT solo dígitos y guiones propios, fechas `YYYY-MM-DD`, montos numéricos sin separadores, `PPPPP-NNNNNNNN` → `punto_venta`/`numero_comprobante`, ítems), reutilizando las reglas de los prompts `10`/`11`/`kvi`/`kvg` (mitiga R-02) | `extraction/key_value.py` (nuevo, doc 03 §11) + integración en `evidencia.py`/`flows.py` |
| **T-403** | Reglas raw por fuente para **todos** los campos extraídos (incluidos los de formato volátil que T-401 dejó explícitamente sin evaluar), con trazabilidad | `rules/raw.py` (ampliado) + `extraction/evidencia.py` |
| **T-404** | Combinación de evidencia con resolución **por campo** y precedencia ADR-002 (`CampoCombinado`/`FieldResolution`), sobre las dos `SourceEvidence` que T-401 ya entrega | `rules/precedencia.py` (nuevo) + `extraction/flows.py` (`combinar_evidencia`) |
| **T-405** | Paridad con `extraction_pipeline.py` (10/11) y `document_extraction.py` (kvi/kvg) sobre el golden set; subconjunto de paridad y runners | `tests/golden/F4/` + `scripts/F4/paridad_*.py` |

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
funcion extraer_evidencia(lector, markdown, vista):
    con_insumo, sin_insumo = fuentes_con_insumo(markdown, vista)   # E-EXT-1
    resolver modelo/num_ctx por rol (vlm/llm) desde Settings       # E-LIB-3
    en paralelo, una tarea por fuente con insumo:
        mensajes = construir_messages_extraccion(fuente, markdown, vista)
        respuesta = lector.ask(mensajes, model, json_format=True, num_ctx)
        evidencia = parsear_evidencia_extraccion(respuesta.contenido, fuente)
        veredicto = veredicto_raw_de_evidencia(evidencia)           # reusa T-303
        source    = construir_source_evidence(evidencia, veredicto) # ADR-001
    si todas las fuentes con insumo fallaron: error de dominio      # ErrorExtraccion
    retornar evidencias por fuente (orden pedido) + detalle         # sin colapsar
```

- El `detalle` de la corrida publica: fuentes pedidas/corridas/sin insumo,
  fallos por fuente, si hubo paralelismo y el `max_workers`, por fuente el
  modelo/`num_ctx`/`valida`/`gravedad`/`reglas_raw`/cantidad de campos/
  `sosten_no_evaluado`/duración, la versión del prompt y la nota de alcance.
- La **combinación** de las dos evidencias (precedencia por campo) es T-404 y no
  ocurre acá: T-401 entrega las dos `SourceEvidence` completas.

## 6. Archivos creados/modificados en T-401

- `src/voucherflow/extraction/prompt_extraccion.py` (nuevo).
- `src/voucherflow/extraction/evidencia.py` (nuevo).
- `src/voucherflow/extraction/flows.py` (reescrito: de esqueleto a implementación).
- `src/voucherflow/extraction/__init__.py` (ampliado).
- `tests/test_extraction_flujos.py` (nuevo, 75 tests).
- `scripts/F4/t401.py` (nuevo).

## 7. Avance

- **Estado (2026-09-10)**: F4 en implementación. **T-401: Hecha** (los dos flujos
  en paralelo devolviendo `SourceEvidence`, con prompt de evidencia versionado,
  pasada raw reutilizada de T-303 y tolerancia a fallos por fuente). Suite en
  verde: **730 passed, 10 skipped** — 75 tests de T-401 sobre una base de 655.
  `F4.md` pasa de 🔴 Backlog a 🟡 En implementación.
- **Punto de partida real**: el contrato de evidencia de F0 está congelado y la
  vista fiel de F2 (`preparar_vista_fiel`) y el markdown de F1
  (`api.process`) están disponibles, así que T-401 pudo implementarse sin tocar
  las fases previas (los flujos reciben esos artefactos).

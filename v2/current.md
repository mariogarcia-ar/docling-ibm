# v2 — Estado actual y qué se puede probar (fase F4)

> **Documento**: estado vivo de la versión 2 de `ibm-docling`.
> **Fecha**: 2026-09-10 · **Rama**: `v2`
> **Fuentes**: `v2/README.md`, `v2/docs/plan/05-plan/F0.md`..`F4.md` y subplanes, `v2/src/voucherflow/`

---

## 1. Fase actual

| Campo | Valor |
|---|---|
| **Fase en curso** | **F4 — Extracción** (T-401..T-405) · ✅ **fase completada**; próxima: F5 |
| **Estado** | **T-401..T-405 ✅ hechas — F4 cerrada con el DoD verificado**: los flujos VLM y LLM corren **en paralelo** devolviendo `SourceEvidence` con el contrato de F0; prompt de evidencia versionado `extraccion-key-value@1`, intérprete que no inventa, paralelismo medido, **normalización key-value** (CUIT cortado, fecha ISO, montos numéricos, `punto_venta`/`numero_comprobante` derivados) con el crudo preservado, **pasada 1 por fuente** (sostén por forma canónica + coherencia interna), **combinación con resolución por campo** (tabla de precedencia ADR-002: visual/textual/programa, conservando todas las lecturas) y **paridad con v1 medida en tres niveles** (reglas 20/20, campos 29/29, sostén 32/32). |
| **F0** | ✅ Fundación completada (schemas, esqueleto, golden set, adaptadores) |
| **F1** | ✅ Implementada (T-101..T-105/ORQ, `api.process()`; paridad de integración en `@pytest.mark.integration`) |
| **F2** | ✅ DoD verificado (T-201..T-204; doble paso qween) |
| **F3** | ✅ DoD verificado (T-301..T-305; motor de reglas R1-R7, evidencia, reglas raw, cadena contable y paridad con v1) |
| **F4** | ✅ DoD verificado (T-401..T-405; flujos en paralelo, normalización, pasada 1, combinación por campo y paridad con v1) |
| **F5–F6** | 🔴 Backlog (conclusión + HITL; CLI/batch y paridad sobre `files/`) |
| **Paquete** | `voucherflow` v`0.1.0` (layout `src/`, ADR-007) |
| **Contrato** | `SCHEMA_VERSION = 1.0.0` (congelado, ver criterio de cambio en `schemas/evidence.py`) |
| **Suite de tests** | ✅ **949 tests en verde + 10 skipped** (`python -m pytest --no-header -p no:cacheprovider`) en env `py313_env` |

**Resumen**: F0 dejó la **fundación de la librería**: contratos de evidencia
congelados, configuración centralizada, adaptadores `OllamaClient`/
`DoclingConverter`, base del motor de reglas, el **golden set inicial** y el
esqueleto de las 5 capacidades (F1–F5). Sobre esa base, **F1** (en curso)
refactoriza Docling en `voucherflow/processing/`: están implementados el
**detector de tipo de entrada** (T-101), el **clasificador de imagen + gate de
procesabilidad** (T-102), la **orientación/preprocesamiento** (T-103), el
**motor OCR/VLM + exportador ordenado por posición** (T-104), el **enrutado de
PDF por página** (`routing.py`), la **orquestación `procesar_documento()` +
`api.process()`** (T-105/ORQ, con flag `docling_raw` que expone el crudo de
Docling — equiv. `v1/run_raw.py`) y la **extracción de PDF apto con
`pdftotext --layout`** (`processing/pdftotext.py`; recupera columnas que
Docling aplana, con fallback a Docling si no hay poppler — decisión 2026-09-07
que revierte A1), todo heurístico liviano, **sin dependencias Python nuevas**
(poppler es un utilitario de sistema) y cubierto por tests. Ya **hay pipeline
funcional de extremo a extremo** de procesamiento (`api.process`); resta la
paridad de integración T-105 y los docs de cierre de F1.

---

## 2. Qué hay hasta el momento (F0)

### 2.1 Implementado y probado (F0)

| Módulo | Archivo | Qué ofrece / se puede probar |
|---|---|---|
| **Contrato de evidencia** | `src/voucherflow/schemas/evidence.py` | Modelos pydantic congelados: `EvidenceField`, `SourceEvidence`, `FieldResolution`, `CampoCombinado`, `Decision`, `CombinedEvidence` + enums `Fuente`/`Certeza`/`Origen`/`EstadoResultado`/`TipoComprobante` y helper `nueva_meta`. |
| **Contrato de resultado/trazabilidad** | `src/voucherflow/schemas/result.py` | `VoucherResult`, `CaseRecord`, `RegistroEtapa`, `CampoExtraido`, `ClasificacionContable`, `HitlDecision` (base de la trazabilidad ADR-005). |
| **Configuración centralizada** | `src/voucherflow/settings/config.py` | `Settings` con defaults → YAML → env `VOUCHERFLOW_*`. |
| **Cliente Ollama robusto** | `src/voucherflow/models/ollama.py` | `OllamaClient` con retry/backoff ante 429/502/503/504, timeout y diagnóstico (E-LIB-3). Probado con mocks HTTP. |
| **Adaptador Docling** | `src/voucherflow/models/docling.py` | `DoclingConverter` + contrato `ProcessedDocument` (markdown + `Box` + metadatos) y `EXTENSIONES_SOPORTADAS`. |
| **Base del motor de reglas** | `src/voucherflow/rules/registry.py` | `Rule` declarativa + `Registry` (evaluación determinista ordenada por prioridad, trazable por `id`). Las reglas de negocio R1-R7 se migran en F3. |
| **Helper de sidecar** | `src/voucherflow/trace/recorder.py` | `sidecar_para()` (nomenclatura canónica `<doc>.case.json`); el `CaseRecorder` se implementa en F5. |
| **Golden set inicial** | `v2/tests/golden/` | `casos.csv` con **9 casos** reales (jpg/pdf/jpeg/png) referenciados en `v2/tests/fixtures/`, splits `train/eval`, README con criterio (T-004). |

### 2.2 Esqueletos (contratos de F0, lógica en F1–F5)

Estos módulos existen con su **firma pública y contratos** pero **no tienen
lógica de negocio**: sus funciones lanzan `NotImplementedError` hasta su fase.
F1–F4 ya implementaron las capacidades de las filas marcadas; las de F5 siguen
pendientes.

| Capacidad (fase) | Módulo | Contratos ya definidos |
|---|---|---|
| Procesamiento (F1) | `processing/type_detector.py` | `TipoEntrada` (tipo, `ruta_ocr`) |
| Validación (F2) | `validation/qween.py` | `VeredictoGate` (comprobante/no/indeterminado), `ValidationResult` |
| Clasificación (F3) | `classification/tipo_comprobante.py` | `TipoComprobanteResult`, `ClasificacionContableResult` |
| Extracción (F4) | `extraction/flows.py` | `flujo_vlm()`, `flujo_llm()`, `extraer()` (**implementados en T-401**); `combinar_evidencia()` (**implementada en T-404**) |
| Conclusión (F5) | `conclusion/engine.py` | `concluir()`, `escalar_a_agente()`, `encolar_hitl()` |
| Conclusión (F5) | `models/arca.py` | `ArcaClient`, `ArcaResultado` (opcional, ADR-003) |
| Trazabilidad (F5) | `trace/recorder.py` | `CaseRecorder` |
| Fachada / orquestador | `api.py`, `orchestrator.py` | Esqueleto de la API pública (`VoucherResult`) y `PipelineResult` |

---

## 2.3 Avance de F1 (en curso)

| Tarea | Módulo | Qué ofrece / se puede probar | Tests |
|---|---|---|---|
| **T-101** (✅) | `processing/type_detector.py` | `detectar() -> TipoEntrada` (pdf_texto/pdf_escaneado/imagen/office/texto/no_soportado) con distinción pdf_texto/pdf_escaneado por capa real de texto (PyMuPDF). | `test_processing_type_detector.py` (29) |
| **T-102** (✅) | `processing/image_classifier.py` | `ClaseImagen` (foto/escaneo_plano/screenshot/manuscrito), `clasificar()`, gate `verificar_procesabilidad() -> VeredictoGate` y hook `sospechar_manuscrito()`. Lee dimensiones JPEG/PNG/BMP/TIFF con stdlib (sin Pillow/OpenCV). | `test_processing_image_classifier.py` (27) |
| **T-103** (✅) | `processing/preprocessing.py` + `orientation.py` | Orientación por boxes (horizontal/vertical dominante) + preprocesamiento heurístico (`QualityReport`/`evaluar_calidad`), sin CV. | `test_processing_orientation.py` (19) |
| **T-104** (✅) | `processing/ocr.py` + `markdown_exporter.py` | Motor OCR/VLM (`elegir_motor`: ocr/vlm/auto) + hook `transcribir_vlm` (sin llamar a Ollama en F1) y exportador ordenado por posición (portado de `v1/lib/orientation.py`, paridad byte-compatible; tablas Markdown como ítem único). | `test_processing_exportador_motor.py` (24) |
| **PDF apto** (✅) | `processing/pdftotext.py` | Extracción de PDF apto con `pdftotext --layout` (poppler) preferido + fallback a Docling directo (A1 revertida 2026-09-07). La orquestación usa motor `pdftotext` (layout de columnas, caso `9dfc597f`); `office`/`texto` y `docling_raw` siguen con Docling. | `test_processing_pdftotext.py` (7) |
| **Apoyo orq.** (✅) | `processing/routing.py` | Enrutado de PDF por página (apta_layout/escaneada/corrupta/vacía) y veredicto por PDF (apto/requiere_ocr/parcial) para la orquestación. | `test_processing_routing.py` (9) |
| **Inspección** | `scripts/inspeccionar_t102.py` | Aplica T-102 sobre fixtures (o carpeta CLI) y muestra resumen/detalle por clase y gate. | — |

> Detalle: sobre los 38 fixtures de imagen, T-102 clasifica 23 `escaneo_plano`,
> 13 `screenshot` y 2 `foto`, con **0 rechazos** en falso del gate.

> **Ruta texto nativo (2026-09-07)**: PDF apto se extrae con `pdftotext
> --layout` preferido (recupera columnas que Docling aplana, caso `9dfc597f`)
> con fallback a Docling directo si no hay poppler (decisión subplan F1 §2.6;
> revierte A1 de 2026-09-06).

> **Pendiente de F1**: T-105 (tests de integración de paridad contra v1,
> `@pytest.mark.integration`) y docs de cierre.

---

## 2.4 Avance de F4 (fase completada)

| Tarea | Módulo | Qué ofrece / se puede probar | Tests |
|---|---|---|---|
| **T-401** (✅) | `extraction/{flows,evidencia,prompt_extraccion}.py` | Los flujos **VLM** (vista fiel de F2) y **LLM** (OCR/Markdown de F1) corren **en paralelo** (`ThreadPoolExecutor`, una tarea por fuente) y devuelven cada uno `SourceEvidence` con el contrato de F0 (ADR-001). Prompt de evidencia versionado `extraccion-key-value@1` (reporta `valor` + `fragmento_sustento` por campo; no normaliza ni decide). El intérprete no inventa campos (los lista en `campos_ausentes`), tolera el JSON plano de v1 (`kvi`/`kvg`) y reutiliza la pasada raw de T-303. Una fuente caída no tumba a la otra; todas caídas lanzan `ErrorExtraccion`. Las dos evidencias se conservan **sin colapsar** (T-404). | `test_extraction_flujos.py` (75) |
| **T-402** (✅) | `extraction/key_value.py` | **Normalización key-value (E-EXT-3)** en código: CUIT a dígitos y guiones propios con corte ante caracteres extraños (`"20-1 Ing, Brutas: 201641"` → `"20-1"`), fechas `YYYY-MM-DD` solo si son completas y reales, montos numéricos sin separadores de miles (signo y constancia de ambigüedad), `punto_venta`/`numero_comprobante` derivados del número impreso, moneda `ARS`/`USD` sin default, texto colapsado, `descripcion` en minúsculas e ítems estructurados. **No inventa**: un dato ilegible conserva el crudo con aviso y el crudo de cada campo queda en `meta['valor_crudo']` (la pasada raw de T-303 sigue viendo el crudo). `normalizar=False` devuelve la lectura cruda de T-401. | `test_extraction_key_value.py` (97) |
| **T-403** (✅) | `rules/raw.py` + `extraction/evidencia.py` | **Pasada 1 por fuente (E-EXT-2)**: el sostén de montos, fechas y CUIT se evalúa contra la **forma canónica** (`12345.67` ← `"$ 12.345,67"`, `-1234.56` ← `"(1.234,56)"`, `2025-08-14` ← `"14/08/2025"`, `30123456789` ← `"30-12345678-9"`) y entra la **coherencia de la fuente consigo misma** (`RAW_COHERENCIA`): una Factura A sin los dos CUIT —o una B con IVA discriminado— queda **debilitada antes de combinarse**. La ausencia que no se puede juzgar no se castiga, la `descripcion` sigue sin evaluarse y el registro de T-303 queda intacto (los puntos de extensión los declara el llamador). | `test_extraction_raw_t403.py` (37) |
| **T-404** (✅) | `rules/precedencia.py` + `extraction/flows.py` | **Combinación con resolución por campo (ADR-002)**: la tabla declara qué lectura gana en cada campo —**visual** para la letra del recuadro, el número impreso, el membrete y los CUIT; **textual** para fecha, moneda e importes; **programa** para los derivados— más la regla de oro para el modo genérico. `combinar_evidencia()` conserva **todas** las lecturas, agrega la resolución (`ganador`/`regla`/`motivo`) y el atajo `valor`/`fuente`, y **respeta la pasada 1**: una fuente invalidada no gana un campo que la otra resolvió. Las fuentes no-lectura (`hitl` > `arca` > `programa`) van siempre por delante. `decision` viaja en `None`: combinar no es decidir (eso es F5). | `test_extraction_combinacion_t404.py` (45) |
| **T-405** (✅) | `tests/golden/F4/` + `scripts/F4/paridad_extraccion.py` | **Paridad con v1** (`extraction_pipeline.py` 10/11 y `document_extraction.py` kvi/kvg) sobre el subconjunto del golden, medida en **tres niveles**: (1) cada regla de normalización v2 aplicada al `texto_v1` literal produce el mismo canónico y **cita su origen** (`regla_v1` + `prompt_v1` + `texto_v1`); (2) los 15 campos con contraparte en v1 tienen paridad estructural y el sostén estructurado se evalúa donde corresponde; (3) corrida real contra v1 sobre 3 documentos (informativa, fuera de la suite default). Métricas: reglas **20/20**, campos **29/29**, sostén **32/32**, genérico **5/5**. Las diferencias por diseño (`tipo_comprobante` con `090`/`099`, `moneda` sin el default `ARS`, `descripcion`) van documentadas como nota. | `test_extraction_paridad.py` (39) |

> **Inspección de T-401**: `python scripts/F4/t401.py` corre **11/11** escenarios
> sintéticos (dos fuentes coincidiendo, discrepancia conservando ambas, campo sin
> sustento, valor fuera del vocabulario, CUIT no sostenido, JSON plano de v1,
> montos no evaluados por sostén, sin vista, JSON inválido aislado, fuente caída
> aislada, todas caídas) y **mide el paralelismo** (en paralelo ≈ el máximo de las
> dos llamadas, no la suma). Con `--origen <doc>` corre la extracción real
> (F1 + vista fiel de F2 + Ollama).
>
> **Decisión de alcance**: los campos de **formato estructurado** (montos, fechas,
> CUIT) no se evalúan por sostén literal (el OCR decide separadores y formato)
> pero **sí se evalúan**: desde T-403 se compara su **forma canónica** contra la
> del fragmento. Los que tienen sostenedor propio se listan en
> `detalle["modelos"][fuente]["sosten_forma_canonica"]`; solo la `descripcion`
> (frase sintética) queda en `sosten_no_evaluado`.
>
> **Inspección de T-402**: `python scripts/F4/t402.py` corre **19/19** casos de
> regla (el CUIT pegado al campo siguiente, nueve formas de fecha, montos con
> separadores/signos/ambigüedad, `PPPPP-NNNNNNNN`, moneda, texto, ítems) y
> **17/17** escenarios de la regla dura de E-EXT-3 (*no inventar*), más **5/5**
> fronteras de la tarea (el crudo sobrevive, `normalizar=False`, la pasada raw
> sigue viendo el crudo). Con
> `--reglas --detalle` imprime el catálogo de reglas caso por caso.
>
> **Decisión de alcance (T-402)**: **"no normalizable" no es "ausente"**. Un
> monto escrito con palabras o una fecha con año de dos dígitos son lecturas
> reales del documento: se **conserva el crudo con un aviso** (que llega a
> `SourceEvidence.debilidades` cuando es una limitación real) en lugar de
> descartar el dato. El **0** es un importe legítimo, no una ausencia.
>
> **Inspección de T-403**: `python scripts/F4/t403.py` corre **11/11** criterios
> de sostén (montos con separadores/signos/paréntesis, fechas —incluida la ventana
> de un período—, CUIT con y sin separadores y con otro CUIT en el fragmento),
> **7/7** escenarios de coherencia de la fuente (una `A` sin los dos CUIT, una `B`
> con IVA discriminado, y las contrapartes que **no** deben marcarse) y **4/4**
> fronteras. Con `--sosten --detalle` imprime el catálogo de criterios.
>
> **Decisión de alcance (T-403)**: la coherencia reporta la implicación que la
> fuente **sí pudo evaluar**. Que una `B` no haya declarado el IVA **no se juzga**
> (la ausencia ya viaja en `campos_ausentes`); castigarla premiaría al que inventa
> datos. La gravedad es `dudosa`, nunca `inválida`: decide la combinación (T-404).
>
> **Inspección de T-404**: `python scripts/F4/t404.py` imprime la **tabla de
> precedencia** y corre **8/8** escenarios de resolución (acuerdo, desacuerdo
> resuelto por precedencia en ambos sentidos, una sola fuente, ninguna, ganadora
> invalidada, ninguna utilizable, dato del programa) más **4/4** fronteras. Con
> `--manual` ejecuta el pipeline F4 completo con la lectura del modelo inyectada:
> es la forma de ver la resolución por campo **sin GPU**.
>
> **Decisión de alcance (T-404)**: la combinación **conserva todas las lecturas**
> (combinar no es descartar: la que pierde queda en el `CampoCombinado` con su
> sostén) y **no decide** el caso — `decision` viaja en `None` porque la certeza se
> deriva de la etapa que decidió (glosario §2) y esa etapa es F5/T-501. Y la
> resolución **respeta la pasada 1**: una fuente que T-403 invalidó no gana un
> campo que otra resolvió.
>
> **Inspección de T-405**: `python scripts/F4/t405.py --subset determinista`
> (default, **sin red**) reporta la paridad del subconjunto: reglas **20/20**,
> paridad estructural **29/29** campos, sostén **32/32**, cobertura del modo
> genérico **5/5** y procedencia verificada; sale con código ≠ 0 si fallan
> reglas, sostén o procedencia. `--subset origen` corre la paridad **real**
> contra v1 (requiere Ollama y `v1/` presentes).
>
> **Decisión de alcance (T-405)**: la paridad se mide en **tres niveles** porque
> comparar la salida cruda de dos modelos no prueba nada por sí solo (ADR-001: el
> prompt reporta, el código decide). El nivel determinista (reglas) y el de
> contrato/sostén son los que **sostienen** la afirmación de paridad; la corrida
> real contra v1 es **informativa** y por eso no entra en la suite default. Las
> diferencias por diseño viajan como nota, no como fallo. La paridad además
> **destapó un hueco real**: `proveedor` (clave obligatoria de `kvg`) no tenía
> regla de normalización y conservaba los espacios múltiples del OCR; se agregó
> `"proveedor": NORM_TEXTO`.
> **Lo que el DoD no cubre**: la medición es sobre el subconjunto y 3 documentos
> del golden, no el golden completo (la curación con contador de montos/fechas
> sigue pendiente de F2 §2.5), y los campos de **decisión**
> (`comprobante_valido`, `categoria_gasto`, `centro_de_costo`…) quedan fuera por
> ADR-001: son el objetivo del rediseño, no una regresión de la extracción.

---

## 3. Qué se puede probar en esta fase (F1)

### 3.1 Rápido — instalación e import

```bash
# Desde la raíz del repo (entorno py313_env)
python -m pip install -e v2/
python -c "import voucherflow; print(voucherflow.__version__, voucherflow.SCHEMA_VERSION)"
# → 0.1.0 1.0.0
```

### 3.2 Suite de tests (949 en verde + 10 skipped)
```bash
cd v2
python -m pytest tests -q
```

Cobertura de la suite por archivo (F0 + F1):

| Archivo de test | Qué valida |
|---|---|
| `test_schemas_evidence.py` | Contrato de evidencia: construcción/validación de `SourceEvidence`, `CombinedEvidence`, enums, helper `nueva_meta`, criterio de cambio. |
| `test_schemas_result.py` | `VoucherResult`, `CaseRecord`, `RegistroEtapa`, `HitlDecision` y serialización. |
| `test_models_ollama.py` | `OllamaClient` con **mock HTTP**: payload de chat, reintentos/backoff, timeouts, mensajes de error. |
| `test_models_docling.py` | Construcción del `DoclingConverter` y contrato `ProcessedDocument` (sin conversión real de archivos, marcada integración). |
| `test_settings_config.py` | `Settings`: defaults, override por env `VOUCHERFLOW_*`, precedencia de fuentes. |
| `test_golden_y_esqueleto.py` | Golden set (integridad de `casos.csv` vs. archivos en `fixtures/`, splits sin cruce) y esqueletos (firmas presentes, lanzan `NotImplementedError`). |
| `test_fixtures.py` | Integridad/consistencia de los fixtures del golden set. |
| `test_processing_type_detector.py` | **F1/T-101**: `detectar()` por extensión y por capa de texto pdf_texto/pdf_escaneado (PDFs sintéticos). |
| `test_processing_image_classifier.py` | **F1/T-102**: clasificador (foto/escaneo/screenshot por ratio/EXIF/RGBA), gate de procesabilidad y hook de manuscrito (PNG/JPEG sintéticos con stdlib). |
| `test_processing_orientation.py` | **F1/T-103**: orientación por boxes (horizontal/vertical), `requiere_rotacion` y preprocesamiento heurístico (`QualityReport`). |
| `test_processing_exportador_motor.py` | **F1/T-104**: exportador ordenado por posición (horizontal/vertical/tablas) + elección de motor ocr/vlm/auto + hook `transcribir_vlm` sin Ollama. |
| `test_processing_routing.py` | **Apoyo orquestación**: clasificación de página (apta/escaneada/corrupta/vacía) y veredicto por PDF (apto/requiere_ocr/parcial). |
| `test_rules_contexto.py` · `test_rules_tipo_comprobante.py` · `test_rules_raw.py` | **F3/T-301 y T-303**: contexto tipado, una clase por regla R1..R7 + combinaciones, y las cuatro reglas raw por fuente (`RAW_CAMPO`/`RAW_VOCABULARIO`/`RAW_SUSTENTO`/`RAW_CONTRADICCION`). |
| `test_classification_prompt_tipo.py` · `test_classification_contable.py` · `test_classification_paridad.py` | **F3/T-302, T-304 y T-305**: prompt de evidencia `tipo-comprobante@1` + lector inyectable; cadena contable 01→02→03 con checkpoints y contratos por paso; paridad con v1 (fidelidad de prompts portados, subconjunto del golden, regresión de R5). |
| `test_extraction_flujos.py` | **F4/T-401**: prompt de evidencia `extraccion-key-value@1`, `messages` por fuente, intérprete (sin normalizar ni inventar; tolera el JSON plano de v1), contrato `SourceEvidence`, pasada raw reutilizada de T-303 y **paralelismo real** de los dos flujos (incluye fallos por fuente). |
| `test_extraction_key_value.py` | **F4/T-402**: cada regla de normalización con los casos de v1 (CUIT cortado, nueve formas de fecha, montos con separadores/signos/ambigüedad, `PPPPP-NNNNNNNN` + derivados, moneda sin default, texto, `descripcion`, ítems), la regla dura de **no inventar** (crudo conservado con aviso), el informe de la corrida (reglas, normalizados/no normalizados, derivados, inmutabilidad) y la integración con el flujo y el contrato de F0. |
| `test_extraction_raw_t403.py` | **F4/T-403**: sostén por forma canónica (montos con signo y paréntesis, fechas incluida la ventana de un período, CUIT con y sin separadores), la **coherencia de la fuente consigo misma** (`RAW_COHERENCIA`: `A` sin los dos CUIT, `B` con IVA discriminado, y los casos que no se juzgan), la trazabilidad (`RAW_COHERENCIA` / `incoherencias` / `sosten_estructurado` en la meta) y las fronteras (descripción sin evaluar, registro de T-303 intacto, esqueleto de T-404). |
| `test_extraction_combinacion_t404.py` | **F4/T-404**: la tabla de precedencia (cobertura del contrato, regla y motivo por campo, orden de lecturas, regla de oro, fuentes no-lectura por delante), `resolver_campo` (acuerdo, desacuerdo en ambos sentidos, una sola fuente, ninguna, ganadora invalidada, ninguna utilizable, determinismo) y `combinar_evidencia` (conserva todas las lecturas, orden determinista de campos, `valor`/`fuente` por campo, `decision is None`, traza de la combinación, integración con el pipeline T-401→T-403). |
| `test_extraction_paridad.py` | **F4/T-405**: procedencia de las reglas (cada una cita `regla_v1` + `prompt_v1` + `texto_v1`), paridad de normalización contra el `texto_v1` literal, la regla de **no inventar**, integridad del subconjunto (cobertura del contrato ∪ campos genéricos, campos de decisión excluidos con motivo, y el guard de que **todo** campo del contrato esté declarado) y la **lógica de comparación** (`coincide`/`difiere`/`no_comparable`, tolerancia número↔texto solo en números). Carga el script de paridad por `importlib` **sin** ejecutar `main`, así que la suite default no toca la red. |

### 3.3 Probar el contrato de evidencia a mano (ejemplos)

```python
from voucherflow.schemas.evidence import (
    EvidenceField, SourceEvidence, CombinedEvidence, nueva_meta, Fuente,
)
from voucherflow.schemas.result import CaseRecord

meta = nueva_meta("doc-1", modelo="qwen2.5vl")
campo = EvidenceField(valor="FC A 0001-00000001", certeza="alta")
src = SourceEvidence(documento_id="doc-1", fuente=Fuente.vlm, campos={"nro_comprobante": campo}, meta=meta)
combinada = CombinedEvidence(documento_id="doc-1", campos={})
```

> ⚠️ La validación **rechaza** valores mal formados con errores de contrato
> claros (diseño E-LIB-2). Probá enviar un campo sin `valor`, una `certeza`
> inválida o un `CampoCombinado` sin fuente válida y verificá el mensaje.

### 3.4 Probar el motor de reglas base

```python
from voucherflow.rules.registry import Rule, Registry

reglas = Registry()
reglas.registrar(Rule(id="R1", prioridad=1, condicion=lambda ctx: ctx["monto"] > 0, resultado="A", detalle="demo"))
reglas.registrar(Rule(id="R2", prioridad=2, condicion=lambda ctx: "IVA" in ctx["texto"], resultado="B", detalle="demo"))
reglas.ids_disparados({"monto": 100, "texto": "tiene IVA"})  # ["R1", "R2"]
```

### 3.5 Verificar el golden set y sus fixtures

| Ítem | Dónde |
|---|---|
| Índice de casos (9) | `v2/tests/golden/casos.csv` |
| Splits train/eval | `v2/tests/golden/splits/` |
| Copias versionadas | `v2/tests/fixtures/{golden,chicos,grandes,otros}/` |
| Criterio de etiquetado | `v2/tests/golden/README.md` |

> ⚠️ **Importante**: las **etiquetas de negocio** (`letra`, `condicion_fiscal`,
> `veredicto`, `calidad`) están en `pendiente`. Solo el `tipo_entrada` está
> etiquetado (por extensión). El etiquetado completo requiere el OCR de F1/v1 o
> el criterio de contador (plan §3.4) — **pendiente de F0**.

### 3.6 Lo que NO se puede probar todavía

- ✅ Pipeline de extremo a extremo de un documento (orquestación `procesar_documento()` + `api.process()`; **hecho en F1**).
- 🟡 Paridad de integración T-105 contra v1 sobre fixtures reales (tests `@pytest.mark.integration`; **F1, pendiente de corrida real**).
- ✅ Gate "¿es comprobante?" estilo qween (**hecho en F2**, T-201..T-204).
- ✅ Clasificar tipo/letra y cadena contable (**F3** completa: T-301..T-305 — motor de reglas R1-R7, prompt de evidencia, reglas raw, cadena contable 01→02→03 y paridad verificada con v1: **8/8** en la cadena y **5/5** de exactitud de letra vs. **2/5** de v1).
- ✅ **Extracción VLM/LLM completa, con paridad medida (T-401 ✅, T-402 ✅, T-403 ✅, T-404 ✅, T-405 ✅)**: `python scripts/F4/t401.py` corre **11/11** escenarios sin Ollama, `t402.py` **19/19** + **17/17** + **5/5**, `t403.py` **11/11** + **7/7** + **4/4**, `t404.py` **8/8** + **4/4** (con `--manual` para ver la combinación sin GPU) y `t405.py` reporta la paridad (**reglas 20/20**, **campos 29/29**, **sostén 32/32**, **genérico 5/5**); con `--origen` los cinco corren la extracción real (F1 + vista fiel de F2 + Ollama). La paridad **real** contra v1 se corre con `t405.py --subset origen`.
- ❌ Conclusión reglas→agente→HITL + trazabilidad persistida (llega en **F5**).
- ❌ CLI/batch (`voucherflow …`) y paridad v1 sobre `files/` (llega en **F6**).

### 3.7 Comprobación rápida de T-301 (F3)

```python
from voucherflow.classification.tipo_comprobante import clasificar_tipo_comprobante
from voucherflow.rules.contexto import ContextoTipoComprobante

ctx = ContextoTipoComprobante(
    emisor_condicion_fiscal="Responsable Inscripto",
    receptor_condicion_fiscal="Responsable Inscripto",
    letra_recuadro_vlm="B",
)
res = clasificar_tipo_comprobante(ctx)
res.letra              # 'B' (default preferencia_letra="documento")
res.reglas_aplicadas   # ['R2A', 'R4', 'R7']
res.alertas            # [{'regla': 'R7', ...}] comprobante inválido para crédito fiscal
```

```bash
python scripts/F3/t301.py            # 14 escenarios R1..R7 contra la expectativa
python -m pytest tests/test_rules_tipo_comprobante.py -q
```

### 3.8 Comprobación rápida de F4 (T-401..T-405)

```bash
python scripts/F4/t401.py                      # 11 escenarios + paralelismo medido
python scripts/F4/t401.py --prompt             # el prompt de evidencia versionado
python scripts/F4/t402.py                      # 19 casos de regla + 17 escenarios + 5 fronteras
python scripts/F4/t402.py --reglas --detalle   # el catálogo de reglas, caso por caso
python scripts/F4/t403.py                      # 11 criterios de sostén + 7 escenarios + 4 fronteras
python scripts/F4/t404.py                      # tabla de precedencia + 8 escenarios + 4 fronteras
python scripts/F4/t404.py --manual --detalle   # combinación del pipeline real (sin GPU)
python scripts/F4/t405.py                      # paridad: reglas 20/20 · campos 29/29 · sostén 32/32
python -m pytest tests/test_extraction_flujos.py tests/test_extraction_key_value.py \
    tests/test_extraction_raw_t403.py tests/test_extraction_combinacion_t404.py \
    tests/test_extraction_paridad.py -q
```

```python
from voucherflow.extraction import extraer
from voucherflow.models.ollama import OllamaClient
from voucherflow.validation.vistas import preparar_vista_fiel
from voucherflow import api

documento = api.process("comprobante.pdf")
vista = preparar_vista_fiel(documento, "comprobante.pdf")   # vista fiel (E-QWE-2)
resultado = extraer(
    OllamaClient(),
    markdown=documento.markdown,
    vista=vista,
    documento_id="doc-1",
)
resultado.evidencias_por_fuente()   # {'vlm': SourceEvidence, 'llm': SourceEvidence}
resultado.debilidades               # ['[llm] La evidencia de la fuente es internamente ...']
resultado.detalle["fuentes_sin_insumo"]
resultado.detalle["modelos"]["llm"]["incoherencias"]   # T-403

# T-404: combinar las dos evidencias resolviendo campo a campo (ADR-002)
from voucherflow.extraction import combinar_evidencia
combinada = combinar_evidencia("doc-1", resultado.evidencias)
combinada.campos["tipo_comprobante"].valor, combinada.campos["tipo_comprobante"].fuente
combinada.campos["tipo_comprobante"].resolucion.regla    # 'PREC_1' (visual)
combinada.decision                                        # None: decide F5
```

> Los valores que publica el `SourceEvidence` están **normalizados** (T-402: CUIT
> cortado, fecha ISO, montos numéricos) y el valor crudo de cada campo viaja en
> `meta['valor_crudo']`; `extraer(..., normalizar=False)` devuelve la lectura cruda
> de T-401. Cada fuente viene además **calificada por sí sola** (T-403: sostén por
> forma canónica y coherencia interna, E-EXT-2). La **combinación** (T-404) conserva
> todas las lecturas y agrega, por campo, el valor vigente, la fuente responsable y
> la resolución (`ganador`/`regla`/`motivo`); `decision` queda en `None` porque
> concluir es F5.

Las funciones de esqueleto de fases futuras (`run`, `concluir`,
`escalar_a_agente`, `encolar_hitl`, `CaseRecorder.registrar`) lanzan
`NotImplementedError` a propósito. Ya **no** son
esqueletos `process` (F1), `validate` (F2), `classify` (F3),
`flujo_vlm`/`flujo_llm`/`extract` (F4/T-401), la normalización key-value
(F4/T-402), la pasada 1 por fuente (F4/T-403), la combinación por campo
(F4/T-404) ni la paridad con v1 (F4/T-405). **F4 queda cerrada** con su DoD
verificado; lo próximo es **F5 — Conclusión + HITL** (T-501..T-507).

---

## 4. Pendientes de F0 (para cerrar la fase)

- [ ] Curar **etiquetas de negocio** del golden set (`letra`, `condición
      fiscal`, `veredicto`) con el contador (T-004 / plan §3.4 y §4).
- [ ] Reportar **métricas de la fase** y dejar sin deuda técnica bloqueante
      para F1 (DoD transversal de calidad — `docs/plan/06-estrategia-calidad.md`).
- [x] F0 en revisión en `docs/plan/05-plan/F0.md` y F1 arrancada
      (`docs/plan/05-plan/F1.md`, T-101..T-105 en curso).

---

## 5. Referencias

- Documentación de arquitectura/plan: `v2/docs/plan/` (00-glosario, 03-arquitectura, 04-ADRs, 05-plan, 06-calidad).
- README del paquete: `v2/README.md`.
- Seguimiento por fase: `v2/docs/plan/05-plan/F0.md` … `F6.md`.

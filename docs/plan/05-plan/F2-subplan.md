# F2-subplan — Subplan de implementación F2 (Validación / refactor qween)

> Documento de trabajo para la implementación de la **Fase F2** por
> `team implementation`. Complementa el seguimiento de la fase
> ([`F2.md`](F2.md)) y el diseño del módulo
> ([`../03-arquitectura/VAL.md`](../03-arquitectura/VAL.md)).
> **Fecha**: 2026-09-08 · **Rama**: `v2` · **Estado**: En planificación (borrador previo a T-201).

## 1. Ficha del subplan

| Campo | Valor |
|---|---|
| **Fase** | F2 — Validación (refactor qween) |
| **Tareas que cubre** | T-201, T-202, T-203, T-204 (ver [`F2.md`](F2.md)) |
| **Épicas asociadas** | E-QWE (E-QWE-1, E-QWE-2) |
| **Módulo** | `voucherflow/validation/` |
| **Responsable** | team implementation |
| **Decisiones de alcance** | Cerradas con el negocio/equipo (ver §2) |
| **DoD de referencia** | "El gate clasifica comprobante/no/indeterminado sobre el golden set con métrica acordada; los no-comprobantes no llegan a extracción" (DoD de F2 en `05-plan-ejecucion.md`) |

## 2. Decisiones de alcance cerradas (2026-09-08)

1. **F2 no porta legacy directo**: qween **no existe** como script en `v1/` ni
   como prompt en `prompts/` (a diferencia de F1 con `run.py`/`run_raw.py`).
   La única fuente es la idea [`v2/docs/ideas/qween.md`](../../ideas/qween.md) y el
   diseño doc 03 §4.2/§5.2 (`VAL.md`). Por lo tanto **no hay paridad v1 que
   verificar**: el DoD de F2 se valida contra el golden set etiquetado y contra
   la semántica del doble paso (ahorro de costo + calidad distinta), no contra
   un `.md` de referencia.
2. **Vistas = imágenes derivadas (no re-procesamiento Docling)**: las tres
   vistas (`rapida`/`revision`/`fiel`) se preparan **sobre la representación
   procesada de F1** (`ProcessedDocument` + ruta de imagen/PDF), sin volver a
   correr Docling. `preparar_vista_rapida` genera un thumbnail de calidad
   baja/moderada; `preparar_vista_revision` sube a calidad media para
   indeterminados; `preparar_vista_fiel` entrega calidad alta con orientación
   corregida y limpieza leve, **sin reutilizar la vista rápida** (principio de
   doble calidad, E-QWE-2). Si el documento de entrada es un PDF escaneado o
   imagen, la vista fiel se deriva de la imagen renderizada de F1; si es un PDF
   con texto nativo, la "vista" para el gate es la representación procesada
   (markdown+boxes) y no aplica thumbnail (decisión §2.4).
3. **El gate llama a Ollama en F2 (VLM) con contrato de evidencia** — a
   diferencia de F1 (que dejó `transcribir_vlm` como hook sin llamar):
   T-202 implementa `decidir_es_comprobante` **sobre el `OllamaClient` de F0
   (T-005)**, con un prompt corto de 3 salidas
   (`comprobante | no_comprobante | indeterminado`, texto del prompt portado de
   la idea qween §1). La decisión reporta `SourceEvidence` (ADR-001): el
   fragmento de sustento es el texto/markdown (o, si el VLM devuelve solo la
   etiqueta, una nota de la vista usada + confianza). **Regla dura**: en la
   suite default los tests usan **doble del cliente** (`FakeOllamaClient`) y el
   Ollama real queda en `@pytest.mark.integration` (mismo criterio que F1 §4).
4. **Resolución de la ambigüedad `VeredictoGate` (processing vs. validation)**:
   existe `processing.image_classifier.VeredictoGate` (gate de procesabilidad
   `procesable: bool` de F1/T-102) y el esqueleto de F2
   `validation.qween.VeredictoGate` (decisión `comprobante/no_comprobante/
   indeterminado` de E-QWE-1). **Decisión**: son contratos distintos y **no se
   fusionan**; el `validation.VeredictoGate` es el del gate qween (semántica de
   E-QWE) y el de `processing` queda para el gate de imagen. Se documenta en
   `VAL.md` y en los docstrings para evitar confusión (mismo nombre, dominios
   distintos: procesabilidad de imagen vs. es comprobante).
5. **Golden set: etiquetar `veredicto` para F2 (subconjunto acotado)** — el
   `casos.csv` de F0 tiene `veredicto=pendiente` (etiquetas de negocio pendían
   de contador, F0 §4). F2 **no espera** a la curación masiva: se etiqueta un
   **subconjunto acotado y representativo** de casos (los 9 del golden +
   no-comprobantes explícitos tomados de `fixtures/otros/` o sintéticos) con
   columna `veredicto ∈ {comprobante, no_comprobante, indeterminado}` para
   validar T-202/T-204. La curación completa con contador sigue pendiente para
   fases posteriores (F3/F4/F5); F2 deja el criterio y el subset documentados en
   `tests/golden/README.md`.
6. **Métrica acordada para el DoD de F2** (cierra el DoD que decía "métrica
   acordada"): sobre el subset etiquetado del golden set se reporta
   **exactitud del gate** (aciertos/total sobre las 3 clases) y, como métrica de
   costo, **% de no-comprobantes que NO llegan a extracción** (debe ser 100%
   para los clasificados `no_comprobante`; los `indeterminado` que re-deciden a
   `no_comprobante` en revisión también se excluyen). Umbral objetivo: exactitud
   ≥ 90% sobre el subset (ajustable al cerrar el etiquetado). Estas métricas se
   reportan en `06-estrategia-calidad.md` (DoD transversal de fase).
7. **`api.validate(origen, quick=True)` como punto de entrada**: la orquestación
   del doble paso se expone como `validation.validar_y_procesar()` (doc 03 §5.1)
   y se enchufa a la API pública `voucherflow.api.validate()` (esqueleto de F0),
   que deja de lanzar `NotImplementedError`. F2 **no** toca `api.run()` (eso es
   F5); la integración con el orquestador completo queda cuando exista.

## 3. Alcance por tarea (T-201..T-204)

### 3.1 T-201 · `preparar_vista_rapida` (thumbnail/calidad baja)

- **Qué**: derivar de la representación procesada (imagen/PDF renderizado por
  F1) un thumbnail de calidad baja/moderada para la decisión barata.
- **Archivos**: `validation/vistas.py` (o `validation/views.py`) con
  `preparar_vista_rapida(origen/processed) -> VistaPreparada`.
- **Sin deps nuevas**: thumbnail por remuestreo con stdlib / Pillow **solo si ya
  está disponible en el entorno** (misma política "sin dependencias nuevas" de
  F1 §4). Si no hay librería de imagen, el thumbnail puede ser la imagen a
  resolución reducida vía el render de F1; se documenta el mecanismo elegido en
  la decisión §2.2.
- **Tests**: `test_validation_vistas.py` — la vista rápida es de menor
  resolución/calidad que la original; se genera barata.

### 3.2 T-202 · Decisión binaria "¿es comprobante?" con prompt corto (3 salidas)

- **Qué**: `decidir_es_comprobante(vista_rapida) -> VeredictoGate` con el
  `OllamaClient` (T-005) y el prompt corto de 3 salidas. Devuelve también la
  vista usada y reporta `SourceEvidence` (ADR-001, decisión §2.3).
- **Archivos**: `validation/prompt_qween.py` (o `prompts.py`) con el texto del
  prompt versionado (portado de la idea qween §1) + `validation/qween.py`
  (ampliar el esqueleto: implementar `validar_comprobante`).
- **Contrato**: respetar la dataclass `ValidationResult` del esqueleto F0
  (`veredicto`, `confianza_fuente`, `vista_usada`, `detalle`) — **regla dura**,
  no romper los tests de esqueleto de F0.
- **Tests**: `test_validation_qween.py` — con `FakeOllamaClient` se cubren las 3
  salidas + que `no_comprobante` corta el flujo (no llama a extracción).

### 3.3 T-203 · `preparar_vista_revision` (indeterminado) y `preparar_vista_fiel`

- **Qué**: `preparar_vista_revision` (calidad media, para la 2ª pasada de
  indeterminados) y `preparar_vista_fiel` (alta calidad + orientación corregida
  + limpieza leve; **nunca** reutiliza la vista rápida). La vista fiel alimenta
  a F4 (extracción) y conserva tabla/sello/firma/QR/texto pequeño cuando existen
  (E-QWE-2).
- **Archivos**: ampliar `validation/vistas.py` y `validation/qween.py`
  (implementar la 2ª pasada y el enrutado a vista fiel).
- **Tests**: `test_validation_vistas.py` + `test_validation_qween.py` — calidad
  distinta entre vistas (la fiel ≥ revisión ≥ rápida) y la fiel no es la rápida
  re-escalada.

### 3.4 T-204 · Tests: ahorro de costo (no extraer no-comprobantes) y calidad distinta

- **Qué**: tests sobre el subset etiquetado del golden set que verifican (a) el
  ahorro de costo: los no-comprobantes no llegan a extracción; (b) la calidad
  distinta entre vistas se usa correctamente (DoD de F2). Incluye el reporte de
  la métrica acordada (§2.6).
- **Archivos**: `test_validation_gate_ahorro.py` (o integrar en
  `test_validation_qween.py`) + actualización de `tests/golden/casos.csv`
  (etiquetar `veredicto` del subset) y `tests/golden/README.md`.
- **Nota**: el etiquetado de `veredicto` del subset (decisión §2.5) es
  prerrequisito de T-204.

### 3.5 Avance

- Suite default en verde (referencia F1: ~187 tests al cierre de F1).
- Pendiente: T-201..T-204 (esta fase aún no arrancó; `F2.md` estado 🔴 Backlog).
- Ajuste F0 aplicado: `api.validate` deja de lanzar `NotImplementedError` cuando
  `validation.validar_y_procesar()` quede implementado (decisión §2.7) — y por
  lo tanto `validate` se quita de la lista de esqueletos en
  `test_esqueletos_lanzan_notimplemented` (análogo a lo hecho en F1 con
  `process`, subplan F1 §2.4).

## 4. Reglas duras (no romper F0)

- No modificar `validation/qween.py` en sus **contratos congelados**: la
  dataclass `ValidationResult(veredicto, confianza_fuente, vista_usada,
  detalle)` y el enumerado `VeredictoGate` deben seguir construibles tal como
  los fija F0 (los tests de esqueleto los congelan). Ampliar con campos/funciones
  nuevas es válido; **no** cambiar los existentes.
- `validation/__init__.py` debe seguir exportando `ValidationResult`,
  `VeredictoGate` y `validar_comprobante`; ampliar `__all__` es válido.
- No tocar `processing.VeredictoGate` (gate de procesabilidad de F1/T-102):
  pertenece a otro dominio (§2.4).
- La suite default corre **sin** Ollama real: se usa `FakeOllamaClient` /
  mocks; la llamada real al VLM queda en `@pytest.mark.integration` (criterio
  F1 §4).
- `Settings`: ampliar solo si hace falta y **sin** alterar los defaults
  congelados (`test_settings_config.py`).
- Estilo: docstrings y mensajes en español citando los docs (doc 03 §4.2/§5.2,
  IDs de épica/tarea); asserts con mensaje explicativo.
- La vista fiel **no reutiliza** la vista rápida (principio de doble calidad,
  E-QWE-2): si un test o implementación la reutiliza, es un bug de diseño.

## 5. Flujo del doble paso (referencia de implementación)

Portar la semántica de doc 03 §4.2/§5.2 y de la idea `qween.md` §4:

```text
funcion validar_y_procesar(documento):
    vista_rapida = preparar_vista_rapida(documento)         # T-201
    decision = decidir_es_comprobante(vista_rapida)         # T-202 (prompt corto, 3 salidas)
    si decision == no_comprobante:
        rechazar_o_reencolar(documento)                     # no llega a extracción (ahorro)
        retornar
    si decision == indeterminado:
        vista_revision = preparar_vista_revision(documento) # T-203 (calidad media)
        decision = decidir_es_comprobante(vista_revision)   # 2ª pasada
        si decision == no_comprobante:
            rechazar_o_reencolar(documento)
            retornar
    vista_fiel = preparar_vista_fiel(documento)             # T-203 (NO reutiliza rápida)
    retornar vista_fiel                                     # alimenta F4 (extracción)
```

- El resultado del gate se reporta como `ValidationResult` con `vista_usada`
  (`rapida` | `revision`) y, cuando aplica, `SourceEvidence` (ADR-001).
- La "vista fiel" es la entrada de F4 (extracción), no se decide aquí la
  extracción (eso es F4).

## 6. Archivos a crear

- `validation/vistas.py` — `preparar_vista_rapida` / `preparar_vista_revision`
  / `preparar_vista_fiel` (+ helper de thumbnail/calidad).
- `validation/prompt_qween.py` — prompt corto versionado de 3 salidas (portado
  de idea qween §1).
- `validation/qween.py` (ampliado) — implementar `decidir_es_comprobante` +
  `validar_y_procesar()`; el esqueleto `validar_comprobante` se reemplaza o
  aliasea a la nueva orquestación.
- `validation/__init__.py` (ampliado si hace falta).
- Tests F2 en `v2/tests/test_validation_*.py`.
- Actualización de `v2/tests/golden/casos.csv` (etiquetar `veredicto` del
  subset) y `v2/tests/golden/README.md`.

## 7. Docs a actualizar al cierre

- `v2/docs/plan/05-plan/F2.md` (estados T-201..T-204 → Hecho/QA según
  corresponda + bitácora).
- `v2/docs/plan/03-arquitectura/VAL.md` (estado diseño → implementado; resolver
  D-11 `llm`/`vlm`/`auto` y la nota del `VeredictoGate` duplicado §2.4).
- `v2/docs/plan/02-epicas/E-QWE.md` (estado de historias E-QWE-1/E-QWE-2).
- `v2/current.md` (F2 en curso / cerrada).
- No adelantar F1 como cerrada si no corresponde (F1 aún tiene T-105 pendiente).
- Si cambia una decisión de diseño: `04-decisiones-abiertas-adr.md`.

## 8. Verificación final

```bash
cd v2 && python -m pytest tests -q   # todo en verde (suite default sin Ollama real)
python -c "import voucherflow.validation, voucherflow.api"
```

- Suite completa en verde.
- Importar `voucherflow.validation` y `api.validate` funciona (deja de lanzar
  `NotImplementedError`).
- Sin dependencias nuevas agregadas.
- Métricas de F2 reportadas (exactitud del gate + % no-comprobantes que no
  llegan a extracción) sobre el subset etiquetado.

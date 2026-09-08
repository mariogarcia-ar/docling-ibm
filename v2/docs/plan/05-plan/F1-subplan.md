# F1-subplan — Subplan de implementación F1 (Procesamiento / refactor docling)

> Documento de trabajo para la implementación de la **Fase F1** por
> `team implementation`. Complementa el seguimiento de la fase
> ([`F1.md`](F1.md)) y el diseño del módulo
> ([`../03-arquitectura/PROC.md`](../03-arquitectura/PROC.md)).
> **Fecha**: 2026-09-06 · **Rama**: `v2` · **Estado**: En implementación (T-101..T-104 + routing + orquestación/`api.process()` hechos; falta T-105 + cierre de docs).

## 1. Ficha del subplan

| Campo | Valor |
|---|---|
| **Fase** | F1 — Procesamiento (refactor docling) |
| **Tareas que cubre** | T-101, T-102, T-103, T-104, T-105 (ver [`F1.md`](F1.md)) |
| **Épicas asociadas** | E-DOC (E-DOC-1, E-DOC-2, E-DOC-3) |
| **Módulo** | `voucherflow/processing/` |
| **Responsable** | team implementation |
| **Decisiones de alcance** | Cerradas con el negocio/equipo (ver §2) |
| **DoD de referencia** | "Procesar los formatos de las ideas produce Markdown ordenado equivalente o superior a v1, con gate y elección de motor; cubierto por tests de formato sobre golden set" (DoD de F1 en `05-plan-ejecucion.md`) |

## 2. Decisiones de alcance cerradas (2026-09-06)

1. **Imagen T-102/T-103 → heurístico liviano**: el clasificador y el gate de
   procesabilidad se implementan con heurísticas simples, **sin** OpenCV/Pillow
   (no se agregan dependencias). La orientación se decide por boxes. Docling
   realiza el OCR.
2. **VLM T-104 → solo selección + hook**: `processing` elige el motor
   (`ocr`/`vlm`/`auto`) y expone el punto de integración; la transcripción VLM
   real queda para F4 (en F1 **no** se llama a Ollama/`qwen2.5vl`).
3. **Paridad T-105 → integración marcada** `@pytest.mark.integration`: corren
   Docling real sobre fixtures y comparan estructura con v1; **no** corren en la
   suite default.
4. **Tests F0 afectados → actualizar en F1**:
   `test_esqueletos_lanzan_notimplemented` debe quitar `process` de la lista
   (cuando `api.process()` quede implementado) y dejarlo cubierto por los tests
   propios de F1. *Aplicado: `api.process()` quedó implementado (T-105/ORQ,
   commit 4190fb4) y `process` ya no figura en la lista de esqueletos.*
5. **Exponer el raw de Docling → flag `docling_raw` (Opción A, 2026-09-06)**: se
   añade el keyword `docling_raw: bool = False` a `procesar_documento()` y a
   `api.process()` para devolver en `ProcessedDocument.markdown` el **crudo de
   Docling** (`export_to_markdown()`, sin el reordenado por posición del
   exportador E-DOC-3); equivale a `v1/run_raw.py`. **No** se modifica
   `models/docling.py` ni la dataclass (regla dura §4). Aplica a documento
   completo (imagen / PDF apto / office / texto y PDF escaneado vía imagen
   renderizada); en PDF mixto/parcial el crudo pleno no existe (páginas aptas
   usan PyMuPDF `get_text`) y se anota `docling_raw: "parcial_no_aplica"` en
   `calidad`. Default `False` preserva la política combinada (contrato F2/F3/F4).
   Exposición CLI: `--docling-raw` en `scripts/probar_api_process.py`.
6. **Ruta texto nativo → `pdftotext --layout` preferido, Docling como
   fallback (decisión 2026-09-07, revierte A1 de 2026-09-06)**: la ruta
   `pdf_texto` **apto** (todas las páginas con texto nativo) se extrae con
   `pdftotext --layout` (poppler) porque recupera el **layout de columnas**
   que Docling directo aplana (caso `9dfc597f`, boleto a 2 columnas; PROC.md
   §5.2: "excelente layout: recupera columnas/alineación"). Si `pdftotext` no
   está disponible o devuelve vacío → **fallback** a Docling directo
   (comportamiento previo A1). `office`/`texto` y el modo `docling_raw`
   (Opción A, §2.5) siguen con Docling. Módulo nuevo `processing/pdftotext.py`
   (`extraer_con_pdftotext_layout`) expuesto en `processing/`; el motor queda
   `"pdftotext"` (con `salida: "pdftotext_layout"` en `calidad`) o `"docling"`
   en el fallback. Los PDFs aptos ya no corren Docling cuando hay poppler:
   más rápido y mejor layout.

## 3. Alcance por tarea (T-101..T-105)

- [x] **T-101** — `type_detector.py`: `detectar(origen) -> TipoEntrada`.
  `TipoEntrada(tipo, ruta_ocr, motivo="")` con valores
  `pdf_texto | pdf_escaneado | imagen | office | texto | no_soportado`;
  distinción `pdf_texto`/`pdf_escaneado` por capa real de texto (PyMuPDF).
  Tests: `test_processing_type_detector.py` (29).
- [x] **T-102** — `image_classifier.py`: clasifica la imagen
  (foto/escaneo/screenshot/manuscrito) + gate de procesabilidad. Heurístico
  liviano (stdlib, sin deps). Tests: `test_processing_image_classifier.py` (27).
- [x] **T-103** — `preprocessing.py` (QualityReport heurístico, sin CV) +
  `orientation.py`: orientación horizontal/vertical por boxes (dominante).
  Tests: `test_processing_orientation.py` (19).
- [x] **T-104** — `ocr.py` (selección de motor `ocr`/`vlm`/`auto` + hook
  `transcribir_vlm` que no llama a Ollama en F1) + `markdown_exporter.py`
  (exporta ordenado por posición, portado de `v1/lib/orientation.py`, paridad
  byte-compatible). Tests: `test_processing_exportador_motor.py` (24).
- [x] **T-105/EXT (apoyo orquestación)** — `routing.py`: análisis de PDF por
  página (apta_layout/escaneada/corrupta/vacía) + veredicto por PDF
  (apto/requiere_ocr/parcial). Tests: `test_processing_routing.py` (9).
- [ ] **T-105** — tests de integración marcados `@pytest.mark.integration`:
  paridad estructural contra v1 sobre fixtures (pendiente).
- [x] **Orquestación** — `procesar_documento(origen, *, converter, modo_motor,
  docling_raw) -> ProcessedDocument` en `processing/` (entrada de `api.process`)
  y `api.process(origen, *, docling_raw)` implementado (T-105/ORQ, commit
  4190fb4). Flujo: detector de tipo → gate → clasificador → preprocesamiento →
  orientación → motor → exportador ordenado (o crudo con `docling_raw`).

### 3.1 Avance

- **Suite default**: en verde (`python -m pytest tests -q`; ~195 tests con los
  de `docling_raw`).
- Hecho: T-101, T-102, T-103, T-104, enrutado PDF por página (`routing.py`) y
  orquestación `procesar_documento()` + `api.process()` (T-105/ORQ) con flag
  `docling_raw` (decisiones §2.5/§2.6).
- Pendiente: T-105 (integración paridad) y docs de cierre (§7).
- Ajuste F0 aplicado: `process` ya no figura en
  `test_esqueletos_lanzan_notimplemented` (decisiones §2.4/§2.5).

## 4. Reglas duras (no romper F0)

- No modificar `models/docling.py` salvo necesidad real (los tests congelan
  `convert`, `_tipo_entrada_basico`, `EXTENSIONES_SOPORTADAS` ⊇
  `{pdf,jpg,png,docx,txt,md}`, construcción lazy).
- `ProcessedDocument(tipo_entrada, ruta, markdown)` debe seguir siendo
  construible con 3 argumentos posicionales (test de F0).
- `processing/__init__.py` debe seguir importando `ProcessedDocument`; ampliar
  `__all__` es válido.
- La suite default corre **sin** Docling real ni Ollama: se usan dobles
  (`FakeConverter`/`FakeItem`) o fixtures; la conversión real queda en
  `@pytest.mark.integration`.
- Estilo: docstrings y mensajes en español citando los docs
  (doc 03 §4.1, IDs de épica/tarea); asserts con mensaje explicativo.
- `Settings`: ampliar solo si hace falta y **sin** alterar los defaults
  congelados (`test_settings_config.py`).

## 5. Algoritmo de exportación por posición (portar de `v1/lib/orientation.py`, byte-compatible)

- Agrupar en líneas por `center_y` (horizontal) o `center_x` (vertical), con
  tolerancia **25.0**.
- Horizontal: líneas ordenadas `reverse=True`; dentro de la línea por `left`
  ascendente. Vertical: líneas por `center_x` sin reverse; dentro por `center_y`
  con reverse.
- Tablas (`label == 'table'`) → ítem único con su Markdown exportado, con
  orientación forzada horizontal.
- Join de línea con `' | '`, `'\n'` entre líneas y `'\n'` final. Sin boxes →
  `"No se encontraron textos en orientación {orient}.\n"`.
- Boxes: usar `center_x`/`center_y` del `Box` de F0; para `left`, derivar del
  `bbox`.

## 6. Archivos a crear

- `processing/image_classifier.py`
- `processing/preprocessing.py`
- `processing/orientation.py`
- `processing/ocr.py`
- `processing/markdown_exporter.py`
- `processing/__init__.py` (ampliado)
- Tests F1 en `v2/tests/test_processing_*.py`

## 7. Docs a actualizar al cierre

- `v2/docs/plan/05-plan/F1.md` (estados T-101..T-105 → Hecho/QA según
  corresponda + bitácora).
- `v2/docs/plan/03-arquitectura/PROC.md`.
- `v2/docs/plan/02-epicas/E-DOC.md`.
- `v2/current.md` (F1 en curso).
- No adelantar F0 como cerrada si no corresponde.

## 8. Verificación final

```bash
cd v2 && python -m pytest tests -q   # todo en verde
python -c "import voucherflow.processing, voucherflow.api"
```

- Suite completa en verde.
- Importar `voucherflow.processing` y `api.process` funciona.
- Sin dependencias nuevas agregadas.

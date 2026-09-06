# F1-subplan — Subplan de implementación F1 (Procesamiento / refactor docling)

> Documento de trabajo para la implementación de la **Fase F1** por
> `team implementation`. Complementa el seguimiento de la fase
> ([`F1.md`](F1.md)) y el diseño del módulo
> ([`../03-arquitectura/PROC.md`](../03-arquitectura/PROC.md)).
> **Fecha**: 2026-09-06 · **Rama**: `v2` · **Estado**: Listo para implementar (DoR de diseño cerrado).

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
   (ya implementado en F1) y dejarlo cubierto por los tests propios de F1.

## 3. Alcance por tarea (T-101..T-105)

- **T-101** — `type_detector.py`: implementar `detectar(origen) -> TipoEntrada`.
  Mantener `@dataclass(frozen=True) TipoEntrada(tipo, ruta_ocr, motivo="")` y los
  valores `pdf_texto | pdf_escaneado | imagen | office | texto | no_soportado`.
  Resolver la distinción `pdf_texto`/`pdf_escaneado` con heurística barata.
- **T-102** — `image_classifier.py`: clasificar la imagen
  (foto/escaneo/screenshot/manuscrito) + gate de procesabilidad. Heurístico
  liviano.
- **T-103** — `preprocessing.py` (stub heurístico liviano, sin CV) +
  `orientation.py`: detectar orientación horizontal/vertical por boxes
  (dominante).
- **T-104** — `ocr.py` (selección de motor `ocr`/`vlm`/`auto` + hook) +
  `markdown_exporter.py`: exportar ordenado por posición (portar el algoritmo de
  `v1/lib/orientation.py`).
- **T-105** — tests de integración marcados `@pytest.mark.integration`: paridad
  estructural contra v1 sobre fixtures.
- **Orquestación** — `procesar_documento(origen) -> ProcessedDocument` en
  `processing/` (entrada de `api.process`); implementar `api.process()`.

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

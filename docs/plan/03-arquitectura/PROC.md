# Módulo processing — Procesamiento docling multi-tipo (Seguimiento)

> Documento de seguimiento generado a partir de
> [`03-arquitectura-solucion.md`](../03-arquitectura-solucion.md).

## 1. Ficha del módulo

| Campo | Valor |
|---|---|
| **Módulo (paquete)** | `voucherflow/processing/` (doc 03 §11: `type_detector.py`, `image_classifier.py`, `preprocessing.py`, `orientation.py`, `ocr.py`, `markdown_exporter.py`; + `routing.py` enrutado PDF por página) |
| **Responsabilidad** | Decidir el tipo de entrada (pdf/img/docx/xlsx/pptx/txt/csv/log/html/md), elegir la ruta de procesamiento y normalizar; en imágenes: gate de procesabilidad → clase de imagen → preprocesamiento → orientación → motor OCR/VLM → salida ordenada (Markdown + boxes). |
| **Épicas asociadas** | E-DOC (E-DOC-1 detección de tipo, E-DOC-2 procesamiento adaptativo de imágenes, E-DOC-3 salida ordenada) |
| **Fase(s) del plan** | F0 (T-006 adaptador Docling) y F1 (T-101..T-105 + orquestación) |
| **Contratos que expone/consume** | Expone: `ProcessedDocument` (tipo_entrada, ruta, markdown, boxes, orientacion, motor, calidad) → lo consumen validation, classification y extraction. Consume: `QualityReport` (salida de calidad) y el adaptador `DoclingConverter` (doc 03 §4.6). |
| **ADRs relacionados** | ADR-007 (organización del paquete `src/` — decide dónde vive el módulo); decisión heredada de v1 (Docling como motor OCR/conversión multi-formato, encapsulado aquí). |
| **Interfaces clave** | `procesar_documento()` / `processar_documento()`; dataclass `ProcessedDocument { tipo_entrada, ruta, markdown, boxes, orientacion, motor, calidad }`; flujo: `Detector de tipo → (texto nativo | pdf escaneado→imagen | imagen | office/plano) → Gate de procesabilidad → Clasificador de imagen → Preprocesamiento → Orientación → Motor (OCR|VLM) → Ordenar por posición (center_y/center_x) → salida Markdown`. |
| **Responsable ciclo** | team analysis (BA/SA/PM) → team implementation |
| **Estado de diseño** | En implementación (T-101..T-105/ORQ hechos, incl. orquestación `procesar_documento()`/`api.process()` con flag `docling_raw`; falta T-105 integración paridad) |
| **Fecha inicio** | 2026-09-06 |
| **Fecha fin** |  |

## 2. Estado de trazabilidad del módulo

| Elemento de arquitectura (doc 03) | Sección | Épica/Historia | Fase/Tarea | Estado |
|---|---|---|---|---|
| Detector de tipo de entrada (pdf/img/office/plano/no soportado → rutas) | §4.1 | E-DOC-1 | F1 / T-101 | [x] hecho (`type_detector.py`) |
| Rutas: texto nativo / pdf escaneado→imagen / office+planos / rechazo-reencolado | §4.1 | E-DOC-1 | F1 / T-101 | [x] hecho (decisión de ruta por tipo; `routing.py` para PDF por página) |
| Enrutado de PDF por página (apta/escaneada/corrupta/vacía → apto/requiere_ocr/parcial) | §4.1 | E-DOC-1 | F1 / T-105/EXT | [x] hecho (`routing.py`) |
| Gate de procesabilidad (imagen) | §4.1 | E-DOC-2 | F1 / T-102 | [x] hecho (`image_classifier.py`) |
| Clasificador de imagen (foto / escaneo / screenshot / manuscrito) | §4.1 | E-DOC-2 | F1 / T-102 | [x] hecho |
| Preprocesamiento (perspectiva, calidad, binarización) | §4.1 | E-DOC-2 | F1 / T-103 | [x] hecho (heurístico `QualityReport`; stub sin CV) |
| Detección de orientación + rotación | §4.1 | E-DOC-2 | F1 / T-103 | [x] hecho (`orientation.py` por boxes) |
| Elección de motor OCR tradicional vs. VLM | §4.1 | E-DOC-2 | F1 / T-104 | [x] hecho (selección + hook; VLM real en F4) |
| Ordenar por posición (center_y / center_x) + tablas Markdown | §4.1 / E-DOC-3 (doc 02) | E-DOC-3 | F1 / T-104 | [x] hecho (`markdown_exporter.py`) |
| Dataclass `ProcessedDocument` (contrato de salida) | §4.1 + §9 | E-DOC | F0 / T-001 (schema afín) | [x] hecho |
| Adaptador `DoclingConverter` encapsulado (consumido aquí) | §4.6 | E-DOC | F0 / T-006 | [x] hecho |
| Extracción de PDF apto con `pdftotext --layout` (poppler) + fallback Docling | §5.2/§5.3 | E-DOC-1 | F1 / T-104 (ruta texto nativo) | [x] hecho (`processing/pdftotext.py`; A1 revertida 2026-09-07, ver subplan F1 §2.6) |
| Orquestación `procesar_documento()` + `api.process()` | §4.1 | E-DOC-1 / E-DOC | F1 / T-105/ORQ | [x] hecho (orquestación completa; `api.process` con keyword `docling_raw`) |
| Exponer crudo de Docling (`docling_raw`, Opción A) | §4.1 | E-DOC-3 | F1 / T-105/ORQ | [x] hecho (flag en orquestación + `api.process`; equiv. `v1/run_raw.py`; ver subplan F1 §2.5) |
| Paridad funcional con `ocr_documents.py`/`run.py`/`run_raw.py` (mismo .md) | §8.2 (mapeo v1→v2) | E-DOC | F1 / T-105 | [ ] pendiente (integración) |

## 3. Definition of Design / contratos a congelar

- [ ] Interfaz pública acordada: dataclass `ProcessedDocument` (tipo_entrada, ruta, markdown, boxes, orientacion, motor, calidad) — validar campos y opcionalidad de `boxes`/`calidad`.
- [ ] Contrato de entrada/salida alineado al schema de evidencia: el `markdown`+`boxes` de `ProcessedDocument` es la entrada del flujo LLM de extracción/clasificación; `ValidationResult` (E-QWE) y `SourceEvidence` (E-EXT) se construyen sobre esta salida.
- [ ] ADR(s) asociado(s) resueltos: ADR-007 (layout del paquete). Confirmar la lista de formatos soportados y su ruta (alcance de E-DOC-1) con el negocio.
- [ ] Casos de golden set / tests que lo validan: un caso etiquetado por tipo de entrada/formato (pdf texto, pdf escaneado, imagen, office, plano, no soportado) + paridad .md contra v1 (T-105, DoD F1).

## 4. Decisiones abiertas que lo afectan

- **ADR-007 (D-7)** — Organización y nombre del paquete: la ubicación `src/voucherflow/processing/` depende de resolver el layout en F0 (T-003).
- **D-11 (sin ADR)** — Modalidades `llm`/`vlm`/`auto` y su mapeo a los nuevos flujos: condiciona cómo el módulo elige el motor y expone la salida; se resuelve en diseño detallado.
- **Decisión heredada de v1** — El motor OCR por defecto (Docling vs. RapidOCR) y el modelo VLM por defecto se conservan configurable (decisión heredada §3 del doc 04).
- **ADR-003 (D-3)** — Indirecto: la calidad de la vista que prepara validation depende de la representación de `processing`; confirmar resolución mínima/orientación para no perder texto pequeño (afín a E-QWE-2).

## 5. Hallazgos técnicos (validación 2026-09-06, datos reales)

Comparación empírica sobre fixtures reales (`pdf_escaneados/` y
`pdf_aptos_layout/`) de **Docling vs `pdftotext --layout`** para decidir la
ruta de orquestación:

### 5.1 PDF escaneado (sin capa de texto)
| Método | Resultado |
|---|---|
| `pdftotext --layout` | ❌ **vacío** (0 chars): no hay texto que extraer. |
| Docling sobre PDF directo | ⚠️ **impredecible**: a veces corre OCR (68623f4b → 11400 chars) y a veces solo `<!-- image -->` (3ac5a2ec → 14 chars, boxes=0). |
| **Docling render→imagen→OCR** | ✅ **confiable**: render de página a imagen (PyMuPDF ~300 dpi) → Docling OCR → exportador ordena. 4/5 dieron texto limpio y ordenado (68623f4b → 10716 chars, boxes=8). |

### 5.2 PDF apto (texto nativo)
| Método | Resultado |
|---|---|
| `pdftotext --layout` | ✅ **excelente layout**: recupera columnas/alineación (242823d2 → 3378 chars, 36744cc6 → 2244 chars). |
| Docling sobre PDF directo | ✅ texto nativo, pero deja `<!-- image -->` residual y a veces corre RapidOCR innecesario (warning "empty result"). |

### 5.3 Conclusión para la orquestación
- **Causa raíz**: el adaptador Docling (F0/T-006, igual que v1) configura
  `force_full_page_ocr=True` **solo para `InputFormat.IMAGE`**, no para
  `InputFormat.PDF`. Por eso el OCR sobre PDF directo es impredecible.
- **Ruta `pdf_escaneado`** → **renderizar a imagen** (PyMuPDF) y luego pasar
  por el pipeline de imagen: gate T-102 → Docling OCR → orientación T-103 →
  exportador T-104. (Coincide con E-DOC-1: "PDF escaneado → se convierte a
  imagen".)
- **Ruta `pdf_texto`/`texto`** → para PDF **apto** (routing): `pdftotext
  --layout` preferido (recupera layout de columnas) con **fallback** a Docling
  directo si poppler no está disponible (decisión 2026-09-07, revierte A1).
- **Decisión 2026-09-06 (A1) — REVERTIDA 2026-09-07**: A1 dejaba la ruta texto
  nativo **solo con Docling** y `pdftotext --layout`/PyMuPDF layout como
  alternativa futura (layout de columnas, caso `9dfc597f`). El 2026-09-07 se
  revierte para PDF **apto**: se migra el helper a la librería
  (`processing/pdftotext.py`, `extraer_con_pdftotext_layout`) y la orquestación
  lo usa primero (motor `"pdftotext"`, `calidad.salida: "pdftotext_layout"`);
  si no hay poppler o devuelve vacío → fallback Docling. Office/texto y el
  modo `docling_raw` siguen con Docling. Ver subplan F1 §2.6.
- **Nota**: la calidad estructural mejora con render→imagen→OCR + exportador
  (texto ordenado línea por línea) frente al PDF directo (texto pegado, pocos
  boxes).

### 5.4 Configuración recomendada de Docling para facturas escaneadas (referencia)

> Fuente: investigación externa (docling.ai, docling-project.github.io,
> issues del repo docling-project/docling) aportada por el equipo el
> 2026-09-06. Complementa los hallazgos empíricos de §5.1–5.3.

Una factura escaneada es un documento **puramente visual** (sin capa de texto)
con tablas complejas (artículos, cantidades, subtotales) donde la precisión es
crítica. Para que el OCR de Docling **no falle en silencio** y extraiga texto y
tablas, se recomienda estructurar la configuración bajo tres pilares:

**Pilar 1 — Forzar OCR de página completa (`force_full_page_ocr`).**
Por defecto Docling optimiza recursos: si detecta trazas de una capa de texto
oculta (aunque esté corrupta/mal codificada por el escáner) puede **saltarse el
OCR**. En una factura escaneada hay que obligar al pipeline a ignorar metadatos
previos y procesar la página como imagen pura.

**Pilar 2 — Cambiar de motor OCR (evitar el bug de RapidOCR).**
En entornos locales el motor por defecto (`RapidOCR`) a veces tiene problemas
para cargar modelos de idioma o falla al interpretar ciertos canales de imagen
(p. ej. PNG con canal alfa). Para facturas en español/inglés la recomendación
oficial es usar **Tesseract** (`TesseractCliOcrOptions`) o **EasyOCR**
(`EasyOcrOptions`). *Nota: Tesseract requiere instalación en el SO.*

**Pilar 3 — Configuración de implementación recomendada.**

```python
from docling.datamodel.base_models import InputFormat
from docling.datamodel.pipeline_options import PdfPipelineOptions, TesseractCliOcrOptions
from docling.document_converter import DocumentConverter, PdfFormatOption

# 1. Opciones del pipeline de PDF
pipeline_options = PdfPipelineOptions()
pipeline_options.do_ocr = True
pipeline_options.do_table_structure = True  # activa TableFormer

# Regla de oro: forzar OCR de página completa con Tesseract (no RapidOCR)
pipeline_options.ocr_options = TesseractCliOcrOptions(
    lang=["spa", "eng"],        # idiomas de las facturas
    force_full_page_ocr=True    # evita saltarse el OCR por texto basura
)

# 2. Asociar estrictamente al formato PDF
ocr_tuned_features = PdfFormatOption(pipeline_options=pipeline_options)

# 3. Convertidor con la configuración personalizada
converter = DocumentConverter(
    allowed_formats=[InputFormat.PDF],
    format_options={InputFormat.PDF: ocr_tuned_features},
)

# 4. Convertir la factura escaneada
resultado = converter.convert("tu_factura_escaneada.pdf")

# 5. Exportar: el Markdown conserva las tablas en formato MD legible
print(resultado.document.render_as_markdown())
```

**Tips para el pipeline de facturas:**
- **Si el PDF falla** (problemas de canales por el software de escaneo): no
  dejar que Docling haga la conversión interna a imagen. Usar `pdf2image`
  externamente, guardar páginas como `.jpg` en disco (RGB **sin canal alfa**) y
  pasar los `.jpg` directo a Docling (acepta imágenes nativas).
- **Estructura de salida**: al renderizar a Markdown las tablas de precios/ítems
  se mantienen alineadas, lo que facilita usar Regex o un LLM para extraer
  campos clave (Monto Total, Fecha, CUIT/Tax ID).

**Decisiones pendientes para la orquestación (derivadas de esta referencia):**
- ¿Instalar Tesseract en el entorno o preferir EasyOCR (puramente Python)?
- ¿El pipeline final interactuará con un LLM/sistema RAG para extraer datos de
  la factura (afín a F3/F4)?

## 6. Bitácora de seguimiento del módulo

| Fecha | Acción / hito | Responsable | Estado |
|---|---|---|---|
| 2026-09-06 | Validación Docling vs pdftotext sobre pdf_escaneados/pdf_aptos_layout; decisión de ruta para orquestación (render→imagen para escaneados). | team implementation | Documentado (§5) |
| 2026-09-06 | T-101 a T-104 implementados (detector PyMuPDF, clasificador+gate, orientación/preproc, motor+exportador). | team implementation | Hecho |
| 2026-09-06 | `routing.py`: enrutado de PDF por página (apta/escaneada/corrupta/vacía → apto/requiere_ocr/parcial) portado de `scripts/detectar_aptos_pdftotext_layout.py`. | team implementation | Hecho |
| 2026-09-06 | Referencia externa de configuración Docling para facturas escaneadas (force_full_page_ocr + Tesseract/EasyOCR) agregada a §5.4. | team analysis / implementation | Documentado |
| 2026-09-06 | Pendiente: orquestación `procesar_documento()` + `api.process()` (entrada de F2/F3/F4) y T-105 (tests de integración de paridad). | team implementation | Pendiente |
| 2026-09-07 | **A1 revertida (ruta texto nativo)**: PDF apto se extrae con `pdftotext --layout` (poppler) preferido + fallback Docling. Helper migrado a `processing/pdftotext.py`; orquestación usa motor `pdftotext` (layout de columnas, caso `9dfc597f`). Tests `test_processing_pdftotext.py` + ajuste orquestación/paridad. | team analysis / implementation | Hecho |

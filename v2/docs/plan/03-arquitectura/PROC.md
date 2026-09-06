# Módulo processing — Procesamiento docling multi-tipo (Seguimiento)

> Documento de seguimiento generado a partir de
> [`03-arquitectura-solucion.md`](../03-arquitectura-solucion.md).

## 1. Ficha del módulo

| Campo | Valor |
|---|---|
| **Módulo (paquete)** | `voucherflow/processing/` (doc 03 §11: `type_detector.py`, `image_classifier.py`, `preprocessing.py`, `orientation.py`, `ocr.py`, `markdown_exporter.py`) |
| **Responsabilidad** | Decidir el tipo de entrada (pdf/img/docx/xlsx/pptx/txt/csv/log/html/md), elegir la ruta de procesamiento y normalizar; en imágenes: gate de procesabilidad → clase de imagen → preprocesamiento → orientación → motor OCR/VLM → salida ordenada (Markdown + boxes). |
| **Épicas asociadas** | E-DOC (E-DOC-1 detección de tipo, E-DOC-2 procesamiento adaptativo de imágenes, E-DOC-3 salida ordenada) |
| **Fase(s) del plan** | F0 (T-006 adaptador Docling) y F1 (T-101..T-105) |
| **Contratos que expone/consume** | Expone: `ProcessedDocument` (tipo_entrada, ruta, markdown, boxes, orientacion, motor, calidad) → lo consumen validation, classification y extraction. Consume: `QualityReport` (salida de calidad) y el adaptador `DoclingConverter` (doc 03 §4.6). |
| **ADRs relacionados** | ADR-007 (organización del paquete `src/` — decide dónde vive el módulo); decisión heredada de v1 (Docling como motor OCR/conversión multi-formato, encapsulado aquí). |
| **Interfaces clave** | `procesar_documento()` / `processar_documento()`; dataclass `ProcessedDocument { tipo_entrada, ruta, markdown, boxes, orientacion, motor, calidad }`; flujo: `Detector de tipo → (texto nativo | pdf escaneado→imagen | imagen | office/plano) → Gate de procesabilidad → Clasificador de imagen → Preprocesamiento → Orientación → Motor (OCR|VLM) → Ordenar por posición (center_y/center_x) → salida Markdown`. |
| **Responsable ciclo** | team analysis (BA/SA/PM) → team implementation |
| **Estado de diseño** | 🔴 Borrador |
| **Fecha inicio** |  |
| **Fecha fin** |  |

## 2. Estado de trazabilidad del módulo

| Elemento de arquitectura (doc 03) | Sección | Épica/Historia | Fase/Tarea | Estado |
|---|---|---|---|---|
| Detector de tipo de entrada (pdf/img/office/plano/no soportado → rutas) | §4.1 | E-DOC-1 | F1 / T-101 | [ ] pendiente |
| Rutas: texto nativo / pdf escaneado→imagen / office+planos / rechazo-reencolado | §4.1 | E-DOC-1 | F1 / T-101 | [ ] pendiente |
| Gate de procesabilidad (imagen) | §4.1 | E-DOC-2 | F1 / T-102 | [ ] pendiente |
| Clasificador de imagen (foto / escaneo / screenshot / manuscrito) | §4.1 | E-DOC-2 | F1 / T-102 | [ ] pendiente |
| Preprocesamiento (perspectiva, calidad, binarización) | §4.1 | E-DOC-2 | F1 / T-103 | [ ] pendiente |
| Detección de orientación + rotación | §4.1 | E-DOC-2 | F1 / T-103 | [ ] pendiente |
| Elección de motor OCR tradicional vs. VLM | §4.1 | E-DOC-2 | F1 / T-104 | [ ] pendiente |
| Ordenar por posición (center_y / center_x) + tablas Markdown | §4.1 / E-DOC-3 (doc 02) | E-DOC-3 | F1 / T-104 | [ ] pendiente |
| Dataclass `ProcessedDocument` (contrato de salida) | §4.1 + §9 | E-DOC | F0 / T-001 (schema afín) | [ ] pendiente |
| Adaptador `DoclingConverter` encapsulado (consumido aquí) | §4.6 | E-DOC | F0 / T-006 | [ ] pendiente |
| Paridad funcional con `ocr_documents.py`/`run.py`/`run_raw.py` (mismo .md) | §8.2 (mapeo v1→v2) | E-DOC | F1 / T-105 | [ ] pendiente |

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
- **Ruta `pdf_texto`/`texto`** → Docling directo (texto nativo); `pdftotext
  --layout` queda como alternativa/complemento para recuperar layout de
  columnas (decisión de orquestación).
- **Nota**: la calidad estructural mejora con render→imagen→OCR + exportador
  (texto ordenado línea por línea) frente al PDF directo (texto pegado, pocos
  boxes).

## 6. Bitácora de seguimiento del módulo

| Fecha | Acción / hito | Responsable | Estado |
|---|---|---|---|
| 2026-09-06 | Validación Docling vs pdftotext sobre pdf_escaneados/pdf_aptos_layout; decisión de ruta para orquestación (render→imagen para escaneados). | team implementation | Documentado (§5) |
| 2026-09-06 | T-101 a T-104 implementados (detector PyMuPDF, clasificador+gate, orientación/preproc, motor+exportador). | team implementation | Hecho |

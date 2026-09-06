# v2 — Evolución cronológica del pipeline

En base a lo aprendido en v1, este documento ordena el flujo del proyecto en el orden real de ejecución: primero se genera el OCR, luego se normaliza la lectura, después se extraen los datos, y finalmente se clasifican y se consolida el pipeline completo.

## 1) Preparación del entorno y entrada de documentos

El proceso empieza con documentos de entrada en formato PDF o imagen (`jpg`, `png`, `tiff`, etc.). La primera decisión es identificar el tipo de archivo y elegir la ruta de procesamiento más adecuada.

### Reglas de entrada

- Si es un PDF con texto extraíble, conviene usar extracción directa por texto.
- Si es un PDF escaneado o una imagen, se debe convertir a imagen y trabajar con OCR.
- Si la imagen tiene baja resolución, conviene hacer preprocesamiento antes del OCR.
- Si la imagen es una fotografía de comprobante, puede requerirse enderezamiento de perspectiva.
- Si hay texto manuscrito, firma, sello o estructura visual compleja, puede requerirse un modelo visual (VLM).

### Recomendación general

1. Detectar tipo de archivo.
2. Detectar si el documento es un comprobante.
3. Si no es comprobante, finalizar temprano.
4. Si es comprobante, continuar con la extracción estructurada.

---

## 2) OCR y generación del Markdown base

La primera etapa funcional del pipeline es procesar el documento con IBM Docling para obtener texto de la imagen/PDF en un formato intermedio: Markdown.

### Script principal

```bash
python ocr_documents.py files
```

Esto recorre recursivamente las carpetas y procesa imágenes/PDFs, generando un `.md` junto al archivo original.

### Comandos útiles

```bash
# Procesar una carpeta específica
python ocr_documents.py files/2025-08

# Forzar re-generación del Markdown
python ocr_documents.py files --force

# Procesar en paralelo
python ocr_documents.py files --workers 4

# Elegir orientación dominante
python ocr_documents.py files --orientation auto

# Guardar salida en otra carpeta
python ocr_documents.py files --output output
```

### OCR sin filtro

Cuando se quiere obtener el Markdown bruto generado por Docling, sin la normalización de orden y orientación, se usa:

```bash
python run_raw.py --input files/2025-08/2E1F7D6C/documento.jpg
python run_raw.py --input documento.pdf --output documento_raw.md
```

Esto genera una vista cruda del contenido, útil para diagnóstico y comparación.

---

## 3) Ordenamiento de boxes y normalización visual

Una vez que el OCR devuelve los elementos detectados, hay que ordenar el contenido según la posición del texto dentro de la página.

### Regla de orden

- En orientación horizontal, se agrupan por `center_y`.
- En orientación vertical, se agrupan por `center_x`.
- Dentro de cada línea o columna, se ordenan por posición.
- Los campos se separan con `|` en Markdown.
- Las tablas detectadas por Docling se conservan como tablas Markdown.

### Importancia

Esto es clave porque el OCR no solo devuelve texto, sino que también devuelve posición espacial. Si el documento está rotado o la orientación es distinta, la extracción final se vuelve inconsistente.

---

## 4) Detección de comprobante y orientación

Antes de extraer datos estructurados, el pipeline debe decidir si el documento es un comprobante o no.

### Flujo recomendado

1. Detectar si hay comprobante.
2. Si no hay comprobante: finalizar.
3. Si hay comprobante:
   - detectar orientación del comprobante,
   - ordenar elementos visualmente,
   - detectar tablas,
   - preparar salida para extracción.

Este punto es central porque la extracción de facturas, tickets y comprobantes suele requerir un orden espacial preciso.

---

## 5) Extracción estructurada a partir del Markdown OCR

Una vez que el OCR y el ordenamiento están bien definidos, el siguiente paso es convertir el Markdown en un JSON estructurado usando modelos LLM o VLM.

### Script de extracción

```bash
python document_extraction.py files/2025-08/2D2C9343/resultado.md -M kvi
python document_extraction.py files/2025-08/2D2C9343/resultado.md -M kvg
python document_extraction.py files/2025-08/2D2C9343/resultado.md -M 11.1
```

### Modos principales

- `kvi`: extracción orientada a comprobantes y facturas.
- `kvg`: extracción genérica para cualquier documento.
- `11.1`: detección del tipo de documento y letra del comprobante mediante prompt visual.
- `01` / `ccc`: clasificación de centros de costo.
- `02` / `mcc`: clasificación de macro categoría (requiere centro de costo).
- `03` / `cfc`: concepto y código final (requiere macro categoría y condición impositiva).

### Comandos de ejemplo

```bash
# Análisis con otro modelo
python document_extraction.py documento.md -M kvi -m qwen2.5vl:3b -o resultado.json

# Forzar análisis de texto
python document_extraction.py documento.md -M 11.1 --modality llm

# Forzar análisis visual
python document_extraction.py documento.jpg -M 11.1 --modality vlm

# Clasificación contable paso a paso
python document_extraction.py documento.md -M 01
python document_extraction.py documento.md -M 02 --centro-costo CC0006
python document_extraction.py documento.md -M 03 --macro-categoria MC07 --condicion-impositiva 21%
```

### Requisito

Ollama debe estar disponible en `http://localhost:11434`.

---

## 6) Pipeline de clasificación contable

La clasificación no se hace de una sola vez, sino en etapas secuenciales.

### Script

```bash
python classification_pipeline.py documento.md --condicion-impositiva 21
```

### Secuencia

1. `01`: devuelve hasta tres centros de costo.
2. `02`: recibe el centro de costo y devuelve hasta tres macro categorías.
3. `03`: recibe la macro categoría y devuelve concepto y código final.

### Características

- Acepta un archivo individual o un directorio.
- Si recibe un directorio, recorre recursivamente todas las subcarpetas.
- Guarda resultados parciales en JSON sidecar y puede reanudar si se interrumpe.
- Con `-o`, acumula todos los resultados en un único archivo JSON.

### Ejemplos

```bash
# Todos los Markdown de una carpeta
python classification_pipeline.py files/2025-08 --condicion-impositiva 10_5

# Guardar todo en un solo JSON
python classification_pipeline.py files/2025-08 --condicion-impositiva 21 -o classification_results.json

# Usar otro modelo
python classification_pipeline.py documento.md --model qwen2.5vl:3b -o resultado.json
```

### Condiciones impositivas soportadas

- `21`
- `10_5`
- `27`
- `2_5`
- `exento_no_gravado`

---

## 7) Pipeline de extracción de información

En paralelo con la clasificación, se ejecutan dos prompts independientes sobre el mismo Markdown OCR.

### Script

```bash
python extraction_pipeline.py documento.md
```

### Secuencia

1. `10`: extracción genérica key-value.
2. `11`: extracción específica de comprobante/factura.

### Importante

- Los resultados de 10 y 11 no se pasan entre sí.
- Ambos se guardan por separado.
- El archivo `_extraction.json` se actualiza después de cada etapa.

### Ejemplos

```bash
# Un documento
python extraction_pipeline.py documento.md

# Todos los Markdown de una carpeta
python extraction_pipeline.py files/2025-08

# Guardar todos los resultados en un JSON único
python extraction_pipeline.py files/2025-08 -o extracciones_2025_08.json
```

---

## 8) Pipeline completo

La etapa final integra todo el flujo end-to-end:

1. OCR del documento.
2. Generación del Markdown.
3. Extracción independiente 10 y 11.
4. Clasificación 01, 02 y 03.

### Script

```bash
python full_pipeline.py imagen.jpg
python full_pipeline.py files/2025-08
```

### Funcionalidades

- Procesa imágenes o PDFs de principio a fin.
- Genera un `.md` y un archivo `_pipeline.json` junto al documento.
- El JSON final contiene `extracciones` y `clasificacion`.
- Se puede reanudar por checkpoints.
- `--force` vuelve a procesar OCR y todas las etapas.
- `--workers N` permite paralelizar el procesamiento.

### Ejemplos

```bash
# Una imagen
python full_pipeline.py imagen.jpg

# Un directorio completo
python full_pipeline.py files/2025-08

# Elegir orientación y condición impositiva
python full_pipeline.py files/2025-08 --orientation horizontal --condicion-impositiva 21

# Procesar varias imágenes en paralelo
python full_pipeline.py files/2025-08 --workers 4

# Guardar resultados agregados en un JSON
python full_pipeline.py files/2025-08 -o resultados_completos.json

# Reprocesar todo ignorando checkpoints
python full_pipeline.py files/2025-08 --force
```

---

## 9) Pseudocódigo cronológico del pipeline

```text
1. Identificar el tipo de archivo y elegir motor de entrada.
2. Si es PDF o imagen, preparar la data para OCR.
3. Ejecutar OCR con Docling y generar Markdown base.
4. Ordenar los boxes detectados por posición visual.
5. Detectar orientación de la página/comprobante.
6. Detectar si hay comprobante.
7. Si no hay comprobante: finalizar.
8. Si hay comprobante:
   8.1. detectar orientación,
   8.2. detectar tablas,
   8.3. preparar estructura visual para extracción.
9. Extraer datos con prompts 10 y 11.
10. Clasificar contablemente con prompts 01 -> 02 -> 03.
11. Consolidar resultados en JSON final del pipeline.
12. Guardar checkpoints para reanudar y evitar re-ejecución innecesaria.
```

---

## 10) Estructura principal del proyecto

```text
ocr_documents.py       # OCR y generación de Markdown
run_raw.py             # OCR bruto sin ordenamiento
document_extraction.py # extracción de datos y clasificación con Ollama
classification_pipeline.py # 01 -> 02 -> 03 en secuencia
extraction_pipeline.py # 10 y 11 en paralelo
full_pipeline.py       # OCR + extracción + clasificación
lib/converter.py       # configuración de Docling
lib/orientation.py     # orientación, boxes y ordenamiento
lib/processor.py       # conversión, workers y recorrido recursivo
prompts/               # templates YAML de prompts
files/                 # documentos originales o generados
```

## Conclusión

El pipeline en v2 refleja una evolución clara y cronológica:

- primero se procesa el documento,
- luego se normaliza su estructura visual,
- después se extraen datos con prompts especializados,
- y finalmente se clasifican los datos contables y se consolida el resultado final.

La lógica principal es que cada capa se construye sobre la anterior: OCR → ordenamiento → extracción → clasificación → consolidación.

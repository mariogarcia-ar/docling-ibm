# IBM Docling

Procesamiento de imágenes y PDFs con OCR usando Docling. El sistema genera archivos Markdown con el texto detectado, ordenado según la posición de sus boxes.

## Requisitos

- Python 3.13 o compatible
- Docling
- RapidOCR y sus modelos
- Entorno virtual con las dependencias instaladas

## Procesamiento OCR

El script principal es `ocr_documents.py`:

```bash
python ocr_documents.py files
```

Por defecto, procesa recursivamente las imágenes y PDFs dentro de `files` y guarda un `.md` junto a cada archivo original.

### Opciones

```bash
# Procesar una carpeta específica
python ocr_documents.py files/2025-08

# Sobrescribir Markdown existentes
python ocr_documents.py files --force

# Procesar en paralelo
python ocr_documents.py files --workers 4

# Extraer sólo texto horizontal
python ocr_documents.py files --orientation horizontal

# Extraer sólo texto vertical
python ocr_documents.py files --orientation vertical

# Detectar automáticamente la orientación predominante
python ocr_documents.py files --orientation auto

# Guardar los resultados en otra carpeta
python ocr_documents.py files --output output
```

Sin `--force`, los archivos Markdown existentes se omiten.

## Ordenamiento de boxes

Para cada elemento detectado:

1. Se obtiene su texto y `bbox`.
2. En orientación horizontal, los boxes se agrupan por `center_y`.
3. En orientación vertical, se agrupan por `center_x`.
4. Los elementos de cada línea o columna se ordenan por su posición.
5. Los campos se separan con `|` en el Markdown.

Las tablas detectadas por Docling se conservan como tablas Markdown.

## Extracción estructurada

El script `document_extraction.py` utiliza Ollama para convertir un Markdown OCR en JSON:

```bash
python document_extraction.py files/2025-08/2D2C9343/resultado.md -M kvi
python document_extraction.py files/2025-08/2D2C9343/resultado.md -M kvg
```

Modos disponibles:

- `kvi`: extracción orientada a comprobantes y facturas.
- `kvg`: extracción genérica para cualquier documento.
- `01` o `ccc`: clasifica hasta tres centros de costo.
- `02` o `mcc`: clasifica la macro categoría; requiere `--centro-costo`.
- `03` o `cfc`: determina concepto y código final; requiere
	`--macro-categoria` y `--condicion-impositiva`.

Se puede indicar otro modelo o guardar la respuesta en un archivo:

```bash
python document_extraction.py documento.md -M kvi -m qwen2.5vl:3b -o resultado.json

# Clasificación contable en pasos
python document_extraction.py documento.md -M 01
python document_extraction.py documento.md -M 02 --centro-costo CC0006
python document_extraction.py documento.md -M 03 --macro-categoria MC07 --condicion-impositiva 21%

# Procesar todos los Markdown de una carpeta
python document_extraction.py files/2025-08 -M 01 -o centros_costos.json
```

Ollama debe estar disponible en `http://localhost:11434`.

## Pipeline de clasificación

`classification_pipeline.py` ejecuta los prompts contables en secuencia para
cualquier Markdown generado por OCR:

1. `01`: devuelve hasta tres centros de costo.
2. `02`: recibe el primer centro de costo y devuelve hasta tres macro categorías.
3. `03`: recibe la primera macro categoría y devuelve concepto y código final.

El resultado conserva las respuestas de cada paso para su evaluación:

```bash
# Un documento
python classification_pipeline.py documento.md \
	--condicion-impositiva 21 \
	-o classification_results.json

# Todos los Markdown de una carpeta, de forma recursiva
python classification_pipeline.py files/2025-08 \
	--condicion-impositiva 10_5 \
	-o clasificaciones_2025_08.json

# Usar otro modelo de Ollama
python classification_pipeline.py documento.md \
	--model qwen2.5vl:3b \
	-o resultado.json
```

La condición impositiva acepta `21`, `10_5`, `27`, `2_5` o
`exento_no_gravado`. Si un paso falla, el JSON conserva los pasos completados
y registra el error en el documento correspondiente.

Formato resumido del resultado:

```json
[
	{
		"archivo": "files/2025-08/documento.md",
		"pasos": {
			"01_centro_costo": {},
			"02_macro_categoria": {},
			"03_concepto_codigo_final": {}
		}
	}
]
```

## Estructura principal

```text
ocr_documents.py       # CLI para procesamiento OCR recursivo
document_extraction.py # Extracción y clasificación con Ollama
classification_pipeline.py # Pipeline secuencial 01 -> 02 -> 03
lib/converter.py       # Configuración de Docling
lib/orientation.py     # Orientación, boxes y ordenamiento
lib/processor.py       # Conversión, workers y recorrido recursivo
prompts/               # Templates YAML para extracción
files/                 # Imágenes, PDFs y Markdown generado
```

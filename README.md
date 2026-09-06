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

Para obtener el Markdown crudo generado por Docling, sin el filtro de
orientación de `run.py`/`ocr_documents.py`, usá `run_raw.py`:

```bash
python run_raw.py --input files/2025-08/2E1F7D6C/documento.jpg
python run_raw.py --input documento.pdf --output documento_raw.md
```

Por defecto, guarda el resultado junto al archivo de entrada con el sufijo
`.raw.md`.

`--workers N` crea hasta `N` procesos para procesar imágenes en paralelo.
Cada proceso inicializa su propio convertidor Docling. Usá `--workers 1` para
procesamiento secuencial. Si quedan workers huérfanos, ejecutá:

```bash
python kill_workers.py
./kill_workers.sh
```

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
python document_extraction.py files/2025-08/2D2C9343/resultado.md -M 11.1
```

Modos disponibles:

- `kvi`: extracción orientada a comprobantes y facturas.
- `11.1`: detección del tipo de documento y letra del comprobante mediante el
	prompt visual de facturación.
- `kvg`: extracción genérica para cualquier documento.
- `01` o `ccc`: clasifica hasta tres centros de costo.
- `02` o `mcc`: clasifica la macro categoría; requiere `--centro-costo`.
- `03` o `cfc`: determina concepto y código final; requiere
	`--macro-categoria` y `--condicion-impositiva`.

Se puede indicar otro modelo o guardar la respuesta en un archivo:

```bash
python document_extraction.py documento.md -M kvi -m qwen2.5vl:3b -o resultado.json

# Forzar análisis de texto (system_llm)
python document_extraction.py documento.md -M 11.1 --modality llm

# Forzar análisis visual de imagen (system_vlm)
python document_extraction.py documento.jpg -M 11.1 --modality vlm

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
cualquier Markdown generado por OCR. Acepta un archivo individual o un
directorio; al recibir un directorio recorre todas sus subcarpetas y procesa
todos los archivos `.md` encontrados:

1. `01`: devuelve hasta tres centros de costo.
2. `02`: recibe el primer centro de costo y devuelve hasta tres macro categorías.
3. `03`: recibe la primera macro categoría y devuelve concepto y código final.

El resultado conserva las respuestas de cada paso para su evaluación:

Sin `-o`, crea un archivo `_classification.json` junto a cada Markdown.
Ese archivo se actualiza después de cada paso `01`, `02` y `03`, y permite
reanudar una ejecución interrumpida. Con `-o`, guarda todos los resultados en
un único JSON y lo actualiza después de cada Markdown.

```bash
# Un documento
python classification_pipeline.py documento.md \
	--condicion-impositiva 21
```

El comando anterior genera `documento_classification.json` junto al Markdown.

```bash
# Todos los Markdown de una carpeta, de forma recursiva; crea sidecars
python classification_pipeline.py files/2025-08 \
	--condicion-impositiva 10_5

# Guardar todos los resultados en un único JSON
python classification_pipeline.py files/2025-08 \
	--condicion-impositiva 21 \
	-o classification_results.json

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

## Pipeline de extracción

`extraction_pipeline.py` aplica dos prompts independientes al mismo Markdown
OCR. Acepta un archivo individual o un directorio; al recibir un directorio
recorre todas sus subcarpetas y procesa todos los archivos `.md` encontrados:

1. `10`: extracción genérica key-value.
2. `11`: extracción específica de comprobantes y facturas.

No pasa datos de 10 a 11 ni de 11 a 10. Guarda ambas respuestas separadas.
El archivo `_extraction.json` se actualiza después de 10 y después de 11.
Con `-o`, el JSON agregado se actualiza después de cada Markdown.

```bash
# Un documento; crea documento_extraction.json junto al Markdown
python extraction_pipeline.py documento.md

# Todos los Markdown de una carpeta, de forma recursiva; crea sidecars
python extraction_pipeline.py files/2025-08

# Usar otro modelo de Ollama
python extraction_pipeline.py documento.md --model qwen2.5vl:3b

# Opcional: guardar todos los resultados en un único JSON
python extraction_pipeline.py files/2025-08 \
	-o extracciones_2025_08.json
```

Formato resumido:

```json
[
	{
		"archivo": "files/2025-08/documento.md",
		"extracciones": {
			"10_extraccion_generica": {},
			"11_extraccion_factura": {}
		}
	}
]
```

## Pipeline completo

`full_pipeline.py` procesa imágenes o PDFs de principio a fin:

1. Ejecuta OCR y genera el Markdown junto a la imagen.
2. Aplica 10 y 11 de forma independiente sobre ese Markdown.
3. Aplica 01, 02 y 03 en secuencia sobre el mismo Markdown.

Los resultados de 10/11 no se pasan a 01/02/03. Sin `-o`, genera un JSON
`_pipeline.json` junto a cada imagen:

```bash
# Una imagen
python full_pipeline.py imagen.jpg

# Una carpeta y todas sus subcarpetas
python full_pipeline.py files/2025-08

# Elegir orientación y condición impositiva
python full_pipeline.py files/2025-08 \
	--orientation horizontal \
	--condicion-impositiva 21

# Procesar varias imágenes en paralelo
python full_pipeline.py files/2025-08 --workers 4

# Guardar todos los resultados en un único JSON
python full_pipeline.py files/2025-08 \
	-o resultados_completos.json

# Ignorar checkpoints y reprocesar OCR y todos los pasos
python full_pipeline.py files/2025-08 --force
```

Para una imagen `documento.jpg`, el pipeline genera:

```text
documento.md
documento_pipeline.json
```

El JSON contiene `extracciones` con 10/11 y `clasificacion` con 01/02/03.
Después de cada etapa se actualiza el archivo `_pipeline.json`, por lo que una
ejecución interrumpida puede continuar sin repetir los pasos ya completados.
La opción `--force` ignora el checkpoint y reprocesa OCR y todos los pasos.
`--workers N` procesa varias imágenes en paralelo, con un convertidor Docling
por worker. Los checkpoints de cada imagen se mantienen independientes y el
JSON agregado se actualiza cuando termina cada imagen.

## Estructura principal

```text
ocr_documents.py       # CLI para procesamiento OCR recursivo
document_extraction.py # Extracción y clasificación con Ollama
classification_pipeline.py # Pipeline secuencial 01 -> 02 -> 03
extraction_pipeline.py # Extracciones independientes 10 y 11
full_pipeline.py       # OCR + extracción 10/11 + clasificación 01/02/03
lib/converter.py       # Configuración de Docling
lib/orientation.py     # Orientación, boxes y ordenamiento
lib/processor.py       # Conversión, workers y recorrido recursivo
prompts/               # Templates YAML para extracción
files/                 # Imágenes, PDFs y Markdown generado
```

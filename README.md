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

El script `extract.py` utiliza Ollama para convertir un Markdown OCR en JSON:

```bash
python extract.py files/2025-08/2D2C9343/resultado.md -M kvi
python extract.py files/2025-08/2D2C9343/resultado.md -M kvg
```

Modos disponibles:

- `kvi`: extracción orientada a comprobantes y facturas.
- `kvg`: extracción genérica para cualquier documento.

Se puede indicar otro modelo o guardar la respuesta en un archivo:

```bash
python extract.py documento.md -M kvi -m qwen2.5vl:3b -o resultado.json
```

Ollama debe estar disponible en `http://localhost:11434`.

## Estructura principal

```text
ocr_documents.py       # CLI para procesamiento recursivo
extract.py             # Extracción estructurada con Ollama
lib/converter.py       # Configuración de Docling
lib/orientation.py     # Orientación, boxes y ordenamiento
lib/processor.py       # Conversión, workers y recorrido recursivo
prompts/               # Templates YAML para extracción
files/                 # Imágenes, PDFs y Markdown generado
```

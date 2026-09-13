# Docling — algoritmo general para procesar distintos tipos de documentos

## Objetivo

Procesar cualquier documento de entrada siguiendo una estrategia según su tipo, su calidad visual y la forma en que se puede extraer texto de forma más confiable.

## Esquema general

```text
funcion procesar_documento(documento):

    tipo = detectar_tipo_documento(documento)

    si tipo == pdf_texto_extraible:
        texto = extraer_texto_pdf(documento)
        return normalizar_y_estructurar(texto)

    si tipo == pdf_escaneado:
        imagen = convertir_pdf_a_imagen(documento)
        return procesar_imagen(imagen)

    si tipo == imagen:
        return procesar_imagen(documento)

    si tipo == office_word:
        texto = convertir_docx_a_texto(documento)
        return normalizar_y_estructurar(texto)

    si tipo == office_excel:
        texto = convertir_xlsx_a_tabla(documento)
        return normalizar_y_estructurar(texto)

    si tipo == office_powerpoint:
        texto = extraer_texto_presentacion(documento)
        return normalizar_y_estructurar(texto)

    si tipo == texto_plano:
        texto = leer_archivo_texto(documento)
        return normalizar_y_estructurar(texto)

    si tipo == csv:
        tabla = leer_csv(documento)
        return normalizar_y_estructurar(tabla)

    si tipo == log:
        texto = parsear_log(documento)
        return normalizar_y_estructurar(texto)

    si tipo == html_o_markdown:
        texto = extraer_contenido_semantico(documento)
        return normalizar_y_estructurar(texto)

    si tipo == archivo_no_soportado:
        retornar rechazar_o_reencolar(documento)

    retornar rechazar_o_reencolar(documento)
```

## Subrutina principal: procesar_imagen

```text
funcion procesar_imagen(imagen):

    si NO es_imagen_aceptable(imagen):
        imagen = preprocesar_imagen(imagen)

    clase = detectar_clase_de_imagen(imagen)

    si clase == fotografia_de_documento:
        imagen = enderezar_perspectiva(imagen)

    si clase == escaneo_plano:
        imagen = mantener_formato_original(imagen)

    si clase == captura_digital_o_screenshot:
        imagen = extraer_directamente(imagen)

    orientacion = detectar_orientacion(imagen)
    si orientacion requiere_rotacion:
        imagen = rotar(imagen, orientacion)

    motor = elegir_motor_ocr(imagen, clase)

    si motor == ocr_tradicional:
        texto = ejecutar_docling_ocr(imagen)
    si motor == vlm:
        texto = ejecutar_modelo_visual(imagen)

    return normalizar_y_estructurar(texto)
```

## Regla de decisión por tipo de documento

### 1) PDF con texto extraíble

- Se intenta extraer el texto directamente.
- Es la ruta más simple y eficiente.
- No requiere OCR visual si la lectura del PDF es válida.

### 2) PDF escaneado

- Se convierte a imagen.
- Luego se aplica OCR o visión sobre la imagen generada.

### 3) Imagen estática

- Se valida la calidad visual.
- Si la resolución es baja, se realiza preprocesamiento.
- Luego se decide si conviene OCR tradicional o modelo visual.

### 4) Archivos de Microsoft Office

- `DOCX`: se extrae texto y estructura de documento.
- `XLSX`: se convierte a tabla o filas/columnas estructuradas.
- `PPTX`: se extrae texto de diapositivas y su orden semántico.

### 5) Texto plano, CSV, logs, HTML y Markdown

- `TXT`: se leen directamente como texto.
- `CSV`: se interpretan como tablas y se convierten a estructura tabular.
- `LOG`: se parsean líneas de evento o registro para extraer contenido útil.
- `HTML` y `Markdown`: se leen desde su formato nativo y se normalizan para mantener estructura, títulos, listas y tablas.

### 6) Otros formatos no soportados

- Se rechazan o reencolan para revisión o soporte manual.
- La idea es no intentar forzar un formato que no puede procesarse confiablemente.

## Clasificación de la imagen

Antes de OCR, se intenta clasificar la imagen en una de estas categorías:

- fotografía de documento
- escaneo plano
- captura digital o screenshot
- imagen con texto manuscrito, sello o firma

### Reglas

- Si hay perspectiva notable: enderezar.
- Si la imagen es un documento plano: mantener sin filtros innecesarios.
- Si hay texto difícil de leer con OCR clásico: usar modelo visual.
- Si hay firmas, sellos o marcas manuscritas: priorizar análisis visual.

## Calidad y preprocesamiento

```text
si resolucion_baja o contraste_pobre o ruido_alto:
    imagen = mejorar_calidad(imagen)
    imagen = binarizar_si_hace_falta(imagen)
    imagen = aumentar_resolucion(imagen)
```

Esto mejora la lectura del OCR y reduce errores de extracción.

## Orientación

El documento debe orientarse visualmente antes de extraer contenido.

```text
orientacion = detectar_orientacion(imagen)
si orientacion != correcta:
    imagen = rotar(imagen, orientacion)
```

La orientación es clave porque influye directamente en el orden de lectura y en la estructura final del texto.

## Elección del motor

El motor se elige según el contenido y la complejidad visual.

### OCR tradicional

Usar cuando:

- el texto es impreso y legible,
- el documento es un escaneo limpio,
- la estructura es estándar y predecible.

### Modelo visual (VLM)

Usar cuando:

- hay texto manuscrito,
- hay firma o sello,
- hay elementos visuales complejos,
- el OCR clásico no devuelve una lectura estable.

## Orden de salida

Una vez extraído el texto, se ordena por posición visual:

- en orientación horizontal: por `center_y`
- en orientación vertical: por `center_x`
- dentro de cada línea o columna: por posición relativa

Esto permite producir un Markdown legible y consistente con la estructura original del documento.

## Resultado final

El algoritmo general sigue esta lógica:

1. identificar el tipo de documento
2. elegir la ruta de procesamiento correcta
3. extraer texto nativo si el formato lo permite
4. convertir a imagen si el documento es PDF escaneado o imagen
5. evaluar calidad visual y aplicar preprocesamiento si hace falta
6. detectar orientación
7. elegir OCR o VLM según la complejidad visual
8. extraer texto
9. ordenar la salida por posición
10. devolver una representación estructurada y legible

## En una frase

El procesamiento de documentos se decide por tipo de archivo, formato de origen, calidad visual, orientación y complejidad del contenido; la extracción se adapta según el mejor motor disponible para cada caso.

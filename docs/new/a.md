Este es el diseño de flujo abstracto puramente basado en contenido de texto. A diferencia del enfoque multimodal, este pipeline ignora por completo los elementos visuales, logotipos o el diseño gráfico del documento.
Su estrategia principal consiste en transformar cualquier imagen o PDF en una cadena de caracteres lineal y homogénea (String), para luego aplicar técnicas de Procesamiento de Lenguaje Natural (NLP) y Expresiones Regulares (Regex) con anclaje semántico para extraer la información de forma sólida, rápida y económica.
------------------------------

[Comprobante] ➔ [OCR Lineal] ➔ [Normalización de Texto] ➔ [Análisis de Contexto Semántico] ➔ [Motores de Extracción] ➔ [Validación] ➔ [Trazabilidad]

------------------------------
## 1. Ingesta y OCR Lineal (Serialización Estricta)
El objetivo de este primer paso es destruir la naturaleza bidimensional de la foto o el PDF y convertirla en un flujo unidimensional de texto.

* Extracción Directa / OCR: Si es un PDF nativo, se extrae el flujo de texto digital. Si es una foto o un PDF escaneado, se pasa por un motor OCR.
* Algoritmo de Reconstrucción de Líneas: Para evitar que el texto se mezcle al digitalizarse, el motor ordena las palabras de izquierda a derecha y de arriba a abajo. Al finalizar cada fila física del documento, inyecta un salto de línea estricto (\n). El resultado es un único bloque de texto plano reproducible.

## 2. Normalización de Texto (Limpieza de Caracteres)
Las variaciones en la escritura o los errores menores del OCR pueden hacer fallar la búsqueda. En esta fase se homogeniza el texto plano obtenido:

* Estandarización de Caja y Acentos: Se convierte todo el texto a minúsculas (o mayúsculas) y se eliminan los acentos o diéresis para evitar problemas de codificación.
* Limpieza de Ruido Textual: Se eliminan caracteres especiales huérfanos generados por suciedad de la foto (por ejemplo, puntos sueltos, guiones o símbolos como ~, | o _ que el OCR interpretó erróneamente de las líneas del papel).
* Colapso de Espacios: Se reducen los espacios en blanco múltiples a un único espacio ( ) para estabilizar los patrones de búsqueda.

## 3. Análisis de Contexto Semántico (Ubicación de Anclas)
Como no tenemos coordenadas visuales para saber dónde están los datos, el pipeline escanea el texto buscando palabras clave invariables o "Anclas Semánticas".

* Diccionarios de Sinónimos: El sistema busca términos clave definidos para cada variable que se desea extraer. Por ejemplo, para encontrar el monto final, busca palabras ancla como total, neto a pagar, importe total o monto de la operacion.
* Identificación de Bloques: El script segmenta la cadena de texto larga en sub-bloques basados en estas palabras clave para acotar la búsqueda y evitar falsos positivos en otras secciones del documento.

## 4. Motores de Extracción (Regex Flotantes y NER)
Una vez localizadas las anclas en el texto plano, se ejecutan las reglas lógicas para capturar el valor exacto:

* Expresiones Regulares (Regex) con Lookbehinds: Se configura la regla para que ignore el ancla y capture el patrón de caracteres específico que se encuentra inmediatamente a la derecha o en la línea de abajo.
* Ejemplo: Si el texto normalizado dice total facturado 15400.00, la Regex busca la frase total facturado y extrae el patrón numérico subsiguiente \d+[\.,]\d{2}.
* Reconocimiento de Entidades (NER Basado en Texto): Si el dato no tiene una palabra clave fija (como el nombre de un proveedor), un modelo de lenguaje ligero (como un modelo basado en BERT o reglas de gramática) analiza el contexto gramatical para identificar nombres propios o razones sociales.

## 5. Validación Lógica y Aritmética
Dado que el pipeline opera a ciegas (sin ver el diseño del comprobante), la validación matemática es el filtro crítico para asegurar que el texto extraído sea el correcto:

* Verificación de Tipo de Dato (Parsing): Los fragmentos de texto extraídos se transforman a tipos nativos del sistema (el texto "15400.00" pasa a ser un número decimal, "16/09/2026" pasa a ser un objeto de tipo Fecha).
* Consistencia Aritmética: Se aplican reglas de negocio cruzadas. Si el motor extrajo tres importes diferentes, se verifica matemáticamente cuál de ellos es la suma de los otros (confirmando cuál es el subtotal, el impuesto y el total real). Si la matemática no cierra, el registro se rechaza.
* Bucle Humano (HITL): Cualquier inconsistencia textual deriva el caso a una cola de revisión para que un operador verifique el texto original.

## 6. Trazabilidad de Origen Indexada (Data Lineage)
La auditoría en un enfoque puramente textual se maneja mediante punteros e índices de caracteres:

* Índices de Coincidencia (Offset): El sistema almacena el valor extraído junto con la posición exacta del caracter de inicio y fin (Start_Index y End_Index) dentro de la cadena de texto original normalizada.
* Registro de Reglas: Se guarda de forma estricta qué patrón Regex o qué regla semántica logró realizar la captura del dato.
* Mapeo Inverso a la Interfaz: Al auditar el dato, el sistema resalta el párrafo o la línea de texto plano de donde se tomó la información, permitiendo al usuario contrastar rápidamente la cadena de texto cruda contra el dato guardado en la base de datos.

Para avanzar en el diseño de tu sistema, contame:

* ¿Qué lenguaje de programación (como Python, Node.js o C#) vas a utilizar para programar la lógica de este flujo de texto?
* ¿Te interesaría que prepare un ejemplo práctico con código de las expresiones regulares (Regex) con anclas flotantes para extraer importes y fechas?



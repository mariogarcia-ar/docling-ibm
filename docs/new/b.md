Este es el diseño de flujo abstracto puramente basado en contenido visual. A diferencia de los enfoques anteriores, este pipeline no realiza lectura tradicional de caracteres (OCR) ni procesa texto.
En su lugar, trata a todo el comprobante como un mapa bidimensional de píxeles y formas. Utiliza algoritmos de Visión por Computación (Computer Vision) y Redes Neuronales Convolucionales (CNN) / Vision Transformers (ViT) para clasificar, segmentar, detectar firmas, validar marcas de verificación (checkboxes) y extraer información a través de patrones de diseño gráfico.
------------------------------

[Foto / PDF] ➔ [Normalización Geométrica] ➔ [Segmentación de Regiones] ➔ [Modelos de Detección de Objetos] ➔ [Clasificación Visual] ➔ [Validación] ➔ [Trazabilidad]

------------------------------
## 1. Ingesta y Normalización Geométrica Avanzada
Dado que el pipeline depende exclusivamente de la posición y la forma de los elementos visuales, cualquier distorsión arruinaría el análisis.

* Alineación Estricta de Perspectiva (Warp Perspective): Se detectan automáticamente las cuatro esquinas del documento mediante algoritmos de gradiente de píxeles y se proyectan sobre un plano cenital rectangular perfecto.
* Escalado a Matriz Fija (Aspect Ratio): El documento se redimensiona a una resolución exacta (por ejemplo, 1024x1024 píxeles). Cada coordenada física de la imagen se convierte en un punto de una matriz matemática estandarizada.
* Mejora de Contraste Local (CLAHE): Se normaliza la iluminación para equilibrar zonas sobreexpuestas (con flash) o con sombras, asegurando que los bordes de los recuadros sean nítidos.

## 2. Segmentación de Diseño y Regiones (Layout Segmentation)
El sistema divide la imagen del comprobante en bloques lógicos basándose en la geometría y la textura visual de las áreas.

* Detección de Tablas y Grillas: Algoritmos morfológicos detectan la intersección de líneas horizontales y verticales para aislar la región de los ítems o filas, sin importar lo que esté escrito adentro.
* Aislamiento de Cajas de Texto (Bounding Boxes): Se localizan todos los "parches" o regiones donde hay presencia de tinta (texto o números), tratándolos como objetos visuales individuales con coordenadas [X, Y, Ancho, Alto].

## 3. Modelos de Detección de Objetos (Object Detection)
Se emplean arquitecturas de redes neuronales (como YOLO, Faster R-CNN o LayoutLM de solo visión) entrenadas para reconocer componentes específicos de los comprobantes como si fueran objetos independientes:

* Localización de Componentes Críticos: El modelo encuadra visualmente zonas específicas: ZONA_TOTAL, ZONA_FECHA, ZONA_LOGOTIPO, ZONA_CODIGO_QR.
* Detección de Firmas y Sellos: Un detector específico escanea la parte inferior del documento buscando patrones de trazos manuscritos o formas circulares/rectangulares de tinta de color (sellos).

## 4. Clasificación y Extracción Visual de Características (Feature Extraction)
En este punto, el sistema extrae significado sin decodificar letras:

* Reconocimiento de Logotipos (Brand Classification): Una CNN compara el logotipo extraído con una base de datos de imágenes corporativas para identificar instantáneamente al proveedor (ej. identifica el logo de "YPF" o "Shell" visualmente, asignando el nombre del proveedor por clasificación gráfica).
* Detección de Estado de Casillas (Checkbox State): El sistema analiza la densidad de píxeles negros dentro de un recuadro pequeño. Si el recuadro está vacío (píxeles blancos), clasifica el estado como FALSO. Si tiene un trazo o cruz (píxeles negros), lo clasifica como VERDADERO.

## 5. Validación Visual de Negocio
El control de calidad se realiza verificando la consistencia e integridad de las formas del documento:

* Validación de Presencia: El sistema verifica que todos los objetos mandatorios existan visualmente. Si el documento requiere una firma para ser válido, el clasificador de firmas debe devolver un score de presencia mayor al 90%.
* Integridad Estructural: Se comprueba que el diseño detectado coincida con la plantilla maestra del comprobante clasificado (por ejemplo, que la zona del código QR esté en la esquina esperada y no alterada).
* Bucle Humano (HITL): Si falta algún componente visual crítico o el nivel de confianza de la detección de objetos es bajo, el archivo se envía a una bandeja de revisión visual humana.

## 6. Trazabilidad Espacial y Mapas de Calor (Data Lineage)
La auditoría de un flujo visual prescinde del texto y se enfoca en el rastro gráfico:

* Coordenadas de Anclaje de Objetos: En la base de datos se almacena el ID del objeto detectado junto con su recorte o coordenadas absolutas en la matriz de la imagen original.
* Mapas de Activación (Grad-CAM): Se guardan los mapas de calor de la red neuronal. Esto permite registrar exactamente qué píxeles de la imagen hicieron que el modelo decidiera, por ejemplo, que esa zona correspondía a la firma o al logotipo.
* Renderizado de Auditoría Gráfica: Cuando un usuario inspecciona el proceso, el sistema superpone capas de color de manera interactiva directamente sobre la foto original, mostrando el recuadro verde para objetos aprobados, rojo para zonas sospechosas y azul para marcas verificadas.

Si estás evaluando implementar o comparar estos modelos independientes, decime:

* ¿El objetivo principal de tu caso de uso visual es detectar elementos gráficos (como firmas, sellos o códigos QR) o clasificar tipos de formularios por su diseño?
* ¿Cuentas con un servidor con GPU para entrenar redes convolucionales (como YOLO) o prefieres soluciones integradas de visión?



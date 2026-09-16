Este es el diseño de flujo abstracto puramente mixto o multimodal, el enfoque más robusto y moderno para resolver la extracción en documentos complejos o fotos de celulares.
A diferencia de los enfoques aislados, este pipeline no procesa el texto y la imagen por separado como islas independientes. Su núcleo estratégico radica en la fusión de características (Feature Fusion) en las primeras etapas del pipeline: cada palabra detectada se enriquece en tiempo real con su significado lingüístico (texto), su ubicación geográfica en el lienzo (coordenadas) y la textura visual de los píxeles que la rodean (imagen).
------------------------------

                               ┌──► [1. Rama Texto: Semántica de caracteres] ──┐
[Comprobante] ➔ [Preproc] ➔ [OCR]                                              ├─► [3. Fusión Multimodal] ➔ [4. Extracción NER] ➔ [5. Validación] ➔ [6. Trazabilidad]
                               └──► [2. Rama Visual: Geometría y Píxeles] ─────┘

------------------------------
## 1. Ingesta, Preprocesamiento y OCR Base
El documento (foto, PDF o escaneo) entra al sistema y se unifica en una capa digital base:

* Normalización Lumínica y Geométrica: Se aplica una corrección de perspectiva (Warp) si es una foto y se estandariza el lienzo a una resolución fija (ej. 1000x1000 píxeles).
* Capa OCR Espacial: Un motor de lectura extrae las palabras, pero retiene estrictamente el Bounding Box (caja delimitadora) de cada una, generando un mapa de coordenadas [X, Y, Ancho, Alto] para cada token.

## 2. Extracción de Características en Paralelo (Bifurcación)
El pipeline divide la información en dos capas de análisis profundo que corren simultáneamente:
## Rama A: Contenido de Texto (Semántica)

* Embeddings de Texto: Las palabras extraídas por el OCR se pasan por un modelo de lenguaje ligero (como BERT). Este modelo traduce el texto a vectores matemáticos densos que representan su significado conceptual. El sistema entiende que "Subtotal", "Neto" o "Base Imponible" ocupan el mismo espacio semántico de negocio.

## Rama B: Contenido Visual (Contexto Gráfico)

* Embeddings Visuales: La imagen completa del documento se pasa por una Red Neuronal Convolucional (CNN) o un Vision Transformer (ViT). El modelo no lee letras; analiza la densidad de píxeles para identificar parches visuales: líneas divisorias de tablas, recuadros de formularios, la presencia de una firma manuscrita, un sello o un código de barras.

## 3. El Núcleo: Fusión Multimodal (LayoutLM / Graph NN)
Este es el punto diferencial del enfoque mixto. El sistema toma cada palabra y fusiona sus características sumando tres vectores en un único "súper-vector":
$$\text{Vector Unido} = \text{Embedding de Texto (Qué dice)} + \text{Posición 2D (Dónde está)} + \text{Embedding Visual (Cómo se ve el entorno)}$$ 

El superpoder del enfoque mixto: Si el OCR lee el texto 21%, la rama de texto por sí sola no sabe si es un descuento o un impuesto. La rama visual detecta que está dentro de una cuadrícula en la parte inferior. Al fusionarse, el sistema cruza que dice 21%, que visualmente está en una celda de tabla y que está a la derecha de la palabra "IVA". El pipeline deduce con precisión matemática que se trata de la alícuota del impuesto.

## 4. Extracción Semántica-Espacial (NER 2D)
Con los vectores fusionados que contienen texto, posición e imagen, el motor de extracción (clasificador de entidades o modelo autorregresivo) realiza la captura de datos sin depender de coordenadas rígidas:

* Extracción de Entidades Clave: El modelo etiqueta los bloques de información directamente (ej. FACTURA_NUMERO, FECHA_EMISION, MONTO_TOTAL), tolerando que el comprobante esté movido o tenga un diseño nunca antes visto por el sistema.
* Estructuración de Tablas por Contexto: La rama visual aporta las fronteras de las celdas (líneas visibles o invisibles) y la rama de texto rellena el JSON estructurado con los artículos, cantidades y precios correspondientes sin mezclar las filas.

## 5. Validación Cruzada Unificada
El control de calidad se ejecuta cruzando las reglas de negocio del texto con las evidencias de la imagen:

* Validación Lógica (Texto): Se corren scripts matemáticos deterministas para verificar que la suma de los ítems y los impuestos coincida exactamente con el total extraído.
* Validación de Integridad (Visual): Se comprueba que, si el estado del documento requiere estar "Aprobado", exista físicamente el píxel de la firma manuscrita o el sello detectado por la rama visual.
* Bucle Humano (HITL): Si el score de confianza combinado del modelo mixto es bajo (ej. < 85%) o las validaciones aritméticas fallan, el documento se deriva a una interfaz de revisión humana.

## 6. Trazabilidad Multimodal Completa (Data Lineage)
La auditoría de este enfoque ofrece el nivel más alto de justificación del dato:

* Anclaje de Coordenadas Indexadas: En la base de datos, cada registro final (ej. Total = 15400.00) queda guardado junto a un puntero JSON con su ubicación espacial exacta y el ID del modelo mixto utilizado.
* Renderizado de Auditoría Interactivo: Cuando un auditor interno hace clic sobre un campo en el sistema de gestión, la interfaz web abre la foto original y dibuja un cuadro de color (Bounding Box) directamente sobre la región exacta de la imagen donde los vectores de texto y visión hicieron match, permitiendo una verificación visual inmediata y transparente.

Si estás evaluando el diseño de la arquitectura para tu proyecto, contame:

* ¿Tienen preferencia por utilizar servicios en la nube (como Azure Document Intelligence o AWS Textract) o prefieren implementar modelos Open Source (como LayoutLM de Hugging Face en Python)?
* ¿Con qué volumen estimado de comprobantes planean alimentar este pipeline (por ejemplo: cientos al mes o miles por día)?



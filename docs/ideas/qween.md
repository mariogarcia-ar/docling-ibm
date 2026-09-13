# Qwen / validación de comprobante y envío del documento

## Idea general

Antes de ejecutar la extracción completa de un comprobante, conviene separar dos decisiones:

1. validar si el documento es un comprobante o no
2. si es comprobante, preparar la evidencia para la extracción final con la menor pérdida de calidad posible

La primera validación puede hacerse con una versión más ligera del documento. La segunda debe hacerse con una versión más cuidadosa, porque ahí sí se requieren detalles visuales y textuales para extraer bien los datos.

---

## 1) Fase de validación rápida: ¿es comprobante?

En esta etapa no queremos gastar costo ni calidad de análisis. El objetivo es responder una pregunta binaria: "¿esto es un comprobante?".

### Regla sugerida

- reducir la resolución o la calidad visual del documento
- enviar una vista simplificada o thumbnail del archivo
- pedir al modelo una respuesta breve y orientada a decisión:
  - comprobante
  - no comprobante
  - no se puede determinar

### Motivación

Esta validación es un gate de entrada. Si el documento no es un comprobante, no deberíamos continuar con los pasos más costosos y más sensibles de extracción. Si es un comprobante, entonces sí pasamos al flujo principal.

### Prompt orientativo

```text
Analiza esta imagen/documento de forma rápida.
Tu tarea es decidir si corresponde a un comprobante fiscal o comercial.
Responder solo con una de estas opciones:
- comprobante
- no_comprobante
- indeterminado

No expliques demasiado. Solo decide.
```

### Recomendación operativa

- usar una versión reducida visualmente
- evitar detalles complejos, firmas, sellos y ruido en esta etapa
- priorizar decisión rápida sobre precisión fina
- si es indeterminado, enviar a revisión o a una segunda validación con mayor calidad

---

## 2) Fase de análisis real: si es comprobante, subir cuidado la calidad

Si la validación dice que sí es comprobante, entonces el documento entra al flujo principal, pero con una estrategia distinta.

### Lo importante aquí

La calidad del documento que se envía para la extracción debe ser mejor que la usada en la validación. No conviene enviar el documento en una versión demasiado degradada porque los pasos finales requieren:

- reconocer campos clave
- detectar letras, fechas, importes, CUIT, etc.
- interpretar tablas o estructuras complejas
- conservar orientación del documento
- no perder texto pequeño o detalle legible

### Recomendación

- mantener resolución suficiente
- preservar orientación correcta
- si hay perspectiva, corregir antes
- si hay ruido, limpiar con preprocesamiento leve
- si hay OCR previo, usar un documento normalizado y legible

---

## 3) Cómo enviar el documento según la etapa

### Etapa A: validación rápida

- propósito: decidir si es comprobante o no
- calidad: baja/moderada
- formato: versión simplificada / thumbnail / imagen escalada para velocidad
- prompt: binario y corto
- salida esperada: `comprobante` o `no_comprobante`

### Etapa B: extracción principal

- propósito: extraer datos del comprobante
- calidad: alta/normal
- formato: imagen original o bien una versión limpia, con orientación corregida
- prompt: detallado, con instrucciones específicas de extracción
- salida esperada: campos estructurados, no solo una respuesta genérica

### Regla práctica

No usar la misma versión del documento para ambas etapas. La validación requiere un documento barato y rápido; la extracción requiere un documento más cuidadoso y más fiel a la realidad original.

---

## 4) Flujo concreto recomendado

```text
funcion validar_y_procesar(documento):

    vista_baja = preparar_vista_rapida(documento)
    decision = modelo_decide_si_es_comprobante(vista_baja)

    si decision == no_comprobante:
        rechazar_o_reencolar(documento)
        retornar

    si decision == indeterminado:
        vista_mejorada = preparar_vista_de_revision(documento)
        decision = modelo_decide_si_es_comprobante(vista_mejorada)

        si decision == no_comprobante:
            rechazar_o_reencolar(documento)
            retornar

    documento_limpio = preparar_documento_para_extraccion(documento)
    resultado = extraer_datos_comprobante(documento_limpio)
    retornar resultado
```

---

## 5) Qué debemos cuidar en los pasos posteriores

Cuando ya sabemos que es un comprobante, el resto del pipeline debe hacerse con atención:

- no enviar la versión deformada o demasiado reducida
- no mezclar la validación rápida con la extracción final
- no pedir al modelo que resuelva todo con una imagen muy pesada o mal orientada
- mantener la imagen clara, centrada y legible
- si el documento tiene tabla, sello, firma, QR o texto pequeño, preparar una vista que preserve esos elementos
- si el documento es complejo, considerar un enfoque por zonas o por partes en lugar de un único envío grande y ambiguo

---

## 6) Principio clave

La validación de comprobante debe ser una decisión rápida y económica. La extracción debe ser una decisión deliberada y precisa.

En otras palabras:

- para decidir si es comprobante: calidad baja + prompt corto + respuesta binaria
- para extraer datos: calidad correcta + prompt específico + documento limpio y bien preparado

Esto evita que la primera fase se vuelva costosa y también evita que la segunda fase se degrade por enviar una versión demasiado pobre del documento.

---

## 7) Resumen corto

```text
1. Reducir calidad para decidir si es comprobante.
2. Si es comprobante, preparar una versión mejor y más fiel.
3. Extraer con cuidado, sin perder detalles visuales importantes.
4. No reutilizar la vista rápida para la extracción final.
```

La regla central es: "doble paso con distinta calidad".

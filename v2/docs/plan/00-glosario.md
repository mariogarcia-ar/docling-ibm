# 00 — Glosario del dominio y modelo de evidencia

> **Rol**: BA (dominio) + SA (contratos de datos)
> **Objetivo**: fijar vocabulario común y el **contrato de evidencia** que
> atraviesa todo el pipeline. Todo documento posterior usa estos términos.

---

## 1. Glosario del dominio

### Documentos y procesamiento

| Término | Definición |
|---------|------------|
| **Comprobante** | Documento fiscal o comercial (factura A/B/C/M/E, nota de crédito/débito, ticket, boleto 090/099, etc.) sujeto a validación y extracción. |
| **Docling / procesamiento** | Capacidad de convertir cualquier entrada (PDF texto, PDF escaneado, imagen, DOCX, XLSX, PPTX, TXT, CSV, LOG, HTML/Markdown) en una representación estructurada y legible (Markdown + boxes). |
| **OCR** | Lectura de texto desde imagen mediante motor clásico (RapidOCR/Docling) o modelo visual (VLM). |
| **Markdown OCR / representación intermedia** | Salida textual ordenada por posición visual que sirve de entrada al flujo LLM. |
| **Box** | Rectángulo (`bbox`) con texto detectado y posición, usado para reordenar la lectura. |

### Clasificación e imagen

| Término | Definición |
|---------|------------|
| **Clase de imagen** | Fotografía de documento, escaneo plano, captura digital/screenshot, imagen con manuscrito/sello/firma. Determina preprocesamiento y motor. |
| **Orientación** | Horizontal o vertical predominante del documento (afecta el orden de lectura: `center_y` vs. `center_x`). |
| **Calidad visual** | Resolución, contraste y ruido de la imagen; gatilla preprocesamiento antes de OCR. |
| **Gate de procesabilidad** | Chequeo previo (formato, resolución mínima, legibilidad) antes de gastar OCR o llamadas a modelo. |

### Qween (doble paso con distinta calidad)

| Término | Definición |
|---------|------------|
| **Qween / doble paso** | Estrategia de dos etapas con distinta calidad: (A) validación rápida barata "¿es comprobante?" y (B) extracción real con la mejor calidad disponible. |
| **Vista rápida (thumbnail)** | Versión degradada/reducida del documento para la decisión binaria barata. |
| **Vista de revisión** | Versión de calidad media usada cuando la validación rápida da "indeterminado". |
| **Vista fiel / documento limpio** | Versión normalizada, orientada y de alta fidelidad que se envía a la extracción real. |

### Flujos de evidencia, reglas y conclusión

| Término | Definición |
|---------|------------|
| **Flujo VLM** | Especialista que lee la imagen directamente (prioriza lo que ve: letra grande del recuadro, sellos, manuscritos). |
| **Flujo LLM** | Especialista que lee el OCR/Markdown y razona sobre el texto. |
| **Evidencia** | Objeto estructurado por campo: valor extraído + fuente + fragmento de sustento. Es la unidad mínima de comparación programática. |
| **Reglas raw (pasada 1)** | Reglas determinísticas que validan cada fuente **por separado** sobre su evidencia en crudo. |
| **Reglas cruzadas (pasada 2)** | Reglas determinísticas sobre la **evidencia combinada**: negocio tributario, fast-fail por letra y conflicto (R7 y análogas). |
| **Evidencia adicional** | Consulta puntual con objetivo y límite de reintentos (ej. padrón ARCA/WSCDC) para cubrir un gap concreto. |
| **Candidatos restantes** | Letras/tipos que sobrevivieron a las reglas de descarte; único universo que el agente IA puede elegir. |
| **Conclusión / consolidación** | Resultado final del caso con nivel de certeza (alta por programa, baja por agente) y origen. |
| **Certeza** | Derivada de la **etapa que resolvió** el caso (programa = alta, agente IA = baja → HITL), no de la confianza autodeclarada del modelo. |

### Roles de decisión

| Término | Definición |
|---------|------------|
| **Agente IA** | Paso de escalado que decide solo cuando el código no concluyó; recibe evidencia, reglas disparadas y candidatos restantes. |
| **HITL (Human-in-the-loop)** | Revisión humana. Autoridad final: corrige decisiones de certeza baja y muestrea las de certeza alta (auditoría). |
| **Certeza alta** | Caso resuelto íntegramente por reglas de programación (no pasó por agente IA). |
| **Certeza baja** | Caso resuelto por el agente IA; siempre se encola a HITL. |

### Clasificación contable

| Término | Definición |
|---------|------------|
| **Centro de costo** | `CCNNNN` (CC0001…CC0008) asignado por reglas de negocio del cliente. |
| **Macro categoría** | Nivel intermedio (ej. MC07) que se resuelve a partir del centro de costo. |
| **Concepto / código final** | Salida final de clasificación contable que depende de macro categoría + condición impositiva. |
| **Condición impositiva** | `21`, `10_5`, `27`, `2_5`, `exento_no_gravado` — contexto requerido por el paso 03. |

### Auditoría y calidad

| Término | Definición |
|---------|------------|
| **Trazabilidad completa** | Registro de versión de prompt, modelo, evidencia por fuente, regla disparada y quién decidió (código/agente). |
| **Golden set** | Conjunto etiquetado de documentos con veredicto de referencia para medir el pipeline. |
| **Muestreo de auditoría** | Revisión HITL periódica de una muestra de casos de "certeza alta" para detectar reglas que matchean por accidente. |

---

## 2. Modelo de evidencia (contrato central)

> De `ideas/flujo_deteccion_tipo_comprobante.md`: "mismo esquema de campos entre
> VLM y LLM (fuente, fragmento de sustento, valor) para que las reglas puedan
> comparar programáticamente". Es la **decisión abierta #1** y el pilar del diseño.

### 2.1 Unidad de evidencia por campo (`Evidence`)

```json
{
  "campo": "tipo_comprobante",
  "valor": "A",
  "fuente": "vlm",                 // "vlm" | "llm" | "programa" | "arca" | "hitl"
  "fragmento_sustento": "Recuadro encabezado: letra grande 'A', COD. 01",
  "confianza_fuente": "alta",      // autoevaluación del flujo (no es la certeza final)
  "meta": {
    "modelo": "qwen2.5vl:3b",
    "version_prompt": "11.1@sha:abc123",
    "timestamp": "2026-09-06T12:00:00Z"
  }
}
```

### 2.2 Evidencia de una fuente (`SourceEvidence`)

```json
{
  "fuente": "vlm",
  "campos": { "tipo_comprobante": { "valor": "A", "fragmento_sustento": "..." } },
  "valida": true,               // resultado de la pasada 1 (reglas raw) sobre esta fuente
  "reglas_aplicadas": ["R4"],
  "debilidades": []
}
```

### 2.3 Evidencia combinada (`CombinedEvidence`)

```json
{
  "documento_id": "sha256(archivo)",
  "campos": {
    "cuit_emisor": {
      "vlm":  { "valor": "...", "fragmento_sustento": "..." },
      "llm":  { "valor": "...", "fragmento_sustento": "..." },
      "resolucion": { "ganador": "vlm", "regla": "PREC_1", "motivo": "visual gana en recuadro" }
    }
  },
  "decision": {
    "concluye": true,
    "certeza": "alta",                 // alta (programa) | baja (agente)
    "origen": "programa",              // programa | agente_ia | hitl
    "candidatos_descartados": ["C"],
    "candidatos_restantes": ["A", "B"],
    "reglas_aplicadas": ["R1", "R4", "R7"],
    "alertas": []
  },
  "trazabilidad": { "prompts": {}, "modelos": {}, "pasos": [] }
}
```

### 2.4 Resultado consolidado final (`VoucherResult`)

```json
{
  "estado": "aprobado | rechazado | revision",
  "tipo_comprobante": "A",
  "certeza": "alta",
  "origen": "programa",
  "campos_extraidos": { },
  "clasificacion_contable": { "centro_costo": "CC0006", "macro_categoria": "MC07", "concepto": "...", "codigo": "..." },
  "evidencia": { },
  "hitl": { "requerido": false, "prioridad": "baja", "estado": "no_aplica" },
  "trazabilidad": { }
}
```

> **Regla de oro**: la certeza final **no** es el promedio de confianzas de los
> modelos; es la etiqueta de la etapa que decidió.

---

## 3. Enlaces

- Visión y alcance: [`01-vision-alcance.md`](01-vision-alcance.md)
- Arquitectura y contratos completos: [`03-arquitectura-solucion.md`](03-arquitectura-solucion.md)
- Decisiones abiertas (contrato, precedencia, alcance de evidencia adicional, etc.): [`04-decisiones-abiertas-adr.md`](04-decisiones-abiertas-adr.md)

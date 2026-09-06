# 03 — Arquitectura de Solución (SA)

> **Rol**: Solution Architect
> **Objetivo**: diseño de alto nivel de la **librería robusta**, el **pipeline**
> clasificación → extracción → conclusión y el **cliente** que la invoca.
> Fuentes: `v2/docs/ideas/*.md`, estado actual de `v1/` y `prompts/`.

---

## Seguimiento por módulo

> Cada módulo de la librería (y los elementos transversales) tiene un archivo de
> seguimiento que traza su arquitectura contra épicas, fases, contratos y ADRs.
> Ver el índice en [`03-arquitectura/README.md`](03-arquitectura/README.md).

- [PROC](03-arquitectura/PROC.md) — Módulo `processing` (docling multi-tipo)
- [VAL](03-arquitectura/VAL.md) — Módulo `validation` (qween doble-paso)
- [CLAS](03-arquitectura/CLAS.md) — Módulo `classification` (tipo/letra + contable)
- [EXT](03-arquitectura/EXT.md) — Módulo `extraction` (VLM + LLM)
- [CONC](03-arquitectura/CONC.md) — Módulo `conclusion` (reglas → agente → HITL)
- [SCHEMAS](03-arquitectura/SCHEMAS.md) — Módulo `schemas/` (contrato de evidencia pydantic)
- [RULES](03-arquitectura/RULES.md) — Módulo `rules/` (motor de reglas R1-R7, precedencia, gaps)
- [MODELS](03-arquitectura/MODELS.md) — Módulo `models/` (adaptadores: OllamaClient, DoclingConverter, ArcaClient)
- [TRACE](03-arquitectura/TRACE.md) — Módulo `trace/` (CaseRecord, trazabilidad, sidecar)
- [ORCH-CLI](03-arquitectura/ORCH-CLI.md) — Orquestador + API de alto nivel + Cliente CLI (`orchestrator.py`, `api.py`, `cli/`)

---

## 1. Principios de diseño

1. **Etapa decide certeza** — la certeza nace de qué etapa resolvió el caso
   (programa = alta; agente IA = baja → HITL), nunca de la confianza
   autodeclarada del modelo.
2. **Evidencia como contrato** — toda fuente (VLM/LLM/programa/ARCA/HITL)
   produce evidencia con el mismo esquema para poder compararla
   programáticamente.
3. **Determinista primero, modelo después** — se agota el código
   (reglas raw + cruzadas) antes de escalar a un agente; el agente solo elige
   entre candidatos no descartados.
4. **Doble calidad** — la validación es barata (vista reducida); la extracción
   es fiel (vista limpia y de alta calidad). Nunca se reutiliza la vista rápida
   para extraer.
5. **Trazable por diseño** — cada caso persiste prompts, modelos, evidencia,
   reglas y decisión para poder auditarse.
6. **Librería primero, cliente después** — el núcleo es un paquete instalable
   sin acoplamiento a carpetas; el CLI/API es un consumidor más.
7. **Modular y testeable** — cada sub-capacidad (docling, qween, clasificación,
   extracción, conclusión) es un módulo con interfaz estable y tests aislados.

---

## 2. Arquitectura de contexto (C4 — Nivel 1)

```mermaid
flowchart LR
    U1["Operador<br/>(batch CLI)"]
    U2["Contador / Auditor<br/>(revisión HITL)"]
    U3["Sistema de negocio<br/>(integración futura)"]

    subgraph SISTEMA["Sistema v2 — Librería + Cliente (docling-qween-v2)"]
        CORE["voucherflow<br/>(librería robusta)"]
    end

    OLLAMA["Ollama local<br/>VLM qwen2.5vl · LLM"]
    DOCLING["Docling<br/>(OCR/multi-formato)"]
    ARCA["ARCA/AFIP<br/>(padrón/WSCDC)"]
    FS[("files/ · .md · .json<br/>sidecars + store")]

    U1 -->|"CLI: procesar lote"| CORE
    U2 -->|"revisar/auditar casos"| CORE
    U3 -.->|"API (futuro)"| CORE
    CORE --> DOCLING
    CORE --> OLLAMA
    CORE -.->|"evidencia adicional<br/>(opcional, con límite)"| ARCA
    CORE <--> FS
```

**Nota**: Ollama y Docling son dependencias externas locales. ARCA es opcional
en el MVP (decisión abierta #3).

---

## 3. Arquitectura de contenedores (C4 — Nivel 2)

```mermaid
flowchart TB
    subgraph CLIENTE["Cliente (consumidor)"]
        CLI["CLI<br/>subcomandos: process / classify / extract / conclude / batch"]
        API["API HTTP (fase 2)<br/>FastAPI"]
        NOTEBOOK["Uso embebido<br/>(notebook / script)"]
    end

    subgraph LIB["Librería voucherflow (paquete Python)"]
        ORCH["Orquestador de pipeline<br/>PipelineOrchestrator"]
        subgraph MODULOS["Módulos de capacidad"]
            PROC["processing<br/>(docling multi-tipo)"]
            VAL["validation<br/>(qween doble-paso)"]
            CLS["classification<br/>(tipo/letra + contable)"]
            EXT["extraction<br/>(VLM + LLM)"]
            CONC["conclusion<br/>(reglas → agente → HITL)"]
        end
        RULES["Motor de reglas<br/>rules/ (R1-R7, fast-fail, precedencia)"]
        EVID["Schemas de evidencia<br/>schemas/ (pydantic)"]
        MODELS["Clientes de modelo<br/>models/ (ollama, docling, arca)"]
        TRACE["Trazabilidad<br/>trace/ (registro por caso)"]
        CONFIG["Configuración<br/>settings/ (yaml + env)"]
    end

    CLI --> ORCH
    API --> ORCH
    NOTEBOOK --> ORCH
    ORCH --> PROC
    ORCH --> VAL
    ORCH --> CLS
    ORCH --> EXT
    ORCH --> CONC
    PROC --> MODELS
    VAL --> MODELS
    CLS --> RULES
    EXT --> EVID
    EXT --> MODELS
    CONC --> RULES
    CONC --> EVID
    CONC --> MODELS
    CONC --> TRACE
    EVID --> MODELS
```

---

## 4. Vista de componentes de la librería (detalle)

### 4.1 Módulo `processing` (refactor docling)

Responsabilidades: decidir tipo de entrada, elegir ruta, normalizar, y en
imágenes: gate de procesabilidad → clase de imagen → preprocesamiento →
orientación → motor OCR/VLM → salida ordenada.

```mermaid
flowchart TD
    IN[Entrada<br/>pdf/img/docx/xlsx/pptx/txt/csv/log/html/md] --> DET[Detector de tipo de entrada]
    DET -->|texto extraíble| EX1[Extractor texto nativo]
    DET -->|pdf escaneado| CV[Convertir a imagen]
    DET -->|imagen| GATE[Gate de procesabilidad]
    DET -->|office/plano| EX2[Extractor específico<br/>docx/xlsx/pptx/txt/csv/log/html/md]
    DET -->|no soportado| REJ[Rechazar / reencolar]

    CV --> GATE
    GATE -->|no pasa| REJ
    GATE -->|pasa| CLS_IMG[Clasificador de imagen<br/>foto / escaneo / screenshot / manuscrito]
    CLS_IMG --> PRE[Preprocesamiento<br/>perspectiva · calidad · binarización]
    PRE --> ORI[Detectar orientación + rotar]
    ORI --> MOTOR[Elegir motor<br/>OCR tradicional vs VLM]
    MOTOR -->|ocr| OCR[Docling OCR / RapidOCR]
    MOTOR -->|vlm| VLM[Modelo visual]
    OCR --> ORD[Ordenar por posición<br/>center_y / center_x]
    VLM --> ORD
    EX1 --> ORD
    EX2 --> ORD
    ORD --> OUT[Representación estructurada<br/>Markdown + boxes]
```

**Interfaz**:
```python
@dataclass
class ProcessedDocument:
    tipo_entrada: str            # "pdf_texto" | "pdf_escaneado" | "imagen" | ...
    ruta: str
    markdown: str                # representación ordenada
    boxes: list[Box] | None      # opcional (para debug)
    orientacion: str             # "horizontal" | "vertical"
    motor: str | None            # "ocr" | "vlm" | None (texto nativo)
    calidad: QualityReport | None
```

### 4.2 Módulo `validation` (refactor qween)

```mermaid
flowchart TD
    DOC[Documento procesado] --> RAP[preparar_vista_rapida<br/>thumbnail / baja calidad]
    RAP --> DEC1[decidir_es_comprobante<br/>prompt binario]
    DEC1 -->|no_comprobante| REJ[Rechazar / reencolar]
    DEC1 -->|indeterminado| REV[preparar_vista_revision]
    REV --> DEC2[decidir_es_comprobante<br/>segunda pasada]
    DEC2 -->|no_comprobante| REJ
    DEC1 -->|comprobante| FIEL[preparar_vista_fiel<br/>alta calidad + orientación]
    DEC2 -->|comprobante| FIEL
    FIEL --> EXT[flujo de extracción]
```

**Interfaz**:
```python
@dataclass
class ValidationResult:
    decision: str                # "comprobante" | "no_comprobante" | "indeterminado"
    vista_usada: str             # "rapida" | "revision"
    evidencia: SourceEvidence    # modelo + fragmento de sustento de la decisión
```

### 4.3 Módulo `classification`

Dos subflujos: **tipo/letra** (usa reglas R1-R7 + VLM/LLM) y **contable**
(cadena 01→02→03).

```mermaid
flowchart LR
    subgraph TIPO["Clasificación tipo/letra"]
        A1[Vista fiel + OCR] --> A2[Flujo VLM: letra del recuadro]
        A1 --> A3[Flujo LLM: regex/razonamiento]
        A2 --> A4[Reglas raw por fuente]
        A3 --> A4
        A4 --> A5[Reglas negocio R1-R3]
        A4 --> A6[Reglas extracción R4-R6]
        A5 --> A7[Reglas conflicto R7]
        A6 --> A7
        A7 --> A8[Candidatos descartados / restantes]
    end

    subgraph CONTABLE["Clasificación contable"]
        B1[Extract: proveedor, descripcion, monto] --> B2[Paso 01 centro de costo]
        B2 --> B3[Paso 02 macro categoría]
        B3 --> B4[Paso 03 concepto + código final<br/>+ condición impositiva]
    end
```

### 4.4 Módulo `extraction`

```mermaid
flowchart LR
    FIEL[Vista fiel] --> VLM[Flujo VLM<br/>lee imagen]
    MD[OCR/Markdown] --> LLM[Flujo LLM<br/>razona texto]
    VLM --> VRAW[Reglas raw VLM]
    LLM --> LRAW[Reglas raw LLM]
    VRAW --> COMB[Combinar evidencia<br/>por campo con fuente]
    LRAW --> COMB
    COMB --> RES[Evidencia combinada]
```

### 4.5 Módulo `conclusion` (el corazón del patrón)

```mermaid
flowchart TD
    CE[Evidencia combinada] --> RC[Reglas cruzadas<br/>negocio + fast-fail + conflicto]
    RC --> GAP{¿faltan datos?}
    GAP -->|sí| EA[Buscar evidencia adicional<br/>por gap · max_reintentos=N]
    EA --> RC2[Aplicar reglas cruzadas<br/>nuevamente]
    RC --> DET{¿el código concluye?}
    RC2 --> DET
    DET -->|sí| ALTA[Consolidar<br/>certeza alta · origen programa]
    DET -->|no| AG[Agente IA decide<br/>entre candidatos_restantes]
    AG --> BAJA[Consolidar<br/>certeza baja · origen agente_ia]
    ALTA --> H1[HITL: muestra de auditoría<br/>prioridad baja]
    BAJA --> H2[HITL: revisión obligatoria<br/>prioridad alta]
    H1 --> FB[Feedback → reglas y prompts]
    H2 --> FB
    ALTA --> TRACE[Persistir trazabilidad]
    BAJA --> TRACE
```

**Interfaz**:
```python
@dataclass
class ConclusionResult:
    concluye: bool
    certeza: str                 # "alta" | "baja"
    origen: str                  # "programa" | "agente_ia" | "hitl"
    candidatos_descartados: list[str]
    candidatos_restantes: list[str]
    reglas_aplicadas: list[str]
    alertas: list[Alerta]
    hitl: HitlDecision           # requerido / prioridad / estado
```

### 4.6 Módulo `models` (adaptadores)

- `OllamaClient` (chat VLM/LLM): encapsula `ask_ollama` de v1 (URL,
  `json_format`, `num_ctx`, manejo de 429/backoff, log de diagnóstico).
- `DoclingConverter`: adaptador sobre Docling para conversión multi-formato.
- `ArcaClient` (opcional): consulta WSCDC/padrón (reutiliza lógica de
  `v1/wip/consultar_arca.py`), con límite de reintentos y timeout.

### 4.7 Módulo `trace`

Registro persistente por caso (`CaseRecord`): entrada, decisiones por etapa,
prompts (hash/versión), modelos, evidencia, reglas y resultado. Salida a JSON
sidecar y/o a un store simple (SQLite) en fases posteriores.

---

## 5. Pipeline end-to-end (secuencia)

### 5.1 Secuencia general (Mermaid sequence)

```mermaid
sequenceDiagram
    autonumber
    actor Op as Operador (CLI)
    participant Or as Orchestrator
    participant Pr as processing (docling)
    participant Va as validation (qween)
    participant Cl as classification
    participant Ex as extraction
    participant Co as conclusion
    participant Ol as Ollama (VLM/LLM)
    participant Rl as Reglas (motor)
    participant Tr as Trazabilidad

    Op->>Or: procesar(documento)
    Or->>Pr: procesar_documento()
    Pr->>Ol: OCR / conversión
    Pr-->>Or: ProcessedDocument (markdown+boxes)

    Or->>Va: validar_y_procesar()
    Va->>Va: preparar_vista_rapida()
    Va->>Ol: decisión binaria (vista baja)
    Va-->>Or: es comprobante?

    alt no comprobante
        Va-->>Op: rechazado (motivo)
    else comprobante
        Va->>Va: preparar_vista_fiel()
        Or->>Cl: clasificar tipo/letra (paralelo VLM+LLM)
        Cl->>Ol: VLM sobre imagen
        Cl->>Ol: LLM sobre OCR
        Cl->>Rl: reglas R1-R7 sobre evidencia
        Cl-->>Or: tipo + candidatos + alertas

        Or->>Ex: extraer campos (paralelo)
        Ex->>Ol: VLM (imagen) / LLM (OCR)
        Ex-->>Or: evidencia combinada

        Or->>Co: concluir(evidencia)
        Co->>Rl: reglas cruzadas
        Rl-->>Co: gaps / conflictos / candidatos
        alt faltan datos
            Co->>Rl: evidencia adicional (ARCA, con límite)
        end
        alt código concluye
            Co-->>Or: certeza alta (origen=programa)
        else no concluye
            Co->>Ol: agente IA decide (candidatos restantes)
            Co-->>Or: certeza baja (origen=agente_ia)
        end
        Or->>Tr: registrar caso (evidencia, reglas, decisión)
        Tr-->>Op: VoucherResult + sidecar
    end
```

### 5.2 Secuencia del "doble paso" qween (detalle)

```mermaid
sequenceDiagram
    participant Va as validation
    participant V as VLM (decisión)
    participant F as Vista fiel

    Va->>Va: preparar_vista_rapida (thumbnail)
    Va->>V: "¿es comprobante?" (prompt binario, calidad baja)
    V-->>Va: comprobante | no_comprobante | indeterminado

    alt indeterminado
        Va->>Va: preparar_vista_revision (calidad media)
        Va->>V: segunda decisión
        V-->>Va: comprobante | no_comprobante
    end

    Va->>F: preparar_documento_para_extraccion()
    Note over F: NO reutilizar vista rápida.<br/>Orientación corregida, resolución suficiente,<br/>preserva sello/firma/QR/texto pequeño.
    F-->>Va: documento_limpio
    Va-->>Ex: vista fiel lista para extracción
```

---

## 6. Modelos de datos / schemas de evidencia

> Contrato completo definido en [`00-glosario.md`](00-glosario.md#2-modelo-de-evidencia-contrato-central).
> Aquí se fijan los esquemas pydantic que la librería debe implementar.

```python
# schemas/evidence.py (borrador)
from enum import Enum
from pydantic import BaseModel, Field

class Fuente(str, Enum):
    vlm = "vlm"
    llm = "llm"
    programa = "programa"
    arca = "arca"
    hitl = "hitl"

class Certeza(str, Enum):
    alta = "alta"
    baja = "baja"

class Origen(str, Enum):
    programa = "programa"
    agente_ia = "agente_ia"
    hitl = "hitl"

class EvidenceField(BaseModel):
    campo: str
    valor: str | float | int | None
    fuente: Fuente
    fragmento_sustento: str
    confianza_fuente: str = "media"   # autoevaluación, no es la certeza final
    meta: dict = Field(default_factory=dict)  # modelo, version_prompt, timestamp

class SourceEvidence(BaseModel):
    fuente: Fuente
    campos: dict[str, EvidenceField]
    valida: bool = True                # resultado pasada 1 (reglas raw)
    reglas_aplicadas: list[str] = []
    debilidades: list[str] = []

class FieldResolution(BaseModel):
    ganador: Fuente | None
    regla: str | None                  # ej. "PREC_1"
    motivo: str | None

class CombinedEvidence(BaseModel):
    documento_id: str
    campos: dict[str, dict[Fuente, EvidenceField] | FieldResolution]  # ver nota
    decision: Decision
    trazabilidad: dict = Field(default_factory=dict)

class Decision(BaseModel):
    concluye: bool
    certeza: Certeza
    origen: Origen
    candidatos_descartados: list[str] = []
    candidatos_restantes: list[str] = []
    reglas_aplicadas: list[str] = []
    alertas: list[dict] = []

class VoucherResult(BaseModel):
    estado: str                        # aprobado | rechazado | revision
    tipo_comprobante: str | None
    certeza: Certeza | None
    origen: Origen | None
    campos_extraidos: dict = {}
    clasificacion_contable: dict = {}  # centro_costo, macro_categoria, concepto, codigo
    evidencia: CombinedEvidence | None
    hitl: dict = Field(default_factory=dict)
    trazabilidad: dict = Field(default_factory=dict)
```

> **Nota de diseño**: la combinación por campo se modela como
> `{ campo: { "vlm": EvidenceField, "llm": EvidenceField, "resolucion": FieldResolution } }`
> para que las reglas de precedencia puedan evaluar ambos lados y registrar la
> resolución. El diagrama ER siguiente lo muestra simplificado.

```mermaid
erDiagram
    DOCUMENTO ||--o{ EVIDENCIA_CAMPO : "produce"
    EVIDENCIA_CAMPO ||--|| FLUJO : "origen (vlm/llm)"
    EVIDENCIA_CAMPO {
        string campo PK
        string valor
        string fuente
        string fragmento_sustento
        string confianza_fuente
    }
    CASO ||--o{ EVIDENCIA_CAMPO : "combina"
    CASO ||--o{ REGLA_DISPARADA : "aplica"
    CASO {
        string documento_id PK
        string estado
        string certeza
        string origen
        string[] candidatos_descartados
        string[] candidatos_restantes
    }
    REGLA_DISPARADA {
        string id PK
        string resultado
        string detalle
    }
    CASO ||--o{ TRAZA : "registra"
    TRAZA {
        string version_prompt
        string modelo
        string etapa
        string timestamp
    }
    CASO }o--o| HITL : "deriva"
    HITL {
        bool requerido
        string prioridad
        string estado
        string correccion
    }
```

---

## 7. Reglas de negocio como motor declarativo

La fuente `prompts/wip/deteccion_tipo_factura.yaml` ya define R1-R7 de forma
declarativa. El refactor las mueve del prompt a un **motor de reglas en código**
(registro de reglas) manteniendo el mismo id para trazabilidad:

```python
# rules/tipo_comprobante.py (borrador)
REGLA_R1 = Rule(
    id="R1",
    prioridad=1,
    condicion=lambda ctx: ctx.emisor.condicion_fiscal in {"Monotributo", "Exento"},
    resultado="C",
    tipo="negocio",
)
REGLA_R2A = Rule(
    id="R2A",
    prioridad=2,
    condicion=lambda ctx: (ctx.emisor.condicion_fiscal == "Responsable Inscripto"
                           and ctx.receptor.condicion_fiscal == "Responsable Inscripto"),
    resultado="A",
    tipo="negocio",
)
# ... R2B, R3 (exportación, prioridad 0), R4-R6 (extracción), R7 (conflicto)
```

- El **LLM/VLM** ya no "aplican reglas" libremente: devuelven *evidencia*
  (letra detectada, condiciones fiscales detectadas, desglose) y el motor decide.
- El prompt `11.1` se reescribe para **devolver evidencia**, no la decisión
  final (ver decisión abierta #1 y #6).
- Con el tiempo, la casuística observada en HITL se codifica como nuevas reglas
  (objetivo: crece el % de certeza alta por programa).

---

## 8. El cliente que invoca la librería

### 8.1 CLI propuesto

```bash
# Procesamiento / docling
voucherflow process archivo.pdf|imagen|docx ... [--orientation auto] [--workers N] [--output dir]

# Validación qween (gate "¿es comprobante?")
voucherflow validate archivo.jpg --quick

# Clasificación tipo/letra + contable
voucherflow classify archivo.md [--condicion-impositiva 21]

# Extracción (modos heredados mapeados)
voucherflow extract archivo.md --mode kvi|kvg|10|11
voucherflow extract-detect archivo.jpg --mode 11.1        # tipo/letra con VLM/LLM

# Pipeline completo (equivalente a full_pipeline.py v1)
voucherflow run archivo|carpeta [--workers N] [--force] [--condicion-impositiva 21] [-o salida.json]

# Lote
voucherflow batch files/2025-08 [--workers N] [--cooling-policy on] [-o resultados.json]

# Auditoría / HITL
voucherflow hitl list --certeza baja
voucherflow case show <documento_id>          # trazabilidad completa
```

### 8.2 Mapeo v1 → v2 (equivalencia verificable)

| Comando v1 | v2 (CLI) | Módulo |
|-----------|----------|--------|
| `ocr_documents.py files` | `voucherflow process files` | processing |
| `run_raw.py --input X` | `voucherflow process X --raw` | processing |
| `document_extraction.py -M kvi/kvg/11.1` | `voucherflow extract --mode ...` | extraction/classification |
| `classification_pipeline.py` (01→02→03) | `voucherflow classify` | classification |
| `extraction_pipeline.py` (10/11) | `voucherflow extract --pipeline 10+11` | extraction |
| `full_pipeline.py` | `voucherflow run` | orchestrator |
| `ask.py` | `voucherflow ask <file> -q ...` | extraction/QA |
| `wip/consultar_arca.py` | `voucherflow arca check ...` (opcional) | models/ArcaClient |

---

## 9. Contratos de integración

| Contrato | Descripción | Fuente |
|----------|-------------|--------|
| `ProcessedDocument` | Salida de `processing` (markdown + boxes + metadatos) | E-DOC |
| `ValidationResult` | Decisión del gate qween | E-QWE |
| `EvidenceField` / `SourceEvidence` | Evidencia por campo con sustento | E-EXT |
| `CombinedEvidence` | Evidencia combinada con resolución por campo | E-EXT/E-CONC |
| `VoucherResult` | Resultado consolidado final (estado, certeza, origen) | E-CONC |
| `CaseRecord` | Registro de trazabilidad completo | E-CONC-5 |

**Formato de prompts**: se conserva el formato YAML `system/system_llm/system_vlm
/user/user_vlm` con placeholders, pero el contrato de salida pasa a ser el
schema de evidencia (ver ADR-004).

---

## 10. Consideraciones de despliegue y recursos (NFR)

- **Paralelismo**: procesamiento por lotes con `ProcessPoolExecutor`; cada
  worker con su `DoclingConverter` (patrón actual de `full_pipeline.py`).
- **Temperatura**: política de enfriamiento configurable; la cuenta de pausa
  arranca cuando **todos** los workers están detenidos (requisito explícito en
  `my_prompt.md`).
- **Reanudación**: checkpoints por documento (sidecar) con escritura atómica
  (patrón `write_results` de `lib/pipeline.py` v1).
- **Observabilidad**: logs estructurados por caso; capturar diagnóstico del SDK
  ante latencia > umbral o status inesperado (guía Cosmos DB / buenas prácticas
  de clientes HTTP).
- **Seguridad**: credenciales ARCA en `.env` (fuera del repo); sin datos
  sensibles en logs; hash del documento como id (`sha256`).

---

## 11. Estructura de paquete propuesta (borrador)

```text
v2/
  pyproject.toml
  src/voucherflow/
    __init__.py
    api.py                    # API de alto nivel (facade)
    orchestrator.py           # PipelineOrchestrator
    schemas/
      evidence.py             # EvidenceField, SourceEvidence, CombinedEvidence...
      result.py               # VoucherResult, CaseRecord
    processing/
      type_detector.py
      image_classifier.py
      preprocessing.py
      orientation.py
      ocr.py                  # adaptador docling/rapidocr
      markdown_exporter.py
    validation/
      qween.py                # doble-paso (vistas rápida/revisión/fiel)
    classification/
      tipo_comprobante.py     # letra A/B/C/M/E (reglas R1-R7 + flujos)
      contable.py             # cadena 01 → 02 → 03
    extraction/
      flows.py                # flujo VLM y flujo LLM
      key_value.py            # normalización de campos (kvi/kvg/10/11)
    conclusion/
      engine.py               # reglas cruzadas, gaps, agente, HITL
      agent.py                # agente IA
      hitl.py                 # cola HITL + muestreo auditoría
    rules/
      registry.py             # motor de reglas
      tipo_comprobante_rules.py   # R1-R7
      precedencia.py          # tabla de precedencia por campo
      gaps.py
    models/
      ollama.py               # cliente chat VLM/LLM (retry, diagnóstico)
      docling.py
      arca.py                 # opcional
    trace/
      recorder.py             # CaseRecord + persistencia
    settings/
      config.py               # carga yaml/env
  cli/
    main.py                   # typer/click: subcomandos
  tests/                      # unit + integración + golden set
    golden/                   # dataset etiquetado (no código)
  docs/
    plan/                     # este plan
```

> El nombre del paquete y la ubicación `src/` vs. plano son decisiones abiertas
> (ADR-001). La estructura anterior es una **propuesta de trabajo**, no una
> decisión tomada.

---

## 12. Enlaces

- Contratos y glosario: [`00-glosario.md`](00-glosario.md)
- Historias: [`02-epicas-historias-usuario.md`](02-epicas-historias-usuario.md)
- Decisiones abiertas y ADRs: [`04-decisiones-abiertas-adr.md`](04-decisiones-abiertas-adr.md)
- Plan de ejecución: [`05-plan-ejecucion.md`](05-plan-ejecucion.md)

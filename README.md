# voucherflow

Librería de procesamiento documental para comprobantes: convierte un archivo
—imagen, PDF, Office o texto— en una **conclusión auditable** (aprobado,
revisión o rechazado) con la evidencia que la sostiene.

El sistema está armado como **cinco capacidades** encadenadas. Cada una tiene un
contrato propio, se puede probar sola y sabe dónde termina su responsabilidad:

```
  documento
      │
      ▼
┌──────────────┐   ┌──────────────┐   ┌────────────────┐   ┌──────────────┐   ┌──────────────┐
│  processing  │──▶│  validation  │──▶│ classification │──▶│  extraction  │──▶│  conclusion  │
│  Docling     │   │  ¿es         │   │  tipo/letra +  │   │  VLM + LLM   │   │  reglas →    │
│  multi-formato│   │  comprobante?│   │  contable      │   │  con evidencia│   │  agente → HITL│
└──────────────┘   └──────────────┘   └────────────────┘   └──────────────┘   └──────────────┘
```

| Capacidad | Pregunta que responde | Módulo |
|---|---|---|
| [`processing`](#1-processing--procesamiento-docling-multi-formato) | ¿Cómo se lee este archivo? | `processing/` |
| [`validation`](#2-validation--gate-es-comprobante-doble-paso-qween) | ¿Esto es un comprobante? | `validation/` |
| [`classification`](#3-classification--tiposletra--clasificación-contable) | ¿Qué tipo es y cómo se imputa? | `classification/` |
| [`extraction`](#4-extraction--extracción-vlm--llm-con-evidencia) | ¿Qué dice el documento? | `extraction/` |
| [`conclusion`](#5-conclusion--reglas--agente--hitl) | ¿Cuál es el veredicto? | `conclusion/` |

Los módulos transversales sostienen a las cinco: `schemas/` (contrato de
evidencia y de resultado), `rules/` (motor de reglas), `models/` (adaptadores
`OllamaClient`, `DoclingConverter`, `ArcaClient`), `trace/` (trazabilidad y
métricas) y `settings/` (configuración centralizada).

## Instalación

```bash
python -m pip install -e .
```

Requiere Python >= 3.11. Docling, pymupdf y requests son dependencias del
paquete; los modelos se sirven localmente con Ollama. La guía del operador
—requisitos, modelos y verificación— está en
[`docs/usuario/01-instalacion.md`](docs/usuario/01-instalacion.md).

## Importar

```python
from voucherflow.api import run, extract, ask
from voucherflow.schemas.evidence import EvidenceField, Fuente

resultado = run("factura.pdf")
print(resultado.estado, resultado.certeza)
```

---

## Las cinco capacidades

### 1. `processing` — procesamiento Docling multi-formato

Recibe cualquier archivo soportado y devuelve un `ProcessedDocument` con el
markdown normalizado, los ítems posicionados y la calidad del procesamiento.

- **Enrutamiento por contenido real**: `type_detector` distingue imagen, PDF con
  texto nativo, PDF escaneado, Office y texto plano. La decisión no se toma por
  extensión.
- **Elección de motor**: cada ruta define su motor. Un PDF apto prefiere
  `pdftotext --layout` (recupera las columnas que Docling aplana) con *fallback*
  a Docling; un PDF escaneado pasa por *render* → imagen → OCR.
- **Gate de imagen propio**: `image_classifier` decide si la imagen es
  procesable antes de gastar OCR (resolución, nitidez, orientación).
- **Orientación y *preprocessing***: detección de orientación dominante y
  rotación, con la misma semántica que el exportador original.
- **Exportador** (`markdown_exporter`): ordena los ítems por posición —
  agrupando en líneas— para que el markdown sea legible y estable.
- **OCR** sobre imágenes y PDFs escaneados, vía Docling.

Todo el módulo es **determinista**: no llama a ningún modelo generativo, así que
se testea con archivos reales sin red.

```bash
voucherflow process factura.pdf -o salida/          # markdown normalizado
voucherflow process factura.pdf --raw -o salida/    # markdown crudo de Docling
```

### 2. `validation` — gate "¿es comprobante?" doble-paso qween

Antes de extraer nada, el sistema decide si el documento **es un comprobante**.
Es el control de costo más importante del pipeline: un "no" evita la extracción
VLM+LLM completa.

- **Doble paso**: una primera pasada sobre una **vista rápida** (menor
  resolución) resuelve la mayoría de los casos; solo los indeterminados escalan
  a una **vista de revisión**. La vista **fiel** —la única que consume la
  extracción— no se prepara si el gate ya dijo que no.
- **Tres salidas**: `comprobante`, `no_comprobante`, `indeterminado`. Un
  `no_comprobante` es una **decisión de negocio con certeza alta**, no un error:
  el pipeline lo registra y termina.
- **Gradiente de calidad**: `CALIDAD_POR_TIPO_VISTA` y `GRADO_CALIDAD_POR_NIVEL`
  hacen explícito qué resolución se usa en cada paso, para que el ahorro sea
  medible.
- **Ahorro verificado**: hay tests que exigen que un `no_comprobante` **no**
  prepare la vista fiel ni llegue a extracción.

```bash
voucherflow validate posible-comprobante.jpg
```

### 3. `classification` — tipo/letra + clasificación contable

Responde dos preguntas con dos cadenas separadas:

**(a) Tipo/letra del comprobante** — se decide **por reglas sobre evidencia**,
nunca porque el modelo haya dicho la letra.

- El modelo aporta **evidencia** (el recuadro de la letra, el texto del
  encabezado, las condiciones fiscales), no la decisión.
- El motor aplica una cascada de reglas declarativas: `R1` (emisor
  monotributo/exento → C), `R2A`/`R2B` (condición fiscal del emisor y del
  receptor → A/B), `R3` (exportación → E, con prioridad máxima), `R4` (letra del
  recuadro), `R5` (regex sobre el encabezado), `R6` (inferencia por totales) y
  `R7` (alerta de conflicto financiero).
- Cuando la lectura del documento **contradice** la expectativa de negocio, se
  dispara `R7`: la letra se resuelve según la preferencia declarada y la certeza
  **baja**, quedando el caso marcado para revisión.

**(b) Clasificación contable** — la cadena `01 → 02 → 03` asigna centro de
costo, macro categoría, concepto y código final.

- Cada paso tiene su **contrato tipado** y su **checkpoint**:
  `<doc>_classification.json` se escribe después de cada paso, así una corrida
  interrumpida no vuelve a pagar los pasos ya resueltos.
- Los prompts están **versionados en código** (`contable-01@1`, `contable-02@1`,
  `contable-03@1`) y tienen su copia de referencia en `prompts/`; un test exige
  que sean idénticos.
- Sin señal específica, el default es `CC0006` con confianza baja y
  `senal_usada=ninguna` — y lo declara en vez de inventar una señal.

```bash
voucherflow classify factura.md
```

### 4. `extraction` — extracción VLM + LLM con evidencia

Extrae los campos del comprobante desde **dos fuentes en paralelo** —visión
(VLM) y texto (LLM)— y combina el resultado campo por campo.

- **El modelo lee, el programa decide**: el modelo devuelve el valor **tal como
  se lee** más el **fragmento del documento que lo sostiene**. La normalización
  es código (`key_value.py`), no una instrucción del prompt.
- **Contrato de evidencia congelado**: cada campo viaja como
  `EvidenceField` con su valor, su fuente y su fragmento de sustento. Un campo
  sin sostén no es auditable.
- **Precedencia explícita**: `rules/precedencia.py` define qué fuente gana por
  campo (la visión suele ser mejor para el recuadro de la letra; el texto para
  los importes). La resolución queda registrada, no es un `if` escondido.
- **Pasada raw por fuente**: antes de combinar, cada lectura pasa por un
  veredicto de calidad (`rules/raw.py`) que la marca válida, dudosa o inválida,
  con las debilidades que detectó.
- **"No inventar" como regla dura**: un valor ilegible conserva el crudo con un
  aviso. Un dato ausente sigue ausente — nunca se rellena con un default
  silencioso.

```bash
voucherflow extract factura.pdf              # evidencia por campo
voucherflow extract-detect factura.pdf       # solo la letra (sin cadena contable)
voucherflow ask factura.pdf -q "¿Cuál es el total?"
```

### 5. `conclusion` — reglas → agente → HITL

Convierte la evidencia combinada en un **veredicto** con certeza declarada,
escalando solo lo que hace falta.

1. **Reglas cruzadas** (`rules/cruzadas.py`): se evalúa la evidencia de la
   pasada 1 sobre el valor vigente del caso. `CRUZ_3` —la letra contradice los
   campos— **rechaza**; `CRUZ_5` —conflicto de negocio contra documento— solo
   **pide revisión**.
2. **Búsqueda de evidencia** (`rules/gaps.py`): si falta un dato que puede
   desempatar, se busca de forma **acotada por presupuesto**. Un proveedor caído
   se reintenta; "consulté y no está" no se insiste.
3. **Consolidación** (`consolidacion.py`): certeza **alta** cuando lo decidió el
   programa, con letra y sin alertas abiertas; un rechazo firme es certeza alta;
   una alerta sin resolver la **baja**. Un caso ambiguo sale sin veredicto.
4. **Agente IA** (`agent.py`): se escala **solo si el código no concluyó y hay
   universo**. Está blindado en tres capas para que no elija fuera de los
   candidatos: el prompt no ve los descartados, el orquestador valida contra
   `candidatos_restantes` y el contrato rechaza la intersección. Una elección
   inválida **se rechaza y se audita, no se corrige**.
5. **HITL** (`hitl.py`): los casos que llegan a revisión humana entran a una cola
   **priorizada**, con muestreo de auditoría **reproducible**
   (`sha256(semilla:documento_id)` — no `random`, sería inauditable). El feedback
   separa los errores del agente de las reglas que aciertan por accidente.

Todo el camino queda persistido: `trace/` escribe un **sidecar** `<doc>.case.json`
por documento (escritura atómica) más un **índice** consultable, del que salen
las **métricas** del lote. Cada métrica usa su propio denominador y, si no hay
datos, sale como "no calculable" con su motivo — «no saber» nunca se reporta como
`0%`.

```bash
voucherflow run factura.pdf                      # pipeline completo
voucherflow batch files/2025-08 -o lote.json     # carpeta recursiva, con workers
voucherflow case list --dir salida/cases         # trazabilidad persistida
voucherflow hitl list --dir salida/cases         # cola de revisión
```

---

## Estados, certeza y salidas

El resultado de una corrida es un `VoucherResult` con un estado:

| Estado | Qué significa |
|---|---|
| `aprobado` | El programa concluyó con certeza alta. |
| `revision` | Falta un dato, hay una alerta abierta o decidió el agente: va a HITL. |
| `rechazado` | **No** es un error: es una conclusión firme con certeza alta. |

La **certeza** se deriva de la etapa que decidió: `programa` → alta,
`agente_ia` → baja, `hitl` → la revisión. Un rechazo con una alerta abierta baja
la certeza, así que las métricas distinguen `rechazado` de
`rechazado_con_certeza_alta`.

Cada comando escribe el **dato** en `stdout` y el **progreso** en `stderr`; el
código de salida es ≠ 0 cuando la corrida falla. La referencia completa —cada
subcomando, sus banderas y sus códigos de salida— está en
[`docs/usuario/02-comandos.md`](docs/usuario/02-comandos.md) y en
`voucherflow <comando> --help`.

| Documento | Para qué |
|---|---|
| [`docs/usuario/01-instalacion.md`](docs/usuario/01-instalacion.md) | Dejar el sistema andando |
| [`docs/usuario/02-comandos.md`](docs/usuario/02-comandos.md) | Referencia de los once subcomandos |
| [`docs/usuario/03-revision-humana.md`](docs/usuario/03-revision-humana.md) | Operar la cola de revisión |
| [`docs/usuario/04-salidas.md`](docs/usuario/04-salidas.md) | Qué archivo genera cada comando |
| [`docs/usuario/05-api-http.md`](docs/usuario/05-api-http.md) | Usar el sistema por red |
| [`BATCH.md`](BATCH.md) | Lotes largos: workers, checkpoints y enfriamiento |

## API

### Python

```python
from voucherflow.api import run, extract, ask

resultado = run("factura.pdf")        # pipeline completo
evidencia = extract("factura.pdf")    # solo la evidencia por campo
print(ask("factura.pdf", "¿Cuál es el total?"))
```

`run` distingue dos cosas que conviene no confundir: un archivo ilegible es una
**falla** (`DocumentoNoProcesableError`), mientras que un gate negativo es una
**conclusión válida** (`estado=rechazado`).

### HTTP (fase 2, opcional)

Segunda superficie para consumidores que no pueden importar el paquete. Es
`http.server` de la stdlib — sin dependencias nuevas — y un binario **aparte**
del CLI:

```bash
voucherflow-http --puerto 8000
curl -s localhost:8000/salud
curl -s -X POST localhost:8000/run -H 'Content-Type: application/json' \
  -d '{"origen": "factura.pdf"}'
```

| Ruta | Equivalente CLI |
|---|---|
| `GET /` | (índice de rutas) |
| `GET /salud` | (liveness) |
| `POST /run` | `voucherflow run` |
| `POST /extract` | `voucherflow extract` |
| `POST /ask` | `voucherflow ask` |
| `GET /version` | `voucherflow --version` |

> **Alcance declarado**: sin autenticación, TLS ni CORS — va detrás de un proxy
> para exponerse — y el default escucha en `127.0.0.1` (publicar requiere
> `--host` explícito y el arranque avisa). Los códigos distinguen `400`
> (petición mal armada), `422` (el documento no se pudo procesar) y `503` (un
> modelo no responde: reintentar sirve). **Un rechazo no es un error HTTP**: se
> responde `200` con `estado=rechazado`.

## Estructura

```
src/voucherflow/
  api.py                    # fachada de alto nivel: run / extract / ask
  orchestrator.py           # encadena las cinco capacidades
  batch.py                  # ejecución por lotes con workers y enfriamiento
  cli/main.py               # CLI `voucherflow` (11 subcomandos, argparse)
  http/                     # API HTTP (http.server de la stdlib)
  processing/               # routing, type_detector, ocr, orientación, exportador
  validation/               # gate qween (doble paso) y vistas
  classification/           # tipo/letra (reglas R1-R7) + cadena contable
  extraction/               # flujos VLM/LLM, key_value, evidencia combinada
  conclusion/               # cruzadas, gaps, consolidación, agente, HITL
  rules/                    # motor de reglas: tipo, raw, cruzadas, gaps, precedencia
  schemas/                  # contrato de evidencia y de resultado (congelados)
  models/                   # OllamaClient, DoclingConverter, ArcaClient
  trace/                    # CaseRecord, índice, agregado y métricas
  settings/                 # configuración centralizada
tests/                      # suite completa (corre sin Ollama, sin Docling y sin red)
  golden/                   # golden set etiquetado y subconjuntos de verificación
scripts/                    # verificación por etapa y utilidades de operación
docs/
  usuario/                  # guía del operador
  plan/                     # documentación técnica: arquitectura, plan y ADR
```

## Contratos

`schemas/` es el **contrato** que consumen las cinco capacidades, congelado bajo
`SCHEMA_VERSION` (ver `schemas/evidence.py`). El validador de
`CombinedEvidence` hace cumplir las invariantes que sostienen la auditabilidad:
un veredicto con `origen=programa` exige certeza alta, uno con `origen=agente_ia`
exige certeza baja, y no puede haber un candidato descartado y disponible a la
vez.

## Test

```bash
python -m pytest
```

La suite corre **sin Ollama**, **sin Docling real** y **sin red**: los modelos se
inyectan por protocolo (`Lector`, `BuscadorEvidencia`, `Agente`) y los dobles
cubren los caminos de error. Los tests que sí necesitan servicios reales están
marcados `@pytest.mark.integration` y no corren por defecto:

```bash
python -m pytest -m integration
```

Las herramientas de `scripts/verificacion/` agregan métricas por capacidad y
salen con código ≠ 0 si algo no cumple su umbral. Corren sin Ollama, sin Docling
y sin red; sirven de evidencia para revisar una etapa, no reemplazan a la suite:

```bash
python scripts/verificacion/gate-comprobante.py       # "¿es comprobante?"
python scripts/verificacion/etapa-clasificacion.py    # tipo/letra y reglas
python scripts/verificacion/etapa-extraccion.py       # normalización y sostén
python scripts/verificacion/etapa-conclusion.py       # veredictos, certeza y HITL
```

Las utilidades de `scripts/operacion/` preparan y diagnostican el corpus
(reducir tokens de visión, generar fixtures). El detalle está en
[`scripts/readme.md`](scripts/readme.md).

El **laboratorio de LLM externos** —ajustar y evaluar el prompt con OpenAI,
DeepSeek o Gemini— es un binario aparte: `voucherflow-lab`. Guía en
[`docs/laboratorio-llm.md`](docs/laboratorio-llm.md).

## Documentación

- **[Guía del operador](docs/usuario/README.md)** — instalación, comandos,
  revisión humana y salidas. Es el punto de entrada para usar el sistema.
- **[Documentación técnica](docs/plan/README.md)** — visión y alcance,
  arquitectura, plan de ejecución, estrategia de calidad y ADRs.
- **[Ideas de diseño](docs/ideas/)** — los documentos que originaron el sistema.

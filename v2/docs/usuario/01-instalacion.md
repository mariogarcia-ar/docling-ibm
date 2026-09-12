# Instalación y requisitos

> [← Volver a la guía del operador](README.md)

## 1. Requisitos

| Componente | Para qué | Cómo se consigue |
|---|---|---|
| **Python ≥ 3.11** | El paquete | `conda`/`pyenv`/el sistema |
| **Ollama** | Los modelos de lenguaje (lectura y decisión) | [ollama.com](https://ollama.com) |
| **Modelos de Ollama** | Ver [§3](#3-modelos-necesarios) | `ollama pull …` |
| **Poppler** (opcional) | Mejora el layout de PDFs con texto (`pdftotext --layout`) | `brew install poppler` / `apt install poppler-utils` |
| **Docling** | Conversión multi-formato | Se instala con el paquete |

**Poppler es opcional pero recomendado.** Para un PDF con capa de texto, el
sistema usa `pdftotext --layout` porque **recupera columnas** que la conversión
genérica aplana — en un comprobante con el emisor a la izquierda y los importes a
la derecha, eso cambia lo que se lee. Sin poppler, el sistema usa Docling igual y
lo deja anotado: **no falla**, pero el layout puede quedar peor.

## 2. Instalar el paquete

```bash
# Desde la raíz del repo
python -m pip install -e v2/

# O desde v2/
cd v2 && python -m pip install -e .
```

Esto deja el comando **`voucherflow`** disponible en la terminal. Para verificar:

```bash
voucherflow --version
voucherflow --help
```

## 3. Modelos necesarios

El sistema usa **tres roles**, cada uno con su modelo configurable:

| Rol | Para qué | Modelo por defecto |
|---|---|---|
| `ocr` | Convertir el documento a texto (no es un modelo de Ollama) | `docling` |
| `vlm` | Mirar la **imagen** del comprobante | `qwen2.5vl:3b` |
| `llm` | Leer el **texto** y decidir | `qwen2.5:7b` |
| `agente` | Resolver casos que el código no pudo concluir | `qwen2.5:7b` |

```bash
ollama pull qwen2.5vl:3b
ollama pull qwen2.5:7b
ollama serve          # si no está corriendo como servicio
```

Verificar que respondan:

```bash
ollama list
curl http://localhost:11434/api/tags
```

> **Sobre los roles `vlm` y `llm`**: el sistema corre **siempre los dos** para
> leer un comprobante y después compara las lecturas (una mira la imagen, la otra
> el texto). No es que elija uno: leer por dos vías distintas y contrastarlas es
> lo que permite detectar una lectura floja.

## 4. Configuración

El sistema funciona **sin configurar nada** (usa los valores por defecto). Se
ajusta con un archivo YAML, variables de entorno o las banderas del comando.

### Orden de prioridad

```
bandera del comando  >  variable de entorno  >  voucherflow.yaml  >  valor por defecto
```

### Archivo `voucherflow.yaml`

Se busca en el directorio donde se ejecuta:

```yaml
workers: 4
cooling:
  enabled: true
  work_window_s: 600     # 10 min de trabajo continuo antes de pausar
  cool_down_s: 120       # 2 min de enfriamiento
ollama:
  url: http://localhost:11434
  timeout_s: 120
  max_reintentos: 3
modelos:
  vlm:    { rol: vlm,    modelo: qwen2.5vl:3b, num_ctx: 4096 }
  llm:    { rol: llm,    modelo: qwen2.5:7b,   num_ctx: 8192 }
  agente: { rol: agente, modelo: qwen2.5:7b,   num_ctx: 8192 }
```

### Variables de entorno

Con **doble guion bajo** para separar los niveles:

```bash
export VOUCHERFLOW__OLLAMA__URL=http://localhost:11434
export VOUCHERFLOW__WORKERS=4
export VOUCHERFLOW__COOLING__ENABLED=1
export VOUCHERFLOW__COOLING__WORK_WINDOW_S=600
export VOUCHERFLOW__COOLING__COOL_DOWN_S=120
```

Las variables de entorno tienen **máxima prioridad** (salvo las banderas del
comando).

## 5. Verificar que quedó bien

```bash
# 1. El paquete está instalado y el CLI responde
voucherflow --version

# 2. Ollama responde y tiene los modelos
ollama list

# 3. Una corrida de humo sobre un documento de texto
#    (no necesita Ollama: es texto plano)
printf 'FACTURA A\nACME SA\nCUIT 30-12345678-9\n' > /tmp/prueba.md
voucherflow process /tmp/prueba.md -o /tmp/salida
ls /tmp/salida
```

Si los tres pasos funcionan, el sistema está listo.

## 6. Problemas frecuentes

| Síntoma | Causa probable | Qué hacer |
|---|---|---|
| `command not found: voucherflow` | El paquete no está instalado en el entorno activo | `python -m pip install -e v2/` con el entorno activado |
| Error de conexión con Ollama | El servicio no está corriendo | `ollama serve` |
| Un modelo no está instalado | Falta el `ollama pull` | `ollama pull qwen2.5vl:3b` |
| Los PDFs salen mal maquetados | No hay poppler | `brew install poppler` |
| Una corrida tarda mucho | Está enfriando entre ciclos | Ver el bloque `lote` del agregado: `enfriamientos` y `segundos_enfriados` |
| Un documento tarda y después falla | El modelo no responde en el `timeout` | Subir `ollama.timeout_s` en la configuración |

## 7. Entornos sin GPU

El procesamiento con modelos es lo lento; el resto es liviano. Para probar el
sistema **sin** modelos (por ejemplo, para verificar la instalación):

- Los documentos de **texto plano** (`.md`, `.txt`) no necesitan modelos para
  procesarse.
- Las **herramientas de inspección** del repo (`scripts/`) corren sin modelos y
  sin red: sirven para verificar que todo está en su lugar.
- La **suite de tests** (`python -m pytest`) corre sin Ollama y sin Docling: no
  necesita GPU ni modelos descargados.

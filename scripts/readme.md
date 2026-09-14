# `scripts/` — verificación por etapa y utilidades operativas

Acá viven dos tipos de herramienta, y **ninguna** es el producto: el paquete es
`src/voucherflow/`. Estas son herramientas para verificarlo y para preparar el
corpus.

## 1. `verificacion/` — reportes agregados por etapa

Miden el comportamiento de cada capacidad y salen con código ≠ 0 si algo no
cumple su umbral. Se pueden correr en cualquier entorno: **sin Ollama, sin
Docling real y sin red**.

| Script | Qué reporta |
|---|---|
| `verificacion/gate-comprobante.py` | Exactitud del gate "¿es comprobante?", % de indeterminación y % de no-comprobantes que no llegan a extracción. Tiene un nivel real que sí usa Ollama (`--manual`). |
| `verificacion/etapa-clasificacion.py` | Exactitud de letra por categoría, alerta R7 y cruce negocio-vs-documento. |
| `verificacion/etapa-extraccion.py` | Exactitud de las reglas de normalización, paridad estructural de los campos y % de campos con fragmento de sustento. |
| `verificacion/etapa-conclusion.py` | Composición de los veredictos, certeza, cobertura HITL y desacuerdos VLM/LLM. |
| `verificacion/documentacion-usuario.py` | Cobertura y navegación de `docs/usuario/`: cada comando documentado, cada bandera en su sección, sin enlaces rotos. |

```bash
python scripts/verificacion/etapa-clasificacion.py            # métricas del tramo determinista
python scripts/verificacion/etapa-clasificacion.py --detalle  # + traza por caso
python scripts/verificacion/etapa-conclusion.py --historico salida/cases  # sobre un lote real
```

> Estos reportes **no** reemplazan a `tests/`: la suite (`python -m pytest`) es
> la verificación de referencia y corre en CI. Los de acá existen porque miden
> cosas que un test unitario no puede — exactitud agregada, tasas y cobertura —
> y sirven de evidencia para el cierre de una etapa.

## 2. `operacion/` — preparar y diagnosticar el corpus

Herramientas para trabajar con las imágenes y las APIs **antes** de procesarlas.

| Script | Para qué |
|---|---|
| `operacion/reducir-tokens.py` | Pre-reduce el peso y los tokens de visión de un corpus de imágenes. |
| `operacion/generar-fixtures-negativos.py` | Genera los negativos sintéticos del golden (documentos que **no** son comprobantes), sin PII. |

> El **laboratorio de LLM externos** (extraer y evaluar el prompt con OpenAI,
> DeepSeek o Gemini) se mudó a la librería: es el binario `voucherflow-lab`.
> La guía está en [`docs/laboratorio-llm.md`](../docs/laboratorio-llm.md).

---

## `reducir-tokens.py` — bajar tokens del corpus

Reduce las imágenes a un **lado mayor objetivo** (1024 px por defecto, sin
agrandar nunca), las reencoda con calidad moderada y espeja el árbol de carpetas
bajo una salida (`var/processed/`). Menos bytes en disco/red y **menos tokens de
visión** cuando la imagen después viaja a un VLM.

Porta el loop de shell con `ffmpeg` y le corrige cuatro defectos:

1. `scale=1024:-1` **agranda** las imágenes chicas (más peso y más tokens que el
   original). Acá una imagen que ya entra en el objetivo no se toca.
2. `scale=1024:-1` fija el **ancho**, no el lado mayor: una foto vertical de
   3000×4000 salía 1024×1365 en vez de 768×1024 (~45% más tokens). Acá se
   reduce por lado mayor.
3. Siempre escribía JPEG, incluso con salida `.png` (bytes JPEG en un archivo
   `.png`). Acá el formato se elige por extensión, o se fuerza con `--formato jpg`.
4. Sin paralelismo, sin reanudación ni reporte. Acá hay `--workers`, se saltea
   el destino ya existente y hay resumen + reporte JSON.

**Dimensiones alineadas al VLM.** Las dimensiones destino se calculan con
`voucherflow.validation.prompt_qween.dimensiones_objetivo_vlm` — el **mismo**
`smart_resize` de Qwen2.5-VL que usa el pipeline — así el servidor no
re-escalea y el conteo de tokens es predecible. Los defaults (1024 px / calidad
80 / piso de lado menor 256) salen de la librería: una sola fuente de verdad con
la vista de revisión (E-QWE-2). Si la librería no se puede importar, cae a una
alineación local a múltiplos de 28 y lo **declara** en el resumen
(`defaults_desde`).

```bash
# Siempre conviene empezar midiendo (no escribe nada)
python scripts/operacion/reducir-tokens.py var/files --solo-medir

# Elegir la carpeta de salida (default: procesadas)
python scripts/operacion/reducir-tokens.py var/files -o salida

# Prueba con 20 imágenes, con una línea por archivo
python scripts/operacion/reducir-tokens.py var/files --limite 20 --detalle

# Corpus completo: 4 workers y reporte JSON
python scripts/operacion/reducir-tokens.py var/files -o salida --workers 4 \
  --reporte salida/reporte.json

# Otra resolución (p. ej. antes de un OCR clásico)
python scripts/operacion/reducir-tokens.py var/files --lado-mayor 1536 --workers 4
```

**Carpeta de salida (`-o` / `--salida`).** El árbol se espeja desde `--raiz` (o
desde el nivel que no sea un mes), sin repetir el nombre de la carpeta de
entrada. Con `var/var/files/2025-08/2D2C9343/foto.jpg`:

| Invocación | Archivo resultante |
|---|---|
| `var/files` | `var/processed/2025-08/2D2C9343/foto.jpg` |
| `var/files/2025-08` | `var/processed/2025-08/2D2C9343/foto.jpg` |
| `var/var/files/2025-08/2D2C9343` | `var/processed/2025-08/2D2C9343/foto.jpg` |
| `-o /tmp/reducido` | `/tmp/reducido/2025-08/2D2C9343/foto.jpg` |

⚠️ **La misma imagen escribe siempre el mismo archivo**, sin importar con qué
subcarpeta se invoque. La raíz **no** se deriva de la ruta pasada: se **sube**
hasta el primer nivel que no sea un mes (`2025-08`) ni un hash de lote
(`2D2C9343`). Sin esto, procesar `var/files` y después `var/files/2025-08` escribía dos
árboles distintos y **volvía a pagar** por lo ya procesado. Para corpus con otra
forma, fijá `--raiz`.

⚠️ `-o` es **solo para las imágenes**; el JSON del reporte va aparte con
`--reporte`. Y si la salida cae dentro de la entrada (p. ej. `-o .`), el script
**excluye** del recorrido los archivos que ya están dentro de ella, para no
reprocesar lo reducido.

Salida (ejemplo real, 200 imágenes de `var/files`):

```text
archivos                  : 200
  reducidos               : 199
  omitidos                : 1
  fallos                  : 0
peso                      : 573.6 MiB  →  20.0 MiB   (-96.5%)
tokens de visión (estim.) : 1,423,303  →  214,020   (-85.0%)
```

### Banderas

| Bandera | Efecto |
|---|---|
| `-o, --salida` | Carpeta raíz de salida (default `procesadas`). El árbol se espeja desde `--raiz`. |
| `--raiz DIR` | Raíz desde la cual se espeja el árbol. Si se omite, sube hasta el nivel que no sea mes/hash. |
| `--lado-mayor PX` | Lado mayor objetivo (default 1024). **Nunca agranda.** |
| `--calidad 1-100` | Calidad de reencode (default 80). |
| `--piso-lado-menor PX` | Piso del lado menor, para imágenes muy alargadas (default 256). |
| `--sin-alinear` | No alinear a múltiplos de 28 (solo si el destino NO es Qwen2.5-VL). |
| `--backend pillow\|ffmpeg` | Motor de reencode (default `pillow`). |
| `--formato mismo\|jpg` | Conserva la extensión o fuerza JPEG (default `mismo`). |
| `--extensiones LISTA` | Extensiones a procesar (default `.jpeg,.jpg,.png`). |
| `--forzar` | Reescribe el destino aunque exista; sin esto **reanuda**. |
| `--copiar-no-reducidas` | Copia sin tocar las que ya entran en el objetivo (salida completa). |
| `--solo-medir` | No escribe nada: mide y reporta qué se reduciría. |
| `--workers N` | Concurrencia (default 4). Usá 1 si el equipo se calienta. |
| `--limite N` | Procesa solo las primeras N (0 = todas). |
| `--detalle` | Una línea por archivo (a stderr). |
| `--reporte ARCHIVO.json` | Reporte completo (resumen + por archivo). |

Códigos de salida: `0` ok (o nada que hacer) · `1` hubo fallos · `2` error de uso
o backend no disponible · `130` interrumpido.

### Cosas que conviene saber

- **Es reanudable.** Si el destino existe, se saltea. Ese es el modo de retomar
  un lote cortado; `--forzar` lo reescribe.
- **Mide el destino real al reanudar**, no lo que *habría* calculado: así el
  reporte no miente si el archivo existente se generó con otros parámetros.
- **En `--solo-medir` el peso destino no se mide** (no se escribió) y el resumen
  lo declara en vez de reportar un «-100%» inventado. Los tokens sí se comparan:
  se derivan de las dimensiones calculadas, no del archivo escrito.
- **Los tokens son una estimación** (`ceil(ancho/28) × ceil(alto/28)`, el
  gridding de Qwen2.5-VL). Sirve para comparar antes/después; no es lo que
  reporta el servidor.
- ⚠️ **Cuidado con el OCR clásico.** Reducir *antes* de un OCR por píxeles
  (RapidOCR/EasyOCR vía Docling) puede degradar la letra
  chica. Usá `--solo-medir`, subí `--lado-mayor` o no reduzcas. Para el camino
  **VLM** (la imagen viaja al modelo) reducir es lo correcto.
- **`--backend ffmpeg`** requiere un ffmpeg sano. El script hace un *preflight*
  y, si el binario no arranca (típico en macOS con dependencias de Homebrew
  rotas), falla **una vez** con un mensaje claro en vez de repetir el error de
  dyld por archivo.

---



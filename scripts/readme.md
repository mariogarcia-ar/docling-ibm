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
| `verificacion/acuerdo-extraccion.py` | **Nivel B**: corre el pipeline **de verdad** (Docling + Ollama) sobre las imágenes de `tests/expected-extraction/` y mide el **acuerdo** contra la referencia, campo por campo. ⚠️ **Es el único de esta tabla que necesita Ollama**; por eso no corre en la suite default. ⚠️ No publica un «% de acuerdo» (ver abajo). |
| `verificacion/documentacion-usuario.py` | Cobertura y navegación de `docs/usuario/`: cada comando documentado, cada bandera en su sección, sin enlaces rotos. |

```bash
python scripts/verificacion/etapa-clasificacion.py            # métricas del tramo determinista
python scripts/verificacion/etapa-clasificacion.py --detalle  # + traza por caso
python scripts/verificacion/etapa-conclusion.py --historico salida/cases  # sobre un lote real
python scripts/verificacion/acuerdo-extraccion.py --listar    # qué se puede medir (sin correr nada)
python scripts/verificacion/acuerdo-extraccion.py --limite 3  # medir los primeros 3 documentos
```

### ⚠️ `acuerdo-extraccion.py` mide acuerdo, no exactitud

La referencia es la lectura de **otro modelo** (DeepSeek), no la verdad: tiene
errores medidos (5 de 28 CUIT con el dígito verificador inválido). **Un `difiere`
puede ser un acierto del pipeline.** Por eso el reporte nunca publica un único
«% de acuerdo»: publica el recuento por estado, separa los `ausente` por causa y
**lista los desacuerdos**.

⚠️ **El rol `llm` del repo (`qwen2.5:7b`) puede no estar instalado.** Sin él el
pipeline resuelve **4 de 16 campos** (medido), así que el script **se niega a
correr** en vez de publicar ese piso como si fuera el pipeline. La salida
declarada es `--sustituir-llm`: corre las dos fuentes con el VLM (⚠️ las dos
fuentes pasan a ser el mismo modelo; el reporte lo declara).

> Estos reportes **no** reemplazan a `tests/`: la suite (`python -m pytest`) es
> la verificación de referencia y corre en CI. Los de acá existen porque miden
> cosas que un test unitario no puede — exactitud agregada, tasas y cobertura —
> y sirven de evidencia para el cierre de una etapa.

## 2. `operacion/` — preparar y diagnosticar el corpus

Herramientas para trabajar con las imágenes y las APIs **antes** de procesarlas.

| Script | Para qué |
|---|---|
| `operacion/generar-fixtures-negativos.py` | Genera los negativos sintéticos del golden (documentos que **no** son comprobantes), sin PII. |
| `operacion/generar-extracciones-esperadas.py` | Gradúa las extracciones pagadas del lab (`var/`) al artefacto versionado `tests/expected-extraction/`. Copia las imágenes faltantes a `tests/fixtures/expected-extraction/`. Es **regenerable y no destructivo** (una corrida ya versionada se respeta). |

> La **conversión de PDF a imágenes** (``pdf-a-imagen.py``) se retiró de acá: es el
> subcomando `voucherflow pdf`. El script usaba `pdf2image`/poppler (una
> dependencia de facto que nadie declaraba) y era una segunda implementación del
> render, del espejado y del recorte que ya estaban en el paquete. Ver
> [`docs/usuario/02-comandos.md`](../docs/usuario/02-comandos.md#pdf--convertir-pdf-a-imágenes).

> La **reducción de imágenes** (pre-reducir peso y tokens del corpus) se mudó a la
> librería: es el subcomando `voucherflow corpus`. Ver más abajo.
> El **laboratorio de LLM externos** (extraer y evaluar el prompt con OpenAI,
> DeepSeek o Gemini) también: es el binario `voucherflow-lab`.
> La guía está en [`docs/laboratorio-llm.md`](../docs/laboratorio-llm.md).

---

## `corpus` — bajar tokens del corpus (se mudó a la librería)

**Ya no es un script de acá.** La reducción de imágenes se refactorizó a
`src/voucherflow/corpus/` y se expone como el subcomando `corpus` del CLI:

```bash
voucherflow corpus var/files --solo-medir
voucherflow corpus var/files -o var/processed --workers 4
```

El motivo del traslado: la capacidad se necesitaba **en el pipeline**, no como
utilidad suelta. Al moverla se corrigieron tres cosas que el script arrastraba —
los helpers de recorrido estaban **duplicados** con `voucherflow.llm.corrida`
(dos copias de la regla que decide dónde se escribe cada archivo, de la que
depende la reanudación), tenía **cero tests** sobre ~1200 líneas, y el reporte se
armaba con dicts sueltos sin contrato.

La guía del comando está en
[`docs/usuario/02-comandos.md`](../docs/usuario/02-comandos.md#corpus--pre-reducir-las-imágenes).


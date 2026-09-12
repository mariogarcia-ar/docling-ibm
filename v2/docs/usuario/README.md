# Guía del operador — `voucherflow`

> **Versión**: v2 · **Fase**: F6 · **Última actualización**: 2026-09-12

Guía para quien **usa** el sistema para procesar comprobantes: cómo instalarlo,
cómo correrlo sobre un archivo o una carpeta, dónde quedan los resultados y qué
hacer cuando un caso queda para revisar.

La documentación técnica (arquitectura, decisiones, plan) vive en
[`../plan/`](../plan/) y no hace falta para operar.

---

## Índice

| Documento | Para qué |
|---|---|
| **[Instalación y requisitos](01-instalacion.md)** | Dejar el sistema funcionando: dependencias, modelos, configuración |
| **[Referencia de comandos](02-comandos.md)** | Los once subcomandos, con sus banderas, salidas y códigos de salida |
| **[Qué archivos genera](04-salidas.md)** | Markdown, checkpoints, agregado del lote y sidecar de trazabilidad |
| **[Trabajo con lotes largos](../../../BATCH.md)** | Workers, checkpoints y enfriamiento (guía dedicada) |
| **[Flujo de revisión humana](03-revision-humana.md)** | Qué hacer con los casos que el sistema manda a revisar |

---

## En 30 segundos

```bash
# Un comprobante: procesar, extraer y concluir en un paso.
voucherflow run factura.pdf

# Una carpeta entera, dejando la trazabilidad guardada.
voucherflow batch files/2025-08 --cases salida/cases -o salida/lote.json
```

El primer comando imprime un JSON con el resultado: si el documento **es** un
comprobante, qué tipo, con qué certeza y qué campos leyó. El segundo recorre las
subcarpetas y deja en `salida/` el agregado del lote y la trazabilidad de cada
caso.

---

## Los tres conceptos que hay que entender

### 1. El resultado tiene un **estado**

Todo caso termina en uno de estos tres estados, y el sistema **nunca** deja un
caso sin estado:

| Estado | Qué significa | Qué hacer |
|---|---|---|
| **`aprobado`** | El comprobante se sostiene: tiene letra, los datos críticos están y nada se contradice | Nada. Se puede usar el resultado |
| **`revision`** | **No se pudo concluir con confianza**, o hay algo que merece un ojo humano | Mirarlo (ver [flujo de revisión](03-revision-humana.md)) |
| **`rechazado`** | El documento **no es** un comprobante, o se contradice de forma dura | No es un error: el sistema concluyó que no sirve |

Un **`rechazado` no es un error de la corrida**. Es una conclusión: el código
miró el documento y determinó que no es un comprobante válido. La corrida termina
bien.

### 2. La **certeza** dice cuánto sabe el sistema, no si le gustó el resultado

| Certeza | Origen | Qué significa |
|---|---|---|
| `alta` | `programa` | Lo decidió el código, con reglas. Es el caso bueno |
| `baja` | `agente_ia` | Lo decidió un modelo, sin reglas que lo respalden. Va a revisión |
| `baja` | `hitl` | Lo decidió una persona |

Un **rechazo firme también es certeza alta**: "esto no es válido" es una
conclusión, no una duda.

### 3. Todo caso se puede **auditar**

Cada resultado puede ir acompañado de un registro con la evidencia: qué leyó cada
fuente, con qué fragmento del documento lo sostiene, qué reglas se dispararon y
quién decidió. Se activa con `--cases DIR` y se consulta con `voucherflow case
show`.

---

## Un flujo de trabajo típico

```bash
# 1. Procesar la carpeta del mes, guardando trazabilidad y el agregado.
voucherflow batch files/2025-08 --workers 4 --cooling on \
  --cases salida/cases -o salida/2025-08.json

# 2. Ver cómo salió el lote.
voucherflow case list --dir salida/cases --filtro estado=revision

# 3. Mirar los que quedaron para revisar.
voucherflow hitl list --dir salida/cases

# 4. Auditar un caso puntual: por qué se decidió lo que se decidió.
voucherflow case show sha256:9f2c… --dir salida/cases
```

El paso 1 es el único que tarda. Los pasos 2 a 4 solo **leen** lo que quedó
guardado: se pueden correr en cualquier momento, sin volver a procesar nada.

---

## Ayuda desde la terminal

Cada comando tiene su propia ayuda, y es la referencia más actualizada:

```bash
voucherflow --help                    # los subcomandos
voucherflow batch --help              # las banderas de uno
voucherflow --version
```

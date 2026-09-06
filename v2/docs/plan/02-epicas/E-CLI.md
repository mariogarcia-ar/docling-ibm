# Épica E-CLI — Cliente (Seguimiento)

> Documento de seguimiento generado a partir de
> [`02-epicas-historias-usuario.md`](../02-epicas-historias-usuario.md) y
> [`05-plan-ejecucion.md`](../05-plan-ejecucion.md).

## 1. Ficha de la épica

| Campo | Valor |
|---|---|
| **Código** | E-CLI |
| **Objetivo(s) que cubre** | OBJ-7 — Entregar un cliente que invoque la librería |
| **Fuente de ideas** | `v2/docs/readme.md` + comandos de `v1/` |
| **Módulo de librería** | `cli/` |
| **Fase(s) del plan** | F6 (T-601..T-606) |
| **Prioridad MoSCoW** | E-CLI-1/2 → Must (MVP); E-CLI-3 (enfriamiento/retries avanzados) → Should; API HTTP (T-606) → fase 2 / Could |
| **Responsable ciclo** | team analysis (BA/SA/PM) → team implementation |
| **Estado épica** | 🔴 Backlog |
| **DoR cumplido** | [ ] pendiente |
| **Fecha inicio** |  |
| **Fecha fin** |  |

## 2. Definition of Done de la épica (criterios de aceptación a nivel épica)

- [ ] CLI `voucherflow` con subcomandos (process/validate/classify/extract/run/batch/ask/arca/case/hitl) que procesa un archivo o una carpeta recursiva.
- [ ] Los comandos reproducen (o superan) la salida de v1 (markdown, JSON de extracción/clasificación/pipeline) respetando `--force`, `--orientation`, `--condicion-impositiva`, `--model` y `--workers`.
- [ ] Modo batch con workers, checkpoints/reanudación y política de enfriamiento (ADR-010): cada worker inicializa su propio convertidor de Docling y la cuenta de enfriamiento inicia cuando TODOS los workers están detenidos.
- [ ] Reintentos con backoff y máximo configurable ante errores transitorios (429 o conexión).
- [ ] Cada resultado genera un JSON sidecar con resultado + evidencia + trazabilidad; en modo lote se puede consolidar en un único JSON agregado.
- [ ] Mapa de paridad v1→v2 verificado sobre carpetas reales de `files/` (DoD de F6 en `05-plan-ejecucion.md`).
- [ ] Documentación de usuario + README v2 actualizados.

## 3. Historias de usuario y seguimiento

### E-CLI-1 · CLI por archivo y por carpeta (equivalente funcional a v1)
- **Estado**: [ ] Pendiente · [ ] En desarrollo · [ ] En QA · [ ] Hecho
- **Responsable**: team analysis / team implementation
- **Como** operador,
  **quiero** un CLI que procese un archivo o una carpeta recursiva con las
  capacidades actuales (OCR, extracción, clasificación, pipeline completo)
  **para** reemplazar los comandos de `v1` sin perder funcionalidad.
- **Criterios de aceptación (Gherkin):**

```gherkin
Dado un archivo imagen/pdf/md
Cuando se ejecuta el CLI con el subcomando correspondiente
Entonces se produce la salida equivalente a v1 (markdown, JSON de extracción/clasificación/pipeline)

Dado una carpeta
Cuando se ejecuta el CLI recursivo
Entonces recorre subcarpetas y procesa todos los archivos soportados
Y respeta --force, --orientation, --condicion-impositiva, --model y --workers

Regla: checkpoint/resumir
  Dado un procesamiento interrumpido
  Cuando se vuelve a ejecutar sobre la misma carpeta
  Entonces retoma desde los checkpoints sin repetir pasos completados
```

### E-CLI-2 · Salidas y sidecars con trazabilidad
- **Estado**: [ ] Pendiente · [ ] En desarrollo · [ ] En QA · [ ] Hecho
- **Responsable**: team analysis / team implementation
- **Como** contador,
  **quiero** que cada resultado incluya la evidencia y trazabilidad del caso
  **para** poder auditar la decisión.
- **Criterios de aceptación (Gherkin):**

```gherkin
Dado un documento procesado por el CLI
Cuando se guarda el resultado
Entonces se genera un JSON con resultado + evidencia + trazabilidad (sidecar)
Y en modo lote se puede consolidar en un único JSON agregado
```

### E-CLI-3 · Manejo de recursos (workers, temperatura, reintentos)
- **Estado**: [ ] Pendiente · [ ] En desarrollo · [ ] En QA · [ ] Hecho
- **Responsable**: team analysis / team implementation
- **Como** operador de máquina local,
  **quiero** controlar paralelismo, pausas de enfriamiento por temperatura y
  reintentos
  **para** no degradar el dispositivo en lotes largos.
- **Criterios de aceptación (Gherkin):**

```gherkin
Dado un lote grande
Cuando se usa --workers N
Entonces cada worker inicializa su propio convertidor de Docling
Y al superar un tiempo de procesamiento continuo se detienen los workers
Y la cuenta de enfriamiento inicia cuando TODOS los workers están detenidos

Regla: reintentos con backoff
  Dado un error transitorio (429 o conexión)
  Cuando se reintenta
  Entonces se aplica backoff y un máximo de intentos configurable
```

## 4. Bitácora de seguimiento

| Fecha | Acción / hito | Responsable | Estado |
|---|---|---|---|
|  | | | |

## 5. Referencias cruzadas

- Historias fuente: [`02-epicas-historias-usuario.md`](../02-epicas-historias-usuario.md)
- Objetivo y alcance: [`01-vision-alcance.md`](../01-vision-alcance.md) (OBJ-7)
- Fases/tareas/DoR-DoD: [`05-plan-ejecucion.md`](../05-plan-ejecucion.md) (F6: T-601..T-606)
- Calidad/golden set: [`06-estrategia-calidad.md`](../06-estrategia-calidad.md)
- Dependencias: E-LIB (librería que invoca), F1-F5 (capacidades expuestas por subcomando)

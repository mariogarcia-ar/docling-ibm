# Épica E-LIB — Librería robusta (Seguimiento)

> Documento de seguimiento generado a partir de
> [`02-epicas-historias-usuario.md`](../02-epicas-historias-usuario.md) y
> [`05-plan-ejecucion.md`](../05-plan-ejecucion.md).

## 1. Ficha de la épica

| Campo | Valor |
|---|---|
| **Código** | E-LIB |
| **Objetivo(s) que cubre** | OBJ-6 — Entregar una librería robusta con API estable y testeable |
| **Fuente de ideas** | `v2/docs/readme.md` ("librería robusta y luego un cliente") |
| **Módulo de librería** | núcleo `voucherflow` |
| **Fase(s) del plan** | F0 (T-001/T-002/T-005) y transversal F1-F5 (cada refactor aterriza en un módulo testeable) |
| **Prioridad MoSCoW** | Must (MVP) |
| **Responsable ciclo** | team analysis (BA/SA/PM) → team implementation |
| **Estado épica** | 🟡 En implementación (E-LIB-5 cerrada con T-507; el resto de las historias cierra con sus fases) |
| **DoR cumplido** | [x] sí |
| **Fecha inicio** | 2026-09-06 |
| **Fecha fin** | 2026-09-11 (E-LIB-5 / T-507; las demás historias cierran con sus fases) |

## 2. Definition of Done de la épica (criterios de aceptación a nivel épica)

- [ ] La librería expone una API de alto nivel simple (`procesar_documento`, `procesar_imagen`, `validar_y_procesar`, `concluir_caso`, …) con punto de entrada por sub-capacidad (procesar, validar, clasificar, extraer, concluir).
- [ ] El paquete es instalable y portable: al importarse en otro proyecto no depende de la estructura de carpetas de este repo.
- [ ] Los schemas de evidencia (pydantic) están definidos y congelados: rechazan con error de contrato claro si falta campo/valor/fuente/fragmento; `VoucherResult` cumple sus enumerados (certeza, origen, estado).
- [ ] La configuración está centralizada (yaml/env): modelos por rol (OCR, VLM, LLM, agente), URLs y parámetros; por defecto Ollama `http://localhost:11434` con manejo claro de errores de conexión (equivalente a v1 `ask_ollama`).
- [ ] El motor de reglas determinísticas es código declarativo y testeable: cada regla reporta id, condición evaluada y resultado; permite ordenar por prioridad y registrar cuáles se dispararon; extensible sin tocar el resto del pipeline.
- [ ] Observabilidad: logs estructurados, diagnóstico por caso (del SDK/cliente de modelo ante latencia alta o status inesperado) y métricas del pipeline (documentos procesados, % certeza alta, % agente, % rechazados, latencias).
- [ ] Paridad verificable con v1 sobre el golden set donde aplique (DoD de fase en `05-plan-ejecucion.md`).
- [ ] Documentación/contratos actualizados (README/ADR si cambia una decisión).

## 3. Historias de usuario y seguimiento

### E-LIB-1 · API estable de alto nivel
- **Estado**: [ ] Pendiente · [ ] En desarrollo · [ ] En QA · [ ] Hecho
- **Responsable**: team analysis / team implementation
- **Como** desarrollador,
  **quiero** una API de librería simple (`processar_documento`, `procesar_imagen`,
  `validar_y_procesar`, `concluir_caso`, …) que abstraiga el pipeline
  **para** integrarla sin conocer los detalles internos.
- **Criterios de aceptación (Gherkin):**

```gherkin
Dado un documento (bytes o ruta)
Cuando llamo a la API de alto nivel
Entonces obtengo un resultado tipado (VoucherResult) con estado, certeza, origen y trazabilidad
Y la API expone un punto de entrada por sub-capacidad (procesar, validar, clasificar, extraer, concluir)

Regla: sin acoplamiento a scripts
  Dado el paquete instalado
  Cuando se importa en otro proyecto
  Entonces no depende de la estructura de carpetas de este repo
```

### E-LIB-2 · Esquemas de evidencia validados (contrato)
- **Estado**: [ ] Pendiente · [ ] En desarrollo · [ ] En QA · [ ] Hecho
- **Responsable**: team analysis / team implementation
- **Como** equipo,
  **quiero** modelos de datos tipados (pydantic) para evidencia, combinación,
  resultado y trazabilidad
  **para** que VLM/LLM/reglas/agente hablen el mismo contrato y los errores de
  schema se detecten temprano.
- **Criterios de aceptación (Gherkin):**

```gherkin
Dado un JSON devuelto por un flujo
Cuando se valida contra el schema de evidencia
Entonces se rechaza si faltan campo/valor/fuente/fragmento
Y se registra un error de contrato claro

Dado un resultado de conclusión
Cuando se valida
Entonces cumple el schema VoucherResult con sus enumerados (certeza, origen, estado)
```

### E-LIB-3 · Configuración centralizada y clientes de modelo
- **Estado**: [ ] Pendiente · [ ] En desarrollo · [ ] En QA · [ ] Hecho
- **Responsable**: team analysis / team implementation
- **Como** operador,
  **quiero** configurar modelos, endpoints, tiempos y políticas en un solo lugar
  **para** no repetir la configuración en cada script (hoy está dispersa en v1).
- **Criterios de aceptación (Gherkin):**

```gherkin
Dado un archivo de configuración (yaml/env)
Cuando se inicializa la librería
Entonces se cargan modelos por rol (OCR, VLM, LLM, agente), URLs y parámetros

Regla: Ollama por defecto
  Dado que no hay configuración explícita
  Cuando se usa la librería
  Entonces usa http://localhost:11434 y el modelo por defecto actual
  Y maneja errores de conexión con mensajes claros (equivalente a v1 ask_ollama)
```

### E-LIB-4 · Motor de reglas determinísticas
- **Estado**: [ ] Pendiente · [ ] En desarrollo · [ ] En QA · [ ] Hecho
- **Responsable**: team analysis / team implementation
- **Como** equipo de negocio,
  **quiero** que las reglas (R1-R7, fast-fail, precedencia por campo, gaps) sean
  código declarativo y testeable
  **para** madurar la casuística sin depender de que un modelo "se sienta seguro".
- **Criterios de aceptación (Gherkin):**

```gherkin
Dado un set de reglas declarativas
Cuando se ejecuta sobre evidencia
Entonces cada regla reporta id, condición evaluada y resultado
Y el motor permite ordenar reglas por prioridad y registrar cuáles se dispararon

Regla: extensibilidad
  Dado que el HITL detecta un caso nuevo recurrente
  Cuando se incorpora una regla nueva
  Entonces se agrega sin modificar el resto del pipeline
```

### E-LIB-5 · Trazabilidad y diagnóstico (observabilidad)
- **Estado**: [ ] Pendiente · [ ] En desarrollo · [ ] En QA · [x] **Hecho** (F0/T-005 + F5/T-507, 2026-09-11)
- **Responsable**: team analysis / team implementation
- **Como** equipo de operaciones,
  **quiero** logs estructurados, diagnóstico por caso y métricas del pipeline
  **para** monitorear latencias, errores 429 y cuellos de botella.
- **Criterios de aceptación (Gherkin):**

```gherkin
Dado un caso con latencia mayor a un umbral o con status inesperado
Cuando se procesa
Entonces el log incluye el diagnóstico del SDK/cliente de modelo

Dado el procesamiento de un lote
Cuando termina
Entonces se producen métricas: documentos procesados, % certeza alta, % agente, % rechazados, latencias
```

## 4. Bitácora de seguimiento

| Fecha | Acción / hito | Responsable | Estado |
|---|---|---|---|
| 2026-09-11 | **E-LIB-5 cerrada (F0/T-005 + F5/T-507)**: la historia tiene dos mitades y las dos están implementadas. (1) **Diagnóstico del cliente de modelos** — `models/ollama.py::_diagnostico` registra latencia, status y reintentos ante **latencia sobre el umbral** o **status inesperado** (Gherkin: "el log incluye el diagnóstico del SDK/cliente"), desde F0/T-005. (2) **Métricas del pipeline** — `trace/metricas.py` (T-507) calcula sobre el `CaseRecord` persistido de T-506: documentos procesados, % certeza alta por programa, % agente IA, % rechazado, **acuerdo VLM/LLM**, cobertura HITL (obligatorios y muestreo) y tasa de alertas R7 (`06-estrategia-calidad.md` §5). Cada métrica usa su propio denominador, declara por qué cuando no es calculable (**"no saber" ≠ "saber que es cero"**) y el reporte se versiona (librería + contrato + formato de traza) para que dos corridas sean comparables. Suites: `tests/test_metricas_t507.py` (47) y `scripts/F5/t507.py` (6/6 + 11/11, con modo `--historico DIR` para un lote real). **Nota de honestidad**: las métricas agregan el histórico; **no** recalculan el pipeline ni duplican el diagnóstico del cliente. | team implementation | Hecho |

## 5. Referencias cruzadas

- Historias fuente: [`02-epicas-historias-usuario.md`](../02-epicas-historias-usuario.md)
- Objetivo y alcance: [`01-vision-alcance.md`](../01-vision-alcance.md) (OBJ-6)
- Fases/tareas/DoR-DoD: [`05-plan-ejecucion.md`](../05-plan-ejecucion.md) (F0: T-001/T-002/T-005; F5: T-507 métricas)
- Calidad/golden set: [`06-estrategia-calidad.md`](../06-estrategia-calidad.md)
- Dependencias: transversal — E-DOC, E-QWE, E-CLAS, E-EXT, E-CONC aterrizan en módulos de la librería; E-CLI la invoca

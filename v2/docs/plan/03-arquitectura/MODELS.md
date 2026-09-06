# Módulo models — Adaptadores de modelo (Seguimiento)

> Documento de seguimiento generado a partir de
> [`03-arquitectura-solucion.md`](../03-arquitectura-solucion.md).

## 1. Ficha del módulo

| Campo | Valor |
|---|---|
| **Módulo (paquete)** | `voucherflow/models/` (doc 03 §11: `ollama.py` — chat VLM/LLM; `docling.py` — adaptador Docling; `arca.py` — opcional) |
| **Responsabilidad** | Encapsular los clientes de modelo y servicios externos para que los módulos de capacidad no conozcan detalles de red/protocolo: `OllamaClient` (chat VLM/LLM con manejo de 429/backoff y log de diagnóstico), `DoclingConverter` (conversión multi-formato) y `ArcaClient` (consulta WSCDC/padrón, opcional, con límite de reintentos y timeout). |
| **Épicas asociadas** | E-LIB-3 (configuración centralizada y clientes de modelo), E-DOC (adaptador Docling T-006), E-CONC-2/ADR-003 (ArcaClient en conclusión) |
| **Fase(s) del plan** | F0 (T-005 `OllamaClient` con retry/diagnóstico, T-006 adaptador Docling); consumido por F1-F6; `ArcaClient` en F5 (T-502 hook ARCA, opcional) |
| **Contratos que expone/consume** | Expone: `OllamaClient.ask()` (VLM/LLM), `DoclingConverter.convert()` y `ArcaClient.consultar()` (opcional). Consume: configuración centralizada `settings/` (modelos por rol: OCR, VLM, LLM, agente; URLs y parámetros). |
| **ADRs relacionados** | ADR-007 (layout del paquete); ADR-003 (ArcaClient como adaptador opcional activado por configuración); ADR-008 (el agente usa `OllamaClient` en la conclusión); ADR-010 (config, indirecto: manejo de 429/backoff y timeout). Decisiones heredadas de v1: Ollama `localhost:11434` por defecto (VLM `qwen2.5vl:3b`), Docling como motor. |
| **Interfaces clave** | `OllamaClient` — encapsula `ask_ollama` de v1 (URL, `json_format`, `num_ctx`, manejo de 429/backoff, log de diagnóstico); `DoclingConverter` — adaptador sobre Docling para conversión multi-formato; `ArcaClient` — consulta WSCDC/padrón (reutiliza lógica de `v1/wip/consultar_arca.py`), con límite de reintentos y timeout. |
| **Responsable ciclo** | team analysis (BA/SA/PM) → team implementation |
| **Estado de diseño** | 🔴 Borrador |
| **Fecha inicio** |  |
| **Fecha fin** |  |

## 2. Estado de trazabilidad del módulo

| Elemento de arquitectura (doc 03) | Sección | Épica/Historia | Fase/Tarea | Estado |
|---|---|---|---|---|
| `OllamaClient` (chat VLM/LLM: URL, json_format, num_ctx, 429/backoff, diagnóstico) | §4.6 | E-LIB-3 | F0 / T-005 | [ ] pendiente |
| `DoclingConverter` (adaptador conversión multi-formato) | §4.6 | E-DOC | F0 / T-006 | [ ] pendiente |
| `ArcaClient` (opcional, WSCDC/padrón, límite reintentos + timeout) | §4.6 | E-CONC-2 | F5 / T-502 (hook opcional, ADR-003) | [ ] pendiente |
| Configuración centralizada `settings/` (yaml + env): modelos por rol | §3 (CONFIG) + §11 | E-LIB-3 | F0 / T-005 | [ ] pendiente |
| Manejo de errores de conexión con mensajes claros (equivalente v1 `ask_ollama`) | §4.6 | E-LIB-3 | F0 / T-005 | [ ] pendiente |
| C4 nivel 2: PROC/VAL/EXT/CONC → MODELS; EVID → MODELS | §3 | E-LIB-3 | F0 / T-005 | [ ] pendiente |
| Agente IA de la conclusión usa `OllamaClient` (decisión estructurada) | §4.6 + ADR-008 | E-CONC-3 | F5 / T-504 | [ ] pendiente |
| Cada worker con su propio `DoclingConverter` (paralelismo NFR) | §10 | E-CLI-1/3 | F6 / T-602 | [ ] pendiente |

## 3. Definition of Design / contratos a congelar

- [ ] Interfaz pública acordada: firmas de `OllamaClient` (parámetros compatibles con `ask_ollama` de v1) y `DoclingConverter`; contrato de errores (429/timeout) con backoff y diagnóstico (E-LIB-3).
- [ ] Contrato de entrada/salida alineado al schema de evidencia: las respuestas de los modelos se validan contra el schema de evidencia en los módulos consumidores (extraction/classification), no en `models/`.
- [ ] ADR(s) asociado(s) resueltos: ADR-007 (layout), ADR-003 (opcionalidad de `ArcaClient` en MVP), ADR-008 (agente vía `OllamaClient`).
- [ ] Casos de golden set / tests que lo validan: tests con mock de `OllamaClient` (R-04) para 429/backoff/timeout/mensajes claros; test del adaptador Docling multi-formato (T-006).

## 4. Decisiones abiertas que lo afectan

- **ADR-007 (D-7)** — Layout/nombre del paquete: decide la ubicación de `models/` y de `settings/`.
- **ADR-003 (D-3)** — Si `ArcaClient` queda como adaptador opcional desactivado en MVP (recomendación) o se activa con casos CAE legibles; afecta F5/T-502.
- **ADR-008 (D-8)** — Implementación del agente IA como llamada a Ollama vía `OllamaClient` con prompt estructurado; el contrato del agente queda estable para reemplazo futuro.
- **Decisión heredada de v1** — Modelo VLM por defecto `qwen2.5vl:3b` en `localhost:11434` (configurable) — se conserva.
- **R-04 (riesgo)** — Modelos locales no disponibles/lentos: mitigado por retry/backoff en `OllamaClient` (T-005).

## 5. Bitácora de seguimiento del módulo

| Fecha | Acción / hito | Responsable | Estado |
|---|---|---|---|
| _(vacío)_ | | | |

# voucherflow

Librería robusta de procesamiento documental de la **versión 2 (v2)** del
sistema documental que hoy vive como scripts sueltos en `v1/`.

`voucherflow` es el paquete instalable (`src/` layout, decisión **ADR-007**) que
alojará, fase a fase, los refactors de las 5 capacidades:

1. `processing` — procesamiento Docling multi-formato (F1)
2. `validation` — gate "¿es comprobante?" doble-paso qween (F2)
3. `classification` — tipo/letra + clasificación contable (F3)
4. `extraction` — extracción VLM + LLM con evidencia (F4)
5. `conclusion` — reglas → agente → HITL (F5)

Más los módulos transversales ya esbozados en F0: `schemas` (contrato de
evidencia congelado), `rules` (motor de reglas), `models` (adaptadores
`OllamaClient` y `DoclingConverter`), `trace` (trazabilidad/`CaseRecord`) y
`settings` (configuración centralizada).

> **Estado**: Fases F0–F4 con DoD verificado. **F4 — Extracción está cerrada**
> (T-401: flujos VLM+LLM en paralelo devolviendo evidencia con el contrato de F0;
> T-402: normalización key-value de los campos fiscales/comerciales; T-403:
> pasada 1 por fuente con sostén por forma canónica y coherencia interna; T-404:
> combinación con resolución por campo según la precedencia de ADR-002; T-405:
> paridad con v1 sobre el subconjunto del golden). El paquete ya tiene
> implementadas las capacidades de procesamiento (F1), validación (F2),
> clasificación (F3) y la extracción completa —contrato, normalización, validación
> por fuente, combinación y paridad— (F4/T-401..T-405); la lógica restante se
> implementa en sus fases (F5–F6, conclusión/HITL y CLI/batch). La suite default
> corre **sin** Ollama ni Docling reales.

## Instalación

```bash
# Desde la raíz del repo (o desde v2/ con `pip install -e .`)
python -m pip install -e v2/
```

Requiere Python >= 3.11. En este repo el entorno es conda `py313_env`
(`python` resuelve a ese entorno).

## Importar

```python
import voucherflow
from voucherflow.schemas.evidence import EvidenceField, Fuente
```

## Estructura

```
v2/
  pyproject.toml
  src/voucherflow/
    __init__.py
    api.py                  # fachada de alto nivel (esqueleto, F5/F6)
    orchestrator.py         # PipelineOrchestrator (esqueleto, F5/F6)
    schemas/
      evidence.py           # contrato de evidencia (F0, congelado)
      result.py             # VoucherResult + CaseRecord (F0, congelado)
    processing/…            # F1 (implementado)
    validation/…            # F2 (implementado)
    classification/…        # F3 (implementado)
    extraction/…            # F4: T-401 (flujos + evidencia), T-402 (key_value), T-403 (raw por fuente), T-404 (combinación) y T-405 (paridad con v1)
    conclusion/…            # esqueleto (F5)
    rules/…                 # F3 (R1-R7 + raw) y F4 (precedencia por campo); cruzadas/gaps en F5 (esqueleto)
    models/
      ollama.py             # OllamaClient (F0, implementado)
      docling.py            # DoclingConverter + ProcessedDocument (F0)
      arca.py               # esqueleto (F5, opcional)
    trace/…                 # esqueleto (F5)
    settings/
      config.py             # Settings (F0, implementado)
  tests/                    # suite de F0
    golden/                 # golden set inicial (índice CSV, ver README)
    golden/F4/              # F4/T-405: subconjunto de paridad (0.1-f4) + README
```

## Contrato versionado

Los schemas de `schemas/` son el **contrato** que consumen F3/F4/F5. Están
congelados bajo `SCHEMA_VERSION` (ver `schemas/evidence.py`). Cualquier cambio
debe seguir el criterio documentado allí.

## Test

```bash
cd v2
python -m pytest tests -q
```

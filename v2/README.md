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

> **Estado**: Fases F0–F5 con DoD verificado (F5 cerrada con T-501..T-507:
> reglas cruzadas de la pasada 2 sobre la evidencia combinada —negocio, fast-fail
> y conflicto R7—, búsqueda acotada de evidencia adicional, consolidación del
> `VoucherResult`, escalado al agente IA con blindaje, **cola HITL** con muestreo
> de auditoría y feedback, **trazabilidad `CaseRecord` persistida** en sidecar con
> escritura atómica + índice consultable, y **métricas** del lote sobre ese
> histórico). **F6 en implementación**: **T-601** entregó el **cliente
> `voucherflow`** (once subcomandos: `process`, `validate`, `classify`, `extract`,
> `extract-detect`, `run`, `batch`, `ask`, `arca`, `case`, `hitl`), el
> **orquestador** que encadena processing → validation → extraction → combinación
> → conclusión → traza y la **fachada** `api.extract`/`api.run`/`api.ask`;
> **T-602** el **modo batch**: workers (cada uno con su convertidor de Docling),
> checkpoints/reanudación por hash del contenido y la política de enfriamiento del
> ADR-010 —la cuenta arranca cuando el pool está detenido, es decir cuando **todos**
> los workers pararon—; y **T-603** la **salida agregada**: un único JSON por lote
> con una entrada por documento (veredicto + puntero al sidecar), la síntesis y las
> métricas"; y **T-604** el **mapa de paridad v1→v2** medido en tres niveles
> deterministas (procedencia, superficie y artefactos) con el **corte de v1**
> declarado; y **T-605** la **documentación de usuario** —la guía del operador
> (`docs/usuario/`, cinco documentos: instalación, referencia de los once
> subcomandos, salidas, revisión humana e índice) y este README— con la cobertura
> y los enlaces **verificados por tests** contra el contrato del CLI; y **T-606**
> la **API HTTP básica** (fase 2, no bloqueante), que expone `run`/`extract`/`ask`
> por red con `http.server` de la **stdlib** (sin dependencias nuevas) como un
> binario **aparte** del CLI. Con T-606 la **fase F6 queda completa**: el paquete
> tiene implementadas las capacidades de procesamiento (F1), validación (F2),
> clasificación (F3), la extracción completa (F4), la conclusión con HITL (F5) y
> los clientes CLI/batch/HTTP (F6). La
> suite default corre **sin** Ollama, **sin** Docling reales y **sin** red.

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
    api.py                  # fachada de alto nivel (F6/T-601: extract/run/ask)
    orchestrator.py         # PipelineOrchestrator (F6/T-601: ejecutar/ejecutar_lote)
    cli/
      main.py               # CLI `voucherflow` (F6/T-601: 11 subcomandos, argparse)
    http/
      server.py             # API HTTP (F6/T-606: 6 rutas, http.server de la stdlib)
      __main__.py           # arranque: `python -m voucherflow.http` / `voucherflow-http`
    schemas/
      evidence.py           # contrato de evidencia (F0, congelado)
      result.py             # VoucherResult + CaseRecord (F0, congelado)
    processing/…            # F1 (implementado)
    validation/…            # F2 (implementado)
    classification/…        # F3 (implementado)
    extraction/…            # F4: T-401 (flujos + evidencia), T-402 (key_value), T-403 (raw por fuente), T-404 (combinación) y T-405 (paridad con v1)
    conclusion/…            # F5: T-501..T-505 (cruzadas, gaps, consolidación, agente, HITL)
    trace/…                 # F5: T-506/T-507 (CaseRecord: sidecar + índice + métricas); F6/T-603: agregado.py (salida agregada)
    rules/…                 # F3 (R1-R7 + raw), F4 (precedencia por campo) y F5 (cruzadas de la pasada 2); gaps en F5
    models/
      ollama.py             # OllamaClient (F0, implementado)
      docling.py            # DoclingConverter + ProcessedDocument (F0)
      arca.py               # esqueleto (F5, opcional)
    settings/
      config.py             # Settings (F0, implementado)
  tests/                    # suite de F0..F6
    golden/                 # golden set inicial (índice CSV, ver README)
    golden/F4/              # F4/T-405: subconjunto de paridad (0.1-f4) + README
    golden/F6/              # F6/T-604: subconjunto de paridad CLI (0.1-f6) + README
  docs/
    usuario/                # F6/T-605: guía del operador (5 documentos)
    plan/                   # documentación técnica (arquitectura, plan, ADR)
```

## Uso del cliente (F6/T-601)

```bash
voucherflow run factura.pdf                 # pipeline completo de un documento
voucherflow batch files/2025-08 -o lote.json # carpeta recursiva con workers (T-602)
voucherflow process img.jpg -o salida/      # markdown de F1
voucherflow classify factura.md             # tipo/letra + cadena contable (F3)
voucherflow ask factura.pdf -q "¿Cuál es el total?"
voucherflow case list --dir salida/cases    # trazabilidad persistida (F5/T-506)
voucherflow case aggregate --dir salida/cases  # agregado del lote (F6/T-603)
voucherflow hitl list --dir salida/cases    # cola de revisión (F5/T-505)
```

Cada comando escribe el dato a `stdout` y el progreso a `stderr`; `main()`
devuelve el **código de salida** (≠ 0 si la corrida falla). Los flags comunes son
`--force`, `--orientation`, `--condicion-impositiva`, `--model` y `--workers`.

**Lotes largos**: `batch` corre con workers, reanuda desde checkpoints y aplica la
política de enfriamiento del ADR-010 (`--workers`, `--force`, `--cooling`,
`--work-window`, `--cool-down`). Guía completa:
[`../BATCH.md`](../BATCH.md).

### Documentación para quien opera

La guía del operador —instalación, cada subcomando con sus banderas y códigos de
salida, dónde quedan los resultados y qué hacer con los casos en revisión— está
en [`docs/usuario/`](docs/usuario/). Empieza por el
[índice](docs/usuario/README.md).

El `--help` de cada comando sigue siendo la fuente más actualizada:
`voucherflow <comando> --help`.

```bash
# Uso embebido (misma fachada que el CLI)
python -c "from voucherflow.api import run; print(run('factura.pdf').estado)"
```

## API HTTP (F6/T-606, fase 2)

Segunda superficie del sistema, para consumidores que no pueden importar el
paquete (otro servicio, un front, un script en otra máquina). Seis rutas:

| Ruta | Equivalente CLI |
|---|---|
| `GET /` | (índice de rutas) |
| `GET /salud` | (liveness) |
| `POST /run` | `voucherflow run` |
| `POST /extract` | `voucherflow extract` |
| `POST /ask` | `voucherflow ask` |
| `GET /version` | `voucherflow --version` |

```bash
voucherflow-http --puerto 8000          # o: python -m voucherflow.http

curl -s localhost:8000/salud
curl -s -X POST localhost:8000/run \
  -H 'Content-Type: application/json' \
  -d '{"origen": "factura.pdf"}'
```

> **Alcance declarado**: es la API **básica** de la fase 2 (MoSCoW *Should*). **No**
> trae autenticación, TLS ni CORS — va detrás de un proxy para exponerse — y por
> eso el default escucha en `127.0.0.1` (publicar requiere `--host` explícito y el
> arranque avisa). Los códigos de estado distinguen `400` (petición mal armada) de
> `422` (el documento no se pudo procesar) y de `503` (un modelo no responde:
> reintentar sirve). **Un rechazo no es un error HTTP**: se responde `200` con
> `estado=rechazado`. Igual que la CLI, **transporta, no reimplementa**: cada ruta
> delega en `api.*`.

## Contrato versionado

Los schemas de `schemas/` son el **contrato** que consumen F3/F4/F5. Están
congelados bajo `SCHEMA_VERSION` (ver `schemas/evidence.py`). Cualquier cambio
debe seguir el criterio documentado allí.

## Test

```bash
cd v2
python -m pytest tests -q
```

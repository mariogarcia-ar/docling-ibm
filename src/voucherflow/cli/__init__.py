"""Cliente CLI de ``voucherflow`` (F6 / T-601, épica E-CLI).

**Fase**: F6 · **Tarea**: T-601 · **Épica**: E-CLI (E-CLI-1/E-CLI-2) · **ADR-007**.

Este paquete es el **cliente** de la librería: la librería primero (E-LIB-1), el
cliente después. La CLI **no** reimplementa nada: cada subcomando delega en la
fachada pública (:mod:`voucherflow.api`) o en el orquestador
(:class:`~voucherflow.orchestrator.PipelineOrchestrator`), que encadenan los
módulos de capacidad de F1–F5.

Equivalencias con v1 (mapa de paridad de T-604; doc 03 §8.2)
-----------------------------------------------------------

======================  ==================================  ==========================
v1                      v2 (CLI)                            Módulo
======================  ==================================  ==========================
``ocr_documents.py``    ``voucherflow process``             processing (F1)
``run_raw.py``          ``voucherflow process --raw``       processing (F1)
``classification_pipeline.py``  ``voucherflow classify``    classification (F3)
``extraction_pipeline.py``      ``voucherflow extract``     extraction (F4)
``document_extraction.py -M 11.1``  ``voucherflow extract-detect``  classification + extracción
``full_pipeline.py``    ``voucherflow run`` / ``batch``     orchestrator (F6)
``ask.py``              ``voucherflow ask``                 models/OllamaClient
``wip/consultar_arca.py``  ``voucherflow arca check``       models/ArcaClient (ADR-003)
—                       ``voucherflow case show/list``      trace (F5/T-506)
—                       ``voucherflow hitl list``           trace + conclusion/hitl
======================  ==================================  ==========================

Decisiones de diseño (T-601)
----------------------------
1. **Sin dependencias nuevas**: ``argparse`` de la stdlib. El extra ``cli`` de
   ``pyproject.toml`` declaraba ``typer`` como posibilidad; el cliente no lo
   necesita y el repo tiene la regla dura de no sumar dependencias. Un CLI que se
   puede leer en un archivo es más auditable que uno que requiere entender un
   framework.
2. **La salida es dato y el error es dato distinto**: los comandos escriben a
   ``stdout`` lo que el operador pidió (markdown, JSON) y a ``stderr`` el
   progreso/errores, con **código de salida** ≠ 0 cuando algo falla. Así el CLI
   se puede encadenar en un script sin parsear texto.
3. **``--workers`` se acepta, el pool es T-602**: el flag viaja al orquestador y
   queda registrado en el detalle de la corrida; el modo batch con workers (cada
   uno con su convertidor de Docling), checkpoints y política de enfriamiento
   (ADR-010) es **T-602**. Aceptar el flag y no declararlo sería peor que no
   aceptarlo: la CLI avisa qué aplicó.
4. **Los sidecars son los de F5/T-506**: ``--cases DIR`` usa el ``CaseRecorder``
   ya implementado (sidecar atómico + índice) en vez de inventar otra
   persistencia. La salida agregada canónica es T-603.
5. **Inyectable para la suite**: :func:`main` acepta un :class:`EntornoCLI` con el
   orquestador y los flujos de salida. Los tests ejercitan los 10 subcomandos
   **sin red, sin Ollama y sin Docling** inyectando dobles, igual que el resto del
   repo.
"""

from __future__ import annotations

from .main import (
    COMANDOS,
    EntornoCLI,
    construir_parser,
    main,
)

__all__ = ["COMANDOS", "EntornoCLI", "construir_parser", "main"]

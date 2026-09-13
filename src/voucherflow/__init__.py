"""voucherflow — librería robusta de procesamiento documental (v2).

Paquete principal del refactor v2 (decisión ADR-007: nombre ``voucherflow`` y
layout ``src/``). Esta es la **Fase F0 (Fundación)**: contiene los contratos de
evidencia congelados (``schemas/``), el esqueleto de los módulos de capacidad
que se implementan en F1–F5, los adaptadores de modelo ``OllamaClient`` y
``DoclingConverter``, y la configuración centralizada ``settings/``.

El paquete es un *consumidor-neutral*: no depende de la estructura de carpetas
del repositorio (ni de los scripts de ``v1/``), de modo que puede instalarse y
usarse desde cualquier proyecto (criterio E-LIB-1: "sin acoplamiento a
scripts").
"""

from __future__ import annotations

__version__ = "0.1.0"

# Versión del contrato de evidencia (schemas/). Congelada en F0 (T-001).
# Ver criterio de cambio en schemas/evidence.py.
SCHEMA_VERSION = "1.0.0"

__all__ = ["__version__", "SCHEMA_VERSION"]

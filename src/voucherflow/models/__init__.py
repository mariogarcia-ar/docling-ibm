"""Adaptadores de modelo y servicios externos (módulo ``models``).

F0/T-005-T-006 (E-LIB-3, E-DOC): ``OllamaClient`` (chat VLM/LLM con retry y
diagnóstico) y ``DoclingConverter`` (conversión multi-formato encapsulada).
``ArcaClient`` (WSCDC/padrón) es opcional y se implementa en F5 (T-502,
ADR-003): aquí queda solo el esqueleto.
"""

from __future__ import annotations

from .ollama import (
    STATUS_REINTENTABLES,
    OllamaClient,
    OllamaError,
    OllamaHTTPError,
    OllamaTimeoutError,
    RespuestaOllama,
)
from .docling import (
    EXTENSIONES_SOPORTADAS,
    Box,
    DoclingConverter,
    ProcessedDocument,
)
from .arca import ArcaClient, ArcaResultado

__all__ = [
    # ollama
    "OllamaClient",
    "OllamaError",
    "OllamaHTTPError",
    "OllamaTimeoutError",
    "RespuestaOllama",
    "STATUS_REINTENTABLES",
    # docling
    "DoclingConverter",
    "ProcessedDocument",
    "Box",
    "EXTENSIONES_SOPORTADAS",
    # arca (esqueleto, F5)
    "ArcaClient",
    "ArcaResultado",
]

"""Detector de tipo de entrada (esqueleto — F1 / T-101).

F1 implementa: decidir entre pdf_texto / pdf_escaneado / imagen / office /
txt-csv-log-html-md y derivar a la ruta correcta (texto nativo, convertir a
imagen + OCR, o extractor específico).

Contrato esbozado (sin implementar en F0):
    detectar(origen: str | Path) -> TipoEntrada
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class TipoEntrada:
    """Clasificación del tipo de entrada (doc 03 §4.1)."""

    tipo: str  # "pdf_texto" | "pdf_escaneado" | "imagen" | "office" | "texto" | "no_soportado"
    ruta_ocr: bool  # True si hay que convertir a imagen/OCR
    motivo: str = ""


# TODO(F1): implementar la detección con fixtures por formato (06 §4 F1).
def detectar(origen: str) -> TipoEntrada:  # pragma: no cover - esqueleto F1
    """Detecta el tipo de entrada de un archivo.

    Nota: esqueleto de F0 — lanza ``NotImplementedError`` hasta F1/T-101.
    """
    raise NotImplementedError("Detector de tipo de entrada: se implementa en F1 (T-101).")

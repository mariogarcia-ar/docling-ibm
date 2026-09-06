"""Selección de motor OCR/VLM + hook (F1 / T-104, épica E-DOC-2).

Elige el motor de transcripción para una imagen: **OCR tradicional** (Docling/
RapidOCR) para impreso estándar y legible, o **VLM** (modelo visual) cuando hay
manuscrito/sello/firma o el OCR clásico no es confiable (doc 02 E-DOC-2, regla
"elección de motor"; doc 00 glosario "OCR" y "Flujo VLM").

Decisión de alcance F1 (subplan §2.2): ``processing`` elige el motor
(``ocr``/``vlm``/``auto``) y expone el **punto de integración** (hook); la
transcripción VLM real queda para F4 — en F1 **no** se llama a Ollama/
``qwen2.5vl``. La transcripción OCR real la hace el adaptador Docling (F0/T-006)
en la orquestación.

Contrato:
    ``MotorOCR`` (enum): ``ocr`` | ``vlm``.
    ``elegir_motor(clasificacion, modo="auto", senal=None) -> MotorOCR``.
    ``transcribir_vlm(...)``: hook que en F1 lanza ``NotImplementedError`` con
    mensaje claro (F4 implementa la llamada real a Ollama).
"""

from __future__ import annotations

from enum import Enum
from typing import Any

from .image_classifier import ClasificacionImagen, sospechar_manuscrito

#: Modos de selección de motor (doc 03 §4.1 / D-11).
MODOS_VALIDOS = frozenset({"ocr", "vlm", "auto"})


class MotorOCR(str, Enum):
    """Motores de transcripción disponibles (doc 00 glosario)."""

    ocr = "ocr"   # OCR tradicional (Docling/RapidOCR): impreso estándar.
    vlm = "vlm"   # Modelo visual (VLM): manuscrito/sello/firma, visual complejo.


def elegir_motor(
    clasificacion: ClasificacionImagen,
    modo: str = "auto",
    senal: dict[str, Any] | None = None,
) -> MotorOCR:
    """Elige el motor de transcripción para una imagen (T-104, E-DOC-2).

    Argumentos:
        clasificacion: resultado de ``clasificar()`` (T-102), con su clase y
            ``motor_sugerido``.
        modo: ``"auto"`` (default, decide por clase/señal), ``"ocr"`` o
            ``"vlm"`` (forzar).
        senal: dict opcional con señales de manuscrito/sello/firma que puede
            reportar un OCR previo (ver ``sospechar_manuscrito``).

    Devuelve:
        ``MotorOCR.ocr`` o ``MotorOCR.vlm``.

    Reglas (doc 02 E-DOC-2):
      - ``modo="vlm"`` → siempre VLM (forzado).
      - ``modo="ocr"`` → siempre OCR (forzado).
      - ``auto``: si hay sospecha de manuscrito/sello/firma (clase
        ``manuscrito`` o señal) → VLM; si no → OCR (impreso estándar).
    """
    if modo not in MODOS_VALIDOS:
        raise ValueError(
            f"Modo de motor inválido: '{modo}'. Válidos: {sorted(MODOS_VALIDOS)} (E-DOC-2)."
        )

    if modo == "vlm":
        return MotorOCR.vlm
    if modo == "ocr":
        return MotorOCR.ocr

    # auto: decidir por clase/señal de manuscrito.
    if clasificacion.clase.value == "manuscrito":
        return MotorOCR.vlm
    if sospechar_manuscrito(clasificacion, senal):
        return MotorOCR.vlm
    if clasificacion.motor_sugerido == "vlm":
        return MotorOCR.vlm
    return MotorOCR.ocr


def transcribir_vlm(origen: str, *args: Any, **kwargs: Any) -> str:
    """Hook de transcripción VLM (F1: NO implementado — se llama en F4).

    Decisión de alcance F1 (subplan §2.2): en F1 ``processing`` solo elige el
    motor y expone este punto de integración; la transcripción VLM real (vía
    Ollama/``qwen2.5vl``) se implementa en F4. Por eso aquí se lanza
    ``NotImplementedError`` con mensaje claro.
    """
    raise NotImplementedError(
        "transcribir_vlm(): la transcripción VLM real se implementa en F4 "
        "(módulo extraction). En F1 solo se selecciona el motor (T-104)."
    )


__all__ = [
    "MODOS_VALIDOS",
    "MotorOCR",
    "elegir_motor",
    "transcribir_vlm",
]

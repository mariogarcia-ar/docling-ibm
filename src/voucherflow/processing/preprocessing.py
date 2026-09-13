"""Preprocesamiento de imagen (F1 / T-103, épica E-DOC-2) — stub heurístico liviano.

Decide **si** una imagen requiere preprocesamiento y **qué tipo** antes de
pasar al motor OCR/VLM (doc 03 §4.1: "Preprocesamiento (perspectiva, calidad,
binarización)" y E-DOC-2 regla "clasificación de imagen": baja resolución o
ruido → mejorar calidad/binarizar antes de OCR).

Decisión de alcance F1 (subplan §2.1): el preprocesamiento se implementa como
**stub heurístico liviano, sin OpenCV/Pillow** (no se agregan dependencias). En
la práctica el preprocesamiento **real** de píxeles (enderezar perspectiva,
binarizar, upscaling) lo realiza Docling al convertir (F0/T-006 ya configura
OCR de página completa). Este módulo:

  - expone :class:`QualityReport` (el ``calidad`` que Documenta
    ``ProcessedDocument.calidad``, doc 03 §4.1);
  - decide con ``evaluar_calidad()`` si la imagen necesita preprocesamiento
    según su clase (T-102) y resolución;
  - deja el hook ``preprocesar()`` que en F1 no transforma píxeles (sin CV) y
    documenta qué haría cada clase en F4+/cuando se integre CV.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from .image_classifier import (
    RESOLUCION_CRITICA_PX,
    RESOLUCION_MINIMA_PX,
    ClaseImagen,
    ClasificacionImagen,
)


@dataclass(frozen=True)
class QualityReport:
    """Reporte de calidad visual de la imagen (doc 03 §4.1 ``calidad``).

    Es el valor que se guarda en ``ProcessedDocument.calidad``. Se construye a
    partir de las características de bajo costo (T-102) sin decodificar
    píxeles.
    """

    requiere_preprocesamiento: bool = False
    acciones: tuple[str, ...] = ()          # ej. ("enderezar_perspectiva",)
    resolucion_lado_menor: int = 0
    motivo: str = ""

    def a_dict(self) -> dict[str, Any]:
        """Serialización plana (para ``ProcessedDocument.calidad``)."""
        return {
            "requiere_preprocesamiento": self.requiere_preprocesamiento,
            "acciones": list(self.acciones),
            "resolucion_lado_menor": self.resolucion_lado_menor,
            "motivo": self.motivo,
        }


#: Acciones de preprocesamiento por clase (doc 02 E-DOC-2 / doc 03 §4.1).
_ACCIONES_POR_CLASE: dict[ClaseImagen, tuple[str, ...]] = {
    # Foto con perspectiva -> enderezar antes de OCR.
    ClaseImagen.foto: ("enderezar_perspectiva",),
    # Escaneo plano limpio -> no tocar (mantener formato original).
    ClaseImagen.escaneo_plano: (),
    # Screenshot -> extraer directo, sin filtros.
    ClaseImagen.screenshot: (),
    # Manuscrito/sello/firma -> priorizar VLM; sin preprocesar para no perder tinta.
    ClaseImagen.manuscrito: (),
    # Desconocida -> aplicar mejora genérica conservadora.
    ClaseImagen.desconocida: ("mejorar_contraste",),
}


def evaluar_calidad(clasificacion: ClasificacionImagen) -> QualityReport:
    """Decide si la imagen requiere preprocesamiento (T-103, E-DOC-2).

    Combina la clase (T-102) con la resolución de bajo costo:

      - Resolución por debajo del mínimo (pero sobre el crítico) → requiere
        ``mejorar_resolucion`` (la lectura será pobre sin upscaling).
      - Clase ``foto`` → requiere ``enderezar_perspectiva`` (E-DOC-2: "foto de
        documento → se endereza la perspectiva antes de OCR").
      - Clase ``desconocida`` → mejora genérica conservadora.
      - ``escaneo_plano``/``screenshot`` a buena resolución → sin acciones.

    Nota: F1 decide por metadatos (sin CV). El refinado por píxeles (contraste,
    ruido real) se evalúa en F4+ cuando haya OCR/VLM que reporte legibilidad.
    """
    carac = clasificacion.caracteristicas
    lado_menor = min(carac.ancho, carac.alto)
    acciones: list[str] = list(_ACCIONES_POR_CLASE.get(clasificacion.clase, ()))
    motivos: list[str] = []

    if lado_menor and lado_menor < RESOLUCION_MINIMA_PX:
        if lado_menor >= RESOLUCION_CRITICA_PX:
            acciones.append("mejorar_resolucion")
            motivos.append(
                f"resolución {lado_menor}px por debajo del mínimo {RESOLUCION_MINIMA_PX}px"
            )
        # (si está bajo el crítico el gate de T-102 ya la rechazó)

    if clasificacion.clase == ClaseImagen.foto:
        motivos.append("foto de documento: enderezar perspectiva antes de OCR (E-DOC-2)")
    elif clasificacion.clase == ClaseImagen.desconocida:
        motivos.append("clase desconocida: mejora genérica conservadora")

    acciones_unicas = tuple(dict.fromkeys(acciones))
    return QualityReport(
        requiere_preprocesamiento=bool(acciones_unicas),
        acciones=acciones_unicas,
        resolucion_lado_menor=lado_menor,
        motivo="; ".join(motivos) if motivos else "imagen en condiciones, sin preprocesamiento (E-DOC-2)",
    )


def preprocesar(origen: str, clasificacion: ClasificacionImagen, quality: QualityReport) -> str:
    """Hook de preprocesamiento (F1: stub sin CV — devuelve la misma ruta).

    En F1 el preprocesamiento real de píxeles lo hace Docling al convertir
    (F0/T-006); por eso este hook **no transforma la imagen** y devuelve la
    ruta original. Documenta qué acciones se aplicarían según ``quality``
    cuando se integre CV en una fase posterior (F4+).

    Argumentos:
        origen: ruta de la imagen.
        clasificacion: resultado de T-102.
        quality: reporte de ``evaluar_calidad()``.

    Devuelve:
        La ruta a procesar (en F1, la misma; sin transformación de píxeles).
    """
    # TODO(F4+): aplicar enderezar_perspectiva / mejorar_resolucion con CV
    # cuando la clase/calidad lo requieran (hoy lo hace Docling).
    return str(origen)


__all__ = [
    "QualityReport",
    "evaluar_calidad",
    "preprocesar",
]

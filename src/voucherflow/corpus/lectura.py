"""Lectura del corpus: qué archivos entran y cómo se convierten en imágenes.

Dos responsabilidades, las dos con la misma idea: **no perder archivos en
silencio**.

* :func:`clasificar` dice qué se va a procesar y qué no, con el motivo. Lo que no
  entra se **declara** en vez de desaparecer del lote.
* :func:`expandir_pdf` convierte un PDF en imágenes (una por página) para que
  entre al lote de reducción. Un PDF es un comprobante válido y, hasta ahora, el
  recorrido lo salteaba por no tener extensión de imagen: 264 documentos del
  corpus real quedaban afuera, 190 de ellos comprobantes fiscales.

El render lo hace :func:`voucherflow.processing.orquestacion.render_pdf_a_jpg`,
que ya estaba validado en el pipeline (recorta a la imagen más grande de la
página, para que un ticket chico centrado en una hoja A4 no quede diminuto).
Reimplementarlo acá sería la segunda copia de una regla que ya costó ajustar.
"""

from __future__ import annotations

import shutil
import tempfile
from collections.abc import Sequence
from dataclasses import dataclass, field
from pathlib import Path

#: Categorías de :func:`clasificar`. Se declaran para que el reporte y los tests
#: hablen del mismo vocabulario.
CATEGORIA_PROCESABLE = "procesable"
CATEGORIA_PDF = "pdf"
CATEGORIA_SIN_EXTENSION = "sin_extension"
CATEGORIA_NO_SOPORTADO = "no_soportado"


@dataclass
class PdfExpandido:
    """PDFs renderizados a imágenes temporales, con su origen lógico.

    ``pares`` guarda ``(render_temporal, pdf_original)``. El original hace falta
    porque **define el nivel de carpetas del destino**: el render vive en un
    temporal, así que calcular el espejado con la ruta del render daría un destino
    plano y dos PDF homónimos en meses distintos colisionarían.

    ``temporal`` es la raíz a borrar cuando termina la corrida.
    """

    temporal: Path
    pares: list[tuple[Path, Path]] = field(default_factory=list)

    @property
    def renders(self) -> list[Path]:
        return [render for render, _ in self.pares]


@dataclass
class Clasificacion:
    """Qué va a procesar el lote y qué no, con el motivo de cada exclusión."""

    #: Archivos que entran al lote tal cual (imágenes con extensión pedida).
    procesables: list[Path] = field(default_factory=list)
    #: PDFs: entran **si** se pidió expandirlos; si no, se cuentan aparte.
    pdfs: list[Path] = field(default_factory=list)
    #: Archivos que no se pueden procesar, agrupados por categoría → extensión.
    #: ``{"no_soportado": {"docx": 12, "xlsx": 3}}``
    ignorados: dict[str, dict[str, int]] = field(default_factory=dict)
    #: Renders temporales de los PDF, si se pidió incluirlos.
    expansion_pdf: PdfExpandido | None = None

    @property
    def total_ignorados(self) -> int:
        return sum(sum(cuenta.values()) for cuenta in self.ignorados.values())

    def describir_ignorados(self, *, limite: int = 4) -> list[str]:
        """Líneas legibles de lo que quedó afuera, para el reporte."""
        lineas: list[str] = []
        for categoria in sorted(self.ignorados):
            cuenta = self.ignorados[categoria]
            detalle = ", ".join(
                f"{ext or '(sin extensión)'} ×{n}"
                for ext, n in sorted(cuenta.items(), key=lambda kv: -kv[1])[:limite]
            )
            lineas.append(f"  {categoria}: {sum(cuenta.values())} ({detalle})")
        return lineas


def clasificar(
    archivos: Sequence[Path],
    extensiones: frozenset[str],
    *,
    extensiones_pdf: frozenset[str] = frozenset({".pdf"}),
) -> Clasificacion:
    """Separa los archivos en procesables, PDFs e ignorados **con su motivo**.

    ``expandir`` filtra por extensión y avisa solo de las rutas que se le pasan
    sueltas; cuando el recorrido es por carpeta, lo que no matchea **desaparece**
    sin dejar rastro. Esta función existe para que el conteo cierre: si el corpus
    tiene 3.846 archivos y el lote dice 3.582, los 264 restantes tienen que estar
    nombrados en algún lado.
    """
    clasificacion = Clasificacion()
    for archivo in archivos:
        ext = archivo.suffix.lower()
        if ext in extensiones:
            clasificacion.procesables.append(archivo)
        elif ext in extensiones_pdf:
            clasificacion.pdfs.append(archivo)
        elif not ext:
            _sumar(clasificacion, CATEGORIA_SIN_EXTENSION, "")
        else:
            _sumar(clasificacion, CATEGORIA_NO_SOPORTADO, ext.lstrip("."))
    return clasificacion


def _sumar(clasificacion: Clasificacion, categoria: str, clave: str) -> None:
    por_extension = clasificacion.ignorados.setdefault(categoria, {})
    por_extension[clave] = por_extension.get(clave, 0) + 1


def expandir_pdf(
    pdf: Path, destino: Path, *, raiz: Path | None = None, dpi: int = 300
) -> list[Path]:
    """Renderiza un PDF a una imagen por página, en ``destino``.

    Devuelve las rutas creadas, en orden de página. Los archivos quedan en
    ``destino`` (un temporal del lote): el llamador los borra al terminar.

    ⚠️ ``raiz`` es la raíz del corpus y **con ella se espeja la estructura**: el
    render de ``2025-08/2D2C9343/comprobante.pdf`` va a
    ``destino/2025-08/2D2C9343/``, no a la raíz del temporal. Sin esto la salida
    perdía el nivel de carpeta del documento y —peor— dos PDF con el mismo nombre
    en meses distintos habrían colisionado.

    ⚠️ Una página que no se puede renderizar **no aborta** el documento: se saltea
    (la lista sale más corta). Perder un PDF entero por una página rota sería peor
    que procesar el resto.
    """
    from ..processing.orquestacion import render_pdf_a_jpg

    # Se espeja el árbol del corpus desde `raiz` (misma regla que la salida).
    try:
        relativo = pdf.resolve().relative_to(raiz.resolve()).parent if raiz else Path()
    except (ValueError, OSError):
        relativo = Path()
    carpeta = destino / relativo
    carpeta.mkdir(parents=True, exist_ok=True)

    try:
        import pymupdf as fitz

        with fitz.open(str(pdf)) as doc:
            paginas = doc.page_count
    except Exception:  # noqa: BLE001 - un PDF ilegible no debe cortar el lote
        return []

    creadas: list[Path] = []
    for pagina in range(paginas):
        try:
            render = render_pdf_a_jpg(pdf, pagina=pagina, dpi=dpi)
        except Exception:  # noqa: BLE001
            continue
        final = carpeta / f"{pdf.stem}_p{pagina + 1:02d}.jpg"
        shutil.move(str(render), final)
        creadas.append(final)
    return creadas


def limpiar_expansion(expansion: PdfExpandido | None) -> None:
    """Borra el temporal de renders de PDF, si se creó.

    Se llama al terminar la corrida (o la medición). Sin esto, cada corrida con
    ``--incluir-pdf`` dejaría un directorio temporal con cientos de JPG.
    """
    if expansion is None:
        return
    shutil.rmtree(expansion.temporal, ignore_errors=True)


def directorio_temporal(etiqueta: str = "voucherflow_pdf_") -> Path:
    """Directorio temporal para los renders de un lote (lo borra el llamador)."""
    return Path(tempfile.mkdtemp(prefix=etiqueta))

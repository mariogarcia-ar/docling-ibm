"""Qué entra a un lote del laboratorio: imágenes, y PDF renderizados a imagen.

El laboratorio manda **imágenes** al proveedor. Un PDF no es una imagen, así que
hasta ahora quedaba afuera del lote sin decirlo: en el corpus real son **264 de
3.846 archivos** (6,9%), y 190 de ellos comprobantes fiscales.

Este módulo cierra esa brecha con la pieza que ya existe para eso
(:func:`voucherflow.corpus.lectura.expandir_pdf`, la misma que usa
``voucherflow corpus --incluir-pdf``), en vez de reimplementar el render.

Dos cosas que el render aporta y el lote necesita:

* **Una imagen por página.** El corpus tiene 100 PDF multipágina (de 2 y 3), 441
  páginas en total: el nombre del render (``<stem>_pNN.jpg``) es lo que hace que
  dos páginas del mismo documento no escriban el mismo resultado.
* **El documento lógico.** El render vive en un temporal, así que su ruta no dice
  nada del corpus: sin el mapeo ``render → documento``, la salida perdería el
  nivel de carpeta y ``2025-08/<hash>/doc.pdf`` escribiría en la raíz de la salida
  (es el mismo bug que documenta ``corpus/lectura.expandir_pdf``).

⚠️ **La página, no el documento, es la unidad del lote.** Cada página se extrae y
se cobra por separado, y su salida lleva el ``_pNN`` en el nombre. Es lo
observable hoy; si un comprobante multipágina tuviera que leerse entero, hace
falta una decisión de producto (ver el reporte de la entrega).
"""

from __future__ import annotations

import re
import shutil
from dataclasses import dataclass, field
from pathlib import Path

from ..corpus.lectura import Clasificacion, clasificar, expandir_pdf
from ..corpus.recorrido import expandir_todo
from .config import EXTENSIONES_IMAGEN

#: DPI del render. El mismo que el default de ``corpus --dpi-pdf``: si divergieran,
#: el lab y el pipeline leerían el mismo PDF con distinta resolución.
DPI_PDF = 300


@dataclass
class Lote:
    """Lo que el laboratorio va a procesar, con lo que quedó afuera declarado."""

    #: Rutas que se le mandan al modelo: las imágenes del corpus y, si hay PDF,
    #: los renders (que viven en ``temporal``).
    imagenes: list[Path] = field(default_factory=list)
    #: ``render → (PDF, número de página)``. Solo tiene entradas para los PDF: el
    #: render es un insumo temporal y no puede figurar como origen de nada (el
    #: archivo se borra al terminar la corrida).
    paginas: dict[Path, tuple[Path, int]] = field(default_factory=dict)
    #: Raíz del render temporario, a borrar al terminar (``None`` si no hubo PDF).
    temporal: Path | None = None
    #: PDF encontrados y páginas renderizadas (para el reporte de la corrida).
    pdfs: int = 0
    total_paginas: int = 0
    #: Lo que quedó fuera del lote, **con su categoría y extensión**.
    #:
    #: Se guarda la clasificación entera (y no un conteo plano) porque el motivo
    #: importa: «un .docx no soportado» y «un archivo sin extensión» son cosas
    #: distintas, y colapsarlas deja el aviso sin la información que lo hace
    #: accionable. Es el mismo objeto que usa ``corpus``.
    clasificacion: Clasificacion | None = None

    @property
    def imagenes_del_corpus(self) -> int:
        """Cuántos de los archivos del lote ya eran imágenes."""
        return len(self.imagenes) - self.total_paginas

    def documento_de(self, imagen: Path) -> Path:
        """El documento del corpus que representa ``imagen`` (ella misma si no es render)."""
        pagina = self.paginas.get(imagen)
        return pagina[0] if pagina else imagen

    def numero_de_pagina(self, imagen: Path) -> int | None:
        """Página del PDF, o ``None`` si ``imagen`` ya era una imagen del corpus."""
        pagina = self.paginas.get(imagen)
        return pagina[1] if pagina else None

    def clave_de(self, imagen: Path) -> Path:
        """Ruta de la que se deriva el **nombre de salida** de ``imagen``.

        Para las imágenes del corpus es la imagen misma. Para una página es el
        PDF con ``_pNN`` en el nombre: es lo que hace que dos páginas del mismo
        documento no escriban el mismo archivo (el corpus tiene 100 PDF de 2 y 3
        páginas).
        """
        pagina = self.paginas.get(imagen)
        if not pagina:
            return imagen
        return clave_de_pagina(pagina[0], pagina[1])

    def describir_ignorados(self) -> list[str]:
        """Líneas legibles de lo que no entra al lote, para declararlo.

        Delega en la clasificación del corpus: es la que sabe nombrar el motivo
        (extensión no soportada / sin extensión) y arma el detalle.
        """
        if self.clasificacion is None:
            return []
        lineas = self.clasificacion.describir_ignorados()
        if not lineas:
            return []
        return [f"{self.total_ignorados} archivo(s) fuera del lote:", *lineas]

    @property
    def total_ignorados(self) -> int:
        """Cuántos archivos quedaron fuera del lote."""
        return self.clasificacion.total_ignorados if self.clasificacion else 0

    def resumen(self) -> str:
        """Línea con la composición del lote."""
        partes = [f"{self.imagenes_del_corpus} imagen(es)"]
        if self.pdfs:
            partes.append(
                f"{self.pdfs} PDF → {self.total_paginas} página(s) renderizada(s)"
            )
        return " + ".join(partes)


def clave_de_pagina(pdf: Path, pagina: int) -> Path:
    """El PDF con el número de página en el nombre (``x.pdf`` → ``x_p01.pdf``).

    ⚠️ **Fuente única del nombre de salida de una página.** Se usa para calcular
    el destino, para la reanudación y para el guardado, así que tener dos copias
    haría que la reanudación buscara en un lugar distinto del que escribe (y el
    documento se pagaría dos veces: es el bug que documenta ``recorrido``).

    Conserva la extensión ``.pdf`` a propósito: la ruta no es un archivo real (la
    página vive en el render temporal), pero sí identifica sin ambigüedad al
    documento **y** su página, y es la que va a ``archivo_relativo``.
    """
    return pdf.with_name(f"{pdf.stem}_p{pagina:02d}{pdf.suffix}")


def armar(rutas: list[Path], raiz: Path, *, dpi: int = DPI_PDF) -> Lote:
    """Arma el lote de ``rutas``: imágenes tal cual + una imagen por página de PDF.

    ⚠️ **Los PDF no entran si no se pueden renderizar.** Un PDF ilegible devuelve
    lista vacía (no aborta el lote), igual que en ``corpus``: perder el resto por
    un archivo roto sería peor.

    El render va a un temporal que **espeja el árbol del corpus**; el llamador es
    responsable de borrarlo con :func:`limpiar` en un ``finally``.
    """
    encontrados = expandir_todo(rutas)
    clasificacion = clasificar(encontrados, EXTENSIONES_IMAGEN)

    lote = Lote(imagenes=list(clasificacion.procesables))
    lote.pdfs = len(clasificacion.pdfs)
    lote.clasificacion = clasificacion

    if not clasificacion.pdfs:
        return lote

    temporal = Path(_temporal())
    for pdf in clasificacion.pdfs:
        # ``expandir_pdf`` espeja desde ``raiz`` y nombra ``<stem>_pNN.jpg``: el
        # número de página sale del nombre del render (única fuente del dato).
        for render in expandir_pdf(pdf, temporal, raiz=raiz, dpi=dpi):
            pagina = _pagina_del_render(render)
            if pagina is None:  # pragma: no cover - el nombre lo fija expandir_pdf
                continue
            lote.imagenes.append(render)
            lote.paginas[render] = (pdf, pagina)

    lote.imagenes.sort()
    lote.total_paginas = len(lote.paginas)
    lote.temporal = temporal if lote.total_paginas else None
    if not lote.total_paginas:
        shutil.rmtree(temporal, ignore_errors=True)
    return lote


def _pagina_del_render(render: Path) -> int | None:
    """Número de página del render (``<stem>_pNN.jpg``), o ``None`` si no matchea."""
    coincidencia = re.search(r"_p(\d+)$", render.stem)
    return int(coincidencia.group(1)) if coincidencia else None


def limpiar(lote: Lote) -> None:
    """Borra el temporal de renders. Sin esto quedan cientos de JPG en /tmp."""
    if lote.temporal is not None:
        shutil.rmtree(lote.temporal, ignore_errors=True)
        lote.temporal = None


def _temporal() -> str:
    import tempfile

    return tempfile.mkdtemp(prefix="lab_pdf_")


__all__ = ["DPI_PDF", "Lote", "armar", "clave_de_pagina", "limpiar"]

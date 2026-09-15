"""Conversión de PDF a imágenes: el paquete detrás de ``voucherflow pdf``.

⚠️ **Por qué existe.** El pipeline renderiza un PDF cuando necesita **leerlo**
como imagen (un escaneado, la página OCR de un PDF mixto), y para eso usa
:func:`voucherflow.processing.orquestacion.render_pdf_a_jpg`. Lo que faltaba era
la capacidad de **materializar** esas imágenes: ver un PDF antes de procesarlo,
armar un corpus de imágenes, o alimentar al laboratorio de LLM externos (que
manda imágenes, no PDF).

Eso vivía en un script suelto (``scripts/operacion/pdf-a-imagen.py``) que usaba
``pdf2image``/poppler. Se mudó acá por tres motivos medidos:

1. **Un script con guion en el nombre no se puede importar ni testear.** En
   ``src`` se testea in-process, como ``corpus``.
2. **``pdf2image`` era una dependencia de facto sin declarar** (nadie la requería;
   ``pip show`` la marcaba como instalada a mano) y traía poppler, un binario de
   sistema, con un ``fork`` por página. **PyMuPDF ya es dependencia declarada**
   (``pymupdf>=1.24``) y hace el mismo trabajo en proceso: mismo tamaño exacto de
   salida, ~8x más rápido por página (medido: 0,017 s vs 0,139 s).
3. **Era una segunda implementación de una regla que ya existía.** El render, el
   recorte y el espejado del árbol ya están en el paquete; el script los tenía
   por su cuenta y podía divergir (de hecho el recorte del script era el
   incorrecto para los PDF nacidos digitales, que son el 89% del corpus).

Uso::

    voucherflow pdf comprobante.pdf
    voucherflow pdf var/files -o var/paginas --dpi 300
"""

from __future__ import annotations

from .modelo import Opciones, Resultado
from .plan import ErrorPdf, planificar

__all__ = ["ErrorPdf", "Opciones", "Resultado", "planificar"]

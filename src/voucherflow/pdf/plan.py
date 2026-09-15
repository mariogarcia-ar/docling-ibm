"""Planificación: qué PDF hay, cuántas páginas y dónde va cada render.

Todo el cálculo del destino vive acá y **la corrida real y la simulación usan
esta misma función**: si el plan y la escritura pudieran calcular rutas distintas,
un ``--solo-medir`` mentiría sobre qué archivos crea (y la reanudación buscaría en
otro lado que el que escribe — el bug que ya costó pagar dos veces un lote en el
laboratorio).

Los dos invariantes que hereda de ``corpus``:

* **Nada desaparece en silencio**: lo que no es PDF se cuenta por extensión y se
  declara (un corpus de 3.846 archivos perdía 264 PDF sin que nada lo dijera).
* **El destino se espeja desde una raíz estable**, no desde la ruta que se pasó:
  convertir ``var/files`` o ``var/files/2025-08`` tiene que escribir **los mismos**
  archivos (la regla vive en ``corpus.recorrido.raiz_espejado``).
"""

from __future__ import annotations

from pathlib import Path

from ..corpus.recorrido import expandir_todo, raiz_espejado, salida_de
from .modelo import PATRON_UN_PDF, PATRON_VARIOS, TOKENS, Opciones, Plan, Tarea

#: Extensión que este comando convierte (en minúsculas: un ``.PDF`` de un escaneo
#: viejo no se puede perder en el filtro).
EXTENSION_PDF = ".pdf"


class ErrorPdf(Exception):
    """Error de uso o de entorno: corta la corrida antes de escribir nada."""


def planificar(
    rutas: list[Path],
    opciones: Opciones,
    *,
    raiz: Path | None = None,
    limite: int = 0,
) -> Plan:
    """Arma el plan de conversión de ``rutas``, sin renderizar todavía.

    Argumentos:
        rutas: archivos y/o carpetas (las carpetas se recorren recursivo).
        opciones: opciones efectivas (patrón, rango de páginas, salida).
        raiz: raíz del espejado (``None`` la deriva, ver ``raiz_espejado``).
        limite: máximo de páginas a planificar (``0`` = sin límite).

    Devuelve:
        El :class:`~voucherflow.pdf.modelo.Plan`, con los PDF ilegibles y los
        archivos que no son PDF **declarados** en vez de omitidos.
    """
    encontrados = expandir_todo(rutas)
    if not encontrados:
        raise ErrorPdf(f"no existe la ruta «{rutas[0]}»")

    raiz_efectiva, motivo = raiz_espejado(rutas, raiz, opciones.salida)
    plan = Plan(raiz=raiz_efectiva, motivo_raiz=motivo)

    pdfs: list[Path] = []
    for archivo in encontrados:
        if archivo.suffix.lower() == EXTENSION_PDF:
            pdfs.append(archivo)
        else:
            clave = archivo.suffix.lower() or "(sin extensión)"
            plan.ignorados[clave] = plan.ignorados.get(clave, 0) + 1

    if not pdfs:
        raise ErrorPdf("no hay ningún PDF en las rutas dadas")

    # Con un solo PDF se usa el patrón del script original (`pagina_1.jpg`); con
    # varios, el nombre del documento pasa a ser obligatorio (si no, dos
    # homónimos de meses distintos se pisarían).
    patron = opciones.patron or (PATRON_VARIOS if len(pdfs) > 1 else PATRON_UN_PDF)

    for pdf in pdfs:
        total = _total_paginas(pdf, plan)
        if total is None:
            continue
        desde = opciones.primera or 1
        hasta = min(opciones.ultima or total, total)
        if desde > hasta:
            plan.problemas.append(
                f"{pdf.name}: el rango pedido sale vacío (se pidió {desde}-"
                f"{opciones.ultima or total}, el PDF tiene {total} página(s))"
            )
            continue
        for pagina in range(desde, hasta + 1):
            plan.tareas.append(
                Tarea(
                    pdf=pdf,
                    pagina=pagina,
                    destino=_destino_de(
                        salida_de(pdf, raiz_efectiva, opciones.salida, ""),
                        pdf,
                        pagina,
                        total,
                        patron,
                    ),
                )
            )
            if limite and plan.paginas >= limite:
                return plan
    return plan


def _total_paginas(pdf: Path, plan: Plan) -> int | None:
    """Cuenta las páginas de un PDF, declarando el problema si no se puede.

    Un PDF ilegible **no aborta el lote**: se anota y se sigue (misma regla que
    ``corpus/lectura.expandir_pdf``, que saltea la página que falla en vez de
    perder el documento entero).
    """
    try:
        import pymupdf as fitz

        with fitz.open(str(pdf)) as doc:
            return doc.page_count
    except Exception as exc:  # noqa: BLE001 - un PDF roto no corta el lote
        plan.problemas.append(f"{pdf.name}: no se pudo leer ({_motivo(exc)})")
        return None


def _destino_de(
    base: Path, pdf: Path, pagina: int, total: int, plantilla: str
) -> Path:
    """Destino de una página: cambia el **nombre** del archivo planificado.

    ``salida_de`` decide la carpeta (espejando el árbol); acá solo se reemplaza el
    nombre por el que pide ``--patron``. Separarlo así evita que este comando
    reimplemente la regla de espejado.
    """
    try:
        nombre = plantilla.format(nombre=pdf.stem, pagina=pagina, total=total)
    except (KeyError, IndexError, ValueError) as exc:
        raise ErrorPdf(
            f"--patron «{plantilla}» no es válido: {_motivo(exc)}. "
            f"Tokens disponibles: {', '.join(TOKENS)}"
        ) from exc

    if Path(nombre).name != nombre:
        raise ErrorPdf(
            f"--patron «{plantilla}» no puede contener separadores de carpeta."
        )
    if not Path(nombre).suffix:
        nombre += ".jpg"
    return base.with_name(nombre)


def _motivo(exc: BaseException, limite: int = 200) -> str:
    """Resume una excepción en UNA línea (el stderr de poppler/PyMuPDF es largo).

    Se toma la primera línea con texto: en un lote de cientos de archivos, volcar
    el mensaje entero tapa el reporte.
    """
    for linea in str(exc).splitlines():
        limpia = " ".join(linea.split())
        if limpia:
            return limpia[:limite]
    return exc.__class__.__name__


def describir_ignorados(plan: Plan, *, limite: int = 4) -> list[str]:
    """Líneas legibles de lo que quedó afuera, para declararlo."""
    if not plan.ignorados:
        return []
    detalle = ", ".join(
        f"{ext} ×{n}"
        for ext, n in sorted(plan.ignorados.items(), key=lambda kv: -kv[1])[:limite]
    )
    return [f"{sum(plan.ignorados.values())} archivo(s) fuera (no son PDF: {detalle})"]


__all__ = [
    "EXTENSION_PDF",
    "ErrorPdf",
    "describir_ignorados",
    "planificar",
]

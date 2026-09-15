#!/usr/bin/env python
"""Convierte PDF a imágenes JPG (una por página) usando ``pdf2image``.

Herramienta **operativa**: no es parte del paquete ``voucherflow`` ni lo
reemplaza. El pipeline renderiza los PDF con
``voucherflow.processing.orquestacion.render_pdf_a_jpg`` (PyMuPDF, recortando
el área de la imagen) cuando se corre con ``--incluir-pdf``; esto de acá sirve
para **mirar** el PDF antes o después de procesarlo, o para armar un corpus de
imágenes a mano.

``pdf2image`` **no** es una dependencia declarada del paquete (usa poppler, que
es un binario de sistema, no una librería de Python). Se importa tarde y, si
falta, el script dice cómo instalarlo en vez de morir con un ``ImportError``:

    python -m pip install pdf2image
    # macOS: brew install poppler   |   Windows: --poppler-path C:\\poppler\\bin

Uso:
    python scripts/operacion/pdf-a-imagen.py comprobante.pdf
    python scripts/operacion/pdf-a-imagen.py comprobante.pdf -o /tmp/paginas --dpi 300
    python scripts/operacion/pdf-a-imagen.py var/files -o var/paginas
    python scripts/operacion/pdf-a-imagen.py var/files --dry-run
    python scripts/operacion/pdf-a-imagen.py lote.pdf --primera 1 --ultima 2 --calidad 90

Salida: por defecto ``var/paginas/`` (``-o`` lo cambia). Con
**varios** PDF en la misma corrida el nombre lleva el del documento y se espeja
el árbol de carpetas del origen (``2025-08/<hash>/x_pagina_1.jpg``): sin eso, dos
PDF homónimos en meses distintos se pisarían — es el mismo bug que documenta
``voucherflow/corpus/lectura.py::expandir_pdf``.

Sale con código 0 si no hubo errores, y **declara** lo que quedó afuera
(archivos que no son PDF) y las páginas que fallaron: un barrido que se come
archivos en silencio es el patrón que ya costó caro en ``corpus/lectura.py``.
"""

from __future__ import annotations

import argparse
import os
import shutil
from dataclasses import dataclass, field
from pathlib import Path

#: Extensión que este script considera "un PDF". Se compara en minúsculas para
#: que un ``.PDF`` de un escaneo viejo no se pierda en el filtro.
EXTENSION_PDF = ".pdf"

#: Patrón por defecto al convertir **un solo** PDF: es el del snippet original
#: (``pagina_1.jpg``), para no cambiar lo que ya se venía escribiendo a mano.
PATRON_UN_PDF = "pagina_{pagina}.jpg"

#: Con **varios** PDF el nombre lleva el del documento: si no, ``pagina_1.jpg``
#: del mes A pisaría el del mes B.
PATRON_VARIOS = "{nombre}_pagina_{pagina}.jpg"

#: Tokens disponibles en ``--patron``.
TOKENS = ("nombre", "pagina", "total")

#: DPI default del snippet original. El pipeline de la librería renderiza a 300
#: (``render_pdf_a_jpg``); para mirar a ojo, 200 pesa la mitad.
DPI_DEFAULT = 200

#: Calidad JPEG default: la que Pillow usa como «alta» razonable (~85).
CALIDAD_DEFAULT = 85

#: Carpeta de salida por defecto, **relativa al cwd**. Va bajo ``var/`` porque es
#: donde este repo mantiene TODOS los datos (decisión del 2026-09-13, ver
#: ``docs``/``test_settings_paths.py``): así la salida queda gitignoreada y no
#: ensucia `git status`. ⚠️ El literal no sale de ``settings.paths``: sus nombres
#: válidos son los del corpus (``files``/``processed``/``validations``) y sumar
#: uno nuevo sería un cambio de librería, fuera del alcance de un script.
SALIDA_DEFAULT = Path("var") / "paginas"

#: Largo máximo de un motivo de error en una línea (los mensajes de poppler
#: suelen traer el stderr completo, con saltos de línea).
LARGO_MOTIVO = 200


class ErrorDeEntorno(Exception):
    """Problema de entorno o de argumentos: corta la corrida antes de escribir."""


@dataclass
class Resumen:
    """Estado acumulado de la corrida, para el reporte final."""

    pdfs: int = 0
    escritas: int = 0
    salteadas: int = 0
    sin_paginas: int = 0
    ignorados: dict[str, int] = field(default_factory=dict)
    errores: list[str] = field(default_factory=list)

    def lineas_ignorados(self) -> list[str]:
        """Archivos descartados por extensión, de mayor a menor."""
        return [
            f"    {ext:12} {cantidad}"
            for ext, cantidad in sorted(
                self.ignorados.items(), key=lambda par: (-par[1], par[0])
            )
        ]


# --------------------------------------------------------------------------- #
# Entorno (poppler / pdf2image): los dos fallos que se arreglan fuera de Python
# --------------------------------------------------------------------------- #

def _motivo(exc: BaseException) -> str:
    """Resume una excepción en UNA línea, sin volcar el stderr de poppler.

    Los errores de poppler citan el PDF y su contenido interno; en un lote de
    cientos de archivos eso tapa el reporte. Se toma la primera línea con texto.
    """
    for linea in str(exc).splitlines():
        limpia = " ".join(linea.split())
        if limpia:
            return limpia[:LARGO_MOTIVO]
    return exc.__class__.__name__


def _importar_pdf2image():
    """Importa ``pdf2image`` tarde y explica cómo instalarlo si falta.

    Dependencia **operativa**, no del paquete: por eso no se declara en
    ``pyproject.toml`` y el mensaje de error es parte del contrato del script.
    """
    try:
        import pdf2image
    except ImportError as exc:  # pragma: no cover - depende del entorno
        raise ErrorDeEntorno(
            "falta pdf2image (dependencia del script, no del paquete): "
            "`python -m pip install pdf2image`"
        ) from exc
    return pdf2image


def _verificar_poppler(poppler_path: str | None) -> None:
    """Falla ANTES de recorrer nada si ``pdfinfo``/``pdftoppm`` no están.

    Sin esto el síntoma es un error por PDF («Unable to get page count») que
    parece un problema del archivo y en realidad es del entorno.

    ⚠️ ``path=None`` (y **no** ``path=""``): ``shutil.which`` con la cadena
    vacía busca solo en el directorio vacío y no encuentra nada, así que pasar
    ``poppler_path or ""`` haría fallar la verificación aunque poppler esté en
    el PATH (bug real de la primera versión de este script).
    """
    for binario in ("pdfinfo", "pdftoppm"):
        if shutil.which(binario, path=poppler_path or None):
            continue
        pista = (
            f"revisá --poppler-path «{poppler_path}»"
            if poppler_path
            else "en macOS: `brew install poppler`; en Windows: pasá "
            "`--poppler-path C:\\ruta\\a\\poppler\\bin`"
        )
        raise ErrorDeEntorno(f"no encuentro «{binario}» (poppler): {pista}")


# --------------------------------------------------------------------------- #
# Entrada: qué PDF hay y qué queda afuera
# --------------------------------------------------------------------------- #

def _enumerar(entrada: Path) -> tuple[list[tuple[Path, Path]], dict[str, int]]:
    """Lista los PDF de ``entrada`` como ``(ruta, carpeta relativa)``.

    La carpeta relativa es la que se espeja bajo la salida. Devuelve además el
    recuento de lo descartado por extensión: ``rglob('*.pdf')`` lo escondería sin
    dejar rastro (misma regla que ``corpus/lectura.clasificar``).
    """
    if entrada.is_file():
        if entrada.suffix.lower() != EXTENSION_PDF:
            ext = entrada.suffix or "sin extensión"
            raise ErrorDeEntorno(f"«{entrada}» no es un PDF ({ext}).")
        return [(entrada, Path())], {}

    if not entrada.is_dir():
        raise ErrorDeEntorno(f"no existe la ruta «{entrada}».")

    pdfs: list[tuple[Path, Path]] = []
    ignorados: dict[str, int] = {}
    for ruta in sorted(entrada.rglob("*")):
        if not ruta.is_file():
            continue
        if ruta.suffix.lower() == EXTENSION_PDF:
            pdfs.append((ruta, ruta.parent.relative_to(entrada)))
        else:
            clave = ruta.suffix.lower() or "(sin extensión)"
            ignorados[clave] = ignorados.get(clave, 0) + 1
    return pdfs, ignorados


def _total_paginas(pdf: Path, poppler_path: str | None) -> int:
    """Cuenta las páginas con ``pdfinfo`` (sin renderizar nada)."""
    from pdf2image import pdfinfo_from_path

    info = pdfinfo_from_path(str(pdf), poppler_path=poppler_path)
    return int(info["Pages"])


def _destino(
    salida: Path, relativo: Path, pdf: Path, numero: int, total: int, plantilla: str
) -> Path:
    """Calcula el destino de UNA página. Única fuente del nombre.

    Que el plan (``--dry-run``) y la escritura real llamen a esta misma función
    es deliberado: si divergieran, el dry-run mentiría sobre qué archivos crea.
    """
    try:
        nombre = plantilla.format(nombre=pdf.stem, pagina=numero, total=total)
    except (KeyError, IndexError, ValueError) as exc:
        raise ErrorDeEntorno(
            f"--patron «{plantilla}» no es válido: {_motivo(exc)}. "
            f"Tokens disponibles: {', '.join(TOKENS)}"
        ) from exc

    if Path(nombre).name != nombre:
        raise ErrorDeEntorno(
            f"--patron «{plantilla}» no puede contener separadores de carpeta."
        )
    if not Path(nombre).suffix:
        nombre += ".jpg"
    return salida / relativo / nombre


# --------------------------------------------------------------------------- #
# Conversión
# --------------------------------------------------------------------------- #

def _guardar_pagina(
    pdf: Path,
    numero: int,
    destino: Path,
    *,
    dpi: int,
    calidad: int,
    grises: bool,
    poppler_path: str | None,
) -> None:
    """Renderiza UNA página a JPEG, de forma atómica.

    Se pide una página por llamada a ``convert_from_path`` a propósito: con el
    PDF entero, pdf2image devuelve **todas** las páginas en memoria a la vez
    (~12 MB por A4 a 200 dpi en RGB; un PDF de 200 páginas son ~2,4 GB).

    La escritura pasa por un temporal en el mismo directorio y un ``os.replace``
    (mismo patrón que ``voucherflow/persistencia.py``): un ``save`` interrumpido
    no deja un JPEG truncado con el nombre final —que la reanudación daría por
    bueno—, y el temporal se limpia si algo falla.
    """
    from pdf2image import convert_from_path

    imagenes = convert_from_path(
        str(pdf),
        dpi=dpi,
        fmt="ppm",  # intermedio sin pérdida: el JPEG se codifica una sola vez
        first_page=numero,
        last_page=numero,
        grayscale=grises,
        poppler_path=poppler_path,
    )
    if not imagenes:
        raise RuntimeError("pdf2image no devolvió ninguna imagen para la página")

    imagen = imagenes[0]
    temporal = destino.with_name(destino.name + ".tmp")
    try:
        destino.parent.mkdir(parents=True, exist_ok=True)
        imagen.save(temporal, "JPEG", quality=calidad, optimize=True)
        os.replace(temporal, destino)
    finally:
        imagen.close()
        temporal.unlink(missing_ok=True)


def convertir(
    pdfs: list[tuple[Path, Path]],
    salida: Path,
    *,
    dpi: int = DPI_DEFAULT,
    calidad: int = CALIDAD_DEFAULT,
    grises: bool = False,
    patron: str | None = None,
    primera: int | None = None,
    ultima: int | None = None,
    forzar: bool = False,
    dry_run: bool = False,
    poppler_path: str | None = None,
    resumen: Resumen | None = None,
) -> Resumen:
    """Convierte los PDF de ``pdfs`` a JPEG. Devuelve el resumen de la corrida."""
    resumen = resumen if resumen is not None else Resumen()
    resumen.pdfs = len(pdfs)
    varios = len(pdfs) > 1
    plantilla = patron or (PATRON_VARIOS if varios else PATRON_UN_PDF)

    for pdf, relativo in pdfs:
        try:
            total = _total_paginas(pdf, poppler_path)
        except Exception as exc:  # noqa: BLE001 - un PDF roto no corta el lote
            resumen.errores.append(f"{pdf.name}: no se pudo leer ({_motivo(exc)})")
            continue

        desde = primera or 1
        hasta = min(ultima or total, total)
        if desde > hasta:
            resumen.sin_paginas += 1
            resumen.errores.append(
                f"{pdf.name}: el rango pedido sale vacío (se pidió {desde}-"
                f"{ultima or total}, el PDF tiene {total} página(s))"
            )
            continue

        for numero in range(desde, hasta + 1):
            destino = _destino(salida, relativo, pdf, numero, total, plantilla)
            if not forzar and destino.exists() and destino.stat().st_size > 0:
                resumen.salteadas += 1
                continue
            if dry_run:
                print(f"  Guardaría: {destino}")
                resumen.escritas += 1
                continue
            try:
                _guardar_pagina(
                    pdf,
                    numero,
                    destino,
                    dpi=dpi,
                    calidad=calidad,
                    grises=grises,
                    poppler_path=poppler_path,
                )
            except Exception as exc:  # noqa: BLE001 - idem: se saltea y se declara
                resumen.errores.append(
                    f"{pdf.name} p.{numero}: no se pudo renderizar ({_motivo(exc)})"
                )
                continue
            print(f"  Guardada: {destino}")
            resumen.escritas += 1

    return resumen


# --------------------------------------------------------------------------- #
# CLI
# --------------------------------------------------------------------------- #

def _entero_positivo(texto: str) -> int:
    valor = int(texto)
    if valor < 1:
        raise argparse.ArgumentTypeError("tiene que ser 1 o más")
    return valor


def _calidad_valida(texto: str) -> int:
    valor = int(texto)
    if not 1 <= valor <= 100:
        raise argparse.ArgumentTypeError("tiene que estar entre 1 y 100")
    return valor


def _construir_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="pdf-a-imagen.py",
        description=__doc__.splitlines()[0],
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument(
        "entrada",
        type=Path,
        help="un archivo .pdf o una carpeta (se recorre recursivamente)",
    )
    parser.add_argument(
        "-o", "--salida", type=Path, default=SALIDA_DEFAULT,
        help=f"carpeta de salida (default: ./{SALIDA_DEFAULT})",
    )
    parser.add_argument(
        "--dpi", type=_entero_positivo, default=DPI_DEFAULT,
        help=f"resolución del render (default: {DPI_DEFAULT})",
    )
    parser.add_argument(
        "--calidad", type=_calidad_valida, default=CALIDAD_DEFAULT,
        help=f"calidad JPEG 1-100 (default: {CALIDAD_DEFAULT})",
    )
    parser.add_argument(
        "--grises", action="store_true",
        help="renderizar en escala de grises (comprobantes térmicos escaneados)",
    )
    parser.add_argument(
        "--primera", type=_entero_positivo, default=None,
        help="primera página a convertir, 1-based (default: la 1)",
    )
    parser.add_argument(
        "--ultima", type=_entero_positivo, default=None,
        help="última página a convertir, inclusive (default: la última del PDF)",
    )
    parser.add_argument(
        "--patron", default=None,
        help="nombre de cada imagen; tokens: "
        + ", ".join(f"{{{t}}}" for t in TOKENS)
        + f" (default: {PATRON_UN_PDF} para un PDF, {PATRON_VARIOS} para varios)",
    )
    parser.add_argument(
        "--forzar", action="store_true",
        help="re-renderizar aunque el archivo ya exista (default: reanudar)",
    )
    parser.add_argument(
        "--dry-run", action="store_true",
        help="mostrar qué escribiría y con qué nombres, sin crear nada",
    )
    parser.add_argument(
        "--poppler-path", default=None,
        help="carpeta de los binarios de poppler (Windows / instalación suelta)",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _construir_parser().parse_args(argv)

    try:
        _importar_pdf2image()
        _verificar_poppler(args.poppler_path)
        pdfs, ignorados = _enumerar(args.entrada)
        if not pdfs:
            print(f"⚠ no hay ningún PDF en «{args.entrada}»")
            return 1

        salida = args.salida
        print(
            f"{len(pdfs)} PDF · {args.dpi} dpi · calidad {args.calidad}"
            f"{' · grises' if args.grises else ''} → {salida}"
            f"{'  [dry-run: no se escribe nada]' if args.dry_run else ''}"
        )
        resumen = convertir(
            pdfs,
            salida,
            dpi=args.dpi,
            calidad=args.calidad,
            grises=args.grises,
            patron=args.patron,
            primera=args.primera,
            ultima=args.ultima,
            forzar=args.forzar,
            dry_run=args.dry_run,
            poppler_path=args.poppler_path,
            resumen=Resumen(ignorados=ignorados),
        )
    except ErrorDeEntorno as exc:
        print(f"⚠ {exc}")
        return 1

    verbo = "se escribirían" if args.dry_run else "escritas"
    print(
        f"resumen: {resumen.pdfs} PDF · {resumen.escritas} imágenes {verbo} · "
        f"{resumen.salteadas} ya existían · {len(resumen.errores)} errores"
    )
    if resumen.ignorados:
        print(f"⚠ quedan afuera (no son PDF) · {sum(resumen.ignorados.values())}:")
        print("\n".join(resumen.lineas_ignorados()))
    if resumen.errores:
        print(f"⚠ {len(resumen.errores)} problema(s):")
        for error in resumen.errores:
            print(f"    {error}")
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

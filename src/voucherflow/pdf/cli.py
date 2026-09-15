"""CLI de la conversión de PDF a imágenes (subcomando ``voucherflow pdf``).

Igual que el resto del repo: ``argparse`` de la stdlib, el dato a ``stdout``, el
progreso y los errores a ``stderr``, y ``main()`` **devuelve** el código de salida
en vez de llamar a ``sys.exit`` (así se testea in-process).
"""

from __future__ import annotations

import argparse
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import TextIO

from ..settings.config import cargar_settings
from .corrida import CADA_CUANTOS_PROGRESO, contar_fallos, ejecutar
from .modelo import (
    CALIDAD_DEFAULT,
    DPI_DEFAULT,
    ESTADO_FALLO,
    PATRON_UN_PDF,
    PATRON_VARIOS,
    TOKENS,
    Opciones,
    Plan,
    Resultado,
)
from .plan import ErrorPdf, describir_ignorados, planificar

#: Códigos de salida (los mismos del resto del CLI).
EXIT_OK = 0
EXIT_FALLOS = 1
EXIT_USO = 2
EXIT_INTERRUMPIDO = 130


@dataclass
class EntornoPdf:
    """Colaboraciones del subcomando, inyectables para la suite."""

    stdout: TextIO = sys.stdout
    stderr: TextIO = sys.stderr

    def log(self, mensaje: str) -> None:
        """Progreso/errores a ``stderr`` (``stdout`` es para el dato)."""
        print(mensaje, file=self.stderr)

    def dato(self, texto: str) -> None:
        """El dato de la corrida a ``stdout``."""
        print(texto, file=self.stdout)


def carpeta_por_defecto() -> str:
    """Carpeta de salida por defecto, bajo ``var/`` (la de datos del proyecto).

    ⚠️ **No** sale de ``settings.paths``: sus nombres válidos son los del corpus
    (``files``/``processed``/``validations``) y sumar uno nuevo sería un cambio de
    configuración (y de su contrato) por un default de comando. Se usa ``var``
    —que sí es la raíz de datos— con un subdirectorio propio.
    """
    try:
        return str(cargar_settings().paths.resolver("var") / "paginas")
    except Exception:  # noqa: BLE001 - sin configuración legible
        return "var/paginas"


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


def agregar_parser(sub: argparse._SubParsersAction) -> argparse.ArgumentParser:
    """Registra el subcomando ``pdf`` en el parser principal."""
    p = sub.add_parser(
        "pdf",
        help="Convierte PDF a imágenes JPG (una por página).",
        description=(
            "Renderiza cada página de un PDF a un JPG, con PyMuPDF. Sirve para "
            "mirar un PDF antes o después de procesarlo, y para armar un corpus "
            "de imágenes (el laboratorio de LLM externos manda imágenes, no PDF)."
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=(
            "El destino espeja el árbol desde la raíz de la entrada, sin repetir\n"
            "el nombre de la carpeta de entrada:\n"
            "  var/files/2025-08/2D2C9343/x.pdf  →  salida/2025-08/2D2C9343/...\n\n"
            "Con VARIOS PDF el nombre lleva el del documento "
            f"({PATRON_VARIOS});\n"
            f"con uno solo es el del script original ({PATRON_UN_PDF}).\n"
        ),
    )
    p.add_argument(
        "rutas",
        nargs="+",
        help="Archivos .pdf y/o carpetas a convertir (las carpetas, recursivo).",
    )
    p.add_argument(
        "-o",
        "--salida",
        default=None,
        help=(
            "Carpeta de salida (default: la de la configuración, var/paginas). "
            "El árbol se espeja desde --raiz (o desde el nivel que no sea un mes)."
        ),
    )
    p.add_argument(
        "--raiz",
        type=Path,
        help=(
            "Raíz desde la cual se espeja el árbol en la salida. Si se omite, se "
            "sube hasta el nivel que no sea un mes, así la MISMA entrada escribe "
            "siempre los MISMOS archivos."
        ),
    )
    p.add_argument(
        "--dpi",
        type=_entero_positivo,
        default=DPI_DEFAULT,
        metavar="DPI",
        help=(
            "Resolución del render (default: %(default)s, la que usa el pipeline "
            "para OCR)."
        ),
    )
    p.add_argument(
        "--calidad",
        type=_calidad_valida,
        default=CALIDAD_DEFAULT,
        metavar="1-100",
        help="Calidad JPEG (default: %(default)s).",
    )
    p.add_argument(
        "--recortar",
        dest="recortar",
        action="store_const",
        const=True,
        default=None,
        help=(
            "Recortar a la imagen más grande de la página, SIEMPRE. El default "
            "decide por página: recorta en un escaneado (la imagen ES el "
            "documento) y renderiza la página completa en un PDF con texto "
            "nativo (donde la imagen mayor suele ser el logo del emisor)."
        ),
    )
    p.add_argument(
        "--sin-recortar",
        dest="recortar",
        action="store_const",
        const=False,
        help="Renderizar la página completa, siempre (nunca recortar).",
    )
    p.add_argument(
        "--primera",
        type=_entero_positivo,
        metavar="N",
        help="Primera página a convertir, 1-based (default: la 1).",
    )
    p.add_argument(
        "--ultima",
        type=_entero_positivo,
        metavar="N",
        help="Última página a convertir, inclusive (default: la última del PDF).",
    )
    p.add_argument(
        "--patron",
        metavar="PLANTILLA",
        help=(
            "Nombre de cada imagen; tokens: "
            + ", ".join(f"{{{t}}}" for t in TOKENS)
            + f" (default: {PATRON_UN_PDF} para un PDF, {PATRON_VARIOS} para varios)."
        ),
    )
    p.add_argument(
        "--forzar",
        action="store_true",
        help="Re-renderizar aunque el archivo ya exista (sin esto, reanuda).",
    )
    p.add_argument(
        "--solo-medir",
        dest="escribir",
        action="store_false",
        help="Mostrar qué se escribiría y con qué nombres, sin crear nada.",
    )
    p.add_argument(
        "--limite",
        type=_entero_positivo,
        default=0,
        metavar="N",
        help="Máximo de páginas a convertir (default: todas).",
    )
    return p


def _opciones_desde(args: argparse.Namespace) -> Opciones:
    """Traduce los argumentos a las opciones efectivas."""
    salida = Path(args.salida) if args.salida else Path(carpeta_por_defecto())
    return Opciones(
        salida=salida,
        dpi=args.dpi,
        calidad=args.calidad,
        patron=args.patron,
        primera=args.primera,
        ultima=args.ultima,
        forzar=args.forzar,
        escribir=args.escribir,
        recortar=args.recortar,
    )


def main(
    args: argparse.Namespace,
    *,
    entorno: EntornoPdf | None = None,
) -> int:
    """Ejecuta el subcomando. Devuelve el código de salida (no llama a ``sys.exit``)."""
    entorno = entorno or EntornoPdf()

    try:
        opciones = _opciones_desde(args)
        rutas = [Path(r) for r in args.rutas]
        plan = planificar(rutas, opciones, raiz=args.raiz, limite=args.limite)
    except ErrorPdf as exc:
        entorno.log(f"error: {exc}")
        return EXIT_USO
    except ValueError as exc:
        entorno.log(f"error: {exc}")
        return EXIT_USO

    # ⚠️ Un plan sin páginas es un error de **uso**, no una corrida exitosa: la
    # entrada tenía PDF, así que lo que falló fue la combinación de argumentos
    # (el caso típico: `--primera` más allá de la última página). El motivo ya
    # quedó declarado en `plan.problemas`.
    if plan.paginas == 0:
        for problema in plan.problemas:
            entorno.log(f"error: {problema}")
        entorno.log("error: no hay ninguna página que convertir")
        return EXIT_USO

    _informar_plan(plan, opciones, entorno)

    def on_resultado(resultado: Resultado, i: int) -> None:
        if resultado.estado == ESTADO_FALLO:
            entorno.log(f"  ✗ {resultado.origen.name} p.{resultado.pagina}: {resultado.motivo}")
        elif _detalle:
            entorno.log(f"  {resultado.estado}: {resultado.destino}")
        elif i % CADA_CUANTOS_PROGRESO == 0:
            entorno.log(f"  … {i}/{plan.paginas}")

    _detalle = not opciones.solo_medir and plan.paginas <= 200

    try:
        resultados = ejecutar(plan.tareas, opciones, on_resultado=on_resultado)
    except KeyboardInterrupt:
        entorno.log("\nInterrumpido por el usuario.")
        return EXIT_INTERRUMPIDO

    _imprimir_resumen(plan, opciones, resultados, entorno)
    return EXIT_FALLOS if contar_fallos(resultados) else EXIT_OK


def _informar_plan(plan: Plan, opciones: Opciones, entorno: EntornoPdf) -> None:
    """Declara qué se va a hacer **antes** de hacerlo."""
    if plan.problemas:
        entorno.log(f"⚠  {len(plan.problemas)} PDF no se pudieron leer:")
        for problema in plan.problemas:
            entorno.log(f"     {problema}")
    for linea in describir_ignorados(plan):
        entorno.log(f"⚠  {linea}")

    if plan.paginas == 0:
        return
    recorte = (
        "automático (por página)"
        if opciones.recortar is None
        else ("siempre" if opciones.recortar else "nunca")
    )
    entorno.log(
        f"raíz de espejado : {plan.raiz}   [{plan.motivo_raiz}]\n"
        f"salida           : {opciones.salida}\n"
        f"plan             : {plan.resumen()}\n"
        f"render           : {opciones.dpi} dpi, calidad {opciones.calidad}, "
        f"recorte {recorte}"
        + ("\nmodo             : --solo-medir (no se escribe nada)" if opciones.solo_medir else "")
    )


def _imprimir_resumen(
    plan: Plan, opciones: Opciones, resultados: list[Resultado], entorno: EntornoPdf
) -> None:
    """Cierra con los números de la corrida."""
    escritos = sum(1 for r in resultados if r.estado == "escrito")
    reanudados = sum(1 for r in resultados if r.estado == "reanudado")
    fallos = contar_fallos(resultados)

    entorno.dato("\n=== Resumen ===")
    entorno.dato(f"páginas                   : {plan.paginas}")
    if opciones.solo_medir:
        entorno.dato(f"  se escribirían          : {escritos}")
    else:
        entorno.dato(f"  escritas                : {escritos}")
    if reanudados:
        entorno.dato(f"  ya existían             : {reanudados}")
    if fallos:
        entorno.dato(f"  fallos                  : {fallos}")
    peso = sum(r.peso or 0 for r in resultados if r.peso)
    if peso:
        entorno.dato(f"peso total               : {_bytes(peso)}")
    entorno.dato(f"salida                    : {opciones.salida}")


def _bytes(n: int) -> str:
    """Bytes legibles (B/KiB/MiB/GiB). Mismo formato que ``corpus``."""
    valor = float(n)
    for unidad in ("B", "KiB", "MiB", "GiB"):
        if valor < 1024 or unidad == "GiB":
            return f"{valor:.1f} {unidad}" if unidad != "B" else f"{int(valor)} B"
        valor /= 1024
    return f"{valor:.1f} GiB"


__all__ = [
    "EntornoPdf",
    "EXIT_FALLOS",
    "EXIT_INTERRUMPIDO",
    "EXIT_OK",
    "EXIT_USO",
    "agregar_parser",
    "carpeta_por_defecto",
    "main",
]

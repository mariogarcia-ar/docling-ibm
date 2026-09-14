"""CLI de la reducción de corpus (subcomando ``voucherflow corpus``).

Igual que el resto del repo: ``argparse`` de la stdlib, el dato a ``stdout``,
el progreso y los errores a ``stderr``, y ``main()`` **devuelve** el código de
salida en vez de llamar a ``sys.exit`` (así se testea in-process).
"""

from __future__ import annotations

import argparse
import sys
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import TextIO

from ..settings.config import cargar_settings
from .corrida import (
    CADA_CUANTOS_PROGRESO,
    EXTENSIONES_POR_DEFECTO,
    ErrorCorpus,
    contar_fallos,
    describir_colisiones,
    detectar_colisiones,
    escribir_reporte,
    normalizar_extensiones,
    planificar,
    validar,
)
from .dimensiones import (
    CALIDAD,
    FACTOR_PATCH_QWEN2VL,
    LADO_MAYOR_PX,
    LADO_MENOR_MINIMO_PX,
)
from .modelo import ESTADO_FALLO, Opciones, Resultado

#: Códigos de salida (los mismos del resto del CLI).
EXIT_OK = 0
EXIT_FALLOS = 1
EXIT_USO = 2
EXIT_INTERRUMPIDO = 130


@dataclass
class EntornoCorpus:
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
    """Carpeta de salida por defecto, según la configuración (``paths.processed``)."""
    try:
        return str(cargar_settings().paths.resolver("processed"))
    except Exception:  # noqa: BLE001 - sin configuración legible
        return "var/processed"


def agregar_parser(sub: argparse._SubParsersAction) -> argparse.ArgumentParser:
    """Registra el subcomando ``corpus`` en el parser principal."""
    p = sub.add_parser(
        "corpus",
        help="Pre-reduce el peso y los tokens de visión de un corpus de imágenes.",
        description=(
            "Reduce el lado mayor y el peso de las imágenes de un corpus, "
            "espejando la estructura de carpetas en la salida."
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=(
            "La salida espeja el árbol desde la raíz de la entrada, sin repetir\n"
            "el nombre de la carpeta de entrada:\n"
            "  var/files/2025-08/2D2C9343/foto.jpg  →  "
            "salida/2025-08/2D2C9343/foto.jpg\n"
        ),
    )
    p.add_argument(
        "rutas",
        nargs="+",
        help="Archivos y/o carpetas a procesar (las carpetas se recorren recursivo).",
    )
    p.add_argument(
        "-o",
        "--salida",
        default=None,
        help=(
            "Carpeta raíz de salida (default: la de la configuración, "
            "var/processed). El árbol se espeja desde --raiz (o desde el nivel "
            "que no sea un mes), sin repetir el nombre de la carpeta de entrada."
        ),
    )
    p.add_argument(
        "--raiz",
        type=Path,
        help=(
            "Raíz desde la cual se espeja el árbol en la salida. Si se omite, se "
            "sube hasta el nivel que no sea un mes (procesar «var/files/2025-08» "
            "espeja desde «files»), así la MISMA imagen escribe siempre el MISMO "
            "archivo. Fijala cuando el corpus no tenga esa forma."
        ),
    )
    p.add_argument(
        "--lado-mayor",
        type=int,
        default=LADO_MAYOR_PX,
        metavar="PX",
        help=(
            "Lado mayor objetivo en px (default: %(default)s, la vista de "
            "revisión de la librería). Nunca agranda."
        ),
    )
    p.add_argument(
        "--calidad",
        type=int,
        default=CALIDAD,
        metavar="1-100",
        help="Calidad de reencode (default: %(default)s).",
    )
    p.add_argument(
        "--piso-lado-menor",
        type=int,
        default=LADO_MENOR_MINIMO_PX,
        metavar="PX",
        help=(
            "Piso del lado menor en px, para imágenes muy alargadas "
            "(default: %(default)s)."
        ),
    )
    p.add_argument(
        "--sin-alinear",
        dest="alinear",
        action="store_false",
        help=(
            "No alinear las dimensiones a múltiplos de "
            f"{FACTOR_PATCH_QWEN2VL} (desactivá solo si el destino NO es "
            "Qwen2.5-VL: la alineación hace predecible el conteo de tokens)."
        ),
    )
    p.add_argument(
        "--backend",
        choices=("pillow", "ffmpeg"),
        default="pillow",
        help=(
            "Motor de reencode (default: pillow). ffmpeg reproduce el loop "
            "base, ya corregido."
        ),
    )
    p.add_argument(
        "--formato",
        choices=("mismo", "jpg"),
        default="mismo",
        help=(
            "Formato de salida (default: mismo). «mismo» conserva la "
            "extensión; «jpg» fuerza JPEG y reescribe la extensión a .jpg."
        ),
    )
    p.add_argument(
        "--extensiones",
        default=",".join(sorted(EXTENSIONES_POR_DEFECTO)),
        metavar="LISTA",
        help="Extensiones a procesar, separadas por coma (default: %(default)s).",
    )
    p.add_argument(
        "--forzar",
        action="store_true",
        help="Reescribe el destino aunque ya exista (sin esto, reanuda).",
    )
    p.add_argument(
        "--copiar-no-reducidas",
        action="store_true",
        help=(
            "Copia sin tocar las imágenes que ya entran en el objetivo, para "
            "que la salida quede completa (default: se omiten)."
        ),
    )
    p.add_argument(
        "--solo-medir",
        action="store_true",
        help="No escribe nada: solo mide y reporta qué se reduciría.",
    )
    p.add_argument(
        "--workers",
        type=int,
        default=4,
        metavar="N",
        help=(
            "Procesos concurrentes (default: %(default)s). Usá 1 si el equipo "
            "se calienta."
        ),
    )
    p.add_argument(
        "--limite",
        type=int,
        default=0,
        metavar="N",
        help="Procesa solo las primeras N imágenes (0 = todas). Útil para probar.",
    )
    p.add_argument(
        "--detalle",
        action="store_true",
        help="Imprime una línea por archivo (a stderr).",
    )
    p.add_argument(
        "--reporte",
        metavar="ARCHIVO.json",
        help="Escribe el reporte completo (resumen + por archivo) en JSON.",
    )
    return p


def _opciones_desde(args: argparse.Namespace) -> Opciones:
    """Traduce los argumentos ya parseados a :class:`Opciones`."""
    salida = args.salida or carpeta_por_defecto()
    return Opciones(
        salida=Path(salida),
        lado_mayor=args.lado_mayor,
        calidad=args.calidad,
        piso_lado_menor=args.piso_lado_menor,
        alinear=args.alinear,
        backend=args.backend,
        formato=args.formato,
        forzar=args.forzar,
        escribir=not args.solo_medir,
        copiar_no_reducidas=args.copiar_no_reducidas,
        workers=args.workers,
        detalle=args.detalle,
    )


def main(
    args: argparse.Namespace,
    *,
    entorno: EntornoCorpus | None = None,
) -> int:
    """Ejecuta el subcomando. Devuelve el código de salida (no llama a ``sys.exit``)."""
    entorno = entorno or EntornoCorpus()

    try:
        extensiones = normalizar_extensiones(args.extensiones)
        opciones = _opciones_desde(args)
        validar(opciones)
        rutas = [Path(r) for r in args.rutas]
        raiz, motivo_raiz, tareas = planificar(
            rutas, opciones, extensiones=extensiones, raiz=args.raiz, limite=args.limite
        )
    except ErrorCorpus as exc:
        entorno.log(f"error: {exc}")
        return EXIT_USO

    if not tareas:
        entorno.log("No hay imágenes que procesar.")
        return EXIT_OK

    entorno.log(
        f"raíz de espejado : {raiz}   [{motivo_raiz}]\n"
        f"salida           : {opciones.salida}\n"
        f"imágenes         : {len(tareas)}\n"
        f"objetivo         : lado mayor <= {opciones.lado_mayor}px, "
        f"calidad {opciones.calidad}, backend {opciones.backend}"
    )

    def on_resultado(resultado: Resultado, i: int) -> None:
        if opciones.detalle:
            imprimir_detalle(resultado, entorno)
        elif i % CADA_CUANTOS_PROGRESO == 0:
            entorno.log(f"  … {i}/{len(tareas)}")

    # ⚠️ Antes de escribir UNA sola imagen: dos originales que apunten al mismo
    # destino se pisarían entre sí y el reporte los contaría a los dos. Se
    # rechaza el lote entero (código de uso) en vez de perder archivos.
    colisiones = detectar_colisiones(tareas)
    if colisiones:
        entorno.log(f"error: {describir_colisiones(colisiones)}")
        return EXIT_USO

    try:
        resultados = _ejecutar_tareas(tareas, opciones, on_resultado)
    except KeyboardInterrupt:
        entorno.log("\nInterrumpido por el usuario.")
        return EXIT_INTERRUMPIDO

    from .reporte import resumen

    rep = resumen(resultados, opciones)
    imprimir_resumen(rep, resultados, entorno)

    if args.reporte:
        destino = Path(args.reporte)
        escribir_reporte(destino, rep, resultados)
        entorno.dato(f"\nreporte escrito: {destino}")

    return EXIT_FALLOS if contar_fallos(resultados) else EXIT_OK


def _ejecutar_tareas(tareas, opciones: Opciones, on_resultado) -> list[Resultado]:
    """Atajo para no arrastrar el import del bucle hasta arriba del módulo."""
    from .corrida import ejecutar

    return ejecutar(tareas, opciones, on_resultado=on_resultado)


# ---------------------------------------------------------------------------
# Presentación
# ---------------------------------------------------------------------------


def imprimir_detalle(r: Resultado, entorno: EntornoCorpus) -> None:
    """Una línea por archivo (a stderr, para no ensuciar el reporte)."""
    dims = (
        f"{r.dims_origen[0]}x{r.dims_origen[1]}→{r.dims_destino[0]}x{r.dims_destino[1]}"
        if r.dims_origen and r.dims_destino
        else "?"
    )
    tokens = ""
    if r.tokens_origen is not None and r.tokens_destino is not None:
        tokens = f" tokens {r.tokens_origen:,}→{r.tokens_destino:,}"
    entorno.log(
        f"  [{r.estado}] {r.origen}  {dims}  "
        f"{formato_bytes(r.peso_origen)}→{formato_bytes(r.peso_destino)}{tokens}"
        + (f"  ({r.motivo})" if r.motivo else "")
    )


def imprimir_resumen(
    rep: dict, resultados: Sequence[Resultado], entorno: EntornoCorpus
) -> None:
    """Imprime el resumen: los números a stdout, los fallos a stderr."""
    entorno.dato("\n=== Resumen ===")
    entorno.dato(f"archivos                  : {rep['archivos']}")
    entorno.dato(f"  reducidos               : {rep['reducidos']}")
    entorno.dato(f"  omitidos                : {rep['omitidos']}")
    entorno.dato(f"  fallos                  : {rep['fallos']}")
    if rep["copiadas"]:
        entorno.dato(f"    copiadas (sin reducir)  : {rep['copiadas']}")

    # Si no hubo medición de destino (simulación), se informa el total de
    # origen y se aclara que la reducción de peso NO se midió.
    pct_peso = rep["reduccion_peso_pct"]
    if pct_peso is None:
        entorno.dato(
            f"peso                      : "
            f"{formato_bytes(rep['peso_origen_bytes'])}"
            "  →  (no medido: simulación)"
        )
    else:
        # El signo va en el número, no en el formato: reducir puede AUMENTAR el
        # peso (un escaneo 1-bit que pasa a RGB), y «(-{pct})» con un negativo
        # imprimía «--238.0%».
        signo = "-" if pct_peso >= 0 else "+"
        entorno.dato(
            f"peso                      : {formato_bytes(rep['peso_origen_bytes'])}"
            f"  →  {formato_bytes(rep['peso_destino_bytes'])}"
            f"   ({signo}{abs(pct_peso)}%)"
        )
        if pct_peso < 0:
            entorno.log(
                "⚠  el peso SUBIÓ: la imagen original ya venía más comprimida que"
                " el reencode (típico de escaneos 1-bit/PNG). Los tokens de visión"
                " igual bajan; mirá --detalle para ver qué archivos engordaron."
            )

    if rep["tokens_origen_estimados"]:
        pct_tok = rep["reduccion_tokens_pct"]
        sufijo = f"   (-{pct_tok}%)" if pct_tok is not None else ""
        entorno.dato(
            f"tokens de visión (estim.) : {rep['tokens_origen_estimados']:,}"
            f"  →  {rep['tokens_destino_estimados']:,}{sufijo}"
        )
    entorno.dato(
        f"backend / defaults        : {rep['opciones']['backend']} / "
        f"{rep['defaults_desde']}"
    )
    if rep["opciones"]["solo_medir"]:
        entorno.dato("modo                      : --solo-medir (no se escribió nada)")

    fallos = [r for r in resultados if r.estado == ESTADO_FALLO]
    if fallos:
        entorno.log(f"\n--- Fallos ({len(fallos)}) ---")
        for r in fallos[:20]:
            entorno.log(f"  {r.origen}: {r.motivo}")
        if len(fallos) > 20:
            entorno.log(f"  … y {len(fallos) - 20} más")

    # ⚠️ Un archivo que engordó es invisible en el agregado (el total puede bajar
    # igual). Se listan los peores para que el operador sepa cuáles revisar.
    engordaron = [r for r in resultados if r.engordo]
    if engordaron:
        peores = sorted(
            engordaron,
            key=lambda r: (r.peso_destino or 0) / (r.peso_origen or 1),
            reverse=True,
        )
        entorno.log(f"\n--- El peso subió ({len(engordaron)}) ---")
        for r in peores[:20]:
            factor = r.peso_destino / r.peso_origen
            entorno.log(
                f"  {r.origen}: {formato_bytes(r.peso_origen)} → "
                f"{formato_bytes(r.peso_destino)} (x{factor:.1f})"
            )
        if len(peores) > 20:
            entorno.log(f"  … y {len(peores) - 20} más")


def formato_bytes(n: int | None) -> str:
    """Bytes legibles (B/KiB/MiB/GiB)."""
    if n is None:
        return "—"
    valor = float(n)
    for unidad in ("B", "KiB", "MiB", "GiB"):
        if valor < 1024 or unidad == "GiB":
            return f"{valor:.1f} {unidad}" if unidad != "B" else f"{int(valor)} B"
        valor /= 1024
    return f"{valor:.1f} GiB"

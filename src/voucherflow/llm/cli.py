"""Comando unificado del laboratorio de LLM externos.

Reemplaza a los dos scripts gemelos (``validar-openai.py`` y
``validar-deepseek.py``) por un solo punto de entrada donde **el proveedor es un
argumento**, no una copia del archivo. Agregar uno nuevo (Gemini) es agregar su
adaptador, no un tercer script.

Superficie **simplificada** a propósito: tres operaciones que hacen falta para
ajustar y evaluar un prompt, y nada más.

============================  ==================================================
Operación                     Qué hace
============================  ==================================================
``extraer`` (**default**)     Lee el comprobante y guarda la lectura. Es la
                              pasada que se corre sobre el corpus.
``validar``                   Lee **y** compara contra los datos cargados (la
                              verdad de negocio).
``diff``                      Compara la extracción ya guardada, **sin llamar
                              a la API**: determinístico y auditable.
============================  ==================================================

El ciclo de ajuste que habilita: ``extraer`` para leer el corpus → ``diff`` para
evaluar contra los datos → cambiar el prompt → repetir. Y ``--dry-run`` estima el
costo **antes** de gastarlo, que es lo que evita descubrir el precio después.

Lo que se ganó al unificar: un solo lugar donde está la decisión de no inventar un
costo, de declarar la temperatura ignorada y de acumular el gasto de los
reintentos. Antes eran dos copias que podían divergir —y de hecho divergían: una
leía mal los montos con separador de miles.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

from . import corrida, costos, ejecucion, entorno, imagenes, prompts
from .config import MODELO_POR_DEFECTO, VERSION_PROMPT
from .datos import buscar_datos, cargar_datos
from .esquemas import esquema_extraccion, esquema_validacion
from .evaluador import diff_deterministico, verificar_aritmetica
from .proveedores import PROVEEDORES, describir_proveedores, proveedor_por_nombre

#: Operaciones del comando. Son las tres del ciclo de ajuste.
OPERACIONES = ("extraer", "validar", "diff")

#: Modo del prompt que corresponde a cada operación.
MODO_POR_OPERACION = {"extraer": "extraer", "validar": "validar", "diff": "diff"}

#: Prompt por defecto (el documento de trabajo que se ajusta).
#: El prompt vive en la librería: es un dato versionado del sistema, no de un
#: script. Acepta `.yaml` (el prompt efectivo) o `.md` (el documento). Se puede
#: pisar con --prompt.
PROMPT_POR_DEFECTO = (
    Path(__file__).resolve().parent / "prompts" / "validacion-mendel.yaml"
)


def construir_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="voucherflow-lab",
        description=(
            "Laboratorio de LLM externos: extrae comprobantes y evalúa el prompt "
            "contra los datos cargados."
        ),
    )
    parser.add_argument("rutas", nargs="*", help="archivo o carpeta de imágenes")
    parser.add_argument(
        "-o", "--salida", type=Path, default=Path("validaciones"),
        help="carpeta de salida (default: validaciones)",
    )
    parser.add_argument(
        "-p", "--proveedor", default="deepseek", choices=sorted(PROVEEDORES),
        help="proveedor del modelo (default: deepseek)",
    )
    parser.add_argument("-m", "--modelo", help="modelo (default: el del proveedor)")
    parser.add_argument(
        "-M", "--operacion", default="extraer", choices=OPERACIONES,
        help="qué hacer (default: extraer)",
    )
    parser.add_argument(
        "--datos", type=Path,
        help="JSON con los datos cargados (obligatorio en validar y diff)",
    )
    parser.add_argument("--prompt", type=Path, default=PROMPT_POR_DEFECTO,
                        help=f"documento del prompt (default: {PROMPT_POR_DEFECTO})")
    parser.add_argument("--detalle", default="high", choices=("low", "high", "auto"),
                        help="detalle de la imagen (default: high)")
    parser.add_argument(
        "--prompt-fiel", action="store_true",
        help=(
            "incluir SIEMPRE el ejemplo de salida del prompt, aunque el "
            "proveedor imponga el esquema (cuesta tokens: solo para comparar)"
        ),
    )
    parser.add_argument("--temperatura", type=float,
                        help="temperatura (se declara si el proveedor la ignora)")
    parser.add_argument("--esfuerzo", help="esfuerzo de razonamiento del proveedor")
    parser.add_argument("--max-tokens", type=int,
                        help="techo de tokens de salida (por defecto: el del proveedor)")
    parser.add_argument("--workers", type=int, default=4, help="concurrencia (default: 4)")
    parser.add_argument("--limite", type=int, default=0,
                        help="procesar solo las primeras N (0 = todas)")
    parser.add_argument("--forzar", action="store_true",
                        help="reprocesar lo ya hecho (por defecto reanuda)")
    parser.add_argument("--env", type=Path, help="archivo .env con la credencial")
    parser.add_argument("--api-key", help="credencial explícita (no se imprime)")
    parser.add_argument("--dry-run", action="store_true",
                        help="estimar el costo sin llamar a la API")
    parser.add_argument("--json", type=Path, help="escribir el resumen en JSON")
    parser.add_argument("--detalle-log", action="store_true",
                        help="una línea por archivo")

    # Precios: la tabla es editable y lo que entra por CLI la pisa.
    precios = parser.add_argument_group("precios")
    precios.add_argument("--precios",
                         help="por modelo: «gpt-4o=2.5/10, *=1/3» (la coma separa)")
    precios.add_argument("--precio-entrada", type=float, help="USD por 1M de entrada")
    precios.add_argument("--precio-salida", type=float, help="USD por 1M de salida")
    precios.add_argument("--precio-cache", type=float, help="USD por 1M de entrada cacheada")

    # Reporte de gastos.
    gastos = parser.add_argument_group("reporte de gastos")
    gastos.add_argument("--reporte-gastos", type=Path,
                        help="agregar el gasto del histórico de la salida y salir")
    gastos.add_argument("--csv-gastos", type=Path, help="además, escribir un CSV")
    gastos.add_argument("--tz", default="local", help="zona horaria del reporte (default: local)")

    # Introspección.
    parser.add_argument("--listar-proveedores", action="store_true",
                        help="mostrar las capacidades de cada proveedor y salir")
    return parser


def _resolver_modelo(proveedor: str, modelo: str | None) -> str:
    """Modelo pedido, o el default del proveedor declarado."""
    if modelo:
        return modelo
    cap = proveedor_por_nombre(proveedor).capacidades
    # El default lo declara el proveedor: asumir el de otro estimaría mal.
    return cap.modelo_por_defecto or MODELO_POR_DEFECTO


def _precios_de(args: argparse.Namespace) -> dict[str, tuple[float | None, float | None]]:
    """Tabla de precios de la corrida: lo que entra por CLI pisa la referencia."""
    tabla: dict[str, tuple[float | None, float | None]] = {}
    if args.precios:
        tabla.update(costos.parsear_precios(args.precios))
    if args.precio_entrada is not None or args.precio_salida is not None:
        # Un precio suelto aplica a **todos** los modelos, así que se usa el
        # comodín (y no se toca la tabla de referencia por modelo).
        tabla["*"] = (args.precio_entrada, args.precio_salida)
    return tabla


def _mostrar_proveedores() -> int:
    print("Proveedores disponibles\n")
    for ficha in describir_proveedores():
        print(f"  {ficha['proveedor']}")
        print(f"    base_url            : {ficha['base_url'] or '(default del SDK)'}")
        print(f"    esquema estricto    : {'sí' if ficha['esquema_estricto'] else 'no (valida local)'}")
        print(f"    temperatura         : {'tiene efecto' if ficha['temperatura_efectiva'] else 'la ignora (se declara)'}")
        print(f"    esfuerzos           : {', '.join(ficha['esfuerzos']) or '—'}")
        print(f"    tokens por imagen   : {ficha['tokens_por_imagen']}")
        print(f"    caché de entrada    : {'expone' if ficha['expone_cache'] else 'no expone'}")
        for nota in ficha["notas"]:
            print(f"    · {nota}")
        print()
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = construir_parser()
    args = parser.parse_args(argv)

    if args.listar_proveedores:
        return _mostrar_proveedores()

    if args.operacion in {"validar", "diff"} and not args.datos:
        parser.error(f"--datos es obligatorio con la operación {args.operacion}")

    if not args.rutas:
        parser.print_help()
        return 2

    # El prompt vive en el `.md`: se ajusta ahí y el código lo toma.
    if not args.prompt.is_file():
        print(f"error: no existe el prompt {args.prompt}", file=sys.stderr)
        return 2
    try:
        sistema, user_template = prompts.cargar_prompt(args.prompt)
    except (OSError, ValueError) as exc:
        print(f"error: no se pudo leer el prompt: {exc}", file=sys.stderr)
        return 2

    if args.forzar and args.dry_run:
        print("error: --forzar y --dry-run son excluyentes", file=sys.stderr)
        return 2
    if args.forzar:
        # Sin esto, `main` estima solo los pendientes: para simular el lote
        # completo hay que forzarlo (o usar una salida vacía).
        print("nota: --forzar con --dry-run estima el lote completo")

    # El `.env` se carga **antes** de resolver la credencial, y sin pisar lo que
    # ya esté exportado: una variable del entorno gana sobre el archivo (así se
    # puede probar otra clave sin editar el `.env`).
    if args.env:
        if not args.env.is_file():
            print(f"error: no existe el .env {args.env}", file=sys.stderr)
            return 2
        entorno.cargar_env(args.env)

    print(f"proveedor : {args.proveedor}")
    print(f"operación : {args.operacion}")
    print(f"prompt    : {args.prompt} ({VERSION_PROMPT})")
    print(f"salida    : {args.salida}")
    if args.dry_run:
        print("modo      : --dry-run (simulación: no llama a la API ni escribe)")
    print()

    opciones = corrida.Opciones(
        modo=args.operacion,
        modelo=_resolver_modelo(args.proveedor, args.modelo),
        detalle=args.detalle,
        temperatura=args.temperatura,
        max_tokens=args.max_tokens,
        esfuerzo=args.esfuerzo,
        salida=args.salida,
        forzar=args.forzar,
        workers=args.workers,
        dry_run=args.dry_run,
        # La forma de la respuesta: si el proveedor **no** impone el esquema en
        # el servidor (DeepSeek, Gemini), el ejemplo tiene que viajar en el
        # prompt o el modelo devuelve una forma que el validador rechaza.
        # `--prompt-fiel` lo fuerza igual, para medir cuánto aporta.
        incluir_ejemplo=(
            args.prompt_fiel
            or not proveedor_por_nombre(args.proveedor).capacidades.esquema_estricto
        ),
        precio_entrada=args.precio_entrada,
        precio_salida=args.precio_salida,
        precios=_precios_de(args),
        precios_cache=costos.PRECIOS_CACHE,
        # `tz` es un `timezone`; el CLI acepta `local` o un offset como -03:00.
        tz=corrida._tz_desde(args.tz)[0],
        tz_etiqueta=corrida._tz_desde(args.tz)[1],
    )

    datos = cargar_datos(args.datos) if args.datos else {}
    try:
        # La credencial se resuelve acá y no antes: un proveedor sin clave
        # (los tests, `--listar-proveedores`) no tiene por qué fallar.
        credencial = entorno.resolver_api_key(args.api_key)
    except RuntimeError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    return corrida.ejecutar(
        rutas=[Path(r) for r in args.rutas],
        opciones=opciones,
        sistema=sistema,
        user_template=user_template,
        datos=datos,
        proveedor=args.proveedor,
        api_key=credencial,
        limite=args.limite,
        detalle_log=args.detalle_log,
        json_salida=args.json,
    )


def entrypoint() -> None:
    """Entry point del binario: `main` devuelve el código, acá se propaga."""
    raise SystemExit(main())


if __name__ == "__main__":
    entrypoint()

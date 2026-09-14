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
import sys
from pathlib import Path

from . import corrida, costos, entorno, prompts
from .config import MODELO_POR_DEFECTO, VERSION_PROMPT
from .datos import cargar_datos
from .proveedores import (
    PROVEEDOR_POR_DEFECTO,
    PROVEEDORES,
    describir_proveedores,
    proveedor_por_nombre,
)

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
        # El default sale de la configuración (`paths.validations`), no de un
        # literal: así los paths viven en un solo lugar.
        "-o", "--salida", type=Path, default=None,
        help="carpeta de salida (default: el de la configuración, var/validations)",
    )
    parser.add_argument(
        "-p", "--proveedor", default=PROVEEDOR_POR_DEFECTO, choices=sorted(PROVEEDORES),
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
    gastos.add_argument("--csv-delim", default=",", metavar="C",
                        help="delimitador del CSV de gastos (default: «,»; Excel es-AR usa «;»)")
    gastos.add_argument("--csv-decimal", default=".", metavar="C",
                        help="separador decimal del CSV (default: «.»; Excel es-AR usa «,»)")
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


def _precios_cache_de(
    args: argparse.Namespace,
) -> dict[str, float]:
    """Precio de la entrada cacheada: la bandera pisa la tabla de referencia.

    ⚠️ ``--precio-cache`` se declaraba en el parser y **nadie lo leía**: el CLI
    pasaba siempre ``costos.PRECIOS_CACHE``, así que una tarifa distinta (un tramo
    off-peak, otra cuenta) se ignoraba en silencio y el costo salía con el precio
    de referencia. La tabla de referencia se usa solo cuando no se pasa la bandera.
    """
    if args.precio_cache is None:
        return costos.PRECIOS_CACHE
    # Una tarifa suelta aplica a todos los modelos, como `--precio-entrada`.
    return {"*": args.precio_cache}


def _mostrar_proveedores() -> int:
    print("Proveedores disponibles\n")
    for ficha in describir_proveedores():
        print(f"  {ficha['proveedor']}")
        print(f"    base_url            : {ficha['base_url'] or '(default del SDK)'}")
        print(f"    esquema estricto    : {'sí' if ficha['esquema_estricto'] else 'no (valida local)'}")
        print(f"    temperatura         : {'tiene efecto' if ficha['temperatura_efectiva'] else 'la ignora (se declara)'}")
        print(f"    esfuerzos           : {', '.join(ficha['esfuerzos']) or '—'}")
        # Cómo cobra la imagen, que es lo que cambia el costo entre proveedores.
        if ficha["estrategia_imagen"] == "mosaicos":
            print("    costo de la imagen  : por resolución (mosaicos de 512; `detail=low` = 85)")
        else:
            print(
                f"    costo de la imagen  : fijo, {ficha['tokens_por_imagen']} por imagen "
                "(no mira el tamaño)"
            )
        # El límite es sobre el payload (base64 infla un 33 %), así que el archivo
        # máximo real es un 25 % menor. Se muestran los dos para no confundirlos.
        limite_mb = ficha["limite_bytes_payload"] / 1024 / 1024
        print(
            f"    límite por imagen   : {limite_mb:.0f} MB de payload "
            f"(archivo de hasta ~{limite_mb / (4 / 3):.0f} MB)"
        )
        print(f"    caché de entrada    : {'expone' if ficha['expone_cache'] else 'no expone'}")
        for nota in ficha["notas"]:
            print(f"    · {nota}")
        print()
    return 0


def _normalizar_argv(argv: list[str] | None) -> list[str]:
    """Une los valores que empiezan con ``-`` al argumento que los espera.

    ``argparse`` lee ``--tz -03:00`` como «falta un argumento» porque el valor
    arranca con ``-`` y lo toma por una bandera. Un offset horario es un valor
    legítimo (y el ejemplo documentado del reporte), así que se reescribe a la
    forma ``--tz=-03:00`` antes de parsear.

    Con ``argv=None`` se normaliza ``sys.argv[1:]``: es el camino del binario
    real (``entrypoint`` llama a ``main()`` sin argumentos), y si no se
    normalizara ahí, la bandera seguiría fallando desde la terminal.
    """
    if argv is None:
        argv = sys.argv[1:]
    con_valor_negativo = ("--tz",)
    normalizado: list[str] = []
    i = 0
    while i < len(argv):
        actual = argv[i]
        if actual in con_valor_negativo and i + 1 < len(argv) and argv[i + 1].startswith("-"):
            normalizado.append(f"{actual}={argv[i + 1]}")
            i += 2
            continue
        normalizado.append(actual)
        i += 1
    return normalizado


def _reportar_gastos(args: argparse.Namespace, salida: Path) -> int:
    """Atiende ``--reporte-gastos`` / ``--csv-gastos`` y devuelve el código de salida.

    Lee el histórico de la carpeta, imprime el reporte y escribe lo que se haya
    pedido. No llama a la API ni toca la credencial: es consulta, no corrida.
    """
    try:
        tz, tz_etiqueta = corrida._tz_desde(args.tz)
    except ValueError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2

    opciones = corrida.Opciones(
        modo="gastos",
        modelo=_resolver_modelo(args.proveedor, args.modelo),
        detalle=args.detalle,
        temperatura=None,
        max_tokens=None,
        esfuerzo=None,
        salida=salida,
        forzar=False,
        workers=1,
        dry_run=True,  # no es una corrida: nada de esto se usa para cobrar
        incluir_ejemplo=False,
        proveedor=args.proveedor,
        precios=_precios_de(args),
        # La bandera pisa la tabla de referencia (ver `_precios_cache_de`).
        precios_cache=_precios_cache_de(args),
        tz=tz,
        tz_etiqueta=tz_etiqueta,
        csv_delim=args.csv_delim,
        csv_decimal=args.csv_decimal,
    )

    try:
        rep, apuntes = corrida.reporte_de_gastos(
            salida,
            opciones,
            json_salida=args.reporte_gastos,
            csv_salida=args.csv_gastos,
        )
    except OSError as exc:
        print(f"error: no se pudo escribir el reporte: {exc}", file=sys.stderr)
        return 2

    corrida.imprimir_reporte_gastos(rep, apuntes)
    if args.reporte_gastos:
        print(f"\nreporte           : {args.reporte_gastos}")
    if args.csv_gastos:
        print(f"csv               : {args.csv_gastos}")
    # Un total incompleto no es un éxito: el número que muestra es un piso.
    return 1 if rep["costo_parcial"] else 0


def main(argv: list[str] | None = None) -> int:
    parser = construir_parser()
    args = parser.parse_args(_normalizar_argv(argv))

    if args.listar_proveedores:
        return _mostrar_proveedores()

    # La carpeta de salida: la pedida con `-o`, o la de la configuración. El
    # default sale de `paths.validations` y no de un literal, así los paths
    # viven en un solo lugar (`voucherflow.yaml` / `VOUCHERFLOW__PATHS__…`).
    # Se resuelve antes del reporte de gastos, que la necesita y no tiene rutas.
    from voucherflow.settings.config import cargar_settings

    ajustes = cargar_settings()
    salida = args.salida or ajustes.paths.resolver("validations")

    # El reporte de gastos es un modo de **consulta**: lee el histórico de la
    # carpeta de salida y sale. No recorre rutas, no arma el prompt y no
    # necesita credencial, así que se atiende antes de validar esas cosas.
    # (Estas banderas existían en el parser pero nadie las leía: el reporte era
    # código inalcanzable.)
    if args.reporte_gastos or args.csv_gastos:
        return _reportar_gastos(args, salida)

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
        # ⚠️ No son excluyentes. El dry-run **no escribe**, así que `--forzar`
        # no tiene nada que forzar: es inocuo. Rechazarlo era un error, porque
        # la documentación del laboratorio invita justamente a esa combinación
        # para simular el lote completo (y el mensaje contradecía a la nota que
        # decía que esa combinación estima el lote completo).
        print(
            "nota: --forzar no cambia la estimación: --dry-run ya recorre el "
            "lote completo, sin saltar lo ya procesado.",
            file=sys.stderr,
        )

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
    print(f"salida    : {salida}")
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
        salida=salida,
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
        # El proveedor viaja en las opciones porque la estimación del costo de la
        # imagen depende de él (OpenAI cobra por mosaicos, DeepSeek a tope fijo).
        proveedor=args.proveedor,
        precio_entrada=args.precio_entrada,
        precio_salida=args.precio_salida,
        precios=_precios_de(args),
        precios_cache=_precios_cache_de(args),
        # `tz` es un `timezone`; el CLI acepta `local` o un offset como -03:00.
        tz=corrida._tz_desde(args.tz)[0],
        tz_etiqueta=corrida._tz_desde(args.tz)[1],
    )

    datos = cargar_datos(args.datos) if args.datos else {}
    try:
        # La credencial se resuelve acá y no antes: un proveedor sin clave
        # (los tests, `--listar-proveedores`) no tiene por qué fallar. Se pasa
        # el proveedor elegido porque la variable depende de él
        # (OPENAI_API_KEY / DEEPSEEK_API_KEY / GEMINI_API_KEY).
        credencial = entorno.resolver_api_key(args.api_key, args.proveedor)
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
    """Entry point del binario: `main` devuelve el código, acá se propaga.

    ⚠️ ``KeyboardInterrupt`` se atrapa **acá** además de en la corrida: un Ctrl-C
    durante la carga del prompt, la resolución de la credencial o la escritura
    del reporte salía como traceback con código 1. El resto del CLI (`corpus`)
    devuelve 130, y con eso el operador distingue «lo interrumpí» de «falló».
    """
    try:
        codigo = main()
    except KeyboardInterrupt:
        print("\nInterrumpido por el usuario.", file=sys.stderr)
        codigo = corrida.EXIT_INTERRUMPIDO
    raise SystemExit(codigo)


if __name__ == "__main__":
    entrypoint()

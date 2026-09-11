#!/usr/bin/env python
"""Inspecciona T-404 (F4) — combinación de evidencia con resolución por campo.

**Fase**: F4 (extracción) · **Tarea**: T-404 · **Épica**: E-EXT-1 · **ADR-002**.

Muestra, **sin Ollama ni Docling** (lectores dobles inyectados):

  1. La **tabla de precedencia** campo por campo: qué lectura gana en cada campo
     (visual para la letra y el número impreso; textual para fecha, moneda e
     importes; programa para los derivados) y con qué regla (`PREC_n`).
  2. Los **escenarios de resolución**: acuerdo entre fuentes, desacuerdo resuelto
     por precedencia, campo que solo leyó una fuente, campo que ninguna leyó
     (no se inventa), y el caso en que la ganadora por precedencia está
     **invalidada** por la pasada 1 (T-403) — no puede ganar, y si ninguna es
     utilizable la resolución lo declara.
  3. Las **fronteras** de la tarea: la combinación conserva **todas** las
     lecturas, **no** decide el caso (`decision=None`: eso es F5/T-501) y **no**
     re-calcula la normalización ni la pasada raw.

Con `--origen` corre la extracción **real** contra Ollama (con `--manual` usa dos
lecturas sintéticas: útil sin GPU) y muestra, campo por campo, quién ganó y por
qué.

Uso:
    python scripts/F4/t404.py                        # tabla + escenarios + fronteras
    python scripts/F4/t404.py --tabla                # solo la tabla de precedencia
    python scripts/F4/t404.py --caso cuit_discrepa   # un solo escenario
    python scripts/F4/t404.py --json /tmp/t404.json
    python scripts/F4/t404.py --manual               # pipeline F4 real, lectura sintética
    python scripts/F4/t404.py --origen files/2025-08/2D2C9343/<doc>.jpg --detalle

Nota: la suite default de pytest cubre lo mismo en
``tests/test_extraction_combinacion_t404.py``; este script es la verificación de
humo legible para la bitácora (sale con código ≠ 0 si algún escenario falla).
"""

from __future__ import annotations

import argparse
import json
import sys
import tempfile
from pathlib import Path
from typing import Any

# El paquete usa layout src/; si se corre sin instalar, se agrega src/ al sys.path.
_SRC = Path(__file__).resolve().parents[2] / "src"
if str(_SRC) not in sys.path:
    sys.path.insert(0, str(_SRC))

from voucherflow.extraction import (  # noqa: E402
    CAMPO_FUENTE_LECTURA,
    CLAVE_CAMPOS,
    VERSION_COMBINACION,
    combinar_evidencia,
    construir_source_evidence,
    parsear_evidencia_extraccion,
    veredicto_raw_de_evidencia,
)
from voucherflow.extraction.key_value import normalizar_evidencia  # noqa: E402
from voucherflow.extraction.prompt_extraccion import (  # noqa: E402
    SYSTEM_PROMPT_POR_FUENTE,
)
from voucherflow.rules.precedencia import (  # noqa: E402
    FUENTES_NO_LECTURA,
    TABLA_PRECEDENCIA,
)
from voucherflow.schemas.evidence import Fuente, SourceEvidence  # noqa: E402
from voucherflow.settings.config import cargar_desde_dict  # noqa: E402
from voucherflow.validation.vistas import VistaPreparada  # noqa: E402

# ---------------------------------------------------------------------------
# Utilidades
# ---------------------------------------------------------------------------

_PNG_1PX = (
    b"\x89PNG\r\n\x1a\n"
    b"\x00\x00\x00\rIHDR\x00\x00\x00\x01\x00\x00\x00\x01\x08\x02\x00\x00\x00"
    b"\x90wS\xde\x00\x00\x00\x0cIDATx\x9cc\xf8\xcf\xc0\x00\x00\x00\x03\x00\x01"
    b"\x86\xa0\xb5\x9d\x00\x00\x00\x00IEND\xaeB`\x82"
)


def _campo(valor: Any, sustento: str) -> dict[str, Any]:
    return {"valor": valor, "fragmento_sustento": sustento}


def _fuente_desde_json(campos: dict[str, Any], fuente: str) -> SourceEvidence:
    """``SourceEvidence`` completa del pipeline real: interpretar + normalizar +
    pasada 1 (T-401 → T-402 → T-403)."""
    crudo = json.dumps(
        {CAMPO_FUENTE_LECTURA: fuente, CLAVE_CAMPOS: campos}, ensure_ascii=False
    )
    lectura = parsear_evidencia_extraccion(crudo, fuente=fuente)
    normalizada = normalizar_evidencia(lectura).evidencia
    veredicto = veredicto_raw_de_evidencia(normalizada)
    return construir_source_evidence(normalizada, veredicto=veredicto)


def _source_simple(fuente: Fuente, **campos: Any) -> SourceEvidence:
    """``SourceEvidence`` con valores ya canónicos (sin pasar por el pipeline)."""
    from voucherflow.schemas.evidence import EvidenceField  # noqa: PLC0415

    return SourceEvidence(
        fuente=fuente,
        campos={
            nombre: EvidenceField(
                campo=nombre,
                valor=valor,
                fuente=fuente,
                fragmento_sustento=f"fragmento de {nombre}",
            )
            for nombre, valor in campos.items()
        },
    )


def _vista_imagen() -> VistaPreparada:
    tmp = tempfile.NamedTemporaryFile(suffix=".png", delete=False)
    tmp.write(_PNG_1PX)
    tmp.close()
    return VistaPreparada(
        tipo_vista="fiel",
        calidad="alta",
        representacion=tmp.name,
        resolucion_objetivo=2048,
        origen=tmp.name,
        ruta_imagen_original=tmp.name,
        nota="Vista fiel sintética (T-404)",
    )


class LectorDoble:
    """Doble del ``OllamaClient``: responde por fuente, sin red."""

    def __init__(self, por_fuente: dict[str, str]) -> None:
        self.por_fuente = por_fuente

    def ask(self, messages, model, json_format=False, options=None, num_ctx=None):
        sistema = messages[0].get("content", "") if messages else ""
        fuente = next(
            (f for f, prompt in SYSTEM_PROMPT_POR_FUENTE.items() if prompt == sistema),
            "",
        )

        class _R:
            contenido = self.por_fuente.get(fuente, "{}")

        return _R()


def _settings() -> Any:
    return cargar_desde_dict(
        {
            "modelos": {
                "vlm": {"rol": "vlm", "modelo": "vlm-doble", "num_ctx": 4096},
                "llm": {"rol": "llm", "modelo": "llm-doble", "num_ctx": 8192},
            }
        }
    )


# ---------------------------------------------------------------------------
# 1. La tabla de precedencia
# ---------------------------------------------------------------------------


def _imprimir_tabla() -> None:
    print("\nTabla de precedencia por campo (ADR-002)")
    print("  la regla de oro: lo comprobado/corregido manda; entre lecturas, el papel")
    no_lectura = " > ".join(f.value for f in FUENTES_NO_LECTURA)
    print(f"  fuentes no-lectura (siempre delante): {no_lectura}")
    print()
    for campo, precedencia in sorted(TABLA_PRECEDENCIA.items()):
        lecturas = " > ".join(f.value for f in precedencia.orden_lecturas())
        print(f"  {precedencia.regla:7} {campo:26} lecturas: {lecturas}")
    print(f"\n  (los campos sin entrada usan la regla de oro PREC_0)")


# ---------------------------------------------------------------------------
# 2. Escenarios de resolución
# ---------------------------------------------------------------------------

ESCENARIOS: list[dict[str, Any]] = [
    {
        "nombre": "acuerdo_entre_fuentes",
        "que": "las dos fuentes leen lo mismo: gana la de mayor precedencia y se registra el acuerdo",
        "vlm": {Fuente.vlm: _source_simple(Fuente.vlm, tipo_comprobante="A")},
        "llm": {Fuente.llm: _source_simple(Fuente.llm, tipo_comprobante="A")},
        "espera": {"campo": "tipo_comprobante", "ganador": "vlm", "valor": "A", "acuerdo": True},
    },
    {
        "nombre": "letra_discrepa_gana_visual",
        "que": "la letra discrepa y gana la lectura VISUAL (PREC_1, ADR-002)",
        "vlm": {Fuente.vlm: _source_simple(Fuente.vlm, tipo_comprobante="A")},
        "llm": {Fuente.llm: _source_simple(Fuente.llm, tipo_comprobante="B")},
        "espera": {
            "campo": "tipo_comprobante",
            "ganador": "vlm",
            "valor": "A",
            "acuerdo": False,
            "regla": "PREC_1",
        },
    },
    {
        "nombre": "importe_discrepa_gana_textual",
        "que": "el importe discrepa y gana la lectura TEXTUAL (PREC_2: el OCR conserva los dígitos)",
        "vlm": {Fuente.vlm: _source_simple(Fuente.vlm, importe_total_facturado=111.0)},
        "llm": {Fuente.llm: _source_simple(Fuente.llm, importe_total_facturado=222.0)},
        "espera": {
            "campo": "importe_total_facturado",
            "ganador": "llm",
            "valor": 222.0,
            "acuerdo": False,
            "regla": "PREC_2",
        },
    },
    {
        "nombre": "solo_una_fuente_leyo",
        "que": "un campo que solo leyó una fuente se resuelve con esa lectura (no hay desacuerdo)",
        "vlm": {Fuente.vlm: _source_simple(Fuente.vlm, cuit_receptor="27-30111222-4")},
        "llm": {Fuente.llm: _source_simple(Fuente.llm, cuit_emisor="30-12345678-9")},
        "espera": {"campo": "cuit_receptor", "ganador": "vlm", "valor": "27-30111222-4", "acuerdo": True},
    },
    {
        "nombre": "ninguna_leyo_el_campo",
        "que": "un campo que ninguna fuente declaró no se inventa (queda para el gate de F5)",
        "vlm": {Fuente.vlm: _source_simple(Fuente.vlm, tipo_comprobante="A")},
        "llm": {Fuente.llm: _source_simple(Fuente.llm, tipo_comprobante="A")},
        "espera": {"campo": "cuit_receptor", "ganador": None, "valor": None},
    },
    {
        "nombre": "la_ganadora_esta_invalidada",
        "que": "una fuente invalidada por la pasada 1 (T-403) no gana: cede a la otra lectura",
        "vlm": {Fuente.vlm: _source_simple(Fuente.vlm, tipo_comprobante="X")},
        "llm": {Fuente.llm: _source_simple(Fuente.llm, tipo_comprobante="A")},
        "invalidas": ["vlm"],
        "espera": {
            "campo": "tipo_comprobante",
            "ganador": "llm",
            "valor": "A",
            "menciona_invalidas": True,
        },
    },
    {
        "nombre": "ninguna_utilizable",
        "que": "si ninguna fuente es utilizable, gana la precedencia pero la resolución lo declara",
        "vlm": {Fuente.vlm: _source_simple(Fuente.vlm, tipo_comprobante="X")},
        "llm": {Fuente.llm: _source_simple(Fuente.llm, tipo_comprobante="Y")},
        "invalidas": ["vlm", "llm"],
        "espera": {"campo": "tipo_comprobante", "confiable": False},
    },
    {
        "nombre": "director_de_programa",
        "que": "una fuente del programa (dato derivado) manda sobre cualquier lectura",
        "vlm": {Fuente.vlm: _source_simple(Fuente.vlm, punto_venta="00006")},
        "llm": {Fuente.llm: _source_simple(Fuente.llm, punto_venta="00007")},
        "programa": {Fuente.programa: _source_simple(Fuente.programa, punto_venta="00005")},
        "espera": {"campo": "punto_venta", "ganador": "programa", "valor": "00005"},
    },
]


def _correr_escenario(escenario: dict[str, Any]) -> dict[str, Any]:
    fuentes: list[SourceEvidence] = []
    invalidas = set(escenario.get("invalidas", ()))
    for clave in ("vlm", "llm", "programa"):
        for fuente, source in escenario.get(clave, {}).items():
            if fuente.value in invalidas:
                source = source.model_copy(update={"valida": False})
            fuentes.append(source)
    try:
        combinada = combinar_evidencia(escenario["nombre"], fuentes)
        return {"combinada": combinada, "error": None}
    except Exception as exc:  # noqa: BLE001 - el escenario declara qué espera
        return {"combinada": None, "error": exc}


def _verificar(corrida: dict[str, Any], espera: dict[str, Any]) -> list[str]:
    if corrida["error"] is not None:
        return [f"error inesperado: {type(corrida['error']).__name__}: {corrida['error']}"]
    combinada = corrida["combinada"]
    campo = espera["campo"]
    problemas: list[str] = []

    if campo not in combinada.campos:
        if espera.get("ganador") is None and espera.get("valor") is None:
            return []  # el campo efectivamente no está
        return [f"el campo {campo} no está en la combinación"]

    combinado = combinada.campos[campo]
    if "ganador" in espera:
        real = combinado.fuente.value if combinado.fuente else None
        if real != espera["ganador"]:
            problemas.append(f"ganador={real!r} (esperado {espera['ganador']!r})")
    if "valor" in espera and combinado.valor != espera["valor"]:
        problemas.append(f"valor={combinado.valor!r} (esperado {espera['valor']!r})")
    if "acuerdo" in espera:
        real = combinada.trazabilidad["combinacion"]["resoluciones"].get(campo, {}).get(
            "acuerdo"
        )
        if real is None:
            real = campo not in combinada.trazabilidad["combinacion"]["desacuerdos"]
        if bool(real) != espera["acuerdo"]:
            problemas.append(f"acuerdo={real} (esperado {espera['acuerdo']})")
    if "regla" in espera:
        real = combinado.resolucion.regla
        if real != espera["regla"]:
            problemas.append(f"regla={real!r} (esperado {espera['regla']!r})")
    if espera.get("menciona_invalidas") and "inválidas" not in combinado.resolucion.motivo:
        problemas.append("la resolución no menciona las fuentes inválidas")
    if "confiable" in espera:
        info = combinada.trazabilidad["combinacion"]["resoluciones"].get(campo, {})
        real = info.get("confiable", True)
        if bool(real) != espera["confiable"]:
            problemas.append(f"confiable={real} (esperado {espera['confiable']})")
    return problemas


def _imprimir_resultado(corrida: dict[str, Any], detalle: bool) -> None:
    combinada = corrida["combinada"]
    info = combinada.trazabilidad["combinacion"]
    print(
        f"    · fuentes={info['fuentes']} · campos={info['campos_totales']} · "
        f"acuerdos={info['acuerdos']} · desacuerdos={info['desacuerdos']}"
    )
    for campo, resolucion in info["resoluciones"].items():
        print(
            f"    {campo}: gana {resolucion['ganador']} "
            f"({resolucion['regla']}) → {resolucion['descartadas']}"
        )
        if detalle:
            print(f"      {resolucion['motivo'][:160]}")


# ---------------------------------------------------------------------------
# 3. Fronteras de la tarea
# ---------------------------------------------------------------------------


def _verificar_fronteras() -> list[dict[str, Any]]:
    vlm = _source_simple(Fuente.vlm, tipo_comprobante="A")
    llm = _source_simple(Fuente.llm, tipo_comprobante="B")
    combinada = combinar_evidencia("doc-1", [vlm, llm])
    campo = combinada.campos["tipo_comprobante"]
    checks = [
        (
            "conserva TODAS las lecturas (combinar no es descartar)",
            campo.vlm is not None
            and campo.llm is not None
            and campo.llm.valor == "B"
            and bool(campo.llm.fragmento_sustento),
        ),
        (
            "no decide el caso: `decision` viaja en None (eso es F5/T-501)",
            combinada.decision is None,
        ),
        (
            "resuelve todos los campos que alguna fuente declaró",
            set(combinada.campos) == {"tipo_comprobante"},
        ),
        (
            "la versión de la combinación queda registrada (reproducibilidad)",
            combinada.trazabilidad["version_combinacion"] == VERSION_COMBINACION,
        ),
    ]
    return [{"que": que, "ok": ok} for que, ok in checks]


# ---------------------------------------------------------------------------
# 4. Pipeline real (--manual / --origen)
# ---------------------------------------------------------------------------

#: Lecturas sintéticas que **discrepan** en la letra y coinciden en el total:
#: es el caso que muestra para qué sirve la precedencia.
_LECTURA_VLM: dict[str, Any] = {
    "tipo_comprobante": _campo("A", "Recuadro grande con 'A' y COD. 01"),
    "cuit_emisor": _campo("30-12345678-9", "C.U.I.T. 30-12345678-9"),
    "cuit_receptor": _campo("27-30111222-4", "C.U.I.T. 27-30111222-4"),
    "importe_total_facturado": _campo(12345.67, "Importe Total: $12.345,67"),
}
_LECTURA_LLM: dict[str, Any] = {
    "tipo_comprobante": _campo("B", "FACTURA B"),
    "cuit_emisor": _campo("30-12345678-9", "C.U.I.T. 30-12345678-9"),
    "cuit_receptor": _campo("27-30111222-4", "C.U.I.T. 27-30111222-4"),
    "importe_total_facturado": _campo(12345.67, "Importe Total: $ 12.345,67"),
}


def _correr_manual(detalle: bool) -> None:
    """Pipeline F4 real (T-401→T-403) con la lectura del modelo inyectada."""
    from voucherflow.extraction import extraer

    lector = LectorDoble(
        {
            "vlm": json.dumps(
                {CAMPO_FUENTE_LECTURA: "vlm", CLAVE_CAMPOS: _LECTURA_VLM},
                ensure_ascii=False,
            ),
            "llm": json.dumps(
                {CAMPO_FUENTE_LECTURA: "llm", CLAVE_CAMPOS: _LECTURA_LLM},
                ensure_ascii=False,
            ),
        }
    )
    resultado = extraer(
        lector,
        markdown="FACTURA B\nC.U.I.T. 30-12345678-9",
        vista=_vista_imagen(),
        documento_id="manual",
        settings=_settings(),
    )
    fuentes = list(resultado.evidencias_por_fuente().values())
    combinada = combinar_evidencia("manual", fuentes)
    print("\nPipeline F4 real (lectura del modelo inyectada: las fuentes discrepan en la letra)")
    for fuente, evidencia in resultado.evidencias_por_fuente().items():
        print(
            f"  [{fuente}] valida={evidencia.valida} "
            f"letra={evidencia.campos.get('tipo_comprobante').valor if 'tipo_comprobante' in evidencia.campos else None!r} "
            f"debilidades={len(evidencia.debilidades)}"
        )
    info = combinada.trazabilidad["combinacion"]
    print(f"  desacuerdos resueltos: {info['desacuerdos']}")
    for campo, resolucion in info["resoluciones"].items():
        print(f"  {campo}: gana {resolucion['ganador']} ({resolucion['regla']})")
        if detalle:
            print(f"    {resolucion['motivo'][:200]}")
    print(f"  decision: {combinada.decision!r} (la produce F5: combinar no es decidir)")


def _correr_origen(origen: Path, modelo: str | None, detalle: bool) -> None:
    from voucherflow import api
    from voucherflow.extraction import extraer
    from voucherflow.models.ollama import OllamaClient
    from voucherflow.validation.vistas import preparar_vista_fiel

    documento = api.process(str(origen))
    vista = preparar_vista_fiel(documento, str(origen))
    resultado = extraer(
        OllamaClient(),
        markdown=documento.markdown,
        vista=vista,
        documento_id=str(origen),
        modelo=modelo,
    )
    fuentes = list(resultado.evidencias_por_fuente().values())
    combinada = combinar_evidencia(str(origen), fuentes)
    print(f"\nOrigen: {origen}")
    info = combinada.trazabilidad["combinacion"]
    print(f"  fuentes: {info['fuentes']}")
    for campo, combinado in combinada.campos.items():
        if combinado.fuente is None:
            continue
        marca = "  " if campo not in info["desacuerdos"] else "≠ "
        print(
            f"  {marca}{campo:26} {combinado.valor!r:18} "
            f"({combinado.fuente.value}, {combinado.resolucion.regla})"
        )
        if detalle and campo in info["desacuerdos"]:
            print(f"      {combinado.resolucion.motivo[:200]}")


# ---------------------------------------------------------------------------
# main
# ---------------------------------------------------------------------------


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Inspecciona la combinación por precedencia de F4/T-404 (sin Ollama)"
    )
    parser.add_argument("--tabla", action="store_true", help="solo la tabla de precedencia")
    parser.add_argument("--caso", help="corre un solo escenario por nombre")
    parser.add_argument("--detalle", action="store_true", help="motivos de cada resolución")
    parser.add_argument("--manual", action="store_true", help="pipeline F4 real con lectura inyectada")
    parser.add_argument("--origen", type=Path, help="documento real (corre Ollama)")
    parser.add_argument("--modelo", help="modelo de Ollama para --origen")
    parser.add_argument("--json", type=Path, help="volcar el reporte a un archivo JSON")
    args = parser.parse_args()

    print(
        "T-404 · Combinación de evidencia con resolución por campo (F4/ADR-002)\n"
        "    conserva TODAS las lecturas y resuelve el desacuerdo campo a campo\n"
        f"    versión de la combinación: {VERSION_COMBINACION}"
    )
    _imprimir_tabla()

    if args.origen:
        _correr_origen(args.origen, args.modelo, args.detalle)
        return
    if args.manual:
        _correr_manual(args.detalle)
        return
    if args.tabla:
        return

    reporte: dict[str, Any] = {}
    fallos = 0

    escenarios = ESCENARIOS
    if args.caso:
        escenarios = [e for e in ESCENARIOS if e["nombre"] == args.caso]
        if not escenarios:
            print(f"\nNo existe el escenario {args.caso!r}.", file=sys.stderr)
            sys.exit(2)

    print("\nEscenarios de resolución")
    reporte["escenarios"] = []
    for escenario in escenarios:
        corrida = _correr_escenario(escenario)
        diferencias = _verificar(corrida, escenario["espera"])
        marca = "✅" if not diferencias else "❌"
        print(f"\n  {marca} {escenario['nombre']:<28} {escenario['que']}")
        if corrida["combinada"] is not None:
            _imprimir_resultado(corrida, args.detalle)
        for diferencia in diferencias:
            print(f"       ❌ {diferencia}")
            fallos += 1
        reporte["escenarios"].append(
            {"nombre": escenario["nombre"], "ok": not diferencias, "diferencias": diferencias}
        )

    print("\nFronteras de T-404 (lo que NO hace)")
    reporte["fronteras"] = []
    for frontera in _verificar_fronteras():
        marca = "✅" if frontera["ok"] else "❌"
        print(f"  {marca} {frontera['que']}")
        if not frontera["ok"]:
            fallos += 1
        reporte["fronteras"].append(frontera)

    ok = sum(1 for e in reporte["escenarios"] if e["ok"])
    print(f"\nEscenarios verificados: {ok}/{len(escenarios)}")
    reporte["fallos"] = fallos
    if args.json:
        args.json.write_text(json.dumps(reporte, ensure_ascii=False, indent=2), encoding="utf-8")
        print(f"Reporte guardado en {args.json}")
    sys.exit(1 if fallos else 0)


if __name__ == "__main__":
    main()

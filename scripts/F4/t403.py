#!/usr/bin/env python
"""Inspecciona T-403 (F4) — reglas raw por fuente de la extracción (pasada 1).

**Fase**: F4 (extracción) · **Tarea**: T-403 · **Épica**: E-EXT-2.

Muestra, **sin Ollama ni Docling** (los lectores se inyectan como dobles):

  1. Los **criterios de sostén** que T-401 dejaba sin evaluar y T-403 cubre: un
     monto impreso como ``"$ 12.345,67"`` sosteniendo el valor ``12345.67``, un
     ajuste ``"(1.234,56)"`` sosteniendo ``-1234.56``, una fecha ``"14/08/2025"``
     sosteniendo ``2025-08-14`` y un CUIT ``"30/12345678/9"`` sosteniendo
     ``30-12345678-9``.
  2. Los **escenarios de coherencia** (el corazón de E-EXT-2): la fuente que dice
     ``A`` sin los dos CUIT, la que dice ``B`` con IVA discriminado, y las
     contrapartes coherentes que **no** deben marcarse.
  3. Las **fronteras** de la tarea: la pasada raw califica pero no reescribe
     valores; la descripción sigue sin evaluarse; una implicación que no se puede
     juzgar no se reporta; el registro de T-303 y el esqueleto de T-404 quedan
     intactos.

Con ``--origen`` corre la extracción **real** contra Ollama y muestra, por fuente,
el veredicto raw (marcando en cuáles de las dos fuentes la evidencia queda
debilitada antes de combinarse).

Uso:
    python scripts/F4/t403.py                       # sostén + escenarios + fronteras
    python scripts/F4/t403.py --sosten              # solo los criterios de sostén
    python scripts/F4/t403.py --caso b_con_iva      # un solo escenario
    python scripts/F4/t403.py --json /tmp/t403.json
    python scripts/F4/t403.py --origen files/2025-08/2D2C9343/<doc>.jpg --detalle

Nota: la suite default de pytest cubre lo mismo en
``tests/test_extraction_raw_t403.py``; este script es la verificación de humo
legible para la bitácora (sale con código ≠ 0 si algún escenario falla).
"""

from __future__ import annotations

import argparse
import json
import sys
import tempfile
from pathlib import Path
from typing import Any

# El paquete usa layout src/; si se corre sin instalar, se agrega src/ al PATH.
_SRC = Path(__file__).resolve().parents[2] / "src"
if str(_SRC) not in sys.path:
    sys.path.insert(0, str(_SRC))

from voucherflow.extraction import (  # noqa: E402
    CAMPO_FUENTE_LECTURA,
    CLAVE_CAMPOS,
    COHERENCIA_POR_CAMPO,
    CampoLectura,
    campo_declarado_de_campo,
    extraer_evidencia,
    parsear_evidencia_extraccion,
    veredicto_raw_de_evidencia,
)
from voucherflow.rules.raw import ID_RAW_COHERENCIA  # noqa: E402
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


class LectorDoble:
    """Doble del ``OllamaClient``: responde el JSON configurado, sin red."""

    def __init__(self, contenido: str = "{}") -> None:
        self.contenido = contenido

    def ask(self, messages, model, json_format=False, options=None, num_ctx=None):
        class _R:
            contenido = self.contenido

        return _R()


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
        nota="Vista fiel sintética (T-403)",
    )


def _campo(valor: Any, sustento: str) -> dict[str, Any]:
    return {"valor": valor, "fragmento_sustento": sustento}


def _evidencia(campos: dict[str, Any], fuente: str = "llm"):
    """Lectura cruda (la de T-401) desde el JSON del contrato."""
    crudo = json.dumps(
        {CAMPO_FUENTE_LECTURA: fuente, CLAVE_CAMPOS: campos}, ensure_ascii=False
    )
    return parsear_evidencia_extraccion(crudo, fuente=fuente)


def _settings() -> Any:
    return cargar_desde_dict(
        {
            "modelos": {
                "vlm": {"rol": "vlm", "modelo": "vlm-doble", "num_ctx": 4096},
                "llm": {"rol": "llm", "modelo": "llm-doble", "num_ctx": 8192},
            }
        }
    )


def _factura_a(**extra: Any) -> dict[str, Any]:
    """Campos de una Factura A coherente (los dos CUIT + IVA discriminado)."""
    campos = {
        "tipo_comprobante": _campo("A", "Recuadro 'A' COD. 01"),
        "razon_social_emisor": _campo("ACME S.A.", "ACME S.A."),
        "cuit_emisor": _campo("30-12345678-9", "C.U.I.T. 30-12345678-9"),
        "cuit_receptor": _campo("27-30111222-4", "C.U.I.T. 27-30111222-4"),
        "fecha_emision": _campo("14/08/2025", "Fecha de Emisión: 14/08/2025"),
        "importe_total_facturado": _campo("$ 12.345,67", "Importe Total: $ 12.345,67"),
        "subtotal": _campo("10.203,03", "Subtotal: $ 10.203,03"),
        "iva": _campo("2.142,64", "IVA 21%: $ 2.142,64"),
    }
    campos.update(extra)
    return campos


# ---------------------------------------------------------------------------
# 1. Criterios de sostén (lo que T-401 dejaba sin evaluar)
# ---------------------------------------------------------------------------

#: ``(campo, valor declarado, fragmento, ¿debe sostenerse?)``.
_SOSTEN: list[tuple[str, str, str, bool]] = [
    # Montos: el OCR decide separadores; el sostén compara importes.
    ("importe_total_facturado", "$ 12.345,67", "Importe Total: $ 12.345,67", True),
    ("importe_total_facturado", "$ 12.345,67", "Importe Total: $ 99.999,99", False),
    ("otros_impuestos", "-1.234,56", "Ajuste: (1.234,56)", True),
    ("otros_impuestos", "1.234,56", "Ajuste: 1.234,56-", False),
    # Fechas: se comparan ya normalizadas a ISO.
    ("fecha_emision", "2025-08-14", "Fecha de Emisión: 14/08/2025", True),
    ("fecha_emision", "2025-08-31", "Período: 01/08/2025 al 31/08/2025", True),
    ("fecha_emision", "2020-01-01", "Fecha: 14/08/2025", False),
    # Identificadores: los separadores del OCR no importan.
    ("cuit_emisor", "30-12345678-9", "C.U.I.T. 30123456789", True),
    ("cuit_emisor", "30123456789", "C.U.I.T. 30-12345678-9", True),
    ("cuit_emisor", "30-12345678-9", "CUIT: 30/12345678/9", True),
    ("cuit_emisor", "30-12345678-9", "C.U.I.T. 20-12345678-9", False),
]


def _correr_sosten() -> list[dict[str, Any]]:
    salida: list[dict[str, Any]] = []
    for campo, declarado, fragmento, esperado in _SOSTEN:
        ev = _evidencia({campo: _campo(declarado, fragmento)})
        veredicto = veredicto_raw_de_evidencia(ev)
        sostenido = not any(campo in d for d in veredicto.debilidades)
        salida.append(
            {
                "campo": campo,
                "declarado": declarado,
                "fragmento": fragmento,
                "sostenido": sostenido,
                "esperado": esperado,
                "ok": sostenido == esperado,
                "debilidades": list(veredicto.debilidades),
            }
        )
    return salida


def _imprimir_sosten(reportes: list[dict[str, Any]], detalle: bool) -> int:
    print("\nCriterios de sostén (T-403 completa lo que T-401 dejaba sin evaluar)")
    fallos = 0
    for r in reportes:
        marca = "✅" if r["ok"] else "❌"
        estado = "sostenido" if r["sostenido"] else "NO sostenido"
        print(
            f"  {marca} {r['campo']:<24} {r['declarado']!r:<16} ← {r['fragmento']!r:<44}"
            f" → {estado}"
        )
        if not r["ok"]:
            fallos += 1
            print(f"       esperado: {'sostenido' if r['esperado'] else 'NO sostenido'}")
        if detalle:
            for d in r["debilidades"]:
                print(f"       · {d[:110]}")
    return fallos


# ---------------------------------------------------------------------------
# 2. Escenarios de coherencia (E-EXT-2)
# ---------------------------------------------------------------------------

ESCENARIOS: list[dict[str, Any]] = [
    {
        "nombre": "a_sin_cuit_receptor",
        "que": 'dice "A" pero no leyó el CUIT del receptor: queda debilitada (E-EXT-2)',
        "campos": {
            "tipo_comprobante": _campo("A", "Recuadro 'A' COD. 01"),
            "cuit_emisor": _campo("30-12345678-9", "C.U.I.T. 30-12345678-9"),
        },
        "espera_incoherencia": True,
        "debilidades_contienen": "cuit_receptor",
    },
    {
        "nombre": "a_completa",
        "que": "una Factura A con los dos CUIT y el IVA discriminado es coherente",
        "campos": _factura_a(),
        "espera_incoherencia": False,
        "debilidades_contienen": None,
    },
    {
        "nombre": "b_con_iva",
        "que": 'dice "B" (no discrimina) pero declaró IVA discrimado: incoherente',
        "campos": {
            "tipo_comprobante": _campo("B", "Recuadro 'B' COD. 06"),
            "iva": _campo("2.100,50", "IVA 21%: 2.100,50"),
        },
        "espera_incoherencia": True,
        "debilidades_contienen": "iva",
    },
    {
        "nombre": "b_con_iva_en_cero",
        "que": 'una "B" con IVA en cero es coherente (no discrimina, como manda la regla)',
        "campos": {
            "tipo_comprobante": _campo("B", "Recuadro 'B' COD. 06"),
            "iva": _campo("0,00", "IVA: 0,00"),
        },
        "espera_incoherencia": False,
        "debilidades_contienen": None,
    },
    {
        "nombre": "b_sin_iva",
        "que": 'una "B" que no declaró el IVA no se juzga (la ausencia no se castiga)',
        "campos": {
            "tipo_comprobante": _campo("B", "Recuadro 'B' COD. 06"),
            "importe_total_facturado": _campo("1.210,00", "Total: 1.210,00"),
        },
        "espera_incoherencia": False,
        "debilidades_contienen": None,
    },
    {
        "nombre": "c_sin_implicaciones",
        "que": 'una "C" sola no dispara incoherencias (no tiene implicaciones declaradas)',
        "campos": {"tipo_comprobante": _campo("C", "Recuadro 'C' COD. 011")},
        "espera_incoherencia": False,
        "debilidades_contienen": None,
    },
    {
        "nombre": "a_con_monto_no_sostenido",
        "que": "coherencia y sostén conviven: la fuente acumula las dos debilidades",
        "campos": {
            "tipo_comprobante": _campo("A", "Recuadro 'A' COD. 01"),
            "importe_total_facturado": _campo("$ 1,00", "Total: $ 9,99"),
        },
        "espera_incoherencia": True,
        "debilidades_contienen": "cuit_emisor",
        "tambien_regla": "RAW_SUSTENTO",
    },
]


def _correr_escenario(escenario: dict[str, Any]) -> dict[str, Any]:
    ev = _evidencia(escenario["campos"])
    return {"evidencia": ev, "veredicto": veredicto_raw_de_evidencia(ev), "error": None}


def _verificar(corrida: dict[str, Any], expectativa: dict[str, Any]) -> list[str]:
    veredicto = corrida["veredicto"]
    problemas: list[str] = []
    tiene = ID_RAW_COHERENCIA in veredicto.reglas_aplicadas
    if tiene != expectativa["espera_incoherencia"]:
        problemas.append(
            f"incoherencia={'sí' if tiene else 'no'} "
            f"(esperado {'sí' if expectativa['espera_incoherencia'] else 'no'})"
        )
    fragmento = expectativa.get("debilidades_contienen")
    if fragmento and not any(fragmento in d for d in veredicto.debilidades):
        problemas.append(f"las debilidades no mencionan {fragmento!r}")
    regla = expectativa.get("tambien_regla")
    if regla and regla not in veredicto.reglas_aplicadas:
        problemas.append(f"no disparó {regla}")
    return problemas


def _imprimir_resultado(corrida: dict[str, Any], detalle: bool) -> None:
    veredicto = corrida["veredicto"]
    print(
        f"    · valida={veredicto.valida} · gravedad={veredicto.gravedad.value} · "
        f"reglas={veredicto.reglas_aplicadas or '[]'}"
    )
    for debilidad in veredicto.debilidades:
        print(f"    ⚠️  {debilidad[:150]}")
    if detalle:
        for nombre, campo in corrida["evidencia"].campos.items():
            declarado = campo_declarado_de_campo(campo)
            criterio = (
                "forma canónica"
                if declarado is not None and declarado.sostenedor is not None
                else ("contención" if declarado is not None else "sin evaluar")
            )
            print(f"      {nombre}: {campo.valor!r} ({criterio})")


# ---------------------------------------------------------------------------
# 3. Fronteras de la tarea
# ---------------------------------------------------------------------------


def _verificar_fronteras() -> list[dict[str, Any]]:
    # (a) la pasada raw no reescribe valores
    ev = _evidencia(_factura_a())
    veredicto = veredicto_raw_de_evidencia(ev)
    # (b) la descripción sigue sin evaluarse
    descripcion = campo_declarado_de_campo(
        CampoLectura(
            campo="descripcion", valor="compra de insumos", fragmento="Ítems varios"
        )
    )
    # (c) el registro de T-303 sigue con sus cuatro reglas
    from voucherflow.rules.raw import REGISTRO_RAW

    checks = [
        (
            "la pasada raw califica sin reescribir los valores publicados",
            veredicto.valida is True
            and ev.campos["importe_total_facturado"].valor == "$ 12.345,67",
        ),
        (
            "la descripcion sigue sin evaluarse (frase sintética)",
            descripcion is None,
        ),
        (
            "las cuatro reglas raw de T-303 siguen intactas",
            [r.id for r in REGISTRO_RAW.reglas]
            == ["RAW_CAMPO", "RAW_VOCABULARIO", "RAW_SUSTENTO", "RAW_CONTRADICCION"],
        ),
        (
            "la coherencia se declara solo por campo (hoy la letra)",
            set(COHERENCIA_POR_CAMPO) == {"tipo_comprobante"},
        ),
    ]
    return [{"que": que, "ok": ok} for que, ok in checks]


# ---------------------------------------------------------------------------
# 4. Corrida real (--origen)
# ---------------------------------------------------------------------------


def _correr_origen(origen: Path, modelo: str | None, detalle: bool) -> None:
    from voucherflow import api
    from voucherflow.models.ollama import OllamaClient
    from voucherflow.validation.vistas import preparar_vista_fiel

    documento = api.process(str(origen))
    vista = preparar_vista_fiel(documento, str(origen))
    resultado = extraer_evidencia(
        OllamaClient(),
        markdown=documento.markdown,
        vista=vista,
        documento_id=str(origen),
        modelo=modelo,
    )
    print(f"\nOrigen: {origen}")
    print(f"  fuentes corridas: {resultado.detalle['fuentes_corridas']}")
    for fuente, res in resultado.resultados.items():
        veredicto = res.veredicto
        print(
            f"\n  [{fuente}] valida={veredicto.valida} gravedad={veredicto.gravedad.value} "
            f"reglas={veredicto.reglas_aplicadas or '[]'}"
        )
        print(f"    sostén por forma canónica: "
              f"{resultado.detalle['modelos'][fuente]['sosten_forma_canonica']}")
        print(f"    sin evaluar: {resultado.detalle['modelos'][fuente]['sosten_no_evaluado']}")
        for debilidad in veredicto.debilidades:
            print(f"    ⚠️  {debilidad[:150]}")
        if detalle:
            for nombre, campo in res.evidencia.campos.items():
                print(f"      {nombre}: {campo.valor!r}")


# ---------------------------------------------------------------------------
# main
# ---------------------------------------------------------------------------


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Inspecciona las reglas raw por fuente de F4/T-403 (sin Ollama)"
    )
    parser.add_argument("--sosten", action="store_true", help="solo los criterios de sostén")
    parser.add_argument("--caso", help="corre un solo escenario por nombre")
    parser.add_argument("--detalle", action="store_true", help="campos y criterios por escenario")
    parser.add_argument("--origen", type=Path, help="documento real (corre Ollama)")
    parser.add_argument("--modelo", help="modelo de Ollama para --origen")
    parser.add_argument("--json", type=Path, help="volcar el reporte a un archivo JSON")
    args = parser.parse_args()

    print(
        "T-403 · Reglas raw por fuente de la extracción (F4/E-EXT-2)\n"
        "    pasada 1 (T-303) + sostén por forma canónica + coherencia de la fuente\n"
        "    objetivo: marcar una fuente internamente inconsistente ANTES de combinarla"
    )

    if args.origen:
        _correr_origen(args.origen, args.modelo, args.detalle)
        return

    reporte: dict[str, Any] = {}
    fallos = 0

    reportes_sosten = _correr_sosten()
    fallos += _imprimir_sosten(reportes_sosten, args.detalle)
    reporte["sosten"] = reportes_sosten
    if args.sosten:
        print(f"\nSostén: {len(reportes_sosten) - fallos}/{len(reportes_sosten)} casos OK.")
        if args.json:
            args.json.write_text(json.dumps(reporte, ensure_ascii=False, indent=2), encoding="utf-8")
        sys.exit(1 if fallos else 0)

    escenarios = ESCENARIOS
    if args.caso:
        escenarios = [e for e in ESCENARIOS if e["nombre"] == args.caso]
        if not escenarios:
            print(f"\nNo existe el escenario {args.caso!r}.", file=sys.stderr)
            sys.exit(2)
    print("\nEscenarios de coherencia (E-EXT-2)")
    reporte["escenarios"] = []
    for escenario in escenarios:
        corrida = _correr_escenario(escenario)
        diferencias = _verificar(corrida, escenario)
        marca = "✅" if not diferencias else "❌"
        print(f"\n  {marca} {escenario['nombre']:<28} {escenario['que']}")
        _imprimir_resultado(corrida, args.detalle)
        for diferencia in diferencias:
            print(f"       ❌ {diferencia}")
            fallos += 1
        reporte["escenarios"].append(
            {"nombre": escenario["nombre"], "ok": not diferencias, "diferencias": diferencias}
        )

    print("\nFronteras de T-403 (lo que NO hace)")
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

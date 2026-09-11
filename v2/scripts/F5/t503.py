#!/usr/bin/env python
"""Inspecciona T-503 (F5) — consolidación "certeza alta por programa".

**Fase**: F5 (conclusión) · **Tarea**: T-503 · **Épica**: E-CONC-1.

Muestra, **sin Ollama, sin Docling y sin red**:

  1. La **tabla de la regla de la certeza**: qué combinación de
     (concluye, alertas, letra) da certeza alta + origen programa, y por qué.
  2. Los **escenarios de consolidación**: el caso aprobado por programa, el
     **rechazo firme** (que también es certeza alta: "no es válido" es una
     conclusión), el conflicto R7 sin resolver (baja), el gap sin cubrir (baja,
     sin origen), el tique fuera del vocabulario y el caso donde el padrón cubre
     el gap y **desbloquea** la certeza alta.
  3. Las **fronteras** de la tarea: la certeza no se declara (se deriva), un caso
     ambiguo no se consolida como final (sale sin `origen`), no se inventa la
     clasificación contable, y no se llama al agente (T-504) ni se encola HITL
     (T-505).

Uso:
    python scripts/F5/t503.py                       # tabla + escenarios + fronteras
    python scripts/F5/t503.py --tabla               # solo la tabla de la regla
    python scripts/F5/t503.py --caso rechazo_firme
    python scripts/F5/t503.py --manual              # el caso ambiguo, paso a paso
    python scripts/F5/t503.py --json /tmp/t503.json

Nota: la suite default de pytest cubre lo mismo en
``tests/test_conclusion_consolidacion_t503.py``; este script es la verificación
de humo legible para la bitácora (sale con código ≠ 0 si algún escenario falla).
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

# El paquete usa layout src/; si se corre sin instalar, se agrega src/ al sys.path.
_SRC = Path(__file__).resolve().parents[2] / "src"
if str(_SRC) not in sys.path:
    sys.path.insert(0, str(_SRC))

from voucherflow.conclusion import concluir_con_busqueda, consolidar_caso  # noqa: E402
from voucherflow.conclusion.consolidacion import (  # noqa: E402
    VERSION_CONSOLIDACION,
    es_certeza_alta_por_programa,
)
from voucherflow.conclusion.engine import encolar_hitl, escalar_a_agente  # noqa: E402
from voucherflow.extraction.flows import combinar_evidencia  # noqa: E402
from voucherflow.rules.contexto import ContextoTipoComprobante  # noqa: E402
from voucherflow.rules.cruzadas import (  # noqa: E402
    ESTADO_APROBADO,
    ESTADO_RECHAZADO,
    ESTADO_REVISION,
    ConclusionResult,
)
from voucherflow.rules.gaps import ResultadoBusqueda  # noqa: E402
from voucherflow.schemas.evidence import (  # noqa: E402
    Certeza,
    EvidenceField,
    Fuente,
    Origen,
    SourceEvidence,
)

# ---------------------------------------------------------------------------
# Datos de los escenarios (sin red)
# ---------------------------------------------------------------------------

BASE: dict[str, Any] = {
    "fecha_emision": "2025-08-14",
    "nro_comprobante": "0001-00000001",
    "moneda": "ARS",
    "importe_total_facturado": "121.00",
}

FACTURA_A: dict[str, Any] = {
    **BASE,
    "tipo_comprobante": "A",
    "razon_social_emisor": "ACME SA",
    "cuit_emisor": "30-12345678-9",
    "razon_social_receptor": "Cliente SA",
    "cuit_receptor": "27-30111222-4",
    "subtotal": "100.00",
    "iva": "21.00",
}

FACTURA_B_INVALIDA: dict[str, Any] = {
    **BASE,
    "tipo_comprobante": "B",
    "cuit_emisor": "30-12345678-9",
    "subtotal": "100.00",
    "iva": "21.00",
}

FACTURA_B_CONFLICTO: dict[str, Any] = {
    **BASE,
    "tipo_comprobante": "B",
    "cuit_emisor": "30-12345678-9",
    "iva": "0.00",
}

CTX_RI_RI = ContextoTipoComprobante(
    emisor_condicion_fiscal="Responsable Inscripto",
    receptor_condicion_fiscal="Responsable Inscripto",
)
CTX_RI_CF = ContextoTipoComprobante(
    emisor_condicion_fiscal="Responsable Inscripto",
    receptor_condicion_fiscal="Consumidor Final",
)


class BuscadorDoble:
    """Buscador de prueba: responde por campo."""

    def __init__(self, respuestas: dict[str, list[ResultadoBusqueda]] | None = None):
        self.respuestas = respuestas or {}
        self.consultas: list[str] = []

    def buscar(self, gap: Any, contexto: Any) -> ResultadoBusqueda:
        self.consultas.append(gap.campo)
        secuencia = self.respuestas.get(gap.campo)
        if not secuencia:
            return ResultadoBusqueda(campo=gap.campo, disponible=True, valor=None)
        if len(secuencia) == 1:
            return secuencia[0]
        return secuencia.pop(0)


def _escenarios() -> list[dict[str, Any]]:
    """Escenarios de consolidación: ``(nombre, campos, contexto, qué espera)``."""
    sin_importe = {k: v for k, v in FACTURA_A.items() if k != "importe_total_facturado"}
    return [
        {
            "nombre": "aprobado_por_programa",
            "que": "las reglas concluyen consistentes → certeza alta, origen programa",
            "campos": FACTURA_A,
            "contexto": CTX_RI_RI,
            "espera": {"estado": ESTADO_APROBADO, "certeza": "alta", "origen": "programa",
                       "tipo": "A", "programa": True},
        },
        {
            "nombre": "rechazo_firme",
            "que": "rechazo por contradicción: 'no es válido' es una conclusión",
            "campos": FACTURA_B_INVALIDA,
            "contexto": CTX_RI_CF,
            "espera": {"estado": ESTADO_RECHAZADO, "certeza": "alta", "origen": "programa",
                       "tipo": "B", "programa": True},
        },
        {
            "nombre": "conflicto_r7_sin_resolver",
            "que": "el conflicto quedó abierto → NO es consistente: certeza baja",
            "campos": FACTURA_B_CONFLICTO,
            "contexto": CTX_RI_RI,
            "espera": {"estado": ESTADO_REVISION, "certeza": "baja", "origen": None,
                       "tipo": "B", "programa": False, "hitl": "alta"},
        },
        {
            "nombre": "gap_sin_cubrir",
            "que": "faltan datos y no hay hook: revisión, sin origen (no decidió nadie)",
            "campos": {"razon_social_emisor": "ACME"},
            "contexto": None,
            "espera": {"estado": ESTADO_REVISION, "certeza": "baja", "origen": None,
                       "tipo": None, "programa": False, "hitl": "alta"},
        },
        {
            "nombre": "tique_fuera_de_vocabulario",
            "que": "un 090 no es letra del motor (D-13): no se consolida como alta",
            "campos": {**BASE, "tipo_comprobante": "090"},
            "contexto": None,
            "espera": {"estado": ESTADO_REVISION, "certeza": "baja", "origen": None,
                       "tipo": None, "programa": False},
        },
        {
            "nombre": "gap_cubierto_por_el_padron",
            "que": "el padrón cubre el crítico faltante → desbloquea la certeza alta",
            "campos": sin_importe,
            "contexto": CTX_RI_RI,
            "buscador": BuscadorDoble({
                "importe_total_facturado": [
                    ResultadoBusqueda(
                        campo="importe_total_facturado",
                        valor="121.00",
                        disponible=True,
                        sostento="padrón WSCDC: total constatado",
                    )
                ]
            }),
            "espera": {"estado": ESTADO_APROBADO, "certeza": "alta", "origen": "programa",
                       "tipo": "A", "programa": True},
        },
    ]


# ---------------------------------------------------------------------------
# Construcción y verificación
# ---------------------------------------------------------------------------


def _source(fuente: Fuente, campos: dict[str, Any]) -> SourceEvidence:
    return SourceEvidence(
        fuente=fuente,
        campos={
            nombre: EvidenceField(
                campo=nombre,
                valor=valor,
                fuente=fuente,
                fragmento_sustento=f"soporte de {nombre}",
            )
            for nombre, valor in campos.items()
        },
    )


def _evidencia(campos: dict[str, Any]) -> Any:
    return combinar_evidencia(
        "doc-t503", [_source(Fuente.vlm, campos), _source(Fuente.llm, campos)]
    )


def _correr(escenario: dict[str, Any]) -> Any:
    return concluir_con_busqueda(
        _evidencia(escenario["campos"]),
        contexto_tipo=escenario["contexto"],
        buscador=escenario.get("buscador"),
        consolidar_resultado=True,
    )


def _verificar(corrida: Any, espera: dict[str, Any]) -> list[str]:
    resultado = corrida.resultado
    diferencias: list[str] = []

    if resultado is None:
        return ["no se consolidó el VoucherResult"]

    obtenido = {
        "estado": resultado.estado.value,
        "certeza": resultado.certeza.value if resultado.certeza else None,
        "origen": resultado.origen.value if resultado.origen else None,
        "tipo": resultado.tipo_comprobante,
        "programa": corrida.consolidacion.concluyo_por_programa,
    }
    for clave, valor in obtenido.items():
        if clave in espera and espera[clave] != valor:
            diferencias.append(f"{clave}: esperado {espera[clave]!r}, obtenido {valor!r}")

    if "hitl" in espera and resultado.hitl.prioridad != espera["hitl"]:
        diferencias.append(
            f"hitl: esperado {espera['hitl']!r}, obtenido {resultado.hitl.prioridad!r}"
        )

    return diferencias


# ---------------------------------------------------------------------------
# Impresión
# ---------------------------------------------------------------------------


def _imprimir_tabla() -> None:
    print("\nTabla de la regla de la certeza (Gherkin E-CONC-1)")
    print(f"  version: {VERSION_CONSOLIDACION}")
    print(
        "\n  certeza=alta + origen=programa  ⇔  concluye ∧ sin alertas ∧ con letra"
    )
    print(f"  {'concluye':<9} {'alertas':<9} {'letra':<7} {'→ certeza':<9} por qué")
    print(f"  {'-'*9} {'-'*9} {'-'*7} {'-'*9} {'-'*58}")

    casos = [
        (True, [], "A", "el código concluyó de forma consistente"),
        (True, [], "B", "un rechazo firme también es una conclusión"),
        (True, [{"regla": "CRUZ_5"}], "B", "el conflicto quedó sin resolver"),
        (False, [], "B", "no concluyó: lo tomarán T-504/T-505"),
        (True, [], None, "sin letra, la aprobación sería vacía"),
    ]
    for concluye, alertas, letra, porque in casos:
        conclusion = ConclusionResult(
            concluye=concluye,
            certeza="alta" if concluye else None,
            origen="programa" if concluye else None,
            estado=ESTADO_APROBADO if concluye else ESTADO_REVISION,
            alertas=list(alertas),
        )
        es_alta, _ = es_certeza_alta_por_programa(conclusion, tipo_comprobante=letra)
        marca = "alta" if es_alta else "baja"
        print(
            f"  {str(concluye):<9} {str(bool(alertas)):<9} {str(letra or '—'):<7} "
            f"{marca:<9} {porque}"
        )

    print(
        "\n  La certeza mide cuánto sabe el sistema, no si le gustó el resultado:\n"
        "  un rechazo firme es certeza alta. Una alerta ABIERTA no lo es."
    )


def _imprimir_corrida(corrida: Any, detalle: bool) -> None:
    resultado = corrida.resultado
    consolidacion = corrida.consolidacion
    print(
        f"       estado={resultado.estado.value:<9} tipo={str(resultado.tipo_comprobante):<5} "
        f"certeza={resultado.certeza.value if resultado.certeza else '—':<4} "
        f"origen={str(resultado.origen.value if resultado.origen else '—'):<8} "
        f"programa={consolidacion.concluyo_por_programa}"
    )
    print(
        f"       hitl={resultado.hitl.prioridad}/{resultado.hitl.estado} · "
        f"campos={len(resultado.campos_extraidos)} · "
        f"clasificación={'sí' if consolidacion.clasificacion_disponible else 'no'}"
    )
    print(f"       motivo: {consolidacion.motivo_certeza}")

    if detalle:
        traza = resultado.trazabilidad["consolidacion"]
        print(
            f"       reglas={traza['reglas_aplicadas']} · "
            f"alertas={len(traza['alertas'])} · "
            f"condición impositiva={traza['condicion_impositiva']}"
        )
        if corrida.gaps_restantes:
            print(f"       gaps restantes: {corrida.gaps_restantes[:4]}")


def _verificar_fronteras() -> list[dict[str, Any]]:
    """Verifica que T-503 respete sus límites (lo que NO hace)."""
    fronteras: list[dict[str, Any]] = []

    # 1. La certeza no se declara: se deriva (no es parámetro).
    import inspect

    from voucherflow.conclusion.consolidacion import consolidar

    firma = inspect.signature(consolidar)
    fronteras.append(
        {
            "que": "la certeza se deriva, no se declara (no es un parámetro)",
            "ok": "certeza" not in firma.parameters and "origen" not in firma.parameters,
        }
    )

    # 2. Un caso ambiguo no se consolida como final (sin origen).
    ambiguo = consolidar_caso(_evidencia({"razon_social_emisor": "ACME"}))
    fronteras.append(
        {
            "que": "un caso ambiguo sale sin origen (no lo decidió nadie)",
            "ok": ambiguo.valor.origen is None
            and ambiguo.valor.estado.value == ESTADO_REVISION
            and not ambiguo.concluyo_por_programa,
        }
    )

    # 3. No se inventa la clasificación contable.
    sin_clasificacion = consolidar_caso(_evidencia(FACTURA_A), contexto_tipo=CTX_RI_RI)
    fronteras.append(
        {
            "que": "sin la cadena contable, el resultado viaja sin clasificación",
            "ok": sin_clasificacion.valor.clasificacion_contable is None
            and not sin_clasificacion.clasificacion_disponible,
        }
    )

    # 4. No muta la evidencia de entrada.
    original = _evidencia(FACTURA_A)
    antes = dict(original.trazabilidad)
    consolidar_caso(original, contexto_tipo=CTX_RI_RI)
    fronteras.append(
        {
            "que": "no muta la evidencia de entrada",
            "ok": dict(original.trazabilidad) == antes,
        }
    )

    # 5. El estado del VoucherResult es el del veredicto (no se re-calcula).
    consistente = consolidar_caso(_evidencia(FACTURA_B_INVALIDA), contexto_tipo=CTX_RI_CF)
    fronteras.append(
        {
            "que": "el estado del resultado es el del veredicto de la pasada 2",
            "ok": consistente.valor.estado.value
            == consistente.valor.trazabilidad["conclusion"]["estado"],
        }
    )

    # 6. La consolidación no encola HITL por su cuenta: el encolado es T-505, un
    #    paso explícito. (El agente (T-504) sí existe, pero tampoco se invoca
    #    desde acá: la consolidación consolida el veredicto que recibe.)
    consolidado_t505 = consolidar_caso(_evidencia(FACTURA_A), contexto_tipo=CTX_RI_RI)
    fronteras.append(
        {
            "que": "la consolidación no encola HITL (el encolado es un paso aparte, T-505)",
            "ok": "hitl" not in consolidado_t505.valor.trazabilidad,
        }
    )

    # 7. Es determinista.
    a = consolidar_caso(_evidencia(FACTURA_A), contexto_tipo=CTX_RI_RI).como_dict()
    b = consolidar_caso(_evidencia(FACTURA_A), contexto_tipo=CTX_RI_RI).como_dict()
    fronteras.append({"que": "es determinista (mismo caso, mismo resultado)", "ok": a == b})

    # 8. La traza conserva todas las etapas (combinación → conclusión → consolidación).
    evidencia = _evidencia(FACTURA_A)
    resultado = concluir_con_busqueda(
        evidencia, contexto_tipo=CTX_RI_RI, consolidar_resultado=True
    )
    traza = resultado.resultado.trazabilidad
    fronteras.append(
        {
            "que": "la traza del resultado conserva combinación, conclusión y consolidación",
            "ok": all(clave in traza for clave in ("combinacion", "conclusion", "consolidacion")),
        }
    )

    return fronteras


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def _parsear_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Inspecciona T-503: consolidación del VoucherResult.",
    )
    parser.add_argument("--tabla", action="store_true", help="solo la tabla de la regla")
    parser.add_argument("--caso", help="correr un solo escenario (por nombre)")
    parser.add_argument("--manual", action="store_true", help="el caso ambiguo, paso a paso")
    parser.add_argument("--json", type=Path, help="guardar el reporte en JSON")
    return parser.parse_args()


def main() -> None:
    args = _parsear_args()
    reporte: dict[str, Any] = {"version": VERSION_CONSOLIDACION}

    print("T-503 (F5) — consolidación «certeza alta por programa»")
    _imprimir_tabla()
    if args.tabla:
        sys.exit(0)

    if args.manual:
        print("\nCaso ambiguo: no lo decidió nadie (sin origen)")
        escenario = next(e for e in _escenarios() if e["nombre"] == "gap_sin_cubrir")
        _imprimir_corrida(_correr(escenario), detalle=True)
        print("\nCaso aprobado por programa: el Gherkin cumplido")
        escenario = next(e for e in _escenarios() if e["nombre"] == "aprobado_por_programa")
        _imprimir_corrida(_correr(escenario), detalle=True)
        sys.exit(0)

    escenarios = _escenarios()
    if args.caso:
        escenarios = [e for e in escenarios if e["nombre"] == args.caso]
        if not escenarios:
            print(f"\nNo existe el escenario {args.caso!r}.", file=sys.stderr)
            sys.exit(2)

    fallos = 0
    print("\nEscenarios de consolidación")
    reporte["escenarios"] = []
    for escenario in escenarios:
        corrida = _correr(escenario)
        diferencias = _verificar(corrida, escenario["espera"])
        marca = "✅" if not diferencias else "❌"
        print(f"\n  {marca} {escenario['nombre']:<30} {escenario['que']}")
        _imprimir_corrida(corrida, detalle=True)
        for diferencia in diferencias:
            print(f"       ❌ {diferencia}")
            fallos += 1
        reporte["escenarios"].append(
            {
                "nombre": escenario["nombre"],
                "ok": not diferencias,
                "diferencias": diferencias,
                "estado": corrida.resultado.estado.value,
                "certeza": corrida.resultado.certeza.value,
                "origen": (
                    corrida.resultado.origen.value if corrida.resultado.origen else None
                ),
            }
        )

    print("\nFronteras de T-503 (lo que NO hace)")
    reporte["fronteras"] = []
    for frontera in _verificar_fronteras():
        marca = "✅" if frontera["ok"] else "❌"
        print(f"  {marca} {frontera['que']}")
        if not frontera["ok"]:
            fallos += 1
        reporte["fronteras"].append(frontera)

    ok = sum(1 for e in reporte["escenarios"] if e["ok"])
    print(f"\nEscenarios verificados: {ok}/{len(escenarios)}")
    por_programa = sum(
        1 for e in reporte["escenarios"] if e.get("certeza") == "alta"
    )
    print(f"Consolidados con certeza alta por programa: {por_programa}/{len(escenarios)}")
    reporte["fallos"] = fallos
    if args.json:
        args.json.write_text(json.dumps(reporte, ensure_ascii=False, indent=2), encoding="utf-8")
        print(f"Reporte guardado en {args.json}")
    sys.exit(1 if fallos else 0)


if __name__ == "__main__":
    main()

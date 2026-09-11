#!/usr/bin/env python
"""Inspecciona T-501 (F5) — reglas cruzadas sobre la evidencia combinada.

**Fase**: F5 (conclusión) · **Tarea**: T-501 · **Épica**: E-CONC-1 · **ADR-002/006/008**.

Muestra, **sin Ollama ni Docling** (todo determinístico):

  1. La **tabla de reglas cruzadas**: la pasada 2 sobre la evidencia combinada,
     con las tres familias del diseño (negocio, fast-fail, conflicto R7), su
     prioridad y qué produce cada una.
  2. Los **escenarios de conclusión**: los cinco desenlaces posibles —aprobado por
     el negocio, rechazado por contradicción de la letra, rechazado por
     incoherencia de una Factura A, revisión por gap de campos críticos, revisión
     por conflicto R7 y revisión por un tique fuera del vocabulario— con el
     estado, la certeza y el origen que resultan.
  3. Las **fronteras** de la tarea: la pasada 2 **no** busca evidencia (T-502),
     **no** llama al agente (T-504) ni encola HITL (T-505); no muta la evidencia
     de entrada; y la certeza se **deriva** de la etapa (el contrato de F0 rechaza
     un ``Decision`` de ``programa`` con certeza baja).

Uso:
    python scripts/F5/t501.py                       # tabla + escenarios + fronteras
    python scripts/F5/t501.py --tabla               # solo la tabla de reglas
    python scripts/F5/t501.py --caso factura_a_completa
    python scripts/F5/t501.py --manual              # muestra el caso ambiguo sin Decision
    python scripts/F5/t501.py --json /tmp/t501.json

Nota: la suite default de pytest cubre lo mismo en
``tests/test_conclusion_cruzadas_t501.py``; este script es la verificación de humo
legible para la bitácora (sale con código ≠ 0 si algún escenario falla).
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

from voucherflow.conclusion import concluir, concluir_caso  # noqa: E402
from voucherflow.conclusion.engine import encolar_hitl, escalar_a_agente  # noqa: E402
from voucherflow.extraction.flows import combinar_evidencia  # noqa: E402
from voucherflow.rules.contexto import ContextoTipoComprobante  # noqa: E402
from voucherflow.rules.contexto_conclusion import (  # noqa: E402
    CAMPOS_CRITICOS,
    ContextoConclusion,
)  # noqa: E402
from voucherflow.rules.cruzadas import (  # noqa: E402
    ESTADO_APROBADO,
    ESTADO_RECHAZADO,
    ESTADO_REVISION,
    FAMILIA_POR_REGLA,
    REGISTRO_CRUZADAS,
    VERSION_CRUZADAS,
    evaluar_cruzadas,
)
from voucherflow.schemas.evidence import (  # noqa: E402
    EvidenceField,
    Fuente,
    SourceEvidence,
)

# ---------------------------------------------------------------------------
# Datos de los escenarios (mismo criterio que F3/F4: sin red)
# ---------------------------------------------------------------------------

#: Campos mínimos para que un caso sea "completo" (sin gaps).
BASE: dict[str, Any] = {
    "fecha_emision": "2025-08-14",
    "nro_comprobante": "0001-00000001",
    "moneda": "ARS",
    "importe_total_facturado": "121.00",
}

#: Contextos fiscales (los aporta F3/padrón; no son campos del contrato de extracción).
CTX_RI_RI = ContextoTipoComprobante(
    emisor_condicion_fiscal="Responsable Inscripto",
    receptor_condicion_fiscal="Responsable Inscripto",
)
CTX_RI_CF = ContextoTipoComprobante(
    emisor_condicion_fiscal="Responsable Inscripto",
    receptor_condicion_fiscal="Consumidor Final",
)
CTX_MONO_CF = ContextoTipoComprobante(
    emisor_condicion_fiscal="Monotributo",
    receptor_condicion_fiscal="Consumidor Final",
)

FACTURA_A_COMPLETA: dict[str, Any] = {
    **BASE,
    "tipo_comprobante": "A",
    "razon_social_emisor": "ACME SA",
    "cuit_emisor": "30-12345678-9",
    "razon_social_receptor": "Cliente SA",
    "cuit_receptor": "27-30111222-4",
    "subtotal": "100.00",
    "iva": "21.00",
}

#: Escenarios de conclusión: ``(nombre, campos, contexto fiscal, qué espera)``.
ESCENARIOS: list[dict[str, Any]] = [
    {
        "nombre": "factura_a_completa",
        "que": "el comprobante se sostiene solo: negocio + coherencia → aprobado",
        "campos": FACTURA_A_COMPLETA,
        "contexto_tipo": CTX_RI_RI,
        "espera": {
            "estado": ESTADO_APROBADO,
            "concluye": True,
            "certeza": "alta",
            "origen": "programa",
            "reglas": ["CRUZ_1"],
            "con_decision": True,
        },
    },
    {
        "nombre": "factura_a_sin_contexto_fiscal",
        "que": "sin padrón igual concluye: la coherencia del documento alcanza",
        "campos": FACTURA_A_COMPLETA,
        "contexto_tipo": None,
        "espera": {
            "estado": ESTADO_APROBADO,
            "concluye": True,
            "certeza": "alta",
            "origen": "programa",
            "reglas": ["CRUZ_1"],
            "con_decision": True,
        },
    },
    {
        "nombre": "factura_b_con_iva",
        "que": "fast-fail: una B no discrimina IVA → rechazado",
        "campos": {
            **BASE,
            "tipo_comprobante": "B",
            "razon_social_emisor": "ACME SA",
            "cuit_emisor": "30-12345678-9",
            "subtotal": "100.00",
            "iva": "21.00",
        },
        "contexto_tipo": CTX_RI_CF,
        "espera": {
            "estado": ESTADO_RECHAZADO,
            "concluye": True,
            "certeza": "alta",
            "origen": "programa",
            "reglas": ["CRUZ_3"],
            "con_decision": True,
        },
    },
    {
        "nombre": "factura_a_sin_cuit_receptor",
        "que": "fast-fail: la A exige los dos CUIT → rechazado",
        "campos": {**BASE, "tipo_comprobante": "A", "cuit_emisor": "30-12345678-9", "iva": "21.00"},
        "contexto_tipo": CTX_RI_RI,
        "espera": {
            "estado": ESTADO_RECHAZADO,
            "concluye": True,
            "certeza": "alta",
            "origen": "programa",
            "reglas": ["CRUZ_3"],
            "con_decision": True,
        },
    },
    {
        "nombre": "gap_de_campos_criticos",
        "que": "faltan críticos: el gap se detecta y se reporta (insumo T-502)",
        "campos": {"razon_social_emisor": "ACME"},
        "contexto_tipo": None,
        "espera": {
            "estado": ESTADO_REVISION,
            "concluye": False,
            "certeza": None,
            "origen": None,
            "reglas": ["CRUZ_4"],
            "con_decision": False,
        },
    },
    {
        "nombre": "conflicto_r7",
        "que": "R7: RI + RI con una B → sospecha, revisión (no rechazo)",
        "campos": {
            **BASE,
            "tipo_comprobante": "B",
            "razon_social_emisor": "ACME SA",
            "cuit_emisor": "30-12345678-9",
            "iva": "0.00",
        },
        "contexto_tipo": CTX_RI_RI,
        "espera": {
            "estado": ESTADO_REVISION,
            "concluye": False,
            "certeza": None,
            "origen": None,
            "reglas": ["CRUZ_5"],
            "con_decision": False,
        },
    },
    {
        "nombre": "negocio_espera_otra_letra",
        "que": "un Monotributo no emite A → discrepancia negocio/documento",
        "campos": FACTURA_A_COMPLETA,
        "contexto_tipo": CTX_MONO_CF,
        "espera": {
            "estado": ESTADO_REVISION,
            "concluye": False,
            "certeza": None,
            "origen": None,
            "reglas": ["CRUZ_5"],
            "con_decision": False,
        },
    },
    {
        "nombre": "tique_fuera_de_vocabulario",
        "que": "un 090 no es letra del motor: no se aprueba por descarte",
        "campos": {**BASE, "tipo_comprobante": "090"},
        "contexto_tipo": None,
        "espera": {
            "estado": ESTADO_REVISION,
            "concluye": False,
            "certeza": None,
            "origen": None,
            "reglas": ["CRUZ_2"],
            "con_decision": False,
        },
    },
]


# ---------------------------------------------------------------------------
# Construcción de la evidencia combinada
# ---------------------------------------------------------------------------


def _source(fuente: Fuente, campos: dict[str, Any]) -> SourceEvidence:
    """``SourceEvidence`` con los campos indicados (valores ya canónicos)."""
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
    """Evidencia combinada (F4/T-404) de las dos fuentes sobre el mismo caso."""
    return combinar_evidencia(
        "doc-t501", [_source(Fuente.vlm, campos), _source(Fuente.llm, campos)]
    )


def _correr(escenario: dict[str, Any]) -> dict[str, Any]:
    """Corre la pasada 2 sobre el escenario y devuelve el resultado observable."""
    evidencia = _evidencia(escenario["campos"])
    contexto = ContextoConclusion.desde_evidencia(
        evidencia, contexto_tipo=escenario["contexto_tipo"]
    )
    veredicto = evaluar_cruzadas(contexto)
    resultado = concluir_caso(evidencia, escenario["contexto_tipo"])
    con_decision = concluir(evidencia, escenario["contexto_tipo"])

    return {
        "contexto": contexto,
        "veredicto": veredicto,
        "resultado": resultado,
        "con_decision": con_decision,
    }


def _verificar(corrida: dict[str, Any], espera: dict[str, Any]) -> list[str]:
    """Compara el resultado con lo esperado y devuelve las diferencias."""
    resultado = corrida["resultado"]
    diferencias: list[str] = []

    for campo in ("estado", "concluye", "certeza", "origen"):
        obtenido = getattr(resultado, campo)
        esperado = espera[campo]
        if obtenido != esperado:
            diferencias.append(f"{campo}: esperado {esperado!r}, obtenido {obtenido!r}")

    if "reglas" in espera:
        obtenidas = sorted(corrida["veredicto"].disparadas)
        esperadas = sorted(espera["reglas"])
        if obtenidas != esperadas:
            diferencias.append(f"reglas: esperadas {esperadas}, obtenidas {obtenidas}")

    if "con_decision" in espera:
        tiene = corrida["con_decision"].decision is not None
        if tiene != espera["con_decision"]:
            diferencias.append(
                f"Decision adjunto: esperado {espera['con_decision']}, obtenido {tiene}"
            )

    return diferencias


# ---------------------------------------------------------------------------
# Impresión
# ---------------------------------------------------------------------------


def _imprimir_tabla() -> None:
    """Imprime la tabla de reglas cruzadas (pasada 2)."""
    print("\nTabla de reglas cruzadas (pasada 2)")
    print(f"  version: {VERSION_CRUZADAS}")
    print(
        f"  {'id':<8} {'prior.':>7} {'familia':<10} {'resultado':<20} qué evalúa"
    )
    print(f"  {'-'*8} {'-'*7} {'-'*10} {'-'*20} {'-'*60}")
    for regla in REGISTRO_CRUZADAS.reglas:
        familia = FAMILIA_POR_REGLA.get(regla.id, "?")
        print(
            f"  {regla.id:<8} {regla.prioridad:>7} {familia:<10} "
            f"{str(regla.resultado):<20} {regla.detalle.split(' (')[0]}"
        )
    print(
        "\n  Sin letra, sin candidatos y sin gap no hay regla que dispare: el "
        "caso queda en revisión (no se aprueba por descarte)."
    )
    print(f"  Campos críticos para concluir: {', '.join(CAMPOS_CRITICOS)}")


def _imprimir_corrida(corrida: dict[str, Any], detalle: bool) -> None:
    """Imprime el resultado de un escenario."""
    resultado = corrida["resultado"]
    contexto = corrida["contexto"]
    veredicto = corrida["veredicto"]

    print(
        f"       estado={resultado.estado:<9} certeza={str(resultado.certeza):<4} "
        f"origen={str(resultado.origen):<8} concluye={resultado.concluye}"
    )
    print(
        f"       letra={str(contexto.letra):<5} coherente={contexto.coherente} "
        f"hitl={resultado.hitl.prioridad if resultado.hitl else '-'}"
        f"/{resultado.hitl.estado if resultado.hitl else '-'}"
        f"  Decision={'sí' if corrida['con_decision'].decision else 'no'}"
    )
    print(f"       reglas: {', '.join(veredicto.disparadas) or '(ninguna)'}")
    print(f"       motivo: {veredicto.motivo}")

    if detalle:
        if veredicto.faltan_datos:
            print(f"       faltan: {', '.join(veredicto.faltan_datos)}")
        if contexto.incoherencias:
            for incoherencia in contexto.incoherencias:
                print(f"       incoherencia: {incoherencia}")
        if veredicto.alertas:
            for alerta in veredicto.alertas:
                print(f"       alerta [{alerta['regla']}]: {alerta['mensaje']}")
        print(
            f"       responsables: "
            f"{', '.join(f'{c}={f.value}' for c, f in sorted(contexto.fuentes_por_campo.items())[:6])}"
            f"{' …' if len(contexto.fuentes_por_campo) > 6 else ''}"
        )


def _verificar_fronteras() -> list[dict[str, Any]]:
    """Verifica que T-501 respete sus límites (lo que NO hace)."""
    fronteras: list[dict[str, Any]] = []

    # 1. No muta la evidencia de entrada.
    original = _evidencia(FACTURA_A_COMPLETA)
    antes = dict(original.trazabilidad)
    concluir(original)
    fronteras.append(
        {
            "que": "no muta la evidencia de entrada (combinar/concluir son puras)",
            "ok": original.decision is None and dict(original.trazabilidad) == antes,
        }
    )

    # 2. Conserva los campos de la evidencia combinada.
    salida = concluir(original)
    fronteras.append(
        {
            "que": "conserva los campos de la evidencia combinada (no los recorta)",
            "ok": set(salida.campos) == set(original.campos),
        }
    )

    # 3. No llama al agente: T-504 ya está implementado, pero la pasada 2 no lo
    #    invoca. Se verifica el comportamiento real (no se escala si el código
    #    concluyó), no que el esqueleto siga roto.
    from voucherflow.conclusion import escalar_a_agente as _escalar_agente

    escalado = _escalar_agente(original)
    fronteras.append(
        {
            "que": "la pasada 2 no escala al agente (T-504 vive en su propia etapa)",
            "ok": (not escalado.escalado) and escalado.candidato is None,
        }
    )

    # 4. No encola HITL (T-505).
    try:
        encolar_hitl(None)  # type: ignore[arg-type]
        ok = False
    except NotImplementedError:
        ok = True
    fronteras.append({"que": "no encola HITL (T-505 sigue siendo esqueleto)", "ok": ok})

    # 5. La certeza se deriva de la etapa (el contrato de F0 lo exige).
    from voucherflow.schemas.evidence import CombinedEvidence, Decision

    try:
        CombinedEvidence(
            documento_id="doc-1",
            campos={},
            decision=Decision(concluye=False, certeza="baja", origen="programa"),
        )
        ok = False
    except Exception:
        ok = True
    fronteras.append(
        {
            "que": "el contrato rechaza un Decision de programa con certeza baja "
            "(la certeza se deriva de la etapa)",
            "ok": ok,
        }
    )

    # 6. Un caso ambiguo viaja sin Decision (no lo decidió nadie todavía).
    ambiguo = concluir(_evidencia({"razon_social_emisor": "ACME"}))
    fronteras.append(
        {
            "que": "un caso sin concluir viaja sin Decision (espera a T-504/T-505)",
            "ok": ambiguo.decision is None
            and ambiguo.trazabilidad["conclusion"]["estado"] == ESTADO_REVISION,
        }
    )

    # 7. Sin letra no se inventa una letra (ADR-001).
    contexto = ContextoConclusion.desde_evidencia(_evidencia(BASE))
    fronteras.append(
        {
            "que": "sin letra declarada no se inventa una letra (ADR-001)",
            "ok": contexto.letra is None and contexto.coherente,
        }
    )

    # 8. Determinismo.
    a = concluir_caso(_evidencia(FACTURA_A_COMPLETA)).como_dict()
    b = concluir_caso(_evidencia(FACTURA_A_COMPLETA)).como_dict()
    fronteras.append({"que": "es determinista (mismo caso, mismo veredicto)", "ok": a == b})

    return fronteras


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def _parsear_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Inspecciona T-501: reglas cruzadas sobre la evidencia combinada.",
    )
    parser.add_argument("--tabla", action="store_true", help="solo la tabla de reglas cruzadas")
    parser.add_argument("--caso", help="correr un solo escenario (por nombre)")
    parser.add_argument("--manual", action="store_true", help="mostrar el caso ambiguo sin Decision")
    parser.add_argument("--json", type=Path, help="guardar el reporte en JSON")
    return parser.parse_args()


def main() -> None:
    args = _parsear_args()
    reporte: dict[str, Any] = {
        "version": VERSION_CRUZADAS,
        "tabla": [
            {
                "id": regla.id,
                "prioridad": regla.prioridad,
                "familia": FAMILIA_POR_REGLA.get(regla.id),
                "resultado": str(regla.resultado),
                "detalle": regla.detalle,
            }
            for regla in REGISTRO_CRUZADAS.reglas
        ],
    }

    print("T-501 (F5) — reglas cruzadas sobre la evidencia combinada")
    _imprimir_tabla()
    if args.tabla:
        sys.exit(0)

    if args.manual:
        print("\nCaso ambiguo (sin Decision: lo resolverán T-504/T-505)")
        _imprimir_corrida(_correr(ESCENARIOS[4]), detalle=True)
        sys.exit(0)

    escenarios = ESCENARIOS
    if args.caso:
        escenarios = [e for e in ESCENARIOS if e["nombre"] == args.caso]
        if not escenarios:
            print(f"\nNo existe el escenario {args.caso!r}.", file=sys.stderr)
            sys.exit(2)

    fallos = 0
    print("\nEscenarios de conclusión")
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
                "estado": corrida["resultado"].estado,
                "reglas": corrida["veredicto"].disparadas,
            }
        )

    print("\nFronteras de T-501 (lo que NO hace)")
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

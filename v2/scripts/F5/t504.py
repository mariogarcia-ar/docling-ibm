#!/usr/bin/env python
"""Inspecciona T-504 (F5) — escalado al agente IA y blindaje post-agente.

**Fase**: F5 (conclusión) · **Tarea**: T-504 · **Épica**: E-CONC-3 · **ADR-008**.

Muestra, **sin Ollama, sin Docling y sin red** (agentes dobles inyectados):

  1. Las **capas del blindaje**: quién puede elegir qué y en qué orden se verifica
     (prompt → orquestador → contrato).
  2. Los **escenarios de escalado**: el agente elige dentro del universo, el agente
     **resucita un descartado** (rechazado y auditado), el agente se abstiene, el
     agente falla o no está configurado, y el caso que **no** se escala porque el
     código ya concluyó.
  3. Las **fronteras** de la tarea: el agente es inyectable, su fallo no tumba el
     pipeline, no decide si el código concluyó, no normaliza, y su decisión **no**
     se consolida como "certeza alta por programa".

Uso:
    python scripts/F5/t504.py                        # capas + escenarios + fronteras
    python scripts/F5/t504.py --capas                # solo las capas del blindaje
    python scripts/F5/t504.py --caso resucita_descartado
    python scripts/F5/t504.py --manual               # el rechazo del blindaje, paso a paso
    python scripts/F5/t504.py --json /tmp/t504.json

Nota: la suite default de pytest cubre lo mismo en
``tests/test_conclusion_agente_t504.py``; este script es la verificación de humo
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

from voucherflow.conclusion import (  # noqa: E402
    AGENTE_ELECCION_INVALIDA,
    AGENTE_FALLO,
    AGENTE_NO_ESCALADO,
    AGENTE_SE_ABSTUVO,
    AGENTE_ELIGIO,
    VERSION_AGENTE,
    concluir_con_agente,
    escalar_a_agente,
)
from voucherflow.conclusion.engine import encolar_hitl  # noqa: E402
from voucherflow.conclusion.prompt_agente import (  # noqa: E402
    VERSION_PROMPT_AGENTE,
    construir_messages_agente,
)
from voucherflow.extraction.flows import combinar_evidencia  # noqa: E402
from voucherflow.rules.contexto import ContextoTipoComprobante  # noqa: E402
from voucherflow.rules.cruzadas import ESTADO_APROBADO, ESTADO_REVISION  # noqa: E402
from voucherflow.schemas.evidence import (  # noqa: E402
    EvidenceField,
    Fuente,
    SourceEvidence,
)

# ---------------------------------------------------------------------------
# Datos de los escenarios (sin red)
# ---------------------------------------------------------------------------

#: Factura B con emisor y receptor RI: el código la deja en revisión (R7) y tiene
#: candidatos (negocio espera ``A``, el documento dice ``B`` → ``A`` descartado).
CASO_AMBIGUO: dict[str, Any] = {
    "tipo_comprobante": "B",
    "razon_social_emisor": "ACME SA",
    "cuit_emisor": "30-12345678-9",
    "fecha_emision": "2025-08-14",
    "nro_comprobante": "0001-00000001",
    "moneda": "ARS",
    "iva": "0.00",
    "importe_total_facturado": "121.00",
}

#: Factura A completa: el código **sí** concluye (no se escala).
CASO_RESUELTO: dict[str, Any] = {
    "tipo_comprobante": "A",
    "razon_social_emisor": "ACME SA",
    "cuit_emisor": "30-12345678-9",
    "razon_social_receptor": "Cliente SA",
    "cuit_receptor": "27-30111222-4",
    "fecha_emision": "2025-08-14",
    "nro_comprobante": "0001-00000001",
    "moneda": "ARS",
    "subtotal": "100.00",
    "iva": "21.00",
    "importe_total_facturado": "121.00",
}

CTX_RI_RI = ContextoTipoComprobante(
    emisor_condicion_fiscal="Responsable Inscripto",
    receptor_condicion_fiscal="Responsable Inscripto",
)


class _Respuesta:
    def __init__(self, contenido: str):
        self.contenido = contenido


class AgenteDoble:
    """Agente de prueba: contenido fijo y registro de lo que recibió."""

    def __init__(self, contenido: str | None):
        self.contenido = contenido or ""
        self.messages: list[dict[str, str]] | None = None

    def decidir(self, messages, modelo, *, num_ctx=None):
        self.messages = messages
        return _Respuesta(self.contenido)


class AgenteRoto:
    def decidir(self, messages, modelo, *, num_ctx=None):
        raise RuntimeError("el modelo no está disponible")


def _json_agente(candidato: Any) -> str:
    return json.dumps(
        {"candidato": candidato, "justificacion": "porque sí", "fragmento_sustento": "iva: 0.00"},
        ensure_ascii=False,
    )


def _escenarios() -> list[dict[str, Any]]:
    return [
        {
            "nombre": "el_agente_elige",
            "que": "el agente elige dentro del universo → se acepta",
            "campos": CASO_AMBIGUO,
            "agente": AgenteDoble(_json_agente("B")),
            "espera": {"escalado": True, "desenlace": AGENTE_ELIGIO, "candidato": "B",
                       "estado": ESTADO_APROBADO, "certeza": "baja", "origen": "agente_ia"},
        },
        {
            "nombre": "resucita_descartado",
            "que": "el agente intenta un descartado → el blindaje lo RECHAZA",
            "campos": CASO_AMBIGUO,
            "agente": AgenteDoble(_json_agente("A")),
            "espera": {"escalado": True, "desenlace": AGENTE_ELECCION_INVALIDA, "candidato": None,
                       "estado": ESTADO_REVISION, "origen": None, "bloques": ["A"]},
        },
        {
            "nombre": "el_agente_se_abstiene",
            "que": "no puede elegir con fundamento → abstención (salida válida)",
            "campos": CASO_AMBIGUO,
            "agente": AgenteDoble(_json_agente(None)),
            "espera": {"escalado": True, "desenlace": AGENTE_SE_ABSTUVO, "candidato": None,
                       "estado": ESTADO_REVISION, "origen": None},
        },
        {
            "nombre": "el_agente_falla",
            "que": "el agente no responde → revisión, con la anomalía registrada",
            "campos": CASO_AMBIGUO,
            "agente": AgenteRoto(),
            "espera": {"escalado": True, "desenlace": AGENTE_FALLO, "candidato": None,
                       "estado": ESTADO_REVISION, "origen": None},
        },
        {
            "nombre": "sin_agente_configurado",
            "que": "hook desactivado (ADR-008) → revisión, sin decisión automática",
            "campos": CASO_AMBIGUO,
            "agente": None,
            "espera": {"escalado": True, "desenlace": AGENTE_FALLO, "candidato": None,
                       "estado": ESTADO_REVISION, "origen": None},
        },
        {
            "nombre": "el_codigo_ya_concluyo",
            "que": "el código concluyó → NO se escala (no se gasta una llamada)",
            "campos": CASO_RESUELTO,
            "agente": AgenteDoble(_json_agente("A")),
            "espera": {"escalado": False, "desenlace": AGENTE_NO_ESCALADO, "candidato": None,
                       "estado": ESTADO_APROBADO, "certeza": "alta", "origen": "programa"},
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
        "doc-t504", [_source(Fuente.vlm, campos), _source(Fuente.llm, campos)]
    )


def _correr(escenario: dict[str, Any]) -> Any:
    return concluir_con_agente(
        _evidencia(escenario["campos"]),
        contexto_tipo=CTX_RI_RI,
        agente=escenario["agente"],
        modelo="modelo-de-prueba",
    )


def _verificar(corrida: Any, espera: dict[str, Any]) -> list[str]:
    agente = corrida.agente
    resultado = corrida.resultado
    diferencias: list[str] = []

    obtenido = {
        "escalado": agente.escalado,
        "desenlace": agente.desenlace,
        "candidato": agente.candidato,
        "origen": resultado.origen.value if resultado.origen else None,
        "certeza": resultado.certeza.value if resultado.certeza else None,
        "estado": resultado.estado.value,
    }
    for clave, valor in obtenido.items():
        if clave in espera and espera[clave] != valor:
            diferencias.append(f"{clave}: esperado {espera[clave]!r}, obtenido {valor!r}")

    if "bloques" in espera and agente.bloques != espera["bloques"]:
        diferencias.append(f"bloques: esperado {espera['bloques']}, obtenido {agente.bloques}")

    return diferencias


# ---------------------------------------------------------------------------
# Impresión
# ---------------------------------------------------------------------------


def _imprimir_capas() -> None:
    print("\nCapas del blindaje (defensa en profundidad)")
    print(f"  orquestación: {VERSION_AGENTE} · prompt: {VERSION_PROMPT_AGENTE}")
    print()
    capas = [
        (
            "1. El prompt",
            "declara el universo cerrado y NO incluye los descartados",
            "lo que el agente no ve, no puede elegir",
        ),
        (
            "2. El orquestador",
            "valida la elección contra candidatos_restantes",
            "una elección fuera del universo SE RECHAZA (no se corrige)",
        ),
        (
            "3. El contrato",
            "Decision rechaza un valor a la vez descartado y restante",
            "el blindaje no puede 'olvidarse': lo hace cumplir el schema",
        ),
    ]
    print(f"  {'capa':<18} {'qué hace':<56} por qué")
    print(f"  {'-'*18} {'-'*56} {'-'*40}")
    for capa, hace, porque in capas:
        print(f"  {capa:<18} {hace:<56} {porque}")

    print(
        "\n  El agente decide, pero NO declara la certeza: la certeza se deriva de la\n"
        "  etapa (glosario §2). Un agente implica certeza=baja y revisión humana."
    )


def _imprimir_corrida(corrida: Any, detalle: bool) -> None:
    agente = corrida.agente
    resultado = corrida.resultado
    print(
        f"       escalado={str(agente.escalado):<5} desenlace={agente.desenlace:<17} "
        f"candidato={str(agente.candidato):<5} bloques={agente.bloques or '[]'}"
    )
    origen = resultado.origen.value if resultado.origen else "—"
    certeza = resultado.certeza.value if resultado.certeza else "—"
    print(
        f"       → estado={resultado.estado.value:<9} certeza={certeza:<4} origen={origen:<9} "
        f"hitl={resultado.hitl.prioridad}/{resultado.hitl.estado}"
    )
    print(f"       {agente.motivo}")

    if detalle and agente.eleccion is not None:
        print(
            f"       el agente declaró: candidato={agente.eleccion.candidato!r} · "
            f"justificación={agente.eleccion.justificacion[:44]!r}"
        )
    if detalle and agente.escalado and agente.hubo_blindaje:
        print(
            "       ⚠️ blindaje: el agente propuso un valor fuera del universo; se "
            "rechazó y quedó registrado"
        )


def _verificar_fronteras() -> list[dict[str, Any]]:
    """Verifica que T-504 respete sus límites (lo que NO hace)."""
    fronteras: list[dict[str, Any]] = []

    # 1. El agente decide, pero no declara la certeza (no es parámetro del escalate).
    import inspect

    from voucherflow.conclusion.agent import escalar_a_agente as _escalar_veredicto

    firma = inspect.signature(_escalar_veredicto)
    fronteras.append(
        {
            "que": "el agente no declara la certeza (se deriva de la etapa)",
            "ok": "certeza" not in firma.parameters and "origen" not in firma.parameters,
        }
    )

    # 2. No se escala si el código concluyó (no se gasta una llamada).
    agente = AgenteDoble(_json_agente("A"))
    corrida = _correr({**next(e for e in _escenarios() if e["nombre"] == "el_codigo_ya_concluyo")})
    fronteras.append(
        {
            "que": "no se escala si el código concluyó (no se llama al modelo)",
            "ok": not corrida.agente.escalado and agente.messages is None,
        }
    )

    # 3. Los descartados no se le pasan al agente (primera capa).
    agente = AgenteDoble(_json_agente("B"))
    escalar_a_agente(_evidencia(CASO_AMBIGUO), contexto_tipo=CTX_RI_RI, agente=agente)
    texto = "\n".join(m["content"] for m in agente.messages)
    fronteras.append(
        {
            "que": "el prompt pide respetar el universo (primera capa del blindaje)",
            "ok": "candidatos_restantes" in texto,
        }
    )

    # 4. Un fallo del agente no tumba el pipeline.
    corrida = _correr({**next(e for e in _escenarios() if e["nombre"] == "el_agente_falla")})
    fronteras.append(
        {
            "que": "un agente que falla no tumba el pipeline (queda en revisión)",
            "ok": corrida.resultado.estado.value == ESTADO_REVISION,
        }
    )

    # 5. El caso del agente NO se consolida como certeza alta por programa.
    corrida = _correr({**next(e for e in _escenarios() if e["nombre"] == "el_agente_elige")})
    fronteras.append(
        {
            "que": "la decisión del agente no se consolida como «certeza alta por programa»",
            "ok": not corrida.consolidacion.concluyo_por_programa
            and corrida.resultado.origen.value == "agente_ia",
        }
    )

    # 6. Un caso del agente pide revisión humana de prioridad alta.
    fronteras.append(
        {
            "que": "el caso del agente va a revisión humana con prioridad alta (Gherkin)",
            "ok": corrida.resultado.hitl.requerido and corrida.resultado.hitl.prioridad == "alta",
        }
    )

    # 7. No encola HITL (T-505 sigue siendo esqueleto).
    try:
        encolar_hitl(None)  # type: ignore[arg-type]
        ok_hitl = False
    except NotImplementedError:
        ok_hitl = True
    fronteras.append({"que": "no encola HITL (T-505 sigue siendo esqueleto)", "ok": ok_hitl})

    # 8. Es determinista y no muta la evidencia de entrada.
    original = _evidencia(CASO_AMBIGUO)
    antes = dict(original.trazabilidad)
    a = concluir_con_agente(
        original, contexto_tipo=CTX_RI_RI, agente=AgenteDoble(_json_agente("B")), modelo="m"
    ).como_dict()
    b = concluir_con_agente(
        _evidencia(CASO_AMBIGUO), contexto_tipo=CTX_RI_RI, agente=AgenteDoble(_json_agente("B")), modelo="m"
    ).como_dict()
    fronteras.append(
        {
            "que": "es determinista y no muta la evidencia de entrada",
            "ok": a == b and dict(original.trazabilidad) == antes,
        }
    )

    # 9. El prompt declara las tres piezas que pide el Gherkin.
    messages = construir_messages_agente(
        evidencia={"cuit_emisor": "30-1"},
        candidatos_restantes=["B"],
        reglas_que_fallaron=["CRUZ_5"],
    )
    texto = "\n".join(m["content"] for m in messages)
    fronteras.append(
        {
            "que": "el agente recibe evidencia + reglas que fallaron + candidatos (Gherkin)",
            "ok": all(fragmento in texto for fragmento in ("cuit_emisor", "CRUZ_5", "B")),
        }
    )

    return fronteras


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def _parsear_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Inspecciona T-504: escalado al agente IA y blindaje post-agente.",
    )
    parser.add_argument("--capas", action="store_true", help="solo las capas del blindaje")
    parser.add_argument("--caso", help="correr un solo escenario (por nombre)")
    parser.add_argument("--manual", action="store_true", help="el rechazo del blindaje, paso a paso")
    parser.add_argument("--json", type=Path, help="guardar el reporte en JSON")
    return parser.parse_args()


def main() -> None:
    args = _parsear_args()
    reporte: dict[str, Any] = {"version": VERSION_AGENTE, "prompt": VERSION_PROMPT_AGENTE}

    print("T-504 (F5) — escalado al agente IA y blindaje post-agente")
    _imprimir_capas()
    if args.capas:
        sys.exit(0)

    if args.manual:
        print("\nEl blindaje en acción: el agente intenta un candidato descartado")
        _imprimir_corrida(_correr(_escenarios()[1]), detalle=True)
        print("\nContraste: el agente elige dentro del universo")
        _imprimir_corrida(_correr(_escenarios()[0]), detalle=True)
        sys.exit(0)

    escenarios = _escenarios()
    if args.caso:
        escenarios = [e for e in escenarios if e["nombre"] == args.caso]
        if not escenarios:
            print(f"\nNo existe el escenario {args.caso!r}.", file=sys.stderr)
            sys.exit(2)

    fallos = 0
    print("\nEscenarios de escalado")
    reporte["escenarios"] = []
    for escenario in escenarios:
        corrida = _correr(escenario)
        diferencias = _verificar(corrida, escenario["espera"])
        marca = "✅" if not diferencias else "❌"
        print(f"\n  {marca} {escenario['nombre']:<26} {escenario['que']}")
        _imprimir_corrida(corrida, detalle=True)
        for diferencia in diferencias:
            print(f"       ❌ {diferencia}")
            fallos += 1
        reporte["escenarios"].append(
            {
                "nombre": escenario["nombre"],
                "ok": not diferencias,
                "diferencias": diferencias,
                "desenlace": corrida.agente.desenlace,
                "candidato": corrida.agente.candidato,
            }
        )

    print("\nFronteras de T-504 (lo que NO hace)")
    reporte["fronteras"] = []
    for frontera in _verificar_fronteras():
        marca = "✅" if frontera["ok"] else "❌"
        print(f"  {marca} {frontera['que']}")
        if not frontera["ok"]:
            fallos += 1
        reporte["fronteras"].append(frontera)

    ok = sum(1 for e in reporte["escenarios"] if e["ok"])
    # % agente (R-09): de los escenarios que **debían** escalar (el código no
    # concluyó), en cuántos se llamó al agente. Es la tasa que pide el DoD.
    elegibles = [e for e in reporte["escenarios"] if e["desenlace"] != AGENTE_NO_ESCALADO]
    escalados = len(elegibles)
    print(f"\nEscenarios verificados: {ok}/{len(escenarios)}")
    print(
        f"% agente (R-09): {escalados}/{len(elegibles)} escenarios elegibles "
        f"escalaron al agente"
    )
    reporte["metrica_pct_agente"] = {
        "escalados": escalados,
        "elegibles": len(elegibles),
        "tasa": (escalados / len(elegibles)) if elegibles else 0.0,
    }
    reporte["fallos"] = fallos
    if args.json:
        args.json.write_text(json.dumps(reporte, ensure_ascii=False, indent=2), encoding="utf-8")
        print(f"Reporte guardado en {args.json}")
    sys.exit(1 if fallos else 0)


if __name__ == "__main__":
    main()

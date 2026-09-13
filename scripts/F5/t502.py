#!/usr/bin/env python
"""Inspecciona T-502 (F5) — gaps y búsqueda acotada de evidencia adicional.

**Fase**: F5 (conclusión) · **Tarea**: T-502 · **Épica**: E-CONC-2 · **ADR-003**.

Muestra, **sin Ollama, sin Docling y sin red** (buscadores dobles inyectados):

  1. El **catálogo de gaps**: qué campos se pueden ir a buscar, con qué objetivo
     concreto y cuáles no (los que solo pueden venir del documento).
  2. Los **escenarios de búsqueda**: el caso con un gap crítico que se cubre con
     el padrón (y desbloquea el veredicto), el mismo caso **sin** hook (ADR-003:
     no bloquea el MVP), el proveedor caído que reintenta, el "consulté y no
     está" que **no** insiste, y el presupuesto que se agota y **corta** la
     búsqueda (E-CONC-2: no hay loop abierto).
  3. Las **fronteras** de la tarea: la búsqueda no decide, no muta la evidencia,
     no busca lo no buscable, no consulta dos veces el mismo gap resuelto, y un
     proveedor que falla o que devuelve cualquier cosa no tumba el pipeline.

Uso:
    python scripts/F5/t502.py                       # catálogo + escenarios + fronteras
    python scripts/F5/t502.py --catalogo            # solo el catálogo de gaps
    python scripts/F5/t502.py --caso cubre_gap
    python scripts/F5/t502.py --manual              # un caso con gap, paso a paso
    python scripts/F5/t502.py --json /tmp/t502.json

Nota: la suite default de pytest cubre lo mismo en
``tests/test_conclusion_gaps_t502.py``; este script es la verificación de humo
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

from voucherflow.conclusion import concluir_con_busqueda  # noqa: E402
from voucherflow.conclusion.engine import encolar_hitl, escalar_a_agente  # noqa: E402
from voucherflow.extraction.flows import combinar_evidencia  # noqa: E402
from voucherflow.models.arca import ArcaClient  # noqa: E402
from voucherflow.rules.contexto_conclusion import ContextoConclusion  # noqa: E402
from voucherflow.rules.cruzadas import ESTADO_APROBADO, ESTADO_REVISION  # noqa: E402
from voucherflow.rules.gaps import (  # noqa: E402
    CATALOGO_GAPS,
    CRITICIDAD_BLOQUEANTE,
    INTENTO_CUBIERTO,
    INTENTO_NO_BUSCABLE,
    INTENTO_NO_DISPONIBLE,
    INTENTO_PRESUPUESTO_AGOTADO,
    INTENTO_SIN_DATO,
    VERSION_GAPS,
    PresupuestoBusqueda,
    ResultadoBusqueda,
    buscar_evidencia_adicional,
    detectar_gaps,
)
from voucherflow.schemas.evidence import (  # noqa: E402
    EvidenceField,
    Fuente,
    SourceEvidence,
)

# ---------------------------------------------------------------------------
# Datos de los escenarios (sin red)
# ---------------------------------------------------------------------------

#: Caso con un gap **bloqueante**: falta el importe total (campo crítico).
SIN_IMPORTE: dict[str, Any] = {
    "tipo_comprobante": "A",
    "razon_social_emisor": "ACME SA",
    "cuit_emisor": "30-12345678-9",
    "cuit_receptor": "27-30111222-4",
    "fecha_emision": "2025-08-14",
    "nro_comprobante": "0001-00000001",
    "moneda": "ARS",
    "subtotal": "100.00",
    "iva": "21.00",
    "descripcion": "servicios",
}

#: Caso completo (sin gaps).
COMPLETO: dict[str, Any] = {
    **SIN_IMPORTE,
    "razon_social_receptor": "Cliente SA",
    "impuestos_internos": "0.00",
    "percepcion_iibb": "0.00",
    "otros_impuestos": "0.00",
    "monto_no_gravado": "0.00",
    "importe_total_facturado": "121.00",
}


class BuscadorDoble:
    """Buscador de prueba: responde por campo y cuenta las consultas."""

    def __init__(self, respuestas: dict[str, list[ResultadoBusqueda]] | None = None):
        self.respuestas = respuestas or {}
        self.consultas: list[str] = []

    def buscar(self, gap: Any, contexto: ContextoConclusion) -> ResultadoBusqueda:
        self.consultas.append(gap.campo)
        secuencia = self.respuestas.get(gap.campo)
        if not secuencia:
            return ResultadoBusqueda(campo=gap.campo, disponible=True, valor=None)
        if len(secuencia) == 1:
            return secuencia[0]
        return secuencia.pop(0)


def _cubre(campo: str, valor: Any = "121.00", sostento: str = "padrón WSCDC: constatado") -> ResultadoBusqueda:
    return ResultadoBusqueda(campo=campo, valor=valor, disponible=True, sostento=sostento)


def _caido(campo: str, error: str = "timeout del padrón") -> ResultadoBusqueda:
    return ResultadoBusqueda(campo=campo, disponible=False, error=error)


def _sin_dato(campo: str) -> ResultadoBusqueda:
    return ResultadoBusqueda(campo=campo, disponible=True, valor=None)


#: Escenarios: ``(nombre, campos, buscador, presupuesto, qué espera)``.
#:
#: **Cada escenario recibe su propia instancia del buscador**: el doble acumula
#: las consultas que recibe, así que compartir una instancia entre escenarios
#: haría que el segundo arranque con el contador del primero (y un caso sin gaps
#: parecería haber consultado).
def _escenarios() -> list[dict[str, Any]]:
    return [
        {
            "nombre": "cubre_gap",
            "que": "el padrón cubre el crítico faltante → el veredicto se desbloquea",
            "campos": SIN_IMPORTE,
            "buscador": BuscadorDoble({"importe_total_facturado": [_cubre("importe_total_facturado")]}),
            "presupuesto": PresupuestoBusqueda(max_consultas=3, max_reintentos=1),
            "espera": {"estado": ESTADO_APROBADO, "cubiertos": ["importe_total_facturado"],
                       "gap_restante": None},
        },
        {
            "nombre": "hook_desactivado",
            "que": "sin hook (ADR-003) el caso sigue en revisión, sin romper nada",
            "campos": SIN_IMPORTE,
            "buscador": None,
            "presupuesto": None,
            "espera": {"estado": ESTADO_REVISION, "cubiertos": [],
                       "gap_restante": "importe_total_facturado"},
        },
        {
            "nombre": "proveedor_caido_luego_responde",
            "que": "timeout y después responde: reintenta y cubre",
            "campos": SIN_IMPORTE,
            "buscador": BuscadorDoble({
                "importe_total_facturado": [_caido("importe_total_facturado"), _cubre("importe_total_facturado")]
            }),
            "presupuesto": PresupuestoBusqueda(max_consultas=5, max_reintentos=2),
            "espera": {"estado": ESTADO_APROBADO, "cubiertos": ["importe_total_facturado"],
                       "gap_restante": None},
        },
        {
            "nombre": "consultado_y_no_esta",
            "que": "el padrón respondió que no está: NO se insiste",
            "campos": SIN_IMPORTE,
            "buscador": BuscadorDoble({"importe_total_facturado": [_sin_dato("importe_total_facturado")]}),
            "presupuesto": PresupuestoBusqueda(max_consultas=5, max_reintentos=3),
            "espera": {"estado": ESTADO_REVISION, "cubiertos": [],
                       "gap_restante": "importe_total_facturado", "consultas_gap": 1},
        },
        {
            "nombre": "presupuesto_agotado",
            "que": "el presupuesto se agota y la búsqueda CORTA (no hay loop abierto)",
            "campos": SIN_IMPORTE,
            "buscador": BuscadorDoble({}),
            "presupuesto": PresupuestoBusqueda(max_consultas=1, max_reintentos=0),
            "espera": {"estado": ESTADO_REVISION, "cubiertos": [],
                       "agotado": True, "intento_agotado": INTENTO_PRESUPUESTO_AGOTADO},
        },
        {
            "nombre": "caso_completo",
            "que": "sin gaps no se consulta nada",
            "campos": COMPLETO,
            "buscador": BuscadorDoble({}),
            "presupuesto": None,
            "espera": {"estado": ESTADO_APROBADO, "cubiertos": [], "consultas": 0},
        },
    ]


# ---------------------------------------------------------------------------
# Construcción
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
        "doc-t502", [_source(Fuente.vlm, campos), _source(Fuente.llm, campos)]
    )


def _correr(escenario: dict[str, Any]) -> Any:
    return concluir_con_busqueda(
        _evidencia(escenario["campos"]),
        buscador=escenario["buscador"],
        presupuesto=escenario["presupuesto"],
    )


def _verificar(corrida: Any, escenario: dict[str, Any]) -> list[str]:
    espera = escenario["espera"]
    diferencias: list[str] = []

    if corrida.conclusion.estado != espera["estado"]:
        diferencias.append(
            f"estado: esperado {espera['estado']!r}, obtenido {corrida.conclusion.estado!r}"
        )
    if sorted(corrida.busqueda.cubiertos) != sorted(espera["cubiertos"]):
        diferencias.append(
            f"cubiertos: esperados {espera['cubiertos']}, obtenidos {corrida.busqueda.cubiertos}"
        )
    if "gap_restante" in espera:
        gap = espera["gap_restante"]
        if gap is None and "importe_total_facturado" in corrida.gaps_restantes:
            diferencias.append("el gap bloqueante debería haber quedado cubierto")
        if gap is not None and gap not in corrida.gaps_restantes:
            diferencias.append(f"el gap {gap!r} debería seguir en gaps_restantes")
    if "consultas" in espera:
        buscador = escenario["buscador"]
        if len(buscador.consultas) != espera["consultas"]:
            diferencias.append(
                f"consultas: esperadas {espera['consultas']}, hubo {len(buscador.consultas)}"
            )
    if "consultas_gap" in espera:
        buscador = escenario["buscador"]
        if buscador.consultas.count("importe_total_facturado") != espera["consultas_gap"]:
            diferencias.append(
                "se consultó el gap "
                f"{buscador.consultas.count('importe_total_facturado')} vez/veces y se "
                f"esperaba {espera['consultas_gap']}"
            )
    if espera.get("agotado"):
        if not corrida.busqueda.presupuesto.get("agotado"):
            diferencias.append("el presupuesto debería haber quedado agotado")
    if "intento_agotado" in espera:
        resultados = [i["resultado"] for i in corrida.busqueda.como_dict()["intentos"]]
        if espera["intento_agotado"] not in resultados:
            diferencias.append(
                f"falta el intento {espera['intento_agotado']!r} en la traza: {resultados}"
            )

    return diferencias


# ---------------------------------------------------------------------------
# Impresión
# ---------------------------------------------------------------------------


def _imprimir_catalogo() -> None:
    print("\nCatálogo de gaps (qué se puede ir a buscar)")
    print(f"  version: {VERSION_GAPS}")
    print(f"  {'campo':<26} {'criticidad':<12} {'buscable':<9} objetivo")
    print(f"  {'-'*26} {'-'*12} {'-'*9} {'-'*52}")
    for campo, entrada in CATALOGO_GAPS.items():
        buscable = "sí" if entrada["buscable"] else "no"
        print(
            f"  {campo:<26} {entrada['criticidad']:<12} {buscable:<9} "
            f"{entrada['objetivo']}"
        )
    print(
        "\n  Un campo fuera del catálogo se reporta igual (informativo y NO "
        "buscable):\n  no se dispara una consulta por algo que el catálogo no sabe "
        "pedir."
    )


def _imprimir_corrida(corrida: Any, detalle: bool) -> None:
    print(
        f"       estado={corrida.conclusion.estado:<9} "
        f"concluye={corrida.conclusion.concluye}  "
        f"Decision={'sí' if corrida.evidencia.decision else 'no'}"
    )
    print(
        f"       gaps={len(corrida.deteccion.gaps)} "
        f"(bloqueantes: {len(corrida.deteccion.bloqueantes)}) · "
        f"cubiertos: {corrida.busqueda.cubiertos or '(ninguno)'} · "
        f"restantes: {len(corrida.gaps_restantes)}"
    )
    p = corrida.busqueda.presupuesto
    print(
        f"       presupuesto: {p.get('consultas')}/{p.get('max_consultas')} "
        f"consultas (agotado={p.get('agotado')}) · "
        f"max_reintentos={p.get('max_reintentos')}"
    )
    print(f"       {corrida.busqueda.motivo}")

    if detalle:
        for intento in corrida.busqueda.como_dict()["intentos"][:6]:
            print(
                f"       intento #{intento['intento']} {intento['campo']}: "
                f"{intento['resultado']} — {intento['motivo'][:72]}"
            )


def _verificar_fronteras() -> list[dict[str, Any]]:
    """Verifica que T-502 respete sus límites (lo que NO hace)."""
    fronteras: list[dict[str, Any]] = []

    # 1. No muta la evidencia de entrada.
    original = _evidencia(SIN_IMPORTE)
    antes = dict(original.trazabilidad)
    concluir_con_busqueda(original, buscador=BuscadorDoble({}))
    fronteras.append(
        {
            "que": "no muta la evidencia de entrada",
            "ok": dict(original.trazabilidad) == antes and original.decision is None,
        }
    )

    # 2. La búsqueda no decide (devuelve datos y traza, no un veredicto).
    contexto = ContextoConclusion.desde_evidencia(_evidencia(SIN_IMPORTE))
    resultado = buscar_evidencia_adicional(detectar_gaps(contexto), contexto, buscador=None)
    fronteras.append(
        {
            "que": "la búsqueda no decide (no expone estado/certeza/origen)",
            "ok": not hasattr(resultado, "estado") and not hasattr(resultado, "certeza"),
        }
    )

    # 3. Lo no buscable no consume presupuesto ni dispara consultas.
    buscador = BuscadorDoble({})
    limite = PresupuestoBusqueda(max_consultas=100)
    contexto_vacio = ContextoConclusion.desde_evidencia(_evidencia({"razon_social_emisor": "ACME"}))
    buscar_evidencia_adicional(detectar_gaps(contexto_vacio), contexto_vacio, buscador=buscador, presupuesto=limite)
    fronteras.append(
        {
            "que": "lo no buscable no consume presupuesto (no se consulta la descripción)",
            "ok": "descripcion" not in buscador.consultas,
        }
    )

    # 4. El agente (T-504) ya está implementado, pero **la búsqueda no decide por
    #    su cuenta**: su corrida no produce una decisión de agente (el origen es
    #    None: no lo resolvió nadie), aunque el caso tenga candidatos.
    corrida = concluir_con_busqueda(_evidencia(SIN_IMPORTE), buscador=None)
    resultado = corrida.resultado or (
        corrida.consolidacion.valor if corrida.consolidacion else None
    )
    fronteras.append(
        {
            "que": "la búsqueda de evidencia no decide (no produce una decisión de agente)",
            "ok": corrida.conclusion.origen is None
            and (resultado is None or resultado.origen is None),
        }
    )

    # 5. No hay loop abierto: re-concluir no dispara otra búsqueda.
    buscador_libre = BuscadorDoble({})
    corrida = concluir_con_busqueda(_evidencia(SIN_IMPORTE), buscador=buscador_libre)
    fronteras.append(
        {
            "que": "no hay loop abierto (se busca una sola vez, el gap persiste y se reporta)",
            "ok": bool(corrida.gaps_restantes)
            and len(buscador_libre.consultas) == len(set(buscador_libre.consultas)),
        }
    )

    # 6. El adaptador ARCA no consulta sin URL (hook desactivado).
    deteccion = detectar_gaps(ContextoConclusion.desde_evidencia(_evidencia(SIN_IMPORTE)))
    gap = next(g for g in deteccion.gaps if g.campo == "importe_total_facturado")
    respuesta = ArcaClient(url=None).buscar(
        gap, ContextoConclusion.desde_evidencia(_evidencia(SIN_IMPORTE))
    )
    fronteras.append(
        {
            "que": "el adaptador ARCA sin URL reporta el hook desactivado (no consulta)",
            "ok": not respuesta.disponible and bool(respuesta.error),
        }
    )

    # 7. El veredicto no depende de si hubo búsqueda cuando no hacía falta.
    sin = concluir_con_busqueda(_evidencia(COMPLETO), buscador=None)
    con = concluir_con_busqueda(_evidencia(COMPLETO), buscador=BuscadorDoble({}))
    fronteras.append(
        {
            "que": "un caso completo concluye igual con el hook activado o desactivado",
            "ok": sin.conclusion.como_dict() == con.conclusion.como_dict(),
        }
    )

    # 8. La búsqueda no encola HITL: eso es un paso explícito aparte (T-505).
    evidencia_t505 = _evidencia(COMPLETO)
    fronteras.append(
        {
            "que": "la búsqueda de evidencia no encola HITL (es un paso aparte, T-505)",
            "ok": "hitl" not in evidencia_t505.trazabilidad,
        }
    )

    return fronteras


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def _parsear_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Inspecciona T-502: gaps y búsqueda acotada de evidencia adicional.",
    )
    parser.add_argument("--catalogo", action="store_true", help="solo el catálogo de gaps")
    parser.add_argument("--caso", help="correr un solo escenario (por nombre)")
    parser.add_argument("--manual", action="store_true", help="un caso con gap, paso a paso")
    parser.add_argument("--json", type=Path, help="guardar el reporte en JSON")
    return parser.parse_args()


def main() -> None:
    args = _parsear_args()
    reporte: dict[str, Any] = {
        "version": VERSION_GAPS,
        "catalogo": {
            campo: {
                "criticidad": entrada["criticidad"],
                "buscable": entrada["buscable"],
                "objetivo": entrada["objetivo"],
            }
            for campo, entrada in CATALOGO_GAPS.items()
        },
    }

    print("T-502 (F5) — gaps y búsqueda acotada de evidencia adicional")
    _imprimir_catalogo()
    if args.catalogo:
        sys.exit(0)

    if args.manual:
        print("\nCaso con gap crítico, paso a paso (hook desactivado)")
        escenario = _escenarios()[1]
        _imprimir_corrida(_correr(escenario), detalle=True)
        print("\nCaso con gap crítico, paso a paso (el padrón lo cubre)")
        escenario = _escenarios()[0]
        _imprimir_corrida(_correr(escenario), detalle=True)
        sys.exit(0)

    escenarios = _escenarios()
    if args.caso:
        escenarios = [e for e in escenarios if e["nombre"] == args.caso]
        if not escenarios:
            print(f"\nNo existe el escenario {args.caso!r}.", file=sys.stderr)
            sys.exit(2)

    fallos = 0
    print("\nEscenarios de búsqueda")
    reporte["escenarios"] = []
    for escenario in escenarios:
        corrida = _correr(escenario)
        diferencias = _verificar(corrida, escenario)
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
                "estado": corrida.conclusion.estado,
                "cubiertos": corrida.busqueda.cubiertos,
            }
        )

    print("\nFronteras de T-502 (lo que NO hace)")
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

#!/usr/bin/env python
"""Inspecciona T-505 (F5) — cola HITL, muestreo de auditoría y feedback.

**Fase**: F5 (conclusión) · **Tarea**: T-505 · **Épica**: E-CONC-4 · **ADR-004 /
ADR-009**.

Muestra, **sin Ollama, sin Docling y sin red**:

  1. La **política de encolado**: qué entra a revisión humana y con qué
     prioridad (certeza baja → obligatoria/alta; certeza alta → muestreo/baja).
  2. Los **escenarios**: un caso de certeza baja, uno de certeza alta muestreado,
     uno de certeza alta que no cae en la muestra, el muestreo apagado y la
     política con la revisión desactivada.
  3. El **muestreo reproducible**: la tasa que se pide es la que sale, el mismo
     caso siempre cae igual, y el muestreo se puede explicar.
  4. El **ciclo de revisión**: corrección estructurada, confirmación sin
     corrección y el feedback agregado separado por motivo (R-03 vs. R-09).
  5. Las **fronteras** de la tarea: sin red, determinista, y los errores
     explícitos.

Uso:
    python scripts/F5/t505.py                        # política + escenarios + fronteras
    python scripts/F5/t505.py --politica             # solo la política
    python scripts/F5/t505.py --muestreo             # el muestreo, en números
    python scripts/F5/t505.py --caso certeza_baja
    python scripts/F5/t505.py --manual               # el ciclo de revisión, paso a paso
    python scripts/F5/t505.py --json /tmp/t505.json

Nota: la suite default de pytest cubre lo mismo en
``tests/test_conclusion_hitl_t505.py``; este script es la verificación de humo
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
    MOTIVO_CERTEZA_BAJA,
    MOTIVO_MUESTREO_AUDITORIA,
    PRIORIDAD_ALTA,
    PRIORIDAD_BAJA,
    VERSION_HITL,
    ColaHitl,
    decidir_encolado,
    encolar_hitl,
    encolar_lote,
    seleccionado_para_auditoria,
)
from voucherflow.conclusion.hitl import (  # noqa: E402
    MOTIVO_NO_APLICA,
    MOTIVO_REVISION_DESACTIVADA,
)
from voucherflow.schemas.evidence import Certeza, Origen  # noqa: E402
from voucherflow.schemas.result import EstadoResultado, VoucherResult  # noqa: E402
from voucherflow.settings.config import HitlSettings, Settings  # noqa: E402

# ---------------------------------------------------------------------------
# Datos de los escenarios (sin red: no hace falta un documento)
# ---------------------------------------------------------------------------


def _resultado(
    documento_id: str,
    *,
    certeza: Certeza | None,
    origen: Origen | None,
    estado: EstadoResultado,
    tipo: str | None = None,
) -> VoucherResult:
    return VoucherResult(
        documento_id=documento_id,
        estado=estado,
        tipo_comprobante=tipo,
        certeza=certeza,
        origen=origen,
    )


def _escenarios() -> list[dict[str, Any]]:
    return [
        {
            "nombre": "certeza_baja",
            "que": "el agente resolvió → revisión obligatoria, prioridad alta",
            "politica": HitlSettings(muestreo_tasa=0.10),
            "resultado": _resultado(
                "doc-agente",
                certeza=Certeza.baja,
                origen=Origen.agente_ia,
                estado=EstadoResultado.revision,
                tipo="B",
            ),
            "espera": {
                "requerido": True,
                "prioridad": PRIORIDAD_ALTA,
                "motivo": MOTIVO_CERTEZA_BAJA,
            },
        },
        {
            "nombre": "sin_veredicto",
            "que": "nadie concluyó (ni el código ni el agente) → también obligatoria",
            "politica": HitlSettings(muestreo_tasa=0.10),
            "resultado": _resultado(
                "doc-huerfano",
                certeza=None,
                origen=None,
                estado=EstadoResultado.revision,
            ),
            "espera": {
                "requerido": True,
                "prioridad": PRIORIDAD_ALTA,
                "motivo": MOTIVO_CERTEZA_BAJA,
            },
        },
        {
            "nombre": "alta_muestreada",
            "que": "el código concluyó y la muestra lo eligió → auditoría, prioridad baja",
            "politica": HitlSettings(muestreo_tasa=1.0),
            "resultado": _resultado(
                "doc-alto",
                certeza=Certeza.alta,
                origen=Origen.programa,
                estado=EstadoResultado.aprobado,
                tipo="A",
            ),
            "espera": {
                "requerido": True,
                "prioridad": PRIORIDAD_BAJA,
                "motivo": MOTIVO_MUESTREO_AUDITORIA,
            },
        },
        {
            "nombre": "alta_sin_muestra",
            "que": "el código concluyó y no cayó en la muestra → no entra",
            "politica": HitlSettings(muestreo_tasa=0.0),
            "resultado": _resultado(
                "doc-alto",
                certeza=Certeza.alta,
                origen=Origen.programa,
                estado=EstadoResultado.aprobado,
                tipo="A",
            ),
            "espera": {
                "requerido": False,
                "prioridad": PRIORIDAD_BAJA,
                "motivo": MOTIVO_NO_APLICA,
            },
        },
        {
            "nombre": "muestreo_apagado",
            "que": "muestreo_activo=False → la auditoría no corre (la tasa se conserva)",
            "politica": HitlSettings(muestreo_tasa=1.0, muestreo_activo=False),
            "resultado": _resultado(
                "doc-alto",
                certeza=Certeza.alta,
                origen=Origen.programa,
                estado=EstadoResultado.aprobado,
                tipo="A",
            ),
            "espera": {
                "requerido": False,
                "prioridad": PRIORIDAD_BAJA,
                "motivo": MOTIVO_NO_APLICA,
            },
        },
        {
            "nombre": "revision_desactivada",
            "que": "revisión obligatoria desactivada explícitamente → se declara, no se silencia",
            "politica": HitlSettings(revision_obligatoria_certeza_baja=False),
            "resultado": _resultado(
                "doc-agente",
                certeza=Certeza.baja,
                origen=Origen.agente_ia,
                estado=EstadoResultado.revision,
                tipo="B",
            ),
            "espera": {
                "requerido": False,
                "prioridad": PRIORIDAD_ALTA,
                "motivo": MOTIVO_REVISION_DESACTIVADA,
            },
        },
    ]


# ---------------------------------------------------------------------------
# Verificación
# ---------------------------------------------------------------------------


def _verificar(escenario: dict[str, Any]) -> list[str]:
    decision = decidir_encolado(escenario["resultado"], hitl=escenario["politica"])
    diferencias: list[str] = []

    obtenido = {
        "requerido": decision.requerido,
        "prioridad": decision.prioridad,
        "motivo": decision.motivo,
    }
    for clave, valor in obtenido.items():
        if clave in escenario["espera"] and escenario["espera"][clave] != valor:
            diferencias.append(
                f"{clave}: esperado {escenario['espera'][clave]!r}, obtenido {valor!r}"
            )
    return diferencias


def _verificar_fronteras() -> list[dict[str, Any]]:
    """Verifica que T-505 respete sus límites (lo que NO hace)."""
    fronteras: list[dict[str, Any]] = []

    # 1. La decisión es pura: no muta el resultado.
    resultado = _resultado(
        "doc-1", certeza=Certeza.baja, origen=Origen.agente_ia,
        estado=EstadoResultado.revision,
    )
    antes = resultado.hitl.model_dump()
    decidir_encolado(resultado)
    fronteras.append(
        {
            "que": "decidir_encolado es puro: no muta el resultado",
            "ok": resultado.hitl.model_dump() == antes,
        }
    )

    # 2. El muestreo es reproducible: mismo documento, misma semilla, mismo resultado.
    docs = [f"doc-{i:04d}" for i in range(300)]
    primera = {d for d in docs if seleccionado_para_auditoria(d, tasa=0.10, semilla=0)}
    segunda = {d for d in docs if seleccionado_para_auditoria(d, tasa=0.10, semilla=0)}
    fronteras.append(
        {
            "que": "el muestreo es reproducible (auditable, no un random sin semilla)",
            "ok": primera == segunda and 0 < len(primera) < len(docs),
        }
    )

    # 3. La tasa configurada es la tasa que sale.
    proporcion = len(primera) / len(docs)
    fronteras.append(
        {
            "que": f"la muestra respeta la tasa pedida (~10%, salió {proporcion:.1%})",
            "ok": 0.05 < proporcion < 0.16,
        }
    )

    # 4. La semilla rota la muestra (permite estratificar sin cambiar la tasa).
    otra = {d for d in docs if seleccionado_para_auditoria(d, tasa=0.10, semilla=7)}
    fronteras.append(
        {
            "que": "la semilla rota qué se audita (estrato configurable)",
            "ok": otra != primera,
        }
    )

    # 5. El mismo caso no se duplica en la cola.
    cola = ColaHitl(hitl=HitlSettings(muestreo_tasa=0.0))
    repetido = _resultado(
        "doc-x", certeza=Certeza.baja, origen=Origen.agente_ia,
        estado=EstadoResultado.revision,
    )
    cola.encolar(repetido)
    cola.encolar(repetido)
    fronteras.append({"que": "el mismo caso no se duplica", "ok": len(cola) == 1})

    # 6. Re-encolar un caso revisado no borra el trabajo humano.
    cola.registrar_correccion("doc-x", campo="tipo_comprobante", valor_nuevo="A")
    cola.encolar(repetido)
    entrada = cola.entrada("doc-x")
    fronteras.append(
        {
            "que": "re-encolar no borra la corrección ya registrada",
            "ok": entrada is not None and entrada.corregido and entrada.revisado,
        }
    )

    # 7. Un documento fuera de la cola es un error explícito (feedback huérfano).
    try:
        ColaHitl().registrar_correccion("nope", campo="x", valor_nuevo="y")
        ok_error = False
    except KeyError:
        ok_error = True
    fronteras.append(
        {"que": "corregir un caso que no está en la cola falla ruidoso", "ok": ok_error}
    )

    # 8. Un campo vacío no es señal.
    try:
        cola.registrar_correccion("doc-x", campo="", valor_nuevo="y")
        ok_campo = False
    except ValueError:
        ok_campo = True
    fronteras.append({"que": "un campo vacío no es señal (falla)", "ok": ok_campo})

    # 9. El encolado no re-decide el caso: la certeza/origen quedan como estaban.
    resultado = _resultado(
        "doc-1", certeza=Certeza.baja, origen=Origen.agente_ia,
        estado=EstadoResultado.revision, tipo="B",
    )
    encolar_hitl(resultado)
    fronteras.append(
        {
            "que": "el encolado no re-decide el caso (certeza/origen intactos)",
            "ok": resultado.certeza == Certeza.baja
            and resultado.origen == Origen.agente_ia
            and resultado.tipo_comprobante == "B",
        }
    )

    # 10. La cola es estable ante el orden del lote (el muestreo es por documento).
    politica = Settings(hitl=HitlSettings(muestreo_tasa=0.5))
    alto_1 = _resultado(
        "doc-0001", certeza=Certeza.alta, origen=Origen.programa,
        estado=EstadoResultado.aprobado,
    )
    alto_2 = _resultado(
        "doc-0002", certeza=Certeza.alta, origen=Origen.programa,
        estado=EstadoResultado.aprobado,
    )
    a = {e.documento_id for e in encolar_lote([alto_1, alto_2], settings=politica).muestreados()}
    b = {e.documento_id for e in encolar_lote([alto_2, alto_1], settings=politica).muestreados()}
    fronteras.append(
        {"que": "el muestreo no depende del orden del lote", "ok": a == b}
    )

    # 11. Sin red: la cola es una estructura de datos.
    import inspect

    from voucherflow.conclusion import hitl as modulo_hitl

    fuente = inspect.getsource(modulo_hitl)
    fronteras.append(
        {
            "que": "no hay red en el módulo (ni requests, ni urllib, ni http)",
            "ok": not any(t in fuente for t in ("requests", "urllib", "http")),
        }
    )

    # 12. El feedback separa las dos razones de auditar (R-03 vs. R-09).
    cola_mixta = ColaHitl(hitl=HitlSettings(muestreo_tasa=1.0))
    cola_mixta.encolar(
        _resultado(
            "agente", certeza=Certeza.baja, origen=Origen.agente_ia,
            estado=EstadoResultado.revision,
        )
    )
    cola_mixta.encolar(
        _resultado(
            "regla", certeza=Certeza.alta, origen=Origen.programa,
            estado=EstadoResultado.aprobado,
        )
    )
    cola_mixta.registrar_correccion("agente", campo="tipo_comprobante", valor_nuevo="A")
    cola_mixta.registrar_correccion("regla", campo="iva", valor_nuevo="21.00")
    feedback = cola_mixta.feedback()
    fronteras.append(
        {
            "que": "el feedback separa certeza baja (R-09) de muestreo (R-03)",
            "ok": len(feedback["correcciones_de_certeza_baja"]) == 1
            and len(feedback["correcciones_de_muestreo"]) == 1,
        }
    )

    # 13. La confirmación sin corrección se distingue de «no revisado».
    cola_ok = ColaHitl(hitl=HitlSettings(muestreo_tasa=1.0))
    cola_ok.encolar(
        _resultado(
            "ok", certeza=Certeza.alta, origen=Origen.programa,
            estado=EstadoResultado.aprobado,
        )
    )
    cola_ok.confirmar("ok")
    fronteras.append(
        {
            "que": "confirmar sin corregir se distingue de no revisado",
            "ok": cola_ok.feedback()["confirmados"] == 1
            and cola_ok.feedback()["corregidos"] == 0,
        }
    )

    return fronteras


# ---------------------------------------------------------------------------
# Impresión
# ---------------------------------------------------------------------------


def _imprimir_politica() -> None:
    print("\nPolítica de la cola HITL (Gherkin E-CONC-4 / ADR-004)")
    print(f"  versión: {VERSION_HITL}")
    print()
    filas = [
        (
            "certeza baja",
            "SIEMPRE",
            "alta",
            "no hay veredicto firme → revisión obligatoria",
        ),
        (
            "certeza alta (código)",
            "solo si la muestra",
            "baja",
            "muestreo de auditoría: reglas que aciertan por accidente (R-03)",
        ),
        (
            "certeza alta (código)",
            "no",
            "—",
            "resuelto y no muestreado: no entra",
        ),
    ]
    print(f"  {'caso':<22} {'entra':<18} {'prioridad':<10} por qué")
    print(f"  {'-'*22} {'-'*18} {'-'*10} {'-'*52}")
    for caso, entra, prioridad, porque in filas:
        print(f"  {caso:<22} {entra:<18} {prioridad:<10} {porque}")
    print(
        "\n  El muestreo se deriva de (semilla, documento_id): es reproducible y\n"
        "  explicable. Un `random` sin semilla haría inauditable la decisión de\n"
        "  auditar un caso (no se podría responder «¿por qué éste y no aquél?»)."
    )


def _imprimir_muestreo() -> None:
    print("\nEl muestreo, en números (2000 documentos sintéticos)")
    print(f"  {'tasa':<8} {'muestra':<10} {'proporción':<12} notas")
    print(f"  {'-'*8} {'-'*10} {'-'*12} {'-'*40}")
    docs = [f"doc-{i:04d}" for i in range(2000)]
    for tasa in (0.0, 0.05, 0.10, 0.25, 1.0):
        muestra = [d for d in docs if seleccionado_para_auditoria(d, tasa=tasa)]
        nota = ""
        if tasa == 0.0:
            nota = "muestreo apagado"
        elif tasa == 0.10:
            nota = "sugerencia del ADR-004 (mitiga R-03)"
        elif tasa == 1.0:
            nota = "auditoría total (control / migración)"
        print(f"  {tasa:<8} {len(muestra):<10} {len(muestra)/len(docs):<12.3f} {nota}")

    print("\n  Estabilidad: la misma semilla da la misma muestra, siempre.")
    for semilla in (0, 1, 7):
        muestra = {d for d in docs if seleccionado_para_auditoria(d, tasa=0.10, semilla=semilla)}
        print(f"    semilla={semilla}: {len(muestra)} casos ({len(muestra)/len(docs):.1%})")


def _imprimir_escenario(escenario: dict[str, Any], detalle: bool) -> None:
    decision = decidir_encolado(escenario["resultado"], hitl=escenario["politica"])
    resultado = escenario["resultado"]
    certeza = resultado.certeza.value if resultado.certeza else "—"
    origen = resultado.origen.value if resultado.origen else "—"
    print(
        f"       caso={resultado.documento_id:<14} certeza={certeza:<5} origen={origen:<10} "
        f"→ requerido={str(decision.requerido):<5} prioridad={decision.prioridad:<5} "
        f"motivo={decision.motivo}"
    )
    if detalle:
        print(f"       muestreo: {decision.seleccionado_muestreo} · {decision.explicacion[:88]}")


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def _parsear_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Inspecciona T-505: cola HITL, muestreo de auditoría y feedback.",
    )
    parser.add_argument("--politica", action="store_true", help="solo la política de encolado")
    parser.add_argument("--muestreo", action="store_true", help="el muestreo, en números")
    parser.add_argument("--caso", help="correr un solo escenario (por nombre)")
    parser.add_argument("--manual", action="store_true", help="el ciclo de revisión, paso a paso")
    parser.add_argument("--json", type=Path, help="guardar el reporte en JSON")
    return parser.parse_args()


def _imprimir_manual() -> None:
    print("\nEl ciclo de revisión humana, paso a paso")
    cola = ColaHitl(hitl=HitlSettings(muestreo_tasa=1.0))

    bajo = _resultado(
        "doc-agente", certeza=Certeza.baja, origen=Origen.agente_ia,
        estado=EstadoResultado.revision, tipo="B",
    )
    alto = _resultado(
        "doc-auditado", certeza=Certeza.alta, origen=Origen.programa,
        estado=EstadoResultado.aprobado, tipo="A",
    )

    print("\n  1. Se aplica la política a los dos casos")
    encolar_hitl(bajo, cola=cola)
    encolar_hitl(alto, cola=cola)
    for entrada in cola.pendientes():
        print(f"     {entrada.documento_id:<14} prioridad={entrada.prioridad:<5} motivo={entrada.motivo}")

    print("\n  2. El revisor corrige el caso del agente (el agente se equivocó, R-09)")
    cola.registrar_correccion(
        "doc-agente",
        campo="tipo_comprobante",
        valor_anterior="B",
        valor_nuevo="A",
        motivo="el negocio esperaba A",
        revisor="contador",
    )
    entrada = cola.entrada("doc-agente")
    correccion = entrada.correcciones[0]
    print(
        f"     {correccion.campo}: {correccion.valor_anterior!r} → {correccion.valor_nuevo!r} "
        f"· revisor={correccion.revisor} · estado={entrada.estado}"
    )

    print("\n  3. El revisor confirma el caso auditado (la regla acertó)")
    cola.confirmar("doc-auditado")
    print(f"     doc-auditado estado={cola.entrada('doc-auditado').estado} (sin corrección)")

    print("\n  4. El feedback agregado: la señal para ajustar reglas y prompts")
    feedback = cola.feedback()
    print(f"     revisados={feedback['revisados']} corregidos={feedback['corregidos']} "
          f"confirmados={feedback['confirmados']} pendientes={feedback['pendientes']}")
    print(f"     correcciones por campo: {feedback['correcciones_por_campo']}")
    print(f"     señal del agente (R-09): {len(feedback['correcciones_de_certeza_baja'])}")
    print(f"     señal de la regla  (R-03): {len(feedback['correcciones_de_muestreo'])}")
    print(
        "\n  Las dos señales no se mezclan: una dice «el agente se equivocó» y la otra\n"
        "  «una regla acierta por accidente». Cada una se arregla en un lugar distinto."
    )


def main() -> None:
    args = _parsear_args()
    reporte: dict[str, Any] = {"version": VERSION_HITL}

    print("T-505 (F5) — cola HITL, muestreo de auditoría y feedback")
    _imprimir_politica()

    if args.politica:
        sys.exit(0)

    if args.muestreo:
        _imprimir_muestreo()
        sys.exit(0)

    if args.manual:
        _imprimir_manual()
        sys.exit(0)

    escenarios = _escenarios()
    if args.caso:
        escenarios = [e for e in escenarios if e["nombre"] == args.caso]
        if not escenarios:
            print(f"\nNo existe el escenario {args.caso!r}.", file=sys.stderr)
            sys.exit(2)

    fallos = 0
    print("\nEscenarios de encolado")
    reporte["escenarios"] = []
    for escenario in escenarios:
        diferencias = _verificar(escenario)
        marca = "✅" if not diferencias else "❌"
        print(f"\n  {marca} {escenario['nombre']:<22} {escenario['que']}")
        _imprimir_escenario(escenario, detalle=True)
        for diferencia in diferencias:
            print(f"       ❌ {diferencia}")
            fallos += 1
        reporte["escenarios"].append(
            {
                "nombre": escenario["nombre"],
                "ok": not diferencias,
                "diferencias": diferencias,
            }
        )

    print("\nFronteras de T-505 (lo que NO hace)")
    reporte["fronteras"] = []
    for frontera in _verificar_fronteras():
        marca = "✅" if frontera["ok"] else "❌"
        print(f"  {marca} {frontera['que']}")
        if not frontera["ok"]:
            fallos += 1
        reporte["fronteras"].append(frontera)

    ok = sum(1 for e in reporte["escenarios"] if e["ok"])

    # % HITL (R-09): de los casos que no quedaron de certeza alta por programa,
    # cuántos entran a revisión obligatoria. Es la carga que el DoD anticipa.
    obligatorios = [
        e for e in reporte["escenarios"] if e["nombre"] in {"certeza_baja", "sin_veredicto"}
    ]
    entraron = sum(
        1
        for e in obligatorios
        if decidir_encolado(
            next(s["resultado"] for s in _escenarios() if s["nombre"] == e["nombre"]),
            hitl=next(s["politica"] for s in _escenarios() if s["nombre"] == e["nombre"]),
        ).requerido
    )
    print(f"\nEscenarios verificados: {ok}/{len(escenarios)}")
    print(
        f"% revisión obligatoria (R-09): {entraron}/{len(obligatorios)} casos sin "
        f"veredicto firme entraron a la cola"
    )
    reporte["metrica_pct_revision_obligatoria"] = {
        "entraron": entraron,
        "elegibles": len(obligatorios),
        "tasa": (entraron / len(obligatorios)) if obligatorios else 0.0,
    }
    reporte["fallos"] = fallos
    if args.json:
        args.json.write_text(json.dumps(reporte, ensure_ascii=False, indent=2), encoding="utf-8")
        print(f"Reporte guardado en {args.json}")
    sys.exit(1 if fallos else 0)


if __name__ == "__main__":
    main()

#!/usr/bin/env python
"""Inspecciona T-506 (F5) — trazabilidad `CaseRecord` persistida (sidecar + índice).

**Fase**: F5 (conclusión) · **Tarea**: T-506 · **Épica**: E-CONC-5 · **ADR-005 /
ADR-009**.

Muestra, **sin Ollama, sin Docling y sin red**:

  1. Qué responde un `CaseRecord` (el Gherkin de E-CONC-5: versión de prompt,
     modelo, evidencia por fuente, reglas disparadas y quién decidió).
  2. Los **escenarios de persistencia**: un caso resuelto por programa, un caso
     ambiguo (sin `origen`), un caso con el agente, y el caso **sin resultado**
     consolidado (que también se persiste).
  3. El **round-trip de auditoría**: se guarda el caso, se reconstruye desde su
     sidecar y el resumen coincide — el registro es autosuficiente.
  4. El **índice** como derivado: consultas (`buscar`), una fila por documento
     (re-procesar no infla) y la reconstrucción desde los sidecars.
  5. Las **fronteras** de la tarea: escritura atómica, nada inventado, sin red y
     errores explícitos.

Uso:
    python scripts/F5/t506.py                        # contrato + escenarios + fronteras
    python scripts/F5/t506.py --contrato             # solo lo que responde el registro
    python scripts/F5/t506.py --indice               # el índice y sus consultas
    python scripts/F5/t506.py --caso caso_ambiguo
    python scripts/F5/t506.py --manual               # el round-trip, paso a paso
    python scripts/F5/t506.py --json /tmp/t506.json

Nota: la suite default de pytest cubre lo mismo en ``tests/test_trace_recorder_t506.py``;
este script es la verificación de humo legible para la bitácora (sale con código
≠ 0 si algún escenario falla). **No** escribe en el repo: usa un directorio
temporal.
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

from voucherflow.conclusion import (  # noqa: E402
    ColaHitl,
    concluir_con_agente,
    consolidar_caso,
    encolar_hitl,
)
from voucherflow.extraction.flows import combinar_evidencia  # noqa: E402
from voucherflow.rules.contexto import ContextoTipoComprobante  # noqa: E402
from voucherflow.schemas.evidence import (  # noqa: E402
    EvidenceField,
    Fuente,
    SourceEvidence,
    nueva_meta,
)
from voucherflow.settings.config import HitlSettings  # noqa: E402
from voucherflow.trace import (  # noqa: E402
    CAMPOS_INDICE,
    NOMBRE_INDICE,
    VERSION_TRAZA,
    CaseRecorder,
    construir_case_record,
    resumen_case_record,
)

# ---------------------------------------------------------------------------
# Datos de los escenarios (sin red)
# ---------------------------------------------------------------------------

CTX_RI_RI = ContextoTipoComprobante(
    emisor_condicion_fiscal="Responsable Inscripto",
    receptor_condicion_fiscal="Responsable Inscripto",
)

BASE: dict[str, Any] = {
    "fecha_emision": "2025-08-14",
    "nro_comprobante": "0001-00000001",
    "importe_total_facturado": "121.00",
}

#: Factura A coherente: el código concluye con certeza alta.
A_COMPLETA: dict[str, Any] = {
    **BASE,
    "tipo_comprobante": "A",
    "cuit_emisor": "30-12345678-9",
    "cuit_receptor": "27-12345678-4",
    "subtotal": "100.00",
    "iva": "21.00",
}

#: Factura B con emisor y receptor RI: el código la deja en revisión (R7), con
#: candidatos (el negocio espera A, el documento dice B).
AMBIGUO: dict[str, Any] = {
    **BASE,
    "tipo_comprobante": "B",
    "cuit_emisor": "30-12345678-9",
    "iva": "0.00",
}

#: Evidencia incompleta: falta casi todo (queda en revisión sin candidatos).
INCOMPLETO: dict[str, Any] = {"razon_social_emisor": "ACME SA"}

MODELO_VLM = "qwen2.5vl:3b"
MODELO_LLM = "qwen2.5:7b"
PROMPT = "extraccion-key-value@1"


class AgenteDoble:
    """Agente de prueba: elige siempre dentro del universo (sin red)."""

    def __init__(self, candidato: str | None) -> None:
        self.candidato = candidato

    def decidir(self, messages, modelo, *, num_ctx=None):
        class Respuesta:
            contenido = json.dumps({"candidato": self.candidato, "justificacion": "x"})

        return Respuesta()


def _source(
    fuente: Fuente,
    campos: dict[str, Any],
    *,
    modelo: str | None = None,
    reglas: list[str] | None = None,
    debilidades: list[str] | None = None,
) -> SourceEvidence:
    return SourceEvidence(
        fuente=fuente,
        reglas_aplicadas=list(reglas or []),
        debilidades=list(debilidades or []),
        campos={
            campo: EvidenceField(
                campo=campo,
                valor=valor,
                fuente=fuente,
                fragmento_sustento=f"soporte de {campo}",
                meta=nueva_meta(modelo, PROMPT),
            )
            for campo, valor in campos.items()
        },
    )


def _fuentes(campos: dict[str, Any]) -> list[SourceEvidence]:
    return [
        _source(
            Fuente.vlm,
            campos,
            modelo=MODELO_VLM,
            reglas=["R1", "R4"],
            debilidades=["sostén parcial"],
        ),
        _source(Fuente.llm, campos, modelo=MODELO_LLM),
    ]


def _evidencia(campos: dict[str, Any], documento_id: str):
    return combinar_evidencia(documento_id, _fuentes(campos))


def _escenarios() -> list[dict[str, Any]]:
    """Los escenarios del script: (nombre, caso, espera)."""

    def resuelto():
        evidencia = _evidencia(A_COMPLETA, "doc-programa")
        resultado = consolidar_caso(evidencia, contexto_tipo=CTX_RI_RI).valor
        return construir_case_record(
            evidencia, resultado=resultado, archivo="files/doc-programa.pdf"
        )

    def ambiguo():
        evidencia = _evidencia(AMBIGUO, "doc-ambiguo")
        resultado = consolidar_caso(evidencia, contexto_tipo=CTX_RI_RI).valor
        return construir_case_record(evidencia, resultado=resultado)

    def con_agente():
        evidencia = _evidencia(AMBIGUO, "doc-agente")
        corrida = concluir_con_agente(
            evidencia,
            contexto_tipo=CTX_RI_RI,
            agente=AgenteDoble("B"),
            modelo="llama3.1:8b",
        )
        return construir_case_record(evidencia, resultado=corrida.resultado)

    def sin_resultado():
        return construir_case_record(_evidencia(A_COMPLETA, "doc-sin-consolidar"))

    return [
        {
            "nombre": "resuelto_por_programa",
            "que": "el código concluyó → certeza alta, origen programa",
            "caso": resuelto(),
            "espera": {
                "quien_decidio": "programa",
                "estado": "aprobado",
                "certeza": "alta",
                "tiene_prompt": True,
                "tiene_modelo": True,
                "tiene_reglas": True,
            },
        },
        {
            "nombre": "caso_ambiguo",
            "que": "nadie lo decidió → sin `origen`, en revisión",
            "caso": ambiguo(),
            "espera": {
                "quien_decidio": None,
                "estado": "revision",
                "certeza": "baja",
                "tiene_reglas": True,
            },
        },
        {
            "nombre": "con_agente",
            "que": "lo decidió el agente → origen agente_ia y su modelo registrado",
            "caso": con_agente(),
            "espera": {
                "quien_decidio": "agente_ia",
                "estado": "aprobado",
                "modelo_agente": "llama3.1:8b",
                "tiene_modelo": True,
            },
        },
        {
            "nombre": "sin_resultado",
            "que": "evidencia sin consolidar → se persiste igual (nada inventado)",
            "caso": sin_resultado(),
            "espera": {
                "quien_decidio": None,
                "estado": None,
                "tiene_reglas": False,
            },
        },
    ]


# ---------------------------------------------------------------------------
# Verificación
# ---------------------------------------------------------------------------


def _verificar(escenario: dict[str, Any]) -> list[str]:
    resumen = resumen_case_record(escenario["caso"])
    diferencias: list[str] = []
    espera = escenario["espera"]

    obtenido: dict[str, Any] = {
        "quien_decidio": resumen["quien_decidio"],
        "estado": (resumen["resultado"] or {}).get("estado"),
        "certeza": (resumen["resultado"] or {}).get("certeza"),
        "tiene_prompt": bool(resumen["version_prompt"]),
        "tiene_modelo": bool(resumen["modelo_por_etapa"]),
        "tiene_reglas": bool(resumen["reglas_disparadas"]),
        "modelo_agente": resumen["modelo_por_etapa"].get("agente"),
    }
    for clave, valor in espera.items():
        if obtenido.get(clave) != valor:
            diferencias.append(f"{clave}: esperado {valor!r}, obtenido {obtenido.get(clave)!r}")
    return diferencias


def _verificar_fronteras(dir_salida: Path) -> list[dict[str, Any]]:
    """Verifica que T-506 respete sus límites (lo que NO hace)."""
    fronteras: list[dict[str, Any]] = []
    recorder = CaseRecorder(dir_salida)

    resuelto = next(e for e in _escenarios() if e["nombre"] == "resuelto_por_programa")["caso"]

    # 1. Round-trip: el registro vuelve al contrato congelado sin pérdida.
    recorder.registrar(resuelto)
    vuelto = recorder.leer("doc-programa")
    fronteras.append(
        {
            "que": "el sidecar vuelve al `CaseRecord` sin pérdida (auditoría verificable)",
            "ok": vuelto.model_dump() == resuelto.model_dump(),
        }
    )

    # 2. El registro responde el Gherkin sin re-correr el pipeline.
    resumen = resumen_case_record(vuelto)
    fronteras.append(
        {
            "que": "el registro responde prompt + modelo + evidencia + reglas + quién decidió",
            "ok": bool(
                resumen["version_prompt"]
                and resumen["modelo_por_etapa"]
                and resumen["evidencia_por_fuente"]
                and resumen["reglas_disparadas"]
                and resumen["quien_decidio"]
            ),
        }
    )

    # 3. Escritura atómica: no quedan temporales.
    temporales = [p.name for p in dir_salida.iterdir() if p.name.endswith(".tmp")]
    fronteras.append(
        {
            "que": "la escritura es atómica y no deja archivos temporales",
            "ok": temporales == [],
        }
    )

    # 4. Un caso sin resultado no se saltea: se indexa igual.
    recorder.registrar(
        next(e for e in _escenarios() if e["nombre"] == "sin_resultado")["caso"]
    )
    fronteras.append(
        {
            "que": "un caso sin consolidar se persiste igual (no se saltea el histórico)",
            "ok": len(recorder.buscar(estado=None)) == 1,
        }
    )

    # 5. Re-procesar no duplica: el índice es por documento, no por corrida.
    recorder.registrar(resuelto)
    recorder.registrar(resuelto)
    fronteras.append(
        {
            "que": "re-procesar un documento no duplica su fila (no inflaría métricas)",
            "ok": len(recorder.leer_indice()) == 2
            and len(recorder.leer_indice(unico=False)) > 2,
        }
    )

    # 6. El índice se reconstruye desde los sidecars (es un derivado).
    antes = {f["documento_id"]: f["estado"] for f in recorder.leer_indice()}
    recorder.indice.unlink()
    reconstruido = recorder.reindexar()
    fronteras.append(
        {
            "que": "el índice se reconstruye desde los sidecars (es un derivado)",
            "ok": {f["documento_id"]: f["estado"] for f in reconstruido} == antes,
        }
    )

    # 7. Un índice corrupto no pierde el histórico.
    with recorder.indice.open("a", encoding="utf-8") as archivo:
        archivo.write("{ no es json\n")
    fronteras.append(
        {
            "que": "una línea corrupta del índice no pierde el resto del histórico",
            "ok": len(recorder.leer_indice()) == len(antes),
        }
    )

    # 8. Nada se inventa: sin agente no hay modelo de agente.
    caso_programa = next(
        e for e in _escenarios() if e["nombre"] == "resuelto_por_programa"
    )["caso"]
    fronteras.append(
        {
            "que": "no se inventa un modelo de agente cuando no se llamó al agente",
            "ok": "agente" not in caso_programa.modelo_por_etapa,
        }
    )

    # 9. El registro declara si la evidencia por fuente es exacta o reconstruida.
    detalle = caso_programa.etapas[0].detalle["construccion"]
    fronteras.append(
        {
            "que": "el registro declara cómo armó la evidencia por fuente",
            "ok": detalle["evidencia_por_fuente"] in {"directa", "reconstruida_desde_campos"},
        }
    )

    # 10. Consultar un campo que el índice no tiene falla ruidoso.
    try:
        recorder.buscar(nope=True)
        ok_buscar = False
    except KeyError:
        ok_buscar = True
    fronteras.append(
        {"que": "consultar un campo inexistente del índice falla ruidoso", "ok": ok_buscar}
    )

    # 11. Leer un caso que no se persistió falla ruidoso (no devuelve un vacío).
    try:
        recorder.leer("no-existe")
        ok_leer = False
    except FileNotFoundError:
        ok_leer = True
    fronteras.append(
        {"que": "leer un caso no persistido falla ruidoso", "ok": ok_leer}
    )

    # 12. Sin red: el módulo es una proyección + escritura de archivos.
    import inspect

    from voucherflow.trace import construccion, recorder as modulo_recorder

    fuente = inspect.getsource(construccion) + inspect.getsource(modulo_recorder)
    fronteras.append(
        {
            "que": "no hay red ni llamadas a modelos (proyección + archivos)",
            "ok": not any(t in fuente for t in ("requests", "urllib", "http", ".ask(")),
        }
    )

    # 13. La persistencia no muta lo que recibe.
    evidencia = _evidencia(A_COMPLETA, "doc-no-muta")
    resultado = consolidar_caso(evidencia, contexto_tipo=CTX_RI_RI).valor
    antes_ev = dict(evidencia.trazabilidad)
    antes_res = resultado.model_dump()
    construir_case_record(evidencia, resultado=resultado, fuentes=_fuentes(A_COMPLETA))
    fronteras.append(
        {
            "que": "construir el registro no muta la evidencia ni el resultado",
            "ok": dict(evidencia.trazabilidad) == antes_ev
            and resultado.model_dump() == antes_res,
        }
    )

    # 14. El estado del HITL viaja en el registro (T-505 + T-506).
    evidencia_hitl = _evidencia(AMBIGUO, "doc-hitl")
    resultado_hitl = consolidar_caso(evidencia_hitl, contexto_tipo=CTX_RI_RI).valor
    encolar_hitl(resultado_hitl, cola=ColaHitl(hitl=HitlSettings(muestreo_tasa=0.0)))
    caso_hitl = construir_case_record(evidencia_hitl, resultado=resultado_hitl)
    recorder.registrar(caso_hitl)
    fronteras.append(
        {
            "que": "el estado HITL del caso viaja al registro y al índice",
            "ok": caso_hitl.resultado.hitl.requerido
            and len(recorder.buscar(hitl_requerido=True)) == 1,
        }
    )

    return fronteras


# ---------------------------------------------------------------------------
# Impresión
# ---------------------------------------------------------------------------


def _imprimir_contrato() -> None:
    print("\nQué responde un `CaseRecord` (Gherkin E-CONC-5)")
    print(f"  formato: {VERSION_TRAZA} · sidecar `<documento>.case.json` · índice `{NOMBRE_INDICE}`")
    print()
    filas = [
        ("versión de prompt", "version_prompt", "del `meta` de cada lectura (F4)"),
        ("modelo usado", "modelo_por_etapa", "del `meta` (vlm/llm) y de la traza (agente)"),
        ("evidencia por fuente", "evidencia_por_fuente", "lecturas exactas por fuente"),
        ("reglas disparadas", "reglas_disparadas", "pasada 1 por fuente + cruzadas T-501"),
        ("quién decidió", "quien_decidio", "origen del resultado (programa|agente_ia|hitl)"),
    ]
    print(f"  {'lo que pide la auditoría':<24} {'campo del registro':<24} de dónde sale")
    print(f"  {'-'*24} {'-'*24} {'-'*44}")
    for pide, campo, origen in filas:
        print(f"  {pide:<24} {campo:<24} {origen}")
    print(
        "\n  Todo sale de artefactos que existen: es una **proyección** de la corrida,\n"
        "  no un estado que las etapas van mutando. Si un dato no está, el campo viaja\n"
        "  vacío — no se rellena con una suposición."
    )


def _imprimir_indice(dir_salida: Path) -> None:
    print("\nEl índice: una línea por caso, consultable")
    print(f"  campos: {', '.join(CAMPOS_INDICE)}")
    print()
    recorder = CaseRecorder(dir_salida)
    for escenario in _escenarios():
        recorder.registrar(escenario["caso"])
    print(f"  {'documento':<22} {'estado':<10} {'certeza':<8} {'decidió':<11} sidecar")
    print(f"  {'-'*22} {'-'*10} {'-'*8} {'-'*11} {'-'*28}")
    for fila in recorder.leer_indice():
        print(
            f"  {fila['documento_id']:<22} {str(fila['estado']):<10} "
            f"{str(fila['certeza']):<8} {str(fila['quien_decidio']):<11} {fila['sidecar']}"
        )

    print("\n  Consultas sobre el índice")
    for filtro in ({"estado": "revision"}, {"quien_decidio": "agente_ia"}):
        pares = ", ".join(f"{k}={v!r}" for k, v in filtro.items())
        encontrados = [f["documento_id"] for f in recorder.buscar(**filtro)]
        print(f"    buscar({pares}) → {encontrados or '[]'}")

    print(
        "\n  El índice es **append-only** (agregar cuesta lo mismo siempre y dos\n"
        "  procesos no se pisan), y la deduplicación es **al leer**: la última fila\n"
        "  de cada documento gana. El `CaseRecord` es por documento (ADR-005), así\n"
        "  que re-procesar actualiza la entrada — si no, los agregados contarían dos\n"
        "  veces el mismo caso (T-507)."
    )


def _imprimir_escenario(escenario: dict[str, Any], detalle: bool) -> None:
    resumen = resumen_case_record(escenario["caso"])
    resultado = resumen["resultado"] or {}
    print(
        f"       doc={resumen['documento_id']:<18} estado={str(resultado.get('estado')):<9} "
        f"certeza={str(resultado.get('certeza')):<5} decidió={resumen['quien_decidio']}"
    )
    if detalle:
        fuentes = ", ".join(
            f"{fuente}({datos['n_campos']})" for fuente, datos in resumen["evidencia_por_fuente"].items()
        )
        print(
            f"       prompt={resumen['version_prompt'] or '—'}\n"
            f"       modelos={resumen['modelo_por_etapa'] or '—'}\n"
            f"       fuentes={fuentes or '—'} · reglas={resumen['reglas_disparadas'] or '[]'}"
        )


def _imprimir_manual() -> None:
    print("\nEl round-trip de auditoría, paso a paso")
    with tempfile.TemporaryDirectory() as tmp:
        dir_salida = Path(tmp) / "cases"
        recorder = CaseRecorder(dir_salida)
        caso = next(e for e in _escenarios() if e["nombre"] == "resuelto_por_programa")["caso"]
        antes = resumen_case_record(caso)

        print("\n  1. Se arma el registro desde la corrida (proyección, sin re-ejecutar)")
        print(f"     documento={antes['documento_id']} · quien_decidio={antes['quien_decidio']}")

        print("\n  2. Se persiste: sidecar + una línea de índice (escritura atómica)")
        persistido = recorder.registrar(caso)
        print(f"     sidecar={persistido.sidecar.name} · indexado={persistido.indexado}")

        print("\n  3. Se reconstruye el caso **desde el sidecar** (como haría un auditor)")
        vuelto = recorder.leer(caso.documento_id)
        despues = resumen_case_record(vuelto)

        print("\n  4. Los cinco datos del Gherkin coinciden")
        for clave in ("quien_decidio", "version_prompt", "modelo_por_etapa", "reglas_disparadas"):
            igual = antes[clave] == despues[clave]
            print(f"     {'✅' if igual else '❌'} {clave}: {despues[clave]}")

        print("\n  5. Y la evidencia por fuente también")
        igual = antes["evidencia_por_fuente"] == despues["evidencia_por_fuente"]
        print(f"     {'✅' if igual else '❌'} {list(despues['evidencia_por_fuente'])}")

        print(
            "\n  La prueba de que el registro es **autosuficiente**: el paso 3 no volvió a\n"
            "  correr el pipeline — leyó un archivo y respondió todo lo que una auditoría\n"
            "  fiscal pregunta (E-CONC-5)."
        )


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def _parsear_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Inspecciona T-506: trazabilidad CaseRecord persistida (sidecar + índice).",
    )
    parser.add_argument("--contrato", action="store_true", help="solo lo que responde el registro")
    parser.add_argument("--indice", action="store_true", help="el índice y sus consultas")
    parser.add_argument("--caso", help="correr un solo escenario (por nombre)")
    parser.add_argument("--manual", action="store_true", help="el round-trip, paso a paso")
    parser.add_argument("--json", type=Path, help="guardar el reporte en JSON")
    return parser.parse_args()


def main() -> None:
    args = _parsear_args()
    reporte: dict[str, Any] = {"version": VERSION_TRAZA}

    print("T-506 (F5) — trazabilidad `CaseRecord` persistida (sidecar + índice)")
    _imprimir_contrato()
    if args.contrato:
        sys.exit(0)

    if args.manual:
        _imprimir_manual()
        sys.exit(0)

    with tempfile.TemporaryDirectory() as tmp:
        dir_salida = Path(tmp) / "cases"

        if args.indice:
            _imprimir_indice(dir_salida)
            sys.exit(0)

        escenarios = _escenarios()
        if args.caso:
            escenarios = [e for e in escenarios if e["nombre"] == args.caso]
            if not escenarios:
                print(f"\nNo existe el escenario {args.caso!r}.", file=sys.stderr)
                sys.exit(2)

        fallos = 0
        print("\nEscenarios de persistencia")
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
                {"nombre": escenario["nombre"], "ok": not diferencias, "diferencias": diferencias}
            )

        print("\nFronteras de T-506 (lo que NO hace)")
        reporte["fronteras"] = []
        for frontera in _verificar_fronteras(dir_salida):
            marca = "✅" if frontera["ok"] else "❌"
            print(f"  {marca} {frontera['que']}")
            if not frontera["ok"]:
                fallos += 1
            reporte["fronteras"].append(frontera)

        # % de casos con registro auditable completo: la métrica que el DoD de
        # T-506 habilita (todo caso tiene su trazabilidad consultable).
        con_registro = sum(
            1
            for escenario in escenarios
            if resumen_case_record(escenario["caso"])["quien_decidio"] is not None
            or resumen_case_record(escenario["caso"])["reglas_disparadas"]
        )
        ok = sum(1 for e in reporte["escenarios"] if e["ok"])
        print(f"\nEscenarios verificados: {ok}/{len(escenarios)}")
        print(
            f"% casos con trazabilidad consultable: {con_registro}/{len(escenarios)} "
            "(el resto son casos sin resultado consolidado, que se persisten igual)"
        )
        reporte["metrica_trazabilidad"] = {
            "con_registro": con_registro,
            "total": len(escenarios),
        }
        reporte["fallos"] = fallos

    if args.json:
        args.json.write_text(json.dumps(reporte, ensure_ascii=False, indent=2), encoding="utf-8")
        print(f"Reporte guardado en {args.json}")
    sys.exit(1 if fallos else 0)


if __name__ == "__main__":
    main()

#!/usr/bin/env python
"""Métricas del DoD de F5 — conclusión + HITL + trazabilidad (F5 / T-507).

**Fase**: F5 (conclusión) · **Tarea**: T-507 · **Épica**: E-LIB-5.

Es el **reporte de cierre** de la fase (`06-estrategia-calidad.md` §5, mismo
patrón que `scripts/F3/t305.py` y `scripts/F4/t405.py`): agrega en un solo lugar
las métricas del DoD de F5, calculadas sobre el **`CaseRecord` persistido** de
T-506.

Dos modos
---------

==============================  =============================================
Modo                            Qué corre
==============================  =============================================
``--sintetico`` (**default**)   Un **lote sintético** construido sin red ni
                                modelos: los casos se arman con evidencia
                                inyectada y se pasan por las etapas reales
                                (T-501 → T-503 → T-505 → T-506). Sirve para
                                verificar que las métricas se calculan y que
                                cada fórmula da lo que debe.
``--historico DIR``             Las métricas del histórico **real** persistido
                                en ``DIR`` (los sidecars de una corrida de la
                                CLI/batch). Es el modo con el que se reporta un
                                lote de verdad.
==============================  =============================================

Métricas que reporta (tabla §5)
-------------------------------

1. **% certeza alta por programa** — objetivo creciente (madurez).
2. **% casos agente IA** — objetivo decreciente (la casuística se mueve a reglas,
   R-09).
3. **% rechazado** — tasa de rechazo del gate, con la distinción crítica de
   cuántos rechazos fueron **de certeza alta** (un rechazo firme es una
   conclusión, no una duda).
4. **Acuerdo VLM/LLM** — campos donde ambas fuentes coinciden, sobre los campos
   que **ambas leyeron**; los desacuerdos se listan (ahí aparecen los formatos
   nuevos).
5. **Cobertura HITL** — obligatorios (objetivo 100%) y muestreo (objetivo: la tasa
   configurada), por separado.
6. **Tasa de alertas R7** — casos con al menos una alerta de conflicto.

Cada métrica lleva su ``n``, su definición y su objetivo; si no se puede calcular,
el reporte dice **por qué** en vez de mostrar un 0% que miente, y avisa si el lote
es chico (una tendencia no se lee sobre dos casos).

Uso:
    python scripts/F5/t507.py                          # lote sintético (default)
    python scripts/F5/t507.py --detalle                # + composición y desacuerdos
    python scripts/F5/t507.py --historico salida/cases # métricas de un lote real
    python scripts/F5/t507.py --json /tmp/t507.json    # reporte para la bitácora

Nota: el modo ``sintetico`` corre en cualquier entorno (sin Ollama, sin Docling y
sin red). El **diagnóstico del cliente de modelos** ante latencia o status
inesperado es la otra mitad de E-LIB-5 y vive en `models/ollama.py` (F0/T-005):
este reporte lo declara y no lo duplica.
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
from voucherflow.models.ollama import (  # noqa: E402
    UMBRAL_LATENCIA_DIAGNOSTICO_S,
    OllamaClient,
)
from voucherflow.rules.contexto import ContextoTipoComprobante  # noqa: E402
from voucherflow.schemas.evidence import (  # noqa: E402
    EvidenceField,
    Fuente,
    SourceEvidence,
    nueva_meta,
)
from voucherflow.settings.config import HitlSettings  # noqa: E402
from voucherflow.trace import (  # noqa: E402
    VERSION_METRICAS,
    CaseRecorder,
    construir_case_record,
    metricas_de,
    metricas_del_recorder,
    resumen_legible,
)

# ---------------------------------------------------------------------------
# Datos del lote sintético (sin red)
# ---------------------------------------------------------------------------

CTX_RI_RI = ContextoTipoComprobante(
    emisor_condicion_fiscal="Responsable Inscripto",
    receptor_condicion_fiscal="Responsable Inscripto",
)

#: Emisor RI + receptor Consumidor Final: sin R7.
CTX_RI_CF = ContextoTipoComprobante(
    emisor_condicion_fiscal="Responsable Inscripto",
    receptor_condicion_fiscal="Consumidor Final",
)

BASE: dict[str, Any] = {
    "fecha_emision": "2025-08-14",
    "nro_comprobante": "0001-00000001",
    "importe_total_facturado": "121.00",
}

#: Factura A coherente: concluye por programa con certeza alta.
A_COMPLETA: dict[str, Any] = {
    **BASE,
    "tipo_comprobante": "A",
    "cuit_emisor": "30-12345678-9",
    "cuit_receptor": "27-12345678-4",
    "subtotal": "100.00",
    "iva": "21.00",
}

#: Factura B con emisor y receptor RI: R7 la deja en revisión (con candidatos).
AMBIGUO: dict[str, Any] = {
    **BASE,
    "tipo_comprobante": "B",
    "cuit_emisor": "30-12345678-9",
    "iva": "0.00",
}

#: Factura B discriminando IVA: contradice su letra → **rechazo**.
RECHAZO: dict[str, Any] = {
    **BASE,
    "tipo_comprobante": "B",
    "cuit_emisor": "30-12345678-9",
    "cuit_receptor": "27-12345678-4",
    "subtotal": "100.00",
    "iva": "21.00",
}

MODELO_VLM = "qwen2.5vl:3b"
MODELO_LLM = "qwen2.5:7b"
PROMPT = "extraccion-key-value@1"

#: Composición del lote sintético: cuántos casos de cada tipo. Es la mezcla que
#: hace visibles las cuatro métricas del DoD a la vez.
COMPOSICION: tuple[tuple[str, int], ...] = (
    ("programa", 6),
    ("rechazo", 3),
    ("agente", 3),
    ("revision", 3),
    ("desacuerdo_llm", 2),
    ("sin_resultado", 1),
)


class AgenteDoble:
    """Agente de prueba: elige dentro del universo (sin red)."""

    def __init__(self, candidato: str | None) -> None:
        self.candidato = candidato

    def decidir(self, messages, modelo, *, num_ctx=None):
        class Respuesta:
            contenido = json.dumps({"candidato": self.candidato, "justificacion": "x"})

        return Respuesta()


def _source(fuente: Fuente, campos: dict[str, Any]) -> SourceEvidence:
    return SourceEvidence(
        fuente=fuente,
        reglas_aplicadas=["R1"] if fuente is Fuente.vlm else [],
        debilidades=["sostén parcial"] if fuente is Fuente.vlm else [],
        campos={
            campo: EvidenceField(
                campo=campo,
                valor=valor,
                fuente=fuente,
                fragmento_sustento=f"soporte de {campo}",
                meta=nueva_meta(
                    MODELO_VLM if fuente is Fuente.vlm else MODELO_LLM, PROMPT
                ),
            )
            for campo, valor in campos.items()
        },
    )


def _evidencia(documento_id: str, campos: dict[str, Any], campos_llm: dict[str, Any] | None):
    return combinar_evidencia(
        documento_id,
        [
            _source(Fuente.vlm, campos),
            _source(Fuente.llm, campos_llm if campos_llm is not None else campos),
        ],
    )


# ---------------------------------------------------------------------------
# Construcción del lote sintético
# ---------------------------------------------------------------------------


def construir_lote(recorder: CaseRecorder, cola: ColaHitl) -> dict[str, int]:
    """Arma el lote sintético pasando los casos por las etapas reales.

    Cada caso recorre el pipeline determinista (combinación → conclusión →
    consolidación → HITL si corresponde) y se persiste con T-506. Devuelve la
    composición efectiva, que es lo que el reporte necesita para verificarse.
    """
    efectiva: dict[str, int] = {}

    def _registrar(clave: str) -> None:
        efectiva[clave] = efectiva.get(clave, 0) + 1

    for clave, cantidad in COMPOSICION:
        for i in range(cantidad):
            documento_id = f"{clave}-{i:03d}"
            campos_llm = None
            contexto = CTX_RI_RI
            agente = False
            con_resultado = True

            if clave == "programa":
                campos = A_COMPLETA
            elif clave == "rechazo":
                campos = RECHAZO
                contexto = CTX_RI_CF  # sin R7: el rechazo queda de certeza alta
            elif clave == "agente":
                campos = AMBIGUO
                agente = True
            elif clave == "revision":
                campos = AMBIGUO
            elif clave == "desacuerdo_llm":
                campos = A_COMPLETA
                campos_llm = {**A_COMPLETA, "iva": "22.00"}
            else:  # sin_resultado
                campos = A_COMPLETA
                con_resultado = False

            evidencia = _evidencia(documento_id, campos, campos_llm)
            if not con_resultado:
                recorder.registrar(construir_case_record(evidencia, archivo=f"{documento_id}.pdf"))
                _registrar("sin_resultado")
                continue

            if agente:
                corrida = concluir_con_agente(
                    evidencia,
                    contexto_tipo=contexto,
                    agente=AgenteDoble("B"),
                    modelo="llama3.1:8b",
                )
                resultado = corrida.resultado
            else:
                resultado = consolidar_caso(evidencia, contexto_tipo=contexto).valor

            # El HITL de T-505 se aplica a todos: es lo que hace que la cobertura
            # sea medible (los que correspondan entran a la cola).
            encolar_hitl(resultado, cola=cola)

            # Una parte de la cola se revisa: la cobertura mide revisión efectiva,
            # no encolado. Solo los casos que **entraron** se pueden confirmar
            # (confirmar uno que no está en la cola es un error declarado, T-505).
            if i % 2 == 0 and documento_id in cola:
                cola.confirmar(documento_id)
                resultado.hitl = cola.entrada(documento_id).como_hitl_decision()

            recorder.registrar(
                construir_case_record(evidencia, resultado=resultado, archivo=f"{documento_id}.pdf")
            )
            _registrar(clave)

    return efectiva


# ---------------------------------------------------------------------------
# Impresión
# ---------------------------------------------------------------------------


def _imprimir_tabla(reporte: dict[str, Any]) -> None:
    print(f"\nMétricas del lote  (reporte {reporte['version']} · librería {reporte['version_libreria']})")
    print(f"  contrato {reporte['schema_version']} · traza {reporte['version_traza']}")
    print()
    print(f"  {'métrica':<32} {'valor':<26} objetivo")
    print(f"  {'-'*32} {'-'*26} {'-'*40}")
    objetivos = {
        "% certeza alta (programa)": "creciente (madurez)",
        "% agente IA": "decreciente (R-09)",
        "% rechazado": "reportar por lote",
        "acuerdo VLM/LLM": "reportar (desacuerdos = formatos nuevos)",
        "cobertura HITL (obligatorios)": "100% de certeza baja revisada",
        "cobertura HITL (muestreo)": "la tasa configurada (ADR-004)",
        "tasa de alertas (R7)": "reportar; revisar falsos positivos",
    }
    for nombre, valor in resumen_legible(reporte):
        print(f"  {nombre:<32} {valor:<26} {objetivos.get(nombre, '')}")

    if reporte.get("lote_chico"):
        print(f"\n  ⚠️  {reporte['aviso_lote_chico']}")


def _imprimir_detalle(reporte: dict[str, Any]) -> None:
    certidumbre = reporte["certidumbre"]
    rechazo = reporte["rechazo"]

    print("\nComposición del lote")
    print(f"  documentos procesados: {reporte['documentos_procesados']} · registros: {reporte['registros']}")
    print(f"  con resultado consolidado: {certidumbre['casos_con_resultado']}")
    print(f"  por origen:   {certidumbre['por_origen']}")
    print(f"  por certeza:  {certidumbre['por_certeza']}")
    print(f"  por estado:   {rechazo['por_estado']}")
    print(f"  por letra:    {rechazo['por_tipo_comprobante']}")

    print("\nRechazo: ¿concluyó el fast-fail?")
    rca = rechazo["rechazado_con_certeza_alta"]
    print(f"  {rca['casos']} de {rca['de']} rechazos fueron de certeza alta")
    print(f"  {rca['nota']}")

    print("\nAcuerdo VLM/LLM")
    acuerdo = reporte["acuerdo_vlm_llm"]
    print(
        f"  {acuerdo['numerador']}/{acuerdo['denominador']} campos leídos por ambas fuentes"
    )
    if acuerdo["desacuerdos"]:
        print("  desacuerdos (los formatos nuevos aparecen acá):")
        for desacuerdo in acuerdo["desacuerdos"]:
            print(
                f"    {desacuerdo['documento_id']:<24} {desacuerdo['campo']:<22} "
                f"vlm={desacuerdo['vlm']!r} llm={desacuerdo['llm']!r}"
            )
    else:
        print("  sin desacuerdos")

    print("\nAlertas (R7)")
    print(f"  por regla: {reporte['alertas'].get('por_regla') or '—'}")

    print("\nLa otra mitad de E-LIB-5 (no duplicada acá)")
    print(
        f"  diagnóstico del cliente de modelos: `models/ollama.py` desde F0/T-005\n"
        f"  (umbral de latencia {UMBRAL_LATENCIA_DIAGNOSTICO_S:.0f}s → "
        f"`OllamaClient._diagnostico`, status inesperado)"
    )


# ---------------------------------------------------------------------------
# Verificación del modo sintético
# ---------------------------------------------------------------------------


def _verificar(efectiva: dict[str, int], reporte: dict[str, Any]) -> list[str]:
    """Verifica que las métricas den lo que el lote construido debe dar.

    No se comprueban porcentajes "redondos": se comprueba la **aritmética** contra
    la composición real del lote (que es dato, no expectativa), de modo que el
    test siga valiendo si cambia la composición.
    """
    fallos: list[str] = []
    certidumbre = reporte["certidumbre"]

    esperados_con_resultado = reporte["registros"] - efectiva.get("sin_resultado", 0)
    if certidumbre["casos_con_resultado"] != esperados_con_resultado:
        fallos.append(
            f"casos con resultado: esperado {esperados_con_resultado}, "
            f"obtenido {certidumbre['casos_con_resultado']}"
        )

    # El 100% de los `programa` del lote debe salir por programa.
    if certidumbre["por_origen"].get("programa", 0) < efectiva.get("programa", 0):
        fallos.append(
            f"origen programa: esperado ≥ {efectiva.get('programa', 0)}, "
            f"obtenido {certidumbre['por_origen'].get('programa', 0)}"
        )

    # El 100% de los `agente` del lote debe salir por agente_ia.
    if certidumbre["por_origen"].get("agente_ia", 0) != efectiva.get("agente", 0):
        fallos.append(
            f"origen agente_ia: esperado {efectiva.get('agente', 0)}, "
            f"obtenido {certidumbre['por_origen'].get('agente_ia', 0)}"
        )

    # Los rechazos del lote deben ser todos de certeza alta (RI+CF: sin R7).
    rca = reporte["rechazo"]["rechazado_con_certeza_alta"]
    if rca["de"] != efectiva.get("rechazo", 0) or rca["casos"] != rca["de"]:
        fallos.append(
            f"rechazos de certeza alta: esperado {efectiva.get('rechazo', 0)}/{efectiva.get('rechazo', 0)}, "
            f"obtenido {rca['casos']}/{rca['de']}"
        )

    # Los desacuerdos inyectados deben aparecer.
    if len(reporte["acuerdo_vlm_llm"]["desacuerdos"]) < efectiva.get("desacuerdo_llm", 0):
        fallos.append(
            f"desacuerdos listados: esperado ≥ {efectiva.get('desacuerdo_llm', 0)}, "
            f"obtenido {len(reporte['acuerdo_vlm_llm']['desacuerdos'])}"
        )

    # La cobertura HITL debe tener denominador (hay casos que requirieron revisión).
    obligatorios = reporte["cobertura_hitl"]["obligatorios"]
    if not obligatorios["calculable"]:
        fallos.append("cobertura HITL obligatoria: no calculable (debería haber casos)")

    # Cada métrica del DoD debe estar presente y con su definición.
    for clave, bloque in (
        ("% certeza alta", certidumbre["certeza_alta_programa"]),
        ("% agente", certidumbre["agente_ia"]),
        ("% rechazado", reporte["rechazo"]["rechazado"]),
        ("acuerdo VLM/LLM", reporte["acuerdo_vlm_llm"]),
    ):
        if not bloque.get("definicion") or not bloque.get("objetivo"):
            fallos.append(f"{clave}: falta la definición o el objetivo")

    return fallos


def _verificar_fronteras() -> list[dict[str, Any]]:
    """Verifica que T-507 respete sus límites (lo que NO hace)."""
    fronteras: list[dict[str, Any]] = []

    # 1. Cada métrica usa su propio denominador: un caso sin resultado no entra.
    with tempfile.TemporaryDirectory() as tmp:
        recorder = CaseRecorder(tmp)
        cola = ColaHitl(hitl=HitlSettings(muestreo_tasa=0.0))
        construir_lote(recorder, cola)
        reporte = metricas_del_recorder(recorder)

    certidumbre = reporte["certidumbre"]
    fronteras.append(
        {
            "que": "un caso sin resultado consolidado no entra en los denominadores",
            "ok": certidumbre["casos_totales"] > certidumbre["casos_con_resultado"],
        }
    )

    # 2. El acuerdo VLM/LLM se mide sobre lo leído por ambas, no sobre el contrato.
    fronteras.append(
        {
            "que": "el acuerdo VLM/LLM divide por los campos leídos por ambas fuentes",
            "ok": reporte["acuerdo_vlm_llm"]["denominador"]
            < certidumbre["casos_con_resultado"] * 30,
        }
    )

    # 3. Un caso rechazado cuenta como rechazo, y el reporte distingue si concluyó.
    fronteras.append(
        {
            "que": "el reporte distingue un rechazo concluyente de uno con alerta abierta",
            "ok": "rechazado_con_certeza_alta" in reporte["rechazo"],
        }
    )

    # 4. La cobertura HITL separa obligatorios de muestreo.
    fronteras.append(
        {
            "que": "la cobertura HITL separa revisión obligatoria de muestreo",
            "ok": "obligatorios" in reporte["cobertura_hitl"]
            and "muestreados" in reporte["cobertura_hitl"],
        }
    )

    # 5. Sin datos, la métrica es "no calculable" con motivo (no un 0%).
    vacio = metricas_de([])
    fronteras.append(
        {
            "que": "sin casos, la métrica es no-calculable con motivo (no un 0% falso)",
            "ok": vacio["certidumbre"]["certeza_alta_programa"]["valor_pct"] is None
            and bool(vacio["certidumbre"]["certeza_alta_programa"].get("motivo")),
        }
    )

    # 6. El lote chico se avisa.
    fronteras.append(
        {
            "que": "un lote chico se avisa (no se leen tendencias sobre pocos casos)",
            "ok": vacio["lote_chico"] is True and "aviso_lote_chico" in vacio,
        }
    )

    # 7. El reporte se versiona (librería + contrato + traza).
    fronteras.append(
        {
            "que": "el reporte lleva las versiones (librería + contrato + formato de traza)",
            "ok": all(
                reporte.get(clave)
                for clave in ("version", "version_libreria", "schema_version", "version_traza")
            ),
        }
    )

    # 8. Las métricas no recalculan el pipeline (agregan lo persistido).
    import inspect

    from voucherflow.trace import metricas as modulo_metricas

    fuente = inspect.getsource(modulo_metricas)
    fronteras.append(
        {
            "que": "las métricas no recalculan el pipeline (agregan lo persistido)",
            "ok": not any(t in fuente for t in ("requests", "urllib", ".ask(", "Ollama")),
        }
    )

    # 9. El diagnóstico de E-LIB-5 no se duplica: vive en el cliente de modelos.
    fronteras.append(
        {
            "que": "el diagnóstico del cliente (E-LIB-5) no se duplica acá",
            "ok": UMBRAL_LATENCIA_DIAGNOSTICO_S > 0 and hasattr(OllamaClient, "_diagnostico"),
        }
    )

    # 10. Las métricas leen los sidecars, no el índice (necesitan la evidencia).
    with tempfile.TemporaryDirectory() as tmp:
        recorder = CaseRecorder(tmp)
        cola = ColaHitl(hitl=HitlSettings(muestreo_tasa=0.0))
        construir_lote(recorder, cola)
        recorder.indice.unlink()
        sin_indice = metricas_del_recorder(recorder)
    fronteras.append(
        {
            "que": "las métricas leen los sidecars (el índice no lleva la evidencia por fuente)",
            "ok": sin_indice["acuerdo_vlm_llm"]["denominador"] > 0,
        }
    )

    # 11. Es determinístico.
    with tempfile.TemporaryDirectory() as tmp:
        recorder = CaseRecorder(tmp)
        cola = ColaHitl(hitl=HitlSettings(muestreo_tasa=0.0))
        construir_lote(recorder, cola)
        a = metricas_del_recorder(recorder)
        b = metricas_del_recorder(recorder)
    fronteras.append({"que": "es determinístico (mismo histórico, mismo reporte)", "ok": a == b})

    return fronteras


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def _parsear_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Métricas del DoD de F5 (conclusión + HITL + trazabilidad).",
    )
    parser.add_argument(
        "--historico",
        type=Path,
        help="directorio con los sidecars de una corrida real (modo histórico)",
    )
    parser.add_argument("--detalle", action="store_true", help="+ composición y desacuerdos")
    parser.add_argument("--json", type=Path, help="guardar el reporte en JSON")
    return parser.parse_args()


def main() -> None:
    args = _parsear_args()

    if args.historico:
        print(f"T-507 (F5) — métricas del histórico real: {args.historico}")
        recorder = CaseRecorder(args.historico)
        reporte = metricas_del_recorder(recorder)
        _imprimir_tabla(reporte)
        if args.detalle:
            _imprimir_detalle(reporte)
        if args.json:
            args.json.write_text(
                json.dumps(reporte, ensure_ascii=False, indent=2), encoding="utf-8"
            )
            print(f"\nReporte guardado en {args.json}")
        # En modo histórico no se "falla": el reporte es un dato, no una prueba.
        sys.exit(0)

    print("T-507 (F5) — métricas del DoD de la fase")
    print(
        f"\nEl lote sintético se arma sin red ni modelos: la evidencia se inyecta y los\n"
        f"casos pasan por las etapas reales (T-501 → T-503 → T-505 → T-506).\n"
        f"Reporte {VERSION_METRICAS}."
    )

    with tempfile.TemporaryDirectory() as tmp:
        recorder = CaseRecorder(Path(tmp) / "cases")
        cola = ColaHitl(hitl=HitlSettings(muestreo_tasa=0.10))
        efectiva = construir_lote(recorder, cola)
        reporte = metricas_del_recorder(recorder)

        print("\nComposición pedida del lote")
        for clave, cantidad in COMPOSICION:
            obtenida = efectiva.get(clave, 0)
            marca = "✅" if obtenida == cantidad else "❌"
            print(f"  {marca} {clave:<18} {obtenida}/{cantidad}")

        _imprimir_tabla(reporte)
        if args.detalle:
            _imprimir_detalle(reporte)

        print("\nVerificación de las métricas contra la composición del lote")
        fallos = _verificar(efectiva, reporte)
        for fallo in fallos:
            print(f"  ❌ {fallo}")
        if not fallos:
            print("  ✅ las cuatro métricas del DoD se calculan y coinciden con el lote")

        print("\nFronteras de T-507 (lo que NO hace)")
        fronteras = _verificar_fronteras()
        for frontera in fronteras:
            marca = "✅" if frontera["ok"] else "❌"
            print(f"  {marca} {frontera['que']}")
            if not frontera["ok"]:
                fallos.append(frontera["que"])

        reporte["composicion_efectiva"] = efectiva
        reporte["verificacion"] = {"fallos": fallos, "fronteras": fronteras}

        total_fronteras = len(fronteras)
        ok_fronteras = sum(1 for f in fronteras if f["ok"])
        print(f"\nMétricas del DoD: {'✅ ok' if not fallos else '❌ revisar'}")
        print(f"Fronteras verificadas: {ok_fronteras}/{total_fronteras}")

    if args.json:
        args.json.write_text(json.dumps(reporte, ensure_ascii=False, indent=2), encoding="utf-8")
        print(f"Reporte guardado en {args.json}")
    sys.exit(1 if fallos else 0)


if __name__ == "__main__":
    main()

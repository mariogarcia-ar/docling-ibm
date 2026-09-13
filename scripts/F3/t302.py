#!/usr/bin/env python
"""Inspecciona T-302 (F3) — evidencia de lectura del tipo/letra (VLM + LLM).

**Fase**: F3 (clasificación) · **Tarea**: T-302 · **Épica**: E-CLAS-1.

Muestra, **sin Ollama ni Docling** (los leedores se inyectan como dobles):

  1. El **prompt de evidencia versionado** (``tipo-comprobante@1``) de las dos
     fuentes: la base común (que declara que el modelo **no** decide), la guía
     de lectura del VLM (recuadro del encabezado) y la del LLM (texto OCR), con
     el esquema JSON que se le pide (``--prompt``).
  2. La **lectura** de cada fuente sobre un conjunto de **escenarios sintéticos**
     que cubren el recuadro (R4), el texto (R5), la discrepancia con alerta R7,
     la lectura sin letra, una letra fuera del vocabulario (``Z``/``090``), un
     JSON inválido y el caso de dos fuentes coincidentes/discrepantes.
  3. La **decisión final**, que la produce el motor de T-301 a partir de esa
     evidencia: el script encadena ``leer_evidencia`` (T-302) →
     ``clasificar_tipo_comprobante`` (T-301) y compara contra la expectativa, con
     ``--detalle`` para ver la evidencia y la traza de reglas.

Opcionalmente corre contra **Ollama real** con ``--origen`` (requiere los
servicios locales): procesa el documento con F1, prepara la vista de revisión de
F2 y lee la evidencia de verdad. Es la integración que F4/T-401 conectará.

Uso:
    python scripts/F3/t302.py                          # escenarios sintéticos
    python scripts/F3/t302.py --prompt                 # además, el prompt
    python scripts/F3/t302.py --detalle                # evidencia + traza
    python scripts/F3/t302.py --caso conflicto_R7_vlm_B
    python scripts/F3/t302.py --json /tmp/t302.json
    python scripts/F3/t302.py --origen files/2025-08/2D2C9343/<doc>.jpg --detalle

Nota: la suite default de pytest cubre lo mismo en
``tests/test_classification_prompt_tipo.py``; este script es la verificación de
humo legible para la bitácora (sale con código ≠ 0 si algún escenario falla).
"""

from __future__ import annotations

import argparse
import json
import sys
import tempfile
from dataclasses import asdict
from pathlib import Path
from typing import Any

# El paquete usa layout src/; si se corre sin instalar, se agrega src/ al path.
_SRC = Path(__file__).resolve().parents[2] / "src"
if str(_SRC) not in sys.path:
    sys.path.insert(0, str(_SRC))

from voucherflow.classification import (  # noqa: E402
    CAMPOS_EVIDENCIA,
    CAMPOS_FUERA_DEL_CONTRATO,
    EVIDENCIA_CAMPO_LETRA,
    FUENTES_LECTURA,
    VERSION_PROMPT_TIPO_COMPROBANTE,
    ErrorEvidencia,
    clasificar_tipo_comprobante,
    construir_source_evidence,
    leer_evidencia,
)
from voucherflow.classification.prompt_tipo_comprobante import (  # noqa: E402
    SYSTEM_PROMPT_POR_FUENTE,
)
from voucherflow.rules.contexto import (  # noqa: E402
    CONDICION_RI,
    ContextoTipoComprobante,
)
from voucherflow.validation.vistas import VistaPreparada  # noqa: E402

# ---------------------------------------------------------------------------
# Utilidades de escenario
# ---------------------------------------------------------------------------

#: PNG mínimo 1x1 válido para las vistas sintéticas (el armado de ``messages``
#: lee el archivo para codificarlo en base64, como en F2/T-202).
_PNG_1PX = (
    b"\x89PNG\r\n\x1a\n"
    b"\x00\x00\x00\rIHDR\x00\x00\x00\x01\x00\x00\x00\x01\x08\x02\x00\x00\x00"
    b"\x90wS\xde\x00\x00\x00\x0cIDATx\x9cc\xf8\xcf\xc0\x00\x00\x00\x03\x00\x01"
    b"\x86\xa0\xb5\x9d\x00\x00\x00\x00IEND\xaeB`\x82"
)


def _vista_imagen() -> VistaPreparada:
    """Vista de revisión sintética con imagen (modalidad VLM)."""
    tmp = tempfile.NamedTemporaryFile(suffix=".png", delete=False)
    tmp.write(_PNG_1PX)
    tmp.close()
    return VistaPreparada(
        tipo_vista="revision",
        calidad="media",
        representacion=tmp.name,
        resolucion_objetivo=1024,
        origen=tmp.name,
        ruta_imagen_original=tmp.name,
        nota="Vista de revisión sintética (T-302)",
    )


def _respuesta(**cambios: Any) -> str:
    """JSON de evidencia con los defaults del contrato, ajustable por kwargs."""
    datos: dict[str, Any] = {
        "tipo_detectado_por_documento": "A",
        "tipo_detectado_por_documento_explicacion": "Recuadro grande con 'A' (COD. 01)",
        "candidatos_descartados": [],
        "candidatos_restantes": [],
        "campos_desconocidos": [],
        "fuente_lectura": "vlm",
    }
    datos.update(cambios)
    return json.dumps(datos, ensure_ascii=False)


class LectorDoble:
    """Lector doble: responde por fuente y registra las llamadas (sin Ollama)."""

    def __init__(self, por_fuente: dict[str, str]) -> None:
        self.por_fuente = por_fuente
        self.llamadas: list[dict[str, Any]] = []

    def _fuente(self, messages: list[dict[str, Any]]) -> str:
        sistema = messages[0].get("content", "") if messages else ""
        for fuente, prompt in SYSTEM_PROMPT_POR_FUENTE.items():
            if prompt == sistema:
                return fuente
        return ""

    def ask(self, messages, model, json_format=False, options=None, num_ctx=None):
        fuente = self._fuente(messages)
        self.llamadas.append({"fuente": fuente, "model": model, "num_ctx": num_ctx})

        class _R:
            contenido = self.por_fuente.get(fuente, "{}")

        return _R()


#: Escenarios: (nombre, contexto fiscal base, respuestas por fuente, markdown,
#: con_vista, expectativa). La expectativa declara lo que el motor debe decidir
#: **a partir de la evidencia** (o el error de contrato esperado).
ESCENARIOS: list[dict[str, Any]] = [
    {
        "nombre": "vlm_recuadro_A",
        "contexto": {CONDICION_RI: CONDICION_RI},
        "respuestas": {"vlm": _respuesta(fuente_lectura="vlm")},
        "markdown": None,
        "vista": True,
        "expectativa": {
            "letra": "A",
            "certeza": "alta",
            "reglas": ["R2A", "R4"],
            "evidencias": 1,
        },
    },
    {
        "nombre": "llm_texto_A",
        "contexto": {CONDICION_RI: CONDICION_RI},
        "respuestas": {
            "llm": _respuesta(
                tipo_detectado_por_documento="A",
                tipo_detectado_por_documento_explicacion="FACTURA A COD. 001",
                fuente_lectura="llm",
            )
        },
        "markdown": "FACTURA A COD. 001\nProveedor: Ejemplo S.A.",
        "vista": False,
        "expectativa": {
            "letra": "A",
            "certeza": "alta",
            "reglas": ["R2A", "R5"],
            "evidencias": 1,
        },
    },
    {
        "nombre": "conflicto_R7_vlm_B",
        "contexto": {CONDICION_RI: CONDICION_RI},
        "respuestas": {
            "vlm": _respuesta(
                tipo_detectado_por_documento="B",
                tipo_detectado_por_documento_explicacion="Recuadro con 'B' (COD. 006)",
            )
        },
        "markdown": None,
        "vista": True,
        "expectativa": {
            "letra": "B",  # preferencia_letra="documento" (default de 11.1 de v1)
            "certeza": "baja",
            "reglas": ["R2A", "R4", "R7"],
            "alertas": ["R7"],
            "evidencias": 1,
        },
    },
    {
        "nombre": "conflicto_R7_preferencia_negocio",
        "contexto": {CONDICION_RI: CONDICION_RI},
        "respuestas": {
            "vlm": _respuesta(
                tipo_detectado_por_documento="B",
                tipo_detectado_por_documento_explicacion="Recuadro con 'B'",
            )
        },
        "markdown": None,
        "vista": True,
        "preferencia": "negocio",
        "expectativa": {
            "letra": "A",  # semántica del WIP: "la ley manda sobre el papel"
            "certeza": "baja",
            "reglas": ["R2A", "R4", "R7"],
            "alertas": ["R7"],
            "evidencias": 1,
        },
    },
    {
        "nombre": "vlm_sin_letra",
        "contexto": {CONDICION_RI: CONDICION_RI},
        "respuestas": {
            "vlm": _respuesta(
                tipo_detectado_por_documento=None,
                tipo_detectado_por_documento_explicacion=None,
            )
        },
        "markdown": None,
        "vista": True,
        "expectativa": {
            "letra": "A",  # decide el negocio; la lectura no aportó nada
            "certeza": "baja",
            "reglas": ["R2A"],
            "evidencias": 1,
            "campos_desconocidos": [EVIDENCIA_CAMPO_LETRA],
        },
    },
    {
        "nombre": "letra_fuera_de_vocabulario",
        "contexto": {CONDICION_RI: CONDICION_RI},
        "respuestas": {
            "vlm": _respuesta(
                tipo_detectado_por_documento="Z",
                tipo_detectado_por_documento_explicacion="Recuadro ilegible",
            )
        },
        "markdown": None,
        "vista": True,
        "expectativa": {
            "letra": "A",
            "certeza": "baja",
            "reglas": ["R2A"],
            "evidencias": 1,
            "letra_leida": None,  # no se inventa una letra válida (D-13)
        },
    },
    {
        "nombre": "json_invalido",
        "contexto": {CONDICION_RI: CONDICION_RI},
        "respuestas": {"vlm": "no soy JSON"},
        "markdown": None,
        "vista": True,
        "expectativa": {"error": ErrorEvidencia},
    },
    {
        "nombre": "dos_fuentes_coinciden",
        "contexto": {CONDICION_RI: CONDICION_RI},
        "respuestas": {
            "vlm": _respuesta(fuente_lectura="vlm"),
            "llm": _respuesta(
                tipo_detectado_por_documento_explicacion="FACTURA A COD. 001",
                fuente_lectura="llm",
            ),
        },
        "markdown": "FACTURA A",
        "vista": True,
        "expectativa": {
            "letra": "A",
            "certeza": "alta",
            "reglas": ["R2A", "R4"],
            "evidencias": 2,  # las dos lecturas se conservan (ADR-002)
            "fuentes": ["vlm", "llm"],
        },
    },
    {
        "nombre": "dos_fuentes_discrepan",
        "contexto": {CONDICION_RI: CONDICION_RI},
        "respuestas": {
            "vlm": _respuesta(fuente_lectura="vlm"),  # recuadro A
            "llm": _respuesta(
                tipo_detectado_por_documento="B",
                tipo_detectado_por_documento_explicacion="FACTURA B COD. 006",
                fuente_lectura="llm",
            ),
        },
        "markdown": "FACTURA B",
        "vista": True,
        "expectativa": {
            "letra": "A",  # R4 (recuadro) gana en la cascada sobre R5
            "certeza": "alta",
            "reglas": ["R2A", "R4"],
            "evidencias": 2,
            "fuentes": ["vlm", "llm"],
        },
    },
    {
        "nombre": "sin_insumo",
        "contexto": {CONDICION_RI: CONDICION_RI},
        "respuestas": {},
        "markdown": None,
        "vista": False,
        "expectativa": {
            "letra": "A",
            "certeza": "baja",
            "reglas": ["R2A"],
            "evidencias": 0,
            "fuentes": [],
        },
    },
]


# ---------------------------------------------------------------------------
# Impresión
# ---------------------------------------------------------------------------


def _imprimir_prompt() -> None:
    """Imprime el prompt de evidencia versionado de cada fuente."""
    print(f"Prompt de evidencia (versión: {VERSION_PROMPT_TIPO_COMPROBANTE})")
    print("=" * 78)
    print(f"\nCampos del contrato : {CAMPOS_EVIDENCIA}")
    print(f"Fuera del contrato  : {CAMPOS_FUERA_DEL_CONTRATO}")
    print("  (los calcula el motor de reglas T-301; el modelo no decide — ADR-006)")
    for fuente in FUENTES_LECTURA:
        print(f"\n--- system prompt · fuente '{fuente}' ---")
        print(SYSTEM_PROMPT_POR_FUENTE[fuente])
    print()


def _verificar(lectura, resultado, expectativa: dict[str, Any]) -> list[str]:
    """Compara la corrida real con la expectativa; devuelve las diferencias."""
    problemas: list[str] = []
    if "letra" in expectativa and expectativa["letra"] != resultado.letra:
        problemas.append(f"letra={resultado.letra!r} (esperado {expectativa['letra']!r})")
    if "certeza" in expectativa and expectativa["certeza"] != resultado.certeza:
        problemas.append(
            f"certeza={resultado.certeza!r} (esperado {expectativa['certeza']!r})"
        )
    if "reglas" in expectativa and sorted(expectativa["reglas"]) != sorted(
        resultado.reglas_aplicadas
    ):
        problemas.append(
            f"reglas={resultado.reglas_aplicadas} (esperado {expectativa['reglas']})"
        )
    if "alertas" in expectativa:
        disparadas = [alerta["regla"] for alerta in resultado.alertas]
        if sorted(expectativa["alertas"]) != sorted(disparadas):
            problemas.append(f"alertas={disparadas} (esperado {expectativa['alertas']})")
    if "evidencias" in expectativa and expectativa["evidencias"] != len(lectura.evidencias):
        problemas.append(
            f"evidencias={len(lectura.evidencias)} (esperado {expectativa['evidencias']})"
        )
    if "fuentes" in expectativa:
        fuentes = [evidencia.fuente for evidencia in lectura.lecturas]
        if expectativa["fuentes"] != fuentes:
            problemas.append(f"fuentes={fuentes} (esperado {expectativa['fuentes']})")
    if "letra_leida" in expectativa:
        leidas = [evidencia.letra for evidencia in lectura.lecturas]
        esperado = expectativa["letra_leida"]
        if any(letra != esperado for letra in leidas):
            problemas.append(f"letras_leidas={leidas} (esperado {esperado!r})")
    if "campos_desconocidos" in expectativa:
        faltan = [
            campo
            for campo in expectativa["campos_desconocidos"]
            if all(campo not in evidencia.campos_desconocidos for evidencia in lectura.lecturas)
        ]
        if faltan:
            problemas.append(f"campos_desconocidos sin {faltan}")
    return problemas


def _imprimir_escenario(
    escenario: dict[str, Any],
    lectura,
    resultado,
    problemas: list[str],
    *,
    detalle: bool,
) -> None:
    """Imprime una línea por escenario (y la evidencia si ``--detalle``)."""
    expectativa = escenario["expectativa"]
    estado = "✅" if not problemas else "❌"
    if "error" in expectativa:
        print(f"  {estado} {escenario['nombre']:<32} error esperado: {expectativa['error'].__name__}")
        return
    letra = resultado.letra if resultado.letra is not None else "-"
    fuentes = ",".join(evidencia.fuente for evidencia in lectura.lecturas) or "-"
    print(
        f"  {estado} {escenario['nombre']:<32} "
        f"fuentes={fuentes:<8} "
        f"letra={letra:<3} certeza={resultado.certeza or '-':<5} "
        f"reglas={','.join(resultado.reglas_aplicadas) or '-'}"
    )
    for evidencia in lectura.lecturas:
        leida = evidencia.letra if evidencia.letra is not None else "-"
        estado_ev = "válida" if evidencia.valida else "débil"
        print(
            f"      · {evidencia.fuente}: letra={leida} ({estado_ev}) "
            f"restantes={evidencia.candidatos_restantes or '-'} "
            f"descartados={evidencia.candidatos_descartados or '-'}"
        )
        if evidencia.fragmento:
            print(f"        sustento: {evidencia.fragmento[:90]}")
        if evidencia.campos_desconocidos:
            print(f"        campos desconocidos: {', '.join(evidencia.campos_desconocidos)}")
        for problema in evidencia.problemas:
            print(f"        ⚠ {problema}")
        if detalle:
            campo = construir_source_evidence(evidencia).campos[EVIDENCIA_CAMPO_LETRA]
            print(
                f"        evidencia: campo={campo.campo} fuente={campo.fuente.value} "
                f"confianza={campo.confianza_fuente} "
                f"prompt={campo.meta['version_prompt']}"
            )
    if resultado.alertas:
        print(f"      ⚠ {resultado.alertas[0]['mensaje'].split('.')[0]}.")
    if problemas:
        print(f"      ❌ no coincide con la expectativa: {'; '.join(problemas)}")
    if detalle:
        print(f"      criterio  : {resultado.detalle.get('criterio', '')}")
        print(f"      disparadas: {resultado.detalle.get('reglas_disparadas', {})}")


def _contexto_base(escenario: dict[str, Any]) -> ContextoTipoComprobante:
    """Contexto con las condiciones fiscales del escenario (parte de negocio)."""
    condiciones = escenario["contexto"]
    return ContextoTipoComprobante(
        emisor_condicion_fiscal=condiciones.get(CONDICION_RI),
        receptor_condicion_fiscal=condiciones.get(CONDICION_RI),
    )


def _correr_escenario(escenario: dict[str, Any]) -> tuple[Any, Any, Any]:
    """Corre un escenario sintético: T-302 (leer) → T-301 (decidir).

    Devuelve ``(lectura, resultado, error)``: ``error`` no es ``None`` cuando la
    expectativa era un error de contrato (JSON inválido).
    """
    lector = LectorDoble(escenario["respuestas"])
    lectura = leer_evidencia(
        lector,
        markdown=escenario["markdown"],
        vista=_vista_imagen() if escenario["vista"] else None,
        documento_id=escenario["nombre"],
        contexto_base=_contexto_base(escenario),
    )
    if lectura.lecturas == []:
        # Sin ninguna lectura no hay contexto de evidencia: se decide solo con
        # el negocio (el motor no necesita lectura para R1/R2A/R2B).
        contexto = _contexto_base(escenario)
    else:
        contexto = lectura.contexto
    resultado = clasificar_tipo_comprobante(
        contexto, preferencia_letra=escenario.get("preferencia", "documento")
    )
    return lectura, resultado, None


def _correr_escenario_seguro(escenario: dict[str, Any]) -> tuple[Any, Any, Exception | None]:
    """Como :func:`_correr_escenario` pero captura el error de contrato."""
    try:
        lectura, resultado, _ = _correr_escenario(escenario)
        return lectura, resultado, None
    except ErrorEvidencia as exc:
        return None, None, exc


def main() -> None:
    parser = argparse.ArgumentParser(
        description=(
            "Inspecciona la evidencia de lectura del tipo/letra (F3 / T-302): "
            "prompt versionado, lectura por fuente y decisión del motor T-301. "
            "Los escenarios sintéticos no requieren Ollama."
        )
    )
    parser.add_argument(
        "--caso",
        action="append",
        help="Filtra por nombre de escenario (repetible). Default: todos.",
    )
    parser.add_argument("--prompt", action="store_true", help="Imprime el prompt de evidencia.")
    parser.add_argument("--detalle", action="store_true", help="Evidencia y traza por escenario.")
    parser.add_argument("--json", type=Path, help="Escribe el reporte completo en JSON.")
    parser.add_argument(
        "--origen",
        type=Path,
        help=(
            "Corre contra Ollama REAL sobre un documento (F1 + vista de revisión "
            "de F2). Requiere Ollama y Docling locales."
        ),
    )
    parser.add_argument("--modelo", help="Modelo explícito (solo con --origen).")
    args = parser.parse_args()

    if args.prompt:
        _imprimir_prompt()

    reporte: dict[str, Any] = {
        "tarea": "T-302",
        "fase": "F3",
        "version_prompt": VERSION_PROMPT_TIPO_COMPROBANTE,
        "escenarios": [],
    }
    fallos = 0

    # --- Modo real (--origen): F1 + vista de F2 + Ollama real --------------
    if args.origen is not None:
        if not args.origen.exists():
            print(f"No existe el documento: {args.origen}", file=sys.stderr)
            sys.exit(2)
        reporte["origen"] = _correr_origen(args.origen, args.modelo, args.detalle)
        if reporte["origen"].get("error"):
            fallos += 1

    # --- Escenarios sintéticos --------------------------------------------
    seleccionados = [
        escenario
        for escenario in ESCENARIOS
        if not args.caso or escenario["nombre"] in set(args.caso)
    ]
    if args.caso and not seleccionados:
        print(
            f"Ningún escenario coincide con {args.caso}. "
            f"Disponibles: {', '.join(e['nombre'] for e in ESCENARIOS)}",
            file=sys.stderr,
        )
        sys.exit(2)

    if seleccionados:
        print(f"Escenarios de evidencia de lectura (prompt {VERSION_PROMPT_TIPO_COMPROBANTE})")
        print("=" * 78)
        for escenario in seleccionados:
            expectativa = escenario["expectativa"]
            lectura, resultado, error = _correr_escenario_seguro(escenario)
            if "error" in expectativa:
                coincide = isinstance(error, expectativa["error"])
                problemas = [] if coincide else [f"error={type(error).__name__} (esperado {expectativa['error'].__name__})"]
                estado = "✅" if coincide else "❌"
                print(
                    f"  {estado} {escenario['nombre']:<32} "
                    f"error esperado {expectativa['error'].__name__}: "
                    f"{'sí' if coincide else 'no'}"
                )
                fallos += int(not coincide)
                reporte["escenarios"].append(
                    {
                        "nombre": escenario["nombre"],
                        "coincide": coincide,
                        "diferencias": problemas,
                        "error": type(error).__name__ if error else None,
                    }
                )
                continue
            problemas = _verificar(lectura, resultado, expectativa)
            fallos += int(bool(problemas))
            _imprimir_escenario(escenario, lectura, resultado, problemas, detalle=args.detalle)
            reporte["escenarios"].append(
                {
                    "nombre": escenario["nombre"],
                    "coincide": not problemas,
                    "diferencias": problemas,
                    "evidencias": [
                        {
                            "fuente": evidencia.fuente,
                            "letra": evidencia.letra,
                            "fragmento": evidencia.fragmento,
                            "candidatos_descartados": evidencia.candidatos_descartados,
                            "candidatos_restantes": evidencia.candidatos_restantes,
                            "campos_desconocidos": evidencia.campos_desconocidos,
                            "problemas": evidencia.problemas,
                            "valida": evidencia.valida,
                        }
                        for evidencia in lectura.lecturas
                    ],
                    "resultado": asdict(resultado),
                    "detalle_corrida": lectura.detalle,
                }
            )
        print()
        total = len(seleccionados)
        print(f"Escenarios verificados: {total - fallos}/{total} coinciden con la expectativa.")
        reporte["escenarios_total"] = total
        reporte["escenarios_ok"] = total - fallos

    if args.json:
        args.json.write_text(
            json.dumps(reporte, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        print(f"Reporte JSON: {args.json}")

    if fallos:
        sys.exit(1)


def _correr_origen(origen: Path, modelo: str | None, detalle: bool) -> dict[str, Any]:
    """Corre la lectura real (F1 + F2 + Ollama) sobre un documento.

    Es la integración que F4/T-401 conectará: procesa el documento con F1,
    prepara la **vista de revisión** de F2 (imagen reducida a 1024 px o markdown)
    y llama a :func:`leer_evidencia` con el ``OllamaClient`` real. Cualquier
    falla (Docling/Ollama) se reporta sin tumbar el resto del script.
    """
    print(f"Documento real: {origen} (F1 + vista de revisión F2 + Ollama real)")
    print("=" * 78)
    resultado_reporte: dict[str, Any] = {"origen": str(origen)}
    try:
        from voucherflow.api import process
        from voucherflow.models.ollama import OllamaClient
        from voucherflow.validation.vistas import preparar_vista_revision

        documento = process(str(origen))
        vista = preparar_vista_revision(documento, str(origen))
        lectura = leer_evidencia(
            OllamaClient(),
            markdown=documento.markdown,
            vista=vista,
            documento_id=origen.name,
            modelo=modelo,
        )
        print(f"tipo_entrada={documento.tipo_entrada} vista={vista.tipo_vista}/{vista.calidad}")
        for evidencia in lectura.lecturas:
            print(
                f"  · {evidencia.fuente}: letra={evidencia.letra or '-'} "
                f"válida={evidencia.valida}"
            )
            if evidencia.fragmento:
                print(f"    sustento: {evidencia.fragmento[:200]}")
            for problema in evidencia.problemas:
                print(f"    ⚠ {problema}")
        resultado = clasificar_tipo_comprobante(
            lectura.contexto or ContextoTipoComprobante()
        )
        print(
            f"  decisión (T-301): letra={resultado.letra or '-'} "
            f"certeza={resultado.certeza} reglas={resultado.reglas_aplicadas}"
        )
        if detalle:
            print(f"  detalle: {json.dumps(lectura.detalle, ensure_ascii=False)}")
        resultado_reporte["lectura"] = {
            "fuentes": [evidencia.fuente for evidencia in lectura.lecturas],
            "letras": {evidencia.fuente: evidencia.letra for evidencia in lectura.lecturas},
            "decision": asdict(resultado),
        }
    except Exception as exc:  # noqa: BLE001 - herramienta manual: se reporta todo
        print(f"  ❌ {type(exc).__name__}: {exc}", file=sys.stderr)
        resultado_reporte["error"] = f"{type(exc).__name__}: {exc}"
    print()
    return resultado_reporte


if __name__ == "__main__":
    main()

#!/usr/bin/env python
"""Inspecciona T-401 (F4) — flujos VLM y LLM en **paralelo** con evidencia.

**Fase**: F4 (extracción) · **Tarea**: T-401 · **Épica**: E-EXT-1.

Muestra, **sin Ollama ni Docling** (los lectores se inyectan como dobles):

  1. El **prompt de evidencia versionado** (``extraccion-key-value@1``) de las dos
     fuentes: la base común (que declara que el modelo **no** normaliza ni
     decide), la guía del VLM (imagen) y la del LLM (texto OCR), con la lista de
     campos y el esquema JSON del contrato (``--prompt``).
  2. La **evidencia por fuente** sobre un conjunto de **escenarios sintéticos**
     que cubren los casos que el DoD de T-401 exige: las dos fuentes
     coincidiendo, la discrepancia entre fuentes (que T-401 **no** resuelve:
     conserva ambas), el campo sin sustento, el valor fuera del vocabulario, el
     CUIT que el fragmento no sostiene, el shape plano de v1 (``kvi``/``kvg``),
     la ausencia de una fuente (sin vista) y el JSON inválido.
  3. El **paralelismo real**: corre el escenario con una demora artificial por
     fuente y muestra que el total ≈ el **máximo** de las dos llamadas y no la
     suma — que es el motivo de correr los flujos en paralelo (E-EXT-1).

Opcionalmente corre contra **Ollama real** con ``--origen``: procesa el
documento con F1, prepara la **vista fiel** de F2 (E-QWE-2) y extrae la evidencia
de verdad. Es el punto de entrada que consumirán T-404 (combinación) y T-405
(paridad).

Uso:
    python scripts/F4/t401.py                            # escenarios sintéticos
    python scripts/F4/t401.py --prompt                   # además, el prompt
    python scripts/F4/t401.py --detalle                  # campos y debilidades
    python scripts/F4/t401.py --caso discrepancia_fuentes
    python scripts/F4/t401.py --json /tmp/t401.json
    python scripts/F4/t401.py --origen files/2025-08/2D2C9343/<doc>.jpg --detalle

Nota: la suite default de pytest cubre lo mismo en
``tests/test_extraction_flujos.py``; este script es la verificación de humo
legible para la bitácora (sale con código ≠ 0 si algún escenario falla).
"""

from __future__ import annotations

import argparse
import json
import sys
import tempfile
import time
from pathlib import Path
from typing import Any

# El paquete usa layout src/; si se corre sin instalar, se agrega src/ al path.
_SRC = Path(__file__).resolve().parents[2] / "src"
if str(_SRC) not in sys.path:
    sys.path.insert(0, str(_SRC))

from voucherflow.extraction import (  # noqa: E402
    CAMPO_FUENTE_LECTURA,
    CAMPOS_EXTRACCION,
    CLAVE_CAMPOS,
    FUENTES_EXTRACCION,
    VERSION_PROMPT_EXTRACCION,
    ErrorExtraccion,
    ejecutar_flujo,
    extraer_evidencia,
)
from voucherflow.extraction.prompt_extraccion import (  # noqa: E402
    SYSTEM_PROMPT_POR_FUENTE,
)
from voucherflow.settings.config import cargar_desde_dict  # noqa: E402
from voucherflow.validation.vistas import VistaPreparada  # noqa: E402

# ---------------------------------------------------------------------------
# Utilidades de escenario (mismo patrón que los scripts de F3)
# ---------------------------------------------------------------------------

#: PNG mínimo 1x1 válido para las vistas sintéticas (el armado de ``messages``
#: lee el archivo para codificarlo en base64, como en F2/T-202 y F3/T-302).
_PNG_1PX = (
    b"\x89PNG\r\n\x1a\n"
    b"\x00\x00\x00\rIHDR\x00\x00\x00\x01\x00\x00\x00\x01\x08\x02\x00\x00\x00"
    b"\x90wS\xde\x00\x00\x00\x0cIDATx\x9cc\xf8\xcf\xc0\x00\x00\x00\x03\x00\x01"
    b"\x86\xa0\xb5\x9d\x00\x00\x00\x00IEND\xaeB`\x82"
)


def _vista_imagen() -> VistaPreparada:
    """Vista **fiel** sintética con imagen (la entrada de la extracción)."""
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
        nota="Vista fiel sintética (T-401)",
    )


def _campo(valor: Any, sustento: str) -> dict[str, Any]:
    """Campo con el shape del contrato (``valor`` + ``fragmento_sustento``)."""
    return {"valor": valor, "fragmento_sustento": sustento}


def _respuesta(fuente: str, **campos: Any) -> str:
    """JSON de evidencia de extracción con la fuente y los campos indicados."""
    datos = {
        CAMPO_FUENTE_LECTURA: fuente,
        CLAVE_CAMPOS: campos,
    }
    return json.dumps(datos, ensure_ascii=False)


#: Respuesta "completa" de referencia (usada como base por los escenarios).
#: Declara los **dos CUIT** porque desde T-403/E-EXT-2 una Factura A sin el CUIT
#: del receptor es una fuente internamente incoherente.
_CAMPOS_BASE: dict[str, Any] = {
    "tipo_comprobante": _campo("A", "Recuadro grande con 'A' y COD. 01"),
    "razon_social_emisor": _campo("ACME S.A.", "ACME S.A."),
    "cuit_emisor": _campo("20-12345678-9", "C.U.I.T. 20-12345678-9"),
    "cuit_receptor": _campo("27-30111222-4", "C.U.I.T. 27-30111222-4"),
    "fecha_emision": _campo("14/08/2025", "Fecha: 14/08/2025"),
    "importe_total_facturado": _campo(12345.67, "Importe Total: $12.345,67"),
}


def _respuesta_base(fuente: str, **cambios: Any) -> str:
    """Respuesta de referencia con los cambios pedidos sobre los campos base."""
    campos = dict(_CAMPOS_BASE)
    campos.update(cambios)
    return _respuesta(fuente, **campos)


class LectorDoble:
    """Lector doble: responde por fuente, registra llamadas y puede demorar.

    Detecta la fuente por el system prompt del primer mensaje (distinto para VLM
    y LLM), como los dobles de la suite. ``demora_s`` permite medir el
    paralelismo real; ``falla`` simula una fuente caída.
    """

    def __init__(
        self,
        por_fuente: dict[str, str],
        *,
        demora_s: float = 0.0,
        falla: str | None = None,
    ) -> None:
        self.por_fuente = por_fuente
        self.demora_s = demora_s
        self.falla = falla
        self.llamadas: list[dict[str, Any]] = []

    def _fuente(self, messages: list[dict[str, Any]]) -> str:
        sistema = messages[0].get("content", "") if messages else ""
        for fuente, prompt in SYSTEM_PROMPT_POR_FUENTE.items():
            if prompt == sistema:
                return fuente
        return ""

    def ask(self, messages, model, json_format=False, options=None, num_ctx=None):
        if self.demora_s:
            time.sleep(self.demora_s)
        fuente = self._fuente(messages)
        self.llamadas.append({"fuente": fuente, "model": model, "num_ctx": num_ctx})
        if self.falla and fuente == self.falla:
            raise RuntimeError(f"Ollama no responde para la fuente {fuente}")

        class _Respuesta:
            def __init__(self, contenido: str) -> None:
                self.contenido = contenido

        return _Respuesta(
            self.por_fuente.get(fuente, "{}")
        )


def _settings() -> Any:
    """``Settings`` de prueba con los roles ``vlm``/``llm`` (sin Ollama real)."""
    return cargar_desde_dict(
        {
            "modelos": {
                "vlm": {"rol": "vlm", "modelo": "vlm-doble", "num_ctx": 4096},
                "llm": {"rol": "llm", "modelo": "llm-doble", "num_ctx": 8192},
            }
        }
    )


# ---------------------------------------------------------------------------
# Escenarios sintéticos
# ---------------------------------------------------------------------------

ESCENARIOS: list[dict[str, Any]] = [
    {
        "nombre": "dos_fuentes_coinciden",
        "que": "las dos fuentes corren en paralelo y aportan la misma lectura",
        "por_fuente": {
            "vlm": _respuesta_base("vlm"),
            "llm": _respuesta_base("llm"),
        },
        "expectativa": {
            "corridas": ["vlm", "llm"],
            "fuentes_validas": ["vlm", "llm"],
            "valores": {
                ("vlm", "tipo_comprobante"): "A",
                ("llm", "tipo_comprobante"): "A",
                ("llm", "cuit_emisor"): "20-12345678-9",
            },
            "debilidades_vacias": ["vlm", "llm"],
        },
    },
    {
        "nombre": "discrepancia_conserva_ambas",
        "que": "VLM y LLM discrepan: T-401 conserva las dos evidencias (T-404 resuelve)",
        "por_fuente": {
            "vlm": _respuesta_base("vlm", tipo_comprobante=_campo("A", "'A' COD. 01")),
            "llm": _respuesta_base("llm", tipo_comprobante=_campo("B", "FACTURA B")),
        },
        "expectativa": {
            "corridas": ["vlm", "llm"],
            "valores": {
                ("vlm", "tipo_comprobante"): "A",
                ("llm", "tipo_comprobante"): "B",
            },
        },
    },
    {
        "nombre": "campo_sin_sustento",
        "que": "un campo declarado sin fragmento de sustento queda como debilidad",
        "por_fuente": {
            "vlm": _respuesta("vlm", tipo_comprobante={"valor": "A"}),
            "llm": _respuesta_base("llm"),
        },
        "expectativa": {
            "corridas": ["vlm", "llm"],
            "fuentes_validas": ["llm"],
            "debilidades_contienen": {"vlm": "sustento"},
        },
    },
    {
        "nombre": "valor_fuera_del_vocabulario",
        "que": "un tipo de comprobante 'X' invalida la evidencia de esa fuente",
        "por_fuente": {
            "vlm": _respuesta_base("vlm", tipo_comprobante=_campo("X", "Recuadro 'X'")),
            "llm": _respuesta_base("llm"),
        },
        "expectativa": {
            "corridas": ["vlm", "llm"],
            "fuentes_invalidas": ["vlm"],
            "reglas_raw": {"vlm": "RAW_VOCABULARIO"},
            # El valor crudo se conserva para auditoría (no se inventa una letra).
            "valores": {("vlm", "tipo_comprobante"): "X"},
        },
    },
    {
        "nombre": "cuit_que_el_fragmento_no_sostiene",
        "que": "el CUIT declarado no está en el fragmento: indicio, no prueba",
        "por_fuente": {
            "vlm": _respuesta_base("vlm"),
            "llm": _respuesta_base(
                "llm",
                cuit_emisor=_campo("20-99999999-9", "C.U.I.T. 20-12345678-9"),
            ),
        },
        "expectativa": {
            "corridas": ["vlm", "llm"],
            "fuentes_validas": ["vlm", "llm"],  # dudosa: valida=True con debilidad
            "reglas_raw": {"llm": "RAW_SUSTENTO"},
            "debilidades_contienen": {"llm": "no contiene ese valor"},
        },
    },
    {
        "nombre": "shape_plano_de_v1",
        "que": "el JSON plano de v1 (kvi/kvg) se acepta pero sin sostén (v1 no lo pedía)",
        "por_fuente": {
            "vlm": _respuesta_base("vlm"),
            "llm": json.dumps(
                {
                    "tipo_comprobante": "A",
                    "cuit_emisor": "20-12345678-9",
                    "importe_total_facturado": 12345.67,
                }
            ),
        },
        "expectativa": {
            "corridas": ["vlm", "llm"],
            "valores": {("llm", "cuit_emisor"): "20-12345678-9"},
            "fuentes_invalidas": [],  # dudosas, no inválidas
            "debilidades_contienen": {"llm": "sustento"},
        },
    },
    {
        "nombre": "montos_se_evaluan_por_forma_canonica",
        "que": "desde T-403 los importes/fechas se evalúan contra su forma canónica",
        "por_fuente": {
            "vlm": _respuesta_base("vlm"),
            "llm": _respuesta_base("llm"),
        },
        "expectativa": {
            "corridas": ["vlm", "llm"],
            "sosten_forma_canonica_contiene": {
                "llm": ["importe_total_facturado", "fecha_emision", "cuit_emisor"],
            },
            # `descripcion` no se declara en la base: no queda nada sin evaluar.
            "sosten_no_evaluado_contiene": {"llm": []},
        },
    },
    {
        "nombre": "solo_llm_sin_vista",
        "que": "sin vista de imagen corre solo el LLM, y la ausencia se reporta",
        "sin_vista": True,
        "por_fuente": {"llm": _respuesta_base("llm")},
        "expectativa": {
            "corridas": ["llm"],
            "sin_insumo": ["vlm"],
        },
    },
    {
        "nombre": "json_invalido_aislado",
        "que": "una fuente con JSON inválido no tumba a la otra (fallo aislado)",
        "por_fuente": {
            "vlm": "esto no es json",
            "llm": _respuesta_base("llm"),
        },
        "expectativa": {
            "corridas": ["llm"],
            "fallos": {"vlm": "ErrorEvidencia"},
        },
    },
    {
        "nombre": "fuente_caida_aislada",
        "que": "una fuente caída (Ollama) no tumba a la otra",
        "falla": "vlm",
        "por_fuente": {"llm": _respuesta_base("llm")},
        "expectativa": {
            "corridas": ["llm"],
            "fallos": {"vlm": "RuntimeError"},
        },
    },
    {
        "nombre": "todas_caidas_es_error_de_dominio",
        "que": "si todas las fuentes con insumo fallan, ErrorExtraccion (no evidencia vacía)",
        "falla": "vlm",
        "falla_ambas": True,
        "por_fuente": {},
        "expectativa": {"error": ErrorExtraccion},
    },
]


def _correr_escenario(escenario: dict[str, Any]) -> dict[str, Any]:
    """Corre un escenario y devuelve su reporte (sin lanzar por fallos esperados)."""
    lector = LectorDoble(
        escenario.get("por_fuente", {}),
        falla="vlm" if escenario.get("falla_ambas") else escenario.get("falla"),
    )
    if escenario.get("falla_ambas"):
        # Falla en las dos fuentes: se anula el contenido para ambas.
        lector.por_fuente = {}

        def _ask(messages, *args, **kwargs):
            _ = lector._fuente(messages)
            raise RuntimeError("Ollama no responde (todas las fuentes)")

        lector.ask = _ask  # type: ignore[method-assign]

    vista = None if escenario.get("sin_vista") else _vista_imagen()
    try:
        resultado = extraer_evidencia(
            lector,
            markdown="FACTURA A\nC.U.I.T. 20-12345678-9\nFecha: 14/08/2025",
            vista=vista,
            documento_id=escenario["nombre"],
            settings=_settings(),
        )
        return {"resultado": resultado, "error": None, "lector": lector}
    except Exception as exc:  # noqa: BLE001 - los escenarios declaran qué esperan
        return {"resultado": None, "error": exc, "lector": lector}


def _verificar(corrida: dict[str, Any], expectativa: dict[str, Any]) -> list[str]:
    """Compara la corrida con la expectativa y devuelve las diferencias."""
    error = corrida["error"]
    if "error" in expectativa:
        if isinstance(error, expectativa["error"]):
            return []
        return [f"error={type(error).__name__ if error else 'ninguno'} (esperado {expectativa['error'].__name__})"]
    if error is not None:
        return [f"error inesperado: {type(error).__name__}: {error}"]

    resultado = corrida["resultado"]
    problemas: list[str] = []
    detalle = resultado.detalle

    if "corridas" in expectativa and detalle["fuentes_corridas"] != expectativa["corridas"]:
        problemas.append(
            f"fuentes corridas={detalle['fuentes_corridas']} (esperado {expectativa['corridas']})"
        )
    if "sin_insumo" in expectativa and detalle["fuentes_sin_insumo"] != expectativa["sin_insumo"]:
        problemas.append(
            f"fuentes sin insumo={detalle['fuentes_sin_insumo']} (esperado {expectativa['sin_insumo']})"
        )

    for fuente in expectativa.get("fuentes_validas", []):
        evidencia = resultado.por_fuente(fuente)
        if evidencia is None or not evidencia.valida:
            problemas.append(f"{fuente} debería ser válida")
    for fuente in expectativa.get("fuentes_invalidas", []):
        evidencia = resultado.por_fuente(fuente)
        if evidencia is None or evidencia.valida:
            problemas.append(f"{fuente} debería quedar invalidada")
    for fuente in expectativa.get("debilidades_vacias", []):
        evidencia = resultado.por_fuente(fuente)
        if evidencia is not None and evidencia.debilidades:
            problemas.append(f"{fuente} no debería tener debilidades: {evidencia.debilidades}")
    for (fuente, campo), esperado in expectativa.get("valores", {}).items():
        evidencia = resultado.por_fuente(fuente)
        real = evidencia.campos[campo].valor if evidencia and campo in evidencia.campos else None
        if real != esperado:
            problemas.append(f"{fuente}.{campo}={real!r} (esperado {esperado!r})")
    for fuente, regla in expectativa.get("reglas_raw", {}).items():
        evidencia = resultado.por_fuente(fuente)
        if evidencia is None or regla not in evidencia.reglas_aplicadas:
            problemas.append(
                f"{fuente} no disparó {regla} (aplicadas: "
                f"{evidencia.reglas_aplicadas if evidencia else []})"
            )
    for fuente, fragmento in expectativa.get("debilidades_contienen", {}).items():
        evidencia = resultado.por_fuente(fuente)
        texto = " ".join(evidencia.debilidades if evidencia else [])
        if fragmento.lower() not in texto.lower():
            problemas.append(f"{fuente} no declara '{fragmento}' en sus debilidades")
    for fuente, campos in expectativa.get("sosten_no_evaluado_contiene", {}).items():
        lista = detalle["modelos"].get(fuente, {}).get("sosten_no_evaluado", [])
        faltan = [c for c in campos if c not in lista]
        if faltan:
            problemas.append(f"{fuente} no lista como no evaluados: {faltan}")
    for fuente, campos in expectativa.get("sosten_forma_canonica_contiene", {}).items():
        lista = detalle["modelos"].get(fuente, {}).get("sosten_forma_canonica", [])
        faltan = [c for c in campos if c not in lista]
        if faltan:
            problemas.append(
                f"{fuente} no lista como evaluados por forma canónica: {faltan}"
            )
    for fuente, campos in expectativa.get("sosten_evaluado_contiene", {}).items():
        lista = detalle["modelos"].get(fuente, {}).get("sosten_no_evaluado", [])
        sobran = [c for c in campos if c in lista]
        if sobran:
            problemas.append(f"{fuente} no debería tener como no evaluados: {sobran}")
    for fuente, tipo_error in expectativa.get("fallos", {}).items():
        fallo = detalle["fallos"].get(fuente, "")
        if tipo_error not in fallo:
            problemas.append(f"fallo de {fuente}={fallo!r} (esperado {tipo_error})")

    return problemas


def _imprimir_resultado(corrida: dict[str, Any], detalle: bool) -> None:
    """Imprime el resumen de una corrida (fuentes, campos y debilidades)."""
    resultado = corrida["resultado"]
    if resultado is None:
        return
    for fuente, res in resultado.resultados.items():
        evidencia = res.source_evidence
        print(
            f"    · {fuente}: {len(evidencia.campos)} campos · "
            f"valida={evidencia.valida} · modelo={res.modelo} "
            f"({res.duracion_s * 1000:.0f} ms)"
        )
        if detalle:
            for campo, valor in res.evidencia.campos_legibles.items():
                print(f"        {campo} = {valor!r}")
            for debilidad in evidencia.debilidades:
                print(f"        ⚠ {debilidad}")


# ---------------------------------------------------------------------------
# Paralelismo (el motivo por el que los flujos corren en paralelo)
# ---------------------------------------------------------------------------


def _medir_paralelismo(demora_s: float) -> dict[str, Any]:
    """Mide el tiempo total de las dos fuentes con una demora artificial.

    Con las dos llamadas en paralelo, el total debe ser ≈ ``demora_s`` (el
    máximo), no ``2 * demora_s`` (la suma). Es la evidencia de que T-401 cumple
    "corren **en paralelo**" (E-EXT-1) y no solo "se llaman las dos".
    """
    por_fuente = {"vlm": _respuesta_base("vlm"), "llm": _respuesta_base("llm")}
    resultados: dict[str, Any] = {"demora_por_fuente_s": demora_s}

    for etiqueta, workers in (("paralelo", None), ("serial", 1)):
        lector = LectorDoble(por_fuente, demora_s=demora_s)
        inicio = time.monotonic()
        extraer_evidencia(
            lector,
            markdown="FACTURA A",
            vista=_vista_imagen(),
            settings=_settings(),
            max_workers=workers,
        )
        resultados[f"total_{etiqueta}_s"] = round(time.monotonic() - inicio, 3)

    resultados["esperado_paralelo_s"] = round(demora_s, 3)
    resultados["esperado_serial_s"] = round(2 * demora_s, 3)
    resultados["paralelismo_verificado"] = (
        resultados["total_paralelo_s"] < resultados["total_serial_s"] * 0.75
    )
    return resultados


# ---------------------------------------------------------------------------
# Modo real (--origen): F1 + vista fiel de F2 + Ollama real
# ---------------------------------------------------------------------------


def _correr_origen(origen: Path, modelo: str | None, detalle: bool) -> dict[str, Any]:
    """Corre la extracción real (F1 + F2 + Ollama) sobre un documento.

    Es el punto de entrada que consumirán T-404/T-405: procesa el documento con
    F1, prepara la **vista fiel** de F2 (E-QWE-2: máxima fidelidad, sin reducir)
    y extrae la evidencia de las dos fuentes con el ``OllamaClient`` real.
    Cualquier falla (Docling/Ollama) se reporta sin tumbar el resto del script.
    """
    print(f"Documento real: {origen} (F1 + vista fiel F2 + Ollama real)")
    print("=" * 78)
    reporte: dict[str, Any] = {"origen": str(origen)}
    try:
        from voucherflow.api import process
        from voucherflow.models.ollama import OllamaClient
        from voucherflow.validation.vistas import preparar_vista_fiel

        documento = process(str(origen))
        vista = preparar_vista_fiel(documento, str(origen))
        resultado = extraer_evidencia(
            OllamaClient(),
            markdown=documento.markdown,
            vista=vista,
            documento_id=origen.name,
            modelo=modelo,
        )
        print(f"tipo_entrada={documento.tipo_entrada} vista={vista.tipo_vista}/{vista.calidad}")
        for fuente, res in resultado.resultados.items():
            print(
                f"  · {fuente}: {len(res.source_evidence.campos)} campos · "
                f"valida={res.source_evidence.valida}"
            )
            if detalle:
                for campo, valor in res.evidencia.campos_legibles.items():
                    print(f"      {campo} = {valor!r}")
            for debilidad in res.source_evidence.debilidades:
                print(f"      ⚠ {debilidad}")
        if resultado.detalle.get("fallos"):
            for fuente, fallo in resultado.detalle["fallos"].items():
                print(f"  ❌ {fuente}: {fallo}", file=sys.stderr)
        reporte["extraccion"] = {
            "fuentes": list(resultado.resultados),
            "campos": {
                fuente: res.evidencia.campos_legibles
                for fuente, res in resultado.resultados.items()
            },
            "debilidades": resultado.debilidades,
            "detalle": resultado.detalle,
        }
    except Exception as exc:  # noqa: BLE001 - herramienta manual: se reporta todo
        print(f"  ❌ {type(exc).__name__}: {exc}", file=sys.stderr)
        reporte["error"] = f"{type(exc).__name__}: {exc}"
    print()
    return reporte


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def _imprimir_prompt() -> None:
    """Imprime el prompt versionado (base y las dos guías por fuente)."""
    print(f"Prompt de extracción versionado: {VERSION_PROMPT_EXTRACCION}")
    print("=" * 78)
    print(SYSTEM_PROMPT_POR_FUENTE["llm"])
    print("=" * 78)
    print(f"Fuentes: {FUENTES_EXTRACCION} · campos del contrato: {len(CAMPOS_EXTRACCION)}")
    print(f"Campos: {', '.join(CAMPOS_EXTRACCION)}")
    print()


def main() -> None:
    parser = argparse.ArgumentParser(
        description=(
            "Inspecciona la extracción VLM+LLM en paralelo (F4 / T-401): prompt "
            "versionado, evidencia por fuente y paralelismo real. Los escenarios "
            "sintéticos no requieren Ollama."
        )
    )
    parser.add_argument(
        "--caso",
        action="append",
        help="Filtra por nombre de escenario (repetible). Default: todos.",
    )
    parser.add_argument("--prompt", action="store_true", help="Imprime el prompt de evidencia.")
    parser.add_argument("--detalle", action="store_true", help="Campos y debilidades por fuente.")
    parser.add_argument("--json", type=Path, help="Escribe el reporte completo en JSON.")
    parser.add_argument(
        "--origen",
        type=Path,
        help=(
            "Corre contra Ollama REAL sobre un documento (F1 + vista fiel de F2). "
            "Requiere Ollama y Docling locales."
        ),
    )
    parser.add_argument("--modelo", help="Modelo explícito (solo con --origen).")
    parser.add_argument(
        "--sin-paralelismo",
        action="store_true",
        help="Omite la medición de paralelismo (escenarios + prompt).",
    )
    args = parser.parse_args()

    if args.prompt:
        _imprimir_prompt()

    reporte: dict[str, Any] = {
        "tarea": "T-401",
        "fase": "F4",
        "version_prompt": VERSION_PROMPT_EXTRACCION,
        "escenarios": [],
    }
    fallos = 0

    # --- Modo real (--origen): F1 + vista fiel de F2 + Ollama real ----------
    if args.origen is not None:
        if not args.origen.exists():
            print(f"No existe el documento: {args.origen}", file=sys.stderr)
            sys.exit(2)
        reporte["origen"] = _correr_origen(args.origen, args.modelo, args.detalle)
        if reporte["origen"].get("error"):
            fallos += 1

    # --- Escenarios sintéticos ---------------------------------------------
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
        print(f"Escenarios de extracción (prompt {VERSION_PROMPT_EXTRACCION})")
        print("=" * 78)
        for escenario in seleccionados:
            corrida = _correr_escenario(escenario)
            problemas = _verificar(corrida, escenario["expectativa"])
            estado = "✅" if not problemas else "❌"
            print(f"  {estado} {escenario['nombre']:<34} {escenario['que']}")
            _imprimir_resultado(corrida, detalle=args.detalle)
            if problemas:
                fallos += 1
                for problema in problemas:
                    print(f"      ↳ {problema}")
            reporte["escenarios"].append(
                {
                    "nombre": escenario["nombre"],
                    "coincide": not problemas,
                    "diferencias": problemas,
                    "detalle_corrida": (
                        corrida["resultado"].detalle if corrida["resultado"] else None
                    ),
                    "error": (
                        f"{type(corrida['error']).__name__}: {corrida['error']}"
                        if corrida["error"]
                        else None
                    ),
                }
            )
        print()
        total = len(seleccionados)
        print(f"Escenarios verificados: {total - fallos}/{total} coinciden con la expectativa.")
        reporte["escenarios_total"] = total
        reporte["escenarios_ok"] = total - fallos

    # --- Paralelismo real ---------------------------------------------------
    if not args.sin_paralelismo:
        medicion = _medir_paralelismo(0.2)
        print()
        print("Paralelismo (dos fuentes con 0.2 s de demora cada una):")
        print("=" * 78)
        print(f"  · en paralelo: {medicion['total_paralelo_s']} s")
        print(f"  · en serie   : {medicion['total_serial_s']} s")
        estado = "✅" if medicion["paralelismo_verificado"] else "❌"
        print(
            f"  {estado} el total en paralelo ≈ el máximo "
            f"({medicion['esperado_paralelo_s']} s), no la suma "
            f"({medicion['esperado_serial_s']} s)"
        )
        if not medicion["paralelismo_verificado"]:
            fallos += 1
        reporte["paralelismo"] = medicion

    if args.json:
        args.json.write_text(
            json.dumps(reporte, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        print(f"Reporte JSON: {args.json}")

    if fallos:
        sys.exit(1)


if __name__ == "__main__":
    main()

#!/usr/bin/env python
"""Inspecciona T-402 (F4) — normalización key-value de la extracción.

**Fase**: F4 (extracción) · **Tarea**: T-402 · **Épica**: E-EXT-3.

Muestra, **sin Ollama ni Docling** (los lectores se inyectan como dobles):

  1. Las **reglas de normalización** una por una, con los casos de la letra
     chica de v1: el CUIT que el OCR pegó al campo siguiente (``"20-1 Ing,
     Brutas: 201641"`` → ``"20-1"``), la fecha impresa (``"14/08/2025"`` →
     ``"2025-08-14"``), el monto con separadores (``"$ 12.345,67"`` →
     ``12345.67``), el número de comprobante (``"00005-00007344"`` →
     ``punto_venta`` + ``numero_comprobante``), la moneda y los ítems.
  2. Los **escenarios** que verifican la regla dura de E-EXT-3 ("no inventar"):
     un dato ilegible conserva el crudo con aviso, un año de dos dígitos no se
     completa, una fecha inexistente no se publica, el monto ambiguo se resuelve
     con la convención argentina **con constancia**, y el ``0`` sigue siendo un
     valor.
  3. Las **fronteras** de la tarea: la evidencia cruda de T-401 sigue disponible
     (``normalizar=False``), la pasada raw de T-303 sigue viendo el crudo, y
     ``combinar_evidencia`` sigue siendo el esqueleto de T-404.

Con ``--origen`` corre la extracción **real** contra Ollama y muestra la
comparación crudo → normalizado de cada fuente (el insumo de la paridad con v1
de T-405).

Uso:
    python scripts/F4/t402.py                       # reglas + escenarios
    python scripts/F4/t402.py --reglas              # solo las reglas, caso por caso
    python scripts/F4/t402.py --caso fecha_anio_corto
    python scripts/F4/t402.py --json /tmp/t402.json
    python scripts/F4/t402.py --origen files/2025-08/2D2C9343/<doc>.jpg --detalle

Nota: la suite default de pytest cubre lo mismo en
``tests/test_extraction_key_value.py``; este script es la verificación de humo
legible para la bitácora (sale con código ≠ 0 si algún escenario falla).
"""

from __future__ import annotations

import argparse
import json
import sys
import tempfile
from pathlib import Path
from typing import Any

# El paquete usa layout src/; si se corre sin instalar, se agrega src/ al path.
_SRC = Path(__file__).resolve().parents[2] / "src"
if str(_SRC) not in sys.path:
    sys.path.insert(0, str(_SRC))

from voucherflow.extraction import (  # noqa: E402
    CAMPO_FUENTE_LECTURA,
    CLAVE_CAMPOS,
    NORM_COMPROBANTE,
    NORM_CUIT,
    NORM_FECHA,
    NORM_MONEDA,
    NORM_MONTO,
    VERSION_NORMALIZACION,
    ejecutar_flujo,
    extraer_evidencia,
    normalizar_campo,
    normalizar_evidencia,
    parsear_evidencia_extraccion,
)
from voucherflow.extraction.prompt_extraccion import (  # noqa: E402
    SYSTEM_PROMPT_POR_FUENTE,
)
from voucherflow.settings.config import cargar_desde_dict  # noqa: E402
from voucherflow.validation.vistas import VistaPreparada  # noqa: E402

# ---------------------------------------------------------------------------
# Utilidades de escenario (mismo patrón que scripts/F3 y scripts/F4/t401.py)
# ---------------------------------------------------------------------------

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
        nota="Vista fiel sintética (T-402)",
    )


def _campo(valor: Any, sustento: str) -> dict[str, Any]:
    """Campo con el shape del contrato (``valor`` + ``fragmento_sustento``)."""
    return {"valor": valor, "fragmento_sustento": sustento}


def _respuesta(campos: dict[str, Any], fuente: str = "llm") -> str:
    """JSON de evidencia de extracción con los campos indicados."""
    return json.dumps(
        {CAMPO_FUENTE_LECTURA: fuente, CLAVE_CAMPOS: campos}, ensure_ascii=False
    )


class LectorDoble:
    """Doble del ``OllamaClient``: responde el JSON configurado, sin red."""

    def __init__(self, contenido: str = "{}") -> None:
        self.contenido = contenido
        self.llamadas: list[str] = []

    def _fuente(self, messages: list[dict[str, Any]]) -> str:
        sistema = messages[0].get("content", "") if messages else ""
        for fuente, prompt in SYSTEM_PROMPT_POR_FUENTE.items():
            if prompt == sistema:
                return fuente
        return ""

    def ask(self, messages, model, json_format=False, options=None, num_ctx=None):
        self.llamadas.append(self._fuente(messages))

        class _R:
            contenido = self.contenido

        return _R()


def _settings() -> Any:
    """``Settings`` con los roles ``vlm``/``llm`` (sin Ollama real)."""
    return cargar_desde_dict(
        {
            "modelos": {
                "vlm": {"rol": "vlm", "modelo": "vlm-doble", "num_ctx": 4096},
                "llm": {"rol": "llm", "modelo": "llm-doble", "num_ctx": 8192},
            }
        }
    )


def _normalizar_lectura(campos: dict[str, Any], fuente: str = "llm") -> Any:
    """Interpreta + normaliza una respuesta sintética y devuelve el resultado."""
    evidencia = parsear_evidencia_extraccion(_respuesta(campos, fuente), fuente=fuente)
    return normalizar_evidencia(evidencia)


# ---------------------------------------------------------------------------
# 1. Reglas, caso por caso (los ejemplos de la letra chica de v1)
# ---------------------------------------------------------------------------

#: ``(campo, valor crudo, valor esperado)`` por regla. El valor esperado es el
#: que publica la normalización; ``None`` significa "no se normaliza y se
#: conserva el crudo" (los casos de "no inventar").
_REGLAS: list[tuple[str, str, list[tuple[str, str, Any]]]] = [
    (
        NORM_CUIT,
        "solo dígitos y los guiones propios; corte ante caracteres extraños (regla 2b de v1)",
        [
            ("cuit_emisor", "20-12345678-9", "20-12345678-9"),
            ("cuit_emisor", "20-1 Ing, Brutas: 201641", "20-1"),
            ("cuit_emisor", "C.U.I.T. Nro.: 20-1", "20-1"),
            ("cuit_emisor", "20301112223", "20301112223"),
            ("cuit_emisor", "ilegible", "ilegible"),
        ],
    ),
    (
        NORM_FECHA,
        "fecha completa a YYYY-MM-DD; sin día/mes completo no se completa (kvg)",
        [
            ("fecha_emision", "14/08/2025", "2025-08-14"),
            ("fecha_emision", "2025-08-14T10:30:00", "2025-08-14"),
            ("fecha_emision", "14082025", "2025-08-14"),
            ("fecha_emision", "14 de agosto de 2025", "2025-08-14"),
            ("fecha_emision", "14/08/25", "14/08/25"),
            ("fecha_emision", "31/02/2025", "31/02/2025"),
        ],
    ),
    (
        NORM_MONTO,
        "número plano, sin separadores de miles ni símbolo (kvg)",
        [
            ("importe_total_facturado", "$ 12.345,67", 12345.67),
            ("importe_total_facturado", "1.234.567", 1234567),
            ("importe_total_facturado", "12,50", 12.5),
            ("importe_total_facturado", "0", 0),
            ("importe_total_facturado", "1.234,56-", -1234.56),
            ("importe_total_facturado", "12.345", 12345),
        ],
    ),
    (
        NORM_COMPROBANTE,
        "PPPPP-NNNNNNNN se separa; el número impreso se conserva tal cual (kvg + regla 3 de 11)",
        [
            ("nro_comprobante", "00005-00007344", "00005-00007344"),
            ("nro_comprobante", "0000500007344", "0000500007344"),
        ],
    ),
]


def _correr_reglas() -> list[dict[str, Any]]:
    """Corre cada ejemplo de regla y devuelve el reporte."""
    salida: list[dict[str, Any]] = []
    for regla, descripcion, casos in _REGLAS:
        for campo, crudo, esperado in casos:
            resultado = normalizar_campo(campo, crudo)
            salida.append(
                {
                    "regla": regla,
                    "descripcion": descripcion,
                    "campo": campo,
                    "crudo": crudo,
                    "normalizado": resultado.valor,
                    "esperado": esperado,
                    "ok": resultado.valor == esperado,
                    "derivados": dict(resultado.derivados),
                    "avisos": [a.motivo for a in resultado.avisos],
                }
            )
    return salida


def _imprimir_reglas(reportes: list[dict[str, Any]], detalle: bool) -> int:
    """Imprime el reporte de reglas; devuelve la cantidad de fallos."""
    fallos = 0
    regla_actual = None
    for r in reportes:
        if r["regla"] != regla_actual:
            regla_actual = r["regla"]
            print(f"\n{regla_actual} — {r['descripcion']}")
        marca = "✅" if r["ok"] else "❌"
        extra = f" · avisos: {len(r['avisos'])}" if detalle and r["avisos"] else ""
        print(
            f"  {marca} {r['campo']}: {r['crudo']!r} → {r['normalizado']!r}"
            f"{extra}"
        )
        if detalle:
            for aviso in r["avisos"]:
                print(f"       ⚠️  {aviso}")
        if r["derivados"]:
            print(f"       derivados: {r['derivados']}")
        if not r["ok"]:
            fallos += 1
            print(f"       esperado: {r['esperado']!r}")
    return fallos


# ---------------------------------------------------------------------------
# 2. Escenarios (la regla dura de E-EXT-3: "no inventar")
# ---------------------------------------------------------------------------

ESCENARIOS: list[dict[str, Any]] = [
    {
        "nombre": "cuit_cortado_por_ocr",
        "que": "el CUIT pegado al campo siguiente se corta (regla 2b de v1) y se avisa",
        "campos": {
            "cuit_emisor": _campo("20-1 Ing, Brutas: 201641", "C.U.I.T. 20-1 Ing, Brutas: 201641")
        },
        "valores": {"cuit_emisor": "20-1"},
        "crudos": {"cuit_emisor": "20-1 Ing, Brutas: 201641"},
        "avisos_contienen": {NORM_CUIT: "11 del CUIT"},
    },
    {
        "nombre": "fecha_impresa_a_iso",
        "que": "la fecha impresa queda en ISO sin tocar el formato volátil del OCR",
        "campos": {"fecha_emision": _campo("14/08/2025", "Fecha de Emisión: 14/08/2025")},
        "valores": {"fecha_emision": "2025-08-14"},
        "crudos": {"fecha_emision": "14/08/2025"},
        "avisos_contienen": {},
    },
    {
        "nombre": "fecha_anio_corto_no_se_completa",
        "que": "un año de dos dígitos no se completa (elegir el siglo sería inventar)",
        "campos": {"fecha_emision": _campo("14/08/25", "Fecha: 14/08/25")},
        "valores": {"fecha_emision": "14/08/25"},
        "crudos": {"fecha_emision": "14/08/25"},
        "avisos_contienen": {NORM_FECHA: "dos dígitos"},
    },
    {
        "nombre": "fecha_inexistente",
        "que": "una fecha que no existe en el calendario no se publica como ISO",
        "campos": {"fecha_emision": _campo("31/02/2025", "Fecha: 31/02/2025")},
        "valores": {"fecha_emision": "31/02/2025"},
        "crudos": {"fecha_emision": "31/02/2025"},
        "avisos_contienen": {NORM_FECHA: "no existe"},
    },
    {
        "nombre": "monto_con_separadores",
        "que": "el importe impreso queda numérico sin separadores de miles",
        "campos": {
            "importe_total_facturado": _campo("$ 12.345,67", "Importe Total: $ 12.345,67")
        },
        "valores": {"importe_total_facturado": 12345.67},
        "crudos": {"importe_total_facturado": "$ 12.345,67"},
        "avisos_contienen": {},
    },
    {
        "nombre": "monto_ambiguo_con_constancia",
        "que": "un separador único con 3 dígitos es ambiguo: se resuelve y se deja constancia",
        "campos": {"subtotal": _campo("12.345", "Subtotal: 12.345")},
        "valores": {"subtotal": 12345},
        "crudos": {"subtotal": "12.345"},
        "avisos_contienen": {NORM_MONTO: "ambiguo"},
    },
    {
        "nombre": "monto_cero_no_es_ausencia",
        "que": "el 0 es un importe legítimo (una factura sin IVA), no un dato faltante",
        "campos": {"iva": _campo("0,00", "IVA: 0,00")},
        "valores": {"iva": 0.0},
        "crudos": {"iva": "0,00"},
        "avisos_contienen": {},
    },
    {
        "nombre": "monto_negativo_impreso",
        "que": "un ajuste negativo impreso se detecta (signo delante, detrás o paréntesis)",
        "campos": {
            "otros_impuestos": _campo("(1.234,56)", "Otros: (1.234,56)"),
            "percepcion_iibb": _campo("1.234,56-", "Percepción: 1.234,56-"),
        },
        "valores": {"otros_impuestos": -1234.56, "percepcion_iibb": -1234.56},
        "crudos": {"otros_impuestos": "(1.234,56)", "percepcion_iibb": "1.234,56-"},
        "avisos_contienen": {},
    },
    {
        "nombre": "monto_ilegible_conserva_el_crudo",
        "que": "un monto ilegible conserva el crudo con aviso; no se inventa un importe",
        "campos": {"iva": _campo("no se lee", "IVA 21%: ilegible")},
        "valores": {"iva": "no se lee"},
        "crudos": {"iva": "no se lee"},
        "avisos_contienen": {NORM_MONTO: "no se reconoció"},
    },
    {
        "nombre": "comprobante_separado",
        "que": "PPPPP-NNNNNNNN se separa en punto_venta y numero_comprobante (campos derivados)",
        "campos": {
            "nro_comprobante": _campo("00005-00007344", "Nro: 00005-00007344")
        },
        "valores": {
            "nro_comprobante": "00005-00007344",
            "punto_venta": "00005",
            "numero_comprobante": "00007344",
        },
        "crudos": {"nro_comprobante": "00005-00007344"},
        "derivados": {"punto_venta": "00005", "numero_comprobante": "00007344"},
        "avisos_contienen": {},
    },
    {
        "nombre": "comprobante_sin_formato",
        "que": "un número sin la forma PPPPP-NNNNNNNN no se separa (aviso informativo)",
        "campos": {"nro_comprobante": _campo("0000500007344", "Nro: 0000500007344")},
        "valores": {"nro_comprobante": "0000500007344"},
        "crudos": {"nro_comprobante": "0000500007344"},
        "derivados": {},
        "informativos_contienen": "no tiene la forma",
    },
    {
        "nombre": "moneda_sin_default",
        "que": "una moneda no reconocida se conserva (v1 asumía ARS: acá no se inventa)",
        "campos": {"moneda": _campo("EUR", "Moneda: EUR")},
        "valores": {"moneda": "EUR"},
        "crudos": {"moneda": "EUR"},
        "avisos_contienen": {NORM_MONEDA: "no corresponde a una moneda"},
    },
    {
        "nombre": "moneda_usd_y_vocabulario",
        "que": "U$S → USD y la letra en minúscula pasa a mayúscula (vocabulario)",
        "campos": {
            "moneda": _campo("u$s", "Moneda: U$S"),
            "tipo_comprobante": _campo("a", "Recuadro 'a'"),
        },
        "valores": {"moneda": "USD", "tipo_comprobante": "A"},
        "crudos": {"moneda": "u$s", "tipo_comprobante": "a"},
        "avisos_contienen": {},
    },
    {
        "nombre": "texto_y_descripcion",
        "que": "el texto colapsa espacios; la descripción además va en minúsculas (regla 8)",
        "campos": {
            "razon_social_emisor": _campo("  ACME   S.A.  ", "ACME S.A."),
            "descripcion": _campo("COMPRA  DE  INSUMOS", "Compra de insumos"),
        },
        "valores": {
            "razon_social_emisor": "ACME S.A.",
            "descripcion": "compra de insumos",
        },
        "crudos": {
            "razon_social_emisor": "  ACME   S.A.  ",
            "descripcion": "COMPRA  DE  INSUMOS",
        },
        "avisos_contienen": {},
    },
    {
        "nombre": "items_estructurados",
        "que": "los ítems de kvg se estructuran sin inventar cantidad ni precio",
        "campos": {
            "productos": _campo(
                "café x2 - 1000 | pan - 250 | servicio", "Ítems: café x2 - 1000..."
            )
        },
        "items_esperados": [
            {"descripcion": "café", "cantidad": 2, "precio_unitario": 1000},
            {"descripcion": "pan", "cantidad": None, "precio_unitario": 250},
            {"descripcion": "servicio", "cantidad": None, "precio_unitario": None},
        ],
        "avisos_contienen": {},
    },
    {
        "nombre": "campo_generico_sin_regla",
        "que": "un campo del modo genérico sin semántica conocida se publica tal cual",
        "campos": {"alicuota_21": _campo("21%", "Alicuota 21%: 21%")},
        "valores": {"alicuota_21": "21%"},
        "crudos": {"alicuota_21": "21%"},
        "avisos_contienen": {},
    },
    {
        "nombre": "las_debilidades_llegan_a_la_evidencia",
        "que": "los avisos que son limitaciones reales llegan a SourceEvidence.debilidades",
        "campos": {
            "cuit_emisor": _campo("20-1 Ing, Brutas: 201641", "C.U.I.T. 20-1"),
            "fecha_emision": _campo("14/08/2025", "Fecha: 14/08/2025"),
        },
        "valores": {"cuit_emisor": "20-1", "fecha_emision": "2025-08-14"},
        "crudos": {"cuit_emisor": "20-1 Ing, Brutas: 201641"},
        "debilidades_contienen": "NORM_CUIT",
        "informativos_ausentes": [NORM_FECHA],
    },
]


def _correr_escenario(escenario: dict[str, Any]) -> dict[str, Any]:
    """Corre un escenario de normalización sobre una lectura sintética."""
    lectura = _normalizar_lectura(escenario["campos"], escenario.get("fuente", "llm"))
    return {"lectura": lectura, "error": None}


def _verificar(corrida: dict[str, Any], expectativa: dict[str, Any]) -> list[str]:
    """Compara la corrida con la expectativa y devuelve las diferencias."""
    if corrida["error"] is not None:
        return [f"error inesperado: {type(corrida['error']).__name__}: {corrida['error']}"]
    lectura = corrida["lectura"]
    campos = lectura.evidencia.campos
    problemas: list[str] = []

    for campo, esperado in expectativa.get("valores", {}).items():
        real = campos[campo].valor if campo in campos else None
        if real != esperado:
            problemas.append(f"{campo}={real!r} (esperado {esperado!r})")
    for campo, esperado in expectativa.get("crudos", {}).items():
        real = campos[campo].valor_crudo if campo in campos else None
        if real != esperado:
            problemas.append(f"valor_crudo de {campo}={real!r} (esperado {esperado!r})")
    for campo, esperado in expectativa.get("derivados", {}).items():
        real = lectura.informe.derivados.get(campo)
        if real != esperado:
            problemas.append(f"derivado {campo}={real!r} (esperado {esperado!r})")
    for regla, fragmento in expectativa.get("avisos_contienen", {}).items():
        textos = [a.motivo for a in lectura.informe.avisos if a.debilidad]
        if not any(fragmento.lower() in t.lower() for t in textos):
            problemas.append(f"falta el aviso de {regla} con '{fragmento}'")
    if "informativos_contienen" in expectativa:
        fragmento = expectativa["informativos_contienen"]
        textos = [a.motivo for a in lectura.informe.avisos if not a.debilidad]
        if not any(fragmento.lower() in t.lower() for t in textos):
            problemas.append(f"falta el aviso informativo con '{fragmento}'")
    for regla in expectativa.get("informativos_ausentes", []):
        textos = " ".join(a.motivo for a in lectura.informe.avisos if not a.debilidad)
        if regla in textos:
            problemas.append(f"no debería haber un aviso informativo de {regla}")
    if "debilidades_contienen" in expectativa:
        fragmento = expectativa["debilidades_contienen"]
        if not any(fragmento in d for d in lectura.informe.debilidades):
            problemas.append(f"las debilidades no mencionan {fragmento}")
    for esperado in expectativa.get("items_esperados", []):
        item = next(
            (i for i in lectura.informe.items if i.descripcion == esperado["descripcion"]),
            None,
        )
        if item is None:
            problemas.append(f"falta el ítem {esperado['descripcion']!r}")
            continue
        for clave in ("cantidad", "precio_unitario"):
            if item.como_dict()[clave] != esperado[clave]:
                problemas.append(
                    f"ítem {esperado['descripcion']!r}.{clave}={item.como_dict()[clave]!r} "
                    f"(esperado {esperado[clave]!r})"
                )
    if "items_cantidad" in expectativa and len(lectura.informe.items) != expectativa["items_cantidad"]:
        problemas.append(
            f"ítems={len(lectura.informe.items)} (esperado {expectativa['items_cantidad']})"
        )
    return problemas


def _imprimir_resultado(corrida: dict[str, Any], detalle: bool) -> None:
    """Imprime las reglas aplicadas y los avisos de la corrida."""
    lectura = corrida["lectura"]
    reglas = lectura.informe.reglas_aplicadas or ("(ninguna)",)
    print(f"    · reglas: {', '.join(reglas)}")
    if lectura.informe.derivados:
        print(f"    · derivados: {lectura.informe.derivados}")
    if lectura.informe.items:
        print(f"    · ítems: {len(lectura.informe.items)}")
    if detalle:
        for campo, c in lectura.evidencia.campos.items():
            print(
                f"      {campo}: {c.valor_crudo!r} → {c.valor!r}"
                + (f"  [{c.regla_normalizacion}]" if c.regla_normalizacion else "")
                + (f"  (derivado de {c.derivado_de})" if c.derivado_de else "")
            )
    for debilidad in lectura.informe.debilidades:
        print(f"    ⚠️  {debilidad}")


# ---------------------------------------------------------------------------
# 3. Fronteras de la tarea (lo que T-402 NO hace)
# ---------------------------------------------------------------------------


def _verificar_fronteras() -> list[dict[str, Any]]:
    """Comprueba las fronteras de T-402 con la corrida de un flujo real (doble)."""
    campos = {
        "cuit_emisor": _campo("20-1 Ing, Brutas: 201641", "C.U.I.T. 20-1 Ing, Brutas: 201641"),
        "tipo_comprobante": _campo("X", "Recuadro 'X'"),
    }
    lector = LectorDoble(_respuesta(campos))
    normalizado = ejecutar_flujo(
        "llm", lector, markdown="FACTURA", settings=_settings()
    )
    crudo = ejecutar_flujo(
        "llm", lector, markdown="FACTURA", settings=_settings(), normalizar=False
    )
    checks = [
        (
            "el SourceEvidence publica el valor normalizado",
            normalizado.source_evidence.campos["cuit_emisor"].valor == "20-1",
        ),
        (
            "el crudo viaja en meta['valor_crudo'] (auditoría sin volver al JSON)",
            normalizado.source_evidence.campos["cuit_emisor"].meta["valor_crudo"]
            == "20-1 Ing, Brutas: 201641",
        ),
        (
            "la normalización queda trazada en meta (regla y avisos)",
            normalizado.source_evidence.campos["cuit_emisor"].meta["regla_normalizacion"]
            == NORM_CUIT
            and bool(
                normalizado.source_evidence.campos["cuit_emisor"].meta[
                    "avisos_normalizacion"
                ]
            ),
        ),
        (
            "normalizar=False devuelve la lectura cruda de T-401",
            crudo.source_evidence.campos["cuit_emisor"].valor
            == "20-1 Ing, Brutas: 201641"
            and crudo.informe_normalizacion is None,
        ),
        (
            "la pasada raw sigue viendo el crudo (RAW_VOCABULARIO con 'X')",
            normalizado.source_evidence.valida is False
            and "RAW_VOCABULARIO" in normalizado.source_evidence.reglas_aplicadas,
        ),
    ]
    return [
        {"que": que, "ok": ok} for que, ok in checks
    ]


# ---------------------------------------------------------------------------
# 4. Corrida real (--origen)
# ---------------------------------------------------------------------------


def _correr_origen(origen: Path, modelo: str | None, detalle: bool) -> dict[str, Any]:
    """Corre la extracción real con Ollama y muestra crudo → normalizado."""
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
        print(f"\n  [{fuente}] modelo={res.modelo} ({res.duracion_s:.1f} s)")
        for campo, c in res.evidencia.campos.items():
            print(f"    {campo}: {c.valor_crudo!r} → {c.valor!r}")
        if res.informe_normalizacion:
            for aviso in res.informe_normalizacion.debilidades:
                print(f"    ⚠️  {aviso}")
    return {"resultado": resultado, "detalle": detalle}


# ---------------------------------------------------------------------------
# main
# ---------------------------------------------------------------------------


def _imprimir_cabecera() -> None:
    print(
        "T-402 · Normalización key-value de la extracción (F4/E-EXT-3)\n"
        f"    versión de reglas: {VERSION_NORMALIZACION}\n"
        "    reglas de v1 portadas a código: prompts 10/11 (kvi) y kvg\n"
        "    regla dura: un dato ilegible conserva el crudo con aviso; no se inventa"
    )


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Inspecciona la normalización key-value de F4/T-402 (sin Ollama)"
    )
    parser.add_argument("--reglas", action="store_true", help="solo las reglas, caso por caso")
    parser.add_argument("--caso", help="corre un solo escenario por nombre")
    parser.add_argument("--detalle", action="store_true", help="campos y avisos por escenario")
    parser.add_argument("--origen", type=Path, help="documento real (corre Ollama)")
    parser.add_argument("--modelo", help="modelo de Ollama para --origen")
    parser.add_argument("--json", type=Path, help="volcar el reporte a un archivo JSON")
    args = parser.parse_args()

    _imprimir_cabecera()
    reporte: dict[str, Any] = {}

    if args.origen:
        _correr_origen(args.origen, args.modelo, args.detalle)
        return

    # 1. Reglas, caso por caso.
    reportes_reglas = _correr_reglas()
    fallos = _imprimir_reglas(reportes_reglas, args.detalle)
    reporte["reglas"] = reportes_reglas
    if args.reglas:
        print(f"\nReglas: {len(reportes_reglas) - fallos}/{len(reportes_reglas)} casos OK.")
        if args.json:
            args.json.write_text(json.dumps(reporte, ensure_ascii=False, indent=2), encoding="utf-8")
        sys.exit(1 if fallos else 0)

    # 2. Escenarios.
    escenarios = ESCENARIOS
    if args.caso:
        escenarios = [e for e in ESCENARIOS if e["nombre"] == args.caso]
        if not escenarios:
            print(f"\nNo existe el escenario {args.caso!r}.", file=sys.stderr)
            sys.exit(2)
    print("\nEscenarios (la regla dura de E-EXT-3: no inventar)")
    reporte["escenarios"] = []
    for escenario in escenarios:
        corrida = _correr_escenario(escenario)
        diferencias = _verificar(corrida, escenario)
        marca = "✅" if not diferencias else "❌"
        print(f"\n  {marca} {escenario['nombre']:<38} {escenario['que']}")
        _imprimir_resultado(corrida, args.detalle)
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

    # 3. Fronteras de la tarea.
    print("\nFronteras de T-402 (lo que NO hace)")
    reporte["fronteras"] = []
    for frontera in _verificar_fronteras():
        marca = "✅" if frontera["ok"] else "❌"
        print(f"  {marca} {frontera['que']}")
        if not frontera["ok"]:
            fallos += 1
        reporte["fronteras"].append(frontera)

    print(f"\nEscenarios verificados: {len(escenarios) - sum(1 for e in reporte['escenarios'] if not e['ok'])}/{len(escenarios)}")
    reporte["fallos"] = fallos
    if args.json:
        args.json.write_text(json.dumps(reporte, ensure_ascii=False, indent=2), encoding="utf-8")
        print(f"Reporte guardado en {args.json}")
    sys.exit(1 if fallos else 0)


if __name__ == "__main__":
    main()

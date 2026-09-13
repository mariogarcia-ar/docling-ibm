#!/usr/bin/env python
"""Inspecciona T-303 (F3) — reglas **raw** por fuente (pasada 1).

**Fase**: F3 (clasificación) · **Tarea**: T-303 · **Épica**: E-CLAS-1.

Muestra, **sin Ollama ni Docling** (el motor raw es determinístico):

  1. El **registro** de reglas raw con su `id`, `prioridad`, `detalle` y la
     gravedad que aportan al dispararse (``--reglas``).
  2. El **veredicto** de la pasada 1 sobre un conjunto de **escenarios
     sintéticos** que cubren las cuatro reglas (fuente incompleta, letra fuera
     de vocabulario, sustento que no sostiene, contradicción entre el texto y la
     letra declarada) y los casos sanos, con la **gradación**
     válida/dudosa/inválida y los candidatos descartados/restantes.
  3. La **cadena completa T-303 → T-301**: cada escenario corre la lectura
     (T-302, con un lector doble), la califica (T-303) y decide la letra con el
     motor de reglas (T-301) — verificando que la calificación raw **no cambia**
     la letra, solo enriquece los candidatos y el detalle.
  4. Opcionalmente, un **campo propio** por JSON (``--campo``) para reutilizar el
     registro con otro dominio (es el uso que le dará F4/T-403).

Por cada escenario imprime: la fuente, el veredicto (válida/dudosa/inválida), las
reglas raw disparadas, las debilidades, los candidatos y la letra final que
resolvió el motor. Con `--json` escribe el reporte completo para la bitácora.

Los escenarios declaran su **expectativa**; el script marca ✅/❌ comparando
contra el resultado real y sale con código ≠ 0 si alguno falla (verificación de
humo legible, no reemplaza a `tests/test_rules_raw.py`).

Uso:
    python scripts/F3/t303.py                       # escenarios sintéticos
    python scripts/F3/t303.py --reglas              # además, la tabla de reglas
    python scripts/F3/t303.py --detalle             # debilidades y candidatos
    python scripts/F3/t303.py --caso contradiccion_texto_B
    python scripts/F3/t303.py --campo campo.json
    python scripts/F3/t303.py --json /tmp/t303.json

Ejemplos:
    python scripts/F3/t303.py --reglas --detalle
    python scripts/F3/t303.py --campo ../files/2025-08/2D2C9343/campo.json

Nota: no requiere Ollama ni Docling. La lectura real (VLM/LLM) es de F4/T-401 y
se puede ejercitar con ``scripts/F3/t302.py --origen``.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
import tempfile
from pathlib import Path
from typing import Any

# El paquete usa layout src/; si se corre sin instalar, se agrega src/ al path.
_SRC = Path(__file__).resolve().parents[2] / "src"
if str(_SRC) not in sys.path:
    sys.path.insert(0, str(_SRC))

from voucherflow.classification import (  # noqa: E402
    EVIDENCIA_CAMPO_LETRA,
    clasificar_tipo_comprobante,
    contexto_desde_evidencia,
    leer_evidencia,
    parsear_evidencia_lectura,
    veredicto_raw_de_evidencia,
)
from voucherflow.classification.prompt_tipo_comprobante import (  # noqa: E402
    SYSTEM_PROMPT_POR_FUENTE,
)
from voucherflow.rules.contexto import (  # noqa: E402
    CONDICION_CONSUMIDOR_FINAL,
    CONDICION_RI,
    LETRAS_COMPROBANTE,
    ContextoTipoComprobante,
    normalizar_letra,
)
from voucherflow.rules.raw import (  # noqa: E402
    GRAVEDAD_POR_REGLA,
    REGISTRO_RAW,
    CampoDeclarado,
    Gravedad,
    evaluar_raw,
)
from voucherflow.rules.tipo_comprobante_rules import REGEX_LETRA_ENCABEZADO  # noqa: E402
from voucherflow.validation.vistas import VistaPreparada  # noqa: E402

# ---------------------------------------------------------------------------
# Utilidades de escenario
# ---------------------------------------------------------------------------

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
        nota="Vista de revisión sintética (T-303)",
    )


def _respuesta(**cambios: Any) -> str:
    """JSON de evidencia de lectura con los defaults del contrato."""
    datos: dict[str, Any] = {
        "tipo_detectado_por_documento": "A",
        "tipo_detectado_por_documento_explicacion": "FACTURA A COD. 001",
        "candidatos_descartados": [],
        "candidatos_restantes": [],
        "campos_desconocidos": [],
        "fuente_lectura": "llm",
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


#: Escenarios: (nombre, respuestas del lector por fuente, expectativa del
#: veredicto raw y — cuando aplica — de la decisión del motor).
ESCENARIOS: list[dict[str, Any]] = [
    {
        "nombre": "lectura_sana_llm",
        "respuestas": {
            "llm": _respuesta(
                tipo_detectado_por_documento="A",
                tipo_detectado_por_documento_explicacion="FACTURA A COD. 001",
            )
        },
        "esperado_negocio": "A",
        "expectativa": {
            "gravedad": "valida",
            "valida": True,
            "reglas": [],
            "letra": "A",
            "desc": [],
        },
    },
    {
        "nombre": "letra_fuera_vocabulario",
        "respuestas": {
            "llm": _respuesta(
                tipo_detectado_por_documento="Z",
                tipo_detectado_por_documento_explicacion="Recuadro ilegible",
            )
        },
        "esperado_negocio": "A",
        "expectativa": {
            "gravedad": "invalida",
            "valida": False,
            "reglas": ["RAW_VOCABULARIO"],
            "letra": "A",  # decide el negocio: la calificación raw no decide
            "crudo_en_debilidad": "'Z'",
        },
    },
    {
        "nombre": "codigo_de_tique_090",
        "respuestas": {
            "llm": _respuesta(
                tipo_detectado_por_documento="090",
                tipo_detectado_por_documento_explicacion="TIQUE FACTURA A",
            )
        },
        "esperado_negocio": "A",
        "expectativa": {
            "gravedad": "invalida",
            "valida": False,
            "reglas": ["RAW_VOCABULARIO"],  # D-13: los tiques no son vocabulario
            "letra": "A",
        },
    },
    {
        "nombre": "sustento_no_sostiene",
        "respuestas": {
            "llm": _respuesta(
                tipo_detectado_por_documento="A",
                tipo_detectado_por_documento_explicacion="Letra A en el margen",
            )
        },
        "esperado_negocio": "A",
        "expectativa": {
            "gravedad": "dudosa",
            "valida": True,  # dudosa sigue siendo utilizable como indicio
            "reglas": ["RAW_SUSTENTO"],
            "nota_r5": True,
            "letra": "A",
        },
    },
    {
        "nombre": "contradiccion_texto_B",
        "respuestas": {
            "llm": _respuesta(
                tipo_detectado_por_documento="A",
                tipo_detectado_por_documento_explicacion="FACTURA B COD. 006",
            )
        },
        "esperado_negocio": "A",
        "expectativa": {
            "gravedad": "dudosa",
            "valida": True,
            "reglas": ["RAW_SUSTENTO", "RAW_CONTRADICCION"],
            "desc": ["B"],
            "rest": ["A"],
            "letra": "B",  # R5 lee B del texto: gana en la cascada
        },
    },
    {
        "nombre": "fuente_incompleta",
        "respuestas": {
            "llm": _respuesta(
                tipo_detectado_por_documento=None,
                tipo_detectado_por_documento_explicacion=None,
            )
        },
        "esperado_negocio": "A",
        "expectativa": {
            "gravedad": "dudosa",
            "valida": True,
            "reglas": ["RAW_CAMPO"],
            "letra": "A",  # decide el negocio
        },
    },
    {
        "nombre": "recuadro_vlm_sin_patron_r5",
        "respuestas": {
            "vlm": _respuesta(
                tipo_detectado_por_documento="B",
                tipo_detectado_por_documento_explicacion="Recuadro grande con 'B' junto a COD. 006",
                fuente_lectura="vlm",
            )
        },
        "esperado_negocio": "A",
        "expectativa": {
            # El patrón de R5 es del **texto**: no se le exige al recuadro del VLM.
            "gravedad": "valida",
            "valida": True,
            "reglas": [],
            "letra": "B",  # R4 lee B; discrepancia con R2A → gana el documento
        },
    },
    {
        "nombre": "dos_fuentes_una_debil",
        "respuestas": {
            "vlm": _respuesta(
                tipo_detectado_por_documento="A",
                tipo_detectado_por_documento_explicacion="Recuadro con 'A' (COD. 01)",
                fuente_lectura="vlm",
            ),
            "llm": _respuesta(
                tipo_detectado_por_documento="Z",
                tipo_detectado_por_documento_explicacion="Texto ilegible",
                fuente_lectura="llm",
            ),
        },
        "esperado_negocio": "A",
        "expectativa": {
            # Cada fuente se califica por separado: el VLM queda válido y el LLM
            # inválido, sin contaminarse.
            "gravedad_por_fuente": {"vlm": "valida", "llm": "invalida"},
            "fuentes_validas": ["vlm"],
            "letra": "A",
        },
    },
    {
        "nombre": "conflicto_r7_con_contradiccion",
        "respuestas": {
            "llm": _respuesta(
                tipo_detectado_por_documento="B",
                tipo_detectado_por_documento_explicacion="FACTURA B COD. 006",
            )
        },
        "esperado_negocio": "A",  # emisor RI + receptor RI (espera A)
        "expectativa": {
            "gravedad": "valida",
            "valida": True,
            "reglas": [],
            "letra": "B",
            "alertas": ["R7"],
        },
    },
]


# ---------------------------------------------------------------------------
# Impresión
# ---------------------------------------------------------------------------


def _imprimir_reglas() -> None:
    """Imprime el registro raw con su prioridad y la gravedad que aporta."""
    print("Registro de reglas raw — pasada 1 por fuente (T-303)")
    print("=" * 78)
    for regla in sorted(REGISTRO_RAW.reglas, key=lambda r: r.prioridad):
        gravedad = GRAVEDAD_POR_REGLA.get(regla.id, Gravedad.dudosa).value
        print(f"  • {regla.id:<18} prioridad={regla.prioridad:<2} gravedad={gravedad}")
        print(f"      {regla.detalle}")
    print()


def _imprimir_campo_propio(campo: CampoDeclarado) -> None:
    """Corre el registro raw sobre un campo declarado propio (uso de F4)."""
    print(f"Campo propio: {campo.campo}")
    print("=" * 78)
    veredicto = evaluar_raw("propio", campo)
    estado = "válida" if veredicto.valida else "inválida"
    print(f"  {estado} ({veredicto.gravedad.value})")
    print(f"  reglas : {veredicto.reglas_aplicadas or '-'}")
    for debilidad in veredicto.debilidades:
        print(f"  ⚠ {debilidad}")
    print(
        f"  candidatos descartados={veredicto.candidatos_descartados or '-'} "
        f"restantes={veredicto.candidatos_restantes or '-'}"
    )
    print()


def _gravedad_de(veredicto, fuente: str) -> str | None:
    """Gravedad del veredicto de ``fuente`` (acepta uno solo o una lista)."""
    veredictos = veredicto if isinstance(veredicto, list) else [veredicto]
    for item in veredictos:
        if item.fuente == fuente:
            return item.gravedad.value
    return None


def _verificar(
    veredictos: list[Any],
    resultado,
    expectativa: dict[str, Any],
) -> list[str]:
    """Compara el resultado real con la expectativa; devuelve las diferencias."""
    problemas: list[str] = []

    if "gravedad_por_fuente" in expectativa:
        for fuente, esperada in expectativa["gravedad_por_fuente"].items():
            real = _gravedad_de(veredictos, fuente)
            if real != esperada:
                problemas.append(f"gravedad[{fuente}]={real!r} (esperado {esperada!r})")
    elif "gravedad" in expectativa:
        gravedades = [v.gravedad.value for v in veredictos]
        if expectativa["gravedad"] not in gravedades:
            problemas.append(f"gravedad={gravedades} (esperado {expectativa['gravedad']!r})")

    if "valida" in expectativa:
        for veredicto in veredictos:
            if veredicto.valida != expectativa["valida"]:
                problemas.append(
                    f"valida[{veredicto.fuente}]={veredicto.valida} "
                    f"(esperado {expectativa['valida']})"
                )

    if "reglas" in expectativa:
        disparadas = [regla for v in veredictos for regla in v.reglas_aplicadas]
        if sorted(set(disparadas)) != sorted(set(expectativa["reglas"])):
            problemas.append(f"reglas={disparadas} (esperado {expectativa['reglas']})")

    if "fuentes_validas" in expectativa:
        validas = [v.fuente for v in veredictos if v.valida]
        if validas != expectativa["fuentes_validas"]:
            problemas.append(
                f"fuentes_válidas={validas} (esperado {expectativa['fuentes_validas']})"
            )

    debilidades = [d for v in veredictos for d in v.debilidades]
    if expectativa.get("nota_r5"):
        if not any("R5" in debilidad for debilidad in debilidades):
            problemas.append("falta la nota de R5 en las debilidades")
    if "crudo_en_debilidad" in expectativa:
        if not any(expectativa["crudo_en_debilidad"] in d for d in debilidades):
            problemas.append(
                f"ninguna debilidad cita {expectativa['crudo_en_debilidad']}"
            )

    if "desc" in expectativa:
        descartados = [c for v in veredictos for c in v.candidatos_descartados]
        if sorted(descartados) != sorted(expectativa["desc"]):
            problemas.append(f"descartados={descartados} (esperado {expectativa['desc']})")
    if "rest" in expectativa:
        restantes = [c for v in veredictos for c in v.candidatos_restantes]
        for letra in expectativa["rest"]:
            if letra not in restantes:
                problemas.append(f"restantes={restantes} no incluye {letra!r}")

    if resultado is not None and "letra" in expectativa:
        if resultado.letra != expectativa["letra"]:
            problemas.append(f"letra={resultado.letra!r} (esperado {expectativa['letra']!r})")
    if resultado is not None and "alertas" in expectativa:
        disparadas = [alerta["regla"] for alerta in resultado.alertas]
        if sorted(disparadas) != sorted(expectativa["alertas"]):
            problemas.append(f"alertas={disparadas} (esperado {expectativa['alertas']})")
    return problemas


def _imprimir_escenario(
    escenario: dict[str, Any],
    veredictos: list[Any],
    resultado,
    problemas: list[str],
    *,
    detalle: bool,
) -> None:
    """Imprime una línea por escenario (y el detalle si se pidió)."""
    estado = "✅" if not problemas else "❌"
    partes: list[str] = []
    for veredicto in veredictos:
        gravedad = veredicto.gravedad.value[:4]
        partes.append(f"{veredicto.fuente}={gravedad}")
    letra = (resultado.letra if resultado is not None else None) or "-"
    print(
        f"  {estado} {escenario['nombre']:<32} "
        f"{' '.join(partes) or '-':<16} letra={letra:<3} "
        f"reglas_raw={','.join(r for v in veredictos for r in v.reglas_aplicadas) or '-'}"
    )
    for veredicto in veredictos:
        etiqueta = "válida" if veredicto.valida else "inválida"
        print(
            f"      · {veredicto.fuente}: {etiqueta} ({veredicto.gravedad.value}) "
            f"descartados={veredicto.candidatos_descartados or '-'} "
            f"restantes={veredicto.candidatos_restantes or '-'}"
        )
        if detalle:
            for debilidad in veredicto.debilidades:
                print(f"        ⚠ {debilidad}")
    if resultado is not None and resultado.alertas:
        print(f"      ⚠ {resultado.alertas[0]['mensaje'].split('.')[0]}.")
    if problemas:
        print(f"      ❌ no coincide con la expectativa: {'; '.join(problemas)}")


def _contexto_base(escenario: dict[str, Any]) -> ContextoTipoComprobante:
    """Contexto con las condiciones fiscales del escenario (parte de negocio).

    ``esperado_negocio="A"`` usa emisor RI + receptor RI (R2A → A); si no, el
    default del escenario es emisor RI + Consumidor Final (R2B → B). El negocio
    es el que fija la **letra esperada**; la lectura solo la cruza.
    """
    if escenario.get("esperado_negocio") == "A":
        return ContextoTipoComprobante(
            emisor_condicion_fiscal=CONDICION_RI,
            receptor_condicion_fiscal=CONDICION_RI,
        )
    return ContextoTipoComprobante(
        emisor_condicion_fiscal=CONDICION_RI,
        receptor_condicion_fiscal=CONDICION_CONSUMIDOR_FINAL,
    )


def _correr_escenario(escenario: dict[str, Any]) -> tuple[list[Any], Any]:
    """Corre T-302 (leer) → T-303 (calificar) → T-301 (decidir).

    Solo se corren las fuentes que el escenario declara: así cada caso ejercita
    exactamente la fuente que quiere mostrar (sin que un doble responda ``{}``
    por una fuente no prevista).
    """
    lector = LectorDoble(escenario["respuestas"])
    fuentes = list(escenario["respuestas"])
    lectura = leer_evidencia(
        lector,
        markdown="FACTURA A\nProveedor: Ejemplo S.A.",
        vista=_vista_imagen() if "vlm" in fuentes else None,
        documento_id=escenario["nombre"],
        fuentes=fuentes,
        contexto_base=_contexto_base(escenario),
    )
    veredictos = list(lectura.veredictos)
    if lectura.lecturas:
        resultado = clasificar_tipo_comprobante(
            lectura.contexto, candidatos_raw=lectura
        )
    else:
        resultado = clasificar_tipo_comprobante(_contexto_base(escenario))
    return veredictos, resultado


def _campo_desde_json(ruta: Path) -> CampoDeclarado:
    """Construye un ``CampoDeclarado`` desde un JSON (uso de F4/T-403).

    Claves admitidas: ``campo``, ``valor``, ``fragmento``, ``vocabulario``
    (lista), ``normalizador`` (``"letra"`` para el de tipo/letra, o ausente),
    ``patron_sustento`` (``"r5"`` para la regex de R5, o una regex propia),
    ``exigir_sustento`` y ``nota``.
    """
    datos = json.loads(ruta.read_text(encoding="utf-8"))
    if not isinstance(datos, dict):
        raise TypeError(
            f"El JSON del campo debe ser un objeto; recibido: {type(datos).__name__}."
        )
    normalizador = normalizar_letra if datos.get("normalizador") == "letra" else None
    patron = datos.get("patron_sustento")
    if patron == "r5":
        patron = REGEX_LETRA_ENCABEZADO
    elif isinstance(patron, str):
        patron = re.compile(patron, re.IGNORECASE)
    else:
        patron = None
    vocabulario = datos.get("vocabulario")
    return CampoDeclarado(
        campo=str(datos.get("campo") or "campo"),
        valor=datos.get("valor"),
        fragmento=str(datos.get("fragmento") or ""),
        vocabulario=tuple(vocabulario) if vocabulario else None,
        normalizador=normalizador,
        patron_sustento=patron,
        exigir_sustento=bool(datos.get("exigir_sustento", True)),
        nota=str(datos.get("nota") or ""),
    )


def main() -> None:
    parser = argparse.ArgumentParser(
        description=(
            "Inspecciona las reglas raw por fuente (F3 / T-303): veredicto por "
            "fuente, candidatos y la cadena T-302 → T-303 → T-301. No requiere "
            "Ollama."
        )
    )
    parser.add_argument(
        "--caso",
        action="append",
        help="Filtra por nombre de escenario (repetible). Default: todos.",
    )
    parser.add_argument("--reglas", action="store_true", help="Imprime la tabla de reglas.")
    parser.add_argument("--detalle", action="store_true", help="Debilidades por escenario.")
    parser.add_argument("--campo", type=Path, help="Campo propio en JSON (uso de F4).")
    parser.add_argument("--json", type=Path, help="Escribe el reporte completo en JSON.")
    args = parser.parse_args()

    if args.reglas:
        _imprimir_reglas()

    reporte: dict[str, Any] = {
        "tarea": "T-303",
        "fase": "F3",
        "vocabulario": sorted(LETRAS_COMPROBANTE),
        "escenarios": [],
    }
    fallos = 0

    if args.campo is not None:
        if not args.campo.exists():
            print(f"No existe el campo: {args.campo}", file=sys.stderr)
            sys.exit(2)
        campo = _campo_desde_json(args.campo)
        _imprimir_campo_propio(campo)
        veredicto = evaluar_raw("propio", campo)
        reporte["campo_propio"] = {
            "campo": campo.campo,
            "veredicto": {
                "valida": veredicto.valida,
                "gravedad": veredicto.gravedad.value,
                "reglas_aplicadas": veredicto.reglas_aplicadas,
                "debilidades": veredicto.debilidades,
                "candidatos_descartados": veredicto.candidatos_descartados,
                "candidatos_restantes": veredicto.candidatos_restantes,
            },
        }

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
        print("Escenarios de la pasada raw por fuente (T-303)")
        print("=" * 78)
        for escenario in seleccionados:
            veredictos, resultado = _correr_escenario(escenario)
            problemas = _verificar(veredictos, resultado, escenario["expectativa"])
            fallos += int(bool(problemas))
            _imprimir_escenario(
                escenario, veredictos, resultado, problemas, detalle=args.detalle
            )
            reporte["escenarios"].append(
                {
                    "nombre": escenario["nombre"],
                    "coincide": not problemas,
                    "diferencias": problemas,
                    "veredictos": [
                        {
                            "fuente": veredicto.fuente,
                            "valida": veredicto.valida,
                            "gravedad": veredicto.gravedad.value,
                            "reglas_aplicadas": veredicto.reglas_aplicadas,
                            "debilidades": veredicto.debilidades,
                            "candidatos_descartados": veredicto.candidatos_descartados,
                            "candidatos_restantes": veredicto.candidatos_restantes,
                        }
                        for veredicto in veredictos
                    ],
                    "letra_final": resultado.letra,
                    "reglas_motor": resultado.reglas_aplicadas,
                    "alertas": [alerta["regla"] for alerta in resultado.alertas],
                    "detalle": resultado.detalle,
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


if __name__ == "__main__":
    main()

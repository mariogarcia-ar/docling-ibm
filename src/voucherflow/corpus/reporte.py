"""Resumen de una corrida de reducción, con honestidad sobre lo medido.

La regla es la del resto del repo: **lo que se puede calcular, se calcula en
código**, y lo que no se midió **se declara**, no se estima como si fuera un
dato. Por eso la reducción de peso se calcula solo sobre los pares medidos (en
el modo de solo medición no hay peso destino, y comparar contra un 0 daría un
«-100%» inventado).
"""

from __future__ import annotations

from typing import Any, Sequence

from .dimensiones import ORIGEN_DEFAULTS, tokens_estimados_vlm
from .modelo import (
    ESTADO_FALLO,
    ESTADO_OMITIDO,
    ESTADO_REDUCIDO,
    Opciones,
    Resultado,
)

#: Campos comparables antes/después (peso en bytes, tokens de visión).
CAMPOS = ("peso", "tokens")


def _valor(r: Resultado, campo: str, *, origen: bool) -> int | None:
    """``peso_*`` o ``tokens_*`` de un resultado (``None`` si no se midió)."""
    if campo == "peso":
        return r.peso_origen if origen else r.peso_destino
    dims = r.dims_origen if origen else r.dims_destino
    return tokens_estimados_vlm(*dims) if dims else None


def _sumatoria(resultados: Sequence[Resultado], campo: str, *, origen: bool) -> int:
    """Suma ``peso_*`` o ``tokens_*`` de los resultados, ignorando ``None``."""
    return sum(
        valor
        for valor in (_valor(r, campo, origen=origen) for r in resultados)
        if valor
    )


def comparar(resultados: Sequence[Resultado], campo: str) -> tuple[int, int, float | None]:
    """Compara antes/después **solo donde hay medición de destino**.

    En el modo de solo medición el peso destino no existe (no se escribió):
    comparar contra un 0 daría un «-100%» inventado. Los tokens sí se pueden
    comparar siempre que las dos dimensiones se hayan podido calcular.

    Devuelve ``(antes, despues, reduccion_pct | None)``.
    """
    pares = [
        (_valor(r, campo, origen=True), _valor(r, campo, origen=False))
        for r in resultados
    ]
    medidos = [(a, d) for a, d in pares if a and d is not None]
    antes = sum(a for a, _ in medidos)
    despues = sum(d for _, d in medidos)
    if not antes:
        return 0, 0, None
    return antes, despues, round((1 - despues / antes) * 100, 1)


def resumen(resultados: Sequence[Resultado], opciones: Opciones) -> dict[str, Any]:
    """Resumen agregado de la corrida (dict, serializable a JSON).

    ``peso_origen_bytes`` es el total de **todo** lo recorrido; los porcentajes
    de reducción se calculan solo sobre los pares medidos (ver :func:`comparar`)
    para no reportar una reducción que no se midió.
    """
    por_estado: dict[str, int] = {}
    for r in resultados:
        por_estado[r.estado] = por_estado.get(r.estado, 0) + 1

    peso_o_cmp, peso_d_cmp, pct_peso = comparar(resultados, "peso")
    tok_o, tok_d, pct_tok = comparar(resultados, "tokens")

    return {
        "archivos": len(resultados),
        "reducidos": por_estado.get(ESTADO_REDUCIDO, 0),
        "omitidos": por_estado.get(ESTADO_OMITIDO, 0),
        "fallos": por_estado.get(ESTADO_FALLO, 0),
        "peso_origen_bytes": _sumatoria(resultados, "peso", origen=True),
        "peso_destino_bytes": _sumatoria(resultados, "peso", origen=False),
        "peso_comparado_origen_bytes": peso_o_cmp,
        "peso_comparado_destino_bytes": peso_d_cmp,
        "reduccion_peso_pct": pct_peso,
        "tokens_origen_estimados": _sumatoria(resultados, "tokens", origen=True),
        "tokens_destino_estimados": _sumatoria(resultados, "tokens", origen=False),
        "tokens_comparados_origen": tok_o,
        "tokens_comparados_destino": tok_d,
        "reduccion_tokens_pct": pct_tok,
        "opciones": opciones.a_dict(),
        "defaults_desde": ORIGEN_DEFAULTS,
    }

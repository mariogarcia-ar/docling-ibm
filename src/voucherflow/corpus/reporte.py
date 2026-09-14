"""Resumen de una corrida de reducción, con honestidad sobre lo medido.

La regla es la del resto del repo: **lo que se puede calcular, se calcula en
código**, y lo que no se midió **se declara**, no se estima como si fuera un
dato. Por eso la reducción de peso se calcula solo sobre los pares medidos (en
el modo de solo medición no hay peso destino, y comparar contra un 0 daría un
«-100%» inventado).
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import Any

from .dimensiones import ORIGEN_DEFAULTS, tokens_estimados_vlm
from .modelo import (
    CATEGORIA_LECTURA,
    CATEGORIA_REANUDADO,
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
    """Suma ``peso_*`` o ``tokens_*``, ignorando lo que no se midió.

    ``is not None`` y no ``if valor``: un 0 medido es un dato, y un archivo de 0
    bytes cuenta. La comprobación contraria (la tupla de ``comparar``) ya lo
    hacía así; era la única inconsistencia en la regla de honestidad del módulo.
    """
    return sum(
        valor
        for valor in (_valor(r, campo, origen=origen) for r in resultados)
        if valor is not None
    )


def contar_categoria(resultados: Sequence[Resultado], categoria: str) -> int:
    """Cuántos resultados traen esa categoría.

    Se cuenta el **dato**, no el texto del motivo: retocar un mensaje cambiaba
    los números del reporte en silencio (una «copia» —deja archivo, la salida
    queda completa— y una omisión que no escribe nada son las dos ``omitido``,
    y significan lo contrario para el operador).
    """
    return sum(1 for r in resultados if r.categoria == categoria)


def contar_errores_lectura(resultados: Sequence[Resultado]) -> int:
    """Fallos de lectura: una imagen ilegible no es un disco lleno."""
    return sum(
        1
        for r in resultados
        if r.estado == ESTADO_FALLO and r.categoria == CATEGORIA_LECTURA
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
    medidos = [(a, d) for a, d in pares if a is not None and d is not None]
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
        "copiadas": sum(1 for r in resultados if r.es_copia),
        "ya_estaba": contar_categoria(resultados, CATEGORIA_REANUDADO),
        "fallos_lectura": contar_errores_lectura(resultados),
        "engordaron": sum(1 for r in resultados if r.engordo),
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

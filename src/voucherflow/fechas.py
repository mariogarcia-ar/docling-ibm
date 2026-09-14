"""Fechas: la política del sistema en un solo lugar.

Existe porque las fechas se escribían con **criterios distintos** en módulos que
tienen que coincidir. El caso que lo destapó es el mismo patrón que el de los
montos: el **pipeline** normaliza la fecha que lee del comprobante con
:func:`~voucherflow.extraction.key_value.normalizar_fecha` (tolera ISO con hora,
formato compacto, fecha escrita, y **rechaza el año de dos dígitos** porque
elegir el siglo sería inventar), y el **evaluador** comparaba con una lista de
formatos propia que:

- **no** entendía el ISO con hora (``2025-08-14T10:30:00``) ni el compacto ni el
  escrito → la fecha quedaba como *no verificable* aunque hubiera que compararla;
- **sí** aceptaba ``14/08/25`` e **inventaba el siglo** (2025) → justo lo que la
  regla del pipeline prohíbe.

Cada diferencia es un veredicto distinto sobre el mismo dato, sin que nada falle.
Por eso acá no se define un formato: se define **una función** y todos la usan.

Lo que esta política sostiene:

1. **La fecha canónica es ISO ``YYYY-MM-DD``.** Es lo que se guarda, se compara y
   se publica. Cualquier otra forma es de entrada.
2. **No se inventa lo que falta.** Un año de dos dígitos, un mes suelto o una
   fecha inexistente (31 de febrero) no se normalizan: se devuelve ``None`` y el
   llamador conserva el crudo **con aviso**. Es preferible un dato declarado
   ilegible a una fecha creíble y falsa.
3. **Comparar es comparar fechas, no textos.** ``14/08/2025`` y ``2025-08-14``
   son la misma fecha; compararlas como string reportaría una discrepancia
   inexistente.

⚠️ **El formato AFIP (``YYYYMMDD``) es de la frontera, no del dominio.** Vive en
:func:`formato_afip` porque el WSCDC lo pide así, pero no es una forma de
*interpretar* una fecha: es cómo se escribe en un protocolo externo.
"""

from __future__ import annotations

import re
from datetime import date, datetime, timezone
from typing import Any

#: Separador de la parte horaria en un ISO (``2025-08-14T10:30:00``).
#: Se corta ahí en vez de parsear el instante completo: de una fecha de emisión
#: interesa el **día**, no el huso en que se emitió.
#: ⚠️ Es una **búsqueda** del separador seguido de la hora, no un patrón que la
#: consuma entera: consumirla obliga a contemplar segundos, microsegundos y
#: offset, y olvidar uno deja basura en el resultado (``…:00``).
_RE_INICIO_HORA = re.compile(r"[T ]\d{2}:\d{2}")

#: Fecha ISO estricta (la forma canónica).
_RE_ISO = re.compile(r"^(\d{4})-(\d{2})-(\d{2})$")

#: ``YYYYMMDD`` (el formato que pide el padrón AFIP).
_RE_AFIP = re.compile(r"^(\d{4})(\d{2})(\d{2})$")


def ahora_utc_iso() -> str:
    """Timestamp ISO-8601 en UTC, con zona.

    Es el ``timestamp`` que llevan los metadatos de evidencia (y la trazabilidad
    de una corrida). UTC y no hora local a propósito: un registro que se compara
    entre máquinas no puede depender de la zona del que lo escribió.
    """
    return datetime.now(timezone.utc).isoformat()


def como_fecha(valor: Any) -> date | None:
    """Interpreta ``valor`` como una fecha del calendario, o ``None``.

    Único punto de entrada para **comparar** fechas: normaliza con
    :func:`~voucherflow.extraction.key_value.normalizar_fecha` —la regla del
    pipeline, que no inventa lo que falta— y recién después construye el
    ``date``.

    ⚠️ Delega en vez de tener su propia lista de formatos. Una lista aparte
    diverge: la que había acá no entendía el ISO con hora (dejaba la fecha como
    no verificable) y aceptaba el año de dos dígitos (inventaba el siglo).
    """
    from .extraction.key_value import normalizar_fecha

    iso = normalizar_fecha(valor)
    if iso is None:
        return None
    coincidencia = _RE_ISO.match(iso)
    if coincidencia is None:  # pragma: no cover - normalizar_fecha garantiza ISO
        return None
    return date(*(int(g) for g in coincidencia.groups()))


def sin_hora(valor: Any) -> str:
    """La parte de fecha de un ISO con hora (``2025-08-14T10:30`` → ``2025-08-14``).

    Se usa al **leer** una fecha que viene de afuera (la carga de datos, un
    registro guardado). Es a propósito una operación de texto y no un parseo:
    descarta la hora sin decidir nada sobre el huso, que para una fecha de
    emisión no aporta y para un corte de día sería una decisión que nadie tomó.

    Corta en donde **empieza** la hora en vez de reemplazarla: así no hay que
    enumerar la forma completa del instante (segundos, microsegundos, offset).
    """
    texto = str(valor or "").strip()
    coincidencia = _RE_INICIO_HORA.search(texto)
    if coincidencia is None:
        return texto
    return texto[: coincidencia.start()]


def formato_afip(valor: Any) -> str:
    """Convierte una fecha ISO a ``YYYYMMDD`` (formato del WSCDC).

    Si no es un ISO reconocible se devuelve el texto tal cual: el padrón admite
    la fecha vacía y decide él. **No se inventa una fecha** (mismo criterio que
    el resto del módulo).
    """
    coincidencia = _RE_ISO.match(str(valor or "").strip())
    if coincidencia is None:
        return str(valor or "")
    return "".join(coincidencia.groups())


def es_afip_valido(valor: Any) -> bool:
    """True si el texto ya está en ``YYYYMMDD`` (y es una fecha real)."""
    coincidencia = _RE_AFIP.match(str(valor or "").strip())
    if coincidencia is None:
        return False
    anio, mes, dia = (int(g) for g in coincidencia.groups())
    try:
        date(anio, mes, dia)
    except ValueError:
        return False
    return True

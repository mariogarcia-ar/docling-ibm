"""Evaluación determinística de una extracción contra los datos cargados.

El prompt de validación pide comparar lo leído contra lo cargado, y él mismo
aconseja hacerlo **en código**: «es más barato y más auditable». Este módulo es
esa implementación: las reglas de negocio del prompt, en Python, sin llamar a
ningún modelo.

Por qué vive en la librería
---------------------------
Es la **referencia contra la que se mide** un prompt o un modelo. Sin una
evaluación determinística, comparar dos proveedores termina siendo «el LLM dijo
que coinciden», que no es auditable: un modelo puede afirmar que dos totales
coinciden cuando difieren en $548,46 (pasó, y por eso la suma se hace acá).

La regla que lo gobierna: **lo que se puede calcular, se calcula en código**.
Un veredicto del modelo sobre aritmética o sobre CUIT no reemplaza a este
módulo; se compara contra él.

Portado sin cambios del evaluador que ya estaba validado contra el corpus real
(15 reglas), para no re-derivar decisiones de negocio que costaron ajustar.
"""

from __future__ import annotations

import re
import unicodedata
from datetime import date, datetime
from typing import Any

from voucherflow.extraction.key_value import normalizar_monto

# ---------------------------------------------------------------------------
# Constantes de las reglas
# ---------------------------------------------------------------------------

#: tildes: el conjunto tiene que estar en esa misma forma o nunca coincide.
CATEGORIAS_CON_COMENSALES = frozenset(
    {"RESTAURANTE", "SUPERMERCADO", "HOSPEDAJE", "BAR", "CAFETERIA", "PANADERIA"}
)

#: Categorías que requieren ``cantidad_litros`` (regla 14).
CATEGORIAS_CON_LITROS = frozenset({"COMBUSTIBLE", "NAFTA", "GASOIL", "GNC"})

#: Tolerancia para comparar montos (redondeo de centavos).
TOLERANCIA_MONTO = 0.011

# ---------------------------------------------------------------------------
# Helpers de comparación
# ---------------------------------------------------------------------------

def _norm(texto: Any) -> str:
    """Normaliza para comparar: sin tildes, mayúsculas, espacios colapsados."""
    if texto is None:
        return ""
    sin_acentos = (
        unicodedata.normalize("NFKD", str(texto))
        .encode("ascii", "ignore")
        .decode("ascii")
    )
    return re.sub(r"\s+", " ", sin_acentos).strip().upper()


def _solo_digitos(texto: Any) -> str:
    return re.sub(r"\D", "", str(texto or ""))


def _norm_razon_social(texto: Any) -> str:
    """Normaliza una razón social para comparar (regla 2 del prompt).

    Tolerar diferencias **menores de formato**: mayúsculas, tildes y sobre todo
    «S.A. vs SA» — por eso además de :func:`_norm` se quita toda puntuación y los
    espacios, que es lo único que suele cambiar entre lo impreso y lo cargado.
    """
    return re.sub(r"[^A-Z0-9]", "", _norm(texto))


def a_numero(valor: Any) -> float | None:
    """Convierte un monto a número con la **convención decimal argentina**.

    Delega en :func:`voucherflow.extraction.key_value.normalizar_monto`, que es
    la implementación única de la regla: ``.`` separa miles y ``,`` es decimal,
    salvo que haya dos tipos de separador (ahí el último es el decimal).

    Se delegó a propósito en vez de mantener una copia propia. El evaluador y el
    pipeline tienen que leer un monto **igual**: son las dos mitades de la misma
    comparación (una lee el comprobante, la otra la carga), y una regla duplicada
    se desincroniza —ya pasó: la copia del evaluador leía ``"1.000"`` como ``1``
    y ``"1.234.567"`` como ilegible, reportando discrepancias falsas justo en los
    importes grandes.

    Devuelve ``None`` si no hay un número reconocible; el ``0`` es un valor
    legítimo, no una ausencia.
    """
    resultado = normalizar_monto(valor)
    return float(resultado) if resultado is not None else None


def _mismo_monto(a: Any, b: Any) -> bool | None:
    """Compara dos montos con tolerancia. ``None`` si alguno no es numérico."""
    na, nb = a_numero(a), a_numero(b)
    if na is None or nb is None:
        return None
    return abs(na - nb) < TOLERANCIA_MONTO


def _a_fecha(valor: Any) -> date | None:
    """Parsea fechas habituales de comprobantes."""
    texto = str(valor or "").strip()
    if not texto:
        return None
    for formato in ("%d/%m/%Y", "%d-%m-%Y", "%Y-%m-%d", "%d/%m/%y", "%d.%m.%Y"):
        try:
            return datetime.strptime(texto, formato).date()
        except ValueError:
            continue
    return None


def _campo(
    valor_comprobante: Any, valor_cargado: Any, coincide: Any, observacion: str | None
) -> dict[str, Any]:
    return {
        "valor_comprobante": valor_comprobante,
        "valor_cargado": valor_cargado,
        "coincide": coincide,
        "observacion": observacion,
    }


def _comparar_impuestos(extraccion: dict, datos: dict) -> dict[str, Any]:
    """Regla 8: sumar lo discriminado y comparar por categoría."""
    carga = datos.get("impuestos") or {}
    discriminado = extraccion.get("discrimina_impuestos")
    pares = {
        "iva": ("iva", "iva"),
        "impuestos_internos": ("impuestos_internos", "impuestos_internos"),
        "percepciones_iibb": ("percepciones_iibb", "percepciones_iibb"),
        "otros": ("otros_impuestos", "otros"),
    }
    salida: dict[str, Any] = {}
    for clave_salida, (clave_ext, clave_carga) in pares.items():
        leido = extraccion.get(clave_ext)
        cargado = carga.get(clave_carga)
        if discriminado is False and a_numero(cargado) not in (None, 0.0):
            salida[clave_salida] = _campo(
                leido,
                cargado,
                False,
                "El comprobante no discrimina impuestos y el campo tiene un valor cargado.",
            )
            continue
        if leido is None and cargado in (None, 0):
            salida[clave_salida] = _campo(
                None, cargado, "no_verificable", "Sin impuesto discriminado en el comprobante."
            )
            continue
        mismo = _mismo_monto(leido or 0, cargado or 0)
        salida[clave_salida] = _campo(
            leido,
            cargado,
            mismo if mismo is not None else "no_verificable",
            None
            if mismo
            else (
                "No discriminado en el comprobante."
                if discriminado is False
                else "Diferencia sin explicar por las reglas de negocio."
            ),
        )
    return salida

# ---------------------------------------------------------------------------
# Diff campo a campo (las 15 reglas)
# ---------------------------------------------------------------------------

def diff_deterministico(extraccion: dict, datos: dict) -> dict[str, Any]:
    """Compara la extracción contra los datos cargados, con las reglas del prompt.

    Devuelve el **mismo formato** que el modo ``validar`` (``estado_global``,
    ``campos``, ``discrepancias_criticas``, ``campos_no_legibles``), para que
    aguas abajo el consumo sea uniforme.
    """
    campos: dict[str, Any] = {}
    criticas: list[str] = []

    def marcar(nombre: str, campo: dict[str, Any], critico: bool) -> None:
        campos[nombre] = campo
        if critico and campo["coincide"] is False:
            criticas.append(nombre)

    # 1. Tipo de comprobante: 090 y 099 son indistintos (regla 1).
    leido_tipo, cargado_tipo = _norm(extraccion.get("tipo_comprobante")), _norm(
        datos.get("tipo_comprobante")
    )
    if {leido_tipo, cargado_tipo} <= {"090", "099"} and leido_tipo and cargado_tipo:
        marcar(
            "tipo_comprobante",
            _campo(
                extraccion.get("tipo_comprobante"),
                datos.get("tipo_comprobante"),
                True,
                "090/099 son indistintos por regla de negocio.",
            ),
            True,
        )
    else:
        marcar(
            "tipo_comprobante",
            _campo(
                extraccion.get("tipo_comprobante"),
                datos.get("tipo_comprobante"),
                (leido_tipo == cargado_tipo) if leido_tipo and cargado_tipo else "no_verificable",
                None if leido_tipo == cargado_tipo else "El tipo leído no coincide con el cargado.",
            ),
            True,
        )

    # 2. Razón social (regla 2: tolera mayúsculas, tildes y «S.A. vs SA»).
    leido_rs = _norm_razon_social(extraccion.get("razon_social_emisor"))
    cargado_rs = _norm_razon_social(datos.get("razon_social_emisor"))
    marcar(
        "razon_social_emisor",
        _campo(
            extraccion.get("razon_social_emisor"),
            datos.get("razon_social_emisor"),
            (leido_rs == cargado_rs) if leido_rs and cargado_rs else "no_verificable",
            None
            if leido_rs == cargado_rs
            else "El nombre difiere más allá del formato (mayúsculas/tildes/puntuación).",
        ),
        False,
    )

    # 3. CUIT: dígito a dígito sobre los 11 dígitos.
    leido_cuit, cargado_cuit = _solo_digitos(extraccion.get("cuit_emisor")), _solo_digitos(
        datos.get("cuit_emisor")
    )
    marcar(
        "cuit_emisor",
        _campo(
            extraccion.get("cuit_emisor"),
            datos.get("cuit_emisor"),
            (leido_cuit == cargado_cuit) if leido_cuit and cargado_cuit else "no_verificable",
            None if leido_cuit == cargado_cuit else "El CUIT leído no coincide con el cargado.",
        ),
        True,
    )

    # 4. Fecha de emisión (se compara como fecha, no como texto).
    fecha_leida, fecha_cargada = _a_fecha(extraccion.get("fecha_emision")), _a_fecha(
        datos.get("fecha_emision")
    )
    marcar(
        "fecha_emision",
        _campo(
            extraccion.get("fecha_emision"),
            datos.get("fecha_emision"),
            (fecha_leida == fecha_cargada)
            if fecha_leida and fecha_cargada
            else "no_verificable",
            None
            if fecha_leida == fecha_cargada
            else "La fecha del comprobante no coincide con la cargada.",
        ),
        True,
    )

    # 5. Nro de factura (se ignora el separador punto de venta-número).
    leido_nro = re.sub(r"[^A-Z0-9]", "", _norm(extraccion.get("nro_factura")))
    cargado_nro = re.sub(r"[^A-Z0-9]", "", _norm(datos.get("nro_factura")))
    marcar(
        "nro_factura",
        _campo(
            extraccion.get("nro_factura"),
            datos.get("nro_factura"),
            (leido_nro == cargado_nro) if leido_nro and cargado_nro else "no_verificable",
            None if leido_nro == cargado_nro else "El número leído no coincide con el cargado.",
        ),
        False,
    )

    # 6. Moneda.
    leido_mon, cargado_mon = _norm(extraccion.get("moneda")), _norm(datos.get("moneda"))
    marcar(
        "moneda",
        _campo(
            extraccion.get("moneda"),
            datos.get("moneda"),
            (leido_mon == cargado_mon) if leido_mon and cargado_mon else "no_verificable",
            None if leido_mon == cargado_mon else "La moneda no coincide.",
        ),
        False,
    )

    # 7. Subtotal (NETO si discrimina; si no discrimina, subtotal == total).
    # Ojo: el neto gravado NO incluye el exento ni el no gravado, así que para
    # comprobantes tipo B/C sin discriminación se contrasta contra el total.
    mismo_subtotal = _mismo_monto(extraccion.get("subtotal"), datos.get("subtotal"))
    if mismo_subtotal is None and extraccion.get("discrimina_impuestos") is False:
        mismo_subtotal = _mismo_monto(
            extraccion.get("importe_total"), datos.get("subtotal")
        )
    marcar(
        "subtotal",
        _campo(
            extraccion.get("subtotal"),
            datos.get("subtotal"),
            mismo_subtotal if mismo_subtotal is not None else "no_verificable",
            None
            if mismo_subtotal
            else "El subtotal cargado no coincide con el neto del comprobante.",
        ),
        True,
    )

    # 7 bis. Exento / no gravado: el prompt los trata aparte del subtotal, así
    # que una diferencia ahí NO es un error de subtotal (es un dato que el
    # comprobante discrimina y la carga puede no reflejar como campo propio).
    for nombre_pdf, clave_ext, clave_datos in (
        ("importe_no_gravado", "no_gravado", "monto_no_gravado"),
        ("importe_exento", "exento", None),
    ):
        leido = extraccion.get(clave_ext)
        cargado = datos.get(clave_datos) if clave_datos else None
        if leido is None and cargado in (None, 0):
            coincide: Any = "no_verificable"
        elif clave_datos is None:
            coincide = "no_verificable"
        else:
            coincide = _mismo_monto(leido, cargado)
        campos[nombre_pdf] = _campo(
            leido,
            cargado,
            coincide,
            None
            if coincide in (True, "no_verificable")
            else "El importe no coincide con el cargado.",
        )

    # 8. Impuestos.
    campos["impuestos"] = _comparar_impuestos(extraccion, datos)
    if any(c["coincide"] is False for c in campos["impuestos"].values()):
        criticas.append("impuestos")

    # 9 y 10. Monto no gravado y total (la diferencia puede estar explicada).
    no_gravado = a_numero(datos.get("monto_no_gravado"))
    total_leido = a_numero(extraccion.get("importe_total"))
    total_cargado = a_numero(datos.get("importe_total_facturado"))
    diferencia = (
        round(total_cargado - total_leido, 2)
        if total_cargado is not None and total_leido is not None
        else None
    )
    if diferencia is None or abs(diferencia) < TOLERANCIA_MONTO:
        explicada: Any = "no_aplica" if not no_gravado else False
        coincide_total: Any = True if diferencia is not None else "no_verificable"
    elif no_gravado and abs(diferencia - no_gravado) < TOLERANCIA_MONTO:
        explicada, coincide_total = True, True
    else:
        explicada, coincide_total = False, False

    campos["monto_no_gravado"] = {
        "valor_cargado": datos.get("monto_no_gravado"),
        "consistente_con_total": (
            "no_aplica"
            if not no_gravado
            else (True if explicada is True else False)
        ),
        "observacion": (
            None
            if not no_gravado
            else (
                "El total cargado excede el impreso justo por el monto no gravado."
                if explicada is True
                else "Hay monto no gravado cargado pero el total cargado coincide con el del comprobante."
            )
        ),
    }
    marcar(
        "importe_total_facturado",
        {
            "valor_comprobante": extraccion.get("importe_total"),
            "valor_cargado": datos.get("importe_total_facturado"),
            "coincide": coincide_total,
            "diferencia": diferencia,
            "diferencia_explicada_por_monto_no_gravado": explicada,
            "observacion": None
            if coincide_total is True
            else "El total difiere y no lo explica el monto no gravado.",
        },
        True,
    )

    # 11. Categoría de gasto vs rubro sugerido.
    leido_cat = _norm(extraccion.get("categoria_gasto_sugerida"))
    cargado_cat = _norm(datos.get("categoria_gasto"))
    categoria_ok: Any = (
        (leido_cat == cargado_cat) if leido_cat and cargado_cat else "no_verificable"
    )
    campos["categoria_gasto"] = {
        "valor_cargado": datos.get("categoria_gasto"),
        "consistente_con_rubro_emisor": categoria_ok,
        "observacion": None
        if categoria_ok in (True, "no_verificable")
        else f"El rubro del emisor sugiere «{extraccion.get('categoria_gasto_sugerida')}».",
    }

    # 12. Notas: no se valida; se sugiere aclaración si hay discrepancias.
    campos["notas"] = {
        "valor_cargado": datos.get("notas"),
        "aclaracion_sugerida": bool(criticas) and not (datos.get("notas") or "").strip(),
        "observacion": None,
    }

    # 13. Comensales/personas.
    requiere_comensales = cargado_cat in CATEGORIAS_CON_COMENSALES
    comensales = a_numero(datos.get("cantidad_comensales_personas"))
    campos["cantidad_comensales_personas"] = {
        "requerido_por_categoria": requiere_comensales,
        "valor_cargado": datos.get("cantidad_comensales_personas"),
        "observacion": (
            "La categoría lo requiere y el campo está vacío o en cero."
            if requiere_comensales and not comensales
            else None
        ),
    }

    # 14. Litros (sí suele estar impreso en el ticket).
    requiere_litros = cargado_cat in CATEGORIAS_CON_LITROS
    mismo_litros = _mismo_monto(
        extraccion.get("cantidad_litros"), datos.get("cantidad_litros")
    )
    campos["cantidad_litros"] = {
        "requerido_por_categoria": requiere_litros,
        "valor_comprobante": extraccion.get("cantidad_litros"),
        "valor_cargado": datos.get("cantidad_litros"),
        "coincide": (mismo_litros if mismo_litros is not None else "no_verificable"),
        "observacion": None
        if mismo_litros in (True, None)
        else "Los litros leídos no coinciden con los cargados.",
    }

    # 15. Centro de costo: informativo.
    campos["centro_de_costo"] = {
        "valor_cargado": datos.get("centro_de_costo"),
        "nota": "Informativo, no controlado por aprobadores",
    }

    no_legibles = list(extraccion.get("campos_no_legibles") or [])
    if _norm(extraccion.get("legibilidad")) == "MALA":
        estado = "INCOMPLETO"
    elif criticas:
        estado = "REVISAR"
    else:
        estado = "OK"

    resumen = {
        "OK": "Todos los campos verificables coinciden con el comprobante.",
        "REVISAR": "Hay discrepancias relevantes: " + ", ".join(criticas) + ".",
        "INCOMPLETO": "La imagen no permite verificar los campos clave.",
    }[estado]

    return {
        "estado_global": estado,
        "resumen": resumen,
        "campos": campos,
        "discrepancias_criticas": criticas,
        "campos_no_legibles": no_legibles,
    }

# ---------------------------------------------------------------------------
# Aritmética del comprobante
# ---------------------------------------------------------------------------

#: Campos de importe que deben sumar el total (en el orden en que se suman).
COMPONENTES_DEL_TOTAL = (
    "subtotal",
    "no_gravado",
    "exento",
    "iva",
    "impuestos_internos",
    "percepciones_iibb",
    "otros_impuestos",
)


def verificar_aritmetica(extraccion: dict) -> dict[str, Any]:
    """Comprueba **en Python** si los importes leídos suman el total.

    No se le cree al modelo: su ``cierra_aritmetica`` sale ``true`` incluso
    cuando la suma no da (se comprobó con un comprobante real: declaró ``true``
    con 10,00 de diferencia). Es la misma regla que el prompt aplica al diff de
    campos — lo que se puede calcular, se calcula en código y se audita.

    Una diferencia acá es una **señal valiosa**: o falta un importe (p. ej. una
    línea que el modelo no transcribió) o el modelo leyó mal un dígito.

    Devuelve ``{"calculable", "suma", "total", "diferencia", "cierra", "faltantes"}``;
    ``calculable`` es ``False`` si no hay ningún importe o no hay total.
    """
    componentes = {k: a_numero(extraccion.get(k)) for k in COMPONENTES_DEL_TOTAL}
    presentes = {k: v for k, v in componentes.items() if v is not None}
    total = a_numero(extraccion.get("importe_total"))
    if not presentes or total is None:
        return {
            "calculable": False,
            "suma": sum(presentes.values()) if presentes else None,
            "total": total,
            "diferencia": None,
            "cierra": None,
            "faltantes": [],
        }

    suma = sum(presentes.values())
    diferencia = round(total - suma, 2)
    cierra = abs(diferencia) < 0.05
    # Cuando no cierra, el candidato más probable es un campo de importe que
    # quedó en null: el modelo no lo transcribió. Se listan todos los ausentes
    # (sin adivinar cuál), para que quien revise mire ahí primero.
    faltantes = (
        [k for k in COMPONENTES_DEL_TOTAL if componentes.get(k) is None]
        if not cierra
        else []
    )
    return {
        "calculable": True,
        "suma": round(suma, 2),
        "total": total,
        "diferencia": diferencia,
        "cierra": cierra,
        "faltantes": faltantes,
    }

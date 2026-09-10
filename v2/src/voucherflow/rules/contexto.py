"""Contexto tipado del motor de reglas R1-R7 (F3 / T-301, épica E-CLAS-1).

**Fase**: F3 — Clasificación. Este módulo materializa el *contexto de entrada*
que anticipa `RULES.md` §1 ("las condiciones se evalúan contra un ``contexto``
tipado que las fases pobladoras construirán"): las **condiciones fiscales** de
emisor y receptor, los **países**, y la **evidencia de lectura** del documento
(letra del recuadro que vio el VLM, texto de encabezado para la regex del LLM,
desglose de IVA y campos totales).

Decisiones de diseño (T-301)
----------------------------
1. **Inmutable y de solo lectura** (``frozen=True``): las reglas R1-R7 son
   funciones puras del contexto (``Rule.condicion`` debe ser determinista y sin
   efectos colaterales, ver ``registry.py``). Ningún registro ni regla puede
   mutar el contexto; para derivar variantes se usa :meth:`reemplazar` (que
   devuelve una copia nueva, basada en ``dataclasses.replace``).
2. **Construible de forma incremental**: todos los campos tienen default
   (``field(default=...)``), de modo que T-302/T-303 puedan poblar solo lo que
   la lectura aportó y dejar el resto en ``None`` (el prompt WIP exige
   "no inventes datos": lo ausente es ``None``/desconocido, nunca un valor
   forzado).
3. **Normalización defensiva** (``__post_init__``): se canoniza el vocabulario
   del dominio (condición fiscal, letra, campos totales) para que las reglas
   comparen contra constantes y no contra el texto crudo del modelo. Un valor
   no reconocido **se conserva tal cual** (no se inventa un valor conocido) y,
   por lo tanto, no dispara ninguna regla.

Referencias: doc 03 §7 (motor de reglas), `RULES.md` §1/§3, `CLAS.md` §2,
F3-subplan §3.1 y §5, ética de evidencia ADR-001 (lo ausente no se asume).
"""

from __future__ import annotations

from dataclasses import dataclass, field, replace
from typing import Any, Iterable, Mapping

# ---------------------------------------------------------------------------
# Vocabulario del dominio (portado literal del prompt WIP
# ``prompts/wip/deteccion_tipo_factura.yaml`` §inputs_esperados)
# ---------------------------------------------------------------------------

#: Condición fiscal del emisor/receptor (enum del prompt WIP).
CONDICION_RI = "Responsable Inscripto"
CONDICION_MONOTRIBUTO = "Monotributo"
CONDICION_EXENTO = "Exento"
CONDICION_CONSUMIDOR_FINAL = "Consumidor Final"
CONDICION_DESCONOCIDA = "desconocido"

#: País del receptor que NO dispara R3 (exportación).
PAIS_ARGENTINA = "Argentina"

#: Valores de ``campos_totales`` (enum del prompt WIP §inputs_esperados).
CAMPOS_TOTALES_DISCRIMINADO = "discriminado"
CAMPOS_TOTALES_SUBTOTAL_UNICO = "subtotal_unico"
CAMPOS_TOTALES_DESCONOCIDO = "desconocido"

#: Letras válidas para la lectura de tipo/letra (enum ``TipoComprobante`` de
#: ``schemas/evidence.py``, sin los códigos de tique 090/099 — ver F3-subplan
#: §2.8 / decisión abierta D-13: el motor no inventa el mapeo letra↔código).
LETRAS_COMPROBANTE = frozenset({"A", "B", "C", "M", "E"})

#: Condiciones fiscales reconocidas (normalizadas).
CONDICIONES_FISCALES_CONOCIDAS = frozenset(
    {
        CONDICION_RI,
        CONDICION_MONOTRIBUTO,
        CONDICION_EXENTO,
        CONDICION_CONSUMIDOR_FINAL,
    }
)

# Mapa case-insensitive -> valor canónico (tolerante a mayúsculas/minúsculas y
# a espacios, típico de la salida de un modelo).
_CONDICIONES_CANONICAS = {c.lower(): c for c in CONDICIONES_FISCALES_CONOCIDAS}
_CAMPOS_TOTALES_CANONICOS = {
    CAMPOS_TOTALES_DISCRIMINADO: CAMPOS_TOTALES_DISCRIMINADO,
    CAMPOS_TOTALES_SUBTOTAL_UNICO: CAMPOS_TOTALES_SUBTOTAL_UNICO,
    CAMPOS_TOTALES_DESCONOCIDO: CAMPOS_TOTALES_DESCONOCIDO,
}


def _limpiar(valor: Any) -> str | None:
    """Devuelve el texto sin espacios o ``None`` si no hay valor útil."""
    if valor is None:
        return None
    texto = str(valor).strip()
    return texto or None


def normalizar_condicion_fiscal(valor: Any) -> str | None:
    """Canoniza la condición fiscal; un valor no reconocido se conserva crudo.

    ``"responsable inscripto"`` → ``"Responsable Inscripto"``; ``""`` → ``None``;
    un valor no reconocido (p. ej. ``"Agente de retención"``) se devuelve tal
    cual para que **no** dispare R1/R2 (no se inventa una condición conocida).
    """
    texto = _limpiar(valor)
    if texto is None:
        return None
    return _CONDICIONES_CANONICAS.get(texto.lower(), texto)


def normalizar_letra(valor: Any) -> str | None:
    """Devuelve la letra en mayúscula si está en ``{A,B,C,M,E}``; si no, ``None``.

    Contrato explícito de **T-301** (Gherkin E-CLAS-1): el vocabulario de letras
    es el enum ``TipoComprobante`` de ``schemas/evidence.py`` sin los códigos de
    tique ``090``/``099`` (decisión abierta D-13; el prompt WIP no define reglas
    para ellos). Es el **normalizador reutilizable por R4 y R5** y el asignador
    de la letra final del orquestador.

    Un valor fuera del vocabulario (``"Z"``, ``"090"``, ``None``, ``""``)
    devuelve ``None``: **no se inventa** una letra válida.
    """
    texto = _limpiar(valor)
    if texto is None:
        return None
    letra = texto.upper()
    return letra if letra in LETRAS_COMPROBANTE else None


def letra_en_vocabulario(valor: Any) -> str | None:
    """Alias explícito de :func:`normalizar_letra` (T-301).

    Se conserva el nombre para las condiciones de las reglas de lectura ("¿hay
    una letra del vocabulario?"), pero la semántica es exactamente la misma.
    """
    return normalizar_letra(valor)


def normalizar_campos_totales(valor: Any) -> str | None:
    """Canoniza ``campos_totales`` al vocabulario del WIP (o lo conserva crudo)."""
    texto = _limpiar(valor)
    if texto is None:
        return None
    return _CAMPOS_TOTALES_CANONICOS.get(texto.lower(), texto)


def es_condicion_fiscal_conocida(valor: Any) -> bool:
    """True si la condición fiscal es una de las cuatro reconocidas (no ``None``)."""
    return _limpiar(valor) in CONDICIONES_FISCALES_CONOCIDAS


# ---------------------------------------------------------------------------
# Contexto
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class ContextoTipoComprobante:
    """Contexto tipado de entrada del motor de reglas R1-R7 (F3 / T-301).

    Es **de solo lectura** (``frozen=True``): las reglas nunca lo mutan. Todos
    los campos son opcionales para permitir construcción incremental (T-302/
    T-303 poblarán la evidencia de lectura real). Un campo en ``None`` significa
    **dato ausente**: las reglas tratan lo ausente como "desconocido" y no lo
    asumen (prompt WIP: "No inventes datos").

    Condiciones fiscales (R1/R2A/R2B/R7):
        emisor_condicion_fiscal: Condición del emisor —
            ``Responsable Inscripto`` | ``Monotributo`` | ``Exento`` |
            ``Consumidor Final`` (o ``None`` si no se pudo determinar).
        receptor_condicion_fiscal: Condición del receptor (mismo vocabulario).
        emisor_pais: País del emisor (informativo; R3 mira al receptor).
            Default :data:`PAIS_ARGENTINA`.
        receptor_pais: País del receptor. Default :data:`PAIS_ARGENTINA`
            (contexto doméstico): R3 (exportación) solo dispara cuando el valor
            informado es distinto de Argentina; un país vacío/ausente cae en el
            default y **no** dispara (no se asume exportación sin evidencia).

    Identidad del contribuyente propio (refuerzo opcional de R7):
        cuit_propio: CUIT del contribuyente para el que se clasifica
            (``cuit_scania_ri`` en el prompt WIP). Es **refuerzo opcional**: el
            Gherkin de E-CLAS-1 define R7 solo con las condiciones fiscales; el
            CUIT se registra en la alerta para auditoría, sin volverlo
            obligatorio (decisión de despliegue, ver F3-subplan §2 y T-301).
        receptor_cuit: CUIT del receptor, para comparar contra ``cuit_propio``.

    Evidencia de lectura (R4/R5/R6):
        letra_recuadro_vlm: Letra única que el VLM vio en el recuadro del
            encabezado (R4). ``None`` si no se leyó.
        texto_encabezado_llm: Texto crudo cercano al punto de venta sobre el
            que se aplica la regex de R5 (p. ej. ``"FACTURA B COD. 006"``).
            Default ``""`` (sin texto).
        desglose_iva_discriminado: ``True``/``False``/``None`` según si Neto +
            IVA aparecen separados en el documento (señal de R6).
        campos_totales: Forma del bloque de totales — ``discriminado`` |
            ``subtotal_unico`` | ``desconocido`` (señal de R6).

    Trazabilidad:
        campos_ausentes: Campos que el productor de la evidencia **declaró**
            como no leídos/desconocidos (además de los que están en ``None``).
            Default lista vacía (``field(default_factory=list)``). Se agregan a
            :meth:`campos_desconocidos` para el reporte final.
    """

    # --- Condiciones fiscales (R1/R2A/R2B/R3/R7) ---
    emisor_condicion_fiscal: str | None = None
    receptor_condicion_fiscal: str | None = None
    emisor_pais: str = PAIS_ARGENTINA
    receptor_pais: str = PAIS_ARGENTINA

    # --- Identidad del contribuyente propio (refuerzo opcional de R7) ---
    cuit_propio: str | None = None
    receptor_cuit: str | None = None

    # --- Evidencia de lectura (R4/R5/R6) ---
    letra_recuadro_vlm: str | None = None
    texto_encabezado_llm: str = ""
    desglose_iva_discriminado: bool | None = None
    campos_totales: str | None = None

    # --- Trazabilidad ---
    campos_ausentes: list[str] = field(default_factory=list)

    def __post_init__(self) -> None:
        # ``frozen=True``: se usa object.__setattr__ para canonizar el
        # vocabulario una sola vez al construir el contexto. Un valor fuera del
        # vocabulario queda en ``None``/default: el contexto es la vista
        # **normalizada** que consume el motor; el crudo vive en la evidencia
        # por fuente (``EvidenceField``, T-302/T-303).
        object.__setattr__(
            self, "emisor_condicion_fiscal", normalizar_condicion_fiscal(self.emisor_condicion_fiscal)
        )
        object.__setattr__(
            self, "receptor_condicion_fiscal", normalizar_condicion_fiscal(self.receptor_condicion_fiscal)
        )
        object.__setattr__(self, "emisor_pais", _limpiar(self.emisor_pais) or PAIS_ARGENTINA)
        object.__setattr__(self, "receptor_pais", _limpiar(self.receptor_pais) or PAIS_ARGENTINA)
        object.__setattr__(self, "cuit_propio", _limpiar(self.cuit_propio))
        object.__setattr__(self, "receptor_cuit", _limpiar(self.receptor_cuit))
        object.__setattr__(self, "letra_recuadro_vlm", normalizar_letra(self.letra_recuadro_vlm))
        object.__setattr__(self, "texto_encabezado_llm", _limpiar(self.texto_encabezado_llm) or "")
        object.__setattr__(self, "campos_totales", normalizar_campos_totales(self.campos_totales))
        object.__setattr__(
            self,
            "campos_ausentes",
            [texto for texto in (_limpiar(campo) for campo in self.campos_ausentes) if texto],
        )

    # ------------------------------------------------------------------
    # Helpers de lectura (sin efectos colaterales)
    # ------------------------------------------------------------------

    @property
    def emisor_condicion_conocida(self) -> bool:
        """True si la condición fiscal del emisor es una de las reconocidas."""
        return es_condicion_fiscal_conocida(self.emisor_condicion_fiscal)

    @property
    def receptor_condicion_conocida(self) -> bool:
        """True si la condición fiscal del receptor es una de las reconocidas."""
        return es_condicion_fiscal_conocida(self.receptor_condicion_fiscal)

    @property
    def tiene_evidencia_lectura(self) -> bool:
        """True si hay al menos una señal de lectura (VLM, texto o totales)."""
        return any(
            (
                self.letra_recuadro_vlm is not None,
                bool(self.texto_encabezado_llm),
                self.desglose_iva_discriminado is not None,
                self.campos_totales is not None,
            )
        )

    def campos_desconocidos(self) -> list[str]:
        """Campos que faltaron para poder decidir con certeza alta.

        Es el insumo de ``TipoComprobanteResult.campos_desconocidos`` (contrato
        del prompt WIP §``output_esperado``): condiciones fiscales no conocidas y
        ausencia total de evidencia de lectura. Además refleja lo que el
        productor declaró en :attr:`campos_ausentes`.

        El país del receptor tiene default :data:`PAIS_ARGENTINA`; solo se
        reporta como faltante si quedó vacío (caso defensivo).
        """
        faltantes: list[str] = []
        if not self.emisor_condicion_conocida:
            faltantes.append("emisor.condicion_fiscal")
        if not self.receptor_condicion_conocida:
            faltantes.append("receptor.condicion_fiscal")
        if not self.receptor_pais:
            faltantes.append("receptor.pais")
        if not self.tiene_evidencia_lectura:
            faltantes.append("evidencia_lectura")
        for campo in self.campos_ausentes:
            if campo and campo not in faltantes:
                faltantes.append(campo)
        return faltantes

    def reemplazar(self, **cambios: Any) -> "ContextoTipoComprobante":
        """Devuelve una copia con los campos indicados reemplazados.

        Mantiene la inmutabilidad del contexto original (las reglas reciben una
        instancia nueva; nunca se muta la existente). Útil para T-302/T-303 al
        completar la evidencia de lectura sin reconstruir todo el contexto.
        """
        return replace(self, **cambios)

    def como_dict(self) -> dict[str, Any]:
        """Serializa el contexto (para ``detalle``/auditoría y reportes)."""
        return {
            "emisor_condicion_fiscal": self.emisor_condicion_fiscal,
            "receptor_condicion_fiscal": self.receptor_condicion_fiscal,
            "emisor_pais": self.emisor_pais,
            "receptor_pais": self.receptor_pais,
            "cuit_propio": self.cuit_propio,
            "receptor_cuit": self.receptor_cuit,
            "letra_recuadro_vlm": self.letra_recuadro_vlm,
            "texto_encabezado_llm": self.texto_encabezado_llm,
            "desglose_iva_discriminado": self.desglose_iva_discriminado,
            "campos_totales": self.campos_totales,
            "campos_ausentes": list(self.campos_ausentes),
        }

    # Nota de diseño (T-301): ``campos_ausentes`` es una ``list`` (contrato del
    # enunciado, para construcción incremental por apéndice). El contexto se
    # declara ``frozen`` para que las reglas no reasignen campos; la lista se
    # reconstruye en ``__post_init__`` para no compartir referencia con quien la
    # pasó, y ninguna regla R1-R7 la muta (condiciones puras, ADR-006).

    # ------------------------------------------------------------------
    # Compatibilidad: construcción desde dict (flat o anidado estilo WIP)
    # ------------------------------------------------------------------

    @classmethod
    def desde_dict(cls, datos: Mapping[str, Any]) -> "ContextoTipoComprobante":
        """Construye el contexto desde un ``dict``.

        Acepta el shape plano (``{"emisor_condicion_fiscal": ...}``) y también
        el shape anidado del prompt WIP
        (``{"emisor": {"condicion_fiscal": ...}, "receptor": {...},
        "ocr": {...}}``), para facilitar la integración de T-302 (el lector
        devuelve evidencia por fuente).

        Lanza ``TypeError`` si ``datos`` no es un mapping.
        """
        if not isinstance(datos, Mapping):
            raise TypeError(
                "ContextoTipoComprobante.desde_dict() espera un mapping (dict); "
                f"recibido: {type(datos).__name__}."
            )

        def _sub(clave: str) -> Mapping[str, Any]:
            valor = datos.get(clave)
            return valor if isinstance(valor, Mapping) else {}

        emisor = _sub("emisor")
        receptor = _sub("receptor")
        ocr = _sub("ocr")

        def _primero(*valores: Any) -> Any:
            for valor in valores:
                if valor is not None:
                    return valor
            return None

        return cls(
            emisor_condicion_fiscal=_primero(
                datos.get("emisor_condicion_fiscal"), emisor.get("condicion_fiscal")
            ),
            receptor_condicion_fiscal=_primero(
                datos.get("receptor_condicion_fiscal"), receptor.get("condicion_fiscal")
            ),
            emisor_pais=_primero(datos.get("emisor_pais"), emisor.get("pais")),
            receptor_pais=_primero(datos.get("receptor_pais"), receptor.get("pais")),
            cuit_propio=_primero(datos.get("cuit_propio"), datos.get("cuit_scania_ri")),
            receptor_cuit=_primero(datos.get("receptor_cuit"), receptor.get("cuit")),
            letra_recuadro_vlm=_primero(
                datos.get("letra_recuadro_vlm"), ocr.get("letra_detectada_cabecera")
            ),
            texto_encabezado_llm=_primero(
                datos.get("texto_encabezado_llm"), ocr.get("texto_encabezado")
            ),
            desglose_iva_discriminado=_primero(
                datos.get("desglose_iva_discriminado"), ocr.get("desglose_iva_discriminado")
            ),
            campos_totales=_primero(datos.get("campos_totales"), ocr.get("campos_totales")),
            campos_ausentes=_lista_de_campos(datos.get("campos_ausentes")),
        )


def _lista_de_campos(valor: Any) -> list[str]:
    """Normaliza ``campos_ausentes`` a lista (acepta ``None``, str o iterable)."""
    if valor is None:
        return []
    if isinstance(valor, str):
        return [valor]
    if isinstance(valor, Iterable):
        return [str(campo) for campo in valor]
    return []


__all__ = [
    "CONDICION_RI",
    "CONDICION_MONOTRIBUTO",
    "CONDICION_EXENTO",
    "CONDICION_CONSUMIDOR_FINAL",
    "CONDICION_DESCONOCIDA",
    "CONDICIONES_FISCALES_CONOCIDAS",
    "PAIS_ARGENTINA",
    "CAMPOS_TOTALES_DISCRIMINADO",
    "CAMPOS_TOTALES_SUBTOTAL_UNICO",
    "CAMPOS_TOTALES_DESCONOCIDO",
    "LETRAS_COMPROBANTE",
    "ContextoTipoComprobante",
    "normalizar_condicion_fiscal",
    "normalizar_letra",
    "normalizar_campos_totales",
    "es_condicion_fiscal_conocida",
    "letra_en_vocabulario",
]

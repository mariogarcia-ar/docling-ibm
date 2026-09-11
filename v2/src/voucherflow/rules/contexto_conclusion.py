"""Contexto tipado de las reglas cruzadas de conclusión (F5 / T-501).

**Fase**: F5 — Conclusión. Este módulo materializa el *contexto de la pasada 2*:
lo que el motor de reglas cruzadas necesita para concluir el caso **sobre la
evidencia combinada** (no sobre una fuente, como la pasada 1 de T-403).

Diferencia con :mod:`voucherflow.rules.contexto` (F3/T-301)
-----------------------------------------------------------
``ContextoTipoComprobante`` describe la lectura de **una** fuente (o el cruce de
negocio vs. documento dentro de la clasificación) y alimenta R1-R7. El motor de
conclusión, en cambio, trabaja sobre lo que **sobrevivió** a la combinación de
F4: el **valor vigente** de cada campo y **qué fuente** lo sostiene
(``CampoCombinado.valor``/``fuente``, T-404). Este dataclass es esa vista.

Decisiones de diseño (T-501)
----------------------------
1. **Inmutable** (``frozen=True``), como el contexto de F3: las reglas cruzadas
   son funciones puras del contexto (``Rule.condicion`` es determinista y sin
   efectos colaterales, ver ``registry.py``).
2. **Todo campo tiene default**: el contexto se construye **incrementalmente**.
   La conclusión puede correr con evidencia parcial (una fuente caída, un campo
   ilegible) y las reglas tratan lo ausente como *desconocido*, nunca como un
   valor asumido (ADR-001: lo ausente no se inventa).
3. **El contexto no decide**: expone hechos (los valores vigentes, los
   candidatos, las incoherencias) y deja el veredicto a las reglas de
   ``cruzadas.py``. Un contexto sin letra vigente **no** implica "rechazado":
   implica que la regla de fast-fail no dispara.
4. **La coherencia de la letra se evalúa sobre el caso, no sobre la fuente**:
   ``COHERENCIA_POR_CAMPO`` de :mod:`voucherflow.extraction.evidencia` (F4/T-403,
   E-EXT-2) ya declara, en un solo lugar, qué campos exige o rechaza cada letra.
   Hasta T-403 eso debilitaba *una fuente*; acá la misma tabla se evalúa contra
   el **valor vigente** del caso y su resultado alimenta el fast-fail.
5. **Los candidatos son un conjunto cerrado** (ADR-008): ``candidatos_descartados``
   y ``candidatos_restantes`` describen lo que el código dejó en pie. El agente
   (T-504) y el HITL (T-505) solo pueden elegir entre los restantes; un valor no
   puede estar en las dos listas.

Referencias: doc 03 §4.5 (módulo ``conclusion``), `CONC.md` §1/§3, Gherkin
E-CONC-1, `algoritmo.md` (paso 5: "reglas de programación sobre evidencia
combinada — negocio + fast-fail + conflicto R7"), F5-subplan §2 y §3.1.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Mapping

from ..schemas.evidence import CombinedEvidence, Fuente
from .contexto import ContextoTipoComprobante

# ---------------------------------------------------------------------------
# Vocabulario de la conclusión
# ---------------------------------------------------------------------------

#: IDs de las reglas cruzadas de F5/T-501 (la pasada 2). Se registran en la
#: trazabilidad del caso (E-CONC-5) y viajan en ``Decision.reglas_aplicadas``.
CRUZ_1_NEGOCIO = "CRUZ_1"
CRUZ_2_LETRA_SIN_SOSTEN = "CRUZ_2"
CRUZ_3_COHERENCIA_LETRA = "CRUZ_3"
CRUZ_4_DATOS_FALTANTES = "CRUZ_4"
CRUZ_5_CONFLICTO_CREDITO = "CRUZ_5"

#: Familias de las reglas cruzadas (para el reporte; mismo criterio que las
#: familias de F3: ``negocio``/``lectura``/``conflicto``).
FAMILIA_NEGOCIO = "negocio"
FAMILIA_FAST_FAIL = "fast_fail"
FAMILIA_CONFLICTO = "conflicto"

#: Tolerancia con la que se compara un importe contra cero (los montos vienen
#: de la normalización de T-402 como ``float``, pero un valor crudo no
#: normalizable puede llegar como texto).
EPSILON_MONTO = 1e-9

#: Nombre canónico del impuesto que señala si la letra discrimina IVA.
CAMPO_IVA = "iva"

#: Campos del comprobante cuya ausencia impide concluir con certeza alta. Es el
#: subconjunto **mínimo** con el que el código puede afirmar algo del caso; el
#: resto de los importes son opcionales (un comprobante puede no tener
#: percepciones ni impuestos internos).
CAMPOS_CRITICOS: tuple[str, ...] = (
    "tipo_comprobante",
    "nro_comprobante",
    "fecha_emision",
    "importe_total_facturado",
)


def _texto(valor: Any) -> str | None:
    """Devuelve el texto sin espacios o ``None`` si no hay valor útil."""
    if valor is None:
        return None
    texto = str(valor).strip()
    return texto or None


def _monto(valor: Any) -> float | None:
    """Interpreta un valor como importe, o ``None`` si no es numérico.

    Acepta ``int``/``float`` (lo que produce la normalización de T-402, siempre
    en formato canónico ``12345.67``) y el texto numérico en las dos
    convenciones que aparecen en la práctica: ``"12345.67"`` (canónica) y
    ``"1.234,56"`` / ``"0,00"`` (separador de miles con punto y decimal con
    coma, la forma en que el documento lo imprime).

    Un valor que no se puede interpretar **no** se asume cero: devuelve ``None``
    y el consumidor decide qué hacer con la ausencia (ADR-001: no inventar
    montos).
    """
    if valor is None or isinstance(valor, bool):
        return None
    if isinstance(valor, (int, float)):
        return float(valor)
    texto = _texto(valor)
    if texto is None:
        return None

    # Símbolos que no son parte del número (moneda, porcentaje, espacios).
    limpio = texto.replace("$", "").replace(" ", "").replace("%", "")
    # Signo contable: ``(1.234,56)`` es negativo.
    negativo = limpio.startswith("(") and limpio.endswith(")")
    if negativo:
        limpio = limpio[1:-1]

    if "," in limpio and "." in limpio:
        # El último separador que aparece es el decimal (1.234,56 / 1,234.56).
        if limpio.rfind(",") > limpio.rfind("."):
            limpio = limpio.replace(".", "").replace(",", ".")
        else:
            limpio = limpio.replace(",", "")
    elif "," in limpio:
        limpio = limpio.replace(",", ".")

    try:
        numero = float(limpio)
    except ValueError:
        return None
    return -numero if negativo else numero


def es_monto_cero(valor: Any) -> bool:
    """True si el valor es un importe **igual a cero** (dentro de la tolerancia).

    Un valor no interpretable como importe devuelve ``False``: la ausencia de un
    monto no es "cero discriminado" (no se inventa el dato).
    """
    numero = _monto(valor)
    return numero is not None and abs(numero) <= EPSILON_MONTO


@dataclass(frozen=True)
class ContextoConclusion:
    """Contexto tipado de la pasada 2: el caso combinado, listo para concluir.

    Se construye normalmente con :meth:`desde_evidencia`, que proyecta la
    ``CombinedEvidence`` de F4 sobre la superficie que las reglas cruzadas
    consumen. Los campos tienen default para que los tests (y T-502) puedan
    construir el contexto a mano y evaluar una regla a la vez.

    Valores vigentes:
        valores: ``campo -> valor`` de la resolución por campo (T-404). Solo
            contiene los campos que **alguien** declaró: un campo ausente no
            aparece (no se rellena con ``None``).
        fuentes_por_campo: ``campo -> Fuente`` responsable del valor vigente.
        letra: letra vigente del comprobante (``A``/``B``/``C``/``M``/``E``) o
            ``None`` si ninguna fuente la resolvió.
        fuente_letra: fuente responsable de :attr:`letra`.

    Insumos del cruce:
        contexto_tipo: el contexto de F3 (R1-R7) armado con los valores
            vigentes, para re-aplicar el motor de negocio sin reimplementarlo.
        campos_criticos_ausentes: campos de :data:`CAMPOS_CRITICOS` que ninguna
            fuente declaró — la señal del gap (insumo de ``CRUZ_4`` y de T-502).
        campos_ausentes: todos los campos del contrato que quedaron sin declarar
            (``CombinedEvidence`` los registra en ``resolucion`` sin ganador).
        lectura_invalida: la letra vigente provino de una lectura que la pasada 1
            (T-403) había marcado como **no confiable** (``resolucion.confiable``
            en falso): el valor existe pero no se puede tratar como firme.

    Candidatos (ADR-008, conjunto cerrado):
        candidatos_descartados: valores que el código ya eliminó.
        candidatos_restantes: valores que siguen posibles. Un mismo valor no
            puede estar en las dos listas.

    Coherencia de la letra:
        coherente: ``True`` si no se detectó ninguna incoherencia **dura** entre
            la letra vigente y los campos del caso.
        incoherencias: motivos legibles de las incoherencias encontradas.
            Insumo directo de ``CRUZ_3`` (fast-fail).
    """

    # --- Valores vigentes (resolución por campo de T-404) ---
    valores: Mapping[str, Any] = field(default_factory=dict)
    fuentes_por_campo: Mapping[str, Fuente] = field(default_factory=dict)
    letra: str | None = None
    fuente_letra: Fuente | None = None

    # --- Insumos del cruce ---
    contexto_tipo: ContextoTipoComprobante = field(default_factory=ContextoTipoComprobante)
    campos_criticos_ausentes: list[str] = field(default_factory=list)
    campos_ausentes: list[str] = field(default_factory=list)
    lectura_invalida: bool = False

    # --- Candidatos (ADR-008: conjunto cerrado) ---
    candidatos_descartados: list[str] = field(default_factory=list)
    candidatos_restantes: list[str] = field(default_factory=list)

    # --- Coherencia de la letra ---
    coherente: bool = True
    incoherencias: list[str] = field(default_factory=list)

    # ------------------------------------------------------------------
    # Constructores
    # ------------------------------------------------------------------

    @classmethod
    def desde_evidencia(
        cls,
        evidencia: CombinedEvidence,
        *,
        contexto_tipo: ContextoTipoComprobante | None = None,
    ) -> "ContextoConclusion":
        """Proyecta la evidencia combinada de F4 sobre el contexto de F5 (T-501).

        Lee la **resolución por campo** (T-404) y arma:

        - los valores vigentes y su fuente responsable (solo los campos que
          alguna fuente declaró — un campo ausente se reporta, no se rellena);
        - el contexto de F3 con esos valores, para re-aplicar R1-R7;
        - los gaps (:data:`CAMPOS_CRITICOS` primero) y las lecturas no fiables;
        - la coherencia de la letra (``COHERENCIA_POR_CAMPO`` de T-403 evaluada
          sobre el caso).

        ``contexto_tipo`` permite pasar un contexto ya armado (p. ej. si el
        llamador ya corrió la clasificación de F3 con más insumos, como la
        condición fiscal del emisor del padrón): se usa como **base** y los
        valores vigentes lo completan.

        Lanza:
            ``TypeError`` si ``evidencia`` no es una ``CombinedEvidence``.
        """
        if not isinstance(evidencia, CombinedEvidence):
            raise TypeError(
                "ContextoConclusion.desde_evidencia() espera una CombinedEvidence "
                f"(la salida de la combinación de F4/T-404); recibido: "
                f"{type(evidencia).__name__}."
            )

        # Import diferido: ``extraction`` importa ``rules`` (precedencia) y acá
        # solo se necesitan la tabla de coherencia y el contrato de campos.
        from ..extraction.evidencia import COHERENCIA_POR_CAMPO
        from ..extraction.prompt_extraccion import CAMPOS_EXTRACCION

        valores: dict[str, Any] = {}
        fuentes: dict[str, Fuente] = {}
        ausentes: list[str] = []
        no_confiable = False

        for campo, combinado in sorted(evidencia.campos.items()):
            if combinado.fuente is None:
                # Nadie declaró el campo (la resolución existe para dejarlo por
                # escrito): se reporta como ausente, no se inventa un valor.
                ausentes.append(campo)
                continue
            valores[campo] = combinado.valor
            fuentes[campo] = combinado.fuente

        # El universo de campos es el **contrato** de extracción: un campo que
        # ninguna fuente mencionó no aparece siquiera en la evidencia combinada,
        # y sin embargo también está ausente (importa para el gap y para T-502).
        # Se suman los campos extra del modo genérico (`kvg`) que hayan quedado
        # sin ganador, para no perderlos de vista.
        ausentes = [
            campo for campo in CAMPOS_EXTRACCION if campo not in valores
        ] + [campo for campo in ausentes if campo not in CAMPOS_EXTRACCION]

        letra = _texto(valores.get("tipo_comprobante"))
        if letra is not None:
            letra = letra.upper()
            if not _es_letra_valida(letra):
                # Fuera del vocabulario del motor: no se trata como letra (no se
                # inventa un valor conocido), pero tampoco se descarta en
                # silencio — queda registrada como incoherencia del caso.
                letra = None

        # ¿La letra vigente vino de una resolución no confiable? La resolución
        # conserva ``confiable=False`` cuando ganó una lectura que la pasada 1
        # marcó como inválida (T-403): el valor existe pero no es firme.
        for campo in ("tipo_comprobante", *CAMPOS_CRITICOS):
            resolucion = evidencia.campos.get(campo)
            if resolucion is None:
                continue
            if _resolucion_no_confiable(evidencia, campo):
                no_confiable = True
                break

        criticos_ausentes = [
            campo for campo in CAMPOS_CRITICOS if campo not in valores
        ]

        # El contexto de F3 se completa con los valores vigentes, para que
        # R1-R7 se re-apliquen sobre la resolución por campo (no sobre una
        # fuente, que es lo que hace la pasada 1 de F3).
        #
        # Sobre el slot de la letra: la letra **vigente** entra por
        # ``letra_recuadro_vlm`` (R4) porque, para las reglas de negocio, lo que
        # importa es *la letra del documento* — y su procedencia real se
        # conserva en ``fuente_letra`` (auditoría). Cuando el ganador fue el
        # LLM, además se le pasa a R5 el **fragmento de sustento** que la
        # sostuvo (el mismo criterio que T-302: R5 aplica su regex sobre el
        # texto literal que la fuente citó, no sobre la letra ya resuelta).
        fragmento = _fragmento_de(evidencia, "tipo_comprobante")
        if fuentes.get("tipo_comprobante") != Fuente.vlm:
            fragmento = fragmento or ""

        base = contexto_tipo or ContextoTipoComprobante()
        contexto_armado = base.reemplazar(
            letra_recuadro_vlm=letra,
            texto_encabezado_llm=fragmento,
            desglose_iva_discriminado=_desglose_discriminado(valores),
            campos_totales=_campos_totales(valores),
            campos_ausentes=list(base.campos_ausentes),
        )

        coherente, incoherencias = _evaluar_coherencia(
            letra=letra, valores=valores, tabla=COHERENCIA_POR_CAMPO
        )

        return cls(
            valores=valores,
            fuentes_por_campo=fuentes,
            letra=letra,
            fuente_letra=fuentes.get("tipo_comprobante"),
            contexto_tipo=contexto_armado,
            campos_criticos_ausentes=criticos_ausentes,
            campos_ausentes=ausentes,
            lectura_invalida=no_confiable,
            coherente=coherente,
            incoherencias=incoherencias,
        )

    # ------------------------------------------------------------------
    # Consultas
    # ------------------------------------------------------------------

    def valor(self, campo: str, default: Any = None) -> Any:
        """Valor vigente de ``campo`` (o ``default`` si nadie lo declaró)."""
        return self.valores.get(campo, default)

    def tiene(self, campo: str) -> bool:
        """True si alguna fuente declaró ``campo`` (aunque el valor sea falsy)."""
        return campo in self.valores

    def importe(self, campo: str) -> float | None:
        """Importe vigente de ``campo`` como número, o ``None`` si no aplica."""
        return _monto(self.valores.get(campo))

    @property
    def iva_es_cero(self) -> bool | None:
        """``True``/``False`` si el IVA vigente es cero; ``None`` si no se sabe.

        Es la señal que distingue "la letra no discrimina IVA" de "el importe no
        se leyó": un IVA ausente o ilegible devuelve ``None``, no ``False``.
        """
        if not self.tiene(CAMPO_IVA):
            return None
        return es_monto_cero(self.valores.get(CAMPO_IVA))

    @property
    def hay_gap(self) -> bool:
        """True si falta algún campo crítico para poder concluir (insumo T-502)."""
        return bool(self.campos_criticos_ausentes)

    @property
    def candidatos(self) -> list[str]:
        """Todos los candidatos conocidos (descartados + restantes), sin repetir."""
        vistos: list[str] = []
        for valor in [*self.candidatos_descartados, *self.candidatos_restantes]:
            if valor not in vistos:
                vistos.append(valor)
        return vistos

    def con_candidatos(
        self, *, descartados: list[str], restantes: list[str]
    ) -> "ContextoConclusion":
        """Devuelve una copia con los candidatos ya curados (blindaje ADR-008).

        Aplica la misma regla que la clasificación de F3 (``_curados_de_candidatos_raw``):
        un valor que aparece en las dos listas **no puede** ser candidato, así
        que se quita de descartados (si el código lo dejó en pie, el descarte no
        se sostiene). Un valor no puede resucitar por una vía lateral.
        """
        restantes_unicos = _unicos(restantes)
        descartados_unicos = [
            valor for valor in _unicos(descartados) if valor not in restantes_unicos
        ]
        return self.reemplazar(
            candidatos_descartados=descartados_unicos,
            candidatos_restantes=restantes_unicos,
        )

    def reemplazar(self, **cambios: Any) -> "ContextoConclusion":
        """Copia del contexto con los campos reemplazados (no muta el original)."""
        from dataclasses import replace

        return replace(self, **cambios)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _unicos(valores: Any) -> list[str]:
    """Lista sin duplicados ni valores vacíos, preservando el orden."""
    salida: list[str] = []
    for valor in valores or []:
        texto = _texto(valor)
        if texto is not None and texto not in salida:
            salida.append(texto)
    return salida


def _es_letra_valida(letra: str) -> bool:
    """True si la letra pertenece al vocabulario del motor (``A``/``B``/``C``/``M``/``E``)."""
    from .contexto import LETRAS_COMPROBANTE

    return letra in LETRAS_COMPROBANTE


def _resolucion_no_confiable(evidencia: CombinedEvidence, campo: str) -> bool:
    """True si la resolución del campo en la trazabilidad quedó no confiable.

    La ``CombinedEvidence`` de F4 guarda, en ``trazabilidad['combinacion']``, el
    resumen de la combinación con la lista ``no_confiables``: los campos donde
    ganó una lectura pero **ninguna fuente utilizable** los declaró (T-403/T-404).
    Un campo así no puede sostener una conclusión con certeza alta.
    """
    combinacion = evidencia.trazabilidad.get("combinacion")
    if not isinstance(combinacion, Mapping):
        return False
    return campo in (combinacion.get("no_confiables") or [])


def _fragmento_de(evidencia: CombinedEvidence, campo: str) -> str:
    """``fragmento_sustento`` del valor **vigente** de ``campo`` (ADR-001).

    Se lee de la fuente que ganó la resolución (``CampoCombinado.fuente``), no
    de una fuente cualquiera: el fragmento tiene que ser el que sostiene el
    valor que efectivamente quedó vigente.
    """
    combinado = evidencia.campos.get(campo)
    if combinado is None or combinado.fuente is None:
        return ""
    lectura = getattr(combinado, combinado.fuente.value, None)
    if lectura is None:
        return ""
    return _texto(getattr(lectura, "fragmento_sustento", None)) or ""


def _desglose_discriminado(valores: Mapping[str, Any]) -> bool | None:
    """Señal de R6: ¿el documento discrimina IVA? (``True``/``False``/``None``).

    El desglose se **observa**, no se deduce del cero: que el IVA esté declarado
    y además haya un neto distinto del total es lo que muestra "Neto + IVA
    separados". Un IVA en cero no es "discriminado" ni su ausencia "no
    discriminado": si no se puede observar, se devuelve ``None`` y R6 no dispara
    (ADR-001: lo que no se ve no se asume).
    """
    if not valores.get(CAMPO_IVA) and CAMPO_IVA not in valores:
        return None
    iva = valores.get(CAMPO_IVA)
    if es_monto_cero(iva):
        return False
    subtotal = valores.get("subtotal")
    total = valores.get("importe_total_facturado")
    if iva is None or subtotal is None or total is None:
        return None
    monto_iva = _monto(iva)
    monto_subtotal = _monto(subtotal)
    monto_total = _monto(total)
    if monto_iva is None or monto_subtotal is None or monto_total is None:
        return None
    # "Discriminado" = el bloque de totales separa neto e IVA y estos explican
    # el total (con tolerancia de centavos).
    return abs((monto_subtotal + monto_iva) - monto_total) <= 0.05


def _campos_totales(valores: Mapping[str, Any]) -> str | None:
    """Señal de R6: forma del bloque de totales (``discriminado``/``subtotal_unico``).

    ``discriminado`` cuando IVA y subtotal están separados del total;
    ``subtotal_unico`` cuando hay un IVA explícito en cero y un total (el caso
    "B/C: importe único"); ``desconocido``/``None`` cuando no alcanza la
    evidencia para afirmar la forma — y entonces R6 **no** infiere letra.
    """
    from .contexto import CAMPOS_TOTALES_DESCONOCIDO, CAMPOS_TOTALES_DISCRIMINADO, CAMPOS_TOTALES_SUBTOTAL_UNICO

    if not valores.get(CAMPO_IVA) and CAMPO_IVA not in valores:
        return CAMPOS_TOTALES_DESCONOCIDO
    if _desglose_discriminado(valores):
        return CAMPOS_TOTALES_DISCRIMINADO
    if es_monto_cero(valores.get(CAMPO_IVA)) and "importe_total_facturado" in valores:
        return CAMPOS_TOTALES_SUBTOTAL_UNICO
    return CAMPOS_TOTALES_DESCONOCIDO


def _evaluar_coherencia(
    *,
    letra: str | None,
    valores: Mapping[str, Any],
    tabla: Mapping[str, Any],
) -> tuple[bool, list[str]]:
    """Evalúa las implicaciones de la letra sobre el **caso combinado** (T-501).

    Reutiliza la tabla ``COHERENCIA_POR_CAMPO`` de T-403 (E-EXT-2) — que declara,
    por **campo disparador** (``"tipo_comprobante"``), las implicaciones de cada
    valor (``implicacion.disparador``: la letra ``"A"``) con los campos que
    ``requiere`` y los ``incompatibles`` — pero la aplica al **valor vigente** del
    caso en lugar de a los campos de una fuente.

    Devuelve ``(coherente, motivos)``. Sin letra vigente no hay nada que evaluar
    (``(True, [])``): la ausencia de letra la reporta ``CRUZ_2``/``CRUZ_4``, no
    esta función.
    """
    if letra is None:
        return True, []

    motivos: list[str] = []
    # La tabla está indexada por el **campo que dispara** (``tipo_comprobante``),
    # no por la letra: cada entrada declara qué valor la activa.
    for implicacion in tabla.get("tipo_comprobante", ()):
        if _texto(getattr(implicacion, "disparador", None)) != letra:
            continue

        faltan = [
            campo
            for campo in getattr(implicacion, "requeridos", ())
            if campo not in valores
        ]
        if faltan:
            motivos.append(
                f"{implicacion.motivo} Falta(n): {', '.join(sorted(faltan))}."
            )

        for campo, admitidos in (getattr(implicacion, "incompatibles", None) or {}).items():
            if campo not in valores:
                continue
            valor = valores[campo]
            if not _valor_en(valor, admitidos):
                motivos.append(
                    f"{implicacion.motivo} El campo «{campo}» trae «{valor}», que "
                    f"contradice la letra {letra}."
                )

    return (not motivos), motivos


def _valor_en(valor: Any, admitidos: Any) -> bool:
    """True si ``valor`` está entre los ``admitidos`` (comparación tolerante).

    Compara números como números y texto como texto normalizado: ``0``, ``0.0``,
    ``"0"``, ``"0,00"`` y ``"0.00"`` son el mismo cero para la coherencia (es la
    tolerancia que ya usa T-403 para el IVA no discriminado).
    """
    if isinstance(admitidos, (set, frozenset, list, tuple)):
        candidatos = list(admitidos)
    else:
        candidatos = [admitidos]

    numero = _monto(valor)
    for candidato in candidatos:
        if numero is not None:
            otro = _monto(candidato)
            if otro is not None and abs(numero - otro) <= EPSILON_MONTO:
                return True
        if _texto(valor) is not None and _texto(valor) == _texto(candidato):
            return True
    return False


__all__ = [
    "CRUZ_1_NEGOCIO",
    "CRUZ_2_LETRA_SIN_SOSTEN",
    "CRUZ_3_COHERENCIA_LETRA",
    "CRUZ_4_DATOS_FALTANTES",
    "CRUZ_5_CONFLICTO_CREDITO",
    "FAMILIA_NEGOCIO",
    "FAMILIA_FAST_FAIL",
    "FAMILIA_CONFLICTO",
    "CAMPOS_CRITICOS",
    "CAMPO_IVA",
    "EPSILON_MONTO",
    "ContextoConclusion",
    "es_monto_cero",
]

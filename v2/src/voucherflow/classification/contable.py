"""Cadena contable 01 → 02 → 03 con contratos entre pasos (F3 / T-304, E-CLAS-2).

**Fase**: F3 (clasificación) · **Tarea**: T-304 · **Épica**: E-CLAS-2.

Refactoriza ``v1/classification_pipeline.py`` a la librería conservando su
**semántica** (paso 01 → paso 02 → paso 03, cada uno alimentado por el **primer**
resultado del anterior) pero con lo que v1 no tenía: **contratos tipados entre
pasos**, checkpoints explícitos y una variante **pura** (sin red) testeable.

Las dos capas (decisión de alcance F3-subplan §2.7)
---------------------------------------------------
1. **Pipeline real** — :func:`ejecutar_paso_01`, :func:`ejecutar_paso_02`,
   :func:`ejecutar_paso_03` y :func:`ejecutar_cadena`: llaman al modelo
   (``OllamaClient`` de F0) con los prompts versionados de
   ``prompts_contable.py`` y escriben checkpoints.
2. **Variante pura** — :class:`ResultadoCadenaContable` (y
   :func:`~voucherflow.classification.tipo_comprobante.clasificar_contable`):
   recibe los tres pasos **ya resueltos** y devuelve el resultado sin tocar la
   red. Es lo que corre la suite default y lo que consumirá F4 cuando los campos
   extraídos estén disponibles.

Contrato entre pasos
--------------------
Cada paso es una dataclass inmutable (:class:`OpcionCentroCosto`,
:class:`OpcionMacroCategoria`, :class:`PasoConceptoCodigo`) y la cadena
**nunca inventa** la entrada del paso siguiente: si un paso no devuelve
opciones, se lanza :class:`ErrorCadenaContable` con los **resultados parciales**
(``pasos``) preservados, igual que ``ClassificationError`` de v1. La cadena
corta es un resultado legítimo (no un ``None`` silencioso) porque el paso 02
necesita el centro de costo del 01 y el 03 necesita la macro del 02.

Checkpoints (paridad con v1)
----------------------------
v1 guardaba ``<doc>_classification.json`` **después de cada paso**, con la clave
``pasos``, para poder reanudar sin volver a llamar al modelo. Se conserva el
nombre y el shape (``{"archivo": ..., "pasos": {...}}``): T-305 compara contra
esos sidecar y la paridad de nombres es parte del contrato.

Valores base (provisional documentado)
--------------------------------------
``proveedor`` y ``monto`` se pasan como :data:`VALOR_NO_INFORMADO` porque F4
todavía no extrae los campos (F3-subplan §2.7). Cuando F4 los entregue, la
cadena los consume por ``base_values`` **sin cambiar su contrato**. ``descripcion``
es el markdown ya procesado de F1 (portado literal del ``classify_document()`` de
v1, que volcaba ``load_document_text()``).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable, Mapping

from ..models.ollama import OllamaClient
from ..schemas.evidence import nueva_meta
from ..settings.config import Settings, cargar_settings
from .prompts_contable import (
    CONDICION_IMPOSITIVA_DEFAULT,
    PASOS_CONTABLES,
    VALOR_NO_INFORMADO,
    construir_messages_contable,
    renderizar_user,
)

# ---------------------------------------------------------------------------
# Errores de dominio
# ---------------------------------------------------------------------------


class ErrorCadenaContable(RuntimeError):
    """Un paso de la cadena contable falló (T-304).

    Portado de ``ClassificationError`` de v1 (``v1/classification_pipeline.py``):
    lleva los **resultados parciales** en :attr:`pasos` para poder evaluar hasta
    dónde llegó la cadena sin perder el trabajo ya hecho (y sin tener que volver
    a llamar al modelo de los pasos resueltos: el checkpoint los conserva).
    """

    def __init__(self, mensaje: str, pasos: Mapping[str, Any] | None = None) -> None:
        super().__init__(mensaje)
        self.pasos: dict[str, Any] = dict(pasos or {})


class RespuestaContableInvalida(ErrorCadenaContable):
    """El modelo devolvió un JSON que no cumple el contrato del paso (T-304).

    Se lanza cuando la respuesta no es JSON, no es un objeto, o no trae las
    claves que el paso exige (``centros_costos`` / ``macro_categorias`` /
    ``concepto``). Es un ``ErrorCadenaContable`` para que el llamador tenga una
    sola excepción que capturar con ``error.pasos``.
    """


# ---------------------------------------------------------------------------
# Contratos por paso (dataclasses inmutables entre pasos)
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class OpcionCentroCosto:
    """Una opción de centro de costo devuelta por el paso 01 (T-304).

    Campos portados del contrato del prompt 01: ``codigo_centro_costo``,
    ``centro``, ``confianza``, ``senal_usada`` y ``justificacion``. Se conservan
    los cinco tal cual llegan del modelo para que la auditoría vea lo que el
    modelo dijo (no solo el código ganador).
    """

    codigo: str
    centro: str = ""
    confianza: str = ""
    senal_usada: str = ""
    justificacion: str = ""


@dataclass(frozen=True)
class OpcionMacroCategoria:
    """Una opción de macro categoría devuelta por el paso 02 (T-304)."""

    macro: str
    nombre: str = ""
    confianza: str = ""
    criterio_inferido: bool = False
    justificacion: str = ""


@dataclass(frozen=True)
class PasoConceptoCodigo:
    """Resultado del paso 03: concepto, cuenta y código final (T-304).

    Es el único paso que devuelve **un** resultado (no una lista de opciones):
    el prompt 03 pide ``concepto`` + ``codigo_final`` + ``candidatos_codigo_final``,
    y el ``requiere_revision_humana`` del propio prompt se expone como
    :attr:`requiere_revision_humana`.
    """

    macro_categoria: str = ""
    concepto: str = ""
    nombre_concepto: str = ""
    cuenta_contable: str = ""
    condicion_impositiva: str = ""
    codigo_final: str | None = None
    candidatos_codigo_final: list[str] = field(default_factory=list)
    requiere_revision_humana: bool = False
    confianza: str = ""
    criterio_inferido: bool = False
    justificacion: str = ""


# ---------------------------------------------------------------------------
# Accesores "primary_*" (portados de v1)
# ---------------------------------------------------------------------------


def primary_centro_costo(paso_01: Mapping[str, Any] | list[Any]) -> str:
    """Código del **primer** centro de costo del paso 01 (portado de v1).

    v1: ``options[0]["codigo_centro_costo"]``. Se conserva la semántica ("el
    primero es el principal") y se agrega el mensaje explícito cuando la lista
    viene vacía o el primer ítem no trae el código.

    Lanza:
        :class:`RespuestaContableInvalida` si no hay opciones o falta el código.
    """
    opciones = _opciones_de(paso_01, "centros_costos")
    if not opciones:
        raise RespuestaContableInvalida(
            "El paso 01 no devolvió centros_costos (la cadena no inventa el "
            "centro de costo del paso 02; T-304)."
        )
    primera = opciones[0]
    codigo = primera.get("codigo_centro_costo") if isinstance(primera, Mapping) else None
    if not codigo:
        raise RespuestaContableInvalida(
            "La primera opción del paso 01 no trae 'codigo_centro_costo' "
            f"(recibido: {primera!r}) — T-304."
        )
    return str(codigo).strip()


def primary_macro_categoria(paso_02: Mapping[str, Any] | list[Any]) -> str:
    """Macro categoría **principal** (la primera) del paso 02 (portado de v1).

    Lanza:
        :class:`RespuestaContableInvalida` si no hay opciones o falta la macro.
    """
    opciones = _opciones_de(paso_02, "macro_categorias")
    if not opciones:
        raise RespuestaContableInvalida(
            "El paso 02 no devolvió macro_categorias (la cadena no inventa la "
            "macro del paso 03; T-304)."
        )
    primera = opciones[0]
    macro = primera.get("macro_categoria") if isinstance(primera, Mapping) else None
    if not macro:
        raise RespuestaContableInvalida(
            "La primera opción del paso 02 no trae 'macro_categoria' "
            f"(recibido: {primera!r}) — T-304."
        )
    return str(macro).strip()


def _opciones_de(paso: Mapping[str, Any] | list[Any], clave: str) -> list[Mapping[str, Any]]:
    """Extrae la lista de opciones de un paso (acepta el dict crudo o la lista).

    Acepta también una lista suelta (el llamador ya extrajo las opciones) para
    que la variante pura sea cómoda de usar desde los tests y desde F5.
    """
    if isinstance(paso, list):
        return [opcion for opcion in paso if isinstance(opcion, Mapping)]
    if isinstance(paso, Mapping):
        opciones = paso.get(clave)
        if isinstance(opciones, list):
            return [opcion for opcion in opciones if isinstance(opcion, Mapping)]
    return []


def opciones_centro_costo(paso_01: Mapping[str, Any] | list[Any]) -> list[OpcionCentroCosto]:
    """Convierte el JSON del paso 01 a las opciones tipadas (T-304)."""
    return [
        OpcionCentroCosto(
            codigo=str(opcion.get("codigo_centro_costo") or "").strip(),
            centro=str(opcion.get("centro") or "").strip(),
            confianza=str(opcion.get("confianza") or "").strip(),
            senal_usada=str(opcion.get("senal_usada") or "").strip(),
            justificacion=str(opcion.get("justificacion") or "").strip(),
        )
        for opcion in _opciones_de(paso_01, "centros_costos")
    ]


def opciones_macro_categoria(paso_02: Mapping[str, Any] | list[Any]) -> list[OpcionMacroCategoria]:
    """Convierte el JSON del paso 02 a las opciones tipadas (T-304)."""
    return [
        OpcionMacroCategoria(
            macro=str(opcion.get("macro_categoria") or "").strip(),
            nombre=str(opcion.get("nombre") or "").strip(),
            confianza=str(opcion.get("confianza") or "").strip(),
            criterio_inferido=bool(opcion.get("criterio_inferido", False)),
            justificacion=str(opcion.get("justificacion") or "").strip(),
        )
        for opcion in _opciones_de(paso_02, "macro_categorias")
    ]


def paso_concepto_codigo(paso_03: Mapping[str, Any]) -> PasoConceptoCodigo:
    """Convierte el JSON del paso 03 al contrato tipado (T-304).

    ``candidatos_codigo_final`` se normaliza a lista de strings (el prompt pide
    copiar **todos** los códigos de la celda cuando vienen separados por ``/``).
    """
    if not isinstance(paso_03, Mapping):
        raise RespuestaContableInvalida(
            f"El paso 03 debe ser un objeto JSON; recibido: {type(paso_03).__name__} (T-304)."
        )
    candidatos = paso_03.get("candidatos_codigo_final")
    if isinstance(candidatos, (list, tuple)):
        normalizados = [str(c).strip() for c in candidatos if str(c).strip()]
    elif candidatos in (None, ""):
        normalizados = []
    else:
        normalizados = [str(candidatos).strip()]

    codigo_final = paso_03.get("codigo_final")
    return PasoConceptoCodigo(
        macro_categoria=str(paso_03.get("macro_categoria") or "").strip(),
        concepto=str(paso_03.get("concepto") or "").strip(),
        nombre_concepto=str(paso_03.get("nombre_concepto") or "").strip(),
        cuenta_contable=str(paso_03.get("cuenta_contable") or "").strip(),
        condicion_impositiva=str(paso_03.get("condicion_impositiva") or "").strip(),
        codigo_final=str(codigo_final).strip() if codigo_final not in (None, "") else None,
        candidatos_codigo_final=normalizados,
        requiere_revision_humana=bool(paso_03.get("requiere_revision_humana", False)),
        confianza=str(paso_03.get("confianza") or "").strip(),
        criterio_inferido=bool(paso_03.get("criterio_inferido", False)),
        justificacion=str(paso_03.get("justificacion") or "").strip(),
    )


# ---------------------------------------------------------------------------
# Resultado de la cadena (variante pura)
# ---------------------------------------------------------------------------


@dataclass
class ResultadoCadenaContable:
    """Resultado de la cadena 01→02→03 sobre pasos **ya resueltos** (T-304).

    Es la variante pura (sin red) de :func:`ejecutar_cadena`: recibe los JSON de
    los tres pasos y deriva el resultado con los **mismos** accesores
    ``primary_*`` que usa el pipeline real, de modo que ambos caminos no puedan
    divergir (y sea testeable en la suite default).

    Campos:
        centro_costo: código del centro principal (1º del paso 01).
        macro_categoria: macro principal (1ª del paso 02).
        concepto: concepto del paso 03 (``CTNNN``).
        codigo: código final del paso 03 (puede ser ``None`` cuando la celda de
            la tabla es "—": no existe código para esa combinación).
        condicion_impositiva: condición usada en el paso 03.
        opciones_centro_costo / opciones_macro_categoria: las opciones tipadas,
            para trazabilidad (el prompt devuelve hasta tres).
        paso_03: contrato tipado del paso 03 (cuenta, nombre, candidatos,
            ``requiere_revision_humana``).
        reglas_aplicadas: ids de trazabilidad de la cadena
            (``CC-01``/``CC-02``/``CC-03``) — no son reglas de decisión: el
            paso lo decide el modelo, esto solo registra qué etapas corrieron
            (E-CONC-5).
        pasos: los JSON crudos de los tres pasos (paridad con el sidecar de v1).
        detalle: traza legible (criterio + versiones de prompt).
        requiere_revision_humana: ``True`` si el paso 03 lo pidió (celda "—" o
            código con alternativas).
    """

    centro_costo: str | None = None
    macro_categoria: str | None = None
    concepto: str | None = None
    codigo: str | None = None
    condicion_impositiva: str | None = None
    opciones_centro_costo: list[OpcionCentroCosto] = field(default_factory=list)
    opciones_macro_categoria: list[OpcionMacroCategoria] = field(default_factory=list)
    paso_03: PasoConceptoCodigo | None = None
    reglas_aplicadas: list[str] = field(default_factory=list)
    pasos: dict[str, Any] = field(default_factory=dict)
    detalle: dict[str, Any] = field(default_factory=dict)
    requiere_revision_humana: bool = False

    def como_clasificacion(self) -> dict[str, Any]:
        """Shape de ``ClasificacionContable`` (``schemas/result.py``).

        Es el puente hacia ``VoucherResult.clasificacion_contable``: mismos
        nombres de campo (``centro_costo``/``macro_categoria``/``concepto``/
        ``codigo``/``condicion_impositiva``) para que ``api.classify`` los pase
        sin traducciones ad-hoc.
        """
        return {
            "centro_costo": self.centro_costo,
            "macro_categoria": self.macro_categoria,
            "concepto": self.concepto,
            "codigo": self.codigo,
            "condicion_impositiva": self.condicion_impositiva,
        }


#: Ids de trazabilidad de las etapas de la cadena contable (E-CONC-5). **No**
#: son reglas de decisión (el paso lo decide el modelo): registran qué etapas
#: corrieron y con qué prompt, para que ``CaseRecord`` sea auditable.
REGLAS_CADENA_CONTABLE: tuple[str, ...] = ("CC-01", "CC-02", "CC-03")


def clasificar_pasos_contables(
    paso_01: Mapping[str, Any] | list[Any],
    paso_02: Mapping[str, Any] | list[Any],
    paso_03: Mapping[str, Any],
    *,
    condicion_impositiva: str | None = None,
) -> ResultadoCadenaContable:
    """Resuelve la cadena sobre pasos **ya resueltos**, sin red (T-304).

    Es el núcleo puro: los tres pasos entran como JSON (o listas de opciones) y
    sale el :class:`ResultadoCadenaContable`. Usa los mismos accesores
    ``primary_*`` que el pipeline real, así que la variante pura y la real no
    pueden divergir en cómo eligen el centro/macro principal.

    Lanza:
        :class:`RespuestaContableInvalida` si el paso 01 o 02 no traen opciones
        (la cadena no inventa la entrada del paso siguiente) o si el paso 03 no
        es un objeto.
    """
    centro = primary_centro_costo(paso_01)
    macro = primary_macro_categoria(paso_02)
    contrato_03 = paso_concepto_codigo(paso_03)

    condicion = (
        condicion_impositiva
        or contrato_03.condicion_impositiva
        or CONDICION_IMPOSITIVA_DEFAULT
    )

    return ResultadoCadenaContable(
        centro_costo=centro,
        macro_categoria=macro,
        concepto=contrato_03.concepto or None,
        codigo=contrato_03.codigo_final,
        condicion_impositiva=condicion,
        opciones_centro_costo=opciones_centro_costo(paso_01),
        opciones_macro_categoria=opciones_macro_categoria(paso_02),
        paso_03=contrato_03,
        reglas_aplicadas=list(REGLAS_CADENA_CONTABLE),
        pasos={
            PASOS_CONTABLES["01"]["clave"]: paso_01,
            PASOS_CONTABLES["02"]["clave"]: paso_02,
            PASOS_CONTABLES["03"]["clave"]: paso_03,
        },
        detalle={
            "criterio": (
                "Cadena contable 01→02→03: el paso siguiente consume el primer "
                "resultado del anterior (v1/classification_pipeline.py)."
            ),
            "versiones_prompt": {
                paso: definicion["version"] for paso, definicion in PASOS_CONTABLES.items()
            },
            "concepto": contrato_03.concepto,
            "cuenta_contable": contrato_03.cuenta_contable,
            "candidatos_codigo_final": list(contrato_03.candidatos_codigo_final),
            "confianza": contrato_03.confianza,
            "criterio_inferido": contrato_03.criterio_inferido,
            "justificacion": contrato_03.justificacion,
        },
        requiere_revision_humana=contrato_03.requiere_revision_humana,
    )


# ---------------------------------------------------------------------------
# Checkpoints (paridad con v1)
# ---------------------------------------------------------------------------


def ruta_checkpoint(documento: str | Path) -> Path:
    """Ruta del sidecar de checkpoints de un documento (paridad con v1).

    v1: ``document_path.with_name(f"{document_path.stem}_classification.json")``.
    Se conserva el nombre exacto porque T-305 compara contra esos sidecar.
    """
    ruta = Path(documento)
    return ruta.with_name(f"{ruta.stem}_classification.json")


def escribir_checkpoint(ruta: str | Path, pasos: Mapping[str, Any], archivo: str = "") -> Path:
    """Escribe el checkpoint de la cadena de forma **atómica** (T-304).

    Shape portado de v1 (``write_checkpoint``): ``{"archivo": ..., "pasos": {...}}``.
    La escritura es atómica (tmp + ``replace``) porque v1 escribía **después de
    cada paso** y una interrupción a mitad de escritura dejaría un sidecar
    ilegible que rompería la reanudación.

    Devuelve la ruta escrita.
    """
    import json

    destino = Path(ruta)
    destino.parent.mkdir(parents=True, exist_ok=True)
    carga = {"archivo": archivo or str(destino), "pasos": dict(pasos)}
    temporal = destino.with_suffix(destino.suffix + ".tmp")
    temporal.write_text(
        json.dumps(carga, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    temporal.replace(destino)
    return destino


def leer_checkpoint(ruta: str | Path) -> dict[str, Any]:
    """Lee los pasos ya resueltos de un checkpoint (T-304).

    Devuelve el dict ``pasos`` (vacío si el archivo no existe o está corrupto:
    un checkpoint ilegible debe **degradar a re-ejecutar**, nunca romper la
    corrida). Portado del patrón de reanudación de v1.
    """
    import json

    camino = Path(ruta)
    if not camino.exists():
        return {}
    try:
        datos = json.loads(camino.read_text(encoding="utf-8"))
    except (ValueError, OSError):
        return {}
    pasos = datos.get("pasos") if isinstance(datos, Mapping) else None
    return dict(pasos) if isinstance(pasos, Mapping) else {}


# ---------------------------------------------------------------------------
# Pipeline real (llama al modelo)
# ---------------------------------------------------------------------------


def _resolver_modelo(modelo: str | None, settings: Settings) -> tuple[str, int | None]:
    """Resuelve modelo y ``num_ctx`` del rol ``llm`` desde ``Settings`` (T-304).

    La cadena contable es **texto** (markdown de F1 + tablas), así que usa el rol
    ``llm`` (``qwen2.5:7b``), no el ``vlm``. Si el llamador fija ``modelo``, se
    usa tal cual (sin ``num_ctx``: el llamador es responsable), igual que F2/T-202.
    """
    if modelo:
        return modelo, None
    rol = settings.modelo_para("llm")
    if rol is None or not rol.modelo:
        raise ValueError(
            "No hay modelo configurado para el rol 'llm' (Settings, E-LIB-3); "
            "la cadena contable necesita un modelo de texto (T-304)."
        )
    return rol.modelo, rol.num_ctx


def _parsear_json(contenido: str, paso: str) -> Mapping[str, Any]:
    """Parsea la respuesta del modelo como objeto JSON (T-304).

    Tolerante con las formas reales de los modelos locales (JSON puro, JSON
    dentro de cercas markdown, JSON con prosa alrededor), igual que
    ``extract_json`` de v1 y que ``_parsear_json`` de T-302. A diferencia de
    T-302, acá una respuesta inválida es un **error de la cadena** (no una
    lectura débil): sin JSON no hay opciones y el paso siguiente no tiene
    entrada.
    """
    import json

    texto = contenido.strip()
    if not texto:
        raise RespuestaContableInvalida(
            f"El modelo devolvió una respuesta vacía en el paso {paso} de la "
            "cadena contable (T-304)."
        )

    intentos: list[str] = [texto]
    if "```" in texto:
        for indice, parte in enumerate(texto.split("```")):
            if indice % 2 == 1:
                intentos.append(parte.removeprefix("json").strip())

    for candidato in intentos:
        try:
            datos = json.loads(candidato)
        except (ValueError, TypeError):
            continue
        if isinstance(datos, Mapping):
            return datos
        raise RespuestaContableInvalida(
            f"El JSON del paso {paso} debe ser un objeto (no una lista ni un "
            f"escalar); recibido: {type(datos).__name__} (T-304)."
        )

    inicio = texto.find("{")
    if inicio >= 0:
        try:
            datos, _fin = json.JSONDecoder().raw_decode(texto[inicio:])
            if isinstance(datos, Mapping):
                return datos
        except ValueError:
            pass

    raise RespuestaContableInvalida(
        f"La respuesta del paso {paso} no contiene un objeto JSON válido "
        f"(T-304). Recibida (primeros 200 caracteres): {texto[:200]!r}"
    )


def ejecutar_paso(
    paso: str,
    cliente: OllamaClient,
    valores: Mapping[str, Any],
    *,
    modelo: str | None = None,
    settings: Settings | None = None,
) -> tuple[dict[str, Any], str]:
    """Ejecuta un paso contable contra el modelo (T-304).

    Construye los ``messages`` con el prompt versionado del paso
    (:func:`~voucherflow.classification.prompts_contable.construir_messages_contable`),
    llama a ``OllamaClient.ask`` con ``json_format=True`` y parsea la respuesta.

    Devuelve ``(json_del_paso, modelo_usado)`` — el modelo se devuelve para que
    el llamador lo registre en la trazabilidad (``RegistroEtapa.modelo``).

    Lanza:
        :class:`RespuestaContableInvalida` si la respuesta no es JSON de objeto
        (con ``pasos`` vacío: el JSON inválido no aporta resultados parciales de
        este paso).
        ``OllamaError`` (del cliente real) **se propaga**: una falla de
        comunicación no es un "paso sin opciones".
    """
    settings_usado = settings or cargar_settings()
    modelo_usado, num_ctx = _resolver_modelo(modelo, settings_usado)
    messages = construir_messages_contable(paso, valores)
    respuesta = cliente.ask(
        messages, model=modelo_usado, json_format=True, num_ctx=num_ctx
    )
    return dict(_parsear_json(respuesta.contenido, paso)), modelo_usado


def ejecutar_paso_01(
    cliente: OllamaClient,
    *,
    descripcion: str,
    proveedor: str | None = None,
    monto: str | None = None,
    modelo: str | None = None,
    settings: Settings | None = None,
) -> tuple[dict[str, Any], str]:
    """Paso 01 — hasta 3 centros de costo, ordenados por probabilidad (T-304)."""
    return ejecutar_paso(
        "01",
        cliente,
        {
            "proveedor": proveedor or VALOR_NO_INFORMADO,
            "descripcion": descripcion,
            "monto": monto or VALOR_NO_INFORMADO,
        },
        modelo=modelo,
        settings=settings,
    )


def ejecutar_paso_02(
    cliente: OllamaClient,
    *,
    descripcion: str,
    centro_costo: str,
    proveedor: str | None = None,
    monto: str | None = None,
    modelo: str | None = None,
    settings: Settings | None = None,
) -> tuple[dict[str, Any], str]:
    """Paso 02 — hasta 3 macro categorías, entrada = 1º de 01 (T-304)."""
    return ejecutar_paso(
        "02",
        cliente,
        {
            "centro_costo": centro_costo,
            "proveedor": proveedor or VALOR_NO_INFORMADO,
            "descripcion": descripcion,
            "monto": monto or VALOR_NO_INFORMADO,
        },
        modelo=modelo,
        settings=settings,
    )


def ejecutar_paso_03(
    cliente: OllamaClient,
    *,
    descripcion: str,
    macro_categoria: str,
    condicion_impositiva: str = CONDICION_IMPOSITIVA_DEFAULT,
    proveedor: str | None = None,
    monto: str | None = None,
    modelo: str | None = None,
    settings: Settings | None = None,
) -> tuple[dict[str, Any], str]:
    """Paso 03 — concepto + código final + condición impositiva (T-304)."""
    return ejecutar_paso(
        "03",
        cliente,
        {
            "macro_categoria": macro_categoria,
            "proveedor": proveedor or VALOR_NO_INFORMADO,
            "descripcion": descripcion,
            "monto": monto or VALOR_NO_INFORMADO,
            "condicion_impositiva": condicion_impositiva,
        },
        modelo=modelo,
        settings=settings,
    )


def _adjuntar_parciales(error: ErrorCadenaContable, pasos: Mapping[str, Any]) -> ErrorCadenaContable:
    """Devuelve el error con los resultados parciales, **conservando su tipo**.

    ``RespuestaContableInvalida`` hereda de ``ErrorCadenaContable`` y el llamador
    puede querer distinguir "la respuesta del modelo no era JSON" de "el paso no
    trajo opciones". Por eso no se degrada a la clase base: se reconstruye con
    ``type(error)`` y se le adjuntan los ``pasos`` resueltos hasta el momento
    (paridad con ``ClassificationError.steps`` de v1).
    """
    nuevo = type(error)(str(error))
    nuevo.pasos = dict(pasos)
    return nuevo


def ejecutar_cadena(
    cliente: OllamaClient,
    *,
    descripcion: str,
    condicion_impositiva: str = CONDICION_IMPOSITIVA_DEFAULT,
    proveedor: str | None = None,
    monto: str | None = None,
    documento: str | Path | None = None,
    checkpoint: str | Path | None = None,
    modelo: str | None = None,
    settings: Settings | None = None,
) -> ResultadoCadenaContable:
    """Corre la cadena completa 01→02→03 con checkpoints (T-304).

    Portado del ``classify_document()`` de v1: **después de cada paso** escribe el
    checkpoint (``<doc>_classification.json``) y, si el checkpoint ya tenía un
    paso resuelto, **no** vuelve a llamar al modelo para ese paso (reanudación).
    El paso siguiente consume el **primer** resultado del anterior
    (``primary_centro_costo`` / ``primary_macro_categoria``).

    Argumentos:
        cliente: ``OllamaClient`` (en la suite default un doble).
        descripcion: el markdown procesado de F1 (v1 volcaba el texto completo).
        condicion_impositiva: ``21`` (default) | ``10_5`` | ``27`` | ``2_5`` |
            ``exento_no_gravado``.
        proveedor / monto: campos que F4 todavía no extrae; ``None`` los manda
            como :data:`VALOR_NO_INFORMADO` (provisional documentado).
        documento: ruta del documento de origen (para el checkpoint implícito y
            la trazabilidad). Si es ``None`` no se escribe checkpoint salvo que
            se pase ``checkpoint`` explícito.
        checkpoint: ruta del sidecar a leer/escribir. Default: el de
            :func:`ruta_checkpoint` sobre ``documento``.
        modelo / settings: resolución del modelo (rol ``llm``).

    Devuelve:
        :class:`ResultadoCadenaContable` con los tres pasos, el centro/macro
        principales, el concepto y el código final.

    Lanza:
        :class:`ErrorCadenaContable` (o su subclase
        :class:`RespuestaContableInvalida`) con ``error.pasos`` = los pasos ya
        resueltos, para poder evaluar resultados parciales (paridad con
        ``ClassificationError`` de v1).
    """
    destino_checkpoint: Path | None = None
    if checkpoint is not None:
        destino_checkpoint = Path(checkpoint)
    elif documento is not None:
        destino_checkpoint = ruta_checkpoint(documento)

    pasos: dict[str, Any] = {}
    if destino_checkpoint is not None:
        pasos.update(leer_checkpoint(destino_checkpoint))

    archivo = str(documento) if documento is not None else ""
    clave_01 = PASOS_CONTABLES["01"]["clave"]
    clave_02 = PASOS_CONTABLES["02"]["clave"]
    clave_03 = PASOS_CONTABLES["03"]["clave"]

    def guardar() -> None:
        if destino_checkpoint is not None:
            escribir_checkpoint(destino_checkpoint, pasos, archivo)

    # ---- Paso 01 --------------------------------------------------------
    if clave_01 not in pasos:
        try:
            paso_01, _modelo = ejecutar_paso_01(
                cliente,
                descripcion=descripcion,
                proveedor=proveedor,
                monto=monto,
                modelo=modelo,
                settings=settings,
            )
        except RespuestaContableInvalida as error:
            raise _adjuntar_parciales(error, pasos) from error
        pasos[clave_01] = paso_01
        guardar()
    else:
        paso_01 = pasos[clave_01]

    try:
        centro = primary_centro_costo(paso_01)
    except RespuestaContableInvalida as error:
        raise ErrorCadenaContable(str(error), pasos) from error

    # ---- Paso 02 --------------------------------------------------------
    if clave_02 not in pasos:
        try:
            paso_02, _modelo = ejecutar_paso_02(
                cliente,
                descripcion=descripcion,
                centro_costo=centro,
                proveedor=proveedor,
                monto=monto,
                modelo=modelo,
                settings=settings,
            )
        except RespuestaContableInvalida as error:
            raise _adjuntar_parciales(error, pasos) from error
        pasos[clave_02] = paso_02
        guardar()
    else:
        paso_02 = pasos[clave_02]

    try:
        macro = primary_macro_categoria(paso_02)
    except RespuestaContableInvalida as error:
        raise ErrorCadenaContable(str(error), pasos) from error

    # ---- Paso 03 --------------------------------------------------------
    if clave_03 not in pasos:
        try:
            paso_03, _modelo = ejecutar_paso_03(
                cliente,
                descripcion=descripcion,
                macro_categoria=macro,
                condicion_impositiva=condicion_impositiva,
                proveedor=proveedor,
                monto=monto,
                modelo=modelo,
                settings=settings,
            )
        except RespuestaContableInvalida as error:
            raise _adjuntar_parciales(error, pasos) from error
        pasos[clave_03] = paso_03
        guardar()
    else:
        paso_03 = pasos[clave_03]

    return clasificar_pasos_contables(
        paso_01, paso_02, paso_03, condicion_impositiva=condicion_impositiva
    )


def base_values(
    descripcion: str, *, proveedor: str | None = None, monto: str | None = None
) -> dict[str, str]:
    """Arma el ``base_values`` de la cadena (paridad con v1, T-304).

    Portado literal de ``classify_document()`` de v1: ``proveedor`` y ``monto``
    son ``"no informado"`` hasta que F4 entregue los campos extraídos, y
    ``descripcion`` es el markdown procesado. Se expone para que F4 reemplace el
    provisional pasando los valores reales **sin cambiar el contrato**.
    """
    return {
        "proveedor": proveedor or VALOR_NO_INFORMADO,
        "descripcion": descripcion,
        "monto": monto or VALOR_NO_INFORMADO,
    }


__all__ = [
    "ErrorCadenaContable",
    "RespuestaContableInvalida",
    "OpcionCentroCosto",
    "OpcionMacroCategoria",
    "PasoConceptoCodigo",
    "ResultadoCadenaContable",
    "REGLAS_CADENA_CONTABLE",
    "primary_centro_costo",
    "primary_macro_categoria",
    "opciones_centro_costo",
    "opciones_macro_categoria",
    "paso_concepto_codigo",
    "clasificar_pasos_contables",
    "ruta_checkpoint",
    "escribir_checkpoint",
    "leer_checkpoint",
    "ejecutar_paso",
    "ejecutar_paso_01",
    "ejecutar_paso_02",
    "ejecutar_paso_03",
    "ejecutar_cadena",
    "base_values",
]

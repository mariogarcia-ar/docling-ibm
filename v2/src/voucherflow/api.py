"""API de alto nivel (facade) de ``voucherflow`` — F1.

**Fase**: F0 deja el esqueleto de la fachada pública de la librería. La
implementación de cada operación se completa cuando su módulo de capacidad
exista (F1–F5): ``process`` (F1, implementado — T-105/ORQ), ``validate`` (F2,
implementado — T-203), ``classify`` (F3), ``extract`` (F4) y ``run``/``concluir``
(F5).

El objetivo de exponer esta fachada desde F0 es **fijar la API pública** de la
librería (E-LIB-1: "librería primero, cliente después") para que el cliente
CLI/API (F6) y los notebooks consuman una superficie estable, sin conocer los
módulos internos.

Contratos que expone (doc 03 §9): ``ProcessedDocument`` (processing),
``ValidationResult`` (validation), ``VoucherResult`` / ``CombinedEvidence``
(conclusion), ``EvidenceField`` / ``SourceEvidence`` (extraction/classification).
"""

from __future__ import annotations

from .schemas.result import VoucherResult

# ---------------------------------------------------------------------------
# Errores públicos de dominio (E-LIB-1)
# ---------------------------------------------------------------------------


class VoucherflowError(RuntimeError):
    """Error base de la librería, con mensaje claro en español."""


class DocumentoNoProcesableError(VoucherflowError):
    """El documento no superó el gate de procesabilidad (F1/F2)."""


class ContratoError(VoucherflowError):
    """Una respuesta de modelo no cumple el contrato de evidencia (E-LIB-2).

    Se usa en F3/F4 cuando el JSON del VLM/LLM no valida contra
    ``schemas/evidence.py``; el mensaje incluye el detalle de pydantic.
    """


# ---------------------------------------------------------------------------
# Fachada (esqueletos de F1–F5; firmas públicas estables)
# ---------------------------------------------------------------------------


def process(origen: str, *, docling_raw: bool = False) -> "ProcessedDocument":
    """Procesa un documento a representación Markdown+boxes (F1).

    Implementación de F1 (T-105/ORQ): delega en la orquestación del módulo
    ``processing`` (``procesar_documento``), que decide la ruta por tipo de
    entrada (doc 03 §4.1 y ``docs/ideas/docling.md``):

      - PDF escaneado / imagen → gate T-102 → Docling OCR sobre la imagen
        (render→imagen para PDF escaneado, PROC.md §5).
      - PDF apto / office / texto → Docling directo (texto nativo).
      - Formato no soportado → ``DocumentoNoProcesableError`` (rechazo).

    El import de ``processing`` es **diferido** (dentro de la función) para no
    crear un ciclo de import en el arranque del paquete: ``processing`` no
    importa ``api`` a nivel de módulo, pero el import local es la opción más
    segura y explícita (el módulo ``processing`` recién se necesita al
    procesar, no al importar la fachada).

    ``docling_raw`` (Opción A, decisión de alcance subplan F1 §2.5): cuando es
    ``True``, ``ProcessedDocument.markdown`` contiene el **crudo de Docling**
    (el markdown de ``export_to_markdown()`` del adaptador, sin el reordenado
    por posición del exportador E-DOC-3); equivale a ``v1/run_raw.py``. El
    default ``False`` preserva el comportamiento actual (política combinada
    E-DOC-3, contrato F2/F3/F4). Aplica a **documento completo** (imagen / PDF
    apto / office / texto y PDF escaneado vía imagen renderizada); en un PDF
    **mixto/parcial** el crudo pleno no existe (las páginas aptas usan
    PyMuPDF, no Docling por página) y se anota en ``calidad``
    (``docling_raw: "parcial_no_aplica"``), no aplica pleno.

    Argumentos:
        origen: ruta al documento (pdf/imagen/office/txt/...).
        docling_raw: si True, devuelve en ``markdown`` el crudo de Docling (sin
            reordenar por posición); default False = comportamiento actual.

    Devuelve:
        :class:`ProcessedDocument` con ``markdown`` + ``boxes`` + metadatos.

    Lanza:
        ``FileNotFoundError`` si la ruta no existe.
        ``DocumentoNoProcesableError`` si el formato no es soportado, el PDF no
        pudo analizarse o la imagen no superó el gate de procesabilidad (F1).
    """
    # Import diferido: evita el ciclo api -> processing -> (api) en el arranque.
    from .processing.orquestacion import procesar_documento

    return procesar_documento(origen, docling_raw=docling_raw)


def validate(origen: str, quick: bool = True) -> "ValidationResult":
    """Gate "¿es comprobante?" con doble paso qween (F2).

    Implementación de F2 (T-203): delega en
    ``validation.validar_comprobante`` (que a su vez orquesta el doble paso con
    ``validar_y_procesar``) y devuelve el :class:`ValidationResult` del gate.
    El import es **diferido** (dentro de la función) para no crear un ciclo de
    import en el arranque del paquete: ``validation`` importa ``processing``
    (lazy) y la fachada no necesita el módulo hasta que se llama ``validate``
    (mismo criterio que ``process`` y su comentario).

    Sobre ``quick``: en F2 no cambia el flujo (la orquestación siempre hace el
    doble paso); se conserva por compatibilidad con el contrato de la fachada
    (F0). Para obtener la vista fiel de extracción o la trazabilidad de las
    pasadas, usar ``validation.validar_y_procesar``.

    Argumentos:
        origen: ruta al documento (pdf/imagen/office/txt/...).
        quick: reservado; hoy no altera el flujo del doble paso.

    Devuelve:
        :class:`voucherflow.validation.ValidationResult` del gate.

    Lanza:
        ``FileNotFoundError`` / ``DocumentoNoProcesableError`` si F1 rechaza el
        documento; ``OllamaError`` si falla la comunicación con Ollama.
    """
    # Import diferido: evita el ciclo api -> validation -> processing -> (api).
    from .validation import validar_comprobante

    return validar_comprobante(origen, quick=quick)


def classify(markdown: str, condicion_impositiva: str | None = None) -> VoucherResult:
    """Clasifica tipo/letra + contable sobre el markdown procesado (F3).

    Implementación de F3 (T-304): corre dos subflujos sobre el **markdown** de
    F1 y devuelve un :class:`~voucherflow.schemas.result.VoucherResult` con el
    tipo/letra y la clasificación contable.

    Sobre el **tipo/letra**: este método no lee la imagen — recibe texto —, así
    que la evidencia de lectura que puede aportar es la del flujo de texto (la
    **regex de R5** sobre el encabezado). El motor R1-R7 (T-301) decide la letra
    cruzando eso con la condición fiscal, que T-304 todavía **no** conoce (la
    aportará F4). Por eso el resultado es deliberadamente **parcial y honesto**:
    ``tipo_comprobante`` sale del motor con la evidencia disponible, la certeza
    es baja y ``campos_desconocidos`` declara las condiciones fiscales que
    faltaron. La clasificación **contable** sí queda completa, porque solo
    necesita el texto.

    La cadena contable se ejecuta en modo **real** (``ejecutar_cadena``: pasos
    01→02→03 contra ``OllamaClient``, con checkpoints). Si un paso falla, se
    propaga :class:`~voucherflow.classification.contable.ErrorCadenaContable`
    con los resultados parciales en ``error.pasos`` — no se silencia.

    El import es **diferido** (dentro de la función) para no crear un ciclo en el
    arranque del paquete: ``classification`` no importa ``api`` a nivel de módulo
    (mismo criterio que ``process`` y ``validate``).

    Argumentos:
        markdown: markdown/OCR procesado de F1 (``api.process(...).markdown``).
        condicion_impositiva: ``21`` (default) | ``10_5`` | ``27`` | ``2_5`` |
            ``exento_no_gravado``.

    Devuelve:
        :class:`VoucherResult` con ``tipo_comprobante``, ``certeza``,
        ``origen``, ``clasificacion_contable`` y la traza en ``trazabilidad``.

    Lanza:
        :class:`~voucherflow.classification.contable.ErrorCadenaContable` si un
        paso contable no devuelve lo que la cadena necesita.
        ``OllamaError`` si falla la comunicación con Ollama.
    """
    # Import diferido: evita el ciclo api -> classification -> (api).
    from .classification.contable import ErrorCadenaContable, ejecutar_cadena
    from .classification.prompts_contable import CONDICION_IMPOSITIVA_DEFAULT
    from .classification.tipo_comprobante import clasificar_tipo_comprobante
    from .models.ollama import OllamaClient
    from .rules.contexto import ContextoTipoComprobante
    from .schemas.evidence import Certeza, Origen
    from .schemas.result import ClasificacionContable, EstadoResultado

    condicion = condicion_impositiva or CONDICION_IMPOSITIVA_DEFAULT

    # --- Tipo/letra con la única evidencia disponible en texto (R5) --------
    # El markdown ES el texto de encabezado candidato: R5 busca en él la
    # expresión ``FACTURA <letra>``. Las condiciones fiscales no llegan todavía
    # (son de F4), así que el motor decide con lo que tiene y lo declara.
    contexto = ContextoTipoComprobante(
        texto_encabezado_llm=markdown or "",
        campos_ausentes=["emisor.condicion_fiscal", "receptor.condicion_fiscal"],
    )
    tipo = clasificar_tipo_comprobante(contexto)

    # --- Cadena contable 01→02→03 (real, con checkpoints) ------------------
    resultado_contable = ejecutar_cadena(
        OllamaClient(),
        descripcion=markdown or "",
        condicion_impositiva=condicion,
    )

    return VoucherResult(
        documento_id=_identificador_de_markdown(markdown),
        estado=EstadoResultado.revision,
        tipo_comprobante=tipo.letra,
        certeza=Certeza(tipo.certeza) if tipo.certeza else None,
        origen=Origen.programa,
        clasificacion_contable=ClasificacionContable(
            **resultado_contable.como_clasificacion()
        ),
        trazabilidad={
            "tipo_comprobante": tipo.detalle,
            "cadena_contable": resultado_contable.detalle,
            "requiere_revision_humana": resultado_contable.requiere_revision_humana,
            "condicion_impositiva": condicion,
            "nota": (
                "T-304: la letra se decide con la evidencia de texto disponible "
                "(R5) y las condiciones fiscales faltantes se declaran; la "
                "cadena contable corre completa 01→02→03."
            ),
        },
    )


def _identificador_de_markdown(markdown: str) -> str:
    """Deriva un ``documento_id`` estable del markdown (T-304).

    En producción el id es el **hash sha256 del archivo** (glosario §2). Acá solo
    hay texto, así que se usa el sha256 del markdown: es estable, no colisiona
    entre documentos distintos y permite correlacionar llamadas. El contrato de
    ``VoucherResult.documento_id`` exige un string no vacío.
    """
    import hashlib

    contenido = markdown or ""
    return hashlib.sha256(contenido.encode("utf-8")).hexdigest()


def extract(origen: str, mode: str = "kvi") -> "CombinedEvidence":
    """Extrae evidencia VLM+LLM de un documento (F4).

    Esqueleto F0 — se implementa en F4 (módulo ``extraction``).
    """
    raise NotImplementedError("extract(): se implementa en F4 (módulo extraction).")


def run(origen: str) -> VoucherResult:
    """Pipeline completo document → VoucherResult (F5).

    Esqueleto F0 — se implementa en F5 (módulo ``conclusion`` + orquestador).
    """
    raise NotImplementedError("run(): se implementa en F5 (módulo conclusion/orquestador).")


__all__ = [
    "VoucherflowError",
    "DocumentoNoProcesableError",
    "ContratoError",
    "process",
    "validate",
    "classify",
    "extract",
    "run",
]

"""Orquestación de la Fase F1 (T-105/ORQ, épica E-DOC) — ``procesar_documento``.

Es la pieza que conecta el pipeline de ``processing`` (T-101..T-104 + routing)
con el adaptador Docling (F0/T-006) y produce el contrato de salida
:class:`~voucherflow.models.docling.ProcessedDocument` que consume ``api.process``
(entrada de F2/F3/F4).

Responsabilidades (doc 03 §4.1 + ``docs/ideas/docling.md``):

  1. Validar que el archivo existe (``FileNotFoundError``).
  2. Detectar el tipo de entrada (T-101, E-DOC-1).
  3. Elegir la ruta de procesamiento según el tipo (texto nativo vs. imagen):

       - ``no_soportado`` → rechazo con excepción de dominio.
       - ``pdf_texto``/``pdf_escaneado`` → se refina con ``routing`` por página
         (T-105/EXT) porque el detector es binario y el PDF puede ser mixto.
       - ``imagen`` / ``pdf_escaneado`` → pipeline de imagen (E-DOC-2).
       - ``office`` / ``texto`` → Docling directo (texto nativo).

  4. En imágenes: gate de procesabilidad (T-102) → clase (T-102) →
     preprocesamiento (T-103) → orientación (T-103) → motor (T-104) →
     exportador ordenado (T-104/E-DOC-3).

Hallazgo clave validado (PROC.md §5): la ruta correcta para **PDF escaneado**
es **renderizar a imagen** (PyMuPDF ~300 dpi, RGB sin alfa) y pasarla por el
pipeline de imagen (Docling OCR); pasar el PDF directo a Docling es
**impredecible** (a veces corre OCR, a veces deja solo ``<!-- image -->``).

Contrato público (congelado para F2/F3/F4):

    ``procesar_documento(origen, *, converter=None, modo_motor="auto", docling_raw=False) -> ProcessedDocument``
    ``procesar_imagen(origen, *, converter=None, modo_motor="auto", tipo_entrada="imagen", docling_raw=False, ...) -> ProcessedDocument``
    ``render_pdf_a_jpg(pdf, pagina=0, dpi=300) -> Path``

Opción A (decisión de alcance, subplan F1 §2.5): el keyword ``docling_raw``
expone en ``ProcessedDocument.markdown`` el **raw de Docling** (el markdown
crudo de ``export_to_markdown()`` del adaptador, sin el reordenado por
posición del exportador E-DOC-3); equivale a ``v1/run_raw.py``. Aplica al
documento completo (imagen/PDF apto/office/texto y PDF escaneado vía imagen
renderizada); en PDF mixto/parcial el crudo pleno no existe (las páginas aptas
usan PyMuPDF, no Docling por página) y se anota ``parcial_no_aplica`` en
``calidad``. Ver docstring de cada subrutina.

Reglas duras (subplan F1 §4): la suite default corre **sin** Docling real ni
Ollama (el ``converter`` es inyectable para tests); ``ProcessedDocument`` sigue
siendo construible con 3 argumentos posicionales (F0); no se agregan
dependencias (solo stdlib + PyMuPDF/docling ya declarados).
"""

from __future__ import annotations

import tempfile
from pathlib import Path
from typing import Any

# ---------------------------------------------------------------------------
# Import diferido de la excepción de dominio (evita el ciclo api -> processing)
# ---------------------------------------------------------------------------

# ``api.py`` define los errores públicos de dominio (E-LIB-1):
#   ``VoucherflowError`` / ``DocumentoNoProcesableError`` / ``ContratoError``.
# Se importan aquí de forma **diferida** (dentro de la función que los lanza)
# para no crear un ciclo de import en el arranque del paquete:
#
#   voucherflow.api -> (módulo) -> voucherflow.schemas.result
#                              -> voucherflow.schemas.evidence
#                          -> (schema congelado, no toca processing)
#
# ``api`` **no** importa ``processing`` a nivel de módulo (solo lo hace dentro
# de ``process()``), así que un import a nivel de módulo de ``api`` aquí sería
# igualmente seguro; aun así, el import diferido deja explícito que el error de
# dominio es un contrato del *dominio* (``api``), no de ``processing``.
def _error_no_procesable(mensaje: str) -> "Exception":
    """Construye la excepción de dominio de rechazo (import diferido).

    Usa :class:`voucherflow.api.DocumentoNoProcesableError` (F1): es el error
    público del gate de procesabilidad (doc 03 §4.1: GATE -->|no pasa| REJ).
    """
    from ..api import DocumentoNoProcesableError

    return DocumentoNoProcesableError(mensaje)


# ---------------------------------------------------------------------------
# Helper de render de PDF a imagen (portado de scripts/implementar_t104.py)
# ---------------------------------------------------------------------------

def render_pdf_a_jpg(pdf: str | Path, pagina: int = 0, dpi: int = 300) -> Path:
    """Renderiza una página de un PDF a un archivo JPG temporal (RGB sin alfa).

    Porta ``_render_pdf_a_jpg`` de ``scripts/implementar_t104.py`` (validado en
    PROC.md §5): si la página tiene imagen(es), renderiza el **área de la
    imagen más grande** (clip) con zoom — evita el caso de un ticket/recibo
    chico centrado en una hoja A4 escaneada, que a página completa queda
    diminuto y Docling no lo lee (ej. fixture ``3ac5a2ec``). Si no hay
    imágenes, renderiza la página completa.

    El JPG se crea en un directorio temporal (``tempfile.mkdtemp``). El
    llamador es responsable de borrarlo tras convertir
    (``Path.unlink(missing_ok=True)``); las funciones de orquestación lo hacen
    en un ``finally``.

    Argumentos:
        pdf: ruta al PDF.
        pagina: índice de página a renderizar (0-based; default 0).
        dpi: resolución objetivo en puntos por pulgada (default 300, PROC.md §5).

    Devuelve:
        ``Path`` al JPG creado (RGB, sin canal alfa).
    """
    import pymupdf as fitz  # PyMuPDF (dependencia del paquete, F1/T-101)

    doc = fitz.open(str(pdf))
    try:
        if not (0 <= pagina < doc.page_count):
            raise IndexError(
                f"render_pdf_a_jpg(): página {pagina} fuera de rango "
                f"(el PDF tiene {doc.page_count} página(s))."
            )
        hoja = doc[pagina]
        infos = hoja.get_image_info()
        if infos:
            # Tomar la imagen de mayor área.
            mayor = max(
                infos,
                key=lambda i: (i["bbox"][2] - i["bbox"][0]) * (i["bbox"][3] - i["bbox"][1]),
            )
            bbox = fitz.Rect(mayor["bbox"])
            # Zoom para que el lado mayor de la imagen quede ~2000 px (bueno para OCR).
            lado_px = max(bbox.width, bbox.height)
            zoom = max(2000 / lado_px, dpi / 72) if lado_px else dpi / 72
            mat = fitz.Matrix(zoom, zoom)
            pix = hoja.get_pixmap(matrix=mat, clip=bbox, colorspace=fitz.csRGB)
        else:
            mat = fitz.Matrix(dpi / 72, dpi / 72)
            pix = hoja.get_pixmap(matrix=mat, colorspace=fitz.csRGB)
    finally:
        doc.close()

    tmp = Path(tempfile.mkdtemp(prefix="vf_render_")) / "pagina.jpg"
    pix.save(str(tmp), jpg_quality=95)
    return tmp


# ---------------------------------------------------------------------------
# Subrutina común de texto nativo (doc 03 §4.1: pdf_texto apto / office / texto)
# ---------------------------------------------------------------------------

def _procesar_texto_nativo(
    ruta: Path,
    tipo_entrada: str,
    *,
    converter: Any,
    nota_calidad: str | None = None,
    docling_raw: bool = False,
) -> "ProcessedDocument":
    """Ruta de texto nativo: Docling directo + orientación + exportador (E-DOC).

    Es la subrutina que comparten ``pdf_texto`` (apto), ``office`` y ``texto``:
    el adaptador Docling (F0/T-006) convierte el archivo (PDF con capa de
    texto, docx/xlsx/pptx, html/md/txt) y luego se completa el contrato:

      - ``tipo_entrada`` final (``pdf_texto`` / ``office`` / ``texto``).
      - ``orientacion = orientacion_de(boxes)`` (T-103; default horizontal si
        no hay boxes).
      - ``markdown``: política combinada E-DOC-3 por defecto (conserva las
        tablas del crudo y ordena el texto por posición cuando no las hay) o,
        si ``docling_raw=True`` (Opción A, subplan F1 §2.5), el **crudo de
        Docling** tal cual lo devolvió ``conv.convert`` (sin reordenar por
        posición; equivale a ``v1/run_raw.py``).
      - ``calidad``: texto nativo no pasa por el gate de imagen (T-102); se
        deja ``None`` salvo que el llamador provea una nota (p. ej. un PDF con
        ruteo parcial, donde conviene registrar la estrategia aplicada). Con
        ``docling_raw=True`` se añade la marca ``docling_raw: True`` y la nota
        ``"markdown_crudo_docling"``.
      - ``motor = "docling"`` (motor efectivo F1, subplan §2.2).

    Nota (Opción A): con ``docling_raw=True`` los ``boxes`` se conservan tal
    cual, aunque el crudo puede no estar alineado con su posición (aceptable
    para debug; el markdown final es el crudo, no el exportado por posición).

    Argumentos:
        ruta: archivo a convertir (PDF apto, office o texto).
        tipo_entrada: valor final de ``ProcessedDocument.tipo_entrada``.
        converter: adaptador Docling inyectable (default real lazy).
        nota_calidad: nota opcional para ``calidad`` (dict pequeño).
        docling_raw: si True, ``markdown`` es el crudo de Docling (sin el
            reordenado por posición E-DOC-3); default False = política
            combinada actual (contrato F2/F3/F4).

    Devuelve:
        ``ProcessedDocument`` completo.
    """
    from ..models.docling import DoclingConverter
    from .markdown_exporter import exportar_documento
    from .orientation import orientacion_de

    conv = converter if converter is not None else DoclingConverter()
    doc = conv.convert(ruta)

    # PUNTO CLAVE (validado, Opción A): ``conv.convert`` ya devuelve un
    # ``ProcessedDocument`` cuyo ``markdown`` ES el crudo de Docling
    # (``export_to_markdown()`` del adaptador, models/docling.py). Se captura
    # ANTES de pisarlo; así el modo raw NO vuelve a correr Docling.
    markdown_crudo = doc.markdown

    doc.tipo_entrada = tipo_entrada
    doc.ruta = str(ruta)  # se conserva la ruta del archivo original
    doc.orientacion = orientacion_de(doc.boxes)
    if docling_raw:
        # Opción A (subplan F1 §2.5): crudo de Docling sin reordenar.
        doc.markdown = markdown_crudo
    else:
        doc.markdown = exportar_documento(doc)
    doc.motor = "docling"

    if nota_calidad or docling_raw:
        base = dict(doc.calidad or {})
        if nota_calidad:
            base.update(nota_calidad)
        if docling_raw:
            # Marca de modo raw (Opción A): el markdown es el crudo de Docling.
            base["docling_raw"] = True
            base["salida"] = "markdown_crudo_docling"
        doc.calidad = base
    else:
        doc.calidad = None
    return doc


# ---------------------------------------------------------------------------
# PDF apto: texto nativo con pdftotext --layout (mejor layout de columnas)
# ---------------------------------------------------------------------------

def _procesar_pdf_apto(
    ruta: Path,
    *,
    converter: Any,
    analisis: "AnalisisPdf",
    docling_raw: bool = False,
) -> "ProcessedDocument":
    """Ruta PDF apto: ``pdftotext --layout`` preferido, Docling como fallback.

    Decisión 2026-09-07 (revierte A1 de 2026-09-06, subplan F1 §2.6 / PROC.md
    §5.3): para un PDF ``apto`` (texto nativo en todas las páginas, las
    imágenes no dominan), se prefiere extraer con ``pdftotext --layout``
    porque recupera el **layout de columnas** que Docling directo aplana
    (caso ``9dfc597f``, boleto a 2 columnas; PROC.md §5.2: "excelente layout").

    Reglas:
      - Si ``pdftotext`` está disponible y devuelve texto no vacío → se usa
        ese texto (``motor="pdftotext"``, orientación ``horizontal``, calidad
        con ``salida: "pdftotext_layout"``). Es la ruta preferida.
      - Si no está disponible, falla o devuelve vacío → **fallback** a
        ``_procesar_texto_nativo`` (Docling directo; comportamiento previo A1).
      - ``docling_raw=True`` (Opción A, subplan F1 §2.5) → se mantiene Docling
        directo siempre (el crudo pleno de Docling es la semántica de ``raw``,
        equiv. ``v1/run_raw.py``; pdftotext no produce el crudo de Docling).
    """
    from .pdftotext import extraer_con_pdftotext_layout

    if not docling_raw:
        texto = extraer_con_pdftotext_layout(ruta)
        if texto:
            # pdftotext --layout: layout de columnas preservado (PROC.md §5.2).
            from ..models.docling import ProcessedDocument

            doc = ProcessedDocument(
                tipo_entrada="pdf_texto",
                ruta=str(ruta),
                markdown=texto if texto.endswith("\n") else texto + "\n",
                motor="pdftotext",
                orientacion="horizontal",
                calidad={
                    "routing": "apto",
                    "salida": "pdftotext_layout",
                    "motor": "pdftotext",
                    "nota": analisis.resumen,
                },
            )
            return doc

    # Fallback (o docling_raw=True): Docling directo (texto nativo, previo A1).
    return _procesar_texto_nativo(
        ruta, "pdf_texto", converter=converter,
        nota_calidad={"routing": "apto", "nota": analisis.resumen},
        docling_raw=docling_raw,
    )


# ---------------------------------------------------------------------------
# Subrutina de imagen (flujo E-DOC-2 + ideas/docling.md)
# ---------------------------------------------------------------------------

def procesar_imagen(
    origen: str | Path,
    *,
    converter: Any = None,
    modo_motor: str = "auto",
    tipo_entrada: str = "imagen",
    tipo_entrada_origen: str | None = None,
    ruta_publica: str | Path | None = None,
    docling_raw: bool = False,
) -> "ProcessedDocument":
    """Procesa una imagen por el pipeline de imagen (E-DOC-2).

    Flujo (doc 03 §4.1 + ``docs/ideas/docling.md`` subrutina ``procesar_imagen``):

      1. ``verificar_procesabilidad`` (T-102) → gate de procesabilidad. Si
         ``procesable=False`` se lanza la excepción de dominio
         (``DocumentoNoProcesableError``) sin gastar OCR (doc 03 §4.1: GATE -->|no
         pasa| REJ). Una advertencia de ``razon_rechazo`` (p. ej.
         ``resolucion_baja``) **no** rechaza: se registra en ``calidad``.
      2. ``clasificar`` (T-102) → clase de imagen.
      3. ``evaluar_calidad`` (T-103) → ``QualityReport``.
      4. ``elegir_motor`` (T-104, ``modo_motor``) → selecciona ``ocr``/``vlm``.
      5. Hook de preprocesamiento (T-103): se llama **siempre** a
         ``preprocesar`` (es idempotente en F1: devuelve la misma ruta; sin CV
         no transforma píxeles, subplan §2.1). Si ``quality`` no requiere
         preprocesamiento, la ruta a procesar es el original.
      6. Conversión: en F1 el motor de transcripción efectivo es **siempre**
         Docling (``docling``). Si ``elegir_motor`` devuelve ``vlm``, **no** se
         llama a ``transcribir_vlm`` (F4, subplan §2.2): la selección queda
         anotada en ``calidad`` y el OCR lo hace Docling igual.
      7. Completar el contrato: ``tipo_entrada``, ``orientacion`` (T-103),
         ``markdown`` y ``motor="docling"`` y ``calidad`` enriquecida
         (QualityReport + motor seleccionado + clase + gate).

    ``markdown`` (E-DOC-3 vs. Opción A): por defecto (``docling_raw=False``)
    se aplica ``exportar_documento`` (política combinada — conserva tablas del
    crudo y ordena el texto por posición). Con ``docling_raw=True`` (decisión
    de alcance subplan F1 §2.5) el ``markdown`` es el **crudo de Docling** tal
    cual lo devolvió ``conv.convert`` (sin reordenar por posición; equivale a
    ``v1/run_raw.py``); se anota en ``calidad`` ``docling_raw: True`` y
    ``salida: "markdown_crudo_docling"``. Con el default se anota (solo
    informativo, no rompe tests) ``salida: "politica_combinada"`` (o
    ``"exportado_por_posicion"`` cuando no aplica tabla). Los ``boxes`` se
    conservan como estén (el crudo puede no estar alineado con boxes;
    aceptable para debug — documentado).

    Argumentos:
        origen: ruta de la imagen (o del JPG renderizado de un PDF escaneado).
        converter: adaptador Docling inyectable (tests); default real lazy.
        modo_motor: modo de selección de motor (T-104): ``"auto"`` (default),
            ``"ocr"`` o ``"vlm"``.
        tipo_entrada: valor final de ``ProcessedDocument.tipo_entrada``
            (``"imagen"`` por defecto; ``"pdf_escaneado"`` cuando el llamador
            viene de un PDF renderizado).
        tipo_entrada_origen: nota opcional en ``calidad`` con el tipo que
            detectó T-101 (p. ej. ``"pdf_escaneado"``) para trazabilidad.
        ruta_publica: ruta que se expone en ``ProcessedDocument.ruta``. Cuando
            la imagen procesada es un JPG temporal renderizado de un PDF
            escaneado (PROC.md §5), el documento público debe apuntar al PDF
            original y no al temporal (que se borra). Si no se pasa, se usa
            ``origen``.
        docling_raw: si True, ``markdown`` es el crudo de Docling (sin el
            reordenado por posición E-DOC-3); default False = política
            combinada actual (contrato F2/F3/F4).

    Devuelve:
        ``ProcessedDocument`` completo.

    Lanza:
        ``FileNotFoundError`` si la imagen no existe.
        ``voucherflow.api.DocumentoNoProcesableError`` si no supera el gate
        (T-102) o Docling no puede convertir.
    """
    from ..models.docling import DoclingConverter
    from .image_classifier import clasificar, verificar_procesabilidad
    from .markdown_exporter import exportar_documento
    from .ocr import MotorOCR, elegir_motor
    from .orientation import orientacion_de
    from .preprocessing import evaluar_calidad, preprocesar

    ruta = Path(origen)
    if not ruta.exists():
        raise FileNotFoundError(f"No existe el archivo a procesar: {ruta}")

    # 1. Gate de procesabilidad (T-102, E-DOC-2).
    veredicto = verificar_procesabilidad(ruta)
    if not veredicto.procesable:
        raise _error_no_procesable(
            f"La imagen '{ruta.name}' no superó el gate de procesabilidad (T-102): "
            f"{veredicto.motivo}"
        )

    # 2. Clasificación (T-102) + 3. calidad (T-103).
    clasificacion = clasificar(ruta)
    quality = evaluar_calidad(clasificacion)

    # 4. Selección de motor (T-104). En F1 el motor efectivo es SIEMPRE Docling.
    motor = elegir_motor(clasificacion, modo=modo_motor)

    # 5. Hook de preprocesamiento (T-103). En F1 ``preprocesar`` es un stub
    #    idempotente (devuelve la misma ruta, sin CV, subplan §2.1). Se llama
    #    SIEMPRE: así el hook queda activo y listo para F4+ sin cambiar la
    #    orquestación; si la calidad no requiere acciones, la ruta es el
    #    original.
    ruta_a_procesar = Path(
        preprocesar(str(ruta), clasificacion, quality)
        if quality.requiere_preprocesamiento
        else str(ruta)
    )

    # 6. Conversión: motor efectivo Docling (F1). Si ``motor == vlm`` NO se
    #    llama a ``transcribir_vlm`` (F4, subplan §2.2); la selección se
    #    anota en ``calidad`` para trazabilidad.
    conv = converter if converter is not None else DoclingConverter()
    try:
        doc = conv.convert(ruta_a_procesar)
    except FileNotFoundError:
        raise
    except Exception as exc:
        # Docling pudo fallar por formato/corrupción; se traduce al error de
        # dominio del gate (la imagen "superó" el gate barato pero no convirtió).
        raise _error_no_procesable(
            f"La imagen '{ruta.name}' no pudo convertirse a texto (Docling): {exc}"
        ) from exc

    # 7. Completar el contrato (E-DOC-1/E-DOC-2/E-DOC-3).
    # PUNTO CLAVE (validado, Opción A): ``conv.convert`` ya devuelve un
    # ``ProcessedDocument`` cuyo ``markdown`` ES el crudo de Docling
    # (``export_to_markdown()`` del adaptador, models/docling.py). Se captura
    # ANTES de pisarlo; así el modo raw NO vuelve a correr Docling.
    markdown_crudo = doc.markdown

    doc.tipo_entrada = tipo_entrada
    # La ruta pública es el documento original; si se procesó un JPG temporal
    # renderizado (PDF escaneado), el llamador pasa ``ruta_publica`` con el PDF.
    doc.ruta = str(ruta_publica) if ruta_publica is not None else str(ruta)
    doc.orientacion = orientacion_de(doc.boxes)
    if docling_raw:
        # Opción A (subplan F1 §2.5): crudo de Docling sin reordenar.
        doc.markdown = markdown_crudo
    else:
        doc.markdown = exportar_documento(doc)
    doc.motor = "docling"

    calidad = dict(quality.a_dict())
    calidad["motor_seleccionado"] = motor.value
    calidad["clase_imagen"] = clasificacion.clase.value
    calidad["gate"] = veredicto.motivo
    if docling_raw:
        # Marca de modo raw (Opción A): el markdown es el crudo de Docling.
        calidad["docling_raw"] = True
        calidad["salida"] = "markdown_crudo_docling"
    else:
        # Nota informativa de política de salida (E-DOC-3); no rompe tests
        # existentes (solo añade claves).
        hay_tabla_cruda = "|" in (doc.markdown or "")
        hay_tablas_en_boxes = any(b.es_tabla and b.markdown_tabla for b in (doc.boxes or []))
        calidad["salida"] = "politica_combinada" if (hay_tabla_cruda and not hay_tablas_en_boxes) else "exportado_por_posicion"
    if veredicto.razon_rechazo:
        calidad["razon_rechazo"] = veredicto.razon_rechazo
    if tipo_entrada_origen:
        calidad["tipo_entrada_origen"] = tipo_entrada_origen
    if motor == MotorOCR.vlm:
        calidad["motor_efectivo"] = "docling"  # F1: OCR aunque se seleccione VLM
    doc.calidad = calidad

    return doc


# ---------------------------------------------------------------------------
# Procesamiento parcial de PDF (routing: mezcla de páginas aptas y escaneadas)
# ---------------------------------------------------------------------------

def _procesar_pdf_parcial(
    ruta: Path,
    analisis: "AnalisisPdf",
    *,
    converter: Any,
    modo_motor: str,
    tipo_entrada: str,
    docling_raw: bool = False,
) -> "ProcessedDocument":
    """Procesa un PDF mixto (veredicto ``parcial``) por página (PROC.md §5).

    Decisión de orquestación (documentada, alcance mínimo F1): el veredicto
    ``parcial`` del routing significa que el PDF mezcla páginas aptas (texto
    nativo) con escaneadas/corruptas (OCR). El contrato ``ProcessedDocument``
    es de **documento único**, así que la estrategia pragmática y alineada con
    PROC.md §5 (implementada como mínimo viable de F1) es:

      1. Se recorren las páginas **en orden** (1-indexado):
         - páginas en ``paginas_ocr`` → render→imagen (``render_pdf_a_jpg``) y
           se procesan por el pipeline de imagen (``procesar_imagen``: gate
           T-102 → Docling OCR → exportador), porque para páginas escaneadas el
           PDF directo a Docling es impredecible (PROC.md §5).
         - páginas aptas/vacías → texto nativo por página con PyMuPDF
           (``fitz``) en F1 (equivalente al complemento ``pdftotext`` que
           documenta PROC.md §5.2; Docling "por página de un PDF" no es
           invocable sin extraer la página).
      2. Se concatenan los markdown en orden de página con separadores
         ``\n\n``.
      3. Se fusionan los boxes de las páginas OCR compensando ``center_y`` por
         página (offset = (n-1) × alto de página en puntos).
      4. Cuando no hay tablas en el crudo y hay boxes, el ``markdown`` final se
         reordena con el exportador por posición (E-DOC-3); si hay tablas se
         conserva el crudo concatenado.

    Decisión de alcance Opción A — PDF **parcial/mixto** (subplan F1 §2.5): el
    crudo pleno de Docling **no existe** para este documento (las páginas aptas
    usan PyMuPDF ``get_text``, no Docling por página; solo el documento
    completo y las páginas OCR tienen crudo Docling). Por eso, cuando
    ``docling_raw=True`` **no** se promete crudo real por página apta: se
    mantiene el comportamiento de concatenación actual (idéntico al default,
    sin propagar el flag a las páginas) y se anota en ``calidad`` la marca
    ``docling_raw: "parcial_no_aplica"`` + ``salida: "concatenado_parcial"``
    con una nota explicativa breve. Si se quisiera el crudo Docling de un PDF
    mixto de verdad, haría falta procesarlo como documento completo (fuera de
    esta ruta) o una mejora de fusión por página (documentada como limitación
    F1 abajo).

    Nota (limitación F1, mejora para cierre de docs / T-105): la fusión fina
    por página (mezclar Docling por página apta con offset exacto de puntos
    sobre un mismo sistema de coordenadas) queda como mejora documentada; este
    es un caso de borde (la mayoría de los comprobantes son 1 página: todo apto
    u todo escaneado) y aquí se resuelve de forma conservadora y determinista.

    Argumentos:
        ruta: PDF mixto.
        analisis: :class:`AnalisisPdf` del routing (veredicto ``parcial``).
        converter: adaptador Docling inyectable.
        modo_motor: modo de selección de motor para las páginas OCR.
        tipo_entrada: valor final de ``ProcessedDocument.tipo_entrada``
            (el detector T-101 devuelve ``pdf_texto`` para PDFs mixtos).
        docling_raw: si True, no aplica pleno a PDF parcial/mixto; se mantiene
            la concatenación actual y se anota ``parcial_no_aplica`` en
            ``calidad`` (Opción A, subplan F1 §2.5).

    Devuelve:
        ``ProcessedDocument`` con el markdown concatenado/fusionado.
    """
    from ..models.docling import Box, ProcessedDocument
    from .markdown_exporter import exportar_por_posicion
    from .orientation import orientacion_de

    import pymupdf as fitz  # PyMuPDF

    paginas_ocr = sorted(analisis.paginas_ocr)

    # Altos de página en puntos + texto nativo de páginas no-OCR (una sola
    # apertura del PDF).
    doc_pdf = fitz.open(str(ruta))
    try:
        total = doc_pdf.page_count
        alto_por_pagina = {i: doc_pdf[i].rect.height for i in range(total)}
        texto_nativo = {
            i: doc_pdf[i].get_text().strip()
            for i in range(total)
            if (i + 1) not in paginas_ocr
        }
    finally:
        doc_pdf.close()

    segmentos_md: list[str] = []
    boxes_fusion: list[Box] = []

    for num in range(1, total + 1):  # 1-indexado, en orden de página
        if num in paginas_ocr:
            # Página escaneada/corrupta: render→imagen → pipeline de imagen.
            img = render_pdf_a_jpg(ruta, pagina=num - 1, dpi=300)
            try:
                doc_pag = procesar_imagen(
                    img,
                    converter=converter,
                    modo_motor=modo_motor,
                    tipo_entrada="pdf_escaneado",
                    tipo_entrada_origen=tipo_entrada,
                )
            finally:
                img.unlink(missing_ok=True)

            md_pag = (doc_pag.markdown or "").strip()
            if md_pag:
                segmentos_md.append(md_pag)

            # Fusionar boxes con offset de página (center_y compensado).
            offset = alto_por_pagina.get(num - 1, 0.0) * (num - 1)
            for b in doc_pag.boxes or []:
                if b.bbox is None:
                    continue
                boxes_fusion.append(
                    Box(
                        texto=b.texto,
                        center_x=b.center_x,
                        center_y=b.center_y + offset,
                        bbox=(
                            b.bbox[0],
                            b.bbox[1] + offset,
                            b.bbox[2],
                            b.bbox[3] + offset,
                        ),
                        es_tabla=b.es_tabla,
                        markdown_tabla=b.markdown_tabla,
                    )
                )
        else:
            # Página apta/vacía: texto nativo por página (F1, PyMuPDF).
            texto = texto_nativo.get(num - 1, "")
            if texto:
                segmentos_md.append(texto)

    # Markdown concatenado en orden de página.
    markdown_concatenado = "\n\n".join(segmentos_md)
    if markdown_concatenado and not markdown_concatenado.endswith("\n"):
        markdown_concatenado += "\n"

    doc = ProcessedDocument(
        tipo_entrada=tipo_entrada,
        ruta=str(ruta),
        markdown=markdown_concatenado or "No se encontraron textos.\n",
        boxes=boxes_fusion,
    )
    doc.orientacion = orientacion_de(doc.boxes)
    doc.motor = "docling"
    doc.calidad = {
        "routing": "parcial",
        "paginas_aptas": sorted(analisis.paginas_aptas),
        "paginas_ocr": paginas_ocr,
        "nota": (
            "PDF mixto: páginas OCR render→imagen (PROC.md §5) + texto nativo "
            "por página; fusión fina como mejora (cierre docs/T-105)."
        ),
    }
    if docling_raw:
        # Opción A en PDF parcial/mixto (subplan F1 §2.5): el crudo pleno de
        # Docling no existe en esta ruta (páginas aptas usan PyMuPDF, no
        # Docling por página); se mantiene la concatenación actual y se deja
        # constancia de la no-aplicación en calidad.
        doc.calidad["docling_raw"] = "parcial_no_aplica"
        doc.calidad["salida"] = "concatenado_parcial"
        doc.calidad["nota_raw"] = (
            "docling_raw=True: el crudo pleno de Docling no aplica a un PDF "
            "mixto/parcial (las páginas aptas usan PyMuPDF, no Docling por "
            "página); se mantiene el markdown concatenado actual."
        )

    # Si no hay tablas en el crudo y hay boxes, ordenar por posición (E-DOC-3).
    if "|" not in markdown_concatenado and boxes_fusion:
        doc.markdown = exportar_por_posicion(boxes_fusion, doc.orientacion)
    return doc


# ---------------------------------------------------------------------------
# Orquestación principal
# ---------------------------------------------------------------------------

def procesar_documento(
    origen: str | Path,
    *,
    converter: Any = None,
    modo_motor: str = "auto",
    docling_raw: bool = False,
) -> "ProcessedDocument":
    """Procesa un documento a ``ProcessedDocument`` (F1 / T-105/ORQ, E-DOC).

    Es la entrada de ``api.process`` y el corazón de la orquestación de F1.
    Implementa el algoritmo de ``docs/ideas/docling.md`` + doc 03 §4.1:

      1. ``Path(origen)`` y validación de existencia (``FileNotFoundError``).
      2. ``detectar`` (T-101, E-DOC-1) → tipo de entrada.
      3. Ruta según el tipo:

         - ``no_soportado`` → :class:`DocumentoNoProcesableError` (rechazo/
           reencolado, doc 03 §4.1), con el ``motivo`` de T-101 en el mensaje.
         - ``pdf_texto`` / ``pdf_escaneado`` (extensión ``.pdf``) → se refina
           con ``analizar_pdf`` (routing por página, T-105/EXT):
             * ``apto`` → texto nativo directo (Docling sobre el PDF).
             * ``requiere_ocr`` → render→imagen→OCR (aunque T-101 dijera
               ``pdf_texto``, p. ej. un PDF vacío o corrupto: el routing manda,
               PROC.md §5).
             * ``parcial`` → ``_procesar_pdf_parcial`` (mezcla por página).
             * ``error`` → ``DocumentoNoProcesableError`` con el ``resumen``.
         - ``imagen`` → ``procesar_imagen`` sobre el archivo.
         - ``pdf_escaneado`` → ``render_pdf_a_jpg`` + ``procesar_imagen`` sobre
           el JPG; el temporal se borra en ``finally``.
         - ``office`` / ``texto`` → Docling directo (texto nativo; Docling
           convierte docx/xlsx/pptx/html/md/txt).
      4. En todos los casos el ``ProcessedDocument`` queda con su ``markdown``
         (por defecto ordenado E-DOC-3, o el crudo de Docling si
         ``docling_raw=True``), ``orientacion`` (T-103), ``motor="docling"`` y
         ``calidad`` según la ruta.

    ``docling_raw`` (Opción A, subplan F1 §2.5): el keyword se propaga a las
    subrutinas que corresponda:

      - ``imagen`` / ``pdf_escaneado`` (vía render→imagen) /
        ``pdf_texto`` apto / ``office`` / ``texto`` → se expone el **crudo de
        Docling** (``conv.convert`` ya lo dejó en ``markdown``; se captura
        antes de pisarlo) sin el reordenado por posición del exportador
        (equivalente a ``v1/run_raw.py``). Marca en ``calidad``
        ``docling_raw: True``.
      - ``parcial`` (PDF mixto) → el crudo pleno no existe (las páginas aptas
        usan PyMuPDF); se mantiene la concatenación actual y se anota
        ``docling_raw: "parcial_no_aplica"`` en ``calidad``.
      - default ``False`` → comportamiento actual (política combinada E-DOC-3,
        contrato F2/F3/F4).

    Decisión de diseño: ``routing`` se aplica SOLO cuando ``detectar`` devuelve
    un PDF (``pdf_texto``/``pdf_escaneado`` y extensión ``.pdf``) para refinar
    el veredicto binario de T-101 (cubre PDFs mixtos que el detector clasifica
    como ``pdf_texto`` pero el routing ve ``parcial``/``requiere_ocr``).
    ``type_detector`` es binario y ``routing`` es por página (T-105/EXT).

    Argumentos:
        origen: ruta al archivo (pdf/imagen/office/txt/...).
        converter: adaptador Docling inyectable (tests; evita Docling real).
        modo_motor: modo de selección de motor para imágenes (``"auto"``).
        docling_raw: si True, ``ProcessedDocument.markdown`` es el crudo de
            Docling (sin reordenar por posición) en documento completo
            (imagen/PDF apto/office/texto y PDF escaneado vía imagen
            renderizada); en PDF mixto/parcial se anota en ``calidad``
            (no aplica pleno). Default False = política combinada actual.

    Devuelve:
        :class:`ProcessedDocument` completo.

    Lanza:
        ``FileNotFoundError`` si la ruta no existe.
        ``voucherflow.api.DocumentoNoProcesableError`` si el formato no es
        soportado, el PDF no pudo analizarse o la imagen no superó el gate.
    """
    from ..models.docling import DoclingConverter
    from .routing import VeredictoPdf, analizar_pdf
    from .type_detector import detectar

    ruta = Path(origen)

    # 1. Validación de existencia.
    if not ruta.exists():
        raise FileNotFoundError(f"No existe el archivo a procesar: {ruta}")
    if ruta.is_dir():
        raise ValueError(f"Se esperaba un archivo, no un directorio: {ruta}")

    # 2. Detección de tipo de entrada (T-101, E-DOC-1).
    te = detectar(ruta)
    tipo = te.tipo

    # 3a. Formato no soportado → rechazo con excepción de dominio.
    if tipo == "no_soportado":
        raise _error_no_procesable(
            f"Formato no soportado / rechazo-reencolado: {te.motivo}"
        )

    # 3b. PDF: refinar con routing por página (PROC.md §5).
    if tipo in ("pdf_texto", "pdf_escaneado") and ruta.suffix.lower() == ".pdf":
        analisis = analizar_pdf(ruta)
        if analisis.veredicto == VeredictoPdf.error:
            raise _error_no_procesable(
                f"No se pudo analizar el PDF '{ruta.name}': {analisis.resumen}"
            )
        if analisis.veredicto == VeredictoPdf.apto:
            # Texto nativo en todas las páginas: pdftotext --layout preferido
            # (layout de columnas), Docling directo como fallback (A1 revertida;
            # ver _procesar_pdf_apto).
            return _procesar_pdf_apto(
                ruta, converter=converter, analisis=analisis,
                docling_raw=docling_raw,
            )
        if analisis.veredicto == VeredictoPdf.requiere_ocr:
            # Sin texto nativo aprovechable (o detector dijo pdf_texto pero el
            # routing ve OCR, p. ej. PDF vacío): render→imagen→OCR.
            img = render_pdf_a_jpg(ruta, pagina=0, dpi=300)
            try:
                return procesar_imagen(
                    img,
                    converter=converter,
                    modo_motor=modo_motor,
                    tipo_entrada="pdf_escaneado",
                    tipo_entrada_origen=tipo,
                    ruta_publica=ruta,
                    docling_raw=docling_raw,
                )
            finally:
                img.unlink(missing_ok=True)
        if analisis.veredicto == VeredictoPdf.parcial:
            return _procesar_pdf_parcial(
                ruta, analisis, converter=converter, modo_motor=modo_motor,
                tipo_entrada=tipo,  # pdf_texto (T-101); la nota va en calidad
                docling_raw=docling_raw,
            )

    # 3c. PDF escaneado (T-101 sin routing claro o routing no aplicable):
    #     render→imagen→OCR (PROC.md §5).
    if tipo == "pdf_escaneado":
        img = render_pdf_a_jpg(ruta, pagina=0, dpi=300)
        try:
            return procesar_imagen(
                img,
                converter=converter,
                modo_motor=modo_motor,
                tipo_entrada="pdf_escaneado",
                tipo_entrada_origen=tipo,
                ruta_publica=ruta,
                docling_raw=docling_raw,
            )
        finally:
            img.unlink(missing_ok=True)

    # 3d. Imagen → pipeline de imagen (E-DOC-2).
    if tipo == "imagen":
        return procesar_imagen(
            ruta,
            converter=converter,
            modo_motor=modo_motor,
            tipo_entrada="imagen",
            tipo_entrada_origen=tipo,
            docling_raw=docling_raw,
        )

    # 3e. Office / texto → texto nativo (Docling convierte docx/xlsx/pptx/html/md/txt).
    if tipo in ("office", "texto"):
        return _procesar_texto_nativo(ruta, tipo, converter=converter, docling_raw=docling_raw)

    # 3f. Seguridad: cualquier tipo no cubierto arriba se rechaza.
    raise _error_no_procesable(
        f"Tipo de entrada '{tipo}' no manejado por la orquestación de F1: {te.motivo}"
    )


__all__ = [
    "procesar_documento",
    "procesar_imagen",
    "render_pdf_a_jpg",
    "_procesar_pdf_apto",
]

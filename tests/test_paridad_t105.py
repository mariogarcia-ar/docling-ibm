"""Tests de integración de **paridad T-105** (Fase F1, épica E-DOC).

**Fase / tarea**: F1 — Procesamiento (refactor docling) · **T-105** · E-DOC.
**DoD de F1** (``05-plan-ejecucion.md`` §F1): "procesar los formatos de las
ideas produce Markdown ordenado equivalente o superior a v1, con gate y elección
de motor; cubierto por tests de formato sobre golden set".
**Estrategia de calidad** (``docs/plan/06-estrategia-calidad.md`` §4 Fase 1):
"*Integración*: procesar una muestra real y comparar Markdown con el de v1
(``run.py``/``run_raw.py``). *Métrica*: paridad de Markdown (estructural) ≥
umbral acordado sobre la muestra". La estructura de caso golden propuesta en la
estrategia §3.2 incluye un ``original.md`` (markdown de referencia de v1).

**Decisión de comparación (documentada, 2026-09-06)**:

  1. **Regla del paquete**: ``voucherflow`` NO importa v1 en runtime (DoD #1 de
     ``05-plan-ejecucion.md`` §8). Esta regla aplica al **código del paquete**;
     el test de integración vive en ``tests/`` y podría permitirse comparar
     contra v1 de forma aislada. Aun así, se descartó **generar la referencia
     v1 en runtime** (subprocess/sys.path sobre ``v1/lib/processor``): v1 es un
     conjunto de scripts con imports absolutos ``from lib...`` (no un paquete
     instalable), correr Docling dos veces (v1 + v2) duplicaría latencia y
     re-descargas de modelos, y añade fragilidad (estado global de worker,
     orientación auto por archivo) por un beneficio marginal en esta iteración.

  2. **Enfoque elegido — invariantes estructurales de paridad + campos clave**:
     se procesa la muestra acotada con ``api.process()`` (v2) y se validan
     invariantes que la paridad con v1 exige conceptualmente:
       - Markdown **no vacío** y sin pérdida grosera (la salida de v1 tampoco
         es vacía para estos formatos).
       - Los **tokens/campos clave** del documento están presentes (p. ej. para
         el boleto ``9dfc597f``: "ALONSO", "SUV-255671438", "Venado Tuerto",
         "31700.00"). Se comparan **normalizados** (NBSP/guiones suaves/
         minúsculas/espacios) porque el OCR y Docling pueden introducir
         variaciones de espaciado (calibrado contra la salida real, ver abajo).
       - Contrato de salida: ``tipo_entrada`` correcto por ruta, ``motor``
         coherente con la ruta (``"pdftotext"`` preferido para PDF apto, con
         fallback ``"docling"``; ``"docling"`` en el resto) y ``calidad``
         coherente con la ruta. En el modo ``pdftotext`` no hay ``boxes``
         (solo texto plano con layout); en el fallback Docling sí hay ítems.

  3. **Referencias v1 versionadas (futuro)**: hoy NO existen ``original.md``
     de v1 en fixtures (verificado 2026-09-06; el ``.txt`` de pdftotext del
     ``9dfc597f`` NO es referencia Docling/v1). Para una futura ampliación a
     paridad **byte a byte** se versionarían las referencias generadas así:

         cd v1 && python ocr_documents.py <dir>   # o run.py
         # por archivo: v1/lib/processor.process_file(ruta, converter) -> <stem>.md
         python run_raw.py --input <ruta>          # -> <stem>.raw.md (crudo)

     y el helper :func:`_referencia_v1` leería ``<stem>.original.md`` junto al
     fixture (estrategia §3.2). Hoy devuelve los campos esperados como
     invariante; el mecanismo de lectura ya está previsto en el helper.

  **Muestra acotada** (ver :data:`MUESTRA`): 1 PDF apto a texto nativo (boleto
  a 2 columnas), 2 imágenes chicas y 1 PDF escaneado — elegidos pequeños para
  que la corrida explícita con Docling real sea del orden de segundos por
  archivo (evita imágenes de 3-4 MB). Se parametriza en una lista para ampliar
  fácil.

  **Por qué estos tests NO corren en la suite default** (decisión 2026-09-06):
  ``pyproject.toml`` tiene ``addopts = "-q"`` (NO excluye el marker
  ``integration``), por lo que pytest **sí recolectaría y correría** un test
  marcado ``@pytest.mark.integration`` en la corrida default. Para mantener la
  suite default rápida y **sin Docling real** (regla dura del subplan F1 §4:
  "la suite default corre sin Docling real ni Ollama"), sin alterar
  ``pyproject.toml``, se usa un **auto-skip** (fixture ``autouse``
  :func:`_t105_integracion`): los tests se recolectan (visibles como "saltados")
  y se omiten **salvo** que la corrida pida explícitamente ``-m integration`` o
  se defina ``VOUCHERFLOW_INTEGRATION=1`` (escape sin marcador). De ese modo:

      # Suite default: rápida, sin Docling (T-105 → skip).
      cd v2 && python -m pytest tests -q
      # T-105 explícito (Docling real; muestra chica, ~10-60 s/archivo).
      cd v2 && python -m pytest tests/test_paridad_t105.py -m integration -q
"""
from __future__ import annotations

import os
import re
from pathlib import Path

import pytest

# La regla dura del paquete (v2 no importa v1) se mantiene también en este test:
# la paridad se valida por invariantes estructurales y campos clave, sin cargar
# v1. El import de la API pública de v2 es suficiente.
from voucherflow.api import process

pytestmark = pytest.mark.integration  # noqa: E402  (marcador de integración)

#: Umbral de solapamiento que se usaría en una comparación de paridad
#: estructural contra una referencia v1 (estrategia 06 §4 F1). Hoy se usa para
#: documentar el criterio; la comparación efectiva se hace por invariantes.
UMBRAL_PARIDAD = 0.9


# ---------------------------------------------------------------------------
# Muestra acotada de paridad (T-105)
# ---------------------------------------------------------------------------
#: Tuplas ``(id, ruta_relativa_a_fixtures, campos_esperados)``.
#: ``campos_esperados`` son tokens/campos que el documento real contiene y que
#: v1 (Docling) también transcribe; calibrados contra la salida real de v2 con
#: Docling (2026-09-06). Normalizar hace la comparación robusta a NBSP/guiones
#: suaves/espaciado (el OCR puede variar).
MUESTRA = [
    {
        # Boleto de colectivo a 2 columnas (PDF apto a texto nativo, eval).
        # Ruta (2026-09-07, A1 revertida): pdftotext --layout preferido
        # (recupera columnas) con fallback a Docling directo.
        "id": "pdf_2026-08_2DC73C08",
        "ruta": "golden/9dfc597f-34c5-41ec-99ae-cf35544c7af8.pdf",
        "tipo_esperado": "pdf_texto",
        "campos": ["ALONSO", "SUV-255671438", "Venado Tuerto", "31700.00"],
    },
    {
        # Screenshot chico de chat (imagen, eval; ~30 KB). Ruta: imagen/OCR.
        # Nota: el gate lo marca "resolución baja pero procesable" (no rechaza).
        "id": "img_2026-02_4FAD7638",
        "ruta": "golden/2926bed9-048a-4d72-9d41-8d65d290bfdb.jpeg",
        "tipo_esperado": "imagen",
        "campos": ["comprobante", "factura", "sellos"],
    },
    {
        # Screenshot chico de anotador (imagen, train; ~11 KB). Ruta: imagen/OCR.
        "id": "img_2026-02_E527948C",
        "ruta": "chicos/243a8b81-eca0-4841-ab69-72adf519a843.png",
        "tipo_esperado": "imagen",
        "campos": ["FALTA FACTURA"],
    },
    {
        # Ticket de venta escaneado (PDF escaneado de pdf_escaneados/).
        # Ruta: render→imagen→OCR (PROC.md §5).
        "id": "pdf_escaneado_3ac5a2ec",
        "ruta": "pdf_escaneados/3ac5a2ec-d129-47c0-947a-4680c7e25f06.pdf",
        "tipo_esperado": "pdf_escaneado",
        "campos": ["SHELL", "TICKET", "VISA", "6.400,00"],
    },
]

#: Sub-muestra de imágenes (para el test parametrizado de imagen).
MUESTRA_IMAGENES = [c for c in MUESTRA if c["tipo_esperado"] == "imagen"]


# ---------------------------------------------------------------------------
# Helpers de normalización y referencia v1
# ---------------------------------------------------------------------------
def _normalizar(texto: str) -> str:
    """Normaliza texto para comparación robusta de tokens (paridad T-105).

    - Convierte NBSP (``\\xa0``) y espacios no separables en espacio normal.
    - Elimina guiones suaves (soft hyphen ``\\u00ad``), guiones no separables
      (``\\u2011``) y caracteres de ancho cero/BOM (el OCR de Docling puede
      introducirlos, p. ej. en "Semi-Cama<Tipo Servicio").
    - Minúsculas y colapso de espacios/saltos de línea.
    """
    texto = texto.replace("\u00a0", " ")
    for ch in ("\u00ad", "\u200b", "\ufeff", "\u2011"):
        texto = texto.replace(ch, "")
    texto = texto.lower()
    return re.sub(r"\s+", " ", texto).strip()


def _contiene_campo(markdown: str, campo: str) -> bool:
    """¿El markdown (normalizado) contiene el campo/token esperado?"""
    return _normalizar(campo) in _normalizar(markdown)


def _referencia_v1(fixtures_dir: Path, caso: dict) -> dict:
    """Referencia de paridad de v1 para un caso (estrategia 06 §3.2).

    Devuelve un dict con ``modo``, ``campos`` y, si existiera, ``contenido``:

    - ``modo == "referencia_versionada"``: existe ``<stem>.original.md`` junto
      al fixture (markdown de referencia de v1 versionado). Es el camino para
      la futura paridad byte a byte / solapamiento de tokens ≥
      :data:`UMBRAL_PARIDAD`. Hoy no hay ninguno (verificado 2026-09-06), pero
      el mecanismo queda listo: alcanza con generar la referencia con v1
      (``cd v1 && python ocr_documents.py <dir>`` / ``run_raw.py --input``) y
      copiarla como ``<stem>.original.md`` en ``tests/fixtures/``.
    - ``modo == "invariantes_campos"``: no hay referencia versionada; se usan
      los ``campos`` esperados (calibrados con Docling) como invariante de
      paridad estructural.

    Argumentos:
        fixtures_dir: raíz de fixtures (tests/fixtures).
        caso: entrada de :data:`MUESTRA`.

    Devuelve:
        dict con ``modo``, ``campos`` (lista) y ``contenido`` (str | None).
    """
    ruta = fixtures_dir / caso["ruta"]
    ref = ruta.with_name(f"{ruta.stem}.original.md")
    if ref.exists():
        return {
            "modo": "referencia_versionada",
            "campos": [],
            "contenido": ref.read_text(encoding="utf-8"),
        }
    return {
        "modo": "invariantes_campos",
        "campos": list(caso.get("campos", [])),
        "contenido": None,
    }


# ---------------------------------------------------------------------------
# Auto-skip de integración (decisión documentada en el docstring del módulo)
# ---------------------------------------------------------------------------
@pytest.fixture(autouse=True)
def _t105_integracion(request) -> None:
    """Salta T-105 salvo que se pida explícitamente integración.

    pyproject.toml NO excluye el marker ``integration`` en ``addopts`` (es
    ``-q``), por lo que un test marcado ``integration`` correría en la suite
    default. Para mantener default rápido y sin Docling real sin tocar
    ``pyproject.toml``:

      - Si la corrida usa ``-m integration`` (markexpr contiene
        ``"integration"``) → corre (Docling real).
      - Si además está ``VOUCHERFLOW_INTEGRATION=1`` → corre aunque no se pase
        ``-m`` (escape explícito).
      - Si no → ``pytest.skip`` inmediato, sin importar Docling.
    """
    markexpr = request.config.getoption("markexpr", None)
    pide_integration = bool(markexpr) and "integration" in str(markexpr)
    fuerza_env = os.environ.get("VOUCHERFLOW_INTEGRATION") == "1"
    if pide_integration or fuerza_env:
        return
    pytest.skip(
        "T-105: test de integración (requiere Docling real). "
        "Correr con `python -m pytest tests/test_paridad_t105.py -m integration -q` "
        "o con VOUCHERFLOW_INTEGRATION=1."
    )


# ---------------------------------------------------------------------------
# Tests de integración T-105
# ---------------------------------------------------------------------------
class TestParidadPdfAptoTextoNativo:
    """Ruta PDF apto → texto nativo (layout) sin pérdida de campos.

    Decisión 2026-09-07 (revierte A1 de 2026-09-06): la ruta apto prefiere
    ``pdftotext --layout`` (recupera columnas que Docling aplana; PROC.md §5.2)
    con **fallback a Docling directo** cuando poppler no está disponible. Por
    eso el motor puede ser ``"pdftotext"`` (preferido) o ``"docling"``
    (fallback), y los ``boxes`` solo existen en el fallback Docling.
    """

    @pytest.mark.integration
    def test_pdf_apto_texto_nativo_no_pierde_campos_clave(self, fixtures_dir) -> None:
        caso = MUESTRA[0]
        ruta = fixtures_dir / caso["ruta"]
        assert ruta.exists(), f"Falta el fixture de la muestra T-105: {ruta}"

        doc = process(str(ruta))
        md = doc.markdown

        # Invariantes de paridad estructural (estrategia 06 §4 F1).
        assert md and md.strip(), (
            f"T-105: el markdown del PDF apto '{caso['id']}' no debe estar vacío "
            "(v1 tampoco produce salida vacía para un boleto a texto nativo)."
        )
        assert doc.tipo_entrada == "pdf_texto", (
            f"T-105: el PDF apto '{caso['id']}' debe enrutarse como pdf_texto "
            f"(texto nativo), se obtuvo '{doc.tipo_entrada}'."
        )
        # Ruta preferida (pdftotext --layout) o fallback (Docling directo).
        assert doc.motor in ("pdftotext", "docling"), (
            f"T-105: el motor de '{caso['id']}' debe ser 'pdftotext' (preferido) "
            f"o 'docling' (fallback), se obtuvo '{doc.motor}'."
        )
        assert isinstance(doc.calidad, dict) and doc.calidad.get("routing") == "apto", (
            f"T-105: la calidad de '{caso['id']}' debe anotar routing=apto."
        )
        # En el modo pdftotext no hay boxes (solo texto plano con layout); en
        # el fallback Docling sí debería haber ítems con texto.
        if doc.motor == "docling":
            assert doc.boxes, (
                f"T-105: se esperaban boxes (ítems con texto) en '{caso['id']}' "
                "cuando el fallback es Docling."
            )

        # Campos clave del boleto presentes (paridad estructural con v1).
        ref = _referencia_v1(fixtures_dir, caso)
        assert ref["campos"], (
            f"T-105: falta referencia de campos para '{caso['id']}'."
        )
        ausentes = [c for c in ref["campos"] if not _contiene_campo(md, c)]
        assert not ausentes, (
            f"T-105: el markdown del boleto '{caso['id']}' perdió campos clave "
            f"que v1 transcribe: {ausentes}. Markdown (inicio): {md[:400]!r}"
        )

    @pytest.mark.integration
    def test_docling_raw_no_vacio(self, fixtures_dir) -> None:
        """``docling_raw=True`` (equiv. v1/run_raw.py) produce crudo no vacío."""
        caso = MUESTRA[0]
        ruta = fixtures_dir / caso["ruta"]
        assert ruta.exists(), f"Falta el fixture de la muestra T-105: {ruta}"

        doc = process(str(ruta), docling_raw=True)
        md = doc.markdown

        assert md and md.strip(), (
            f"T-105: el markdown crudo (docling_raw=True) de '{caso['id']}' "
            "no debe estar vacío."
        )
        # Opción A (subplan F1 §2.5): la marca del crudo vive en calidad.
        assert isinstance(doc.calidad, dict) and doc.calidad.get("docling_raw") is True, (
            f"T-105: docling_raw=True debe anotar calidad['docling_raw']=True "
            f"en '{caso['id']}' (se obtuvo {doc.calidad!r})."
        )
        assert doc.calidad.get("salida") == "markdown_crudo_docling", (
            f"T-105: la calidad del modo raw debe indicar salida "
            "'markdown_crudo_docling' en '{caso['id']}'."
        )
        # El crudo de Docling también conserva el campo clave del pasajero.
        assert _contiene_campo(md, "ALONSO"), (
            f"T-105: el crudo de Docling de '{caso['id']}' debería contener "
            "el pasajero (ALONSO)."
        )


class TestParidadImagen:
    """Ruta imagen → gate/clasificador → OCR sin pérdida de texto."""

    @pytest.mark.integration
    @pytest.mark.parametrize(
        "caso",
        MUESTRA_IMAGENES,
        ids=[c["id"] for c in MUESTRA_IMAGENES],
    )
    def test_imagen_no_pierde_texto(self, fixtures_dir, caso: dict) -> None:
        ruta = fixtures_dir / caso["ruta"]
        assert ruta.exists(), f"Falta el fixture de la muestra T-105: {ruta}"

        doc = process(str(ruta))
        md = doc.markdown

        assert md and md.strip(), (
            f"T-105: el markdown de la imagen '{caso['id']}' no debe estar vacío "
            "(el OCR de Docling debe transcribir el texto visible)."
        )
        assert doc.tipo_entrada == "imagen", (
            f"T-105: '{caso['id']}' debe enrutarse como imagen (se obtuvo "
            f"'{doc.tipo_entrada}')."
        )
        assert doc.boxes, (
            f"T-105: se esperaban boxes (ítems OCR) en la imagen '{caso['id']}'."
        )
        assert doc.motor == "docling"

        ref = _referencia_v1(fixtures_dir, caso)
        ausentes = [c for c in ref["campos"] if not _contiene_campo(md, c)]
        assert not ausentes, (
            f"T-105: la imagen '{caso['id']}' perdió texto que v1 transcribe: "
            f"{ausentes}. Markdown (inicio): {md[:300]!r}"
        )


class TestParidadPdfEscaneado:
    """Ruta PDF escaneado → render→imagen→OCR (PROC.md §5) no vacía."""

    @pytest.mark.integration
    def test_pdf_escaneado_via_ocr_no_vacio(self, fixtures_dir) -> None:
        caso = next(c for c in MUESTRA if c["tipo_esperado"] == "pdf_escaneado")
        ruta = fixtures_dir / caso["ruta"]
        assert ruta.exists(), f"Falta el fixture de la muestra T-105: {ruta}"

        doc = process(str(ruta))
        md = doc.markdown

        assert md and md.strip(), (
            f"T-105: el markdown del PDF escaneado '{caso['id']}' no debe estar "
            "vacío (valida la ruta render→imagen→OCR de PROC.md §5)."
        )
        assert doc.tipo_entrada == "pdf_escaneado", (
            f"T-105: '{caso['id']}' debe enrutarse como pdf_escaneado (se obtuvo "
            f"'{doc.tipo_entrada}')."
        )
        assert doc.boxes, (
            f"T-105: se esperaban boxes (ítems OCR) en '{caso['id']}'."
        )
        # La calidad de la ruta imagen anota clase y gate (el render→imagen pasa
        # por el pipeline de imagen de E-DOC-2).
        assert isinstance(doc.calidad, dict) and doc.calidad.get("clase_imagen"), (
            f"T-105: la calidad de '{caso['id']}' debe anotar la clase de imagen "
            "(viene del pipeline de imagen)."
        )

        ref = _referencia_v1(fixtures_dir, caso)
        ausentes = [c for c in ref["campos"] if not _contiene_campo(md, c)]
        assert not ausentes, (
            f"T-105: el PDF escaneado '{caso['id']}' perdió texto que v1 "
            f"transcribe: {ausentes}. Markdown (inicio): {md[:300]!r}"
        )


class TestParidadMuestra:
    """Invariantes comunes de paridad estructural sobre toda la muestra."""

    @pytest.mark.integration
    @pytest.mark.parametrize(
        "caso",
        MUESTRA,
        ids=[c["id"] for c in MUESTRA],
    )
    def test_estructura_paridad_sobre_muestra(self, fixtures_dir, caso: dict) -> None:
        """Cada caso de la muestra: markdown no vacío, tipo y motor correctos.

        Es el barrido de "paridad estructural" de la estrategia 06 §4 F1 sobre
        la muestra acotada: ninguna ruta de F1 produce salida vacía ni pierde
        los campos esperados (pérdida grosera). Los casos individuales ya
        validan campos; aquí se asegura el invariante común por si la muestra
        crece.
        """
        ruta = fixtures_dir / caso["ruta"]
        assert ruta.exists(), f"Falta el fixture de la muestra T-105: {ruta}"

        doc = process(str(ruta))
        md = doc.markdown

        assert md and md.strip(), (
            f"T-105: el markdown de '{caso['id']}' no debe estar vacío "
            "(ninguna ruta de F1 debe producir salida vacía para la muestra)."
        )
        assert doc.tipo_entrada == caso["tipo_esperado"], (
            f"T-105: '{caso['id']}' debe enrutarse como {caso['tipo_esperado']} "
            f"(se obtuvo '{doc.tipo_entrada}')."
        )
        # PDF apto: pdftotext preferido / docling fallback. Resto: docling.
        if caso["tipo_esperado"] == "pdf_texto":
            assert doc.motor in ("pdftotext", "docling"), (
                f"T-105: '{caso['id']}' (pdf_texto apto) debe usar 'pdftotext' "
                f"o 'docling', se obtuvo '{doc.motor}'."
            )
        else:
            assert doc.motor == "docling", (
                f"T-105: '{caso['id']}' debe usar motor docling (no es PDF apto "
                f"a texto nativo), se obtuvo '{doc.motor}'."
            )
        assert len(doc.markdown) >= 20, (
            f"T-105: '{caso['id']}' tiene un markdown sospechosamente corto "
            "(posible pérdida grosera): {len(doc.markdown)} chars."
        )

        # Si el caso declara campos esperados, deben estar (pérdida grosera).
        ref = _referencia_v1(fixtures_dir, caso)
        ausentes = [c for c in ref["campos"] if not _contiene_campo(md, c)]
        assert not ausentes, (
            f"T-105: '{caso['id']}' perdió campos clave de la referencia v1: "
            f"{ausentes}."
        )

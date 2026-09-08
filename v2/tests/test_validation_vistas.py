"""Tests de preparación de vistas (F2 / T-201, épica E-QWE-1).

Validan ``preparar_vista_rapida`` y el contrato :class:`VistaPreparada` (vista
barata del doble paso qween): la vista rápida es de calidad baja/moderada (no
alta), distinguible de las vistas de revisión/fiel (gradiente de calidad), se
deriva sin Docling/Ollama reales y expone trazabilidad (tipo ``rapida``,
coherente con ``ValidationResult.vista_usada``).

Reglas duras (F2-subplan §4): no se rompe el contrato congelado de F0
(``ValidationResult``/``VeredictoGate``/``validar_comprobante`` siguen
exportándose y ``validar_comprobante`` sigue lanzando ``NotImplementedError``
hasta T-202). La suite default corre sin servicios reales; los fixtures de
imagen se leen con stdlib (``processing.leer_caracteristicas``, sin Pillow).
"""

from __future__ import annotations

from pathlib import Path

import pytest

from voucherflow.models.docling import Box, ProcessedDocument
from voucherflow.validation import (
    CALIDAD_POR_TIPO_VISTA,
    GRADO_CALIDAD_POR_NIVEL,
    HOOK_DEGRADACION,
    RESOLUCION_VISTA_RAPIDA_PX,
    TIPOS_TEXTO_SIN_THUMBNAIL,
    TIPOS_VISTA,
    ValidationResult,
    VeredictoGate,
    VistaPreparada,
    preparar_vista_rapida,
    validar_comprobante,
)


# ---------------------------------------------------------------------------
# Helpers: documentos mínimos y selección de fixture de imagen real
# ---------------------------------------------------------------------------

def _doc_imagen(ruta: str | Path, markdown: str = "# comprobante") -> ProcessedDocument:
    """``ProcessedDocument`` de imagen mínimo (3 posicionales + boxes, F0)."""
    return ProcessedDocument(
        "imagen",
        str(ruta),
        markdown,
        boxes=[Box("importe $100", 10.0, 20.0, (1, 2, 100, 20))],
    )


def _doc_texto(ruta: str | Path = "doc.pdf", markdown: str = "FACTURA A\nTotal: $100") -> ProcessedDocument:
    """``ProcessedDocument`` de texto nativo mínimo (pdf_texto)."""
    return ProcessedDocument("pdf_texto", str(ruta), markdown)


def _primer_fixture_imagen(fixtures_dir: Path) -> Path:
    """Devuelve la primera imagen real legible bajo tests/fixtures (determinista).

    Recorre las extensiones de imagen en orden y devuelve la primera que
    ``leer_caracteristicas`` puede leer (dimensiones > 0). No depende de un id
    concreto (los fixtures pueden regenerarse); falla con un mensaje claro si
    no hay ninguna imagen legible.
    """
    from voucherflow.processing import leer_caracteristicas

    for ext in (".jpg", ".jpeg", ".png", ".tif", ".tiff", ".bmp"):
        candidatos = sorted(fixtures_dir.rglob(f"*{ext}"))
        for p in candidatos:
            c = leer_caracteristicas(p)
            if c.ancho and c.alto:
                return p
    raise AssertionError("No se encontró una imagen real legible en tests/fixtures (T-201).")


# ---------------------------------------------------------------------------
# Vista rápida sobre ProcessedDocument (requisito 1)
# ---------------------------------------------------------------------------

class TestPrepararVistaRapida:
    def test_acepta_processdocument_y_devuelve_vista_rapida_baja(self):
        # Un ProcessedDocument mínimo (3 posicionales + boxes) alcanza para
        # derivar la vista rápida; no se necesita Docling/Ollama reales.
        doc = _doc_imagen("ruta.jpg")
        vista = preparar_vista_rapida(doc)

        assert vista.tipo_vista == "rapida", "T-201: la vista debe ser del tipo 'rapida'"
        assert vista.tipo_vista in TIPOS_VISTA
        # Calidad baja/moderada (no alta): la vista rápida es barata (E-QWE-1).
        assert vista.calidad in ("baja", "media"), vista.calidad
        assert vista.calidad != "alta", "la vista rápida no puede ser de calidad alta (E-QWE-1)"
        # Grado numérico menor que el de la vista fiel (alta=3).
        assert vista.nivel_vista < GRADO_CALIDAD_POR_NIVEL["alta"]
        # Trazabilidad: nota y vocabulario coherente con ValidationResult.vista_usada.
        assert "T-201" in vista.nota and "E-QWE-1" in vista.nota
        assert vista.tipo_vista in ("rapida", "revision")  # vocabulario de vista_usada

    def test_vista_rapida_es_para_decidir_no_para_extraer(self):
        # Semántica del doble paso (qween.md §1/§6): la vista rápida es un gate
        # de decisión; la nota y el contrato lo reflejan (hook de degradación).
        doc = _doc_imagen("ruta.jpg")
        vista = preparar_vista_rapida(doc)
        assert vista.tipo_vista == "rapida"
        assert vista.representacion  # hay un artefacto consumible
        assert vista.metadatos.get("tipo_vista_futura_fiel_no_reutiliza") is True, (
            "E-QWE-2: la futura vista fiel no reutiliza la rápida."
        )

    def test_vista_rapida_de_texto_nativo_usa_markdown(self):
        # PDF de texto nativo / office: no aplica thumbnail (F2-subplan §2.4);
        # la vista rápida es el markdown de F1 con metadatos de calidad baja.
        doc = _doc_texto(markdown="FACTURA B\nIVA 21%")
        vista = preparar_vista_rapida(doc)

        assert vista.tipo_vista == "rapida"
        assert vista.calidad == CALIDAD_POR_TIPO_VISTA["rapida"] == "baja"
        assert vista.resolucion_objetivo == 0, "texto nativo: sin resolución de píxeles"
        assert vista.ruta_imagen_original is None
        assert "FACTURA B" in vista.representacion
        assert doc.tipo_entrada in TIPOS_TEXTO_SIN_THUMBNAIL
        assert vista.metadatos.get("hook_degradacion") is None

    def test_documento_sin_ruta_ni_markdown_lanza_valueerror(self):
        # Sin representación no hay vista que derivar (T-201): error con mensaje.
        doc = ProcessedDocument("imagen", "", "")
        with pytest.raises(ValueError, match="T-201"):
            preparar_vista_rapida(doc)

    def test_se_puede_pasar_origen_explicito(self):
        # El kwarg ``origen`` permite sobreescribir la ruta del documento
        # (útil cuando la ruta viva del render de F1 difiere de doc.ruta).
        doc = _doc_texto(ruta="interno.pdf", markdown="texto")
        vista = preparar_vista_rapida(doc, origen="/tmp/otro.pdf")
        assert vista.origen == "/tmp/otro.pdf"


# ---------------------------------------------------------------------------
# Distinguibilidad: rápida vs. revisión/fiel (requisito 2)
# ---------------------------------------------------------------------------

class TestVistaDistinguibleDeMayorCalidad:
    def test_gradiente_de_calidad_baja_media_alta(self):
        # El contrato permite comparar/exponer calidad: el gradiente numérico
        # garantiza que la rápida sea menor que la de revisión y la fiel.
        assert GRADO_CALIDAD_POR_NIVEL["baja"] < GRADO_CALIDAD_POR_NIVEL["media"] < GRADO_CALIDAD_POR_NIVEL["alta"]
        assert CALIDAD_POR_TIPO_VISTA == {"rapida": "baja", "revision": "media", "fiel": "alta"}

    def test_rapida_no_reutiliza_imagen_fiel(self):
        # La vista rápida nunca puede declararse de calidad alta (la fiel de
        # T-203 sí): el contrato la distingue y evita reutilizarla (E-QWE-2).
        rapida = VistaPreparada(tipo_vista="rapida", calidad="baja", representacion="ruta.jpg")
        with pytest.raises(ValueError):
            VistaPreparada(tipo_vista="rapida", calidad="alta", representacion="ruta.jpg")
        assert rapida.nivel_vista == 1
        assert rapida.tipo_vista != "fiel"

    def test_vista_fiel_futura_es_constructible_y_distinta(self):
        # T-203 preparará la vista fiel con tipo 'fiel'/calidad 'alta'. Aquí se
        # valida que el contrato ya la distingue de la rápida (no se fusionan).
        fiel = VistaPreparada(tipo_vista="fiel", calidad="alta", representacion="ruta_fiel.jpg")
        rapida = VistaPreparada(tipo_vista="rapida", calidad="baja", representacion="ruta.jpg")
        assert fiel.tipo_vista != rapida.tipo_vista
        assert fiel.nivel_vista > rapida.nivel_vista
        assert fiel.calidad != rapida.calidad

    def test_calidad_incoherente_con_tipo_lanza_valueerror(self):
        with pytest.raises(ValueError, match="incoherente"):
            VistaPreparada(tipo_vista="revision", calidad="baja", representacion="x")
        # nivel_vista es derivado (init=False): no se puede fijar a mano.
        with pytest.raises(TypeError):
            VistaPreparada(tipo_vista="rapida", calidad="baja", representacion="x", nivel_vista=3)

    def test_tipo_vista_invalido_lanza_valueerror(self):
        with pytest.raises(ValueError, match="tipo_vista inválido"):
            VistaPreparada(tipo_vista="lenta", calidad="baja", representacion="x")


# ---------------------------------------------------------------------------
# Vista rápida sobre imagen real de fixture (requisito 3)
# ---------------------------------------------------------------------------

class TestVistaRapidaSobreFixtureReal:
    def test_se_deriva_sin_excepciones_desde_fixture_real(self, fixtures_dir):
        # La vista rápida se deriva de una imagen real de tests/fixtures sin
        # levantar excepciones y sin depender de Docling/Ollama (suite default).
        img = _primer_fixture_imagen(fixtures_dir)
        doc = _doc_imagen(img)
        vista = preparar_vista_rapida(doc)
        assert vista.tipo_vista == "rapida"
        assert vista.calidad in ("baja", "media")

    def test_anota_dimensiones_leidas_por_leer_caracteristicas(self, fixtures_dir):
        # Diseño anotativo (stub funcional, F2-subplan §3.1): cuando la entrada
        # es imagen, la vista rápida lee las dimensiones reales vía
        # processing.leer_caracteristicas (stdlib) y anota la resolución
        # reducida objetivo + factor de escala (sin remuestrear píxeles).
        from voucherflow.processing import leer_caracteristicas

        img = _primer_fixture_imagen(fixtures_dir)
        carac = leer_caracteristicas(img)
        doc = _doc_imagen(img)
        vista = preparar_vista_rapida(doc)

        assert vista.ruta_imagen_original == str(img)
        assert vista.representacion == str(img)
        # Dimensiones reales anotadas en metadatos (mismas que leer_caracteristicas).
        assert vista.metadatos["dimensiones_originales"] == (carac.ancho, carac.alto)
        # Resolución objetivo reducida (<= constante del módulo) y factor >= 1.
        assert 0 < vista.resolucion_objetivo <= RESOLUCION_VISTA_RAPIDA_PX
        assert vista.factor_escala >= 1.0
        # Hook de degradación documentado (la degradación real es futura).
        assert vista.metadatos["hook_degradacion"] == HOOK_DEGRADACION
        # La imagen original sigue siendo mayor (o igual) que el objetivo.
        lado_mayor = max(carac.ancho, carac.alto)
        assert vista.resolucion_objetivo <= lado_mayor


# ---------------------------------------------------------------------------
# F0 intacto (requisito 4): contratos congelados de validation
# ---------------------------------------------------------------------------

class TestNoRompeF0:
    def test_contratos_congelados_siguen_exportandose(self):
        # F2-subplan §4 (regla dura): validation/__init__ sigue exportando los
        # contratos de F0 (la ampliación de __all__ no los quita).
        from voucherflow import validation

        assert hasattr(validation, "ValidationResult")
        assert hasattr(validation, "VeredictoGate")
        assert hasattr(validation, "validar_comprobante")
        assert validation.ValidationResult is ValidationResult
        assert validation.VeredictoGate is VeredictoGate

    def test_validationresult_y_veredictogate_construibles(self):
        # El contrato congelado se construye igual que en F0.
        r = ValidationResult(veredicto=VeredictoGate.comprobante)
        assert r.veredicto == VeredictoGate.comprobante
        assert r.confianza_fuente == "media"
        assert r.vista_usada == "rapida"
        assert r.detalle == {}

    def test_validar_comprobante_sigue_siendo_esqueleto(self):
        # T-201 es solo la preparación de la vista rápida; el gate y la
        # orquestación son T-202/T-203. ``validar_comprobante`` debe seguir
        # lanzando NotImplementedError (congelado por test_esqueletos...).
        with pytest.raises(NotImplementedError):
            validar_comprobante("origen.jpg")

    def test_vocabulario_vista_usada_coherente(self):
        # ``VistaPreparada.tipo_vista`` usa el mismo vocabulario que
        # ``ValidationResult.vista_usada`` (rapida | revision); fiel es T-203.
        assert "rapida" in TIPOS_VISTA and "revision" in TIPOS_VISTA
        assert ValidationResult(veredicto=VeredictoGate.no_comprobante, vista_usada="revision").vista_usada == "revision"

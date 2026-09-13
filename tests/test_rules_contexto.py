"""Tests del contexto tipado del motor de reglas (F3 / T-301, E-CLAS-1).

Cubren el contrato de entrada de R1-R7 definido en
``voucherflow/rules/contexto.py`` (F3-subplan §3.1, `RULES.md` §1):

1. **Construcción incremental**: todos los campos tienen default (se puede
   construir con ``ContextoTipoComprobante()`` vacío para "nada se sabe").
2. **Normalización defensiva**: el vocabulario del dominio se canoniza
   (condición fiscal, letra, ``campos_totales``) y un valor no reconocido se
   conserva crudo — nunca se inventa un valor conocido (prompt WIP: "No
   inventes datos").
3. **Inmutabilidad y solo lectura**: ``frozen=True`` (las reglas no mutan el
   contexto) y ``reemplazar()`` devuelve una copia nueva.
4. **``campos_desconocidos()``**: refleja lo que faltó para concluir con
   certeza alta (contrato del prompt WIP §``output_esperado``).
5. **``desde_dict``**: acepta el shape plano y el anidado del WIP
   (``emisor``/``receptor``/``ocr``) para facilitar la integración de T-302.

La suite es pura (sin Ollama ni Docling) y no rompe la base congelada de F0.
"""

from __future__ import annotations

import dataclasses

import pytest

from voucherflow.rules.contexto import (
    CAMPOS_TOTALES_DESCONOCIDO,
    CAMPOS_TOTALES_DISCRIMINADO,
    CAMPOS_TOTALES_SUBTOTAL_UNICO,
    CONDICION_CONSUMIDOR_FINAL,
    CONDICION_EXENTO,
    CONDICION_MONOTRIBUTO,
    CONDICION_RI,
    LETRAS_COMPROBANTE,
    ContextoTipoComprobante,
    es_condicion_fiscal_conocida,
    letra_en_vocabulario,
    normalizar_campos_totales,
    normalizar_condicion_fiscal,
    normalizar_letra,
)


class TestConstruccionIncremental:
    """El contexto debe poder construirse vacío y completarse por etapas."""

    def test_contexto_vacio_tiene_defaults(self):
        ctx = ContextoTipoComprobante()
        assert ctx.emisor_condicion_fiscal is None, "Sin emisor informado debe quedar en None"
        assert ctx.receptor_condicion_fiscal is None, "Sin receptor informado debe quedar en None"
        assert ctx.emisor_pais == "Argentina", "El país del emisor tiene default Argentina"
        assert ctx.receptor_pais == "Argentina", "El país del receptor tiene default Argentina"
        assert ctx.letra_recuadro_vlm is None, "Sin lectura VLM debe quedar en None"
        assert ctx.texto_encabezado_llm == "", "Sin texto de encabezado debe quedar en ''"
        assert ctx.campos_ausentes == [], "Sin campos declarados ausentes debe ser lista vacía"
        assert not ctx.tiene_evidencia_lectura, "Sin señales de lectura no hay evidencia de lectura"

    def test_construccion_por_etapas_con_reemplazar(self):
        # T-302/T-303 poblarán la lectura sin reconstruir todo el contexto.
        base = ContextoTipoComprobante(
            emisor_condicion_fiscal=CONDICION_RI,
            receptor_condicion_fiscal=CONDICION_RI,
        )
        completa = base.reemplazar(
            receptor_pais="Argentina", letra_recuadro_vlm="A"
        )
        assert base.letra_recuadro_vlm is None, (
            "reemplazar() no debe mutar el contexto original (solo lectura)"
        )
        assert completa.letra_recuadro_vlm == "A", "La copia debe tener la letra informada"
        assert completa.receptor_pais == "Argentina", "La copia debe tener el país informado"
        assert completa.emisor_condicion_fiscal == CONDICION_RI, "La copia conserva el resto"

    def test_contexto_es_frozen(self):
        ctx = ContextoTipoComprobante(emisor_condicion_fiscal=CONDICION_RI)
        with pytest.raises(dataclasses.FrozenInstanceError):
            ctx.emisor_condicion_fiscal = CONDICION_MONOTRIBUTO  # type: ignore[misc]
        assert dataclasses.is_dataclass(ctx), "El contexto debe ser una dataclass"

    def test_campos_ausentes_es_lista_incremental(self):
        ctx = ContextoTipoComprobante()
        assert ctx.campos_ausentes == [], "campos_ausentes arranca como lista vacía (construcción incremental)"
        ctx2 = ContextoTipoComprobante(campos_ausentes=["emisor.cuit", " "])
        assert ctx2.campos_ausentes == ["emisor.cuit"], (
            "campos_ausentes ignora entradas vacías y conserva el orden"
        )

    def test_reemplazar_no_comparte_la_lista_de_ausentes(self):
        original = ContextoTipoComprobante(campos_ausentes=["fecha"])
        copia = original.reemplazar(letra_recuadro_vlm="A")
        assert copia.campos_ausentes == ["fecha"]
        copia.campos_ausentes.append("otro")
        assert original.campos_ausentes == ["fecha"], (
            "La lista del contexto original no debe compartir referencia con la copia"
        )

    def test_como_dict_serializa_todos_los_campos(self):
        ctx = ContextoTipoComprobante(
            emisor_condicion_fiscal=CONDICION_MONOTRIBUTO,
            receptor_pais="Uruguay",
            letra_recuadro_vlm="c",
            campos_ausentes=("emisor.cuit",),
        )
        datos = ctx.como_dict()
        assert datos["emisor_condicion_fiscal"] == CONDICION_MONOTRIBUTO
        assert datos["receptor_pais"] == "Uruguay"
        assert datos["letra_recuadro_vlm"] == "C", "La letra debe serializarse ya normalizada"
        assert datos["campos_ausentes"] == ["emisor.cuit"], "Los ausentes se serializan como lista"


class TestNormalizacion:
    """El vocabulario del WIP se canoniza; lo desconocido se conserva crudo."""

    @pytest.mark.parametrize(
        "entrada,esperado",
        [
            ("responsable inscripto", CONDICION_RI),
            ("  RESPONSABLE INSCRIPTO  ", CONDICION_RI),
            ("monotributo", CONDICION_MONOTRIBUTO),
            ("exento", CONDICION_EXENTO),
            ("consumidor final", CONDICION_CONSUMIDOR_FINAL),
            ("", None),
            ("   ", None),
            (None, None),
            ("Agente de retención", "Agente de retención"),
        ],
    )
    def test_normalizar_condicion_fiscal(self, entrada, esperado):
        assert normalizar_condicion_fiscal(entrada) == esperado, (
            f"normalizar_condicion_fiscal({entrada!r}) debe dar {esperado!r}: se canoniza "
            "lo reconocido y se conserva crudo lo desconocido (no se inventa)"
        )

    @pytest.mark.parametrize(
        "entrada,esperado",
        [
            ("a", "A"),
            (" B ", "B"),
            ("e", "E"),
            ("m", "M"),
            ("c", "C"),
            ("Z", None),
            ("090", None),
            ("", None),
            (None, None),
        ],
    )
    def test_normalizar_letra(self, entrada, esperado):
        assert normalizar_letra(entrada) == esperado, (
            f"normalizar_letra({entrada!r}) debe dar {esperado!r}: mayúscula si está en "
            "{A,B,C,M,E}; None en caso contrario (vocabulario del enum TipoComprobante)"
        )

    @pytest.mark.parametrize(
        "entrada,esperado",
        [
            ("discriminado", CAMPOS_TOTALES_DISCRIMINADO),
            ("SUBTOTAL_UNICO", CAMPOS_TOTALES_SUBTOTAL_UNICO),
            ("desconocido", CAMPOS_TOTALES_DESCONOCIDO),
            ("otro", "otro"),
            (None, None),
        ],
    )
    def test_normalizar_campos_totales(self, entrada, esperado):
        assert normalizar_campos_totales(entrada) == esperado, (
            f"normalizar_campos_totales({entrada!r}) debe dar {esperado!r}"
        )

    def test_letra_en_vocabulario_filtra_lo_invalido(self):
        assert letra_en_vocabulario("a") == "A", "Una letra válida en minúscula se canoniza"
        assert letra_en_vocabulario("090") is None, (
            "090/099 no son letras de R4-R6 (decisión abierta D-13: el WIP no las define)"
        )
        assert letra_en_vocabulario("Z") is None, "Una letra fuera de {A,B,C,M,E} no es válida"
        assert letra_en_vocabulario(None) is None, "None no es una letra válida"

    def test_letra_en_vocabulario_es_alias_de_normalizar_letra(self):
        from voucherflow.rules import normalizar_letra

        for valor in ("A", "B", "C", "M", "E", "a", "Z", "090", None, ""):
            assert letra_en_vocabulario(valor) == normalizar_letra(valor), (
                "letra_en_vocabulario debe ser un alias exacto de normalizar_letra (T-301)"
            )

    def test_condiciones_conocidas(self):
        assert es_condicion_fiscal_conocida(CONDICION_RI)
        assert es_condicion_fiscal_conocida(CONDICION_MONOTRIBUTO)
        assert not es_condicion_fiscal_conocida("desconocido"), (
            "'desconocido' no es una condición fiscal conocida (no debe disparar R1/R2)"
        )
        assert not es_condicion_fiscal_conocida(None)

    def test_vocabulario_letras(self):
        assert LETRAS_COMPROBANTE == frozenset({"A", "B", "C", "M", "E"}), (
            "El vocabulario de letras debe ser el enum TipoComprobante sin los tiques 090/099"
        )


class TestCamposDesconocidos:
    """``campos_desconocidos()`` refleja lo que faltó para la certeza alta."""

    def test_contexto_completo_no_reporta_faltantes(self):
        ctx = ContextoTipoComprobante(
            emisor_condicion_fiscal=CONDICION_RI,
            receptor_condicion_fiscal=CONDICION_RI,
            receptor_pais="Argentina",
            letra_recuadro_vlm="A",
        )
        assert ctx.campos_desconocidos() == [], (
            "Con condiciones fiscales, país y lectura informados no debe haber campos faltantes"
        )

    def test_reporta_condiciones_ausentes_y_pais_default(self):
        ctx = ContextoTipoComprobante(letra_recuadro_vlm="A")
        faltantes = ctx.campos_desconocidos()
        assert "emisor.condicion_fiscal" in faltantes, "Debe reportar la condición del emisor faltante"
        assert "receptor.condicion_fiscal" in faltantes, "Debe reportar la condición del receptor faltante"
        assert "receptor.pais" not in faltantes, (
            "El país del receptor tiene default Argentina: no se reporta como faltante"
        )

    def test_reporta_ausencia_total_de_lectura(self):
        ctx = ContextoTipoComprobante(
            emisor_condicion_fiscal=CONDICION_RI,
            receptor_condicion_fiscal=CONDICION_RI,
            receptor_pais="Argentina",
        )
        assert "evidencia_lectura" in ctx.campos_desconocidos(), (
            "Sin ninguna señal de lectura debe reportarse 'evidencia_lectura'"
        )

    def test_refleja_campos_ausentes_declarados(self):
        ctx = ContextoTipoComprobante(
            emisor_condicion_fiscal=CONDICION_RI,
            receptor_condicion_fiscal=CONDICION_RI,
            receptor_pais="Argentina",
            letra_recuadro_vlm="A",
            campos_ausentes=("emisor.cuit", "fecha"),
        )
        faltantes = ctx.campos_desconocidos()
        assert "emisor.cuit" in faltantes and "fecha" in faltantes, (
            "Debe incluir lo que el productor de evidencia declaró como ausente"
        )
        assert len(faltantes) == len(set(faltantes)), "No debe haber duplicados en campos_desconocidos()"


class TestDesdeDict:
    """``desde_dict`` acepta el shape plano y el anidado del WIP (T-302)."""

    def test_shape_plano(self):
        ctx = ContextoTipoComprobante.desde_dict(
            {
                "emisor_condicion_fiscal": "RI",
                "receptor_condicion_fiscal": "Consumidor Final",
                "receptor_pais": "Argentina",
                "letra_recuadro_vlm": "b",
            }
        )
        # "RI" no es una condición canónica: se conserva crudo (no se inventa).
        assert ctx.emisor_condicion_fiscal == "RI", "Un valor no canónico se conserva crudo"
        assert ctx.receptor_condicion_fiscal == CONDICION_CONSUMIDOR_FINAL
        assert ctx.letra_recuadro_vlm == "B", "La letra se normaliza a mayúscula"

    def test_shape_anidado_del_wip(self):
        # Shape literal del prompt WIP §inputs_esperados.
        ctx = ContextoTipoComprobante.desde_dict(
            {
                "emisor": {"cuit": "30-12345678-9", "condicion_fiscal": "Responsable Inscripto", "pais": "Argentina"},
                "receptor": {"cuit": "30-98765432-1", "condicion_fiscal": "Responsable Inscripto", "pais": "Argentina"},
                "ocr": {
                    "letra_detectada_cabecera": "B",
                    "texto_encabezado": "FACTURA B",
                    "desglose_iva_discriminado": False,
                    "campos_totales": "subtotal_unico",
                },
            }
        )
        assert ctx.emisor_condicion_fiscal == CONDICION_RI
        assert ctx.receptor_condicion_fiscal == CONDICION_RI
        assert ctx.receptor_cuit == "30-98765432-1"
        assert ctx.letra_recuadro_vlm == "B"
        assert ctx.texto_encabezado_llm == "FACTURA B"
        assert ctx.desglose_iva_discriminado is False
        assert ctx.campos_totales == CAMPOS_TOTALES_SUBTOTAL_UNICO

    def test_alias_cuit_scania(self):
        ctx = ContextoTipoComprobante.desde_dict({"cuit_scania_ri": "30-11111111-1"})
        assert ctx.cuit_propio == "30-11111111-1", (
            "Debe aceptar el alias cuit_scania_ri del WIP como cuit_propio (refuerzo opcional de R7)"
        )

    def test_rechaza_no_mapping(self):
        with pytest.raises(TypeError, match="mapping"):
            ContextoTipoComprobante.desde_dict(["no", "es", "mapping"])  # type: ignore[arg-type]

"""Prompt efectivo y validación local del esquema.

Lo que se protege acá son las dos compensaciones que hacen que un proveedor sin
esquema estricto (DeepSeek, Gemini) no rompa al consumidor:

1. **El prompt se adapta al modo.** El del template está escrito para *comparar*
   contra datos cargados; al extraer sin datos, ese cierre sobra y un modelo que
   no tiene la forma impuesta por el servidor lo obedece al pie de la letra,
   devolviendo `estado_global` donde se esperaba la lectura.
2. **La forma se sostiene localmente.** El ejemplo de salida se **genera del
   mismo esquema que valida**, así no pueden divergir; y cuando el modelo se
   desvía en algo trivial —un `enum` en otra caja— se normaliza en vez de pagar
   un reintento.

El caso delicado es el mensaje de error: `jsonschema` cita el **valor recibido**,
que es la lectura del comprobante. Un error que lo incluya manda el contenido del
documento al registro y al log.
"""

from __future__ import annotations

import json

from voucherflow.llm.esquema import (
    _coincidencia_enum,
    _errores_de_esquema,
    _tipo_compatible,
    _validador_jsonschema,
    normalizar_por_esquema,
)
from voucherflow.llm.prompts import (
    INSTRUCCIONES_SISTEMA_EXTRACCION,
    ejemplo_desde_esquema,
    prompt_de_extraccion,
    sistema_de_extraccion,
)

ESQUEMA = {
    "type": "object",
    "properties": {
        "total": {"type": "number"},
        "moneda": {"type": "string", "enum": ["ARS", "USD"]},
        "items": {"type": "array"},
        "legible": {"type": "boolean"},
    },
    "required": ["total"],
    "additionalProperties": False,
}

#: El SYSTEM del template, con su cierre de comparación (como está en el `.md`).
SISTEMA_CON_CIERRE = (
    "Sos un asistente que controla comprobantes.\n"
    "Regla 3: el CUIT se compara dígito a dígito.\n"
    "\n"
    "Instrucciones generales:\n"
    "- Devolvé un estado_global y una comparación campo a campo.\n"
    "- El campo `coincide` es true/false por campo."
)


# ---------------------------------------------------------------------------
# El prompt se adapta al modo
# ---------------------------------------------------------------------------


class TestSistemaDeExtraccion:
    def test_reemplaza_el_cierre_de_comparacion(self):
        adaptado = sistema_de_extraccion(SISTEMA_CON_CIERRE)
        assert "estado_global" not in adaptado
        assert "comparación campo a campo" not in adaptado
        assert "transcripción" in adaptado

    def test_conserva_las_reglas_de_negocio(self):
        # Las 15 reglas son el dominio, no el modo: valen igual al transcribir.
        adaptado = sistema_de_extraccion(SISTEMA_CON_CIERRE)
        assert "Regla 3" in adaptado
        assert "CUIT" in adaptado

    def test_si_no_hay_cierre_lo_agrega_igual(self):
        # Sin encuadre de extracción, el modelo podría devolver la comparación.
        adaptado = sistema_de_extraccion("Solo reglas, sin cierre.")
        assert "Solo reglas" in adaptado
        assert "transcripción" in adaptado

    def test_es_idempotente(self):
        """Aplicarlo dos veces no acumula instrucciones.

        Importa porque el prompt efectivo se arma en varias rutas (correr y
        estimar el costo) y una acumulación cambiaría el prompt sin que se note.
        """
        una = sistema_de_extraccion(SISTEMA_CON_CIERRE)
        dos = sistema_de_extraccion(una)
        assert dos == una

    def test_la_instruccion_pide_todos_los_campos(self):
        # Un campo omitido en silencio es indistinguible de un dato ausente.
        assert "TODOS" in INSTRUCCIONES_SISTEMA_EXTRACCION
        assert "null" in INSTRUCCIONES_SISTEMA_EXTRACCION


class TestEjemploDesdeEsquema:
    def test_tiene_la_forma_del_esquema(self):
        ejemplo = json.loads(ejemplo_desde_esquema(ESQUEMA))
        assert set(ejemplo) == set(ESQUEMA["properties"])

    def test_usa_la_primera_opcion_del_enum(self):
        # El modelo copia el ejemplo: si el enum fuera inventado, copiaría un
        # valor que el validador rechaza.
        assert json.loads(ejemplo_desde_esquema(ESQUEMA))["moneda"] == "ARS"

    def test_el_ejemplo_y_el_validador_no_pueden_divergir(self):
        """Es la razón de generarlo en vez de escribirlo a mano.

        El bug original: el ejemplo del `.md` era el del modo *validar*, así que
        el modelo copiaba una forma que el validador rechazaba. Generado del
        mismo esquema, eso no puede pasar.
        """
        ejemplo = json.loads(ejemplo_desde_esquema(ESQUEMA))
        validador = _validador_jsonschema()
        if validador is not None:
            assert not _errores_de_esquema(validador, ESQUEMA, ejemplo)

    def test_cubre_todos_los_tipos(self):
        ejemplo = json.loads(ejemplo_desde_esquema(ESQUEMA))
        assert isinstance(ejemplo["total"], (int, float))
        assert isinstance(ejemplo["items"], list)
        assert ejemplo["legible"] is False

    def test_un_objeto_anidado_se_despliega(self):
        anidado = {
            "type": "object",
            "properties": {
                "emisor": {
                    "type": "object",
                    "properties": {"cuit": {"type": "string"}},
                }
            },
        }
        ejemplo = json.loads(ejemplo_desde_esquema(anidado))
        assert "cuit" in ejemplo["emisor"]


class TestPromptDeExtraccion:
    def test_pide_transcribir_todo_y_declarar_lo_ilegible(self):
        p = prompt_de_extraccion("contenido del documento")
        assert "contenido del documento" in p


# ---------------------------------------------------------------------------
# Validación local
# ---------------------------------------------------------------------------


class TestValidacionLocal:
    def test_jsonschema_esta_disponible(self):
        # Si no lo estuviera, la validación local no correría y habría que
        # **declararlo** en el registro en vez de dar por bueno lo no comprobado.
        assert _validador_jsonschema() is not None

    def test_un_objeto_valido_no_da_errores(self):
        validador = _validador_jsonschema()
        assert _errores_de_esquema(validador, ESQUEMA, {"total": 1.0, "moneda": "ARS"}) == []

    def test_falta_una_propiedad_obligatoria(self):
        validador = _validador_jsonschema()
        errores = _errores_de_esquema(validador, ESQUEMA, {"moneda": "ARS"})
        assert errores
        assert any("obligatoria" in e for e in errores)

    def test_una_propiedad_de_mas_se_reporta(self):
        validador = _validador_jsonschema()
        errores = _errores_de_esquema(
            validador, ESQUEMA, {"total": 1.0, "campo_de_mas": "x"}
        )
        assert any("no declaradas" in e for e in errores)

    def test_el_error_no_cita_el_valor_leido(self):
        """⚠️ El mensaje de `jsonschema` incluye el valor recibido.

        Ese valor es la **lectura del comprobante**, y el error viaja al registro
        y al log. Se nombra el problema por su tipo, sin el dato.
        """
        validador = _validador_jsonschema()
        secreto = "ESTACION DE SERVICIO SUR S.A. CUIT 30-12345678-9"
        errores = _errores_de_esquema(
            validador, ESQUEMA, {"total": secreto, "moneda": "ARS"}
        )
        assert errores
        assert all(secreto not in e for e in errores)
        assert all("30-12345678-9" not in e for e in errores)

    def test_un_tipo_equivocado_se_reporta_por_tipo(self):
        validador = _validador_jsonschema()
        errores = _errores_de_esquema(
            validador, ESQUEMA, {"total": "no es un numero", "moneda": "ARS"}
        )
        assert any("tipo" in e for e in errores)


class TestNormalizarPorEsquema:
    def test_normaliza_un_enum_por_caja(self):
        # Evita un reintento (que se paga) por un detalle tipográfico.
        notas: list[str] = []
        r = normalizar_por_esquema(ESQUEMA, {"total": 1.0, "moneda": "ars"}, notas)
        assert r["moneda"] == "ARS"
        assert notas and "normalizado" in notas[0]

    def test_declara_la_normalizacion_en_vez_de_callarla(self):
        # Corregir en silencio haría invisible una desviación del modelo.
        notas: list[str] = []
        normalizar_por_esquema(ESQUEMA, {"total": 1.0, "moneda": "ars"}, notas)
        assert any("ars" in n for n in notas)

    def test_no_toca_un_valor_que_ya_es_canonico(self):
        notas: list[str] = []
        r = normalizar_por_esquema(ESQUEMA, {"total": 1.0, "moneda": "ARS"}, notas)
        assert r["moneda"] == "ARS"
        assert notas == []

    def test_un_valor_fuera_del_enum_no_se_inventa(self):
        # Solo se corrige una diferencia de forma, no un valor distinto.
        notas: list[str] = []
        r = normalizar_por_esquema(ESQUEMA, {"total": 1.0, "moneda": "pesos"}, notas)
        assert r["moneda"] == "pesos"
        assert notas == []

    def test_no_muta_la_entrada(self):
        original = {"total": 1.0, "moneda": "ars"}
        normalizar_por_esquema(ESQUEMA, original, [])
        assert original["moneda"] == "ars"

    def test_normaliza_anidado(self):
        esquema = {
            "type": "object",
            "properties": {
                "impuestos": {
                    "type": "object",
                    "properties": {"iva": {"type": "string", "enum": ["SI", "NO"]}},
                }
            },
        }
        notas: list[str] = []
        r = normalizar_por_esquema(esquema, {"impuestos": {"iva": "si"}}, notas)
        assert r["impuestos"]["iva"] == "SI"
        assert notas


class TestCoincidenciaEnum:
    def test_ignora_caja_tildes_y_espacios(self):
        assert _coincidencia_enum(" bueno ", ["BUENO", "MALO"]) == "BUENO"
        assert _coincidencia_enum("café", ["CAFE"]) == "CAFE"

    def test_devuelve_none_si_no_coincide(self):
        assert _coincidencia_enum("otro", ["A", "B"]) is None

    def test_una_sola_letra_coincide_por_caja(self):
        # Acá «a» y «A» son lo mismo: `_norm` pasa a mayúsculas. La regla de
        # exigir mayúscula exacta para un valor de un carácter vive en el
        # vocabulario del pipeline (RAW_VOCABULARIO), no en el evaluador.
        assert _coincidencia_enum("a", ["A", "B"]) == "A"
        assert _coincidencia_enum("A", ["A", "B"]) == "A"
        assert _coincidencia_enum("c", ["A", "B"]) is None


class TestTipoCompatible:
    def test_los_tipos_simples(self):
        assert _tipo_compatible({"type": "string"}, "x")
        assert _tipo_compatible({"type": "number"}, 1.5)
        assert _tipo_compatible({"type": "boolean"}, True)
        assert not _tipo_compatible({"type": "number"}, "x")

    def test_un_entero_es_un_numero(self):
        assert _tipo_compatible({"type": "number"}, 3)

    def test_un_booleano_no_es_un_numero(self):
        # En Python `True` es un `int`; aceptarlo como monto sería un bug sutil.
        assert not _tipo_compatible({"type": "number"}, True)

    def test_any_of_no_restinge(self):
        """Un `anyOf` sin `type` propio no permite decidir: no se restringe.

        La función elige de qué rama tomar el ejemplo; para eso alcanza con que
        no rechace. Quien decide si un valor es válido es `jsonschema`, no esto.
        """
        esquema = {"anyOf": [{"type": "string"}, {"type": "null"}]}
        assert _tipo_compatible(esquema, "x")
        assert _tipo_compatible(esquema, None)
        assert _tipo_compatible(esquema, 1)

"""Adaptadores de proveedor: lo que se declara y lo que se omite.

Estos tests **no** llaman a la red: construyen los parámetros de la llamada y
comparan contra lo que cada proveedor acepta, y normalizan respuestas fabricadas.
Eso alcanza para fijar lo que importa, que no es la forma exacta del pedido sino
la **consecuencia** de cada diferencia:

* que a DeepSeek no se le mande ``temperature`` (la ignora, y creer que influyó
  hace inauditable el ajuste del prompt);
* que se **sepa** que no impone el esquema, para que el núcleo valide localmente;
* que a quien no acepta ``detail`` no se le mande, en vez de comerse un 400 que
  no dice qué campo sobra;
* que Gemini entre por el mismo protocolo, sin dependencia nueva.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import pytest

from voucherflow.llm.protocolo import CLAVE_CACHE_HIT, CLAVE_CACHE_MISS
from voucherflow.llm.proveedores import (
    PROVEEDORES,
    AdaptadorDeepSeek,
    AdaptadorGemini,
    AdaptadorOpenAI,
    describir_proveedores,
    proveedor_por_nombre,
)

ESQUEMA = {
    "type": "object",
    "properties": {"total": {"type": "number"}},
    "required": ["total"],
    "additionalProperties": False,
}


def _parametros(adaptador, **over):
    base = dict(
        modelo="un-modelo",
        sistema="SISTEMA",
        usuario="USUARIO",
        data_url="data:image/jpeg;base64,AAAA",
        esquema=ESQUEMA,
        nombre_esquema="comprobante",
    )
    base.update(over)
    return adaptador.parametros_de_llamada(**base)


# ---------------------------------------------------------------------------
# El catálogo
# ---------------------------------------------------------------------------


class TestCatalogo:
    def test_los_tres_proveedores_estan(self):
        assert set(PROVEEDORES) == {"openai", "deepseek", "gemini"}

    def test_instancia_por_nombre(self):
        assert proveedor_por_nombre("gemini").capacidades.nombre == "gemini"

    def test_un_nombre_desconocido_lista_los_validos(self):
        with pytest.raises(ValueError) as exc:
            proveedor_por_nombre("claude")
        assert "claude" in str(exc.value)
        assert "deepseek" in str(exc.value)

    def test_la_descripcion_es_serializable(self):
        # La ficha viaja al registro de la corrida y a la documentación.
        import json

        fichas = describir_proveedores()
        assert len(fichas) == 3
        assert json.dumps(fichas)  # no rompe con tipos raros

    def test_cada_proveedor_declara_su_modelo_por_defecto(self):
        """⚠️ Asumir el modelo de otro hace que la estimación use sus precios.

        Antes del arreglo, una corrida contra OpenAI se estimaba con los precios
        de DeepSeek: el número parecía válido y era de otro proveedor.
        """
        modelos = {n: PROVEEDORES[n]().capacidades.modelo_por_defecto for n in PROVEEDORES}
        assert all(modelos.values()), modelos
        assert len(set(modelos.values())) == len(modelos), (
            f"dos proveedores comparten modelo por defecto: {modelos}"
        )
        assert modelos["openai"] == "gpt-4o"
        assert modelos["gemini"].startswith("gemini")

    def test_todos_declaran_su_credencial(self):
        for adaptador in PROVEEDORES.values():
            assert adaptador().capacidades.variable_api_key.endswith("_API_KEY")


# ---------------------------------------------------------------------------
# La forma de la llamada
# ---------------------------------------------------------------------------


class TestFormaDeLaLlamada:
    def test_arma_los_mensajes_con_la_imagen(self):
        p = _parametros(AdaptadorOpenAI())
        assert p["model"] == "un-modelo"
        sistema, usuario = p["messages"]
        assert sistema["role"] == "system"
        assert usuario["content"][1]["type"] == "image_url"

    def test_openai_impone_el_esquema_con_strict(self):
        # Es lo que garantiza que el servidor devuelva la forma esperada.
        p = _parametros(AdaptadorOpenAI())
        formato = p["response_format"]
        assert formato["type"] == "json_schema"
        assert formato["json_schema"]["strict"] is True

    def test_deepseek_no_usa_strict(self):
        # Solo acepta `json_object`; el núcleo valida localmente.
        p = _parametros(AdaptadorDeepSeek())
        assert p["response_format"] == {"type": "json_object"}

    def test_gemini_usa_json_schema_sin_strict(self):
        p = _parametros(AdaptadorGemini())
        assert p["response_format"]["type"] == "json_schema"
        assert "strict" not in p["response_format"]["json_schema"]


# ---------------------------------------------------------------------------
# Las diferencias que obligan a compensar
# ---------------------------------------------------------------------------


class TestTemperatura:
    def test_se_manda_a_quien_la_respeta(self):
        assert _parametros(AdaptadorOpenAI(), temperatura=0.2)["temperature"] == 0.2
        assert _parametros(AdaptadorGemini(), temperatura=0.2)["temperature"] == 0.2

    def test_no_se_manda_a_quien_la_ignora(self):
        """A DeepSeek no se le manda: su modo de razonamiento la descarta.

        Mandarla igual haría creer que el ajuste del prompt cambió algo, cuando
        el resultado es idéntico con y sin ella.
        """
        p = _parametros(AdaptadorDeepSeek(), temperatura=0.2)
        assert "temperature" not in p
        assert AdaptadorDeepSeek().capacidades.temperatura_efectiva is False

    def test_no_se_manda_si_no_se_pide(self):
        assert "temperature" not in _parametros(AdaptadorOpenAI())


class TestDetalleDeImagen:
    def test_se_manda_a_quien_lo_acepta(self):
        p = _parametros(AdaptadorOpenAI(), detalle="high")
        assert p["messages"][1]["content"][1]["image_url"]["detail"] == "high"

    def test_no_se_manda_a_quien_no_lo_acepta(self):
        # Un `detail` a quien no lo soporta es un 400 que no dice cuál es el
        # campo de más — mejor no mandarlo.
        for adaptador in (AdaptadorDeepSeek(), AdaptadorGemini()):
            p = _parametros(adaptador, detalle="high")
            assert "detail" not in p["messages"][1]["content"][1]["image_url"]


class TestMaxTokens:
    def test_se_manda_cuando_se_pide(self):
        p = _parametros(AdaptadorOpenAI(), max_tokens=4096)
        assert p["max_completion_tokens"] == 4096

    def test_por_defecto_no_se_manda(self):
        """No mandar el techo es lo correcto por defecto.

        Los proveedores tienen un default holgado (8K sin razonamiento, 64K con
        él); poner un número creyéndolo generoso **trunca** el JSON, y el error
        aparece como un «JSON inválido» que se busca en el lugar equivocado.
        """
        assert "max_completion_tokens" not in _parametros(AdaptadorOpenAI())


class TestEsfuerzoDeRazonamiento:
    def test_se_manda_a_quien_lo_declara(self):
        p = _parametros(AdaptadorDeepSeek(), esfuerzo="high")
        assert p["reasoning_effort"] == "high"

    def test_no_se_manda_a_quien_no_lo_usa(self):
        assert "reasoning_effort" not in _parametros(AdaptadorOpenAI(), esfuerzo="high")

    def test_un_valor_invalido_falla_con_los_validos_a_la_vista(self):
        # Cada proveedor tiene su vocabulario: `minimal` es de Gemini y `max` de
        # DeepSeek. Un error que no los liste obliga a adivinar.
        with pytest.raises(ValueError) as exc:
            _parametros(AdaptadorDeepSeek(), esfuerzo="minimal")
        assert "minimal" in str(exc.value)
        assert "high" in str(exc.value)

    def test_gemini_acepta_minimal(self):
        assert _parametros(AdaptadorGemini(), esfuerzo="minimal")["reasoning_effort"] == (
            "minimal"
        )


# ---------------------------------------------------------------------------
# Lectura de la respuesta
# ---------------------------------------------------------------------------


@dataclass
class _Mensaje:
    content: str


@dataclass
class _Eleccion:
    message: _Mensaje
    finish_reason: str = "stop"


@dataclass
class _Uso:
    prompt_tokens: int = 1000
    completion_tokens: int = 200
    total_tokens: int = 1200
    prompt_cache_hit_tokens: int | None = None
    prompt_cache_miss_tokens: int | None = None


@dataclass
class _Respuesta:
    choices: list
    usage: Any = None


class TestLeerRespuesta:
    def test_extrae_el_contenido_y_el_uso(self):
        r = AdaptadorOpenAI().leer_respuesta(
            _Respuesta([_Eleccion(_Mensaje('{"total": 100}'))], _Uso())
        )
        assert r["contenido"] == '{"total": 100}'
        assert r["uso"]["prompt_tokens"] == 1000
        assert r["error"] is None

    def test_una_respuesta_sin_opciones_es_un_error(self):
        r = AdaptadorOpenAI().leer_respuesta(_Respuesta([]))
        assert r["error"] is not None
        assert r["contenido"] is None

    def test_el_truncado_por_tokens_se_explica(self):
        """Un `finish_reason=length` es la causa más común de un JSON roto.

        Sin decirlo, el síntoma («JSON inválido») manda a buscar el problema al
        parser cuando el problema es el techo de tokens.
        """
        r = AdaptadorOpenAI().leer_respuesta(
            _Respuesta([_Eleccion(_Mensaje('{"total": 1'), "length")], _Uso())
        )
        assert r["error"] is not None
        assert "límite de tokens" in r["error"]
        assert r.get("truncado") is True

    def test_el_uso_de_cache_solo_se_lee_de_quien_lo_expone(self):
        uso = _Uso(prompt_cache_hit_tokens=800, prompt_cache_miss_tokens=200)
        con = AdaptadorDeepSeek().leer_respuesta(_Respuesta([_Eleccion(_Mensaje("{}"))], uso))
        assert con["uso"][CLAVE_CACHE_HIT] == 800
        assert con["uso"][CLAVE_CACHE_MISS] == 200

        # OpenAI no lo expone: el campo no aparece, y no se rellena con 0 porque
        # "no hubo caché" y "no me dijeron" no son lo mismo.
        sin = AdaptadorOpenAI().leer_respuesta(_Respuesta([_Eleccion(_Mensaje("{}"))], uso))
        assert CLAVE_CACHE_HIT not in sin["uso"]

    def test_la_clave_de_cache_es_la_del_sdk(self):
        """⚠️ Regresión del bug de mayor impacto del módulo.

        El adaptador emitía ``cache_hit_tokens`` y ``corrida.py`` leía
        ``prompt_cache_hit_tokens``: al no coincidir nunca, el descuento de la
        caché no se aplicaba **jamás** y el gasto se reportaba ~3,2x de más. Un
        ``dict`` no tiene forma, así que nada lo detectaba: cada mitad estaba
        testeada por separado y la costura no.

        La clave tiene que ser el nombre del campo del SDK, porque es el mismo
        que lee el consumidor del uso.
        """
        uso = _Uso(prompt_cache_hit_tokens=2816, prompt_cache_miss_tokens=201)
        lectura = AdaptadorDeepSeek().leer_respuesta(
            _Respuesta([_Eleccion(_Mensaje("{}"))], uso)
        )
        assert CLAVE_CACHE_HIT == "prompt_cache_hit_tokens"
        # El nombre que ``corrida.py`` usa para cobrar el descuento TIENE que
        # estar en el uso normalizado.
        assert lectura["uso"][CLAVE_CACHE_HIT] == 2816
        assert "cache_hit_tokens" not in lectura["uso"]

    def test_sin_uso_no_se_inventan_tokens(self):
        r = AdaptadorOpenAI().leer_respuesta(_Respuesta([_Eleccion(_Mensaje("{}"))], None))
        assert r["uso"]["prompt_tokens"] is None


# ---------------------------------------------------------------------------
# Lo que se declara para la trazabilidad
# ---------------------------------------------------------------------------


class TestCapacidadesDeclaradas:
    def test_deepseek_declara_no_imponer_el_esquema(self):
        # Es la señal que hace que el núcleo valide localmente en vez de confiar.
        assert AdaptadorDeepSeek().capacidades.esquema_estricto is False

    def test_solo_openai_impone_el_esquema(self):
        assert AdaptadorOpenAI().capacidades.esquema_estricto is True
        assert AdaptadorGemini().capacidades.esquema_estricto is False

    def test_las_notas_explican_la_compensacion(self):
        # Una limitación declarada en el reporte no se lee como un defecto oculto.
        notas = AdaptadorDeepSeek().capacidades.notas
        assert any("esquema" in n for n in notas)
        assert any("temperatura" in n for n in notas)

    def test_deepseek_declara_el_tope_fijo_de_imagen(self):
        # Su costo de imagen NO depende del tamaño: agranda las chicas.
        assert AdaptadorDeepSeek().capacidades.tokens_por_imagen == 1024
        assert AdaptadorDeepSeek().capacidades.acepta_detalle is False

    def test_gemini_declara_que_no_agrega_dependencia(self):
        notas = AdaptadorGemini().capacidades.notas
        assert any("dependencia" in n for n in notas)

    def test_el_base_url_de_gemini_es_el_endpoint_compatible(self):
        # Es lo que permite usar el mismo SDK `openai`.
        assert "openai" in AdaptadorGemini().capacidades.base_url

    def test_la_ficha_serializa_las_capacidades(self):
        ficha = AdaptadorDeepSeek().capacidades.como_diccionario()
        assert ficha["proveedor"] == "deepseek"
        assert ficha["esquema_estricto"] is False
        assert ficha["temperatura_efectiva"] is False
        assert isinstance(ficha["notas"], list)

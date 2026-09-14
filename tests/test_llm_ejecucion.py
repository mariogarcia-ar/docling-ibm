"""La llamada al modelo: el ciclo que se adapta al proveedor.

Lo que se prueba es que el ciclo **no** pregunte de qué proveedor se trata, sino
que consulte sus capacidades. Los casos que importan son las diferencias:

* sin esquema estricto, la forma se sostiene validando y repreguntando;
* con el esquema impuesto por el servidor, no se valida localmente (sería trabajo
  redundante y un reintento que no hace falta);
* cuando el proveedor ignora la temperatura, se **declara**;
* cada repregunta se **paga**, así que el uso acumula todos los intentos.

El doble del SDK captura los parámetros: eso permite afirmar que a un proveedor
no se le mandó algo, que es la mitad de la compensación.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import pytest

from voucherflow.llm.ejecucion import Respuesta, describir_error, llamar_api

ESQUEMA = {
    "type": "object",
    "properties": {
        "total": {"type": "number"},
        "moneda": {"type": "string", "enum": ["ARS", "USD"]},
    },
    "required": ["total", "moneda"],
    "additionalProperties": False,
}

# ---------------------------------------------------------------------------
# Doble del SDK
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
    prompt_tokens: int = 100
    completion_tokens: int = 20
    total_tokens: int = 120
    prompt_cache_hit_tokens: int | None = None
    prompt_cache_miss_tokens: int | None = None


@dataclass
class _RespuestaSDK:
    choices: list
    usage: Any = None


class _Completions:
    def __init__(self, respuestas):
        self._respuestas = list(respuestas)
        self.llamadas: list[dict] = []

    def create(self, **kwargs):
        self.llamadas.append(kwargs)
        if not self._respuestas:
            raise AssertionError("el ciclo pidió más respuestas de las previstas")
        return self._respuestas.pop(0)


class Cliente:
    """Cliente del SDK con respuestas enlatadas y registro de lo pedido."""

    def __init__(self, *respuestas):
        self.completions = _Completions(respuestas)
        self.chat = type("Chat", (), {"completions": self.completions})()


def _respuesta(texto: str, fin: str = "stop", uso: Any = None):
    return _RespuestaSDK([_Eleccion(_Mensaje(texto), fin)], uso or _Uso())


def _llamar(cliente, **over):
    base = dict(
        modelo="un-modelo",
        sistema="SISTEMA",
        usuario="USUARIO",
        data_url="data:image/jpeg;base64,AAAA",
        esquema=ESQUEMA,
    )
    base.update(over)
    return llamar_api(cliente, **base)


# ---------------------------------------------------------------------------
# El caso feliz
# ---------------------------------------------------------------------------


class TestRespuestaValida:
    def test_una_respuesta_correcta_sale_sin_reintentos(self):
        c = Cliente(_respuesta('{"total": 10, "moneda": "ARS"}'))
        r = _llamar(c, proveedor="deepseek")
        assert r.ok
        assert r.reintentos == 0
        assert r.datos == {"total": 10, "moneda": "ARS"}

    def test_una_sola_llamada(self):
        c = Cliente(_respuesta('{"total": 10, "moneda": "ARS"}'))
        _llamar(c, proveedor="deepseek")
        assert len(c.completions.llamadas) == 1

    def test_devuelve_el_uso(self):
        c = Cliente(_respuesta('{"total": 10, "moneda": "ARS"}'))
        r = _llamar(c, proveedor="deepseek")
        assert r.uso["prompt_tokens"] == 100


# ---------------------------------------------------------------------------
# La forma: quién la sostiene
# ---------------------------------------------------------------------------


class TestValidacionLocal:
    def test_sin_esquema_estricto_repregunta_con_el_error(self):
        """Es lo más cerca del structured output del servidor que permite el
        proveedor: validar y devolverle el error al modelo."""
        c = Cliente(
            _respuesta('{"total": 10}'),
            _respuesta('{"total": 10, "moneda": "ARS"}'),
        )
        r = _llamar(c, proveedor="deepseek")
        assert r.ok
        assert r.reintentos == 1
        segundo = c.completions.llamadas[1]
        # El error viaja como feedback en el último mensaje.
        assert "formato pedido" in str(segundo["messages"][-1])

    def test_la_repregunta_suma_contexto_sin_repetir_la_imagen(self):
        c = Cliente(
            _respuesta('{"total": 10}'),
            _respuesta('{"total": 10, "moneda": "ARS"}'),
        )
        _llamar(c, proveedor="deepseek")
        primero, segundo = c.completions.llamadas

        def imagenes(mensajes: list[dict]) -> int:
            total = 0
            for mensaje in mensajes:
                contenido = mensaje.get("content")
                if isinstance(contenido, list):
                    total += sum(
                        1 for parte in contenido if parte.get("type") == "image_url"
                    )
            return total

        # El primer intento: system + el usuario con la imagen.
        assert [m["role"] for m in primero["messages"]] == ["system", "user"]
        assert imagenes(primero["messages"]) == 1

        # El segundo agrega el intento anterior y el feedback, y la imagen sigue
        # yendo **una sola vez** (el caché de contexto cobra barato el prefijo).
        assert [m["role"] for m in segundo["messages"]] == [
            "system", "user", "assistant", "user",
        ]
        assert imagenes(segundo["messages"]) == 1

    def test_cada_intento_lleva_su_propia_lista_de_mensajes(self):
        """⚠️ Si los intentos compartieran la lista, el segundo pedido mandaría el
        feedback y el **primero** también (viajarían con el mismo contenido)."""
        c = Cliente(
            _respuesta('{"total": 10}'),
            _respuesta('{"total": 10, "moneda": "ARS"}'),
        )
        _llamar(c, proveedor="deepseek")
        primero, segundo = c.completions.llamadas
        assert primero["messages"] is not segundo["messages"]
        assert len(primero["messages"]) < len(segundo["messages"])

    def test_con_esquema_estricto_no_valida_local(self):
        """Con `strict`, el servidor ya garantiza la forma.

        Validar localmente sería redundante, y además un reintento que no hace
        falta: el pedido salió bien.
        """
        c = Cliente(_respuesta('{"total": 10, "moneda": "OTRA"}'))
        r = _llamar(c, proveedor="openai")
        assert r.ok
        assert len(c.completions.llamadas) == 1

    def test_un_enum_en_otra_caja_no_cuesta_un_reintento(self):
        """Cada repregunta se paga: un detalle tipográfico no la justifica."""
        c = Cliente(_respuesta('{"total": 10, "moneda": "ars"}'))
        r = _llamar(c, proveedor="gemini")
        assert r.ok
        assert r.reintentos == 0
        assert len(c.completions.llamadas) == 1
        assert r.datos["moneda"] == "ARS"
        # Pero se declara: corregir en silencio ocultaría la desviación.
        assert r.avisos_esquema

    def test_se_rinde_tras_el_maximo_de_reintentos(self):
        # Sin tope, un modelo que no respeta la forma se repreguntaría sin fin.
        c = Cliente(*[_respuesta('{"total": 10}') for _ in range(5)])
        r = _llamar(c, proveedor="deepseek")
        assert not r.ok
        assert r.error is not None
        assert "esquema" in r.error.lower()

    def test_un_json_invalido_tambien_se_repregunta(self):
        c = Cliente(
            _respuesta("no es json"),
            _respuesta('{"total": 10, "moneda": "ARS"}'),
        )
        r = _llamar(c, proveedor="deepseek")
        assert r.ok
        assert r.reintentos == 1

    def test_tolera_un_bloque_de_codigo_alrededor(self):
        # Un proveedor sin modo estricto a veces envuelve el JSON en ```json.
        # Rechazarlo costaría un reintento por algo que se puede leer.
        c = Cliente(_respuesta('```json\n{"total": 10, "moneda": "ARS"}\n```'))
        r = _llamar(c, proveedor="deepseek")
        assert r.ok
        assert r.reintentos == 0

    def test_no_intenta_arreglar_un_json_roto(self):
        """«Arreglar» un JSON roto ocultaría que el modelo no respetó la forma,
        que es justo lo que hay que medir."""
        c = Cliente(*[_respuesta('{"total": 10,,}') for _ in range(5)])
        r = _llamar(c, proveedor="deepseek")
        assert not r.ok


# ---------------------------------------------------------------------------
# Lo que se omite y se declara
# ---------------------------------------------------------------------------


class TestTemperatura:
    def test_a_quien_la_ignora_no_se_le_manda_y_se_declara(self):
        c = Cliente(_respuesta('{"total": 1, "moneda": "ARS"}'))
        r = _llamar(c, proveedor="deepseek", temperatura=0.5)
        assert "temperature" not in c.completions.llamadas[0]
        assert r.temperatura_ignorada is True

    def test_a_quien_la_respeta_se_le_manda(self):
        c = Cliente(_respuesta('{"total": 1, "moneda": "ARS"}'))
        r = _llamar(c, proveedor="openai", temperatura=0.5)
        assert c.completions.llamadas[0]["temperature"] == 0.5
        assert r.temperatura_ignorada is False

    def test_si_no_se_pide_no_se_declara_nada(self):
        c = Cliente(_respuesta('{"total": 1, "moneda": "ARS"}'))
        r = _llamar(c, proveedor="deepseek")
        assert r.temperatura_ignorada is False


class TestProveedor:
    def test_acepta_el_nombre(self):
        c = Cliente(_respuesta('{"total": 1, "moneda": "ARS"}'))
        assert _llamar(c, proveedor="gemini").ok

    def test_acepta_la_instancia(self):
        from voucherflow.llm.proveedores import AdaptadorGemini

        c = Cliente(_respuesta('{"total": 1, "moneda": "ARS"}'))
        assert _llamar(c, proveedor=AdaptadorGemini()).ok

    def test_sin_proveedor_usa_el_default(self):
        c = Cliente(_respuesta('{"total": 1, "moneda": "ARS"}'))
        r = _llamar(c)
        assert r.ok

    def test_un_proveedor_desconocido_falla_con_los_validos(self):
        c = Cliente(_respuesta("{}"))
        with pytest.raises(ValueError, match="deepseek"):
            _llamar(c, proveedor="claude")


class TestEsfuerzo:
    def test_se_propaga_al_pedido(self):
        c = Cliente(_respuesta('{"total": 1, "moneda": "ARS"}'))
        _llamar(c, proveedor="deepseek", esfuerzo="high")
        assert c.completions.llamadas[0]["reasoning_effort"] == "high"

    def test_un_esfuerzo_invalido_para_ese_proveedor_falla(self):
        # `minimal` es de Gemini; DeepSeek no lo conoce.
        c = Cliente(_respuesta("{}"))
        with pytest.raises(ValueError, match="minimal"):
            _llamar(c, proveedor="deepseek", esfuerzo="minimal")


# ---------------------------------------------------------------------------
# Errores y costo
# ---------------------------------------------------------------------------


class TestErrores:
    def test_una_excepcion_del_sdk_se_clasifica(self):
        class _ClienteRoto:
            def __init__(self):
                self.chat = type(
                    "C", (),
                    {"completions": type("X", (), {"create": staticmethod(self._boom)})()},
                )()

            @staticmethod
            def _boom(**kwargs):
                raise ConnectionError("no route to host")

        r = _llamar(_ClienteRoto(), proveedor="deepseek")
        assert not r.ok
        assert "conectar" in (r.error or "")

    def test_el_truncado_por_tokens_se_explica(self):
        """El síntoma («JSON inválido») manda a buscar el problema al parser
        cuando el problema es el techo de tokens."""
        c = Cliente(_respuesta('{"total": 1', "length"))
        r = _llamar(c, proveedor="deepseek")
        assert not r.ok
        assert "límite de tokens" in (r.error or "")

    def test_la_credencial_no_aparece_en_el_error(self):
        # El SDK lanza `AuthenticationError`; la clasificación mira el **nombre
        # de la clase**, no el texto del mensaje.
        class AuthenticationError(Exception):
            pass

        r = describir_error(AuthenticationError("invalid key sk-abc123"), "DEEPSEEK_API_KEY")
        assert "DEEPSEEK_API_KEY" in r
        # Ni el mensaje original ni una clave filtrada llegan al reporte.
        assert "sk-abc123" not in r
        assert "invalid key" not in r

    def test_la_credencial_tambien_se_detecta_por_el_401(self):
        r = describir_error(RuntimeError("HTTP 401 unauthorized"), "OPENAI_API_KEY")
        assert "OPENAI_API_KEY" in r

    def test_un_error_de_tasa_sugiere_bajar_workers(self):
        assert "workers" in describir_error(RuntimeError("429 rate limit"))


class TestUsoAcumulado:
    def test_cada_reintento_se_paga(self):
        """Contar solo el último intento haría parecer más barata una corrida que
        gastó de más justamente por un prompt flojo."""
        c = Cliente(
            _respuesta('{"total": 10}'),
            _respuesta('{"total": 10, "moneda": "ARS"}'),
        )
        r = _llamar(c, proveedor="deepseek")
        assert r.reintentos == 1
        assert r.uso["prompt_tokens"] == 200  # 2 llamadas × 100

    def test_el_uso_sobrevive_a_un_error(self):
        # El gasto ya ocurrió: el registro tiene que poder contarlo.
        c = Cliente(_respuesta('{"total": 10}'), _respuesta("no json"))
        r = _llamar(c, proveedor="deepseek")
        assert not r.ok
        assert r.uso["prompt_tokens"] == 200


class TestRespuestaOK:
    def test_no_es_ok_sin_datos(self):
        assert not Respuesta(None, error="x").ok

    def test_no_es_ok_con_error(self):
        assert not Respuesta({"a": 1}, error="x").ok

    def test_es_ok_con_datos_y_sin_error(self):
        assert Respuesta({"a": 1}).ok

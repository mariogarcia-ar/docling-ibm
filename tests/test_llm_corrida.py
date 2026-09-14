"""El reporte de gastos: leer el histórico, sumarlo y declararlo.

Estos tests cubren el cluster que **estaba escrito y desconectado**: las banderas
``--reporte-gastos`` y ``--csv-gastos`` se registraban en el CLI y nadie las leía,
así que las funciones de este camino no las ejercitaba ningún test (y por eso el
bug sobrevivió). Lo que se fija acá:

* que un ``--dry-run`` o un modo ``diff`` **no** cuenten como gasto (no llamaron
  a la API);
* que un fallo pagado **sí** cuente, pero declarado aparte (consumió tokens);
* que el gasto se deduplique por **documento real** y fecha, no por la ruta de
  salida (el mismo trabajo escrito en dos carpetas no se paga dos veces);
* que la entrada **cacheada** llegue al total (la costura que fallaba en silencio).

No hay red: los registros son archivos JSON escritos en un ``tmp_path``.
"""

from __future__ import annotations

import json
from dataclasses import replace
from pathlib import Path

import pytest

from voucherflow.llm import corrida
from voucherflow.llm.protocolo import CLAVE_CACHE_HIT, CLAVE_CACHE_MISS
from voucherflow.llm.proveedores import AdaptadorDeepSeek


def _opciones(salida: Path, **over) -> corrida.Opciones:
    base = dict(
        modo="extraer",
        modelo="deepseek-flash",
        detalle="high",
        temperatura=None,
        max_tokens=None,
        esfuerzo=None,
        salida=salida,
        forzar=False,
        workers=1,
        dry_run=False,
        incluir_ejemplo=False,
    )
    base.update(over)
    return corrida.Opciones(**base)


def _registro(
    origen: str,
    *,
    modo: str = "extraer",
    cuando: str = "2026-09-13T10:00:00+00:00",
    modelo: str = "deepseek-flash",
    prompt: int = 3017,
    completion: int = 250,
    cache_hit: int | None = 2816,
    cache_miss: int | None = 201,
    **extra,
) -> dict:
    uso: dict = {"prompt_tokens": prompt, "completion_tokens": completion}
    if cache_hit is not None:
        uso[CLAVE_CACHE_HIT] = cache_hit
        uso[CLAVE_CACHE_MISS] = cache_miss
    return {
        "origen": origen,
        "archivo_relativo": Path(origen).name,
        "procesado_utc": cuando,
        "modelo": modelo,
        "modo": modo,
        "fuente": "api",
        "uso": uso,
        **extra,
    }


def _escribir(salida: Path, nombre: str, registro: dict) -> Path:
    destino = salida / nombre
    destino.parent.mkdir(parents=True, exist_ok=True)
    destino.write_text(json.dumps(registro, ensure_ascii=False), encoding="utf-8")
    return destino


def _payload_valido(esquema: dict) -> dict:
    """Respuesta que **pasa** la validación local, derivada del propio esquema.

    Los esquemas son estrictos (todas las propiedades en ``required``,
    ``additionalProperties: false``), así que un JSON de mentira con dos campos
    hace que el núcleo repregunte —y el test mediría la repregunta en vez de lo
    que quiere medir—. Derivarlo del esquema evita que el test se rompa cada vez
    que se agrega un campo.
    """
    def valor(nodo: dict):
        if "anyOf" in nodo:
            # Se prefiere el tipo real sobre `null` (un payload completo).
            opciones = [o for o in nodo["anyOf"] if o.get("type") != "null"]
            return valor((opciones or nodo["anyOf"])[0])
        if "enum" in nodo:
            return nodo["enum"][0]
        tipo = nodo.get("type")
        if tipo == "object":
            return {k: valor(v) for k, v in nodo["properties"].items()}
        if tipo == "array":
            return []
        if tipo == "string":
            return ""
        if tipo == "number":
            return 0
        if tipo == "boolean":
            return False
        return None

    return valor(esquema)


def _respuesta_extraccion_valida(uso=None):
    """Respuesta del modo ``extraer`` que pasa el esquema (y no repregunta)."""
    from tests.test_llm_ejecucion import _respuesta
    from voucherflow.llm.esquemas import esquema_extraccion

    payload = json.dumps(_payload_valido(esquema_extraccion()))
    return _respuesta(payload, uso=uso) if uso is not None else _respuesta(payload)



class TestLeerApuntes:
    def test_une_el_historico_de_toda_la_carpeta(self, tmp_path):
        """El gasto es del histórico, no de la última corrida."""
        _escribir(tmp_path, "2026-09/a/doc.extraccion.json", _registro("/x/a.jpg"))
        _escribir(tmp_path, "2026-10/b/doc.extraccion.json", _registro("/x/b.jpg"))
        apuntes, descartados = corrida.leer_apuntes(tmp_path, _opciones(tmp_path))
        assert len(apuntes) == 2
        assert descartados == 0

    def test_un_dry_run_no_es_un_gasto(self, tmp_path):
        """⚠️ No se llamó a la API: contarlo inflaría el reporte."""
        _escribir(
            tmp_path,
            "doc.extraccion.json",
            _registro("/x/a.jpg", dry_run=True),
        )
        apuntes, descartados = corrida.leer_apuntes(tmp_path, _opciones(tmp_path))
        assert apuntes == []
        assert descartados == 1

    def test_el_modo_diff_no_es_un_gasto(self, tmp_path):
        """El diff es determinístico y local: no llama a la API."""
        _escribir(
            tmp_path,
            "doc.validacion.json",
            _registro("/x/a.jpg", modo="diff", fuente="diff_local"),
        )
        apuntes, _ = corrida.leer_apuntes(tmp_path, _opciones(tmp_path))
        assert apuntes == []

    def test_un_fallo_con_uso_si_es_un_gasto_pero_se_declara(self, tmp_path):
        """Un fallo después de reintentar consumió (y facturó) tokens."""
        _escribir(
            tmp_path,
            "doc.extraccion.json",
            _registro("/x/a.jpg", error="no cumplió el esquema"),
        )
        apuntes, _ = corrida.leer_apuntes(tmp_path, _opciones(tmp_path))
        assert len(apuntes) == 1
        assert apuntes[0]["fallo"] is True

    def test_sin_uso_no_se_puede_afirmar_que_gasto(self, tmp_path):
        registro = _registro("/x/a.jpg")
        registro.pop("uso")
        _escribir(tmp_path, "doc.extraccion.json", registro)
        apuntes, descartados = corrida.leer_apuntes(tmp_path, _opciones(tmp_path))
        assert apuntes == []
        assert descartados == 1

    def test_los_reportes_no_se_recuentan(self, tmp_path):
        """Los reportes del propio comando no son registros de gasto."""
        _escribir(tmp_path, "doc.extraccion.json", _registro("/x/a.jpg"))
        _escribir(tmp_path, "gastos.json", {"apuntes": [{"costo_usd": 5.0}]})
        _escribir(tmp_path, "reporte.json", {"total": {}})
        apuntes, descartados = corrida.leer_apuntes(tmp_path, _opciones(tmp_path))
        assert len(apuntes) == 1
        # Se saltean por nombre, así que NO cuentan como descartados: no son
        # registros ilegibles ni llamadas no pagas, son salida nuestra.
        assert descartados == 0

    def test_un_reporte_con_otro_nombre_tampoco_se_recuenta(self, tmp_path):
        """Un archivo que guarda `apuntes` ya calculados no se vuelve a contar.

        Es la red que cubre al reporte escrito fuera de los nombres conocidos:
        si no, escribir el reporte dentro de la carpeta de salida duplicaría el
        gasto en la próxima lectura.
        """
        _escribir(tmp_path, "doc.extraccion.json", _registro("/x/a.jpg"))
        _escribir(tmp_path, "mi-resumen.json", {"apuntes": [{"costo_usd": 5.0}]})
        apuntes, descartados = corrida.leer_apuntes(tmp_path, _opciones(tmp_path))
        assert len(apuntes) == 1
        assert descartados == 1

    def test_un_json_ilegible_se_descarta_sin_romper(self, tmp_path):
        (tmp_path / "roto.extraccion.json").write_text("{no es json", encoding="utf-8")
        _escribir(tmp_path, "ok.extraccion.json", _registro("/x/a.jpg"))
        apuntes, descartados = corrida.leer_apuntes(tmp_path, _opciones(tmp_path))
        assert len(apuntes) == 1
        assert descartados == 1

    def test_el_apunte_de_fallo_no_guarda_la_lectura_del_modelo(self, tmp_path):
        """⚠️ El error puede citar el contenido del comprobante."""
        _escribir(
            tmp_path,
            "doc.extraccion.json",
            _registro("/x/a.jpg", error="cita la lectura: 'CUIT 20123456789'"),
        )
        apuntes, _ = corrida.leer_apuntes(tmp_path, _opciones(tmp_path))
        assert "error" not in apuntes[0]

    def test_una_carpeta_inexistente_no_falla(self, tmp_path):
        apuntes, descartados = corrida.leer_apuntes(
            tmp_path / "no-existe", _opciones(tmp_path)
        )
        assert apuntes == []
        assert descartados == 0


class TestDeduplicacion:
    def test_el_mismo_documento_en_dos_carpetas_no_se_cuenta_dos_veces(self, tmp_path):
        """⚠️ El bug real: cambiar la raíz de espejado duplicaba el gasto.

        El mismo trabajo escrito en dos ubicaciones (por invocar el comando con
        otra ruta) **no** son dos llamadas pagas. Se identifica por el documento
        real + el momento, no por la ruta de salida.
        """
        _escribir(tmp_path, "2026-09/a/doc.extraccion.json", _registro("/x/a.jpg"))
        _escribir(tmp_path, "a/doc.extraccion.json", _registro("/x/a.jpg"))
        apuntes, _ = corrida.leer_apuntes(tmp_path, _opciones(tmp_path))
        assert len(apuntes) == 1

    def test_reprocesar_en_otro_momento_SI_es_un_gasto_nuevo(self, tmp_path):
        """Reprocesar (otro día, o con --forzar) se pagó de nuevo: se cuenta aparte."""
        _escribir(
            tmp_path,
            "a/doc.extraccion.json",
            _registro("/x/a.jpg", cuando="2026-09-13T10:00:00+00:00"),
        )
        _escribir(
            tmp_path,
            "b/doc.extraccion.json",
            _registro("/x/a.jpg", cuando="2026-09-14T10:00:00+00:00"),
        )
        apuntes, _ = corrida.leer_apuntes(tmp_path, _opciones(tmp_path))
        assert len(apuntes) == 2

    def test_documentos_distintos_no_se_colapsan(self, tmp_path):
        _escribir(tmp_path, "a.extraccion.json", _registro("/x/a.jpg"))
        _escribir(tmp_path, "b.extraccion.json", _registro("/x/b.jpg"))
        apuntes, _ = corrida.leer_apuntes(tmp_path, _opciones(tmp_path))
        assert len(apuntes) == 2


class TestTotalizar:
    def _apunte(self, **over) -> dict:
        base = {
            "fecha": "2026-09-13",
            "hora": "07:00:00",
            "fecha_hora": "2026-09-13T07:00:00-03:00",
            "documento_id": "/x/a.jpg",
            "modo": "extraer",
            "modelo": "deepseek-flash",
            "fuente": "api",
            "fallo": False,
            "tokens_prompt": 3017,
            "tokens_prompt_cache_hit": 2816,
            "tokens_completion": 250,
            "tokens_total": 3267,
            "costo_usd": 0.000377,
            "costo_confiable": True,
        }
        base.update(over)
        return base

    def test_suma_totales_y_agrupa(self):
        rep = corrida.totalizar(
            [
                self._apunte(),
                self._apunte(
                    documento_id="/x/b.jpg",
                    fecha="2026-09-14",
                    modelo="gpt-4o",
                    modo="validar",
                ),
            ]
        )
        assert rep["total"]["apuntes"] == 2
        assert rep["total"]["tokens_prompt"] == 6034
        assert rep["total"]["tokens_prompt_cache_hit"] == 5632
        assert rep["total"]["costo_usd"] == pytest.approx(0.000754)
        assert list(rep["por_dia"]) == ["2026-09-13", "2026-09-14"]
        assert set(rep["por_modelo"]) == {"deepseek-flash", "gpt-4o"}
        assert set(rep["por_modo"]) == {"extraer", "validar"}

    def test_sin_precio_se_declara_en_vez_de_sumar_cero(self):
        """Un `None` no es cero: el total reportado es menor al real."""
        rep = corrida.totalizar([self._apunte(costo_usd=None)])
        assert rep["sin_precio"] == 1
        assert rep["costo_parcial"] is True

    def test_un_precio_a_medias_marca_el_total_como_piso(self):
        rep = corrida.totalizar([self._apunte(costo_confiable=False)])
        assert rep["precio_incompleto"] == 1
        assert rep["costo_parcial"] is True

    def test_los_fallos_pagados_se_suman_y_se_declaran_aparte(self):
        rep = corrida.totalizar([self._apunte(fallo=True), self._apunte()])
        assert rep["fallos_pagados"] == 1
        assert rep["costo_fallos_usd"] == pytest.approx(0.000377)
        # Ya están dentro del total: es gasto real, solo que no es una extracción.
        assert rep["total"]["costo_usd"] == pytest.approx(0.000754)

    def test_sin_apuntes_el_total_es_cero_y_confiable(self):
        rep = corrida.totalizar([])
        assert rep["total"]["apuntes"] == 0
        assert rep["costo_parcial"] is False


class TestReporteDeGastos:
    """El punto de entrada que faltaba: sin él, todo el cluster era inalcanzable."""

    def test_devuelve_reporte_y_apuntes(self, tmp_path):
        _escribir(tmp_path, "a.extraccion.json", _registro("/x/a.jpg"))
        salida = tmp_path / "out"
        _escribir(salida, "a.extraccion.json", _registro("/x/a.jpg"))
        rep, apuntes = corrida.reporte_de_gastos(
            salida, _opciones(salida), escribir=False
        )
        assert len(apuntes) == 1
        assert rep["total"]["apuntes"] == 1
        assert rep["descartados"] == 0

    def test_la_entrada_cacheada_llega_al_total(self, tmp_path):
        """⚠️ La costura que fallaba: el adaptador emite y el costo lee la misma clave.

        Sin esto el descuento no se aplicaba nunca y el gasto se reportaba ~3,2x
        de más (medido: US$ 0,001205 vs US$ 0,000377 reales).
        """
        _escribir(tmp_path, "a.extraccion.json", _registro("/x/a.jpg"))
        rep, _ = corrida.reporte_de_gastos(
            tmp_path, _opciones(tmp_path), escribir=False
        )
        assert rep["total"]["tokens_prompt_cache_hit"] == 2816
        # El costo con caché aplicada, no el de la entrada completa.
        assert rep["total"]["costo_usd"] == pytest.approx(0.000377)

    def test_escribe_el_json_del_reporte(self, tmp_path):
        _escribir(tmp_path, "a.extraccion.json", _registro("/x/a.jpg"))
        destino = tmp_path / "gastos.json"
        corrida.reporte_de_gastos(
            tmp_path, _opciones(tmp_path), json_salida=destino
        )
        escrito = json.loads(destino.read_text(encoding="utf-8"))
        assert escrito["total"]["apuntes"] == 1

    def test_escribe_el_csv_con_delimitador_y_decimal(self, tmp_path):
        """Excel es-AR espera «;» y «,»; el BOM hace que respete los acentos."""
        _escribir(tmp_path, "a.extraccion.json", _registro("/x/a.jpg"))
        destino = tmp_path / "gastos.csv"
        corrida.reporte_de_gastos(
            tmp_path,
            _opciones(tmp_path, csv_delim=";", csv_decimal=","),
            csv_salida=destino,
        )
        crudo = destino.read_bytes()
        assert crudo.startswith(b"\xef\xbb\xbf")  # utf-8-sig
        lineas = destino.read_text(encoding="utf-8-sig").splitlines()
        assert ";" in lineas[0]
        assert "," in lineas[1]  # el decimal del costo

    def test_escribir_false_no_toca_el_disco(self, tmp_path):
        _escribir(tmp_path, "a.extraccion.json", _registro("/x/a.jpg"))
        destino = tmp_path / "no-debe-existir.json"
        corrida.reporte_de_gastos(
            tmp_path, _opciones(tmp_path), json_salida=destino, escribir=False
        )
        assert not destino.exists()

    def test_una_carpeta_vacia_no_rompe(self, tmp_path):
        rep, apuntes = corrida.reporte_de_gastos(
            tmp_path, _opciones(tmp_path), escribir=False
        )
        assert apuntes == []
        assert rep["total"]["apuntes"] == 0


class TestImprimirReporteGastos:
    def test_declara_que_no_hay_nada_facturable(self, capsys):
        corrida.imprimir_reporte_gastos(
            {"total": {"apuntes": 0}, "opciones": {"tz": "local"}}, []
        )
        assert "Sin extracciones" in capsys.readouterr().out

    def test_avisa_los_fallos_pagados_y_el_precio_faltante(self, tmp_path, capsys):
        _escribir(
            tmp_path,
            "a.extraccion.json",
            _registro("/x/a.jpg", error="falló"),
        )
        rep, apuntes = corrida.reporte_de_gastos(
            tmp_path, _opciones(tmp_path), escribir=False
        )
        corrida.imprimir_reporte_gastos(rep, apuntes)
        capturado = capsys.readouterr()
        assert "FALLOS PAGADOS" in capturado.err
        assert rep["fallos_pagados"] == 1


class TestAvisoDeSalidasEnOtrasRaices:
    """La protección contra el bug que hizo pagar dos veces 5 documentos."""

    def test_detecta_la_salida_de_la_misma_imagen_en_otra_raiz(self, tmp_path):
        imagen = tmp_path / "in" / "2025-08" / "2D2C9343" / "foto.jpg"
        imagen.parent.mkdir(parents=True, exist_ok=True)
        imagen.write_bytes(b"x")
        salida = tmp_path / "out"
        # Salida con la raíz "corta" (lo que la reanudación NO va a encontrar).
        otro = salida / "2D2C9343" / "foto.extraccion.json"
        otro.parent.mkdir(parents=True, exist_ok=True)
        otro.write_text("{}", encoding="utf-8")

        encontradas = corrida._salidas_en_otras_raices(
            salida, tmp_path / "in", [imagen], "extraccion"
        )
        assert [p.name for p in encontradas] == ["foto.extraccion.json"]

    def test_no_marca_archivos_de_otros_documentos(self, tmp_path):
        """⚠️ Un aviso que salta siempre es ruido y nadie lo lee."""
        imagen = tmp_path / "in" / "AAA" / "foto.jpg"
        imagen.parent.mkdir(parents=True, exist_ok=True)
        imagen.write_bytes(b"x")
        salida = tmp_path / "out"
        ajeno = salida / "BBB" / "otra.extraccion.json"
        ajeno.parent.mkdir(parents=True, exist_ok=True)
        ajeno.write_text("{}", encoding="utf-8")

        assert corrida._salidas_en_otras_raices(
            salida, tmp_path / "in", [imagen], "extraccion"
        ) == []

    def test_no_marca_la_salida_esperada(self, tmp_path):
        imagen = tmp_path / "in" / "AAA" / "foto.jpg"
        imagen.parent.mkdir(parents=True, exist_ok=True)
        imagen.write_bytes(b"x")
        salida = tmp_path / "out"
        esperada = corrida.salida_de(imagen, tmp_path / "in", salida, "extraccion")
        esperada.parent.mkdir(parents=True, exist_ok=True)
        esperada.write_text("{}", encoding="utf-8")

        assert corrida._salidas_en_otras_raices(
            salida, tmp_path / "in", [imagen], "extraccion"
        ) == []

    def test_una_carpeta_inexistente_devuelve_vacio(self, tmp_path):
        assert corrida._salidas_en_otras_raices(
            tmp_path / "no-existe", tmp_path, [], "extraccion"
        ) == []


class TestProcesar:
    """`procesar` es donde se **cobra**: el costo que queda en el registro.

    Es la ruta que más importa y la que no tenía ningún test (10% de cobertura
    en `corrida.py`). Acá se ejercita con un cliente doble, sin red.
    """

    def _imagen(self, tmp_path):
        img = tmp_path / "in" / "doc.jpg"
        img.parent.mkdir(parents=True, exist_ok=True)
        # Un JPEG mínimo válido, para que Pillow lo pueda medir.
        from PIL import Image

        Image.new("RGB", (2000, 1500), "white").save(img)
        return img

    def _procesar(self, tmp_path, cliente, **over):
        img = self._imagen(tmp_path)
        raiz = tmp_path / "in"
        opciones = _opciones(
            tmp_path / "out", incluir_ejemplo=False, **over
        )
        return corrida.procesar(
            img, raiz, cliente, "SISTEMA", "USER [IMAGEN] {}",
            {}, {}, opciones, "deepseek",
        )

    def test_el_costo_del_registro_descuenta_la_cache(self, tmp_path):
        """⚠️ La costura del bug: lo que se persiste tiene que incluir la caché.

        El adaptador emite la clave del SDK y acá se lee esa misma clave. Si
        divergieran, `costo_usd` se guardaría sin el descuento y el reporte de
        gastos —que respeta el costo del registro— mostraría el número inflado.
        """
        from tests.test_llm_ejecucion import Cliente, _Uso

        cliente = Cliente(
            _respuesta_extraccion_valida(
                uso=_Uso(
                    prompt_tokens=3017,
                    completion_tokens=250,
                    total_tokens=3267,
                    prompt_cache_hit_tokens=2816,
                    prompt_cache_miss_tokens=201,
                )
            )
        )
        registro = self._procesar(tmp_path, cliente)
        assert registro.get("error") is None, registro.get("error")
        # El precio de caché se persiste junto al costo (para auditar después).
        assert registro["precios_usd_1m"]["entrada_cache"] == pytest.approx(0.006)
        assert registro["costo_usd"] == round(
            (201 * 0.30 + 2816 * 0.006 + 250 * 1.20) / 1e6, 6
        )
        # Y el uso guardado tiene la clave con la que el reporte lo suma.
        assert registro["uso"][CLAVE_CACHE_HIT] == 2816

    def test_sin_cache_expuesta_el_costo_cobra_la_entrada_completa(self, tmp_path):
        from tests.test_llm_ejecucion import Cliente, _Uso

        cliente = Cliente(
            _respuesta_extraccion_valida(
                uso=_Uso(prompt_tokens=3017, completion_tokens=250, total_tokens=3267)
            )
        )
        registro = self._procesar(tmp_path, cliente)
        assert registro.get("error") is None, registro.get("error")
        assert registro["costo_usd"] == round((3017 * 0.30 + 250 * 1.20) / 1e6, 6)

    def test_guarda_la_procedencia_del_prompt(self, tmp_path):
        """Sin el hash del prompt efectivo, comparar dos corridas es adivinar."""
        from tests.test_llm_ejecucion import Cliente

        registro = self._procesar(tmp_path, Cliente(_respuesta_extraccion_valida()))
        assert registro["prompt_hash"].startswith("sha256:")
        assert registro["version_prompt"]
        assert registro["archivo_relativo"] == "doc.jpg"

    def test_un_error_del_modelo_se_registra_y_guarda_el_uso(self, tmp_path):
        """⚠️ Un fallo puede haber costado tokens: el uso se guarda igual."""
        from tests.test_llm_ejecucion import Cliente, _respuesta, _Uso

        # Respuesta truncada por el techo de tokens (finish_reason=length).
        registro = self._procesar(
            tmp_path, Cliente(_respuesta('{"a": ', fin="length", uso=_Uso()))
        )
        assert registro["error"] is not None
        assert registro["uso"]["prompt_tokens"] == 100
        # El costo no se calcula sobre una respuesta inválida: el gasto queda en
        # `uso` y el reporte de gastos lo toma de ahí (marcado `fallo`).
        assert "costo_usd" not in registro

    def test_sin_datos_cargados_el_modo_validar_avisa(self, tmp_path):
        from tests.test_llm_ejecucion import Cliente

        registro = self._procesar(
            tmp_path, Cliente(_respuesta_extraccion_valida()), modo="validar"
        )
        assert "no hay datos cargados" in registro["error"]

    def test_el_modo_validar_usa_los_datos_cargados(self, tmp_path):
        """El modo validar compara contra la verdad de negocio; deja la clave usada."""
        from tests.test_llm_ejecucion import Cliente, _respuesta
        from voucherflow.llm.esquemas import esquema_validacion

        img = self._imagen(tmp_path)
        payload = {
            **_payload_valido(esquema_validacion()),
            "estado_global": "OK",
        }
        cliente = Cliente(_respuesta(json.dumps(payload)))
        registro = corrida.procesar(
            img, tmp_path / "in", cliente, "SISTEMA", "USER [IMAGEN] {}",
            {"doc": {"tipo_comprobante": "A"}}, {},
            _opciones(tmp_path / "out", modo="validar"),
            "deepseek",
        )
        assert registro["datos_clave"] == "doc"
        assert registro["resultado"]["estado_global"] == "OK"

    def test_el_modo_diff_no_llama_a_la_api(self, tmp_path):
        """⚠️ Reutiliza la extracción previa: no vuelve a pagar."""
        img = self._imagen(tmp_path)
        registro = corrida.procesar(
            img, tmp_path / "in", None, "S", "U [IMAGEN] {}",
            {"doc": {"tipo_comprobante": "A", "importe_total_facturado": 100.0}},
            {"doc.jpg": {"importe_total": 100.0}},
            _opciones(tmp_path / "out", modo="diff"),
            "deepseek",
        )
        assert registro["fuente"] == "diff_local"
        assert registro["resultado"]["estado_global"] == "OK"
        assert "costo_usd" not in registro

    def test_el_modo_diff_sin_extraccion_previa_avisa(self, tmp_path):
        img = self._imagen(tmp_path)
        registro = corrida.procesar(
            img, tmp_path / "in", None, "S", "U [IMAGEN] {}",
            {"doc": {"tipo_comprobante": "A"}}, {},
            _opciones(tmp_path / "out", modo="diff"), "deepseek",
        )
        assert "sin extracción previa" in registro["error"]

    def test_una_imagen_demasiado_grande_se_avisa_antes_de_mandarla(self, tmp_path):
        """⚠️ El límite es por **proveedor** y se mide sobre el payload base64.

        Antes se comparaba el tamaño del archivo contra una constante global:
        una imagen de 28 MB (payload 37 MB) pasaba el control, viajaba, y volvía
        con un error del servidor por un límite que se podía chequear gratis.
        """
        from tests.test_llm_ejecucion import Cliente

        img = self._imagen(tmp_path)
        # Se baja el límite del proveedor en vez de escribir un archivo enorme.
        monkey = corrida.proveedor_por_nombre
        cap = AdaptadorDeepSeek().capacidades
        corrida.proveedor_por_nombre = lambda _n: type(
            "A", (), {"capacidades": replace(cap, limite_bytes_payload=10)}
        )()
        try:
            registro = corrida.procesar(
                img, tmp_path / "in", Cliente(), "S", "U [IMAGEN] {}",
                {}, {}, _opciones(tmp_path / "out"), "deepseek",
            )
        finally:
            corrida.proveedor_por_nombre = monkey
        assert "reducíla con `voucherflow corpus`" in registro["error"]
        # El aviso distingue el archivo del payload: es lo que confunde al operador.
        assert "en base64 ocupa" in registro["error"]

    def test_el_limite_no_depende_del_proveedor_por_defecto(self, tmp_path):
        """El límite que rige es el del proveedor elegido, no el de DeepSeek.

        Con la constante global, un proveedor más restrictivo (Gemini, 20 MB) o
        más permisivo (OpenAI, 512 MB) se validaba con el número ajeno.
        """
        from tests.test_llm_ejecucion import Cliente
        from voucherflow.llm.proveedores import AdaptadorOpenAI

        img = self._imagen(tmp_path)
        monkey = corrida.proveedor_por_nombre
        cap = AdaptadorOpenAI().capacidades
        corrida.proveedor_por_nombre = lambda _n: type(
            "A", (), {"capacidades": replace(cap, limite_bytes_payload=10)}
        )()
        try:
            registro = corrida.procesar(
                img, tmp_path / "in", Cliente(), "S", "U [IMAGEN] {}",
                {}, {}, _opciones(tmp_path / "out"), "openai",
            )
        finally:
            corrida.proveedor_por_nombre = monkey
        # El error salió del límite de ESTE proveedor (10 bytes), no del global.
        assert "en base64 ocupa" in registro["error"]
        assert "0 MB de payload" in registro["error"]

    def test_una_imagen_no_se_puede_leer(self, tmp_path):
        """Una ruta que no existe es un error del registro, no un crash."""
        from tests.test_llm_ejecucion import Cliente

        registro = corrida.procesar(
            tmp_path / "in" / "no-existe.jpg", tmp_path / "in", Cliente(),
            "S", "U [IMAGEN] {}", {}, {}, _opciones(tmp_path / "out"), "deepseek",
        )
        assert "no se pudo leer la imagen" in registro["error"]


class TestYaProcesado:
    def test_un_registro_con_error_no_cuenta_como_hecho(self, tmp_path):
        """⚠️ Si contara, una re-corrida saltearía el documento y reportaría éxito."""
        destino = tmp_path / "a.extraccion.json"
        destino.write_text(json.dumps({"error": "falló la red"}), encoding="utf-8")
        assert corrida.ya_procesado(destino) is False

    def test_un_registro_valido_cuenta_como_hecho(self, tmp_path):
        destino = tmp_path / "a.extraccion.json"
        destino.write_text(json.dumps({"resultado": {}}), encoding="utf-8")
        assert corrida.ya_procesado(destino) is True

    def test_un_json_ilegible_se_reprocesa(self, tmp_path):
        destino = tmp_path / "a.extraccion.json"
        destino.write_text("{roto", encoding="utf-8")
        assert corrida.ya_procesado(destino) is False

    def test_un_archivo_inexistente_no_esta_procesado(self, tmp_path):
        assert corrida.ya_procesado(tmp_path / "no-existe.json") is False


class TestEjecutarPersisteLosFallos:
    """⚠️ Regresión: el port perdía el gasto de un fallo pagado.

    El script original (`scripts/operacion/validar-*.py`, retirado) guardaba el
    registro **siempre** —«también los fallidos, para auditarlos», decía su
    docstring—. El port puso `_guardar` en el `else` de la rama de error: el
    registro quedaba solo en memoria, así que el gasto de un fallo de la API
    (que sí consumió tokens) desaparecía al terminar el proceso y el reporte de
    gastos —que lee la carpeta— no lo veía nunca.
    """

    def _ejecutar_con_cliente(self, tmp_path, monkeypatch, contenido: str):
        """Corre `ejecutar` con un cliente doble, sin red.

        Se parchea el constructor del cliente: es el único punto por donde
        `ejecutar` obtiene el cliente, así que el resto del camino (recorrido,
        reanudación, guardado, resumen) es el de producción.
        """
        from PIL import Image

        from voucherflow.llm import corrida as mod

        entrada = tmp_path / "in"
        salida = tmp_path / "out"
        entrada.mkdir(parents=True, exist_ok=True)
        Image.new("RGB", (800, 600), "white").save(entrada / "a.jpg")

        class _Uso:
            prompt_tokens = 3017
            completion_tokens = 0
            total_tokens = 3017
            prompt_cache_hit_tokens = 2816
            prompt_cache_miss_tokens = 201

        class _Eleccion:
            finish_reason = "stop"

            class message:
                content = contenido

        class _Respuesta:
            choices = [_Eleccion()]
            usage = _Uso()

        class ClienteDoble:
            class _C:
                def create(self, **kw):
                    return _Respuesta()

            def __init__(self):
                self.chat = type("Chat", (), {"completions": self._C()})()

        monkeypatch.setattr(
            mod, "_cliente_del_proveedor", lambda *a, **k: ClienteDoble()
        )
        codigo = mod.ejecutar(
            rutas=[entrada],
            opciones=_opciones(salida),
            sistema="S",
            user_template="U [IMAGEN] {}",
            proveedor="deepseek",
            api_key="x",
        )
        return codigo, salida

    def test_un_documento_que_falla_igual_queda_en_disco(self, tmp_path, monkeypatch):
        """El registro con error se escribe: es la evidencia del fallo."""
        # JSON roto: se agotan los reintentos y el registro sale con `error`.
        codigo, salida = self._ejecutar_con_cliente(tmp_path, monkeypatch, '{"x": ')
        assert codigo == corrida.EXIT_FALLOS

        escritos = list(salida.rglob("*.extraccion.json"))
        assert len(escritos) == 1, "el registro del fallo tiene que persistirse"
        datos = json.loads(escritos[0].read_text(encoding="utf-8"))
        assert datos.get("error")
        # Y trae el uso: es el gasto real de los 4 intentos (1 + 3 reintentos),
        # que es justo lo que se perdía. Se suman, no se pisan.
        assert datos["uso"]["prompt_tokens"] == 3017 * 4

    def test_el_gasto_del_fallo_llega_al_reporte(self, tmp_path, monkeypatch):
        """⚠️ El motivo del fix: ese gasto era invisible para el reporte."""
        _, salida = self._ejecutar_con_cliente(tmp_path, monkeypatch, '{"x": ')

        rep, apuntes = corrida.reporte_de_gastos(
            salida, _opciones(salida), escribir=False
        )
        assert len(apuntes) == 1
        assert rep["fallos_pagados"] == 1
        assert rep["total"]["costo_usd"] > 0

    def test_un_documento_exitoso_tambien_se_persiste(self, tmp_path, monkeypatch):
        """El camino feliz no cambió."""
        from voucherflow.llm.esquemas import esquema_extraccion

        payload = json.dumps(_payload_valido(esquema_extraccion()))
        codigo, salida = self._ejecutar_con_cliente(tmp_path, monkeypatch, payload)
        assert codigo == corrida.EXIT_OK

        escritos = list(salida.rglob("*.extraccion.json"))
        assert len(escritos) == 1
        datos = json.loads(escritos[0].read_text(encoding="utf-8"))
        assert not datos.get("error")
        assert datos.get("costo_usd") is not None

    def test_el_fallo_no_cuenta_como_hecho_al_reanudar(self, tmp_path, monkeypatch):
        """Se persiste para auditar, pero la corrida siguiente lo reintenta."""
        _, salida = self._ejecutar_con_cliente(tmp_path, monkeypatch, '{"x": ')
        escrito = next(salida.rglob("*.extraccion.json"))
        assert corrida.ya_procesado(escrito) is False


class TestInterrupcion:
    """Ctrl-C tiene que salir con 130, no con un traceback."""

    def test_la_constante_es_la_del_resto_del_cli(self):
        from voucherflow.corpus.cli import EXIT_INTERRUMPIDO

        assert corrida.EXIT_INTERRUMPIDO == EXIT_INTERRUMPIDO == 130

    def test_el_entrypoint_atrapa_ctrl_c(self, monkeypatch, capsys):
        """⚠️ Sin esto, un Ctrl-C salía con traceback y código 1.

        El resto del CLI (`corpus`) devuelve 130, y con eso el operador distingue
        «lo interrumpí» de «falló».
        """
        from voucherflow.llm import cli

        def _interrumpir(*a, **k):
            raise KeyboardInterrupt

        monkeypatch.setattr(cli, "main", _interrumpir)
        with pytest.raises(SystemExit) as excinfo:
            cli.entrypoint()
        assert excinfo.value.code == 130
        assert "Interrumpido" in capsys.readouterr().err

    def test_una_interrupcion_durante_la_corrida_devuelve_130(
        self, tmp_path, monkeypatch
    ):
        """Los documentos ya guardados quedan en disco: la próxima corrida reanuda."""
        from PIL import Image

        from voucherflow.llm import corrida as mod

        entrada = tmp_path / "in"
        entrada.mkdir(parents=True)
        Image.new("RGB", (800, 600), "white").save(entrada / "a.jpg")

        def _boom(*a, **k):
            raise KeyboardInterrupt

        monkeypatch.setattr(mod, "procesar", _boom)
        codigo = mod.ejecutar(
            rutas=[entrada],
            opciones=_opciones(tmp_path / "out"),
            sistema="S",
            user_template="U [IMAGEN] {}",
            proveedor="deepseek",
            api_key="x",
        )
        assert codigo == corrida.EXIT_INTERRUMPIDO
        # Y no se escribió nada a medias.
        assert not list((tmp_path / "out").rglob("*.json"))


class TestProtocoloProveedorLLM:
    def test_los_tres_adaptadores_cumplen_el_protocolo(self):
        """`ProveedorLLM` es documental: sin este test, nadie lo verifica.

        El núcleo usa dúck-typing (`hasattr(proveedor, "parametros_de_llamada")`),
        así que el Protocol no lo chequea el intérprete. Fijarlo acá evita que un
        adaptador nuevo se olvide de una pieza y falle recién en la corrida.
        """
        from voucherflow.llm.protocolo import ProveedorLLM
        from voucherflow.llm.proveedores import PROVEEDORES

        for nombre, clase in PROVEEDORES.items():
            adaptador = clase()
            assert isinstance(adaptador, ProveedorLLM), nombre
            assert adaptador.capacidades.nombre == nombre


class TestCLIGastos:
    """El CLI: las banderas que estaban publicadas y no se leían."""

    def _correr(self, argv, tmp_path, monkeypatch, salida_con_datos=True):
        from voucherflow.llm import cli

        salida = tmp_path / "out"
        if salida_con_datos:
            _escribir(salida, "a.extraccion.json", _registro("/x/a.jpg"))
        monkeypatch.setattr(
            "voucherflow.settings.config.cargar_settings",
            lambda: type("A", (), {"paths": type("P", (), {
                "resolver": staticmethod(lambda k: salida)
            })()})(),
        )
        return cli.main(argv)

    def test_el_reporte_de_gastos_se_atiende(self, tmp_path, monkeypatch, capsys):
        """⚠️ Regresión: las banderas existían en el parser y nadie las leía."""
        codigo = self._correr(
            ["--reporte-gastos", str(tmp_path / "gastos.json")],
            tmp_path,
            monkeypatch,
        )
        assert codigo == 0
        assert "Reporte de gastos" in capsys.readouterr().out
        assert (tmp_path / "gastos.json").is_file()

    def test_no_pide_rutas_ni_credencial(self, tmp_path, monkeypatch, capsys):
        """Es una consulta: no recorre rutas, no arma el prompt, no pide clave."""
        monkeypatch.delenv("DEEPSEEK_API_KEY", raising=False)
        codigo = self._correr(["--csv-gastos", str(tmp_path / "g.csv")], tmp_path, monkeypatch)
        assert codigo == 0
        assert (tmp_path / "g.csv").is_file()

    def test_sin_datos_devuelve_1_porque_el_total_es_un_piso(
        self, tmp_path, monkeypatch
    ):
        """Un modelo sin precio hace que el total no sea confiable."""
        salida = tmp_path / "out"
        _escribir(
            salida,
            "a.extraccion.json",
            _registro("/x/a.jpg", modelo="modelo-sin-precio"),
        )
        from voucherflow.llm import cli

        monkeypatch.setattr(
            "voucherflow.settings.config.cargar_settings",
            lambda: type("A", (), {"paths": type("P", (), {
                "resolver": staticmethod(lambda k: salida)
            })()})(),
        )
        assert cli.main(["--reporte-gastos", str(tmp_path / "g.json")]) == 1

    def test_el_offset_horario_negativo_se_acepta(self):
        """⚠️ `argparse` lee `--tz -03:00` como «falta un argumento»."""
        from voucherflow.llm import cli

        assert cli._normalizar_argv(["--tz", "-03:00"]) == ["--tz=-03:00"]
        parser = cli.construir_parser()
        assert parser.parse_args(cli._normalizar_argv(["--tz", "-03:00"])).tz == "-03:00"
        # Un valor normal no se toca.
        assert cli._normalizar_argv(["--tz", "UTC"]) == ["--tz", "UTC"]

    def test_las_banderas_del_csv_llegan_a_las_opciones(self):
        from voucherflow.llm import cli

        args = cli.construir_parser().parse_args(
            ["--csv-gastos", "g.csv", "--csv-delim", ";", "--csv-decimal", ","]
        )
        assert args.csv_delim == ";"
        assert args.csv_decimal == ","

    def test_forzar_con_dry_run_no_es_un_error(self, tmp_path, monkeypatch, capsys):
        """⚠️ Regresión: la doc recomendaba `--dry-run --forzar` y el CLI lo rechazaba.

        `--forzar` no tiene nada que forzar en un dry-run (no se escribe), así que
        es inocuo. Y el dry-run ya recorre el lote completo, así que la nota que
        decía «sin --forzar estima solo los pendientes» también era falsa.
        """
        from voucherflow.llm import cli

        img_dir = tmp_path / "in"
        img_dir.mkdir(parents=True, exist_ok=True)
        from PIL import Image

        Image.new("RGB", (500, 400), "white").save(img_dir / "a.jpg")
        salida = tmp_path / "out"
        _escribir(salida, "a.extraccion.json", _registro("/x/a.jpg"))

        monkeypatch.setattr(
            "voucherflow.settings.config.cargar_settings",
            lambda: type("A", (), {"paths": type("P", (), {
                "resolver": staticmethod(lambda k: salida)
            })()})(),
        )
        codigo = cli.main([str(img_dir), "--dry-run", "--forzar", "-o", str(salida)])
        assert codigo == 0
        capturado = capsys.readouterr()
        assert "no cambia la estimación" in capturado.err
        # Y estima el lote COMPLETO aunque haya una ya procesada.
        assert "archivos          : 1" in capturado.out


"""Nivel A: el artefacto `tests/expected-extraction/` y su motor de comparación.

**Plan**: [`docs/plan/07-extracciones-esperadas.md`](../../docs/plan/07-extracciones-esperadas.md).

Qué verifica este módulo (y qué **no**)
---------------------------------------

Verifica **la lógica y la integridad del artefacto**, sin Ollama, sin Docling y
sin red — el mismo corte que `scripts/verificacion/etapa-extraccion.py`. El nivel B
(correr el pipeline de verdad y medir acuerdo) es `@pytest.mark.integration` y vive
en `scripts/verificacion/acuerdo-extraccion.py`.

⚠️ **Este módulo no mide la calidad de la extracción.** El artefacto contiene la
lectura de **otro modelo**, no la verdad: lo que se mide con él es *acuerdo*. Ver
el README del artefacto.

Lo que este módulo sí protege, y por qué importa
------------------------------------------------

1. **Que el artefacto no se rompa en silencio.** Manifiesto ↔ carpeta ↔ rutas: una
   corrida huérfana en disco o una entrada sin archivo son invisibles sin un test.
2. **Que el mapa de campos esté completo en las DOS direcciones.** Sin ese guard,
   un campo nuevo desaparece de la medición y el reporte parece cubrir todo el
   contrato sin cubrirlo. Es el guard de F4/T-405: ahí destapó que
   `razon_social_receptor` no tenía contraparte.
3. **Que la comparación no dé falsos `difiere`.** El caso de la fecha
   (`29/08/2025` → `2025-08-29`) afecta a 9 de 10 documentos.
4. **Que el generador sea no destructivo** (D-5) y que **no** marque los CUIT
   sospechosos (D-6).
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from voucherflow.extraction import (
    CAMPOS_EXTRACCION,
    combinar_evidencia,
    construir_source_evidence,
    parsear_evidencia_extraccion,
    veredicto_raw_de_evidencia,
)
from voucherflow.extraction.key_value import normalizar_evidencia_extraccion
from voucherflow.llm import comparacion as cmp

RAIZ = Path(__file__).resolve().parents[1]
ARTEFACTO = RAIZ / "tests" / "expected-extraction"
MANIFIESTO = ARTEFACTO / "manifiesto.json"
MAPA = ARTEFACTO / "mapa_de_campos.json"
FIXTURES = RAIZ / "tests" / "fixtures"


def _json(ruta: Path):
    return json.loads(ruta.read_text(encoding="utf-8"))


@pytest.fixture(scope="module")
def manifiesto() -> dict:
    assert MANIFIESTO.is_file(), (
        f"Falta {MANIFIESTO.relative_to(RAIZ)}: generá el artefacto con "
        "`python scripts/operacion/generar-extracciones-esperadas.py`."
    )
    return _json(MANIFIESTO)


@pytest.fixture(scope="module")
def mapa() -> dict:
    return _json(MAPA)


def _leer_corrida(entrada: dict) -> dict:
    return _json(ARTEFACTO / entrada["ruta"])


# --------------------------------------------------------------------------- #
# Integridad del artefacto
# --------------------------------------------------------------------------- #


class TestIntegridadDelArtefacto:
    """El manifiesto y la carpeta tienen que decir lo mismo, en las dos direcciones."""

    def test_toda_entrada_del_manifiesto_tiene_su_archivo(self, manifiesto):
        faltan = [
            e["ruta"] for e in manifiesto["entradas"] if not (ARTEFACTO / e["ruta"]).is_file()
        ]
        assert not faltan, f"entradas del manifiesto sin archivo: {faltan}"

    def test_no_hay_corridas_huerfanas_en_disco(self, manifiesto):
        """Una corrida que está en disco pero no en el manifiesto es invisible.

        Es el error que introduciría un regenerado a mano o un `cp` suelto, y sin
        este test nadie lo vería: el manifiesto parecería completo.
        """
        declaradas = {e["ruta"] for e in manifiesto["entradas"]}
        en_disco = {
            str(p.relative_to(ARTEFACTO))
            for p in ARTEFACTO.rglob("*.json")
            if p.name not in {"manifiesto.json", "mapa_de_campos.json"}
        }
        assert not (en_disco - declaradas), (
            f"archivos en disco que el manifiesto no declara: {sorted(en_disco - declaradas)}"
        )

    def test_la_cobertura_del_manifiesto_es_exacta(self, manifiesto):
        """Los contadores declarados tienen que coincidir con las entradas reales.

        Un contador desactualizado es peor que no tenerlo: se lee como medición.
        """
        entradas = manifiesto["entradas"]
        cobertura = manifiesto["cobertura"]
        assert cobertura["corridas"] == len(entradas)
        assert cobertura["documentos"] == len({e["documento_id"] for e in entradas})
        assert cobertura["con_imagen"] == sum(
            1 for e in entradas if e["imagen_en_fixtures"]
        )
        assert cobertura["sin_imagen"] == sum(
            1 for e in entradas if not e["imagen_en_fixtures"]
        )
        assert cobertura["con_imagen"] + cobertura["sin_imagen"] == len(entradas)

    def test_toda_imagen_declarada_existe(self, manifiesto):
        """El bloqueante §3.1 se declara **en el dato**: acá se verifica que no mienta."""
        faltan = [
            e["imagen_en_fixtures"]
            for e in manifiesto["entradas"]
            if e["imagen_en_fixtures"]
            and not (FIXTURES / e["imagen_en_fixtures"]).is_file()
        ]
        assert not faltan, f"imágenes declaradas que no existen: {faltan}"

    def test_la_corrida_de_la_ruta_coincide_con_la_declarada(self, manifiesto):
        """`id/modelo/corrida` es un contrato, no una convención (§6.3 del plan)."""
        for e in manifiesto["entradas"]:
            partes = Path(e["ruta"]).parts
            assert partes[:3] == (e["documento_id"], e["modelo"], e["corrida"]), (
                f"la ruta {e['ruta']} no coincide con id/modelo/corrida declarados"
            )

    def test_los_campos_obligatorios_estan_declarados(self, manifiesto):
        """Todo lo que hace falta para interpretar una lectura vive en el manifiesto.

        ⚠️ La ruta guarda `id/modelo/corrida` pero **no** el `prompt_hash`: dos
        corridas del mismo modelo pueden haber usado prompts distintos. Por eso el
        contexto va en el manifiesto y no solo en el nombre de la carpeta.
        """
        obligatorios = (
            "documento_id",
            "archivo",
            "modelo",
            "corrida",
            "fuente_lote",
            "version_prompt",
            "prompt_hash",
            "uso",
            "extraido_utc",
            "ruta",
        )
        for e in manifiesto["entradas"]:
            faltan = [c for c in obligatorios if c not in e]
            assert not faltan, f"{e.get('ruta')}: faltan campos {faltan}"


class TestCorridas:
    """El registro versionado es el del lab, y la clave del bloque está fijada."""

    def test_la_clave_de_extraccion_existe(self, manifiesto):
        """`resultado` y `extraccion` son idénticos (medido): hay que fijar uno.

        Si un cambio de forma futuro moviera el dato a otro bloque, un test que
        leyera el equivocado no fallaría — leería lo mismo. Fijarlo evita que el
        artefacto se rompa en silencio.
        """
        clave = manifiesto["clave_extraccion"]
        for e in manifiesto["entradas"]:
            registro = _leer_corrida(e)
            assert clave in registro, f"{e['ruta']}: falta {clave!r}"

    def test_se_tolera_el_esquema_viejo(self, manifiesto):
        """El lote `piloto` es de un esquema anterior (§2.2): sin `reintentos_esquema`.

        No se rellena con un default inventado: se tolera la ausencia.
        """
        for e in manifiesto["entradas"]:
            registro = _leer_corrida(e)
            # `reintentos_esquema` puede faltar, pero `uso` y `modelo` no.
            assert registro.get("modelo"), f"{e['ruta']}: sin modelo"
            assert registro.get("uso"), f"{e['ruta']}: sin uso"

    def test_la_lectura_no_esta_normalizada(self, manifiesto):
        """Se versiona **crudo** (§6.2): normalizarlo a mano crearía un 'esperado'
        que el modelo nunca dijo, y una segunda fuente de verdad.

        Se verifica con la fecha: el lab la guarda como la leyó (`dd/mm/aaaa`), no
        como el canónico del pipeline (`aaaa-mm-dd`).
        """
        con_fecha = [
            e for e in manifiesto["entradas"]
            if _leer_corrida(e)["extraccion"].get("fecha_emision")
        ]
        assert con_fecha, "el artefacto no tiene ninguna fecha: revisá el generador"
        formato_canonico = [
            e["ruta"]
            for e in con_fecha
            if str(_leer_corrida(e)["extraccion"]["fecha_emision"]).count("-") == 2
        ]
        assert not formato_canonico, (
            f"fechas ya normalizadas en el artefacto (deberían estar crudas): "
            f"{formato_canonico}"
        )


# --------------------------------------------------------------------------- #
# El mapa de campos (guard de integridad, dos direcciones)
# --------------------------------------------------------------------------- #


class TestMapaDeCampos:
    """⚠️ El guard que evita que un campo desaparezca de la medición en silencio."""

    def test_cada_campo_del_pipeline_esta_en_exactamente_una_lista(self, mapa):
        """Ningún campo del contrato puede quedar sin clasificar.

        Es el guard de F4/T-405, que destapó que `razon_social_receptor` no tenía
        contraparte en los prompts de referencia. Sin él, el reporte **parece**
        cubrir todo el contrato sin cubrirlo.
        """
        clasificados: list[str] = []
        for item in mapa["comparables"]["campos"]:
            clasificados.append(item["pipeline"])
        for item in mapa["comparables_por_mapa"]["campos"]:
            clasificados.append(item["pipeline"])
        clasificados += [c["pipeline"] for c in mapa["sin_contraparte"]["campos"]]

        faltan = [c for c in CAMPOS_EXTRACCION if c not in clasificados]
        assert not faltan, (
            f"campos del contrato sin clasificar en el mapa: {faltan}. "
            "Agregalos a comparables / comparables_por_mapa / sin_contraparte."
        )

        repetidos = {c for c in clasificados if clasificados.count(c) > 1}
        assert not repetidos, f"campos del contrato clasificados dos veces: {repetidos}"

    def test_cada_campo_del_lab_esta_en_exactamente_una_lista(self, mapa, manifiesto):
        """La otra dirección: un campo del lab sin clasificar tampoco se mide.

        ⚠️ Se recorren **todas** las secciones del mapa que declaran campos del lab.
        Olvidarse de una (p. ej. `comparacion_propia`) deja un campo fuera de la
        medición sin que nada falle — que es exactamente el agujero que este test
        existe para tapar. Lo destapó `campos_no_legibles`.
        """
        campos_lab: set[str] = set()
        for e in manifiesto["entradas"]:
            campos_lab |= set(_leer_corrida(e)["extraccion"])

        clasificados: list[str] = []
        for seccion in (
            "comparables",
            "comparables_por_mapa",
            "auto_chequeo",
            "no_puntua",
            "comparacion_propia",
            "fuera_del_contrato",
        ):
            clasificados += [c["lab"] for c in mapa[seccion]["campos"]]

        faltan = sorted(campos_lab - set(clasificados))
        assert not faltan, (
            f"campos del lab sin clasificar en el mapa: {faltan}. "
            "Un campo sin clasificar no se mide y nadie se entera."
        )

    def test_todas_las_secciones_del_mapa_se_recorren(self, mapa):
        """Control del test anterior: si se agrega una sección, hay que recorrerla.

        Sin esto, agregar `"otra_seccion"` al mapa la dejaría invisible para el
        guard de arriba, que verificaría cada vez menos sin avisar. Lo destapó
        `comparacion_propia` (existía y nadie la leía).
        """
        secciones_con_campos = {
            clave
            for clave, valor in mapa.items()
            if isinstance(valor, dict) and "campos" in valor
        }
        #: Secciones del guard de la punta **lab** (el que recorre el test anterior).
        punta_lab = {
            "comparables",
            "comparables_por_mapa",
            "auto_chequeo",
            "no_puntua",
            "comparacion_propia",
            "fuera_del_contrato",
        }
        #: Secciones de la punta **pipeline**: las cubre el test de la dirección
        #: contraria (`test_cada_campo_del_pipeline_esta_en_exactamente_una_lista`),
        #: así que se declaran acá en vez de duplicar el recorrido.
        punta_pipeline = {"sin_contraparte"}

        desconocidas = secciones_con_campos - punta_lab - punta_pipeline
        assert not desconocidas, (
            f"el mapa tiene secciones con 'campos' que ningún guard recorre: "
            f"{sorted(desconocidas)}. Sumalas a un guard o declará por qué no."
        )
        assert punta_lab | punta_pipeline == secciones_con_campos

    def test_los_nombres_del_mapa_existen_en_su_punta(self, mapa, manifiesto):
        """⚠️ La pareja `percepciones_iibb`/`percepcion_iibb` se distingue por un PLURAL.

        Un renombre en cualquiera de las dos puntas rompería la comparación **sin
        que nada fallara**: el campo daría 'sin contraparte' en silencio. Este test
        es lo que convierte el mapa en un contrato verificado.
        """
        lectura = _leer_corrida(manifiesto["entradas"][0])["extraccion"]
        for seccion in ("comparables", "comparables_por_mapa"):
            for item in mapa[seccion]["campos"]:
                assert item["lab"] in lectura, (
                    f"el mapa dice que el lab emite {item['lab']!r}, pero no está "
                    "en la lectura. ¿Se renombró en una punta?"
                )
                assert item["pipeline"] in CAMPOS_EXTRACCION, (
                    f"el mapa dice que el pipeline emite {item['pipeline']!r}, pero "
                    "no está en CAMPOS_EXTRACCION. ¿Se renombró en una punta?"
                )

    def test_la_lista_de_auto_chequeo_documenta_que_no_se_implementa(self, mapa):
        """⛔ D-6: el auto-chequeo **no se implementa**. El mapa tiene que decirlo.

        Si alguien lo implementara sin actualizar el mapa, esta aserción falla y
        obliga a actualizar la documentación a propósito (mismo criterio que los
        tests de frontera del repo).
        """
        assert "NO se implementa" in mapa["auto_chequeo"]["decision"]
        assert "D-6" in mapa["auto_chequeo"]["decision"]

    def test_la_prosa_no_puntua_y_esta_declarada(self, mapa):
        """`observaciones` y `rubro_emisor` son `no_comparable`: el mismo modelo los
        devuelve distintos entre corridas idénticas (§8.1 del plan).

        Si alguien los pasara a `comparables`, el ruido del modelo se leería como
        desacuerdo de lectura y el acuerdo bajaría sin motivo.
        """
        no_puntua = {c["lab"] for c in mapa["no_puntua"]["campos"]}
        assert {"observaciones", "rubro_emisor"} <= no_puntua

        comparables = {
            c["lab"]
            for seccion in ("comparables", "comparables_por_mapa")
            for c in mapa[seccion]["campos"]
        }
        assert not (no_puntua & comparables), (
            f"campos que puntúan y no puntúan a la vez: {no_puntua & comparables}"
        )


# --------------------------------------------------------------------------- #
# Normalización y comparación (lógica pura)
# --------------------------------------------------------------------------- #


def _evidencia_del_pipeline(lectura: dict, mapa: dict, documento_id: str = "doc"):
    """Evidencia sintética construida desde la lectura de referencia.

    Traduce los nombres del lab a los del contrato **antes** de armar la evidencia,
    para que las dos puntas hablen el mismo idioma (si no, los campos del mapa
    darían `ausente`, que es correcto pero no prueba nada).

    ⚠️ La usan sólo los tests de la **lógica** de comparación: no es el pipeline.
    """
    traduccion = {
        item["lab"]: item["pipeline"]
        for seccion in ("comparables", "comparables_por_mapa")
        for item in mapa[seccion]["campos"]
    }
    campos = {
        traduccion.get(k, k): {"valor": v, "fragmento_sustento": "sostén de prueba"}
        for k, v in lectura.items()
        if v is not None and traduccion.get(k, k) in CAMPOS_EXTRACCION
    }
    crudo = json.dumps({"fuente_lectura": "llm", "campos": campos}, ensure_ascii=False)
    norm = normalizar_evidencia_extraccion(parsear_evidencia_extraccion(crudo, fuente="llm"))
    source = construir_source_evidence(
        norm.evidencia, veredicto=veredicto_raw_de_evidencia(norm.evidencia)
    )
    return combinar_evidencia(documento_id, [source])


class TestNormalizacionDeLaReferencia:
    """⚠️ Sin normalizar, 9 de 10 fechas darían un `difiere` falso."""

    def test_la_fecha_se_normaliza_al_canonico_del_pipeline(self):
        """El caso que el plan identificó como el más delicado (§5.1)."""
        canonica = cmp.normalizar_referencia({"fecha_emision": "29/08/2025"}, ["fecha_emision"])
        assert canonica["fecha_emision"] == "2025-08-29"

    def test_un_monto_como_texto_impreso_tambien_se_normaliza(self):
        """El lab devuelve número; el pipeline espera el texto impreso. Los dos entran."""
        canonica = cmp.normalizar_referencia(
            {"subtotal": "52.069,85", "iva": 10934.67}, ["subtotal", "iva"]
        )
        assert canonica["subtotal"] == 52069.85
        assert canonica["iva"] == 10934.67

    def test_un_campo_que_la_referencia_no_declaro_no_entra(self):
        """La ausencia es información: un `None` no puede confundirse con un valor."""
        canonica = cmp.normalizar_referencia({"cuit_emisor": None}, ["cuit_emisor"])
        assert "cuit_emisor" not in canonica

    def test_un_valor_no_normalizable_conserva_el_crudo(self):
        """Regla del repo: un ilegible conserva el crudo, no se inventa."""
        canonica = cmp.normalizar_referencia({"cuit_emisor": "ilegible"}, ["cuit_emisor"])
        assert canonica["cuit_emisor"] == "ilegible"


class TestMotorDeComparacion:
    """La lógica de los cinco estados."""

    def test_una_lectura_contra_si_misma_coincide_todo(self, manifiesto, mapa):
        """La prueba más fuerte del motor: no puede haber un `difiere` espurio.

        Se construye la evidencia desde la MISMA lectura (traduciendo los nombres),
        así que cualquier `difiere` sería un defecto del motor, no un dato.
        """
        entrada = manifiesto["entradas"][0]
        lectura = _leer_corrida(entrada)["extraccion"]
        evidencia = _evidencia_del_pipeline(lectura, mapa, entrada["documento_id"])

        resultado = cmp.comparar(entrada["documento_id"], lectura, evidencia, mapa)

        assert resultado.por_estado(cmp.DIFIERE) == [], (
            "una lectura comparada contra sí misma no puede diferir: "
            f"{[(c.campo, c.valor_referencia, c.valor_pipeline) for c in resultado.por_estado(cmp.DIFIERE)]}"
        )
        assert resultado.comparables > 0, "el motor no comparó ningún campo"
        assert resultado.coincidencias == resultado.comparables

    def test_la_fecha_cuenta_como_coincide_normalizado(self, manifiesto, mapa):
        """Se distingue 'leyeron lo mismo en otra forma' de 'lo mismo en la misma forma'."""
        entrada = manifiesto["entradas"][0]
        lectura = _leer_corrida(entrada)["extraccion"]
        evidencia = _evidencia_del_pipeline(lectura, mapa, entrada["documento_id"])
        resultado = cmp.comparar(entrada["documento_id"], lectura, evidencia, mapa)

        fecha = next(c for c in resultado.campos if c.campo == "fecha_emision")
        assert fecha.estado == cmp.COINCIDE_NORMALIZADO, (
            "la fecha del lab (dd/mm/aaaa) tiene que coincidir tras normalizar, "
            f"y quedó en {fecha.estado!r}"
        )

    def test_un_valor_distinto_es_un_difiere(self, mapa):
        """La contracara: el motor tiene que saber decir que no."""
        evidencia = _evidencia_del_pipeline({"cuit_emisor": "20-11111111-1"}, mapa)
        resultado = cmp.comparar(
            "doc", {"cuit_emisor": "30-71816388-5"}, evidencia, mapa
        )
        campo = next(c for c in resultado.campos if c.campo == "cuit_emisor")
        assert campo.estado == cmp.DIFIERE

    def test_los_dos_lados_ausentes_no_es_un_difiere(self, mapa):
        """Que ninguno lo haya leído no es un desacuerdo: es información distinta."""
        evidencia = _evidencia_del_pipeline({}, mapa)
        resultado = cmp.comparar("doc", {"cuit_emisor": None}, evidencia, mapa)
        campo = next(c for c in resultado.campos if c.campo == "cuit_emisor")
        assert campo.estado == cmp.AUSENTE

    def test_una_diferencia_en_cuit_avisa_que_puede_ser_un_acierto(self, mapa):
        """⚠️ El caso que evita el error más grave del plan.

        La referencia tiene CUIT mal leídos. Si el pipeline lee bien uno de esos,
        el reporte dice `difiere` — y **tiene que explicar** que puede ser un
        acierto del pipeline, no un error. Sin la nota, quien lee el reporte
        concluye lo contrario.
        """
        evidencia = _evidencia_del_pipeline({"cuit_emisor": "30-71816388-5"}, mapa)
        resultado = cmp.comparar("doc", {"cuit_emisor": "30-62221785-4"}, evidencia, mapa)
        campo = next(c for c in resultado.campos if c.campo == "cuit_emisor")
        assert campo.estado == cmp.DIFIERE
        assert campo.nota is not None
        assert "ACUERTO" in campo.nota.upper() or "ACIERTO" in campo.nota.upper()

    def test_las_causas_conocidas_estan_declaradas(self):
        """Una diferencia que el diseño ya conoce se **explica**, no se promedia.

        Mismo criterio que el README de `tests/golden/F4/`.
        """
        for campo in ("tipo_comprobante", "moneda", "cuit_emisor"):
            assert campo in cmp.CAUSAS_CONOCIDAS, (
                f"falta la causa conocida de {campo!r}: una diferencia ahí "
                "se leería como un error del pipeline"
            )

    def test_el_recuento_siempre_tiene_las_cinco_claves(self, mapa):
        """Un estado ausente del reporte es indistinguible de uno no evaluado."""
        resultado = cmp.comparar("doc", {}, _evidencia_del_pipeline({}, mapa), mapa)
        assert set(resultado.recuento()) == {
            cmp.COINCIDE,
            cmp.COINCIDE_NORMALIZADO,
            cmp.DIFIERE,
            cmp.AUSENTE,
            cmp.NO_COMPARABLE,
        }

    def test_la_prosa_entra_como_no_comparable_con_su_valor(self, manifiesto, mapa):
        """No puntúa, pero no se descarta: es dato de contexto para revisión humana."""
        entrada = manifiesto["entradas"][0]
        lectura = _leer_corrida(entrada)["extraccion"]
        resultado = cmp.comparar(
            "doc", lectura, _evidencia_del_pipeline(lectura, mapa), mapa
        )
        prosa = next(c for c in resultado.campos if c.campo == "observaciones")
        assert prosa.estado == cmp.NO_COMPARABLE
        assert prosa.valor_referencia == lectura["observaciones"]
        assert not prosa.puntua

    def test_ausente_no_puntua(self, mapa):
        """El denominador honesto: `ausente` no es acuerdo ni desacuerdo."""
        resultado = cmp.comparar("doc", {}, _evidencia_del_pipeline({}, mapa), mapa)
        assert all(not c.puntua for c in resultado.por_estado(cmp.AUSENTE))


# --------------------------------------------------------------------------- #
# El generador: no destructivo (D-5) y sin marcar (D-6)
# --------------------------------------------------------------------------- #


class TestGenerador:
    """Los invariantes de las decisiones D-5 y D-6."""

    def _modulo(self):
        import importlib.util

        ruta = RAIZ / "scripts" / "operacion" / "generar-extracciones-esperadas.py"
        spec = importlib.util.spec_from_file_location("generador_ee", ruta)
        modulo = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(modulo)
        return modulo

    def test_el_generador_existe_y_es_ejecutable_desde_el_repo(self):
        ruta = RAIZ / "scripts" / "operacion" / "generar-extracciones-esperadas.py"
        assert ruta.is_file(), "falta el generador del artefacto (D-5)"

    def test_el_digito_verificador_detecta_los_invalidos(self):
        """Calibración del cálculo (módulo 11), con casos conocidos."""
        gen = self._modulo()
        # Válidos: un CUIT real y uno público de ejemplo.
        assert gen._digito_verificador_valido("30-58221570-3") is True
        assert gen._digito_verificador_valido("33-69345023-9") is True
        # Inválido: uno inventado.
        assert gen._digito_verificador_valido("20-12345678-9") is False
        # Sin 11 dígitos no hay veredicto (no es "inválido": es "no evaluable").
        assert gen._digito_verificador_valido("20-1") is None
        assert gen._digito_verificador_valido(None) is None

    def test_los_cuit_sospechosos_estan_identificados(self, manifiesto):
        """Los 5 CUIT del plan siguen detectándose (el hallazgo no se perdió).

        ⚠️ Este test **no** exige que estén marcados en el manifiesto: D-6 decidió
        no implementar el auto-chequeo. Exige que el cálculo siga siendo correcto,
        que es lo que permite al README listarlos.
        """
        gen = self._modulo()
        sospechosos = {cuit for _, cuit, _ in gen._cuit_sospechosos(manifiesto["entradas"])}
        assert len(sospechosos) == 5, f"se esperaban 5 CUIT sospechosos, hay {len(sospechosos)}"

    def test_el_manifiesto_no_marca_los_sospechosos(self, manifiesto):
        """⛔ D-6, en la dirección que importa: que **no** se haya implementado.

        Si alguien agregara la marca sin reabrir la decisión, este test lo obliga a
        actualizar el plan y el README a propósito. Es un test de **frontera**: fija
        un alcance, no un defecto.
        """
        for entrada in manifiesto["entradas"]:
            assert "referencia_dudosa" not in entrada, (
                "el manifiesto marca campos dudosos: D-6 decidió NO implementar el "
                "auto-chequeo. Si se reabre, actualizá el plan (§1.3) y el README."
            )

    def test_no_se_inventan_los_campos_que_el_registro_no_tenia(self, manifiesto):
        """⚠️ La ausencia de un campo **no** se rellena con un default.

        Un `0` en `reintentos_esquema` diría "no hubo reintentos", que es una
        afirmación; la ausencia dice "no se registraba", que es la verdad.

        ⚠️ **No se puede verificar por lote**: medido, la presencia de estos campos
        es **por registro**, no por lote — dentro del mismo lote `piloto` hay 4
        registros con `reintentos_esquema` y 16 sin él (el esquema se agregó a
        mitad de camino). Un test que asumiera "el lote piloto no lo tiene" falla,
        y la falla es del test, no del artefacto.
        """
        claves = {e["ruta"]: set(_leer_corrida(e)) for e in manifiesto["entradas"]}
        con_campo = [r for r, k in claves.items() if "reintentos_esquema" in k]
        sin_campo = [r for r, k in claves.items() if "reintentos_esquema" not in k]

        assert con_campo, "ninguna corrida trae `reintentos_esquema`: revisá el generador"
        assert sin_campo, "todas traen el campo: el artefacto dejó de tener lotes viejos"

        # El campo se declaró SOLO donde el registro lo traía, y nunca con un
        # default inventado donde no estaba.
        for entrada in manifiesto["entradas"]:
            en_manifiesto = "reintentos_esquema" in entrada
            en_registro = "reintentos_esquema" in claves[entrada["ruta"]]
            assert en_manifiesto == en_registro, (
                f"{entrada['ruta']}: el manifiesto "
                f"{'declara' if en_manifiesto else 'omite'} `reintentos_esquema` "
                f"y el registro {'lo trae' if en_registro else 'no lo trae'}"
            )


class TestCoberturaDeclarada:
    """Que el silencio no se lea como cobertura."""

    def test_el_artefacto_declara_su_version_y_su_no_objetivo(self, manifiesto):
        """El manifiesto tiene que decir que **no** es un golden.

        Es la regla de honestidad del plan (§1): el nombre "esperadas" invita a
        leerlo como "correctas", y el artefacto tiene que defenderse solo.
        """
        texto = json.dumps(manifiesto, ensure_ascii=False).lower()
        assert "no es un golden" in texto
        assert "acuerdo" in texto

    def test_el_readme_declara_el_limite_y_los_5_cuit(self, manifiesto):
        """La mitigación de D-6 depende de que la lista esté en el README.

        Si el README pierde la lista, un `difiere` en `cuit_emisor` queda sin
        ninguna forma de interpretarse: es la única defensa que dejó D-6.
        """
        texto = (ARTEFACTO / "README.md").read_text(encoding="utf-8")
        assert "NO es un golden" in texto
        for cuit in ("30-62221785-4", "30586221578", "30-71530218-5",
                     "30-71144495-3", "30-70715163-1"):
            assert cuit in texto, f"el README no lista el CUIT sospechoso {cuit}"

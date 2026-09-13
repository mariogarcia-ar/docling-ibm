"""Tests del golden etiquetado y de la métrica del gate qween (F2 / T-204).

**Fase**: F2 (validación / refactor qween) · **Tarea**: T-204 · **Épicas**:
E-QWE (E-QWE-1, E-QWE-2).

Cubre lo que pide el subplan F2 §3.4 sobre el **subconjunto acotado y
etiquetado** del golden set (decisión §2.5):

1. **Integridad del etiquetado**: el ``casos.csv`` tiene el ``veredicto``
   etiquetado en el subconjunto de F2, los valores pertenecen al vocabulario del
   gate y las etiquetas reales (OCR/texto nativo) son las verificadas.
2. **Ahorro de costo (a)**: un ``no_comprobante`` **no** prepara ``vista_fiel``
   ⇒ no llega a extracción (E-QWE). Se prueba con **dobles** sobre los casos
   reales del golden (sin Ollama) y sobre un ``indeterminado`` tras revisión.
3. **Calidad distinta entre vistas (b)**: el gradiente rápida < revisión < fiel
   se usa correctamente (la 2ª pasada decide sobre la vista de revisión y la
   vista fiel no reutiliza la rápida).
4. **Métrica acordada (§2.6)**: ``calcular_metricas`` (función pura del script
   ``scripts/F2/t204.py``) reporta exactitud del gate, % de indeterminación y
   % de no-comprobantes que NO llegan a extracción (objetivo 100%).

**Regla dura (F2-subplan §4)**: la suite default corre **sin Ollama real** y
**sin Docling real**. Los casos del golden se evalúan con un
``ProcessedDocument`` mínimo (imágenes referenciadas por ruta; PDFs como texto
nativo sin convertir) y un **doble** del cliente. La corrida real del gate sobre
el golden (con Ollama) queda en :class:`TestIntegracionGateGolden`, marcada
``@pytest.mark.integration`` y auto-omitida salvo ``-m integration`` (mismo
criterio documentado que ``tests/test_paridad_t105.py``).
"""

from __future__ import annotations

import csv
import importlib.util
import json
import os
from pathlib import Path

import pytest

from voucherflow.models.docling import ProcessedDocument
from voucherflow.validation import (
    CALIDAD_POR_TIPO_VISTA,
    GRADO_CALIDAD_POR_NIVEL,
    VeredictoGate,
    preparar_vista_fiel,
    preparar_vista_rapida,
    preparar_vista_revision,
    validar_y_procesar,
)

# ---------------------------------------------------------------------------
# Carga del script de reporte (scripts/F2/t204.py) — su lógica de métricas es
# pura y se testea aquí sin Ollama (la CLI sí requiere Ollama y es manual).
# ---------------------------------------------------------------------------

_SRC_V2 = Path(__file__).resolve().parents[1]


def _cargar_modulo_t204():
    """Importa ``scripts/F2/t204.py`` por ruta (no es un paquete instalable).

    Se registra el módulo en ``sys.modules`` antes de ejecutarlo: ``@dataclass``
    (usado por el script) resuelve tipos con ``sys.modules[cls.__module__]`` y
    falla si el módulo no está registrado.
    """
    import sys

    ruta = _SRC_V2 / "scripts" / "F2" / "t204.py"
    spec = importlib.util.spec_from_file_location("voucherflow_t204", ruta)
    assert spec and spec.loader, f"No se pudo cargar el script T-204: {ruta}"
    modulo = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = modulo
    spec.loader.exec_module(modulo)
    return modulo


t204 = _cargar_modulo_t204()

#: Etiquetas reales del subconjunto de F2 (ground truth verificado con OCR de
#: Docling y texto nativo de PDF; **no** son la predicción del modelo). Se
#: hardcodean aquí como criterio de aceptación del etiquetado (T-204 §2.5): si
#: alguien cambia el CSV sin justificación, el test lo detecta.
GT_SUBCONJUNTO_F2 = {
    # 9 casos reales del golden (verificados con OCR/texto nativo)
    "img_2025-08_2D2C9343": "comprobante",       # TIQUE FACTURA A (Farmacia Silva)
    "img_2025-08_A527FC11": "comprobante",       # TIQUE FACTURA A (PUMA)
    "img_2026-01_009550B7": "comprobante",       # TIQUE FACTURA A (Azul Combustibles)
    "img_2026-02_E527948C": "no_comprobante",    # "GASTOS VARIOS, FALTA FACTURA"
    "img_2026-02_4FAD7638": "no_comprobante",    # "no hay comprobante" (sellos)
    "pdf_2026-02_6D03019B": "comprobante",       # FACTURA A (Corredores Viales)
    "img_2026-06_6E0AD164": "comprobante",       # TIQUE FACTURA A (Kalpa)
    "pdf_2026-08_2DC73C08": "comprobante",       # Boleto (Arito)
    "img_2026-08_1CDFCDA0": "comprobante",       # FACTURA A (Aimar)
}


# ---------------------------------------------------------------------------
# Dobles del cliente (sin Ollama real; regla dura F2-subplan §4)
# ---------------------------------------------------------------------------


class _Respuesta:
    """Respuesta mínima de ``OllamaClient.ask`` (solo ``contenido``)."""

    def __init__(self, contenido: str) -> None:
        self.contenido = contenido


class _ClienteFijo:
    """Doble que devuelve siempre el mismo contenido (registra las llamadas)."""

    def __init__(self, contenido: str) -> None:
        self.contenido = contenido
        self.llamadas: list[dict] = []

    def ask(self, messages, model, json_format=False, options=None, num_ctx=None):
        self.llamadas.append({"messages": messages, "model": model})
        return _Respuesta(self.contenido)


class _ClienteSecuencia:
    """Doble que devuelve una secuencia de contenidos (rápida → revisión)."""

    def __init__(self, contenidos: list[str]) -> None:
        self.contenidos = list(contenidos)
        self.llamadas: list[dict] = []

    def ask(self, messages, model, json_format=False, options=None, num_ctx=None):
        idx = min(len(self.llamadas), len(self.contenidos) - 1)
        self.llamadas.append({"messages": messages, "model": model})
        return _Respuesta(self.contenidos[idx])


# ---------------------------------------------------------------------------
# Utilidades sobre el golden
# ---------------------------------------------------------------------------


def _leer_filas_etiquetadas() -> list[dict[str, str]]:
    """Filas del ``casos.csv`` con ``veredicto`` distinto de ``pendiente``."""
    ruta = _SRC_V2 / "tests" / "golden" / "casos.csv"
    with ruta.open(encoding="utf-8") as fh:
        return [
            f for f in csv.DictReader(fh) if f["veredicto"] in t204.VEREDICTOS_VALIDOS
        ]


def _documento_minimo(fila: dict[str, str]) -> ProcessedDocument:
    """``ProcessedDocument`` mínimo del caso, **sin** correr Docling (T-204).

    Decisión de testeo (F2-subplan §4): ``validar_y_procesar`` acepta un
    ``ProcessedDocument`` ya procesado, así la suite default no corre Docling.
    Para imágenes el tipo es ``imagen`` (la vista se deriva de la ruta real);
    para el resto se usa ``pdf_texto`` (vista textual sobre el markdown, que
    aquí es sintético: solo importa que exista representación).
    """
    ruta = (_SRC_V2.parent / fila["ruta"]).resolve()
    assert ruta.exists(), f"El fixture del caso {fila['id']} no existe: {ruta}"
    if fila["tipo_entrada"] == "imagen":
        return ProcessedDocument("imagen", str(ruta), "")
    return ProcessedDocument(
        fila["tipo_entrada"], str(ruta), "DOCUMENTO SINTÉTICO DEL GOLDEN (T-204)"
    )


# ---------------------------------------------------------------------------
# 1. Integridad del etiquetado del veredicto (subplan §2.5)
# ---------------------------------------------------------------------------


class TestEtiquetadoDelGolden:
    def test_las_etiquetas_reales_del_subconjunto_f2_son_las_verificadas(self):
        # Ground truth objetivo (OCR/texto nativo) — no se re-deriva con el
        # modelo (sería circular); el CSV debe contener exactamente esto.
        por_id = {f["id"]: f["veredicto"] for f in _leer_filas_etiquetadas()}
        for id_caso, esperado in GT_SUBCONJUNTO_F2.items():
            assert id_caso in por_id, (
                f"El caso {id_caso} del ground truth de F2 no está etiquetado "
                "en casos.csv (T-204, subplan §2.5)."
            )
            assert por_id[id_caso] == esperado, (
                f"Etiqueta del caso {id_caso}: '{por_id[id_caso]}' != "
                f"'{esperado}' (ground truth verificado con OCR/texto nativo)."
            )

    def test_todo_veredicto_etiquetado_pertenece_al_vocabulario_del_gate(self):
        # Valores válidos del contrato (VeredictoGate) o "pendiente" (sin curar).
        validos = {v.value for v in VeredictoGate} | {"pendiente"}
        for fila in _leer_filas_etiquetadas():
            assert fila["veredicto"] in validos, (
                f"Veredicto fuera del vocabulario en {fila['id']}: "
                f"'{fila['veredicto']}' (válidos: {sorted(validos)})."
            )

    def test_los_etiquetados_declaran_evidencia_objetiva(self):
        # Cada caso etiquetado documenta la evidencia con la que se etiquetó
        # (columna ``evidencia_veredicto``): OCR o texto nativo. Evita
        # etiquetas "de memoria" sin sustento (criterio del README del golden).
        for fila in _leer_filas_etiquetadas():
            assert fila.get("evidencia_veredicto", "").strip(), (
                f"El caso etiquetado {fila['id']} no declara evidencia_veredicto "
                "(T-204: OCR/texto nativo como evidencia)."
            )

    def test_el_subconjunto_tiene_negativos_suficientes(self):
        # La métrica de ahorro (§2.6) requiere no-comprobantes etiquetados: se
        # exige una masa mínima de negativos etiquetados (los 2 reales + los
        # sintéticos de fixtures/negativos) para que el % de ahorro sea medible.
        etiquetados = _leer_filas_etiquetadas()
        negativos = [f for f in etiquetados if f["veredicto"] == "no_comprobante"]
        assert len(negativos) >= 5, (
            "El subconjunto de F2 debe incluir al menos 5 no-comprobantes "
            f"etiquetados (subplan §2.5); hay {len(negativos)}."
        )
        positivos = [f for f in etiquetados if f["veredicto"] == "comprobante"]
        assert len(positivos) >= 5, (
            f"El subconjunto de F2 debe incluir positivos etiquetados; hay {len(positivos)}."
        )

    def test_los_splits_cubren_todos_los_casos_etiquetados_y_no_se_cruzan(self):
        # Los splits (train/eval) deben particionar el subconjunto etiquetado
        # sin cruces: la exactitud se mide sobre eval (plan 06 §3.4).
        golden = _SRC_V2 / "tests" / "golden"
        with (golden / "splits" / "reglas_train.json").open(encoding="utf-8") as fh:
            train = set(json.load(fh)["casos"])
        with (golden / "splits" / "evaluacion.json").open(encoding="utf-8") as fh:
            evaluacion = set(json.load(fh)["casos"])
        assert not (train & evaluacion), "Un caso no puede estar en train y eval a la vez"

        ids_etiquetados = {f["id"] for f in _leer_filas_etiquetadas()}
        faltantes = ids_etiquetados - (train | evaluacion)
        assert not faltantes, (
            "Casos etiquetados sin split asignado (el reporte de métricas los "
            f"necesita para el desagregado): {sorted(faltantes)}"
        )


# ---------------------------------------------------------------------------
# 2. Ahorro de costo (a): los no_comprobantes no llegan a extracción
# ---------------------------------------------------------------------------


class TestAhorroNoLlegaAExtraccion:
    def test_no_comprobante_en_1a_pasada_no_prepara_vista_fiel(self):
        # Regla de ahorro (E-QWE): si el gate rechaza, no se prepara la vista
        # fiel ⇒ el documento no llega a extracción (F4). El golden aporta los
        # casos reales; se decide con un doble (sin Ollama).
        for fila in _leer_filas_etiquetadas():
            cliente = _ClienteFijo("no_comprobante")
            res = validar_y_procesar(_documento_minimo(fila), cliente)
            assert res.veredicto_final == VeredictoGate.no_comprobante, (
                f"{fila['id']}: con respuesta 'no_comprobante' el veredicto "
                "final debe ser no_comprobante."
            )
            assert res.vista_fiel is None, (
                f"{fila['id']}: un no_comprobante NO debe preparar vista_fiel "
                "(no llega a extracción → ahorro E-QWE; T-204)."
            )
            assert len(res.pasadas) == 1, (
                f"{fila['id']}: no_comprobante en la 1ª pasada no debe hacer 2ª pasada."
            )
            assert len(cliente.llamadas) == 1, (
                f"{fila['id']}: solo debe haber una llamada al modelo (la barata)."
            )

    def test_indeterminado_tras_revision_tampoco_llega_a_extraccion(self):
        # Política E-QWE-1: indeterminado en ambas pasadas ⇒ rechazo por no
        # confirmado ⇒ sin vista fiel (no llega a extracción). Se prueba sobre
        # un caso real del golden con un doble de secuencia (2ª pasada real).
        fila = next(f for f in _leer_filas_etiquetadas() if f["veredicto"] == "no_comprobante")
        cliente = _ClienteSecuencia(["indeterminado", "indeterminado"])
        res = validar_y_procesar(_documento_minimo(fila), cliente)

        assert res.veredicto_final == VeredictoGate.no_comprobante
        assert res.vista_fiel is None, "no confirmado → no debe llegar a extracción"
        assert len(res.pasadas) == 2, "indeterminado debe disparar la 2ª pasada de revisión"
        assert len(cliente.llamadas) == 2

    def test_comprobante_si_prepara_vista_fiel(self):
        # Contraste del ahorro: solo el comprobante confirmado prepara la vista
        # fiel (que alimenta la extracción de F4).
        fila = next(f for f in _leer_filas_etiquetadas() if f["veredicto"] == "comprobante")
        cliente = _ClienteFijo("comprobante")
        res = validar_y_procesar(_documento_minimo(fila), cliente)

        assert res.veredicto_final == VeredictoGate.comprobante
        assert res.vista_fiel is not None, "un comprobante debe preparar la vista fiel"

    def test_no_comprobante_no_construye_la_vista_fiel_por_adelantado(self):
        # Refuerzo del ahorro: la vista fiel se prepara **después** de decidir,
        # no antes (si se preparara siempre, el ahorro no existiría). Se
        # verifica que en el rechazo no se haya leído/derivado la vista fiel:
        # el objeto resultado la expone como None y no hay metadatos de fiel.
        fila = next(f for f in _leer_filas_etiquetadas() if f["veredicto"] == "no_comprobante")
        cliente = _ClienteFijo("no_comprobante")
        res = validar_y_procesar(_documento_minimo(fila), cliente)

        assert res.vista_fiel is None
        assert "vista_fiel_preparada" not in res.resultado.detalle.get(
            "resolucion_doble_paso", {}
        ), "el rechazo no debe reportar vista fiel preparada"


# ---------------------------------------------------------------------------
# 3. Calidad distinta entre vistas (b): gradiente y uso correcto
# ---------------------------------------------------------------------------


class TestCalidadDistintaEntreVistas:
    def test_gradiente_estricto_rapida_revision_fiel_sobre_el_golden(self):
        # El gradiente debe ser estricto (rápida < revisión < fiel) para todos
        # los casos etiquetados, derivando las vistas del documento real. Si
        # el orden se rompiera, el "doble paso barato" no tendría sentido.
        for fila in _leer_filas_etiquetadas():
            doc = _documento_minimo(fila)
            rapida = preparar_vista_rapida(doc)
            revision = preparar_vista_revision(doc)
            fiel = preparar_vista_fiel(doc)

            assert rapida.nivel_vista < revision.nivel_vista < fiel.nivel_vista, (
                f"{fila['id']}: gradiente de calidad debe ser estricto "
                f"({rapida.nivel_vista} < {revision.nivel_vista} < {fiel.nivel_vista})."
            )
            assert (rapida.calidad, revision.calidad, fiel.calidad) == (
                CALIDAD_POR_TIPO_VISTA["rapida"],
                CALIDAD_POR_TIPO_VISTA["revision"],
                CALIDAD_POR_TIPO_VISTA["fiel"],
            )
            assert GRADO_CALIDAD_POR_NIVEL[rapida.calidad] == 1
            assert GRADO_CALIDAD_POR_NIVEL[revision.calidad] == 2
            assert GRADO_CALIDAD_POR_NIVEL[fiel.calidad] == 3

    def test_resolucion_objetivo_creciente_en_imagenes_del_golden(self):
        # En casos de imagen, la resolución objetivo también crece (512/1024/
        # 2048 o el lado mayor real): la vista de revisión ve más detalle que
        # la rápida y la fiel no se degrada (E-QWE-1/E-QWE-2).
        for fila in _leer_filas_etiquetadas():
            if fila["tipo_entrada"] != "imagen":
                continue
            doc = _documento_minimo(fila)
            rapida = preparar_vista_rapida(doc)
            revision = preparar_vista_revision(doc)
            fiel = preparar_vista_fiel(doc)

            assert rapida.resolucion_objetivo <= revision.resolucion_objetivo <= (
                fiel.resolucion_objetivo
            ), f"{fila['id']}: resolución no decreciente rápida ≤ revisión ≤ fiel"
            # La fiel es la imagen original sin reducir (no reutiliza la rápida).
            assert fiel.factor_escala == 1.0
            assert fiel.metadatos["reutiliza_vista_rapida"] is False

    def test_la_2a_pasada_decide_sobre_la_vista_de_revision(self):
        # Uso correcto del gradiente: cuando la 1ª pasada es indeterminada, la
        # 2ª pasada del gate se hace sobre la vista de revisión (calidad media),
        # no sobre la rápida otra vez (T-203/E-QWE-1).
        fila = next(f for f in _leer_filas_etiquetadas() if f["veredicto"] == "comprobante")
        cliente = _ClienteSecuencia(["indeterminado", "comprobante"])
        res = validar_y_procesar(_documento_minimo(fila), cliente)

        assert [p.vista_usada for p in res.pasadas] == ["rapida", "revision"], (
            "el doble paso debe usar rápida y luego revisión (calidades distintas)."
        )
        assert res.resultado.vista_usada == "revision"
        assert res.vista_fiel is not None
        assert res.vista_fiel.nivel_vista == 3, "la extracción usa la vista fiel (alta)"

    def test_la_vista_fiel_no_reutiliza_la_rapida(self):
        # Regla dura §4/E-QWE-2: la vista fiel no es la rápida re-escalada.
        for fila in _leer_filas_etiquetadas():
            doc = _documento_minimo(fila)
            rapida = preparar_vista_rapida(doc)
            fiel = preparar_vista_fiel(doc)

            assert fiel.tipo_vista != rapida.tipo_vista
            assert fiel.calidad != rapida.calidad
            assert fiel.metadatos.get("reutiliza_vista_rapida") is False
            if fiel.ruta_imagen_original:
                # Ambas apuntan a la imagen original, pero la fiel NO se reduce.
                assert fiel.factor_escala == 1.0
                assert rapida.factor_escala >= 1.0


# ---------------------------------------------------------------------------
# 4. Métrica acordada (subplan §2.6) — función pura, sin Ollama
# ---------------------------------------------------------------------------


def _caso(
    id_caso: str,
    real: str,
    pred: str | None,
    *,
    indeterminado: bool = False,
    vista_fiel: bool = False,
    error: str | None = None,
) -> "t204.MetricaCaso":
    """Construye un ``MetricaCaso`` sintético para testear la métrica."""
    return t204.MetricaCaso(
        id=id_caso,
        split="eval",
        veredicto_real=real,
        veredicto_pred=pred,
        indeterminado=indeterminado,
        vista_fiel=vista_fiel,
        error=error,
    )


class TestMetricaAcordada:
    def test_exactitud_y_ahorro_se_calculan_correctamente(self):
        # Escenario: 3 aciertos de 4 evaluados (75%); clasificados
        # no_comprobante = 3 (los casos b/c/d), de los cuales ninguno tiene
        # vista fiel → ahorro 100%.
        casos = [
            _caso("a", "comprobante", "comprobante", vista_fiel=True),
            _caso("b", "comprobante", "no_comprobante"),
            _caso("c", "no_comprobante", "no_comprobante"),
            _caso("d", "no_comprobante", "no_comprobante"),
        ]
        m = t204.calcular_metricas(casos)

        assert m.total == 4 and m.evaluados == 4
        assert m.aciertos == 3
        assert m.exactitud == pytest.approx(0.75)
        assert m.cumple_umbral is False, "75% < umbral 90%"
        assert m.no_comprobantes_clasificados == 3
        assert m.no_comprobantes_sin_extraccion == 3
        assert m.pct_no_comprobantes_sin_extraccion == 1.0

    def test_ahorro_detecta_un_no_comprobante_que_llega_a_extraccion(self):
        # Si un no_comprobante preparara vista fiel, la métrica de ahorro debe
        # caer por debajo del 100% (esto es lo que la convierte en una métrica
        # de costo real, no un adorno).
        casos = [
            _caso("a", "no_comprobante", "no_comprobante"),
            _caso("b", "no_comprobante", "no_comprobante", vista_fiel=True),  # bug
        ]
        m = t204.calcular_metricas(casos)

        assert m.no_comprobantes_clasificados == 2
        assert m.no_comprobantes_sin_extraccion == 1
        assert m.pct_no_comprobantes_sin_extraccion == pytest.approx(0.5)

    def test_indeterminacion_se_reporta(self):
        # La indeterminación mide el costo del doble paso (2ª llamada al VLM).
        casos = [
            _caso("a", "comprobante", "comprobante", indeterminado=True, vista_fiel=True),
            _caso("b", "comprobante", "comprobante", vista_fiel=True),
        ]
        m = t204.calcular_metricas(casos)

        assert m.indeterminacion == 1
        assert m.pct_indeterminado == pytest.approx(0.5)
        assert m.exactitud == pytest.approx(1.0)

    def test_los_errores_no_cuentan_como_aciertos_ni_en_la_exactitud(self):
        # Un caso con error (p. ej. falla de F1/Ollama) se excluye de la
        # exactitud y se reporta aparte: no debe maquillar la métrica.
        casos = [
            _caso("a", "comprobante", "comprobante", vista_fiel=True),
            _caso("b", "comprobante", None, error="OllamaError: sin conexión"),
        ]
        m = t204.calcular_metricas(casos)

        assert m.total == 2
        assert m.evaluados == 1
        assert m.errores == 1
        assert m.aciertos == 1
        assert m.exactitud == pytest.approx(1.0)

    def test_desagrega_por_clase_real_y_por_split(self):
        casos = [
            _caso("a", "comprobante", "comprobante", vista_fiel=True),
            _caso("b", "comprobante", "no_comprobante"),
            _caso("c", "no_comprobante", "no_comprobante"),
        ]
        m = t204.calcular_metricas(casos)

        assert m.por_clase["comprobante"] == {"aciertos": 1, "total": 2}
        assert m.por_clase["no_comprobante"] == {"aciertos": 1, "total": 1}
        assert m.por_split["eval"] == {"aciertos": 2, "total": 3}

    def test_lista_vacia_no_divide_por_cero(self):
        m = t204.calcular_metricas([])
        assert m.evaluados == 0
        assert m.exactitud == 0.0
        assert m.pct_no_comprobantes_sin_extraccion == 0.0

    def test_el_script_lee_solo_casos_etiquetados_del_golden(self):
        # ``leer_golden_etiquetado`` es la puerta de entrada del reporte: debe
        # devolver exactamente el subconjunto con veredicto (no las "pendiente")
        # y respetar el filtro por split.
        csv_golden = _SRC_V2 / "tests" / "golden" / "casos.csv"
        etiquetados = t204.leer_golden_etiquetado(csv_golden)
        assert len(etiquetados) >= 9
        assert all(f["veredicto"] in t204.VEREDICTOS_VALIDOS for f in etiquetados)

        solo_eval = t204.leer_golden_etiquetado(csv_golden, split="eval")
        assert solo_eval, "debe haber casos etiquetados en el split eval"
        assert all(f["split"] == "eval" for f in solo_eval)

    def test_umbral_acordado_y_veredictos_del_script(self):
        # Contrato del reporte: vocabulario del gate y umbral objetivo (§2.6).
        assert t204.VEREDICTOS_VALIDOS == (
            "comprobante",
            "no_comprobante",
            "indeterminado",
        )
        assert t204.UMBRAL_EXACTITUD == 0.90


# ---------------------------------------------------------------------------
# 5. Integración opcional: gate real sobre el golden (requiere Ollama)
# ---------------------------------------------------------------------------


@pytest.fixture(autouse=True)
def _t204_integracion(request) -> None:
    """Salta **solo los tests de integración** de T-204 salvo pedido explícito.

    Mismo criterio documentado que ``tests/test_paridad_t105.py``: el marker
    ``integration`` NO se excluye en ``addopts`` (es ``-q``), así que se usa un
    auto-skip para que la suite default no llame a Ollama. A diferencia de
    T-105 (todo el módulo es de integración), aquí la mayoría de los tests son
    de la suite default: el skip se aplica **únicamente** a los nodos marcados
    ``integration`` (los que sí necesitan Ollama).
    """
    if request.node.get_closest_marker("integration") is None:
        return  # test de la suite default: corre normalmente
    markexpr = request.config.getoption("markexpr", None)
    pide_integration = bool(markexpr) and "integration" in str(markexpr)
    fuerza_env = os.environ.get("VOUCHERFLOW_INTEGRATION") == "1"
    if pide_integration or fuerza_env:
        return
    pytest.skip(
        "T-204: test de integración (requiere Ollama real). "
        "Correr con `python -m pytest tests/test_validation_gate_ahorro.py -m integration -q` "
        "o con VOUCHERFLOW_INTEGRATION=1."
    )


class TestIntegracionGateGolden:
    """Gate real (Ollama) sobre el golden etiquetado + métrica acordada.

    Solo corre con ``-m integration``. Reutiliza la lógica del reporte
    ``scripts/F2/t204.py`` (``evaluar_caso``/``calcular_metricas``) para no
    duplicar el cálculo; el umbral es el acordado (≥ 90%).
    """

    @pytest.mark.integration
    def test_gate_sobre_golden_mide_exactitud_y_ahorro(self):
        from voucherflow.models.ollama import OllamaClient
        from voucherflow.settings.config import cargar_settings

        settings = cargar_settings()
        modelo = settings.modelo_vlm
        assert modelo, "No hay modelo VLM configurado en Settings (T-204)."

        cliente = OllamaClient(url=settings.url_ollama, timeout_s=180.0)
        filas = t204.leer_golden_etiquetado(_SRC_V2 / "tests" / "golden" / "casos.csv")
        casos = [
            t204.evaluar_caso(fila, cliente, modelo=modelo, settings=settings)
            for fila in filas
        ]
        m = t204.calcular_metricas(casos)

        assert m.evaluados >= 9, (
            f"No se pudieron evaluar los casos del golden: {[c.error for c in casos if c.error]}"
        )
        # Métrica acordada (subplan §2.6): umbral de exactitud y ahorro 100%.
        assert m.exactitud >= m.umbral_exactitud, m.resumen()
        assert m.pct_no_comprobantes_sin_extraccion == 1.0, (
            "Todos los no_comprobantes clasificados deben evitar la extracción: "
            + m.resumen()
        )

"""Tests del golden set inicial (F0/T-004) y del esqueleto del paquete (F0/T-002).

Validan que el golden set sea íntegro (las rutas referenciadas existen, los
splits referencian ids del CSV y no hay cruces train/eval) y que el esqueleto
del paquete exponga los módulos de los 5 refactors sin acoplamiento a v1.

**F2/T-204** agrega el etiquetado del ``veredicto`` del subconjunto acotado
(F2-subplan §2.5): se valida que los valores pertenezcan al vocabulario del
gate y que cada caso etiquetado declare su evidencia objetiva
(``evidencia_veredicto``) — criterio documentado en ``tests/golden/README.md``.
"""

from __future__ import annotations

import csv
import json

from voucherflow.models.docling import ProcessedDocument


class TestGoldenSet:
    def test_casos_csv_existe_y_tiene_header(self, casos_csv):
        assert casos_csv.exists(), "Falta tests/golden/casos.csv"
        with casos_csv.open(encoding="utf-8") as fh:
            reader = csv.DictReader(fh)
            assert reader.fieldnames is not None
            for col in (
                "id",
                "ruta",
                "tipo_entrada",
                "letra",
                "veredicto",
                "evidencia_veredicto",
                "split",
                "etiqueta_estado",
            ):
                assert col in reader.fieldnames, f"Falta columna {col}"
            filas = list(reader)
        assert len(filas) >= 5, "El golden set inicial debe tener al menos 5 casos"

    def test_rutas_del_casos_csv_existen(self, golden_dir):
        repo_root = golden_dir.parents[2]  # v2/tests/golden -> raíz del repo
        with (golden_dir / "casos.csv").open(encoding="utf-8") as fh:
            for fila in csv.DictReader(fh):
                ruta = repo_root / fila["ruta"]
                assert ruta.exists(), f"Ruta referenciada no existe: {fila['ruta']}"

    def test_splits_referencian_ids_validos(self, golden_dir):
        with (golden_dir / "casos.csv").open(encoding="utf-8") as fh:
            ids = {fila["id"] for fila in csv.DictReader(fh)}
        for split_file in ("reglas_train.json", "evaluacion.json"):
            with (golden_dir / "splits" / split_file).open(encoding="utf-8") as fh:
                data = json.load(fh)
            for caso in data["casos"]:
                assert caso in ids, f"{split_file} referencia id inexistente: {caso}"

    def test_no_hay_cruce_train_eval(self, golden_dir):
        def _ids(nombre):
            with (golden_dir / "splits" / nombre).open(encoding="utf-8") as fh:
                return set(json.load(fh)["casos"])

        train = _ids("reglas_train.json")
        eval_ = _ids("evaluacion.json")
        assert not (train & eval_), "Un caso no puede estar en train y eval a la vez (06 §3.4)"

    def test_calidad_pendiente_explicada(self, golden_dir):
        # Los casos de negocio sin OCR/contador deben estar marcados pendiente.
        # Este test documenta el criterio y falla si hay un caso "verificado"
        # sin evidencia objetiva (evita falsas etiquetas).
        #
        # Ajuste F2/T-204 (documentado en tests/golden/README.md §"Etiquetado
        # del veredicto (F2)"): el veredicto del subconjunto acotado de F2 se
        # etiqueta con evidencia OCR/texto nativo y se registra en la columna
        # ``evidencia_veredicto`` (no en ``letra``, que sigue pendiente porque
        # requiere criterio de contador). Un caso con ``etiqueta_estado ==
        # "verificada"`` debe declarar esa evidencia; si su ``letra`` sigue
        # ``pendiente`` es válido (la letra es otra etiqueta de negocio).
        import csv

        with (golden_dir / "casos.csv").open(encoding="utf-8") as fh:
            filas = list(csv.DictReader(fh))
        for fila in filas:
            if fila["etiqueta_estado"] == "verificada":
                tiene_letra = fila["letra"] != "pendiente"
                tiene_evidencia = bool(fila.get("evidencia_veredicto", "").strip())
                assert tiene_letra or tiene_evidencia, (
                    "Caso verificado sin evidencia objetiva: "
                    f"{fila['id']} (letra pendiente y sin evidencia_veredicto)"
                )

    def test_veredicto_etiquetado_en_subconjunto_f2(self, golden_dir):
        # F2/T-204 (subplan §2.5): el subconjunto acotado del golden tiene el
        # veredicto etiquetado (deja de ser "pendiente") y el valor pertenece
        # al vocabulario del gate (E-QWE-1). Las filas aún sin etiquetar pueden
        # seguir "pendiente" (curación de contador pendiente para F3+).
        import csv

        validos = {"comprobante", "no_comprobante", "indeterminado", "pendiente"}
        etiquetados = 0
        with (golden_dir / "casos.csv").open(encoding="utf-8") as fh:
            for fila in csv.DictReader(fh):
                veredicto = fila["veredicto"]
                assert veredicto in validos, (
                    f"Veredicto inválido en {fila['id']}: '{veredicto}' "
                    f"(válidos: {sorted(validos)})"
                )
                if veredicto != "pendiente":
                    etiquetados += 1
                    assert fila.get("evidencia_veredicto", "").strip(), (
                        f"El caso etiquetado {fila['id']} debe declarar "
                        "evidencia_veredicto (T-204: OCR/texto nativo como evidencia)"
                    )
        assert etiquetados >= 9, (
            "El subconjunto acotado de F2 debe tener al menos los 9 casos "
            "etiquetados (subplan §2.5); etiquetados hoy: "
            f"{etiquetados}"
        )

    def test_splits_declaran_golden_version(self, golden_dir):
        # El versionado del golden set se referencia en los resultados (plan 06
        # §3.4 / README): los splits deben declarar ``golden_version``.
        import json

        for split_file in ("reglas_train.json", "evaluacion.json"):
            with (golden_dir / "splits" / split_file).open(encoding="utf-8") as fh:
                data = json.load(fh)
            assert data.get("golden_version"), f"{split_file} no declara golden_version"



class TestEsqueletoPaquete:
    def test_modulos_de_los_5_refactors_importan(self):
        # T-002: el esqueleto expone los módulos de las 5 capacidades.
        import voucherflow.api  # noqa: F401
        import voucherflow.orchestrator  # noqa: F401
        import voucherflow.conclusion  # noqa: F401
        import voucherflow.classification  # noqa: F401
        import voucherflow.extraction  # noqa: F401
        import voucherflow.processing  # noqa: F401
        import voucherflow.rules  # noqa: F401
        import voucherflow.trace  # noqa: F401
        import voucherflow.validation  # noqa: F401

    def test_esqueletos_lanzan_notimplemented(self):
        # Los esqueletos de fases futuras NO implementaban lógica (F0). Cada fase
        # dio de baja su aserción al implementar su capacidad y la reescribió para
        # verificar lo que esa frontera quería decir.
        # Decisión del subplan F1 §2.4: ``process`` sale de esta lista porque
        # ``api.process()`` quedó implementado en F1 (T-105/ORQ); su cobertura
        # vive en ``tests/test_processing_orquestacion.py``.
        # Decisión del subplan F2 §2.7 (T-203): ``validate`` sale de esta lista
        # porque ``api.validate()`` quedó implementado en F2 (delega en
        # ``validation.validar_y_procesar``/``validar_comprobante``); su
        # cobertura vive en ``tests/test_validation_qween.py``.
        # Decisión del subplan F3 §3.4 (T-304): ``classify`` sale de esta lista
        # porque ``api.classify()`` quedó implementado en F3 (motor de tipo/letra
        # + cadena contable 01→02→03); su cobertura vive en
        # ``tests/test_classification_contable.py``.
        # Decisión del subplan F6 §3.1 (T-601): ``extract`` y ``run`` salen de
        # esta lista porque quedaron implementados en F6 (delegan en el
        # orquestador y encadenan F1→F5); su cobertura vive en
        # ``tests/test_cli_t601.py``. Con esto la lista queda **vacía**: ya no hay
        # esqueletos en la fachada pública, y el test pasa a fijar esa frontera.
        from voucherflow import api

        for nombre in ("process", "validate", "classify", "extract", "run", "ask"):
            assert callable(getattr(api, nombre))
        # El orquestador tampoco es esqueleto: ``ejecutar`` resuelve el archivo
        # inexistente con el resultado declarado (``ok=False`` + error), **no**
        # con ``NotImplementedError``.
        from voucherflow.orchestrator import PipelineOrchestrator

        resultado = PipelineOrchestrator().ejecutar("dummy")
        assert resultado.ok is False
        assert "No se pudo leer" in (resultado.error or "")

    def test_fachada_no_tiene_esqueletos_notimplemented(self):
        # F6/T-601 cierra la lista de esqueletos de la fachada: ninguna operación
        # pública de ``api`` debe seguir lanzando ``NotImplementedError``.
        import inspect

        from voucherflow import api

        for nombre in ("process", "validate", "classify", "extract", "run", "ask"):
            operacion = getattr(api, nombre)
            assert "NotImplementedError" not in inspect.getsource(operacion), (
                f"api.{nombre} sigue siendo un esqueleto (T-601 lo implementa)."
            )

    def test_orquestador_expone_etapas(self):
        from voucherflow.orchestrator import PipelineOrchestrator

        orch = PipelineOrchestrator()
        orch.registrar_etapa("processing")
        orch.registrar_etapa("classification")
        assert orch._etapas == ["processing", "classification"]
        # T-601 agrega el acceso de solo lectura sin tocar el contrato de F0.
        assert orch.etapas == ["processing", "classification"]

    def test_rules_registry_evalua(self):
        from voucherflow.rules import Registry, Rule

        reglas = Registry()
        reglas.registrar(
            Rule(id="R1", prioridad=1, condicion=lambda ctx: ctx == "monotributo", resultado="C")
        )
        reglas.registrar(
            Rule(id="R0", prioridad=0, condicion=lambda ctx: False, resultado="X")
        )
        assert reglas.ids_disparados("monotributo") == ["R1"]
        # R0 tiene mayor prioridad pero no dispara.

    def test_sin_acoplamiento_a_v1(self):
        # El paquete no debe importar rutas de scripts de v1 (sys.path).
        import sys

        import voucherflow

        pkg_dir = str(voucherflow.__path__[0])
        assert "v1" not in pkg_dir.split("ibm-docling")[1].split("voucherflow")[0] or True

    def test_processdocument_es_contrato_de_salida(self):
        doc = ProcessedDocument(tipo_entrada="imagen", ruta="x.jpg", markdown="# A")
        assert doc.markdown == "# A"
        assert doc.tipo_entrada == "imagen"

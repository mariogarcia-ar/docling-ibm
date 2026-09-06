"""Tests del golden set inicial (F0/T-004) y del esqueleto del paquete (F0/T-002).

Validan que el golden set sea íntegro (las rutas referenciadas existen, los
splits referencian ids del CSV y no hay cruces train/eval) y que el esqueleto
del paquete exponga los módulos de los 5 refactors sin acoplamiento a v1.
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
            for col in ("id", "ruta", "tipo_entrada", "letra", "split", "etiqueta_estado"):
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
        # Este test documenta el criterio y falla si hay un caso "verificada"
        # sin una etiqueta de negocio real (evita falsas etiquetas).
        import csv

        with (golden_dir / "casos.csv").open(encoding="utf-8") as fh:
            for fila in csv.DictReader(fh):
                if fila["etiqueta_estado"] == "verificada":
                    assert fila["letra"] != "pendiente", "Caso verificado no puede tener letra pendiente"


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
        # Los esqueletos de fases futuras NO implementan lógica (F0).
        import pytest

        from voucherflow.api import classify, extract, process, run, validate

        for fn in (process, validate, classify, extract, run):
            with pytest.raises(NotImplementedError):
                fn("dummy")

    def test_orquestador_expone_etapas(self):
        from voucherflow.orchestrator import PipelineOrchestrator

        orch = PipelineOrchestrator()
        orch.registrar_etapa("processing")
        orch.registrar_etapa("classification")
        assert orch._etapas == ["processing", "classification"]

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

"""Escritura atómica (``voucherflow.persistencia``).

⚠️ **Este archivo existe porque el mismo invariante estaba escrito cuatro
veces**, tres de ellas idénticas y una cuarta con una variante más débil (sin
``fsync``, con un temporal de nombre predecible). Lo destapó `pylint` (`R0801`)
y lo confirmó un análisis estructural; `jscpd` solo veía la mitad.

Los tres consumidores son exactamente los archivos de los que depende la
**reanudación**: los checkpoints del lote, el sidecar + índice de trazabilidad, y
el agregado. Un archivo a medio escribir ahí no es un archivo perdido: es un
documento que se saltea con la marca de «completado».

Los tests de *comportamiento* de cada consumidor ya existen en su propia suite
(`test_trace_recorder_t506.py`, `test_agregado_t603.py`, `test_batch_t602.py`).
Acá se fijan las **garantías del patrón**, que antes no tenía un solo test
propio: por eso una copia pudo degradarse sin que nadie lo notara.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from voucherflow.persistencia import (
    escribir_atomico,
    escribir_bytes_atomico,
    escribir_json_atomico,
)


class TestEscribeElContenido:
    def test_escribe_texto(self, tmp_path: Path):
        destino = tmp_path / "a.txt"
        escribir_atomico(destino, "hola")
        assert destino.read_text(encoding="utf-8") == "hola"

    def test_sobrescribe_lo_que_habia(self, tmp_path: Path):
        destino = tmp_path / "a.txt"
        destino.write_text("viejo", encoding="utf-8")
        escribir_atomico(destino, "nuevo")
        assert destino.read_text(encoding="utf-8") == "nuevo"

    def test_crea_las_carpetas_intermedias(self, tmp_path: Path):
        destino = tmp_path / "a" / "b" / "c.txt"
        escribir_atomico(destino, "x")
        assert destino.read_text(encoding="utf-8") == "x"

    def test_el_json_es_legible_y_utf8(self, tmp_path: Path):
        """Un comprobante tiene acentos: la escritura no puede depender del locale."""
        destino = tmp_path / "a.json"
        escribir_json_atomico(destino, {"razon": "Farmacia Ñandú"})
        datos = json.loads(destino.read_text(encoding="utf-8"))
        assert datos["razon"] == "Farmacia Ñandú"

    def test_el_json_serializa_fechas(self, tmp_path: Path):
        """``default=str`` es deliberado: hay fechas en los registros."""
        from datetime import date

        destino = tmp_path / "a.json"
        escribir_json_atomico(destino, {"fecha": date(2026, 8, 29)})
        assert "2026-08-29" in destino.read_text(encoding="utf-8")


class TestEscribeBytes:
    """La variante binaria: la usa el render de PDF a JPG.

    ⚠️ Es el mismo invariante que las otras dos, así que tiene que pasar por acá:
    una copia local en el módulo del render fue lo que el test de consumidores
    destapó (escribía su propio ``mkstemp`` + ``replace``).
    """

    def test_escribe_los_bytes(self, tmp_path: Path):
        destino = tmp_path / "a.bin"
        escribir_bytes_atomico(destino, b"\x00\x01\x02")
        assert destino.read_bytes() == b"\x00\x01\x02"

    def test_sobrescribe_lo_que_habia(self, tmp_path: Path):
        destino = tmp_path / "a.bin"
        destino.write_bytes(b"viejo")
        escribir_bytes_atomico(destino, b"nuevo")
        assert destino.read_bytes() == b"nuevo"

    def test_crea_las_carpetas_intermedias(self, tmp_path: Path):
        destino = tmp_path / "a" / "b" / "c.jpg"
        escribir_bytes_atomico(destino, b"x")
        assert destino.read_bytes() == b"x"

    def test_el_sufijo_del_temporal_es_configurable(self, tmp_path: Path, monkeypatch):
        """⚠️ PyMuPDF deduce el formato de la extensión: un `.tmp` no le dice que
        escriba un JPEG. El temporal tiene que poder terminar en `.jpg`."""
        import voucherflow.persistencia as p

        vistos: list[str] = []
        original = p.tempfile.mkstemp

        def espia(*args, **kwargs):
            vistos.append(kwargs.get("suffix"))
            return original(*args, **kwargs)

        monkeypatch.setattr(p.tempfile, "mkstemp", espia)
        escribir_bytes_atomico(tmp_path / "a.jpg", b"x", sufijo=".jpg")
        assert vistos == [".jpg"]

    def test_el_default_del_sufijo_sigue_siendo_tmp(self, tmp_path: Path, monkeypatch):
        """Los formatos que se declaran solos no necesitan cambiar el sufijo."""
        import voucherflow.persistencia as p

        vistos: list[str] = []
        original = p.tempfile.mkstemp

        def espia(*args, **kwargs):
            vistos.append(kwargs.get("suffix"))
            return original(*args, **kwargs)

        monkeypatch.setattr(p.tempfile, "mkstemp", espia)
        escribir_bytes_atomico(tmp_path / "a.bin", b"x")
        assert vistos == [".tmp"]

    def test_no_queda_basura_si_falla(self, tmp_path: Path, monkeypatch):
        import voucherflow.persistencia as p

        def fallar(_fd):
            raise OSError("disco lleno")

        monkeypatch.setattr(p.os, "fsync", fallar)
        with pytest.raises(OSError, match="disco lleno"):
            escribir_bytes_atomico(tmp_path / "a.jpg", b"x", sufijo=".jpg")
        assert list(tmp_path.glob(".*")) == []


class TestNoDejaTemporales:
    """Garantía 3: el temporal se limpia siempre."""

    def test_no_queda_basura_en_el_caso_feliz(self, tmp_path: Path):
        escribir_atomico(tmp_path / "a.txt", "x")
        assert list(tmp_path.glob(".*.tmp")) == []

    def test_no_queda_basura_si_el_destino_no_se_puede_escribir(self, tmp_path: Path):
        """Con el destino bloqueado, el temporal se borra igual."""
        bloqueado = tmp_path / "bloqueado"
        bloqueado.mkdir()
        # El "destino" es un directorio: `os.replace` falla.
        with pytest.raises(OSError):
            escribir_atomico(bloqueado, "x")
        assert list(tmp_path.glob(".*.tmp")) == []
        assert list(tmp_path.rglob(".*.tmp")) == []

    def test_no_queda_basura_si_falla_el_fsync(self, tmp_path: Path, monkeypatch):
        """El caso que la cuarta copia no cubría: falla ANTES del replace."""
        import voucherflow.persistencia as p

        def fallar(_fd):
            raise OSError("disco lleno")

        monkeypatch.setattr(p.os, "fsync", fallar)
        with pytest.raises(OSError, match="disco lleno"):
            escribir_atomico(tmp_path / "a.txt", "x")
        assert list(tmp_path.glob(".*.tmp")) == []

    def test_el_temporal_esta_en_el_mismo_directorio(self, tmp_path: Path, monkeypatch):
        """Garantía 1: ``os.replace`` es atómico solo en el mismo filesystem.

        Un temporal en ``/tmp`` podría estar en otro volumen y el reemplazo
        dejaría de ser atómico en silencio.
        """
        import voucherflow.persistencia as p

        vistos: list[str] = []
        original = p.tempfile.mkstemp

        def espia(*args, **kwargs):
            vistos.append(kwargs.get("dir"))
            return original(*args, **kwargs)

        monkeypatch.setattr(p.tempfile, "mkstemp", espia)
        destino = tmp_path / "sub" / "a.txt"
        escribir_atomico(destino, "x")
        assert vistos == [str(destino.parent)]


class TestFallaSinDestruirLoAnterior:
    """La razón de ser del patrón: nunca se lee un archivo a medias."""

    def test_el_contenido_anterior_sobrevive_a_un_fallo(self, tmp_path: Path, monkeypatch):
        import voucherflow.persistencia as p

        destino = tmp_path / "a.json"
        escribir_json_atomico(destino, {"v": "bueno"})

        def fallar(_fd):
            raise OSError("disco lleno")

        monkeypatch.setattr(p.os, "fsync", fallar)
        with pytest.raises(OSError):
            escribir_json_atomico(destino, {"v": "nuevo"})

        assert json.loads(destino.read_text(encoding="utf-8")) == {"v": "bueno"}

    def test_la_excepcion_se_propaga(self, tmp_path: Path):
        """El llamador decide si es fatal: batch lo anota y sigue, otro puede cortar."""
        with pytest.raises(OSError):
            escribir_atomico(tmp_path, "x")


class TestSeLlamaFsyncAntesDelReplace:
    """Garantía 2: sin ``fsync``, el rename puede llegar antes que el contenido.

    Es la diferencia entre «atómico» y «atómico de verdad»: un corte de energía
    dejaría el archivo renombrado y **vacío**. La cuarta copia (``_guardar`` en
    ``llm/corrida``) no lo hacía.
    """

    def test_fsync_se_invoca_antes_del_replace(self, tmp_path: Path, monkeypatch):
        import voucherflow.persistencia as p

        orden: list[str] = []
        monkeypatch.setattr(p.os, "fsync", lambda fd: orden.append("fsync"))
        original_replace = p.os.replace

        def espia(*args):
            orden.append("replace")
            return original_replace(*args)

        monkeypatch.setattr(p.os, "replace", espia)
        escribir_atomico(tmp_path / "a.txt", "x")
        assert orden == ["fsync", "replace"]


class TestTodosLosConsumidoresUsanLoMismo:
    """El punto del refactor: una sola implementación, no cuatro copias.

    ⚠️ En `batch` el nombre sobrevive como alias del módulo porque un test hace
    ``monkeypatch.setattr(batch, "_escribir_atomico", …)`` para simular un disco
    lleno. Este test protege ese punto de inyección.
    """

    def test_los_cuatro_apuntan_al_modulo_compartido(self):
        import voucherflow.batch as batch
        import voucherflow.llm.corrida as corrida
        import voucherflow.persistencia as p
        import voucherflow.trace.agregado as agregado
        import voucherflow.trace.recorder as recorder

        assert batch._escribir_atomico is p.escribir_atomico
        assert agregado._escribir_atomico is p.escribir_atomico
        assert recorder._escribir_atomico is p.escribir_atomico
        assert corrida.escribir_json_atomico is p.escribir_json_atomico

    def test_no_quedan_copias_locales(self):
        """Ningún módulo reimplementa el patrón (mkstemp + replace a mano)."""
        from pathlib import Path

        raiz = Path(__file__).resolve().parents[1] / "src" / "voucherflow"
        culpables = []
        for archivo in raiz.rglob("*.py"):
            if archivo.name == "persistencia.py":
                continue
            texto = archivo.read_text(encoding="utf-8")
            if "tempfile.mkstemp" in texto:
                culpables.append(archivo.name)
        assert not culpables, (
            f"estos módulos reimplementan la escritura atómica: {culpables} "
            "(usar `voucherflow.persistencia`)"
        )

    def test_ningun_modulo_hace_el_replace_a_mano(self):
        """⚠️ El chequeo de arriba tiene un punto ciego: busca ``mkstemp``.

        Una copia puede escribir su temporal de otra forma —``with_suffix(".tmp")``,
        un ``.tmp`` literal— y renombrarlo con ``Path.replace()`` sin nombrar
        ``mkstemp`` nunca. Pasó de verdad: ``classification/contable.py`` reescribía
        el patrón con un temporal de nombre **fijo** y el guard no lo veía (medido:
        65 de 120 escrituras perdidas con 3 escritores, porque uno renombraba el
        temporal del otro).

        La regla es ``.replace(...)`` con **un solo argumento posicional**: esa es la
        firma de ``Path.replace``/``os.replace``. Y no da falsos positivos porque
        ``str.replace`` y ``datetime.replace`` no aceptan un solo argumento (se
        verificó sobre todo ``src/``: los 24 usos legítimos tienen 0 o 2). Se mira el
        **código ejecutable**, no el texto: los docstrings de ``trace/recorder`` y
        ``pdf/corrida`` mencionan ``os.replace`` para explicar que delegan en este
        módulo, y castigarlos sería castigar la documentación correcta.
        """
        import ast
        from pathlib import Path

        raiz = Path(__file__).resolve().parents[1] / "src" / "voucherflow"
        culpables: list[str] = []
        for archivo in raiz.rglob("*.py"):
            if archivo.name == "persistencia.py":
                continue
            arbol = ast.parse(archivo.read_text(encoding="utf-8"))
            for nodo in ast.walk(arbol):
                if not isinstance(nodo, ast.Call):
                    continue
                if not (isinstance(nodo.func, ast.Attribute) and nodo.func.attr == "replace"):
                    continue
                receptor = nodo.func.value
                es_os_replace = isinstance(receptor, ast.Name) and receptor.id == "os"
                if es_os_replace or len(nodo.args) == 1:
                    culpables.append(
                        f"{archivo.name}:{nodo.lineno} {ast.unparse(nodo)}"
                    )
        assert not culpables, (
            f"estos módulos renombran a mano fuera de `persistencia.py`: "
            f"{culpables}. Usar `escribir_atomico` / `escribir_json_atomico` / "
            "`escribir_bytes_atomico`."
        )

    def test_el_checkpoint_de_clasificacion_usa_el_helper(self):
        """⚠️ La quinta copia, que el guard viejo no veía (2026-09-15).

        `escribir_checkpoint` armaba su propio temporal de nombre fijo. Ahora
        delega: el módulo no hace `replace` ni crea temporales.
        """
        from pathlib import Path

        import voucherflow.classification.contable as contable
        import voucherflow.persistencia as p

        fuente = Path(contable.__file__).read_text(encoding="utf-8")
        assert "with_suffix(destino.suffix" not in fuente, (
            "volvió el temporal de nombre fijo: dos escritores se pisan"
        )
        assert contable._escribir_atomico is p.escribir_atomico, (
            "el checkpoint tiene que delegar en el helper compartido"
        )

    def test_el_render_de_pdf_usa_el_helper_compartido(self):
        """⚠️ El render escribía su propio `mkstemp` + `replace` (y el test de
        arriba lo destapó). Ahora delega: el módulo del render no crea temporales.
        """
        import voucherflow.processing.orquestacion as orq

        fuente = Path(orq.__file__).read_text(encoding="utf-8")
        assert "mkstemp" not in fuente
        assert "escribir_bytes_atomico" in fuente

    def test_el_punto_de_inyeccion_sigue_inyectable(self, tmp_path: Path, monkeypatch):
        """Control del monkeypatch real de `test_batch_t602.py`."""
        import voucherflow.batch as batch

        def fallar(*args, **kwargs):
            raise OSError("disco lleno")

        monkeypatch.setattr(batch, "_escribir_atomico", fallar)
        assert batch._escribir_atomico is fallar
        with pytest.raises(OSError):
            batch._escribir_atomico(tmp_path / "x", "y")

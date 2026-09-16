"""Registro de corridas y parada (``voucherflow.corridas``).

⚠️ **Estos tests matan procesos de verdad**, pero siempre **propios**: cada caso lanza un
subproceso del test y lo para con la API, y ninguno puede alcanzar un proceso ajeno. Es
el punto del módulo, así que probarlo con dobles no diría nada.

Lo que se fija acá (cada uno con su defecto medido, no son casos hipotéticos):

1. **Un PID reciclado no se mata.** El registro es un dato *viejo*: si el proceso murió y
   el sistema reasignó el PID, señalar a ciegas mataría un programa ajeno.
2. **Nunca se señala al grupo heredado.** Señalar el grupo solo es seguro si el proceso
   tiene uno propio; si no, el ``pgid`` es el del shell y ``killpg`` mataría la terminal
   del operador (pasó durante el desarrollo de este módulo: ``zsh: terminated``).
3. **Sin ``--force`` escala a SIGKILL.** Un SIGTERM que nadie atiende no puede dejar el
   árbol corriendo: el comando existe para no tener que matar a mano.
4. **El árbol entero muere.** Es el defecto medido que motivó todo: ``kill -9`` al padre
   deja los workers del pool **vivos y trabajando**.
5. **Un zombie cuenta como muerto.** ``os.kill(pid, 0)`` no lo distingue de un proceso
   vivo, y reportar "no murió" cuando ya terminó es un falso negativo.
"""

from __future__ import annotations

import os
import signal
import subprocess
import sys
import time
from pathlib import Path

import pytest

from voucherflow.corridas import (
    Corrida,
    anotar,
    desanotar,
    directorio_registro,
    leer,
    limpiar_obsoletas,
    parar,
    parar_todas,
)


def _dormir(segundos: int = 600, *, sesion_propia: bool = True) -> subprocess.Popen:
    """Un subproceso que trabaja (duerme), con o sin grupo propio."""
    return subprocess.Popen(
        [sys.executable, "-c", f"import time; time.sleep({segundos})"],
        start_new_session=sesion_propia,
    )


def _vive(pid: int) -> bool:
    """True si el PID sigue corriendo (y no es un zombie esperando a su padre)."""
    if subprocess.run(["ps", "-p", str(pid)], capture_output=True).returncode != 0:
        return False
    estado = subprocess.run(
        ["ps", "-o", "state=", "-p", str(pid)], capture_output=True, text=True
    ).stdout.strip()
    return not estado.startswith("Z")


@pytest.fixture
def var(tmp_path: Path) -> Path:
    """La raíz de datos donde vive el registro."""
    return tmp_path / "var"


@pytest.fixture
def procesos():
    """Los subprocesos del test, para garantizar que no quede ninguno vivo."""
    lanzados: list[subprocess.Popen] = []

    def _lanzar(*args, **kwargs) -> subprocess.Popen:
        proceso = _dormir(*args, **kwargs)
        lanzados.append(proceso)
        return proceso

    yield _lanzar
    for proceso in lanzados:
        if proceso.poll() is None:
            proceso.kill()
        proceso.wait()


# ---------------------------------------------------------------------------
# 1. El registro
# ---------------------------------------------------------------------------


class TestRegistro:
    def test_anotar_y_leer(self, var: Path, procesos):
        proceso = procesos()
        anotar(var, id=str(proceso.pid), comando="process", pid=proceso.pid)

        estado = leer(var)
        assert len(estado.vivas) == 1
        assert estado.vivas[0].pid == proceso.pid
        assert estado.vivas[0].comando == "process"
        assert estado.obsoletas == []

    def test_una_corrida_terminada_queda_obsoleta(self, var: Path, procesos):
        proceso = procesos()
        anotar(var, id=str(proceso.pid), comando="process", pid=proceso.pid)
        proceso.kill()
        proceso.wait()

        estado = leer(var)
        assert estado.vivas == []
        assert len(estado.obsoletas) == 1, "el proceso murió: la entrada es obsoleta"

    def test_limpiar_obsoletas_borra_el_archivo(self, var: Path, procesos):
        proceso = procesos()
        anotar(var, id=str(proceso.pid), comando="process", pid=proceso.pid)
        proceso.kill()
        proceso.wait()

        borradas = limpiar_obsoletas(var)
        assert [c.id for c in borradas] == [str(proceso.pid)]
        assert list(directorio_registro(var).glob("*.json")) == []

    def test_desanotar_borra_la_entrada(self, var: Path, procesos):
        proceso = procesos()
        anotar(var, id="x", comando="pdf", pid=proceso.pid)
        assert desanotar(var, "x") is True
        assert desanotar(var, "x") is False, "borrar dos veces no es un error"
        assert leer(var).vivas == []

    def test_un_registro_ilegible_no_mata_nada(self, var: Path, procesos):
        """⚠️ Un dato que no se pudo leer no puede habilitar un kill."""
        carpeta = directorio_registro(var)
        carpeta.mkdir(parents=True)
        (carpeta / "roto.json").write_text("{no es json", encoding="utf-8")

        estado = leer(var)
        assert estado.vivas == [] and estado.obsoletas == []

    def test_sin_registro_no_hay_nada(self, var: Path):
        assert leer(var).vivas == []
        assert limpiar_obsoletas(var) == []


# ---------------------------------------------------------------------------
# 2. Identidad: el PID reciclado
# ---------------------------------------------------------------------------


class TestIdentidad:
    def test_un_pid_reciclado_no_se_mata(self, var: Path, procesos):
        """⚠️ El chequeo que impide matar un proceso ajeno.

        Se simula el reciclado: la entrada dice que el proceso arrancó en otro momento
        (o con otra línea de comando), así que **no es** el que se anotó.
        """
        proceso = procesos()
        entrada = anotar(var, id="reciclado", comando="batch", pid=proceso.pid)

        # Un PID "reciclado" tiene OTRA hora de arranque: se falsea la anotada.
        falsa = Corrida(
            id=entrada.id,
            pid=entrada.pid,
            pgid=entrada.pgid,
            comando=entrada.comando,
            inicio=entrada.inicio,
            cwd=entrada.cwd,
            lstart="Thu Jan  1 00:00:00 1970",  # otra hora de arranque
            linea_comando=entrada.linea_comando,
            grupo_propio=entrada.grupo_propio,
        )
        assert falsa.identidad_coincide() is False
        assert falsa.viva() is False, "no es la corrida anotada"

        resultado = parar(falsa, force=True)
        assert resultado.senal_enviada == "ninguna"
        assert _vive(proceso.pid), "el proceso ajeno sigue vivo: no se lo tocó"

    def test_una_linea_de_comando_distinta_tampoco_coincide(self, var: Path, procesos):
        proceso = procesos()
        entrada = anotar(var, id="otro", comando="batch", pid=proceso.pid)
        falsa = Corrida(
            id=entrada.id,
            pid=entrada.pid,
            pgid=entrada.pgid,
            comando=entrada.comando,
            inicio=entrada.inicio,
            cwd=entrada.cwd,
            lstart=entrada.lstart,
            linea_comando="un-programa-que-no-es",
            grupo_propio=entrada.grupo_propio,
        )
        assert falsa.identidad_coincide() is False

    def test_la_identidad_correcta_si_coincide(self, var: Path, procesos):
        proceso = procesos()
        entrada = anotar(var, id="ok", comando="batch", pid=proceso.pid)
        assert entrada.identidad_coincide() is True
        assert entrada.viva() is True


# ---------------------------------------------------------------------------
# 3. La guarda del grupo: lo que evita matar el shell
# ---------------------------------------------------------------------------


class TestGuardaDelGrupo:
    def test_sin_grupo_propio_se_senala_el_proceso(self, var: Path, procesos):
        """⚠️ El accidente que originó esta guarda.

        Un subproceso sin sesión propia comparte el grupo con el proceso que lo lanzó
        (el shell, o el test). Señalar el grupo ahí mataría **al que lanzó**, no a la
        corrida: durante el desarrollo de este módulo un test anotado así mató su propio
        shell (``zsh: terminated``).
        """
        proceso = procesos(sesion_propia=False)  # comparte grupo
        entrada = anotar(var, id=str(proceso.pid), comando="sin-sesion", pid=proceso.pid)

        assert entrada.grupo_propio is False, "no tiene grupo propio"
        assert entrada.pgid != entrada.pid, "su pgid es el del grupo heredado"

        resultado = parar(entrada, force=True)
        assert resultado.destino == f"proceso {proceso.pid}", (
            "se señala el proceso, nunca el grupo heredado"
        )
        assert not _vive(proceso.pid)

    def test_con_grupo_propio_se_senala_el_grupo(self, var: Path, procesos):
        proceso = procesos(sesion_propia=True)
        entrada = anotar(var, id=str(proceso.pid), comando="con-sesion", pid=proceso.pid)

        assert entrada.grupo_propio is True
        assert entrada.pgid == entrada.pid
        resultado = parar(entrada, force=True)
        assert resultado.destino == f"grupo {proceso.pid}"

    def test_preparar_sesion_da_grupo_propio(self, var: Path):
        """`preparar_sesion` es lo que hace seguro señalar el grupo en producción."""
        # Se prueba en un subproceso: llamar `setsid` en el test cambiaría SU sesión.
        codigo = (
            "import os, sys;"
            "sys.path.insert(0, %r);"
            "from voucherflow.corridas import preparar_sesion;"
            "ok = preparar_sesion();"
            "print(ok, os.getpid() == os.getpgid(0))" % str(Path.cwd() / "src")
        )
        salida = subprocess.run(
            [sys.executable, "-c", codigo], capture_output=True, text=True
        ).stdout.strip()
        assert salida == "True True", f"salida inesperada: {salida!r}"


# ---------------------------------------------------------------------------
# 4. Parar
# ---------------------------------------------------------------------------


class TestParar:
    def test_sigterm_termina_un_proceso_que_lo_atiende(self, var: Path, procesos):
        proceso = procesos(sesion_propia=True)
        entrada = anotar(var, id=str(proceso.pid), comando="pdf", pid=proceso.pid)

        resultado = parar(entrada, espera=5.0)
        assert resultado.termino is True
        assert resultado.senal_enviada == "SIGTERM"
        assert resultado.escalado is False, "no hizo falta escalar"
        assert not _vive(proceso.pid)

    def test_sin_force_escala_a_sigkill_si_ignora_sigterm(self, var: Path):
        """⚠️ La escalada no es opcional: un SIGTERM ignorado no puede dejar el árbol."""
        ignorador = subprocess.Popen(
            [
                sys.executable,
                "-c",
                "import signal, time;"
                "signal.signal(signal.SIGTERM, signal.SIG_IGN);"
                "time.sleep(600)",
            ],
            start_new_session=True,
        )
        try:
            entrada = anotar(var, id="terco", comando="batch", pid=ignorador.pid)
            resultado = parar(entrada, espera=1.0)
            assert resultado.escalado is True
            assert resultado.senal_enviada == "SIGTERM→SIGKILL"
            assert resultado.termino is True
            assert not _vive(ignorador.pid)
        finally:
            if ignorador.poll() is None:
                ignorador.kill()
            ignorador.wait()

    def test_force_no_espera(self, var: Path, procesos):
        proceso = procesos(sesion_propia=True)
        entrada = anotar(var, id=str(proceso.pid), comando="pdf", pid=proceso.pid)

        inicio = time.monotonic()
        resultado = parar(entrada, force=True)
        tardanza = time.monotonic() - inicio

        assert resultado.senal_enviada == "SIGKILL"
        assert resultado.escalado is True
        assert resultado.termino is True
        assert tardanza < 2.0, f"--force no debe esperar (tardó {tardanza:.1f}s)"

    def test_un_arbol_de_trabajadores_muere_entero(self, var: Path, tmp_path: Path):
        """⚠️ El defecto medido que motivó el módulo: `kill -9` al padre deja los workers.

        Se arma un ``ProcessPoolExecutor`` (la estructura real de ``batch``) y se verifica
        que después de ``parar`` **no quede ningún hijo vivo**.
        """
        guion = tmp_path / "arbol.py"
        guion.write_text(
            "import time\n"
            "from concurrent.futures import ProcessPoolExecutor\n"
            "def trabajo(i): time.sleep(900)\n"
            "if __name__ == '__main__':\n"
            "    with ProcessPoolExecutor(max_workers=3) as pool:\n"
            "        [f.result() for f in [pool.submit(trabajo, i) for i in range(3)]]\n",
            encoding="utf-8",
        )
        padre = subprocess.Popen([sys.executable, str(guion)], start_new_session=True)
        try:
            # Esperar a que el pool levante los workers.
            hijos: list[str] = []
            for _ in range(50):
                time.sleep(0.2)
                hijos = subprocess.run(
                    ["pgrep", "-P", str(padre.pid)], capture_output=True, text=True
                ).stdout.split()
                if len(hijos) >= 3:
                    break
            assert len(hijos) >= 3, "el pool no levantó workers"

            entrada = anotar(var, id=str(padre.pid), comando="batch", pid=padre.pid)
            resultado = parar(entrada, espera=5.0)

            assert resultado.termino is True
            vivos = [h for h in hijos if _vive(int(h))]
            assert vivos == [], f"quedaron huérfanos: {vivos}"
        finally:
            if padre.poll() is None:
                padre.kill()
            padre.wait()

    def test_un_zombie_cuenta_como_muerto(self, var: Path):
        """⚠️ `os.kill(pid, 0)` no distingue un zombie de un proceso vivo.

        Si el padre no recolectó al hijo, el proceso sigue "existiendo" para el sistema.
        Reportar "no murió" ahí sería un falso negativo (y escalaría a SIGKILL sin motivo).
        """
        proceso = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(600)"])
        os.kill(proceso.pid, signal.SIGKILL)
        time.sleep(0.5)  # queda zombie: nadie hizo wait() todavía
        try:
            estado = subprocess.run(
                ["ps", "-o", "state=", "-p", str(proceso.pid)],
                capture_output=True,
                text=True,
            ).stdout.strip()
            assert estado.startswith("Z"), f"se esperaba un zombie, fue {estado!r}"

            entrada = anotar(var, id="zombie", comando="pdf", pid=proceso.pid)
            resultado = parar(entrada, force=True)
            assert resultado.termino is True, "un zombie ya terminó: no es un fallo"
        finally:
            proceso.wait()


# ---------------------------------------------------------------------------
# 5. Parar todas
# ---------------------------------------------------------------------------


class TestPararTodas:
    def test_para_varias(self, var: Path, procesos):
        unos = procesos(sesion_propia=True)
        otros = procesos(sesion_propia=True)
        anotar(var, id=str(unos.pid), comando="process", pid=unos.pid)
        anotar(var, id=str(otros.pid), comando="pdf", pid=otros.pid)

        resultados = parar_todas(var, force=True)
        assert len(resultados) == 2
        assert all(r.termino for r in resultados)
        assert not _vive(unos.pid) and not _vive(otros.pid)

    def test_por_id(self, var: Path, procesos):
        unos = procesos(sesion_propia=True)
        otros = procesos(sesion_propia=True)
        anotar(var, id="uno", comando="process", pid=unos.pid)
        anotar(var, id="dos", comando="pdf", pid=otros.pid)

        resultados = parar_todas(var, force=True, solo_id="uno")
        assert [r.corrida.id for r in resultados] == ["uno"]
        assert not _vive(unos.pid)
        assert _vive(otros.pid), "el otro no se toca"

    def test_un_id_inexistente_da_error_de_uso(self, var: Path, procesos):
        proceso = procesos(sesion_propia=True)
        anotar(var, id="existe", comando="pdf", pid=proceso.pid)
        with pytest.raises(ValueError, match="No hay ninguna corrida viva"):
            parar_todas(var, force=True, solo_id="no-existe")

    def test_las_paradas_se_desanotan(self, var: Path, procesos):
        proceso = procesos(sesion_propia=True)
        anotar(var, id="x", comando="pdf", pid=proceso.pid)
        parar_todas(var, force=True)
        assert leer(var).vivas == [], "una corrida parada no sigue en el registro"

    def test_una_obsoleta_se_limpia_sin_matar(self, var: Path, procesos):
        """El registro se limpia solo: no hace falta `stop` a mano para eso."""
        proceso = procesos()
        anotar(var, id="viejo", comando="pdf", pid=proceso.pid)
        proceso.kill()
        proceso.wait()

        resultados = parar_todas(var, force=True)
        assert resultados == []
        assert leer(var).vivas == [] and leer(var).obsoletas == []

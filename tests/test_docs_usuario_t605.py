"""Tests de la documentación de usuario (F6/T-605).

La documentación del operador es un **artefacto del sistema**: si un comando
existe y no está documentado, el operador no lo puede usar; si la doc describe un
comando que no existe, manda al operador a un callejón sin salida. Los dos casos
son fallas, y los dos se pueden verificar.

Estos tests no comprueban la *redacción* de la doc (no es automatizable): acotan
que la **cobertura** y las **referencias** estén completas contra la fuente
factual — `COMANDOS` del CLI y los archivos de la guía. La prosa sigue siendo
responsabilidad de quien escribe.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

from voucherflow.cli.main import COMANDOS

RAIZ_V2 = Path(__file__).resolve().parents[1]
DOC_USUARIO = RAIZ_V2 / "docs" / "usuario"

#: Los documentos que la guía del operador debe tener (F6/T-605; T-606 sumó el de
#: la API HTTP).
DOCS_REQUERIDOS = (
    "README.md",
    "01-instalacion.md",
    "02-comandos.md",
    "03-revision-humana.md",
    "04-salidas.md",
    "05-api-http.md",
)

#: La guía de lotes vive en la raíz del repo (ligada desde `README.md`).
BATCH_MD = RAIZ_V2.parent / "BATCH.md"


def _texto(nombre: str) -> str:
    return (DOC_USUARIO / nombre).read_text(encoding="utf-8")


def _secciones(texto: str, nivel: int = 2) -> list[str]:
    """Títulos de las secciones de un nivel dado (``## `` por defecto)."""
    return re.findall(rf"^{'#' * nivel} (.+)$", texto, re.MULTILINE)


def _encabezado_de_comando(nombre: str) -> str:
    """El texto del encabezado ``## `<nombre>` — …`` de la referencia."""
    return f"`{nombre}`"


# ---------------------------------------------------------------------------
# Cobertura
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("nombre", DOCS_REQUERIDOS)
def test_los_documentos_de_usuario_existen(nombre: str) -> None:
    assert (DOC_USUARIO / nombre).is_file(), (
        f"Falta docs/usuario/{nombre}: la guía del operador está incompleta "
        "(F6/T-605)."
    )


def test_la_guia_de_lotes_existe_en_la_raiz() -> None:
    assert BATCH_MD.is_file(), (
        "Falta BATCH.md en la raíz del repo: es la guía de lotes largos "
        "(T-602) y la referencia de formato del README del operador."
    )


@pytest.mark.parametrize("comando", COMANDOS)
def test_cada_comando_tiene_su_seccion(comando: str) -> None:
    """Todo comando del contrato (E-CLI-1) está documentado.

    Se busca el encabezado ``## `<comando>` — …`` / ``## `<comando>` …``. Un
    comando sin sección es un comando que el operador no puede descubrir.
    """
    secciones = _secciones(_texto("02-comandos.md"))
    esperado = _encabezado_de_comando(comando)
    assert any(esperado in seccion for seccion in secciones), (
        f"El comando {comando!r} no tiene sección en 02-comandos.md "
        f"(secciones: {secciones})."
    )


def test_no_hay_secciones_de_comandos_inexistentes() -> None:
    """La referencia no documenta comandos que **no existen**.

    Documentar un comando inventado es peor que no documentarlo: el operador lo
    escribe y falla. Se extraen los encabezados con forma ``## `x``` y se exige
    que ``x`` esté en el contrato.
    """
    secciones = _secciones(_texto("02-comandos.md"))
    declarados = {
        coincide.group(1)
        for seccion in secciones
        if (coincide := re.match(r"^`([a-z][a-z-]*)`", seccion))
    }
    assert declarados, "No se reconoció ningún encabezado de comando."
    inexistentes = declarados - set(COMANDOS)
    assert not inexistentes, (
        f"02-comandos.md documenta comandos que no existen: "
        f"{sorted(inexistentes)} (válidos: {list(COMANDOS)})."
    )


def test_la_referencia_documenta_todos_los_comandos_del_contrato() -> None:
    """El conjunto documentado y el conjunto real son **el mismo**.

    Es la versión fuerte de la cobertura: no alcanza con que cada comando tenga
    sección; tampoco puede sobrar ninguna.
    """
    secciones = _secciones(_texto("02-comandos.md"))
    declarados = {
        coincide.group(1)
        for seccion in secciones
        if (coincide := re.match(r"^`([a-z][a-z-]*)`", seccion))
    }
    assert declarados == set(COMANDOS)


# ---------------------------------------------------------------------------
# Referencias entre documentos
# ---------------------------------------------------------------------------


def _enlaces_markdown(texto: str) -> list[str]:
    return re.findall(r"\[[^\]]*\]\(([^)]+)\)", texto)


def test_el_readme_enlaza_los_documentos_que_promete() -> None:
    """Todo destino relativo del README existe (no hay enlaces roto)."""
    roto: list[str] = []
    for destino in _enlaces_markdown(_texto("README.md")):
        if "://" in destino or destino.startswith("#"):
            continue
        ruta = (DOC_USUARIO / destino).resolve()
        if not ruta.exists():
            roto.append(destino)
    assert not roto, f"docs/usuario/README.md tiene enlaces rotos: {roto}"


@pytest.mark.parametrize("nombre", DOCS_REQUERIDOS[1:])
def test_cada_documento_vuelve_al_indice(nombre: str) -> None:
    """Los documentos de la guía enlazan al README (navegación con vuelta)."""
    assert "README.md" in _texto(nombre), (
        f"docs/usuario/{nombre} no tiene el enlace de vuelta a README.md."
    )


def test_las_referencias_relativas_de_la_guia_existen() -> None:
    """Los enlaces *relativos* de toda la guía resuelven a algo real.

    Se resuelven desde el directorio del documento —incluido `../../BATCH.md`,
    que sale de `docs/usuario/`— así que un cambio de ubicación se detecta.
    """
    roto: list[str] = []
    for nombre in DOCS_REQUERIDOS:
        for destino in _enlaces_markdown(_texto(nombre)):
            if "://" in destino or destino.startswith("#"):
                continue
            if not (DOC_USUARIO / destino).resolve().exists():
                roto.append(f"{nombre} → {destino}")
    assert not roto, f"Enlaces relativos rotos en la guía: {roto}"


# ---------------------------------------------------------------------------
# Honestidad: los límites documentados siguen siendo ciertos
# ---------------------------------------------------------------------------


def test_la_guia_declara_que_no_hay_comando_de_correccion_hitl() -> None:
    """La doc dice que el CLI **no** carga correcciones (es el estado real).

    Es el test que impide que la documentación prometa una capacidad que no
    existe. Si algún día se agrega el comando, este test falla y obliga a
    actualizar la doc a propósito — que es exactamente lo que se quiere.
    """
    from voucherflow.cli.main import DESPACHO

    texto = _texto("03-revision-humana.md")
    assert "no está implementado" in texto or "no existe" in texto, (
        "03-revision-humana.md debe declarar que la carga de correcciones desde "
        "el CLI no está implementada."
    )
    # La cola HITL del CLI es de **lectura**: `hitl list` y `case show`.
    assert set(DESPACHO) >= {"hitl", "case"}
    assert not any(
        "correccion" in destino or "revision" in destino for destino in DESPACHO
    ), "Apareció un comando de correcciones: actualizar 03-revision-humana.md."


def test_la_instalacion_no_promete_dependencias_inexistentes() -> None:
    """El documento de instalación menciona los roles de modelo **reales**.

    Un rol inventado en la doc manda al operador a configurar algo que el sistema
    no consulta.
    """
    from voucherflow.settings.config import _DEFAULTS

    texto = _texto("01-instalacion.md")
    roles = set(_DEFAULTS["modelos"])
    for rol in roles:
        assert f"`{rol}`" in texto, f"01-instalacion.md no documenta el rol {rol!r}."
    # Los modelos por defecto que se listan deben ser los configurados.
    for rol, definicion in _DEFAULTS["modelos"].items():
        modelo = definicion["modelo"]
        if modelo == "docling":
            continue  # el rol `ocr` no es un modelo de Ollama.
        assert modelo in texto, (
            f"01-instalacion.md no menciona el modelo por defecto del rol "
            f"{rol!r} ({modelo}): el `ollama pull` documentado no lo instalaría."
        )


def test_la_instalacion_documenta_las_claves_de_configuracion_reales() -> None:
    """Las claves de configuración que la doc muestra existen en los defaults.

    Un operador que copia un `voucherflow.yaml` con una clave inventada no recibe
    un error: recibe un archivo que no hace nada. Verificar las claves contra el
    default real es lo que evita ese silencio.

    Cada grupo se documenta en el documento que le corresponde (`cooling` y
    `ollama` en la instalación; `hitl` en el flujo de revisión): la guía separa
    "dejar el sistema andando" de "operar la revisión", y forzar las claves de
    HITL en el doc de instalación metería política de revisión donde el operador
    solo quiere arrancar.
    """
    from voucherflow.settings.config import _DEFAULTS

    por_grupo = {
        "ollama": ("01-instalacion.md", ("url",)),
        "cooling": ("01-instalacion.md", ("enabled", "work_window_s", "cool_down_s")),
        "hitl": (
            "03-revision-humana.md",
            ("muestreo_tasa", "muestreo_semilla", "muestreo_activo"),
        ),
    }
    for grupo, (documento, claves) in por_grupo.items():
        assert grupo in _DEFAULTS, f"El default real no tiene el grupo {grupo!r}."
        texto = _texto(documento)
        for clave in claves:
            assert clave in _DEFAULTS[grupo], (
                f"El default real no tiene {grupo}.{clave}: la doc lo muestra."
            )
            assert clave in texto, (
                f"{documento} no documenta la clave {grupo}.{clave}, que sí existe "
                "en la configuración."
            )
    # El patrón de variables de entorno (doble guion bajo) se explica donde se
    # explica la configuración.
    assert "VOUCHERFLOW__OLLAMA__URL" in _texto("01-instalacion.md"), (
        "La doc no muestra el patrón de variables de entorno (doble guion bajo)."
    )


def test_las_secciones_de_configuracion_usan_claves_reales() -> None:
    """Ningún bloque YAML de la guía usa una clave que el default no tenga.

    Es la contracara del test anterior: además de que las claves reales estén
    documentadas, se vigila que la doc no introduzca ninguna **inventada** (una
    clave con typo se ignora en silencio en el merge, así que el sistema arranca
    con el valor por defecto sin avisar).
    """
    from voucherflow.settings.config import _DEFAULTS

    claves_validas: set[str] = set(_DEFAULTS)
    for valor in _DEFAULTS.values():
        if isinstance(valor, dict):
            claves_validas |= set(valor)

    sospechosas: list[str] = []
    for nombre in DOCS_REQUERIDOS:
        dentro_de_yaml = False
        for linea in _texto(nombre).splitlines():
            if linea.strip().startswith("```yaml"):
                dentro_de_yaml = True
                continue
            if dentro_de_yaml and linea.strip().startswith("```"):
                dentro_de_yaml = False
                continue
            if not dentro_de_yaml:
                continue
            coincide = re.match(r"^\s{0,4}(\w+):", linea)
            # Solo las claves de primer nivel (las anidadas se cubren arriba);
            # `modelos` y los nombres de rol se validan contra el default.
            if coincide and coincide.group(1) not in claves_validas:
                sospechosas.append(f"{nombre}: {coincide.group(1)}")
    assert not sospechosas, (
        f"Bloques YAML de la guía con claves que no existen en la configuración: "
        f"{sospechosas}. Válidas: {sorted(claves_validas)}."
    )


# ---------------------------------------------------------------------------
# README de v2
# ---------------------------------------------------------------------------


def test_la_guia_documenta_la_api_http_con_su_alcance() -> None:
    """La API HTTP (T-606) está documentada **con sus límites declarados**.

    Un documento que solo muestra cómo levantarla y no dice que no trae
    autenticación es peor que no documentarla: alguien la publica y queda un
    endpoint abierto sin saberlo. Se exige que diga las dos cosas: cómo se usa y
    qué no hace.
    """
    from voucherflow.http import RUTAS

    texto = _texto("05-api-http.md")
    # Las rutas reales del servidor están en la guía.
    for ruta in RUTAS:
        assert ruta.camino in texto, (
            f"05-api-http.md no documenta la ruta {ruta.metodo} {ruta.camino}."
        )
    # Y los límites que importan para no exponerla por accidente.
    for limite in ("autenticaci", "TLS", "127.0.0.1"):
        assert limite.lower() in texto.lower(), (
            f"05-api-http.md no declara {limite!r}: el alcance de seguridad de la "
            "API tiene que estar escrito, no supuesto."
        )
    # Los códigos de estado que el operador va a ver.
    for codigo in ("400", "404", "405", "422", "503", "500"):
        assert codigo in texto, f"05-api-http.md no documenta el código {codigo}."


def test_el_indice_enlaza_la_guia_de_la_api_http() -> None:
    """El documento nuevo está colgado del índice (no es un archivo huérfano)."""
    assert "05-api-http.md" in _texto("README.md")


def test_el_readme_de_v2_enlaza_la_guia_del_operador() -> None:
    """`v2/README.md` apunta a `docs/usuario/` y el enlace resuelve."""
    texto = (RAIZ_V2 / "README.md").read_text(encoding="utf-8")
    assert "docs/usuario/" in texto, (
        "v2/README.md no enlaza la guía del operador: quien llega al paquete no "
        "encuentra cómo usarlo (F6/T-605)."
    )
    for destino in _enlaces_markdown(texto):
        if "://" in destino or destino.startswith("#"):
            continue
        if "docs/usuario" in destino:
            assert (RAIZ_V2 / destino).resolve().exists(), (
                f"v2/README.md enlaza {destino}, que no existe."
            )

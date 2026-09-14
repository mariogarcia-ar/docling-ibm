"""`.env.example` como documentación **verificable** de las variables.

El archivo se declara a sí mismo «la documentación viva de qué variables
existen», pero hasta ahora nada lo comprobaba, y ya había derivado de dos
maneras:

1. **Citaba scripts que no existen** (`scripts/validar_comprobantes_{openai,
   deepseek}.py`, retirados al unificarse en `voucherflow-lab`). Documentar una
   herramienta inexistente manda al operador a un callejón sin salida.
2. **Le faltaba `GEMINI_API_KEY`**: el proveedor estaba implementado y
   documentado en la guía, pero su variable no aparecía en la plantilla.

La lección es la del resto del repo: **un artefacto que se declara fuente de
verdad tiene que estar verificado contra el código**, o miente en silencio. Lo
que se comprueba acá es la **cobertura** (existen las variables que el código
lee) y la **honestidad** (no se documentan variables inventadas); la redacción
sigue siendo responsabilidad de quien escribe.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

from voucherflow.llm.proveedores import PROVEEDORES
from voucherflow.settings.config import _DEFAULTS

RAIZ = Path(__file__).resolve().parents[1]
ENV_EXAMPLE = RAIZ / ".env.example"

#: Claves de la configuración que NO se documentan a propósito.
#:
#: `schema_version` es de formato del archivo de configuración, y `rol` es el
#: nombre del rol repetido dentro de su propia sección: son estructurales, nadie
#: los configura a mano. Documentarlos daría la falsa impresión de que hay algo
#: que decidir ahí.
NO_SE_DOCUMENTAN = frozenset(
    {
        "VOUCHERFLOW__SCHEMA_VERSION",
        "VOUCHERFLOW__MODELOS__OCR__ROL",
        "VOUCHERFLOW__MODELOS__VLM__ROL",
        "VOUCHERFLOW__MODELOS__LLM__ROL",
        "VOUCHERFLOW__MODELOS__AGENTE__ROL",
    }
)

#: Herramientas y scripts retirados: nombrarlos en la plantilla es una promesa rota.
RETIRADOS = (
    "validar_comprobantes_openai",
    "validar_comprobantes_deepseek",
    "reducir-tokens",
    "reducir_tokens",
)


def _texto() -> str:
    return ENV_EXAMPLE.read_text(encoding="utf-8")


def _documentadas() -> set[str]:
    """Variables declaradas en la plantilla (comentadas o no).

    Se acepta la forma ``# VAR=…`` porque la plantilla **comenta** lo que el
    operador descomenta: eso es lo que lo hace una plantilla.
    """
    return {
        coincide.group(1)
        for coincide in re.finditer(
            r"^#?\s*(VOUCHERFLOW__[A-Z_]+|[A-Z_]*API_KEY|ARCA_[A-Z_]+)\s*=",
            _texto(),
            re.MULTILINE,
        )
    }


def _claves_reales() -> set[str]:
    """Claves ``VOUCHERFLOW__…`` que la configuración lee de verdad."""
    claves: set[str] = set()
    for seccion, valor in _DEFAULTS.items():
        if not isinstance(valor, dict):
            claves.add(f"VOUCHERFLOW__{seccion.upper()}")
            continue
        for clave, sub in valor.items():
            if isinstance(sub, dict):
                for nombre in sub:
                    claves.add(
                        f"VOUCHERFLOW__{seccion.upper()}__"
                        f"{clave.upper()}__{nombre.upper()}"
                    )
            else:
                claves.add(f"VOUCHERFLOW__{seccion.upper()}__{clave.upper()}")
    return claves


# ---------------------------------------------------------------------------
# El archivo existe y se declara lo que es
# ---------------------------------------------------------------------------


def test_la_plantilla_existe_y_esta_versionada() -> None:
    """`.env` está ignorado y `.env.example` se commitea: hay que poder leerlo."""
    assert ENV_EXAMPLE.is_file(), (
        "Falta .env.example: es la plantilla que el operador copia a .env "
        "(docs/usuario/01-instalacion.md)."
    )


def test_la_plantilla_no_tiene_claves_reales() -> None:
    """⚠️ La plantilla **no** puede contener una credencial.

    Un valor no vacío en un archivo versionado es una clave filtrada. Se busca
    cualquier asignación sin comentar con valor.
    """
    con_valor = [
        linea
        for linea in _texto().splitlines()
        if linea.strip()
        and not linea.strip().startswith("#")
        and re.match(r"^[A-Z_]+\s*=\s*\S+", linea.strip())
    ]
    assert not con_valor, (
        f".env.example tiene variables con valor (¿una clave filtrada?): {con_valor}"
    )


# ---------------------------------------------------------------------------
# Cobertura: las variables que el código lee están documentadas
# ---------------------------------------------------------------------------


def test_todos_los_proveedores_tienen_su_variable_documentada() -> None:
    """Cada proveedor del catálogo tiene su `…_API_KEY` en la plantilla.

    Es la falla que se destapó: `gemini` existía en el código y en la guía, pero
    su variable no estaba acá.
    """
    documentadas = _documentadas()
    faltantes = sorted(
        adaptador().capacidades.variable_api_key
        for adaptador in PROVEEDORES.values()
        if adaptador().capacidades.variable_api_key not in documentadas
    )
    assert not faltantes, (
        f".env.example no documenta la credencial de estos proveedores: {faltantes}"
    )


@pytest.mark.parametrize("clave", sorted(_claves_reales() - NO_SE_DOCUMENTAN))
def test_las_claves_de_la_configuracion_estan_documentadas(clave: str) -> None:
    """Toda clave configurable aparece en la plantilla.

    Si se agrega una sección a `_DEFAULTS` y no se documenta, el operador no
    tiene forma de saber que existe. (Las estructurales se excluyen arriba.)
    """
    assert clave in _documentadas(), (
        f"{clave} se lee de la configuración pero no está en .env.example."
    )


def test_las_exclusiones_siguen_siendo_claves_reales() -> None:
    """Control de la lista de exclusiones: si una clave desaparece, hay que sacarla.

    Sin esto, la lista se vuelve un cajón de sastre que esconde claves sin
    documentar.
    """
    reales = _claves_reales()
    fantasma = sorted(NO_SE_DOCUMENTAN - reales)
    assert not fantasma, (
        f"NO_SE_DOCUMENTAN nombra claves que ya no existen: {fantasma}"
    )


# ---------------------------------------------------------------------------
# Honestidad: no se documenta lo que no existe
# ---------------------------------------------------------------------------


def test_no_documenta_claves_inventadas() -> None:
    """Una variable con prefix `VOUCHERFLOW__` que la config no lee es un error.

    Un typo en la plantilla hace que el operador configure algo que no tiene
    efecto — y no hay forma de notarlo desde afuera.
    """
    reales = _claves_reales()
    inventadas = sorted(
        clave
        for clave in _documentadas()
        if clave.startswith("VOUCHERFLOW__") and clave not in reales
    )
    assert not inventadas, (
        f".env.example documenta claves que la configuración no lee: {inventadas}"
    )


@pytest.mark.parametrize("retirado", RETIRADOS)
def test_no_cita_herramientas_retiradas(retirado: str) -> None:
    """No se nombran scripts que ya no existen (mandan a un callejón sin salida)."""
    assert retirado not in _texto(), (
        f".env.example cita {retirado!r}, que ya no existe en el repo."
    )


def test_nombra_la_herramienta_que_si_existe() -> None:
    """La contracara: el laboratorio que reemplazó a los scripts sí se nombra."""
    assert "voucherflow-lab" in _texto()


def test_advierte_que_el_env_no_se_carga_solo() -> None:
    """⚠️ El aviso que evita la confusión más común.

    Un `.env` en disco no tiene efecto por sí solo: el sistema lee el entorno
    del proceso. La única excepción (`voucherflow-lab --env`) también se declara.
    """
    texto = _texto()
    assert "NO se carga solo" in texto
    assert "--env" in texto

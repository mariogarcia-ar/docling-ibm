"""Verifica que el manual documente exactamente las banderas del parser.

Mismo criterio que `tests/test_docs_usuario_t605.py`: una bandera documentada y
inexistente es peor que no documentarla.
"""

from __future__ import annotations

import re
from pathlib import Path

from voucherflow.llm import cli

MANUAL = Path("manual/user/llm.md")
BANDERAS_COMUNES = {"-h", "--help"}


def _texto() -> str:
    return MANUAL.read_text(encoding="utf-8")


def _sin_bloques_de_codigo(texto: str) -> str:
    """Quita los bloques ```…``` (fences).

    ⚠️ Sin esto el emparejamiento de backticks se desalinea: los ``` de los
    fences se toman como backticks simples y todo lo que sigue se lee mal.
    """
    return re.sub(r"```.*?```", "", texto, flags=re.DOTALL)


def _banderas_reales() -> set[str]:
    """Toda bandera larga y corta que el parser acepta."""
    parser = cli.construir_parser()
    reales: set[str] = set()
    for accion in parser._actions:
        reales.update(accion.option_strings)
    return reales


def _banderas_documentadas() -> set[str]:
    """Banderas del manual, leídas de la **cabecera** de cada ``código``.

    Las tablas escriben ``-o, --salida DIR`` o ``-M, --operacion {…}``: se toman
    los tokens iniciales que son banderas y se corta en el primero que no lo es
    (el `DIR`, el `{…}`, el `LISTA`).

    Mirar todo el contenido del span tomaría por bandera cualquier cosa que
    empiece con guion, como el ``set -a`` de un ejemplo de shell.
    """
    encontradas: set[str] = set()
    for bloque in re.findall(r"`([^`]+)`", _sin_bloques_de_codigo(_texto())):
        for crudo in bloque.split():
            candidato = crudo.rstrip(",")
            if not re.fullmatch(r"-\w|--[\w-]+", candidato):
                break  # llegó el valor: la cabecera terminó
            encontradas.add(candidato)
    return encontradas


def test_toda_bandera_documentada_existe():
    """Ninguna bandera inventada: el manual no puede prometer lo que no hay."""
    inventadas = _banderas_documentadas() - _banderas_reales() - BANDERAS_COMUNES
    assert not inventadas, f"banderas documentadas que no existen: {sorted(inventadas)}"


def test_toda_bandera_real_esta_documentada():
    """Tampoco al revés: una bandera sin documentar es una capacidad escondida."""
    sin_documentar = _banderas_reales() - _banderas_documentadas() - BANDERAS_COMUNES
    assert not sin_documentar, (
        f"banderas del parser sin documentar en el manual: {sorted(sin_documentar)}"
    )


def test_documenta_los_proveedores_reales():
    from voucherflow.llm.proveedores import PROVEEDORES

    texto = _texto()
    for nombre in PROVEEDORES:
        assert nombre in texto, f"el proveedor {nombre} no está documentado"


def test_documenta_las_operaciones_reales():
    texto = _texto()
    for operacion in cli.OPERACIONES:
        assert operacion in texto, f"la operación {operacion} no está documentada"


def test_los_sufijos_documentados_son_los_reales():
    """La tabla de sufijos del manual tiene que coincidir con `ejecutar`."""
    import inspect

    from voucherflow.llm import corrida

    fuente = inspect.getsource(corrida.ejecutar)
    for operacion, sufijo in (
        ("extraer", "extraccion"),
        ("validar", "validacion"),
        ("diff", "validacion"),
    ):
        assert f'"{operacion}": "{sufijo}"' in fuente, (
            f"el manual dice {operacion} → {sufijo}: verificá `corrida.ejecutar`"
        )


def test_documenta_los_codigos_de_salida_reales():
    """0/1/2 son los que devuelve el código; el manual no puede inventar otros."""
    texto = _texto()
    assert "| `0` |" in texto
    assert "| `1` |" in texto
    assert "| `2` |" in texto


def test_los_enlaces_markdown_resuelven():
    """Un enlace relativo roto es un callejón sin salida para el operador."""
    base = MANUAL.parent
    for destino in re.findall(r"\]\(([^)#][^)]*)\)", _texto()):
        if destino.startswith(("http://", "https://")):
            continue
        ruta = (base / destino.split("#")[0]).resolve()
        assert ruta.exists(), f"enlace roto en el manual: {destino}"


def test_menciona_que_gasta_dinero():
    """Es lo primero que hay que saber de una herramienta que llama a una API paga."""
    texto = _texto()
    assert "--dry-run" in texto
    assert "gasta" in texto.lower() or "paga" in texto.lower()

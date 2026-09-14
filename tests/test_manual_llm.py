"""Verifica que el manual documente exactamente las banderas del parser.

Mismo criterio que `tests/test_docs_usuario_t605.py`: una bandera documentada y
inexistente es peor que no documentarla.
"""

from __future__ import annotations

import re
import unicodedata
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


def _ancla(titulo: str) -> str:
    """Ancla que GitHub genera para un título: minúsculas, sin tildes, con guiones.

    Es la misma normalización que usa el render de Markdown (y la que rompió el
    único enlace interno que escribí a mano: `#la-aritmética-…` con tilde).
    """
    sin_tildes = "".join(
        c
        for c in unicodedata.normalize("NFD", titulo.lower())
        if unicodedata.category(c) != "Mn"
    )
    limpio = re.sub(r"[^\w\s-]", "", sin_tildes)
    return re.sub(r"\s+", "-", limpio.strip())


def test_los_anclajes_internos_existen():
    """⚠️ Un `#ancla` mal escrito no falla el render: lleva a la nada.

    `test_los_enlaces_markdown_resuelven` no los cubre —su regex excluye los que
    empiezan con `#`—, así que un anclaje con la tilde puesta (`#la-aritmética-…`
    en vez de `#la-aritmetica-…`) pasaba desapercibido.
    """
    texto = _texto()
    anclas = {_ancla(t) for t in re.findall(r"^#{2,3} (.+)$", texto, re.M)}
    for destino in re.findall(r"\]\(#([^)]+)\)", texto):
        assert destino in anclas, (
            f"anclaje interno roto: #{destino}. "
            f"Anclas disponibles: {sorted(anclas)}"
        )


def test_menciona_que_gasta_dinero():
    """Es lo primero que hay que saber de una herramienta que llama a una API paga."""
    texto = _texto()
    assert "--dry-run" in texto
    assert "gasta" in texto.lower() or "paga" in texto.lower()


def test_declara_que_es_la_vara_de_calidad_del_run():
    """El uso previsto del utilitario tiene que estar al principio, no escondido.

    Va al inicio porque es la razón por la que se corre: si esto se muda a una
    nota al pie, el operador no sabe que el `diff` es lo que mide calidad.
    """
    texto = _texto()
    cabecera = texto[:2000]
    assert "Nota de uso previsto" in cabecera, "la nota de uso previsto se movió"
    assert "calidad del run" in texto
    # Y tiene que decir cómo se mide, no solo que se mide.
    assert "-M diff" in texto or "--operacion diff" in texto
    assert "--datos" in texto


def test_documenta_que_el_costo_de_imagen_depende_del_proveedor():
    """⚠️ El manual nombraba solo la fórmula de DeepSeek («tope fijo»).

    Con `-p openai` la estimación erró de 0,9x a 12x, y el operador no tenía cómo
    saber que el número cambiaba según el proveedor.
    """
    texto = _texto()
    assert "tope fijo" in texto
    assert "mosaicos" in texto
    # Y los números que hacen la diferencia visible.
    assert "1.105" in texto  # el A4 que se subestimaba
    assert "85" in texto  # el detalle low que se sobreestimaba 12x


def test_documenta_el_limite_de_peso_real():
    """El corte práctico es ~24 MB por el inflado de base64, no 32."""
    texto = _texto()
    assert "base64" in texto
    assert "24 MB" in texto or "24 MB" in texto
    assert "corpus" in texto  # a dónde mandarlo a reducir


def test_las_estrategias_documentadas_son_las_reales():
    """La tabla del manual no puede inventar una estrategia que el código no tiene."""
    from voucherflow.llm.protocolo import ESTRATEGIAS_IMAGEN

    texto = _texto()
    for estrategia in ESTRATEGIAS_IMAGEN:
        # `tope_fijo` se documenta como «tope fijo», `mosaicos` como «mosaicos».
        legible = estrategia.replace("_", " ")
        assert legible in texto, f"la estrategia {estrategia!r} no está documentada"

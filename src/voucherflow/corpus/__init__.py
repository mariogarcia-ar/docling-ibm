"""Reducción de corpus: pre-reducir peso y tokens de visión de las imágenes.

Capacidad del pipeline que **baja el costo de procesamiento**: menos bytes en
disco/red y, sobre todo, **menos tokens de visión** si después las imágenes se le
pasan a un VLM (qwen2.5vl, Docling VLM, etc.).

Nació como ``scripts/operacion/reducir-tokens.py`` (1181 líneas, sin un solo
test) y se movió acá porque el pipeline la necesita como funcionalidad, no como
utilidad suelta. Al moverla se corrigieron tres cosas que el script arrastraba:

1. **Los helpers de recorrido estaban duplicados** con
   :mod:`voucherflow.llm.corrida` (``_expandir``, ``_raiz_espejado``,
   ``_es_nivel_de_corpus``, ``_esta_dentro``): dos copias de la regla que decide
   dónde se escribe cada archivo. La reanudación depende de esa regla, así que
   una divergencia se paga dos veces. Ahora viven en
   :mod:`voucherflow.corpus.recorrido`.
2. **Cero cobertura de tests** sobre 1181 líneas. Ahora hay suite (ver
   ``tests/test_corpus_*.py``).
3. **Sin contrato de datos**: el reporte se armaba con dicts sueltos. Ahora
   :class:`~voucherflow.corpus.modelo.Resultado` es tipado y reconstruible
   (``desde_dict``), y el reporte lleva ``version``.

Una segunda ronda de revisión (contrato ``voucherflow-corpus@2``) corrigió
cuatro defectos que la primera no cubría, todos de la misma familia —**el
reporte no puede afirmar algo que no pasó**—:

4. **Dos originales escribiendo el mismo destino.** Con ``--formato jpg`` (o con
   ``foto.JPG`` y ``foto.jpg``) el plan las colisionaba: quedaba un archivo y el
   reporte contaba **dos** reducidos. Ahora
   :func:`~voucherflow.corpus.corrida.detectar_colisiones` rechaza el lote antes
   de escribir nada.
5. **Reducir podía AUMENTAR el peso.** ``convert("RGB")`` incondicional inflaba
   un escaneo 1-bit; el modo del original ahora se conserva, y si el peso sube el
   resumen lo avisa y lista los archivos (dentro del total quedaban invisibles).
6. **``--sin-alinear`` era inerte** cuando el cálculo pasaba por la librería de
   Docling (que alinea siempre): la bandera solo llegaba al fallback.
7. **El formato se elegía por el original**, no por el destino, así que
   ``foto.webp`` salía con bytes JPEG (el defecto del loop base, corregido solo
   para ``.png``).

Uso:
    voucherflow corpus <ruta|carpeta>... [opciones]

Para el detalle de cada bandera: ``voucherflow corpus --help``.
"""

from __future__ import annotations

from .corrida import (
    EXTENSIONES_POR_DEFECTO,
    VERSION_CORPUS,
    ErrorCorpus,
    contar_fallos,
    describir_colisiones,
    detectar_colisiones,
    ejecutar,
    escribir_reporte,
    normalizar_extensiones,
    planificar,
    validar,
)
from .dimensiones import (
    CALIDAD,
    FACTOR_PATCH_QWEN2VL,
    LADO_MAYOR_PX,
    LADO_MENOR_MINIMO_PX,
    ORIGEN_DEFAULTS,
    dimensiones_objetivo,
    tokens_estimados_vlm,
)
from .modelo import (
    CATEGORIA_COPIA,
    CATEGORIA_LECTURA,
    CATEGORIA_REANUDADO,
    CATEGORIAS,
    ESTADO_FALLO,
    ESTADO_OMITIDO,
    ESTADO_REDUCIDO,
    ESTADOS,
    Opciones,
    Resultado,
)
from .recorrido import (
    es_nivel_de_corpus,
    esta_dentro,
    expandir,
    raiz_espejado,
    salida_de,
)
from .reduccion import procesar
from .reporte import comparar, resumen

__all__ = [
    "CALIDAD",
    "CATEGORIAS",
    "CATEGORIA_COPIA",
    "CATEGORIA_LECTURA",
    "CATEGORIA_REANUDADO",
    "ESTADOS",
    "ESTADO_FALLO",
    "ESTADO_OMITIDO",
    "ESTADO_REDUCIDO",
    "EXTENSIONES_POR_DEFECTO",
    "ErrorCorpus",
    "FACTOR_PATCH_QWEN2VL",
    "LADO_MAYOR_PX",
    "LADO_MENOR_MINIMO_PX",
    "ORIGEN_DEFAULTS",
    "Opciones",
    "Resultado",
    "VERSION_CORPUS",
    "comparar",
    "contar_fallos",
    "describir_colisiones",
    "detectar_colisiones",
    "dimensiones_objetivo",
    "ejecutar",
    "es_nivel_de_corpus",
    "escribir_reporte",
    "esta_dentro",
    "expandir",
    "normalizar_extensiones",
    "planificar",
    "procesar",
    "raiz_espejado",
    "resumen",
    "salida_de",
    "tokens_estimados_vlm",
    "validar",
]

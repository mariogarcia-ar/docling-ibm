"""Extracción de texto nativo de PDF con ``pdftotext --layout`` (F1 / T-104).

Recupera el **layout visual** (columnas, tablas, campos alineados) de un PDF
con capa de texto usando el utilitario ``pdftotext -layout`` de **poppler**
(PROC.md §5.2: para PDF apto, ``pdftotext --layout`` da "excelente layout:
recupera columnas/alineación"; Docling directo aplana columnas, caso
``9dfc597f``).

Es el **complemento** de la ruta texto nativo de la orquestación: cuando el
routing marca un PDF como ``apto`` (texto nativo real, las imágenes no
dominan), conviene extraerlo con ``pdftotext --layout`` **antes** de gastar
Docling. Si el utilitario no está disponible o falla, el llamador hace
fallback a Docling directo (comportamiento previo; decisión A1 revertida —
ver docs 05-plan/F1-subplan.md §2.6 y PROC.md §5.3).

Requiere poppler (``pdftotext``) en el sistema. Si no está, ``extraer()``
devuelve ``None`` (sin lanzar): la orquestación decide el fallback.
"""

from __future__ import annotations

import shutil
import subprocess
import tempfile
from pathlib import Path

#: Nombre del ejecutable de poppler que se busca en el PATH.
PDFTOTEXT_BIN = "pdftotext"


def pdftotext_disponible() -> bool:
    """¿Está ``pdftotext`` disponible en el sistema?

    Usa ``shutil.which`` para localizar el binario de poppler. Es la guarda
    barata que evita lanzar subprocesos cuando el utilitario no existe.
    """
    return shutil.which(PDFTOTEXT_BIN) is not None


def extraer_con_pdftotext_layout(pdf: str | Path) -> str | None:
    """Extrae el texto de un PDF con ``pdftotext -layout``.

    Argumentos:
        pdf: ruta al PDF con capa de texto (routing ``apto``).

    Devuelve:
        El texto extraído (UTF-8) con el layout de columnas preservado, o
        ``None`` si ``pdftotext`` no está disponible o falló (el llamador
        decide el fallback; PROC.md §5.3). Un PDF apto no debería dar vacío,
        pero si ocurre se devuelve ``None`` para no propagar salida vacía.
    """
    ruta = Path(pdf)
    if not ruta.exists():
        return None
    if not pdftotext_disponible():
        return None

    with tempfile.TemporaryDirectory(prefix="vf_pdftotext_") as d:
        salida = Path(d) / "texto.txt"
        try:
            subprocess.run(
                [PDFTOTEXT_BIN, "-layout", str(ruta), str(salida)],
                check=True,
                capture_output=True,
                timeout=60,
            )
        except Exception:
            return None
        if not salida.exists():
            return None
        texto = salida.read_text(encoding="utf-8")
        return texto if texto.strip() else None


__all__ = [
    "PDFTOTEXT_BIN",
    "pdftotext_disponible",
    "extraer_con_pdftotext_layout",
]

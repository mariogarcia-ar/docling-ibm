"""Contratos de una corrida de conversión: opciones, resultado y su reporte.

Los dos son **datos planos serializables** (``a_dict()``), como
:mod:`voucherflow.corpus.modelo`: el reporte se construye con ellos y una corrida
se puede inspeccionar o reanudar sin volver a renderizar.

⚠️ **Por qué no se reusa ``corpus.modelo``.** ``Opciones`` y ``Resultado`` de
``corpus`` son de **reducción de imágenes** (lado mayor, alineación a 28, tokens
de visión, backend pillow/ffmpeg): ninguno de esos campos existe acá. Compartir
los tipos obligaría a llenar campos que no aplican y a leerlos como si
importaran. Lo que sí se comparte es lo que **no** depende del dominio: los
estados, la categoría de reanudación y el formato de bytes.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

#: Estados posibles de un archivo. Se reusa el vocabulario de ``corpus`` a
#: propósito: los dos comandos son recorridos de un corpus que escriben salidas, y
#: que un ``fallo`` signifique lo mismo en los dos es lo que permite tratarlos
#: igual en un script.
ESTADO_ESCRITO = "escrito"
ESTADO_REANUDADO = "reanudado"
ESTADO_FALLO = "fallo"

ESTADOS = frozenset({ESTADO_ESCRITO, ESTADO_REANUDADO, ESTADO_FALLO})

#: Dónde pegar el número de página y el total en el nombre del archivo.
#: El default reproduce el nombre del script original (``pagina_1.jpg``).
PATRON_UN_PDF = "pagina_{pagina}.jpg"
#: Con varios PDF el nombre lleva el del documento: sin esto, ``pagina_1.jpg`` de
#: un mes pisaría al de otro (el corpus tiene 264 PDF, varios homónimos).
PATRON_VARIOS = "{nombre}_pagina_{pagina}.jpg"

#: Tokens disponibles en ``--patron``.
TOKENS = ("nombre", "pagina", "total")

#: DPI por defecto. El mismo que el render del pipeline y que ``corpus --dpi-pdf``:
#: si divergieran, el mismo PDF se leería con distinta resolución según el comando.
DPI_DEFAULT = 300

#: Calidad JPEG (la del render del pipeline, ``jpg_quality=95``).
CALIDAD_DEFAULT = 95


@dataclass
class Opciones:
    """Opciones efectivas de una corrida."""

    salida: Path
    dpi: int = DPI_DEFAULT
    calidad: int = CALIDAD_DEFAULT
    patron: str | None = None
    primera: int | None = None
    ultima: int | None = None
    forzar: bool = False
    escribir: bool = True
    #: Recortar a la imagen mayor en vez de renderizar la página completa.
    #:
    #: ``None`` es el default **correcto** para los dos universos: el render decide
    #: por cobertura de la imagen (ver ``processing.orquestacion``). ``true`` y
    #: ``false`` existen para forzarlo, no porque haga falta saber qué hay enfrente.
    recortar: bool | None = None

    @property
    def solo_medir(self) -> bool:
        """True si no se escribe nada (simulación)."""
        return not self.escribir

    def a_dict(self) -> dict[str, Any]:
        return {
            "salida": str(self.salida),
            "dpi": self.dpi,
            "calidad": self.calidad,
            "patron": self.patron,
            "primera": self.primera,
            "ultima": self.ultima,
            "forzar": self.forzar,
            "solo_medir": self.solo_medir,
            "recortar": self.recortar,
        }


@dataclass
class Resultado:
    """Una página renderizada (o el motivo por el que no se pudo)."""

    origen: Path
    pagina: int
    destino: Path | None
    estado: str
    motivo: str = ""
    dims: tuple[int, int] | None = None
    peso: int | None = None

    def __post_init__(self) -> None:
        if self.estado not in ESTADOS:
            raise ValueError(
                f"estado desconocido: {self.estado!r} (válidos: {sorted(ESTADOS)})"
            )

    @property
    def ok(self) -> bool:
        """False solo si hubo fallo (una reanudación es un resultado válido)."""
        return self.estado != ESTADO_FALLO

    def a_dict(self) -> dict[str, Any]:
        return {
            "origen": str(self.origen),
            "pagina": self.pagina,
            "destino": str(self.destino) if self.destino else None,
            "estado": self.estado,
            "motivo": self.motivo,
            "dims": list(self.dims) if self.dims else None,
            "peso_bytes": self.peso,
        }


@dataclass
class Tarea:
    """Una página a renderizar: qué PDF, qué página y dónde va.

    Se planifica **entera** antes de escribir (y antes de pagar tiempo de render):
    así la simulación y la corrida real comparten el cálculo del destino y no
    pueden divergir.
    """

    pdf: Path
    pagina: int  # 1-based
    destino: Path


@dataclass
class Plan:
    """Lo que la corrida va a hacer, con lo que quedó afuera declarado."""

    tareas: list[Tarea] = field(default_factory=list)
    #: ``{".txt": 3}``: archivos descartados por no ser PDF.
    ignorados: dict[str, int] = field(default_factory=dict)
    #: Raíz desde la cual se espeja el árbol, con su motivo.
    raiz: Path = field(default_factory=Path.cwd)
    motivo_raiz: str = ""
    #: Problemas de lectura por PDF (un PDF ilegible no aborta el lote).
    problemas: list[str] = field(default_factory=list)

    @property
    def paginas(self) -> int:
        return len(self.tareas)

    @property
    def pdfs(self) -> int:
        """Cuántos PDF distintos aportan páginas al plan."""
        return len({t.pdf for t in self.tareas})

    def resumen(self) -> str:
        return f"{self.pdfs} PDF → {self.paginas} página(s) a renderizar"

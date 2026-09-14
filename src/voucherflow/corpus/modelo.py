"""Contratos de una corrida de reducción: opciones y resultado por archivo.

Los dos son **datos planos serializables** (``a_dict()`` / ``desde_dict()``): el
reporte JSON se construye con ellos y una corrida se puede inspeccionar o
reanudar sin volver a tocar las imágenes.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from .dimensiones import tokens_estimados_vlm

#: Estados posibles de un archivo (``ok`` es todo lo que no sea ``fallo``).
ESTADO_REDUCIDO = "reducido"
ESTADO_OMITIDO = "omitido"
ESTADO_FALLO = "fallo"

ESTADOS = frozenset({ESTADO_REDUCIDO, ESTADO_OMITIDO, ESTADO_FALLO})


@dataclass
class Opciones:
    """Opciones efectivas de una corrida."""

    salida: Path
    lado_mayor: int
    calidad: int
    piso_lado_menor: int
    alinear: bool
    backend: str
    formato: str
    forzar: bool
    escribir: bool
    copiar_no_reducidas: bool
    workers: int
    detalle: bool

    @property
    def solo_medir(self) -> bool:
        """True si no se escribe nada (modo de solo medición)."""
        return not self.escribir

    def a_dict(self) -> dict[str, Any]:
        return {
            "salida": str(self.salida),
            "lado_mayor_px": self.lado_mayor,
            "calidad": self.calidad,
            "piso_lado_menor_px": self.piso_lado_menor,
            "alinear_patch_qwen2vl": self.alinear,
            "backend": self.backend,
            "formato": self.formato,
            "forzar": self.forzar,
            "solo_medir": self.solo_medir,
            "copiar_no_reducidas": self.copiar_no_reducidas,
            "workers": self.workers,
            "detalle": self.detalle,
        }


@dataclass
class Resultado:
    """Resultado por archivo (reducido, omitido o fallido)."""

    origen: Path
    destino: Path | None
    estado: str
    motivo: str = ""
    dims_origen: tuple[int, int] | None = None
    dims_destino: tuple[int, int] | None = None
    peso_origen: int | None = None
    peso_destino: int | None = None

    def __post_init__(self) -> None:
        if self.estado not in ESTADOS:
            raise ValueError(
                f"estado desconocido: {self.estado!r} (válidos: {sorted(ESTADOS)})"
            )

    @property
    def ok(self) -> bool:
        """False solo si hubo fallo (una omisión es un resultado válido)."""
        return self.estado != ESTADO_FALLO

    @property
    def tokens_origen(self) -> int | None:
        return tokens_estimados_vlm(*self.dims_origen) if self.dims_origen else None

    @property
    def tokens_destino(self) -> int | None:
        return tokens_estimados_vlm(*self.dims_destino) if self.dims_destino else None

    def a_dict(self) -> dict[str, Any]:
        return {
            "origen": str(self.origen),
            "destino": str(self.destino) if self.destino else None,
            "estado": self.estado,
            "motivo": self.motivo,
            "dims_origen": list(self.dims_origen) if self.dims_origen else None,
            "dims_destino": list(self.dims_destino) if self.dims_destino else None,
            "peso_origen_bytes": self.peso_origen,
            "peso_destino_bytes": self.peso_destino,
            "tokens_origen": self.tokens_origen,
            "tokens_destino": self.tokens_destino,
        }

    @classmethod
    def desde_dict(cls, datos: dict[str, Any]) -> Resultado:
        """Reconstruye un resultado desde su ``a_dict()`` (para leer un reporte)."""

        def _dims(clave: str) -> tuple[int, int] | None:
            valor = datos.get(clave)
            return (int(valor[0]), int(valor[1])) if valor else None

        destino = datos.get("destino")
        return cls(
            origen=Path(datos["origen"]),
            destino=Path(destino) if destino else None,
            estado=datos.get("estado", ESTADO_FALLO),
            motivo=datos.get("motivo", ""),
            dims_origen=_dims("dims_origen"),
            dims_destino=_dims("dims_destino"),
            peso_origen=datos.get("peso_origen_bytes"),
            peso_destino=datos.get("peso_destino_bytes"),
        )

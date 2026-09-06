"""Configuración centralizada de ``voucherflow`` (módulo ``settings``).

F0/T-005 (E-LIB-3): carga de config desde YAML + variables de entorno
``VOUCHERFLOW_*``, con defaults en código. Ver ``config.py``.
"""

from __future__ import annotations

from .config import (
    DEFAULT_CONFIG_FILE,
    ENV_PREFIX,
    CoolingSettings,
    ModeloRol,
    OllamaSettings,
    Settings,
    cargar_desde_dict,
    cargar_settings,
)

__all__ = [
    "ENV_PREFIX",
    "DEFAULT_CONFIG_FILE",
    "Settings",
    "OllamaSettings",
    "ModeloRol",
    "CoolingSettings",
    "cargar_settings",
    "cargar_desde_dict",
]

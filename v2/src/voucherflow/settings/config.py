"""Configuración centralizada de ``voucherflow`` (F0, T-005).

Patrón de carga (E-LIB-3): *defaults en código* → *override por archivo YAML*
→ *override por variables de entorno* ``VOUCHERFLOW_*`` (máxima prioridad).

La configuración agrupa: URL de Ollama, modelos por rol (ocr/vlm/llm/agente),
tiempos (timeout/retry/backoff), políticas de ejecución (workers, enfriamiento
— ADR-010, aún no usado en F0) y el criterio de contratos (schema version).

Uso típico::

    from voucherflow.settings.config import Settings, cargar_settings
    settings = cargar_settings()              # defaults + YAML (si existe) + env
    settings = cargar_settings(archivo="config.yaml", prefijo_env="VOUCHERFLOW")

Las claves de entorno se mapean con un separador ``__`` para anidados:
``VOUCHERFLOW_OLLAMA__URL`` anula ``settings.ollama.url``.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable

import yaml

#: Prefijo de variables de entorno reconocidas por la librería.
ENV_PREFIX = "VOUCHERFLOW"

#: Ruta por defecto del archivo de configuración YAML (relativa al cwd).
DEFAULT_CONFIG_FILE = "voucherflow.yaml"


@dataclass
class ModeloRol:
    """Modelo asignado a un rol del pipeline (E-LIB-3).

    ``rol`` indica para qué se usa: ocr (Docling/OCR), vlm (modelo visual),
    llm (modelo de texto) o agente (decisión de conclusión).
    """

    rol: str
    modelo: str
    num_ctx: int | None = None
    temperatura: float | None = None

    def to_ollama_options(self) -> dict[str, Any]:
        """Devuelve las opciones compatibles con el payload ``options`` de Ollama."""
        opts: dict[str, Any] = {}
        if self.num_ctx is not None:
            opts["num_ctx"] = self.num_ctx
        if self.temperatura is not None:
            opts["temperature"] = self.temperatura
        return opts


@dataclass
class OllamaSettings:
    """Configuración del servidor Ollama (default local)."""

    url: str = "http://localhost:11434"
    timeout_s: float = 120.0
    max_reintentos: int = 3
    backoff_base_s: float = 1.0
    backoff_max_s: float = 15.0
    #: Lista de status HTTP que disparan reintento con backoff (429 por defecto).
    reintentar_status: tuple[int, ...] = (429, 502, 503, 504)


@dataclass
class CoolingSettings:
    """Política de enfriamiento por temperatura en lotes (ADR-010, F6)."""

    enabled: bool = False
    work_window_s: int = 600
    cool_down_s: int = 120


@dataclass
class Settings:
    """Configuración raíz de la librería.

    Los atributos son *dataclasses* tipadas (no un dict genérico) para que el
    consumidor tenga descubrimiento y validación en tiempo de desarrollo.
    """

    schema_version: str = "1.0.0"
    ollama: OllamaSettings = field(default_factory=OllamaSettings)
    modelos: dict[str, ModeloRol] = field(default_factory=dict)
    cooling: CoolingSettings = field(default_factory=CoolingSettings)
    workers: int = 1
    #: Ruta del archivo YAML efectivamente cargado (si hubo).
    archivo_config: str | None = None

    def modelo_para(self, rol: str) -> ModeloRol | None:
        """Modelo asignado a un rol (ocr/vlm/llm/agente) o ``None``."""
        return self.modelos.get(rol)

    # -- helpers de acceso directo -----------------------------------------
    @property
    def url_ollama(self) -> str:
        return self.ollama.url

    @property
    def modelo_vlm(self) -> str | None:
        m = self.modelos.get("vlm")
        return m.modelo if m else None

    @property
    def modelo_llm(self) -> str | None:
        m = self.modelos.get("llm")
        return m.modelo if m else None

    @property
    def modelo_agente(self) -> str | None:
        m = self.modelos.get("agente")
        return m.modelo if m else None


# ---------------------------------------------------------------------------
# Valores por defecto
# ---------------------------------------------------------------------------

_DEFAULTS: dict[str, Any] = {
    "schema_version": "1.0.0",
    "workers": 1,
    "ollama": {
        "url": "http://localhost:11434",
        "timeout_s": 120.0,
        "max_reintentos": 3,
        "backoff_base_s": 1.0,
        "backoff_max_s": 15.0,
        "reintentar_status": [429, 502, 503, 504],
    },
    "cooling": {"enabled": False, "work_window_s": 600, "cool_down_s": 120},
    # Modelos por rol (heredados de v1: VLM por defecto qwen2.5vl:3b).
    "modelos": {
        "ocr": {"rol": "ocr", "modelo": "docling"},
        "vlm": {"rol": "vlm", "modelo": "qwen2.5vl:3b", "num_ctx": 4096},
        "llm": {"rol": "llm", "modelo": "qwen2.5:7b", "num_ctx": 8192},
        "agente": {"rol": "agente", "modelo": "qwen2.5:7b", "num_ctx": 8192},
    },
}


# ---------------------------------------------------------------------------
# Carga y merge
# ---------------------------------------------------------------------------


def _deep_merge(base: dict[str, Any], override: dict[str, Any]) -> dict[str, Any]:
    """Merge recursivo de dicts (override gana). No muta ``base``."""
    out = dict(base)
    for k, v in override.items():
        if isinstance(v, dict) and isinstance(out.get(k), dict):
            out[k] = _deep_merge(out[k], v)
        else:
            out[k] = v
    return out


def _aplanar(prefijo: str, d: dict[str, Any], sep: str = "__") -> dict[str, str]:
    """Aplana un dict anidado a claves ``prefijo__sub`` → valor (para env)."""
    planas: dict[str, str] = {}
    for k, v in d.items():
        clave = f"{prefijo}__{k}" if prefijo else k
        if isinstance(v, dict):
            planas.update(_aplanar(clave, v, sep))
        else:
            planas[clave] = str(v)
    return planas


def _coercion(tipo_objetivo: type[Any], valor: str) -> Any:
    """Coerción básica de strings de entorno al tipo destino."""
    if tipo_objetivo is bool:
        return valor.strip().lower() in {"1", "true", "yes", "si", "sí"}
    if tipo_objetivo is int:
        return int(valor)
    if tipo_objetivo is float:
        return float(valor)
    return valor


def _aplicar_env(datos: dict[str, Any], prefijo: str = ENV_PREFIX) -> dict[str, Any]:
    """Aplica override por variables de entorno ``PREFIJO__...`` (máx. prioridad)."""
    planas_env = {
        k: v for k, v in os.environ.items() if k.startswith(f"{prefijo}__")
    }
    # Reconstruir dict anidado: VOUCHERFLOW_OLLAMA__URL → ollama.url
    # Las variables de entorno suelen escribirse en MAYÚSCULAS; las claves de
    # config (yaml/dict) van en minúscula. Se normaliza cada componente a
    # minúscula para poder matchear (VOUCHERFLOW_OLLAMA__URL == ollama.url).
    for clave_env, valor in planas_env.items():
        ruta = [p.lower() for p in clave_env[len(f"{prefijo}__"):].split("__")]
        nodo = datos
        for parte in ruta[:-1]:
            if not isinstance(nodo.get(parte), dict):
                nodo[parte] = {}
            nodo = nodo[parte]
        ultima = ruta[-1]
        # Tipo objetivo para coerción
        objetivo: Any = None
        if isinstance(nodo.get(ultima), bool):
            objetivo = bool
        elif isinstance(nodo.get(ultima), int):
            objetivo = int
        elif isinstance(nodo.get(ultima), float):
            objetivo = float
        elif isinstance(nodo.get(ultima), list):
            objetivo = list
        if objetivo is list:
            try:
                import json

                nodo[ultima] = json.loads(valor)
            except Exception:
                nodo[ultima] = [x.strip() for x in valor.split(",") if x.strip()]
        else:
            nodo[ultima] = _coercion(objetivo, valor) if objetivo is not None else valor
    return datos


def _leer_yaml(ruta: Path) -> dict[str, Any]:
    """Lee y parsea un YAML de configuración (vacío si no existe)."""
    if not ruta.exists():
        return {}
    with ruta.open(encoding="utf-8") as fh:
        data = yaml.safe_load(fh) or {}
    if not isinstance(data, dict):
        raise ValueError(f"El archivo de configuración {ruta} debe contener un mapeo YAML (dict).")
    return data


def _from_dict(datos: dict[str, Any], archivo: str | None = None) -> Settings:
    """Convierte un dict normalizado a :class:`Settings` (con defaults)."""
    d = _deep_merge(_DEFAULTS, datos or {})

    ollama_raw = d.get("ollama", {})
    ollama = OllamaSettings(
        url=str(ollama_raw.get("url", _DEFAULTS["ollama"]["url"])),
        timeout_s=float(ollama_raw.get("timeout_s", _DEFAULTS["ollama"]["timeout_s"])),
        max_reintentos=int(ollama_raw.get("max_reintentos", _DEFAULTS["ollama"]["max_reintentos"])),
        backoff_base_s=float(ollama_raw.get("backoff_base_s", _DEFAULTS["ollama"]["backoff_base_s"])),
        backoff_max_s=float(ollama_raw.get("backoff_max_s", _DEFAULTS["ollama"]["backoff_max_s"])),
        reintentar_status=tuple(ollama_raw.get("reintentar_status", [429, 502, 503, 504])),
    )
    cooling_raw = d.get("cooling", {})
    cooling = CoolingSettings(
        enabled=bool(cooling_raw.get("enabled", False)),
        work_window_s=int(cooling_raw.get("work_window_s", 600)),
        cool_down_s=int(cooling_raw.get("cool_down_s", 120)),
    )
    modelos = {}
    for rol, m in (d.get("modelos") or {}).items():
        modelos[rol] = ModeloRol(
            rol=str(m.get("rol", rol)),
            modelo=str(m["modelo"]),
            num_ctx=int(m["num_ctx"]) if m.get("num_ctx") is not None else None,
            temperatura=float(m["temperatura"]) if m.get("temperatura") is not None else None,
        )
    return Settings(
        schema_version=str(d.get("schema_version", "1.0.0")),
        ollama=ollama,
        modelos=modelos,
        cooling=cooling,
        workers=int(d.get("workers", 1)),
        archivo_config=archivo,
    )


def cargar_settings(
    archivo: str | Path | None = None,
    prefijo_env: str = ENV_PREFIX,
    buscar_default: bool = True,
) -> Settings:
    """Carga la configuración aplicando la precedencia *defaults → YAML → env*.

    Argumentos:
        archivo: Ruta explícita de YAML. Si es ``None`` y ``buscar_default`` es
            ``True``, se busca ``voucherflow.yaml`` en el directorio de trabajo.
        prefijo_env: Prefijo de variables de entorno (default ``VOUCHERFLOW``).
        buscar_default: Si no se pasa archivo, buscar el YAML por defecto.
    """
    datos: dict[str, Any] = {}
    archivo_efectivo: str | None = None

    if archivo is not None:
        ruta = Path(archivo)
        if not ruta.exists():
            raise FileNotFoundError(f"No existe el archivo de configuración: {ruta}")
        datos = _leer_yaml(ruta)
        archivo_efectivo = str(ruta)
    elif buscar_default:
        ruta_default = Path(DEFAULT_CONFIG_FILE)
        if ruta_default.exists():
            datos = _leer_yaml(ruta_default)
            archivo_efectivo = str(ruta_default)

    datos = _aplicar_env(datos, prefijo=prefijo_env)
    return _from_dict(datos, archivo=archivo_efectivo)


__all__ = [
    "ENV_PREFIX",
    "DEFAULT_CONFIG_FILE",
    "ModeloRol",
    "OllamaSettings",
    "CoolingSettings",
    "Settings",
    "cargar_settings",
    "cargar_desde_dict",
]


def cargar_desde_dict(datos: dict[str, Any]) -> Settings:
    """Carga desde un dict (útil en tests). Aplica defaults + valores dados."""
    return _from_dict(datos)

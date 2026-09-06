"""Tests de configuración centralizada (F0/T-005) — settings/config.py.

Validan la precedencia *defaults → YAML → env* (E-LIB-3): valores por defecto,
override por YAML, override por variables de entorno ``VOUCHERFLOW_*`` y
acceso a modelos por rol.
"""

from __future__ import annotations

import pytest

from voucherflow.settings.config import (
    ENV_PREFIX,
    ModeloRol,
    Settings,
    cargar_desde_dict,
    cargar_settings,
)


class TestDefaults:
    def test_defaults_ollama_local(self):
        s = cargar_desde_dict({})
        assert s.ollama.url == "http://localhost:11434"
        assert s.ollama.max_reintentos == 3
        assert s.schema_version == "1.0.0"
        assert s.workers == 1

    def test_modelos_por_rol_default(self):
        s = cargar_desde_dict({})
        vlm = s.modelo_para("vlm")
        assert vlm is not None
        assert vlm.modelo == "qwen2.5vl:3b"  # heredado de v1
        assert s.modelo_vlm == "qwen2.5vl:3b"
        assert s.modelo_llm is not None
        assert s.modelo_agente is not None

    def test_helpers_propiedades(self):
        s = cargar_desde_dict({})
        assert s.url_ollama == "http://localhost:11434"


class TestOverrideDict:
    def test_override_yaml_parcial(self):
        s = cargar_desde_dict(
            {
                "ollama": {"url": "http://192.168.1.10:11434", "max_reintentos": 5},
                "workers": 4,
            }
        )
        assert s.ollama.url == "http://192.168.1.10:11434"
        assert s.ollama.max_reintentos == 5
        # El resto conserva defaults (merge profundo)
        assert s.ollama.backoff_base_s == 1.0
        assert s.workers == 4

    def test_modelo_rol_to_ollama_options(self):
        m = ModeloRol(rol="vlm", modelo="qwen2.5vl:3b", num_ctx=4096, temperatura=0.2)
        opts = m.to_ollama_options()
        assert opts == {"num_ctx": 4096, "temperature": 0.2}


class TestOverrideEnv:
    def test_env_anula_url(self, monkeypatch):
        monkeypatch.setenv(f"{ENV_PREFIX}__OLLAMA__URL", "http://ollama:11434")
        s = cargar_desde_dict({})
        s2 = cargar_settings(prefijo_env=ENV_PREFIX, buscar_default=False)
        # cargar_settings aplica env sobre defaults
        assert s2.ollama.url == "http://ollama:11434"

    def test_env_anula_workers_int(self, monkeypatch):
        monkeypatch.setenv(f"{ENV_PREFIX}__WORKERS", "8")
        s = cargar_settings(prefijo_env=ENV_PREFIX, buscar_default=False)
        assert s.workers == 8

    def test_sin_env_usa_default(self, monkeypatch):
        monkeypatch.delenv(f"{ENV_PREFIX}__OLLAMA__URL", raising=False)
        s = cargar_settings(prefijo_env=ENV_PREFIX, buscar_default=False)
        assert s.ollama.url == "http://localhost:11434"


class TestArchivoYaml:
    def test_archivo_inexistente_lanza(self, tmp_path):
        with pytest.raises(FileNotFoundError):
            cargar_settings(archivo=tmp_path / "no_existe.yaml")

    def test_carga_desde_yaml(self, tmp_path):
        yaml_file = tmp_path / "config.yaml"
        yaml_file.write_text(
            "ollama:\n  url: http://localhost:11435\n  max_reintentos: 7\nworkers: 2\n",
            encoding="utf-8",
        )
        s = cargar_settings(archivo=yaml_file)
        assert s.ollama.url == "http://localhost:11435"
        assert s.ollama.max_reintentos == 7
        assert s.workers == 2
        assert s.archivo_config == str(yaml_file)

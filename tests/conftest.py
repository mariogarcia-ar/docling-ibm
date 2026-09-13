"""Fixtures y configuración compartida de la suite de F0.

La suite de F0 **no** requiere servicios reales (Docling/Ollama): los tests de
``OllamaClient`` usan mock de HTTP y los de Docling solo construcción. Los
tests que necesiten servicios reales se marcan ``@pytest.mark.integration`` y
no corren por defecto (ver ``pyproject.toml``).
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

# Garantiza que el paquete ``voucherflow`` sea importable cuando se corre
# pytest desde ``v2/`` con el layout ``src/`` (sin instalación previa).
SRC = Path(__file__).resolve().parents[2] / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

# Raíz del golden set (tests/golden) para tests que lo referencian.
GOLDEN_DIR = Path(__file__).resolve().parent / "golden"
# Raíz de fixtures de datos reales copiados (tests/fixtures). ``files/`` es
# temporal e ignorada por git, por eso los tests usan estas copias estables.
FIXTURES_DIR = Path(__file__).resolve().parent / "fixtures"


@pytest.fixture
def golden_dir() -> Path:
    """Ruta al golden set de F0 (tests/golden)."""
    return GOLDEN_DIR


@pytest.fixture
def casos_csv(golden_dir: Path) -> Path:
    """Ruta al índice de casos del golden set."""
    return golden_dir / "casos.csv"


@pytest.fixture
def fixtures_dir() -> Path:
    """Ruta a los fixtures de datos reales (tests/fixtures)."""
    return FIXTURES_DIR


@pytest.fixture
def fixtures_manifest(fixtures_dir: Path) -> dict:
    """Manifiesto de fixtures (lista de archivos copiados con metadata)."""
    manifest_path = fixtures_dir / "manifest.json"
    if not manifest_path.exists():
        return {"archivos": []}
    with manifest_path.open(encoding="utf-8") as fh:
        return json.load(fh)


@pytest.fixture
def fixtures_archivos(fixtures_dir: Path, fixtures_manifest: dict) -> list[Path]:
    """Lista de Path a todos los archivos de fixture copiados."""
    archivos = []
    for item in fixtures_manifest.get("archivos", []):
        p = fixtures_dir / item["archivo"]
        if p.exists():
            archivos.append(p)
    return archivos

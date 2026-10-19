"""Bootstrap rieng cua `classification/v1` (khong dua vao `ml/conftest.py` va khong them `cls_training` vao pythonpath chung).

Chay:  python -B -m pytest classification/v1/tests -p no:cacheprovider   (tu thu muc `ml/`)
Test du lieu THAT tu skip neu thieu `outputs/datasets` hoac regression predictions; chung chi DOC, khong fit.
"""
from __future__ import annotations

import shutil
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent))
from cls_testenv import DATASETS_ROOT, ML_DIR, V1_DIR, WORKSPACE, needs_real_data  # noqa: E402,F401

if str(V1_DIR) not in sys.path:
    sys.path.insert(0, str(V1_DIR))
for stale in [m for m in sys.modules if m == "cls_training" or m.startswith("cls_training.")]:
    del sys.modules[stale]                    # module cache sach: khong dung ban cu tu phien khac


@pytest.fixture(scope="session")
def cfg():
    from cls_training.config import load_config
    return load_config()


@pytest.fixture(scope="session")
def loaded_h1(cfg):
    from cls_training.config import dataset_pin
    from cls_training.data import load_dataset
    pin = dataset_pin(cfg, 1)
    return load_dataset(DATASETS_ROOT / pin["dataset_version"], pin, 1)


@pytest.fixture(scope="session")
def loaded_h3(cfg):
    from cls_training.config import dataset_pin
    from cls_training.data import load_dataset
    pin = dataset_pin(cfg, 3)
    return load_dataset(DATASETS_ROOT / pin["dataset_version"], pin, 3)


@pytest.fixture()
def dataset_copy(tmp_path, cfg):
    """Ban sao dataset nf1 (≈10 MB) de thu nghiem pha hong; ten thu muc giu nguyen (dataset_version == ten thu muc)."""
    pin = next(p for p in cfg["dataset_pins"] if int(p["horizon"]) == 1)
    src = DATASETS_ROOT / pin["dataset_version"]
    dst = tmp_path / pin["dataset_version"]
    shutil.copytree(src, dst)
    return dst, pin

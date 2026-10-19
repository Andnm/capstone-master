"""Hang so/marker dung chung cua test v1 (module rieng, KHONG phu thuoc ten `conftest` de chay duoc cung `ml/conftest.py`)."""
from __future__ import annotations

from pathlib import Path

import pytest

V1_DIR = Path(__file__).resolve().parents[1]
ML_DIR = V1_DIR.parents[1]
WORKSPACE = ML_DIR.parents[1]
DATASETS_ROOT = WORKSPACE / "outputs" / "datasets"
SNAPSHOT_DIR = ML_DIR / "classification" / "results" / "v1" / "baseline_snapshot"


def real_data_available() -> bool:
    return (DATASETS_ROOT / "ds_20261010_nf1" / "samples.parquet").is_file() and (DATASETS_ROOT / "ds_20261010_nf3" / "samples.parquet").is_file()


needs_real_data = pytest.mark.skipif(not real_data_available(), reason="thieu outputs/datasets nf1/nf3")

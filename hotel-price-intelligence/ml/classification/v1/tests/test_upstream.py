"""Ban copy upstream: phat hien drift + parity hanh vi voi nguon regression/shared dong bang. Test dung nguon chay trong subprocess rieng (khong lam ban sys.path cua phien test)."""
from __future__ import annotations

import importlib.util
import json
import shutil
import subprocess
import sys
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

from cls_testenv import DATASETS_ROOT, ML_DIR, V1_DIR, needs_real_data
from cls_training import upstream_sync

REGRESSION_SRC = ML_DIR / "regression" / "training"
needs_sources = pytest.mark.skipif(not (REGRESSION_SRC / "cv.py").is_file() or not (ML_DIR / "shared" / "dataset_builder").is_dir(), reason="thieu nguon regression/shared")


@needs_sources
def test_upstream_copies_match_current_sources_and_manifest():
    assert upstream_sync.verify() == []


@needs_sources
def test_hand_edited_copy_is_detected(tmp_path, monkeypatch):
    tmp_up = tmp_path / "upstream"
    shutil.copytree(upstream_sync.UPSTREAM_DIR, tmp_up)
    (tmp_up / "cv.py").write_text((tmp_up / "cv.py").read_text(encoding="utf-8") + "\n# sua tay\n", encoding="utf-8")
    monkeypatch.setattr(upstream_sync, "UPSTREAM_DIR", tmp_up)
    problems = upstream_sync.verify()
    assert any("cv.py" in p and "sua tay" in p for p in problems)


@needs_sources
def test_source_drift_is_detected(monkeypatch):
    real = upstream_sync._read_source
    monkeypatch.setattr(upstream_sync, "_read_source", lambda rel: real(rel) + "\n# upstream doi\n" if rel.endswith("training/cv.py") else real(rel))
    problems = upstream_sync.verify()
    assert any("cv.py" in p for p in problems)


@needs_sources
def test_missing_manifest_entry_is_detected(tmp_path, monkeypatch):
    bad = json.loads(upstream_sync.MANIFEST_PATH.read_text(encoding="utf-8"))
    bad["entries"] = bad["entries"][:-1]
    mp = tmp_path / "m.json"
    mp.write_text(json.dumps(bad), encoding="utf-8")
    monkeypatch.setattr(upstream_sync, "MANIFEST_PATH", mp)
    assert any("khong co trong manifest" in p for p in upstream_sync.verify())


def _load_by_path(name: str, path: Path):
    spec = importlib.util.spec_from_file_location(name, path)
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod
    spec.loader.exec_module(mod)
    return mod


@needs_sources
@pytest.mark.parametrize("n_splits,gap,min_train", [(3, 1, 14), (2, 3, 14), (4, 1, 7)])
def test_cv_parity_with_frozen_regression(n_splits, gap, min_train):
    from cls_training.upstream.cv import PurgedExpandingWindowSplit as Copy
    Orig = _load_by_path("_frozen_regression_cv", REGRESSION_SRC / "cv.py").PurgedExpandingWindowSplit
    rng = np.random.default_rng(7)
    dates = pd.Series(pd.to_datetime("2026-08-21") + pd.to_timedelta(rng.integers(0, 40, 3000), unit="D"))
    a, b = list(Copy(n_splits, gap, min_train).split(dates)), list(Orig(n_splits, gap, min_train).split(dates))
    assert len(a) == len(b) == n_splits
    for (ta, va), (tb, vb) in zip(a, b):
        assert np.array_equal(ta, tb) and np.array_equal(va, vb)


@needs_sources
@needs_real_data
def test_dataset_checks_and_primary_frames_parity_with_frozen_regression(cfg):
    """Chay NGUON dong bang (regression.training.provenance/schema/runner.horizon_frames) trong subprocess va so ket qua voi ban copy."""
    from cls_training.config import dataset_pin
    from cls_training.upstream.frames import horizon_frames
    from cls_training.upstream.provenance import verify_dataset
    from cls_training.upstream.schema import read_dictionary, select_features
    pin = dataset_pin(cfg, 1)
    d = DATASETS_ROOT / pin["dataset_version"]
    script = f"""
import sys, json
sys.path[:0] = [r"{ML_DIR / 'regression'}", r"{ML_DIR / 'shared'}"]
import pandas as pd
from training.provenance import verify_dataset
from training.schema import read_dictionary, select_features
from training.runner import horizon_frames
d = r"{d}"
meta = verify_dataset(d)
feats = select_features(read_dictionary(d))
samples = pd.read_parquet(d + "/samples.parquet")
frames, info = horizon_frames(samples, 1, {{"primary_requires_seen_hotel": True}})
print(json.dumps({{"meta": {{k: v for k, v in meta.items() if k != "dataset_dir"}}, "features": feats, "info": info,
                  "ids": {{k: [int(x) for x in v["warehouse_record_id"].tolist()] for k, v in frames.items()}}}}, default=str, sort_keys=True))
"""
    out = subprocess.run([sys.executable, "-I", "-B", "-c", script], capture_output=True, text=True, timeout=600)
    assert out.returncode == 0, out.stderr[-2000:]
    ref = json.loads(out.stdout.strip().splitlines()[-1])
    meta = verify_dataset(d)
    assert json.loads(json.dumps({k: v for k, v in meta.items() if k != "dataset_dir"}, default=str, sort_keys=True)) == ref["meta"]
    assert select_features(read_dictionary(d)) == ref["features"]
    samples = pd.read_parquet(d / "samples.parquet")
    frames, info = horizon_frames(samples, 1, {"primary_requires_seen_hotel": True})
    assert json.loads(json.dumps(info, default=str, sort_keys=True)) == ref["info"]
    for k, v in frames.items():
        assert [int(x) for x in v["warehouse_record_id"].tolist()] == ref["ids"][k]


# ------------------------------------------------------------------ che do packaged (di dong, khong can nguon)

def test_packaged_check_passes_without_touching_any_source(monkeypatch):
    def no_source(rel):
        raise AssertionError(f"packaged khong duoc doc nguon: {rel}")
    monkeypatch.setattr(upstream_sync, "_read_source", no_source)
    assert upstream_sync.verify_packaged() == []
    assert len(upstream_sync.manifest_sha256()) == 64


def test_packaged_check_detects_edit_missing_and_stray_files(tmp_path, monkeypatch):
    tmp_up = tmp_path / "upstream"
    shutil.copytree(upstream_sync.UPSTREAM_DIR, tmp_up, ignore=shutil.ignore_patterns("__pycache__"))
    monkeypatch.setattr(upstream_sync, "UPSTREAM_DIR", tmp_up)
    assert upstream_sync.verify_packaged() == []
    (tmp_up / "cv.py").write_text((tmp_up / "cv.py").read_text(encoding="utf-8") + chr(10) + "# sua tay" + chr(10), encoding="utf-8")
    assert any("cv.py" in p and "bi sua" in p for p in upstream_sync.verify_packaged())
    (tmp_up / "cv.py").unlink()
    assert any("cv.py" in p and "thieu" in p for p in upstream_sync.verify_packaged())
    (tmp_up / "stray.py").write_text("x = 1" + chr(10), encoding="utf-8")
    assert any("ngoai manifest" in p and "stray.py" in p for p in upstream_sync.verify_packaged())


def test_packaged_check_detects_manifest_problems(tmp_path, monkeypatch):
    mp = tmp_path / "m.json"
    monkeypatch.setattr(upstream_sync, "MANIFEST_PATH", mp)
    assert any("khong doc duoc" in p for p in upstream_sync.verify_packaged())
    mp.write_text(json.dumps({"entries": []}), encoding="utf-8")
    assert any("khong co entries" in p for p in upstream_sync.verify_packaged())
    good = json.loads((V1_DIR / "UPSTREAM_MANIFEST.json").read_text(encoding="utf-8"))
    good["entries"].append(dict(good["entries"][0]))
    mp.write_text(json.dumps(good), encoding="utf-8")
    assert any("dest trung" in p for p in upstream_sync.verify_packaged())

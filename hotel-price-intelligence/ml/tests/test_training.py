"""Test pipeline huan luyen (Phase 4) tren dataset GIA LAP dung schema Parquet cua builder - khong can MySQL.

Chay: `python -m pytest tests/test_training.py -q` bang moi truong co scikit-learn (vd anaconda base); thieu sklearn -> skip ca file.
"""
from __future__ import annotations

import json
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

pytest.importorskip("sklearn")

from dataset_builder.dictionary import dictionary_rows  # noqa: E402
from training import config as tconfig  # noqa: E402
from training.cv import CVError, PurgedExpandingWindowSplit  # noqa: E402
from training.encoder import TreeEncoder  # noqa: E402
from training.metrics import regression_metrics  # noqa: E402
from training.models import xgboost_available  # noqa: E402
from training.runner import horizon_frames, run_horizon  # noqa: E402
from training.schema import SchemaError, select_features  # noqa: E402
from training.target import to_price, to_target  # noqa: E402
from training.tuning import refine_grid  # noqa: E402

CITIES = ["Hà Nội", "Đà Lạt", "Phú Quốc"]
FEATURE_COLS = ["current_price", "day_of_week", "is_weekend", "lead_time", "lead_time_bucket", "is_last_minute", "city",
                "max_occupancy", "breakfast_included", "free_cancellation", "price_lag_7", "price_velocity", "inference_mode"]
ID_COLS = ["dataset_version", "hotel_id", "checkin_date", "canonical_series_id", "vn_observation_date", "prediction_time", "split",
           "hotel_seen_in_train", "warehouse_record_id"]
LABEL_COLS = [f"{p}_h7" for p in ("has_label", "label_usable", "y_price", "y_delta", "y_pct_change", "y_direction")]


def make_dataset(root: Path, *, n_days: int = 110, n_series: int = 24, seed: int = 7, version: str = "ds_test") -> Path:
    """Moi mau doc lap: y = current * exp(0.3 * is_weekend + nhieu nho) => co tin hieu ro ma persistence khong bat duoc."""
    rng = np.random.default_rng(seed)
    start = pd.Timestamp("2026-08-18")
    rows = []
    for s in range(n_series):
        base = float(np.exp(rng.normal(14.0, 0.4)))
        city = CITIES[s % 3]
        for t in range(n_days):
            obs = start + pd.Timedelta(days=t)
            lead = int(rng.integers(1, 30))
            checkin = obs + pd.Timedelta(days=lead)
            weekend = int(checkin.dayofweek in (4, 5))
            cur = base * float(np.exp(rng.normal(0, 0.05)))
            y = cur * float(np.exp(0.3 * weekend + rng.normal(0, 0.03)))
            if t <= 59:
                split = "train"
            elif t <= 67:
                split = None
            elif t <= 82:
                split = "validation"
            elif t <= 90:
                split = None
            else:
                split = "test"
            # Nhan dung duoc chi khi muc tieu o t+7 cung split voi mau.
            target_split = None if t + 7 > 109 else ("train" if t + 7 <= 59 else "validation" if 68 <= t + 7 <= 82 else "test" if t + 7 >= 91 else None)
            usable = split is not None and target_split == split
            rows.append({
                "dataset_version": version, "hotel_id": f"hotel-{s}", "checkin_date": checkin.date(), "canonical_series_id": f"series-{s}-{lead}",
                "vn_observation_date": obs, "prediction_time": obs, "split": split, "hotel_seen_in_train": True, "warehouse_record_id": s * 1000 + t,
                "current_price": cur, "day_of_week": checkin.dayofweek, "is_weekend": bool(weekend), "lead_time": lead,
                "lead_time_bucket": "lt3" if lead < 3 else "3-7" if lead < 7 else "7-14" if lead < 14 else "14-30",
                "is_last_minute": lead <= 3, "city": city, "max_occupancy": 2.0, "breakfast_included": bool(s % 2),
                "free_cancellation": True, "price_lag_7": np.nan if t < 7 else cur * 0.99, "price_velocity": np.nan if t < 1 else 0.001,
                "inference_mode": "cold_start" if t < 3 else "history_enriched",
                "has_label_h7": usable or target_split is not None, "label_usable_h7": usable,
                "y_price_h7": y if target_split is not None else np.nan, "y_delta_h7": y - cur, "y_pct_change_h7": (y - cur) / cur,
                "y_direction_h7": "up" if (y - cur) / cur > 0.02 else "down" if (y - cur) / cur < -0.02 else "stable",
            })
    frame = pd.DataFrame(rows)
    out = root / version
    out.mkdir(parents=True, exist_ok=True)
    frame.to_parquet(out / "samples.parquet", index=False)
    pd.DataFrame(dictionary_rows(ID_COLS + FEATURE_COLS + LABEL_COLS)).to_csv(out / "data_dictionary.csv", index=False)
    (out / "output_checksums.json").write_text(json.dumps({"samples.parquet": {"content_sha256": "c" * 64, "file_sha256": "f" * 64}}), encoding="utf-8")
    (out / "sufficiency_report.json").write_text(json.dumps({"horizons": {"h7": {"status": "exploratory", "failed_gates": ["synthetic"]}}}), encoding="utf-8")
    return out


@pytest.fixture()
def cfg():
    c = tconfig.load_config()
    c["min_rows"] = {"train": 200, "validation": 50, "test": 50}
    c["cv"] = {"n_splits": 3, "min_train_days": 15}
    c["models"]["rf"]["random_search"] = {"n_iter": 2, "space": {"n_estimators": [20, 40], "max_depth": [4, 8, None], "min_samples_leaf": [1, 5]}}
    c["models"]["rf"]["fixed"] = {"n_jobs": 1}
    c["models"]["xgb"]["random_search"] = {"n_iter": 2, "space": {"n_estimators": [20, 40], "max_depth": [3, 4], "learning_rate": [0.1, 0.2]}}
    c["models"]["xgb"]["fixed"] = {"tree_method": "hist", "n_jobs": 1}
    return c


def test_config_loads_and_hashes():
    c = tconfig.load_config()
    assert c["target_transform"] == "log_ratio" and len(c["config_sha256"]) == 64
    assert tconfig.load_config()["config_sha256"] == c["config_sha256"]


def test_target_roundtrip():
    y, cur = np.array([120.0, 80.0]), np.array([100.0, 100.0])
    for kind in ("log_ratio", "log_price", "price"):
        assert np.allclose(to_price(to_target(y, cur, kind), cur, kind), y)


def test_metrics_known_values():
    m = regression_metrics([100, 100, 100, 100], [100, 119, 121, 50], [100, 100, 100, 100], tol=0.20, stable=0.02)
    assert m["accuracy_at_tol"] == 0.5 and m["mae"] == 22.5
    assert m["directional_accuracy"] == 0.25  # that: stable; du bao: stable, up, up, down


def test_cv_purge_has_no_leakage_and_blocks_are_ordered():
    dates = pd.Series(pd.date_range("2026-09-01", periods=60).repeat(3))
    h = 7
    folds = list(PurgedExpandingWindowSplit(4, gap_days=h, min_train_days=14).split(dates))
    assert len(folds) == 4
    d = pd.to_datetime(dates).reset_index(drop=True)
    last_val_end = None
    prev_train = 0
    for tr, va in folds:
        assert d.iloc[tr].max() + pd.Timedelta(days=h) < d.iloc[va].min()  # nhan cua moi mau train nam truoc khoi validation
        assert set(tr).isdisjoint(va)
        if last_val_end is not None:
            assert d.iloc[va].min() > last_val_end
        assert len(tr) > prev_train
        last_val_end, prev_train = d.iloc[va].max(), len(tr)


def test_cv_too_few_days_raises():
    with pytest.raises(CVError, match="qua it ngay"):
        list(PurgedExpandingWindowSplit(4, gap_days=14, min_train_days=14).split(pd.Series(pd.date_range("2026-09-01", periods=30))))


def test_tree_encoder_unseen_category_and_bool_missing():
    frame = pd.DataFrame({"city": ["A", "B", None], "flag": pd.array([True, False, None], dtype="boolean"), "x": [1.0, np.nan, 3.0]})
    enc = TreeEncoder(["city", "flag", "x"])
    enc.fit(frame.iloc[:2])  # city thuoc CATEGORICAL_COLUMNS; danh muc hoc tu train chi gom A, B
    out = enc.transform(pd.DataFrame({"city": ["A", "Z"], "flag": pd.array([True, None], dtype="boolean"), "x": [2.0, 4.0]}))
    assert out.loc[0, "city"] == 0.0 and np.isnan(out.loc[1, "city"])   # nhan chua thay -> NaN
    assert out.loc[0, "flag"] == 1.0 and np.isnan(out.loc[1, "flag"])


def test_select_features_blocks_forbidden_and_group():
    d = pd.DataFrame({"column": ["hotel_id", "current_price", "source_code", "y_price_h7"], "group": ["identifier", "current", "static", "label"]})
    with pytest.raises(SchemaError, match="bi cam"):
        select_features(d)
    ok = pd.DataFrame({"column": ["hotel_id", "current_price", "rooms_left", "y_price_h7"], "group": ["identifier", "current", "listing_signals", "label"]})
    assert select_features(ok) == ["current_price", "rooms_left"]
    assert select_features(ok, exclude_groups=["listing_signals"]) == ["current_price"]


def test_refine_grid_uses_adjacent_values():
    space = {"max_depth": [6, 10, 14, None], "n": [1, 2]}
    assert refine_grid({"max_depth": 10, "n": 1}, space) == {"max_depth": [6, 10, 14], "n": [1, 2]}
    assert refine_grid({"max_depth": None, "n": 2}, space) == {"max_depth": [14, None], "n": [1, 2]}


def test_horizon_frames_filters_usable_and_unseen_hotels(tmp_path, cfg):
    out = make_dataset(tmp_path, n_days=110, n_series=6)
    samples = pd.read_parquet(out / "samples.parquet")
    samples.loc[samples["hotel_id"] == "hotel-0", "hotel_seen_in_train"] = False
    frames = horizon_frames(samples, 7, cfg)
    assert set(frames["train"]["split"]) == {"train"} and (frames["train"]["y_true"] > 0).all()
    assert "hotel-0" not in set(frames["validation"]["hotel_id"]) and "hotel-0" in set(frames["train"]["hotel_id"])
    assert frames["test"]["label_usable_h7"].all()


def test_run_horizon_end_to_end_selects_by_validation_and_tests_once(tmp_path, cfg):
    ds = make_dataset(tmp_path, n_days=110, n_series=24)
    models = ["ridge", "rf"] + (["xgb"] if xgboost_available() else [])
    rep = run_horizon(ds, 7, cfg, tmp_path / "out", models)
    assert rep["status"] == "ok" and rep["evaluation_status"] == "exploratory" and "warning" in rep
    selected = rep["selected_model"]
    assert selected in {"ridge", "rf", "xgb"}
    for name, res in rep["results"].items():
        has_test = "test" in res
        assert has_test == (name in ("persistence", "ratio_median_by_city_leadtime", selected)), name  # test chi cho mo hinh da chon + baseline
    assert rep["results"]["rf"]["tuning"]["n_folds"] == 3 and rep["results"]["rf"]["tuning"]["scoring"] == cfg["scoring"]
    # Tin hieu ro (tuan cuoi +35%): mo hinh phai hon persistence tren Accuracy@20%
    assert rep["results"][selected]["test"]["accuracy_at_tol"] > rep["results"]["persistence"]["test"]["accuracy_at_tol"] + 0.1
    assert rep["beats_persistence"]["test_accuracy"] is True
    out = tmp_path / "out"
    assert (out / "h7_report.json").exists() and (out / f"h7_model_{selected}.joblib").exists()
    assert (out / f"h7_predictions_{selected}.parquet").exists() and (out / "h7_test_metrics_by_city.csv").exists()
    pred = pd.read_parquet(out / f"h7_predictions_{selected}.parquet")
    assert set(pred["split"]) == {"validation", "test"}
    assert rep["dataset"]["samples_content_sha256"] == "c" * 64
    if not xgboost_available():
        rep2 = run_horizon(ds, 7, cfg, tmp_path / "out2", ["xgb"])
        assert rep2["results"]["xgb"]["status"] == "skipped" and rep2["status"] == "baselines_only"


def test_run_horizon_is_deterministic(tmp_path, cfg):
    ds = make_dataset(tmp_path, n_days=110, n_series=24)
    a = run_horizon(ds, 7, cfg, tmp_path / "a", ["rf"])
    b = run_horizon(ds, 7, cfg, tmp_path / "b", ["rf"])
    assert a["results"]["rf"]["validation"] == b["results"]["rf"]["validation"]
    assert a["results"]["rf"]["params"] == b["results"]["rf"]["params"]


def test_run_horizon_skips_when_too_few_rows(tmp_path, cfg):
    ds = make_dataset(tmp_path, n_days=110, n_series=2)
    rep = run_horizon(ds, 7, cfg, tmp_path / "thin", ["ridge", "rf"])
    assert rep["status"] == "skipped" and "khong du mau toi thieu" in rep["reason"]
    assert (tmp_path / "thin" / "h7_report.json").exists()


def test_cv_infeasible_skips_trees_but_keeps_baselines(tmp_path, cfg):
    cfg["cv"] = {"n_splits": 3, "min_train_days": 500}
    ds = make_dataset(tmp_path, n_days=110, n_series=24)
    rep = run_horizon(ds, 7, cfg, tmp_path / "cv", ["rf"])
    assert rep["results"]["rf"]["status"] == "skipped" and "cv khong kha thi" in rep["results"]["rf"]["reason"]
    assert rep["status"] == "baselines_only" and "persistence" in rep["results"]


def test_apply_overrides_device_changes_hash():
    base = tconfig.load_config()
    over = tconfig.apply_overrides(tconfig.load_config(), device="cuda")
    assert over["models"]["xgb"]["fixed"]["device"] == "cuda" and over["config_sha256"] != base["config_sha256"]
    assert tconfig.apply_overrides(tconfig.load_config(), device=None)["config_sha256"] == base["config_sha256"]
    with pytest.raises(tconfig.ConfigError):
        tconfig.apply_overrides(tconfig.load_config(), device="tpu")


def _load_package_module():
    import importlib.util

    path = Path(__file__).resolve().parents[1] / "scripts" / "package_for_colab.py"
    spec = importlib.util.spec_from_file_location("package_for_colab", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_colab_package_is_self_sufficient_and_runs_cli(tmp_path):
    """Goi Colab chi gom code toi thieu; giai nen o noi khong co repo van import + chay CLI duoc (bat file thieu trong goi)."""
    import subprocess
    import sys
    import zipfile

    pkg = _load_package_module()
    ds = make_dataset(tmp_path / "src", n_days=110, n_series=24, version="ds_pkg")
    manifest = pkg.build_package(tmp_path / "out", ds, stamp="t1")
    code_zip, data_zip = tmp_path / "out" / "ml_train_pkg_t1.zip", tmp_path / "out" / "dataset_ds_pkg.zip"
    assert set(manifest["archives"]) == {code_zip.name, data_zip.name} and all(len(v["sha256"]) == 64 for v in manifest["archives"].values())
    names = zipfile.ZipFile(code_zip).namelist()
    for needed in ("ml/training/runner.py", "ml/training/schema.py", "ml/scripts/train_models.py", "ml/configs/train_v1.yaml",
                   "ml/dataset_builder/feature_spec.py", "ml/dataset_builder/dictionary.py", "ml/requirements-train.txt"):
        assert needed in names, needed
    assert not [n for n in names if "tests" in n or "__pycache__" in n or n.endswith(("steps.py", "export.py", "db.py", ".env"))]
    run_dir = tmp_path / "colab"
    zipfile.ZipFile(code_zip).extractall(run_dir)
    zipfile.ZipFile(data_zip).extractall(run_dir)
    assert (run_dir / "ds_pkg" / "samples.parquet").exists() and (run_dir / "ds_pkg" / "data_dictionary.csv").exists()
    done = subprocess.run(
        [sys.executable, str(run_dir / "ml" / "scripts" / "train_models.py"), "--dataset-dir", str(run_dir / "ds_pkg"), "--horizons", "7",
         "--models", "ridge", "--output-root", str(run_dir / "models"), "--run-id", "r1"],
        capture_output=True, text=True, cwd=str(run_dir), timeout=300)
    assert done.returncode == 0, done.stderr[-2000:]
    assert "h7: status=ok" in done.stdout and "selected=ridge" in done.stdout
    report = json.loads((run_dir / "models" / "ds_pkg" / "r1" / "h7_report.json").read_text(encoding="utf-8"))
    assert report["library_versions"]["sklearn"] and report["xgb_device"] == "cpu"

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
from dataset_builder.feature_spec import CATEGORY_DOMAINS  # noqa: E402
from training import config as tconfig  # noqa: E402
from training.cv import CVError, PurgedExpandingWindowSplit  # noqa: E402
from training.encoder import TreeEncoder  # noqa: E402
from training.metrics import regression_metrics  # noqa: E402
from training.models import xgboost_available  # noqa: E402
from training.provenance import DatasetVerificationError, ProvenanceError, code_provenance, require_known_provenance  # noqa: E402
from training.runner import horizon_frames, run_horizon  # noqa: E402
from training.schema import SchemaError, select_features  # noqa: E402
from training.target import to_price, to_target  # noqa: E402
from training.tuning import refine_grid  # noqa: E402

CITIES = ["Hà Nội", "Đà Lạt", "Phú Quốc"]
FEATURE_COLS = ["current_price", "day_of_week", "is_weekend", "lead_time", "lead_time_bucket", "is_last_minute", "city",
                "max_occupancy", "breakfast_included", "free_cancellation", "price_lag_7", "price_velocity", "inference_mode"]
ID_COLS = ["dataset_version", "hotel_id", "checkin_date", "canonical_series_id", "vn_observation_date", "prediction_time", "split",
           "hotel_seen_in_train_h7", "warehouse_record_id"]
LABEL_COLS = [f"{p}_h7" for p in ("has_label", "label_usable", "y_price", "y_delta", "y_pct_change", "y_direction")]


SYNTHETIC_START = pd.Timestamp("2026-08-18")
SYNTHETIC_SPLIT_DAYS = {"train_start": 0, "train_end": 59, "validation_start": 68, "validation_end": 82, "test_start": 91, "test_end": 109}   # khop `split` trong make_dataset (gap 8 ngay)


def make_dataset(root: Path, *, n_days: int = 110, n_series: int = 24, seed: int = 7, version: str = "ds_test",
                 evaluation_horizons: tuple[int, ...] = (7,), purpose: str = "rehearsal", status: str = "exploratory") -> Path:
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
                "vn_observation_date": obs, "prediction_time": obs, "split": split, "hotel_seen_in_train_h7": True, "warehouse_record_id": s * 1000 + t,
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
    (out / "coverage_report.json").write_text(json.dumps({"rows": len(frame)}), encoding="utf-8")
    write_calendar_evidence(out)
    write_contract(out, version=version, evaluation_horizons=evaluation_horizons, purpose=purpose, status=status)
    write_checksums(out, rows=len(frame))
    return out


def write_calendar_evidence(out: Path) -> None:
    """Hai bang chung lich cua builder >= 1.3.0: snapshot bytes + calendar_input.json khai bao dung hash cua snapshot."""
    import hashlib

    (out / "inputs").mkdir(exist_ok=True)
    raw = (b"holiday_date,event_code,name,event_type,scope,city,is_tet,status,source_url\n"
           b"2026-09-02,national_day,QK,public_holiday,national,,0,confirmed,u\n")
    (out / "inputs" / "vn_holidays.csv").write_bytes(raw)
    (out / "calendar_input.json").write_text(json.dumps({"vn_holidays_csv_sha256": hashlib.sha256(raw).hexdigest(), "name": "vn_holidays.csv"}), encoding="utf-8")


def write_sufficiency(out: Path, evaluation_horizons, status: str) -> None:
    """sufficiency_report.json nhu builder >= 1.4.0: du bon horizon, horizon khong duoc danh gia -> not_evaluated."""
    horizons = {f"h{k}": ({"status": status, "failed_gates": [] if status == "primary_eligible" else ["synthetic"]} if k in evaluation_horizons else {"status": "not_evaluated"})
                for k in (1, 3, 7, 14)}
    (out / "sufficiency_report.json").write_text(json.dumps({"horizons": horizons}), encoding="utf-8")


def write_contract(out: Path, *, version: str, evaluation_horizons=(7,), purge_gap_days: int | None = None, purpose: str = "rehearsal", status: str = "exploratory", **overrides) -> None:
    """dataset_contract.json DAY DU nhu builder >= 1.4.0 (hash ma/config, hash lich khop snapshot, split_plan co ngay that, sufficiency khop bao cao).
    Ghi lai sufficiency_report.json cho nhat quan, TRU KHI test truyen `sufficiency_status` (cho phep tao lech co chu y)."""
    import hashlib

    from dataset_builder import BUILDER_VERSION

    purge = max(evaluation_horizons) if purge_gap_days is None else purge_gap_days
    snapshot = out / "inputs" / "vn_holidays.csv"
    calendar_sha = hashlib.sha256(snapshot.read_bytes()).hexdigest() if snapshot.exists() else "0" * 64
    plan = {k: (SYNTHETIC_START + pd.Timedelta(days=d)).date().isoformat() for k, d in SYNTHETIC_SPLIT_DAYS.items()}
    plan.update(purge_gap_days=purge, policy_path="fallback_ratio", feasible_horizon=None)
    contract = {"contract_version": 1, "dataset_version": version, "purpose": purpose, "evaluation_horizons": list(evaluation_horizons),
                "computed_label_horizons": [1, 3, 7, 14], "purge_gap_days": purge, "split_plan": plan,
                "sufficiency_status": {f"h{k}": (status if k in evaluation_horizons else "not_evaluated") for k in (1, 3, 7, 14)},
                "builder_version": BUILDER_VERSION, "builder_code_sha256": "a" * 64, "build_config_sha256": "b" * 64, "calendar_sha256": calendar_sha}
    if "sufficiency_status" not in overrides:
        write_sufficiency(out, evaluation_horizons, status)
    contract.update(overrides)
    (out / "dataset_contract.json").write_text(json.dumps(contract), encoding="utf-8")


def write_checksums(out: Path, *, rows: int, content_sha256: str = "c" * 64) -> None:
    """output_checksums.json THAT: file_sha256 tinh lai tu chinh cac file (nhu builder) + content_sha256/rows cho samples.parquet."""
    from training.provenance import file_sha256

    entries = {name: {"file_sha256": file_sha256(out / name)} for name in
               ("samples.parquet", "data_dictionary.csv", "coverage_report.json", "sufficiency_report.json", "calendar_input.json", "inputs/vn_holidays.csv",
                "dataset_contract.json")}
    entries["samples.parquet"].update(content_sha256=content_sha256, rows=rows)
    (out / "output_checksums.json").write_text(json.dumps(entries), encoding="utf-8")


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


def test_tree_encoder_fixed_domain_maps_unknown_to_nan_and_bool_missing():
    enc = TreeEncoder(["city", "flag", "x"])        # khong can fit: mapping lay tu domain protocol
    out = enc.transform(pd.DataFrame({"city": ["Hà Nội", "Z", None], "flag": pd.array([True, None, False], dtype="boolean"), "x": [2.0, 4.0, np.nan]}))
    assert out.loc[0, "city"] == float(CATEGORY_DOMAINS["city"].index("Hà Nội")) and np.isnan(out.loc[1, "city"]) and np.isnan(out.loc[2, "city"])
    assert out.loc[0, "flag"] == 1.0 and np.isnan(out.loc[1, "flag"]) and out.loc[2, "flag"] == 0.0 and np.isnan(out.loc[2, "x"])
    assert enc.unknown_counts(pd.DataFrame({"city": ["Hà Nội", "Z", None, "Y"]})) == {"city": 2}


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
    # hotel-0 khong co mau train DUNG DUOC o h7 (co mau train nhung label_usable=False) => khong thuoc tap primary cua horizon nay
    samples.loc[(samples["hotel_id"] == "hotel-0") & (samples["split"] == "train"), "label_usable_h7"] = False
    samples.loc[samples["hotel_id"] == "hotel-0", "hotel_seen_in_train_h7"] = False
    frames, info = horizon_frames(samples, 7, cfg)
    assert set(frames["train"]["split"]) == {"train"} and (frames["train"]["y_true"] > 0).all()
    assert "hotel-0" not in set(frames["validation"]["hotel_id"]) and "hotel-0" not in set(frames["train"]["hotel_id"])
    assert frames["test"]["label_usable_h7"].all()
    # ca hai mau so duoc bao cao: all-hotel vs primary, va so mau/hotel bi loai
    assert info["rows_all_hotels"]["validation"] > info["rows_primary"]["validation"] > 0
    assert info["excluded_unseen_hotel_rows"]["validation"] == info["rows_all_hotels"]["validation"] - info["rows_primary"]["validation"]
    assert info["excluded_unseen_hotels"] == {"validation": 1, "test": 1} and info["train_hotels"] == 5


def test_horizon_frames_rejects_a_seen_flag_that_disagrees_with_the_definition(tmp_path, cfg):
    out = make_dataset(tmp_path, n_days=110, n_series=6)
    samples = pd.read_parquet(out / "samples.parquet")
    samples.loc[samples["hotel_id"] == "hotel-0", "hotel_seen_in_train_h7"] = False      # hotel-0 THUC SU co mau train dung duoc
    with pytest.raises(DatasetVerificationError, match="khong khop dinh nghia"):
        horizon_frames(samples, 7, cfg)


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


# ================================================================= GPT review vong 1: TR-M1..M4, TR-m1, TR-m2
class Spy:
    """Boc estimator da fit: dem so lan predict va so dong moi lan (de chung minh test KHONG bi cham truoc khi chon)."""
    def __init__(self, inner):
        self.inner, self.calls = inner, []

    def fit(self, X, y):
        self.inner.fit(X, y)
        return self

    def predict(self, X):
        self.calls.append(len(X))
        return self.inner.predict(X)


def test_test_split_is_predicted_exactly_once_and_only_for_the_selected_model(tmp_path, cfg, monkeypatch):
    """TR-M1: truoc day predict(test) duoc goi cho MOI mo hinh roi moi chon. Nay: ung vien chi predict validation (1 lan), mo hinh da chon them dung 1 lan cho test."""
    import training.runner as runner

    spies: dict[str, Spy] = {}
    real_ridge, real_tree = runner.make_ridge, runner.make_tree_model

    def ridge_spy(alpha, features):
        spies["ridge"] = Spy(real_ridge(alpha, features))
        return spies["ridge"]

    def tree_spy(name, c, seed, params=None):
        spies[name] = Spy(real_tree(name, c, seed, params))
        return spies[name]

    monkeypatch.setattr(runner, "make_ridge", ridge_spy)
    monkeypatch.setattr(runner, "make_tree_model", tree_spy)     # chi anh huong model cuoi: tuning.py import make_tree_model rieng
    ds = make_dataset(tmp_path, n_days=110, n_series=24)
    rep = run_horizon(ds, 7, cfg, tmp_path / "out", ["ridge", "rf"])
    selected = rep["selected_model"]
    other = ({"ridge", "rf"} - {selected}).pop()
    frames, _ = horizon_frames(pd.read_parquet(ds / "samples.parquet"), 7, cfg)
    n_val, n_test = len(frames["validation"]), len(frames["test"])
    assert spies[selected].calls == [n_val, n_test]              # validation roi dung mot lan test, theo thu tu do
    assert spies[other].calls == [n_val]                         # mo hinh khong duoc chon: KHONG BAO GIO chay tren test
    assert "test" in rep["results"][selected] and "test" not in rep["results"][other]


def test_dataset_files_are_rehashed_and_tampering_is_rejected(tmp_path, cfg):
    """TR-M2: sua samples.parquet sau khi build (hash khai bao con nguyen) => training TU CHOI."""
    ds = make_dataset(tmp_path, n_days=110, n_series=24)
    with open(ds / "samples.parquet", "ab") as handle:
        handle.write(b"tamper")
    with pytest.raises(DatasetVerificationError, match="samples.parquet: file_sha256 that"):
        run_horizon(ds, 7, cfg, tmp_path / "out", ["ridge"])
    assert not (tmp_path / "out" / "h7_report.json").exists()


def _rewrite_checksums(ds: Path, *, drop: str) -> None:
    data = json.loads((ds / "output_checksums.json").read_text(encoding="utf-8"))
    data["samples.parquet"].pop(drop)
    (ds / "output_checksums.json").write_text(json.dumps(data), encoding="utf-8")


@pytest.mark.parametrize("mutation, message", [
    (lambda ds: (ds / "output_checksums.json").unlink(), "thieu"),
    (lambda ds: (ds / "data_dictionary.csv").write_text("column,group\nx,static\n", encoding="utf-8"), "data_dictionary.csv: file_sha256 that"),
    (lambda ds: (ds / "coverage_report.json").unlink(), "coverage_report.json: file khong ton tai"),
    (lambda ds: _rewrite_checksums(ds, drop="content_sha256"), "content_sha256"),
    (lambda ds: _rewrite_checksums(ds, drop="rows"), "thieu rows"),
])
def test_dataset_verification_requires_complete_published_metadata(tmp_path, cfg, mutation, message):
    ds = make_dataset(tmp_path, n_days=110, n_series=24)
    mutation(ds)
    with pytest.raises(DatasetVerificationError, match=message):
        run_horizon(ds, 7, cfg, tmp_path / "out", ["ridge"])


def test_dataset_version_inside_rows_must_match_directory_and_row_count(tmp_path, cfg):
    ds = make_dataset(tmp_path, n_days=110, n_series=24, version="ds_test")
    renamed = tmp_path / "ds_other"
    ds.rename(renamed)
    with pytest.raises(DatasetVerificationError, match="dataset_contract.json.dataset_version='ds_test' khac ten thu muc"):    # hop dong bat truoc
        run_horizon(renamed, 7, cfg, tmp_path / "out", ["ridge"])
    write_contract(renamed, version="ds_other")                                       # hop dong dung ten moi: cot dataset_version trong Parquet van la lop phong thu thu hai
    write_checksums(renamed, rows=len(pd.read_parquet(renamed / "samples.parquet")))
    with pytest.raises(DatasetVerificationError, match="dataset_version trong du lieu"):
        run_horizon(renamed, 7, cfg, tmp_path / "out", ["ridge"])
    ds2 = make_dataset(tmp_path / "b", n_days=110, n_series=24, version="ds_rows")
    write_checksums(ds2, rows=999)                                                    # khai bao so dong sai
    with pytest.raises(DatasetVerificationError, match="so dong"):
        run_horizon(ds2, 7, cfg, tmp_path / "out2", ["ridge"])


def test_report_carries_primary_and_all_hotel_denominators_and_provenance(tmp_path, cfg):
    """TR-M3 + TR-M4 + TR-m2: bao cao ghi ca hai mau so, hash dataset da xac minh, provenance code va bang moi truong."""
    import hashlib

    ds = make_dataset(tmp_path, n_days=110, n_series=24)
    out = tmp_path / "out"
    rep = run_horizon(ds, 7, cfg, out, ["ridge"])
    assert rep["rows"] == rep["primary_selection"]["rows_primary"] and rep["rows_all_hotels"] == rep["primary_selection"]["rows_all_hotels"]
    assert rep["dataset"]["verified_file_sha256"]["samples.parquet"] == rep["dataset"]["samples_file_sha256"]
    assert len(rep["dataset"]["samples_file_sha256"]) == 64
    assert rep["provenance"]["source"] in ("git", "unknown", "code_manifest")
    assert len(rep["environment"]["environment_sha256"]) == 64 and rep["environment"]["packages"] > 5
    env_file = out / "environment_resolved.txt"
    assert hashlib.sha256(env_file.read_bytes()).hexdigest() == rep["environment"]["environment_sha256"]
    assert any(line.lower().startswith("scikit-learn==") for line in env_file.read_text(encoding="utf-8").splitlines())


def test_code_provenance_verifies_code_manifest_and_official_requires_known_provenance(tmp_path):
    import zipfile

    pkg = _load_package_module()
    ds = make_dataset(tmp_path / "src", n_days=110, n_series=24, version="ds_prov")
    pkg.build_package(tmp_path / "out", ds, stamp="t2")
    root = tmp_path / "colab"
    zipfile.ZipFile(tmp_path / "out" / "ml_train_pkg_t2.zip").extractall(root)
    prov = code_provenance(root / "ml")
    assert prov["source"] == "code_manifest" and len(prov["code_sha256"]) == 64 and prov["verified_files"] >= 12
    require_known_provenance(prov, official=True)
    (root / "ml" / "training" / "metrics.py").write_text("# bi sua\n", encoding="utf-8")      # sua 1 file sau khi dong goi
    with pytest.raises(ProvenanceError, match="KHONG khop"):
        code_provenance(root / "ml")
    with pytest.raises(ProvenanceError, match="official"):
        require_known_provenance({"source": "unknown", "error": "no git"}, official=True)
    with pytest.raises(ProvenanceError, match="official"):
        require_known_provenance({"source": "git", "head": "abc", "ml_dirty": True}, official=True)
    require_known_provenance({"source": "unknown"}, official=False)
    require_known_provenance({"source": "git", "head": "abc", "ml_dirty": False}, official=True)


def test_cli_refuses_to_overwrite_an_existing_run_and_writes_run_manifest(tmp_path):
    import subprocess
    import sys

    ds = make_dataset(tmp_path / "src", n_days=110, n_series=24, version="ds_cli")
    script = Path(__file__).resolve().parents[1] / "scripts" / "train_models.py"
    cmd = [sys.executable, str(script), "--dataset-dir", str(ds), "--horizons", "7", "--models", "ridge", "--output-root", str(tmp_path / "models"),
           "--run-id", "r1"]
    first = subprocess.run(cmd, capture_output=True, text=True, timeout=300)
    assert first.returncode == 0, first.stderr[-1500:]
    run_dir = tmp_path / "models" / "ds_cli" / "r1"
    manifest = json.loads((run_dir / "run_manifest.json").read_text(encoding="utf-8"))
    assert manifest["official"] is False and manifest["horizons"][0]["horizon"] == 7 and (run_dir / "environment_resolved.txt").exists()
    before = (run_dir / "h7_report.json").read_bytes()
    again = subprocess.run(cmd, capture_output=True, text=True, timeout=300)
    assert again.returncode == 2 and "da ton tai" in again.stderr
    assert (run_dir / "h7_report.json").read_bytes() == before                           # artifact cu khong bi ghi de


def test_cli_rejects_tampered_dataset_before_any_output(tmp_path):
    import subprocess
    import sys

    ds = make_dataset(tmp_path / "src", n_days=110, n_series=24, version="ds_bad")
    with open(ds / "samples.parquet", "ab") as handle:
        handle.write(b"x")
    script = Path(__file__).resolve().parents[1] / "scripts" / "train_models.py"
    done = subprocess.run([sys.executable, str(script), "--dataset-dir", str(ds), "--horizons", "7", "--models", "ridge",
                           "--output-root", str(tmp_path / "models"), "--run-id", "r1"], capture_output=True, text=True, timeout=300)
    assert done.returncode == 3 and "KHONG qua xac minh" in done.stderr
    assert not list((tmp_path / "models" / "ds_bad" / "r1").glob("h*_report.json"))

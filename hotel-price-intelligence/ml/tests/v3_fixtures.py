"""Fixture cho test train-v3: frames tong hop (co tin hieu hoi quy ve trung binh + nhieu hang khong doi + spike), cau hinh nho, xgboost GIA, dataset tong hop cho CLI."""
from __future__ import annotations

import copy
import importlib.machinery
import json
import sys
import types
import warnings
from pathlib import Path

import numpy as np
import pandas as pd

from training.v3_contract import load_config_v3

CITIES = ["Hồ Chí Minh", "Hà Nội", "Phú Quốc"]
FEATURES = ["current_price", "price_rolling_mean_14", "price_max_trailing_14", "price_min_trailing_14", "price_rolling_mean_7", "price_rolling_std_7",
            "price_velocity", "lead_time", "lead_time_bucket", "city", "inference_mode"]
BASE = pd.Timestamp("2026-08-18")


def synthetic_frames(*, n_hotels: int = 30, train_days: int = 34, val_days: int = 6, test_days: int = 5, seed: int = 1, signal: bool = True,
                     all_unchanged: bool = False, horizon: int = 1) -> dict[str, pd.DataFrame]:
    rng = np.random.default_rng(seed)
    gap = max(1, horizon)
    spans = {"train": range(0, train_days), "validation": range(train_days + gap, train_days + gap + val_days),
             "test": range(train_days + 2 * gap + val_days, train_days + 2 * gap + val_days + test_days)}
    base = {h: float(np.exp(rng.normal(14.0, 0.5))) for h in range(n_hotels)}
    rec = 0
    out: dict[str, list[dict]] = {k: [] for k in spans}
    for split, days in spans.items():
        for t in days:
            for h in range(n_hotels):
                rec += 1
                mean14 = base[h] * float(np.exp(rng.normal(0, 0.01)))
                spike = rng.random() < 0.10
                dev = float(rng.choice([-1, 1]) * rng.uniform(0.2, 0.5)) if spike else float(rng.normal(0, 0.03))
                cur = mean14 * float(np.exp(dev))
                z = 0.0
                if signal and not all_unchanged:
                    if abs(dev) > 0.15:
                        z = -0.7 * dev
                    elif rng.random() < 0.15:
                        z = float(rng.normal(0, 0.06))
                y = cur * float(np.exp(z))
                if all_unchanged:
                    y = cur
                checkin = (BASE + pd.Timedelta(days=t + 5 + h % 7)).date().isoformat()
                lead = 5 + h % 7
                out[split].append({
                    "hotel_id": f"hotel-{h:03d}", "checkin_date": checkin, "canonical_series_id": f"series-{h}-{checkin}",
                    "vn_observation_date": (BASE + pd.Timedelta(days=t)).date().isoformat(), "warehouse_record_id": rec,
                    "current_price": cur, "y_true": y, "price_rolling_mean_14": mean14, "price_max_trailing_14": mean14 * 1.15, "price_min_trailing_14": mean14 * 0.85,
                    "price_rolling_mean_7": mean14 * float(np.exp(rng.normal(0, 0.005))), "price_rolling_std_7": mean14 * 0.03,
                    "price_velocity": float(rng.normal(0, 0.02)), "lead_time": lead, "lead_time_bucket": "3-7" if lead < 7 else "7-14", "city": CITIES[h % 3],
                    "inference_mode": "cold_start" if t < 4 else "history_enriched"})
    return {k: pd.DataFrame(v) for k, v in out.items()}


def small_cfg(*, families=("hgb_l1",), n_iter: int = 3, null_n: int = 2, budget: float = 3600.0) -> dict:
    """Cau hinh v3 nho cho test: giu cau truc va gia tri hop dong, chi thu nho pool/cay/bootstrap/null va hash tinh lai."""
    from training.config import _with_hash
    from training.v3_contract import apply_smoke_overrides

    cfg = load_config_v3()
    cfg = apply_smoke_overrides(cfg, families=list(families), n_iter=n_iter, null_n=null_n)
    cfg = copy.deepcopy(cfg)
    cfg["smoke"] = False
    cfg["bootstrap"] = {"n": 60, "seed": 20261008}
    cfg["budget"]["time_budget_seconds"] = budget
    cfg["cv"]["n_splits"] = {"1": 2, "3": 2, "7": 2, "14": 2}
    cfg["controls"]["rf_l2"]["params_by_horizon"] = {k: {**v, "n_estimators": 12} for k, v in cfg["controls"]["rf_l2"]["params_by_horizon"].items()}
    for spec in cfg["tuning"]["families"].values():
        if "max_iter" in spec["space"]:
            spec["space"]["max_iter"] = [10, 20]
        if "n_estimators" in spec["space"]:
            spec["space"]["n_estimators"] = [5, 10]
    cfg["controls"]["hgb_l2"]["params"]["max_iter"] = 15
    cfg["controls"]["xgb_l2"]["params"]["n_estimators"] = 5
    return _with_hash(cfg)


# --------------------------------------------------------------------------- xgboost gia
class _FakeBooster:
    def __init__(self, actual):
        self.actual = actual

    def save_config(self):
        return json.dumps({"learner": {"generic_param": {"device": "cuda:0" if self.actual == "cuda" else self.actual}}})


class FakeXGBRegressor:
    """XGBoost GIA: ghi tham so; mo phong 3 kieu loi thiet bi cua XGBoost that (GPT file 16 M5):
    fail_devices (nem ngoai le), warn_fallback_devices (CHI canh bao roi chay CPU), silent_cpu_when (fit that roi chuyen CPU im lang), unreadable_actual (khong doc duoc thiet bi thuc)."""
    instances: list["FakeXGBRegressor"] = []
    fail_devices: set[str] = set()
    warn_fallback_devices: set[str] = set()
    silent_cpu_when = None
    unreadable_actual = False

    def __init__(self, **kwargs):
        if kwargs.get("device") in FakeXGBRegressor.fail_devices:
            raise RuntimeError(f"CUDA khong kha dung (fake) device={kwargs.get('device')}")
        self.kwargs = kwargs
        self.level = 0.0
        self._actual = kwargs.get("device")
        FakeXGBRegressor.instances.append(self)

    def get_params(self, deep=True):
        return dict(self.kwargs)

    def fit(self, X, y):
        self.level = float(np.median(np.asarray(y, float)))
        device = self.kwargs.get("device")
        if device in FakeXGBRegressor.warn_fallback_devices:
            warnings.warn("No visible GPU is found, setting device to CPU.")
            self._actual = "cpu"
        elif FakeXGBRegressor.silent_cpu_when is not None and FakeXGBRegressor.silent_cpu_when(self.kwargs):
            self._actual = "cpu"
        else:
            self._actual = device
        return self

    def get_booster(self):
        if FakeXGBRegressor.unreadable_actual:
            raise AttributeError("booster khong kha dung (fake)")
        return _FakeBooster(self._actual)

    def predict(self, X):
        return np.full(len(X), self.level)


def install_fake_xgboost(monkeypatch, *, fail_devices: set[str] | None = None, warn_fallback_devices: set[str] | None = None, silent_cpu_when=None, unreadable_actual: bool = False):
    mod = types.ModuleType("xgboost")
    mod.__spec__ = importlib.machinery.ModuleSpec("xgboost", None)
    mod.XGBRegressor = FakeXGBRegressor
    mod.__version__ = "3.4.1"
    FakeXGBRegressor.instances = []
    FakeXGBRegressor.fail_devices = set(fail_devices or ())
    FakeXGBRegressor.warn_fallback_devices = set(warn_fallback_devices or ())
    FakeXGBRegressor.silent_cpu_when = silent_cpu_when
    FakeXGBRegressor.unreadable_actual = bool(unreadable_actual)
    monkeypatch.setitem(sys.modules, "xgboost", mod)
    return FakeXGBRegressor


# --------------------------------------------------------------------------- dataset tong hop cho CLI (horizon 1)
def make_v3_dataset(root: Path, *, version: str = "ds_v3test", n_hotels: int = 20, evaluation_horizons=(1,)) -> Path:
    """Dataset day du (hash/contract/dictionary) cho `train_models_v3.py` voi horizon 1 - dung lai builder-fixture cua v2."""
    from dataset_builder.dictionary import dictionary_rows
    from test_training import write_calendar_evidence, write_checksums, write_contract

    # ngay layout giong `test_training.SYNTHETIC_SPLIT_DAYS`: train t<=59, validation 68..82, test >=91
    rng = np.random.default_rng(3)
    rows = []
    base = {h: float(np.exp(rng.normal(14.0, 0.5))) for h in range(n_hotels)}
    for h in range(n_hotels):
        for t in range(110):
            obs = BASE + pd.Timedelta(days=t)
            split = "train" if t <= 59 else ("validation" if 68 <= t <= 82 else ("test" if t >= 91 else None))
            target_split = ("train" if t + 1 <= 59 else "validation" if 68 <= t + 1 <= 82 else "test" if t + 1 >= 91 else None) if t + 1 <= 109 else None
            usable = split is not None and target_split == split
            mean14 = base[h] * float(np.exp(rng.normal(0, 0.01)))
            spike = rng.random() < 0.1
            dev = float(rng.choice([-1, 1]) * rng.uniform(0.2, 0.5)) if spike else float(rng.normal(0, 0.03))
            cur = mean14 * float(np.exp(dev))
            z = -0.7 * dev if abs(dev) > 0.15 else (float(rng.normal(0, 0.06)) if rng.random() < 0.15 else 0.0)
            y = cur * float(np.exp(z))
            lead = 5 + h % 7
            rows.append({
                "prediction_match_status": "exact", "label_match_status_h1": "exact" if target_split is not None else None, "dataset_version": version,
                "hotel_id": f"hotel-{h:03d}", "checkin_date": (obs + pd.Timedelta(days=lead)).date(), "canonical_series_id": f"series-{h}-{lead}",
                "vn_observation_date": obs, "prediction_time": obs, "split": split, "hotel_seen_in_train_h1": True, "warehouse_record_id": h * 1000 + t,
                "current_price": cur, "price_rolling_mean_14": mean14, "price_max_trailing_14": mean14 * 1.15, "price_min_trailing_14": mean14 * 0.85,
                "price_rolling_mean_7": mean14, "price_rolling_std_7": mean14 * 0.03, "price_velocity": float(rng.normal(0, 0.02)), "lead_time": lead,
                "lead_time_bucket": "3-7" if lead < 7 else "7-14", "city": CITIES[h % 3], "inference_mode": "cold_start" if t < 4 else "history_enriched",
                "has_label_h1": usable or target_split is not None, "label_usable_h1": usable, "y_price_h1": y if target_split is not None else np.nan,
                "y_delta_h1": y - cur, "y_pct_change_h1": (y - cur) / cur,
                "y_direction_h1": "up" if (y - cur) / cur > 0.02 else "down" if (y - cur) / cur < -0.02 else "stable"})
    frame = pd.DataFrame(rows)
    out = root / version
    out.mkdir(parents=True, exist_ok=True)
    frame.to_parquet(out / "samples.parquet", index=False)
    id_cols = ["dataset_version", "hotel_id", "checkin_date", "canonical_series_id", "vn_observation_date", "prediction_time", "split", "hotel_seen_in_train_h1",
               "prediction_match_status", "label_match_status_h1", "warehouse_record_id"]
    label_cols = [f"{p}_h1" for p in ("has_label", "label_usable", "y_price", "y_delta", "y_pct_change", "y_direction")]
    pd.DataFrame(dictionary_rows(id_cols + FEATURES + label_cols)).to_csv(out / "data_dictionary.csv", index=False)
    (out / "coverage_report.json").write_text(json.dumps({"rows": len(frame)}), encoding="utf-8")
    write_calendar_evidence(out)
    write_contract(out, version=version, evaluation_horizons=evaluation_horizons, purpose="dev", status="exploratory", n1=False)
    write_checksums(out, rows=len(frame))
    return out

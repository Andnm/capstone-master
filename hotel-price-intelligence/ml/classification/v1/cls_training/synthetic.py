"""Du lieu TONG HOP (khong dung dataset that) dung chung cho test va cho smoke tren Colab: khung feature nho nhung co du cot ma pipeline dung (ke ca dau vao cua khoi ratio-1.0).

Fixture tong hop mang dinh danh tong hop ro rang (`dataset_version='ds_synthetic'`, khong co `output_checksums_sha256`) nen khong the bi nham voi nf1/nf3 that.
"""
from __future__ import annotations

import copy
from types import SimpleNamespace
from typing import Any

import numpy as np
import pandas as pd

SYNTH_COLUMNS = ["inference_mode", "city", "free_cancellation", "current_price", "price_velocity", "price_lag_1", "price_rolling_mean_7", "price_rolling_std_7",
                 "price_rolling_mean_14", "price_max_trailing_14", "price_min_trailing_14", "history_observation_count", "lead_time", "lead_time_bucket", "is_weekend"]
SYNTH_GROUPS = {"inference_mode": "mode", "city": "static", "free_cancellation": "static", "current_price": "current", "price_velocity": "history", "price_lag_1": "history",
                "price_rolling_mean_7": "history", "price_rolling_std_7": "history", "price_rolling_mean_14": "history", "price_max_trailing_14": "history",
                "price_min_trailing_14": "history", "history_observation_count": "history", "lead_time": "lead_time", "lead_time_bucket": "lead_time", "is_weekend": "calendar"}
CITIES = ["Hồ Chí Minh", "Hà Nội", "Vũng Tàu", "Đà Lạt", "Phú Quốc"]
BUCKETS = ["lt3", "3-7", "7-14", "14-30", "30-60", "gt60"]


def synth_dictionary() -> pd.DataFrame:
    return pd.DataFrame({"column": SYNTH_COLUMNS, "group": [SYNTH_GROUPS[c] for c in SYNTH_COLUMNS]})


def make_frame(n: int = 600, seed: int = 0, n_days: int = 24) -> tuple[pd.DataFrame, np.ndarray]:
    """Khung da sap theo ngay; nhan 3 lop mat can bang (~85% stable) tu tin hieu `price_velocity` + nhieu => mo hinh hoc duoc mot phan."""
    rng = np.random.default_rng(seed)
    days = np.sort(rng.integers(0, n_days, n))
    cur = rng.uniform(3e5, 3e6, n).round(0)
    vel = rng.normal(0, 0.05, n)
    vel[rng.random(n) < 0.15] = np.nan
    signal = np.nan_to_num(vel) + rng.normal(0, 0.03, n)
    y = np.where(signal > 0.07, 2, np.where(signal < -0.07, 0, 1)).astype(np.int8)
    for k in (0, 1, 2):                                          # moi lop co mat o moi nua dau/cuoi => moi fold du 3 lop
        y[k::50] = k
    hist = rng.integers(0, 12, n)
    frame = pd.DataFrame({
        "warehouse_record_id": np.arange(5000, 5000 + n, dtype="int64"),
        "hotel_id": [f"h{i % 17}" for i in range(n)], "checkin_date": (pd.Timestamp("2026-10-20") + pd.to_timedelta(rng.integers(0, 10, n), unit="D")).strftime("%Y-%m-%d"),
        "canonical_series_id": [f"s{i}" for i in range(n)], "vn_observation_date": (pd.Timestamp("2026-09-01") + pd.to_timedelta(days, unit="D")).strftime("%Y-%m-%d"),
        "split": "train",
        "inference_mode": np.where(hist > 2, "history_enriched", "cold_start"), "city": rng.choice(CITIES, n),
        "free_cancellation": pd.array(rng.choice([True, False, None], n, p=[0.5, 0.45, 0.05]), dtype="boolean"),
        "current_price": cur, "price_velocity": vel, "price_lag_1": cur * (1 - np.nan_to_num(vel)), "price_rolling_mean_7": cur * rng.uniform(0.9, 1.1, n),
        "price_rolling_std_7": cur * rng.uniform(0.0, 0.1, n), "price_rolling_mean_14": cur * rng.uniform(0.9, 1.1, n), "price_max_trailing_14": cur * rng.uniform(1.0, 1.3, n),
        "price_min_trailing_14": cur * rng.uniform(0.7, 1.0, n), "history_observation_count": hist, "lead_time": rng.integers(0, 60, n),
        "lead_time_bucket": rng.choice(BUCKETS, n), "is_weekend": rng.random(n) < 0.3,
    })
    frame["y_direction_h1"] = np.array(["down", "stable", "up"], dtype=object)[y]
    return frame, y.astype(np.int64)


def make_loaded(horizon: int = 1, *, n_train: int = 700, n_eval: int = 300):
    """LoadedDataset gia lap (chi truong runner dung). Ngay: train 24 ngay dau; validation +30; test +45 (khong chong lan)."""
    train, _ = make_frame(n_train, seed=1, n_days=24)
    val, _ = make_frame(n_eval, seed=2, n_days=8)
    test, _ = make_frame(n_eval, seed=3, n_days=8)
    for frame, name, shift, off in ((train, "train", 0, 0), (val, "validation", 30, 100_000), (test, "test", 45, 200_000)):
        frame["split"] = name
        frame["warehouse_record_id"] = frame["warehouse_record_id"] + off
        frame["vn_observation_date"] = (pd.to_datetime(frame["vn_observation_date"]) + pd.to_timedelta(shift, unit="D")).dt.strftime("%Y-%m-%d")
        frame[f"y_direction_h{horizon}"] = frame["y_direction_h1"]
    pin = {"dataset_version": "ds_synthetic", "horizon": horizon}
    unseen = {}
    for name, src in (("validation", val), ("test", test)):                  # hang cua mot hotel KHONG co trong train (mau so all-hotels)
        u = src.head(12).copy()
        u["hotel_id"] = "hotel_unseen"
        u["warehouse_record_id"] = u["warehouse_record_id"] + 50_000
        u["canonical_series_id"] = u["canonical_series_id"] + "_u"
        unseen[name] = u
    return SimpleNamespace(horizon=horizon, frames={"train": train, "validation": val, "test": test}, features=list(SYNTH_COLUMNS), dictionary=synth_dictionary(), pin=pin,
                           unseen_frames=unseen)


def smoke_config(cfg: dict[str, Any]) -> dict[str, Any]:
    """Cau hinh THAT thu nho cho smoke tong hop: giu nguyen device/seed/quota/ho ung vien, chi thu nho khong gian cay + CV + so cot A2 de chay nhanh tren fixture; van tinh lai van tay ung vien + hash."""
    from .candidates import candidate_set_sha256
    from .config import canonical_sha256
    out = copy.deepcopy(cfg)
    out["tuning"]["families"]["hgb"]["space"]["max_iter"] = [20, 30]
    out["tuning"]["families"]["hgb"]["space"]["max_depth"] = [2, 3]
    out["tuning"]["families"]["xgb"]["space"]["n_estimators"] = [10, 20]
    out["cv"] = {"min_train_days": 8, "n_splits": {"1": 3, "3": 2}}
    out["ablation"] = {**out["ablation"], "a2_n_features": 6}
    out["tuning"]["candidate_set_sha256"] = candidate_set_sha256(out)
    out.pop("config_sha256", None)
    out["config_sha256"] = canonical_sha256(out)
    return out

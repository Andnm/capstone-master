"""Feature v1 + nhan (CLAUDE.md muc 5/6, spec muc 14) tren cac `ml_samples` da chon.

Moi dong = 1 daily snapshot duoc chon cua 1 frozen series (hotel, check-in, canonical room/rate) tai 1 ngay quan sat VN.
CAUSAL: moi feature lich su chi dung CAC MAU DA CHON truoc do cua CUNG series (`vn_observation_date` nho hon ngay du bao); mau
chi ton tai tu `approved_at` nen khong bao gio dung quan sat truoc khi series duoc duyet. Thieu lich su -> NULL (khong dien).
Thuoc tinh phong/rate lay tu assignment da dong bang (on dinh theo series), khong tu observation le.
"""
from __future__ import annotations

import re
from typing import Any

import numpy as np
import pandas as pd

from . import env  # noqa: F401
from .calendar_features import CalendarFeatures
from .db import fetch_all
from .feature_spec import HORIZONS, LEAD_TIME_BUCKETS

_SAMPLES_SQL = """
SELECT s.record_id AS warehouse_record_id, s.prediction_time, s.vn_observation_date, s.split,
       a.hotel_id, a.checkin_date, a.canonical_series_id, h.city,
       a.max_occupancy, a.room_area, a.breakfast_included, a.free_cancellation,
       po.price_per_night AS current_price, po.rooms_left, po.discount_percent,
       s.label_source_record_id_h1 AS lsr_h1, s.label_source_record_id_h3 AS lsr_h3,
       s.label_source_record_id_h7 AS lsr_h7, s.label_source_record_id_h14 AS lsr_h14
FROM ml_samples s
JOIN ml_reference_assignments a ON a.id = s.ml_reference_assignment_id AND a.dataset_version = s.dataset_version
JOIN price_observations po ON po.record_id = s.record_id
JOIN hotels h ON h.hotel_id = a.hotel_id
WHERE s.dataset_version = %s AND s.is_daily_snapshot_selected = TRUE
ORDER BY a.hotel_id, a.checkin_date, a.canonical_series_id, s.vn_observation_date
"""

_AREA_RE = re.compile(r"(\d+(?:[.,]\d+)?)\s*(m|ft)", re.IGNORECASE)
FT2_TO_M2 = 0.09290304


def parse_room_area_m2(text: str | None) -> float | None:
    """'25 m²' / '20 m2' -> 25.0; '269 ft²' -> 24.99 m². Khong parse duoc -> None."""
    if not text:
        return None
    match = _AREA_RE.search(str(text).replace("²", "2"))
    if not match:
        return None
    value = float(match.group(1).replace(",", "."))
    return round(value * FT2_TO_M2, 2) if match.group(2).lower() == "ft" else value


def lead_time_bucket(lead_time: int) -> str:
    for lo, hi, name in LEAD_TIME_BUCKETS:
        if lo <= lead_time <= hi:
            return name
    raise ValueError(f"lead_time ngoai bucket: {lead_time}")


def history_features_for_series(day_ordinals: np.ndarray, prices: np.ndarray, *, cold_start_max_history: int) -> dict[str, np.ndarray]:
    """Feature lich su cho MOT series; `day_ordinals` tang dan, duy nhat; chi dung mau co chi so < i (truoc ngay du bao)."""
    n = len(day_ordinals)
    out = {name: np.full(n, np.nan) for name in (
        "price_lag_1", "price_lag_3", "price_lag_7", "price_lag_14", "price_rolling_mean_7", "price_rolling_mean_14",
        "price_rolling_std_7", "price_velocity", "price_min_trailing_14", "price_max_trailing_14", "price_vs_own_mean_30",
        "days_since_last_price_change", "history_span_days")}
    counts = np.arange(n, dtype=np.int64)
    for i in range(n):
        if i == 0:
            continue
        day = day_ordinals[i]
        for k in (1, 3, 7, 14):
            idx = np.searchsorted(day_ordinals, day - k, side="left")
            if idx < i and day_ordinals[idx] == day - k:
                out[f"price_lag_{k}"][i] = prices[idx]
        for window in (7, 14, 30):
            left = np.searchsorted(day_ordinals, day - window, side="left")
            chunk = prices[left:i]
            if chunk.size:
                if window == 7:
                    out["price_rolling_mean_7"][i] = chunk.mean()
                    if chunk.size >= 2:
                        out["price_rolling_std_7"][i] = chunk.std(ddof=1)
                elif window == 14:
                    out["price_rolling_mean_14"][i] = chunk.mean()
                    out["price_min_trailing_14"][i] = chunk.min()
                    out["price_max_trailing_14"][i] = chunk.max()
                else:
                    out["price_vs_own_mean_30"][i] = prices[i] / chunk.mean()
        previous = prices[i - 1]
        if previous:
            out["price_velocity"][i] = (prices[i] - previous) / previous
        last_change = None
        for j in range(i, 0, -1):
            if prices[j] != prices[j - 1]:
                last_change = day_ordinals[j]
                break
        out["days_since_last_price_change"][i] = (day - last_change) if last_change is not None else (day - day_ordinals[0])
        out["history_span_days"][i] = day - day_ordinals[0]
    out["history_observation_count"] = counts
    return out


def build_feature_frame(conn, *, dataset_version: str, config: dict[str, Any], calendar: CalendarFeatures) -> pd.DataFrame:
    feature_cfg = config["feature_config"]
    label_cfg = config["label_config"]
    rows = fetch_all(conn, _SAMPLES_SQL, (dataset_version,))
    conn.commit()
    if not rows:
        raise ValueError("khong co sample duoc chon de xuat dataset.")
    df = pd.DataFrame(rows)
    df["current_price"] = df["current_price"].astype("float64")
    df["discount_percent"] = pd.to_numeric(df["discount_percent"], errors="coerce").astype("float64")
    df["rooms_left"] = df["rooms_left"].astype("Int64")
    df["max_occupancy"] = df["max_occupancy"].astype("Int64")
    for column in ("breakfast_included", "free_cancellation"):
        df[column] = df[column].map(lambda v: None if v is None else bool(v)).astype("boolean")
    df["room_area_m2"] = df["room_area"].map(parse_room_area_m2).astype("float64")
    df = df.drop(columns=["room_area"])
    df["vn_observation_date"] = pd.to_datetime(df["vn_observation_date"])
    df["checkin_date"] = pd.to_datetime(df["checkin_date"])
    df["lead_time"] = (df["checkin_date"] - df["vn_observation_date"]).dt.days.astype("int64")
    df["lead_time_bucket"] = df["lead_time"].map(lead_time_bucket)
    df["is_last_minute"] = df["lead_time"] <= 3
    # lich (theo ngay check-in + thanh pho)
    cal_cache: dict[tuple, dict] = {}
    for key in df[["checkin_date", "city"]].drop_duplicates().itertuples(index=False):
        cal_cache[(key.checkin_date, key.city)] = calendar.features(key.checkin_date.date(), key.city)
    calendar_frame = pd.DataFrame([cal_cache[(c, city)] for c, city in zip(df["checkin_date"], df["city"])], index=df.index)
    calendar_frame["days_to_nearest_holiday"] = calendar_frame["days_to_nearest_holiday"].astype("Int64")
    for column in calendar_frame.columns:
        df[column] = calendar_frame[column]
    # lich su gia theo tung series (df da sap xep theo series, ngay)
    history_columns: dict[str, list[np.ndarray]] = {}
    for _series, group in df.groupby("canonical_series_id", sort=False):
        ordinals = group["vn_observation_date"].map(pd.Timestamp.toordinal).to_numpy(dtype=np.int64)
        features = history_features_for_series(ordinals, group["current_price"].to_numpy(dtype=np.float64),
                                               cold_start_max_history=feature_cfg["cold_start_max_history"])
        for name, values in features.items():
            history_columns.setdefault(name, []).append(values)
    for name, chunks in history_columns.items():
        df[name] = np.concatenate(chunks)
    df["history_observation_count"] = df["history_observation_count"].astype("int64")
    df["inference_mode"] = np.where(df["history_observation_count"] <= feature_cfg["cold_start_max_history"], "cold_start", "history_enriched")
    # nhan: target la mau da chon cua cung frozen series; label_usable = target cung split voi source
    price_by_record = dict(zip(df["warehouse_record_id"], df["current_price"]))
    split_by_record = dict(zip(df["warehouse_record_id"], df["split"]))
    threshold = float(label_cfg["direction_threshold"])
    for k in HORIZONS:
        target = df[f"lsr_h{k}"]
        has = target.notna()
        df[f"has_label_h{k}"] = has
        y = target.map(price_by_record).astype("float64")
        df[f"y_price_h{k}"] = y
        df[f"y_delta_h{k}"] = y - df["current_price"]
        pct = df[f"y_delta_h{k}"] / df["current_price"]
        df[f"y_pct_change_h{k}"] = pct
        direction = pd.Series(np.where(pct > threshold, "up", np.where(pct < -threshold, "down", "stable")), index=df.index, dtype="object")
        direction[~has] = None
        df[f"y_direction_h{k}"] = direction
        target_split = target.map(split_by_record)
        df[f"label_usable_h{k}"] = has & df["split"].notna() & (target_split == df["split"])
    train_hotels = set(df.loc[df["split"] == "train", "hotel_id"])
    df["hotel_seen_in_train"] = df["hotel_id"].isin(train_hotels)
    df = df.drop(columns=[f"lsr_h{k}" for k in HORIZONS])
    df.insert(0, "dataset_version", dataset_version)
    return df


def output_columns(config: dict[str, Any]) -> list[str]:
    """Thu tu cot Parquet: dinh danh -> nhom feature -> nhan."""
    groups = config["feature_config"]["groups"]
    identifiers = ["dataset_version", "hotel_id", "checkin_date", "canonical_series_id", "vn_observation_date", "prediction_time",
                   "split", "hotel_seen_in_train", "warehouse_record_id"]
    features = [column for group in groups.values() for column in group]
    labels: list[str] = []
    for k in HORIZONS:
        labels += [f"has_label_h{k}", f"label_usable_h{k}", f"y_price_h{k}", f"y_delta_h{k}", f"y_pct_change_h{k}", f"y_direction_h{k}"]
    return identifiers + features + labels

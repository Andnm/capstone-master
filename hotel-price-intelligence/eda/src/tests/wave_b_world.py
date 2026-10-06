"""Mot "the gioi" gia nhat quan cho test Wave B: Parquet + ml_samples + assignments + matches + hotels dung quy tac cua builder (khong can MySQL).

3 series (2 hotel, 2 thanh pho), quan sat moi ngay; mau chi ton tai tu ngay approval (day 3); split theo cong thuc spec (P = purge); nhan h{k} = mau cung series o d+k,
usable khi cung split. Co them mau KHONG duoc chon (duplicate) o ml_samples va item pre-approval/unavailable o matches.
"""
from __future__ import annotations

import datetime as dt
from pathlib import Path

import numpy as np
import pandas as pd

import wave_b
from wave_b import WaveBData
from wave_b_inputs import WaveBInputs

START = pd.Timestamp("2026-09-01")
HORIZONS = (1, 3, 7, 14)
APPROVAL_DAY = 3                          # assignment duoc duyet khi run cua ngay 3 ket thuc (run-completion semantics)
FIRST_SAMPLE_DAY = APPROVAL_DAY + 1       # mau chi ton tai khi observed_at >= approved_at => khong gom mau cua CHINH run approving
SERIES = [("A", "hotel-a", "Hà Nội"), ("B", "hotel-a", "Hà Nội"), ("C", "hotel-c", "Đà Lạt")]   # (series id, hotel, city)


def _zone(day: int, train_end: int, validation_end: int, purge: int) -> str | None:
    if day <= train_end:
        return "train"
    if day <= train_end + purge:
        return None
    if day <= validation_end:
        return "validation"
    if day <= validation_end + purge:
        return None
    return "test"


def make_world(*, n_days: int = 40, purge: int = 2, train_end: int = 14, validation_end: int = 24, alias_every: int = 7) -> WaveBData:
    sample_rows, ml_rows, match_rows, assignments, first_evidence = [], [], [], [], []
    record_of: dict[tuple[str, int], int] = {}
    for index, (series, hotel, city) in enumerate(SERIES, start=1):
        for day in range(n_days):
            record_of[(series, day)] = 1000 * index + day
    price_of = {key: 500_000.0 + 1_000.0 * (ord(key[0]) % 5) + (3_000.0 * key[1]) % 17_000 for key in record_of}
    for index, (series, hotel, city) in enumerate(SERIES, start=1):
        checkin = START + pd.Timedelta(days=60)
        approved_at = (START + pd.Timedelta(days=APPROVAL_DAY)).to_pydatetime() - dt.timedelta(hours=6, minutes=30) + dt.timedelta(hours=10)     # = run.finished_at cua run ngay 3
        assignments.append({"assignment_id": index, "hotel_id": hotel, "city": city, "checkin_date": checkin, "approved_at": approved_at, "approving_run_warehouse_id": 1 + APPROVAL_DAY,
                            "evidence_run_count": 3, "evidence_item_count": 3, "eligible_item_count": 3, "coverage": 0.9, "confidence_score": 0.9})
        first_evidence.append({"assignment_id": index, "first_evidence_run_finished_at": pd.Timestamp(START.to_pydatetime() - dt.timedelta(hours=6, minutes=30) + dt.timedelta(hours=10)),
                               "evidence_items_all_time": 3})
        for day in range(n_days):
            record = record_of[(series, day)]
            date = START + pd.Timedelta(days=day)
            status = "exact" if day % alias_every else "alias"
            started = date.to_pydatetime() - dt.timedelta(hours=6, minutes=30)                                   # 00:30 gio VN cua `date`
            event = started + dt.timedelta(hours=1)                                                              # observed_at cua item (trong run)
            match_rows.append({"crawl_run_item_id": 100_000 + record, "assignment_id": index, "match_status": status, "match_score": 1.0, "selected_record_id": record,
                               "run_id": 1 + day, "checkin_date": checkin, "city": city, "source_code": "local_primary" if day % 2 else "vps",
                               "run_started_at": started, "run_finished_at": started + dt.timedelta(hours=10), "approved_at": approved_at,
                               "approving_run_warehouse_id": 1 + APPROVAL_DAY, "event_utc": event, "selected_observed_at": event, "item_obs_min": event, "item_obs_max": event})
            if day % 5 == 0:
                match_rows.append({"crawl_run_item_id": 200_000 + record, "assignment_id": index, "match_status": "unavailable", "match_score": None, "selected_record_id": None,
                                   "run_id": 1 + day, "checkin_date": checkin, "city": city, "source_code": "vps", "run_started_at": started,
                                   "run_finished_at": started + dt.timedelta(hours=10), "approved_at": approved_at, "approving_run_warehouse_id": 1 + APPROVAL_DAY,
                                   "event_utc": event, "selected_observed_at": pd.NaT, "item_obs_min": event, "item_obs_max": event})
            if day < FIRST_SAMPLE_DAY:
                continue
            split = _zone(day, train_end, validation_end, purge)
            row = {"warehouse_record_id": record, "hotel_id": hotel, "checkin_date": checkin, "canonical_series_id": series, "vn_observation_date": date, "split": split,
                   "city": city, "lead_time": (checkin - date).days, "current_price": price_of[(series, day)], "breakfast_included": bool(index % 2),
                   "history_observation_count": day - FIRST_SAMPLE_DAY, "inference_mode": "cold_start" if day - FIRST_SAMPLE_DAY <= 2 else "history_enriched"}
            for lag in (1, 3, 7, 14):
                prior = day - lag
                row[f"price_lag_{lag}"] = price_of[(series, prior)] if prior >= FIRST_SAMPLE_DAY else np.nan
            for k in HORIZONS:
                target_day = day + k
                has = target_day < n_days
                target_split = _zone(target_day, train_end, validation_end, purge) if has else None
                row[f"has_label_h{k}"] = bool(has)
                row[f"label_usable_h{k}"] = bool(has and split is not None and target_split == split)
                row[f"_target_h{k}"] = record_of[(series, target_day)] if has else None
            sample_rows.append(row)
            ml = {"record_id": record, "assignment_id": index, "vn_observation_date": date, "is_daily_snapshot_selected": True,
                  "daily_snapshot_reason": "single_candidate" if day % 4 else "earliest_in_day", "split": split}
            for k in HORIZONS:
                ml[f"has_label_h{k}"] = row[f"has_label_h{k}"]
                ml[f"label_source_record_id_h{k}"] = row[f"_target_h{k}"]
            ml_rows.append(ml)
            if day % 4 == 0:                                                                                   # mot ung vien thu hai bi loai (duplicate trong ngay)
                dup = dict(ml, record_id=90_000 + record, is_daily_snapshot_selected=False, daily_snapshot_reason="later_in_day",
                           **{f"has_label_h{k}": False for k in HORIZONS}, **{f"label_source_record_id_h{k}": None for k in HORIZONS})
                ml_rows.append(dup)
    samples = pd.DataFrame(sample_rows)
    for k in HORIZONS:
        seen = set(samples.loc[samples[f"label_usable_h{k}"] & (samples["split"] == "train"), "hotel_id"])
        samples[f"hotel_seen_in_train_h{k}"] = samples["hotel_id"].isin(seen)
        samples = samples.drop(columns=f"_target_h{k}")
    samples.insert(0, "dataset_version", "ds_world")
    ml_samples = pd.DataFrame(ml_rows)
    ml_samples["vn_observation_date"] = pd.to_datetime(ml_samples["vn_observation_date"])
    matches = pd.DataFrame(match_rows)
    for column in ("run_started_at", "run_finished_at", "approved_at", "event_utc", "selected_observed_at", "item_obs_min", "item_obs_max"):
        matches[column] = pd.to_datetime(matches[column])
    assignments_df = pd.DataFrame(assignments)
    assignments_df["approved_at"] = pd.to_datetime(assignments_df["approved_at"])
    hotels = pd.DataFrame({"hotel_id": ["hotel-a", "hotel-c"], "city": ["Hà Nội", "Đà Lạt"], "review_score": [9.1, 7.4], "review_count": [100, 20]})
    dictionary = pd.DataFrame({"column": [c for c in samples.columns], "group": [
        "identifier" if c in ("dataset_version", "hotel_id", "checkin_date", "canonical_series_id", "vn_observation_date", "split", "warehouse_record_id")
        or c.startswith("hotel_seen") else "label" if c.startswith(("has_label", "label_usable")) else "feature" for c in samples.columns]})
    preflight = {"split_train_end": (START + pd.Timedelta(days=train_end)).date().isoformat(), "split_validation_end": (START + pd.Timedelta(days=validation_end)).date().isoformat(),
                 "purge_gap_days": purge, "artifact_generation": {"has_dataset_contract": True, "has_strata_columns": False, "builder_version": "dataset-builder-test",
                                                                   "feature_version": "features-test", "evaluation_horizons": [1, 3, 7, 14], "purpose": "rehearsal", "legacy": False}}
    inputs = WaveBInputs("db_test", "b_test", "ds_world", Path("."))
    return WaveBData(inputs, preflight, samples, ml_samples, assignments_df, pd.DataFrame(first_evidence), matches, hotels, dictionary, [1, 3, 7, 14])


def compute(world: WaveBData | None = None):
    world = world or make_world()
    return world, wave_b.compute_tables(world)

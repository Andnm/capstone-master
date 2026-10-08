"""Audit nhan/split (hai chieu) cho samples.parquet cua dataset train-v3 (GPT file 28 R-m1). Chi doc; pure function tren DataFrame de co negative control.

usable_hk  <=>  has_target_hk  AND source_split in {train, validation, test}  AND target_split == source_split          (khong chi `usable ⊆ has`)
Dong khong nhan phai khong co gia/delta/pct/direction. Moi dong phai nam trong [train_start, test_end] (khong co mau truoc train_start). Split theo NGAY phai khop cot `split`.
Khong dung code cua builder: dinh nghia lay tu data_dictionary/CLAUDE.md 6.1 (direction: >+2% up, <-2% down, con lai stable; so sanh strict).
"""
from __future__ import annotations

from typing import Any, Mapping

import numpy as np
import pandas as pd

HORIZONS = (1, 3, 7, 14)
SPLITS = ("train", "validation", "test")


def _ordinal(value: Any) -> int:
    return pd.Timestamp(value).toordinal()


def split_of_ordinals(ordinals: np.ndarray, plan: Mapping[str, Any]) -> np.ndarray:
    """'train'|'validation'|'test'|'purge' (giua cac split) | 'outside' (truoc train_start hoac sau test_end)."""
    ts, te = _ordinal(plan["train_start"]), _ordinal(plan["train_end"])
    vs, ve = _ordinal(plan["validation_start"]), _ordinal(plan["validation_end"])
    xs, xe = _ordinal(plan["test_start"]), _ordinal(plan["test_end"])
    out = np.full(len(ordinals), "purge", dtype=object)
    out[(ordinals < ts) | (ordinals > xe)] = "outside"
    out[(ordinals >= ts) & (ordinals <= te)] = "train"
    out[(ordinals >= vs) & (ordinals <= ve)] = "validation"
    out[(ordinals >= xs) & (ordinals <= xe)] = "test"
    return out


def _same(a: np.ndarray, b: np.ndarray) -> np.ndarray:
    a, b = np.asarray(a, float), np.asarray(b, float)
    return (np.isnan(a) & np.isnan(b)) | np.isclose(a, b, rtol=1e-9, atol=1e-9)


def audit_labels(p: pd.DataFrame, plan: Mapping[str, Any], horizons=HORIZONS) -> dict[str, Any]:
    ordinals = pd.to_datetime(p["vn_observation_date"]).map(lambda x: x.toordinal()).to_numpy()
    sid = p["canonical_series_id"].to_numpy()
    cur = p["current_price"].to_numpy(float)
    price_by = {(s, int(o)): float(c) for s, o, c in zip(sid, ordinals, cur)}
    src_split = split_of_ordinals(ordinals, plan)
    given_split = np.array([s if isinstance(s, str) else "purge" for s in p["split"].astype(object).where(p["split"].notna(), None).to_numpy()], dtype=object)
    out: dict[str, Any] = {"rows": int(len(p)), "rows_before_train_start_or_after_test_end": int((src_split == "outside").sum()),
                           "split_column_vs_date_mismatch": int((np.where(src_split == "outside", "purge", src_split) != given_split).sum())}
    for k in horizons:
        has = p[f"has_label_h{k}"].to_numpy(bool)
        usable = p[f"label_usable_h{k}"].to_numpy(bool)
        y = p[f"y_price_h{k}"].to_numpy(float)
        target = np.array([price_by.get((s, int(o) + k), np.nan) for s, o in zip(sid, ordinals)])
        has_expected = ~np.isnan(target)
        tgt_split = split_of_ordinals(ordinals + k, plan)
        expected_usable = has_expected & np.isin(src_split, SPLITS) & (tgt_split == src_split)
        res = {"has_label_vs_target_exists_mismatch": int((has != has_expected).sum()),
               "usable_but_not_expected": int((usable & ~expected_usable).sum()), "expected_but_not_usable": int((~usable & expected_usable).sum()),
               "usable_rows": int(usable.sum()), "expected_usable_rows": int(expected_usable.sum()),
               "usable_on_purge_or_outside_rows": int((usable & ~np.isin(src_split, SPLITS)).sum()),
               "usable_with_cross_split_target": int((usable & np.isin(src_split, SPLITS) & (tgt_split != src_split)).sum())}
        m = has & has_expected
        res["y_price_ne_target_snapshot_price"] = int((~_same(y[m], target[m])).sum())
        delta, pct, direction = (p.get(f"y_delta_h{k}"), p.get(f"y_pct_change_h{k}"), p.get(f"y_direction_h{k}"))
        if delta is not None:
            expected_delta = np.where(m, target - cur, np.nan)
            res["y_delta_mismatch"] = int((~_same(delta.to_numpy(float), expected_delta)).sum())
        if pct is not None:
            expected_pct = np.where(m, (target - cur) / cur, np.nan)
            res["y_pct_change_mismatch"] = int((~_same(pct.to_numpy(float), expected_pct)).sum())
        if direction is not None:
            expected_dir = np.where(~m, None, np.where(((target - cur) / cur) > 0.02, "up", np.where(((target - cur) / cur) < -0.02, "down", "stable")))
            got_dir = direction.astype(object).where(direction.notna(), None).to_numpy()
            res["y_direction_mismatch"] = int(sum(1 for a, b in zip(expected_dir, got_dir) if a != b))
        no_label = ~has
        bad = int((no_label & ~np.isnan(y)).sum())
        for extra in (delta, pct):
            if extra is not None:
                bad += int((no_label & extra.notna().to_numpy()).sum())
        if direction is not None:
            bad += int((no_label & direction.notna().to_numpy()).sum())
        res["rows_without_label_but_with_y_values"] = bad
        out[f"h{k}"] = res
    return out


def total_violations(result: Mapping[str, Any]) -> int:
    """Tong cac bo dem 'loi' (khong tinh so dong usable/expected); dung de assert audit sach va de negative control phai > 0."""
    skip = {"rows", "usable_rows", "expected_usable_rows"}
    total = 0
    for key, value in result.items():
        if isinstance(value, dict):
            total += sum(int(v) for k, v in value.items() if k not in skip)
        elif key not in skip:
            total += int(value)
    return total

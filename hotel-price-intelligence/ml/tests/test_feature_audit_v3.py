"""Audit nhan hai chieu (audit_tools/feature_audit_v3.py; GPT file 28 R-m1): khung tong hop dung hop dong => 0 loi; moi loi cu the (mat 1 usable, usable tren purge/cross-split, y co gia o dong khong nhan,
has sai, truoc train_start, delta/direction sai) phai lam so loi > 0 (negative control, khong 'xanh rong')."""
from __future__ import annotations

import importlib.util
from pathlib import Path

import numpy as np
import pandas as pd

TOOLS = Path(__file__).resolve().parents[2] / "audit_tools"
spec = importlib.util.spec_from_file_location("feature_audit_v3", TOOLS / "feature_audit_v3.py")
audit = importlib.util.module_from_spec(spec)
spec.loader.exec_module(audit)

PLAN = {"train_start": "2026-09-01", "train_end": "2026-09-10", "validation_start": "2026-09-13", "validation_end": "2026-09-18", "test_start": "2026-09-21", "test_end": "2026-09-26"}
BASE = pd.Timestamp("2026-09-01")


def _split(day: int):
    d = BASE + pd.Timedelta(days=day)
    for name, a, b in (("train", "train_start", "train_end"), ("validation", "validation_start", "validation_end"), ("test", "test_start", "test_end")):
        if pd.Timestamp(PLAN[a]) <= d <= pd.Timestamp(PLAN[b]):
            return name
    return None


def synthetic(seed=0) -> pd.DataFrame:
    rng = np.random.default_rng(seed)
    rows = []
    for s in range(8):
        days = [d for d in range(0, 26) if rng.random() > 0.15]                                   # vai ngay thieu => mot so dong khong co nhan
        price = {d: float(round(rng.uniform(5e5, 3e6), 0)) for d in days}
        for d in days:
            row = {"canonical_series_id": f"s{s}", "vn_observation_date": (BASE + pd.Timedelta(days=d)).date().isoformat(), "split": _split(d), "current_price": price[d]}
            for k in audit.HORIZONS:
                t = price.get(d + k)
                has = t is not None
                src, tgt = _split(d), _split(d + k)
                row[f"has_label_h{k}"] = has
                row[f"label_usable_h{k}"] = bool(has and src is not None and tgt == src)
                row[f"y_price_h{k}"] = t if has else np.nan
                row[f"y_delta_h{k}"] = (t - price[d]) if has else np.nan
                row[f"y_pct_change_h{k}"] = ((t - price[d]) / price[d]) if has else np.nan
                pct = ((t - price[d]) / price[d]) if has else None
                row[f"y_direction_h{k}"] = None if not has else ("up" if pct > 0.02 else ("down" if pct < -0.02 else "stable"))
            rows.append(row)
    return pd.DataFrame(rows)


def _total(frame):
    return audit.total_violations(audit.audit_labels(frame, PLAN))


def test_a_consistent_frame_has_zero_violations_and_nonzero_usable_rows():
    frame = synthetic()
    result = audit.audit_labels(frame, PLAN)
    assert audit.total_violations(result) == 0
    assert all(result[f"h{k}"]["usable_rows"] > 0 and result[f"h{k}"]["usable_rows"] == result[f"h{k}"]["expected_usable_rows"] for k in (1, 3))


def test_dropping_one_valid_usable_row_is_detected():
    frame = synthetic()
    idx = frame.index[frame["label_usable_h1"]][0]
    broken = frame.copy()
    broken.loc[idx, "label_usable_h1"] = False                                                   # chi mat MOT mau hop le (loi GPT da tai hien o audit cu)
    result = audit.audit_labels(broken, PLAN)
    assert result["h1"]["expected_but_not_usable"] == 1 and audit.total_violations(result) == 1


def test_usable_on_purge_or_across_splits_or_without_target_is_detected():
    frame = synthetic()
    purge = frame.index[frame["split"].isna()][0]
    b1 = frame.copy()
    b1.loc[purge, "label_usable_h1"] = True
    assert audit.audit_labels(b1, PLAN)["h1"]["usable_on_purge_or_outside_rows"] >= 1
    cross = frame.index[(frame["split"] == "train") & frame["has_label_h3"] & (pd.to_datetime(frame["vn_observation_date"]) >= pd.Timestamp("2026-09-08")) & ~frame["label_usable_h3"]]
    assert len(cross) > 0                                                                          # dong train co target sang vung purge/validation (d+3 vuot train_end)
    b2 = frame.copy()
    b2.loc[cross[0], "label_usable_h3"] = True
    assert audit.audit_labels(b2, PLAN)["h3"]["usable_but_not_expected"] >= 1
    nolabel = frame.index[~frame["has_label_h1"]][0]
    b3 = frame.copy()
    b3.loc[nolabel, "label_usable_h1"] = True
    assert _total(b3) > 0


def test_label_values_on_unlabelled_rows_wrong_has_flags_and_wrong_deltas_are_detected():
    frame = synthetic()
    nolabel = frame.index[~frame["has_label_h1"]][0]
    b1 = frame.copy()
    b1.loc[nolabel, "y_price_h1"] = 123.0
    assert audit.audit_labels(b1, PLAN)["h1"]["rows_without_label_but_with_y_values"] >= 1
    b2 = frame.copy()
    b2.loc[nolabel, "has_label_h1"] = True
    assert audit.audit_labels(b2, PLAN)["h1"]["has_label_vs_target_exists_mismatch"] >= 1
    labelled = frame.index[frame["has_label_h1"]][0]
    b3 = frame.copy()
    b3.loc[labelled, "has_label_h1"] = False
    assert audit.audit_labels(b3, PLAN)["h1"]["has_label_vs_target_exists_mismatch"] >= 1
    b4 = frame.copy()
    b4.loc[labelled, "y_delta_h1"] += 1.0
    assert audit.audit_labels(b4, PLAN)["h1"]["y_delta_mismatch"] == 1
    b5 = frame.copy()
    b5.loc[labelled, "y_direction_h1"] = "up" if b5.loc[labelled, "y_direction_h1"] != "up" else "down"
    assert audit.audit_labels(b5, PLAN)["h1"]["y_direction_mismatch"] == 1
    b6 = frame.copy()
    b6.loc[labelled, "y_price_h1"] += 5.0
    assert audit.audit_labels(b6, PLAN)["h1"]["y_price_ne_target_snapshot_price"] == 1


def test_rows_before_train_start_and_split_column_mismatch_are_detected():
    frame = synthetic()
    early = frame.copy()
    early.loc[early.index[0], "vn_observation_date"] = "2026-08-20"                                  # truoc train_start
    assert audit.audit_labels(early, PLAN)["rows_before_train_start_or_after_test_end"] >= 1
    swapped = frame.copy()
    idx = swapped.index[swapped["split"] == "train"][0]
    swapped.loc[idx, "split"] = "validation"
    assert audit.audit_labels(swapped, PLAN)["split_column_vs_date_mismatch"] >= 1

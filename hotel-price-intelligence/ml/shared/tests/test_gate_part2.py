"""Fixture nho cho cac ham thuan cua gate Phan 2 (GPT file 48: P2-M1/M2/M3). Khong DB."""
from __future__ import annotations

import sys
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from analysis import gate_part2 as g  # noqa: E402


def _rows(*items):
    cols = ["item_id", "status", "aid", "checkin_date", "approved_at", "source_code", "run_id", "sel_observed_at", "obs_min", "obs_max"]
    return pd.DataFrame(items, columns=cols)


T = pd.Timestamp


# ----------------------------------------------------------------- P2-M1: observed_at UTC, nua dem, cung ngay voi gio duyet
def test_post_approval_compares_utc_timestamps_not_dates_and_not_run_start():
    approved = T("2026-09-10 10:30:00")                      # 17:30 gio VN cung ngay
    frame = _rows(
        (1, "exact", 1, "2026-09-20", approved, "local_primary", 5, T("2026-09-10 10:29:59"), T("2026-09-10 10:29:59"), T("2026-09-10 10:29:59")),   # 1 giay TRUOC duyet, cung ngay
        (2, "exact", 1, "2026-09-20", approved, "local_primary", 5, T("2026-09-10 10:30:00"), T("2026-09-10 10:30:00"), T("2026-09-10 10:30:00")),   # dung gio duyet => post (>=)
        (3, "alias", 1, "2026-09-20", approved, "vps", 6, T("2026-09-10 23:00:00"), T("2026-09-10 23:00:00"), T("2026-09-10 23:00:00")),
    )
    out = g.add_time_columns(frame)
    assert out["post_approval"].tolist() == [False, True, True]                 # tinh theo ngay thi ca ba deu "cung ngay" => sai; theo timestamp thi item 1 la truoc duyet


def test_cross_midnight_vn_date_uses_plus_7h_for_event_and_for_approval():
    approved = T("2026-09-10 17:30:00")                      # = 00:30 ngay 11 gio VN (UTC va VN khac ngay)
    frame = _rows((1, "exact", 1, "2026-09-20", approved, "local_primary", 5, T("2026-09-10 18:00:00"), T("2026-09-10 18:00:00"), T("2026-09-10 18:00:00")))
    out = g.add_time_columns(frame)
    assert out["event_vn_day"].iloc[0] == T("2026-09-11") and out["approval_vn_day"].iloc[0] == T("2026-09-11")
    assert out["since_days"].iloc[0] == 0                       # cung ngay VN; neu lay DATE(approved UTC)=10 thi se ra 1 (loi file 48: 7.170 item lech)
    assert out["lead"].iloc[0] == 9                             # 20/09 - 11/09


def test_unavailable_and_ambiguous_use_item_min_observed_at_selected_rows_use_selected_observation():
    approved = T("2026-09-10 00:00:00")
    frame = _rows(
        (1, "unavailable", 1, "2026-09-20", approved, "vps", 7, pd.NaT, T("2026-09-12 03:00:00"), T("2026-09-12 03:00:09")),
        (2, "ambiguous", 1, "2026-09-20", approved, "vps", 7, pd.NaT, T("2026-09-12 03:00:00"), T("2026-09-12 03:00:00")),
        (3, "exact", 1, "2026-09-20", approved, "vps", 7, T("2026-09-12 03:00:05"), T("2026-09-12 03:00:01"), T("2026-09-12 03:00:09")),
    )
    out = g.add_time_columns(frame)
    assert out["event_utc"].tolist() == [T("2026-09-12 03:00:00"), T("2026-09-12 03:00:00"), T("2026-09-12 03:00:05")]
    audit = g.item_time_audit(frame)
    assert audit["items_with_multiple_observed_at"] == 2 and audit["max_spread_seconds"] == 9.0
    assert audit["selected_observed_at_differs_from_item_min"] == 1                  # item 3: duoc chon tai :05, MIN la :01


# ----------------------------------------------------------------- P2-M2: ambiguous - dem DISTINCT ngay-series, khong dem item, khong goi "chi vi"
def test_ambiguous_missing_days_counts_distinct_series_days_and_is_not_attributed():
    approved = T("2026-09-01")
    same_day = [(i, "ambiguous", 1, "2026-09-20", approved, "vps", 10 + i, pd.NaT, T("2026-09-12 02:00:00") + pd.Timedelta(minutes=i), T("2026-09-12 02:30:00")) for i in range(3)]
    other_day = [(10, "ambiguous", 1, "2026-09-20", approved, "vps", 20, pd.NaT, T("2026-09-13 02:00:00"), T("2026-09-13 02:00:00"))]
    has_sample_day = [(11, "exact", 2, "2026-09-20", approved, "vps", 21, T("2026-09-12 02:00:00"), T("2026-09-12 02:00:00"), T("2026-09-12 02:00:00")),
                      (12, "ambiguous", 2, "2026-09-20", approved, "vps", 22, pd.NaT, T("2026-09-12 05:00:00"), T("2026-09-12 05:00:00"))]
    post = g.add_time_columns(_rows(*same_day, *other_day, *has_sample_day))
    samples = {(2, T("2026-09-12").date())}                                              # series 2 co sample ngay 12/09
    result = g.ambiguous_missing_days(post, samples)
    assert result["ambiguous_items_post_approval"] == 5
    assert result["ambiguous_items_without_daily_sample_same_assignment_day"] == 4       # 3 item cung ngay + 1 ngay khac (series 1)
    assert result["distinct_ambiguous_associated_missing_series_days"] == 2              # DISTINCT: ngay 12 va ngay 13 cua series 1 (3 item cung ngay chi tinh 1)
    assert result["denominator_candidate_series_days_distinct"] == 3 and result["candidate_series_days_with_a_sample_distinct"] == 1
    assert "KHONG phai thiet hai" in result["note"] and "series_days_lost" not in " ".join(result)


def test_ambiguous_day_uses_observation_day_not_run_day_across_midnight():
    approved = T("2026-09-01")
    # quan sat 17:30 UTC ngay 12 = 00:30 ngay 13 gio VN; sample cung series ngay VN 13 phai duoc coi la "co sample"
    post = g.add_time_columns(_rows((1, "ambiguous", 1, "2026-09-20", approved, "vps", 3, pd.NaT, T("2026-09-12 17:30:00"), T("2026-09-12 17:30:00"))))
    assert g.ambiguous_missing_days(post, {(1, T("2026-09-13").date())})["ambiguous_items_without_daily_sample_same_assignment_day"] == 0
    assert g.ambiguous_missing_days(post, {(1, T("2026-09-12").date())})["ambiguous_items_without_daily_sample_same_assignment_day"] == 1


# ----------------------------------------------------------------- churn: thu tu xac dinh, ba item cung ngay != ba ngay
def test_churn_tail_requires_three_distinct_days_not_three_items_in_one_day():
    approved = T("2026-09-01")
    one_day = [(i, "unavailable", 1, "2026-09-20", approved, "vps", 100 + i, pd.NaT, T("2026-09-12 01:00:00") + pd.Timedelta(minutes=i), T("2026-09-12 01:30:00")) for i in range(3)]
    three_days = [(10 + d, "unavailable", 2, "2026-09-20", approved, "vps", 200 + d, pd.NaT, T(f"2026-09-1{d} 01:00:00"), T(f"2026-09-1{d} 01:00:00")) for d in (2, 3, 4)]
    recovered = [(30, "exact", 3, "2026-09-20", approved, "vps", 300, T("2026-09-12 01:00:00"), T("2026-09-12 01:00:00"), T("2026-09-12 01:00:00"))] + [
        (31 + d, "unavailable", 3, "2026-09-20", approved, "vps", 301 + d, pd.NaT, T(f"2026-09-1{d} 01:00:00"), T(f"2026-09-1{d} 01:00:00")) for d in (3, 4, 5)]
    post = g.add_time_columns(_rows(*one_day, *three_days, *recovered))
    result = g.churn_tail(post, k=3)
    assert result["assignments"] == 3
    assert result["assignments_last3_items_all_unavailable"] == 3                         # ca ba series deu co 3 item cuoi unavailable
    assert result["assignments_last3_distinct_days_all_unavailable"] == 2                 # series 1 chi co 1 ngay => khong tinh; series 2 va 3 co 3 ngay cuoi unavailable


def test_churn_tail_is_independent_of_input_row_order():
    approved = T("2026-09-01")
    rows = [(i, "unavailable" if i >= 2 else "exact", 1, "2026-09-20", approved, "vps", i, T(f"2026-09-1{i} 01:00:00") if i < 2 else pd.NaT,
             T(f"2026-09-1{i} 01:00:00"), T(f"2026-09-1{i} 01:00:00")) for i in range(5)]
    a = g.churn_tail(g.add_time_columns(_rows(*rows)))
    b = g.churn_tail(g.add_time_columns(_rows(*reversed(rows))))
    assert a == b and a["assignments_last3_items_all_unavailable"] == 1


# ----------------------------------------------------------------- P2-M3: Accuracy@20% mau so = y that
def _label_frame(current, y):
    frame = pd.DataFrame({"current_price": np.asarray(current, float), "y_price_h1": np.asarray(y, float), "split": "train",
                          "label_usable_h1": True, "hotel_seen_in_train_h1": True})
    frame["y_pct_change_h1"] = (frame["y_price_h1"] - frame["current_price"]) / frame["current_price"]
    return frame


def test_persistence_accuracy_uses_y_as_denominator_not_current_price():
    # current=100: y=80 => |100-80|/80 = 25% (FAIL theo mau so y) nhung |y-cur|/cur = 20% (PASS theo mau so cu)
    # y=125 => |100-125|/125 = 20% (PASS) nhung |y-cur|/cur = 25% (FAIL theo mau so cu)
    frame = _label_frame([100, 100, 100], [80, 125, 100])
    result = g.persistence_accuracy(frame, 1)
    assert result["train"]["accuracy_at_tol"] == pytest.approx(2 / 3)                    # y=125 PASS, y=100 PASS, y=80 FAIL
    old_definition = float((frame["y_pct_change_h1"].abs() <= 0.20).mean())
    assert old_definition == pytest.approx(2 / 3) and old_definition == pytest.approx(result["train"]["accuracy_at_tol"], abs=1e-5)   # trung ve SO LUONG nhung khac ve DONG nao pass:
    passes_old = (frame["y_pct_change_h1"].abs() <= 0.20).tolist()
    passes_new = ((frame["current_price"] - frame["y_price_h1"]).abs() / frame["y_price_h1"] <= 0.20).tolist()
    assert passes_old != passes_new and passes_new == [False, True, True]


def test_persistence_accuracy_primary_only_filters_unseen_hotels_in_validation_and_test_only():
    frame = pd.concat([_label_frame([100] * 4, [100, 100, 100, 100]).assign(split="train"),
                       _label_frame([100] * 4, [100, 100, 300, 300]).assign(split="validation", hotel_seen_in_train_h1=[True, True, False, False])], ignore_index=True)
    all_rows = g.persistence_accuracy(frame, 1, primary_only=False)
    primary = g.persistence_accuracy(frame, 1, primary_only=True)
    assert all_rows["validation"]["n"] == 4 and primary["validation"]["n"] == 2 and primary["train"]["n"] == 4      # train khong loc
    assert all_rows["validation"]["accuracy_at_tol"] == pytest.approx(0.5) and primary["validation"]["accuracy_at_tol"] == 1.0
    assert primary["all_splits"]["n"] == 6


# ----------------------------------------------------------------- 3 lop: confusion matrix, balanced accuracy, baseline hang 'stable'
def test_class_metrics_reports_confusion_balanced_accuracy_and_constant_stable_baseline():
    true = np.array(["stable"] * 8 + ["up", "down"])
    pred = np.array(["stable"] * 8 + ["stable", "down"])
    result = g.class_metrics(true, pred)
    assert result["confusion_matrix_rows_true_cols_pred"]["up"] == {"down": 0, "stable": 1, "up": 0}
    assert result["accuracy"] == pytest.approx(0.9) and result["constant_stable_baseline_accuracy"] == pytest.approx(0.8)
    assert result["recall"]["stable"] == 1.0 and result["recall"]["up"] == 0.0 and result["recall"]["down"] == 1.0
    assert result["balanced_accuracy"] == pytest.approx(2 / 3)
    assert result["prevalence_true"]["stable"] == pytest.approx(0.8)
    assert list(g.classify_pct(np.array([0.05, -0.05, 0.01, -0.02, 0.02]))) == ["up", "down", "stable", "stable", "stable"]

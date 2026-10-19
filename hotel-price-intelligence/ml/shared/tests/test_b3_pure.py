"""Logic thuan cua B3: chon bien split, feature lich su, lich, dien tich phong, hash noi dung."""
from __future__ import annotations

import datetime as dt

import numpy as np
import pandas as pd
import pytest

from dataset_builder import config as cfg
from dataset_builder.calendar_features import CalendarFeatures
from dataset_builder.export import content_sha256
from dataset_builder.features import history_features_for_series, lead_time_bucket, parse_room_area_m2
from dataset_builder.splitter import SplitInfeasible, plan_split, required_windows

D = dt.date
POLICY = cfg.split_selection_policy()


def _always_pass(plan, horizon):
    """Evaluate gia: moi ung vien lich-kha-thi deu 'pass' - chi de test hinh hoc cua cua so (hanh vi gate that o test_split_gates.py)."""
    return {"status": "primary_eligible", "failed_gates": [], "splits": {}}


def _plan(span: int, purge: int = 14, evaluate=_always_pass):
    first = D(2026, 8, 21)
    return plan_split(first, first + dt.timedelta(days=span - 1), policy=POLICY, purge_gap_days=purge, evaluate=evaluate), first


def test_required_windows_follow_registered_gates():
    assert required_windows(POLICY, 1) == (29, 8, 8)
    assert required_windows(POLICY, 7) == (35, 14, 14)
    assert required_windows(POLICY, 14) == (28, 21, 21)          # h14: 14/7/7 ngay eligible + 14


@pytest.mark.parametrize("span,horizon", [(120, 14), (98, 14), (97, 7), (91, 7), (90, 3), (79, 3), (78, 1), (73, 1)])
def test_gate_driven_choice_picks_largest_feasible_horizon(span, horizon):
    plan, first = _plan(span)
    assert plan.feasible_horizon == horizon and plan.policy_path == f"gate_driven:H={horizon}"
    train_len, val_len, test_len = required_windows(POLICY, horizon)
    assert (plan.test_end - plan.test_start).days + 1 == test_len
    assert (plan.validation_end - plan.validation_start).days + 1 == val_len
    assert (plan.train_end - plan.train_start).days + 1 >= train_len
    assert (plan.validation_start - plan.train_end).days - 1 == 14      # purge 1 dung 14 ngay
    assert (plan.test_start - plan.validation_end).days - 1 == 14       # purge 2
    assert plan.test_end == first + dt.timedelta(days=span - 1)         # test la cua so CUOI


def test_fallback_when_no_horizon_feasible_and_infeasible_when_too_short():
    plan, _ = _plan(45)   # 45 ngay: moi ung vien deu khong du ngay lich, nen evaluate khong bao gio duoc goi
    assert plan.policy_path == "fallback_ratio" and plan.feasible_horizon is None
    assert (plan.validation_start - plan.train_end).days - 1 == 14 and (plan.test_start - plan.validation_end).days - 1 == 14
    assert (plan.train_end - plan.train_start).days + 1 == 11 and (plan.test_end - plan.test_start).days + 1 == 3
    with pytest.raises(SplitInfeasible):
        _plan(30)                                                       # 30 - 28 = 2 < 3


def test_split_of_assigns_purge_as_none():
    plan, first = _plan(120)
    assert plan.split_of(first) == "train" and plan.split_of(plan.train_end) == "train"
    assert plan.split_of(plan.train_end + dt.timedelta(days=1)) is None
    assert plan.split_of(plan.train_end + dt.timedelta(days=14)) is None
    assert plan.split_of(plan.validation_start) == "validation" and plan.split_of(plan.validation_end) == "validation"
    assert plan.split_of(plan.validation_end + dt.timedelta(days=14)) is None
    assert plan.split_of(plan.test_start) == "test" and plan.split_of(plan.test_end) == "test"


def test_history_features_are_causal_and_null_without_history():
    days = np.array([100, 101, 103, 107, 108, 120], dtype=np.int64)
    prices = np.array([10.0, 12.0, 12.0, 15.0, 15.0, 30.0])
    f = history_features_for_series(days, prices, cold_start_max_history=2)
    # mau dau: khong co lich su -> NULL, count 0
    assert np.isnan(f["price_lag_1"][0]) and f["history_observation_count"][0] == 0 and np.isnan(f["history_span_days"][0])
    # ngay 101: lag1 = gia ngay 100
    assert f["price_lag_1"][1] == 10.0 and f["price_velocity"][1] == pytest.approx(0.2)
    # ngay 108: lag1 = ngay 107 (15), lag7 = ngay 101 (12), lag3 khong ton tai (ngay 105 thieu)
    assert f["price_lag_1"][4] == 15.0 and f["price_lag_7"][4] == 12.0 and np.isnan(f["price_lag_3"][4])
    # rolling 7 ngay truoc ngay 108: ngay 101,103,107 -> (12+12+15)/3
    assert f["price_rolling_mean_7"][4] == pytest.approx(13.0)
    assert f["price_min_trailing_14"][4] == 10.0 and f["price_max_trailing_14"][4] == 15.0
    # ngay 120: cua so 14 ngay truoc = ngay 106..119 -> chi ngay 107,108 (15,15); khong cham gia sau (khong ro ri tuong lai)
    assert f["price_rolling_mean_14"][5] == pytest.approx(15.0) and f["price_rolling_std_7"][5] != f["price_rolling_std_7"][5]  # NaN: <2 diem trong 7 ngay
    assert f["price_vs_own_mean_30"][5] == pytest.approx(30.0 / np.mean(prices[:5]))
    # days_since_last_price_change tinh ca gia HIEN TAI (biet luc du bao): ngay 108 gia = ngay 107 -> 1 ngay ke tu lan doi 12->15 o 107;
    # ngay 120 gia doi (15->30) so voi snapshot lien truoc -> 0
    assert f["days_since_last_price_change"][4] == 108 - 107 and f["days_since_last_price_change"][5] == 0
    assert f["days_since_last_price_change"][2] == 103 - 101                      # 12->12 khong doi; lan doi gan nhat o 101 (10->12)
    assert f["history_observation_count"].tolist() == [0, 1, 2, 3, 4, 5] and f["history_span_days"][5] == 20


def test_history_does_not_leak_when_future_prices_change():
    days = np.arange(1, 11, dtype=np.int64)
    base = np.linspace(100, 190, 10)
    changed = base.copy()
    changed[7:] = 999.0                                               # doi gia SAU ngay 7
    a = history_features_for_series(days, base, cold_start_max_history=2)
    b = history_features_for_series(days, changed, cold_start_max_history=2)
    for name in ("price_lag_1", "price_lag_3", "price_rolling_mean_7", "price_velocity", "price_min_trailing_14"):
        assert np.allclose(a[name][:7], b[name][:7], equal_nan=True), name


def test_parse_room_area_and_lead_bucket():
    assert parse_room_area_m2("25 m²") == 25.0 and parse_room_area_m2("20 m2") == 20.0 and parse_room_area_m2("269 ft²") == pytest.approx(24.99, abs=0.01)
    assert parse_room_area_m2(None) is None and parse_room_area_m2("khong ro") is None
    assert [lead_time_bucket(x) for x in (0, 2, 3, 6, 7, 13, 14, 29, 30, 59, 60, 400)] == [
        "lt3", "lt3", "3-7", "3-7", "7-14", "7-14", "14-30", "14-30", "30-60", "30-60", "gt60", "gt60"]


def test_calendar_features_city_scope_and_eve(tmp_path):
    csv = ("holiday_date,event_code,name,event_type,scope,city,is_tet,status,source_url\n"
           "2026-09-02,national_day,QK,public_holiday,national,,0,confirmed,u\n"
           "2026-09-14,fest1,LH,festival,city,Phú Quốc,0,confirmed,u\n"
           "2027-02-17,tet,Tet,public_holiday,national,,1,confirmed,u\n")
    path = tmp_path / "h.csv"
    path.write_text(csv, encoding="utf-8")
    cal = CalendarFeatures.load(path)
    f = cal.features(D(2026, 9, 2), "Hà Nội")
    assert f["is_public_holiday"] and not f["is_festival_period"] and f["days_to_nearest_holiday"] == 0 and f["is_weekend"] is False
    assert cal.features(D(2026, 9, 1), "Hà Nội")["is_holiday_eve"] is True
    assert cal.features(D(2026, 9, 14), "Phú Quốc")["is_festival_period"] is True
    assert cal.features(D(2026, 9, 14), "Hà Nội")["is_festival_period"] is False       # su kien theo thanh pho
    assert cal.features(D(2027, 2, 17), "Hà Nội")["is_tet_period"] is True
    assert cal.features(D(2026, 9, 4), "Hà Nội")["is_weekend"] is True                  # thu Sau
    assert cal.features(D(2026, 9, 5), "Hà Nội")["is_weekend"] is True                  # thu Bay
    assert cal.features(D(2026, 9, 6), "Hà Nội")["is_weekend"] is False                 # chu nhat


def test_content_hash_ignores_technical_id_and_row_order():
    base = pd.DataFrame({"hotel_id": ["a", "b"], "checkin_date": [D(2026, 9, 20)] * 2, "canonical_series_id": ["s1", "s2"],
                         "vn_observation_date": [D(2026, 9, 5)] * 2, "price": [1.0, 2.0], "warehouse_record_id": [10, 20]})
    other = base.iloc[::-1].copy()
    other["warehouse_record_id"] = [999, 888]
    assert content_sha256(base) == content_sha256(other)
    changed = base.copy()
    changed.loc[0, "price"] = 1.5
    assert content_sha256(base) != content_sha256(changed)

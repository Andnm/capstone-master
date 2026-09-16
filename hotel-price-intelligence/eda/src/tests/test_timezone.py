"""Test cho `timezone.py` - UTC -> ngay Viet Nam (EDA_CURATED_PLAN.md muc 10.1)."""
from __future__ import annotations

import datetime as dt

import pandas as pd
import pytest

from timezone import lead_time_days, lead_time_days_series, to_vn_date, to_vn_date_series


def test_to_vn_date_cong_dung_7_gio():
    # 23:30 UTC 15/09 -> 06:30 VN 16/09 (qua ngay)
    assert to_vn_date(dt.datetime(2026, 9, 15, 23, 30)) == dt.date(2026, 9, 16)
    # 16:59 UTC 15/09 -> 23:59 VN 15/09 (chua qua ngay)
    assert to_vn_date(dt.datetime(2026, 9, 15, 16, 59)) == dt.date(2026, 9, 15)
    # 17:00 UTC 15/09 -> 00:00 VN 16/09 (dung ranh gioi)
    assert to_vn_date(dt.datetime(2026, 9, 15, 17, 0)) == dt.date(2026, 9, 16)


def test_to_vn_date_tu_choi_tz_aware():
    aware = dt.datetime(2026, 9, 15, 12, 0, tzinfo=dt.timezone.utc)
    with pytest.raises(ValueError, match="NAIVE"):
        to_vn_date(aware)


def test_to_vn_date_tu_choi_sai_kieu():
    with pytest.raises(TypeError):
        to_vn_date("2026-09-15")  # type: ignore[arg-type]


def test_to_vn_date_series_khop_ham_don_le():
    values = [dt.datetime(2026, 9, 15, 23, 30), dt.datetime(2026, 9, 15, 16, 59), dt.datetime(2026, 9, 15, 17, 0)]
    series = pd.Series(values)
    result = to_vn_date_series(series)
    assert list(result) == [to_vn_date(v) for v in values]


def test_to_vn_date_series_tu_choi_tz_aware():
    series = pd.Series(pd.to_datetime(["2026-09-15 12:00:00"])).dt.tz_localize("UTC")
    with pytest.raises(ValueError, match="tz-aware"):
        to_vn_date_series(series)


def test_to_vn_date_series_tu_choi_null():
    series = pd.Series(pd.to_datetime(["2026-09-15 12:00:00", None]))
    with pytest.raises(ValueError, match="NULL"):
        to_vn_date_series(series)


def test_to_vn_date_series_tu_choi_khong_phai_datetime():
    with pytest.raises(TypeError):
        to_vn_date_series(pd.Series([1, 2, 3]))


def test_lead_time_days_doc_lap_voi_cot_da_luu():
    # checkin 20/08, quan sat 23:30 UTC 15/08 -> 06:30 VN 16/08 -> lead_time = 20-16 = 4
    assert lead_time_days(dt.date(2026, 8, 20), dt.datetime(2026, 8, 15, 23, 30)) == 4
    # lead_time = 0 hop le (quan sat dung ngay check-in, muc 4.2.a cua CLAUDE.md)
    assert lead_time_days(dt.date(2026, 8, 20), dt.datetime(2026, 8, 19, 20, 0)) == 0


def test_lead_time_days_series_khop_ham_don_le():
    checkin = pd.Series([dt.date(2026, 8, 20), dt.date(2026, 9, 5)])
    observed = pd.Series([dt.datetime(2026, 8, 15, 23, 30), dt.datetime(2026, 9, 1, 10, 0)])
    result = lead_time_days_series(checkin, observed)
    expected = [lead_time_days(c, o) for c, o in zip(checkin, observed)]
    assert list(result) == expected
    assert result.dtype == "int64"

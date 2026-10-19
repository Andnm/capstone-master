"""Tich hop: `build_feature_frame` THAT (khong DB: `fetch_all` duoc thay bang hang gia lap kieu MySQL) giu NULL cua hai co la `<NA>` va chuyen qua encoder huan luyen thanh NaN.
Tep nay import `dataset_builder.*` that (module `env` nap `backend/.env` luc import, nhu cac test builder khac); phan THUAN o `test_nullable_flags.py`."""
from __future__ import annotations

import datetime as dt
from decimal import Decimal

import numpy as np
import pandas as pd
import pytest

from dataset_builder import features as F
from dataset_builder.feature_spec import HORIZONS


class _Calendar:
    def features(self, checkin, city):
        return {"day_of_week": checkin.weekday(), "days_to_nearest_holiday": 4}


class _Conn:
    def commit(self):
        pass


def _row(record_id, *, breakfast, cancellation, occupancy=2, series="s1", day=1):
    row = {"warehouse_record_id": record_id, "prediction_time": dt.datetime(2026, 9, day, 3), "vn_observation_date": dt.date(2026, 9, day), "split": "train",
           "hotel_id": "h", "checkin_date": dt.date(2026, 9, 20), "canonical_series_id": series, "city": "Hà Nội", "max_occupancy": occupancy, "room_area": "20 m²",
           "breakfast_included": breakfast, "free_cancellation": cancellation, "current_price": Decimal("1000000.00"), "rooms_left": None, "discount_percent": None}
    row.update({f"lsr_h{k}": None for k in HORIZONS})
    return row


def _frame(monkeypatch, rows):
    def fake_fetch_all(conn, sql, params=()):
        if "ml_item_reference_matches" in sql:
            return [{"selected_record_id": r["warehouse_record_id"], "match_status": "exact"} for r in rows]
        return [dict(r) for r in rows]
    monkeypatch.setattr(F, "fetch_all", fake_fetch_all)
    config = {"feature_config": {"cold_start_max_history": 2}, "label_config": {"direction_threshold": 0.02}}
    return F.build_feature_frame(_Conn(), dataset_version="ds_test", config=config, calendar=_Calendar())


def test_build_feature_frame_keeps_null_flags_as_na_with_mysql_style_int_and_none_rows(monkeypatch):
    rows = [_row(1, breakfast=1, cancellation=0, day=1), _row(2, breakfast=0, cancellation=None, day=2), _row(3, breakfast=None, cancellation=1, day=3), _row(4, breakfast=1, cancellation=None, day=4)]
    frame = _frame(monkeypatch, rows)
    assert str(frame["free_cancellation"].dtype) == "boolean" and str(frame["breakfast_included"].dtype) == "boolean"
    assert frame["free_cancellation"].isna().tolist() == [False, True, False, True]            # NULL -> <NA> (truoc day: True)
    assert frame["free_cancellation"].tolist()[0] is False and frame["free_cancellation"].tolist()[2] is True
    assert frame["breakfast_included"].isna().tolist() == [False, False, True, False]
    assert frame["breakfast_included"].tolist()[0] is True and frame["breakfast_included"].tolist()[1] is False


def test_the_old_lambda_would_have_failed_this_integration_test(monkeypatch):
    monkeypatch.setattr(F, "to_nullable_boolean", lambda series, column: series.map(lambda v: None if v is None else bool(v)).astype("boolean"))     # cach cu
    rows = [_row(1, breakfast=1, cancellation=0, day=1), _row(2, breakfast=0, cancellation=None, day=2)]
    frame = _frame(monkeypatch, rows)
    assert frame["free_cancellation"].isna().tolist() == [False, False]                        # cach cu: NULL bi mat thanh True => test that su phat hien duoc
    assert frame["free_cancellation"].tolist() == [False, True]


def test_unknown_domain_in_a_flag_column_fails_the_build_loudly(monkeypatch):
    with pytest.raises(ValueError, match="free_cancellation"):
        _frame(monkeypatch, [_row(1, breakfast=1, cancellation=2)])


def test_all_known_flags_are_unchanged_by_the_fix(monkeypatch):
    frame = _frame(monkeypatch, [_row(1, breakfast=1, cancellation=0, day=1), _row(2, breakfast=0, cancellation=1, day=2)])
    assert frame["free_cancellation"].tolist() == [False, True] and frame["breakfast_included"].tolist() == [True, False] and not frame[["free_cancellation", "breakfast_included"]].isna().any().any()


def test_training_encoder_turns_the_repaired_flags_into_nan_not_one(monkeypatch):
    from training.encoder import TreeEncoder
    rows = [_row(1, breakfast=1, cancellation=0, day=1), _row(2, breakfast=0, cancellation=None, day=2)]
    frame = _frame(monkeypatch, rows)
    encoded = TreeEncoder(["free_cancellation", "breakfast_included", "max_occupancy"]).fit().transform(frame)
    assert encoded["free_cancellation"].tolist()[0] == 0.0 and np.isnan(encoded["free_cancellation"].tolist()[1])
    assert encoded["breakfast_included"].tolist() == [1.0, 0.0]

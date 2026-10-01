"""Test cho `contracts.py` - BOOLEAN coercion tap trung + cam cot reference-operational
(EDA_CURATED_PLAN.md muc 5 quy tac 11-12, muc 10.1)."""
from __future__ import annotations

import pandas as pd
import pytest

from contracts import (
    BOOLEAN_SCHEMA,
    FORBIDDEN_REFERENCE_COLUMNS,
    BooleanContractError,
    assert_columns_present,
    assert_no_forbidden_reference_columns,
    assert_no_null,
    coerce_boolean_columns,
    coerce_boolean_series,
)


# --- coerce_boolean_series: mo phong DUNG kieu du lieu mysql-connector tra ve (int 0/1, None cho NULL)
def test_coerce_int01_thanh_boolean():
    out = coerce_boolean_series(pd.Series([1, 0, 1, 0], dtype=object), nullable=False)
    assert out.tolist() == [True, False, True, False]
    assert out.dtype == "boolean"


def test_coerce_giu_null_khi_nullable():
    out = coerce_boolean_series(pd.Series([1, None, 0], dtype=object), nullable=True)
    assert out.tolist()[0] is True and out.tolist()[2] is False and pd.isna(out.tolist()[1])


def test_coerce_fail_khi_null_tren_cot_bat_buoc():
    with pytest.raises(BooleanContractError, match="is_sold_out"):
        coerce_boolean_series(pd.Series([1, None], dtype=object), nullable=False,
                              table="price_observations", column="is_sold_out")


@pytest.mark.parametrize("bad_value", [2, -1, "yes", "1.0", 1.5])
def test_coerce_fail_khi_gia_tri_ngoai_hop_dong(bad_value):
    with pytest.raises(BooleanContractError):
        coerce_boolean_series(pd.Series([1, 0, bad_value], dtype=object), nullable=False)


def test_coerce_idempotent_tren_ket_qua_da_coerce():
    once = coerce_boolean_series(pd.Series([1, 0, None], dtype=object), nullable=True)
    twice = coerce_boolean_series(once, nullable=True)
    assert once.tolist() == twice.tolist()
    assert twice.dtype == "boolean"


def test_coerce_chap_nhan_bool_python_that():
    out = coerce_boolean_series(pd.Series([True, False, True]), nullable=False)
    assert out.tolist() == [True, False, True]


# --- coerce_boolean_columns: theo dung dang ky BOOLEAN_SCHEMA, khong doan mo
def test_coerce_columns_chi_dong_cot_da_dang_ky():
    df = pd.DataFrame({
        "is_sold_out": [1, 0], "breakfast_included": [1, None],
        "hotel_id": ["h1", "h2"],  # khong phai BOOLEAN - phai giu nguyen
    })
    out = coerce_boolean_columns(df, "price_observations")
    assert out["is_sold_out"].dtype == "boolean"
    assert out["breakfast_included"].dtype == "boolean"
    assert out["hotel_id"].tolist() == ["h1", "h2"]  # khong bi dong


def test_coerce_columns_khong_sua_in_place():
    df = pd.DataFrame({"is_sold_out": [1, 0]})
    out = coerce_boolean_columns(df, "price_observations")
    assert df["is_sold_out"].dtype != "boolean"  # ban goc khong doi
    assert out["is_sold_out"].dtype == "boolean"


def test_coerce_columns_bang_chua_dang_ky_thi_fail():
    with pytest.raises(KeyError):
        coerce_boolean_columns(pd.DataFrame({"x": [1]}), "bang_khong_ton_tai")


def test_boolean_schema_khop_dung_ddl_that():
    """Doi chieu voi app/database/setup.sql + app/warehouse/etl_ddl.py (khong doan)."""
    assert dict(BOOLEAN_SCHEMA["price_observations"])["is_sold_out"] is False  # NOT NULL
    assert dict(BOOLEAN_SCHEMA["price_observations"])["breakfast_included"] is True  # nullable
    assert set(dict(BOOLEAN_SCHEMA["etl_item_map"])) == {
        "include_reference", "include_eda_raw", "include_eda_main", "include_training"}


# --- cam cot reference-operational (muc 5 quy tac 12)
def test_assert_no_forbidden_reference_columns_bat_duoc():
    df = pd.DataFrame({"hotel_id": ["h1"], "is_reference_room": [False]})
    with pytest.raises(ValueError, match="is_reference_room"):
        assert_no_forbidden_reference_columns(df, context="test")


def test_assert_no_forbidden_reference_columns_qua_khi_sach():
    df = pd.DataFrame({"hotel_id": ["h1"], "canonical_room_key": ["abc"]})
    assert_no_forbidden_reference_columns(df, context="test")  # khong raise


def test_forbidden_reference_columns_dung_4_cot_that():
    assert set(FORBIDDEN_REFERENCE_COLUMNS) == {
        "is_reference_room", "reference_definition_id", "reference_match_status", "reference_match_score"}


# --- helper assertion khac
def test_assert_columns_present():
    df = pd.DataFrame({"a": [1]})
    assert_columns_present(df, ("a",), context="t")
    with pytest.raises(ValueError, match="thieu cot"):
        assert_columns_present(df, ("a", "b"), context="t")


def test_assert_no_null():
    df = pd.DataFrame({"a": [1, 2], "b": [1, None]})
    assert_no_null(df, ("a",), context="t")
    with pytest.raises(ValueError, match="NULL"):
        assert_no_null(df, ("b",), context="t")

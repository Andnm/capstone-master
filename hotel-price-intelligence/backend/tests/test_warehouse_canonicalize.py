"""Canonical key (muc 9) - pure.

`test_bool_*` tai hien bay da lam lech 100% `rate_plan_key` (MySQL tra 1/0, scraper ghi True/False).
`test_sold_out_*` khoa phuong an P-A (GPT chot file 08, theo input user file 07d): quyet dinh sentinel
dua TUONG MINH vao `is_sold_out`, khong suy tu payload.
"""
import datetime as dt

import pytest

from app.scraper.reference import rate_plan_key, room_identity_key
from app.warehouse.canonicalize import (
    EMPTY_RATE_KEY,
    EMPTY_ROOM_KEY,
    canonical_series_id,
    compute_canonical_keys,
    is_empty_room_identity,
)
from app.warehouse.errors import CanonicalizationError

BASE = {
    "record_id": 1, "hotel_id": "roma", "checkin_date": dt.date(2026, 9, 20), "is_sold_out": 0,
    "room_type_raw": "Phòng Deluxe Giường Đôi", "max_occupancy": 2, "bed_config": "1 giường đôi lớn",
    "room_area": "25 m²", "cancellation_policy": "Hủy miễn phí trước 25 tháng 9, 2026",
}
EMPTY_ROOM = {"room_type_raw": None, "max_occupancy": None, "bed_config": None, "room_area": None,
              "breakfast_included": None, "free_cancellation": None, "cancellation_policy": None}


def row(**overrides):
    return {**BASE, "breakfast_included": 1, "free_cancellation": 0, **overrides}


# ---------------------------------------------------------------- bay kieu boolean
def test_bool_int_va_bool_cho_cung_key():
    a = compute_canonical_keys(row(breakfast_included=1, free_cancellation=0, is_sold_out=0))
    b = compute_canonical_keys(row(breakfast_included=True, free_cancellation=False, is_sold_out=False))
    assert (a.canonical_rate_key, a.canonical_series_id) == (b.canonical_rate_key, b.canonical_series_id)


def test_key_warehouse_trung_key_operational():
    operational = rate_plan_key({**BASE, "breakfast_included": True, "free_cancellation": True})
    assert compute_canonical_keys(row(breakfast_included=1, free_cancellation=1)).canonical_rate_key == operational


def test_bay_la_that_int_tho_cho_key_khac():
    assert rate_plan_key({**BASE, "breakfast_included": 1, "free_cancellation": 1}) != rate_plan_key(
        {**BASE, "breakfast_included": True, "free_cancellation": True})


def test_none_khong_bi_bien_thanh_false():
    assert compute_canonical_keys(row(breakfast_included=None)).canonical_rate_key != compute_canonical_keys(
        row(breakfast_included=False)).canonical_rate_key


@pytest.mark.parametrize("column,value", [
    ("breakfast_included", 2), ("breakfast_included", "1"), ("free_cancellation", -1),
    ("is_sold_out", 2), ("is_sold_out", "0"),
])
def test_boolean_ngoai_tap_hop_le_thi_fail(column, value):
    """bool(2) va bool("0") deu la True trong Python - khong duoc am tham chap nhan."""
    with pytest.raises(CanonicalizationError, match="khong thuoc"):
        compute_canonical_keys(row(**{column: value}))


def test_room_key_khop_room_identity_key():
    assert compute_canonical_keys(row()).canonical_room_key == room_identity_key(BASE)


# ---------------------------------------------------------------- series id
def test_series_id_du_4_thanh_phan_va_on_dinh():
    keys = compute_canonical_keys(row())
    assert keys.canonical_series_id == canonical_series_id(
        "roma", dt.date(2026, 9, 20), keys.canonical_room_key, keys.canonical_rate_key)
    assert canonical_series_id("roma", dt.date(2026, 9, 20), "a", "b") == canonical_series_id(
        "roma", "2026-09-20", "a", "b")
    assert canonical_series_id("roma", dt.date(2026, 9, 20), "a", "b") != canonical_series_id(
        "sen", dt.date(2026, 9, 20), "a", "b")
    for value in (keys.canonical_room_key, keys.canonical_rate_key, keys.canonical_series_id):
        assert len(value) == 64 and all(c in "0123456789abcdef" for c in value)


# ---------------------------------------------------------------- sold-out (P-A)
@pytest.mark.parametrize("flag", [True, 1])
def test_sold_out_luon_nhan_key_rong(flag):
    keys = compute_canonical_keys({**BASE, **EMPTY_ROOM, "is_sold_out": flag})
    assert (keys.canonical_room_key, keys.canonical_rate_key) == (EMPTY_ROOM_KEY, EMPTY_RATE_KEY)
    assert is_empty_room_identity(keys) and keys.is_sold_out


def test_sold_out_con_sot_text_phong_van_bi_ep_key_rong():
    """GPT 08: sold-out tuong lai con sot text o field phong KHONG duoc nhan key phong gia."""
    keys = compute_canonical_keys(row(is_sold_out=1))
    assert is_empty_room_identity(keys)


def test_khong_sold_out_ma_mat_room_identity_thi_fail():
    """Nghi loi parser - khong duoc de trong giong sold-out."""
    with pytest.raises(CanonicalizationError, match="KHONG co room identity"):
        compute_canonical_keys({**BASE, **EMPTY_ROOM, "is_sold_out": 0})


def test_thieu_is_sold_out_thi_fail():
    data = row()
    del data["is_sold_out"]
    with pytest.raises(CanonicalizationError, match="is_sold_out"):
        compute_canonical_keys(data)


def test_sold_out_2_hotel_chung_key_phong_nhung_khac_series():
    a = compute_canonical_keys({**BASE, **EMPTY_ROOM, "is_sold_out": 1, "hotel_id": "roma"})
    b = compute_canonical_keys({**BASE, **EMPTY_ROOM, "is_sold_out": 1, "hotel_id": "sen"})
    assert a.canonical_room_key == b.canonical_room_key
    assert a.canonical_series_id != b.canonical_series_id


def test_phong_that_khong_bao_gio_trung_key_rong():
    assert compute_canonical_keys(row()).canonical_room_key != EMPTY_ROOM_KEY

"""Canonical key (muc 9) - pure.

Test quan trong nhat o day la nhom `test_bool_*`: chung tai hien DUNG bay da lam lech 100%
`rate_plan_key` khi doc tu MySQL (int 1/0) so voi luc scraper ghi (bool True/False).
Xem discuss/warehouse-build-implementation/07b.
"""
import datetime as dt

from app.scraper.reference import rate_plan_key, room_identity_key
from app.warehouse.canonicalize import (
    EMPTY_RATE_KEY,
    EMPTY_ROOM_KEY,
    canonical_series_id,
    compute_canonical_keys,
    is_empty_room_identity,
)

BASE = {
    "record_id": 1,
    "hotel_id": "roma",
    "checkin_date": dt.date(2026, 9, 20),
    "room_type_raw": "Phòng Deluxe Giường Đôi",
    "max_occupancy": 2,
    "bed_config": "1 giường đôi lớn",
    "room_area": "25 m²",
    "cancellation_policy": "Hủy miễn phí trước 25 tháng 9, 2026",
}


def with_flags(breakfast, cancellation):
    return {**BASE, "breakfast_included": breakfast, "free_cancellation": cancellation}


# ======================================================================================
# Bay kieu boolean
# ======================================================================================
def test_bool_int_1_va_true_cho_cung_key():
    """MySQL tra 1/0, scraper ghi True/False - phai ra CUNG key sau chuan hoa."""
    from_mysql = compute_canonical_keys(with_flags(1, 0))
    from_python = compute_canonical_keys(with_flags(True, False))
    assert from_mysql.canonical_rate_key == from_python.canonical_rate_key
    assert from_mysql.canonical_series_id == from_python.canonical_series_id


def test_bool_chuan_hoa_tai_tao_dung_key_operational():
    """Key warehouse phai TRUNG key ma scraper da ghi, khong chi 'deterministic la duoc'."""
    operational = rate_plan_key({**BASE, "breakfast_included": True, "free_cancellation": True})
    warehouse = compute_canonical_keys(with_flags(1, 1)).canonical_rate_key
    assert warehouse == operational


def test_khong_chuan_hoa_thi_that_su_khac_nhau():
    """Chung minh bay la THAT: ep tho int vao rate_plan_key() cho key khac."""
    raw_int = rate_plan_key({**BASE, "breakfast_included": 1, "free_cancellation": 1})
    raw_bool = rate_plan_key({**BASE, "breakfast_included": True, "free_cancellation": True})
    assert raw_int != raw_bool


def test_none_van_la_none_khong_bien_thanh_false():
    """NULL trong DB nghia la 'nguon khong noi ro' - khong duoc bien thanh False."""
    as_none = compute_canonical_keys(with_flags(None, None)).canonical_rate_key
    as_false = compute_canonical_keys(with_flags(False, False)).canonical_rate_key
    assert as_none != as_false


def test_room_key_khong_dinh_bay_nhung_van_duoc_chuan_hoa():
    """room_identity_key khong co boolean trong payload - chi khop la du."""
    assert compute_canonical_keys(with_flags(1, 0)).canonical_room_key == room_identity_key(BASE)


# ======================================================================================
# series id
# ======================================================================================
def test_series_id_phu_thuoc_du_4_thanh_phan():
    keys = compute_canonical_keys(with_flags(True, True))
    assert keys.canonical_series_id == canonical_series_id(
        "roma", dt.date(2026, 9, 20), keys.canonical_room_key, keys.canonical_rate_key
    )
    assert canonical_series_id("roma", dt.date(2026, 9, 20), "a", "b") != canonical_series_id(
        "roma", dt.date(2026, 9, 21), "a", "b"
    )
    assert canonical_series_id("roma", dt.date(2026, 9, 20), "a", "b") != canonical_series_id(
        "sen", dt.date(2026, 9, 20), "a", "b"
    )


def test_series_id_on_dinh_voi_date_vs_chuoi_date():
    """Driver co the tra date hoac chuoi - canonical_json phai cho cung ket qua."""
    assert canonical_series_id("roma", dt.date(2026, 9, 20), "a", "b") == canonical_series_id(
        "roma", "2026-09-20", "a", "b"
    )


def test_moi_key_deu_la_64_hex():
    keys = compute_canonical_keys(with_flags(True, False))
    for value in (keys.canonical_room_key, keys.canonical_rate_key, keys.canonical_series_id):
        assert len(value) == 64 and all(c in "0123456789abcdef" for c in value)


# ======================================================================================
# Sentinel sold-out (khong co room identity)
# ======================================================================================
def test_sold_out_sentinel_van_sinh_duoc_key_xac_dinh():
    """11.251 dong sold-out that co room_identity_key NULL; curated_observation_keys thi NOT NULL."""
    sold_out = {
        "record_id": 9, "hotel_id": "roma", "checkin_date": dt.date(2026, 9, 20),
        "room_type_raw": None, "max_occupancy": None, "bed_config": None, "room_area": None,
        "breakfast_included": None, "free_cancellation": None, "cancellation_policy": None,
    }
    keys = compute_canonical_keys(sold_out)
    assert keys.canonical_room_key == EMPTY_ROOM_KEY
    assert keys.canonical_rate_key == EMPTY_RATE_KEY
    assert is_empty_room_identity(keys)
    assert len(keys.canonical_series_id) == 64


def test_sold_out_cua_2_hotel_khac_nhau_van_khac_series():
    """Key phong rong dung chung, nhung series van phai phan biet duoc theo hotel/check-in."""
    base = {
        "record_id": 1, "checkin_date": dt.date(2026, 9, 20), "room_type_raw": None,
        "max_occupancy": None, "bed_config": None, "room_area": None,
        "breakfast_included": None, "free_cancellation": None, "cancellation_policy": None,
    }
    a = compute_canonical_keys({**base, "hotel_id": "roma"})
    b = compute_canonical_keys({**base, "hotel_id": "sen"})
    assert a.canonical_room_key == b.canonical_room_key
    assert a.canonical_series_id != b.canonical_series_id


def test_phong_that_khong_bao_gio_trung_key_rong():
    assert compute_canonical_keys(with_flags(True, True)).canonical_room_key != EMPTY_ROOM_KEY

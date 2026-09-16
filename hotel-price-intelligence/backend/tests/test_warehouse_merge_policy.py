"""Merge `hotels` theo nhom truong (muc 6, GPT file 02 D5) - pure."""
import datetime as dt

import pytest

from app.warehouse.errors import WarehouseError
from app.warehouse.merge_policy import HotelSourceRow, merge_hotel

T = dt.datetime


def src(code, priority, **row):
    base = {"hotel_id": "roma", "name": f"Roma {code}", "hotel_link": "u", "created_at": T(2026, 8, 20)}
    return HotelSourceRow(code, priority, {**base, **row})


def city(hotel_id):
    return {"roma": "Phú Quốc"}.get(hotel_id)


def test_hai_nhom_co_the_lay_tu_2_nguon_khac_nhau():
    local = src("local_primary", 0, attributes_updated_at=T(2026, 9, 10), booking_status="active",
                booking_status_checked_at=T(2026, 9, 1))
    vps = src("vps", 1, attributes_updated_at=T(2026, 9, 5), booking_status="not_bookable",
              booking_status_checked_at=T(2026, 9, 12))
    merged = merge_hotel("roma", [local, vps], city)
    assert merged.row["name"] == "Roma local_primary" and merged.attribute_source == "local_primary"
    assert merged.row["booking_status"] == "not_bookable" and merged.status_source == "vps"
    assert merged.row["booking_status_checked_at"] == T(2026, 9, 12)


def test_timestamp_null_la_cu_nhat():
    a = src("local_primary", 0, attributes_updated_at=None, address="A")
    b = src("vps", 1, attributes_updated_at=T(2026, 1, 1), address="B")
    assert merge_hotel("roma", [a, b], city).row["address"] == "B"


def test_bang_timestamp_thi_it_null_hon_thang_truoc_priority():
    a = src("local_primary", 0, attributes_updated_at=T(2026, 9, 1), review_score=None)
    b = src("vps", 1, attributes_updated_at=T(2026, 9, 1), review_score=8.5, address="x")
    assert merge_hotel("roma", [a, b], city).attribute_source == "vps"


def test_hoa_het_thi_source_priority_nho_hon_thang_khong_so_chuoi_ten():
    a = src("zzz_nguon", 0, attributes_updated_at=T(2026, 9, 1))
    b = src("aaa_nguon", 1, attributes_updated_at=T(2026, 9, 1))
    assert merge_hotel("roma", [a, b], city).attribute_source == "zzz_nguon"


def test_completeness_nhom_status_khong_anh_huong_nhom_attribute():
    a = src("local_primary", 0, attributes_updated_at=T(2026, 9, 1), booking_status="active",
            booking_status_reason="ly do", booking_status_checked_at=None)
    b = src("vps", 1, attributes_updated_at=T(2026, 9, 1), booking_status="active",
            booking_status_checked_at=None, address="co them 1 field attribute")
    merged = merge_hotel("roma", [a, b], city)
    assert merged.attribute_source == "vps"      # it NULL hon trong nhom ATTRIBUTE
    assert merged.status_source == "local_primary"  # it NULL hon trong nhom STATUS


def test_created_at_min_va_city_chi_tu_cohort():
    a = src("local_primary", 0, created_at=T(2026, 8, 9), city="Sai City")
    b = src("vps", 1, created_at=T(2026, 8, 24), city="Sai Nua")
    merged = merge_hotel("roma", [a, b], city)
    assert merged.row["created_at"] == T(2026, 8, 9)
    assert merged.row["city"] == "Phú Quốc"


def test_hotel_ngoai_cohort_thi_city_null_khong_lay_city_nguon():
    row = HotelSourceRow("local_primary", 0, {"hotel_id": "sen", "name": "Sen", "hotel_link": "u",
                                              "city": "Hà Nội", "created_at": T(2026, 8, 9)})
    assert merge_hotel("sen", [row], city).row["city"] is None


@pytest.mark.parametrize("rows,match", [
    ([], "khong co dong"),
    ([src("local_primary", 0), src("vps", 0)], "trung source_priority"),
    ([HotelSourceRow("vps", 0, {"hotel_id": "khac", "created_at": T(2026, 1, 1)})], "hotel_id"),
    ([HotelSourceRow("vps", 0, {"hotel_id": "roma", "created_at": None})], "created_at"),
])
def test_dau_vao_sai_thi_fail(rows, match):
    with pytest.raises(WarehouseError, match=match):
        merge_hotel("roma", rows, city)

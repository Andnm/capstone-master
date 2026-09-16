"""Test cho `holidays.py` (EDA_CURATED_PLAN.md muc 6, muc 7.6, muc 10.1)."""
from __future__ import annotations

import csv
import datetime as dt
from pathlib import Path

import pytest

from holidays import (
    EXPECTED_COLUMNS,
    VALID_CITIES,
    calendar_flags_by_date_city,
    checkin_calendar_flags,
    load_holiday_csv,
    observation_day_calendar_flags,
)

D = dt.date


def write_csv(path: Path, rows: list[dict]) -> Path:
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=EXPECTED_COLUMNS)
        writer.writeheader()
        for row in rows:
            writer.writerow(row)
    return path


def row(holiday_date, event_code, *, event_type="public_holiday", scope="national", city="",
       is_tet="0", status="confirmed", name=None):
    return {"holiday_date": holiday_date, "event_code": event_code, "name": name or event_code,
            "event_type": event_type, "scope": scope, "city": city, "is_tet": is_tet, "status": status,
            "source_url": "https://example.test"}


# ======================================================================== load_holiday_csv
def test_load_that_csv_du_an_204_dong_sach():
    real_path = Path(__file__).resolve().parents[3] / "data" / "vn_holidays.csv"
    loaded = load_holiday_csv(real_path)
    assert len(loaded.events) == 203  # 204 dong file - 1 header (CLAUDE.md muc 7.2)
    assert loaded.sha256 == __import__("hashlib").sha256(real_path.read_bytes()).hexdigest()
    assert set(loaded.events["status"]) <= {"confirmed", "provisional"}
    assert (loaded.events.loc[loaded.events["scope"] == "national", "city"].isna()).all()
    assert (loaded.events.loc[loaded.events["scope"] == "city", "city"].notna()).all()


def test_load_khong_ton_tai_thi_fail(tmp_path):
    with pytest.raises(FileNotFoundError):
        load_holiday_csv(tmp_path / "khong_co.csv")


def test_load_sai_cot_thi_fail(tmp_path):
    path = tmp_path / "bad.csv"
    path.write_text("a,b,c\n1,2,3\n", encoding="utf-8")
    with pytest.raises(ValueError, match="sai cot"):
        load_holiday_csv(path)


def test_load_trung_khoa_thi_fail(tmp_path):
    path = write_csv(tmp_path / "dup.csv", [row("2026-01-01", "new_year"), row("2026-01-01", "new_year")])
    with pytest.raises(ValueError, match="trung khoa"):
        load_holiday_csv(path)


def test_load_scope_national_nhung_co_city_thi_fail(tmp_path):
    path = write_csv(tmp_path / "bad.csv", [row("2026-01-01", "x", scope="national", city="Hà Nội")])
    with pytest.raises(ValueError, match="scope=national"):
        load_holiday_csv(path)


def test_load_scope_city_nhung_khong_co_city_thi_fail(tmp_path):
    path = write_csv(tmp_path / "bad.csv", [row("2026-01-01", "x", scope="city", city="")])
    with pytest.raises(ValueError, match="city rong"):
        load_holiday_csv(path)


def test_load_city_ngoai_5_thanh_pho_thi_fail(tmp_path):
    path = write_csv(tmp_path / "bad.csv", [row("2026-01-01", "x", scope="city", city="Đà Nẵng")])
    with pytest.raises(ValueError, match="ngoai 5 thanh pho"):
        load_holiday_csv(path)


def test_load_event_type_sai_thi_fail(tmp_path):
    path = write_csv(tmp_path / "bad.csv", [row("2026-01-01", "x", event_type="khong_hop_le")])
    with pytest.raises(ValueError, match="event_type"):
        load_holiday_csv(path)


def test_load_rong_thi_fail(tmp_path):
    path = write_csv(tmp_path / "empty.csv", [])
    with pytest.raises(ValueError, match="rong"):
        load_holiday_csv(path)


# ======================================================================== aggregation - test bat buoc cua GPT (file 03 muc 6)
def test_2_national_2_city_cung_ngay_khong_nhan_dong():
    """2 event national + 2 event city (dung ngay, dung 1 thanh pho) van ra DUNG 1 dong (date, city)."""
    path_rows = [
        row("2026-02-16", "tet_29", event_type="public_holiday", is_tet="1"),
        row("2026-02-16", "concert_toq", event_type="major_event"),
        row("2026-02-16", "dalat_flower", event_type="festival", scope="city", city="Đà Lạt"),
        row("2026-02-16", "dalat_extra", event_type="major_event", scope="city", city="Đà Lạt"),
    ]
    events = load_holiday_csv(write_csv(_tmp(), path_rows)).events
    out = calendar_flags_by_date_city(events, dates=[D(2026, 2, 16)], cities=VALID_CITIES)

    assert len(out) == len(VALID_CITIES)  # dung 1 dong / (date, city), khong nhan boi so event
    dalat = out[(out["holiday_date"] == D(2026, 2, 16)) & (out["city"] == "Đà Lạt")].iloc[0]
    assert dalat["holiday_event_count"] == 4  # 2 national + 2 city rieng cua Da Lat
    assert bool(dalat["is_public_holiday"]) and bool(dalat["is_tet"]) and bool(dalat["is_festival_period"]) and bool(dalat["is_major_event"])
    assert dalat["confirmed_event_count"] == 4 and dalat["provisional_event_count"] == 0

    other = out[(out["holiday_date"] == D(2026, 2, 16)) & (out["city"] == "Hà Nội")].iloc[0]
    assert other["holiday_event_count"] == 2  # chi 2 national, khong nhan Da Lat event
    assert not bool(other["is_festival_period"])


def test_city_event_khong_lan_sang_thanh_pho_khac():
    events = load_holiday_csv(write_csv(_tmp(), [
        row("2026-04-30", "danang_x", event_type="festival", scope="city", city="Vũng Tàu"),
    ])).events
    out = calendar_flags_by_date_city(events, dates=[D(2026, 4, 30)], cities=VALID_CITIES)
    vung_tau = out[out["city"] == "Vũng Tàu"].iloc[0]
    ha_noi = out[out["city"] == "Hà Nội"].iloc[0]
    assert vung_tau["holiday_event_count"] == 1
    assert ha_noi["holiday_event_count"] == 0


def test_ngay_khong_co_event_van_co_dong_gia_tri_false_0():
    events = load_holiday_csv(write_csv(_tmp(), [row("2026-01-01", "new_year")])).events
    out = calendar_flags_by_date_city(events, dates=[D(2099, 12, 31)], cities=VALID_CITIES)
    assert len(out) == len(VALID_CITIES)
    assert (out["holiday_event_count"] == 0).all()
    assert not out["is_public_holiday"].any()


def test_provisional_va_confirmed_dem_rieng():
    events = load_holiday_csv(write_csv(_tmp(), [
        row("2026-03-01", "a", status="confirmed"), row("2026-03-01", "b", status="provisional"),
    ])).events
    out = calendar_flags_by_date_city(events, dates=[D(2026, 3, 1)], cities=("Hà Nội",))
    r = out.iloc[0]
    assert r["confirmed_event_count"] == 1 and r["provisional_event_count"] == 1
    assert r["holiday_event_count"] == 2


def test_khong_co_ngay_nao_van_tra_ve_frame_dung_cot():
    events = load_holiday_csv(write_csv(_tmp(), [row("2026-01-01", "x")])).events
    out = calendar_flags_by_date_city(events, dates=[], cities=VALID_CITIES)
    assert len(out) == 0
    assert set(out.columns) >= {"holiday_date", "city", "is_public_holiday", "holiday_event_count"}


# ======================================================================== 2 date role KHONG duoc tron
def test_checkin_va_observation_day_dung_cot_khac_nhau():
    events = load_holiday_csv(write_csv(_tmp(), [row("2026-02-16", "tet_29", is_tet="1")])).events
    checkin = checkin_calendar_flags(events, [D(2026, 2, 16)])
    obs = observation_day_calendar_flags(events, [D(2026, 2, 16)])
    assert "checkin_date" in checkin.columns and "vn_observation_date" not in checkin.columns
    assert "vn_observation_date" in obs.columns and "checkin_date" not in obs.columns
    assert "is_tet" in checkin.columns and "is_tet_observation_day" in obs.columns
    assert "is_tet" not in obs.columns  # khong tron 2 bo cot


_tmp_counter = [0]


def _tmp() -> Path:
    import tempfile

    _tmp_counter[0] += 1
    return Path(tempfile.gettempdir()) / f"eda_holiday_test_{_tmp_counter[0]}.csv"

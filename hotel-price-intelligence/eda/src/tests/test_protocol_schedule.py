"""Test cho `protocol_schedule.py` (EDA_CURATED_PLAN.md muc 7.5, GPT review 12 eda M1/M2/M5) - dung
loader manifest/cohort THAT cua backend (khong mock), fixture nho tu tao trong tmp_path."""
from __future__ import annotations

import datetime as dt
import json

import openpyxl
import pandas as pd
import pytest

import db
from protocol_schedule import classify_outcomes, expected_schedule, resolve_effective_hotel_id, summarize_unattributed

D = dt.date


def _write_cohort_workbook(path, members):
    db._ensure_backend_importable()
    from app.warehouse.cohort_manifest import VALID_CITIES

    workbook = openpyxl.Workbook()
    workbook.remove(workbook.active)
    for city in VALID_CITIES:
        sheet = workbook.create_sheet(city)
        sheet.append(["Tên khách sạn", "Link"])
        for name, slug in members.get(city, []):
            sheet.append([name, f"https://www.booking.com/hotel/vn/{slug}.vi.html"])
    workbook.save(path)
    return path


def _write_cohort_history(tmp_path, versions):
    db._ensure_backend_importable()
    from app.warehouse.cohort_manifest import load_cohort_manifest

    entries = []
    for index, (label, effective_from, members) in enumerate(versions):
        wb_path = _write_cohort_workbook(tmp_path / f"{index}_{label}.xlsx", members)
        manifest = load_cohort_manifest(wb_path)
        entries.append({"cohort_version": label, "effective_from_crawl_date": effective_from,
                        "workbook_path": wb_path.name, "members_sha256": manifest.manifest_sha256,
                        "size": manifest.size})
    history_path = tmp_path / "cohort_history.json"
    history_path.write_text(json.dumps({"cohort_history_version": 1, "versions": entries}), encoding="utf-8")
    return history_path


def _write_ownership_manifest(tmp_path, rows):
    db._ensure_backend_importable()
    from app.warehouse.ownership_manifest import OwnershipRow, write_ownership_manifest

    path = tmp_path / "ownership.json"
    write_ownership_manifest([OwnershipRow(*row) for row in rows], path)
    return path


# ======================================================================== expected_schedule (GPT review 12 M1)
def test_expected_schedule_dung_planned_window_giao_cutoff_theo_source(tmp_path):
    cohort_path = _write_cohort_history(tmp_path, [
        ("v1", "2026-08-18", {"Hà Nội": [("H1", "h1")], "Đà Lạt": [("H2", "h2")]}),
    ])
    ownership_path = _write_ownership_manifest(tmp_path, [
        ("local_primary", D(2026, 8, 18), "N1", D(2026, 9, 5)),
        ("local_primary", D(2026, 8, 19), "N1", D(2026, 9, 6)),
        ("local_primary", D(2026, 8, 20), "N1", D(2026, 9, 7)),  # sau cutoff local_primary -> loai
        ("vps", D(2026, 8, 18), "V1", D(2026, 9, 10)),  # cutoff vps truoc ca window -> loai het
    ])
    out = expected_schedule(
        ownership_path, cohort_path, base_dir=tmp_path,
        cutoff_date_by_source={"local_primary": D(2026, 8, 19), "vps": D(2026, 8, 17)},
    )
    assert set(out["crawl_date"]) == {D(2026, 8, 18), D(2026, 8, 19)}
    assert set(out["owner_source"]) == {"local_primary"}
    assert set(out["hotel_id"]) == {"h1", "h2"}


def test_expected_schedule_khong_phu_thuoc_actual_run_ngay_hoan_toan_khong_co_run_van_co_mat(tmp_path):
    """GPT review 12 eda M1 (bug cu): dung tap 'ngay THAT SU co run' de loc TRUOC nghia la 1 ngay co ke
    hoach nhung KHONG CO RUN NAO CA se khong bao gio vao duoc expected, nen `missing_run` khong bao gio
    xuat hien. `expected_schedule` gio KHONG nhan tham so nao ve actual run - chi phu thuoc manifest +
    cohort + cutoff, nen ca ngay "trang" (khong co run) van phai co mat o day."""
    cohort_path = _write_cohort_history(tmp_path, [("v1", "2026-08-18", {"Hà Nội": [("H1", "h1")]})])
    ownership_path = _write_ownership_manifest(tmp_path, [
        ("local_primary", D(2026, 8, 18), "N1", D(2026, 9, 5)),
        ("local_primary", D(2026, 8, 19), "N1", D(2026, 9, 6)),  # gia su KHONG co run nao trong DB
    ])
    out = expected_schedule(ownership_path, cohort_path, base_dir=tmp_path,
                            cutoff_date_by_source={"local_primary": D(2026, 8, 25)})
    assert set(out["crawl_date"]) == {D(2026, 8, 18), D(2026, 8, 19)}


def test_expected_schedule_khong_co_cutoff_cho_source_thi_dung_het_planned_window(tmp_path):
    cohort_path = _write_cohort_history(tmp_path, [("v1", "2026-08-18", {"Hà Nội": [("H1", "h1")]})])
    ownership_path = _write_ownership_manifest(tmp_path, [
        ("local_primary", D(2026, 8, 18), "N1", D(2026, 9, 5)),
        ("local_primary", D(2026, 11, 30), "N1", D(2026, 12, 5)),
    ])
    out = expected_schedule(ownership_path, cohort_path, base_dir=tmp_path, cutoff_date_by_source={})
    assert set(out["crawl_date"]) == {D(2026, 8, 18), D(2026, 11, 30)}


def test_expected_schedule_hotel_ngoai_cohort_tai_ngay_do_bi_loai(tmp_path):
    """Ca Mac Valley: h2 la thanh vien cohort v1 nhung roi tu v2 (03/09) - expected schedule phai
    dung DUNG version co hieu luc tai crawl_date, khong dung workbook hien tai (CLAUDE.md muc 2)."""
    cohort_path = _write_cohort_history(tmp_path, [
        ("v1", "2026-08-18", {"Hà Nội": [("H1", "h1"), ("H2", "h2")]}),
        ("v2", "2026-09-03", {"Hà Nội": [("H1", "h1")]}),
    ])
    ownership_path = _write_ownership_manifest(tmp_path, [
        ("local_primary", D(2026, 8, 20), "N1", D(2026, 9, 5)),
        ("local_primary", D(2026, 9, 5), "N1", D(2026, 9, 20)),
    ])
    out = expected_schedule(ownership_path, cohort_path, base_dir=tmp_path,
                            cutoff_date_by_source={"local_primary": D(2026, 9, 5)})
    day1 = out[out["crawl_date"] == D(2026, 8, 20)]
    day2 = out[out["crawl_date"] == D(2026, 9, 5)]
    assert set(day1["hotel_id"]) == {"h1", "h2"}  # h2 con trong cohort ngay 20/08
    assert set(day2["hotel_id"]) == {"h1"}  # h2 da roi cohort ngay 05/09


def test_expected_schedule_khong_co_ngay_nao_khop_thi_rong_khong_raise(tmp_path):
    cohort_path = _write_cohort_history(tmp_path, [("v1", "2026-08-18", {"Hà Nội": [("H1", "h1")]})])
    ownership_path = _write_ownership_manifest(tmp_path, [
        ("local_primary", D(2026, 8, 18), "N1", D(2026, 9, 5)),
    ])
    out = expected_schedule(ownership_path, cohort_path, base_dir=tmp_path,
                            cutoff_date_by_source={"local_primary": D(2026, 8, 1)})
    assert len(out) == 0
    assert list(out.columns) == ["owner_source", "crawl_date", "schedule_slot", "checkin_date", "hotel_id"]


# ======================================================================== classify_outcomes (thuan, khong MySQL)
def test_classify_outcomes_missing_run_khi_khong_co_actual_va_khong_truyen_source_run_dates():
    expected = pd.DataFrame({
        "owner_source": ["local_primary"], "crawl_date": [D(2026, 8, 18)],
        "schedule_slot": ["N1"], "checkin_date": [D(2026, 9, 5)], "hotel_id": ["h1"],
    })
    actual = pd.DataFrame(columns=["source_code", "crawl_date", "checkin_date", "hotel_id", "outcome"])
    out = classify_outcomes(expected, actual)
    assert out.iloc[0]["outcome"] == "missing_run"


def test_classify_outcomes_lay_dung_outcome_tu_actual():
    expected = pd.DataFrame({
        "owner_source": ["local_primary", "local_primary"], "crawl_date": [D(2026, 8, 18)] * 2,
        "schedule_slot": ["N1", "N1"], "checkin_date": [D(2026, 9, 5)] * 2, "hotel_id": ["h1", "h2"],
    })
    actual = pd.DataFrame({
        "source_code": ["local_primary"], "crawl_date": [D(2026, 8, 18)],
        "checkin_date": [D(2026, 9, 5)], "hotel_id": ["h1"], "outcome": ["owner_success"],
    })
    out = classify_outcomes(expected, actual)
    h1 = out[out["hotel_id"] == "h1"].iloc[0]
    h2 = out[out["hotel_id"] == "h2"].iloc[0]
    assert h1["outcome"] == "owner_success" and h2["outcome"] == "missing_run"


def test_classify_outcomes_phan_biet_missing_source_run_va_missing_item_in_existing_run():
    """GPT review 12 eda M1: khi truyen `source_run_dates`, phai tach 2 loai gap khac nhau ve nguyen
    nhan - ca ngay khong co run (missing_source_run) vs co run nhung thieu item nay
    (missing_item_in_existing_run)."""
    expected = pd.DataFrame({
        "owner_source": ["local_primary", "local_primary"],
        "crawl_date": [D(2026, 8, 18), D(2026, 8, 19)],
        "schedule_slot": ["N1", "N1"], "checkin_date": [D(2026, 9, 5), D(2026, 9, 6)],
        "hotel_id": ["h1", "h1"],
    })
    # 18/08: khong co dong actual nao (ca ngay mat trang). 19/08: co actual nhung la hotel KHAC (h2),
    # nen h1 ngay 19/08 thieu du CO run trong ngay do.
    actual = pd.DataFrame({
        "source_code": ["local_primary"], "crawl_date": [D(2026, 8, 19)],
        "checkin_date": [D(2026, 9, 6)], "hotel_id": ["h2"], "outcome": ["owner_success"],
    })
    out = classify_outcomes(
        expected, actual, source_run_dates={"local_primary": {D(2026, 8, 19)}},
    )
    day1 = out[out["crawl_date"] == D(2026, 8, 18)].iloc[0]
    day2 = out[out["crawl_date"] == D(2026, 8, 19)].iloc[0]
    assert day1["outcome"] == "missing_source_run"
    assert day2["outcome"] == "missing_item_in_existing_run"


def test_classify_outcomes_actual_trung_khoa_thi_fail():
    expected = pd.DataFrame({"owner_source": ["s"], "crawl_date": [D(2026, 1, 1)],
                             "schedule_slot": ["N1"], "checkin_date": [D(2026, 1, 5)], "hotel_id": ["h1"]})
    actual = pd.DataFrame({
        "source_code": ["s", "s"], "crawl_date": [D(2026, 1, 1)] * 2, "checkin_date": [D(2026, 1, 5)] * 2,
        "hotel_id": ["h1", "h1"], "outcome": ["owner_success", "owner_failure_status_error"],
    })
    with pytest.raises(ValueError, match="trung khoa"):
        classify_outcomes(expected, actual)


def test_classify_outcomes_thieu_cot_thi_fail():
    with pytest.raises(ValueError, match="expected thieu cot"):
        classify_outcomes(pd.DataFrame({"x": [1]}), pd.DataFrame({
            "source_code": [], "crawl_date": [], "checkin_date": [], "hotel_id": [], "outcome": []}))
    with pytest.raises(ValueError, match="actual thieu cot"):
        classify_outcomes(pd.DataFrame({
            "owner_source": [], "crawl_date": [], "checkin_date": [], "hotel_id": []}), pd.DataFrame({"x": [1]}))


# ======================================================================== resolve_effective_hotel_id (GPT review 12 M2)
def _actual_row(*, source_code="local_primary", crawl_date=D(2026, 9, 1), checkin_date=D(2026, 9, 5),
                hotel_id=None, link="https://www.booking.com/searchresults.html?ss=hanoi",
                link_hash="hash0", outcome="owner_failure_status_error"):
    return {
        "source_code": source_code, "crawl_date": crawl_date, "checkin_date": checkin_date,
        "hotel_id": hotel_id, "source_hotel_link": link, "source_link_hash": link_hash, "outcome": outcome,
    }


def test_resolve_effective_hotel_id_giu_nguyen_khi_hotel_id_da_co(tmp_path):
    cohort_path = _write_cohort_history(tmp_path, [("v1", "2026-08-18", {"Hà Nội": [("H1", "h1")]})])
    actual = pd.DataFrame([_actual_row(hotel_id="h1", outcome="owner_success")])
    out = resolve_effective_hotel_id(actual, cohort_path, base_dir=tmp_path)
    assert out.iloc[0]["effective_hotel_id"] == "h1"
    assert out.iloc[0]["hotel_id_resolution"] == "original"


def test_resolve_effective_hotel_id_resolve_duoc_tu_source_hotel_link(tmp_path):
    """Ca that GPT review 12 M2 phat hien: item error hotel_id=NULL nhung source_hotel_link van con
    nguyen slug - resolve duoc qua extract_hotel_slug va slug do dang active trong cohort."""
    cohort_path = _write_cohort_history(tmp_path, [("v1", "2026-08-18", {"Hà Nội": [("H1", "mai-gia-huy")]})])
    actual = pd.DataFrame([_actual_row(
        hotel_id=None, crawl_date=D(2026, 8, 20),
        link="https://www.booking.com/hotel/vn/mai-gia-huy.vi.html?checkin=2026-09-05",
        link_hash="hashA",
    )])
    out = resolve_effective_hotel_id(actual, cohort_path, base_dir=tmp_path)
    assert out.iloc[0]["effective_hotel_id"] == "mai-gia-huy"
    assert out.iloc[0]["hotel_id_resolution"] == "resolved_from_link"


def test_resolve_effective_hotel_id_slug_resolve_duoc_nhung_ngoai_cohort_tai_ngay_do_van_unattributed(tmp_path):
    """"assert mapping ... thuoc cohort hieu luc" (GPT file 09 M2 diem 2): slug parse duoc KHONG DU -
    phai la thanh vien cohort dang active tai dung crawl_date, tranh resolve nham chuoi rac."""
    cohort_path = _write_cohort_history(tmp_path, [("v1", "2026-08-18", {"Hà Nội": [("H1", "h1")]})])
    actual = pd.DataFrame([_actual_row(
        hotel_id=None, crawl_date=D(2026, 8, 10),  # TRUOC ngay cohort v1 co hieu luc
        link="https://www.booking.com/hotel/vn/h1.vi.html", link_hash="hashB",
    )])
    out = resolve_effective_hotel_id(actual, cohort_path, base_dir=tmp_path)
    assert out.iloc[0]["effective_hotel_id"] is None
    assert out.iloc[0]["hotel_id_resolution"] == "unattributed"


def test_resolve_effective_hotel_id_link_khong_parse_duoc_slug_thi_unattributed(tmp_path):
    cohort_path = _write_cohort_history(tmp_path, [("v1", "2026-08-18", {"Hà Nội": [("H1", "h1")]})])
    actual = pd.DataFrame([_actual_row(
        hotel_id=None, link="https://www.booking.com/searchresults.html?ss=hanoi", link_hash="hashC",
    )])
    out = resolve_effective_hotel_id(actual, cohort_path, base_dir=tmp_path)
    assert out.iloc[0]["effective_hotel_id"] is None
    assert out.iloc[0]["hotel_id_resolution"] == "unattributed"


def test_resolve_effective_hotel_id_thieu_cot_thi_fail(tmp_path):
    cohort_path = _write_cohort_history(tmp_path, [("v1", "2026-08-18", {"Hà Nội": [("H1", "h1")]})])
    with pytest.raises(ValueError, match="thieu cot"):
        resolve_effective_hotel_id(pd.DataFrame({"x": [1]}), cohort_path, base_dir=tmp_path)


# ======================================================================== summarize_unattributed (GPT M2 diem 4 + MIN1)
def test_summarize_unattributed_rong_khi_khong_co_dong_nao(tmp_path):
    cohort_path = _write_cohort_history(tmp_path, [("v1", "2026-08-18", {"Hà Nội": [("H1", "h1")]})])
    actual = pd.DataFrame([_actual_row(hotel_id="h1", outcome="owner_success")])
    resolved = resolve_effective_hotel_id(actual, cohort_path, base_dir=tmp_path)
    out = summarize_unattributed(resolved)
    assert len(out) == 0
    assert list(out.columns) == ["source_code", "crawl_date", "n_unattributed_errors", "sample_keys"]


def test_summarize_unattributed_gom_nhom_va_gioi_han_sample(tmp_path):
    cohort_path = _write_cohort_history(tmp_path, [("v1", "2026-08-18", {"Hà Nội": [("H1", "h1")]})])
    rows = [
        _actual_row(hotel_id=None, crawl_date=D(2026, 9, 1), link_hash=f"hash{i}")
        for i in range(3)
    ]
    actual = pd.DataFrame(rows)
    resolved = resolve_effective_hotel_id(actual, cohort_path, base_dir=tmp_path)
    out = summarize_unattributed(resolved, sample_size=2)
    assert len(out) == 1
    row = out.iloc[0]
    assert row["source_code"] == "local_primary" and row["crawl_date"] == D(2026, 9, 1)
    assert row["n_unattributed_errors"] == 3
    assert json.loads(row["sample_keys"]) == ["hash0", "hash1"]  # gioi han dung sample_size=2


def test_summarize_unattributed_thieu_cot_thi_fail():
    with pytest.raises(ValueError, match="thieu cot"):
        summarize_unattributed(pd.DataFrame({"x": [1]}))

"""Cohort theo version (CLAUDE.md muc 2): hotel roi cohort van la thanh vien o moi ngay TRUOC khi roi.

Ca that: Mac Valley (`mac-dalat`) co 108 item success hop le 18-26/08 o local; workbook hien tai (v2=354)
khong con hotel nay. Tra cohort bang workbook hien tai -> 108 item do bi danh protocol_deviation va
city=NULL -> loai khoi train, dung loai survivor-selection bias ma CLAUDE.md cam.
Fixture ghi file .xlsx/.json THAT (khong mock openpyxl), cung ly do nhu test_warehouse_manifests.
"""
import datetime as dt
import json

import openpyxl
import pytest

from app.warehouse.cohort_manifest import VALID_CITIES, load_cohort, load_cohort_history, load_cohort_manifest
from app.warehouse.errors import ManifestError

D = dt.date
V1 = {"Đà Lạt": ["mac-dalat", "lumina"], "Hà Nội": ["roma"]}
V2 = {"Đà Lạt": ["lumina"], "Hà Nội": ["roma"]}


def write_cohort(path, members):
    workbook = openpyxl.Workbook()
    workbook.remove(workbook.active)
    for city in VALID_CITIES:
        sheet = workbook.create_sheet(city)
        sheet.append(["Tên khách sạn", "Link"])
        for slug in members.get(city, []):
            sheet.append([slug.upper(), f"https://www.booking.com/hotel/vn/{slug}.vi.html?aid=1"])
    workbook.save(path)
    return path


def write_history(tmp_path, versions, **overrides):
    entries = []
    for index, (label, effective, members) in enumerate(versions):
        path = write_cohort(tmp_path / f"{index}_{label}.xlsx", members)
        manifest = load_cohort_manifest(path)
        entries.append({"cohort_version": label, "effective_from_crawl_date": effective, "workbook_path": path.name,
                        "members_sha256": manifest.manifest_sha256, "size": manifest.size,
                        **overrides.get(label, {})})
    history = tmp_path / "history.json"
    history.write_text(json.dumps({"cohort_history_version": 1, "versions": entries}), encoding="utf-8")
    return history


def standard(tmp_path, **overrides):
    return write_history(tmp_path, [("v1", "2026-08-18", V1), ("v2", "2026-09-02", V2)], **overrides)


def test_hotel_roi_cohort_van_la_thanh_vien_truoc_ngay_roi(tmp_path):
    history = load_cohort_history(standard(tmp_path), base_dir=tmp_path)
    assert history.contains_at("mac-dalat", D(2026, 8, 18))
    assert history.contains_at("mac-dalat", D(2026, 9, 1))
    assert not history.contains_at("mac-dalat", D(2026, 9, 2))  # dung ngay hieu luc v2
    assert history.contains_at("lumina", D(2026, 9, 2)) and history.contains_at("lumina", D(2026, 11, 30))


def test_truoc_version_dau_tien_khong_ai_la_thanh_vien(tmp_path):
    history = load_cohort_history(standard(tmp_path), base_dir=tmp_path)
    assert history.version_at(D(2026, 8, 17)) is None
    assert not history.contains_at("lumina", D(2026, 8, 17))


def test_city_lay_tu_hop_cac_version(tmp_path):
    history = load_cohort_history(standard(tmp_path), base_dir=tmp_path)
    assert history.city_of("mac-dalat") == "Đà Lạt"  # da roi cohort nhung van giu city
    assert history.city_of("sen") is None


def test_members_sha256_khai_bao_sai_thi_fail(tmp_path):
    with pytest.raises(ManifestError, match="members_sha256 KHONG KHOP"):
        load_cohort_history(standard(tmp_path, v1={"members_sha256": "0" * 64}), base_dir=tmp_path)


def test_workbook_bi_sua_sau_khi_tao_history_thi_fail(tmp_path):
    path = standard(tmp_path)
    write_cohort(tmp_path / "0_v1.xlsx", V2)  # "sua tai cho" dung nhu workbook that da bi sua
    with pytest.raises(ManifestError, match="members_sha256 KHONG KHOP"):
        load_cohort_history(path, base_dir=tmp_path)


@pytest.mark.parametrize("size", [999, "3", True])
def test_size_sai_thi_fail(tmp_path, size):
    with pytest.raises(ManifestError, match="size"):
        load_cohort_history(standard(tmp_path, v1={"size": size}), base_dir=tmp_path)


def test_ngay_hieu_luc_khong_tang_dan_thi_fail(tmp_path):
    path = write_history(tmp_path, [("v1", "2026-09-02", V1), ("v2", "2026-08-18", V2)])
    with pytest.raises(ManifestError, match="tang dan"):
        load_cohort_history(path, base_dir=tmp_path)


def test_trung_nhan_version_thi_fail(tmp_path):
    path = write_history(tmp_path, [("v1", "2026-08-18", V1), ("v1", "2026-09-02", V2)])
    with pytest.raises(ManifestError, match="trung nhan"):
        load_cohort_history(path, base_dir=tmp_path)


def test_1_hotel_2_city_giua_cac_version_thi_fail(tmp_path):
    path = write_history(tmp_path, [("v1", "2026-08-18", V1), ("v2", "2026-09-02", {"Hà Nội": ["lumina", "roma"]})])
    with pytest.raises(ManifestError, match="city KHAC NHAU"):
        load_cohort_history(path, base_dir=tmp_path)


def test_hash_history_doi_khi_doi_ngay_hieu_luc(tmp_path):
    first = load_cohort_history(standard(tmp_path), base_dir=tmp_path).manifest_sha256
    moved = write_history(tmp_path, [("v1", "2026-08-18", V1), ("v2", "2026-09-03", V2)])
    assert load_cohort_history(moved, base_dir=tmp_path).manifest_sha256 != first


@pytest.mark.parametrize("payload,match", [
    ({"cohort_history_version": 2, "versions": []}, "cohort_history_version"),
    ({"cohort_history_version": 1, "versions": []}, "khong rong"),
    ({"cohort_history_version": 1, "versions": [{"cohort_version": "v1"}]}, "thieu truong"),
])
def test_json_sai_cau_truc_thi_fail(tmp_path, payload, match):
    path = tmp_path / "h.json"
    path.write_text(json.dumps(payload), encoding="utf-8")
    with pytest.raises(ManifestError, match=match):
        load_cohort_history(path, base_dir=tmp_path)


def test_load_cohort_xlsx_la_1_version_hieu_luc_moi_ngay(tmp_path):
    history = load_cohort(write_cohort(tmp_path / "c.xlsx", V2), base_dir=tmp_path)
    assert [version.label for version in history.versions] == ["single"]
    assert history.contains_at("lumina", D(2000, 1, 1))
    assert not history.contains_at("mac-dalat", D(2026, 8, 18))

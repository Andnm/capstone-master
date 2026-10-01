"""Hash/canonicalization cua warehouse - pure, khong can MySQL.

Trong tam: `source_manifest_sha256` phai la fingerprint cua DANH TINH nguon, khong phai cua cach
manifest duoc viet ra. Neu no doi vi thu tu dong hay vi `dump_path` thi bat bien "verify on every
consumption" (muc 16) tro nen vo dung - moi lan chay lai tren may khac se FAIL oan.
"""
import pytest

from app.warehouse.errors import ManifestError
from app.warehouse.hashing import (
    SOURCE_IDENTITY_FIELDS,
    canonical_json,
    iso_utc,
    source_manifest_payload,
    source_manifest_sha256,
)

_BASE = [
    {
        "source_code": "vps",
        "source_priority": 1,
        "dump_path": "/tmp/vps.sql",
        "dump_sha256": "b" * 64,
        "dump_taken_at": "2026-09-15T15:49:12Z",
        "schema_sha256": "d" * 64,
        "source_version_json": {"scraper_version": "2.3.0"},
    },
    {
        "source_code": "local_primary",
        "source_priority": 0,
        "dump_path": "/tmp/local.sql",
        "dump_sha256": "a" * 64,
        "dump_taken_at": "2026-09-15T15:55:33Z",
        "schema_sha256": "c" * 64,
        "source_version_json": {"scraper_version_range": ["2.1.0", "2.3.0"]},
    },
]


def test_payload_chi_gom_6_field_identity():
    payload = source_manifest_payload(_BASE)
    assert [set(entry) for entry in payload] == [set(SOURCE_IDENTITY_FIELDS)] * 2
    assert all("dump_path" not in entry for entry in payload)


def test_payload_luon_sort_theo_source_code():
    assert [entry["source_code"] for entry in source_manifest_payload(_BASE)] == ["local_primary", "vps"]


def test_hash_khong_doi_khi_dao_thu_tu_dong():
    assert source_manifest_sha256(_BASE) == source_manifest_sha256(list(reversed(_BASE)))


def test_hash_khong_doi_khi_doi_dump_path():
    moved = [dict(entry, dump_path=f"/other/{entry['source_code']}.sql") for entry in _BASE]
    assert source_manifest_sha256(moved) == source_manifest_sha256(_BASE)


def test_hash_doi_khi_doi_priority():
    changed = [dict(entry) for entry in _BASE]
    changed[0]["source_priority"] = 7
    assert source_manifest_sha256(changed) != source_manifest_sha256(_BASE)


def test_hash_doi_khi_doi_dump_sha256():
    changed = [dict(entry) for entry in _BASE]
    changed[1]["dump_sha256"] = "e" * 64
    assert source_manifest_sha256(changed) != source_manifest_sha256(_BASE)


def test_timestamp_viet_khac_dinh_dang_van_cho_cung_hash():
    """'2026-09-15T15:49:12Z' va '2026-09-15T22:49:12+07:00' la CUNG mot thoi diem."""
    other = [dict(entry) for entry in _BASE]
    other[0]["dump_taken_at"] = "2026-09-15T22:49:12+07:00"
    assert source_manifest_sha256(other) == source_manifest_sha256(_BASE)


def test_iso_utc_tu_choi_datetime_tz_aware():
    from datetime import datetime, timezone

    with pytest.raises(ManifestError):
        iso_utc(datetime(2026, 9, 15, tzinfo=timezone.utc))


def test_thieu_field_thi_fail_chu_khong_hash_thieu():
    broken = [dict(_BASE[0])]
    del broken[0]["schema_sha256"]
    with pytest.raises(ManifestError, match="schema_sha256"):
        source_manifest_sha256(broken)


def test_source_code_trung_thi_fail():
    with pytest.raises(ManifestError, match="trung"):
        source_manifest_sha256([_BASE[0], dict(_BASE[0])])


def test_manifest_rong_thi_fail():
    with pytest.raises(ManifestError, match="rong"):
        source_manifest_sha256([])


def test_canonical_json_on_dinh_voi_thu_tu_key():
    assert canonical_json({"b": 1, "a": 2}) == canonical_json({"a": 2, "b": 1})

"""Test cho `artifacts.py` (EDA_CURATED_PLAN.md muc 4, muc 10.1) - thuan, khong can MySQL."""
from __future__ import annotations

import datetime as dt
import json

import pytest

from artifacts import (
    analysis_id,
    atomic_write_json,
    mark_failed,
    new_analysis_dir,
    sha256_file,
    write_artifact_manifest,
)


def test_analysis_id_dinh_dang():
    aid = analysis_id("b20260916_2src", now=dt.datetime(2026, 9, 17, 8, 5, 3, tzinfo=dt.timezone.utc), suffix="ab12")
    assert aid == "eda_b20260916_2src_20260917_080503_ab12"


def test_analysis_id_khong_trung_khi_goi_lien_tiep():
    """GPT review 12 (eda) M2: chi den phut co the trung neu chay 2 lan/phut."""
    now = dt.datetime(2026, 9, 17, 8, 5, 3, tzinfo=dt.timezone.utc)
    a = analysis_id("b1", now=now)
    b = analysis_id("b1", now=now)
    assert a != b  # suffix ngau nhien khac nhau du cung batch_id + cung giay


def test_new_analysis_dir_tao_du_3_thu_muc_con(tmp_path):
    directory = new_analysis_dir("eda_test_1", outputs_dir=tmp_path)
    assert directory.is_dir()
    for sub in ("tables", "figures", "executed_notebooks"):
        assert (directory / sub).is_dir()


def test_new_analysis_dir_fail_if_exists(tmp_path):
    new_analysis_dir("eda_test_1", outputs_dir=tmp_path)
    with pytest.raises(FileExistsError):
        new_analysis_dir("eda_test_1", outputs_dir=tmp_path)


def test_mark_failed_ghi_file_ro_rang(tmp_path):
    directory = new_analysis_dir("eda_test_1", outputs_dir=tmp_path)
    mark_failed(directory, error="loi gia lap")
    payload = json.loads((directory / "FAILED.json").read_text(encoding="utf-8"))
    assert payload["error"] == "loi gia lap" and "failed_at_utc" in payload


def test_atomic_write_json_doc_lai_dung(tmp_path):
    path = tmp_path / "sub" / "x.json"
    atomic_write_json(path, {"a": 1, "b": "hai"})
    assert json.loads(path.read_text(encoding="utf-8")) == {"a": 1, "b": "hai"}


def test_atomic_write_json_khong_de_lai_file_tmp(tmp_path):
    path = tmp_path / "x.json"
    atomic_write_json(path, {"a": 1})
    leftovers = [p for p in tmp_path.iterdir() if p.name != "x.json"]
    assert leftovers == []


def test_sha256_file_khop_hashlib(tmp_path):
    path = tmp_path / "f.txt"
    path.write_bytes(b"noi dung test")
    import hashlib
    assert sha256_file(path) == hashlib.sha256(b"noi dung test").hexdigest()


def test_write_artifact_manifest_bao_dung_file_va_khong_tu_hash_chinh_no(tmp_path):
    (tmp_path / "tables").mkdir()
    (tmp_path / "tables" / "a.csv").write_text("x,y\n1,2\n", encoding="utf-8")
    (tmp_path / "EDA_REPORT.md").write_text("# report\n", encoding="utf-8")

    manifest_path = write_artifact_manifest(tmp_path)
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    paths = {entry["path"] for entry in manifest["files"]}
    assert paths == {"tables/a.csv", "EDA_REPORT.md"}
    assert manifest["file_count"] == 2
    # manifest KHONG tu liet ke chinh no
    assert "artifact_manifest.json" not in paths


def test_write_artifact_manifest_hash_dung_tung_file(tmp_path):
    (tmp_path / "a.txt").write_bytes(b"hello")
    manifest = json.loads(write_artifact_manifest(tmp_path).read_text(encoding="utf-8"))
    entry = next(e for e in manifest["files"] if e["path"] == "a.txt")
    assert entry["sha256"] == sha256_file(tmp_path / "a.txt")
    assert entry["size_bytes"] == 5

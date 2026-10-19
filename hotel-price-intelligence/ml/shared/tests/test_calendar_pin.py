"""GPT review vong 3 R3-M1: `vn_holidays.csv` la input DU LIEU lam doi feature lich => ghim hash trong `build_config_json`, kiem truoc khi doc/ghi,
parse dung bytes da kiem, snapshot bytes trong artifact + checksum, validation kiem lai. Thuan (khong MySQL); ban MySQL o `test_b5_mysql.py`/`test_b3_mysql.py`."""
from __future__ import annotations

import hashlib
import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from dataset_builder import config as cfg  # noqa: E402
from dataset_builder import env, export  # noqa: E402
from dataset_builder.calendar_features import CALENDAR_NAME, CalendarFeatures, CalendarInputError, calendar_input_descriptor  # noqa: E402
from dataset_builder.validation import _calendar_check  # noqa: E402

HEADER = "holiday_date,event_code,name,event_type,scope,city,is_tet,status,source_url\n"
ROW_A = "2026-09-02,national_day,QK,public_holiday,national,,0,confirmed,u\n"
ROW_B = "2026-09-14,fest1,LH,festival,city,Phú Quốc,0,confirmed,u\n"


def _csv(tmp_path: Path, *rows: str, name: str = "h.csv") -> Path:
    path = tmp_path / name
    path.write_bytes((HEADER + "".join(rows)).encode("utf-8"))
    return path


def test_descriptor_has_name_hash_and_size_but_no_absolute_path(tmp_path):
    path = _csv(tmp_path, ROW_A)
    d = calendar_input_descriptor(path)
    assert d == {"name": CALENDAR_NAME, "sha256": hashlib.sha256(path.read_bytes()).hexdigest(), "bytes": path.stat().st_size}
    assert str(tmp_path) not in json.dumps(d)


def test_load_verifies_pinned_hash_and_parses_the_verified_bytes(tmp_path):
    path = _csv(tmp_path, ROW_A, ROW_B)
    pinned = calendar_input_descriptor(path)["sha256"]
    cal = CalendarFeatures.load(path, expected_sha256=pinned)
    assert cal.sha256 == pinned and cal.raw == path.read_bytes() and hashlib.sha256(cal.raw).hexdigest() == pinned
    assert cal.features(__import__("datetime").date(2026, 9, 14), "Phú Quốc")["is_festival_period"] is True
    assert CalendarFeatures.load(path).sha256 == pinned                                    # khong ghim: hanh vi cu (test/EDA)


def test_changed_csv_fails_on_hash_before_it_is_even_parsed(tmp_path):
    path = _csv(tmp_path, ROW_A)
    pinned = calendar_input_descriptor(path)["sha256"]
    _csv(tmp_path, ROW_A, ROW_B)                                                           # sua noi dung sau init
    with pytest.raises(CalendarInputError, match="DA DOI") as raised:
        CalendarFeatures.load(path, expected_sha256=pinned)
    assert "dataset_version MOI" in str(raised.value)
    path.write_bytes(b"khong phai csv hop le\n")                                           # hong ca cau truc: van bao loi hash, khong phai loi parse
    with pytest.raises(CalendarInputError):
        CalendarFeatures.load(path, expected_sha256=pinned)


def test_config_pins_the_calendar_and_any_change_changes_the_config_hash(tmp_path, monkeypatch):
    base = dict(import_batch_id="b1", purpose="rehearsal", min_runs=3, min_coverage=0.8, anomaly_mode="retrospective_full", anomaly_cutoff_at=None,
                anomaly_registry_file_sha256="0" * 64, builder_code={"files": {"a.py": "1" * 64}, "code_sha256": "a" * 64})
    one = cfg.build_config(**base, calendar_input={"name": CALENDAR_NAME, "sha256": "1" * 64, "bytes": 10})
    two = cfg.build_config(**base, calendar_input={"name": CALENDAR_NAME, "sha256": "2" * 64, "bytes": 10})
    assert one["calendar_input"]["sha256"] == "1" * 64 and cfg.config_sha256(one) != cfg.config_sha256(two)
    monkeypatch.setattr(env, "HOLIDAYS_CSV", _csv(tmp_path, ROW_A))                       # khong phu thuoc file lich that (gitignored) tren may khac
    default = cfg.build_config(**base)                                                     # mac dinh: hash cua lich dang dung
    assert default["calendar_input"] == calendar_input_descriptor(env.HOLIDAYS_CSV)
    assert "calendar_input" not in default["builder_code"]["files"]                       # la data input, khong nam trong danh tinh ma
    assert not [f for f in cfg.build_config(**base)["builder_code"]["files"] if f.endswith("vn_holidays.csv")]


def test_features_step_fails_before_any_read_or_write_when_the_calendar_moved(tmp_path, monkeypatch):
    called: list[str] = []
    monkeypatch.setattr(export, "build_feature_frame", lambda *a, **k: called.append("frame"))
    out = tmp_path / "datasets"
    config = {"purpose": "rehearsal", "calendar_input": {"name": CALENDAR_NAME, "sha256": "0" * 64}}      # khong khop lich that
    with pytest.raises(CalendarInputError, match="DA DOI"):
        export.build_features_labels(None, dataset_version="ds_20261006_t", config=config, output_root=out, report_dir=tmp_path / "r")
    assert called == [] and not out.exists()                                               # khong doc DB (frame), khong tao thu muc output
    with pytest.raises(CalendarInputError, match="khong ghim"):
        export.build_features_labels(None, dataset_version="ds_20261006_t", config={"purpose": "rehearsal"}, output_root=out, report_dir=tmp_path / "r")
    assert called == [] and not out.exists()


# ----------------------------------------------------------------- validation: snapshot + manifest + checksum khop config
def _artifact(tmp_path: Path):
    path = _csv(tmp_path, ROW_A, ROW_B, name="src.csv")
    raw = path.read_bytes()
    sha = hashlib.sha256(raw).hexdigest()
    out = tmp_path / "ds"
    (out / "inputs").mkdir(parents=True)
    (out / export.CALENDAR_SNAPSHOT).write_bytes(raw)
    (out / export.CALENDAR_MANIFEST).write_text(json.dumps({"vn_holidays_csv_sha256": sha, "name": CALENDAR_NAME}), encoding="utf-8")
    stored = {export.CALENDAR_SNAPSHOT: {"file_sha256": sha}, export.CALENDAR_MANIFEST: {"file_sha256": "m" * 64}, "samples.parquet": {"file_sha256": "p" * 64}}
    return out, {"calendar_input": {"name": CALENDAR_NAME, "sha256": sha}}, stored, sha


def test_calendar_check_accepts_a_consistent_artifact(tmp_path):
    out, config, stored, _ = _artifact(tmp_path)
    assert _calendar_check(out, config, stored)["ok"] is True


@pytest.mark.parametrize("tamper", ["edit_snapshot", "delete_snapshot", "delete_manifest", "bad_manifest_json", "manifest_other_hash",
                                    "not_in_checksums", "config_without_pin", "config_other_pin"])
def test_calendar_check_rejects_tamper_missing_and_unpinned(tmp_path, tamper):
    out, config, stored, sha = _artifact(tmp_path)
    if tamper == "edit_snapshot":
        (out / export.CALENDAR_SNAPSHOT).write_bytes((out / export.CALENDAR_SNAPSHOT).read_bytes() + b"2030-01-01,x,x,public_holiday,national,,0,confirmed,u\n")
    elif tamper == "delete_snapshot":
        (out / export.CALENDAR_SNAPSHOT).unlink()
    elif tamper == "delete_manifest":
        (out / export.CALENDAR_MANIFEST).unlink()
    elif tamper == "bad_manifest_json":
        (out / export.CALENDAR_MANIFEST).write_text("not json", encoding="utf-8")
    elif tamper == "manifest_other_hash":
        (out / export.CALENDAR_MANIFEST).write_text(json.dumps({"vn_holidays_csv_sha256": "f" * 64}), encoding="utf-8")
    elif tamper == "not_in_checksums":
        stored.pop(export.CALENDAR_SNAPSHOT)
    elif tamper == "config_without_pin":
        config = {}
    elif tamper == "config_other_pin":
        config = {"calendar_input": {"sha256": "e" * 64}}
    result = _calendar_check(out, config, stored)
    assert result["ok"] is False and result["name"] == "lich_snapshot_khop_config_va_checksum"


def test_rebuilding_with_identical_bytes_gives_identical_pin_and_snapshot(tmp_path):
    a, b = _csv(tmp_path, ROW_A, ROW_B, name="a.csv"), _csv(tmp_path, ROW_A, ROW_B, name="b.csv")
    assert calendar_input_descriptor(a) == calendar_input_descriptor(b)
    assert CalendarFeatures.load(a).raw == CalendarFeatures.load(b).raw

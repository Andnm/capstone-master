"""GPT review vong 2 (R2-M1, R2-m3) tren MySQL that: danh tinh ma truoc moi step, ma doi o step cuoi khong PASS, gioi report va cleanup validation.

Chay: `ML_SMOKE=1 pytest -m mysql tests/test_b5_mysql.py` - CHI khi operational DB khong con run queued/running (no-overlap gate); dung warehouse fixture
dung rieng (`warehouse_edafx*`), khong cham warehouse that.
"""
from __future__ import annotations

import json

import pytest

from dataset_builder import bundle, code_identity, config as cfg, manifest, runner
from dataset_builder.cleanup import cleanup_from
from dataset_builder.db import connect, execute
from dataset_builder.manifest import STEPS

from helpers import drop_dataset, make_dataset, rows, steps_with

pytestmark = pytest.mark.mysql


@pytest.fixture()
def ds(dataset_wh):
    database, version = make_dataset(dataset_wh)
    try:
        yield database, version
    finally:
        drop_dataset(database, version)


def _drifted(monkeypatch):
    """Gia lap 'doi mot byte mot dependency backend sau init': manifest hien tai co hash khac o dung 1 file backend."""
    real = code_identity.builder_code_manifest()
    target = next(name for name in real["files"] if "/backend/app/" in name)
    drifted = {**real, "files": {**real["files"], target: "f" * 64}, "code_sha256": "e" * 64}
    monkeypatch.setattr(runner, "verify_code_identity", lambda config: code_identity.verify_code_identity(config, current=lambda: drifted))
    return target


def test_changed_backend_dependency_after_init_fails_apply_before_any_step_or_write(dataset_wh, ds, tmp_path, monkeypatch):
    database, version = ds
    called: list[str] = []
    registry = {name: (lambda ctx, n=name: called.append(n) or {}) for name in STEPS}
    target = _drifted(monkeypatch)
    with pytest.raises(code_identity.CodeIdentityError, match="DA DOI") as raised:
        runner.apply(database, version, steps=registry, output_root=tmp_path)
    assert target in str(raised.value) or "changed=" in str(raised.value)
    assert called == []                                                                    # khong step nao duoc goi
    row = rows(database, "SELECT status, last_completed_step, active_step, active_step_attempt FROM dataset_build_manifests WHERE dataset_version=%s", (version,))[0]
    assert row["last_completed_step"] == "initialized" and row["active_step"] is None and row["active_step_attempt"] == 0
    for table in ("ml_reference_assignments", "ml_item_reference_matches", "ml_samples"):
        assert rows(database, f"SELECT COUNT(*) n FROM {table} WHERE dataset_version=%s", (version,))[0]["n"] == 0
    with pytest.raises(code_identity.CodeIdentityError):                                   # rebuild_from/retry cung bi chan truoc cleanup
        runner.rebuild_from(database, version, "causal_references", steps=registry, output_root=tmp_path)


def test_config_pins_current_manifest_and_unchanged_code_runs(dataset_wh, ds, tmp_path):
    database, version = ds
    with connect(database) as conn:
        config = manifest.verify_manifest(conn, manifest.load_manifest(conn, version))
    assert config["builder_code"]["code_sha256"] == code_identity.builder_code_manifest()["code_sha256"]
    final = runner.apply(database, version, steps=steps_with(), output_root=tmp_path, stop_after="causal_references")
    assert final["last_completed_step"] == "causal_references"


def test_code_change_during_last_step_blocks_pass(dataset_wh, ds, tmp_path, monkeypatch):
    database, version = ds
    monkeypatch.setattr(runner, "_publish_reports_bundle", lambda ctx: None)

    def validation_then_drift(ctx):
        _drifted(monkeypatch)                                                              # doi ma NGAY SAU khi validation tinh xong
        return {"ok": True, "failed": []}

    registry = steps_with({"validation": validation_then_drift})
    with pytest.raises(code_identity.CodeIdentityError):
        runner.apply(database, version, steps=registry, output_root=tmp_path)
    row = rows(database, "SELECT status, fail_reason, last_completed_step, active_step FROM dataset_build_manifests WHERE dataset_version=%s", (version,))[0]
    assert row["status"] == "fail" and "DA DOI" in row["fail_reason"] and row["active_step"] == "validation" and row["last_completed_step"] != "validation"


def _stored(database: str, version: str) -> dict:
    value = rows(database, "SELECT output_parquet_sha256_json o FROM dataset_build_manifests WHERE dataset_version=%s", (version,))[0]["o"]
    return json.loads(value) if isinstance(value, str) else value


def test_publish_bundle_writes_db_checksums_and_verifies_then_cleanup_keeps_parquet(dataset_wh, ds, tmp_path):
    database, version = ds
    out = tmp_path / version
    out.mkdir()
    (out / "samples.parquet").write_bytes(b"PAR1")
    with connect(database) as conn:
        execute(conn, "UPDATE dataset_build_manifests SET output_parquet_sha256_json=%s WHERE dataset_version=%s",
                (json.dumps({"samples.parquet": {"file_sha256": "p" * 64}}), version))
        conn.commit()
        config = manifest.verify_manifest(conn, manifest.load_manifest(conn, version))
        ctx = runner.StepContext(database=database, dataset_version=version, conn=conn, config=config, output_root=tmp_path)
        for step in STEPS:
            ctx.save_report(step, {"step": step})
        runner._publish_reports_bundle(ctx)
    stored = _stored(database, version)
    assert {f"reports/{s}.json" for s in STEPS} | {"reports/REPORTS_MANIFEST.json", "samples.parquet"} == set(stored)
    assert bundle.verify_bundle(out_dir=out, stored=stored, dataset_version=version, build_config_sha256=cfg.config_sha256(config),
                                builder_code_sha256=config["builder_code"]["code_sha256"]) == []
    with connect(database) as conn:                                                        # retry validation: chi go san pham cua validation
        cleanup_from(conn, version, "validation", output_root=tmp_path)
    assert (out / "samples.parquet").read_bytes() == b"PAR1" and not (out / "reports" / "validation.json").exists()
    after = _stored(database, version)
    assert set(after) == {"samples.parquet"}                                               # muc reports/* bi go, Parquet con nguyen
    with connect(database) as conn:                                                        # features_labels cleanup van go ca thu muc
        cleanup_from(conn, version, "features_labels", output_root=tmp_path)
    assert not out.exists() and _stored(database, version) is None


def test_calendar_changed_after_init_fails_the_features_step_before_any_output(dataset_wh, ds, tmp_path, monkeypatch):
    """R3-M1: sua `vn_holidays.csv` sau init => features_labels FAIL (hash lech config), khong doc DB mau/khong ghi Parquet; version moi moi dung duoc lich moi."""
    from dataset_builder import env
    from dataset_builder.calendar_features import CalendarInputError
    from dataset_builder.steps import STEP_FUNCTIONS

    database, version = ds
    with connect(database) as conn:
        config = manifest.verify_manifest(conn, manifest.load_manifest(conn, version))
    assert config["calendar_input"]["sha256"] and config["calendar_input"]["name"] == "vn_holidays.csv"
    moved = tmp_path / "vn_holidays.csv"
    moved.write_bytes(env.HOLIDAYS_CSV.read_bytes() + b"2030-01-01,x,x,public_holiday,national,,0,confirmed,u\n")
    monkeypatch.setattr(env, "HOLIDAYS_CSV", moved)
    registry = steps_with({"features_labels": STEP_FUNCTIONS["features_labels"]})
    with pytest.raises(CalendarInputError, match="DA DOI"):
        runner.apply(database, version, steps=registry, output_root=tmp_path / "out")
    row = rows(database, "SELECT status, fail_reason, active_step FROM dataset_build_manifests WHERE dataset_version=%s", (version,))[0]
    assert row["status"] == "fail" and "DA DOI" in row["fail_reason"] and row["active_step"] == "features_labels"
    assert not (tmp_path / "out" / version / "samples.parquet").exists() and not (tmp_path / "out" / version / "inputs").exists()

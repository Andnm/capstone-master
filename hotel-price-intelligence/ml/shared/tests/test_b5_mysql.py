"""GPT review vong 2 (R2-M1, R2-m3) tren MySQL that: danh tinh ma truoc moi step, ma doi o step cuoi khong PASS, gioi report va cleanup validation.

Chay: `ML_SMOKE=1 pytest -m mysql tests/test_b5_mysql.py` - CHI khi operational DB khong con run queued/running (no-overlap gate); dung warehouse fixture
dung rieng (`warehouse_edafx*`), khong cham warehouse that.
"""
from __future__ import annotations

import json

import pytest

from dataset_builder import bundle, code_identity, config as cfg, manifest, runner
from dataset_builder.cleanup import cleanup_from
from dataset_builder.db import connect, execute, fetch_all
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


# ----------------------------------------------------------------- rehearsal 06/10: gan nhan phai chay duoc o quy mo that (khong chi vai chuc dong)
_SCRATCH_DDL = """CREATE TABLE scratch_ml_samples (
  id BIGINT AUTO_INCREMENT PRIMARY KEY, dataset_version VARCHAR(40) NOT NULL, record_id BIGINT NOT NULL, ml_reference_assignment_id BIGINT NOT NULL,
  vn_observation_date DATE NOT NULL, is_daily_snapshot_selected BOOLEAN NOT NULL DEFAULT TRUE,
  has_label_h1 BOOLEAN NOT NULL DEFAULT FALSE, label_source_record_id_h1 BIGINT,
  has_label_h3 BOOLEAN NOT NULL DEFAULT FALSE, label_source_record_id_h3 BIGINT,
  has_label_h7 BOOLEAN NOT NULL DEFAULT FALSE, label_source_record_id_h7 BIGINT,
  has_label_h14 BOOLEAN NOT NULL DEFAULT FALSE, label_source_record_id_h14 BIGINT,
  UNIQUE KEY uq (dataset_version, record_id), INDEX idx_daily (dataset_version, ml_reference_assignment_id, vn_observation_date)) ENGINE=InnoDB"""


def _fill_scratch(conn, series: int, days: int) -> None:
    import datetime as dt

    from dataset_builder.db import executemany

    execute(conn, "DROP TABLE IF EXISTS scratch_ml_samples")
    execute(conn, _SCRATCH_DDL)
    cursor = conn.cursor()
    cursor.execute("ANALYZE TABLE scratch_ml_samples")           # thong ke = bang rong (giong luc step vua nap du lieu trong cung transaction)
    cursor.fetchall()
    cursor.close()
    conn.commit()
    day0 = dt.date(2026, 9, 1)
    rows = [("dsx", s * days + d + 1, s + 1, day0 + dt.timedelta(days=d)) for s in range(series) for d in range(days)]
    executemany(conn, "INSERT INTO scratch_ml_samples (dataset_version, record_id, ml_reference_assignment_id, vn_observation_date) VALUES (%s,%s,%s,%s)", rows)


def test_label_build_runs_in_seconds_on_a_table_filled_in_the_same_transaction(dataset_wh):
    """Tai hien loi that: truoc day UPDATE self-join chay >30 phut/horizon o ~148k dong (thong ke index cu cua bang rong => full join). Gio phai < 60 s va dung."""
    import threading
    import time

    from dataset_builder.samples import _build_labels

    database = dataset_wh["warehouse_database"]
    series, days = 7400, 20
    with connect(database) as conn:
        try:
            _fill_scratch(conn, series, days)                     # CHUA commit: nap + gan nhan cung mot transaction nhu step that
            cid = scalar_one(conn, "SELECT CONNECTION_ID()")

            def kill():                                            # neu van chay lau => dung han de test fail ro rang, khong treo
                with connect(database) as killer:
                    execute(killer, f"KILL QUERY {int(cid)}")

            timer = threading.Timer(90, kill)
            timer.start()
            started = time.monotonic()
            try:
                labels = _build_labels(conn, "dsx", table="scratch_ml_samples")
            finally:
                timer.cancel()
            elapsed = time.monotonic() - started
            assert elapsed < 60, f"gan nhan mat {elapsed:.1f}s (qua cham)"
            assert labels == {"h1": series * (days - 1), "h3": series * (days - 3), "h7": series * (days - 7), "h14": series * (days - 14)}
            # nhan tro dung snapshot cua cung series o ngay + k (record_id = series_idx * days + day + 1)
            sample = rows_of(conn, "SELECT record_id, label_source_record_id_h1 h1, label_source_record_id_h3 h3, label_source_record_id_h7 h7, "
                                   "label_source_record_id_h14 h14 FROM scratch_ml_samples WHERE ml_reference_assignment_id=5 AND vn_observation_date='2026-09-03'")[0]
            assert (sample["h1"], sample["h3"], sample["h7"], sample["h14"]) == (sample["record_id"] + 1, sample["record_id"] + 3, sample["record_id"] + 7,
                                                                                  sample["record_id"] + 14)
            last = rows_of(conn, "SELECT has_label_h1 a, has_label_h14 b FROM scratch_ml_samples WHERE ml_reference_assignment_id=1 AND vn_observation_date='2026-09-20'")[0]
            assert not last["a"] and not last["b"]                # ngay cuoi chuoi khong co nhan
        finally:
            conn.rollback()
            execute(conn, "DROP TABLE IF EXISTS scratch_ml_samples")
            conn.commit()


def test_label_source_primary_key_rejects_two_selected_snapshots_for_one_series_day(dataset_wh):
    import mysql.connector

    from dataset_builder.samples import _build_labels

    database = dataset_wh["warehouse_database"]
    with connect(database) as conn:
        try:
            _fill_scratch(conn, 3, 5)
            execute(conn, "INSERT INTO scratch_ml_samples (dataset_version, record_id, ml_reference_assignment_id, vn_observation_date) "
                          "VALUES ('dsx', 999999, 1, '2026-09-01')")        # snapshot thu hai cung (series, ngay) cung duoc chon
            with pytest.raises(mysql.connector.errors.IntegrityError):
                _build_labels(conn, "dsx", table="scratch_ml_samples")
            conn.rollback()
            leftover = rows_of(conn, "SHOW TABLES LIKE 'tmp_label_source'")
            assert leftover == []                                          # bang tam khong de lai sau loi
        finally:
            conn.rollback()
            execute(conn, "DROP TABLE IF EXISTS scratch_ml_samples")
            conn.commit()


def scalar_one(conn, sql):
    from dataset_builder.db import scalar
    return scalar(conn, sql)


def rows_of(conn, sql):
    from dataset_builder.db import fetch_all
    return fetch_all(conn, sql)


# ----------------------------------------------------------------- lat 1: hop dong horizon tren MySQL that
def test_single_horizon_build_writes_a_checked_contract_and_marks_other_horizons_not_evaluated(pipeline, tmp_path):
    from dataset_builder.steps import STEP_FUNCTIONS

    database, version = pipeline([], purge_gap_days=1, required_label_splits={"h1": ["train"]})              # evaluation_horizons = [1] (fixture 9 ngay)
    final = runner.apply(database, version, steps={name: STEP_FUNCTIONS[name] for name in STEP_FUNCTIONS}, output_root=tmp_path)
    assert final["status"] == "pass"
    out = tmp_path / version
    contract = json.loads((out / "dataset_contract.json").read_text(encoding="utf-8"))
    assert contract["evaluation_horizons"] == [1] and contract["purge_gap_days"] == 1 and contract["computed_label_horizons"] == [1, 3, 7, 14]
    assert contract["dataset_version"] == version and contract["purpose"] == "rehearsal"
    assert contract["sufficiency_status"] == {"h1": "exploratory", "h3": "not_evaluated", "h7": "not_evaluated", "h14": "not_evaluated"}
    row = rows(database, "SELECT split_train_end, split_validation_end, output_parquet_sha256_json o FROM dataset_build_manifests WHERE dataset_version=%s", (version,))[0]
    stored = json.loads(row["o"]) if isinstance(row["o"], str) else row["o"]
    assert "dataset_contract.json" in stored and stored["dataset_contract.json"] == json.loads((out / "output_checksums.json").read_text(encoding="utf-8"))["dataset_contract.json"]
    assert contract["split_plan"]["train_end"] == str(row["split_train_end"]) and contract["split_plan"]["validation_end"] == str(row["split_validation_end"])
    report = json.loads((tmp_path / "_reports" / version / "validation.json").read_text(encoding="utf-8"))
    names = {c["name"]: c["ok"] for c in report["checks"]}
    assert names["hop_dong_horizon_khop_config_va_sufficiency"] is True and names["official_evaluation_horizons_primary_eligible"] is True
    # purge hieu luc tren du lieu that: moi mau train label_usable_h1 co ngay + 1 < validation_start
    import pandas as pd
    frame = pd.read_parquet(out / "samples.parquet")
    plan = contract["split_plan"]
    train_usable = frame[(frame["split"] == "train") & frame["label_usable_h1"]]
    assert (pd.to_datetime(train_usable["vn_observation_date"]) + pd.Timedelta(days=1) < pd.Timestamp(plan["validation_start"])).all()


def test_official_build_fails_validation_unless_every_evaluation_horizon_is_primary_eligible(pipeline, tmp_path, monkeypatch):
    from dataset_builder import export
    from dataset_builder.steps import STEP_FUNCTIONS

    monkeypatch.setattr(runner, "assert_official_clean", lambda config: None)             # cay git dang dirty khi phat trien; chi kiem gate sufficiency
    monkeypatch.setattr(export, "assert_official_provenance", lambda purpose, state: None)
    database, version = pipeline([], purge_gap_days=1, purpose="official", required_label_splits={"h1": ["train"]})     # evaluation_horizons=[1]; fixture chi 9 ngay => exploratory
    with pytest.raises(runner.BuildFailedError, match="official_evaluation_horizons_primary_eligible"):
        runner.apply(database, version, steps={name: STEP_FUNCTIONS[name] for name in STEP_FUNCTIONS}, output_root=tmp_path)
    row = rows(database, "SELECT status, fail_reason FROM dataset_build_manifests WHERE dataset_version=%s", (version,))[0]
    assert row["status"] == "fail" and "official_evaluation_horizons_primary_eligible" in row["fail_reason"]
    assert not (tmp_path / "_reports" / version / "REPORTS_MANIFEST.json").exists()


def test_invalid_horizon_contract_in_a_stored_manifest_is_rejected_before_any_step(dataset_wh, ds, tmp_path):
    database, version = ds
    with connect(database) as conn:
        execute(conn, "UPDATE dataset_build_manifests SET build_config_json=JSON_SET(build_config_json,'$.evaluation_horizons',JSON_ARRAY(7)) WHERE dataset_version=%s", (version,))
        conn.commit()
    called: list[str] = []
    registry = {name: (lambda ctx, n=name: called.append(n) or {}) for name in STEPS}
    with pytest.raises(manifest.ManifestError, match="hop dong horizon|build_config_sha256"):
        runner.apply(database, version, steps=registry, output_root=tmp_path)
    assert called == []


# ----------------------------------------------------------------- policy N1 (GPT file 54/56): ghim bang chung, loai hotel qua moi regime, official bat buoc
@pytest.fixture()
def n1_dir(tmp_path, monkeypatch):
    """Policy N1 rieng cho fixture (hotel `h1` bi loai) + bang chung that, dat lam POLICY_DIR de runner/export doc dung ban nay."""
    import shutil

    from dataset_builder import n1_policy

    target = tmp_path / "n1_policy_dir"
    shutil.copytree(n1_policy.POLICY_DIR, target)
    path = target / n1_policy.POLICY_FILE
    policy = json.loads(path.read_text(encoding="utf-8"))
    policy["excluded_hotels"] = ["h1"]
    path.write_text(json.dumps(policy, ensure_ascii=False, indent=2, sort_keys=True), encoding="utf-8")
    monkeypatch.setattr(n1_policy, "POLICY_DIR", target)
    return target


def test_n1_policy_excludes_the_hotel_everywhere_and_pins_evidence_into_the_artifact(pipeline, tmp_path, n1_dir):
    import pandas as pd

    from dataset_builder import n1_policy
    from dataset_builder.validation import validate_dataset

    descriptor = n1_policy.policy_descriptor(n1_dir / n1_policy.POLICY_FILE)
    database, version = pipeline([], purge_gap_days=1, required_label_splits={"h1": ["train"]}, n1_policy=descriptor)
    runner.apply(database, version, steps={name: __import__("dataset_builder.steps", fromlist=["STEP_FUNCTIONS"]).STEP_FUNCTIONS[name] for name in STEPS}, output_root=tmp_path)
    out = tmp_path / version
    frame = pd.read_parquet(out / "samples.parquet")
    assert "h1" not in set(frame["hotel_id"]) and len(frame) > 0
    contract = json.loads((out / "dataset_contract.json").read_text(encoding="utf-8"))
    assert contract["n1_policy"] == {"policy_version": descriptor["policy_version"], "policy_sha256": descriptor["policy_sha256"], "excluded_hotels": ["h1"]}
    checksums = json.loads((out / "output_checksums.json").read_text(encoding="utf-8"))
    for name in [n1_policy.POLICY_FILE, n1_policy.INPUT_MANIFEST, *(e["name"] for e in descriptor["evidence"])]:
        assert f"{n1_policy.SNAPSHOT_SUBDIR}/{name}" in checksums and (out / n1_policy.SNAPSHOT_SUBDIR / name).is_file()
        if name != n1_policy.INPUT_MANIFEST:                                                   # manifest dau vao chi co o artifact; con lai phai la NGUYEN bytes cua nguon
            assert (out / n1_policy.SNAPSHOT_SUBDIR / name).read_bytes() == (n1_dir / name).read_bytes()
    with connect(database) as conn:
        row = manifest.load_manifest(conn, version)
        config = manifest.verify_manifest(conn, row)
        conn.commit()
        report = validate_dataset(conn, dataset_version=version, config=config, manifest=row, output_root=tmp_path)
    check = next(c for c in report["checks"] if c["name"] == "n1_policy_ghim_bang_chung_va_loai_tru")
    assert report["ok"] is True and check["ok"] is True and check["detail"]["excluded_hotels"] == ["h1"]
    assert "h1" in config["eligibility_overrides"]["exclude_hotels"] and config["eligibility_overrides"]["sources"]["n1_policy"] == ["h1"]


def test_n1_evidence_changed_after_init_fails_before_any_step_or_write(pipeline, tmp_path, n1_dir):
    from dataset_builder import n1_policy

    descriptor = n1_policy.policy_descriptor(n1_dir / n1_policy.POLICY_FILE)
    database, version = pipeline([], purge_gap_days=1, required_label_splits={"h1": ["train"]}, n1_policy=descriptor)
    (n1_dir / "n1_affected_items.json").write_bytes(b"{}")                                   # bang chung doi SAU init
    called: list[str] = []
    registry = {name: (lambda ctx, n=name: called.append(n) or {}) for name in STEPS}
    with pytest.raises(n1_policy.N1PolicyError, match="lech hash|DA DOI"):
        runner.apply(database, version, steps=registry, output_root=tmp_path)
    assert called == []
    row = rows(database, "SELECT last_completed_step, active_step, active_step_attempt FROM dataset_build_manifests WHERE dataset_version=%s", (version,))[0]
    assert row["last_completed_step"] == "initialized" and row["active_step"] is None and row["active_step_attempt"] == 0
    for table in ("ml_reference_assignments", "ml_item_reference_matches", "ml_samples"):
        assert rows(database, f"SELECT COUNT(*) n FROM {table} WHERE dataset_version=%s", (version,))[0]["n"] == 0


def test_official_build_without_an_n1_policy_fails_validation(pipeline, tmp_path, monkeypatch):
    from dataset_builder import export
    from dataset_builder.steps import STEP_FUNCTIONS

    monkeypatch.setattr(runner, "assert_official_clean", lambda config: None)
    monkeypatch.setattr(export, "assert_official_provenance", lambda purpose, state: None)
    database, version = pipeline([], purge_gap_days=1, purpose="official", required_label_splits={"h1": ["train"]})
    with pytest.raises(runner.BuildFailedError, match="n1_policy_ghim_bang_chung_va_loai_tru"):
        runner.apply(database, version, steps={name: STEP_FUNCTIONS[name] for name in STEPS}, output_root=tmp_path)


def test_utc_connection_reads_timestamps_as_utc_even_when_a_raw_session_would_not(dataset_wh):
    """GPT file 60 C59-M2: FROM_UNIXTIME(0) phu thuoc time_zone cua session; phien raw o +07:00 cho 07:00:00 (cai bay da gay sai 7 gio), ket noi UTC da xac minh cho 00:00:00."""
    import datetime as dt

    from analysis.utc_connection import connect_utc_readonly

    database = dataset_wh["warehouse_database"]
    cursor, conn = connect_utc_readonly(database)
    try:
        cursor.execute("SELECT @@session.time_zone AS tz, FROM_UNIXTIME(0) AS epoch")
        row = cursor.fetchone()
        assert row["tz"] == "+00:00" and row["epoch"] == dt.datetime(1970, 1, 1, 0, 0, 0)
        cursor.execute("SELECT @@session.transaction_read_only AS ro")
        assert int(cursor.fetchone()["ro"]) == 1
    finally:
        conn.close()
    with connect(database) as raw:                                                            # phien raw: tu dat +07:00 de chung minh cai bay
        execute(raw, "SET SESSION time_zone = '+07:00'")
        assert fetch_all(raw, "SELECT FROM_UNIXTIME(0) AS epoch")[0]["epoch"] == dt.datetime(1970, 1, 1, 7, 0, 0)

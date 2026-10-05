"""B1 tren MySQL that (fixture warehouse build bang `build_warehouse()` that): init manifest, causal references,
state machine (crash/resume/circuit-breaker/rebuild-from/lock/heartbeat). Chay: `ML_SMOKE=1 pytest -m mysql`."""
from __future__ import annotations

import datetime as dt
import time

import pytest

from dataset_builder import manifest, runner
from dataset_builder.causal_references import build_causal_references
from dataset_builder.db import connect, execute, scalar
from dataset_builder.steps import STEP_FUNCTIONS

from helpers import drop_dataset, make_dataset, rows, steps_with

pytestmark = pytest.mark.mysql
T = dt.datetime


@pytest.fixture()
def ds(dataset_wh):
    database, version = make_dataset(dataset_wh)
    try:
        yield database, version
    finally:
        drop_dataset(database, version)


def test_init_writes_immutable_config_and_rejects_duplicate(dataset_wh, ds):
    database, version = ds
    with connect(database) as conn:
        row = manifest.load_manifest(conn, version)
        config = manifest.verify_manifest(conn, row)
        assert row["status"] == "running" and row["last_completed_step"] == "initialized" and row["active_step"] is None
        assert row["anomaly_registry_mode"] == "evaluation_asof" and row["anomaly_registry_file_sha256"]
        assert config["reference_quality_gate"]["min_runs"] == 3
        with pytest.raises(manifest.ManifestError):
            manifest.init_dataset_build(conn, dataset_version=version, config=config)
        with pytest.raises(manifest.ManifestError):
            manifest.init_dataset_build(conn, dataset_version="bad version", config=config)


def test_tampered_config_is_rejected(dataset_wh, ds):
    database, version = ds
    with connect(database) as conn:
        execute(conn, "UPDATE dataset_build_manifests SET purge_gap_days=21, build_config_json=JSON_SET(build_config_json,'$.purge_gap_days',21) "
                      "WHERE dataset_version=%s", (version,))
        conn.commit()
        with pytest.raises(manifest.ManifestError, match="build_config_sha256"):
            manifest.verify_manifest(conn, manifest.load_manifest(conn, version))


def test_causal_assignments_and_integrity(dataset_wh, ds, tmp_path):
    database, version = ds
    steps = steps_with({"causal_references": STEP_FUNCTIONS["causal_references"]})
    runner.apply(database, version, steps=steps, output_root=tmp_path, stop_after="causal_references")
    got = rows(database, "SELECT hotel_id, checkin_date, approved_at, approving_run_warehouse_id, approving_item_warehouse_id, "
                         "evidence_run_count, evidence_item_count, eligible_item_count, coverage, reference_algorithm_version "
                         "FROM ml_reference_assignments WHERE dataset_version=%s ORDER BY hotel_id", (version,))
    assert [r["hotel_id"] for r in got] == ["h1", "h4", "h5"]        # h2 non-unique, h3 chi 2 run
    for r in got:
        assert r["approved_at"] == T(2026, 9, 3, 10, 30)
        assert r["evidence_run_count"] == 3 and float(r["coverage"]) == 1.0
        assert r["reference_algorithm_version"] == "warehouse-causal-1.0.0"
    # spec muc 4: approving item thuoc dung run/hotel/check-in cua assignment
    bad = rows(database, "SELECT COUNT(*) n FROM ml_reference_assignments a JOIN crawl_run_items cri ON cri.id=a.approving_item_warehouse_id "
                         "WHERE a.dataset_version=%s AND (cri.crawl_run_id<>a.approving_run_warehouse_id OR cri.hotel_id<>a.hotel_id "
                         "OR cri.checkin_date<>a.checkin_date)", (version,))[0]["n"]
    assert bad == 0
    # run duyet = run nguon so 3
    run_map = rows(database, "SELECT rm.source_run_id FROM etl_run_map rm JOIN ml_reference_assignments a ON a.approving_run_warehouse_id=rm.warehouse_run_id "
                             "WHERE a.dataset_version=%s", (version,))
    assert {r["source_run_id"] for r in run_map} == {3}


def test_causal_vs_full_history_reference(dataset_wh, ds, tmp_path):
    """Causal duyet SOM hon/khac full-history co chu dich: h4 (A chi 5/12 run) chi duoc duyet o causal."""
    database, version = ds
    steps = steps_with({"causal_references": STEP_FUNCTIONS["causal_references"]})
    runner.apply(database, version, steps=steps, output_root=tmp_path, stop_after="causal_references")
    causal = {r["hotel_id"] for r in rows(database, "SELECT hotel_id FROM ml_reference_assignments WHERE dataset_version=%s", (version,))}
    full = {r["hotel_id"] for r in rows(database, "SELECT hotel_id FROM hotel_reference_rooms WHERE status='approved'")}
    assert full <= causal and "h4" in causal - full


def test_crash_mid_step_cleans_and_resumes(dataset_wh, ds, tmp_path):
    database, version = ds

    def crash_after_write(ctx):
        build_causal_references(ctx.conn, dataset_version=ctx.dataset_version, config=ctx.config)
        raise RuntimeError("boom")

    with pytest.raises(RuntimeError, match="boom"):
        runner.apply(database, version, steps=steps_with({"causal_references": crash_after_write}), output_root=tmp_path)
    row = rows(database, "SELECT status, active_step, active_step_attempt, fail_reason, last_completed_step FROM dataset_build_manifests "
                         "WHERE dataset_version=%s", (version,))[0]
    assert row["status"] == "fail" and row["active_step"] == "causal_references" and row["active_step_attempt"] == 1
    assert "boom" in row["fail_reason"] and row["last_completed_step"] == "initialized"
    partial = rows(database, "SELECT COUNT(*) n FROM ml_reference_assignments WHERE dataset_version=%s", (version,))[0]["n"]
    assert partial == 3                                               # output do dang con do lai
    final = runner.apply(database, version, steps=steps_with({"causal_references": STEP_FUNCTIONS["causal_references"]}),
                         output_root=tmp_path, stop_after="causal_references")
    assert final["last_completed_step"] == "causal_references" and final["active_step"] is None
    assert rows(database, "SELECT COUNT(*) n FROM ml_reference_assignments WHERE dataset_version=%s", (version,))[0]["n"] == 3


def test_circuit_breaker_and_retry_override(dataset_wh, ds, tmp_path):
    database, version = ds
    calls = []

    def always_fail(ctx):
        calls.append(1)
        raise RuntimeError("nguyen nhan chua sua")

    registry = steps_with({"causal_references": always_fail})
    for _ in range(3):                                                # max_step_attempts = 3
        with pytest.raises(RuntimeError, match="nguyen nhan"):
            runner.apply(database, version, steps=registry, output_root=tmp_path)
    assert len(calls) == 3
    with pytest.raises(runner.CircuitOpenError):                      # lan thu 4: khong cleanup, khong chay lai
        runner.apply(database, version, steps=registry, output_root=tmp_path)
    assert len(calls) == 3
    row = rows(database, "SELECT status, fail_reason, active_step, active_step_attempt FROM dataset_build_manifests WHERE dataset_version=%s", (version,))[0]
    assert row["status"] == "fail" and "circuit-breaker" in row["fail_reason"] and row["active_step"] == "causal_references"
    with pytest.raises(ValueError):
        runner.retry_failed_step(database, version, reason="", actor="claude", steps=registry, output_root=tmp_path)
    ok = {"causal_references": STEP_FUNCTIONS["causal_references"]}
    final = runner.retry_failed_step(database, version, reason="da sua nguyen nhan", actor="claude",
                                     steps=steps_with(ok), output_root=tmp_path, stop_after="causal_references")
    assert final["last_completed_step"] == "causal_references" and final["active_step"] is None
    with connect(database) as conn:
        overrides = manifest.load_manifest(conn, version)["retry_overrides_json"]
    assert len(overrides) == 1 and overrides[0]["reason"] == "da sua nguyen nhan" and overrides[0]["actor"] == "claude"
    assert overrides[0]["previous_attempts"] == 3 and overrides[0]["step"] == "causal_references"


def test_rebuild_from_resets_downstream(dataset_wh, ds, tmp_path):
    database, version = ds
    steps = steps_with({"causal_references": STEP_FUNCTIONS["causal_references"]})
    runner.apply(database, version, steps=steps, output_root=tmp_path, stop_after="item_matches")
    assert rows(database, "SELECT last_completed_step s FROM dataset_build_manifests WHERE dataset_version=%s", (version,))[0]["s"] == "item_matches"
    final = runner.rebuild_from(database, version, "causal_references", steps=steps, output_root=tmp_path, stop_after="causal_references")
    assert final["last_completed_step"] == "causal_references"
    assert rows(database, "SELECT COUNT(*) n FROM ml_reference_assignments WHERE dataset_version=%s", (version,))[0]["n"] == 3
    with pytest.raises(ValueError):
        runner.rebuild_from(database, version, "not_a_step", steps=steps, output_root=tmp_path)


def test_rebuild_from_refuses_when_active_step_exists(dataset_wh, ds, tmp_path):
    database, version = ds
    with pytest.raises(RuntimeError):
        runner.apply(database, version, steps=steps_with({"causal_references": lambda ctx: (_ for _ in ()).throw(RuntimeError("x"))}),
                     output_root=tmp_path)
    with pytest.raises(manifest.ManifestError, match="active_step"):
        runner.rebuild_from(database, version, "causal_references", steps=steps_with(), output_root=tmp_path)


def test_advisory_lock_blocks_second_builder(dataset_wh, ds, tmp_path):
    database, version = ds
    with connect(database) as other:
        assert scalar(other, "SELECT GET_LOCK(%s, 0)", (runner.lock_name(version),)) == 1
        with pytest.raises(runner.LockHeldError):
            runner.apply(database, version, steps=steps_with(), output_root=tmp_path)
        assert runner.describe(database, version)["lock_held"] is True
    assert runner.describe(database, version)["lock_held"] is False


def test_heartbeat_advances_during_long_step(dataset_wh, ds, tmp_path):
    database, version = ds
    seen = {}

    def slow(ctx):
        before = rows(database, "SELECT active_step_heartbeat_at h FROM dataset_build_manifests WHERE dataset_version=%s", (version,))[0]["h"]
        time.sleep(1.6)
        after = rows(database, "SELECT active_step_heartbeat_at h FROM dataset_build_manifests WHERE dataset_version=%s", (version,))[0]["h"]
        seen["before"], seen["after"] = before, after
        return {}

    runner.apply(database, version, steps=steps_with({"causal_references": slow}), output_root=tmp_path,
                 stop_after="causal_references", heartbeat_seconds=0.5)
    assert seen["after"] > seen["before"]


def test_stale_detection_requires_stale_heartbeat_and_no_lock(dataset_wh, ds, tmp_path):
    database, version = ds
    with connect(database) as conn:
        manifest.start_step(conn, version, "causal_references", now=T(2026, 10, 1, 0, 0, 0))   # heartbeat rat cu
    state = runner.describe(database, version)
    assert state["heartbeat_stale"] is True and state["lock_held"] is False and state["recoverable"] is True

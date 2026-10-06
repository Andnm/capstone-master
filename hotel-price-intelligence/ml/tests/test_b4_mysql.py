"""B4 tren MySQL that: validation + PASS gate + no-op khi PASS + phat hien vi pham chen vao. Chay: `ML_SMOKE=1 pytest -m mysql`."""
from __future__ import annotations

import json

import pytest

from dataset_builder import manifest, runner
from dataset_builder.db import connect, execute
from dataset_builder.steps import STEP_FUNCTIONS
from dataset_builder.validation import validate_dataset

from helpers import rows, steps_with

pytestmark = pytest.mark.mysql
ALL = {name: STEP_FUNCTIONS[name] for name in STEP_FUNCTIONS}
TRAIN_ONLY = {"h1": ["train"]}


def _validate(database, version, tmp_path):
    with connect(database) as conn:
        row = manifest.load_manifest(conn, version)
        config = manifest.verify_manifest(conn, row)
        conn.commit()
        return validate_dataset(conn, dataset_version=version, config=config, manifest=row, output_root=tmp_path)


def _failed(report):
    return set(report["failed"])


@pytest.fixture()
def passed(pipeline, tmp_path):
    database, version = pipeline([], purge_gap_days=1, required_label_splits=TRAIN_ONLY)
    final = runner.apply(database, version, steps=ALL, output_root=tmp_path)
    assert final["status"] == "pass"
    return database, version, tmp_path


def test_full_pipeline_passes_and_every_check_ok(passed):
    database, version, tmp_path = passed
    row = rows(database, "SELECT status, last_completed_step, active_step, finished_at, split_train_end, split_validation_end, "
                         "library_versions_json, output_parquet_sha256_json, anomaly_source_member_checksums FROM dataset_build_manifests "
                         "WHERE dataset_version=%s", (version,))[0]
    assert row["status"] == "pass" and row["last_completed_step"] == "validation" and row["active_step"] is None and row["finished_at"]
    report = json.loads((tmp_path / "_reports" / version / "validation.json").read_text(encoding="utf-8"))
    assert report["ok"] is True and report["failed"] == []
    names = {c["name"] for c in report["checks"]}
    assert {"sample_la_observation_da_match_chon", "label_source_target_khong_hop_le", "hash_noi_dung_khop", "split_khong_rong"} <= names
    assert all(c["ok"] for c in report["checks"])


def test_apply_on_pass_is_noop_and_detects_tampered_output(passed):
    database, version, tmp_path = passed
    before = rows(database, "SELECT finished_at f, last_step_finished_at l FROM dataset_build_manifests WHERE dataset_version=%s", (version,))[0]
    runner.apply(database, version, steps=ALL, output_root=tmp_path)                 # no-op: khong chay lai step nao
    assert rows(database, "SELECT finished_at f, last_step_finished_at l FROM dataset_build_manifests WHERE dataset_version=%s", (version,))[0] == before
    parquet = tmp_path / version / "samples.parquet"
    data = bytearray(parquet.read_bytes())
    data[len(data) // 2] ^= 0xFF
    parquet.write_bytes(bytes(data))
    with pytest.raises(manifest.ManifestError, match="output khong con khop"):
        runner.apply(database, version, steps=ALL, output_root=tmp_path)


def test_required_label_gate_blocks_pass_without_lowering_standard(pipeline, tmp_path):
    database, version = pipeline([], purge_gap_days=1)                              # mac dinh bat buoc h1 o ca 3 split
    with pytest.raises(runner.BuildFailedError):
        runner.apply(database, version, steps=ALL, output_root=tmp_path)
    row = rows(database, "SELECT status, active_step, fail_reason FROM dataset_build_manifests WHERE dataset_version=%s", (version,))[0]
    assert row["status"] == "fail" and row["active_step"] == "validation" and "label_h1_bat_buoc_co_mau" in row["fail_reason"]


def test_validation_detects_injected_violations(passed):
    database, version, tmp_path = passed
    assert _validate(database, version, tmp_path)["ok"] is True
    with connect(database) as conn:
        # 1) vung purge bi gan split
        execute(conn, "UPDATE ml_samples SET split='train' WHERE dataset_version=%s AND split IS NULL LIMIT 1", (version,))
        conn.commit()
    assert "split_purge1_khong_null" in _failed(_validate(database, version, tmp_path)) or "split_purge2_khong_null" in _failed(_validate(database, version, tmp_path))
    with connect(database) as conn:
        execute(conn, "UPDATE ml_samples SET split=NULL WHERE dataset_version=%s AND split='train' AND vn_observation_date=(SELECT d FROM (SELECT MIN(vn_observation_date) d FROM ml_samples WHERE dataset_version=%s) x) LIMIT 1", (version, version))
        conn.commit()
    assert "split_train_sai" in _failed(_validate(database, version, tmp_path))
    with connect(database) as conn:
        # 2) approved_at sau quan sat -> sample truoc approved_at
        execute(conn, "UPDATE ml_reference_assignments SET approved_at='2026-09-30 00:00:00' WHERE dataset_version=%s LIMIT 1", (version,))
        conn.commit()
    assert "sample_truoc_approved_at" in _failed(_validate(database, version, tmp_path))
    with connect(database) as conn:
        # 3) lech prediction_time so voi observation
        execute(conn, "UPDATE ml_samples SET prediction_time=DATE_ADD(prediction_time, INTERVAL 1 HOUR) WHERE dataset_version=%s LIMIT 1", (version,))
        conn.commit()
    assert "vn_date_hoac_prediction_time_lech_observation" in _failed(_validate(database, version, tmp_path))


def test_strata_columns_follow_the_match_table_and_validation_detects_drift(passed):
    import pandas as pd

    from dataset_builder.feature_spec import HORIZONS

    database, version, tmp_path = passed
    frame = pd.read_parquet(tmp_path / version / "samples.parquet")
    assert frame["prediction_match_status"].isin(["exact", "alias"]).all()
    by_record = {r["selected_record_id"]: r["match_status"] for r in rows(database, "SELECT selected_record_id, match_status FROM ml_item_reference_matches WHERE dataset_version=%s", (version,))}
    got_prediction = dict(zip(frame["warehouse_record_id"], frame["prediction_match_status"]))
    assert all(got_prediction[rid] == by_record[rid] for rid in got_prediction)                                              # trang thai cua chinh sample
    for k in HORIZONS:
        has = frame[f"has_label_h{k}"].astype(bool)
        assert frame.loc[~has, f"label_match_status_h{k}"].isna().all() and frame.loc[has, f"label_match_status_h{k}"].isin(["exact", "alias"]).all()
        source = {r["record_id"]: r["label_source_record_id_h" + str(k)] for r in rows(
            database, f"SELECT record_id, label_source_record_id_h{k} FROM ml_samples WHERE dataset_version=%s AND is_daily_snapshot_selected=TRUE", (version,))}
        got = dict(zip(frame["warehouse_record_id"], frame[f"label_match_status_h{k}"]))
        for rid, target in source.items():                                                                                    # nhan cua dung target, NULL khi khong co nhan
            expected = None if target is None else by_record[target]
            assert (None if pd.isna(got[rid]) else got[rid]) == expected, (k, rid)
    assert _validate(database, version, tmp_path)["ok"] is True
    flipped = next(r for r in rows(database, "SELECT m.selected_record_id AS rid FROM ml_item_reference_matches m JOIN ml_samples s ON s.dataset_version=m.dataset_version "
                                             "AND s.record_id=m.selected_record_id WHERE m.dataset_version=%s AND m.match_status='exact' AND s.is_daily_snapshot_selected=TRUE LIMIT 1", (version,)))
    with connect(database) as conn:
        execute(conn, "UPDATE ml_item_reference_matches SET match_status='alias' WHERE dataset_version=%s AND selected_record_id=%s", (version, flipped["rid"]))
        conn.commit()
    assert "strata_audit_hop_le_va_khop_db" in _failed(_validate(database, version, tmp_path))                              # DB doi, Parquet khong => phat hien

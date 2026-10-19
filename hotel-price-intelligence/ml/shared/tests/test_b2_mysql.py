"""B2 tren MySQL that: item matching, samples, daily dedup, labels, anomaly replay as-of. Chay: `ML_SMOKE=1 pytest -m mysql`."""
from __future__ import annotations

import datetime as dt
import json

import pytest

from dataset_builder import anomaly, runner
from dataset_builder import samples as samples_mod
from dataset_builder.anomaly import AnomalyReplayError
from dataset_builder.db import connect, execute
from dataset_builder.steps import STEP_FUNCTIONS

from helpers import member_for as _member_for
from helpers import registry_event as _event
from helpers import rows, steps_with

pytestmark = pytest.mark.mysql
T = dt.datetime
REAL = {name: STEP_FUNCTIONS[name] for name in ("causal_references", "item_matches", "samples_labels")}


def _run(database, version, tmp_path, stop_after="samples_labels"):
    return runner.apply(database, version, steps=steps_with(REAL), output_root=tmp_path, stop_after=stop_after)


def test_matches_statuses(pipeline, tmp_path):
    database, version = pipeline([])
    _run(database, version, tmp_path, stop_after="item_matches")
    by = {(r["hotel_id"], r["match_status"]): r["n"] for r in rows(
        database, "SELECT a.hotel_id, m.match_status, COUNT(*) n FROM ml_item_reference_matches m JOIN ml_reference_assignments a "
                  "ON a.id=m.ml_reference_assignment_id WHERE m.dataset_version=%s GROUP BY 1,2", (version,))}
    assert by == {("h1", "exact"): 12, ("h4", "exact"): 5, ("h4", "unavailable"): 7, ("h5", "exact"): 11}   # h5 ngay 7 sold_out khong phai item success
    # spec muc 4: selected_record_id thuoc dung item cua match; unavailable <-> NULL
    bad = rows(database, "SELECT COUNT(*) n FROM ml_item_reference_matches m JOIN price_observations po ON po.record_id=m.selected_record_id "
                         "WHERE m.dataset_version=%s AND po.crawl_run_item_id<>m.crawl_run_item_id", (version,))[0]["n"]
    assert bad == 0


def test_samples_funnel_counts_and_labels(pipeline, tmp_path):
    database, version = pipeline([])
    final = _run(database, version, tmp_path)
    report = json.loads((tmp_path / "_reports" / version / "samples_labels.json").read_text(encoding="utf-8"))
    funnel = report["funnel"]
    assert funnel["matched_exact_alias"] == 28
    assert funnel["after_price_ok"] == 28 and funnel["after_not_before_approval"] == 19 and funnel["after_hotel_not_overridden"] == 19
    assert report["samples_inserted"] == 19 and report["samples_selected"] == 19 and report["samples_superseded"] == 0
    assert report["labels"] == {"h1": 15, "h3": 10, "h7": 4, "h14": 0}
    # mau dau tien cua moi chuoi la ngay 4 (quan sat 10:15 >= approved_at ngay 3 10:30; ngay 3 10:15 < 10:30 bi loai)
    first = rows(database, "SELECT a.hotel_id, MIN(s.vn_observation_date) d FROM ml_samples s JOIN ml_reference_assignments a "
                           "ON a.id=s.ml_reference_assignment_id WHERE s.dataset_version=%s GROUP BY 1 ORDER BY 1", (version,))
    assert {r["hotel_id"]: r["d"] for r in first} == {"h1": dt.date(2026, 9, 4), "h4": dt.date(2026, 9, 4), "h5": dt.date(2026, 9, 4)}
    # nhan tro dung target: gia h1 ngay k+1 = price_a(k+1)
    pair = rows(database, "SELECT s.vn_observation_date d, po.price_per_night p, tp.price_per_night tp FROM ml_samples s "
                          "JOIN price_observations po ON po.record_id=s.record_id JOIN price_observations tp ON tp.record_id=s.label_source_record_id_h1 "
                          "JOIN ml_reference_assignments a ON a.id=s.ml_reference_assignment_id WHERE s.dataset_version=%s AND a.hotel_id='h1' "
                          "ORDER BY s.vn_observation_date", (version,))
    assert [(int(r["p"]), int(r["tp"])) for r in pair] == [(100_000 + 10_000 * d, 100_000 + 10_000 * (d + 1)) for d in range(4, 12)]
    assert final["last_completed_step"] == "samples_labels"


def test_label_integrity_query_returns_zero(pipeline, tmp_path):
    """Query UNION ALL cua spec muc 4: source/target cung frozen series, dung target date, ca hai la daily snapshot da chon."""
    database, version = pipeline([])
    _run(database, version, tmp_path)
    parts = []
    for k in (1, 3, 7, 14):
        parts.append(f"SELECT dataset_version, ml_reference_assignment_id, vn_observation_date, is_daily_snapshot_selected AS sel, "
                     f"label_source_record_id_h{k} AS target_record_id, {k} AS horizon_days FROM ml_samples "
                     f"WHERE label_source_record_id_h{k} IS NOT NULL")
    sql = ("SELECT COUNT(*) n FROM (" + " UNION ALL ".join(parts) + ") labels JOIN ml_samples target ON target.dataset_version=labels.dataset_version "
           "AND target.record_id=labels.target_record_id WHERE labels.dataset_version=%s AND (target.ml_reference_assignment_id<>labels.ml_reference_assignment_id "
           "OR target.vn_observation_date<>DATE_ADD(labels.vn_observation_date, INTERVAL labels.horizon_days DAY) OR labels.sel<>TRUE "
           "OR target.is_daily_snapshot_selected<>TRUE)")
    assert rows(database, sql, (version,))[0]["n"] == 0


def test_anomaly_as_of_cutoff_excludes_only_decided_before_cutoff(pipeline, tmp_path):
    database = None
    # dung chung 1 warehouse: lay member truoc khi tao pipeline (record id cua h1 ngay 6 / ngay 8)
    from helpers import rows as _rows  # noqa: F401
    # tao pipeline rong de co `database`, lay member roi tao 2 dataset khac nhau voi cung registry
    database, version0 = pipeline([])
    m6 = _member_for(database, "h1", 6)
    m8 = _member_for(database, "h1", 8)
    events = [_event(1, "r1", "2026-09-04T00:00:00Z", [m6]), _event(2, "r2", "2026-10-10T00:00:00Z", [m8])]
    database, v_asof = pipeline(events, mode="evaluation_asof", cutoff=T(2026, 10, 5))
    _run(database, v_asof, tmp_path)
    days = lambda v: [r["d"].day for r in rows(database, "SELECT s.vn_observation_date d FROM ml_samples s JOIN ml_reference_assignments a "
                                               "ON a.id=s.ml_reference_assignment_id WHERE s.dataset_version=%s AND a.hotel_id='h1' ORDER BY 1", (v,))]
    assert days(v_asof) == [4, 5, 7, 8, 9, 10, 11, 12]                   # chi r1 (ngay 6) bi loai; r2 quyet dinh SAU cutoff
    stored = rows(database, "SELECT anomaly_source_member_checksums c FROM dataset_build_manifests WHERE dataset_version=%s", (v_asof,))[0]["c"]
    stored = json.loads(stored) if isinstance(stored, str) else stored
    assert stored["events_applied"] == 1 and stored["events_total"] == 2 and stored["excluded_record_count"] == 1
    database, v_full = pipeline(events, mode="retrospective_full", cutoff=None)
    _run(database, v_full, tmp_path)
    assert days(v_full) == [4, 5, 7, 9, 10, 11, 12]                      # ca hai quyet dinh
    # nhan khong noi qua mau bi loai: h1 ngay 5 -> h1 label dung ngay 6 co the khong con
    gone = rows(database, "SELECT has_label_h1 FROM ml_samples s JOIN ml_reference_assignments a ON a.id=s.ml_reference_assignment_id "
                          "WHERE s.dataset_version=%s AND a.hotel_id='h1' AND s.vn_observation_date=%s", (v_asof, dt.date(2026, 9, 5)))[0]["has_label_h1"]
    assert gone == 0


def test_registry_member_with_wrong_fingerprint_fails_closed(pipeline, tmp_path):
    database, v0 = pipeline([])
    bad = _member_for(database, "h1", 6, tamper=True)
    database, version = pipeline([_event(1, "r1", "2026-09-04T00:00:00Z", [bad])])
    with pytest.raises(AnomalyReplayError, match="fingerprint lech"):
        _run(database, version, tmp_path)
    assert rows(database, "SELECT COUNT(*) n FROM ml_samples WHERE dataset_version=%s", (version,))[0]["n"] == 0


def test_registry_file_change_after_init_is_rejected(pipeline, tmp_path):
    database, version = pipeline([])
    path = anomaly.registry_path()
    path.write_text(path.read_text(encoding="utf-8").replace('"events": []', '"events": [ ]'), encoding="utf-8")
    with pytest.raises(AnomalyReplayError, match="registry file da doi"):
        _run(database, version, tmp_path)


def test_daily_dedup_prefers_earlier_observation_and_marks_reason(pipeline, tmp_path):
    database, version = pipeline([])
    _run(database, version, tmp_path, stop_after="samples_labels")
    with connect(database) as conn:
        target = rows(database, "SELECT s.id, s.ml_reference_assignment_id a, s.vn_observation_date d, s.prediction_time t FROM ml_samples s "
                                "JOIN ml_reference_assignments x ON x.id=s.ml_reference_assignment_id WHERE s.dataset_version=%s AND x.hotel_id='h1' "
                                "AND s.vn_observation_date=%s", (version, dt.date(2026, 9, 6)))[0]
        extra = rows(database, "SELECT po.record_id FROM price_observations po WHERE po.hotel_id='h1' AND DATE(po.observed_at)=%s AND po.room_type_raw='B'",
                     (dt.date(2026, 9, 6),))[0]["record_id"]
        # chen mot dong DU cung (assignment, ngay VN) co prediction_time MUON hon - gia lap observation tu run cat qua nua dem
        execute(conn, "INSERT INTO ml_samples (dataset_version, record_id, ml_reference_assignment_id, prediction_time, vn_observation_date, "
                      "is_daily_snapshot_selected, created_at) VALUES (%s,%s,%s,%s,%s,TRUE,%s)",
                (version, extra, target["a"], target["t"] + dt.timedelta(hours=2), target["d"], T(2026, 10, 5)))
        conn.commit()
    batch = rows(database, "SELECT import_batch_id b FROM dataset_build_manifests WHERE dataset_version=%s", (version,))[0]["b"]
    with connect(database) as conn:
        samples_mod._daily_dedup(conn, version, batch)
        conn.commit()
    got = rows(database, "SELECT s.record_id, s.is_daily_snapshot_selected sel, s.daily_snapshot_reason r FROM ml_samples s "
                         "WHERE s.dataset_version=%s AND s.ml_reference_assignment_id=%s AND s.vn_observation_date=%s ORDER BY s.prediction_time",
               (version, target["a"], target["d"]))
    assert [(r["sel"], r["r"]) for r in got] == [(1, None), (0, "superseded_same_vn_day")]

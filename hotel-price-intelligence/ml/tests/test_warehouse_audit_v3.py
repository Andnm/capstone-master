"""Audit warehouse (audit_tools/warehouse_audit_v3.py): HAM THUAN + guard + tinh chat doc-chi cua SQL, voi negative control. KHONG co ket noi DB nao trong test."""
from __future__ import annotations

import datetime as dt
import importlib.util
import re
from pathlib import Path

import pytest

TOOLS = Path(__file__).resolve().parents[2] / "audit_tools"
spec = importlib.util.spec_from_file_location("warehouse_audit_v3", TOOLS / "warehouse_audit_v3.py")
wa = importlib.util.module_from_spec(spec)
spec.loader.exec_module(wa)
UTC = dt.timezone.utc


def vn(hour, minute=0):
    return dt.datetime(2026, 10, 8, hour, minute, tzinfo=wa.VN).astimezone(UTC)


# --------------------------------------------------------------------------- guard
@pytest.mark.parametrize("name,ok", [("warehouse_dsdev_20261004_3src", True), ("warehouse_dsdev_x", True), ("hotel_price_intel", False), ("warehouse_20261004_3src", False),
                                      ("warehouse_dsdev_", False), ("warehouse_dsdev_a;DROP", False), ("", False), ("warehouse_dsdev_a b", False), ("warehouse_dsdev_" + "x" * 80, False)])
def test_only_dsdev_clones_are_allowed_never_the_operational_or_promoted_warehouse(name, ok):
    assert wa.database_allowed(name) is ok


@pytest.mark.parametrize("hour,minute,ok", [(0, 29, True), (0, 30, False), (8, 0, False), (16, 59, False), (17, 0, True), (23, 59, True), (3, 15, False), (12, 0, False)])
def test_time_gate_excludes_the_crawl_window_0030_to_1700_vn(hour, minute, ok):
    assert wa.time_gate_ok(vn(hour, minute)) is ok


@pytest.mark.parametrize("text,ok", [
    ("Run #65: 4248/4248 (100.0%) — completed\nItem gần nhất: #1", True),
    ("Run #65: 3939/4248 (92.7%) — running", False),
    ("Run #65: 3/4 (75.0%) — completed", False),
    ("Run #65: 4248/4248 (100.0%) — failed", False),
    ("Không tìm thấy run.", False), ("", False)])
def test_crawler_gate_requires_a_terminal_completed_run_and_never_guesses(text, ok):
    assert wa.crawler_gate_ok(text)[0] is ok


def test_explain_guard_rejects_full_scans_and_accepts_keyed_plans():
    good = [{"table": "s", "type": "range"}, {"table": "a", "type": "eq_ref"}, {"table": "po", "type": "eq_ref"}]
    assert wa.explain_ok(good, driving="s")[0] is True
    assert wa.explain_ok([{"table": "s", "type": "ALL"}], driving="s")[0] is False
    assert wa.explain_ok([{"table": "s", "type": "range"}, {"table": "po", "type": "ALL"}], driving="s")[0] is False
    assert wa.explain_ok([{"table": "s", "type": "range"}, {"table": "a", "type": "index"}], driving="s")[0] is False
    assert wa.explain_ok([{"table": "s", "type": "index"}], driving="s", allow_index_scan=("s",))[0] is True          # Q3 index-only duoc phep CHI cho bang s
    assert wa.explain_ok([{"table": "s", "type": "range"}, {"table": "t", "type": "range"}], driving="s")[0] is False  # bang noi khong dung eq_ref/ref
    assert wa.explain_ok([], driving="s")[0] is False


# --------------------------------------------------------------------------- Q1..Q4 thuan
def _q1_row(**over):
    row = {"a_id": 1, "po_id": 2, "approved_at": dt.datetime(2026, 9, 1, 10), "observed_at": dt.datetime(2026, 9, 2, 10), "prediction_time": dt.datetime(2026, 9, 2, 10),
           "a_hotel": "h", "po_hotel": "h", "a_checkin": dt.date(2026, 9, 5), "po_checkin": dt.date(2026, 9, 5)}
    row.update(over)
    return row


def test_q1_detects_each_violation_including_null_timestamps_and_orphans():
    assert wa.q1_violations(_q1_row()) == []
    assert "observed_before_approval" in wa.q1_violations(_q1_row(observed_at=dt.datetime(2026, 8, 30), prediction_time=dt.datetime(2026, 8, 30)))
    assert "missing_assignment" in wa.q1_violations(_q1_row(a_id=None, approved_at=None, a_hotel=None, a_checkin=None))
    assert "missing_observation" in wa.q1_violations(_q1_row(po_id=None, observed_at=None, po_hotel=None, po_checkin=None))
    assert "approved_at_null" in wa.q1_violations(_q1_row(approved_at=None))
    assert "observed_at_null" in wa.q1_violations(_q1_row(observed_at=None))          # NULL khong duoc 'bien mat' (so sanh NULL trong SQL khong tra true)
    assert "prediction_time_ne_observed_at" in wa.q1_violations(_q1_row(prediction_time=dt.datetime(2026, 9, 2, 11)))
    assert "observation_series_ne_assignment_series" in wa.q1_violations(_q1_row(po_hotel="other"))
    assert "observation_series_ne_assignment_series" in wa.q1_violations(_q1_row(po_checkin=dt.date(2026, 9, 6)))


def _q2_row(**over):
    row = {"has_label": 1, "tgt_record": 10, "t_sample_id": 5, "t_selected": 1, "assignment": 7, "t_assignment": 7, "s_date": dt.date(2026, 9, 1), "t_date": dt.date(2026, 9, 2),
           "tp_id": 10, "tp_hotel": "h", "tp_checkin": dt.date(2026, 9, 5), "s_hotel": "h", "s_checkin": dt.date(2026, 9, 5)}
    row.update(over)
    return row


def test_q2_detects_label_link_violations_and_the_shifted_control_is_sensitive():
    assert wa.q2_violations(_q2_row(), 1) == []
    assert "has_label_ne_target_id" in wa.q2_violations(_q2_row(tgt_record=None), 1)
    assert "has_label_ne_target_id" in wa.q2_violations(_q2_row(has_label=0), 1)
    assert "target_sample_missing" in wa.q2_violations(_q2_row(t_sample_id=None), 1)
    assert "target_not_daily_selected" in wa.q2_violations(_q2_row(t_selected=0), 1)
    assert "target_other_assignment" in wa.q2_violations(_q2_row(t_assignment=8), 1)
    assert "target_date_not_source_plus_k" in wa.q2_violations(_q2_row(t_date=dt.date(2026, 9, 3)), 1)
    assert "target_observation_missing" in wa.q2_violations(_q2_row(tp_id=None), 1)
    assert "target_observation_other_series" in wa.q2_violations(_q2_row(tp_hotel="x"), 1)
    assert wa.q2_violations(_q2_row(has_label=0, tgt_record=None, t_sample_id=None), 1) == []
    rows = [_q2_row(), _q2_row(), _q2_row(has_label=0, tgt_record=None, t_sample_id=None, t_date=None)]
    assert wa.q2_sensitivity_count(rows, 1) == 2 and wa.q2_sensitivity_count(rows, 0) == 0                  # k=1: +k+1 (=2) lam ca hai dong co nhan bi phat hien; k=0 thi target +1 chinh la +k+1 nen 0
    assert wa.q2_sensitivity_count([_q2_row(t_date=dt.date(2026, 9, 3))], 1) == 0                            # dung khi target that su la +k+1


def _g(sel, ts, owner="owner_success", code="local_primary", run=1, item=1):
    return {"is_daily_snapshot_selected": sel, "prediction_time": dt.datetime(2026, 9, 1, ts), "ownership_status": owner, "source_code": code, "source_run_id": run, "source_item_id": item}


def test_q3_group_verdict_uses_the_full_tuple_and_controls_only_apply_to_strict_orders():
    good = wa.q3_group_verdict([_g(1, 8), _g(0, 9)])
    assert good["exactly_one_selected"] and good["selected_is_min_key"] and good["strict_order"] and good["reverse_order_would_pick_other"]
    assert wa.q3_group_verdict([_g(0, 8), _g(1, 9)])["selected_is_min_key"] is False                          # chon sai (muon hon) bi bat
    assert wa.q3_group_verdict([_g(1, 8), _g(1, 9)])["exactly_one_selected"] is False                         # hai selected
    assert wa.q3_group_verdict([_g(0, 8), _g(0, 9)])["exactly_one_selected"] is False                         # khong co selected
    owner_first = wa.q3_group_verdict([_g(1, 9, owner="owner_success"), _g(0, 8, owner="non_owner_duplicate")])   # owner_success thang ke ca khi muon hon
    assert owner_first["selected_is_min_key"] and owner_first["strict_order"]
    tie = wa.q3_group_verdict([_g(1, 8), _g(0, 8)])                                                           # moi khoa bang nhau: khong strict, khong ep control > 0
    assert tie["strict_order"] is False and tie["reverse_order_would_pick_other"] is False
    tiebreak = wa.q3_group_verdict([_g(1, 8, run=1, item=2), _g(0, 8, run=1, item=1)])                       # tie-break theo source_item_id tang dan: chon sai bi bat
    assert tiebreak["selected_is_min_key"] is False


def test_q4_fidelity_has_null_semantics_and_drift_is_only_counted():
    row = {"city": "Hà Nội", "a_max_occupancy": 2, "a_room_area": "20 m²", "a_breakfast_included": 1, "a_free_cancellation": 0}
    parquet = {"city": "Hà Nội", "max_occupancy": 2, "room_area_m2": 20.0, "breakfast_included": True, "free_cancellation": False}
    parse = lambda text: 20.0 if text == "20 m²" else None      # noqa: E731
    assert wa.q4_fidelity_violations(row, parquet, parse) == []
    assert wa.q4_fidelity_violations({**row, "city": "Đà Lạt"}, parquet, parse) == ["city"]
    assert wa.q4_fidelity_violations({**row, "a_max_occupancy": None}, parquet, parse) == ["max_occupancy"]
    assert wa.q4_fidelity_violations({**row, "a_max_occupancy": None}, {**parquet, "max_occupancy": float("nan")}, parse) == []          # NULL == NaN
    assert wa.q4_fidelity_violations({**row, "a_room_area": None}, {**parquet, "room_area_m2": float("nan")}, parse) == []
    assert wa.q4_fidelity_violations({**row, "a_breakfast_included": 0}, parquet, parse) == ["breakfast_included"]
    drift = wa.q4_drift({"a_max_occupancy": 2, "po_max_occupancy": 3, "a_room_area": None, "po_room_area": None, "a_breakfast_included": 1, "po_breakfast_included": None,
                         "a_free_cancellation": 0, "po_free_cancellation": 0})
    assert drift == ["max_occupancy", "breakfast_included"]


def test_step0_verdict_requires_pass_validation_batch_build_sha_and_parquet_hash():
    shas = {"ds_20261006_dev1b": "aa" * 32, "ds_20261006_dev3b": "bb" * 32}

    def manifest(name, **over):
        row = {"dataset_version": name, "import_batch_id": wa.BATCH_ID, "status": "pass", "last_completed_step": "validation", "active_step": None,
               "build_config_sha256": wa.DATASETS[name]["build_config_sha256"], "output_parquet_sha256_json": {"samples.parquet": shas[name]}}
        row.update(over)
        return row

    ok_rows = [manifest("ds_20261006_dev1b"), manifest("ds_20261006_dev3b")]
    assert wa.step0_verdict(ok_rows, shas)["all_ok"] is True
    assert wa.step0_verdict(ok_rows[:1], shas)["all_ok"] is False                                                         # thieu mot dataset
    for bad in ({"status": "running"}, {"last_completed_step": "split"}, {"active_step": "samples_labels"}, {"import_batch_id": "other"}, {"build_config_sha256": "0" * 64},
                {"output_parquet_sha256_json": {"samples.parquet": "cc" * 32}}):
        rows = [manifest("ds_20261006_dev1b", **bad), manifest("ds_20261006_dev3b")]
        assert wa.step0_verdict(rows, shas)["all_ok"] is False, bad
    assert wa.step0_verdict([], shas)["all_ok"] is False


# --------------------------------------------------------------------------- tinh chat SQL
SQL_CONSTANTS = {n: getattr(wa, n) for n in dir(wa) if n.startswith("SQL_")}


def test_every_sql_statement_is_a_single_select_without_writes_or_ddl():
    assert SQL_CONSTANTS
    for name, sql in SQL_CONSTANTS.items():
        lowered = sql.lower()
        assert lowered.lstrip().startswith("select"), name
        assert ";" not in sql, name
        assert not re.search(r"\b(insert|update|delete|replace|alter|create|drop|truncate|analyze|optimize|grant|set\s)\b", lowered), name


def test_dataset_scoped_queries_have_a_dataset_predicate_and_batched_queries_are_keyset_with_limit():
    for name in ("SQL_Q1", "SQL_Q2", "SQL_Q3_GROUPS", "SQL_Q3_ROWS", "SQL_Q4", "SQL_COUNT_SELECTED"):
        assert "dataset_version = %s" in SQL_CONSTANTS[name], name
    for name in ("SQL_Q1", "SQL_Q2", "SQL_Q4"):
        sql = SQL_CONSTANTS[name]
        assert f"LIMIT {wa.BATCH}" in sql and "ORDER BY s.record_id" in sql and "s.record_id > %s" in sql, name
    assert "LIMIT" in SQL_CONSTANTS["SQL_Q1_CONTROL"] and "m.crawl_run_item_id > %s" in SQL_CONSTANTS["SQL_Q1_CONTROL"] and wa.BATCH <= 1000 and wa.MAX_EXECUTION_MS <= 10_000
    for name in ("SQL_Q1", "SQL_Q2", "SQL_Q4"):
        assert "LEFT JOIN" in SQL_CONSTANTS[name] or name == "SQL_Q4"                                               # Q1/Q2 dung LEFT JOIN de dem mat dong; Q4 la JOIN co chu y (assignment/hotel bat buoc ton tai)


def test_q1_control_uses_the_funnel_prefix_stage_and_not_late_conditions():
    sql = SQL_CONSTANTS["SQL_Q1_CONTROL"]
    assert "match_status IN ('exact','alias')" in sql and "po.is_sold_out = 0" in sql and "po.price_per_night > 0" in sql
    for late in ("hotels", "N1", "registry", "city"):
        assert late.lower() not in sql.lower().replace("hotel", "")                                                 # khong ap N1/registry/city truoc khi tai hien burn-in


# --------------------------------------------------------------------------- main khong mo ket noi khi gate/approval that bai
def test_main_refuses_before_connecting_when_the_database_is_not_a_clone_or_approval_is_missing(monkeypatch, capsys):
    called = {"n": 0}
    monkeypatch.setattr(wa, "connect", lambda db: called.__setitem__("n", called["n"] + 1) or (_ for _ in ()).throw(AssertionError("khong duoc ket noi")))
    assert wa.main(["--db", "hotel_price_intel", "--step0", "--skip-time-gate", "--skip-crawler-gate"]) == 3              # DB van hanh bi chan boi guard
    assert wa.main(["--db", "warehouse_dsdev_20261004_3src", "--run", "q1", "--skip-time-gate", "--skip-crawler-gate"]) == 2   # --run khong co --gpt-approval
    assert called["n"] == 0
    monkeypatch.setattr(wa, "time_gate_ok", lambda now: False)
    assert wa.main(["--db", "warehouse_dsdev_20261004_3src", "--step0", "--skip-crawler-gate"]) == 3                   # trong khung crawl
    assert called["n"] == 0


def test_artifact_hashes_in_code_match_the_contract_build_shas_in_gpt_file_30():
    assert wa.DATASETS["ds_20261006_dev1b"]["build_config_sha256"].startswith("6494c1d4") and wa.DATASETS["ds_20261006_dev3b"]["build_config_sha256"].startswith("45f2fc6a")
    assert wa.EXPECTED_BURN_IN == 43662 and wa.EXPECTED_SELECTED == 146431 and wa.EXPECTED_LABELLED == {1: 128583, 3: 111515, 7: 83077, 14: 45049}

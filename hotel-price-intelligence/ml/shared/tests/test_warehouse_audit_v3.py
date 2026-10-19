"""Audit warehouse (audit_tools/warehouse_audit_v3.py): HAM THUAN + guard + wrapper truy van + verdict, voi negative control. KHONG co ket noi DB that: DB gia (FakeConn) tra loi theo noi dung SQL."""
from __future__ import annotations

import contextlib
import datetime as dt
import importlib.util
import io
import json
import re
import sys
from pathlib import Path

import pandas as pd
import pytest

TOOLS = Path(__file__).resolve().parents[3] / "audit_tools"
spec = importlib.util.spec_from_file_location("warehouse_audit_v3", TOOLS / "warehouse_audit_v3.py")
wa = importlib.util.module_from_spec(spec)
spec.loader.exec_module(wa)
ev_spec = importlib.util.spec_from_file_location("crawl_idle_evidence", TOOLS / "crawl_idle_evidence.py")
ev = importlib.util.module_from_spec(ev_spec)
ev_spec.loader.exec_module(ev)
UTC = dt.timezone.utc


def _builder_area_parser():
    """parse_room_area_m2 cua builder lay bang AST (khong import `dataset_builder.features` vi no nhap `env.py` tu load .env)."""
    import ast
    source = (Path(__file__).resolve().parents[1] / "dataset_builder" / "features.py").read_text(encoding="utf-8")
    wanted = {"_AREA_RE", "FT2_TO_M2", "parse_room_area_m2"}
    nodes = [node for node in ast.parse(source).body
             if (isinstance(node, ast.Assign) and any(isinstance(target, ast.Name) and target.id in wanted for target in node.targets)) or (isinstance(node, ast.FunctionDef) and node.name in wanted)]
    module = ast.fix_missing_locations(ast.Module(body=nodes, type_ignores=[]))
    namespace: dict = {"re": re}
    exec(compile(module, "features_ast", "exec"), namespace)                         # noqa: S102
    return namespace["parse_room_area_m2"]


AREA_PARSER = _builder_area_parser()


def vn(hour, minute=0):
    return dt.datetime(2026, 10, 8, hour, minute, tzinfo=wa.VN).astimezone(UTC)


# =========================================================================== guard thuan
@pytest.mark.parametrize("name,ok", [("warehouse_dsdev_20261004_3src", True), ("warehouse_dsdev_x", True), ("hotel_price_intel", False), ("warehouse_20261004_3src", False),
                                      ("warehouse_dsdev_", False), ("warehouse_dsdev_a;DROP", False), ("", False), ("warehouse_dsdev_a b", False), ("warehouse_dsdev_" + "x" * 80, False)])
def test_only_dsdev_clones_are_allowed_never_the_operational_or_promoted_warehouse(name, ok):
    assert wa.database_allowed(name) is ok


@pytest.mark.parametrize("hour,minute,ok", [(0, 29, True), (0, 30, False), (8, 0, False), (16, 59, False), (17, 0, True), (23, 59, True), (3, 15, False), (12, 0, False)])
def test_time_gate_excludes_the_crawl_window_0030_to_1700_vn(hour, minute, ok):
    assert wa.time_gate_ok(vn(hour, minute)) is ok


def _evidence(now=None, **over):
    now = now or vn(18, 0)
    base = ev.build_evidence(0, 0, {"id": 66, "status": "completed"}, now, session_read_only=True)
    base.update(over)
    return base


@pytest.mark.parametrize("mutate,ok", [
    ({}, True),
    ({"active_runs": 1}, False), ({"active_items": 3}, False), ({"active_runs": True}, False), ({"active_items": "0"}, False), ({"active_runs": None}, False),
    ({"schema": "other"}, False), ({"generated_at_utc": "yesterday"}, False), ({"latest_run_id": None}, False), ({"latest_run_id": True}, False),
    ({"expires_at_utc": "2026-10-09T11:10:00Z"}, False),                                  # tu khai han qua dai (>600 s)
    ({"session": {"read_only_verified": False}}, False), ({"session": None}, False), ({"session": {}}, False), ({"session": {"read_only_verified": 1}}, False),
])
def test_crawl_evidence_must_be_fresh_schema_valid_and_show_zero_active_runs_and_items(mutate, ok):
    now = vn(18, 5)
    assert wa.validate_crawl_evidence(_evidence(vn(18, 0), **mutate), now)[0] is ok


def test_crawl_evidence_expiry_future_and_garbage_are_rejected():
    assert wa.validate_crawl_evidence(_evidence(vn(18, 0)), vn(18, 11))[0] is False        # het han sau 10 phut
    ok, why = wa.validate_crawl_evidence(_evidence(vn(18, 0)), vn(18, 11))
    assert not ok and "da het han" in why                                                  # thong bao rieng cho "het han" va "con it han hon margin"
    ok, why = wa.validate_crawl_evidence(_evidence(vn(18, 0)), vn(18, 9), margin_s=61)
    assert not ok and "bien an toan" in why and "da het han" not in why
    assert wa.validate_crawl_evidence(_evidence(vn(18, 30)), vn(18, 0))[0] is False        # tu tuong lai
    for garbage in (None, [], "x", 7, {}):
        assert wa.validate_crawl_evidence(garbage, vn(18, 0))[0] is False


def test_evidence_must_leave_a_safety_margin_before_its_expiry():
    evidence = _evidence(vn(18, 0))                                                                          # het han luc 18:10:00
    assert wa.validate_crawl_evidence(evidence, vn(18, 9), margin_s=15)[0] is True
    assert wa.validate_crawl_evidence(evidence, vn(18, 9), margin_s=61)[0] is False                        # con < 61 s
    assert wa.validate_crawl_evidence(evidence, vn(18, 10), margin_s=0)[0] is True                         # dung luc het han: chua qua han
    assert wa.validate_crawl_evidence(evidence, vn(18, 10), margin_s=1)[0] is False
    assert [wa.gate_time_ok(vn(h, m)) for h, m in ((17, 0), (23, 40), (0, 15), (0, 25), (16, 55), (10, 0))] == [True, True, True, False, False, False]    # margin 10 phut truoc 00:30


def test_evidence_generator_output_is_accepted_by_the_controller_and_pins_the_queries():
    produced = ev.build_evidence(0, 0, None, vn(18, 0), host="h", session_read_only=True)
    assert wa.validate_crawl_evidence(produced, vn(18, 1))[0] is True
    assert wa.validate_crawl_evidence(ev.build_evidence(0, 0, None, vn(18, 0), host="h"), vn(18, 1))[0] is False   # khong chung minh READ ONLY (mac dinh)
    assert produced["queries"]["active_runs"] == ev.SQL_ACTIVE_RUNS and "queued" in ev.SQL_ACTIVE_ITEMS and "running" in ev.SQL_ACTIVE_ITEMS
    busy = ev.build_evidence(1, 4248, {"id": 65, "status": "running"}, vn(18, 0), session_read_only=True)
    assert wa.validate_crawl_evidence(busy, vn(18, 1))[0] is False


def test_explain_guard_rejects_full_scans_and_accepts_keyed_plans():
    good = [{"table": "s", "type": "range"}, {"table": "a", "type": "eq_ref"}, {"table": "po", "type": "eq_ref"}]
    assert wa.explain_ok(good, driving="s")[0] is True
    assert wa.explain_ok([{"table": "s", "type": "ALL"}], driving="s")[0] is False
    assert wa.explain_ok([{"table": "s", "type": "range"}, {"table": "po", "type": "ALL"}], driving="s")[0] is False
    assert wa.explain_ok([{"table": "s", "type": "range"}, {"table": "a", "type": "index"}], driving="s")[0] is False
    assert wa.explain_ok([{"table": "s", "type": "index"}], driving="s", allow_index_scan=("s",))[0] is True
    assert wa.explain_ok([{"table": "s", "type": "range"}, {"table": "t", "type": "range"}], driving="s")[0] is False
    assert wa.explain_ok([], driving="s")[0] is False


# =========================================================================== duyet cua GPT
def _approval_text(controller=None, sql=None, modes="step0, explain, run:count, run:q1, run:q1_control, run:q2, run:q3, run:q4"):
    return (f"# 34 — GPT\n\nAPPROVED_CONTROLLER_SHA256: {controller or wa.controller_sha256()}\nAPPROVED_SQL_SET_SHA256: `{sql or wa.sql_set_sha256()}`\nAPPROVED_MODES: {modes}\n")


def _check(tmp_path, text, requested, name="34-gpt-approval.md", where=None):
    folder = where or tmp_path
    (folder / name).write_text(text, encoding="utf-8")
    return wa.check_approval(str(folder / name), requested, approvals_dir=tmp_path, controller_sha=wa.controller_sha256(), sql_sha=wa.sql_set_sha256())


def test_approval_binds_to_current_controller_sql_set_and_requested_modes(tmp_path):
    ok, why, info = _check(tmp_path, _approval_text(), {"step0", "run:q1"})
    assert ok and info["sha256"] and "run:q1" in info["modes"], why
    assert _check(tmp_path, _approval_text(controller="0" * 64), {"step0"})[0] is False             # sha controller cu / sai
    assert _check(tmp_path, _approval_text(sql="1" * 64), {"step0"})[0] is False                    # bo SQL da doi
    assert _check(tmp_path, _approval_text(modes="step0"), {"step0", "run:q3"})[0] is False         # che do chua duoc cho phep
    assert _check(tmp_path, "APPROVED_MODES: step0\n", {"step0"})[0] is False                       # thieu khoa
    duplicated = _approval_text() + f"APPROVED_CONTROLLER_SHA256: {'2' * 64}\n"
    assert _check(tmp_path, duplicated, {"step0"})[0] is False                                      # khoa lap lai
    assert _check(tmp_path, "da duyet het", {"step0"})[0] is False                                  # chuoi tuy y khong thay the


def test_approval_file_must_be_a_gpt_file_directly_inside_the_thread_folder(tmp_path):
    assert _check(tmp_path, _approval_text(), {"step0"}, name="notes.md")[0] is False
    assert _check(tmp_path, _approval_text(), {"step0"}, name="34-claude-self-approval.md")[0] is False
    sub = tmp_path / "sub"
    sub.mkdir()
    assert _check(tmp_path, _approval_text(), {"step0"}, where=sub)[0] is False
    assert wa.check_approval(str(tmp_path / "missing-gpt-x.md"), {"step0"}, approvals_dir=tmp_path, controller_sha=wa.controller_sha256(), sql_sha=wa.sql_set_sha256())[0] is False
    assert wa.check_approval("not-an-approved-file", {"step0"}, approvals_dir=tmp_path, controller_sha=wa.controller_sha256(), sql_sha=wa.sql_set_sha256())[0] is False


# =========================================================================== Q1..Q4 thuan
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
    assert "observed_at_null" in wa.q1_violations(_q1_row(observed_at=None))
    assert "prediction_time_ne_observed_at" in wa.q1_violations(_q1_row(prediction_time=dt.datetime(2026, 9, 2, 11)))
    assert "observation_series_ne_assignment_series" in wa.q1_violations(_q1_row(po_hotel="other"))
    assert "observation_series_ne_assignment_series" in wa.q1_violations(_q1_row(po_checkin=dt.date(2026, 9, 6)))


def _q2_row(**over):
    row = {"has_label": 1, "tgt_record": 10, "t_sample_id": 5, "t_selected": 1, "assignment": 7, "t_assignment": 7, "s_date": dt.date(2026, 9, 1), "t_date": dt.date(2026, 9, 2),
           "tp_id": 10, "tp_hotel": "h", "tp_checkin": dt.date(2026, 9, 5), "tp_price": 100, "tp_sold_out": 0, "tp_vn_date": dt.date(2026, 9, 2),
           "sp_id": 9, "s_hotel": "h", "s_checkin": dt.date(2026, 9, 5), "sp_vn_date": dt.date(2026, 9, 1)}
    row.update(over)
    return row


def test_q2_detects_label_link_and_observation_clock_violations():
    assert wa.q2_violations(_q2_row(), 1) == []
    assert "has_label_ne_target_id" in wa.q2_violations(_q2_row(tgt_record=None), 1)
    assert "has_label_ne_target_id" in wa.q2_violations(_q2_row(has_label=0), 1)
    assert "target_sample_missing" in wa.q2_violations(_q2_row(t_sample_id=None), 1)
    assert "target_not_daily_selected" in wa.q2_violations(_q2_row(t_selected=0), 1)
    assert "target_other_assignment" in wa.q2_violations(_q2_row(t_assignment=8), 1)
    assert "target_date_not_source_plus_k" in wa.q2_violations(_q2_row(t_date=dt.date(2026, 9, 3)), 1)
    assert "target_observation_missing" in wa.q2_violations(_q2_row(tp_id=None), 1)
    assert "target_observation_other_series" in wa.q2_violations(_q2_row(tp_hotel="x"), 1)
    assert "source_observation_missing" in wa.q2_violations(_q2_row(sp_id=None), 1)
    assert "source_observation_vn_date_ne_sample_date" in wa.q2_violations(_q2_row(sp_vn_date=dt.date(2026, 8, 31)), 1)
    assert "target_observation_vn_date_ne_target_sample_date" in wa.q2_violations(_q2_row(tp_vn_date=dt.date(2026, 9, 3)), 1)
    assert "observation_clock_gap_ne_k" in wa.q2_violations(_q2_row(sp_vn_date=dt.date(2026, 9, 1), tp_vn_date=dt.date(2026, 9, 2)), 3)
    assert "target_observation_sold_out_or_no_price" in wa.q2_violations(_q2_row(tp_sold_out=1), 1)
    assert "target_observation_sold_out_or_no_price" in wa.q2_violations(_q2_row(tp_price=None), 1)
    assert "target_observation_sold_out_or_no_price" in wa.q2_violations(_q2_row(tp_price=0), 1)
    assert wa.q2_violations(_q2_row(has_label=0, tgt_record=None, t_sample_id=None), 1) == []


def test_q2_controls_are_sensitive_on_both_sample_date_and_observation_clock():
    rows = [_q2_row(), _q2_row(), _q2_row(has_label=0, tgt_record=None, t_sample_id=None, t_date=None, tp_vn_date=None)]
    assert wa.q2_sensitivity_count(rows, 1) == {"sample_date": 2, "observation_clock": 2}
    assert wa.q2_sensitivity_count([_q2_row(t_date=dt.date(2026, 9, 3), tp_vn_date=dt.date(2026, 9, 3))], 1) == {"sample_date": 0, "observation_clock": 0}


def _g(sel, ts, owner="owner_success", code="local_primary", run=1, item=1, **over):
    row = {"is_daily_snapshot_selected": sel, "prediction_time": dt.datetime(2026, 9, 1, ts), "ownership_status": owner, "item_source_code": code, "run_source_code": code, "source_run_id": run,
           "source_item_id": item, "po_id": 5}
    row.update(over)
    return row


def test_q3_group_verdict_uses_the_full_tuple_and_controls_only_apply_to_strict_orders():
    good = wa.q3_group_verdict([_g(1, 8), _g(0, 9)])
    assert good["exactly_one_selected"] and good["selected_is_min_key"] and good["strict_order"] and good["reverse_order_would_pick_other"] and good["evaluable"]
    assert wa.q3_group_verdict([_g(0, 8), _g(1, 9)])["selected_is_min_key"] is False
    assert wa.q3_group_verdict([_g(1, 8), _g(1, 9)])["exactly_one_selected"] is False
    assert wa.q3_group_verdict([_g(0, 8), _g(0, 9)])["exactly_one_selected"] is False
    owner_first = wa.q3_group_verdict([_g(1, 9, owner="owner_success"), _g(0, 8, owner="non_owner_duplicate")])
    assert owner_first["selected_is_min_key"] and owner_first["strict_order"]
    tie = wa.q3_group_verdict([_g(1, 8), _g(0, 8)])
    assert tie["strict_order"] is False and tie["reverse_order_would_pick_other"] is False
    tiebreak = wa.q3_group_verdict([_g(1, 8, run=1, item=2), _g(0, 8, run=1, item=1)])
    assert tiebreak["selected_is_min_key"] is False


@pytest.mark.parametrize("over,problem", [({"po_id": None}, "missing_observation"), ({"source_item_id": None}, "missing_item_map"), ({"ownership_status": None}, "missing_item_map"),
                                          ({"source_run_id": None}, "missing_run_map"), ({"run_source_code": None}, "missing_run_map"), ({"item_source_code": "vps"}, "item_run_source_mismatch")])
def test_q3_missing_lineage_is_an_error_and_not_zero_filled_into_a_min_key_verdict(over, problem):
    verdict = wa.q3_group_verdict([_g(1, 8, **over), _g(0, 9)])
    assert problem in verdict["lineage_problems"] and verdict["evaluable"] is False
    assert verdict["selected_is_min_key"] is None and verdict["strict_order"] is None and verdict["reverse_order_would_pick_other"] is False


def test_q4_fidelity_has_null_semantics_orphans_are_errors_and_drift_is_only_counted():
    row = {"a_id": 1, "h_id": "h", "po_id": 3, "city": "Hà Nội", "a_max_occupancy": 2, "a_room_area": "20 m²", "a_breakfast_included": 1, "a_free_cancellation": 0}
    parquet = {"city": "Hà Nội", "max_occupancy": 2, "room_area_m2": 20.0, "breakfast_included": True, "free_cancellation": False}
    parse = lambda text: 20.0 if text == "20 m²" else None      # noqa: E731
    assert wa.q4_fidelity_violations(row, parquet, parse) == []
    assert wa.q4_fidelity_violations({**row, "city": "Đà Lạt"}, parquet, parse) == ["city"]
    assert wa.q4_fidelity_violations({**row, "a_max_occupancy": None}, parquet, parse) == ["max_occupancy"]
    assert wa.q4_fidelity_violations({**row, "a_max_occupancy": None}, {**parquet, "max_occupancy": float("nan")}, parse) == []
    assert wa.q4_fidelity_violations({**row, "a_room_area": None}, {**parquet, "room_area_m2": float("nan")}, parse) == []
    assert wa.q4_fidelity_violations({**row, "a_breakfast_included": 0}, parquet, parse) == ["breakfast_included"]
    assert wa.q4_fidelity_violations({**row, "a_id": None}, parquet, parse) == ["missing_assignment"]
    assert wa.q4_fidelity_violations({**row, "h_id": None}, parquet, parse) == ["missing_hotel"]
    drift = wa.q4_drift({"po_id": 3, "a_max_occupancy": 2, "po_max_occupancy": 3, "a_room_area": None, "po_room_area": None, "a_breakfast_included": 1, "po_breakfast_included": None,
                         "a_free_cancellation": 0, "po_free_cancellation": 0})
    assert drift == ["max_occupancy", "breakfast_included"]
    assert wa.q4_drift({"po_id": None, "a_max_occupancy": 2, "po_max_occupancy": None}) == []


def test_key_set_report_detects_missing_extra_and_duplicates():
    assert wa.key_set_report({1, 2, 3}, {1, 2, 3}, 3)["ok"] is True
    assert wa.key_set_report({1, 2}, {1, 2, 3}, 2)["missing_in_db"] == 1
    assert wa.key_set_report({1, 2, 3, 4}, {1, 2, 3}, 4)["extra_in_db"] == 1
    inflated = wa.key_set_report({1, 2, 3}, {1, 2, 3}, 4)
    assert inflated["duplicates"] == 1 and inflated["ok"] is False


# =========================================================================== Buoc 0 (schema that, hash dung khoa)
ARTIFACTS = {"samples_sha": {"ds_20261006_dev1b": "aa" * 32, "ds_20261006_dev3b": "bb" * 32}, "samples_rows": {"ds_20261006_dev1b": 4, "ds_20261006_dev3b": 4}}


def _manifest(name, **over):
    row = {"dataset_version": name, "import_batch_id": wa.BATCH_ID, "status": "pass", "last_completed_step": "validation", "active_step": None,
           "build_config_sha256": wa.DATASETS[name]["build_config_sha256"],
           "output_parquet_sha256_json": json.dumps({"samples.parquet": {"file_sha256": ARTIFACTS["samples_sha"][name], "content_sha256": "cc" * 32, "rows": 4, "columns": 9},
                                                      "data_dictionary.csv": {"file_sha256": "dd" * 32}}),
           "anomaly_registry_cutoff_at": dt.datetime(2026, 10, 5), "anomaly_registry_file_sha256": "ee" * 32}
    row.update(over)
    return row


def test_step0_verdict_requires_pass_validation_batch_build_sha_and_the_exact_samples_hash_key():
    ok_rows = [_manifest("ds_20261006_dev1b"), _manifest("ds_20261006_dev3b")]
    verdict = wa.step0_verdict(ok_rows, ARTIFACTS)
    assert verdict["all_ok"] is True and verdict["identity"]["ds_20261006_dev1b"]["samples_file_sha256_in_db"] == "aa" * 32
    assert wa.step0_verdict(ok_rows[:1], ARTIFACTS)["all_ok"] is False
    assert wa.step0_verdict([ok_rows[0], ok_rows[0], ok_rows[1]], ARTIFACTS)["all_ok"] is False         # hai dong cho mot dataset
    for bad in ({"status": "running"}, {"last_completed_step": "split"}, {"active_step": "samples_labels"}, {"import_batch_id": "other"}, {"build_config_sha256": "0" * 64}):
        rows = [_manifest("ds_20261006_dev1b", **bad), _manifest("ds_20261006_dev3b")]
        assert wa.step0_verdict(rows, ARTIFACTS)["all_ok"] is False, bad
    assert wa.step0_verdict([], ARTIFACTS)["all_ok"] is False


def test_step0_rejects_the_expected_hash_when_it_only_appears_under_the_wrong_key_or_in_text():
    wrong_hash = {"samples.parquet": {"file_sha256": "0" * 64, "rows": 4}, "unrelated_note": ARTIFACTS["samples_sha"]["ds_20261006_dev1b"]}
    other_key = {"data_dictionary.csv": {"file_sha256": ARTIFACTS["samples_sha"]["ds_20261006_dev1b"]}}
    flat_old_style = {"samples.parquet": ARTIFACTS["samples_sha"]["ds_20261006_dev1b"]}               # dang flat (khong phai export that)
    wrong_rows = {"samples.parquet": {"file_sha256": ARTIFACTS["samples_sha"]["ds_20261006_dev1b"], "rows": 5}}
    for bad in (wrong_hash, other_key, flat_old_style, wrong_rows, "not json {", None, 7, [], {"samples.parquet": "x"}):
        for packed in (bad, json.dumps(bad) if not isinstance(bad, str) else bad):
            rows = [_manifest("ds_20261006_dev1b", output_parquet_sha256_json=packed), _manifest("ds_20261006_dev3b")]
            assert wa.step0_verdict(rows, ARTIFACTS)["all_ok"] is False, packed
    dict_form = [_manifest("ds_20261006_dev1b", output_parquet_sha256_json={"samples.parquet": {"file_sha256": ARTIFACTS["samples_sha"]["ds_20261006_dev1b"], "rows": 4}}), _manifest("ds_20261006_dev3b")]
    assert wa.step0_verdict(dict_form, ARTIFACTS)["all_ok"] is True                                    # MySQL connector co the tra dict da parse
    assert wa.parquet_entry(b'{"samples.parquet": {"file_sha256": "x"}}') == {"file_sha256": "x"}


# =========================================================================== tinh chat SQL
SQL_CONSTANTS = wa.sql_statements()


def test_every_sql_statement_is_a_single_select_without_writes_or_ddl():
    assert set(SQL_CONSTANTS) == {"SQL_STEP0", "SQL_COUNT_SELECTED", "SQL_Q1", "SQL_Q1_CONTROL", "SQL_Q2", "SQL_Q3_GROUPS", "SQL_Q3_ROWS", "SQL_Q3_ROWS_PACKED", "SQL_Q4"}
    for name, sql in SQL_CONSTANTS.items():
        lowered = sql.lower()
        assert lowered.lstrip().startswith("select"), name
        assert ";" not in sql, name
        assert not re.search(r"\b(insert|update|delete|replace|alter|create|drop|truncate|analyze|optimize|grant|set\s)\b", lowered), name


def test_dataset_scoped_queries_have_a_dataset_predicate_and_row_bounds():
    for name in ("SQL_Q1", "SQL_Q1_CONTROL", "SQL_Q2", "SQL_Q3_GROUPS", "SQL_Q3_ROWS", "SQL_Q4", "SQL_COUNT_SELECTED"):
        assert "dataset_version = %s" in SQL_CONSTANTS[name], name
    for name in ("SQL_Q1", "SQL_Q2", "SQL_Q4"):
        sql = SQL_CONSTANTS[name]
        assert f"LIMIT {wa.BATCH}" in sql and "ORDER BY s.record_id" in sql and "s.record_id > %s" in sql, name
    assert f"LIMIT {wa.BATCH}" in SQL_CONSTANTS["SQL_Q1_CONTROL"] and "m.crawl_run_item_id > %s" in SQL_CONSTANTS["SQL_Q1_CONTROL"]
    assert f"LIMIT {wa.BATCH}" in SQL_CONSTANTS["SQL_Q3_GROUPS"] and "ml_reference_assignment_id > %s" in SQL_CONSTANTS["SQL_Q3_GROUPS"]
    assert f"LIMIT {wa.BATCH + 1}" in SQL_CONSTANTS["SQL_Q3_ROWS"] and "vn_observation_date = %s" in SQL_CONSTANTS["SQL_Q3_ROWS"]
    packed = SQL_CONSTANTS["SQL_Q3_ROWS_PACKED"]
    assert f"LIMIT {wa.BATCH + 1}" in packed and "dataset_version = %s" in packed and "{predicates}" in packed and "ORDER BY s.ml_reference_assignment_id, s.vn_observation_date, s.record_id" in packed
    assert wa.packed_sql(2).count("%s") == 7 and wa.packed_sql(1).count("%s") == 5                           # 2 batch id + dataset + 2 tham so moi nhom
    assert wa.sql_set_sha256() != hashlib_sha(json.dumps(wa.sql_statements(), sort_keys=True, ensure_ascii=False))   # bo SQL cung gom vi tu packed
    assert wa.BATCH <= 1000 and wa.MAX_EXECUTION_MS <= 10_000


def hashlib_sha(text):
    import hashlib
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def test_no_query_can_hide_orphans_behind_an_inner_join_or_run_unbounded():
    for name in ("SQL_Q1", "SQL_Q1_CONTROL", "SQL_Q2", "SQL_Q3_ROWS", "SQL_Q3_ROWS_PACKED", "SQL_Q4"):
        sql = SQL_CONSTANTS[name]
        assert not re.search(r"(?<!LEFT )\bJOIN\b", sql.replace("LEFT JOIN", "LEFT_JOIN")) , name           # khong co INNER JOIN nao
    assert "HAVING COUNT(*) > 1 OR SUM(s.is_daily_snapshot_selected) <> 1" in SQL_CONSTANTS["SQL_Q3_GROUPS"]      # bat ca nhom khong co dung 1 selected, ke ca nhom 1 dong
    assert "DATE_ADD(tp.observed_at, INTERVAL 7 HOUR)" in SQL_CONSTANTS["SQL_Q2"] and "DATE_ADD(sp.observed_at, INTERVAL 7 HOUR)" in SQL_CONSTANTS["SQL_Q2"]


def test_q1_control_uses_the_funnel_prefix_stage_and_not_late_conditions():
    sql = SQL_CONSTANTS["SQL_Q1_CONTROL"]
    assert "match_status IN ('exact','alias')" in sql and "po.is_sold_out = 0" in sql and "po.price_per_night > 0" in sql
    for late in ("hotels", "N1", "registry", "city"):
        assert late.lower() not in sql.lower().replace("hotel", "")


def test_connection_is_read_only_with_execution_timeout_and_no_live_gate_bypass():
    import inspect
    source = inspect.getsource(wa.connect)
    assert "READ ONLY" in source and "MAX_EXECUTION_TIME" in source
    parser = wa.build_parser()
    for flag in ("--skip-time-gate", "--skip-crawler-gate"):
        with pytest.raises(SystemExit):
            parser.parse_args(["--step0", "--gpt-approval", "x", "--crawl-evidence", "y", flag])
    with pytest.raises(SystemExit):
        parser.parse_args(["--step0", "--crawl-evidence", "y"])                                          # thieu --gpt-approval
    assert "check_crawl_progress" not in CONTROLLER_TEXT and "subprocess" not in CONTROLLER_TEXT         # controller khong goi monitor/van hanh


CONTROLLER_TEXT = (TOOLS / "warehouse_audit_v3.py").read_text(encoding="utf-8")


def test_artifact_hashes_in_code_match_the_contract_build_shas_in_gpt_file_30():
    assert wa.DATASETS["ds_20261006_dev1b"]["build_config_sha256"].startswith("6494c1d4") and wa.DATASETS["ds_20261006_dev3b"]["build_config_sha256"].startswith("45f2fc6a")
    assert wa.EXPECTED_BURN_IN == 43662 and wa.EXPECTED_SELECTED == 146431 and wa.EXPECTED_LABELLED == {1: 128583, 3: 111515, 7: 83077, 14: 45049}
    assert wa.EXPECTED_MATCHED == 192590 and wa.EXPECTED_SUPERSEDED == 1335


# =========================================================================== DB gia (khong ket noi that)
class World:
    """Mot the gioi nho nhat quan, tra loi cac SQL cua controller y nhu MySQL se tra (lap lai ngu nghia LEFT JOIN, keyset, GROUP BY)."""

    def __init__(self):
        D = dt.date
        self.approved_at = dt.datetime(2026, 8, 31, 0, 0)
        self.assignment = {"id": 1, "hotel": "h", "checkin": D(2026, 9, 10), "max_occ": 2, "room_area": "20 m²", "bf": 1, "fc": 0}
        self.hotels = {"h": "Hà Nội"}
        # sample_id -> (vn_date, selected, targets{k: record}); observation record = sample record
        self.samples = {11: {"date": D(2026, 9, 1), "sel": 1, "t": {1: 12, 3: 15, 7: None, 14: None}},
                        12: {"date": D(2026, 9, 2), "sel": 1, "t": {1: 13, 3: None, 7: None, 14: None}},
                        14: {"date": D(2026, 9, 2), "sel": 0, "t": {1: None, 3: None, 7: None, 14: None}},
                        13: {"date": D(2026, 9, 3), "sel": 1, "t": {1: 15, 3: None, 7: None, 14: None}},
                        15: {"date": D(2026, 9, 4), "sel": 1, "t": {1: None, 3: None, 7: None, 14: None}}}
        self.obs = {r: {"hotel": "h", "checkin": D(2026, 9, 10), "observed_at": dt.datetime(2026, 9, s["date"].day, 3, 0), "price": 100 + 10 * (i + 1) if r != 14 else 105,
                        "sold_out": 0, "item": 900 + r, "run": 70 + (r % 2)}
                    for i, (r, s) in enumerate(sorted(self.samples.items())) if r != 14 or True}
        self.obs[14]["observed_at"] = dt.datetime(2026, 9, 2, 6, 0)
        self.obs[12]["observed_at"] = dt.datetime(2026, 9, 2, 3, 0)
        self.item_map = {self.obs[r]["item"]: {"ownership_status": "owner_success", "source_code": "local_primary", "source_item_id": 5000 + r} for r in self.obs}
        self.run_map = {70: {"source_code": "local_primary", "source_run_id": 60}, 71: {"source_code": "local_primary", "source_run_id": 61}}
        self.matches = [{"item": 101 + i, "price_ok": 1, "before_approval": 1 if i == 0 else 0, "missing_assignment": 0, "missing_observation": 0} for i in range(4)]
        self.count_selected = 4
        self.manifest = {name: _manifest(name) for name in wa.DATASETS}
        self.plan_type = {}                # nhan -> type de ep plan xau
        self.fail_on = None                # chuoi SQL gay loi (timeout)
        self.drop_q1 = set()               # record bi thieu khoi ket qua Q1
        self.dup_q1 = set()                # record bi tra 2 lan
        self.q3_extra_rows = 0
        self.drop_run_map = False
        self.on_query = None               # goi truoc moi query DU LIEU (khong phai EXPLAIN/Buoc 0)
        self.q3_leak_other_group = False   # query Q3 tra them mot dong thuoc nhom KHAC (khong duoc yeu cau)
        self.on_explain = None             # goi KHI mot EXPLAIN dang duoc tra loi (truoc khi tra plan ve controller)
        self.on_step0 = None               # goi KHI Buoc 0 dang duoc tra loi
        self.q4_override = {}              # record_id -> {cot Q4: gia tri} (vd NULL cua assignment)
        self.drop_q4 = set()               # record bi thieu khoi ket qua Q4

    # ---- helpers
    def vn_date(self, record):
        return (self.obs[record]["observed_at"] + dt.timedelta(hours=7)).date()

    def selected(self):
        return sorted(r for r, s in self.samples.items() if s["sel"])

    def a(self):
        return self.assignment

    def sample_row(self, r):
        s = self.samples[r]
        return {"record_id": r, "assignment": 1, "ml_reference_assignment_id": 1, "vn_date": s["date"], "is_daily_snapshot_selected": s["sel"], "prediction_time": self.obs[r]["observed_at"]}

    # ---- tra loi
    def answer(self, sql, params):
        if self.fail_on and self.fail_on in sql and not sql.startswith("EXPLAIN "):
            raise RuntimeError("Query execution was interrupted, maximum statement execution time exceeded")
        if self.on_query is not None and not sql.startswith("EXPLAIN ") and sql != wa.SQL_STEP0:
            self.on_query(sql)
        if sql.startswith("EXPLAIN "):
            if self.on_explain is not None:
                self.on_explain(sql[len("EXPLAIN "):])
            return self.plan(sql[len("EXPLAIN "):])
        if sql == wa.SQL_STEP0:
            if self.on_step0 is not None:
                self.on_step0()
            return [dict(self.manifest[n]) for n in wa.DATASETS if n in self.manifest]
        if sql == wa.SQL_COUNT_SELECTED:
            return [{"n": self.count_selected}]
        if sql == wa.SQL_Q1:
            return self.q1(params)
        if sql == wa.SQL_Q1_CONTROL:
            return [{"crawl_run_item_id": m["item"], **{k: v for k, v in m.items() if k != "item"}} for m in self.matches if m["item"] > params[1]][:wa.BATCH]
        for k in (1, 3, 7, 14):
            if sql == wa.SQL_Q2.format(k=k):
                return self.q2(k, params)
        if sql == wa.SQL_Q3_GROUPS:
            return self.q3_groups(params)
        if sql == wa.SQL_Q3_ROWS:
            return self.q3_rows(params)
        if sql == wa.SQL_Q4:
            return self.q4(params)
        if sql.startswith(PACKED_HEAD):
            return self.q3_rows_packed(params)
        raise AssertionError(f"SQL la: {sql[:80]}")

    def plan(self, inner):
        known = {wa.SQL_COUNT_SELECTED: ["s"], wa.SQL_Q1: ["s", "a", "po"], wa.SQL_Q1_CONTROL: ["m", "a", "po"], wa.SQL_Q3_GROUPS: ["s"], wa.SQL_Q3_ROWS: ["s", "po", "im", "rm"],
                 wa.SQL_Q4: ["s", "a", "h", "po"], **{wa.SQL_Q2.format(k=k): ["s", "t", "tp", "sp"] for k in (1, 3, 7, 14)}}
        aliases = ["s", "po", "im", "rm"] if inner.startswith(PACKED_HEAD) else known[inner]    # KeyError neu controller EXPLAIN mot SQL la
        driving = aliases[0]
        rows = []
        for alias in aliases:
            kind = "range" if alias == driving else "eq_ref"
            rows.append({"table": alias, "type": self.plan_type.get((inner, alias), self.plan_type.get(alias, kind)), "key": "k", "rows": 1})
        return rows

    def q1(self, params):
        out = []
        for r in self.selected():
            if r <= params[1] or r in self.drop_q1:
                continue
            o, a = self.obs[r], self.a()
            row = {"record_id": r, "ml_reference_assignment_id": 1, "prediction_time": o["observed_at"], "a_id": a["id"], "approved_at": self.approved_at, "a_hotel": a["hotel"], "a_checkin": a["checkin"],
                   "po_id": r, "observed_at": o["observed_at"], "po_hotel": o["hotel"], "po_checkin": o["checkin"]}
            out.append(row)
            if r in self.dup_q1:
                out.append(dict(row))
        return out[:wa.BATCH]

    def q2(self, k, params):
        out = []
        for r in self.selected():
            if r <= params[1]:
                continue
            s = self.samples[r]
            t_rec = s["t"][k]
            t = self.samples.get(t_rec) if t_rec else None
            tp = self.obs.get(t_rec) if t_rec else None
            sp = self.obs[r]
            out.append({"record_id": r, "assignment": 1, "s_date": s["date"], "has_label": 1 if t_rec else 0, "tgt_record": t_rec,
                        "t_sample_id": (t_rec * 10 if t else None), "t_selected": t["sel"] if t else None, "t_assignment": 1 if t else None, "t_date": t["date"] if t else None,
                        "tp_id": t_rec if tp else None, "tp_hotel": tp["hotel"] if tp else None, "tp_checkin": tp["checkin"] if tp else None, "tp_price": tp["price"] if tp else None,
                        "tp_sold_out": tp["sold_out"] if tp else None, "tp_vn_date": self.vn_date(t_rec) if tp else None,
                        "sp_id": r, "s_hotel": sp["hotel"], "s_checkin": sp["checkin"], "sp_vn_date": self.vn_date(r)})
        return out[:wa.BATCH]

    def groups(self):
        by = {}
        for r, s in self.samples.items():
            by.setdefault((1, s["date"]), []).append(s["sel"])
        return [{"assignment": a, "vn_date": d, "n": len(v), "n_selected": sum(v)} for (a, d), v in sorted(by.items()) if len(v) > 1 or sum(v) != 1]

    def q3_groups(self, params):
        _, a1, a2, d = params
        return [g for g in self.groups() if g["assignment"] > a1 or (g["assignment"] == a2 and g["vn_date"] > d)][:wa.BATCH]

    def q3_rows(self, params):
        _, _, _, assignment, vn_date = params
        out = []
        for r, s in sorted(self.samples.items()):
            if s["date"] != vn_date or assignment != 1:
                continue
            o = self.obs.get(r)
            item = self.item_map.get(o["item"]) if o else None
            run = None if self.drop_run_map else (self.run_map.get(o["run"]) if o else None)
            out.append({"record_id": r, "assignment": 1, "vn_date": s["date"], "is_daily_snapshot_selected": s["sel"], "prediction_time": o["observed_at"] if o else None, "po_id": r if o else None,
                        "ownership_status": item["ownership_status"] if item else None, "item_source_code": item["source_code"] if item else None, "source_item_id": item["source_item_id"] if item else None,
                        "run_source_code": run["source_code"] if run else None, "source_run_id": run["source_run_id"] if run else None})
        extra = [dict(out[0], record_id=900 + i) for i in range(self.q3_extra_rows)] if out else []
        if self.q3_leak_other_group and out:
            extra.append(dict(out[0], record_id=777, vn_date=out[0]["vn_date"] + dt.timedelta(days=1)))
        return out + extra

    def q3_rows_packed(self, params):
        pairs = list(zip(params[3::2], params[4::2]))
        rows = []
        for assignment, vn_date in sorted(pairs):
            rows += self.q3_rows((None, None, None, assignment, vn_date))
        return rows[:wa.BATCH + 1]

    def q4(self, params):
        out = []
        a = self.a()
        for r in self.selected():
            if r <= params[1] or r in self.drop_q4:
                continue
            o = self.obs[r]
            row = {"record_id": r, "a_id": a["id"], "h_id": "h", "city": self.hotels["h"], "a_max_occupancy": a["max_occ"], "a_room_area": a["room_area"], "a_breakfast_included": a["bf"],
                   "a_free_cancellation": a["fc"], "po_id": r, "po_max_occupancy": a["max_occ"], "po_room_area": a["room_area"], "po_breakfast_included": a["bf"], "po_free_cancellation": a["fc"]}
            row.update(self.q4_override.get(r, {}))
            out.append(row)
        return out[:wa.BATCH]


PACKED_HEAD = wa.SQL_Q3_ROWS_PACKED.split("{predicates}")[0]


class FakeCursor:
    def __init__(self, conn):
        self.conn, self.rows = conn, []

    def execute(self, sql, params=()):
        self.conn.log.append((sql, tuple(params)))
        self.rows = self.conn.world.answer(sql, tuple(params))

    def fetchall(self):
        return self.rows

    def close(self):
        pass


class FakeConn:
    def __init__(self, world):
        self.world, self.log, self.closed = world, [], False

    def cursor(self, dictionary=False):
        return FakeCursor(self)

    def rollback(self):
        pass

    def close(self):
        self.closed = True

    def executed(self):
        return [sql for sql, _ in self.log if not sql.startswith("EXPLAIN ")]


def _frame():
    area = AREA_PARSER("20 m²")
    nan = float("nan")
    return pd.DataFrame({"warehouse_record_id": [11, 12, 13, 15], "city": ["Hà Nội"] * 4, "max_occupancy": [2] * 4, "room_area_m2": [area] * 4, "breakfast_included": [True] * 4,
                         "free_cancellation": [False] * 4,
                         "has_label_h1": [1, 1, 1, 0], "y_price_h1": [120.0, 130.0, 150.0, nan],           # 11->12 (120), 12->13 (130), 13->15 (150)
                         "has_label_h3": [1, 0, 0, 0], "y_price_h3": [150.0, nan, nan, nan],
                         "has_label_h7": [0] * 4, "y_price_h7": [nan] * 4, "has_label_h14": [0] * 4, "y_price_h14": [nan] * 4})


class Env:
    def __init__(self, tmp_path, monkeypatch):
        self.tmp_path, self.monkeypatch = tmp_path, monkeypatch
        self.world = World()
        self.conn = FakeConn(self.world)
        self.connect_calls = 0
        self.frame = _frame()
        self.now = vn(18, 0)
        monkeypatch.setattr(wa, "BATCH", 2)
        monkeypatch.setattr(wa, "EXPECTED_SELECTED", 4)
        monkeypatch.setattr(wa, "EXPECTED_LABELLED", {1: 3, 3: 1, 7: 0, 14: 0})
        monkeypatch.setattr(wa, "EXPECTED_BURN_IN", 1)
        monkeypatch.setattr(wa, "EXPECTED_MATCHED", 4)
        monkeypatch.setattr(wa, "EXPECTED_SUPERSEDED", 1)
        monkeypatch.setattr(wa, "APPROVALS_DIR", tmp_path)
        monkeypatch.setattr(wa, "AUDIT_OUT_DIR", tmp_path / "out")
        monkeypatch.setattr(wa, "_sleep", lambda s: None)
        monkeypatch.setattr(wa, "_area_parser", lambda: AREA_PARSER)                                              # oracle thuan, khong import builder/env
        monkeypatch.setattr(wa, "GATE_INTERVAL_S", 0.0)
        monkeypatch.setattr(wa, "_now_utc", lambda: self.now)
        monkeypatch.setattr(wa, "connect", self._connect)
        monkeypatch.setattr(wa, "load_artifacts", self._artifacts)
        self.write_approval()
        self.write_evidence()

    def _connect(self, db):
        self.connect_calls += 1
        return self.conn

    def _artifacts(self, need_frames):
        out = {**ARTIFACTS, "contract_build_sha": {n: wa.DATASETS[n]["build_config_sha256"] for n in wa.DATASETS}, "frames": {}}
        if need_frames:
            out["frames"] = {n: self.frame.copy() for n in wa.DATASETS}
        return out

    def write_approval(self, **kw):
        path = self.tmp_path / "34-gpt-approval.md"
        path.write_text(_approval_text(**kw), encoding="utf-8")
        return path

    def write_evidence(self, **over):
        path = self.tmp_path / "evidence.json"
        path.write_text(json.dumps(_evidence(self.now, **over)), encoding="utf-8")
        return path

    def run(self, *flags, **kw):
        flags = list(flags)
        if "--run" in flags and "--budget-seconds" not in flags:
            flags += ["--budget-seconds", "600", "--max-queries", "5000"]
        argv = ["--gpt-approval", str(self.tmp_path / "34-gpt-approval.md"), "--crawl-evidence", str(self.tmp_path / "evidence.json"), *flags]
        buffer = io.StringIO()
        with contextlib.redirect_stdout(buffer):
            code = wa.main(argv)
        match = re.search(r"^bao cao: (.+)$", buffer.getvalue(), re.MULTILINE)                                     # DUNG duong dan cua lan goi nay (khong suy ra "moi nhat" theo ten/gio)
        report = json.loads(Path(match.group(1)).read_text(encoding="utf-8")) if match else None
        if report is not None:
            assert report["run_id"] in match.group(1)                                                              # ten tep mang run_id cua chinh lan goi
        self.last_stdout = buffer.getvalue()
        return code, report


@pytest.fixture
def env(tmp_path, monkeypatch):
    return Env(tmp_path, monkeypatch)


# --------------------------------------------------------------------------- duong vui: PASS_FULL chi khi du moi ho + control
def test_full_run_passes_only_with_all_families_controls_and_denominators(env):
    code, report = env.run("--run", "all")
    assert code == 0 and report["verdict"]["overall"] == "PASS_FULL" and report["verdict"]["execution"] == "complete", report["verdict"]
    for name, per in report["families"].items():
        assert set(per) == {"count", "q1", "q1_control", "q2_h1", "q2_h3", "q2_h7", "q2_h14", "q3", "q4"}
        assert all(res["status"] == "PASS" for res in per.values()), (name, {k: v["status"] for k, v in per.items()})
    q1c = report["families"]["ds_20261006_dev1b"]["q1_control"]
    assert q1c["totals"]["before_approval"] == 1 and q1c["checks"]["control_is_positive"]
    q3 = report["families"]["ds_20261006_dev1b"]["q3"]
    assert q3["state"]["superseded_rows"] == 1 and q3["state"]["groups_evaluated"] == q3["state"]["groups_expected"] == 1
    assert report["step0"]["all_ok"] and report["approval"]["sha256"] and report["crawl_evidence"]["sha_chain"] and report["controller_sha256"] == wa.controller_sha256()


def test_step0_runs_first_in_every_live_mode_and_nothing_else_runs_in_step0_mode(env):
    code, report = env.run("--step0")
    assert code == 0 and report["verdict"]["overall"] == "STEP0_OK" and env.conn.executed() == [wa.SQL_STEP0]
    assert not [s for s, _ in env.conn.log if s.startswith("EXPLAIN ")]
    env.conn.log.clear()
    env.run("--run", "q1")
    assert env.conn.log[0][0] == wa.SQL_STEP0
    env.conn.log.clear()
    env.run("--explain", "all")
    assert env.conn.log[0][0] == wa.SQL_STEP0


def test_explain_mode_runs_only_step0_and_explain_never_count_or_data_queries(env):
    code, report = env.run("--explain", "all")
    assert code == 0 and report["verdict"]["overall"] == "EXPLAIN_OK" and env.conn.executed() == [wa.SQL_STEP0]
    explained = [s for s, _ in env.conn.log if s.startswith("EXPLAIN ")]
    assert any(wa.SQL_COUNT_SELECTED in s for s in explained) and any(wa.SQL_Q3_ROWS in s for s in explained) and any(wa.SQL_Q1_CONTROL in s for s in explained)
    plans = list(report["plans"].values())
    assert {p["label"] for p in plans} == {"count", "q1", "q1_control", "q2_h1", "q2_h3", "q2_h7", "q2_h14", "q3_groups", "q3_rows", "q4"}
    for dataset in wa.DATASETS:
        mine = [p for p in plans if p["dataset"] == dataset]
        assert len(mine) == 10 and all(p["ok"] and p["sql_sha256"] and p["params"] for p in mine)
        assert [p["representative"] for p in mine if p["label"] == "q3_rows"] == [False]                     # tham so placeholder, khong phai nhom that
    explain_calls = [params for sql, params in env.conn.log if sql.startswith("EXPLAIN ")]
    for dataset in wa.DATASETS:                                                                               # EXPLAIN THAT duoc goi cho TUNG dataset (khong chi dict PLAN_OK)
        assert sum(1 for params in explain_calls if dataset in params) == 10, dataset


def test_explain_mode_with_a_refused_plan_is_not_ok_and_still_runs_no_data_query(env):
    env.world.plan_type[("s")] = "ALL"
    code, report = env.run("--explain", "all")
    assert code == 4 and report["verdict"]["overall"] == "EXPLAIN_REFUSED" and env.conn.executed() == [wa.SQL_STEP0]
    env.world.plan_type = {"rm": "ALL"}                                                                   # Q3 rows: bang noi quet toan bo
    env.conn.log.clear()
    code, report = env.run("--explain", "q3")
    assert code == 4 and env.conn.executed() == [wa.SQL_STEP0]


# --------------------------------------------------------------------------- negative controls cua verdict
def test_run_with_a_refused_plan_never_executes_that_query_and_is_unverified(env):
    env.world.plan_type = {("s"): "ALL"}
    code, report = env.run("--run", "q1")
    assert code == 4 and report["verdict"]["overall"] in ("UNVERIFIED", "PARTIAL_PASS")
    assert wa.SQL_Q1 not in env.conn.executed()
    assert report["families"]["ds_20261006_dev1b"]["count"]["status"] == "PLAN_REFUSED" or report["families"]["ds_20261006_dev1b"]["q1"]["status"] == "PLAN_REFUSED"


def test_denominator_mismatch_fails_and_stops_before_any_other_family(env):
    env.world.count_selected = 3
    code, report = env.run("--run", "all")
    assert code == 1 and report["verdict"]["overall"] == "FAIL"
    assert report["families"]["ds_20261006_dev1b"]["count"]["status"] == "FAIL"
    assert not [s for s in env.conn.executed() if s in (wa.SQL_Q1, wa.SQL_Q4)]


def test_observed_before_approval_is_a_q1_failure(env):
    env.world.approved_at = dt.datetime(2026, 9, 2, 12, 0)
    code, report = env.run("--run", "all")
    assert code == 1 and report["families"]["ds_20261006_dev1b"]["q1"]["violation_counts"]["observed_before_approval"] >= 1


def test_missing_and_duplicated_rows_fail_the_key_set_coverage(env):
    env.world.drop_q1 = {13}
    code, report = env.run("--run", "all")
    assert code == 1 and report["families"]["ds_20261006_dev1b"]["q1"]["status"] == "FAIL" and report["families"]["ds_20261006_dev1b"]["q1"]["coverage"]["missing_in_db"] == 1
    env.world.drop_q1, env.world.dup_q1 = set(), {11}
    code, report = env.run("--run", "all")
    cov = report["families"]["ds_20261006_dev1b"]["q1"]["coverage"]
    assert code == 1 and cov["duplicates"] == 1 and cov["ok"] is False


def test_exhausting_the_batch_budget_is_unverified_never_pass(env):
    code, report = env.run("--run", "all", "--max-batches", "1")                                           # BATCH=2, 4 dong => can 2 batch
    assert code == 4 and report["verdict"]["overall"] == "UNVERIFIED" and report["verdict"]["execution"] == "incomplete"
    assert report["families"]["ds_20261006_dev1b"]["q1"]["status"] == "UNVERIFIED" and report["families"]["ds_20261006_dev1b"]["q1"]["iteration"]["complete"] is False


def test_a_timeout_stops_that_query_without_retry_and_is_unverified(env):
    env.world.fail_on = "FROM ml_samples s LEFT JOIN ml_samples t"
    code, report = env.run("--run", "all")
    assert code == 4 and report["families"]["ds_20261006_dev1b"]["q2_h1"]["status"] == "UNVERIFIED" and "interrupted" in report["families"]["ds_20261006_dev1b"]["q2_h1"]["reason"]
    attempts = [s for s in env.conn.executed() if s == wa.SQL_Q2.format(k=1)]
    assert len(attempts) == 2                                                                              # 1 lan / dataset, khong retry


def test_partial_request_is_never_reported_as_a_full_pass(env):
    code, report = env.run("--run", "q1")
    assert report["families"]["ds_20261006_dev1b"]["q1"]["status"] == "PASS" and report["verdict"]["overall"] == "PARTIAL_PASS" and code == 4


def test_q1_control_must_be_positive_and_match_the_funnel(env):
    for m in env.world.matches:
        m["before_approval"] = 0
    code, report = env.run("--run", "all")
    assert code == 1 and report["families"]["ds_20261006_dev1b"]["q1_control"]["status"] == "FAIL"
    assert report["families"]["ds_20261006_dev1b"]["q1_control"]["checks"]["control_is_positive"] is False


def test_q2_price_or_label_flag_mismatch_with_parquet_fails(env):
    env.frame.loc[0, "y_price_h1"] = 121.0
    code, report = env.run("--run", "all")
    assert code == 1 and report["families"]["ds_20261006_dev1b"]["q2_h1"]["price_mismatch"] == 1
    env.frame = _frame()
    env.frame.loc[3, "has_label_h1"] = 1
    code, report = env.run("--run", "all")
    assert code == 1 and report["families"]["ds_20261006_dev1b"]["q2_h1"]["has_label_mismatch"] >= 1


def test_q2_uses_observation_clocks_so_a_shifted_target_observation_is_caught(env):
    env.world.obs[12]["observed_at"] = dt.datetime(2026, 9, 3, 3, 0)                                       # target cua 11 (h1) co dong ho VN 09-03 thay vi 09-02
    code, report = env.run("--run", "all")
    counts = report["families"]["ds_20261006_dev1b"]["q2_h1"]["violation_counts"]
    assert code == 1 and (counts.get("target_observation_vn_date_ne_target_sample_date", 0) >= 1 or counts.get("observation_clock_gap_ne_k", 0) >= 1)


def test_q3_missing_lineage_and_too_many_rows_are_errors_not_zero_filled(env):
    env.world.drop_run_map = True
    code, report = env.run("--run", "all")
    q3 = report["families"]["ds_20261006_dev1b"]["q3"]
    assert code == 1 and q3["status"] == "FAIL" and q3["state"]["lineage_groups"] == 1 and q3["checks"]["lineage_complete"] is False and q3["state"]["not_min_key"] == 0
    env.world.drop_run_map = False
    env.world.q3_extra_rows = wa.BATCH + 1 + 5                                                               # vuot gioi han so dong cua truy van diem
    env.conn.log.clear()
    code, report = env.run("--run", "all")
    assert report["families"]["ds_20261006_dev1b"]["q3"]["status"] == "UNVERIFIED" and "gioi han" in report["families"]["ds_20261006_dev1b"]["q3"]["reason"] and code in (1, 4)


def test_q3_superseded_row_count_and_group_size_are_checked(env):
    env.world.samples[16] = {"date": dt.date(2026, 9, 2), "sel": 0, "t": {1: None, 3: None, 7: None, 14: None}}
    env.world.obs[16] = dict(env.world.obs[14], item=916)
    env.world.item_map[916] = {"ownership_status": "owner_success", "source_code": "local_primary", "source_item_id": 5016}
    code, report = env.run("--run", "all")
    state = report["families"]["ds_20261006_dev1b"]["q3"]["state"]
    assert code == 1 and state["superseded_rows"] == 2 and report["families"]["ds_20261006_dev1b"]["q3"]["checks"]["superseded_rows_equal_expected"] is False


def test_q3_is_bound_to_the_parquet_source_set_in_both_directions(env):
    env.frame.loc[env.frame["warehouse_record_id"] == 12, "warehouse_record_id"] = 99                         # selected 12 khong con trong Parquet (giu nguyen so dong de COUNT van khop)
    code, report = env.run("--run", "q3")
    q3 = report["families"]["ds_20261006_dev1b"]["q3"]
    assert q3["state"]["selected_missing_in_parquet"] == 1 and q3["checks"]["selected_rows_are_in_parquet"] is False and q3["status"] == "FAIL"
    env.frame = _frame()
    env.frame.loc[env.frame["warehouse_record_id"] == 15, "warehouse_record_id"] = 14                         # dong bi daily-dedup loai (14) lai nam trong Parquet
    code, report = env.run("--run", "q3")
    q3 = report["families"]["ds_20261006_dev1b"]["q3"]
    assert q3["state"]["superseded_present_in_parquet"] == 1 and q3["checks"]["superseded_rows_not_in_parquet"] is False and q3["status"] == "FAIL"


def test_q4_orphans_and_value_mismatches_fail(env):
    original = env.world.q4

    def orphan(params):
        rows = original(params)
        for row in rows:
            if row["record_id"] == 13:
                row["a_id"] = None
        return rows
    env.world.q4 = orphan
    code, report = env.run("--run", "all")
    assert code == 1 and report["families"]["ds_20261006_dev1b"]["q4"]["fidelity_violation_counts"]["missing_assignment"] == 1
    env.world.q4 = original
    env.world.hotels["h"] = "Đà Lạt"
    code, report = env.run("--run", "all")
    assert code == 1 and report["families"]["ds_20261006_dev1b"]["q4"]["fidelity_violation_counts"]["city"] >= 1


# --------------------------------------------------------------------------- tu choi truoc khi mo ket noi + Buoc 0 gan manifest
def test_step0_mismatch_refuses_everything_after_the_single_manifest_query(env):
    wrong = _manifest("ds_20261006_dev1b", output_parquet_sha256_json=json.dumps({"samples.parquet": {"file_sha256": "0" * 64, "rows": 4}, "unrelated_note": ARTIFACTS["samples_sha"]["ds_20261006_dev1b"]}))
    env.world.manifest["ds_20261006_dev1b"] = wrong
    for flags in (("--step0",), ("--explain", "all"), ("--run", "all")):
        env.conn.log.clear()
        code, report = env.run(*flags)
        assert code == 3 and report["verdict"]["overall"] == "REFUSED" and env.conn.executed() == [wa.SQL_STEP0] and not [s for s, _ in env.conn.log if s.startswith("EXPLAIN ")], flags
        assert report["step0"]["identity"]["ds_20261006_dev1b"]["samples_file_sha256_in_db"] == "0" * 64


def test_refusals_happen_before_any_connection(env):
    assert env.run("--run", "all")[0] == 0
    env.connect_calls = 0
    assert wa.main(["--db", "hotel_price_intel", "--gpt-approval", str(env.tmp_path / "34-gpt-approval.md"), "--crawl-evidence", str(env.tmp_path / "evidence.json"), "--step0"]) == 3
    env.write_approval(controller="0" * 64)
    assert env.run("--step0")[0] == 3
    env.write_approval(modes="step0, explain")
    assert env.run("--run", "all")[0] == 3                                                                   # run chua duoc duyet
    env.write_approval()
    env.now = vn(10, 0)
    env.write_evidence()
    assert env.run("--step0")[0] == 3                                                                        # trong khung crawl
    env.now = vn(18, 0)
    env.write_evidence(active_items=7)
    assert env.run("--step0")[0] == 3                                                                        # co item dang chay
    env.write_evidence()
    env.now = vn(18, 20)
    assert env.run("--step0")[0] == 3                                                                        # bang chung het han
    env.now = vn(18, 0)
    (env.tmp_path / "evidence.json").unlink()
    assert env.run("--step0")[0] == 3                                                                        # khong co bang chung
    assert env.connect_calls == 0


def test_invalid_family_is_a_usage_error_and_missing_evidence_or_approval_exit_2(env):
    assert env.run("--run", "q9")[0] == 2
    with pytest.raises(SystemExit):
        wa.main(["--step0", "--gpt-approval", "x"])
    assert env.connect_calls == 0


def test_a_connection_failure_is_refused_not_passed(env):
    def boom(db):
        raise RuntimeError("cannot connect")
    env.monkeypatch.setattr(wa, "connect", boom)
    code, report = env.run("--step0")
    assert code == 3 and report["verdict"]["overall"] == "REFUSED" and any("ket noi" in r for r in report["verdict"]["reasons"])


def test_summarize_never_returns_pass_full_without_every_family():
    base = {"mode": "run", "requested": list(wa.FAMILIES), "refusals": [], "step0": {"all_ok": True}}
    full = {n: {f: {"status": "PASS"} for f in ("count", "q1", "q1_control", "q2_h1", "q2_h3", "q2_h7", "q2_h14", "q3", "q4")} for n in wa.DATASETS}
    assert wa.summarize({**base, "families": full})["overall"] == "PASS_FULL"
    missing = {n: {k: v for k, v in per.items() if k != "q3"} for n, per in full.items()}
    assert wa.summarize({**base, "families": missing})["overall"] == "UNVERIFIED"                            # thieu nhan q3 trong khi da yeu cau
    subset = {"mode": "run", "requested": ["count", "q1"], "refusals": [], "step0": {"all_ok": True}, "families": {n: {"count": {"status": "PASS"}, "q1": {"status": "PASS"}} for n in wa.DATASETS}}
    assert wa.summarize(subset)["overall"] == "PARTIAL_PASS"                                                  # du nhan da yeu cau, nhung khong du ho
    one_unverified = {**full, "ds_20261006_dev3b": {**full["ds_20261006_dev3b"], "q4": {"status": "UNVERIFIED"}}}
    assert wa.summarize({**base, "families": one_unverified})["overall"] == "UNVERIFIED"
    one_fail = {**full, "ds_20261006_dev1b": {**full["ds_20261006_dev1b"], "q1": {"status": "FAIL"}}}
    assert wa.summarize({**base, "families": one_fail})["exit_code"] == 1
    assert wa.summarize({**base, "families": full, "refusals": ["x"]})["overall"] == "REFUSED"
    assert wa.summarize({**base, "families": full, "step0": {"all_ok": False}})["overall"] == "REFUSED"
    assert wa.summarize({**base, "families": {}})["overall"] != "PASS_FULL"


def test_evidence_generator_records_the_verified_read_only_session_and_refuses_when_it_cannot_verify():
    import inspect
    produced = ev.build_evidence(0, 0, {"id": 66, "status": "completed"}, vn(18, 0), host="h", session_read_only=True)
    assert produced["session"] == {"read_only_verified": True, "max_execution_ms": ev.MAX_EXECUTION_MS, "statements": produced["session"]["statements"]}
    assert ev.build_evidence(0, 0, None, vn(18, 0))["session"]["read_only_verified"] is False                # mac dinh KHONG nhan vo
    source = inspect.getsource(ev.read_counts)
    assert "SET SESSION TRANSACTION READ ONLY" in source and "MAX_EXECUTION_TIME" in source and "transaction_read_only" in source.replace("SQL_SESSION_RO", "transaction_read_only") and "rollback" in source
    assert source.index("SET SESSION TRANSACTION READ ONLY") < source.index("SQL_ACTIVE_RUNS")             # dat READ ONLY truoc moi SELECT
    assert "khong xac minh duoc" in source and "raise RuntimeError" in source
    assert all(sql.lstrip().upper().startswith("SELECT") for sql in (ev.SQL_ACTIVE_RUNS, ev.SQL_ACTIVE_ITEMS, ev.SQL_LATEST_RUN, ev.SQL_SESSION_RO))


# =========================================================================== follow-up cua GPT file 34 (A cache theo dataset, B strict summarize, C gate suot run, D READ ONLY, Q3 packed/pilot)
def test_summarize_requires_exact_dataset_and_label_sets_and_every_status_equal_to_pass():
    labels = ("count", "q1", "q1_control", "q2_h1", "q2_h3", "q2_h7", "q2_h14", "q3", "q4")
    base = {"mode": "run", "requested": list(wa.FAMILIES), "refusals": [], "step0": {"all_ok": True}}

    def families(status="PASS", labels_=labels):
        return {n: {f: {"status": status} for f in labels_} for n in wa.DATASETS}
    assert wa.summarize({**base, "families": families()})["overall"] == "PASS_FULL"
    for status in ("NOT_RUN", "PENDING", "UNKNOWN", None, "pass"):                                           # du 18 entry nhung status la => khong PASS
        assert wa.summarize({**base, "families": families(status)})["overall"] != "PASS_FULL", status
    swapped = families(labels_=tuple(l if l != "q3" else "q9" for l in labels))                               # van du 18 entry nhung doi 1 nhan that bang nhan la
    assert wa.summarize({**base, "families": swapped})["overall"] == "UNVERIFIED"
    extra = families()
    extra["ds_20261006_dev1b"]["bonus"] = {"status": "PASS"}
    assert wa.summarize({**base, "families": extra})["overall"] == "UNVERIFIED"
    one_dataset = {"ds_20261006_dev1b": families()["ds_20261006_dev1b"]}
    assert wa.summarize({**base, "families": one_dataset})["overall"] == "UNVERIFIED"
    assert wa.summarize({**base, "families": {**families(), "ds_other": families()["ds_20261006_dev1b"]}})["overall"] == "UNVERIFIED"
    in_run = families()
    in_run["ds_20261006_dev1b"]["q4"] = {"status": "PLAN_OK"}                                                  # PLAN_OK trong mode run khong phai ket qua
    assert wa.summarize({**base, "families": in_run})["overall"] == "UNVERIFIED"
    explain_base = {"mode": "explain", "requested": list(wa.FAMILIES), "refusals": [], "step0": {"all_ok": True}}
    assert wa.summarize({**explain_base, "families": families("PLAN_OK")})["overall"] == "EXPLAIN_OK"
    assert wa.summarize({**explain_base, "families": swapped_status(families("PLAN_OK"))})["overall"] == "EXPLAIN_REFUSED"
    assert wa.summarize({**explain_base, "families": {"ds_20261006_dev1b": families("PLAN_OK")["ds_20261006_dev1b"]}})["overall"] == "EXPLAIN_REFUSED"


def swapped_status(fams):
    fams["ds_20261006_dev3b"]["q3"] = {"status": "PLAN_OK"}
    fams["ds_20261006_dev3b"]["q9"] = fams["ds_20261006_dev3b"].pop("q4")                                    # giu nguyen so luong, doi mot nhan that
    return fams


def test_run_requires_explicit_budget_and_query_limits_no_defaults(env):
    argv = ["--gpt-approval", str(env.tmp_path / "34-gpt-approval.md"), "--crawl-evidence", str(env.tmp_path / "evidence.json"), "--run", "all"]
    assert wa.main(argv) == 2 and env.connect_calls == 0
    assert wa.main([*argv, "--budget-seconds", "600"]) == 2 and env.connect_calls == 0
    assert wa.main([*argv, "--max-queries", "50"]) == 2 and env.connect_calls == 0
    code, report = env.run("--step0")                                                                          # preflight co mac dinh hep
    assert report["budget_seconds"] == wa.DEFAULT_PREFLIGHT_BUDGET_S == 120 and report["max_queries"] == wa.DEFAULT_PREFLIGHT_MAX_QUERIES


def test_plans_are_cached_per_dataset_label_and_sql_and_record_what_was_explained(env):
    code, report = env.run("--explain", "all")
    plans = report["plans"]
    assert len(plans) == 2 * 10 and len({k.split("|")[0] for k in plans}) == 2
    q3 = [p for p in plans.values() if p["label"] == "q3_rows"]
    assert {p["dataset"] for p in q3} == set(wa.DATASETS) and all(p["representative"] is False and p["params"][-2:] == ["0", "1970-01-01"] for p in q3)
    assert wa.plan_key("a", "q1", "SELECT 1") != wa.plan_key("b", "q1", "SELECT 1") != wa.plan_key("b", "q1", "SELECT 2")
    assert report["explains_executed"] == 20 and report["queries_executed"] == 0
    env.conn.log.clear()
    env.world.plan_type = {"po": "ALL"}                                                                        # ca hai dataset bi tu choi RIENG
    code, report = env.run("--explain", "q4")
    assert code == 4 and sum(1 for sql, _ in env.conn.log if sql.startswith("EXPLAIN ")) == 2


def test_run_uses_a_representative_plan_for_q3_rows_from_a_real_group(env):
    code, report = env.run("--run", "q3")
    q3_plans = [p for p in report["plans"].values() if p["label"] == "q3_rows"]
    assert code == 4                                                                                           # chi q3 (+count): PARTIAL_PASS
    assert q3_plans and all(p["representative"] is True and p["params"][-2:] != ["0", "1970-01-01"] for p in q3_plans)         # EXPLAIN bang tham so cua nhom THAT dau tien


def _first_data_queries(env, after, action):
    state = {"n": 0}

    def hook(sql):
        state["n"] += 1
        if state["n"] == after:
            action()
    env.world.on_query = hook
    return state


def test_losing_the_crawler_gate_mid_run_stops_without_more_queries_and_is_unverified_not_fail(env):
    state = _first_data_queries(env, 2, lambda: env.write_evidence(active_runs=1))                            # crawler bat giua chung (gia han bang chung bao active)
    code, report = env.run("--run", "all")
    assert code == 4 and report["verdict"]["overall"] == "UNVERIFIED" and "FAIL" not in report["verdict"]["overall"]
    assert "cong crawler" in report["stopped_reason"] and "active_runs=1" in report["stopped_reason"]
    assert state["n"] == 2                                                                                     # khong query du lieu nao nua sau khi mat cong
    assert all(res["status"] in ("PASS", "UNVERIFIED") for per in report["families"].values() for res in per.values())


def test_expired_evidence_without_renewal_stops_the_run_and_renewal_lets_it_continue(env):
    _first_data_queries(env, 3, lambda: setattr(env, "now", vn(18, 12)))                                        # qua han 18:10, khong ai gia han
    code, report = env.run("--run", "all")
    assert code == 4 and report["verdict"]["overall"] == "UNVERIFIED" and "het han" in report["stopped_reason"]
    env.conn.log.clear()
    env.now = vn(18, 0)
    env.write_evidence()
    renew = _first_data_queries(env, 3, lambda: (setattr(env, "now", vn(18, 12)), env.write_evidence()))     # nguon van hanh gia han bang tep MOI (timestamp that, khong sua TTL)
    code, report = env.run("--run", "all")
    assert code == 0 and report["verdict"]["overall"] == "PASS_FULL" and len(report["crawl_evidence"]["sha_chain"]) == 2 and report["stopped_reason"] is None
    assert renew["n"] > 3


def test_a_renewal_that_tampers_with_ttl_or_proves_nothing_is_not_accepted(env):
    long_ttl = lambda: env.write_evidence(expires_at_utc="2026-10-08T12:00:00Z")                              # tu keo dai han
    _first_data_queries(env, 2, long_ttl)
    code, report = env.run("--run", "all")
    assert code == 4 and "han qua dai" in report["stopped_reason"]
    env.world.on_query = None
    env.write_evidence()                                                                                       # dat lai bang chung hop le truoc lan chay thu hai
    _first_data_queries(env, 2, lambda: env.write_evidence(session={"read_only_verified": False}))
    code, report = env.run("--run", "all")
    assert code == 4 and "READ ONLY" in report["stopped_reason"]


def test_the_run_stops_before_the_crawl_window_with_a_margin(env):
    _first_data_queries(env, 3, lambda: setattr(env, "now", vn(0, 25)))                                        # 00:25 + 10 phut margin cham 00:30
    code, report = env.run("--run", "all")
    assert code == 4 and "khung crawl" in report["stopped_reason"] and report["verdict"]["overall"] == "UNVERIFIED"


def test_evidence_gate_rereads_the_file_at_most_once_per_interval(tmp_path, monkeypatch):
    path = tmp_path / "e.json"
    path.write_text(json.dumps(_evidence(vn(18, 0))), encoding="utf-8")
    monkeypatch.setattr(wa, "_now_utc", lambda: vn(18, 1))
    gate = wa.EvidenceGate(path, interval_s=1000)
    gate.check()
    path.write_text(json.dumps(_evidence(vn(18, 0), active_runs=3)), encoding="utf-8")
    gate.check()                                                                                               # trong khoang: chua doc lai
    assert gate.checks == 1
    with pytest.raises(wa.GateLost):
        gate.check(force=True)
    path.unlink()
    with pytest.raises(wa.GateLost):
        gate.check(force=True)


# --------------------------------------------------------------------------- Q3: packed == point, oversized, pilot
def _grow(env, monkeypatch):
    """3 nhom: (09-02: 2 dong), (09-05: 2 dong), (09-06: 3 dong). Chi chay --run q3 (+count)."""
    D = dt.date
    w = env.world
    for record, day, sel in ((21, 5, 1), (22, 5, 0), (23, 6, 1), (24, 6, 0), (25, 6, 0)):
        w.samples[record] = {"date": D(2026, 9, day), "sel": sel, "t": {1: None, 3: None, 7: None, 14: None}}
        w.obs[record] = {"hotel": "h", "checkin": D(2026, 9, 10), "observed_at": dt.datetime(2026, 9, day, 3, 0, record - 20), "price": 100, "sold_out": 0, "item": 900 + record, "run": 70 + (record % 2)}
        w.item_map[900 + record] = {"ownership_status": "owner_success", "source_code": "local_primary", "source_item_id": 5000 + record}
    w.count_selected = 6
    env.frame = pd.concat([env.frame, env.frame.iloc[[0, 0]].assign(warehouse_record_id=[21, 23])], ignore_index=True)
    monkeypatch.setattr(wa, "EXPECTED_SELECTED", 6)
    monkeypatch.setattr(wa, "EXPECTED_SUPERSEDED", 4)


def _q3_state(report, dataset="ds_20261006_dev1b"):
    return report["families"][dataset]["q3"]


def test_packing_keeps_total_rows_under_the_cap_and_never_truncates_oversized_groups():
    groups = [{"n": 2}, {"n": 3}, {"n": 5}, {"n": 9}, {"n": 4}, {"n": 6}]
    batches, oversized = wa.pack_groups(groups, cap=8, max_groups=100)
    assert [sum(g["n"] for g in b) for b in batches] == [5, 5, 4, 6] and [g["n"] for g in oversized] == [9]
    assert sum(len(b) for b in batches) + len(oversized) == len(groups)                                       # khong mat nhom
    capped, _ = wa.pack_groups([{"n": 1}] * 7, cap=100, max_groups=3)
    assert [len(b) for b in capped] == [3, 3, 1]                                                              # gioi han so nhom/predicate


def test_packed_mode_gives_the_same_verdict_digest_as_the_point_baseline_with_fewer_queries(env, monkeypatch):
    _grow(env, monkeypatch)
    monkeypatch.setattr(wa, "BATCH", 6)
    code_point, point = env.run("--run", "q3", "--q3-mode", "point")
    point_queries = [s for s in env.conn.executed() if s == wa.SQL_Q3_ROWS]
    env.conn.log.clear()
    code_packed, packed = env.run("--run", "q3", "--q3-mode", "packed")
    packed_queries = [s for s in env.conn.executed() if s.startswith(PACKED_HEAD)]
    sp, sk = _q3_state(point), _q3_state(packed)
    assert sp["status"] == sk["status"] == "PASS", (sp.get("checks"), sk.get("checks"))
    assert sp["state"]["verdict_digest"] == sk["state"]["verdict_digest"] and sp["state"]["groups_evaluated"] == sk["state"]["groups_evaluated"] == 3
    assert sp["state"]["superseded_rows"] == sk["state"]["superseded_rows"] == 4
    assert sp["state"]["mode"] == "point" and sk["state"]["mode"] == "packed"
    assert len(point_queries) == 6 and len(packed_queries) < len(point_queries)                               # 3 nhom x 2 dataset vs gom nhom


def test_packed_mode_reports_oversized_groups_as_unverified_instead_of_truncating(env, monkeypatch):
    _grow(env, monkeypatch)                                                                                    # BATCH=2: nhom 09-06 co 3 dong > tran 2
    code, report = env.run("--run", "q3", "--q3-mode", "packed")
    q3 = _q3_state(report)
    assert q3["state"]["oversized_groups"] == 1 and q3["status"] == "UNVERIFIED" and "oversized" in q3["reason"] and code == 4
    code_point, point = env.run("--run", "q3", "--q3-mode", "point")
    assert _q3_state(point)["status"] == "PASS"                                                               # point doc du 3 dong (tran query = BATCH+1)


def test_pilot_groups_limit_is_never_a_full_pass_but_still_reports_violations(env, monkeypatch):
    _grow(env, monkeypatch)
    code, report = env.run("--run", "q3", "--pilot-groups", "1")
    q3 = _q3_state(report)
    assert q3["status"] == "UNVERIFIED" and q3["state"]["pilot_limited"] is True and q3["state"]["groups_evaluated"] == 1 and q3["state"]["groups_total"] is None and q3["state"]["groups_seen"] == 2
    assert "superseded_rows_equal_expected" not in q3["checks"] and code == 4 and report["verdict"]["overall"] != "PASS_FULL"
    env.world.drop_run_map = True                                                                              # co loi lineage trong pilot => van FAIL (khong an loi vi la pilot)
    code, report = env.run("--run", "q3", "--pilot-groups", "1")
    assert _q3_state(report)["status"] == "FAIL" and code == 1


def test_query_count_and_time_budgets_are_enforced_and_end_unverified(env):
    code, report = env.run("--run", "all", "--budget-seconds", "600", "--max-queries", "5")
    assert code == 4 and report["verdict"]["overall"] == "UNVERIFIED" and report["statements_total"] == 5 and report["queries_executed"] + report["explains_executed"] == 2   # 3 metadata + 1 EXPLAIN + 1 COUNT
    assert report["metadata_statements"] == 3 and wa.SQL_Q1 not in env.conn.executed()
    env.conn.log.clear()
    code, report = env.run("--run", "all", "--budget-seconds", "0", "--max-queries", "5000")
    assert code == 3 and env.conn.log == [] and "truoc Buoc 0" in report["verdict"]["reasons"][0]                    # khong du margin cho mot query: khong chay ke ca Buoc 0


def test_q3_group_size_mismatch_and_rows_from_other_groups_are_errors(env):
    env.world.q3_extra_rows = 1                                                                                # nhom tra 3 dong trong khi n = 2
    code, report = env.run("--run", "q3")
    q3 = _q3_state(report)
    assert q3["status"] == "FAIL" and q3["state"]["size_mismatch"] == 1 and q3["checks"]["group_sizes_match"] is False
    env.world.q3_extra_rows = 0
    env.world.q3_leak_other_group = True                                                                       # dong thuoc ngay khac lot vao ket qua
    code, report = env.run("--run", "q3")
    q3 = _q3_state(report)
    assert q3["status"] == "FAIL" and q3["state"]["unexpected_group_rows"] >= 1 and q3["checks"]["no_rows_outside_requested_groups"] is False


def test_packed_explain_mode_plans_the_packed_shape_for_each_dataset(env):
    code, report = env.run("--explain", "q3", "--q3-mode", "packed")
    labels = sorted((p["dataset"], p["label"], p["representative"]) for p in report["plans"].values() if p["label"].startswith("q3_rows"))
    assert code == 0 and labels == [(d, "q3_rows_packed:2", False) for d in sorted(wa.DATASETS)]
    assert env.conn.executed() == [wa.SQL_STEP0]


# =========================================================================== GPT file 36: renewal 60 s / age <= 120 s, pilot ghim truoc, compare point<->packed, report khong ghi de
def test_gate_requires_schema_v2_observed_time_and_rejects_evidence_older_than_120_seconds_even_if_not_expired(env):
    old = _evidence(vn(18, 0))
    assert wa.validate_crawl_evidence(old, vn(18, 1), max_age_s=wa.EVIDENCE_MAX_AGE_S)[0] is True              # tuoi 60 s
    ok, why = wa.validate_crawl_evidence(old, vn(18, 3), max_age_s=wa.EVIDENCE_MAX_AGE_S)                       # chua het han (TTL 600) nhung tuoi 180 s
    assert not ok and "qua cu" in why
    assert wa.validate_crawl_evidence(old, vn(18, 3))[0] is True                                                # khong gioi han tuoi neu khong yeu cau
    assert wa.validate_crawl_evidence({**old, "schema": "crawl_idle_evidence_v1"}, vn(18, 1))[0] is False       # schema cu bi tu choi
    missing = {k: v for k, v in old.items() if k != "observed_at_utc"}
    assert wa.validate_crawl_evidence(missing, vn(18, 1))[0] is False
    env.now = vn(18, 3)                                                                                          # evidence cu 3 phut ngoai gate
    assert env.run("--step0")[0] == 3


def test_age_is_measured_from_the_start_of_the_read_not_from_publication(env):
    published = vn(18, 0) + dt.timedelta(seconds=100)                                                           # doc dem tu 18:00:00, xuat ban 18:01:40 (doc cham)
    produced = ev.build_evidence(0, 0, {"id": 66, "status": "completed"}, vn(18, 0), session_read_only=True, published_at_utc=published)
    assert produced["observed_at_utc"] != produced["generated_at_utc"]
    assert wa.validate_crawl_evidence(produced, vn(18, 1) + dt.timedelta(seconds=50), max_age_s=120)[0] is True    # tuoi 110 s tu luc BAT DAU doc
    ok, why = wa.validate_crawl_evidence(produced, vn(18, 2) + dt.timedelta(seconds=30), max_age_s=120)           # chi 50 s sau xuat ban nhung tuoi 150 s tu luc doc
    assert not ok and "qua cu" in why


def test_the_run_stops_when_renewal_stops_and_old_evidence_becomes_older_than_the_age_bound(env):
    _first_data_queries(env, 3, lambda: setattr(env, "now", vn(18, 3)))                                          # khong ai gia han trong 3 phut
    code, report = env.run("--run", "all")
    assert code == 4 and report["verdict"]["overall"] == "UNVERIFIED" and "qua cu" in report["stopped_reason"]


def test_receipts_record_observed_generated_expiry_and_age_once_per_sha(env):
    renew = _first_data_queries(env, 3, lambda: (setattr(env, "now", vn(18, 1)), env.write_evidence()))
    code, report = env.run("--run", "all")
    receipts = report["crawl_evidence"]["receipts"]
    assert code == 0 and len(receipts) == 2 and [r["sha256"] for r in receipts] == report["crawl_evidence"]["sha_chain"]
    assert all({"observed_at_utc", "generated_at_utc", "expires_at_utc", "age_s_at_first_use"} <= set(r) for r in receipts) and receipts[1]["age_s_at_first_use"] == 0.0
    assert renew["n"] > 3


def test_metadata_step0_select_counts_against_the_statement_budget_and_reports_are_never_overwritten(env, monkeypatch):
    code, report = env.run("--run", "all", "--budget-seconds", "600", "--max-queries", "5")
    assert report["statements_total"] == 5 and report["metadata_statements"] == 3 and report["queries_executed"] + report["explains_executed"] == 2   # 2 SET + Buoc 0 + EXPLAIN + COUNT = tran 5
    first = report["run_id"]
    code, report = env.run("--step0")
    assert report["run_id"] != first
    names = [p.name for p in (env.tmp_path / "out").glob("*.json")]
    assert len(names) == len(set(names)) >= 2 and all(first in n or report["run_id"] in n or "pilot_manifest" in n for n in names)
    monkeypatch.setattr(wa.time, "strftime", lambda fmt: "T")
    wa.write_unique_json("x", {"a": 1}, "rid")
    with pytest.raises(FileExistsError):
        wa.write_unique_json("x", {"a": 2}, "rid")                                                                # 'xb': tu choi ghi de


def test_pilot_is_never_pass_full_even_when_every_subset_check_passes(env):
    code, report = env.run("--run", "all", "--max-batches", "5")                                                  # khong cham tran batch => moi ho PASS, nhung day la pilot
    statuses = [res["status"] for per in report["families"].values() for res in per.values()]
    assert report["pilot"] is True and all(s == "PASS" for s in statuses)
    assert report["verdict"]["overall"] == "PILOT_PARTIAL" and code == 4


def test_pilot_manifests_are_pinned_to_files_before_the_verdicts_are_computed(env, monkeypatch):
    _grow(env, monkeypatch)
    seen = {"prefix": None, "q3_groups": None}

    def hook(sql):
        names = [p.name for p in (env.tmp_path / "out").glob("pilot_manifest_*.json")]
        if sql == wa.SQL_Q1 and seen["prefix"] is None:
            seen["prefix"] = [n for n in names if "prefix" in n]
        if sql == wa.SQL_Q3_ROWS and seen["q3_groups"] is None:
            seen["q3_groups"] = [n for n in names if "q3_groups" in n]
    env.world.on_query = hook
    code, report = env.run("--run", "all", "--pilot-groups", "2", "--max-batches", "2", "--q3-mode", "compare")
    assert seen["prefix"] and seen["q3_groups"]                                                                    # ton tai TRUOC query Q1 dau tien / Q3 rows dau tien
    manifests = report["pilot_manifests"]
    kinds = sorted((m["kind"], m["dataset"]) for m in manifests)
    assert kinds == sorted([("prefix", d) for d in wa.DATASETS] + [("q3_groups", d) for d in wa.DATASETS])
    for entry in manifests:
        data = json.loads(Path(entry["path"]).read_text(encoding="utf-8"))
        assert hashlib_sha(Path(entry["path"]).read_text(encoding="utf-8")) == entry["sha256"] and data["controller_sha256"] == wa.controller_sha256() and data["sql_set_sha256"] == wa.sql_set_sha256()
        if entry["kind"] == "prefix":
            assert data["expected_prefix_ids"] == sorted(int(x) for x in env.frame["warehouse_record_id"])[:2 * wa.BATCH] and data["batches"] == 2 and data["expected_prefix_ids_sha256"]
        else:
            assert len(data["groups"]) == 2 and data["sum_n"] == sum(g["n"] for g in data["groups"]) and data["packed_batches"]


def test_prefix_key_set_must_equal_the_parquet_prefix_not_the_full_cohort(env):
    env.world.drop_q1 = {12}                                                                                      # DB tra {11,13} thay vi {11,12} o batch dau
    code, report = env.run("--run", "all", "--max-batches", "1")
    q1 = report["families"]["ds_20261006_dev1b"]["q1"]
    assert q1["coverage"]["scope"] == "prefix" and q1["coverage"]["ok"] is False and q1["status"] == "FAIL" and code == 1
    env.world.drop_q1 = set()
    code, report = env.run("--run", "all", "--max-batches", "1")
    q1 = report["families"]["ds_20261006_dev1b"]["q1"]
    assert q1["coverage"]["scope"] == "prefix" and q1["coverage"]["ok"] is True and q1["status"] == "UNVERIFIED"   # prefix dung nhung chua day du


def test_prefix_q2_uses_the_parquet_subset_for_labelled_counts_and_q1_control_reports_when_not_exercised(env):
    code, report = env.run("--run", "all", "--max-batches", "1")
    q2 = report["families"]["ds_20261006_dev1b"]["q2_h1"]
    assert q2["coverage"]["scope"] == "prefix" and q2["labelled_rows"] == q2["parquet_labelled"] == 2 and "labelled_equals_expected" not in q2["checks"]   # khong so 3 cua cohort
    control = report["families"]["ds_20261006_dev1b"]["q1_control"]
    assert control["control_status"] == "EXERCISED" and "burn_in_equals_expected" not in control["checks"]
    for m in env.world.matches:
        m["before_approval"] = 0
    code, report = env.run("--run", "all", "--max-batches", "1")
    control = report["families"]["ds_20261006_dev1b"]["q1_control"]
    assert control["control_status"] == "CONTROL_NOT_EXERCISED" and control["status"] == "UNVERIFIED"             # zero tren prefix khong phai bang chung


def test_compare_mode_requires_zero_point_packed_mismatches_and_reports_each_kind(env, monkeypatch):
    _grow(env, monkeypatch)
    monkeypatch.setattr(wa, "BATCH", 6)
    code, report = env.run("--run", "q3", "--q3-mode", "compare")
    q3 = _q3_state(report)
    eq = q3["state"]["equivalence"]
    assert q3["status"] == "PASS" and q3["checks"]["point_equals_packed"] is True and eq["groups_compared"] == 3 and eq["groups_with_mismatch"] == 0
    assert all(eq[k] == 0 for k in ("missing_rows", "extra_rows", "value_mismatch", "verdict_mismatch", "unexpected_rows"))
    original = env.world.q3_rows_packed

    def drop_one(params):
        return [r for r in original(params) if r["record_id"] != 22]
    env.world.q3_rows_packed = drop_one
    code, report = env.run("--run", "q3", "--q3-mode", "compare")
    q3 = _q3_state(report)
    assert q3["status"] == "FAIL" and q3["state"]["equivalence"]["missing_rows"] == 1 and q3["checks"]["point_equals_packed"] is False

    def extra_row(params):
        rows = original(params)
        return rows + [dict(rows[0], record_id=888)]
    env.world.q3_rows_packed = extra_row
    code, report = env.run("--run", "q3", "--q3-mode", "compare")
    assert _q3_state(report)["state"]["equivalence"]["extra_rows"] >= 1 and _q3_state(report)["status"] == "FAIL"

    def alter_value(params):
        return [dict(r, source_item_id=(r["source_item_id"] or 0) + 1) if r["record_id"] == 21 else r for r in original(params)]
    env.world.q3_rows_packed = alter_value
    code, report = env.run("--run", "q3", "--q3-mode", "compare")
    assert _q3_state(report)["state"]["equivalence"]["value_mismatch"] >= 1 and _q3_state(report)["status"] == "FAIL"

    def flip_selected(params):
        return [dict(r, is_daily_snapshot_selected=1 - r["is_daily_snapshot_selected"]) if r["record_id"] in (21, 22) else r for r in original(params)]
    env.world.q3_rows_packed = flip_selected
    code, report = env.run("--run", "q3", "--q3-mode", "compare")
    eq = _q3_state(report)["state"]["equivalence"]
    assert eq["value_mismatch"] >= 1 and eq["verdict_mismatch"] >= 1 and _q3_state(report)["status"] == "FAIL"


def test_compare_mode_with_an_oversized_group_is_unverified_not_pass_and_the_point_baseline_still_checks(env, monkeypatch):
    _grow(env, monkeypatch)                                                                                       # BATCH=2: nhom 09-06 co 3 dong => oversized cho packed
    code, report = env.run("--run", "q3", "--q3-mode", "compare")
    q3 = _q3_state(report)
    assert q3["status"] == "UNVERIFIED" and q3["state"]["equivalence"]["packed_oversized_groups"] == 1 and q3["state"]["groups_evaluated"] == 3 and code == 4
    assert q3["checks"]["point_equals_packed"] is True                                                            # phan so sanh duoc van 0 mismatch


def test_compare_explain_mode_plans_both_shapes_and_executes_no_data_query(env):
    code, report = env.run("--explain", "q3", "--q3-mode", "compare")
    labels = sorted((p["dataset"], p["label"]) for p in report["plans"].values() if p["label"].startswith("q3_rows"))
    assert code == 0 and labels == sorted([(d, "q3_rows") for d in wa.DATASETS] + [(d, "q3_rows_packed:2") for d in wa.DATASETS]) and env.conn.executed() == [wa.SQL_STEP0]


# --------------------------------------------------------------------------- generator: xuat ban nguyen tu + vong gia han co han
class FakeClock:
    def __init__(self, start):
        self.now, self.mono = start, 0.0

    def __call__(self):
        return self.now

    def sleep(self, seconds):
        self.now += dt.timedelta(seconds=seconds)
        self.mono += seconds

    def monotonic(self):
        return self.mono


def test_publish_is_atomic_archives_by_sha_and_records_observed_before_publication(tmp_path):
    clock = FakeClock(vn(18, 0))
    reads = iter([(0, 0, {"id": 65, "status": "completed"}, True)])

    def slow_read():
        clock.sleep(5)                                                                                            # doc dem mat 5 s: observed != generated
        return next(reads)
    out, archive = tmp_path / "live.json", tmp_path / "archive"
    result = ev.publish(out, archive_dir=archive, read=slow_read, clock=clock, host="h")
    data = json.loads(out.read_text(encoding="utf-8"))
    assert data["schema"] == ev.SCHEMA and data["observed_at_utc"] != data["generated_at_utc"] and data["session"]["read_only_verified"] is True
    assert (archive / f"crawl_idle_{result['sha256']}.json").read_bytes() == out.read_bytes()
    assert not list(tmp_path.glob(".*.tmp")) and wa.validate_crawl_evidence(data, vn(18, 1), max_age_s=120)[0] is True


def test_renew_loop_is_bounded_stops_on_busy_and_never_touches_the_live_file_on_errors(tmp_path):
    clock = FakeClock(vn(18, 0))
    out, archive = tmp_path / "live.json", tmp_path / "archive"
    sequence = [(0, 0, {"id": 65, "status": "completed"}, True)] * 3
    logs = []
    code = ev.renew_loop(out, duration_s=150, interval_s=60, archive_dir=archive, read=lambda: sequence.pop(0), clock=clock, sleep=clock.sleep, monotonic=clock.monotonic, log=logs.append)
    assert code == 0 and not sequence and len(list(archive.glob("*.json"))) == 3                                  # 3 lan xuat ban trong 150 s voi chu ky 60 s, roi dung (CO HAN)
    stamps = [json.loads(p.read_text(encoding="utf-8"))["observed_at_utc"] for p in sorted(archive.glob("*.json"))]
    assert len(set(stamps)) == 3
    before = out.read_bytes()
    clock2 = FakeClock(vn(18, 10))

    def broken():
        raise RuntimeError("db down")
    code = ev.renew_loop(out, duration_s=300, interval_s=60, archive_dir=archive, read=broken, clock=clock2, sleep=clock2.sleep, monotonic=clock2.monotonic, log=logs.append)
    assert code == 2 and out.read_bytes() == before                                                                # loi => khong cham tep live, khong keo dai han
    busy_seq = [(0, 0, {"id": 65, "status": "completed"}, True), (1, 4248, {"id": 66, "status": "running"}, True)]
    clock3 = FakeClock(vn(18, 20))
    code = ev.renew_loop(out, duration_s=600, interval_s=60, archive_dir=archive, read=lambda: busy_seq.pop(0), clock=clock3, sleep=clock3.sleep, monotonic=clock3.monotonic, log=logs.append)
    published = json.loads(out.read_text(encoding="utf-8"))
    assert code == 1 and published["active_runs"] == 1 and published["active_items"] == 4248                      # phat hien crawler => xuat ban busy roi dung
    assert wa.validate_crawl_evidence(published, vn(18, 21), max_age_s=120)[0] is False


# =========================================================================== GPT file 36a: gate/ngan sach/thoi gian kiem NGAY TRUOC tung lenh SQL (EXPLAIN, SELECT, Buoc 0)
def _data_selects(env):
    return [sql for sql in env.conn.executed() if sql != wa.SQL_STEP0]


def test_evidence_revoked_while_an_explain_runs_prevents_the_select_that_follows(env):
    env.world.on_explain = lambda sql: env.write_evidence(active_runs=1)                                           # crawler bat ngay trong luc EXPLAIN
    code, report = env.run("--run", "all")
    assert code == 4 and _data_selects(env) == [] and report["verdict"]["overall"] == "UNVERIFIED" and "active_runs=1" in report["stopped_reason"]
    assert report["families"]["ds_20261006_dev1b"]["count"]["status"] == "UNVERIFIED"


@pytest.mark.parametrize("action,fragment", [(lambda env: setattr(env, "now", vn(18, 12)), "het han"), (lambda env: setattr(env, "now", vn(0, 25)), "khung crawl"),
                                             (lambda env: setattr(env, "now", vn(18, 3)), "qua cu")])
def test_expiry_cutoff_or_age_reached_during_an_explain_prevents_the_select_that_follows(env, action, fragment):
    env.world.on_explain = lambda sql: action(env)
    code, report = env.run("--run", "all")
    assert code == 4 and _data_selects(env) == [] and fragment in report["stopped_reason"]


def test_an_explain_that_consumes_the_remaining_budget_prevents_the_select_that_follows(env, monkeypatch):
    clock = {"t": 1000.0}
    monkeypatch.setattr(wa, "_monotonic", lambda: clock["t"])                                                       # dong ho don dieu ca controller dung
    env.world.on_explain = lambda sql: clock.__setitem__("t", clock["t"] + 95)
    code, report = env.run("--run", "all", "--budget-seconds", "100", "--max-queries", "50")
    count = report["families"]["ds_20261006_dev1b"]["count"]
    assert code == 4 and _data_selects(env) == [] and count["status"] == "UNVERIFIED" and "ngan sach" in count["reason"]


def test_gate_lost_between_connect_and_step0_runs_no_sql_at_all(env, monkeypatch):
    original = env._connect

    def connect_then_busy(db):
        conn = original(db)
        env.write_evidence(active_items=5)                                                                          # busy ngay sau connect, truoc Buoc 0
        return conn
    monkeypatch.setattr(wa, "connect", connect_then_busy)
    code, report = env.run("--step0")
    assert code == 3 and env.conn.log == [] and "truoc Buoc 0" in report["verdict"]["reasons"][0] and "active_items=5" in report["verdict"]["reasons"][0]


def test_a_slow_connect_consumes_the_shared_budget_before_step0(env, monkeypatch):
    clock = {"t": 50.0}
    monkeypatch.setattr(wa, "_monotonic", lambda: clock["t"])
    original = env._connect

    def slow_connect(db):
        conn = original(db)
        clock["t"] += 115                                                                                           # budget preflight mac dinh 120 s
        return conn
    monkeypatch.setattr(wa, "connect", slow_connect)
    code, report = env.run("--step0")
    assert code == 3 and env.conn.log == [] and "ngan sach" in report["verdict"]["reasons"][0]


def test_budget_clock_is_shared_across_datasets_and_never_reset(env, monkeypatch):
    clock = {"t": 0.0}
    monkeypatch.setattr(wa, "_monotonic", lambda: clock["t"])
    starts = []

    def hook(sql):
        starts.append(clock["t"])                                                                                    # elapsed LUC KHOI DONG moi query du lieu
        clock["t"] += 30
    env.world.on_query = hook
    code, report = env.run("--run", "all", "--budget-seconds", "100", "--max-queries", "5000")
    assert code == 4 and starts and max(starts) + wa.MAX_EXECUTION_MS / 1000 <= 100                                  # khong query nao khoi dong khi con < 1 margin query
    second = report["families"]["ds_20261006_dev3b"]["count"]                                                        # dataset thu hai KHONG duoc cap ngan sach moi
    assert second["status"] == "UNVERIFIED" and "ngan sach" in second["reason"]


def test_gate_revoked_while_step0_runs_prevents_even_the_first_explain(env):
    env.world.on_step0 = lambda: env.write_evidence(active_runs=2)
    code, report = env.run("--run", "all")
    assert code == 4 and env.conn.executed() == [wa.SQL_STEP0] and not [sql for sql, _ in env.conn.log if sql.startswith("EXPLAIN ")]       # khong EXPLAIN, khong SELECT
    assert "active_runs=2" in report["stopped_reason"] and report["explains_executed"] == 0


def test_connect_set_statements_are_counted_in_the_statement_denominator(env):
    code, report = env.run("--step0")
    assert report["metadata_statements"] == wa.CONNECT_SET_STATEMENTS + 1 == 3 and report["statements_total"] == 3


# =========================================================================== GPT file 42: null semantics (Int64/boolean nullable), chan doan row-level, khong keo builder/env
import numpy as np  # noqa: E402


@pytest.mark.parametrize("value", [None, pd.NA, pd.NaT, float("nan"), np.nan, np.float32("nan"), np.float64("nan")])
def test_every_null_scalar_flavour_is_recognised(value):
    assert wa.is_null_scalar(value) is True


@pytest.mark.parametrize("value", [0, 1, False, True, 0.0, 2, "", "x", np.int64(0), np.bool_(False), np.float64(1.5)])
def test_valid_scalars_are_never_null_including_known_false_and_zero(value):
    assert wa.is_null_scalar(value) is False


@pytest.mark.parametrize("value", [[], [1], (1,), {}, set(), np.array([1]), np.array(1.0)])
def test_non_scalars_are_rejected_explicitly_not_coerced(value):
    with pytest.raises(TypeError):
        wa.is_null_scalar(value)


def test_flag_state_separates_null_false_true_and_rejects_unexpected_domain():
    assert [wa.flag_state(v) for v in (None, pd.NA, np.nan, 0, False, np.bool_(False), 1, True, np.bool_(True))] == ["NULL", "NULL", "NULL", "0", "0", "0", "1", "1", "1"]
    for bad in (2, -1, 0.5, "yes", b"1"):
        with pytest.raises(ValueError):
            wa.flag_state(bad)


def _q4_row(**over):
    row = {"a_id": 1, "h_id": "h", "city": "Hà Nội", "a_max_occupancy": 2, "a_room_area": "20 m²", "a_breakfast_included": 1, "a_free_cancellation": 0}
    row.update(over)
    return row


def _pq_row(**over):
    row = {"city": "Hà Nội", "max_occupancy": 2, "room_area_m2": 20.0, "breakfast_included": True, "free_cancellation": False}
    row.update(over)
    return row


def test_q4_fidelity_handles_real_pandas_nullable_dtypes_without_false_positives_or_typeerror():
    int64 = pd.Series([2, None, 3], dtype="Int64").tolist()                                                      # [2, <NA>, 3] thuc te tu Parquet
    boolean = pd.Series([True, None, False], dtype="boolean").tolist()
    assert wa.q4_fidelity_violations(_q4_row(a_max_occupancy=None), _pq_row(max_occupancy=int64[1]), AREA_PARSER) == []             # DB None <-> pd.NA: cung null, KHONG vi pham
    assert wa.q4_fidelity_violations(_q4_row(a_free_cancellation=None), _pq_row(free_cancellation=boolean[1]), AREA_PARSER) == []     # truoc day nem TypeError "boolean value of NA is ambiguous"
    assert wa.q4_fidelity_violations(_q4_row(a_breakfast_included=None), _pq_row(breakfast_included=boolean[1]), AREA_PARSER) == []
    assert wa.q4_fidelity_violations(_q4_row(a_max_occupancy=3), _pq_row(max_occupancy=int64[2]), AREA_PARSER) == []


def test_q4_fidelity_still_reports_one_sided_null_and_known_false_is_never_missing_or_true():
    na_int, na_bool = pd.NA, pd.NA
    assert wa.q4_fidelity_violations(_q4_row(a_max_occupancy=3), _pq_row(max_occupancy=na_int), AREA_PARSER) == ["max_occupancy"]       # DB biet, Parquet NA
    assert wa.q4_fidelity_violations(_q4_row(a_max_occupancy=None), _pq_row(max_occupancy=3), AREA_PARSER) == ["max_occupancy"]         # DB NULL, Parquet biet
    assert wa.q4_fidelity_violations(_q4_row(a_free_cancellation=0), _pq_row(free_cancellation=na_bool), AREA_PARSER) == ["free_cancellation"]      # False da biet KHONG bang NA
    assert wa.q4_fidelity_violations(_q4_row(a_free_cancellation=None), _pq_row(free_cancellation=True), AREA_PARSER) == ["free_cancellation"]       # NULL -> True la LECH (loi nghi cua builder)
    assert wa.q4_fidelity_violations(_q4_row(a_free_cancellation=0), _pq_row(free_cancellation=True), AREA_PARSER) == ["free_cancellation"]
    assert wa.q4_fidelity_violations(_q4_row(a_free_cancellation=1), _pq_row(free_cancellation=True), AREA_PARSER) == []
    assert wa.q4_fidelity_violations(_q4_row(a_free_cancellation=0), _pq_row(free_cancellation=False), AREA_PARSER) == []
    assert wa.q4_fidelity_violations(_q4_row(a_room_area=None), _pq_row(room_area_m2=float("nan")), AREA_PARSER) == []
    assert wa.q4_fidelity_violations(_q4_row(a_room_area=None), _pq_row(room_area_m2=pd.NA), AREA_PARSER) == []


def test_q4_fidelity_rejects_unexpected_domains_and_arrays_explicitly():
    assert wa.q4_fidelity_violations(_q4_row(a_free_cancellation=2), _pq_row(), AREA_PARSER) == ["domain_error:free_cancellation"]
    assert wa.q4_fidelity_violations(_q4_row(), _pq_row(breakfast_included="yes"), AREA_PARSER) == ["domain_error:breakfast_included"]
    assert wa.q4_fidelity_violations(_q4_row(a_max_occupancy=[2]), _pq_row(), AREA_PARSER) == ["domain_error:max_occupancy"]
    assert wa.q4_fidelity_violations(_q4_row(a_id=None), _pq_row(), AREA_PARSER) == ["missing_assignment"]


def _diag(db, pq, column="free_cancellation", start=100):
    diag = wa.Q4Diagnosis()
    for offset, (d, p) in enumerate(zip(db, pq)):
        row = _q4_row(**{"a_" + column: d})
        diag.add(start + offset, row, _pq_row(**{column: p}))
    return diag


def test_gpt_counterexample_equal_marginals_with_two_wrong_non_null_rows_is_rejected_not_supported():
    db, pq = [1, 0, None], [False, True, True]
    assert sum(1 for v in pq if v is True) - sum(1 for v in db if v == 1) == sum(1 for v in db if v is None) and sum(1 for v in pq if v is False) == sum(1 for v in db if v == 0)   # marginal bi lua
    verdict = wa.evaluate_null_hypotheses(_diag(db, pq))["free_cancellation"]
    assert verdict["null_to_true"] == "REJECTED" and verdict["other_cells"] == ["0|1", "1|0"] and verdict["explains_all_mismatches"] is False


def test_null_to_true_is_supported_only_when_every_cell_is_known_correct_or_null_to_true():
    result = wa.evaluate_null_hypotheses(_diag([1, 0, None, None, 1], [True, False, True, True, True]))["free_cancellation"]
    assert result["null_to_true"] == "SUPPORTED" and result["db_null"] == 2 == result["db_null_to_parquet_true"] and result["mismatching_rows"] == 2 and result["explains_all_mismatches"] is True
    assert wa.evaluate_null_hypotheses(_diag([1, 0, None], [True, False, False]))["free_cancellation"]["null_to_true"] == "REJECTED"          # NULL -> False khong phai gia thuyet
    assert wa.evaluate_null_hypotheses(_diag([1, 0, 1], [True, False, True]))["free_cancellation"]["null_to_true"] == "NOT_EXERCISED"          # khong co DB NULL: khong duoc goi la confirmed
    mixed = wa.evaluate_null_hypotheses(_diag([None, 0, None], [True, False, True]))["free_cancellation"]
    assert mixed["null_to_true"] == "SUPPORTED" and mixed["db_null_to_parquet_true"] == 2


def test_occupancy_false_positive_requires_equal_null_id_sets_and_zero_non_null_disagreement():
    ok = wa.evaluate_null_hypotheses(_diag([2, None, 3, None], [2, pd.NA, 3, pd.NA], "max_occupancy"))["max_occupancy"]
    assert ok["controller_false_positive"] == "CONFIRMED" and ok["db_null"] == ok["parquet_na"] == 2 and ok["null_id_sets_equal"] is True
    same_count_different_ids = wa.evaluate_null_hypotheses(_diag([None, 2, 3], [2, None, 3], "max_occupancy"))["max_occupancy"]       # cung so luong, khac id
    assert same_count_different_ids["controller_false_positive"] == "REJECTED" and same_count_different_ids["null_id_sets_equal"] is False
    non_null = wa.evaluate_null_hypotheses(_diag([None, 2], [pd.NA, 4], "max_occupancy"))["max_occupancy"]
    assert non_null["controller_false_positive"] == "REJECTED" and non_null["non_null_value_mismatches"] == 1
    assert wa.evaluate_null_hypotheses(_diag([2, 3], [2, 3], "max_occupancy"))["max_occupancy"]["controller_false_positive"] == "NOT_EXERCISED"


def test_prereg_expectations_need_the_pinned_prefix_and_exactly_25_null_to_true_and_28_na():
    diag = wa.Q4Diagnosis()
    for i in range(25):
        diag.add(i, _q4_row(a_free_cancellation=None), _pq_row(free_cancellation=True))
    for i in range(25, 53):
        diag.add(i, _q4_row(a_max_occupancy=None), _pq_row(max_occupancy=pd.NA))
    assert wa.evaluate_null_hypotheses(diag, wa.PREFIX41_IDS_SHA256)["matches_prereg_expectations"] is True
    assert wa.evaluate_null_hypotheses(diag, "0" * 64)["matches_prereg_expectations"] is False                      # prefix khac pin
    diag.add(900, _q4_row(a_free_cancellation=None), _pq_row(free_cancellation=True))                              # 26 thay vi 25
    assert wa.evaluate_null_hypotheses(diag, wa.PREFIX41_IDS_SHA256)["matches_prereg_expectations"] is False


def test_diagnosis_records_domain_errors_instead_of_crashing():
    diag = wa.Q4Diagnosis()
    diag.add(1, _q4_row(a_free_cancellation=2), _pq_row())
    diag.add(2, _q4_row(), _pq_row())
    assert [e["record_id"] for e in diag.domain_errors] == [1] and diag.rows == 2 and wa.evaluate_null_hypotheses(diag)["domain_errors"] == 1


def test_controller_and_test_module_do_not_import_the_builder_env_or_settings():
    import subprocess
    code = ("import sys, importlib.util; sys.path.insert(0, sys.argv[1]);"
            "import test_warehouse_audit_v3 as t;"
            "bad = sorted(m for m in sys.modules if m.split('.')[0] in ('dataset_builder', 'dotenv') or m in ('app', 'app.core', 'app.core.config'));"
            "print(bad); sys.exit(1 if bad else 0)")
    done = subprocess.run([sys.executable, "-I", "-c", code, str(Path(__file__).resolve().parent)], capture_output=True, text=True, cwd=str(Path(__file__).resolve().parent), timeout=120)
    assert done.returncode == 0, done.stdout + done.stderr


def _nullable_frame_with_bug(env, null_fc_records=(), null_occ_records=()):
    frame = env.frame
    frame["max_occupancy"] = frame["max_occupancy"].astype("Int64")
    frame["free_cancellation"] = frame["free_cancellation"].astype("boolean")
    for record in null_occ_records:
        frame.loc[frame["warehouse_record_id"] == record, "max_occupancy"] = pd.NA
    for record in null_fc_records:
        frame.loc[frame["warehouse_record_id"] == record, "free_cancellation"] = True              # builder bug: DB NULL -> Parquet True
    env.frame = frame


def test_run_q4_with_real_nullable_dtypes_reports_only_the_real_free_cancellation_defect_and_exports_a_diagnosis(env):
    env.world.q4_override = {12: {"a_max_occupancy": None, "po_max_occupancy": None}, 13: {"a_free_cancellation": None, "po_free_cancellation": None}}
    _nullable_frame_with_bug(env, null_fc_records=(13,), null_occ_records=(12,))
    code, report = env.run("--run", "q4")
    q4 = report["families"]["ds_20261006_dev1b"]["q4"]
    assert q4["fidelity_violation_counts"] == {"ok": 3, "free_cancellation": 1}                                       # max_occupancy NULL<->NA KHONG con la dương tinh gia
    assert q4["status"] == "FAIL" and code == 1
    hyp = q4["diagnosis"]["hypotheses"]
    assert hyp["free_cancellation"]["null_to_true"] == "SUPPORTED" and hyp["free_cancellation"]["explains_all_mismatches"] is True and hyp["max_occupancy"]["controller_false_positive"] == "CONFIRMED"
    assert hyp["matches_prereg_expectations"] is False                                                                # 1 != 25, prefix khac pin
    exported = json.loads(Path(q4["diagnosis"]["path"]).read_text(encoding="utf-8"))
    assert exported["columns"]["free_cancellation"]["mismatch_ids"] == [13] and exported["columns"]["free_cancellation"]["db_null_ids"] == [13]
    assert exported["columns"]["max_occupancy"]["db_null_ids"] == exported["columns"]["max_occupancy"]["parquet_null_ids"] == [12] and hashlib_sha(Path(q4["diagnosis"]["path"]).read_text(encoding="utf-8")) == q4["diagnosis"]["sha256"]


def test_run_q4_passes_when_db_null_and_parquet_na_agree_for_all_columns(env):
    env.world.q4_override = {12: {"a_max_occupancy": None, "a_free_cancellation": None, "a_breakfast_included": None}}
    frame = env.frame
    frame["max_occupancy"] = frame["max_occupancy"].astype("Int64")
    frame["free_cancellation"] = frame["free_cancellation"].astype("boolean")
    frame["breakfast_included"] = frame["breakfast_included"].astype("boolean")
    frame.loc[frame["warehouse_record_id"] == 12, ["max_occupancy", "free_cancellation", "breakfast_included"]] = pd.NA
    code, report = env.run("--run", "q4")
    q4 = report["families"]["ds_20261006_dev1b"]["q4"]
    assert q4["fidelity_violation_counts"] == {"ok": 4} and q4["status"] == "PASS"
    fc = q4["diagnosis"]["hypotheses"]["free_cancellation"]
    assert fc["db_null"] == 1 and fc["null_to_true"] == "REJECTED" and fc["other_cells"] == ["NULL|NULL"] and fc["mismatching_rows"] == 0          # NULL -> NULL trung thuc, khong phai NULL -> True


# =========================================================================== kiem tung dieu kien rieng le (truoc day mot so duoc "bat" chi vi fixture bi ERROR, khong phai assertion)
def test_count_checks_each_condition_independently(env):
    env.frame = env.frame.iloc[:3].reset_index(drop=True)                                                       # DB = 4 = EXPECTED nhung Parquet 3 dong
    code, report = env.run("--run", "count")
    count = report["families"]["ds_20261006_dev1b"]["count"]
    assert count["status"] == "FAIL" and count["checks"] == {"db_equals_parquet": False, "equals_expected_selected": True}
    env.world.count_selected = 3                                                                                # DB = Parquet = 3 nhung EXPECTED = 4
    code, report = env.run("--run", "count")
    count = report["families"]["ds_20261006_dev1b"]["count"]
    assert count["status"] == "FAIL" and count["checks"] == {"db_equals_parquet": True, "equals_expected_selected": False}


def test_q1_control_checks_matches_burn_in_and_orphans_each_on_a_full_run(env):
    env.world.matches[1]["before_approval"] = 1                                                                 # burn-in 2 thay vi 1 (EXPECTED_BURN_IN = 1)
    code, report = env.run("--run", "q1_control")
    checks = report["families"]["ds_20261006_dev1b"]["q1_control"]["checks"]
    assert checks["burn_in_equals_expected"] is False and checks["matches_equal_expected"] is True and report["families"]["ds_20261006_dev1b"]["q1_control"]["status"] == "FAIL"
    env.world.matches[1]["before_approval"] = 0
    env.world.matches.pop()                                                                                     # 3 matches thay vi 4
    code, report = env.run("--run", "q1_control")
    checks = report["families"]["ds_20261006_dev1b"]["q1_control"]["checks"]
    assert checks["matches_equal_expected"] is False and checks["burn_in_equals_expected"] is True
    env.world.matches.append({"item": 104, "price_ok": 1, "before_approval": 0, "missing_assignment": 1, "missing_observation": 0})
    code, report = env.run("--run", "q1_control")
    assert report["families"]["ds_20261006_dev1b"]["q1_control"]["checks"]["no_orphans"] is False


def test_run_always_requires_the_count_token_in_the_approval_and_adds_the_count_family(env):
    env.write_approval(modes="run:q1")                                                                          # co q1 nhung KHONG co run:count
    code, report = env.run("--run", "q1")
    assert code == 3 and any("run:count" in r for r in report["verdict"]["reasons"]) and env.connect_calls == 0
    env.write_approval()
    code, report = env.run("--run", "q1")
    assert set(report["families"]["ds_20261006_dev1b"]) == {"count", "q1"} and report["requested"] == ["count", "q1"]


# =========================================================================== GPT file 44: D1 (eligibility/integrity cua chan doan), D2 (khong cat phan le), ledger id theo tung o
import decimal  # noqa: E402


def _prefix_diag(n=3000, fc_null_true=25, occ_na=28):
    """Prefix gia lap CO KICH THUOC THAT: `fc_null_true` hang DB NULL -> Parquet True, `occ_na` hang max_occupancy NULL <-> NA, cac hang con lai dung."""
    diag = wa.Q4Diagnosis()
    ids = list(range(1, n + 1))
    for i in ids:
        row, parquet = _q4_row(), _pq_row()
        if i <= fc_null_true:
            row["a_free_cancellation"], parquet["free_cancellation"] = None, True
        elif i <= fc_null_true + occ_na:
            row["a_max_occupancy"], parquet["max_occupancy"] = None, pd.NA
        diag.add(i, row, parquet)
    return diag, ids


@pytest.fixture
def pinned(monkeypatch):
    ids = list(range(1, 3001))
    sha = hashlib_sha(json.dumps(ids))
    monkeypatch.setattr(wa, "PREFIX41_IDS_SHA256", sha)
    return sha, set(ids)


def test_full_size_prefix_with_clean_ledger_matches_the_prereg_expectations(pinned):
    sha, expected = pinned
    diag, ids = _prefix_diag()
    hyp = wa.evaluate_null_hypotheses(diag, sha, expected)
    assert hyp["integrity"]["eligible"] is True and hyp["integrity"]["rows_seen"] == hyp["integrity"]["classified"] == 3000
    assert hyp["free_cancellation"]["null_to_true"] == "SUPPORTED" and hyp["max_occupancy"]["controller_false_positive"] == "CONFIRMED" and hyp["matches_prereg_expectations"] is True


def test_d1_one_invalid_row_blocks_every_positive_verdict_and_prereg(pinned):
    sha, expected = pinned
    diag, ids = _prefix_diag(n=2999)
    diag.add(3000, _q4_row(a_free_cancellation=2), _pq_row())                                                  # DUY NHAT mot hang co raw DB = 2 (mien la)
    hyp = wa.evaluate_null_hypotheses(diag, sha, expected)
    assert hyp["integrity"]["eligible"] is False and hyp["integrity"]["domain_errors"] == 1 and hyp["integrity"]["classified"] == 2999 and hyp["integrity"]["rows_seen"] == 3000
    assert hyp["free_cancellation"]["null_to_true"] == "UNVERIFIED" and hyp["breakfast_included"]["null_to_true"] == "UNVERIFIED"
    assert hyp["max_occupancy"]["controller_false_positive"] == "UNVERIFIED" and hyp["matches_prereg_expectations"] is False


def test_d1_orphan_missing_extra_and_duplicate_ids_also_block_positive_verdicts(pinned):
    sha, expected = pinned
    orphan, _ = _prefix_diag(n=2999)
    orphan.skip(3000, "orphan_assignment_or_hotel")
    hyp = wa.evaluate_null_hypotheses(orphan, sha, expected)
    assert hyp["integrity"]["skipped"] == 1 and hyp["integrity"]["eligible"] is False and hyp["matches_prereg_expectations"] is False and hyp["free_cancellation"]["null_to_true"] == "UNVERIFIED"
    missing, _ = _prefix_diag(n=2999)                                                                         # thieu id 3000 so voi tap ky vong
    hyp = wa.evaluate_null_hypotheses(missing, sha, expected)
    assert hyp["integrity"]["missing_ids"] == 1 and hyp["integrity"]["eligible"] is False and hyp["matches_prereg_expectations"] is False
    extra, _ = _prefix_diag()
    extra.add(9999, _q4_row(), _pq_row())
    hyp = wa.evaluate_null_hypotheses(extra, sha, expected)
    assert hyp["integrity"]["extra_ids"] == 1 and hyp["integrity"]["eligible"] is False
    duplicate, _ = _prefix_diag()
    duplicate.add(5, _q4_row(), _pq_row())
    hyp = wa.evaluate_null_hypotheses(duplicate, sha, expected)
    assert hyp["integrity"]["duplicates"] == 1 and hyp["integrity"]["eligible"] is False and hyp["matches_prereg_expectations"] is False


def test_d2_fractional_occupancy_is_a_domain_error_never_truncated_to_an_integer():
    with pytest.raises(ValueError):
        wa.occupancy_state(2.5)
    assert wa.q4_fidelity_violations(_q4_row(a_max_occupancy=2.5), _pq_row(max_occupancy=2), AREA_PARSER) == ["domain_error:max_occupancy"]       # truoc day: int(2.5)=2 che mat bat dong
    assert wa.q4_fidelity_violations(_q4_row(a_max_occupancy=2), _pq_row(max_occupancy=2.5), AREA_PARSER) == ["domain_error:max_occupancy"]
    diag = wa.Q4Diagnosis()
    diag.add(1, _q4_row(a_max_occupancy=None), _pq_row(max_occupancy=pd.NA))
    diag.add(2, _q4_row(a_max_occupancy=2.5), _pq_row(max_occupancy=2))
    hyp = wa.evaluate_null_hypotheses(diag)
    assert hyp["max_occupancy"]["controller_false_positive"] == "UNVERIFIED" and hyp["integrity"]["domain_errors"] == 1                                # khong CONFIRMED


@pytest.mark.parametrize("value,expected", [(2, ("VAL", 2)), (2.0, ("VAL", 2)), (decimal.Decimal("2"), ("VAL", 2)), (np.int64(3), ("VAL", 3)), (np.float64(4.0), ("VAL", 4)), (None, ("NULL", None)), (pd.NA, ("NULL", None))])
def test_occupancy_normalisation_accepts_only_integral_values_documented_two_equals_two_point_zero(value, expected):
    assert wa.occupancy_state(value) == expected


@pytest.mark.parametrize("value", [2.5, 0.1, decimal.Decimal("2.5"), True, "2", b"2", float("inf")])
def test_occupancy_rejects_fractions_booleans_strings_and_unexpected_domains(value):
    with pytest.raises((ValueError, TypeError)):
        wa.occupancy_state(value)


def test_non_null_occupancy_disagreement_is_kept_with_raw_values_and_matches_the_fidelity_verdict():
    diag = wa.Q4Diagnosis()
    diag.add(1, _q4_row(a_max_occupancy=2), _pq_row(max_occupancy=3))
    diag.add(2, _q4_row(a_max_occupancy=None), _pq_row(max_occupancy=pd.NA))
    assert wa.q4_fidelity_violations(_q4_row(a_max_occupancy=2), _pq_row(max_occupancy=3), AREA_PARSER) == ["max_occupancy"]
    assert diag.cols["max_occupancy"]["value_pairs"] == [{"record_id": 1, "db": 2, "parquet": 3}]
    assert wa.evaluate_null_hypotheses(diag)["max_occupancy"]["controller_false_positive"] == "REJECTED"


def test_ledger_lists_record_ids_per_cell_for_every_column():
    diag = wa.Q4Diagnosis()
    diag.add(10, _q4_row(a_free_cancellation=None), _pq_row(free_cancellation=True))
    diag.add(11, _q4_row(a_free_cancellation=0), _pq_row(free_cancellation=False))
    diag.add(12, _q4_row(a_free_cancellation=1), _pq_row(free_cancellation=True))
    diag.add(13, _q4_row(a_free_cancellation=None), _pq_row(free_cancellation=True))
    cells = diag.cols["free_cancellation"]["cells"]
    assert cells == {"NULL|1": [10, 13], "0|0": [11], "1|1": [12]} and diag.cols["free_cancellation"]["crosstab"] == {"NULL|1": 2, "0|0": 1, "1|1": 1}
    assert diag.cols["breakfast_included"]["cells"] == {"1|1": [10, 11, 12, 13]} and diag.cols["max_occupancy"]["cells"] == {"VAL|VAL": [10, 11, 12, 13]}
    assert diag.ids == diag.classified_ids == [10, 11, 12, 13]


def test_family_q4_d1_invalid_row_and_orphan_leave_only_unverified_diagnosis_but_fidelity_still_fails(env):
    env.world.q4_override = {12: {"a_max_occupancy": None, "po_max_occupancy": None}, 13: {"a_free_cancellation": 2}, 15: {"a_id": None}}
    frame = env.frame
    frame["max_occupancy"] = frame["max_occupancy"].astype("Int64")
    frame.loc[frame["warehouse_record_id"] == 12, "max_occupancy"] = pd.NA
    code, report = env.run("--run", "q4")
    q4 = report["families"]["ds_20261006_dev1b"]["q4"]
    assert q4["status"] == "FAIL" and q4["fidelity_violation_counts"] == {"ok": 2, "domain_error:free_cancellation": 1, "missing_assignment": 1}
    hyp = q4["diagnosis"]["hypotheses"]
    assert hyp["integrity"]["domain_errors"] == 1 and hyp["integrity"]["skipped"] == 1 and hyp["integrity"]["eligible"] is False
    assert hyp["free_cancellation"]["null_to_true"] == "UNVERIFIED" and hyp["max_occupancy"]["controller_false_positive"] == "UNVERIFIED" and hyp["matches_prereg_expectations"] is False
    exported = json.loads(Path(q4["diagnosis"]["path"]).read_text(encoding="utf-8"))
    assert [e["record_id"] for e in exported["domain_errors"]] == [13] and [e["record_id"] for e in exported["skipped"]] == [15]


def test_family_q4_d2_fractional_occupancy_is_reported_as_domain_error_not_confirmed(env):
    env.world.q4_override = {12: {"a_max_occupancy": None}, 13: {"a_max_occupancy": 2.5}}
    frame = env.frame
    frame["max_occupancy"] = frame["max_occupancy"].astype("Int64")
    frame.loc[frame["warehouse_record_id"] == 12, "max_occupancy"] = pd.NA
    code, report = env.run("--run", "q4")
    q4 = report["families"]["ds_20261006_dev1b"]["q4"]
    assert q4["status"] == "FAIL" and q4["fidelity_violation_counts"] == {"ok": 3, "domain_error:max_occupancy": 1}
    hyp = q4["diagnosis"]["hypotheses"]
    assert hyp["max_occupancy"]["controller_false_positive"] == "UNVERIFIED" and hyp["integrity"]["eligible"] is False


def test_family_q4_row_missing_from_parquet_is_recorded_as_skipped_and_blocks_positive_verdicts(env, monkeypatch):
    env.frame = env.frame[env.frame["warehouse_record_id"] != 15].reset_index(drop=True)                        # DB co id 15 nhung Parquet khong co
    env.world.count_selected = 3                                                                                # COUNT khop Parquet va hang so ky vong (3) de COUNT khong chan Q4
    monkeypatch.setattr(wa, "EXPECTED_SELECTED", 3)
    code, report = env.run("--run", "q4")
    q4 = report["families"]["ds_20261006_dev1b"]["q4"]
    hyp = q4["diagnosis"]["hypotheses"]
    exported = json.loads(Path(q4["diagnosis"]["path"]).read_text(encoding="utf-8"))
    assert [e["reason"] for e in exported["skipped"]] == ["source_missing_in_parquet"] and hyp["integrity"]["skipped"] == 1 and hyp["integrity"]["eligible"] is False
    assert hyp["free_cancellation"]["null_to_true"] == "UNVERIFIED"


def test_family_q4_expected_prefix_ids_are_enforced_when_the_db_returns_fewer_rows(env):
    env.world.drop_q4 = {13}                                                                                    # Parquet co 13, DB khong tra ve
    code, report = env.run("--run", "q4")
    q4 = report["families"]["ds_20261006_dev1b"]["q4"]
    hyp = q4["diagnosis"]["hypotheses"]
    assert q4["coverage"]["missing_in_db"] == 1 and q4["status"] == "FAIL"
    assert hyp["integrity"]["missing_ids"] == 1 and hyp["integrity"]["eligible"] is False and hyp["max_occupancy"]["controller_false_positive"] == "UNVERIFIED"

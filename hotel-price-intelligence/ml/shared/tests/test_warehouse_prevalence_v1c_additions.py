"""v1c = producer 9bde4955 + `driver_types` + `groups` trong ket qua (GPT file 54 muc 3: raw groups va kieu counter cua driver). Kiem tra: (1) hai truong moi dung, (2) thay doi CHI CONG THEM — so voi producer dong bang
(differential), SQL/SQL set/summarize/evaluator khong doi. Test sinh tu test dong bang (tep `test_warehouse_prevalence_v1c.py`, chi doi ten tep producer) cung chay cho v1c."""
from __future__ import annotations

import decimal
import importlib.util
import json
from pathlib import Path

import pytest

from test_warehouse_prevalence_v1c import Env, _good_groups, _row, env, wa, wp  # noqa: F401  (env la fixture dung chung)

TOOLS = Path(__file__).resolve().parents[3] / "audit_tools"
_spec = importlib.util.spec_from_file_location("warehouse_prevalence_v1_frozen", TOOLS / "warehouse_prevalence_v1.py")
frozen = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(frozen)
D = decimal.Decimal
NEW_KEYS = ("driver_types", "groups", "rows_received", "groups_complete", "ledger_note")      # CHI cac khoa nay duoc them so voi producer dong bang


def test_the_frozen_producer_is_still_the_approved_bytes_and_v1c_is_a_different_file():
    assert frozen.script_sha256() == "9bde4955ba9b1c882365a29219d1fb2f3c4ae201a0f6930c67a26069cddb41a5"
    assert wp.script_sha256() != frozen.script_sha256() and wp.SCRIPT_PATH.name == "warehouse_prevalence_v1c.py"


def test_sql_sql_set_and_base_pin_are_identical_to_the_frozen_producer():
    assert wp.SQL_PREVALENCE == frozen.SQL_PREVALENCE and wp.sql_set_sha256() == frozen.sql_set_sha256() == "928eb80b53f5986f562c8e7007c0ef82f2e894d8f40659b3ec20aafca9781211"
    assert wp.PINNED_BASE_CONTROLLER_SHA256 == frozen.PINNED_BASE_CONTROLLER_SHA256 and wp.Q4_PREFIX_LOWER_BOUNDS == frozen.Q4_PREFIX_LOWER_BOUNDS
    assert (wp.NUMERIC, wp.GROUP_CAP, wp.MAX_COUNTER) == (frozen.NUMERIC, frozen.GROUP_CAP, frozen.MAX_COUNTER)


INPUTS = {
    "good_int": ([_row()], 4), "good_decimal": ([{k: (D(v) if isinstance(v, int) else v) for k, v in _row().items()}], 4), "wrong_denominator": ([_row()], 5),
    "orphans": ([_row(orphan_assignment=1, fc_one=1, bf_one=2, occ_known=2)], 4), "invalid_flag": ([_row(fc_invalid=1, fc_one=1)], 4), "fraction": ([_row(fc_null=D("1.9"))], 4),
    "bool": ([_row(fc_one=True)], 4), "none": ([_row(fc_null=None)], 4), "missing_col": ([{k: v for k, v in _row().items() if k != "fc_one"}], 4), "empty": ([], 0), "empty_bad": ([], 3),
    "two_groups": ([_row(), _row(split="test", vn_date="2026-09-02")], 8), "compensating": ([_row(fc_one=3), _row(split="test", selected_rows=2, fc_null=0, fc_zero=0, fc_one=1, bf_null=0, bf_zero=1, bf_one=1,
                                                                                                         occ_null=0, occ_known=2)], 6),
}


@pytest.mark.parametrize("name", list(INPUTS))
@pytest.mark.parametrize("bounds", [None, {"fc_null": 1, "occ_null": 1}, {"fc_null": 25, "occ_null": 28}])
def test_the_amendment_is_purely_additive_evaluator_output_equals_frozen_after_dropping_the_two_new_keys(name, bounds):
    rows, expected = INPUTS[name]
    new = wp.evaluate_prevalence(rows, expected, lower_bounds=bounds)
    old = frozen.evaluate_prevalence(rows, expected, lower_bounds=bounds)
    assert {k: v for k, v in new.items() if k not in NEW_KEYS} == old
    assert "driver_types" in new


@pytest.mark.parametrize("truncated", [True, False])
def test_truncated_path_is_unchanged_apart_from_driver_types(truncated):
    new, old = wp.evaluate_prevalence([_row()], 4, truncated=truncated), frozen.evaluate_prevalence([_row()], 4, truncated=truncated)
    assert {k: v for k, v in new.items() if k not in NEW_KEYS} == old


def test_summarize_is_identical_to_the_frozen_producer_on_all_report_shapes():
    ok0 = {"all_ok": True}
    both = {name: {"status": "MEASURED"} for name in wa.DATASETS}
    shapes = [{"refusals": [], "step0": ok0, "datasets": both, "mode": "run"}, {"refusals": ["x"], "step0": ok0, "datasets": both, "mode": "run"}, {"refusals": [], "step0": {"all_ok": False}, "datasets": both, "mode": "run"},
              {"refusals": [], "step0": ok0, "datasets": {**both, "ds_20261006_dev3b": {"status": "FAIL"}}, "mode": "run"}, {"refusals": [], "step0": ok0, "datasets": {"ds_20261006_dev1b": {"status": "MEASURED"}}, "mode": "run"},
              {"refusals": [], "step0": ok0, "datasets": {name: {"status": "PLAN_OK"} for name in wa.DATASETS}, "mode": "explain"}]
    assert [wp.summarize(s) for s in shapes] == [frozen.summarize(s) for s in shapes]


def test_driver_types_record_the_raw_python_type_name_per_column_including_mixed_int_and_decimal():
    rows = [{**_row(), "selected_rows": 4, "fc_null": D("1"), "fc_zero": D("1"), "fc_one": D("2")}, {**_row(split="test"), "selected_rows": 4}]
    types = wp.evaluate_prevalence(rows, 8)["driver_types"]
    assert types["selected_rows"] == {"int": 2} and types["fc_null"] == {"Decimal": 1, "int": 1} and types["fc_one"] == {"Decimal": 1, "int": 1}
    assert set(types) == set(wp.NUMERIC) and all(sum(c.values()) == 2 for c in types.values())


def test_driver_types_are_reported_even_when_the_rows_are_rejected_and_show_the_offending_type():
    result = wp.evaluate_prevalence([_row(fc_one=True), _row(fc_null=D("1.9"))], 8)
    assert result["status"] == "FAIL" and "groups" not in result
    assert result["driver_types"]["fc_one"] == {"bool": 1, "int": 1} and result["driver_types"]["fc_null"] == {"int": 1, "Decimal": 1}


def test_driver_types_handle_missing_columns_and_non_mapping_rows_without_raising():
    result = wp.evaluate_prevalence([{k: v for k, v in _row().items() if k != "fc_one"}, None, 7], 4)
    assert result["status"] == "FAIL" and result["driver_types"]["fc_one"] == {"missing": 3} and result["driver_types"]["selected_rows"] == {"int": 1, "missing": 2}


@pytest.mark.parametrize("rows", [None, 7, "abc", {"a": 1}, object()])
def test_driver_types_never_raise_on_a_non_sequence_rows_argument_and_the_evaluator_returns_fail_like_the_frozen_one(rows):
    new, old = wp.evaluate_prevalence(rows, 4), frozen.evaluate_prevalence(rows, 4)
    assert new["status"] == old["status"] == "FAIL" and new["driver_types"] == {key: {} for key in wp.NUMERIC}
    assert {k: v for k, v in new.items() if k not in NEW_KEYS} == old and new["rows_received"] == 0 and new["groups_complete"] is False


def test_truncated_result_still_carries_driver_types_for_diagnosis():
    result = wp.evaluate_prevalence([_row()], 4, truncated=True)
    assert result["status"] == "UNVERIFIED" and result["driver_types"]["selected_rows"] == {"int": 1} and "groups" not in result


def test_groups_are_normalized_ints_in_input_order_with_null_split_and_reproduce_every_total():
    rows = [{k: (D(v) if isinstance(v, int) and k != "selected_rows" else v) for k, v in _row(vn_date=f"2026-09-0{i}", split=s).items()} for i, s in ((1, "train"), (2, None), (3, "test"))]
    result = wp.evaluate_prevalence(rows, 12)
    groups = result["groups"]
    assert result["status"] == "MEASURED" and [(g["vn_date"], g["split"]) for g in groups] == [("2026-09-01", "train"), ("2026-09-02", None), ("2026-09-03", "test")]
    assert all(type(g[k]) is int for g in groups for k in wp.NUMERIC) and len(groups) == result["summary"]["groups"] == 3
    assert {k: sum(g[k] for g in groups) for k in wp.NUMERIC} == result["summary"]["totals"]
    json.dumps(result)                                                                                       # serializable (khong con Decimal)


def test_main_report_contains_groups_and_driver_types_for_each_dataset_and_survives_json_round_trip(env):
    env.world.groups = [{k: (D(v) if isinstance(v, int) and k != "selected_rows" else v) for k, v in g.items()} for g in _good_groups()]
    code, report = env.run("--run")
    assert code == 0 and report["verdict"]["overall"] == "MEASURED"
    for name in wa.DATASETS:
        data = report["datasets"][name]
        assert len(data["groups"]) == 2 and data["driver_types"]["selected_rows"] == {"int": 2} and data["driver_types"]["fc_null"] == {"Decimal": 2}
        assert {k: sum(g[k] for g in data["groups"]) for k in wp.NUMERIC} == data["summary"]["totals"]
        assert data["groups"][0]["vn_date"] == "2026-09-01" and data["groups"][1]["split"] == "test"


def test_a_fail_through_main_still_reports_driver_types_and_no_groups(env):
    env.world.groups = _good_groups()
    env.world.groups[0]["fc_null"] = D("1.9")
    code, report = env.run("--run")
    data = report["datasets"]["ds_20261006_dev1b"]
    assert code == 1 and data["status"] == "FAIL" and "groups" not in data and data["driver_types"]["fc_null"] == {"Decimal": 1, "int": 1}


# =========================================================================== hop dong dau ra cua GPT file 56: so cai phai tai cong doc lap + tinh day du
def _ledger_world(seed_dates=30):
    """Nhieu ngay x nhieu split (ke ca purge NULL) voi bucket nhat quan; tra (rows kieu driver: COUNT int + SUM Decimal, expected_total)."""
    import datetime as dt
    rows, total = [], 0
    for day in range(seed_dates):
        for index, split in enumerate(("train", "validation", "test", None)):
            if (day + index) % 5 == 4:
                continue                                                                                     # khong phai moi ngay co du moi split
            n = 20 + 3 * day + 7 * index
            fc_null, fc_zero = (day + index) % 4, (day * 3 + index) % 9
            bf_null, bf_zero = (day * 2) % 3, (index + day) % 7
            occ_null = (day + 2 * index) % 5
            rows.append({"vn_date": dt.date(2026, 8, 1) + dt.timedelta(days=day), "split": split, "selected_rows": n, "orphan_assignment": D(0),
                         "fc_null": D(fc_null), "fc_zero": D(fc_zero), "fc_one": D(n - fc_null - fc_zero), "fc_invalid": D(0),
                         "bf_null": D(bf_null), "bf_zero": D(bf_zero), "bf_one": D(n - bf_null - bf_zero), "bf_invalid": D(0), "occ_null": D(occ_null), "occ_known": D(n - occ_null)})
            total += n
    return rows, total


def test_end_to_end_ledger_is_complete_and_totals_by_split_null_rates_and_null_dates_recompute_independently_from_the_saved_report(env, monkeypatch):
    rows, total = _ledger_world()
    monkeypatch.setattr(wa, "EXPECTED_SELECTED", total)
    monkeypatch.setattr(wp, "Q4_PREFIX_LOWER_BOUNDS", {"fc_null": 1, "occ_null": 1})
    env.world.groups = rows
    code, report = env.run("--run")
    assert code == 0 and report["verdict"]["overall"] == "MEASURED"
    for name in wa.DATASETS:
        data = report["datasets"][name]
        ledger = data["groups"]
        assert data["groups_complete"] is True and data["rows_received"] == len(rows) == len(ledger) == data["summary"]["groups"]
        assert [(g["vn_date"], g["split"]) for g in ledger] == [(r["vn_date"].isoformat(), r["split"]) for r in rows]           # moi nhom, dung thu tu, split=None giu nguyen (purge)
        assert any(g["split"] is None for g in ledger) and len({g["vn_date"] for g in ledger}) == 30
        # ---- tai tinh DOC LAP tu so cai da luu (khong dung code producer) ----
        totals = {k: sum(g[k] for g in ledger) for k in wp.NUMERIC}
        assert totals == data["summary"]["totals"] and totals["selected_rows"] == total
        by_split = {}
        for g in ledger:
            slot = by_split.setdefault(str(g["split"]), dict.fromkeys(wp.NUMERIC, 0))
            for k in wp.NUMERIC:
                slot[k] += g[k]
        assert by_split == data["summary"]["by_split"] and "None" in by_split
        known = totals["selected_rows"] - totals["orphan_assignment"]
        for col in ("fc_null", "bf_null", "occ_null"):
            assert data["summary"]["null_fraction_of_non_orphan"][col] == totals[col] / known
            dates = sorted({g["vn_date"] for g in ledger if g[col] > 0})
            assert data["summary"]["dates_with_nulls"][col] == {"count": len(dates), "first": dates[0], "last": dates[-1]}
        for g in ledger:                                                                                      # moi nhom: bucket cong dung mau so
            assert g["selected_rows"] == g["orphan_assignment"] + g["fc_null"] + g["fc_zero"] + g["fc_one"] + g["fc_invalid"] == g["orphan_assignment"] + g["bf_null"] + g["bf_zero"] + g["bf_one"] + g["bf_invalid"]
            assert g["selected_rows"] == g["orphan_assignment"] + g["occ_null"] + g["occ_known"] and all(type(g[k]) is int for k in wp.NUMERIC)
        # ---- driver_types: kieu goc (COUNT int, SUM Decimal), moi histogram cong dung so hang ----
        types = data["driver_types"]
        assert types["selected_rows"] == {"int": len(rows)} and all(types[k] == {"Decimal": len(rows)} for k in wp.NUMERIC if k != "selected_rows")
        assert all(sum(types[k].values()) == data["rows_received"] for k in wp.NUMERIC)


def test_ledger_and_types_survive_a_json_round_trip_exactly(env):
    code, report = env.run("--run")
    data = report["datasets"]["ds_20261006_dev1b"]
    assert json.loads(json.dumps(data)) == data and data["groups"] and data["driver_types"]["selected_rows"] == {"int": 2}
    assert "JSON number" in data["ledger_note"]                                                               # nhac: JSON number khong chung minh kieu driver


def test_truncated_result_has_no_ledger_is_marked_incomplete_and_records_rows_received_and_full_histograms(env):
    template = _good_groups()[0]
    import datetime as dt
    env.world.groups = [dict(template, vn_date=dt.date(2000, 1, 1) + dt.timedelta(days=i)) for i in range(wp.GROUP_CAP + 1)]
    code, report = env.run("--run")
    data = report["datasets"]["ds_20261006_dev1b"]
    assert code == 4 and data["status"] == "UNVERIFIED" and "groups" not in data and data["groups_complete"] is False
    assert data["rows_received"] == wp.GROUP_CAP + 1 and all(sum(data["driver_types"][k].values()) == wp.GROUP_CAP + 1 for k in wp.NUMERIC)     # histogram phu CA hang sentinel


def test_invalid_rows_through_main_report_missing_counter_counts_and_are_never_complete(env):
    env.world.groups = _good_groups()
    del env.world.groups[1]["fc_one"]
    code, report = env.run("--run")
    data = report["datasets"]["ds_20261006_dev1b"]
    assert code == 1 and data["status"] == "FAIL" and data["groups_complete"] is False and "groups" not in data
    assert data["driver_types"]["fc_one"] == {"int": 1, "missing": 1} and data["rows_received"] == 2


def test_an_approval_for_the_frozen_producer_does_not_authorize_the_candidate(env):
    path = env.write_approval(controller=frozen.script_sha256(), name="49-gpt-old-producer.md", modes="explain, run:prevalence")
    code, report = env.run("--run", approval=path.name)
    assert code == 3 and env.connect_calls == 0 and report["verdict"]["overall"] == "REFUSED"


def test_the_candidate_keeps_seven_statements_and_two_aggregates_with_the_ledger_enabled(env):
    code, report = env.run("--run")
    assert code == 0 and report["statements_total"] == 7 and report["queries_executed"] == 2 and len(env.conn.selects()) == 2 and len(env.conn.explains()) == 2

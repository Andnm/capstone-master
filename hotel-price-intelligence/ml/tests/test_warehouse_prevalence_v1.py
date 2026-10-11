"""Prevalence NULL doc-chi (audit_tools/warehouse_prevalence_v1.py): verdict thuan + SQL that chay tren SQLite (oracle ngu nghia) + wrapper voi DB gia. KHONG co ket noi MySQL that."""
from __future__ import annotations

import contextlib
import datetime as dt
import decimal
import importlib.util
import io
import json
import re
import sqlite3
from pathlib import Path

import pytest

TOOLS = Path(__file__).resolve().parents[2] / "audit_tools"


def _load(name, filename):
    spec = importlib.util.spec_from_file_location(name, TOOLS / filename)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


wp = _load("warehouse_prevalence_v1", "warehouse_prevalence_v1.py")
wa = wp.wa                                         # CHINH module ma script prevalence dung (cong gio/crawler/Ctx/guarded)
ev = _load("crawl_idle_evidence", "crawl_idle_evidence.py")
UTC = dt.timezone.utc
D = dt.date


def vn(hour, minute=0):
    return dt.datetime(2026, 10, 8, hour, minute, tzinfo=wa.VN).astimezone(UTC)


# =========================================================================== SQL: hinh dang + oracle ngu nghia tren SQLite
def test_the_prevalence_sql_is_one_bounded_read_only_aggregate_that_keeps_orphans():
    sql = wp.SQL_PREVALENCE
    assert sql.startswith("SELECT ") and ";" not in sql and sql.count("%s") == 1
    assert not re.search(r"\b(INSERT|UPDATE|DELETE|DROP|ALTER|CREATE|TRUNCATE|REPLACE|INTO|CALL|LOCK)\b", sql, re.IGNORECASE)
    assert "LEFT JOIN ml_reference_assignments a" in sql and "INNER JOIN" not in sql.upper() and " JOIN " not in sql.replace("LEFT JOIN", "")
    assert "a.dataset_version = s.dataset_version" in sql and "s.dataset_version = %s" in sql and "s.is_daily_snapshot_selected = 1" in sql
    assert f"LIMIT {wp.GROUP_CAP + 1}" in sql and "GROUP BY s.vn_observation_date, s.split" in sql
    for alias in wp.NUMERIC:
        assert f" AS {alias}" in sql, alias


def test_every_counter_is_a_case_sum_never_a_sum_over_a_bare_boolean_expression():
    sums = re.findall(r"SUM\((.*?)\) AS (\w+)", wp.SQL_PREVALENCE)
    assert {alias for _, alias in sums} == set(wp.NUMERIC) - {"selected_rows"} and all(expr.startswith("CASE WHEN ") and expr.endswith(" THEN 1 ELSE 0 END") for expr, _ in sums)
    assert "COUNT(*) AS selected_rows" in wp.SQL_PREVALENCE


def test_a_group_whose_flags_are_all_null_yields_zero_counts_not_null_sums():
    rows = _sqlite_run([(5, "2026-09-03", "test", 1, "ds")], [(5, "ds", None, None, None)])             # truoc khi sua: SUM(NULL AND ...) => NULL cho *_zero/*_one
    assert len(rows) == 1 and all(rows[0][k] is not None for k in wp.NUMERIC)
    assert (rows[0]["fc_null"], rows[0]["fc_zero"], rows[0]["fc_one"], rows[0]["fc_invalid"], rows[0]["occ_null"], rows[0]["occ_known"]) == (1, 0, 0, 0, 1, 0)


def test_the_old_boolean_sum_form_returned_null_on_that_group_so_this_test_is_sensitive():
    old = ("SELECT SUM(a.id IS NOT NULL AND a.free_cancellation = 0) AS fc_zero FROM ml_samples s LEFT JOIN ml_reference_assignments a ON a.id = s.ml_reference_assignment_id "
           "AND a.dataset_version = s.dataset_version WHERE s.dataset_version = ?")
    conn = sqlite3.connect(":memory:")
    conn.execute(SAMPLES_DDL)
    conn.execute(ASSIGN_DDL)
    conn.execute("INSERT INTO ml_samples (ml_reference_assignment_id, vn_observation_date, split, is_daily_snapshot_selected, dataset_version) VALUES (5,'2026-09-03','test',1,'ds')")
    conn.execute("INSERT INTO ml_reference_assignments VALUES (5,'ds',NULL,NULL,NULL)")
    assert conn.execute(old, ("ds",)).fetchone()[0] is None


SAMPLES_DDL = "CREATE TABLE ml_samples (id INTEGER PRIMARY KEY, dataset_version TEXT, ml_reference_assignment_id INTEGER, vn_observation_date TEXT, split TEXT, is_daily_snapshot_selected INTEGER)"
ASSIGN_DDL = "CREATE TABLE ml_reference_assignments (id INTEGER, dataset_version TEXT, free_cancellation INTEGER, breakfast_included INTEGER, max_occupancy INTEGER)"


def _sqlite_run(samples, assignments, dataset="ds"):
    """Chay CHINH `SQL_PREVALENCE` (doi %s -> ?) tren SQLite voi bang gia lap; samples = (assignment_id, date, split, selected, dataset), assignments = (id, dataset, fc, bf, occ)."""
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.execute(SAMPLES_DDL)
    conn.execute(ASSIGN_DDL)
    conn.executemany("INSERT INTO ml_samples (ml_reference_assignment_id, vn_observation_date, split, is_daily_snapshot_selected, dataset_version) VALUES (?,?,?,?,?)", samples)
    conn.executemany("INSERT INTO ml_reference_assignments VALUES (?,?,?,?,?)", assignments)
    rows = conn.execute(wp.SQL_PREVALENCE.replace("%s", "?"), (dataset,)).fetchall()
    return [dict(r) for r in rows]


def _oracle(samples, assignments, dataset="ds"):
    """Oracle doc lap (khong dung SQL): dem theo (date, split) tren cac mau da chon cua dataset, join assignment CUNG dataset."""
    index = {(a_id, a_ds): (fc, bf, occ) for a_id, a_ds, fc, bf, occ in assignments}
    out = {}
    for a_id, date, split, selected, ds in samples:
        if ds != dataset or selected != 1:
            continue
        g = out.setdefault((date, split), {k: 0 for k in wp.NUMERIC})
        g["selected_rows"] += 1
        found = index.get((a_id, ds))
        if found is None:
            g["orphan_assignment"] += 1
            continue
        fc, bf, occ = found
        for key, value in (("fc", fc), ("bf", bf)):
            g[f"{key}_null" if value is None else f"{key}_zero" if value == 0 else f"{key}_one" if value == 1 else f"{key}_invalid"] += 1
        g["occ_null" if occ is None else "occ_known"] += 1
    return out


SAMPLES = [(1, "2026-09-01", "train", 1, "ds"), (1, "2026-09-01", "train", 1, "ds"), (2, "2026-09-01", "train", 1, "ds"), (3, "2026-09-02", "validation", 1, "ds"),
           (4, "2026-09-03", "test", 1, "ds"), (99, "2026-09-03", "test", 1, "ds"),                       # 99: orphan (assignment khong ton tai)
           (5, "2026-09-03", None, 1, "ds"),                                                               # split NULL (purge zone)
           (1, "2026-09-01", "train", 0, "ds"),                                                            # khong duoc chon -> khong dem
           (1, "2026-09-01", "train", 1, "other"), (3, "2026-09-02", "validation", 1, "other")]            # dataset khac -> khong dem
ASSIGNMENTS = [(1, "ds", 1, 0, 2), (2, "ds", None, 1, None), (3, "ds", 0, None, 3), (4, "ds", 2, 1, 4), (5, "ds", None, None, None),     # 4: fc=2 (ngoai mien -> invalid)
               (1, "other", None, None, None), (3, "other", 1, 1, 1), (99, "other", 1, 1, 1)]                                            # 99 chi ton tai o dataset KHAC: van la orphan cua "ds"


def test_sqlite_execution_of_the_real_sql_matches_an_independent_oracle_including_orphans_other_datasets_unselected_rows_and_null_split():
    rows = _sqlite_run(SAMPLES, ASSIGNMENTS)
    got = {(r["vn_date"], r["split"]): {k: int(r[k]) for k in wp.NUMERIC} for r in rows}
    assert got == _oracle(SAMPLES, ASSIGNMENTS)
    totals = {k: sum(g[k] for g in got.values()) for k in wp.NUMERIC}
    assert totals["selected_rows"] == 7 and totals["orphan_assignment"] == 1 and totals["fc_invalid"] == 1 and totals["fc_null"] == 2 and totals["bf_null"] == 2 and totals["occ_null"] == 2
    assert (None) in {r["split"] for r in rows}                                                              # nhom purge zone khong bi mat


def test_sqlite_counts_always_partition_each_bucket_set_even_with_orphans():
    evaluated = wp.evaluate_prevalence(_sqlite_run(SAMPLES, ASSIGNMENTS), 7)
    assert evaluated["integrity"]["fc_buckets_reconcile"] and evaluated["integrity"]["bf_buckets_reconcile"] and evaluated["integrity"]["occ_buckets_reconcile"]
    assert evaluated["integrity"]["every_group_reconciles"] and evaluated["integrity"]["selected_equals_expected"]
    assert evaluated["status"] == "FAIL" and not evaluated["integrity"]["zero_orphans"] and not evaluated["integrity"]["zero_invalid_flags"]   # co orphan + invalid => khong MEASURED


def test_a_clean_world_is_measured():
    clean_assign = [(1, "ds", 1, 0, 2), (2, "ds", None, 1, None), (3, "ds", 0, None, 3), (4, "ds", 1, 1, 4), (5, "ds", None, None, None)]
    clean_samples = [s for s in SAMPLES if s[0] != 99]
    evaluated = wp.evaluate_prevalence(_sqlite_run(clean_samples, clean_assign), 6)
    assert evaluated["status"] == "MEASURED" and evaluated["integrity"]["ok"]
    totals = evaluated["summary"]["totals"]
    assert totals["fc_null"] == 2 and totals["bf_null"] == 2 and totals["occ_null"] == 2 and totals["fc_zero"] == 1 and totals["fc_one"] == 3 and totals["selected_rows"] == 6
    assert evaluated["summary"]["by_split"]["None"]["fc_null"] == 1 and evaluated["summary"]["by_split"]["train"]["selected_rows"] == 3
    assert evaluated["summary"]["dates_with_nulls"]["fc_null"] == {"count": 2, "first": "2026-09-01", "last": "2026-09-03"}
    assert evaluated["summary"]["null_fraction_of_non_orphan"]["fc_null"] == pytest.approx(1 / 3)


# =========================================================================== danh gia thuan: negative controls
def _row(**over):
    base = {"vn_date": D(2026, 9, 1), "split": "train", "selected_rows": 4, "orphan_assignment": 0, "fc_null": 1, "fc_zero": 1, "fc_one": 2, "fc_invalid": 0,
            "bf_null": 0, "bf_zero": 1, "bf_one": 3, "bf_invalid": 0, "occ_null": 1, "occ_known": 3}
    base.update(over)
    return base


def test_evaluation_accepts_a_consistent_group_and_decimal_driver_values():
    assert wp.evaluate_prevalence([_row()], 4)["status"] == "MEASURED"
    decimals = {k: decimal.Decimal(v) if isinstance(v, int) else v for k, v in _row().items()}
    assert wp.evaluate_prevalence([decimals], 4)["status"] == "MEASURED"


@pytest.mark.parametrize("name,rows,expected", [
    ("wrong denominator", [_row()], 5),
    ("fc buckets do not add up", [_row(fc_one=1)], 4),
    ("bf buckets do not add up", [_row(bf_one=2)], 4),
    ("occupancy buckets do not add up", [_row(occ_known=2)], 4),
    ("orphan present", [_row(orphan_assignment=1, fc_one=1, bf_one=2, occ_known=2)], 4),
    ("invalid fc", [_row(fc_invalid=1, fc_one=1)], 4),
    ("invalid bf", [_row(bf_invalid=1, bf_one=2)], 4),
    ("compensating groups hide a bad group", [_row(fc_one=3), _row(split="test", selected_rows=2, fc_null=0, fc_zero=0, fc_one=1, bf_null=0, bf_zero=1, bf_one=1, occ_null=0, occ_known=2)], 6),
])
def test_integrity_violations_are_fail_never_measured(name, rows, expected):
    result = wp.evaluate_prevalence(rows, expected)
    assert result["status"] == "FAIL", name


def test_group_level_reconciliation_catches_errors_that_cancel_out_in_the_global_totals():
    rows = [_row(fc_one=3), _row(split="test", selected_rows=2, fc_null=0, fc_zero=0, fc_one=1, bf_null=0, bf_zero=1, bf_one=1, occ_null=0, occ_known=2)]
    result = wp.evaluate_prevalence(rows, 6)
    assert result["integrity"]["fc_buckets_reconcile"] is True and result["integrity"]["every_group_reconciles"] is False and result["status"] == "FAIL"


@pytest.mark.parametrize("over", [{"fc_null": None}, {"fc_null": -1}, {"selected_rows": "x"}, {"occ_null": [1]}])
def test_non_numeric_negative_or_null_aggregates_are_a_clean_fail(over):
    result = wp.evaluate_prevalence([_row(**over)], 4)
    assert result["status"] == "FAIL" and "khong hop le" in result["reason"]


BIG = dict(selected_rows=146431, orphan_assignment=0, fc_null=25, fc_zero=50000, fc_one=146431 - 25 - 50000, fc_invalid=0, bf_null=0, bf_zero=100000, bf_one=46431, bf_invalid=0,
           occ_null=28, occ_known=146403)
COUNTERS = [key for key in BIG]


def test_the_big_consistent_row_is_measured_as_the_baseline_for_the_gpt_controls():
    assert sum(BIG[k] for k in ("orphan_assignment", "fc_null", "fc_zero", "fc_one", "fc_invalid")) == 146431
    assert wp.evaluate_prevalence([_row(**BIG)], 146431, lower_bounds=wp.Q4_PREFIX_LOWER_BOUNDS)["status"] == "MEASURED"


def test_gpt_control_1_every_counter_shifted_by_a_decimal_fraction_is_a_fail_not_measured():
    shifted = {key: decimal.Decimal(str(value)) + decimal.Decimal("0.9") for key, value in BIG.items()}
    result = wp.evaluate_prevalence([_row(**shifted)], 146431, lower_bounds=wp.Q4_PREFIX_LOWER_BOUNDS)
    assert result["status"] == "FAIL" and "khong nguyen" in result["reason"] and result["integrity"] == {"valid_rows": False}


def test_gpt_control_2_a_bool_counter_is_a_fail_not_the_number_one():
    result = wp.evaluate_prevalence([_row(**{**BIG, "fc_one": True, "fc_zero": 146405})], 146431, lower_bounds=wp.Q4_PREFIX_LOWER_BOUNDS)
    assert result["status"] == "FAIL" and "bool" in result["reason"]
    result = wp.evaluate_prevalence([_row(**{**BIG, "bf_invalid": False})], 146431, lower_bounds=wp.Q4_PREFIX_LOWER_BOUNDS)         # False cung khong phai 0
    assert result["status"] == "FAIL" and "bool" in result["reason"]


@pytest.mark.parametrize("value", [0, 25, 146431, decimal.Decimal("0"), decimal.Decimal("25"), decimal.Decimal("25.0"), decimal.Decimal("2.5E+1"), decimal.Decimal("-0"), 10 ** 12])
def test_integral_non_negative_int_and_decimal_counters_are_accepted_as_exact_ints(value):
    out = wp._num(value)
    assert type(out) is int and out == int(value)


@pytest.mark.parametrize("value", [None, True, False, 25.0, float("nan"), float("inf"), "25", "x", b"25", [1], {}, (1,), decimal.Decimal("NaN"), decimal.Decimal("sNaN"), decimal.Decimal("Infinity"),
                                   decimal.Decimal("-Infinity"), decimal.Decimal("-1"), -1, decimal.Decimal("0.5"), decimal.Decimal("25.9"), decimal.Decimal("1E+400"), decimal.Decimal("1E+999999999"),
                                   10 ** 12 + 1, 10 ** 30, complex(1, 0), object()])
def test_everything_else_is_a_clear_value_error_never_an_overflow_or_a_silent_truncation(value):
    with pytest.raises(ValueError):
        wp._num(value)


def test_numpy_scalars_are_not_in_the_declared_set():
    np = pytest.importorskip("numpy")
    for value in (np.int64(25), np.float64(25.0), np.bool_(True)):
        with pytest.raises(ValueError):
            wp._num(value)


@pytest.mark.parametrize("value", [decimal.Decimal("1E+999999999"), float("inf"), decimal.Decimal("NaN"), decimal.Decimal("sNaN"), object()])
def test_pathological_counters_return_a_fail_report_and_never_raise_out_of_the_evaluator(value):
    result = wp.evaluate_prevalence([_row(fc_null=value)], 4)
    assert result["status"] == "FAIL" and result["integrity"] == {"valid_rows": False}


@pytest.mark.parametrize("error", [decimal.InvalidOperation(), decimal.Overflow(), OverflowError("x"), ZeroDivisionError("x")])
def test_the_evaluator_turns_unexpected_arithmetic_errors_from_the_counter_validator_into_fail_defence_in_depth(monkeypatch, error):
    def explode(value):
        raise error

    monkeypatch.setattr(wp, "_num", explode)
    result = wp.evaluate_prevalence([_row()], 4)
    assert result["status"] == "FAIL" and result["integrity"] == {"valid_rows": False}


def test_missing_columns_and_truncation_are_not_measured():
    broken = _row()
    broken.pop("fc_one")
    assert wp.evaluate_prevalence([broken], 4)["status"] == "FAIL"
    assert wp.evaluate_prevalence([_row()], 4, truncated=True)["status"] == "UNVERIFIED"
    assert wp.evaluate_prevalence([], 0)["status"] in {"MEASURED", "FAIL"} and wp.evaluate_prevalence([], 5)["status"] == "FAIL"


def test_summarize_never_reports_measured_without_both_datasets_measured_and_step0_ok():
    ok_step0 = {"all_ok": True}
    both = {name: {"status": "MEASURED"} for name in wa.DATASETS}
    assert wp.summarize({"refusals": [], "step0": ok_step0, "datasets": both, "mode": "run"})["overall"] == "MEASURED"
    one = {**both, "ds_20261006_dev3b": {"status": "FAIL"}}
    assert wp.summarize({"refusals": [], "step0": ok_step0, "datasets": one, "mode": "run"})["overall"] == "FAIL"
    assert wp.summarize({"refusals": [], "step0": ok_step0, "datasets": {"ds_20261006_dev1b": {"status": "MEASURED"}}, "mode": "run"})["overall"] == "UNVERIFIED"
    unver = {**both, "ds_20261006_dev1b": {"status": "UNVERIFIED"}}
    assert wp.summarize({"refusals": [], "step0": ok_step0, "datasets": unver, "mode": "run"})["overall"] == "UNVERIFIED"
    assert wp.summarize({"refusals": ["x"], "step0": ok_step0, "datasets": both, "mode": "run"})["overall"] == "REFUSED"
    assert wp.summarize({"refusals": [], "step0": {"all_ok": False}, "datasets": both, "mode": "run"})["overall"] == "REFUSED"
    assert wp.summarize({"refusals": [], "datasets": both, "mode": "run"})["overall"] == "REFUSED"
    plan_ok = {name: {"status": "PLAN_OK"} for name in wa.DATASETS}
    assert wp.summarize({"refusals": [], "step0": ok_step0, "datasets": plan_ok, "mode": "explain"})["overall"] == "EXPLAIN_OK"
    assert wp.summarize({"refusals": [], "step0": ok_step0, "datasets": plan_ok, "mode": "run"})["overall"] == "UNVERIFIED"          # PLAN_OK khong phai ket qua do
    refused = {**plan_ok, "ds_20261006_dev3b": {"status": "PLAN_REFUSED"}}
    assert wp.summarize({"refusals": [], "step0": ok_step0, "datasets": refused, "mode": "explain"})["overall"] == "EXPLAIN_REFUSED"


# =========================================================================== wrapper voi DB gia
ARTIFACTS = {"samples_sha": {"ds_20261006_dev1b": "aa" * 32, "ds_20261006_dev3b": "bb" * 32}, "samples_rows": {"ds_20261006_dev1b": 4, "ds_20261006_dev3b": 4}}


def _manifest(name):
    return {"dataset_version": name, "import_batch_id": wa.BATCH_ID, "status": "pass", "last_completed_step": "validation", "active_step": None,
            "build_config_sha256": wa.DATASETS[name]["build_config_sha256"],
            "output_parquet_sha256_json": json.dumps({"samples.parquet": {"file_sha256": ARTIFACTS["samples_sha"][name], "content_sha256": "cc" * 32, "rows": 4, "columns": 9}}),
            "anomaly_registry_cutoff_at": dt.datetime(2026, 10, 5), "anomaly_registry_file_sha256": "ee" * 32}


def _good_groups():
    return [{"vn_date": D(2026, 9, 1), "split": "train", "selected_rows": 2, "orphan_assignment": 0, "fc_null": 1, "fc_zero": 0, "fc_one": 1, "fc_invalid": 0, "bf_null": 0, "bf_zero": 1, "bf_one": 1,
             "bf_invalid": 0, "occ_null": 0, "occ_known": 2},
            {"vn_date": D(2026, 9, 2), "split": "test", "selected_rows": 2, "orphan_assignment": 0, "fc_null": 0, "fc_zero": 2, "fc_one": 0, "fc_invalid": 0, "bf_null": 1, "bf_zero": 0, "bf_one": 1,
             "bf_invalid": 0, "occ_null": 1, "occ_known": 1}]


class World:
    def __init__(self):
        self.groups = _good_groups()
        self.manifest = {name: _manifest(name) for name in wa.DATASETS}
        self.plan_type = {}
        self.fail_on = None
        self.on_explain = None
        self.on_select = None

    def answer(self, sql, params):
        if sql.startswith("EXPLAIN "):
            if self.on_explain is not None:
                self.on_explain()
            inner = sql[len("EXPLAIN "):]
            assert inner == wp.SQL_PREVALENCE, "EXPLAIN chi duoc phep cho SQL da duyet"
            return [{"table": "s", "type": self.plan_type.get("s", "ref"), "key": "k", "rows": 1}, {"table": "a", "type": self.plan_type.get("a", "eq_ref"), "key": "PRIMARY", "rows": 1}]
        if sql == wa.SQL_STEP0:
            return [dict(self.manifest[n]) for n in wa.DATASETS if n in self.manifest]
        if sql == wp.SQL_PREVALENCE:
            if self.on_select is not None:
                self.on_select()
            if self.fail_on:
                raise RuntimeError("Query execution was interrupted, maximum statement execution time exceeded")
            return [dict(g) for g in self.groups]
        raise AssertionError(f"SQL la: {sql[:80]}")


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

    def close(self):
        self.closed = True

    def selects(self):
        return [(sql, p) for sql, p in self.log if sql == wp.SQL_PREVALENCE]

    def explains(self):
        return [(sql, p) for sql, p in self.log if sql.startswith("EXPLAIN ")]


class Env:
    def __init__(self, tmp_path, monkeypatch):
        self.tmp_path, self.monkeypatch = tmp_path, monkeypatch
        self.world = World()
        self.conn = FakeConn(self.world)
        self.connect_calls = 0
        self.now = vn(18, 0)
        monkeypatch.setattr(wa, "EXPECTED_SELECTED", 4)
        monkeypatch.setattr(wp, "Q4_PREFIX_LOWER_BOUNDS", {"fc_null": 1, "occ_null": 1})                           # World nho: bound that (25/28) duoc kiem rieng o test duoi
        monkeypatch.setattr(wa, "APPROVALS_DIR", tmp_path)
        monkeypatch.setattr(wa, "AUDIT_OUT_DIR", tmp_path / "out")
        monkeypatch.setattr(wa, "_sleep", lambda s: None)
        monkeypatch.setattr(wa, "_now_utc", lambda: self.now)
        monkeypatch.setattr(wa, "connect", self._connect)
        monkeypatch.setattr(wa, "load_artifacts", lambda need_frames: {**ARTIFACTS, "contract_build_sha": {n: wa.DATASETS[n]["build_config_sha256"] for n in wa.DATASETS}, "frames": {}})
        self.write_approval()
        self.write_evidence()

    def _connect(self, db):
        self.connect_calls += 1
        return self.conn

    def write_approval(self, controller=None, sql=None, modes="explain, run:prevalence", name="48-gpt-approval.md"):
        text = f"# 48 — GPT\n\nAPPROVED_CONTROLLER_SHA256: {controller or wp.script_sha256()}\nAPPROVED_SQL_SET_SHA256: `{sql or wp.sql_set_sha256()}`\nAPPROVED_MODES: {modes}\n"
        path = self.tmp_path / name
        path.write_text(text, encoding="utf-8")
        return path

    def write_evidence(self, **over):
        evidence = ev.build_evidence(0, 0, {"id": 66, "status": "completed"}, self.now, session_read_only=True)
        evidence.update(over)
        path = self.tmp_path / "evidence.json"
        path.write_text(json.dumps(evidence), encoding="utf-8")
        return path

    def run(self, *flags, approval="48-gpt-approval.md"):
        argv = ["--gpt-approval", str(self.tmp_path / approval), "--crawl-evidence", str(self.tmp_path / "evidence.json"), *flags]
        buffer = io.StringIO()
        with contextlib.redirect_stdout(buffer):
            code = wp.main(argv)
        match = re.search(r"^bao cao: (.+)$", buffer.getvalue(), re.MULTILINE)
        report = json.loads(Path(match.group(1)).read_text(encoding="utf-8")) if match else None
        if report is not None:
            assert report["run_id"] in match.group(1)
        return code, report


@pytest.fixture
def env(tmp_path, monkeypatch):
    return Env(tmp_path, monkeypatch)


def test_the_real_q4_prefix_lower_bounds_are_pinned_and_a_world_below_them_fails():
    assert wp.Q4_PREFIX_LOWER_BOUNDS == {"fc_null": 25, "occ_null": 28}                                         # khong dung fixture env: bound that, ham thuan
    low = wp.evaluate_prevalence([_row()], 4, lower_bounds=wp.Q4_PREFIX_LOWER_BOUNDS)
    assert low["status"] == "FAIL" and low["integrity"]["prefix_lower_bounds_ok"] is False
    assert low["integrity"]["prefix_lower_bounds"]["fc_null"] == {"required_at_least": 25, "observed": 1, "ok": False}
    enough = [_row(selected_rows=100, fc_null=25, fc_zero=25, fc_one=50, bf_null=0, bf_zero=50, bf_one=50, occ_null=28, occ_known=72)]
    assert wp.evaluate_prevalence(enough, 100, lower_bounds=wp.Q4_PREFIX_LOWER_BOUNDS)["status"] == "MEASURED"
    assert wp.evaluate_prevalence(enough, 100)["integrity"]["prefix_lower_bounds_ok"] is True                   # khong dat bound => khong kiem


def test_a_dataset_whose_aggregate_contradicts_the_q4_row_level_evidence_is_a_fail_in_the_wrapper(env, monkeypatch):
    monkeypatch.setattr(wp, "Q4_PREFIX_LOWER_BOUNDS", {"fc_null": 2, "occ_null": 1})                              # World co tong fc_null = 1 < 2
    code, report = env.run("--run")
    assert code == 1 and report["verdict"]["overall"] == "FAIL"
    assert report["datasets"]["ds_20261006_dev1b"]["integrity"]["prefix_lower_bounds"]["fc_null"]["ok"] is False


def test_the_base_controller_pin_matches_the_frozen_file_so_the_gate_code_is_the_reviewed_code():
    assert wa.controller_sha256() == wp.PINNED_BASE_CONTROLLER_SHA256


def test_explain_mode_runs_step0_and_one_explain_per_dataset_and_never_the_aggregate(env):
    code, report = env.run("--explain")
    assert code == 0 and report["verdict"]["overall"] == "EXPLAIN_OK"
    assert env.conn.selects() == [] and len(env.conn.explains()) == 2
    assert [p for _, p in env.conn.explains()] == [("ds_20261006_dev1b",), ("ds_20261006_dev3b",)]
    assert report["step0"]["all_ok"] and report["queries_executed"] == 0 and report["explains_executed"] == 2
    assert {p["label"] for p in report["plans"].values()} == {"prevalence"} and all(p["ok"] for p in report["plans"].values())


def test_run_mode_executes_exactly_one_aggregate_per_dataset_after_its_explain_and_is_measured(env):
    code, report = env.run("--run")
    assert code == 0 and report["verdict"]["overall"] == "MEASURED"
    assert len(env.conn.selects()) == 2 and len(env.conn.explains()) == 2
    assert [p for _, p in env.conn.selects()] == [("ds_20261006_dev1b",), ("ds_20261006_dev3b",)]
    order = [("E" if sql.startswith("EXPLAIN ") else "S" if sql == wp.SQL_PREVALENCE else "0") for sql, _ in env.conn.log]
    assert order == ["0", "E", "S", "E", "S"]                                                                     # Buoc 0, roi (EXPLAIN truoc SELECT) cho tung dataset
    assert report["statements_total"] == 2 + 1 + 2 + 2 and report["queries_executed"] == 2 and report["explains_executed"] == 2   # 2 SET + Buoc 0 + 2 EXPLAIN + 2 SELECT
    for name in wa.DATASETS:
        data = report["datasets"][name]
        assert data["status"] == "MEASURED" and data["summary"]["totals"]["selected_rows"] == 4 and data["summary"]["totals"]["fc_null"] == 1
    assert report["crawl_evidence"]["checks"] >= 5 and report["base_controller_sha256"] == wp.PINNED_BASE_CONTROLLER_SHA256


def test_reports_are_never_overwritten(env):
    _, first = env.run("--explain")
    _, second = env.run("--explain")
    assert first["run_id"] != second["run_id"]
    assert len(list((env.tmp_path / "out").glob("prevalence_explain_*.json"))) == 2


def test_orphans_or_wrong_row_count_fail_with_exit_1_and_are_not_a_pass(env):
    env.world.groups[0]["orphan_assignment"], env.world.groups[0]["fc_one"], env.world.groups[0]["bf_one"], env.world.groups[0]["occ_known"] = 1, 0, 0, 1
    code, report = env.run("--run")
    assert code == 1 and report["verdict"]["overall"] == "FAIL"
    env.world.groups = _good_groups()
    env.world.groups[0]["selected_rows"] = 3
    env.world.groups[0].update(fc_one=0, bf_one=0, bf_zero=1, occ_known=1)
    code, report = env.run("--run")
    assert code == 1 and report["verdict"]["overall"] == "FAIL"


def test_a_refused_plan_means_no_aggregate_runs_and_the_result_is_unverified(env):
    env.world.plan_type["s"] = "ALL"
    code, report = env.run("--run")
    assert code == 4 and report["verdict"]["overall"] == "UNVERIFIED" and env.conn.selects() == []
    assert all(d["status"] == "PLAN_REFUSED" for d in report["datasets"].values())
    code, report = env.run("--explain")
    assert code == 4 and report["verdict"]["overall"] == "EXPLAIN_REFUSED" and env.conn.selects() == []


def test_a_timeout_stops_without_retry_and_is_unverified(env):
    env.world.fail_on = True
    code, report = env.run("--run")
    assert code == 4 and report["verdict"]["overall"] == "UNVERIFIED"
    assert len(env.conn.selects()) == 2 and all(d["status"] == "UNVERIFIED" for d in report["datasets"].values())      # moi dataset dung 1 lan, khong retry


def test_group_cap_is_enforced_truncation_is_unverified_and_oversized_results_are_incomplete(env):
    template = _good_groups()[0]
    env.world.groups = [dict(template, vn_date=D(2026, 1, 1) + dt.timedelta(days=i)) for i in range(wp.GROUP_CAP + 1)]
    code, report = env.run("--run")
    assert code == 4 and report["verdict"]["overall"] == "UNVERIFIED" and report["datasets"]["ds_20261006_dev1b"]["status"] == "UNVERIFIED"
    env.world.groups = [dict(template, vn_date=D(2026, 1, 1) + dt.timedelta(days=i)) for i in range(wp.GROUP_CAP + 2)]
    code, report = env.run("--run")
    assert code == 4 and report["datasets"]["ds_20261006_dev1b"]["status"] == "UNVERIFIED"


def test_step0_mismatch_refuses_everything_after_the_manifest_query(env):
    env.world.manifest["ds_20261006_dev1b"]["status"] = "running"
    code, report = env.run("--run")
    assert code == 3 and report["verdict"]["overall"] == "REFUSED"
    assert [sql for sql, _ in env.conn.log] == [wa.SQL_STEP0]


def test_approval_is_bound_to_this_script_its_sql_and_the_requested_mode(env):
    assert env.run("--run", approval=env.write_approval(controller=wa.controller_sha256(), name="49-gpt-wrong.md").name)[0] == 3         # sha cua controller NEN khong phai sha script nay
    assert env.run("--run", approval=env.write_approval(sql="1" * 64, name="49-gpt-wrong-sql.md").name)[0] == 3
    assert env.run("--run", approval=env.write_approval(modes="explain", name="49-gpt-explain-only.md").name)[0] == 3                   # chi duoc EXPLAIN
    assert env.connect_calls == 0
    code, report = env.run("--explain", approval=env.write_approval(modes="explain", name="49-gpt-explain-only.md").name)
    assert code == 0 and report["verdict"]["overall"] == "EXPLAIN_OK"


def test_changed_base_controller_or_non_dsdev_database_or_missing_approval_is_refused_before_any_connection(env, monkeypatch):
    monkeypatch.setattr(wp, "PINNED_BASE_CONTROLLER_SHA256", "0" * 64)
    code, report = env.run("--explain")
    assert code == 3 and any("controller nen" in r for r in report["verdict"]["reasons"]) and env.connect_calls == 0
    monkeypatch.setattr(wp, "PINNED_BASE_CONTROLLER_SHA256", wa.controller_sha256())
    buffer = io.StringIO()
    with contextlib.redirect_stdout(buffer):
        code = wp.main(["--db", "hotel_price_intel", "--explain", "--gpt-approval", str(env.tmp_path / "48-gpt-approval.md"), "--crawl-evidence", str(env.tmp_path / "evidence.json")])
    assert code == 3 and env.connect_calls == 0
    buffer = io.StringIO()
    with contextlib.redirect_stdout(buffer):
        code = wp.main(["--explain", "--gpt-approval", str(env.tmp_path / "missing.md"), "--crawl-evidence", str(env.tmp_path / "evidence.json")])
    assert code == 3 and env.connect_calls == 0


def test_busy_crawler_stale_evidence_or_crawl_window_refuse_before_connecting(env):
    env.write_evidence(active_runs=1)
    assert env.run("--run")[0] == 3 and env.connect_calls == 0
    env.write_evidence()
    env.now = vn(10, 0)
    assert env.run("--run")[0] == 3 and env.connect_calls == 0
    env.now = vn(18, 0)
    env.write_evidence(observed_at_utc="2026-10-08T10:00:00Z", generated_at_utc="2026-10-08T10:00:00Z", expires_at_utc="2026-10-08T10:09:00Z")
    assert env.run("--run")[0] == 3 and env.connect_calls == 0


def test_evidence_revoked_during_an_explain_prevents_the_aggregate_that_follows(env):
    env.world.on_explain = lambda: env.write_evidence(active_runs=1)
    code, report = env.run("--run")
    assert code == 4 and report["verdict"]["overall"] == "UNVERIFIED" and env.conn.selects() == []


def test_gate_is_rechecked_immediately_before_each_aggregate(env):
    calls = []

    def revoke_on_first_select():
        calls.append(1)

    env.world.on_select = revoke_on_first_select
    env.run("--run")
    assert len(calls) == 2
    env.world.on_select = lambda: env.write_evidence(active_runs=1)          # sau SELECT dau tien, bang chung mat => SELECT thu hai khong duoc khoi dong
    code, report = env.run("--run")
    assert env.conn.selects()[2:] == [env.conn.selects()[2]] and report["verdict"]["overall"] == "UNVERIFIED"


def test_decimal_counters_as_returned_by_mysql_sum_are_measured_through_the_whole_wrapper(env):
    env.world.groups = [{k: (decimal.Decimal(v) if isinstance(v, int) else v) for k, v in g.items()} for g in _good_groups()]
    env.world.groups[0]["selected_rows"] = 2                                                                   # COUNT(*) la int; SUM(CASE) la Decimal: tron hai kieu trong cung mot hang
    code, report = env.run("--run")
    assert code == 0 and report["verdict"]["overall"] == "MEASURED" and report["datasets"]["ds_20261006_dev1b"]["summary"]["totals"]["selected_rows"] == 4


def test_fractional_bool_or_null_counters_through_the_wrapper_are_fail_with_a_report_and_exit_1(env):
    for bad in (decimal.Decimal("1.9"), True, None, float("nan")):
        env.world.groups = _good_groups()
        env.world.groups[0]["fc_null"] = bad
        code, report = env.run("--run")
        assert code == 1 and report["verdict"]["overall"] == "FAIL", bad
        assert report["datasets"]["ds_20261006_dev1b"]["status"] == "FAIL" and report["datasets"]["ds_20261006_dev1b"]["integrity"] == {"valid_rows": False}


def test_a_settings_validation_error_at_connect_is_refused_with_a_cwd_hint_and_zero_sql(env, monkeypatch):
    class ValidationError(Exception):                                                                           # cung ten kieu voi pydantic ValidationError
        pass

    message = ("2 validation errors for Settings\nDB_PASSWORD\n  Field required [type=missing, input_value={'DB_HOST': 'h', 'DB_USER': 'u', 'k': 'hunter2-secret'}, input_type=dict]\n"
               "DB_PORT\n  Input should be a valid integer [type=int_parsing, input_value='hunter2-secret', input_type=str]")

    def boom(db):
        raise ValidationError(message)

    monkeypatch.setattr(wa, "connect", boom)
    code, report = env.run("--explain")
    assert code == 3 and report["verdict"]["overall"] == "REFUSED" and env.conn.log == []
    reason = " ".join(report["verdict"]["reasons"])
    assert "ket noi DB that bai" in reason and "cwd=hotel-price-intelligence/backend" in reason and "0 lenh SQL" in reason
    assert "truong: DB_PASSWORD, DB_PORT" in reason and "gia tri duoc an" in reason
    saved = next((env.tmp_path / "out").glob("prevalence_explain_*.json")).read_text(encoding="utf-8")
    assert "hunter2-secret" not in reason and "hunter2-secret" not in saved and "input_value" not in saved                 # khong in/ghi gia tri cau hinh


def test_settings_field_names_are_bounded_in_count_and_length_and_contain_no_values():
    long_name = "X" * 100
    message = "13 validation errors for Settings\n" + "".join(f"FIELD_{i}\n  Field required [type=missing, input_value='v{i}', input_type=str]\n" for i in range(11)) + f"{long_name}\n  bad\n"
    fields = wp._settings_fields(Exception(message))
    assert len(fields) == 10 and fields[0] == "FIELD_0" and all(len(f) <= 64 for f in fields)
    assert wp._settings_fields(Exception(f"1 validation error for Settings\n{long_name}\n  bad")) == ["X" * 64]
    assert wp._settings_fields(Exception("no field lines here")) == [] and all("v1" not in f for f in fields)
    text = wp.connect_refusal(type("ValidationError", (Exception,), {})("1 validation error for Settings\n  weird"))
    assert "truong: khong xac dinh" in text


def test_other_connection_errors_get_no_cwd_hint(env, monkeypatch):
    def boom(db):
        raise ConnectionRefusedError("can't connect to MySQL server")

    monkeypatch.setattr(wa, "connect", boom)
    code, report = env.run("--explain")
    assert code == 3 and "cwd=" not in " ".join(report["verdict"]["reasons"])


def test_the_statement_budget_stops_the_run_unverified(env):
    code, report = env.run("--run", "--max-queries", "4")
    assert code == 4 and report["verdict"]["overall"] == "UNVERIFIED"
    assert report["statements_total"] <= 4 and len(env.conn.selects()) < 2

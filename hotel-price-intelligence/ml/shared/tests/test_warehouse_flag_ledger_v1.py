"""audit_tools/warehouse_flag_ledger_v1.py — producer doc-chi ledger raw + snapshot P4. KHONG ket noi MySQL that: DB gia = SQLite chay CHINH cac cau SQL cua producer (ngu nghia LEFT JOIN/keyset/phan trang that)."""
from __future__ import annotations

import contextlib
import datetime as dt
import hashlib
import importlib.util
import io
import json
import re
import sqlite3
from pathlib import Path

import pytest

TOOLS = Path(__file__).resolve().parents[3] / "audit_tools"


def _load(name, filename):
    spec = importlib.util.spec_from_file_location(name, TOOLS / filename)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


fl = _load("warehouse_flag_ledger_v1", "warehouse_flag_ledger_v1.py")
wa, rp = fl.wa, fl.rp                                           # CHINH cac module ma producer dung
ev = _load("crawl_idle_evidence_for_ledger", "crawl_idle_evidence.py")
UTC = dt.timezone.utc
DS, DS2 = "ds_20261006_dev1b", "ds_20261006_dev3b"
N = 5
BASE_IDS = [1001 + i for i in range(N)]


def vn(hour, minute=0):
    return dt.datetime(2026, 10, 9, hour, minute, tzinfo=wa.VN).astimezone(UTC)


# =========================================================================== SQL / tinh
def test_the_sql_set_contains_exactly_three_statements_each_a_single_read_only_select():
    statements = fl.sql_statements()
    assert set(statements) == {"SQL_STEP0_ONE", "SQL_MANIFEST_SNAPSHOT", "SQL_FLAG_LEDGER"}
    for name, sql in statements.items():
        assert sql.startswith("SELECT ") and ";" not in sql, name
        assert not re.search(r"\b(INSERT|UPDATE|DELETE|DROP|ALTER|CREATE|TRUNCATE|REPLACE|INTO|CALL|LOCK)\b", sql, re.IGNORECASE), name
    assert statements["SQL_FLAG_LEDGER"] == rp.SQL_FLAG_LEDGER and "a.id AS joined_assignment_id" in rp.SQL_FLAG_LEDGER and "INNER JOIN" not in rp.SQL_FLAG_LEDGER.upper()
    assert "WHERE dataset_version = %s" in fl.SQL_STEP0_ONE and "IN (%s, %s)" in fl.SQL_MANIFEST_SNAPSHOT and "build_config_json" in fl.SQL_MANIFEST_SNAPSHOT


def test_the_base_controller_pin_matches_the_frozen_file_and_the_script_hash_is_bound():
    assert wa.controller_sha256() == fl.PINNED_BASE_CONTROLLER_SHA256
    assert fl.script_sha256() == hashlib.sha256((TOOLS / "warehouse_flag_ledger_v1.py").read_bytes()).hexdigest() and len(fl.sql_set_sha256()) == 64


# =========================================================================== DB gia = SQLite
MANIFEST_DDL = ("CREATE TABLE dataset_build_manifests (dataset_version TEXT, import_batch_id TEXT, status TEXT, last_completed_step TEXT, active_step TEXT, build_config_sha256 TEXT, "
                "output_parquet_sha256_json TEXT, anomaly_registry_cutoff_at TEXT, anomaly_registry_file_sha256 TEXT, build_config_json TEXT, library_versions_json TEXT)")


def make_config(code_files=40, tag="a"):
    files = {f"hotel-price-intelligence/ml/f{i:02d}.py": hashlib.sha256(f"{tag}{i}".encode()).hexdigest() for i in range(code_files)}
    code = {"files": files, "excluded_backend_prefixes": ["app.core"], "code_sha256": rp.code_sha256_of(files, ["app.core"])}
    return {"purpose": "dev", "builder_code": code, "purge_gap_days": 1, "evaluation_horizons": [1], "label_config": {"threshold": 0.02}}


class World:
    def __init__(self, tmp_path):
        self.db = sqlite3.connect(":memory:", check_same_thread=False)
        self.db.row_factory = sqlite3.Row
        self.db.execute("CREATE TABLE ml_samples (id INTEGER PRIMARY KEY, dataset_version TEXT, record_id INTEGER, ml_reference_assignment_id INTEGER, is_daily_snapshot_selected INTEGER)")
        self.db.execute("CREATE TABLE ml_reference_assignments (id INTEGER, dataset_version TEXT, hotel_id TEXT, checkin_date TEXT, canonical_series_id TEXT, free_cancellation INTEGER, breakfast_included INTEGER, max_occupancy INTEGER)")
        self.db.execute(MANIFEST_DDL)
        self.identity = {}
        for i, rid in enumerate(BASE_IDS):
            hotel, checkin, series = f"h{i % 2}", f"2026-09-{10 + i % 3:02d}", f"S{i}"
            self.identity[rid] = (hotel, checkin, series)
            self.db.execute("INSERT INTO ml_samples (dataset_version, record_id, ml_reference_assignment_id, is_daily_snapshot_selected) VALUES (?,?,?,1)", (DS, rid, 100 + i))
            self.db.execute("INSERT INTO ml_reference_assignments VALUES (?,?,?,?,?,?,?,?)", (100 + i, DS, hotel, checkin, series, [0, 1, None][i % 3], [1, 0, 1][i % 3], [None, 2, 4][i % 3]))
        self.db.execute("INSERT INTO ml_samples (dataset_version, record_id, ml_reference_assignment_id, is_daily_snapshot_selected) VALUES ('ds_other', 9999, 1, 1)")      # dataset khac: khong duoc lay
        self.db.execute("INSERT INTO ml_samples (dataset_version, record_id, ml_reference_assignment_id, is_daily_snapshot_selected) VALUES (?, 5000, 100, 0)", (DS,))      # khong duoc chon
        self.dataset_dir = tmp_path / "ds"
        self.dataset_dir.mkdir()
        self.samples_sha = self.write_parquet(self.identity)
        cfg1, cfg2 = make_config(tag="a"), make_config(tag="b")
        self.configs = {DS: cfg1, DS2: cfg2}
        self.insert_manifest(DS, self.samples_sha, cfg1)
        self.insert_manifest(DS2, "f" * 64, cfg2)
        self.plan_type, self.fail_on_ledger, self.on_query, self.on_explain = {}, False, None, None
        self.db.commit()

    def write_parquet(self, identity):
        import pyarrow as pa
        import pyarrow.parquet as pq
        ids = sorted(identity)
        table = pa.table({"warehouse_record_id": pa.array(ids, pa.int64()), "hotel_id": [identity[i][0] for i in ids], "checkin_date": pa.array([dt.date.fromisoformat(identity[i][1]) for i in ids], pa.date32()),
                          "canonical_series_id": [identity[i][2] for i in ids], "other": [1.0] * len(ids)})
        path = self.dataset_dir / "samples.parquet"
        pq.write_table(table, path)
        return hashlib.sha256(path.read_bytes()).hexdigest()

    def insert_manifest(self, version, sample_sha, config, **over):
        row = {"dataset_version": version, "import_batch_id": wa.BATCH_ID, "status": "pass", "last_completed_step": "validation", "active_step": None, "build_config_sha256": rp.config_sha256(config),
               "output_parquet_sha256_json": json.dumps({"samples.parquet": {"file_sha256": sample_sha, "content_sha256": "c" * 64, "rows": N}}), "anomaly_registry_cutoff_at": "2026-10-05 00:00:00",
               "anomaly_registry_file_sha256": "e" * 64, "build_config_json": json.dumps(config), "library_versions_json": json.dumps({"pandas": "3.0.5", "pyarrow": "25.0.1"})}
        row.update(over)
        self.db.execute("DELETE FROM dataset_build_manifests WHERE dataset_version = ?", (version,))
        self.db.execute("INSERT INTO dataset_build_manifests VALUES (:dataset_version,:import_batch_id,:status,:last_completed_step,:active_step,:build_config_sha256,:output_parquet_sha256_json,"
                        ":anomaly_registry_cutoff_at,:anomaly_registry_file_sha256,:build_config_json,:library_versions_json)", row)

    def answer(self, sql, params):
        if sql.startswith("EXPLAIN "):
            if self.on_explain is not None:
                self.on_explain()
            assert sql[8:] == rp.SQL_FLAG_LEDGER, "chi duoc EXPLAIN cau ledger"
            return [{"table": "s", "type": self.plan_type.get("s", "range"), "key": "uq_ml_samples", "rows": 5}, {"table": "a", "type": self.plan_type.get("a", "eq_ref"), "key": "PRIMARY", "rows": 1}]
        if sql == fl.SQL_STEP0_ONE:
            return [dict(r) for r in self.db.execute(fl.SQL_STEP0_ONE.replace("%s", "?"), params).fetchall()]
        if sql == fl.SQL_MANIFEST_SNAPSHOT:
            return [dict(r) for r in self.db.execute(fl.SQL_MANIFEST_SNAPSHOT.replace("%s", "?"), params).fetchall()]
        if sql == rp.SQL_FLAG_LEDGER:
            if self.on_query is not None:
                self.on_query(params)
            if self.fail_on_ledger:
                raise RuntimeError("Query execution was interrupted, maximum statement execution time exceeded")
            rows = [dict(r) for r in self.db.execute(rp.SQL_FLAG_LEDGER.replace("%s", "?"), params).fetchall()]
            for row in rows:
                if isinstance(row["a_checkin_date"], str):
                    row["a_checkin_date"] = dt.date.fromisoformat(row["a_checkin_date"])
            return rows
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

    def ledger_selects(self):
        return [(s, p) for s, p in self.log if s == rp.SQL_FLAG_LEDGER]

    def explains(self):
        return [(s, p) for s, p in self.log if s.startswith("EXPLAIN ")]


class Env:
    def __init__(self, tmp_path, monkeypatch):
        self.tmp_path, self.monkeypatch = tmp_path, monkeypatch
        self.world = World(tmp_path)
        self.conn = FakeConn(self.world)
        self.connect_calls = 0
        self.now = vn(18, 0)
        monkeypatch.setattr(wa, "BATCH", 2)
        monkeypatch.setattr(rp, "SQL_FLAG_LEDGER", rp.SQL_FLAG_LEDGER.replace("LIMIT 1000", "LIMIT 2"))
        monkeypatch.setattr(wa, "APPROVALS_DIR", tmp_path)
        monkeypatch.setattr(wa, "AUDIT_OUT_DIR", tmp_path / "out")
        monkeypatch.setattr(wa, "_sleep", lambda s: None)
        monkeypatch.setattr(wa, "_now_utc", lambda: self.now)
        monkeypatch.setattr(wa, "connect", self._connect)
        self.write_approval()
        self.write_evidence()

    def _connect(self, db):
        self.connect_calls += 1
        return self.conn

    def write_approval(self, controller=None, sql=None, modes="explain, run:ledger, run:p4", name="66-gpt-approval.md"):
        text = f"# GPT\n\nAPPROVED_CONTROLLER_SHA256: {controller or fl.script_sha256()}\nAPPROVED_SQL_SET_SHA256: `{sql or fl.sql_set_sha256()}`\nAPPROVED_MODES: {modes}\n"
        path = self.tmp_path / name
        path.write_text(text, encoding="utf-8")
        return path

    def write_evidence(self, **over):
        evidence = ev.build_evidence(0, 0, {"id": 66, "status": "completed"}, self.now, session_read_only=True)
        evidence.update(over)
        path = self.tmp_path / "evidence.json"
        path.write_text(json.dumps(evidence), encoding="utf-8")
        return path

    def pin(self, version):
        return rp.config_sha256(self.world.configs[version])                                      # pin DOC LAP: tinh tu cau hinh goc cua world, khong doc lai tu DB dang bi thao tung

    def run(self, *flags, approval="66-gpt-approval.md", dataset=DS, db="warehouse_dsdev_x", sha=None, rows=N, extra=(), pins="auto", dataset_dir=None):
        versions = [dataset]
        everything = [*extra, *flags]
        if "--snapshot-versions" in everything:
            versions = sorted(set(versions + everything[everything.index("--snapshot-versions") + 1].split(",")))
        if pins == "auto":
            pin_args = [a for v in versions if v in self.world.configs for a in ("--expected-config-sha256", f"{v}={self.pin(v)}")]
        else:
            pin_args = [a for v, d in (pins or {}).items() for a in ("--expected-config-sha256", f"{v}={d}")]
        argv = ["--db", db, "--dataset", dataset, "--dataset-dir", str(dataset_dir or self.world.dataset_dir), "--expected-samples-sha256", sha or self.world.samples_sha, "--expected-rows", str(rows),
                "--gpt-approval", str(self.tmp_path / approval), "--crawl-evidence", str(self.tmp_path / "evidence.json"), "--budget-seconds", "120", "--max-queries", "50", "--max-batches", "10", *pin_args, *extra, *flags]
        buffer = io.StringIO()
        with contextlib.redirect_stdout(buffer):
            code = fl.main(argv)
        match = re.search(r"^bao cao: (.+)$", buffer.getvalue(), re.MULTILINE)
        report = json.loads(Path(match.group(1)).read_text(encoding="utf-8")) if match else None
        if report is not None:
            assert report["run_id"] in match.group(1)
        return code, report


@pytest.fixture
def env(tmp_path, monkeypatch):
    return Env(tmp_path, monkeypatch)


SNAP = ("--snapshot-versions", f"{DS},{DS2}")


# --------------------------------------------------------------------------- duong vui
def test_run_mode_pages_the_keyset_writes_a_schema_pinned_ledger_and_reports_pass(env):
    code, report = env.run("--run")
    assert code == 0 and report["verdict"]["overall"] == "LEDGER_PASS" and report["ledger"]["status"] == "PASS" and report["ledger"]["rows"] == N
    assert len(env.conn.ledger_selects()) == 3 and [params[1] for _, params in env.conn.ledger_selects()] == [0, 1002, 1004]                    # 2 + 2 + 1 hang; cursor keyset theo record_id
    assert report["statements_total"] == 2 + 1 + 1 + 3 and report["queries_executed"] == 3 and report["explains_executed"] == 1 and report["ledger"]["keyset"]["complete"] is True
    file = report["ledger"]["file"]
    assert Path(file["path"]).is_file() and file["sha256"] == hashlib.sha256(Path(file["path"]).read_bytes()).hexdigest() and file["rows"] == N
    rows = rp.read_ledger(Path(file["path"]))
    assert [r["record_id"] for r in rows] == BASE_IDS and all(type(r["raw_free_cancellation"]) in (int, type(None)) for r in rows)
    assert {r["raw_free_cancellation"] for r in rows} == {0, 1, None} and rows[0]["a_checkin_date"] == dt.date(2026, 9, 10) and rows[0]["joined_assignment_id"] == 100
    assert rows[0] == dict(rows[0]) and set(rows[0]) == set(rp.LEDGER_COLUMNS)


def test_dataset_scoping_unselected_and_other_datasets_never_appear(env):
    _, report = env.run("--run")
    rows = rp.read_ledger(Path(report["ledger"]["file"]["path"]))
    assert 9999 not in {r["record_id"] for r in rows} and 5000 not in {r["record_id"] for r in rows}


def test_explain_mode_runs_step0_and_one_explain_and_no_data_select(env):
    code, report = env.run("--explain")
    assert code == 0 and report["verdict"]["overall"] == "EXPLAIN_OK" and env.conn.ledger_selects() == [] and len(env.conn.explains()) == 1
    assert report["statements_total"] == 2 + 1 + 1 and report["queries_executed"] == 0 and "file" not in report["ledger"]


def test_a_refused_plan_means_no_ledger_select_and_no_file(env):
    env.world.plan_type["s"] = "ALL"
    code, report = env.run("--run")
    assert env.conn.ledger_selects() == [] and report["ledger"]["status"] == "PLAN_REFUSED" and code == 4 and not list((env.tmp_path / "out").glob("ledger_*.parquet"))


# --------------------------------------------------------------------------- tinh toan ven cua ledger
def test_an_orphan_in_the_database_is_a_fail_with_no_ledger_file(env):
    env.world.db.execute("DELETE FROM ml_reference_assignments WHERE id = 102")
    code, report = env.run("--run")
    assert code == 1 and report["verdict"]["overall"] == "FAIL" and report["ledger"]["violations"] == {"missing_assignment": 1} and not list((env.tmp_path / "out").glob("ledger_*.parquet"))


@pytest.mark.parametrize("statement,fragment", [
    ("UPDATE ml_reference_assignments SET canonical_series_id = 'X' WHERE id = 101", "identity_mismatch"),
    ("UPDATE ml_reference_assignments SET free_cancellation = 2 WHERE id = 100", "domain_invalid_free_cancellation"),
    ("UPDATE ml_reference_assignments SET max_occupancy = -3 WHERE id = 100", "domain_invalid_max_occupancy"),
    ("UPDATE ml_reference_assignments SET dataset_version = 'ds_other' WHERE id = 103", "missing_assignment"),
])
def test_identity_domain_and_cross_dataset_violations_fail_the_run(env, statement, fragment):
    env.world.db.execute(statement)
    code, report = env.run("--run")
    assert code == 1 and fragment in report["ledger"]["violations"] and "file" not in report["ledger"]


def test_a_sample_missing_from_the_database_or_extra_in_it_fails_against_the_parquet_key_set(env):
    env.world.db.execute("DELETE FROM ml_samples WHERE record_id = 1003 AND dataset_version = ?", (DS,))
    code, report = env.run("--run")
    assert code == 1 and report["ledger"]["missing_vs_parquet"] == 1 and report["ledger"]["status"] == "FAIL"
    env2_world = env.world
    env2_world.db.execute("INSERT INTO ml_samples (dataset_version, record_id, ml_reference_assignment_id, is_daily_snapshot_selected) VALUES (?, 7777, 100, 1)", (DS,))
    code, report = env.run("--run")
    assert code == 1 and report["ledger"]["extra_vs_parquet"] == 1


# --------------------------------------------------------------------------- Buoc 0 / artifact / gate
def test_artifact_hash_or_row_count_mismatch_is_refused_before_any_connection(env):
    assert env.run("--run", sha="0" * 64)[0] == 3 and env.connect_calls == 0
    assert env.run("--run", rows=N + 1)[0] == 3 and env.connect_calls == 0


@pytest.mark.parametrize("override", [{"status": "running"}, {"last_completed_step": "split"}, {"active_step": "samples_labels"}, {"import_batch_id": "other"},
                                      {"output_parquet_sha256_json": json.dumps({"samples.parquet": {"file_sha256": "0" * 64, "rows": N}})},
                                      {"output_parquet_sha256_json": json.dumps({"samples.parquet": {"file_sha256": "x", "rows": 3}})}])
def test_manifest_mismatch_refuses_everything_after_the_single_step0_query(env, override):
    env.world.insert_manifest(DS, env.world.samples_sha, env.world.configs[DS], **override)
    if "output_parquet_sha256_json" in override and "file_sha256\": \"x" in override["output_parquet_sha256_json"]:
        env.world.insert_manifest(DS, env.world.samples_sha, env.world.configs[DS], output_parquet_sha256_json=json.dumps({"samples.parquet": {"file_sha256": env.world.samples_sha, "rows": 3}}))
    code, report = env.run("--run")
    assert code == 3 and [s for s, _ in env.conn.log] == [fl.SQL_STEP0_ONE]


def test_a_dataset_without_exactly_one_manifest_row_is_refused(env):
    env.world.db.execute("DELETE FROM dataset_build_manifests WHERE dataset_version = ?", (DS,))
    assert env.run("--run")[0] == 3


def test_approval_binds_script_sql_set_and_modes_p4_needs_its_own_mode(env):
    assert env.run("--run", approval=env.write_approval(controller=wa.controller_sha256(), name="67-gpt-base.md").name)[0] == 3
    assert env.run("--run", approval=env.write_approval(sql="1" * 64, name="67-gpt-sql.md").name)[0] == 3
    assert env.run("--run", approval=env.write_approval(modes="explain", name="67-gpt-explain-only.md").name)[0] == 3
    assert env.run("--run", *SNAP, approval=env.write_approval(modes="explain, run:ledger", name="67-gpt-no-p4.md").name)[0] == 3
    assert env.run("--explain", approval=env.write_approval(modes="explain", name="67-gpt-explain-only.md").name)[0] == 0
    assert env.connect_calls == 1


def test_changed_base_controller_bad_db_bad_dataset_and_bad_snapshot_versions_are_refused_before_connecting(env, monkeypatch):
    assert env.run("--run", db="hotel_price_intel")[0] == 3 and env.run("--run", dataset="not_a_dataset")[0] == 3
    assert env.run("--run", extra=("--snapshot-versions", DS))[0] == 3 and env.run("--run", extra=("--snapshot-versions", f"{DS},{DS}"))[0] == 3
    monkeypatch.setattr(fl, "PINNED_BASE_CONTROLLER_SHA256", "0" * 64)
    assert env.run("--run")[0] == 3 and env.connect_calls == 0


def test_busy_crawler_stale_evidence_or_the_crawl_window_refuse_before_connecting(env):
    env.write_evidence(active_items=3)
    assert env.run("--run")[0] == 3 and env.connect_calls == 0
    env.write_evidence()
    env.now = vn(10, 0)
    assert env.run("--run")[0] == 3 and env.connect_calls == 0


def test_evidence_revoked_during_the_explain_prevents_every_ledger_select(env):
    env.world.on_explain = lambda: env.write_evidence(active_runs=1)
    code, report = env.run("--run")
    assert env.conn.ledger_selects() == [] and code == 4 and report["ledger"]["status"] == "UNVERIFIED"


def test_evidence_revoked_between_pages_stops_further_pages_and_the_result_is_unverified_with_no_file(env):
    env.world.on_query = lambda params: env.write_evidence(active_items=1) if params[1] == 0 else None             # sau trang dau tien
    code, report = env.run("--run")
    assert len(env.conn.ledger_selects()) == 1 and code == 4 and report["ledger"]["status"] == "UNVERIFIED" and "file" not in report["ledger"]


def test_stale_evidence_between_pages_is_also_a_stop(env):
    def age_it(params):
        if params[1] == 0:
            env.now = env.now + dt.timedelta(seconds=200)                                                           # bang chung qua 120 s tuoi
    env.world.on_query = age_it
    code, report = env.run("--run")
    assert code == 4 and report["ledger"]["status"] == "UNVERIFIED" and len(env.conn.ledger_selects()) == 1


def test_a_timeout_stops_without_retry_and_is_unverified(env):
    env.world.fail_on_ledger = True
    code, report = env.run("--run")
    assert code == 4 and len(env.conn.ledger_selects()) == 1 and report["ledger"]["status"] == "UNVERIFIED"


def test_max_batches_exhausted_is_unverified_never_pass(env):
    code, report = env.run("--run", extra=())
    assert code == 0
    buffer = io.StringIO()
    argv = ["--db", "warehouse_dsdev_x", "--dataset", DS, "--dataset-dir", str(env.world.dataset_dir), "--expected-samples-sha256", env.world.samples_sha, "--expected-rows", str(N), "--gpt-approval",
            str(env.tmp_path / "66-gpt-approval.md"), "--crawl-evidence", str(env.tmp_path / "evidence.json"), "--budget-seconds", "120", "--max-queries", "50", "--max-batches", "2", "--run", "--expected-config-sha256", f"{DS}={env.pin(DS)}"]
    with contextlib.redirect_stdout(buffer):
        code = fl.main(argv)
    report = json.loads(Path(re.search(r"^bao cao: (.+)$", buffer.getvalue(), re.MULTILINE).group(1)).read_text(encoding="utf-8"))
    assert code == 4 and report["ledger"]["status"] == "UNVERIFIED" and "file" not in report["ledger"]


def test_the_statement_budget_stops_the_run_unverified(env):
    buffer = io.StringIO()
    argv = ["--db", "warehouse_dsdev_x", "--dataset", DS, "--dataset-dir", str(env.world.dataset_dir), "--expected-samples-sha256", env.world.samples_sha, "--expected-rows", str(N), "--gpt-approval",
            str(env.tmp_path / "66-gpt-approval.md"), "--crawl-evidence", str(env.tmp_path / "evidence.json"), "--budget-seconds", "120", "--max-queries", "5", "--max-batches", "10", "--run", "--expected-config-sha256", f"{DS}={env.pin(DS)}"]
    with contextlib.redirect_stdout(buffer):
        code = fl.main(argv)
    report = json.loads(Path(re.search(r"^bao cao: (.+)$", buffer.getvalue(), re.MULTILINE).group(1)).read_text(encoding="utf-8"))
    assert code == 4 and report["statements_total"] <= 5 and report["ledger"]["status"] == "UNVERIFIED"


def test_reports_and_ledger_files_are_never_overwritten(env):
    _, first = env.run("--run")
    _, second = env.run("--run")
    assert first["run_id"] != second["run_id"] and first["ledger"]["file"]["path"] != second["ledger"]["file"]["path"]
    assert len(list((env.tmp_path / "out").glob("flag_ledger_run_*.json"))) == 2 and len(list((env.tmp_path / "out").glob("ledger_*.parquet"))) == 2


# --------------------------------------------------------------------------- P4 snapshot
def test_p4_snapshot_is_one_extra_select_returns_both_configs_and_is_saved_verbatim(env):
    code, report = env.run("--run", *SNAP, extra=("--expected-old-code-sha256", env.world.configs[DS]["builder_code"]["code_sha256"]))
    snap = report["snapshot"]
    assert code == 1 and snap["status"] == "FAIL"                                                      # DS2 co code khac DS => pin chi dung cho 1 trong 2 => FAIL (khong nuot)
    code, report = env.run("--run", *SNAP)
    snap = report["snapshot"]
    assert code == 0 and snap["status"] == "PASS" and set(snap["versions"]) == {DS, DS2} and report["statements_total"] == 2 + 1 + 1 + 1 + 3
    for version in (DS, DS2):
        entry = snap["versions"][version]
        assert entry["canonical_config_sha256"] == entry["build_config_sha256_in_db"] == rp.config_sha256(env.world.configs[version]) and entry["config"] == env.world.configs[version]
        assert entry["builder_code"]["files"] == 40 and entry["library_versions"] == {"pandas": "3.0.5", "pyarrow": "25.0.1"}
    saved = json.loads(Path(snap["file"]["path"]).read_text(encoding="utf-8"))
    assert saved["versions"] == [DS, DS2] and saved["rows"][0]["build_config_json"] == json.dumps(env.world.configs[DS]) and hashlib.sha256(Path(snap["file"]["path"]).read_bytes()).hexdigest() == snap["file"]["sha256"]


def test_p4_pinned_old_code_sha_must_match_when_both_versions_share_it(env):
    shared = make_config(tag="a")
    env.world.insert_manifest(DS2, "f" * 64, shared)
    env.world.insert_manifest(DS, env.world.samples_sha, shared)
    env.world.configs[DS] = env.world.configs[DS2] = shared                                          # pin doc lap theo cau hinh moi (hop le)
    ok = env.run("--run", *SNAP, extra=("--expected-old-code-sha256", shared["builder_code"]["code_sha256"]))
    assert ok[0] == 0 and ok[1]["snapshot"]["status"] == "PASS"
    bad = env.run("--run", *SNAP, extra=("--expected-old-code-sha256", "0" * 64))
    assert bad[0] == 1 and "pinned old" in " ".join(bad[1]["snapshot"]["violations"])


@pytest.mark.parametrize("name,patch,fragment", [
    ("config_sha_not_reproduced", lambda w: w.db.execute("UPDATE dataset_build_manifests SET build_config_sha256 = ? WHERE dataset_version = ?", ("0" * 64, DS2)), "canonical config sha"),
    ("config_not_json", lambda w: w.db.execute("UPDATE dataset_build_manifests SET build_config_json = 'not json' WHERE dataset_version = ?", (DS2,)), "json_unparseable"),
    ("config_not_object", lambda w: w.db.execute("UPDATE dataset_build_manifests SET build_config_json = '[1,2]' WHERE dataset_version = ?", (DS2,)), "not JSON objects"),
    ("libraries_not_object", lambda w: w.db.execute("UPDATE dataset_build_manifests SET library_versions_json = '7' WHERE dataset_version = ?", (DS2,)), "not JSON objects"),
    ("one_row_missing", lambda w: w.db.execute("DELETE FROM dataset_build_manifests WHERE dataset_version = ?", (DS2,)), "expected exactly 2 rows"),
])
def test_p4_violations_fail_the_run_and_nothing_is_saved(env, name, patch, fragment):
    patch(env.world)
    code, report = env.run("--run", *SNAP)
    assert code == 1 and report["snapshot"]["status"] == "FAIL" and fragment in " ".join(report["snapshot"]["violations"]), name
    assert report["snapshot"]["file"]["trusted"] is False and "UNTRUSTED" in report["snapshot"]["file"]["path"]                                    # P1: luu de dieu tra nhung danh dau khong tin cay
    assert json.loads(Path(report["snapshot"]["file"]["path"]).read_text(encoding="utf-8"))["trusted"] is False
    assert env.conn.ledger_selects() == [] and env.conn.explains() == [] and report["ledger"]["status"] == "NOT_RUN" and not list((env.tmp_path / "out").glob("ledger_*.parquet"))


def test_p4_code_manifest_must_have_forty_files_and_be_self_consistent(env):
    env.world.insert_manifest(DS2, "f" * 64, make_config(code_files=39, tag="b"))
    code, report = env.run("--run", *SNAP)
    assert code == 1 and "files 39 != 40" in " ".join(report["snapshot"]["violations"])
    broken = make_config(tag="b")
    broken["builder_code"]["code_sha256"] = "0" * 64
    env.world.insert_manifest(DS2, "f" * 64, broken)
    code, report = env.run("--run", *SNAP)
    assert code == 1 and "self hash mismatch" in " ".join(report["snapshot"]["violations"])


def test_p4_is_not_executed_in_explain_mode_and_does_not_block_it(env):
    code, report = env.run("--explain", *SNAP)
    assert code == 0 and report["snapshot"] == {"status": "SKIPPED_IN_EXPLAIN"} and all(s != fl.SQL_MANIFEST_SNAPSHOT for s, _ in env.conn.log)


def test_json_columns_may_arrive_as_bytes_or_dicts_from_the_driver(env):
    cfg = env.world.configs[DS2]
    env.world.db.execute("UPDATE dataset_build_manifests SET build_config_json = ? WHERE dataset_version = ?", (json.dumps(cfg), DS2))
    rows = [{"dataset_version": DS, "import_batch_id": wa.BATCH_ID, "build_config_json": json.dumps(env.world.configs[DS]).encode(), "build_config_sha256": rp.config_sha256(env.world.configs[DS]), "library_versions_json": b'{"pandas":"3.0.5"}'},
            {"dataset_version": DS2, "import_batch_id": wa.BATCH_ID, "build_config_json": cfg, "build_config_sha256": rp.config_sha256(cfg), "library_versions_json": {"pandas": "3.0.5"}}]
    assert fl.evaluate_snapshot(rows, [DS, DS2], expected_old_code_sha256=None)["ok"] is True


def test_a_parquet_with_duplicate_record_ids_is_refused_before_any_connection(env):
    import pyarrow as pa
    import pyarrow.parquet as pq
    ids = BASE_IDS[:-1] + [BASE_IDS[0]]                                                             # N hang nhung co id trung
    table = pa.table({"warehouse_record_id": pa.array(ids, pa.int64()), "hotel_id": ["h"] * N, "checkin_date": pa.array([dt.date(2026, 9, 10)] * N, pa.date32()), "canonical_series_id": ["s"] * N})
    path = env.world.dataset_dir / "samples.parquet"
    pq.write_table(table, path)
    sha = hashlib.sha256(path.read_bytes()).hexdigest()
    code, report = env.run("--run", sha=sha)
    assert code == 3 and env.connect_calls == 0 and any("trung" in r for r in report["verdict"]["reasons"])


# =========================================================================== GPT 66 (P1/P2/P3)
def _ledger_files(env):
    return list((env.tmp_path / "out").glob("ledger_*.parquet"))


def test_p1_a_failed_p4_snapshot_means_zero_ledger_selects_zero_explains_and_zero_ledger_files(env):
    env.world.db.execute("UPDATE dataset_build_manifests SET build_config_sha256 = ? WHERE dataset_version = ?", ("0" * 64, DS2))
    code, report = env.run("--run", *SNAP)
    assert code == 1 and report["verdict"]["overall"] == "FAIL" and report["snapshot"]["status"] == "FAIL"
    assert report["ledger"] == {"status": "NOT_RUN", "reason": "P4 snapshot FAIL: khong doc ledger"}
    assert env.conn.ledger_selects() == [] and env.conn.explains() == [] and _ledger_files(env) == []
    assert report["snapshot"]["file"]["trusted"] is False and Path(report["snapshot"]["file"]["path"]).name.startswith("manifest_snapshot_UNTRUSTED_")
    assert not [f for f in (env.tmp_path / "out").glob("manifest_snapshot_*.json") if "UNTRUSTED" not in f.name]                   # khong co ban "tin cay" nao


def test_p1_an_unverified_p4_snapshot_also_blocks_the_ledger(env):
    original = env.world.answer

    def answer(sql, params):
        result = original(sql, params)
        if sql == fl.SQL_STEP0_ONE:
            env.now = env.now + dt.timedelta(hours=3)                                                   # bang chung het han NGAY SAU Buoc 0 => cau P4 bi chan truoc khi chay
        return result

    env.world.answer = answer
    code, report = env.run("--run", *SNAP)
    assert code == 4 and report["snapshot"]["status"] == "UNVERIFIED" and report["verdict"]["overall"] == "UNVERIFIED"
    assert report["ledger"]["status"] == "NOT_RUN" and "UNVERIFIED" in report["ledger"]["reason"]
    assert env.conn.ledger_selects() == [] and env.conn.explains() == [] and _ledger_files(env) == [] and all(s != fl.SQL_MANIFEST_SNAPSHOT for s, _ in env.conn.log)


def test_p1_the_ledger_file_is_written_only_when_the_overall_phase_is_ledger_pass(env, monkeypatch):
    code, report = env.run("--run", *SNAP)
    assert code == 0 and report["verdict"]["overall"] == "LEDGER_PASS" and report["snapshot"]["status"] == "PASS" and len(_ledger_files(env)) == 1
    assert report["snapshot"]["file"]["trusted"] is True and "UNTRUSTED" not in report["snapshot"]["file"]["path"]
    monkeypatch.setattr(rp, "write_ledger", lambda rows, path: (_ for _ in ()).throw(ValueError("ledger_rows_invalid: boom")))
    code, report = env.run("--run", *SNAP)
    assert code == 1 and report["verdict"]["overall"] == "FAIL" and report["ledger"]["status"] == "FAIL" and "ledger_rows_invalid" in report["ledger"]["write_error"]
    assert len(_ledger_files(env)) == 1                                                                 # lan loi khong tao them tep


def test_p2_a_self_consistent_forged_config_is_caught_by_the_independent_pin_at_step0(env):
    forged = json.loads(json.dumps(env.world.configs[DS]))
    forged["purge_gap_days"] = 7                                                                        # DB bi sua THONG NHAT: config_json va build_config_sha256 deu cap nhat
    env.world.db.execute("UPDATE dataset_build_manifests SET build_config_json = ?, build_config_sha256 = ? WHERE dataset_version = ?", (json.dumps(forged), rp.config_sha256(forged), DS))
    code, report = env.run("--run")
    assert code == 3 and report["verdict"]["overall"] == "REFUSED" and report["step0"]["ok"] is False and report["step0"]["checks"]["build_config_sha256_pin"] is False
    assert env.conn.ledger_selects() == [] and env.conn.explains() == [] and _ledger_files(env) == []


def test_p2_a_self_consistent_forged_old_snapshot_version_is_caught_by_its_own_pin(env):
    forged = json.loads(json.dumps(env.world.configs[DS2]))
    forged["purge_gap_days"] = 7
    env.world.db.execute("UPDATE dataset_build_manifests SET build_config_json = ?, build_config_sha256 = ? WHERE dataset_version = ?", (json.dumps(forged), rp.config_sha256(forged), DS2))
    code, report = env.run("--run", *SNAP)
    versions = report["snapshot"]["violations"]
    assert code == 1 and report["snapshot"]["status"] == "FAIL" and any("build_config_sha256 in DB != pin doc lap" in v and DS2 in v for v in versions) and any("canonical config sha != pin doc lap" in v for v in versions)
    assert env.conn.ledger_selects() == [] and _ledger_files(env) == []


def test_p2_snapshot_rows_must_belong_to_the_expected_import_batch(env):
    env.world.insert_manifest(DS2, "f" * 64, env.world.configs[DS2], import_batch_id="other_batch")
    code, report = env.run("--run", *SNAP)
    assert code == 1 and any("import_batch_id" in v and DS2 in v for v in report["snapshot"]["violations"]) and env.conn.ledger_selects() == []


@pytest.mark.parametrize("flags,extra,pins,fragment", [
    (("--run",), (), {}, "--run can --expected-config-sha256 cho dataset"),
    (("--run", *SNAP), (), "only_dataset", "CA HAI version"),
    (("--run",), ("--expected-config-sha256", "not-a-pin"), {}, "khong hop le"),
    (("--run",), ("--expected-config-sha256", f"{DS}={'g' * 64}"), {}, "khong hop le"),
    (("--run",), ("--expected-config-sha256", f"bad name={'a' * 64}"), {}, "khong hop le"),
])
def test_p2_pins_are_mandatory_and_validated_before_any_connection(env, flags, extra, pins, fragment):
    if pins == "only_dataset":
        pins = {DS: env.pin(DS)}
    code, report = env.run(*flags, extra=extra, pins=pins)
    assert code == 3 and env.connect_calls == 0 and any(fragment in r for r in report["verdict"]["reasons"]), report["verdict"]


def test_p2_a_duplicate_pin_for_the_same_version_is_refused(env):
    code, report = env.run("--run", extra=("--expected-config-sha256", f"{DS}={env.pin(DS)}"))
    assert code == 3 and env.connect_calls == 0 and any("khong hop le/trung" in r for r in report["verdict"]["reasons"])


def test_p2_the_report_records_the_pins_and_the_step0_pin_check(env):
    code, report = env.run("--run", *SNAP)
    assert code == 0 and report["expected_config_sha256s"] == {DS: env.pin(DS), DS2: env.pin(DS2)} and report["step0"]["checks"]["build_config_sha256_pin"] is True
    assert report["snapshot"]["versions"][DS]["canonical_config_sha256"] == env.pin(DS)


def test_p3_a_changed_repair_parity_dependency_is_refused_before_connecting(env, monkeypatch):
    monkeypatch.setattr(fl, "PINNED_REPAIR_PARITY_SHA256", "0" * 64)
    code, report = env.run("--run")
    assert code == 3 and env.connect_calls == 0 and any("repair_parity_v1.py" in r for r in report["verdict"]["reasons"]) and env.conn.log == []


def test_p3_the_dependencies_are_loaded_from_the_hashed_bytes_and_the_report_binds_both_hashes(env, tmp_path, monkeypatch):
    assert rp.__source_sha256__ == hashlib.sha256((TOOLS / "repair_parity_v1.py").read_bytes()).hexdigest() == fl.PINNED_REPAIR_PARITY_SHA256
    assert wa.__source_sha256__ == fl.PINNED_BASE_CONTROLLER_SHA256
    _, report = env.run("--run")
    assert report["repair_parity_sha256"] == report["repair_parity_source_sha256"] == rp.__source_sha256__ and report["base_source_sha256"] == wa.__source_sha256__
    source = tmp_path / "mod.py"
    source.write_bytes(b"VALUE = 41 + 1\r\n")
    monkeypatch.setattr(fl, "HERE", tmp_path)
    module = fl._load("m_for_test", "mod.py")
    assert module.VALUE == 42 and module.__source_sha256__ == hashlib.sha256(b"VALUE = 41 + 1\r\n").hexdigest() and module.__file__ == str(source)


def test_p3_a_modified_base_controller_source_hash_is_refused_even_if_the_pin_were_bypassed(env, monkeypatch):
    monkeypatch.setattr(wa, "__source_sha256__", "0" * 64)
    code, report = env.run("--run")
    assert code == 3 and env.connect_calls == 0 and any("controller nen" in r for r in report["verdict"]["reasons"])


# =========================================================================== tich hop: bao cao producer THAT => check_provenance cua cong tong (khong viet tay hinh dang JSON)
def test_real_producer_reports_for_the_old_and_new_datasets_satisfy_the_gate_provenance_check(env, tmp_path):
    import pyarrow as pa
    import pyarrow.parquet as pq
    new_version, world = "ds_20261009_nf1", env.world
    world.db.execute("INSERT INTO ml_samples (dataset_version, record_id, ml_reference_assignment_id, is_daily_snapshot_selected) "
                     "SELECT ?, record_id, ml_reference_assignment_id + 1000, 1 FROM ml_samples WHERE dataset_version = ? AND is_daily_snapshot_selected = 1", (new_version, DS))
    world.db.execute("INSERT INTO ml_reference_assignments SELECT id + 1000, ?, hotel_id, checkin_date, canonical_series_id, free_cancellation, breakfast_included, max_occupancy "
                     "FROM ml_reference_assignments WHERE dataset_version = ?", (new_version, DS))
    new_dir = tmp_path / "ds_new"
    new_dir.mkdir()
    ids = sorted(world.identity)
    pq.write_table(pa.table({"warehouse_record_id": pa.array(ids, pa.int64()), "hotel_id": [world.identity[i][0] for i in ids],
                             "checkin_date": pa.array([dt.date.fromisoformat(world.identity[i][1]) for i in ids], pa.date32()),
                             "canonical_series_id": [world.identity[i][2] for i in ids], "other": [2.0] * len(ids)}), new_dir / "samples.parquet")
    new_sha = hashlib.sha256((new_dir / "samples.parquet").read_bytes()).hexdigest()
    new_config = make_config(tag="n")
    world.configs[new_version] = new_config
    world.insert_manifest(new_version, new_sha, new_config)
    code_old, old_report = env.run("--run", *SNAP)
    code_new, new_report = env.run("--run", dataset=new_version, sha=new_sha, dataset_dir=new_dir)
    assert code_old == 0 and code_new == 0 and old_report["verdict"]["overall"] == new_report["verdict"]["overall"] == "LEDGER_PASS"
    old_code = world.configs[DS]["builder_code"]["code_sha256"]
    docs = {"old_ledger_report": old_report, "new_ledger_report": new_report,
            "config_prediction": rp.prediction_receipt({"ok": True, "violations": [], "old_config_sha256": env.pin(DS), "predicted_config_sha256": env.pin(new_version), "old_code_sha256": old_code,
                                                       "new_code_sha256": "c" * 64, "changed_files": []}),
            "old_config_snapshot": json.loads(Path(old_report["snapshot"]["file"]["path"]).read_text(encoding="utf-8"))}
    paths = {}
    for name, doc in docs.items():
        paths[name] = tmp_path / f"{name}.json"
        paths[name].write_text(json.dumps(doc), encoding="utf-8")
    paths["old_config_snapshot"] = Path(old_report["snapshot"]["file"]["path"])                           # CHINH tep snapshot do producer ghi (sha khop snapshot.file.sha256)
    kwargs = dict(old_version=DS, new_version=new_version, old_samples_sha256=env.world.samples_sha, new_samples_sha256=new_sha, old_ledger_sha256=old_report["ledger"]["file"]["sha256"],
                  new_ledger_sha256=new_report["ledger"]["file"]["sha256"], expected_code_sha256="c" * 64, expected_config_sha256=env.pin(new_version), expected_old_config_sha256=env.pin(DS), expected_old_code_sha256=old_code,
                  expected_producer_script_sha256=fl.script_sha256(), expected_producer_sql_set_sha256=fl.sql_set_sha256(), expected_base_controller_sha256=wa.__source_sha256__,
                  tool_sha256=rp.__source_sha256__)
    violations, summary = rp.check_provenance(paths, **kwargs)
    assert violations == [] and set(summary) == set(rp.PROVENANCE_KEYS)
    unapproved = dict(kwargs, expected_producer_script_sha256="0" * 64)                                   # bao cao THAT cua producer, nhung tuple 'da duyet' khac => bi tu choi o CA HAI bao cao
    bad = rp.check_provenance(paths, **unapproved)[0]
    assert sum("producer_script_is_approved" in v for v in bad) == 2
    # nguoc lai: dung bao cao CU lam bao cao MOI, hoac doi tep snapshot => bi bat
    swapped = dict(paths, new_ledger_report=paths["old_ledger_report"])
    assert any("provenance_new_ledger_report_mismatch" in v for v in rp.check_provenance(swapped, **kwargs)[0])
    other_snapshot = tmp_path / "other_snapshot.json"
    other_snapshot.write_text(Path(paths["old_config_snapshot"]).read_text(encoding="utf-8") + " ", encoding="utf-8")
    assert any("snapshot_file_sha_is_provided" in v for v in rp.check_provenance(dict(paths, old_config_snapshot=other_snapshot), **kwargs)[0])


def test_p1_publication_depends_on_the_single_overall_decision_not_only_on_the_ledger_component(env, monkeypatch):
    real = fl.summarize
    monkeypatch.setattr(fl, "summarize", lambda report: {"overall": "UNVERIFIED", "exit_code": wa.EXIT_CODES["UNVERIFIED"], "reasons": ["forced by test"]})
    code, report = env.run("--run")
    assert report["ledger"]["status"] == "PASS" and report["verdict"]["overall"] == "UNVERIFIED" and code == wa.EXIT_CODES["UNVERIFIED"]      # thanh phan ledger PASS nhung phase KHONG PASS
    assert _ledger_files(env) == [] and "file" not in report["ledger"]
    monkeypatch.setattr(fl, "summarize", real)
    assert env.run("--run")[0] == 0 and len(_ledger_files(env)) == 1


def test_p3_a_base_controller_file_that_changes_on_disk_after_loading_is_refused(env, monkeypatch):
    monkeypatch.setattr(wa, "controller_sha256", lambda: "0" * 64)                                       # hash luc chay != hash luc nap (tep bi doi giua chung)
    code, report = env.run("--run")
    assert code == 3 and env.connect_calls == 0 and any("controller nen" in r for r in report["verdict"]["reasons"])

"""audit_tools/repair_db_observer_v1.py — observer DB chi-doc tren MAY CHU GIA (khong ket noi that): cap SQL co dinh, baseline/probe/self-test, tich hop that voi runner + mo hinh quiescence."""
from __future__ import annotations

import hashlib
import importlib.util
import json
import os
import re
from pathlib import Path

import pytest

TOOLS = Path(__file__).resolve().parents[2] / "audit_tools"


def _load(name, filename):
    spec = importlib.util.spec_from_file_location(name, TOOLS / filename)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


ob = _load("repair_db_observer_v1", "repair_db_observer_v1.py")
qm = ob.qm
APP = "hotel_app"


class FakeServer:
    """May chu gia: moi ket noi la mot phien; tra loi DUNG cac cau SQL cua observer (so sanh nguyen van)."""

    def __init__(self):
        self.next_id = 2000
        self.sessions = {}                   # id -> {user, db, pid}
        self.uptime = 5000
        self.uuid = "uuid-fake-1"
        self.databases = ["hotel_price_intel", "warehouse_20261004_3src", "warehouse_dsdev_20261004_3src"]
        self.read_only_sessions = set()
        self.rolling = []
        self.hide_pid = False
        self.trx_unreadable = False
        self.close_lag = 0                   # so lan quet SQL_SESSIONS truoc khi phien dong thuc su bien mat
        self.log = []
        self.pending_close = {}
        self.add_session(user="other", db="hotel_price_intel", pid=1)      # phien co san (crawler/khac)

    def add_session(self, user=APP, db=None, pid=None):
        self.next_id += 1
        self.sessions[self.next_id] = {"user": user, "db": db, "pid": os.getpid() if pid is None else pid}
        return self.next_id

    def connect(self, user=APP, db=None):
        return FakeConn(self, self.add_session(user=user, db=db))

    def answer(self, session_id, sql, params):
        self.log.append(sql)
        if sql == ob.SQL_CONNECTION_INFO:
            return [{"probe_session_id": session_id, "app_user": self.sessions[session_id]["user"]}]
        if sql == ob.SQL_SERVER_IDENTITY:
            return [{"server_uuid": self.uuid, "version": "8.0.99"}]
        if sql == ob.SQL_DATABASES:
            return [{"schema_name": name} for name in self.databases][: ob.DATABASES_LIMIT + 1]
        if sql == ob.SQL_SESSION_SANITY:
            return [{"ro": 1 if session_id in self.read_only_sessions else 0, "tz": "+00:00"}]
        if sql == "SET SESSION TRANSACTION READ ONLY":
            self.read_only_sessions.add(session_id)
            return []
        if sql == ob.SQL_UPTIME:
            return [{"uptime_s": self.uptime}]
        if sql == ob.SQL_MAX_THREAD_ID:
            return [{"max_id": max(self.sessions)}]
        if sql == ob.SQL_SESSIONS:
            for sid in list(self.pending_close):
                self.pending_close[sid] -= 1
                if self.pending_close[sid] <= 0:
                    self.sessions.pop(sid, None)
                    del self.pending_close[sid]
            rows = [{"PROCESSLIST_ID": sid, "PROCESSLIST_USER": s["user"], "PROCESSLIST_DB": s["db"], "pid": None if self.hide_pid else str(s["pid"])}
                    for sid, s in sorted(self.sessions.items()) if sid > params[0]]
            return rows[:ob.PROBE_LIMIT]
        if sql == ob.SQL_ROLLING_BACK:
            if self.trx_unreadable:
                raise PermissionError("PROCESS privilege required")
            return list(self.rolling)
        raise AssertionError(f"SQL la: {sql[:80]}")


class FakeCursor:
    def __init__(self, conn):
        self.conn, self.rows = conn, []

    def execute(self, sql, params=()):
        self.rows = self.conn.server.answer(self.conn.session_id, sql, tuple(params))

    def fetchall(self):
        return self.rows

    def close(self):
        pass


class FakeConn:
    def __init__(self, server, session_id):
        self.server, self.session_id, self.closed = server, session_id, False

    def cursor(self, dictionary=False):
        return FakeCursor(self)

    def close(self):
        self.closed = True
        if self.server.close_lag:
            self.server.pending_close[self.session_id] = self.server.close_lag
        else:
            self.server.sessions.pop(self.session_id, None)


def observer(server, **kw):
    return ob.DbObserver(lambda: server.connect(), **kw)


# =========================================================================== SQL co dinh
def test_the_sql_set_is_eight_single_read_only_selects_with_a_time_limit_and_no_foreign_sql_text():
    statements = ob.sql_statements()
    assert set(statements) == {"SQL_CONNECTION_INFO", "SQL_SERVER_IDENTITY", "SQL_UPTIME", "SQL_MAX_THREAD_ID", "SQL_SESSIONS", "SQL_ROLLING_BACK", "SQL_DATABASES", "SQL_SESSION_SANITY"}
    assert ob.AUX_SET_STATEMENTS == ("SET SESSION TRANSACTION READ ONLY",)
    for name, sql in statements.items():
        assert sql.startswith("SELECT /*+ MAX_EXECUTION_TIME(5000) */ ") and ";" not in sql, name
        assert not re.search(r"\b(INSERT|UPDATE|DELETE|DROP|ALTER|CREATE|TRUNCATE|REPLACE|INTO|CALL|LOCK|KILL|SET)\b", sql, re.IGNORECASE), name
        assert "PROCESSLIST_INFO" not in sql and "INFO" not in re.sub(r"CONNECTION_INFO", "", sql.replace("information_schema", "")).upper().replace("INFORMATION", ""), name
    assert "LIMIT 50" in statements["SQL_SESSIONS"] and "LIMIT 50" in statements["SQL_ROLLING_BACK"] and "%s" in statements["SQL_SESSIONS"] and statements["SQL_SESSIONS"].count("%s") == 1
    assert statements["SQL_DATABASES"].endswith("LIMIT 201") and "'information_schema','mysql','performance_schema','sys'" in statements["SQL_DATABASES"]
    assert len(ob.sql_set_sha256()) == 64 and len(ob.script_sha256()) == 64


def test_the_sql_set_hash_is_recomputed_from_every_statement_and_the_auxiliary_set_and_moves_with_either(monkeypatch):
    body = "\n".join(f"{k}={v}" for k, v in sorted(ob.sql_statements().items())) + "\nAUX=" + "|".join(ob.AUX_SET_STATEMENTS)
    assert ob.sql_set_sha256() == hashlib.sha256(body.encode("utf-8")).hexdigest()
    base = ob.sql_set_sha256()
    monkeypatch.setattr(ob, "AUX_SET_STATEMENTS", ("SET SESSION TRANSACTION READ WRITE",))
    assert ob.sql_set_sha256() != base                                                                          # lenh SET phu cua adapter nam trong hash can duyet
    monkeypatch.undo()
    monkeypatch.setattr(ob, "SQL_UPTIME", ob.SQL_UPTIME + " ")
    assert ob.sql_set_sha256() != base


def test_the_in_process_tooling_hash_binds_the_observer_script_and_the_quiescence_model(monkeypatch):
    expected = hashlib.sha256(json.dumps({"observer": ob.script_sha256(), "quiescence_model": qm.__source_sha256__}, sort_keys=True).encode("utf-8")).hexdigest()
    assert observer(FakeServer()).tooling_sha256 == expected
    monkeypatch.setattr(qm, "__source_sha256__", "f" * 64)
    assert observer(FakeServer()).tooling_sha256 != expected


# =========================================================================== baseline / probe
def test_baseline_reads_the_max_thread_id_the_uptime_the_own_session_and_the_user():
    server = FakeServer()
    o = observer(server)
    baseline = o.capture_baseline()
    assert baseline == {"baseline_thread_id": max(server.sessions), "baseline_uptime_s": 5000.0, "probe_session_id": max(server.sessions), "app_user": APP, "server_uuid": "uuid-fake-1"} and o.statements == 4


@pytest.mark.parametrize("breakage", ["no_rows", "two_rows", "non_int_uptime", "empty_user", "empty_uuid"])
def test_a_malformed_server_answer_makes_the_baseline_raise_instead_of_guessing(breakage):
    server = FakeServer()
    original = server.answer

    def answer(sid, sql, params):
        rows = original(sid, sql, params)
        if breakage == "no_rows" and sql == ob.SQL_UPTIME:
            return []
        if breakage == "two_rows" and sql == ob.SQL_MAX_THREAD_ID:
            return rows + rows
        if breakage == "non_int_uptime" and sql == ob.SQL_UPTIME:
            return [{"uptime_s": "12x"}]
        if breakage == "empty_user" and sql == ob.SQL_CONNECTION_INFO:
            return [{"probe_session_id": sid, "app_user": ""}]
        if breakage == "empty_uuid" and sql == ob.SQL_SERVER_IDENTITY:
            return [{"server_uuid": "", "version": "x"}]
        return rows
    server.answer = answer
    with pytest.raises(ValueError):
        observer(server).capture_baseline()


def test_probe_returns_only_sessions_after_the_baseline_with_normalized_types_and_no_foreign_sql_text():
    server = FakeServer()
    o = observer(server)
    o.capture_baseline()
    assert o.probe()["rows"] == [] and o.probe()["rolling_back"] == []
    new_id = server.add_session(user=APP, db=None, pid=4242)
    server.add_session(user="crawler", db="hotel_price_intel", pid=77)
    server.rolling = [{"trx_mysql_thread_id": new_id, "trx_state": "ROLLING BACK"}]
    probe = o.probe()
    assert probe["status"] == "ok" and probe["truncated"] is False and probe["uptime_s"] == 5000.0
    assert [r["PROCESSLIST_ID"] for r in probe["rows"]] == [new_id, new_id + 1] and probe["rows"][0] == {"PROCESSLIST_ID": new_id, "PROCESSLIST_USER": APP, "PROCESSLIST_DB": None, "pid": 4242}
    assert probe["rolling_back"] == [{"trx_mysql_thread_id": new_id, "trx_state": "ROLLING BACK"}]
    assert all(set(r) == {"PROCESSLIST_ID", "PROCESSLIST_USER", "PROCESSLIST_DB", "pid"} for r in probe["rows"])


def test_probe_before_baseline_is_an_error_and_a_non_numeric_pid_becomes_none_so_rule_a_still_applies():
    server = FakeServer()
    o = observer(server)
    with pytest.raises(RuntimeError, match="probe_before_baseline"):
        o.probe()
    o.capture_baseline()
    server.add_session(pid=4242)
    server.hide_pid = True
    assert o.probe()["rows"][0]["pid"] is None
    assert ob.normalize_session_row({"PROCESSLIST_ID": 5, "PROCESSLIST_USER": None, "PROCESSLIST_DB": None, "pid": "12x"})["pid"] is None
    assert ob.normalize_session_row({"PROCESSLIST_ID": 5, "PROCESSLIST_USER": None, "PROCESSLIST_DB": None, "pid": True})["pid"] is None
    with pytest.raises(ValueError):
        ob.normalize_session_row({"PROCESSLIST_ID": "x", "PROCESSLIST_USER": None, "PROCESSLIST_DB": None, "pid": None})


def test_probe_marks_truncation_when_the_limit_is_reached():
    server = FakeServer()
    o = observer(server)
    o.capture_baseline()
    for _ in range(ob.PROBE_LIMIT + 5):
        server.add_session()
    probe = o.probe()
    assert len(probe["rows"]) == ob.PROBE_LIMIT and probe["truncated"] is True


def test_the_statement_budget_stops_the_observer_with_a_specific_error():
    server = FakeServer()
    o = observer(server, max_statements=5)
    o.capture_baseline()                                                                                   # 4 lenh
    with pytest.raises(RuntimeError, match="observer_statement_budget_exhausted"):
        o.probe()                                                                                          # can 3 lenh nua


def test_database_errors_propagate_so_the_runner_records_an_error_probe_never_a_fabricated_clear():
    server = FakeServer()
    o = observer(server)
    o.capture_baseline()
    server.trx_unreadable = True
    with pytest.raises(PermissionError):
        o.probe()


# =========================================================================== self-test Q0
def test_selftest_on_a_healthy_server_proves_every_required_property_and_carries_the_details():
    server = FakeServer()
    result = observer(server).selftest(sleep=lambda s: None)
    assert all(result[k] is True for k in qm.SELFTEST_KEYS) and result["details"]["ids_increase"] is True and len(result["details"]["successive_ids"]) == 2 and result["details"]["errors"] == []
    assert len(server.sessions) == 2                                                                       # chi con phien probe + phien co san: hai phien phu da dong


@pytest.mark.parametrize("setup,failed", [
    (lambda s: setattr(s, "hide_pid", True), "pid_attribute_visible"),
    (lambda s: setattr(s, "trx_unreadable", True), "innodb_trx_readable"),
])
def test_each_missing_capability_is_reported_false_and_never_assumed_true(setup, failed):
    server = FakeServer()
    setup(server)
    result = observer(server).selftest(sleep=lambda s: None)
    assert result[failed] is False and all(result[k] is True for k in qm.SELFTEST_KEYS if k != failed)


def test_a_session_that_does_not_disappear_after_close_within_the_wait_is_reported_false():
    server = FakeServer()
    server.close_lag = 10 ** 6
    ticks = {"t": 0.0}
    result = observer(server).selftest(wait_gone_s=3.0, poll_s=1.0, sleep=lambda s: ticks.update(t=ticks["t"] + s), monotonic=lambda: ticks["t"])
    assert result["removed_after_close"] is False and result["visible_other_session"] is True


def test_a_session_with_a_lag_that_disappears_during_the_wait_is_reported_true():
    server = FakeServer()
    server.close_lag = 3
    ticks = {"t": 0.0}
    result = observer(server).selftest(wait_gone_s=10.0, poll_s=1.0, sleep=lambda s: ticks.update(t=ticks["t"] + s), monotonic=lambda: ticks["t"])
    assert result["removed_after_close"] is True


def test_a_second_connection_with_a_default_database_is_not_counted_as_a_db_null_session():
    server = FakeServer()
    o = ob.DbObserver(lambda: server.connect(db="warehouse_x"))
    result = o.selftest(sleep=lambda s: None)
    assert result["visible_db_null_session"] is False and result["visible_other_session"] is True


def test_a_user_mismatch_between_probe_and_second_connection_is_not_visibility_of_the_same_account():
    server = FakeServer()
    calls = {"n": 0}

    def connect():
        calls["n"] += 1
        return server.connect(user=APP if calls["n"] == 1 else "someone_else")
    result = ob.DbObserver(connect).selftest(sleep=lambda s: None)
    assert result["visible_other_session"] is False


def test_the_selftest_never_leaves_extra_connections_open_even_when_a_step_fails():
    server = FakeServer()
    created = []
    original = server.connect

    def tracking(**kw):
        conn = original(**kw)
        created.append(conn)
        return conn
    server.connect = tracking
    server.trx_unreadable = True
    ob.DbObserver(lambda: server.connect()).selftest(sleep=lambda s: None)
    assert len(created) == 3 and sum(1 for c in created if c.closed) == 2                                  # probe (giu mo) + hai phien phu da dong


# =========================================================================== Q0 receipt: observer GHI -> runner v2 DOC (hinh dang that, khong viet tay)
import datetime as dt
import json

rr = _load("repair_phase_runner_v2_for_observer", "repair_phase_runner_v2.py")
NOW = dt.datetime(2026, 10, 9, 11, 0, tzinfo=dt.timezone.utc)                                              # 18:00 VN


def q0_for(tmp_path, server, **kw):
    result = observer(server).selftest(sleep=lambda s: None)
    path = tmp_path / f"q0_{len(list(tmp_path.glob('q0_*.json')))}.json"
    sha = ob.write_q0_receipt(result, path, now_utc=NOW - dt.timedelta(minutes=5))
    return path, sha, result


def load(path, sha, **over):
    kwargs = dict(expected_receipt_sha256=sha, expected_observer_sha256=ob.script_sha256(), expected_sql_set_sha256=ob.sql_set_sha256(), expected_model_sha256=qm.__source_sha256__, now_utc=NOW, max_age_s=3600.0)
    kwargs.update(over)
    return rr.load_q0_receipt(path, **kwargs)


def test_the_q0_receipt_written_by_the_observer_is_loaded_and_verified_by_the_runner(tmp_path):
    server = FakeServer()
    path, sha, result = q0_for(tmp_path, server)
    doc = json.loads(path.read_text(encoding="utf-8"))
    assert sha == rr.file_sha256(path) and doc["kind"] == "q0_selftest_receipt" and doc["server"]["server_uuid"] == "uuid-fake-1" and doc["server"]["uptime_s"] == 5000.0
    assert doc["observer_script_sha256"] == ob.script_sha256() and doc["sql_set_sha256"] == ob.sql_set_sha256() and all(doc["selftest"][k] is True for k in qm.SELFTEST_KEYS)
    loaded = load(path, sha)
    assert loaded["verified"] is True and loaded["receipt_sha256"] == sha
    for pin in ("expected_observer_sha256", "expected_sql_set_sha256", "expected_model_sha256"):
        with pytest.raises(ValueError, match="q0_receipt_invalid"):
            load(path, sha, **{pin: "0" * 64})


def test_a_q0_receipt_is_never_written_when_a_selftest_key_is_not_true_and_never_overwritten(tmp_path):
    server = FakeServer()
    server.hide_pid = True
    result = observer(server).selftest(sleep=lambda s: None)
    with pytest.raises(ValueError, match="q0_selftest_not_all_true"):
        ob.write_q0_receipt(result, tmp_path / "q0.json", now_utc=NOW)
    assert not (tmp_path / "q0.json").exists()
    server2 = FakeServer()
    good = observer(server2).selftest(sleep=lambda s: None)
    ob.write_q0_receipt(good, tmp_path / "q0.json", now_utc=NOW)
    with pytest.raises(FileExistsError):
        ob.write_q0_receipt(good, tmp_path / "q0.json", now_utc=NOW)


class Clock:
    def __init__(self):
        self.t = 1000.0

    def monotonic(self):
        return self.t

    def sleep(self, seconds):
        self.t += seconds

    def now_utc(self):
        return NOW + dt.timedelta(seconds=self.t - 1000.0)


class PhaseLauncher:
    """Mo phong child cua phase: mo mot phien DB=NULL (pid thuoc cay) trong luc chay; `linger` = so lan quet truoc khi phien do het."""
    source_sha256 = "j" * 64

    def __init__(self, clock, server, linger):
        self.clock, self.server, self.linger, self.calls = clock, server, linger, []

    def launch(self, spec, cap_s, should_abort=None, gate_poll_s=5.0):
        self.calls.append(spec["name"])
        sid = self.server.add_session(user=APP, db=None, pid=4242)
        if self.linger:
            self.server.pending_close[sid] = self.linger
        else:
            self.server.sessions.pop(sid)
        self.clock.t += 5
        return {"exit_code": 0, "timed_out": False, "aborted_by_gate": None, "gate_polls": 0, "killed_tree": False, "contained": True, "pids_seen": [4242],
                "stdout_path": "x", "stderr_path": "y", "stdout_sha256": "a" * 64, "stderr_sha256": "b" * 64, "started": 0.0, "ended": 1.0}


def integration_runner(tmp_path, server, linger):
    script = tmp_path / "work.py"
    script.write_text("print(1)\n", encoding="utf-8")
    plan = tmp_path / "plan"
    plan.mkdir()
    q0_path, q0_sha, _ = q0_for(tmp_path, server)
    q0 = load(q0_path, q0_sha)
    server.uptime += 100                                                                                      # thoi gian troi qua tu Q0
    spec = {"name": "ph_w", "kind": "write", "argv": ["x"], "cwd": str(tmp_path), "env": {"SystemRoot": "C:\\Windows"}, "required_files": {str(script): rr.file_sha256(script)}, "deadline_s": 60,
            "db": {"app_user": APP, "db_scope": ["warehouse_dsdev_nf"], "protected_dbs": ["hotel_price_intel"]}}
    clock = Clock()
    launcher = PhaseLauncher(clock, server, linger)
    settings = {**rr.DEFAULT_SETTINGS, "window_end_vn": None}
    observer_ = observer(server)
    tooling = rr.tooling_for(launcher, observer_)
    runner = rr.Runner(plan_dir=plan, specs=[spec], settings=settings, expected_tooling=tooling, q0_receipt_sha256=q0_sha, approved_plan_sha256=rr.plan_sha256([spec], settings, tooling, q0_sha),
                       launcher=launcher, clock=clock, now_utc=clock.now_utc, gate=lambda: (True, []), observer=observer_, q0=q0)
    return runner, observer_


def test_end_to_end_a_lingering_phase_session_that_vanishes_before_the_clean_pair_still_passes(tmp_path):
    server = FakeServer()
    runner, observer_ = integration_runner(tmp_path, server, linger=2)
    receipt = runner.run_plan()[0]
    assert receipt["verdict"] == rr.PASS and receipt["quiescence"]["status"] == qm.VERIFIED and receipt["quiescence"]["probe_count"] == 3
    assert receipt["baseline"]["app_user"] == APP and receipt["baseline"]["server_uuid"] == "uuid-fake-1" and receipt["q0_receipt_sha256"] == runner.q0["receipt_sha256"]


def test_end_to_end_a_phase_session_that_never_goes_away_quarantines_with_the_candidate_recorded_not_killed(tmp_path):
    server = FakeServer()
    runner, observer_ = integration_runner(tmp_path, server, linger=10 ** 6)
    receipt = runner.run_plan()[0]
    assert receipt["verdict"] == rr.QUARANTINE and receipt["quiescence"]["status"] == qm.NOT_QUIESCENT and len(receipt["quiescence"]["kill_candidates"]) == 1
    assert not any("KILL" in sql.upper() for sql in server.log) and (tmp_path / "plan" / "QUARANTINE.json").exists()


def test_end_to_end_a_clean_phase_passes_with_two_probes_and_only_select_statements_were_issued(tmp_path):
    server = FakeServer()
    runner, observer_ = integration_runner(tmp_path, server, linger=0)
    receipt = runner.run_plan()[0]
    assert receipt["verdict"] == rr.PASS and receipt["quiescence"]["probe_count"] == 2
    assert all(sql.startswith("SELECT /*+ MAX_EXECUTION_TIME(5000) */ ") for sql in server.log)


def test_end_to_end_a_restart_of_the_server_between_baseline_and_probe_is_unknown_and_quarantines(tmp_path):
    server = FakeServer()
    runner, observer_ = integration_runner(tmp_path, server, linger=0)
    original = runner.launcher.launch

    def restart(spec_, cap, **kw):
        result = original(spec_, cap, **kw)
        server.uptime = 7                                                                                  # server khoi dong lai trong phase
        return result
    runner.launcher.launch = restart
    receipt = runner.run_plan()[0]
    assert receipt["verdict"] == rr.QUARANTINE and receipt["quiescence"]["status"] == qm.UNKNOWN and "server_lifecycle_unverifiable" in " ".join(receipt["quiescence"]["reasons"])


def test_end_to_end_a_different_server_than_the_one_the_q0_was_issued_for_is_refused_before_launch(tmp_path):
    server = FakeServer()
    runner, observer_ = integration_runner(tmp_path, server, linger=0)
    server.uuid = "uuid-OTHER-SERVER"
    receipts = runner.run_plan()
    assert receipts[0]["verdict"] == rr.REFUSED and "server_identity_differs_from_q0" in receipts[0]["reasons"][0] and runner.launcher.calls == []


# =========================================================================== bo sung sau luot dot bien dau
def test_the_two_list_statements_have_exactly_limit_50_not_just_a_prefix_of_it():
    for name in ("SQL_SESSIONS", "SQL_ROLLING_BACK"):
        assert re.search(r"LIMIT 50$", ob.sql_statements()[name]), name
    assert ob.PROBE_LIMIT == 50


@pytest.mark.parametrize("bad", [True, 5.5, "-5", " 7", "7.0", None, ""])
def test_integer_fields_from_the_server_are_validated_strictly(bad):
    server = FakeServer()
    original = server.answer

    def answer(sid, sql, params):
        if sql == ob.SQL_UPTIME:
            return [{"uptime_s": bad}]
        return original(sid, sql, params)
    server.answer = answer
    with pytest.raises(ValueError):
        observer(server).capture_baseline()


# =========================================================================== GPT 74: S (connection-id sanity phai chan Q0), danh sach DB, adapter chi-doc, kiem ke lenh
def test_selftest_records_the_database_list_the_app_user_and_a_true_ids_sanity_on_a_healthy_server():
    result = observer(FakeServer()).selftest(sleep=lambda s: None)
    d = result["details"]
    assert d["databases"] == ["hotel_price_intel", "warehouse_20261004_3src", "warehouse_dsdev_20261004_3src"] and d["databases_truncated"] is False and d["app_user"] == APP
    assert d["ids_increase"] is True and len(d["successive_ids"]) == 2 and d["successive_ids"][0] < d["successive_ids"][1]


def test_a_false_connection_id_sanity_is_never_written_as_an_accepted_q0_receipt(tmp_path):
    server = FakeServer()
    original = server.answer
    seen = []

    def answer(sid, sql, params):
        rows = original(sid, sql, params)
        if sql == ob.SQL_CONNECTION_INFO:
            seen.append(rows[0]["probe_session_id"])
            if len(seen) == 3:                                                                                  # lan thu 3 (phien phu C) bao CUNG id voi phien phu B
                return [{"probe_session_id": seen[1], "app_user": rows[0]["app_user"]}]
        return rows
    server.answer = answer
    result = observer(server).selftest(sleep=lambda s: None)
    assert result["details"]["ids_increase"] is False and all(result[k] is True for k in qm.SELFTEST_KEYS)    # nam khoa van True (nhu GPT 74 do) ...
    with pytest.raises(ValueError, match="q0_connection_id_sanity_false"):
        ob.write_q0_receipt(result, tmp_path / "q0.json", now_utc=NOW)                                         # ... nhung receipt KHONG duoc ghi
    assert not (tmp_path / "q0.json").exists()


@pytest.mark.parametrize("name,mutate,match", [
    ("ids_missing", lambda d: d.pop("successive_ids"), "q0_connection_id_sanity_false"), ("ids_wrong_type", lambda d: d.update(successive_ids=[1.0, 2.0]), "q0_connection_id_sanity_false"),
    ("ids_decreasing", lambda d: d.update(successive_ids=[5, 4]), "q0_connection_id_sanity_false"), ("ids_zero", lambda d: d.update(successive_ids=[0, 3]), "q0_connection_id_sanity_false"),
    ("ids_three", lambda d: d.update(successive_ids=[1, 2, 3]), "q0_connection_id_sanity_false"), ("flag_missing", lambda d: d.pop("ids_increase"), "q0_connection_id_sanity_false"),
    ("flag_truthy", lambda d: d.update(ids_increase=1), "q0_connection_id_sanity_false"), ("databases_truncated", lambda d: d.update(databases_truncated=True), "q0_database_list_missing_or_truncated"),
    ("databases_empty", lambda d: d.update(databases=[]), "q0_database_list_missing_or_truncated"), ("databases_missing", lambda d: d.pop("databases"), "q0_database_list_missing_or_truncated"),
    ("app_user_empty", lambda d: d.update(app_user=""), "q0_app_user_missing"), ("app_user_missing", lambda d: d.pop("app_user"), "q0_app_user_missing"),
])
def test_the_q0_writer_refuses_every_malformed_sanity_detail(tmp_path, name, mutate, match):
    result = observer(FakeServer()).selftest(sleep=lambda s: None)
    result = {**result, "details": dict(result["details"])}
    mutate(result["details"])
    with pytest.raises(ValueError, match=match):
        ob.write_q0_receipt(result, tmp_path / f"{name}.json", now_utc=NOW)
    assert not (tmp_path / f"{name}.json").exists()


def test_a_truncated_database_list_is_flagged(tmp_path):
    server = FakeServer()
    server.databases = [f"db_{i:03d}" for i in range(ob.DATABASES_LIMIT + 5)]
    result = observer(server).selftest(sleep=lambda s: None)
    assert result["details"]["databases_truncated"] is True and len(result["details"]["databases"]) == ob.DATABASES_LIMIT
    with pytest.raises(ValueError, match="q0_database_list_missing_or_truncated"):
        ob.write_q0_receipt(result, tmp_path / "q0.json", now_utc=NOW)


def test_the_readonly_adapter_sets_and_verifies_a_read_only_session_and_fails_closed_otherwise():
    server = FakeServer()
    created = []

    def connector_connect(**kwargs):
        conn = server.connect()
        created.append((kwargs, conn))
        return conn
    make = ob.readonly_connect_factory(connector_connect, lambda: {"host": "h", "time_zone": "+00:00", "autocommit": False})
    conn = make()
    assert created[0][0] == {"host": "h", "time_zone": "+00:00", "autocommit": False, "use_pure": True} and conn.session_id in server.read_only_sessions        # driver thuan Python duoc ghim trong ma
    overridden = ob.readonly_connect_factory(connector_connect, lambda: {"host": "h", "use_pure": False})
    overridden()
    assert created[-1][0]["use_pure"] is True                                                                    # kwargs cua backend khong the tat che do thuan
    assert server.log[-2:] == ["SET SESSION TRANSACTION READ ONLY", ob.SQL_SESSION_SANITY]

    class NotReadOnly(FakeServer):
        def answer(self, session_id, sql, params):
            if sql == "SET SESSION TRANSACTION READ ONLY":
                self.log.append(sql)
                return []                                                                                       # lenh 'bi nuot': phien khong chi-doc
            return super().answer(session_id, sql, params)
    bad = NotReadOnly()
    made = []

    def bad_connect(**kwargs):
        conn = bad.connect()
        made.append(conn)
        return conn
    with pytest.raises(RuntimeError, match="session_not_read_only"):
        ob.readonly_connect_factory(bad_connect, dict)()
    assert made[0].closed is True                                                                               # ket noi hong bi dong, khong ro ri


def test_every_statement_a_full_selftest_issues_is_in_the_catalogue_or_the_single_aux_set_and_the_counts_are_stated():
    server = FakeServer()
    make = ob.readonly_connect_factory(lambda **kw: server.connect(), dict)
    o = ob.DbObserver(make, max_statements=40)
    o.selftest(sleep=lambda s: None)
    catalogue = set(ob.sql_statements().values()) | set(ob.AUX_SET_STATEMENTS)
    assert set(server.log) <= catalogue
    observer_selects = o.statements
    aux = sum(1 for s in server.log if s in ob.AUX_SET_STATEMENTS) + sum(1 for s in server.log if s == ob.SQL_SESSION_SANITY)
    assert observer_selects == 9 and aux == 6                                                                   # 9 SELECT cua observer (3+1+1+1+1+1+1) + 3 phien x (1 SET + 1 SELECT sanity); them 3 SET/phien cua connector (khong mo phong o day)
    assert len(server.log) == observer_selects + aux

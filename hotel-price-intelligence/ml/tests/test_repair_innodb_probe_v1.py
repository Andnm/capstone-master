"""repair_innodb_probe_v1: MOT SELECT chi-doc hai bien GLOBAL InnoDB. Test voi ket noi/gate/timer GIA (khong DB, khong .env) + MOT test process THAT (child Python, khong DB) chung minh watchdog thoat 4 khi close treo."""
from __future__ import annotations

import hashlib
import importlib.util
import json
import subprocess
import sys
import textwrap
import time
from pathlib import Path

import pytest

PROBE = Path(r"D:\MSE\CAPSTONE\hotel-price-intelligence\audit_tools\repair_innodb_probe_v1.py")
SECRET = "S3cr3t-Pa55w0rd!"


def load():
    spec = importlib.util.spec_from_file_location("probe_under_test", PROBE)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


class GateLost(Exception):
    pass


class FakeGate:
    def __init__(self, fail_on=()):
        self.fail_on, self.calls = set(fail_on), 0
        self.checks, self.last_verdict, self.sha_chain, self.receipts = 0, "zero run/item queued|running; latest_run_id=66", ["a" * 64], [{"sha256": "a" * 64}]

    def check(self, force=False):
        self.calls += 1
        if self.calls in self.fail_on:
            raise GateLost(f"cong crawler: bang chung qua cu (call {self.calls})")
        self.checks += 1


class FakeCursor:
    def __init__(self, conn):
        self.conn = conn

    def execute(self, statement, params=None):
        self.conn.executed.append(statement)
        if statement == self.conn.mod_sql and self.conn.select_error:
            raise self.conn.select_error

    def fetchall(self):
        return self.conn.rows

    def close(self):
        pass


class FakeConn:
    def __init__(self, mod, rows=None, select_error=None, on_close=None):
        self.executed, self.mod_sql, self.select_error, self.on_close = [], mod.SQL, select_error, on_close
        self.rows = [(8 * 1024 ** 3, 4 * 1024 ** 3)] if rows is None else rows
        self.connection_id, self.closed, self.rolled_back = 4321, 0, 0

    def get_server_info(self):
        return "8.0.45"

    def cursor(self):
        return FakeCursor(self)

    def rollback(self):
        self.rolled_back += 1

    def close(self):
        self.closed += 1
        if self.on_close:
            self.on_close()


class FakeTimer:
    """Timer gia: khong tu chay; `fire()` mo phong het 30 s. `immediate=True` thi chay callback ngay khi start()."""

    def __init__(self, interval, callback, immediate=False):
        self.interval, self.callback, self.immediate = interval, callback, immediate
        self.daemon, self.started, self.cancelled = False, False, False

    def start(self):
        self.started = True
        if self.immediate:
            self.callback()

    def fire(self):
        if not self.cancelled:                                                                                   # timer da huy thi that su khong no
            self.callback()

    def cancel(self):
        self.cancelled = True


def run(tmp_path, *, conn=None, gate=None, connect_error=None, timer=None, out_name="probe.json", secrets=(SECRET,), mod=None, exits=None):
    mod = mod or load()
    conn = conn or FakeConn(mod)
    gate = gate or FakeGate()
    order = []
    exits = [] if exits is None else exits

    def connect_fn(secret_list):
        order.append(("connect", gate.calls))
        secret_list.extend(secrets)
        if connect_error:
            raise connect_error
        return conn
    timers = []

    def timer_factory(interval, callback):
        t = timer(interval, callback) if timer else FakeTimer(interval, callback)
        timers.append(t)
        return t
    out = tmp_path / out_name
    code = mod.main(["--crawl-evidence", str(tmp_path / "live.json"), "--out", str(out)], connect_fn=connect_fn, gate_factory=lambda path: (gate, GateLost), timer_factory=timer_factory, exit_fn=exits.append)
    report = json.loads(out.read_text(encoding="utf-8")) if out.exists() else None
    return mod, code, report, conn, gate, order, exits, timers, out


def test_success_runs_exactly_the_two_session_sets_then_one_select_and_records_bytes(tmp_path):
    mod, code, report, conn, gate, order, exits, timers, out = run(tmp_path)
    assert code == 0 and report["ok"] is True and report["errors"] == [] and exits == []
    assert conn.executed == ["SET SESSION TRANSACTION READ ONLY", "SET SESSION MAX_EXECUTION_TIME=5000", mod.SQL] and conn.rolled_back == 1 and conn.closed == 1
    assert report["values"] == {"buffer_pool_size_bytes": 8 * 1024 ** 3, "redo_log_capacity_bytes": 4 * 1024 ** 3} and report["units"] == "bytes"
    assert report["server"] == {"version": "8.0.45", "connection_id": 4321}
    assert report["source_sha256"] == hashlib.sha256(PROBE.read_bytes()).hexdigest() and report["sql_set_sha256"] == mod.sql_set_sha256()
    assert [s["sql"] for s in report["planned_inventory"]] == [*mod.SESSION_SQL, mod.SQL, "ROLLBACK"] and "statements" not in report
    assert [(a["sql"], a["status"]) for a in report["attempted"]] == [(mod.SESSION_SQL[0], "ok"), (mod.SESSION_SQL[1], "ok"), (mod.SQL, "ok"), ("ROLLBACK", "ok"), ("close", "ok")]
    assert report["limits"] == {"max_execution_ms": 5000, "connect_timeout_s": 10, "wall_max_s": 30.0}
    assert report["gate"]["checks"] == 2 and gate.calls == 2 and order == [("connect", 1)]                      # gate TRUOC khi connect (1 lan) va SAU truy van (lan 2)
    assert timers[0].interval == 30.0 and timers[0].started and timers[0].cancelled


def test_the_only_select_reads_exactly_two_global_innodb_variables_and_nothing_mutating_runs():
    mod = load()
    lowered = mod.SQL.lower()
    assert lowered.count("@@global.") == 2 and "innodb_buffer_pool_size" in lowered and "innodb_redo_log_capacity" in lowered and lowered.startswith("select ") and " from " not in lowered
    for statement in (*mod.SESSION_SQL, mod.SQL):
        assert not any(token in statement.lower() for token in ("set global", "set persist", "insert", "update", "delete", "drop", "create", "alter", "kill", "count("))
    import ast
    tree = ast.parse(PROBE.read_text(encoding="utf-8"))
    docstrings = {id(node.body[0].value) for node in ast.walk(tree) if isinstance(node, (ast.Module, ast.FunctionDef)) and node.body and isinstance(node.body[0], ast.Expr) and isinstance(node.body[0].value, ast.Constant)}
    strings = [n.value.lower() for n in ast.walk(tree) if isinstance(n, ast.Constant) and isinstance(n.value, str) and id(n) not in docstrings]
    assert not any("set global" in x or "set persist" in x for x in strings)
    imports = [n.module for n in ast.walk(tree) if isinstance(n, ast.ImportFrom)] + [a.name for n in ast.walk(tree) if isinstance(n, ast.Import) for a in n.names]
    assert "app.core.database" not in imports


def test_a_gate_refusal_before_connecting_never_opens_a_connection_and_reports_nothing_as_attempted(tmp_path):
    mod, code, report, conn, gate, order, exits, timers, out = run(tmp_path, gate=FakeGate(fail_on={1}))
    assert code == 3 and order == [] and conn.executed == [] and report["ok"] is False and report["values"] is None and "gate_refused" in report["errors"][0] and timers[0].cancelled
    assert report["attempted"] == [] and len(report["planned_inventory"]) == 4                                   # inventory co dinh KHONG bi hieu la lenh da chay


def test_a_connect_failure_records_no_statement_and_no_rollback_or_close(tmp_path):
    mod, code, report, conn, gate, order, exits, timers, out = run(tmp_path, connect_error=ConnectionError("refused"))
    assert code == 4 and report["attempted"] == [] and conn.closed == 0 and conn.rolled_back == 0


def test_a_gate_lost_after_the_query_makes_the_probe_unverified_not_ok(tmp_path):
    mod, code, report, conn, gate, order, exits, timers, out = run(tmp_path, gate=FakeGate(fail_on={2}))
    assert code == 4 and report["ok"] is False and "gate_lost_after_query" in report["errors"][0] and report["values"] is not None and conn.closed == 1


def test_an_existing_out_or_non_absolute_paths_are_refused_without_any_action(tmp_path):
    out = tmp_path / "probe.json"
    out.write_text("precious", encoding="utf-8")
    mod = load()
    called = []
    kw = dict(connect_fn=lambda s: called.append(1), gate_factory=lambda p: called.append(2), timer_factory=lambda *a: called.append(3))
    assert mod.main(["--crawl-evidence", str(tmp_path / "live.json"), "--out", str(out)], **kw) == 2 and out.read_text(encoding="utf-8") == "precious"
    assert mod.main(["--crawl-evidence", "live.json", "--out", str(tmp_path / "new.json")], **kw) == 2
    assert mod.main(["--crawl-evidence", str(tmp_path / "live.json"), "--out", "relative.json"], **kw) == 2
    assert mod.main(["--crawl-evidence", str(tmp_path / "live.json"), "--out", str(tmp_path / "missing_dir" / "x.json")], **kw) == 2
    assert called == []


def test_connection_and_query_errors_are_reported_in_full_but_secrets_are_redacted(tmp_path):
    mod, code, report, conn, gate, order, exits, timers, out = run(tmp_path, connect_error=ConnectionError(f"Access denied (using password: {SECRET}) for 'root'@'{SECRET}'"))
    text = out.read_text(encoding="utf-8")
    assert code == 4 and report["ok"] is False and SECRET not in text and "<redacted>" in text and "ConnectionError" in report["errors"][0]
    mod2 = load()
    failing = FakeConn(mod2, select_error=RuntimeError(f"boom {SECRET}"))
    mod2, code, report, conn, gate, order, exits, timers, out = run(tmp_path, conn=failing, out_name="second.json")
    assert code == 4 and SECRET not in out.read_text(encoding="utf-8") and failing.closed == 1 and report["values"] is None
    assert [(a["sql"], a["status"]) for a in report["attempted"]][-2:] == [(mod2.SQL, "error"), ("close", "ok")]       # SELECT loi: khong co ROLLBACK, close van chay


@pytest.mark.parametrize("rows", [[], [(1, 2), (3, 4)], [(1,)], [(None, 5)], [("8", 5)], [(0, 5)], [(5, -1)], [(True, 5)], [(1.5, 5)]])
def test_unexpected_result_shapes_or_non_positive_integer_values_are_rejected(tmp_path, rows):
    mod = load()
    mod, code, report, conn, gate, order, exits, timers, out = run(tmp_path, conn=FakeConn(mod, rows=rows))
    assert code == 4 and report["ok"] is False and report["values"] is None and conn.closed == 1


def test_a_keyboard_interrupt_during_the_query_is_recorded_then_re_raised_and_everything_is_closed(tmp_path):
    mod = load()
    failing = FakeConn(mod, select_error=KeyboardInterrupt())
    out = tmp_path / "ki.json"
    timers = []

    def tf(interval, callback):
        timers.append(FakeTimer(interval, callback))
        return timers[0]
    with pytest.raises(KeyboardInterrupt):
        mod.main(["--crawl-evidence", str(tmp_path / "live.json"), "--out", str(out)], connect_fn=lambda s: failing, gate_factory=lambda p: (FakeGate(), GateLost), timer_factory=tf, exit_fn=lambda c: None)
    report = json.loads(out.read_text(encoding="utf-8"))
    assert report["ok"] is False and "KeyboardInterrupt" in report["errors"][0] and failing.closed == 1 and timers[0].cancelled


def test_the_real_gate_class_refuses_a_missing_evidence_file(tmp_path):
    mod = load()
    gate, lost = mod.default_gate_factory(tmp_path / "missing.json")
    with pytest.raises(lost):
        gate.check(force=True)


# =========================================================================== 102 H-PROBE: 3 counterexample regressions + containment that
def test_102_a_watchdog_firing_while_close_is_in_progress_never_ends_in_success(tmp_path):
    mod = load()
    holder = {}

    def fire_during_close():
        holder["timer"].fire()                                                                                   # watchdog no GIUA luc dang close
    conn = FakeConn(mod, on_close=fire_during_close)

    class T(FakeTimer):
        def __init__(self, interval, callback):
            super().__init__(interval, callback)
            holder["timer"] = self
    mod, code, report, conn, gate, order, exits, timers, out = run(tmp_path, conn=conn, timer=T, mod=mod)
    assert exits == [4] and code == 4 and report["ok"] is False and "wall_timeout" in report["errors"]            # watchdog thoat 4; main khong claim thanh cong; chi mot bao cao


def test_102_a_close_failure_is_not_swallowed_and_blocks_success(tmp_path):
    mod = load()

    def broken_close():
        raise OSError(f"socket reset {SECRET}")
    mod, code, report, conn, gate, order, exits, timers, out = run(tmp_path, conn=FakeConn(mod, on_close=broken_close), mod=mod)
    assert code == 4 and report["ok"] is False and "close_failed" in report["errors"][0] and SECRET not in out.read_text(encoding="utf-8")
    assert report["attempted"][-1] == {"sql": "close", "status": "error"}


def test_102_a_watchdog_that_cannot_write_the_report_still_exits_with_4_and_main_cannot_claim_success(tmp_path, monkeypatch):
    mod = load()
    holder = {}
    real_write = mod.write_report

    def failing_write(path, report):
        raise OSError("disk full")
    conn = FakeConn(mod, on_close=lambda: (monkeypatch.setattr(mod, "write_report", failing_write), holder["timer"].fire()))

    class T(FakeTimer):
        def __init__(self, interval, callback):
            super().__init__(interval, callback)
            holder["timer"] = self
    mod, code, report, conn, gate, order, exits, timers, out = run(tmp_path, conn=conn, timer=T, mod=mod)
    assert exits == [4] and code == 4 and report is None                                                         # khong ghi duoc bao cao, nhung thoat 4 va main khong tra 0
    monkeypatch.setattr(mod, "write_report", real_write)


def test_102_a_main_report_write_failure_is_rc4_not_success(tmp_path, monkeypatch):
    mod = load()
    monkeypatch.setattr(mod, "write_report", lambda path, report: (_ for _ in ()).throw(OSError("disk full")))
    mod, code, report, conn, gate, order, exits, timers, out = run(tmp_path, mod=mod)
    assert code == 4 and report is None and exits == [] and conn.closed == 1


def test_102_the_watchdog_after_a_completed_report_does_not_exit_the_process(tmp_path):
    mod = load()
    mod, code, report, conn, gate, order, exits, timers, out = run(tmp_path, mod=mod)
    assert code == 0 and report["ok"] is True
    timers[0].fire()                                                                                             # watchdog muon: bao cao da xong => khong thoat, khong ghi de
    assert exits == [] and json.loads(out.read_text(encoding="utf-8"))["ok"] is True


def test_102_a_real_child_process_with_a_hanging_close_is_terminated_by_the_watchdog_with_exit_4(tmp_path):
    """Chung minh containment bang process THAT (child Python, khong DB, khong .env): close treo > wall => os._exit(4) tu thread watchdog, bao cao `wall_timeout` ton tai."""
    out = tmp_path / "wall.json"
    child = textwrap.dedent(f"""
        import importlib.util, sys, time
        sys.path.insert(0, r"{PROBE.parent}")
        spec = importlib.util.spec_from_file_location("p", r"{PROBE}")
        mod = importlib.util.module_from_spec(spec); spec.loader.exec_module(mod)
        mod.WALL_MAX_S = 1.5
        class Gate:
            checks = 0; last_verdict = "ok"; sha_chain = []; receipts = []
            def check(self, force=False): pass
        class Cur:
            def execute(self, s): pass
            def fetchall(self): return [(1 << 30, 1 << 29)]
            def close(self): pass
        class Conn:
            connection_id = 7
            def get_server_info(self): return "fake"
            def cursor(self): return Cur()
            def rollback(self): pass
            def close(self): time.sleep(60)
        sys.exit(mod.main(["--crawl-evidence", r"{tmp_path / 'live.json'}", "--out", r"{out}"], connect_fn=lambda s: Conn(), gate_factory=lambda p: (Gate(), RuntimeError)))
    """)
    started = time.monotonic()
    done = subprocess.run([sys.executable, "-I", "-c", child], capture_output=True, text=True, timeout=30)
    elapsed = time.monotonic() - started
    assert done.returncode == 4 and elapsed < 15, (done.returncode, elapsed, done.stderr[-300:])
    report = json.loads(out.read_text(encoding="utf-8"))
    assert report["ok"] is False and "wall_timeout" in report["errors"]

def test_102_a_watchdog_that_fails_to_write_but_a_later_main_write_succeeding_cannot_make_the_run_a_success(tmp_path, monkeypatch):
    mod = load()
    holder = {}
    real_write = mod.write_report
    calls = []

    def write_fails_once(path, report):
        calls.append(1)
        if len(calls) == 1:
            raise OSError("disk full (watchdog)")
        return real_write(path, report)
    monkeypatch.setattr(mod, "write_report", write_fails_once)
    conn = FakeConn(mod, on_close=lambda: holder["timer"].fire())

    class T(FakeTimer):
        def __init__(self, interval, callback):
            super().__init__(interval, callback)
            holder["timer"] = self
    mod, code, report, conn, gate, order, exits, timers, out = run(tmp_path, conn=conn, timer=T, mod=mod)
    assert exits == [4] and code == 4 and not out.exists()                                                       # main KHONG duoc ghi bao cao thanh cong sau khi watchdog da no


def test_102_the_watchdog_stays_armed_through_the_final_report_write(tmp_path, monkeypatch):
    mod = load()
    holder = {}
    real_write = mod.write_report

    def hanging_write(path, report):
        holder["timer"].fire()                                                                                   # watchdog no GIUA luc ghi bao cao cuoi (ghi treo)
        return real_write(path, report)
    monkeypatch.setattr(mod, "write_report", hanging_write)

    class T(FakeTimer):
        def __init__(self, interval, callback):
            super().__init__(interval, callback)
            holder["timer"] = self
    mod, code, report, conn, gate, order, exits, timers, out = run(tmp_path, timer=T, mod=mod)
    assert exits == [4]                                                                                          # timer con hieu luc (chua cancel) nen ep thoat 4 du main dang giu khoa


def test_102_after_a_failed_final_write_the_watchdog_must_still_be_able_to_exit(tmp_path, monkeypatch):
    mod = load()
    monkeypatch.setattr(mod, "write_report", lambda path, report: (_ for _ in ()).throw(OSError("disk full")))
    exits = []
    mod, code, report, conn, gate, order, exits, timers, out = run(tmp_path, mod=mod, exits=exits)
    assert code == 4 and exits == []
    timers[0].cancelled = False                                                                                  # mo phong timer that van dang chay den khi het gio
    timers[0].fire()
    assert exits == [4]                                                                                          # chua co bao cao nao duoc ghi => watchdog khong duoc coi la "da xong"

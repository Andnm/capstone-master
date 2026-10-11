"""audit_tools/repair_q0_v1.py — CLI Q0 tren ket noi GIA: ghi receipt chi khi moi khoa True, khong ghi de, khong cham DB neu out ton tai, receipt doc duoc boi runner v2."""
from __future__ import annotations

import datetime as dt
import importlib.util
import json
from pathlib import Path

import pytest

HERE = Path(__file__).resolve().parent
TOOLS = HERE.parents[1] / "audit_tools"


def _load(path, name):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


q0 = _load(TOOLS / "repair_q0_v1.py", "repair_q0_v1")
obs_tests = _load(HERE / "test_repair_db_observer_v1.py", "observer_tests_for_q0")
rr = _load(TOOLS / "repair_phase_runner_v2.py", "runner_v2_for_q0")
NOW = dt.datetime(2026, 10, 9, 11, 0, tzinfo=dt.timezone.utc)


def run(tmp_path, server, name="q0.json", extra=()):
    out = tmp_path / name
    rc = q0.main(["--out", str(out), "--wait-gone-s", "0.5", *extra], connect_factory=lambda stack: server.connect, now_utc=lambda: NOW)
    return rc, out


def test_a_healthy_server_yields_exit_zero_a_receipt_the_runner_can_verify_and_a_secret_free_summary(tmp_path, capsys):
    server = obs_tests.FakeServer()
    rc, out = run(tmp_path, server)
    summary = json.loads(capsys.readouterr().out)
    assert rc == 0 and out.is_file() and summary["ok"] is True and summary["receipt_sha256"] == rr.file_sha256(out) and summary["statements"] > 0
    assert set(summary) == {"ok", "selftest", "server", "statements", "errors", "receipt_sha256", "receipt_path"} and "uuid-fake-1" not in json.dumps(summary)
    loaded = rr.load_q0_receipt(out, expected_receipt_sha256=summary["receipt_sha256"], expected_observer_sha256=obs_tests.ob.script_sha256(), expected_sql_set_sha256=obs_tests.ob.sql_set_sha256(),
                                expected_model_sha256=rr.qm.__source_sha256__, now_utc=NOW + dt.timedelta(minutes=5), max_age_s=3600)
    assert loaded["verified"] is True and list(server.sessions.values()) == [{"user": "other", "db": "hotel_price_intel", "pid": 1}]   # ca ba phien cua ta da dong


def test_a_missing_capability_gives_exit_one_and_no_receipt_file(tmp_path, capsys):
    server = obs_tests.FakeServer()
    server.hide_pid = True
    rc, out = run(tmp_path, server)
    summary = json.loads(capsys.readouterr().out)
    assert rc == 1 and not out.exists() and summary["ok"] is False and summary["selftest"]["pid_attribute_visible"] is False and "receipt_sha256" not in summary
    assert "rejected" not in summary and "receipt_path" not in summary                                           # self-test khong dat => khong he THU ghi receipt (khong chi bi writer tu choi)


def test_an_existing_out_path_is_refused_before_any_database_activity(tmp_path, capsys):
    server = obs_tests.FakeServer()
    (tmp_path / "q0.json").write_text("existing", encoding="utf-8")
    rc, out = run(tmp_path, server)
    assert rc == 2 and out.read_text(encoding="utf-8") == "existing" and server.log == [] and json.loads(capsys.readouterr().out) == {"ok": False, "error": "out_exists"}


def test_only_select_statements_with_a_time_limit_were_issued(tmp_path):
    server = obs_tests.FakeServer()
    run(tmp_path, server)
    assert server.log and all(sql.startswith("SELECT /*+ MAX_EXECUTION_TIME(5000) */ ") for sql in server.log)


def test_the_statement_budget_is_enforced_by_the_cli_option(tmp_path):
    server = obs_tests.FakeServer()
    with pytest.raises(RuntimeError, match="observer_statement_budget_exhausted"):
        run(tmp_path, server, extra=("--max-statements", "3"))
    assert not (tmp_path / "q0.json").exists()


@pytest.mark.parametrize("extra", [("--max-statements", "0"), ("--max-statements", "41"), ("--wait-gone-s", "0"), ("--wait-gone-s", "10.5"), ("--wait-gone-s", "nan"), ("--wait-gone-s", "inf"),
                                   ("--poll-s", "0"), ("--poll-s", "6"), ("--poll-s", "nan")])
def test_limits_outside_the_pinned_bounds_exit_two_before_any_database_activity(tmp_path, capsys, extra):
    server = obs_tests.FakeServer()
    out = tmp_path / "q0.json"
    rc = q0.main(["--out", str(out), *extra], connect_factory=lambda stack: server.connect, now_utc=lambda: NOW)
    assert rc == 2 and not out.exists() and server.log == [] and json.loads(capsys.readouterr().out)["error"].startswith("bad_limits")


def test_the_poll_interval_option_reaches_the_observer_wait_loop(tmp_path, monkeypatch):
    import time as _time
    server = obs_tests.FakeServer()
    server.close_lag = 3                                                                                      # phien phu chi bien mat sau 3 lan quet => vong cho phai ngu giua cac lan
    sleeps = []
    monkeypatch.setattr(_time, "sleep", lambda seconds: sleeps.append(seconds))
    rc, out = run(tmp_path, server, extra=("--poll-s", "0.25"))
    assert rc == 0 and out.is_file() and sleeps and set(sleeps) == {0.25}


def test_a_false_connection_id_sanity_gives_exit_one_no_receipt_and_the_reason(tmp_path, capsys):
    server = obs_tests.FakeServer()
    original = server.answer
    seen = []

    def answer(sid, sql, params):
        rows = original(sid, sql, params)
        if sql == obs_tests.ob.SQL_CONNECTION_INFO:
            seen.append(rows[0]["probe_session_id"])
            if len(seen) == 3:
                return [{"probe_session_id": seen[1], "app_user": rows[0]["app_user"]}]
        return rows
    server.answer = answer
    rc, out = run(tmp_path, server)
    summary = json.loads(capsys.readouterr().out)
    assert rc == 1 and not out.exists() and summary["ok"] is False and "q0_connection_id_sanity_false" in summary["rejected"] and "receipt_sha256" not in summary


def test_the_default_factory_uses_the_readonly_adapter_over_the_backend_connect_kwargs(tmp_path, monkeypatch):
    import types as _types
    import sys as _sys
    calls = {}
    fake_mysql = _types.ModuleType("mysql")
    fake_connector = _types.ModuleType("mysql.connector")
    fake_connector.connect = lambda **kw: calls.setdefault("kwargs", kw) and None
    fake_mysql.connector = fake_connector
    fake_app = _types.ModuleType("app")
    fake_wh = _types.ModuleType("app.warehouse")
    fake_conn = _types.ModuleType("app.warehouse.connection")
    fake_conn._connect_kwargs = lambda database: {"host": "h", "database_arg": database}
    monkeypatch.setitem(_sys.modules, "mysql", fake_mysql)
    monkeypatch.setitem(_sys.modules, "mysql.connector", fake_connector)
    monkeypatch.setitem(_sys.modules, "app", fake_app)
    monkeypatch.setitem(_sys.modules, "app.warehouse", fake_wh)
    monkeypatch.setitem(_sys.modules, "app.warehouse.connection", fake_conn)
    import contextlib
    factory = q0.default_connect_factory(contextlib.ExitStack(), obs_tests.ob)
    with pytest.raises(Exception):
        factory()                                                                                             # fake connect tra None => adapter nem loi, nhung kwargs da duoc lay tu backend
    assert calls["kwargs"] == {"host": "h", "database_arg": None, "use_pure": True}

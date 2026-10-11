"""operator_probe + run_probe_workflow (probe InnoDB: MOT plan readonly mot phase, khong Q0): freeze, probe crawler fail-closed, hai cong gate, chuoi receipt MOT phase + bao cao JSON khop tung truong (GPT 106), cleanup finally.
Toan bo fake: khong SQL, khong process that. Hash runner/probe lay tu CHINH tep da ghim (exec-hash)."""
from __future__ import annotations

import datetime as dt
import hashlib
import importlib.util
import json
import subprocess
from pathlib import Path

import pytest

from test_operator_q0 import FakeWorld

RUN_REAL = Path(r"D:\MSE\CAPSTONE\outputs\repair-run-20261009")
OPERATOR = RUN_REAL / "operator_probe.py"
DRIVER = RUN_REAL / "run_probe_workflow.py"
NOW = dt.datetime(2026, 10, 9, 14, 0, tzinfo=dt.timezone.utc)


def load(path: Path, name: str):
    spec = importlib.util.spec_from_file_location(name, path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def text(t: dt.datetime) -> str:
    return t.strftime("%Y-%m-%dT%H:%M:%SZ")


GOOD_ATTEMPTED = [("SET SESSION TRANSACTION READ ONLY", "ok"), ("SET SESSION MAX_EXECUTION_TIME=5000", "ok"), ("SELECT", "ok"), ("ROLLBACK", "ok"), ("close", "ok")]


class Probe:
    def __init__(self, tmp_path, monkeypatch):
        self.mod = load(OPERATOR, "operator_probe_under_test")
        mod = self.mod
        self.run = tmp_path / "run"
        self.run.mkdir()
        run = self.run
        monkeypatch.setattr(mod, "RUN", run)
        for name, rel in (("LIVE", "live.json"), ("SUP_LOG", "probe_supervisor.jsonl"), ("PUB_LOG", "probe_publisher.log"), ("STOP", "probe_STOP"), ("SUP_STDOUT", "probe_supervisor_stdout.log"),
                          ("OP_LOG", "probe_operator_log.jsonl"), ("PREFLIGHT", "probe_dossier_preflight.md"), ("ARCHIVE_DIR", "evidence_archive/probe"), ("PROBE_DIR", "probe"),
                          ("PROBE_REPORT", "probe/innodb_probe_report.json")):
            monkeypatch.setattr(mod, name, run / rel)
        self.world = FakeWorld(mod, run, monkeypatch)
        monkeypatch.setattr(mod, "now", lambda: NOW + dt.timedelta(seconds=self.world.t - self.world.t0))
        self.tmp, self.monkeypatch = tmp_path, monkeypatch
        self.windows = {"not_before_utc": text(NOW - dt.timedelta(minutes=5)), "start_deadline_utc": text(NOW + dt.timedelta(minutes=55)), "hard_not_after_utc": text(NOW + dt.timedelta(hours=5))}
        self.freeze = self.make_freeze()
        self.freeze_path, self.freeze_sha = self.write_freeze(self.freeze)

    def file(self, name, content=b"x"):
        path = self.tmp / name
        path.write_bytes(content)
        return path

    def make_freeze(self):
        mod, run = self.mod, self.run
        (run / "probe" / "plan").mkdir(parents=True)
        spec = {"name": "innodb_probe", "kind": "readonly", "argv": ["py", "probe.py", "--out", str(mod.PROBE_REPORT)], "cwd": r"D:\MSE\CAPSTONE\hotel-price-intelligence\backend", "env": {"PATH": "p"},
                "required_files": {}, "deadline_s": 30.0, "outputs": [str(mod.PROBE_REPORT)]}
        plan_sha = "c" * 64
        plan_file = self.file("probe.plan.json", json.dumps({"document": {"specs": [spec]}, "plan_sha256": plan_sha}).encode())
        approval = self.file("probe_approval.md", b"approval probe")
        plans = {"probe": {"plan_file": str(plan_file), "plan_file_sha256": hashlib.sha256(plan_file.read_bytes()).hexdigest(), "plan_sha256": plan_sha, "approval": str(approval),
                           "approval_sha256": hashlib.sha256(approval.read_bytes()).hexdigest(), "invocation_sha256": "a" * 64, "plan_dir": str(run / "probe" / "plan"), "launcher_logs": str(run / "probe" / "logs"),
                           "output": str(mod.PROBE_REPORT), "total_budget_s": 240.0}}
        profile = self.file("profile.json", b"profile")
        pins = {name: {"path": str(path), "sha256": hashlib.sha256(Path(path).read_bytes()).hexdigest()} for name, path in mod.FIXED_PIN_PATHS.items()}
        return {"kind": "probe_freeze", "run_root": str(run), "windows": dict(self.windows), "plans": plans,
                "profile": {"path": str(profile), "sha256": hashlib.sha256(profile.read_bytes()).hexdigest(), "renew_seconds": 900}, "pins": pins}

    def write_freeze(self, doc, name="probe_freeze.json"):
        path = self.run / name
        data = json.dumps(doc, sort_keys=True).encode("utf-8")
        path.write_bytes(data)
        return path, hashlib.sha256(data).hexdigest()

    def load(self):
        return self.mod.load_freeze(self.freeze_path, self.freeze_sha)

    def started(self, wall_seconds=900):
        self.world.append({"event": "started", "pid": 1, "at_utc": NOW.strftime("%Y-%m-%dT%H:%M:%S.%fZ"), "wall_deadline_utc": text(NOW + dt.timedelta(seconds=wall_seconds))})

    def evidence(self, **kw):
        state = {"present": True, "age_s": 10, "active_runs": 0, "active_items": 0, "read_only_verified": True, "latest_run_id": 66, "latest_run_status": "completed", "sha256": "x" * 64,
                 "generator_sha256": self.mod.CFG["pins"]["source"]["sha256"]}
        state.update(kw)
        self.monkeypatch.setattr(self.mod, "evidence_state", lambda: dict(state))

    def good_report(self):
        probe = self.mod.probe_module()
        planned = [*probe.SESSION_SQL, probe.SQL, "ROLLBACK"]
        sqls = {"SELECT": probe.SQL}
        return {"kind": "innodb_global_probe", "ok": True, "errors": [], "units": "bytes", "source_sha256": self.mod.CFG["pins"]["probe_script"]["sha256"], "sql_set_sha256": probe.sql_set_sha256(),
                "planned_inventory": [{"sql": s, "sha256": hashlib.sha256(s.encode()).hexdigest()} for s in planned],
                "attempted": [{"sql": sqls.get(name, name), "status": status} for name, status in GOOD_ATTEMPTED], "values": {"buffer_pool_size_bytes": 1 << 30, "redo_log_capacity_bytes": 1 << 29},
                "gate": {"checks": 2, "last_verdict": "ok", "sha_chain": ["a" * 64], "receipts": [{"sha256": "a" * 64}]}, "server": {"version": "8.0.45", "connection_id": 77}}

    def entry(self, *, check_ok=True, rc=0, report=None, report_mutator=None, write_report=True, raw_report=None, receipt_tamper=None, bound_tamper=None, summary_tamper=None, extra_files=(), outputs_override=None,
              check_elapsed_s=0.0, invoke_exc=None, stdout_prefix=b"", hash_override=None):
        calls = []
        rr = self.mod.runner()

        def fake_run(argv, **kwargs):
            calls.append((list(argv), kwargs))
            p = self.mod.CFG["plans"]["probe"]
            if argv[-1] == "--check-only":
                self.world.t += check_elapsed_s
                payload = {"ok": check_ok, "computed_plan_sha256": p["plan_sha256"], "computed_invocation_sha256": hash_override or p["invocation_sha256"]}
                return subprocess.CompletedProcess(argv, 0 if check_ok else 1, stdout=(json.dumps(payload) + "\n").encode(), stderr=b"")
            if invoke_exc:
                raise invoke_exc
            directory = Path(p["plan_dir"])
            plan_doc = json.loads(Path(p["plan_file"]).read_text(encoding="utf-8"))["document"]
            spec = plan_doc["specs"][0]
            entry_sha = self.mod.CFG["pins"]["entry"]["sha256"]
            data = raw_report
            if data is None and write_report:
                doc = report if report is not None else self.good_report()
                if report_mutator:
                    report_mutator(doc)
                data = json.dumps(doc, indent=2, sort_keys=True).encode("utf-8")
            if data is not None:
                self.mod.PROBE_REPORT.write_bytes(data)
            outputs = outputs_override if outputs_override is not None else ({str(self.mod.PROBE_REPORT): hashlib.sha256(data).hexdigest()} if data is not None else {})
            bound = {"kind": "plan_bound", "approved_plan_sha256": p["plan_sha256"], "computed_plan_sha256": p["plan_sha256"], "entrypoint_sha256": entry_sha, "phases": ["innodb_probe"], **(bound_tamper or {})}
            (directory / "PLAN_BOUND.json").write_text(json.dumps(bound), encoding="utf-8")
            launch = {"exit_code": 0, "timed_out": False, "killed_tree": False, "aborted_by_gate": None, "contained": True}
            full = {"phase": "innodb_probe", "seq": 1, "verdict": "PASS", "reasons": [], "kind": "readonly", "prev_receipt_sha256": None, "approved_plan_sha256": p["plan_sha256"], "computed_plan_sha256": p["plan_sha256"],
                    "entrypoint_sha256": entry_sha, "spec_sha256": rr.spec_sha256(spec), "argv_sha256": rr.argv_sha256(spec), "q0_receipt_sha256": None, "launch": launch, "outputs": outputs, **(receipt_tamper or {})}
            (directory / "receipt_01_innodb_probe.json").write_text(json.dumps(full, indent=2, sort_keys=True), encoding="utf-8")
            canonical = rr.sha256_hex(rr.canonical_json(full))
            for extra in extra_files:
                (directory / extra).write_text("x", encoding="utf-8")
            summary = {"approved_plan_sha256": p["plan_sha256"], "computed_plan_sha256": p["plan_sha256"], "entrypoint_sha256": entry_sha, "invocation_sha256": p["invocation_sha256"], "ok": True,
                       "phases": [{"phase": "innodb_probe", "reasons": [], "receipt_path": "x", "receipt_sha256": canonical, "verdict": "PASS"}], **(summary_tamper or {})}
            return subprocess.CompletedProcess(argv, rc, stdout=stdout_prefix + json.dumps(summary).encode() + b"\r\n", stderr=b"")
        self.monkeypatch.setattr(self.mod.subprocess, "run", fake_run)
        return calls


@pytest.fixture
def pr(tmp_path, monkeypatch):
    env = Probe(tmp_path, monkeypatch)
    assert env.load() == []
    return env


def ready(pr):
    pr.started()
    pr.evidence()
    return pr.entry(stdout_prefix=b"line1\r\n")


# =========================================================================== load_freeze
def test_a_valid_freeze_loads_and_the_argv_has_no_q0_receipt(pr):
    mod = pr.mod
    p = mod.CFG["plans"]["probe"]
    assert mod.approved_argv("probe") == [mod.PY_VENV, str(mod.ENTRY), "--approval", p["approval"], "--plan", p["plan_file"], "--plan-dir", p["plan_dir"], "--launcher-logs", p["launcher_logs"],
                                           "--crawl-evidence", str(mod.LIVE)]
    assert "q0" not in mod.CFG and set(mod.CFG["plans"]) == {"probe"} and mod.PLAN_ORDER == ("probe",)


def mutated(pr, fn):
    doc = json.loads(json.dumps(pr.freeze))
    fn(doc)
    path, digest = pr.write_freeze(doc, "mutated.json")
    return pr.mod.load_freeze(path, digest)


@pytest.mark.parametrize("name,fn,expected", [
    ("kind", lambda d: d.__setitem__("kind", "heavy_ci_freeze"), "freeze_kind_or_run_root"),
    ("run_root", lambda d: d.__setitem__("run_root", r"D:\elsewhere"), "freeze_kind_or_run_root"),
    ("windows_order", lambda d: d["windows"].__setitem__("start_deadline_utc", d["windows"]["not_before_utc"]), "freeze_windows_order"),
    ("plan_key_missing", lambda d: d["plans"]["probe"].pop("output"), "freeze_plan_probe"),
    ("plan_relative", lambda d: d["plans"]["probe"].__setitem__("approval", "approval.md"), "freeze_plan_probe"),
    ("plan_dir_elsewhere", lambda d: d["plans"]["probe"].__setitem__("plan_dir", r"D:\elsewhere\plan"), "freeze_plan_probe_dirs_not_in_run_root"),
    ("output_elsewhere", lambda d: d["plans"]["probe"].__setitem__("output", r"D:\elsewhere\report.json"), "freeze_plan_probe_output_not_the_probe_report"),
    ("pin_runner_redirected", lambda d: d["pins"]["runner"].__setitem__("path", r"D:\evil\runner.py"), "freeze_pin_runner"),
    ("pin_probe_script_redirected", lambda d: d["pins"]["probe_script"].__setitem__("path", r"D:\evil\probe.py"), "freeze_pin_probe_script"),
    ("pin_gate_module_missing", lambda d: d["pins"].pop("gate_module"), "freeze_pin_gate_module"),
    ("profile_bad", lambda d: d["profile"].__setitem__("sha256", "short"), "freeze_profile"),
])
def test_every_malformed_or_redirected_freeze_is_refused_and_leaves_cfg_empty(pr, name, fn, expected):
    assert expected in mutated(pr, fn) and pr.mod.CFG == {}


def test_a_wrong_sha_or_unreadable_freeze_is_refused(pr):
    assert pr.mod.load_freeze(pr.freeze_path, "0" * 64) == ["freeze_sha256_mismatch"] and pr.mod.CFG == {}
    assert pr.mod.load_freeze(pr.run / "nope.json", pr.freeze_sha) == ["freeze_unreadable"]


# =========================================================================== crawler probe + preconditions
@pytest.mark.parametrize("result,success,pids", [
    (subprocess.CompletedProcess([], 0, stdout="", stderr=""), True, []),
    (subprocess.CompletedProcess([], 1, stdout="", stderr=""), False, []),
    (subprocess.CompletedProcess([], 0, stdout="", stderr="denied"), False, []),
    (subprocess.CompletedProcess([], 0, stdout="junk 5", stderr=""), False, [5]),
])
def test_crawler_probe_is_fail_closed(pr, monkeypatch, result, success, pids):
    monkeypatch.setattr(pr.mod.subprocess, "run", lambda *a, **k: result)
    probe = pr.mod.crawler_probe()
    assert probe["success"] is success and probe["pids"] == pids


def clean_probe(pr, monkeypatch, **override):
    probe = {"success": True, "pids": [], "returncode": 0, "timed_out": False}
    probe.update(override)
    monkeypatch.setattr(pr.mod, "crawler_probe", lambda: dict(probe))


def test_preconditions_clean_and_each_problem(pr, monkeypatch):
    clean_probe(pr, monkeypatch)
    assert pr.mod.preconditions() == []
    clean_probe(pr, monkeypatch, success=False, returncode=1)
    assert any("crawler_probe_failed" in p for p in pr.mod.preconditions())
    clean_probe(pr, monkeypatch, pids=[3])
    assert any("crawler_process_alive" in p for p in pr.mod.preconditions())
    clean_probe(pr, monkeypatch)
    pr.world.t += 3600
    assert any("outside_start_window" in p for p in pr.mod.preconditions())
    pr.world.t = pr.world.t0
    Path(pr.mod.CFG["plans"]["probe"]["approval"]).write_bytes(b"tampered")
    (pr.run / "probe" / "innodb_probe_report.json").write_text("old", encoding="utf-8")
    (pr.run / "probe_STOP").write_text("x", encoding="utf-8")
    (pr.run / "probe" / "plan" / "leftover.json").write_text("x", encoding="utf-8")
    monkeypatch.setenv("DB_HOST", "x")
    problems = pr.mod.preconditions()
    for needle in ("sha_mismatch: probe_approval", "already_exists: innodb_probe_report.json", "already_exists: probe_STOP", "plan_dir_not_empty: probe", "shell_inherits_DB_vars"):
        assert needle in problems


def test_preconditions_refuse_when_no_freeze_is_loaded(pr):
    pr.mod.CFG.clear()
    assert pr.mod.preconditions() == ["freeze_not_loaded"]


# =========================================================================== plan thanh cong + cong
def test_the_plan_runs_one_check_only_then_one_invocation_with_exact_argv_timeouts_and_byte_exact_outputs(pr):
    calls = ready(pr)
    assert pr.mod.cmd_plans() == 0
    assert [a[-1] == "--check-only" for a, _ in calls] == [True, False]
    for argv, kw in calls:
        assert argv == pr.mod.approved_argv("probe") + (["--check-only"] if argv[-1] == "--check-only" else []) and kw["env"] == pr.mod.ENV6 and kw["cwd"] == str(pr.mod.ROOT)
        assert kw["capture_output"] is True and kw["text"] is False
    assert calls[0][1]["timeout"] == 120 and calls[1][1]["timeout"] == 240 + 120                                # wrapper 360 s = budget+margin, KHONG phai gioi han con SQL (outer Job la 30 s trong plan)
    written = (pr.run / "probe_probe_entry_stdout.json").read_bytes()
    assert written.startswith(b"line1\r\n") and hashlib.sha256(written).hexdigest() in pr.mod.OP_LOG.read_text(encoding="utf-8")
    assert pr.world.stopped() and pr.mod.STOP.exists()


def test_the_lead_wait_happens_before_the_check_only(pr):
    ready(pr)
    pr.mod.cmd_plans()
    assert pr.world.t - pr.world.t0 >= 90 and "waiting_lead" in pr.world.op_events()


def test_without_a_started_publisher_nothing_runs(pr):
    calls = pr.entry()
    assert pr.mod.cmd_plans() == 2 and calls == [] and "stop_not_needed" in pr.world.op_events()


@pytest.mark.parametrize("what", ["check_not_ok", "hash_differs"])
def test_a_failing_check_only_means_no_invocation(pr, what):
    pr.started()
    pr.evidence()
    calls = pr.entry(check_ok=(what != "check_not_ok"), hash_override=("f" * 64 if what == "hash_differs" else None))
    assert pr.mod.cmd_plans() == 2 and len(calls) == 1 and pr.world.stopped()


def test_gates_are_rechecked_right_before_the_invocation(pr, monkeypatch):
    pr.started(wall_seconds=90 + 360 + 100)
    pr.evidence()
    calls = pr.entry(check_elapsed_s=115.0)
    assert pr.mod.cmd_plans() == 2 and [a[-1] for a, _ in calls] == ["--check-only"] and "before_invocation" in pr.mod.OP_LOG.read_text(encoding="utf-8")
    assert "insufficient_publisher_time" in pr.mod.OP_LOG.read_text(encoding="utf-8")


def test_the_start_deadline_is_a_gate_of_the_actual_first_invocation(pr):
    pr.started(wall_seconds=9000)
    pr.evidence()
    calls = pr.entry(check_elapsed_s=115.0)
    pr.mod.CFG["windows"]["start_deadline_utc"] = NOW + dt.timedelta(seconds=100)
    assert pr.mod.cmd_plans() == 2 and [a[-1] for a, _ in calls] == ["--check-only"] and "start_deadline_passed" in pr.mod.OP_LOG.read_text(encoding="utf-8")


@pytest.mark.parametrize("kwargs", [dict(active_runs=1), dict(read_only_verified=False), dict(age_s=500), dict(generator_sha256="f" * 64)])
def test_busy_or_stale_evidence_blocks_the_plan_at_the_first_gate(pr, kwargs):
    pr.started()
    pr.evidence(**kwargs)
    calls = pr.entry()
    assert pr.mod.cmd_plans() == 2 and calls == [] and pr.world.stopped()


def test_evidence_that_turns_busy_during_check_only_blocks_the_invocation(pr, monkeypatch):
    pr.started()
    pr.evidence()
    calls = pr.entry()
    real = pr.mod.subprocess.run

    def run_then_busy(argv, **kw):
        result = real(argv, **kw)
        if argv[-1] == "--check-only":
            pr.evidence(active_runs=1)
        return result
    monkeypatch.setattr(pr.mod.subprocess, "run", run_then_busy)
    assert pr.mod.cmd_plans() == 2 and [a[-1] for a, _ in calls] == ["--check-only"]


def test_a_publisher_that_stops_or_a_hard_deadline_that_passes_blocks_the_plan(pr):
    pr.started()
    pr.world.append({"event": "stopped", "verified_dead": True, "reason": "wall_deadline"})
    pr.evidence()
    calls = pr.entry()
    assert pr.mod.run_plan("probe", {"at_utc": NOW.strftime("%Y-%m-%dT%H:%M:%S.%fZ"), "wall_deadline_utc": text(NOW + dt.timedelta(seconds=900))}, first=True) == 2 and calls == []


def test_the_hard_not_after_limit_refuses_with_its_own_reason(pr):
    pr.evidence()
    calls = pr.entry()
    pr.mod.CFG["windows"]["hard_not_after_utc"] = NOW - dt.timedelta(seconds=1)
    assert pr.mod.run_plan("probe", {"at_utc": NOW.strftime("%Y-%m-%dT%H:%M:%S.%fZ"), "wall_deadline_utc": text(NOW + dt.timedelta(seconds=900))}, first=True) == 2 and calls == []
    assert "hard_not_after_passed" in pr.mod.OP_LOG.read_text(encoding="utf-8")


def test_an_existing_report_or_missing_probe_dir_refuses_the_invocation(pr):
    pr.started()
    pr.evidence()
    calls = pr.entry()
    pr.mod.PROBE_REPORT.write_text("old", encoding="utf-8")
    assert pr.mod.cmd_plans() == 2 and [a[-1] for a, _ in calls] == ["--check-only"]


# =========================================================================== sau invocation: receipt + bao cao (GPT 106 §2.3-2.4)
RECEIPT_TAMPER = [
    ("foreign_plan", {"approved_plan_sha256": "f" * 64}), ("wrong_prev", {"prev_receipt_sha256": "0" * 64}), ("wrong_argv", {"argv_sha256": "e" * 64}), ("wrong_spec", {"spec_sha256": "e" * 64}),
    ("wrong_seq", {"seq": 2}), ("write_kind", {"kind": "write"}), ("q0_pin_present", {"q0_receipt_sha256": "9" * 64}), ("wrong_entry", {"entrypoint_sha256": "7" * 64}),
    ("timed_out", {"launch": {"exit_code": 0, "timed_out": True, "killed_tree": False, "aborted_by_gate": None, "contained": True}}),
    ("killed_tree", {"launch": {"exit_code": 0, "timed_out": False, "killed_tree": True, "aborted_by_gate": None, "contained": True}}),
    ("not_contained", {"launch": {"exit_code": 0, "timed_out": False, "killed_tree": False, "aborted_by_gate": None, "contained": False}}),
    ("aborted_by_gate", {"launch": {"exit_code": 0, "timed_out": False, "killed_tree": False, "aborted_by_gate": "crawler", "contained": True}}),
    ("nonzero_exit", {"launch": {"exit_code": 3, "timed_out": False, "killed_tree": False, "aborted_by_gate": None, "contained": True}}),
    ("verdict", {"verdict": "QUARANTINE"}), ("reasons", {"reasons": ["x"]}),
]


@pytest.mark.parametrize("name,tamper", RECEIPT_TAMPER, ids=[n for n, _ in RECEIPT_TAMPER])
def test_a_receipt_that_is_not_the_genuine_runner_shape_is_a_failure_even_when_the_report_says_ok(pr, name, tamper):
    pr.started()
    pr.evidence()
    pr.entry(receipt_tamper=tamper)
    assert pr.mod.cmd_plans() == 5 and "plan_not_pass" in pr.world.op_events() and pr.world.stopped()


@pytest.mark.parametrize("kwargs", [
    dict(bound_tamper={"entrypoint_sha256": "7" * 64}), dict(bound_tamper={"phases": ["other"]}), dict(summary_tamper={"invocation_sha256": "0" * 64}), dict(summary_tamper={"ok": False}),
    dict(summary_tamper={"phases": []}), dict(extra_files=["QUARANTINE.json"]), dict(rc=3),
    dict(summary_tamper={"phases": [{"phase": "innodb_probe", "reasons": [], "receipt_path": "x", "receipt_sha256": "0" * 64, "verdict": "PASS"}]}),
    dict(bound_tamper={"approved_plan_sha256": "f" * 64}), dict(bound_tamper={"computed_plan_sha256": "f" * 64}), dict(bound_tamper={"kind": "other"})])
def test_plan_bound_summary_stray_files_and_nonzero_exit_are_failures(pr, kwargs):
    pr.started()
    pr.evidence()
    pr.entry(**kwargs)
    assert pr.mod.cmd_plans() != 0 and pr.world.stopped()


def mutate(**changes):
    def apply(doc):
        for key, value in changes.items():
            doc[key] = value
    return apply


REPORT_BAD = [
    ("not_ok", mutate(ok=False)), ("errors_present", mutate(errors=["wall_timeout"])), ("wrong_kind", mutate(kind="other")), ("wrong_units", mutate(units="kib")),
    ("wrong_source", mutate(source_sha256="0" * 64)), ("wrong_sql_set", mutate(sql_set_sha256="0" * 64)), ("planned_inventory_short", mutate(planned_inventory=[])),
    ("attempted_missing_close", lambda d: d.__setitem__("attempted", d["attempted"][:-1])), ("attempted_error_status", lambda d: d["attempted"][2].__setitem__("status", "error")),
    ("attempted_extra", lambda d: d["attempted"].append({"sql": "SET GLOBAL x=1", "status": "ok"})),
    ("value_bool", mutate(values={"buffer_pool_size_bytes": True, "redo_log_capacity_bytes": 1})), ("value_zero", mutate(values={"buffer_pool_size_bytes": 0, "redo_log_capacity_bytes": 1})),
    ("value_float", mutate(values={"buffer_pool_size_bytes": 1.5, "redo_log_capacity_bytes": 1})), ("value_missing", mutate(values={"buffer_pool_size_bytes": 1})), ("value_none", mutate(values=None)),
    ("gate_once", mutate(gate={"checks": 1, "last_verdict": "ok", "sha_chain": ["a" * 64]})), ("gate_no_chain", mutate(gate={"checks": 2, "last_verdict": "ok", "sha_chain": []})),
    ("server_missing", mutate(server=None)), ("server_conn_bool", mutate(server={"version": "8", "connection_id": True})),
]


@pytest.mark.parametrize("name,mutator", REPORT_BAD, ids=[n for n, _ in REPORT_BAD])
def test_a_report_that_does_not_match_every_field_is_a_failure(pr, name, mutator):
    pr.started()
    pr.evidence()
    pr.entry(report_mutator=mutator)
    assert pr.mod.cmd_plans() == 5 and "plan_not_pass" in pr.world.op_events() and pr.world.stopped()


@pytest.mark.parametrize("kwargs,why", [
    (dict(write_report=False), "probe_report_missing"),
    (dict(raw_report=b"{partial"), "report_unreadable"),
    (dict(outputs_override={}), "report_hash_differs_from_receipt_outputs"),
    (dict(outputs_override={"x": "0" * 64}), "report_hash_differs_from_receipt_outputs"),
])
def test_missing_partial_or_unbound_reports_are_failures(pr, kwargs, why):
    pr.started()
    pr.evidence()
    pr.entry(**kwargs)
    assert pr.mod.cmd_plans() == 5 and why in pr.mod.OP_LOG.read_text(encoding="utf-8")


def test_a_probe_script_that_differs_from_the_freeze_pin_cannot_validate_the_report(pr):
    pr.started()
    pr.evidence()
    pr.entry(report=pr.good_report())                                                                            # bao cao dung, nhung pin probe lech => khong validate duoc => FAIL
    pr.mod.CFG["pins"]["probe_script"]["sha256"] = "0" * 64
    pr.mod._PROBE = None
    assert pr.mod.cmd_plans() == 5 and "report_unreadable" in pr.mod.OP_LOG.read_text(encoding="utf-8")


def test_a_runner_that_differs_from_the_freeze_pin_cannot_validate_the_receipt_chain(pr):
    pr.started()
    pr.evidence()
    pr.entry(report=pr.good_report())
    pr.mod.CFG["pins"]["runner"]["sha256"] = "0" * 64
    pr.mod._RUNNER = None
    assert pr.mod.cmd_plans() == 5 and "receipt_chain_unreadable" in pr.mod.OP_LOG.read_text(encoding="utf-8")


def test_the_good_fixture_is_accepted_end_to_end_with_a_complete_ledger(pr):
    ready(pr)
    assert pr.mod.cmd_plans() == 0 and pr.mod.probe_report_problems("probe") == []


@pytest.mark.parametrize("exc", [KeyboardInterrupt, RuntimeError, subprocess.TimeoutExpired])
def test_exception_or_cancel_stops_the_publisher_and_propagates(pr, exc):
    pr.started()
    pr.evidence()
    pr.entry(invoke_exc=exc("x") if exc is not subprocess.TimeoutExpired else subprocess.TimeoutExpired("entry", 360))
    with pytest.raises(exc):
        pr.mod.cmd_plans()
    assert pr.mod.STOP.exists() and pr.world.stopped()


def test_unverified_cleanup_makes_a_passing_run_exit_nonzero(pr):
    ready(pr)
    pr.world.verified_dead = False
    with pytest.raises(SystemExit) as info:
        pr.mod.cmd_plans()
    assert info.value.code == 3


def test_a_failing_diagnostic_log_never_skips_the_stop(pr, monkeypatch):
    pr.started()
    real = pr.mod.log
    monkeypatch.setattr(pr.mod, "log", lambda kind, **f: (_ for _ in ()).throw(OSError("disk")) if kind == "stop_required_by_terminal_path" else real(kind, **f))
    assert pr.mod.ensure_stopped("test") == 0 and pr.mod.STOP.exists() and pr.world.stopped()


def test_start_handshake_timeout_requests_stop_and_confirms_refusal(pr):
    class Sup:
        def poll(self):
            return None
    pr.mod.SUP_STDOUT.write_bytes(b"")
    pr.world.refuse_on_stop_when_pending = True
    assert pr.mod.start_handshake(Sup()) == 2 and pr.mod.STOP.exists()


# =========================================================================== driver
@pytest.fixture
def driver():
    return load(DRIVER, "run_probe_workflow_under_test")


class FakeOp:
    __file__ = str(OPERATOR)

    def __init__(self, **rcs):
        self.calls, self.logged = [], []
        self.rcs = {"load_freeze": [], "preconditions": [], "cmd_start": 0, "cmd_evidence": 0, "cmd_plans": 0, "ensure_stopped": 0, "cmd_collect": 0}
        self.rcs.update(rcs)

    def _do(self, name):
        self.calls.append(name)
        value = self.rcs[name]
        if isinstance(value, BaseException):
            raise value
        return value

    def load_freeze(self, path, sha): return self._do("load_freeze")
    def preconditions(self): return self._do("preconditions")
    def cmd_start(self): return self._do("cmd_start")
    def cmd_evidence(self): return self._do("cmd_evidence")
    def cmd_plans(self): return self._do("cmd_plans")
    def ensure_stopped(self, reason): return self._do("ensure_stopped")
    def cmd_collect(self): return self._do("cmd_collect")
    def log(self, kind, **fields): self.logged.append(kind)
    def stamp(self): return "2026-10-09T14:00:00Z"


def go(driver, tmp_path, **rcs):
    fake = FakeOp(**rcs)
    code = driver.run_workflow(fake, freeze_path=tmp_path / "f.json", freeze_sha256="0" * 64, ack=lambda m, p: p.write_text("ack"), result_path=tmp_path / "result.json", ack_path=tmp_path / "ack.md", pre_checks=False)
    result = json.loads((tmp_path / "result.json").read_text(encoding="utf-8")) if (tmp_path / "result.json").exists() else None
    return code, fake, result


def test_driver_order_cleanup_and_collect(driver, tmp_path):
    code, fake, result = go(driver, tmp_path)
    assert code == 0 and fake.calls == ["load_freeze", "preconditions", "cmd_start", "cmd_evidence", "cmd_plans", "ensure_stopped", "cmd_collect"] and result["exit_code"] == 0


def test_driver_refuses_a_bad_freeze_before_acknowledging(driver, tmp_path):
    code, fake, result = go(driver, tmp_path, load_freeze=["freeze_sha256_mismatch"])
    assert code == 2 and fake.calls == ["load_freeze"] and result is None and not (tmp_path / "ack.md").exists()


@pytest.mark.parametrize("failing,rc,expected_calls", [("cmd_start", 2, ["load_freeze", "preconditions", "cmd_start"]), ("cmd_evidence", 1, ["load_freeze", "preconditions", "cmd_start", "cmd_evidence"])])
def test_driver_stops_after_a_nonzero_stage_but_cleans_up(driver, tmp_path, failing, rc, expected_calls):
    code, fake, result = go(driver, tmp_path, **{failing: rc})
    assert code == rc and fake.calls == [*expected_calls, "ensure_stopped", "cmd_collect"]


def test_driver_precondition_problems_and_plan_failure(driver, tmp_path):
    code, fake, _ = go(driver, tmp_path, preconditions=["crawler_process_alive: [1]"])
    assert code == 2 and "cmd_start" not in fake.calls and fake.calls[-2:] == ["ensure_stopped", "cmd_collect"]
    other = tmp_path / "o"
    other.mkdir()
    code, _, result = go(driver, other, cmd_plans=5)
    assert code == 5 and result["plans"] == 5


@pytest.mark.parametrize("where,exc", [("cmd_start", RuntimeError("x")), ("cmd_evidence", OSError("x")), ("cmd_plans", KeyboardInterrupt()), ("cmd_plans", SystemExit(3))])
def test_driver_exceptions_cancel_system_exit_still_cleanup(driver, tmp_path, where, exc):
    code, fake, _ = go(driver, tmp_path, **{where: exc})
    assert code != 0 and fake.calls[-2:] == ["ensure_stopped", "cmd_collect"]


def test_driver_cleanup_and_collect_failures_are_nonzero_without_masking(driver, tmp_path):
    for index, (rcs, expected) in enumerate([({"ensure_stopped": 3}, 3), ({"cmd_collect": 1}, 4), ({"cmd_collect": OSError("h")}, 4), ({"cmd_plans": 5, "cmd_collect": 1}, 5), ({"ensure_stopped": 3, "cmd_collect": 1}, 3)]):
        folder = tmp_path / f"c{index}"
        folder.mkdir()
        assert go(driver, folder, **rcs)[0] == expected


def test_driver_result_write_failure_cannot_turn_a_failure_into_success(driver, tmp_path):
    folder = tmp_path / "ro"
    folder.mkdir()
    fake = FakeOp()
    code = driver.run_workflow(fake, freeze_path=folder / "f.json", freeze_sha256="0" * 64, ack=lambda m, p: p.write_text("ack"), result_path=folder / "missing_dir" / "result.json", ack_path=folder / "ack.md", pre_checks=False)
    assert code != 0                                                                                             # khong ghi duoc ket qua => van nonzero


def test_driver_refuses_on_operator_pin_drift_or_a_previous_run(driver, tmp_path):
    fake = FakeOp()
    kwargs = dict(freeze_path=tmp_path / "f.json", freeze_sha256="0" * 64, ack=lambda m, p: p.write_text("a"), result_path=tmp_path / "r.json", ack_path=tmp_path / "a.md")
    driver.OPERATOR_PROBE_SHA256 = "0" * 64
    assert driver.run_workflow(fake, **kwargs) == 2 and fake.calls == []
    driver.OPERATOR_PROBE_SHA256 = hashlib.sha256(OPERATOR.read_bytes()).hexdigest()
    (tmp_path / "a.md").write_text("already")
    assert driver.run_workflow(fake, **kwargs) == 2 and fake.calls == []

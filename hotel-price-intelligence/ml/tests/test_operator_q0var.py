"""operator_q0var + run_q0var_workflow (Q0 THAM SO Q0-A / Q0-B: MOT plan readonly mot phase `q0_selftest`, khong Q0 pin): freeze voi run-root tham so (chi outputs\\repair-run-q0[ab]-rNNN), probe crawler fail-closed, hai cong gate,
chuoi receipt MOT phase + Q0 receipt kiem bang CHINH `runner.load_q0_receipt` tung truong, cleanup finally. Toan bo fake: khong SQL, khong process that. Hash runner/observer lay tu CHINH tep da ghim (exec-hash)."""
from __future__ import annotations

import datetime as dt
import hashlib
import importlib.util
import json
import os
import subprocess
from pathlib import Path

import pytest

from test_operator_q0 import FakeWorld

RUN_REAL = Path(r"D:\MSE\CAPSTONE\outputs\repair-run-20261009")
OPERATOR = Path(os.environ.get("Q0VAR_OPERATOR_UNDER_TEST", str(RUN_REAL / "operator_q0var.py")))                  # ghi de CHI de chay mutation harness
DRIVER = RUN_REAL / "run_q0var_workflow.py"
REAL_Q0_RECEIPT = RUN_REAL / "q0" / "q0_receipt.json"
NOW = dt.datetime(2026, 10, 9, 14, 0, tzinfo=dt.timezone.utc)
SELFTEST_KEYS = ["innodb_trx_readable", "pid_attribute_visible", "removed_after_close", "visible_db_null_session", "visible_other_session"]


def load(path: Path, name: str):
    spec = importlib.util.spec_from_file_location(name, path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def text(t: dt.datetime) -> str:
    return t.strftime("%Y-%m-%dT%H:%M:%SZ")


class Q0Var:
    def __init__(self, tmp_path, monkeypatch, name="repair-run-q0a-r120"):
        self.mod = load(OPERATOR, "operator_q0var_under_test")
        mod = self.mod
        self.root = tmp_path / "root"
        (self.root / "outputs").mkdir(parents=True)
        monkeypatch.setattr(mod, "ROOT", self.root)                                                              # regex/parent check dung ROOT\outputs => run-root gia nam trong tmp
        self.run = self.root / "outputs" / name
        self.run.mkdir()
        self.world = FakeWorld(mod, self.run, monkeypatch)
        monkeypatch.setattr(mod, "now", lambda: NOW + dt.timedelta(seconds=self.world.t - self.world.t0))
        self.tmp, self.monkeypatch = tmp_path, monkeypatch
        self.windows = {"not_before_utc": text(NOW - dt.timedelta(minutes=5)), "start_deadline_utc": text(NOW + dt.timedelta(minutes=55)), "hard_not_after_utc": text(NOW + dt.timedelta(hours=5))}
        self.receipt = self.run / "q0" / "q0_receipt.json"
        self.freeze = self.make_freeze()
        self.freeze_path, self.freeze_sha = self.write_freeze(self.freeze)

    def file(self, name, content=b"x"):
        path = self.tmp / name
        path.write_bytes(content)
        return path

    def make_freeze(self):
        mod, run = self.mod, self.run
        (run / "q0" / "plan").mkdir(parents=True)
        spec = {"name": "q0_selftest", "kind": "readonly", "argv": ["py", "q0.py", "--out", str(self.receipt)], "cwd": r"D:\MSE\CAPSTONE\hotel-price-intelligence\backend", "env": {"PATH": "p"},
                "required_files": {}, "deadline_s": 180.0, "outputs": [str(self.receipt)]}
        plan_sha = "c" * 64
        plan_file = self.file("q0.plan.json", json.dumps({"document": {"specs": [spec]}, "plan_sha256": plan_sha}).encode())
        approval = self.file("q0_approval.md", b"approval q0")
        plans = {"q0": {"plan_file": str(plan_file), "plan_file_sha256": hashlib.sha256(plan_file.read_bytes()).hexdigest(), "plan_sha256": plan_sha, "approval": str(approval),
                        "approval_sha256": hashlib.sha256(approval.read_bytes()).hexdigest(), "invocation_sha256": "a" * 64, "plan_dir": str(run / "q0" / "plan"), "launcher_logs": str(run / "q0" / "logs"),
                        "output": str(self.receipt), "total_budget_s": 600.0}}
        profile = self.file("profile.json", b"profile")
        pins = {name: {"path": str(path), "sha256": hashlib.sha256(Path(path).read_bytes()).hexdigest()} for name, path in mod.FIXED_PIN_PATHS.items()}
        return {"kind": "q0_freeze", "run_root": str(run), "windows": dict(self.windows), "plans": plans,
                "profile": {"path": str(profile), "sha256": hashlib.sha256(profile.read_bytes()).hexdigest(), "renew_seconds": 1200}, "pins": pins}

    def write_freeze(self, doc, name="q0v_freeze.json"):
        path = self.tmp / name
        data = json.dumps(doc, sort_keys=True).encode("utf-8")
        path.write_bytes(data)
        return path, hashlib.sha256(data).hexdigest()

    def load(self):
        return self.mod.load_freeze(self.freeze_path, self.freeze_sha)

    def started(self, wall_seconds=1500):
        self.world.append({"event": "started", "pid": 1, "at_utc": NOW.strftime("%Y-%m-%dT%H:%M:%S.%fZ"), "wall_deadline_utc": text(NOW + dt.timedelta(seconds=wall_seconds))})

    def evidence(self, **kw):
        state = {"present": True, "age_s": 10, "active_runs": 0, "active_items": 0, "read_only_verified": True, "latest_run_id": 66, "latest_run_status": "completed", "sha256": "x" * 64,
                 "generator_sha256": self.mod.CFG["pins"]["source"]["sha256"]}
        state.update(kw)
        self.monkeypatch.setattr(self.mod, "evidence_state", lambda: dict(state))

    def good_q0_receipt(self, *, age_s=0):
        mod = self.mod
        observer = mod.observer_module()
        result = {**{k: True for k in SELFTEST_KEYS}, "server": {"server_uuid": "92d7a052-0723-11f1-8a6b-a82bdd5416bb", "uptime_s": 1000.0, "version": "8.0.45"},
                  "details": {"app_user": "root", "databases": ["hotel_price_intel", "warehouse_20261004_3src"], "databases_truncated": False, "errors": [], "ids_increase": True, "probe_session_id": 743, "successive_ids": [744, 745]}}
        doc = observer.q0_receipt_document(result, now_utc=mod.now() - dt.timedelta(seconds=age_s))
        assert doc["kind"] == "q0_selftest_receipt"
        return doc

    def entry(self, *, check_ok=True, rc=0, q0_mutator=None, write_q0=True, raw_q0=None, age_s=0, receipt_tamper=None, bound_tamper=None, summary_tamper=None, extra_files=(), outputs_override=None,
              check_elapsed_s=0.0, invoke_exc=None, stdout_prefix=b"", hash_override=None, check_rc=None):
        calls = []
        rr = self.mod.runner()

        def fake_run(argv, **kwargs):
            calls.append((list(argv), kwargs))
            p = self.mod.CFG["plans"]["q0"]
            if argv[-1] == "--check-only":
                self.world.t += check_elapsed_s
                payload = {"ok": check_ok, "computed_plan_sha256": p["plan_sha256"], "computed_invocation_sha256": hash_override or p["invocation_sha256"]}
                return subprocess.CompletedProcess(argv, check_rc if check_rc is not None else (0 if check_ok else 1), stdout=(json.dumps(payload) + "\n").encode(), stderr=b"")
            if invoke_exc:
                raise invoke_exc
            directory = Path(p["plan_dir"])
            plan_doc = json.loads(Path(p["plan_file"]).read_text(encoding="utf-8"))["document"]
            spec = plan_doc["specs"][0]
            entry_sha = self.mod.CFG["pins"]["entry"]["sha256"]
            data = raw_q0
            if data is None and write_q0:
                doc = self.good_q0_receipt(age_s=age_s)
                if q0_mutator:
                    q0_mutator(doc)
                data = json.dumps(doc, indent=2, sort_keys=True).encode("utf-8")
            if data is not None:
                self.mod.Q0_RECEIPT.write_bytes(data)
            outputs = outputs_override if outputs_override is not None else ({str(self.mod.Q0_RECEIPT): hashlib.sha256(data).hexdigest()} if data is not None else {})
            bound = {"kind": "plan_bound", "approved_plan_sha256": p["plan_sha256"], "computed_plan_sha256": p["plan_sha256"], "entrypoint_sha256": entry_sha, "phases": ["q0_selftest"], **(bound_tamper or {})}
            (directory / "PLAN_BOUND.json").write_text(json.dumps(bound), encoding="utf-8")
            launch = {"exit_code": 0, "timed_out": False, "killed_tree": False, "aborted_by_gate": None, "contained": True}
            full = {"phase": "q0_selftest", "seq": 1, "verdict": "PASS", "reasons": [], "kind": "readonly", "prev_receipt_sha256": None, "approved_plan_sha256": p["plan_sha256"], "computed_plan_sha256": p["plan_sha256"],
                    "entrypoint_sha256": entry_sha, "spec_sha256": rr.spec_sha256(spec), "argv_sha256": rr.argv_sha256(spec), "q0_receipt_sha256": None, "launch": launch, "outputs": outputs, **(receipt_tamper or {})}
            (directory / "receipt_01_q0_selftest.json").write_text(json.dumps(full, indent=2, sort_keys=True), encoding="utf-8")
            canonical = rr.sha256_hex(rr.canonical_json(full))
            for extra in extra_files:
                (directory / extra).write_text("x", encoding="utf-8")
            summary = {"approved_plan_sha256": p["plan_sha256"], "computed_plan_sha256": p["plan_sha256"], "entrypoint_sha256": entry_sha, "invocation_sha256": p["invocation_sha256"], "ok": True,
                       "phases": [{"phase": "q0_selftest", "reasons": [], "receipt_path": "x", "receipt_sha256": canonical, "verdict": "PASS"}], **(summary_tamper or {})}
            return subprocess.CompletedProcess(argv, rc, stdout=stdout_prefix + json.dumps(summary).encode() + b"\r\n", stderr=b"")
        self.monkeypatch.setattr(self.mod.subprocess, "run", fake_run)
        return calls


@pytest.fixture
def pr(tmp_path, monkeypatch):
    env = Q0Var(tmp_path, monkeypatch)
    assert env.load() == []
    return env


def ready(pr):
    pr.started()
    pr.evidence()
    return pr.entry(stdout_prefix=b"line1\r\n")


# =========================================================================== load_freeze + run-root tham so
def test_a_valid_freeze_loads_and_the_argv_has_no_q0_receipt(pr):
    mod = pr.mod
    p = mod.CFG["plans"]["q0"]
    assert mod.approved_argv("q0") == [mod.PY_VENV, str(mod.ENTRY), "--approval", p["approval"], "--plan", p["plan_file"], "--plan-dir", p["plan_dir"], "--launcher-logs", p["launcher_logs"],
                                       "--crawl-evidence", str(mod.LIVE)]
    assert "q0" in mod.CFG["plans"] and set(mod.CFG["plans"]) == {"q0"} and mod.PLAN_ORDER == ("q0",) and mod.PLAN_PHASES == {"q0": ("q0_selftest",)} and "--q0-receipt" not in mod.approved_argv("q0")


@pytest.mark.parametrize("name", ["repair-run-q0a-r120", "repair-run-q0b-r121"])
def test_every_path_follows_the_run_root_from_the_freeze(tmp_path, monkeypatch, name):
    env = Q0Var(tmp_path, monkeypatch, name)
    assert env.load() == []
    mod, run = env.mod, env.run
    assert (mod.RUN, mod.LIVE, mod.SUP_LOG, mod.PUB_LOG, mod.STOP, mod.SUP_STDOUT, mod.OP_LOG, mod.PREFLIGHT, mod.ARCHIVE_DIR, mod.Q0_DIR, mod.Q0_RECEIPT) == (
        run, run / "live_crawl_idle_evidence.json", run / "q0v_supervisor.jsonl", run / "q0v_publisher.log", run / "q0v_STOP", run / "q0v_supervisor_stdout.log", run / "q0v_operator_log.jsonl",
        run / "q0v_dossier_preflight.md", run / "evidence_archive" / "q0v", run / "q0", run / "q0" / "q0_receipt.json")


def mutated(pr, fn):
    doc = json.loads(json.dumps(pr.freeze))
    fn(doc)
    path, digest = pr.write_freeze(doc, "mutated.json")
    return pr.mod.load_freeze(path, digest)


@pytest.mark.parametrize("name,fn,expected", [
    ("kind", lambda d: d.__setitem__("kind", "probe_freeze"), "freeze_kind_or_run_root"),
    ("kind_heavy", lambda d: d.__setitem__("kind", "heavy_ci_freeze"), "freeze_kind_or_run_root"),
    ("run_root_elsewhere", lambda d: d.__setitem__("run_root", r"D:\elsewhere\repair-run-q0a-r120"), "freeze_kind_or_run_root"),
    ("windows_order", lambda d: d["windows"].__setitem__("start_deadline_utc", d["windows"]["not_before_utc"]), "freeze_windows_order"),
    ("plan_key_missing", lambda d: d["plans"]["q0"].pop("output"), "freeze_plan_q0"),
    ("plan_relative", lambda d: d["plans"]["q0"].__setitem__("approval", "approval.md"), "freeze_plan_q0"),
    ("plan_budget_text", lambda d: d["plans"]["q0"].__setitem__("total_budget_s", "600"), "freeze_plan_q0"),
    ("plan_dir_elsewhere", lambda d: d["plans"]["q0"].__setitem__("plan_dir", r"D:\elsewhere\plan"), "freeze_plan_q0_dirs_not_in_run_root"),
    ("launcher_logs_elsewhere", lambda d: d["plans"]["q0"].__setitem__("launcher_logs", r"D:\elsewhere\logs"), "freeze_plan_q0_dirs_not_in_run_root"),
    ("output_elsewhere", lambda d: d["plans"]["q0"].__setitem__("output", r"D:\elsewhere\q0_receipt.json"), "freeze_plan_q0_output_not_the_q0_receipt"),
    ("pin_runner_redirected", lambda d: d["pins"]["runner"].__setitem__("path", r"D:\evil\runner.py"), "freeze_pin_runner"),
    ("pin_observer_redirected", lambda d: d["pins"]["observer_module"].__setitem__("path", r"D:\evil\observer.py"), "freeze_pin_observer_module"),
    ("pin_model_redirected", lambda d: d["pins"]["quiescence_model"].__setitem__("path", r"D:\evil\model.py"), "freeze_pin_quiescence_model"),
    ("pin_q0_script_redirected", lambda d: d["pins"]["q0_script"].__setitem__("path", r"D:\evil\q0.py"), "freeze_pin_q0_script"),
    ("pin_observer_missing", lambda d: d["pins"].pop("observer_module"), "freeze_pin_observer_module"),
    ("pin_entry_short_sha", lambda d: d["pins"]["entry"].__setitem__("sha256", "abc"), "freeze_pin_entry"),
    ("pins_not_a_dict", lambda d: d.__setitem__("pins", ["x"]), "freeze_pins"),
    ("pins_empty_list", lambda d: d.__setitem__("pins", []), "freeze_pin_runner"),
    ("profile_bad", lambda d: d["profile"].__setitem__("sha256", "short"), "freeze_profile"),
    ("profile_renew_text", lambda d: d["profile"].__setitem__("renew_seconds", "1200"), "freeze_profile"),
])
def test_every_malformed_or_redirected_freeze_is_refused_and_leaves_cfg_empty(pr, name, fn, expected):
    assert expected in mutated(pr, fn) and pr.mod.CFG == {}


def test_a_windows_field_that_is_missing_or_unparsable_is_refused(pr):
    assert mutated(pr, lambda d: d["windows"].pop("hard_not_after_utc")) == ["freeze_windows"] and pr.mod.CFG == {}
    assert mutated(pr, lambda d: d["windows"].__setitem__("not_before_utc", "yesterday")) == ["freeze_windows"] and pr.mod.CFG == {}


@pytest.mark.parametrize("name", ["repair-run-20261009", "repair-run-q0c-r120", "repair-run-q0a-r12", "repair-run-q0a-r1200", "repair-run-q0a-r120-x", "x-repair-run-q0b-r121", "repair-run-probe-r105"])
def test_only_q0a_q0b_run_roots_are_accepted_and_a_refusal_never_reconfigures_the_module(pr, name):
    before = (pr.mod.RUN, pr.mod.STOP, pr.mod.Q0_RECEIPT)
    pr.mod.CFG.clear()
    problems = mutated(pr, lambda d: d.__setitem__("run_root", str(pr.root / "outputs" / name)))
    assert problems == ["freeze_kind_or_run_root"] and pr.mod.CFG == {} and (pr.mod.RUN, pr.mod.STOP, pr.mod.Q0_RECEIPT) == before


def test_the_real_executed_run_root_is_not_a_valid_q0var_run_root():
    mod = load(OPERATOR, "operator_q0var_for_real_root")
    assert not mod.RUN_NAME_RE.match(RUN_REAL.name) and RUN_REAL.parent == mod.ROOT / "outputs"


def test_a_wrong_sha_or_unreadable_or_non_json_freeze_is_refused(pr):
    assert pr.mod.load_freeze(pr.freeze_path, "0" * 64) == ["freeze_sha256_mismatch"] and pr.mod.CFG == {}
    assert pr.mod.load_freeze(pr.tmp / "nope.json", pr.freeze_sha) == ["freeze_unreadable"]
    junk = pr.tmp / "junk.json"
    junk.write_bytes(b"{nope")
    assert pr.mod.load_freeze(junk, hashlib.sha256(b"{nope").hexdigest()) == ["freeze_not_json"]
    arr = pr.tmp / "arr.json"
    arr.write_bytes(b"[]")
    assert pr.mod.load_freeze(arr, hashlib.sha256(b"[]").hexdigest()) == ["freeze_kind_or_run_root"]


def test_log_before_a_freeze_only_prints_and_never_creates_a_directory(capsys):
    mod = load(OPERATOR, "operator_q0var_unset")
    assert not mod.RUN.exists()
    mod.log("early", x=1)
    assert not mod.RUN.exists() and '"event": "early"' in capsys.readouterr().out


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
    Path(pr.mod.CFG["plans"]["q0"]["approval"]).write_bytes(b"tampered")
    pr.receipt.write_text("old", encoding="utf-8")
    (pr.run / "q0v_STOP").write_text("x", encoding="utf-8")
    (pr.run / "q0" / "plan" / "leftover.json").write_text("x", encoding="utf-8")
    (pr.run / "q0" / "logs").mkdir()
    (pr.run / "q0v_q0_entry_stdout.json").write_text("x", encoding="utf-8")
    monkeypatch.setenv("DB_HOST", "x")
    problems = pr.mod.preconditions()
    for needle in ("sha_mismatch: q0_approval", "already_exists: q0_receipt.json", "already_exists: q0v_STOP", "already_exists: logs", "already_exists: q0v_q0_entry_stdout.json", "plan_dir_not_empty: q0", "shell_inherits_DB_vars"):
        assert needle in problems


@pytest.mark.parametrize("pin", ["observer_module", "quiescence_model", "q0_script", "runner", "entry", "supervisor", "source", "interpreter", "parent_interpreter"])
def test_a_drifted_pinned_file_blocks_the_start(pr, monkeypatch, pin):
    clean_probe(pr, monkeypatch)
    pr.mod.CFG["pins"][pin]["sha256"] = "0" * 64
    assert f"sha_mismatch: {pin}" in pr.mod.preconditions()


def test_preconditions_refuse_when_no_freeze_is_loaded(pr):
    pr.mod.CFG.clear()
    assert pr.mod.preconditions() == ["freeze_not_loaded"]


def test_the_old_executed_run_files_are_never_confused_with_this_workflows_files(pr):
    ready(pr)
    pr.mod.cmd_plans()
    names = {p.name for p in pr.run.iterdir()}
    assert "q0v_q0_entry_stdout.json" in names and "q0v_q0_entry_stderr.txt" in names and "q0v_operator_log.jsonl" in names
    assert not ({"q0_entry_stdout.json", "q0_operator_log.jsonl", "q0_STOP", "q0_publisher.log", "q0_supervisor.jsonl"} & names)


# =========================================================================== plan thanh cong + cong
def test_the_plan_runs_one_check_only_then_one_invocation_with_exact_argv_timeouts_and_byte_exact_outputs(pr):
    calls = ready(pr)
    assert pr.mod.cmd_plans() == 0
    assert [a[-1] == "--check-only" for a, _ in calls] == [True, False]
    for argv, kw in calls:
        assert argv == pr.mod.approved_argv("q0") + (["--check-only"] if argv[-1] == "--check-only" else []) and kw["env"] == pr.mod.ENV6 and kw["cwd"] == str(pr.mod.ROOT)
        assert kw["capture_output"] is True and kw["text"] is False
    assert calls[0][1]["timeout"] == 120 and calls[1][1]["timeout"] == 600 + 120                                # wrapper 720 s = budget+margin, KHONG phai deadline 180 s cua outer Job
    written = (pr.run / "q0v_q0_entry_stdout.json").read_bytes()
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


def test_a_check_only_that_exits_nonzero_means_no_invocation_even_if_its_json_says_ok(pr):
    pr.started()
    pr.evidence()
    calls = pr.entry(check_ok=True, check_rc=1)
    assert pr.mod.cmd_plans() == 2 and len(calls) == 1 and pr.world.stopped()


def test_a_plan_file_with_an_extra_or_renamed_phase_is_never_a_pass(pr):
    ready(pr)
    assert pr.mod.cmd_plans() == 0
    plan_file = Path(pr.mod.CFG["plans"]["q0"]["plan_file"])
    doc = json.loads(plan_file.read_text(encoding="utf-8"))
    doc["document"]["specs"].append(dict(doc["document"]["specs"][0], name="extra_phase"))
    plan_file.write_text(json.dumps(doc), encoding="utf-8")
    assert pr.mod.plan_receipts_pass("q0") == ["plan_file_phase_order_differs_from_expected"]


def test_ensure_stopped_does_nothing_for_a_publisher_that_was_refused_before_spawning(pr):
    pr.world.append({"event": "refused", "reasons": ["outside_window"], "stage": "preflight"})
    assert pr.mod.publisher_state() == "refused" and pr.mod.ensure_stopped("x") == 0 and not pr.mod.STOP.exists() and "stop_not_needed" in pr.world.op_events()


def test_gates_are_rechecked_right_before_the_invocation(pr, monkeypatch):
    pr.started(wall_seconds=90 + 720 + 100)
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
    assert pr.mod.run_plan("q0", {"at_utc": NOW.strftime("%Y-%m-%dT%H:%M:%S.%fZ"), "wall_deadline_utc": text(NOW + dt.timedelta(seconds=1500))}, first=True) == 2 and calls == []


def test_the_hard_not_after_limit_refuses_with_its_own_reason(pr):
    pr.evidence()
    calls = pr.entry()
    pr.mod.CFG["windows"]["hard_not_after_utc"] = NOW - dt.timedelta(seconds=1)
    assert pr.mod.run_plan("q0", {"at_utc": NOW.strftime("%Y-%m-%dT%H:%M:%S.%fZ"), "wall_deadline_utc": text(NOW + dt.timedelta(seconds=1500))}, first=True) == 2 and calls == []
    assert "hard_not_after_passed" in pr.mod.OP_LOG.read_text(encoding="utf-8")


def test_an_existing_q0_receipt_or_a_missing_q0_dir_refuses_the_invocation(pr):
    pr.started()
    pr.evidence()
    calls = pr.entry()
    pr.receipt.write_text("old", encoding="utf-8")
    assert pr.mod.cmd_plans() == 2 and [a[-1] for a, _ in calls] == ["--check-only"] and "q0_receipt_exists_or_q0_dir_missing" in pr.mod.OP_LOG.read_text(encoding="utf-8")


def test_a_missing_q0_dir_refuses_the_invocation(pr, tmp_path):
    pr.started()
    pr.evidence()
    calls = pr.entry()
    plan_dir = pr.run / "q0" / "plan"
    plan_dir.rmdir()
    plan_dir.parent.rmdir()
    assert pr.mod.cmd_plans() == 2 and [a[-1] for a, _ in calls] == ["--check-only"]


# =========================================================================== sau invocation: receipt + Q0 receipt (GPT 106 + 114)
RECEIPT_TAMPER = [
    ("foreign_plan", {"approved_plan_sha256": "f" * 64}), ("wrong_prev", {"prev_receipt_sha256": "0" * 64}), ("wrong_argv", {"argv_sha256": "e" * 64}), ("wrong_spec", {"spec_sha256": "e" * 64}),
    ("wrong_seq", {"seq": 2}), ("write_kind", {"kind": "write"}), ("q0_pin_present", {"q0_receipt_sha256": "9" * 64}), ("wrong_entry", {"entrypoint_sha256": "7" * 64}),
    ("timed_out", {"launch": {"exit_code": 0, "timed_out": True, "killed_tree": False, "aborted_by_gate": None, "contained": True}}),
    ("killed_tree", {"launch": {"exit_code": 0, "timed_out": False, "killed_tree": True, "aborted_by_gate": None, "contained": True}}),
    ("not_contained", {"launch": {"exit_code": 0, "timed_out": False, "killed_tree": False, "aborted_by_gate": None, "contained": False}}),
    ("aborted_by_gate", {"launch": {"exit_code": 0, "timed_out": False, "killed_tree": False, "aborted_by_gate": "crawler", "contained": True}}),
    ("nonzero_exit", {"launch": {"exit_code": 3, "timed_out": False, "killed_tree": False, "aborted_by_gate": None, "contained": True}}),
    ("verdict", {"verdict": "QUARANTINE"}), ("reasons", {"reasons": ["x"]}), ("wrong_phase", {"phase": "other"}),
]


@pytest.mark.parametrize("name,tamper", RECEIPT_TAMPER, ids=[n for n, _ in RECEIPT_TAMPER])
def test_a_receipt_that_is_not_the_genuine_runner_shape_is_a_failure_even_when_the_q0_receipt_is_good(pr, name, tamper):
    pr.started()
    pr.evidence()
    pr.entry(receipt_tamper=tamper)
    assert pr.mod.cmd_plans() == 5 and "plan_not_pass" in pr.world.op_events() and pr.world.stopped()


@pytest.mark.parametrize("kwargs", [
    dict(bound_tamper={"entrypoint_sha256": "7" * 64}), dict(bound_tamper={"phases": ["other"]}), dict(summary_tamper={"invocation_sha256": "0" * 64}), dict(summary_tamper={"ok": False}),
    dict(summary_tamper={"phases": []}), dict(extra_files=["QUARANTINE.json"]), dict(rc=3),
    dict(summary_tamper={"phases": [{"phase": "q0_selftest", "reasons": [], "receipt_path": "x", "receipt_sha256": "0" * 64, "verdict": "PASS"}]}),
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


def set_detail(key, value):
    return lambda d: d["details"].__setitem__(key, value)


Q0_BAD = [
    ("kind", mutate(kind="other")), ("observer_pin", mutate(observer_script_sha256="0" * 64)), ("sql_set_pin", mutate(sql_set_sha256="0" * 64)), ("model_pin", mutate(quiescence_model_sha256="0" * 64)),
    *[(f"selftest_{k}_false", (lambda key: lambda d: d["selftest"].__setitem__(key, False))(k)) for k in SELFTEST_KEYS],
    *[(f"selftest_{k}_truthy_not_true", (lambda key: lambda d: d["selftest"].__setitem__(key, 1))(k)) for k in SELFTEST_KEYS[:2]],
    ("selftest_missing", lambda d: d.pop("selftest")), ("selftest_key_removed", lambda d: d["selftest"].pop("removed_after_close")),
    ("server_missing", lambda d: d.pop("server")), ("server_uuid_empty", lambda d: d["server"].__setitem__("server_uuid", "")), ("server_uptime_negative", lambda d: d["server"].__setitem__("uptime_s", -1.0)),
    ("server_uptime_none", lambda d: d["server"].__setitem__("uptime_s", None)),
    ("ids_not_increasing", set_detail("successive_ids", [745, 744])), ("ids_equal", set_detail("successive_ids", [744, 744])), ("ids_three", set_detail("successive_ids", [1, 2, 3])),
    ("ids_zero", set_detail("successive_ids", [0, 1])), ("ids_bool", set_detail("successive_ids", [True, 2])), ("ids_increase_false", set_detail("ids_increase", False)),
    ("app_user_empty", set_detail("app_user", "")), ("app_user_missing", lambda d: d["details"].pop("app_user")), ("databases_truncated", set_detail("databases_truncated", True)),
    ("databases_empty", set_detail("databases", [])), ("databases_non_str", set_detail("databases", ["a", 1])), ("details_missing", lambda d: d.pop("details")),
    ("created_malformed", mutate(created_at_utc="yesterday")), ("created_missing", lambda d: d.pop("created_at_utc")),
]


@pytest.mark.parametrize("name,mutator", Q0_BAD, ids=[n for n, _ in Q0_BAD])
def test_a_q0_receipt_that_fails_load_q0_receipt_is_a_failure_even_with_a_perfect_receipt_chain(pr, name, mutator):
    pr.started()
    pr.evidence()
    pr.entry(q0_mutator=mutator)
    assert pr.mod.cmd_plans() == 5 and "plan_not_pass" in pr.world.op_events() and "q0_receipt_invalid" in pr.mod.OP_LOG.read_text(encoding="utf-8") and pr.world.stopped()


@pytest.mark.parametrize("age_s,ok", [(0, True), (1800, True), (3590, True), (3700, False), (7200, False), (-300, False)], ids=["fresh", "half_hour", "just_under_limit", "stale", "very_stale", "future"])
def test_the_q0_receipt_must_be_fresh_and_not_from_the_future(pr, age_s, ok):
    pr.started()
    pr.evidence()
    pr.entry(age_s=age_s)
    assert (pr.mod.cmd_plans() == 0) is ok
    if not ok:
        assert "q0_receipt_invalid" in pr.mod.OP_LOG.read_text(encoding="utf-8")


@pytest.mark.parametrize("kwargs,why", [
    (dict(write_q0=False), "q0_receipt_missing"),
    (dict(raw_q0=b"{partial"), "q0_receipt_invalid"),
    (dict(outputs_override={}), "q0_receipt_hash_differs_from_receipt_outputs"),
    (dict(outputs_override={"x": "0" * 64}), "q0_receipt_hash_differs_from_receipt_outputs"),
])
def test_missing_partial_or_unbound_q0_receipts_are_failures(pr, kwargs, why):
    pr.started()
    pr.evidence()
    pr.entry(**kwargs)
    assert pr.mod.cmd_plans() == 5 and why in pr.mod.OP_LOG.read_text(encoding="utf-8")


def test_a_q0_receipt_whose_hash_differs_from_the_receipt_outputs_is_refused_even_if_the_receipt_is_valid(pr):
    pr.started()
    pr.evidence()
    pr.entry(outputs_override={str(pr.receipt): "0" * 64})
    assert pr.mod.cmd_plans() == 5 and "q0_receipt_hash_differs_from_receipt_outputs" in pr.mod.OP_LOG.read_text(encoding="utf-8")


def test_an_observer_that_differs_from_the_freeze_pin_cannot_validate_the_q0_receipt(pr):
    pr.started()
    pr.evidence()
    good = json.dumps(pr.good_q0_receipt(), indent=2, sort_keys=True).encode("utf-8")                              # tao receipt hop le TRUOC khi pin lech (fake invocation khong duoc dung observer da hong)
    pr.entry(raw_q0=good)
    pr.mod.CFG["pins"]["observer_module"]["sha256"] = "0" * 64
    pr.mod._OBSERVER = None
    assert pr.mod.cmd_plans() == 5 and "q0_receipt_invalid: RuntimeError" in pr.mod.OP_LOG.read_text(encoding="utf-8")


def test_a_runner_that_differs_from_the_freeze_pin_cannot_validate_the_receipt_chain(pr):
    pr.started()
    pr.evidence()
    pr.entry()
    pr.mod.CFG["pins"]["runner"]["sha256"] = "0" * 64
    pr.mod._RUNNER = None
    assert pr.mod.cmd_plans() == 5 and "receipt_chain_unreadable" in pr.mod.OP_LOG.read_text(encoding="utf-8")


def test_the_good_fixture_is_accepted_end_to_end(pr):
    ready(pr)
    assert pr.mod.cmd_plans() == 0 and pr.mod.q0_receipt_problems("q0") == [] and pr.mod.plan_receipts_pass("q0") == []


@pytest.mark.skipif(not REAL_Q0_RECEIPT.exists(), reason="executed Q0 receipt not present")
def test_the_genuinely_produced_q0_receipt_of_the_executed_run_passes_the_validation_when_fresh(pr):
    """Receipt THAT do `repair_q0_v1` tao ngay 09/10 (hash 1989ca93…) phai qua cung kiem tra nhu receipt gia — chung minh khong lech dinh dang. Dong ho ao dat vao 60 s sau created_at."""
    real = REAL_Q0_RECEIPT.read_bytes()
    created = dt.datetime.strptime(json.loads(real.decode("utf-8"))["created_at_utc"], "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=dt.timezone.utc)
    pr.monkeypatch.setattr(pr.mod, "now", lambda: created + dt.timedelta(seconds=60))
    pr.mod.Q0_RECEIPT.write_bytes(real)
    (pr.run / "q0" / "plan" / "receipt_01_q0_selftest.json").write_text(json.dumps({"outputs": {str(pr.mod.Q0_RECEIPT): hashlib.sha256(real).hexdigest()}}), encoding="utf-8")
    assert pr.mod.q0_receipt_problems("q0") == []
    pr.monkeypatch.setattr(pr.mod, "now", lambda: created + dt.timedelta(seconds=3601))                          # cung receipt that nhung da het han => tu choi
    assert any("q0_receipt_invalid" in p for p in pr.mod.q0_receipt_problems("q0"))


@pytest.mark.parametrize("exc", [KeyboardInterrupt, RuntimeError, subprocess.TimeoutExpired])
def test_exception_or_cancel_in_the_invocation_stops_the_publisher_and_propagates(pr, exc):
    pr.started()
    pr.evidence()
    pr.entry(invoke_exc=exc("x") if exc is not subprocess.TimeoutExpired else subprocess.TimeoutExpired("entry", 720))
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


class FakeSup:
    pid = 77

    def poll(self):
        return None


def test_cmd_start_launches_the_supervisor_with_exactly_the_frozen_profile_pins_and_window(pr, monkeypatch):
    clean_probe(pr, monkeypatch)
    launched = []

    def fake_popen(command, **kw):
        launched.append((command, kw))
        return FakeSup()
    monkeypatch.setattr(pr.mod.subprocess, "Popen", fake_popen)
    pr.world.timeline.append((1, {"event": "started", "pid": 1, "at_utc": NOW.strftime("%Y-%m-%dT%H:%M:%S.%fZ"), "wall_deadline_utc": text(NOW + dt.timedelta(seconds=1500))}))
    assert pr.mod.cmd_start() == 0 and len(launched) == 1
    command, kw = launched[0]
    cfg, mod = pr.mod.CFG, pr.mod
    expected = [mod.PY_SUP, str(mod.SUPERVISOR), "--profile", cfg["profile"]["path"], "--expected-profile-sha256", cfg["profile"]["sha256"], "--expected-source-sha256", cfg["pins"]["source"]["sha256"],
                "--expected-interpreter-sha256", cfg["pins"]["interpreter"]["sha256"], "--not-before-utc", pr.windows["not_before_utc"], "--start-deadline-utc", pr.windows["start_deadline_utc"],
                "--hard-not-after-utc", pr.windows["hard_not_after_utc"], "--max-pool-size", "5", "--supervisor-log", str(mod.SUP_LOG), "--publisher-log", str(mod.PUB_LOG), "--stop-file", str(mod.STOP)]
    assert command == expected and kw["env"] == mod.ENV6 and kw["cwd"] == str(mod.ROOT) and kw["stdin"] == subprocess.DEVNULL and kw["stderr"] == subprocess.STDOUT and kw["creationflags"] == 0x08000000
    assert "shell" not in kw and pr.freeze_sha in mod.PREFLIGHT.read_text(encoding="utf-8") and str(pr.run) in mod.PREFLIGHT.read_text(encoding="utf-8")


def test_cmd_start_refuses_without_launching_anything_when_a_precondition_fails(pr, monkeypatch):
    clean_probe(pr, monkeypatch, pids=[9])
    monkeypatch.setattr(pr.mod.subprocess, "Popen", lambda *a, **k: (_ for _ in ()).throw(AssertionError("must not launch")))
    assert pr.mod.cmd_start() == 2 and not pr.mod.PREFLIGHT.exists() and not pr.mod.SUP_STDOUT.exists()


@pytest.mark.parametrize("kwargs", [dict(active_runs=1), dict(active_items=2), dict(read_only_verified=False), dict(age_s=500), dict(generator_sha256="f" * 64)])
def test_cmd_evidence_stops_the_started_publisher_when_evidence_is_busy_stale_or_foreign(pr, kwargs):
    pr.started()
    pr.evidence(**kwargs)
    assert pr.mod.cmd_evidence() == 1 and pr.mod.STOP.exists() and pr.world.stopped()


def test_cmd_evidence_with_clean_evidence_keeps_the_publisher_running(pr):
    pr.started()
    pr.evidence()
    assert pr.mod.cmd_evidence() == 0 and not pr.world.stopped() and not pr.mod.STOP.exists()


def test_cmd_evidence_without_a_running_publisher_is_not_applicable_and_cleans_up(pr):
    pr.evidence()
    assert pr.mod.cmd_evidence() == 1 and "evidence_not_applicable" in pr.world.op_events()


def test_a_stop_that_never_produces_a_verified_dead_publisher_is_nonzero(pr):
    pr.started()
    pr.world.stopped_after_stop_file = False
    assert pr.mod.ensure_stopped("x") == 3 and "publisher_stop_timeout" in pr.world.op_events()
    pr.world.stopped_after_stop_file = True
    pr.world.verified_dead = False
    assert pr.mod.ensure_stopped("y") == 3


def test_collect_lists_the_files_of_the_run_root_only(pr):
    ready(pr)
    pr.mod.cmd_plans()
    assert pr.mod.cmd_collect() == 0
    logged = [json.loads(x) for x in pr.mod.OP_LOG.read_text(encoding="utf-8").splitlines() if '"event": "collected"' in x]
    assert logged and "q0\\q0_receipt.json" in logged[-1]["files"] and "q0v_q0_entry_stdout.json" in logged[-1]["files"]


# =========================================================================== driver
@pytest.fixture
def driver():
    return load(DRIVER, "run_q0var_workflow_under_test")


class FakeOp:
    __file__ = str(OPERATOR)

    def __init__(self, run: Path, **rcs):
        self.RUN = run
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
    fake = FakeOp(tmp_path, **rcs)
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


def test_driver_cleanup_exception_is_recorded_and_still_collects(driver, tmp_path):
    code, fake, result = go(driver, tmp_path, ensure_stopped=OSError("x"))
    assert code == 3 and fake.calls[-2:] == ["ensure_stopped", "cmd_collect"] and result["cleanup_exception"] == "OSError"


def test_driver_result_write_failure_cannot_turn_a_failure_into_success(driver, tmp_path):
    folder = tmp_path / "ro"
    folder.mkdir()
    fake = FakeOp(folder)
    code = driver.run_workflow(fake, freeze_path=folder / "f.json", freeze_sha256="0" * 64, ack=lambda m, p: p.write_text("ack"), result_path=folder / "missing_dir" / "result.json", ack_path=folder / "ack.md", pre_checks=False)
    assert code != 0                                                                                             # khong ghi duoc ket qua => van nonzero


def test_driver_refuses_on_operator_pin_drift_or_a_previous_run(driver, tmp_path):
    fake = FakeOp(tmp_path)
    kwargs = dict(freeze_path=tmp_path / "f.json", freeze_sha256="0" * 64, ack=lambda m, p: p.write_text("a"), result_path=tmp_path / "r.json", ack_path=tmp_path / "a.md")
    driver.OPERATOR_Q0VAR_SHA256 = "0" * 64
    assert driver.run_workflow(fake, **kwargs) == 2 and fake.calls == []
    driver.OPERATOR_Q0VAR_SHA256 = hashlib.sha256(OPERATOR.read_bytes()).hexdigest()
    (tmp_path / "a.md").write_text("already")
    assert driver.run_workflow(fake, **kwargs) == 2 and fake.calls == ["load_freeze"]                              # pin dung => nap freeze (dat run-root) roi moi phat hien da chay


def test_driver_derives_ack_and_result_paths_from_the_freeze_run_root_after_loading_it(driver, tmp_path):
    fake = FakeOp(tmp_path)
    acks = []
    code = driver.run_workflow(fake, freeze_path=tmp_path / "f.json", freeze_sha256="0" * 64, ack=lambda m, p: (acks.append(p), p.write_text("ack")), pre_checks=False)
    assert code == 0 and acks == [tmp_path / "q0v_dossier_outer_cleanup_ack.md"] and (tmp_path / "q0v_workflow_result.json").exists()
    assert not (tmp_path / "probe_workflow_result.json").exists() and not (tmp_path / "q0_workflow_result.json").exists()


@pytest.mark.skipif("Q0VAR_OPERATOR_UNDER_TEST" in os.environ, reason="mutation run uses a different operator file")
def test_driver_pin_constant_equals_the_sha256_of_the_real_operator(driver):
    assert driver.OPERATOR_Q0VAR_SHA256 == hashlib.sha256((RUN_REAL / "operator_q0var.py").read_bytes()).hexdigest()


def test_driver_ack_records_pins_and_the_cleanup_contract(driver, pr, tmp_path):
    out = tmp_path / "ack.md"
    driver.write_ack(pr.mod, out, driver_path=str(DRIVER))
    body = out.read_text(encoding="utf-8")
    assert pr.freeze_sha in body and hashlib.sha256(DRIVER.read_bytes()).hexdigest() in body and "ensure_stopped" in body and "collect" in body and str(pr.run) in body
    assert hashlib.sha256(OPERATOR.read_bytes()).hexdigest() in body                                             # ack ghi SHA operator that dang chay (m.__file__)

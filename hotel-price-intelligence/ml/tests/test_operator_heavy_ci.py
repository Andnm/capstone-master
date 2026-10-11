"""operator_heavy_ci + run_heavy_ci_workflow (phuong an C): freeze manifest, probe crawler fail-closed, hai plan tuan tu co gate (init CHI sau clone PASS that), cleanup finally. Toan bo fake: khong SQL, khong process that."""
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
OPERATOR = Path(os.environ.get("HEAVY_CI_OPERATOR_UNDER_TEST", str(RUN_REAL / "operator_heavy_ci.py")))                    # ghi de CHI de chay mutation harness
DRIVER = RUN_REAL / "run_heavy_ci_workflow.py"
NOW = dt.datetime(2026, 10, 9, 12, 35, tzinfo=dt.timezone.utc)
U = dt.timezone.utc


def load(path: Path, name: str):
    spec = importlib.util.spec_from_file_location(name, path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def text(t: dt.datetime) -> str:
    return t.strftime("%Y-%m-%dT%H:%M:%SZ")


class Ci:
    """Moi truong test: module da patch vao run-root tam + freeze hop le + FakeWorld (dong ho ao)."""

    def __init__(self, tmp_path, monkeypatch):
        self.mod = load(OPERATOR, "operator_heavy_ci_under_test")
        mod = self.mod
        self.run = tmp_path / "run"
        self.run.mkdir()
        run = self.run
        monkeypatch.setattr(mod, "RUN", run)
        for name, rel in (("LIVE", "live.json"), ("SUP_LOG", "ci_supervisor.jsonl"), ("PUB_LOG", "ci_publisher.log"), ("STOP", "ci_STOP"), ("SUP_STDOUT", "ci_supervisor_stdout.log"),
                          ("OP_LOG", "ci_operator_log.jsonl"), ("PREFLIGHT", "ci_dossier_preflight.md"), ("ARCHIVE_DIR", "evidence_archive/ci"), ("H2_DIR", "h2"), ("H2_REPORTS", "h2/reports")):
            monkeypatch.setattr(mod, name, run / rel)
        self.world = FakeWorld(mod, run, monkeypatch)
        monkeypatch.setattr(mod, "now", lambda: NOW + dt.timedelta(seconds=self.world.t - self.world.t0))
        self.tmp = tmp_path
        self.monkeypatch = monkeypatch
        self.windows = {"not_before_utc": text(NOW - dt.timedelta(minutes=5)), "start_deadline_utc": text(NOW + dt.timedelta(minutes=55)), "hard_not_after_utc": text(NOW + dt.timedelta(hours=5))}
        self.freeze = self.make_freeze()
        self.freeze_path, self.freeze_sha = self.write_freeze(self.freeze)

    def file(self, name, content=b"x"):
        path = self.tmp / name
        path.write_bytes(content)
        return path

    def spec_for(self, name, kind):
        return {"name": name, "kind": kind, "argv": ["py", f"{name}.py", "--db", "x"], "cwd": r"D:\MSE\CAPSTONE\hotel-price-intelligence\ml", "env": {"PATH": "p"}, "required_files": {}, "deadline_s": 300.0}

    def make_freeze(self):
        mod, run = self.mod, self.run
        plans = {}
        kinds = {"ph3_clone": "write", "ph4_init_h1": "write", "ph4_verify_h1": "readonly", "ph4_init_h3": "write", "ph4_verify_h3": "readonly"}
        for key, budget, plan_sha, inv in (("clone", 1260.0, "c" * 64, "a" * 64), ("init", 1380.0, "d" * 64, "b" * 64)):
            (run / key / "plan").mkdir(parents=True)
            specs = [self.spec_for(name, kinds[name]) for name in mod.PLAN_PHASES[key]]
            plan_file = self.file(f"{key}.plan.json", json.dumps({"document": {"specs": specs}, "plan_sha256": plan_sha}).encode())
            approval = self.file(f"{key}_approval.md", f"approval {key}".encode())
            plans[key] = {"plan_file": str(plan_file), "plan_file_sha256": hashlib.sha256(plan_file.read_bytes()).hexdigest(), "plan_sha256": plan_sha, "approval": str(approval),
                          "approval_sha256": hashlib.sha256(approval.read_bytes()).hexdigest(), "invocation_sha256": inv, "plan_dir": str(run / key / "plan"), "launcher_logs": str(run / key / "logs"),
                          "total_budget_s": budget}
        q0 = self.file("q0_receipt.json", b'{"kind": "q0_selftest_receipt"}')
        profile = self.file("profile.json", b"profile")
        pins = {name: {"path": str(path), "sha256": hashlib.sha256(Path(path).read_bytes()).hexdigest()} for name, path in mod.FIXED_PIN_PATHS.items()}
        return {"kind": "heavy_ci_freeze", "run_root": str(run), "windows": dict(self.windows), "q0_receipt": {"path": str(q0), "sha256": hashlib.sha256(q0.read_bytes()).hexdigest(), "created_at_utc": text(NOW - dt.timedelta(minutes=5))},
                "plans": plans, "profile": {"path": str(profile), "sha256": hashlib.sha256(profile.read_bytes()).hexdigest(), "renew_seconds": 3300}, "pins": pins,
                "h2_reports": [str(run / "h2" / "reports" / "verify_a.json"), str(run / "h2" / "reports" / "verify_b.json")]}

    def write_freeze(self, doc, name="ci_freeze.json"):
        path = self.run / name
        data = json.dumps(doc, sort_keys=True).encode("utf-8")
        path.write_bytes(data)
        return path, hashlib.sha256(data).hexdigest()

    def load(self):
        return self.mod.load_freeze(self.freeze_path, self.freeze_sha)

    # ---- tien trinh gia
    def started(self, wall_seconds=3000):
        self.world.append({"event": "started", "pid": 1, "at_utc": NOW.strftime("%Y-%m-%dT%H:%M:%S.%fZ"), "wall_deadline_utc": text(NOW + dt.timedelta(seconds=wall_seconds))})

    def evidence(self, **kw):
        state = {"present": True, "age_s": 10, "active_runs": 0, "active_items": 0, "read_only_verified": True, "latest_run_id": 66, "latest_run_status": "completed", "sha256": "x" * 64,
                 "generator_sha256": self.mod.CFG["pins"]["source"]["sha256"]}
        state.update(kw)
        self.monkeypatch.setattr(self.mod, "evidence_state", lambda: dict(state))

    def entry(self, *, check_ok=True, rc=None, verdicts=None, extra_files=None, summary_ok=True, invoke_exc=None, hash_override=None, tamper_receipt=None, tamper_bound=None, tamper_summary=None,
              check_elapsed_s=0.0, stdout_prefix=b""):
        """subprocess.run gia: --check-only tra JSON bytes; goi that tao PLAN_BOUND + chuoi receipt DUNG HINH DANG RUNNER (hash-chain, spec/argv hash tu plan file) + tom tat stdout."""
        calls = []
        rc, verdicts, extra_files = rc or {}, verdicts or {}, extra_files or {}
        tamper_receipt, tamper_bound, tamper_summary = tamper_receipt or {}, tamper_bound or {}, tamper_summary or {}
        rr = self.mod.runner()

        def fake_run(argv, **kwargs):
            key = "clone" if str(self.run / "clone") in " ".join(argv) else "init"
            calls.append((key, list(argv), kwargs))
            p = self.mod.CFG["plans"][key]
            if argv[-1] == "--check-only":
                self.world.t += check_elapsed_s
                payload = {"ok": check_ok, "computed_plan_sha256": p["plan_sha256"], "computed_invocation_sha256": (hash_override or {}).get(key, p["invocation_sha256"])}
                return subprocess.CompletedProcess(argv, 0 if check_ok else 1, stdout=(json.dumps(payload) + "\n").encode(), stderr=b"")
            if invoke_exc and key in invoke_exc:
                raise invoke_exc[key]
            directory = Path(p["plan_dir"])
            plan_doc = json.loads(Path(p["plan_file"]).read_text(encoding="utf-8"))["document"]
            specs = {sp["name"]: sp for sp in plan_doc["specs"]}
            entry_sha, q0_sha = self.mod.CFG["pins"]["entry"]["sha256"], self.mod.CFG["q0"]["sha256"]
            bound = {"kind": "plan_bound", "approved_plan_sha256": p["plan_sha256"], "computed_plan_sha256": p["plan_sha256"], "entrypoint_sha256": entry_sha, "phases": list(self.mod.PLAN_PHASES[key]), **tamper_bound}
            (directory / "PLAN_BOUND.json").write_text(json.dumps(bound), encoding="utf-8")
            previous, rows = None, []
            for index, name in enumerate(self.mod.PLAN_PHASES[key], 1):
                spec = specs[name]
                launch = {"exit_code": 0, "timed_out": False, "killed_tree": False, "aborted_by_gate": None, "contained": True}
                full = {"phase": name, "seq": index, "verdict": verdicts.get(name, "PASS"), "reasons": [], "kind": spec["kind"], "prev_receipt_sha256": previous, "approved_plan_sha256": p["plan_sha256"],
                        "computed_plan_sha256": p["plan_sha256"], "entrypoint_sha256": entry_sha, "spec_sha256": rr.spec_sha256(spec), "argv_sha256": rr.argv_sha256(spec),
                        "q0_receipt_sha256": q0_sha if spec["kind"] == "write" else None, "launch": launch, **tamper_receipt.get(name, {})}
                (directory / f"receipt_{index:02d}_{name}.json").write_text(json.dumps(full, indent=2, sort_keys=True), encoding="utf-8")
                previous = rr.sha256_hex(rr.canonical_json(full))
                rows.append({"phase": name, "reasons": [], "receipt_path": str(directory / f"receipt_{index:02d}_{name}.json"), "receipt_sha256": previous, "verdict": "PASS"})
            for extra in extra_files.get(key, ()):
                (directory / extra).write_text("x", encoding="utf-8")
            summary = {"approved_plan_sha256": p["plan_sha256"], "computed_plan_sha256": p["plan_sha256"], "entrypoint_sha256": entry_sha, "invocation_sha256": p["invocation_sha256"], "ok": summary_ok,
                       "phases": rows, **tamper_summary}
            return subprocess.CompletedProcess(argv, rc.get(key, 0), stdout=stdout_prefix + json.dumps(summary).encode() + b"\r\n", stderr=b"")
        self.monkeypatch.setattr(self.mod.subprocess, "run", fake_run)
        return calls


@pytest.fixture
def ci(tmp_path, monkeypatch):
    env = Ci(tmp_path, monkeypatch)
    assert env.load() == []
    return env


# =========================================================================== load_freeze
def test_a_valid_freeze_loads_and_builds_the_exact_argv_with_the_q0_receipt(ci):
    mod = ci.mod
    clone = mod.CFG["plans"]["clone"]
    assert mod.approved_argv("clone") == [mod.PY_VENV, str(mod.ENTRY), "--approval", clone["approval"], "--plan", clone["plan_file"], "--plan-dir", clone["plan_dir"], "--launcher-logs", clone["launcher_logs"],
                                           "--crawl-evidence", str(mod.LIVE), "--q0-receipt", mod.CFG["q0"]["path"]]
    assert set(mod.CFG["plans"]) == {"clone", "init"} and mod.CFG["freeze_sha256"] == ci.freeze_sha


def mutated(ci, fn):
    doc = json.loads(json.dumps(ci.freeze))
    fn(doc)
    path, digest = ci.write_freeze(doc, "mutated.json")
    return ci.mod.load_freeze(path, digest)


@pytest.mark.parametrize("name,fn,expected", [
    ("kind", lambda d: d.__setitem__("kind", "other"), "freeze_kind_or_run_root"),
    ("run_root", lambda d: d.__setitem__("run_root", r"D:\elsewhere"), "freeze_kind_or_run_root"),
    ("windows_order", lambda d: d["windows"].__setitem__("start_deadline_utc", d["windows"]["not_before_utc"]), "freeze_windows_order"),
    ("windows_missing", lambda d: d["windows"].pop("hard_not_after_utc"), "freeze_windows"),
    ("q0_created", lambda d: d["q0_receipt"].__setitem__("created_at_utc", "yesterday"), "freeze_q0_receipt"),
    ("q0_sha", lambda d: d["q0_receipt"].__setitem__("sha256", "zz"), "freeze_q0_receipt"),
    ("plan_missing_key", lambda d: d["plans"]["clone"].pop("invocation_sha256"), "freeze_plan_clone"),
    ("plan_relative", lambda d: d["plans"]["init"].__setitem__("approval", "approval.md"), "freeze_plan_init"),
    ("plan_dir_elsewhere", lambda d: d["plans"]["init"].__setitem__("plan_dir", r"D:\elsewhere\plan"), "freeze_plan_init_dirs_not_in_run_root"),
    ("pin_redirected", lambda d: d["pins"]["supervisor"].__setitem__("path", r"D:\evil\renewal_supervisor_v1.py"), "freeze_pin_supervisor"),
    ("pin_missing", lambda d: d["pins"].pop("entry"), "freeze_pin_entry"),
    ("profile_bad", lambda d: d["profile"].__setitem__("sha256", "short"), "freeze_profile"),
    ("reports_count", lambda d: d["h2_reports"].pop(), "freeze_h2_reports"),
    ("reports_dir", lambda d: d["h2_reports"].__setitem__(0, r"D:\elsewhere\verify.json"), "freeze_h2_reports"),
])
def test_every_malformed_or_redirected_freeze_is_refused_and_leaves_cfg_empty(ci, name, fn, expected):
    assert expected in mutated(ci, fn) and ci.mod.CFG == {}


def test_a_freeze_with_the_wrong_sha_or_content_is_refused(ci):
    assert ci.mod.load_freeze(ci.freeze_path, "0" * 64) == ["freeze_sha256_mismatch"] and ci.mod.CFG == {}
    assert ci.mod.load_freeze(ci.run / "nope.json", ci.freeze_sha) == ["freeze_unreadable"]
    bad = ci.run / "bad.json"
    bad.write_bytes(b"not json")
    assert ci.mod.load_freeze(bad, hashlib.sha256(b"not json").hexdigest()) == ["freeze_not_json"]


def test_every_command_refuses_when_no_freeze_is_loaded(ci):
    ci.mod.CFG.clear()
    assert ci.mod.preconditions() == ["freeze_not_loaded"]


# =========================================================================== probe crawler fail-closed
@pytest.mark.parametrize("result,success,pids", [
    (subprocess.CompletedProcess([], 0, stdout="", stderr=""), True, []),
    (subprocess.CompletedProcess([], 0, stdout="12\n34\n", stderr=""), True, [12, 34]),
    (subprocess.CompletedProcess([], 1, stdout="", stderr=""), False, []),
    (subprocess.CompletedProcess([], 0, stdout="", stderr="Access is denied"), False, []),
    (subprocess.CompletedProcess([], 0, stdout="garbage 12", stderr=""), False, [12]),
])
def test_crawler_probe_succeeds_only_with_rc0_empty_stderr_and_numeric_stdout(ci, monkeypatch, result, success, pids):
    monkeypatch.setattr(ci.mod.subprocess, "run", lambda *a, **k: result)
    probe = ci.mod.crawler_probe()
    assert probe["success"] is success and probe["pids"] == pids


def test_crawler_probe_timeout_or_oserror_is_a_failed_probe_not_idle(ci, monkeypatch):
    def timeout(*a, **k):
        raise subprocess.TimeoutExpired("ps", 60)

    def oserror(*a, **k):
        raise FileNotFoundError("powershell")
    monkeypatch.setattr(ci.mod.subprocess, "run", timeout)
    assert ci.mod.crawler_probe()["success"] is False
    monkeypatch.setattr(ci.mod.subprocess, "run", oserror)
    assert ci.mod.crawler_probe()["success"] is False


# =========================================================================== preconditions
def clean_probe(ci, monkeypatch, **override):
    probe = {"success": True, "pids": [], "returncode": 0, "timed_out": False}
    probe.update(override)
    monkeypatch.setattr(ci.mod, "crawler_probe", lambda: dict(probe))


def test_preconditions_are_empty_for_a_clean_fresh_state(ci, monkeypatch):
    clean_probe(ci, monkeypatch)
    assert ci.mod.preconditions() == []


@pytest.mark.parametrize("probe,needle", [({"success": False, "returncode": 1}, "crawler_probe_failed"), ({"pids": [99]}, "crawler_process_alive")])
def test_preconditions_refuse_a_failed_probe_or_a_live_crawler(ci, monkeypatch, probe, needle):
    clean_probe(ci, monkeypatch, **probe)
    assert any(needle in p for p in ci.mod.preconditions())


def test_preconditions_refuse_outside_the_window_and_a_stale_q0_receipt(ci, monkeypatch):
    clean_probe(ci, monkeypatch)
    ci.world.t += 3600                                                                                           # +1 gio: qua start_deadline (55 phut) va Q0 qua tuoi
    problems = ci.mod.preconditions()
    assert any("outside_start_window" in p for p in problems) and any("q0_receipt_too_old" in p for p in problems)


def test_preconditions_refuse_a_pin_drift_existing_outputs_nonempty_plan_dirs_and_db_env(ci, monkeypatch):
    clean_probe(ci, monkeypatch)
    Path(ci.mod.CFG["plans"]["clone"]["approval"]).write_bytes(b"tampered")
    (ci.run / "ci_STOP").write_text("x", encoding="utf-8")
    (ci.run / "init" / "plan" / "leftover.json").write_text("x", encoding="utf-8")
    monkeypatch.setenv("DB_PASSWORD", "x")
    problems = ci.mod.preconditions()
    assert "sha_mismatch: clone_approval" in problems and "already_exists: ci_STOP" in problems and "plan_dir_not_empty: init" in problems and "shell_inherits_DB_vars" in problems


def test_preconditions_refuse_when_the_h2_directory_or_a_launcher_logs_dir_already_exists(ci, monkeypatch):
    clean_probe(ci, monkeypatch)
    (ci.run / "h2" / "reports").mkdir(parents=True)
    (ci.run / "clone" / "logs").mkdir()
    problems = ci.mod.preconditions()
    assert "already_exists: h2" in problems and "already_exists: logs" in problems


# =========================================================================== hai plan tuan tu
def ready(ci):
    ci.started()
    ci.evidence()
    return ci.entry(stdout_prefix=b"line1\r\n")


def test_clone_then_init_each_get_one_check_only_then_one_invocation_with_the_exact_argv(ci):
    calls = ready(ci)
    assert ci.mod.cmd_plans() == 0
    assert [(k, a[-1] == "--check-only") for k, a, _ in calls] == [("clone", True), ("clone", False), ("init", True), ("init", False)]
    for key, argv, kw in calls:
        expected = ci.mod.approved_argv(key) + (["--check-only"] if argv[-1] == "--check-only" else [])
        assert argv == expected and kw["env"] == ci.mod.ENV6 and kw["cwd"] == str(ci.mod.ROOT)
    invocations = {k: kw for k, a, kw in calls if a[-1] != "--check-only"}
    assert invocations["clone"]["timeout"] == 1260 + 120 and invocations["init"]["timeout"] == 1380 + 120
    assert ci.mod.H2_REPORTS.is_dir() and ci.world.stopped() and ci.mod.STOP.exists()
    for key in ("clone", "init"):
        written = (ci.run / f"ci_{key}_entry_stdout.json").read_bytes()
        assert written.startswith(b"line1\r\n") and written.endswith(b"\r\n") and b"\r\n" in written                      # byte-exact: CRLF cua entry duoc giu nguyen (khong chuan hoa newline)
        assert hashlib.sha256(written).hexdigest() in ci.mod.OP_LOG.read_text(encoding="utf-8")


def test_the_lead_wait_happens_once_before_the_first_plan_only(ci):
    ready(ci)
    ci.mod.cmd_plans()
    assert ci.world.t - ci.world.t0 >= 90 and "waiting_lead" in ci.world.op_events() and ci.world.op_events().count("waiting_lead") == 1


@pytest.mark.parametrize("scenario", ["rc", "quarantine", "stdout_not_ok", "extra_file"])
def test_init_never_runs_unless_clone_passed_for_real(ci, scenario):
    kwargs = {"rc": dict(rc={"clone": 3}), "quarantine": dict(verdicts={"ph3_clone": "QUARANTINE"}), "stdout_not_ok": dict(summary_ok=False), "extra_file": dict(extra_files={"clone": ["QUARANTINE.json"]})}[scenario]
    ci.started()
    ci.evidence()
    calls = ci.entry(**kwargs)
    assert ci.mod.cmd_plans() != 0
    assert not any(k == "init" for k, _, _ in calls) and not ci.mod.H2_DIR.exists() and ci.world.stopped()


def test_a_failing_clone_check_only_or_hash_difference_means_no_invocation_and_no_init(ci):
    ci.started()
    ci.evidence()
    calls = ci.entry(check_ok=False)
    assert ci.mod.cmd_plans() == 2 and [a[-1] for _, a, _ in calls] == ["--check-only"] and ci.world.stopped()


def test_a_check_only_hash_that_differs_from_the_freeze_is_refused(ci):
    ci.started()
    ci.evidence()
    calls = ci.entry(hash_override={"clone": "f" * 64})
    assert ci.mod.cmd_plans() == 2 and len(calls) == 1


def test_init_is_refused_when_the_evidence_turns_busy_after_clone(ci, monkeypatch):
    ci.started()
    ci.evidence()
    calls = ci.entry()
    states = iter([None])
    real_entry = ci.mod.subprocess.run

    def run_then_busy(argv, **kw):
        result = real_entry(argv, **kw)
        if argv[-1] != "--check-only" and "clone" in " ".join(argv):
            ci.evidence(active_runs=1)                                                                           # crawler bat dau chay NGAY sau clone
        return result
    monkeypatch.setattr(ci.mod.subprocess, "run", run_then_busy)
    assert ci.mod.cmd_plans() == 2 and not any(k == "init" for k, _, _ in calls) and ci.world.stopped()


def test_init_is_refused_when_the_publisher_has_less_time_than_the_plan_needs(ci):
    ci.started(wall_seconds=1260 + 120 + 90 + 100)                                                               # du clone, thieu init (1380+120)
    ci.evidence()
    calls = ci.entry()
    assert ci.mod.cmd_plans() == 2 and [k for k, a, _ in calls if a[-1] != "--check-only"] == ["clone"]


def test_a_stale_q0_receipt_refuses_the_next_plan(ci, monkeypatch):
    ci.started(wall_seconds=20000)                                                                               # du thoi gian publisher: chi tuoi Q0 moi chan duoc
    ci.evidence()
    calls = ci.entry()
    real = ci.mod.run_plan

    def aged(key, started, *, first):
        if key == "init":
            ci.world.t += 3600                                                                                   # review cham: receipt Q0 het tuoi truoc khi vao init
        return real(key, started, first=first)
    monkeypatch.setattr(ci.mod, "run_plan", aged)
    assert ci.mod.cmd_plans() == 2 and not any(k == "init" for k, _, _ in calls)


def test_the_start_deadline_only_applies_to_the_first_plan(ci):
    ci.started(wall_seconds=4000)
    ci.evidence()
    calls = ci.entry()
    started = {"at_utc": NOW.strftime("%Y-%m-%dT%H:%M:%S.%fZ"), "wall_deadline_utc": text(NOW + dt.timedelta(seconds=4000))}
    ci.mod.CFG["windows"]["start_deadline_utc"] = NOW + dt.timedelta(minutes=10)
    ci.world.t += 900                                                                                            # 15 phut sau: qua start_deadline (10 phut) nhung Q0 (20 phut tuoi) va thoi gian con du
    assert ci.mod.run_plan("clone", started, first=True) == 2 and calls == []                                    # plan dau: bi chan boi start_deadline
    assert ci.mod.run_plan("clone", started, first=False) == 0                                                   # plan sau: start_deadline khong ap dung
    assert [k for k, a, _ in calls if a[-1] != "--check-only"] == ["clone"]


def test_a_publisher_that_already_stopped_refuses_every_plan(ci):
    ci.started()
    ci.world.append({"event": "stopped", "verified_dead": True, "reason": "wall_deadline"})
    ci.evidence()
    calls = ci.entry()
    assert ci.mod.run_plan("clone", {"at_utc": NOW.strftime("%Y-%m-%dT%H:%M:%S.%fZ"), "wall_deadline_utc": text(NOW + dt.timedelta(seconds=9000))}, first=False) == 2 and calls == []
    assert "publisher_already_stopped" in ci.mod.OP_LOG.read_text(encoding="utf-8")


def test_the_hard_not_after_limit_refuses_with_its_own_reason(ci):
    ci.evidence()
    calls = ci.entry()
    ci.mod.CFG["windows"]["hard_not_after_utc"] = NOW - dt.timedelta(seconds=1)
    assert ci.mod.run_plan("clone", {"at_utc": NOW.strftime("%Y-%m-%dT%H:%M:%S.%fZ"), "wall_deadline_utc": text(NOW + dt.timedelta(seconds=9000))}, first=False) == 2 and calls == []
    assert "hard_not_after_passed" in ci.mod.OP_LOG.read_text(encoding="utf-8")


def test_an_existing_h2_reports_directory_refuses_init_and_never_overwrites(ci):
    ci.started()
    ci.evidence()
    calls = ci.entry()
    ci.mod.H2_REPORTS.mkdir(parents=True)
    assert ci.mod.cmd_plans() == 2 and [k for k, a, _ in calls if a[-1] != "--check-only"] == ["clone"]


def test_without_a_started_publisher_nothing_runs(ci):
    calls = ci.entry()
    assert ci.mod.cmd_plans() == 2 and calls == [] and "stop_not_needed" in ci.world.op_events()


@pytest.mark.parametrize("exc", [KeyboardInterrupt, RuntimeError, subprocess.TimeoutExpired])
def test_exception_or_cancel_in_a_plan_stops_the_publisher_and_propagates(ci, exc):
    ci.started()
    ci.evidence()
    boom = exc("x") if exc is not subprocess.TimeoutExpired else subprocess.TimeoutExpired("entry", 1380)
    ci.entry(invoke_exc={"clone": boom})
    with pytest.raises(exc):
        ci.mod.cmd_plans()
    assert ci.mod.STOP.exists() and ci.world.stopped()


def test_unverified_cleanup_makes_a_passing_run_exit_nonzero(ci):
    ready(ci)
    ci.world.verified_dead = False
    with pytest.raises(SystemExit) as info:
        ci.mod.cmd_plans()
    assert info.value.code == 3


# =========================================================================== 102 H-CHAIN: chuoi receipt THAT (khong chi ten file/PASS)
CLONE_TAMPER = [
    ("foreign_plan_hash", {"ph3_clone": {"approved_plan_sha256": "f" * 64}}),
    ("foreign_computed_plan_hash", {"ph3_clone": {"computed_plan_sha256": "f" * 64}}),
    ("wrong_prev", {"ph3_clone": {"prev_receipt_sha256": "0" * 64}}),
    ("wrong_argv_hash", {"ph3_clone": {"argv_sha256": "e" * 64}}),
    ("wrong_spec_hash", {"ph3_clone": {"spec_sha256": "e" * 64}}),
    ("wrong_seq", {"ph3_clone": {"seq": 2}}),
    ("wrong_kind", {"ph3_clone": {"kind": "readonly"}}),
    ("wrong_q0_for_write", {"ph3_clone": {"q0_receipt_sha256": "9" * 64}}),
    ("missing_q0_for_write", {"ph3_clone": {"q0_receipt_sha256": None}}),
    ("wrong_entrypoint", {"ph3_clone": {"entrypoint_sha256": "7" * 64}}),
    ("launch_killed_tree", {"ph3_clone": {"launch": {"exit_code": 0, "timed_out": False, "killed_tree": True, "aborted_by_gate": None, "contained": True}}}),
    ("launch_not_contained", {"ph3_clone": {"launch": {"exit_code": 0, "timed_out": False, "killed_tree": False, "aborted_by_gate": None, "contained": False}}}),
    ("launch_aborted_by_gate", {"ph3_clone": {"launch": {"exit_code": 0, "timed_out": False, "killed_tree": False, "aborted_by_gate": "crawler", "contained": True}}}),
    ("reasons_present", {"ph3_clone": {"reasons": ["something"]}}),
]


@pytest.mark.parametrize("name,tamper", CLONE_TAMPER, ids=[n for n, _ in CLONE_TAMPER])
def test_102_a_clone_receipt_that_is_not_the_genuine_runner_chain_never_lets_init_run(ci, name, tamper):
    ci.started()
    ci.evidence()
    calls = ci.entry(tamper_receipt=tamper)
    assert ci.mod.cmd_plans() == 5
    assert not any(k == "init" for k, _, _ in calls) and not ci.mod.H2_DIR.exists() and "plan_not_pass" in ci.world.op_events()


INIT_TAMPER = [
    ("break_chain_at_verify_h1", {"ph4_verify_h1": {"prev_receipt_sha256": "0" * 64}}),
    ("break_chain_at_init_h3", {"ph4_init_h3": {"prev_receipt_sha256": "1" * 64}}),
    ("wrong_argv_on_last", {"ph4_verify_h3": {"argv_sha256": "e" * 64}}),
    ("foreign_plan_on_third", {"ph4_init_h3": {"computed_plan_sha256": "f" * 64}}),
    ("verify_marked_readonly_as_write", {"ph4_verify_h1": {"kind": "write"}}),
    ("quarantine_verdict_on_init_h1", {"ph4_init_h1": {"verdict": "QUARANTINE"}}),
]


@pytest.mark.parametrize("name,tamper", INIT_TAMPER, ids=[n for n, _ in INIT_TAMPER])
def test_102_all_four_init_receipts_are_chain_checked_too(ci, name, tamper):
    ci.started()
    ci.evidence()
    ci.entry(tamper_receipt=tamper)
    assert ci.mod.cmd_plans() == 5 and "plan_not_pass" in ci.world.op_events()


@pytest.mark.parametrize("name,kwargs", [
    ("bound_entry", dict(tamper_bound={"entrypoint_sha256": "7" * 64})),
    ("bound_phases", dict(tamper_bound={"phases": ["other"]})),
    ("bound_kind", dict(tamper_bound={"kind": "other"})),
    ("summary_receipt_hash", dict(tamper_summary={"phases": [{"phase": "ph3_clone", "reasons": [], "receipt_path": "x", "receipt_sha256": "0" * 64, "verdict": "PASS"}]})),
    ("summary_invocation", dict(tamper_summary={"invocation_sha256": "0" * 64})),
    ("summary_entry", dict(tamper_summary={"entrypoint_sha256": "0" * 64})),
    ("summary_plan", dict(tamper_summary={"approved_plan_sha256": "0" * 64})),
    ("summary_empty_phases", dict(tamper_summary={"phases": []})),
])
def test_102_plan_bound_and_the_entry_summary_must_match_every_receipt(ci, name, kwargs):
    ci.started()
    ci.evidence()
    calls = ci.entry(**kwargs)
    assert ci.mod.cmd_plans() == 5 and not any(k == "init" for k, _, _ in calls)


def test_102_a_runner_that_differs_from_the_freeze_pin_cannot_validate_any_chain(ci):
    ci.started()
    ci.evidence()
    calls = ci.entry()
    ci.mod.CFG["pins"]["runner"]["sha256"] = "0" * 64
    ci.mod._RUNNER = None
    assert ci.mod.cmd_plans() == 5 and not any(k == "init" for k, _, _ in calls)


def test_102_the_runner_shaped_fixture_is_accepted_end_to_end(ci):
    ready(ci)
    assert ci.mod.cmd_plans() == 0                                                                               # lan chay hop le van PASS: fixture khong "thieu" truong nao


# =========================================================================== 102 H-TIME: kiem lai NGAY truoc invocation (check-only co the ton 120 s)
def test_102_check_only_that_consumes_115_s_leaves_too_little_publisher_time_so_no_invocation(ci):
    ci.started(wall_seconds=90 + 1380 + 100)                                                                     # truoc check-only con 1480 >= 1380; sau 115 s con 1365 < 1380
    ci.evidence()
    calls = ci.entry(check_elapsed_s=115.0)
    assert ci.mod.cmd_plans() == 2
    assert [a[-1] for _, a, _ in calls] == ["--check-only"] and "before_invocation" in ci.mod.OP_LOG.read_text(encoding="utf-8")
    assert "insufficient_publisher_time" in ci.mod.OP_LOG.read_text(encoding="utf-8") and ci.world.stopped()


def test_102_evidence_that_turns_busy_during_check_only_is_caught_before_the_invocation(ci, monkeypatch):
    ci.started()
    ci.evidence()
    calls = ci.entry()
    real = ci.mod.subprocess.run

    def run_then_busy(argv, **kw):
        result = real(argv, **kw)
        if argv[-1] == "--check-only":
            ci.evidence(active_runs=1)
        return result
    monkeypatch.setattr(ci.mod.subprocess, "run", run_then_busy)
    assert ci.mod.cmd_plans() == 2 and [a[-1] for _, a, _ in calls] == ["--check-only"] and ci.world.stopped()


def test_102_q0_that_ages_out_during_check_only_is_caught_before_the_invocation(ci):
    ci.started(wall_seconds=20000)
    ci.evidence()
    ci.mod.CFG["windows"]["start_deadline_utc"] = NOW + dt.timedelta(hours=4)                                    # tach rieng tuoi Q0 khoi start_deadline (hien duoc kiem lai o cong thu hai)
    calls = ci.entry(check_elapsed_s=3600.0)                                                                     # check-only (gia) keo dai tuoi Q0 qua gioi han
    assert ci.mod.cmd_plans() == 2 and [a[-1] for _, a, _ in calls] == ["--check-only"] and "q0_receipt_too_old" in ci.mod.OP_LOG.read_text(encoding="utf-8")


def test_102_a_publisher_that_stops_during_check_only_blocks_the_invocation(ci, monkeypatch):
    ci.started()
    ci.evidence()
    calls = ci.entry()
    real = ci.mod.subprocess.run

    def run_then_stop(argv, **kw):
        result = real(argv, **kw)
        if argv[-1] == "--check-only":
            ci.world.append({"event": "stopped", "verified_dead": True, "reason": "wall_deadline"})
        return result
    monkeypatch.setattr(ci.mod.subprocess, "run", run_then_stop)
    assert ci.mod.cmd_plans() == 2 and [a[-1] for _, a, _ in calls] == ["--check-only"]


def test_102_the_init_report_dir_prepared_before_the_second_gate_is_left_empty_when_that_gate_refuses(ci, monkeypatch):
    ci.started(wall_seconds=20000)
    ci.evidence()
    calls = ci.entry()
    real = ci.mod.subprocess.run

    def run_then_busy_on_init_check(argv, **kw):
        result = real(argv, **kw)
        if argv[-1] == "--check-only" and str(ci.run / "init") in " ".join(argv):
            ci.evidence(active_runs=1)
        return result
    monkeypatch.setattr(ci.mod.subprocess, "run", run_then_busy_on_init_check)
    assert ci.mod.cmd_plans() == 2 and [(k, a[-1] == "--check-only") for k, a, _ in calls][-1] == ("init", True)
    assert ci.mod.H2_REPORTS.is_dir() and not any(ci.mod.H2_REPORTS.iterdir())                                   # da tao thu muc roi bi chan: rong, khong co bao cao nao


def test_104_the_first_plan_start_deadline_is_rechecked_before_the_actual_invocation(ci):
    ci.started(wall_seconds=9000)
    ci.evidence()
    calls = ci.entry(check_elapsed_s=115.0)
    ci.mod.CFG["windows"]["start_deadline_utc"] = NOW + dt.timedelta(seconds=100)                               # lead 90 s => check-only bat dau hop le o +90; check-only 115 s => invocation se o +205 > +100
    assert ci.mod.cmd_plans() == 2
    assert [a[-1] for _, a, _ in calls] == ["--check-only"] and "start_deadline_passed" in ci.mod.OP_LOG.read_text(encoding="utf-8") and "before_invocation" in ci.mod.OP_LOG.read_text(encoding="utf-8")


def test_102_check_only_uses_a_120_second_cap_and_bytes_capture_for_both_calls(ci):
    calls = ready(ci)
    assert ci.mod.cmd_plans() == 0
    for _, argv, kw in calls:
        assert kw["capture_output"] is True and kw["text"] is False
        assert kw["timeout"] == (120 if argv[-1] == "--check-only" else kw["timeout"])
    assert ci.mod.CHECK_ONLY_CAP_S == 120


# =========================================================================== lifecycle (sao tu cac operator da duyet)
@pytest.mark.parametrize("kwargs", [dict(active_runs=1), dict(read_only_verified=False), dict(age_s=500), dict(generator_sha256="f" * 64)])
def test_busy_or_stale_evidence_stops_the_publisher_and_blocks_the_plans(ci, kwargs):
    ci.started()
    ci.evidence(**kwargs)
    assert ci.mod.cmd_evidence() == 1 and ci.mod.STOP.exists() and ci.world.stopped()


def test_pending_supervisor_that_starts_after_two_seconds_is_waited_for(ci):
    ci.mod.SUP_STDOUT.write_bytes(b"")
    ci.world.timeline.append((2.0, {"event": "started", "pid": 1}))
    ci.evidence()
    assert ci.mod.cmd_evidence() == 0 and ci.world.t - ci.world.t0 >= 2.0 and not ci.mod.STOP.exists()


def test_start_handshake_timeout_requests_stop_and_confirms_refusal(ci):
    class Sup:
        def poll(self):
            return None
    ci.mod.SUP_STDOUT.write_bytes(b"")
    ci.world.refuse_on_stop_when_pending = True
    assert ci.mod.start_handshake(Sup()) == 2 and ci.mod.STOP.exists()


def test_a_failing_diagnostic_log_never_skips_the_stop(ci, monkeypatch):
    ci.started()
    real = ci.mod.log
    monkeypatch.setattr(ci.mod, "log", lambda kind, **f: (_ for _ in ()).throw(OSError("disk")) if kind == "stop_required_by_terminal_path" else real(kind, **f))
    assert ci.mod.ensure_stopped("test") == 0 and ci.mod.STOP.exists() and ci.world.stopped()


# =========================================================================== driver
@pytest.fixture
def driver():
    return load(DRIVER, "run_heavy_ci_workflow_under_test")


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
    def stamp(self): return "2026-10-09T12:35:00Z"


def go(driver, tmp_path, **rcs):
    fake = FakeOp(**rcs)
    code = driver.run_workflow(fake, freeze_path=tmp_path / "f.json", freeze_sha256="0" * 64, ack=lambda m, p: p.write_text("ack"), result_path=tmp_path / "result.json", ack_path=tmp_path / "ack.md", pre_checks=False)
    result = json.loads((tmp_path / "result.json").read_text(encoding="utf-8")) if (tmp_path / "result.json").exists() else None
    return code, fake, result


def test_driver_runs_the_stages_in_order_then_cleans_up_and_collects(driver, tmp_path):
    code, fake, result = go(driver, tmp_path)
    assert code == 0 and fake.calls == ["load_freeze", "preconditions", "cmd_start", "cmd_evidence", "cmd_plans", "ensure_stopped", "cmd_collect"] and result["exit_code"] == 0


def test_driver_refuses_a_bad_freeze_before_acknowledging_or_launching(driver, tmp_path):
    code, fake, result = go(driver, tmp_path, load_freeze=["freeze_sha256_mismatch"])
    assert code == 2 and fake.calls == ["load_freeze"] and result is None and not (tmp_path / "ack.md").exists()


@pytest.mark.parametrize("failing,rc,expected_calls", [("cmd_start", 2, ["load_freeze", "preconditions", "cmd_start"]), ("cmd_evidence", 1, ["load_freeze", "preconditions", "cmd_start", "cmd_evidence"])])
def test_driver_does_not_continue_after_a_nonzero_stage_but_still_cleans_up(driver, tmp_path, failing, rc, expected_calls):
    code, fake, result = go(driver, tmp_path, **{failing: rc})
    assert code == rc and fake.calls == [*expected_calls, "ensure_stopped", "cmd_collect"]


def test_driver_stops_at_precondition_problems_without_starting(driver, tmp_path):
    code, fake, result = go(driver, tmp_path, preconditions=["crawler_process_alive: [1]"])
    assert code == 2 and "cmd_start" not in fake.calls and fake.calls[-2:] == ["ensure_stopped", "cmd_collect"]


def test_driver_passes_through_a_plans_failure(driver, tmp_path):
    code, fake, result = go(driver, tmp_path, cmd_plans=5)
    assert code == 5 and result["plans"] == 5


@pytest.mark.parametrize("where,exc", [("cmd_start", RuntimeError("x")), ("cmd_evidence", OSError("x")), ("cmd_plans", KeyboardInterrupt()), ("cmd_plans", SystemExit(3))])
def test_driver_exceptions_cancel_and_system_exit_still_cleanup_and_collect(driver, tmp_path, where, exc):
    code, fake, result = go(driver, tmp_path, **{where: exc})
    assert code != 0 and fake.calls[-2:] == ["ensure_stopped", "cmd_collect"]


def test_driver_cleanup_failure_and_collect_failure_are_nonzero_without_masking_earlier_failures(driver, tmp_path):
    assert go(driver, tmp_path, ensure_stopped=3)[0] == 3
    for index, (rcs, expected) in enumerate([({"cmd_collect": 1}, 4), ({"cmd_collect": OSError("h")}, 4), ({"cmd_plans": 5, "cmd_collect": 1}, 5), ({"ensure_stopped": 3, "cmd_collect": 1}, 3)]):
        folder = tmp_path / f"c{index}"
        folder.mkdir()
        assert go(driver, folder, **rcs)[0] == expected


def test_driver_refuses_on_operator_pin_drift_or_a_previous_run(driver, tmp_path):
    fake = FakeOp()
    kwargs = dict(freeze_path=tmp_path / "f.json", freeze_sha256="0" * 64, ack=lambda m, p: p.write_text("a"), result_path=tmp_path / "r.json", ack_path=tmp_path / "a.md")
    driver.OPERATOR_HEAVY_CI_SHA256 = "0" * 64
    assert driver.run_workflow(fake, **kwargs) == 2 and fake.calls == []
    driver.OPERATOR_HEAVY_CI_SHA256 = hashlib.sha256(OPERATOR.read_bytes()).hexdigest()
    (tmp_path / "a.md").write_text("already")
    assert driver.run_workflow(fake, **kwargs) == 2 and fake.calls == []


# =========================================================================== GPT 120: tuoi Q0 ngay truoc CLONE thuc te <= 1200 s (phase ghi CUOI `ph4_init_h3` van thay Q0 con han)
LAST_WRITE_REASON = "q0_receipt_too_old_for_the_last_ci_write_phase"


def gate(ci, key, offset_s):
    """plan_gate_reason o thoi diem NOW+offset; Q0 trong fixture tao luc NOW-300 s nen tuoi = offset + 300."""
    ci.started(wall_seconds=40000)
    ci.evidence()
    started = {"at_utc": NOW.strftime("%Y-%m-%dT%H:%M:%S.%fZ"), "wall_deadline_utc": text(NOW + dt.timedelta(seconds=40000))}
    ci.world.t = ci.world.t0 + offset_s
    return ci.mod.plan_gate_reason(key, started, first=False, stage="unit")


def test_120_the_bound_is_the_published_formula(ci):
    assert ci.mod.CLONE_FIRST_WRITE_MAX_AGE_S == 3600 - (1260 + 120) - 120 - 780 - 120 == 1200


@pytest.mark.parametrize("key,offset,expected", [
    ("clone", 899, None), ("clone", 900, None), ("clone", 901, LAST_WRITE_REASON), ("clone", 2000, LAST_WRITE_REASON),             # tuoi 1199 / 1200 (dung bien, chap nhan) / 1201 (tu choi)
    ("clone", 3180, LAST_WRITE_REASON), ("clone", 3181, "q0_receipt_too_old"),                                                     # cong 3480 cu van la ly do uu tien khi qua 3480
    ("init", 900, None), ("init", 901, None), ("init", 2800, None), ("init", 3180, None), ("init", 3181, "q0_receipt_too_old")])    # init KHONG chiu rang buoc 1200 (tuoi 3100 duoc chap nhan nhu GPT 120)
def test_120_the_clone_gate_is_exactly_1200_s_and_does_not_apply_to_init(ci, key, offset, expected):
    assert gate(ci, key, offset) == expected


def test_120_a_q0_receipt_already_over_the_bound_at_the_first_gate_means_no_check_only_and_no_invocation(ci):
    ci.mod.CFG["q0"]["created_at_utc"] = text(NOW - dt.timedelta(seconds=1200))                                   # + lead 90 s => tuoi 1290 > 1200 truoc check-only
    ci.started(wall_seconds=20000)
    ci.evidence()
    calls = ci.entry()
    assert ci.mod.cmd_plans() == 2 and calls == [] and ci.world.stopped()
    log = ci.mod.OP_LOG.read_text(encoding="utf-8")
    assert LAST_WRITE_REASON in log and "before_check_only" in log


def test_120_a_receipt_inside_the_bound_at_the_first_gate_that_check_only_pushes_over_gets_zero_actual_invocation(ci):
    ci.started(wall_seconds=20000)
    ci.evidence()
    calls = ci.entry(check_elapsed_s=811.0)                                                                       # tuoi cong 1 = 300+90 = 390; sau check-only = 1201
    assert ci.mod.cmd_plans() == 2 and [a[-1] for _, a, _ in calls] == ["--check-only"] and ci.world.stopped()
    log = ci.mod.OP_LOG.read_text(encoding="utf-8")
    assert LAST_WRITE_REASON in log and "before_invocation" in log and not any(k == "init" for k, _, _ in calls)


def test_120_exactly_1200_s_at_the_second_gate_still_runs_clone_and_then_init(ci):
    ci.started(wall_seconds=20000)
    ci.evidence()
    calls = ci.entry(check_elapsed_s=810.0)                                                                       # tuoi cong 2 cua clone = 1200 chinh xac; init (tuoi 1200..2010) duoc cong 3480 cu cho phep
    assert ci.mod.cmd_plans() == 0
    assert [(k, a[-1] == "--check-only") for k, a, _ in calls] == [("clone", True), ("clone", False), ("init", True), ("init", False)]


def test_120_preconditions_block_early_when_the_q0_receipt_is_already_older_than_the_clone_bound(ci, monkeypatch):
    clean_probe(ci, monkeypatch)
    ci.mod.CFG["q0"]["created_at_utc"] = text(NOW - dt.timedelta(seconds=1300))
    problems = ci.mod.preconditions()
    assert any(p.startswith(LAST_WRITE_REASON) for p in problems) and not any(p.startswith("q0_receipt_too_old:") for p in problems)
    ci.mod.CFG["q0"]["created_at_utc"] = text(NOW - dt.timedelta(seconds=1200))
    assert not any(LAST_WRITE_REASON in p for p in ci.mod.preconditions())                                         # dung bien 1200: chua chan


def test_120_the_preflight_dossier_states_the_clone_bound(ci, monkeypatch):
    clean_probe(ci, monkeypatch)

    class Sup:
        pid = 5

        def poll(self):
            return None
    monkeypatch.setattr(ci.mod.subprocess, "Popen", lambda *a, **k: Sup())
    ci.world.timeline.append((1, {"event": "started", "pid": 1, "at_utc": NOW.strftime("%Y-%m-%dT%H:%M:%S.%fZ"), "wall_deadline_utc": text(NOW + dt.timedelta(seconds=3000))}))
    assert ci.mod.cmd_start() == 0
    assert "≤ 1200 s" in ci.mod.PREFLIGHT.read_text(encoding="utf-8") and "ph4_init_h3" in ci.mod.PREFLIGHT.read_text(encoding="utf-8")


@pytest.mark.skipif("HEAVY_CI_OPERATOR_UNDER_TEST" in os.environ, reason="mutation run uses a different operator file")
def test_120_the_driver_pin_equals_the_sha256_of_the_real_operator(driver):
    assert driver.OPERATOR_HEAVY_CI_SHA256 == hashlib.sha256((RUN_REAL / "operator_heavy_ci.py").read_bytes()).hexdigest()

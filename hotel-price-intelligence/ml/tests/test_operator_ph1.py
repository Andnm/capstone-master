"""Ph1-only operator (90a/90b) + driver: (1) hang so/binding dung 90a/90b va khong dung ten tep Q0; (2) lifecycle fake (khong SQL, khong process that): evidence/ph1/start handshake/cleanup khong phu thuoc log;
(3) _ph1_body: tu choi dung dieu kien, dung MOT invocation, stdout byte-exact; (4) archival move live Q0 khong ghi de; (5) driver: outer finally STOP+collect, collect that bai => nonzero."""
from __future__ import annotations

import hashlib
import importlib.util
import json
import re
import subprocess
import sys
from pathlib import Path

import pytest

from test_operator_q0 import FakeWorld, evidence  # noqa: F401  (FakeWorld la generic theo mod.SUP_LOG/STOP/SUP_STDOUT/OP_LOG)

RUN_REAL = Path(r"D:\MSE\CAPSTONE\outputs\repair-run-20261009")
OPERATOR = RUN_REAL / "operator_ph1.py"
DRIVER = RUN_REAL / "run_ph1_workflow.py"
DISCUSS = Path(r"D:\MSE\CAPSTONE\discuss\model-results-improvement-20261008")


def load(path: Path, name: str):
    spec = importlib.util.spec_from_file_location(name, path)
    mod = importlib.util.module_from_spec(spec)
    sys.modules.setdefault(name, mod) if False else None
    spec.loader.exec_module(mod)
    return mod


@pytest.fixture
def world(tmp_path, monkeypatch):
    mod = load(OPERATOR, "operator_ph1_under_test")
    run = tmp_path / "run"
    (run / "q0").mkdir(parents=True)
    monkeypatch.setattr(mod, "RUN", run)
    for name, filename in (("SUP_LOG", "ph1_supervisor.jsonl"), ("OP_LOG", "ph1_operator_log.jsonl"), ("STOP", "ph1_STOP"), ("LIVE", "live.json"), ("SUP_STDOUT", "ph1_supervisor_stdout.log"),
                           ("ENTRY_STDOUT", "ph1_entry_stdout.json"), ("ENTRY_STDERR", "ph1_entry_stderr.txt"), ("Q0_SUP_LOG", "q0_supervisor.jsonl"), ("PREFLIGHT", "ph1_dossier_preflight.md")):
        monkeypatch.setattr(mod, name, run / filename)
    monkeypatch.setattr(mod, "Q0_LIVE_ARCHIVE_TARGET", run / "q0" / f"live_final_before_ph1_{mod.Q0_LIVE_SHA}.json")
    return FakeWorld(mod, run, monkeypatch)


def set_evidence(world, monkeypatch, **kwargs):
    state = evidence(world, **kwargs)
    monkeypatch.setattr(world.mod, "evidence_state", lambda: state)


# =========================================================================== (1) binding
def exact_block(text: str) -> list[str]:
    block = re.search(r"Exact entry invocation[^\n]*\n\n```text\n(.*?)\n```", text, re.S).group(1)
    return block.split()


def test_ph1_argv_equals_the_exact_90a_invocation_and_has_no_q0_receipt():
    mod = load(OPERATOR, "operator_ph1_binding")
    tokens = exact_block((DISCUSS / "90a-gpt-ph1-outer-approval.md").read_text(encoding="utf-8"))
    assert subprocess.list2cmdline(mod.APPROVED_ARGV) == " ".join(tokens) and "--q0-receipt" not in mod.APPROVED_ARGV
    assert mod.APPROVED_ARGV.count("--approval") == 1 and mod.APPROVED_ARGV[mod.APPROVED_ARGV.index("--approval") + 1].endswith("90a-gpt-ph1-outer-approval.md")


def test_ph1_windows_pool_and_publisher_argv_equal_90b_and_the_profile():
    mod = load(OPERATOR, "operator_ph1_binding2")
    text = (DISCUSS / "90b-gpt-ph1-evidence-renewal-approval.md").read_text(encoding="utf-8")
    for needle in (f"not-before `{mod.NOT_BEFORE}`", f"start-deadline `{mod.START_DEADLINE}`", f"hard-not-after `{mod.HARD_NOT_AFTER}`", "max-pool-size5"):
        assert needle in text, needle
    profile = json.loads(Path(mod.PROFILE).read_text(encoding="utf-8"))
    argv_block = re.search(r"Exactpublisherargv[^\n]*\n\n```text\n(.*?)\n```", text, re.S).group(1)
    assert " ".join(profile["argv"]) == " ".join(argv_block.replace("\n--", " --").split()) and profile["renew_seconds"] == 3300 and "--renew-seconds 3300" in argv_block.replace("\n", " ")


def test_ph1_state_and_log_paths_never_reuse_q0_file_names():
    mod = load(OPERATOR, "operator_ph1_binding3")
    for name in ("SUP_LOG", "PUB_LOG", "STOP", "OP_LOG", "PREFLIGHT", "SUP_STDOUT", "ENTRY_STDOUT", "ENTRY_STDERR", "PLAN_DIR", "LOGS", "ARCHIVE_DIR"):
        path = getattr(mod, name)
        assert "q0" not in str(path.relative_to(mod.RUN)).lower() and "ph1" in str(path.relative_to(mod.RUN)).lower(), (name, path)
    assert mod.ARCHIVE_DIR.name == "ph1" and mod.LIVE == RUN_REAL / "live_crawl_idle_evidence.json"      # live path chung theo profile; archival move co dieu kien


def test_ph1_pins_match_the_real_files():
    mod = load(OPERATOR, "operator_ph1_binding4")
    real = {"approval_90a": mod.APPROVAL, "approval_90b": mod.RENEWAL_APPROVAL, "profile": mod.PROFILE, "source": mod.PROJECT / "audit_tools" / "crawl_idle_evidence.py", "interpreter": Path(mod.PY_VENV),
            "parent_interpreter": Path(mod.PY_SUP), "supervisor": mod.SUPERVISOR, "plan_file": mod.PLAN, "entry": mod.ENTRY, "runner": mod.PROJECT / "audit_tools" / "repair_phase_runner_v2.py"}
    for key, path in real.items():
        assert hashlib.sha256(Path(path).read_bytes()).hexdigest() == mod.SHA[key], key
    plan = json.loads(Path(mod.PLAN).read_text(encoding="utf-8"))
    assert plan["plan_sha256"] == mod.SHA["plan_canonical"]


def test_start_launches_the_supervisor_with_ph1_flags_and_writes_the_ph1_dossier(world, monkeypatch):
    mod = world.mod
    monkeypatch.setattr(mod, "preconditions", lambda: [])
    popen_calls = []

    class Sup:
        pid = 777

        def poll(self):
            return None

    def fake_popen(command, **kwargs):
        popen_calls.append((command, kwargs))
        world.timeline.append((1.0, {"event": "started", "pid": 1}))
        return Sup()
    monkeypatch.setattr(mod.subprocess, "Popen", fake_popen)
    assert mod.cmd_start() == 0
    (command, kwargs), = popen_calls
    flat = " ".join(command)
    for needle in (mod.SHA["profile"], "renewal_profile_ph1_r83.json", "2026-10-09T11:00:00Z", "2026-10-09T12:00:00Z", "2026-10-09T13:00:00Z", "ph1_supervisor.jsonl", "ph1_publisher.log", "ph1_STOP", "--max-pool-size 5"):
        assert needle in flat, needle
    assert "q0_" not in flat and kwargs["env"] == mod.ENV6 and kwargs["creationflags"] == 0x08000000
    dossier = mod.PREFLIGHT.read_text(encoding="utf-8")
    assert "Ph1 dossier" in dossier and mod.SHA["plan_canonical"] in dossier and "Q0 dossier" not in dossier


# =========================================================================== (2) lifecycle (fake)
@pytest.mark.parametrize("kwargs", [dict(active_runs=1), dict(active_items=2), dict(read_only=False), dict(age=500), dict(generator="f" * 64)])
def test_ph1_evidence_busy_stale_or_wrong_stops_and_verifies(world, monkeypatch, kwargs):
    world.start_publisher()
    set_evidence(world, monkeypatch, **kwargs)
    assert world.mod.cmd_evidence() == 1 and world.mod.STOP.exists() and world.stopped()


def test_ph1_clean_evidence_does_not_stop(world, monkeypatch):
    world.start_publisher()
    set_evidence(world, monkeypatch)
    assert world.mod.cmd_evidence() == 0 and not world.mod.STOP.exists()


def test_ph1_evidence_waits_for_a_pending_supervisor_started_after_two_seconds(world, monkeypatch):
    world.mod.SUP_STDOUT.write_bytes(b"")
    world.timeline.append((2.0, {"event": "started", "pid": 1}))
    set_evidence(world, monkeypatch)
    assert world.mod.cmd_evidence() == 0 and world.t - world.t0 >= 2.0 and not world.mod.STOP.exists()


def test_ph1_evidence_error_with_failing_log_still_stops(world, monkeypatch):
    world.start_publisher()
    real = world.mod.log
    monkeypatch.setattr(world.mod, "log", lambda kind, **f: (_ for _ in ()).throw(OSError("disk")) if kind == "evidence_error" else real(kind, **f))
    monkeypatch.setattr(world.mod, "evidence_state", lambda: (_ for _ in ()).throw(ValueError("corrupt")))
    assert world.mod.cmd_evidence() == 1 and world.mod.STOP.exists() and world.stopped()


@pytest.mark.parametrize("code", [0, 1, 2, 3])
def test_ph1_body_result_never_leaves_the_publisher_running(world, monkeypatch, code):
    world.start_publisher()
    monkeypatch.setattr(world.mod, "_ph1_body", lambda: code)
    assert world.mod.cmd_ph1() == code and world.mod.STOP.exists() and world.stopped()


@pytest.mark.parametrize("exc", [KeyboardInterrupt, RuntimeError, OSError, subprocess.TimeoutExpired])
def test_ph1_exception_or_cancel_stops_the_publisher_and_propagates(world, monkeypatch, exc):
    world.start_publisher()

    def boom():
        raise exc("simulated") if exc is not subprocess.TimeoutExpired else subprocess.TimeoutExpired("entry", 3150)
    monkeypatch.setattr(world.mod, "_ph1_body", boom)
    with pytest.raises(exc):
        world.mod.cmd_ph1()
    assert world.mod.STOP.exists() and world.stopped()


def test_ph1_unverified_cleanup_makes_success_exit_nonzero(world, monkeypatch):
    world.start_publisher()
    world.verified_dead = False
    monkeypatch.setattr(world.mod, "_ph1_body", lambda: 0)
    with pytest.raises(SystemExit) as info:
        world.mod.cmd_ph1()
    assert info.value.code == 3


def test_ph1_start_handshake_started_refused_timeout_and_cancel(world, monkeypatch):
    class Sup:
        def __init__(self, alive=True):
            self.alive = alive

        def poll(self):
            return None if self.alive else 1
    world.mod.SUP_STDOUT.write_bytes(b"")
    world.timeline.append((2.0, {"event": "started", "pid": 1}))
    assert world.mod.start_handshake(Sup()) == 0


def test_ph1_start_handshake_timeout_requests_stop_and_confirms_refusal(world):
    class Sup:
        def poll(self):
            return None
    world.mod.SUP_STDOUT.write_bytes(b"")
    world.refuse_on_stop_when_pending = True
    assert world.mod.start_handshake(Sup()) == 2 and world.mod.STOP.exists() and "publisher_never_started_confirmed_refused" in world.op_events()


def test_ph1_start_handshake_unresponsive_supervisor_is_nonzero(world):
    class Sup:
        def poll(self):
            return None
    world.mod.SUP_STDOUT.write_bytes(b"")
    assert world.mod.start_handshake(Sup()) == 3 and world.mod.STOP.exists()


# =========================================================================== (3) _ph1_body
NOW = __import__("datetime").datetime(2026, 10, 9, 11, 30, tzinfo=__import__("datetime").timezone.utc)


def started_event(world, *, at="2026-10-09T11:28:00.000000Z", wall="2026-10-09T12:23:00Z"):
    world.append({"event": "started", "pid": 1, "at_utc": at, "wall_deadline_utc": wall})


def fake_runs(world, monkeypatch, *, check_ok=True, invoke_rc=0, invoke_exc=None, stdout="line1\nline2\n"):
    calls = []

    def fake_run(argv, **kwargs):
        calls.append((list(argv), kwargs))
        if argv[-1] == "--check-only":
            return subprocess.CompletedProcess(argv, 0 if check_ok else 1, stdout=json.dumps({"ok": check_ok}) + "\n", stderr="")
        if invoke_exc:
            raise invoke_exc
        return subprocess.CompletedProcess(argv, invoke_rc, stdout=stdout, stderr="")
    monkeypatch.setattr(world.mod.subprocess, "run", fake_run)
    return calls


def body_ready(world, monkeypatch, **evidence_kwargs):
    monkeypatch.setattr(world.mod, "now", lambda: NOW)
    started_event(world)
    set_evidence(world, monkeypatch, **evidence_kwargs)


def test_ph1_body_refuses_without_a_started_publisher_and_runs_nothing(world, monkeypatch):
    calls = fake_runs(world, monkeypatch)
    assert world.mod._ph1_body() == 2 and calls == []


@pytest.mark.parametrize("kwargs", [dict(active_runs=1), dict(active_items=1), dict(read_only=False), dict(age=101), dict(generator="f" * 64)])
def test_ph1_body_refuses_unclean_or_stale_evidence_without_running_the_entry(world, monkeypatch, kwargs):
    body_ready(world, monkeypatch, **kwargs)
    calls = fake_runs(world, monkeypatch)
    assert world.mod._ph1_body() == 2 and calls == []


def test_ph1_body_refuses_after_the_start_deadline(world, monkeypatch):
    body_ready(world, monkeypatch)
    monkeypatch.setattr(world.mod, "now", lambda: __import__("datetime").datetime(2026, 10, 9, 12, 0, tzinfo=__import__("datetime").timezone.utc))
    calls = fake_runs(world, monkeypatch)
    assert world.mod._ph1_body() == 2 and calls == [] and "start_deadline_passed" in world.mod.OP_LOG.read_text(encoding="utf-8")


def test_ph1_body_refuses_when_the_publisher_hard_cap_leaves_less_than_plan_plus_margin(world, monkeypatch):
    monkeypatch.setattr(world.mod, "now", lambda: NOW)
    started_event(world, wall="2026-10-09T12:20:00Z")                                                           # con 3000 s < 3120 s
    set_evidence(world, monkeypatch)
    calls = fake_runs(world, monkeypatch)
    assert world.mod._ph1_body() == 2 and calls == []


def test_ph1_body_check_only_not_ok_means_no_invocation(world, monkeypatch):
    body_ready(world, monkeypatch)
    calls = fake_runs(world, monkeypatch, check_ok=False)
    assert world.mod._ph1_body() == 2 and len(calls) == 1 and calls[0][0][-1] == "--check-only"


def test_ph1_body_happy_path_runs_check_only_then_exactly_one_invocation_with_byte_exact_outputs(world, monkeypatch):
    body_ready(world, monkeypatch)
    calls = fake_runs(world, monkeypatch, stdout="a\nb\n")
    assert world.mod._ph1_body() == 0
    assert [c[0] for c in calls] == [[*world.mod.APPROVED_ARGV, "--check-only"], world.mod.APPROVED_ARGV]
    assert calls[1][1]["timeout"] == world.mod.INVOKE_TIMEOUT_S == 3150 and calls[1][1]["env"] == world.mod.ENV6 and calls[1][1]["cwd"] == str(world.mod.ROOT)
    data = world.mod.ENTRY_STDOUT.read_bytes()
    logged = [json.loads(x) for x in world.mod.OP_LOG.read_text(encoding="utf-8").splitlines() if '"ph1_invoked"' in x][0]
    assert hashlib.sha256(data).hexdigest() == logged["stdout_sha256"] and data == b"a\nb\n"                    # khong CRLF => SHA tep == SHA log


def test_ph1_body_passes_through_a_nonzero_entry_exit_and_a_timeout_propagates(world, monkeypatch):
    body_ready(world, monkeypatch)
    fake_runs(world, monkeypatch, invoke_rc=5)
    assert world.mod._ph1_body() == 5
    calls = fake_runs(world, monkeypatch, invoke_exc=subprocess.TimeoutExpired("entry", 3150))
    with pytest.raises(subprocess.TimeoutExpired):
        world.mod._ph1_body()


# =========================================================================== (4) archival move
def prepare_archive(world, content=b"q0 live final", verified=True):
    mod = world.mod
    mod.LIVE.write_bytes(content)
    mod.Q0_LIVE_SHA = hashlib.sha256(content).hexdigest()
    mod.Q0_LIVE_ARCHIVE_TARGET = mod.RUN / "q0" / f"live_final_before_ph1_{mod.Q0_LIVE_SHA}.json"
    if verified is not None:
        mod.Q0_SUP_LOG.write_text(json.dumps({"event": "stopped", "verified_dead": verified}) + "\n", encoding="utf-8")


def test_archive_moves_the_exact_q0_live_file_without_overwrite_and_records_hashes(world):
    prepare_archive(world)
    assert world.mod.archive_q0_live() == 0
    assert not world.mod.LIVE.exists() and world.mod.Q0_LIVE_ARCHIVE_TARGET.read_bytes() == b"q0 live final" and "archive_q0_live" in world.op_events()


def test_archive_refuses_on_hash_drift_and_moves_nothing(world):
    prepare_archive(world)
    world.mod.LIVE.write_bytes(b"drifted")
    assert world.mod.archive_q0_live() == 2 and world.mod.LIVE.read_bytes() == b"drifted" and not world.mod.Q0_LIVE_ARCHIVE_TARGET.exists()


def test_archive_refuses_when_the_target_exists_and_never_overwrites(world):
    prepare_archive(world)
    world.mod.Q0_LIVE_ARCHIVE_TARGET.write_bytes(b"precious")
    assert world.mod.archive_q0_live() == 2 and world.mod.Q0_LIVE_ARCHIVE_TARGET.read_bytes() == b"precious" and world.mod.LIVE.exists()


@pytest.mark.parametrize("verified", [False, None])
def test_archive_refuses_unless_q0_cleanup_was_verified_dead(world, verified):
    prepare_archive(world, verified=verified)
    assert world.mod.archive_q0_live() == 2 and world.mod.LIVE.exists()


def test_archive_refuses_a_missing_source(world):
    prepare_archive(world)
    world.mod.LIVE.unlink()
    assert world.mod.archive_q0_live() == 2


def test_archive_refuses_a_target_outside_the_q0_directory(world):
    prepare_archive(world)
    world.mod.Q0_LIVE_ARCHIVE_TARGET = world.mod.RUN / "elsewhere.json"
    assert world.mod.archive_q0_live() == 2 and world.mod.LIVE.exists()


def test_archive_refuses_paths_outside_the_repair_run_root(world, tmp_path):
    prepare_archive(world)
    outside = tmp_path / "outside" / "q0"
    outside.mkdir(parents=True)
    world.mod.Q0_LIVE_ARCHIVE_TARGET = outside / world.mod.Q0_LIVE_ARCHIVE_TARGET.name
    assert world.mod.archive_q0_live() == 2 and world.mod.LIVE.exists()


def test_archive_refuses_a_live_source_outside_the_repair_run_root(world, tmp_path):
    prepare_archive(world)
    outside = tmp_path / "outside"
    outside.mkdir()
    foreign = outside / "live.json"
    foreign.write_bytes(b"q0 live final")                                                                       # dung hash, nhung nam ngoai repair-run root
    world.mod.LIVE = foreign
    assert world.mod.archive_q0_live() == 2 and foreign.exists() and not world.mod.Q0_LIVE_ARCHIVE_TARGET.exists()


# =========================================================================== (5) driver
class FakeOp:
    """Module gia cho run_workflow: ghi lai thu tu goi; ket qua cau hinh duoc."""

    __file__ = str(OPERATOR)

    def __init__(self, **rcs):
        self.calls = []
        self.rcs = {"archive_q0_live": 0, "preconditions": [], "cmd_start": 0, "cmd_evidence": 0, "cmd_ph1": 0, "ensure_stopped": 0, "cmd_collect": 0}
        self.rcs.update(rcs)
        self.logged = []

    def _do(self, name, *args):
        self.calls.append(name)
        value = self.rcs[name]
        if isinstance(value, BaseException):
            raise value
        return value

    def archive_q0_live(self): return self._do("archive_q0_live")
    def preconditions(self): return self._do("preconditions")
    def cmd_start(self): return self._do("cmd_start")
    def cmd_evidence(self): return self._do("cmd_evidence")
    def cmd_ph1(self): return self._do("cmd_ph1")
    def ensure_stopped(self, reason): return self._do("ensure_stopped")
    def cmd_collect(self): return self._do("cmd_collect")
    def log(self, kind, **fields): self.logged.append(kind)
    def stamp(self): return "2026-10-09T11:00:00Z"


@pytest.fixture
def driver():
    return load(DRIVER, "run_ph1_workflow_under_test")


def go(driver, tmp_path, **rcs):
    fake = FakeOp(**rcs)
    code = driver.run_workflow(fake, ack=lambda m, p: p.write_text("ack"), result_path=tmp_path / "result.json", ack_path=tmp_path / "ack.md", pre_checks=False)
    return code, fake, json.loads((tmp_path / "result.json").read_text(encoding="utf-8"))


def test_driver_runs_the_stages_in_order_and_always_cleans_up_then_collects(driver, tmp_path):
    code, fake, result = go(driver, tmp_path)
    assert code == 0 and fake.calls == ["archive_q0_live", "preconditions", "cmd_start", "cmd_evidence", "cmd_ph1", "ensure_stopped", "cmd_collect"] and result["exit_code"] == 0


@pytest.mark.parametrize("failing,expected_calls,code", [
    ("archive_q0_live", ["archive_q0_live"], 2), ("cmd_start", ["archive_q0_live", "preconditions", "cmd_start"], 2), ("cmd_evidence", ["archive_q0_live", "preconditions", "cmd_start", "cmd_evidence"], 1)])
def test_driver_does_not_run_later_stages_after_a_nonzero_stage_but_still_cleans_up(driver, tmp_path, failing, expected_calls, code):
    rc = {"archive_q0_live": 2, "cmd_start": 2, "cmd_evidence": 1}[failing]
    got, fake, result = go(driver, tmp_path, **{failing: rc})
    assert got == code and fake.calls == [*expected_calls, "ensure_stopped", "cmd_collect"] and result["exit_code"] == code


def test_driver_stops_at_precondition_problems_without_starting(driver, tmp_path):
    code, fake, result = go(driver, tmp_path, preconditions=["crawler_process_alive: [1]"])
    assert code == 2 and "cmd_start" not in fake.calls and fake.calls[-2:] == ["ensure_stopped", "cmd_collect"]


def test_driver_passes_through_a_nonzero_ph1_exit(driver, tmp_path):
    code, fake, result = go(driver, tmp_path, cmd_ph1=5)
    assert code == 5 and result["ph1"] == 5


@pytest.mark.parametrize("where,exc", [("cmd_start", RuntimeError("x")), ("cmd_evidence", OSError("x")), ("cmd_ph1", KeyboardInterrupt()), ("cmd_ph1", SystemExit(3))])
def test_driver_exceptions_cancel_and_system_exit_still_cleanup_and_collect_and_are_nonzero(driver, tmp_path, where, exc):
    code, fake, result = go(driver, tmp_path, **{where: exc})
    assert code != 0 and fake.calls[-2:] == ["ensure_stopped", "cmd_collect"]


def test_driver_unverified_cleanup_or_a_cleanup_exception_is_nonzero(driver, tmp_path):
    assert go(driver, tmp_path, ensure_stopped=3)[0] == 3
    other = tmp_path / "second"
    other.mkdir()
    code, fake, result = go(driver, other, ensure_stopped=RuntimeError("cleanup"))
    assert code == 3 and result["cleanup_exception"] == "RuntimeError" and fake.calls[-1] == "cmd_collect"


def test_driver_collect_failure_or_exception_is_nonzero_but_never_overrides_an_earlier_failure(driver, tmp_path):
    assert go(driver, tmp_path, cmd_collect=1)[0] == 4
    a = tmp_path / "a"
    a.mkdir()
    code, _, result = go(driver, a, cmd_collect=OSError("hash"))
    assert code == 4 and result["collect_exception"] == "OSError"
    b = tmp_path / "b"
    b.mkdir()
    code, _, result = go(driver, b, cmd_ph1=5, cmd_collect=1)
    assert code == 5 and result["collect_rc"] == 1
    c = tmp_path / "c"
    c.mkdir()
    code, _, result = go(driver, c, ensure_stopped=3, cmd_collect=1)
    assert code == 3


def test_driver_refuses_on_operator_pin_drift_or_a_previous_run_before_running_anything(driver, tmp_path):
    fake = FakeOp()
    driver.OPERATOR_PH1_SHA256 = "0" * 64                                                                         # != SHA that cua operator => drift
    assert driver.run_workflow(fake, ack=lambda m, p: p.write_text("a"), result_path=tmp_path / "r.json", ack_path=tmp_path / "a.md") == 2
    assert fake.calls == [] and not (tmp_path / "a.md").exists()
    driver.OPERATOR_PH1_SHA256 = hashlib.sha256(Path(fake.__file__).read_bytes()).hexdigest()
    (tmp_path / "a.md").write_text("already")
    assert driver.run_workflow(fake, ack=lambda m, p: p.write_text("a"), result_path=tmp_path / "r.json", ack_path=tmp_path / "a.md") == 2 and fake.calls == []

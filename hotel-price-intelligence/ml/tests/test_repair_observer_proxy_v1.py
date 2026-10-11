"""audit_tools/repair_observer_worker_v1.py + repair_observer_proxy_v1.py — observer co TIMEOUT CUNG: worker rieng trong Job Object, moi lenh co cap, het gio => giet cay worker, UNKNOWN/quarantine.
Chi dung worker GIA (python con cuc bo, khong DB). Tren Windows."""
from __future__ import annotations

import datetime as dt
import hashlib
import importlib.util
import io
import json
import subprocess
import sys
import time
import types
from pathlib import Path

import pytest

HERE = Path(__file__).resolve().parent
TOOLS = HERE.parents[1] / "audit_tools"


def _load(path, name):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


worker = _load(TOOLS / "repair_observer_worker_v1.py", "repair_observer_worker_v1")
proxy = _load(TOOLS / "repair_observer_proxy_v1.py", "repair_observer_proxy_v1")
jobt = _load(HERE / "test_windows_job_v1.py", "job_tests_for_proxy")
r2t = _load(HERE / "test_repair_phase_runner_v2.py", "runner2_tests_for_proxy")
rr, qm = r2t.rr, r2t.qm
windows_only = pytest.mark.skipif(sys.platform != "win32", reason="Job Object chi co tren Windows")

FAKE_WORKER = '''
import json, os, subprocess, sys, time
MODE = sys.argv[1]
def out(o):
    sys.stdout.write(json.dumps(o) + "\\n"); sys.stdout.flush()
if MODE == "no_ready":
    time.sleep(120)
out({"ready": {"bad_ready": False, "truthy_ready": 1}.get(MODE, True), "pid": os.getpid()})
BASELINE = {"baseline_thread_id": 1000, "baseline_uptime_s": 500.0, "probe_session_id": 5000, "server_uuid": "uuid-1", "app_user": "hotel_app"}
for line in sys.stdin:
    req = json.loads(line)
    op, rid = req["op"], req["id"]
    if op == "close":
        out({"id": rid, "ok": True, "result": "closed"})
        if MODE == "close_stays_alive":
            time.sleep(120)
        break
    if MODE == "hang_" + op:
        gc = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(120)"]); open(sys.argv[2] + ".gc", "w").write(str(gc.pid)); time.sleep(120)
    if MODE == "crash_" + op:
        os._exit(3)
    if MODE == "garbage_" + op:
        sys.stdout.write("not json\\n"); sys.stdout.flush(); continue
    if MODE == "wrong_id_" + op:
        out({"id": rid + 99, "ok": True, "result": {}}); continue
    if MODE == "big_" + op:
        sys.stdout.write("x" * 2000000 + "\\n"); sys.stdout.flush(); continue
    if MODE == "remote_error_" + op and not os.path.exists(sys.argv[2] + ".once"):
        open(sys.argv[2] + ".once", "w").close(); out({"id": rid, "ok": False, "error": "RuntimeError: db down"}); continue
    if op == "baseline":
        out({"id": rid, "ok": True, "result": BASELINE})
    elif op == "probe":
        out({"id": rid, "ok": True, "result": {"status": "ok", "rows": [], "truncated": False, "rolling_back": [], "uptime_s": 900.0 + time.monotonic()}})
    else:
        out({"id": rid, "ok": True, "result": {"op": op, "pid": os.getpid()}})
'''


@pytest.fixture
def fake(tmp_path):
    script = tmp_path / "fake_worker.py"
    script.write_text(FAKE_WORKER, encoding="utf-8")

    def make(mode, name="w", **caps):
        return proxy.BoundedObserver([sys.executable, str(script), mode, str(tmp_path / name)], cwd=str(tmp_path), env=jobt.SYSTEM_ENV, stderr_path=tmp_path / f"{name}.err",
                                     caps={"start": 5.0, "baseline": 5.0, "probe": 5.0, "selftest": 5.0, **caps})
    return make


# =========================================================================== worker (khong process): serve() tren luong trong bo nho
class FakeDbObserver:
    def __init__(self, fail_on=None):
        self.fail_on, self.closed, self.calls = fail_on, 0, []

    def capture_baseline(self):
        self.calls.append("baseline")
        if self.fail_on == "baseline":
            raise RuntimeError("db down " + "x" * 500)
        return {"baseline_thread_id": 1}

    def probe(self):
        self.calls.append("probe")
        return {"status": "ok"}

    def selftest(self, **kw):
        self.calls.append(("selftest", kw))
        return {"visible_other_session": True}

    def close(self):
        self.closed += 1


def run_serve(lines, observer=None, **kw):
    out = io.StringIO()
    rc = worker.serve(io.StringIO("".join(line + "\n" for line in lines)), out, observer or FakeDbObserver(), **kw)
    return rc, [json.loads(x) for x in out.getvalue().splitlines()]


def test_serve_announces_ready_answers_each_op_and_closes_the_observer():
    observer = FakeDbObserver()
    rc, out = run_serve([json.dumps({"id": 1, "op": "baseline"}), json.dumps({"id": 2, "op": "probe"}), json.dumps({"id": 3, "op": "selftest"}), json.dumps({"id": 4, "op": "close"}),
                         json.dumps({"id": 5, "op": "probe"})], observer, selftest_kwargs={"wait_gone_s": 3.0})
    assert rc == 0 and out[0]["ready"] is True and isinstance(out[0]["pid"], int)
    assert out[1:] == [{"id": 1, "ok": True, "result": {"baseline_thread_id": 1}}, {"id": 2, "ok": True, "result": {"status": "ok"}}, {"id": 3, "ok": True, "result": {"visible_other_session": True}},
                       {"id": 4, "ok": True, "result": "closed"}]
    assert observer.calls == ["baseline", "probe", ("selftest", {"wait_gone_s": 3.0})] and observer.closed == 1                       # sau close khong xu ly them (id 5)


def test_serve_reports_a_db_error_clipped_and_keeps_serving_and_rejects_bad_requests_without_dying():
    observer = FakeDbObserver(fail_on="baseline")
    rc, out = run_serve(["not json", json.dumps({"id": "x", "op": "probe"}), json.dumps({"id": 1, "op": "drop"}), json.dumps([1]), json.dumps({"id": 2, "op": "baseline"}),
                         json.dumps({"id": 3, "op": "probe"})], observer)
    assert rc == 0 and [o.get("ok") for o in out[1:]] == [False, False, False, False, False, True]
    assert [o["error"] for o in out[1:5]] == ["bad_request"] * 4 and out[5]["error"].startswith("RuntimeError: db down") and len(out[5]["error"]) <= 230 and out[6] == {"id": 3, "ok": True, "result": {"status": "ok"}}


def test_serve_ends_on_eof_and_still_closes_the_observer():
    observer = FakeDbObserver()
    rc, out = run_serve([], observer)
    assert rc == 0 and out == [out[0]] and observer.closed == 1


def test_main_validates_limits_and_uses_the_injected_observer_factory():
    out = io.StringIO()
    assert worker.main(["--max-statements", "0"], observer_factory=lambda n: FakeDbObserver(), stdin=io.StringIO(""), stdout=out) == 2
    seen = []
    assert worker.main(["--max-statements", "77"], observer_factory=lambda n: seen.append(n) or FakeDbObserver(), stdin=io.StringIO(json.dumps({"id": 1, "op": "close"}) + "\n"), stdout=out) == 0 and seen == [77]


# =========================================================================== proxy: giao thuc, timeout, treo, chet, rac
@windows_only
def test_a_healthy_worker_answers_every_op_runs_inside_a_job_and_dies_with_close(fake):
    obs = fake("ok")
    assert obs.capture_baseline()["server_uuid"] == "uuid-1" and obs.probe()["status"] == "ok" and obs.selftest()["op"] == "selftest"
    pid = obs._proc.pid
    assert pid in obs._job.pids() and [c["op"] for c in obs.calls] == ["baseline", "probe", "selftest"] and all(c["elapsed_s"] < 5 for c in obs.calls)
    obs.close()
    assert jobt.wait_dead(pid) and obs.broken == "closed"
    with pytest.raises(proxy.ObserverWorkerDead):
        obs.probe()


@windows_only
def test_a_hung_call_is_cut_at_its_cap_the_whole_worker_tree_is_killed_and_the_observer_is_dead_afterwards(fake):
    obs = fake("hang_probe", probe=1.0)
    obs.capture_baseline()
    pid = obs._proc.pid
    started = time.monotonic()
    with pytest.raises(proxy.ObserverTimeout, match="probe: no answer within 1.0s"):
        obs.probe()
    elapsed = time.monotonic() - started
    assert 0.9 <= elapsed < 4.0 and obs.broken == "timeout:probe"
    assert jobt.wait_dead(pid)                                                                                  # worker chet; hau due (sleep 120) cung chet nho Job (xem test duoi)
    for call in (obs.probe, obs.capture_baseline, obs.selftest):
        with pytest.raises(proxy.ObserverWorkerDead):
            call()
    obs.close()


@windows_only
def test_the_grandchild_of_a_hung_worker_does_not_survive_the_timeout(fake, tmp_path):
    obs = fake("hang_probe", probe=1.0)
    obs.capture_baseline()
    with pytest.raises(proxy.ObserverTimeout):
        obs.probe()
    grandchild = int((tmp_path / "w.gc").read_text())
    assert jobt.wait_dead(grandchild) and obs._job is None                                                      # hau due cua worker treo cung chet (Job kill-on-close)


@windows_only
def test_a_worker_that_never_reports_ready_times_out_at_the_start_cap(fake):
    obs = fake("no_ready", start=1.0)
    started = time.monotonic()
    with pytest.raises(proxy.ObserverTimeout, match="start"):
        obs.capture_baseline()
    assert time.monotonic() - started < 4.0 and obs.broken == "timeout:start"


@windows_only
@pytest.mark.parametrize("mode,error,text,reason", [("crash_probe", proxy.ObserverWorkerDead, "worker stream eof", "eof"), ("garbage_probe", proxy.ObserverProtocolError, "non-JSON", "garbage"),
                                                    ("wrong_id_probe", proxy.ObserverProtocolError, "does not match request id", "id_mismatch"),
                                                    ("big_probe", proxy.ObserverWorkerDead, "worker stream too_large", "too_large")])
def test_crash_garbage_wrong_id_and_oversize_answers_break_the_worker_loudly(fake, mode, error, text, reason):
    obs = fake(mode)
    obs.capture_baseline()
    with pytest.raises(error, match=text):
        obs.probe()
    assert obs.broken == reason
    with pytest.raises(proxy.ObserverWorkerDead):
        obs.capture_baseline()


@windows_only
@pytest.mark.parametrize("mode", ["bad_ready", "truthy_ready"])
def test_a_worker_whose_first_line_is_not_exactly_the_ready_banner_is_rejected_even_though_it_would_answer(fake, mode):
    obs = fake(mode)
    with pytest.raises(proxy.ObserverProtocolError, match="did_not_report_ready"):
        obs.capture_baseline()
    assert obs.broken == "bad_ready" and obs._proc is None and obs.calls == []                                   # khong co lenh nao duoc gui toi worker


@windows_only
def test_closing_tears_the_worker_tree_down_even_when_it_acknowledged_close_but_stays_alive(fake):
    obs = fake("close_stays_alive")
    obs.capture_baseline()
    pid = obs._proc.pid
    obs.close()
    assert jobt.wait_dead(pid, timeout=5.0) and obs._proc is None and obs._job is None and obs.broken == "closed"


@windows_only
def test_a_request_that_cannot_be_written_breaks_the_worker_loudly_instead_of_returning_nothing(fake):
    obs = fake("ok")
    obs.capture_baseline()
    pid = obs._proc.pid
    obs._proc.stdin.close()                                                                                      # duong ong yeu cau bi dong: ghi se loi
    with pytest.raises(proxy.ObserverWorkerDead, match="cannot write to worker"):
        obs.probe()
    assert obs.broken == "stdin_closed" and jobt.wait_dead(pid, timeout=5.0) and obs._proc is None
    with pytest.raises(proxy.ObserverWorkerDead, match="worker unusable: stdin_closed"):
        obs.probe()


@windows_only
def test_the_tooling_hash_is_recomputed_from_the_bytes_of_proxy_worker_observer_model_and_job(fake):
    obs = fake("ok")

    def digest(name):
        return hashlib.sha256((TOOLS / name).read_bytes()).hexdigest()

    parts = {"proxy": digest("repair_observer_proxy_v1.py"), "worker": digest("repair_observer_worker_v1.py"), "observer": digest("repair_db_observer_v1.py"),
             "quiescence_model": digest("quiescence_model_v1.py"), "windows_job": digest("windows_job_v1.py")}
    assert obs.source_sha256 == parts["windows_job"] and obs.tooling_sha256 == hashlib.sha256(json.dumps(parts, sort_keys=True).encode("utf-8")).hexdigest()
    assert len(set(parts.values())) == 5                                                                         # nam thanh phan khac nhau, moi cai co mat trong hash


@windows_only
def test_the_tooling_hash_changes_when_any_one_component_changes(fake, tmp_path, monkeypatch):
    base = fake("ok").tooling_sha256
    original = Path.read_bytes
    for needle in ("repair_observer_worker_v1.py", "repair_db_observer_v1.py", "quiescence_model_v1.py"):
        monkeypatch.setattr(Path, "read_bytes", lambda self, needle=needle: original(self) + (b"#" if self.name == needle else b""))
        assert fake("ok").tooling_sha256 != base, needle
    monkeypatch.setattr(Path, "read_bytes", lambda self: original(self) + (b"#" if self.name == "repair_observer_proxy_v1.py" else b""))
    assert fake("ok").tooling_sha256 != base


@windows_only
def test_a_remote_db_error_is_raised_as_such_but_the_worker_stays_usable(fake):
    obs = fake("remote_error_probe")
    obs.capture_baseline()
    with pytest.raises(proxy.ObserverRemoteError, match="db down"):
        obs.probe()
    assert obs.broken is None and obs.probe()["status"] == "ok"
    obs.close()


@windows_only
def test_invalid_caps_and_an_existing_stderr_file_are_rejected_without_leaving_a_process(fake, tmp_path):
    with pytest.raises(ValueError, match="cap_invalid"):
        fake("ok", probe=0)
    with pytest.raises(ValueError, match="cap_invalid"):
        fake("ok", probe=float("nan"))
    with pytest.raises(ValueError, match="cap_invalid"):
        fake("ok", probe=True)
    (tmp_path / "w.err").write_text("existing", encoding="utf-8")
    obs = fake("ok")
    with pytest.raises(FileExistsError):
        obs.capture_baseline()
    assert (tmp_path / "w.err").read_text(encoding="utf-8") == "existing" and obs._proc is None


@windows_only
def test_closing_an_observer_that_never_started_is_a_noop(fake):
    obs = fake("ok")
    obs.close()
    assert obs.broken == "closed"


# =========================================================================== nhanh assign Job that bai (GPT 76 §3)
SLEEPER_ROOT = """
import time
time.sleep(120)
"""


class FailingAssignJob:
    instances = []

    def __init__(self):
        self.killed = self.closed = 0
        FailingAssignJob.instances.append(self)

    def assign_process_handle(self, handle):
        raise OSError("assign failed (injected)")

    def kill_tree(self):
        self.killed += 1

    def close(self):
        self.closed += 1


def failing_assign_observer(tmp_path, monkeypatch, **kw):
    """Root worker NGU 120 s, KHONG doc stdin; Job gia luon that bai khi gan; ghi lai moi Popen de kiem tra root (subclass, de thu vien khac van dung duoc)."""
    FailingAssignJob.instances = []
    created = []
    real = subprocess.Popen

    class Tracking(real):
        def __init__(self, *a, **k):
            super().__init__(*a, **k)
            created.append(self)

    monkeypatch.setattr(proxy.subprocess, "Popen", Tracking)
    script = tmp_path / "sleeper_root.py"
    script.write_text(SLEEPER_ROOT, encoding="utf-8")
    wj = types.SimpleNamespace(Job=FailingAssignJob, **{"__source_sha256__": "f" * 64})
    obs = proxy.BoundedObserver([sys.executable, str(script)], cwd=str(tmp_path), env=jobt.SYSTEM_ENV, stderr_path=tmp_path / "assign.err", wj=wj, **kw)
    return obs, created


@windows_only
def test_a_failed_job_assignment_kills_and_reaps_the_own_root_worker_and_leaves_the_observer_dead(tmp_path, monkeypatch):
    obs, created = failing_assign_observer(tmp_path, monkeypatch)
    with pytest.raises(OSError, match="assign failed"):
        obs.capture_baseline()
    assert len(created) == 1 and jobt.wait_dead(created[0].pid, timeout=5.0)                                    # root KHONG thuoc Job nhung van chet (GPT 76: truoc day van song)
    assert created[0].poll() is not None and obs._proc is None and obs.cleanup_verified is True and obs.broken == "start_failed"
    job = FailingAssignJob.instances[0]
    assert job.killed == 1 and job.closed == 1
    with pytest.raises(proxy.ObserverWorkerDead, match="worker unusable: start_failed"):
        obs.probe()                                                                                              # khong retry, khong worker thu hai
    assert len(created) == 1


@windows_only
def test_a_root_that_cannot_be_verified_dead_after_a_failed_start_is_reported_and_its_reference_is_kept(tmp_path, monkeypatch):
    obs, created = failing_assign_observer(tmp_path, monkeypatch)
    monkeypatch.setattr(obs, "_teardown", lambda: False)                                                        # mo phong: don dep khong xac minh duoc
    try:
        with pytest.raises(proxy.ObserverCleanupError, match=r"worker root pid=\d+ not verified dead") as info:
            obs.capture_baseline()
        assert isinstance(info.value.__cause__, OSError) and obs.broken == "start_failed"
    finally:
        for proc in created:                                                                                     # chi giet DUNG root do test nay tao
            proc.kill()
            proc.wait(timeout=10)


def test_teardown_keeps_the_process_reference_and_reports_false_when_the_root_never_dies():
    class Stream:
        def close(self):
            pass

    class Stuck:
        pid = 4242
        stdin, stdout = Stream(), Stream()
        kills = 0

        def poll(self):
            return None

        def kill(self):
            Stuck.kills += 1

        def wait(self, timeout=None):
            raise subprocess.TimeoutExpired("stuck", timeout)

    obs = proxy.BoundedObserver(["x"], cwd=".", env={}, stderr_path="unused.err", wj=types.SimpleNamespace(Job=FailingAssignJob, **{"__source_sha256__": "f" * 64}))
    stuck = Stuck()
    obs._proc, obs._job, obs._stderr = stuck, None, io.BytesIO()
    assert obs._teardown() is False and obs._proc is stuck and obs.cleanup_verified is False and Stuck.kills == 1


def test_overhead_is_the_worker_start_cap_plus_the_teardown_verification_wait():
    obs = proxy.BoundedObserver(["x"], cwd=".", env={}, stderr_path="unused.err", caps={"start": 7.0})
    assert obs.overhead_s == 7.0 + proxy.TEARDOWN_WAIT_S == 12.0
    assert proxy.BoundedObserver(["x"], cwd=".", env={}, stderr_path="unused.err").overhead_s == 25.0


# =========================================================================== tich hop voi runner v2 (dong ho THAT, worker gia)
class SystemClock:
    def monotonic(self):
        return time.monotonic()

    def sleep(self, seconds):
        time.sleep(seconds)

    def now_utc(self):
        return dt.datetime.now(dt.timezone.utc)


class InstantLauncher:
    source_sha256 = "j" * 64

    def launch(self, spec, cap_s, should_abort=None, gate_poll_s=5.0):
        return {"exit_code": 0, "timed_out": False, "aborted_by_gate": None, "gate_polls": 0, "killed_tree": False, "contained": True, "pids_seen": [4242], "stdout_path": "x", "stderr_path": "y",
                "stdout_sha256": "a" * 64, "stderr_sha256": "b" * 64, "started": 0.0, "ended": 1.0}


def real_clock_runner(tmp_path, observer):
    clock = SystemClock()
    script = tmp_path / "work.py"
    script.write_text("print(1)\n", encoding="utf-8")
    plan = tmp_path / "plan"
    plan.mkdir()
    q0_path, q0_sha = r2t.write_q0(tmp_path, created_at_utc=(clock.now_utc() - dt.timedelta(minutes=5)).strftime("%Y-%m-%dT%H:%M:%SZ"))
    q0 = rr.load_q0_receipt(q0_path, expected_receipt_sha256=q0_sha, expected_observer_sha256=r2t.OBSERVER_SHA, expected_sql_set_sha256=r2t.SQL_SHA, expected_model_sha256=qm.__source_sha256__,
                            now_utc=clock.now_utc(), max_age_s=3600.0)
    spec = {"name": "ph_w", "kind": "write", "argv": ["x"], "cwd": str(tmp_path), "env": {"SystemRoot": "C:\\Windows"}, "required_files": {str(script): rr.file_sha256(script)}, "deadline_s": 30,
            "db": {"app_user": "hotel_app", "db_scope": ["warehouse_dsdev_nf"], "protected_dbs": ["hotel_price_intel"]}}
    settings = {**rr.DEFAULT_SETTINGS, "window_end_vn": None, "total_budget_s": 900.0, "settle_s": 0.0, "margin_s": 1.0, "baseline_cap_s": 5.0, "probe_cap_s": 5.0, "containment_grace_s": 1.0,
                "receipt_reserve_s": 1.0}
    launcher = InstantLauncher()
    tooling = rr.tooling_for(launcher, observer)
    runner = rr.Runner(plan_dir=plan, specs=[spec], settings=settings, expected_tooling=tooling, q0_receipt_sha256=q0_sha, approved_plan_sha256=rr.plan_sha256([spec], settings, tooling, q0_sha),
                       launcher=launcher, clock=clock, now_utc=clock.now_utc, gate=lambda: (True, []), observer=observer, q0=q0)
    return runner


@windows_only
def test_runner_with_a_bounded_observer_passes_when_the_worker_is_healthy(fake, tmp_path):
    obs = fake("ok", probe=3.0, baseline=3.0)
    receipts = real_clock_runner(tmp_path, obs).run_plan()
    assert receipts[0]["verdict"] == rr.PASS and receipts[0]["quiescence"]["probe_count"] == 2 and obs.broken == "closed"                       # runner dong observer o cuoi plan


@windows_only
def test_runner_quarantines_within_the_cap_when_a_probe_hangs_and_refuses_when_the_baseline_hangs(fake, tmp_path):
    obs = fake("hang_probe", probe=1.0, baseline=3.0)
    started = time.monotonic()
    receipts = real_clock_runner(tmp_path, obs).run_plan()
    assert receipts[0]["verdict"] == rr.QUARANTINE and receipts[0]["quiescence"]["status"] == qm.UNKNOWN and time.monotonic() - started < 20
    assert any(p.get("status") == "error" for p in receipts[0]["quiescence"]["probes"])
    other = tmp_path / "second"
    other.mkdir()
    hung = proxy.BoundedObserver([sys.executable, str(tmp_path / "fake_worker.py"), "hang_baseline", str(other / "w")], cwd=str(other), env=jobt.SYSTEM_ENV, stderr_path=other / "w.err",
                                 caps={"start": 5.0, "baseline": 1.0, "probe": 3.0})
    started = time.monotonic()
    receipts = real_clock_runner(other, hung).run_plan()
    assert receipts[0]["verdict"] == rr.REFUSED and "baseline_capture_failed" in receipts[0]["reasons"][0] and time.monotonic() - started < 15

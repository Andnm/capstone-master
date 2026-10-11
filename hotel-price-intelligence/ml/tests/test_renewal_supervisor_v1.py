"""audit_tools/renewal_supervisor_v1.py — supervisor cua publisher gia han (82c): tu choi dung dieu kien, moi truong hermetic, hard wall deadline, dung/reap/xac minh chet CHINH process; test voi child gia (khong DB)."""
from __future__ import annotations

import argparse
import datetime as dt
import hashlib
import importlib.util
import json
import subprocess
import sys
import time
from pathlib import Path

import pytest

HERE = Path(__file__).resolve().parent
TOOLS = HERE.parents[1] / "audit_tools"


def _load(path, name):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


sv = _load(TOOLS / "renewal_supervisor_v1.py", "renewal_supervisor_v1")
T0 = dt.datetime(2026, 10, 9, 10, 5, tzinfo=dt.timezone.utc)                                                # 17:05 VN, trong cua so khoi dong
ENV6 = {"SystemRoot": r"C:\Windows", "TEMP": r"C:\Windows\Temp", "TMP": r"C:\Windows\Temp", "PATH": r"C:\Windows\System32;C:\Windows", "PYTHONUTF8": "1", "PYTHONIOENCODING": "utf-8"}


class Clock:
    def __init__(self, start=T0):
        self.now = start

    def __call__(self):
        return self.now

    def sleep(self, seconds):
        self.now += dt.timedelta(seconds=seconds)
        if self.now > T0 + dt.timedelta(hours=3):
            raise RuntimeError("runaway supervision loop")                                                       # vong lap khong ket thuc phai THAT BAI test, khong treo harness


class FakeChild:
    pid = 4321

    def __init__(self, lives_s=None, clock=None, die_on_kill=True):
        self.clock, self.lives_s, self.die_on_kill = clock, lives_s, die_on_kill
        self.birth, self.code, self.killed, self.waited = clock.now if clock else None, None, 0, 0

    def poll(self):
        if self.code is None and self.lives_s is not None and self.clock.now >= self.birth + dt.timedelta(seconds=self.lives_s):
            self.code = 0
        return self.code

    def kill(self):
        self.killed += 1
        if self.die_on_kill:
            self.code = 1

    def wait(self, timeout=None):
        self.waited += 1
        if self.code is None:
            raise subprocess.TimeoutExpired("fake", timeout)


@pytest.fixture
def world(tmp_path):
    source = tmp_path / "publisher.py"
    source.write_text("print('x')\n", encoding="utf-8")
    interpreter = tmp_path / "python.exe"
    interpreter.write_bytes(b"fake interpreter bytes")
    live, archive = tmp_path / "run" / "live.json", tmp_path / "run" / "archive"
    (tmp_path / "run").mkdir()
    argv = [str(interpreter), str(source), "--out", str(live), "--renew-seconds", "900", "--interval", "60", "--archive-dir", str(archive)]
    profile = {"kind": "renewal_publisher_profile", "argv": argv, "cwd": str(tmp_path), "env": dict(ENV6), "renew_seconds": 900, "interval_s": 60, "source": {"path": str(source), "sha256": hashlib.sha256(source.read_bytes()).hexdigest()},
               "outputs": {"live": str(live), "archive_dir": str(archive), "log": str(tmp_path / "renewal.log")}}
    args = argparse.Namespace(expected_profile_sha256="p" * 64, expected_source_sha256=profile["source"]["sha256"], expected_interpreter_sha256=hashlib.sha256(interpreter.read_bytes()).hexdigest(),
                              not_before_utc="2026-10-09T10:00:00Z", start_deadline_utc="2026-10-09T11:00:00Z", hard_not_after_utc="2026-10-09T11:30:00Z", max_pool_size=5,
                              publisher_log=tmp_path / "publisher.log", stop_file=tmp_path / "STOP", supervisor_log=tmp_path / "sup.jsonl")
    return profile, args, tmp_path


def events(path):
    return [json.loads(line) for line in Path(path).read_text(encoding="utf-8").splitlines()]


def run(world, *, child=None, clock=None, pool=(5, None), popen=None):
    profile, args, tmp = world
    clock = clock or Clock()
    log = sv.Log(args.supervisor_log, clock)
    spawned = []

    def fake_popen(argv, **kwargs):
        spawned.append((argv, kwargs))
        return child or FakeChild(lives_s=30, clock=clock)
    code = sv.supervise(profile, args, log=log, clock=clock, sleep=clock.sleep, popen=popen or fake_popen, pool_check=lambda p, m: pool)
    return code, events(args.supervisor_log), spawned


# =========================================================================== tu choi truoc khi tao process
def test_a_matching_profile_in_the_window_starts_exactly_one_process_with_the_hermetic_environment_and_a_direct_file_sink(world):
    profile, args, tmp = world
    code, ev, spawned = run(world)
    assert code == 0 and len(spawned) == 1
    argv, kwargs = spawned[0]
    assert argv == profile["argv"] and kwargs["cwd"] == profile["cwd"] and kwargs["env"] == ENV6 and kwargs["stdin"] == subprocess.DEVNULL and kwargs["stderr"] == subprocess.STDOUT
    assert kwargs["creationflags"] == sv.CREATE_NO_WINDOW and kwargs["stdout"] != subprocess.PIPE and getattr(kwargs["stdout"], "name", None) == str(args.publisher_log)    # tep that (ghi thang), khong phai pipe
    assert [e["event"] for e in ev] == ["pool_size_checked", "started", "stopped"] and ev[0]["pool_size"] == 5
    started, stopped = ev[1], ev[2]
    assert started["pid"] == 4321 and started["argv"] == profile["argv"] and started["wall_deadline_utc"] == "2026-10-09T10:20:00Z" and started["profile_sha256"] == "p" * 64
    assert stopped["reason"] == "exited" and stopped["verified_dead"] is True and stopped["killed_by_supervisor"] is False and stopped["exit_code"] == 0
    assert stopped["publisher_log_sha256"] == hashlib.sha256(args.publisher_log.read_bytes()).hexdigest() and Path(profile["outputs"]["archive_dir"]).is_dir()


@pytest.mark.parametrize("mutate,reason", [
    (lambda p, a: a.__setattr__("not_before_utc", "2026-10-09T10:06:00Z"), "outside_start_window"), (lambda p, a: a.__setattr__("start_deadline_utc", "2026-10-09T10:05:00Z"), "outside_start_window"),
    (lambda p, a: a.__setattr__("hard_not_after_utc", "2026-10-09T10:05:00Z"), "after_hard_not_after"), (lambda p, a: a.stop_file.write_text("x"), "stop_file_already_exists"),
    (lambda p, a: a.publisher_log.write_text("x"), "publisher_log_already_exists"), (lambda p, a: Path(p["outputs"]["live"]).write_text("old"), "live_evidence_already_exists"),
    (lambda p, a: (Path(p["outputs"]["archive_dir"]).mkdir(), (Path(p["outputs"]["archive_dir"]) / "old.json").write_text("x")), "archive_dir_not_empty"),
])
def test_every_precondition_failure_refuses_before_any_process_exists(world, mutate, reason):
    profile, args, tmp = world
    mutate(profile, args)
    code, ev, spawned = run(world)
    assert code == 2 and spawned == [] and ev == [{"at_utc": ev[0]["at_utc"], "event": "refused", "reasons": ev[0]["reasons"]}] and reason in ev[0]["reasons"]


@pytest.mark.parametrize("which", ["source", "interpreter", "kind", "env_extra", "argv_flags", "argv_len", "argv_flag_name", "argv_out_path", "argv_interval"])
def test_a_profile_that_differs_from_the_approved_bytes_or_shape_is_refused(world, which):
    profile, args, tmp = world
    if which == "source":
        Path(profile["source"]["path"]).write_text("print('tampered')\n", encoding="utf-8")
    elif which == "interpreter":
        Path(profile["argv"][0]).write_bytes(b"other")
    elif which == "kind":
        profile["kind"] = "other"
    elif which == "env_extra":
        profile["env"]["DB_NAME"] = "x"
    elif which == "argv_flags":
        profile["argv"][5] = "9999"
    elif which == "argv_flag_name":
        profile["argv"][4] = "--duration"
    elif which == "argv_out_path":
        profile["argv"][3] = profile["argv"][3] + ".other"
    elif which == "argv_interval":
        profile["argv"][7] = "5"
    else:
        profile["argv"].append("--extra")
    code, ev, spawned = run(world)
    assert code == 2 and spawned == [] and ev[0]["event"] == "refused"


@pytest.mark.parametrize("pool,reason", [((6, "pool_size_above_scope"), "pool_size_above_scope"), ((None, "pool_size_unreadable"), "pool_size_unreadable"), ((3, "pool_size_check_failed"), "pool_size_check_failed")])
def test_a_pool_size_above_scope_or_unreadable_refuses_without_creating_a_pool_or_the_archive_dir(world, pool, reason):
    profile, args, tmp = world
    code, ev, spawned = run(world, pool=pool)
    assert code == 2 and spawned == [] and ev[-1]["reasons"] == [reason] and ev[0]["event"] == "pool_size_checked" and not Path(profile["outputs"]["archive_dir"]).exists()


def test_check_pool_size_runs_one_isolated_hermetic_child_and_judges_only_the_integer(world):
    profile, args, tmp = world
    calls = []

    def fake_run(cmd, **kw):
        calls.append((cmd, kw))
        return subprocess.CompletedProcess(cmd, 0, stdout="noise\n5\n", stderr="")
    assert sv.check_pool_size(profile, 5, run=fake_run) == (5, None)
    cmd, kw = calls[0]
    assert cmd == [profile["argv"][0], "-I", "-c", sv.POOL_CHECK_SNIPPET] and kw["cwd"] == profile["cwd"] and kw["env"] == ENV6 and kw["timeout"] == 30 and "database" not in sv.POOL_CHECK_SNIPPET
    assert sv.check_pool_size(profile, 5, run=lambda c, **k: subprocess.CompletedProcess(c, 0, stdout="6\n", stderr="")) == (6, "pool_size_above_scope")
    assert sv.check_pool_size(profile, 5, run=lambda c, **k: subprocess.CompletedProcess(c, 0, stdout="0\n", stderr="")) == (0, "pool_size_above_scope")
    assert sv.check_pool_size(profile, 5, run=lambda c, **k: subprocess.CompletedProcess(c, 0, stdout="", stderr="")) == (None, "pool_size_unreadable")
    assert sv.check_pool_size(profile, 5, run=lambda c, **k: subprocess.CompletedProcess(c, 1, stdout="4\n", stderr="")) == (4, "pool_size_check_failed")


def _fake_backend(root: Path, pool: int) -> Path:
    package = root / "app" / "core"
    package.mkdir(parents=True)
    (root / "app" / "__init__.py").write_text("", encoding="utf-8")
    (package / "__init__.py").write_text("", encoding="utf-8")
    (package / "config.py").write_text("class _S:\n    DB_POOL_SIZE = %d\nsettings = _S()\n" % pool, encoding="utf-8")
    return root


def test_pool_check_snippet_imports_app_from_cwd_under_isolated_mode_with_a_real_child(tmp_path):
    """Hoi quy thuc te 09/10: `python -I` bo cwd khoi sys.path nen `app` khong import duoc -> pool_size_unreadable. Chay child THAT (khong fake) voi goi `app` tam trong cwd."""
    ok_dir, big_dir, empty = _fake_backend(tmp_path / "ok", 3), _fake_backend(tmp_path / "big", 9), tmp_path / "empty"
    empty.mkdir()
    def profile_for(cwd):
        return {"argv": [sys.executable], "cwd": str(cwd), "env": dict(ENV6)}
    assert sv.check_pool_size(profile_for(ok_dir), 5) == (3, None)
    assert sv.check_pool_size(profile_for(big_dir), 5) == (9, "pool_size_above_scope")
    assert sv.check_pool_size(profile_for(empty), 5) == (None, "pool_size_unreadable")


# =========================================================================== 84: cleanup moi duong thoat + kiem lai sau pool-check
class FlakyLog(sv.Log):
    """Log cua supervisor nhung `event(fail_kind)` nem OSError (mo phong dia day/khoa tep sau khi child da duoc tao)."""

    def __init__(self, path, clock, fail_kind):
        super().__init__(path, clock)
        self.fail_kind = fail_kind

    def event(self, kind, **fields):
        if kind == self.fail_kind:
            raise OSError("disk full (simulated)")
        super().event(kind, **fields)


def test_84_a_failing_started_log_after_the_spawn_still_kills_reaps_and_verifies_the_owned_child(world):
    profile, args, tmp = world
    clock = Clock()
    child = FakeChild(lives_s=None, clock=clock)
    log = FlakyLog(args.supervisor_log, clock, "started")
    with pytest.raises(OSError):
        sv.supervise(profile, args, log=log, clock=clock, sleep=clock.sleep, popen=lambda *a, **k: child, pool_check=lambda p, m: (5, None))
    assert child.killed == 1 and child.waited >= 1 and child.poll() is not None
    stopped = [e for e in events(args.supervisor_log) if e["event"] == "stopped"]
    assert len(stopped) == 1 and stopped[0]["reason"] == "supervisor_exception" and stopped[0]["verified_dead"] is True and stopped[0]["killed_by_supervisor"] is True


def test_84_a_failing_stopped_log_on_the_normal_path_is_not_swallowed(world):
    profile, args, tmp = world
    clock = Clock()
    child = FakeChild(lives_s=30, clock=clock)
    with pytest.raises(OSError):
        sv.supervise(profile, args, log=FlakyLog(args.supervisor_log, clock, "stopped"), clock=clock, sleep=clock.sleep, popen=lambda *a, **k: child, pool_check=lambda p, m: (5, None))
    assert child.poll() is not None                                                                              # child da chet; loi ghi nhat ky khong bi nuot (ma thoat != 0)


def test_84_a_keyboard_interrupt_in_the_supervision_loop_still_kills_reaps_and_verifies_the_owned_child(world):
    profile, args, tmp = world
    clock = Clock()
    child = FakeChild(lives_s=None, clock=clock)

    def interrupted_sleep(seconds):
        raise KeyboardInterrupt
    with pytest.raises(KeyboardInterrupt):
        sv.supervise(profile, args, log=sv.Log(args.supervisor_log, clock), clock=clock, sleep=interrupted_sleep, popen=lambda *a, **k: child, pool_check=lambda p, m: (5, None))
    assert child.killed == 1 and child.poll() is not None
    ev = events(args.supervisor_log)
    assert [e["event"] for e in ev] == ["pool_size_checked", "started", "stopped"] and ev[-1]["reason"] == "supervisor_exception" and ev[-1]["verified_dead"] is True


def test_84_unverifiable_cleanup_on_an_exception_path_is_recorded_as_not_dead_and_the_exception_still_propagates(world):
    profile, args, tmp = world
    clock = Clock()
    child = FakeChild(lives_s=None, clock=clock, die_on_kill=False)
    with pytest.raises(KeyboardInterrupt):
        sv.supervise(profile, args, log=sv.Log(args.supervisor_log, clock), clock=clock, sleep=lambda s: (_ for _ in ()).throw(KeyboardInterrupt), popen=lambda *a, **k: child,
                     pool_check=lambda p, m: (5, None))
    stopped = [e for e in events(args.supervisor_log) if e["event"] == "stopped"][-1]
    assert child.killed == 1 and stopped["verified_dead"] is False and stopped["reason"] == "supervisor_exception"


def test_84_pool_check_that_crosses_the_start_deadline_spawns_nothing(world):
    profile, args, tmp = world
    clock = Clock(start=dt.datetime(2026, 10, 9, 10, 59, 50, tzinfo=dt.timezone.utc))
    spawned = []

    def slow_pool_check(profile_, max_pool):
        clock.now += dt.timedelta(seconds=20)                                                                    # kiem pool ton 20 s (< timeout 30 s) -> qua 11:00:00Z
        return 5, None
    code = sv.supervise(profile, args, log=sv.Log(args.supervisor_log, clock), clock=clock, sleep=clock.sleep, popen=lambda *a, **k: spawned.append(1), pool_check=slow_pool_check)
    ev = events(args.supervisor_log)
    assert code == 2 and spawned == [] and ev[-1]["event"] == "refused" and ev[-1]["stage"] == "after_pool_check" and "outside_start_window" in ev[-1]["reasons"]
    assert not Path(args.publisher_log).exists() and not Path(profile["outputs"]["archive_dir"]).exists()


def test_84_stop_file_created_during_the_pool_check_spawns_nothing(world):
    profile, args, tmp = world
    clock = Clock()
    spawned = []

    def pool_check_with_stop(profile_, max_pool):
        Path(args.stop_file).write_text("stop", encoding="utf-8")
        return 5, None
    code = sv.supervise(profile, args, log=sv.Log(args.supervisor_log, clock), clock=clock, sleep=clock.sleep, popen=lambda *a, **k: spawned.append(1), pool_check=pool_check_with_stop)
    ev = events(args.supervisor_log)
    assert code == 2 and spawned == [] and ev[-1]["reasons"] == ["stop_file_already_exists"] and ev[-1]["stage"] == "after_pool_check"


# =========================================================================== vong doi: dung, deadline, reap
def test_a_stop_request_kills_only_the_owned_process_reaps_it_and_verifies_death(world):
    profile, args, tmp = world
    clock = Clock()
    child = FakeChild(lives_s=None, clock=clock)

    class StoppingClock(Clock):
        def sleep(self, seconds):
            super().sleep(seconds)
            if self.now >= T0 + dt.timedelta(seconds=40):
                args.stop_file.write_text("stop")
    clock = StoppingClock()
    child = FakeChild(lives_s=None, clock=clock)
    code, ev, spawned = run(world, child=child, clock=clock)
    stopped = ev[-1]
    assert code == 0 and stopped["reason"] == "stop_requested" and stopped["killed_by_supervisor"] is True and stopped["verified_dead"] is True and child.killed == 1 and child.waited == 1


def test_the_wall_deadline_is_start_plus_n_seconds_and_never_after_the_hard_not_after(world):
    profile, args, tmp = world
    clock = Clock()
    child = FakeChild(lives_s=None, clock=clock)
    code, ev, _ = run(world, child=child, clock=clock)
    assert ev[1]["wall_deadline_utc"] == "2026-10-09T10:20:00Z" and ev[-1]["reason"] == "wall_deadline" and clock.now == T0 + dt.timedelta(seconds=900) and child.killed == 1
    for f in tmp.glob("*.jsonl"):
        f.unlink()
    (tmp / "publisher.log").unlink()
    args.hard_not_after_utc = "2026-10-09T10:08:00Z"
    clock2 = Clock()
    code, ev, _ = run(world, child=FakeChild(lives_s=None, clock=clock2), clock=clock2)
    assert ev[1]["wall_deadline_utc"] == "2026-10-09T10:08:00Z" and clock2.now == T0 + dt.timedelta(minutes=3)


def test_a_child_that_survives_the_kill_is_reported_unverified_with_exit_code_three(world):
    clock = Clock()
    child = FakeChild(lives_s=None, clock=clock, die_on_kill=False)
    code, ev, _ = run(world, child=child, clock=clock)
    assert code == 3 and ev[-1]["verified_dead"] is False and ev[-1]["killed_by_supervisor"] is True and ev[-1]["exit_code"] is None


def test_a_kill_that_raises_is_logged_and_the_death_check_decides_the_exit_code(world):
    clock = Clock()

    class Hostile(FakeChild):
        def kill(self):
            raise PermissionError("denied")
    code, ev, _ = run(world, child=Hostile(lives_s=None, clock=clock), clock=clock)
    assert code == 3 and any(e["event"] == "kill_error" and "PermissionError" in e["error"] for e in ev) and ev[-1]["verified_dead"] is False


def test_the_supervisor_log_is_append_only_and_the_publisher_log_cannot_be_overwritten(world, monkeypatch):
    profile, args, tmp = world
    args.supervisor_log.write_text('{"event": "earlier"}\n', encoding="utf-8")
    run(world)
    assert events(args.supervisor_log)[0] == {"event": "earlier"} and len(events(args.supervisor_log)) == 4
    code, ev, _ = run(world)
    assert code == 2 and ev[-1]["reasons"] == ["publisher_log_already_exists"]                                  # tep da ton tai => tu choi truoc khi tao process
    args.publisher_log.unlink()
    clock = Clock()
    log = sv.Log(args.supervisor_log, clock)
    spawned = []

    def racing_pool_check(profile_, max_pool):
        args.publisher_log.write_text("someone else", encoding="utf-8")                                         # xuat hien SAU kiem tra tien dieu kien
        return 5, None
    assert sv.supervise(profile, args, log=log, clock=clock, sleep=clock.sleep, popen=lambda *a, **k: spawned.append(1), pool_check=racing_pool_check) == 2    # 84: kiem lai sau pool-check bat duoc
    assert spawned == [] and args.publisher_log.read_text(encoding="utf-8") == "someone else" and events(args.supervisor_log)[-1]["stage"] == "after_pool_check"
    args.publisher_log.unlink()
    real, calls = sv.refusal_reasons, []

    def refusal_then_race(profile_, args_, now):                                                                 # tep xuat hien SAU ca hai lan kiem tra, truoc khi mo `xb`
        result = real(profile_, args_, now)
        calls.append(1)
        if len(calls) == 3:
            args.publisher_log.write_text("someone else", encoding="utf-8")
        return result
    monkeypatch.setattr(sv, "refusal_reasons", refusal_then_race)
    with pytest.raises(FileExistsError):
        sv.supervise(profile, args, log=log, clock=clock, sleep=clock.sleep, popen=lambda *a, **k: spawned.append(1), pool_check=lambda p, m: (5, None))
    assert spawned == [] and args.publisher_log.read_text(encoding="utf-8") == "someone else"                    # `xb`: khong ghi de, khong process nao duoc tao


# =========================================================================== process THAT (khong DB): child ngu, dung bang stop-file, giet va reap
@pytest.mark.skipif(sys.platform != "win32", reason="Windows")
def test_with_a_real_sleeping_child_the_stop_file_ends_it_and_death_is_verified(world):
    profile, args, tmp = world
    script = tmp / "sleeper.py"
    script.write_text("import time\ntime.sleep(120)\n", encoding="utf-8")
    interpreter = Path(sys.executable)
    profile["argv"][0], profile["argv"][1] = str(interpreter), str(script)
    profile["source"] = {"path": str(script), "sha256": hashlib.sha256(script.read_bytes()).hexdigest()}
    args.expected_source_sha256, args.expected_interpreter_sha256 = profile["source"]["sha256"], hashlib.sha256(interpreter.read_bytes()).hexdigest()
    profile["renew_seconds"] = 60
    profile["argv"][5] = "60"
    profile["env"] = {**ENV6}
    import threading

    def stop_later():
        time.sleep(2.5)
        args.stop_file.write_text("stop")
    threading.Thread(target=stop_later, daemon=True).start()
    log = sv.Log(args.supervisor_log)
    code = sv.supervise(profile, args, log=log, clock=lambda: T0, pool_check=lambda p, m: (5, None))
    ev = events(args.supervisor_log)
    assert code == 0 and ev[-1]["reason"] == "stop_requested" and ev[-1]["verified_dead"] is True and ev[-1]["killed_by_supervisor"] is True
    pid = ev[1]["pid"]
    assert ev[1]["creation_time_utc"] is not None and ev[1]["creation_time_utc"].endswith("Z") and ev[1]["creation_time_utc"].startswith("2026-")    # thoi diem tao CUA process that, ghi lai
    done = subprocess.run(["tasklist", "/FI", f"PID eq {pid}", "/NH"], capture_output=True, text=True).stdout
    assert str(pid) not in done                                                                                # process that su het


def test_creation_time_is_none_for_a_pid_that_does_not_exist():
    assert sv.creation_time_utc(2 ** 30) is None


def test_main_refuses_a_profile_whose_bytes_differ_from_the_approved_hash(world, capsys):
    profile, args, tmp = world
    path = tmp / "profile.json"
    path.write_text(json.dumps(profile), encoding="utf-8")
    rc = sv.main(["--profile", str(path), "--expected-profile-sha256", "0" * 64, "--expected-source-sha256", args.expected_source_sha256, "--expected-interpreter-sha256", args.expected_interpreter_sha256,
                  "--not-before-utc", args.not_before_utc, "--start-deadline-utc", args.start_deadline_utc, "--hard-not-after-utc", args.hard_not_after_utc, "--max-pool-size", "5",
                  "--supervisor-log", str(args.supervisor_log), "--publisher-log", str(args.publisher_log), "--stop-file", str(args.stop_file)])
    assert rc == 2 and events(args.supervisor_log)[0]["reasons"] == ["profile_sha256"] and not args.publisher_log.exists()


def test_the_real_q0_profile_validates_against_the_approved_hashes():
    profile_path = Path(r"D:\MSE\CAPSTONE\outputs\repair-run-20261009\renewal_profile_q0.json")
    if not profile_path.exists():
        pytest.skip("profile Q0 khong co tren may nay")
    profile = json.loads(profile_path.read_text(encoding="utf-8"))
    assert hashlib.sha256(profile_path.read_bytes()).hexdigest() == "616162f83bb6869654dd7861bff45459de090b53a8e9cabecf1390154ed14add"
    assert sv.validate_profile(profile, expected_source_sha256="6429482e6deddf711a8228726ec4776d7450bb82b526194299c9eae6c82ed4d9",
                               expected_interpreter_sha256="0e818a1f9a0b8fbd4e7cc458a07cb7de2ea02ea326e387699a33b92f151242cd") == []
    assert sv.hermetic_env(profile) == profile["env"]

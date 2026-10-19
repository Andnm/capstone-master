"""audit_tools/windows_job_v1.py — Job Object that: containment cua child + moi hau due, kill-on-close, khong breakaway, khong race. Chi chay tren Windows; chi sinh python con local, khong DB/mang."""
from __future__ import annotations

import ctypes
import importlib.util
import os
import sys
import time
from pathlib import Path

import pytest

pytestmark = pytest.mark.skipif(sys.platform != "win32", reason="Job Object chi co tren Windows")
TOOLS = Path(__file__).resolve().parents[3] / "audit_tools"
spec = importlib.util.spec_from_file_location("windows_job_v1", TOOLS / "windows_job_v1.py")
wj = importlib.util.module_from_spec(spec)
spec.loader.exec_module(wj)

SLEEPER = "import time; time.sleep(120)"
SYSTEM_ENV = {"SystemRoot": os.environ.get("SystemRoot", r"C:\Windows"), "TEMP": os.environ.get("TEMP", r"C:\Windows\Temp")}


def alive(pid: int) -> bool:
    k = ctypes.WinDLL("kernel32", use_last_error=True)
    k.OpenProcess.restype = ctypes.c_void_p
    handle = k.OpenProcess(0x00100000, False, pid)                                                       # SYNCHRONIZE
    if not handle:
        return False                                                                                       # khong mo duoc => khong con (hoac khong co quyen; process con cua ta luon mo duoc)
    try:
        return k.WaitForSingleObject(ctypes.c_void_p(handle), 0) == wj.WAIT_TIMEOUT
    finally:
        k.CloseHandle(ctypes.c_void_p(handle))


def wait_dead(*pids, timeout=10.0):
    """Doi (poll) toi khi tat ca pid khong con song: ket thuc process la bat dong bo so voi bo dem ActiveProcesses."""
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if not any(alive(pid) for pid in pids):
            return True
        time.sleep(0.05)
    return not any(alive(pid) for pid in pids)


def spawn(job, code, tmp_path, name="c", env=None):
    return job.spawn([sys.executable, "-c", code], cwd=tmp_path, env=env or SYSTEM_ENV, stdout_path=tmp_path / f"{name}.out", stderr_path=tmp_path / f"{name}.err")


def read(path):
    return Path(path).read_text(encoding="utf-8", errors="replace").strip()


def test_spawn_runs_the_child_inside_the_job_captures_output_and_reports_the_exit_code(tmp_path):
    with wj.Job() as job:
        child = spawn(job, "import sys; print('hello'); sys.exit(7)", tmp_path)
        assert child.wait(20) == 7 and read(tmp_path / "c.out") == "hello"
        assert job.wait_empty(10) is True and job.active_process_count() == 0


def test_a_running_root_is_listed_in_the_job_and_wait_times_out_without_killing(tmp_path):
    with wj.Job() as job:
        child = spawn(job, SLEEPER, tmp_path)
        assert child.wait(0.3) is None and alive(child.pid) and child.pid in job.pids() and job.active_process_count() >= 1
        job.kill_tree()
        assert child.wait(10) == 1 and job.wait_empty(10) is True


def test_descendants_that_outlive_the_root_stay_in_the_job_until_the_tree_is_killed(tmp_path):
    code = ("import subprocess, sys; p = subprocess.Popen([sys.executable, '-c', %r]); print(p.pid, flush=True)" % SLEEPER)
    with wj.Job() as job:
        child = spawn(job, code, tmp_path)
        assert child.wait(20) == 0                                                                         # root da thoat
        grandchild = int(read(tmp_path / "c.out"))
        assert alive(grandchild) and grandchild in job.pids() and job.active_process_count() >= 1          # root chet nhung hau due van trong job: quiescence KHONG the dua vao root pid
        job.kill_tree()
        assert job.wait_empty(10) is True and wait_dead(grandchild)


def test_closing_the_job_handle_kills_every_process_kill_on_close(tmp_path):
    code = "import subprocess, sys; p = subprocess.Popen([sys.executable, '-c', %r]); print(p.pid, flush=True); import time; time.sleep(120)" % SLEEPER
    job = wj.Job()
    try:
        child = spawn(job, code, tmp_path)
        deadline = time.monotonic() + 20
        while time.monotonic() < deadline and not (tmp_path / "c.out").read_text().strip():
            time.sleep(0.05)
        grandchild, root = int(read(tmp_path / "c.out")), child.pid
        assert alive(root) and alive(grandchild)
    finally:
        job.close()
    assert wait_dead(root, grandchild)


def test_a_breakaway_attempt_never_produces_a_process_that_survives_the_job(tmp_path):
    """Tinh chat quan trong la KHONG THOAT KHOI job (kill_tree giet het), khong phai viec CreateProcess co bi tu choi hay khong: voi launcher venv (python.exe stub) job long nhau cho phep co
    BREAKAWAY o tang trong nhung tien trinh van thuoc job NGOAI cua ta."""
    code = ("import subprocess, sys\n"
            "try:\n"
            "    p = subprocess.Popen([sys.executable, '-c', %r], creationflags=0x01000000)\n"           # CREATE_BREAKAWAY_FROM_JOB
            "    print('spawned', p.pid, flush=True)\n"
            "except OSError as exc:\n"
            "    print('denied', exc.winerror, flush=True)\n"
            "import time; time.sleep(120)\n" % SLEEPER)
    with wj.Job() as job:
        child = spawn(job, code, tmp_path)
        deadline = time.monotonic() + 20
        while time.monotonic() < deadline and not (tmp_path / "c.out").read_text().strip():
            time.sleep(0.05)
        outcome = read(tmp_path / "c.out").split()
        assert outcome[0] in ("spawned", "denied")
        pids_before_kill = job.pids()
        if outcome[0] == "spawned":
            assert int(outcome[1]) in pids_before_kill                                                      # neu CreateProcess chap nhan thi tien trinh VAN nam trong job ngoai cua ta
        job.kill_tree()
        assert job.wait_empty(10) is True and wait_dead(child.pid, *([int(outcome[1])] if outcome[0] == "spawned" else []))


def test_no_race_a_grandchild_created_as_the_very_first_action_is_already_in_the_job(tmp_path):
    code = "import subprocess, sys; subprocess.Popen([sys.executable, '-c', %r]); import time; time.sleep(120)" % SLEEPER
    with wj.Job() as job:
        child = spawn(job, code, tmp_path)
        deadline = time.monotonic() + 20
        while time.monotonic() < deadline and job.active_process_count() < 2:
            time.sleep(0.05)
        assert job.active_process_count() >= 2                                                              # root + hau due (chi nhin dem trong job, khong dua tren poll pid cua ta)
        job.kill_tree()
        assert job.wait_empty(10) is True and child.wait(10) is not None


def test_many_descendants_are_all_contained_and_killed(tmp_path):
    code = ("import subprocess, sys\n"
            "for _ in range(6):\n"
            "    subprocess.Popen([sys.executable, '-c', %r])\n"
            "import time; time.sleep(120)\n" % SLEEPER)
    with wj.Job() as job:
        spawn(job, code, tmp_path)
        deadline = time.monotonic() + 30
        while time.monotonic() < deadline and job.active_process_count() < 7:
            time.sleep(0.1)
        pids = job.pids()
        assert len(pids) >= 7
        job.kill_tree()
        assert job.wait_empty(15) is True and wait_dead(*pids)


def test_the_environment_is_exactly_what_the_caller_passed_nothing_inherited_silently(tmp_path):
    code = "import os; print(sorted(k.upper() for k in os.environ)); print(os.environ.get('FOO'))"
    with wj.Job() as job:
        child = spawn(job, code, tmp_path, env={**SYSTEM_ENV, "FOO": "bar"})
        assert child.wait(20) == 0
        keys, foo = read(tmp_path / "c.out").splitlines()
        assert foo == "bar" and "FOO" in keys and "USERNAME" not in keys and "USERPROFILE" not in keys and "PATH" not in keys


def test_output_files_are_never_overwritten_and_a_failed_spawn_raises_without_leaving_a_process(tmp_path):
    (tmp_path / "c.out").write_text("existing", encoding="utf-8")
    with wj.Job() as job:
        with pytest.raises(FileExistsError):
            spawn(job, "pass", tmp_path)
        with pytest.raises(OSError):
            job.spawn([str(tmp_path / "no_such_program.exe")], cwd=tmp_path, env=SYSTEM_ENV, stdout_path=tmp_path / "d.out", stderr_path=tmp_path / "d.err")
        assert job.active_process_count() == 0


def test_wait_empty_reports_false_while_something_still_runs_and_pids_never_silently_truncate(tmp_path):
    with wj.Job() as job:
        spawn(job, SLEEPER, tmp_path)
        assert job.wait_empty(0.3) is False
        assert len(job.pids()) >= 1
        job.kill_tree()
        assert job.wait_empty(10) is True


def test_a_failed_stderr_open_never_overwrites_and_leaks_no_handle_on_the_stdout_file(tmp_path):
    (tmp_path / "c.err").write_text("existing", encoding="utf-8")
    with wj.Job() as job:
        with pytest.raises(FileExistsError):
            spawn(job, "pass", tmp_path)
        (tmp_path / "c.out").unlink()                                                                      # xoa duoc => handle cua tep stdout da duoc dong (Windows chan xoa tep dang mo)
        assert (tmp_path / "c.err").read_text(encoding="utf-8") == "existing"


def test_the_child_is_created_suspended_it_has_run_nothing_when_it_is_assigned_to_the_job(tmp_path):
    seen = {}

    def hook(pid):
        time.sleep(0.6)                                                                                    # neu child da chay, no da in ra trong 0.6 s
        seen["output_before_resume"] = (tmp_path / "c.out").read_text()
        seen["count"] = job.active_process_count()
        seen["pid"] = pid

    with wj.Job() as job:
        child = job.spawn([sys.executable, "-c", "print('started')"], cwd=tmp_path, env=SYSTEM_ENV, stdout_path=tmp_path / "c.out", stderr_path=tmp_path / "c.err", before_resume=hook)
        assert seen["output_before_resume"] == "" and seen["count"] >= 1 and seen["pid"] == child.pid
        assert child.wait(20) == 0 and read(tmp_path / "c.out") == "started"


def test_an_undersized_pid_buffer_raises_instead_of_returning_a_partial_list(tmp_path):
    code = "import subprocess, sys; subprocess.Popen([sys.executable, '-c', %r]); import time; time.sleep(120)" % SLEEPER
    with wj.Job() as job:
        spawn(job, code, tmp_path)
        deadline = time.monotonic() + 20
        while time.monotonic() < deadline and job.active_process_count() < 2:
            time.sleep(0.05)
        assert job.active_process_count() >= 2
        with pytest.raises(OSError):                                                                       # HDH tu tra ERROR_MORE_DATA (234) khi bo dem nho; kiem tra assigned>listed la lop phong thu thu hai
            job.pids(max_pids=1)
        assert len(job.pids()) >= 2
        job.kill_tree()
        assert job.wait_empty(10) is True


def test_an_existing_popen_process_can_be_assigned_and_is_killed_with_the_tree_and_on_close(tmp_path):
    import subprocess
    proc = subprocess.Popen([sys.executable, "-c", SLEEPER], stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, creationflags=0x08000000, env=SYSTEM_ENV)
    job = wj.Job()
    try:
        job.assign_process_handle(proc._handle)
        assert proc.pid in job.pids() and alive(proc.pid)
        job.kill_tree()
        assert job.wait_empty(10) is True and wait_dead(proc.pid)
    finally:
        job.close()
        proc.kill()
        proc.wait()
    second = subprocess.Popen([sys.executable, "-c", SLEEPER], stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, creationflags=0x08000000, env=SYSTEM_ENV)
    with wj.Job() as job2:
        job2.assign_process_handle(second._handle)
        assert alive(second.pid)
    assert wait_dead(second.pid)                                                                              # dong job => KILL_ON_JOB_CLOSE giet worker
    second.wait()
    with wj.Job() as job3:
        with pytest.raises(OSError):
            job3.assign_process_handle(0)

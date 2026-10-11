"""Operator Ph1-only (90a + 90b): CHI cho plan Ph1 r83 (readonly serial h1+h3). Ban sao co chu dich cua operator_q0.py (da duoc 88 duyet) voi hang so Ph1; KHONG monkeypatch Q0, KHONG dung lai tep Q0.
Moi buoc in MOT dong JSON va ghi vao ph1_operator_log.jsonl (noi tiep). Khong co retry.

    archive_q0_live  (1 lan, TRUOC start) di chuyen KHONG ghi de live Q0 sang dich da bind trong 90b (chi khi hash dung + Q0 da verified-dead + dich chua ton tai)
    start      kiem tien dieu kien (gio, tep, hash approval/profile/nguon), ghi xac nhan lifecycle vao dossier Ph1, khoi dong supervisor (publisher) + handshake co han
    evidence   doc tep live: READ ONLY verified, zero-active, tuoi hop le
    ph1        cho >= lead 90 s ke tu luc publisher khoi dong, `--check-only` (phai ok) roi DUNG MOT lan lenh Ph1 da duyet
    stop       tao stop-file, doi supervisor xac minh publisher chet
    collect    SHA moi artifact dung cho dossier
    status     doc nhanh nhat ky supervisor
"""
from __future__ import annotations

import datetime as dt
import hashlib
import json
import os
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(r"D:\MSE\CAPSTONE")
PROJECT = ROOT / "hotel-price-intelligence"
RUN = ROOT / "outputs" / "repair-run-20261009"
DISCUSS = ROOT / "discuss" / "model-results-improvement-20261008"
APPROVAL = DISCUSS / "90a-gpt-ph1-outer-approval.md"
RENEWAL_APPROVAL = DISCUSS / "90b-gpt-ph1-evidence-renewal-approval.md"
PROFILE = RUN / "renewal_profile_ph1_r83.json"
PY_VENV = str(PROJECT / "eda" / ".venv" / "Scripts" / "python.exe")
PY_SUP = r"C:\Users\ADMIN\anaconda3\python.exe"
SUPERVISOR = PROJECT / "audit_tools" / "renewal_supervisor_v1.py"
ENTRY = PROJECT / "audit_tools" / "repair_run_v1.py"
PLAN = ROOT / "outputs" / "repair-proposal-20261009-r83" / "ph1.plan.json"
PLAN_DIR, LOGS, LIVE = RUN / "ph1" / "plan", RUN / "ph1" / "logs", RUN / "live_crawl_idle_evidence.json"
SUP_LOG, PUB_LOG, STOP = RUN / "ph1_supervisor.jsonl", RUN / "ph1_publisher.log", RUN / "ph1_STOP"
OP_LOG, PREFLIGHT = RUN / "ph1_operator_log.jsonl", RUN / "ph1_dossier_preflight.md"
ARCHIVE_DIR = RUN / "evidence_archive" / "ph1"
ENTRY_STDOUT, ENTRY_STDERR = RUN / "ph1_entry_stdout.json", RUN / "ph1_entry_stderr.txt"
Q0_SUP_LOG = RUN / "q0_supervisor.jsonl"                                                                  # chi DOC: bang chung Q0 da verified-dead truoc khi di chuyen live Q0
Q0_LIVE_SHA = "d54b6ca1417475fa62e015f428883c516fd0ada372270dc52052616bd2b9336c"
Q0_LIVE_ARCHIVE_TARGET = RUN / "q0" / f"live_final_before_ph1_{Q0_LIVE_SHA}.json"
SUP_STDOUT = RUN / "ph1_supervisor_stdout.log"                                                              # 86: tao NGUYEN TU truoc Popen => dau hieu "da launch supervisor"
SHA = {"approval_90a": "7d4c007e65a814c8438d42f7aa3e685ba0e90fe244a93458434450e89a46d3b7", "approval_90b": "4131145de37a9dfc1d3a1c7adcd8578b388220779685551ed053d1ded9f369d1",
       "profile": "a5a0f1e0bf6f64101884d2b1d726b26f0f871b37893e1841edb211554f917f51", "source": "6429482e6deddf711a8228726ec4776d7450bb82b526194299c9eae6c82ed4d9",
       "interpreter": "0e818a1f9a0b8fbd4e7cc458a07cb7de2ea02ea326e387699a33b92f151242cd", "parent_interpreter": "7679e53fa969789309e81fdad0d52b8cda5f83c9abf7cb31a3c58bf24b31e264",
       "supervisor": "1fe241c64871f64b755e5bc6f0ac2bb689d4ab4d6720e2e27cd634ef1ab38d42",
       "plan_file": "0dbfc554706c9a60a7f7a926f02e4659fcd0f2c22e9c294b6e3e693989ebe24a", "plan_canonical": "ba951b990f5df1db8d10a04f3174f7e5c28cb2bffb8cf9a06cc4c68f3eb0d81b",
       "invocation": "e3ea7a635ae6d3dae7fb7b4af0d939aa8d70b687dca39f470645bf616c180be4",
       "entry": "bc93c167f7f64544818cb7b4ce54e11cca6e39f77dd400987df038e586182fb0", "runner": "d3b4b595bd7c52a67af147f2ef25c614627fc9e57fdf5815791ffc463951aa8d"}
NOT_BEFORE, START_DEADLINE, HARD_NOT_AFTER = "2026-10-09T11:00:00Z", "2026-10-09T12:00:00Z", "2026-10-09T13:00:00Z"       # 90b: 18:00<=VN<19:00; het hieu luc tuyet doi 20:00 VN (13:00Z)
PLAN_TOTAL_S, INVOKE_TIMEOUT_S, MIN_REMAINING_S = 3000, 3150, 3120                                           # entry plan 3000 s; wrapper timeout < publisher cap 3300; can >= plan + 120 s tai luc goi
ENV6 = {"SystemRoot": r"C:\Windows", "TEMP": r"C:\Windows\Temp", "TMP": r"C:\Windows\Temp", "PATH": r"C:\Windows\System32;C:\Windows", "PYTHONUTF8": "1", "PYTHONIOENCODING": "utf-8"}
APPROVED_ARGV = [PY_VENV, str(ENTRY), "--approval", str(APPROVAL), "--plan", str(PLAN), "--plan-dir", str(PLAN_DIR), "--launcher-logs", str(LOGS), "--crawl-evidence", str(LIVE)]
LEAD_S = 90


def now() -> dt.datetime:
    return dt.datetime.now(dt.timezone.utc)


def stamp(t: dt.datetime | None = None) -> str:
    return (t or now()).strftime("%Y-%m-%dT%H:%M:%SZ")


def sha(path: Path) -> str:
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def log(kind: str, **fields) -> None:
    line = json.dumps({"at_utc": stamp(), "event": kind, **fields}, ensure_ascii=False, sort_keys=True, default=str)
    with open(OP_LOG, "ab") as handle:
        handle.write((line + "\n").encode("utf-8"))
    print(line, flush=True)


def events(path: Path) -> list[dict]:
    return [json.loads(x) for x in Path(path).read_text(encoding="utf-8").splitlines()] if Path(path).exists() else []


def safe_log(kind: str, **fields) -> None:
    """86: log CHAN DOAN trong luc don dep — loi ghi nhat ky (dia day/khoa tep) KHONG duoc lam bo qua STOP/cleanup."""
    try:
        log(kind, **fields)
    except Exception:                                                                                           # noqa: BLE001
        pass


def request_stop() -> None:
    """86: STOP di TRUOC moi log chan doan; neu khong ghi duoc thi cmd_stop se het han va tra nonzero (khong gia vo da dung)."""
    try:
        if not STOP.exists():
            STOP.write_text("stop", encoding="utf-8")
    except OSError:
        pass


def publisher_state() -> str:
    """not_launched | pending (da launch supervisor nhung chua thay started/refused/stopped) | running | stopped | refused.
    Chi `refused` hoac `not_launched` moi la never-started; VANG su kien KHONG phai bang chung chua tung launch (86)."""
    names = [e["event"] for e in events(SUP_LOG)]
    if "stopped" in names:
        return "stopped"
    if "started" in names:
        return "running"
    if "refused" in names:
        return "refused"
    return "pending" if SUP_STDOUT.exists() else "not_launched"


def await_handshake(timeout_s: float) -> str:
    deadline = time.time() + timeout_s
    state = publisher_state()
    while state == "pending" and time.time() < deadline:
        time.sleep(1)
        state = publisher_state()
    return state


def preconditions() -> list[str]:
    problems = []
    t = now()
    if not (dt.datetime.strptime(NOT_BEFORE, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=dt.timezone.utc) <= t < dt.datetime.strptime(START_DEADLINE, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=dt.timezone.utc)):
        problems.append(f"outside_start_window: {stamp(t)}")
    for name, path, expected in (("profile", PROFILE, SHA["profile"]), ("source", PROJECT / "audit_tools" / "crawl_idle_evidence.py", SHA["source"]), ("interpreter", Path(PY_VENV), SHA["interpreter"]),
                                 ("parent_interpreter", Path(PY_SUP), SHA["parent_interpreter"]), ("supervisor", SUPERVISOR, SHA["supervisor"]), ("approval_90a", APPROVAL, SHA["approval_90a"]),
                                 ("approval_90b", RENEWAL_APPROVAL, SHA["approval_90b"]),
                                 ("plan_file", PLAN, SHA["plan_file"]), ("entry", ENTRY, SHA["entry"]), ("runner", PROJECT / "audit_tools" / "repair_phase_runner_v2.py", SHA["runner"])):
        if sha(path) != expected:
            problems.append(f"sha_mismatch: {name}")
    for path in (LIVE, STOP, PUB_LOG, SUP_LOG, SUP_STDOUT, LOGS, ARCHIVE_DIR, PREFLIGHT, ENTRY_STDOUT, ENTRY_STDERR):
        if path.exists():
            problems.append(f"already_exists: {path.name}")
    if not PLAN_DIR.is_dir() or any(PLAN_DIR.iterdir()):
        problems.append("plan_dir_not_empty")
    if any(key.upper().startswith("DB_") for key in os.environ):
        problems.append("shell_inherits_DB_vars")
    alive = crawler_processes()
    if alive:
        problems.append(f"crawler_process_alive: {alive}")                                                    # KHONG khoi dong publisher mot-lan khi crawler hang ngay con chay (se phi quyen)
    return problems


def crawler_processes() -> list[int]:
    """PID cac process `run_scheduled_calendar` (crawler hang ngay) — chi doc danh sach process cua OS, khong cham DB."""
    done = subprocess.run([r"C:\Windows\System32\WindowsPowerShell\v1.0\powershell.exe", "-NoProfile", "-NonInteractive", "-Command",
                           "Get-CimInstance Win32_Process | Where-Object { $_.Name -match '^python' -and $_.CommandLine -match 'run_scheduled_calendar' } | ForEach-Object { $_.ProcessId }"],
                          capture_output=True, text=True, timeout=60, env=ENV6, creationflags=0x08000000)
    return [int(x) for x in done.stdout.split() if x.strip().isdigit()]


def cmd_start() -> int:
    problems = preconditions()
    if problems:
        log("start_refused", problems=problems)
        return 2
    t = now()
    text = f"""# Ph1 dossier — xác nhận lifecycle trước khi khởi động ({stamp(t)})

Tôi (Claude) xác nhận các điều kiện 90a/90b (+ ruling thực thi của GPT) và exact invocation TRƯỚC khi khởi động publisher:

- Chỉ MỘT publisher phục vụ Ph1 canonical `{SHA['plan_canonical']}`; bắt đầu 18:00 ≤ VN < 19:00 ngày 09/10/2026 (11:00Z ≤ t < 12:00Z); hết hiệu lực tuyệt đối 20:00 VN (13:00Z); không Q0/heavy/Ph1 phụ; không auto-restart/extend/đổi cadence.
- argv đúng 90b/profile (N=3300, interval 60, archive `evidence_archive/ph1`); cwd `backend`; môi trường Clear + đúng 6 biến (Popen `env=` tường minh, `shell=False`, `CREATE_NO_WINDOW`); stdout/stderr ghi thẳng vào `ph1_publisher.log`.
- Kiểm `DB_POOL_SIZE` qua Settings bằng child hermetic TRƯỚC khi import tạo pool; chỉ ghi số nguyên; ≤5 mới tiếp tục.
- PID/creation-time/argv/profile SHA/source SHA/wall-clock start ghi vào `ph1_supervisor.jsonl`; không tìm/kill theo tên/glob.
- Hard wall deadline = min(start+3300 s, 13:00Z); dừng đúng process của mình (kill + wait 5 s + verified-dead) khi Ph1 xong/refused/fail/cancel hoặc publisher busy/lỗi/stale. Ph1 chỉ được gọi khi còn ≥ {MIN_REMAINING_S} s trước hard deadline.
- Publish đầu phải fresh + READ ONLY + zero-active (đo lại thật ở Ph1; KHÔNG dùng snapshot Q0); chờ lead 90 s; busy ⇒ KHÔNG chạy Ph1.
- Exact Ph1 invocation (90a, một lần, không auto-retry, không `--q0-receipt`): `{subprocess.list2cmdline(APPROVED_ARGV)}`
- Hash: 90a `{sha(APPROVAL)}`; 90b `{sha(RENEWAL_APPROVAL)}`; profile `{SHA['profile']}`; nguồn `{SHA['source']}`; interpreter `{SHA['interpreter']}`; parent `{SHA['parent_interpreter']}`; supervisor `{SHA['supervisor']}`; plan-file `{SHA['plan_file']}`; invocation `{SHA['invocation']}`; entry `{SHA['entry']}`; runner `{SHA['runner']}`.
"""
    PREFLIGHT.write_text(text, encoding="utf-8")
    command = [PY_SUP, str(SUPERVISOR), "--profile", str(PROFILE), "--expected-profile-sha256", SHA["profile"], "--expected-source-sha256", SHA["source"], "--expected-interpreter-sha256", SHA["interpreter"],
               "--not-before-utc", NOT_BEFORE, "--start-deadline-utc", START_DEADLINE, "--hard-not-after-utc", HARD_NOT_AFTER, "--max-pool-size", "5", "--supervisor-log", str(SUP_LOG),
               "--publisher-log", str(PUB_LOG), "--stop-file", str(STOP)]
    sup = subprocess.Popen(command, cwd=str(ROOT), env=ENV6, stdin=subprocess.DEVNULL, stdout=open(SUP_STDOUT, "xb"), stderr=subprocess.STDOUT, creationflags=0x08000000)
    log("supervisor_launched", supervisor_pid=sup.pid, command=command, preflight_sha256=sha(PREFLIGHT))
    return start_handshake(sup)


def start_handshake(sup, timeout_s: float = 60.0) -> int:
    """86: sau launch, cho CO HAN su kien started/refused (hoac supervisor da thoat) truoc khi tra start thanh cong. Timeout/cancel/loi luc con pending => STOP + xac nhan ket thuc co han (khong gia verified-dead)."""
    try:
        deadline = time.time() + timeout_s
        state = publisher_state()
        while state == "pending" and time.time() < deadline and sup.poll() is None:
            time.sleep(1)
            state = publisher_state()
        if state == "pending":
            if sup.poll() is None:
                raise TimeoutError("handshake_timeout")
            state = publisher_state()                                                                            # supervisor DA thoat: doc lai lan cuoi (tranh dua)
            if state == "pending":
                safe_log("handshake_supervisor_exited_without_event", exit_code=sup.poll())
                return 2                                                                                         # da thoat truoc spawn; finally cua supervisor tu don neu co child
    except BaseException as exc:                                                                                 # noqa: BLE001  (gom ca KeyboardInterrupt)
        safe_log("handshake_incomplete", error=type(exc).__name__)
        stop_rc = ensure_stopped("handshake_incomplete")
        if isinstance(exc, (KeyboardInterrupt, SystemExit)):
            raise
        return 3 if stop_rc else 2
    if state == "running":
        safe_log("handshake_started")
        return 0
    safe_log("handshake_not_running", publisher_state=state)
    return 2 if state == "refused" else 3


def evidence_state() -> dict:
    if not LIVE.exists():
        return {"present": False}
    doc = json.loads(LIVE.read_text(encoding="utf-8"))
    observed = dt.datetime.strptime(doc["observed_at_utc"], "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=dt.timezone.utc)
    return {"present": True, "age_s": (now() - observed).total_seconds(), "active_runs": doc.get("active_runs"), "active_items": doc.get("active_items"), "read_only_verified": doc["session"]["read_only_verified"],
            "latest_run_id": doc.get("latest_run_id"), "latest_run_status": doc.get("latest_run_status"), "sha256": sha(LIVE), "generator_sha256": doc.get("generator_sha256")}


def ensure_stopped(reason: str) -> int:
    """84: sau publisher-start MOI duong ket thuc (evidence busy/stale/loi; Q0 xong/refused/fail/timeout/cancel) phai yeu cau dung + xac minh chet co han, khong phu thuoc viec nho chay `stop`.
    Chua co `started` => khong co gi de dung va KHONG nguy tao verified-dead."""
    state = publisher_state()
    if state in ("not_launched", "refused"):
        safe_log("stop_not_needed", reason=reason, detail=f"publisher_never_started:{state}")
        return 0
    request_stop()                                                                                              # 86: STOP truoc moi log chan doan
    safe_log("stop_required_by_terminal_path", reason=reason, publisher_state=state)
    return cmd_stop()


def cmd_evidence() -> int:
    ok = False
    try:
        pub = await_handshake(60)                                                                               # 86: chua thay `started` != chua launch (pool-check co the dang chay): cho handshake co han
        if pub == "pending":
            safe_log("handshake_timeout_in_evidence")
            return 3 if ensure_stopped("handshake_timeout") else 2
        if pub in ("not_launched", "refused", "stopped"):
            safe_log("evidence_not_applicable", publisher_state=pub)
            return 3 if ensure_stopped(f"publisher_{pub}") else 1
        deadline = time.time() + 60                                                                             # file dau tien co the chua kip xuat hien ngay sau start: cho toi da 60 s truoc khi coi la loi
        state = evidence_state()
        while not state.get("present") and time.time() < deadline and publisher_state() == "running":
            time.sleep(2)
            state = evidence_state()
        ok = state.get("present") and state["read_only_verified"] is True and state["active_runs"] == 0 and state["active_items"] == 0 and state["age_s"] <= 90 and state["generator_sha256"] == SHA["source"]
        log("evidence", ok=bool(ok), **state)
    except BaseException as exc:                                                                                 # noqa: BLE001  (gom ca KeyboardInterrupt: van phai dung publisher)
        safe_log("evidence_error", error=type(exc).__name__)                                                     # 86: log hong khong duoc bo qua cleanup
        stop_rc = ensure_stopped("evidence_error")
        if isinstance(exc, (KeyboardInterrupt, SystemExit)):
            raise
        return 3 if stop_rc else 1
    if ok:
        return 0
    stop_rc = ensure_stopped("evidence_not_clean_busy_or_stale")                                                # busy/stale/READ ONLY sai/generator sai => publish ket thuc, KHONG chay Ph1
    return 3 if stop_rc else 1


def cmd_ph1() -> int:
    """84: boc than Ph1 trong try/finally — du Ph1 success/refused/fail/timeout/exception/cancel, publisher do wrapper tao deu bi yeu cau dung va xac minh."""
    rc: int | None = None
    try:
        rc = _ph1_body()
        return rc
    finally:
        stop_rc = ensure_stopped("ph1_terminal" if rc is not None else "ph1_exception_or_cancel")
        if stop_rc and rc is not None:
            safe_log("ph1_cleanup_not_verified", stop_exit_code=stop_rc, q0_exit_code=rc)
            sys.exit(3)                                                                                          # cleanup khong xac minh => nonzero ro rang


def _ph1_body() -> int:
    started = next((e for e in events(SUP_LOG) if e["event"] == "started"), None)
    if started is None:
        log("ph1_refused", reason="publisher_not_started")
        return 2
    start_t = dt.datetime.strptime(started["at_utc"], "%Y-%m-%dT%H:%M:%S.%fZ").replace(tzinfo=dt.timezone.utc)
    wait = LEAD_S - (now() - start_t).total_seconds()
    if wait > 0:
        log("waiting_lead", seconds=round(wait, 1))
        time.sleep(wait)
    if any(e["event"] == "stopped" for e in events(SUP_LOG)):
        log("ph1_refused", reason="publisher_already_stopped")
        return 2
    state = evidence_state()
    if not (state.get("present") and state["read_only_verified"] is True and state["active_runs"] == 0 and state["active_items"] == 0 and state["age_s"] <= 100 and state["generator_sha256"] == SHA["source"]):
        log("ph1_refused", reason="evidence_not_clean_or_stale", **state)
        return 2
    if now() >= dt.datetime.strptime(START_DEADLINE, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=dt.timezone.utc):
        log("ph1_refused", reason="start_deadline_passed")
        return 2
    wall = dt.datetime.strptime(started["wall_deadline_utc"], "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=dt.timezone.utc)
    remaining = (wall - now()).total_seconds()
    if remaining < MIN_REMAINING_S:                                                                             # khong bat dau sat deadline: plan 3000 s + margin phai nam trong hard cap cua publisher
        log("ph1_refused", reason="insufficient_publisher_time", remaining_s=round(remaining, 1), required_s=MIN_REMAINING_S)
        return 2
    check = subprocess.run([*APPROVED_ARGV, "--check-only"], cwd=str(ROOT), env=ENV6, capture_output=True, text=True, timeout=120, creationflags=0x08000000)
    log("check_only", exit_code=check.returncode, stdout=check.stdout.strip()[:2000], stderr=check.stderr.strip()[:500])
    try:
        ok = json.loads(check.stdout.strip().splitlines()[-1]).get("ok") is True
    except (ValueError, IndexError):
        ok = False
    if check.returncode != 0 or not ok:
        log("ph1_refused", reason="check_only_not_ok")
        return 2
    run = subprocess.run(APPROVED_ARGV, cwd=str(ROOT), env=ENV6, capture_output=True, text=True, timeout=INVOKE_TIMEOUT_S, creationflags=0x08000000)         # DUNG MOT LAN, khong retry
    stdout_bytes, stderr_bytes = run.stdout.encode("utf-8"), run.stderr.encode("utf-8")
    ENTRY_STDOUT.write_bytes(stdout_bytes)                                                                      # byte-exact: SHA tep == SHA ghi trong log (Q0 bi CRLF vi write_text)
    ENTRY_STDERR.write_bytes(stderr_bytes)
    log("ph1_invoked", exit_code=run.returncode, stdout_sha256=hashlib.sha256(stdout_bytes).hexdigest(), stderr_sha256=hashlib.sha256(stderr_bytes).hexdigest(), stderr_bytes=len(stderr_bytes))
    print(run.stdout[-3000:])
    return run.returncode


def archive_q0_live() -> int:
    """90b: MOT archival move khong ghi de cua live Q0 sang dich da bind. REFUSE (khong glob/xoa/reset) neu hash lech, dich ton tai, Q0 chua verified-dead, hoac duong dan ra ngoai repair-run root."""
    source, target, root = LIVE, Q0_LIVE_ARCHIVE_TARGET, RUN.resolve()
    reasons = []
    try:
        if not source.is_file() or sha(source) != Q0_LIVE_SHA:
            reasons.append("source_missing_or_hash_drift")
        if target.exists():
            reasons.append("target_exists")
        for path in (source, target.parent):
            if root not in Path(path).resolve().parents and Path(path).resolve() != root:
                reasons.append("path_outside_repair_run_root")
        if Path(target).resolve().parent != (RUN / "q0").resolve():
            reasons.append("target_not_in_q0_dir")
        q0_stopped = [e for e in events(Q0_SUP_LOG) if e["event"] == "stopped"]
        if not q0_stopped or q0_stopped[-1].get("verified_dead") is not True:
            reasons.append("q0_cleanup_not_verified")
    except OSError as exc:
        reasons.append(f"os_error:{type(exc).__name__}")
    if reasons:
        safe_log("archive_q0_live_refused", reasons=reasons)
        return 2
    before = sha(source)
    os.rename(source, target)                                                                                    # cung o dia, khong ghi de (rename that bai neu dich ton tai)
    after = sha(target)
    ok = before == after == Q0_LIVE_SHA and not source.exists()
    log("archive_q0_live", ok=ok, source=str(source), target=str(target), sha256_before=before, sha256_after=after, at_utc=stamp())
    return 0 if ok else 3


def cmd_stop() -> int:
    request_stop()
    deadline = time.time() + 40
    while time.time() < deadline:
        stop_events = events(SUP_LOG)
        stopped = [e for e in stop_events if e["event"] == "stopped"]
        if not stopped and not any(e["event"] == "started" for e in stop_events) and any(e["event"] == "refused" for e in stop_events):
            safe_log("publisher_never_started_confirmed_refused")                                               # 86: STOP den luc supervisor con pending => no tu choi, xac nhan khong co process
            return 0
        if stopped:
            safe_log("publisher_stopped", **{k: v for k, v in stopped[-1].items() if k not in ("at_utc", "event")})                  # 84: khoa `event` cua supervisor ghi de loai su kien cua operator ("publisher_stopped" bi ghi thanh "stopped")
            return 0 if stopped[-1].get("verified_dead") else 3
        time.sleep(1)
    safe_log("publisher_stop_timeout")
    return 3


def cmd_collect() -> int:
    files = {}
    for path in sorted(RUN.rglob("*")):
        if path.is_file() and "evidence_archive" not in path.parts or (path.is_file() and path.suffix == ".json" and "evidence_archive" in path.parts):
            files[str(path.relative_to(RUN))] = sha(path)
    log("collected", files=files)
    return 0


def cmd_status() -> int:
    print(json.dumps({"supervisor": events(SUP_LOG)[-3:], "evidence": evidence_state(), "time_utc": stamp()}, ensure_ascii=False, default=str))
    return 0


if __name__ == "__main__":
    commands = {"archive_q0_live": archive_q0_live, "start": cmd_start, "evidence": cmd_evidence, "ph1": cmd_ph1, "stop": cmd_stop, "collect": cmd_collect, "status": cmd_status}
    sys.exit(commands[sys.argv[1]]())

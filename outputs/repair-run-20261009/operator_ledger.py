"""Operator LEDGER-only (Claude, uy quyen cua nguoi dung 10/10/2026; GPT chua duyet): ban sao co chu dich cua operator_ph1.py (da duoc GPT duyet 84-92) voi hang so cho ledger DOC-CHI cua hai dataset MOI
`ds_20261010_nf1/nf3` tren DB `warehouse_dsdev_20261009_3src_nf`. KHONG chay qua runner v2/entry `repair_run_v1` (khong plan/PLAN_BOUND/receipt): producer `warehouse_flag_ledger_v1.py` tu giu cac cong
(duyet gan SHA, evidence crawler-idle <=120 s moi lenh, READ ONLY, EXPLAIN truoc SELECT, ngan sach/tran lenh). Moi buoc in MOT dong JSON va ghi vao ld_operator_log.jsonl. Khong retry.

    start      kiem tien dieu kien (gio, tep, hash), ghi dossier lifecycle, khoi dong supervisor (publisher evidence) + handshake co han
    evidence   doc tep live: READ ONLY verified, zero-active, tuoi hop le
    ledger     cho >= lead 90 s, roi chay TUAN TU hai producer (nf1 roi nf3), moi cai DUNG MOT lan; producer dau khong LEDGER_PASS => khong chay producer sau
    stop       tao stop-file, doi supervisor xac minh publisher chet
    collect    SHA moi artifact dung cho dossier
    status     doc nhanh nhat ky supervisor
"""
from __future__ import annotations

import datetime as dt
import hashlib
import json
import os
import re
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(r"D:\MSE\CAPSTONE")
PROJECT = ROOT / "hotel-price-intelligence"
TOOLS = ROOT / "outputs" / "repair-run-20261009"
RUN = ROOT / "outputs" / "repair-run-ledger-r124"
DISCUSS = ROOT / "discuss" / "model-results-improvement-20261008"
APPROVAL = DISCUSS / "141f-gpt-USER-DELEGATED-claude-authored-ledger-producer-approval.md"
PROFILE = RUN / "renewal_profile_ledger.json"
PY_VENV = str(PROJECT / "eda" / ".venv" / "Scripts" / "python.exe")
PY_SUP = r"C:\Users\ADMIN\anaconda3\python.exe"
AUDIT = PROJECT / "audit_tools"
SUPERVISOR = AUDIT / "renewal_supervisor_v1.py"
PRODUCER = AUDIT / "warehouse_flag_ledger_v1.py"
BACKEND = PROJECT / "backend"
DATASETS_DIR = ROOT / "outputs" / "datasets"
LOGS = RUN / "ld_logs"
LIVE = RUN / "live_crawl_idle_evidence.json"
SUP_LOG, PUB_LOG, STOP = RUN / "ld_supervisor.jsonl", RUN / "ld_publisher.log", RUN / "ld_STOP"
OP_LOG, PREFLIGHT = RUN / "ld_operator_log.jsonl", RUN / "ld_dossier_preflight.md"
ARCHIVE_DIR = RUN / "evidence_archive" / "ledger"
SUP_STDOUT = RUN / "ld_supervisor_stdout.log"
DB = "warehouse_dsdev_20261009_3src_nf"
VERSIONS = ("ds_20261010_nf1", "ds_20261010_nf3")
EXPECTED_ROWS = "146431"
SHA = {"approval": "b912d98b3ed17805822d79f713aa8325a2b2de2768f07b774678d7a6f092ece7", "profile": "4b4cdd167b5ea8710051afd17381e2a35db3867141abac88fa61e28096392fbf",
       "source": "6429482e6deddf711a8228726ec4776d7450bb82b526194299c9eae6c82ed4d9", "interpreter": "0e818a1f9a0b8fbd4e7cc458a07cb7de2ea02ea326e387699a33b92f151242cd",
       "parent_interpreter": "7679e53fa969789309e81fdad0d52b8cda5f83c9abf7cb31a3c58bf24b31e264", "supervisor": "1fe241c64871f64b755e5bc6f0ac2bb689d4ab4d6720e2e27cd634ef1ab38d42",
       "producer": "a6968c0ff7a15bed7e91833933359bf2b2fa05f6cf9201539068add9b44ab67c", "controller": "491d3f4a3e220da15ed2b57eac07762351cd98c2cf95ab2058f492676cfcbbc0",
       "parity": "1022c5ee9f0227536413636d5bcbb46fc18a0d59849df54d53eb83e75cd83510"}
CONFIG_SHA = {"ds_20261010_nf1": "39d9eca0633fe84e1c422279bd4b4ba8e95da9f3776a8287e06c1c2e39597a91", "ds_20261010_nf3": "27a922dc264aa1099e13f3facd54a20ffcc7b4c727209db856971d74a3940b27"}
SAMPLES_SHA = {"ds_20261010_nf1": "74e3426042b87c0f7f84ee9eef122a4e69ed7737599da2bed14a5487fbdc86da", "ds_20261010_nf3": "b3647ead6245beb45ea09a50522aa274cea166e17933e00af1c89a0cfcbd57f7"}
NEW_CODE_SHA256 = "c576079a7ca0310a8f5bb0074f29a58a3e66c1765cf4197306229bfa777edbfe"
NOT_BEFORE, START_DEADLINE, HARD_NOT_AFTER = "2026-10-10T13:30:00Z", "2026-10-10T15:30:00Z", "2026-10-10T16:30:00Z"        # chi trong khung crawler-idle (17:00-00:20 VN = 10:00Z-17:20Z)
PRODUCER_TIMEOUT_S, MIN_REMAINING_S, LEAD_S = 700, 720, 90                                                       # producer budget 600 s; wrapper timeout < publisher cap 1800
ENV6 = {"SystemRoot": r"C:\Windows", "TEMP": r"C:\Windows\Temp", "TMP": r"C:\Windows\Temp", "PATH": r"C:\Windows\System32;C:\Windows", "PYTHONUTF8": "1", "PYTHONIOENCODING": "utf-8"}
PRODUCER_ENV = {"SystemRoot": r"C:\Windows", "TEMP": r"C:\Windows\Temp", "TMP": r"C:\Windows\Temp", "PATH": r"C:\Program Files\Git\cmd;C:\Windows\System32;C:\Windows", "PYTHONUTF8": "1", "PYTHONIOENCODING": "utf-8"}


def producer_argv(dataset: str) -> list[str]:
    return [PY_VENV, str(PRODUCER), "--db", DB, "--dataset", dataset, "--dataset-dir", str(DATASETS_DIR / dataset), "--expected-samples-sha256", SAMPLES_SHA[dataset], "--expected-rows", EXPECTED_ROWS,
            "--snapshot-versions", ",".join(VERSIONS), "--expected-old-code-sha256", NEW_CODE_SHA256,
            "--expected-config-sha256", f"{VERSIONS[0]}={CONFIG_SHA[VERSIONS[0]]}", "--expected-config-sha256", f"{VERSIONS[1]}={CONFIG_SHA[VERSIONS[1]]}",
            "--run", "--gpt-approval", str(APPROVAL), "--crawl-evidence", str(LIVE), "--budget-seconds", "600", "--max-queries", "200", "--max-batches", "160"]


def out_paths(dataset: str) -> tuple[Path, Path]:
    return RUN / f"ld_producer_{dataset}_stdout.txt", RUN / f"ld_producer_{dataset}_stderr.txt"


def now() -> dt.datetime:
    return dt.datetime.now(dt.timezone.utc)


def stamp(t: dt.datetime | None = None) -> str:
    return (t or now()).strftime("%Y-%m-%dT%H:%M:%SZ")


def sha(path) -> str:
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def log(kind: str, **fields) -> None:
    line = json.dumps({"at_utc": stamp(), "event": kind, **fields}, ensure_ascii=False, sort_keys=True, default=str)
    with open(OP_LOG, "ab") as handle:
        handle.write((line + "\n").encode("utf-8"))
    print(line, flush=True)


def events(path: Path) -> list[dict]:
    return [json.loads(x) for x in Path(path).read_text(encoding="utf-8").splitlines()] if Path(path).exists() else []


def safe_log(kind: str, **fields) -> None:
    try:
        log(kind, **fields)
    except Exception:                                                                                           # noqa: BLE001
        pass


def request_stop() -> None:
    try:
        if not STOP.exists():
            STOP.write_text("stop", encoding="utf-8")
    except OSError:
        pass


def publisher_state() -> str:
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


def crawler_processes() -> list[int]:
    done = subprocess.run([r"C:\Windows\System32\WindowsPowerShell\v1.0\powershell.exe", "-NoProfile", "-NonInteractive", "-Command",
                           "Get-CimInstance Win32_Process | Where-Object { $_.Name -match '^python' -and $_.CommandLine -match 'run_scheduled_calendar' } | ForEach-Object { $_.ProcessId }"],
                          capture_output=True, text=True, timeout=60, env=ENV6, creationflags=0x08000000)
    if done.returncode != 0 or done.stderr.strip():
        raise RuntimeError(f"crawler_probe_failed rc={done.returncode}")                                       # fail-closed: probe loi => khong coi la 'khong co crawler'
    return [int(x) for x in done.stdout.split() if x.strip().isdigit()]


def preconditions() -> list[str]:
    problems = []
    t = now()
    if not (dt.datetime.strptime(NOT_BEFORE, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=dt.timezone.utc) <= t < dt.datetime.strptime(START_DEADLINE, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=dt.timezone.utc)):
        problems.append(f"outside_start_window: {stamp(t)}")
    for name, path, expected in (("profile", PROFILE, SHA["profile"]), ("source", AUDIT / "crawl_idle_evidence.py", SHA["source"]), ("interpreter", Path(PY_VENV), SHA["interpreter"]),
                                 ("parent_interpreter", Path(PY_SUP), SHA["parent_interpreter"]), ("supervisor", SUPERVISOR, SHA["supervisor"]), ("approval", APPROVAL, SHA["approval"]),
                                 ("producer", PRODUCER, SHA["producer"]), ("controller", AUDIT / "warehouse_audit_v3.py", SHA["controller"]), ("parity", AUDIT / "repair_parity_v1.py", SHA["parity"]),
                                 ("samples_nf1", DATASETS_DIR / VERSIONS[0] / "samples.parquet", SAMPLES_SHA[VERSIONS[0]]), ("samples_nf3", DATASETS_DIR / VERSIONS[1] / "samples.parquet", SAMPLES_SHA[VERSIONS[1]])):
        try:
            if sha(path) != expected:
                problems.append(f"sha_mismatch: {name}")
        except OSError:
            problems.append(f"unreadable: {name}")
    paths = [LIVE, STOP, PUB_LOG, SUP_LOG, SUP_STDOUT, LOGS, PREFLIGHT, *(p for v in VERSIONS for p in out_paths(v))]
    for path in paths:
        if path.exists():
            problems.append(f"already_exists: {path.name}")
    if ARCHIVE_DIR.exists() and any(ARCHIVE_DIR.iterdir()):
        problems.append("archive_dir_not_empty")
    if any(key.upper().startswith("DB_") for key in os.environ):
        problems.append("shell_inherits_DB_vars")
    try:
        alive = crawler_processes()
        if alive:
            problems.append(f"crawler_process_alive: {alive}")
    except Exception as exc:                                                                                    # noqa: BLE001
        problems.append(f"crawler_probe_error: {type(exc).__name__}")
    return problems


def cmd_start() -> int:
    problems = preconditions()
    if problems:
        log("start_refused", problems=problems)
        return 2
    t = now()
    text = f"""# Ledger dossier — xác nhận lifecycle trước khi khởi động ({stamp(t)})

Claude (uỷ quyền trực tiếp của người dùng; GPT CHƯA duyệt) xác nhận trước khi khởi động publisher:

- Chỉ MỘT publisher phục vụ hai process producer đọc-chỉ tuần tự (`{VERSIONS[0]}` rồi `{VERSIONS[1]}`) trên `{DB}`; mỗi process đúng MỘT lần, không retry; không DDL/ghi DB; không SET GLOBAL.
- argv publisher đúng profile (N=1800, interval 60, archive `evidence_archive/ledger`); cwd `backend`; môi trường Clear + đúng 6 biến, `shell=False`, `CREATE_NO_WINDOW`; stdout/stderr ghi vào `ld_publisher.log`.
- Kiểm `DB_POOL_SIZE` ≤5 bằng child hermetic của supervisor TRƯỚC khi import tạo pool.
- Cửa sổ: {NOT_BEFORE} ≤ start < {START_DEADLINE}; hết hiệu lực tuyệt đối {HARD_NOT_AFTER}; chỉ khi crawler idle (OS probe fail-closed) và evidence mới READ ONLY zero-active.
- Producer `{SHA['producer']}` (controller `{SHA['controller']}`, parity `{SHA['parity']}`), approval `141f` `{SHA['approval']}`; P4 snapshot hai version, pin config nf1 `{CONFIG_SHA[VERSIONS[0]]}` / nf3 `{CONFIG_SHA[VERSIONS[1]]}`, code `{NEW_CODE_SHA256}`.
- Lệnh producer h1: `{subprocess.list2cmdline(producer_argv(VERSIONS[0]))}`
- Hash: profile `{SHA['profile']}`; nguồn `{SHA['source']}`; interpreter `{SHA['interpreter']}`; parent `{SHA['parent_interpreter']}`; supervisor `{SHA['supervisor']}`.
"""
    PREFLIGHT.write_text(text, encoding="utf-8")
    command = [PY_SUP, str(SUPERVISOR), "--profile", str(PROFILE), "--expected-profile-sha256", SHA["profile"], "--expected-source-sha256", SHA["source"], "--expected-interpreter-sha256", SHA["interpreter"],
               "--not-before-utc", NOT_BEFORE, "--start-deadline-utc", START_DEADLINE, "--hard-not-after-utc", HARD_NOT_AFTER, "--max-pool-size", "5", "--supervisor-log", str(SUP_LOG),
               "--publisher-log", str(PUB_LOG), "--stop-file", str(STOP)]
    sup = subprocess.Popen(command, cwd=str(ROOT), env=ENV6, stdin=subprocess.DEVNULL, stdout=open(SUP_STDOUT, "xb"), stderr=subprocess.STDOUT, creationflags=0x08000000)
    log("supervisor_launched", supervisor_pid=sup.pid, command=command, preflight_sha256=sha(PREFLIGHT))
    return start_handshake(sup)


def start_handshake(sup, timeout_s: float = 60.0) -> int:
    try:
        deadline = time.time() + timeout_s
        state = publisher_state()
        while state == "pending" and time.time() < deadline and sup.poll() is None:
            time.sleep(1)
            state = publisher_state()
        if state == "pending":
            if sup.poll() is None:
                raise TimeoutError("handshake_timeout")
            state = publisher_state()
            if state == "pending":
                safe_log("handshake_supervisor_exited_without_event", exit_code=sup.poll())
                return 2
    except BaseException as exc:                                                                                 # noqa: BLE001
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


def evidence_clean(state: dict, max_age: float) -> bool:
    return bool(state.get("present") and state["read_only_verified"] is True and state["active_runs"] == 0 and state["active_items"] == 0 and state["age_s"] <= max_age and state["generator_sha256"] == SHA["source"])


def ensure_stopped(reason: str) -> int:
    state = publisher_state()
    if state in ("not_launched", "refused"):
        safe_log("stop_not_needed", reason=reason, detail=f"publisher_never_started:{state}")
        return 0
    request_stop()
    safe_log("stop_required_by_terminal_path", reason=reason, publisher_state=state)
    return cmd_stop()


def cmd_evidence() -> int:
    ok = False
    try:
        pub = await_handshake(60)
        if pub == "pending":
            safe_log("handshake_timeout_in_evidence")
            return 3 if ensure_stopped("handshake_timeout") else 2
        if pub in ("not_launched", "refused", "stopped"):
            safe_log("evidence_not_applicable", publisher_state=pub)
            return 3 if ensure_stopped(f"publisher_{pub}") else 1
        deadline = time.time() + 60
        state = evidence_state()
        while not state.get("present") and time.time() < deadline and publisher_state() == "running":
            time.sleep(2)
            state = evidence_state()
        ok = evidence_clean(state, 90)
        log("evidence", ok=bool(ok), **state)
    except BaseException as exc:                                                                                 # noqa: BLE001
        safe_log("evidence_error", error=type(exc).__name__)
        stop_rc = ensure_stopped("evidence_error")
        if isinstance(exc, (KeyboardInterrupt, SystemExit)):
            raise
        return 3 if stop_rc else 1
    if ok:
        return 0
    stop_rc = ensure_stopped("evidence_not_clean_busy_or_stale")
    return 3 if stop_rc else 1


def cmd_ledger() -> int:
    rc: int | None = None
    try:
        rc = _ledger_body()
        return rc
    finally:
        stop_rc = ensure_stopped("ledger_terminal" if rc is not None else "ledger_exception_or_cancel")
        if stop_rc and rc is not None:
            safe_log("ledger_cleanup_not_verified", stop_exit_code=stop_rc, ledger_exit_code=rc)
            sys.exit(3)


def _run_one(dataset: str, wall: dt.datetime) -> int:
    state = evidence_state()
    if not evidence_clean(state, 100):
        log("ledger_refused", dataset=dataset, reason="evidence_not_clean_or_stale", **state)
        return 2
    remaining = (wall - now()).total_seconds()
    if remaining < MIN_REMAINING_S:
        log("ledger_refused", dataset=dataset, reason="insufficient_publisher_time", remaining_s=round(remaining, 1), required_s=MIN_REMAINING_S)
        return 2
    LOGS.mkdir(parents=True, exist_ok=True)
    out_path, err_path = out_paths(dataset)
    argv = producer_argv(dataset)
    started = time.time()
    try:
        run = subprocess.run(argv, cwd=str(BACKEND), env=PRODUCER_ENV, capture_output=True, text=True, timeout=PRODUCER_TIMEOUT_S, creationflags=0x08000000)         # DUNG MOT LAN, khong retry
    except subprocess.TimeoutExpired:
        log("producer_timeout", dataset=dataset, timeout_s=PRODUCER_TIMEOUT_S)
        return 3
    stdout_bytes, stderr_bytes = run.stdout.encode("utf-8"), run.stderr.encode("utf-8")
    out_path.write_bytes(stdout_bytes)                                                                          # byte-exact
    err_path.write_bytes(stderr_bytes)
    match = re.search(r"bao cao:\s*(.+)", run.stdout)
    report_path = match.group(1).strip() if match else None
    overall = re.search(r'"overall":\s*"([A-Z_]+)"', run.stdout)                                                 # stdout = JSON indent=1 (overall/exit_code/reasons) + dong `bao cao: <path>`
    summary: dict = {"overall": overall.group(1) if overall else None}
    reasons = re.search(r'"reasons":\s*(\[.*?\])\s*\n?\}', run.stdout, re.DOTALL)
    try:
        summary["reasons"] = json.loads(reasons.group(1)) if reasons else None
    except ValueError:
        summary["reasons"] = None
    log("producer_invoked", dataset=dataset, exit_code=run.returncode, elapsed_s=round(time.time() - started, 1), stdout_sha256=hashlib.sha256(stdout_bytes).hexdigest(), stderr_sha256=hashlib.sha256(stderr_bytes).hexdigest(),
        stderr_bytes=len(stderr_bytes), overall=summary.get("overall"), reasons=summary.get("reasons"), report_path=report_path, report_sha256=sha(report_path) if report_path and Path(report_path).exists() else None)
    print(run.stdout[-2000:])
    if run.returncode != 0 or summary.get("overall") != "LEDGER_PASS":
        return run.returncode or 1
    return 0


def _ledger_body() -> int:
    started = next((e for e in events(SUP_LOG) if e["event"] == "started"), None)
    if started is None:
        log("ledger_refused", reason="publisher_not_started")
        return 2
    start_t = dt.datetime.strptime(started["at_utc"], "%Y-%m-%dT%H:%M:%S.%fZ").replace(tzinfo=dt.timezone.utc)
    wait = LEAD_S - (now() - start_t).total_seconds()
    if wait > 0:
        log("waiting_lead", seconds=round(wait, 1))
        time.sleep(wait)
    if any(e["event"] == "stopped" for e in events(SUP_LOG)):
        log("ledger_refused", reason="publisher_already_stopped")
        return 2
    if now() >= dt.datetime.strptime(START_DEADLINE, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=dt.timezone.utc):
        log("ledger_refused", reason="start_deadline_passed")
        return 2
    wall = dt.datetime.strptime(started["wall_deadline_utc"], "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=dt.timezone.utc)
    for dataset in VERSIONS:                                                                                    # tuan tu: nf1 roi nf3; producer dau loi => khong chay producer sau
        rc = _run_one(dataset, wall)
        if rc != 0:
            return rc
    return 0


def cmd_stop() -> int:
    request_stop()
    deadline = time.time() + 40
    while time.time() < deadline:
        stop_events = events(SUP_LOG)
        stopped = [e for e in stop_events if e["event"] == "stopped"]
        if not stopped and not any(e["event"] == "started" for e in stop_events) and any(e["event"] == "refused" for e in stop_events):
            safe_log("publisher_never_started_confirmed_refused")
            return 0
        if stopped:
            safe_log("publisher_stopped", **{k: v for k, v in stopped[-1].items() if k not in ("at_utc", "event")})
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
    commands = {"start": cmd_start, "evidence": cmd_evidence, "ledger": cmd_ledger, "stop": cmd_stop, "collect": cmd_collect, "status": cmd_status}
    sys.exit(commands[sys.argv[1]]())

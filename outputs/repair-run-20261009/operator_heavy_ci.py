"""Operator HEAVY clone->init (phuong an C cua GPT 100): MOT publisher, HAI plan/approval ngoai rieng (clone roi init+verify), init CHI vao sau khi clone PASS that. Ban phat sinh co chu dich tu operator_ph1.py (da duoc 92 duyet)
voi: du lieu dong bang nam trong **freeze manifest** (SHA do GPT duyet), probe crawler THAT BAI => fail-closed (kiem returncode), chay tuan tu hai plan co gate, tao dung mot thu muc bao cao moi. KHONG co quyen thuc thi.

Moi buoc in MOT dong JSON va ghi vao ci_operator_log.jsonl (noi tiep). Khong co retry. Khong DROP/--drop-existing. Khong SET GLOBAL.
"""
from __future__ import annotations

import datetime as dt
import hashlib
import json
import os
import subprocess
import sys
import time
import types
from pathlib import Path
from typing import Any

ROOT = Path(r"D:\MSE\CAPSTONE")
PROJECT = ROOT / "hotel-price-intelligence"
RUN = ROOT / "outputs" / "repair-run-heavy-ci-r101"
PY_VENV = str(PROJECT / "eda" / ".venv" / "Scripts" / "python.exe")
PY_SUP = r"C:\Users\ADMIN\anaconda3\python.exe"
SUPERVISOR = PROJECT / "audit_tools" / "renewal_supervisor_v1.py"
ENTRY = PROJECT / "audit_tools" / "repair_run_v1.py"
LIVE = RUN / "live_crawl_idle_evidence.json"
SUP_LOG, PUB_LOG, STOP = RUN / "ci_supervisor.jsonl", RUN / "ci_publisher.log", RUN / "ci_STOP"
SUP_STDOUT = RUN / "ci_supervisor_stdout.log"
OP_LOG, PREFLIGHT = RUN / "ci_operator_log.jsonl", RUN / "ci_dossier_preflight.md"
ARCHIVE_DIR = RUN / "evidence_archive" / "ci"
H2_DIR = RUN / "h2"
H2_REPORTS = H2_DIR / "reports"
PLAN_ORDER = ("clone", "init")
PLAN_PHASES = {"clone": ("ph3_clone",), "init": ("ph4_init_h1", "ph4_verify_h1", "ph4_init_h3", "ph4_verify_h3")}
ENV6 = {"SystemRoot": r"C:\Windows", "TEMP": r"C:\Windows\Temp", "TMP": r"C:\Windows\Temp", "PATH": r"C:\Windows\System32;C:\Windows", "PYTHONUTF8": "1", "PYTHONIOENCODING": "utf-8"}
POWERSHELL = r"C:\Windows\System32\WindowsPowerShell\v1.0\powershell.exe"
LEAD_S = 90
Q0_MAX_AGE_S = 3600
Q0_AGE_MARGIN_S = 120                                                                                   # chi bat dau mot plan khi receipt con >= margin tuoi (entry van kiem tuoi that o moi phase ghi)
PLAN_TIMEOUT_MARGIN_S = 120
CHECK_ONLY_CAP_S = 120                                                                                  # timeout cua `--check-only`: moi plan co the ton toi da chung nay TRUOC invocation (102 H-TIME)
CLONE_FIRST_WRITE_MAX_AGE_S = 1200                                                                      # GPT 120: tuoi Q0 toi da NGAY TRUOC clone thuc te = 3600 - 1380 (clone budget 1260 + cap 120) - 120 (check-only init) - 780 (prefix init_h1+verify_h1) - 120 (margin) de phase ghi CUOI (ph4_init_h3) khong bi tu choi vi tuoi sau khi clone/init_h1 da ghi
CFG: dict[str, Any] = {}                                                                                # dien boi load_freeze(); rong => moi lenh tu choi


def now() -> dt.datetime:
    return dt.datetime.now(dt.timezone.utc)


def stamp(t: dt.datetime | None = None) -> str:
    return (t or now()).strftime("%Y-%m-%dT%H:%M:%SZ")


def parse_utc(text: str) -> dt.datetime:
    return dt.datetime.strptime(text, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=dt.timezone.utc)


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
    """Log CHAN DOAN trong luc don dep — loi ghi nhat ky KHONG duoc lam bo qua STOP/cleanup."""
    try:
        log(kind, **fields)
    except Exception:                                                                                   # noqa: BLE001
        pass


# ----------------------------------------------------------------------------- freeze manifest
def _abs_ok(text: Any) -> bool:
    return isinstance(text, str) and os.path.isabs(text) and "/" not in text


def _is_sha(text: Any) -> bool:
    return isinstance(text, str) and len(text) == 64 and all(c in "0123456789abcdef" for c in text)


FIXED_PIN_PATHS = {"source": PROJECT / "audit_tools" / "crawl_idle_evidence.py", "interpreter": Path(PY_VENV), "parent_interpreter": Path(PY_SUP), "supervisor": SUPERVISOR, "entry": ENTRY,
                   "runner": PROJECT / "audit_tools" / "repair_phase_runner_v2.py"}                      # freeze KHONG duoc tro sang tep khac cho cac thanh phan co dinh


def load_freeze(path: Path, expected_sha256: str) -> list[str]:
    """Nap freeze manifest da duoc GPT duyet (SHA ghim). Tra danh sach van de; CFG chi duoc dien khi KHONG co van de."""
    problems: list[str] = []
    CFG.clear()
    try:
        data = Path(path).read_bytes()
    except OSError:
        return ["freeze_unreadable"]
    if hashlib.sha256(data).hexdigest() != expected_sha256:
        return ["freeze_sha256_mismatch"]
    try:
        doc = json.loads(data.decode("utf-8"))
    except ValueError:
        return ["freeze_not_json"]
    if not (isinstance(doc, dict) and doc.get("kind") == "heavy_ci_freeze" and doc.get("run_root") == str(RUN)):
        return ["freeze_kind_or_run_root"]
    win = doc.get("windows") or {}
    try:
        windows = {k: parse_utc(win[k]) for k in ("not_before_utc", "start_deadline_utc", "hard_not_after_utc")}
    except (KeyError, ValueError, TypeError):
        return ["freeze_windows"]
    if not (windows["not_before_utc"] < windows["start_deadline_utc"] <= windows["hard_not_after_utc"]):
        problems.append("freeze_windows_order")
    q0 = doc.get("q0_receipt") or {}
    try:
        parse_utc(q0.get("created_at_utc"))
        q0_ok = _abs_ok(q0.get("path")) and _is_sha(q0.get("sha256"))
    except (TypeError, ValueError):
        q0_ok = False
    if not q0_ok:
        problems.append("freeze_q0_receipt")
    plans: dict[str, dict[str, Any]] = {}
    for key in PLAN_ORDER:
        p = (doc.get("plans") or {}).get(key) or {}
        need = ("plan_file", "plan_file_sha256", "plan_sha256", "approval", "approval_sha256", "invocation_sha256", "plan_dir", "launcher_logs", "total_budget_s")
        if any(k not in p for k in need) or not all(_abs_ok(p[k]) for k in ("plan_file", "approval", "plan_dir", "launcher_logs")) or not isinstance(p["total_budget_s"], (int, float)):
            problems.append(f"freeze_plan_{key}")
            continue
        if Path(p["plan_dir"]) != RUN / key / "plan" or Path(p["launcher_logs"]) != RUN / key / "logs":
            problems.append(f"freeze_plan_{key}_dirs_not_in_run_root")
        plans[key] = p
    profile, pins = doc.get("profile") or {}, doc.get("pins") or {}
    if not (_abs_ok(profile.get("path")) and _is_sha(profile.get("sha256")) and isinstance(profile.get("renew_seconds"), int)):
        problems.append("freeze_profile")
    if not isinstance(pins, dict):
        problems.append("freeze_pins")
    else:
        for name, fixed in FIXED_PIN_PATHS.items():
            pin = pins.get(name)
            if not (isinstance(pin, dict) and _is_sha(pin.get("sha256")) and pin.get("path") == str(fixed)):
                problems.append(f"freeze_pin_{name}")
    reports = doc.get("h2_reports") or []
    if not (isinstance(reports, list) and len(reports) == 2 and all(_abs_ok(x) and Path(x).parent == H2_REPORTS for x in reports)):
        problems.append("freeze_h2_reports")
    if problems:
        return problems
    CFG.update(doc=doc, windows=windows, q0=q0, plans=plans, profile=profile, pins=pins, h2_reports=[Path(x) for x in reports], freeze_sha256=expected_sha256)
    for key, p in plans.items():
        p["argv"] = [PY_VENV, str(ENTRY), "--approval", p["approval"], "--plan", p["plan_file"], "--plan-dir", p["plan_dir"], "--launcher-logs", p["launcher_logs"], "--crawl-evidence", str(LIVE),
                     "--q0-receipt", q0["path"]]
    return []


def approved_argv(key: str) -> list[str]:
    return list(CFG["plans"][key]["argv"])


# ----------------------------------------------------------------------------- probe crawler (fail-closed)
PS_COMMAND = "Get-CimInstance Win32_Process | Where-Object { $_.Name -match '^python' -and $_.CommandLine -match 'run_scheduled_calendar' } | ForEach-Object { $_.ProcessId }"


def crawler_probe() -> dict[str, Any]:
    """Inventory tien trinh crawler hang ngay qua OS (read-only). `success` CHI khi returncode 0, stderr rong, stdout chi gom so, khong timeout. Probe that bai KHONG duoc coi la idle."""
    out: dict[str, Any] = {"command": PS_COMMAND}
    try:
        done = subprocess.run([POWERSHELL, "-NoProfile", "-NonInteractive", "-Command", PS_COMMAND], capture_output=True, text=True, timeout=60, env=ENV6, creationflags=0x08000000)
    except subprocess.TimeoutExpired:
        return {**out, "returncode": None, "timed_out": True, "pids": [], "success": False}
    except OSError as exc:
        return {**out, "returncode": None, "timed_out": False, "pids": [], "success": False, "error": type(exc).__name__}
    tokens = done.stdout.split()
    pids = [int(x) for x in tokens if x.isdigit()]
    junk = [x for x in tokens if not x.isdigit()]
    return {**out, "returncode": done.returncode, "timed_out": False, "stderr": done.stderr.strip()[:300], "pids": pids, "success": done.returncode == 0 and not done.stderr.strip() and not junk}


# ----------------------------------------------------------------------------- trang thai publisher (sao tu operator_ph1, da duyet 84-92)
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


def preconditions() -> list[str]:
    problems: list[str] = []
    if not CFG:
        return ["freeze_not_loaded"]
    t = now()
    w = CFG["windows"]
    if not (w["not_before_utc"] <= t < w["start_deadline_utc"]):
        problems.append(f"outside_start_window: {stamp(t)}")
    pins: dict[str, Any] = {"profile": (CFG["profile"]["path"], CFG["profile"]["sha256"]), "q0_receipt": (CFG["q0"]["path"], CFG["q0"]["sha256"])}
    for name, expected in CFG["pins"].items():
        pins[name] = (expected["path"], expected["sha256"])
    for key, p in CFG["plans"].items():
        pins[f"{key}_plan_file"], pins[f"{key}_approval"] = (p["plan_file"], p["plan_file_sha256"]), (p["approval"], p["approval_sha256"])
    for name, pair in pins.items():
        try:
            if pair is None or sha(pair[0]) != pair[1]:
                problems.append(f"sha_mismatch: {name}")
        except OSError:
            problems.append(f"unreadable: {name}")
    q0_created = CFG["q0"].get("created_at_utc")
    try:
        age = (t - parse_utc(q0_created)).total_seconds()
        if age > Q0_MAX_AGE_S - Q0_AGE_MARGIN_S:
            problems.append(f"q0_receipt_too_old: {round(age)}s")
        if age > CLONE_FIRST_WRITE_MAX_AGE_S:                                                           # chan som (khong thay hai cong thuc te truoc clone): da qua gioi han thi publisher khoi dong cung vo ich
            problems.append(f"q0_receipt_too_old_for_the_last_ci_write_phase: {round(age)}s")
    except (TypeError, ValueError):
        problems.append("q0_created_at_unreadable")
    absent = [LIVE, STOP, PUB_LOG, SUP_LOG, SUP_STDOUT, PREFLIGHT, ARCHIVE_DIR, H2_DIR]
    for key, p in CFG["plans"].items():
        absent += [Path(p["launcher_logs"]), RUN / f"ci_{key}_entry_stdout.json", RUN / f"ci_{key}_entry_stderr.txt"]
    for path in absent:
        if path.exists():
            problems.append(f"already_exists: {path.name}")
    for key, p in CFG["plans"].items():
        plan_dir = Path(p["plan_dir"])
        if not plan_dir.is_dir() or any(plan_dir.iterdir()):
            problems.append(f"plan_dir_not_empty: {key}")
    if any(key.upper().startswith("DB_") for key in os.environ):
        problems.append("shell_inherits_DB_vars")
    probe = crawler_probe()
    if not probe["success"]:
        problems.append(f"crawler_probe_failed: rc={probe.get('returncode')} timed_out={probe.get('timed_out')}")
    elif probe["pids"]:
        problems.append(f"crawler_process_alive: {probe['pids']}")
    return problems


def cmd_start() -> int:
    problems = preconditions()
    if problems:
        log("start_refused", problems=problems)
        return 2
    t, w = now(), CFG["windows"]
    p = CFG["profile"]
    text = f"""# Heavy CI dossier — xác nhận lifecycle trước khi khởi động ({stamp(t)})

Tôi (Claude) xác nhận các điều kiện trong ruling thực thi của GPT và freeze manifest SHA `{CFG['freeze_sha256']}`:

- Chỉ MỘT publisher phục vụ HAI plan tuần tự (clone rồi init+verify); mỗi plan một approval ngoài riêng; không build, không plan phụ; không auto-restart/extend/đổi cadence.
- Cửa sổ: {stamp(w['not_before_utc'])} ≤ start < {stamp(w['start_deadline_utc'])}; hết hiệu lực tuyệt đối {stamp(w['hard_not_after_utc'])}; N={p['renew_seconds']} s.
- Env Clear + đúng 6 biến, `shell=False`, `CREATE_NO_WINDOW`; stdout/stderr ghi thẳng vào `ci_publisher.log`; kiểm `DB_POOL_SIZE` ≤5 bằng child hermetic TRƯỚC import tạo pool.
- Probe crawler OS là fail-closed (returncode/stderr/timeout đều phải thành công và PID rỗng).
- Clone: tuổi Q0 NGAY trước clone thực tế ≤ {CLONE_FIRST_WRITE_MAX_AGE_S} s (ở cả cổng trước check-only và cổng ngay trước invocation) để phase ghi cuối `ph4_init_h3` không bị từ chối vì tuổi sau khi clone/init_h1 đã ghi (GPT 120).
- Mỗi plan: lead 90 s (lần đầu), evidence mới READ ONLY zero-active, tuổi Q0 còn ≥ {Q0_AGE_MARGIN_S} s dưới giới hạn {Q0_MAX_AGE_S} s, còn đủ thời gian publisher cho ngân sách plan, `--check-only` ok đúng hash plan/invocation đã freeze, rồi MỘT invocation. Init chỉ vào khi clone PASS thật (PLAN_BOUND + receipt, không QUARANTINE).
- Không DROP/--drop-existing/SET GLOBAL; DB nguồn promoted chỉ đọc; chỉ tạo target mới theo plan.
"""
    PREFLIGHT.write_text(text, encoding="utf-8")
    command = [PY_SUP, str(SUPERVISOR), "--profile", CFG["profile"]["path"], "--expected-profile-sha256", CFG["profile"]["sha256"], "--expected-source-sha256", CFG["pins"]["source"]["sha256"],
               "--expected-interpreter-sha256", CFG["pins"]["interpreter"]["sha256"], "--not-before-utc", stamp(w["not_before_utc"]), "--start-deadline-utc", stamp(w["start_deadline_utc"]),
               "--hard-not-after-utc", stamp(w["hard_not_after_utc"]), "--max-pool-size", "5", "--supervisor-log", str(SUP_LOG), "--publisher-log", str(PUB_LOG), "--stop-file", str(STOP)]
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
    except BaseException as exc:                                                                        # noqa: BLE001
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
    observed = parse_utc(doc["observed_at_utc"])
    return {"present": True, "age_s": (now() - observed).total_seconds(), "active_runs": doc.get("active_runs"), "active_items": doc.get("active_items"), "read_only_verified": doc["session"]["read_only_verified"],
            "latest_run_id": doc.get("latest_run_id"), "latest_run_status": doc.get("latest_run_status"), "sha256": sha(LIVE), "generator_sha256": doc.get("generator_sha256")}


def evidence_clean(state: dict, max_age_s: float) -> bool:
    return bool(state.get("present") and state["read_only_verified"] is True and state["active_runs"] == 0 and state["active_items"] == 0 and state["age_s"] <= max_age_s
                and state["generator_sha256"] == CFG["pins"]["source"]["sha256"])


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
    except BaseException as exc:                                                                        # noqa: BLE001
        safe_log("evidence_error", error=type(exc).__name__)
        stop_rc = ensure_stopped("evidence_error")
        if isinstance(exc, (KeyboardInterrupt, SystemExit)):
            raise
        return 3 if stop_rc else 1
    if ok:
        return 0
    stop_rc = ensure_stopped("evidence_not_clean_busy_or_stale")
    return 3 if stop_rc else 1


# ----------------------------------------------------------------------------- hai plan tuan tu co gate
_RUNNER: Any = None


def runner():
    """Nap runner v2 (exec-hash) tu CHINH tep da ghim trong freeze; hash lech => loi. Dung de tinh lai canonical/spec/argv hash giong het receipt that."""
    global _RUNNER
    if _RUNNER is None:
        path = FIXED_PIN_PATHS["runner"]
        data = path.read_bytes()
        if hashlib.sha256(data).hexdigest() != CFG["pins"]["runner"]["sha256"]:
            raise RuntimeError("runner_sha256_differs_from_freeze_pin")
        module = types.ModuleType("repair_phase_runner_for_ci")
        module.__file__ = str(path)
        exec(compile(data, str(path), "exec"), module.__dict__)                                       # noqa: S102
        _RUNNER = module
    return _RUNNER


def plan_receipts_pass(key: str, stdout_bytes: bytes = b"") -> list[str]:
    """Sau mot plan, kiem chuoi receipt THAT cua runner (102 H-CHAIN): PLAN_BOUND + dung cac receipt theo phase; moi receipt PASS, reasons rong, launch sach, noi dai bang canonical hash receipt truoc,
    cung plan hash/entry da duyet, spec_sha256/argv_sha256 tinh lai tu CHINH plan file, q0_receipt_sha256 = receipt Q0 cho phase ghi; tom tat stdout cua entry (neu co) phai khop tung receipt. Rong => PASS that."""
    p = CFG["plans"][key]
    directory = Path(p["plan_dir"])
    phases = PLAN_PHASES[key]
    expected = ["PLAN_BOUND.json", *[f"receipt_{i:02d}_{name}.json" for i, name in enumerate(phases, 1)]]
    try:
        present = sorted(e.name for e in directory.iterdir())
    except OSError:
        return ["plan_dir_unreadable"]
    if present != sorted(expected):
        return [f"unexpected_plan_dir_content: {present}"]                                            # ke ca QUARANTINE.json / tep la
    problems: list[str] = []
    try:
        rr = runner()
        plan_doc = json.loads(Path(p["plan_file"]).read_text(encoding="utf-8"))["document"]
        specs = {spec["name"]: spec for spec in plan_doc["specs"]}
        if list(phases) != [spec["name"] for spec in plan_doc["specs"]]:
            return ["plan_file_phase_order_differs_from_expected"]
        entry_sha = CFG["pins"]["entry"]["sha256"]
        bound = json.loads((directory / "PLAN_BOUND.json").read_text(encoding="utf-8"))
        if bound.get("kind") != "plan_bound" or bound.get("computed_plan_sha256") != p["plan_sha256"] or bound.get("approved_plan_sha256") != p["plan_sha256"]:
            problems.append("plan_bound_hash")
        if bound.get("entrypoint_sha256") != entry_sha or bound.get("phases") != list(phases):
            problems.append("plan_bound_entry_or_phases")
        previous: str | None = None
        canonical: list[str] = []
        for index, name in enumerate(phases, 1):
            doc = json.loads((directory / f"receipt_{index:02d}_{name}.json").read_text(encoding="utf-8"))
            spec = specs[name]
            launch = doc.get("launch") or {}
            expected_fields = (("phase", name), ("seq", index), ("verdict", "PASS"), ("reasons", []), ("prev_receipt_sha256", previous), ("approved_plan_sha256", p["plan_sha256"]),
                               ("computed_plan_sha256", p["plan_sha256"]), ("entrypoint_sha256", entry_sha), ("spec_sha256", rr.spec_sha256(spec)), ("argv_sha256", rr.argv_sha256(spec)), ("kind", spec["kind"]))
            bad = [field for field, value in expected_fields if doc.get(field) != value]
            if spec["kind"] == "write" and doc.get("q0_receipt_sha256") != CFG["q0"]["sha256"]:
                bad.append("q0_receipt_sha256")
            if launch.get("exit_code") != 0 or launch.get("timed_out") or launch.get("killed_tree") or launch.get("aborted_by_gate") or launch.get("contained") is not True:
                bad.append("launch_not_clean")
            if bad:
                problems.append(f"receipt_rejected: {name}: {bad}")
            previous = rr.sha256_hex(rr.canonical_json(doc))
            canonical.append(previous)
        if stdout_bytes:
            summary = json.loads(stdout_bytes.decode("utf-8", errors="replace").strip().splitlines()[-1])
            rows = summary.get("phases")
            if summary.get("ok") is not True or summary.get("computed_plan_sha256") != p["plan_sha256"] or summary.get("approved_plan_sha256") != p["plan_sha256"] \
                    or summary.get("invocation_sha256") != p["invocation_sha256"] or summary.get("entrypoint_sha256") != entry_sha:
                problems.append("stdout_summary_header")
            if not (isinstance(rows, list) and len(rows) == len(phases) and all(r.get("phase") == n and r.get("verdict") == "PASS" and r.get("reasons") == [] and r.get("receipt_sha256") == c
                                                                                for r, n, c in zip(rows, phases, canonical))):
                problems.append("stdout_summary_phases")
    except (OSError, ValueError, KeyError, IndexError, AttributeError, TypeError, RuntimeError) as exc:
        problems.append(f"receipt_chain_unreadable: {type(exc).__name__}")
    return problems


def plan_gate_reason(key: str, started: dict, *, first: bool, stage: str) -> str | None:
    """Mot cong kiem lai (dung TRUOC check-only va lai NGAY TRUOC invocation, 102 H-TIME): publisher con chay, evidence moi READ ONLY zero-active, start_deadline (chi plan dau), hard_not_after, tuoi Q0,
    va thoi gian publisher con lai >= budget plan + margin. Tra ly do tu choi (None = dat)."""
    p, w = CFG["plans"][key], CFG["windows"]
    if any(e["event"] == "stopped" for e in events(SUP_LOG)):
        return "publisher_already_stopped"
    state = evidence_state()
    if not evidence_clean(state, 100):
        return "evidence_not_clean_or_stale"
    if first and now() >= w["start_deadline_utc"]:                                                      # 104: start_deadline la cong cua invocation THUC dau tien => kiem o CA hai lan
        return "start_deadline_passed"
    if now() >= w["hard_not_after_utc"]:
        return "hard_not_after_passed"
    age = (now() - parse_utc(CFG["q0"]["created_at_utc"])).total_seconds()
    if age > Q0_MAX_AGE_S - Q0_AGE_MARGIN_S:
        return "q0_receipt_too_old"
    if key == "clone" and age > CLONE_FIRST_WRITE_MAX_AGE_S:                                            # GPT 120: ap dung o CA hai cong cua clone thuc te (truoc check-only va NGAY truoc invocation)
        return "q0_receipt_too_old_for_the_last_ci_write_phase"
    need = p["total_budget_s"] + PLAN_TIMEOUT_MARGIN_S
    remaining = (min(parse_utc(started["wall_deadline_utc"]), w["hard_not_after_utc"]) - now()).total_seconds()
    if remaining < need:
        return f"insufficient_publisher_time: remaining={round(remaining, 1)} need={need}"
    return None


def run_plan(key: str, started: dict, *, first: bool) -> int:
    """Mot plan: lead, cong, check-only dung hash, chuan bi thu muc bao cao (init), CONG LAN HAI ngay truoc invocation, DUNG MOT invocation (stdout/stderr bytes), kiem chuoi receipt. 0 = PASS that."""
    p = CFG["plans"][key]
    start_t = dt.datetime.strptime(started["at_utc"], "%Y-%m-%dT%H:%M:%S.%fZ").replace(tzinfo=dt.timezone.utc)
    wait = LEAD_S - (now() - start_t).total_seconds()                                                   # lead tinh theo luc publisher khoi dong: chi cho o plan dau, plan sau tu nhien = 0
    if wait > 0:
        log("waiting_lead", seconds=round(wait, 1))
        time.sleep(wait)
    reason = plan_gate_reason(key, started, first=first, stage="before_check_only")
    if reason:
        log("plan_refused", plan=key, reason=reason, stage="before_check_only")
        return 2
    argv = approved_argv(key)
    check = subprocess.run([*argv, "--check-only"], cwd=str(ROOT), env=ENV6, capture_output=True, text=False, timeout=CHECK_ONLY_CAP_S, creationflags=0x08000000)
    check_out = check.stdout.decode("utf-8", errors="replace").strip()
    log("check_only", plan=key, exit_code=check.returncode, stdout=check_out[:2000], stderr=check.stderr.decode("utf-8", errors="replace").strip()[:500])
    try:
        verdict = json.loads(check_out.splitlines()[-1])
    except (ValueError, IndexError):
        verdict = {}
    if check.returncode != 0 or verdict.get("ok") is not True or verdict.get("computed_plan_sha256") != p["plan_sha256"] or verdict.get("computed_invocation_sha256") != p["invocation_sha256"]:
        log("plan_refused", plan=key, reason="check_only_not_ok_or_hash_differs")
        return 2
    if key == "init":
        try:
            H2_REPORTS.mkdir(parents=True, exist_ok=False)                                                # dung mot thu muc bao cao MOI (cha chua ton tai); tu choi neu da co
        except OSError as exc:
            log("plan_refused", plan=key, reason=f"h2_reports_dir_unavailable:{type(exc).__name__}")
            return 2
        if any(r.exists() for r in CFG["h2_reports"]):
            log("plan_refused", plan=key, reason="h2_report_file_already_exists")
            return 2
    reason = plan_gate_reason(key, started, first=first, stage="before_invocation")                      # check-only co the ton toi 120 s: kiem lai NGAY truoc invocation
    if reason:
        log("plan_refused", plan=key, reason=reason, stage="before_invocation")
        return 2
    need = p["total_budget_s"] + PLAN_TIMEOUT_MARGIN_S
    run = subprocess.run(argv, cwd=str(ROOT), env=ENV6, capture_output=True, text=False, timeout=int(need), creationflags=0x08000000)             # DUNG MOT LAN, khong retry; bytes goc
    (RUN / f"ci_{key}_entry_stdout.json").write_bytes(run.stdout)                                         # byte-exact: khong decode/encode lai, khong chuan hoa newline
    (RUN / f"ci_{key}_entry_stderr.txt").write_bytes(run.stderr)
    log("plan_invoked", plan=key, exit_code=run.returncode, stdout_sha256=hashlib.sha256(run.stdout).hexdigest(), stderr_sha256=hashlib.sha256(run.stderr).hexdigest(), stderr_bytes=len(run.stderr))
    print(run.stdout.decode("utf-8", errors="replace")[-2000:])
    if run.returncode != 0:
        return run.returncode
    problems = plan_receipts_pass(key, run.stdout)
    if problems:
        log("plan_not_pass", plan=key, problems=problems)
        return 5
    log("plan_pass", plan=key)
    return 0


def cmd_plans() -> int:
    """Boc than trong try/finally: moi duong ket thuc (xong/tu choi/loi/cancel) deu yeu cau dung publisher va xac minh."""
    rc: int | None = None
    try:
        started = next((e for e in events(SUP_LOG) if e["event"] == "started"), None)
        if started is None:
            log("plans_refused", reason="publisher_not_started")
            rc = 2
            return rc
        for index, key in enumerate(PLAN_ORDER):
            rc = run_plan(key, started, first=(index == 0))
            if rc != 0:
                return rc                                                                               # clone khong PASS that => init KHONG chay
        return 0
    finally:
        stop_rc = ensure_stopped("plans_terminal" if rc is not None else "plans_exception_or_cancel")
        if stop_rc and rc is not None:
            safe_log("plans_cleanup_not_verified", stop_exit_code=stop_rc, plans_exit_code=rc)
            sys.exit(3)


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

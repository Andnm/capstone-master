"""Operator BUILD (giai doan cuoi cua sua loi free_cancellation/breakfast; GPT 100/114): MOT publisher, MOT plan `build` gom HAI phase GHI (`ph5_build_h1`, `ph5_build_h3`), moi phase mot dataset version MOI, thu muc dau ra
`outputs\\datasets\\<version>` PHAI chua ton tai truoc va duoc runner niem phong (`output_dirs`/`directory_seal`) sau build. Ban phat sinh co chu dich tu operator_q0var.py (da PASS) + operator_heavy_ci.py (phase ghi, Q0 pin) voi:
run-root THAM SO lay tu freeze (chi `outputs\\repair-run-build-rNNN`), Q0 receipt (chi tu `outputs\\repair-run-q0[ab]-rNNN\\q0\\q0_receipt.json`) duoc kiem bang CHINH `runner.load_q0_receipt` o preconditions va moi cong, cong TUOI Q0 tinh den
PHASE GHI CUOI (tuoi luc bat dau plan + du tru toi da cac phase truoc + margin <= `q0_max_age_s` cua plan — tranh de h1 chay xong roi h3 bi tu choi vi Q0 het han), bang chung H2 (thu muc plan H2 dung 5 tep, moi tep `required_files` cua
spec khop dia), receipt phase ghi (Q0 pin, quiescence VERIFIED, niem phong thu muc khop va tinh lai dung). KHONG co quyen thuc thi, KHONG retry, KHONG DROP/SET GLOBAL, khong xoa thu muc dau ra.

Moi buoc in MOT dong JSON va ghi vao bld_operator_log.jsonl (noi tiep).
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
import types
from pathlib import Path
from typing import Any

ROOT = Path(r"D:\MSE\CAPSTONE")
PROJECT = ROOT / "hotel-price-intelligence"
RUN = ROOT / "outputs" / "repair-run-build-unset"                                                       # dat lai boi configure(run_root) khi nap freeze
PY_VENV = str(PROJECT / "eda" / ".venv" / "Scripts" / "python.exe")
PY_SUP = r"C:\Users\ADMIN\anaconda3\python.exe"
SUPERVISOR = PROJECT / "audit_tools" / "renewal_supervisor_v1.py"
ENTRY = PROJECT / "audit_tools" / "repair_run_v1.py"
DATASET_ROOT = ROOT / "outputs" / "datasets"
RUN_NAME_RE = re.compile(r"^repair-run-build-r[0-9]{3}$")
Q0_RUN_NAME_RE = re.compile(r"^repair-run-q0[ab]-r[0-9]{3}$")
VERSION_RE = re.compile(r"^ds_[0-9]{8}_[a-z0-9_]{1,24}$")
PLAN_ORDER = ("build",)
PLAN_PHASES = {"build": ("ph5_build_h1", "ph5_build_h3")}
H2_EXPECTED_FILES = ("PLAN_BOUND.json", "receipt_01_ph4_init_h1.json", "receipt_02_ph4_verify_h1.json", "receipt_03_ph4_init_h3.json", "receipt_04_ph4_verify_h3.json")
Q0_AGE_MARGIN_S = 120                                                                                   # margin tuoi Q0 (entry van kiem tuoi that o MOI phase ghi)


def configure(run_root: Path) -> None:
    """Dat cac duong dan phu thuoc run-root (cung ma cho moi lan build, khac run-root)."""
    global RUN, LIVE, SUP_LOG, PUB_LOG, STOP, SUP_STDOUT, OP_LOG, PREFLIGHT, ARCHIVE_DIR
    RUN = Path(run_root)
    LIVE = RUN / "live_crawl_idle_evidence.json"
    SUP_LOG, PUB_LOG, STOP = RUN / "bld_supervisor.jsonl", RUN / "bld_publisher.log", RUN / "bld_STOP"
    SUP_STDOUT = RUN / "bld_supervisor_stdout.log"
    OP_LOG, PREFLIGHT = RUN / "bld_operator_log.jsonl", RUN / "bld_dossier_preflight.md"
    ARCHIVE_DIR = RUN / "evidence_archive" / "build"


configure(RUN)
ENV6 = {"SystemRoot": r"C:\Windows", "TEMP": r"C:\Windows\Temp", "TMP": r"C:\Windows\Temp", "PATH": r"C:\Windows\System32;C:\Windows", "PYTHONUTF8": "1", "PYTHONIOENCODING": "utf-8"}
POWERSHELL = r"C:\Windows\System32\WindowsPowerShell\v1.0\powershell.exe"
LEAD_S = 90
PLAN_TIMEOUT_MARGIN_S = 120
CHECK_ONLY_CAP_S = 120                                                                                  # timeout cua `--check-only`: plan co the ton toi da chung nay TRUOC invocation (102 H-TIME)
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
    if RUN.is_dir():                                                                                    # truoc khi freeze xac dinh run-root thi chi in (khong co thu muc de ghi)
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


def _num(value: Any) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool)


def q0_path_ok(text: Any) -> bool:
    """Q0 receipt chi duoc nam o `outputs\\repair-run-q0[ab]-rNNN\\q0\\q0_receipt.json` (khong tro sang tep khac)."""
    if not _abs_ok(text):
        return False
    path = Path(text)
    return path.name == "q0_receipt.json" and path.parent.name == "q0" and bool(Q0_RUN_NAME_RE.match(path.parent.parent.name)) and path.parent.parent.parent == ROOT / "outputs"


FIXED_PIN_PATHS = {"source": PROJECT / "audit_tools" / "crawl_idle_evidence.py", "interpreter": Path(PY_VENV), "parent_interpreter": Path(PY_SUP), "supervisor": SUPERVISOR, "entry": ENTRY,
                   "runner": PROJECT / "audit_tools" / "repair_phase_runner_v2.py", "observer_module": PROJECT / "audit_tools" / "repair_db_observer_v1.py",
                   "quiescence_model": PROJECT / "audit_tools" / "quiescence_model_v1.py"}                  # freeze KHONG duoc tro sang tep khac cho cac thanh phan co dinh


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
    if not (isinstance(doc, dict) and doc.get("kind") == "build_freeze"):
        return ["freeze_kind_or_run_root"]
    run_root = doc.get("run_root")
    if not (_abs_ok(run_root) and Path(run_root).parent == ROOT / "outputs" and RUN_NAME_RE.match(Path(run_root).name)):
        return ["freeze_kind_or_run_root"]
    configure(Path(run_root))
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
        q0_ok = q0_path_ok(q0.get("path")) and _is_sha(q0.get("sha256"))
    except (TypeError, ValueError):
        q0_ok = False
    if not q0_ok:
        problems.append("freeze_q0_receipt")
    plans: dict[str, dict[str, Any]] = {}
    for key in PLAN_ORDER:
        p = (doc.get("plans") or {}).get(key) or {}
        need = ("plan_file", "plan_file_sha256", "plan_sha256", "approval", "approval_sha256", "invocation_sha256", "plan_dir", "launcher_logs", "total_budget_s", "dataset_dirs", "q0_max_age_s",
                "q0_preceding_worst_case_s")
        if any(k not in p for k in need) or not all(_abs_ok(p[k]) for k in ("plan_file", "approval", "plan_dir", "launcher_logs")) or not all(_num(p[k]) for k in ("total_budget_s", "q0_max_age_s", "q0_preceding_worst_case_s")):
            problems.append(f"freeze_plan_{key}")
            continue
        dirs = p["dataset_dirs"]
        if not (isinstance(dirs, list) and len(dirs) == 2 and len(set(dirs)) == 2 and all(_abs_ok(d) and Path(d).parent == DATASET_ROOT and VERSION_RE.match(Path(d).name) for d in dirs)):
            problems.append(f"freeze_plan_{key}_dataset_dirs")
        if p["q0_preceding_worst_case_s"] < 0 or p["q0_preceding_worst_case_s"] + Q0_AGE_MARGIN_S >= p["q0_max_age_s"]:             # (max_age <= 0 da nam trong dieu kien thu hai: preceding >= 0 va margin > 0)
            problems.append(f"freeze_plan_{key}_q0_age_budget_impossible")
        if Path(p["plan_dir"]) != RUN / key / "plan" or Path(p["launcher_logs"]) != RUN / key / "logs":
            problems.append(f"freeze_plan_{key}_dirs_not_in_run_root")
        plans[key] = p
    h2 = doc.get("h2") or {}
    reports = h2.get("reports")
    if not (_abs_ok(h2.get("plan_dir")) and _is_sha(h2.get("plan_sha256")) and isinstance(reports, list) and len(reports) == 2 and all(_abs_ok(x) for x in reports)):
        problems.append("freeze_h2")
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
    if problems:
        return problems
    CFG.update(doc=doc, windows=windows, q0=q0, plans=plans, h2=h2, profile=profile, pins=pins, freeze_sha256=expected_sha256)
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


# ----------------------------------------------------------------------------- trang thai publisher (sao tu operator_q0var, da duyet)
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


# ----------------------------------------------------------------------------- module nap theo hash (exec-hash) tu CHINH tep da ghim trong freeze
_RUNNER: Any = None
_OBSERVER: Any = None


def _load_pinned(name: str, module_name: str) -> Any:
    path = FIXED_PIN_PATHS[name]
    data = path.read_bytes()
    if hashlib.sha256(data).hexdigest() != CFG["pins"][name]["sha256"]:
        raise RuntimeError(f"{name}_sha256_differs_from_freeze_pin")
    module = types.ModuleType(module_name)
    module.__file__ = str(path)
    exec(compile(data, str(path), "exec"), module.__dict__)                                           # noqa: S102
    return module


def runner():
    """Nap runner v2 (exec-hash) tu CHINH tep da ghim; hash lech => loi. Dung tinh lai canonical/spec/argv hash va niem phong thu muc giong het receipt that."""
    global _RUNNER
    if _RUNNER is None:
        _RUNNER = _load_pinned("runner", "repair_phase_runner_for_build")
    return _RUNNER


def observer_module():
    global _OBSERVER
    if _OBSERVER is None:
        _OBSERVER = _load_pinned("observer_module", "repair_db_observer_for_build")
    return _OBSERVER


# ----------------------------------------------------------------------------- Q0 receipt + cau truc plan
def q0_allowed_age_s(p: dict[str, Any]) -> float:
    """Tuoi toi da cua Q0 TAI LUC BAT DAU plan sao cho phase ghi CUOI van thay Q0 con hop le: max_age cua plan - du tru toi da cac phase truoc - margin."""
    return float(p["q0_max_age_s"]) - float(p["q0_preceding_worst_case_s"]) - Q0_AGE_MARGIN_S


def q0_problem(max_age_s: float) -> str | None:
    """Q0 receipt PHAI qua `runner.load_q0_receipt` (cung ham entry dung cho phase ghi): hash = freeze, kind, observer/SQL-set/model pin, 5 khoa self-test, server identity, tuoi trong gioi han. None = hop le."""
    try:
        rr = runner()
        observer = observer_module()
        rr.load_q0_receipt(CFG["q0"]["path"], expected_receipt_sha256=CFG["q0"]["sha256"], expected_observer_sha256=observer.script_sha256(), expected_sql_set_sha256=observer.sql_set_sha256(),
                           expected_model_sha256=rr.qm.__source_sha256__, now_utc=now(), max_age_s=float(max_age_s))
    except (OSError, ValueError, KeyError, AttributeError, TypeError, RuntimeError) as exc:
        return f"q0_receipt_invalid_or_too_old: {type(exc).__name__}: {str(exc)[:160]}"
    return None


def plan_structure_problems(key: str) -> list[str]:
    """Plan file phai KHOP freeze: ten/thu tu phase, deu phase GHI, output_dirs dung thu muc dataset, Q0 pin = receipt freeze, settings/budget khop, plan hash, va MOI tep `required_files` (gom bang chung H2) khop dia."""
    p = CFG["plans"][key]
    problems: list[str] = []
    try:
        plan = json.loads(Path(p["plan_file"]).read_text(encoding="utf-8"))
        doc = plan["document"]
        specs = doc["specs"]
        if plan.get("plan_sha256") != p["plan_sha256"]:
            problems.append("plan_file_hash_differs_from_freeze")
        if [s["name"] for s in specs] != list(PLAN_PHASES[key]):
            return [*problems, "plan_phases_differ_from_expected"]
        if any(s.get("kind") != "write" for s in specs):
            problems.append("plan_has_a_non_write_phase")
        if [s.get("output_dirs") for s in specs] != [[d] for d in p["dataset_dirs"]]:
            problems.append("plan_output_dirs_differ_from_freeze")
        if doc.get("q0_receipt_sha256") != CFG["q0"]["sha256"]:
            problems.append("plan_q0_pin_differs_from_freeze")
        settings = doc.get("settings") or {}
        if settings.get("q0_max_age_s") != p["q0_max_age_s"] or settings.get("total_budget_s") != p["total_budget_s"]:
            problems.append("plan_settings_differ_from_freeze")
        drifted = []
        for spec in specs:
            for path, digest in (spec.get("required_files") or {}).items():
                try:
                    if sha(path) != digest:
                        drifted.append(Path(path).name)
                except OSError:
                    drifted.append(Path(path).name)
        if drifted:
            problems.append(f"required_files_drifted: {sorted(set(drifted))[:6]}")
    except (OSError, ValueError, KeyError, TypeError, AttributeError) as exc:
        problems.append(f"plan_file_unreadable: {type(exc).__name__}")
    return problems


def h2_problems() -> list[str]:
    problems: list[str] = []
    h2 = CFG["h2"]
    try:
        names = sorted(e.name for e in Path(h2["plan_dir"]).iterdir())
    except OSError:
        return ["h2_plan_dir_unreadable"]
    if names != sorted(H2_EXPECTED_FILES):
        problems.append(f"h2_plan_dir_unexpected_content: {names}")
    else:
        try:
            bound = json.loads((Path(h2["plan_dir"]) / "PLAN_BOUND.json").read_text(encoding="utf-8"))
            if bound.get("computed_plan_sha256") != h2["plan_sha256"] or bound.get("approved_plan_sha256") != h2["plan_sha256"]:
                problems.append("h2_plan_bound_hash_differs_from_freeze")
        except (OSError, ValueError):
            problems.append("h2_plan_bound_unreadable")
    for report in h2["reports"]:
        if not Path(report).is_file():
            problems.append(f"h2_report_missing: {Path(report).name}")
    return problems


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
    absent = [LIVE, STOP, PUB_LOG, SUP_LOG, SUP_STDOUT, PREFLIGHT, ARCHIVE_DIR]
    for key, p in CFG["plans"].items():
        absent += [Path(p["launcher_logs"]), RUN / f"bld_{key}_entry_stdout.json", RUN / f"bld_{key}_entry_stderr.txt", *[Path(d) for d in p["dataset_dirs"]]]
    for path in absent:
        if os.path.lexists(path):
            problems.append(f"already_exists: {path.name}")
    for key, p in CFG["plans"].items():
        plan_dir = Path(p["plan_dir"])
        if not plan_dir.is_dir() or any(plan_dir.iterdir()):
            problems.append(f"plan_dir_not_empty: {key}")
        problems += plan_structure_problems(key)
        allowed = q0_allowed_age_s(p)
        reason = q0_problem(allowed)
        if reason:
            problems.append(f"{reason} (allowed_age_at_start_s={allowed})")
    problems += h2_problems()
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
    plan = CFG["plans"]["build"]
    text = f"""# Build dossier — xác nhận lifecycle trước khi khởi động ({stamp(t)})

Tôi (Claude) xác nhận các điều kiện trong ruling thực thi của GPT và freeze manifest SHA `{CFG['freeze_sha256']}` (run-root `{RUN}`):

- Chỉ MỘT publisher phục vụ MỘT plan `build` gồm HAI phase ghi (`ph5_build_h1`, `ph5_build_h3`); không plan phụ; không auto-restart/extend/đổi cadence; không DROP/--drop-existing/SET GLOBAL; không xoá thư mục đầu ra.
- Thư mục dataset (phải chưa tồn tại, runner niêm phong sau build): {plan['dataset_dirs']}.
- Cửa sổ: {stamp(w['not_before_utc'])} ≤ start < {stamp(w['start_deadline_utc'])}; hết hiệu lực tuyệt đối {stamp(w['hard_not_after_utc'])}; N={p['renew_seconds']} s.
- Env Clear + đúng 6 biến, `shell=False`, `CREATE_NO_WINDOW`; stdout/stderr ghi thẳng vào `bld_publisher.log`; kiểm `DB_POOL_SIZE` ≤5 bằng child hermetic TRƯỚC import tạo pool.
- Probe crawler OS là fail-closed (returncode/stderr/timeout đều phải thành công và PID rỗng).
- Q0 receipt `{CFG['q0']['sha256']}` qua `load_q0_receipt` với tuổi lúc bắt đầu ≤ {q0_allowed_age_s(plan)} s (= q0_max_age_s {plan['q0_max_age_s']} − dự trù phase trước {plan['q0_preceding_worst_case_s']} − margin {Q0_AGE_MARGIN_S}).
- Plan khớp freeze (tên/thứ tự phase, mọi phase ghi, output_dirs, Q0 pin, settings, mọi tệp `required_files` khớp đĩa — gồm bằng chứng H2), thư mục plan H2 đúng 5 tệp.
- Lead 90 s, evidence mới READ ONLY zero-active, còn đủ thời gian publisher cho ngân sách plan; `--check-only` ok đúng hash plan/invocation đã freeze; kiểm lại cổng NGAY trước invocation; rồi MỘT invocation (stdout/stderr ghi byte-exact).
- Sau invocation: chuỗi receipt hai phase khớp runner thật (Q0 pin, quiescence VERIFIED, niêm phong thư mục khớp và tính lại đúng); timeout/killed/aborted/uncontained/nonzero/thiếu/dở ⇒ FAIL.
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


# ----------------------------------------------------------------------------- plan co gate
def plan_receipts_pass(key: str, stdout_bytes: bytes = b"") -> list[str]:
    """Sau plan, kiem chuoi receipt THAT cua runner: PLAN_BOUND + dung cac receipt theo phase; moi receipt PASS, reasons rong, launch sach, noi dai bang canonical hash receipt truoc, cung plan hash/entry da duyet, spec/argv hash
    tinh lai tu CHINH plan file, phase GHI: Q0 pin = receipt freeze, quiescence VERIFIED, `output_dirs` = dung thu muc dataset da niem phong (files>0) VA niem phong tinh lai bay gio KHOP receipt; tom tat stdout (neu co) khop tung
    receipt. Rong => PASS that."""
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
            if spec["kind"] != "write" or doc.get("q0_receipt_sha256") != CFG["q0"]["sha256"]:
                bad.append("not_a_write_phase_with_the_q0_pin")
            if (doc.get("quiescence") or {}).get("status") != rr.qm.VERIFIED:
                bad.append("quiescence_not_verified")
            if launch.get("exit_code") != 0 or launch.get("timed_out") or launch.get("killed_tree") or launch.get("aborted_by_gate") or launch.get("contained") is not True:
                bad.append("launch_not_clean")
            seals = doc.get("output_dirs")
            wanted = list(spec.get("output_dirs", []))
            if not (isinstance(seals, dict) and sorted(seals) == sorted(wanted) and wanted):
                bad.append("output_dirs_keys")
            else:
                for output in wanted:
                    seal = seals[output]
                    if not (isinstance(seal, dict) and seal.get("kind") == "directory" and type(seal.get("files")) is int and seal["files"] > 0):
                        bad.append(f"output_dir_not_sealed: {Path(output).name}")
                        continue
                    try:
                        if rr.directory_seal(output) != seal:
                            bad.append(f"output_dir_changed_after_seal: {Path(output).name}")
                    except (ValueError, OSError):
                        bad.append(f"output_dir_cannot_be_resealed: {Path(output).name}")
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
    """Mot cong kiem lai (dung TRUOC check-only va lai NGAY TRUOC invocation, 102 H-TIME): publisher con chay, evidence moi READ ONLY zero-active, start_deadline (plan dau), hard_not_after, Q0 receipt hop le va du tre
    de phase ghi CUOI van thay no con han, va thoi gian publisher con lai >= budget plan + margin. Tra ly do tu choi (None = dat)."""
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
    reason = q0_problem(q0_allowed_age_s(p))
    if reason:
        return "q0_receipt_unusable_for_the_last_write_phase: " + reason
    need = p["total_budget_s"] + PLAN_TIMEOUT_MARGIN_S
    remaining = (min(parse_utc(started["wall_deadline_utc"]), w["hard_not_after_utc"]) - now()).total_seconds()
    if remaining < need:
        return f"insufficient_publisher_time: remaining={round(remaining, 1)} need={need}"
    return None


def run_plan(key: str, started: dict, *, first: bool) -> int:
    """Mot plan: lead, cong, check-only dung hash, kiem thu muc dataset chua ton tai, CONG LAN HAI ngay truoc invocation, DUNG MOT invocation (stdout/stderr bytes), kiem chuoi receipt. 0 = PASS that."""
    p = CFG["plans"][key]
    start_t = dt.datetime.strptime(started["at_utc"], "%Y-%m-%dT%H:%M:%S.%fZ").replace(tzinfo=dt.timezone.utc)
    wait = LEAD_S - (now() - start_t).total_seconds()                                                   # lead tinh theo luc publisher khoi dong
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
    existing = [d for d in p["dataset_dirs"] if os.path.lexists(d)]
    if existing:                                                                                         # dataset dir phai chua ton tai ngay truoc invocation (runner cung tu choi, day la cong som)
        log("plan_refused", plan=key, reason="dataset_dir_already_exists", dirs=existing)
        return 2
    reason = plan_gate_reason(key, started, first=first, stage="before_invocation")                      # check-only co the ton toi 120 s: kiem lai NGAY truoc invocation
    if reason:
        log("plan_refused", plan=key, reason=reason, stage="before_invocation")
        return 2
    need = p["total_budget_s"] + PLAN_TIMEOUT_MARGIN_S
    run = subprocess.run(argv, cwd=str(ROOT), env=ENV6, capture_output=True, text=False, timeout=int(need), creationflags=0x08000000)             # DUNG MOT LAN, khong retry; bytes goc
    (RUN / f"bld_{key}_entry_stdout.json").write_bytes(run.stdout)                                      # byte-exact: khong decode/encode lai, khong chuan hoa newline
    (RUN / f"bld_{key}_entry_stderr.txt").write_bytes(run.stderr)
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
                return rc                                                                               # plan khong PASS that => dung
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

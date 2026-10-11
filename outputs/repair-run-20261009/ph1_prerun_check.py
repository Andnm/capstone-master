"""Pre-run READ-ONLY cho Ph1 (92 §1): xac nhan pin, dau ra chua ton tai, Q0 verified-dead, live Q0 dung SHA, dich archival chua co, VA mot OS crawler-process inventory THANH CONG
(ghi returncode/stderr/timeout/PID thuc; probe loi KHONG duoc coi la idle). Khong cham DB, khong ghi gi ngoai `ph1_prerun_check.json` + `ph1_supplemental_dossier.md`. Ma thoat 0 chi khi tat ca dat."""
from __future__ import annotations

import datetime as dt
import hashlib
import json
import subprocess
import sys
from pathlib import Path

RUN = Path(r"D:\MSE\CAPSTONE\outputs\repair-run-20261009")
ROOT = Path(r"D:\MSE\CAPSTONE")
PROJECT = ROOT / "hotel-price-intelligence"
DISCUSS = ROOT / "discuss" / "model-results-improvement-20261008"
OUT_JSON, OUT_MD = RUN / "ph1_prerun_check.json", RUN / "ph1_supplemental_dossier.md"
ENV6 = {"SystemRoot": r"C:\Windows", "TEMP": r"C:\Windows\Temp", "TMP": r"C:\Windows\Temp", "PATH": r"C:\Windows\System32;C:\Windows", "PYTHONUTF8": "1", "PYTHONIOENCODING": "utf-8"}
PINS = {
    r"C:\Users\ADMIN\anaconda3\python.exe": "7679e53fa969789309e81fdad0d52b8cda5f83c9abf7cb31a3c58bf24b31e264",
    str(RUN / "run_ph1_workflow.py"): "0192533ee5cb0ee0a7693e9af3baeaaa5f88a77274bd5c18b369b24c855d12ec",
    str(RUN / "operator_ph1.py"): "e8c59eb0c08fd4144dee51728f5c48dbdfdc7f3246a8f933ecf2dc7e5a99873d",
    str(PROJECT / "audit_tools" / "renewal_supervisor_v1.py"): "1fe241c64871f64b755e5bc6f0ac2bb689d4ab4d6720e2e27cd634ef1ab38d42",
    str(DISCUSS / "90a-gpt-ph1-outer-approval.md"): "7d4c007e65a814c8438d42f7aa3e685ba0e90fe244a93458434450e89a46d3b7",
    str(DISCUSS / "90b-gpt-ph1-evidence-renewal-approval.md"): "4131145de37a9dfc1d3a1c7adcd8578b388220779685551ed053d1ded9f369d1",
    str(RUN / "renewal_profile_ph1_r83.json"): "a5a0f1e0bf6f64101884d2b1d726b26f0f871b37893e1841edb211554f917f51",
    str(ROOT / "outputs" / "repair-proposal-20261009-r83" / "ph1.plan.json"): "0dbfc554706c9a60a7f7a926f02e4659fcd0f2c22e9c294b6e3e693989ebe24a",
    str(PROJECT / "audit_tools" / "crawl_idle_evidence.py"): "6429482e6deddf711a8228726ec4776d7450bb82b526194299c9eae6c82ed4d9",
    str(PROJECT / "eda" / ".venv" / "Scripts" / "python.exe"): "0e818a1f9a0b8fbd4e7cc458a07cb7de2ea02ea326e387699a33b92f151242cd",
    str(PROJECT / "audit_tools" / "repair_run_v1.py"): "bc93c167f7f64544818cb7b4ce54e11cca6e39f77dd400987df038e586182fb0",
    str(PROJECT / "audit_tools" / "repair_phase_runner_v2.py"): "d3b4b595bd7c52a67af147f2ef25c614627fc9e57fdf5815791ffc463951aa8d",
    str(DISCUSS / "82b-gpt-ph1-inner-producer-approval.md"): "d20e65dcbfabd495fa8d1d14964edf418325acbe90f5eff3ae092ea23ffebca7",
}
ABSENT = [RUN / n for n in ("ph1_supervisor.jsonl", "ph1_supervisor_stdout.log", "ph1_publisher.log", "ph1_STOP", "ph1_operator_log.jsonl", "ph1_dossier_preflight.md", "ph1_dossier_outer_cleanup_ack.md",
                            "ph1_workflow_result.json", "ph1_entry_stdout.json", "ph1_entry_stderr.txt")] + [RUN / "ph1" / "logs", RUN / "evidence_archive" / "ph1"]
Q0_LIVE_SHA = "d54b6ca1417475fa62e015f428883c516fd0ada372270dc52052616bd2b9336c"
TARGET = RUN / "q0" / f"live_final_before_ph1_{Q0_LIVE_SHA}.json"
PS = r"C:\Windows\System32\WindowsPowerShell\v1.0\powershell.exe"
PS_CMD = "Get-CimInstance Win32_Process | Where-Object { $_.Name -match '^python' -and $_.CommandLine -match 'run_scheduled_calendar' } | ForEach-Object { $_.ProcessId }"


def sha(p) -> str:
    return hashlib.sha256(Path(p).read_bytes()).hexdigest()


def probe() -> dict:
    out = {"started_utc": dt.datetime.now(dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"), "command": PS_CMD}
    try:
        done = subprocess.run([PS, "-NoProfile", "-NonInteractive", "-Command", PS_CMD], capture_output=True, text=True, timeout=60, env=ENV6, creationflags=0x08000000)
        pids = [int(x) for x in done.stdout.split() if x.strip().isdigit()]
        junk = [x for x in done.stdout.split() if x.strip() and not x.strip().isdigit()]
        out.update(returncode=done.returncode, stderr=done.stderr.strip()[:500], stdout_nonnumeric_tokens=junk[:5], pids=pids, timed_out=False)
        out["success"] = done.returncode == 0 and not done.stderr.strip() and not junk
    except subprocess.TimeoutExpired:
        out.update(returncode=None, stderr="", pids=[], timed_out=True, success=False)
    except OSError as exc:
        out.update(returncode=None, stderr=f"{type(exc).__name__}", pids=[], timed_out=False, success=False)
    out["finished_utc"] = dt.datetime.now(dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    return out


def main() -> int:
    now = dt.datetime.now(dt.timezone.utc)
    result: dict = {"checked_at_utc": now.strftime("%Y-%m-%dT%H:%M:%SZ"), "problems": []}
    in_window = dt.datetime(2026, 10, 9, 11, 0, tzinfo=dt.timezone.utc) <= now < dt.datetime(2026, 10, 9, 11, 50, tzinfo=dt.timezone.utc)
    result["dispatch_window_ok"] = in_window
    if not in_window:
        result["problems"].append("outside_dispatch_window_18:00-18:50_VN")
    pins = {}
    for path, expected in PINS.items():
        actual = sha(path)
        pins[path] = {"expected": expected, "actual": actual, "ok": actual == expected}
        if actual != expected:
            result["problems"].append(f"pin_drift: {Path(path).name}")
    result["pins"] = pins
    plan_dir = RUN / "ph1" / "plan"
    result["ph1_plan_dir_empty"] = plan_dir.is_dir() and not any(plan_dir.iterdir())
    if not result["ph1_plan_dir_empty"]:
        result["problems"].append("ph1_plan_dir_not_empty")
    present = [str(p.relative_to(RUN)) for p in ABSENT if p.exists()]
    result["outputs_present_but_must_be_absent"] = present
    result["outputs_checked_absent_count"] = len(ABSENT) - len(present)
    if present:
        result["problems"].append("ph1_outputs_already_exist")
    q0_events = [json.loads(x) for x in (RUN / "q0_supervisor.jsonl").read_text(encoding="utf-8").splitlines()]
    q0_stopped = [e for e in q0_events if e["event"] == "stopped"]
    result["q0_verified_dead"] = bool(q0_stopped) and q0_stopped[-1].get("verified_dead") is True
    if not result["q0_verified_dead"]:
        result["problems"].append("q0_not_verified_dead")
    live = RUN / "live_crawl_idle_evidence.json"
    result["live_q0_sha_ok"] = live.is_file() and sha(live) == Q0_LIVE_SHA
    result["archival_target_absent"] = not TARGET.exists()
    if not result["live_q0_sha_ok"]:
        result["problems"].append("live_q0_sha_mismatch")
    if not result["archival_target_absent"]:
        result["problems"].append("archival_target_exists")
    result["os_crawler_probe"] = probe()
    probe_ok = result["os_crawler_probe"]["success"] and not result["os_crawler_probe"]["pids"]
    result["os_crawler_probe_ok_and_zero_pids"] = probe_ok
    if not probe_ok:
        result["problems"].append("os_crawler_probe_failed_or_crawler_process_alive")
    result["ok"] = not result["problems"]
    OUT_JSON.write_text(json.dumps(result, ensure_ascii=False, indent=1, sort_keys=True), encoding="utf-8")
    OUT_MD.write_text(f"""# Ph1 supplemental dossier — pre-run read-only ({result['checked_at_utc']})

- Dispatch trong 18:00 ≤ VN < 18:50: **{in_window}**; ok tổng: **{result['ok']}**; vấn đề: {result['problems'] or 'không'}.
- Pin (13 tệp, gồm parent interpreter, driver, operator, supervisor, 90a, 90b, profile, plan-file, nguồn publisher, child interpreter, entry, runner, 82b): {sum(1 for v in pins.values() if v['ok'])}/{len(pins)} khớp.
- `ph1/plan` rỗng: {result['ph1_plan_dir_empty']}; đầu ra Ph1 chưa tồn tại: {result['outputs_checked_absent_count']}/{len(ABSENT)}.
- Q0 verified-dead: {result['q0_verified_dead']}; live Q0 đúng SHA `{Q0_LIVE_SHA}`: {result['live_q0_sha_ok']}; archival target chưa tồn tại: {result['archival_target_absent']}.
- OS crawler-process probe (`Get-CimInstance Win32_Process` lọc python + `run_scheduled_calendar`): returncode **{result['os_crawler_probe']['returncode']}**, timed_out **{result['os_crawler_probe']['timed_out']}**, stderr `{result['os_crawler_probe']['stderr']}`, PIDs **{result['os_crawler_probe']['pids']}**, success **{result['os_crawler_probe']['success']}**; không lấy `[]` của probe lỗi làm idle.
- SHA kết quả: xem `ph1_prerun_check.json` (băm trong dossier gửi GPT).
""", encoding="utf-8")
    print(json.dumps({"ok": result["ok"], "problems": result["problems"], "probe": {k: result["os_crawler_probe"][k] for k in ("returncode", "success", "pids", "timed_out")}}, ensure_ascii=False))
    return 0 if result["ok"] else 2


if __name__ == "__main__":
    sys.exit(main())

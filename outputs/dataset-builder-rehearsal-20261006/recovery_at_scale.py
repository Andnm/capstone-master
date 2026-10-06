"""Phuc hoi o quy mo that: ngat tien trinh build giua mot step nang (tren ban sao dev, KHONG dong vao DB van hanh), kiem trang thai, `--apply` lai, doi chieu ket qua.

    eda/.venv/Scripts/python.exe outputs/dataset-builder-rehearsal-20261006/recovery_at_scale.py --version ds_20261006_rh8 --kill-after 100 --reference-version ds_20261006_rh4

Quy trinh: init version moi -> `build_dataset --apply` (Popen) -> sau N giay `taskkill /T /F` -> KILL cac thread MySQL con lai tren DB dev -> `--status`
(ky vong stale/recoverable, KHONG PASS) -> `--apply` lai -> ky vong PASS + `content_sha256` == version tham chieu. Chi tac dong len `warehouse_dsdev_*`.
"""
from __future__ import annotations

import argparse
import json
import subprocess
import sys
import time
from pathlib import Path

ML = Path(r"D:\MSE\CAPSTONE\hotel-price-intelligence\ml")
PY = Path(r"D:\MSE\CAPSTONE\hotel-price-intelligence\eda\.venv\Scripts\python.exe")
BACKEND = Path(r"D:\MSE\CAPSTONE\hotel-price-intelligence\backend")
DATASETS = Path(r"D:\MSE\CAPSTONE\outputs\datasets")
DB = "warehouse_dsdev_20261004_3src"


def run(args: list[str], timeout: int = 3600) -> subprocess.CompletedProcess:
    return subprocess.run([str(PY), *args], cwd=str(ML), capture_output=True, text=True, timeout=timeout, encoding="utf-8", errors="replace")


def kill_dev_threads() -> list[int]:
    sys.path.insert(0, str(BACKEND))
    from dotenv import load_dotenv
    load_dotenv(BACKEND / ".env")
    import mysql.connector
    from app.core.config import settings
    conn = mysql.connector.connect(host=settings.DB_HOST, port=settings.DB_PORT, user=settings.DB_USER, password=settings.DB_PASSWORD, connection_timeout=10, autocommit=True)
    cur = conn.cursor(dictionary=True)
    cur.execute("SHOW FULL PROCESSLIST")
    ids = [int(r["Id"]) for r in cur.fetchall() if r["db"] == DB and r["Command"] != "Sleep"]     # chi DB dev, chi thread dang chay
    for i in ids:
        cur.execute(f"KILL {i}")
    conn.close()
    return ids


def status(version: str) -> dict:
    done = run(["scripts/build_dataset.py", "--database", DB, "--dataset-version", version, "--status"], 120)
    try:
        return json.loads(done.stdout[done.stdout.index("{"):])
    except Exception:  # noqa: BLE001
        return {"raw": (done.stdout + done.stderr)[-600:]}


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--version", required=True)
    parser.add_argument("--kill-after", type=int, default=100)
    parser.add_argument("--reference-version", default=None)
    parser.add_argument("--anomaly-cutoff", default="2026-10-05T00:00:00Z")
    args = parser.parse_args()
    t0 = time.time()
    init = run(["scripts/init_dataset_build.py", "--database", DB, "--dataset-version", args.version, "--purpose", "rehearsal", "--anomaly-cutoff", args.anomaly_cutoff], 300)
    print("INIT exit", init.returncode, init.stdout.strip().splitlines()[-1] if init.stdout.strip() else init.stderr[-300:], flush=True)
    if init.returncode:
        return 2
    proc = subprocess.Popen([str(PY), "scripts/build_dataset.py", "--database", DB, "--dataset-version", args.version, "--apply"], cwd=str(ML),
                            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    time.sleep(args.kill_after)
    mid = status(args.version)
    print(f"[{time.time() - t0:.0f}s] truoc khi kill: active_step={mid.get('active_step')} last_completed={mid.get('last_completed_step')} status={mid.get('status')}", flush=True)
    subprocess.run(["taskkill", "/F", "/T", "/PID", str(proc.pid)], capture_output=True)
    time.sleep(3)
    killed = kill_dev_threads()
    time.sleep(8)
    after = status(args.version)
    print(f"[{time.time() - t0:.0f}s] sau khi kill (thread MySQL dev da kill: {killed}): {json.dumps(after)}", flush=True)
    resume_started = time.time()
    resume = run(["scripts/build_dataset.py", "--database", DB, "--dataset-version", args.version, "--apply"])
    final = status(args.version)
    print(f"[{time.time() - t0:.0f}s] resume exit={resume.returncode} ({time.time() - resume_started:.0f}s): status={final.get('status')} last_completed={final.get('last_completed_step')} "
          f"attempt={final.get('active_step_attempt')}", flush=True)
    if resume.returncode:
        print((resume.stdout + resume.stderr)[-1500:])
    out = {"version": args.version, "kill_after_s": args.kill_after, "before_kill": mid, "after_kill": after, "final": final, "resume_exit": resume.returncode}
    checks = DATASETS / args.version / "output_checksums.json"
    if checks.exists():
        content = json.loads(checks.read_text(encoding="utf-8"))["samples.parquet"]["content_sha256"]
        out["content_sha256"] = content
        if args.reference_version:
            ref = json.loads((DATASETS / args.reference_version / "output_checksums.json").read_text(encoding="utf-8"))["samples.parquet"]["content_sha256"]
            out["reference_content_sha256"] = ref
            out["content_equals_reference"] = content == ref
    print(json.dumps(out, ensure_ascii=False, indent=1))
    (Path(r"D:\MSE\CAPSTONE\outputs\dataset-builder-rehearsal-20261006") / f"recovery_{args.version}.json").write_text(json.dumps(out, ensure_ascii=False, indent=2), encoding="utf-8")
    return 0 if final.get("status") == "pass" else 1


if __name__ == "__main__":
    raise SystemExit(main())

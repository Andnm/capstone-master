"""THUAN (khong DB): dung **freeze manifest** + **renewal profile** cho luong C (clone -> init+verify, mot publisher, hai plan/approval ngoai rieng) tu cac TEP DA CO (Q0 receipt, hai plan, hai approval cua GPT).
Moi gia tri la tham so tuong minh; moi lenh ghi bang `xb` (khong ghi de). Tao hai thu muc plan RONG (`clone/plan`, `init/plan`) trong run-root. KHONG chay publisher/entry, KHONG DB.

    python repair_heavy_ci_freeze_v1.py --q0-receipt R --clone-plan P1 --clone-approval A1 --init-plan P2 --init-approval A2 --not-before-utc T --start-deadline-utc T --hard-not-after-utc T [--run-root DIR]

Kiem: approval (nam NGOAI thu muc plan, 5 khoa) khop plan_sha256/plan_dir/entrypoint/NOT_AFTER; plan co q0_receipt_sha256 = SHA tep receipt; hai plan cung target/q0; init co dung hai verify output nam trong run-root/h2/reports.
N = ceil((tong total_budget_s + margin 60 + lead 90 + evidence_age 120 + n_plans * check_only_cap 120) / 300) * 300  (102 H-TIME: moi plan co the ton toi 120 s cho --check-only truoc invocation).
"""
from __future__ import annotations

import argparse
import datetime as dt
import hashlib
import json
import math
import re
from pathlib import Path
from typing import Any

HERE = Path(__file__).resolve().parent
PROJECT = HERE.parent
ROOT = PROJECT.parent
RUN = ROOT / "outputs" / "repair-run-heavy-ci-r101"
PY = str(PROJECT / "eda" / ".venv" / "Scripts" / "python.exe")
PY_SUP = r"C:\Users\ADMIN\anaconda3\python.exe"
SOURCE = PROJECT / "audit_tools" / "crawl_idle_evidence.py"
ENV = {"SystemRoot": r"C:\Windows", "TEMP": r"C:\Windows\Temp", "TMP": r"C:\Windows\Temp", "PATH": r"C:\Windows\System32;C:\Windows", "PYTHONUTF8": "1", "PYTHONIOENCODING": "utf-8"}
FIXED = {"source": SOURCE, "interpreter": Path(PY), "parent_interpreter": Path(PY_SUP), "supervisor": HERE / "renewal_supervisor_v1.py", "entry": HERE / "repair_run_v1.py",
         "runner": HERE / "repair_phase_runner_v2.py"}
MARGIN_S, LEAD_S, AGE_S, CHECK_ONLY_CAP_S = 60, 90, 120, 120
APPROVAL_KEYS = ("APPROVED_PLAN_SHA256", "APPROVED_NOT_AFTER_UTC", "APPROVED_PLAN_DIR", "APPROVED_ENTRYPOINT_SHA256", "APPROVED_INVOCATION_SHA256")


def sha256_file(path) -> str:
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def parse_utc(text: str) -> dt.datetime:
    return dt.datetime.strptime(text, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=dt.timezone.utc)


def parse_approval(path: Path) -> dict[str, str]:
    found: dict[str, str] = {}
    for line in Path(path).read_text(encoding="utf-8").splitlines():
        m = re.match(r"^(APPROVED_[A-Z0-9_]+):\s*`?([^`\s]+)`?\s*$", line.strip())
        if m and m.group(1) in APPROVAL_KEYS:
            if m.group(1) in found:
                raise ValueError(f"approval_duplicate_key: {m.group(1)}")
            found[m.group(1)] = m.group(2)
    if set(found) != set(APPROVAL_KEYS):
        raise ValueError(f"approval_keys: {sorted(set(APPROVAL_KEYS) - set(found))}")
    return found


def renew_seconds(total_budget_s: float, n_plans: int) -> int:
    return int(math.ceil((total_budget_s + MARGIN_S + LEAD_S + AGE_S + n_plans * CHECK_ONLY_CAP_S) / 300) * 300)


def build(*, q0_receipt: Path, plans: dict[str, tuple[Path, Path]], windows: dict[str, str], run_root: Path = RUN) -> tuple[dict[str, Any], dict[str, Any]]:
    """(profile, freeze) thuan tu cac tep da co; nem ValueError neu bat ky rang buoc nao vi pham."""
    if set(plans) != {"clone", "init"}:
        raise ValueError("plans_must_be_clone_and_init")
    for k in ("not_before_utc", "start_deadline_utc", "hard_not_after_utc"):
        parse_utc(windows[k])
    if not (parse_utc(windows["not_before_utc"]) < parse_utc(windows["start_deadline_utc"]) <= parse_utc(windows["hard_not_after_utc"])):
        raise ValueError("windows_order")
    receipt = json.loads(Path(q0_receipt).read_text(encoding="utf-8"))
    if receipt.get("kind") != "q0_selftest_receipt" or not receipt.get("created_at_utc"):
        raise ValueError("q0_receipt_malformed")
    q0_sha = sha256_file(q0_receipt)
    out_plans: dict[str, Any] = {}
    total = 0.0
    docs: dict[str, dict[str, Any]] = {}
    for key, (plan_file, approval_file) in plans.items():
        plan = json.loads(Path(plan_file).read_text(encoding="utf-8"))
        doc = plan["document"]
        if doc.get("q0_receipt_sha256") != q0_sha:
            raise ValueError(f"plan_{key}_q0_receipt_mismatch")
        approval = parse_approval(approval_file)
        plan_dir = run_root / key / "plan"
        if approval["APPROVED_PLAN_SHA256"] != plan["plan_sha256"]:
            raise ValueError(f"approval_{key}_plan_sha256")
        if Path(approval["APPROVED_PLAN_DIR"]) != plan_dir:
            raise ValueError(f"approval_{key}_plan_dir")
        if approval["APPROVED_ENTRYPOINT_SHA256"] != sha256_file(FIXED["entry"]):
            raise ValueError(f"approval_{key}_entrypoint")
        if parse_utc(approval["APPROVED_NOT_AFTER_UTC"]) < parse_utc(windows["hard_not_after_utc"]):
            raise ValueError(f"approval_{key}_expires_before_hard_not_after")
        budget = float(doc["settings"]["total_budget_s"])
        total += budget
        docs[key] = doc
        out_plans[key] = {"plan_file": str(plan_file), "plan_file_sha256": sha256_file(plan_file), "plan_sha256": plan["plan_sha256"], "approval": str(approval_file), "approval_sha256": sha256_file(approval_file),
                          "invocation_sha256": approval["APPROVED_INVOCATION_SHA256"], "plan_dir": str(plan_dir), "launcher_logs": str(run_root / key / "logs"), "total_budget_s": budget}
    reports = sorted(out for spec in docs["init"]["specs"] if spec["name"].startswith("ph4_verify_") for out in spec.get("outputs", []))
    if len(reports) != 2 or any(Path(r).parent != run_root / "h2" / "reports" for r in reports):
        raise ValueError("init_verify_outputs_must_be_two_files_in_run_root_h2_reports")
    n = renew_seconds(total, len(out_plans))
    live = run_root / "live_crawl_idle_evidence.json"
    profile = {
        "kind": "renewal_publisher_profile", "plan": "heavy_ci", "plans": {k: v["plan_sha256"] for k, v in out_plans.items()}, "plan_total_budget_s": total,
        "source": {"path": str(SOURCE), "sha256": sha256_file(SOURCE)},
        "argv": [PY, str(SOURCE), "--out", str(live), "--renew-seconds", str(n), "--interval", "60", "--archive-dir", str(run_root / "evidence_archive" / "ci")],
        "cwd": str(PROJECT / "backend"), "env": ENV, "renew_seconds": n, "interval_s": 60,
        "formula": f"N = ceil((sum total_budget_s {total} + margin_s {MARGIN_S} + lead {LEAD_S} + evidence_age {AGE_S} + {len(out_plans)} x check_only_cap {CHECK_ONLY_CAP_S}) / 300) * 300",
        "start_before_plan_s": LEAD_S, "stdout_stderr": str(run_root / "ci_publisher.log"),
        "outputs": {"live": str(live), "archive_dir": str(run_root / "evidence_archive" / "ci"), "log": str(run_root / "ci_publisher.log")},
        "forbidden": ["ghi DB", "DDL", "KILL", "deploy", "crawl", "tu keo dai N", "khoi dong khi crawler busy hoac ngoai 17:00-00:20 VN"],
    }
    freeze = {
        "kind": "heavy_ci_freeze", "run_root": str(run_root), "windows": dict(windows),
        "q0_receipt": {"path": str(q0_receipt), "sha256": q0_sha, "created_at_utc": receipt["created_at_utc"]},
        "plans": out_plans, "h2_reports": reports,
        "pins": {name: {"path": str(path), "sha256": sha256_file(path)} for name, path in FIXED.items()},
    }
    return profile, freeze


def write_exclusive(path: Path, payload: Any) -> str:
    data = json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True).encode("utf-8")
    with open(path, "xb") as handle:
        handle.write(data)
    return hashlib.sha256(data).hexdigest()


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    for flag in ("q0-receipt", "clone-plan", "clone-approval", "init-plan", "init-approval"):
        parser.add_argument(f"--{flag}", type=Path, required=True)
    for flag in ("not-before-utc", "start-deadline-utc", "hard-not-after-utc"):
        parser.add_argument(f"--{flag}", required=True)
    parser.add_argument("--run-root", type=Path, default=RUN)
    args = parser.parse_args(argv)
    run_root = args.run_root
    try:
        profile, freeze = build(q0_receipt=args.q0_receipt, plans={"clone": (args.clone_plan, args.clone_approval), "init": (args.init_plan, args.init_approval)},
                                windows={"not_before_utc": args.not_before_utc, "start_deadline_utc": args.start_deadline_utc, "hard_not_after_utc": args.hard_not_after_utc}, run_root=run_root)
        run_root.mkdir(parents=True, exist_ok=True)
        profile_path, freeze_path = run_root / "renewal_profile_ci.json", run_root / "ci_freeze.json"
        if profile_path.exists() or freeze_path.exists() or any((run_root / k).exists() for k in ("clone", "init", "h2")):
            raise ValueError("run_root_not_fresh")
        profile_sha = write_exclusive(profile_path, profile)
        freeze["profile"] = {"path": str(profile_path), "sha256": profile_sha, "renew_seconds": profile["renew_seconds"]}
        freeze_sha = write_exclusive(freeze_path, freeze)
        for key in ("clone", "init"):
            (run_root / key / "plan").mkdir(parents=True, exist_ok=False)
    except (OSError, ValueError, KeyError) as exc:
        print(json.dumps({"ok": False, "error": f"{type(exc).__name__}: {str(exc)[:200]}"}))
        return 1
    print(json.dumps({"ok": True, "profile": str(profile_path), "profile_sha256": profile_sha, "renew_seconds": profile["renew_seconds"], "freeze": str(freeze_path), "freeze_sha256": freeze_sha}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

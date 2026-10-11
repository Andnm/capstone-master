"""THUAN (khong DB): dung **freeze manifest** + **renewal profile** cho workflow Q0 THAM SO (Q0-A / Q0-B: mot plan readonly mot phase `q0_selftest`, khong Q0 pin) tu cac TEP DA CO (plan + approval cua GPT). Tai dung cac ham
da duyet cua `repair_heavy_ci_freeze_v1` (parse approval, cong thuc N, ghi `xb`). Run-root chi nhan `outputs\\repair-run-q0[ab]-rNNN`. Tao `q0/plan` RONG (thu muc cha `q0` moi); Q0 receipt phai CHUA ton tai. KHONG chay publisher/entry, KHONG DB.

    python repair_q0var_freeze_v1.py --run-root <abs> --plan P --approval A --not-before-utc T --start-deadline-utc T --hard-not-after-utc T

N = ceil((total_budget_s + margin 60 + lead 90 + evidence_age 120 + 1 x check_only_cap 120) / 300) * 300 (= 1200 cho budget 600).
"""
from __future__ import annotations

import argparse
import hashlib
import json
import re
import types
from pathlib import Path
from typing import Any

HERE = Path(__file__).resolve().parent
PROJECT = HERE.parent
ROOT = PROJECT.parent
RUN_NAME_RE = re.compile(r"^repair-run-q0[ab]-r[0-9]{3}$")
OUTER_DEADLINE_S = 180.0


def _load(name: str, filename: str):
    path = HERE / filename
    data = path.read_bytes()
    module = types.ModuleType(name)
    module.__file__ = str(path)
    exec(compile(data, str(path), "exec"), module.__dict__)                                           # noqa: S102
    module.__source_sha256__ = hashlib.sha256(data).hexdigest()
    return module


ci = _load("repair_heavy_ci_freeze_for_q0var", "repair_heavy_ci_freeze_v1.py")
FIXED = {**ci.FIXED, "observer_module": HERE / "repair_db_observer_v1.py", "quiescence_model": HERE / "quiescence_model_v1.py", "q0_script": HERE / "repair_q0_v1.py"}


def check_run_root(run_root: Path) -> None:
    if not (Path(run_root).parent == ROOT / "outputs" and RUN_NAME_RE.match(Path(run_root).name)):
        raise ValueError("run_root_must_be_outputs_repair_run_q0a_or_q0b_rNNN")


def build(*, plan_file: Path, approval_file: Path, windows: dict[str, str], run_root: Path) -> tuple[dict[str, Any], dict[str, Any]]:
    check_run_root(run_root)
    for k in ("not_before_utc", "start_deadline_utc", "hard_not_after_utc"):
        ci.parse_utc(windows[k])
    if not (ci.parse_utc(windows["not_before_utc"]) < ci.parse_utc(windows["start_deadline_utc"]) <= ci.parse_utc(windows["hard_not_after_utc"])):
        raise ValueError("windows_order")
    plan = json.loads(Path(plan_file).read_text(encoding="utf-8"))
    doc = plan["document"]
    specs = doc["specs"]
    if len(specs) != 1 or specs[0].get("name") != "q0_selftest" or specs[0].get("kind") != "readonly" or specs[0].get("deadline_s") != OUTER_DEADLINE_S:
        raise ValueError("plan_must_be_one_readonly_q0_selftest_phase_with_outer_deadline_180")
    if doc.get("q0_receipt_sha256") is not None:
        raise ValueError("q0_plan_must_not_pin_a_q0_receipt")
    receipt = run_root / "q0" / "q0_receipt.json"
    live = run_root / "live_crawl_idle_evidence.json"
    spec = specs[0]
    if spec.get("outputs") != [str(receipt)] or spec["argv"][spec["argv"].index("--out") + 1] != str(receipt):
        raise ValueError("plan_paths_differ_from_run_root")
    approval = ci.parse_approval(approval_file)
    plan_dir = run_root / "q0" / "plan"
    if approval["APPROVED_PLAN_SHA256"] != plan["plan_sha256"]:
        raise ValueError("approval_plan_sha256")
    if Path(approval["APPROVED_PLAN_DIR"]) != plan_dir:
        raise ValueError("approval_plan_dir")
    if approval["APPROVED_ENTRYPOINT_SHA256"] != ci.sha256_file(FIXED["entry"]):
        raise ValueError("approval_entrypoint")
    if ci.parse_utc(approval["APPROVED_NOT_AFTER_UTC"]) < ci.parse_utc(windows["hard_not_after_utc"]):
        raise ValueError("approval_expires_before_hard_not_after")
    budget = float(doc["settings"]["total_budget_s"])
    n = ci.renew_seconds(budget, 1)
    profile = {
        "kind": "renewal_publisher_profile", "plan": "q0_selftest", "plans": {"q0": plan["plan_sha256"]}, "plan_total_budget_s": budget,
        "source": {"path": str(ci.SOURCE), "sha256": ci.sha256_file(ci.SOURCE)},
        "argv": [ci.PY, str(ci.SOURCE), "--out", str(live), "--renew-seconds", str(n), "--interval", "60", "--archive-dir", str(run_root / "evidence_archive" / "q0v")],
        "cwd": str(PROJECT / "backend"), "env": ci.ENV, "renew_seconds": n, "interval_s": 60,
        "formula": f"N = ceil((total_budget_s {budget} + margin_s {ci.MARGIN_S} + lead {ci.LEAD_S} + evidence_age {ci.AGE_S} + 1 x check_only_cap {ci.CHECK_ONLY_CAP_S}) / 300) * 300",
        "start_before_plan_s": ci.LEAD_S, "stdout_stderr": str(run_root / "q0v_publisher.log"),
        "outputs": {"live": str(live), "archive_dir": str(run_root / "evidence_archive" / "q0v"), "log": str(run_root / "q0v_publisher.log")},
        "forbidden": ["ghi DB", "DDL", "KILL", "deploy", "crawl", "tu keo dai N", "SET GLOBAL", "khoi dong khi crawler busy hoac ngoai 17:00-00:20 VN"],
    }
    freeze = {
        "kind": "q0_freeze", "run_root": str(run_root), "windows": dict(windows),
        "plans": {"q0": {"plan_file": str(plan_file), "plan_file_sha256": ci.sha256_file(plan_file), "plan_sha256": plan["plan_sha256"], "approval": str(approval_file), "approval_sha256": ci.sha256_file(approval_file),
                         "invocation_sha256": approval["APPROVED_INVOCATION_SHA256"], "plan_dir": str(plan_dir), "launcher_logs": str(run_root / "q0" / "logs"), "output": str(receipt), "total_budget_s": budget}},
        "pins": {name: {"path": str(path), "sha256": ci.sha256_file(path)} for name, path in FIXED.items()},
    }
    return profile, freeze


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--run-root", type=Path, required=True)
    parser.add_argument("--plan", type=Path, required=True)
    parser.add_argument("--approval", type=Path, required=True)
    for flag in ("not-before-utc", "start-deadline-utc", "hard-not-after-utc"):
        parser.add_argument(f"--{flag}", required=True)
    args = parser.parse_args(argv)
    run_root = args.run_root
    try:
        profile, freeze = build(plan_file=args.plan, approval_file=args.approval, run_root=run_root,
                                windows={"not_before_utc": args.not_before_utc, "start_deadline_utc": args.start_deadline_utc, "hard_not_after_utc": args.hard_not_after_utc})
        run_root.mkdir(parents=True, exist_ok=True)
        profile_path, freeze_path = run_root / "renewal_profile_q0v.json", run_root / "q0v_freeze.json"
        if profile_path.exists() or freeze_path.exists() or (run_root / "q0").exists():
            raise ValueError("run_root_not_fresh")
        profile_sha = ci.write_exclusive(profile_path, profile)
        freeze["profile"] = {"path": str(profile_path), "sha256": profile_sha, "renew_seconds": profile["renew_seconds"]}
        freeze_sha = ci.write_exclusive(freeze_path, freeze)
        (run_root / "q0" / "plan").mkdir(parents=True, exist_ok=False)
    except (OSError, ValueError, KeyError, IndexError) as exc:
        print(json.dumps({"ok": False, "error": f"{type(exc).__name__}: {str(exc)[:200]}"}))
        return 1
    print(json.dumps({"ok": True, "profile": str(profile_path), "profile_sha256": profile_sha, "renew_seconds": profile["renew_seconds"], "freeze": str(freeze_path), "freeze_sha256": freeze_sha}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

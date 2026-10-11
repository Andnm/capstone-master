"""THUAN (khong DB): dung **freeze manifest** + **renewal profile** cho workflow BUILD (mot plan `build` gom hai phase ghi, mot publisher) tu cac TEP DA CO (Q0 receipt moi, plan build, approval ngoai cua GPT, bang chung H2 that).
Tai dung cac ham da duyet cua `repair_heavy_ci_freeze_v1` (parse approval, cong thuc N, ghi `xb`). Run-root chi nhan `outputs\\repair-run-build-rNNN`; Q0 receipt chi nhan tu `outputs\\repair-run-q0[ab]-rNNN\\q0\\q0_receipt.json`.
Plan phai la **chinh plan ma `repair_heavy_plan_v1.build_plan` dung lai duoc tu bang chung H2 + Q0 receipt nay** (cung plan_sha256 va cung document) — nhu vay plan duoc GPT duyet gan dung voi chuoi H2 that.
Tao `build/plan` RONG; thu muc dataset phai CHUA ton tai. KHONG chay publisher/entry, KHONG DB.

    python repair_build_freeze_v1.py --run-root <abs> --q0-receipt R --plan P --approval A --h2-plan-dir D --h2-plan-sha256 H --report-dir D2 --prediction-receipt h1=PATH --prediction-receipt h3=PATH \\
        --not-before-utc T --start-deadline-utc T --hard-not-after-utc T [--python PY]

N = ceil((total_budget_s + margin 60 + lead 90 + evidence_age 120 + 1 x check_only_cap 120) / 300) * 300.
`q0_preceding_worst_case_s` = du tru toi da cac phase GHI TRUOC phase ghi cuoi (cong thuc `suggest_total_budget` cua builder) — operator dung de chan viec bat dau khi Q0 se het han truoc phase cuoi.
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
OUTPUTS = ROOT / "outputs"                                                                                # run-root/Q0 receipt chi nam duoi day (test tro sang thu muc tam)
RUN_NAME_RE = re.compile(r"^repair-run-build-r[0-9]{3}$")
Q0_RUN_NAME_RE = re.compile(r"^repair-run-q0[ab]-r[0-9]{3}$")
PHASES = ("ph5_build_h1", "ph5_build_h3")
Q0_AGE_MARGIN_S = 120


def _load(name: str, filename: str):
    path = HERE / filename
    data = path.read_bytes()
    module = types.ModuleType(name)
    module.__file__ = str(path)
    exec(compile(data, str(path), "exec"), module.__dict__)                                           # noqa: S102
    module.__source_sha256__ = hashlib.sha256(data).hexdigest()
    return module


ci = _load("repair_heavy_ci_freeze_for_build", "repair_heavy_ci_freeze_v1.py")
hp = _load("repair_heavy_plan_for_build_freeze", "repair_heavy_plan_v1.py")
FIXED = {**ci.FIXED, "observer_module": HERE / "repair_db_observer_v1.py", "quiescence_model": HERE / "quiescence_model_v1.py"}


def check_run_root(run_root: Path) -> None:
    if not (Path(run_root).parent == OUTPUTS and RUN_NAME_RE.match(Path(run_root).name)):
        raise ValueError("run_root_must_be_outputs_repair_run_build_rNNN")


def check_q0_path(q0_receipt: Path) -> None:
    path = Path(q0_receipt)
    if not (path.name == "q0_receipt.json" and path.parent.name == "q0" and Q0_RUN_NAME_RE.match(path.parent.parent.name) and path.parent.parent.parent == OUTPUTS):
        raise ValueError("q0_receipt_must_be_outputs_repair_run_q0a_or_q0b_rNNN_q0_q0_receipt_json")


def build(*, q0_receipt: Path, plan_file: Path, approval_file: Path, windows: dict[str, str], run_root: Path, h2_plan_dir: Path, h2_plan_sha256: str, report_dir: Path,
          prediction_receipts: dict[str, Path], python: str = ci.PY) -> tuple[dict[str, Any], dict[str, Any]]:
    check_run_root(run_root)
    check_q0_path(q0_receipt)
    for k in ("not_before_utc", "start_deadline_utc", "hard_not_after_utc"):
        ci.parse_utc(windows[k])
    if not (ci.parse_utc(windows["not_before_utc"]) < ci.parse_utc(windows["start_deadline_utc"]) <= ci.parse_utc(windows["hard_not_after_utc"])):
        raise ValueError("windows_order")
    receipt = json.loads(Path(q0_receipt).read_text(encoding="utf-8"))
    if receipt.get("kind") != "q0_selftest_receipt" or not receipt.get("created_at_utc"):
        raise ValueError("q0_receipt_malformed")
    ci.parse_utc(receipt["created_at_utc"])
    q0_sha = ci.sha256_file(q0_receipt)
    plan = json.loads(Path(plan_file).read_text(encoding="utf-8"))
    doc = plan["document"]
    specs = doc["specs"]
    if [s.get("name") for s in specs] != list(PHASES) or any(s.get("kind") != "write" for s in specs):
        raise ValueError("plan_must_be_the_two_write_phases_ph5_build_h1_and_h3")
    if doc.get("q0_receipt_sha256") != q0_sha:
        raise ValueError("plan_q0_receipt_mismatch")
    dataset_dirs = [out for s in specs for out in s.get("output_dirs", [])]
    if len(dataset_dirs) != 2 or any(Path(d).parent != hp.DATASET_ROOT for d in dataset_dirs) or any(Path(d).exists() for d in dataset_dirs):
        raise ValueError("dataset_dirs_must_be_two_new_directories_under_outputs_datasets")
    verify_reports = {key: Path(report_dir) / f"verify_{hp.VERSIONS[key][0]}.json" for key in ("h1", "h3")}
    rebuilt = hp.build_plan(python=python, q0_receipt=Path(q0_receipt), verify_reports=verify_reports, prediction_receipts={k: Path(v) for k, v in prediction_receipts.items()}, report_dir=Path(report_dir),
                            h2_plan_dir=Path(h2_plan_dir), h2_plan_sha256=h2_plan_sha256)                       # kiem luon bao cao verify + chuoi H2 that; nem ValueError neu khong dung
    if rebuilt["plan_sha256"] != plan["plan_sha256"] or rebuilt["document"] != doc:
        raise ValueError("plan_differs_from_the_plan_rebuilt_from_the_h2_evidence_and_this_q0_receipt")
    approval = ci.parse_approval(approval_file)
    plan_dir = run_root / "build" / "plan"
    if approval["APPROVED_PLAN_SHA256"] != plan["plan_sha256"]:
        raise ValueError("approval_plan_sha256")
    if Path(approval["APPROVED_PLAN_DIR"]) != plan_dir:
        raise ValueError("approval_plan_dir")
    if approval["APPROVED_ENTRYPOINT_SHA256"] != ci.sha256_file(FIXED["entry"]):
        raise ValueError("approval_entrypoint")
    if ci.parse_utc(approval["APPROVED_NOT_AFTER_UTC"]) < ci.parse_utc(windows["hard_not_after_utc"]):
        raise ValueError("approval_expires_before_hard_not_after")
    budget = float(doc["settings"]["total_budget_s"])
    max_age = float(doc["settings"]["q0_max_age_s"])
    _, overhead = hp._observer_facts()
    preceding = float(hp.suggest_total_budget(specs[:-1], doc["settings"], observer_overhead_s=overhead))
    if preceding + Q0_AGE_MARGIN_S >= max_age:
        raise ValueError("q0_age_budget_impossible_for_the_last_write_phase")
    n = ci.renew_seconds(budget, 1)
    live = run_root / "live_crawl_idle_evidence.json"
    profile = {
        "kind": "renewal_publisher_profile", "plan": "build", "plans": {"build": plan["plan_sha256"]}, "plan_total_budget_s": budget,
        "source": {"path": str(ci.SOURCE), "sha256": ci.sha256_file(ci.SOURCE)},
        "argv": [ci.PY, str(ci.SOURCE), "--out", str(live), "--renew-seconds", str(n), "--interval", "60", "--archive-dir", str(run_root / "evidence_archive" / "build")],
        "cwd": str(PROJECT / "backend"), "env": ci.ENV, "renew_seconds": n, "interval_s": 60,
        "formula": f"N = ceil((total_budget_s {budget} + margin_s {ci.MARGIN_S} + lead {ci.LEAD_S} + evidence_age {ci.AGE_S} + 1 x check_only_cap {ci.CHECK_ONLY_CAP_S}) / 300) * 300",
        "start_before_plan_s": ci.LEAD_S, "stdout_stderr": str(run_root / "bld_publisher.log"),
        "outputs": {"live": str(live), "archive_dir": str(run_root / "evidence_archive" / "build"), "log": str(run_root / "bld_publisher.log")},
        "forbidden": ["ghi DB ngoai plan da duyet", "DDL ngoai plan", "KILL", "deploy", "crawl", "tu keo dai N", "SET GLOBAL", "DROP", "xoa thu muc dau ra", "khoi dong khi crawler busy hoac ngoai 17:00-00:20 VN"],
    }
    reports = sorted(str(p) for p in verify_reports.values())
    freeze = {
        "kind": "build_freeze", "run_root": str(run_root), "windows": dict(windows),
        "q0_receipt": {"path": str(q0_receipt), "sha256": q0_sha, "created_at_utc": receipt["created_at_utc"]},
        "plans": {"build": {"plan_file": str(plan_file), "plan_file_sha256": ci.sha256_file(plan_file), "plan_sha256": plan["plan_sha256"], "approval": str(approval_file), "approval_sha256": ci.sha256_file(approval_file),
                            "invocation_sha256": approval["APPROVED_INVOCATION_SHA256"], "plan_dir": str(plan_dir), "launcher_logs": str(run_root / "build" / "logs"), "total_budget_s": budget,
                            "dataset_dirs": dataset_dirs, "q0_max_age_s": max_age, "q0_preceding_worst_case_s": preceding}},
        "h2": {"plan_dir": str(h2_plan_dir), "plan_sha256": h2_plan_sha256, "reports": reports},
        "pins": {name: {"path": str(path), "sha256": ci.sha256_file(path)} for name, path in FIXED.items()},
    }
    return profile, freeze


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--run-root", type=Path, required=True)
    parser.add_argument("--q0-receipt", type=Path, required=True)
    parser.add_argument("--plan", type=Path, required=True)
    parser.add_argument("--approval", type=Path, required=True)
    parser.add_argument("--h2-plan-dir", type=Path, required=True)
    parser.add_argument("--h2-plan-sha256", required=True)
    parser.add_argument("--report-dir", type=Path, required=True)
    parser.add_argument("--prediction-receipt", action="append", default=[], metavar="h1=PATH")
    parser.add_argument("--python", default=ci.PY)
    for flag in ("not-before-utc", "start-deadline-utc", "hard-not-after-utc"):
        parser.add_argument(f"--{flag}", required=True)
    args = parser.parse_args(argv)
    run_root = args.run_root
    try:
        predictions = dict(item.split("=", 1) for item in args.prediction_receipt)
        profile, freeze = build(q0_receipt=args.q0_receipt, plan_file=args.plan, approval_file=args.approval, run_root=run_root, h2_plan_dir=args.h2_plan_dir, h2_plan_sha256=args.h2_plan_sha256,
                                report_dir=args.report_dir, prediction_receipts={k: Path(v) for k, v in predictions.items()}, python=args.python,
                                windows={"not_before_utc": args.not_before_utc, "start_deadline_utc": args.start_deadline_utc, "hard_not_after_utc": args.hard_not_after_utc})
        run_root.mkdir(parents=True, exist_ok=True)
        profile_path, freeze_path = run_root / "renewal_profile_build.json", run_root / "build_freeze.json"
        if profile_path.exists() or freeze_path.exists() or (run_root / "build").exists():
            raise ValueError("run_root_not_fresh")
        profile_sha = ci.write_exclusive(profile_path, profile)
        freeze["profile"] = {"path": str(profile_path), "sha256": profile_sha, "renew_seconds": profile["renew_seconds"]}
        freeze_sha = ci.write_exclusive(freeze_path, freeze)
        (run_root / "build" / "plan").mkdir(parents=True, exist_ok=False)
    except (OSError, ValueError, KeyError, IndexError) as exc:
        print(json.dumps({"ok": False, "error": f"{type(exc).__name__}: {str(exc)[:200]}"}))
        return 1
    print(json.dumps({"ok": True, "profile": str(profile_path), "profile_sha256": profile_sha, "renew_seconds": profile["renew_seconds"], "freeze": str(freeze_path), "freeze_sha256": freeze_sha}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

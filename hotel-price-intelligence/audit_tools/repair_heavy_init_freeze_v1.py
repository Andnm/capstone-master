"""THUAN (khong DB): dung **freeze manifest** + **renewal profile** cho workflow HEAVY INIT-ONLY (mot plan `init` 4 phase, mot publisher; khac phuc su co 10/10) tu cac TEP DA CO (Q0 receipt moi, plan init, approval ngoai cua GPT,
prediction receipts, bang chung clone PASS + ho so quarantine cua `ds_20261009_nf1`). Tai dung cac ham da duyet cua `repair_heavy_ci_freeze_v1` (parse approval, cong thuc N, ghi `xb`). Run-root chi nhan
`outputs\\repair-run-heavy-init-rNNN`; Q0 receipt chi nhan tu `outputs\\repair-run-q0[ab]-rNNN\\q0\\q0_receipt.json`. Plan phai la **chinh plan ma `repair_heavy_plan_v1.init_plan` dung lai duoc tu Q0 receipt nay + 2 prediction
receipt + report-dir `<run-root>\\h2\\reports`** (cung plan_sha256 va cung document), nen plan GPT duyet gan dung voi cac dau vao that. Tao `init/plan` RONG; KHONG tao `h2` (operator tao ngay truoc invocation). KHONG DB.

    python repair_heavy_init_freeze_v1.py --run-root R --q0-receipt Q --plan P --approval A --prediction-receipt h1=PATH --prediction-receipt h3=PATH
        --clone-receipt C --clone-plan-bound B --quarantine-verify-report V --quarantine-init-receipt I --quarantine-verify-receipt W --quarantined-version ds_20261009_nf1
        --not-before-utc T --start-deadline-utc T --hard-not-after-utc T [--python PY]

N = ceil((total_budget_s + margin 60 + lead 90 + evidence_age 120 + 1 x check_only_cap 120) / 300) * 300 (= 1800 cho budget 1380).
`q0_preceding_worst_case_s` = du tru toi da cac phase TRUOC phase ghi cuoi (`init_h1`+`verify_h1`, cong thuc `suggest_total_budget`).
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
RUN_NAME_RE = re.compile(r"^repair-run-heavy-init-r[0-9]{3}$")
Q0_RUN_NAME_RE = re.compile(r"^repair-run-q0[ab]-r[0-9]{3}$")
PHASES = ("ph4_init_h1", "ph4_verify_h1", "ph4_init_h3", "ph4_verify_h3")
KINDS = ("write", "readonly", "write", "readonly")
Q0_AGE_MARGIN_S = 120


def _load(name: str, filename: str):
    path = HERE / filename
    data = path.read_bytes()
    module = types.ModuleType(name)
    module.__file__ = str(path)
    exec(compile(data, str(path), "exec"), module.__dict__)                                           # noqa: S102
    module.__source_sha256__ = hashlib.sha256(data).hexdigest()
    return module


ci = _load("repair_heavy_ci_freeze_for_heavy_init", "repair_heavy_ci_freeze_v1.py")
hp = _load("repair_heavy_plan_for_heavy_init_freeze", "repair_heavy_plan_v1.py")
FIXED = {**ci.FIXED, "observer_module": HERE / "repair_db_observer_v1.py", "quiescence_model": HERE / "quiescence_model_v1.py"}
DEPENDENCY_ARGS = ("clone_receipt", "clone_plan_bound", "quarantine_verify_report", "quarantine_init_receipt", "quarantine_verify_receipt")


def check_run_root(run_root: Path) -> None:
    if not (Path(run_root).parent == OUTPUTS and RUN_NAME_RE.match(Path(run_root).name)):
        raise ValueError("run_root_must_be_outputs_repair_run_heavy_init_rNNN")


def check_q0_path(q0_receipt: Path) -> None:
    path = Path(q0_receipt)
    if not (path.name == "q0_receipt.json" and path.parent.name == "q0" and Q0_RUN_NAME_RE.match(path.parent.parent.name) and path.parent.parent.parent == OUTPUTS):
        raise ValueError("q0_receipt_must_be_outputs_repair_run_q0a_or_q0b_rNNN_q0_q0_receipt_json")


def build(*, q0_receipt: Path, plan_file: Path, approval_file: Path, windows: dict[str, str], run_root: Path, prediction_receipts: dict[str, Path], dependencies: dict[str, Path],
          quarantined_version: str, python: str = ci.PY) -> tuple[dict[str, Any], dict[str, Any]]:
    check_run_root(run_root)
    check_q0_path(q0_receipt)
    for k in ("not_before_utc", "start_deadline_utc", "hard_not_after_utc"):
        ci.parse_utc(windows[k])
    if not (ci.parse_utc(windows["not_before_utc"]) < ci.parse_utc(windows["start_deadline_utc"]) <= ci.parse_utc(windows["hard_not_after_utc"])):
        raise ValueError("windows_order")
    if set(dependencies) != set(DEPENDENCY_ARGS) or not hp.VERSION_RE.match(quarantined_version):
        raise ValueError("dependencies_or_quarantined_version_malformed")
    if quarantined_version in {v for v, _ in hp.VERSIONS.values()}:
        raise ValueError("plan_versions_must_not_include_the_quarantined_version")
    receipt = json.loads(Path(q0_receipt).read_text(encoding="utf-8"))
    if receipt.get("kind") != "q0_selftest_receipt" or not receipt.get("created_at_utc"):
        raise ValueError("q0_receipt_malformed")
    ci.parse_utc(receipt["created_at_utc"])
    q0_sha = ci.sha256_file(q0_receipt)
    plan = json.loads(Path(plan_file).read_text(encoding="utf-8"))
    doc = plan["document"]
    specs = doc["specs"]
    if [s.get("name") for s in specs] != list(PHASES) or [s.get("kind") for s in specs] != list(KINDS):
        raise ValueError("plan_must_be_the_four_phases_init_verify_init_verify_with_write_readonly_kinds")
    if doc.get("q0_receipt_sha256") != q0_sha:
        raise ValueError("plan_q0_receipt_mismatch")
    report_dir = run_root / "h2" / "reports"
    versions = {key: hp.VERSIONS[key][0] for key in ("h1", "h3")}
    rebuilt = hp.init_plan(python=python, q0_receipt=Path(q0_receipt), prediction_receipts={k: Path(v) for k, v in prediction_receipts.items()}, report_dir=report_dir)
    if rebuilt["plan_sha256"] != plan["plan_sha256"] or rebuilt["document"] != doc:
        raise ValueError("plan_differs_from_the_plan_rebuilt_from_these_inputs")
    for name in DEPENDENCY_ARGS:                                                                          # bang chung phai TON TAI (SHA ghim vao freeze)
        if not Path(dependencies[name]).is_file():
            raise ValueError(f"dependency_missing: {name}")
    clone = json.loads(Path(dependencies["clone_receipt"]).read_text(encoding="utf-8"))
    if clone.get("phase") != "ph3_clone" or clone.get("verdict") != "PASS":
        raise ValueError("clone_receipt_is_not_a_pass")
    approval = ci.parse_approval(approval_file)
    plan_dir = run_root / "init" / "plan"
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
    last_write = max(i for i, k in enumerate(KINDS) if k == "write")
    preceding = float(hp.suggest_total_budget(specs[:last_write], doc["settings"], observer_overhead_s=overhead))
    if preceding + Q0_AGE_MARGIN_S >= max_age:
        raise ValueError("q0_age_budget_impossible_for_the_last_write_phase")
    n = ci.renew_seconds(budget, 1)
    live = run_root / "live_crawl_idle_evidence.json"
    profile = {
        "kind": "renewal_publisher_profile", "plan": "heavy_init", "plans": {"init": plan["plan_sha256"]}, "plan_total_budget_s": budget,
        "source": {"path": str(ci.SOURCE), "sha256": ci.sha256_file(ci.SOURCE)},
        "argv": [ci.PY, str(ci.SOURCE), "--out", str(live), "--renew-seconds", str(n), "--interval", "60", "--archive-dir", str(run_root / "evidence_archive" / "hi")],
        "cwd": str(PROJECT / "backend"), "env": ci.ENV, "renew_seconds": n, "interval_s": 60,
        "formula": f"N = ceil((total_budget_s {budget} + margin_s {ci.MARGIN_S} + lead {ci.LEAD_S} + evidence_age {ci.AGE_S} + 1 x check_only_cap {ci.CHECK_ONLY_CAP_S}) / 300) * 300",
        "start_before_plan_s": ci.LEAD_S, "stdout_stderr": str(run_root / "hi_publisher.log"),
        "outputs": {"live": str(live), "archive_dir": str(run_root / "evidence_archive" / "hi"), "log": str(run_root / "hi_publisher.log")},
        "forbidden": ["ghi DB ngoai plan da duyet", "DDL ngoai plan", "KILL", "deploy", "crawl", "tu keo dai N", "SET GLOBAL", "DROP", "clone", "build", "khoi dong khi crawler busy hoac ngoai 17:00-00:20 VN"],
    }
    freeze = {
        "kind": "heavy_init_freeze", "run_root": str(run_root), "windows": dict(windows),
        "q0_receipt": {"path": str(q0_receipt), "sha256": q0_sha, "created_at_utc": receipt["created_at_utc"]},
        "plans": {"init": {"plan_file": str(plan_file), "plan_file_sha256": ci.sha256_file(plan_file), "plan_sha256": plan["plan_sha256"], "approval": str(approval_file), "approval_sha256": ci.sha256_file(approval_file),
                           "invocation_sha256": approval["APPROVED_INVOCATION_SHA256"], "plan_dir": str(plan_dir), "launcher_logs": str(run_root / "init" / "logs"), "total_budget_s": budget,
                           "q0_max_age_s": max_age, "q0_preceding_worst_case_s": preceding, "target_database": hp.TARGET_DB, "versions": versions,
                           "h2_reports": [str(report_dir / f"verify_{versions[k]}.json") for k in ("h1", "h3")]}},
        "dependencies": {name: {"path": str(dependencies[name]), "sha256": ci.sha256_file(dependencies[name])} for name in DEPENDENCY_ARGS},
        "quarantined_version": quarantined_version,
        "pins": {name: {"path": str(path), "sha256": ci.sha256_file(path)} for name, path in FIXED.items()},
    }
    return profile, freeze


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--run-root", type=Path, required=True)
    parser.add_argument("--q0-receipt", type=Path, required=True)
    parser.add_argument("--plan", type=Path, required=True)
    parser.add_argument("--approval", type=Path, required=True)
    parser.add_argument("--prediction-receipt", action="append", default=[], metavar="h1=PATH")
    for name in DEPENDENCY_ARGS:
        parser.add_argument(f"--{name.replace('_', '-')}", type=Path, required=True)
    parser.add_argument("--quarantined-version", required=True)
    parser.add_argument("--python", default=ci.PY)
    for flag in ("not-before-utc", "start-deadline-utc", "hard-not-after-utc"):
        parser.add_argument(f"--{flag}", required=True)
    args = parser.parse_args(argv)
    run_root = args.run_root
    try:
        predictions = dict(item.split("=", 1) for item in args.prediction_receipt)
        profile, freeze = build(q0_receipt=args.q0_receipt, plan_file=args.plan, approval_file=args.approval, run_root=run_root, prediction_receipts={k: Path(v) for k, v in predictions.items()}, python=args.python,
                                dependencies={name: getattr(args, name) for name in DEPENDENCY_ARGS}, quarantined_version=args.quarantined_version,
                                windows={"not_before_utc": args.not_before_utc, "start_deadline_utc": args.start_deadline_utc, "hard_not_after_utc": args.hard_not_after_utc})
        run_root.mkdir(parents=True, exist_ok=True)
        profile_path, freeze_path = run_root / "renewal_profile_hi.json", run_root / "hi_freeze.json"
        if profile_path.exists() or freeze_path.exists() or (run_root / "init").exists() or (run_root / "h2").exists():
            raise ValueError("run_root_not_fresh")
        profile_sha = ci.write_exclusive(profile_path, profile)
        freeze["profile"] = {"path": str(profile_path), "sha256": profile_sha, "renew_seconds": profile["renew_seconds"]}
        freeze_sha = ci.write_exclusive(freeze_path, freeze)
        (run_root / "init" / "plan").mkdir(parents=True, exist_ok=False)
    except (OSError, ValueError, KeyError, IndexError) as exc:
        print(json.dumps({"ok": False, "error": f"{type(exc).__name__}: {str(exc)[:200]}"}))
        return 1
    print(json.dumps({"ok": True, "profile": str(profile_path), "profile_sha256": profile_sha, "renew_seconds": profile["renew_seconds"], "freeze": str(freeze_path), "freeze_sha256": freeze_sha}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

"""ENTRY POINT CUOI cho ke hoach repair: approval (APPROVED_PLAN_SHA256) -> plan.json -> Q0 receipt (neu co phase `write`) -> cong that -> Job Object launcher -> DbObserver -> `Runner.run_plan()`.
Moi thanh phan o day la THAT (nap tu byte da bam); chi test moi tiem fake qua `overrides`. Khong co cach nao truyen spec/hash/settings qua CLI: ke hoach CHI den tu `plan.json`, va `Runner` tu tinh lai plan hash
roi so voi hash trong approval — sua `plan.json` (hoac approval) => REFUSED truoc gate/baseline/launch.

TRANG THAI: THIET KE + MA + TEST voi fake; KHONG quyen thuc thi; chua co approval nao. `--check-only` kiem tra moi precondition (hash, tooling, Q0, thu muc) KHONG ghi gi.
Ma thoat: 0 moi phase PASS; 1 FAIL_CLEAN; 3 REFUSED; 4 QUARANTINE; 2 loi dau vao/approval/plan/Q0 (khong chay gi).
"""
from __future__ import annotations

import argparse
import datetime as dt
import hashlib
import json
import os
import re
import sys
import time
import types
from pathlib import Path
from typing import Any, Mapping

HERE = Path(__file__).resolve().parent
DISCUSS_DIR = HERE.parent.parent / "discuss" / "model-results-improvement-20261008"                    # noi DUY NHAT chap nhan approval that (GPT 76 §4)
REAL_APPROVAL_NAME_RE = re.compile(r"^\d+[a-z]?-gpt-[A-Za-z0-9._-]+\.md$")
FAKE_APPROVAL_WORD_RE = re.compile(r"draft|stand-?in|proposal|template", re.IGNORECASE)


def _load(name: str, filename: str):
    path = HERE / filename
    data = path.read_bytes()
    module = types.ModuleType(name)
    module.__file__ = str(path)
    exec(compile(data, str(path), "exec"), module.__dict__)                                           # noqa: S102
    module.__source_sha256__ = hashlib.sha256(data).hexdigest()
    return module


rr = _load("repair_phase_runner_v2_for_run", "repair_phase_runner_v2.py")
EXIT = {rr.PASS: 0, rr.FAIL_CLEAN: 1, rr.REFUSED: 3, rr.QUARANTINE: 4}


class SystemClock:
    def monotonic(self) -> float:
        return time.monotonic()

    def sleep(self, seconds: float) -> None:
        time.sleep(seconds)

    def now_utc(self) -> dt.datetime:
        return dt.datetime.now(dt.timezone.utc)


def _norm(path: Any) -> str:
    return os.path.normcase(str(Path(path).resolve()))


def real_approval_problem(path: Any, discuss_dir: Any) -> str | None:
    """Approval THAT = tep `NN-gpt-*.md` nam TRUC TIEP trong thu muc thao luan (khong thu muc con, khong ten NHAP/STAND-IN/proposal); khac => ly do (None = hop le). Khong phai xac thuc tac gia."""
    try:
        resolved = Path(path).resolve(strict=True)
    except OSError:
        return "approval_file_missing"
    if _norm(resolved.parent) != _norm(discuss_dir):
        return "approval_not_in_discussion_directory"
    if not REAL_APPROVAL_NAME_RE.match(resolved.name) or FAKE_APPROVAL_WORD_RE.search(resolved.name):
        return "approval_name_not_a_numbered_gpt_md"
    return None


def invocation_sha256(args: argparse.Namespace) -> str:
    """Hash canonical cua lenh goi (duong dan tuyet doi da resolve cua plan/plan-dir/launcher-logs/crawl-evidence/q0-receipt): doi bat ky duong dan dau ra/bang chung nao sau duyet => approval vo hieu."""
    paths = {"plan": args.plan, "plan_dir": args.plan_dir, "launcher_logs": args.launcher_logs, "crawl_evidence": args.crawl_evidence, "q0_receipt": args.q0_receipt}
    canonical = {key: (_norm(value) if value is not None else None) for key, value in paths.items()}
    return hashlib.sha256(json.dumps(canonical, sort_keys=True, separators=(",", ":")).encode("utf-8")).hexdigest()


def entrypoint_sha256() -> str:
    return hashlib.sha256(Path(__file__).read_bytes()).hexdigest()


def inner_approval_problems(doc: Mapping[str, Any], discuss_dir: Any) -> list[str]:
    """Moi `--gpt-approval <path>` trong argv cua spec (approval cua producer) cung phai la approval that."""
    problems: list[str] = []
    for spec in doc["specs"]:
        argv = spec.get("argv", [])
        for index, token in enumerate(argv):
            if token == "--gpt-approval":
                problem = real_approval_problem(argv[index + 1], discuss_dir) if index + 1 < len(argv) else "approval_path_missing"
                if problem:
                    problems.append(f"{spec.get('name')}: inner_{problem}")
    return problems


def _fail(code: int, message: str, **extra: Any) -> int:
    print(json.dumps({"ok": False, "error": message, **extra}, ensure_ascii=False, sort_keys=True))
    return code


def load_plan_file(path: Path) -> dict[str, Any]:
    """Doc plan.json (do `repair_plan_v1.emit` ghi); hash ghi trong tep PHAI khop hash tinh lai tu `document` (tep tu-nhat-quan), nhung quyen quyet dinh la approval."""
    data = json.loads(Path(path).read_text(encoding="utf-8"))
    doc = data.get("document") if isinstance(data, dict) else None
    if not isinstance(doc, dict) or set(doc) != {"plan_version", "specs", "settings", "expected_tooling", "q0_receipt_sha256"} or doc.get("plan_version") != 2:
        raise ValueError("plan_file_malformed")
    recomputed = rr.plan_sha256(doc["specs"], doc["settings"], doc["expected_tooling"], doc["q0_receipt_sha256"])
    if data.get("plan_sha256") != recomputed:
        raise ValueError("plan_file_hash_inconsistent_with_its_document")
    return data


WORKER_MAX_STATEMENTS = 200                                                                                # tran cua `repair_observer_worker_v1` (--max-statements 1..200)
BASELINE_SELECTS, PROBE_SELECTS = 4, 3                                                                    # DbObserver.capture_baseline = 4 SELECT; probe = UPTIME + SESSIONS + ROLLING_BACK = 3 SELECT


def observer_statement_budget(doc: Mapping[str, Any]) -> int:
    """Tran so SELECT cua observer CHO CA KE HOACH (bo dem cua DbObserver khong reset giua cac phase): moi phase `write` = 1 baseline + toi da `max_probes` probe. Suy ra tu plan (duoc approval rang buoc)."""
    write_phases = sum(1 for s in doc["specs"] if isinstance(s, Mapping) and s.get("kind") == "write")
    return write_phases * (BASELINE_SELECTS + PROBE_SELECTS * doc["settings"]["max_probes"])


def build_components(args: argparse.Namespace, doc: Mapping[str, Any], plan_has_write: bool, overrides: Mapping[str, Any], *, create_dirs: bool = True) -> dict[str, Any]:
    """Thanh phan THAT, tru khi `overrides` (chi test) thay the. Observer chi tao khi co phase `write` va LA `BoundedObserver` (worker rieng trong Job Object, moi lenh co cap cung tu settings cua plan).
    `create_dirs=False` (--check-only): KHONG tao thu muc nao."""
    components: dict[str, Any] = {}
    components["clock"] = overrides.get("clock") or SystemClock()
    if "gate" in overrides:
        components["gate"] = overrides["gate"]
    else:
        components["gate"], _ = rr.make_evidence_gate(args.crawl_evidence)
    if "launcher" in overrides:
        components["launcher"] = overrides["launcher"]
    else:
        if create_dirs:
            args.launcher_logs.mkdir(parents=True, exist_ok=True)
        components["launcher"] = rr.load_job_launcher_class()(args.launcher_logs)
    if "observer" in overrides:
        components["observer"] = overrides["observer"]
    elif plan_has_write:
        proxy = _load("repair_observer_proxy_for_run", "repair_observer_proxy_v1.py")
        first_write = next(s for s in doc["specs"] if s["kind"] == "write")                                  # interpreter + env cua worker = chinh cai da ghim trong spec phase write dau tien
        settings = doc["settings"]
        argv = list(overrides.get("observer_worker_argv") or [first_write["argv"][0], str(HERE / "repair_observer_worker_v1.py"), "--max-statements", str(observer_statement_budget(doc))])
        if create_dirs:
            args.launcher_logs.mkdir(parents=True, exist_ok=True)
        components["observer"] = proxy.BoundedObserver(argv, cwd=str(overrides.get("observer_cwd") or (HERE.parent / "backend")), env=first_write["env"], stderr_path=args.launcher_logs / "observer_worker.stderr",
                                                       caps={"start": 20.0, "baseline": settings["baseline_cap_s"], "probe": settings["probe_cap_s"], "selftest": 120.0, "close": 2.0})
    else:
        components["observer"] = None
    return components


def main(argv: list[str] | None = None, *, overrides: Mapping[str, Any] | None = None) -> int:
    overrides = overrides or {}
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--approval", type=Path, required=True, help="approval THAT `NN-gpt-*.md` trong thu muc thao luan: nam dong APPROVED_PLAN_SHA256/NOT_AFTER_UTC/PLAN_DIR/ENTRYPOINT_SHA256/INVOCATION_SHA256")
    parser.add_argument("--plan", type=Path, required=True, help="plan.json do repair_plan_v1 ghi")
    parser.add_argument("--plan-dir", type=Path, required=True, help="thu muc receipt: phai ton tai va RONG")
    parser.add_argument("--launcher-logs", type=Path, required=True, help="thu muc stdout/stderr cua child (tao neu chua co)")
    parser.add_argument("--crawl-evidence", type=Path, required=True)
    parser.add_argument("--q0-receipt", type=Path, default=None, help="bat buoc neu ke hoach co phase `write`")
    parser.add_argument("--check-only", action="store_true", help="kiem tra precondition, KHONG ghi gi, KHONG chay")
    args = parser.parse_args(argv)
    clock_for_time = overrides.get("clock") or SystemClock()
    now_fn = overrides.get("now_utc") or clock_for_time.now_utc
    discuss_dir = overrides.get("discuss_dir") or DISCUSS_DIR
    own_sha = entrypoint_sha256()
    invocation = invocation_sha256(args)
    try:
        approval_text = args.approval.read_text(encoding="utf-8")
    except OSError as exc:
        return _fail(2, f"approval_unreadable: {type(exc).__name__}")
    approval_problems: list[str] = []
    problem = real_approval_problem(args.approval, discuss_dir)
    if problem:
        approval_problems.append(problem)
    outer: dict[str, Any] | None = None
    try:
        outer = rr.parse_outer_approval(approval_text)
    except ValueError as exc:
        approval_problems.append(str(exc)[:160])
    if outer is not None:
        if outer["entrypoint_sha256"] != own_sha:
            approval_problems.append("approval_entrypoint_sha256_mismatch")
        if outer["invocation_sha256"] != invocation:
            approval_problems.append("approval_invocation_sha256_mismatch")
        if _norm(outer["plan_dir"]) != _norm(args.plan_dir):
            approval_problems.append("approval_plan_dir_mismatch")
        if outer["not_after_utc"] <= now_fn():
            approval_problems.append("approval_expired")
    if approval_problems and not args.check_only:
        return _fail(2, "approval_rejected", problems=approval_problems)                                    # TRUOC moi thanh phan/thu muc/worker
    try:
        approved = outer["plan_sha256"] if outer is not None else rr.parse_approved_plan(approval_text)
    except ValueError as exc:
        approved = ""
        approval_problems.append(f"approval_plan_hash_unreadable: {str(exc)[:80]}")
    try:
        plan = load_plan_file(args.plan)
    except (OSError, ValueError) as exc:
        return _fail(2, f"approval_or_plan_unreadable: {type(exc).__name__}: {str(exc)[:120]}")
    doc = plan["document"]
    inner_problems = inner_approval_problems(doc, discuss_dir)
    if inner_problems and not args.check_only:
        return _fail(2, "inner_approval_rejected", problems=inner_problems)
    settings_errors = rr.validate_settings(doc["settings"])
    if settings_errors:
        return _fail(2, f"plan_settings_invalid: {settings_errors}")
    has_write = any(isinstance(s, Mapping) and s.get("kind") == "write" for s in doc["specs"])
    if has_write and observer_statement_budget(doc) > WORKER_MAX_STATEMENTS:
        return _fail(2, f"observer_statement_budget_exceeds_worker_limit: {observer_statement_budget(doc)} > {WORKER_MAX_STATEMENTS}")
    q0 = None
    if has_write:
        if args.q0_receipt is None or doc["q0_receipt_sha256"] is None:
            return _fail(2, "write_plan_requires_q0_receipt_and_a_q0_pin_in_the_plan")
        observer_module = _load("repair_db_observer_for_run_check", "repair_db_observer_v1.py")
        try:
            q0 = rr.load_q0_receipt(args.q0_receipt, expected_receipt_sha256=doc["q0_receipt_sha256"], expected_observer_sha256=observer_module.script_sha256(),
                                    expected_sql_set_sha256=observer_module.sql_set_sha256(), expected_model_sha256=rr.qm.__source_sha256__, now_utc=now_fn(), max_age_s=doc["settings"]["q0_max_age_s"])
        except (OSError, ValueError) as exc:
            return _fail(2, f"q0_receipt_rejected: {type(exc).__name__}: {str(exc)[:120]}")
    components = build_components(args, doc, has_write, overrides, create_dirs=not args.check_only)
    runner = rr.Runner(plan_dir=args.plan_dir, specs=doc["specs"], settings=doc["settings"], expected_tooling=doc["expected_tooling"], q0_receipt_sha256=doc["q0_receipt_sha256"],
                       approved_plan_sha256=approved, launcher=components["launcher"], clock=components["clock"], now_utc=now_fn, gate=components["gate"], observer=components["observer"], q0=q0,
                       not_after_utc=outer["not_after_utc"] if outer is not None else None, entrypoint_sha256=own_sha)
    if args.check_only:
        problems = [*approval_problems, *inner_problems, *_check_only(runner, doc, approved, args.plan_dir)]
        print(json.dumps({"ok": not problems, "problems": problems, "computed_plan_sha256": runner.computed_plan_sha or rr.plan_sha256(doc["specs"], doc["settings"], doc["expected_tooling"], doc["q0_receipt_sha256"]),
                          "computed_invocation_sha256": invocation, "entrypoint_sha256": own_sha}, ensure_ascii=False, sort_keys=True))
        return 0 if not problems else 3
    receipts = runner.run_plan()
    summary = {"ok": all(r["verdict"] == rr.PASS for r in receipts) and len(receipts) == len(doc["specs"]), "approved_plan_sha256": approved, "computed_plan_sha256": runner.computed_plan_sha,
               "entrypoint_sha256": own_sha, "invocation_sha256": invocation,
               "phases": [{"phase": r.get("phase"), "verdict": r["verdict"], "reasons": r.get("reasons"), "receipt_path": r.get("receipt_path"), "receipt_sha256": r.get("receipt_sha256")} for r in receipts]}
    print(json.dumps(summary, ensure_ascii=False, sort_keys=True, default=str))
    worst = max((EXIT[r["verdict"]] for r in receipts), default=3)
    return 0 if summary["ok"] else (worst or 1)


def _check_only(runner, doc: Mapping[str, Any], approved: str, plan_dir: Path) -> list[str]:
    """Precondition KHONG ghi gi: dung cac ham thuan cua runner (khong goi _preflight vi no ghi PLAN_BOUND)."""
    problems: list[str] = []
    errors = rr.validate_settings(doc["settings"])
    problems += [f"settings_invalid: {errors}"] if errors else []
    for index, spec in enumerate(doc["specs"]):
        errors = rr.validate_spec(spec)
        problems += [f"spec_invalid[{index}]: {errors}"] if errors else []
    computed = rr.plan_sha256(doc["specs"], doc["settings"], doc["expected_tooling"], doc["q0_receipt_sha256"])
    if computed != approved:
        problems.append("plan_sha256_not_approved")
    if runner.tooling() != dict(doc["expected_tooling"]):
        problems.append("tooling_not_approved")
    if not plan_dir.is_dir() or any(plan_dir.iterdir()):
        problems.append("plan_dir_missing_or_not_empty")
    for spec in doc["specs"]:
        for path, expected in spec.get("required_files", {}).items():
            try:
                if rr.file_sha256(path) != expected:
                    problems.append(f"required_file_hash_mismatch: {spec['name']}: {path}")
            except OSError:
                problems.append(f"required_file_unreadable: {spec['name']}: {path}")
    ok, reasons = runner.gate()
    if not ok:
        problems.append(f"gate_closed: {reasons}")
    return problems


if __name__ == "__main__":
    raise SystemExit(main())

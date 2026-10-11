"""THUAN (khong DB, khong chay): dung KE HOACH `readonly` MOT PHASE cho probe InnoDB (`repair_innodb_probe_v1.py`) theo GPT 104: outer Job deadline 30.0 s BAT BUOC (hard bound la outer runner, khong phai watchdog trong),
output JSON tuyet doi MOI, required_files ghim nguon + phu thuoc (gate `warehouse_audit_v3.py` + closure Settings), cwd backend, env 6 bien. Khong Q0 (plan readonly, `q0_receipt_sha256 = null`, nhu Ph1/Q0).

    python repair_innodb_probe_plan_v1.py --out-dir <thu muc moi> --python <abs python> --run-root <abs run-root> [--crawl-evidence <abs live json>]

Moi gia tri la tham so tuong minh; doi bat ky gia tri nao => plan hash doi => approval cu vo hieu. Ban nhap approval KHONG phai authority.
"""
from __future__ import annotations

import argparse
import json
import math
import ntpath
from pathlib import Path
from typing import Any

import types
import hashlib

HERE = Path(__file__).resolve().parent
PROJECT = HERE.parent
BACKEND = PROJECT / "backend"
OUTER_DEADLINE_S = 30.0
TOTAL_SLACK_S = 120.0


def _load(name: str, filename: str):
    path = HERE / filename
    data = path.read_bytes()
    module = types.ModuleType(name)
    module.__file__ = str(path)
    exec(compile(data, str(path), "exec"), module.__dict__)                                           # noqa: S102
    module.__source_sha256__ = hashlib.sha256(data).hexdigest()
    return module


rp = _load("repair_plan_v1_for_probe_plan", "repair_plan_v1.py")
rr = rp.rr


def probe_out_path(run_root: Path) -> Path:
    return Path(run_root) / "probe" / "innodb_probe_report.json"


def probe_spec(*, python: str, run_root: Path, crawl_evidence: Path) -> dict[str, Any]:
    for label, value in (("python", python), ("run_root", str(run_root)), ("crawl_evidence", str(crawl_evidence))):
        if not ntpath.isabs(value) or "/" in value:
            raise ValueError(f"{label}_must_be_absolute_backslash_path: {value!r}")
    script = HERE / "repair_innodb_probe_v1.py"
    out = probe_out_path(run_root)
    argv = [python, str(script), "--crawl-evidence", str(crawl_evidence), "--out", str(out)]
    closure = [script, HERE / "warehouse_audit_v3.py", *rp.backend_closure(BACKEND)]
    return {"name": "innodb_probe", "kind": "readonly", "argv": argv, "cwd": str(BACKEND), "env": rp.child_env(), "required_files": rp.pins_for([python, *closure]), "deadline_s": OUTER_DEADLINE_S, "outputs": [str(out)]}


def suggest_total_budget(spec: dict[str, Any], settings: dict[str, Any]) -> float:
    """Cung cong thuc nhu tool heavy cho phase readonly: margin + slack + deadline + du tru sau launch (khong baseline/observer vi khong ghi), lam tron len 60 s."""
    total = settings["margin_s"] + TOTAL_SLACK_S + spec["deadline_s"] + rr.reserve_after_launch(spec, settings)
    return float(math.ceil(total / 60.0) * 60.0)


def build(*, python: str, run_root: Path, crawl_evidence: Path) -> dict[str, Any]:
    spec = probe_spec(python=python, run_root=run_root, crawl_evidence=crawl_evidence)
    base = dict(rr.DEFAULT_SETTINGS)
    settings = {**base, "total_budget_s": suggest_total_budget(spec, base)}
    return rp.build_plan([spec], settings, q0_receipt_sha256=None, launcher_source_sha256=rr.file_sha256(HERE / "windows_job_v1.py"), observer_tooling_sha256=None)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--out-dir", type=Path, required=True)
    parser.add_argument("--python", required=True)
    parser.add_argument("--run-root", type=Path, required=True)
    parser.add_argument("--crawl-evidence", type=Path, default=None)
    args = parser.parse_args(argv)
    evidence = args.crawl_evidence or (args.run_root / "live_crawl_idle_evidence.json")
    try:
        plan = build(python=args.python, run_root=args.run_root, crawl_evidence=evidence)
        written = rp.emit(args.out_dir, "innodb_probe", plan, "Probe InnoDB chi-doc (mot phase)")
    except (ValueError, OSError) as exc:
        print(json.dumps({"ok": False, "error": f"{type(exc).__name__}: {str(exc)[:200]}"}))
        return 1
    print(json.dumps({"ok": True, "plan_sha256": plan["plan_sha256"], "total_budget_s": plan["document"]["settings"]["total_budget_s"], "files": written}, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

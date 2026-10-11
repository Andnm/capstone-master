"""THUAN (khong DB, khong chay): dung KE HOACH Q0 (self-test server, `readonly` mot phase `q0_selftest`) voi run-root THAM SO — de lay Q0 receipt TUOI (Q0-A cho clone+init, Q0-B cho build). Dung dung `repair_plan_v1.q0_spec`
+ `build_plan` + settings `total_budget_s 600` nhu plan Q0 da duoc review/chay ngay 09/10 (plan `6c5a18fe…`): voi run-root `repair-run-20261009` builder nay phai TAI TAO dung hash do (test). Khong Q0 pin (`q0_receipt_sha256 null`).

    python repair_q0_plan_param_v1.py --out-dir <thu muc moi> --python <abs python> --run-root <abs run-root>

Output receipt nam o `<run-root>\\q0\\q0_receipt.json` (phai chua ton tai). Ban nhap approval KHONG phai authority.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import ntpath
import types
from pathlib import Path
from typing import Any

HERE = Path(__file__).resolve().parent
PROJECT = HERE.parent
BACKEND = PROJECT / "backend"
TOTAL_BUDGET_S = 600.0


def _load(name: str, filename: str):
    path = HERE / filename
    data = path.read_bytes()
    module = types.ModuleType(name)
    module.__file__ = str(path)
    exec(compile(data, str(path), "exec"), module.__dict__)                                           # noqa: S102
    module.__source_sha256__ = hashlib.sha256(data).hexdigest()
    return module


rp = _load("repair_plan_v1_for_q0_param", "repair_plan_v1.py")
rr = rp.rr


def q0_receipt_path(run_root: Path) -> Path:
    return Path(run_root) / "q0" / "q0_receipt.json"


def build(*, python: str, run_root: Path) -> dict[str, Any]:
    for label, value in (("python", python), ("run_root", str(run_root))):
        if not ntpath.isabs(value) or "/" in value:
            raise ValueError(f"{label}_must_be_absolute_backslash_path: {value!r}")
    spec = rp.q0_spec(python=python, backend=BACKEND, out_receipt=q0_receipt_path(run_root))
    settings = {**rr.DEFAULT_SETTINGS, "total_budget_s": TOTAL_BUDGET_S}
    return rp.build_plan([spec], settings, q0_receipt_sha256=None, launcher_source_sha256=rr.file_sha256(HERE / "windows_job_v1.py"))


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--out-dir", type=Path, required=True)
    parser.add_argument("--python", required=True)
    parser.add_argument("--run-root", type=Path, required=True)
    args = parser.parse_args(argv)
    try:
        plan = build(python=args.python, run_root=args.run_root)
        written = rp.emit(args.out_dir, "q0", plan, "Q0 self-test server (chi doc), run-root tham so")
    except (ValueError, OSError) as exc:
        print(json.dumps({"ok": False, "error": f"{type(exc).__name__}: {str(exc)[:200]}"}))
        return 1
    print(json.dumps({"ok": True, "plan_sha256": plan["plan_sha256"], "total_budget_s": TOTAL_BUDGET_S, "files": written}, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

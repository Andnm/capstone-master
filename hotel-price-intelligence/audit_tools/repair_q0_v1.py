"""Q0 — self-test server CHI DOC cho quiescence (ghi Q0 receipt). Chay DUOI `repair_phase_runner_v2` (argv/hash/containment/receipt cua runner), KHONG tu chay.

Lam gi: mo 1 ket noi probe + 2 ket noi phu (cung user, DB=NULL) bang ADAPTER CHI-DOC cua observer (`readonly_connect_factory` tren `_connect_kwargs(None)` cua backend; DB credentials do `backend/.env` o cwd),
goi `DbObserver.selftest()` (toan SELECT co MAX_EXECUTION_TIME; tran `--max-statements` <= 40 chi dem lenh cua observer, KHONG dem cac lenh SET/SELECT khoi tao ket noi — xem docstring adapter),
ghi Q0 receipt `xb` CHI KHI moi khoa True va ids/databases/app_user hop le. Khong ghi DB, khong DDL, khong KILL, khong luu text SQL cua phien khac.

TRANG THAI: THIET KE + MA + TEST voi ket noi GIA (`connect_factory`); KHONG co quyen thuc thi; chua ai duyet cau SQL cua observer.
"""
from __future__ import annotations

import argparse
import contextlib
import datetime as dt
import hashlib
import json
import math
import sys
import types
from pathlib import Path

HERE = Path(__file__).resolve().parent


def _load(name: str, filename: str):
    path = HERE / filename
    data = path.read_bytes()
    module = types.ModuleType(name)
    module.__file__ = str(path)
    exec(compile(data, str(path), "exec"), module.__dict__)                                           # noqa: S102
    module.__source_sha256__ = hashlib.sha256(data).hexdigest()
    return module


def default_connect_factory(stack: contextlib.ExitStack, observer_module):
    """connect_fn that: `mysql.connector.connect(**_connect_kwargs(None))` cua backend + dat/xac minh phien CHI-DOC. Import tri hoan: chi khi that su ket noi. Moi ket noi do observer dong."""
    backend = HERE.parent / "backend"
    if str(backend) not in sys.path:
        sys.path.insert(0, str(backend))
    import mysql.connector                                                                                # noqa: PLC0415
    from app.warehouse.connection import _connect_kwargs                                                  # noqa: PLC0415
    return observer_module.readonly_connect_factory(mysql.connector.connect, lambda: _connect_kwargs(None))


def main(argv: list[str] | None = None, *, connect_factory=None, now_utc=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out", type=Path, required=True, help="duong dan Q0 receipt (xb: khong ghi de)")
    parser.add_argument("--max-statements", type=int, default=40)
    parser.add_argument("--wait-gone-s", type=float, default=10.0)
    parser.add_argument("--poll-s", type=float, default=0.5)
    args = parser.parse_args(argv)
    if not (1 <= args.max_statements <= 40) or not (math.isfinite(args.wait_gone_s) and 0 < args.wait_gone_s <= 10.0) or not (math.isfinite(args.poll_s) and 0 < args.poll_s <= 5.0):
        print(json.dumps({"ok": False, "error": "bad_limits: max_statements 1..40, wait_gone_s (0,10], poll_s (0,5]"}))
        return 2                                                                                           # sai gioi han => khong cham DB
    if args.out.exists():                                                                                  # khong ghi de: tu choi TRUOC khi cham DB
        print(json.dumps({"ok": False, "error": "out_exists"}))
        return 2
    observer_module = _load("repair_db_observer_for_q0", "repair_db_observer_v1.py")
    now = (now_utc or (lambda: dt.datetime.now(dt.timezone.utc)))()
    with contextlib.ExitStack() as stack:
        connect = (connect_factory or (lambda st: default_connect_factory(st, observer_module)))(stack)
        observer = observer_module.DbObserver(connect, max_statements=args.max_statements)
        try:
            result = observer.selftest(wait_gone_s=args.wait_gone_s, poll_s=args.poll_s)
        finally:
            observer.close()
    ok = all(result.get(k) is True for k in observer_module.qm.SELFTEST_KEYS)
    rejected = None
    summary = {"ok": ok, "selftest": {k: result.get(k) for k in observer_module.qm.SELFTEST_KEYS}, "server": {"server_uuid_present": bool(result["server"].get("server_uuid")), "uptime_s": result["server"].get("uptime_s")},
               "statements": observer.statements, "errors": result.get("details", {}).get("errors", [])}
    if ok:
        try:
            summary["receipt_sha256"] = observer_module.write_q0_receipt(result, args.out, now_utc=now)
            summary["receipt_path"] = str(args.out)
        except ValueError as exc:                                                                           # sanity sai (id khong tang / DB list cat / thieu user): KHONG ghi receipt
            ok, rejected = False, str(exc)[:160]
    summary["ok"] = ok
    if rejected:
        summary["rejected"] = rejected
    print(json.dumps(summary, ensure_ascii=False, sort_keys=True))
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())

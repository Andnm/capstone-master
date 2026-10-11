"""Worker observer (PROCESS RIENG, chi-doc): giu MOT ket noi DB xuyen suot phase va tra loi lenh JSON-lines tren stdin/stdout. Duoc `repair_observer_proxy_v1.BoundedObserver` khoi chay trong Job Object rieng
voi timeout cung cho tung lenh; neu mot lenh khong tra loi dung han, PROXY giet worker (khong KILL DB, khong retry).

Giao thuc (moi dong mot JSON, <= 1 MB): dau ra dau tien `{"ready": true, "pid": N}`; yeu cau `{"id": n, "op": "baseline"|"probe"|"selftest"|"close"}`; tra loi `{"id": n, "ok": true, "result": ...}` hoac
`{"id": n, "ok": false, "error": "<Kieu>: <thong diep da cat 200 ky tu>"}`. Khong in secret. Lenh la (op khac) => ok false, worker tiep tuc.

TRANG THAI: THIET KE + MA + TEST voi ket noi GIA; KHONG quyen thuc thi.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import sys
import types
from pathlib import Path
from typing import Any, Callable, TextIO

HERE = Path(__file__).resolve().parent
OPS = ("baseline", "probe", "selftest", "close")
MAX_LINE = 1_000_000


def _load(name: str, filename: str):
    path = HERE / filename
    data = path.read_bytes()
    module = types.ModuleType(name)
    module.__file__ = str(path)
    exec(compile(data, str(path), "exec"), module.__dict__)                                           # noqa: S102
    module.__source_sha256__ = hashlib.sha256(data).hexdigest()
    return module


def _emit(stream: TextIO, obj: Any) -> None:
    stream.write(json.dumps(obj, ensure_ascii=False, sort_keys=True, default=str) + "\n")
    stream.flush()


def serve(stdin: TextIO, stdout: TextIO, observer: Any, *, selftest_kwargs: dict[str, Any] | None = None) -> int:
    """Vong doc-yeu-cau-tra-loi. Tra 0 khi nhan `close` hoac EOF."""
    _emit(stdout, {"ready": True, "pid": os.getpid()})
    for raw in stdin:
        if len(raw) > MAX_LINE:
            _emit(stdout, {"id": None, "ok": False, "error": "request_too_large"})
            continue
        try:
            request = json.loads(raw)
            req_id, op = request["id"], request["op"]
            if type(req_id) is not int or op not in OPS:
                raise ValueError("bad_request")
        except (ValueError, KeyError, TypeError):
            _emit(stdout, {"id": None, "ok": False, "error": "bad_request"})
            continue
        if op == "close":
            _emit(stdout, {"id": req_id, "ok": True, "result": "closed"})
            break
        try:
            if op == "baseline":
                result = observer.capture_baseline()
            elif op == "probe":
                result = observer.probe()
            else:
                result = observer.selftest(**(selftest_kwargs or {}))
            _emit(stdout, {"id": req_id, "ok": True, "result": result})
        except Exception as exc:                                                                           # noqa: BLE001  — loi DB/observer => tra loi ok false, worker van song
            _emit(stdout, {"id": req_id, "ok": False, "error": f"{type(exc).__name__}: {str(exc)[:200]}"})
    try:
        observer.close()
    except Exception:                                                                                      # noqa: BLE001
        pass
    return 0


def default_observer(max_statements: int):
    """DbObserver that tren adapter chi-doc + kwargs backend (`.env` o cwd)."""
    observer_module = _load("repair_db_observer_for_worker", "repair_db_observer_v1.py")
    backend = HERE.parent / "backend"
    if str(backend) not in sys.path:
        sys.path.insert(0, str(backend))
    import mysql.connector                                                                                # noqa: PLC0415
    from app.warehouse.connection import _connect_kwargs                                                  # noqa: PLC0415
    return observer_module.DbObserver(observer_module.readonly_connect_factory(mysql.connector.connect, lambda: _connect_kwargs(None)), max_statements=max_statements)


def main(argv: list[str] | None = None, *, observer_factory: Callable[[int], Any] | None = None, stdin: TextIO | None = None, stdout: TextIO | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--max-statements", type=int, default=60)
    args = parser.parse_args(argv)
    if not (1 <= args.max_statements <= 200):
        print(json.dumps({"ok": False, "error": "bad_limits"}))
        return 2
    observer = (observer_factory or default_observer)(args.max_statements)
    return serve(stdin or sys.stdin, stdout or sys.stdout, observer)


if __name__ == "__main__":
    raise SystemExit(main())

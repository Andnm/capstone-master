"""READ-ONLY, MOT SELECT: do hai bien GLOBAL cua InnoDB (buffer pool, redo log capacity) de GPT/nguoi dung quyet dinh co can SET GLOBAL hay khong (GPT 100 §4). KHONG SET GLOBAL, KHONG DDL/DML, KHONG COUNT bang,
KHONG doc/in secret. Chua co quyen thuc thi: duoc chay chi nhu mot plan readonly mot-phase qua cung vong doi publisher/gate nhu Ph1, sau review rieng.

    python repair_innodb_probe_v1.py --crawl-evidence <live_crawl_idle_evidence.json> --out <JSON moi, chua ton tai>

GIOI HAN HARD (GPT 104): hard bound 30 s la **outer Job deadline cua runner** (plan readonly mot phase, `deadline_s=30.0`); WATCHDOG trong script chi la best-effort — no KHONG bao duoc moi stall (vd. watchdog tu ghi bao cao treo,
connect treo ma khong tra lai). Outer timeout / thieu bao cao / bao cao do dang PHAI bi coi la FAIL/QUARANTINE du bao cao tung co `ok:true`; khong dung JSON de chung minh process da chet.
Hang rao: (1) gate crawler-idle cua `warehouse_audit_v3.EvidenceGate` TRUOC khi mo ket noi VA SAU truy van; (2) phien READ ONLY + MAX_EXECUTION_TIME=5000 ms; (3) connection_timeout=10 s; (4) WATCHDOG 30 s (best-effort, xem tren)
chay SUOT cac duong ket noi/truy van/dong ket noi/ghi bao cao: het gio => ghi JSON loi (neu ghi duoc) roi `exit_fn(4)` (ke ca khi ghi that bai); (5) `--out` mo bang `xb`.
THANH CONG chi duoc ghi SAU KHI dong ket noi da xac nhan (close that bai => ok=false, rc 4) va chi sau khi ghi tep thanh cong; watchdog huy SAU CUNG. Bao cao tach `planned_inventory` (ke hoach co dinh) va `attempted`
(nhat ky thuc te: lenh nao da thu, ket qua), ROLLBACK/close chi duoc ghi khi co ket noi that. Server identity lay tu bat tay giao thuc (`get_server_info`, `connection_id`) -- KHONG them cau SQL nao.
Ma thoat: 0 ok; 2 tham so/`--out` da ton tai; 3 gate tu choi; 4 loi DB/timeout/close/ghi/gia tri khong hop le/gate mat sau truy van.
"""
from __future__ import annotations

import argparse
import datetime as dt
import hashlib
import json
import os
import sys
import threading
import time
import types
from pathlib import Path
from typing import Any, Callable, Mapping

HERE = Path(__file__).resolve().parent
BACKEND = HERE.parent / "backend"
SQL = "SELECT @@GLOBAL.innodb_buffer_pool_size AS buffer_pool_size_bytes, @@GLOBAL.innodb_redo_log_capacity AS redo_log_capacity_bytes"
SESSION_SQL = ("SET SESSION TRANSACTION READ ONLY", "SET SESSION MAX_EXECUTION_TIME=5000")
MAX_EXECUTION_MS = 5000
CONNECT_TIMEOUT_S = 10
WALL_MAX_S = 30.0


def sql_set_sha256() -> str:
    return hashlib.sha256(json.dumps([*SESSION_SQL, SQL, "ROLLBACK"], sort_keys=True).encode("utf-8")).hexdigest()


def _load(name: str, filename: str):
    path = HERE / filename
    data = path.read_bytes()
    module = types.ModuleType(name)
    module.__file__ = str(path)
    exec(compile(data, str(path), "exec"), module.__dict__)                                           # noqa: S102
    module.__source_sha256__ = hashlib.sha256(data).hexdigest()
    return module


def source_sha256() -> str:
    return hashlib.sha256(Path(__file__).read_bytes()).hexdigest()


def utc_text(t: dt.datetime | None = None) -> str:
    return (t or dt.datetime.now(dt.timezone.utc)).strftime("%Y-%m-%dT%H:%M:%SZ")


def default_connect(secrets: list[str]):
    """Ket noi that: Settings (nap `.env` boi chinh Settings) + mysql.connector thuan Python. KHONG import `app.core.database` (khong tao pool). `secrets` duoc dien TRUOC khi ket noi de loi ket noi cung duoc loc."""
    sys.path.insert(0, str(BACKEND))
    import mysql.connector  # noqa: PLC0415
    from app.core.config import settings  # noqa: PLC0415
    secrets.extend(str(v) for v in (settings.DB_PASSWORD, settings.DB_USER, settings.DB_HOST) if v)
    return mysql.connector.connect(host=settings.DB_HOST, port=settings.DB_PORT, user=settings.DB_USER, password=settings.DB_PASSWORD, time_zone="+00:00", autocommit=False,
                                   connection_timeout=CONNECT_TIMEOUT_S, use_pure=True)


def redact(text: str, secrets: list[str]) -> str:
    for secret in sorted(set(secrets), key=len, reverse=True):
        if secret:
            text = text.replace(secret, "<redacted>")
    return text[:400]


def default_gate_factory(path: Path):
    module = _load("warehouse_audit_for_probe", "warehouse_audit_v3.py")
    return module.EvidenceGate(path), module.GateLost


def write_report(path: Path, report: Mapping[str, Any]) -> str:
    data = json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True, default=str).encode("utf-8")
    with open(path, "xb") as handle:
        handle.write(data)
    return hashlib.sha256(data).hexdigest()


def main(argv: list[str] | None = None, *, connect_fn: Callable[[list[str]], Any] = default_connect, gate_factory: Callable[[Path], tuple[Any, type]] = default_gate_factory,
         timer_factory: Callable[..., Any] = threading.Timer, exit_fn: Callable[[int], None] = os._exit) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--crawl-evidence", type=Path, required=True)
    parser.add_argument("--out", type=Path, required=True)
    args = parser.parse_args(argv)
    out = args.out
    if not out.is_absolute() or not out.parent.is_dir() or out.exists() or not args.crawl_evidence.is_absolute():
        print(json.dumps({"ok": False, "error": "bad_args_or_out_exists"}))
        return 2
    started = time.monotonic()
    planned = [*SESSION_SQL, SQL, "ROLLBACK"]
    report: dict[str, Any] = {"kind": "innodb_global_probe", "ok": False, "started_utc": utc_text(), "units": "bytes", "source_sha256": source_sha256(), "sql_set_sha256": sql_set_sha256(),
                              "planned_inventory": [{"sql": s, "sha256": hashlib.sha256(s.encode("utf-8")).hexdigest()} for s in planned], "attempted": [],
                              "limits": {"max_execution_ms": MAX_EXECUTION_MS, "connect_timeout_s": CONNECT_TIMEOUT_S, "wall_max_s": WALL_MAX_S}, "errors": [], "values": None, "server": None, "gate": None}
    secrets: list[str] = []
    lock = threading.Lock()
    state = {"finished": False, "wall_fired": False}

    def attempt(sql: str, action: Callable[[], Any]) -> Any:
        entry = {"sql": sql, "status": "started"}
        report["attempted"].append(entry)                                                             # ghi TRUOC khi thu: neu treo/loi thi nhat ky van cho thay lenh nao dang chay
        try:
            result = action()
        except BaseException:
            entry["status"] = "error"
            raise
        entry["status"] = "ok"
        return result

    def finish(code: int) -> int:
        """Ghi bao cao cuoi (xb). Thanh cong (code 0) chi khi ghi duoc; ghi that bai => 4 va KHONG danh dau da hoan tat. Neu watchdog da no thi khong bao gio tra thanh cong."""
        report["finished_utc"], report["wall_s"] = utc_text(), round(time.monotonic() - started, 3)
        with lock:
            if state["wall_fired"]:
                return 4
            if code == 0 and report["errors"]:
                code = 4
            report["ok"] = code == 0 and not report["errors"]
            try:
                digest = write_report(out, report)
            except Exception as exc:                                                                  # noqa: BLE001  (khong ghi duoc bao cao => khong the claim thanh cong)
                print(json.dumps({"ok": False, "error": f"report_write_failed: {type(exc).__name__}"}))
                return 4
            state["finished"] = True
        print(json.dumps({"ok": report["ok"], "out": str(out), "report_sha256": digest, "errors": [e[:120] for e in report["errors"]]}, ensure_ascii=False))
        return code

    def on_wall() -> None:
        """Het 30 s o BAT KY dau: ghi JSON loi neu co the, roi `exit_fn(4)` -- LUON, ke ca khi ghi that bai. Chi khong thoat neu bao cao da duoc ghi xong (finished)."""
        got = lock.acquire(timeout=2.0)                                                               # main giu khoa luc ghi: khong cho vo han
        try:
            if got and state["finished"]:
                return
            state["wall_fired"] = True
            if got:
                report["errors"].append("wall_timeout")
                report["ok"] = False
                report["finished_utc"] = utc_text()
                try:
                    write_report(out, report)
                except Exception:                                                                     # noqa: BLE001
                    pass
        finally:
            if got:
                lock.release()
        if not (got and state["finished"]):
            exit_fn(4)

    gate, gate_lost = gate_factory(args.crawl_evidence)
    timer = timer_factory(WALL_MAX_S, on_wall)
    timer.daemon = True
    timer.start()
    conn = None
    code = 4
    try:
        try:
            gate.check(force=True)                                                                    # TRUOC khi mo ket noi
        except gate_lost as exc:
            report["errors"].append(redact(f"gate_refused: {exc}", secrets))
            report["gate"] = {"checks": gate.checks, "last_verdict": gate.last_verdict}
            code = 3
        else:
            conn = connect_fn(secrets)
            report["server"] = {"version": conn.get_server_info(), "connection_id": getattr(conn, "connection_id", None)}
            cursor = conn.cursor()
            for statement in SESSION_SQL:
                attempt(statement, lambda s=statement: cursor.execute(s))
            rows = attempt(SQL, lambda: (cursor.execute(SQL), cursor.fetchall())[1])
            cursor.close()
            attempt("ROLLBACK", conn.rollback)
            if not (isinstance(rows, list) and len(rows) == 1 and len(rows[0]) == 2):
                report["errors"].append("unexpected_result_shape")
            else:
                values = [rows[0][0], rows[0][1]]
                if not all(type(v) is int and v > 0 for v in values):
                    report["errors"].append(f"values_not_positive_integers: {[type(v).__name__ for v in values]}")
                else:
                    report["values"] = {"buffer_pool_size_bytes": values[0], "redo_log_capacity_bytes": values[1]}
            try:
                gate.check(force=True)                                                                # SAU truy van: crawler van idle
                report["gate"] = {"checks": gate.checks, "last_verdict": gate.last_verdict, "sha_chain": gate.sha_chain, "receipts": gate.receipts}
            except gate_lost as exc:
                report["errors"].append(redact(f"gate_lost_after_query: {exc}", secrets))
                report["gate"] = {"checks": gate.checks, "last_verdict": gate.last_verdict, "sha_chain": gate.sha_chain}
            code = 0 if not report["errors"] else 4
    except BaseException as exc:                                                                      # noqa: BLE001  (gom ca KeyboardInterrupt: van dong ket noi + ghi loi)
        report["errors"].append(redact(f"{type(exc).__name__}: {exc}", secrets))
        code = 4
        interrupt = exc if isinstance(exc, (KeyboardInterrupt, SystemExit)) else None
    else:
        interrupt = None
    # ---- don dep DUOI watchdog: dong ket noi TRUOC khi bao cao cuoi; that bai => khong bao thanh cong
    if conn is not None:
        try:
            attempt("close", conn.close)
        except BaseException as exc:                                                                  # noqa: BLE001
            report["errors"].append(redact(f"close_failed: {type(exc).__name__}: {exc}", secrets))   # finish() bien moi loi thanh rc 4 / ok=false
    final = finish(code)
    timer.cancel()                                                                                    # HUY SAU CUNG: sau khi bao cao cuoi da ghi (hoac that bai)
    if interrupt is not None:
        raise interrupt
    return final


if __name__ == "__main__":
    raise SystemExit(main())

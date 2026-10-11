"""Bang chung `crawler-idle` cho controller audit warehouse (GPT file 32 W4, 34 §3D, 36 §2). Do CLAUDE chay (trong phien van hanh du an), doc-chi.

    python crawl_idle_evidence.py [--out <file>]                                      # mot lan
    python crawl_idle_evidence.py --out <live> --renew-seconds 300 --interval 60 --archive-dir <dir>   # vong gia han CO HAN

Controller `warehouse_audit_v3.py` KHONG cham DB van hanh / .env; no chi doc tep `--out` o MOI ranh gioi query. Bang chung: so run `queued|running` toan cuc va so item `queued|running` toan cuc (deu phai = 0), run moi nhat,
`observed_at_utc` (luc BAT DAU doc dem, dung de tinh tuoi), `generated_at_utc` (luc xuat ban), `expires_at_utc`. Phien `READ ONLY` + `MAX_EXECUTION_TIME` ngan, XAC MINH bang `@@session.transaction_read_only`
(khong xac minh duoc => khong ghi bang chung), rollback sau khi doc. Khong ghi DB, khong deploy, khong crawl.
Xuat ban NGUYEN TU (tep tam cung thu muc + os.replace) de controller khong bao gio doc JSON do dang; moi ban da xuat duoc luu theo SHA trong `--archive-dir`. Loi gia han => KHONG cham tep live (no tu het han);
phat hien crawler dang chay => xuat ban trang thai busy roi dung vong lap (controller se tu choi). Day KHONG phai khoa phan tan: van co do tre phat hien toi mot chu ky.
"""
from __future__ import annotations

import argparse
import datetime as dt
import hashlib
import json
import os
import socket
import sys
import time
from pathlib import Path
from typing import Any, Callable, Mapping

HERE = Path(__file__).resolve().parent
REPO = HERE.parent
ROOT = REPO.parent
SCHEMA = "crawl_idle_evidence_v2"
VALIDITY_S = 600
SQL_ACTIVE_RUNS = "SELECT COUNT(*) AS n FROM crawl_runs WHERE status IN ('queued','running')"
SQL_ACTIVE_ITEMS = "SELECT COUNT(*) AS n FROM crawl_run_items WHERE status IN ('queued','running')"
SQL_LATEST_RUN = "SELECT id, status FROM crawl_runs ORDER BY id DESC LIMIT 1"
SQL_SESSION_RO = "SELECT @@session.transaction_read_only AS ro"
MAX_EXECUTION_MS = 5000
DEFAULT_OUT = ROOT / "outputs" / "model-diagnosis-20261008" / "warehouse_audit" / "crawl_idle_evidence_latest.json"


def _utc(value: dt.datetime) -> dt.datetime:
    return value.astimezone(dt.timezone.utc).replace(microsecond=0)


def build_evidence(active_runs: int, active_items: int, latest: Mapping[str, Any] | None, now_utc: dt.datetime, *, host: str = "", session_read_only: bool = False,
                   published_at_utc: dt.datetime | None = None) -> dict[str, Any]:
    """Pure. `now_utc` = thoi diem BAT DAU doc (observed); `published_at_utc` = luc xuat ban (mac dinh = observed). Het han tinh tu observed."""
    observed = _utc(now_utc)
    generated = _utc(published_at_utc) if published_at_utc is not None else observed
    return {
        "schema": SCHEMA,
        "observed_at_utc": observed.strftime("%Y-%m-%dT%H:%M:%SZ"),
        "generated_at_utc": generated.strftime("%Y-%m-%dT%H:%M:%SZ"),
        "expires_at_utc": (observed + dt.timedelta(seconds=VALIDITY_S)).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "active_runs": int(active_runs),
        "active_items": int(active_items),
        "latest_run_id": int(latest["id"]) if latest else 0,
        "latest_run_status": str(latest["status"]) if latest else None,
        "queries": {"active_runs": SQL_ACTIVE_RUNS, "active_items": SQL_ACTIVE_ITEMS, "latest_run": SQL_LATEST_RUN},
        "session": {"read_only_verified": bool(session_read_only), "max_execution_ms": MAX_EXECUTION_MS, "statements": "3 SELECT + 1 SELECT @@session.transaction_read_only, rollback"},
        "generator_sha256": hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
        "host": host,
    }


def read_counts() -> tuple[int, int, dict[str, Any] | None, bool]:
    sys.path.insert(0, str(REPO / "backend"))
    from app.core.database import get_db_connection  # noqa: PLC0415
    with get_db_connection() as conn:
        cursor = conn.cursor(dictionary=True)
        try:
            cursor.execute("SET SESSION TRANSACTION READ ONLY")           # truoc moi SELECT; phien tam thoi cua tien trinh nay
            cursor.execute(f"SET SESSION MAX_EXECUTION_TIME={MAX_EXECUTION_MS}")
            cursor.execute(SQL_SESSION_RO)
            read_only = int(cursor.fetchone()["ro"]) == 1
            if not read_only:
                raise RuntimeError("khong xac minh duoc phien READ ONLY: khong doc va khong ghi bang chung")
            cursor.execute(SQL_ACTIVE_RUNS)
            runs = int(cursor.fetchone()["n"])
            cursor.execute(SQL_ACTIVE_ITEMS)
            items = int(cursor.fetchone()["n"])
            cursor.execute(SQL_LATEST_RUN)
            latest = cursor.fetchone()
            return runs, items, latest, read_only
        finally:
            cursor.close()
            conn.rollback()                      # ket thuc giao dich doc; khong khoa, khong ghi


def publish(out: Path, *, archive_dir: Path | None = None, read: Callable[[], tuple[int, int, Any, bool]] = read_counts,
            clock: Callable[[], dt.datetime] = lambda: dt.datetime.now(dt.timezone.utc), host: str | None = None) -> dict[str, Any]:
    """Doc dem -> xuat ban NGUYEN TU (tep tam cung thu muc + os.replace) -> luu ban da xuat theo SHA. Loi doc => nem ngoai le va KHONG cham tep live (tu het han)."""
    observed = clock()                                                    # moc bat dau doc: dung tinh tuoi
    runs, items, latest, read_only = read()
    evidence = build_evidence(runs, items, latest, observed, host=socket.gethostname() if host is None else host, session_read_only=read_only, published_at_utc=clock())
    payload = json.dumps(evidence, ensure_ascii=False, indent=2).encode("utf-8")
    out = Path(out)
    out.parent.mkdir(parents=True, exist_ok=True)
    tmp = out.with_name(f".{out.name}.{os.getpid()}.{time.time_ns()}.tmp")
    tmp.write_bytes(payload)
    os.replace(tmp, out)                                                  # nguyen tu tren cung o dia
    digest = hashlib.sha256(payload).hexdigest()
    if archive_dir is not None:
        Path(archive_dir).mkdir(parents=True, exist_ok=True)
        target = Path(archive_dir) / f"crawl_idle_{digest}.json"
        if not target.exists():
            target.write_bytes(payload)
    return {"evidence": evidence, "sha256": digest, "busy": bool(runs or items)}


def renew_loop(out: Path, *, duration_s: float, interval_s: float, archive_dir: Path | None = None, read: Callable[[], tuple[int, int, Any, bool]] = read_counts,
               clock: Callable[[], dt.datetime] = lambda: dt.datetime.now(dt.timezone.utc), sleep: Callable[[float], None] = time.sleep,
               monotonic: Callable[[], float] = time.monotonic, log: Callable[[str], None] = print) -> int:
    """Vong gia han CO HAN (khong daemon vo thoi han). 0 = het thoi gian binh thuong; 1 = phat hien crawler dang chay (da xuat ban busy, dung); 2 = loi doc (KHONG cham tep live; dung)."""
    deadline = monotonic() + duration_s
    while True:
        try:
            result = publish(out, archive_dir=archive_dir, read=read, clock=clock)
        except Exception as exc:  # noqa: BLE001 - khong kéo dai han khi gia han loi
            log(f"GIA HAN LOI: {type(exc).__name__}: {exc} - khong cham tep live, dung vong lap")
            return 2
        log(f"gia han {result['evidence']['generated_at_utc']} sha {result['sha256'][:12]} active_runs={result['evidence']['active_runs']} active_items={result['evidence']['active_items']}")
        if result["busy"]:
            log("CRAWLER DANG CHAY: da xuat ban trang thai busy, dung vong lap")
            return 1
        if monotonic() + interval_s >= deadline:
            return 0
        sleep(interval_s)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--out", default=str(DEFAULT_OUT))
    parser.add_argument("--renew-seconds", type=float, default=0.0, help="> 0: lap gia han trong khoang thoi gian nay roi dung (CO HAN)")
    parser.add_argument("--interval", type=float, default=60.0)
    parser.add_argument("--archive-dir", default="")
    args = parser.parse_args(argv)
    archive = Path(args.archive_dir) if args.archive_dir else None
    if args.renew_seconds > 0:
        return renew_loop(Path(args.out), duration_s=args.renew_seconds, interval_s=args.interval, archive_dir=archive)
    result = publish(Path(args.out), archive_dir=archive)
    evidence = result["evidence"]
    print(json.dumps({k: evidence[k] for k in ("observed_at_utc", "generated_at_utc", "expires_at_utc", "active_runs", "active_items", "latest_run_id", "latest_run_status")}, ensure_ascii=False))
    print("bang chung:", args.out, "| sha256:", result["sha256"])
    return 1 if result["busy"] else 0


if __name__ == "__main__":
    raise SystemExit(main())

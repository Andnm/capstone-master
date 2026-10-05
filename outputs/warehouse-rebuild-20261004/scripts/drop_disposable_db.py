"""Xoa DUNG MOT DB rehearsal disposable (ten co dinh qua --database, phai khop `warehouse_rh20261004_*`).

    venv/Scripts/python.exe ../../outputs/warehouse-rebuild-20261004/scripts/drop_disposable_db.py --database warehouse_rh20261004_3src --expect-status running

An toan: chi chap nhan ten khop `^warehouse_rh20261004_[a-z0-9]+$`, tu choi neu la DB current (warehouse_current.json), batch phai co
dung trang thai ky vong, khong xoa DB nao khac. Dry-run mac dinh; `--apply` moi xoa.
"""
from __future__ import annotations

import argparse
import json
import re
import sys
import time
from pathlib import Path

BACKEND = Path(__file__).resolve().parents[3] / "hotel-price-intelligence" / "backend"
sys.path.insert(0, str(BACKEND))
from dotenv import load_dotenv  # noqa: E402

load_dotenv(BACKEND / ".env")
import mysql.connector  # noqa: E402
from app.core.config import settings  # noqa: E402

POINTER = BACKEND.parent.parent / "outputs" / "warehouse" / "warehouse_current.json"
t0 = time.time()


def log(msg: str) -> None:
    print(f"[{time.time() - t0:6.1f}s] {msg}", flush=True)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--database", required=True)
    parser.add_argument("--expect-status", required=True, choices=["running", "pass", "fail"])
    parser.add_argument("--apply", action="store_true")
    args = parser.parse_args()
    if not re.fullmatch(r"warehouse_rh20261004_[a-z0-9]+", args.database):
        log(f"TU CHOI: {args.database!r} khong phai DB rehearsal 20261004.")
        return 2
    if POINTER.exists() and json.loads(POINTER.read_text(encoding="utf-8")).get("warehouse_database") == args.database:
        log("TU CHOI: DB nay la warehouse current.")
        return 2
    cn = mysql.connector.connect(host=settings.DB_HOST, port=settings.DB_PORT, user=settings.DB_USER,
                                 password=settings.DB_PASSWORD, connection_timeout=10, autocommit=True)
    cur = cn.cursor(dictionary=True)
    cur.execute("SET SESSION max_execution_time = 0")
    cur.execute(f"SELECT batch_id, status, warehouse_database FROM `{args.database}`.etl_import_batches")
    batches = cur.fetchall()
    log(f"batches trong DB: {batches}")
    if len(batches) != 1 or batches[0]["status"] != args.expect_status or batches[0]["warehouse_database"] != args.database:
        log("DUNG: batch khong dung ky vong - khong xoa.")
        return 2
    if not args.apply:
        log("dry-run: du dieu kien xoa. Them --apply de DROP DATABASE.")
        return 0
    log(f"DROP DATABASE `{args.database}` ...")
    cur.execute(f"DROP DATABASE `{args.database}`")
    cur.execute("SHOW DATABASES")
    still = [r["Database"] for r in cur.fetchall() if r["Database"] == args.database]
    log(f"DB con ton tai sau drop: {still}")
    return 0 if not still else 1


if __name__ == "__main__":
    raise SystemExit(main())

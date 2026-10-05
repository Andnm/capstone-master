"""Chan doan Tang A (GPT file 22): chay DUNG cau lenh nang da ket - `build_full_history_references(commit=False)` - tren DB dang do,
roi LUON ROLLBACK. Khong ghi du lieu ben vung; khong bien DB dang do thanh ket qua hop le.

    venv/Scripts/python.exe ../../outputs/warehouse-rebuild-20261004/scripts/diag_references.py \\
        --database warehouse_rh20261004_3src --batch-id b20261004_3srcrh

Khong poll COUNT(*) bang trieu dong. Heartbeat 30 s: thoi gian, CPU cua chinh process nay va `SELECT 1` qua ket noi RIENG (timeout ngan).
Ba lan `SELECT 1` lien tiep hong => MySQL wedge => in canh bao va thoat (khong lap lai).
"""
from __future__ import annotations

import argparse
import os
import sys
import threading
import time
from pathlib import Path

BACKEND = Path(__file__).resolve().parents[3] / "hotel-price-intelligence" / "backend"
sys.path.insert(0, str(BACKEND))
from dotenv import load_dotenv  # noqa: E402

load_dotenv(BACKEND / ".env")
import mysql.connector  # noqa: E402
from app.core.config import settings  # noqa: E402
from app.warehouse.connection import warehouse_connection  # noqa: E402
from app.warehouse.etl_config import etl_config, etl_config_sha256  # noqa: E402
from app.warehouse.naming import require_warehouse_database  # noqa: E402
from app.warehouse.reference_builder import build_full_history_references  # noqa: E402

t0 = time.time()
done = threading.Event()
wedged = threading.Event()


def log(msg: str) -> None:
    print(f"[{time.time() - t0:8.1f}s cpu={time.process_time():7.1f}s] {msg}", flush=True)


def heartbeat() -> None:
    failures = 0
    while not done.wait(30):
        started = time.time()
        try:
            cn = mysql.connector.connect(host=settings.DB_HOST, port=settings.DB_PORT, user=settings.DB_USER,
                                         password=settings.DB_PASSWORD, connection_timeout=5)
            cur = cn.cursor()
            cur.execute("SELECT 1")
            cur.fetchall()
            cur.close()
            cn.close()
            failures = 0
            log(f"heartbeat: MySQL song (SELECT 1 {time.time() - started:.2f}s)")
        except Exception as exc:  # noqa: BLE001
            failures += 1
            log(f"heartbeat: SELECT 1 THAT BAI lan {failures}/3: {type(exc).__name__}: {str(exc)[:100]}")
            if failures >= 3:
                log("MYSQL WEDGE (3 lan lien tiep) - dung va bao, khong lap lai.")
                wedged.set()
                os._exit(3)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--database", required=True)
    parser.add_argument("--batch-id", required=True)
    args = parser.parse_args()
    database = require_warehouse_database(args.database)
    threading.Thread(target=heartbeat, daemon=True).start()
    with warehouse_connection(database) as conn:
        cursor = conn.cursor(dictionary=True)
        cursor.execute("SELECT batch_id, status, warehouse_database, started_at, finished_at, etl_config_sha256 "
                       "FROM etl_import_batches WHERE batch_id=%s", (args.batch_id,))
        batch = cursor.fetchone()
        cursor.execute("SELECT @@innodb_buffer_pool_size pool, @@innodb_redo_log_capacity redo")
        cfgvars = cursor.fetchone()
        cursor.close()
        log(f"batch={batch}")
        log(f"InnoDB hien tai: {cfgvars}")
        if batch is None or batch["warehouse_database"] != database or batch["status"] != "running":
            log("DUNG: batch khong dung/khong o trang thai 'running' (ky vong DB dang do).")
            return 2
        config, sha = etl_config(), etl_config_sha256()
        log(f"etl_config sha code={sha} pin={batch['etl_config_sha256']} khop={sha == batch['etl_config_sha256']} "
            f"min_runs={config['reference_min_runs']} min_coverage={config['reference_min_coverage']}")
        log("BAT DAU build_full_history_references(commit=False) - cau lenh nang da ket lan truoc")
        result = build_full_history_references(
            conn, batch_id=args.batch_id, activated_at=batch["started_at"], min_runs=config["reference_min_runs"],
            min_coverage=config["reference_min_coverage"], commit=False)
        log(f"HOAN TAT tinh toan (chua commit): {result}")
        conn.rollback()
        log("DA ROLLBACK")
        cursor = conn.cursor(dictionary=True)
        cursor.execute("SELECT (SELECT COUNT(*) FROM hotel_room_candidates) cand, (SELECT COUNT(*) FROM hotel_reference_rooms) ref")
        after = cursor.fetchone()
        cursor.close()
        conn.commit()
        log(f"sau rollback: {after}  (ky vong 0/0 vi DB dang do chua co reference)")
        ok = int(after["cand"]) == 0 and int(after["ref"]) == 0
        log("KET QUA: " + ("PASS (hoan tat + rollback sach)" if ok else "FAIL - rollback khong sach"))
    done.set()
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())

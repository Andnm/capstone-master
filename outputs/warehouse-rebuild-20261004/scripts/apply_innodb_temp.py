"""Tang cau hinh InnoDB TAM THOI (khong persist, mat khi restart MySQL) - nguoi dung dong y 05/10/2026 21:2x, GPT file 22.

    venv/Scripts/python.exe ../../outputs/warehouse-rebuild-20261004/scripts/apply_innodb_temp.py [--apply]

Mac dinh DRY-RUN: chi in baseline + gia tri hien tai. `--apply` moi chay SET GLOBAL:
    innodb_buffer_pool_size   = 8 GiB   (8589934592)
    innodb_redo_log_capacity  = 4 GiB   (4294967296)
Truoc khi ap dung: bat buoc 0 run/item queued|running o DB van hanh. Sau khi ap dung: doc lai ca hai bien, cho resize xong,
SELECT 1, doi chieu count/max_id DB van hanh voi baseline GPT/Claude da ghi.
"""
from __future__ import annotations

import argparse
import sys
import time
from pathlib import Path

BACKEND = Path(__file__).resolve().parents[3] / "hotel-price-intelligence" / "backend"
sys.path.insert(0, str(BACKEND))
from dotenv import load_dotenv  # noqa: E402

load_dotenv(BACKEND / ".env")
import mysql.connector  # noqa: E402
from app.core.config import settings  # noqa: E402

TARGET_POOL = 8 * 1024 ** 3
TARGET_REDO = 4 * 1024 ** 3
OPS_BASELINE = {"crawl_runs": (59, 62), "crawl_run_items": (211669, 224449), "price_observations": (1729104, 1732535)}
t0 = time.time()


def log(msg: str) -> None:
    print(f"[{time.time() - t0:6.1f}s] {msg}", flush=True)


def connect():
    return mysql.connector.connect(host=settings.DB_HOST, port=settings.DB_PORT, user=settings.DB_USER,
                                   password=settings.DB_PASSWORD, connection_timeout=10, autocommit=True)


def variables(cur) -> dict:
    cur.execute("SELECT @@innodb_buffer_pool_size pool, @@innodb_redo_log_capacity redo, @@innodb_buffer_pool_chunk_size chunk, "
                "@@innodb_buffer_pool_instances inst")
    return cur.fetchone()


def ops_state(cur) -> dict:
    out = {}
    for table, pk in (("crawl_runs", "id"), ("crawl_run_items", "id"), ("price_observations", "record_id")):
        cur.execute(f"SELECT COUNT(*) n, MAX({pk}) mx FROM hotel_price_intel.{table}")
        row = cur.fetchone()
        out[table] = (int(row["n"]), int(row["mx"]))
    cur.execute("SELECT (SELECT COUNT(*) FROM hotel_price_intel.crawl_runs WHERE status IN ('queued','running')) r, "
                "(SELECT COUNT(*) FROM hotel_price_intel.crawl_run_items WHERE status IN ('queued','running')) i")
    active = cur.fetchone()
    out["active"] = (int(active["r"]), int(active["i"]))
    return out


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--apply", action="store_true", help="that su SET GLOBAL (mac dinh dry-run)")
    args = parser.parse_args()
    cn = connect()
    cur = cn.cursor(dictionary=True)
    cur.execute("SET SESSION max_execution_time = 60000")
    before = variables(cur)
    log(f"truoc: {before}")
    state = ops_state(cur)
    log(f"DB van hanh: {state}")
    if state["active"] != (0, 0):
        log(f"DUNG: con run/item active {state['active']} - khong doi cau hinh khi crawler dang chay.")
        return 2
    for table, expected in OPS_BASELINE.items():
        if state[table] != expected:
            log(f"DUNG: {table} {state[table]} != baseline {expected}")
            return 2
    if not args.apply:
        log("dry-run xong, khong doi gi. Them --apply de SET GLOBAL.")
        return 0
    if int(before["pool"]) != TARGET_POOL:
        log(f"SET GLOBAL innodb_buffer_pool_size = {TARGET_POOL}")
        cur.execute(f"SET GLOBAL innodb_buffer_pool_size = {TARGET_POOL}")
    if int(before["redo"]) != TARGET_REDO:
        log(f"SET GLOBAL innodb_redo_log_capacity = {TARGET_REDO}")
        cur.execute(f"SET GLOBAL innodb_redo_log_capacity = {TARGET_REDO}")
    deadline = time.time() + 180
    while time.time() < deadline:
        cur.execute("SELECT @@innodb_buffer_pool_size pool, @@innodb_redo_log_capacity redo")
        now = cur.fetchone()
        cur.execute("SHOW GLOBAL STATUS WHERE Variable_name IN ('Innodb_buffer_pool_resize_status','Innodb_redo_log_resize_status',"
                    "'Innodb_redo_log_capacity_resized')")
        status = {r["Variable_name"]: r["Value"] for r in cur.fetchall()}
        log(f"pool={now['pool']} redo={now['redo']} status={status}")
        if int(now["pool"]) == TARGET_POOL and int(now["redo"]) == TARGET_REDO and not status.get("Innodb_buffer_pool_resize_status"):
            break
        time.sleep(5)
    after = variables(cur)
    log(f"sau: {after}")
    cur.execute("SELECT 1 AS ok")
    log(f"SELECT 1 -> {cur.fetchone()}")
    state_after = ops_state(cur)
    log(f"DB van hanh sau: {state_after}")
    ok = (int(after["pool"]) == TARGET_POOL and int(after["redo"]) == TARGET_REDO
          and all(state_after[t] == OPS_BASELINE[t] for t in OPS_BASELINE) and state_after["active"] == (0, 0))
    log("KET QUA: " + ("OK" if ok else "CHUA DAT - xem dong tren"))
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())

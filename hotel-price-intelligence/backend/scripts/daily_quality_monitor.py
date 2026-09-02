"""Health-check ONE crawl run in isolation (operational quality monitor, not EDA).

Different job from audit_sampling_quality.py: that script compares a BATCH of same-protocol
pilot runs against each other (fixed cohort, same checkin dates, >=6 runs). This script checks
a single day's run on a single source (local_primary / vps / local_aux) — the thing an operator
runs once a day, on the machine that owns that source's database, from 02/09/2026 to 30/11/2026.

--source-code is a label only (for the report filename / output field). Each source keeps its
own physical database (CLAUDE.md muc 4.10), so this script never filters SQL by source — it only
ever sees whatever DB is configured in the local .env of the machine it runs on.

Usage (from backend/, on the machine that owns the DB you want to check):
    python scripts/daily_quality_monitor.py --source-code local_primary --latest
    python scripts/daily_quality_monitor.py --source-code vps --latest
    python scripts/daily_quality_monitor.py --source-code local_aux --latest
    python scripts/daily_quality_monitor.py --source-code local_primary --run-id 42

Report is printed to console (summary line + full JSON) AND saved to
hotel-price-intelligence/data/quality_monitor/{source_code}_{as_of_date}.json (data/ is
gitignored, matches CLAUDE.md muc 10 — khong commit du lieu tho).
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path
from statistics import median
from zoneinfo import ZoneInfo

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.core.database import get_db_connection

VN_TZ = ZoneInfo("Asia/Ho_Chi_Minh")
PRICE_FLOOR_VND = 50_000
PRICE_CEILING_VND = 50_000_000
STUCK_AFTER_HOURS = 24

BACKEND_DIR = Path(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
REPORT_DIR = BACKEND_DIR.parent / "data" / "quality_monitor"

_STATUS_RANK = {"pass": 0, "warn": 1, "fail": 2}


def _select_run(cursor, run_id: int | None) -> dict | None:
    if run_id:
        cursor.execute("SELECT * FROM crawl_runs WHERE id=%s", (run_id,))
    else:
        cursor.execute("SELECT * FROM crawl_runs ORDER BY id DESC LIMIT 1")
    return cursor.fetchone()


def _city_price_stats(price_rows: list[dict]) -> dict:
    by_city: dict[str, list[float]] = defaultdict(list)
    for row in price_rows:
        by_city[row["city"] or "unknown"].append(float(row["price_per_night"]))
    stats = {}
    for city, values in by_city.items():
        values.sort()
        n = len(values)
        stats[city] = {
            "n": n,
            "p5": values[max(0, int(n * 0.05) - 1)],
            "median": median(values),
            "p95": values[min(n - 1, int(n * 0.95))],
        }
    return stats


def monitor(source_code: str, run_id: int | None) -> dict:
    with get_db_connection() as conn:
        cursor = conn.cursor(dictionary=True)
        run = _select_run(cursor, run_id)
        if not run:
            cursor.close()
            return {
                "source_code": source_code,
                "run_id": run_id,
                "as_of_date": datetime.now(VN_TZ).date().isoformat(),
                "overall_status": "fail",
                "gates": {"run_found": {"status": "fail", "message": "Khong tim thay crawl_run"}},
            }
        rid = run["id"]

        cursor.execute(
            """
            SELECT
              COUNT(*) item_count,
              SUM(status='success') success_n,
              SUM(status='partial') partial_n,
              SUM(status='sold_out') sold_out_n,
              SUM(status='not_bookable') not_bookable_n,
              SUM(status='error') error_n,
              SUM(status IN ('queued','running')) unfinished_n,
              SUM(status IN ('success','partial')
                  AND parsed_options_count <> saved_options_count + duplicate_options_count
              ) recon_mismatch_n,
              SUM(status IN ('success','partial') AND requested_hotel_link IS NOT NULL AND (
                  requested_hotel_link NOT LIKE CONCAT('%checkin=', DATE_FORMAT(checkin_date,'%Y-%m-%d'), '%')
                  OR requested_hotel_link NOT LIKE CONCAT('%checkout=', DATE_FORMAT(checkout_date,'%Y-%m-%d'), '%')
              )) url_mismatch_n,
              SUM(checkout_date <> DATE_ADD(checkin_date, INTERVAL 1 DAY)) checkout_mismatch_n,
              COUNT(DISTINCT source_link_hash) hotel_count
            FROM crawl_run_items WHERE crawl_run_id=%s
            """,
            (rid,),
        )
        items = cursor.fetchone()

        cursor.execute(
            """
            SELECT last_error_code, COUNT(*) n
            FROM crawl_run_items
            WHERE crawl_run_id=%s AND status='error'
            GROUP BY last_error_code
            """,
            (rid,),
        )
        error_rows = cursor.fetchall()

        cursor.execute(
            """
            SELECT hotel_name_hint, checkin_date FROM crawl_run_items
            WHERE crawl_run_id=%s AND last_error_code='dead_link'
            """,
            (rid,),
        )
        dead_link_rows = cursor.fetchall()

        cursor.execute(
            """
            SELECT COUNT(DISTINCT source_link_hash) n
            FROM crawl_run_items
            WHERE crawl_run_id = (
              SELECT id FROM crawl_runs WHERE id < %s AND status='completed' ORDER BY id DESC LIMIT 1
            )
            """,
            (rid,),
        )
        prev_row = cursor.fetchone()
        prev_hotel_count = int(prev_row["n"]) if prev_row and prev_row["n"] is not None else None

        cursor.execute(
            """
            SELECT
              COUNT(*) obs_count,
              SUM(is_sold_out=0 AND price_per_night IS NULL) missing_price_n,
              SUM(is_sold_out=0 AND room_identity_key IS NULL) missing_room_key_n,
              SUM(is_anomaly=1) anomaly_n,
              SUM(is_sold_out=0 AND price_per_night IS NOT NULL
                  AND (price_per_night < %s OR price_per_night > %s)) extreme_price_n
            FROM price_observations WHERE crawl_run_id=%s
            """,
            (PRICE_FLOOR_VND, PRICE_CEILING_VND, rid),
        )
        prices = cursor.fetchone()

        cursor.execute(
            """
            SELECT hotels.city, price_observations.price_per_night
            FROM price_observations
            JOIN hotels ON hotels.hotel_id = price_observations.hotel_id
            WHERE price_observations.crawl_run_id=%s AND price_observations.is_sold_out=0
              AND price_observations.price_per_night IS NOT NULL
            """,
            (rid,),
        )
        price_rows = cursor.fetchall()
        cursor.close()

    total_declared = int(run["total"] or 0)
    processed = int(run["processed"] or 0)
    item_count = int(items["item_count"] or 0)
    unfinished_n = int(items["unfinished_n"] or 0)

    started_at = run["started_at"]
    now_utc = datetime.now(timezone.utc)
    stuck = False
    if run["status"] in ("queued", "running") and started_at:
        stuck = (now_utc - started_at.replace(tzinfo=timezone.utc)).total_seconds() > STUCK_AFTER_HOURS * 3600

    gates: dict[str, dict] = {}

    gates["run_completed"] = {
        "status": "fail" if stuck else ("warn" if run["status"] != "completed" else "pass"),
        "run_status": run["status"],
        "started_at": str(started_at),
        "finished_at": str(run["finished_at"]),
    }

    gates["item_accounting"] = {
        "status": "pass" if (item_count == total_declared and processed == total_declared and unfinished_n == 0) else "warn",
        "declared_total": total_declared,
        "actual_item_rows": item_count,
        "processed_field": processed,
        "unfinished_items": unfinished_n,
    }

    distinct_hotels = int(items["hotel_count"] or 0)
    checkins_per_hotel = round(item_count / distinct_hotels, 2) if distinct_hotels else 0
    # So voi run completed gan nhat truoc do trong cung DB, KHONG so voi COUNT(*) FROM hotels:
    # bang hotels tich luy ca hotel pilot/preflight da bi loai khoi cohort hien tai (vd "sen",
    # cac ung vien bi swap truoc khi khoa cohort 2026-08-17) nen khong phai ground truth dung.
    if prev_hotel_count is None:
        coverage_status = "pass"
        coverage_note = "Chua co run completed truoc do de doi chieu (co the la run dau tien)."
    elif distinct_hotels == prev_hotel_count:
        coverage_status = "pass"
        coverage_note = None
    else:
        coverage_status = "warn"
        coverage_note = (
            "So hotel khac run completed gan nhat truoc do - kiem tra upload nham file hoac "
            "attrition/cohort thay doi chua duoc ghi nhan (vd giong Mac Valley, xem CLAUDE.md muc 2)."
        )
    gates["hotel_coverage"] = {
        "status": coverage_status,
        "distinct_hotels_this_run": distinct_hotels,
        "distinct_hotels_previous_completed_run": prev_hotel_count,
        "checkins_per_hotel_this_run": checkins_per_hotel,
        **({"note": coverage_note} if coverage_note else {}),
    }

    recon_mismatch = int(items["recon_mismatch_n"] or 0)
    gates["option_reconciliation"] = {
        "status": "pass" if recon_mismatch == 0 else "fail",
        "mismatch_count": recon_mismatch,
    }

    url_mismatch = int(items["url_mismatch_n"] or 0)
    checkout_mismatch = int(items["checkout_mismatch_n"] or 0)
    gates["url_date_invariant"] = {
        "status": "pass" if url_mismatch == 0 and checkout_mismatch == 0 else "fail",
        "requested_url_mismatch": url_mismatch,
        "checkout_mismatch": checkout_mismatch,
    }

    error_n = int(items["error_n"] or 0)
    error_taxonomy = {(row["last_error_code"] or "unknown"): int(row["n"]) for row in error_rows}
    dead_link_list = [f'{r["hotel_name_hint"]}@{r["checkin_date"]}' for r in dead_link_rows]
    gates["error_taxonomy"] = {
        "status": "warn" if (error_n > 0 or dead_link_list) else "pass",
        "error_count": error_n,
        "error_rate": round(error_n / item_count, 4) if item_count else 0,
        "by_code": error_taxonomy,
        "dead_link_items_needs_manual_review": dead_link_list,
    }
    if dead_link_list:
        gates["error_taxonomy"]["note"] = (
            "Bug circuit-break dead_link CHUA vá (xem CLAUDE.md muc 4.5) - khong tu ket luan "
            "hotel het hoat dong chi tu 1 lan nay."
        )

    missing_price = int(prices["missing_price_n"] or 0)
    missing_room_key = int(prices["missing_room_key_n"] or 0)
    gates["missingness"] = {
        "status": "fail" if missing_price > 0 else ("warn" if missing_room_key > 0 else "pass"),
        "missing_price_on_available": missing_price,
        "missing_room_identity_key": missing_room_key,
        "observation_count": int(prices["obs_count"] or 0),
    }

    anomaly_n = int(prices["anomaly_n"] or 0)
    extreme_n = int(prices["extreme_price_n"] or 0)
    gates["price_sanity"] = {
        "status": "fail" if extreme_n > 0 else ("warn" if anomaly_n > 0 else "pass"),
        "is_anomaly_flagged": anomaly_n,
        "outside_floor_ceiling_50k_50m_vnd": extreme_n,
        "by_city": _city_price_stats(price_rows),
    }

    overall = max((g["status"] for g in gates.values()), key=lambda s: _STATUS_RANK[s])

    as_of_source = started_at or run["finished_at"] or now_utc
    if as_of_source.tzinfo is None:
        as_of_source = as_of_source.replace(tzinfo=timezone.utc)
    as_of_date = as_of_source.astimezone(VN_TZ).date().isoformat()

    return {
        "source_code": source_code,
        "run_id": rid,
        "as_of_date": as_of_date,
        "overall_status": overall,
        "gates": gates,
    }


def _save_report(report: dict) -> Path:
    REPORT_DIR.mkdir(parents=True, exist_ok=True)
    path = REPORT_DIR / f'{report["source_code"]}_{report["as_of_date"]}.json'
    path.write_text(json.dumps(report, ensure_ascii=False, indent=2, default=str), encoding="utf-8")
    return path


def main() -> None:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--source-code", required=True,
        help="Nhan cho report (local_primary | vps | local_aux) - khong loc SQL, chi anh huong ten file luu",
    )
    selection = parser.add_mutually_exclusive_group(required=True)
    selection.add_argument("--run-id", type=int)
    selection.add_argument("--latest", action="store_true")
    args = parser.parse_args()

    report = monitor(args.source_code, args.run_id)
    path = _save_report(report)

    label = {"pass": "PASS", "warn": "WARN", "fail": "FAIL"}[report["overall_status"]]
    print(f'[{label}] source={report["source_code"]} run_id={report.get("run_id")} as_of={report.get("as_of_date")}')
    for name, gate in report.get("gates", {}).items():
        if gate["status"] != "pass":
            print(f'  - {gate["status"].upper()} {name}')
    print()
    print(json.dumps(report, ensure_ascii=False, indent=2, default=str))
    print(f"\nSaved: {path}")
    raise SystemExit(0 if report["overall_status"] != "fail" else 1)


if __name__ == "__main__":
    main()

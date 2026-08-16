"""Evaluate fixed-cohort pilot runs before expanding from 10 to 50/272 hotels.

Examples (from backend/):
    python scripts/audit_sampling_quality.py --run-ids 1 2 3 4 5 6
    python scripts/audit_sampling_quality.py --latest 6
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from datetime import timezone
from zoneinfo import ZoneInfo

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.core.database import get_db_connection


def _selected_runs(cursor, run_ids: list[int] | None, latest: int) -> list[dict]:
    if run_ids:
        placeholders = ",".join(["%s"] * len(run_ids))
        cursor.execute(
            f"SELECT * FROM crawl_runs WHERE id IN ({placeholders}) ORDER BY id", tuple(run_ids)
        )
    else:
        cursor.execute("SELECT * FROM crawl_runs ORDER BY id DESC LIMIT %s", (latest,))
    return list(cursor.fetchall())


def audit(run_ids: list[int] | None, latest: int) -> dict:
    with get_db_connection() as conn:
        cursor = conn.cursor(dictionary=True)
        runs = _selected_runs(cursor, run_ids, latest)
        ids = [run["id"] for run in runs]
        if not ids:
            return {"passed": False, "gates": {"runs_found": {"passed": False, "value": 0}}}
        placeholders = ",".join(["%s"] * len(ids))
        cursor.execute(
            f"""
            SELECT
              COUNT(*) item_count,
              SUM(status='error') technical_errors,
              SUM(last_error_code IN ('captcha','blocked')) blocked_items,
              SUM(status IN ('success','partial') AND (
                rejected_options_count>0 OR candidate_rate_count<>parsed_options_count
                OR parsed_options_count<>saved_options_count+duplicate_options_count
              )) incomplete_items,
              COUNT(DISTINCT source_link_hash) hotel_count
            FROM crawl_run_items WHERE crawl_run_id IN ({placeholders})
            """,
            tuple(ids),
        )
        items = cursor.fetchone()
        cursor.execute(
            """
            SELECT COUNT(*) approved,
              SUM(distinct_run_count >= 3 AND coverage >= 0.8
                  AND observation_count=distinct_item_count
                  AND checkin_date IS NOT NULL) valid_approved
            FROM hotel_reference_rooms WHERE status='approved'
            """
        )
        references = cursor.fetchone()
        cursor.execute(
            f"""
            SELECT COUNT(*) available_series,
              SUM(EXISTS(
                SELECT 1 FROM hotel_reference_rooms reference
                WHERE reference.hotel_id=series.hotel_id
                  AND reference.checkin_date=series.checkin_date
                  AND reference.status='approved'
              )) reference_ready_series
            FROM (
              SELECT DISTINCT po.hotel_id,po.checkin_date
              FROM price_observations po
              JOIN crawl_run_items item ON item.id=po.crawl_run_item_id
              WHERE po.crawl_run_id IN ({placeholders})
                AND item.status='success' AND po.is_sold_out=0
                AND po.room_identity_key IS NOT NULL
            ) series
            """,
            tuple(ids),
        )
        readiness = cursor.fetchone()
        cursor.close()

    source_hashes = {run.get("source_file_sha256") for run in runs}
    checkin_sets = {
        json.dumps(run.get("checkin_dates"), sort_keys=True, default=str) for run in runs
    }
    local_zone = ZoneInfo("Asia/Ho_Chi_Minh")
    off_schedule = []
    local_dates = set()
    for run in runs:
        started = run.get("started_at")
        if not started:
            off_schedule.append(run["id"])
            continue
        local = started.replace(tzinfo=timezone.utc).astimezone(local_zone)
        local_dates.add(local.date().isoformat())
        minute = local.hour * 60 + local.minute
        if not (21 * 60 + 30 <= minute <= 22 * 60 + 30):
            off_schedule.append(run["id"])

    scraper_versions = sorted({run.get("scraper_version") for run in runs if run.get("scraper_version")})
    available_series = int(readiness["available_series"] or 0)
    ready_series = int(readiness["reference_ready_series"] or 0)
    ready_ratio = ready_series / available_series if available_series else 0.0

    gates = {
        "six_distinct_real_days": {
            "passed": len(local_dates) >= 6,
            "value": sorted(local_dates),
            "required": 6,
        },
        "same_protocol_file": {
            "passed": len(source_hashes) == 1 and None not in source_hashes,
            "value": len(source_hashes),
        },
        "same_checkin_dates": {"passed": len(checkin_sets) == 1, "value": len(checkin_sets)},
        "parser_complete": {
            "passed": int(items["incomplete_items"] or 0) == 0,
            "incomplete_items": int(items["incomplete_items"] or 0),
        },
        "no_technical_errors": {
            "passed": int(items["technical_errors"] or 0) == 0,
            "technical_errors": int(items["technical_errors"] or 0),
        },
        "no_block_or_captcha": {
            "passed": int(items["blocked_items"] or 0) == 0,
            "blocked_items": int(items["blocked_items"] or 0),
        },
        "approved_references_valid": {
            "passed": (
                int(references["approved"] or 0) > 0
                and int(references["approved"] or 0) == int(references["valid_approved"] or 0)
            ),
            "approved": int(references["approved"] or 0),
            "valid_approved": int(references["valid_approved"] or 0),
        },
        "reference_ready_ratio": {
            "passed": available_series > 0 and ready_ratio >= 0.80,
            "ready_series": ready_series,
            "available_series": available_series,
            "ratio": round(ready_ratio, 4),
            "required": 0.80,
        },
    }
    warnings = []
    if off_schedule:
        warnings.append({
            "code": "off_schedule",
            "message": "Một số run nằm ngoài khung 22:00 ±30 phút; dữ liệu vẫn hợp lệ để kiểm tra parser/reference.",
            "run_ids": off_schedule,
        })
    if len(scraper_versions) > 1:
        warnings.append({
            "code": "mixed_scraper_versions",
            "message": "Pilot đi qua nhiều phiên bản scraper; ghi nhận để phân tầng khi phân tích biến động.",
            "versions": scraper_versions,
        })
    return {
        "passed": all(gate["passed"] for gate in gates.values()),
        "run_ids": ids,
        "hotel_count": int(items["hotel_count"] or 0),
        "item_count": int(items["item_count"] or 0),
        "gates": gates,
        "warnings": warnings,
    }


def main() -> None:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    parser = argparse.ArgumentParser()
    selection = parser.add_mutually_exclusive_group()
    selection.add_argument("--run-ids", type=int, nargs="+")
    selection.add_argument("--latest", type=int, default=6)
    args = parser.parse_args()
    report = audit(args.run_ids, args.latest)
    print(json.dumps(report, ensure_ascii=False, indent=2, default=str))
    raise SystemExit(0 if report["passed"] else 1)


if __name__ == "__main__":
    main()

"""Read-only CLI for the current crawl run progress."""
import argparse
import os
import sys
from datetime import datetime, timezone
from zoneinfo import ZoneInfo

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.core.config import settings
from app.core.database import get_db_connection


TERMINAL_ITEM_STATUSES = ("success", "partial", "sold_out", "not_bookable", "error")


def format_vietnam_time(value: datetime | None) -> str:
    if value is None:
        return "chưa có"
    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    local_value = value.astimezone(ZoneInfo(settings.DISPLAY_TIMEZONE))
    return local_value.strftime("%d/%m/%Y %H:%M:%S ICT")


def fetch_progress(run_id: int | None = None) -> tuple[dict | None, dict | None]:
    with get_db_connection() as conn:
        cursor = conn.cursor(dictionary=True)
        try:
            if run_id is None:
                cursor.execute(
                    """
                    SELECT id,status,total,processed
                    FROM crawl_runs
                    ORDER BY id DESC
                    LIMIT 1
                    """
                )
            else:
                cursor.execute(
                    """
                    SELECT id,status,total,processed
                    FROM crawl_runs
                    WHERE id=%s
                    """,
                    (run_id,),
                )
            run = cursor.fetchone()
            if run is None:
                return None, None

            placeholders = ",".join(["%s"] * len(TERMINAL_ITEM_STATUSES))
            cursor.execute(
                f"""
                SELECT id,status,COALESCE(finished_at,updated_at) AS crawled_at
                FROM crawl_run_items
                WHERE crawl_run_id=%s
                  AND status IN ({placeholders})
                ORDER BY COALESCE(finished_at,updated_at) DESC,id DESC
                LIMIT 1
                """,
                (run["id"], *TERMINAL_ITEM_STATUSES),
            )
            return run, cursor.fetchone()
        finally:
            cursor.close()
            # End the short read transaction before returning the connection to
            # the pool. The monitor never acquires locks or mutates crawl state.
            conn.rollback()


def render_progress(run: dict, latest_item: dict | None) -> str:
    total = int(run.get("total") or 0)
    processed = int(run.get("processed") or 0)
    percent = (processed / total * 100) if total else 0.0
    progress_line = (
        f"Run #{run['id']}: {processed}/{total} ({percent:.1f}%) — {run['status']}"
    )
    if latest_item is None:
        latest_line = "Item gần nhất: chưa có item nào hoàn tất"
    else:
        latest_line = (
            f"Item gần nhất: #{latest_item['id']} lúc "
            f"{format_vietnam_time(latest_item.get('crawled_at'))}"
        )
    return f"{progress_line}\n{latest_line}"


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Xem tiến độ crawl và thời gian item hoàn tất gần nhất (giờ Việt Nam)."
    )
    parser.add_argument(
        "--run-id",
        type=int,
        help="Run cần kiểm tra; mặc định lấy run mới nhất.",
    )
    args = parser.parse_args()

    run, latest_item = fetch_progress(args.run_id)
    if run is None:
        target = f" #{args.run_id}" if args.run_id is not None else ""
        print(f"Không tìm thấy run{target}.")
        return 1
    print(render_progress(run, latest_item))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

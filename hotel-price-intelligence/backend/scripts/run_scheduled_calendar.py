"""Create and monitor one calendar-driven scheduled crawl.

The process is intentionally independent from FastAPI: it reads the daily plan,
creates one durable MySQL run with trigger_type=scheduled, ensures the worker is
available, and writes the terminal aggregate back to the one log row for that
crawl date.
"""

from __future__ import annotations

import argparse
import hashlib
import os
import shutil
import subprocess
import sys
import time
from datetime import date, datetime
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

from openpyxl import load_workbook

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.core.config import settings  # noqa: E402
from app.core.database import get_db_connection  # noqa: E402
from app.database.durable import DurableQueueRepository  # noqa: E402
from app.database.repositories import CrawlRunRepository  # noqa: E402
from app.scraper.data_contract import current_git_commit, default_crawl_context  # noqa: E402
from app.scraper.job_runner import inspect_hotel_list_excel, parse_hotel_list_excel  # noqa: E402

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")
if hasattr(sys.stderr, "reconfigure"):
    sys.stderr.reconfigure(encoding="utf-8")


PLAN_SHEET = "DAILY_CRAWL_PLAN"
LOG_SHEET = "CRAWL_LOG"
PLAN_HEADER_ROW = 3
LOG_HEADER_ROW = 3
CHECKIN_HEADERS = [
    "N1 gần", "N2 gần", "N3 gần", "N4 gần", "N5 gần", "N6 gần",
    "N7 gần", "N8 gần", "N9 gần", "F1 xa", "F2 xa", "F3 xa",
]
CHECKIN_COUNT = len(CHECKIN_HEADERS)
TERMINAL_RUN_STATUS = "completed"


def _as_date(value: Any) -> date | None:
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    if isinstance(value, str):
        for fmt in ("%Y-%m-%d", "%d/%m/%Y"):
            try:
                return datetime.strptime(value.strip(), fmt).date()
            except ValueError:
                continue
    return None


def _headers(sheet, row: int) -> dict[str, int]:
    return {str(cell.value).strip(): cell.column for cell in sheet[row] if cell.value is not None}


def _find_row(sheet, header_row: int, date_column: int, target: date) -> int:
    for row in range(header_row + 1, sheet.max_row + 1):
        if _as_date(sheet.cell(row, date_column).value) == target:
            return row
    raise ValueError(f"Không tìm thấy ngày {target.isoformat()} trong sheet {sheet.title}")


def _save_with_retry(workbook, path: Path, retries: int = 12, delay_seconds: int = 10) -> None:
    temp_path = path.with_name(f".{path.stem}.tmp.xlsx")
    last_error: Exception | None = None
    for _ in range(retries):
        try:
            workbook.save(temp_path)
            os.replace(temp_path, path)
            return
        except (PermissionError, OSError) as exc:
            last_error = exc
            time.sleep(delay_seconds)
    if temp_path.exists():
        temp_path.unlink(missing_ok=True)
    raise RuntimeError(f"Không thể lưu calendar workbook sau {retries} lần thử: {last_error}")


def _load_calendar(calendar_path: Path, target: date):
    workbook = load_workbook(calendar_path)
    plan = workbook[PLAN_SHEET]
    log = workbook[LOG_SHEET]
    plan_headers = _headers(plan, PLAN_HEADER_ROW)
    log_headers = _headers(log, LOG_HEADER_ROW)
    plan_row = _find_row(plan, PLAN_HEADER_ROW, plan_headers["Crawl date"], target)
    log_row = _find_row(log, LOG_HEADER_ROW, log_headers["Crawl date"], target)
    checkins = []
    for header in CHECKIN_HEADERS:
        value = _as_date(plan.cell(plan_row, plan_headers[header]).value)
        if value is None or value < target:
            raise ValueError(f"Check-in không hợp lệ tại {header}: {value}")
        checkins.append(value.isoformat())
    if len(set(checkins)) != CHECKIN_COUNT:
        raise ValueError(f"Lịch ngày hôm nay không có đúng {CHECKIN_COUNT} check-in khác nhau")
    return workbook, plan, log, plan_headers, log_headers, plan_row, log_row, checkins


def _write_log(log, headers: dict[str, int], row: int, values: dict[str, Any]) -> None:
    for header, value in values.items():
        log.cell(row, headers[header]).value = value


def _ensure_worker(queue_repo: DurableQueueRepository, backend_root: Path) -> None:
    if queue_repo.worker_health().get("online"):
        return
    creation_flags = subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0
    subprocess.Popen(
        [sys.executable, str(backend_root / "scripts" / "run_worker.py")],
        cwd=backend_root,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        creationflags=creation_flags,
    )
    for _ in range(30):
        time.sleep(2)
        if queue_repo.worker_health().get("online"):
            return
    raise RuntimeError("Worker không online sau 60 giây")


def _prepare_source(hotel_file: Path) -> tuple[Path, str, int, list[tuple]]:
    content = hotel_file.read_bytes()
    digest = hashlib.sha256(content).hexdigest()
    upload_root = (Path(__file__).resolve().parents[1] / settings.UPLOAD_DIR).resolve()
    upload_root.mkdir(parents=True, exist_ok=True)
    saved_path = upload_root / f"{digest[:16]}_{hotel_file.name}"
    if not saved_path.exists():
        shutil.copy2(hotel_file, saved_path)
    preflight = inspect_hotel_list_excel(str(saved_path))
    if int(preflight.get("valid_links", 0)) <= 0:
        raise ValueError("File khách sạn không có Booking.com link hợp lệ")
    links = parse_hotel_list_excel(str(saved_path))
    return saved_path, digest, len(content), links


def _valid_record_count(run_id: int) -> int:
    with get_db_connection() as conn:
        cursor = conn.cursor()
        cursor.execute("SELECT COUNT(*) FROM price_observations WHERE crawl_run_id=%s", (run_id,))
        count = int(cursor.fetchone()[0] or 0)
        cursor.close()
        return count


def run(calendar_path: Path, hotel_file: Path, target: date, poll_seconds: int) -> int:
    timezone = ZoneInfo(settings.DISPLAY_TIMEZONE)
    backend_root = Path(__file__).resolve().parents[1]
    queue_repo = DurableQueueRepository()
    run_repo = CrawlRunRepository()

    workbook, _, log, _, log_headers, _, log_row, checkins = _load_calendar(calendar_path, target)
    current_status = str(log.cell(log_row, log_headers["Status"]).value or "").strip()
    existing_run = log.cell(log_row, log_headers["Run IDs"]).value
    if current_status in {"Hoàn thành", "Hoàn thành có lỗi"}:
        print(f"Ngày {target.isoformat()} đã hoàn thành; không tạo run trùng.")
        return 0

    run_id: int
    if existing_run:
        try:
            run_id = int(str(existing_run).split(",")[0].strip())
        except ValueError as exc:
            raise ValueError(f"Run IDs không hợp lệ: {existing_run}") from exc
    else:
        saved_path, digest, source_size, links = _prepare_source(hotel_file)
        _ensure_worker(queue_repo, backend_root)
        run_id = queue_repo.create_run_with_items(
            trigger_type="scheduled",
            source_file=str(saved_path),
            source_original_filename=hotel_file.name,
            source_file_sha256=digest,
            source_file_size=source_size,
            date_mode="explicit",
            checkin_dates=checkins,
            hotel_links=links,
            crawl_context=default_crawl_context(False),
            save_artifacts=False,
            scraper_version=settings.SCRAPER_VERSION,
            selector_version=settings.SELECTOR_VERSION,
            git_commit=current_git_commit(),
        )
        now = datetime.now(timezone).replace(tzinfo=None)
        _write_log(log, log_headers, log_row, {
            "Status": "Đang chạy",
            "Run IDs": str(run_id),
            "Started at": now,
            "Check-in count": CHECKIN_COUNT,
            "Notes": "Scheduled crawl đã tạo durable run; save_artifacts=false",
        })
        _save_with_retry(workbook, calendar_path)

    while True:
        result = run_repo.get_by_id(run_id)
        if result is None:
            raise RuntimeError(f"Không tìm thấy crawl run {run_id}")
        if result.get("status") == TERMINAL_RUN_STATUS:
            break
        time.sleep(max(10, poll_seconds))

    workbook, _, log, _, log_headers, _, log_row, _ = _load_calendar(calendar_path, target)
    partial_count = int(result.get("partial_count") or 0)
    error_count = int(result.get("error_count") or 0)
    final_status = "Hoàn thành có lỗi" if partial_count > 0 or error_count > 0 else "Hoàn thành"
    _write_log(log, log_headers, log_row, {
        "Status": final_status,
        "Run IDs": str(run_id),
        "Finished at": datetime.now(timezone).replace(tzinfo=None),
        "Total items": int(result.get("total") or 0),
        "Processed": int(result.get("processed") or 0),
        "Success": int(result.get("success_count") or 0),
        "Partial": partial_count,
        "Sold out": int(result.get("sold_out_count") or 0),
        "Not bookable": int(result.get("not_bookable_count") or 0),
        "Error": error_count,
        "Valid records": _valid_record_count(run_id),
        "Check-in count": CHECKIN_COUNT,
        "Notes": f"Scheduled run {run_id} đã đạt terminal state",
    })
    _save_with_retry(workbook, calendar_path)
    print(f"Ngày {target.isoformat()} hoàn tất với status={final_status}, run_id={run_id}")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description="Run one daily crawl from crawl_sampling_master.xlsx")
    parser.add_argument("--calendar", type=Path, required=True)
    parser.add_argument("--hotel-file", type=Path, required=True)
    parser.add_argument("--date", help="YYYY-MM-DD; mặc định là ngày hiện tại theo DISPLAY_TIMEZONE")
    parser.add_argument("--poll-seconds", type=int, default=60)
    args = parser.parse_args()

    timezone = ZoneInfo(settings.DISPLAY_TIMEZONE)
    target = datetime.strptime(args.date, "%Y-%m-%d").date() if args.date else datetime.now(timezone).date()
    calendar_path = args.calendar.resolve()
    hotel_file = args.hotel_file.resolve()
    if not calendar_path.exists():
        raise FileNotFoundError(calendar_path)
    if not hotel_file.exists():
        raise FileNotFoundError(hotel_file)

    try:
        return run(calendar_path, hotel_file, target, args.poll_seconds)
    except Exception as exc:
        try:
            workbook, _, log, _, log_headers, _, log_row, _ = _load_calendar(calendar_path, target)
            existing_run = log.cell(log_row, log_headers["Run IDs"]).value
            failure_values = {
                "Status": "Đang chạy" if existing_run else "Thất bại",
                "Notes": (
                    f"Monitor gặp lỗi nhưng run {existing_run} đã tồn tại; cần tiếp tục kiểm tra: {type(exc).__name__}: {exc}"
                    if existing_run else f"{type(exc).__name__}: {exc}"
                ),
            }
            if not existing_run:
                failure_values["Finished at"] = datetime.now(timezone).replace(tzinfo=None)
            _write_log(log, log_headers, log_row, failure_values)
            _save_with_retry(workbook, calendar_path)
        except Exception:
            pass
        raise


if __name__ == "__main__":
    raise SystemExit(main())

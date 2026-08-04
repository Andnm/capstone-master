"""Hàm chạy job dùng chung cho cả API (upload thủ công) lẫn cron (scripts/run_crawl.py).
Không viết 2 bộ logic riêng cho 'thủ công' và 'tự động' (CLAUDE.md muc 4.6).
"""
import random
import time
from datetime import datetime, timedelta
from typing import List, Optional, Tuple

import openpyxl

from app.core.config import settings
from app.database.repositories import (
    CrawlRunItemRepository,
    CrawlRunRepository,
    HotelRepository,
    PriceObservationRepository,
)
from app.scraper.booking_scraper import scrape_booking_hotel
from app.scraper.transform import build_hotel_upsert, build_price_observations
from app.scraper.url_utils import set_checkin_checkout

run_repo = CrawlRunRepository()
run_item_repo = CrawlRunItemRepository()
hotel_repo = HotelRepository()
price_repo = PriceObservationRepository()

_STAY_NIGHTS = 1  # luôn cào 1 đêm - xem CLAUDE.md muc 4.6/6


def parse_hotel_list_excel(file_path: str) -> List[Tuple[str, Optional[str], Optional[str]]]:
    """Đọc file Excel (nhiều sheet = nhiều market/city), cột A = tên, cột B = link.
    Trả về list (hotel_link, hotel_name_hint, market_hint).
    """
    links: List[Tuple[str, Optional[str], Optional[str]]] = []
    wb = openpyxl.load_workbook(file_path, data_only=True)
    for sheet_name in wb.sheetnames:
        ws = wb[sheet_name]
        for row_idx in range(2, ws.max_row + 1):  # bỏ header
            cell_a = ws.cell(row=row_idx, column=1)
            cell_b = ws.cell(row=row_idx, column=2)
            if not cell_b.value:
                continue
            link = str(cell_b.value).strip()
            name_hint = str(cell_a.value).strip() if cell_a.value else None
            if 'booking.com/hotel/' in link:
                links.append((link, name_hint, sheet_name))
    return links


def _drain_progress(run_id: int, processed: int, success: int, errors: int) -> None:
    run_repo.update_progress(run_id, processed, success, errors)


def _resolve_checkin_checkout_pairs(run: dict) -> List[Tuple[str, str]]:
    """Chế độ 'lead_time' (scheduled): checkin = hôm nay + từng mốc trong lead_time_buckets.
    Chế độ 'explicit' (thủ công): dùng đúng list ngày checkin do người dùng chọn lúc upload.
    Checkout luôn = checkin + 1 (luôn 1 đêm - CLAUDE.md muc 4.6/6), bất kể chế độ nào.
    """
    pairs: List[Tuple[str, str]] = []
    if run.get('date_mode') == 'explicit':
        for checkin_date in (run.get('checkin_dates') or []):
            checkin_dt = datetime.strptime(checkin_date, '%Y-%m-%d')
            checkout_date = (checkin_dt + timedelta(days=_STAY_NIGHTS)).strftime('%Y-%m-%d')
            pairs.append((checkin_date, checkout_date))
    else:
        buckets = [
            int(x) for x in (run.get('lead_time_buckets') or settings.DEFAULT_LEAD_TIME_BUCKETS).split(',')
        ]
        for lead_time in buckets:
            checkin_date = (datetime.now() + timedelta(days=lead_time)).strftime('%Y-%m-%d')
            checkout_date = (datetime.now() + timedelta(days=lead_time + _STAY_NIGHTS)).strftime('%Y-%m-%d')
            pairs.append((checkin_date, checkout_date))
    return pairs


def run_crawl_job(run_id: int) -> None:
    """Chạy 1 crawl_run đã được claim (status='running'). Cào toàn bộ hotel_list x các cặp
    checkin/checkout (tính theo date_mode), ghi kết quả vào DB, cập nhật progress liên tục.
    Không raise ra ngoài — lỗi tổng thể sẽ được ghi vào crawl_runs.status='failed'.
    """
    run = run_repo.get_by_id(run_id)
    if not run:
        return

    try:
        date_pairs = _resolve_checkin_checkout_pairs(run)
        source_file = run.get('source_file') or settings.DEFAULT_HOTEL_LIST_PATH
        hotel_list = parse_hotel_list_excel(source_file)

        total = len(hotel_list) * len(date_pairs)
        processed = 0
        success_count = 0
        error_count = 0
        run_repo.set_total(run_id, total)
        run_repo.update_progress(run_id, 0, 0, 0)

        for hotel_link, hotel_name_hint, market_hint in hotel_list:
            for checkin_date, checkout_date in date_pairs:
                url = set_checkin_checkout(hotel_link, checkin_date, checkout_date)

                observed_at = datetime.now()
                raw, error = scrape_booking_hotel(url, checkin_date, checkout_date)
                if error:
                    time.sleep(random.uniform(2, 4))
                    raw, error = scrape_booking_hotel(url, checkin_date, checkout_date)  # 1 lần retry

                item_status = 'error'
                item_error_message = error
                item_hotel_id = None
                item_hotel_name = None

                if error or not raw:
                    error_count += 1
                else:
                    hotel_dict = build_hotel_upsert(raw, hotel_link, market_hint)
                    if not hotel_dict:
                        error_count += 1
                        item_error_message = 'Không lấy được hotel_id từ link (URL không đúng định dạng Booking)'
                    else:
                        item_hotel_id = hotel_dict['hotel_id']
                        item_hotel_name = hotel_dict['name']
                        item_error_message = None
                        hotel_repo.upsert(hotel_dict)
                        records = build_price_observations(
                            raw, hotel_dict['hotel_id'], run_id, run['trigger_type'],
                            observed_at, checkin_date, checkout_date,
                        )
                        price_repo.insert_many(records)
                        item_status = 'sold_out' if raw.get('is_sold_out') else 'success'
                        success_count += 1

                # Lưu đúng URL Selenium đã mở (đã thay checkin/checkout thật), không phải link gốc trong Excel.
                run_item_repo.create(
                    crawl_run_id=run_id,
                    hotel_link=url,
                    hotel_name_hint=hotel_name_hint,
                    hotel_name=item_hotel_name,
                    hotel_id=item_hotel_id,
                    checkin_date=checkin_date,
                    status=item_status,
                    error_message=item_error_message,
                )

                processed += 1
                _drain_progress(run_id, processed, success_count, error_count)
                time.sleep(random.uniform(3, 6))  # delay ngẫu nhiên, tôn trọng nguồn (CLAUDE.md 4.2.e)

        run_repo.mark_completed(run_id)

    except Exception as e:
        run_repo.mark_failed(run_id, str(e))


def drain_queue() -> None:
    """Claim và chạy lần lượt mọi crawl_run đang 'queued' cho tới khi hết hàng đợi.
    Dùng chung cho cron (scripts/run_crawl.py) và job nền được API spawn khi upload.
    """
    run_repo.recover_stale_running()
    while True:
        run_id = run_repo.try_claim_next_queued()
        if run_id is None:
            break
        run_crawl_job(run_id)

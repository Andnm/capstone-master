"""Entrypoint cho cron/systemd timer. Dùng chung logic (drain_queue) với job do API upload
kích hoạt - không viết 2 bộ logic riêng (CLAUDE.md muc 4.6).

Chạy: python scripts/run_crawl.py
Nếu không có job nào trong hàng đợi, script tự tạo 1 job 'scheduled' dùng danh sách khách sạn
mặc định (settings.DEFAULT_HOTEL_LIST_PATH) rồi mới xử lý hàng đợi.

Cấu hình cron vd (mỗi 6 tiếng):
    0 */6 * * * cd /path/to/backend && venv/bin/python scripts/run_crawl.py >> logs/cron.log 2>&1
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.core.config import settings
from app.database.repositories import CrawlRunRepository
from app.scraper.job_runner import drain_queue

run_repo = CrawlRunRepository()


def main():
    run_repo.recover_stale_running()

    if not run_repo.has_running():
        # Không tự tạo job scheduled nếu đã có job queued sẵn (vd job thủ công đang chờ) -
        # để job thủ công được ưu tiên chạy trước theo đúng thứ tự FIFO.
        if not run_repo.has_queued():
            if os.path.exists(settings.DEFAULT_HOTEL_LIST_PATH):
                run_repo.create(
                    trigger_type='scheduled',
                    source_file=None,  # None -> job_runner dùng DEFAULT_HOTEL_LIST_PATH
                    lead_time_buckets=settings.DEFAULT_LEAD_TIME_BUCKETS,
                    total=0,
                )
            else:
                print(f"[run_crawl] Không thấy {settings.DEFAULT_HOTEL_LIST_PATH}, bỏ qua tạo job scheduled.")

    drain_queue()
    print("[run_crawl] Hàng đợi đã xử lý xong, không còn job 'queued'.")


if __name__ == "__main__":
    main()

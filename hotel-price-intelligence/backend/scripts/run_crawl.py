"""One-shot worker cho job đã được tạo thủ công; không tự tạo lịch/job mới."""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.scraper.worker import CrawlWorker


def main():
    worker = CrawlWorker()
    worker.run_until_empty()
    print("[run_crawl] Đã xử lý hết item đang queued.")


if __name__ == "__main__":
    main()

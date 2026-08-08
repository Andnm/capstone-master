"""Worker thủ công: chỉ xử lý job do UI/API tạo, không tự sinh lịch crawl."""
import os
import sys

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.scraper.retention import cleanup_files
from app.scraper.worker import CrawlWorker


def main():
    cleanup_files(apply=True)
    worker = CrawlWorker()
    print(f"[worker] online: {worker.worker_id}")
    worker.run_forever()


if __name__ == "__main__":
    main()

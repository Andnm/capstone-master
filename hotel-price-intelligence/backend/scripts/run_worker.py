"""Worker thủ công: chỉ xử lý job do UI/API tạo, không tự sinh lịch crawl."""
import argparse
import os
import sys
from pathlib import Path

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.scraper.retention import cleanup_files
from app.scraper.worker import CrawlWorker
from app.scraper.worker_supervisor import supervise_worker


def run_child():
    cleanup_files(apply=True)
    worker = CrawlWorker()
    print(f"[worker] online: {worker.worker_id}", flush=True)
    worker.run_forever()


def main():
    parser = argparse.ArgumentParser(description="Run the durable crawler worker")
    parser.add_argument(
        "--child",
        action="store_true",
        help=argparse.SUPPRESS,
    )
    args = parser.parse_args()
    if args.child:
        run_child()
        return 0

    worker_script = Path(__file__).resolve()
    return supervise_worker(worker_script, worker_script.parents[1])


if __name__ == "__main__":
    raise SystemExit(main())

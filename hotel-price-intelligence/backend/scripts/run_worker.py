"""Worker thủ công: chỉ xử lý job do UI/API tạo, không tự sinh lịch crawl."""
import argparse
import os
import sys

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.scraper.retention import cleanup_files
from app.scraper.worker import CrawlWorker


def build_parser():
    parser = argparse.ArgumentParser()
    parser.add_argument("--run-id", type=int, help="Chỉ xử lý item thuộc đúng crawl run này")
    parser.add_argument("--worker-id", help="Worker ID cố định để monitor theo dõi đúng process")
    return parser


def main():
    args = build_parser().parse_args()
    cleanup_files(apply=True)
    worker = CrawlWorker(worker_id=args.worker_id, run_id=args.run_id)
    scope = f" run_id={args.run_id}" if args.run_id is not None else ""
    print(f"[worker] online: {worker.worker_id}{scope}")
    worker.run_forever()


if __name__ == "__main__":
    main()

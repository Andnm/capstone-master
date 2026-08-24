"""Xem trước hoặc áp dụng retention cleanup.

python scripts/cleanup_files.py --dry-run
python scripts/cleanup_files.py --apply
"""
import argparse
import os
import sys

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.scraper.retention import cleanup_files


def main():
    parser = argparse.ArgumentParser()
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--dry-run", action="store_true")
    group.add_argument("--apply", action="store_true")
    args = parser.parse_args()
    rows = cleanup_files(apply=args.apply)
    for row in rows:
        print(f"{row['action']}: {row['type']} | {row['path']}")
    print(f"Tổng file phù hợp retention: {len(rows)}")


if __name__ == "__main__":
    main()

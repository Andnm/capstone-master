"""Promote 1 batch PASS (muc 3a buoc 17): atomic replace outputs/warehouse/warehouse_current.json.

Tu kiem lai status + source_manifest_sha256 + toan bo validation tai thoi diem promote. KHONG dung trong
rehearsal. Chi chay sau final review va khi nguoi van hanh dong y.

Chay (tu backend/):
    python scripts/promote_warehouse.py --database warehouse_20260916_2src --batch-id b20260916120000
"""
from __future__ import annotations

import argparse
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.warehouse.errors import WarehouseError
from app.warehouse.promote import promote_warehouse


def main() -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    parser = argparse.ArgumentParser(description="Promote warehouse batch.")
    parser.add_argument("--database", required=True)
    parser.add_argument("--batch-id", required=True)
    args = parser.parse_args()
    try:
        result = promote_warehouse(args.database, args.batch_id)
    except WarehouseError as exc:
        print(f"FAIL: {exc}", file=sys.stderr)
        return 1
    print(result)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

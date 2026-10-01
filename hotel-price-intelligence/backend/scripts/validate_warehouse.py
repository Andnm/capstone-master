"""Validate 1 batch warehouse (muc 17) - chi DOC. Re-verify source_manifest_sha256 truoc tien (muc 16).

Chay (tu backend/):
    python scripts/validate_warehouse.py --database warehouse_20260916_2src --batch-id b20260916120000
"""
from __future__ import annotations

import argparse
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.warehouse.connection import warehouse_connection
from app.warehouse.errors import WarehouseError
from app.warehouse.naming import require_warehouse_database
from app.warehouse.validation import validate_warehouse


def main() -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    parser = argparse.ArgumentParser(description="Validate warehouse batch.")
    parser.add_argument("--database", required=True)
    parser.add_argument("--batch-id", required=True)
    parser.add_argument("--json", action="store_true", help="in toan bo report dang JSON")
    args = parser.parse_args()
    try:
        with warehouse_connection(require_warehouse_database(args.database)) as wh:
            report = validate_warehouse(wh, batch_id=args.batch_id)
    except WarehouseError as exc:
        print(f"FAIL: {exc}", file=sys.stderr)
        return 1
    if args.json:
        print(json.dumps(report, ensure_ascii=False, indent=2, default=str))
    else:
        for check in report["checks"]:
            print(f"[{'OK ' if check['ok'] else 'FAIL'}] {check['name']}: {check['detail']}")
    print("KET QUA:", "PASS" if report["ok"] else "FAIL")
    return 0 if report["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())

"""Rebuild full-history reference cho 1 batch, ROI khoi `build_warehouse` (spec muc 10, muc 18).

Duong nay fail-closed 5 lop TRUOC moi write (chi tiet o `app/warehouse/rebuild_references.py`):
batch phai ton tai va dung warehouse; KHONG duoc la warehouse dang duoc `warehouse_current.json` tro
toi; re-verify `source_manifest_sha256`; `etl_config()` cua code dang chay phai TRUNG hash da pin trong
batch; va rebuild chi duoc commit khi semantic checksum 2 bang reference trung ban da pin luc build VA
`validate_warehouse` PASS. Lech bat ky dieu nao -> rollback, khong ghi gi.

Chay (tu backend/):
    python scripts/build_warehouse_references.py --database warehouse_20260916_2src \\
        --batch-id b20260916120000 --mode full-history
"""
from __future__ import annotations

import argparse
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.warehouse.errors import WarehouseError
from app.warehouse.rebuild_references import rebuild_full_history_references


def main() -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    parser = argparse.ArgumentParser(description="Rebuild full-history reference (fail-closed).")
    parser.add_argument("--database", required=True)
    parser.add_argument("--batch-id", required=True)
    parser.add_argument("--mode", required=True, choices=["full-history"])
    args = parser.parse_args()
    try:
        result = rebuild_full_history_references(args.database, args.batch_id)
    except WarehouseError as exc:
        print(f"FAIL: {type(exc).__name__}: {exc}", file=sys.stderr)
        return 1
    print(result)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

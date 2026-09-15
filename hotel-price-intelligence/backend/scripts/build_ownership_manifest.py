"""Sinh ownership manifest phang tu N workbook lich (WAREHOUSE_EDA_ML_SPEC.md muc 8).

Ket qua: 1 file JSON gom cac dong `(owner_source, crawl_date, schedule_slot, checkin_date)` va
`ownership_manifest_sha256` tinh tu chinh noi dung cac dong (khong tu tham chieu envelope).

FAIL CUNG neu 2 nguon cung claim 1 cap `(crawl_date, checkin_date)` - muc 8 khong cho phep
"conflicting ownership" ton tai trong manifest; nguoi van hanh phai quyet dinh chu so huu truoc.

Chay (tu backend/, duong dan tuong doi goc repo D:\\MSE\\CAPSTONE):
    python scripts/build_ownership_manifest.py \\
        --workbook local_primary=../outputs/crawl-data-planner-20260816/crawl_sampling_master.xlsx \\
        --workbook vps=../outputs/vps-crawl-planner-20260823/vps_crawl_sampling_master.xlsx \\
        --out ../data/warehouse/ownership_manifest_20260915.json
"""
from __future__ import annotations

import argparse
import os
import sys
from collections import Counter
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.warehouse.errors import WarehouseError
from app.warehouse.ownership_manifest import (
    WORKBOOK_LAYOUTS,
    build_ownership_rows,
    write_ownership_manifest,
)


def _parse_workbook_arg(value: str) -> tuple[str, str]:
    if "=" not in value:
        raise argparse.ArgumentTypeError(
            f"--workbook phai co dang source_code=duong/dan.xlsx, nhan duoc: {value!r}"
        )
    source_code, path = value.split("=", 1)
    source_code = source_code.strip()
    if source_code not in WORKBOOK_LAYOUTS:
        raise argparse.ArgumentTypeError(
            f"chua khai bao layout cho source_code={source_code!r}; da biet: {sorted(WORKBOOK_LAYOUTS)}"
        )
    return source_code, path.strip()


def main() -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    parser = argparse.ArgumentParser(description="Sinh ownership manifest phang tu workbook lich.")
    parser.add_argument(
        "--workbook", action="append", required=True, type=_parse_workbook_arg,
        help="source_code=duong/dan.xlsx (lap lai cho tung nguon)",
    )
    parser.add_argument("--out", required=True, help="duong dan file JSON dau ra")
    parser.add_argument("--dry-run", action="store_true", help="chi in thong ke, khong ghi file")
    args = parser.parse_args()

    try:
        # `dict(args.workbook)` se GHI DE IM LANG neu lap lai cung source_code - nguoi van hanh se
        # tuong minh da dung 2 workbook trong khi thuc te chi 1 cai co tac dung (GPT review 06).
        workbooks: dict[str, str] = {}
        for source_code, path in args.workbook:
            if source_code in workbooks:
                print(
                    f"FAIL: --workbook lap lai cho source_code={source_code!r} "
                    f"({workbooks[source_code]!r} va {path!r}) - chi duoc 1 workbook/nguon.",
                    file=sys.stderr,
                )
                return 1
            workbooks[source_code] = path
        rows = build_ownership_rows(workbooks)

        by_source = Counter(row.owner_source for row in rows)
        print(f"Tong dong ownership: {len(rows)}")
        for source_code in sorted(by_source):
            source_rows = [row for row in rows if row.owner_source == source_code]
            days = len({row.crawl_date for row in source_rows})
            slots = sorted({row.schedule_slot for row in source_rows})
            print(
                f"  {source_code:<14} rows={by_source[source_code]:>5}  crawl_dates={days:>4}  "
                f"slots={len(slots)} ({', '.join(slots)})"
            )
        print("Khong co conflicting ownership (da kiem tra toan bo cap (crawl_date, checkin_date)).")

        if args.dry_run:
            from app.warehouse.ownership_manifest import compute_ownership_manifest_sha256
            print(f"[dry-run] ownership_manifest_sha256 = {compute_ownership_manifest_sha256(rows)}")
            print("[dry-run] khong ghi file.")
            return 0

        out_path = Path(args.out)
        out_path.parent.mkdir(parents=True, exist_ok=True)
        digest = write_ownership_manifest(rows, out_path)
        print(f"Da ghi {out_path}")
        print(f"ownership_manifest_sha256 = {digest}")
        return 0
    except WarehouseError as exc:
        print(f"FAIL: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())

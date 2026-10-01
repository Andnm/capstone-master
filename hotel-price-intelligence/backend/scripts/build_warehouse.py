"""Build 1 batch vao warehouse DB da init (WAREHOUSE_EDA_ML_SPEC.md muc 3a buoc 9-16, muc 18).

Tien quyet: `init_warehouse_db.py` da tao DB rong; source/ownership manifest da sinh + checksum.
KHONG promote - promote la lenh rieng `promote_warehouse.py`, chi sau khi review.

Chay (tu backend/):
    python scripts/build_warehouse.py --database warehouse_20260916_2src \\
        --source-manifest ../data/warehouse/source_manifest_20260916.json \\
        --cohort-manifest ../data/warehouse/cohort_history_20260916.json \\
        --ownership-manifest ../data/warehouse/ownership_manifest_20260916.json
"""
from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.warehouse.batch import REPORT_DIR, BuildInputs, build_warehouse
from app.warehouse.errors import WarehouseError

REPO_ROOT = Path(__file__).resolve().parents[3]


def main() -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    parser = argparse.ArgumentParser(description="Build warehouse batch (khong promote).")
    parser.add_argument("--database", required=True)
    parser.add_argument("--source-manifest", required=True, type=Path)
    parser.add_argument("--cohort-manifest", required=True, type=Path,
                        help="cohort history JSON (scripts/build_cohort_history.py) - KHONG nhan workbook .xlsx")
    parser.add_argument("--ownership-manifest", required=True, type=Path)
    parser.add_argument("--base-dir", type=Path, default=REPO_ROOT,
                        help="goc de resolve dump_path / workbook_path tuong doi")
    parser.add_argument("--batch-id", default=None)
    parser.add_argument("--report-dir", type=Path, default=REPORT_DIR)
    parser.add_argument("--allow-dirty-provenance", action="store_true",
                        help="CHI cho rehearsal disposable: cho build khi code warehouse chua commit. Batch se "
                             "ghi canonicalization_git_commit co hau to '+dirty' va promote_warehouse tu choi no")
    args = parser.parse_args()
    if args.cohort_manifest.suffix.lower() != ".json":
        print("FAIL: --cohort-manifest phai la cohort history JSON (scripts/build_cohort_history.py). Workbook "
              ".xlsx dang dung da bi sua tai cho, mat cac hotel roi cohort (Mac Valley) -> du lieu hop le truoc "
              "ngay roi se bi danh protocol_deviation (CLAUDE.md muc 2). .xlsx chi danh cho test fixture.",
              file=sys.stderr)
        return 1
    try:
        report = build_warehouse(BuildInputs(
            warehouse_database=args.database, source_manifest_path=args.source_manifest,
            cohort_manifest_path=args.cohort_manifest, ownership_manifest_path=args.ownership_manifest,
            base_dir=args.base_dir, batch_id=args.batch_id, report_dir=args.report_dir,
            require_clean_provenance=not args.allow_dirty_provenance))
    except WarehouseError as exc:
        print(f"FAIL: {type(exc).__name__}: {exc}", file=sys.stderr)
        return 1
    print(f"batch {report['batch_id']} -> status={report['status']}")
    if report.get("failed_checks"):
        print("check FAIL:", ", ".join(report["failed_checks"]))
    print(f"report: {args.report_dir / (report['batch_id'] + '.json')}")
    return 0 if report["status"] == "pass" else 1


if __name__ == "__main__":
    raise SystemExit(main())

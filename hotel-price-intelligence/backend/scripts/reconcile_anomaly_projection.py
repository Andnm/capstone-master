"""Reconcile price_observations.is_anomaly (PROJECTION) voi anomaly_review_resolutions (SU THAT).

expected(record) = TRUE  <=>  EXISTS resolution (source_code=<nay>, source_record_id=record)
                                tro toi 1 decision dang state='active' AND decision='exclude_from_train'

Day la SAFETY NET, khong phai duong cap nhat chinh - sync_anomaly_registry.py da tu cap nhat
is_anomaly ngay trong transaction cua tung event. Chay script nay bat cu luc nao de xac nhan/sua
lech. Cung tinh anomaly_projection_checksum dung boi anomaly_registry_sync_runs.

Integrity gate BAT BUOC truoc khi daily_quality_monitor.py/export/warehouse doc is_anomaly: chay
--dry-run, phai ra 0 lech.

Run (dry-run mac dinh - tu backend/):
    python scripts/reconcile_anomaly_projection.py --source-code local_primary
    python scripts/reconcile_anomaly_projection.py --source-code local_primary --apply
"""
from __future__ import annotations

import argparse
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.core.database import get_db_connection
from app.scraper.anomaly_registry_lib import SourceIdentityError, checksum_of_pairs, require_source_identity

_EXPECTED_TRUE_SQL = """
    SELECT r.source_record_id
    FROM anomaly_review_resolutions r
    JOIN anomaly_review_decisions d ON d.review_id = r.review_id
    WHERE r.source_code = %s AND d.state = 'active' AND d.decision = 'exclude_from_train'
"""


def compute_expected_true_ids(cursor, source_code: str) -> set[int]:
    cursor.execute(_EXPECTED_TRUE_SQL, (source_code,))
    return {row["source_record_id"] if isinstance(row, dict) else row[0] for row in cursor.fetchall()}


def compute_active_resolution_checksum(cursor, source_code: str) -> str:
    cursor.execute(
        """
        SELECT r.source_record_id, r.review_id
        FROM anomaly_review_resolutions r
        JOIN anomaly_review_decisions d ON d.review_id = r.review_id
        WHERE r.source_code = %s AND d.state = 'active'
        """,
        (source_code,),
    )
    pairs = [(row["source_record_id"], row["review_id"]) for row in cursor.fetchall()]
    return checksum_of_pairs(pairs)


def compute_anomaly_projection_checksum(cursor) -> str:
    cursor.execute("SELECT record_id FROM price_observations WHERE is_anomaly = TRUE")
    ids = [row["record_id"] if isinstance(row, dict) else row[0] for row in cursor.fetchall()]
    return checksum_of_pairs([(rid,) for rid in ids])


def find_mismatches(cursor, expected_true_ids: set[int]) -> tuple[list[int], list[int]]:
    """Tra (should_be_true_but_isnt, should_be_false_but_isnt) - CHI trong pham vi toan bo
    price_observations (khong loc theo run status - resolution la su that tuyet doi cho dung
    record_id do, khong phu thuoc run con dang chay hay khong)."""
    cursor.execute("SELECT record_id, is_anomaly FROM price_observations")
    rows = cursor.fetchall()
    should_true, should_false = [], []
    for row in rows:
        rid = row["record_id"] if isinstance(row, dict) else row[0]
        current = bool(row["is_anomaly"] if isinstance(row, dict) else row[1])
        expected = rid in expected_true_ids
        if expected and not current:
            should_true.append(rid)
        elif current and not expected:
            should_false.append(rid)
    return should_true, should_false


def main() -> None:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    parser = argparse.ArgumentParser()
    parser.add_argument("--source-code", required=True)
    parser.add_argument("--apply", action="store_true", help="Sua lech that; mac dinh chi dry-run bao cao.")
    args = parser.parse_args()

    with get_db_connection() as conn:
        cursor = conn.cursor(dictionary=True)
        try:
            require_source_identity(cursor, args.source_code)
        except SourceIdentityError as exc:
            cursor.close()
            raise SystemExit(str(exc)) from exc

        expected_true_ids = compute_expected_true_ids(cursor, args.source_code)
        should_true, should_false = find_mismatches(cursor, expected_true_ids)
        mismatch_n = len(should_true) + len(should_false)

        print(f"source_code: {args.source_code}")
        print(f"Expected TRUE (theo resolution+decision active): {len(expected_true_ids)}")
        print(f"Lech: is_anomaly dang FALSE nhung phai TRUE: {len(should_true)}")
        print(f"Lech: is_anomaly dang TRUE nhung phai FALSE: {len(should_false)}")

        if mismatch_n == 0:
            active_checksum = compute_active_resolution_checksum(cursor, args.source_code)
            projection_checksum = compute_anomaly_projection_checksum(cursor)
            print(f"Integrity: OK (0 lech).")
            print(f"active_resolution_checksum: {active_checksum}")
            print(f"anomaly_projection_checksum: {projection_checksum}")
            cursor.close()
            return

        if not args.apply:
            print("\nDry run - khong sua gi. Chay lai voi --apply de dong bo lai is_anomaly.")
            cursor.close()
            raise SystemExit(1)

        try:
            if should_true:
                for start in range(0, len(should_true), 5000):
                    chunk = should_true[start:start + 5000]
                    placeholders = ",".join(["%s"] * len(chunk))
                    cursor.execute(
                        f"UPDATE price_observations SET is_anomaly=TRUE WHERE record_id IN ({placeholders})",
                        tuple(chunk),
                    )
            if should_false:
                for start in range(0, len(should_false), 5000):
                    chunk = should_false[start:start + 5000]
                    placeholders = ",".join(["%s"] * len(chunk))
                    cursor.execute(
                        f"UPDATE price_observations SET is_anomaly=FALSE WHERE record_id IN ({placeholders})",
                        tuple(chunk),
                    )
        except Exception:
            conn.rollback()
            cursor.close()
            raise
        conn.commit()

        should_true2, should_false2 = find_mismatches(cursor, expected_true_ids)
        if should_true2 or should_false2:
            cursor.close()
            raise RuntimeError(
                f"Sau khi apply van con lech: {len(should_true2)}+{len(should_false2)} - dung, kiem tra lai."
            )
        active_checksum = compute_active_resolution_checksum(cursor, args.source_code)
        projection_checksum = compute_anomaly_projection_checksum(cursor)
        print(f"\nDa sua {mismatch_n} record. Integrity: OK.")
        print(f"active_resolution_checksum: {active_checksum}")
        print(f"anomaly_projection_checksum: {projection_checksum}")
        cursor.close()


if __name__ == "__main__":
    main()

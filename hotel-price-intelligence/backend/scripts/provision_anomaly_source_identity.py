"""One-time setup: seed anomaly_registry_source_identity cho CHINH DB nay.

Chay 1 lan/DB (local_primary, vps, local_aux moi noi tu chay voi --source-code cua chinh no) TRUOC
khi dung sync_anomaly_registry.py lan dau. FAIL neu da provision voi source_code KHAC (khong tu doi -
tranh ghi nham identity). No-op neu da dung.

Run (tu backend/):
    python scripts/provision_anomaly_source_identity.py --source-code local_primary
"""
from __future__ import annotations

import argparse
import os
import sys
from datetime import datetime, timezone

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.core.database import get_db_connection


def main() -> None:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    parser = argparse.ArgumentParser()
    parser.add_argument("--source-code", required=True)
    args = parser.parse_args()

    now = datetime.now(timezone.utc).replace(tzinfo=None)
    with get_db_connection() as conn:
        cursor = conn.cursor(dictionary=True)
        cursor.execute("SELECT source_code FROM anomaly_registry_source_identity WHERE id=1")
        existing = cursor.fetchone()
        if existing is None:
            cursor.execute(
                "INSERT INTO anomaly_registry_source_identity (id, source_code, configured_at) "
                "VALUES (1,%s,%s)",
                (args.source_code, now),
            )
            conn.commit()
            print(f"Da provision source identity: {args.source_code}")
        elif existing["source_code"] == args.source_code:
            print(f"Da provision san la '{args.source_code}' tu truoc - no-op.")
        else:
            cursor.close()
            raise SystemExit(
                f"DB nay DA duoc provision voi source_code='{existing['source_code']}', khac voi "
                f"'{args.source_code}' vua truyen vao. Khong tu doi identity - kiem tra lai truoc "
                f"khi chay tiep (co the ban dang chay nham may)."
            )
        cursor.close()


if __name__ == "__main__":
    main()

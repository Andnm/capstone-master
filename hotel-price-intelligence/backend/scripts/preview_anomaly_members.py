"""Preview cac record khop 1 tieu chi loc, kem fingerprint - dung de SOAN (khong ghi) 1 decision
moi cho anomaly_registry.json. Day la buoc "draft/preview" NGOAI registry (discuss file 15 M1) -
KHONG ghi gi vao DB, chi in JSON co the copy thang vao truong "members" cua 1 event.

Chi ho tro tieu chi don gian (hotel_id + room_identity_key + gioi han gia) thay vi nhan SQL tuy y,
tranh nguy co injection/chay nham dieu kien qua rong.

Run (tu backend/):
    python scripts/preview_anomaly_members.py --source-code local_primary \\
        --hotel-id lumina-dalat-premium \\
        --room-identity-key 2583b58e4b8e43e11364f27ff4de4c006965156562aae3edab529b4f661543a2 \\
        --room-identity-key c8a8d77ab00258bf9bcd884c29fa6cd9776c63a2a8f28100204d0f76475e1ce1 \\
        --price-exact 90000000
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from datetime import datetime, timezone

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.core.database import get_db_connection
from app.scraper.anomaly_registry_lib import (
    SourceIdentityError,
    canonical_json,
    observation_fingerprint,
    require_source_identity,
)


def build_query(args) -> tuple[str, list]:
    where = ["po.hotel_id = %s"]
    params: list = [args.hotel_id]
    if args.room_identity_key:
        placeholders = ",".join(["%s"] * len(args.room_identity_key))
        where.append(f"po.room_identity_key IN ({placeholders})")
        params.extend(args.room_identity_key)
    if args.price_exact:
        placeholders = ",".join(["%s"] * len(args.price_exact))
        where.append(f"po.price_per_night IN ({placeholders})")
        params.extend(args.price_exact)
    if args.price_min is not None:
        where.append("po.price_per_night >= %s")
        params.append(args.price_min)
    if args.price_max is not None:
        where.append("po.price_per_night <= %s")
        params.append(args.price_max)
    if args.checkin_date:
        where.append("po.checkin_date = %s")
        params.append(args.checkin_date)

    sql = f"""
        SELECT po.record_id, po.hotel_id, po.crawl_run_id, po.crawl_run_item_id,
               po.observed_at, po.checkin_date, po.checkout_date, po.room_option_index,
               po.room_identity_key, po.rate_plan_key, po.price_total, po.price_per_night
        FROM price_observations po
        JOIN crawl_run_items cri ON cri.id = po.crawl_run_item_id
        JOIN crawl_runs cr ON cr.id = cri.crawl_run_id
        WHERE cr.status = 'completed' AND cri.status = 'success'
          AND po.is_sold_out = 0 AND {' AND '.join(where)}
        ORDER BY po.record_id
    """
    return sql, params


def main() -> None:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    parser = argparse.ArgumentParser()
    parser.add_argument("--source-code", required=True)
    parser.add_argument("--hotel-id", required=True)
    parser.add_argument("--room-identity-key", action="append", default=[])
    parser.add_argument("--price-exact", type=int, action="append", default=[])
    parser.add_argument("--price-min", type=int, default=None)
    parser.add_argument("--price-max", type=int, default=None)
    parser.add_argument("--checkin-date", default=None)
    parser.add_argument("--output", default=None, help="Ghi JSON ra file thay vi stdout.")
    args = parser.parse_args()

    sql, params = build_query(args)
    with get_db_connection() as conn:
        cursor = conn.cursor(dictionary=True)
        try:
            require_source_identity(cursor, args.source_code)
        except SourceIdentityError as exc:
            cursor.close()
            raise SystemExit(str(exc)) from exc

        cursor.execute(sql, params)
        rows = cursor.fetchall()
        cursor.close()

    members = [
        {
            "source_code": args.source_code,
            "source_record_id": r["record_id"],
            "source_record_sha256": observation_fingerprint(r),
        }
        for r in rows
    ]

    out = {
        "matched_count": len(members),
        "preview_at": datetime.now(timezone.utc).replace(microsecond=0).isoformat() + "Z",
        "selection_query": sql.strip(),
        "selection_params": [str(p) for p in params],
        "members": members,
    }
    text = json.dumps(out, ensure_ascii=False, indent=2)
    if args.output:
        with open(args.output, "w", encoding="utf-8") as f:
            f.write(text)
        print(f"matched_count={len(members)} -> {args.output}", file=sys.stderr)
    else:
        print(text)


if __name__ == "__main__":
    main()

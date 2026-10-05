"""Item matching alias-aware (spec muc 12) -> `ml_item_reference_matches`.

Moi item `training_protocol_eligible` (run completed + include_training, item success + include_training) thuoc
mot series co assignment duoc so voi reference DA DONG BANG bang `select_best_match()` cua `app.scraper.reference`.
Adapter field-name BAT BUOC: `match_reference()` doc `room_identity_key`/`rate_plan_key` (khong phai
`canonical_*`); truyen thang dict canonical co the thanh `None == None` va tao exact-match gia. Hang observation
cua item doc theo `room_option_index ASC` vi ham tra INDEX (xac dinh). `UNIQUE(dataset_version, crawl_run_item_id)`
tu cuong che 1 quyet dinh/item.

Pham vi (E4, dang bi phan bien): theo dung spec - ca item TRUOC `approved_at`; bao cao coverage loc them theo thoi gian.
"""
from __future__ import annotations

from collections import Counter
from typing import Any, Callable

from . import env  # noqa: F401
from .db import execute, executemany, fetch_all, utc_now

from app.scraper.reference import select_best_match  # noqa: E402

_ASSIGNMENTS_SQL = """
SELECT id, hotel_id, checkin_date, canonical_room_key, canonical_rate_key, room_type_anchor_raw, max_occupancy, room_area
FROM ml_reference_assignments WHERE dataset_version = %s
"""

_STREAM_SQL = """
SELECT cri.id AS item_id, cri.hotel_id, cri.checkin_date, po.record_id, cok.canonical_room_key, cok.canonical_rate_key,
       po.room_type_raw, po.max_occupancy, po.room_area
FROM crawl_run_items cri
JOIN crawl_runs cr ON cr.id = cri.crawl_run_id AND cr.status = 'completed'
JOIN etl_run_map rm ON rm.warehouse_run_id = cr.id AND rm.import_batch_id = %s AND rm.include_training = TRUE
JOIN etl_item_map im ON im.warehouse_item_id = cri.id AND im.import_batch_id = %s AND im.include_training = TRUE
JOIN ml_reference_assignments a ON a.dataset_version = %s AND a.hotel_id = cri.hotel_id AND a.checkin_date = cri.checkin_date
JOIN price_observations po ON po.crawl_run_item_id = cri.id
JOIN curated_observation_keys cok ON cok.record_id = po.record_id
WHERE cri.status = 'success' AND cri.id >= %s AND cri.id < %s
ORDER BY cri.id, po.room_option_index
"""

_INSERT_SQL = """
INSERT INTO ml_item_reference_matches (dataset_version, crawl_run_item_id, ml_reference_assignment_id, selected_record_id,
                                        match_status, match_score, created_at)
VALUES (%s,%s,%s,%s,%s,%s,%s)
"""

_FLUSH = 5000
ITEM_ID_CHUNK = 25_000    # moi lan doc ~25k item (~vai tram nghin dong): sort nho, bo nho co han (buffer pool chi 128 MB)


def adapt_rooms(records: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Adapter field-name (spec muc 12): canonical key -> ten ma `match_reference()` doc."""
    return [{"room_identity_key": r["canonical_room_key"], "rate_plan_key": r["canonical_rate_key"],
             "max_occupancy": r["max_occupancy"], "room_area": r["room_area"], "room_type_raw": r["room_type_raw"]}
            for r in records]


def adapt_reference(assignment: dict[str, Any]) -> dict[str, Any]:
    return {"room_identity_key": assignment["canonical_room_key"], "rate_plan_key": assignment["canonical_rate_key"],
            "room_type_anchor_raw": assignment["room_type_anchor_raw"], "max_occupancy": assignment["max_occupancy"],
            "room_area": assignment["room_area"]}


def match_item(records: list[dict[str, Any]], assignment: dict[str, Any]) -> tuple[int | None, str, float]:
    """(selected_record_id | None, status, score). `records` PHAI da sap xep theo room_option_index."""
    index, status, score = select_best_match(adapt_rooms(records), adapt_reference(assignment))
    selected = records[index]["record_id"] if index is not None else None
    return selected, status, round(float(score), 4)


def build_item_matches(conn, *, dataset_version: str, config: dict[str, Any],
                       heartbeat: Callable[[], None] | None = None) -> dict[str, Any]:
    """Doc theo khoang `crawl_run_items.id` (moi khoang 1 truy van, sort nho), match tung item, ghi + commit moi khoang."""
    batch_id = config["import_batch_id"]
    assignments = {(r["hotel_id"], r["checkin_date"]): r for r in fetch_all(conn, _ASSIGNMENTS_SQL, (dataset_version,))}
    created_at = utc_now()
    counts: Counter = Counter()
    items = 0
    try:
        execute(conn, "DELETE FROM ml_item_reference_matches WHERE dataset_version=%s", (dataset_version,))
        bounds = fetch_all(conn, "SELECT MIN(id) AS lo, MAX(id) AS hi FROM crawl_run_items")[0]
        if bounds["lo"] is not None:
            for lo in range(int(bounds["lo"]), int(bounds["hi"]) + 1, ITEM_ID_CHUNK):
                rows = fetch_all(conn, _STREAM_SQL, (batch_id, batch_id, dataset_version, lo, lo + ITEM_ID_CHUNK))
                pending: list[tuple] = []
                current_item, current_assignment, records = None, None, []

                def finish() -> None:
                    nonlocal items
                    selected, status, score = match_item(records, current_assignment)
                    pending.append((dataset_version, current_item, current_assignment["id"], selected, status, score, created_at))
                    counts[status] += 1
                    items += 1

                for row in rows:
                    if row["item_id"] != current_item:
                        if current_item is not None:
                            finish()
                        current_item = row["item_id"]
                        current_assignment = assignments[(row["hotel_id"], row["checkin_date"])]
                        records = []
                    records.append(row)
                if current_item is not None:
                    finish()
                executemany(conn, _INSERT_SQL, pending)
                conn.commit()
                if heartbeat is not None:
                    heartbeat()
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    return {"items_matched": items, "by_status": dict(counts), "assignments": len(assignments)}

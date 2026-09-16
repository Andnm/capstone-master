"""Full-history reference trong warehouse (WAREHOUSE_EDA_ML_SPEC.md muc 10, muc 3a buoc 15).

Bam logic production `DurableQueueRepository._refresh_reference()` (app/database/durable.py), voi
DUNG 4 khac biet chu dich do spec quy dinh:
1. Doc canonical key tu `curated_observation_keys`, khong doc raw `room_identity_key`/`rate_plan_key`.
   Cot `room_identity_key`/`rate_plan_key` cua 2 bang reference trong warehouse chua CANONICAL key.
2. Chi dung run/item co `reference_evidence=TRUE`: run `completed` + `etl_run_map.include_reference`,
   item `success` + `etl_item_map.include_reference` (precedence muc 4).
3. Tie-break cuoi them `room_identity_key ASC, rate_plan_key ASC` de ket qua xac dinh.
4. Full-history, KHONG causal (causal freeze la viec cua dataset build, muc 11): tinh 1 lan tren toan
   bo du lieu cua batch.

KHONG goi `repair_not_bookable_item_urls()`, khong import `DurableQueueRepository`, va KHONG BAO GIO
ghi nguoc vao `price_observations` (production UPDATE is_reference_room... sau khi approve; warehouse
thi field reference da reset dung 1 lan luc import va vinh vien khong ghi lai - muc 6).
Chay lai: DELETE sach 2 bang reference roi dung lai (1 warehouse DB chi chua 1 batch).
"""
from __future__ import annotations

import datetime as dt
import json
from typing import Any

from app.scraper.reference import is_reference_candidate_eligible

from .canonicalize import EMPTY_ROOM_KEY
from .errors import ValidationError

REFERENCE_ALGORITHM_VERSION = "warehouse-fullhistory-1.0.0"

_EVIDENCE = """
    FROM price_observations po
    JOIN curated_observation_keys cok ON cok.record_id = po.record_id
    JOIN crawl_runs cr ON cr.id = po.crawl_run_id AND cr.status = 'completed'
    JOIN crawl_run_items cri ON cri.id = po.crawl_run_item_id AND cri.status = 'success'
    JOIN etl_run_map rm ON rm.warehouse_run_id = cr.id
     AND rm.import_batch_id = %(batch)s AND rm.include_reference = TRUE
    JOIN etl_item_map im ON im.warehouse_item_id = cri.id
     AND im.import_batch_id = %(batch)s AND im.include_reference = TRUE
    WHERE po.is_sold_out = 0 AND cok.canonical_room_key <> %(empty_room)s
"""

_CANDIDATES_SQL = f"""
INSERT INTO hotel_room_candidates (
  hotel_id, checkin_date, room_identity_key, rate_plan_key, room_type_anchor_raw, room_type_norm,
  max_occupancy, bed_config, room_area, breakfast_included, free_cancellation,
  observation_count, distinct_run_count, distinct_item_count, eligible_item_count,
  item_coverage, first_seen_at, last_seen_at, aliases)
SELECT c.hotel_id, c.checkin_date, c.room_key, c.rate_key, LEFT(COALESCE(c.anchor, ''), 500), c.norm,
       c.occ, LEFT(c.bed, 500), c.area, c.bf, c.fc,
       c.obs, c.runs, c.items, e.eligible, LEAST(1, c.items / e.eligible), c.first_seen, c.last_seen,
       JSON_ARRAY(LEFT(COALESCE(c.anchor, ''), 500))
FROM (
  SELECT po.hotel_id, po.checkin_date, cok.canonical_room_key room_key, cok.canonical_rate_key rate_key,
         MAX(po.room_type_raw) anchor, MAX(po.room_type_norm) norm, MAX(po.max_occupancy) occ,
         MAX(po.bed_config) bed, MAX(po.room_area) area, MAX(po.breakfast_included) bf,
         MAX(po.free_cancellation) fc, COUNT(*) obs, COUNT(DISTINCT po.crawl_run_id) runs,
         COUNT(DISTINCT po.crawl_run_item_id) items, MIN(po.observed_at) first_seen, MAX(po.observed_at) last_seen
  {_EVIDENCE}
  GROUP BY po.hotel_id, po.checkin_date, cok.canonical_room_key, cok.canonical_rate_key
) c
JOIN (
  SELECT po.hotel_id, po.checkin_date, COUNT(DISTINCT po.crawl_run_item_id) eligible
  {_EVIDENCE}
  GROUP BY po.hotel_id, po.checkin_date
) e ON e.hotel_id = c.hotel_id AND e.checkin_date = c.checkin_date
ORDER BY c.hotel_id, c.checkin_date, c.room_key, c.rate_key
"""
# `hotel_reference_rooms.id` la AUTO_INCREMENT -> ORDER BY o _BEST_SQL de ID gan theo KHOA TU NHIEN, xac dinh
# theo cau truc chu khong dua vao thu tu ngam cua query plan (checksum "exact" giu ca ID, muc 16 / D10 gate 7).
# `hotel_room_candidates` khong co ID (PK = khoa tu nhien); ORDER BY o tren chi de chen dung thu tu PK.

_BEST_SQL = """
SELECT * FROM (
  SELECT c.*, ROW_NUMBER() OVER (
    PARTITION BY c.hotel_id, c.checkin_date
    ORDER BY (c.observation_count = c.distinct_item_count) DESC, c.item_coverage DESC,
             c.distinct_run_count DESC, (c.max_occupancy IS NOT NULL AND c.max_occupancy <= 2) DESC,
             c.observation_count DESC, c.room_identity_key ASC, c.rate_plan_key ASC
  ) rn
  FROM hotel_room_candidates c
) ranked WHERE rn = 1
ORDER BY hotel_id, checkin_date
"""


def build_full_history_references(conn, *, batch_id: str, activated_at: dt.datetime, min_runs: int,
                                  min_coverage: float, commit: bool = True) -> dict[str, Any]:
    """Rebuild 2 bang reference trong 1 transaction. `activated_at` = thoi diem bat dau batch (xac dinh).

    Nguong la THAM SO BAT BUOC, nguoi goi lay tu `etl_config()` da pin cua batch - KHONG doc `settings`
    o day, de khong the doi nguong am tham qua .env giua build va rebuild (GPT review 12 MAJOR 1).
    `commit=False` giu transaction mo de nguoi goi validate roi moi tu quyet commit/rollback.
    """
    params = {"batch": batch_id, "empty_room": EMPTY_ROOM_KEY}
    cursor = conn.cursor(dictionary=True)
    try:
        cursor.execute("DELETE FROM hotel_reference_rooms")
        cursor.execute("DELETE FROM hotel_room_candidates")
        cursor.execute(_CANDIDATES_SQL, params)
        candidates = cursor.rowcount
        cursor.execute(_BEST_SQL)
        best_rows = cursor.fetchall()
        inserts = []
        approved = 0
        for best in best_rows:
            coverage = float(best["item_coverage"])
            unique = best["observation_count"] == best["distinct_item_count"]
            ok = is_reference_candidate_eligible(best, min_runs=min_runs, min_coverage=min_coverage)
            approved += ok
            inserts.append((
                best["hotel_id"], best["checkin_date"], best["room_identity_key"], best["rate_plan_key"],
                best["room_type_anchor_raw"], best["room_type_norm"], best["max_occupancy"],
                best["bed_config"], best["room_area"], best["breakfast_included"], best["free_cancellation"],
                "approved" if ok else "proposed", coverage, coverage if unique else coverage * 0.60,
                best["observation_count"], best["distinct_run_count"], best["distinct_item_count"],
                best["eligible_item_count"], json.dumps([best["room_type_anchor_raw"]], ensure_ascii=False),
                activated_at if ok else None,
            ))
        if inserts:
            cursor.executemany(
                """INSERT INTO hotel_reference_rooms (
                  hotel_id, checkin_date, room_identity_key, rate_plan_key, room_type_anchor_raw, room_type_norm,
                  max_occupancy, bed_config, room_area, breakfast_included, free_cancellation, selection_method,
                  status, coverage, confidence_score, observation_count, distinct_run_count, distinct_item_count,
                  eligible_item_count, aliases, active_from)
                VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,'auto',%s,%s,%s,%s,%s,%s,%s,%s,%s)""",
                inserts,
            )
        cursor.execute(
            "SELECT COUNT(*) n FROM (SELECT hotel_id, checkin_date FROM hotel_reference_rooms "
            "WHERE status='approved' GROUP BY hotel_id, checkin_date HAVING COUNT(*) > 1) d"
        )
        duplicate_approved = int(cursor.fetchone()["n"])
        if duplicate_approved:
            conn.rollback()
            raise ValidationError(f"{duplicate_approved} cap (hotel, checkin) co >1 reference approved")
        if commit:
            conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        cursor.close()
    return {
        "algorithm_version": REFERENCE_ALGORITHM_VERSION,
        "min_runs": min_runs,
        "min_coverage": min_coverage,
        "candidates": candidates,
        "series_with_reference": len(best_rows),
        "approved": approved,
        "proposed": len(best_rows) - approved,
    }

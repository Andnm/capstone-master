"""Buoc 13 + muc 17 (phan warehouse) - chi DOC, tra report `{ok, checks, numbers}`; khong sua gi.

Moi check la 1 query co ky vong ro rang. Batch chi PASS khi MOI check ok. Cac check can staging
(doi chieu TIMESTAMP) nam o `audit_run_timestamps()` va chay trong luc staging con song (buoc 11-12).
"""
from __future__ import annotations

import json
from typing import Any, Iterable

from .curated import check_curated_invariants
from .registry import verify_source_manifest

_ZERO_CHECKS: tuple[tuple[str, str], ...] = (
    # --- muc 7: orphan / duplicate tren toan core
    ("orphan_items_khong_co_run",
     "SELECT COUNT(*) FROM crawl_run_items i LEFT JOIN crawl_runs r ON r.id=i.crawl_run_id WHERE r.id IS NULL"),
    ("orphan_observations",
     "SELECT COUNT(*) FROM price_observations po LEFT JOIN crawl_runs r ON r.id=po.crawl_run_id "
     "LEFT JOIN crawl_run_items i ON i.id=po.crawl_run_item_id LEFT JOIN hotels h ON h.hotel_id=po.hotel_id "
     "WHERE r.id IS NULL OR i.id IS NULL OR h.hotel_id IS NULL"),
    ("duplicate_item_option",
     "SELECT COUNT(*) FROM (SELECT crawl_run_item_id, room_option_index FROM price_observations "
     "GROUP BY crawl_run_item_id, room_option_index HAVING COUNT(*) > 1) d"),
    ("observation_run_khac_run_cua_item",
     "SELECT COUNT(*) FROM price_observations po JOIN crawl_run_items i ON i.id=po.crawl_run_item_id "
     "WHERE i.crawl_run_id <> po.crawl_run_id"),
    # --- map 1:1 va join nguoc (GPT D10 gate 1)
    ("run_khong_co_map",
     "SELECT COUNT(*) FROM crawl_runs r LEFT JOIN etl_run_map m ON m.warehouse_run_id=r.id WHERE m.warehouse_run_id IS NULL"),
    ("item_khong_co_map",
     "SELECT COUNT(*) FROM crawl_run_items i LEFT JOIN etl_item_map m ON m.warehouse_item_id=i.id WHERE m.warehouse_item_id IS NULL"),
    ("observation_khong_co_map",
     "SELECT COUNT(*) FROM price_observations po LEFT JOIN etl_observation_map m ON m.warehouse_record_id=po.record_id "
     "WHERE m.warehouse_record_id IS NULL"),
    ("fingerprint_sai_dinh_dang",
     "SELECT COUNT(*) FROM etl_observation_map WHERE source_record_sha256 NOT REGEXP '^[0-9a-f]{64}$'"),
    ("item_map_khac_source_voi_run_map",
     "SELECT COUNT(*) FROM etl_item_map im JOIN crawl_run_items i ON i.id=im.warehouse_item_id "
     "JOIN etl_run_map rm ON rm.warehouse_run_id=i.crawl_run_id WHERE rm.source_code <> im.source_code"),
    # --- co eligibility: item khong duoc cao hon run cha (GPT file 04 O4) + 4 CHECK 2 chieu (defense in depth)
    ("item_co_cao_hon_run_cha",
     "SELECT COUNT(*) FROM etl_item_map im JOIN crawl_run_items i ON i.id=im.warehouse_item_id "
     "JOIN etl_run_map rm ON rm.warehouse_run_id=i.crawl_run_id WHERE (im.include_reference AND NOT rm.include_reference) "
     "OR (im.include_training AND NOT rm.include_training) OR (im.include_eda_main AND NOT rm.include_eda_main) "
     "OR (im.include_eda_raw AND NOT rm.include_eda_raw)"),
    ("unassigned_ma_con_co",
     "SELECT COUNT(*) FROM etl_item_map WHERE ownership_status='unassigned' AND (include_reference OR include_training "
     "OR include_eda_main OR exclusion_reason IS NULL)"),
    ("resolved_ma_thieu_slot_key",
     "SELECT COUNT(*) FROM etl_item_map WHERE ownership_status<>'unassigned' AND (schedule_slot IS NULL "
     "OR schedule_manifest_row_key IS NULL)"),
    # --- trang thai ket thuc (GPT file 04 ghi chu 2, O3)
    ("run_chua_ket_thuc", "SELECT COUNT(*) FROM crawl_runs WHERE status IN ('queued','running')"),
    ("item_chua_ket_thuc_trong_run_completed",
     "SELECT COUNT(*) FROM crawl_run_items i JOIN crawl_runs r ON r.id=i.crawl_run_id "
     "WHERE r.status='completed' AND i.status IN ('queued','running')"),
    ("item_success_ma_hotel_id_null", "SELECT COUNT(*) FROM crawl_run_items WHERE status='success' AND hotel_id IS NULL"),
    ("item_success_khong_co_observation",
     "SELECT COUNT(*) FROM crawl_run_items i LEFT JOIN price_observations po ON po.crawl_run_item_id=i.id "
     "WHERE i.status='success' AND po.record_id IS NULL"),
    # --- field reference reset dung 1 lan (D8)
    ("observation_reference_chua_reset",
     "SELECT COUNT(*) FROM price_observations WHERE is_reference_room OR reference_definition_id IS NOT NULL "
     "OR reference_match_status <> 'calibrating' OR reference_match_score IS NOT NULL"),
    ("item_reference_chua_reset", "SELECT COUNT(*) FROM crawl_run_items WHERE reference_match_status <> 'calibrating'"),
    # --- reference full-history
    ("reference_approved_trung_series",
     "SELECT COUNT(*) FROM (SELECT hotel_id, checkin_date FROM hotel_reference_rooms WHERE status='approved' "
     "GROUP BY hotel_id, checkin_date HAVING COUNT(*) > 1) d"),
    ("hotel_city_ngoai_5_thanh_pho",
     "SELECT COUNT(*) FROM hotels WHERE city IS NOT NULL AND city NOT IN "
     "('Hồ Chí Minh','Hà Nội','Vũng Tàu','Đà Lạt','Phú Quốc')"),
)


def _scalar(cursor, sql: str, params: tuple = ()) -> int:
    cursor.execute(sql, params)
    value = cursor.fetchone()[0]
    return int(value or 0)


def validate_warehouse(wh_conn, *, batch_id: str) -> dict[str, Any]:
    checks: list[dict[str, Any]] = []

    def add(name: str, ok: bool, detail: Any) -> None:
        checks.append({"name": name, "ok": bool(ok), "detail": detail})

    try:
        add("source_manifest_sha256_khop", True, verify_source_manifest(wh_conn, batch_id))
    except Exception as exc:  # noqa: BLE001 - check that bai la mot ket qua, khong phai crash
        add("source_manifest_sha256_khop", False, str(exc))

    cursor = wh_conn.cursor()
    try:
        for name, sql in _ZERO_CHECKS:
            count = _scalar(cursor, sql)
            add(name, count == 0, count)

        cursor.execute("SELECT notes FROM etl_import_batches WHERE batch_id=%s", (batch_id,))
        notes = json.loads((cursor.fetchone() or [None])[0] or "{}")
        source_counts = notes.get("source_row_counts", {})
        numbers: dict[str, Any] = {"source_row_counts": source_counts}

        # --- muc 7 cong thuc 1:1: source = imported + row_error + parent_rejected (moi nguon, moi bang)
        for source_code, tables in sorted(source_counts.items()):
            for table, map_table, map_col in (("crawl_runs", "etl_run_map", "source_run_id"),
                                              ("crawl_run_items", "etl_item_map", "source_item_id"),
                                              ("price_observations", "etl_observation_map", "source_record_id")):
                imported = _scalar(cursor, f"SELECT COUNT(*) FROM {map_table} WHERE import_batch_id=%s AND source_code=%s",
                                   (batch_id, source_code))
                rejected = {scope: _scalar(cursor, "SELECT COUNT(*) FROM etl_import_rejections WHERE import_batch_id=%s "
                                           "AND source_code=%s AND source_table=%s AND rejection_scope=%s",
                                           (batch_id, source_code, table, scope))
                            for scope in ("row_error", "parent_rejected")}
                source_rows = int(tables.get(table, -1))
                total = imported + rejected["row_error"] + rejected["parent_rejected"]
                add(f"reconcile_{source_code}_{table}", source_rows == total,
                    {"source": source_rows, "imported": imported, **rejected})

        # --- muc 7 cong thuc rieng cho hotels
        hotels = notes.get("hotels", {})
        distinct = _scalar(cursor, "SELECT COUNT(DISTINCT hotel_id) FROM hotels")
        rejected_hotels = _scalar(cursor, "SELECT COUNT(*) FROM etl_import_rejections WHERE import_batch_id=%s "
                                          "AND source_table='hotels'", (batch_id,))
        accepted = int(hotels.get("source_hotel_rows", -1)) - rejected_hotels
        add("reconcile_hotels", distinct == int(hotels.get("warehouse_distinct_hotels", -2))
            and accepted - distinct == int(hotels.get("duplicate_business_key_rows", -3)),
            {"source_hotel_rows": hotels.get("source_hotel_rows"), "rejected": rejected_hotels,
             "accepted": accepted, "warehouse_distinct_hotels": distinct, "duplicate_business_key_rows": accepted - distinct})

        # --- core == map (cardinality tong)
        for table, map_table in (("crawl_runs", "etl_run_map"), ("crawl_run_items", "etl_item_map"),
                                 ("price_observations", "etl_observation_map")):
            core = _scalar(cursor, f"SELECT COUNT(*) FROM {table}")
            mapped = _scalar(cursor, f"SELECT COUNT(*) FROM {map_table} WHERE import_batch_id=%s", (batch_id,))
            add(f"cardinality_{table}", core == mapped, {"core": core, "map": mapped})

        # --- D1: AUTO_INCREMENT that su > MAX(id) (khong ALTER; chi assert)
        cursor.execute("SET SESSION information_schema_stats_expiry = 0")
        for table, column in (("crawl_runs", "id"), ("crawl_run_items", "id"), ("price_observations", "record_id")):
            maximum = _scalar(cursor, f"SELECT COALESCE(MAX({column}), 0) FROM {table}")
            auto = _scalar(cursor, "SELECT AUTO_INCREMENT FROM information_schema.TABLES WHERE TABLE_SCHEMA=DATABASE() "
                                   "AND TABLE_NAME=%s", (table,))
            add(f"auto_increment_{table}", auto > maximum, {"auto_increment": auto, "max_id": maximum})

        # --- cong rejection: 0, hoac moi dong da waived co ly do + nguoi duyet
        unwaived = _scalar(cursor, "SELECT COUNT(*) FROM etl_import_rejections WHERE import_batch_id=%s AND waived=FALSE",
                           (batch_id,))
        add("rejection_chua_waive", unwaived == 0, unwaived)

        # --- bao cao ownership (khong phai gate): group theo (source, status, reason)
        cursor.execute("SELECT source_code, ownership_status, exclusion_reason, COUNT(*) FROM etl_item_map "
                       "WHERE import_batch_id=%s GROUP BY 1, 2, 3 ORDER BY 1, 2, 3", (batch_id,))
        numbers["ownership_items"] = [list(row) for row in cursor.fetchall()]
        # hotel nao dang bi danh protocol_deviation: dua nham cohort input (vd workbook hien tai thay vi cohort
        # history) se lo ra ngay o day - ca Mac Valley bi bat bang dung bang nay o rehearsal dau tien.
        cursor.execute("SELECT m.source_code, i.hotel_id, COUNT(*) FROM etl_item_map m "
                       "JOIN crawl_run_items i ON i.id = m.warehouse_item_id "
                       "WHERE m.import_batch_id=%s AND m.ownership_status='protocol_deviation' "
                       "GROUP BY 1, 2 ORDER BY 3 DESC, 1, 2 LIMIT 50", (batch_id,))
        numbers["protocol_deviation_by_hotel"] = [list(row) for row in cursor.fetchall()]
    finally:
        cursor.close()

    curated = check_curated_invariants(wh_conn)
    numbers["curated"] = curated
    for key, value in curated.items():
        if key not in ("price_observations", "curated_observation_keys"):
            add(f"curated_{key}", value == 0, value)
    return {"ok": all(check["ok"] for check in checks), "checks": checks, "numbers": numbers}


# Muc 16: checksum tai lap KHONG tinh tren technical ID. Moi bang doc theo KHOA TU NHIEN (source_code +
# PK nguon qua bang map, hoac business key) de 2 lan build giong het nhau cho cung checksum ke ca khi
# ID khac. Ban "exact" giu ca ID - vi ID duoc gan xac dinh (D1) nen no cung phai trung; GPT file 02:
# "ID giong nhau co the assert them nhung khong thay contract checksum loai technical ID".
_SEMANTIC_QUERIES = {
    "hotels": "SELECT * FROM hotels ORDER BY hotel_id",
    "crawl_runs": "SELECT m.source_code _src, m.source_run_id _pk, r.* FROM crawl_runs r "
                  "JOIN etl_run_map m ON m.warehouse_run_id=r.id ORDER BY _src, _pk",
    "crawl_run_items": "SELECT m.source_code _src, m.source_item_id _pk, i.* FROM crawl_run_items i "
                       "JOIN etl_item_map m ON m.warehouse_item_id=i.id ORDER BY _src, _pk",
    "price_observations": "SELECT m.source_code _src, m.source_record_id _pk, po.* FROM price_observations po "
                          "JOIN etl_observation_map m ON m.warehouse_record_id=po.record_id ORDER BY _src, _pk",
    "etl_run_map": "SELECT * FROM etl_run_map ORDER BY source_code, source_run_id",
    "etl_item_map": "SELECT * FROM etl_item_map ORDER BY source_code, source_item_id",
    "etl_observation_map": "SELECT * FROM etl_observation_map ORDER BY source_code, source_record_id",
    "curated_observation_keys": "SELECT m.source_code _src, m.source_record_id _pk, c.* FROM curated_observation_keys c "
                                "JOIN etl_observation_map m ON m.warehouse_record_id=c.record_id ORDER BY _src, _pk",
    "hotel_room_candidates": "SELECT * FROM hotel_room_candidates "
                             "ORDER BY hotel_id, checkin_date, room_identity_key, rate_plan_key",
    "hotel_reference_rooms": "SELECT * FROM hotel_reference_rooms "
                             "ORDER BY hotel_id, checkin_date, room_identity_key, rate_plan_key",
    "etl_import_rejections": "SELECT * FROM etl_import_rejections "
                             "ORDER BY source_code, source_table, source_pk_value, rejection_scope",
}
# Tap bang chuan cua checksum: promote bat buoc `notes.checksums` co DUNG tap nay (GPT review 14 MAJOR).
CHECKSUM_TABLES = tuple(_SEMANTIC_QUERIES)
_TECHNICAL_COLUMNS = frozenset({"id", "record_id", "crawl_run_id", "crawl_run_item_id", "retry_of_run_id",
                                "warehouse_run_id", "warehouse_item_id", "warehouse_record_id",
                                "reference_definition_id"})
# Chi cot do ETL sinh ra theo thoi diem build. created_at cua 4 bang core la DU LIEU NGUON, khong phai o day.
_VOLATILE_COLUMNS = {
    "etl_run_map": {"imported_at"}, "etl_item_map": {"imported_at"}, "etl_observation_map": {"imported_at"},
    "curated_observation_keys": {"created_at"}, "etl_import_rejections": {"created_at"},
    "hotel_reference_rooms": {"created_at", "updated_at", "active_from"},
}


def _checksum_default(value: Any) -> Any:
    from datetime import date, datetime
    from decimal import Decimal

    if isinstance(value, Decimal):
        return str(value)
    if isinstance(value, (datetime, date)):
        return value.isoformat()
    if isinstance(value, (bytes, bytearray)):
        return value.hex()
    raise TypeError(f"khong checksum duoc kieu {type(value)!r}")


def semantic_checksums(wh_conn, *, tables: Iterable[str] | None = None) -> dict[str, dict[str, str]]:
    import hashlib

    result: dict[str, dict[str, str]] = {"semantic": {}, "exact": {}}
    cursor = wh_conn.cursor(dictionary=True)
    try:
        for table, sql in _SEMANTIC_QUERIES.items():
            if tables is not None and table not in tables:
                continue
            # `import_batch_id` la dinh danh CUA LAN BUILD (nguoi goi dat), khong phai noi dung: 2 lan build
            # giong het nhau voi 2 batch_id khac nhau van phai cho cung checksum (test e2e bat duoc).
            volatile = _VOLATILE_COLUMNS.get(table, set()) | {"import_batch_id"}
            semantic, exact = hashlib.sha256(), hashlib.sha256()
            cursor.execute(sql)
            for row in cursor.fetchall():
                kept = {key: value for key, value in row.items() if key not in volatile}
                exact.update(json.dumps(kept, sort_keys=True, separators=(",", ":"), default=_checksum_default,
                                        ensure_ascii=False).encode("utf-8"))
                natural = {key: value for key, value in kept.items() if key not in _TECHNICAL_COLUMNS}
                semantic.update(json.dumps(natural, sort_keys=True, separators=(",", ":"), default=_checksum_default,
                                           ensure_ascii=False).encode("utf-8"))
            result["semantic"][table] = semantic.hexdigest()
            result["exact"][table] = exact.hexdigest()
    finally:
        cursor.close()
    return result


def audit_run_timestamps(staging_conn, wh_conn, *, batch_id: str, source_code: str) -> dict[str, Any]:
    """D10 gate 5: TIMESTAMP cua crawl_runs o warehouse phai TRUNG staging (khong lech mui gio)."""
    columns = ("started_at", "finished_at", "created_at", "updated_at")
    read = staging_conn.cursor(dictionary=True)
    read.execute(f"SELECT id, {', '.join(columns)} FROM crawl_runs")
    source = {row["id"]: row for row in read.fetchall()}
    read.close()
    cursor = wh_conn.cursor(dictionary=True)
    cursor.execute(
        f"SELECT m.source_run_id, {', '.join('r.' + c for c in columns)} FROM etl_run_map m "
        "JOIN crawl_runs r ON r.id=m.warehouse_run_id WHERE m.import_batch_id=%s AND m.source_code=%s",
        (batch_id, source_code))
    mismatches = [row["source_run_id"] for row in cursor.fetchall()
                  if any(row[c] != source.get(row["source_run_id"], {}).get(c) for c in columns)]
    cursor.close()
    return {"runs_checked": len(source), "mismatches": mismatches}

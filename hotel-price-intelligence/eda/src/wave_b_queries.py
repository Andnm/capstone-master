"""Query catalog cua Wave B (Curated ML EDA): moi SQL duoc publish nam o day, kem grain/scope/denominator va output schema (EDA_CURATED_PLAN.md muc 4).

Notebook KHONG chua SQL rieng. `run_query` thi hanh qua `db.read_sql` (da bat READ ONLY) va cuong che dung khop `output_schema` (thieu/thua cot => raise).
Moi query chi tham so hoa bang `%s` (dataset_version/batch_id), khong noi chuoi du lieu nguoi dung. `catalog_manifest()` tra ban ghi co SHA-256 cua tung SQL
de ghi vao `query_catalog_b.json` (bang chung dung query nao da chay).
"""
from __future__ import annotations

import hashlib
from dataclasses import dataclass
from typing import Any

import pandas as pd

import db

CATALOG_VERSION = "eda-wave-b-catalog-1.1.0"        # 1.1.0: item match dung event time cua observation + evidence reference dung hop dong builder (GPT file 56)
MAX_EXECUTION_MS = 600_000


@dataclass(frozen=True)
class Query:
    query_id: str
    grain: str
    scope: str
    numerator: str
    denominator: str
    params: tuple[str, ...]
    output_schema: tuple[str, ...]
    sql: str


class QuerySchemaError(ValueError):
    pass


def _q(query_id: str, *, grain: str, scope: str, numerator: str, denominator: str, params: tuple[str, ...], output_schema: tuple[str, ...], sql: str) -> Query:
    return Query(query_id, grain, scope, numerator, denominator, params, output_schema, sql.strip())


QUERIES: dict[str, Query] = {}
for _query in (
    _q("assignments", grain="1 dong = 1 causal reference assignment (dataset_version, hotel_id, checkin_date)", scope="ml_reference_assignments cua dung dataset_version",
       numerator="dem assignment", denominator="khong co (bang liet ke)", params=("dataset_version",),
       output_schema=("assignment_id", "hotel_id", "city", "checkin_date", "approved_at", "approving_run_warehouse_id", "evidence_run_count", "evidence_item_count",
                      "eligible_item_count", "coverage", "confidence_score"),
       sql="""
SELECT a.id AS assignment_id, a.hotel_id, h.city, a.checkin_date, a.approved_at, a.approving_run_warehouse_id, a.evidence_run_count,
       a.evidence_item_count, a.eligible_item_count, a.coverage, a.confidence_score
FROM ml_reference_assignments a
JOIN hotels h ON h.hotel_id = a.hotel_id
WHERE a.dataset_version = %s"""),
    _q("first_reference_evidence", grain="1 dong = 1 assignment co it nhat 1 item bang chung (dung hop dong `_RUN_ORDER_SQL`/`_RUN_EVIDENCE_SQL` cua causal builder, item grain)",
       scope="REFERENCE EVIDENCE: run completed + etl_run_map.include_reference + item success + etl_item_map.include_reference + EXISTS option (is_sold_out=0, gia khong null, "
             "canonical_room_key khac EMPTY); khong gioi han theo approved_at", numerator="MIN(finished_at) cua run chua bang chung dau tien",
       denominator="assignment co bang chung", params=("batch_id", "batch_id", "dataset_version", "empty_room_key"),
       output_schema=("assignment_id", "first_evidence_run_finished_at", "evidence_items_all_time"),
       sql="""
SELECT a.id AS assignment_id, MIN(r.finished_at) AS first_evidence_run_finished_at, COUNT(*) AS evidence_items_all_time
FROM ml_reference_assignments a
JOIN crawl_run_items cri ON cri.hotel_id = a.hotel_id AND cri.checkin_date = a.checkin_date AND cri.status = 'success'
JOIN etl_item_map im ON im.warehouse_item_id = cri.id AND im.import_batch_id = %s AND im.include_reference = TRUE
JOIN crawl_runs r ON r.id = cri.crawl_run_id AND r.status = 'completed'
JOIN etl_run_map rm ON rm.warehouse_run_id = r.id AND rm.import_batch_id = %s AND rm.include_reference = TRUE
WHERE a.dataset_version = %s
  AND EXISTS (SELECT 1 FROM price_observations po JOIN curated_observation_keys cok ON cok.record_id = po.record_id
              WHERE po.crawl_run_item_id = cri.id AND po.is_sold_out = 0 AND po.price_per_night IS NOT NULL AND cok.canonical_room_key <> %s)
GROUP BY a.id"""),
    _q("item_matches", grain="1 dong = 1 item (crawl_run_item) duoc khop voi assignment cua dung dataset_version", scope="ml_item_reference_matches",
       numerator="dem item theo match_status", denominator="so item trong nhom (city/lead bucket/source/observation phase)",
       params=("batch_id", "dataset_version", "dataset_version"),
       output_schema=("crawl_run_item_id", "assignment_id", "match_status", "match_score", "selected_record_id", "run_id", "checkin_date", "city", "source_code",
                      "run_started_at", "run_finished_at", "approved_at", "approving_run_warehouse_id", "event_utc", "selected_observed_at", "item_obs_min", "item_obs_max"),
       sql="""
SELECT m.crawl_run_item_id, m.ml_reference_assignment_id AS assignment_id, m.match_status, m.match_score, m.selected_record_id,
       cri.crawl_run_id AS run_id, cri.checkin_date, h.city, im.source_code, r.started_at AS run_started_at, r.finished_at AS run_finished_at,
       a.approved_at, a.approving_run_warehouse_id,
       COALESCE(sel.observed_at, ot.obs_min) AS event_utc, sel.observed_at AS selected_observed_at, ot.obs_min AS item_obs_min, ot.obs_max AS item_obs_max
FROM ml_item_reference_matches m
JOIN ml_reference_assignments a ON a.id = m.ml_reference_assignment_id AND a.dataset_version = m.dataset_version
JOIN crawl_run_items cri ON cri.id = m.crawl_run_item_id
JOIN crawl_runs r ON r.id = cri.crawl_run_id
JOIN hotels h ON h.hotel_id = a.hotel_id
JOIN etl_item_map im ON im.warehouse_item_id = cri.id AND im.import_batch_id = %s
LEFT JOIN price_observations sel ON sel.record_id = m.selected_record_id
LEFT JOIN (SELECT crawl_run_item_id, MIN(observed_at) AS obs_min, MAX(observed_at) AS obs_max FROM price_observations
           WHERE crawl_run_item_id IN (SELECT crawl_run_item_id FROM ml_item_reference_matches WHERE dataset_version = %s) GROUP BY crawl_run_item_id) ot
       ON ot.crawl_run_item_id = cri.id
WHERE m.dataset_version = %s"""),
    _q("ml_samples", grain="1 dong = 1 ml_samples (ca mau KHONG duoc chon lam daily snapshot)", scope="ml_samples cua dung dataset_version",
       numerator="dem mau", denominator="khong co (bang liet ke)", params=("dataset_version",),
       output_schema=("record_id", "assignment_id", "vn_observation_date", "is_daily_snapshot_selected", "daily_snapshot_reason", "split",
                      "has_label_h1", "label_source_record_id_h1", "has_label_h3", "label_source_record_id_h3", "has_label_h7", "label_source_record_id_h7",
                      "has_label_h14", "label_source_record_id_h14"),
       sql="""
SELECT s.record_id, s.ml_reference_assignment_id AS assignment_id, s.vn_observation_date, s.is_daily_snapshot_selected, s.daily_snapshot_reason, s.split,
       s.has_label_h1, s.label_source_record_id_h1, s.has_label_h3, s.label_source_record_id_h3, s.has_label_h7, s.label_source_record_id_h7,
       s.has_label_h14, s.label_source_record_id_h14
FROM ml_samples s
WHERE s.dataset_version = %s"""),
    _q("hotels_snapshot", grain="1 dong = 1 khach san co mau trong dataset", scope="hotels (SNAPSHOT thuoc tinh luc build warehouse, KHONG as-of)",
       numerator="review_score/review_count snapshot", denominator="khach san co mau", params=("dataset_version",),
       output_schema=("hotel_id", "city", "review_score", "review_count"),
       sql="""
SELECT h.hotel_id, h.city, h.review_score, h.review_count
FROM hotels h
WHERE h.hotel_id IN (SELECT DISTINCT a.hotel_id FROM ml_reference_assignments a WHERE a.dataset_version = %s)"""),
):
    QUERIES[_query.query_id] = _query


def run_query(conn, query_id: str, *params: Any) -> pd.DataFrame:
    """Diem goi DUY NHAT cho SQL cua Wave B. Cuong che so tham so va output schema; dat gioi han thoi gian thi hanh cho session."""
    if query_id not in QUERIES:
        raise KeyError(f"query_id {query_id!r} chua dang ky")
    query = QUERIES[query_id]
    if len(params) != len(query.params):
        raise ValueError(f"query {query_id!r} can {len(query.params)} tham so {query.params}, nhan {len(params)}")
    cursor = conn.cursor()
    try:
        cursor.execute(f"SET SESSION max_execution_time = {int(MAX_EXECUTION_MS)}")
    finally:
        cursor.close()
    frame = db.read_sql(conn, query.sql, tuple(params))
    if set(frame.columns) != set(query.output_schema):
        raise QuerySchemaError(f"query {query_id!r}: cot tra ve {sorted(frame.columns)} != output_schema {sorted(query.output_schema)}")
    return frame[list(query.output_schema)]


def catalog_manifest() -> dict[str, Any]:
    return {
        "catalog_version": CATALOG_VERSION, "max_execution_ms": MAX_EXECUTION_MS,
        "queries": [
            {"query_id": q.query_id, "grain": q.grain, "scope": q.scope, "numerator": q.numerator, "denominator": q.denominator, "params": list(q.params),
             "output_schema": list(q.output_schema), "sql": q.sql, "sql_sha256": hashlib.sha256(q.sql.encode("utf-8")).hexdigest()}
            for q in QUERIES.values()],
    }

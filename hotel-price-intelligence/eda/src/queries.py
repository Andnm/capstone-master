"""Query catalog - moi metric duoc PUBLISH trong notebook phai goi qua day (EDA_CURATED_PLAN.md muc 4).

Moi entry: `metric_id`, `title`, `grain`, `scope`, `numerator`, `denominator`, ham thuc thi (`fn`) va
`output_schema`. Notebook CHI duoc goi ham qua `CATALOG[...]` hoac ham public o day - khong duoc chua
khoi SQL rieng khong dang ky (muc 4). Moi ket noi di qua `db.read_sql`/`db.connect` (da bat READ ONLY).

Scope chuan (muc 6.1), doi chieu dung CHECK constraint that trong `etl_run_map`/`etl_item_map`:
  RAW               -> `etl_item_map.include_eda_raw = TRUE`
  MAIN              -> `etl_item_map.include_eda_main = TRUE`
  REFERENCE EVIDENCE -> run 'completed' + item 'success' + ca 2 `include_reference = TRUE`
Chi loc qua `etl_item_map` la DU (khong can join them `etl_run_map`) vi co da duoc INTERSECT voi run
cha luc import - xem `app/warehouse/ownership_manifest.py::resolve_item_ownership`
(`FLAGS_*.intersect(run_flags)`), da xac nhan lai o vong thao luan ke hoach (file 04 muc 5, GPT dong y
o file 05 quyet dinh "Query catalog la contract... Availability rate dung item grain").
"""
from __future__ import annotations

import datetime as dt
from dataclasses import dataclass
from typing import Any, Callable

import pandas as pd

import contracts
import db
import timezone

CoreTable = str

# GPT review 12 (eda) M6: version cua CHINH catalog nay, pin vao input_manifest.json - doi so luong/
# dinh nghia metric la mot thay doi dang ghi nhan y het doi code khac trong `eda/src/`.
CATALOG_VERSION = "eda-catalog-1.0.0"


@dataclass(frozen=True)
class Metric:
    metric_id: str
    title: str
    grain: str
    scope: str
    numerator: str
    denominator: str
    output_schema: tuple[str, ...]
    fn: Callable[..., "pd.DataFrame"]


CATALOG: dict[str, Metric] = {}


class MetricSchemaError(ValueError):
    """`run_metric()` phat hien DataFrame tra ve KHONG khop dung `output_schema` da khai bao."""


def _register(metric_id: str, *, title: str, grain: str, scope: str, numerator: str, denominator: str,
             output_schema: tuple[str, ...]):
    def deco(fn):
        if metric_id in CATALOG:
            raise KeyError(f"metric_id {metric_id!r} da dang ky - trung ten")
        CATALOG[metric_id] = Metric(metric_id, title, grain, scope, numerator, denominator, output_schema, fn)
        return fn
    return deco


def run_metric(metric_id: str, conn, snapshot: db.WarehouseSnapshot, **kwargs) -> "pd.DataFrame":
    """DIEM GOI DUY NHAT cho notebook (GPT review 12 eda M3): goi ham + CUONG CHE dung khop
    `output_schema` da khai bao - khong con la mo ta suong. Lech (thieu HOAC thua cot) -> raise ngay,
    khong am tham tra ve DataFrame sai hop dong (da bat duoc 1 ca that: `reference_approval_by_city_month`
    tra them cot `n` chua khai bao - da sua schema, xem lich su Git/discuss file 08).
    """
    if metric_id not in CATALOG:
        raise KeyError(f"metric_id {metric_id!r} chua dang ky trong CATALOG")
    metric = CATALOG[metric_id]
    df = metric.fn(conn, snapshot, **kwargs)
    actual_cols, expected_cols = set(df.columns), set(metric.output_schema)
    if actual_cols != expected_cols:
        raise MetricSchemaError(
            f"metric {metric_id!r}: cot tra ve {sorted(actual_cols)} KHONG khop output_schema da khai bao "
            f"{sorted(expected_cols)} - thieu {sorted(expected_cols - actual_cols)}, "
            f"thua {sorted(actual_cols - expected_cols)}."
        )
    return df


# ======================================================================== 7.1 preflight / snapshot identity
@_register(
    "preflight_core_counts", title="So dong 4 bang core + curated keys, doi soat voi validation report",
    grain="warehouse (1 dong)", scope="RAW", numerator="COUNT(*) tuyet doi", denominator="khong co",
    output_schema=("hotels", "crawl_runs", "crawl_run_items", "price_observations", "curated_observation_keys"),
)
def preflight_core_counts(conn, snapshot: db.WarehouseSnapshot) -> "pd.DataFrame":
    sql = """
        SELECT (SELECT COUNT(*) FROM hotels) hotels, (SELECT COUNT(*) FROM crawl_runs) crawl_runs,
               (SELECT COUNT(*) FROM crawl_run_items) crawl_run_items,
               (SELECT COUNT(*) FROM price_observations) price_observations,
               (SELECT COUNT(*) FROM curated_observation_keys) curated_observation_keys
    """
    return db.read_sql(conn, sql)


@_register(
    "preflight_non_terminal_runs_items", title="Run/item chua terminal (queued/running) - PHAI = 0",
    grain="warehouse (1 dong)", scope="RAW", numerator="COUNT(*) tuyet doi", denominator="khong co",
    output_schema=("non_terminal_runs", "non_terminal_items"),
)
def preflight_non_terminal_runs_items(conn, snapshot: db.WarehouseSnapshot) -> "pd.DataFrame":
    sql = """
        SELECT (SELECT COUNT(*) FROM crawl_runs WHERE status NOT IN ('completed','failed')) non_terminal_runs,
               (SELECT COUNT(*) FROM crawl_run_items WHERE status IN ('queued','running')) non_terminal_items
    """
    return db.read_sql(conn, sql)


# ======================================================================== 7.2 source / ownership / protocol coverage
@_register(
    "ownership_by_source_status_reason", title="Item theo (source, ownership_status, exclusion_reason)",
    grain="item", scope="RAW",
    numerator="COUNT(*) trong tung nhom", denominator="tong item RAW cua dung batch (bang ky mau)",
    output_schema=("source_code", "ownership_status", "exclusion_reason", "n_items"),
)
def ownership_by_source_status_reason(conn, snapshot: db.WarehouseSnapshot) -> "pd.DataFrame":
    sql = """
        SELECT source_code, ownership_status, exclusion_reason, COUNT(*) n_items
        FROM etl_item_map WHERE import_batch_id=%s AND include_eda_raw=TRUE
        GROUP BY 1, 2, 3 ORDER BY 1, 2, 3
    """
    return db.read_sql(conn, sql, (snapshot.batch_id,))


@_register(
    "run_item_observation_by_source_crawl_date", title="Run/item/observation theo nguon + ngay crawl (VN)",
    # MIN3: grain that su la 1 dong / (source_code, vn_crawl_date) - "run" sai vi 1 dong co the gom
    # NHIEU run cung ngay (COUNT(DISTINCT r.id) > 1 khi co retry/nhieu lan chay trong 1 ngay).
    grain="source x vn_crawl_date", scope="RAW",
    numerator="COUNT(*) tuyet doi", denominator="khong co (bang mo ta, khong phai ty le)",
    output_schema=("source_code", "vn_crawl_date", "runs", "items", "observations"),
)
def run_item_observation_by_source_crawl_date(conn, snapshot: db.WarehouseSnapshot) -> "pd.DataFrame":
    # GPT review 12 (eda) M3: RAW phai THUC SU loc include_eda_raw o CA run va item, khong duoc gia
    # dinh "moi core row = RAW" (dung du batch nay tinh co 100% include_eda_raw=1).
    sql = """
        SELECT rm.source_code, DATE(CONVERT_TZ(r.started_at,'+00:00','+07:00')) vn_crawl_date,
               COUNT(DISTINCT r.id) runs, COUNT(DISTINCT i.id) items, COUNT(po.record_id) observations
        FROM crawl_runs r
        JOIN etl_run_map rm ON rm.warehouse_run_id=r.id AND rm.import_batch_id=%s AND rm.include_eda_raw=TRUE
        JOIN crawl_run_items i ON i.crawl_run_id=r.id
        JOIN etl_item_map m ON m.warehouse_item_id=i.id AND m.import_batch_id=%s AND m.include_eda_raw=TRUE
        LEFT JOIN price_observations po ON po.crawl_run_item_id=i.id
        GROUP BY 1, 2 ORDER BY 1, 2
    """
    return db.read_sql(conn, sql, (snapshot.batch_id, snapshot.batch_id))


# ======================================================================== 7.8 availability - ITEM grain (GPT review 12 M2)
@_register(
    "main_item_status", title="Item MAIN (moi item 1 dong, status terminal) - input cho item_availability_rates",
    grain="item", scope="MAIN",
    numerator="n/a - day la du lieu tho cho metrics.item_availability_rates", denominator="n/a",
    output_schema=("item_id", "status", "source_code", "hotel_id", "checkin_date", "city"),
)
def main_item_status(conn, snapshot: db.WarehouseSnapshot) -> "pd.DataFrame":
    sql = """
        SELECT i.id item_id, i.status, m.source_code, i.hotel_id, i.checkin_date, h.city
        FROM crawl_run_items i
        JOIN etl_item_map m ON m.warehouse_item_id=i.id AND m.import_batch_id=%s AND m.include_eda_main=TRUE
        LEFT JOIN hotels h ON h.hotel_id=i.hotel_id
    """
    return db.read_sql(conn, sql, (snapshot.batch_id,))


# ======================================================================== 7.10 full-history reference (GPT review 12 M3)
@_register(
    "reference_observation_match_main", title="Observation (MAIN, khong sold-out) co khop canonical key reference approved",
    grain="observation", scope="MAIN",
    numerator="observation co canonical key = mot reference approved cua dung (hotel_id, checkin_date)",
    denominator="TOAN BO observation MAIN khong sold-out",
    output_schema=("record_id", "hotel_id", "checkin_date", "lead_time", "matches_approved_key"),
)
def reference_observation_match_main(conn, snapshot: db.WarehouseSnapshot) -> "pd.DataFrame":
    sql = """
        SELECT po.record_id, po.hotel_id, po.checkin_date, po.lead_time,
               (r.id IS NOT NULL) matches_approved_key
        FROM price_observations po
        JOIN etl_item_map m ON m.warehouse_item_id=po.crawl_run_item_id
         AND m.import_batch_id=%s AND m.include_eda_main=TRUE
        JOIN curated_observation_keys cok ON cok.record_id=po.record_id
        LEFT JOIN hotel_reference_rooms r ON r.hotel_id=po.hotel_id AND r.checkin_date=po.checkin_date
         AND r.status='approved' AND r.room_identity_key=cok.canonical_room_key
         AND r.rate_plan_key=cok.canonical_rate_key
        WHERE po.is_sold_out=0
    """
    df = db.read_sql(conn, sql, (snapshot.batch_id,))
    df["matches_approved_key"] = contracts.coerce_boolean_series(df["matches_approved_key"], nullable=False)
    return df


@_register(
    "reference_observation_match_raw", title="Nhu tren nhung scope RAW/toan bo non-sold-out (phu luc doi chieu CLAUDE.md)",
    grain="observation", scope="RAW",
    numerator="observation co canonical key = mot reference approved cua dung (hotel_id, checkin_date)",
    denominator="TOAN BO observation RAW khong sold-out (bao gom ca off-plan/duplicate/pre-protocol)",
    output_schema=("record_id", "hotel_id", "checkin_date", "lead_time", "matches_approved_key"),
)
def reference_observation_match_raw(conn, snapshot: db.WarehouseSnapshot) -> "pd.DataFrame":
    # GPT review 12 (eda) M3: RAW la `include_eda_raw=TRUE`, PHAI join etl_item_map that su - khong
    # gia dinh "moi core row = RAW" (batch nay tinh co 100% include_eda_raw=1, batch khac co the khac).
    sql = """
        SELECT po.record_id, po.hotel_id, po.checkin_date, po.lead_time,
               (r.id IS NOT NULL) matches_approved_key
        FROM price_observations po
        JOIN etl_item_map m ON m.warehouse_item_id=po.crawl_run_item_id
         AND m.import_batch_id=%s AND m.include_eda_raw=TRUE
        JOIN curated_observation_keys cok ON cok.record_id=po.record_id
        LEFT JOIN hotel_reference_rooms r ON r.hotel_id=po.hotel_id AND r.checkin_date=po.checkin_date
         AND r.status='approved' AND r.room_identity_key=cok.canonical_room_key
         AND r.rate_plan_key=cok.canonical_rate_key
        WHERE po.is_sold_out=0
    """
    df = db.read_sql(conn, sql, (snapshot.batch_id,))
    df["matches_approved_key"] = contracts.coerce_boolean_series(df["matches_approved_key"], nullable=False)
    return df


@_register(
    "reference_series_exists_observation_coverage_raw",
    title="Observation thuoc mot SERIES co reference approved (KHONG doi hoi dung key) - metric turnover "
          "phu tro, DA CHOT GPT file 09 muc 2 - KHAC voi exact-key o tren, xem series_has_approved_reference",
    grain="observation", scope="RAW",
    numerator="observation ma (hotel_id, checkin_date) CO mot hang hotel_reference_rooms status=approved "
              "(khong doi hoi room_identity_key/rate_plan_key cua chinh no trung reference)",
    denominator="TOAN BO observation khong sold-out (khong loc scope)",
    output_schema=("record_id", "hotel_id", "checkin_date", "lead_time", "series_has_approved_reference"),
)
def reference_series_exists_observation_coverage_raw(conn, snapshot: db.WarehouseSnapshot) -> "pd.DataFrame":
    """XEM eda docstring o dau file va discuss/eda-curated-implementation file 06/09: query nay khong
    nam trong dinh nghia M3 cua GPT (file 03) nhung duoc them de doi chieu minh bach voi bang lich su
    CLAUDE.md muc 7.2 (30,4/27,1/18,3/11,2/7,6/7,1) - vi khi tu tay verify, join theo DUNG canonical
    key (`reference_observation_match_raw`) KHONG tai hien duoc bang do (ra ~3,5% thay vi ~30%). Nguyen
    nhan: trung binh 1 series co reference co toi 13,75 cap (room,rate) canonical PHAN BIET (max 157) -
    approved chi la DUNG 1 trong so do, nen "exact key" << "series co reference".

    Cot `series_has_approved_reference` (GPT file 09 muc 2: KHONG dung ten `matches_approved_key` cho
    metric long nay - de khong the vo tinh dan nhan 30% series-exists thanh 30% exact-match). Dung
    `metrics.series_with_approved_reference_coverage()`, KHONG dung
    `metrics.exact_approved_key_observation_coverage()` cho DataFrame nay.
    """
    sql = """
        SELECT po.record_id, po.hotel_id, po.checkin_date, po.lead_time,
               (r.id IS NOT NULL) series_has_approved_reference
        FROM price_observations po
        JOIN etl_item_map m ON m.warehouse_item_id=po.crawl_run_item_id
         AND m.import_batch_id=%s AND m.include_eda_raw=TRUE
        LEFT JOIN hotel_reference_rooms r ON r.hotel_id=po.hotel_id AND r.checkin_date=po.checkin_date
         AND r.status='approved'
        WHERE po.is_sold_out=0
    """
    df = db.read_sql(conn, sql, (snapshot.batch_id,))
    df["series_has_approved_reference"] = contracts.coerce_boolean_series(
        df["series_has_approved_reference"], nullable=False)
    return df


@_register(
    "reference_item_level_availability_main", title="Success MAIN item thuoc series co reference approved - co option khop khong",
    grain="item", scope="MAIN",
    numerator="item co it nhat 1 observation khop dung key approved",
    denominator="success MAIN item thuoc (hotel_id, checkin_date) co reference approved (da loc san)",
    output_schema=("item_id", "hotel_id", "checkin_date", "lead_time", "has_matching_option"),
)
def reference_item_level_availability_main(conn, snapshot: db.WarehouseSnapshot) -> "pd.DataFrame":
    sql = """
        SELECT i.id item_id, i.hotel_id, i.checkin_date,
               MIN(po.lead_time) lead_time,
               MAX(r.id IS NOT NULL) has_matching_option
        FROM crawl_run_items i
        JOIN etl_item_map m ON m.warehouse_item_id=i.id AND m.import_batch_id=%s AND m.include_eda_main=TRUE
        JOIN hotel_reference_rooms r_exists ON r_exists.hotel_id=i.hotel_id
         AND r_exists.checkin_date=i.checkin_date AND r_exists.status='approved'
        JOIN price_observations po ON po.crawl_run_item_id=i.id AND po.is_sold_out=0
        JOIN curated_observation_keys cok ON cok.record_id=po.record_id
        LEFT JOIN hotel_reference_rooms r ON r.hotel_id=i.hotel_id AND r.checkin_date=i.checkin_date
         AND r.status='approved' AND r.room_identity_key=cok.canonical_room_key
         AND r.rate_plan_key=cok.canonical_rate_key
        WHERE i.status='success'
        GROUP BY i.id, i.hotel_id, i.checkin_date
    """
    df = db.read_sql(conn, sql, (snapshot.batch_id,))
    df["has_matching_option"] = contracts.coerce_boolean_series(df["has_matching_option"], nullable=False)
    return df


@_register(
    "reference_approval_by_city_month", title="Approved/proposed count + rate theo city, check-in month",
    grain="full-history reference series (hotel_id, checkin_date)", scope="REFERENCE EVIDENCE",
    numerator="COUNT(status='approved')", denominator="COUNT(*) series co candidate",
    output_schema=("city", "checkin_month", "approved", "proposed", "n", "approval_rate"),
)
def reference_approval_by_city_month(conn, snapshot: db.WarehouseSnapshot) -> "pd.DataFrame":
    sql = """
        SELECT h.city, DATE_FORMAT(r.checkin_date, '%%Y-%%m') checkin_month,
               SUM(r.status='approved') approved, SUM(r.status='proposed') proposed, COUNT(*) n
        FROM hotel_reference_rooms r LEFT JOIN hotels h ON h.hotel_id=r.hotel_id
        GROUP BY 1, 2 ORDER BY 1, 2
    """
    df = db.read_sql(conn, sql)
    df["approval_rate"] = df["approved"] / df["n"]
    return df


# ======================================================================== 7.7 price distribution
@_register(
    "price_observations_main", title="Observation MAIN, khong sold-out, gia hop le - dau vao price_distribution_stats",
    grain="observation", scope="MAIN",
    numerator="n/a - du lieu tho", denominator="n/a",
    output_schema=("record_id", "hotel_id", "checkin_date", "vn_observation_date", "lead_time", "city",
                   "price_per_night", "price_total"),
)
def price_observations_main(conn, snapshot: db.WarehouseSnapshot) -> "pd.DataFrame":
    sql = """
        SELECT po.record_id, po.hotel_id, po.checkin_date,
               DATE(CONVERT_TZ(po.observed_at,'+00:00','+07:00')) vn_observation_date,
               po.lead_time, h.city, po.price_per_night, po.price_total
        FROM price_observations po
        JOIN etl_item_map m ON m.warehouse_item_id=po.crawl_run_item_id
         AND m.import_batch_id=%s AND m.include_eda_main=TRUE
        LEFT JOIN hotels h ON h.hotel_id=po.hotel_id
        WHERE po.is_sold_out=0 AND po.price_per_night IS NOT NULL AND po.price_per_night > 0
    """
    return db.read_sql(conn, sql, (snapshot.batch_id,))


# ======================================================================== 7.11 mot so quality finding (tap con)
@_register(
    "quality_price_non_positive", title="Observation KHONG sold-out nhung gia <= 0 hoac NULL",
    grain="observation", scope="RAW",
    numerator="COUNT(*) vi pham", denominator="tat ca observation khong sold-out",
    output_schema=("n_violations", "n_total"),
)
def quality_price_non_positive(conn, snapshot: db.WarehouseSnapshot) -> "pd.DataFrame":
    sql = """
        SELECT SUM(po.price_per_night IS NULL OR po.price_per_night <= 0) n_violations, COUNT(*) n_total
        FROM price_observations po
        JOIN etl_item_map m ON m.warehouse_item_id=po.crawl_run_item_id
         AND m.import_batch_id=%s AND m.include_eda_raw=TRUE
        WHERE po.is_sold_out=0
    """
    return db.read_sql(conn, sql, (snapshot.batch_id,))


@_register(
    "quality_checkout_not_after_checkin", title="checkout_date <> checkin_date + 1 dem (CLAUDE.md muc 4.3: chi cao 1 dem)",
    grain="observation", scope="RAW", numerator="COUNT(*) vi pham", denominator="tat ca observation",
    output_schema=("n_violations",),
)
def quality_checkout_not_after_checkin(conn, snapshot: db.WarehouseSnapshot) -> "pd.DataFrame":
    # MIN3: query cu chi bat `checkout<=checkin` nhung mo ta noi "phai +1 dem" - lech nghia vu, va bo
    # sot ca truong hop checkout=checkin+2+ (van "sau" checkin nhung SAI nghiep vu 1 dem). Doi thanh
    # DATEDIFF<>1 de kiem dung invariant nghiep vu, khong chi "khong am".
    sql = """
        SELECT COUNT(*) n_violations FROM price_observations po
        JOIN etl_item_map m ON m.warehouse_item_id=po.crawl_run_item_id
         AND m.import_batch_id=%s AND m.include_eda_raw=TRUE
        WHERE DATEDIFF(po.checkout_date, po.checkin_date) <> 1
    """
    return db.read_sql(conn, sql, (snapshot.batch_id,))


@_register(
    "quality_success_item_without_observation", title="Item status='success' nhung khong co observation nao",
    grain="item", scope="MAIN", numerator="COUNT(*) vi pham", denominator="tat ca success MAIN item",
    output_schema=("n_violations", "n_total"),
)
def quality_success_item_without_observation(conn, snapshot: db.WarehouseSnapshot) -> "pd.DataFrame":
    # Dem 2 subquery doc lap (thay vi 1 GROUP BY roi loc) de tranh nham lan HAVING COUNT(...)=0 sau
    # LEFT JOIN + GROUP BY - cach do de bug thanh "luon dung" (COUNT(po.record_id) luon >= 0).
    sql = """
        SELECT
          (SELECT COUNT(*) FROM crawl_run_items i
             JOIN etl_item_map m ON m.warehouse_item_id=i.id AND m.import_batch_id=%s AND m.include_eda_main=TRUE
             WHERE i.status='success'
               AND NOT EXISTS (SELECT 1 FROM price_observations po WHERE po.crawl_run_item_id=i.id)
          ) n_violations,
          (SELECT COUNT(*) FROM crawl_run_items i
             JOIN etl_item_map m ON m.warehouse_item_id=i.id AND m.import_batch_id=%s AND m.include_eda_main=TRUE
             WHERE i.status='success'
          ) n_total
    """
    return db.read_sql(conn, sql, (snapshot.batch_id, snapshot.batch_id))


# ======================================================================== Wave B preflight (GPT review 12 eda M7)
@_register(
    "wave_b_dataset_version_readiness",
    title="dataset_build_manifests status=pass + 3 bang ml_* CUNG dataset_version - GPT review 12 M7",
    grain="dataset_version", scope="ML CURATED",
    numerator="n/a - bang readiness, khong phai ty le", denominator="n/a",
    output_schema=("dataset_version", "import_batch_id", "status", "n_reference_assignments",
                   "n_item_matches", "n_samples", "ready"),
)
def wave_b_dataset_version_readiness(conn, snapshot: db.WarehouseSnapshot) -> "pd.DataFrame":
    """SUA loi cu (GPT review 12 M7): dem TONG 4 bang co the bao "san sang" ngay ca khi manifest dang
    'running'/'fail' hoac 3 bang ml_* thuoc CAC dataset_version khac nhau khong nhat quan. O day BAT
    BUOC chon dataset_build_manifests.status='pass' TRUOC, roi dem 3 bang ml_* cho CUNG dataset_version
    do; `ready=TRUE` chi khi ca 3 dem > 0 cho DUNG version pass do.

    MIN4 (GPT review 12 file 09): them `dbm.import_batch_id=%s` pin dung batch dang doc - 1 DB co the
    chua nhieu ban ghi `dataset_build_manifests` cua NHIEU batch khac nhau qua thoi gian; khong pin se
    tra ve dataset_version cua batch KHAC (rebuild sau) du dang doc warehouse snapshot cu hon."""
    sql = """
        SELECT dbm.dataset_version, dbm.import_batch_id, dbm.status,
               (SELECT COUNT(*) FROM ml_reference_assignments a WHERE a.dataset_version=dbm.dataset_version) n_reference_assignments,
               (SELECT COUNT(*) FROM ml_item_reference_matches x WHERE x.dataset_version=dbm.dataset_version) n_item_matches,
               (SELECT COUNT(*) FROM ml_samples s WHERE s.dataset_version=dbm.dataset_version) n_samples
        FROM dataset_build_manifests dbm
        WHERE dbm.status='pass' AND dbm.import_batch_id=%s
        ORDER BY dbm.finished_at DESC
    """
    df = db.read_sql(conn, sql, (snapshot.batch_id,))
    df["ready"] = (df["n_reference_assignments"] > 0) & (df["n_item_matches"] > 0) & (df["n_samples"] > 0)
    df["ready"] = contracts.coerce_boolean_series(df["ready"], nullable=False)
    return df


# ======================================================================== 7.12 readiness
@_register(
    "series_history_length", title="So ngay quan sat + so evidence run cho moi (hotel_id, checkin_date, canonical_series_id)",
    grain="canonical series", scope="MAIN",
    numerator="n/a - dau vao cho metrics.canonical_series_turnover + readiness table", denominator="n/a",
    output_schema=("hotel_id", "checkin_date", "canonical_series_id", "vn_observation_date"),
)
def series_daily_presence_main(conn, snapshot: db.WarehouseSnapshot) -> "pd.DataFrame":
    sql = """
        SELECT DISTINCT po.hotel_id, po.checkin_date, cok.canonical_series_id,
               DATE(CONVERT_TZ(po.observed_at,'+00:00','+07:00')) vn_observation_date
        FROM price_observations po
        JOIN etl_item_map m ON m.warehouse_item_id=po.crawl_run_item_id
         AND m.import_batch_id=%s AND m.include_eda_main=TRUE
        JOIN curated_observation_keys cok ON cok.record_id=po.record_id
        WHERE po.is_sold_out=0
    """
    return db.read_sql(conn, sql, (snapshot.batch_id,))


def run_all_scalar_metrics(conn, snapshot: db.WarehouseSnapshot) -> dict[str, "pd.DataFrame"]:
    """Tien ich cho notebook: chay tat ca metric 1-dong (preflight/quality scalar) trong 1 lan, tra ve
    dict {metric_id: DataFrame}. Khong bao gom metric can group_cols/tham so tu notebook chon."""
    scalar_ids = (
        "preflight_core_counts", "preflight_non_terminal_runs_items", "quality_price_non_positive",
        "quality_checkout_not_after_checkin", "quality_success_item_without_observation",
        "quality_canonical_key_anomalies", "quality_city_outside_scope", "quality_unexpected_nulls_by_field_group",
    )
    return {metric_id: run_metric(metric_id, conn, snapshot) for metric_id in scalar_ids}


# ======================================================================== 7.3 crawl operations / capacity
@_register(
    "run_duration_and_throughput", title="Thoi luong + throughput moi crawl run (VN)",
    grain="run", scope="RAW",
    numerator="n/a - bang mo ta, khong phai ty le", denominator="n/a",
    output_schema=("source_code", "run_id", "vn_crawl_date", "started_at_vn", "finished_at_vn",
                   "duration_minutes", "n_items", "n_observations", "items_per_hour",
                   "observations_per_hour", "crosses_next_crawl_day"),
)
def run_duration_and_throughput(conn, snapshot: db.WarehouseSnapshot) -> "pd.DataFrame":
    sql = """
        SELECT rm.source_code, r.id run_id,
               DATE(CONVERT_TZ(r.started_at,'+00:00','+07:00')) vn_crawl_date,
               CONVERT_TZ(r.started_at,'+00:00','+07:00') started_at_vn,
               CONVERT_TZ(r.finished_at,'+00:00','+07:00') finished_at_vn,
               TIMESTAMPDIFF(SECOND, r.started_at, r.finished_at) / 60.0 duration_minutes,
               COUNT(DISTINCT i.id) n_items, COUNT(po.record_id) n_observations,
               (DATE(CONVERT_TZ(r.finished_at,'+00:00','+07:00'))
                > DATE(CONVERT_TZ(r.started_at,'+00:00','+07:00'))) crosses_next_crawl_day
        FROM crawl_runs r
        JOIN etl_run_map rm ON rm.warehouse_run_id=r.id AND rm.import_batch_id=%s AND rm.include_eda_raw=TRUE
        JOIN crawl_run_items i ON i.crawl_run_id=r.id
        JOIN etl_item_map m ON m.warehouse_item_id=i.id AND m.import_batch_id=%s AND m.include_eda_raw=TRUE
        LEFT JOIN price_observations po ON po.crawl_run_item_id=i.id
        WHERE r.finished_at IS NOT NULL
        GROUP BY 1, 2, 3, 4, 5, 6
        ORDER BY 1, 3
    """
    df = db.read_sql(conn, sql, (snapshot.batch_id, snapshot.batch_id))
    df["crosses_next_crawl_day"] = contracts.coerce_boolean_series(df["crosses_next_crawl_day"], nullable=False)
    hours = df["duration_minutes"] / 60.0
    df["items_per_hour"] = df["n_items"] / hours.replace(0, pd.NA)
    df["observations_per_hour"] = df["n_observations"] / hours.replace(0, pd.NA)
    return df


# ======================================================================== 7.4 hotel / check-in coverage
@_register(
    "active_hotel_by_crawl_date_source", title="So hotel active (>=1 owned terminal item) theo ngay crawl + nguon",
    grain="crawl day", scope="MAIN",
    numerator="COUNT(DISTINCT hotel_id) co it nhat 1 owned terminal item trong ngay",
    denominator="khong co - bang mo ta theo thoi gian",
    output_schema=("source_code", "vn_crawl_date", "n_active_hotels"),
)
def active_hotel_by_crawl_date_source(conn, snapshot: db.WarehouseSnapshot) -> "pd.DataFrame":
    # "active" = co it nhat 1 item OWNED (ownership_status owner_success/owner_failure) o trang thai
    # terminal trong ngay - KHONG dung hotels.booking_status hien tai (se viet lai lich su, muc 7.4).
    sql = """
        SELECT m.source_code, DATE(CONVERT_TZ(r.started_at,'+00:00','+07:00')) vn_crawl_date,
               COUNT(DISTINCT i.hotel_id) n_active_hotels
        FROM crawl_run_items i
        JOIN etl_item_map m ON m.warehouse_item_id=i.id AND m.import_batch_id=%s
         AND m.ownership_status IN ('owner_success','owner_failure')
        JOIN crawl_runs r ON r.id=i.crawl_run_id
        GROUP BY 1, 2 ORDER BY 1, 2
    """
    return db.read_sql(conn, sql, (snapshot.batch_id,))


def actual_crawl_dates_by_source(conn, snapshot: db.WarehouseSnapshot) -> dict[str, set]:
    """Khong phai catalog metric (khong publish rieng) - tien ich lay `{source_code: {crawl_date VN co
    IT NHAT 1 run}}` THAT SU co trong batch.

    GPT review 12 eda M1 (sua loi cu): TRUOC DAY ham nay dung de GIOI HAN truoc universe expected (loc
    het cac ngay khong co run truoc khi LEFT JOIN) - do la chinh nguyen nhan `missing_run` khong bao
    gio xuat hien. Bay gio dung SAU khi `classify_outcomes()` da LEFT JOIN xong, chi de PHAN LOAI 2
    tang cho cac dong con thieu: co dong cho `(source, crawl_date)` nay hay khong -> phan biet
    `missing_source_run` (khong co dong nao) voi `missing_item_in_existing_run` (co dong, tuc co it
    nhat 1 run ngay do, nhung item/hotel nay khong nam trong do)."""
    sql = """
        SELECT DISTINCT rm.source_code, DATE(CONVERT_TZ(r.started_at,'+00:00','+07:00')) vn_crawl_date
        FROM crawl_runs r JOIN etl_run_map rm ON rm.warehouse_run_id=r.id AND rm.import_batch_id=%s
    """
    df = db.read_sql(conn, sql, (snapshot.batch_id,))
    result: dict[str, set] = {}
    for source_code, group in df.groupby("source_code"):
        result[source_code] = set(group["vn_crawl_date"])
    return result


def quality_violation_samples(
    conn, snapshot: db.WarehouseSnapshot, *, sample_size: int = 10,
) -> dict[str, list]:
    """Khong phai catalog metric - sample key THAT (record_id/item_id/hotel_id, LIMIT `sample_size`)
    cho tung quality check trong `wave_a.build_quality_findings` (GPT review 12 eda MIN1: 'sample_keys'
    khong duoc rong khi count>0). Chi de audit/debug, KHONG anh huong n_violations/n_total (van tinh o
    cac catalog metric rieng qua `run_all_scalar_metrics`)."""
    samples: dict[str, list] = {}

    sql_price = """
        SELECT po.record_id FROM price_observations po
        JOIN etl_item_map m ON m.warehouse_item_id=po.crawl_run_item_id
         AND m.import_batch_id=%s AND m.include_eda_raw=TRUE
        WHERE po.is_sold_out=0 AND (po.price_per_night IS NULL OR po.price_per_night<=0)
        ORDER BY po.record_id LIMIT %s
    """
    samples["price_non_positive"] = db.read_sql(
        conn, sql_price, (snapshot.batch_id, sample_size))["record_id"].tolist()

    sql_checkout = """
        SELECT po.record_id FROM price_observations po
        JOIN etl_item_map m ON m.warehouse_item_id=po.crawl_run_item_id
         AND m.import_batch_id=%s AND m.include_eda_raw=TRUE
        WHERE DATEDIFF(po.checkout_date, po.checkin_date) <> 1
        ORDER BY po.record_id LIMIT %s
    """
    samples["checkout_not_after_checkin"] = db.read_sql(
        conn, sql_checkout, (snapshot.batch_id, sample_size))["record_id"].tolist()

    sql_success_no_obs = """
        SELECT i.id item_id FROM crawl_run_items i
        JOIN etl_item_map m ON m.warehouse_item_id=i.id AND m.import_batch_id=%s AND m.include_eda_main=TRUE
        WHERE i.status='success'
          AND NOT EXISTS (SELECT 1 FROM price_observations po WHERE po.crawl_run_item_id=i.id)
        ORDER BY i.id LIMIT %s
    """
    samples["success_item_without_observation"] = db.read_sql(
        conn, sql_success_no_obs, (snapshot.batch_id, sample_size))["item_id"].tolist()

    db._ensure_backend_importable()
    from app.warehouse.canonicalize import EMPTY_ROOM_KEY
    sql_canonical = """
        SELECT po.record_id FROM price_observations po
        JOIN etl_item_map m ON m.warehouse_item_id=po.crawl_run_item_id
         AND m.import_batch_id=%s AND m.include_eda_raw=TRUE
        LEFT JOIN curated_observation_keys cok ON cok.record_id=po.record_id
        WHERE (po.is_sold_out=0 AND (cok.record_id IS NULL OR cok.canonical_room_key IS NULL))
           OR (po.is_sold_out=1 AND cok.canonical_room_key IS NOT NULL AND cok.canonical_room_key<>%s)
        ORDER BY po.record_id LIMIT %s
    """
    samples["canonical_key_anomalies"] = db.read_sql(
        conn, sql_canonical, (snapshot.batch_id, EMPTY_ROOM_KEY, sample_size))["record_id"].tolist()

    sql_city = """
        SELECT hotel_id FROM hotels
        WHERE city IS NOT NULL AND city NOT IN ('Hồ Chí Minh','Hà Nội','Vũng Tàu','Đà Lạt','Phú Quốc')
        ORDER BY hotel_id LIMIT %s
    """
    samples["city_outside_scope"] = db.read_sql(conn, sql_city, (sample_size,))["hotel_id"].tolist()

    return samples


def dump_taken_at_vn_date_by_source(conn, snapshot: db.WarehouseSnapshot) -> dict[str, "dt.date"]:
    """Khong phai catalog metric - ngay VN cua `dump_taken_at` moi nguon (tu `etl_import_sources`),
    dung lam UPPER BOUND (`cutoff_date_by_source`) cho `protocol_schedule.expected_schedule` (GPT
    review 12 eda M1). `dump_taken_at` la DATETIME (KHONG phai TIMESTAMP) luu dung gia tri UTC tu
    source manifest (`app.warehouse.source_manifest.iso_utc`) - MySQL tra ve literal, khong can
    CONVERT_TZ; chi can cong offset VN co dinh qua `timezone.to_vn_date`."""
    sql = "SELECT source_code, dump_taken_at FROM etl_import_sources WHERE import_batch_id=%s"
    df = db.read_sql(conn, sql, (snapshot.batch_id,))
    return {row.source_code: timezone.to_vn_date(row.dump_taken_at) for row in df.itertuples()}


@_register(
    "protocol_continuity_actual",
    title="Item da resolve owner_success/owner_failure + source_hotel_link/hash - dau vao "
          "protocol_schedule.resolve_effective_hotel_id/classify_outcomes",
    grain="item", scope="MAIN",
    numerator="n/a - du lieu tho cho protocol_schedule.classify_outcomes", denominator="n/a",
    output_schema=("source_code", "crawl_date", "checkin_date", "hotel_id", "source_hotel_link",
                   "source_link_hash", "outcome"),
)
def protocol_continuity_actual(conn, snapshot: db.WarehouseSnapshot) -> "pd.DataFrame":
    # GPT review 12 eda M2 (sua loi cu): TRUOC DAY loc `WHERE i.hotel_id IS NOT NULL` roi bao rieng
    # item hotel_id=NULL o metric `protocol_continuity_unattributed_errors` - GPT chi ra day la SAI:
    # da xac minh 623/623 item hotel_id=NULL nay VAN co the resolve duoc hotel that qua
    # `extract_hotel_slug(source_hotel_link)` (deu la item 'error' dead-link/CAPTCHA truoc khi parser
    # luu duoc hotel_id, nhung URL van con nguyen slug). Loc cung roi bao rieng lam expected row
    # tuong ung vua thanh "missing_run" (SAI - that ra co lam nhung khong duoc gan) VUA bi dem lai o
    # bucket unattributed - double-count. Bay gio KHONG loc gi ca, tra ca `source_hotel_link` +
    # `source_link_hash` de `protocol_schedule.resolve_effective_hotel_id()` tu resolve/phan loai.
    sql = """
        SELECT m.source_code, DATE(CONVERT_TZ(r.started_at,'+00:00','+07:00')) crawl_date,
               i.checkin_date, i.hotel_id, i.source_hotel_link, i.source_link_hash,
               IF(m.ownership_status='owner_success', 'owner_success', m.exclusion_reason) outcome
        FROM crawl_run_items i
        JOIN etl_item_map m ON m.warehouse_item_id=i.id AND m.import_batch_id=%s
         AND m.ownership_status IN ('owner_success','owner_failure')
        JOIN crawl_runs r ON r.id=i.crawl_run_id
    """
    return db.read_sql(conn, sql, (snapshot.batch_id,))


# ======================================================================== 7.9 missingness theo field group
_MISSINGNESS_FIELD_GROUPS: dict[str, tuple[str, ...]] = {
    "room_identity": ("room_type_raw", "max_occupancy", "bed_config", "room_area"),
    "rate_plan": ("breakfast_included", "cancellation_policy", "price_includes_tax"),
    "price": ("price_per_night", "taxes_fees"),
    "hotel_attributes": ("review_score", "review_count", "address"),
}


@_register(
    "missingness_available_observations", title="Missingness theo field group tren observation KHONG sold-out (MAIN)",
    grain="observation", scope="MAIN",
    numerator="COUNT(field IS NULL)", denominator="COUNT(*) observation KHONG sold-out trong nhom",
    output_schema=("source_code", "field", "field_group", "n_null", "n_total", "null_rate"),
)
def missingness_available_observations(conn, snapshot: db.WarehouseSnapshot) -> "pd.DataFrame":
    fields = [f for group in _MISSINGNESS_FIELD_GROUPS.values() for f in group]
    hotel_fields = {"review_score", "review_count", "address"}
    select_parts = ", ".join(
        f"SUM(po.{f} IS NULL) n_null_{f}" if f not in hotel_fields else f"SUM(h.{f} IS NULL) n_null_{f}"
        for f in fields
    )
    sql = f"""
        SELECT m.source_code, COUNT(*) n_total, {select_parts}
        FROM price_observations po
        JOIN etl_item_map m ON m.warehouse_item_id=po.crawl_run_item_id
         AND m.import_batch_id=%s AND m.include_eda_main=TRUE
        LEFT JOIN hotels h ON h.hotel_id=po.hotel_id
        WHERE po.is_sold_out=0
        GROUP BY 1
    """
    wide = db.read_sql(conn, sql, (snapshot.batch_id,))
    rows = []
    for _, row in wide.iterrows():
        for group_name, group_fields in _MISSINGNESS_FIELD_GROUPS.items():
            for field in group_fields:
                n_null = int(row[f"n_null_{field}"])
                n_total = int(row["n_total"])
                rows.append({
                    "source_code": row["source_code"], "field": field, "field_group": group_name,
                    "n_null": n_null, "n_total": n_total,
                    "null_rate": (n_null / n_total) if n_total else 0.0,
                })
    return pd.DataFrame(rows, columns=["source_code", "field", "field_group", "n_null", "n_total", "null_rate"])


# ======================================================================== them 7.11 quality checks
@_register(
    "quality_canonical_key_anomalies", title="Non-sold-out thieu canonical key; sold-out ma co canonical room key",
    grain="observation", scope="RAW",
    numerator="COUNT(*) vi pham tung loai", denominator="tat ca observation RAW",
    output_schema=("n_nonsoldout_missing_canonical", "n_soldout_with_room_key", "n_total"),
)
def quality_canonical_key_anomalies(conn, snapshot: db.WarehouseSnapshot) -> "pd.DataFrame":
    # EMPTY_ROOM_KEY = sha256 cua room rong (P-A: sold-out -> EMPTY sentinel, KHONG phai chuoi '0'*64 -
    # tu import gia tri THAT tu chinh module canonicalize thay vi doan/hardcode; da tung sai o ban nhap
    # dau (doan la '0'*64), tu bat truoc khi gui review).
    db._ensure_backend_importable()
    from app.warehouse.canonicalize import EMPTY_ROOM_KEY

    sql = """
        SELECT
          SUM(po.is_sold_out=0 AND (cok.record_id IS NULL OR cok.canonical_room_key IS NULL)) n_nonsoldout_missing_canonical,
          SUM(po.is_sold_out=1 AND cok.canonical_room_key IS NOT NULL
              AND cok.canonical_room_key <> %s) n_soldout_with_room_key,
          COUNT(*) n_total
        FROM price_observations po
        JOIN etl_item_map m ON m.warehouse_item_id=po.crawl_run_item_id
         AND m.import_batch_id=%s AND m.include_eda_raw=TRUE
        LEFT JOIN curated_observation_keys cok ON cok.record_id=po.record_id
    """
    return db.read_sql(conn, sql, (EMPTY_ROOM_KEY, snapshot.batch_id))


@_register(
    "quality_city_outside_scope", title="Hotel co city ngoai 5 thanh pho scope (khong tinh NULL)",
    grain="hotel", scope="RAW", numerator="COUNT(*) vi pham", denominator="tat ca hotel co city",
    output_schema=("n_violations", "n_total"),
)
def quality_city_outside_scope(conn, snapshot: db.WarehouseSnapshot) -> "pd.DataFrame":
    sql = """
        SELECT SUM(city NOT IN ('Hồ Chí Minh','Hà Nội','Vũng Tàu','Đà Lạt','Phú Quốc')) n_violations,
               COUNT(*) n_total
        FROM hotels WHERE city IS NOT NULL
    """
    return db.read_sql(conn, sql)


@_register(
    "quality_unexpected_nulls_by_field_group", title="NULL ngoai du kien tren cac field_value_cell available (tong hop)",
    # MIN2 (GPT review 12 file 09): sau khi sua bug >100%, mau so la SO CELL (source, field, observation)
    # - 1 observation dong gop 12 cell (1 cell/field theo doi). Doi ten cot de khong goi nham day la ty
    # le OBSERVATION bi missing (1 observation co the co NHIEU cell NULL cung luc).
    grain="field_value_cell (source, field, observation)", scope="MAIN",
    numerator="tong n_null cua missingness_available_observations",
    denominator="tong n_total tuong ung",
    output_schema=("n_unexpected_null_field_value_cells", "n_total_field_value_cells"),
)
def quality_unexpected_nulls_by_field_group(conn, snapshot: db.WarehouseSnapshot) -> "pd.DataFrame":
    # Structural missing (sold-out khong co room payload) da bi loai vi query nguon la is_sold_out=0
    # (muc 7.9: "structural missing phai tach khoi unexpected missing tren available observation").
    #
    # BUG da tu bat (chay tren du lieu that): ban dau cong `n_null` tren CA 12 field (moi field 1 dong
    # trong missingness_available_observations) nhung lay `n_total` DA KHU TRUNG theo source_code -
    # ra ty le >100% (1.504.006/1.276.337) vi tu so cong tren 12 field ma mau so chi tinh 1 field.
    # Sua: cong CA HAI o CUNG grain (moi dong = 1 (source,field)), de tu so/mau so cung don vi
    # "so 6 (source,field,observation)" = field_value_cell.
    detail = run_metric("missingness_available_observations", conn, snapshot)
    return pd.DataFrame({
        "n_unexpected_null_field_value_cells": [int(detail["n_null"].sum())],
        "n_total_field_value_cells": [int(detail["n_total"].sum())],
    })

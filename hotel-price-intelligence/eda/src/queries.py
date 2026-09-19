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
import metrics
import sql_builders as sqlb
import timezone

CoreTable = str

# GPT review 12 (eda) M6: version cua CHINH catalog nay, pin vao input_manifest.json - doi so luong/
# dinh nghia metric la mot thay doi dang ghi nhan y het doi code khac trong `eda/src/`.
CATALOG_VERSION = "eda-catalog-1.1.0"


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


@_register(
    "preflight_rejections", title="So dong etl_import_rejections cua batch (plan 7.1: doi soat 'rejections' voi validation report)",
    grain="warehouse (1 dong)", scope="RAW", numerator="COUNT(*) tuyet doi", denominator="khong co",
    output_schema=("n_rejections", "n_unwaived"),
)
def preflight_rejections(conn, snapshot: db.WarehouseSnapshot) -> "pd.DataFrame":
    sql = "SELECT COUNT(*) n_rejections, COALESCE(SUM(waived=FALSE), 0) n_unwaived FROM etl_import_rejections WHERE import_batch_id=%s"
    return db.read_sql(conn, sql, (snapshot.batch_id,))


@_register(
    "preflight_import_sources", title="Danh sach nguon cua batch (plan 7.1: 'source list') tu etl_import_sources",
    grain="source", scope="RAW", numerator="n/a - bang mo ta", denominator="n/a",
    output_schema=("source_code", "source_priority", "dump_taken_at", "dump_sha256", "schema_sha256"),
)
def preflight_import_sources(conn, snapshot: db.WarehouseSnapshot) -> "pd.DataFrame":
    sql = (
        "SELECT source_code, source_priority, dump_taken_at, dump_sha256, schema_sha256 "
        "FROM etl_import_sources WHERE import_batch_id=%s ORDER BY source_priority"
    )
    return db.read_sql(conn, sql, (snapshot.batch_id,))


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


@_register(
    "raw_vs_main_by_source", title="RAW so voi MAIN theo nguon: so item + so observation (plan 7.2 'RAW so voi MAIN theo source')",
    grain="source", scope="RAW+MAIN",
    numerator="SUM(include_eda_main) so voi SUM(include_eda_raw) tren item_map; observation nhan theo co cua item cha",
    denominator="khong co - bang doi chieu tuyet doi; ty le MAIN/RAW = main_share_*",
    output_schema=("source_code", "n_items_raw", "n_items_main", "n_observations_raw", "n_observations_main",
                   "n_priced_observations_raw", "n_priced_observations_main"),
)
def raw_vs_main_by_source(conn, snapshot: db.WarehouseSnapshot) -> "pd.DataFrame":
    # observation gom theo item cha 1 lan (derived table) roi nhan voi co include_* cua item - nhu vay MAIN/RAW la loc THAT
    # cua etl_item_map, khong phai gia dinh "moi row = RAW". Priced = is_sold_out=0 (khong tinh sentinel sold-out).
    sql = """
        SELECT m.source_code,
               SUM(m.include_eda_raw) n_items_raw, SUM(m.include_eda_main) n_items_main,
               SUM(m.include_eda_raw * COALESCE(p.n_obs, 0)) n_observations_raw,
               SUM(m.include_eda_main * COALESCE(p.n_obs, 0)) n_observations_main,
               SUM(m.include_eda_raw * COALESCE(p.n_priced, 0)) n_priced_observations_raw,
               SUM(m.include_eda_main * COALESCE(p.n_priced, 0)) n_priced_observations_main
        FROM etl_item_map m
        LEFT JOIN (
          SELECT crawl_run_item_id, COUNT(*) n_obs, SUM(is_sold_out=0) n_priced
          FROM price_observations GROUP BY crawl_run_item_id
        ) p ON p.crawl_run_item_id=m.warehouse_item_id
        WHERE m.import_batch_id=%s
        GROUP BY m.source_code ORDER BY m.source_code
    """
    df = db.read_sql(conn, sql, (snapshot.batch_id,))
    for column in df.columns.drop("source_code"):
        df[column] = df[column].fillna(0).astype("int64")
    return df


# ======================================================================== 7.8 availability - ITEM grain, aggregate SQL (GPT review 12 M2, file 11 muc 4/5)
# TRUOC DAY `main_item_status` tra 1 dong/item (~167k dong, tang tuyen tinh theo so ngay crawl) ve pandas de dem status;
# gio moi bang la COUNT theo status GROUP BY thang trong SQL (item-grain van la don vi dem - moi item dung 1 lan - nhung
# Python khong con giu frame item). `n_items` = tong 5 status terminal; lech (status la) => raise (khong am tham bo).
_ITEM_CRAWL_VN_DATE = "DATE(CONVERT_TZ(r.started_at,'+00:00','+07:00'))"
_ITEM_STATUS_COUNT_COLUMNS = ("n_success", "n_sold_out", "n_not_bookable", "n_partial", "n_error", "n_items")
_ITEM_LEAD_TIME_BUCKET = "COALESCE(" + metrics.lead_time_bucket_sql_case(f"DATEDIFF(i.checkin_date, {_ITEM_CRAWL_VN_DATE})") + ", '(invalid)')"


def _item_status_counts(conn, snapshot: db.WarehouseSnapshot, *, scope: str = "MAIN",
                        group_exprs: dict[str, str] | None = None) -> "pd.DataFrame":
    group_exprs = dict(group_exprs or {})
    dims = "".join(f"{expr} {alias}, " for alias, expr in group_exprs.items())
    positions = ", ".join(str(n) for n in range(1, len(group_exprs) + 1))
    tail = f"GROUP BY {positions} ORDER BY {positions}" if group_exprs else ""
    sql = f"""
        SELECT {dims}SUM(i.status='success') n_success, SUM(i.status='sold_out') n_sold_out,
               SUM(i.status='not_bookable') n_not_bookable, SUM(i.status='partial') n_partial,
               SUM(i.status='error') n_error, COUNT(*) n_items
        FROM crawl_run_items i
        JOIN etl_item_map m ON m.warehouse_item_id=i.id AND m.import_batch_id=%s AND m.{_SCOPE_FLAG[scope]}=TRUE
        JOIN crawl_runs r ON r.id=i.crawl_run_id
        LEFT JOIN hotels h ON h.hotel_id=i.hotel_id
        {tail}
    """
    return metrics.finalize_item_status_counts(db.read_sql(conn, sql, (snapshot.batch_id,)), list(group_exprs))


@_register(
    "item_status_counts_overall_main", title="Dem item MAIN theo status terminal - toan bo (1 dong)",
    grain="item", scope="MAIN", numerator="COUNT(*) item trong tung status", denominator="COUNT(*) item MAIN terminal (n_items)",
    output_schema=_ITEM_STATUS_COUNT_COLUMNS,
)
def item_status_counts_overall_main(conn, snapshot: db.WarehouseSnapshot) -> "pd.DataFrame":
    return _item_status_counts(conn, snapshot)


@_register(
    "item_status_counts_by_city_main", title="Dem item MAIN theo status terminal, theo thanh pho",
    grain="item", scope="MAIN", numerator="COUNT(*) item trong tung status", denominator="COUNT(*) item MAIN trong city (n_items)",
    output_schema=("city", *_ITEM_STATUS_COUNT_COLUMNS),
)
def item_status_counts_by_city_main(conn, snapshot: db.WarehouseSnapshot) -> "pd.DataFrame":
    return _item_status_counts(conn, snapshot, group_exprs={"city": "COALESCE(h.city,'(unknown)')"})


@_register(
    "item_status_counts_by_hotel_main", title="Dem item MAIN theo status terminal, theo hotel (plan 7.8: sold-out rate theo hotel)",
    grain="item", scope="MAIN", numerator="COUNT(*) item trong tung status", denominator="COUNT(*) item MAIN cua hotel (n_items)",
    output_schema=("hotel_id", "city", *_ITEM_STATUS_COUNT_COLUMNS),
)
def item_status_counts_by_hotel_main(conn, snapshot: db.WarehouseSnapshot) -> "pd.DataFrame":
    return _item_status_counts(conn, snapshot, group_exprs={
        "hotel_id": "COALESCE(i.hotel_id,'(unattributed)')", "city": "COALESCE(h.city,'(unknown)')"})


@_register(
    "item_status_counts_by_checkin_month_main", title="Dem item MAIN theo status terminal, theo thang check-in",
    grain="item", scope="MAIN", numerator="COUNT(*) item trong tung status", denominator="COUNT(*) item MAIN trong thang (n_items)",
    output_schema=("checkin_month", *_ITEM_STATUS_COUNT_COLUMNS),
)
def item_status_counts_by_checkin_month_main(conn, snapshot: db.WarehouseSnapshot) -> "pd.DataFrame":
    return _item_status_counts(conn, snapshot, group_exprs={
        "checkin_month": "CONCAT(YEAR(i.checkin_date), '-', LPAD(MONTH(i.checkin_date), 2, '0'))"})


@_register(
    "item_status_counts_by_lead_time_bucket_main",
    title="Dem item MAIN theo status terminal, theo lead-time bucket cua ITEM (checkin_date - ngay VN crawl)",
    grain="item", scope="MAIN", numerator="COUNT(*) item trong tung status", denominator="COUNT(*) item MAIN trong bucket (n_items)",
    output_schema=("lead_time_bucket", *_ITEM_STATUS_COUNT_COLUMNS),
)
def item_status_counts_by_lead_time_bucket_main(conn, snapshot: db.WarehouseSnapshot) -> "pd.DataFrame":
    df = _item_status_counts(conn, snapshot, group_exprs={"lead_time_bucket": _ITEM_LEAD_TIME_BUCKET})
    return metrics.order_lead_time_bucket_rows(df)


@_register(
    "item_status_counts_by_crawl_date_hotel_main",
    title="Dem item MAIN theo status terminal, theo (ngay crawl VN, hotel) - not_bookable rate theo crawl date va hotel (plan 7.8)",
    grain="item", scope="MAIN", numerator="COUNT(*) item trong tung status",
    denominator="COUNT(*) item MAIN cua (crawl_date, hotel) (n_items)",
    output_schema=("crawl_date", "hotel_id", *_ITEM_STATUS_COUNT_COLUMNS),
)
def item_status_counts_by_crawl_date_hotel_main(conn, snapshot: db.WarehouseSnapshot) -> "pd.DataFrame":
    return _item_status_counts(conn, snapshot, group_exprs={
        "crawl_date": _ITEM_CRAWL_VN_DATE, "hotel_id": "COALESCE(i.hotel_id,'(unattributed)')"})


@_register(
    "run_day_status_counts_raw", title="Dem item RAW theo status terminal, theo (nguon, ngay crawl VN) - dau vao co ngay bat thuong (plan 7.3)",
    grain="item", scope="RAW", numerator="COUNT(*) item trong tung status", denominator="COUNT(*) item RAW trong (source, ngay) (n_items)",
    output_schema=("source_code", "vn_crawl_date", *_ITEM_STATUS_COUNT_COLUMNS),
)
def run_day_status_counts_raw(conn, snapshot: db.WarehouseSnapshot) -> "pd.DataFrame":
    return _item_status_counts(conn, snapshot, scope="RAW", group_exprs={
        "source_code": "m.source_code", "vn_crawl_date": _ITEM_CRAWL_VN_DATE})


@_register(
    "run_day_error_code_counts_raw",
    title="Item RAW status khac success/sold_out theo (nguon, ngay crawl VN, status, last_error_code) - xem error/block cu the (plan 7.3)",
    grain="item", scope="RAW", numerator="COUNT(*) item", denominator="khong co - bang mo ta phan loai loi",
    output_schema=("source_code", "vn_crawl_date", "status", "last_error_code", "n_items"),
)
def run_day_error_code_counts_raw(conn, snapshot: db.WarehouseSnapshot) -> "pd.DataFrame":
    sql = f"""
        SELECT m.source_code, {_ITEM_CRAWL_VN_DATE} vn_crawl_date, i.status, COALESCE(i.last_error_code,'(none)') last_error_code,
               COUNT(*) n_items
        FROM crawl_run_items i
        JOIN etl_item_map m ON m.warehouse_item_id=i.id AND m.import_batch_id=%s AND m.include_eda_raw=TRUE
        JOIN crawl_runs r ON r.id=i.crawl_run_id
        WHERE i.status NOT IN ('success','sold_out')
        GROUP BY 1, 2, 3, 4 ORDER BY 1, 2, 3, 4
    """
    df = db.read_sql(conn, sql, (snapshot.batch_id,))
    df["n_items"] = df["n_items"].astype("int64")
    return df


# ======================================================================== 7.10 full-history reference (GPT review 12 M3, file 11 muc 4)
# GPT review 12 eda file 11 muc 4 (MAJOR con lai truoc full run): 3 metric nay tung tra OBSERVATION-
# grain tho (~1,36 trieu dong tren warehouse full) chi de aggregate theo lead_time_bucket trong pandas
# sau do (`metrics.exact_approved_key_observation_coverage`) - "giam duoc peak so voi ban giu ca 3 ban
# cung luc, nhung KHONG giai quyet scale" (nguyen van GPT). Gio GROUP BY THANG trong SQL bang
# `metrics.lead_time_bucket_sql_case()` (mot nguon su that DUY NHAT cho ranh gioi bucket, sinh CASE tu
# CHINH `LEAD_TIME_BUCKETS` cua Python) - khong bao gio nap qua ~7 dong/lan doc.
@_register(
    "reference_observation_match_main",
    title="Exact approved-key coverage theo lead_time_bucket (scope MAIN) - aggregate THANG trong SQL",
    grain="lead_time_bucket", scope="MAIN",
    numerator="SUM(observation co canonical key = mot reference approved cua dung (hotel_id, checkin_date))",
    denominator="COUNT(*) observation MAIN khong sold-out trong bucket",
    output_schema=("lead_time_bucket", "n_observations", "n_matched"),
)
def reference_observation_match_main(conn, snapshot: db.WarehouseSnapshot) -> "pd.DataFrame":
    bucket_case = metrics.lead_time_bucket_sql_case("po.lead_time")
    sql = f"""
        SELECT {bucket_case} lead_time_bucket, COUNT(*) n_observations, SUM(r.id IS NOT NULL) n_matched
        FROM price_observations po
        JOIN etl_item_map m ON m.warehouse_item_id=po.crawl_run_item_id
         AND m.import_batch_id=%s AND m.include_eda_main=TRUE
        JOIN curated_observation_keys cok ON cok.record_id=po.record_id
        LEFT JOIN hotel_reference_rooms r ON r.hotel_id=po.hotel_id AND r.checkin_date=po.checkin_date
         AND r.status='approved' AND r.room_identity_key=cok.canonical_room_key
         AND r.rate_plan_key=cok.canonical_rate_key
        WHERE po.is_sold_out=0
        GROUP BY 1
    """
    return metrics.order_lead_time_bucket_rows(db.read_sql(conn, sql, (snapshot.batch_id,)))


@_register(
    "reference_observation_match_raw",
    title="Nhu tren nhung scope RAW/toan bo non-sold-out (phu luc doi chieu CLAUDE.md) - aggregate THANG trong SQL",
    grain="lead_time_bucket", scope="RAW",
    numerator="SUM(observation co canonical key = mot reference approved cua dung (hotel_id, checkin_date))",
    denominator="COUNT(*) observation RAW khong sold-out trong bucket (bao gom ca off-plan/duplicate/pre-protocol)",
    output_schema=("lead_time_bucket", "n_observations", "n_matched"),
)
def reference_observation_match_raw(conn, snapshot: db.WarehouseSnapshot) -> "pd.DataFrame":
    # GPT review 12 (eda) M3: RAW la `include_eda_raw=TRUE`, PHAI join etl_item_map that su - khong
    # gia dinh "moi core row = RAW" (batch nay tinh co 100% include_eda_raw=1, batch khac co the khac).
    bucket_case = metrics.lead_time_bucket_sql_case("po.lead_time")
    sql = f"""
        SELECT {bucket_case} lead_time_bucket, COUNT(*) n_observations, SUM(r.id IS NOT NULL) n_matched
        FROM price_observations po
        JOIN etl_item_map m ON m.warehouse_item_id=po.crawl_run_item_id
         AND m.import_batch_id=%s AND m.include_eda_raw=TRUE
        JOIN curated_observation_keys cok ON cok.record_id=po.record_id
        LEFT JOIN hotel_reference_rooms r ON r.hotel_id=po.hotel_id AND r.checkin_date=po.checkin_date
         AND r.status='approved' AND r.room_identity_key=cok.canonical_room_key
         AND r.rate_plan_key=cok.canonical_rate_key
        WHERE po.is_sold_out=0
        GROUP BY 1
    """
    return metrics.order_lead_time_bucket_rows(db.read_sql(conn, sql, (snapshot.batch_id,)))


def _series_exists_coverage_sql(bucket_case: str) -> str:
    """SQL dung chung cho ca 2 bien the bucket (chuan + legacy) cua series-exists coverage - chi khac
    bieu thuc bucket, JOIN giu nguyen 1 cho de tranh 2 ban SQL trong."""
    return f"""
        SELECT {bucket_case} lead_time_bucket, COUNT(*) n_observations,
               SUM(r.id IS NOT NULL) n_series_has_reference
        FROM price_observations po
        JOIN etl_item_map m ON m.warehouse_item_id=po.crawl_run_item_id
         AND m.import_batch_id=%s AND m.include_eda_raw=TRUE
        LEFT JOIN hotel_reference_rooms r ON r.hotel_id=po.hotel_id AND r.checkin_date=po.checkin_date
         AND r.status='approved'
        WHERE po.is_sold_out=0
        GROUP BY 1
    """


@_register(
    "reference_series_exists_observation_coverage_raw",
    title="Observation thuoc mot SERIES co reference approved (KHONG doi hoi dung key) - metric turnover "
          "phu tro, DA CHOT GPT file 09 muc 2 - KHAC voi exact-key o tren, xem series_has_approved_reference. "
          "Aggregate THANG trong SQL (GPT file 11 muc 4).",
    grain="lead_time_bucket", scope="RAW",
    numerator="SUM(observation ma (hotel_id, checkin_date) CO mot hang hotel_reference_rooms status=approved, "
              "khong doi hoi room_identity_key/rate_plan_key cua chinh no trung reference)",
    denominator="COUNT(*) observation khong sold-out trong bucket (khong loc scope)",
    output_schema=("lead_time_bucket", "n_observations", "n_series_has_reference"),
)
def reference_series_exists_observation_coverage_raw(conn, snapshot: db.WarehouseSnapshot) -> "pd.DataFrame":
    """XEM eda docstring o dau file va discuss/eda-curated-implementation file 06/09/11: query nay
    khong nam trong dinh nghia M3 cua GPT (file 03) nhung duoc them de doi chieu minh bach voi bang
    lich su CLAUDE.md muc 7.2 (30,4/27,1/18,3/11,2/7,6/7,1) - vi khi tu tay verify, join theo DUNG
    canonical key (`reference_observation_match_raw`) KHONG tai hien duoc bang do (ra ~3,5% thay vi
    ~30%). Nguyen nhan: trung binh 1 series co reference co toi 13,75 cap (room,rate) canonical PHAN
    BIET (max 157) - approved chi la DUNG 1 trong so do, nen "exact key" << "series co reference".

    Cot `series_has_approved_reference` (GPT file 09 muc 2: KHONG dung ten `matches_approved_key` cho
    metric long nay). Dung `metrics.series_with_approved_reference_coverage()`, KHONG dung
    `metrics.exact_approved_key_observation_coverage()` cho DataFrame nay. Bucket CHUAN (7 muc, "0" va
    "1-3" tach rieng) - xem `reference_series_exists_observation_coverage_raw_legacy_bucket` cho ban
    bucket LEGACY (0-3 gop) doi chieu dung dinh dang bang lich su CLAUDE.md.
    """
    bucket_case = metrics.lead_time_bucket_sql_case("po.lead_time")
    return metrics.order_lead_time_bucket_rows(db.read_sql(conn, _series_exists_coverage_sql(bucket_case), (snapshot.batch_id,)))


@_register(
    "reference_series_exists_observation_coverage_raw_legacy_bucket",
    title="Nhu reference_series_exists_observation_coverage_raw nhung bucket LEGACY (0-3 gop) - doi "
          "chieu TRUC TIEP dinh dang bang lich su CLAUDE.md muc 7.2 (GPT review 12 file 11 muc 6.1)",
    grain="legacy_lead_time_bucket", scope="RAW",
    numerator="SUM(observation ma (hotel_id, checkin_date) CO mot hang hotel_reference_rooms status=approved)",
    denominator="COUNT(*) observation khong sold-out trong bucket (khong loc scope)",
    output_schema=("lead_time_bucket", "n_observations", "n_series_has_reference"),
)
def reference_series_exists_observation_coverage_raw_legacy_bucket(conn, snapshot: db.WarehouseSnapshot) -> "pd.DataFrame":
    bucket_case = metrics.legacy_last_minute_bucket_sql_case("po.lead_time")
    return metrics.order_lead_time_bucket_rows(
        db.read_sql(conn, _series_exists_coverage_sql(bucket_case), (snapshot.batch_id,)), order=metrics.LEGACY_LAST_MINUTE_BUCKET_ORDER)


@_register(
    "reference_item_level_availability_main",
    title="Success MAIN item thuoc series co reference approved - co option khop khong, theo lead-time bucket - aggregate SQL",
    grain="item", scope="MAIN",
    numerator="COUNT(item co it nhat 1 observation khop dung key approved) = n_items_matched",
    denominator="success MAIN item thuoc (hotel_id, checkin_date) co reference approved = n_items",
    output_schema=("lead_time_bucket", "n_items", "n_items_matched"),
)
def reference_item_level_availability_main(conn, snapshot: db.WarehouseSnapshot) -> "pd.DataFrame":
    # Truoc day tra 1 dong/item (~150k) ve pandas chi de GROUP BY lead_time_bucket; gio GROUP BY 2 tang trong SQL (tang trong: 1
    # dong/item, tang ngoai: 1 dong/bucket). `MIN(po.lead_time)` on dinh trong 1 item (moi observation cua item cung ngay check-in
    # va cung phien cao => cung lead_time).
    bucket = metrics.lead_time_bucket_sql_case("t.lead_time")
    sql = f"""
        SELECT COALESCE({bucket}, '(invalid)') lead_time_bucket, COUNT(*) n_items, SUM(t.has_matching_option) n_items_matched
        FROM (
          SELECT i.id item_id, MIN(po.lead_time) lead_time, MAX(r.id IS NOT NULL) has_matching_option
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
          GROUP BY i.id
        ) t
        GROUP BY 1 ORDER BY 1
    """
    df = db.read_sql(conn, sql, (snapshot.batch_id,))
    df["n_items"] = df["n_items"].astype("int64")
    df["n_items_matched"] = df["n_items_matched"].fillna(0).astype("int64")
    return metrics.order_lead_time_bucket_rows(df)


@_register(
    "reference_approval_by_city_month", title="Approved/proposed count + rate theo city, check-in month",
    grain="full-history reference series (hotel_id, checkin_date)", scope="REFERENCE EVIDENCE",
    numerator="COUNT(status='approved')", denominator="COUNT(*) series co candidate",
    output_schema=("city", "checkin_month", "approved", "proposed", "n", "approval_rate"),
)
def reference_approval_by_city_month(conn, snapshot: db.WarehouseSnapshot) -> "pd.DataFrame":
    # KHONG dung DATE_FORMAT('%Y-%m'): `db.read_sql(..., params=())` khong %-format nen '%%Y-%%m' se ra HANG SO
    # '%Y-%m' (bug am tham tung lam moi hang cung 1 "thang") - dung CONCAT/LPAD, khong co ky tu '%'.
    sql = """
        SELECT h.city, CONCAT(YEAR(r.checkin_date), '-', LPAD(MONTH(r.checkin_date), 2, '0')) checkin_month,
               SUM(r.status='approved') approved, SUM(r.status='proposed') proposed, COUNT(*) n
        FROM hotel_reference_rooms r LEFT JOIN hotels h ON h.hotel_id=r.hotel_id
        GROUP BY 1, 2 ORDER BY 1, 2
    """
    df = db.read_sql(conn, sql)
    df["approval_rate"] = df["approved"] / df["n"]
    return df


@_register(
    "reference_status_evidence_summary",
    title="Candidate coverage / distinct run+item count cua hotel_reference_rooms theo status (plan 7.10: 'candidate coverage, "
          "distinct run/item count')",
    grain="full-history reference series (hotel_id, checkin_date)", scope="REFERENCE EVIDENCE",
    numerator="COUNT(reference) / SUM(distinct_run_count>=3) / SUM(coverage>=0.8) trong tung status",
    denominator="COUNT(*) reference trong status (n_references)",
    output_schema=("status", "n_references", "n_series", "min_runs", "mean_runs", "max_runs", "min_items", "mean_items",
                   "max_items", "min_coverage", "mean_coverage", "max_coverage", "n_with_ge3_runs", "n_with_coverage_ge_080"),
)
def reference_status_evidence_summary(conn, snapshot: db.WarehouseSnapshot) -> "pd.DataFrame":
    sql = """
        SELECT r.status, COUNT(*) n_references, COUNT(DISTINCT r.hotel_id, r.checkin_date) n_series,
               MIN(r.distinct_run_count) min_runs, AVG(r.distinct_run_count) mean_runs, MAX(r.distinct_run_count) max_runs,
               MIN(r.distinct_item_count) min_items, AVG(r.distinct_item_count) mean_items, MAX(r.distinct_item_count) max_items,
               MIN(r.coverage) min_coverage, AVG(r.coverage) mean_coverage, MAX(r.coverage) max_coverage,
               SUM(r.distinct_run_count >= 3) n_with_ge3_runs, SUM(r.coverage >= 0.8) n_with_coverage_ge_080
        FROM hotel_reference_rooms r GROUP BY r.status ORDER BY r.status
    """
    return db.read_sql(conn, sql)


@_register(
    "reference_uniqueness_per_series",
    title="So reference moi series (hotel_id, checkin_date) theo status approved - kiem tra tinh duy nhat (plan 7.10: 'uniqueness')",
    grain="full-history reference series (hotel_id, checkin_date)", scope="REFERENCE EVIDENCE",
    numerator="COUNT(series) co dung n_approved reference approved", denominator="COUNT(*) series co it nhat 1 reference row",
    output_schema=("n_approved", "n_series"),
)
def reference_uniqueness_per_series(conn, snapshot: db.WarehouseSnapshot) -> "pd.DataFrame":
    sql = """
        SELECT t.n_approved, COUNT(*) n_series
        FROM (SELECT hotel_id, checkin_date, SUM(status='approved') n_approved FROM hotel_reference_rooms GROUP BY 1, 2) t
        GROUP BY t.n_approved ORDER BY t.n_approved
    """
    df = db.read_sql(conn, sql)
    df["n_approved"] = df["n_approved"].astype("int64")
    return df


@_register(
    "reference_candidate_coverage_summary",
    title="Tong hop hotel_room_candidates: so candidate/series, distinct run+item count, item_coverage (plan 7.10 candidate coverage)",
    grain="candidate (hotel_id, checkin_date, room_identity_key, rate_plan_key)", scope="REFERENCE EVIDENCE",
    numerator="SUM(distinct_run_count>=3) / SUM(item_coverage>=0.8)", denominator="COUNT(*) candidate (n_candidates)",
    output_schema=("n_candidates", "n_series_with_candidates", "mean_candidates_per_series", "max_candidates_per_series",
                   "mean_distinct_run_count", "max_distinct_run_count", "mean_distinct_item_count", "mean_item_coverage",
                   "n_candidates_ge3_runs", "n_candidates_coverage_ge_080"),
)
def reference_candidate_coverage_summary(conn, snapshot: db.WarehouseSnapshot) -> "pd.DataFrame":
    sql = """
        SELECT SUM(t.n) n_candidates, COUNT(*) n_series_with_candidates, AVG(t.n) mean_candidates_per_series,
               MAX(t.n) max_candidates_per_series, SUM(t.sum_runs) / SUM(t.n) mean_distinct_run_count,
               MAX(t.max_runs) max_distinct_run_count, SUM(t.sum_items) / SUM(t.n) mean_distinct_item_count,
               SUM(t.sum_cov) / SUM(t.n) mean_item_coverage,
               SUM(t.n_ge3) n_candidates_ge3_runs, SUM(t.n_cov) n_candidates_coverage_ge_080
        FROM (
          SELECT hotel_id, checkin_date, COUNT(*) n, SUM(distinct_run_count) sum_runs, MAX(distinct_run_count) max_runs,
                 SUM(distinct_item_count) sum_items, SUM(item_coverage) sum_cov,
                 SUM(distinct_run_count >= 3) n_ge3, SUM(item_coverage >= 0.8) n_cov
          FROM hotel_room_candidates GROUP BY hotel_id, checkin_date
        ) t
    """
    df = db.read_sql(conn, sql)
    for column in ("n_candidates", "n_series_with_candidates", "max_candidates_per_series", "max_distinct_run_count",
                   "n_candidates_ge3_runs", "n_candidates_coverage_ge_080"):
        df[column] = df[column].fillna(0).astype("int64")
    return df


@_register(
    "series_evidence_runs_distribution",
    title="Series (hotel_id, checkin_date) theo so run completed co item success trong scope REFERENCE EVIDENCE, theo city "
          "(plan 7.12: 'ty le series co it nhat 3 evidence runs')",
    grain="series (hotel_id, checkin_date)", scope="REFERENCE EVIDENCE",
    numerator="COUNT(series) co evidence_runs_bucket = '3+'", denominator="COUNT(*) series co it nhat 1 evidence run trong city",
    output_schema=("city", "evidence_runs_bucket", "n_series"),
)
def series_evidence_runs_distribution(conn, snapshot: db.WarehouseSnapshot) -> "pd.DataFrame":
    # Dinh nghia EVIDENCE = dung dieu kien REFERENCE EVIDENCE cua plan 6.1: run 'completed' + item 'success' + ca 2 include_reference.
    sql = """
        SELECT COALESCE(h.city, '(unknown)') city,
               CASE WHEN e.evidence_runs >= 3 THEN '3+' ELSE CAST(e.evidence_runs AS CHAR) END evidence_runs_bucket,
               COUNT(*) n_series
        FROM (
          SELECT i.hotel_id, i.checkin_date, COUNT(DISTINCT i.crawl_run_id) evidence_runs
          FROM crawl_run_items i
          JOIN etl_item_map m ON m.warehouse_item_id=i.id AND m.import_batch_id=%s AND m.include_reference=TRUE
          JOIN crawl_runs r ON r.id=i.crawl_run_id AND r.status='completed'
          JOIN etl_run_map rm ON rm.warehouse_run_id=r.id AND rm.import_batch_id=%s AND rm.include_reference=TRUE
          WHERE i.status='success' AND i.hotel_id IS NOT NULL
          GROUP BY i.hotel_id, i.checkin_date
        ) e
        LEFT JOIN hotels h ON h.hotel_id=e.hotel_id
        GROUP BY 1, 2 ORDER BY 1, 2
    """
    df = db.read_sql(conn, sql, (snapshot.batch_id, snapshot.batch_id))
    df["n_series"] = df["n_series"].astype("int64")
    return df


@_register(
    "history_length_by_hotel_checkin_main",
    title="Do dai lich su theo (hotel_id, checkin_date): so ngay crawl VN co item success MAIN, theo bucket va city (plan 7.12)",
    grain="series (hotel_id, checkin_date)", scope="MAIN",
    numerator="COUNT(series) trong bucket so ngay co snapshot", denominator="COUNT(*) series (hotel_id, checkin_date) co it nhat 1 item success MAIN",
    output_schema=("city", "history_days_bucket", "n_series", "sum_span_days"),
)
def history_length_by_hotel_checkin_main(conn, snapshot: db.WarehouseSnapshot) -> "pd.DataFrame":
    # `history_days` = so ngay crawl VN khac nhau ma series co item success (snapshot co gia); `span_days` = ngay dau -> ngay cuoi + 1.
    sql = f"""
        SELECT COALESCE(h.city, '(unknown)') city,
               CASE WHEN s.history_days = 1 THEN '1' WHEN s.history_days = 2 THEN '2' WHEN s.history_days BETWEEN 3 AND 6 THEN '3-6'
                    WHEN s.history_days BETWEEN 7 AND 13 THEN '7-13' WHEN s.history_days BETWEEN 14 AND 29 THEN '14-29'
                    ELSE '30+' END history_days_bucket,
               COUNT(*) n_series, SUM(s.span_days) sum_span_days
        FROM (
          SELECT i.hotel_id, i.checkin_date, COUNT(DISTINCT {_ITEM_CRAWL_VN_DATE}) history_days,
                 DATEDIFF(MAX({_ITEM_CRAWL_VN_DATE}), MIN({_ITEM_CRAWL_VN_DATE})) + 1 span_days
          FROM crawl_run_items i
          JOIN etl_item_map m ON m.warehouse_item_id=i.id AND m.import_batch_id=%s AND m.include_eda_main=TRUE
          JOIN crawl_runs r ON r.id=i.crawl_run_id
          WHERE i.status='success' AND i.hotel_id IS NOT NULL
          GROUP BY i.hotel_id, i.checkin_date
        ) s
        LEFT JOIN hotels h ON h.hotel_id=s.hotel_id
        GROUP BY 1, 2 ORDER BY 1, 2
    """
    df = db.read_sql(conn, sql, (snapshot.batch_id,))
    df["n_series"] = df["n_series"].astype("int64")
    df["sum_span_days"] = df["sum_span_days"].fillna(0).astype("int64")
    return df


# ======================================================================== 7.6/7.7 gia + calendar: aggregate THANG trong SQL (bounded-memory)
# GPT review 12 eda file 11 muc 4 (MAJOR): "khong keo full observation frame chi de GROUP BY; observation-level
# chi duoc giu duoi dang sample audit co gioi han". Truoc day `price_observations_main/raw` tra ~1,27/1,36 trieu
# dong ve Python de tinh phan phoi/plot; gio MOI bang publish ve 7.6/7.7 la ket qua aggregate SQL (toi da vai
# tram nghin dong o bang per-series/per-hotel-day, khong bao gio observation-grain) - xem `sql_builders.py`.
OUTLIER_MAD_MULTIPLIER = 5.0
OUTLIER_MIN_HOTEL_OBSERVATIONS = 5
OUTLIER_SAMPLE_LIMIT = 200
_SCOPE_FLAG = {"MAIN": "include_eda_main", "RAW": "include_eda_raw"}
_PRICE_VALID = "po.is_sold_out=0 AND po.price_per_night IS NOT NULL AND po.price_per_night > 0"
_VN_OBS_DATE = "DATE(CONVERT_TZ(po.observed_at,'+00:00','+07:00'))"
_PRICE_STATS = ("n_obs", "min_price", "max_price", "mean_price", "p1", "p5", "p50", "p75", "p95", "p99")


def _price_from_where(scope: str, *, extra_join: str = "") -> str:
    """FROM/JOIN/WHERE chung cho moi aggregate gia. 1 placeholder `%s` (batch_id) dat TRUOC `extra_join`."""
    return (
        "FROM price_observations po "
        f"JOIN etl_item_map m ON m.warehouse_item_id=po.crawl_run_item_id AND m.import_batch_id=%s AND m.{_SCOPE_FLAG[scope]}=TRUE "
        f"LEFT JOIN hotels h ON h.hotel_id=po.hotel_id {extra_join} "
        f"WHERE {_PRICE_VALID}"
    )


def _grouped_price_stats(conn, snapshot: db.WarehouseSnapshot, *, scope: str, group_exprs=None, quantiles=None,
                         extra_aggregates=None, min_group_size: int = 1, extra_join: str = "",
                         extra_params: tuple = ()) -> "pd.DataFrame":
    sql = sqlb.grouped_quantile_sql(
        value_expr="po.price_per_night", from_where=_price_from_where(scope, extra_join=extra_join),
        group_exprs=group_exprs, quantiles=quantiles or sqlb.STANDARD_QUANTILES,
        extra_aggregates=extra_aggregates, min_group_size=min_group_size,
    )
    return db.read_sql(conn, sql, (snapshot.batch_id, *extra_params))


@_register(
    "price_distribution_overall_main",
    title="Phan phoi gia toan bo (quantile noi suy tuyen tinh = pandas), scope MAIN - aggregate SQL",
    grain="observation", scope="MAIN", numerator="n/a - bang mo ta",
    denominator="COUNT(*) observation co gia hop le, khong sold-out", output_schema=_PRICE_STATS,
)
def price_distribution_overall_main(conn, snapshot: db.WarehouseSnapshot) -> "pd.DataFrame":
    return _grouped_price_stats(conn, snapshot, scope="MAIN")


@_register(
    "price_distribution_overall_raw",
    title="Nhu price_distribution_overall_main nhung scope RAW (plan 7.7: tach it nhat RAW va MAIN)",
    grain="observation", scope="RAW", numerator="n/a - bang mo ta",
    denominator="COUNT(*) observation co gia hop le, khong sold-out", output_schema=_PRICE_STATS,
)
def price_distribution_overall_raw(conn, snapshot: db.WarehouseSnapshot) -> "pd.DataFrame":
    return _grouped_price_stats(conn, snapshot, scope="RAW")


@_register(
    "price_distribution_by_city_main", title="Phan phoi gia theo thanh pho, scope MAIN - aggregate SQL",
    grain="observation", scope="MAIN", numerator="n/a - bang mo ta", denominator="COUNT(*) observation trong city",
    output_schema=("city", *_PRICE_STATS),
)
def price_distribution_by_city_main(conn, snapshot: db.WarehouseSnapshot) -> "pd.DataFrame":
    return _grouped_price_stats(conn, snapshot, scope="MAIN", group_exprs={"city": "COALESCE(h.city,'(unknown)')"})


@_register(
    "price_distribution_by_lead_time_bucket_main", title="Phan phoi gia theo lead-time bucket, scope MAIN - aggregate SQL",
    grain="observation", scope="MAIN", numerator="n/a - bang mo ta", denominator="COUNT(*) observation trong bucket",
    output_schema=("lead_time_bucket", *_PRICE_STATS),
)
def price_distribution_by_lead_time_bucket_main(conn, snapshot: db.WarehouseSnapshot) -> "pd.DataFrame":
    return _grouped_price_stats(
        conn, snapshot, scope="MAIN", group_exprs={"lead_time_bucket": metrics.lead_time_bucket_sql_case("po.lead_time")})


@_register(
    "price_distribution_by_weekday_main",
    title="Phan phoi gia theo thu trong tuan cua check-in (is_weekend_fri_sat = thu Sau/Bay, CLAUDE.md muc 5.1), MAIN",
    grain="observation", scope="MAIN", numerator="n/a - bang mo ta", denominator="COUNT(*) observation trong thu",
    output_schema=("weekday_number", "weekday", "is_weekend_fri_sat", *_PRICE_STATS),
)
def price_distribution_by_weekday_main(conn, snapshot: db.WarehouseSnapshot) -> "pd.DataFrame":
    return _grouped_price_stats(conn, snapshot, scope="MAIN", group_exprs={
        "weekday_number": "WEEKDAY(po.checkin_date)", "weekday": "DAYNAME(po.checkin_date)",
        "is_weekend_fri_sat": "(DAYOFWEEK(po.checkin_date) IN (6,7))",
    })


@_register(
    "price_distribution_by_calendar_flags_main",
    title="Phan phoi gia theo co calendar cua ngay check-in (public_holiday/tet/festival/major_event), MAIN - JOIN "
          "calendar inline (khong bang tam, ket noi READ ONLY)",
    grain="observation", scope="MAIN", numerator="n/a - bang mo ta", denominator="COUNT(*) observation trong nhom co",
    output_schema=("is_public_holiday", "is_tet", "is_festival_period", "is_major_event", *_PRICE_STATS),
)
def price_distribution_by_calendar_flags_main(conn, snapshot: db.WarehouseSnapshot, *, calendar_rows) -> "pd.DataFrame":
    fragment, params = sqlb.inline_calendar_derived_table(calendar_rows)
    # Ep collation cua warehouse (utf8mb4_unicode_ci) len cot city cua derived table: literal/CAST cua ket noi mac
    # dinh la utf8mb4_0900_ai_ci -> "Illegal mix of collations" khi so sanh voi hotels.city.
    join = f"LEFT JOIN {fragment} ON cal.checkin_date=po.checkin_date AND cal.city COLLATE utf8mb4_unicode_ci = h.city"
    return _grouped_price_stats(
        conn, snapshot, scope="MAIN", extra_join=join, extra_params=tuple(params),
        group_exprs={c: f"COALESCE(cal.{c},0)" for c in ("is_public_holiday", "is_tet", "is_festival_period", "is_major_event")},
    )


@_register(
    "price_box_stats_by_city_main", title="Thong ke box (P5/P25/P50/P75/P95) theo thanh pho cho box plot, MAIN - aggregate SQL",
    grain="observation", scope="MAIN", numerator="n/a - bang mo ta", denominator="COUNT(*) observation trong city",
    output_schema=("city", "n_obs", "min_price", "max_price", "mean_price", "p5", "p25", "p50", "p75", "p95"),
)
def price_box_stats_by_city_main(conn, snapshot: db.WarehouseSnapshot) -> "pd.DataFrame":
    return _grouped_price_stats(
        conn, snapshot, scope="MAIN", group_exprs={"city": "COALESCE(h.city,'(unknown)')"}, quantiles=sqlb.BOX_QUANTILES)


@_register(
    "price_hotel_dispersion_main",
    title=f"Do phan tan gia theo hotel co >= {OUTLIER_MIN_HOTEL_OBSERVATIONS} observation (plan 7.7), MAIN - aggregate SQL",
    grain="hotel", scope="MAIN", numerator="n/a - bang mo ta",
    denominator=f"hotel co >= {OUTLIER_MIN_HOTEL_OBSERVATIONS} observation gia hop le",
    output_schema=("hotel_id", "n_obs", "min_price", "max_price", "mean_price", "p50", "std_price", "coefficient_of_variation"),
)
def price_hotel_dispersion_main(conn, snapshot: db.WarehouseSnapshot) -> "pd.DataFrame":
    df = _grouped_price_stats(
        conn, snapshot, scope="MAIN", group_exprs={"hotel_id": "po.hotel_id"}, quantiles={"p50": "0.50"},
        extra_aggregates={"std_price": "STDDEV_SAMP(g.v)"}, min_group_size=OUTLIER_MIN_HOTEL_OBSERVATIONS)
    df["coefficient_of_variation"] = df["std_price"] / df["mean_price"]
    return df


@_register(
    "price_sensitivity_by_series_main",
    title="Gia tong hop (min + median) o grain (hotel_id, checkin_date, vn_observation_date) - plan 7.7 sensitivity, MAIN",
    grain="(hotel_id, checkin_date, vn_observation_date)", scope="MAIN", numerator="n/a - bang mo ta",
    denominator="COUNT(*) option trong nhom", output_schema=("hotel_id", "checkin_date", "vn_observation_date",
                                                              "n_options", "min_price", "median_price"),
)
def price_sensitivity_by_series_main(conn, snapshot: db.WarehouseSnapshot) -> "pd.DataFrame":
    df = _grouped_price_stats(conn, snapshot, scope="MAIN", quantiles={"p50": "0.50"}, group_exprs={
        "hotel_id": "po.hotel_id", "checkin_date": "po.checkin_date", "vn_observation_date": _VN_OBS_DATE})
    return df.rename(columns={"n_obs": "n_options", "p50": "median_price"})[
        ["hotel_id", "checkin_date", "vn_observation_date", "n_options", "min_price", "median_price"]]


@_register(
    "price_histogram_linear_main", title="Histogram gia thang thuong (80 bin deu tu min den max), MAIN - bin count tu SQL",
    grain="observation", scope="MAIN", numerator="COUNT(*) observation trong bin", denominator="COUNT(*) observation co gia",
    output_schema=("bin_index", "bin_lo", "bin_hi", "n_obs"),
)
def price_histogram_linear_main(conn, snapshot: db.WarehouseSnapshot, *, n_bins: int = 80) -> "pd.DataFrame":
    columns = ["bin_index", "bin_lo", "bin_hi", "n_obs"]
    span = db.read_sql(
        conn, f"SELECT MIN(po.price_per_night) mn, MAX(po.price_per_night) mx {_price_from_where('MAIN')}", (snapshot.batch_id,))
    if span.empty or pd.isna(span["mn"].iloc[0]):
        return pd.DataFrame(columns=columns)
    low, high = float(span["mn"].iloc[0]), float(span["mx"].iloc[0])
    width = (high - low) / n_bins or 1.0
    df = db.read_sql(
        conn, "SELECT LEAST(FLOOR((po.price_per_night - %s) / %s), %s) bin_index, COUNT(*) n_obs "
              f"{_price_from_where('MAIN')} GROUP BY 1 ORDER BY 1", (low, width, n_bins - 1, snapshot.batch_id))
    df["bin_index"] = df["bin_index"].astype(int)
    df["bin_lo"] = low + df["bin_index"] * width
    df["bin_hi"] = df["bin_lo"] + width
    return df[columns]


@_register(
    "price_histogram_log10_main", title="Histogram log10(gia) - bin 0.05 log10 (~12%), MAIN - bin count tu SQL",
    grain="observation", scope="MAIN", numerator="COUNT(*) observation trong bin", denominator="COUNT(*) observation co gia",
    output_schema=("bin_index", "log10_lo", "log10_hi", "price_lo", "price_hi", "n_obs"),
)
def price_histogram_log10_main(conn, snapshot: db.WarehouseSnapshot) -> "pd.DataFrame":
    df = db.read_sql(
        conn, f"SELECT FLOOR(LOG10(po.price_per_night) * 20) bin_index, COUNT(*) n_obs {_price_from_where('MAIN')} "
              "GROUP BY 1 ORDER BY 1", (snapshot.batch_id,))
    df["bin_index"] = df["bin_index"].astype(int)
    df["log10_lo"] = df["bin_index"] / 20.0
    df["log10_hi"] = (df["bin_index"] + 1) / 20.0
    df["price_lo"] = 10 ** df["log10_lo"]
    df["price_hi"] = 10 ** df["log10_hi"]
    return df[["bin_index", "log10_lo", "log10_hi", "price_lo", "price_hi", "n_obs"]]


def _robust_outlier_ctes(from_where: str) -> str:
    """CTE tinh median gia va MAD trong TUNG hotel (plan 7.11: 'outlier price theo robust within-hotel rule, chi flag
    khong xoa') - median = quantile noi suy 0.5 (khop pandas), robust_z = |p - median| / (1.4826 * MAD). `base`
    duoc tham chieu nhieu lan nen MySQL materialize 1 lan. Chi co 1 placeholder `%s` (batch_id) trong `from_where`."""
    med = sqlb.quantile_expr("0.50", value="r.p", rank="r.rn", count="r.cnt")
    mad = sqlb.quantile_expr("0.50", value="dr.abs_dev", rank="dr.rn", count="dr.cnt")
    return f"""
    WITH base AS (
      SELECT po.record_id, po.hotel_id, po.checkin_date, {_VN_OBS_DATE} vn_observation_date, po.price_per_night p
      {from_where}
    ),
    ranked AS (
      SELECT b.hotel_id, b.p, ROW_NUMBER() OVER (PARTITION BY b.hotel_id ORDER BY b.p) rn,
             COUNT(*) OVER (PARTITION BY b.hotel_id) cnt
      FROM base b
    ),
    med AS (SELECT r.hotel_id, MAX(r.cnt) n_obs, {med} hotel_median_price FROM ranked r GROUP BY r.hotel_id),
    dev AS (
      SELECT b.record_id, b.hotel_id, b.checkin_date, b.vn_observation_date, b.p, med.n_obs, med.hotel_median_price,
             ABS(b.p - med.hotel_median_price) abs_dev
      FROM base b JOIN med ON med.hotel_id=b.hotel_id
    ),
    dev_ranked AS (
      SELECT d.hotel_id, d.abs_dev, ROW_NUMBER() OVER (PARTITION BY d.hotel_id ORDER BY d.abs_dev) rn,
             COUNT(*) OVER (PARTITION BY d.hotel_id) cnt
      FROM dev d
    ),
    mad AS (SELECT dr.hotel_id, {mad} price_mad FROM dev_ranked dr GROUP BY dr.hotel_id),
    flagged AS (
      SELECT d.*, mad.price_mad, d.abs_dev / NULLIF(mad.price_mad * 1.4826, 0) price_robust_z
      FROM dev d JOIN mad ON mad.hotel_id=d.hotel_id
    )
    """


_OUTLIER_PREDICATE = f"f.n_obs >= {OUTLIER_MIN_HOTEL_OBSERVATIONS} AND f.price_robust_z >= {OUTLIER_MAD_MULTIPLIER}"


@_register(
    "price_outlier_summary_by_hotel_main",
    title=f"So observation gia lech > {OUTLIER_MAD_MULTIPLIER} robust-sigma (MAD) khoi median cua CHINH hotel "
          f"(hotel >= {OUTLIER_MIN_HOTEL_OBSERVATIONS} obs) - chi FLAG, khong xoa; MAIN",
    grain="hotel", scope="MAIN", numerator="SUM(observation co robust_z >= nguong)",
    denominator=f"observation cua hotel co >= {OUTLIER_MIN_HOTEL_OBSERVATIONS} observation gia hop le",
    output_schema=("hotel_id", "city", "n_obs", "hotel_median_price", "price_mad", "n_outliers", "max_robust_z"),
)
def price_outlier_summary_by_hotel_main(conn, snapshot: db.WarehouseSnapshot) -> "pd.DataFrame":
    sql = _robust_outlier_ctes(_price_from_where("MAIN")) + f"""
    SELECT f.hotel_id, COALESCE(h.city,'(unknown)') city, MAX(f.n_obs) n_obs, MAX(f.hotel_median_price) hotel_median_price,
           MAX(f.price_mad) price_mad, SUM({_OUTLIER_PREDICATE}) n_outliers, MAX(f.price_robust_z) max_robust_z
    FROM flagged f LEFT JOIN hotels h ON h.hotel_id=f.hotel_id
    GROUP BY f.hotel_id, COALESCE(h.city,'(unknown)') ORDER BY f.hotel_id
    """
    df = db.read_sql(conn, sql, (snapshot.batch_id,))
    df["n_outliers"] = df["n_outliers"].fillna(0).astype(int)
    return df


@_register(
    "price_outlier_sample_main", title=f"Sample audit co gioi han ({OUTLIER_SAMPLE_LIMIT}) observation lech nhat theo robust_z, MAIN",
    grain="observation", scope="MAIN", numerator="n/a - sample audit", denominator="n/a",
    output_schema=("record_id", "hotel_id", "checkin_date", "vn_observation_date", "price_per_night",
                   "hotel_median_price", "price_mad", "price_robust_z"),
)
def price_outlier_sample_main(conn, snapshot: db.WarehouseSnapshot) -> "pd.DataFrame":
    sql = _robust_outlier_ctes(_price_from_where("MAIN")) + f"""
    SELECT f.record_id, f.hotel_id, f.checkin_date, f.vn_observation_date, f.p price_per_night,
           f.hotel_median_price, f.price_mad, f.price_robust_z
    FROM flagged f WHERE {_OUTLIER_PREDICATE}
    ORDER BY f.price_robust_z DESC, f.record_id LIMIT {OUTLIER_SAMPLE_LIMIT}
    """
    return db.read_sql(conn, sql, (snapshot.batch_id,))


# ---- 7.6 lead time / calendar coverage (dem observation, aggregate SQL)
_MAIN_PRICE_FROM = _price_from_where("MAIN")


@_register(
    "lead_time_bucket_distribution_main", title="Phan bo observation (MAIN, khong sold-out) theo lead-time bucket",
    grain="observation", scope="MAIN", numerator="COUNT(*) trong bucket", denominator="COUNT(*) observation co gia",
    output_schema=("lead_time_bucket", "n_observations"),
)
def lead_time_bucket_distribution_main(conn, snapshot: db.WarehouseSnapshot) -> "pd.DataFrame":
    bucket = metrics.lead_time_bucket_sql_case("po.lead_time")
    df = db.read_sql(conn, f"SELECT {bucket} lead_time_bucket, COUNT(*) n_observations {_MAIN_PRICE_FROM} GROUP BY 1",
                     (snapshot.batch_id,))
    return (df.set_index("lead_time_bucket").reindex(metrics.LEAD_TIME_BUCKET_ORDER, fill_value=0)
            .rename_axis("lead_time_bucket").reset_index())


@_register(
    "lead_time_bucket_distribution_by_city_source_main", title="Phan bo observation theo (city, source, lead-time bucket), MAIN",
    grain="observation", scope="MAIN", numerator="COUNT(*) trong nhom", denominator="COUNT(*) observation co gia",
    output_schema=("city", "source_code", "lead_time_bucket", "n_observations"),
)
def lead_time_bucket_distribution_by_city_source_main(conn, snapshot: db.WarehouseSnapshot) -> "pd.DataFrame":
    bucket = metrics.lead_time_bucket_sql_case("po.lead_time")
    return db.read_sql(
        conn, f"SELECT COALESCE(h.city,'(unknown)') city, m.source_code source_code, {bucket} lead_time_bucket, "
              f"COUNT(*) n_observations {_MAIN_PRICE_FROM} GROUP BY 1, 2, 3 ORDER BY 1, 2, 3", (snapshot.batch_id,))


@_register(
    "checkin_weekday_distribution_main",
    title="Phan bo observation theo thu trong tuan cua check-in (+ is_weekend_fri_sat = thu Sau/Bay, CLAUDE.md muc 5.1), MAIN",
    grain="observation", scope="MAIN", numerator="COUNT(*) trong thu", denominator="COUNT(*) observation co gia",
    output_schema=("weekday_number", "weekday", "is_weekend_fri_sat", "n_observations"),
)
def checkin_weekday_distribution_main(conn, snapshot: db.WarehouseSnapshot) -> "pd.DataFrame":
    df = db.read_sql(
        conn, "SELECT WEEKDAY(po.checkin_date) weekday_number, DAYNAME(po.checkin_date) weekday, "
              "(DAYOFWEEK(po.checkin_date) IN (6,7)) is_weekend_fri_sat, COUNT(*) n_observations "
              f"{_MAIN_PRICE_FROM} GROUP BY 1, 2, 3 ORDER BY 1", (snapshot.batch_id,))
    df["is_weekend_fri_sat"] = contracts.coerce_boolean_series(df["is_weekend_fri_sat"], nullable=False)
    return df


@_register(
    "checkin_month_distribution_main", title="Phan bo observation theo thang check-in, MAIN",
    grain="observation", scope="MAIN", numerator="COUNT(*) trong thang", denominator="COUNT(*) observation co gia",
    output_schema=("checkin_month", "n_observations"),
)
def checkin_month_distribution_main(conn, snapshot: db.WarehouseSnapshot) -> "pd.DataFrame":
    return db.read_sql(
        conn, "SELECT CONCAT(YEAR(po.checkin_date), '-', LPAD(MONTH(po.checkin_date), 2, '0')) checkin_month, "
              f"COUNT(*) n_observations {_MAIN_PRICE_FROM} GROUP BY 1 ORDER BY 1", (snapshot.batch_id,))


@_register(
    "observation_counts_by_checkin_date_city_main",
    title="So observation theo (checkin_date, city), MAIN - de gan co calendar/dem coverage ma khong keo observation-level",
    grain="(checkin_date, city)", scope="MAIN", numerator="COUNT(*)", denominator="COUNT(*) observation co gia",
    output_schema=("checkin_date", "city", "n_observations"),
)
def observation_counts_by_checkin_date_city_main(conn, snapshot: db.WarehouseSnapshot) -> "pd.DataFrame":
    return db.read_sql(
        conn, f"SELECT po.checkin_date checkin_date, COALESCE(h.city,'(unknown)') city, COUNT(*) n_observations "
              f"{_MAIN_PRICE_FROM} GROUP BY 1, 2 ORDER BY 1, 2", (snapshot.batch_id,))


@_register(
    "observation_date_ranges_main", title="Khoang ngay quan sat (VN) va ngay check-in cua observation co gia, MAIN",
    grain="warehouse (1 dong)", scope="MAIN", numerator="MIN/MAX", denominator="n/a",
    output_schema=("observed_date_min", "observed_date_max", "checkin_date_min", "checkin_date_max"),
)
def observation_date_ranges_main(conn, snapshot: db.WarehouseSnapshot) -> "pd.DataFrame":
    return db.read_sql(
        conn, f"SELECT MIN({_VN_OBS_DATE}) observed_date_min, MAX({_VN_OBS_DATE}) observed_date_max, "
              f"MIN(po.checkin_date) checkin_date_min, MAX(po.checkin_date) checkin_date_max {_MAIN_PRICE_FROM}",
        (snapshot.batch_id,))


@_register(
    "quality_price_total_per_night_inconsistent",
    title="price_total <> price_per_night tren observation 1 dem (CLAUDE.md muc 4.3: price_total LUON = price_per_night)",
    grain="observation", scope="RAW", numerator="COUNT(*) lech", denominator="tat ca observation co gia (khong sold-out)",
    output_schema=("n_violations", "n_total"),
)
def quality_price_total_per_night_inconsistent(conn, snapshot: db.WarehouseSnapshot) -> "pd.DataFrame":
    sql = """
        SELECT SUM(po.price_total IS NOT NULL AND po.price_per_night IS NOT NULL
                   AND po.price_total <> po.price_per_night) n_violations,
               SUM(po.price_total IS NOT NULL OR po.price_per_night IS NOT NULL) n_total
        FROM price_observations po
        JOIN etl_item_map m ON m.warehouse_item_id=po.crawl_run_item_id
         AND m.import_batch_id=%s AND m.include_eda_raw=TRUE
        WHERE po.is_sold_out=0
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


# ======================================================================== 7.5/7.12 canonical-series turnover + readiness (1 lan quet cua so, series-grain SQL)
# Wave A can first/last/median-gap tren TOAN BO population, khong duoc suy tu sample 200. Truy van
# tra 1 dong/series, nhung van chi mang cac scalar da aggregate (khong keo observation payload); mot
# lan sap xep `presence` theo (series, ngay) tinh moi thu bang ham cua so:
# moi thu bang HAM CUA SO tren cung 1 partition:
#   * LAG(d)                                      -> khoang cach lien tiep (max_gap, reappearance);
#   * COUNT(*) OVER (RANGE INTERVAL K DAY FOLLOWING..K DAY FOLLOWING) -> co ngay quan sat dung d+K khong (cap horizon K, K = 1/3/7/14) - thay tu-join.
# `canonical_series_id` = hash(hotel_id, checkin_date, room_key, rate_key) (backend canonicalize.py) nen MOT MINH no dinh danh series -> presence chi can
# (series_id, ngay) - hotel_id/checkin_date chi mang theo de in sample.
# QUAN TRONG (do tren warehouse that): `canonical_series_id` la CHAR(64) collation utf8mb4_unicode_ci - GROUP BY/PARTITION BY tren no so sanh theo COLLATION
# (cham): dedupe presence mat 271s. `UNHEX(...)` -> BINARY(32) so sanh bang memcmp: cung phep dedupe chi 11s (~25x nhanh hon). Hex sha256 la chuoi chu thuong
# nen `LOWER(HEX(sid))` khoi phuc dung gia tri goc.
_HORIZON_COLUMNS = tuple(f"n_pairs_h{k}" for k in metrics.HORIZON_DAYS)
_PRESENCE_CTE = f"""presence AS (
      SELECT UNHEX(cok.canonical_series_id) sid, {_VN_OBS_DATE} d, MIN(po.hotel_id) hotel_id, MIN(po.checkin_date) checkin_date
      FROM price_observations po
      JOIN etl_item_map m ON m.warehouse_item_id=po.crawl_run_item_id AND m.import_batch_id=%s AND m.include_eda_main=TRUE
      JOIN curated_observation_keys cok ON cok.record_id=po.record_id
      WHERE po.is_sold_out=0
      GROUP BY 1, 2
    )"""

TURNOVER_SAMPLE_LIMIT = 200
_FACTS_COLUMNS = (
    "hotel_id", "checkin_date", "canonical_series_id", "n_observed_days", "first_observed",
    "last_observed", "span_days", "max_gap_days", "median_gap_days", "n_reappearance_events",
    *_HORIZON_COLUMNS,
)


def _facts_sql() -> str:
    frames = "\n".join(
        f"         COUNT(*) OVER (ws RANGE BETWEEN INTERVAL {k} DAY FOLLOWING AND INTERVAL {k} DAY FOLLOWING) f{k}," for k in metrics.HORIZON_DAYS)
    stats = ", ".join(f"SUM(w.f{k} > 0) n_pairs_h{k}" for k in metrics.HORIZON_DAYS)
    pair_columns = ", ".join(f"s.n_pairs_h{k}" for k in metrics.HORIZON_DAYS)
    return f"""
    WITH {_PRESENCE_CTE},
    win AS (
      SELECT p.sid, p.d, p.hotel_id, p.checkin_date, DATEDIFF(p.d, LAG(p.d) OVER ws) gap,
{frames}
             1 one
      FROM presence p
      WINDOW ws AS (PARTITION BY p.sid ORDER BY p.d)
    ),
    series_stats AS (
      SELECT w.sid, MIN(w.hotel_id) hotel_id, MIN(w.checkin_date) checkin_date, COUNT(*) n_days, MIN(w.d) first_d, MAX(w.d) last_d,
             COALESCE(MAX(w.gap), 0) max_gap, COALESCE(SUM(w.gap > 1), 0) n_gaps, {stats}
      FROM win w GROUP BY w.sid
    ),
    ranked_gaps AS (
      SELECT w.sid, w.gap,
             ROW_NUMBER() OVER (PARTITION BY w.sid ORDER BY w.gap) gap_rank,
             COUNT(*) OVER (PARTITION BY w.sid) gap_count
      FROM win w WHERE w.gap IS NOT NULL
    ),
    median_gaps AS (
      SELECT g.sid, AVG(g.gap) median_gap
      FROM ranked_gaps g
      WHERE g.gap_rank IN (FLOOR((g.gap_count + 1) / 2), FLOOR((g.gap_count + 2) / 2))
      GROUP BY g.sid
    )
    SELECT s.hotel_id, s.checkin_date, LOWER(HEX(s.sid)) canonical_series_id,
           s.n_days n_observed_days, s.first_d first_observed, s.last_d last_observed,
           DATEDIFF(s.last_d, s.first_d) + 1 span_days, s.max_gap max_gap_days,
           COALESCE(m.median_gap, 0) median_gap_days, s.n_gaps n_reappearance_events,
           {pair_columns}
    FROM series_stats s LEFT JOIN median_gaps m ON m.sid=s.sid
    ORDER BY s.hotel_id, s.checkin_date, s.sid
    """


@_register(
    "canonical_series_facts_main",
    title="Turnover + readiness tai DUNG canonical-series grain: first/last/max/median gap, reappearance va cap ngay K=1/3/7/14",
    grain="canonical series (hotel_id, checkin_date, canonical_series_id)", scope="MAIN",
    numerator="n_pairs_hK = so ngay t sao cho t+K cung duoc quan sat trong series",
    denominator="canonical series MAIN co it nhat 1 observation co gia (moi series dung 1 dong)",
    output_schema=_FACTS_COLUMNS,
)
def canonical_series_facts_main(conn, snapshot: db.WarehouseSnapshot) -> "pd.DataFrame":
    df = db.read_sql(conn, _facts_sql(), (snapshot.batch_id,))
    for column in ("n_observed_days", "span_days", "max_gap_days", "n_reappearance_events", *_HORIZON_COLUMNS):
        df[column] = df[column].fillna(0).astype("int64")
    df["median_gap_days"] = df["median_gap_days"].fillna(0.0).astype(float)
    return df


def turnover_sample(conn, snapshot: db.WarehouseSnapshot, facts: "pd.DataFrame") -> "pd.DataFrame":
    """Deterministic top-gap sample tu bang population; khong query lai observation."""
    return metrics.turnover_sample_rows(facts, limit=TURNOVER_SAMPLE_LIMIT)


QUALITY_SCALAR_IDS: tuple[str, ...] = (
    "quality_price_non_positive", "quality_checkout_not_after_checkin", "quality_success_item_without_observation",
    "quality_canonical_key_anomalies", "quality_city_outside_scope", "quality_unexpected_nulls_by_field_group",
    # GPT review 12 eda file 11 muc 2/5 (plan 7.11): 5 check con thieu.
    "quality_lead_time_mismatch", "quality_duplicate_daily_series", "quality_parent_mismatch",
    "quality_sold_out_sentinel_consistency", "quality_price_total_per_night_inconsistent",
)


def run_all_scalar_metrics(conn, snapshot: db.WarehouseSnapshot) -> dict[str, "pd.DataFrame"]:
    """Tien ich cho notebook/test: chay tat ca metric 1-dong (preflight/quality scalar) trong 1 lan, tra ve
    dict {metric_id: DataFrame}. Khong bao gom metric can group_cols/tham so tu notebook chon."""
    scalar_ids = ("preflight_core_counts", "preflight_non_terminal_runs_items", *QUALITY_SCALAR_IDS)
    return {metric_id: run_metric(metric_id, conn, snapshot) for metric_id in scalar_ids}


# ======================================================================== 7.3 crawl operations / capacity
@_register(
    "run_duration_and_throughput", title="Thoi luong + throughput moi crawl run (VN)",
    grain="run", scope="RAW",
    numerator="n/a - bang mo ta, khong phai ty le", denominator="n/a",
    output_schema=("source_code", "run_id", "vn_crawl_date", "started_at_vn", "finished_at_vn",
                   "duration_minutes", "n_items", "n_observations", "n_checkin_slots", "items_per_hour",
                   "observations_per_hour", "crosses_next_crawl_day"),
)
def run_duration_and_throughput(conn, snapshot: db.WarehouseSnapshot) -> "pd.DataFrame":
    # GPT review 12 eda file 11 muc 5 (plan 7.3): them `n_checkin_slots` (DISTINCT checkin_date trong
    # run - "phan bo duration/throughput theo... so check-in slot"); `finished_at_vn` da du de tinh
    # "gio hoan thanh VN" trong pandas (metrics.finish_hour_distribution), khong can them cot rieng.
    sql = """
        SELECT rm.source_code, r.id run_id,
               DATE(CONVERT_TZ(r.started_at,'+00:00','+07:00')) vn_crawl_date,
               CONVERT_TZ(r.started_at,'+00:00','+07:00') started_at_vn,
               CONVERT_TZ(r.finished_at,'+00:00','+07:00') finished_at_vn,
               TIMESTAMPDIFF(SECOND, r.started_at, r.finished_at) / 60.0 duration_minutes,
               COUNT(DISTINCT i.id) n_items, COUNT(po.record_id) n_observations,
               COUNT(DISTINCT i.checkin_date) n_checkin_slots,
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


@_register(
    "active_hotel_by_crawl_date_source_city",
    title="Nhu active_hotel_by_crawl_date_source nhung them chieu city (plan 7.4: 'theo crawl date, city va source')",
    grain="crawl day x city", scope="MAIN",
    numerator="COUNT(DISTINCT hotel_id) co it nhat 1 owned terminal item trong ngay, trong dung city",
    denominator="khong co - bang mo ta theo thoi gian",
    output_schema=("source_code", "vn_crawl_date", "city", "n_active_hotels"),
)
def active_hotel_by_crawl_date_source_city(conn, snapshot: db.WarehouseSnapshot) -> "pd.DataFrame":
    sql = """
        SELECT m.source_code, DATE(CONVERT_TZ(r.started_at,'+00:00','+07:00')) vn_crawl_date,
               COALESCE(h.city, '(unknown)') city, COUNT(DISTINCT i.hotel_id) n_active_hotels
        FROM crawl_run_items i
        JOIN etl_item_map m ON m.warehouse_item_id=i.id AND m.import_batch_id=%s
         AND m.ownership_status IN ('owner_success','owner_failure')
        JOIN crawl_runs r ON r.id=i.crawl_run_id
        LEFT JOIN hotels h ON h.hotel_id=i.hotel_id
        WHERE i.hotel_id IS NOT NULL
        GROUP BY 1, 2, 3 ORDER BY 1, 2, 3
    """
    # `i.hotel_id IS NOT NULL`: item error chua resolve hotel khong phai "hotel active" (COUNT DISTINCT bo NULL nen se ra dong '(unknown)' voi 0 hotel).
    return db.read_sql(conn, sql, (snapshot.batch_id,))


@_register(
    "checkin_dates_tracked_by_crawl_date_source",
    title="So check-in date DUOC THEO DOI moi ngay crawl, theo nguon (plan 7.4: 'so check-in date duoc theo doi/ngay')",
    grain="crawl day", scope="MAIN",
    numerator="COUNT(DISTINCT checkin_date) trong ngay crawl do", denominator="khong co - bang mo ta",
    output_schema=("source_code", "vn_crawl_date", "n_checkin_dates_tracked"),
)
def checkin_dates_tracked_by_crawl_date_source(conn, snapshot: db.WarehouseSnapshot) -> "pd.DataFrame":
    sql = """
        SELECT m.source_code, DATE(CONVERT_TZ(r.started_at,'+00:00','+07:00')) vn_crawl_date,
               COUNT(DISTINCT i.checkin_date) n_checkin_dates_tracked
        FROM crawl_run_items i
        JOIN etl_item_map m ON m.warehouse_item_id=i.id AND m.import_batch_id=%s
         AND m.ownership_status IN ('owner_success','owner_failure')
        JOIN crawl_runs r ON r.id=i.crawl_run_id
        GROUP BY 1, 2 ORDER BY 1, 2
    """
    return db.read_sql(conn, sql, (snapshot.batch_id,))


@_register(
    "crawl_date_lead_time_bucket_heatmap",
    title="So item theo (crawl_date, lead_time_bucket) - du lieu nguon cho heatmap (plan 7.4: "
          "'heatmap crawl_date x checkin_date hoac crawl_date x lead_time bucket')",
    grain="crawl day x lead_time_bucket", scope="MAIN",
    numerator="COUNT(*) item", denominator="khong co - bang mo ta",
    output_schema=("vn_crawl_date", "lead_time_bucket", "n_items"),
)
def crawl_date_lead_time_bucket_heatmap(conn, snapshot: db.WarehouseSnapshot) -> "pd.DataFrame":
    bucket_case = metrics.lead_time_bucket_sql_case("DATEDIFF(i.checkin_date, DATE(CONVERT_TZ(r.started_at,'+00:00','+07:00')))")
    sql = f"""
        SELECT DATE(CONVERT_TZ(r.started_at,'+00:00','+07:00')) vn_crawl_date,
               {bucket_case} lead_time_bucket, COUNT(*) n_items
        FROM crawl_run_items i
        JOIN etl_item_map m ON m.warehouse_item_id=i.id AND m.import_batch_id=%s AND m.include_eda_main=TRUE
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

    # GPT review 12 eda file 11 muc 2/5: sample cho 5 check quality con lai vua them (plan 7.11/7.8/7.7).
    sql_lead_time_mismatch = """
        SELECT po.record_id FROM price_observations po
        JOIN etl_item_map m ON m.warehouse_item_id=po.crawl_run_item_id
         AND m.import_batch_id=%s AND m.include_eda_raw=TRUE
        WHERE po.lead_time <> DATEDIFF(po.checkin_date, DATE(CONVERT_TZ(po.observed_at,'+00:00','+07:00')))
        ORDER BY po.record_id LIMIT %s
    """
    samples["lead_time_mismatch"] = db.read_sql(
        conn, sql_lead_time_mismatch, (snapshot.batch_id, sample_size))["record_id"].tolist()

    sql_duplicate_series = """
        SELECT po.crawl_run_item_id FROM price_observations po
        JOIN etl_item_map m ON m.warehouse_item_id=po.crawl_run_item_id
         AND m.import_batch_id=%s AND m.include_eda_raw=TRUE
        JOIN curated_observation_keys cok ON cok.record_id=po.record_id
        WHERE po.is_sold_out=0
        GROUP BY po.crawl_run_item_id, cok.canonical_room_key, cok.canonical_rate_key
        HAVING COUNT(*) > 1
        ORDER BY po.crawl_run_item_id LIMIT %s
    """
    samples["duplicate_daily_series"] = db.read_sql(
        conn, sql_duplicate_series, (snapshot.batch_id, sample_size))["crawl_run_item_id"].tolist()

    sql_parent_mismatch = """
        SELECT po.record_id FROM price_observations po
        JOIN crawl_run_items i ON i.id=po.crawl_run_item_id
        JOIN etl_item_map m ON m.warehouse_item_id=po.crawl_run_item_id
         AND m.import_batch_id=%s AND m.include_eda_raw=TRUE
        WHERE NOT (po.hotel_id <=> i.hotel_id) OR NOT (po.checkin_date <=> i.checkin_date)
        ORDER BY po.record_id LIMIT %s
    """
    samples["parent_mismatch"] = db.read_sql(
        conn, sql_parent_mismatch, (snapshot.batch_id, sample_size))["record_id"].tolist()

    sql_sentinel = """
        SELECT i.id FROM crawl_run_items i
        JOIN etl_item_map m ON m.warehouse_item_id=i.id AND m.import_batch_id=%s AND m.include_eda_main=TRUE
        WHERE i.status='sold_out' AND (
          SELECT COUNT(*) FROM price_observations po JOIN curated_observation_keys cok ON cok.record_id=po.record_id
          WHERE po.crawl_run_item_id=i.id AND po.is_sold_out=1 AND cok.canonical_room_key=%s
        ) <> 1
        ORDER BY i.id LIMIT %s
    """
    db._ensure_backend_importable()
    from app.warehouse.canonicalize import EMPTY_ROOM_KEY
    samples["sold_out_sentinel_consistency"] = db.read_sql(
        conn, sql_sentinel, (snapshot.batch_id, EMPTY_ROOM_KEY, sample_size))["id"].tolist()

    sql_total_per_night = """
        SELECT po.record_id FROM price_observations po
        JOIN etl_item_map m ON m.warehouse_item_id=po.crawl_run_item_id
         AND m.import_batch_id=%s AND m.include_eda_raw=TRUE
        WHERE po.is_sold_out=0 AND po.price_total IS NOT NULL AND po.price_per_night IS NOT NULL
          AND po.price_total <> po.price_per_night
        ORDER BY po.record_id LIMIT %s
    """
    samples["price_total_per_night_inconsistent"] = db.read_sql(
        conn, sql_total_per_night, (snapshot.batch_id, sample_size))["record_id"].tolist()

    return samples


def dump_taken_at_vn_date_by_source(conn, snapshot: db.WarehouseSnapshot) -> dict[str, "dt.date"]:
    """Khong phai catalog metric - ngay VN cua `dump_taken_at` moi nguon (tu `etl_import_sources`),
    dung lam `protocol_complete_through_date_by_source` (UPPER BOUND) cho
    `protocol_schedule.expected_schedule` (GPT review 12 eda M1, dat ten ro rang o file 11 muc 6.3 -
    khong con goi la "cutoff" mo ho). `dump_taken_at` la DATETIME (KHONG phai TIMESTAMP) luu dung gia
    tri UTC tu source manifest (`app.warehouse.source_manifest.iso_utc`) - MySQL tra ve literal, khong
    can CONVERT_TZ; chi can cong offset VN co dinh qua `timezone.to_vn_date`."""
    sql = "SELECT source_code, dump_taken_at FROM etl_import_sources WHERE import_batch_id=%s"
    df = db.read_sql(conn, sql, (snapshot.batch_id,))
    return {row.source_code: timezone.to_vn_date(row.dump_taken_at) for row in df.itertuples()}


@_register(
    "item_identity_actual_raw",
    title="Tat ca RAW item voi du field de resolve effective hotel identity (gom non-owner collision)",
    grain="item", scope="RAW",
    numerator="n/a - input cho effective identity", denominator="tat ca item include_eda_raw cua batch",
    output_schema=("item_id", "source_code", "crawl_date", "checkin_date", "hotel_id", "source_hotel_link",
                   "source_link_hash", "item_status", "ownership_status", "item_finished_at"),
)
def item_identity_actual_raw(conn, snapshot: db.WarehouseSnapshot) -> "pd.DataFrame":
    """Identity frame for RAW collision audit.

    This is intentionally separate from `protocol_continuity_actual`: protocol/availability use
    owner rows only, whereas a collision requires the non-owner duplicate by definition. Reusing
    the protocol frame would silently remove every cross-source collision.
    """
    sql = """
        SELECT i.id item_id, m.source_code,
               DATE(CONVERT_TZ(r.started_at,'+00:00','+07:00')) crawl_date,
               i.checkin_date, i.hotel_id,
               IF(i.hotel_id IS NULL, i.source_hotel_link, NULL) source_hotel_link,
               IF(i.hotel_id IS NULL, i.source_link_hash, NULL) source_link_hash,
               i.status item_status, m.ownership_status, i.finished_at item_finished_at
        FROM crawl_run_items i
        JOIN etl_item_map m ON m.warehouse_item_id=i.id
         AND m.import_batch_id=%s AND m.include_eda_raw=TRUE
        JOIN crawl_runs r ON r.id=i.crawl_run_id
    """
    return db.read_sql(conn, sql, (snapshot.batch_id,))


@_register(
    "protocol_continuity_actual",
    title="Item da resolve owner_success/owner_failure + source_hotel_link/hash - dau vao "
          "protocol_schedule.resolve_effective_hotel_id/classify_outcomes",
    grain="item", scope="MAIN",
    numerator="n/a - du lieu tho cho protocol_schedule.classify_outcomes", denominator="n/a",
    output_schema=("item_id", "source_code", "crawl_date", "checkin_date", "hotel_id", "source_hotel_link",
                   "source_link_hash", "item_status", "ownership_status", "item_finished_at", "outcome"),
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
    # BO NHO: URL/hash chi can de resolve + sample cho item hotel_id=NULL (vai tram item) - KHONG keo URL ~150 byte cho ~140k+ item co
    # hotel_id (se tang tuyen tinh theo so ngay crawl). Cot khac (source/ngay/check-in/hotel/outcome) la scalar ngan.
    sql = """
        SELECT i.id item_id, m.source_code, DATE(CONVERT_TZ(r.started_at,'+00:00','+07:00')) crawl_date,
               i.checkin_date, i.hotel_id,
               IF(i.hotel_id IS NULL, i.source_hotel_link, NULL) source_hotel_link,
               IF(i.hotel_id IS NULL, i.source_link_hash, NULL) source_link_hash,
               i.status item_status, m.ownership_status, i.finished_at item_finished_at,
               IF(m.ownership_status='owner_success', 'owner_success', m.exclusion_reason) outcome
        FROM crawl_run_items i
        JOIN etl_item_map m ON m.warehouse_item_id=i.id AND m.import_batch_id=%s
         AND m.ownership_status IN ('owner_success','owner_failure')
        JOIN crawl_runs r ON r.id=i.crawl_run_id
    """
    return db.read_sql(conn, sql, (snapshot.batch_id,))


# ======================================================================== 7.9 missingness theo field group
# GPT review 12 eda file 11 muc 5 (plan 7.9): "Bao missingness: toan bo; theo source; theo scraper/
# selector version; theo crawl date; theo city; theo item status va sold-out." Them nhom field thu 5
# "artifact_source_metadata" (plan 7.9: "artifact/source metadata") tren `crawl_runs` (scraper_version/
# selector_version/git_commit) - truoc day chi co 4 nhom.
_MISSINGNESS_FIELD_GROUPS: dict[str, tuple[str, ...]] = {
    "room_identity": ("room_type_raw", "max_occupancy", "bed_config", "room_area"),
    "rate_plan": ("breakfast_included", "free_cancellation", "cancellation_policy", "price_includes_tax"),
    "price": ("price_per_night", "taxes_fees"),
    "hotel_attributes": ("review_score", "review_count", "address"),
    "artifact_source_metadata": ("scraper_version", "selector_version", "git_commit"),
}
_MISSINGNESS_HOTEL_FIELDS = {"review_score", "review_count", "address"}
_MISSINGNESS_RUN_FIELDS = {"scraper_version", "selector_version", "git_commit"}


def _missingness_wide_sql(group_select: str, *, available_only: bool = True, n_group_columns: int = 1,
                          join_item: bool = False) -> str:
    """SQL dung chung cho MOI bien the missingness (source_code/selector_version/crawl_date/city/item_status) -
    chi khac GROUP BY dimension (GPT review 12 eda file 11: "missingness theo selector version/crawl
    date/city"). Luon JOIN `crawl_runs` (can cho nhom `artifact_source_metadata` VA cho cac bien the
    group-by selector_version/crawl_date) - `crawl_run_id` la NOT NULL tren `price_observations` nen
    INNER JOIN khong lam mat dong nao.

    `available_only=True` (mac dinh): loc `is_sold_out=0` - structural missing (sentinel sold-out khong co room payload) bi loai,
    chi con unexpected missing tren observation available. `available_only=False` (chi dung cho bang theo item status/sold-out,
    plan 7.9): giu ca sentinel de bang `missingness_by_item_status_sold_out` PHAN LOAI structural vs unexpected."""
    fields = [f for group in _MISSINGNESS_FIELD_GROUPS.values() for f in group]
    select_parts = ", ".join(
        f"SUM(h.{f} IS NULL) n_null_{f}" if f in _MISSINGNESS_HOTEL_FIELDS
        else f"SUM(r.{f} IS NULL) n_null_{f}" if f in _MISSINGNESS_RUN_FIELDS
        else f"SUM(po.{f} IS NULL) n_null_{f}"
        for f in fields
    )
    where = "WHERE po.is_sold_out=0" if available_only else ""
    positions = ", ".join(str(n) for n in range(1, n_group_columns + 1))
    item_join = "JOIN crawl_run_items i ON i.id=po.crawl_run_item_id" if join_item else ""
    return f"""
        SELECT {group_select}, COUNT(*) n_total, {select_parts}
        FROM price_observations po
        JOIN etl_item_map m ON m.warehouse_item_id=po.crawl_run_item_id
         AND m.import_batch_id=%s AND m.include_eda_main=TRUE
        JOIN crawl_runs r ON r.id=po.crawl_run_id
        {item_join}
        LEFT JOIN hotels h ON h.hotel_id=po.hotel_id
        {where}
        GROUP BY {positions}
    """


def _missingness_wide_to_long(wide: "pd.DataFrame", *, group_col: "str | list[str]") -> "pd.DataFrame":
    group_cols = [group_col] if isinstance(group_col, str) else list(group_col)
    rows = []
    for _, row in wide.iterrows():
        for group_name, group_fields in _MISSINGNESS_FIELD_GROUPS.items():
            for field in group_fields:
                n_null = int(row[f"n_null_{field}"])
                n_total = int(row["n_total"])
                rows.append({
                    **{c: row[c] for c in group_cols}, "field": field, "field_group": group_name,
                    "n_null": n_null, "n_total": n_total,
                    "null_rate": (n_null / n_total) if n_total else 0.0,
                })
    return pd.DataFrame(rows, columns=[*group_cols, "field", "field_group", "n_null", "n_total", "null_rate"])


@_register(
    "missingness_available_observations", title="Missingness theo field group tren observation KHONG sold-out (MAIN), theo nguon",
    grain="observation", scope="MAIN",
    numerator="COUNT(field IS NULL)", denominator="COUNT(*) observation KHONG sold-out trong nhom",
    output_schema=("source_code", "field", "field_group", "n_null", "n_total", "null_rate"),
)
def missingness_available_observations(conn, snapshot: db.WarehouseSnapshot) -> "pd.DataFrame":
    wide = db.read_sql(conn, _missingness_wide_sql("m.source_code source_code"), (snapshot.batch_id,))
    return _missingness_wide_to_long(wide, group_col="source_code")


@_register(
    "missingness_by_selector_version", title="Missingness theo field group, theo crawl_runs.selector_version",
    grain="observation", scope="MAIN",
    numerator="COUNT(field IS NULL)", denominator="COUNT(*) observation KHONG sold-out trong nhom",
    output_schema=("selector_version", "field", "field_group", "n_null", "n_total", "null_rate"),
)
def missingness_by_selector_version(conn, snapshot: db.WarehouseSnapshot) -> "pd.DataFrame":
    wide = db.read_sql(
        conn, _missingness_wide_sql("COALESCE(r.selector_version, '(unknown)') selector_version"),
        (snapshot.batch_id,))
    return _missingness_wide_to_long(wide, group_col="selector_version")


@_register(
    "missingness_by_crawl_date", title="Missingness theo field group, theo ngay crawl (VN)",
    grain="observation", scope="MAIN",
    numerator="COUNT(field IS NULL)", denominator="COUNT(*) observation KHONG sold-out trong nhom",
    output_schema=("vn_crawl_date", "field", "field_group", "n_null", "n_total", "null_rate"),
)
def missingness_by_crawl_date(conn, snapshot: db.WarehouseSnapshot) -> "pd.DataFrame":
    wide = db.read_sql(
        conn, _missingness_wide_sql("DATE(CONVERT_TZ(r.started_at,'+00:00','+07:00')) vn_crawl_date"),
        (snapshot.batch_id,))
    return _missingness_wide_to_long(wide, group_col="vn_crawl_date")


@_register(
    "missingness_by_city", title="Missingness theo field group, theo thanh pho",
    grain="observation", scope="MAIN",
    numerator="COUNT(field IS NULL)", denominator="COUNT(*) observation KHONG sold-out trong nhom",
    output_schema=("city", "field", "field_group", "n_null", "n_total", "null_rate"),
)
def missingness_by_city(conn, snapshot: db.WarehouseSnapshot) -> "pd.DataFrame":
    wide = db.read_sql(conn, _missingness_wide_sql("COALESCE(h.city, '(unknown)') city"), (snapshot.batch_id,))
    return _missingness_wide_to_long(wide, group_col="city")


# Field group co payload phong/gia: sentinel sold-out KHONG co (structural missing, ky vong). Field group thuoc hotel/run metadata
# van PHAI co ke ca tren sentinel - NULL o do van la unexpected.
_STRUCTURAL_WHEN_SOLD_OUT_GROUPS = frozenset({"room_identity", "rate_plan", "price"})


@_register(
    "missingness_by_item_status_sold_out",
    title="Missingness theo (item status, is_sold_out) - tach structural missing (sentinel sold-out khong co room payload) "
          "khoi unexpected missing tren observation available (plan 7.9)",
    grain="field_value_cell (item_status, is_sold_out, field)", scope="MAIN",
    numerator="COUNT(field IS NULL)", denominator="COUNT(*) observation trong (item_status, is_sold_out)",
    output_schema=("item_status", "is_sold_out", "field", "field_group", "missing_kind", "n_null", "n_total", "null_rate"),
)
def missingness_by_item_status_sold_out(conn, snapshot: db.WarehouseSnapshot) -> "pd.DataFrame":
    wide = db.read_sql(
        conn, _missingness_wide_sql("i.status item_status, po.is_sold_out is_sold_out", available_only=False,
                                    n_group_columns=2, join_item=True),
        (snapshot.batch_id,))
    long = _missingness_wide_to_long(wide, group_col=["item_status", "is_sold_out"])
    long["is_sold_out"] = long["is_sold_out"].astype(int).astype(bool)
    structural = long["is_sold_out"] & long["field_group"].isin(_STRUCTURAL_WHEN_SOLD_OUT_GROUPS)
    long["missing_kind"] = structural.map({True: "structural_expected", False: "unexpected_if_null"})
    return long[["item_status", "is_sold_out", "field", "field_group", "missing_kind", "n_null", "n_total", "null_rate"]]


@_register(
    "artifact_completeness_by_source_crawl_date",
    title="Artifact HTML/screenshot completeness tai ITEM grain, tach requested va structural-not-requested",
    grain="item -> source x vn_crawl_date x save_artifacts", scope="MAIN",
    numerator="n_html_present / n_screenshot_present va missing tuong ung",
    denominator="n_items MAIN trong (source, vn_crawl_date, save_artifacts)",
    output_schema=("source_code", "vn_crawl_date", "save_artifacts", "missing_kind", "n_items",
                   "n_html_present", "n_html_missing", "html_coverage_rate",
                   "n_screenshot_present", "n_screenshot_missing", "screenshot_coverage_rate"),
)
def artifact_completeness_by_source_crawl_date(conn, snapshot: db.WarehouseSnapshot) -> "pd.DataFrame":
    """Artifact path thuoc `crawl_run_items`, vi vay dem o item grain thay vi nhan len theo so room
    option. `save_artifacts=FALSE` la structural-not-requested; chi `TRUE` ma thieu path moi la
    unexpected missing. Path chi la metadata audit, notebook khong mo/ghi artifact goc."""
    sql = """
        SELECT m.source_code, DATE(CONVERT_TZ(r.started_at,'+00:00','+07:00')) vn_crawl_date,
               r.save_artifacts, COUNT(*) n_items,
               SUM(i.artifact_html_path IS NOT NULL) n_html_present,
               SUM(i.screenshot_path IS NOT NULL) n_screenshot_present
        FROM crawl_run_items i
        JOIN etl_item_map m ON m.warehouse_item_id=i.id
         AND m.import_batch_id=%s AND m.include_eda_main=TRUE
        JOIN crawl_runs r ON r.id=i.crawl_run_id
        GROUP BY 1, 2, 3 ORDER BY 1, 2, 3
    """
    df = db.read_sql(conn, sql, (snapshot.batch_id,))
    if df.empty:
        return pd.DataFrame(columns=CATALOG["artifact_completeness_by_source_crawl_date"].output_schema)
    for column in ("n_items", "n_html_present", "n_screenshot_present"):
        df[column] = df[column].fillna(0).astype("int64")
    df["save_artifacts"] = contracts.coerce_boolean_series(df["save_artifacts"], nullable=False)
    df["missing_kind"] = df["save_artifacts"].map({True: "unexpected_if_missing", False: "structural_not_requested"})
    df["n_html_missing"] = df["n_items"] - df["n_html_present"]
    df["n_screenshot_missing"] = df["n_items"] - df["n_screenshot_present"]
    denominator = df["n_items"].replace(0, pd.NA)
    df["html_coverage_rate"] = df["n_html_present"] / denominator
    df["screenshot_coverage_rate"] = df["n_screenshot_present"] / denominator
    return df[list(CATALOG["artifact_completeness_by_source_crawl_date"].output_schema)]


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


# ======================================================================== 7.11 quality findings con lai (GPT review 12 eda file 11 muc 2/5)
@_register(
    "quality_lead_time_mismatch",
    title="lead_time luu san (luc insert) khac lead_time tinh lai tu ngay VN cua observed_at (plan 7.11)",
    grain="observation", scope="RAW",
    numerator="COUNT(*) observation co lead_time luu san != DATEDIFF(checkin_date, ngay VN cua observed_at)",
    denominator="tat ca observation RAW",
    output_schema=("n_mismatch", "n_total"),
)
def quality_lead_time_mismatch(conn, snapshot: db.WarehouseSnapshot) -> "pd.DataFrame":
    sql = """
        SELECT SUM(po.lead_time <> DATEDIFF(po.checkin_date, DATE(CONVERT_TZ(po.observed_at,'+00:00','+07:00')))) n_mismatch,
               COUNT(*) n_total
        FROM price_observations po
        JOIN etl_item_map m ON m.warehouse_item_id=po.crawl_run_item_id
         AND m.import_batch_id=%s AND m.include_eda_raw=TRUE
    """
    return db.read_sql(conn, sql, (snapshot.batch_id,))


@_register(
    "quality_duplicate_daily_series",
    title="Cung 1 item co >1 observation trung canonical (room,rate) key - vi pham ky vong 1 option/canonical key/item (plan 7.11)",
    grain="item x canonical key", scope="RAW",
    numerator="COUNT(*) nhom (item,canonical_room_key,canonical_rate_key) co >1 observation",
    denominator="TOAN BO nhom (item,canonical_room_key,canonical_rate_key) khong sold-out, scope RAW",
    output_schema=("n_duplicate_groups", "n_extra_observations", "n_total_groups"),
)
def quality_duplicate_daily_series(conn, snapshot: db.WarehouseSnapshot) -> "pd.DataFrame":
    sql = """
        SELECT SUM(cnt>1) n_duplicate_groups, SUM(GREATEST(cnt-1,0)) n_extra_observations, COUNT(*) n_total_groups
        FROM (
          SELECT po.crawl_run_item_id, cok.canonical_room_key, cok.canonical_rate_key, COUNT(*) cnt
          FROM price_observations po
          JOIN etl_item_map m ON m.warehouse_item_id=po.crawl_run_item_id
           AND m.import_batch_id=%s AND m.include_eda_raw=TRUE
          JOIN curated_observation_keys cok ON cok.record_id=po.record_id
          WHERE po.is_sold_out=0
          GROUP BY 1, 2, 3
        ) g
    """
    return db.read_sql(conn, sql, (snapshot.batch_id,))


@_register(
    "quality_parent_mismatch",
    title="Observation co hotel_id/checkin_date KHAC voi item cha (crawl_run_items) - plan 7.11 "
          "'observation co hotel/check-in khac item cha'",
    grain="observation", scope="RAW",
    numerator="COUNT(*) observation lech hotel_id/checkin_date so voi item cha",
    denominator="tat ca observation RAW",
    output_schema=("n_mismatch", "n_total"),
)
def quality_parent_mismatch(conn, snapshot: db.WarehouseSnapshot) -> "pd.DataFrame":
    # NULL-safe `<=>` (KHONG dung `<>` thuong) - i.hotel_id ve nguyen tac khong NULL cho item da co
    # observation, nhung phai an toan neu du lieu bat thuong thay vi im lang bo qua do NULL<>x = NULL.
    sql = """
        SELECT SUM(NOT (po.hotel_id <=> i.hotel_id) OR NOT (po.checkin_date <=> i.checkin_date)) n_mismatch,
               COUNT(*) n_total
        FROM price_observations po
        JOIN crawl_run_items i ON i.id=po.crawl_run_item_id
        JOIN etl_item_map m ON m.warehouse_item_id=po.crawl_run_item_id
         AND m.import_batch_id=%s AND m.include_eda_raw=TRUE
    """
    return db.read_sql(conn, sql, (snapshot.batch_id,))


@_register(
    "quality_sold_out_sentinel_consistency",
    title="Item status=sold_out phai co DUNG 1 observation sentinel (is_sold_out=1, canonical room key = EMPTY_ROOM_KEY) - plan 7.8",
    grain="item", scope="MAIN",
    numerator="COUNT(*) item sold_out KHONG co dung 1 sentinel (0 hoac >1, hoac sentinel sai key)",
    denominator="tat ca item status=sold_out, scope MAIN",
    output_schema=("n_violations", "n_total"),
)
def quality_sold_out_sentinel_consistency(conn, snapshot: db.WarehouseSnapshot) -> "pd.DataFrame":
    db._ensure_backend_importable()
    from app.warehouse.canonicalize import EMPTY_ROOM_KEY

    sql = """
        SELECT SUM(sentinel_count<>1) n_violations, COUNT(*) n_total
        FROM (
          SELECT i.id,
            (SELECT COUNT(*) FROM price_observations po
             JOIN curated_observation_keys cok ON cok.record_id=po.record_id
             WHERE po.crawl_run_item_id=i.id AND po.is_sold_out=1 AND cok.canonical_room_key=%s) sentinel_count
          FROM crawl_run_items i
          JOIN etl_item_map m ON m.warehouse_item_id=i.id AND m.import_batch_id=%s AND m.include_eda_main=TRUE
          WHERE i.status='sold_out'
        ) t
    """
    return db.read_sql(conn, sql, (EMPTY_ROOM_KEY, snapshot.batch_id))


# ======================================================================== 7.2/7.11 collision / source divergence (GPT review 12 eda file 11 muc 3)
# Contract CHINH XAC theo file 11 muc 3 ("Contract chinh xac cho collision/source divergence"):
#   - RAW audit thuan tuy - KHONG doi MAIN, KHONG average/dedupe 2 gia tri nguon;
#   - candidate collision = item cua 2 nguon KHAC NHAU cung (vn_crawl_date, hotel_id, checkin_date);
#   - 2 lop bao cao tach biet: item-level (concordance status) va option-level (chi tren pair
#     success-success, join bang canonical room/rate key);
#   - thoi gian (abs(observed_at chenh lech, phut), stratify 0-5/6-15/16-60/>60) la bien audit BAT
#     BUOC - gia khac nhau do thoi diem cao khac nhau KHONG tu dong la loi parser;
#   - moi ty le cong bo DUNG mau so cua chinh no (collision item-pairs / success-success item-pairs /
#     shared canonical option-pairs) - khong tron lan.
@_register(
    "collision_item_pairs",
    title="Candidate collision item-pair: 2 nguon KHAC NHAU cung (vn_crawl_date, hotel_id, checkin_date) - GPT file 11 muc 3.1",
    grain="item pair (source_a, source_b, item_id_a, item_id_b)", scope="RAW",
    numerator="n/a - du lieu tho cho collision_item_status_concordance/collision_option_analysis",
    denominator="n/a",
    output_schema=("source_a", "source_b", "item_id_a", "item_id_b", "vn_crawl_date", "hotel_id",
                   "checkin_date", "status_a", "status_b", "ownership_status_a", "ownership_status_b",
                   "observed_finish_a", "observed_finish_b"),
)
def collision_item_pairs(conn, snapshot: db.WarehouseSnapshot) -> "pd.DataFrame":
    # `a.source_code < b.source_code`: tranh dem 2 lan (A,B) va (B,A) cung 1 cap. Dung item.finished_at
    # (KHONG phai observed_at cua tung observation) lam moc thoi gian dai dien cho ca item - can cho
    # 7.11 muc 3.3 ("thoi gian la bien audit bat buoc"); tung observation co the lech nhau vai giay,
    # finished_at cua item la moc on dinh hon de so sanh 2 nguon.
    #
    # HIEU NANG (da do tren warehouse that, smoke read-only): ban dau tu-join 2 derived table ~167k dong khong index tren
    # (ngay, hotel, check-in) chay >6 phut khong xong. Sua: cua so `MIN/MAX(source_code) OVER (PARTITION BY khoa)` chi giu cac
    # item thuoc khoa co >= 2 nguon (vai nghin dong, mot lan quet), roi moi tu-join tren tap NHO do. Khong loc theo gia/status -
    # cap duoc chon HOAN TOAN theo khoa (ngay crawl VN, hotel_id, check-in), dung contract GPT file 11 muc 3.1. Xac minh tren du lieu
    # that: moi (source, ngay, hotel, check-in) co dung 1 item, nen cap la 1-1, khong can tie-break.
    sql = f"""
        WITH base AS (
          SELECT i.id item_id, m.source_code, m.ownership_status, i.hotel_id, i.checkin_date, i.status, i.finished_at,
                 {_ITEM_CRAWL_VN_DATE} vn_crawl_date
          FROM crawl_run_items i
          JOIN etl_item_map m ON m.warehouse_item_id=i.id AND m.import_batch_id=%s AND m.include_eda_raw=TRUE
          JOIN crawl_runs r ON r.id=i.crawl_run_id
          WHERE i.hotel_id IS NOT NULL
        ),
        multi AS (
          SELECT f.* FROM (
            SELECT b.*, MIN(b.source_code) OVER w min_source, MAX(b.source_code) OVER w max_source
            FROM base b
            WINDOW w AS (PARTITION BY b.vn_crawl_date, b.hotel_id, b.checkin_date)
          ) f WHERE f.min_source <> f.max_source
        )
        SELECT a.source_code source_a, b.source_code source_b, a.item_id item_id_a, b.item_id item_id_b,
               a.vn_crawl_date, a.hotel_id, a.checkin_date, a.status status_a, b.status status_b,
               a.ownership_status ownership_status_a, b.ownership_status ownership_status_b,
               a.finished_at observed_finish_a, b.finished_at observed_finish_b
        FROM multi a JOIN multi b
          ON a.vn_crawl_date=b.vn_crawl_date AND a.hotel_id=b.hotel_id AND a.checkin_date=b.checkin_date
             AND a.source_code < b.source_code
        ORDER BY a.vn_crawl_date, a.hotel_id, a.checkin_date, a.source_code, b.source_code
    """
    return db.read_sql(conn, sql, (snapshot.batch_id,))


COLLISION_OPTION_DETAIL_COLUMNS = [
    "item_id_a", "item_id_b", "source_a", "source_b", "canonical_room_key", "canonical_rate_key",
    "price_per_night_a", "price_per_night_b", "price_abs_diff", "price_relative_diff",
    "observed_at_a", "observed_at_b", "observed_at_diff_minutes",
    "currency_concordant", "breakfast_included_concordant", "free_cancellation_concordant",
    "cancellation_policy_concordant", "price_includes_tax_concordant",
    "taxes_fees_state", "taxes_fees_concordant", "taxes_fees_abs_diff",
]
COLLISION_PAIR_COVERAGE_COLUMNS = [
    "item_id_a", "item_id_b", "source_a", "source_b", "n_options_a", "n_options_b", "n_shared_options",
    "n_union_options", "option_jaccard", "n_ambiguous_shared_keys", "n_duplicate_canonical_keys",
]


def collision_option_analysis(
    conn, snapshot: db.WarehouseSnapshot, success_pairs: "pd.DataFrame",
) -> "tuple[pd.DataFrame, pd.DataFrame]":
    """Khong phai catalog metric (nhan tham so `success_pairs` tu ben goi, khong tu query lai) - cho
    DUNG cac cap item success-success (GPT file 11 muc 3.2: "Option-level VOI pair success-success"),
    fetch price_observations + canonical key CHI cho cac item_id trong tap do (nho, bounded boi so
    collision that su - KHONG phai toan bo warehouse), roi JOIN trong pandas theo
    (canonical_room_key, canonical_rate_key) de tim shared option va tinh price diff/time diff/
    attribute concordance.

    Tra ve `(option_detail, pair_coverage)`:
      - `option_detail`: 1 dong / shared canonical option-pair DUY NHAT 1-1 o ca 2 ben (mau so cua price/attribute concordance);
      - `pair_coverage`: 1 dong / cap success-success - intersection/union cua tap canonical key DISTINCT (GPT file 11 muc 3.2:
        "intersection/union coverage of option keys"); `option_jaccard = n_shared / n_union`;
        `n_duplicate_canonical_keys` = so dong lap key TRONG 1 item (co that trong du lieu); `n_ambiguous_shared_keys` = so key
        chung nhung lap o >= 1 ben, BI LOAI khoi option_detail (khong so sanh gia vi khong biet cap nao voi cap nao).
    Ca hai RONG dung schema neu `success_pairs` rong (khong co collision success-success nao)."""
    columns = COLLISION_OPTION_DETAIL_COLUMNS
    if success_pairs.empty:
        return pd.DataFrame(columns=columns), pd.DataFrame(columns=COLLISION_PAIR_COVERAGE_COLUMNS)

    db._ensure_backend_importable()
    from app.scraper.reference import canonical_text

    coverage_rows: list[dict] = []
    rows: list[dict] = []
    # Xu ly theo CHUNK cap item de bo nho luon bi chan boi COLLISION_CHUNK_PAIRS (khong phu thuoc tong so collision), va tra cuu
    # option cua tung item qua dict (khong quet lai ca bang observation cho moi cap - O(P*N) cu).
    for start in range(0, len(success_pairs), COLLISION_CHUNK_PAIRS):
        chunk = success_pairs.iloc[start:start + COLLISION_CHUNK_PAIRS]
        item_ids = sorted(set(chunk["item_id_a"]) | set(chunk["item_id_b"]))
        placeholders = ",".join(["%s"] * len(item_ids))
        sql = f"""
            SELECT po.crawl_run_item_id item_id, cok.canonical_room_key, cok.canonical_rate_key,
                   po.price_per_night, po.observed_at, po.breakfast_included, po.free_cancellation,
                   po.cancellation_policy, po.price_includes_tax, po.taxes_fees
            FROM price_observations po
            JOIN curated_observation_keys cok ON cok.record_id=po.record_id
            WHERE po.is_sold_out=0 AND po.crawl_run_item_id IN ({placeholders})
        """
        observations = db.read_sql(conn, sql, tuple(item_ids))
        by_item = {item_id: group for item_id, group in observations.groupby("item_id")}
        for pair in chunk.itertuples():
            options_a, options_b = by_item.get(pair.item_id_a), by_item.get(pair.item_id_b)
            if options_a is None or options_b is None:
                continue
            key_cols = ["canonical_room_key", "canonical_rate_key"]
            keys_a = set(map(tuple, options_a[key_cols].itertuples(index=False, name=None)))
            keys_b = set(map(tuple, options_b[key_cols].itertuples(index=False, name=None)))
            n_union = len(keys_a | keys_b)
            # Du lieu THAT co canonical key lap TRONG 1 item (smoke read-only: n_duplicate_canonical_keys max 72/cap) - join thang
            # se NHAN cap (tich Descartes) va thoi phong ty le exact-match. Chi so sanh gia/thuoc tinh tren key DUY NHAT o CA 2 ben
            # (1-1, khong mo ho); key trung o >= 1 ben duoc dem rieng (`n_ambiguous_shared_keys`), khong dua vao mau so option-pair.
            unique_a = options_a.drop_duplicates(subset=key_cols, keep=False)
            unique_b = options_b.drop_duplicates(subset=key_cols, keep=False)
            shared = unique_a.merge(unique_b, on=key_cols, suffixes=("_a", "_b"))
            coverage_rows.append({
                "item_id_a": pair.item_id_a, "item_id_b": pair.item_id_b, "source_a": pair.source_a, "source_b": pair.source_b,
                "n_options_a": len(keys_a), "n_options_b": len(keys_b), "n_shared_options": len(keys_a & keys_b),
                "n_union_options": n_union, "option_jaccard": (len(keys_a & keys_b) / n_union) if n_union else None,
                "n_ambiguous_shared_keys": len(keys_a & keys_b) - len(shared),
                "n_duplicate_canonical_keys": int(options_a.duplicated(subset=key_cols).sum() + options_b.duplicated(subset=key_cols).sum()),
            })
            for opt in shared.itertuples():
                price_a, price_b = float(opt.price_per_night_a), float(opt.price_per_night_b)
                abs_diff = abs(price_a - price_b)
                mean_price = (price_a + price_b) / 2
                time_diff_minutes = abs((opt.observed_at_a - opt.observed_at_b).total_seconds()) / 60.0
                tax_a_null, tax_b_null = pd.isna(opt.taxes_fees_a), pd.isna(opt.taxes_fees_b)
                if tax_a_null and tax_b_null:
                    taxes_state, taxes_concordant, taxes_abs_diff = "both_null", True, None
                elif tax_a_null or tax_b_null:
                    taxes_state, taxes_concordant, taxes_abs_diff = "one_null", False, None
                else:
                    taxes_abs_diff = abs(float(opt.taxes_fees_a) - float(opt.taxes_fees_b))
                    taxes_state, taxes_concordant = "both_present", taxes_abs_diff <= 0.01
                rows.append({
                    "item_id_a": pair.item_id_a, "item_id_b": pair.item_id_b,
                    "source_a": pair.source_a, "source_b": pair.source_b,
                    "canonical_room_key": opt.canonical_room_key, "canonical_rate_key": opt.canonical_rate_key,
                    "price_per_night_a": price_a, "price_per_night_b": price_b,
                    "price_abs_diff": abs_diff,
                    # Tuong doi SYMMETRIC (chia cho TRUNG BINH 2 gia, khong phai chia cho 1 ben) - 2 nguon
                    # la peer, khong ben nao la "ground truth" de lam mau so rieng.
                    "price_relative_diff": (abs_diff / mean_price) if mean_price else None,
                    "observed_at_a": opt.observed_at_a, "observed_at_b": opt.observed_at_b,
                    "observed_at_diff_minutes": time_diff_minutes,
                    # currency KHONG luu tren price_observations (luon VND, ep qua URL - CLAUDE.md muc 4.3)
                    # nen concordant LUON True; giu cot de dung schema voi "concordance cac field co san".
                    "currency_concordant": True,
                    "breakfast_included_concordant": _null_safe_equal(opt.breakfast_included_a, opt.breakfast_included_b),
                    "free_cancellation_concordant": _null_safe_equal(opt.free_cancellation_a, opt.free_cancellation_b),
                    # Shared rate_plan_key da bao gom canonical cancellation policy, nen metric nay
                    # chu yeu la structural audit; van tinh explicit de report khong danh dong no
                    # voi raw-string equality.
                    "cancellation_policy_concordant": (
                        canonical_text(opt.cancellation_policy_a) == canonical_text(opt.cancellation_policy_b)
                    ),
                    "price_includes_tax_concordant": _null_safe_equal(opt.price_includes_tax_a, opt.price_includes_tax_b),
                    "taxes_fees_state": taxes_state,
                    "taxes_fees_concordant": taxes_concordant,
                    "taxes_fees_abs_diff": taxes_abs_diff,
                })
    return (pd.DataFrame(rows, columns=columns),
            pd.DataFrame(coverage_rows, columns=COLLISION_PAIR_COVERAGE_COLUMNS))


COLLISION_CHUNK_PAIRS = 500


def _null_safe_equal(left, right) -> bool:
    """Concordance cua 1 field tren 2 nguon: ca hai NULL (cung 'khong ro') = concordant; 1 NULL 1 co gia tri =
    KHONG concordant; con lai so sanh gia tri. Khong dung `bool(x)` (bool(NaN) = True, NaN == NaN = False)."""
    left_null, right_null = pd.isna(left), pd.isna(right)
    if left_null or right_null:
        return bool(left_null and right_null)
    return bool(left) == bool(right)

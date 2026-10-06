"""Daily sample + label (spec muc 12 "3 tang eligibility", muc 14) -> `ml_samples`.

Bat bien populate (spec muc 12): mot dong CHI duoc insert vao `ml_samples` neu da thoa tang 1+2 - nho do label
chi can kiem "ton tai trong ml_samples". Tang 1 da nam trong `ml_item_reference_matches` (chi item
`training_protocol_eligible`). Tang 2 (o day): match exact/alias va `selected_record_id = record`, `is_sold_out=0`,
`price_per_night>0`, `observed_at >= approved_at`, `checkout=checkin+1`, lead_time TINH LAI tu ngay VN (>=0, khong tin
cot luu), city trong 5 thanh pho, KHONG bi anomaly registry loai (replay as-of cutoff), khong thuoc `exclude_hotels`.

Daily dedup khoa `(assignment, vn_observation_date)`: owner_success truoc -> `observed_at` som hon trong ngay VN ->
(source_code, source_run_id, source_item_id). KHONG dung `record_id` (technical ID), KHONG average gia. Dong thua van
duoc insert voi `is_daily_snapshot_selected=FALSE` + ly do de audit. Label h1/h3/h7/h14 chi noi snapshot da chon voi
snapshot da chon cung assignment o dung `vn_observation_date + k` (spec muc 14): insert TRUOC voi label rong, UPDATE
label SAU (tranh FK tu tham chieu luc insert).
"""
from __future__ import annotations

import json
import re
import time
from typing import Any, Callable

from . import env  # noqa: F401
from .anomaly import replay_registry
from .db import analyze_tables, execute, executemany, fetch_all, scalar, utc_now
from .feature_spec import HORIZONS

CITIES = ("Hồ Chí Minh", "Hà Nội", "Vũng Tàu", "Đà Lạt", "Phú Quốc")
_VN_DATE = "DATE(DATE_ADD(po.observed_at, INTERVAL 7 HOUR))"

_FROM = f"""
FROM ml_item_reference_matches m
JOIN ml_reference_assignments a ON a.id = m.ml_reference_assignment_id AND a.dataset_version = m.dataset_version
JOIN price_observations po ON po.record_id = m.selected_record_id
JOIN hotels h ON h.hotel_id = po.hotel_id
LEFT JOIN tmp_anomaly_excluded ex ON ex.record_id = po.record_id
LEFT JOIN tmp_excluded_hotels eh ON eh.hotel_id = po.hotel_id
WHERE m.dataset_version = %(dv)s AND m.match_status IN ('exact', 'alias')
"""
# Bang tam MySQL chi duoc tham chieu MOT lan trong mot cau lenh ("Can't reopen table") -> LEFT JOIN mot lan,
# dieu kien dung alias `ex`/`eh` (khong dung subquery NOT EXISTS lap lai).

# Dieu kien tang 2, dung CUNG chuoi cho funnel va insert de hai bao cao khong the lech nhau.
_CONDITIONS = (
    ("price_ok", "po.is_sold_out = 0 AND po.price_per_night > 0"),
    ("not_before_approval", "po.observed_at >= a.approved_at"),
    ("checkout_is_checkin_plus_1", "po.checkout_date = DATE_ADD(po.checkin_date, INTERVAL 1 DAY)"),
    ("lead_time_nonneg", f"DATEDIFF(po.checkin_date, {_VN_DATE}) >= 0"),
    ("city_in_scope", "h.city IN (%(c0)s, %(c1)s, %(c2)s, %(c3)s, %(c4)s)"),
    ("not_registry_excluded", "ex.record_id IS NULL"),
    ("hotel_not_overridden", "eh.hotel_id IS NULL"),
)


def _params(dataset_version: str) -> dict[str, Any]:
    return {"dv": dataset_version, **{f"c{i}": city for i, city in enumerate(CITIES)}}


def _funnel(conn, dataset_version: str) -> dict[str, int]:
    """Phieu dem tich luy: moi dong bi loai o dung mot dieu kien dau tien (thu tu `_CONDITIONS`)."""
    selects = ["COUNT(*) AS matched_exact_alias"]
    cumulative: list[str] = []
    for name, condition in _CONDITIONS:
        cumulative.append(f"({condition})")
        selects.append(f"SUM({' AND '.join(cumulative)}) AS after_{name}")
    selects.append(f"SUM(({' AND '.join(c for _, c in _CONDITIONS)}) AND po.lead_time <> DATEDIFF(po.checkin_date, {_VN_DATE})) "
                   f"AS final_lead_time_stored_mismatch")
    cursor = conn.cursor(dictionary=True)
    try:
        cursor.execute(f"SELECT {', '.join(selects)} {_FROM}", _params(dataset_version))
        row = cursor.fetchone()
    finally:
        cursor.close()
    return {key: int(value or 0) for key, value in row.items()}


def _prepare_temp_tables(conn, excluded_record_ids: set[int], excluded_hotels: list[str]) -> None:
    execute(conn, "DROP TEMPORARY TABLE IF EXISTS tmp_anomaly_excluded")
    execute(conn, "DROP TEMPORARY TABLE IF EXISTS tmp_excluded_hotels")
    execute(conn, "CREATE TEMPORARY TABLE tmp_anomaly_excluded (record_id BIGINT PRIMARY KEY)")
    execute(conn, "CREATE TEMPORARY TABLE tmp_excluded_hotels (hotel_id VARCHAR(255) PRIMARY KEY) DEFAULT CHARSET=utf8mb4 "
                  "COLLATE utf8mb4_unicode_ci")
    if excluded_record_ids:
        executemany(conn, "INSERT INTO tmp_anomaly_excluded (record_id) VALUES (%s)", [(rid,) for rid in sorted(excluded_record_ids)])
    if excluded_hotels:
        executemany(conn, "INSERT INTO tmp_excluded_hotels (hotel_id) VALUES (%s)", [(h,) for h in sorted(excluded_hotels)])


def build_samples_labels(conn, *, dataset_version: str, config: dict[str, Any],
                         heartbeat: Callable[[], None] | None = None) -> dict[str, Any]:
    """Step `samples_labels`: replay anomaly -> funnel -> insert -> daily dedup -> label 4 horizon."""
    batch_id = config["import_batch_id"]
    replay = replay_registry(conn, config=config)
    excluded_hotels = list(config["eligibility_overrides"]["exclude_hotels"])
    created_at = utc_now()
    params = _params(dataset_version)
    try:
        detach_and_clear(conn, dataset_version)
        _prepare_temp_tables(conn, replay.excluded_record_ids, excluded_hotels)
        funnel = _funnel(conn, dataset_version)
        where = " AND ".join(condition for _, condition in _CONDITIONS)
        inserted = execute(
            conn,
            f"""INSERT INTO ml_samples (dataset_version, record_id, ml_reference_assignment_id, prediction_time,
                                         vn_observation_date, is_daily_snapshot_selected, daily_snapshot_reason, created_at)
                SELECT %(dv)s, po.record_id, m.ml_reference_assignment_id, po.observed_at, {_VN_DATE}, TRUE, NULL, %(now)s
                {_FROM} AND {where}""",
            {**params, "now": created_at})  # type: ignore[arg-type]
        if heartbeat is not None:
            heartbeat()
        selected = _daily_dedup(conn, dataset_version, batch_id)
        label_started = time.monotonic()
        labels = _build_labels(conn, dataset_version)
        label_seconds = round(time.monotonic() - label_started, 1)
        _write_anomaly_checksums(conn, dataset_version, replay.manifest_checksums())
        duplicate_target = int(scalar(conn,
            "SELECT COUNT(*) FROM (SELECT ml_reference_assignment_id, vn_observation_date FROM ml_samples "
            "WHERE dataset_version=%s AND is_daily_snapshot_selected=TRUE GROUP BY 1,2 HAVING COUNT(*)>1) d", (dataset_version,)) or 0)
        if duplicate_target:
            raise RuntimeError(f"{duplicate_target} (assignment, ngay) co >1 snapshot duoc chon - daily dedup hong, dung build.")
        conn.commit()
        analyze_tables(conn, ("ml_samples",))                   # step sau (split/features/validation) doc bang nay: thong ke phai la cua du lieu that
    except Exception:
        conn.rollback()
        raise
    finally:
        try:
            execute(conn, "DROP TEMPORARY TABLE IF EXISTS tmp_anomaly_excluded")
            execute(conn, "DROP TEMPORARY TABLE IF EXISTS tmp_excluded_hotels")
        except Exception:  # noqa: BLE001
            pass
    return {
        "funnel": funnel, "samples_inserted": inserted, **selected, "labels": labels, "label_build_seconds": label_seconds,
        "anomaly": replay.manifest_checksums(),
    }


def detach_and_clear(conn, dataset_version: str) -> None:
    """Full replacement an toan: detach label roi xoa mau cu cua version (idempotent khi chay lai)."""
    from .cleanup import detach_sample_labels
    detach_sample_labels(conn, dataset_version)
    execute(conn, "DELETE FROM ml_samples WHERE dataset_version=%s", (dataset_version,))


def _daily_dedup(conn, dataset_version: str, batch_id: str) -> dict[str, int]:
    execute(
        conn,
        """UPDATE ml_samples s
           JOIN (
             SELECT s2.id,
                    ROW_NUMBER() OVER (
                      PARTITION BY s2.ml_reference_assignment_id, s2.vn_observation_date
                      ORDER BY (im.ownership_status = 'owner_success') DESC, s2.prediction_time ASC,
                               im.source_code ASC, rm.source_run_id ASC, im.source_item_id ASC) AS rn
             FROM ml_samples s2
             JOIN price_observations po ON po.record_id = s2.record_id
             JOIN etl_item_map im ON im.warehouse_item_id = po.crawl_run_item_id AND im.import_batch_id = %s
             JOIN etl_run_map rm ON rm.warehouse_run_id = po.crawl_run_id AND rm.import_batch_id = %s
             WHERE s2.dataset_version = %s) r ON r.id = s.id
           SET s.is_daily_snapshot_selected = (r.rn = 1),
               s.daily_snapshot_reason = IF(r.rn = 1, NULL, 'superseded_same_vn_day')""",
        (batch_id, batch_id, dataset_version))
    total = int(scalar(conn, "SELECT COUNT(*) FROM ml_samples WHERE dataset_version=%s", (dataset_version,)) or 0)
    selected = int(scalar(conn, "SELECT COUNT(*) FROM ml_samples WHERE dataset_version=%s AND is_daily_snapshot_selected=TRUE",
                          (dataset_version,)) or 0)
    return {"samples_total": total, "samples_selected": selected, "samples_superseded": total - selected}


_TABLE_IDENT = re.compile(r"^[A-Za-z_][A-Za-z0-9_]{0,63}$")


def _build_labels(conn, dataset_version: str, *, table: str = "ml_samples") -> dict[str, int]:
    """Gan nhan h1/h3/h7/h14 qua BANG NGUON NHAN co PK `(assignment, ngay)` thay cho self-join `ml_samples` UPDATE.

    Ly do (rehearsal 06/10, tai hien duoc): step nap ~148 nghin dong va UPDATE trong CUNG transaction nen thong ke index van la cua bang rong; MySQL chon
    ke hoach full-join khong index cho self-join (`NO_INDEX_USED`, `SELECT_FULL_JOIN`, ~22 ty lan doc dong - hon 30 phut cho MOT horizon va khong xong;
    test fixture chi vai chuc dong nen khong thay). Bang nguon nhan voi PK (assignment, ngay) cho phep lookup duy nhat va chay nhanh trong phep tai hien (4,3 giay) va rehearsal that (21 giay cho 4 horizon); ke hoach cuoi van do optimizer chon.
    PK dong thoi la rang buoc toan ven: moi (assignment, ngay) co DUNG MOT snapshot duoc chon (daily dedup), trung => loi (fail-closed).
    `table` chi de test tren bang scratch; mac dinh `ml_samples`.
    """
    if not _TABLE_IDENT.match(table):
        raise ValueError(f"ten bang khong hop le: {table!r}")
    out: dict[str, int] = {}
    execute(conn, "DROP TEMPORARY TABLE IF EXISTS tmp_label_source")
    execute(conn, "CREATE TEMPORARY TABLE tmp_label_source (ml_reference_assignment_id BIGINT NOT NULL, vn_observation_date DATE NOT NULL, "
                  "record_id BIGINT NOT NULL, PRIMARY KEY (ml_reference_assignment_id, vn_observation_date)) ENGINE=InnoDB")
    try:
        execute(conn, f"INSERT INTO tmp_label_source SELECT ml_reference_assignment_id, vn_observation_date, record_id FROM {table} "
                      f"WHERE dataset_version=%s AND is_daily_snapshot_selected=TRUE", (dataset_version,))
        for k in HORIZONS:
            execute(
                conn,
                f"""UPDATE {table} s
                    JOIN tmp_label_source t ON t.ml_reference_assignment_id = s.ml_reference_assignment_id
                     AND t.vn_observation_date = DATE_ADD(s.vn_observation_date, INTERVAL {int(k)} DAY)
                    SET s.has_label_h{int(k)} = TRUE, s.label_source_record_id_h{int(k)} = t.record_id
                    WHERE s.dataset_version = %s AND s.is_daily_snapshot_selected = TRUE""", (dataset_version,))
            out[f"h{k}"] = int(scalar(conn, f"SELECT COUNT(*) FROM {table} WHERE dataset_version=%s AND has_label_h{int(k)}=TRUE",
                                      (dataset_version,)) or 0)
    finally:
        execute(conn, "DROP TEMPORARY TABLE IF EXISTS tmp_label_source")
    return out


def _write_anomaly_checksums(conn, dataset_version: str, payload: dict[str, Any]) -> None:
    execute(conn, "UPDATE dataset_build_manifests SET anomaly_source_member_checksums=%s WHERE dataset_version=%s",
            (json.dumps(payload, ensure_ascii=False, sort_keys=True), dataset_version))

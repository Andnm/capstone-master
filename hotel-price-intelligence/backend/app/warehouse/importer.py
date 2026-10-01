"""Import/remap 4 bang core tu cac staging vao warehouse (muc 3a buoc 11; muc 6, 8; D1, D8, D9).

- ID warehouse gan TUONG MINH, tang dan theo `(source_priority, source_pk)` (D1) - map co san truoc khi
  insert, build lai cung input ra cung ID. Khong ALTER AUTO_INCREMENT (validation chi assert).
- `hotels` gop theo business key (merge_policy); run/item/observation append 1:1.
- `crawl_runs.retry_of_run_id` (self-FK): insert NULL truoc, UPDATE sau khi du map.
- Field reference reset DUNG 1 LAN o day (D8); `is_anomaly` copy nguyen lam audit snapshot (muc 20).
- Fingerprint `etl_observation_map` tinh tren dong NGUON truoc remap, bang chinh
  `observation_fingerprint()` cua tang operational (D9).
- Moi chunk: dong chinh + dong map cung 1 transaction. Chunk loi -> thu tung dong de co lap dong hong
  -> `etl_import_rejections(row_error)`; con cua dong bi reject -> `parent_rejected` (D6).
- JSON di qua nguyen chuoi (driver tra `str`), khong json.loads/dumps lai (07c).
"""
from __future__ import annotations

import datetime as dt
import json
from collections import Counter, defaultdict
from dataclasses import dataclass, field
from decimal import Decimal
from typing import Any, Callable

import mysql.connector

from app.scraper.anomaly_registry_lib import observation_fingerprint

from .cohort_manifest import CohortHistory
from .connection import warehouse_connection
from .errors import CoreImportError
from .merge_policy import HOTEL_COLUMNS, HotelSourceRow, merge_hotel
from .naming import require_identifier
from .ownership_manifest import (
    OwnershipManifest, resolve_item_ownership, resolve_run_ownership, schedule_day_key,
    schedule_manifest_row_key,
)

# Viet Nam khong co DST tu 1975 -> UTC+7 co dinh, khong phu thuoc tzdata tren Windows.
VN_OFFSET = dt.timezone(dt.timedelta(hours=7))
_CHUNK = 5000


def vn_crawl_date(started_at_utc: dt.datetime) -> dt.date:
    """D3: TIMESTAMP doc duoi session +00:00 la UTC naive -> gan UTC TRUOC roi moi doi sang VN."""
    return started_at_utc.replace(tzinfo=dt.timezone.utc).astimezone(VN_OFFSET).date()


def _json_safe(value: Any) -> Any:
    if isinstance(value, Decimal):
        return str(value)
    if isinstance(value, (dt.datetime, dt.date)):
        return value.isoformat()
    if isinstance(value, (bytes, bytearray)):
        return value.hex()
    return value


def raw_row_json(row: dict[str, Any]) -> str:
    """Serializer cho `raw_row_json` - khong bao gio duoc lam chinh duong ghi rejection fail."""
    return json.dumps({key: _json_safe(value) for key, value in row.items()}, ensure_ascii=False, default=str)


@dataclass
class SourceStaging:
    source_code: str
    source_priority: int
    staging: str


@dataclass
class ImportReport:
    hotels: dict[str, int] = field(default_factory=dict)
    per_source: dict[str, dict[str, Any]] = field(default_factory=dict)
    ownership_items: Counter = field(default_factory=Counter)
    ownership_observations: Counter = field(default_factory=Counter)
    rejections: Counter = field(default_factory=Counter)


class Importer:
    def __init__(self, wh_conn, *, batch_id: str, cohort: CohortHistory, ownership: OwnershipManifest,
                 imported_at: dt.datetime, connect: Callable = warehouse_connection) -> None:
        self.wh = wh_conn
        self.batch_id = batch_id
        self.cohort = cohort
        self.ownership = ownership
        self.imported_at = imported_at
        self.connect = connect
        self.report = ImportReport()
        self._next_id = {"crawl_runs": 1, "crawl_run_items": 1, "price_observations": 1}
        self._columns: dict[str, list[str]] = {}

    # ------------------------------------------------------------------ helpers
    def columns(self, table: str) -> list[str]:
        if table not in self._columns:
            cursor = self.wh.cursor()
            cursor.execute(
                "SELECT COLUMN_NAME FROM information_schema.COLUMNS WHERE TABLE_SCHEMA=DATABASE() "
                "AND TABLE_NAME=%s ORDER BY ORDINAL_POSITION", (table,))
            self._columns[table] = [row[0] for row in cursor.fetchall()]
            cursor.close()
        return self._columns[table]

    def _stream(self, staging: str, sql: str):
        with self.connect(staging) as conn:
            cursor = conn.cursor(dictionary=True)
            try:
                cursor.execute(sql)
                while True:
                    rows = cursor.fetchmany(_CHUNK)
                    if not rows:
                        break
                    yield rows
            finally:
                cursor.close()

    def _reject(self, cursor, source: SourceStaging, table: str, pk: Any, scope: str, reason: str,
                parent_pk: Any, row: dict[str, Any] | None) -> None:
        cursor.execute(
            """INSERT INTO etl_import_rejections (import_batch_id, source_code, source_table, source_pk_value,
                 rejection_scope, source_parent_pk_value, rejection_reason, raw_row_json, created_at)
               VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s)""",
            (self.batch_id, source.source_code, table, str(pk), scope,
             None if parent_pk is None else str(parent_pk), reason[:2000],
             raw_row_json(row) if row is not None else None, self.imported_at),
        )
        self.report.rejections[(source.source_code, table, scope)] += 1

    def _insert_isolated(self, cursor, table: str, rows: list[dict[str, Any]]) -> tuple[list[int], list[tuple[int, str]]]:
        """Insert ca chunk; neu loi -> thu tung dong. Tra (chi so thanh cong, [(chi so, loi)])."""
        if not rows:
            return [], []
        cols = self.columns(table)
        sql = f"INSERT INTO `{table}` ({', '.join(f'`{c}`' for c in cols)}) VALUES ({', '.join(['%s'] * len(cols))})"
        values = [tuple(row.get(col) for col in cols) for row in rows]
        try:
            cursor.executemany(sql, values)
            return list(range(len(rows))), []
        except mysql.connector.Error:
            ok, failed = [], []
            for index, value in enumerate(values):
                try:
                    cursor.execute(sql, value)
                    ok.append(index)
                except mysql.connector.Error as exc:  # loi cap statement -> InnoDB chi rollback statement do
                    failed.append((index, f"{getattr(exc, 'errno', '')} {exc}"[:500]))
            return ok, failed

    # ------------------------------------------------------------------ hotels
    def import_hotels(self, sources: list[SourceStaging]) -> None:
        cols = set(self.columns("hotels"))
        if cols != set(HOTEL_COLUMNS):
            raise CoreImportError(f"cot hotels {sorted(cols)} khac tap merge_policy xu ly {sorted(HOTEL_COLUMNS)}.")
        grouped: dict[str, list[HotelSourceRow]] = defaultdict(list)
        source_rows = 0
        for source in sources:
            for rows in self._stream(source.staging, "SELECT * FROM hotels ORDER BY hotel_id"):
                for row in rows:
                    grouped[row["hotel_id"]].append(HotelSourceRow(source.source_code, source.source_priority, row))
                    source_rows += 1
        merged = [merge_hotel(hotel_id, grouped[hotel_id], self.cohort.city_of).row for hotel_id in sorted(grouped)]
        cursor = self.wh.cursor()
        try:
            ok, failed = self._insert_isolated(cursor, "hotels", merged)
            if failed:
                raise CoreImportError(f"{len(failed)} dong hotels da merge khong insert duoc: {failed[:3]}")
            self.wh.commit()
        except Exception:
            self.wh.rollback()
            raise
        finally:
            cursor.close()
        self.report.hotels = {
            "source_hotel_rows": source_rows, "rejected_hotel_source_rows": 0,
            "accepted_hotel_source_rows": source_rows, "warehouse_distinct_hotels": len(merged),
            "duplicate_business_key_rows": source_rows - len(merged),
        }

    # ------------------------------------------------------------------ runs / items / observations
    def import_source(self, source: SourceStaging) -> None:
        stats = self.report.per_source.setdefault(source.source_code, Counter())
        runs = self._import_runs(source, stats)
        items = self._import_items(source, runs, stats)
        self._import_observations(source, runs, items, stats)

    def _import_runs(self, source: SourceStaging, stats: Counter) -> dict[int, dict[str, Any]]:
        run_info: dict[int, dict[str, Any]] = {}
        pending_retry: list[tuple[int, int]] = []
        cursor = self.wh.cursor()
        try:
            for rows in self._stream(source.staging, "SELECT * FROM crawl_runs ORDER BY id"):
                prepared, maps = [], []
                for row in rows:
                    stats["crawl_runs_source"] += 1
                    wid = self._next_id["crawl_runs"]; self._next_id["crawl_runs"] += 1
                    started = row.get("started_at")
                    crawl_date = vn_crawl_date(started) if started is not None else None
                    ownership = (resolve_run_ownership(manifest=self.ownership, source_code=source.source_code,
                                                       crawl_date=crawl_date) if crawl_date else None)
                    info = {"wid": wid, "crawl_date": crawl_date, "ownership": ownership, "status": row["status"]}
                    run_info[row["id"]] = info
                    if row.get("retry_of_run_id") is not None:
                        pending_retry.append((wid, row["retry_of_run_id"]))
                    prepared.append({**row, "id": wid, "retry_of_run_id": None})
                    maps.append((row, info))
                ok, failed = self._insert_isolated(cursor, "crawl_runs", prepared)
                for index, error in failed:
                    row, info = maps[index]
                    info["rejected"] = True
                    self._reject(cursor, source, "crawl_runs", row["id"], "row_error", error, None, row)
                self._write_run_maps(cursor, source, [maps[index] for index in ok])
                stats["crawl_runs_imported"] += len(ok)
                self.wh.commit()
            for wid, source_retry in pending_retry:
                target = run_info.get(source_retry)
                if target and not target.get("rejected"):
                    cursor.execute("UPDATE crawl_runs SET retry_of_run_id=%s WHERE id=%s", (target["wid"], wid))
            self.wh.commit()
        except Exception:
            self.wh.rollback()
            raise
        finally:
            cursor.close()
        return run_info

    def _write_run_maps(self, cursor, source: SourceStaging, entries) -> None:
        values = []
        for row, info in entries:
            own = info["ownership"]
            has_plan = bool(own and own.has_plan)
            flags = own.flags if own else None
            day_key = (schedule_day_key(ownership_manifest_sha256=self.ownership.manifest_sha256,
                                        owner_source=source.source_code, crawl_date=info["crawl_date"],
                                        cohort_manifest_sha256=self.cohort.manifest_sha256) if has_plan else None)
            values.append((
                self.batch_id, source.source_code, row["id"], info["wid"], row["status"], row["created_at"],
                info["crawl_date"] if has_plan else None, day_key,
                bool(flags and flags.include_reference), bool(flags.include_eda_raw) if flags else True,
                bool(flags and flags.include_eda_main), bool(flags and flags.include_training),
                None if has_plan else (own.exclusion_reason if own else "run_started_at_null"), self.imported_at,
            ))
        if values:
            cursor.executemany(
                """INSERT INTO etl_run_map (import_batch_id, source_code, source_run_id, warehouse_run_id,
                     source_status, source_created_at, planned_crawl_date, schedule_day_key, include_reference,
                     include_eda_raw, include_eda_main, include_training, exclusion_reason, imported_at)
                   VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)""", values)

    def _import_items(self, source: SourceStaging, runs: dict[int, dict[str, Any]],
                      stats: Counter) -> dict[int, dict[str, Any]]:
        item_info: dict[int, dict[str, Any]] = {}
        cursor = self.wh.cursor()
        try:
            for rows in self._stream(source.staging, "SELECT * FROM crawl_run_items ORDER BY id"):
                prepared, meta = [], []
                for row in rows:
                    stats["crawl_run_items_source"] += 1
                    run = runs.get(row["crawl_run_id"])
                    if run is None or run.get("rejected"):
                        item_info[row["id"]] = {"rejected": True}
                        self._reject(cursor, source, "crawl_run_items", row["id"], "parent_rejected",
                                     "crawl_run cha bi reject/khong ton tai", row["crawl_run_id"], None)
                        continue
                    wid = self._next_id["crawl_run_items"]; self._next_id["crawl_run_items"] += 1
                    info = self._item_ownership(source, run, row)
                    info["wid"] = wid
                    item_info[row["id"]] = info
                    prepared.append({**row, "id": wid, "crawl_run_id": run["wid"], "reference_match_status": "calibrating"})
                    meta.append((row, info))
                ok, failed = self._insert_isolated(cursor, "crawl_run_items", prepared)
                for index, error in failed:
                    row, info = meta[index]
                    info["rejected"] = True
                    self._reject(cursor, source, "crawl_run_items", row["id"], "row_error", error, None, row)
                self._write_item_maps(cursor, source, [meta[index] for index in ok])
                for index in ok:
                    self.report.ownership_items[(source.source_code, meta[index][1]["status"], meta[index][1]["reason"])] += 1
                stats["crawl_run_items_imported"] += len(ok)
                self.wh.commit()
        except Exception:
            self.wh.rollback()
            raise
        finally:
            cursor.close()
        return item_info

    def _item_ownership(self, source: SourceStaging, run: dict[str, Any], row: dict[str, Any]) -> dict[str, Any]:
        hotel_id = row.get("hotel_id")
        own_run = run["ownership"]
        if own_run is None:
            return {"status": "unassigned", "slot": None, "owner": None, "reason": "run_started_at_null",
                    "flags": (True, False, False, False)}
        # Cohort version CO HIEU LUC tai crawl_date (khong phai workbook hien tai): hotel roi cohort van la
        # thanh vien o moi ngay truoc khi roi (CLAUDE.md muc 2, ca Mac Valley).
        result = resolve_item_ownership(
            manifest=self.ownership, source_code=source.source_code, crawl_date=run["crawl_date"],
            checkin_date=row["checkin_date"], item_status=row["status"], hotel_id=hotel_id,
            hotel_in_cohort=self.cohort.contains_at(hotel_id, run["crawl_date"]) if hotel_id else False,
            run_flags=own_run.flags,
        )
        flags = result.flags
        return {"status": result.ownership_status, "slot": result.schedule_slot, "owner": result.owner_source,
                "reason": result.exclusion_reason, "crawl_date": run["crawl_date"],
                "flags": (flags.include_eda_raw, flags.include_eda_main, flags.include_reference, flags.include_training)}

    def _write_item_maps(self, cursor, source: SourceStaging, entries) -> None:
        values = []
        for row, info in entries:
            row_key = None
            if info["status"] != "unassigned":
                row_key = schedule_manifest_row_key(
                    ownership_manifest_sha256=self.ownership.manifest_sha256, owner_source=info["owner"],
                    crawl_date=info["crawl_date"], schedule_slot=info["slot"], checkin_date=row["checkin_date"],
                    cohort_manifest_sha256=self.cohort.manifest_sha256)
            raw, main, reference, training = info["flags"]
            values.append((self.batch_id, source.source_code, row["id"], info["wid"], info["slot"], row_key,
                           info["status"], reference, raw, main, training, info["reason"], self.imported_at))
        if values:
            cursor.executemany(
                """INSERT INTO etl_item_map (import_batch_id, source_code, source_item_id, warehouse_item_id,
                     schedule_slot, schedule_manifest_row_key, ownership_status, include_reference, include_eda_raw,
                     include_eda_main, include_training, exclusion_reason, imported_at)
                   VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)""", values)

    def _import_observations(self, source: SourceStaging, runs, items, stats: Counter) -> None:
        cursor = self.wh.cursor()
        try:
            for rows in self._stream(source.staging, "SELECT * FROM price_observations ORDER BY record_id"):
                prepared, meta = [], []
                for row in rows:
                    stats["price_observations_source"] += 1
                    item = items.get(row["crawl_run_item_id"])
                    run = runs.get(row["crawl_run_id"])
                    if item is None or item.get("rejected") or run is None or run.get("rejected"):
                        self._reject(cursor, source, "price_observations", row["record_id"], "parent_rejected",
                                     "item/run cha bi reject/khong ton tai", row["crawl_run_item_id"], None)
                        continue
                    fingerprint = observation_fingerprint(row)  # TRUOC remap: payload co ID nguon
                    wid = self._next_id["price_observations"]; self._next_id["price_observations"] += 1
                    prepared.append({**row, "record_id": wid, "crawl_run_id": run["wid"],
                                     "crawl_run_item_id": item["wid"], "is_reference_room": False,
                                     "reference_definition_id": None, "reference_match_status": "calibrating",
                                     "reference_match_score": None})
                    # Luu CHINH dong nguon: dong parent_rejected bi bo qua lam lech chi so giua `rows` va
                    # `prepared`, nen khong duoc lay `rows[index]`.
                    meta.append((row["record_id"], fingerprint, wid, item, row))
                ok, failed = self._insert_isolated(cursor, "price_observations", prepared)
                for index, error in failed:
                    self._reject(cursor, source, "price_observations", meta[index][0], "row_error", error, None,
                                 meta[index][4])
                if ok:
                    cursor.executemany(
                        """INSERT INTO etl_observation_map (import_batch_id, source_code, source_record_id,
                             source_record_sha256, warehouse_record_id, imported_at) VALUES (%s,%s,%s,%s,%s,%s)""",
                        [(self.batch_id, source.source_code, meta[i][0], meta[i][1], meta[i][2], self.imported_at) for i in ok])
                    for index in ok:
                        item = meta[index][3]
                        self.report.ownership_observations[(source.source_code, item["status"], item["reason"])] += 1
                stats["price_observations_imported"] += len(ok)
                self.wh.commit()
        except Exception:
            self.wh.rollback()
            raise
        finally:
            cursor.close()


def require_core_empty(wh_conn) -> None:
    cursor = wh_conn.cursor()
    try:
        for table in ("hotels", "crawl_runs", "crawl_run_items", "price_observations"):
            require_identifier(table, what="table")
            cursor.execute(f"SELECT 1 FROM `{table}` LIMIT 1")
            if cursor.fetchone() is not None:
                raise CoreImportError(f"bang core {table} khong rong - 1 warehouse DB chi chua 1 batch.")
    finally:
        cursor.close()

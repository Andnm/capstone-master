"""Builder warehouse DISPOSABLE theo SPEC khai bao (nhieu nguon, nhieu item/observation) qua CHINH
`app.warehouse.batch.build_warehouse()` that - khong tu hand-insert bang ETL (tranh vi pham invariant ma
khong biet). Dung cho integration test can du lieu co GIA TRI BIET TRUOC (quantile/outlier/series turnover/
collision) - xem `test_sql_aggregates.py`, `test_collision_integration.py`, `test_wave_a_dry_run.py`.

Quy uoc thoi gian (memory project_mysql_timestamp_tz_pitfall): moi datetime la NAIVE UTC. 10:00 UTC = 17:00 VN
cung ngay - an toan de `vn_crawl_date` = ngay UTC (tranh 17:00+ UTC bi doi sang ngay VN ke tiep).
"""
from __future__ import annotations

import contextlib
import datetime as dt
import hashlib
import json
import subprocess
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Iterator

import openpyxl

import db

DEFAULT_ROOM = {
    "room_type_raw": "Deluxe", "max_occupancy": 2, "bed_config": "1 giuong", "room_area": "20 m2",
    "breakfast_included": 1, "free_cancellation": 0, "cancellation_policy": "Khong hoan",
}
VN_OFFSET = dt.timedelta(hours=7)


def room(name: str, **overrides) -> dict:
    """Room spec voi ten rieng (canonical key khac nhau theo `room_type_raw`)."""
    return {**DEFAULT_ROOM, "room_type_raw": name, **overrides}


@dataclass
class FxRun:
    id: int
    started_at: dt.datetime
    finished_at: dt.datetime
    scraper_version: str | None = "2.3.0"
    selector_version: str | None = "sel-1"
    git_commit: str | None = "abc123"


@dataclass
class FxObs:
    record_id: int
    item_id: int
    observed_at: dt.datetime
    price: int | None                     # None => sentinel sold-out (is_sold_out=1, khong co room payload)
    room: dict | None = None              # None + price None => sentinel
    option_index: int = 0


@dataclass
class FxItem:
    id: int
    run_id: int
    hotel_id: str | None                  # None => item loi chua resolve hotel (M2)
    checkin_date: dt.date
    status: str                           # success|sold_out|not_bookable|error|partial
    finished_at: dt.datetime | None = None
    source_hotel_link: str = "https://www.booking.com/hotel/vn/unknown.vi.html"


@dataclass
class FxSource:
    code: str
    priority: int
    runs: list[FxRun]
    items: list[FxItem]
    observations: list[FxObs]
    dump_taken_at: str = "2026-09-30T00:00:00Z"


def _root(sql: str, params: tuple = ()) -> None:
    with db._connect_raw(None) as conn:
        cursor = conn.cursor()
        cursor.execute(sql, params)
        conn.commit()
        cursor.close()


@contextlib.contextmanager
def mutated(fx: dict, statements: list[tuple[str, tuple]], reverts: list[tuple[str, tuple]]) -> Iterator[None]:
    """Chay cac UPDATE tren warehouse fixture (ket noi GHI rieng), yield, ROI LUON hoan tac (finally) - fixture session dung chung nen khong duoc de ban.
    Dung de TIEM vi pham chung minh check chat luong that su phat hien duoc. Doc lai bang ket noi MOI (`db.connect`) - ket noi session dung chung dang o
    snapshot REPEATABLE READ cu nen khong thay UPDATE vua commit."""
    def apply(items: list[tuple[str, tuple]]) -> None:
        with db._connect_raw(fx["warehouse_database"]) as conn:
            cursor = conn.cursor()
            for sql, params in items:
                cursor.execute(sql, params)
            conn.commit()
            cursor.close()

    apply(statements)
    try:
        yield
    finally:
        apply(reverts)


def query_one(fx: dict, sql: str, params: tuple = ()) -> tuple:
    """Doc 1 dong tu warehouse fixture (ket noi MOI) - dung de tim khoa thuc (warehouse id da duoc gan lai, KHONG trung id nguon) cua ban ghi can tiem."""
    with db._connect_raw(fx["warehouse_database"]) as conn:
        cursor = conn.cursor()
        cursor.execute(sql, params)
        row = cursor.fetchone()
        cursor.close()
    assert row is not None, f"khong co dong nao: {sql}"
    return row


@contextlib.contextmanager
def mutate_row(fx: dict, table: str, key_column: str, key_value, changes: dict) -> Iterator[dict]:
    """Doi cot cua DUNG 1 dong roi TU DONG khoi phuc gia tri GOC (doc truoc khi doi) - khong bao gio hard-code gia tri hoan tac (warehouse id/gia tri
    cua fixture duoc gan lai khi build, doan sai se lam BAN fixture session dung chung). `changes`: {cot: gia tri | callable(dong_goc) -> gia tri}."""
    columns = list(changes)
    with db._connect_raw(fx["warehouse_database"]) as conn:
        cursor = conn.cursor(dictionary=True)
        cursor.execute(f"SELECT {', '.join(f'`{c}`' for c in columns)} FROM `{table}` WHERE `{key_column}`=%s", (key_value,))
        original = cursor.fetchone()
        cursor.close()
    assert original is not None, f"khong co dong {table}.{key_column}={key_value!r}"
    new_values = {c: (v(original) if callable(v) else v) for c, v in changes.items()}
    assignments = ", ".join(f"`{c}`=%s" for c in columns)

    def write(values: dict) -> None:
        with db._connect_raw(fx["warehouse_database"]) as conn:
            cursor = conn.cursor()
            cursor.execute(f"UPDATE `{table}` SET {assignments} WHERE `{key_column}`=%s", (*[values[c] for c in columns], key_value))
            conn.commit()
            cursor.close()

    write(new_values)
    try:
        yield original
    finally:
        write(original)


def _populate_source_db(source_db: str, source: FxSource, hotels: dict[str, str]) -> None:
    from app.scraper.reference import rate_plan_key, room_identity_key
    from app.warehouse.bootstrap import apply_setup_sql, create_warehouse_database
    from app.warehouse.connection import warehouse_connection

    create_warehouse_database(source_db)
    apply_setup_sql(source_db)
    created_at = dt.datetime(2026, 9, 1)
    with warehouse_connection(source_db) as conn:
        cursor = conn.cursor()
        for hotel_id in hotels:
            cursor.execute(
                "INSERT INTO hotels (hotel_id,name,hotel_link,city,attributes_updated_at,created_at) "
                "VALUES (%s,%s,%s,'Sai',%s,%s)",
                (hotel_id, hotel_id.upper(), f"https://x/{hotel_id}", created_at, created_at),
            )
        for run in source.runs:
            cursor.execute(
                "INSERT INTO crawl_runs (id,status,started_at,finished_at,scraper_version,selector_version,git_commit) "
                "VALUES (%s,'completed',%s,%s,%s,%s,%s)",
                (run.id, run.started_at, run.finished_at, run.scraper_version, run.selector_version, run.git_commit),
            )
        for item in source.items:
            link_hash = hashlib.sha256(f"{source.code}:{item.id}".encode()).hexdigest()
            cursor.execute(
                "INSERT INTO crawl_run_items (id,crawl_run_id,source_hotel_link,source_link_hash,hotel_link,"
                "hotel_id,checkin_date,checkout_date,status,finished_at) VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)",
                (item.id, item.run_id, item.source_hotel_link, link_hash, item.source_hotel_link, item.hotel_id,
                 item.checkin_date, item.checkin_date + dt.timedelta(days=1), item.status, item.finished_at),
            )
        items_by_id = {item.id: item for item in source.items}
        for obs in source.observations:
            item = items_by_id[obs.item_id]
            run_id = item.run_id
            checkin = item.checkin_date
            vn_date = (obs.observed_at + VN_OFFSET).date()
            lead_time = (checkin - vn_date).days
            option_key = hashlib.sha256(f"{source.code}:{obs.record_id}".encode()).hexdigest()
            if obs.price is None:
                cursor.execute(
                    "INSERT INTO price_observations (record_id,hotel_id,crawl_run_id,crawl_run_item_id,observed_at,"
                    "checkin_date,checkout_date,lead_time,room_option_index,room_option_key,is_sold_out,"
                    "availability_status) VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,1,'sold_out')",
                    (obs.record_id, item.hotel_id, run_id, obs.item_id, obs.observed_at, checkin,
                     checkin + dt.timedelta(days=1), lead_time, obs.option_index, option_key),
                )
                continue
            spec = obs.room or DEFAULT_ROOM
            cursor.execute(
                "INSERT INTO price_observations (record_id,hotel_id,crawl_run_id,crawl_run_item_id,observed_at,"
                "checkin_date,checkout_date,lead_time,room_option_index,room_option_key,room_identity_key,"
                "rate_plan_key,price_total,price_per_night,is_sold_out,availability_status,room_type_raw,"
                "max_occupancy,bed_config,room_area,breakfast_included,free_cancellation,cancellation_policy) "
                "VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,0,'available',%s,%s,%s,%s,%s,%s,%s)",
                (obs.record_id, item.hotel_id, run_id, obs.item_id, obs.observed_at, checkin,
                 checkin + dt.timedelta(days=1), lead_time, obs.option_index, option_key,
                 room_identity_key(spec), rate_plan_key(spec), obs.price, obs.price, spec["room_type_raw"],
                 spec["max_occupancy"], spec["bed_config"], spec["room_area"], spec["breakfast_included"],
                 spec["free_cancellation"], spec["cancellation_policy"]),
            )
        conn.commit()
        cursor.close()


def _dump_source_db(source_db: str, dump_path: Path) -> None:
    from app.core.config import settings
    from app.warehouse.staging import _client_defaults_file

    with _client_defaults_file(settings.DB_USER, settings.DB_PASSWORD) as cnf, open(dump_path, "wb") as out:
        subprocess.run(
            ["mysqldump", f"--defaults-extra-file={cnf}", "--single-transaction", "--no-tablespaces",
             "--set-charset", "--complete-insert", source_db, "hotels", "crawl_runs", "crawl_run_items",
             "price_observations"],
            stdout=out, check=True,
        )


DEFAULT_HOLIDAYS_CSV = (
    "holiday_date,event_code,name,event_type,scope,city,is_tet,status,source_url\n"
    "2026-09-02,national_day,Quoc khanh,public_holiday,national,,0,confirmed,https://example.test\n"
)


@contextlib.contextmanager
def build_fixture_warehouse(
    tmp_path: Path, *, sources: list[FxSource], hotels: dict[str, str],
    ownership_rows: list[tuple[str, dt.date, str, dt.date]], holidays_csv: str = DEFAULT_HOLIDAYS_CSV,
) -> Iterator[dict]:
    """`hotels`: `{hotel_id: city}` (city PHAI thuoc 5 thanh pho cua cohort). `ownership_rows`:
    `(owner_source, crawl_date, schedule_slot, checkin_date)`. Yield dict path + batch; DROP moi database
    disposable trong `finally`."""
    db._ensure_backend_importable()
    from app.warehouse.batch import BuildInputs, build_warehouse
    from app.warehouse.bootstrap import apply_setup_sql, create_etl_tables, create_warehouse_database
    from app.warehouse.cohort_manifest import VALID_CITIES, load_cohort_manifest
    from app.warehouse.hashing import file_sha256
    from app.warehouse.ownership_manifest import OwnershipRow, write_ownership_manifest
    from app.warehouse.source_manifest import compute_schema_sha256_from_dump

    tag = uuid.uuid4().hex[:8]
    warehouse_db = f"warehouse_edafxwh{tag}"
    created = [warehouse_db]
    try:
        manifest_sources = []
        for source in sources:
            source_db = f"warehouse_edafx{tag}{source.code}"[:60]
            created.append(source_db)
            _populate_source_db(source_db, source, hotels)
            dump_path = tmp_path / f"src_{source.code}.sql"
            _dump_source_db(source_db, dump_path)
            manifest_sources.append({
                "source_code": source.code, "source_priority": source.priority, "dump_path": str(dump_path),
                "dump_sha256": file_sha256(dump_path), "dump_taken_at": source.dump_taken_at,
                "schema_sha256": compute_schema_sha256_from_dump(dump_path)[0],
                "source_version_json": {"fixture": True},
            })
        source_manifest_path = tmp_path / "source_manifest.json"
        source_manifest_path.write_text(json.dumps({"manifest_version": 1, "sources": manifest_sources}), encoding="utf-8")

        cohort_path = tmp_path / "cohort.xlsx"
        workbook = openpyxl.Workbook()
        workbook.remove(workbook.active)
        for city in VALID_CITIES:
            workbook.create_sheet(city).append(["Tên khách sạn", "Link"])
        for hotel_id, city in hotels.items():
            workbook[city].append([hotel_id.upper(), f"https://www.booking.com/hotel/vn/{hotel_id}.vi.html"])
        workbook.save(cohort_path)

        ownership_path = tmp_path / "ownership.json"
        write_ownership_manifest([OwnershipRow(*row) for row in ownership_rows], ownership_path)

        earliest = min(row[1] for row in ownership_rows)
        cohort_history_path = tmp_path / "cohort_history.json"
        cohort_history_path.write_text(json.dumps({
            "cohort_history_version": 1,
            "versions": [{"cohort_version": "v1", "effective_from_crawl_date": earliest.isoformat(),
                          "workbook_path": "cohort.xlsx",
                          "members_sha256": load_cohort_manifest(cohort_path).manifest_sha256, "size": len(hotels)}],
        }), encoding="utf-8")

        holidays_path = tmp_path / "vn_holidays.csv"
        holidays_path.write_text(holidays_csv, encoding="utf-8")

        create_warehouse_database(warehouse_db)
        apply_setup_sql(warehouse_db)
        create_etl_tables(warehouse_db)
        batch_id = f"fx{tag}"
        report = build_warehouse(BuildInputs(
            warehouse_database=warehouse_db, source_manifest_path=source_manifest_path,
            cohort_manifest_path=cohort_path, ownership_manifest_path=ownership_path, base_dir=tmp_path,
            batch_id=batch_id, report_dir=tmp_path / "reports", require_clean_provenance=False,
        ))
        assert report["status"] == "pass", report.get("failed_checks")

        pointer_path = tmp_path / "pointer.json"
        pointer_path.write_text(json.dumps({
            "warehouse_database": warehouse_db, "batch_id": batch_id,
            "source_manifest_sha256": report["steps"]["16_source_manifest_sha256"],
            "cohort_manifest_sha256": None, "ownership_manifest_sha256": None,
        }), encoding="utf-8")
        yield {
            "pointer_path": pointer_path, "warehouse_validation_report_path": tmp_path / "reports" / f"{batch_id}.json",
            "ownership_manifest_path": ownership_path, "cohort_history_path": cohort_history_path,
            "vn_holidays_csv_path": holidays_path, "batch_id": batch_id, "source_manifest_path": source_manifest_path,
            "warehouse_database": warehouse_db,
        }
    finally:
        for database in created:
            _root(f"DROP DATABASE IF EXISTS `{database}`")

"""E2E `build_warehouse` tren MySQL THAT voi 2 nguon synthetic - opt-in (WAREHOUSE_SMOKE=1).

Dump fixture duoc sinh bang CHINH `mysqldump` that tu 2 DB tam dung setup.sql, nen di qua dung
preflight/allowlist/schema gate nhu dump that. Du lieu thiet ke de phu du cac nhom ownership:
owner_success, owner_failure (sold_out), protocol_deviation (hotel ngoai cohort), unassigned
off_plan_unknown, unassigned pre_protocol_pilot, non_owner_duplicate - va ca "Mac Valley": hotel `hd`
thuoc cohort v1 nhung roi cohort tu v2 (02/09) -> item 01/09 van owner_success va giu city, chi item
02/09 moi la protocol_deviation. Build 2 lan vao 2 warehouse de chung minh checksum tai lap (GPT D10
gate 7). Moi DB tam deu bi DROP trong finally.
"""
from __future__ import annotations

import dataclasses
import datetime as dt
import hashlib
import json
import os
import subprocess
import uuid

import openpyxl
import pytest

from app.core.config import settings
from app.scraper.reference import rate_plan_key, room_identity_key
from app.warehouse.batch import BuildInputs, build_warehouse
from app.warehouse.bootstrap import apply_setup_sql, create_etl_tables, create_warehouse_database
from app.warehouse.cohort_manifest import VALID_CITIES, load_cohort_manifest
from app.warehouse.connection import warehouse_connection
from app.warehouse.hashing import file_sha256
from app.warehouse.ownership_manifest import OwnershipRow, write_ownership_manifest
from app.warehouse.source_manifest import compute_schema_sha256_from_dump
from app.warehouse.staging import _client_defaults_file

pytestmark = pytest.mark.skipif(os.environ.get("WAREHOUSE_SMOKE") != "1", reason="can MySQL that")

D, T = dt.date, dt.datetime
ROOM = {"room_type_raw": "Phòng Deluxe", "max_occupancy": 2, "bed_config": "1 giường đôi", "room_area": "20 m²",
        "breakfast_included": True, "free_cancellation": False, "cancellation_policy": "Không hoàn tiền"}
RUN_OBSERVED = {100: T(2026, 8, 14, 18, 0), 101: T(2026, 8, 31, 18, 0), 102: T(2026, 9, 1, 18, 0),
                201: T(2026, 8, 31, 18, 0)}


def _root(sql, params=()):
    with warehouse_connection(None, verify=False) as conn:
        cursor = conn.cursor()
        cursor.execute(sql, params)
        conn.commit()
        cursor.close()


def _checksums(database, tables=None):
    from app.warehouse.validation import semantic_checksums

    with warehouse_connection(database) as conn:
        return semantic_checksums(conn, tables=tables)


def _notes(database, batch_id):
    with warehouse_connection(database) as conn:
        cursor = conn.cursor()
        cursor.execute("SELECT notes FROM etl_import_batches WHERE batch_id=%s", (batch_id,))
        row = cursor.fetchone()
        cursor.close()
    return json.loads(row[0])


def _set_notes(database, batch_id, notes):
    _root(f"UPDATE `{database}`.etl_import_batches SET notes=%s WHERE batch_id=%s",
          (json.dumps(notes, ensure_ascii=False, sort_keys=True, default=str), batch_id))


def _observation(record_id, hotel, run, item, checkin, index, *, sold_out=False, price=500000):
    observed = RUN_OBSERVED[run]
    base = {"record_id": record_id, "hotel_id": hotel, "crawl_run_id": run, "crawl_run_item_id": item,
            "observed_at": observed, "checkin_date": checkin, "checkout_date": checkin + dt.timedelta(days=1),
            "lead_time": (checkin - (observed + dt.timedelta(hours=7)).date()).days,
            "room_option_index": index, "room_option_key": hashlib.sha256(f"{record_id}".encode()).hexdigest()}
    if sold_out:
        return {**base, "is_sold_out": 1, "availability_status": "sold_out"}
    room = {**ROOM, "room_type_raw": f"Phòng {index}"}
    return {**base, **room, "price_total": price + index, "price_per_night": price + index, "is_sold_out": 0,
            "availability_status": "available", "room_identity_key": room_identity_key(room),
            "rate_plan_key": rate_plan_key(room),
            "breakfast_included": int(room["breakfast_included"]), "free_cancellation": int(room["free_cancellation"])}


SOURCES = {
    "local_primary": {
        "hotels": [("h1", "H1 A", T(2026, 9, 2)), ("h2", "H2 A", T(2026, 9, 2)), ("hx", "HX A", T(2026, 9, 2)),
                   ("hd", "HD A", T(2026, 9, 2))],
        "runs": [(100, T(2026, 8, 14, 17, 35)), (101, T(2026, 8, 31, 17, 35)), (102, T(2026, 9, 1, 17, 35))],
        "items": [(1000, 100, "h1", D(2026, 8, 20), "success"), (1001, 101, "h1", D(2026, 9, 5), "success"),
                  (1002, 101, "h2", D(2026, 9, 5), "sold_out"), (1003, 101, "hx", D(2026, 9, 5), "success"),
                  (1004, 101, "h1", D(2026, 9, 7), "success"),
                  # hd = "Mac Valley": thanh vien cohort v1, roi cohort tu v2 (crawl date 02/09)
                  (1005, 101, "hd", D(2026, 9, 5), "success"), (1006, 102, "hd", D(2026, 9, 6), "success"),
                  (1007, 102, "h1", D(2026, 9, 6), "success")],
    },
    "vps": {
        "hotels": [("h1", "H1 B", T(2026, 9, 5))],
        "runs": [(201, T(2026, 8, 31, 17, 40))],
        "items": [(2001, 201, "h1", D(2026, 9, 10), "success"), (2002, 201, "h1", D(2026, 9, 5), "success")],
    },
}
COHORT_V1 = {"Hà Nội": [("H1", "h1")], "Đà Lạt": [("H2", "h2"), ("HD", "hd")]}
COHORT_V2 = {"Hà Nội": [("H1", "h1")], "Đà Lạt": [("H2", "h2")]}


def _make_dump(tmp_path, code, tag):
    database = f"warehouse_fxsrc{tag}{code[:3]}"
    create_warehouse_database(database)
    apply_setup_sql(database)
    spec = SOURCES[code]
    with warehouse_connection(database) as conn:
        cursor = conn.cursor()
        for hotel, name, updated in spec["hotels"]:
            cursor.execute("INSERT INTO hotels (hotel_id,name,hotel_link,city,attributes_updated_at,created_at) "
                           "VALUES (%s,%s,%s,'Sai',%s,%s)", (hotel, name, f"https://x/{hotel}", updated, updated))
        for run, started in spec["runs"]:
            cursor.execute("INSERT INTO crawl_runs (id,status,started_at,finished_at) VALUES (%s,'completed',%s,%s)",
                           (run, started, started + dt.timedelta(hours=2)))
        record = 1
        for item, run, hotel, checkin, status in spec["items"]:
            cursor.execute("INSERT INTO crawl_run_items (id,crawl_run_id,source_hotel_link,source_link_hash,hotel_link,"
                           "hotel_id,checkin_date,checkout_date,status) VALUES (%s,%s,'u',%s,'u',%s,%s,%s,%s)",
                           (item, run, hashlib.sha256(f"{hotel}".encode()).hexdigest(), hotel, checkin,
                            checkin + dt.timedelta(days=1), status))
            observations = ([_observation(record, hotel, run, item, checkin, 0, sold_out=True)] if status == "sold_out"
                            else [_observation(record + i, hotel, run, item, checkin, i) for i in range(2)])
            for obs in observations:
                cols = ", ".join(obs)
                cursor.execute(f"INSERT INTO price_observations ({cols}) VALUES ({', '.join(['%s'] * len(obs))})",
                               tuple(obs.values()))
            record += len(observations)
        conn.commit()
        cursor.close()
    path = tmp_path / f"{code}.sql"
    with _client_defaults_file(settings.DB_USER, settings.DB_PASSWORD) as cnf, open(path, "wb") as out:
        subprocess.run(["mysqldump", f"--defaults-extra-file={cnf}", "--single-transaction", "--no-tablespaces",
                        "--set-charset", "--complete-insert", database, "hotels", "crawl_runs", "crawl_run_items",
                        "price_observations"], stdout=out, check=True)
    return database, path


def _write_cohort(path, members):
    workbook = openpyxl.Workbook()
    workbook.remove(workbook.active)
    for city in VALID_CITIES:
        sheet = workbook.create_sheet(city)
        sheet.append(["Tên khách sạn", "Link"])
        for name, slug in members.get(city, []):
            sheet.append([name, f"https://www.booking.com/hotel/vn/{slug}.vi.html"])
    workbook.save(path)
    manifest = load_cohort_manifest(path)
    return {"workbook_path": path.name, "members_sha256": manifest.manifest_sha256, "size": manifest.size}


def _inputs(tmp_path, dumps, database):
    manifest = {"manifest_version": 1, "sources": [
        {"source_code": code, "source_priority": priority, "dump_path": str(path), "dump_sha256": file_sha256(path),
         "dump_taken_at": "2026-09-16T00:00:00Z", "schema_sha256": compute_schema_sha256_from_dump(path)[0],
         "source_version_json": {"fixture": True}}
        for priority, (code, path) in enumerate(dumps)]}
    (tmp_path / "sources.json").write_text(json.dumps(manifest), encoding="utf-8")
    versions = [{"cohort_version": "v1", "effective_from_crawl_date": "2026-09-01",
                 **_write_cohort(tmp_path / "cohort_v1.xlsx", COHORT_V1)},
                {"cohort_version": "v2", "effective_from_crawl_date": "2026-09-02",
                 **_write_cohort(tmp_path / "cohort_v2.xlsx", COHORT_V2)}]
    (tmp_path / "cohort_history.json").write_text(
        json.dumps({"cohort_history_version": 1, "versions": versions}), encoding="utf-8")
    write_ownership_manifest([OwnershipRow("local_primary", D(2026, 9, 1), "N1", D(2026, 9, 5)),
                              OwnershipRow("local_primary", D(2026, 9, 2), "N1", D(2026, 9, 6)),
                              OwnershipRow("vps", D(2026, 9, 1), "V1", D(2026, 9, 10))], tmp_path / "ownership.json")
    return BuildInputs(warehouse_database=database, source_manifest_path=tmp_path / "sources.json",
                       cohort_manifest_path=tmp_path / "cohort_history.json",
                       ownership_manifest_path=tmp_path / "ownership.json", base_dir=tmp_path,
                       report_dir=tmp_path / "reports", require_clean_provenance=False)


def _init(database):
    create_warehouse_database(database)
    apply_setup_sql(database)
    create_etl_tables(database)


def test_build_2_nguon_synthetic_pass_va_tai_lap(tmp_path, monkeypatch):
    tag = uuid.uuid4().hex[:6]
    created = []
    try:
        dumps = []
        for code in ("local_primary", "vps"):
            database, path = _make_dump(tmp_path, code, tag)
            created.append(database)
            dumps.append((code, path))
        warehouses = [f"warehouse_fxa{tag}", f"warehouse_fxb{tag}"]
        reports = []
        for index, warehouse in enumerate(warehouses):
            _init(warehouse)
            created.append(warehouse)
            inputs = dataclasses.replace(_inputs(tmp_path, dumps, warehouse), batch_id=f"fx{tag}{index}")
            reports.append(build_warehouse(inputs))
        first = reports[0]
        assert first["status"] == "pass", first.get("failed_checks")
        assert [version["cohort_version"] for version in first["steps"]["cohort_versions"]] == ["v1", "v2"]
        imported = first["steps"]["11_import"]
        assert imported["hotels"] == {"source_hotel_rows": 5, "rejected_hotel_source_rows": 0,
                                      "accepted_hotel_source_rows": 5, "warehouse_distinct_hotels": 4,
                                      "duplicate_business_key_rows": 1}
        assert first["steps"]["14_curated"]["written"] == 19 and first["steps"]["14_curated"]["sold_out"] == 1
        deviations = first["steps"]["13_validation"]["numbers"]["protocol_deviation_by_hotel"]
        assert sorted(deviations) == [["local_primary", "hd", 1], ["local_primary", "hx", 1]]
        with warehouse_connection(warehouses[0]) as conn:
            cursor = conn.cursor()
            cursor.execute("SELECT m.source_code, m.source_item_id, m.ownership_status, m.exclusion_reason "
                           "FROM etl_item_map m ORDER BY 1, 2")
            ownership = {(row[0], row[1]): (row[2], row[3]) for row in cursor.fetchall()}
            cursor.execute("SELECT name, city FROM hotels WHERE hotel_id IN ('h1','hd','hx') ORDER BY hotel_id")
            hotels = cursor.fetchall()
            cursor.close()
        assert ownership == {
            ("local_primary", 1000): ("unassigned", "pre_protocol_pilot"),
            ("local_primary", 1001): ("owner_success", None),
            ("local_primary", 1002): ("owner_failure", "owner_failure_status_sold_out"),
            ("local_primary", 1003): ("protocol_deviation", "hotel_outside_cohort_manifest"),
            ("local_primary", 1004): ("unassigned", "off_plan_unknown"),
            ("local_primary", 1005): ("owner_success", None),  # hd con trong cohort v1 ngay 01/09
            ("local_primary", 1006): ("protocol_deviation", "hotel_outside_cohort_manifest"),  # hd da roi (v2)
            ("local_primary", 1007): ("owner_success", None),
            ("vps", 2001): ("owner_success", None),
            ("vps", 2002): ("non_owner_duplicate", "non_owner_duplicate"),
        }
        # attribute moi hon tu vps; hd da roi cohort van giu city; hx chua bao gio thuoc cohort -> NULL
        assert hotels == [("H1 B", "Hà Nội"), ("HD A", "Đà Lạt"), ("HX A", None)]
        for kind in ("semantic", "exact"):  # exact cung phai trung vi ID gan xac dinh (D1)
            differing = sorted(table for table, digest in reports[0]["checksums"][kind].items()
                               if reports[1]["checksums"][kind][table] != digest)
            assert differing == [], f"checksum {kind} lech o: {differing}"

        # --- Buoc 17 promote + 2 gate moi cua GPT review 12 (pointer TAM, khong dung outputs/warehouse that)
        from app.warehouse.errors import BatchStateError, ProvenanceError
        from app.warehouse.promote import promote_warehouse
        from app.warehouse.provenance import DIRTY_SUFFIX, code_provenance
        from app.warehouse.rebuild_references import rebuild_full_history_references

        pointer = tmp_path / "pointer" / "warehouse_current.json"
        head = code_provenance(require_clean=False).removesuffix(DIRTY_SUFFIX)
        # Ca 3 dang provenance KHONG replay duoc deu phai bi tu choi TRUOC khi pointer duoc tao. "a" * 40
        # dung dinh dang nhung khong ton tai trong git (GPT review 14 MINOR).
        for bad in (head + DIRTY_SUFFIX, "a" * 40, "unknown"):
            _root(f"UPDATE `{warehouses[0]}`.etl_import_batches SET canonicalization_git_commit=%s", (bad,))
            with pytest.raises(ProvenanceError):
                promote_warehouse(warehouses[0], f"fx{tag}0", pointer_path=pointer)
        assert not pointer.exists()
        for warehouse in warehouses:  # gia lap batch build tu code da commit: commit THAT, co trong git
            _root(f"UPDATE `{warehouse}`.etl_import_batches SET canonicalization_git_commit=%s", (head,))
        assert promote_warehouse(warehouses[0], f"fx{tag}0", pointer_path=pointer)["promoted"] is True
        assert json.loads(pointer.read_text(encoding="utf-8"))["batch_id"] == f"fx{tag}0"
        assert promote_warehouse(warehouses[0], f"fx{tag}0", pointer_path=pointer)["promoted"] is False

        # MAJOR 1 (c): cung config, chua promote -> rebuild tai lap NOI DUNG (semantic y nguyen o ca 11 bang).
        # Exact chi duoc phep lech o hotel_reference_rooms: InnoDB khong lui AUTO_INCREMENT sau DELETE.
        before = _checksums(warehouses[1])
        assert rebuild_full_history_references(warehouses[1], f"fx{tag}1", pointer_path=pointer)["rebuilt"]
        after = _checksums(warehouses[1])
        assert after["semantic"] == before["semantic"]
        assert set(table for table in after["exact"] if after["exact"][table] != before["exact"][table]) <= {
            "hotel_reference_rooms"}

        # MAJOR 1 (a): doi threshold -> FAIL TRUOC khi DELETE, 2 bang reference giu nguyen
        monkeypatch.setattr(settings, "REFERENCE_MIN_COVERAGE", settings.REFERENCE_MIN_COVERAGE / 2)
        with pytest.raises(BatchStateError, match="etl_config"):
            rebuild_full_history_references(warehouses[1], f"fx{tag}1", pointer_path=pointer)
        monkeypatch.undo()
        assert _checksums(warehouses[1]) == after

        # MAJOR 1 (b): warehouse dang la current -> tu choi, pointer va du lieu giu nguyen
        with pytest.raises(BatchStateError, match="current"):
            rebuild_full_history_references(warehouses[0], f"fx{tag}0", pointer_path=pointer)
        assert json.loads(pointer.read_text(encoding="utf-8"))["batch_id"] == f"fx{tag}0"

        # GPT review 14 MAJOR: notes.checksums thieu 1 bang -> gate khong con day du -> khong promote
        notes = _notes(warehouses[1], f"fx{tag}1")
        broken = json.loads(json.dumps(notes))
        del broken["checksums"]["semantic"]["price_observations"]
        _set_notes(warehouses[1], f"fx{tag}1", broken)
        with pytest.raises(BatchStateError, match="notes.checksums"):
            promote_warehouse(warehouses[1], f"fx{tag}1", pointer_path=pointer)
        assert json.loads(pointer.read_text(encoding="utf-8"))["batch_id"] == f"fx{tag}0"
        _set_notes(warehouses[1], f"fx{tag}1", notes)

        # Gate cu: warehouse bi sua sau build -> validation tai thoi diem promote FAIL, pointer giu nguyen
        _root(f"UPDATE `{warehouses[1]}`.crawl_run_items SET hotel_id=NULL WHERE status='success' ORDER BY id LIMIT 1")
        with pytest.raises(BatchStateError, match="validation tai thoi diem promote FAIL"):
            promote_warehouse(warehouses[1], f"fx{tag}1", pointer_path=pointer)
        assert json.loads(pointer.read_text(encoding="utf-8"))["batch_id"] == f"fx{tag}0"
    finally:
        for database in created:
            _root(f"DROP DATABASE IF EXISTS `{database}`")


def test_build_vao_warehouse_khong_rong_thi_fail_va_dump_bi_sua_thi_fail(tmp_path):
    from app.warehouse.errors import BatchStateError, ManifestError

    tag = uuid.uuid4().hex[:6]
    created = []
    try:
        database, path = _make_dump(tmp_path, "vps", tag)
        created.append(database)
        warehouse = f"warehouse_fxc{tag}"
        _init(warehouse)
        created.append(warehouse)
        _root(f"INSERT INTO `{warehouse}`.hotels (hotel_id,name,hotel_link) VALUES ('z','Z','u')")
        with pytest.raises(BatchStateError, match="KHONG rong"):
            build_warehouse(_inputs(tmp_path, [("vps", path)], warehouse))
        inputs = _inputs(tmp_path, [("vps", path)], warehouse)
        with open(path, "ab") as handle:
            handle.write(b"-- sua sau khi tao manifest\n")
        with pytest.raises(ManifestError, match="dump_sha256 KHONG KHOP"):
            build_warehouse(inputs)
    finally:
        for database in created:
            _root(f"DROP DATABASE IF EXISTS `{database}`")

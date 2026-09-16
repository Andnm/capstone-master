"""Duong rejection cua importer (GPT file 02 D6) - fake DB, khong can MySQL.

Dung "dong hong" co chu dich: run 11, item 22, observation 33 bi DB tu choi khi insert. Kiem:
- chunk loi -> thu tung dong: dong tot van vao, dong hong -> `row_error`;
- con cua dong bi reject -> `parent_rejected` o CA run->item VA item->observation;
- doi soat muc 7: source = imported + row_error + parent_rejected, khong dem trung;
- fingerprint map tinh tren dong NGUON (ID nguon), khong phai dong da remap;
- `raw_row_json` cua row_error la DUNG dong do (bug lech chi so da sua).
"""
from __future__ import annotations

import datetime as dt
import json
from collections import defaultdict
from contextlib import contextmanager
from pathlib import Path
from types import MappingProxyType

import mysql.connector

from app.scraper.anomaly_registry_lib import observation_fingerprint
from app.warehouse.cohort_manifest import CohortManifest, single_version_history
from app.warehouse.importer import Importer, SourceStaging
from app.warehouse.ownership_manifest import OwnershipManifest, OwnershipRow

D, T = dt.date, dt.datetime
COLUMNS = {
    "crawl_runs": ["id", "status", "started_at", "created_at", "retry_of_run_id"],
    "crawl_run_items": ["id", "crawl_run_id", "hotel_id", "checkin_date", "status", "hotel_link", "reference_match_status"],
    "price_observations": ["record_id", "hotel_id", "crawl_run_id", "crawl_run_item_id", "observed_at", "checkin_date",
                           "checkout_date", "room_option_index", "room_identity_key", "rate_plan_key", "price_total",
                           "price_per_night", "room_option_key", "is_reference_room", "reference_definition_id",
                           "reference_match_status", "reference_match_score"],
}
RUN = T(2026, 8, 31, 17, 35)  # 00:35 ngay 01/09 gio VN


def obs(record_id, item, run, key="k"):
    return {"record_id": record_id, "hotel_id": "h1", "crawl_run_id": run, "crawl_run_item_id": item,
            "observed_at": T(2026, 8, 31, 18), "checkin_date": D(2026, 9, 5), "checkout_date": D(2026, 9, 6),
            "room_option_index": record_id, "room_identity_key": "r" * 64, "rate_plan_key": "p" * 64,
            "price_total": 100, "price_per_night": 100, "room_option_key": key, "is_reference_room": 1,
            "reference_definition_id": 9, "reference_match_status": "exact", "reference_match_score": 1}


STAGING = {
    "crawl_runs": [{"id": 10, "status": "completed", "started_at": RUN, "created_at": RUN, "retry_of_run_id": None},
                   {"id": 11, "status": "POISON", "started_at": RUN, "created_at": RUN, "retry_of_run_id": None},
                   {"id": 12, "status": "completed", "started_at": RUN, "created_at": RUN, "retry_of_run_id": 10}],
    "crawl_run_items": [
        {"id": 21, "crawl_run_id": 10, "hotel_id": "h1", "checkin_date": D(2026, 9, 5), "status": "success", "hotel_link": "u"},
        {"id": 22, "crawl_run_id": 10, "hotel_id": "h1", "checkin_date": D(2026, 9, 5), "status": "success", "hotel_link": "POISON"},
        {"id": 23, "crawl_run_id": 11, "hotel_id": "h1", "checkin_date": D(2026, 9, 5), "status": "success", "hotel_link": "u"},
        {"id": 24, "crawl_run_id": 12, "hotel_id": "h1", "checkin_date": D(2026, 9, 5), "status": "success", "hotel_link": "u"}],
    "price_observations": [obs(31, 21, 10), obs(32, 22, 10), obs(33, 21, 10, key="POISON"), obs(34, 23, 11), obs(35, 24, 12)],
}


class FakeDB:
    def __init__(self):
        self.inserted = defaultdict(list)
        self.maps = defaultdict(list)
        self.rejections = []
        self.updates = []

    @staticmethod
    def poisoned(table, params):
        return "POISON" in dict(zip(COLUMNS[table], params)).values()


class FakeCursor:
    def __init__(self, db, rows=None):
        self.db, self.rows, self.with_rows = db, rows or [], False

    def execute(self, sql, params=()):
        text = " ".join(sql.split())
        if text.startswith("SELECT COLUMN_NAME"):
            self.rows = [(column,) for column in COLUMNS[params[0]]]
        elif text.startswith("INSERT INTO `"):
            table = text.split("`")[1]
            if self.db.poisoned(table, params):
                raise mysql.connector.Error(msg="poison row")
            self.db.inserted[table].append(dict(zip(COLUMNS[table], params)))
        elif text.startswith("INSERT INTO etl_import_rejections"):
            self.db.rejections.append(params)
        elif text.startswith("UPDATE crawl_runs SET retry_of_run_id"):
            self.db.updates.append(params)
        elif text.startswith("SELECT * FROM"):
            self.rows = [dict(row) for row in STAGING[text.split()[3]]]
        else:
            raise AssertionError(f"SQL la trong fake: {text[:80]}")

    def executemany(self, sql, seq):
        text = " ".join(sql.split())
        if text.startswith("INSERT INTO `"):
            table = text.split("`")[1]
            if any(self.db.poisoned(table, params) for params in seq):
                raise mysql.connector.Error(msg="chunk co dong hong")
            self.db.inserted[table].extend(dict(zip(COLUMNS[table], params)) for params in seq)
        else:
            self.db.maps[text.split()[2]].extend(seq)

    def fetchall(self):
        return self.rows

    def fetchmany(self, size):
        out, self.rows = self.rows[:size], self.rows[size:]
        return out

    def close(self):
        pass


class FakeConn:
    def __init__(self, db):
        self.db = db

    def cursor(self, dictionary=False):
        return FakeCursor(self.db)

    def commit(self):
        pass

    def rollback(self):
        pass


def run_import():
    db = FakeDB()

    @contextmanager
    def connect(_staging):
        yield FakeConn(db)

    manifest = OwnershipManifest.from_rows([OwnershipRow("vps", D(2026, 9, 1), "V1", D(2026, 9, 5))])
    cohort = single_version_history(CohortManifest(path=Path("cohort.xlsx"), hotel_city=MappingProxyType({"h1": "Hà Nội"})))
    importer = Importer(FakeConn(db), batch_id="b1", cohort=cohort, ownership=manifest,
                        imported_at=T(2026, 9, 16), connect=connect)
    importer.import_source(SourceStaging("vps", 0, "wh_staging_b1_vps"))
    return db, importer


def rejected(db, table, scope):
    return sorted(int(params[3]) for params in db.rejections if params[2] == table and params[4] == scope)


def test_row_error_va_parent_rejected_o_ca_2_tang():
    db, importer = run_import()
    assert rejected(db, "crawl_runs", "row_error") == [11]
    assert rejected(db, "crawl_run_items", "row_error") == [22]
    assert rejected(db, "crawl_run_items", "parent_rejected") == [23]
    assert rejected(db, "price_observations", "row_error") == [33]
    assert rejected(db, "price_observations", "parent_rejected") == [32, 34]


def test_dong_tot_trong_chunk_hong_van_duoc_insert_va_doi_soat_khop():
    db, importer = run_import()
    assert [row["room_option_index"] for row in db.inserted["price_observations"]] == [31, 35]
    stats = importer.report.per_source["vps"]
    for table in ("crawl_runs", "crawl_run_items", "price_observations"):
        errors = len(rejected(db, table, "row_error")) + len(rejected(db, table, "parent_rejected"))
        assert stats[f"{table}_source"] == stats[f"{table}_imported"] + errors, table


def test_fingerprint_map_tinh_tren_dong_nguon_va_reference_bi_reset():
    db, _importer = run_import()
    mapped = {row[2]: row[3] for row in db.maps["etl_observation_map"]}
    source = {row["record_id"]: row for row in STAGING["price_observations"]}
    assert mapped == {31: observation_fingerprint(source[31]), 35: observation_fingerprint(source[35])}
    for row in db.inserted["price_observations"]:
        assert (row["is_reference_room"], row["reference_definition_id"], row["reference_match_status"],
                row["reference_match_score"]) == (False, None, "calibrating", None)


def test_raw_row_json_cua_row_error_la_dung_dong_do():
    db, _importer = run_import()
    payload = [json.loads(params[7]) for params in db.rejections if params[2] == "price_observations" and params[4] == "row_error"]
    assert [row["record_id"] for row in payload] == [33]


def test_retry_of_run_id_remap_sau_khi_du_map():
    db, _importer = run_import()
    wid = {row["status"]: row["id"] for row in db.inserted["crawl_runs"]}
    assert db.updates == [(1, 3)] and wid["completed"] in (1, 3)  # run 12 (wid 3) -> run 10 (wid 1); run 11 hong giu wid 2

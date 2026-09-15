"""Smoke test tren MySQL THAT - opt-in.

Chay:  WAREHOUSE_SMOKE=1 python -m pytest tests/test_warehouse_init_smoke.py -q

Khong bat mac dinh vi no tao/xoa database that. Nhung day la thu DUY NHAT chung minh duoc DDL 11
bang ETL thuc su chay tren MySQL 8.0.45 (CHECK constraint, composite FK, 4 self-FK cua ml_samples)
- test pure khong the chung minh dieu do.

Database dung trong test luon co prefix `warehouse_smoketest_` va bi DROP trong `finally`.
"""
from __future__ import annotations

import os
import uuid

import pytest

from app.warehouse.bootstrap import (
    apply_setup_sql,
    assert_ready_for_build,
    create_etl_tables,
    create_warehouse_database,
    database_exists,
    list_tables,
)
from app.warehouse.connection import warehouse_connection
from app.warehouse.errors import BatchStateError
from app.warehouse.etl_ddl import CORE_TABLES, ETL_TABLE_NAMES

pytestmark = pytest.mark.skipif(
    os.environ.get("WAREHOUSE_SMOKE") != "1",
    reason="can MySQL that - dat WAREHOUSE_SMOKE=1 de chay",
)


def _drop(name: str) -> None:
    with warehouse_connection(None, verify=False) as conn:
        cursor = conn.cursor()
        try:
            cursor.execute(f"DROP DATABASE IF EXISTS `{name}`")
            conn.commit()
        finally:
            cursor.close()


@pytest.fixture()
def warehouse_db():
    name = f"warehouse_smoketest_{uuid.uuid4().hex[:8]}"
    try:
        yield name
    finally:
        _drop(name)


def test_init_tao_du_bang_va_deu_rong(warehouse_db):
    create_warehouse_database(warehouse_db)
    apply_setup_sql(warehouse_db)
    create_etl_tables(warehouse_db)

    tables = list_tables(warehouse_db)
    for table in (*CORE_TABLES, *ETL_TABLE_NAMES):
        assert table in tables, f"thieu bang {table}"
    assert len(ETL_TABLE_NAMES) == 11
    assert_ready_for_build(warehouse_db)


def test_tao_lai_cung_ten_thi_fail(warehouse_db):
    create_warehouse_database(warehouse_db)
    with pytest.raises(BatchStateError, match="da ton tai"):
        create_warehouse_database(warehouse_db)


def test_khong_tao_duoc_warehouse_khong_dung_prefix():
    from app.warehouse.errors import NamingError

    with pytest.raises(NamingError):
        create_warehouse_database("hotel_price_intel_copy")


def test_assert_ready_fail_khi_core_co_du_lieu(warehouse_db):
    create_warehouse_database(warehouse_db)
    apply_setup_sql(warehouse_db)
    create_etl_tables(warehouse_db)
    with warehouse_connection(warehouse_db) as conn:
        cursor = conn.cursor()
        cursor.execute(
            "INSERT INTO hotels (hotel_id, name, hotel_link) VALUES ('x','X','https://x')"
        )
        conn.commit()
        cursor.close()
    with pytest.raises(BatchStateError, match="KHONG rong"):
        assert_ready_for_build(warehouse_db)


def test_check_constraint_cua_etl_that_su_duoc_cuong_che(warehouse_db):
    """MySQL 8.0.16+ moi enforce CHECK. Neu server khong enforce, ca thiet ke ownership sup do."""
    import mysql.connector

    create_warehouse_database(warehouse_db)
    apply_setup_sql(warehouse_db)
    create_etl_tables(warehouse_db)
    with warehouse_connection(warehouse_db) as conn:
        cursor = conn.cursor()
        try:
            cursor.execute(
                "INSERT INTO etl_import_batches (batch_id, warehouse_database, started_at, "
                " setup_sql_sha256, source_manifest_sha256, canonicalization_version, "
                " canonicalization_git_commit, canonicalization_config_sha256) "
                "VALUES ('b1', %s, NOW(), %s, %s, 'v1', 'abc', %s)",
                (warehouse_db, "a" * 64, "b" * 64, "c" * 64),
            )
            # chk_sources_code_format: 'VPS-1' khong khop ^[a-z][a-z0-9_]{0,19}$
            with pytest.raises(mysql.connector.Error):
                cursor.execute(
                    "INSERT INTO etl_import_sources (import_batch_id, source_code, source_priority, "
                    " dump_path, dump_sha256, dump_taken_at, schema_sha256, source_version_json) "
                    "VALUES ('b1','VPS-1',0,'/x',%s,NOW(),%s,'{}')",
                    ("d" * 64, "e" * 64),
                )
            conn.rollback()
        finally:
            cursor.close()


def test_drop_database_khong_prefix_bi_chan_o_tang_naming():
    from app.warehouse.errors import NamingError
    from app.warehouse.naming import require_droppable_staging

    with pytest.raises(NamingError):
        require_droppable_staging("hotel_price_intel", allowed={"hotel_price_intel"})


def test_database_exists_phan_biet_dung(warehouse_db):
    assert database_exists(warehouse_db) is False
    create_warehouse_database(warehouse_db)
    assert database_exists(warehouse_db) is True


# ======================================================================================
# staging.py - guard DROP va cleanup (muc 3a buoc 10/12)
# ======================================================================================
def _database_exists_raw(name: str) -> bool:
    with warehouse_connection(None, verify=False) as conn:
        cursor = conn.cursor()
        try:
            cursor.execute(
                "SELECT SCHEMA_NAME FROM information_schema.SCHEMATA WHERE SCHEMA_NAME=%s", (name,)
            )
            return cursor.fetchone() is not None
        finally:
            cursor.close()


def test_staging_bi_drop_ke_ca_khi_ben_trong_nem_loi(tmp_path):
    """Buoc 12 khong duoc phu thuoc vao duong thanh cong - phai DROP trong `finally`."""
    from app.warehouse.staging import staging_database

    dump = tmp_path / "tiny.sql"
    dump.write_text("INSERT INTO hotels (hotel_id,name,hotel_link) VALUES ('a','A','u');\n", encoding="utf-8")
    batch = f"t{uuid.uuid4().hex[:6]}"
    captured: list[str] = []

    class Boom(RuntimeError):
        pass

    with pytest.raises(Boom):
        with staging_database(batch, "vps", dump) as name:
            captured.append(name)
            assert _database_exists_raw(name)
            raise Boom("loi giua chung")

    assert captured and not _database_exists_raw(captured[0])


def test_staging_restore_that_va_dem_dong_dung(tmp_path):
    from app.warehouse.staging import count_core_rows, staging_database

    dump = tmp_path / "tiny.sql"
    dump.write_text(
        "INSERT INTO hotels (hotel_id,name,hotel_link) VALUES ('a','A','u'),('b','B','u2');\n",
        encoding="utf-8",
    )
    batch = f"t{uuid.uuid4().hex[:6]}"
    with staging_database(batch, "vps", dump) as name:
        assert name.startswith("wh_staging_")
        assert count_core_rows(name, ("hotels",))["hotels"] == 2


def test_file_defaults_extra_bi_xoa_sau_khi_dung():
    """Password khong duoc de lai tren dia sau khi lenh chay xong."""
    import os as _os

    from app.warehouse.staging import _client_defaults_file

    with _client_defaults_file() as path:
        assert _os.path.exists(path)
        leaked = path
    assert not _os.path.exists(leaked)

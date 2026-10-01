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
# staging.py - 5 lop an toan (GPT review 06/08). Moi DB/user tao ra deu co tag rieng va bi don
# trong finally; test cuoi kiem tra khong sot user tam `whr_%`.
# ======================================================================================
# Dump chi co INSERT, KHONG co DDL: truoc N2 (GPT 10) van qua preflight; gio phai bi chan (MISSING_TABLE_DDL).
INSERT_ONLY_DUMP = "INSERT INTO `hotels` (`hotel_id`,`name`,`hotel_link`) VALUES ('a','A','u'),('b','B','u2');\n"


@pytest.fixture(scope="module")
def tiny_dump(tmp_path_factory):
    """Dump 4 bang core sinh bang CHINH mysqldump that (2 hotel) - di dung duong preflight/allowlist/N2."""
    import subprocess

    from app.core.config import settings
    from app.warehouse.staging import _client_defaults_file

    source = f"warehouse_smoketest_src{uuid.uuid4().hex[:8]}"
    path = tmp_path_factory.mktemp("dump") / "tiny.sql"
    try:
        create_warehouse_database(source)
        apply_setup_sql(source)
        _root(f"INSERT INTO `{source}`.hotels (hotel_id,name,hotel_link) VALUES ('a','A','u'),('b','B','u2')")
        with _client_defaults_file(settings.DB_USER, settings.DB_PASSWORD) as cnf, open(path, "wb") as out:
            subprocess.run(["mysqldump", f"--defaults-extra-file={cnf}", "--single-transaction", "--no-tablespaces",
                            "--set-charset", "--complete-insert", source, *CORE_TABLES], stdout=out, check=True)
    finally:
        _drop(source)
    return path


def _database_exists_raw(name: str) -> bool:
    with warehouse_connection(None, verify=False) as conn:
        cursor = conn.cursor()
        try:
            cursor.execute("SELECT SCHEMA_NAME FROM information_schema.SCHEMATA WHERE SCHEMA_NAME=%s", (name,))
            return cursor.fetchone() is not None
        finally:
            cursor.close()


def _root(sql: str) -> None:
    with warehouse_connection(None, verify=False) as conn:
        cursor = conn.cursor()
        cursor.execute(sql)
        conn.commit()
        cursor.close()


def test_staging_restore_that_va_dem_dong(tiny_dump):
    from app.warehouse.staging import count_core_rows, staging_database

    with staging_database(f"t{uuid.uuid4().hex[:6]}", "vps", tiny_dump) as handle:
        assert handle.name.startswith("wh_staging_")
        assert handle.preflight["findings"] == []
        assert handle.preflight["tables_created"] == sorted(CORE_TABLES)
        assert handle.preflight["support_tables_checked"] > 0  # N2: bang ho tro cua setup.sql khong bi dump dong vao
        assert count_core_rows(handle.name, ("hotels",))["hotels"] == 2
    assert not _database_exists_raw(handle.name)


def test_staging_bi_drop_ke_ca_khi_ben_trong_nem_loi(tiny_dump):
    from app.warehouse.staging import staging_database

    captured = []
    with pytest.raises(RuntimeError, match="giua chung"):
        with staging_database(f"t{uuid.uuid4().hex[:6]}", "vps", tiny_dump) as handle:
            captured.append(handle.name)
            raise RuntimeError("loi giua chung")
    assert captured and not _database_exists_raw(captured[0])


def test_dump_chi_co_insert_thieu_ddl_core_bi_chan_o_preflight(tmp_path):
    """N2 (GPT 10): dump thieu DDL bang core -> FAIL o preflight; staging da tao van bi drop."""
    from app.warehouse.errors import SqlScriptError
    from app.warehouse.naming import staging_database_name
    from app.warehouse.staging import staging_database

    dump = tmp_path / "insert_only.sql"
    dump.write_text(INSERT_ONLY_DUMP, encoding="utf-8", newline="")
    batch = f"t{uuid.uuid4().hex[:6]}"
    with pytest.raises(SqlScriptError, match="MISSING_TABLE_DDL"):
        with staging_database(batch, "vps", dump):
            pass
    assert not _database_exists_raw(staging_database_name(batch, "vps"))


def test_create_staging_tu_choi_ten_khong_prefix_TRUOC_khi_phat_sql(monkeypatch):
    """BLOCKER 2: require_identifier('hotel_price_intel') PASS -> ban cu co the DROP DB van hanh."""
    import app.warehouse.staging as staging
    from app.warehouse.errors import NamingError

    def _must_not_run(*_args, **_kwargs):
        raise AssertionError("da phat SQL toi server - guard phai chan truoc")

    monkeypatch.setattr(staging, "_root_execute", _must_not_run)
    for name in ("hotel_price_intel", "warehouse_x", "mysql"):
        with pytest.raises(NamingError):
            staging.create_staging_database(name)


def test_create_staging_trung_ten_thi_fail_va_khong_thay_the():
    from app.warehouse.errors import BatchStateError
    from app.warehouse.staging import create_staging_database, drop_stale_staging

    name = f"wh_staging_dup{uuid.uuid4().hex[:8]}"
    create_staging_database(name)
    try:
        _root(f"CREATE TABLE `{name}`.marker (i INT)")
        with pytest.raises(BatchStateError, match="da ton tai"):
            create_staging_database(name)
        with warehouse_connection(name) as conn:
            cursor = conn.cursor()
            cursor.execute("SHOW TABLES LIKE 'marker'")
            assert cursor.fetchone() is not None, "staging cu bi thay the"
            cursor.close()
    finally:
        drop_stale_staging(name)


@pytest.mark.parametrize("attack", [
    "--\f comment\nCREATE DATABASE `{evil}`;\n",
    "CREATE /*!99999 harmless */ DATABASE `{evil}`;\n",
])
def test_phan_vi_du_gpt_08_bi_chan_o_preflight_va_khong_toi_server(tmp_path, tiny_dump, attack):
    """GPT 08 da tao duoc database THAT bang 2 cau nay qua ban cu. Gio phai dung o preflight.

    Gan vao dump mysqldump THAT (du DDL 4 core) de preflight chi con DUNG 1 ly do la chinh cau tan cong.
    Gan vao dump thieu DDL thi test van PASS nho MISSING_TABLE_DDL ma khong chung minh gi ve cau tan cong.
    """
    from app.warehouse.errors import SqlScriptError
    from app.warehouse.staging import staging_database

    evil = f"wh_staging_evil{uuid.uuid4().hex[:8]}"
    dump = tmp_path / "attack.sql"
    dump.write_bytes(tiny_dump.read_bytes() + attack.format(evil=evil).encode("utf-8"))
    try:
        with pytest.raises(SqlScriptError, match="FAIL: 1 statement") as caught:
            with staging_database(f"t{uuid.uuid4().hex[:6]}", "vps", dump):
                pass
        assert "CREATE DATABASE" in str(caught.value)
        assert not _database_exists_raw(evil)
    finally:
        _root(f"DROP DATABASE IF EXISTS `{evil}`")


def test_user_tam_bi_server_chan_ke_ca_khi_preflight_bi_vuot_qua():
    """Gia lap lexer co lo hong: goi thang lop restore, bo qua preflight. Server van phai tu choi."""
    from app.warehouse.errors import WarehouseError
    from app.warehouse.staging import (
        _client_defaults_file, _run_mysql_client, create_staging_database, drop_stale_staging,
        restricted_restore_user,
    )

    tag = uuid.uuid4().hex[:8]
    staging_name, canary, evil = f"wh_staging_r{tag}", f"wh_staging_canary{tag}", f"wh_staging_evil{tag}"
    create_staging_database(staging_name)
    create_staging_database(canary)
    try:
        _root(f"CREATE TABLE `{canary}`.t (i INT)")
        with restricted_restore_user(staging_name) as (user, password):
            with _client_defaults_file(user, password) as cnf:
                for sql in (f"DROP DATABASE `{canary}`;", f"DROP TABLE `{canary}`.t;",
                            f"CREATE DATABASE `{evil}`;", "USE hotel_price_intel;",
                            "SELECT COUNT(*) FROM hotel_price_intel.hotels;"):
                    with pytest.raises(WarehouseError, match="denied|Access"):
                        _run_mysql_client(cnf, staging_name, sql=sql)
        assert _database_exists_raw(canary) and not _database_exists_raw(evil)
    finally:
        drop_stale_staging(staging_name)
        drop_stale_staging(canary)
        _root(f"DROP DATABASE IF EXISTS `{evil}`")


def test_khong_sot_user_tam_sau_cac_test():
    with warehouse_connection(None, verify=False) as conn:
        cursor = conn.cursor()
        cursor.execute("SELECT user FROM mysql.user WHERE user LIKE 'whr\\_%'")
        assert cursor.fetchall() == []
        cursor.close()


def test_file_defaults_extra_bi_xoa_sau_khi_dung():
    import os as _os

    from app.warehouse.staging import _client_defaults_file

    with _client_defaults_file("u", "p") as path:
        assert _os.path.exists(path)
    assert not _os.path.exists(path)

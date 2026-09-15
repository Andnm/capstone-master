"""Tao warehouse database versioned (muc 3a buoc 5-8) - phan viec cua `init_warehouse_db`.

Buoc 5: tao DB, ten qua whitelist. Buoc 6: sanitize + chay `setup.sql` theo tung statement.
Buoc 7: verify `SELECT DATABASE()`. Buoc 8: tao 11 bang ETL-only.

Tach khoi `build_warehouse` de 2 lenh co ranh gioi ro: `init_warehouse_db` tao vo rong;
`build_warehouse` chi duoc chay tren vo rong do va FAIL neu core/map/batch da co du lieu
(yeu cau GPT file 02: 1 batch / 1 warehouse database, khong append batch thu hai).
"""
from __future__ import annotations

from pathlib import Path

from .connection import operational_database_name, verify_session, warehouse_connection
from .errors import BatchStateError, WarehouseError
from .etl_ddl import CORE_TABLES, ETL_TABLES, ETL_TABLE_NAMES
from .hashing import sha256_hex
from .naming import require_warehouse_database
from .sql_script import sanitize_setup_sql

SETUP_SQL_PATH = Path(__file__).resolve().parent.parent / "database" / "setup.sql"


def read_setup_sql(path: str | Path | None = None) -> tuple[str, str]:
    """Tra (noi dung, sha256) cua setup.sql. Hash tinh tren van ban GOC, truoc khi sanitize."""
    setup_path = Path(path) if path is not None else SETUP_SQL_PATH
    if not setup_path.exists():
        raise WarehouseError(f"khong tim thay setup.sql tai {setup_path}")
    text = setup_path.read_text(encoding="utf-8")
    return text, sha256_hex(text)


def _execute_and_drain(cursor, statement: str) -> None:
    """Chay 1 statement va DOC HET result set neu co.

    `setup.sql` ket thuc bang `SELECT '... created successfully!' AS message;`. Neu khong doc het,
    mysql-connector se nem `InternalError: Unread result found` ngay o `conn.commit()` ke tiep -
    bug nay do smoke test tren MySQL that bat duoc, test pure khong the thay.
    """
    cursor.execute(statement)
    if cursor.with_rows:
        cursor.fetchall()


def database_exists(name: str) -> bool:
    require_warehouse_database(name)
    with warehouse_connection(None, verify=False) as conn:
        cursor = conn.cursor()
        try:
            cursor.execute(
                "SELECT SCHEMA_NAME FROM information_schema.SCHEMATA WHERE SCHEMA_NAME=%s", (name,)
            )
            return cursor.fetchone() is not None
        finally:
            cursor.close()


def create_warehouse_database(name: str) -> None:
    """Buoc 5. Loi neu DB da ton tai (muc 18: 'Ten da ton tai -> loi')."""
    require_warehouse_database(name)
    if name == operational_database_name():
        raise BatchStateError(
            f"tu choi: {name!r} chinh la database van hanh cua may nay. Warehouse phai la DB rieng."
        )
    if database_exists(name):
        raise BatchStateError(
            f"warehouse database {name!r} da ton tai - `init_warehouse_db` khong ghi de. "
            f"Dung ten batch moi, hoac xoa DB cu thu cong neu chac chan khong con can."
        )
    with warehouse_connection(None, verify=False) as conn:
        cursor = conn.cursor()
        try:
            # Ten da qua whitelist ^[A-Za-z0-9_]+$ nen noi thang la an toan; DDL khong nhan placeholder.
            cursor.execute(
                f"CREATE DATABASE `{name}` CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci"
            )
            conn.commit()
        finally:
            cursor.close()


def apply_setup_sql(name: str, *, setup_path: str | Path | None = None) -> str:
    """Buoc 6-7. Tra ve `setup_sql_sha256` da ghi vao `etl_import_batches`."""
    require_warehouse_database(name)
    text, digest = read_setup_sql(setup_path)
    statements = sanitize_setup_sql(text)
    with warehouse_connection(name) as conn:
        verify_session(conn, expect_database=name)  # buoc 7, lam lai sau khi da chon DB
        cursor = conn.cursor()
        try:
            for statement in statements:
                _execute_and_drain(cursor, statement)
            conn.commit()
        except Exception:
            conn.rollback()
            raise
        finally:
            cursor.close()
    return digest


def create_etl_tables(name: str) -> tuple[str, ...]:
    """Buoc 8: tao 11 bang ETL/dataset. Tra ve danh sach bang da tao."""
    require_warehouse_database(name)
    with warehouse_connection(name) as conn:
        cursor = conn.cursor()
        try:
            for _table, ddl in ETL_TABLES:
                _execute_and_drain(cursor, ddl)
            conn.commit()
        except Exception:
            conn.rollback()
            raise
        finally:
            cursor.close()
    return ETL_TABLE_NAMES


def list_tables(name: str) -> set[str]:
    require_warehouse_database(name)
    with warehouse_connection(name) as conn:
        cursor = conn.cursor()
        try:
            cursor.execute(
                "SELECT TABLE_NAME FROM information_schema.TABLES WHERE TABLE_SCHEMA=%s", (name,)
            )
            return {row[0] for row in cursor.fetchall()}
        finally:
            cursor.close()


def assert_ready_for_build(name: str) -> None:
    """Warehouse phai co du bang VA core/ETL phai rong truoc khi `build_warehouse` chay.

    GPT file 02: "`build_warehouse` phai fail neu core/map/batch khong rong; khong dung cung
    database de append batch thu hai."
    """
    require_warehouse_database(name)
    tables = list_tables(name)
    missing = [table for table in (*CORE_TABLES, *ETL_TABLE_NAMES) if table not in tables]
    if missing:
        raise BatchStateError(
            f"warehouse database {name!r} thieu bang {missing} - chay `init_warehouse_db` truoc."
        )
    non_empty: list[str] = []
    with warehouse_connection(name) as conn:
        cursor = conn.cursor()
        try:
            for table in (*CORE_TABLES, *ETL_TABLE_NAMES):
                cursor.execute(f"SELECT 1 FROM `{table}` LIMIT 1")
                if cursor.fetchone() is not None:
                    non_empty.append(table)
        finally:
            cursor.close()
    if non_empty:
        raise BatchStateError(
            f"warehouse database {name!r} KHONG rong - cac bang da co du lieu: {non_empty}. "
            f"Mot warehouse database chi chua dung 1 batch. Tao DB moi cho batch moi."
        )

"""Ket noi + tien ich SQL nho cho dataset builder. Tai dung `warehouse_connection` cua backend (pin UTC,
autocommit=False, verify SELECT DATABASE()) - khong tu mo connection rieng.

Builder GHI vao warehouse (chi `dataset_build_manifests`/`ml_*`), khac EDA (READ ONLY). Guard ten DB:
chi nhan database co prefix `warehouse_` (da qua whitelist cua backend) va KHONG phai DB van hanh.
"""
from __future__ import annotations

import datetime as dt
from contextlib import contextmanager
from typing import Any, Iterator, Sequence

from . import env  # noqa: F401

from app.warehouse.connection import operational_database_name, warehouse_connection  # noqa: E402
from app.warehouse.naming import require_warehouse_database  # noqa: E402

# Truy van nang that bai som thay vi treo ca may (bai hoc GROUP BY/CHAR(64)): 0 = khong gioi han.
DEFAULT_MAX_EXECUTION_MS = 0


def utc_now() -> dt.datetime:
    return dt.datetime.now(dt.timezone.utc).replace(tzinfo=None, microsecond=0)


def require_target_database(database: str) -> str:
    """Chan viec lo tay tro builder vao DB van hanh hoac DB khong phai warehouse."""
    require_warehouse_database(database)
    if database == operational_database_name():
        raise ValueError(f"{database!r} la DB van hanh - builder khong duoc ghi vao day.")
    return database


@contextmanager
def connect(database: str) -> Iterator[Any]:
    require_target_database(database)
    with warehouse_connection(database) as conn:
        yield conn


def _bind(params: Any) -> Any:
    """Dict -> tham so dat ten (%(ten)s); con lai -> tuple vi tri."""
    return params if isinstance(params, dict) else tuple(params)


def scalar(conn, sql: str, params: Sequence[Any] | dict[str, Any] = ()) -> Any:
    cursor = conn.cursor()
    try:
        cursor.execute(sql, _bind(params))
        row = cursor.fetchone()
        return None if row is None else row[0]
    finally:
        cursor.close()


def fetch_all(conn, sql: str, params: Sequence[Any] | dict[str, Any] = (), *, dictionary: bool = True) -> list[Any]:
    cursor = conn.cursor(dictionary=dictionary)
    try:
        cursor.execute(sql, _bind(params))
        return cursor.fetchall()
    finally:
        cursor.close()


def execute(conn, sql: str, params: Sequence[Any] | dict[str, Any] = ()) -> int:
    cursor = conn.cursor()
    try:
        cursor.execute(sql, _bind(params))
        return cursor.rowcount
    finally:
        cursor.close()


def executemany(conn, sql: str, rows: Sequence[Sequence[Any]], *, chunk: int = 5000) -> int:
    total = 0
    cursor = conn.cursor()
    try:
        for start in range(0, len(rows), chunk):
            part = rows[start:start + chunk]
            cursor.executemany(sql, part)
            total += len(part)
    finally:
        cursor.close()
    return total


def current_database(conn) -> str:
    return scalar(conn, "SELECT DATABASE()")

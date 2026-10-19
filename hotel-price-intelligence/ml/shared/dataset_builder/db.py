"""Ket noi + tien ich SQL nho cho dataset builder. Tai dung `warehouse_connection` cua backend (pin UTC,
autocommit=False, verify SELECT DATABASE()) - khong tu mo connection rieng.

Builder GHI vao warehouse (chi `dataset_build_manifests`/`ml_*`), khac EDA (READ ONLY). Guard ten DB:
chi nhan database co prefix `warehouse_` (da qua whitelist cua backend) va KHONG phai DB van hanh.
"""
from __future__ import annotations

import datetime as dt
import os
import re
from contextlib import contextmanager
from typing import Any, Iterator, Sequence

from . import env  # noqa: F401

from app.warehouse.connection import operational_database_name, warehouse_connection  # noqa: E402
from app.warehouse.naming import require_warehouse_database  # noqa: E402

# Truy van SELECT nang that bai som thay vi treo ca may (bai hoc GROUP BY/CHAR(64)): mac dinh 30 phut, 0 = khong gioi han.
# Override: bien moi truong ML_SELECT_TIMEOUT_MS hoac `--max-select-seconds` cua build_dataset.py.
DEFAULT_MAX_EXECUTION_MS = 30 * 60 * 1000
_max_execution_ms = int(os.environ.get("ML_SELECT_TIMEOUT_MS", DEFAULT_MAX_EXECUTION_MS))


def utc_now() -> dt.datetime:
    return dt.datetime.now(dt.timezone.utc).replace(tzinfo=None, microsecond=0)


def require_target_database(database: str) -> str:
    """Chan viec lo tay tro builder vao DB van hanh hoac DB khong phai warehouse."""
    require_warehouse_database(database)
    if database == operational_database_name():
        raise ValueError(f"{database!r} la DB van hanh - builder khong duoc ghi vao day.")
    return database


def max_execution_ms() -> int:
    return _max_execution_ms


def set_max_execution_ms(value: int) -> None:
    """Dat tran thoi gian cho MOI cau SELECT cua cac ket noi mo sau do (0 = khong gioi han, chi dung co chu y cho official/dai han)."""
    global _max_execution_ms
    if int(value) < 0:
        raise ValueError("max_execution_ms phai >= 0")
    _max_execution_ms = int(value)


@contextmanager
def connect(database: str) -> Iterator[Any]:
    require_target_database(database)
    with warehouse_connection(database) as conn:
        if _max_execution_ms > 0:
            # MySQL `max_execution_time` CHI ap dung cho SELECT (khong cat cau ghi): SELECT nang that bai som thay vi treo; buoc ghi dua vao
            # circuit-breaker/heartbeat cua runner (GPT review DB-m2).
            cursor = conn.cursor()
            try:
                cursor.execute(f"SET SESSION max_execution_time = {int(_max_execution_ms)}")
            finally:
                cursor.close()
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


_IDENT = re.compile(r"^[A-Za-z_][A-Za-z0-9_]{0,63}$")


class AnalyzeError(RuntimeError):
    pass


def analyze_tables(conn, tables: Sequence[str]) -> list[dict[str, str]]:
    """`ANALYZE TABLE` sau khi mot step da COMMIT du lieu lon (ANALYZE tu commit): thong ke index cua bang vua nap trong cung transaction co the con la cua
    bang rong => optimizer chon ke hoach full-join (rehearsal 06/10: UPDATE gan nhan tren ~148 nghin dong chay >30 phut). Chi tin identifier hop le.

    MySQL co the tra `Msg_type='error'` trong result set thay vi nem exception (GPT file 46): dong `error` => AnalyzeError (khong bao da refresh khi server noi that bai);
    `status`/`note`/`warning` duoc tra ve de ghi audit. Tra danh sach `{table, msg_type, msg_text}`."""
    messages: list[dict[str, str]] = []
    for table in tables:
        if not _IDENT.match(table):
            raise ValueError(f"ten bang khong hop le: {table!r}")
        cursor = conn.cursor(dictionary=True)
        try:
            cursor.execute(f"ANALYZE TABLE {table}")
            rows = cursor.fetchall()
        finally:
            cursor.close()
        for row in rows:
            msg_type, msg_text = str(row.get("Msg_type", "")), str(row.get("Msg_text", ""))
            messages.append({"table": table, "msg_type": msg_type, "msg_text": msg_text})
            if msg_type.lower() == "error":
                raise AnalyzeError(f"ANALYZE TABLE {table}: {msg_text}")
        if not rows:
            raise AnalyzeError(f"ANALYZE TABLE {table}: khong co ket qua tra ve")
    return messages


def current_database(conn) -> str:
    return scalar(conn, "SELECT DATABASE()")

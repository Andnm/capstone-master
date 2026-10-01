"""Connection cho ETL warehouse - KHONG dung pool cua app van hanh.

Khac biet co y voi `app/core/database.py`:
- Pool van hanh ghim cung `settings.DB_NAME`; ETL phai mo duoc database bat ky (warehouse versioned,
  staging tam) nen dung connect truc tiep, khong pool.
- Moi connection pin `time_zone='+00:00'` (muc 5) va `autocommit=False` (muc 3a buoc 9 doi
  transaction that su).

Muc 5 cung yeu cau "moi connection ETL pin time_zone='+00:00'" - `verify_session()` kiem tra lai
sau khi mo, khong chi tin tham so truyen vao.
"""
from __future__ import annotations

from contextlib import contextmanager
from typing import Any, Iterator, Optional

import mysql.connector

from app.core.config import settings

from .errors import WarehouseError
from .naming import require_identifier


def _connect_kwargs(database: Optional[str]) -> dict[str, Any]:
    kwargs: dict[str, Any] = {
        "host": settings.DB_HOST,
        "port": settings.DB_PORT,
        "user": settings.DB_USER,
        "password": settings.DB_PASSWORD,
        "time_zone": "+00:00",
        "autocommit": False,
        "charset": "utf8mb4",
        "use_unicode": True,
    }
    if database is not None:
        kwargs["database"] = require_identifier(database)
    return kwargs


def verify_session(conn, *, expect_database: Optional[str]) -> None:
    """Xac nhan session that su dang o dung database + dung timezone.

    Muc 3a buoc 7/10 doi "verify SELECT DATABASE()" sau khi tao/restore - khong duoc gia dinh rang
    tham so `database=` luc connect da co tac dung (vd bi USE o file SQL doi di noi khac).
    """
    cursor = conn.cursor()
    try:
        cursor.execute("SELECT DATABASE(), @@session.time_zone")
        row = cursor.fetchone()
    finally:
        cursor.close()
    current_db, tz = (row[0], row[1]) if row else (None, None)
    if expect_database is not None and current_db != expect_database:
        raise WarehouseError(
            f"session dang o database {current_db!r} chu khong phai {expect_database!r} - dung ngay, "
            f"khong ghi bat ky thu gi."
        )
    if tz != "+00:00":
        raise WarehouseError(
            f"session time_zone={tz!r}, khong phai '+00:00' (muc 5). Moi TIMESTAMP doc ra se lech gio."
        )


@contextmanager
def warehouse_connection(database: Optional[str], *, verify: bool = True) -> Iterator[Any]:
    """Mo connection toi `database` (None = khong chon database, dung cho CREATE/DROP DATABASE)."""
    conn = mysql.connector.connect(**_connect_kwargs(database))
    try:
        if verify:
            verify_session(conn, expect_database=database)
        yield conn
    finally:
        try:
            conn.close()
        except Exception:  # noqa: BLE001 - dong connection khong duoc che loi that phia tren
            pass


def operational_database_name() -> str:
    """Ten DB van hanh cua chinh may nay - dung lam guard 'khong duoc tro nham vao day'."""
    return settings.DB_NAME

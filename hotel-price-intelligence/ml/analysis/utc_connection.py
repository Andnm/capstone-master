"""Ket noi MySQL DOC-CHI voi session UTC DA XAC MINH (GPT file 60 C59-M2) cho script thu thap bang chung/provenance.

Cot TIMESTAMP tra ve theo `time_zone` cua session; session mac dinh co the la gio VN (+07:00) nen dat nhan `_utc` cho gia tri doc tu session mac dinh la SAI 7 gio
(da xay ra voi scan_identity cua policy N1). Moi script bang chung dung ham nay: `SET SESSION time_zone='+00:00'`, doc lai `@@session.time_zone` va FAIL neu khac,
`SET SESSION TRANSACTION READ ONLY`, `max_execution_time`. Khong dung `app.warehouse.connection` vi no chan ten database khong co tien to `warehouse_` (scan DB la DB van hanh).
"""
from __future__ import annotations

import sys
from pathlib import Path
from typing import Any

BACKEND = Path(__file__).resolve().parents[2] / "backend"


class SessionTimezoneError(RuntimeError):
    pass


def verify_utc_session(conn) -> None:
    cursor = conn.cursor()
    try:
        cursor.execute("SELECT @@session.time_zone")
        value = cursor.fetchone()[0]
    finally:
        cursor.close()
    if str(value) != "+00:00":
        raise SessionTimezoneError(f"session time_zone={value!r}, can '+00:00' - khong duoc ghi nhan TIMESTAMP la UTC.")


def connect_utc_readonly(database: str, *, max_execution_ms: int = 120_000):
    """Tra ve (dictionary cursor, connection); session UTC da xac minh + READ ONLY."""
    if str(BACKEND) not in sys.path:
        sys.path.insert(0, str(BACKEND))
    import mysql.connector
    from dotenv import load_dotenv

    load_dotenv(BACKEND / ".env", override=False)          # PHAI nap .env TRUOC khi import settings (Settings() doc env luc import)
    from app.core.config import settings
    conn = mysql.connector.connect(host=settings.DB_HOST, port=settings.DB_PORT, user=settings.DB_USER, password=settings.DB_PASSWORD, database=database,
                                   connection_timeout=10, autocommit=True)
    cursor = conn.cursor()
    try:
        cursor.execute("SET SESSION time_zone = '+00:00'")
        cursor.execute(f"SET SESSION max_execution_time = {int(max_execution_ms)}")
        cursor.execute("SET SESSION TRANSACTION READ ONLY")
    finally:
        cursor.close()
    verify_utc_session(conn)
    return conn.cursor(dictionary=True), conn

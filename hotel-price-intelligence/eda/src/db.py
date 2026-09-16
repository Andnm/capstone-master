"""Ket noi warehouse cho EDA - fail-closed, chi doc (EDA_CURATED_PLAN.md muc 5 quy tac 1).

Ranh gioi an toan THAT la `SET SESSION TRANSACTION READ ONLY` (server MySQL tu choi moi
INSERT/UPDATE/DELETE/DDL voi errno 1792), khong phai quy uoc code. Da tu tay verify tren chinh
`warehouse_20260916_2src` (roi rollback ngay) o vong thao luan ke hoach - xem
`discuss/eda-curated-implementation/04-claude-plan-response.md`.

**KHONG thu ghi len warehouse current de test** (quy tac 1 cua plan). Ham `verify_read_only_enforced()`
o day CHI duoc goi trong test tren disposable DB (`src/tests/test_db_read_only.py`), khong bao gio goi
tu `connect()` san xuat.

Tai su dung `app.warehouse.connection.warehouse_connection` cua backend (khong tu viet lai logic pin
`time_zone='+00:00'`/`autocommit=False`/`verify_session()`). Backend doc config qua bien MOI TRUONG chu
khong qua CWD-relative `.env` (notebook EDA co CWD khac `backend/`), nen o day nap `backend/.env` vao
`os.environ` (khong ghi de bien da co) TRUOC khi import bat ky module `app.*` nao - da verify hoat dong
dung tu CWD `eda/`.
"""
from __future__ import annotations

import decimal
import json
import sys
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterator

import mysql.connector
import pandas as pd

REPO_ROOT = Path(__file__).resolve().parents[3]  # .../CAPSTONE (eda/src/db.py -> len 3 cap)
BACKEND_DIR = REPO_ROOT / "hotel-price-intelligence" / "backend"
DEFAULT_POINTER_PATH = REPO_ROOT / "outputs" / "warehouse" / "warehouse_current.json"

READ_ONLY_ERRNO = 1792  # ER_CANT_EXECUTE_IN_READ_ONLY_TRANSACTION


class ReadOnlyEnforcementError(RuntimeError):
    """`SET SESSION TRANSACTION READ ONLY` khong co hieu luc nhu mong doi - dung ngay, khong query gi."""


class SnapshotVerificationError(RuntimeError):
    """Pointer/batch khong o trang thai an toan de doc (chua promote, batch khong PASS, sai database)."""


_backend_ready = False


def _ensure_backend_importable() -> None:
    """Idempotent: nap `backend/.env` + them `backend/` vao `sys.path` dung 1 lan cho ca process."""
    global _backend_ready
    if _backend_ready:
        return
    if str(BACKEND_DIR) not in sys.path:
        sys.path.insert(0, str(BACKEND_DIR))
    from dotenv import load_dotenv  # import cuc bo: chi can luc thuc su ket noi

    load_dotenv(BACKEND_DIR / ".env", override=False)
    _backend_ready = True


def _connect_raw(database: str):
    """Mo 1 connection MySQL da pin UTC/autocommit qua `warehouse_connection` cua backend. CHUA bat
    read-only - noi goi (`connect()` hoac test) chiu trach nhiem goi `enforce_read_only_session()`
    ngay sau do, TRUOC query dau tien."""
    _ensure_backend_importable()
    from app.warehouse.connection import warehouse_connection

    return warehouse_connection(database)


def enforce_read_only_session(conn) -> None:
    """Bat READ ONLY o cap SESSION - moi INSERT/UPDATE/DELETE/DDL sau day bi MySQL tu choi (errno
    1792) cho toi khi session dong. Goi dung 1 lan, ngay sau khi mo connection."""
    cursor = conn.cursor()
    try:
        cursor.execute("SET SESSION TRANSACTION READ ONLY")
    finally:
        cursor.close()


def verify_read_only_enforced(conn, *, probe_table: str = "hotels", probe_column: str = "city") -> None:
    """CHI dung trong test tren disposable DB - KHONG BAO GIO goi tren warehouse current (quy tac 1).

    Thu 1 UPDATE vo hai (`WHERE 1=0`, khong khop dong nao ke ca khi khong bi chan) va assert bi MySQL
    tu choi DUNG vi READ ONLY (errno 1792) - khong phai vi ly do khac (quyen, cu phap, bang khong ton
    tai), de khong bao PASS nham khi that ra co 1 loi khac che khuat that bai thuc su. Mac dinh nham
    `hotels.city` (co that trong schema warehouse); test tren DB toi gian co the truyen bang/cot khac.
    """
    cursor = conn.cursor()
    try:
        cursor.execute(f"UPDATE `{probe_table}` SET `{probe_column}` = `{probe_column}` WHERE 1 = 0")
    except mysql.connector.Error as exc:
        if exc.errno != READ_ONLY_ERRNO:
            raise ReadOnlyEnforcementError(
                f"UPDATE bi chan nhung KHONG phai do READ ONLY (errno={exc.errno}, ky vong "
                f"{READ_ONLY_ERRNO}): {exc}"
            ) from exc
        return
    finally:
        cursor.close()
    raise ReadOnlyEnforcementError(
        "SET SESSION TRANSACTION READ ONLY KHONG chan duoc UPDATE - ranh gioi an toan hong, dung ngay."
    )


@dataclass(frozen=True)
class WarehouseSnapshot:
    """Danh tinh warehouse dang doc, xac nhan lai tu DB (khong tin suong pointer)."""

    database: str
    batch_id: str
    source_manifest_sha256: str
    cohort_manifest_sha256: str | None
    ownership_manifest_sha256: str | None
    canonicalization_version: str
    canonicalization_git_commit: str
    batch_finished_at: str

    def to_manifest_dict(self) -> dict[str, Any]:
        """Phan snapshot cho `input_manifest.json` (muc 5 quy tac 3)."""
        return {
            "warehouse_database": self.database, "batch_id": self.batch_id,
            "source_manifest_sha256": self.source_manifest_sha256,
            "cohort_manifest_sha256": self.cohort_manifest_sha256,
            "ownership_manifest_sha256": self.ownership_manifest_sha256,
            "canonicalization_version": self.canonicalization_version,
            "canonicalization_git_commit": self.canonicalization_git_commit,
            "batch_finished_at": self.batch_finished_at,
        }


def load_pointer(pointer_path: Path = DEFAULT_POINTER_PATH) -> dict[str, Any]:
    if not pointer_path.exists():
        raise SnapshotVerificationError(
            f"khong tim thay {pointer_path} - chua co warehouse nao duoc promote, EDA khong the chay."
        )
    return json.loads(pointer_path.read_text(encoding="utf-8"))


def _verify_snapshot(conn, pointer: dict[str, Any]) -> WarehouseSnapshot:
    """Xac nhan lai TRONG DB (khong tin gia tri da luu san trong pointer): batch ton tai, status='pass',
    va source_manifest_sha256 tinh lai tu `etl_import_sources` hien tai khop dung gia tri da pin
    (muc 16 cua spec - chan truong hop registry bi sua sau khi batch da tao)."""
    from app.warehouse.registry import verify_source_manifest

    batch_id = pointer["batch_id"]
    cursor = conn.cursor(dictionary=True)
    try:
        cursor.execute("SELECT * FROM etl_import_batches WHERE batch_id=%s", (batch_id,))
        batch = cursor.fetchone()
    finally:
        cursor.close()
    if batch is None:
        raise SnapshotVerificationError(f"pointer tro batch {batch_id!r} nhung khong ton tai trong DB.")
    if batch["status"] != "pass":
        raise SnapshotVerificationError(f"batch {batch_id!r} co status={batch['status']!r} != 'pass'.")
    manifest_sha = verify_source_manifest(conn, batch_id)  # tu raise ValidationError neu lech
    return WarehouseSnapshot(
        database=pointer["warehouse_database"], batch_id=batch_id, source_manifest_sha256=manifest_sha,
        cohort_manifest_sha256=pointer.get("cohort_manifest_sha256"),
        ownership_manifest_sha256=pointer.get("ownership_manifest_sha256"),
        canonicalization_version=batch["canonicalization_version"],
        canonicalization_git_commit=batch["canonicalization_git_commit"],
        batch_finished_at=str(batch["finished_at"]),
    )


@contextmanager
def connect(*, pointer_path: Path = DEFAULT_POINTER_PATH) -> Iterator[tuple[Any, WarehouseSnapshot]]:
    """Mo 1 connection READ-ONLY (server-enforced) vao warehouse pointer tro toi, da xac nhan batch
    'pass' va manifest khop. Tra ve `(connection, snapshot)`. Luon rollback + close, ke ca khi loi.

    Dung nhu `with connect() as (conn, snapshot): ...` - KHONG mo connection nao khac ngoai ham nay
    (hoac `_connect_raw` cho test) trong toan bo `eda/`.

    LUU Y KY THUAT: PHAI dung `with _connect_raw(...) as conn:` (long true 1 context manager), KHONG
    duoc goi `.__enter__()` roi chi giu `conn` - `warehouse_connection` la generator-based context
    manager voi `try/finally: conn.close()`; neu khong con tham chieu nao toi chinh object context
    manager do, Python co the garbage-collect no bat cu luc nao va nem `GeneratorExit` vao generator
    dang tam dung o `yield`, kich hoat `finally` dong connection SOM NGOAI Y MUON (da gap loi nay khi
    tu viet, sua bang cach long `with` dung chuan thay vi tu quan ly vong doi).
    """
    pointer = load_pointer(pointer_path)
    with _connect_raw(pointer["warehouse_database"]) as conn:
        try:
            enforce_read_only_session(conn)
            snapshot = _verify_snapshot(conn, pointer)
            yield conn, snapshot
        finally:
            conn.rollback()


def _coerce_decimal_columns(df: pd.DataFrame) -> pd.DataFrame:
    """`mysql-connector-python` tra cot DECIMAL (`price_per_night`, `price_total`, `coverage`... va ca
    ket qua `SUM()` tren bieu thuc boolean) nhu `decimal.Decimal`, KHONG phai `float`. numpy/pandas
    khong tinh duoc voi Decimal tron voi float - vd `.quantile()` nem `TypeError: unsupported operand
    type(s) for *: 'decimal.Decimal' and 'float'` (da tu bat gap khi test `price_distribution_stats`
    tren du lieu that). EDA khong can precision tuyet doi cua Decimal (khong phai tien giao dich, la
    phan tich thong ke) nen ep ve `float64` ngay tai diem doc DUY NHAT, thay vi rai `float(x)` o tung
    noi goi rieng le."""
    for column in df.columns:
        if df[column].map(lambda value: isinstance(value, decimal.Decimal)).any():
            df[column] = df[column].astype("float64")
    return df


def read_sql(conn, sql: str, params: tuple = ()) -> pd.DataFrame:
    """Diem DOC DUY NHAT trong EDA: SQL -> DataFrame. Khong dung `pandas.read_sql`/mo cursor rieng le
    o noi khac trong `eda/`, de moi lan doc du lieu deu di qua cung 1 cho co the audit/test."""
    cursor = conn.cursor(dictionary=True)
    try:
        cursor.execute(sql, params)
        rows = cursor.fetchall()
        columns = [d[0] for d in cursor.description] if cursor.description else []
    finally:
        cursor.close()
    return _coerce_decimal_columns(pd.DataFrame(rows, columns=columns))

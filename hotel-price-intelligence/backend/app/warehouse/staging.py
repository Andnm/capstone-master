"""Staging tam: restore dump vao 1 database rieng/nguon, doc ra, roi DROP (muc 3a buoc 10 va 12).

RANH GIOI AN TOAN - 5 lop, moi lop tu dung duoc khi lop khac hong:
1. Preflight (`sql_script.preflight_dump`) chay BEN TRONG `restore_dump_into_staging`, khong phu thuoc
   caller nho goi. Loi hoac statement ngoai allowlist mysqldump -> FAIL truoc khi server nhan 1 byte.
2. Dump duoc pipe vao `mysql` bang mot user MySQL TAM chi co `ALL PRIVILEGES` tren dung 1 staging
   DB. Do that tren 8.0.45 (discuss file 09): server tu choi CREATE/DROP DATABASE khac, DROP/SELECT
   bang cua DB khac, USE, SET GLOBAL. Day moi la ranh gioi that - server kiem tra quyen bang chinh
   parser cua no, khong phu thuoc lexer cua ta hieu dung SQL hay khong.
3. `mysql --binary-mode`: DO THAT chan duoc `tee <file>` (thanh loi cu phap, khong file nao duoc
   ghi) va lenh system (`\\!` -> "Unknown command"). KHONG chan het: GPT do duoc `delimiter` van
   chay duoi `--binary-mode` (review 10, N1), va co the con lenh client khac. Nen lop nay chi giam be
   mat tan cong, KHONG phai ranh gioi; preflight FAIL moi `DELIMITER`, con user tam (lop 2) moi la
   ranh gioi an toan duy nhat duoc tin cay.
4. Khong `--force`: loi dau tien la dung ca restore.
5. `information_schema.SCHEMATA` truoc/sau restore phai giong het.

Ve DROP (GPT review 08 BLOCKER 2): `create_staging_database()` KHONG BAO GIO drop de "don truoc" - trung
ten thi FAIL (tranh 2 build cung (batch, source) xoa staging dang dung cua nhau). Moi DROP di qua
`require_droppable_staging()` (prefix + membership). Don staging sot tu lan build chet giua chung la
thao tac rieng, tuong minh: `drop_stale_staging(name)`.

Password: subprocess goi bang argument list (khong `shell=True`); password nam trong file
`--defaults-extra-file` dung 1 lan, dong handle truoc khi goi mysql (Windows), xoa trong `finally`,
khong bao gio log noi dung/duong dan. User tam co password ngau nhien, bi DROP trong `finally`.
"""
from __future__ import annotations

import os
import re
import secrets
import subprocess
import tempfile
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Iterator

import mysql.connector

from app.core.config import settings

from .bootstrap import read_setup_sql
from .connection import warehouse_connection
from .errors import BatchStateError, SqlScriptError, WarehouseError
from .naming import require_droppable_staging, require_identifier, require_staging_database, staging_database_name
from .etl_ddl import CORE_TABLES
from .sql_script import mysql_version_id, preflight_dump, sanitize_setup_sql

_ER_DB_CREATE_EXISTS = 1007
_HOST_RE = re.compile(r"^[A-Za-z0-9._%:-]+$")


@dataclass(frozen=True)
class StagingHandle:
    name: str
    preflight: dict


def _root_execute(sql: str, params: tuple = ()) -> list:
    with warehouse_connection(None, verify=False) as conn:
        cursor = conn.cursor()
        try:
            cursor.execute(sql, params)
            rows = cursor.fetchall() if cursor.with_rows else []
            conn.commit()
            return rows
        finally:
            cursor.close()


def server_version_id() -> int:
    return mysql_version_id(_root_execute("SELECT VERSION()")[0][0])


def list_databases() -> set[str]:
    return {row[0] for row in _root_execute("SELECT SCHEMA_NAME FROM information_schema.SCHEMATA")}


@contextmanager
def _client_defaults_file(user: str, password: str) -> Iterator[str]:
    fd, path = tempfile.mkstemp(prefix=".wh_client_", suffix=".cnf")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            handle.write(
                "[client]\n"
                f"host={settings.DB_HOST}\n"
                f"port={settings.DB_PORT}\n"
                f"user={user}\n"
                f"password={password}\n"
                "default-character-set=utf8mb4\n"
            )
        try:
            os.chmod(path, 0o600)
        except OSError:
            pass
        yield path
    finally:
        try:
            os.unlink(path)
        except OSError:
            pass


def _run_mysql_client(defaults_path: str, database: str, *, stdin_path: Path | None = None,
                      sql: str | None = None) -> None:
    require_staging_database(database)  # client chi bao gio duoc tro vao staging
    command = ["mysql", f"--defaults-extra-file={defaults_path}", "--binary-mode", "--batch", "--silent", database]
    try:
        if stdin_path is not None:
            with open(stdin_path, "rb") as handle:
                result = subprocess.run(command, stdin=handle, capture_output=True)
        else:
            result = subprocess.run(command, input=(sql or "").encode("utf-8"), capture_output=True)
    except FileNotFoundError as exc:
        raise WarehouseError("khong tim thay lenh `mysql` tren PATH - can MySQL client de restore.") from exc
    if result.returncode != 0:
        stderr = result.stderr.decode("utf-8", errors="replace").strip()
        raise WarehouseError(f"restore vao staging {database!r} that bai (exit={result.returncode}): {stderr[:2000]}")


@contextmanager
def restricted_restore_user(staging_name: str) -> Iterator[tuple[str, str]]:
    """User MySQL tam chi co quyen tren dung `staging_name`. DROP trong `finally`."""
    require_staging_database(staging_name)
    current = _root_execute("SELECT CURRENT_USER()")[0][0]
    host = current.rsplit("@", 1)[1]
    if not _HOST_RE.match(host):
        raise WarehouseError(f"host cua tai khoan ETL {host!r} khong hop le de tao user tam.")
    user = f"whr_{secrets.token_hex(6)}"
    password = secrets.token_urlsafe(24)
    # Trong GRANT o cap database, `_` la wildcard 1 ky tu -> phai escape de khong cap quyen rong hon.
    escaped = staging_name.replace("_", "\\_")
    _root_execute(f"CREATE USER '{user}'@'{host}' IDENTIFIED BY %s", (password,))
    try:
        _root_execute(f"GRANT ALL PRIVILEGES ON `{escaped}`.* TO '{user}'@'{host}'")
        yield user, password
    finally:
        _root_execute(f"DROP USER IF EXISTS '{user}'@'{host}'")


def create_staging_database(name: str) -> None:
    """Chi CREATE. Da ton tai -> FAIL, khong drop/replace (BLOCKER 2)."""
    require_staging_database(name)
    try:
        _root_execute(f"CREATE DATABASE `{name}` CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci")
    except mysql.connector.Error as exc:
        if getattr(exc, "errno", None) == _ER_DB_CREATE_EXISTS:
            raise BatchStateError(
                f"staging {name!r} da ton tai - co the sot lai tu mot build chet giua chung, hoac mot build "
                f"khac dang chay cung (batch, source). KHONG tu xoa. Neu chac chan la ban sot, chay "
                f"`drop_stale_staging({name!r})` tuong minh roi build lai."
            ) from exc
        raise


def drop_staging_database(name: str, *, allowed: set[str]) -> None:
    require_droppable_staging(name, allowed=allowed)
    _root_execute(f"DROP DATABASE IF EXISTS `{name}`")


def drop_stale_staging(name: str) -> None:
    """Thao tac don dep TUONG MINH do nguoi van hanh goi voi dung ten staging sot lai."""
    require_staging_database(name)
    drop_staging_database(name, allowed={name})


def restore_dump_into_staging(name: str, dump_path: str | Path, *,
                              setup_sql_path: str | Path | None = None) -> dict:
    """Preflight -> setup.sql -> dump, ca 2 buoc sau chay bang user tam gioi han quyen.

    setup.sql chay TRUOC vi dump chi co 4 bang core nhung `price_observations` co FK toi
    `hotel_reference_rooms` (khong nam trong dump). mysqldump tu phat `FOREIGN_KEY_CHECKS=0` va
    `DROP TABLE IF EXISTS` nen ghi de dung 4 bang core.
    """
    require_staging_database(name)
    dump = Path(dump_path)
    if not dump.exists():
        raise WarehouseError(f"khong tim thay dump {dump}")
    version = server_version_id()
    report = preflight_dump(dump, server_version=version, expected_tables=CORE_TABLES)
    if report["findings"]:
        sample = "; ".join(f"{label} @offset {offset}: {preview}" for label, offset, preview in report["findings"][:5])
        raise SqlScriptError(
            f"preflight dump {dump.name} FAIL: {len(report['findings'])} statement khong an toan/khong dung "
            f"dang mysqldump - KHONG restore. {sample}"
        )
    text, _digest = read_setup_sql(setup_sql_path)
    setup_statements = sanitize_setup_sql(text, server_version=version)

    before = list_databases()
    with restricted_restore_user(name) as (user, password):
        with _client_defaults_file(user, password) as defaults_path:
            _run_mysql_client(defaults_path, name, sql=";\n".join(setup_statements) + ";\n")
            after_setup = _staging_table_counts(name, skip=CORE_TABLES)
            _run_mysql_client(defaults_path, name, stdin_path=dump)
    after = list_databases()
    if before != after:
        raise WarehouseError(
            f"danh sach database DOI trong luc restore {dump.name}: them {sorted(after - before)}, "
            f"mat {sorted(before - after)} - dung ngay."
        )
    report["support_tables_checked"] = _verify_staging_effects(name, after_setup)
    return report


def _staging_table_counts(name: str, *, skip: tuple[str, ...] = ()) -> dict[str, int | None]:
    require_staging_database(name)
    rows = _root_execute(
        "SELECT TABLE_NAME FROM information_schema.TABLES WHERE TABLE_SCHEMA=%s", (name,)
    )
    counts: dict[str, int | None] = {}
    for (table,) in sorted(rows):
        require_identifier(table, what="table")
        counts[table] = None if table in skip else int(
            _root_execute(f"SELECT COUNT(*) FROM `{name}`.`{table}`")[0][0]
        )
    return counts


def _verify_staging_effects(name: str, after_setup: dict[str, int | None]) -> int:
    """GPT review 10 N2 - kiem theo HIEU UNG THAT, khong theo lexer.

    Sau khi dump chay xong: tap bang phai y het tap `setup.sql` da tao (dump khong duoc them/bot bang),
    va so dong cua moi bang KHONG phai core phai giu nguyen so voi ngay truoc dump (dump chi duoc ghi
    4 bang core). So voi truoc-dump chu khong ep = 0, de khong bao nham neu setup.sql co du lieu seed.
    Tra ve so bang ho tro da kiem.
    """
    after_dump = _staging_table_counts(name, skip=CORE_TABLES)
    if set(after_dump) != set(after_setup):
        raise WarehouseError(
            f"staging {name}: tap bang doi sau dump - them {sorted(set(after_dump) - set(after_setup))}, "
            f"mat {sorted(set(after_setup) - set(after_dump))}."
        )
    missing = [table for table in CORE_TABLES if table not in after_dump]
    if missing:
        raise WarehouseError(f"staging {name}: thieu bang core sau restore: {missing}")
    changed = sorted(table for table, count in after_setup.items() if after_dump[table] != count)
    if changed:
        raise WarehouseError(
            f"staging {name}: dump da ghi du lieu vao bang ngoai 4 core: {changed} - source contract vi pham."
        )
    return sum(1 for table in after_setup if table not in CORE_TABLES)


@contextmanager
def staging_database(batch_id: str, source_code: str, dump_path: str | Path, *,
                     setup_sql_path: str | Path | None = None) -> Iterator[StagingHandle]:
    """Tao staging (FAIL neu trung), restore, yield, DROP trong `finally` (buoc 12)."""
    name = staging_database_name(batch_id, source_code)
    create_staging_database(name)  # raise truoc `try` -> khong bao gio drop thu minh khong tao
    try:
        report = restore_dump_into_staging(name, dump_path, setup_sql_path=setup_sql_path)
        yield StagingHandle(name=name, preflight=report)
    finally:
        drop_staging_database(name, allowed={name})


def count_core_rows(name: str, tables: tuple[str, ...]) -> dict[str, int]:
    require_identifier(name, what="database")
    counts: dict[str, int] = {}
    with warehouse_connection(name) as conn:
        cursor = conn.cursor()
        try:
            for table in tables:
                require_identifier(table, what="table")
                cursor.execute(f"SELECT COUNT(*) FROM `{table}`")
                counts[table] = int(cursor.fetchone()[0])
        finally:
            cursor.close()
    return counts

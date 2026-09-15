"""Staging tam: 1 database/nguon, restore dump vao, doc ra, roi DROP (muc 3a buoc 10 va 12).

Bat bien an toan:
- Ten staging deterministic theo `(batch_id, source_code)` va bat buoc prefix `wh_staging_`.
- DROP di qua `require_droppable_staging()` - kiem tra CA prefix LAN membership trong tap staging do
  chinh build nay tao. Hai lop co y trung nhau de mot lop hong khong du de xoa nham database.
- `staging_database()` la context manager: DROP trong `finally`, ke ca khi restore/compare nem loi.
- Nguon la READ-ONLY: khong bao gio mo connection ghi toi DB van hanh; chi doc FILE dump.

Ve password (yeu cau GPT review 06): subprocess `mysql` duoc goi bang ARGUMENT LIST (khong
`shell=True`) va password di qua `--defaults-extra-file` tro toi 1 file tam dung-1-lan, KHONG bao
gio nam tren command line (ai cung doc duoc qua `ps`/Process Explorer). File tam duoc dong handle
truoc khi goi mysql (bat buoc tren Windows) va xoa trong `finally`; noi dung/duong dan cua no khong
bao gio duoc log.
"""
from __future__ import annotations

import os
import subprocess
import tempfile
from contextlib import contextmanager
from pathlib import Path
from typing import Iterator

from app.core.config import settings

from .bootstrap import read_setup_sql
from .connection import warehouse_connection
from .errors import WarehouseError
from .naming import require_droppable_staging, require_identifier, staging_database_name
from .sql_script import sanitize_setup_sql


@contextmanager
def _client_defaults_file() -> Iterator[str]:
    """File `--defaults-extra-file` dung 1 lan. Handle dong truoc khi yield (Windows can dieu do)."""
    fd, path = tempfile.mkstemp(prefix=".wh_client_", suffix=".cnf")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            handle.write(
                "[client]\n"
                f"host={settings.DB_HOST}\n"
                f"port={settings.DB_PORT}\n"
                f"user={settings.DB_USER}\n"
                f"password={settings.DB_PASSWORD}\n"
                "default-character-set=utf8mb4\n"
            )
        try:
            os.chmod(path, 0o600)  # best-effort; tren Windows ACL khac nhung file nam trong temp rieng
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
    """Goi `mysql` bang argument list. Khong `shell=True`, khong password tren command line."""
    require_identifier(database)
    command = [
        "mysql",
        f"--defaults-extra-file={defaults_path}",
        "--batch",
        "--silent",
        database,
    ]
    try:
        if stdin_path is not None:
            with open(stdin_path, "rb") as handle:
                result = subprocess.run(command, stdin=handle, capture_output=True)
        else:
            result = subprocess.run(command, input=(sql or "").encode("utf-8"), capture_output=True)
    except FileNotFoundError as exc:
        raise WarehouseError(
            "khong tim thay lenh `mysql` tren PATH - can MySQL client de restore dump vao staging."
        ) from exc
    if result.returncode != 0:
        stderr = result.stderr.decode("utf-8", errors="replace").strip()
        # KHONG in command (chua duong dan file defaults) - chi in stderr cua mysql.
        raise WarehouseError(
            f"restore/execute vao staging {database!r} that bai (exit={result.returncode}): "
            f"{stderr[:2000]}"
        )


def create_staging_database(name: str) -> None:
    require_identifier(name, what="staging database")
    with warehouse_connection(None, verify=False) as conn:
        cursor = conn.cursor()
        try:
            cursor.execute(f"DROP DATABASE IF EXISTS `{name}`")
            cursor.execute(
                f"CREATE DATABASE `{name}` CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci"
            )
            conn.commit()
        finally:
            cursor.close()


def drop_staging_database(name: str, *, allowed: set[str]) -> None:
    require_droppable_staging(name, allowed=allowed)
    with warehouse_connection(None, verify=False) as conn:
        cursor = conn.cursor()
        try:
            cursor.execute(f"DROP DATABASE IF EXISTS `{name}`")
            conn.commit()
        finally:
            cursor.close()


def restore_dump_into_staging(name: str, dump_path: str | Path, *,
                              setup_sql_path: str | Path | None = None) -> None:
    """Dung setup.sql TRUOC roi moi restore dump.

    Ly do bat buoc phai co setup.sql truoc: dump chi chua 4 bang core, nhung `price_observations`
    co FK toi `hotel_reference_rooms(id)` - bang KHONG nam trong dump. Restore dump vao database
    rong se fail o dung cho do. mysqldump tu phat `FOREIGN_KEY_CHECKS=0` va `DROP TABLE IF EXISTS`
    nen no ghi de dung 4 bang core, cac bang con lai giu nguyen tu setup.sql.
    """
    require_identifier(name, what="staging database")
    dump = Path(dump_path)
    if not dump.exists():
        raise WarehouseError(f"khong tim thay dump {dump}")
    text, _digest = read_setup_sql(setup_sql_path)
    statements = sanitize_setup_sql(text)
    with _client_defaults_file() as defaults_path:
        _run_mysql_client(defaults_path, name, sql=";\n".join(statements) + ";\n")
        _run_mysql_client(defaults_path, name, stdin_path=dump)


@contextmanager
def staging_database(batch_id: str, source_code: str, dump_path: str | Path, *,
                     setup_sql_path: str | Path | None = None) -> Iterator[str]:
    """Tao staging, restore, yield ten, DROP trong `finally` du thanh cong hay that bai (buoc 12)."""
    name = staging_database_name(batch_id, source_code)
    allowed = {name}
    create_staging_database(name)
    try:
        restore_dump_into_staging(name, dump_path, setup_sql_path=setup_sql_path)
        yield name
    finally:
        drop_staging_database(name, allowed=allowed)


def count_core_rows(name: str, tables: tuple[str, ...]) -> dict[str, int]:
    """Dem dong tung bang trong staging - dung de doi chieu voi `row_counts_at_dump_time`."""
    require_identifier(name, what="staging database")
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

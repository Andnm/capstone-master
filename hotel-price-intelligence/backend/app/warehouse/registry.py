"""Registry nguon cua batch (muc 3a buoc 9 va 16, muc 16 "verify on every consumption").

Buoc 9: MOT transaction - INSERT `etl_import_batches` (status='running', source_manifest_sha256)
roi INSERT toan bo `etl_import_sources`. Chi sau khi commit moi duoc ghi 3 bang map/rejection (chung
co composite FK toi registry).

Buoc 16 + moi lenh doc registry ve sau: TINH LAI `source_manifest_sha256` tu `etl_import_sources` HIEN
TAI (cung cong thuc buoc 9) va so voi gia tri da ghi - lech -> FAIL cung, khong tu sua hash.
"""
from __future__ import annotations

import datetime as dt
import json
from typing import Any

from .errors import ValidationError
from .hashing import source_manifest_sha256
from .source_manifest import SourceManifest


def materialize_batch(conn, *, batch_id: str, warehouse_database: str, manifest: SourceManifest,
                      started_at: dt.datetime, setup_sql_sha256: str, cohort_manifest_sha256: str,
                      ownership_manifest_sha256: str, etl_config_sha256: str,
                      canonicalization_version: str, canonicalization_git_commit: str,
                      canonicalization_config_sha256: str) -> str:
    digest = manifest.manifest_sha256
    cursor = conn.cursor()
    try:
        cursor.execute(
            """INSERT INTO etl_import_batches (batch_id, warehouse_database, status, started_at,
                 setup_sql_sha256, source_manifest_sha256, cohort_manifest_sha256, ownership_manifest_sha256,
                 etl_config_sha256, canonicalization_version, canonicalization_git_commit,
                 canonicalization_config_sha256)
               VALUES (%s,%s,'running',%s,%s,%s,%s,%s,%s,%s,%s,%s)""",
            (batch_id, warehouse_database, started_at, setup_sql_sha256, digest, cohort_manifest_sha256,
             ownership_manifest_sha256, etl_config_sha256, canonicalization_version,
             canonicalization_git_commit, canonicalization_config_sha256),
        )
        cursor.executemany(
            """INSERT INTO etl_import_sources (import_batch_id, source_code, source_priority, dump_path,
                 dump_sha256, dump_taken_at, schema_sha256, source_version_json)
               VALUES (%s,%s,%s,%s,%s,%s,%s,%s)""",
            [
                (batch_id, entry.source_code, entry.source_priority, str(entry.dump_path), entry.dump_sha256,
                 dt.datetime.fromisoformat(entry.dump_taken_at.replace("Z", "")), entry.schema_sha256,
                 json.dumps(entry.source_version_json, ensure_ascii=False, sort_keys=True))
                for entry in manifest.sources
            ],
        )
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        cursor.close()
    return digest


def recompute_source_manifest_sha256(conn, batch_id: str) -> tuple[str, list[dict[str, Any]]]:
    cursor = conn.cursor(dictionary=True)
    try:
        cursor.execute(
            """SELECT source_code, source_priority, dump_sha256, dump_taken_at, schema_sha256, source_version_json
               FROM etl_import_sources WHERE import_batch_id=%s""",
            (batch_id,),
        )
        rows = cursor.fetchall()
    finally:
        cursor.close()
    if not rows:
        raise ValidationError(f"batch {batch_id!r} khong co dong etl_import_sources nao.")
    entries = []
    for row in rows:
        version = row["source_version_json"]
        entries.append({**row, "source_version_json": json.loads(version) if isinstance(version, str) else version})
    return source_manifest_sha256(entries), entries


def verify_source_manifest(conn, batch_id: str) -> str:
    """Muc 16: FAIL cung neu registry bi sua/them/xoa sau khi batch duoc tao."""
    cursor = conn.cursor(dictionary=True)
    try:
        cursor.execute("SELECT source_manifest_sha256 FROM etl_import_batches WHERE batch_id=%s", (batch_id,))
        row = cursor.fetchone()
    finally:
        cursor.close()
    if row is None:
        raise ValidationError(f"khong tim thay batch {batch_id!r}.")
    actual, _entries = recompute_source_manifest_sha256(conn, batch_id)
    if actual != row["source_manifest_sha256"]:
        raise ValidationError(
            f"source_manifest_sha256 LECH: da ghi {row['source_manifest_sha256']} nhung tinh lai tu "
            f"etl_import_sources hien tai ra {actual} - registry bi tac dong ngoai quy trinh. Dung."
        )
    return actual


def set_batch_status(conn, batch_id: str, status: str, *, finished_at: dt.datetime | None = None,
                     fail_reason: str | None = None, notes: str | None = None) -> None:
    cursor = conn.cursor()
    try:
        cursor.execute(
            "UPDATE etl_import_batches SET status=%s, finished_at=%s, fail_reason=%s, notes=COALESCE(%s, notes) "
            "WHERE batch_id=%s",
            (status, finished_at, fail_reason, notes, batch_id),
        )
        if cursor.rowcount != 1:
            raise ValidationError(f"cap nhat status batch {batch_id!r} anh huong {cursor.rowcount} dong.")
        conn.commit()
    finally:
        cursor.close()

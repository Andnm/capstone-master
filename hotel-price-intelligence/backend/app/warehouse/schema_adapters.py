"""Adapter staging HEP cho nguon co schema cu hon `setup.sql` (discuss/warehouse-rebuild-3src-20261004, GPT 02 D3).

Boi canh: `local_aux` chay code/schema cu hon, thieu cot `crawl_run_items.dead_link_confirmation JSON NULL` (migration
`20260903_dead_link_confirmation.sql` chua ap dung tren may do). `schema_fingerprint.compare_table()` coi thieu cot la FATAL,
nen build 3 nguon se dung o schema gate. Cot nay chi la evidence probe dead-link (NULL voi moi item cu); khong anh huong gia/key.

Nguyen tac (fail-closed, KHONG co co `allow-missing-column` tong quat):
1. Chi kich hoat cho dung `(source_code, raw_schema_sha256)` da dang ky - hash la sha256 canonical cua DDL tho cua dump
   (`compute_schema_sha256_from_dump`), duoc `build_warehouse` re-verify tu dump that truoc khi toi day. Hash la -> khong adapter ->
   schema gate FAIL nhu cu.
2. Adapter chay SAU khi restore vao staging EPHEMERAL (khong sua dump, khong sua DB nguon, khong doi raw schema hash trong registry)
   va TRUOC `compare_databases`: cot phai THUC SU vang va cot `AFTER` phai ton tai; neu khong -> FAIL.
3. Them cot dung dinh nghia canonical (`JSON NULL DEFAULT NULL`, ngay sau `reference_match_status` -> cung ordinal voi setup.sql);
   xac nhan moi dong o cot moi la NULL.
4. `build_warehouse` van chay FULL logical schema comparison sau do: bat ky khac biet con lai ngoai cosmetic deu FATAL.
5. Ket qua (adapter_id, raw hash, so dong, so NULL, cot truoc/sau) duoc ghi vao build report va `etl_import_batches.notes`.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Iterable

from .connection import warehouse_connection
from .errors import WarehouseError
from .naming import require_identifier, require_staging_database


class SchemaAdapterError(WarehouseError):
    pass


@dataclass(frozen=True)
class StagingAdapter:
    adapter_id: str
    source_code: str
    raw_schema_sha256: str
    table: str
    column: str
    column_ddl: str          # phan dinh nghia cot sau ten cot, dung y canonical trong setup.sql
    after_column: str        # cot dung truoc -> quyet dinh ordinal_position


# local_aux dump 2026-10-04 14:22 UTC (canonical raw schema hash tinh bang `compute_schema_sha256_from_dump`; GPT 02 §1.1).
AUX_MISSING_DEAD_LINK_CONFIRMATION_V1 = StagingAdapter(
    adapter_id="aux-missing-dead-link-confirmation-v1",
    source_code="local_aux",
    raw_schema_sha256="5155ba4e5b4f7375d5bd4f9bd4ceb23b258990c445a6365c8635d01ac29d8037",
    table="crawl_run_items",
    column="dead_link_confirmation",
    column_ddl="JSON NULL DEFAULT NULL",
    after_column="reference_match_status",
)

REGISTERED_ADAPTERS: tuple[StagingAdapter, ...] = (AUX_MISSING_DEAD_LINK_CONFIRMATION_V1,)


def find_adapter(source_code: str, raw_schema_sha256: str, registry: Iterable[StagingAdapter] | None = None) -> StagingAdapter | None:
    """Khop CHINH XAC ca source_code lan raw schema hash; khong khop mot trong hai -> None."""
    for adapter in (REGISTERED_ADAPTERS if registry is None else registry):
        if adapter.source_code == source_code and adapter.raw_schema_sha256 == raw_schema_sha256:
            return adapter
    return None


def _columns(cursor, database: str, table: str) -> list[str]:
    cursor.execute("SELECT COLUMN_NAME FROM information_schema.COLUMNS WHERE TABLE_SCHEMA=%s AND TABLE_NAME=%s ORDER BY ORDINAL_POSITION",
                   (database, table))
    return [row[0] for row in cursor.fetchall()]


def apply_staging_adapter(staging_name: str, *, source_code: str, raw_schema_sha256: str,
                          registry: Iterable[StagingAdapter] | None = None) -> dict[str, Any] | None:
    """Ap adapter (neu co) len staging da restore. Tra ve report de ghi vao build report; None neu khong co adapter cho nguon nay."""
    adapter = find_adapter(source_code, raw_schema_sha256, registry)
    if adapter is None:
        return None
    require_staging_database(staging_name)
    for identifier, what in ((adapter.table, "table"), (adapter.column, "column"), (adapter.after_column, "column")):
        require_identifier(identifier, what=what)
    with warehouse_connection(staging_name) as conn:
        cursor = conn.cursor()
        try:
            before = _columns(cursor, staging_name, adapter.table)
            if adapter.column in before:
                raise SchemaAdapterError(
                    f"adapter {adapter.adapter_id}: cot {adapter.table}.{adapter.column} DA TON TAI trong staging {staging_name} - "
                    f"trang thai khong dung nhu da dang ky (raw schema hash {adapter.raw_schema_sha256[:12]}...), dung ngay.")
            if adapter.after_column not in before:
                raise SchemaAdapterError(
                    f"adapter {adapter.adapter_id}: thieu cot dinh vi {adapter.table}.{adapter.after_column} - khong them cot.")
            cursor.execute(f"ALTER TABLE `{adapter.table}` ADD COLUMN `{adapter.column}` {adapter.column_ddl} AFTER `{adapter.after_column}`")
            conn.commit()
            after = _columns(cursor, staging_name, adapter.table)
            cursor.execute(f"SELECT COUNT(*), SUM(`{adapter.column}` IS NOT NULL) FROM `{adapter.table}`")
            total, non_null = cursor.fetchone()
            total, non_null = int(total or 0), int(non_null or 0)
            conn.commit()
        finally:
            cursor.close()
    expected_after = before[: before.index(adapter.after_column) + 1] + [adapter.column] + before[before.index(adapter.after_column) + 1:]
    if after != expected_after:
        raise SchemaAdapterError(f"adapter {adapter.adapter_id}: thu tu cot sau ALTER khong nhu mong doi: {after} != {expected_after}")
    if non_null != 0:
        raise SchemaAdapterError(f"adapter {adapter.adapter_id}: cot moi co {non_null} dong khac NULL (mong doi 0).")
    return {
        "adapter_id": adapter.adapter_id, "source_code": source_code, "raw_schema_sha256": raw_schema_sha256,
        "table": adapter.table, "column": adapter.column, "column_ddl": adapter.column_ddl, "after_column": adapter.after_column,
        "rows": total, "null_rows": total - non_null, "non_null_rows": non_null,
        "columns_before": len(before), "columns_after": len(after),
        "semantics": "structural missingness: NULL = khong co evidence/chua do tren nguon nay, KHONG nghia la 'khong dead link'",
    }

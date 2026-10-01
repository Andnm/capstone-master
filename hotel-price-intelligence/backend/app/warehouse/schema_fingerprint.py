"""So sanh schema NGUON voi `setup.sql` theo CAU TRUC LOGIC, khong theo van ban (muc 5).

Vi sao khong so van ban/khong so `DATA_TYPE`:
- Van ban `SHOW CREATE TABLE` nhung nhau o ten constraint. Thuc te giua 2 nguon that:
  `fk_runs_retry_of`/`fk_po_item`/`fk_po_reference` (local, do migration 20260808 doi ten) vs
  `crawl_runs_ibfk_1`/`price_observations_ibfk_3`/`_ibfk_4` (VPS, ten MySQL tu sinh vi `setup.sql`
  baseline chua duoc cap nhat theo migration do). Cau truc GIONG HET, chi ten khac.
- `DATA_TYPE='varchar'` KHONG phan biet `VARCHAR(20)` voi `VARCHAR(500)`; tuong tu DECIMAL
  precision/scale va tap gia tri ENUM. Vi vay phai dung `COLUMN_TYPE` (yeu cau GPT file 02 D4).

Nguyen tac: khac ten constraint/index -> NOTE (cosmetic). Khac cot/kieu/nullable/default/extra/
generation/charset/collation, khac cau truc key, khac FK, khac CHECK, khac engine/collation bang
-> FAIL. KHONG co co `--allow-missing-column`: hai nguon that da biet la tuong thich; mo duong
waiver ad-hoc se de importer am tham tao payload khuyet (GPT file 02 D4).
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any, Iterable, Sequence

from .errors import SchemaMismatchError

_WHITESPACE_RE = re.compile(r"\s+")


def _normalize_expression(value: str | None) -> str | None:
    """Gom khoang trang + bo backtick de so CHECK/generation expression giua 2 server khong lech vat."""
    if value is None:
        return None
    text = _WHITESPACE_RE.sub(" ", str(value)).strip()
    return text.replace("`", "") or None


@dataclass(frozen=True)
class ColumnFingerprint:
    ordinal_position: int
    column_name: str
    column_type: str
    is_nullable: str
    column_default: str | None
    extra: str
    generation_expression: str | None
    character_set_name: str | None
    collation_name: str | None


@dataclass(frozen=True)
class KeyFingerprint:
    """Key/index KHONG kem ten - ten di vao `cosmetic_names` de bao cao rieng."""
    is_unique: bool
    is_primary: bool
    index_type: str
    columns: tuple[tuple[str, int | None], ...]  # (column_name, sub_part)


@dataclass(frozen=True)
class ForeignKeyFingerprint:
    columns: tuple[str, ...]
    referenced_table: str
    referenced_columns: tuple[str, ...]
    update_rule: str
    delete_rule: str


@dataclass(frozen=True)
class TableFingerprint:
    table_name: str
    engine: str
    table_collation: str | None
    columns: tuple[ColumnFingerprint, ...]
    keys: frozenset[KeyFingerprint]
    foreign_keys: frozenset[ForeignKeyFingerprint]
    checks: frozenset[str]
    cosmetic_names: dict[str, tuple[str, ...]] = field(default_factory=dict, compare=False)


@dataclass(frozen=True)
class SchemaComparison:
    fatal: tuple[str, ...]
    notes: tuple[str, ...]

    @property
    def ok(self) -> bool:
        return not self.fatal


def read_table_fingerprint(cursor, database: str, table: str) -> TableFingerprint:
    """Doc `information_schema` cua `database`.`table` -> fingerprint cau truc."""
    cursor.execute(
        "SELECT ENGINE, TABLE_COLLATION FROM information_schema.TABLES "
        "WHERE TABLE_SCHEMA=%s AND TABLE_NAME=%s",
        (database, table),
    )
    table_row = cursor.fetchone()
    if not table_row:
        raise SchemaMismatchError(f"khong tim thay bang {database}.{table} trong information_schema.")

    cursor.execute(
        "SELECT ORDINAL_POSITION, COLUMN_NAME, COLUMN_TYPE, IS_NULLABLE, COLUMN_DEFAULT, EXTRA, "
        "       GENERATION_EXPRESSION, CHARACTER_SET_NAME, COLLATION_NAME "
        "FROM information_schema.COLUMNS WHERE TABLE_SCHEMA=%s AND TABLE_NAME=%s "
        "ORDER BY ORDINAL_POSITION",
        (database, table),
    )
    columns = tuple(
        ColumnFingerprint(
            ordinal_position=int(row["ORDINAL_POSITION"]),
            column_name=row["COLUMN_NAME"],
            column_type=_normalize_expression(row["COLUMN_TYPE"]) or "",
            is_nullable=row["IS_NULLABLE"],
            column_default=_normalize_expression(row["COLUMN_DEFAULT"]),
            extra=_normalize_expression(row["EXTRA"]) or "",
            generation_expression=_normalize_expression(row["GENERATION_EXPRESSION"]),
            character_set_name=row["CHARACTER_SET_NAME"],
            collation_name=row["COLLATION_NAME"],
        )
        for row in cursor.fetchall()
    )

    cursor.execute(
        "SELECT INDEX_NAME, NON_UNIQUE, SEQ_IN_INDEX, COLUMN_NAME, SUB_PART, INDEX_TYPE "
        "FROM information_schema.STATISTICS WHERE TABLE_SCHEMA=%s AND TABLE_NAME=%s "
        "ORDER BY INDEX_NAME, SEQ_IN_INDEX",
        (database, table),
    )
    grouped: dict[str, list[dict[str, Any]]] = {}
    for row in cursor.fetchall():
        grouped.setdefault(row["INDEX_NAME"], []).append(row)
    keys: set[KeyFingerprint] = set()
    key_names: list[str] = []
    for index_name, rows in grouped.items():
        key_names.append(index_name)
        keys.add(KeyFingerprint(
            is_unique=not int(rows[0]["NON_UNIQUE"]),
            is_primary=index_name == "PRIMARY",
            index_type=rows[0]["INDEX_TYPE"],
            columns=tuple(
                (row["COLUMN_NAME"], int(row["SUB_PART"]) if row["SUB_PART"] is not None else None)
                for row in rows
            ),
        ))

    cursor.execute(
        "SELECT rc.CONSTRAINT_NAME, rc.UPDATE_RULE, rc.DELETE_RULE, rc.REFERENCED_TABLE_NAME, "
        "       kcu.COLUMN_NAME, kcu.REFERENCED_COLUMN_NAME, kcu.ORDINAL_POSITION "
        "FROM information_schema.REFERENTIAL_CONSTRAINTS rc "
        "JOIN information_schema.KEY_COLUMN_USAGE kcu "
        "  ON kcu.CONSTRAINT_SCHEMA = rc.CONSTRAINT_SCHEMA "
        " AND kcu.CONSTRAINT_NAME = rc.CONSTRAINT_NAME AND kcu.TABLE_NAME = rc.TABLE_NAME "
        "WHERE rc.CONSTRAINT_SCHEMA=%s AND rc.TABLE_NAME=%s "
        "ORDER BY rc.CONSTRAINT_NAME, kcu.ORDINAL_POSITION",
        (database, table),
    )
    fk_groups: dict[str, list[dict[str, Any]]] = {}
    for row in cursor.fetchall():
        fk_groups.setdefault(row["CONSTRAINT_NAME"], []).append(row)
    foreign_keys: set[ForeignKeyFingerprint] = set()
    fk_names: list[str] = []
    for constraint_name, rows in fk_groups.items():
        fk_names.append(constraint_name)
        foreign_keys.add(ForeignKeyFingerprint(
            columns=tuple(row["COLUMN_NAME"] for row in rows),
            referenced_table=rows[0]["REFERENCED_TABLE_NAME"],
            referenced_columns=tuple(row["REFERENCED_COLUMN_NAME"] for row in rows),
            update_rule=rows[0]["UPDATE_RULE"],
            delete_rule=rows[0]["DELETE_RULE"],
        ))

    checks: set[str] = set()
    check_names: list[str] = []
    cursor.execute(
        "SELECT cc.CONSTRAINT_NAME, cc.CHECK_CLAUSE FROM information_schema.CHECK_CONSTRAINTS cc "
        "JOIN information_schema.TABLE_CONSTRAINTS tc "
        "  ON tc.CONSTRAINT_SCHEMA = cc.CONSTRAINT_SCHEMA AND tc.CONSTRAINT_NAME = cc.CONSTRAINT_NAME "
        "WHERE cc.CONSTRAINT_SCHEMA=%s AND tc.TABLE_NAME=%s",
        (database, table),
    )
    for row in cursor.fetchall():
        check_names.append(row["CONSTRAINT_NAME"])
        normalized = _normalize_expression(row["CHECK_CLAUSE"])
        if normalized:
            checks.add(normalized)

    return TableFingerprint(
        table_name=table,
        engine=table_row["ENGINE"],
        table_collation=table_row["TABLE_COLLATION"],
        columns=columns,
        keys=frozenset(keys),
        foreign_keys=frozenset(foreign_keys),
        checks=frozenset(checks),
        cosmetic_names={
            "indexes": tuple(sorted(key_names)),
            "foreign_keys": tuple(sorted(fk_names)),
            "checks": tuple(sorted(check_names)),
        },
    )


def compare_table(expected: TableFingerprint, actual: TableFingerprint, *, label: str) -> SchemaComparison:
    """So 2 fingerprint. `expected` = warehouse dung setup.sql; `actual` = staging cua nguon."""
    fatal: list[str] = []
    notes: list[str] = []
    table = expected.table_name

    if expected.engine != actual.engine:
        fatal.append(f"[{label}] {table}: ENGINE {actual.engine!r} != {expected.engine!r}")
    if expected.table_collation != actual.table_collation:
        fatal.append(
            f"[{label}] {table}: TABLE_COLLATION {actual.table_collation!r} != {expected.table_collation!r}"
        )

    expected_columns = {column.column_name: column for column in expected.columns}
    actual_columns = {column.column_name: column for column in actual.columns}
    missing = sorted(set(expected_columns) - set(actual_columns))
    extra = sorted(set(actual_columns) - set(expected_columns))
    if missing:
        fatal.append(f"[{label}] {table}: nguon THIEU cot {missing}")
    if extra:
        fatal.append(f"[{label}] {table}: nguon co cot THUA {extra}")
    for name in sorted(set(expected_columns) & set(actual_columns)):
        want, got = expected_columns[name], actual_columns[name]
        for attribute in (
            "ordinal_position", "column_type", "is_nullable", "column_default", "extra",
            "generation_expression", "character_set_name", "collation_name",
        ):
            want_value = getattr(want, attribute)
            got_value = getattr(got, attribute)
            if want_value != got_value:
                fatal.append(
                    f"[{label}] {table}.{name}: {attribute} {got_value!r} != {want_value!r}"
                )

    fatal.extend(_compare_sets(
        expected.keys, actual.keys, label=label, table=table, what="key/index (khong ke ten)",
    ))
    fatal.extend(_compare_sets(
        expected.foreign_keys, actual.foreign_keys, label=label, table=table,
        what="foreign key (khong ke ten)",
    ))
    fatal.extend(_compare_sets(
        expected.checks, actual.checks, label=label, table=table, what="CHECK (khong ke ten)",
    ))

    for kind in ("indexes", "foreign_keys", "checks"):
        want_names = expected.cosmetic_names.get(kind, ())
        got_names = actual.cosmetic_names.get(kind, ())
        if want_names != got_names:
            notes.append(
                f"[{label}] {table}: ten {kind} khac nhau (cosmetic, KHONG chan import) - "
                f"setup.sql={list(want_names)} vs nguon={list(got_names)}"
            )

    return SchemaComparison(fatal=tuple(fatal), notes=tuple(notes))


def _compare_sets(
    expected: Iterable[Any], actual: Iterable[Any], *, label: str, table: str, what: str,
) -> list[str]:
    expected_set, actual_set = set(expected), set(actual)
    problems: list[str] = []
    for item in sorted(expected_set - actual_set, key=repr):
        problems.append(f"[{label}] {table}: nguon THIEU {what}: {item!r}")
    for item in sorted(actual_set - expected_set, key=repr):
        problems.append(f"[{label}] {table}: nguon co THUA {what}: {item!r}")
    return problems


def compare_databases(
    cursor, *, expected_database: str, actual_database: str, tables: Sequence[str], label: str,
) -> SchemaComparison:
    """So toan bo `tables` giua 2 database. Tra ve tong hop fatal + notes."""
    fatal: list[str] = []
    notes: list[str] = []
    for table in tables:
        expected = read_table_fingerprint(cursor, expected_database, table)
        actual = read_table_fingerprint(cursor, actual_database, table)
        comparison = compare_table(expected, actual, label=label)
        fatal.extend(comparison.fatal)
        notes.extend(comparison.notes)
    return SchemaComparison(fatal=tuple(fatal), notes=tuple(notes))

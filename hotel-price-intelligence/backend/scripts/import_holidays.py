"""Validate and import data/vn_holidays.csv into MySQL.

Usage from backend/:
    python scripts/import_holidays.py --replace

Without --replace, rows are upserted by (holiday_date, event_code). With
--replace, the table is synchronized exactly to the CSV inside one transaction.
"""
from __future__ import annotations

import argparse
import csv
import os
import sys
from datetime import date
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.core.database import get_db_connection


EXPECTED_COLUMNS = [
    "holiday_date",
    "event_code",
    "name",
    "event_type",
    "scope",
    "city",
    "is_tet",
    "status",
    "source_url",
]
EVENT_TYPES = {"public_holiday", "festival", "major_event"}
STATUSES = {"confirmed", "provisional"}


def default_csv_path() -> Path:
    return Path(__file__).resolve().parents[2] / "data" / "vn_holidays.csv"


def load_rows(path: Path) -> list[dict[str, object]]:
    with path.open("r", encoding="utf-8", newline="") as handle:
        reader = csv.DictReader(handle)
        if reader.fieldnames != EXPECTED_COLUMNS:
            raise ValueError(
                f"Unexpected columns: {reader.fieldnames!r}; expected {EXPECTED_COLUMNS!r}"
            )

        rows: list[dict[str, object]] = []
        seen: set[tuple[date, str]] = set()
        for line_number, raw in enumerate(reader, start=2):
            try:
                holiday_date = date.fromisoformat(raw["holiday_date"].strip())
            except ValueError as exc:
                raise ValueError(f"Line {line_number}: invalid holiday_date") from exc

            event_code = raw["event_code"].strip()
            name = raw["name"].strip()
            event_type = raw["event_type"].strip()
            scope = raw["scope"].strip()
            city = raw["city"].strip() or None
            is_tet_raw = raw["is_tet"].strip()
            status = raw["status"].strip()
            source_url = raw["source_url"].strip() or None

            if not event_code or not name:
                raise ValueError(f"Line {line_number}: event_code and name are required")
            if event_type not in EVENT_TYPES:
                raise ValueError(f"Line {line_number}: invalid event_type {event_type!r}")
            if scope not in {"national", "city"}:
                raise ValueError(f"Line {line_number}: invalid scope {scope!r}")
            if (scope == "national" and city is not None) or (scope == "city" and city is None):
                raise ValueError(f"Line {line_number}: scope/city combination is invalid")
            if is_tet_raw not in {"0", "1"}:
                raise ValueError(f"Line {line_number}: is_tet must be 0 or 1")
            if status not in STATUSES:
                raise ValueError(f"Line {line_number}: invalid status {status!r}")

            key = (holiday_date, event_code)
            if key in seen:
                raise ValueError(f"Line {line_number}: duplicate key {key!r}")
            seen.add(key)
            rows.append(
                {
                    "holiday_date": holiday_date,
                    "event_code": event_code,
                    "name": name,
                    "event_type": event_type,
                    "scope": scope,
                    "city": city,
                    "is_tet": int(is_tet_raw),
                    "status": status,
                    "source_url": source_url,
                }
            )

    if not rows:
        raise ValueError("CSV contains no holiday rows")
    return rows


def import_rows(rows: list[dict[str, object]], *, replace: bool) -> None:
    sql = """
        INSERT INTO vn_holidays (
            holiday_date, event_code, name, event_type, scope, city,
            is_tet, status, source_url
        ) VALUES (
            %(holiday_date)s, %(event_code)s, %(name)s, %(event_type)s,
            %(scope)s, %(city)s, %(is_tet)s, %(status)s, %(source_url)s
        )
        ON DUPLICATE KEY UPDATE
            name = VALUES(name),
            event_type = VALUES(event_type),
            scope = VALUES(scope),
            city = VALUES(city),
            is_tet = VALUES(is_tet),
            status = VALUES(status),
            source_url = VALUES(source_url)
    """
    with get_db_connection() as connection:
        cursor = connection.cursor()
        try:
            if replace:
                cursor.execute("DELETE FROM vn_holidays")
            cursor.executemany(sql, rows)
            connection.commit()
        except Exception:
            connection.rollback()
            raise
        finally:
            cursor.close()


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--csv", type=Path, default=default_csv_path())
    parser.add_argument(
        "--replace",
        action="store_true",
        help="Delete existing holidays and synchronize the table exactly to the CSV.",
    )
    args = parser.parse_args()

    path = args.csv.resolve()
    rows = load_rows(path)
    import_rows(rows, replace=args.replace)
    print(f"Imported {len(rows)} holiday rows from {path} (replace={args.replace}).")


if __name__ == "__main__":
    main()

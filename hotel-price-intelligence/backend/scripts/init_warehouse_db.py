"""Tao warehouse database versioned rong (WAREHOUSE_EDA_ML_SPEC.md muc 3a buoc 5-8, muc 18).

Ket qua: 1 database moi, ten qua whitelist ^[A-Za-z0-9_]+$ va bat buoc co prefix `warehouse_`,
chua 4 bang core (tu `setup.sql` da sanitize) + cac bang phu cua setup.sql + 11 bang ETL/dataset.

Ten da ton tai -> LOI (khong ghi de, khong append batch thu hai vao cung DB).

Chay (tu backend/):
    python scripts/init_warehouse_db.py --database warehouse_20260915_2src
    python scripts/init_warehouse_db.py --database warehouse_20260915_2src --dry-run
"""
from __future__ import annotations

import argparse
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.warehouse.bootstrap import (
    apply_setup_sql,
    assert_ready_for_build,
    create_etl_tables,
    create_warehouse_database,
    database_exists,
    list_tables,
    read_setup_sql,
)
from app.warehouse.errors import WarehouseError
from app.warehouse.etl_ddl import CORE_TABLES, ETL_TABLE_NAMES
from app.warehouse.naming import require_warehouse_database
from app.warehouse.sql_script import sanitize_setup_sql


def main() -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    parser = argparse.ArgumentParser(description="Tao warehouse database versioned rong.")
    parser.add_argument("--database", required=True, help="ten DB, phai bat dau bang 'warehouse_'")
    parser.add_argument("--setup-sql", default=None, help="duong dan setup.sql (mac dinh: app/database/setup.sql)")
    parser.add_argument("--dry-run", action="store_true", help="chi kiem tra, khong tao gi")
    args = parser.parse_args()

    try:
        name = require_warehouse_database(args.database)
        text, digest = read_setup_sql(args.setup_sql)
        statements = sanitize_setup_sql(text)

        print(f"warehouse database : {name}")
        print(f"setup_sql_sha256   : {digest}")
        print(f"statement sau sanitize: {len(statements)}")
        print(f"bang ETL se tao    : {len(ETL_TABLE_NAMES)}")

        if args.dry_run:
            exists = database_exists(name)
            print(f"[dry-run] database da ton tai: {exists}")
            if exists:
                print("[dry-run] -> chay that se FAIL vi khong ghi de DB co san.")
            return 0

        create_warehouse_database(name)
        print(f"[1/3] da tao database {name}")
        applied_digest = apply_setup_sql(name, setup_path=args.setup_sql)
        print(f"[2/3] da chay setup.sql ({len(statements)} statement), sha256={applied_digest}")
        created = create_etl_tables(name)
        print(f"[3/3] da tao {len(created)} bang ETL: {', '.join(created)}")

        tables = list_tables(name)
        missing_core = [table for table in CORE_TABLES if table not in tables]
        if missing_core:
            raise WarehouseError(f"sau khi init, van thieu bang core: {missing_core}")
        assert_ready_for_build(name)
        print(f"OK - tong {len(tables)} bang, core + ETL deu rong, san sang cho build_warehouse.")
        return 0
    except WarehouseError as exc:
        print(f"FAIL: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())

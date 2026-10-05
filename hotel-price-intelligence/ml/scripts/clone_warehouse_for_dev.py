"""CHI DEV/REHEARSAL: sao mot warehouse da PASS sang DB dung-mot-lan `warehouse_dsdev_*` de chay thu dataset builder ma KHONG ghi vao
warehouse da promote (bat bien). KHONG sao bang reference (`hotel_room_candidates`/`hotel_reference_rooms`) vi builder khong doc.

    python ml/scripts/clone_warehouse_for_dev.py --source warehouse_20260916_2src --target warehouse_dsdev_20260916_2src [--drop-existing]
    python ml/scripts/clone_warehouse_for_dev.py --drop-only --target warehouse_dsdev_20260916_2src

Guard: target PHAI bat dau bang `warehouse_dsdev_`; source != target; khong bao gio dung DB van hanh; chi DROP DB co prefix dsdev.
Bao thoi gian that. Thu tu sao theo FK; `FOREIGN_KEY_CHECKS=0` chi trong session sao chep.
"""
from __future__ import annotations

import argparse
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from dataset_builder import env  # noqa: E402,F401
from dataset_builder.db import connect, scalar  # noqa: E402

from app.warehouse.bootstrap import apply_setup_sql, create_etl_tables, create_warehouse_database  # noqa: E402
from app.warehouse.connection import warehouse_connection  # noqa: E402
from app.warehouse.naming import require_identifier  # noqa: E402

DEV_PREFIX = "warehouse_dsdev_"
COPY_ORDER = ("hotels", "crawl_runs", "crawl_run_items", "price_observations", "etl_import_batches", "etl_import_sources",
              "etl_import_rejections", "etl_run_map", "etl_item_map", "etl_observation_map", "curated_observation_keys")


def _require_dev(name: str) -> str:
    require_identifier(name, what="target database")
    if not name.startswith(DEV_PREFIX):
        raise SystemExit(f"FAIL: target {name!r} phai bat dau bang {DEV_PREFIX!r}")
    return name


def drop_dev(target: str) -> None:
    _require_dev(target)
    with warehouse_connection(None) as conn:
        cursor = conn.cursor()
        cursor.execute(f"DROP DATABASE IF EXISTS `{target}`")
        conn.commit()
        cursor.close()
    print(f"Da DROP {target} (neu co).")


def main() -> int:
    parser = argparse.ArgumentParser(description="Sao warehouse PASS sang DB dsdev dung-mot-lan.")
    parser.add_argument("--source")
    parser.add_argument("--target", required=True)
    parser.add_argument("--drop-existing", action="store_true")
    parser.add_argument("--drop-only", action="store_true")
    args = parser.parse_args()
    target = _require_dev(args.target)
    if args.drop_only:
        drop_dev(target)
        return 0
    if not args.source or args.source == target:
        print("FAIL: can --source khac --target", file=sys.stderr)
        return 2
    with connect(args.source) as src:
        batch = scalar(src, "SELECT COUNT(*) FROM etl_import_batches WHERE status='pass'")
        src.commit()
    if batch != 1:
        print(f"FAIL: source phai co dung 1 batch PASS (co {batch}).", file=sys.stderr)
        return 2
    with warehouse_connection(None) as conn:
        cursor = conn.cursor()
        cursor.execute("SHOW DATABASES LIKE %s", (target,))
        exists = cursor.fetchone() is not None
        cursor.close()
    if exists:
        if not args.drop_existing:
            print(f"FAIL: {target} da ton tai (dung --drop-existing de tao lai).", file=sys.stderr)
            return 2
        drop_dev(target)
    started = time.monotonic()
    create_warehouse_database(target)
    apply_setup_sql(target)
    create_etl_tables(target)
    print(f"Tao {target} + 15 bang xong sau {time.monotonic() - started:.1f}s")
    with warehouse_connection(target, verify=True) as conn:
        cursor = conn.cursor()
        cursor.execute("SET SESSION foreign_key_checks=0")
        cursor.execute("SET SESSION unique_checks=0")
        for table in COPY_ORDER:
            lap = time.monotonic()
            cursor.execute(f"INSERT INTO `{target}`.`{table}` SELECT * FROM `{args.source}`.`{table}`")
            copied = cursor.rowcount
            conn.commit()
            print(f"  {table:<28} {copied:>10} dong  {time.monotonic() - lap:7.1f}s", flush=True)
        cursor.execute("SET SESSION foreign_key_checks=1")
        # Ban sao dev: batch ghi warehouse_database cua BAN SAO (de verify_manifest khop SELECT DATABASE()); nguon goc ghi vao notes.
        cursor.execute("UPDATE etl_import_batches SET warehouse_database=%s, notes=JSON_SET(COALESCE(notes, '{}'), '$.cloned_from_for_dev', %s)",
                       (target, args.source))
        conn.commit()
        cursor.close()
    print(f"XONG sau {time.monotonic() - started:.1f}s. Nho DROP bang: clone_warehouse_for_dev.py --drop-only --target {target}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

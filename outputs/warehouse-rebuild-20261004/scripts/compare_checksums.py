"""So semantic + exact checksum (11 bảng CHECKSUM_TABLES) giữa hai batch warehouse (rehearsal vs official). CHỈ ĐỌC.

    venv/Scripts/python.exe ../../outputs/warehouse-rebuild-20261004/scripts/compare_checksums.py \\
        --a warehouse_rh20261004_3src:b20261004_3srcrh --b warehouse_20261004_3src:b20261004_3src

Lấy `etl_import_batches.notes.checksums` đã PIN lúc build (không tính lại) và so từng bảng; ngoài ra TÍNH LẠI `semantic_checksums()`
trực tiếp từ DB để chắc giá trị pin khớp dữ liệu hiện tại (phát hiện sửa đổi sau build). Exit 0 chỉ khi mọi bảng trùng ở cả semantic và exact.
Checksum 11 bảng KHÔNG gồm `etl_import_batches`/`etl_import_sources` nên script còn BẮT BUỘC so provenance (GPT 07b MAJOR 2): cả hai batch status='pass',
setup_sql/source_manifest/cohort/ownership/etl_config sha256, canonicalization version/commit/config sha256 và tập source chuẩn hoá theo source_code
(priority, dump SHA, schema SHA, dump timestamp, source_version_json). Chỉ bỏ qua batch_id/warehouse_database/started/finished/fail_reason/notes.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[3]
BACKEND = REPO / "hotel-price-intelligence" / "backend"
sys.path.insert(0, str(BACKEND))
from dotenv import load_dotenv  # noqa: E402

load_dotenv(BACKEND / ".env")
from app.warehouse.connection import warehouse_connection  # noqa: E402
from app.warehouse.etl_ddl import CORE_TABLES  # noqa: E402,F401
from app.warehouse.validation import semantic_checksums  # noqa: E402

sys.path.insert(0, str(Path(__file__).resolve().parent))
from rebuild_lib import diff_batch_provenance  # noqa: E402


def load(spec: str) -> dict:
    database, batch = spec.split(":", 1)
    with warehouse_connection(database) as conn:
        cursor = conn.cursor(dictionary=True)
        cursor.execute("SET SESSION TRANSACTION READ ONLY")
        cursor.execute("SELECT * FROM etl_import_batches WHERE batch_id=%s", (batch,))
        batch_row = cursor.fetchone()
        cursor.execute("SELECT * FROM etl_import_sources WHERE import_batch_id=%s ORDER BY source_code", (batch,))
        sources = cursor.fetchall()
        notes = json.loads(batch_row["notes"])
        pinned = notes["checksums"]
        recomputed = semantic_checksums(conn)
        cursor.close()
    return {"database": database, "batch": batch, "pinned": pinned, "recomputed": recomputed,
            "provenance": {"batch": batch_row, "sources": sources}}


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--a", required=True, help="database:batch_id")
    parser.add_argument("--b", required=True, help="database:batch_id")
    args = parser.parse_args()
    a, b = load(args.a), load(args.b)
    bad = 0
    for side in (a, b):
        for kind in ("semantic", "exact"):
            drift = sorted(t for t, d in side["pinned"][kind].items() if side["recomputed"][kind][t] != d)
            print(f"[{'OK ' if not drift else 'FAIL'}] {side['database']} pinned == recomputed ({kind}): drift={drift}")
            bad += bool(drift)
    for kind in ("semantic", "exact"):
        tables_a, tables_b = set(a["pinned"][kind]), set(b["pinned"][kind])
        differing = sorted(t for t in tables_a & tables_b if a["pinned"][kind][t] != b["pinned"][kind][t])
        only = sorted(tables_a ^ tables_b)
        ok = not differing and not only
        print(f"[{'OK ' if ok else 'FAIL'}] {kind}: {len(tables_a & tables_b)} bang so sanh, lech={differing}, thieu/thua={only}")
        bad += (not ok)
    diffs = diff_batch_provenance(a["provenance"], b["provenance"])
    for side in (a, b):
        status = side["provenance"]["batch"]["status"]
        print(f"[{'OK ' if status == 'pass' else 'FAIL'}] {side['database']} batch status={status}")
        bad += status != "pass"
    print(f"[{'OK ' if not diffs else 'FAIL'}] provenance trung nhau ngoai field theo-lan-build: {diffs}")
    bad += bool(diffs)
    return 1 if bad else 0


if __name__ == "__main__":
    raise SystemExit(main())

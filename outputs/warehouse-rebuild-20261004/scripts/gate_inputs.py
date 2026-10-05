"""Gói đầu vào cho GATE tổng thể sau rebuild (GPT 02 §D8). CHỈ ĐỌC warehouse (session READ ONLY); ghi JSON/CSV mới, không ghi đè.

    venv/Scripts/python.exe ../../outputs/warehouse-rebuild-20261004/scripts/gate_inputs.py --database warehouse_rh20261004_3src \\
        --batch-id b20261004_3srcrh --ownership-manifest ../data/warehouse/ownership_manifest_20261004.json \
        --cutoff-file ../../outputs/warehouse-rebuild-20261004/cutoff_20261004.json
(So voi Wave A hai nguon = script rieng `compare_wave_a.py`.)

Chỉ MÔ TẢ (đếm, tỷ lệ, danh sách) — KHÔNG phân tích canonical key/N1/parser, không diễn giải `chal_t`. Output: outputs/warehouse-rebuild-20261004/gate_inputs/<batch_id>/.
Truy vấn nặng dùng MAX_EXECUTION_TIME và pin time_zone='+00:00' (qua warehouse_connection).
"""
from __future__ import annotations

import argparse
import csv
import datetime as dt
import hashlib
import json
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[3]
BACKEND = REPO / "hotel-price-intelligence" / "backend"
sys.path.insert(0, str(BACKEND))
from dotenv import load_dotenv  # noqa: E402

load_dotenv(BACKEND / ".env")
from app.warehouse.connection import warehouse_connection  # noqa: E402
from app.warehouse.ownership_manifest import load_ownership_manifest  # noqa: E402

sys.path.insert(0, str(Path(__file__).resolve().parent))
from rebuild_lib import json_default, sweep_overlaps, validate_cutoff  # noqa: E402

OUT_ROOT = REPO / "outputs" / "warehouse-rebuild-20261004" / "gate_inputs"
MAX_MS = 900_000


def q(cursor, sql, params=()):
    cursor.execute(f"SELECT /*+ MAX_EXECUTION_TIME({MAX_MS}) */ * FROM ({sql}) t", params)
    return cursor.fetchall()


def write_csv(path: Path, rows: list[dict]) -> None:
    if not rows:
        path.write_text("", encoding="utf-8")
        return
    with open(path, "w", encoding="utf-8", newline="") as fh:
        writer = csv.DictWriter(fh, fieldnames=list(rows[0]))
        writer.writeheader()
        writer.writerows(rows)


def jsonable(value):
    """Giá trị ô CSV/summary: bytes/date/Decimal đều chuyển được (dùng chung json_default)."""
    try:
        return json_default(value)
    except TypeError:
        return value


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--database", required=True)
    parser.add_argument("--batch-id", required=True)
    parser.add_argument("--ownership-manifest", type=Path, required=True)
    parser.add_argument("--cutoff-file", type=Path, required=True,
                        help="JSON da pin cung input-freeze: {cutoff_vn_crawl_date: {source: YYYY-MM-DD}, evidence: ...}; KHONG dung date.today()")
    args = parser.parse_args()
    final = OUT_ROOT / args.batch_id
    if final.exists():
        raise SystemExit(f"FAIL: {final} da ton tai - khong ghi de goi gate cu.")
    stamp = dt.datetime.now().strftime("%Y%m%d_%H%M%S")
    out = OUT_ROOT / f".tmp-{args.batch_id}-{stamp}"            # ghi vao thu muc tam, thanh cong moi rename (atomic)
    out.mkdir(parents=True)
    try:
        run_gate(args, out)
    except BaseException as exc:                                  # giu bang chung, khong chan retry
        (out / "ERROR.txt").write_text(f"{type(exc).__name__}: {exc}\n", encoding="utf-8")
        out.rename(OUT_ROOT / f".failed-{args.batch_id}-{stamp}")
        raise
    out.rename(final)
    print(f"Da ghi goi gate tai {final}")
    return 0


def run_gate(args, out: Path) -> None:
    cutoff_raw = args.cutoff_file.read_bytes()
    cutoff_doc = json.loads(cutoff_raw.decode("utf-8"))
    summary: dict = {"database": args.database, "batch_id": args.batch_id, "generated_at_utc": dt.datetime.now(dt.timezone.utc).isoformat(),
                     "cutoff": cutoff_doc, "cutoff_file": str(args.cutoff_file),
                     "cutoff_file_sha256": hashlib.sha256(cutoff_raw).hexdigest()}
    with warehouse_connection(args.database) as conn:
        cur = conn.cursor(dictionary=True)
        cur.execute("SET SESSION TRANSACTION READ ONLY")
        # 1) nguon + dem so dong nhap
        sources = q(cur, "SELECT s.source_code, s.source_priority, s.dump_sha256, s.schema_sha256, s.dump_taken_at, s.source_version_json "
                         "FROM etl_import_sources s WHERE s.import_batch_id=%s", (args.batch_id,))
        summary["sources"] = [{k: jsonable(v) for k, v in row.items()} for row in sources]
        notes = json.loads(q(cur, "SELECT notes FROM etl_import_batches WHERE batch_id=%s", (args.batch_id,))[0]["notes"] or "{}")
        summary["notes_source_row_counts"] = notes.get("source_row_counts")
        summary["notes_schema_adapters"] = notes.get("schema_adapters")
        summary["notes_checksums"] = notes.get("checksums")
        recon = q(cur, "SELECT 'crawl_runs' t, source_code, COUNT(*) n FROM etl_run_map WHERE import_batch_id=%s GROUP BY source_code UNION ALL "
                       "SELECT 'crawl_run_items', source_code, COUNT(*) FROM etl_item_map WHERE import_batch_id=%s GROUP BY source_code UNION ALL "
                       "SELECT 'price_observations', source_code, COUNT(*) FROM etl_observation_map WHERE import_batch_id=%s GROUP BY source_code",
                  (args.batch_id,) * 3)
        summary["imported_rows_by_source"] = [{k: jsonable(v) for k, v in r.items()} for r in recon]
        summary["rejections"] = [{k: jsonable(v) for k, v in r.items()} for r in q(
            cur, "SELECT source_code, source_table, rejection_scope, waived, COUNT(*) n FROM etl_import_rejections WHERE import_batch_id=%s "
                 "GROUP BY 1,2,3,4", (args.batch_id,))]
        # 2) phan bo ownership
        own = q(cur, "SELECT im.source_code, im.ownership_status, im.exclusion_reason, im.include_reference, im.include_training, COUNT(*) items "
                     "FROM etl_item_map im WHERE im.import_batch_id=%s GROUP BY 1,2,3,4,5 ORDER BY 1,2,3", (args.batch_id,))
        write_csv(out / "ownership_by_source_status_reason.csv", own)
        own_day = q(cur, "SELECT im.source_code, rm.planned_crawl_date, im.ownership_status, COUNT(*) items FROM etl_item_map im "
                         "JOIN crawl_run_items cri ON cri.id=im.warehouse_item_id JOIN etl_run_map rm ON rm.warehouse_run_id=cri.crawl_run_id "
                         "AND rm.import_batch_id=im.import_batch_id WHERE im.import_batch_id=%s GROUP BY 1,2,3 ORDER BY 1,2,3", (args.batch_id,))
        write_csv(out / "ownership_by_source_crawl_date_status.csv", own_day)
        # 3) danh sach run (bat thuong: failed, thieu check-in, chong gio, nguon backup)
        runs = q(cur, "SELECT rm.source_code, rm.source_run_id, r.id warehouse_run_id, r.status, r.trigger_type, r.git_commit, r.scraper_version, "
                      "r.started_at, r.finished_at, r.total, r.processed, rm.planned_crawl_date, rm.include_reference, rm.include_training, rm.exclusion_reason, "
                      "(SELECT COUNT(*) FROM crawl_run_items i WHERE i.crawl_run_id=r.id) items, "
                      "(SELECT COUNT(DISTINCT i.checkin_date) FROM crawl_run_items i WHERE i.crawl_run_id=r.id) checkin_dates "
                      "FROM etl_run_map rm JOIN crawl_runs r ON r.id=rm.warehouse_run_id WHERE rm.import_batch_id=%s ORDER BY 1,2", (args.batch_id,))
        write_csv(out / "runs.csv", [{k: jsonable(v) for k, v in r.items()} for r in runs])
        summary["runs_not_completed"] = [{k: jsonable(v) for k, v in r.items()} for r in runs if r["status"] != "completed"]
        summary["runs_manual_or_no_commit"] = [{k: jsonable(v) for k, v in r.items()} for r in runs if r["trigger_type"] != "scheduled" or not r["git_commit"]]
        overlaps = sweep_overlaps(runs)
        summary["overlapping_runs_same_source"] = overlaps
        # 4) lien tuc lich: slot ky vong (ownership manifest) vs item thuc te
        manifest = load_ownership_manifest(args.ownership_manifest)
        actual = {(r["source_code"], r["planned_crawl_date"], r["checkin_date"]): r["items"] for r in q(
            cur, "SELECT rm.source_code, rm.planned_crawl_date, i.checkin_date, COUNT(*) items FROM crawl_run_items i "
                 "JOIN etl_run_map rm ON rm.warehouse_run_id=i.crawl_run_id AND rm.import_batch_id=%s GROUP BY 1,2,3", (args.batch_id,))}
        observed_max = {r["source_code"]: r["d"] for r in q(
            cur, "SELECT rm.source_code, MAX(rm.planned_crawl_date) d FROM etl_run_map rm WHERE rm.import_batch_id=%s GROUP BY 1", (args.batch_id,))}
        problems = validate_cutoff(cutoff_doc, [src["source_code"] for src in sources], observed_max)
        if problems:
            raise SystemExit("FAIL cutoff: " + "; ".join(problems))
        cutoff = {code: dt.date.fromisoformat(day) for code, day in cutoff_doc["cutoff_vn_crawl_date"].items()}
        missing = []
        for row in manifest.rows:
            if row.crawl_date > cutoff[row.owner_source]:        # ngoai cua so da freeze trong dump (khong phai finding)
                continue
            if (row.owner_source, row.crawl_date, row.checkin_date) not in actual:
                missing.append({"source_code": row.owner_source, "crawl_date": row.crawl_date.isoformat(), "slot": row.schedule_slot,
                                "checkin_date": row.checkin_date.isoformat()})
        write_csv(out / "expected_slots_without_items.csv", missing)
        summary["expected_slots_without_items"] = len(missing)
        # 5) missingness theo nguon (observation co gia) va run backup
        miss = q(cur, "SELECT om.source_code, COUNT(*) obs, SUM(po.price_per_night IS NULL) price_null, SUM(po.room_identity_key IS NULL) room_key_null, "
                      "SUM(po.rate_plan_key IS NULL) rate_key_null, SUM(po.max_occupancy IS NULL) occ_null, SUM(po.room_area IS NULL) area_null, "
                      "SUM(po.rooms_left IS NULL) rooms_left_null, SUM(po.taxes_fees IS NULL) taxes_null, SUM(po.price_includes_tax IS NULL) incl_tax_null, "
                      "SUM(po.breakfast_included IS NULL) breakfast_null, SUM(po.cancellation_policy IS NULL) cancel_null "
                      "FROM price_observations po JOIN etl_observation_map om ON om.warehouse_record_id=po.record_id AND om.import_batch_id=%s "
                      "WHERE po.is_sold_out=0 GROUP BY 1", (args.batch_id,))
        write_csv(out / "missingness_by_source.csv", [{k: jsonable(v) for k, v in r.items()} for r in miss])
        # 6) dem cot moc phien (khong dien giai)
        chal = q(cur, "SELECT rm.source_code, COUNT(*) items, SUM(i.hotel_link LIKE '%%chal_t=%%') with_chal_t FROM crawl_run_items i "
                      "JOIN etl_run_map rm ON rm.warehouse_run_id=i.crawl_run_id AND rm.import_batch_id=%s GROUP BY 1", (args.batch_id,))
        summary["chal_t_marker_counts_not_interpreted"] = [{k: jsonable(v) for k, v in r.items()} for r in chal]
        # 7) reference full-history
        summary["reference_full_history"] = [{k: jsonable(v) for k, v in r.items()} for r in q(
            cur, "SELECT status, COUNT(*) series, AVG(coverage) avg_coverage FROM hotel_reference_rooms GROUP BY status")]
        summary["hotel_room_candidates"] = int(q(cur, "SELECT COUNT(*) n FROM hotel_room_candidates")[0]["n"])
    (out / "summary.json").write_text(json.dumps(summary, ensure_ascii=False, indent=2, default=json_default), encoding="utf-8")


if __name__ == "__main__":
    raise SystemExit(main())

"""So warehouse 3 nguồn mới với Wave A (warehouse hai nguồn, bản chốt 16/09) — CHỈ ĐỌC; kết quả là DELTA CÓ DENOMINATOR (GPT 07b MAJOR 4).

    venv/Scripts/python.exe ../../outputs/warehouse-rebuild-20261004/scripts/compare_wave_a.py \\
        --old warehouse_20260916_2src:b20260916_2src --old-sources local_primary,vps \
        --new warehouse_20261004_3src:b20261004_3src --new-sources local_primary,vps,local_aux

Không đòi hai snapshot bằng nhau (nguồn/cửa sổ đã mở rộng). Nhưng lịch sử đã đóng băng PHẢI bất biến ở cấp BẢN GHI (GPT 09 MAJOR 1):
- observation: mọi `(source_code, source_record_id)` của Wave A có ở bản mới với cùng `source_record_sha256` (fingerprint nội dung bất biến);
- item: khớp theo `(source_code, source_item_id)`, so source run, status, source link hash, hotel, check-in/out, saved/raw/parsed option counts, error code;
- run: khớp theo `(source_code, source_run_id)`, so status, trigger, date mode, source file hash, version/commit, started/finished, counters, check-in config, planned crawl date;
không so ID kỹ thuật warehouse/imported_at/field reference rebuild. Đếm ĐẦY ĐỦ chênh lệch (mẫu tối đa 20); exit 1 nếu thiếu hoặc lệch bất kỳ.
Đếm run/item/observation (denominator) vẫn được báo nhưng KHÔNG được coi là "row parity". Ghi: đếm theo nguồn (kèm tỉ số new/old), phân bố status item, phân bố ownership,
cửa sổ thời gian, hotel theo thành phố, reference full-history. Ghi vào outputs/warehouse-rebuild-20261004/wave_a_comparison/<new_batch>/ (thư mục tạm
rồi rename; không ghi đè). Chạy sau zero-active (truy vấn GROUP BY trên ~3 triệu dòng).
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[3]
BACKEND = REPO / "hotel-price-intelligence" / "backend"
sys.path.insert(0, str(BACKEND))
from dotenv import load_dotenv  # noqa: E402

load_dotenv(BACKEND / ".env")
from app.warehouse.connection import warehouse_connection  # noqa: E402

sys.path.insert(0, str(Path(__file__).resolve().parent))
from rebuild_lib import (  # noqa: E402
    build_parity_sqls, compare_run_sets, json_default, parity_session_sql, parity_verdict, validate_snapshot_identity,
)

OUT_ROOT = REPO / "outputs" / "warehouse-rebuild-20261004" / "wave_a_comparison"
MAX_MS = 900_000


def q(cur, sql, params=()):
    cur.execute(f"SELECT /*+ MAX_EXECUTION_TIME({MAX_MS}) */ * FROM ({sql}) t", params)
    return cur.fetchall()


def snapshot(spec: str, label: str, expected_sources: list[str]) -> dict:
    database, batch = spec.split(":", 1)
    out: dict = {"database": database, "batch": batch}
    with warehouse_connection(database) as conn:
        cur = conn.cursor(dictionary=True)
        cur.execute("SET SESSION TRANSACTION READ ONLY")
        batch_rows = q(cur, "SELECT * FROM etl_import_batches WHERE batch_id=%s", (batch,))
        source_rows = q(cur, "SELECT source_code, source_priority, dump_sha256, schema_sha256 FROM etl_import_sources WHERE import_batch_id=%s", (batch,))
        problems, identity = validate_snapshot_identity(label, batch_rows[0] if batch_rows else None, source_rows, expected_sources)
        if problems:                                                    # fail-closed: batch thieu / khong pass / sai tap source
            raise SystemExit("FAIL identity: " + "; ".join(problems))
        out["identity"] = identity
        runs = q(cur, "SELECT rm.source_code, rm.source_run_id, r.status, COALESCE(i.n,0) items, COALESCE(o.n,0) observations "
                      "FROM etl_run_map rm JOIN crawl_runs r ON r.id=rm.warehouse_run_id "
                      "LEFT JOIN (SELECT crawl_run_id, COUNT(*) n FROM crawl_run_items GROUP BY crawl_run_id) i ON i.crawl_run_id=r.id "
                      "LEFT JOIN (SELECT crawl_run_id, COUNT(*) n FROM price_observations GROUP BY crawl_run_id) o ON o.crawl_run_id=r.id "
                      "WHERE rm.import_batch_id=%s", (batch,))
        out["runs"] = runs
        out["by_source"] = {}
        for r in runs:
            s = out["by_source"].setdefault(r["source_code"], {"runs": 0, "items": 0, "observations": 0})
            s["runs"] += 1
            s["items"] += int(r["items"])
            s["observations"] += int(r["observations"])
        out["item_status_by_source"] = [dict(r) for r in q(
            cur, "SELECT rm.source_code, i.status, COUNT(*) n FROM crawl_run_items i JOIN etl_run_map rm ON rm.warehouse_run_id=i.crawl_run_id "
                 "AND rm.import_batch_id=%s GROUP BY 1,2 ORDER BY 1,2", (batch,))]
        out["ownership_by_source"] = [dict(r) for r in q(
            cur, "SELECT source_code, ownership_status, COUNT(*) n FROM etl_item_map WHERE import_batch_id=%s GROUP BY 1,2 ORDER BY 1,2", (batch,))]
        out["window_by_source"] = [dict(r) for r in q(
            cur, "SELECT rm.source_code, MIN(rm.planned_crawl_date) first_crawl_date, MAX(rm.planned_crawl_date) last_crawl_date, "
                 "COUNT(DISTINCT rm.planned_crawl_date) crawl_dates FROM etl_run_map rm WHERE rm.import_batch_id=%s GROUP BY 1", (batch,))]
        out["hotels"] = [dict(r) for r in q(cur, "SELECT COALESCE(city,'(none)') city, COUNT(*) n FROM hotels GROUP BY 1 ORDER BY 1")]
        out["reference_full_history"] = [dict(r) for r in q(cur, "SELECT status, COUNT(*) series FROM hotel_reference_rooms GROUP BY status")]
        out["totals"] = {"observations": int(q(cur, "SELECT COUNT(*) n FROM price_observations")[0]["n"]),
                         "sold_out": int(q(cur, "SELECT COUNT(*) n FROM price_observations WHERE is_sold_out=1")[0]["n"])}
    return out


def row_parity(old_spec: str, new_spec: str) -> dict:
    old_db, old_batch = old_spec.split(":", 1)
    new_db, new_batch = new_spec.split(":", 1)
    sqls = build_parity_sqls(old_db, new_db)
    counts, samples = {}, {}
    with warehouse_connection(new_db) as conn:
        cur = conn.cursor(dictionary=True)
        cur.execute("SET SESSION TRANSACTION READ ONLY")
        cur.execute(parity_session_sql(MAX_MS))        # tran thoi gian cho MOI truy van parity (timeout -> .failed-* co loi ro, khong cho vo han)
        for kind, pair in sqls.items():
            cur.execute(pair["count"], (new_batch, old_batch))
            counts[kind] = cur.fetchone()
            cur.execute(pair["sample"], (new_batch, old_batch))
            samples[kind] = cur.fetchall()
    verdict = parity_verdict(counts)
    verdict["samples_max20_per_kind"] = samples
    verdict["old_source_batches"] = {"old": old_spec, "new": new_spec}
    verdict["max_execution_time_ms"] = MAX_MS
    return verdict


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--old", required=True, help="database:batch_id của Wave A")
    parser.add_argument("--new", required=True, help="database:batch_id của warehouse mới")
    parser.add_argument("--old-sources", required=True, help="tập source đã pin của Wave A, vd local_primary,vps (runbook ghi giá trị cụ thể)")
    parser.add_argument("--new-sources", required=True, help="tập source đã pin của warehouse mới, vd local_primary,vps,local_aux")
    args = parser.parse_args()
    final = OUT_ROOT / args.new.split(":", 1)[1]
    if final.exists():
        raise SystemExit(f"FAIL: {final} đã tồn tại - không ghi đè.")
    OUT_ROOT.mkdir(parents=True, exist_ok=True)
    stamp = dt.datetime.now().strftime("%Y%m%d_%H%M%S")
    tmp = OUT_ROOT / f".tmp-{final.name}-{stamp}"
    tmp.mkdir()
    try:
        old = snapshot(args.old, "old(Wave A)", [x for x in args.old_sources.split(",") if x])
        new = snapshot(args.new, "new", [x for x in args.new_sources.split(",") if x])
        parity = compare_run_sets(old["runs"], new["runs"])           # chỉ là denominator (đếm), KHÔNG phải row parity
        rows = row_parity(args.old, args.new)
        delta = {}
        for source in sorted(set(old["by_source"]) | set(new["by_source"])):
            o = old["by_source"].get(source, {"runs": 0, "items": 0, "observations": 0})
            n = new["by_source"].get(source, {"runs": 0, "items": 0, "observations": 0})
            delta[source] = {k: {"old": o[k], "new": n[k], "delta": n[k] - o[k], "new_over_old": (round(n[k] / o[k], 4) if o[k] else None)} for k in o}
        report = {
            "old": {"database": old["database"], "batch": old["batch"], "identity": old["identity"]},
            "new": {"database": new["database"], "batch": new["batch"], "identity": new["identity"]},
            "record_level_parity_for_frozen_history": rows, "run_counts_denominator": parity, "counts_delta_by_source": delta,
            "totals": {"old": old["totals"], "new": new["totals"]},
            "item_status_by_source": {"old": old["item_status_by_source"], "new": new["item_status_by_source"]},
            "ownership_by_source": {"old": old["ownership_by_source"], "new": new["ownership_by_source"]},
            "window_by_source": {"old": old["window_by_source"], "new": new["window_by_source"]},
            "hotels_by_city": {"old": old["hotels"], "new": new["hotels"]},
            "reference_full_history": {"old": old["reference_full_history"], "new": new["reference_full_history"]},
            "note": "Delta có denominator; không yêu cầu hai snapshot bằng nhau. Chỉ 'record_level_parity_for_frozen_history.parity' phải true.",
        }
        (tmp / "wave_a_comparison.json").write_text(json.dumps(report, ensure_ascii=False, indent=2, default=json_default), encoding="utf-8")
    except BaseException as exc:
        (tmp / "ERROR.txt").write_text(f"{type(exc).__name__}: {exc}\n", encoding="utf-8")
        tmp.rename(OUT_ROOT / f".failed-{final.name}-{stamp}")
        raise
    tmp.rename(final)
    print(f"Đã ghi {final / 'wave_a_comparison.json'}; record-level parity (lịch sử đóng băng) = {rows['parity']}")
    return 0 if rows["parity"] else 1


if __name__ == "__main__":
    raise SystemExit(main())

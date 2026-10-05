"""Thu bang chung cho bao cao rehearsal/official (CHI DOC): theo tung nguon + toan warehouse (GPT file 20 §3).

    venv/Scripts/python.exe ../../outputs/warehouse-rebuild-20261004/scripts/collect_evidence.py --database <db> --batch-id <batch>
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

BACKEND = Path(__file__).resolve().parents[3] / "hotel-price-intelligence" / "backend"
sys.path.insert(0, str(BACKEND))
from dotenv import load_dotenv  # noqa: E402

load_dotenv(BACKEND / ".env")
from app.warehouse.connection import warehouse_connection  # noqa: E402

REPORT_DIR = BACKEND.parent / "data" / "warehouse" / "reports"


def q(cur, sql, params=()):
    cur.execute(sql, params)
    return cur.fetchall()


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--database", required=True)
    parser.add_argument("--batch-id", required=True)
    args = parser.parse_args()
    report = json.loads((REPORT_DIR / f"{args.batch_id}.json").read_text(encoding="utf-8"))
    print("report status:", report["status"], "failed_checks:", report["failed_checks"], "started:", report["started_at"], "finished:", report["finished_at"])
    print("timings_s:", report["timings_s"], "TONG(buoc)=", round(sum(report["timings_s"].values()), 1))
    with warehouse_connection(args.database) as conn:
        cur = conn.cursor(dictionary=True)
        cur.execute("SET SESSION TRANSACTION READ ONLY")
        cur.execute("SET SESSION max_execution_time = 600000")
        batch = q(cur, "SELECT batch_id,status,fail_reason,setup_sql_sha256,source_manifest_sha256,etl_config_sha256 FROM etl_import_batches WHERE batch_id=%s", (args.batch_id,))[0]
        print("batch:", batch)
        for r in q(cur, "SELECT source_code, source_priority, dump_sha256, schema_sha256, dump_taken_at FROM etl_import_sources WHERE import_batch_id=%s ORDER BY source_priority", (args.batch_id,)):
            print("source:", r)
        notes = json.loads(q(cur, "SELECT notes FROM etl_import_batches WHERE batch_id=%s", (args.batch_id,))[0]["notes"])
        print("schema_adapters:", json.dumps(notes.get("schema_adapters"), ensure_ascii=False))
        print("rejections (waived/unwaived):", q(cur, "SELECT source_code, waived, COUNT(*) n FROM etl_import_rejections WHERE import_batch_id=%s GROUP BY source_code, waived", (args.batch_id,)))
        for t in ("hotels", "crawl_runs", "crawl_run_items", "price_observations", "curated_observation_keys", "hotel_room_candidates", "hotel_reference_rooms"):
            print("rows", t, q(cur, f"SELECT COUNT(*) n FROM {t}")[0]["n"])
        print("obs sold-out:", q(cur, "SELECT SUM(is_sold_out) so FROM price_observations")[0]["so"])
        print("run_map by source/include:", q(cur, "SELECT source_code, include_reference, include_eda_main, include_training, COUNT(*) n FROM etl_run_map GROUP BY 1,2,3,4 ORDER BY 1,2,3,4"))
        print("item ownership by source:", q(cur, "SELECT source_code, ownership_status, COUNT(*) n FROM etl_item_map GROUP BY 1,2 ORDER BY 1,2"))
        print("item exclusion_reason by source:", q(cur, "SELECT source_code, ownership_status, exclusion_reason, COUNT(*) n FROM etl_item_map WHERE exclusion_reason IS NOT NULL GROUP BY 1,2,3 ORDER BY 1,2,3"))
        print("obs by source (map):", q(cur, "SELECT source_code, COUNT(*) n FROM etl_observation_map GROUP BY 1 ORDER BY 1"))
        print("reference status:", q(cur, "SELECT status, COUNT(*) n FROM hotel_reference_rooms GROUP BY 1"))
        print("checksum semantic keys:", sorted(report["checksums"]["semantic"].keys()))
        print("checksums semantic:", json.dumps(report["checksums"]["semantic"]))
        print("checksums exact:", json.dumps(report["checksums"]["exact"]))
        cur.execute("SELECT @@innodb_buffer_pool_size pool, @@innodb_redo_log_capacity redo")
        print("InnoDB config luc thu bang chung:", cur.fetchone())
        cur.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

"""Bang chung doc-chi cho bao cao rehearsal: (1) DB van hanh khong doi so voi baseline sau run 63, (2) warehouse NGUON khong bi ghi (core giu nguyen, khong co dataset/ml_*),
(3) pointer warehouse_current.json khong doi, (4) GUARDED_PATHS sach so voi HEAD, (5) bang manifest cua moi dataset_version tren ban sao dev."""
from __future__ import annotations

import hashlib
import json
import subprocess
import sys
from pathlib import Path

BACKEND = Path(r"D:\MSE\CAPSTONE\hotel-price-intelligence\backend")
REPO = Path(r"D:\MSE\CAPSTONE")
sys.path.insert(0, str(BACKEND))
from dotenv import load_dotenv

load_dotenv(BACKEND / ".env")
import mysql.connector
from app.core.config import settings
from app.warehouse.provenance import GUARDED_PATHS

BASELINE = {"crawl_runs": (60, 63), "crawl_run_items": (215917, 228697), "price_observations": (1762171, 1765602)}
SOURCE_EXPECTED = {"crawl_runs": 130, "crawl_run_items": 385914, "price_observations": 3153852, "hotels": 358}


def connect(database=None):
    return mysql.connector.connect(host=settings.DB_HOST, port=settings.DB_PORT, user=settings.DB_USER, password=settings.DB_PASSWORD,
                                   database=database, connection_timeout=10, autocommit=True)


def main() -> int:
    out: dict = {}
    conn = connect()
    cur = conn.cursor(dictionary=True)
    cur.execute("SET SESSION max_execution_time=60000")
    ops = {}
    for table, pk in (("crawl_runs", "id"), ("crawl_run_items", "id"), ("price_observations", "record_id")):
        cur.execute(f"SELECT COUNT(*) n, MAX({pk}) mx FROM {settings.DB_NAME}.{table}")
        r = cur.fetchone()
        ops[table] = (int(r["n"]), int(r["mx"]))
    cur.execute(f"SELECT (SELECT COUNT(*) FROM {settings.DB_NAME}.crawl_runs WHERE status IN ('queued','running')) r, "
                f"(SELECT COUNT(*) FROM {settings.DB_NAME}.crawl_run_items WHERE status IN ('queued','running')) i")
    active = cur.fetchone()
    out["operational"] = {"now": ops, "baseline_after_run_63": BASELINE, "unchanged": ops == BASELINE, "active_runs_items": [int(active["r"]), int(active["i"])]}
    src = {}
    for table in SOURCE_EXPECTED:
        cur.execute(f"SELECT COUNT(*) n FROM warehouse_20261004_3src.{table}")
        src[table] = int(cur.fetchone()["n"])
    for table in ("dataset_build_manifests", "ml_reference_assignments", "ml_item_reference_matches", "ml_samples"):
        cur.execute(f"SELECT COUNT(*) n FROM warehouse_20261004_3src.{table}")
        src[table] = int(cur.fetchone()["n"])
    out["source_warehouse"] = {"counts": src, "core_as_promoted": all(src[t] == v for t, v in SOURCE_EXPECTED.items()),
                               "no_dataset_rows_in_source": all(src[t] == 0 for t in ("dataset_build_manifests", "ml_reference_assignments", "ml_item_reference_matches", "ml_samples"))}
    cur.execute("SELECT dataset_version, status, last_completed_step, build_config_sha256, started_at, finished_at, split_train_end, split_validation_end, "
                "JSON_UNQUOTE(JSON_EXTRACT(build_config_json,'$.builder_version')) builder_version, "
                "JSON_UNQUOTE(JSON_EXTRACT(build_config_json,'$.builder_code.code_sha256')) code_sha256, "
                "JSON_UNQUOTE(JSON_EXTRACT(build_config_json,'$.calendar_input.sha256')) calendar_sha256, "
                "JSON_UNQUOTE(JSON_EXTRACT(build_config_json,'$.purpose')) purpose, active_step, active_step_attempt, retry_overrides_json "
                "FROM warehouse_dsdev_20261004_3src.dataset_build_manifests ORDER BY dataset_version")
    manifests = cur.fetchall()
    out["dev_manifests"] = [{k: (str(v) if v is not None else None) for k, v in row.items()} for row in manifests]
    conn.close()
    pointer = Path(r"D:\MSE\CAPSTONE\outputs\warehouse\warehouse_current.json")
    out["pointer_sha256"] = hashlib.sha256(pointer.read_bytes()).hexdigest()
    status = subprocess.run(["git", "-C", str(REPO), "status", "--porcelain", "--untracked-files=all", "--", *GUARDED_PATHS], capture_output=True, text=True).stdout
    out["guarded_paths_dirty"] = [line[3:] for line in status.splitlines() if line.strip()]
    out["git_head"] = subprocess.run(["git", "-C", str(REPO), "rev-parse", "HEAD"], capture_output=True, text=True).stdout.strip()
    print(json.dumps(out, ensure_ascii=False, indent=1, default=str))
    (Path(r"D:\MSE\CAPSTONE\outputs\dataset-builder-rehearsal-20261006") / "evidence_guard.json").write_text(json.dumps(out, ensure_ascii=False, indent=2, default=str), encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

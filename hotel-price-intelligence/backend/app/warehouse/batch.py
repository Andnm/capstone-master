"""Orchestrator warehouse build - WAREHOUSE_EDA_ML_SPEC.md muc 3a (17 buoc).

Anh xa buoc -> noi thuc hien (GPT file 02: report 17 buoc phai tach buoc do init/ben ngoai lam):
  1   external source manifest            -> nguoi van hanh (data/warehouse/source_manifest_*.json)
  2-4 dump/scan/checksum                  -> ben ngoai; o day RE-VERIFY dump_sha256 + schema_sha256,
                                             preflight chay trong staging.restore_dump_into_staging
  5-8 tao DB, setup.sql, verify, 11 bang ETL -> init_warehouse_db; o day assert_ready_for_build
  9   registry 1 transaction              -> registry.materialize_batch
  10  N staging (giu dong thoi)           -> staging.staging_database (ExitStack) + schema gate muc 5
  11  import/remap                        -> importer.Importer (+ audit TIMESTAMP khi staging con song)
  12  drop N staging                      -> ExitStack thoat (ke ca khi loi)
  14  curated_observation_keys            -> curated.build_curated_keys
  15  full-history reference              -> reference_builder
  13  integrity/reconcile                 -> validation.validate_warehouse (chay SAU 14-15 de gom ca
                                             check curated/reference vao cung 1 report)
  16  re-verify source_manifest_sha256    -> registry.verify_source_manifest -> status pass/fail
  17  promote                             -> lenh rieng promote_warehouse (KHONG chay o day)
Moi loi -> batch status='fail' + fail_reason, re-raise. Warehouse DB loi thi bo di, build DB moi.
Report co `timings_s` tung buoc (GPT file 10: bao runtime that, khong hua truoc con so).
"""
from __future__ import annotations

import datetime as dt
import json
import time
from contextlib import ExitStack
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .atomic import atomic_write_json
from .bootstrap import assert_ready_for_build, read_setup_sql
from .canonicalize import CANONICALIZATION_VERSION
from .cohort_manifest import load_cohort
from .connection import warehouse_connection
from .curated import build_curated_keys
from .errors import SchemaMismatchError, ValidationError
from .etl_config import canonicalization_config_sha256, etl_config, etl_config_sha256
from .etl_ddl import CORE_TABLES
from .importer import Importer, SourceStaging, require_core_empty
from .naming import require_warehouse_database
from .ownership_manifest import load_ownership_manifest
from .provenance import code_provenance
from .reference_builder import build_full_history_references
from .registry import materialize_batch, set_batch_status, verify_source_manifest
from .schema_fingerprint import compare_databases
from .source_manifest import load_source_manifest, verify_dump_checksum, verify_schema_checksum
from .staging import staging_database
from .validation import audit_run_timestamps, semantic_checksums, validate_warehouse

REPORT_DIR = Path(__file__).resolve().parents[3] / "data" / "warehouse" / "reports"


@dataclass(frozen=True)
class BuildInputs:
    warehouse_database: str
    source_manifest_path: Path
    cohort_manifest_path: Path  # .json = cohort history nhieu version (duong chay that); .xlsx = 1 version (fixture)
    ownership_manifest_path: Path
    base_dir: Path
    batch_id: str | None = None
    report_dir: Path = REPORT_DIR
    # Provenance fail-closed (GPT review 12 MAJOR 2): False CHI cho rehearsal disposable/fixture - khi do
    # commit ghi vao batch mang hau to "+dirty" va `promote_warehouse` tu choi batch do.
    require_clean_provenance: bool = True


def utc_now() -> dt.datetime:
    return dt.datetime.now(dt.timezone.utc).replace(tzinfo=None, microsecond=0)


class _Clock:
    def __init__(self, report: dict[str, Any]) -> None:
        self.report = report
        self.last = time.monotonic()
        self.report["timings_s"] = {}

    def lap(self, name: str) -> None:
        now = time.monotonic()
        self.report["timings_s"][name] = round(now - self.last, 1)
        self.last = now


def build_warehouse(inputs: BuildInputs) -> dict[str, Any]:
    database = require_warehouse_database(inputs.warehouse_database)
    started = utc_now()
    batch_id = inputs.batch_id or f"b{started:%Y%m%d%H%M%S}"
    report: dict[str, Any] = {"batch_id": batch_id, "warehouse_database": database, "started_at": started, "steps": {}}
    clock = _Clock(report)

    manifest = load_source_manifest(inputs.source_manifest_path, base_dir=inputs.base_dir)
    for entry in manifest.sources:                       # buoc 2-4: re-verify, khong tin manifest
        verify_dump_checksum(entry)
        verify_schema_checksum(entry)
    report["steps"]["2-4_reverify_dumps"] = [entry.source_code for entry in manifest.by_priority()]
    cohort = load_cohort(inputs.cohort_manifest_path, base_dir=inputs.base_dir)
    report["steps"]["cohort_versions"] = cohort.summary()
    ownership = load_ownership_manifest(inputs.ownership_manifest_path)
    assert_ready_for_build(database)                     # buoc 5-8 da xong + DB rong
    _text, setup_sha = read_setup_sql()
    config = etl_config()
    etl_sha, canon_sha = etl_config_sha256(), canonicalization_config_sha256()
    # Buoc 9 chi duoc ghi provenance THAT: cac file quyet dinh canonical/reference/import phai trung HEAD.
    git_commit = code_provenance(require_clean=inputs.require_clean_provenance)
    report["steps"]["provenance"] = {"canonicalization_git_commit": git_commit,
                                     "require_clean": inputs.require_clean_provenance,
                                     "etl_config": config, "etl_config_sha256": etl_sha}
    clock.lap("2-8_reverify_manifests")

    with warehouse_connection(database) as wh:
        require_core_empty(wh)
        materialize_batch(                              # buoc 9
            wh, batch_id=batch_id, warehouse_database=database, manifest=manifest, started_at=started,
            setup_sql_sha256=setup_sha, cohort_manifest_sha256=cohort.manifest_sha256,
            ownership_manifest_sha256=ownership.manifest_sha256, etl_config_sha256=etl_sha,
            canonicalization_version=CANONICALIZATION_VERSION,
            canonicalization_git_commit=git_commit,
            canonicalization_config_sha256=canon_sha,
        )
        try:
            importer = Importer(wh, batch_id=batch_id, cohort=cohort, ownership=ownership, imported_at=started)
            with ExitStack() as stack:                   # buoc 10 ... 12 (thoat = drop moi staging)
                stagings = []
                for entry in manifest.by_priority():
                    handle = stack.enter_context(staging_database(batch_id, entry.source_code, entry.dump_path))
                    # Connection MOI cho moi lan so schema: `wh` chay autocommit=False + REPEATABLE READ, va
                    # information_schema cua MySQL 8 doc tu data dictionary InnoDB -> lan doc truoc da mo 1
                    # snapshot, staging tao SAU snapshot do (boi connection khac) se VO HINH voi `wh`. Bug
                    # nay do test e2e 2 nguon bat duoc: nguon thu 2 bi bao "khong tim thay bang".
                    with warehouse_connection(database) as meta:
                        cursor = meta.cursor(dictionary=True)
                        schema = compare_databases(cursor, expected_database=database, actual_database=handle.name,
                                                   tables=CORE_TABLES, label=entry.source_code)
                        cursor.close()
                    if schema.fatal:
                        raise SchemaMismatchError(f"nguon {entry.source_code}: {list(schema.fatal)[:5]}")
                    stagings.append(SourceStaging(entry.source_code, entry.source_priority, handle.name))
                    report["steps"].setdefault("10_staging", {})[entry.source_code] = {
                        "preflight_statements": handle.preflight["statements"],
                        "preflight_findings": len(handle.preflight["findings"]),
                        "support_tables_checked": handle.preflight.get("support_tables_checked"),
                        "schema_notes": list(schema.notes)}
                    clock.lap(f"10_staging_{entry.source_code}")
                importer.import_hotels(stagings)         # buoc 11
                clock.lap("11_hotels")
                for staging in stagings:
                    importer.import_source(staging)
                    with warehouse_connection(staging.staging) as staging_conn:
                        audit = audit_run_timestamps(staging_conn, wh, batch_id=batch_id, source_code=staging.source_code)
                    if audit["mismatches"]:
                        raise ValidationError(f"TIMESTAMP lech mui gio o {staging.source_code}: run {audit['mismatches'][:5]}")
                    report["steps"].setdefault("11_timestamp_audit", {})[staging.source_code] = audit
                    clock.lap(f"11_import_{staging.source_code}")
            clock.lap("12_drop_staging")
            import_report = importer.report
            notes = {"source_row_counts": {code: {
                        "crawl_runs": stats["crawl_runs_source"], "crawl_run_items": stats["crawl_run_items_source"],
                        "price_observations": stats["price_observations_source"]}
                     for code, stats in import_report.per_source.items()},
                     "hotels": import_report.hotels, "cohort_versions": cohort.summary()}
            _save_notes(wh, batch_id, notes)
            report["steps"]["11_import"] = {"per_source": {k: dict(v) for k, v in import_report.per_source.items()},
                                            "hotels": import_report.hotels,
                                            "rejections": {"|".join(k): v for k, v in import_report.rejections.items()}}
            report["steps"]["14_curated"] = build_curated_keys(wh, created_at=started)
            clock.lap("14_curated")
            report["steps"]["15_references"] = build_full_history_references(
                wh, batch_id=batch_id, activated_at=started, min_runs=config["reference_min_runs"],
                min_coverage=config["reference_min_coverage"])
            clock.lap("15_references")
            validation = validate_warehouse(wh, batch_id=batch_id)
            report["steps"]["13_validation"] = validation
            report["checksums"] = semantic_checksums(wh)  # D10 gate 7: build lai cung input -> cung checksum
            # PIN checksum vao notes de `promote_warehouse` va rebuild reference doi chieu lai, phat hien
            # moi thay doi du lieu SAU build (GPT review 12 MAJOR 1).
            notes["checksums"] = report["checksums"]
            _save_notes(wh, batch_id, notes)
            report["steps"]["16_source_manifest_sha256"] = verify_source_manifest(wh, batch_id)
            clock.lap("13_16_validation_checksums")
            status = "pass" if validation["ok"] else "fail"
            failed = [check["name"] for check in validation["checks"] if not check["ok"]]
            set_batch_status(wh, batch_id, status, finished_at=utc_now(),
                             fail_reason=None if status == "pass" else f"validation fail: {failed}")
            report.update(status=status, failed_checks=failed, finished_at=utc_now())
        except Exception as exc:
            set_batch_status(wh, batch_id, "fail", finished_at=utc_now(), fail_reason=f"{type(exc).__name__}: {exc}"[:4000])
            report.update(status="fail", error=f"{type(exc).__name__}: {exc}", finished_at=utc_now())
            atomic_write_json(Path(inputs.report_dir) / f"{batch_id}.json", report)
            raise
    atomic_write_json(Path(inputs.report_dir) / f"{batch_id}.json", report)
    return report


def _save_notes(wh, batch_id: str, notes: dict[str, Any]) -> None:
    cursor = wh.cursor()
    try:
        cursor.execute("UPDATE etl_import_batches SET notes=%s WHERE batch_id=%s",
                       (json.dumps(notes, ensure_ascii=False, sort_keys=True, default=str), batch_id))
        wh.commit()
    finally:
        cursor.close()

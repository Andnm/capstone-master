"""`dataset_build_manifests`: tao (init) + cac chuyen trang thai cua state machine (spec muc 3b, 18).

Thu tu step co dinh: initialized -> causal_references -> item_matches -> samples_labels -> split ->
features_labels -> validation -> status=pass. `active_step IS NOT NULL` = step do dang chay HOAC da crash/fail
giua chung (bang chung phuc hoi). Moi chuyen trang thai la mot transaction ngan, commit ngay.
"""
from __future__ import annotations

import datetime as dt
import json
from typing import Any

from . import env  # noqa: F401
from .config import DATASET_VERSION_RE, PURPOSES, config_sha256, verify_config_projection
from .db import current_database, execute, fetch_all, scalar, utc_now

STEPS: tuple[str, ...] = ("causal_references", "item_matches", "samples_labels", "split", "features_labels", "validation")
LAST_COMPLETED_VALUES: tuple[str, ...] = ("initialized",) + STEPS

# Cot cua manifest bi xoa khi mot step upstream bi rerun/rebuild (spec muc 18): moi gia tri sau do khong con dung.
_RESET_COLUMNS_BY_STEP: dict[str, tuple[str, ...]] = {
    "causal_references": ("split_train_end", "split_validation_end", "output_parquet_sha256_json",
                          "anomaly_source_member_checksums", "library_versions_json"),
    "item_matches": ("split_train_end", "split_validation_end", "output_parquet_sha256_json",
                     "anomaly_source_member_checksums", "library_versions_json"),
    "samples_labels": ("split_train_end", "split_validation_end", "output_parquet_sha256_json",
                       "anomaly_source_member_checksums", "library_versions_json"),
    "split": ("split_train_end", "split_validation_end", "output_parquet_sha256_json", "library_versions_json"),
    "features_labels": ("output_parquet_sha256_json", "library_versions_json"),
    "validation": (),
}


class ManifestError(RuntimeError):
    pass


def step_index(step: str) -> int:
    return LAST_COMPLETED_VALUES.index(step)


def previous_step(step: str) -> str:
    return LAST_COMPLETED_VALUES[LAST_COMPLETED_VALUES.index(step) - 1]


def next_step(last_completed: str) -> str | None:
    index = LAST_COMPLETED_VALUES.index(last_completed)
    return LAST_COMPLETED_VALUES[index + 1] if index + 1 < len(LAST_COMPLETED_VALUES) else None


def steps_from(step: str) -> tuple[str, ...]:
    return STEPS[STEPS.index(step):]


def resolve_pinned_thresholds(conn, batch_id: str) -> dict[str, Any]:
    """Nguong reference da PIN cua batch: tinh lai `etl_config_sha256()` hien tai va doi chieu voi hash batch da ghi.
    Lech = settings/.env da doi giua luc build warehouse va bay gio -> FAIL cung (khong doc nguong am tham)."""
    from app.warehouse.etl_config import etl_config, etl_config_sha256
    rows = fetch_all(conn, "SELECT etl_config_sha256 FROM etl_import_batches WHERE batch_id=%s", (batch_id,))
    if not rows:
        raise ManifestError(f"batch {batch_id!r} khong ton tai.")
    if rows[0]["etl_config_sha256"] != etl_config_sha256():
        raise ManifestError(
            f"etl_config_sha256 hien tai {etl_config_sha256()} khac gia tri batch {batch_id!r} da pin "
            f"{rows[0]['etl_config_sha256']} - nguong reference da bi doi sau khi build warehouse.")
    config = etl_config()
    return {"min_runs": config["reference_min_runs"], "min_coverage": config["reference_min_coverage"]}


def load_manifest(conn, dataset_version: str, *, for_update: bool = False) -> dict[str, Any]:
    rows = fetch_all(conn, "SELECT * FROM dataset_build_manifests WHERE dataset_version=%s" + (" FOR UPDATE" if for_update else ""),
                     (dataset_version,))
    if not rows:
        raise ManifestError(f"khong co dataset_version {dataset_version!r} trong warehouse hien tai.")
    row = rows[0]
    for column in ("build_config_json", "library_versions_json", "output_parquet_sha256_json", "retry_overrides_json",
                   "anomaly_source_member_checksums"):
        value = row.get(column)
        if isinstance(value, (str, bytes)):
            row[column] = json.loads(value)
    return row


def manifest_config(row: dict[str, Any]) -> dict[str, Any]:
    return row["build_config_json"]


def verify_manifest(conn, row: dict[str, Any]) -> dict[str, Any]:
    """Batch phai dang PASS va config canonical phai khop hash da ghi (spec muc 18 buoc 2). Tra ve config."""
    config = manifest_config(row)
    problems = verify_config_projection(config, row)
    if problems:
        raise ManifestError("manifest/config khong khop: " + "; ".join(problems) + " - tao dataset_version moi.")
    batch = fetch_all(conn, "SELECT status, warehouse_database FROM etl_import_batches WHERE batch_id=%s", (row["import_batch_id"],))
    if not batch:
        raise ManifestError(f"batch {row['import_batch_id']!r} khong ton tai.")
    if batch[0]["status"] != "pass":
        raise ManifestError(f"batch {row['import_batch_id']!r} status={batch[0]['status']!r}, can 'pass'.")
    if batch[0]["warehouse_database"] != current_database(conn):
        raise ManifestError(
            f"batch ghi warehouse_database={batch[0]['warehouse_database']!r} nhung dang o {current_database(conn)!r}.")
    return config


def init_dataset_build(conn, *, dataset_version: str, config: dict[str, Any], now: dt.datetime | None = None) -> dict[str, Any]:
    """Lenh DUY NHAT tao `dataset_version` (spec muc 19). Ghi NGAY toan bo cau hinh bat bien; chi split_*/output de NULL."""
    if not DATASET_VERSION_RE.match(dataset_version):
        raise ManifestError(f"dataset_version {dataset_version!r} khong khop {DATASET_VERSION_RE.pattern}")
    if config["purpose"] not in PURPOSES:
        raise ManifestError(f"purpose {config['purpose']!r} khong hop le")
    batch_id = config["import_batch_id"]
    batch = fetch_all(conn, "SELECT status, warehouse_database FROM etl_import_batches WHERE batch_id=%s", (batch_id,))
    if not batch:
        raise ManifestError(f"batch {batch_id!r} khong ton tai trong {current_database(conn)!r}.")
    if batch[0]["status"] != "pass":
        raise ManifestError(f"batch {batch_id!r} status={batch[0]['status']!r}, chi build dataset tren batch PASS.")
    if batch[0]["warehouse_database"] != current_database(conn):
        raise ManifestError(f"batch {batch_id!r} thuoc {batch[0]['warehouse_database']!r}, khong phai {current_database(conn)!r}.")
    if scalar(conn, "SELECT COUNT(*) FROM dataset_build_manifests WHERE dataset_version=%s", (dataset_version,)):
        raise ManifestError(f"dataset_version {dataset_version!r} da ton tai - dung version moi (khong sua config).")
    now = now or utc_now()
    anomaly = config["anomaly"]
    cutoff = (dt.datetime.fromisoformat(anomaly["cutoff_at"].replace("Z", "")) if anomaly["cutoff_at"] else None)
    try:
        execute(conn,
                """INSERT INTO dataset_build_manifests
                   (dataset_version, import_batch_id, status, started_at, reference_algorithm_version, label_config_sha256,
                    feature_config_sha256, build_config_json, build_config_sha256, purge_gap_days, random_seed,
                    last_completed_step, active_step, active_step_attempt, max_step_attempts, created_at,
                    anomaly_registry_file_sha256, anomaly_registry_mode, anomaly_registry_cutoff_at)
                   VALUES (%s,%s,'running',%s,%s,%s,%s,%s,%s,%s,%s,'initialized',NULL,0,3,%s,%s,%s,%s)""",
                (dataset_version, batch_id, now, config["reference_algorithm_version"], config["label_config_sha256"],
                 config["feature_config_sha256"], json.dumps(config, sort_keys=True, ensure_ascii=False), config_sha256(config),
                 config["purge_gap_days"], config["random_seed"], now, anomaly["registry_file_sha256"], anomaly["mode"], cutoff))
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    return load_manifest(conn, dataset_version)


# --------------------------------------------------------------------------- state transitions

def start_step(conn, dataset_version: str, step: str, *, now: dt.datetime | None = None) -> None:
    """Ghi dau vet step (marker) TRUOC khi ghi bat ky output nao - mot transaction."""
    now = now or utc_now()
    try:
        count = execute(conn,
                        """UPDATE dataset_build_manifests
                           SET status='running', active_step=%s, active_step_attempt=1, active_step_started_at=%s,
                               active_step_heartbeat_at=%s, fail_reason=NULL, finished_at=NULL
                           WHERE dataset_version=%s AND active_step IS NULL""", (step, now, now, dataset_version))
        if count != 1:
            raise ManifestError(f"start_step({step}): manifest co active_step khac hoac khong ton tai ({count} dong).")
        conn.commit()
    except Exception:
        conn.rollback()
        raise


def complete_step(conn, dataset_version: str, step: str, *, now: dt.datetime | None = None) -> None:
    now = now or utc_now()
    try:
        count = execute(conn,
                        """UPDATE dataset_build_manifests
                           SET last_completed_step=%s, active_step=NULL, active_step_attempt=0, active_step_started_at=NULL,
                               active_step_heartbeat_at=NULL, last_step_finished_at=%s
                           WHERE dataset_version=%s AND active_step=%s""", (step, now, dataset_version, step))
        if count != 1:
            raise ManifestError(f"complete_step({step}): active_step khong khop ({count} dong).")
        conn.commit()
    except Exception:
        conn.rollback()
        raise


def fail_step(conn, dataset_version: str, reason: str) -> None:
    """Giu NGUYEN active_step (bang chung phuc hoi); chi doi status/fail_reason."""
    try:
        execute(conn, "UPDATE dataset_build_manifests SET status='fail', fail_reason=%s WHERE dataset_version=%s",
                (reason[:4000], dataset_version))
        conn.commit()
    except Exception:
        conn.rollback()
        raise


def mark_pass(conn, dataset_version: str, *, now: dt.datetime | None = None) -> None:
    now = now or utc_now()
    try:
        execute(conn,
                """UPDATE dataset_build_manifests SET status='pass', finished_at=%s, fail_reason=NULL,
                       last_completed_step='validation', active_step=NULL, active_step_attempt=0,
                       active_step_started_at=NULL, active_step_heartbeat_at=NULL, last_step_finished_at=%s
                   WHERE dataset_version=%s""", (now, now, dataset_version))
        conn.commit()
    except Exception:
        conn.rollback()
        raise


def begin_retry_attempt(conn, dataset_version: str, *, now: dt.datetime | None = None) -> None:
    now = now or utc_now()
    try:
        execute(conn,
                """UPDATE dataset_build_manifests SET status='running', fail_reason=NULL, active_step_attempt=active_step_attempt+1,
                       active_step_started_at=%s, active_step_heartbeat_at=%s WHERE dataset_version=%s AND active_step IS NOT NULL""",
                (now, now, dataset_version))
        conn.commit()
    except Exception:
        conn.rollback()
        raise


def reset_manifest_outputs(conn, dataset_version: str, step: str) -> None:
    """Xoa cac cot ket qua khong con dung khi `step` (va downstream) bi cleanup. Khong commit."""
    columns = _RESET_COLUMNS_BY_STEP[step]
    # Moi rerun: status ve 'running', xoa finished_at/fail_reason (CHECK chk_dataset_fail_reason doi fail_reason khi status='fail').
    sets = ["status='running'", "finished_at=NULL", "fail_reason=NULL"] + [f"{column}=NULL" for column in columns]
    execute(conn, f"UPDATE dataset_build_manifests SET {', '.join(sets)} WHERE dataset_version=%s", (dataset_version,))


def write_recovery_marker(conn, dataset_version: str, step: str, *, now: dt.datetime | None = None) -> None:
    """`--rebuild-from STEP`: ghi marker TRUOC cleanup (spec muc 18): ha last_completed_step ve step lien truoc."""
    now = now or utc_now()
    try:
        count = execute(conn,
                        """UPDATE dataset_build_manifests SET status='running', active_step=%s, active_step_attempt=1,
                               active_step_started_at=%s, active_step_heartbeat_at=%s, last_completed_step=%s,
                               finished_at=NULL, fail_reason=NULL
                           WHERE dataset_version=%s AND active_step IS NULL""",
                        (step, now, now, previous_step(step), dataset_version))
        if count != 1:
            raise ManifestError("rebuild-from: manifest dang co active_step - phuc hoi step do truoc (--apply).")
        conn.commit()
    except Exception:
        conn.rollback()
        raise


def append_retry_override(conn, dataset_version: str, entry: dict[str, Any], *, now: dt.datetime | None = None) -> None:
    """Ghi audit override (step, previous attempts, reason, actor, UTC, code version), dat attempt ve 1. `entry` phai JSON-hoa duoc."""
    now = now or utc_now()
    row = load_manifest(conn, dataset_version, for_update=True)
    overrides = list(row.get("retry_overrides_json") or [])
    overrides.append(entry)
    try:
        execute(conn, "UPDATE dataset_build_manifests SET retry_overrides_json=%s, active_step_attempt=1, status='running', "
                      "fail_reason=NULL, active_step_started_at=%s, active_step_heartbeat_at=%s WHERE dataset_version=%s",
                (json.dumps(overrides, ensure_ascii=False, sort_keys=True), now, now, dataset_version))
        conn.commit()
    except Exception:
        conn.rollback()
        raise


def heartbeat_is_stale(row: dict[str, Any], *, now: dt.datetime, stale_seconds: int = 300) -> bool:
    """Trang thai HIEN THI suy ra (khong them gia tri enum): running + co active_step + heartbeat cu."""
    heartbeat = row.get("active_step_heartbeat_at")
    return bool(row["status"] == "running" and row.get("active_step") and heartbeat
                and (now - heartbeat).total_seconds() > stale_seconds)

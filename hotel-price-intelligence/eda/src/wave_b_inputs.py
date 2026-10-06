"""Wave B (Curated ML EDA) - dau vao TUONG MINH + preflight fail-closed (GPT file 52 muc 1).

Khong co "dataset moi nhat" hay "any ready": nguoi goi phai chi ro `database`, `batch_id`, `dataset_version` va thu muc artifact
(`samples.parquet` + `output_checksums.json` ...). Preflight dung ngay (khong query phan tich nao) khi:
  * manifest dataset khong `pass`, hoac thuoc batch khac `batch_id` da chi dinh;
  * mot trong ba bang `ml_reference_assignments` / `ml_item_reference_matches` / `ml_samples` rong cho DUNG version;
  * file artifact khong khop hash da luu trong `dataset_build_manifests.output_parquet_sha256_json` (hoac thieu `samples.parquet`);
  * cot `dataset_version` trong Parquet khong dong nhat voi version da chi dinh, hoac so dong khac so mau duoc chon trong DB.

Artifact cu (truoc builder 1.4: khong co `dataset_contract.json`; truoc feature 1.2: khong co cot strata) van doc duoc nhung duoc GHI NHAN dung nhu vay
trong input manifest (`artifact_generation`), khong sinh contract/cot gia va khong di qua verifier training 1.4.
"""
from __future__ import annotations

import hashlib
import json
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterator

import pandas as pd
import pyarrow.parquet as pq

import db

REQUIRED_ML_TABLES = ("ml_reference_assignments", "ml_item_reference_matches", "ml_samples")
SAMPLES_FILE = "samples.parquet"
CONTRACT_FILE = "dataset_contract.json"
STRATA_COLUMNS = ("prediction_match_status", "label_match_status_h1", "label_match_status_h3", "label_match_status_h7", "label_match_status_h14")


class PreflightError(RuntimeError):
    """Dau vao Wave B khong du dieu kien - khong chay phan tich."""


@dataclass(frozen=True)
class WaveBInputs:
    database: str
    batch_id: str
    dataset_version: str
    dataset_dir: Path

    def to_manifest_dict(self) -> dict[str, str]:
        return {"database": self.database, "batch_id": self.batch_id, "dataset_version": self.dataset_version, "dataset_dir": str(self.dataset_dir)}


@contextmanager
def connect_explicit(database: str, batch_id: str) -> Iterator[tuple[Any, db.WarehouseSnapshot]]:
    """Ket noi READ-ONLY vao DATABASE DUOC CHI DINH (khong qua pointer), xac nhan batch 'pass' + manifest nguon khop - cung `db._verify_snapshot`."""
    with db._connect_raw(database) as conn:
        try:
            db.enforce_read_only_session(conn)
            snapshot = db._verify_snapshot(conn, {"batch_id": batch_id, "warehouse_database": database})
            yield conn, snapshot
        finally:
            conn.rollback()


def file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with open(path, "rb") as handle:
        for chunk in iter(lambda: handle.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _scalar(conn, sql: str, params: tuple = ()) -> int:
    frame = db.read_sql(conn, sql, params)
    return int(frame.iloc[0, 0]) if len(frame) else 0


def _json_value(value: Any) -> Any:
    if value is None or isinstance(value, (dict, list)):
        return value
    return json.loads(value)


def preflight(conn, inputs: WaveBInputs) -> dict[str, Any]:
    """Tra ve preflight dict (ghi vao input manifest); raise `PreflightError` voi MOI van de tim thay (khong dung o van de dau tien)."""
    problems: list[str] = []
    manifest_rows = db.read_sql(conn, "SELECT * FROM dataset_build_manifests WHERE dataset_version = %s", (inputs.dataset_version,))
    if manifest_rows.empty:
        raise PreflightError(f"dataset_version {inputs.dataset_version!r} khong co trong dataset_build_manifests cua {inputs.database}.")
    manifest = manifest_rows.iloc[0].to_dict()
    if manifest["status"] != "pass":
        problems.append(f"manifest status={manifest['status']!r} != 'pass' (last_completed_step={manifest.get('last_completed_step')!r})")
    if manifest["import_batch_id"] != inputs.batch_id:
        problems.append(f"manifest thuoc batch {manifest['import_batch_id']!r}, khong phai batch da chi dinh {inputs.batch_id!r}")
    counts: dict[str, int] = {}
    for table in REQUIRED_ML_TABLES:
        counts[table] = _scalar(conn, f"SELECT COUNT(*) FROM {table} WHERE dataset_version = %s", (inputs.dataset_version,))
        if counts[table] == 0:
            problems.append(f"{table} rong cho dataset_version {inputs.dataset_version!r}")
    selected = _scalar(conn, "SELECT COUNT(*) FROM ml_samples WHERE dataset_version = %s AND is_daily_snapshot_selected = TRUE", (inputs.dataset_version,))
    stored = _json_value(manifest.get("output_parquet_sha256_json")) or {}
    samples_path = inputs.dataset_dir / SAMPLES_FILE
    verified: dict[str, str] = {}
    if not samples_path.exists():
        problems.append(f"khong tim thay {samples_path}")
    if SAMPLES_FILE not in stored:
        problems.append(f"manifest khong co checksum {SAMPLES_FILE}")
    for name, entry in sorted(stored.items()):
        path = inputs.dataset_dir / name
        if not path.exists():
            problems.append(f"{name}: co trong checksum manifest nhung khong co trong {inputs.dataset_dir}")
            continue
        actual = file_sha256(path)
        verified[name] = actual
        if actual != entry.get("file_sha256"):
            problems.append(f"{name}: sha256 that {actual[:12]}... != manifest {str(entry.get('file_sha256'))[:12]}...")
    frame_info: dict[str, Any] = {}
    if samples_path.exists():
        header = pd.read_parquet(samples_path, columns=["dataset_version"])
        versions = sorted(set(header["dataset_version"].astype(str)))
        if versions != [inputs.dataset_version]:
            problems.append(f"cot dataset_version trong Parquet = {versions} != {[inputs.dataset_version]}")
        if len(header) != selected:
            problems.append(f"Parquet {len(header)} dong != {selected} mau duoc chon (is_daily_snapshot_selected) trong DB")
        columns = pq.read_schema(samples_path).names
        frame_info = {"rows": int(len(header)), "columns": len(columns), "has_strata_columns": all(c in columns for c in STRATA_COLUMNS)}
    contract_path = inputs.dataset_dir / CONTRACT_FILE
    config = _json_value(manifest.get("build_config_json")) or {}
    feature_config = config.get("feature_config") or {}
    generation = {
        "has_dataset_contract": contract_path.exists(), "has_strata_columns": bool(frame_info.get("has_strata_columns")),
        "builder_version": config.get("builder_version"), "feature_version": feature_config.get("version"),
        "evaluation_horizons": config.get("evaluation_horizons"), "purpose": config.get("purpose"),
        "builder_code_sha256": (config.get("builder_code") or {}).get("code_sha256"), "legacy": not contract_path.exists(),
    }
    if problems:
        raise PreflightError("Wave B preflight FAIL:\n  - " + "\n  - ".join(problems))
    return {
        "dataset_version": inputs.dataset_version, "manifest_status": manifest["status"], "import_batch_id": manifest["import_batch_id"],
        "ml_table_rows": counts, "selected_samples": selected, "parquet": frame_info, "verified_file_sha256": verified,
        "reference_algorithm_version": manifest.get("reference_algorithm_version"),
        "split_train_end": str(manifest.get("split_train_end")), "split_validation_end": str(manifest.get("split_validation_end")),
        "purge_gap_days": int(manifest["purge_gap_days"]), "artifact_generation": generation,
        "build_config_sha256": manifest.get("build_config_sha256"), "anomaly_registry_mode": manifest.get("anomaly_registry_mode"),
        "anomaly_registry_cutoff_at": str(manifest.get("anomaly_registry_cutoff_at")),
        "feature_config_sha256": manifest.get("feature_config_sha256"), "label_config_sha256": manifest.get("label_config_sha256"),
    }

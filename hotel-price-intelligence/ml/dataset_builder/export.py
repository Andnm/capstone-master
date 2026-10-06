"""Step `features_labels` (spec muc 3b buoc 6): feature + nhan -> Parquet versioned + data dictionary + bao cao + checksum.

Output o `outputs/datasets/<dataset_version>/`; ghi vao `tmp/` truoc roi publish tung file bang `os.replace` (atomic).
`output_parquet_sha256_json` luu HAI loai hash: `file_sha256` (toan ven file) va `content_sha256` (noi dung dong da sap xep
theo khoa tu nhien, KHONG co technical ID `warehouse_record_id`) - hai lan build cung input/config phai co cung `content_sha256`
(spec muc 16); `file_sha256` chi de doi chieu file tren dia.
"""
from __future__ import annotations

import csv
import datetime as dt
import hashlib
import json
import os
import platform
import shutil
import subprocess
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

from . import BUILDER_VERSION
from . import env
from .calendar_features import CALENDAR_NAME, CalendarFeatures, CalendarInputError
from .config import config_sha256
from .db import execute
from .dictionary import dictionary_rows
from .features import build_feature_frame, output_columns
from .reports import coverage_report, sufficiency_report

SAMPLES_FILE = "samples.parquet"
CONTRACT_NAME = "dataset_contract.json"                 # hop dong horizon/split cua dataset (GPT file 50): nam trong checksum DB + output_checksums.json
CONTRACT_VERSION = 1
CALENDAR_SNAPSHOT = f"inputs/{CALENDAR_NAME}"          # bytes goc cua lich da dung (R3-M1), nam trong checksum DB + output_checksums.json
CALENDAR_MANIFEST = "calendar_input.json"
KEY_COLUMNS = ["hotel_id", "checkin_date", "canonical_series_id", "vn_observation_date"]
TECHNICAL_COLUMNS = ["warehouse_record_id", "dataset_version"]   # ID ky thuat + nhan version (khong phai noi dung)


def file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with open(path, "rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def content_sha256(frame: pd.DataFrame) -> str:
    """Hash noi dung theo khoa tu nhien, khong technical ID (spec muc 16)."""
    columns = [c for c in frame.columns if c not in TECHNICAL_COLUMNS]
    ordered = frame[columns].sort_values(KEY_COLUMNS, kind="mergesort").reset_index(drop=True)
    row_hashes = pd.util.hash_pandas_object(ordered, index=False).to_numpy(dtype="uint64")
    digest = hashlib.sha256()
    digest.update(json.dumps(columns).encode("utf-8"))
    digest.update(row_hashes.tobytes())
    return digest.hexdigest()


def library_versions(config: dict[str, Any] | None = None) -> dict[str, Any]:
    import mysql.connector
    import pyarrow
    return {"python": platform.python_version(), "pandas": pd.__version__, "numpy": np.__version__, "pyarrow": pyarrow.__version__,
            "mysql_connector": mysql.connector.__version__, "builder_version": BUILDER_VERSION, "code": git_state(),
            "builder_code_sha256": ((config or {}).get("builder_code") or {}).get("code_sha256")}


def git_state() -> dict[str, Any]:
    try:
        head = subprocess.run(["git", "-C", str(env.REPO_ROOT), "rev-parse", "HEAD"], capture_output=True, text=True, timeout=30, check=True).stdout.strip()
        dirty = subprocess.run(["git", "-C", str(env.REPO_ROOT), "status", "--porcelain", "--untracked-files=all", "--", "hotel-price-intelligence/ml"],
                               capture_output=True, text=True, timeout=30, check=True).stdout.strip()
        return {"head": head, "ml_dir_dirty": bool(dirty), "ml_dirty_files": len(dirty.splitlines()) if dirty else 0}
    except Exception as exc:  # noqa: BLE001 - provenance khong duoc lam hong build; ghi nhan loi
        return {"head": None, "error": f"{type(exc).__name__}: {exc}"}


class ProvenanceError(RuntimeError):
    pass


def assert_official_provenance(purpose: str | None, state: dict[str, Any]) -> None:
    """Dataset `official` PHAI tai lap duoc tu mot commit: khong co HEAD (git loi) hoac `ml/` dirty => tu choi.
    Chi la lop phong thu thu hai: cong chinh nam o `runner._session` (code_identity: moi file ml + dependency backend, TRUOC moi step/ghi - R2-M1).
    rehearsal/dev duoc dirty, `git_state` van ghi nhan vao manifest."""
    if purpose != "official":
        return
    if not state.get("head"):
        raise ProvenanceError(f"official: khong xac dinh duoc git HEAD ({state.get('error')}) - khong the tai lap dataset.")
    if state.get("ml_dir_dirty"):
        raise ProvenanceError(f"official: ml/ co {state.get('ml_dirty_files')} file chua commit - commit truoc khi build official.")


def dataset_contract(*, dataset_version: str, config: dict[str, Any], split_report: dict[str, Any] | None, sufficiency: dict[str, Any], calendar_sha: str) -> dict[str, Any]:
    """Hop dong horizon cua artifact, XAC DINH (khong timestamp): horizon duoc danh gia, purge, bien split that, trang thai sufficiency cua tung horizon, nhan dang
    ma/config/lich. Training/Colab doc file nay de tu choi horizon ngoai whitelist; `official` yeu cau moi evaluation horizon `primary_eligible`."""
    plan = (split_report or {}).get("plan", {})
    keys = ("train_start", "train_end", "validation_start", "validation_end", "test_start", "test_end", "purge_gap_days", "policy_path", "feasible_horizon")
    return {
        "contract_version": CONTRACT_VERSION, "dataset_version": dataset_version, "purpose": config["purpose"],
        "evaluation_horizons": list(config["evaluation_horizons"]), "computed_label_horizons": list(config["computed_label_horizons"]),
        "purge_gap_days": int(config["purge_gap_days"]),
        "split_plan": {k: plan.get(k) for k in keys},
        "sufficiency_status": {name: entry["status"] for name, entry in sufficiency["horizons"].items()},
        "builder_version": BUILDER_VERSION, "builder_code_sha256": (config.get("builder_code") or {}).get("code_sha256"),
        "build_config_sha256": config_sha256(config), "calendar_sha256": calendar_sha,
    }


def _json_dump(path: Path, payload: Any) -> None:
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True, default=str), encoding="utf-8")


def build_features_labels(conn, *, dataset_version: str, config: dict[str, Any], output_root: Path, report_dir: Path) -> dict[str, Any]:
    assert_official_provenance(config.get("purpose"), git_state())   # fail-closed truoc khi doc/ghi bat cu thu gi
    pinned = (config.get("calendar_input") or {}).get("sha256")
    if not pinned:
        raise CalendarInputError("config khong ghim `calendar_input` - dataset tao bang ban builder cu, khong xac minh duoc input lich.")
    calendar = CalendarFeatures.load(expected_sha256=pinned)          # lech hash => fail TRUOC khi doc DB / ghi output
    frame = build_feature_frame(conn, dataset_version=dataset_version, config=config, calendar=calendar)
    columns = output_columns(config)
    missing = [c for c in columns if c not in frame.columns]
    if missing:
        raise RuntimeError(f"thieu cot trong frame: {missing}")
    out = frame[columns].copy()
    for column in ("checkin_date", "vn_observation_date"):
        out[column] = out[column].dt.date                       # date32 trong Parquet
    split_report = _load_split_report(report_dir)
    coverage = coverage_report(frame, split_report)
    sufficiency = sufficiency_report(frame, config)

    final_dir = output_root / dataset_version
    tmp_dir = final_dir / "tmp"
    if tmp_dir.exists():
        shutil.rmtree(tmp_dir)
    tmp_dir.mkdir(parents=True, exist_ok=True)
    out.to_parquet(tmp_dir / SAMPLES_FILE, engine="pyarrow", index=False, compression="snappy")
    with open(tmp_dir / "data_dictionary.csv", "w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=["column", "group", "description", "formula_or_source", "leakage_note"])
        writer.writeheader()
        writer.writerows(dictionary_rows(columns))
    _json_dump(tmp_dir / "coverage_report.json", coverage)
    _json_dump(tmp_dir / "sufficiency_report.json", sufficiency)
    _json_dump(tmp_dir / CONTRACT_NAME, dataset_contract(dataset_version=dataset_version, config=config, split_report=split_report, sufficiency=sufficiency,
                                                         calendar_sha=calendar.sha256))
    (tmp_dir / "inputs").mkdir()
    (tmp_dir / "inputs" / CALENDAR_NAME).write_bytes(calendar.raw)    # snapshot NGUYEN bytes da kiem hash va da dung de tinh feature
    _json_dump(tmp_dir / CALENDAR_MANIFEST, {"vn_holidays_csv_sha256": calendar.sha256, "name": CALENDAR_NAME, "snapshot": CALENDAR_SNAPSHOT,
                                              "bytes": len(calendar.raw), "pinned_in_config": pinned})
    reports_tmp = tmp_dir / "reports"
    reports_tmp.mkdir(exist_ok=True)
    if report_dir.exists():
        for source in report_dir.glob("*.json"):
            shutil.copy2(source, reports_tmp / source.name)
    # kiem doc lai: Parquet doc duoc, dung so dong/cot
    check = pd.read_parquet(tmp_dir / SAMPLES_FILE)
    if len(check) != len(out) or list(check.columns) != columns:
        raise RuntimeError("Parquet doc lai khong khop so dong/cot da ghi.")
    hashes = {
        SAMPLES_FILE: {"file_sha256": file_sha256(tmp_dir / SAMPLES_FILE), "content_sha256": content_sha256(out), "rows": int(len(out)),
                       "columns": len(columns)},
        "data_dictionary.csv": {"file_sha256": file_sha256(tmp_dir / "data_dictionary.csv")},
        "coverage_report.json": {"file_sha256": file_sha256(tmp_dir / "coverage_report.json")},
        "sufficiency_report.json": {"file_sha256": file_sha256(tmp_dir / "sufficiency_report.json")},
        CONTRACT_NAME: {"file_sha256": file_sha256(tmp_dir / CONTRACT_NAME)},
        CALENDAR_MANIFEST: {"file_sha256": file_sha256(tmp_dir / CALENDAR_MANIFEST)},
        CALENDAR_SNAPSHOT: {"file_sha256": file_sha256(tmp_dir / CALENDAR_SNAPSHOT)},
    }
    _json_dump(tmp_dir / "output_checksums.json", hashes)
    for name in [SAMPLES_FILE, "data_dictionary.csv", "coverage_report.json", "sufficiency_report.json", CONTRACT_NAME, CALENDAR_MANIFEST, "output_checksums.json"]:
        os.replace(tmp_dir / name, final_dir / name)               # publish atomic tung file
    final_inputs = final_dir / "inputs"
    if final_inputs.exists():
        shutil.rmtree(final_inputs)
    os.replace(tmp_dir / "inputs", final_inputs)
    final_reports = final_dir / "reports"
    if final_reports.exists():
        shutil.rmtree(final_reports)
    os.replace(reports_tmp, final_reports)
    shutil.rmtree(tmp_dir, ignore_errors=True)
    try:
        execute(conn, "UPDATE dataset_build_manifests SET output_parquet_sha256_json=%s, library_versions_json=%s WHERE dataset_version=%s",
                (json.dumps(hashes, sort_keys=True), json.dumps(library_versions(config), sort_keys=True, default=str), dataset_version))
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    return {"output_dir": str(final_dir), "rows": int(len(out)), "columns": len(columns), "content_sha256": hashes[SAMPLES_FILE]["content_sha256"],
            "file_sha256": hashes[SAMPLES_FILE]["file_sha256"], "sufficiency": {h: v["status"] for h, v in sufficiency["horizons"].items()},
            "vn_holidays_csv_sha256": calendar.sha256}


def _load_split_report(report_dir: Path) -> dict[str, Any] | None:
    path = report_dir / "split.json"
    return json.loads(path.read_text(encoding="utf-8")) if path.exists() else None

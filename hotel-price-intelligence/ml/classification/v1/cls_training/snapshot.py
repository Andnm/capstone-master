"""Baseline snapshot bat bien (EVAL_PROTOCOL §9): du doan regression record-level, mang theo goi Colab de panel/CI/claim khong phu thuoc `ml/regression/models/`.

`export_snapshot` CHI DOC nguon regression (khong sua, khong retrain) va doi chieu voi AUTHORITY cua chinh run do: `run_manifest.json` (state, horizon, hash file) va report
(`test.locked`, `test.prespecified_models`, `validation_table`). Cot du doan nao duoc CHAM tren split nao KHONG suy tu gia tri ma tu report; gia tri phai khop:
cot duoc cham phai huu han VA > 0 (gia), cot khong duoc cham phai NaN toan bo split. Lech => SnapshotError (khong tu doan).
`load_snapshot` la validator cua goi: manifest + pin BAT BUOC + SHA + tap record bang chinh xac + `current_price`, `y_true` bang nhau tung BIT float64 (view uint64) + nhan luu tru.
`content_sha256` la hash CSV chuan tac (gia dinh dinh dang cua pandas); pin chinh la `snapshot_file_sha256`, khong hua dong nhat noi dung tuyet doi xuyen phien ban thu vien.
"""
from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

from .data import LoadedDataset
from .labels import LabelContractError, direction_from_pct, pct_change
from .upstream.provenance import file_sha256

KEY = ["hotel_id", "checkin_date", "canonical_series_id", "vn_observation_date", "split"]
BASE_COLUMNS = ["warehouse_record_id", *KEY, "current_price", "y_true", "y_direction"]
SNAPSHOT_VERSION = 2
EVAL_SPLITS = ("validation", "test")


class SnapshotError(RuntimeError):
    pass


def snapshot_paths(out_dir: Path, horizon: int) -> tuple[Path, Path]:
    return out_dir / f"baseline_snapshot_h{horizon}.parquet", out_dir / f"baseline_snapshot_h{horizon}_manifest.json"


def bitwise_equal(a: Any, b: Any) -> bool:
    """Bang nhau tung bit (float64 -> uint64). Cam dtype khac float64 (cast co the che khac biet); khong coi +0 == -0 hay NaN payload khac nhau la bang nhau."""
    x, y = np.asarray(a), np.asarray(b)
    if x.dtype != np.float64 or y.dtype != np.float64:
        raise SnapshotError(f"so sanh bitwise can float64 (nhan {x.dtype}, {y.dtype})")
    return x.shape == y.shape and bool(np.array_equal(np.ascontiguousarray(x).view(np.uint64), np.ascontiguousarray(y).view(np.uint64)))


def _norm_dates(df: pd.DataFrame) -> pd.DataFrame:
    df = df.copy()
    for col in ("checkin_date", "vn_observation_date"):
        df[col] = pd.to_datetime(df[col]).dt.strftime("%Y-%m-%d")
    return df


def content_sha256(df: pd.DataFrame) -> str:
    return hashlib.sha256(df.to_csv(index=False, float_format="%.17g", lineterminator="\n").encode("utf-8")).hexdigest()


def _primary_eval_rows(loaded: LoadedDataset) -> pd.DataFrame:
    h = loaded.horizon
    frame = pd.concat([loaded.frames[name] for name in EVAL_SPLITS], ignore_index=True)
    cols = ["warehouse_record_id", *KEY, "current_price", f"y_price_h{h}", f"y_direction_h{h}", "price_velocity"]
    out = _norm_dates(frame[cols].rename(columns={f"y_price_h{h}": "y_true", f"y_direction_h{h}": "y_direction"}))
    if out.duplicated(KEY).any() or out["warehouse_record_id"].duplicated().any():
        raise SnapshotError("khung primary co khoa/record trung")
    return out


def _lf_sha(path: Path) -> str:
    return hashlib.sha256(path.read_text(encoding="utf-8").replace("\r\n", "\n").encode("utf-8")).hexdigest()


def scored_splits(snap: pd.DataFrame, pred_cols: list[str]) -> dict[str, list[str]]:
    """Cot du doan nao co GIA TRI tren split nao (suy tu gia tri). Chi chap nhan tat-ca-hoac-khong theo split; gia tri co mat phai huu han va > 0 (gia). Loi => SnapshotError."""
    out: dict[str, list[str]] = {}
    for col in pred_cols:
        ok_splits = []
        for sp in EVAL_SPLITS:
            s = snap.loc[snap["split"] == sp, col]
            n_nan = int(s.isna().sum())
            if n_nan not in (0, len(s)):
                raise SnapshotError(f"{col}: NaN mot phan tren {sp} ({n_nan}/{len(s)}) - khong ro 'duoc cham' hay khong")
            if n_nan == 0:
                v = s.to_numpy(float)
                if not np.isfinite(v).all():
                    raise SnapshotError(f"{col}: co gia tri vo cuc tren {sp}")
                if not (v > 0).all():
                    raise SnapshotError(f"{col}: du doan duoc cham phai la gia > 0 (co {int((v <= 0).sum())} gia tri <= 0 tren {sp})")
                ok_splits.append(sp)
        out[col] = ok_splits
    return out


def expected_scored_splits(source: dict[str, Any], pred_cols: list[str]) -> dict[str, list[str]]:
    """Cot nao PHAI duoc cham tren split nao theo authority cua report regression: validation = `validation_table`, test = `test.prespecified_models` (khi `test.locked`)."""
    if source.get("test_locked") is not True:
        raise SnapshotError("report: test.locked khong phai true - test chua khoa, khong biet cot nao duoc cham")
    val_names, test_names = list(source.get("validation_table_names") or []), list(source.get("prespecified_models") or [])
    if not val_names or not test_names or len(set(val_names)) != len(val_names) or len(set(test_names)) != len(test_names):
        raise SnapshotError("report: thieu/trung validation_table hoac test.prespecified_models")
    names = {c[len("pred_"):] for c in pred_cols}
    missing_val = sorted(set(val_names) ^ names)
    if missing_val:
        raise SnapshotError(f"cot du doan va validation_table cua report khong khop: {missing_val}")
    not_in_cols = sorted(set(test_names) - names)
    if not_in_cols:
        raise SnapshotError(f"test.prespecified_models co ten khong co cot du doan: {not_in_cols}")
    return {c: [sp for sp, listed in (("validation", c[len("pred_"):] in val_names), ("test", c[len("pred_"):] in test_names)) if listed] for c in pred_cols}


def _scoredness_source(report: dict[str, Any]) -> dict[str, Any]:
    test = report.get("test")
    if not isinstance(test, dict) or not isinstance(report.get("validation_table"), dict):
        raise SnapshotError("report thieu `test` hoac `validation_table`")
    return {"test_locked": test.get("locked"), "prespecified_models": list(test.get("prespecified_models") or []),
            "validation_table_names": list(report["validation_table"]), "report_keys": ["test.locked", "test.prespecified_models", "validation_table"]}


def _check_run_manifest(run_dir: Path, h: int, pred_path: Path, report_path: Path, loaded: LoadedDataset) -> dict[str, Any]:
    rm_path = run_dir / "run_manifest.json"
    if not rm_path.is_file():
        raise SnapshotError(f"thieu {rm_path}")
    rm = json.loads(rm_path.read_text(encoding="utf-8"))
    problems = []
    if rm.get("state") != "pass":
        problems.append(f"run_manifest.state={rm.get('state')!r} != 'pass'")
    if rm.get("expected_horizons") != [h]:
        problems.append(f"run_manifest.expected_horizons={rm.get('expected_horizons')!r} != [{h}]")
    outputs = rm.get("outputs") or {}
    for path in (pred_path, report_path):
        if outputs.get(path.name) != file_sha256(path):
            problems.append(f"{path.name}: sha256 that khac run_manifest.outputs")
    if (rm.get("dataset") or {}).get("samples_file_sha256") != loaded.meta["samples_file_sha256"]:
        problems.append("run_manifest.dataset.samples_file_sha256 khac dataset")
    if problems:
        raise SnapshotError("run_manifest khong khop: " + "; ".join(problems))
    return {"name": rm_path.name, "sha256": file_sha256(rm_path), "state": rm["state"], "run_id": rm.get("run_id"), "official": rm.get("official")}


def export_snapshot(loaded: LoadedDataset, regression_run_dir: Path | str, out_dir: Path | str, *, export_code_files: dict[str, Path]) -> dict[str, Any]:
    h = loaded.horizon
    run_dir, out_dir = Path(regression_run_dir), Path(out_dir)
    pred_path, report_path = run_dir / f"h{h}_predictions_v3.parquet", run_dir / f"h{h}_report.json"
    for p in (pred_path, report_path):
        if not p.is_file():
            raise SnapshotError(f"thieu {p}")
    run_manifest = _check_run_manifest(run_dir, h, pred_path, report_path, loaded)
    report = json.loads(report_path.read_text(encoding="utf-8"))
    if report.get("dataset", {}).get("samples_file_sha256") != loaded.meta["samples_file_sha256"]:
        raise SnapshotError("report regression khong tham chieu dung dataset (samples_file_sha256 khac)")
    if int(report.get("horizon", -1)) != h:
        raise SnapshotError(f"report.horizon {report.get('horizon')} != {h}")
    champions = {arm: (report.get("champions") or {}).get(arm, {}).get("winner") for arm in ("A", "B")}
    pr = _norm_dates(pd.read_parquet(pred_path))
    pred_cols = [c for c in pr.columns if c.startswith("pred_")]
    if not pred_cols:
        raise SnapshotError("file du doan khong co cot pred_*")
    if pr.duplicated(KEY).any():
        raise SnapshotError("file du doan regression co khoa trung")
    base = _primary_eval_rows(loaded)
    merged = pr.merge(base[["warehouse_record_id", *KEY, "y_direction"]], on=KEY, how="left", validate="one_to_one")
    if merged["warehouse_record_id"].isna().any():
        raise SnapshotError(f"{int(merged['warehouse_record_id'].isna().sum())} hang du doan khong noi duoc ve khung primary cua dataset")
    if set(merged["warehouse_record_id"].astype("int64")) != set(base["warehouse_record_id"].astype("int64")):
        raise SnapshotError("tap warehouse_record_id cua du doan != tap primary validation+test cua dataset")
    base_by_id = base.set_index("warehouse_record_id")
    ids = merged["warehouse_record_id"].astype("int64").to_numpy()
    if not bitwise_equal(merged["current_price"].to_numpy(), base_by_id.loc[ids, "current_price"].to_numpy()):
        raise SnapshotError("current_price cua du doan != dataset (bitwise float64)")
    if not bitwise_equal(merged["y_true"].to_numpy(), base_by_id.loc[ids, "y_true"].to_numpy()):
        raise SnapshotError("y_true cua du doan != y_price_h cua dataset (bitwise float64)")
    merged["warehouse_record_id"] = ids
    snap = merged[BASE_COLUMNS + pred_cols].sort_values("warehouse_record_id", kind="mergesort").reset_index(drop=True)
    for col in ("current_price", "y_true", *pred_cols):
        snap[col] = snap[col].astype("float64")
    source_scored = _scoredness_source(report)
    expected = expected_scored_splits(source_scored, pred_cols)
    derived = scored_splits(snap, pred_cols)
    if expected != derived:
        diff = {c: {"report_authority": expected[c], "gia_tri_that": derived[c]} for c in pred_cols if expected[c] != derived[c]}
        raise SnapshotError(f"mau 'duoc cham' suy tu gia tri khac authority cua report: {diff}")
    out_dir.mkdir(parents=True, exist_ok=True)
    parquet_path, manifest_path = snapshot_paths(out_dir, h)
    snap.to_parquet(parquet_path, index=False)
    manifest = {
        "snapshot_version": SNAPSHOT_VERSION, "horizon": h, "dataset_version": loaded.meta["dataset_name"], "rows": int(len(snap)),
        "rows_by_split": {k: int(v) for k, v in snap["split"].value_counts().items()}, "prediction_columns": pred_cols, "prediction_scored_splits": derived,
        "expected_scoredness_source": source_scored,
        "snapshot_file_sha256": file_sha256(parquet_path), "snapshot_content_sha256": content_sha256(snap),
        "source": {"run_id": run_dir.name, "run_dir_provenance_text": str(run_dir), "run_manifest": run_manifest,
                   "predictions_file": {"name": pred_path.name, "sha256": file_sha256(pred_path)}, "report_file": {"name": report_path.name, "sha256": file_sha256(report_path)},
                   "regression_config_sha256": report.get("config_sha256"), "regression_training_version": report.get("training_version"),
                   "regression_dataset_samples_file_sha256": report["dataset"]["samples_file_sha256"]},
        "champions": champions, "routed_columns": [c for c in pred_cols if c.startswith("pred_routed:")],
        "label_arithmetic": "(y - c) / c; up if > +0.02, down if < -0.02 (strict)",
        "dataset_pin": {k: loaded.pin[k] for k in ("dataset_version", "output_checksums_sha256", "dataset_contract_sha256", "samples_file_sha256")},
        "export_code_lf_sha256": {name: _lf_sha(path) for name, path in sorted(export_code_files.items())},
    }
    manifest_path.write_text(json.dumps(manifest, indent=2, ensure_ascii=False, sort_keys=True) + "\n", encoding="utf-8", newline="\n")
    return manifest


def _read_snapshot(out_dir: Path | str, h: int, *, pin: dict[str, Any] | None, allow_unpinned: bool, dataset_pin: dict[str, Any]) -> tuple[pd.DataFrame, dict[str, Any], str, str]:
    """Phan kiem KHONG can dataset: manifest + pin + SHA + cot + hash noi dung + mau 'duoc cham' + khoa duy nhat + (y-c)/c. Tra (snap, manifest, sha file, sha manifest)."""
    if pin is None and not allow_unpinned:
        raise SnapshotError(f"h{h}: thieu pin snapshot - khong duoc chay khong pin")
    parquet_path, manifest_path = snapshot_paths(Path(out_dir), h)
    if not parquet_path.is_file() or not manifest_path.is_file():
        raise SnapshotError(f"thieu snapshot/manifest h{h} trong {out_dir}")
    problems: list[str] = []
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    actual_file, actual_manifest = file_sha256(parquet_path), file_sha256(manifest_path)
    if manifest.get("snapshot_version") != SNAPSHOT_VERSION or int(manifest.get("horizon", -1)) != h:
        problems.append("manifest sai version/horizon")
    if manifest.get("snapshot_file_sha256") != actual_file:
        problems.append("sha256 file snapshot khac manifest")
    if pin is not None and (int(pin["horizon"]) != h or pin["snapshot_file_sha256"] != actual_file or pin["manifest_sha256"] != actual_manifest):
        problems.append("snapshot/manifest khac pin trong config")
    for k in ("dataset_version", "output_checksums_sha256", "dataset_contract_sha256", "samples_file_sha256"):
        if manifest.get("dataset_pin", {}).get(k) != dataset_pin.get(k):
            problems.append(f"manifest.dataset_pin.{k} khac pin dataset")
    if problems:
        raise SnapshotError("; ".join(problems))
    snap = pd.read_parquet(parquet_path)
    if list(snap.columns) != BASE_COLUMNS + list(manifest["prediction_columns"]):
        raise SnapshotError("cot snapshot khac manifest")
    if content_sha256(snap) != manifest["snapshot_content_sha256"]:
        raise SnapshotError("hash noi dung snapshot khac manifest")
    derived = scored_splits(snap, list(manifest["prediction_columns"]))
    expected = expected_scored_splits(manifest.get("expected_scoredness_source") or {}, list(manifest["prediction_columns"]))
    if derived != manifest.get("prediction_scored_splits") or expected != derived:
        raise SnapshotError("mau 'duoc cham' (gia tri) khac prediction_scored_splits trong manifest hoac authority cua report")
    if snap["warehouse_record_id"].duplicated().any() or snap.duplicated(KEY).any():
        raise SnapshotError("snapshot co record/khoa trung")
    return snap, manifest, actual_file, actual_manifest


def _check_label_contract(snap: pd.DataFrame) -> None:
    if (direction_from_pct(pct_change(snap["y_true"], snap["current_price"])) != snap["y_direction"].to_numpy(dtype=object)).any():
        raise LabelContractError("snapshot: y_direction != phep tinh (y-c)/c")


def load_snapshot(out_dir: Path | str, loaded: LoadedDataset, *, pin: dict[str, Any] | None, allow_unpinned: bool = False) -> tuple[pd.DataFrame, dict[str, Any]]:
    """Validator cua goi. `pin` (tu config) BAT BUOC = {horizon, snapshot_file_sha256, manifest_sha256}. `allow_unpinned=True` CHI cho test tong hop (khong co duong tu CLI)."""
    h = loaded.horizon
    snap, manifest, actual_file, actual_manifest = _read_snapshot(out_dir, h, pin=pin, allow_unpinned=allow_unpinned, dataset_pin=loaded.pin)
    base = _primary_eval_rows(loaded).set_index("warehouse_record_id")
    ids = snap["warehouse_record_id"].astype("int64")
    if set(ids) != set(base.index.astype("int64")) or len(snap) != len(base):
        raise SnapshotError(f"tap record snapshot ({len(snap)}) != tap primary val/test cua dataset ({len(base)})")
    aligned = base.loc[ids.to_numpy()]
    if not bitwise_equal(snap["current_price"].to_numpy(), aligned["current_price"].to_numpy()):
        raise SnapshotError("current_price snapshot != dataset (bitwise float64)")
    if not bitwise_equal(snap["y_true"].to_numpy(), aligned["y_true"].to_numpy()):
        raise SnapshotError("y_true snapshot != y_price_h dataset (bitwise float64)")
    if (snap["y_direction"].to_numpy(dtype=object) != aligned["y_direction"].to_numpy(dtype=object)).any():
        raise SnapshotError("y_direction snapshot != nhan luu tru cua dataset")
    for col in ("hotel_id", "checkin_date", "canonical_series_id", "vn_observation_date", "split"):
        if (snap[col].astype(str).to_numpy() != aligned[col].astype(str).to_numpy()).any():
            raise SnapshotError(f"cot {col} snapshot != dataset")
    _check_label_contract(snap)
    out_manifest = dict(manifest)
    out_manifest["_validated"] = {"snapshot_file_sha256": actual_file, "manifest_sha256": actual_manifest, "pin_checked": pin is not None, "compared_with_dataset": True}
    snap = snap.assign(price_velocity=aligned["price_velocity"].reset_index(drop=True).to_numpy(float))
    return snap, out_manifest


def load_snapshot_standalone(out_dir: Path | str, horizon: int, *, pin: dict[str, Any], dataset_pin: dict[str, Any], price_velocity_by_record: pd.Series) -> tuple[pd.DataFrame, dict[str, Any]]:
    """Nap snapshot KHONG can dataset (phan tich lai ket qua da tai ve). Kiem day du moi thu khong dua vao dataset (manifest/pin/SHA/hash noi dung/mau duoc cham/(y-c)/c) va doi chieu `dataset_pin`
    voi manifest. `price_velocity` (chi cho comparator C1) lay tu cot cua CHINH artifact du doan cua run da verify hash - KHONG duoc doi chieu doc lap voi dataset (gioi han duoc ghi trong manifest)."""
    snap, manifest, actual_file, actual_manifest = _read_snapshot(out_dir, int(horizon), pin=pin, allow_unpinned=False, dataset_pin=dataset_pin)
    _check_label_contract(snap)
    missing = sorted(set(snap["warehouse_record_id"]) - set(price_velocity_by_record.index))
    if missing:
        raise SnapshotError(f"thieu price_velocity cho {len(missing)} record primary")
    out_manifest = dict(manifest)
    out_manifest["_validated"] = {"snapshot_file_sha256": actual_file, "manifest_sha256": actual_manifest, "pin_checked": True, "compared_with_dataset": False,
                                  "price_velocity_source": "run_predictions_artifact"}
    return snap.assign(price_velocity=price_velocity_by_record.loc[snap["warehouse_record_id"].to_numpy()].to_numpy(float)), out_manifest

"""Doc dataset da xuat qua kiem toan ven + pin (EVAL_PROTOCOL §1) va dung khung primary / fold (LABEL_CONTRACT §3, EVAL §2). KHONG fit gi.

Chuoi tin cay: `dataset_pins` (trong config) ghim sha256 cua `output_checksums.json` va `dataset_contract.json`; moi entry trong output_checksums phai ton tai va khop hash that
(14 entry + chinh output_checksums.json); `verify_dataset` (ban copy) kiem hop dong split/purge/sufficiency/N1; roi validator nhan chay tren 100% hang usable.
"""
from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

from .labels import LabelContractError, assert_all_classes, class_index, usable_mask, validate_label_contract
from .upstream.frames import SPLITS, horizon_frames
from .upstream.provenance import DatasetVerificationError, file_sha256, verify_dataset, verify_frame
from .upstream.schema import read_dictionary, select_features
from .upstream.cv import PurgedExpandingWindowSplit


class PinError(RuntimeError):
    pass


@dataclass
class LoadedDataset:
    dataset_dir: Path
    horizon: int
    samples: pd.DataFrame
    meta: dict[str, Any]
    features: list[str]
    frames: dict[str, pd.DataFrame]
    frames_info: dict[str, Any]
    label_report: dict[str, Any]
    entries_verified: int = 0
    pin: dict[str, Any] = field(default_factory=dict)
    dictionary: pd.DataFrame | None = None
    unseen_frames: dict[str, pd.DataFrame] | None = None            # hang usable cua hotel KHONG co mau train cung horizon (mau so all-hotels)


def feature_list_sha256(features: list[str]) -> str:
    return hashlib.sha256("\n".join(features).encode("utf-8")).hexdigest()


def verify_all_entries(dataset_dir: Path) -> tuple[int, list[str]]:
    """Moi entry khai bao trong output_checksums.json phai ton tai va khop hash that (verify_dataset chi hash 7 file co ban + N1)."""
    declared = json.loads((dataset_dir / "output_checksums.json").read_text(encoding="utf-8"))
    problems: list[str] = []
    count = 0
    for name, entry in declared.items():
        path = dataset_dir / name
        if not isinstance(entry, dict) or "file_sha256" not in entry:
            problems.append(f"{name}: entry khong co file_sha256")
        elif not path.is_file():
            problems.append(f"{name}: file khong ton tai")
        elif file_sha256(path) != entry["file_sha256"]:
            problems.append(f"{name}: hash that khac khai bao")
        else:
            count += 1
    return count, problems


def check_pin(dataset_dir: Path, meta: dict[str, Any], pin: dict[str, Any], horizon: int) -> None:
    problems = []
    if int(pin["horizon"]) != int(horizon):
        problems.append(f"pin.horizon {pin['horizon']} != {horizon}")
    if pin["dataset_version"] != meta["dataset_name"]:
        problems.append(f"dataset_version {meta['dataset_name']!r} != pin {pin['dataset_version']!r}")
    if file_sha256(dataset_dir / "output_checksums.json") != pin["output_checksums_sha256"]:
        problems.append("sha256 cua output_checksums.json khac pin trong config")
    if meta["verified_file_sha256"]["dataset_contract.json"] != pin["dataset_contract_sha256"]:
        problems.append("sha256 cua dataset_contract.json khac pin")
    if meta["verified_file_sha256"]["samples.parquet"] != pin["samples_file_sha256"]:
        problems.append("sha256 cua samples.parquet khac pin")
    if list(meta["contract"]["evaluation_horizons"]) != [int(h) for h in pin["evaluation_horizons"]] or int(horizon) not in meta["contract"]["evaluation_horizons"]:
        problems.append(f"evaluation_horizons {meta['contract']['evaluation_horizons']} khac pin {pin['evaluation_horizons']} hoac khong chua h{horizon}")
    if meta["declared_rows"] != int(pin["n_rows"]):
        problems.append(f"so dong {meta['declared_rows']} != pin {pin['n_rows']}")
    if problems:
        raise PinError("pin dataset KHONG khop: " + "; ".join(problems))


def load_dataset(dataset_dir: Path | str, pin: dict[str, Any], horizon: int, *, frame_cfg: dict[str, Any] | None = None) -> LoadedDataset:
    dataset_dir = Path(dataset_dir)
    meta = verify_dataset(dataset_dir)                      # hop dong + hash 7 file + calendar + N1 snapshot (ban copy cua checker regression)
    check_pin(dataset_dir, meta, pin, horizon)
    n_ok, problems = verify_all_entries(dataset_dir)
    if problems or n_ok != int(pin["n_entries"]):
        raise DatasetVerificationError(f"entry output_checksums: dung {n_ok}/{pin['n_entries']}; loi {problems}")
    samples = pd.read_parquet(dataset_dir / "samples.parquet")
    verify_frame(samples, meta)
    dictionary = read_dictionary(dataset_dir)
    features = select_features(dictionary)
    if len(features) != int(pin["n_features"]) or feature_list_sha256(features) != pin["feature_list_sha256"]:
        raise PinError(f"danh sach feature ({len(features)}, {feature_list_sha256(features)[:12]}) khac pin ({pin['n_features']}, {str(pin['feature_list_sha256'])[:12]})")
    missing_cols = [c for c in features if c not in samples.columns]
    if missing_cols:
        raise DatasetVerificationError(f"feature khong co trong Parquet: {missing_cols}")
    label_report = validate_label_contract(samples, horizon)              # FAIL neu hang usable khong hop le / nhan khac (y-c)/c
    frames, info = horizon_frames(samples, horizon, frame_cfg or {"primary_requires_seen_hotel": True})
    if sum(info["rows_all_hotels"].values()) != label_report["n_usable"]:
        raise LabelContractError(f"khung horizon_frames bo bot hang usable: {sum(info['rows_all_hotels'].values())} != {label_report['n_usable']}")
    target_col = f"y_direction_h{horizon}"
    for name in SPLITS:
        assert_all_classes(class_index(frames[name][target_col]), where=f"h{horizon} {name} primary")
    train_hotels = set(frames["train"]["hotel_id"])
    usable = samples[usable_mask(samples, horizon)]
    unseen = {sp: usable[(usable["split"] == sp) & ~usable["hotel_id"].isin(train_hotels)].copy() for sp in ("validation", "test")}
    for sp in ("validation", "test"):
        if len(unseen[sp]) != int(info["excluded_unseen_hotel_rows"][sp]):
            raise LabelContractError(f"{sp}: so hang unseen {len(unseen[sp])} != horizon_frames {info['excluded_unseen_hotel_rows'][sp]}")
    return LoadedDataset(dataset_dir, int(horizon), samples, meta, features, frames, info, label_report, n_ok, pin, dictionary, unseen)


def dataset_sufficiency(loaded: LoadedDataset, frames: dict[str, pd.DataFrame] | None = None) -> dict[str, Any]:
    """Trang thai du bang chung do CHINH dataset bao cao (file da hash-verify) + so ngay du doan hop le moi split DUOC TRUYEN VAO (mac dinh moi khung cua `loaded`).
    Runner truyen chi train/validation truoc khi khoa; so ngay test duoc bo sung SAU khi mo test (`with_test_dates`) => khong cham khung test truoc freeze.
    Khong doi cong sufficiency cho classifier; fixture tong hop khong co `sufficiency_report.json` => `report_present=false`, status null."""
    dataset_dir = getattr(loaded, "dataset_dir", None)
    path = None if dataset_dir is None else Path(dataset_dir) / "sufficiency_report.json"
    entry: dict[str, Any] = {}
    present = path is not None and path.is_file()
    if present:
        report = json.loads(path.read_text(encoding="utf-8"))
        entry = (report.get("horizons") or {}).get(f"h{loaded.horizon}") or {}
    source = loaded.frames if frames is None else frames
    dates = {sp: int(source[sp]["vn_observation_date"].nunique()) for sp in SPLITS if sp in source}
    out = {"report_present": present, "status": entry.get("status"), "failed_gates": list(entry.get("failed_gates") or []), "has_failed_gates": bool(entry.get("failed_gates")),
           "eligible_prediction_dates": dates, "short_window": None}
    return with_test_dates(out, dates["test"]) if "test" in dates else out


def with_test_dates(suff: dict[str, Any], n_test_dates: int) -> dict[str, Any]:
    out = {**suff, "eligible_prediction_dates": {**suff["eligible_prediction_dates"], "test": int(n_test_dates)}}
    out["short_window"] = out["eligible_prediction_dates"].get("validation", 0) < 7 or int(n_test_dates) < 7
    return out


def make_folds(train: pd.DataFrame, horizon: int, cv_cfg: dict[str, Any]) -> list[tuple[np.ndarray, np.ndarray]]:
    n_splits = int(cv_cfg["n_splits"][str(horizon)])
    splitter = PurgedExpandingWindowSplit(n_splits, gap_days=int(horizon), min_train_days=int(cv_cfg["min_train_days"]))
    return list(splitter.split(train["vn_observation_date"]))


def describe_folds(train: pd.DataFrame, folds: list[tuple[np.ndarray, np.ndarray]], horizon: int) -> list[dict[str, Any]]:
    dates = pd.to_datetime(train["vn_observation_date"]).dt.normalize().to_numpy()
    y = class_index(train[f"y_direction_h{horizon}"])
    out = []
    for k, (tr, va) in enumerate(folds):
        if not dates[tr].max() + np.timedelta64(int(horizon), "D") < dates[va].min():
            raise LabelContractError(f"fold {k}: train khong cach validation it nhat h={horizon} ngay (purge)")
        assert_all_classes(y[tr], where=f"fold {k} train")
        assert_all_classes(y[va], where=f"fold {k} validation")
        out.append({"fold": k, "n_train": int(len(tr)), "n_val": int(len(va)), "val_days": int(len(set(dates[va].tolist()))),
                    "train_end": str(dates[tr].max())[:10], "val_start": str(dates[va].min())[:10], "val_end": str(dates[va].max())[:10],
                    "val_support": {name: int((y[va] == i).sum()) for i, name in enumerate(("down", "stable", "up"))}})
    return out
